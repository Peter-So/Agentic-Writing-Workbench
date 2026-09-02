#!/usr/bin/env bash
set -euo pipefail

ROOT=/home/AppsPros/svr/agentic-writing-workbench
echo $$ > "$ROOT/shared/app.pid"
exec "$ROOT/shared/venv/bin/python" -m uvicorn app.writing_web:app \
  --app-dir "$ROOT/current" \
  --host 127.0.0.1 \
  --port 17861 \
  --workers 1
