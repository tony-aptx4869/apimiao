"""TRANS User：从数据表查询最新数据，支持别名映射、LEFT JOIN、字段开关。

查询在应用层完成（逻辑表存 JSON）：
1. 每个选中的数据表按请求参数过滤，取符合条件的最新一条
2. 按 joins 做 LEFT JOIN
3. 按 sheet_fields 开关过滤返回字段
4. 始终返回数组，字段名用原始名
"""
import json
from datetime import datetime

from ..db import all_rows, one


def _load(value, default):
    try:
        return json.loads(value or default)
    except (ValueError, TypeError):
        return json.loads(default)


def _parse_condition(value):
    """把请求参数值转成可比较的字符串。"""
    if value is None:
        return None
    return str(value)


def query_latest_rows(sheet_id, filters, limit=1, dedupe=False):
    """对单张表：按 filters（字段→值）过滤，取最新 limit 条。

    dedupe=True 时按 params_used（列表参数组合）分组，每组只取最新一条，
    用于「无参查询返回各记录最新」——油价场景每省各一条。
    """
    rows = all_rows(
        "SELECT id, data_json, params_used, fetched_at FROM data_rows "
        "WHERE sheet_id = ? ORDER BY fetched_at DESC, id DESC", (sheet_id,),
    )
    matched, seen = [], set()
    for row in rows:
        try:
            data = json.loads(row["data_json"] or "{}")
        except (ValueError, TypeError):
            continue
        ok = True
        for field, want in filters.items():
            got = data.get(field)
            if _parse_condition(got) != _parse_condition(want):
                ok = False
                break
        if not ok:
            continue
        if dedupe:
            key = row["params_used"] or "{}"
            if key in seen:
                continue
            seen.add(key)
        matched.append({"row_id": row["id"], "data": data, "fetched_at": row["fetched_at"]})
        if len(matched) >= limit:
            break
    return matched


def execute_query(api, request_params):
    """执行一次 TRANS User 查询。

    api: trans_apis 的 row（dict）
    request_params: 用户请求的参数 dict（已按别名映射前的原始 key）
    返回 (result_list, error)。result_list 为 [{字段: 值}]。
    """
    sheet_ids = _load(api["sheet_ids_json"], "[]")
    if not sheet_ids:
        return [], "No data sheets configured"
    joins = _load(api["joins_json"], "[]")
    sheet_fields = _load(api["sheet_fields_json"], "{}")
    param_mapping = _load(api["param_mapping_json"], "[]")
    body_mapping = _load(api["body_mapping_json"], "[]")

    # 1) 别名映射：对外 key -> (sheet_id, 字段名)
    #    param_mapping: [{sheet_id, field, alias, location}]  location: params/body
    alias_map = {}
    for m in param_mapping + body_mapping:
        alias = m.get("alias") or m.get("field")
        if alias:
            alias_map[str(alias)] = (m.get("sheet_id"), m.get("field"))

    # 把用户请求参数映射成各表的字段过滤条件
    filters_by_sheet = {}   # sheet_id -> {field: value}
    for key, value in request_params.items():
        target = alias_map.get(str(key))
        if not target:
            continue
        sid, field = target
        if sid and field:
            filters_by_sheet.setdefault(sid, {})[field] = value

    # 2) 每张表取最新一条（无过滤条件则取全表最新的所有记录各一条——按每个不同记录取最新）
    sheet_rows = {}
    owner_id = api.get("user_id")
    for sid in sheet_ids:
        sheet = one("SELECT id FROM data_sheets WHERE id = ? AND user_id = ?", (sid, owner_id))
        if not sheet:
            continue
        filters = filters_by_sheet.get(sid, {})
        if filters:
            hits = query_latest_rows(sid, filters, limit=1)
        else:
            hits = query_latest_rows(sid, {}, limit=10000, dedupe=True)  # 无参：每个参数组合各取最新
        sheet_rows[sid] = [{**hit, "sheet_id": sid} for hit in hits]

    # 3) LEFT JOIN：以第一张表为主表，其余按 join 条件关联
    primary_id = sheet_ids[0]
    primary = sheet_rows.get(primary_id, [])
    if not primary:
        return [], None

    def fields_for(sid, data):
        """按单张表的字段开关过滤，未配置的字段默认启用。"""
        configured = sheet_fields.get(sid) if isinstance(sheet_fields, dict) else None
        if not isinstance(configured, dict):
            return dict(data)
        return {k: v for k, v in data.items() if configured.get(k, True)}

    # 构造 join 条件列表：{left_sheet, left_field, right_sheet, right_field}
    results = []
    for base in primary:
        merged = fields_for(base["sheet_id"], base["data"])
        for j in joins:
            left_sheet, left_field = j.get("left_sheet"), j.get("left_field")
            right_sheet, right_field = j.get("right_sheet"), j.get("right_field")
            if not all([left_sheet, left_field, right_sheet, right_field]):
                continue
            # 用左表当前记录的值去右表找匹配
            want = base["data"].get(left_field)
            right_hits = sheet_rows.get(right_sheet, [])
            match = None
            for rh in right_hits:
                if _parse_condition(rh["data"].get(right_field)) == _parse_condition(want):
                    match = rh
                    break
            if match:
                right_data = fields_for(right_sheet, match["data"])
                for k, v in right_data.items():
                    merged.setdefault(k, v)  # 不覆盖主表同名字段
            # LEFT JOIN：匹配不到则右表字段为空，不丢弃主表记录
        results.append(merged)

    return results, None
