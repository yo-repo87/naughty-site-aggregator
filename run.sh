#!/bin/bash
# Start the aggregator at http://localhost:8000 (set HOST=0.0.0.0 to reach it from other devices on your LAN)
cd "$(dirname "$0")"
if [ ! -x .venv/bin/python ]; then
  python3 -m venv --without-pip .venv
  python3 -m pip --python .venv/bin/python install -q -r requirements.txt
fi
exec .venv/bin/python -m uvicorn aggregator.app:app --host "${HOST:-127.0.0.1}" --port "${PORT:-8000}"
