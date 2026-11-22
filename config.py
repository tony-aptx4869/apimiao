"""APIMiao 全局配置。

所有配置集中在项目根目录的 config.ini（复制自 config.ini.example）。
首次启动若没有 config.ini，会自动从模板复制一份，开箱即用。

优先级：环境变量覆盖 > config.ini > 模板默认值。
环境变量覆盖是可选的应急手段，键名形如 APIMIAO_<SECTION>_<KEY>，
例如 APIMIAO_WEBAUTHN_RP_ID、APIMIAO_SECURITY_SECRET_KEY。
"""
import configparser
import os
import shutil
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DB_PATH = DATA_DIR / "apimiao.db"
CONFIG_PATH = BASE_DIR / "config.ini"
EXAMPLE_PATH = BASE_DIR / "config.ini.example"

# 首次运行：没有 config.ini 就从模板生成，保证开箱即用
if not CONFIG_PATH.exists() and EXAMPLE_PATH.exists():
    shutil.copy(EXAMPLE_PATH, CONFIG_PATH)
    print(f"[APIMiao] created {CONFIG_PATH.name} from template; edit it to customize.")

_parser = configparser.ConfigParser()
_parser.read(CONFIG_PATH, encoding="utf-8")


def _get(section, key, default=""):
    """读取配置：环境变量优先，其次 ini，最后默认值。"""
    env_key = f"APIMIAO_{section.upper()}_{key.upper()}"
    if env_key in os.environ:
        return os.environ[env_key]
    return _parser.get(section, key, fallback=default)


def _bool(value) -> bool:
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def _int(value, default: int) -> int:
    try:
        return int(str(value).strip())
    except (ValueError, TypeError):
        return default


# ---------------- server：开发服务器 ----------------
HOST = _get("server", "host", "0.0.0.0")
PORT = _int(_get("server", "port", "5099"), 5099)
DEBUG = _bool(_get("server", "debug", "0"))
# 服务器公网出口 IP（源 API 实际看到的 IP）。留空则运行时自动探测。
# 用于 UPSTREAM_* 日志，让日志里的 IP 反映「源 API 看到的来源」而非调用方 IP。
EGRESS_IP = _get("server", "egress_ip", "").strip()

# ---------------- gunicorn：生产服务器 ----------------
BIND = _get("gunicorn", "bind", "127.0.0.1:8080")
WORKERS = _int(_get("gunicorn", "workers", "2"), 2)
THREADS = _int(_get("gunicorn", "threads", "4"), 4)
ACCESSLOG = _get("gunicorn", "accesslog", "-")
ERRORLOG = _get("gunicorn", "errorlog", "-")
LOGLEVEL = _get("gunicorn", "loglevel", "info")
# gunicorn 运行时控制套接字：默认禁用（避免服务用户家目录无写权限导致 Permission denied）
CONTROL_SOCKET_DISABLE = _bool(_get("gunicorn", "control_socket_disable", "true"))
CONTROL_SOCKET_PATH = _get("gunicorn", "control_socket", "").strip()

# ---------------- webauthn：通行密钥 ----------------
RP_ID = _get("webauthn", "rp_id", "localhost")
RP_NAME = _get("webauthn", "rp_name", "APIMiao")
ORIGIN = _get("webauthn", "origin", "") or f"http://localhost:{PORT}"

# ---------------- security：安全 ----------------
SECRET_KEY = _get("security", "secret_key", "").strip() or os.urandom(32).hex()
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
SESSION_COOKIE_SECURE = _bool(_get("security", "cookie_secure", "false"))

# ---------------- admin：默认管理员 ----------------
ADMIN_ID = "0000005f"
ADMIN_EMAIL = _get("admin", "email", "miao@apimiao.local")

PASSKEY_MAX = 9

# API 网关路径模式（gateway.prefix）：
#   split  -> /p/<endpoint> 仅 PROXY，/t/<endpoint> 仅 TRANSMISSION（新默认）
#   gate   -> /gate/<endpoint> 不区分类型（旧）
#   none   -> /<endpoint> 不区分类型（旧）
_gateway_prefix_raw = _get("gateway", "prefix", "split").strip().strip("/").lower()
if _gateway_prefix_raw in ("", "none", "off"):
    GATEWAY_MODE = "none"
elif _gateway_prefix_raw == "gate":
    GATEWAY_MODE = "gate"
else:
    GATEWAY_MODE = "split"


def endpoint_prefix(api_type):
    """返回该类型 API 在 URL 中的路径前缀（不含结尾斜杠），供网关与列表展示共用。"""
    if GATEWAY_MODE == "split":
        return "t" if str(api_type).upper() == "TRANSMISSION" else "p"
    if GATEWAY_MODE == "gate":
        return "gate"
    return ""


# ---------------- general：全站时区 ----------------
# 用于每日统计分组（图表按"本地日"归集）。库里始终存 UTC，展示/统计时按此时区换算。
# 值为 IANA 时区名（如 Asia/Shanghai），留空或无效则默认 Asia/Shanghai。
TIMEZONE = _get("general", "timezone", "Asia/Shanghai").strip() or "Asia/Shanghai"

# ---------------- trans：TRANS 模块 ----------------
# 列表笛卡尔积组合数上限（0 或负数 = 不限制）。新建/编辑 Origin 保存时与定时采集时都校验。
CARTESIAN_LIMIT = _int(_get("trans", "cartesian_limit", "10000"), 10000)

DATA_DIR.mkdir(parents=True, exist_ok=True)
