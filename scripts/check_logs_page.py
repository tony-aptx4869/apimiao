#!/usr/bin/env python3
"""Regression checks for logs page labels, pagination, and redacted TRANS logs."""
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import app.db as dbmod

TMP_DIR = Path(tempfile.mkdtemp(prefix="apimiao-logs-check-"))
dbmod.DB_PATH = TMP_DIR / "apimiao.db"

from app import create_app
from app.api import trans_origin as to
from app.data_sheets.service import create_sheet
from app.db import execute, utcnow
from config import ADMIN_ID

RESULT = []


def record(name, ok, detail=""):
    RESULT.append({"case": name, "ok": bool(ok), "detail": detail})


def login(client):
    with client.session_transaction() as session:
        session.clear()
        session["uid"] = ADMIN_ID
        session["role"] = "ADMIN"


def run():
    app = create_app()
    original_request = to.do_request
    try:
        with app.app_context():
            execute(
                "INSERT INTO request_logs "
                "(user_id, api_id, method, endpoint, status, success, created_at, duration_ms, detail, event_type) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (ADMIN_ID, "", "GET", "gas/price", 200, 1, utcnow(), 4, "", "GATEWAY"),
            )
            sheet = create_sheet(ADMIN_ID, {
                "name": "logs-check",
                "fields": [
                    {"key": "code", "label": "code", "json_type": "number", "enabled": True, "order": 0},
                    {"key": "prov", "label": "prov", "json_type": "string", "enabled": True, "order": 1},
                ],
            })

        with app.test_client() as client:
            login(client)
            base = client.get("/api/logs/query").get_json()
            record("stat_scope_default", base.get("stat_scope") == "近365天",
                   f"scope={base.get('stat_scope')}")
            filtered = client.get("/api/logs/query?method=GET&status=SUCCESS").get_json()
            record("stat_scope_follows_filter",
                   filtered.get("stat_scope") == "当前筛选" and "GET" in filtered.get("stat_scope_detail", "")
                   and "成功" in filtered.get("stat_scope_detail", ""),
                   f"scope={filtered.get('stat_scope')} detail={filtered.get('stat_scope_detail')}")

            page = client.get("/panel/logs").get_data(as_text=True)
            labels_ok = all(marker in page for marker in (
                "statTotalLabel", "statSuccessLabel", "statFailedLabel", "stat-scope-detail"
            ))
            record("stat_labels_and_scope_subtitle", labels_ok, f"length={len(page)}")

            dynamic_pager_ok = all(marker in page for marker in (
                "pager-ellipsis", "cur <= 3", "cur >= total - 2", "cur - 1, cur, cur + 1"
            ))
            record("dynamic_pager_strategy", dynamic_pager_ok, "ellipsis+first+last+middle")

            cfg = {
                "original_url": "https://apis.tianapi.com/oilprice/index",
                "original_method": "GET",
                "original_key": "SUPER_SECRET_KEY",
                "params_json": json.dumps([
                    {"key": "key", "value": "{{o-api-key}}"},
                    {"key": "prov", "value": "北京"},
                ]),
                "headers_json": "[]", "cookies_json": "[]",
                "body_mode": "none", "body": "",
            }
            method, endpoint = to.redact_request_url(cfg, {})
            record("origin_url_redacted",
                   method == "GET" and "prov=北京" in endpoint
                   and "SUPER_SECRET_KEY" not in endpoint and "key=" not in endpoint,
                   f"endpoint={endpoint}")

            to.do_request = lambda cfg, values=None: (
                200, {}, {}, json.dumps({"code": 0, "prov": "北京"}), 6, ""
            )
            origin = {"id": "logs-origin-check", "user_id": ADMIN_ID, "sheet_id": sheet["id"]}
            to.collect_batch(origin, cfg)
            with app.app_context():
                row = execute(
                    "SELECT endpoint, event_type FROM request_logs WHERE event_type=? ORDER BY id DESC LIMIT 1",
                    ("UPSTREAM_TRANS",),
                ).fetchone()
            record("origin_request_logged",
                   row is not None and "prov=北京" in row["endpoint"]
                   and "SUPER_SECRET_KEY" not in row["endpoint"],
                   f"row={dict(row) if row else None}")
    finally:
        to.do_request = original_request

    print(json.dumps(RESULT, ensure_ascii=False, indent=2))
    return 0 if all(item["ok"] for item in RESULT) else 1


if __name__ == "__main__":
    sys.exit(run())
