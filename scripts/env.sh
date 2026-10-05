# Source this file: `source scripts/env.sh`
# Everything lives inside the repo folder — nothing is written to ~/.n8n.
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")/.." && pwd)"
export PATH="/opt/homebrew/opt/node@24/bin:/opt/homebrew/opt/postgresql@17/bin:$PATH"
export LC_ALL=en_US.UTF-8 LANG=en_US.UTF-8

export N8N_USER_FOLDER="$ROOT/.n8n-home"
export N8N_PORT=5678
export N8N_DIAGNOSTICS_ENABLED=false
export N8N_PERSONALIZATION_ENABLED=false
export N8N_BLOCK_ENV_ACCESS_IN_NODE=false   # workflows read YOUTUBE_API_KEY / SLACK_WEBHOOK_URL
export GENERIC_TIMEZONE=Europe/Berlin

export PGHOST=localhost PGPORT=5433 PGUSER=creatorops PGDATABASE=creator_ops

# Secrets come from .env (git-ignored). Without them the demo runs on fixtures.
if [ -f "$ROOT/.env" ]; then set -a; . "$ROOT/.env"; set +a; fi
