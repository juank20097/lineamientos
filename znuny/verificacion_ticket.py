"""
verificacion_ticket.py
Verifica si un ticket existe en Znuny.

Uso:
    python verificacion_ticket.py <numero_ticket>

Retorna JSON:
    {"existe": true,  "numero": "2026072271003084"}
    {"existe": false, "numero": "2026072271003084"}
"""

import sys
import json
import re
from znuny_session import ZnunySession, _sesion_caducada, reautenticar

URL = 'https://soporte.iess.gob.ec/otrs/index.pl'


def salida(data: dict):
    print(json.dumps(data, ensure_ascii=False))
    sys.exit(0)


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
            salida({'existe': existe, 'numero': numero})
    except Exception as e:
        salida({'existe': False, 'numero': numero, 'error': str(e)})


if __name__ == '__main__':
    if len(sys.argv) < 2:
        salida({'existe': False, 'error': 'Uso: python verificacion_ticket.py <numero>'})
    verificar_ticket(sys.argv[1].strip())
