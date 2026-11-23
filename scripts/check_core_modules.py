#!/usr/bin/env python3
"""Regression checks for core APIMiao module state changes."""
import json
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import app.db as dbmod

TMP_DIR = Path(tempfile.mkdtemp(prefix="apimiao-core-check-"))
dbmod.DB_PATH = TMP_DIR / "apimiao.db"

from app import create_app
from app.api import trans_origin as to
from app.api.trans_user import execute_query
from app.db import all_rows, execute, one, utcnow
from config import ADMIN_ID


RESULT = []


def record(name, ok, detail=""):
    RESULT.append({"case": name, "ok": bool(ok), "detail": detail})


def login(client):
    with client.session_transaction() as session:
        session.clear()
        session["uid"] = ADMIN_ID
        session["role"] = "ADMIN"


def count(table):
    return one(f"SELECT COUNT(*) AS n FROM {table}")["n"]


def make_sheet(client, name="Core Check Sheet", description="desc"):
    result = client.post("/api/data-sheets", json={
        "name": name,
        "description": description,
        "fields": [
            {"key": "city", "label": "City", "json_type": "string", "enabled": True, "order": 0},
            {"key": "price", "label": "Price", "json_type": "number", "enabled": True, "order": 1},
        ],
    }).get_json()
    return result["sheet"]


def proxy_payload(endpoint):
    return {
        "endpoint": endpoint,
        "method": "GET",
        "original_url": "https://upstream.invalid/data",
        "original_method": "GET",
        "note": "note",
        "params": [], "headers": [], "cookies": [],
        "body_mode": "none", "body": "",
        "timeout": 10, "retry": 2, "rate_limit": 0,
    }


def origin_payload(sheet_id, retry=2, note="note"):
    return {
        "note": note,
        "original_url": "https://upstream.invalid/oil",
        "original_method": "GET",
        "params": [], "headers": [], "cookies": [],
        "body_mode": "none", "body": "",
        "timeout": 10, "retry": retry, "request_interval": 0,
        "frequency": "DAY", "days": [], "times": [],
        "sheet_id": sheet_id,
    }


def user_payload(sheet, endpoint):
    field_map = {field["key"]: True for field in sheet["fields"]}
    return {
        "endpoint": endpoint,
        "method": "GET",
        "note": "note",
        "sheet_ids": [sheet["id"]],
        "sheet_fields": {sheet["id"]: field_map},
        "param_mapping": [{
            "sheet_id": sheet["id"],
            "field_id": sheet["fields"][0]["id"],
            "field": sheet["fields"][0]["key"],
            "alias": "q",
        }],
        "body_mapping": [],
        "joins": [],
        "timeout": 10, "retry": 0, "rate_limit": 0,
    }


def run():
    app = create_app()
    original_request = to.do_request
    to.do_request = lambda cfg, values=None: (
        200, {}, {}, json.dumps({"code": 0, "city": "北京", "price": 1}), 5, ""
    )
    with app.test_client() as client:
        login(client)

        # PROXY API
        first = client.post("/api/apis", json=proxy_payload("core-proxy"))
        second = client.post("/api/apis", json=proxy_payload("core-proxy"))
        record("proxy_duplicate_create_blocked", first.status_code == 200 and second.status_code == 400,
               f"statuses={first.status_code},{second.status_code}")
        api_id = first.get_json()["id"]
        execute("INSERT INTO api_cache (api_id, cache_key, status, headers_json, body, cached_at) VALUES (?, ?, ?, ?, ?, ?)",
                (api_id, "stale-cache", 200, "{}", b"stale", utcnow()))
        updated = client.put("/api/apis/" + api_id, json={**proxy_payload("core-proxy"), "note": "changed"})
        cache_left = count("api_cache")
        record("proxy_edit_clears_cache", cache_left == 0, f"cache_rows={cache_left}")
        rows = all_rows("SELECT id, note FROM apis WHERE endpoint='core-proxy'")
        record("proxy_edit_keeps_one_row", updated.status_code == 200 and len(rows) == 1 and rows[0]["note"] == "changed",
               f"rows={len(rows)} note={rows[0]['note'] if rows else ''}")

        # Data Sheets + TRANS User API
        sheet = make_sheet(client, description="original description")
        field_ids_before = [field["id"] for field in sheet["fields"]]
        sheet_edit = client.put("/api/data-sheets/" + sheet["id"], json={
            "name": sheet["name"],
            "description": "original description",
            "fields": [{**field, "label": field["label"] + "!"} for field in sheet["fields"]],
        })
        sheet_after = client.get("/api/data-sheets/" + sheet["id"]).get_json()["sheet"]
        field_ids_after = [field["id"] for field in sheet_after["fields"]]
        record("sheet_edit_keeps_field_ids", sheet_edit.status_code == 200 and field_ids_before == field_ids_after,
               f"before={field_ids_before} after={field_ids_after}")

        partial = client.put("/api/data-sheets/" + sheet["id"], json={"name": "renamed sheet"})
        sheet_partial = client.get("/api/data-sheets/" + sheet["id"]).get_json()["sheet"]
        record("sheet_partial_put_preserves_description",
               partial.status_code == 200 and sheet_partial["description"] == "original description",
               f"description={sheet_partial['description']!r}")

        user_api = client.post("/api/trans-apis", json=user_payload(sheet, "core-trans-user"))
        record("trans_user_create", user_api.status_code == 200, f"status={user_api.status_code}")
        user_id = user_api.get_json().get("id", "")
        user_rows, user_err = execute_query(dict(one("SELECT * FROM trans_apis WHERE id=?", (user_id,))), {"q": "x"})
        query_ok = user_err is None
        for field in sheet["fields"]:
            sheet_edit2 = client.put("/api/data-sheets/" + sheet["id"], json={
                "name": sheet["name"],
                "description": "original description",
                "fields": [{**f, "label": f["label"] + "?"} for f in sheet_after["fields"]],
            })
            sheet_after2 = client.get("/api/data-sheets/" + sheet["id"]).get_json()["sheet"]
            _, query_err = execute_query(dict(one("SELECT * FROM trans_apis WHERE id=?", (user_id,))), {"q": "x"})
            query_ok = query_ok and query_err is None
            break
        record("trans_user_query_survives_sheet_edit", query_ok, f"query_err={query_err!r} edit_status={sheet_edit2.status_code}")

        delete_sheet = client.delete("/api/data-sheets/" + sheet["id"])
        after_delete_query, after_delete_err = execute_query(dict(one("SELECT * FROM trans_apis WHERE id=?", (user_id,))), {"q": "x"}) if one("SELECT 1 FROM trans_apis WHERE id=?", (user_id,)) else ([], None)
        record("sheet_delete_referenced_blocked", delete_sheet.status_code >= 400,
               f"delete_status={delete_sheet.status_code} query_err={after_delete_err!r}")

        # TRANS Origin API
        origin_sheet = make_sheet(client, "Origin Sheet", "for origin")
        repeat_payload = {**origin_payload(origin_sheet["id"], retry=2), "original_url": "https://upstream.invalid/repeat"}
        client.post("/api/trans-origins/precheck", json=repeat_payload)
        repeat_second = client.post("/api/trans-origins/precheck", json={**repeat_payload, "note": "second click"})
        repeat_rows = all_rows("SELECT id FROM trans_origins WHERE original_url='https://upstream.invalid/repeat'")
        record("origin_create_repeat_no_duplicate",
               repeat_second.status_code >= 400 and len(repeat_rows) == 1,
               f"status={repeat_second.status_code} rows={len(repeat_rows)}")

        created = client.post("/api/trans-origins/precheck", json=origin_payload(origin_sheet["id"], retry=2))
        origin_id = created.get_json().get("id", "")
        sheet_list_page = client.get("/panel/data-sheets").get_data(as_text=True)
        bound_injected = 'new Set(["' + origin_sheet["id"] + '"])' in sheet_list_page
        record("sheet_list_receives_bound_sheet_ids", bound_injected,
               f"bound_sheet={origin_sheet['id']}")
        edit_page = client.get("/panel/trans-origin/" + origin_id + "/edit").get_data(as_text=True)
        retry_match = re.search(r'id="retry"[^>]*value="(\d+)"', edit_page)
        record("origin_edit_restores_retry", bool(retry_match and retry_match.group(1) == "2"),
               f"retry={retry_match.group(1) if retry_match else None}")

        body_id = client.post("/api/trans-origins/precheck", json={**origin_payload(origin_sheet["id"], retry=2), "id": origin_id})
        record("origin_create_rejects_body_id", body_id.status_code >= 400, f"status={body_id.status_code}")

        batches_before = count("fetch_batches")
        rows_before = count("data_rows")
        client.post("/api/trans-origins/" + origin_id + "/precheck", json={**origin_payload(origin_sheet["id"], retry=2), "note": "only note changed"})
        batches_after = count("fetch_batches")
        rows_after = count("data_rows")
        record("origin_repeat_edit_no_duplicate_data",
               batches_after == batches_before and rows_after == rows_before,
               f"batches={batches_before}->{batches_after} rows={rows_before}->{rows_after}")

        force_bad = client.post("/api/trans-origins/precheck", json={**origin_payload("missing-sheet"), "force_save": True})
        record("origin_force_save_rejects_invalid_sheet", force_bad.status_code >= 400,
               f"status={force_bad.status_code}")

        bad_create = client.post("/api/apis", json={**proxy_payload("bad-api"), "original_url": "not-a-url"})
        record("proxy_invalid_url_rejected", bad_create.status_code == 400, f"status={bad_create.status_code}")

    to.do_request = original_request
    print(json.dumps(RESULT, ensure_ascii=False, indent=2))
    return 0 if all(item["ok"] for item in RESULT) else 1


if __name__ == "__main__":
    sys.exit(run())
