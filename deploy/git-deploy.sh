#!/usr/bin/env bash
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REF="${1:-origin/main}"
SERVICE_USER="${APIMIAO_USER:-www-data}"
TS="$(date -u +%Y%m%dT%H%M%SZ)"

cd "$APP_DIR"

if [ ! -d .git ]; then
  echo "[APIMiao] Not a git worktree: $APP_DIR" >&2
  exit 1
fi

echo "[APIMiao] Fetching origin"
git fetch --all --prune

TARGET="$(git rev-parse "$REF^{commit}")"
echo "[APIMiao] Target $TARGET"

mkdir -p data/backups /opt/backups
if [ -f config.ini ]; then
  cp config.ini "/opt/backups/config.ini.$TS"
  echo "[APIMiao] Config backup /opt/backups/config.ini.$TS"
fi
if [ -f data/apimiao.db ]; then
  .venv/bin/python - "$TS" <<'PY'
import sqlite3
import sys
from pathlib import Path

ts = sys.argv[1]
source = Path("/opt/apimiao/data/apimiao.db")
target = source.parent / "backups" / f"apimiao-predeploy-{ts}.db"
src = sqlite3.connect(str(source))
dst = sqlite3.connect(str(target))
src.backup(dst)
src.close()
dst.close()
print(f"[APIMiao] Database backup {target}")
PY
fi

PREVIOUS="$(cat .last_deploy 2>/dev/null || true)"
git reset --hard "$TARGET"

if [ -z "$PREVIOUS" ] || ! git diff --quiet "$PREVIOUS" "$TARGET" -- requirements.txt; then
  echo "[APIMiao] Installing Python dependencies"
  .venv/bin/pip install -r requirements.txt
else
  echo "[APIMiao] Dependencies unchanged"
fi

if [ -f data/apimiao.db ]; then
  echo "[APIMiao] Running database migration"
  .venv/bin/python scripts/migrate_trans_rewrite.py --db "$APP_DIR/data/apimiao.db"
fi

chown -R "$SERVICE_USER":"$SERVICE_USER" "$APP_DIR/data"

echo "[APIMiao] Restarting services"
systemctl restart apimiao apimiao-scheduler

echo "[APIMiao] Health check"
systemctl is-active apimiao apimiao-scheduler caddy
curl -fsS -o /dev/null http://127.0.0.1:8080/
echo "$TARGET" > .last_deploy
echo "[APIMiao] Deployed $TARGET"
