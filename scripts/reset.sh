#!/usr/bin/env bash
# Restore day 0: delete the pipeline database and serving/*.json, then rebuild everything as of
# 2026-03-22 through scripts/advance.py --day0 (which takes data/.advance.lock).
# Run inside WSL from anywhere:   bash scripts/reset.sh            (refuses while an advance runs)
#                                 bash scripts/reset.sh --force    (also removes a stale lock)
# From Windows:  wsl -e bash -lc "cd /mnt/c/<path-to>/patch-tuesday && bash scripts/reset.sh"
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
export PATH="$HOME/.local/bin:$PATH"

LOCK="data/.advance.lock"
if [[ -e "$LOCK" ]]; then
  # advance.py writes its pid into the lock; a lock whose process is gone (or older than 2 h) is stale.
  STATUS="$(python3 scripts/advance.py --lock-status || true)"
  if [[ "$STATUS" == *'"lock": "stale"'* ]]; then
    echo "[reset] removing a stale lock: $STATUS" >&2
    rm -f "$LOCK"
  elif [[ "${1:-}" == "--force" ]]; then
    echo "[reset] --force: removing $LOCK ($STATUS)" >&2
    rm -f "$LOCK"
  else
    echo "[reset] $LOCK is held: an advance is running ($STATUS). Wait for it, or use --force." >&2
    exit 1
  fi
fi

DB="$(sed -n 's/^[[:space:]]*path:[[:space:]]*"\{0,1\}\([^"]*\)"\{0,1\}[[:space:]]*$/\1/p' .bruin.yml | head -1)"
if [[ -z "$DB" ]]; then
  echo "[reset] no duckdb path found in .bruin.yml (copy .bruin.yml.example first)" >&2
  exit 1
fi

echo "[reset] deleting $DB and serving/*.json" >&2
rm -f "$DB" "$DB.wal"
mkdir -p serving
rm -f serving/world.json serving/attack_cards.json serving/patch_priority.json serving/reveal.json

python3 scripts/advance.py --day0
