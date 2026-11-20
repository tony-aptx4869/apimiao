"""APIMiao 应用工厂。"""
from pathlib import Path

from flask import Flask

from config import SECRET_KEY, SESSION_COOKIE_HTTPONLY, SESSION_COOKIE_SAMESITE, SESSION_COOKIE_SECURE
from . import i18n
from .db import close_db, init_db

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def create_app():
    app = Flask(__name__, template_folder=str(PROJECT_ROOT / "templates"), static_folder=str(PROJECT_ROOT / "static"))
    app.config.update(
        SECRET_KEY=SECRET_KEY,
        SESSION_COOKIE_HTTPONLY=SESSION_COOKIE_HTTPONLY,
        SESSION_COOKIE_SAMESITE=SESSION_COOKIE_SAMESITE,
        SESSION_COOKIE_SECURE=SESSION_COOKIE_SECURE,
        MAX_CONTENT_LENGTH=16 * 1024 * 1024,
    )

    init_db()
    app.teardown_appcontext(close_db)
    i18n.init_app(app)
    app.jinja_env.globals.update(t=i18n.t, LANG=i18n.get_lang, LANGS=i18n.LANGS)

    # 延迟导入，避免循环依赖
    from .auth import bp as auth_bp
    from .control import bp as control_bp
    from .api import bp as api_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(control_bp)
    app.register_blueprint(api_bp)

    # 调度器不在这里启动：Web 进程（尤其 gunicorn 多 worker）不得启动 APScheduler。
    # 单进程开发用 main.py 的 start_embedded_scheduler；生产用 scheduler_worker.py 独立进程。
    return app
