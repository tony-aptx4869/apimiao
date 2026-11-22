"""API 网关：验证 API Key、占位符替换、参数合并、转发、原样响应、限流与日志。"""
import hashlib
import json
import re
import time
from datetime import datetime, timedelta, timezone

import httpx
from flask import Response, g, request

from config import GATEWAY_MODE
from ..db import api_config, all_rows, check_rate_limit, execute, log_request, one, utcnow
from ..utils import PLACEHOLDER_RE, error, hash_api_key, merge_url_params, server_egress_ip
from . import bp


# ---------------- Proxy 响应缓存 ----------------
def _cache_key(api_id, method, endpoint, params, body_bytes):
    """按 API + Method + 端点 + 全部参数/body 生成稳定的缓存 key。"""
    raw = json.dumps({
        "api": api_id, "method": method, "endpoint": endpoint,
        "params": {str(k): str(v) for k, v in sorted((params or {}).items())},
        "body": (body_bytes or b"").decode("utf-8", "replace"),
    }, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _cache_get(api_id, key, ttl_sec):
    """读有效缓存，返回 (status, headers_dict, body) 或 None。ttl_sec 单位为秒。"""
    row = one("SELECT * FROM api_cache WHERE api_id = ? AND cache_key = ?", (api_id, key))
    if not row or not ttl_sec:
        return None
    try:
        cached_at = datetime.strptime(row["cached_at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None
    if datetime.now(timezone.utc) - cached_at > timedelta(seconds=ttl_sec):
        return None  # 过期
    try:
        headers = json.loads(row["headers_json"] or "{}")
    except (ValueError, TypeError):
        headers = {}
    return row["status"], headers, row["body"]


def _cache_put(api_id, key, status, headers, body):
    """写入/替换缓存（新数据覆盖旧数据）。"""
    execute(
        "INSERT INTO api_cache (api_id, cache_key, status, headers_json, body, cached_at) VALUES (?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(api_id, cache_key) DO UPDATE SET status=excluded.status, headers_json=excluded.headers_json, "
        "body=excluded.body, cached_at=excluded.cached_at",
        (api_id, key, status, json.dumps(headers or {}, ensure_ascii=False), body, utcnow()),
    )

_TEMPLATE_TOKEN = re.compile(r"\{\{([A-Za-z_][A-Za-z0-9_]*)\}\}")


def _client_ip():
    """取调用方 IP：优先反代（Caddy）注入的 X-Forwarded-For 第一段，其次 X-Real-IP，最后直连地址。"""
    xff = request.headers.get("X-Forwarded-For", "")
    if xff:
        first = xff.split(",")[0].strip()
        if first:
            return first
    real = request.headers.get("X-Real-IP", "").strip()
    if real:
        return real
    return request.remote_addr or ""


def _extract_api_key():
    hk = request.headers.get("X-API-Key")
    auth = request.headers.get("Authorization", "")
    bearer = auth[7:].strip() if auth.lower().startswith("bearer ") else ""
    return hk or bearer or ""


def _authenticate():
    key = _extract_api_key()
    if not key:
        return None, None, "Missing API key"
    row = one("SELECT u.*, k.id AS key_id FROM users u JOIN api_keys k ON k.user_id = u.id WHERE k.key_hash = ?",
              (hash_api_key(key),))
    if not row:
        return None, None, "Invalid API key"
    user = {k: row[k] for k in row.keys() if k != "key_id"}
    g.current_key_id = row["key_id"]  # 供 log_request 记录使用的 API Key
    return user, key, None


def _compile_template(endpoint):
    parts, names, pos = [], [], 0
    for m in _TEMPLATE_TOKEN.finditer(endpoint):
        parts.append(re.escape(endpoint[pos:m.start()]))
        names.append(m.group(1))
        parts.append(r"(?P<" + m.group(1) + r">[^/]+)")
        pos = m.end()
    parts.append(re.escape(endpoint[pos:]))
    return re.compile("^" + "".join(parts) + "$"), names


def _match_api(user_id, method, path, api_type=None):
    sql = "SELECT * FROM apis WHERE user_id = ? AND method = ? AND enabled = 1"
    args = [user_id, method]
    if api_type:
        sql += " AND type = ?"
        args.append(api_type)
    for row in all_rows(sql, args):
        pattern, names = _compile_template(row["endpoint"])
        m = pattern.match(path)
        if m:
            return row, {k: v for k, v in m.groupdict().items() if v is not None}
    return None, None


def _check_rate(key_hash, limit):
    """跨进程限流，计数存 SQLite，所有 gunicorn worker 共享。"""
    return check_rate_limit(key_hash, limit)


def _fill(text, values, o_api_key=""):
    if not text:
        return ""
    def repl(m):
        k = m.group(1)
        if k == "o-api-key":
            return o_api_key
        return values.get(k, m.group(0))
    return PLACEHOLDER_RE.sub(repl, text)


def _pairs_to_dict(pairs, values, o_api_key=""):
    return {p["key"]: _fill(p["value"], values, o_api_key) for p in pairs if p.get("key")}


def _merge_pairs(preset, incoming, conflict):
    result = dict(preset)
    for k, v in incoming.items():
        if k in result:
            if conflict == "user":
                result[k] = v
            elif conflict == "reject":
                raise ValueError(f"Conflict parameter: {k}")
        else:
            result[k] = v
    return result


# 转发给源 API 时必须剥离的请求头：
# 1) hop-by-hop 与平台鉴权头（X-API-Key/Authorization 是本站凭据，绝不能给源 API）
# 2) 客户端 IP / 代理链隐私头：Caddy 等反代默认注入 X-Forwarded-For / X-Real-IP，
#    若原样转发会把调用方的真实 IP 泄露给源 API，违背中转站的隔离意义。
#    各类 CDN / 反代注入的 IP 头一并剥离。
STRIP_REQUEST_HEADERS = {
    "host", "content-length", "connection", "keep-alive",
    "proxy-authenticate", "proxy-authorization", "te", "trailer",
    "transfer-encoding", "upgrade", "accept-encoding",
    "x-api-key", "authorization", "cookie", "content-type",
    # 客户端 IP / 代理链
    "x-forwarded-for", "x-forwarded-proto", "x-forwarded-host",
    "x-forwarded-port", "x-forwarded-server", "x-real-ip", "forwarded",
    "x-client-ip", "x-original-forwarded-for", "cf-connecting-ip",
    "true-client-ip", "x-cluster-client-ip", "fastly-client-ip",
    "x-proxy-user-ip",
}


def _gateway(endpoint, api_type=None):
    """网关核心逻辑。api_type 非空时只匹配该类型 API（split 模式用）。"""
    started = time.monotonic()
    user, key, auth_err = _authenticate()
    if auth_err:
        return error(auth_err, 401)
    client_ip = _client_ip()

    api, vars_ = _match_api(user["id"], request.method, endpoint, api_type)
    if not api:
        log_request(user["id"], None, request.method, endpoint, 404, False, 0, "Endpoint not found", client_ip=client_ip)
        return error("Endpoint not found", 404)

    cfg = api_config(api)
    if not _check_rate(hash_api_key(key), cfg.get("rate_limit", 0)):
        log_request(user["id"], api["id"], request.method, endpoint, 429, False, 0, "Rate limited", client_ip=client_ip)
        return error("Rate limit exceeded", 429)

    # ---------- Proxy：实时转发 ----------
    o_api_key = cfg.get("original_key", "")
    values = dict(vars_)
    user_params = request.args.to_dict(flat=True)
    user_headers = {k: v for k, v in request.headers if k.lower() not in STRIP_REQUEST_HEADERS}
    user_cookies = request.cookies.to_dict()

    try:
        preset_params = _pairs_to_dict(cfg.get("params", []), values, o_api_key)
        preset_headers = _pairs_to_dict(cfg.get("headers", []), values, o_api_key)
        preset_cookies = _pairs_to_dict(cfg.get("cookies", []), values, o_api_key)
        params = _merge_pairs(preset_params, user_params, cfg.get("param_conflict", "user"))
        headers = _merge_pairs(preset_headers, user_headers, cfg.get("param_conflict", "user"))
        cookies = _merge_pairs(preset_cookies, user_cookies, cfg.get("param_conflict", "user"))
        # Accept-Encoding 控制：默认剥离（源 API 不压缩，避免 br 解压问题）；
        # pass-through 放行调用方原值；gzip-only 仅允许 gzip（httpx 可自动解压）。
        ae_mode = cfg.get("accept_encoding", "auto")
        incoming_ae = request.headers.get("Accept-Encoding", "")
        if ae_mode == "pass":
            if incoming_ae:
                headers["Accept-Encoding"] = incoming_ae
        elif ae_mode == "gzip":
            headers["Accept-Encoding"] = "gzip"
        else:  # auto：剥离
            headers.pop("Accept-Encoding", None)
    except ValueError as exc:
        log_request(user["id"], api["id"], request.method, endpoint, 400, False, 0, str(exc), client_ip=client_ip)
        return error(str(exc), 400)

    url = _fill(cfg.get("original_url", ""), values, o_api_key)
    method = cfg.get("original_method", "GET")
    # URL 自带的 query 与合并后的 Params 一起交给 httpx，防止 URL 里的占位符参数被覆盖丢失
    url, params = merge_url_params(url, params)

    body_mode = cfg.get("body_mode", "none")
    body, content = None, None
    if body_mode == "json":
        preset_body = _fill(cfg.get("body", ""), values, o_api_key)
        body = preset_body.encode("utf-8") if preset_body else None
        if body and "content-type" not in {k.lower() for k in headers}:
            headers["Content-Type"] = "application/json; charset=utf-8"
    elif body_mode == "text":
        body = _fill(cfg.get("body", ""), values, o_api_key).encode("utf-8")
    elif body_mode == "binary":
        content = request.get_data()

    if cfg.get("body_enabled", True):
        if request.data and content is None:
            content = request.get_data()
        if request.is_json and body_mode == "json":
            try:
                merged = json.loads(body.decode("utf-8")) if body else {}
                user_json = request.get_json(silent=True) or {}
                if isinstance(merged, dict) and isinstance(user_json, dict):
                    if cfg.get("param_conflict") == "reject":
                        for k in user_json:
                            if k in merged:
                                raise ValueError(f"Conflict body field: {k}")
                    merged.update(user_json)
                    content = json.dumps(merged, ensure_ascii=False).encode("utf-8")
            except ValueError as exc:
                log_request(user["id"], api["id"], request.method, endpoint, 400, False, 0, str(exc), client_ip=client_ip)
                return error(str(exc), 400)

    # ---- 缓存检查：命中则直接返回，不再请求源 API ----
    # 统一按秒计算：用户填的数值 × 单位系数（min=60，sec=1）
    cache_value = float(cfg.get("cache_time", 0) or 0)
    cache_sec = cache_value * 60 if cfg.get("cache_unit") == "min" else cache_value
    ck = _cache_key(api["id"], request.method, endpoint, params,
                    (content if content is not None else body))
    if cache_sec > 0:
        hit = _cache_get(api["id"], ck, cache_sec)
        if hit is not None:
            c_status, c_headers, c_body = hit
            log_request(user["id"], api["id"], request.method, endpoint, c_status, True, 0,
                        "cache hit", client_ip=client_ip)
            return Response(c_body, status=c_status, headers=c_headers)

    timeout = cfg.get("timeout", 10)
    retries = cfg.get("retry", 0)
    resp, last_exc = None, None
    for attempt in range(retries + 1):
        try:
            with httpx.Client(timeout=timeout, follow_redirects=False) as client:
                resp = client.request(
                    method, url, params=params, headers=headers, cookies=cookies,
                    content=(content if content is not None else body),
                )
            break
        except Exception as exc:
            last_exc = exc
            if attempt < retries:
                time.sleep(0.3 * (attempt + 1))

    duration = int((time.monotonic() - started) * 1000)
    if resp is None:
        log_request(user["id"], api["id"], request.method, endpoint, 502, False, duration, str(last_exc), client_ip=client_ip)
        log_request(user["id"], api["id"], method, url, 502, False, duration,
                    f"upstream error: {last_exc}", client_ip=server_egress_ip(), event_type="UPSTREAM_PROXY")
        return error(f"Upstream error: {last_exc}", 502)

    success = 200 <= resp.status_code < 400
    # 1) 用户调用日志（含调用方 IP）
    log_request(user["id"], api["id"], request.method, endpoint, resp.status_code, success, duration,
                client_ip=client_ip)
    # 2) 请求源 API 日志：只记 URL 路径（去掉 query，避免把 key/敏感参数写进库）
    upstream_path = url.split("?")[0]
    log_request(user["id"], api["id"], method, upstream_path, resp.status_code, success, duration,
                f"upstream via {endpoint}", client_ip=server_egress_ip(), event_type="UPSTREAM_PROXY")

    excluded = {"content-encoding", "transfer-encoding", "content-length", "connection"}
    resp_headers = ({k: v for k, v in resp.headers.items() if k.lower() not in excluded}
                    if cfg.get("passthrough", True)
                    else {"Content-Type": resp.headers.get("content-type", "application/octet-stream")})
    # 成功后写缓存（只缓存成功响应，覆盖同 key 旧缓存）
    if cache_sec > 0 and success:
        _cache_put(api["id"], ck, resp.status_code, resp_headers, resp.content)
    return Response(resp.content, status=resp.status_code, headers=resp_headers)


# ---------------- TRANS User 网关 ----------------
def _trans_gateway(endpoint):
    """TRANS User API：鉴权 → 参数映射 → 查数据表 → 返回数组。"""
    import time
    from flask import jsonify
    from ..db import log_request
    from .trans_user import execute_query
    started = time.monotonic()
    user, key, auth_err = _authenticate()
    if auth_err:
        return error(auth_err, 401)
    client_ip = _client_ip()
    # 匹配 trans_apis（endpoint 支持 {{占位符}}，此处简单精确匹配 + 模板匹配）
    from ..utils import PLACEHOLDER_RE
    rows = all_rows("SELECT * FROM trans_apis WHERE user_id = ? AND method = ? AND enabled = 1",
                    (user["id"], request.method))
    api, vars_ = None, {}
    for row in rows:
        pattern, names = _compile_template(row["endpoint"])
        m = pattern.match(endpoint)
        if m:
            api, vars_ = row, {k: v for k, v in m.groupdict().items() if v is not None}
            break
    if not api:
        log_request(user["id"], None, request.method, endpoint, 404, False, 0,
                    "Endpoint not found", client_ip=client_ip)
        return error("Endpoint not found", 404)
    # 限流
    if not _check_rate(hash_api_key(key), api["rate_limit"] or 0):
        log_request(user["id"], api["id"], request.method, endpoint, 429, False, 0,
                    "Rate limited", client_ip=client_ip)
        return error("Rate limit exceeded", 429)
    # 收集请求参数：query + body(JSON/form)
    params = dict(request.args)
    if request.is_json:
        body = request.get_json(silent=True) or {}
        if isinstance(body, dict):
            params.update(body)
    # 合并 URL 占位符变量
    params.update(vars_)
    try:
        result, err = execute_query(dict(api), params)
    except Exception as exc:
        result, err = [], str(exc)
    duration = int((time.monotonic() - started) * 1000)
    if err:
        log_request(user["id"], api["id"], request.method, endpoint, 500, False, duration,
                    err, client_ip=client_ip)
        return error(err, 500)
    log_request(user["id"], api["id"], request.method, endpoint, 200, True, duration,
                client_ip=client_ip)
    return Response(json.dumps(result, ensure_ascii=False), status=200, mimetype="application/json")


# ---------------- 按配置注册网关路由 ----------------
_METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE"]
if GATEWAY_MODE == "split":
    # /p/* 仅匹配 Proxy API，/t/* 走 TRANS User 查询
    bp.add_url_rule("/p/<path:endpoint>", "gateway_proxy",
                    lambda endpoint: _gateway(endpoint, "PROXY"), methods=_METHODS)
    bp.add_url_rule("/t/<path:endpoint>", "gateway_trans_user",
                    _trans_gateway, methods=_METHODS)
elif GATEWAY_MODE == "gate":
    bp.add_url_rule("/gate/<path:endpoint>", "gateway",
                    lambda endpoint: _gateway(endpoint, None), methods=_METHODS)
else:  # none
    bp.add_url_rule("/<path:endpoint>", "gateway",
                    lambda endpoint: _gateway(endpoint, None), methods=_METHODS)
