"""
znuny_session.py
Manejo centralizado de sesion para los scripts de Znuny.

Uso:
    from znuny_session import ZnunySession

    with ZnunySession() as page:
        page.goto(...)
        # ... lógica del script
    # Al salir del context manager cierra sesion automaticamente
"""

import os
import json
from pathlib import Path
from dotenv import load_dotenv
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout

ENV_FILE     = Path(__file__).parent / '.env'
SESSION_FILE = Path(__file__).parent / '.znuny_session.json'
URL_BASE     = 'https://soporte.iess.gob.ec'
URL          = f'{URL_BASE}/otrs/index.pl'
TIMEOUT      = 120_000


def _cargar_credenciales():
    load_dotenv(ENV_FILE)
    user     = os.getenv('ZNUNY_USER')
    password = os.getenv('ZNUNY_PASS')
    headless = os.getenv('ZNUNY_HEADLESS', 'true').strip().lower() != 'false'
    return user, password, headless


def _esta_logueado(page) -> bool:
    """Navega a la raiz y verifica si hay formulario de login (sesion expirada)."""
    try:
        page.goto(URL, wait_until='networkidle', timeout=20_000)
        # Si aparece el input #User, la sesion expiro
        return page.locator('#User').count() == 0
    except Exception:
        return False


def _hacer_login(page, user: str, password: str) -> bool:
    """Realiza el login. Retorna True si fue exitoso."""
    page.goto(URL, wait_until='networkidle')
    if page.locator('#User').count() > 0:
        page.fill('#User', user)
        page.fill('#Password', password)
        with page.expect_navigation(wait_until='networkidle', timeout=TIMEOUT):
            page.click('#LoginButton')
    return page.locator('#User').count() == 0


def _hacer_logout(page):
    """Cierra la sesion en Znuny."""
    try:
        page.goto(f'{URL}?Action=Logout', wait_until='networkidle', timeout=15_000)
    except Exception:
        pass


class ZnunySession:
    """
    Context manager que maneja la sesion de Znuny.

    - Intenta reutilizar sesion guardada en disco.
    - Si expiro o no existe, hace login automatico.
    - Guarda la sesion actualizada tras cada login.
    - Cierra sesion al salir del bloque with.

    Ejemplo:
        with ZnunySession() as page:
            page.goto(...)
    """

    def __init__(self, headless: bool = None):
        self.headless = headless
        self._pw      = None
        self._browser = None
        self._context = None
        self._page    = None

    def __enter__(self):
        user, password, headless_env = _cargar_credenciales()
        if not user or not password:
            raise RuntimeError('ZNUNY_USER o ZNUNY_PASS no definidos en .env')

        # ZNUNY_HEADLESS=false muestra la UI; true (default) corre sin ventana
        headless_final = self.headless if self.headless is not None else headless_env

        self._pw      = sync_playwright().start()
        self._browser = self._pw.chromium.launch(headless=headless_final)

        # Intentar restaurar sesion guardada
        if SESSION_FILE.exists():
            try:
                self._context = self._browser.new_context(
                    storage_state=str(SESSION_FILE)
                )
            except Exception:
                self._context = self._browser.new_context()
        else:
            self._context = self._browser.new_context()

        self._context.set_default_timeout(TIMEOUT)
        self._context.set_default_navigation_timeout(TIMEOUT)
        self._page = self._context.new_page()

        # Verificar si la sesion guardada sigue activa
        if not _esta_logueado(self._page):
            ok = _hacer_login(self._page, user, password)
            if not ok:
                self.__exit__(None, None, None)
                raise RuntimeError('Login fallido en Znuny')
            # Guardar nueva sesion
            self._context.storage_state(path=str(SESSION_FILE))
        else:
            # Sesion activa: refrescar el archivo de sesion
            self._context.storage_state(path=str(SESSION_FILE))

        return self._page

    def __exit__(self, exc_type, exc_val, exc_tb):
        # Cerrar sesion en Znuny
        if self._page:
            try:
                _hacer_logout(self._page)
            except Exception:
                pass

        # Eliminar archivo de sesion (ya que cerramos sesion)
        if SESSION_FILE.exists():
            try:
                SESSION_FILE.unlink()
            except Exception:
                pass

        # Cerrar browser y playwright
        try:
            if self._browser:
                self._browser.close()
        except Exception:
            pass
        try:
            if self._pw:
                self._pw.stop()
        except Exception:
            pass

        return False  # No suprimir excepciones
