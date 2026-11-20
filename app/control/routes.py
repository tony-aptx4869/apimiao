"""Control Panel 路由。"""
import json
from datetime import timedelta

from flask import redirect, render_template, request, session, url_for

from ..db import all_rows, one, execute, utcnow
from ..utils import cutoff_iso, error, ok, require_login, require_admin, gen_api_key, hash_api_key
from config import endpoint_prefix
from . import bp


@bp.before_request
def _gate():
    if session.get("force_pwd") and request.endpoint not in ("control.profile",):
        return redirect(url_for("control.profile") + "?force=1")


@bp.route("/panel")
@bp.route("/panel/overview")
def overview():
    gate = require_login()
    if gate:
        return gate
    uid = session["uid"]
    windows = {
        "h1": cutoff_iso(timedelta(hours=1)),
        "h24": cutoff_iso(timedelta(hours=24)),
        "d7": cutoff_iso(timedelta(days=7)),
        "d30": cutoff_iso(timedelta(days=30)),
    }
    counts = {}          # 用户调用（GATEWAY）
    original_counts = {} # 源 API 调用（UPSTREAM_PROXY / UPSTREAM_TRANSMISSION）
    for key, since in windows.items():
        counts[key] = one(
            "SELECT COUNT(*) AS n FROM request_logs WHERE user_id = ? AND created_at >= ? "
            "AND COALESCE(event_type, 'GATEWAY') = 'GATEWAY'", (uid, since)
        )["n"]
        original_counts[key] = one(
            "SELECT COUNT(*) AS n FROM request_logs WHERE user_id = ? AND created_at >= ? "
            "AND event_type IN ('UPSTREAM_PROXY', 'UPSTREAM_TRANSMISSION')", (uid, since)
        )["n"]
    key_count = one("SELECT COUNT(*) AS n FROM api_keys WHERE user_id = ?", (uid,))["n"]
    # 近 30 天每日用户调用量（供概览页 Daily Usage 图表）
    since30 = windows["d30"]
    daily_rows = all_rows(
        "SELECT created_at FROM request_logs WHERE user_id = ? AND created_at >= ? "
        "AND COALESCE(event_type, 'GATEWAY') = 'GATEWAY'", (uid, since30),
    )
    daily_map = {}
    for r in daily_rows:
        day = r["created_at"][:10]  # YYYY-MM-DD
        daily_map[day] = daily_map.get(day, 0) + 1
    daily_days = sorted(daily_map)
    return render_template("overview.html", counts=counts, original_counts=original_counts,
                           key_count=key_count, daily_days=daily_days,
                           daily_values=[daily_map[d] for d in daily_days])


@bp.route("/panel/api-keys")
def api_keys_page():
    gate = require_login()
    if gate:
        return gate
    rows = all_rows("SELECT id, name, prefix, suffix, created_at FROM api_keys WHERE user_id = ? ORDER BY created_at DESC", (session["uid"],))
    return render_template("api_keys.html", keys=[dict(r) for r in rows])


@bp.route("/api/api-keys", methods=["POST"])
def api_key_create():
    gate = require_login()
    if gate:
        return gate
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip() or "Untitled"
    key = gen_api_key()
    execute(
        "INSERT INTO api_keys (user_id, name, key_hash, prefix, suffix, created_at) VALUES (?, ?, ?, ?, ?, ?)",
        (session["uid"], name, hash_api_key(key), key[:4], key[-4:], utcnow()),
    )
    return ok(key=key)


@bp.route("/api/api-keys/<int:key_id>/rename", methods=["POST"])
def api_key_rename(key_id):
    gate = require_login()
    if gate:
        return gate
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    if not name:
        return error("Name required", 400)
    execute("UPDATE api_keys SET name = ? WHERE id = ? AND user_id = ?", (name, key_id, session["uid"]))
    return ok()


@bp.route("/api/api-keys/<int:key_id>", methods=["DELETE"])
def api_key_delete(key_id):
    gate = require_login()
    if gate:
        return gate
    execute("DELETE FROM api_keys WHERE id = ? AND user_id = ?", (key_id, session["uid"]))
    return ok()


@bp.route("/panel/logs")
def logs_page():
    gate = require_login()
    if gate:
        return gate
    return render_template("logs.html")


@bp.route("/api/logs/query")
def logs_query():
    gate = require_login()
    if gate:
        return gate
    since = cutoff_iso(timedelta(days=30))
    all_logs = all_rows(
        "SELECT method, endpoint, status, success, created_at, client_ip, event_type, api_id "
        "FROM request_logs WHERE user_id = ? AND created_at >= ?",
        (session["uid"], since),
    )
    # api_id -> API 类型，用于给 Endpoint 加 p/ / t/ 前缀（日志只存了不含前缀的 endpoint）
    api_types = {r["id"]: r["type"] for r in all_rows(
        "SELECT id, type FROM apis WHERE user_id = ?", (session["uid"],))}

    def display_endpoint(r):
        """用户调用日志的 Endpoint 加上网关前缀，还原实际调用路径。"""
        ep = r["endpoint"] or ""
        if not ep:
            return ep
        # 已是完整路径（上游日志的 URL）或已带前缀则不重复添加
        if ep.startswith(("http://", "https://")):
            return ep
        parts = ep.split("/", 1)
        first = parts[0]
        if first in ("p", "t", "gate") and len(parts) > 1:
            return ep
        t = api_types.get(r["api_id"])
        if t:
            pre = endpoint_prefix(t)
            if pre:
                return pre + "/" + ep
        return ep

    # 统计只算用户调用网关（GATEWAY），上游请求单独展示，避免重复计数
    gateway_logs = [r for r in all_logs if (r["event_type"] or "GATEWAY") == "GATEWAY"]

    method = (request.args.get("method") or "").upper()
    status = request.args.get("status") or ""

    def match(r):
        if method and method != "ALL" and r["method"] != method:
            return False
        if status and status != "ALL":
            if status == "SUCCESS" and not r["success"]:
                return False
            if status == "FAILED" and r["success"]:
                return False
            if status.isdigit() and r["status"] != int(status):
                return False
        return True

    rows = [r for r in gateway_logs if match(r)]
    total = len(rows)
    success = sum(1 for r in rows if r["success"])
    failed = total - success

    daily = {}
    for r in rows:
        day = r["created_at"][:10]  # YYYY-MM-DD
        daily[day] = daily.get(day, 0) + 1
    days = sorted(daily)

    counts = {}
    for r in rows:
        ep = display_endpoint(r)
        counts[ep] = counts.get(ep, 0) + 1
    top = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:9]

    def to_item(r):
        return {
            "method": r["method"], "endpoint": display_endpoint(r), "status": r["status"],
            "success": bool(r["success"]), "datetime": r["created_at"],
            "client_ip": r["client_ip"] or "-", "event_type": r["event_type"] or "GATEWAY",
        }

    history = [to_item(r) for r in sorted(all_logs, key=lambda r: r["created_at"], reverse=True)[:200]]
    upstream = [to_item(r) for r in sorted(
        [x for x in all_logs if (x["event_type"] or "GATEWAY") != "GATEWAY"],
        key=lambda r: r["created_at"], reverse=True,
    )[:200]]
    return ok(total=total, success=success, failed=failed, days=days,
              daily=[daily[d] for d in days], top=[{"endpoint": k, "count": v} for k, v in top],
              history=history, upstream=upstream)


@bp.route("/panel/users")
def users_page():
    gate = require_admin()
    if gate:
        return gate
    return render_template("users.html")


@bp.route("/panel/profile")
def profile():
    gate = require_login()
    if gate:
        return gate
    user = one("SELECT * FROM users WHERE id = ?", (session["uid"],))
    passkeys = all_rows("SELECT id, name, platform, created_at FROM passkeys WHERE user_id = ? ORDER BY created_at DESC", (session["uid"],))
    return render_template("profile.html", user=user, passkeys=[dict(r) for r in passkeys],
                           force=request.args.get("force") == "1")
