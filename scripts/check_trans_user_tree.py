#!/usr/bin/env python3
"""Regression checks for TRANS User filtering and output-tree editor."""
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import app.db as dbmod

TMP_DIR = Path(tempfile.mkdtemp(prefix="apimiao-trans-user-tree-"))
dbmod.DB_PATH = TMP_DIR / "apimiao.db"

from app import create_app
from app.api.trans_user import execute_query
from app.api.query_spec import build_query_spec
from app.data_sheets.service import create_sheet, get_sheet
from app.db import all_rows, execute, utcnow
from config import ADMIN_ID

RESULT = []


def record(name, ok, detail=""):
    RESULT.append({"case": name, "ok": bool(ok), "detail": detail})


def login(client):
    with client.session_transaction() as session:
        session.clear()
        session["uid"] = ADMIN_ID
        session["role"] = "ADMIN"


def api_payload(sheet_id, fields, endpoint, tree=None):
    payload = {
        "endpoint": endpoint,
        "method": "GET",
        "note": "check",
        "sheet_ids": [sheet_id],
        "sheet_fields": {sheet_id: {field["id"]: True for field in fields}},
        "param_mapping": [{
            "sheet_id": sheet_id,
            "field_id": fields[0]["id"],
            "field": fields[0]["key"],
            "alias": "prov",
        }],
        "body_mapping": [],
        "joins": [],
        "timeout": 10, "retry": 0, "rate_limit": 0,
    }
    if tree is not None:
        payload["output_tree"] = tree
    return payload


def run():
    app = create_app()
    with app.app_context():
        sheet = create_sheet(ADMIN_ID, {
            "name": "trans-user-tree-check",
            "fields": [
                {"key": "result.prov", "label": "省份", "json_type": "string", "enabled": True, "order": 0},
                {"key": "result.p0.price", "label": "92#", "json_type": "string", "enabled": True, "order": 1},
            ],
        })
        sheet_id = sheet["id"]
        fields = get_sheet(ADMIN_ID, sheet_id)["fields"]
        for prov, price in (("北京", "7.82"), ("上海", "8.10")):
            execute(
                "INSERT INTO data_rows (sheet_id, batch_id, data_json, params_used, fetched_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (sheet_id, "test-batch", json.dumps({
                    "result.prov": prov, "result.p0.price": price,
                }, ensure_ascii=False), "{}", utcnow()),
            )

    prov_id = fields[0]["id"]
    price_id = fields[1]["id"]
    tree = {
        "id": "root", "type": "object", "name": "root", "key": "", "field_id": "", "children": [
            {"id": "obj-result", "type": "object", "name": "结果", "key": "result", "field_id": "", "children": [
                {"id": "f-prov", "type": "field", "name": "省份", "key": "prov", "field_id": prov_id, "children": []},
            ]},
            {"id": "arr-price", "type": "array", "name": "价格", "key": "prices", "field_id": "", "children": [
                {"id": "f-price", "type": "field", "name": "92号", "key": "p0", "field_id": price_id, "children": []},
            ]},
        ],
    }
    empty_key_tree = {
        "id": "root", "type": "object", "key": "", "field_id": "", "children": [
            {"id": "f-price-empty", "type": "field", "key": "", "field_id": price_id, "children": []},
        ],
    }

    with app.test_client() as client:
        login(client)
        created = client.post("/api/trans-apis", json=api_payload(
            sheet_id, fields, "tree/check", tree
        ))
        record("tree_api_created", created.status_code == 200, f"status={created.status_code}")
        api_id = created.get_json().get("id", "")

        flat_created = client.post("/api/trans-apis", json=api_payload(
            sheet_id, fields, "flat/check"
        ))
        record("flat_api_created", flat_created.status_code == 200, f"status={flat_created.status_code}")
        flat_id = flat_created.get_json().get("id", "")

        empty_key_created = client.post("/api/trans-apis", json=api_payload(
            sheet_id, fields, "emptykey/check", empty_key_tree
        ))
        record("empty_key_api_created", empty_key_created.status_code == 200,
               f"status={empty_key_created.status_code}")
        empty_key_id = empty_key_created.get_json().get("id", "")

        invalid = client.post("/api/trans-apis", json=api_payload(
            sheet_id, fields, "invalid/check", {"id": "bad", "type": "nope", "children": []}
        ))
        record("invalid_tree_rejected", invalid.status_code == 400, f"status={invalid.status_code}")

        with app.app_context():
            api_row = dict(next(r for r in all_rows("SELECT * FROM trans_apis") if r["id"] == api_id))
            flat_row = dict(next(r for r in all_rows("SELECT * FROM trans_apis") if r["id"] == flat_id))

            rows, err = execute_query(api_row, {"prov": "北京"})
            spec = build_query_spec(flat_row)
            flat_filtered, flat_filter_err = execute_query(flat_row, {"prov": "北京"})
            record("flat_key_filter_hits",
                   flat_filter_err is None and len(flat_filtered) == 1
                   and flat_filtered[0].get("result.prov") == "北京",
                   f"count={len(flat_filtered)} err={flat_filter_err} filters={[(f.alias, f.key) for f in spec.filters]} row={flat_filtered[0] if flat_filtered else None}")
            record("output_tree_shape",
                   err is None and rows and rows[0] == {
                       "result": {"prov": "北京"},
                       "prices": [{"p0": "7.82"}],
                   },
                   f"row={rows[0] if rows else None}")

            empty_key_row = dict(next(r for r in all_rows("SELECT * FROM trans_apis") if r["id"] == empty_key_id))
            empty_key_rows, empty_key_err = execute_query(empty_key_row, {"prov": "北京"})
            record("empty_field_key_uses_full_source",
                   empty_key_err is None and empty_key_rows and empty_key_rows[0] == {
                       "result.p0.price": "7.82",
                   },
                   f"row={empty_key_rows[0] if empty_key_rows else None} err={empty_key_err}")

            flat_rows, flat_err = execute_query(flat_row, {"prov": "上海"})
            record("empty_tree_keeps_flat_output",
                   flat_err is None and len(flat_rows) == 1
                   and "result.prov" in flat_rows[0],
                   f"row={flat_rows[0] if flat_rows else None} err={flat_err}")

        edit_page = client.get("/panel/trans-user/" + api_id + "/edit").get_data(as_text=True)
        record("editor_present",
               all(marker in edit_page for marker in (
                   "返回数据树", "outputTree", "outputAddObject", "outputAddArray", "outputReset"
               )), f"length={len(edit_page)}")
        record("saved_tree_restored",
               "f-prov" in edit_page and "arr-price" in edit_page,
               "saved node ids present")

    print(json.dumps(RESULT, ensure_ascii=False, indent=2))
    return 0 if all(item["ok"] for item in RESULT) else 1


if __name__ == "__main__":
    sys.exit(run())
