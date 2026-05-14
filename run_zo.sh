#!/bin/sh
set -e

cd "$(dirname "$0")"

if [ ! -d "venv" ]; then
    python3 -m venv venv
fi
. venv/bin/activate

pip install -q --no-cache-dir -r requirements.txt

PORT=${PORT:-10842}

exec python main.py
