"""TRANS Origin 的页面与 API 路由。"""
import json

from flask import redirect, render_template, request, session, url_for

from ..db import all_rows, execute, new_id, one, utcnow
from ..utils import error, ok, require_login
from . import bp
from . import trans_origin as to
from ..data_sheets.service import get_sheet

METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE")


def _number(value, cast, default, low, high):
    try:
        text = str(value).strip() if value is not None else ""
        return max(low, min(cast(text or default), high))
    except (TypeError, ValueError):
        return None


def _origin_config(row):
    """把 trans_origins 的 JSON 列解析成 dict，供表单使用。"""
    def load(key, default):
        try:
            return json.loads(row[key] or default)
        except (ValueError, TypeError, KeyError):
            return json.loads(default)
    return {
        "id": row["id"], "note": row["note"],
        "original_url": row["original_url"], "original_method": row["original_method"],
        "original_key": row["original_key"],
        "params": load("params_json", "[]"), "headers": load("headers_json", "[]"),
        "cookies": load("cookies_json", "[]"),
        "body_mode": row["body_mode"], "body": row["body"], "encoding": row["encoding"],
        "timeout": row["timeout"], "retry": row["retry"],
        "request_interval": row["request_interval"],
        "frequency": row["frequency"], "days": load("days_json", "[]"),
        "times": load("times_json", "[]"),
        "sheet_id": row["sheet_id"] if "sheet_id" in row.keys() else "",
        "result_path": row["result_path"] if "result_path" in row.keys() else "",
        "state": row["state"] if "state" in row.keys() else "DRAFT",
        "verified": row["verified"], "enabled": row["enabled"],
        "created_at": row["created_at"] if "created_at" in row.keys() else "",
    }


def _payload_config(data):
    """从请求 JSON 构造 trans_origins 各列的值。"""
    def dumps(v):
        return json.dumps(v or [], ensure_ascii=False)
    return {
        "note": str(data.get("note") or "").strip(),
        "original_url": str(data.get("original_url") or "").strip(),
        "original_method": str(data.get("original_method") or "GET").upper(),
        "original_key": str(data.get("original_key") or "").strip(),
        "params_json": dumps(data.get("params")), "headers_json": dumps(data.get("headers")),
        "cookies_json": dumps(data.get("cookies")),
        "body_mode": str(data.get("body_mode") or "none"),
        "body": str(data.get("body") or ""),
        "encoding": str(data.get("encoding") or "UTF-8"),
        "timeout": _number(data.get("timeout"), int, 10, 1, 120),
        "retry": _number(data.get("retry"), int, 0, 0, 5),
        "request_interval": _number(data.get("request_interval"), float, 0, 0, 3600),
        "frequency": str(data.get("frequency") or "DAY"),
        "days_json": dumps(data.get("days")), "times_json": dumps(data.get("times")),
        "sheet_id": str(data.get("sheet_id") or "").strip(),
        "result_path": str(data.get("result_path") or "result").strip(),
    }


def _validate(cfg):
    if any(cfg[k] is None for k in ("timeout", "retry", "request_interval")):
        return "Invalid numeric field"
    if not cfg["original_url"]:
        return "Original API URL is required"
    if cfg["original_method"] not in METHODS:
        return "Invalid Original API method"
    if not cfg["sheet_id"]:
        return "Data Sheet is required"
    sheet = one("SELECT name FROM data_sheets WHERE id = ? AND user_id = ?",
                (cfg["sheet_id"], session["uid"]))
    if not sheet:
        return "Data Sheet not found"
    # 时间格式 HH:mm
    for ts in json.loads(cfg["times_json"] or "[]"):
        try:
            h, m = str(ts).split(":")
            if not (0 <= int(h) <= 23 and 0 <= int(m) <= 59):
                return f"Invalid time: {ts}"
        except (ValueError, TypeError):
            return f"Invalid time: {ts}"
    # 列表解析 + 笛卡尔积上限校验
    try:
        pairs, err = to.normalize_pairs(json.loads(cfg["params_json"] or "[]"))
        if err:
            return err
        _, err = to.expand_combinations(pairs)
        if err:
            return err
    except (ValueError, TypeError) as exc:
        return f"Invalid params: {exc}"
    return None


def _sheet_field_config(user_id, sheet_id):
    sheet = get_sheet(user_id, sheet_id)
    if not sheet:
        return []
    return [
        {"name": field["key"], "enabled": field["enabled"], "order": field["order"]}
        for field in sheet["fields"]
    ]


# ---------------- 页面 ----------------
@bp.route("/panel/trans-origin")
def trans_origin_list():
    gate = require_login()
    if gate:
        return gate
    rows = all_rows("SELECT * FROM trans_origins WHERE user_id = ? ORDER BY created_at DESC", (session["uid"],))
    return render_template("trans_origin_list.html", origins=[_origin_config(r) for r in rows])


@bp.route("/panel/trans-origin/new")
def trans_origin_new():
    gate = require_login()
    if gate:
        return gate
    return render_template("trans_origin_form.html", origin=None, cfg={})


@bp.route("/panel/trans-origin/<origin_id>/edit")
def trans_origin_edit(origin_id):
    gate = require_login()
    if gate:
        return gate
    row = one("SELECT * FROM trans_origins WHERE id = ? AND user_id = ?", (origin_id, session["uid"]))
    if not row:
        return redirect(url_for("api.trans_origin_list"))
    cfg = _origin_config(row)
    # 字段配置由 Data Sheet 拥有
    cfg["fields"] = _sheet_field_config(session["uid"], cfg["sheet_id"])
    return render_template("trans_origin_form.html", origin=dict(row), cfg=cfg)


# ---------------- API ----------------
@bp.route("/api/trans-origins/fetch-preview", methods=["POST"])
def trans_origin_fetch_preview():
    gate = require_login()
    if gate:
        return gate
    data = request.get_json(silent=True) or {}
    cfg = _payload_config(data)
    cfg["fields"] = _sheet_field_config(session["uid"], cfg["sheet_id"])
    result = to.fetch_preview(cfg)
    return ok(**result)


@bp.route("/api/trans-origins/precheck", methods=["POST"])
def trans_origin_precheck():
    """Save 前的完整预检：跑一轮采集，全部成功则保存配置+数据（先验证后落地）。

    新建（无 id）：预检成功后落库配置和数据，保证创建完立刻有数据可用。
    编辑（有 id）：预检成功后更新配置；数据由本轮采集写入（覆盖式追加新批次）。
    force_save=1：应急通道，预检失败也只保存配置、不写数据、标记未验证。
    """
    gate = require_login()
    if gate:
        return gate
    data = request.get_json(silent=True) or {}
    force = bool(data.get("force_save"))
    cfg = _payload_config(data)
    err = _validate(cfg)
    if err and not force:
        return error(err, 400)
    cfg["fields"] = _sheet_field_config(session["uid"], cfg["sheet_id"])

    origin_id = data.get("id") or ""
    is_new = not origin_id
    if is_new:
        origin_id = new_id()
    elif not one("SELECT 1 FROM trans_origins WHERE id = ? AND user_id = ?",
                 (origin_id, session["uid"])):
        return error("Origin not found", 404)

    # 预检：只采集不落库（dry-run），失败时数据库完全不留痕迹
    collected = {"success": False, "total": 0, "failed": 0, "errors": [err] if err else [],
                 "items": [], "payloads": [], "field_names": []}
    if not err:
        origin = {"id": origin_id, "user_id": session["uid"], **cfg}
        collected = to.collect_batch(origin, cfg)

    if not collected["success"] and not force:
        # 失败：不保存配置、不写数据，让前端展示错误并阻止保存
        return ok(saved=False, verified=0, id="", success=False,
                  total=collected["total"], failed=collected["failed"],
                  errors=collected["errors"], batch_id="", sheet_id="")

    # 全成功（或 force_save 应急）：保存配置
    verified = 1 if collected["success"] else 0
    _save_origin(origin_id, session["uid"], cfg, verified, is_new)
    batch_info = {"batch_id": "", "sheet_id": ""}
    if collected["success"]:
        origin = {"id": origin_id, "user_id": session["uid"], **cfg}
        batch_info = to.commit_batch(origin, cfg, collected, triggered_by="PRECHECK")
    return ok(saved=True, verified=verified, id=origin_id, success=collected["success"],
              total=collected["total"], failed=collected["failed"],
              errors=collected["errors"], **batch_info)


def _save_origin(origin_id, user_id, cfg, verified, is_new):
    """插入或更新 trans_origins 记录。"""
    sheet = one("SELECT name FROM data_sheets WHERE id = ? AND user_id = ?",
                (cfg["sheet_id"], user_id))
    if not sheet:
        raise ValueError("Data Sheet not found")
    cols = ("note", "original_url", "original_method", "original_key", "params_json",
            "headers_json", "cookies_json", "body_mode", "body", "encoding", "timeout",
            "retry", "request_interval", "frequency", "days_json", "times_json",
            "sheet_id", "result_path", "state")
    vals = [cfg[c] for c in cols[:-1]] + ["VERIFIED" if verified else "DRAFT"]
    legacy_name = sheet["name"]
    if is_new:
        sql = ("INSERT INTO trans_origins (id, user_id, " + ", ".join(cols) +
               ", sheet_name, verified, enabled, created_at) VALUES (" +
               ", ".join(["?"] * (len(cols) + 6)) + ")")
        execute(sql, (origin_id, user_id, *vals, legacy_name, verified, 1, utcnow()))
    else:
        sql = ("UPDATE trans_origins SET " + ", ".join(f"{c}=?" for c in cols) +
               ", sheet_name=?, verified=?, enabled=1 WHERE id=? AND user_id=?")
        execute(sql, (*vals, legacy_name, verified, origin_id, user_id))


@bp.route("/api/trans-origins", methods=["POST"])
def trans_origin_create():
    gate = require_login()
    if gate:
        return gate
    data = request.get_json(silent=True) or {}
    cfg = _payload_config(data)
    err = _validate(cfg)
    if err:
        return error(err, 400)
    origin_id = new_id()
    verified = 1 if data.get("verified") else 0
    _save_origin(origin_id, session["uid"], cfg, verified, True)
    return ok(id=origin_id)


@bp.route("/api/trans-origins/<origin_id>", methods=["PUT"])
def trans_origin_update(origin_id):
    gate = require_login()
    if gate:
        return gate
    row = one("SELECT * FROM trans_origins WHERE id = ? AND user_id = ?", (origin_id, session["uid"]))
    if not row:
        return error("Origin not found", 404)
    data = request.get_json(silent=True) or {}
    cfg = _payload_config(data)
    err = _validate(cfg)
    if err:
        return error(err, 400)
    verified = 1 if data.get("verified") else row["verified"]
    _save_origin(origin_id, session["uid"], cfg, verified, False)
    return ok()


@bp.route("/api/trans-origins/<origin_id>", methods=["DELETE"])
def trans_origin_delete(origin_id):
    gate = require_login()
    if gate:
        return gate
    row = one("SELECT 1 FROM trans_origins WHERE id = ? AND user_id = ?", (origin_id, session["uid"]))
    if not row:
        return error("Origin not found", 404)
    execute("DELETE FROM trans_origins WHERE id = ? AND user_id = ?", (origin_id, session["uid"]))
    return ok()


@bp.route("/api/trans-origins/<origin_id>/fetch-now", methods=["POST"])
def trans_origin_fetch_now(origin_id):
    """手动触发一轮采集。"""
    gate = require_login()
    if gate:
        return gate
    row = one("SELECT * FROM trans_origins WHERE id = ? AND user_id = ?", (origin_id, session["uid"]))
    if not row:
        return error("Origin not found", 404)
    result = to.run_batch(dict(row), triggered_by="MANUAL")
    return ok(**result)
