"""
crear_nota.py
Crea una nota interna en un ticket de Znuny.

Uso:
    python crear_nota.py <numero_ticket> [adjunto_pdf]

Retorna JSON:
    {"creado": true,  "numero": "2026072271003084"}
    {"creado": false, "error": "..."}
"""

import sys
import json
import os
from pathlib import Path
from dotenv import load_dotenv
from znuny_session import ZnunySession, URL_BASE

load_dotenv(Path(__file__).parent / '.env')

ASUNTO_NOTA = os.getenv('ZNUNY_ASUNTO_NOTA', 'Nota - Actualizacion del Lineamiento')
CUERPO_NOTA = os.getenv('ZNUNY_CUERPO_NOTA', 'Estimad@s, se realizo una actualizacion al lineamiento.')
URL         = f'{URL_BASE}/otrs/index.pl'


def salida(data: dict):
    print(json.dumps(data, ensure_ascii=False))
    sys.exit(0)


def crear_nota(numero_ticket: str, adjunto_pdf: str = None):
    try:
        with ZnunySession() as page:

            # IR AL TICKET
            page.goto(
                f'{URL}?Action=AgentTicketZoom;TicketNumber={numero_ticket}',
                wait_until='networkidle',
            )
            page.wait_for_timeout(1500)

            if 'No TicketID is given!' in page.locator('body').inner_text():
                salida({'creado': False, 'error': f'Ticket {numero_ticket} no encontrado'})

            # EXTRAER URL DE NOTA
            nota_link = page.locator("a[href*='AgentTicketNote']").first
            if nota_link.count() == 0:
                salida({'creado': False, 'error': 'No se encontro el enlace de Nota'})
            href     = nota_link.get_attribute('href')
            nota_url = f'{URL_BASE}{href}' if href.startswith('/') else href

            # NAVEGAR AL FORMULARIO DE NOTA
            page.goto(nota_url, wait_until='networkidle')
            page.wait_for_timeout(2000)

            # ASUNTO
            try:
                page.fill('#Subject', ASUNTO_NOTA)
            except Exception:
                pass

            # CUERPO via CKEditor
            try:
                page.wait_for_function(
                    "typeof CKEDITOR !== 'undefined' && CKEDITOR.instances && CKEDITOR.instances['RichText']",
                    timeout=15000
                )
                page.wait_for_timeout(800)
                page.evaluate(f"CKEDITOR.instances['RichText'].setData('{CUERPO_NOTA}');")
                page.wait_for_timeout(500)
            except Exception:
                try:
                    page.fill('#RichText', CUERPO_NOTA)
                except Exception:
                    pass

            # ADJUNTO PDF (si se especifico)
            if adjunto_pdf:
                ruta_adjunto = Path(adjunto_pdf)
                if not ruta_adjunto.exists():
                    salida({'creado': False, 'error': f'Adjunto no encontrado: {adjunto_pdf}'})
                try:
                    file_input = page.locator("input[type='file']").first
                    if file_input.count() == 0:
                        salida({'creado': False, 'error': 'No se encontro el campo de adjuntos'})
                    file_input.set_input_files(str(ruta_adjunto))
                    page.wait_for_timeout(1500)
                    if ruta_adjunto.name not in page.locator('body').inner_text():
                        salida({'creado': False, 'error': 'El adjunto no se registro en el ticket'})
                except Exception as e:
                    salida({'creado': False, 'error': f'Fallo al subir adjunto: {e}'})

            # SUBMIT
            btn_submit = page.locator(
                "button[type='submit']:has-text('Guardar'), "
                "button[type='submit']:has-text('Submit'), "
                "button[type='submit']"
            ).first
            if btn_submit.count() == 0:
                salida({'creado': False, 'error': 'No se encontro el boton de submit'})

            with page.expect_navigation(wait_until='networkidle'):
                btn_submit.click()
            page.wait_for_timeout(2000)

        salida({'creado': True, 'numero': numero_ticket})

    except Exception as e:
        salida({'creado': False, 'error': str(e)})


if __name__ == '__main__':
    if len(sys.argv) < 2:
        salida({'creado': False, 'error': 'Uso: python crear_nota.py <numero_ticket> [adjunto_pdf]'})
    adjunto_arg = sys.argv[2] if len(sys.argv) >= 3 else None
    crear_nota(sys.argv[1].strip(), adjunto_arg)
