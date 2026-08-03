FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DEBIAN_FRONTEND=noninteractive

WORKDIR /app

# Dependencias de sistema para psycopg2 (compilacion) y Playwright (fuentes,
# render de PDF/paginas, librerias graficas que Chromium necesita en runtime).
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    curl \
    wget \
    gnupg \
    ca-certificates \
    fonts-liberation \
    libnss3 \
    libatk-bridge2.0-0 \
    libatk1.0-0 \
    libcups2 \
    libdrm2 \
    libxkbcommon0 \
    libxcomposite1 \
    libxdamage1 \
    libxfixes3 \
    libxrandr2 \
    libgbm1 \
    libasound2 \
    libpango-1.0-0 \
    libcairo2 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
# NOTA: --trusted-host evita la verificacion SSL de pip porque la red de
# desarrollo intercepta TLS con un certificado propio (proxy corporativo),
# lo que rompe la verificacion normal contra pypi.org. Es un ajuste temporal
# solo para build local; en un entorno con CA corporativa instalada
# correctamente esto deberia quitarse.
RUN pip install --no-cache-dir \
    --trusted-host pypi.org \
    --trusted-host files.pythonhosted.org \
    --trusted-host pypi.python.org \
    -r requirements.txt

# Instala el navegador Chromium de Playwright, usado por znuny/ para
# automatizar OTRS. No se usa --with-deps: los paquetes que necesita
# (libnss3, libgbm1, fonts-liberation, etc.) ya se instalaron arriba a mano,
# porque --with-deps referencia nombres de paquete de Ubuntu (ttf-unifont,
# ttf-ubuntu-font-family) que no existen en Debian trixie.
# NODE_TLS_REJECT_UNAUTHORIZED=0 es necesario solo aqui porque el instalador
# de Playwright usa Node (no pip) para descargar los binarios, y la red de
# desarrollo intercepta TLS con un certificado autofirmado (mismo problema
# que --trusted-host resuelve para pip mas arriba).
RUN NODE_TLS_REJECT_UNAUTHORIZED=0 playwright install chromium

COPY . .

RUN useradd --create-home --shell /bin/bash appuser \
    && mkdir -p /app/media /app/staticfiles \
    && chmod +x /app/scripts/entrypoint.sh \
    && chown -R appuser:appuser /app

USER appuser

EXPOSE 8000

ENTRYPOINT ["/app/scripts/entrypoint.sh"]
