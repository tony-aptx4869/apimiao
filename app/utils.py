"""通用工具：密钥、占位符、时间、校验、响应封装。"""
import hashlib
import re
import uuid
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qsl, urlsplit, urlunsplit

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


def merge_url_params(url: str, extra_params: dict) -> tuple:
    """把 URL 里自带的 query 与额外参数合并，返回 (不含query的base_url, 合并后的params)。

    httpx 在 URL 已带 query 时传入 params 会覆盖原 query，导致 URL 里的参数丢失。
    先拆出原 query 合并进 params，再交给 httpx，可保留两边所有参数。
    """
    u = urlsplit(url)
    merged = dict(parse_qsl(u.query, keep_blank_values=True))
    merged.update(extra_params or {})
    base = urlunsplit((u.scheme, u.netloc, u.path, "", ""))
    return base, merged


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


# 统计分组用的本地时区（来自 config.ini [general] timezone，默认 Asia/Shanghai）。
# 库里统一存 UTC，图表/每日统计按配置的时区归到"本地日"。
from zoneinfo import ZoneInfo
from config import TIMEZONE as _TIMEZONE_NAME

try:
    LOCAL_TZ = ZoneInfo(_TIMEZONE_NAME)
except Exception:
    LOCAL_TZ = timezone(timedelta(hours=8))  # 配置无效时兜底北京时间


def local_day(iso: str) -> str:
    """把 UTC ISO 时间按配置的本地时区转成 YYYY-MM-DD，用于每日统计分组。"""
    try:
        return utc_dt(iso).astimezone(LOCAL_TZ).strftime("%Y-%m-%d")
    except (ValueError, TypeError):
        return (iso or "")[:10]


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


def _live_user():
    """按 session 中的 uid 读取当前用户，并同步角色与邮箱。"""
    uid = session.get("uid")
    if not uid:
        return None
    from .db import one
    user = one("SELECT * FROM users WHERE id = ?", (uid,))
    if not user:
        session.clear()
        return None
    if session.get("role") != user["role"]:
        session["role"] = user["role"]
    if session.get("email") != user["email"]:
        session["email"] = user["email"]
    return user


def require_login():
    user = _live_user()
    if not user:
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
