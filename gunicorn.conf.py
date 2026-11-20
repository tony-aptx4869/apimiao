"""gunicorn 配置。启动：gunicorn -c gunicorn.conf.py wsgi:app
所有参数来自 config.ini 的 [gunicorn] 分组。

生产环境前置 Caddy 反代（自动 HTTPS，WebAuthn 通行密钥要求安全上下文）。
定时任务已拆到独立进程 scheduler_worker.py，Web 进程无论多少 worker 都不跑 APScheduler；
限流计数存 SQLite，多 worker 共享。因此这里可以放心开多 worker。
"""
from config import BIND as bind, WORKERS as workers, THREADS as threads
from config import ACCESSLOG as accesslog, ERRORLOG as errorlog, LOGLEVEL as loglevel
from config import CONTROL_SOCKET_DISABLE, CONTROL_SOCKET_PATH

worker_class = "gthread"
timeout = 120
graceful_timeout = 30
keepalive = 5

# 不要开启 preload_app：每个 worker 需独立加载 wsgi:app，避免共享状态。
preload_app = False

# gunicorn 25.1+ 的运行时控制套接字。默认禁用：它默认往 $HOME/.gunicorn/ 写 socket，
# 服务用户（如 www-data，home=/var/www）常无写权限，会在日志刷 Permission denied。
# 我们不用 gunicornc，禁用即可；如需启用，在 config.ini 里改并给对应目录写权限。
control_socket_disable = CONTROL_SOCKET_DISABLE
if CONTROL_SOCKET_PATH:
    control_socket = CONTROL_SOCKET_PATH
