#!/usr/bin/env python3
"""Check platform behavior when an upstream response drifts from fixed fields."""
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import app.db as dbmod

TMP_DIR = Path(tempfile.mkdtemp(prefix="apimiao-upstream-drift-"))
dbmod.DB_PATH = TMP_DIR / "apimiao.db"

from app import create_app
from app.api import trans_origin as to
from app.data_sheets.service import create_sheet, delete_sheet
from app.db import all_rows
from config import ADMIN_ID

FIELDS = [
    {"key": "code", "label": "code", "json_type": "number", "enabled": True, "order": 0},
    {"key": "result.prov", "label": "prov", "json_type": "string", "enabled": True, "order": 1},
    {"key": "result.items[0].p92", "label": "p92-0", "json_type": "string", "enabled": True, "order": 2},
    {"key": "result.items[1].p92", "label": "p92-1", "json_type": "string", "enabled": True, "order": 3},
]

STABLE = {"code": 0, "result": {"prov": "北京", "items": [{"p92": "8.37"}, {"p92": "8.51"}]}}
ADDED = {"code": 0, "result": {"prov": "北京", "items": [{"p92": "8.37"}, {"p92": "8.51"}], "extra": 1}, "msg": "ok"}
MISSING = {"code": 0, "result": {"prov": "北京", "items": [{"p92": "8.37"}]}}
TYPE_CHANGED = {"code": 0, "result": {"prov": 123, "items": [{"p92": "8.37"}, {"p92": "8.51"}]}}
ARRAY_SHORT = {"code": 0, "result": {"prov": "北京", "items": [{"p92": "8.37"}]}}
INVALID = "{not json"

CASES = [
    ("stable", STABLE, True, 1, [], []),
    ("added_field", ADDED, True, 1, [], []),
    ("missing_field", MISSING, True, 1, ["result.items[1].p92"], ["result.items[1].p92"]),
    ("type_changed", TYPE_CHANGED, True, 1, ["result.prov"], ["result.prov"]),
    ("array_shortened", ARRAY_SHORT, True, 1, ["result.items[1].p92"], ["result.items[1].p92"]),
    ("invalid_json", INVALID, False, 0, [], []),
]


def body_of(value):
    return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)


def run_case(name, body, expect_success, expect_payloads, expect_paths, expect_skipped):
    origin = {
        "id": "drift-origin",
        "user_id": ADMIN_ID,
        "sheet_id": CURRENT_SHEET_ID,
        "original_url": "http://upstream.invalid",
    }
    cfg = {
        "original_url": origin["original_url"],
        "sheet_id": CURRENT_SHEET_ID,
        "params_json": "[]",
        "headers_json": "[]",
        "cookies_json": "[]",
        "body_mode": "none",
        "body": "",
        "request_interval": 0,
    }
    to.do_request = lambda _cfg, _combo: (200, {}, {}, body_of(body), 10, "")
    collected = to.collect_batch(origin, cfg)
    text = json.dumps(collected, ensure_ascii=False)
    failures = []
    if collected["success"] != expect_success:
        failures.append(f"success={collected['success']}, expected {expect_success}")
    if len(collected["payloads"]) != expect_payloads:
        failures.append(f"payloads={len(collected['payloads'])}, expected {expect_payloads}")
    for path in expect_paths:
        if path not in text:
            failures.append(f"warning does not mention {path}")
    for path in expect_skipped:
        if any(path in item["data"] for item in collected["payloads"]):
            failures.append(f"drifted field was written: {path}")
    return {"case": name, "ok": not failures, "failures": failures,
            "success": collected["success"], "payloads": len(collected["payloads"]),
            "warnings": collected["warnings"], "errors": collected["errors"]}


CURRENT_SHEET_ID = ""
RESULT = []


def main():
    global CURRENT_SHEET_ID
    app = create_app()
    original_request = to.do_request
    with app.app_context():
        sheet = create_sheet(ADMIN_ID, {"name": "upstream-drift-check", "fields": FIELDS})
        CURRENT_SHEET_ID = sheet["id"]
        try:
            for case in CASES:
                RESULT.append(run_case(*case))
        finally:
            to.do_request = original_request
            delete_sheet(ADMIN_ID, CURRENT_SHEET_ID)
        endpoints = [row["endpoint"] for row in all_rows(
            "SELECT endpoint FROM request_logs WHERE event_type=?", ("UPSTREAM_TRANS",)
        )]
    RESULT.append({
        "case": "upstream_log_endpoint_recorded",
        "ok": bool(endpoints) and all("upstream.invalid" in endpoint for endpoint in endpoints),
        "count": len(endpoints),
        "sample": endpoints[0] if endpoints else "",
    })
    print(json.dumps(RESULT, ensure_ascii=False, indent=2))
    return 0 if all(item["ok"] for item in RESULT) else 1


if __name__ == "__main__":
    sys.exit(main())
