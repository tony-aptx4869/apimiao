"""TRANS User：从 Data Sheets 的最新快照中查询并投影数据。"""
from .query_spec import build_query_spec
from .snapshot import latest_rows


def _project(sheet_id, data, projections):
    """Apply per-API projections. No projection means expose all fields."""
    rules = [item for item in projections if item.sheet_id == sheet_id]
    if not rules:
        return dict(data)
    out = {}
    for rule in rules:
        if rule.enabled and rule.key in data:
            out[rule.alias or rule.key] = data[rule.key]
    return out


def execute_query(api, request_params):
    """Execute one TRANS User query from a parsed QuerySpec."""
    try:
        spec = build_query_spec(api)
    except ValueError as exc:
        return [], str(exc)

    filters_by_sheet = {}
    for rule in spec.filters:
        if rule.alias in request_params:
            filters_by_sheet.setdefault(rule.sheet_id, {})[rule.key] = request_params[rule.alias]

    sheet_rows = {}
    for sheet in spec.sheets:
        filters = filters_by_sheet.get(sheet.id)
        if filters:
            rows = latest_rows(sheet.id, filters, dedupe=False, limit=1)
        else:
            rows = latest_rows(sheet.id, {}, dedupe=True, limit=10000)
        sheet_rows[sheet.id] = rows

    primary = sheet_rows.get(spec.sheets[0].id, [])
    results = []
    for base in primary:
        merged = _project(base["sheet_id"], base["data"], spec.projections)
        for join in spec.joins:
            want = base["data"].get(join.left_key)
            right_hits = sheet_rows.get(join.right_sheet, [])
            match = next(
                (hit for hit in right_hits if str(hit["data"].get(join.right_key)) == str(want)),
                None,
            )
            if match:
                for key, value in _project(join.right_sheet, match["data"], spec.projections).items():
                    merged.setdefault(key, value)
        results.append(merged)
    return results, None
