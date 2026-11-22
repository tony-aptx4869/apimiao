"""TRANS User 的页面与 API 路由。"""
import json

from flask import redirect, render_template, request, session, url_for

from ..db import all_rows, execute, new_id, one, utcnow
from ..utils import error, ok, require_login, valid_endpoint
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
        "sheet_ids": _load(row["sheet_ids_json"], "[]"),
        "joins": _load(row["joins_json"], "[]"),
        "sheet_fields": _load(row["sheet_fields_json"], "{}"),
        "param_mapping": _load(row["param_mapping_json"], "[]"),
        "body_mapping": _load(row["body_mapping_json"], "[]"),
        "timeout": row["timeout"], "retry": row["retry"], "rate_limit": row["rate_limit"],
        "enabled": row["enabled"],
    }


def _payload_config(data):
    def dumps(v):
        return json.dumps(v or [], ensure_ascii=False)
    return {
        "endpoint": str(data.get("endpoint") or "").strip(),
        "method": str(data.get("method") or "GET").upper(),
        "auth": str(data.get("auth") or "x-api-key,bearer"),
        "encoding": str(data.get("encoding") or "UTF-8"),
        "note": str(data.get("note") or "").strip(),
        "sheet_ids_json": dumps(data.get("sheet_ids")),
        "joins_json": dumps(data.get("joins")),
        "sheet_fields_json": json.dumps(data.get("sheet_fields") or {}, ensure_ascii=False),
        "param_mapping_json": dumps(data.get("param_mapping")),
        "body_mapping_json": dumps(data.get("body_mapping")),
        "timeout": int(data.get("timeout") or 10),
        "retry": int(data.get("retry") or 0),
        "rate_limit": int(data.get("rate_limit") or 0),
    }


def _validate(cfg):
    if not valid_endpoint(cfg["endpoint"]):
        return "Invalid API Endpoint"
    if cfg["method"] not in METHODS:
        return "Invalid method"
    if not cfg["sheet_ids_json"] or cfg["sheet_ids_json"] == "[]":
        return "Select at least one data sheet"
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
                           apis=[_api_config(r) for r in rows], prefix=endpoint_prefix("TRANS"))


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
    err = _validate(cfg)
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
    err = _validate(cfg)
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
