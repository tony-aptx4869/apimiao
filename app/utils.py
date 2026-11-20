"""通用工具：密钥、占位符、时间、校验、响应封装。"""
import hashlib
import re
import uuid
from datetime import datetime, timedelta, timezone

from flask import jsonify, redirect, request, session, url_for

# 占位符名允许字母/数字/下划线/横杠（横杠用于内置 {{o-api-key}}）
PLACEHOLDER_RE = re.compile(r"\{\{([A-Za-z_][A-Za-z0-9_-]*)\}\}")
ENDPOINT_RE = re.compile(r"^[A-Za-z][A-Za-z0-9\-/{}]*$")


def gen_api_key() -> str:
    return str(uuid.uuid4())


def hash_api_key(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def hash_recovery_code(code: str) -> str:
    """恢复码规范化（去掉连字符、转大写）后哈希。"""
    normalized = code.replace("-", "").strip().upper()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def gen_recovery_codes(count=10) -> list:
    """生成人类可读的恢复码，形如 XXXX-XXXX-XXXX（去掉易混淆字符）。"""
    import secrets
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # 去掉 O/0/I/1 等易混淆
    codes = []
    for _ in range(count):
        raw = "".join(secrets.choice(alphabet) for _ in range(12))
        codes.append(f"{raw[:4]}-{raw[4:8]}-{raw[8:]}")
    return codes


def mask_api_key(prefix: str, suffix: str) -> str:
    return f"{prefix}{'•' * 9}{suffix}"


_egress_ip_cache = None


def server_egress_ip() -> str:
    """返回服务器公网出口 IP（源 API 实际看到的来源），结果缓存。

    优先用 config.ini 的 server.egress_ip；否则用 UDP connect 探测本机出口 IP
    （不真正发包）。探测失败返回空串。
    """
    global _egress_ip_cache
    if _egress_ip_cache is not None:
        return _egress_ip_cache
    from config import EGRESS_IP
    if EGRESS_IP:
        _egress_ip_cache = EGRESS_IP
        return _egress_ip_cache
    try:
        import socket
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("8.8.8.8", 80))  # 仅选路，不发送数据
            _egress_ip_cache = s.getsockname()[0]
        finally:
            s.close()
    except Exception:
        _egress_ip_cache = ""
    return _egress_ip_cache


def placeholders(text: str) -> set:
    return set(PLACEHOLDER_RE.findall(text or ""))


def valid_endpoint(endpoint: str) -> bool:
    if not endpoint or not ENDPOINT_RE.match(endpoint):
        return False
    i = 0
    while i < len(endpoint):
        if endpoint[i] == "{":
            if not endpoint.startswith("{{", i):
                return False
            end = endpoint.find("}}", i + 2)
            if end == -1 or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", endpoint[i + 2:end]):
                return False
            i = end + 2
        elif endpoint[i] == "}":
            return False
        else:
            i += 1
    return True


def utc_dt(iso: str) -> datetime:
    return datetime.strptime(iso, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def cutoff_iso(delta: timedelta) -> str:
    return (datetime.now(timezone.utc) - delta).strftime("%Y-%m-%dT%H:%M:%SZ")


def json_response(data, status=200):
    resp = jsonify(data)
    resp.status_code = status
    return resp


def error(message: str, status=400):
    return json_response({"ok": False, "error": message}, status)


def ok(**kwargs):
    payload = {"ok": True}
    payload.update(kwargs)
    return json_response(payload)


def is_admin() -> bool:
    return session.get("role") == "ADMIN"


def require_login():
    if not session.get("uid"):
        if request.is_json or request.path.startswith("/api/"):
            return error("Unauthorized", 401)
        return redirect(url_for("auth.login"))
    return None


def require_admin():
    gate = require_login()
    if gate:
        return gate
    if not is_admin():
        return error("Forbidden", 403) if (request.is_json or request.path.startswith("/api/")) else redirect(url_for("control.overview"))
    return None
