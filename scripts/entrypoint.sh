#!/bin/sh
set -e

echo "Esperando a que la base de datos este disponible..."
python - <<'PYEOF'
import os
import sys
import time

import psycopg2

host = os.environ.get("POSTGRES_HOST", "db")
port = os.environ.get("POSTGRES_PORT", "5432")
db   = os.environ.get("POSTGRES_DB")
user = os.environ.get("POSTGRES_USER")
pw   = os.environ.get("POSTGRES_PASSWORD")

for intento in range(30):
    try:
        conn = psycopg2.connect(host=host, port=port, dbname=db, user=user, password=pw, connect_timeout=3)
        conn.close()
        print("Base de datos disponible.")
        sys.exit(0)
    except psycopg2.OperationalError:
        print(f"DB no disponible aun (intento {intento + 1}/30)...")
        time.sleep(2)

print("No se pudo conectar a la base de datos tras 30 intentos.")
sys.exit(1)
PYEOF

echo "Aplicando migraciones..."
python manage.py migrate --noinput

echo "Recolectando archivos estaticos..."
python manage.py collectstatic --noinput

echo "Iniciando Gunicorn..."
exec gunicorn config.wsgi:application \
    --bind 0.0.0.0:8000 \
    --workers "${GUNICORN_WORKERS:-3}" \
    --timeout "${GUNICORN_TIMEOUT:-120}" \
    --access-logfile - \
    --error-logfile -
