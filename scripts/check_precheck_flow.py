#!/usr/bin/env python3
"""Regression checks for task-based TRANS Origin precheck flow."""
import json
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import app.db as dbmod

TMP_DIR = Path(tempfile.mkdtemp(prefix="apimiao-precheck-check-"))
dbmod.DB_PATH = TMP_DIR / "apimiao.db"

from app import create_app
from app.api import trans_origin as to
from app.data_sheets.service import create_sheet
from config import ADMIN_ID

RESULT = []
MODE = {"name": "ok", "calls": 0}
SLEEPS = []
ORIGINAL_SLEEP = to.time.sleep


def record(name, ok, detail=""):
    RESULT.append({"case": name, "ok": bool(ok), "detail": detail})


def fake_request(cfg, values=None):
    MODE["calls"] += 1
    mode = MODE["name"]
    if mode == "fail" or (mode == "fail_once" and MODE["calls"] == 1):
        return 500, {}, {}, '{"code":500,"message":"upstream failed"}', 5, ""
    return 200, {}, {}, json.dumps({
        "code": 0,
        "city": (values or {}).get("city", "北京"),
        "price": "8.37",
    }), 5, ""


def recording_sleep(seconds):
    SLEEPS.append(seconds)
    return ORIGINAL_SLEEP(seconds)


def login(client):
    with client.session_transaction() as session:
        session.clear()
        session["uid"] = ADMIN_ID
        session["role"] = "ADMIN"


def task_payload(sheet_id, **overrides):
    payload = {
        "note": "check",
        "original_url": "https://upstream.invalid/precheck",
        "original_method": "GET",
        "params": [], "headers": [], "cookies": [],
        "body_mode": "none", "body": "",
        "timeout": 10, "retry": 0, "batch_retry_rounds": 1,
        "request_interval": 0, "frequency": "DAY", "days": [], "times": [],
        "sheet_id": sheet_id,
    }
    payload.update(overrides)
    return payload


def wait_status(client, task_id, statuses, timeout=5):
    deadline = time.time() + timeout
    last = {}
    while time.time() < deadline:
        response = client.get("/api/trans-origins/precheck-tasks/" + task_id)
        last = response.get_json().get("task", {})
        if last.get("status") in statuses:
            return last
        time.sleep(0.02)
    return last


def decide(client, task_id, decision):
    return client.post(
        "/api/trans-origins/precheck-tasks/" + task_id + "/decision",
        json={"decision": decision},
    )


def db_one(sql, args=()):
    conn = sqlite3.connect(dbmod.DB_PATH)
    conn.row_factory = sqlite3.Row
    row = conn.execute(sql, args).fetchone()
    conn.close()
    return dict(row) if row else None


def count(table):
    return db_one("SELECT COUNT(*) AS n FROM " + table)["n"]


def run():
    app = create_app()
    to.do_request = fake_request
    to.time.sleep = recording_sleep
    try:
        with app.test_client() as client:
            login(client)
            with app.app_context():
                sheet = create_sheet(ADMIN_ID, {
                    "name": "precheck-flow-check",
                    "fields": [
                        {"key": "code", "label": "code", "json_type": "number", "enabled": True, "order": 0},
                        {"key": "city", "label": "city", "json_type": "string", "enabled": True, "order": 1},
                        {"key": "price", "label": "price", "json_type": "string", "enabled": True, "order": 2},
                    ],
                })
            sheet_id = sheet["id"]

            # 1. Success with request interval between two combos.
            MODE.update(name="ok", calls=0)
            SLEEPS.clear()
            started = client.post("/api/trans-origins/precheck-tasks", json=task_payload(
                sheet_id,
                params=[{"key": "city", "is_list": True, "items": ["北京", "上海"]}],
                request_interval=0.2,
            ))
            task_id = started.get_json()["task_id"]
            task = wait_status(client, task_id, {"SUCCEEDED"}, timeout=8)
            record("task_success_writes_once",
                   task.get("status") == "SUCCEEDED" and count("trans_origins") == 1
                   and count("fetch_batches") == 1 and count("data_rows") == 2,
                   f"status={task.get('status')} errors={task.get('errors')} origins={count('trans_origins')} batches={count('fetch_batches')} rows={count('data_rows')}")
            record("request_interval_applied", any(value >= 0.2 for value in SLEEPS), f"sleeps={SLEEPS}")
            record("progress_reported", task.get("total") == 2 and task.get("done") == 2,
                   f"total={task.get('total')} done={task.get('done')}")

            # 2. Failure then cancel. No config, no data.
            MODE.update(name="fail", calls=0)
            started = client.post("/api/trans-origins/precheck-tasks", json=task_payload(
                sheet_id, original_url="https://upstream.invalid/cancel"
            ))
            task_id = started.get_json()["task_id"]
            task = wait_status(client, task_id, {"AWAITING_USER"})
            before_origins, before_rows = count("trans_origins"), count("data_rows")
            decision = decide(client, task_id, "cancel")
            record("failure_then_cancel_writes_nothing",
                   task.get("status") == "AWAITING_USER" and decision.status_code == 200
                   and count("trans_origins") == before_origins
                   and count("data_rows") == before_rows,
                   f"status={task.get('status')} decision={decision.status_code}")

            # 3. Failure then save anyway. Config saved as draft, no data.
            MODE.update(name="fail", calls=0)
            started = client.post("/api/trans-origins/precheck-tasks", json=task_payload(
                sheet_id, original_url="https://upstream.invalid/save-anyway"
            ))
            task_id = started.get_json()["task_id"]
            wait_status(client, task_id, {"AWAITING_USER"})
            before_rows = count("data_rows")
            decision = decide(client, task_id, "save_anyway")
            saved = db_one("SELECT state FROM trans_origins WHERE original_url=?", ("https://upstream.invalid/save-anyway",))
            record("save_anyway_saves_config_without_data",
                   decision.status_code == 200 and saved and saved["state"] == "DRAFT"
                   and count("data_rows") == before_rows,
                   f"state={saved['state'] if saved else None} rows={count('data_rows')}")

            # 4. Failure then user retry succeeds. Write only once.
            MODE.update(name="fail_once", calls=0)
            started = client.post("/api/trans-origins/precheck-tasks", json=task_payload(
                sheet_id, original_url="https://upstream.invalid/retry-ok", batch_retry_rounds=1
            ))
            task_id = started.get_json()["task_id"]
            task = wait_status(client, task_id, {"AWAITING_USER"})
            before_origins, before_batches, before_rows = count("trans_origins"), count("fetch_batches"), count("data_rows")
            retry = decide(client, task_id, "retry")
            task = wait_status(client, task_id, {"SUCCEEDED"}, timeout=8)
            record("retry_succeeds_and_writes_once",
                   task.get("status") == "SUCCEEDED" and retry.status_code == 200
                   and count("trans_origins") == before_origins + 1
                   and count("fetch_batches") == before_batches + 1
                   and count("data_rows") == before_rows + 1,
                   f"status={task.get('status')} round={task.get('round')}")

            # 5. Retry limit enforced.
            MODE.update(name="fail", calls=0)
            started = client.post("/api/trans-origins/precheck-tasks", json=task_payload(
                sheet_id, original_url="https://upstream.invalid/retry-limit", batch_retry_rounds=0
            ))
            task_id = started.get_json()["task_id"]
            wait_status(client, task_id, {"AWAITING_USER"})
            retry = decide(client, task_id, "retry")
            record("retry_limit_enforced", retry.status_code == 409, f"status={retry.status_code}")
    finally:
        to.do_request = fake_request
        to.time.sleep = ORIGINAL_SLEEP
    print(json.dumps(RESULT, ensure_ascii=False, indent=2))
    return 0 if all(item["ok"] for item in RESULT) else 1


if __name__ == "__main__":
    sys.exit(run())
