"""Transmission：定时拉取源 API、留存历史、按字段映射生成返回数据。"""
import json

import httpx

from ..db import all_rows, api_config, execute, log_request, one, utcnow
from ..utils import server_egress_ip


def _fill(text, values, o_api_key=""):
    from ..utils import PLACEHOLDER_RE
    if not text:
        return ""
    def repl(m):
        if m.group(1) == "o-api-key":
            return o_api_key
        return values.get(m.group(1), m.group(0))
    return PLACEHOLDER_RE.sub(repl, text)


def fetch_and_store(api_id):
    """被调度器调用：请求源 API 并把原始响应写入 transmission_data。"""
    row = one("SELECT * FROM apis WHERE id = ? AND type = 'TRANSMISSION' AND enabled = 1", (api_id,))
    if not row:
        return False
    cfg = api_config(row)
    o_api_key = cfg.get("original_key", "")
    values = {}
    params = {p["key"]: _fill(p["value"], values, o_api_key) for p in cfg.get("params", []) if p.get("key")}
    headers = {p["key"]: _fill(p["value"], values, o_api_key) for p in cfg.get("headers", []) if p.get("key")}
    cookies = {p["key"]: _fill(p["value"], values, o_api_key) for p in cfg.get("cookies", []) if p.get("key")}
    url = _fill(cfg.get("original_url", ""), values, o_api_key)
    method = cfg.get("original_method", "GET")
    upstream_path = url.split("?")[0]  # 去掉 query，避免把 key/敏感参数写进日志
    try:
        with httpx.Client(timeout=cfg.get("timeout", 10), follow_redirects=True) as client:
            resp = client.request(method, url, params=params, headers=headers, cookies=cookies)
        try:
            body = resp.json()
        except ValueError:
            body = resp.text
        execute(
            "INSERT INTO transmission_data (api_id, captured_at, payload_json, headers_json, cookies_json, raw_json) VALUES (?, ?, ?, ?, ?, ?)",
            (api_id, utcnow(), json.dumps(body, ensure_ascii=False), json.dumps(dict(resp.headers), ensure_ascii=False),
             json.dumps(dict(resp.cookies), ensure_ascii=False), json.dumps(resp.text, ensure_ascii=False)),
        )
        log_request(row["user_id"], api_id, method, upstream_path, resp.status_code,
                    200 <= resp.status_code < 400, 0, "transmission fetch",
                    client_ip=server_egress_ip(), event_type="UPSTREAM_TRANSMISSION")
        return True
    except Exception as exc:
        print(f"[APIMiao] transmission fetch failed {api_id}: {exc}")
        log_request(row["user_id"], api_id, method, upstream_path, 502, False, 0,
                    f"transmission fetch failed: {exc}", client_ip=server_egress_ip(), event_type="UPSTREAM_TRANSMISSION")
        return False


def _resolve_path(data, path):
    """按点号路径从 dict/list 中取值，如 data.items.0.name。"""
    cur = data
    for part in str(path).split("."):
        if isinstance(cur, dict):
            if part not in cur:
                return None
            cur = cur[part]
        elif isinstance(cur, list) and part.isdigit():
            idx = int(part)
            if idx >= len(cur):
                return None
            cur = cur[idx]
        else:
            return None
    return cur


def apply_mapping(payload, headers, cookies, mapping):
    """映射表：{目标字段: 源字段路径}。支持 'body.xxx'/'header.Xxx'/'cookie.xxx' 前缀。"""
    out = {}
    for target, source in (mapping or {}).items():
        section, _, path = str(source).partition(".")
        if section == "body":
            val = _resolve_path(payload, path)
        elif section == "header":
            val = _resolve_path(headers, path)
        elif section == "cookie":
            val = _resolve_path(cookies, path)
        else:
            val = _resolve_path(payload, str(source))
        out[target] = val
    return out


def current_payload(api_id):
    """返回 (映射后的最新数据, 状态码)。"""
    row = one("SELECT payload_json, headers_json, cookies_json FROM transmission_data WHERE api_id = ? ORDER BY captured_at DESC, id DESC LIMIT 1", (api_id,))
    if not row:
        return None, 503
    cfg = api_config(one("SELECT config FROM apis WHERE id = ?", (api_id,)))
    try:
        payload = json.loads(row["payload_json"] or "null")
        headers = json.loads(row["headers_json"] or "{}")
        cookies = json.loads(row["cookies_json"] or "{}")
    except (ValueError, TypeError):
        return None, 500
    return apply_mapping(payload, headers, cookies, cfg.get("mapping", {})), 200


def history(api_id, limit=100):
    rows = all_rows(
        "SELECT captured_at, payload_json FROM transmission_data WHERE api_id = ? ORDER BY captured_at DESC, id DESC LIMIT ?",
        (api_id, limit),
    )
    return [dict(r) for r in rows]
