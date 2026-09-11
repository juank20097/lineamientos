"""
verificacion_ticket.py
Verifica si un ticket existe en Znuny y obtiene su Asunto.

Uso:
    python verificacion_ticket.py <numero_ticket>

Retorna JSON:
    {"existe": true,  "numero": "2026072271003084", "asunto": "..."}
    {"existe": false, "numero": "2026072271003084", "asunto": ""}
"""

import sys
import json
import re
from znuny_session import ZnunySession, _sesion_caducada, reautenticar

URL = 'https://soporte.iess.gob.ec/otrs/index.pl'


def salida(data: dict):
    print(json.dumps(data, ensure_ascii=False))
    sys.exit(0)


def extraer_asunto(page) -> str:
    """Obtiene el Asunto del ticket desde el panel de detalle del primer
    articulo en el Zoom: el <label>Asunto:</label> seguido de <p class="Value">.
    NO usa page.title(): en el Zoom de Znuny el <title> del documento es un
    texto generico ('Detalle - Ticket - OTRS::ITSM 6'), no el asunto real."""
    valor = page.locator(
        'label:has-text("Asunto:") + p.Value, label:has-text("Asunto:") ~ p.Value'
    ).first
    if valor.count() > 0:
        texto = valor.inner_text().strip()
        if texto:
            return texto
    return ''


def verificar_ticket(numero: str):
    try:
        with ZnunySession() as page:
            page.goto(
                f'{URL}?Action=AgentTicketZoom;TicketNumber={numero}',
                wait_until='networkidle',
            )
            page.wait_for_timeout(1000)

            if _sesion_caducada(page):
                reautenticar(page)
                page.goto(
                    f'{URL}?Action=AgentTicketZoom;TicketNumber={numero}',
                    wait_until='networkidle',
                )
                page.wait_for_timeout(1000)

            body = page.locator('body').inner_text()
            existe = 'No TicketID is given!' not in body and 'No se encontr' not in body
            asunto = extraer_asunto(page) if existe else ''
            salida({'existe': existe, 'numero': numero, 'asunto': asunto})
    except Exception as e:
        salida({'existe': False, 'numero': numero, 'asunto': '', 'error': str(e)})


if __name__ == '__main__':
    if len(sys.argv) < 2:
        salida({'existe': False, 'error': 'Uso: python verificacion_ticket.py <numero>'})
    verificar_ticket(sys.argv[1].strip())
