from __future__ import annotations

from flask_apscheduler import APScheduler

scheduler = APScheduler()


def init_scheduler(app) -> None:
    scheduler.init_app(app)
    scheduler.start()
