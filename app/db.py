"""SQLite 存储层。WAL 模式 + 外键约束，UTC 时间统一 ISO8601 字符串。"""
import hashlib
import json
import os
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from flask import current_app, g, has_request_context

from config import DB_PATH, ADMIN_ID, ADMIN_EMAIL

PROJECT_ROOT = Path(__file__).resolve().parent.parent
MIGRATIONS_DIR = PROJECT_ROOT / "migrations"
TRANS_REWRITE_VERSION = "001_trans_rewrite"
TRANS_HARDENING_VERSION = "002_trans_hardening"
TRANS_CLEANUP_VERSION = "003_trans_cleanup"

SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    email TEXT NOT NULL UNIQUE COLLATE NOCASE,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'USER',
    totp_secret TEXT,
    totp_enabled INTEGER NOT NULL DEFAULT 0,
    must_change_password INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS passkeys (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    platform TEXT NOT NULL,
    created_at TEXT NOT NULL,
    credential_id BLOB NOT NULL UNIQUE,
    public_key BLOB NOT NULL,
    sign_count INTEGER NOT NULL DEFAULT 0,
    transports TEXT
);

CREATE TABLE IF NOT EXISTS api_keys (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    key_hash TEXT NOT NULL UNIQUE,
    prefix TEXT NOT NULL,
    suffix TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS apis (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    endpoint TEXT NOT NULL,
    method TEXT NOT NULL,
    type TEXT NOT NULL,
    auth TEXT NOT NULL DEFAULT 'x-api-key,bearer',
    encoding TEXT NOT NULL DEFAULT 'UTF-8',
    note TEXT NOT NULL DEFAULT '',
    config TEXT NOT NULL DEFAULT '{}',
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    UNIQUE(user_id, endpoint, method)
);

CREATE TABLE IF NOT EXISTS request_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    api_id TEXT,
    method TEXT NOT NULL,
    endpoint TEXT NOT NULL,
    status INTEGER NOT NULL,
    success INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    duration_ms INTEGER NOT NULL DEFAULT 0,
    detail TEXT,
    client_ip TEXT,
    event_type TEXT NOT NULL DEFAULT 'GATEWAY',
    api_key_id INTEGER
);
CREATE INDEX IF NOT EXISTS idx_logs_user_time ON request_logs(user_id, created_at);

CREATE TABLE IF NOT EXISTS api_test_params (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    api_id TEXT,
    key TEXT NOT NULL,
    value TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(user_id, api_id, key)
);

-- ============ TRANS 模块（新版采集/查询架构）============
-- 采集源配置：URL、参数（含列表）、定时、间隔、目标数据表
CREATE TABLE IF NOT EXISTS trans_origins (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    note TEXT NOT NULL DEFAULT '',
    original_url TEXT NOT NULL,
    original_method TEXT NOT NULL DEFAULT 'GET',
    original_key TEXT NOT NULL DEFAULT '',
    params_json TEXT NOT NULL DEFAULT '[]',
    headers_json TEXT NOT NULL DEFAULT '[]',
    cookies_json TEXT NOT NULL DEFAULT '[]',
    body_mode TEXT NOT NULL DEFAULT 'none',
    body TEXT NOT NULL DEFAULT '',
    encoding TEXT NOT NULL DEFAULT 'UTF-8',
    timeout INTEGER NOT NULL DEFAULT 10,
    retry INTEGER NOT NULL DEFAULT 0,
    request_interval REAL NOT NULL DEFAULT 0,
    frequency TEXT NOT NULL DEFAULT 'DAY',
    days_json TEXT NOT NULL DEFAULT '[]',
    times_json TEXT NOT NULL DEFAULT '[]',
    sheet_name TEXT NOT NULL DEFAULT '',
    verified INTEGER NOT NULL DEFAULT 0,
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_origin_user ON trans_origins(user_id);

-- 逻辑数据表：user + name 唯一，fields_json 记录字段名/是否保存/顺序
CREATE TABLE IF NOT EXISTS data_sheets (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    origin_id TEXT REFERENCES trans_origins(id) ON DELETE CASCADE,
    fields_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL,
    UNIQUE(user_id, name)
);

-- 数据行：data_json 存字段→值，params_used 记录本次是哪个列表组合，fetched_at 用于取最新
CREATE TABLE IF NOT EXISTS data_rows (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sheet_id TEXT NOT NULL REFERENCES data_sheets(id) ON DELETE CASCADE,
    batch_id TEXT NOT NULL,
    data_json TEXT NOT NULL,
    params_used TEXT NOT NULL DEFAULT '{}',
    fetched_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_rows_sheet_time ON data_rows(sheet_id, fetched_at);

-- 采集批次：每轮定时任务/预检的汇总
CREATE TABLE IF NOT EXISTS fetch_batches (
    id TEXT PRIMARY KEY,
    origin_id TEXT NOT NULL REFERENCES trans_origins(id) ON DELETE CASCADE,
    user_id TEXT NOT NULL,
    triggered_by TEXT NOT NULL DEFAULT 'SCHEDULE',
    started_at TEXT NOT NULL,
    finished_at TEXT,
    total INTEGER NOT NULL DEFAULT 0,
    success INTEGER NOT NULL DEFAULT 0,
    failed INTEGER NOT NULL DEFAULT 0
);

-- 每次请求明细：参数组合、状态、错误、耗时
CREATE TABLE IF NOT EXISTS fetch_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_id TEXT NOT NULL REFERENCES fetch_batches(id) ON DELETE CASCADE,
    origin_id TEXT NOT NULL,
    params_used TEXT NOT NULL DEFAULT '{}',
    status INTEGER,
    success INTEGER NOT NULL DEFAULT 0,
    error TEXT NOT NULL DEFAULT '',
    duration_ms INTEGER NOT NULL DEFAULT 0,
    fetched_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_fetchlogs_batch ON fetch_logs(batch_id);

-- 用户 API 配置：指向若干数据表、字段开关、JOIN、别名映射、限流
CREATE TABLE IF NOT EXISTS trans_apis (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    endpoint TEXT NOT NULL,
    method TEXT NOT NULL,
    auth TEXT NOT NULL DEFAULT 'x-api-key,bearer',
    encoding TEXT NOT NULL DEFAULT 'UTF-8',
    note TEXT NOT NULL DEFAULT '',
    sheet_ids_json TEXT NOT NULL DEFAULT '[]',
    joins_json TEXT NOT NULL DEFAULT '[]',
    sheet_fields_json TEXT NOT NULL DEFAULT '{}',
    param_mapping_json TEXT NOT NULL DEFAULT '[]',
    body_mapping_json TEXT NOT NULL DEFAULT '[]',
    timeout INTEGER NOT NULL DEFAULT 10,
    retry INTEGER NOT NULL DEFAULT 0,
    rate_limit INTEGER NOT NULL DEFAULT 0,
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    UNIQUE(user_id, endpoint, method)
);
CREATE INDEX IF NOT EXISTS idx_transapi_user ON trans_apis(user_id);

-- Proxy API 响应缓存：按 API + 参数组合缓存源 API 成功响应
CREATE TABLE IF NOT EXISTS api_cache (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    api_id TEXT NOT NULL REFERENCES apis(id) ON DELETE CASCADE,
    cache_key TEXT NOT NULL,
    status INTEGER NOT NULL,
    headers_json TEXT NOT NULL DEFAULT '{}',
    body BLOB,
    cached_at TEXT NOT NULL,
    UNIQUE(api_id, cache_key)
);

CREATE TABLE IF NOT EXISTS recovery_codes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    code_hash TEXT NOT NULL UNIQUE,
    used INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_recovery_user ON recovery_codes(user_id);

-- 跨进程共享的滑动窗口限流（gunicorn 多 worker 都读写同一张表）
CREATE TABLE IF NOT EXISTS rate_limit_hits (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    key_hash TEXT NOT NULL,
    hit_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_rate_key_time ON rate_limit_hits(key_hash, hit_at);
"""


def _write_initial_password(pwd: str):
    """初始密码落盘（仅首次），方便用户忘记终端输出时找回。"""
    try:
        path = DB_PATH.parent / "initial_password.txt"
        path.write_text(
            f"ADMIN email: {ADMIN_EMAIL}\nInitial password: {pwd}\n"
            "Change it on first login, then delete this file.\n",
            encoding="utf-8",
        )
        os.chmod(path, 0o600)
    except OSError:
        pass


def reset_admin_password():
    """命令行：python3 -c 'from app.db import reset_admin_password; print(reset_admin_password())'"""
    from werkzeug.security import generate_password_hash
    pwd = secrets.token_urlsafe(12)
    conn = connect()
    try:
        conn.execute(
            "UPDATE users SET password_hash = ?, must_change_password = 1, totp_secret = NULL, totp_enabled = 0 WHERE id = ?",
            (generate_password_hash(pwd, method="scrypt"), ADMIN_ID),
        )
        conn.commit()
    finally:
        conn.close()
    _write_initial_password(pwd)
    return f"New ADMIN password: {pwd} (email: {ADMIN_EMAIL})"


def utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def new_id() -> str:
    return secrets.token_hex(4)


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


def get_db() -> sqlite3.Connection:
    if "db" not in g:
        g.db = connect()
    return g.db


def close_db(_exc=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def _migrate(conn):
    """给旧版本库补列，避免用户删库升级。"""
    cols = {row["name"] for row in conn.execute("PRAGMA table_info(request_logs)")}
    if "client_ip" not in cols:
        conn.execute("ALTER TABLE request_logs ADD COLUMN client_ip TEXT")
    if "event_type" not in cols:
        conn.execute("ALTER TABLE request_logs ADD COLUMN event_type TEXT NOT NULL DEFAULT 'GATEWAY'")
    if "api_key_id" not in cols:
        conn.execute("ALTER TABLE request_logs ADD COLUMN api_key_id INTEGER")
    # 事件类型重命名：UPSTREAM_TRANSMISSION → UPSTREAM_TRANS（幂等）
    conn.execute("UPDATE request_logs SET event_type = 'UPSTREAM_TRANS' WHERE event_type = 'UPSTREAM_TRANSMISSION'")
    # 旧 TRANSMISSION 的清理放在新架构全面上线后单独执行（见 deploy/cleanup_legacy_transmission.py），
    # 避免旧代码在过渡期引用到已删除的表。


def canonical_params(value):
    """把参数组合序列化成稳定 JSON，并返回 SHA-256 key。"""
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (ValueError, TypeError):
            parsed = {"__raw__": value}
    else:
        parsed = value if isinstance(value, dict) else {"__raw__": value}
    text = json.dumps(parsed, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return text, hashlib.sha256(text.encode("utf-8")).hexdigest()


def _connect_path(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path), timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


def _column_names(conn, table: str) -> set:
    return {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}


def _ensure_column(conn, table: str, name: str, ddl: str):
    if name not in _column_names(conn, table):
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}")


def _load_json(value, default):
    try:
        loaded = json.loads(value or "")
    except (ValueError, TypeError):
        return default
    return loaded if isinstance(loaded, type(default)) else default


def _backfill_sheet_fields(conn, report):
    for sheet in conn.execute("SELECT id, fields_json FROM data_sheets"):
        fields = _load_json(sheet["fields_json"], [])
        for index, field in enumerate(fields):
            if not isinstance(field, dict):
                continue
            key = str(field.get("name") or "").strip()
            if not key:
                continue
            exists = conn.execute(
                "SELECT 1 FROM sheet_fields WHERE sheet_id = ? AND key = ?",
                (sheet["id"], key),
            ).fetchone()
            if exists:
                continue
            conn.execute(
                "INSERT INTO sheet_fields "
                "(id, sheet_id, key, label, json_type, ordinal, enabled, created_at) "
                "VALUES (?, ?, ?, ?, 'string', ?, ?, ?)",
                (
                    new_id(), sheet["id"], key, key,
                    int(field.get("order", index) or 0),
                    1 if field.get("enabled", True) else 0,
                    utcnow(),
                ),
            )
            report["fields_created"] += 1


def _backfill_origins(conn, report):
    origin_cols = _column_names(conn, "trans_origins")
    if "sheet_name" not in origin_cols:
        conn.execute(
            "UPDATE trans_origins SET result_path = COALESCE(result_path, ''), "
            "state = COALESCE(NULLIF(state, ''), 'DRAFT') WHERE result_path IS NULL OR state IS NULL OR state = ''"
        )
        return
    for origin in conn.execute("SELECT id, user_id, sheet_name FROM trans_origins"):
        sheet_id = origin["sheet_name"] and conn.execute(
            "SELECT id FROM data_sheets WHERE user_id = ? AND name = ?",
            (origin["user_id"], origin["sheet_name"]),
        ).fetchone()
        if not sheet_id:
            name = origin["sheet_name"] or "Imported data sheet"
            sheet_id = conn.execute(
                "SELECT id FROM data_sheets WHERE user_id = ? AND name = ?",
                (origin["user_id"], name),
            ).fetchone()
            if not sheet_id:
                sheet_id_value = new_id()
                conn.execute(
                    "INSERT INTO data_sheets (id, user_id, name, description, fields_json, created_at, updated_at) "
                    "VALUES (?, ?, ?, '', '[]', ?, ?)",
                    (sheet_id_value, origin["user_id"], name, utcnow(), utcnow()),
                )
                report["sheets_created"] += 1
            else:
                sheet_id_value = sheet_id["id"]
        else:
            sheet_id_value = sheet_id["id"]
        conn.execute(
            "UPDATE trans_origins SET sheet_id = ?, result_path = COALESCE(result_path, ''), "
            "state = COALESCE(NULLIF(state, ''), 'DRAFT') WHERE id = ?",
            (sheet_id_value, origin["id"]),
        )


def _backfill_rows(conn, report):
    rows = list(conn.execute("SELECT id, params_used FROM data_rows"))
    for row in rows:
        params_json, params_key = canonical_params(row["params_used"])
        conn.execute(
            "UPDATE data_rows SET params_json = ?, params_key = ? WHERE id = ?",
            (params_json, params_key, row["id"]),
        )
    report["rows_backfilled"] = len(rows)


def _backfill_trans_apis(conn, report):
    field_ids = {}
    for row in conn.execute("SELECT sheet_id, key, id FROM sheet_fields"):
        field_ids[(row["sheet_id"], row["key"])] = row["id"]

    def resolve_field(sheet_id, field_name):
        sid = str(sheet_id or "")
        name = str(field_name or "")
        field_id = field_ids.get((sid, name))
        if not field_id:
            report["unresolved_field_names"].append({"sheet_id": sid, "field": name})
        return field_id

    def resolve_pairs(raw):
        out = []
        for item in _load_json(raw, []):
            if not isinstance(item, dict):
                continue
            copied = dict(item)
            field_name = copied.pop("field", "")
            field_id = resolve_field(copied.get("sheet_id"), field_name)
            if field_id:
                copied["field_id"] = field_id
            out.append(copied)
        return out

    for api in conn.execute("SELECT * FROM trans_apis"):
        sheet_ids = [str(sid) for sid in _load_json(api["sheet_ids_json"], [])]
        joins = []
        for item in _load_json(api["joins_json"], []):
            if not isinstance(item, dict):
                continue
            copied = dict(item)
            left_id = resolve_field(copied.get("left_sheet"), copied.get("left_field"))
            right_id = resolve_field(copied.get("right_sheet"), copied.get("right_field"))
            if left_id:
                copied["left_field_id"] = left_id
            if right_id:
                copied["right_field_id"] = right_id
            joins.append(copied)
        projections = []
        for sheet_id, fields in _load_json(api["sheet_fields_json"], {}).items():
            if not isinstance(fields, dict):
                continue
            for field_name, enabled in fields.items():
                field_id = resolve_field(sheet_id, field_name)
                if field_id:
                    projections.append({
                        "sheet_id": str(sheet_id),
                        "field_id": field_id,
                        "enabled": bool(enabled),
                    })
        query_config = {
            "sheets": sheet_ids,
            "joins": joins,
            "projections": projections,
            "filters": resolve_pairs(api["param_mapping_json"]) + resolve_pairs(api["body_mapping_json"]),
        }
        conn.execute(
            "UPDATE trans_apis SET query_config_v2 = ? WHERE id = ?",
            (json.dumps(query_config, ensure_ascii=False), api["id"]),
        )


def apply_trans_rewrite_migration(conn):
    """应用 001_trans_rewrite。可重复执行，已应用的版本只重跑数据回填。"""
    report = {
        "version": TRANS_REWRITE_VERSION,
        "fields_created": 0,
        "sheets_created": 0,
        "rows_backfilled": 0,
        "unresolved_field_names": [],
    }
    _ensure_column(conn, "data_sheets", "description", "TEXT NOT NULL DEFAULT ''")
    _ensure_column(conn, "data_sheets", "updated_at", "TEXT NOT NULL DEFAULT ''")
    _ensure_column(conn, "trans_origins", "sheet_id", "TEXT")
    _ensure_column(conn, "trans_origins", "result_path", "TEXT NOT NULL DEFAULT ''")
    _ensure_column(conn, "trans_origins", "state", "TEXT NOT NULL DEFAULT 'DRAFT'")
    _ensure_column(conn, "data_rows", "params_key", "TEXT NOT NULL DEFAULT ''")
    _ensure_column(conn, "data_rows", "params_json", "TEXT NOT NULL DEFAULT '{}'")
    _ensure_column(conn, "trans_apis", "query_config_v2", "TEXT NOT NULL DEFAULT ''")

    migration_path = MIGRATIONS_DIR / "001_trans_rewrite.sql"
    conn.executescript(migration_path.read_text(encoding="utf-8"))

    _backfill_sheet_fields(conn, report)
    _backfill_origins(conn, report)
    _backfill_rows(conn, report)
    _backfill_trans_apis(conn, report)

    applied = conn.execute(
        "SELECT 1 FROM schema_migrations WHERE version = ?", (TRANS_REWRITE_VERSION,)
    ).fetchone()
    if not applied:
        conn.execute(
            "INSERT INTO schema_migrations (version, applied_at) VALUES (?, ?)",
            (TRANS_REWRITE_VERSION, utcnow()),
        )
        report["applied"] = True
    else:
        report["applied"] = False
    conn.commit()
    return report


def apply_trans_hardening_migration(conn):
    """Add batch idempotency key and normalize enabled Origins to the state enum."""
    report = {"version": TRANS_HARDENING_VERSION, "batch_keys_backfilled": 0, "states_normalized": 0}
    _ensure_column(conn, "fetch_batches", "batch_key", "TEXT")
    report["batch_keys_backfilled"] = conn.execute(
        "UPDATE fetch_batches SET batch_key = id WHERE batch_key IS NULL OR batch_key = ''"
    ).rowcount
    migration_path = MIGRATIONS_DIR / "002_trans_hardening.sql"
    conn.executescript(migration_path.read_text(encoding="utf-8"))
    if "enabled" in _column_names(conn, "trans_origins"):
        report["states_normalized"] = conn.execute(
            "UPDATE trans_origins SET state = 'ENABLED' WHERE state = 'VERIFIED' AND enabled = 1"
        ).rowcount
    else:
        report["states_normalized"] = 0
    applied = conn.execute(
        "SELECT 1 FROM schema_migrations WHERE version = ?", (TRANS_HARDENING_VERSION,)
    ).fetchone()
    if not applied:
        conn.execute(
            "INSERT INTO schema_migrations (version, applied_at) VALUES (?, ?)",
            (TRANS_HARDENING_VERSION, utcnow()),
        )
        report["applied"] = True
    else:
        report["applied"] = False
    conn.commit()
    return report


def apply_trans_cleanup_migration(conn):
    """Drop legacy TRANS columns after verifying no code path reads them."""
    report = {"version": TRANS_CLEANUP_VERSION, "dropped": []}
    applied = conn.execute(
        "SELECT 1 FROM schema_migrations WHERE version = ?", (TRANS_CLEANUP_VERSION,)
    ).fetchone()
    if applied:
        report["applied"] = False
        return report
    targets = (
        ("trans_origins", "sheet_name"),
        ("trans_origins", "verified"),
        ("trans_origins", "enabled"),
        ("data_sheets", "origin_id"),
    )
    for table, column in targets:
        if column in _column_names(conn, table):
            conn.execute(f"ALTER TABLE {table} DROP COLUMN {column}")
            report["dropped"].append(f"{table}.{column}")
    conn.execute(
        "INSERT INTO schema_migrations (version, applied_at) VALUES (?, ?)",
        (TRANS_CLEANUP_VERSION, utcnow()),
    )
    conn.commit()
    report["applied"] = True
    return report


def prune_trans_retention():
    """Delete old snapshots and logs when retention days are configured."""
    from config import TRANS_LOG_RETENTION_DAYS, TRANS_ROW_RETENTION_DAYS

    db = get_db()
    removed = {"rows": 0, "logs": 0}
    if TRANS_ROW_RETENTION_DAYS > 0:
        cutoff = datetime.now(timezone.utc) - timedelta(days=TRANS_ROW_RETENTION_DAYS)
        removed["rows"] = db.execute(
            "DELETE FROM data_rows WHERE fetched_at < ?", (cutoff.strftime("%Y-%m-%dT%H:%M:%SZ"),)
        ).rowcount
    if TRANS_LOG_RETENTION_DAYS > 0:
        cutoff = datetime.now(timezone.utc) - timedelta(days=TRANS_LOG_RETENTION_DAYS)
        removed["logs"] = db.execute(
            "DELETE FROM fetch_logs WHERE fetched_at < ?", (cutoff.strftime("%Y-%m-%dT%H:%M:%SZ"),)
        ).rowcount
    db.commit()
    return removed


def migrate_database(path=None, dry_run=False):
    """独立迁移入口。dry_run 时只修改临时副本。"""
    import shutil
    import tempfile

    source = Path(path) if path else DB_PATH
    temp_dir = tempfile.TemporaryDirectory()
    target = Path(temp_dir.name) / "migrate.db"
    if source.exists():
        source_conn = _connect_path(source)
        target_conn = sqlite3.connect(str(target))
        try:
            source_conn.backup(target_conn)
        finally:
            source_conn.close()
            target_conn.close()
    else:
        target_conn = sqlite3.connect(str(target))
        try:
            target_conn.executescript(SCHEMA)
            target_conn.commit()
        finally:
            target_conn.close()

    conn = _connect_path(target)
    try:
        report = apply_trans_rewrite_migration(conn)
        report["hardening"] = apply_trans_hardening_migration(conn)
        report["cleanup"] = apply_trans_cleanup_migration(conn)
    finally:
        conn.close()
    report["dry_run"] = dry_run
    report["source"] = str(source)
    if dry_run:
        shutil.rmtree(temp_dir.name, ignore_errors=True)
    else:
        source_conn = _connect_path(source)
        target_conn = _connect_path(target)
        try:
            target_conn.backup(source_conn)
        finally:
            source_conn.close()
            target_conn.close()
        temp_dir.cleanup()
    return report


def init_db(seed_admin=True):
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = connect()
    try:
        conn.executescript(SCHEMA)
        _migrate(conn)
        apply_trans_rewrite_migration(conn)
        apply_trans_hardening_migration(conn)
        apply_trans_cleanup_migration(conn)
        conn.commit()
        if seed_admin:
            row = conn.execute("SELECT 1 FROM users WHERE id = ?", (ADMIN_ID,)).fetchone()
            if not row:
                from werkzeug.security import generate_password_hash
                pwd = secrets.token_urlsafe(12)
                conn.execute(
                    "INSERT INTO users (id, created_at, email, password_hash, role, must_change_password) "
                    "VALUES (?, ?, ?, ?, 'ADMIN', 1)",
                    (ADMIN_ID, utcnow(), ADMIN_EMAIL, generate_password_hash(pwd, method="scrypt")),
                )
                conn.commit()
                _write_initial_password(pwd)
                print(f"[APIMiao] ADMIN account created: {ADMIN_EMAIL}")
                print(f"[APIMiao] Initial password: {pwd}")
                print("[APIMiao] You will be asked to change it on first login.")
    finally:
        conn.close()


def q(sql: str, args=()):
    return get_db().execute(sql, args)


def one(sql: str, args=()):
    return get_db().execute(sql, args).fetchone()


def all_rows(sql: str, args=()):
    return get_db().execute(sql, args).fetchall()


def execute(sql: str, args=()):
    db = get_db()
    cur = db.execute(sql, args)
    db.commit()
    return cur


def api_config(row) -> dict:
    try:
        return json.loads(row["config"] or "{}")
    except (ValueError, TypeError):
        return {}


def save_api_config(api_id: str, cfg: dict):
    execute("UPDATE apis SET config = ? WHERE id = ?", (json.dumps(cfg, ensure_ascii=False), api_id))


def log_request(user_id, api_id, method, endpoint, status, success, duration_ms=0, detail="",
                client_ip="", event_type="GATEWAY"):
    # 当前请求使用的 API Key（由网关鉴权时写入 g），用于日志里显示 key 名称
    key_id = getattr(g, "current_key_id", None) if has_request_context() else None
    execute(
        "INSERT INTO request_logs (user_id, api_id, method, endpoint, status, success, created_at, duration_ms, detail, client_ip, event_type, api_key_id) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (user_id, api_id, method, endpoint, status, 1 if success else 0, utcnow(), duration_ms,
         detail, client_ip or "", event_type, key_id),
    )


def check_rate_limit(key_hash, limit):
    """跨进程滑动窗口限流，返回 True 表示放行。

    用独立连接 + autocommit + BEGIN IMMEDIATE，避免与 flask.g 上的请求事务混用，
    并靠 SQLite 写锁保证多 worker 并发下计数准确。
    """
    if not limit:
        return True
    import time as _time
    now = _time.time()
    cutoff = now - 60
    conn = sqlite3.connect(DB_PATH, timeout=10, isolation_level=None)
    try:
        conn.execute("PRAGMA busy_timeout = 5000")
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("DELETE FROM rate_limit_hits WHERE hit_at < ?", (cutoff,))
        count = conn.execute(
            "SELECT COUNT(*) FROM rate_limit_hits WHERE key_hash = ? AND hit_at >= ?",
            (key_hash, cutoff),
        ).fetchone()[0]
        if count >= limit:
            conn.rollback()
            return False
        conn.execute("INSERT INTO rate_limit_hits (key_hash, hit_at) VALUES (?, ?)", (key_hash, now))
        conn.commit()
        return True
    except sqlite3.Error:
        try:
            conn.rollback()
        except sqlite3.Error:
            pass
        return True  # 存储异常时放行，避免拖垮全部调用
    finally:
        conn.close()
