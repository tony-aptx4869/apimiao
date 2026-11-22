"""TRANS User 的页面与 API 路由。"""
import json

from flask import redirect, render_template, request, session, url_for

from ..db import all_rows, execute, new_id, one, utcnow
from ..utils import error, ok, require_login, valid_endpoint, PLACEHOLDER_RE
from . import bp
from ..data_sheets.service import list_sheets

METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE")


def _load(value, default):
    try:
        return json.loads(value or default)
    except (ValueError, TypeError):
        return json.loads(default)


def _api_config(row):
    return {
        "id": row["id"], "endpoint": row["endpoint"], "method": row["method"],
        "auth": row["auth"], "encoding": row["encoding"], "note": row["note"],
        "created_at": row["created_at"] if "created_at" in row.keys() else "",
        "sheet_ids": _load(row["sheet_ids_json"], "[]"),
        "joins": _load(row["joins_json"], "[]"),
        "sheet_fields": _load(row["sheet_fields_json"], "{}"),
        "param_mapping": _load(row["param_mapping_json"], "[]"),
        "body_mapping": _load(row["body_mapping_json"], "[]"),
        "query_config_v2": _load(row["query_config_v2"] if "query_config_v2" in row.keys() else "{}", "{}"),
        "timeout": row["timeout"], "retry": row["retry"], "rate_limit": row["rate_limit"],
        "enabled": row["enabled"],
    }


def _number(value, default, low, high):
    try:
        text = str(value).strip() if value is not None else ""
        return max(low, min(int(text or default), high))
    except (TypeError, ValueError):
        return None


def _payload_config(data):
    def dumps(v):
        return json.dumps(v if isinstance(v, list) else [], ensure_ascii=False)
    return {
        "endpoint": str(data.get("endpoint") or "").strip(),
        "method": str(data.get("method") or "GET").upper(),
        "auth": str(data.get("auth") or "x-api-key,bearer"),
        "encoding": str(data.get("encoding") or "UTF-8"),
        "note": str(data.get("note") or "").strip(),
        "sheet_ids_json": dumps(data.get("sheet_ids")),
        "joins_json": dumps(data.get("joins")),
        "sheet_fields_json": json.dumps(data.get("sheet_fields") if isinstance(data.get("sheet_fields"), dict) else {}, ensure_ascii=False),
        "param_mapping_json": dumps(data.get("param_mapping")),
        "body_mapping_json": dumps(data.get("body_mapping")),
        "query_config_v2": _query_config(data, None),
        "timeout": _number(data.get("timeout"), 10, 1, 120),
        "retry": _number(data.get("retry"), 0, 0, 5),
        "rate_limit": _number(data.get("rate_limit"), 0, 0, 100000),
    }


def _field_id(sheet_id, field_name, field_id=""):
    if field_id:
        return str(field_id)
    value = str(field_name or "")
    row = one("SELECT id FROM sheet_fields WHERE sheet_id = ? AND (id = ? OR key = ?)",
              (str(sheet_id or ""), value, value))
    return row["id"] if row else ""


def _query_config(data, user_id):
    """Build the typed query config from the form payload at the boundary."""
    sheet_ids = [str(sid) for sid in (data.get("sheet_ids") or [])]
    joins = []
    for item in data.get("joins") or []:
        if not isinstance(item, dict):
            continue
        left_sheet = str(item.get("left_sheet") or "")
        right_sheet = str(item.get("right_sheet") or "")
        left_id = _field_id(left_sheet, item.get("left_field"), item.get("left_field_id"))
        right_id = _field_id(right_sheet, item.get("right_field"), item.get("right_field_id"))
        if left_id and right_id:
            joins.append({
                "left_sheet": left_sheet, "left_field_id": left_id,
                "right_sheet": right_sheet, "right_field_id": right_id,
            })
    projections = []
    for sheet_id, fields in (data.get("sheet_fields") or {}).items():
        if not isinstance(fields, dict):
            continue
        for field_name, enabled in fields.items():
            field_id = _field_id(sheet_id, field_name)
            if field_id:
                projections.append({
                    "sheet_id": str(sheet_id), "field_id": field_id,
                    "key": str(field_name), "enabled": bool(enabled),
                })
    filters = []
    for location in ("params", "body"):
        items = (data.get(location + "_mapping") or [])
        for item in items:
            if not isinstance(item, dict):
                continue
            sheet_id = str(item.get("sheet_id") or "")
            field_id = _field_id(sheet_id, item.get("field"), item.get("field_id"))
            if field_id:
                filters.append({
                    "sheet_id": sheet_id, "field_id": field_id,
                    "key": str(item.get("field")),
                    "alias": str(item.get("alias") or item.get("field")),
                    "location": location,
                })
    return json.dumps({"sheets": sheet_ids, "joins": joins, "projections": projections, "filters": filters},
                      ensure_ascii=False)


def _validate(cfg, user_id):
    if not valid_endpoint(cfg["endpoint"]):
        return "Invalid API Endpoint"
    tokens = PLACEHOLDER_RE.findall(cfg["endpoint"])
    if len(tokens) != len(set(tokens)):
        return "Duplicate placeholder in endpoint"
    if cfg["method"] not in METHODS:
        return "Invalid method"
    if any(cfg[k] is None for k in ("timeout", "retry", "rate_limit")):
        return "Invalid numeric field"
    sheet_ids = _load(cfg["sheet_ids_json"], "[]")
    if not isinstance(sheet_ids, list) or not sheet_ids:
        return "Select at least one data sheet"
    joins = _load(cfg["joins_json"], "[]")
    sheet_fields = _load(cfg["sheet_fields_json"], "{}")
    referenced = set(str(s) for s in sheet_ids)
    if isinstance(joins, list):
        for j in joins:
            if not isinstance(j, dict):
                continue
            for key in ("left_sheet", "right_sheet"):
                if j.get(key):
                    referenced.add(str(j[key]))
    if isinstance(sheet_fields, dict):
        referenced.update(str(sid) for sid in sheet_fields)
    for sid in referenced:
        if not one("SELECT 1 FROM data_sheets WHERE id = ? AND user_id = ?", (sid, user_id)):
            return "Invalid data sheet"
    return None


# ---------------- 页面 ----------------
@bp.route("/panel/trans-user")
def trans_user_list():
    gate = require_login()
    if gate:
        return gate
    from config import endpoint_prefix
    rows = all_rows("SELECT * FROM trans_apis WHERE user_id = ? ORDER BY created_at DESC", (session["uid"],))
    return render_template("trans_user_list.html",
                           apis=[_api_config(r) for r in rows], prefix=endpoint_prefix("TRANSMISSION"))


@bp.route("/panel/trans-user/new")
def trans_user_new():
    gate = require_login()
    if gate:
        return gate
    sheets = all_rows("SELECT id, name, fields_json FROM data_sheets WHERE user_id = ? ORDER BY name", (session["uid"],))
    return render_template("trans_user_form.html", api=None, cfg={},
                           sheets=[{"id": s["id"], "name": s["name"], "fields": _load(s["fields_json"], "[]")} for s in sheets])


@bp.route("/panel/trans-user/<api_id>/edit")
def trans_user_edit(api_id):
    gate = require_login()
    if gate:
        return gate
    row = one("SELECT * FROM trans_apis WHERE id = ? AND user_id = ?", (api_id, session["uid"]))
    if not row:
        return redirect(url_for("api.trans_user_list"))
    sheets = all_rows("SELECT id, name, fields_json FROM data_sheets WHERE user_id = ? ORDER BY name", (session["uid"],))
    return render_template("trans_user_form.html", api=dict(row), cfg=_api_config(row),
                           sheets=[{"id": s["id"], "name": s["name"], "fields": _load(s["fields_json"], "[]")} for s in sheets])


@bp.route("/api/trans-user/sheets")
def trans_user_sheets():
    """当前用户的所有数据表（供 Choose Data Sheets 弹窗）。"""
    gate = require_login()
    if gate:
        return gate
    out = []
    for sheet in list_sheets(session["uid"]):
        out.append({
            "id": sheet["id"],
            "name": sheet["name"],
            "fields": [
                {"id": field["id"], "key": field["key"], "enabled": field["enabled"]}
                for field in sheet["fields"]
            ],
        })
    return ok(sheets=out)


# ---------------- API ----------------
@bp.route("/api/trans-apis", methods=["POST"])
def trans_api_create():
    gate = require_login()
    if gate:
        return gate
    data = request.get_json(silent=True) or {}
    cfg = _payload_config(data)
    err = _validate(cfg, session["uid"])
    if err:
        return error(err, 400)
    if one("SELECT 1 FROM trans_apis WHERE user_id = ? AND endpoint = ? AND method = ?",
           (session["uid"], cfg["endpoint"], cfg["method"])):
        return error("Endpoint + method already exists", 400)
    api_id = new_id()
    cols = ("endpoint", "method", "auth", "encoding", "note", "sheet_ids_json", "joins_json",
            "sheet_fields_json", "param_mapping_json", "body_mapping_json", "query_config_v2",
            "timeout", "retry", "rate_limit")
    execute(
        "INSERT INTO trans_apis (id, user_id, " + ", ".join(cols) + ", enabled, created_at) VALUES (" +
        ", ".join(["?"] * (len(cols) + 4)) + ")",
        (api_id, session["uid"], *[cfg[c] for c in cols], 1, utcnow()),
    )
    return ok(id=api_id)


@bp.route("/api/trans-apis/<api_id>", methods=["PUT"])
def trans_api_update(api_id):
    gate = require_login()
    if gate:
        return gate
    row = one("SELECT 1 FROM trans_apis WHERE id = ? AND user_id = ?", (api_id, session["uid"]))
    if not row:
        return error("API not found", 404)
    data = request.get_json(silent=True) or {}
    cfg = _payload_config(data)
    err = _validate(cfg, session["uid"])
    if err:
        return error(err, 400)
    if one("SELECT 1 FROM trans_apis WHERE user_id = ? AND endpoint = ? AND method = ? AND id != ?",
           (session["uid"], cfg["endpoint"], cfg["method"], api_id)):
        return error("Endpoint + method already exists", 400)
    cols = ("endpoint", "method", "auth", "encoding", "note", "sheet_ids_json", "joins_json",
            "sheet_fields_json", "param_mapping_json", "body_mapping_json", "query_config_v2",
            "timeout", "retry", "rate_limit")
    execute("UPDATE trans_apis SET " + ", ".join(f"{c}=?" for c in cols) + " WHERE id=? AND user_id=?",
            (*[cfg[c] for c in cols], api_id, session["uid"]))
    return ok()


@bp.route("/api/trans-apis/<api_id>", methods=["DELETE"])
def trans_api_delete(api_id):
    gate = require_login()
    if gate:
        return gate
    if not one("SELECT 1 FROM trans_apis WHERE id = ? AND user_id = ?", (api_id, session["uid"])):
        return error("API not found", 404)
    execute("DELETE FROM trans_apis WHERE id = ? AND user_id = ?", (api_id, session["uid"]))
    return ok()
