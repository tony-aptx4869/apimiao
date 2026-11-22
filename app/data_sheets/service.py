import json

from ..db import get_db, new_id, utcnow
from ..json_path import resolve_root
from .models import FieldDraft, SheetDraft


def _json(value, default):
    try:
        loaded = json.loads(value or "")
    except (TypeError, ValueError):
        return default
    return loaded if isinstance(loaded, type(default)) else default


def _json_type(value):
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, dict):
        return "object"
    if isinstance(value, list):
        return "array"
    if value is None:
        return "null"
    return "string"


def discover_fields(sample_json, path):
    """Return scalar leaf fields under the resolved JSON node."""
    try:
        sample = json.loads(sample_json)
    except (TypeError, ValueError):
        raise ValueError("Sample JSON is invalid")
    root = resolve_root(sample, path)
    if not isinstance(root, dict):
        raise ValueError("Resolved node must be a JSON object")
    fields = []
    for index, (key, value) in enumerate(root.items()):
        if isinstance(value, (dict, list)):
            continue
        fields.append(FieldDraft(key=str(key), label=str(key), json_type=_json_type(value), order=index))
    return fields


def _field_rows(sheet_id):
    return [
        {
            "id": row["id"],
            "key": row["key"],
            "label": row["label"],
            "json_type": row["json_type"],
            "enabled": bool(row["enabled"]),
            "order": row["ordinal"],
        }
        for row in get_db().execute(
            "SELECT id, key, label, json_type, enabled, ordinal FROM sheet_fields "
            "WHERE sheet_id = ? ORDER BY ordinal, key",
            (sheet_id,),
        )
    ]


def _sheet_dict(row):
    return {
        "id": row["id"],
        "name": row["name"],
        "description": row["description"] or "",
        "created_at": row["created_at"],
        "updated_at": row["updated_at"] or "",
        "fields": _field_rows(row["id"]),
    }


def _sync_legacy_fields(sheet_id):
    """Keep the legacy column current until Origin and scheduler stop reading it."""
    fields = _field_rows(sheet_id)
    legacy = [
        {"name": item["key"], "enabled": item["enabled"], "order": item["order"]}
        for item in fields
    ]
    get_db().execute(
        "UPDATE data_sheets SET fields_json = ? WHERE id = ?",
        (json.dumps(legacy, ensure_ascii=False), sheet_id),
    )


def list_sheets(user_id):
    rows = get_db().execute(
        "SELECT id, name, description, created_at, updated_at FROM data_sheets "
        "WHERE user_id = ? ORDER BY name",
        (user_id,),
    )
    return [_sheet_dict(row) for row in rows]


def get_sheet(user_id, sheet_id):
    row = get_db().execute(
        "SELECT id, name, description, created_at, updated_at FROM data_sheets "
        "WHERE id = ? AND user_id = ?",
        (sheet_id, user_id),
    ).fetchone()
    return _sheet_dict(row) if row else None


def _normalize_fields(items):
    fields = []
    for index, item in enumerate(items or []):
        if not isinstance(item, dict):
            continue
        key = str(item.get("key") or item.get("name") or "").strip()
        if not key:
            continue
        fields.append({
            "key": key,
            "label": str(item.get("label") or key),
            "json_type": str(item.get("json_type") or "string"),
            "enabled": bool(item.get("enabled", True)),
            "order": int(item.get("order", index) or index),
        })
    return fields


def create_sheet(user_id, payload):
    draft = SheetDraft(
        name=str(payload.get("name") or "").strip(),
        description=str(payload.get("description") or "").strip(),
        fields=_normalize_fields(payload.get("fields")),
    )
    if not draft.name:
        raise ValueError("Sheet name is required")
    db = get_db()
    sheet_id = new_id()
    now = utcnow()
    db.execute(
        "INSERT INTO data_sheets (id, user_id, name, description, fields_json, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, '[]', ?, ?)",
        (sheet_id, user_id, draft.name, draft.description, now, now),
    )
    _write_fields(sheet_id, draft.fields)
    _sync_legacy_fields(sheet_id)
    db.commit()
    return get_sheet(user_id, sheet_id)


def update_sheet(user_id, sheet_id, payload):
    current = get_sheet(user_id, sheet_id)
    if not current:
        return None
    name = str(payload.get("name") or current["name"]).strip()
    if not name:
        raise ValueError("Sheet name is required")
    db = get_db()
    db.execute(
        "UPDATE data_sheets SET name = ?, description = ?, updated_at = ? WHERE id = ? AND user_id = ?",
        (name, str(payload.get("description") or "").strip(), utcnow(), sheet_id, user_id),
    )
    if "fields" in payload:
        _write_fields(sheet_id, _normalize_fields(payload.get("fields")))
        _sync_legacy_fields(sheet_id)
    db.commit()
    return get_sheet(user_id, sheet_id)


def delete_sheet(user_id, sheet_id):
    db = get_db()
    cur = db.execute("DELETE FROM data_sheets WHERE id = ? AND user_id = ?", (sheet_id, user_id))
    db.commit()
    return cur.rowcount == 1


def _write_fields(sheet_id, fields):
    db = get_db()
    db.execute("DELETE FROM sheet_fields WHERE sheet_id = ?", (sheet_id,))
    seen = set()
    for item in fields:
        if item["key"] in seen:
            continue
        seen.add(item["key"])
        db.execute(
            "INSERT INTO sheet_fields "
            "(id, sheet_id, key, label, json_type, ordinal, enabled, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                new_id(), sheet_id, item["key"], item["label"], item["json_type"],
                item["order"], 1 if item["enabled"] else 0, utcnow(),
            ),
        )
