"""TRANS User 的页面与 API 路由。"""
import json

from flask import redirect, render_template, request, session, url_for

from ..db import all_rows, execute, new_id, one, utcnow
from ..utils import error, ok, require_login, valid_endpoint, PLACEHOLDER_RE
from . import bp

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
        "timeout": _number(data.get("timeout"), 10, 1, 120),
        "retry": _number(data.get("retry"), 0, 0, 5),
        "rate_limit": _number(data.get("rate_limit"), 0, 0, 100000),
    }


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
    sheets = all_rows("SELECT id, name FROM data_sheets WHERE user_id = ? ORDER BY name", (session["uid"],))
    return render_template("trans_user_form.html", api=None, cfg={},
                           sheets=[dict(s) for s in sheets])


@bp.route("/panel/trans-user/<api_id>/edit")
def trans_user_edit(api_id):
    gate = require_login()
    if gate:
        return gate
    row = one("SELECT * FROM trans_apis WHERE id = ? AND user_id = ?", (api_id, session["uid"]))
    if not row:
        return redirect(url_for("api.trans_user_list"))
    sheets = all_rows("SELECT id, name FROM data_sheets WHERE user_id = ? ORDER BY name", (session["uid"],))
    return render_template("trans_user_form.html", api=dict(row), cfg=_api_config(row),
                           sheets=[dict(s) for s in sheets])


@bp.route("/api/trans-user/sheets")
def trans_user_sheets():
    """当前用户的所有数据表（供 Choose Data Sheets 弹窗）。"""
    gate = require_login()
    if gate:
        return gate
    rows = all_rows(
        "SELECT s.id, s.name, s.fields_json, o.note AS origin_note "
        "FROM data_sheets s LEFT JOIN trans_origins o ON s.origin_id = o.id "
        "WHERE s.user_id = ? ORDER BY s.name", (session["uid"],),
    )
    out = []
    for r in rows:
        out.append({"id": r["id"], "name": r["name"],
                    "fields": _load(r["fields_json"], "[]")})
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
            "sheet_fields_json", "param_mapping_json", "body_mapping_json", "timeout", "retry", "rate_limit")
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
            "sheet_fields_json", "param_mapping_json", "body_mapping_json", "timeout", "retry", "rate_limit")
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
