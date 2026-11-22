from dataclasses import dataclass, field

from ..db import all_rows, one


@dataclass(frozen=True)
class SheetRef:
    id: str
    alias: str = ""


@dataclass(frozen=True)
class FieldProjection:
    sheet_id: str
    field_id: str
    key: str
    enabled: bool = True
    alias: str = ""


@dataclass(frozen=True)
class FieldFilter:
    alias: str
    sheet_id: str
    field_id: str
    key: str


@dataclass(frozen=True)
class FieldJoin:
    left_sheet: str
    left_field_id: str
    left_key: str
    right_sheet: str
    right_field_id: str
    right_key: str


@dataclass(frozen=True)
class QuerySpec:
    user_id: str
    sheets: list = field(default_factory=list)
    projections: list = field(default_factory=list)
    filters: list = field(default_factory=list)
    joins: list = field(default_factory=list)


def _load(value, default):
    import json
    try:
        loaded = json.loads(value or "")
    except (TypeError, ValueError):
        return default
    return loaded if isinstance(loaded, type(default)) else default


def _field_keys(sheet_id):
    return {
        row["id"]: row["key"]
        for row in all_rows("SELECT id, key FROM sheet_fields WHERE sheet_id = ?", (sheet_id,))
    }


def build_query_spec(api, user_id=None):
    """Parse and validate query_config_v2 at the HTTP boundary."""
    owner = user_id or api.get("user_id")
    config = _load(api.get("query_config_v2"), {})
    if not config:
        raise ValueError("Missing query configuration")

    sheets = []
    for item in config.get("sheets") or []:
        sheet_id = str(item.get("id") if isinstance(item, dict) else item)
        if not one("SELECT 1 FROM data_sheets WHERE id = ? AND user_id = ?", (sheet_id, owner)):
            raise ValueError("Invalid data sheet")
        sheets.append(SheetRef(sheet_id, str(item.get("alias") or "") if isinstance(item, dict) else ""))
    if not sheets:
        raise ValueError("No data sheets configured")

    field_cache = {sheet.id: _field_keys(sheet.id) for sheet in sheets}

    def key_for(sheet_id, field_id, fallback=""):
        key = field_cache.get(sheet_id, {}).get(str(field_id or ""))
        if not key:
            raise ValueError("Invalid field reference")
        return key

    projections = []
    for item in config.get("projections") or []:
        if not isinstance(item, dict):
            continue
        sheet_id = str(item.get("sheet_id") or "")
        field_id = str(item.get("field_id") or "")
        if not field_id:
            fallback = str(item.get("key") or item.get("field") or "")
            field_id = next((fid for fid, key in field_cache.get(sheet_id, {}).items() if key == fallback), "")
        projections.append(FieldProjection(
            sheet_id, field_id, key_for(sheet_id, field_id),
            bool(item.get("enabled", True)), str(item.get("alias") or ""),
        ))

    filters = []
    for item in config.get("filters") or []:
        if not isinstance(item, dict):
            continue
        sheet_id = str(item.get("sheet_id") or "")
        field_id = str(item.get("field_id") or "")
        if not field_id:
            fallback = str(item.get("key") or item.get("field") or "")
            field_id = next((fid for fid, key in field_cache.get(sheet_id, {}).items() if key == fallback), "")
        alias = str(item.get("alias") or key_for(sheet_id, field_id))
        filters.append(FieldFilter(alias, sheet_id, field_id, key_for(sheet_id, field_id)))

    joins = []
    for item in config.get("joins") or []:
        if not isinstance(item, dict):
            continue
        left_sheet = str(item.get("left_sheet") or "")
        right_sheet = str(item.get("right_sheet") or "")
        left_id = str(item.get("left_field_id") or "")
        right_id = str(item.get("right_field_id") or "")
        if not left_id:
            fallback = str(item.get("left_field") or "")
            left_id = next((fid for fid, key in field_cache.get(left_sheet, {}).items() if key == fallback), "")
        if not right_id:
            fallback = str(item.get("right_field") or "")
            right_id = next((fid for fid, key in field_cache.get(right_sheet, {}).items() if key == fallback), "")
        joins.append(FieldJoin(
            left_sheet, left_id, key_for(left_sheet, left_id),
            right_sheet, right_id, key_for(right_sheet, right_id),
        ))

    return QuerySpec(owner, sheets, projections, filters, joins)
