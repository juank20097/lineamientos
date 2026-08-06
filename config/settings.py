from decouple import config
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = config('DJANGO_SECRET_KEY')
DEBUG = config('DJANGO_DEBUG', default=False, cast=bool)
ALLOWED_HOSTS = config('DJANGO_ALLOWED_HOSTS', default='localhost').split(',')

# Subruta bajo la que se expone la app detras de un proxy compartido (ej.
# "/tools/lineamientos", igual que OpenProject). Vacio = se sirve en la raiz.
FORCE_SCRIPT_NAME = config('DJANGO_FORCE_SCRIPT_NAME', default='') or None
USE_X_FORWARDED_HOST = bool(FORCE_SCRIPT_NAME)

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    # Propias
    'apps.usuarios',
    'apps.core',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'config.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'config.wsgi.application'

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': config('POSTGRES_DB'),
        'USER': config('POSTGRES_USER'),
        'PASSWORD': config('POSTGRES_PASSWORD'),
        'HOST': config('POSTGRES_HOST', default='127.0.0.1'),
        'PORT': config('POSTGRES_PORT', default='5432'),
    }
}

AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

AUTH_USER_MODEL = 'usuarios.Usuario'

LANGUAGE_CODE = 'es-ec'
TIME_ZONE = 'America/Guayaquil'
USE_I18N = True
USE_TZ = True

STATIC_URL = (FORCE_SCRIPT_NAME or '') + '/static/'
STATICFILES_DIRS = [BASE_DIR / 'static']
STATIC_ROOT = BASE_DIR / 'staticfiles'

# MEDIA_URL/MEDIA_ROOT ya no se usan para servir archivos de usuario (ver
# apps/core/storage.py: diagramas/PDFs/certificados viven en Postgres como
# BLOB), pero se dejan por si algun paquete de terceros los referencia.
MEDIA_URL = (FORCE_SCRIPT_NAME or '') + '/media/'
MEDIA_ROOT = BASE_DIR / 'media'

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# Auth
# Se usan nombres de URL (no rutas literales) porque Django los resuelve via
# resolve_url()/reverse(), que si respeta FORCE_SCRIPT_NAME; una ruta como
# '/' quedaria fija en la raiz del dominio y rompe el logout/login cuando la
# app esta detras de un prefijo (ej. /tools/lineamientos/).
LOGIN_URL = 'login'
LOGIN_REDIRECT_URL = 'home'
LOGOUT_REDIRECT_URL = 'login'

if FORCE_SCRIPT_NAME:
    CSRF_COOKIE_PATH = FORCE_SCRIPT_NAME
    SESSION_COOKIE_PATH = FORCE_SCRIPT_NAME

# Playwright
ZNUNY_PYTHON = config('ZNUNY_PYTHON', default='python')

# Znuny (enlaces directos a tickets)
ZNUNY_URL_BASE = config('ZNUNY_URL_BASE', default='https://soporte.iess.gob.ec/otrs')

# FirmaEC - Web Service de Firma Electronica (IESS)
FIRMA_EC_URL = config('FIRMA_EC_URL', default='http://192.168.114.216:8090/api/iess/firmaec/firmar')
FIRMA_EC_ENLACES_URL = config('FIRMA_EC_ENLACES_URL', default='http://192.168.114.216:8090/api/iess/firmaec/enlaces')
FIRMA_EC_CLAVE_PUBLICA_URL = config('FIRMA_EC_CLAVE_PUBLICA_URL', default='http://192.168.114.216:8090/api/iess/seguridad/clave-publica')

# Email (notificacion de firma de autoridad pendiente)
EMAIL_BACKEND = 'django.core.mail.backends.smtp.EmailBackend'
EMAIL_HOST = config('EMAIL_HOST', default='mail.iess.gob.ec')
EMAIL_PORT = config('EMAIL_PORT', default=25, cast=int)
EMAIL_HOST_USER = config('EMAIL_HOST_USER', default='')
EMAIL_HOST_PASSWORD = config('EMAIL_HOST_PASSWORD', default='')
EMAIL_USE_TLS = config('EMAIL_USE_TLS', default=False, cast=bool)
EMAIL_USE_SSL = config('EMAIL_USE_SSL', default=False, cast=bool)
DEFAULT_FROM_EMAIL = config('DEFAULT_FROM_EMAIL', default='lineamientos@iess.gob.ec')

# Messages
from django.contrib.messages import constants as messages_constants
MESSAGE_TAGS = {
    messages_constants.DEBUG:   'info',
    messages_constants.INFO:    'info',
    messages_constants.SUCCESS: 'success',
    messages_constants.WARNING: 'warning',
    messages_constants.ERROR:   'danger',
}

# Confiar en el dominio sdnas.iess.gob.ec
CSRF_TRUSTED_ORIGINS = [
   'https://sdnas.iess.gob.ec'
]
