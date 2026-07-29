import json
import math
import os
import subprocess
import re
import tempfile
import textwrap
from decimal import Decimal
from io import BytesIO
from pathlib import Path
from datetime import date

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse, HttpResponse
from django.shortcuts import render, redirect, get_object_or_404
from django.views.decorators.http import require_POST

from apps.usuarios.models import Usuario
from .models import (
    Lineamiento, LineamientoDetalle, Guideline,
    LineamientoGenerado, LineamientoGeneradoFila,
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


def _limpiar_ticket(valor):
    return re.sub(r'^ticket#', '', valor.strip(), flags=re.IGNORECASE)


def _run_script(script, args):
    try:
        r = subprocess.run(
            [settings.ZNUNY_PYTHON, str(script)] + args,
            capture_output=True, text=True, timeout=150,
        )
        if r.stdout.strip():
            return json.loads(r.stdout)
        return {'error': r.stderr[-500:] if r.stderr else 'Sin salida'}
    except subprocess.TimeoutExpired:
        return {'error': 'Timeout'}
    except Exception as e:
        return {'error': str(e)}


# ── GENERACION PDF ───────────────────────────────────────────────────────────

PDF_REVISADO_NOMBRE = os.getenv('PDF_REVISADO_NOMBRE', 'Juan Carlos Estevez Hidalgo')
PDF_REVISADO_CARGO  = os.getenv('PDF_REVISADO_CARGO',  'Lider de Arquitectura')
PDF_APROBADO_NOMBRE = os.getenv('PDF_APROBADO_NOMBRE', 'Andres Garcia Romero')
PDF_APROBADO_CARGO  = os.getenv('PDF_APROBADO_CARGO',  'Subdirector Nacional de Arquitectura y Soluciones')


def _generar_pdf_lineamientos(lin, version_map, watermark=False, tipos_incluir=None):
    """tipos_incluir: iterable de tipos ('software','bdd','infraestructura') a incluir; None = todos."""
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib import colors
    from reportlab.lib.units import cm, mm
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_JUSTIFY
    from reportlab.platypus import (
        SimpleDocTemplate, Table, TableStyle, Paragraph,
        Spacer, PageBreak, KeepTogether,
    )
    from reportlab.graphics.shapes import Drawing, Rect, Line, String as RLString, Group
    from reportlab.graphics import renderPDF

    hoy        = date.today().strftime('%d/%m/%Y')
    num_doc    = lin.ticket_principal
    PAGE_W, PAGE_H = landscape(A4)
    MARGIN = 1.5 * cm

    # Colores institucionales
    VERDE  = colors.HexColor('#006847')
    GRIS   = colors.HexColor('#475569')
    GRIS_C = colors.HexColor('#f8fafc')
    BDD_C  = colors.HexColor('#7c3aed')

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
            gen_sel   = det.generados.order_by('-version').first()
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
            Paragraph(f'<b>LINEAMIENTOS TÉCNICOS</b> &nbsp;&nbsp;|&nbsp;&nbsp; Fecha: <b>{hoy}</b> &nbsp;&nbsp;|&nbsp;&nbsp; Doc: <b>PAS-MLT-{num_doc}</b>', S_TITLE),
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
            else detalle.generados.order_by('-version').first()
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
                Paragraph(responsable, S_CELL),
                Paragraph(tipo_label, S_CENTER),
                Paragraph(fila.necesidad or '', S_CELL),
                Paragraph(fila.lineamiento or '', S_CELL),
                Paragraph(fila.mecanismo or '', S_CELL),
                Paragraph(hoy, S_CENTER),
                Paragraph(fila.observacion or '', S_CELL),
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

    # ── FIRMAS (nueva pagina) ──
    story.append(PageBreak())
    story += cabecera()
    story.append(Paragraph('FIRMAS DE RESPONSABILIDAD', estilo('ft', fontName='Helvetica-Bold', fontSize=10, alignment=TA_CENTER, textColor=VERDE)))
    story.append(Spacer(1, 10*mm))

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
    story.append(fila_firmas('Elaborado\npor:', elaborados[:3]))
    story.append(Spacer(1, 6*mm))

    # Revisado
    story.append(fila_firmas('Revisado\npor:', [{'nombre': PDF_REVISADO_NOMBRE, 'cargo': PDF_REVISADO_CARGO}]))
    story.append(Spacer(1, 6*mm))

    # Aprobado
    story.append(fila_firmas('Aprobado\npor:', [{'nombre': PDF_APROBADO_NOMBRE, 'cargo': PDF_APROBADO_CARGO}]))

    # ── PAGINAS BDD (diagrama, tablas, SQL) ──
    bdd_detalle = lin.detalles.filter(tipo='bdd').first() if (tipos_incluir is None or 'bdd' in tipos_incluir) else None
    if bdd_detalle:
        gen_pk   = version_map.get(bdd_detalle.pk)
        bdd_gen  = (
            bdd_detalle.generados.filter(pk=gen_pk).first()
            if gen_pk
            else bdd_detalle.generados.order_by('-version').first()
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
                        Paragraph(col_def['name'] + pk_label, S_CELL),
                        Paragraph(col_def['type'], S_CENTER),
                        Paragraph(col_def.get('size', ''), S_CENTER),
                        Paragraph(col_def.get('nullable', ''), S_CENTER),
                        Paragraph(col_def.get('description', ''), S_CELL),
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

    # ── BUILD PDF ──
    buf = BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=landscape(A4),
        leftMargin=MARGIN, rightMargin=MARGIN,
        topMargin=MARGIN, bottomMargin=MARGIN,
        title=f'Lineamientos PAS-MLT-{num_doc}',
        author='IESS - SDNAS',
    )

    def on_page(canvas, doc):
        """Pie de pagina con numero."""
        canvas.saveState()
        canvas.setFont('Helvetica', 6)
        canvas.setFillColor(colors.HexColor('#94a3b8'))
        canvas.drawRightString(PAGE_W - MARGIN, 0.5*cm, f'Pág. {doc.page}')
        canvas.drawString(MARGIN, 0.5*cm, f'PAS-MLT-{num_doc} | {hoy} | IESS - DNTI - SDNAS')
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
    return buf


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


def _parse_sql(content):
    # Eliminar bloques /* ... */ antes de parsear (FKs comentadas, etc)
    content = re.sub(r'/\*.*?\*/', '', content, flags=re.DOTALL)
    schema = None; tables = {}; sequences = []
    m = re.search(r'CREATE\s+TABLE\s+(\w+)\.(\w+)', content, re.I)
    if m:
        schema = m.group(1).upper()
    for m in re.finditer(r'CREATE\s+SEQUENCE\s+\w+\.(\w+)', content, re.I):
        sequences.append(m.group(1).upper())
    for m in re.finditer(r'CREATE\s+TABLE\s+\w+\.(\w+)\s*\((.+?)\)\s*;', content, re.I | re.DOTALL):
        tname = m.group(1).upper()
        body  = m.group(2)
        tables[tname] = {'columns': _parse_columnas(body), 'pks': [], 'fks': []}
        # PKs inline dentro del CREATE TABLE: CONSTRAINT xxx PRIMARY KEY (col)
        pk_m = re.search(r'CONSTRAINT\s+\w+\s+PRIMARY\s+KEY\s*\(([^)]+)\)', body, re.I)
        if pk_m:
            pks = [p.strip().upper() for p in pk_m.group(1).split(',')]
            tables[tname]['pks'] = pks
            for col in tables[tname]['columns']:
                if col['name'] in pks: col['pk'] = True
    for m in re.finditer(r"COMMENT\s+ON\s+COLUMN\s+\w+\.(\w+)\.(\w+)\s+IS\s+'(.*?)'", content, re.I | re.DOTALL):
        tname = m.group(1).upper(); cname = m.group(2).upper()
        desc  = m.group(3).strip().replace("''", "'")
        if tname in tables:
            for col in tables[tname]['columns']:
                if col['name'] == cname: col['description'] = desc; break
    for m in re.finditer(
        r'ALTER\s+TABLE\s+(?:\w+\.)?(\w+)\s+ADD\s+CONSTRAINT\s+\w+\s+PRIMARY\s+KEY\s*\(([^)]+)\)',
        content, re.I | re.DOTALL
    ):
        tname = m.group(1).upper(); pks = [p.strip().upper() for p in m.group(2).split(',')]
        if tname in tables:
            tables[tname]['pks'] = pks
            for col in tables[tname]['columns']:
                if col['name'] in pks: col['pk'] = True
    for m in re.finditer(
        r'ALTER\s+TABLE\s+(?:\w+\.)?(\w+)\s+ADD\s+CONSTRAINT\s+\w+\s+FOREIGN\s+KEY\s*\(([^)]+)\)\s+REFERENCES\s+(?:\w+\.)?(\w+)\s*\(([^)]+)\)',
        content, re.I | re.DOTALL
    ):
        tname = m.group(1).upper()
        if tname in tables:
            tables[tname]['fks'].append({
                'columns':     [c.strip().upper() for c in m.group(2).split(',')],
                'ref_table':   m.group(3).upper(),
                'ref_columns': [c.strip().upper() for c in m.group(4).split(',')],
            })
    return {'schema': schema, 'tables': tables, 'sequences': sequences}


# ── VISTAS BDD ────────────────────────────────────────────────────────────────

@login_required
def generar_lineamiento_bdd_view(request, detalle_id):
    detalle   = get_object_or_404(LineamientoDetalle, pk=detalle_id, usuario_asignado=request.user)
    ultima    = detalle.generados.order_by('-version').first()
    modo      = request.GET.get('modo', 'nuevo')
    ticket_nv = _limpiar_ticket(request.GET.get('ticket', ''))
    filas_precarga = []
    bdd_precarga   = {}
    if modo in ('actualizar', 'nueva_version') and ultima:
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
    return render(request, 'generar_lineamiento_bdd.html', {
        'detalle': detalle, 'ultima': ultima, 'ya_generado': ultima is not None,
        'modo': modo, 'ticket_nv': ticket_nv,
        'filas_precarga': filas_precarga,
        'bdd_precarga':   json.dumps(bdd_precarga),
    })


@login_required
@require_POST
def cargar_sql_ajax(request, detalle_id):
    get_object_or_404(LineamientoDetalle, pk=detalle_id, usuario_asignado=request.user)
    sql_file = request.FILES.get('sql_file')
    if not sql_file:
        return JsonResponse({'ok': False, 'error': 'No se recibio el archivo SQL'})
    try:
        content = sql_file.read().decode('utf-8', errors='replace')
        parsed  = _parse_sql(content)
        return JsonResponse({
            'ok': True,
            'schema':    parsed['schema'],
            'tables':    parsed['tables'],
            'sequences': parsed['sequences'],
            'sql_raw':   content,
        })
    except Exception as e:
        return JsonResponse({'ok': False, 'error': str(e)})


# ── LOGICA GUIDELINES ─────────────────────────────────────────────────────────

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
    return JsonResponse(_run_script(ZNUNY_SCRIPT_VERIFICAR, [numero]))


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


# ── HOME ──────────────────────────────────────────────────────────────────────

@login_required
def home_view(request):
    user = request.user
    if user.is_staff:
        solicitudes = list(Lineamiento.objects.filter(
            creado_por=user
        ).prefetch_related('detalles__usuario_asignado', 'detalles__generados').order_by('-fecha_creacion'))
        progreso = {}
        for sol in solicitudes:
            detalles    = list(sol.detalles.all())
            total       = len(detalles)
            finalizados = sum(1 for d in detalles if d.finalizado)
            progreso[sol.pk] = round(finalizados / total * 100) if total else 0
        solicitudes_asignadas = [s for s in solicitudes if progreso.get(s.pk, 0) < 100]
        solicitudes_atendidas = [s for s in solicitudes if progreso.get(s.pk, 0) == 100]
        detalles_pendientes = []; detalles_atendidos = []
    else:
        detalles_qs         = LineamientoDetalle.objects.filter(usuario_asignado=user).select_related('lineamiento').prefetch_related('generados')
        detalles_pendientes = [d for d in detalles_qs if not d.finalizado]
        detalles_atendidos  = [d for d in detalles_qs if d.finalizado]
        solicitudes = []; solicitudes_asignadas = []; solicitudes_atendidas = []; progreso = {}
    return render(request, 'home.html', {
        'solicitudes':           solicitudes,
        'solicitudes_asignadas': solicitudes_asignadas,
        'solicitudes_atendidas': solicitudes_atendidas,
        'progreso':              progreso,
        'progreso_json':         json.dumps(progreso),
        'detalles_pendientes':   detalles_pendientes,
        'detalles_atendidos':    detalles_atendidos,
    })


# ── GENERAR LINEAMIENTO (SW + redirect BDD) ───────────────────────────────────

@login_required
def generar_lineamiento_view(request, detalle_id):
    detalle = get_object_or_404(LineamientoDetalle, pk=detalle_id, usuario_asignado=request.user)
    if detalle.tipo == 'bdd':
        qs = request.GET.urlencode()
        return redirect(f"/lineamiento/generar-bdd/{detalle_id}/" + (f"?{qs}" if qs else ""))
    clave = f'chat_sw_{detalle_id}'
    if clave not in request.session:
        request.session[clave] = {'paso': 'inicio', 'info': {}, 'mensajes': []}
    ultima    = detalle.generados.order_by('-version').first()
    modo      = request.GET.get('modo', 'nuevo')
    ticket_nv = _limpiar_ticket(request.GET.get('ticket', ''))
    filas_precarga = []
    if modo in ('actualizar', 'nueva_version') and ultima:
        filas_precarga = [
            {'necesidad': f.necesidad, 'lineamiento': f.lineamiento,
             'mecanismo': f.mecanismo,  'observacion': f.observacion}
            for f in ultima.filas.all()
        ]
    return render(request, 'generar_lineamiento.html', {
        'detalle': detalle, 'paso_inicial': PASOS['inicio'],
        'ultima': ultima, 'ya_generado': ultima is not None,
        'modo': modo, 'ticket_nv': ticket_nv, 'filas_precarga': filas_precarga,
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
    if msg and paso_actual != 'completado':
        campo = PASOS[paso_actual].get('campo')
        if campo: info[campo] = _procesar_valor(campo, msg)
        paso_actual = _siguiente_paso(paso_actual, info)
        estado['paso'] = paso_actual; estado['info'] = info
    paso_def = PASOS[paso_actual]; completado = paso_actual == 'completado'
    request.session[clave] = estado; request.session.modified = True
    return JsonResponse({'respuesta': paso_def['pregunta'], 'tipo': paso_def['tipo'],
        'opciones': paso_def.get('opciones', []), 'guidelines': _guidelines_para_preview(info), 'completado': completado})


# ── AJAX: FINALIZAR ───────────────────────────────────────────────────────────

@login_required
@require_POST
def finalizar_ajax(request, detalle_id):
    detalle = get_object_or_404(LineamientoDetalle, pk=detalle_id, usuario_asignado=request.user)
    data    = json.loads(request.body)
    filas   = data.get('filas', [])
    modo    = data.get('modo', 'nuevo')
    ticket  = _limpiar_ticket(data.get('ticket', '')) or detalle.ticket_interno
    # Datos BDD opcionales
    bdd_sql       = data.get('bdd_sql', '')
    bdd_schema    = data.get('bdd_schema', '')
    bdd_tables    = data.get('bdd_tables', None)
    bdd_sequences = data.get('bdd_sequences', None)
    if modo == 'actualizar':
        generado = detalle.generados.order_by('-version').first()
        if not generado:
            return JsonResponse({'ok': False, 'error': 'No existe version para actualizar'})
        generado.filas.all().delete()
        if bdd_sql:       generado.bdd_sql       = bdd_sql
        if bdd_schema:    generado.bdd_schema    = bdd_schema
        if bdd_tables:    generado.bdd_tables    = bdd_tables
        if bdd_sequences: generado.bdd_sequences = bdd_sequences
        generado.save(update_fields=['bdd_sql','bdd_schema','bdd_tables','bdd_sequences'])
    elif modo == 'nueva_version':
        ultima   = detalle.generados.order_by('-version').first()
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
        tmp_path = None
        try:
            with tempfile.NamedTemporaryFile(
                suffix='.pdf', prefix=f'temporal_{detalle.pk}_',
                delete=False,
            ) as tmp:
                tmp.write(buf.read())
                tmp_path = tmp.name
            resultado = _run_script(
                ZNUNY_SCRIPT_CERRAR, [ticket_cierre, MENSAJE_FINALIZACION, tmp_path],
            )
            if not resultado.get('cerrado'):
                return JsonResponse({
                    'ok': False,
                    'error': resultado.get('error', 'No se pudo adjuntar el PDF y cerrar el ticket'),
                })
        finally:
            if tmp_path and os.path.exists(tmp_path):
                os.remove(tmp_path)
    elif modo == 'actualizar':
        ticket_version = generado.ticket or detalle.ticket_interno
        _run_script(ZNUNY_SCRIPT_NOTA, [ticket_version])
    return JsonResponse({'ok': True, 'version': generado.version_display(), 'id': generado.pk})


# ── AJAX: CARGAR VERSION ──────────────────────────────────────────────────────

@login_required
def cargar_version_ajax(request, detalle_id):
    detalle  = get_object_or_404(LineamientoDetalle, pk=detalle_id, usuario_asignado=request.user)
    generado = detalle.generados.order_by('-version').first()
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
    lin = get_object_or_404(Lineamiento, pk=lineamiento_id, creado_por=request.user)
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
        buf  = _generar_pdf_lineamientos(lin, version_map, watermark=(progreso < 100))
        resp = HttpResponse(buf.read(), content_type='application/pdf')
        resp['Content-Disposition'] = f'attachment; filename="PAS-MLT-{lin.ticket_principal}_lineamientos.pdf"'
        return resp
    except Exception as e:
        return HttpResponse(f'Error al generar PDF: {e}', status=500)


# ── ELIMINAR DETALLE ──────────────────────────────────────────────────────────

@login_required
def eliminar_detalle_view(request, detalle_id):
    if not request.user.is_staff:
        return redirect('home')
    detalle = get_object_or_404(LineamientoDetalle, pk=detalle_id, lineamiento__creado_por=request.user)
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
    lin = get_object_or_404(Lineamiento, pk=lineamiento_id, creado_por=request.user)
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
        return redirect('home')
    return render(request, 'editar_solicitud.html', {
        'lin': lin, 'detalles_existentes': detalles_existentes,
        'tipos_disponibles': tipos_disponibles,
        'usuarios_software': usuarios_software, 'usuarios_bdd': usuarios_bdd,
        'usuarios_infraestructura': usuarios_infraestructura,
    })


# ── CREAR LINEAMIENTO ─────────────────────────────────────────────────────────

@login_required
def crear_lineamiento_view(request):
    if not request.user.is_staff:
        return redirect('home')
    usuarios_software        = Usuario.objects.filter(roles__contains='software',        is_active=True).order_by('first_name', 'username')
    usuarios_bdd             = Usuario.objects.filter(roles__contains='bdd',             is_active=True).order_by('first_name', 'username')
    usuarios_infraestructura = Usuario.objects.filter(roles__contains='infraestructura', is_active=True).order_by('first_name', 'username')
    if request.method == 'POST':
        ticket_principal    = _limpiar_ticket(request.POST.get('ticket_principal', ''))
        tipos_seleccionados = request.POST.getlist('tipos')
        errores = []; detalles_validos = []
        if not ticket_principal: errores.append('Ingrese el ticket principal.')
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
                'ticket_principal': ticket_principal, 'usuarios_software': usuarios_software,
                'usuarios_bdd': usuarios_bdd, 'usuarios_infraestructura': usuarios_infraestructura, 'errores': errores,
            })
        lin = Lineamiento.objects.create(ticket_principal=ticket_principal, creado_por=request.user)
        for d in detalles_validos:
            LineamientoDetalle.objects.create(lineamiento=lin, **d)
        return redirect('home')
    return render(request, 'crear_lineamiento.html', {
        'ticket_principal':         _limpiar_ticket(request.GET.get('ticket', '')),
        'usuarios_software':        usuarios_software,
        'usuarios_bdd':             usuarios_bdd,
        'usuarios_infraestructura': usuarios_infraestructura,
    })
