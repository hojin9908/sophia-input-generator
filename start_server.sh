#!/usr/bin/env bash
# SOPHIA Input Generator - Linux server launcher (no sudo required).
#
# SESSIONS is an in-process dict  ->  exactly ONE worker process.
# Concurrency comes from threads:  --workers 1 --threads 8
#
# Auto-start on reboot (crontab -e):
#   @reboot /path/to/sophia-input-generator/start_server.sh
set -euo pipefail
cd "$(dirname "$(readlink -f "$0")")"

PORT="${PORT:-5000}"
mkdir -p logs

if [ ! -d .venv ]; then
  python3 -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate
pip install -q -r requirements.txt

# stop a previous instance if its pid file exists
if [ -f logs/gunicorn.pid ] && kill -0 "$(cat logs/gunicorn.pid)" 2>/dev/null; then
  kill "$(cat logs/gunicorn.pid)"
  sleep 1
fi

exec gunicorn app:app \
  --workers 1 --threads 8 \
  --bind "0.0.0.0:${PORT}" \
  --timeout 300 \
  --pid logs/gunicorn.pid \
  --access-logfile logs/access.log \
  --error-logfile logs/error.log \
  --daemon
