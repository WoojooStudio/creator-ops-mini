#!/usr/bin/env bash
# Fresh local setup: database + n8n with all workflows imported.
# Credentials are imported as empty placeholders — paste real keys in the n8n UI
# (Credentials tab). They are stored encrypted in .n8n-home, never in this repo.
set -euo pipefail
cd "$(dirname "$0")/.."
source scripts/env.sh

if [ ! -d .pgdata ]; then
  initdb -D .pgdata -U creatorops --auth=trust -E UTF8 >/dev/null
fi
pg_ctl -D .pgdata status >/dev/null 2>&1 || pg_ctl -D .pgdata -o "-p $PGPORT -k /tmp" -l .pgdata/server.log start
until pg_isready -q; do sleep 0.5; done
psql -q -d postgres -tc "SELECT 1 FROM pg_database WHERE datname='$PGDATABASE'" | grep -q 1 || createdb "$PGDATABASE"
psql -q -v ON_ERROR_STOP=1 -f db/schema.sql 2>&1 | grep -v NOTICE || true
psql -q -v ON_ERROR_STOP=1 -f db/seed.sql

python3 scripts/build_workflows.py
for f in credentials/*.json; do npx n8n import:credentials --input="$f" >/dev/null; done
npx n8n import:workflow --separate --input=workflows/ >/dev/null
# n8n 2.x only runs an error workflow that is published.
npx n8n publish:workflow --id=creatorOps000000 >/dev/null
echo "Ready. Start the editor with: npm start  →  http://localhost:$N8N_PORT"
