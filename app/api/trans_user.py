"""TRANS User：从 Data Sheets 的最新快照中查询并投影数据。"""
import json

from ..db import all_rows
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


def _load_output_tree(api):
    """Read the stored output tree; empty or malformed means flat output."""
    raw = api.get("output_tree_json") or ""
    if isinstance(raw, str):
        raw = raw.strip()
    if not raw:
        return None
    try:
        tree = json.loads(raw)
    except (TypeError, ValueError):
        return None
    return tree if isinstance(tree, dict) else None


def _children_dict(node, merged, field_keys):
    out = {}
    for child in node.get("children") or []:
        _append_node(out, child, merged, field_keys)
    return out


def _append_node(out, node, merged, field_keys):
    if not isinstance(node, dict):
        return
    output_key = str(node.get("key") or "")
    kind = node.get("type")
    if kind == "field":
        field_id = str(node.get("field_id") or "")
        source_key = field_keys.get(field_id) or output_key
        if source_key in merged:
            out[output_key or source_key] = merged[source_key]
        return
    if not output_key:
        return
    if kind == "object":
        out[output_key] = _children_dict(node, merged, field_keys)
    elif kind == "array":
        out[output_key] = [_children_dict(node, merged, field_keys)]


def _apply_output_tree(merged, tree, field_keys):
    """Shape one merged row with the configured output tree."""
    if not tree or tree.get("type") != "object" or not tree.get("children"):
        return merged
    out = {}
    for child in tree["children"]:
        _append_node(out, child, merged, field_keys)
    return out


def execute_query(api, request_params):
    """Execute one TRANS User query from a parsed QuerySpec."""
    try:
        spec = build_query_spec(api)
    except ValueError as exc:
        return [], str(exc)

    tree = _load_output_tree(api)
    field_keys = {}
    if tree and tree.get("children"):
        field_keys = {row["id"]: row["key"] for row in all_rows("SELECT id, key FROM sheet_fields")}

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
        results.append(_apply_output_tree(merged, tree, field_keys))
    return results, None
