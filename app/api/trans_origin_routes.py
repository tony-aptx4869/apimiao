"""TRANS Origin 的页面与 API 路由。"""
import json

from flask import redirect, render_template, request, session, url_for

from ..db import all_rows, execute, new_id, one, utcnow
from ..utils import error, ok, require_login
from . import bp
from . import trans_origin as to

METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE")


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
        "times": load("times_json", "[]"), "sheet_name": row["sheet_name"],
        "verified": row["verified"], "enabled": row["enabled"],
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
        "timeout": int(data.get("timeout") or 10), "retry": int(data.get("retry") or 0),
        "request_interval": float(data.get("request_interval") or 0),
        "frequency": str(data.get("frequency") or "DAY"),
        "days_json": dumps(data.get("days")), "times_json": dumps(data.get("times")),
        "sheet_name": str(data.get("sheet_name") or "").strip(),
    }


def _validate(cfg):
    if not cfg["original_url"]:
        return "Original API URL is required"
    if cfg["original_method"] not in METHODS:
        return "Invalid Original API method"
    if not cfg["sheet_name"]:
        return "Data Sheet Name is required"
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
    # 恢复 Data 区域字段配置
    sheet = one("SELECT fields_json FROM data_sheets WHERE user_id = ? AND name = ?",
                (session["uid"], row["sheet_name"]))
    if sheet:
        try:
            cfg["fields"] = json.loads(sheet["fields_json"] or "[]")
        except (ValueError, TypeError):
            cfg["fields"] = []
    return render_template("trans_origin_form.html", origin=dict(row), cfg=cfg)


# ---------------- API ----------------
@bp.route("/api/trans-origins/fetch-preview", methods=["POST"])
def trans_origin_fetch_preview():
    gate = require_login()
    if gate:
        return gate
    data = request.get_json(silent=True) or {}
    cfg = _payload_config(data)
    # 字段配置（Data 区域用户设定的保存项）
    cfg["fields_json"] = json.dumps(data.get("fields") or [], ensure_ascii=False)
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
    fields = data.get("fields") or []
    cfg["fields_json"] = json.dumps(fields, ensure_ascii=False)

    origin_id = data.get("id") or ""
    is_new = not origin_id
    if is_new:
        origin_id = new_id()

    # 预检：只采集不落库（dry-run），失败时数据库完全不留痕迹
    collected = {"success": False, "total": 0, "failed": 0, "errors": [err] if err else [],
                 "items": [], "payloads": [], "field_names": []}
    if not err:
        origin = {"id": origin_id, "user_id": session["uid"], "fields_json": cfg["fields_json"], **cfg}
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
        origin = {"id": origin_id, "user_id": session["uid"], "fields_json": cfg["fields_json"], **cfg}
        batch_info = to.commit_batch(origin, cfg, collected, triggered_by="PRECHECK")
    else:
        # 应急：只建数据表结构，不写数据
        to.ensure_sheet(session["uid"], origin_id, cfg["sheet_name"],
                        [f["name"] for f in fields if f.get("enabled", True)])
    return ok(saved=True, verified=verified, id=origin_id, success=collected["success"],
              total=collected["total"], failed=collected["failed"],
              errors=collected["errors"], **batch_info)


def _save_origin(origin_id, user_id, cfg, verified, is_new):
    """插入或更新 trans_origins 记录。"""
    cols = ("note", "original_url", "original_method", "original_key", "params_json",
            "headers_json", "cookies_json", "body_mode", "body", "encoding", "timeout",
            "retry", "request_interval", "frequency", "days_json", "times_json", "sheet_name")
    vals = [cfg[c] for c in cols]
    if is_new:
        sql = ("INSERT INTO trans_origins (id, user_id, " + ", ".join(cols) +
               ", verified, enabled, created_at) VALUES (" + ", ".join(["?"] * (len(cols) + 5)) + ")")
        execute(sql, (origin_id, user_id, *vals, verified, 1, utcnow()))
    else:
        sql = "UPDATE trans_origins SET " + ", ".join(f"{c}=?" for c in cols) + ", verified=? WHERE id=? AND user_id=?"
        execute(sql, (*vals, verified, origin_id, user_id))


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
    execute(
        "INSERT INTO trans_origins (id, user_id, note, original_url, original_method, original_key, "
        "params_json, headers_json, cookies_json, body_mode, body, encoding, timeout, retry, "
        "request_interval, frequency, days_json, times_json, sheet_name, verified, enabled, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)",
        (origin_id, session["uid"], cfg["note"], cfg["original_url"], cfg["original_method"],
         cfg["original_key"], cfg["params_json"], cfg["headers_json"], cfg["cookies_json"],
         cfg["body_mode"], cfg["body"], cfg["encoding"], cfg["timeout"], cfg["retry"],
         cfg["request_interval"], cfg["frequency"], cfg["days_json"], cfg["times_json"],
         cfg["sheet_name"], verified, utcnow()),
    )
    # 创建数据表（若预检已采到数据，run_batch 预检 save_data=False 不建表；这里按默认字段建）
    fields = data.get("fields") or []
    if not fields:
        preview = to.fetch_preview(cfg)
        fields = [{"name": n, "enabled": True, "order": i} for i, n in enumerate(preview.get("fields", []))]
    to.ensure_sheet(session["uid"], origin_id, cfg["sheet_name"],
                    [f["name"] for f in fields if f.get("enabled", True)])
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
    execute(
        "UPDATE trans_origins SET note=?, original_url=?, original_method=?, original_key=?, "
        "params_json=?, headers_json=?, cookies_json=?, body_mode=?, body=?, encoding=?, "
        "timeout=?, retry=?, request_interval=?, frequency=?, days_json=?, times_json=?, "
        "sheet_name=?, verified=? WHERE id=? AND user_id=?",
        (cfg["note"], cfg["original_url"], cfg["original_method"], cfg["original_key"],
         cfg["params_json"], cfg["headers_json"], cfg["cookies_json"], cfg["body_mode"],
         cfg["body"], cfg["encoding"], cfg["timeout"], cfg["retry"], cfg["request_interval"],
         cfg["frequency"], cfg["days_json"], cfg["times_json"], cfg["sheet_name"],
         verified, origin_id, session["uid"]),
    )
    fields = data.get("fields")
    if fields is not None:
        to.ensure_sheet(session["uid"], origin_id, cfg["sheet_name"],
                        [f["name"] for f in fields if f.get("enabled", True)])
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
