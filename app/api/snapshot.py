import json

from ..db import all_rows


def _json_path(key):
    return '$."' + str(key).replace('"', '""') + '"'


def latest_rows(sheet_id, filters=None, dedupe=True, limit=1):
    """Read latest snapshot rows with SQL-side filtering and ranking."""
    filters = filters or {}
    where = []
    args = [sheet_id]
    for key, value in filters.items():
        where.append("json_extract(data_json, ?) = ?")
        args.extend([_json_path(key), str(value)])
    filter_sql = (" AND " + " AND ".join(where)) if where else ""

    if dedupe:
        sql = (
            "WITH ranked AS ("
            "SELECT id, data_json, params_key, fetched_at, "
            "ROW_NUMBER() OVER (PARTITION BY params_key ORDER BY fetched_at DESC, id DESC) AS rn "
            "FROM data_rows WHERE sheet_id = ?" + filter_sql + ") "
            "SELECT id, data_json, params_key, fetched_at FROM ranked WHERE rn = 1 LIMIT ?"
        )
    else:
        sql = (
            "SELECT id, data_json, params_key, fetched_at FROM data_rows "
            "WHERE sheet_id = ?" + filter_sql + " "
            "ORDER BY fetched_at DESC, id DESC LIMIT ?"
        )
    rows = all_rows(sql, (*args, limit))
    out = []
    for row in rows:
        try:
            data = json.loads(row["data_json"] or "{}")
        except (TypeError, ValueError):
            continue
        out.append({
            "row_id": row["id"],
            "sheet_id": sheet_id,
            "params_key": row["params_key"],
            "data": data if isinstance(data, dict) else {},
            "fetched_at": row["fetched_at"],
        })
    return out
