#!/usr/bin/env bash
# Run the whole program once, in pipeline order, and print what each workflow did.
# Usage: bash scripts/run_all.sh [workflow-id ...]
set -euo pipefail
cd "$(dirname "$0")/.."
source scripts/env.sh

ids=("$@")
[ ${#ids[@]} -eq 0 ] && ids=(creatorOps000001 creatorOps000003 creatorOps000004 creatorOps000005 creatorOps000006)

for id in "${ids[@]}"; do
  npx n8n execute --id="$id" --rawOutput 2>/dev/null | python3 scripts/summarize_run.py "$id"
done
