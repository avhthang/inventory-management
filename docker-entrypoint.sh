#!/bin/sh
set -e

wait_for_database() {
  if [ -z "$DATABASE_URL" ]; then
    return 0
  fi

  python -c "
import os
import socket
import sys
import time
from urllib.parse import urlparse

url = urlparse(os.environ.get('DATABASE_URL', ''))
if url.scheme not in ('postgresql', 'postgres'):
    sys.exit(0)

host = url.hostname or 'inventory-postgres'
port = url.port or 5432
deadline = time.time() + int('${DB_WAIT_TIMEOUT:-60}')

while time.time() < deadline:
    try:
        with socket.create_connection((host, port), timeout=3):
            sys.exit(0)
    except OSError:
        time.sleep(2)

print(f'Database is not reachable at {host}:{port}', file=sys.stderr)
sys.exit(1)
"
}

wait_for_database

if [ "${RUN_DB_INIT:-true}" = "true" ]; then
  python init_database.py
fi

exec "$@"
