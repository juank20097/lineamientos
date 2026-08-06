"""
znuny_session.py
Manejo centralizado de sesion para los scripts de Znuny.

Uso:
    from znuny_session import ZnunySession

    with ZnunySession() as page:
        page.goto(...)
        # ... lógica del script
    # Al salir del context manager la sesion se conserva en disco
    # (storage_state) para que la proxima ejecucion sea inmediata.
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
LOGIN_TIMEOUT = 30_000

# Znuny/OTRS mantiene polling en segundo plano (notificaciones, sesion), por lo
# que 'networkidle' puede no resolverse nunca. Se usa 'domcontentloaded' + espera
# explicita del selector relevante en su lugar.


def _cargar_credenciales():
    load_dotenv(ENV_FILE)
    user     = os.getenv('ZNUNY_USER')
    password = os.getenv('ZNUNY_PASS')
    headless = os.getenv('ZNUNY_HEADLESS', 'true').strip().lower() != 'false'
    return user, password, headless


def _esta_logueado(page) -> bool:
    """Navega a la raiz y verifica si hay formulario de login (sesion expirada)."""
    try:
        page.goto(URL, wait_until='domcontentloaded', timeout=LOGIN_TIMEOUT)
        # Atajo: si la URL resultante ya es una pantalla autenticada (redirect
        # tipico tras sesion valida, ej. ?Action=Admin o el dashboard de agente),
        # evitamos esperar selectores y confirmamos sesion activa de inmediato.
        if 'Action=Login' not in page.url and page.locator('#User').count() == 0:
            return True
        # Espera a que se resuelva a login (#User) o a la vista logueada (barra superior).
        page.wait_for_selector('#User, #LoginButton, #Header, #ToolBar', state='visible', timeout=LOGIN_TIMEOUT)
        return page.locator('#User').count() == 0
    except PlaywrightTimeout:
        return False
    except Exception:
        return False


def _hacer_login(page, user: str, password: str) -> bool:
    """Realiza el login. Retorna True si fue exitoso. Lanza RuntimeError con detalle si falla."""
    page.goto(URL, wait_until='domcontentloaded', timeout=LOGIN_TIMEOUT)

    try:
        page.wait_for_selector('#User', state='visible', timeout=LOGIN_TIMEOUT)
    except PlaywrightTimeout:
        # Puede que ya haya sesion activa (no aparece el formulario de login).
        if page.locator('#User').count() == 0:
            return True
        _screenshot_debug(page, 'debug_login_no_user_field.png')
        raise RuntimeError(
            "No se encontro el campo de usuario (#User) en la pagina de login. "
            "Verifica que el selector siga vigente en la version actual de Znuny."
        )

    try:
        page.fill('#User', user)
        page.wait_for_selector('#Password', state='visible', timeout=LOGIN_TIMEOUT)
        page.fill('#Password', password)
    except PlaywrightTimeout as e:
        _screenshot_debug(page, 'debug_login_fill_error.png')
        raise RuntimeError(f"No se pudo completar el formulario de login: {e}")

    try:
        page.wait_for_selector('#LoginButton', state='visible', timeout=LOGIN_TIMEOUT)
        with page.expect_navigation(wait_until='domcontentloaded', timeout=LOGIN_TIMEOUT):
            page.click('#LoginButton')
    except PlaywrightTimeout as e:
        _screenshot_debug(page, 'debug_login_click_error.png')
        raise RuntimeError(f"El boton de login (#LoginButton) no respondio a tiempo: {e}")

    try:
        page.wait_for_selector('#User, #Header, #ToolBar', state='visible', timeout=LOGIN_TIMEOUT)
    except PlaywrightTimeout:
        _screenshot_debug(page, 'debug_login_post_click.png')

    return page.locator('#User').count() == 0


def _screenshot_debug(page, filename: str):
    """Guarda una captura de pantalla para diagnostico cuando el login falla."""
    try:
        page.screenshot(path=str(Path(__file__).parent / filename))
    except Exception:
        pass


def _sesion_caducada(page) -> bool:
    """Detecta si la pagina actual es el formulario de login (sesion expirada
    a mitad de una operacion, tras una navegacion posterior al login inicial)."""
    try:
        return page.locator('#User').count() > 0 or 'Action=Login' in page.url
    except Exception:
        return False


def reautenticar(page):
    """Fuerza un nuevo login cuando se detecta sesion caducada en pleno script
    (no al inicio, donde ya lo cubre ZnunySession.__enter__). Borra la sesion
    en disco para no reutilizar cookies invalidas en la proxima ejecucion."""
    user, password, _ = _cargar_credenciales()
    if not user or not password:
        raise RuntimeError('ZNUNY_USER o ZNUNY_PASS no definidos en .env')
    try:
        SESSION_FILE.unlink(missing_ok=True)
    except Exception:
        pass
    page.context.clear_cookies()
    ok = _hacer_login(page, user, password)
    if not ok:
        raise RuntimeError('Re-login fallido en Znuny: credenciales incorrectas o pagina inesperada.')
    page.context.storage_state(path=str(SESSION_FILE))


class ZnunySession:
    """
    Context manager que maneja la sesion de Znuny.

    - Intenta reutilizar sesion guardada en disco.
    - Si expiro o no existe, hace login automatico.
    - Guarda la sesion actualizada tras cada login.
    - NO cierra sesion al salir del bloque with (se preserva para reutilizar
      en la siguiente ejecucion y evitar loguearse desde cero cada vez).

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
        try:
            sesion_activa = _esta_logueado(self._page)
        except Exception:
            sesion_activa = False

        if not sesion_activa:
            # Sesion restaurada invalida/corrupta o inexistente: limpiar cookies
            # antes de reintentar el login para evitar estados intermedios raros.
            self._context.clear_cookies()
            try:
                ok = _hacer_login(self._page, user, password)
            except RuntimeError:
                self.__exit__(None, None, None)
                raise
            if not ok:
                self.__exit__(None, None, None)
                raise RuntimeError('Login fallido en Znuny: credenciales incorrectas o pagina inesperada.')
            # Guardar nueva sesion
            self._context.storage_state(path=str(SESSION_FILE))
        else:
            # Sesion activa: refrescar el archivo de sesion
            self._context.storage_state(path=str(SESSION_FILE))

        return self._page

    def __exit__(self, exc_type, exc_val, exc_tb):
        # NOTA: NO se cierra sesion en Znuny ni se borra SESSION_FILE aqui.
        # La sesion se mantiene viva en disco (storage_state) para que la
        # siguiente ejecucion de cualquier script reutilice el login existente
        # en vez de autenticarse desde cero cada vez.

        # Cerrar browser y playwright (solo libera el proceso local, no la sesion remota)
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
