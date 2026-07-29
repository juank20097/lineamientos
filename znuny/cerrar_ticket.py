"""
cerrar_ticket.py
Cierra un ticket en Znuny via AgentTicketClose.

Uso:
    python cerrar_ticket.py <numero_ticket> [mensaje_override]

Retorna JSON:
    {"cerrado": true,  "numero": "2026072271003084"}
    {"cerrado": false, "error": "..."}
"""

import sys
import json
import os
from pathlib import Path
from dotenv import load_dotenv
from znuny_session import ZnunySession, PlaywrightTimeout, URL_BASE

load_dotenv(Path(__file__).parent / '.env')

ASUNTO_CIERRE  = os.getenv('ZNUNY_ASUNTO_CIERRE',  'Cierre de Solicitud de Lineamientos')
MENSAJE_CIERRE = os.getenv('ZNUNY_MENSAJE_CIERRE', 'Se generaron los lineamientos respectivos.')
URL            = f'{URL_BASE}/otrs/index.pl'


def salida(data: dict):
    print(json.dumps(data, ensure_ascii=False))
    sys.exit(0)


def cerrar_ticket(numero_ticket: str, mensaje_override: str = None):
    asunto  = ASUNTO_CIERRE
    mensaje = mensaje_override if mensaje_override else MENSAJE_CIERRE

    try:
        with ZnunySession() as page:

            # IR AL TICKET
            page.goto(
                f'{URL}?Action=AgentTicketZoom;TicketNumber={numero_ticket}',
                wait_until='networkidle',
            )
            page.wait_for_timeout(1500)

            if 'No TicketID is given!' in page.locator('body').inner_text():
                salida({'cerrado': False, 'error': f'Ticket {numero_ticket} no encontrado'})

            # EXTRAER URL DEL BOTON CERRAR
            cerrar_link = page.locator("a[href*='AgentTicketClose']").first
            if cerrar_link.count() == 0:
                salida({'cerrado': False, 'error': 'No se encontro el enlace de cierre'})
            href      = cerrar_link.get_attribute('href')
            close_url = f'{URL_BASE}{href}' if href.startswith('/') else href

            # NAVEGAR AL FORMULARIO DE CIERRE
            page.goto(close_url, wait_until='networkidle')
            page.wait_for_timeout(2000)

            # ACTIVAR CreateArticle si no esta marcado
            try:
                checkbox = page.locator('#CreateArticle')
                if checkbox.count() > 0 and not checkbox.is_checked():
                    checkbox.check()
                    page.wait_for_timeout(500)
            except Exception:
                pass

            # ASUNTO
            try:
                page.fill('#Subject', asunto)
            except Exception:
                pass

            # CUERPO
            try:
                tiene_cke = page.evaluate(
                    "typeof CKEDITOR !== 'undefined' && !!CKEDITOR.instances['RichText']"
                )
                if tiene_cke:
                    page.evaluate(f"CKEDITOR.instances['RichText'].setData('{mensaje}');")
                else:
                    page.fill('#RichText', mensaje)
                page.wait_for_timeout(500)
            except Exception:
                try:
                    page.fill('#RichText', mensaje)
                except Exception:
                    pass

            # SUBMIT
            btn_submit = page.locator(
                "button[type='submit']:has-text('Cerrar'), "
                "button[type='submit']:has-text('Enviar'), "
                "button[type='submit']"
            ).first
            if btn_submit.count() == 0:
                salida({'cerrado': False, 'error': 'No se encontro el boton de submit'})

            with page.expect_navigation(wait_until='networkidle'):
                btn_submit.click()
            page.wait_for_timeout(2000)

        salida({'cerrado': True, 'numero': numero_ticket})

    except Exception as e:
        salida({'cerrado': False, 'error': str(e)})


if __name__ == '__main__':
    if len(sys.argv) < 2:
        salida({'cerrado': False, 'error': 'Uso: python cerrar_ticket.py <numero_ticket> [mensaje]'})
    mensaje_arg = sys.argv[2] if len(sys.argv) >= 3 else None
    cerrar_ticket(sys.argv[1].strip(), mensaje_arg)
