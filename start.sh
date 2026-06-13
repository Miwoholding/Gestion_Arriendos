#!/bin/bash
# Inicia la app con Gunicorn (servidor estable para producción)
set -e
cd "$(dirname "$0")"

# Cargar variables de entorno
set -a
[ -f .env ] && source .env
set +a

exec python3 -m gunicorn -c gunicorn.conf.py app:app
