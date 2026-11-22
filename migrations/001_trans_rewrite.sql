PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS sheet_fields (
    id TEXT PRIMARY KEY,
    sheet_id TEXT NOT NULL REFERENCES data_sheets(id) ON DELETE CASCADE,
    key TEXT NOT NULL,
    label TEXT NOT NULL DEFAULT '',
    json_type TEXT NOT NULL DEFAULT 'string',
    ordinal INTEGER NOT NULL DEFAULT 0,
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    UNIQUE(sheet_id, key)
);

CREATE INDEX IF NOT EXISTS idx_sheet_fields_sheet ON sheet_fields(sheet_id, ordinal);

CREATE INDEX IF NOT EXISTS idx_rows_sheet_params_time ON data_rows(sheet_id, params_key, fetched_at);

CREATE TABLE IF NOT EXISTS schema_migrations (
    version TEXT PRIMARY KEY,
    applied_at TEXT NOT NULL
);
