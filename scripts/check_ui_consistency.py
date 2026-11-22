#!/usr/bin/env python3
"""Regression checks for unified list actions, form layout, and modal dialogs."""
import json
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import app.db as dbmod

TMP_DIR = Path(tempfile.mkdtemp(prefix="apimiao-ui-consistency-"))
dbmod.DB_PATH = TMP_DIR / "apimiao.db"

from app import create_app
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


def source_has_native_dialogs():
    files = [
        "static/js/app.js", "static/js/passkey.js", "static/js/api_form.js",
        "templates/api_manager.html", "templates/trans_origin_list.html",
        "templates/trans_user_list.html", "templates/api_keys.html",
        "templates/profile.html", "templates/users.html",
        "templates/data_sheets_list.html",
    ]
    hits = []
    pattern = re.compile(r"\b(alert|confirm|prompt)\s*\(")
    for rel in files:
        text = (ROOT / rel).read_text(encoding="utf-8")
        if pattern.search(text):
            hits.append(rel)
    return hits


def ordered(text, *markers):
    positions = [text.find(marker) for marker in markers]
    return all(pos >= 0 for pos in positions) and positions == sorted(positions)


def source_contracts():
    proxy = (ROOT / "templates/api_manager.html").read_text(encoding="utf-8")
    origin = (ROOT / "templates/trans_origin_list.html").read_text(encoding="utf-8")
    user = (ROOT / "templates/trans_user_list.html").read_text(encoding="utf-8")
    form = (ROOT / "templates/trans_user_form.html").read_text(encoding="utf-8")
    js = (ROOT / "static/js/trans_user_form.js").read_text(encoding="utf-8")
    backend = (ROOT / "app/api/trans_user.py").read_text(encoding="utf-8")
    return {
        "proxy_action_order": ordered(proxy, 'class="btn btn-xs btn-outline copy-api"',
                                      'class="btn btn-xs btn-outline edit-api"',
                                      'class="btn btn-xs btn-danger del-api"'),
        "origin_no_auth": "LABELS.auth" not in origin and ">${esc(LABELS.auth)}</th>" not in origin,
        "origin_copy_action": '<i class="bi bi-plus-square"></i> ${esc(LABELS.copy)}' in origin
                              and ordered(origin, "bi-plus-square", "LABELS.edit", "delOrigin"),
        "user_endpoint_copy": "copy-endpoint" in user and "copy-url" in user
                              and "location.origin + '/' + b.dataset.path" in user,
        "user_copy_action": '<i class="bi bi-plus-square"></i> {{ t(\'common.copy\') }}' in user,
        "form_chinese_hints": "选择数据表后，可拖动字段到 Params 或返回数据树" in form,
        "advanced_spacing": "tu-advanced" in form and "margin-top: 24px" in form,
        "no_dead_timeout_retry": 'id="timeout"' not in form and 'id="retry"' not in form
                                  and "$('timeout')" not in js and "$('retry')" not in js,
        "advanced_semantics_hint": "超时和重试不参与用户请求" in form,
        "single_sheet_join_guard": "selectedSheets.length < 2" in js
                                   and "src.sheet===target.dataset.sheet" in js,
        "output_node_drag_scope": 'class="output-node" data-id="${esc(node.id)}" draggable="true"' in js
                                  and "function blockOutputNodeDrag()" in js
                                  and "#outputTree .output-node, #outputTree .output-node-row" in js
                                  and "blockOutputNodeDrag();" in js
                                  and "function restoreOutputNodeDraggable()" in js,
        "output_node_key_only": "output-node-name" not in js
                                and "key:'Key 名称'" in js
                                and "key:'Key Name'" in js
                                and "legacyName" not in js
                                and "output_key = str(node.get(\"key\") or \"\")" in backend,
        "root_key_disabled": "const isRoot = node.id===outputTree.id" in js
                             and "const keyDisabled = isRoot ? 'disabled' : ''" in js
                             and "${keyDisabled}" in js,
        "field_key_from_source_leaf": "sourceKey.split('.').pop()||sourceKey" in js
                                      and "out[output_key or source_key]" in backend,
    }


def run():
    app = create_app()
    with app.app_context():
        sheet = create_sheet(ADMIN_ID, {
            "name": "ui-consistency-check",
            "fields": [
                {"key": "result.prov", "label": "省份", "json_type": "string", "enabled": True, "order": 0},
                {"key": "result.price", "label": "价格", "json_type": "string", "enabled": True, "order": 1},
            ],
        })
        sheet_id = sheet["id"]

    with app.test_client() as client:
        login(client)
        api = client.post("/api/trans-apis", json={
            "endpoint": "ui/check", "method": "GET", "note": "copy source",
            "sheet_ids": [sheet_id], "sheet_fields": {sheet_id: {}},
            "param_mapping": [], "body_mapping": [], "joins": [],
        }).get_json()
        api_id = api.get("id", "")
        with app.app_context():
            execute(
                "INSERT INTO trans_origins (id, user_id, note, original_url, original_method, sheet_id, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                ("ui-origin-1", ADMIN_ID, "origin source", "https://upstream.invalid/ui-check", "GET", sheet_id, utcnow()),
            )
            execute(
                "INSERT INTO trans_origins (id, user_id, note, original_url, original_method, sheet_id, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                ("ui-origin-2", ADMIN_ID, "origin second", "https://upstream.invalid/ui-check-2", "GET", sheet_id, utcnow()),
            )

        user_list = client.get("/panel/trans-user").get_data(as_text=True)
        record("user_list_created_date_fixed", "${esc(" not in user_list, "literal template expression absent")
        record("user_list_bulk_actions", all(marker in user_list for marker in (
            "bulkDeleteBtn", "selectAll", "copy_from=", "del-api"
        )), f"length={len(user_list)}")

        origin_list = client.get("/panel/trans-origin").get_data(as_text=True)
        record("origin_list_bulk_and_copy", all(marker in origin_list for marker in (
            "bulkDeleteBtn", "copy_from=", "delOrigin"
        )), f"length={len(origin_list)}")

        with app.app_context():
            execute(
                "INSERT INTO apis (id, user_id, endpoint, method, type, auth, note, config, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                ("ui-proxy-1", ADMIN_ID, "ui/proxy/check", "GET", "PROXY", "x-api-key,bearer", "proxy check", "{}", utcnow()),
            )
        proxy_list = client.get("/panel/api-manager").get_data(as_text=True)
        record("proxy_list_has_edit_action", "copy-api" in proxy_list and "del-api" in proxy_list
               and "btn-xs btn-outline" in proxy_list, f"length={len(proxy_list)}")

        copy_user = client.get("/panel/trans-user/new?copy_from=" + api_id).get_data(as_text=True)
        record("trans_user_copy_prefill", "window.USER_ID = null" in copy_user
               and 'id="note"' in copy_user and "copy source" in copy_user,
               f"length={len(copy_user)}")
        copy_origin = client.get("/panel/trans-origin/new?copy_from=ui-origin-1").get_data(as_text=True)
        record("trans_origin_copy_prefill", "origin source" in copy_origin,
               f"length={len(copy_origin)}")

        edit_user = client.get("/panel/trans-user/" + api_id + "/edit").get_data(as_text=True)
        record("edit_form_has_field_ids", '"id":' in edit_user and "result.prov" in edit_user,
               f"length={len(edit_user)}")
        form = client.get("/panel/trans-user/new").get_data(as_text=True)
        record("form_side_by_side_and_no_body", "tu-columns" in form
               and "targetBody" not in form and "返回数据树" in form,
               f"length={len(form)}")

        deleted_user = client.post("/api/trans-apis/bulk-delete", json={"ids": [api_id]})
        deleted_origin = client.post("/api/trans-origins/bulk-delete", json={"ids": ["ui-origin-2"]})
        record("bulk_delete_endpoints", deleted_user.status_code == 200 and deleted_origin.status_code == 200,
               f"user={deleted_user.status_code} origin={deleted_origin.status_code}")

    record("no_native_browser_dialogs", source_has_native_dialogs() == [],
           f"files={source_has_native_dialogs()}")
    contracts = source_contracts()
    for name, ok in contracts.items():
        record(name, ok, "")

    print(json.dumps(RESULT, ensure_ascii=False, indent=2))
    return 0 if all(item["ok"] for item in RESULT) else 1


if __name__ == "__main__":
    sys.exit(run())
