#!/bin/sh
set -eu
ROOT=/home/ec2-user/bayworks
. "$ROOT/.venv/bin/activate"
cd "$ROOT"
uvicorn mock_dms.main:app --host 127.0.0.1 --port 8001 &
sleep 1
python -m app.worker &
exec uvicorn app.main:app --host 127.0.0.1 --port 8000
