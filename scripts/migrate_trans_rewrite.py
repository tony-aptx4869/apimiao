#!/usr/bin/env python3
"""Apply the TRANS rewrite migration without touching runtime code paths."""
import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.db import DB_PATH, migrate_database  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description="Migrate the database to the TRANS rewrite schema.")
    parser.add_argument("--db", default=str(DB_PATH), help="Database path. Defaults to config.DB_PATH.")
    parser.add_argument("--dry-run", action="store_true", help="Apply the migration to a temporary copy only.")
    args = parser.parse_args()
    report = migrate_database(args.db, dry_run=args.dry_run)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
