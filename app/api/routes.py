"""API Manager：列表、创建、编辑、删除。"""
import json
import re

from flask import redirect, render_template, request, session, url_for

from config import endpoint_prefix
from ..db import api_config, all_rows, execute, new_id, one, utcnow
from ..utils import error, ok, placeholders, require_login, valid_endpoint, PLACEHOLDER_RE
from . import bp

METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE")
KEYVALUES = ("params", "headers", "cookies")


def _clean_pairs(data, key):
    items = data.get(key) if isinstance(data, dict) else []
    if not isinstance(items, list):
        return []
    out = []
    for item in items:
        if not isinstance(item, dict):
            continue
        k = str(item.get("key") or "").strip()
        v = str(item.get("value") or "")
        if k:
            out.append({"key": k, "value": v})
    return out


def _as_bool(value, default=True):
    """兼容前端下拉传来的 true/false、'1'/'0'、1/0。"""
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def _number(value, cast, default, low, high):
    """安全解析数值字段，失败返回 None。"""
    try:
        text = str(value).strip() if value is not None else ""
        result = cast(text or default)
    except (TypeError, ValueError):
        return None
    return max(low, min(result, high))


def _validate_payload(data, api_type):
    endpoint = (data.get("endpoint") or "").strip()
    method = (data.get("method") or "").upper()
    original_url = (data.get("original_url") or "").strip()
    if not valid_endpoint(endpoint):
        return None, "Invalid API Endpoint"
    tokens = PLACEHOLDER_RE.findall(endpoint)
    if len(tokens) != len(set(tokens)):
        return None, "Duplicate placeholder in endpoint"
    if method not in METHODS:
        return None, "Invalid Method"
    if not original_url.startswith(("http://", "https://")):
        return None, "Invalid Original API URL"
    if not placeholders(endpoint) <= placeholders(original_url):
        return None, "Original API URL must contain all endpoint placeholders"

    cfg = {
        "original_url": original_url,
        "original_method": (data.get("original_method") or "GET").upper(),
        "original_key": (data.get("original_key") or "").strip(),
        "params": _clean_pairs(data, "params"),
        "headers": _clean_pairs(data, "headers"),
        "cookies": _clean_pairs(data, "cookies"),
        "body_mode": data.get("body_mode") or "none",
        "body": data.get("body") or "",
        "body_enabled": _as_bool(data.get("body_enabled"), True),
        "accept_encoding": data.get("accept_encoding") if data.get("accept_encoding") in ("auto", "gzip", "pass") else "auto",
        "timeout": _number(data.get("timeout"), int, 10, 1, 120),
        "retry": _number(data.get("retry"), int, 0, 0, 5),
        "rate_limit": _number(data.get("rate_limit"), int, 0, 0, 100000),
        "cache_time": _number(data.get("cache_time"), float, 0, 0, 604800),
        "cache_unit": data.get("cache_unit") if data.get("cache_unit") in ("min", "sec") else "sec",
        "param_conflict": data.get("param_conflict") or "user",
        "passthrough": _as_bool(data.get("passthrough"), True),
    }
    if any(cfg[k] is None for k in ("timeout", "retry", "rate_limit", "cache_time")):
        return None, "Invalid numeric field"
    if cfg["original_method"] not in METHODS:
        return None, "Invalid Original API Method"
    if api_type == "PROXY":
        if cfg["body_mode"] not in ("none", "json", "text", "binary"):
            return None, "Invalid Body format"
    else:
        cfg["frequency"] = data.get("frequency") if data.get("frequency") in ("DAY", "WEEK") else "DAY"
        days = data.get("days") or []
        cfg["days"] = [int(x) for x in days if str(x).isdigit() and 0 <= int(x) <= 6]
        times = data.get("times") or []
        for ts in times:
            if not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", str(ts)):
                return None, f"Invalid time: {ts}"
        cfg["times"] = list(times)
        cfg["mapping"] = data.get("mapping") or {}
        cfg["test_params"] = data.get("test_params") or []
    return {"endpoint": endpoint, "method": method, "note": (data.get("note") or "").strip(), "auth": data.get("auth") or "x-api-key,bearer", "config": cfg}, None


@bp.route("/panel/api-manager")
def api_manager():
    gate = require_login()
    if gate:
        return gate
    rows = all_rows("SELECT * FROM apis WHERE user_id = ? ORDER BY created_at DESC", (session["uid"],))
    return render_template("api_manager.html", apis=[dict(r) for r in rows], endpoint_prefix=endpoint_prefix)


@bp.route("/panel/api-manager/new")
def api_new():
    gate = require_login()
    if gate:
        return gate
    api_type = "PROXY"  # TRANSMISSION 已迁移到独立的 TRANS Origin/User 模块
    # 复制已有 API：用其信息预填创建表单。id 置空 -> 保存时走新建；endpoint 留空因其必须唯一
    copy_from = request.args.get("copy_from", "").strip()
    api, cfg, is_copy = None, {}, False
    if copy_from:
        src = one("SELECT * FROM apis WHERE id = ? AND user_id = ?", (copy_from, session["uid"]))
        if src:
            api_type = src["type"]
            cfg = api_config(src)
            api = dict(src)
            api["id"] = ""          # 关键：让表单以「新建」模式提交
            api["endpoint"] = ""    # Endpoint + Method 必须唯一，留空让用户填
            is_copy = True
    return render_template("api_form.html", api=api, api_type=api_type, cfg=cfg,
                           ph="{{", phend="}}", is_copy=is_copy)


@bp.route("/panel/api-manager/<api_id>/edit")
def api_edit(api_id):
    gate = require_login()
    if gate:
        return gate
    row = one("SELECT * FROM apis WHERE id = ? AND user_id = ?", (api_id, session["uid"]))
    if not row:
        return redirect(url_for("api.api_manager"))
    return render_template("api_form.html", api=dict(row), api_type=row["type"], cfg=api_config(row), ph="{{", phend="}}", is_copy=False)


@bp.route("/api/apis", methods=["POST"])
def api_create():
    gate = require_login()
    if gate:
        return gate
    data = request.get_json(silent=True) or {}
    api_type = "PROXY"  # 仅支持 PROXY，TRANS 走独立模块
    payload, err = _validate_payload(data, api_type)
    if err:
        return error(err, 400)
    if one("SELECT 1 FROM apis WHERE user_id = ? AND endpoint = ? AND method = ?", (session["uid"], payload["endpoint"], payload["method"])):
        return error("Endpoint + method already exists", 400)
    api_id = new_id()
    execute(
        "INSERT INTO apis (id, user_id, endpoint, method, type, auth, encoding, note, config, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (api_id, session["uid"], payload["endpoint"], payload["method"], api_type, payload["auth"],
         "UTF-8", payload["note"], json.dumps(payload["config"], ensure_ascii=False), utcnow()),
    )
    return ok(id=api_id)


@bp.route("/api/apis/<api_id>", methods=["PUT"])
def api_update(api_id):
    gate = require_login()
    if gate:
        return gate
    row = one("SELECT * FROM apis WHERE id = ? AND user_id = ?", (api_id, session["uid"]))
    if not row:
        return error("API not found", 404)
    data = request.get_json(silent=True) or {}
    api_type = row["type"]
    payload, err = _validate_payload(data, api_type)
    if err:
        return error(err, 400)
    duplicate = one("SELECT 1 FROM apis WHERE user_id = ? AND endpoint = ? AND method = ? AND id != ?",
                    (session["uid"], payload["endpoint"], payload["method"], api_id))
    if duplicate:
        return error("Endpoint + method already exists", 400)
    execute(
        "UPDATE apis SET endpoint = ?, method = ?, auth = ?, encoding = ?, note = ?, config = ? WHERE id = ?",
        (payload["endpoint"], payload["method"], payload["auth"], "UTF-8",
         payload["note"], json.dumps(payload["config"], ensure_ascii=False), api_id),
    )
    return ok()


@bp.route("/api/apis/<api_id>/toggle", methods=["POST"])
def api_toggle(api_id):
    gate = require_login()
    if gate:
        return gate
    row = one("SELECT 1 FROM apis WHERE id = ? AND user_id = ?", (api_id, session["uid"]))
    if not row:
        return error("API not found", 404)
    data = request.get_json(silent=True) or {}
    execute("UPDATE apis SET enabled = ? WHERE id = ?", (1 if data.get("enabled") else 0, api_id))
    return ok()


@bp.route("/api/apis/<api_id>", methods=["DELETE"])
def api_delete(api_id):
    gate = require_login()
    if gate:
        return gate
    execute("DELETE FROM apis WHERE id = ? AND user_id = ?", (api_id, session["uid"]))
    return ok()


@bp.route("/api/apis/bulk-delete", methods=["POST"])
def api_bulk_delete():
    gate = require_login()
    if gate:
        return gate
    ids = (request.get_json(silent=True) or {}).get("ids") or []
    for api_id in ids:
        execute("DELETE FROM apis WHERE id = ? AND user_id = ?", (api_id, session["uid"]))
    return ok()

