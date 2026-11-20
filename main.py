#!/usr/bin/env python3
"""APIMiao — API 中转站平台。运行：python3 main.py"""
from app import create_app
from config import HOST, PORT, DEBUG

app = create_app()


def _start_dev_scheduler():
    """单进程开发模式：Web 与调度器同进程。生产请改用 scheduler_worker.py。"""
    from app.api.scheduler import start_embedded_scheduler
    start_embedded_scheduler(app)


if __name__ == "__main__":
    _start_dev_scheduler()
    print(f"[APIMiao] running at http://localhost:{PORT}")
    print("[APIMiao] dev mode: scheduler embedded. For production use gunicorn + scheduler_worker.py.")
    app.run(host=HOST, port=PORT, debug=DEBUG, threaded=True, use_reloader=False)
