"""API Manager：列表、创建、编辑、删除、API Test。"""
import json
import re

import httpx
from flask import redirect, render_template, request, session, url_for

from config import endpoint_prefix
from ..db import api_config, all_rows, execute, new_id, one, save_api_config, utcnow
from ..utils import error, ok, placeholders, require_login, valid_endpoint, merge_url_params
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


def _validate_payload(data, api_type):
    endpoint = (data.get("endpoint") or "").strip()
    method = (data.get("method") or "").upper()
    original_url = (data.get("original_url") or "").strip()
    if not valid_endpoint(endpoint):
        return None, "Invalid API Endpoint"
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
        "timeout": max(1, min(int(data.get("timeout") or 10), 120)),
        "retry": max(0, min(int(data.get("retry") or 0), 5)),
        "rate_limit": max(0, min(int(data.get("rate_limit") or 0), 100000)),
        "cache_time": max(0, min(float(data.get("cache_time") or 0), 604800)),
        "cache_unit": data.get("cache_unit") if data.get("cache_unit") in ("min", "sec") else "sec",
        "param_conflict": data.get("param_conflict") or "user",
        "passthrough": _as_bool(data.get("passthrough"), True),
    }
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


@bp.route("/api/apis/test", methods=["POST"])
def api_test():
    """API Test：用占位符键值对真实请求一次源 API，保存键值对供日后复用。

    测试请求与真实转发保持一致：应用配置的 Params/Header/Cookie 和 {{o-api-key}}。
    """
    gate = require_login()
    if gate:
        return gate
    # 延迟导入，避免 app/api/__init__.py 中 routes 先于 proxy 导入导致循环依赖
    from .proxy import _fill, _pairs_to_dict
    data = request.get_json(silent=True) or {}
    # 占位符键值对（弹窗里填的）。前端传的字段名是 pairs，兼容旧的 params。
    pairs = data.get("pairs") or data.get("params") or []
    api_id = data.get("api_id") or ""
    cfg = data.get("cfg") or {}
    # 优先用前端传来的当前表单配置；没有则从数据库读取已保存的配置
    if not cfg and api_id:
        row = one("SELECT * FROM apis WHERE id = ? AND user_id = ?", (api_id, session["uid"]))
        if row:
            cfg = api_config(row)
    url = (data.get("url") or cfg.get("original_url") or "").strip()
    method = (data.get("method") or cfg.get("original_method") or "GET").upper()
    o_api_key = cfg.get("original_key", "")

    for p in pairs:
        key = str(p.get("key") or "").strip()
        if not key:
            continue
        execute(
            "INSERT INTO api_test_params (user_id, api_id, key, value, created_at) VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(user_id, api_id, key) DO UPDATE SET value = excluded.value, created_at = excluded.created_at",
            (session["uid"], api_id, key, str(p.get("value") or ""), utcnow()),
        )

    # 占位符值：弹窗键值对。URL 与 Params/Header/Cookie 都用它替换。
    values = {str(p.get("key")): str(p.get("value") or "") for p in pairs if p.get("key")}
    try:
        url = _fill(url, values, o_api_key)
        all_params = _pairs_to_dict(cfg.get("params", []), values, o_api_key)
        headers = _pairs_to_dict(cfg.get("headers", []), values, o_api_key)
        cookies = _pairs_to_dict(cfg.get("cookies", []), values, o_api_key)
        # URL 自带的 query 与配置 Params 合并，防止 httpx 覆盖 URL 里的占位符参数
        url, all_params = merge_url_params(url, all_params)
        kwargs = {}
        if all_params:
            kwargs["params"] = all_params
        if headers:
            kwargs["headers"] = headers
        if cookies:
            kwargs["cookies"] = cookies
        # Body（与 proxy 的 body_mode 处理保持一致）
        body_mode = cfg.get("body_mode", "none")
        if body_mode == "json" and cfg.get("body"):
            kwargs["content"] = _fill(cfg["body"], values, o_api_key).encode("utf-8")
            if "content-type" not in {k.lower() for k in headers}:
                kwargs.setdefault("headers", {})["Content-Type"] = "application/json; charset=utf-8"
        elif body_mode == "text" and cfg.get("body"):
            kwargs["content"] = _fill(cfg["body"], values, o_api_key).encode("utf-8")
        timeout = cfg.get("timeout", 10) or 10
        with httpx.Client(timeout=timeout, follow_redirects=True) as client:
            resp = client.request(method, url, **kwargs)
        return ok(status=resp.status_code, headers=dict(resp.headers),
                  body=resp.text, cookies=dict(resp.cookies))
    except Exception as exc:
        return error(f"Request failed: {exc}", 502)


@bp.route("/api/apis/test-params")
def api_test_params():
    gate = require_login()
    if gate:
        return gate
    api_id = request.args.get("api_id", "")
    rows = all_rows("SELECT key, value FROM api_test_params WHERE user_id = ? AND api_id = ? ORDER BY created_at", (session["uid"], api_id))
    return ok(params=[dict(r) for r in rows])


