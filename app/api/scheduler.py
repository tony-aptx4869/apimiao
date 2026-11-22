"""Transmission 定时任务调度器。

两种运行模式：
- 内嵌模式 start_embedded_scheduler(app)：随 Web 进程启动，靠请求触发配置同步，适合
  `python3 main.py` 单进程开发。
- 独立模式 run_standalone_scheduler(app)：由 scheduler_worker.py 独占运行，自带
  定时同步（每 60s），Web 进程（含 gunicorn 多 worker）不要启动它，避免任务重复执行。

任务函数内部需要 Flask app context，因为 db helpers 依赖 flask.g。
"""
import hashlib
import time
from datetime import datetime, timezone

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from ..db import all_rows, one, prune_trans_retention

_scheduler = None
SYNC_INTERVAL = 60


def _job(app, origin_id, job_id):
    """在 app context 内执行 TRANS Origin 采集。"""
    from .trans_origin import run_batch
    with app.app_context():
        row = one("SELECT * FROM trans_origins WHERE id = ? AND state = 'ENABLED'", (origin_id,))
        if row:
            bucket = datetime.now(timezone.utc).strftime("%Y%m%d%H%M")
            run_batch(dict(row), triggered_by="SCHEDULE", batch_key=f"{job_id}:{bucket}")
            prune_trans_retention()


def _trigger_for(cfg):
    """每个 HH:MM 生成一个独立 trigger，避免 hour 与 minute 交叉组合。"""
    triggers = []
    if cfg.get("frequency") == "WEEK":
        days = cfg.get("days") or []
        dow = ",".join(str(d) for d in days) if days else "*"
    else:
        dow = "*"
    for index, ts in enumerate(cfg.get("times") or []):
        try:
            h, m = str(ts).split(":")
            hour, minute = int(h), int(m)
            if not (0 <= hour <= 23 and 0 <= minute <= 59):
                continue
            trigger = CronTrigger(hour=str(hour), minute=str(minute), day_of_week=dow)
        except (ValueError, TypeError):
            continue
        triggers.append((f"t{index}_{hour:02d}{minute:02d}", trigger))
    return triggers


def _origin_schedule(row):
    """从 trans_origins row 构造 _trigger_for 需要的 dict。"""
    import json
    def load(key):
        try:
            return json.loads(row[key] or "[]")
        except (ValueError, TypeError, KeyError):
            return []
    return {"times": load("times_json"), "days": load("days_json"), "frequency": row["frequency"]}


def sync_jobs(app):
    """从数据库重建全部定时任务（删除已不存在/禁用的，更新变化的）。"""
    if _scheduler is None:
        return
    with app.app_context():
        rows = all_rows("SELECT id, frequency, days_json, times_json FROM trans_origins WHERE state = 'ENABLED'")
        desired = {}
        for row in rows:
            signature = hashlib.sha256(
                f"{row['frequency']}|{row['days_json']}|{row['times_json']}".encode("utf-8")
            ).hexdigest()[:8]
            for suffix, trigger in _trigger_for(_origin_schedule(row)):
                desired[f"trans_{row['id']}_{suffix}_{signature}"] = (row["id"], trigger)

    current = {job.id for job in _scheduler.get_jobs() if job.id.startswith("trans_")}
    for job_id in current - set(desired):
        _scheduler.remove_job(job_id)
    for job_id, (api_id, trigger) in desired.items():
        _scheduler.add_job(
            _job, trigger, args=[app, api_id, job_id], id=job_id,
            replace_existing=True, max_instances=1, coalesce=True,
        )


def _ensure_scheduler():
    global _scheduler
    if _scheduler is None:
        _scheduler = BackgroundScheduler(timezone="UTC")
        _scheduler.start()
        import atexit
        atexit.register(_shutdown)
    return _scheduler


def _shutdown():
    global _scheduler
    if _scheduler is not None:
        try:
            if _scheduler.running:
                _scheduler.shutdown(wait=False)
        except Exception:
            pass
        _scheduler = None


def start_embedded_scheduler(app):
    """单进程开发模式：随 Web 进程启动，靠 HTTP 请求触发配置同步。"""
    _ensure_scheduler()
    sync_jobs(app)

    @app.before_request
    def _maybe_reschedule():
        now = datetime.now(timezone.utc)
        if not hasattr(app, "_last_schedule") or (now - app._last_schedule).total_seconds() > 60:
            app._last_schedule = now
            sync_jobs(app)


def run_standalone_scheduler(app):
    """独立进程模式：自带配置同步，阻塞常驻。Web 进程不要调用它。"""
    scheduler = _ensure_scheduler()
    sync_jobs(app)
    scheduler.add_job(
        sync_jobs, "interval", seconds=SYNC_INTERVAL, args=[app],
        id="__sync_jobs__", replace_existing=True, max_instances=1,
    )
    print(f"[APIMiao] scheduler started (standalone, sync every {SYNC_INTERVAL}s). Ctrl+C to stop.")
    try:
        while True:
            time.sleep(1)
    except (KeyboardInterrupt, SystemExit):
        _shutdown()
