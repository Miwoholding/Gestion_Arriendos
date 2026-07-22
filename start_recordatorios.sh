#!/bin/bash
# Ejecuta el envío diario de recordatorios de pago (llamado por launchd).
set -e
cd "$(dirname "$0")"

set -a
[ -f .env ] && source .env
set +a

exec python3 recordatorios.py
