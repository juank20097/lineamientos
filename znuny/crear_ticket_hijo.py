"""
crear_ticket_hijo.py
Separa (split) un ticket padre en Znuny creando un ticket hijo.

Uso:
    python crear_ticket_hijo.py <numero_padre> <tipo> <nombre_propietario>

    tipo              : "Software" | "Base de Datos" | "Capacidad"
    nombre_propietario: nombre completo o parcial del usuario a asignar como Owner

Retorna JSON:
    {"creado": true,  "numero_hijo": "202607...", "padre": "2026062971004091"}
    {"creado": false, "error": "..."}
"""

import sys
import json
import re
import os
from pathlib import Path
from dotenv import load_dotenv
from znuny_session import ZnunySession, URL_BASE, PlaywrightTimeout

load_dotenv(Path(__file__).parent / '.env')

URL    = f'{URL_BASE}/otrs/index.pl'
TIMEOUT = 120_000

ASUNTO = {
    'Software':      'Generacion de Lineamiento - Software',
    'Base de Datos': 'Generacion de Lineamiento - Base de Datos',
    'Capacidad':     'Generacion de Lineamiento - Capacidad',
}

CUERPO = os.getenv(
    'ZNUNY_CUERPO_HIJO',
    'Estimad@, mediante el presente ticket solicito la generacion de lineamientos '
    'respectivos en relacion a sus competencias sobre el requerimiento solicitado.'
)


def salida(data: dict):
    print(json.dumps(data, ensure_ascii=False))
    sys.exit(0)


def seleccionar_por_texto(page, selector_id: str, texto_parcial: str) -> bool:
    select = page.locator(f'#{selector_id}')
    if select.count() == 0:
        return False
    opciones = select.locator('option')
    for i in range(opciones.count()):
        texto = opciones.nth(i).inner_text().strip()
        if texto_parcial.lower() in texto.lower():
            valor = opciones.nth(i).get_attribute('value')
            select.select_option(value=valor)
            return True
    return False


def seleccionar_usuario_autocomplete(page, input_id: str, nombre: str):
    # 1. Quitar seleccion actual via JS
    try:
        page.evaluate(f"""
            const input = document.getElementById('{input_id}');
            if (input) {{
                const parent = input.closest('div') || input.parentElement;
                const btn = parent.querySelector('a[title*="Quitar"], a[aria-label*="Quitar"]');
                if (btn) btn.click();
            }}
        """)
        page.wait_for_timeout(500)
    except Exception:
        pass

    # 2. Foco y limpieza
    search = page.locator(f'#{input_id}')
    search.click();  page.wait_for_timeout(500)
    search.focus();  page.wait_for_timeout(300)
    search.fill(''); page.wait_for_timeout(200)

    # 3. Buscar por apellido
    palabras = nombre.strip().split()
    termino  = palabras[-2] if len(palabras) >= 3 else (palabras[1] if len(palabras) == 2 else palabras[0])
    search.fill(termino)
    page.keyboard.press('End')
    page.wait_for_timeout(2000)

    # 4. Seleccionar resultado
    opcion = page.locator(
        f".InputField_Results li:has-text('{nombre}'), "
        f".ui-menu-item:has-text('{nombre}'), "
        f"li[title*='{termino}']"
    ).first
    if opcion.count() > 0:
        opcion.click(); page.wait_for_timeout(500)
    else:
        items   = page.locator('.InputField_Results li, .ui-autocomplete li')
        clicked = False
        for i in range(items.count()):
            texto = items.nth(i).inner_text().strip()
            if nombre.lower() in texto.lower() or termino.lower() in texto.lower():
                items.nth(i).click(); clicked = True; break
        if not clicked:
            page.keyboard.press('ArrowDown'); page.wait_for_timeout(200)
            page.keyboard.press('Enter')
        page.wait_for_timeout(500)


def crear_hijo(numero_padre: str, tipo: str, propietario_nombre: str):
    asunto = ASUNTO.get(tipo)
    if not asunto:
        salida({'creado': False, 'error': f'Tipo invalido: {tipo}'})

    try:
        with ZnunySession() as page:
            page.set_default_timeout(TIMEOUT)
            page.context.set_default_timeout(TIMEOUT)

            # IR AL TICKET PADRE
            page.goto(f'{URL}?Action=AgentTicketZoom;TicketNumber={numero_padre}', wait_until='networkidle')
            page.wait_for_timeout(1500)

            if 'No TicketID is given!' in page.locator('body').inner_text():
                salida({'creado': False, 'error': f'Ticket padre {numero_padre} no encontrado'})

            # CLICK EN SEPARAR
            separar = page.get_by_role('link', name='Separar')
            if separar.count() == 0:
                separar = page.locator('a', has_text='Separar').first
            separar.click()
            page.wait_for_load_state('networkidle')
            page.wait_for_timeout(1500)

            # POPUP DE CONFIRMACION
            try:
                popup_btn = page.locator("div.Dialog button", has_text='Separar').first
                if popup_btn.count() > 0:
                    popup_btn.click()
                    page.wait_for_load_state('networkidle')
                    page.wait_for_timeout(2000)
            except PlaywrightTimeout:
                pass

            # ESPERAR FORMULARIO
            page.wait_for_selector('#Subject', timeout=TIMEOUT)
            page.wait_for_timeout(1000)

            # ASUNTO
            page.fill('#Subject', asunto)

            # CUERPO via CKEditor
            try:
                page.wait_for_function(
                    "typeof CKEDITOR !== 'undefined' && CKEDITOR.instances && CKEDITOR.instances['RichText']",
                    timeout=15000
                )
                page.wait_for_timeout(800)
                page.evaluate(f"""
                    (function() {{
                        var ed = CKEDITOR.instances['RichText'];
                        if (ed) ed.setData('{CUERPO}');
                    }})();
                """)
                page.wait_for_timeout(500)
            except Exception:
                try:
                    frame = page.frame_locator('iframe.cke_wysiwyg_frame').first
                    body  = frame.locator('body')
                    body.click()
                    page.keyboard.press('Control+a')
                    page.keyboard.press('Delete')
                    body.type(CUERPO)
                except Exception:
                    pass

            # REFRESH LISTA DE AGENTES
            try:
                refresh_btn = page.locator('a i.fa-refresh').first
                if refresh_btn.count() > 0:
                    refresh_btn.click()
                    page.wait_for_timeout(2000)
            except Exception:
                pass

            # PROPIETARIO
            try:
                seleccionar_usuario_autocomplete(page, 'NewUserID_Search', propietario_nombre)
            except Exception:
                seleccionar_por_texto(page, 'NewUserID', propietario_nombre)

            # RESPONSABLE: siempre Daysi Chango
            try:
                seleccionar_usuario_autocomplete(page, 'NewResponsibleID_Search', 'Juan Estevez')
            except Exception:
                seleccionar_por_texto(page, 'NewResponsibleID', 'Daysi')

            # CREAR
            btn_crear = page.locator("button[type='submit']", has_text='Crear').first
            if btn_crear.count() == 0:
                btn_crear = page.locator("button[type='submit']").first
            with page.expect_navigation(wait_until='networkidle', timeout=TIMEOUT):
                btn_crear.click()
            page.wait_for_timeout(2000)

            # EXTRAER NUMERO DEL TICKET HIJO
            numero_hijo = ''
            body_text   = page.locator('body').inner_text()
            m = re.search(r'Ticket\s+["\u201c\u00ab]?([\d]{10,})["\u201d\u00bb]?\s+creado', body_text)
            if m:
                numero_hijo = m.group(1)

            if not numero_hijo:
                confirmacion = page.locator("a:has-text('creado')").first
                if confirmacion.count() > 0:
                    href = confirmacion.get_attribute('href') or ''
                    page.goto(f'{URL_BASE}{href}', wait_until='networkidle')
                    page.wait_for_timeout(1000)
                    m2 = re.search(r'TicketNumber=([\d]+)', page.url)
                    if m2:
                        numero_hijo = m2.group(1)

        salida({'creado': True, 'numero_hijo': numero_hijo, 'padre': numero_padre, 'tipo': tipo})

    except Exception as e:
        salida({'creado': False, 'error': str(e)})


if __name__ == '__main__':
    if len(sys.argv) < 4:
        salida({'creado': False, 'error': 'Uso: python crear_ticket_hijo.py <numero_padre> <tipo> <nombre_propietario>'})
    crear_hijo(
        numero_padre       = sys.argv[1].strip(),
        tipo               = sys.argv[2].strip(),
        propietario_nombre = sys.argv[3].strip(),
    )
