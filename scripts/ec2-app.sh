#!/bin/sh
set -eu
uvicorn mock_dms.main:app --host 127.0.0.1 --port 8001 &
sleep 1
python -m app.worker &
exec uvicorn app.main:app --host 0.0.0.0 --port 8000
