"""Integracion con el Web Service de Firma Electronica (FirmaEC) del IESS."""

import base64
import logging
import time

import requests
from django.conf import settings

logger = logging.getLogger(__name__)


class FirmaECError(Exception):
    """Error de conexion o respuesta negativa del WS de FirmaEC."""


def _log_llamada(titulo, ok, detalle=''):
    """Log minimo de una llamada al WS de FirmaEC: solo confirma exito/fallo
    y un detalle no sensible (status code, mensaje de error). NUNCA debe
    recibir el payload, headers, credenciales cifradas ni el contenido del
    PDF - esos datos no deben quedar en los logs del servidor."""
    if ok:
        logger.info('%s: OK%s', titulo, f' ({detalle})' if detalle else '')
    else:
        logger.error('%s: fallo%s', titulo, f' ({detalle})' if detalle else '')


def generar_id_lote(lineamiento_id):
    return f'LOT-{lineamiento_id}-{int(time.time())}'


def obtener_clave_publica_firmaec():
    """Obtiene la clave publica RSA del WS de seguridad (GET /seguridad/clave-publica).

    El WS confirma el esquema exacto en su respuesta:
      - cifradoDatos:  AES/GCM/NoPadding        (para pkcs12 y password)
      - algoritmo:     RSA/ECB/OAEPPadding con SHA-256 y MGF1(SHA-256)  (para la clave AES)
      - clavePublica:  Base64 DER (SubjectPublicKeyInfo, sin cabeceras PEM)

    Retorna el objeto de clave publica RSA utilizable por `cryptography`.
    """
    try:
        resp = requests.get(settings.FIRMA_EC_CLAVE_PUBLICA_URL, timeout=30)
    except requests.exceptions.RequestException as e:
        _log_llamada('FIRMA EC (GET clave-publica)', ok=False, detalle=str(e))
        raise FirmaECError(f'No se pudo obtener la clave publica de FirmaEC: {e}')

    if resp.status_code != 200:
        _log_llamada('FIRMA EC (GET clave-publica)', ok=False, detalle=f'HTTP {resp.status_code}')
        raise FirmaECError(f'FirmaEC (clave-publica) respondio con estado {resp.status_code}: {resp.text[:300]}')
    _log_llamada('FIRMA EC (GET clave-publica)', ok=True)

    try:
        data = resp.json()
        clave_raw = data.get('clavePublica')
    except ValueError:
        clave_raw = resp.text.strip()

    if not isinstance(clave_raw, str) or not clave_raw:
        raise FirmaECError('FirmaEC no devolvio una clave publica valida.')

    from cryptography.hazmat.primitives.serialization import load_der_public_key, load_pem_public_key

    clave_raw = clave_raw.strip()
    if '-----BEGIN' in clave_raw:
        return load_pem_public_key(clave_raw.encode('utf-8'))

    try:
        der_bytes = base64.b64decode(clave_raw)
        return load_der_public_key(der_bytes)
    except Exception as e:
        raise FirmaECError(f'No se pudo interpretar la clave publica de FirmaEC: {e}')


def _cifrar_aes_gcm(valor_b64: str, clave_aes: bytes) -> str:
    """Cifra un valor (ya en su forma Base64, tal como lo pide el WS) con
    AES-256-GCM. El resultado se codifica en Base64 como IV(12) + ciphertext+tag,
    confirmado con el script de referencia oficial del WS."""
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    import os as _os

    aesgcm = AESGCM(clave_aes)
    iv = _os.urandom(12)
    ciphertext = aesgcm.encrypt(iv, valor_b64.encode('utf-8'), None)
    return base64.b64encode(iv + ciphertext).decode('ascii')


def _cifrar_rsa_oaep(valor_bytes: bytes, clave_publica) -> str:
    """Cifra bytes (la clave AES, 32 bytes) con RSA-OAEP/SHA-256+MGF1(SHA-256),
    devolviendo el resultado en Base64. Este esquema si soporta el tamano de
    una clave AES (32 bytes) dentro del limite de un bloque RSA."""
    from cryptography.hazmat.primitives.asymmetric import padding
    from cryptography.hazmat.primitives import hashes

    cifrado = clave_publica.encrypt(
        valor_bytes,
        padding.OAEP(
            mgf=padding.MGF1(algorithm=hashes.SHA256()),
            algorithm=hashes.SHA256(),
            label=None,
        ),
    )
    return base64.b64encode(cifrado).decode('ascii')


def cifrar_credenciales_firmaec(pkcs12_b64, password):
    """Cifra pkcs12Cifrado y passwordCifrado con AES-256-GCM usando una clave
    AES aleatoria (unica por firma), y cifra esa clave AES con RSA-OAEP usando
    la clave publica de FirmaEC (esquema hibrido confirmado por el propio WS
    y su script de referencia oficial).

    IMPORTANTE (segun el script de referencia del WS): tanto el pkcs12 como el
    password se cifran en su forma Base64 (no el texto plano directo) - el
    password se codifica primero a Base64 antes de cifrar, igual que el pkcs12
    (que ya viene en Base64 desde el archivo .p12 original).

    Retorna (pkcs12_cifrado, password_cifrado, clave_aes_cifrada)."""
    import os as _os

    clave_publica = obtener_clave_publica_firmaec()
    clave_aes = _os.urandom(32)  # AES-256

    password_b64 = base64.b64encode(password.encode('utf-8')).decode('ascii')

    pkcs12_cifrado    = _cifrar_aes_gcm(pkcs12_b64, clave_aes)
    password_cifrado  = _cifrar_aes_gcm(password_b64, clave_aes)
    clave_aes_cifrada = _cifrar_rsa_oaep(clave_aes, clave_publica)

    return pkcs12_cifrado, password_cifrado, clave_aes_cifrada


def obtener_documento_firmado_por_lote(id_lote):
    """Consulta GET /firmaec/enlaces/<idLote> para obtener el PDF firmado
    hasta el momento para ese lote. Es la fuente de verdad del 'ultimo
    documento firmado' antes de pasarlo a la siguiente firma en cadena.

    La respuesta de este endpoint NO trae el binario del PDF: trae un JSON
    con 'estado' y una lista 'enlaces', cada uno con una URL pre-firmada
    (ej. S3/MinIO) desde donde hay que descargar el archivo por separado:

        {
          "idLote": "...", "estado": "OK",
          "enlaces": [{"nombre": "...signed.pdf", "url": "http://.../...",
                        "horasExpiracion": 24}]
        }

    Retorna los bytes del PDF descargado, o None si el lote aun no tiene
    ningun enlace disponible (ej. primera firma, lote recien creado)."""
    url = f'{settings.FIRMA_EC_ENLACES_URL}/{id_lote}'
    try:
        resp = requests.get(url, timeout=30)
    except requests.exceptions.RequestException as e:
        _log_llamada('FIRMA EC (GET /enlaces)', ok=False, detalle=str(e))
        raise FirmaECError(f'No se pudo consultar el documento del lote {id_lote}: {e}')

    if resp.status_code == 404:
        _log_llamada('FIRMA EC (GET /enlaces)', ok=False, detalle='HTTP 404 (lote sin enlaces aun)')
        return None
    if resp.status_code != 200:
        _log_llamada('FIRMA EC (GET /enlaces)', ok=False, detalle=f'HTTP {resp.status_code}')
        raise FirmaECError(f'FirmaEC (enlaces) respondio con estado {resp.status_code}: {resp.text[:300]}')
    _log_llamada('FIRMA EC (GET /enlaces)', ok=True)

    try:
        data = resp.json()
    except ValueError:
        # Algunos WS devuelven el binario directo en vez de JSON.
        return resp.content or None

    if not isinstance(data, dict):
        return None

    if data.get('estado') and data.get('estado') != 'OK':
        raise FirmaECError(f'FirmaEC (enlaces) devolvio estado "{data.get("estado")}": {data.get("mensaje", "")}')

    enlaces = data.get('enlaces')
    if not enlaces or not isinstance(enlaces, list):
        return None

    # Si el WS acumula un enlace por cada firma dentro del mismo idLote, el
    # mas reciente (el que incluye todas las firmas hasta el momento) es el
    # ultimo de la lista, no el primero: usar enlaces[0] aqui devolveria
    # siempre el PDF de la primera firma, descartando las siguientes.
    # La URL de descarga es pre-firmada (ej. S3/MinIO) e incluye credenciales
    # de firma en la query string: nunca se loguea completa, solo el host.
    descarga_url = enlaces[-1].get('url')
    if not descarga_url:
        return None

    try:
        resp_doc = requests.get(descarga_url, timeout=60)
    except requests.exceptions.RequestException as e:
        _log_llamada('FIRMA EC (descarga documento firmado)', ok=False, detalle=str(e))
        raise FirmaECError(f'No se pudo descargar el documento firmado desde el enlace del lote {id_lote}: {e}')

    if resp_doc.status_code != 200:
        _log_llamada('FIRMA EC (descarga documento firmado)', ok=False, detalle=f'HTTP {resp_doc.status_code}')
        raise FirmaECError(f'La descarga del documento firmado respondio con estado {resp_doc.status_code}.')
    _log_llamada('FIRMA EC (descarga documento firmado)', ok=True)

    return resp_doc.content


def firmar_documento_pdf(pdf_bytes, cedula_usuario, id_lote, pkcs12_cifrado, password_cifrado,
                          clave_aes_cifrada, nombre_documento='documento.pdf', llx=0, lly=0, pagina='1'):
    """Envia un PDF al WS de FirmaEC (POST /firmaec/firmar) para aplicar la
    firma digital del usuario.

    IMPORTANTE: este WS NO devuelve el documento firmado en la respuesta del
    POST. Tras una respuesta exitosa, el PDF firmado (acumulado del lote) debe
    obtenerse por separado con GET /firmaec/enlaces/<idLote>
    (ver obtener_documento_firmado_por_lote). Esta funcion solo confirma que
    el WS acepto la solicitud de firma.

    - pdf_bytes: contenido binario del PDF a firmar (debe ser el ultimo PDF
      firmado en cadena si ya hubo firmas previas; ver
      obtener_documento_firmado_por_lote).
    - cedula_usuario: cedula del firmante.
    - id_lote: identificador del lote de firma. Debe ser el MISMO id_lote
      para todo el proceso de firma en cadena de una Formalizacion (no uno
      nuevo por cada firmante), para que /enlaces/<idLote> devuelva el
      acumulado correcto.
    - pkcs12_cifrado / password_cifrado / clave_aes_cifrada: salida de
      cifrar_credenciales_firmaec (AES-256-GCM para los datos, RSA-OAEP para
      la clave AES), unica por cada llamada de firma.
    - llx/lly/pagina: posicion del estampado de firma (QR) en el PDF.

    Lanza FirmaECError si el WS falla o responde con un error.
    """
    payload = {
        'idLote': id_lote,
        'cedula': cedula_usuario,
        'pkcs12Cifrado': pkcs12_cifrado,
        'passwordCifrado': password_cifrado,
        'claveAesCifrada': clave_aes_cifrada,
        'documentos': [
            {
                'nombre': nombre_documento,
                'documento': base64.b64encode(pdf_bytes).decode('ascii'),
            },
        ],
        'parametros': {
            # El WS de FirmaEC rechaza llx/lly con decimales (solo acepta
            # enteros); las coordenadas del PDF (reportlab) sí son floats,
            # por lo que se truncan aqui, justo antes de armar el payload.
            'llx': int(llx),
            'lly': int(lly),
            'pagina': str(pagina),
            'tipoEstampado': 'QR',
        },
    }

    # 'No se recibio el callback de firma' es un error transitorio del lado
    # del servidor (el microservicio interno firmadigital-servicio no
    # respondio a tiempo), confirmado reproduciendo el MISMO payload/idLote
    # manualmente y viendo que si funciona poco despues. Se reintenta unas
    # pocas veces con backoff antes de reportar fallo definitivo.
    intentos = 3
    ultimo_error = None
    headers = {'Content-Type': 'application/json'}
    for intento in range(intentos):
        try:
            resp = requests.post(settings.FIRMA_EC_URL, json=payload, headers=headers, timeout=60)
        except requests.exceptions.RequestException as e:
            _log_llamada('FIRMA EC (POST /firmar)', ok=False, detalle=str(e))
            raise FirmaECError(f'No se pudo conectar con el servicio de FirmaEC: {e}')

        if resp.status_code != 200:
            _log_llamada('FIRMA EC (POST /firmar)', ok=False, detalle=f'HTTP {resp.status_code}')
            raise FirmaECError(f'FirmaEC respondio con estado {resp.status_code}: {resp.text[:300]}')

        try:
            data = resp.json()
        except ValueError:
            # Algunos endpoints responden 200 sin cuerpo JSON; se considera exitoso.
            _log_llamada('FIRMA EC (POST /firmar)', ok=True)
            return

        if not isinstance(data, dict):
            _log_llamada('FIRMA EC (POST /firmar)', ok=True)
            return

        # Respuesta real confirmada del WS:
        # {"idLote": "...", "totalEnviados": 1, "totalFirmados": 1,
        #  "totalErrores": 0, "mensaje": "1 documento(s) firmado(s) exitosamente.",
        #  "estado": "OK"}
        mensaje = data.get('mensaje', '')
        es_error_callback_transitorio = 'callback' in mensaje.lower() and 'firmadigital-servicio' in mensaje.lower()

        if data.get('error'):
            _log_llamada('FIRMA EC (POST /firmar)', ok=False, detalle='WS devolvio error')
            raise FirmaECError(f'FirmaEC devolvio un error: {data.get("error")}')

        estado = data.get('estado')
        total_errores = data.get('totalErrores')

        if (estado and estado != 'OK') or total_errores:
            ultimo_error = mensaje or f'estado "{estado}"'
            if es_error_callback_transitorio and intento < intentos - 1:
                _log_llamada('FIRMA EC (POST /firmar)', ok=False, detalle='reintentando (callback transitorio)')
                time.sleep(3 * (intento + 1))
                continue
            _log_llamada('FIRMA EC (POST /firmar)', ok=False, detalle=f'estado {estado}')
            raise FirmaECError(f'FirmaEC devolvio estado "{estado}": {mensaje}')

        _log_llamada('FIRMA EC (POST /firmar)', ok=True)
        return  # estado OK, sin errores

    raise FirmaECError(f'FirmaEC no pudo procesar la firma tras {intentos} intentos: {ultimo_error}')


def firmar_documento_acumulativo(pdf_bytes, cedula, id_lote, password_p12, pkcs12_b64,
                                  nombre_documento='documento.pdf', llx=0, lly=0, pagina='1'):
    """Encapsula el flujo completo de una firma dentro de una cadena FirmaEC:
    cifra las credenciales, envia el PDF de entrada (que ya debe incluir las
    firmas previas del mismo id_lote) al WS, y descarga el PDF acumulado
    resultante consultando /enlaces/<idLote> (con reintentos, ya que el WS no
    devuelve el documento firmado en la respuesta del POST).

    - pdf_bytes: PDF de entrada (limpio si es la primera firma del lote, o el
      ultimo PDF firmado en cadena si ya hubo firmas previas).
    - cedula: cedula del firmante.
    - id_lote: identificador unico del lote de firma (el MISMO para todo el
      proceso de firma en cadena de un mismo documento).
    - password_p12 / pkcs12_b64: password y contenido Base64 del archivo .p12
      del firmante.

    Retorna los bytes del PDF con la nueva firma acumulada.
    Lanza FirmaECError si el WS falla, o si no se pudo confirmar/descargar el
    resultado; en ningun caso deja datos a medio guardar.
    """
    pkcs12_cifrado, password_cifrado, clave_aes_cifrada = cifrar_credenciales_firmaec(pkcs12_b64, password_p12)

    firmar_documento_pdf(
        pdf_bytes, cedula, id_lote,
        pkcs12_cifrado=pkcs12_cifrado,
        password_cifrado=password_cifrado,
        clave_aes_cifrada=clave_aes_cifrada,
        nombre_documento=nombre_documento,
        llx=llx, lly=lly, pagina=pagina,
    )

    pdf_firmado = None
    ultimo_error = None
    for _intento in range(5):
        try:
            pdf_firmado = obtener_documento_firmado_por_lote(id_lote)
        except FirmaECError as e:
            ultimo_error = str(e)
            break
        if pdf_firmado:
            break
        time.sleep(1)

    if not pdf_firmado:
        detalle_error = f' ({ultimo_error})' if ultimo_error else ''
        raise FirmaECError(
            f'La firma se envio correctamente pero no se pudo descargar el documento firmado desde FirmaEC{detalle_error}.'
        )

    return pdf_firmado
