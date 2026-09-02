import base64
import json
import logging
import math
import mimetypes
import os
import shutil
import subprocess
import re
import tempfile
import textwrap
import time
from decimal import Decimal
from io import BytesIO
from pathlib import Path
from datetime import date

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.core.files.base import ContentFile
from django.http import JsonResponse, HttpResponse, FileResponse, Http404
from django.shortcuts import render, redirect, get_object_or_404
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.usuarios.models import Usuario
from .models import (
    Lineamiento, LineamientoDetalle, Guideline,
    LineamientoGenerado, LineamientoGeneradoFila,
    Formalizacion, FormalizacionFirma, Autoridad, ArchivoBlob,
    LineamientoImagen, MAX_IMAGENES_LINEAMIENTO,
    FormalizacionFirmaAutoridad,
)
from .utils import (
    generar_id_lote, FirmaECError, firmar_documento_acumulativo,
)

logger = logging.getLogger(__name__)


@login_required
def servir_archivo_blob(request, blob_id):
    """Sirve un archivo (diagrama, PDF de Formalizacion, certificado .p12)
    guardado como BLOB en Postgres (ver apps/core/storage.py). Reemplaza el
    .url que Django genera para FileField/ImageField en storage de
    filesystem, ya que un BLOB no tiene una ruta servible directamente.

    as_attachment=True es necesario para que Django emita
    'Content-Disposition: attachment; filename=...' - sin esto (el default es
    'inline') algunos navegadores descargan el archivo sin el nombre/extension
    correctos al usar "Guardar como", aunque el contenido binario sea valido."""
    blob = get_object_or_404(ArchivoBlob, pk=blob_id)
    nombre = os.path.basename(blob.nombre_original)
    content_type = mimetypes.guess_type(nombre)[0] or 'application/octet-stream'
    return FileResponse(
        BytesIO(bytes(blob.contenido)), content_type=content_type,
        as_attachment=True, filename=nombre,
    )


BASE_DIR               = Path(settings.BASE_DIR)
ZNUNY_SCRIPT_VERIFICAR = BASE_DIR / 'znuny' / 'verificacion_ticket.py'
ZNUNY_SCRIPT_CREAR     = BASE_DIR / 'znuny' / 'crear_ticket_hijo.py'
ZNUNY_SCRIPT_CERRAR    = BASE_DIR / 'znuny' / 'cerrar_ticket.py'
ZNUNY_SCRIPT_NOTA      = BASE_DIR / 'znuny' / 'crear_nota.py'

MENSAJE_ELIMINACION = (
    'Se genero una confusion en la asignacion del ticket correspondiente. '
    'En consecuencia, y al haberse identificado esta inconsistencia, '
    'se procede al cierre del ticket.'
)
MENSAJE_FINALIZACION = (
    'Los lineamientos solicitados fueron generados satisfactoriamente. '
    'De igual forma, estos fueron notificados al administrador para que '
    'realice la comunicacion correspondiente al area de negocio.'
)
MENSAJE_FORMALIZACION_COMPLETA = (
    'Todos los responsables han firmado la formalizacion de este ticket. '
    'Se adjunta el documento consolidado final y se procede al cierre '
    'definitivo del ticket padre.'
)


def _limpiar_ticket(valor):
    return re.sub(r'^ticket#', '', valor.strip(), flags=re.IGNORECASE)


TIPO_ABREV  = {'software': 'SW', 'bdd': 'BDD', 'infraestructura': 'INF'}
ORDEN_TIPOS = ['software', 'bdd', 'infraestructura']  # SW, BDD, INF


def _codigo_documento(id_numerico, ticket_padre):
    """Codigo unico del documento: PAS-MLT-{ID_Numerico}-{TicketPadre}
    (sin el ID, si el Lineamiento aun no lo tiene por ser un registro
    anterior a la introduccion de este campo, se usa solo el ticket)."""
    if id_numerico:
        return f'PAS-MLT-{id_numerico}-{ticket_padre}'
    return f'PAS-MLT-{ticket_padre}'


def _nombre_pdf(id_numerico, ticket_padre, tipos=None, temporal=False):
    """
    Nomenclatura del PDF (SIEMPRE basada en el codigo del documento
    PAS-MLT-{ID}-{TicketPadre}, nunca en el ticket hijo):
    - final    -> PAS-MLT-{ID}-{TicketPadre}.pdf
    - temporal -> PAS-MLT-{ID}-{TicketPadre}-{Tipos...}-TMP.pdf (tipos en orden SW, BDD, INF)
    - temporal sin tipos finalizados aun -> PAS-MLT-{ID}-{TicketPadre}-BORRADOR-TMP.pdf
    """
    codigo = _codigo_documento(id_numerico, ticket_padre)
    if not temporal:
        return f'{codigo}.pdf'
    tipos = tipos or []
    abrevs = [TIPO_ABREV[t] for t in ORDEN_TIPOS if t in tipos]
    sufijo = '-'.join(abrevs) if abrevs else 'BORRADOR'
    return f'{codigo}-{sufijo}-TMP.pdf'


def _run_script(script, args, timeout=150):
    try:
        r = subprocess.run(
            [settings.ZNUNY_PYTHON, str(script)] + args,
            capture_output=True, text=True, timeout=timeout,
        )
        if r.stdout.strip():
            return json.loads(r.stdout)
        return {'error': r.stderr[-500:] if r.stderr else 'Sin salida'}
    except subprocess.TimeoutExpired:
        return {'error': 'Timeout'}
    except Exception as e:
        return {'error': str(e)}


# ── GENERACION PDF ───────────────────────────────────────────────────────────
# Nota: los nombres/cargos de "Revisado por"/"Aprobado por" se leen SIEMPRE
# desde la tabla Autoridad (ver _generar_pdf_lineamientos), nunca desde .env.


def _generar_pdf_lineamientos(lin, version_map, watermark=False, tipos_incluir=None):
    """tipos_incluir: iterable de tipos ('software','bdd','infraestructura') a incluir; None = todos."""
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib import colors
    from reportlab.lib.units import cm, mm
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_JUSTIFY
    from reportlab.platypus import (
        SimpleDocTemplate, Table, TableStyle, Paragraph,
        Spacer, PageBreak, KeepTogether, Image as RLImage,
    )
    from reportlab.graphics.shapes import Drawing, Rect, Line, String as RLString, Group
    from reportlab.graphics import renderPDF

    hoy        = date.today().strftime('%d/%m/%Y')
    num_doc    = lin.ticket_principal
    codigo_doc = _codigo_documento(lin.id_numerico, lin.ticket_principal)
    PAGE_W, PAGE_H = landscape(A4)
    MARGIN = 1.5 * cm

    # Colores institucionales
    VERDE  = colors.HexColor('#006847')
    GRIS   = colors.HexColor('#475569')
    GRIS_C = colors.HexColor('#f8fafc')
    BDD_C  = colors.HexColor('#7c3aed')
    INF_C  = colors.HexColor('#FF6347')

    # Estilos
    def estilo(nombre, **kw):
        base = {'fontName': 'Helvetica', 'fontSize': 8, 'leading': 10}
        base.update(kw)
        return ParagraphStyle(nombre, **base)

    S_TITLE  = estilo('title',  fontName='Helvetica-Bold', fontSize=11, alignment=TA_CENTER, textColor=VERDE)
    S_HDR    = estilo('hdr',    fontName='Helvetica-Bold', fontSize=7,  alignment=TA_CENTER, textColor=colors.white)
    S_CELL   = estilo('cell',   fontSize=7, leading=9, alignment=TA_JUSTIFY)
    S_CENTER = estilo('center', fontSize=7, leading=9, alignment=TA_CENTER)
    S_FIRMA  = estilo('firma',  fontSize=8, alignment=TA_CENTER)
    S_CARGO  = estilo('cargo',  fontSize=7, alignment=TA_CENTER, textColor=GRIS)
    S_MONO   = estilo('mono',   fontName='Courier', fontSize=6.5, leading=9)
    S_SUBTIT = estilo('subtit', fontName='Helvetica-Bold', fontSize=9, textColor=BDD_C)

    def texto_pdf(valor):
        """Escapa texto libre (pegado por el usuario, puede traer '<'/'>'/'&'
        de SQL u otros simbolos) para que Paragraph no lo interprete como XML,
        y convierte saltos de linea reales en <br/> para que se respeten en
        el PDF (Paragraph por si solo ignora '\\n')."""
        txt = (valor or '')
        txt = txt.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
        return txt.replace('\n', '<br/>')

    # ── CONSTRUIR INFO DE TICKETS (para el header) ──
    ticket_info_rows = []  # lista de (label, texto) para la tabla de tickets
    TIPO_ORDEN = [('software', 'Software'), ('bdd', 'Base de Datos'), ('infraestructura', 'Capacidad')]

    for tipo_key, tipo_nombre in TIPO_ORDEN:
        if tipos_incluir is not None and tipo_key not in tipos_incluir:
            continue
        det = lin.detalles.filter(tipo=tipo_key).first()
        if not det:
            continue
        # Version seleccionada
        gen_pk = version_map.get(det.pk)
        if gen_pk:
            gen_sel = det.generados.filter(pk=gen_pk).first()
            version_sel = gen_sel.version if gen_sel else None
        else:
            gen_sel   = det.generados.filter(es_borrador=False).order_by('-version').first()
            version_sel = gen_sel.version if gen_sel else None
        if not version_sel:
            continue
        # Versiones desde 1.0 hasta la seleccionada
        versiones = list(det.generados.filter(version__lte=version_sel).order_by('version'))
        lineas_versiones = '\n'.join(
            f'      Ticket#{v.ticket or det.ticket_interno}  v{v.version_display()}'
            for v in versiones
        )
        ticket_info_rows.append((f'-{tipo_nombre}', lineas_versiones))

    def cabecera(include_tickets=False):
        items = [
            Paragraph('INSTITUTO ECUATORIANO DE SEGURIDAD SOCIAL', estilo('iess', fontName='Helvetica-Bold', fontSize=10, alignment=TA_CENTER, textColor=VERDE)),
            Paragraph('Dirección Nacional de Tecnologías de la Información', estilo('dnti', fontSize=8, alignment=TA_CENTER, textColor=GRIS)),
            Paragraph('Subdirección Nacional de Arquitectura y Soluciones', estilo('sdnas', fontSize=8, alignment=TA_CENTER, textColor=GRIS)),
            Spacer(1, 3*mm),
            Paragraph(f'<b>LINEAMIENTOS TÉCNICOS</b> &nbsp;&nbsp;|&nbsp;&nbsp; Fecha: <b>{hoy}</b> &nbsp;&nbsp;|&nbsp;&nbsp; Código Documento: <b>{codigo_doc}</b>', S_TITLE),
            Spacer(1, 2*mm),
        ]
        if include_tickets and ticket_info_rows:
            # Tabla compacta de tickets
            S_TK_HDR  = estilo('tkh', fontName='Helvetica-Bold', fontSize=7, textColor=VERDE)
            S_TK_TIPO = estilo('tkt', fontName='Helvetica-Bold', fontSize=7, textColor=colors.HexColor('#374151'))
            S_TK_VER  = estilo('tkv', fontName='Courier', fontSize=6.5, leading=9, textColor=colors.HexColor('#374151'))

            tk_data = [[Paragraph(f'Ticket Padre: <b>Ticket#{num_doc}</b>', S_TK_HDR), '']]
            for tipo_label, versiones_txt in ticket_info_rows:
                tk_data.append([
                    Paragraph(tipo_label, S_TK_TIPO),
                    Paragraph(versiones_txt, S_TK_VER),
                ])

            avail = PAGE_W - 2*MARGIN
            tk_tbl = Table(tk_data, colWidths=[4*cm, avail - 4*cm])
            tk_tbl.setStyle(TableStyle([
                ('BACKGROUND',    (0,0), (-1,-1), colors.HexColor('#f8fafc')),
                ('BOX',           (0,0), (-1,-1), 0.5, colors.HexColor('#e2e8f0')),
                ('INNERGRID',     (0,0), (-1,-1), 0.3, colors.HexColor('#e2e8f0')),
                ('SPAN',          (0,0), (1,0)),
                ('TOPPADDING',    (0,0), (-1,-1), 3),
                ('BOTTOMPADDING', (0,0), (-1,-1), 3),
                ('LEFTPADDING',   (0,0), (-1,-1), 6),
                ('VALIGN',        (0,0), (-1,-1), 'TOP'),
            ]))
            items.append(tk_tbl)
            items.append(Spacer(1, 3*mm))
        else:
            items.append(Spacer(1, 1*mm))
        return items

    story = []

    story += cabecera(include_tickets=True)

    # ── COLUMNAS DE LA TABLA ──
    COL_WIDTHS = [
        1.0*cm,   # No.
        3.5*cm,   # Equipo Participante
        1.5*cm,   # Tipo
        5.0*cm,   # Necesidad Técnica
        6.5*cm,   # Lineamiento
        5.5*cm,   # Mecanismo
        2.0*cm,   # Fecha
        4.5*cm,   # Observaciones
    ]
    HEADERS = ['No.', 'Equipo\nParticipante', 'Tipo\nRevisión', 'Necesidad\nTécnica',
               'Lineamiento', 'Mecanismo de\nImplementación', 'Fecha\nRevisión', 'Observaciones']

    HDR_ROW  = [Paragraph(h, S_HDR) for h in HEADERS]
    tabla_data = [HDR_ROW]
    idx = 1
    elaborados = []  # Para las firmas

    for tipo_key, tipo_label in [('software','SW'),('bdd','BDD'),('infraestructura','INF')]:
        if tipos_incluir is not None and tipo_key not in tipos_incluir:
            continue
        detalle = lin.detalles.filter(tipo=tipo_key).first()
        if not detalle:
            continue
        generado_pk = version_map.get(detalle.pk)
        generado = (
            detalle.generados.filter(pk=generado_pk).first()
            if generado_pk
            else detalle.generados.filter(es_borrador=False).order_by('-version').first()
        )
        if not generado:
            continue

        responsable = (
            detalle.usuario_asignado.get_full_name()
            or detalle.usuario_asignado.username
        ) if detalle.usuario_asignado else ''
        cargo_label = 'Analista de Software' if tipo_key == 'software' else ('Analista de Base de Datos' if tipo_key == 'bdd' else 'Analista de Capacidad')
        elaborados.append({'nombre': responsable, 'cargo': cargo_label})

        for fila in generado.filas.all():
            row = [
                Paragraph(str(idx), S_CENTER),
                Paragraph(texto_pdf(responsable), S_CELL),
                Paragraph(tipo_label, S_CENTER),
                Paragraph(texto_pdf(fila.necesidad), S_CELL),
                Paragraph(texto_pdf(fila.lineamiento), S_CELL),
                Paragraph(texto_pdf(fila.mecanismo), S_CELL),
                Paragraph(hoy, S_CENTER),
                Paragraph(texto_pdf(fila.observacion), S_CELL),
            ]
            tabla_data.append(row)
            idx += 1

    # Estilo de la tabla principal
    tbl_style = TableStyle([
        # Encabezado
        ('BACKGROUND',   (0,0), (-1,0), VERDE),
        ('TEXTCOLOR',    (0,0), (-1,0), colors.white),
        ('FONTNAME',     (0,0), (-1,0), 'Helvetica-Bold'),
        ('FONTSIZE',     (0,0), (-1,0), 7),
        ('ALIGN',        (0,0), (-1,0), 'CENTER'),
        ('VALIGN',       (0,0), (-1,-1), 'MIDDLE'),
        # Cuerpo
        ('FONTNAME',     (0,1), (-1,-1), 'Helvetica'),
        ('FONTSIZE',     (0,1), (-1,-1), 7),
        ('ROWBACKGROUNDS', (0,1), (-1,-1), [colors.white, GRIS_C]),
        ('GRID',         (0,0), (-1,-1), 0.4, colors.HexColor('#d1d5db')),
        ('TOPPADDING',   (0,0), (-1,-1), 3),
        ('BOTTOMPADDING',(0,0), (-1,-1), 3),
        ('LEFTPADDING',  (0,0), (-1,-1), 4),
        ('RIGHTPADDING', (0,0), (-1,-1), 4),
    ])

    tabla = Table(tabla_data, colWidths=COL_WIDTHS, repeatRows=1)
    tabla.setStyle(tbl_style)
    story.append(tabla)

    # ── FIRMAS: se arma aparte y se agrega al final del documento (ultima
    # pagina), despues de las paginas BDD, para que las coordenadas del
    # estampado FirmaEC (llx/lly/pagina) sean siempre una posicion fija en
    # la ultima hoja, sin depender de cuanto contenido haya antes. ──
    story_firmas = []
    story_firmas.append(PageBreak())
    story_firmas += cabecera()
    story_firmas.append(Paragraph('FIRMAS DE RESPONSABILIDAD', estilo('ft', fontName='Helvetica-Bold', fontSize=10, alignment=TA_CENTER, textColor=VERDE)))
    story_firmas.append(Spacer(1, 10*mm))

    LINEA_FIRMA = '_' * 38
    ESPACIO_FIRMA = 22 * mm  # espacio vertical para la firma fisica
    LABEL_W  = 3.5 * cm
    FIRMA_W  = (PAGE_W - 2*MARGIN - LABEL_W) / 3  # max 3 columnas de firma

    def bloque_firma(nombre, cargo):
        """Retorna lista de flowables: espacio + linea + nombre + cargo."""
        return [
            Spacer(1, ESPACIO_FIRMA),
            Paragraph(LINEA_FIRMA, estilo('linea', alignment=TA_LEFT)),
            Paragraph(nombre, estilo('fnombre', fontName='Helvetica-Bold', fontSize=8, alignment=TA_LEFT)),
            Paragraph(cargo,  estilo('fcargo',  fontSize=7, alignment=TA_LEFT, textColor=GRIS)),
        ]

    def fila_firmas(etiqueta, personas):
        """Genera una fila con la etiqueta a la izq y las firmas a la derecha."""
        # Columna etiqueta
        etiq_cell = Paragraph(etiqueta, estilo('etiq_lbl', fontName='Helvetica-Bold', fontSize=9, textColor=VERDE))

        # Columnas de firma (max 3)
        firma_cells = []
        for p in personas:
            inner = Table(
                [[Spacer(1, ESPACIO_FIRMA)],
                 [Paragraph(LINEA_FIRMA, estilo('lin2', fontSize=8))],
                 [Paragraph(p['nombre'], estilo('fn2', fontName='Helvetica-Bold', fontSize=8))],
                 [Paragraph(p['cargo'],  estilo('fc2', fontSize=7, textColor=GRIS))]],
                colWidths=[FIRMA_W - 8]
            )
            inner.setStyle(TableStyle([
                ('TOPPADDING',    (0,0),(-1,-1), 0),
                ('BOTTOMPADDING', (0,0),(-1,-1), 2),
                ('LEFTPADDING',   (0,0),(-1,-1), 0),
                ('RIGHTPADDING',  (0,0),(-1,-1), 0),
            ]))
            firma_cells.append(inner)

        # Rellenar hasta 3 columnas con espacios vacios
        while len(firma_cells) < 3:
            firma_cells.append(Spacer(1, 1))

        row_data = [[etiq_cell] + firma_cells]
        tbl = Table(row_data, colWidths=[LABEL_W] + [FIRMA_W]*3)
        tbl.setStyle(TableStyle([
            ('VALIGN',       (0,0), (-1,-1), 'TOP'),
            ('TOPPADDING',   (0,0), (-1,-1), 4),
            ('BOTTOMPADDING',(0,0), (-1,-1), 12),
            ('LEFTPADDING',  (0,0), (-1,-1), 4),
            ('LINEBELOW',    (0,-1), (-1,-1), 0.5, colors.HexColor('#e2e8f0')),
        ]))
        return tbl

    # Elaborados (hasta 3)
    story_firmas.append(fila_firmas('Elaborado\npor:', elaborados[:3]))
    story_firmas.append(Spacer(1, 6*mm))

    # Revisado/Aprobado: SIEMPRE desde la tabla Autoridad (nunca desde .env).
    # Si no hay un registro activo de ese tipo, se deja el nombre en blanco
    # con un texto de marcador en vez de usar un valor estatico.
    revisor    = Autoridad.objects.filter(tipo='revisor', activo=True).first()
    aprobador  = Autoridad.objects.filter(tipo='aprobador', activo=True).first()
    nombre_revisor   = revisor.nombre_completo   if revisor   else 'Pendiente de asignación'
    cargo_revisor    = revisor.cargo             if revisor   else ''
    nombre_aprobador = aprobador.nombre_completo if aprobador else 'Pendiente de asignación'
    cargo_aprobador  = aprobador.cargo           if aprobador else ''

    # Revisado
    story_firmas.append(fila_firmas('Revisado\npor:', [{'nombre': nombre_revisor, 'cargo': cargo_revisor}]))
    story_firmas.append(Spacer(1, 6*mm))

    # Aprobado
    story_firmas.append(fila_firmas('Aprobado\npor:', [{'nombre': nombre_aprobador, 'cargo': cargo_aprobador}]))

    # ── PAGINAS BDD (diagrama, tablas, SQL) ──
    bdd_detalle = lin.detalles.filter(tipo='bdd').first() if (tipos_incluir is None or 'bdd' in tipos_incluir) else None
    if bdd_detalle:
        gen_pk   = version_map.get(bdd_detalle.pk)
        bdd_gen  = (
            bdd_detalle.generados.filter(pk=gen_pk).first()
            if gen_pk
            else bdd_detalle.generados.filter(es_borrador=False).order_by('-version').first()
        )
        if bdd_gen and bdd_gen.bdd_tables:
            tables    = bdd_gen.bdd_tables
            schema    = bdd_gen.bdd_schema or ''
            sequences = bdd_gen.bdd_sequences or []
            sql_raw   = bdd_gen.bdd_sql or ''

            # ── HOJA DIAGRAMA ER ──
            story.append(PageBreak())
            story += cabecera()
            story.append(Paragraph('DIAGRAMA ENTIDAD-RELACIÓN', estilo('dr', fontName='Helvetica-Bold', fontSize=10, alignment=TA_CENTER, textColor=BDD_C)))
            story.append(Spacer(1, 4*mm))

            if bdd_detalle.diagrama_personalizado:
                # Imagen personalizada subida por el usuario, escalada para que quepa en la pagina
                AVAIL_W = PAGE_W - 2 * MARGIN
                AVAIL_H = PAGE_H - 7 * cm
                img = RLImage(BytesIO(bdd_detalle.diagrama_personalizado.read()))
                scale = min(AVAIL_W / img.imageWidth, AVAIL_H / img.imageHeight, 1.0)
                img.drawWidth  = img.imageWidth * scale
                img.drawHeight = img.imageHeight * scale
                story.append(img)
            else:
                # Dibujar diagrama con reportlab graphics
                tnames   = list(tables.keys())
                ncols    = max(1, math.ceil(math.sqrt(len(tnames))))
                BOX_W    = 180; BOX_COL_H = 14; BOX_HDR = 20; GAP_X = 40; GAP_Y = 30

                # Calcular tamano natural del diagrama
                nrows    = math.ceil(len(tnames) / ncols)
                max_cols = max(len(tables[t]['columns']) for t in tnames)
                D_W_NAT  = ncols * (BOX_W + GAP_X) + 20
                D_H_NAT  = nrows * (BOX_HDR + max_cols * BOX_COL_H + GAP_Y) + 40

                # Espacio disponible en la pagina (descontando cabecera y titulo)
                AVAIL_W  = PAGE_W - 2 * MARGIN
                AVAIL_H  = PAGE_H - 7 * cm  # cabecera + titulo + margenes

                # Factor de escala para que todo quepa, manteniendo proporciones
                scale    = min(AVAIL_W / D_W_NAT, AVAIL_H / D_H_NAT, 1.0)

                D_W = D_W_NAT; D_H = D_H_NAT
                drawing = Drawing(D_W_NAT * scale, D_H_NAT * scale)
                drawing.transform = (scale, 0, 0, scale, 0, 0)  # escalar todo el contenido
                positions = {}

                for i, tname in enumerate(tnames):
                    col = i % ncols; row = i // ncols
                    tdata = tables[tname]
                    x = col * (BOX_W + GAP_X) + 10
                    y = D_H - row * (BOX_HDR + len(tdata['columns']) * BOX_COL_H + GAP_Y) - BOX_HDR - 10
                    h = BOX_HDR + len(tdata['columns']) * BOX_COL_H
                    positions[tname] = {'x': x, 'y': y - h + BOX_HDR, 'w': BOX_W, 'h': h}

                    # Caja principal
                    drawing.add(Rect(x, y - h + BOX_HDR, BOX_W, h, fillColor=colors.white, strokeColor=colors.HexColor('#7c3aed'), strokeWidth=1.2))
                    # Header
                    drawing.add(Rect(x, y, BOX_W, BOX_HDR, fillColor=BDD_C, strokeColor=BDD_C))
                    drawing.add(RLString(x + BOX_W/2, y + 6, tname,
                        textAnchor='middle', fontSize=7, fillColor=colors.white, fontName='Helvetica-Bold'))

                    # Columnas — empiezan justo debajo del header
                    for ci, col_def in enumerate(tdata['columns']):
                        cy = y - (ci + 1) * BOX_COL_H  # sin + BOX_HDR, eso las sube al header
                        bg = colors.HexColor('#fef9ec') if col_def.get('pk') else colors.white
                        drawing.add(Rect(x, cy, BOX_W, BOX_COL_H, fillColor=bg, strokeColor=colors.HexColor('#e2e8f0'), strokeWidth=0.5))
                        pk_mark = 'PK ' if col_def.get('pk') else ''
                        col_text = f"{pk_mark}{col_def['name']} : {col_def['type']}{('(' + col_def['size'] + ')') if col_def.get('size') else ''}"
                        drawing.add(RLString(x + 5, cy + 4, col_text[:38],
                            textAnchor='start', fontSize=6, fillColor=colors.HexColor('#374151'), fontName='Courier'))

                # Líneas FK
                for tname, tdata in tables.items():
                    for fk in tdata.get('fks', []):
                        src = positions.get(tname); dst = positions.get(fk.get('ref_table', ''))
                        if src and dst:
                            x1 = src['x'] + src['w']; y1 = src['y'] + src['h'] / 2
                            x2 = dst['x'];             y2 = dst['y'] + dst['h'] / 2
                            drawing.add(Line(x1, y1, x2, y2, strokeColor=BDD_C, strokeWidth=1))

                story.append(drawing)

            # ── HOJAS DE IMAGENES ADICIONALES (una por hoja) ──
            for img_obj in bdd_detalle.imagenes.order_by('orden', 'id'):
                story.append(PageBreak())
                story += cabecera()
                story.append(Paragraph('IMAGEN ADJUNTA', estilo('dr', fontName='Helvetica-Bold', fontSize=10, alignment=TA_CENTER, textColor=BDD_C)))
                story.append(Spacer(1, 4*mm))
                AVAIL_W = PAGE_W - 2 * MARGIN
                AVAIL_H = PAGE_H - 7 * cm
                img = RLImage(BytesIO(img_obj.imagen.read()))
                scale = min(AVAIL_W / img.imageWidth, AVAIL_H / img.imageHeight, 1.0)
                img.drawWidth  = img.imageWidth * scale
                img.drawHeight = img.imageHeight * scale
                story.append(img)

            # ── HOJA TABLAS BDD ──
            story.append(PageBreak())
            story += cabecera()
            story.append(Paragraph('DETALLE DE TABLAS', estilo('dt', fontName='Helvetica-Bold', fontSize=10, alignment=TA_CENTER, textColor=BDD_C)))
            story.append(Spacer(1, 4*mm))

            for tname, tdata in tables.items():
                story.append(Paragraph(f'{schema}.{tname}', S_SUBTIT))
                story.append(Spacer(1, 2*mm))
                tbl_hdr = [Paragraph(h, estilo('th', fontName='Helvetica-Bold', fontSize=7, alignment=TA_CENTER, textColor=colors.white))
                           for h in ['Campo', 'Tipo', 'Tamaño', 'Nulo', 'Descripción']]
                tbl_rows = [tbl_hdr]
                for col_def in tdata['columns']:
                    pk_label = ' (PK)' if col_def.get('pk') else ''
                    tbl_rows.append([
                        Paragraph(texto_pdf(col_def['name']) + pk_label, S_CELL),
                        Paragraph(texto_pdf(col_def['type']), S_CENTER),
                        Paragraph(texto_pdf(col_def.get('size', '')), S_CENTER),
                        Paragraph(texto_pdf(col_def.get('nullable', '')), S_CENTER),
                        Paragraph(texto_pdf(col_def.get('description', '')), S_CELL),
                    ])
                tbl_bdd = Table(tbl_rows, colWidths=[4*cm, 2.5*cm, 2*cm, 1.5*cm, None])
                tbl_bdd.setStyle(TableStyle([
                    ('BACKGROUND',    (0,0), (-1,0), BDD_C),
                    ('TEXTCOLOR',     (0,0), (-1,0), colors.white),
                    ('GRID',          (0,0), (-1,-1), 0.4, colors.HexColor('#d1d5db')),
                    ('FONTSIZE',      (0,0), (-1,-1), 7),
                    ('VALIGN',        (0,0), (-1,-1), 'MIDDLE'),
                    ('TOPPADDING',    (0,0), (-1,-1), 3),
                    ('BOTTOMPADDING', (0,0), (-1,-1), 3),
                    ('ROWBACKGROUNDS',(0,1), (-1,-1), [colors.white, colors.HexColor('#f5f3ff')]),
                ]))
                story.append(tbl_bdd)
                story.append(Spacer(1, 5*mm))

            # ── HOJA SQL ──
            story.append(PageBreak())
            story += cabecera()
            story.append(Paragraph('SCRIPT SQL', estilo('sql_t', fontName='Helvetica-Bold', fontSize=10, alignment=TA_CENTER, textColor=BDD_C)))
            story.append(Spacer(1, 4*mm))
            # Partir SQL en líneas cortas para que quepan
            for linea in sql_raw.splitlines():
                partes = textwrap.wrap(linea, width=130) if linea.strip() else ['']
                for parte in partes:
                    story.append(Paragraph(parte.replace('<', '&lt;').replace('>', '&gt;'), S_MONO))

    # ── PAGINA INFRAESTRUCTURA (diagrama de capacidad/topologia) ──
    infra_detalle = (
        lin.detalles.filter(tipo='infraestructura').first()
        if (tipos_incluir is None or 'infraestructura' in tipos_incluir) else None
    )
    if infra_detalle and infra_detalle.diagrama_personalizado:
        story.append(PageBreak())
        story += cabecera()
        story.append(Paragraph('DIAGRAMA DE INFRAESTRUCTURA', estilo('inf_t', fontName='Helvetica-Bold', fontSize=10, alignment=TA_CENTER, textColor=INF_C)))
        story.append(Spacer(1, 4*mm))

        AVAIL_W = PAGE_W - 2 * MARGIN
        AVAIL_H = PAGE_H - 7 * cm
        img = RLImage(BytesIO(infra_detalle.diagrama_personalizado.read()))
        scale = min(AVAIL_W / img.imageWidth, AVAIL_H / img.imageHeight, 1.0)
        img.drawWidth  = img.imageWidth * scale
        img.drawHeight = img.imageHeight * scale
        img.hAlign = 'CENTER'
        story.append(img)

    # La hoja de firmas va siempre al final (ultima pagina del documento).
    story += story_firmas

    # ── BUILD PDF ──
    buf = BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=landscape(A4),
        leftMargin=MARGIN, rightMargin=MARGIN,
        topMargin=MARGIN, bottomMargin=MARGIN,
        title=f'Lineamientos {codigo_doc}',
        author='IESS - SDNAS',
    )

    ultima_pagina = [1]

    def on_page(canvas, doc):
        """Pie de pagina con numero."""
        ultima_pagina[0] = doc.page
        canvas.saveState()
        canvas.setFont('Helvetica', 6)
        canvas.setFillColor(colors.HexColor('#94a3b8'))
        canvas.drawRightString(PAGE_W - MARGIN, 0.5*cm, f'Pág. {doc.page}')
        canvas.drawString(MARGIN, 0.5*cm, f'{codigo_doc} | {hoy} | IESS - DNTI - SDNAS')
        canvas.restoreState()
        if watermark:
            canvas.saveState()
            canvas.translate(PAGE_W / 2, PAGE_H / 2)
            canvas.rotate(45)
            canvas.setFillColorRGB(0.5, 0.5, 0.5, alpha=0.3)
            canvas.setFont('Helvetica-Bold', 90)
            canvas.drawCentredString(0, 0, 'TEMPORAL')
            canvas.restoreState()

    doc.build(story, onFirstPage=on_page, onLaterPages=on_page)
    buf.seek(0)
    buf.total_paginas = ultima_pagina[0]
    return buf


# Coordenadas del bloque "Elaborado por:" en la ultima pagina del PDF, MEDIDAS
# con pdfminer sobre un PDF real generado por _generar_pdf_lineamientos
# (pagina A4 landscape, 841.89x595.28pt). El layout de cada bloque de firma es,
# de arriba hacia abajo: etiqueta -> espacio en blanco (~62pt, para la firma
# fisica/QR) -> linea "____" -> nombre/cargo (debajo de la linea). El QR mide
# 50pt de alto y se dibuja hacia ARRIBA desde `lly`, asi que `lly` debe
# coincidir con el techo de la linea para que el QR caiga en el espacio en
# blanco sin tocar la etiqueta de arriba ni el nombre de abajo.
#   Elaborado: etiqueta Y=436.5-445.5 | linea Y=373.4-381.4 | nombre Y=350.6-369.4
#   Revisado:  etiqueta Y=303.2-312.2 | linea Y=240.0-248.0 | nombre Y=217.2-236.0
#   Aprobado:  etiqueta Y=169.8-178.8 | linea Y=106.6-114.6 | nombre Y=83.9-102.6
FIRMA_COL_X   = [145.7, 364.9, 584.2]
FIRMA_NOMBRE_Y = 426.0  # ajustado hacia arriba (381.4 -> 426.0): el estampado
                        # del WS quedaba tapando la linea y el nombre, asi que
                        # se subio para que caiga en el espacio en blanco justo
                        # debajo de la etiqueta "Elaborado por:" (Y=436.5-445.5).

# Bloque "Revisado por:": coordenadas FIJAS.
REVISOR_FIRMA_X    = 158.0
REVISOR_FIRMA_Y    = 293.0  # ajustado hacia arriba (248.0 -> 293.0) por el
                            # mismo motivo, debajo de la etiqueta "Revisado
                            # por:" (Y=303.2-312.2).

# Bloque "Aprobado por:".
APROBADO_NOMBRE_Y  = 159.0  # ajustado hacia arriba (114.6 -> 159.0), debajo
                            # de la etiqueta "Aprobado por:" (Y=169.8-178.8).

# Offset de calibracion para la seleccion MANUAL del punto de firma (canvas
# en formalizacion.html): el WS de FirmaEC no estampa el QR pegado al `lly`
# que se le envia, lo dibuja mas abajo. Calibrado empiricamente comparando un
# clic de prueba (lly=395, aparecio tapando la linea/nombre de "Elaborado por")
# contra la posicion FIJA ya confirmada correcta para ese mismo bloque (Y=426).
AJUSTE_LLY_MANUAL_PT = 31.0  # 426.0 (posicion correcta conocida) - 395.0 (lly sin ajustar)



def _posicion_firma_pdf(lin, detalle_pk, tipos_incluir, total_paginas):
    """Calcula (llx, lly, pagina) para estampar la firma de `detalle_pk` justo
    arriba de su nombre en el bloque 'Elaborado por:', que ahora es siempre
    la ULTIMA pagina del documento (_generar_pdf_lineamientos agrega la hoja
    de firmas al final, despues de todo el contenido, incluyendo BDD)."""
    tipos_presentes = []
    for tipo_key in ('software', 'bdd', 'infraestructura'):
        if tipos_incluir is not None and tipo_key not in tipos_incluir:
            continue
        if lin.detalles.filter(tipo=tipo_key).exists():
            tipos_presentes.append(tipo_key)

    detalle = LineamientoDetalle.objects.filter(pk=detalle_pk).first()
    if not detalle or detalle.tipo not in tipos_presentes:
        return FIRMA_COL_X[0], FIRMA_NOMBRE_Y, str(total_paginas)

    columna = tipos_presentes.index(detalle.tipo)
    columna = min(columna, len(FIRMA_COL_X) - 1)
    return FIRMA_COL_X[columna], FIRMA_NOMBRE_Y, str(total_paginas)


# ── PARSER SQL (BDD) ──────────────────────────────────────────────────────────

def _parse_columnas(body):
    cols = []
    for line in body.splitlines():
        # Limpiar: quitar coma al inicio (formato Oracle: ", CAMPO TIPO")
        line = re.sub(r'^\s*,\s*', '', line.strip()).rstrip(',')
        line = line.strip()
        # Saltar lineas vacias, parentesis solos, ENABLE, CONSTRAINT, etc.
        if not line or re.match(
            r'^(CONSTRAINT|PRIMARY|UNIQUE|CHECK|FOREIGN|ENABLE|\(|\)|--)',
            line, re.I
        ):
            continue
        m = re.match(r'(\w+)\s+([\w]+)(?:\s*\(([^)]+)\))?\s*(NOT NULL|NULL)?', line, re.I)
        if m:
            cols.append({
                'name':        m.group(1).upper(),
                'type':        m.group(2).upper(),
                'size':        m.group(3) or '',
                'nullable':    'NO' if 'NOT NULL' in line.upper() else 'SI',
                'description': '',
                'pk':          False,
            })
    return cols


def _extraer_bloque_parentesis(content, pos_apertura):
    # Extrae el contenido entre el '(' en pos_apertura y su ')' correspondiente,
    # respetando parentesis anidados (tipos con precision/escala, CHECK, etc.)
    profundidad = 0
    for i in range(pos_apertura, len(content)):
        if content[i] == '(':
            profundidad += 1
        elif content[i] == ')':
            profundidad -= 1
            if profundidad == 0:
                return content[pos_apertura + 1:i], i + 1
    return content[pos_apertura + 1:], len(content)


def _parse_sql(content):
    # Eliminar bloques /* ... */ antes de parsear (FKs comentadas, etc)
    content = re.sub(r'/\*.*?\*/', '', content, flags=re.DOTALL)
    schema = None; tables = {}; sequences = []
    # Esquema es opcional: soporta tanto "CREATE TABLE ESQUEMA.TABLA" como "CREATE TABLE TABLA"
    m = re.search(r'CREATE\s+TABLE\s+(?:(\w+)\.)?(\w+)', content, re.I)
    if m and m.group(1):
        schema = m.group(1).upper()
    for m in re.finditer(r'CREATE\s+SEQUENCE\s+(?:(\w+)\.)?(\w+)', content, re.I):
        sequences.append(m.group(2).upper())
    # Se busca el '(' de apertura y se extrae el bloque respetando parentesis anidados,
    # en vez de un regex no-greedy que puede cortar en el primer ')' de otra sentencia
    # (p.ej. un ALTER TABLE ... PRIMARY KEY (...) que aparezca antes del cierre real).
    duplicadas = []
    for m in re.finditer(r'CREATE\s+TABLE\s+(?:\w+\.)?(\w+)\s*\(', content, re.I):
        tname = m.group(1).upper()
        if tname in tables:
            duplicadas.append(tname)
            continue
        body, _fin = _extraer_bloque_parentesis(content, m.end() - 1)
        tables[tname] = {'columns': _parse_columnas(body), 'pks': [], 'pk_name': None, 'fks': [], 'indexes': [], 'checks': [], 'uniques': [], 'table_comment': ''}
        # PKs inline dentro del CREATE TABLE: CONSTRAINT xxx PRIMARY KEY (col)
        pk_m = re.search(r'CONSTRAINT\s+(\w+)\s+PRIMARY\s+KEY\s*\(([^)]+)\)', body, re.I)
        if pk_m:
            pks = [p.strip().upper() for p in pk_m.group(2).split(',')]
            tables[tname]['pks'] = pks
            tables[tname]['pk_name'] = pk_m.group(1)
            for col in tables[tname]['columns']:
                if col['name'] in pks: col['pk'] = True
        # CHECKs inline dentro del CREATE TABLE: CONSTRAINT xxx CHECK (expr)
        for chk_m in re.finditer(r'CONSTRAINT\s+(\w+)\s+CHECK\s*\(', body, re.I):
            expr, _fin = _extraer_bloque_parentesis(body, chk_m.end() - 1)
            tables[tname]['checks'].append({'name': chk_m.group(1), 'expr': expr.strip()})
        # UNIQUEs inline dentro del CREATE TABLE: CONSTRAINT xxx UNIQUE (col, ...)
        for uq_m in re.finditer(r'CONSTRAINT\s+(\w+)\s+UNIQUE\s*\(([^)]+)\)', body, re.I):
            cols = [c.strip().upper() for c in uq_m.group(2).split(',')]
            tables[tname]['uniques'].append({'name': uq_m.group(1), 'columns': cols})
    # COMMENT ON TABLE ESQUEMA.TABLA IS '...' (o sin esquema)
    for m in re.finditer(r"COMMENT\s+ON\s+TABLE\s+(?:\w+\.)?(\w+)\s+IS\s+'(.*?)'", content, re.I | re.DOTALL):
        tname = m.group(1).upper()
        if tname in tables:
            tables[tname]['table_comment'] = m.group(2).strip().replace("''", "'")
    # Esquema opcional: soporta "COMMENT ON COLUMN ESQUEMA.TABLA.COL" y "COMMENT ON COLUMN TABLA.COL"
    for m in re.finditer(r"COMMENT\s+ON\s+COLUMN\s+(?:\w+\.)?(\w+)\.(\w+)\s+IS\s+'(.*?)'", content, re.I | re.DOTALL):
        tname = m.group(1).upper(); cname = m.group(2).upper()
        desc  = m.group(3).strip().replace("''", "'")
        if tname in tables:
            for col in tables[tname]['columns']:
                if col['name'] == cname: col['description'] = desc; break
    for m in re.finditer(
        r'ALTER\s+TABLE\s+(?:\w+\.)?(\w+)\s+ADD\s+CONSTRAINT\s+(\w+)\s+PRIMARY\s+KEY\s*\(([^)]+)\)',
        content, re.I | re.DOTALL
    ):
        tname = m.group(1).upper(); pks = [p.strip().upper() for p in m.group(3).split(',')]
        if tname in tables:
            tables[tname]['pks'] = pks
            tables[tname]['pk_name'] = m.group(2)
            for col in tables[tname]['columns']:
                if col['name'] in pks: col['pk'] = True
    for m in re.finditer(
        r'ALTER\s+TABLE\s+(?:\w+\.)?(\w+)\s+ADD\s+CONSTRAINT\s+(\w+)\s+FOREIGN\s+KEY\s*\(([^)]+)\)\s+REFERENCES\s+(?:(\w+)\.)?(\w+)\s*\(([^)]+)\)',
        content, re.I | re.DOTALL
    ):
        tname = m.group(1).upper()
        if tname in tables:
            tables[tname]['fks'].append({
                'name':        m.group(2),
                'columns':     [c.strip().upper() for c in m.group(3).split(',')],
                'ref_schema':  m.group(4).upper() if m.group(4) else None,
                'ref_table':   m.group(5).upper(),
                'ref_columns': [c.strip().upper() for c in m.group(6).split(',')],
            })
    for m in re.finditer(
        r'ALTER\s+TABLE\s+(?:\w+\.)?(\w+)\s+ADD\s+CONSTRAINT\s+(\w+)\s+CHECK\s*\(',
        content, re.I
    ):
        tname = m.group(1).upper()
        if tname in tables:
            expr, _fin = _extraer_bloque_parentesis(content, m.end() - 1)
            tables[tname]['checks'].append({'name': m.group(2), 'expr': expr.strip()})
    for m in re.finditer(
        r'ALTER\s+TABLE\s+(?:\w+\.)?(\w+)\s+ADD\s+CONSTRAINT\s+(\w+)\s+UNIQUE\s*\(([^)]+)\)',
        content, re.I | re.DOTALL
    ):
        tname = m.group(1).upper()
        if tname in tables:
            cols = [c.strip().upper() for c in m.group(3).split(',')]
            tables[tname]['uniques'].append({'name': m.group(2), 'columns': cols})
    for m in re.finditer(
        r'CREATE\s+(UNIQUE\s+)?INDEX\s+(?:\w+\.)?(\w+)\s+ON\s+(?:\w+\.)?(\w+)\s*\(([^)]+)\)',
        content, re.I | re.DOTALL
    ):
        tname = m.group(3).upper()
        if tname in tables:
            cols = []
            for c in m.group(4).split(','):
                c = c.strip()
                cm = re.match(r'(\w+)\s*(ASC|DESC)?', c, re.I)
                cols.append({'name': cm.group(1).upper(), 'order': (cm.group(2) or 'ASC').upper()})
            tables[tname]['indexes'].append({
                'name':   m.group(2),
                'unique': bool(m.group(1)),
                'columns': cols,
            })
    return {'schema': schema, 'tables': tables, 'sequences': sequences, 'duplicadas': duplicadas}


# ── VISTAS BDD ────────────────────────────────────────────────────────────────

@login_required
def generar_lineamiento_bdd_view(request, detalle_id):
    detalle   = get_object_or_404(LineamientoDetalle, pk=detalle_id, usuario_asignado=request.user)
    borrador  = detalle.generados.filter(es_borrador=True).first()
    ultima    = detalle.generados.filter(es_borrador=False).order_by('-version').first()
    modo      = request.GET.get('modo', 'nuevo')
    ticket_nv = _limpiar_ticket(request.GET.get('ticket', ''))
    filas_precarga = []
    bdd_precarga   = {}
    if borrador:
        filas_precarga = [
            {'necesidad': f.necesidad, 'lineamiento': f.lineamiento,
             'mecanismo': f.mecanismo,  'observacion': f.observacion}
            for f in borrador.filas.all()
        ]
        if borrador.bdd_sql:
            bdd_precarga = {
                'sql':       borrador.bdd_sql,
                'schema':    borrador.bdd_schema,
                'tables':    borrador.bdd_tables or {},
                'sequences': borrador.bdd_sequences or [],
            }
    elif modo in ('actualizar', 'nueva_version') and ultima:
        filas_precarga = [
            {'necesidad': f.necesidad, 'lineamiento': f.lineamiento,
             'mecanismo': f.mecanismo,  'observacion': f.observacion}
            for f in ultima.filas.all()
        ]
        if ultima.bdd_sql:
            bdd_precarga = {
                'sql':       ultima.bdd_sql,
                'schema':    ultima.bdd_schema,
                'tables':    ultima.bdd_tables or {},
                'sequences': ultima.bdd_sequences or [],
            }
    imagenes_precarga = [
        {'id': img.pk, 'url': img.imagen.url} for img in detalle.imagenes.order_by('orden', 'id')
    ]
    return render(request, 'generar_lineamiento_bdd.html', {
        'detalle': detalle, 'ultima': ultima, 'ya_generado': ultima is not None,
        'modo': modo, 'ticket_nv': ticket_nv,
        'filas_precarga': filas_precarga,
        'bdd_precarga':   json.dumps(bdd_precarga),
        'hay_borrador': borrador is not None,
        'diagrama_personalizado_url': detalle.diagrama_personalizado.url if detalle.diagrama_personalizado else '',
        'imagenes_precarga': json.dumps(imagenes_precarga),
        'max_imagenes': MAX_IMAGENES_LINEAMIENTO,
        'fila_inicial_transversal': json.dumps(_fila_inicial_transversal()),
    })


# ── VISTA INFRAESTRUCTURA / CAPACIDAD ──────────────────────────────────────────

@login_required
def generar_lineamiento_capacidad_view(request, detalle_id):
    detalle   = get_object_or_404(LineamientoDetalle, pk=detalle_id, usuario_asignado=request.user)
    borrador  = detalle.generados.filter(es_borrador=True).first()
    ultima    = detalle.generados.filter(es_borrador=False).order_by('-version').first()
    modo      = request.GET.get('modo', 'nuevo')
    ticket_nv = _limpiar_ticket(request.GET.get('ticket', ''))
    if borrador:
        filas_precarga = [
            {'necesidad': f.necesidad, 'lineamiento': f.lineamiento,
             'mecanismo': f.mecanismo,  'observacion': f.observacion}
            for f in borrador.filas.all()
        ]
    elif modo in ('actualizar', 'nueva_version') and ultima:
        filas_precarga = [
            {'necesidad': f.necesidad, 'lineamiento': f.lineamiento,
             'mecanismo': f.mecanismo,  'observacion': f.observacion}
            for f in ultima.filas.all()
        ]
    else:
        filas_precarga = []
    return render(request, 'generar_lineamiento_capacidad.html', {
        'detalle': detalle, 'ultima': ultima, 'ya_generado': ultima is not None,
        'modo': modo, 'ticket_nv': ticket_nv,
        'filas_precarga': filas_precarga,
        'hay_borrador': borrador is not None,
        'diagrama_personalizado_url': detalle.diagrama_personalizado.url if detalle.diagrama_personalizado else '',
        'fila_inicial_transversal': json.dumps(_fila_inicial_transversal()),
    })


MAX_SQL_FILES = 5


@login_required
@require_POST
def cargar_sql_ajax(request, detalle_id):
    get_object_or_404(LineamientoDetalle, pk=detalle_id, usuario_asignado=request.user)
    sql_files = request.FILES.getlist('sql_file')
    if not sql_files:
        return JsonResponse({'ok': False, 'error': 'No se recibio el archivo SQL'})
    if len(sql_files) > MAX_SQL_FILES:
        return JsonResponse({'ok': False, 'error': f'Maximo {MAX_SQL_FILES} archivos .sql por carga'})
    try:
        schema      = None
        tables      = {}
        sequences   = []
        sql_raw_partes = []
        duplicadas  = []
        for sql_file in sql_files:
            content = sql_file.read().decode('utf-8', errors='replace')
            parsed  = _parse_sql(content)
            duplicadas.extend(parsed.get('duplicadas', []))
            if parsed['schema'] and not schema:
                schema = parsed['schema']
            for tname, tdata in parsed['tables'].items():
                if tname in tables:
                    duplicadas.append(tname)
                tables[tname] = tdata
            for seq in parsed['sequences']:
                if seq not in sequences:
                    sequences.append(seq)
            sql_raw_partes.append(content)
        duplicadas = sorted(set(duplicadas))
        if duplicadas:
            return JsonResponse({
                'ok': False,
                'error': (
                    'El SQL contiene la(s) tabla(s) duplicada(s): ' + ', '.join(duplicadas) +
                    '. Elimina la definicion repetida antes de continuar; no se puede guardar '
                    'mientras exista una tabla con el mismo nombre definida mas de una vez.'
                ),
                'duplicadas': duplicadas,
            })
        return JsonResponse({
            'ok': True,
            'schema':     schema,
            'tables':     tables,
            'sequences':  sequences,
            'sql_raw':    '\n\n'.join(sql_raw_partes),
        })
    except Exception as e:
        return JsonResponse({'ok': False, 'error': str(e)})


@login_required
@require_POST
def diagrama_personalizado_ajax(request, detalle_id):
    detalle = get_object_or_404(LineamientoDetalle, pk=detalle_id, usuario_asignado=request.user)
    if request.POST.get('restaurar') == 'true':
        if detalle.diagrama_personalizado:
            detalle.diagrama_personalizado.delete(save=False)
        detalle.diagrama_personalizado = None
        detalle.save(update_fields=['diagrama_personalizado'])
        return JsonResponse({'ok': True, 'url': None})
    imagen = request.FILES.get('imagen')
    if not imagen:
        return JsonResponse({'ok': False, 'error': 'No se recibio la imagen'})
    if detalle.diagrama_personalizado:
        detalle.diagrama_personalizado.delete(save=False)
    detalle.diagrama_personalizado = imagen
    detalle.save(update_fields=['diagrama_personalizado'])
    return JsonResponse({'ok': True, 'url': detalle.diagrama_personalizado.url})


@login_required
@require_POST
def imagenes_lineamiento_ajax(request, detalle_id):
    """Sube una imagen adicional (hasta MAX_IMAGENES_LINEAMIENTO) para el
    detalle. Cada imagen se incluye en el documento final, una por hoja."""
    detalle = get_object_or_404(LineamientoDetalle, pk=detalle_id, usuario_asignado=request.user)
    actuales = detalle.imagenes.count()
    if actuales >= MAX_IMAGENES_LINEAMIENTO:
        return JsonResponse({'ok': False, 'error': f'Maximo {MAX_IMAGENES_LINEAMIENTO} imagenes por lineamiento'})
    imagen = request.FILES.get('imagen')
    if not imagen:
        return JsonResponse({'ok': False, 'error': 'No se recibio la imagen'})
    obj = LineamientoImagen.objects.create(detalle=detalle, imagen=imagen, orden=actuales)
    return JsonResponse({'ok': True, 'id': obj.pk, 'url': obj.imagen.url})


@login_required
@require_POST
def eliminar_imagen_lineamiento_ajax(request, detalle_id, imagen_id):
    detalle = get_object_or_404(LineamientoDetalle, pk=detalle_id, usuario_asignado=request.user)
    imagen = get_object_or_404(LineamientoImagen, pk=imagen_id, detalle=detalle)
    imagen.imagen.delete(save=False)
    imagen.delete()
    for i, img in enumerate(detalle.imagenes.order_by('orden', 'id')):
        if img.orden != i:
            img.orden = i
            img.save(update_fields=['orden'])
    return JsonResponse({'ok': True})


# ── LOGICA GUIDELINES ─────────────────────────────────────────────────────────

def _fila_inicial_transversal():
    """Fila de Consideracion Tecnica (Base_Conocimiento, tipo=ALL) que se
    precarga automaticamente como primera fila en los 3 tipos de lineamiento
    cuando no hay filas previas (borrador ni version anterior)."""
    g = Guideline.objects.filter(tipo='ALL', necesidad='Consideración Técnica').first()
    if not g:
        return None
    return {'necesidad': g.necesidad, 'lineamiento': g.lineamiento, 'mecanismo': '', 'observacion': ''}


def _calcular_ids(info):
    ids = []; software = info.get('software', ''); ws_tipo = info.get('ws_tipo', '')
    if software in ('web', 'hibrido'):
        tipo = info.get('tipo', '')
        if tipo == 'Nuevo':     ids.append(1)
        elif tipo == 'Antiguo': ids.extend([2, 3])
        elif tipo == 'Actual':  ids.append(3)
        elif tipo == 'Migrar':  ids.extend([4, 1])
        if info.get('auditoria'): ids.append(13)
    if software in ('ws', 'hibrido'):
        if ws_tipo == 'request':
            ids.extend([6, 11]); ids.append(10 if info.get('wso2') else 9)
        elif ws_tipo == 'response':
            cat = info.get('ws_tipo_categoria', '')
            if cat == 'soap': ids.extend([7, 12])
            elif cat == 'rest': ids.extend([8, 12])
        if info.get('auditoria'): ids.append(19)
    if info.get('reportes'):       ids.append(15)
    if info.get('firmaec'):        ids.append(16)
    if info.get('keycloak'):       ids.append(17)
    if info.get('notificaciones'): ids.append(18)
    ids.extend([5, 14]); seen = set()
    return [i for i in ids if not (i in seen or seen.add(i))]


def _guidelines_para_preview(info):
    ids  = _calcular_ids(info); rows = {g.id: g for g in Guideline.objects.filter(id__in=ids)}
    nombre = info.get('tipo_project_name', '<<project_name>>'); gitlab = info.get('tipo_url_gitlab', '<<url_gitlab>>')
    result = []
    for i in ids:
        g = rows.get(i)
        if not g: continue
        lin = g.lineamiento.replace('<<project_name>>', nombre)
        obs = (g.observacion or '').replace('<<url_gitlab>>', gitlab).replace('<<project_name>>', nombre)
        result.append({'id': g.id, 'necesidad': g.necesidad.strip(), 'lineamiento': lin, 'mecanismo': g.mecanismo, 'observacion': obs})
    return result


PASOS = {
    'inicio':        {'pregunta': '¡Hola! Soy tu asistente para generar el lineamiento de Software. ¿Cuál es el <strong>nombre del proyecto</strong>?', 'tipo': 'texto', 'campo': 'tipo_project_name'},
    'tipo_software': {'pregunta': '¿Qué tipo de aplicación es?', 'tipo': 'opciones', 'campo': 'software', 'opciones': [{'valor': 'web', 'label': 'Web'}, {'valor': 'ws', 'label': 'Servicio Web (WS)'}, {'valor': 'hibrido', 'label': 'Híbrido (Web + WS)'}]},
    'tipo_proyecto': {'pregunta': '¿En qué estado se encuentra el proyecto?', 'tipo': 'opciones', 'campo': 'tipo', 'opciones': [{'valor': 'Nuevo', 'label': 'Nuevo'}, {'valor': 'Antiguo', 'label': 'Antiguo'}, {'valor': 'Actual', 'label': 'Actual'}, {'valor': 'Migrar', 'label': 'Migrar a JEE8'}]},
    'url_gitlab':    {'pregunta': '¿Cuál es la <strong>URL de GitLab</strong> del proyecto existente?', 'tipo': 'texto', 'campo': 'tipo_url_gitlab'},
    'ws_tipo':       {'pregunta': '¿El servicio web es de tipo <strong>request</strong> o <strong>response</strong>?', 'tipo': 'opciones', 'campo': 'ws_tipo', 'opciones': [{'valor': 'request', 'label': 'Request'}, {'valor': 'response', 'label': 'Response'}]},
    'wso2':          {'pregunta': '¿El servicio va a ser publicado a través de <strong>WSO2</strong>?', 'tipo': 'opciones', 'campo': 'wso2', 'opciones': [{'valor': 'si', 'label': 'Sí, usa WSO2'}, {'valor': 'no', 'label': 'No usa WSO2'}]},
    'ws_categoria':  {'pregunta': '¿Qué tipo de servicio se va a consumir?', 'tipo': 'opciones', 'campo': 'ws_tipo_categoria', 'opciones': [{'valor': 'soap', 'label': 'SOAP'}, {'valor': 'rest', 'label': 'REST'}]},
    'auditoria':     {'pregunta': '¿Requiere implementar <strong>pistas de auditoría</strong>?', 'tipo': 'opciones', 'campo': 'auditoria', 'opciones': [{'valor': 'si', 'label': 'Sí'}, {'valor': 'no', 'label': 'No'}]},
    'firmaec':       {'pregunta': '¿Requiere integración con <strong>Firma Electrónica</strong>?', 'tipo': 'opciones', 'campo': 'firmaec', 'opciones': [{'valor': 'si', 'label': 'Sí'}, {'valor': 'no', 'label': 'No'}]},
    'keycloak':      {'pregunta': '¿Requiere gestión de usuarios externos con <strong>Keycloak</strong>?', 'tipo': 'opciones', 'campo': 'keycloak', 'opciones': [{'valor': 'si', 'label': 'Sí'}, {'valor': 'no', 'label': 'No'}]},
    'notificaciones':{'pregunta': '¿Requiere envío de <strong>notificaciones por correo</strong>?', 'tipo': 'opciones', 'campo': 'notificaciones', 'opciones': [{'valor': 'si', 'label': 'Sí'}, {'valor': 'no', 'label': 'No'}]},
    'reportes':      {'pregunta': '¿Requiere generación de <strong>reportes con JasperReports</strong>?', 'tipo': 'opciones', 'campo': 'reportes', 'opciones': [{'valor': 'si', 'label': 'Sí'}, {'valor': 'no', 'label': 'No'}]},
    'completado':    {'pregunta': '¡Perfecto! Revisa los lineamientos a la derecha, edita lo que necesites y haz clic en <strong>Finalizar</strong>.', 'tipo': 'fin'},
}


def _siguiente_paso(paso, info):
    sw = info.get('software', '')
    if paso == 'inicio':          return 'tipo_software'
    if paso == 'tipo_software':   return 'tipo_proyecto' if sw in ('web', 'hibrido') else 'ws_tipo'
    if paso == 'tipo_proyecto':   return 'url_gitlab' if info.get('tipo') in ('Antiguo', 'Migrar', 'Actual') else ('ws_tipo' if sw == 'hibrido' else 'auditoria')
    if paso == 'url_gitlab':      return 'ws_tipo' if sw == 'hibrido' else 'auditoria'
    if paso == 'ws_tipo':         return 'wso2' if info.get('ws_tipo') == 'request' else 'ws_categoria'
    if paso in ('wso2', 'ws_categoria'): return 'auditoria'
    if paso == 'auditoria':       return 'firmaec'
    if paso == 'firmaec':         return 'keycloak'
    if paso == 'keycloak':        return 'notificaciones'
    if paso == 'notificaciones':  return 'reportes'
    if paso == 'reportes':        return 'completado'
    return 'completado'


def _procesar_valor(campo, valor):
    booleanos = ('wso2', 'auditoria', 'firmaec', 'keycloak', 'notificaciones', 'reportes')
    return valor.lower() == 'si' if campo in booleanos else valor


# ── AJAX TICKETS ──────────────────────────────────────────────────────────────

@login_required
@require_POST
def validar_ticket_ajax(request):
    numero = _limpiar_ticket(request.POST.get('numero', ''))
    if not numero:
        return JsonResponse({'existe': False, 'error': 'Numero vacio'})
    # Timeout corto (coincide con el limite de espera del frontend, que a los
    # 60s cancela la peticion y activa el fallback manual de ingreso de
    # ticket): esta es solo una consulta, sin fallback no tiene sentido dejar
    # el proceso Playwright corriendo mucho mas alla de lo que el usuario
    # ya dejo de esperar. 60s (en vez de 30s) da margen para un re-login
    # automatico si la sesion de Znuny caduco a mitad de la consulta.
    return JsonResponse(_run_script(ZNUNY_SCRIPT_VERIFICAR, [numero], timeout=60))


@login_required
@require_POST
def crear_hijo_ajax(request):
    ticket_padre = _limpiar_ticket(request.POST.get('ticket_padre', ''))
    tipo         = request.POST.get('tipo', '').strip()
    usuario_id   = request.POST.get('usuario_id', '').strip()
    if not all([ticket_padre, tipo, usuario_id]):
        return JsonResponse({'creado': False, 'error': 'Faltan parametros'})
    try:
        usuario = Usuario.objects.get(pk=usuario_id)
    except Usuario.DoesNotExist:
        return JsonResponse({'creado': False, 'error': 'Usuario no encontrado'})
    TIPO_LABEL = {'software': 'Software', 'bdd': 'Base de Datos', 'infraestructura': 'Capacidad'}
    tipo_label = TIPO_LABEL.get(tipo)
    if not tipo_label:
        return JsonResponse({'creado': False, 'error': f'Tipo invalido: {tipo}'})
    return JsonResponse(_run_script(ZNUNY_SCRIPT_CREAR, [ticket_padre, tipo_label, usuario.get_full_name() or usuario.username]))


# ── HOME (Repositorio de Lineamientos Formalizados) ───────────────────────────

@login_required
def home_view(request):
    """Repositorio publico (visible para staff y no-staff por igual) de
    Lineamientos ya 100% formalizados por los responsables Y con el ticket
    padre cerrado en Znuny. No incluye borradores ni procesos en curso.

    El cierre del ticket ya NO espera a que las Autoridades con autofirma
    manual (ej. el Aprobador) hayan firmado -ver _cerrar_ticket_padre_si_formalizado-
    asi que un documento puede aparecer aqui con su firma de Autoridad
    todavia pendiente; la columna 'Autoridad' en el template lo marca con
    una X en ese caso y se actualiza a check apenas esa Autoridad firma
    (lo que ademas reemplaza el PDF por la version con su firma incluida)."""
    if getattr(request.user, 'autoridad', None) is not None:
        return redirect('mis_firmas_autoridad')

    formalizaciones = (
        Formalizacion.objects
        .filter(ticket_padre_cerrado=True)
        .select_related('lineamiento')
        .prefetch_related(
            'firmas__detalle', 'firmas__responsable', 'lineamiento__detalles',
            'firmas_autoridad__autoridad',
        )
        .order_by('-fecha_cierre_ticket_padre')
    )
    # `formalizado` es una property (no un campo de BD): filtrar en Python
    # sobre el queryset ya reducido por ticket_padre_cerrado=True (el cierre
    # automatico solo se ejecuta cuando ya estaba formalizado, asi que en la
    # practica esto siempre es True, pero se valida explicitamente para
    # respetar la regla de negocio incluso ante datos historicos inconsistentes).
    repositorio = [f for f in formalizaciones if f.formalizado]

    for f in repositorio:
        f.codigo_documento = _codigo_documento(f.lineamiento.id_numerico, f.lineamiento.ticket_principal)
        f.tipos_incluidos = sorted(
            {firma.detalle.tipo for firma in f.firmas.all()},
            key=lambda t: ORDEN_TIPOS.index(t) if t in ORDEN_TIPOS else 99,
        )
        # Firmas de Autoridad pendientes (ej. Aprobador con firma_automatica
        # desactivada): si hay alguna sin firmar, el documento aun no tiene
        # TODAS las firmas aunque el ticket ya este cerrado.
        f.autoridades_pendientes = [
            fa.autoridad for fa in f.firmas_autoridad.all() if not fa.firmado
        ]

    return render(request, 'home.html', {
        'repositorio': repositorio,
        'seccion_activa': 'repositorio',
    })


@login_required
def mis_tickets_view(request):
    """Vista de trabajo de un responsable no-staff: sus solicitudes de
    lineamiento pendientes y atendidas (antes vivia en 'home', ahora 'home'
    es el repositorio publico de formalizados)."""
    user = request.user
    if user.is_staff:
        return redirect('tickets_asignadas')
    detalles_qs         = LineamientoDetalle.objects.filter(usuario_asignado=user).select_related('lineamiento').prefetch_related('generados')
    detalles_pendientes = [d for d in detalles_qs if not d.finalizado]
    detalles_atendidos  = [d for d in detalles_qs if d.finalizado]
    return render(request, 'mis_tickets.html', {
        'detalles_pendientes': detalles_pendientes,
        'detalles_atendidos':  detalles_atendidos,
    })


@login_required
def repositorio_detalle_view(request, formalizacion_id):
    """Detalle de un Lineamiento formalizado del repositorio: contenido
    tecnico por tipo (Necesidad/Lineamiento/Mecanismo/Observacion), diagramas
    (BDD/Infraestructura) y quien firmo. Solo accesible para formalizaciones
    ya cerradas (mismo criterio que el listado del repositorio)."""
    formalizacion = get_object_or_404(
        Formalizacion.objects.select_related('lineamiento').prefetch_related(
            'firmas__detalle', 'firmas__responsable',
        ),
        pk=formalizacion_id, ticket_padre_cerrado=True,
    )
    if not formalizacion.formalizado:
        return redirect('home')

    lin = formalizacion.lineamiento
    version_map = {int(k): v for k, v in formalizacion.version_map.items()}
    firmas_por_detalle = {firma.detalle_id: firma for firma in formalizacion.firmas.all()}

    secciones = []
    filas_consolidadas = []
    for detalle in lin.detalles.all().order_by('tipo'):
        generado_id = version_map.get(detalle.pk)
        generado = detalle.generados.filter(pk=generado_id).first() if generado_id else None
        if generado is None:
            generado = detalle.generados.filter(es_borrador=False).order_by('-version').first()
        firma = firmas_por_detalle.get(detalle.pk)
        filas = generado.filas.all() if generado else []

        secciones.append({
            'tipo': detalle.tipo,
            'tipo_label': detalle.get_tipo_display(),
            'ticket_interno': detalle.ticket_interno,
            'generado': generado,
            'filas': filas,
            'diagrama_url': detalle.diagrama_personalizado.url if detalle.diagrama_personalizado else '',
            'responsable': firma.responsable if firma else detalle.usuario_asignado,
            'firmado': firma.firmado if firma else False,
            'fecha_firma': firma.fecha_firma if firma else None,
        })

        for fila in filas:
            filas_consolidadas.append({
                'tipo': detalle.tipo,
                'tipo_label': detalle.get_tipo_display(),
                'necesidad': fila.necesidad,
                'lineamiento': fila.lineamiento,
                'mecanismo': fila.mecanismo,
                'observacion': fila.observacion,
            })

    revisor = Autoridad.objects.filter(tipo='revisor', activo=True).first()
    aprobador = Autoridad.objects.filter(tipo='aprobador', activo=True).first()

    return render(request, 'repositorio_detalle.html', {
        'formalizacion': formalizacion,
        'lin': lin,
        'codigo_documento': _codigo_documento(lin.id_numerico, lin.ticket_principal),
        'secciones': secciones,
        'filas_consolidadas': filas_consolidadas,
        'revisor': revisor,
        'aprobador': aprobador,
        'seccion_activa': 'repositorio',
    })


def _solicitudes_staff(user):
    """Solicitudes visibles para un usuario staff, con su progreso calculado.
    Todo usuario is_staff actua como supervisor global: ve TODAS las
    solicitudes del sistema, sin importar quien las creo. Retorna
    (asignadas, atendidas, progreso_dict)."""
    solicitudes = list(Lineamiento.objects.all(
    ).prefetch_related(
        'detalles__usuario_asignado', 'detalles__generados',
    ).order_by('-fecha_creacion'))
    progreso = {}
    for sol in solicitudes:
        detalles    = list(sol.detalles.all())
        total       = len(detalles)
        finalizados = sum(1 for d in detalles if d.finalizado)
        progreso[sol.pk] = round(finalizados / total * 100) if total else 0
    asignadas = [s for s in solicitudes if progreso.get(s.pk, 0) < 100]
    atendidas = [s for s in solicitudes if progreso.get(s.pk, 0) == 100]
    return asignadas, atendidas, progreso


@login_required
def tickets_asignadas_view(request):
    if not request.user.is_staff:
        return redirect('home')
    solicitudes_asignadas, _, progreso = _solicitudes_staff(request.user)
    return render(request, 'tickets_asignadas.html', {
        'solicitudes_asignadas': solicitudes_asignadas,
        'progreso_json':         json.dumps(progreso),
    })


@login_required
def tickets_atendidas_view(request):
    if not request.user.is_staff:
        return redirect('home')
    _, solicitudes_atendidas, _ = _solicitudes_staff(request.user)
    return render(request, 'tickets_atendidas.html', {
        'solicitudes_atendidas': solicitudes_atendidas,
    })


# ── FORMALIZACION ─────────────────────────────────────────────────────────────
# Visible para CUALQUIER responsable (staff o no) que tenga una firma asignada.

def _agrupar_firmas_por_ticket(firmas):
    """Agrupa firmas (ya ordenadas) por ticket principal, para renderizar una
    sola fila por ticket con los tipos/firmas anidados dentro (igual estilo
    que 'Solicitudes Atendidas')."""
    grupos = []
    indice = {}
    for firma in firmas:
        ticket = firma.formalizacion.lineamiento.ticket_principal
        if ticket not in indice:
            indice[ticket] = {
                'ticket_principal': ticket,
                'lineamiento_id': firma.formalizacion.lineamiento_id,
                'firmas': [],
            }
            grupos.append(indice[ticket])
        indice[ticket]['firmas'].append(firma)
    for grupo in grupos:
        grupo['tiene_firmas_parciales'] = any(f.firmado for f in grupo['firmas'])
        # PDF final unico por ticket: la Formalizacion mas reciente (la que
        # consolida todas las firmas). Evita un boton de descarga por cada
        # tipo/Formalizacion cuando un mismo ticket se formalizo por partes.
        formalizacion_final = max(
            (f.formalizacion for f in grupo['firmas']),
            key=lambda fz: fz.fecha_creacion,
        )
        grupo['formalizacion_final'] = formalizacion_final
    return grupos


@login_required
def formalizacion_asignadas_view(request):
    if request.user.is_staff:
        # Se muestra el ticket completo (TODAS sus firmas, firmadas o no)
        # mientras la Formalizacion no este 100% completa, para no ocultar
        # los tipos que ya firmaron cuando otros aun estan pendientes.
        from django.db.models import Count, Q
        formalizaciones_incompletas = Formalizacion.objects.annotate(
            firmas_pendientes=Count('firmas', filter=Q(firmas__firmado=False)),
        ).filter(firmas_pendientes__gt=0)
        firmas = FormalizacionFirma.objects.filter(
            formalizacion__in=formalizaciones_incompletas,
        ).select_related('formalizacion__lineamiento', 'detalle').order_by('-formalizacion__fecha_creacion')
    else:
        firmas = FormalizacionFirma.objects.filter(
            responsable=request.user, firmado=False,
        ).select_related('formalizacion__lineamiento', 'detalle').order_by('-formalizacion__fecha_creacion')
    return render(request, 'formalizacion.html', {
        'seccion_activa': 'form-asignadas',
        'titulo': 'Mis Formalizaciones',
        'grupos': _agrupar_firmas_por_ticket(firmas),
        'modo': 'asignadas',
        'filter_ticket': request.GET.get('filter_ticket', ''),
    })


@login_required
def formalizacion_atendidas_view(request):
    if request.user.is_staff:
        # Solo tickets completamente formalizados (TODAS las firmas requeridas
        # ya completadas). Se traen TODAS las firmas de esas formalizaciones
        # (no solo firmado=True) para mostrar el detalle de cada responsable;
        # como el ticket ya esta formalizado, todas estaran en estado Firmado.
        from django.db.models import Count, Q
        formalizaciones_completas = Formalizacion.objects.annotate(
            total_firmas=Count('firmas'),
            firmas_pendientes=Count('firmas', filter=Q(firmas__firmado=False)),
        ).filter(total_firmas__gt=0, firmas_pendientes=0)
        firmas = FormalizacionFirma.objects.filter(
            formalizacion__in=formalizaciones_completas,
        ).select_related('formalizacion__lineamiento', 'detalle').order_by('-fecha_firma')
    else:
        firmas = FormalizacionFirma.objects.filter(
            responsable=request.user, firmado=True,
        ).select_related('formalizacion__lineamiento', 'detalle').order_by('-fecha_firma')
    return render(request, 'formalizacion.html', {
        'seccion_activa': 'form-atendidas',
        'titulo': 'Formalizaciones Atendidas',
        'grupos': _agrupar_firmas_por_ticket(firmas),
        'modo': 'atendidas',
        'filter_ticket': request.GET.get('filter_ticket', ''),
    })


@login_required
def reemplazar_documento_formalizacion_ajax(request, formalizacion_id):
    """Permite a Staff subir manualmente un PDF (ej. firmado fisicamente y
    escaneado) que reemplaza al documento generado por el sistema para una
    Formalizacion ya completa. Solo aplica sobre formalizaciones 100%
    firmadas (formalizado=True); queda registrado quien y cuando lo hizo."""
    if not request.user.is_staff:
        return JsonResponse({'ok': False, 'error': 'No autorizado.'}, status=403)
    if request.method != 'POST':
        return JsonResponse({'ok': False, 'error': 'Metodo no permitido.'}, status=405)

    formalizacion = get_object_or_404(Formalizacion, pk=formalizacion_id)
    if not formalizacion.formalizado:
        return JsonResponse({'ok': False, 'error': 'Solo se puede reemplazar el documento de una Formalizacion ya completada.'})

    archivo = request.FILES.get('documento')
    if not archivo:
        return JsonResponse({'ok': False, 'error': 'No se recibio ningun archivo.'})
    if not archivo.name.lower().endswith('.pdf') or archivo.content_type != 'application/pdf':
        return JsonResponse({'ok': False, 'error': 'El archivo debe ser un PDF.'})

    if formalizacion.documento:
        formalizacion.documento.delete(save=False)
    formalizacion.documento = archivo
    formalizacion.reemplazo_manual = True
    formalizacion.reemplazado_por = request.user
    formalizacion.fecha_reemplazo = timezone.now()
    formalizacion.save(update_fields=['documento', 'reemplazo_manual', 'reemplazado_por', 'fecha_reemplazo'])

    logger.warning(
        'Documento de Formalizacion #%s (ticket %s) reemplazado manualmente por %s',
        formalizacion.pk, formalizacion.lineamiento.ticket_principal, request.user.username,
    )
    return JsonResponse({'ok': True, 'url': formalizacion.documento.url})


def _observacion_version_divergente(firma):
    """Una Formalizacion cubre TODOS los tipos de detalle a la vez (un solo
    version_map con la version de cada tipo en el momento en que se creo).
    Si CUALQUIER detalle incluido en ese version_map (el propio tipo de
    `firma` u otro) genero una version NUEVA despues de eso (su ultima_version
    actual ya no coincide con la que quedo registrada en version_map), retorna
    un aviso de que el documento quedo desactualizado. Cubre tanto el caso de
    "otro tipo cambio de version" como "el mismo tipo ya firmado tiene una
    version mas nueva disponible". Si no hay ninguna version nueva pendiente,
    retorna ''."""
    formalizacion = firma.formalizacion

    for detalle_pk_str, generado_pk in formalizacion.version_map.items():
        otro_detalle = LineamientoDetalle.objects.filter(pk=detalle_pk_str).first()
        if not otro_detalle or not otro_detalle.ultima_version:
            continue
        if otro_detalle.ultima_version.pk != int(generado_pk):
            return (
                'Se generó una nueva versión después de esta formalización. '
                'El documento firmado quedó desactualizado y se debe formalizar de nuevo.'
            )
    return ''


@login_required
def formalizacion_view(request):
    """Vista de Formalizacion para usuarios NO staff: una sola pantalla con
    'Mis Formalizaciones' (pendientes) y 'Formalizaciones Atendidas' (firmadas)
    apiladas, calcada de la estructura de 'home' (Mis Solicitudes / Solicitudes
    Atendidas) en vez de pestanas o submenus separados."""
    if request.user.is_staff:
        return redirect('formalizacion_asignadas')

    firmas_pendientes = list(FormalizacionFirma.objects.filter(
        responsable=request.user, firmado=False,
    ).select_related('formalizacion__lineamiento', 'detalle').order_by('-formalizacion__fecha_creacion'))

    firmas_atendidas = list(FormalizacionFirma.objects.filter(
        responsable=request.user, firmado=True,
    ).select_related('formalizacion__lineamiento', 'detalle').order_by('-fecha_firma'))
    for firma in firmas_atendidas:
        firma.observacion = _observacion_version_divergente(firma)

    return render(request, 'formalizacion_home.html', {
        'seccion_activa': 'formalizacion',
        'firmas_pendientes': firmas_pendientes,
        'firmas_atendidas': firmas_atendidas,
    })


def _firmar_con_autoridad(pdf_bytes, autoridad, lineamiento_id, nombre_pdf, llx, lly, pagina):
    """Firma `pdf_bytes` (ya con las firmas previas guardadas en BD) con las
    credenciales FirmaEC guardadas en un registro de Autoridad (Revisor/
    Aprobador). Cada llamada genera su propio idLote NUEVO y de un solo uso
    (no se comparte entre firmantes): el WS firma ese documento puntual y se
    descarga el resultado; la acumulacion de firmas la hace la app guardando
    el PDF resultante en la BD entre cada paso, no el WS por idLote.

    Retorna los bytes del PDF con la nueva firma, o None si fallo (el error
    se registra en logs; nunca detiene el cierre del ticket)."""
    try:
        autoridad.archivo_p12.open('rb')
        pkcs12_b64 = base64.b64encode(autoridad.archivo_p12.read()).decode('ascii')
    finally:
        autoridad.archivo_p12.close()

    id_lote = generar_id_lote(lineamiento_id)
    try:
        pdf_firmado = firmar_documento_acumulativo(
            pdf_bytes, autoridad.cedula, id_lote, autoridad.clave_p12, pkcs12_b64,
            nombre_documento=nombre_pdf, llx=llx, lly=lly, pagina=pagina,
        )
        return pdf_firmado
    except FirmaECError as e:
        logger.warning(
            'No se pudo firmar automaticamente con %s "%s" (id_lote=%s): %s',
            autoridad.get_tipo_display(), autoridad.nombre_completo, id_lote, e,
        )
        return None


def enviar_alerta_firma_pendiente(autoridad, lineamiento, pdf_bytes, nombre_pdf):
    """Notifica por correo a una Autoridad (Revisor/Aprobador) que el
    lineamiento ya fue procesado por los elaboradores pero le falta su firma
    digital (porque no tiene credenciales FirmaEC completas cargadas),
    adjuntando el PDF actual para que pueda gestionarla por fuera del sistema.

    No hace nada si la Autoridad no tiene un correo registrado. Cualquier
    error de envio queda solo en logs; nunca debe interrumpir el cierre del
    ticket."""
    if not autoridad.correo:
        logger.warning(
            'No se pudo notificar a %s "%s": no tiene correo registrado en Autoridad.',
            autoridad.get_tipo_display(), autoridad.nombre_completo,
        )
        return

    from django.core.mail import EmailMessage

    correos_staff = list(
        Usuario.objects.filter(is_staff=True)
        .exclude(email='')
        .exclude(email=autoridad.correo)
        .values_list('email', flat=True)
        .distinct()
    )

    asunto = f'Acción Requerida: Firma pendiente para Lineamiento Ticket #{lineamiento.ticket_principal}'
    cuerpo = (
        f'Estimado/a {autoridad.nombre_completo},\n\n'
        f'El lineamiento asociado al Ticket #{lineamiento.ticket_principal} ha sido procesado por '
        f'los elaboradores, pero carece de su firma digital ({autoridad.get_tipo_display()}) para '
        'la formalización completa.\n\n'
        'Por favor, firme el documento adjunto y retorne o siga las instrucciones del proceso de '
        'formalización.\n\n'
        'Este es un mensaje automático del Sistema de Lineamientos Técnicos.'
    )
    try:
        email = EmailMessage(asunto, cuerpo, to=[autoridad.correo], cc=correos_staff)
        email.attach(nombre_pdf, pdf_bytes, 'application/pdf')
        email.send(fail_silently=False)
        logger.info(
            'Alerta de firma pendiente enviada a %s "%s" (%s) para ticket %s.',
            autoridad.get_tipo_display(), autoridad.nombre_completo, autoridad.correo,
            lineamiento.ticket_principal,
        )
    except Exception as e:
        logger.warning(
            'No se pudo enviar la alerta de firma pendiente a %s "%s" (%s): %s',
            autoridad.get_tipo_display(), autoridad.nombre_completo, autoridad.correo, e,
        )


def _firma_autoridad_pendiente(formalizacion, autoridad):
    """Obtiene (creando si hace falta) el registro FormalizacionFirmaAutoridad
    que trackea el estado de firma de `autoridad` sobre `formalizacion`. Solo
    tiene sentido cuando la Autoridad tiene un usuario vinculado (autofirma
    manual); para autoridades sin usuario se sigue usando el flujo automatico
    / correo de siempre, sin crear este registro."""
    firma, _ = FormalizacionFirmaAutoridad.objects.get_or_create(
        formalizacion=formalizacion, autoridad=autoridad,
    )
    return firma


def _autoridad_pendiente_manual(formalizacion, autoridad):
    """True si `autoridad` debe quedar pendiente de autofirma manual para
    esta Formalizacion (firma_automatica desactivado + usuario vinculado) Y
    todavia no la ha firmado."""
    if autoridad.firma_automatica or not autoridad.usuario_id:
        return False
    return not FormalizacionFirmaAutoridad.objects.filter(
        formalizacion=formalizacion, autoridad=autoridad, firmado=True,
    ).exists()


def _cerrar_ticket_padre_si_formalizado(formalizacion):
    """Al completarse la ultima firma de los responsables: parte del PDF YA
    firmado por ellos (formalizacion.documento) e intenta estampar las firmas
    de Revisor y Aprobador, en ese orden, cada una de tres formas posibles:

      1. Si la Autoridad tiene credenciales FirmaEC completas (`puede_firmar`)
         Y `firma_automatica` esta activo (default), se firma automaticamente
         como siempre, sin intervencion humana.
      2. Si `firma_automatica` esta desactivado y tiene un usuario Django
         vinculado (`Autoridad.usuario`), se omite su firma AQUI (queda
         pendiente en FormalizacionFirmaAutoridad) pero YA NO bloquea nada
         mas: el cierre del Ticket Padre en Znuny y el "documento final" (sin
         marca de agua temporal) se generan igual, sin esperarla. Esa
         Autoridad vera el documento pendiente en su propia tabla (ver
         mis_firmas_autoridad_view) y lo firmara ella misma cuando pueda,
         entrando con su usuario y subiendo cedula/password/.p12 (que quedan
         guardados en Autoridad para el dia que se reactive
         `firma_automatica`). Cuando firme, _firmar_una_autoridad unicamente
         reemplaza formalizacion.documento con el PDF ya con su firma
         estampada -el ticket en Znuny NO se vuelve a tocar, porque ya estaba
         cerrado desde el paso anterior.
      3. Si no tiene credenciales completas ni usuario vinculado, se mantiene
         el comportamiento legado: se notifica por correo para gestion manual
         fuera del sistema (tampoco bloquea el cierre del ticket).

    No falla el flujo de firma si el cierre en Znuny no se puede ejecutar, ni
    si la firma automatica de autoridades falla; en ambos casos solo queda
    constancia en logs y el proceso continua con lo que si se pudo completar."""
    if formalizacion.ticket_padre_cerrado or not formalizacion.formalizado:
        return

    lin = formalizacion.lineamiento
    tmp_dir = None
    try:
        nombre_pdf = _nombre_pdf(lin.id_numerico, lin.ticket_principal, temporal=False)
        # IMPORTANTE: partir del documento YA firmado por los responsables
        # (formalizacion.documento), NO regenerar el PDF desde cero aqui -
        # _generar_pdf_lineamientos produce un PDF limpio sin ningun QR
        # estampado, y usarlo como base perderia la firma de "Elaborado por".
        pdf_bytes = _obtener_pdf_base_actual(formalizacion)
        total_paginas = _asegurar_total_paginas(formalizacion)

        # Firma de Revisor/Aprobador: cada una usa su propio idLote nuevo (ver
        # _firmar_con_autoridad); el encadenado de firmas lo hace la app
        # pasando el pdf_bytes ya firmado de un paso al siguiente, no el WS.
        # `firma_automatica` (default True) manda sobre `puede_firmar`: si esta
        # en False, la Autoridad SIEMPRE queda pendiente de autofirma manual
        # (via su usuario vinculado) aunque ya tenga credenciales guardadas -
        # pero eso YA NO detiene el resto del proceso (ver docstring, caso 2).
        revisor = Autoridad.objects.filter(tipo='revisor', activo=True).first()
        revisor_ya_firmo = bool(revisor) and FormalizacionFirmaAutoridad.objects.filter(
            formalizacion=formalizacion, autoridad=revisor, firmado=True,
        ).exists()
        if revisor_ya_firmo:
            pass  # ya firmado manualmente por el propio usuario de la Autoridad
        elif revisor and revisor.puede_firmar and revisor.firma_automatica:
            pdf_firmado = _firmar_con_autoridad(
                pdf_bytes, revisor, lin.pk, nombre_pdf,
                REVISOR_FIRMA_X, REVISOR_FIRMA_Y, str(total_paginas),
            )
            if pdf_firmado:
                pdf_bytes = pdf_firmado
        elif revisor and _autoridad_pendiente_manual(formalizacion, revisor):
            logger.info(
                'Firma de Revisor "%s" queda pendiente de autofirma manual (usuario vinculado); '
                'el cierre del ticket continua sin esperarla.',
                revisor.nombre_completo,
            )
            _firma_autoridad_pendiente(formalizacion, revisor)
        elif revisor:
            logger.warning(
                'Firma automatica omitida para Revisor "%s": faltan credenciales '
                '(cedula/archivo_p12/clave_p12) en el registro de Autoridad.',
                revisor.nombre_completo,
            )
            enviar_alerta_firma_pendiente(revisor, lin, pdf_bytes, nombre_pdf)
        else:
            logger.warning('Firma automatica omitida para Revisor: no hay Autoridad activa configurada.')

        aprobador = Autoridad.objects.filter(tipo='aprobador', activo=True).first()
        aprobador_ya_firmo = bool(aprobador) and FormalizacionFirmaAutoridad.objects.filter(
            formalizacion=formalizacion, autoridad=aprobador, firmado=True,
        ).exists()
        if aprobador_ya_firmo:
            pass  # ya firmado manualmente por el propio usuario de la Autoridad
        elif aprobador and aprobador.puede_firmar and aprobador.firma_automatica:
            pdf_firmado = _firmar_con_autoridad(
                pdf_bytes, aprobador, lin.pk, nombre_pdf,
                FIRMA_COL_X[0], APROBADO_NOMBRE_Y, str(total_paginas),
            )
            if pdf_firmado:
                pdf_bytes = pdf_firmado
        elif aprobador and _autoridad_pendiente_manual(formalizacion, aprobador):
            logger.info(
                'Firma de Aprobador "%s" queda pendiente de autofirma manual (usuario vinculado); '
                'el cierre del ticket continua sin esperarla.',
                aprobador.nombre_completo,
            )
            _firma_autoridad_pendiente(formalizacion, aprobador)
        elif aprobador:
            logger.warning(
                'Firma automatica omitida para Aprobador "%s": faltan credenciales '
                '(cedula/archivo_p12/clave_p12) en el registro de Autoridad.',
                aprobador.nombre_completo,
            )
            enviar_alerta_firma_pendiente(aprobador, lin, pdf_bytes, nombre_pdf)
        else:
            logger.warning('Firma automatica omitida para Aprobador: no hay Autoridad activa configurada.')

        tmp_dir = tempfile.mkdtemp(prefix=f'formalizacion_{formalizacion.pk}_')
        tmp_path = os.path.join(tmp_dir, nombre_pdf)
        with open(tmp_path, 'wb') as f:
            f.write(pdf_bytes)
        formalizacion.documento.save(nombre_pdf, ContentFile(pdf_bytes), save=True)
        resultado = _run_script(
            ZNUNY_SCRIPT_CERRAR, [lin.ticket_principal, MENSAJE_FORMALIZACION_COMPLETA, tmp_path],
        )
        if resultado.get('cerrado'):
            formalizacion.ticket_padre_cerrado = True
            formalizacion.fecha_cierre_ticket_padre = timezone.now()
            formalizacion.save(update_fields=['ticket_padre_cerrado', 'fecha_cierre_ticket_padre'])
    finally:
        if tmp_dir and os.path.exists(tmp_dir):
            shutil.rmtree(tmp_dir, ignore_errors=True)


def _asegurar_total_paginas(formalizacion):
    if not formalizacion.total_paginas:
        # Formalizaciones creadas antes de que existiera este campo: el
        # contenido es determinista por version_map, asi que se puede
        # recalcular regenerando el PDF limpio solo para contar paginas.
        lin = formalizacion.lineamiento
        version_map = {int(k): v for k, v in formalizacion.version_map.items()}
        buf_conteo = _generar_pdf_lineamientos(lin, version_map, watermark=False)
        formalizacion.total_paginas = buf_conteo.total_paginas
        formalizacion.save(update_fields=['total_paginas'])
    return formalizacion.total_paginas


def _obtener_pdf_base_actual(formalizacion):
    """Devuelve los bytes del PDF que se usaria como insumo para la PROXIMA
    firma de esta Formalizacion. Cada firma usa su propio idLote nuevo y de
    un solo uso (ver firmar_formalizacion_ajax / _firmar_con_autoridad), asi
    que el WS nunca acumula nada entre firmantes: la fuente de verdad del
    documento con las firmas previas es siempre el ultimo PDF guardado en BD
    (formalizacion.documento). Es la misma fuente que usa
    firmar_formalizacion_ajax, reutilizada por la vista previa para que el
    usuario vea exactamente el documento sobre el que va a firmar."""
    try:
        formalizacion.documento.open('rb')
        return formalizacion.documento.read()
    finally:
        formalizacion.documento.close()


@login_required
def formalizacion_preview_pdf(request, firma_id):
    """Sirve el PDF base actual de una firma pendiente, para que el modal de
    firma lo renderice con pdf.js (solo la ultima pagina) y el usuario pueda
    elegir manualmente donde estampar su firma."""
    firma = get_object_or_404(FormalizacionFirma, pk=firma_id, responsable=request.user)
    if firma.firmado:
        return HttpResponse('Esta firma ya fue registrada.', status=400)

    formalizacion = firma.formalizacion
    try:
        pdf_bytes = _obtener_pdf_base_actual(formalizacion)
    except FirmaECError as e:
        return HttpResponse(f'No se pudo obtener el documento a firmar: {e}', status=502)

    return HttpResponse(pdf_bytes, content_type='application/pdf')


@login_required
@require_POST
def firmar_formalizacion_ajax(request, firma_id):
    firma = get_object_or_404(FormalizacionFirma, pk=firma_id, responsable=request.user)
    if firma.firmado:
        return JsonResponse({'ok': False, 'error': 'Esta firma ya fue registrada'})

    cedula        = (request.POST.get('cedula') or '').strip()
    password_p12  = request.POST.get('password_p12') or ''
    archivo_p12   = request.FILES.get('archivo_p12')
    if not cedula or not password_p12 or not archivo_p12:
        return JsonResponse({'ok': False, 'error': 'Debe ingresar cedula, contrasena y el archivo .p12 del certificado.'})

    formalizacion = firma.formalizacion
    lin           = formalizacion.lineamiento
    version_map   = {int(k): v for k, v in formalizacion.version_map.items()}
    tipos_incluir = list(_version_map_tipos(version_map))

    total_paginas = _asegurar_total_paginas(formalizacion)

    # Cada evento de firma usa su propio idLote NUEVO y de un solo uso (aunque
    # sea la misma persona/cedula/certificado firmando mas de una vez en el
    # mismo flujo, ej. como Responsable y tambien como Autoridad). El
    # encadenado de firmas no depende de que el WS acumule por idLote: lo hace
    # la app, guardando en BD (formalizacion.documento) el PDF resultante de
    # cada firma antes de pasarlo como base a la siguiente.
    id_lote = generar_id_lote(lin.pk)

    try:
        pdf_base = _obtener_pdf_base_actual(formalizacion)
    except FirmaECError as e:
        return JsonResponse({'ok': False, 'error': f'No se pudo obtener el documento firmado previo: {e}'})

    pkcs12_b64 = base64.b64encode(archivo_p12.read()).decode('ascii')

    # Si el usuario selecciono manualmente el punto de firma en la vista
    # previa (pdf.js), se usan esas coordenadas (en puntos PDF, origen
    # abajo-izquierda, ya convertidas en el navegador). Si no vinieron o son
    # invalidas, se cae al calculo fijo existente como respaldo.
    llx_manual = request.POST.get('llx')
    lly_manual = request.POST.get('lly')
    if llx_manual is not None and lly_manual is not None:
        try:
            llx, lly = float(llx_manual), float(lly_manual)
            # El WS de FirmaEC NO estampa el QR pegado al `lly` enviado: lo
            # dibuja mas abajo (calibrado empiricamente, ver
            # AJUSTE_LLY_MANUAL_PT). Se compensa aqui sumando este offset
            # antes de enviar, para que el estampado final coincida con el
            # punto que el usuario selecciono en la vista previa.
            lly += AJUSTE_LLY_MANUAL_PT
            pagina = str(total_paginas)
        except (TypeError, ValueError):
            llx, lly, pagina = _posicion_firma_pdf(lin, firma.detalle_id, tipos_incluir, total_paginas)
    else:
        llx, lly, pagina = _posicion_firma_pdf(lin, firma.detalle_id, tipos_incluir, total_paginas)

    nombre_pdf = _nombre_pdf(lin.id_numerico, lin.ticket_principal, temporal=False)

    try:
        pdf_firmado = firmar_documento_acumulativo(
            pdf_base, cedula, id_lote, password_p12, pkcs12_b64,
            nombre_documento=nombre_pdf, llx=llx, lly=lly, pagina=pagina,
        )
    except FirmaECError as e:
        return JsonResponse({'ok': False, 'error': f'Error al firmar con FirmaEC: {e}'})

    formalizacion.documento.save(nombre_pdf, ContentFile(pdf_firmado), save=True)

    firma.firmado = True
    firma.fecha_firma = timezone.now()
    firma.save(update_fields=['firmado', 'fecha_firma'])

    formalizado = formalizacion.formalizado
    if formalizado:
        _cerrar_ticket_padre_si_formalizado(formalizacion)

    return JsonResponse({
        'ok': True,
        'formalizado': formalizado,
    })


def _posicion_firma_autoridad(autoridad, total_paginas):
    """Coordenadas de estampado para una Autoridad (Revisor/Aprobador),
    las mismas que usa la firma automatica en _cerrar_ticket_padre_si_formalizado."""
    if autoridad.tipo == 'revisor':
        return REVISOR_FIRMA_X, REVISOR_FIRMA_Y, str(total_paginas)
    return FIRMA_COL_X[0], APROBADO_NOMBRE_Y, str(total_paginas)


@login_required
def formalizacion_preview_autoridad_pdf(request, firma_id):
    """Sirve el PDF actual (sin firmar aun por esta Autoridad) de una firma
    pendiente en FormalizacionFirmaAutoridad, para que la Autoridad pueda
    revisarlo antes de firmarlo. Analoga a formalizacion_preview_pdf pero
    para Autoridades en vez de responsables."""
    autoridad = getattr(request.user, 'autoridad', None)
    if autoridad is None:
        return HttpResponse('No tiene un rol de Autoridad asignado.', status=403)

    firma = get_object_or_404(FormalizacionFirmaAutoridad, pk=firma_id, autoridad=autoridad)
    try:
        pdf_bytes = _obtener_pdf_base_actual(firma.formalizacion)
    except FirmaECError as e:
        return HttpResponse(f'No se pudo obtener el documento a firmar: {e}', status=502)

    return HttpResponse(pdf_bytes, content_type='application/pdf')


@login_required
def mis_firmas_autoridad_view(request):
    """Tabla de documentos pendientes de firma para el usuario logueado,
    cuando ese usuario esta vinculado a un registro de Autoridad (Revisor o
    Aprobador) con `firma_automatica` desactivado. Analoga a
    formalizacion_asignadas_view pero para Autoridades en vez de responsables."""
    autoridad = getattr(request.user, 'autoridad', None)
    if autoridad is None:
        return HttpResponse('No tiene un rol de Autoridad asignado.', status=403)

    firmas_pendientes = list(FormalizacionFirmaAutoridad.objects.filter(
        autoridad=autoridad, firmado=False,
    ).select_related('formalizacion__lineamiento').order_by('-formalizacion__fecha_creacion'))

    firmas_atendidas = list(FormalizacionFirmaAutoridad.objects.filter(
        autoridad=autoridad, firmado=True,
    ).select_related('formalizacion__lineamiento').order_by('-fecha_firma'))

    return render(request, 'formalizacion_autoridad.html', {
        'seccion_activa': 'formalizacion',
        'autoridad': autoridad,
        'firmas_pendientes': firmas_pendientes,
        'firmas_atendidas': firmas_atendidas,
    })


def _firmar_una_autoridad(firma, cedula, password_p12, pkcs12_b64, archivo_p12_content):
    """Firma el documento de una Formalizacion con la Autoridad de `firma`
    (FormalizacionFirmaAutoridad), guarda cedula/password/.p12 en el registro
    de Autoridad para uso futuro del flujo automatico, y marca la firma como
    hecha. El ticket padre en Znuny ya fue cerrado antes (en cuanto las
    autoridades automaticas terminaron, sin esperar a esta), asi que
    _cerrar_ticket_padre_si_formalizado no vuelve a hacer nada mas alla de
    reemplazar formalizacion.documento con el PDF que ya incluye esta firma
    -salvo el caso raro de que otra autoridad automatica siga pendiente.
    Levanta FirmaECError si el WS de firma falla."""
    formalizacion = firma.formalizacion
    lin = formalizacion.lineamiento
    autoridad = firma.autoridad

    total_paginas = _asegurar_total_paginas(formalizacion)
    pdf_base = _obtener_pdf_base_actual(formalizacion)
    id_lote = generar_id_lote(lin.pk)
    nombre_pdf = _nombre_pdf(lin.id_numerico, lin.ticket_principal, temporal=False)
    llx, lly, pagina = _posicion_firma_autoridad(autoridad, total_paginas)

    pdf_firmado = firmar_documento_acumulativo(
        pdf_base, cedula, id_lote, password_p12, pkcs12_b64,
        nombre_documento=nombre_pdf, llx=llx, lly=lly, pagina=pagina,
    )
    formalizacion.documento.save(nombre_pdf, ContentFile(pdf_firmado), save=True)

    firma.firmado = True
    firma.fecha_firma = timezone.now()
    firma.save(update_fields=['firmado', 'fecha_firma'])

    # Se guardan las credenciales en Autoridad para que el dia que se
    # reactive firma_automatica, el sistema ya tenga con que firmar solo.
    archivo_p12_content.seek(0)
    autoridad.cedula = cedula
    autoridad.clave_p12 = password_p12
    autoridad.archivo_p12.save(archivo_p12_content.name, ContentFile(archivo_p12_content.read()), save=False)
    autoridad.save(update_fields=['cedula', 'clave_p12', 'archivo_p12'])

    _cerrar_ticket_padre_si_formalizado(formalizacion)


@login_required
@require_POST
def firmar_autoridad_ajax(request, firma_id):
    """Firma manual de UNA Autoridad sobre una Formalizacion especifica,
    invocada desde la tabla de pendientes de esa Autoridad (mis_firmas_autoridad_view)."""
    autoridad = getattr(request.user, 'autoridad', None)
    if autoridad is None:
        return JsonResponse({'ok': False, 'error': 'No tiene un rol de Autoridad asignado.'})

    firma = get_object_or_404(FormalizacionFirmaAutoridad, pk=firma_id, autoridad=autoridad)
    if firma.firmado:
        return JsonResponse({'ok': False, 'error': 'Esta firma ya fue registrada'})

    cedula       = (request.POST.get('cedula') or '').strip()
    password_p12 = request.POST.get('password_p12') or ''
    archivo_p12  = request.FILES.get('archivo_p12')
    if not cedula or not password_p12 or not archivo_p12:
        return JsonResponse({'ok': False, 'error': 'Debe ingresar cedula, contrasena y el archivo .p12 del certificado.'})

    pkcs12_b64 = base64.b64encode(archivo_p12.read()).decode('ascii')
    try:
        _firmar_una_autoridad(firma, cedula, password_p12, pkcs12_b64, archivo_p12)
    except FirmaECError as e:
        return JsonResponse({'ok': False, 'error': f'Error al firmar con FirmaEC: {e}'})

    return JsonResponse({'ok': True})


@login_required
@require_POST
def firmar_todas_autoridad_ajax(request):
    """Firma en lote TODOS los documentos pendientes de la Autoridad del
    usuario logueado, reutilizando la misma cedula/password/.p12 subidos una
    sola vez en el formulario."""
    autoridad = getattr(request.user, 'autoridad', None)
    if autoridad is None:
        return JsonResponse({'ok': False, 'error': 'No tiene un rol de Autoridad asignado.'})

    cedula       = (request.POST.get('cedula') or '').strip()
    password_p12 = request.POST.get('password_p12') or ''
    archivo_p12  = request.FILES.get('archivo_p12')
    if not cedula or not password_p12 or not archivo_p12:
        return JsonResponse({'ok': False, 'error': 'Debe ingresar cedula, contrasena y el archivo .p12 del certificado.'})

    pkcs12_b64 = base64.b64encode(archivo_p12.read()).decode('ascii')
    pendientes = list(FormalizacionFirmaAutoridad.objects.filter(autoridad=autoridad, firmado=False))

    firmadas, errores = [], []
    for firma in pendientes:
        archivo_p12.seek(0)
        try:
            _firmar_una_autoridad(firma, cedula, password_p12, pkcs12_b64, archivo_p12)
            firmadas.append(firma.pk)
        except FirmaECError as e:
            errores.append(f'{firma.formalizacion.lineamiento.ticket_principal}: {e}')

    return JsonResponse({'ok': True, 'firmadas': firmadas, 'errores': errores})


@login_required
@require_POST
def revertir_formalizacion_ajax(request, lineamiento_id):
    """Revierte (elimina) las Formalizaciones pendientes de un Lineamiento,
    devolviendo sus version_map a disponibles para volver a 'Formalizar'.

    CONDICION DE SEGURIDAD CRITICA: solo se permite si NINGUNA firma de esas
    Formalizaciones ya fue registrada. Si existe al menos una firma parcial
    (ej. SW ya firmo pero BDD no), la reversion se rechaza sin excepcion,
    incluso si el boton en el front estuviera deshabilitado - esta vista es
    la unica fuente de verdad para esa regla."""
    if not request.user.is_staff:
        return JsonResponse({'ok': False, 'error': 'No autorizado'}, status=403)

    lin = get_object_or_404(Lineamiento, pk=lineamiento_id)
    formalizaciones = lin.formalizaciones.all()
    if not formalizaciones.exists():
        return JsonResponse({'ok': False, 'error': 'Este ticket no tiene formalizaciones pendientes.'})

    for formalizacion in formalizaciones:
        if formalizacion.firmas.filter(firmado=True).exists():
            return JsonResponse({
                'ok': False,
                'error': 'No se puede revertir porque ya existen firmas parciales.',
            })

    formalizaciones.delete()
    return JsonResponse({'ok': True})


def _version_map_tipos(version_map):
    """Tipos de detalle presentes en un version_map (para ubicar la columna de firma)."""
    detalle_ids = version_map.keys()
    return LineamientoDetalle.objects.filter(pk__in=detalle_ids).values_list('tipo', flat=True)


# ── GENERAR LINEAMIENTO (SW + redirect BDD) ───────────────────────────────────

@login_required
def generar_lineamiento_view(request, detalle_id):
    detalle = get_object_or_404(LineamientoDetalle, pk=detalle_id, usuario_asignado=request.user)
    if detalle.tipo == 'bdd':
        qs = request.GET.urlencode()
        return redirect(f"/lineamiento/generar-bdd/{detalle_id}/" + (f"?{qs}" if qs else ""))
    if detalle.tipo == 'infraestructura':
        qs = request.GET.urlencode()
        return redirect(f"/lineamiento/generar-capacidad/{detalle_id}/" + (f"?{qs}" if qs else ""))
    clave    = f'chat_sw_{detalle_id}'
    borrador = detalle.generados.filter(es_borrador=True).first()
    if borrador and borrador.chat_estado:
        request.session[clave] = borrador.chat_estado
    elif clave not in request.session:
        request.session[clave] = {'paso': 'inicio', 'info': {}, 'mensajes': []}
    ultima    = detalle.generados.filter(es_borrador=False).order_by('-version').first()
    modo      = request.GET.get('modo', 'nuevo')
    ticket_nv = _limpiar_ticket(request.GET.get('ticket', ''))
    if borrador:
        filas_precarga = [
            {'necesidad': f.necesidad, 'lineamiento': f.lineamiento,
             'mecanismo': f.mecanismo,  'observacion': f.observacion}
            for f in borrador.filas.all()
        ]
    elif modo in ('actualizar', 'nueva_version') and ultima:
        filas_precarga = [
            {'necesidad': f.necesidad, 'lineamiento': f.lineamiento,
             'mecanismo': f.mecanismo,  'observacion': f.observacion}
            for f in ultima.filas.all()
        ]
    else:
        filas_precarga = []
    mensajes_previos = (borrador.chat_estado or {}).get('mensajes', []) if borrador else []
    return render(request, 'generar_lineamiento_software.html', {
        'detalle': detalle, 'paso_inicial': PASOS['inicio'],
        'ultima': ultima, 'ya_generado': ultima is not None,
        'modo': modo, 'ticket_nv': ticket_nv, 'filas_precarga': filas_precarga,
        'hay_borrador': borrador is not None, 'mensajes_previos': mensajes_previos,
        'fila_inicial_transversal': json.dumps(_fila_inicial_transversal()),
    })


# ── AJAX: CHAT ────────────────────────────────────────────────────────────────

@login_required
@require_POST
def chat_software_ajax(request, detalle_id):
    get_object_or_404(LineamientoDetalle, pk=detalle_id, usuario_asignado=request.user)
    clave  = f'chat_sw_{detalle_id}'
    estado = request.session.get(clave, {'paso': 'inicio', 'info': {}, 'mensajes': []})
    data   = json.loads(request.body)
    msg    = data.get('mensaje', '').strip()
    if data.get('reiniciar'):
        estado = {'paso': 'inicio', 'info': {}, 'mensajes': []}
        request.session[clave] = estado
        paso = PASOS['inicio']
        return JsonResponse({'respuesta': paso['pregunta'], 'tipo': paso['tipo'], 'opciones': paso.get('opciones', []), 'guidelines': [], 'completado': False})
    paso_actual = estado['paso']; info = estado['info']
    mensajes = estado.setdefault('mensajes', [])
    if msg and paso_actual != 'completado':
        mensajes.append({'tipo': 'user', 'texto': msg})
        campo = PASOS[paso_actual].get('campo')
        if campo: info[campo] = _procesar_valor(campo, msg)
        paso_actual = _siguiente_paso(paso_actual, info)
        estado['paso'] = paso_actual; estado['info'] = info
    paso_def = PASOS[paso_actual]; completado = paso_actual == 'completado'
    mensajes.append({'tipo': 'bot', 'texto': paso_def['pregunta']})
    request.session[clave] = estado; request.session.modified = True
    return JsonResponse({'respuesta': paso_def['pregunta'], 'tipo': paso_def['tipo'],
        'opciones': paso_def.get('opciones', []), 'guidelines': _guidelines_para_preview(info), 'completado': completado})


# ── AJAX: GUARDAR BORRADOR ─────────────────────────────────────────────────────

@login_required
@require_POST
def guardar_borrador_ajax(request, detalle_id):
    detalle = get_object_or_404(LineamientoDetalle, pk=detalle_id, usuario_asignado=request.user)
    data    = json.loads(request.body)
    filas   = data.get('filas', [])

    generado, _creado = LineamientoGenerado.objects.get_or_create(
        detalle=detalle, es_borrador=True, version=Decimal('0.0'),
        defaults={'creado_por': request.user},
    )

    bdd_sql       = data.get('bdd_sql', '')
    bdd_schema    = data.get('bdd_schema', '')
    bdd_tables    = data.get('bdd_tables', None)
    bdd_sequences = data.get('bdd_sequences', None)
    if bdd_sql:       generado.bdd_sql       = bdd_sql
    if bdd_schema:     generado.bdd_schema    = bdd_schema
    if bdd_tables is not None:    generado.bdd_tables    = bdd_tables
    if bdd_sequences is not None: generado.bdd_sequences = bdd_sequences

    if detalle.tipo == 'software':
        generado.chat_estado = request.session.get(f'chat_sw_{detalle_id}')

    generado.save()

    generado.filas.all().delete()
    for i, fila in enumerate(filas, start=1):
        LineamientoGeneradoFila.objects.create(
            generado=generado, orden=i,
            necesidad=fila.get('necesidad', ''), lineamiento=fila.get('lineamiento', ''),
            mecanismo=fila.get('mecanismo', ''),  observacion=fila.get('observacion', ''),
        )
    return JsonResponse({'ok': True})


# ── AJAX: FINALIZAR ───────────────────────────────────────────────────────────

def _autoformalizar_si_completo(lin, usuario):
    """Si todos los detalles del ticket padre llegaron al 100% (finalizado),
    crea automaticamente la Formalizacion con las versiones actuales (ultima
    version de cada detalle), respetando la validacion de duplicados exacta.
    No hace nada si ya existe una formalizacion para esa combinacion."""
    detalles = list(lin.detalles.all())
    if not detalles or not all(d.finalizado for d in detalles):
        return
    version_map = {d.pk: d.ultima_version.pk for d in detalles}
    _crear_formalizacion_si_no_existe(lin, version_map, usuario)


@login_required
@require_POST
def finalizar_ajax(request, detalle_id):
    detalle = get_object_or_404(LineamientoDetalle, pk=detalle_id, usuario_asignado=request.user)
    data    = json.loads(request.body)
    filas   = data.get('filas', [])
    modo    = data.get('modo', 'nuevo')
    ticket  = _limpiar_ticket(data.get('ticket', '')) or detalle.ticket_interno

    if detalle.tipo == 'infraestructura':
        hay_fila_con_datos = any(
            (f.get('necesidad') or '').strip()
            and (f.get('lineamiento') or '').strip()
            and (f.get('mecanismo') or '').strip()
            for f in filas
        )
        if not detalle.diagrama_personalizado and not hay_fila_con_datos:
            return JsonResponse({
                'ok': False,
                'error': 'Debe completar al menos el diagrama de infraestructura o una fila de lineamiento con datos.',
            })

    # Datos BDD opcionales
    bdd_sql       = data.get('bdd_sql', '')
    bdd_schema    = data.get('bdd_schema', '')
    bdd_tables    = data.get('bdd_tables', None)
    bdd_sequences = data.get('bdd_sequences', None)
    if modo == 'actualizar':
        generado = detalle.generados.filter(es_borrador=False).order_by('-version').first()
        if not generado:
            return JsonResponse({'ok': False, 'error': 'No existe version para actualizar'})
        if generado.en_formalizacion:
            return JsonResponse({
                'ok': False,
                'bloqueado': True,
                'error': 'Esta versión ya está en proceso de formalización. Para realizar cambios, debe crear una nueva versión.',
            })
        generado.filas.all().delete()
        if bdd_sql:       generado.bdd_sql       = bdd_sql
        if bdd_schema:    generado.bdd_schema    = bdd_schema
        if bdd_tables:    generado.bdd_tables    = bdd_tables
        if bdd_sequences: generado.bdd_sequences = bdd_sequences
        generado.save(update_fields=['bdd_sql','bdd_schema','bdd_tables','bdd_sequences'])
    elif modo == 'nueva_version':
        ultima   = detalle.generados.filter(es_borrador=False).order_by('-version').first()
        nueva_v  = (ultima.version + Decimal('1.0')) if ultima else Decimal('1.0')
        generado = LineamientoGenerado.objects.create(
            detalle=detalle, version=nueva_v, ticket=ticket, creado_por=request.user,
            bdd_sql=bdd_sql, bdd_schema=bdd_schema, bdd_tables=bdd_tables, bdd_sequences=bdd_sequences,
        )
    else:
        generado, creado = LineamientoGenerado.objects.get_or_create(
            detalle=detalle, version=Decimal('1.0'),
            defaults={'ticket': ticket, 'creado_por': request.user,
                      'bdd_sql': bdd_sql, 'bdd_schema': bdd_schema,
                      'bdd_tables': bdd_tables, 'bdd_sequences': bdd_sequences},
        )
        if not creado:
            generado.filas.all().delete()
            if bdd_sql:       generado.bdd_sql       = bdd_sql
            if bdd_schema:    generado.bdd_schema    = bdd_schema
            if bdd_tables:    generado.bdd_tables    = bdd_tables
            if bdd_sequences: generado.bdd_sequences = bdd_sequences
            generado.save(update_fields=['bdd_sql','bdd_schema','bdd_tables','bdd_sequences'])
    for i, fila in enumerate(filas, start=1):
        LineamientoGeneradoFila.objects.create(
            generado=generado, orden=i,
            necesidad=fila.get('necesidad', ''), lineamiento=fila.get('lineamiento', ''),
            mecanismo=fila.get('mecanismo', ''),  observacion=fila.get('observacion', ''),
        )
    if modo in ('nuevo', 'nueva_version'):
        ticket_cierre = detalle.ticket_interno if modo == 'nuevo' else ticket
        version_map_pdf = {detalle.pk: generado.pk}
        buf = _generar_pdf_lineamientos(
            detalle.lineamiento, version_map_pdf,
            watermark=True, tipos_incluir=[detalle.tipo],
        )
        nombre_pdf = _nombre_pdf(
            detalle.lineamiento.id_numerico, detalle.lineamiento.ticket_principal,
            tipos=[detalle.tipo], temporal=True,
        )
        tmp_dir = None
        try:
            tmp_dir = tempfile.mkdtemp(prefix=f'pdf_{detalle.pk}_')
            tmp_path = os.path.join(tmp_dir, nombre_pdf)
            with open(tmp_path, 'wb') as f:
                f.write(buf.read())
            resultado = _run_script(
                ZNUNY_SCRIPT_CERRAR, [ticket_cierre, MENSAJE_FINALIZACION, tmp_path],
            )
            if not resultado.get('cerrado'):
                return JsonResponse({
                    'ok': False,
                    'error': resultado.get('error', 'No se pudo adjuntar el PDF y cerrar el ticket'),
                })
        finally:
            if tmp_dir and os.path.exists(tmp_dir):
                shutil.rmtree(tmp_dir, ignore_errors=True)
        _autoformalizar_si_completo(detalle.lineamiento, request.user)
    elif modo == 'actualizar':
        ticket_version  = generado.ticket or detalle.ticket_interno
        version_map_pdf = {detalle.pk: generado.pk}
        buf = _generar_pdf_lineamientos(
            detalle.lineamiento, version_map_pdf,
            watermark=True, tipos_incluir=[detalle.tipo],
        )
        nombre_pdf = _nombre_pdf(
            detalle.lineamiento.id_numerico, detalle.lineamiento.ticket_principal,
            tipos=[detalle.tipo], temporal=True,
        )
        tmp_dir = None
        try:
            tmp_dir = tempfile.mkdtemp(prefix=f'pdf_{detalle.pk}_')
            tmp_path = os.path.join(tmp_dir, nombre_pdf)
            with open(tmp_path, 'wb') as f:
                f.write(buf.read())
            resultado = _run_script(ZNUNY_SCRIPT_NOTA, [ticket_version, tmp_path])
            if not resultado.get('creado'):
                return JsonResponse({
                    'ok': False,
                    'error': resultado.get('error', 'No se pudo adjuntar el PDF a la nota'),
                })
        finally:
            if tmp_dir and os.path.exists(tmp_dir):
                shutil.rmtree(tmp_dir, ignore_errors=True)
    detalle.generados.filter(es_borrador=True).delete()
    return JsonResponse({'ok': True, 'version': generado.version_display(), 'id': generado.pk})


# ── AJAX: CARGAR VERSION ──────────────────────────────────────────────────────

@login_required
def cargar_version_ajax(request, detalle_id):
    detalle  = get_object_or_404(LineamientoDetalle, pk=detalle_id, usuario_asignado=request.user)
    generado = detalle.generados.filter(es_borrador=False).order_by('-version').first()
    if not generado:
        return JsonResponse({'ok': False, 'error': 'Sin version guardada'})
    filas = [{'necesidad': f.necesidad, 'lineamiento': f.lineamiento,
               'mecanismo': f.mecanismo, 'observacion': f.observacion} for f in generado.filas.all()]
    return JsonResponse({'ok': True, 'version': generado.version_display(), 'filas': filas})



# ── DESCARGAR PDF (staff) ─────────────────────────────────────────

@login_required
def descargar_pdf_solicitud(request, lineamiento_id):
    if not request.user.is_staff:
        return redirect('home')
    lin = get_object_or_404(Lineamiento, pk=lineamiento_id)
    try:
        version_map = {}
        if request.method == 'POST':
            for key, val in request.POST.items():
                if key.startswith('version_') and val:
                    try:
                        detalle_pk = int(key.split('_')[1]); version_map[detalle_pk] = int(val)
                    except (ValueError, IndexError): pass
        detalles    = list(lin.detalles.all())
        total       = len(detalles)
        finalizados = sum(1 for d in detalles if d.finalizado)
        progreso    = round(finalizados / total * 100) if total else 0
        es_temporal = progreso < 100
        buf  = _generar_pdf_lineamientos(lin, version_map, watermark=es_temporal)
        resp = HttpResponse(buf.read(), content_type='application/pdf')
        if es_temporal:
            tipos_finalizados = [d.tipo for d in detalles if d.finalizado]
            nombre_pdf = _nombre_pdf(lin.id_numerico, lin.ticket_principal, tipos=tipos_finalizados, temporal=True)
        else:
            nombre_pdf = _nombre_pdf(lin.id_numerico, lin.ticket_principal, temporal=False)
        resp['Content-Disposition'] = f'attachment; filename="{nombre_pdf}"'
        return resp
    except Exception as e:
        return HttpResponse(f'Error al generar PDF: {e}', status=500)


# ── FORMALIZAR (staff) ────────────────────────────────────────────

def _buscar_formalizacion_existente(lin, version_map_normalizado):
    for f in lin.formalizaciones.all():
        if f.version_map == version_map_normalizado:
            return f
    return None


def _crear_formalizacion_si_no_existe(lin, version_map, usuario):
    """Crea la Formalizacion + firmas para exactamente ese version_map si no existe ya una.

    Retorna (formalizacion, creada:bool, error:str|None). Si ya existe (creada o
    finalizada), retorna la existente con creada=False y sin error.
    """
    version_map_normalizado = {str(k): v for k, v in version_map.items()}
    existente = _buscar_formalizacion_existente(lin, version_map_normalizado)
    if existente:
        return existente, False, None

    try:
        buf = _generar_pdf_lineamientos(lin, version_map, watermark=False)
        nombre_pdf = _nombre_pdf(lin.id_numerico, lin.ticket_principal, temporal=False)
        formalizacion = Formalizacion.objects.create(
            lineamiento=lin,
            version_map=version_map_normalizado,
            creado_por=usuario,
            total_paginas=buf.total_paginas,
        )
        formalizacion.documento.save(nombre_pdf, ContentFile(buf.read()), save=True)
        detalles = LineamientoDetalle.objects.filter(pk__in=version_map.keys()).select_related('usuario_asignado')
        FormalizacionFirma.objects.bulk_create([
            FormalizacionFirma(formalizacion=formalizacion, detalle=d, responsable=d.usuario_asignado)
            for d in detalles
        ])
        return formalizacion, True, None
    except Exception as e:
        return None, False, str(e)


@login_required
@require_POST
def formalizar_ajax(request, lineamiento_id):
    if not request.user.is_staff:
        return JsonResponse({'ok': False, 'error': 'No autorizado'}, status=403)
    lin = get_object_or_404(Lineamiento, pk=lineamiento_id)
    version_map = {}
    for key, val in request.POST.items():
        if key.startswith('version_') and val:
            try:
                detalle_pk = int(key.split('_')[1]); version_map[detalle_pk] = int(val)
            except (ValueError, IndexError): pass
    if not version_map:
        return JsonResponse({'ok': False, 'error': 'No se recibieron versiones seleccionadas'})

    version_map_normalizado = {str(k): v for k, v in version_map.items()}
    existente = _buscar_formalizacion_existente(lin, version_map_normalizado)

    if existente:
        ticket_qs = f'?filter_ticket={lin.ticket_principal}'
        if existente.formalizado:
            return JsonResponse({
                'ok': True,
                'duplicado': True,
                'formalizado': True,
                'mensaje': 'Esta formalización ya fue completada anteriormente. Redirigiendo al historial...',
                'redirect_url': reverse('formalizacion_atendidas') + ticket_qs,
            })
        return JsonResponse({
            'ok': True,
            'duplicado': True,
            'formalizado': False,
            'mensaje': 'Esta combinación de versiones ya se encuentra en proceso de firma. Redirigiendo...',
            'redirect_url': reverse('formalizacion_asignadas') + ticket_qs,
        })

    formalizacion, creada, error = _crear_formalizacion_si_no_existe(lin, version_map, request.user)
    if error:
        return JsonResponse({'ok': False, 'error': error}, status=500)
    return JsonResponse({'ok': True, 'formalizacion_id': formalizacion.pk})


@login_required
def estado_formalizacion_ajax(request, lineamiento_id):
    if not request.user.is_staff:
        return JsonResponse({'ok': False, 'error': 'No autorizado'}, status=403)
    lin = get_object_or_404(Lineamiento, pk=lineamiento_id)
    version_map = {}
    for key, val in request.GET.items():
        if key.startswith('version_') and val:
            try:
                detalle_pk = int(key.split('_')[1]); version_map[detalle_pk] = int(val)
            except (ValueError, IndexError): pass
    version_map_normalizado = {str(k): v for k, v in version_map.items()}

    existente = None
    if version_map_normalizado:
        for f in lin.formalizaciones.all():
            if f.version_map == version_map_normalizado:
                existente = f
                break

    if not existente:
        return JsonResponse({'ok': True, 'estado': 'sin_formalizar', 'label': 'Sin Formalizar'})
    if existente.formalizado:
        return JsonResponse({'ok': True, 'estado': 'formalizado', 'label': 'Formalizado'})
    return JsonResponse({'ok': True, 'estado': 'formalizando', 'label': 'Formalizando...'})


# ── ELIMINAR DETALLE ──────────────────────────────────────────────────────────

@login_required
def eliminar_detalle_view(request, detalle_id):
    if not request.user.is_staff:
        return redirect('home')
    detalle = get_object_or_404(LineamientoDetalle, pk=detalle_id)
    if detalle.finalizado:
        return redirect('editar_solicitud', lineamiento_id=detalle.lineamiento.pk)
    lin_id = detalle.lineamiento.pk
    _run_script(ZNUNY_SCRIPT_CERRAR, [detalle.ticket_interno, MENSAJE_ELIMINACION])
    detalle.delete()
    return redirect('editar_solicitud', lineamiento_id=lin_id)


# ── EDITAR SOLICITUD ──────────────────────────────────────────────────────────

@login_required
def editar_solicitud_view(request, lineamiento_id):
    if not request.user.is_staff:
        return redirect('home')
    lin = get_object_or_404(Lineamiento, pk=lineamiento_id)
    detalles_existentes = list(lin.detalles.select_related('usuario_asignado').all())
    tipos_existentes    = {d.tipo for d in detalles_existentes}
    tipos_disponibles   = [t for t in ['software', 'bdd', 'infraestructura'] if t not in tipos_existentes]
    usuarios_software        = Usuario.objects.filter(roles__contains='software',        is_active=True).order_by('first_name', 'username')
    usuarios_bdd             = Usuario.objects.filter(roles__contains='bdd',             is_active=True).order_by('first_name', 'username')
    usuarios_infraestructura = Usuario.objects.filter(roles__contains='infraestructura', is_active=True).order_by('first_name', 'username')
    LABEL_POR_TIPO = {'software': 'Software', 'bdd': 'Base de Datos', 'infraestructura': 'Capacidad'}
    if request.method == 'POST':
        tipos_nuevos = request.POST.getlist('tipos'); errores = []; nuevos = []
        for tipo in tipos_nuevos:
            if tipo in tipos_existentes: continue
            ticket_hijo = _limpiar_ticket(request.POST.get(f'ticket_hijo_{tipo}', ''))
            usuario_id  = request.POST.get(f'usuario_{tipo}', '')
            if not ticket_hijo: errores.append(f'Falta ticket hijo para {LABEL_POR_TIPO.get(tipo, tipo)}.'); continue
            if not usuario_id:  errores.append(f'Falta usuario para {LABEL_POR_TIPO.get(tipo, tipo)}.'); continue
            try: usuario = Usuario.objects.get(pk=usuario_id)
            except Usuario.DoesNotExist: errores.append(f'Usuario invalido para {tipo}.'); continue
            nuevos.append({'tipo': tipo, 'ticket_interno': ticket_hijo, 'usuario_asignado': usuario})
        if errores:
            return render(request, 'editar_solicitud.html', {
                'lin': lin, 'detalles_existentes': detalles_existentes,
                'tipos_disponibles': tipos_disponibles,
                'usuarios_software': usuarios_software, 'usuarios_bdd': usuarios_bdd,
                'usuarios_infraestructura': usuarios_infraestructura, 'errores': errores,
            })
        for d in nuevos:
            LineamientoDetalle.objects.create(lineamiento=lin, **d)
        return redirect('tickets_asignadas')
    return render(request, 'editar_solicitud.html', {
        'lin': lin, 'detalles_existentes': detalles_existentes,
        'tipos_disponibles': tipos_disponibles,
        'usuarios_software': usuarios_software, 'usuarios_bdd': usuarios_bdd,
        'usuarios_infraestructura': usuarios_infraestructura,
    })


# ── CREAR LINEAMIENTO ─────────────────────────────────────────────────────────

def _con_carga_laboral(usuarios_qs):
    """Anota cada usuario con `en_curso` (LineamientoDetalle asignados sin
    ninguna version generada todavia) y `finalizados` (con al menos una
    version generada), para mostrar la carga de trabajo actual en el
    selector de responsables."""
    from django.db.models import Count, Q
    return usuarios_qs.annotate(
        en_curso=Count('lineamientos_asignados', filter=Q(lineamientos_asignados__generados__isnull=True), distinct=True),
        finalizados=Count('lineamientos_asignados', filter=Q(lineamientos_asignados__generados__isnull=False), distinct=True),
    )


@login_required
def crear_lineamiento_view(request):
    if not request.user.is_staff:
        return redirect('home')
    usuarios_software        = _con_carga_laboral(Usuario.objects.filter(roles__contains='software',        is_active=True)).order_by('first_name', 'username')
    usuarios_bdd             = _con_carga_laboral(Usuario.objects.filter(roles__contains='bdd',             is_active=True)).order_by('first_name', 'username')
    usuarios_infraestructura = _con_carga_laboral(Usuario.objects.filter(roles__contains='infraestructura', is_active=True)).order_by('first_name', 'username')
    if request.method == 'POST':
        ticket_principal    = _limpiar_ticket(request.POST.get('ticket_principal', ''))
        id_numerico         = request.POST.get('id_numerico', '').strip()
        tipos_seleccionados = request.POST.getlist('tipos')
        errores = []; detalles_validos = []
        if not ticket_principal: errores.append('Ingrese el ticket principal.')
        if not id_numerico: errores.append('Ingrese el ID numérico del documento.')
        if not tipos_seleccionados: errores.append('Seleccione al menos un tipo.')
        for tipo in tipos_seleccionados:
            ticket_hijo = _limpiar_ticket(request.POST.get(f'ticket_hijo_{tipo}', ''))
            usuario_id  = request.POST.get(f'usuario_{tipo}', '')
            if not ticket_hijo: errores.append(f'Falta ticket hijo para {tipo}.'); continue
            if not usuario_id:  errores.append(f'Falta usuario para {tipo}.'); continue
            try: usuario = Usuario.objects.get(pk=usuario_id)
            except Usuario.DoesNotExist: errores.append(f'Usuario invalido para {tipo}.'); continue
            detalles_validos.append({'tipo': tipo, 'ticket_interno': ticket_hijo, 'usuario_asignado': usuario})
        if errores:
            return render(request, 'crear_lineamiento.html', {
                'ticket_principal': ticket_principal, 'id_numerico': id_numerico,
                'usuarios_software': usuarios_software,
                'usuarios_bdd': usuarios_bdd, 'usuarios_infraestructura': usuarios_infraestructura, 'errores': errores,
            })
        lin = Lineamiento.objects.create(
            ticket_principal=ticket_principal, id_numerico=id_numerico, creado_por=request.user,
        )
        for d in detalles_validos:
            LineamientoDetalle.objects.create(lineamiento=lin, **d)
        return redirect('tickets_asignadas')
    return render(request, 'crear_lineamiento.html', {
        'ticket_principal':         _limpiar_ticket(request.GET.get('ticket', '')),
        'usuarios_software':        usuarios_software,
        'usuarios_bdd':             usuarios_bdd,
        'usuarios_infraestructura': usuarios_infraestructura,
    })
