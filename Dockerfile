FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DEBIAN_FRONTEND=noninteractive

WORKDIR /app

# Dependencias de sistema para psycopg2 (compilacion) y Playwright (fuentes,
# render de PDF/paginas, librerias graficas que Chromium necesita en runtime).
RUN apt-get update && apt-get install -y --no-install-recursive \
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
RUN pip install --no-cache-dir -r requirements.txt

# Instala los navegadores de Playwright (Chromium) y sus dependencias de
# sistema adicionales, usados por znuny/ para automatizar OTRS.
RUN playwright install --with-deps chromium

COPY . .

RUN useradd --create-home --shell /bin/bash appuser \
    && mkdir -p /app/media /app/staticfiles \
    && chmod +x /app/scripts/entrypoint.sh \
    && chown -R appuser:appuser /app

USER appuser

EXPOSE 8000

ENTRYPOINT ["/app/scripts/entrypoint.sh"]
