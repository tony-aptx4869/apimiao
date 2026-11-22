"""TRANS Origin：采集源的配置、列表展开、定时/预检采集、字段预览。

数据存逻辑表：trans_origins（配置）→ fetch_batches/fetch_logs（记录）→ data_sheets/data_rows（数据）。
所有 db helper 依赖 flask.g，需在 app context 内调用。
"""
import ast
import itertools
import json
import time

import httpx

from config import CARTESIAN_LIMIT
from ..db import all_rows, canonical_params, execute, get_db, new_id, one, utcnow
from ..json_path import resolve_root
from ..utils import PLACEHOLDER_RE, merge_url_params


# ---------------- 列表值解析 ----------------
def parse_list_value(raw):
    """把列表值解析成字符串列表。

    支持两种格式：
      1) Python list 字面量，如 ['北京', '天津']
      2) 英文分号分隔文本，如 北京;天津;河北（分号用 \\; 转义）

    返回 (items, error)。error 非空表示解析失败。
    """
    if raw is None:
        return [], None
    text = str(raw).strip()
    if not text:
        return [], None
    if text.startswith("["):
        try:
            val = ast.literal_eval(text)
            if not isinstance(val, (list, tuple)):
                return [], "列表格式必须是 Python list，如 ['北京', '天津']"
            items = [str(x).strip() for x in val if str(x).strip()]
            return items, None
        except (ValueError, SyntaxError) as exc:
            return [], f"列表解析失败：{exc}（检查括号和引号是否完整）"
    # 分号分隔，先保护转义分号 \;
    placeholder = "\x00SEMICOLON\x00"
    escaped = text.replace("\\;", placeholder)
    parts = escaped.split(";")
    items = [p.replace(placeholder, ";").strip() for p in parts]
    items = [x for x in items if x]
    return items, None


def normalize_pairs(pairs):
    """把前端传来的键值对标准化，解析列表模式。

    每项形如 {key, value, is_list, list_raw}。返回 (normalized, error)。
    normalized 每项：{key, value, is_list, items}
    """
    out = []
    for p in pairs or []:
        key = str(p.get("key") or "").strip()
        if not key:
            continue
        is_list = bool(p.get("is_list"))
        if is_list:
            # 若已带 items（内部复用/编辑场景），直接用；否则从 list_raw/value 解析
            items = p.get("items")
            if not items:
                raw = p.get("list_raw", p.get("value", ""))
                items, err = parse_list_value(raw)
                if err:
                    return [], f"参数 {key}：{err}"
            items = [str(x).strip() for x in (items or []) if str(x).strip()]
            if not items:
                return [], f"参数 {key} 的列表为空"
            out.append({"key": key, "value": str(p.get("value") or ""), "is_list": True, "items": items})
        else:
            out.append({"key": key, "value": str(p.get("value") or ""), "is_list": False, "items": []})
    return out, None


def expand_combinations(pairs):
    """按所有 list 项做笛卡尔积，返回参数组合列表（每项为 dict）。

    超过 CARTESIAN_LIMIT（>0 时）返回错误。
    """
    list_pairs = [p for p in pairs if p["is_list"]]
    scalars = {p["key"]: p["value"] for p in pairs if not p["is_list"]}
    if not list_pairs:
        return [scalars], None
    total = 1
    for p in list_pairs:
        total *= len(p["items"])
    if CARTESIAN_LIMIT > 0 and total > CARTESIAN_LIMIT:
        return [], (f"列表组合数 {total} 超过上限 {CARTESIAN_LIMIT}，请减少列表项或调整 config.ini 的 cartesian_limit")
    keys = [p["key"] for p in list_pairs]
    # 标量参数固定值，每个组合都要带上
    scalars = {p["key"]: p["value"] for p in pairs if not p["is_list"]}
    combos = []
    for combo in itertools.product(*[p["items"] for p in list_pairs]):
        item = dict(scalars)
        item.update(dict(zip(keys, combo)))
        combos.append(item)
    return combos, None


# ---------------- 成功判定 ----------------
def judge_success(status, body_text):
    """HTTP 2xx 且业务码成功才算成功。

    业务码识别常见字段：code / status / success / result / errcode / errno。
    无法解析 JSON 时只看 HTTP；字段类型多样，尽量兼容。
    """
    if not (200 <= status < 300):
        return False, "HTTP " + str(status)
    if not body_text:
        return True, ""
    try:
        data = json.loads(body_text)
    except (ValueError, TypeError):
        return True, ""  # 非 JSON（如文本/二进制），HTTP 成功即算成功
    if not isinstance(data, dict):
        return True, ""
    # success 布尔，兼容 0/1 和字符串 true/false
    if "success" in data and isinstance(data["success"], (bool, int, float, str)):
        raw = data["success"]
        if isinstance(raw, bool):
            ok_value = raw
        elif isinstance(raw, (int, float)):
            ok_value = raw != 0
        else:
            ok_value = str(raw).strip().lower() in ("1", "true", "yes", "ok", "success")
        return ok_value, "" if ok_value else "success=false"
    for field in ("code", "status", "errcode", "errno"):
        if field in data:
            v = data[field]
            # 数值码：0 或 200 视为成功；字符串：'0'/'200'/'ok'/'success'
            if isinstance(v, bool):
                ok = v
            elif isinstance(v, (int, float)):
                ok = int(v) in (0, 200)
            else:
                sv = str(v).strip().lower()
                ok = sv in ("0", "200", "ok", "success", "true")
            return ok, "" if ok else f"{field}={v}"
    return True, ""


# ---------------- 占位符与请求 ----------------
def _fill(text, values, o_api_key=""):
    if not text:
        return ""
    def repl(m):
        k = m.group(1)
        if k == "o-api-key":
            return o_api_key
        return values.get(k, m.group(0))
    return PLACEHOLDER_RE.sub(repl, text)


def _pairs_to_dict(pairs_json, values, o_api_key=""):
    try:
        pairs = json.loads(pairs_json or "[]")
    except (ValueError, TypeError):
        pairs = []
    out = {}
    for p in pairs:
        key = p.get("key")
        if not key:
            continue
        # 列表型参数的值由本次 combo 提供（values），其 value 字段通常为空，
        # 若照原样加入会用空串覆盖掉 URL/values 里的真实列表值。
        if p.get("is_list"):
            if key in values:
                out[key] = str(values[key])
            continue
        out[key] = _fill(str(p.get("value") or ""), values, o_api_key)
    return out


def build_request(cfg, values=None):
    """根据 Origin 配置 + 本次列表值，构造 httpx 请求参数。"""
    values = values or {}
    o_key = cfg.get("original_key", "")
    url = _fill(cfg.get("original_url", ""), values, o_key)
    params = _pairs_to_dict(cfg.get("params_json", "[]"), values, o_key)
    headers = _pairs_to_dict(cfg.get("headers_json", "[]"), values, o_key)
    cookies = _pairs_to_dict(cfg.get("cookies_json", "[]"), values, o_key)
    url, params = merge_url_params(url, params)
    kwargs = {"headers": headers, "cookies": cookies}
    if params:
        kwargs["params"] = params
    body_mode = cfg.get("body_mode", "none")
    if body_mode == "json" and cfg.get("body"):
        kwargs["content"] = _fill(cfg["body"], values, o_key).encode("utf-8")
        if "content-type" not in {k.lower() for k in headers}:
            kwargs["headers"]["Content-Type"] = "application/json; charset=utf-8"
    elif body_mode == "text" and cfg.get("body"):
        kwargs["content"] = _fill(cfg["body"], values, o_key).encode("utf-8")
    elif body_mode == "binary" and cfg.get("body"):
        kwargs["content"] = _fill(cfg["body"], values, o_key).encode("utf-8")
    return cfg.get("original_method", "GET"), url, kwargs


def do_request(cfg, values=None):
    """发一次请求，返回 (status, headers, cookies, body_text, duration_ms, error)。"""
    method, url, kwargs = build_request(cfg, values)
    timeout = cfg.get("timeout", 10) or 10
    retries = cfg.get("retry", 0) or 0
    started = time.monotonic()
    last_err = ""
    for attempt in range(retries + 1):
        try:
            with httpx.Client(timeout=timeout, follow_redirects=True) as client:
                resp = client.request(method, url, **kwargs)
            duration = int((time.monotonic() - started) * 1000)
            return resp.status_code, dict(resp.headers), dict(resp.cookies), resp.text, duration, ""
        except Exception as exc:
            last_err = str(exc)
            if attempt < retries:
                time.sleep(0.3 * (attempt + 1))
    duration = int((time.monotonic() - started) * 1000)
    return 0, {}, {}, "", duration, last_err


# ---------------- 数据字段与存储 ----------------
def extract_fields(body_text, fields_config=None):
    """从响应体解析字段。fields_config 为 [{name, enabled, order}]。

    返回 (field_names_in_order, data_dict)。fields_config 为空时取全部顶层字段。
    """
    try:
        data = json.loads(body_text or "{}")
    except (ValueError, TypeError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    if not fields_config:
        return list(data.keys()), data
    ordered = sorted(fields_config, key=lambda f: f.get("order", 0))
    names = [f["name"] for f in ordered if f.get("enabled", True) and f["name"] in data]
    # 只保留开启且存在的字段，按用户设定顺序构造 payload
    payload = {name: data[name] for name in names}
    return names, payload


def load_sheet_fields(sheet_id):
    """Load the authoritative field schema owned by Data Sheets."""
    rows = all_rows(
        "SELECT key AS name, enabled, ordinal AS `order` FROM sheet_fields "
        "WHERE sheet_id = ? ORDER BY ordinal, key",
        (sheet_id,),
    )
    return [dict(row) for row in rows]


def extract_payload(body_text, result_path, fields_cfg=None):
    """Resolve the configured result node before extracting fields."""
    try:
        data = json.loads(body_text or "{}")
    except (ValueError, TypeError):
        data = {}
    root = resolve_root(data, result_path)
    if not isinstance(root, dict):
        raise ValueError("Resolved result node must be a JSON object")
    return extract_fields(json.dumps(root, ensure_ascii=False), fields_cfg)


def ensure_sheet(user_id, origin_id, sheet_name, field_names, replace_fields=False):
    """获取或创建逻辑数据表记录，返回 sheet_id。"""
    row = one("SELECT id FROM data_sheets WHERE user_id = ? AND name = ? AND origin_id = ?",
              (user_id, sheet_name, origin_id))
    fields_json = json.dumps(
        [{"name": n, "enabled": True, "order": i} for i, n in enumerate(field_names)],
        ensure_ascii=False,
    )
    if row:
        if replace_fields:
            execute("UPDATE data_sheets SET fields_json = ?, origin_id = ? WHERE id = ?",
                    (fields_json, origin_id, row["id"]))
        return row["id"]
    sheet_id = new_id()
    execute(
        "INSERT INTO data_sheets (id, user_id, name, origin_id, fields_json, created_at) VALUES (?, ?, ?, ?, ?, ?)",
        (sheet_id, user_id, sheet_name, origin_id, fields_json, utcnow()),
    )
    return sheet_id


# ---------------- 完整批次采集 ----------------
def collect_batch(origin, cfg_override=None):
    """只采集不落库（dry-run）：跑完整一轮，把所有请求结果收集在内存返回。

    用于 Save 预检——失败时数据库完全不留痕迹，全成功才由 commit_batch 落库。
    返回 dict: {success, total, failed, errors, items, field_names, payloads}
      items: 每次请求的 {combo, status, success, error, duration}
      payloads: 成功请求的 {combo, data}（按字段配置过滤后）
    """
    cfg = dict(cfg_override) if cfg_override else dict(origin)
    pairs, err = normalize_pairs(json.loads(cfg.get("params_json", "[]")))
    if err:
        return {"success": False, "total": 0, "failed": 0, "errors": [err], "items": [], "payloads": [], "field_names": []}
    combos, err = expand_combinations(pairs)
    if err:
        return {"success": False, "total": 0, "failed": 0, "errors": [err], "items": [], "payloads": [], "field_names": []}

    fields_cfg = load_sheet_fields(origin.get("sheet_id") or cfg.get("sheet_id") or "")
    result_path = cfg.get("result_path") or origin.get("result_path") or ""
    interval = float(cfg.get("request_interval", 0) or 0)
    ok_count, fail_count, errors, items, payloads = 0, 0, [], [], []
    field_names = []

    for idx, combo in enumerate(combos):
        status, headers, cookies, body, duration, net_err = do_request(cfg, combo)
        biz_err = ""
        if net_err:
            success = False
        else:
            try:
                resolved = resolve_root(json.loads(body or "{}"), result_path)
                success, biz_err = judge_success(status, json.dumps(resolved, ensure_ascii=False))
            except (ValueError, TypeError) as exc:
                success, biz_err = False, str(exc)
        err_msg = net_err or biz_err
        items.append({"combo": combo, "status": status, "success": success,
                      "error": err_msg, "duration": duration})
        if success:
            ok_count += 1
            try:
                names, payload = extract_payload(body, result_path, fields_cfg)
            except (ValueError, TypeError) as exc:
                success = False
                fail_count += 1
                errors.append({"params": combo, "error": str(exc), "status": status})
                continue
            if not field_names:
                field_names = names
            payloads.append({"combo": combo, "data": payload})
        else:
            fail_count += 1
            errors.append({"params": combo, "error": err_msg, "status": status})
        if interval > 0 and idx < len(combos) - 1:
            time.sleep(interval)

    return {"success": fail_count == 0, "total": len(combos), "failed": fail_count,
            "errors": errors, "items": items, "payloads": payloads, "field_names": field_names}


def commit_batch(origin, cfg, collected, triggered_by="PRECHECK", batch_key=""):
    """Commit one collection batch atomically into the chosen Data Sheet."""
    batch_id = new_id()
    key = batch_key or batch_id
    sheet_id = origin["sheet_id"]
    db = get_db()
    existing = one("SELECT id FROM fetch_batches WHERE batch_key = ?", (key,))
    if existing:
        return {"batch_id": existing["id"], "sheet_id": sheet_id, "skipped": True}
    try:
        db.execute(
            "INSERT INTO fetch_batches (id, batch_key, origin_id, user_id, triggered_by, started_at, total, success, failed, finished_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (batch_id, key, origin["id"], origin["user_id"], triggered_by, utcnow(), collected["total"],
             collected["total"] - collected["failed"], collected["failed"], utcnow()),
        )
        for item in collected["items"]:
            params_json, params_key = canonical_params(item["combo"])
            db.execute(
                "INSERT INTO fetch_logs (batch_id, origin_id, params_used, status, success, error, duration_ms, fetched_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (batch_id, origin["id"], params_json, item["status"],
                 1 if item["success"] else 0, item["error"], item["duration"], utcnow()),
            )
        for payload in collected["payloads"]:
            params_json, params_key = canonical_params(payload["combo"])
            db.execute(
                "INSERT INTO data_rows (sheet_id, batch_id, data_json, params_json, params_key, params_used, fetched_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (sheet_id, batch_id, json.dumps(payload["data"], ensure_ascii=False),
                 params_json, params_key, params_json, utcnow()),
            )
        db.commit()
    except Exception:
        db.rollback()
        raise
    return {"batch_id": batch_id, "sheet_id": sheet_id}


def run_batch(origin, triggered_by="SCHEDULE", cfg_override=None, save_data=True, batch_key=""):
    """执行完整一轮采集并落库（定时任务/手动触发用）。"""
    collected = collect_batch(origin, cfg_override)
    if not save_data:
        return collected
    committed = commit_batch(
        origin, cfg_override or dict(origin), collected, triggered_by, batch_key=batch_key
    )
    return {**collected, **committed}



def fetch_preview(cfg):
    """Fetch Data：只发列表第一个值，返回字段预览（不入库）。"""
    pairs, err = normalize_pairs(json.loads(cfg.get("params_json", "[]")))
    if err:
        return {"ok": False, "error": err}
    values = {}
    for p in pairs:
        values[p["key"]] = p["items"][0] if p["is_list"] else p["value"]
    status, headers, cookies, body, duration, net_err = do_request(cfg, values)
    try:
        resolved = resolve_root(json.loads(body or "{}"), cfg.get("result_path", ""))
        success, biz_err = judge_success(status, json.dumps(resolved, ensure_ascii=False))
    except (ValueError, TypeError) as exc:
        success, biz_err = False, str(exc)
    if net_err:
        return {"ok": False, "error": net_err, "status": status}
    try:
        names, data = extract_payload(body, cfg.get("result_path", ""), cfg.get("fields"))
    except (ValueError, TypeError) as exc:
        return {"ok": False, "error": str(exc), "status": status}
    return {
        "ok": True, "success": success, "biz_error": biz_err,
        "status": status, "fields": names, "data": data,
        "headers": headers, "cookies": cookies, "duration_ms": duration,
    }
