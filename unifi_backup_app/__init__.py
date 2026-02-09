from __future__ import annotations

from flask import Flask

from .data import load_appdata
from .routes import register_routes
from .scheduler import init_scheduler
from .scheduling import init_schedule_jobs
from .settings import SECRET_KEY
from .worker import start_worker


class Config:
    SCHEDULER_API_ENABLED = True


def create_app() -> Flask:
    app = Flask(__name__)
    app.config.from_object(Config)
    app.config["SECRET_KEY"] = SECRET_KEY

    init_scheduler(app)
    load_appdata()
    init_schedule_jobs()
    start_worker()
    register_routes(app)

    return app
