#!/usr/bin/env python3
"""APIMiao Transmission 定时任务独立进程。

生产环境（gunicorn 多 worker）必须单独运行本进程，Web 进程不要跑调度器，
否则每个 worker 都会执行一遍定时任务。

运行：python3 scheduler_worker.py
"""
from app import create_app
from app.api.scheduler import run_standalone_scheduler

app = create_app()

if __name__ == "__main__":
    run_standalone_scheduler(app)
