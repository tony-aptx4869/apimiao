"""SQLite 存储层。WAL 模式 + 外键约束，UTC 时间统一 ISO8601 字符串。"""
import json
import secrets
import sqlite3
from datetime import datetime, timezone

from flask import current_app, g

from config import DB_PATH, ADMIN_ID, ADMIN_EMAIL

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
    event_type TEXT NOT NULL DEFAULT 'GATEWAY'
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

CREATE TABLE IF NOT EXISTS transmission_data (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    api_id TEXT NOT NULL REFERENCES apis(id) ON DELETE CASCADE,
    captured_at TEXT NOT NULL,
    payload_json TEXT,
    headers_json TEXT,
    cookies_json TEXT,
    raw_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_trans_api_time ON transmission_data(api_id, captured_at);

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
        (DB_PATH.parent / "initial_password.txt").write_text(
            f"ADMIN email: {ADMIN_EMAIL}\nInitial password: {pwd}\n"
            "Change it on first login, then delete this file.\n",
            encoding="utf-8",
        )
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


def init_db(seed_admin=True):
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = connect()
    try:
        conn.executescript(SCHEMA)
        _migrate(conn)
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
    execute(
        "INSERT INTO request_logs (user_id, api_id, method, endpoint, status, success, created_at, duration_ms, detail, client_ip, event_type) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (user_id, api_id, method, endpoint, status, 1 if success else 0, utcnow(), duration_ms,
         detail, client_ip or "", event_type),
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
