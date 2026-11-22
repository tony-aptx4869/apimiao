"""TRANS Origin 的页面与 API 路由。"""
import json
import threading
import time
from datetime import datetime, timedelta, timezone

from flask import current_app, redirect, render_template, request, session, url_for

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
        "batch_retry_rounds": row["batch_retry_rounds"] if "batch_retry_rounds" in row.keys() else 2,
        "request_interval": row["request_interval"],
        "frequency": row["frequency"], "days": load("days_json", "[]"),
        "times": load("times_json", "[]"),
        "sheet_id": row["sheet_id"] if "sheet_id" in row.keys() else "",
        "result_path": row["result_path"] if "result_path" in row.keys() else "",
        "state": row["state"] if "state" in row.keys() else "DRAFT",
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
        "batch_retry_rounds": _number(data.get("batch_retry_rounds"), int, 2, 0, 5),
        "request_interval": _number(data.get("request_interval"), float, 0, 0, 3600),
        "frequency": str(data.get("frequency") or "DAY"),
        "days_json": dumps(data.get("days")), "times_json": dumps(data.get("times")),
        "sheet_id": str(data.get("sheet_id") or "").strip(),
        "result_path": "",
    }


def _validate(cfg):
    if any(cfg[k] is None for k in ("timeout", "retry", "batch_retry_rounds", "request_interval")):
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


def _prepare_precheck(data, route_origin_id=""):
    cfg = _payload_config(data)
    err = _validate(cfg)
    if err:
        return None, "", False, err, 400
    cfg["fields"] = _sheet_field_config(session["uid"], cfg["sheet_id"])

    body_id = data.get("id") or ""
    if route_origin_id:
        origin_id = route_origin_id
        is_new = False
    elif body_id:
        return None, "", False, "Use the edit precheck endpoint", 400
    else:
        duplicate = one(
            "SELECT id FROM trans_origins WHERE user_id = ? "
            "AND original_url = ? AND original_method = ? AND original_key = ? "
            "AND params_json = ? AND headers_json = ? AND cookies_json = ? "
            "AND body_mode = ? AND body = ? AND encoding = ? "
            "AND timeout = ? AND retry = ? AND batch_retry_rounds = ? "
            "AND request_interval = ? AND frequency = ? AND days_json = ? "
            "AND times_json = ? AND sheet_id = ?",
            (session["uid"], cfg["original_url"], cfg["original_method"], cfg["original_key"],
             cfg["params_json"], cfg["headers_json"], cfg["cookies_json"],
             cfg["body_mode"], cfg["body"], cfg["encoding"], cfg["timeout"], cfg["retry"],
             cfg["batch_retry_rounds"], cfg["request_interval"], cfg["frequency"],
             cfg["days_json"], cfg["times_json"], cfg["sheet_id"]),
        )
        if duplicate:
            return None, "", False, "Equivalent TRANS Origin already exists", 400
        origin_id = new_id()
        is_new = True

    if not is_new and not one(
        "SELECT 1 FROM trans_origins WHERE id = ? AND user_id = ?", (origin_id, session["uid"])
    ):
        return None, "", False, "Origin not found", 404
    return cfg, origin_id, is_new, "", 200


def _task_public(row):
    try:
        state = json.loads(row["state_json"] or "{}")
    except (TypeError, ValueError):
        state = {}
    return {
        "id": row["id"], "origin_id": row["origin_id"], "status": row["status"],
        "total": row["total"], "done": row["done"], "failed": row["failed"],
        "round": row["round"], "max_rounds": row["max_rounds"],
        "can_retry": row["status"] == "AWAITING_USER" and row["round"] < row["max_rounds"],
        "next_retry_at": row["next_retry_at"], "current": row["current"],
        "error": row["error"], "items": state.get("items", []),
        "errors": state.get("errors", []), "warnings": state.get("warnings", []),
        "created_at": row["created_at"], "updated_at": row["updated_at"],
    }


def _update_task(task_id, **fields):
    if "state" in fields:
        fields["state_json"] = json.dumps(fields.pop("state"), ensure_ascii=False)
    if not fields:
        return
    fields["updated_at"] = utcnow()
    sql = "UPDATE precheck_tasks SET " + ", ".join(f"{key} = ?" for key in fields) + " WHERE id = ?"
    execute(sql, (*fields.values(), task_id))


def _launch_precheck_worker(task_id, wait_sec=0):
    app = current_app._get_current_object()

    def worker():
        if wait_sec > 0:
            time.sleep(wait_sec)
        with app.app_context():
            _run_precheck_round(task_id)

    threading.Thread(target=worker, daemon=True).start()


def _run_precheck_round(task_id):
    row = one("SELECT * FROM precheck_tasks WHERE id = ?", (task_id,))
    if not row or row["status"] != "RUNNING":
        return
    try:
        cfg = json.loads(row["payload_json"] or "{}")
        state = json.loads(row["state_json"] or "{}")
        failed_combos = state.get("failed_combos") or None
        origin = {"id": row["origin_id"], "user_id": row["user_id"], **cfg}
        current_round = row["round"]
        collected = to.collect_batch(origin, cfg, combos_override=failed_combos)
        for item in collected["items"]:
            item["round"] = current_round
        state["payloads"] = state.get("payloads", []) + collected["payloads"]
        state["items"] = state.get("items", []) + collected["items"]
        state["errors"] = state.get("errors", []) + collected["errors"]
        state["warnings"] = state.get("warnings", []) + collected["warnings"]
        failed = [item["combo"] for item in collected["items"] if not item["success"]]
        state["failed_combos"] = failed

        if not failed and collected["success"]:
            merged = dict(collected)
            merged["payloads"] = state["payloads"]
            merged["total"] = row["total"]
            merged["failed"] = 0
            _save_origin(row["origin_id"], row["user_id"], cfg, 1, bool(row["is_new"]))
            to.commit_batch(origin, cfg, merged, triggered_by="PRECHECK")
            _update_task(
                task_id, status="SUCCEEDED", done=row["total"], failed=0,
                current="", next_retry_at="", error="", state=state,
            )
            return

        first = failed[0] if failed else {}
        _update_task(
            task_id, status="AWAITING_USER", done=max(0, row["total"] - len(failed)),
            failed=len(failed), current=json.dumps(first, ensure_ascii=False),
            next_retry_at="", error="Upstream requests failed", state=state,
        )
    except Exception as exc:
        _update_task(task_id, status="AWAITING_USER", error=str(exc), next_retry_at="")


def _sheet_field_config(user_id, sheet_id):
    sheet = get_sheet(user_id, sheet_id)
    if not sheet:
        return []
    return [
        {"name": field["key"], "json_type": field["json_type"], "enabled": field["enabled"], "order": field["order"]}
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


def _run_origin_precheck(route_origin_id=""):
    """Save 前的完整预检。编辑身份由路由 ID 持有，不信任请求体 ID。"""
    gate = require_login()
    if gate:
        return gate
    data = request.get_json(silent=True) or {}
    force = bool(data.get("force_save"))
    cfg = _payload_config(data)
    err = _validate(cfg)
    if err:
        return error(err, 400)
    cfg["fields"] = _sheet_field_config(session["uid"], cfg["sheet_id"])

    body_id = data.get("id") or ""
    if route_origin_id:
        origin_id = route_origin_id
        is_new = False
    elif body_id:
        return error("Use the edit precheck endpoint", 400)
    else:
        origin_id = ""
        is_new = True

    if is_new:
        duplicate = one(
            "SELECT id FROM trans_origins WHERE user_id = ? "
            "AND original_url = ? AND original_method = ? AND original_key = ? "
            "AND params_json = ? AND headers_json = ? AND cookies_json = ? "
            "AND body_mode = ? AND body = ? AND encoding = ? "
            "AND timeout = ? AND retry = ? AND request_interval = ? "
            "AND frequency = ? AND days_json = ? AND times_json = ? AND sheet_id = ?",
            (session["uid"], cfg["original_url"], cfg["original_method"], cfg["original_key"],
             cfg["params_json"], cfg["headers_json"], cfg["cookies_json"],
             cfg["body_mode"], cfg["body"], cfg["encoding"], cfg["timeout"], cfg["retry"],
             cfg["request_interval"], cfg["frequency"], cfg["days_json"], cfg["times_json"],
             cfg["sheet_id"]),
        )
        if duplicate:
            return error("Equivalent TRANS Origin already exists", 400)
        origin_id = new_id()
    elif not one("SELECT 1 FROM trans_origins WHERE id = ? AND user_id = ?",
                 (origin_id, session["uid"])):
        return error("Origin not found", 404)

    # 预检：只采集不落库（dry-run），失败时数据库完全不留痕迹
    collected = {"success": False, "total": 0, "failed": 0, "errors": [err] if err else [],
                 "warnings": [], "items": [], "payloads": [], "field_names": []}
    if not err:
        origin = {"id": origin_id, "user_id": session["uid"], **cfg}
        collected = to.collect_batch(origin, cfg)

    if not collected["success"] and not force:
        # 失败：不保存配置、不写数据，让前端展示错误并阻止保存
        return ok(saved=False, verified=0, id="", success=False,
                  total=collected["total"], failed=collected["failed"],
                  errors=collected["errors"], warnings=collected["warnings"],
                  batch_id="", sheet_id="")

    # 全成功（或 force_save 应急）：保存配置
    verified = 1 if collected["success"] else 0
    _save_origin(origin_id, session["uid"], cfg, verified, is_new)
    batch_info = {"batch_id": "", "sheet_id": ""}
    if collected["success"]:
        origin = {"id": origin_id, "user_id": session["uid"], **cfg}
        batch_info = to.commit_batch(origin, cfg, collected, triggered_by="PRECHECK")
    return ok(saved=True, verified=verified, id=origin_id, success=collected["success"],
              total=collected["total"], failed=collected["failed"],
              errors=collected["errors"], warnings=collected["warnings"], **batch_info)


@bp.route("/api/trans-origins/precheck", methods=["POST"])
def trans_origin_precheck():
    return _run_origin_precheck()


@bp.route("/api/trans-origins/<origin_id>/precheck", methods=["POST"])
def trans_origin_precheck_edit(origin_id):
    return _run_origin_precheck(origin_id)


def _start_precheck_task(route_origin_id=""):
    gate = require_login()
    if gate:
        return gate
    data = request.get_json(silent=True) or {}
    cfg, origin_id, is_new, err, status_code = _prepare_precheck(data, route_origin_id)
    if err:
        return error(err, status_code)
    pairs, err = to.normalize_pairs(json.loads(cfg["params_json"] or "[]"))
    if err:
        return error(err, 400)
    combos, err = to.expand_combinations(pairs)
    if err:
        return error(err, 400)
    task_id = new_id()
    now = utcnow()
    state = {"payloads": [], "items": [], "errors": [], "warnings": [], "failed_combos": []}
    execute(
        "INSERT INTO precheck_tasks "
        "(id, user_id, origin_id, is_new, status, payload_json, state_json, total, "
        "done, failed, round, max_rounds, next_retry_at, current, error, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (task_id, session["uid"], origin_id, 1 if is_new else 0, "RUNNING",
         json.dumps(cfg, ensure_ascii=False), json.dumps(state, ensure_ascii=False),
         len(combos), 0, 0, 1, int(cfg["batch_retry_rounds"]) + 1, "", "", "", now, now),
    )
    _launch_precheck_worker(task_id)
    return ok(task_id=task_id, origin_id=origin_id, is_new=is_new)


@bp.route("/api/trans-origins/precheck-tasks", methods=["POST"])
def trans_origin_precheck_task_create():
    return _start_precheck_task()


@bp.route("/api/trans-origins/<origin_id>/precheck-tasks", methods=["POST"])
def trans_origin_precheck_task_edit(origin_id):
    return _start_precheck_task(origin_id)


@bp.route("/api/trans-origins/precheck-tasks/<task_id>", methods=["GET"])
def trans_origin_precheck_task_status(task_id):
    gate = require_login()
    if gate:
        return gate
    row = one(
        "SELECT * FROM precheck_tasks WHERE id = ? AND user_id = ?", (task_id, session["uid"])
    )
    if not row:
        return error("Precheck task not found", 404)
    return ok(task=_task_public(row))


@bp.route("/api/trans-origins/precheck-tasks/<task_id>/decision", methods=["POST"])
def trans_origin_precheck_task_decision(task_id):
    gate = require_login()
    if gate:
        return gate
    row = one(
        "SELECT * FROM precheck_tasks WHERE id = ? AND user_id = ?", (task_id, session["uid"])
    )
    if not row or row["status"] != "AWAITING_USER":
        return error("Precheck task is not waiting for a decision", 409)
    data = request.get_json(silent=True) or {}
    decision = str(data.get("decision") or "")
    cfg = json.loads(row["payload_json"] or "{}")

    if decision == "cancel":
        _update_task(task_id, status="CANCELLED")
        return ok(status="CANCELLED", saved=False, wrote_data=False)
    if decision == "save_anyway":
        _save_origin(row["origin_id"], row["user_id"], cfg, 0, bool(row["is_new"]))
        _update_task(task_id, status="SAVED_UNVERIFIED")
        return ok(status="SAVED_UNVERIFIED", saved=True, wrote_data=False)
    if decision == "retry":
        if row["round"] >= row["max_rounds"]:
            return error("Retry limit reached", 409)
        interval = float(cfg.get("request_interval") or 0)
        wait_sec = min(max(interval * (2 ** row["round"]), 1.0), 300.0)
        next_at = (datetime.now(timezone.utc) + timedelta(seconds=wait_sec)).isoformat()
        _update_task(
            task_id, status="RUNNING", round=row["round"] + 1,
            next_retry_at=next_at, current="", error="",
        )
        _launch_precheck_worker(task_id, wait_sec=wait_sec)
        return ok(status="RUNNING", round=row["round"] + 1, wait_sec=wait_sec)
    return error("Invalid decision", 400)


def _save_origin(origin_id, user_id, cfg, verified, is_new):
    """Insert or update trans_origins using the state enum only."""
    cols = ("note", "original_url", "original_method", "original_key", "params_json",
            "headers_json", "cookies_json", "body_mode", "body", "encoding", "timeout",
            "retry", "batch_retry_rounds", "request_interval", "frequency", "days_json", "times_json",
            "sheet_id", "result_path", "state")
    vals = [cfg[c] for c in cols[:-1]] + ["ENABLED" if verified else "DRAFT"]
    if is_new:
        sql = ("INSERT INTO trans_origins (id, user_id, " + ", ".join(cols) +
               ", created_at) VALUES (" + ", ".join(["?"] * (len(cols) + 3)) + ")")
        execute(sql, (origin_id, user_id, *vals, utcnow()))
    else:
        sql = ("UPDATE trans_origins SET " + ", ".join(f"{c}=?" for c in cols) +
               " WHERE id=? AND user_id=?")
        execute(sql, (*vals, origin_id, user_id))


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
    verified = 1 if data.get("verified") else (1 if row["state"] == "ENABLED" else 0)
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
