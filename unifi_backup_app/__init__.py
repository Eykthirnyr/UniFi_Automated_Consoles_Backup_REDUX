from __future__ import annotations

from flask import Flask

from .data import load_appdata
from .routes import register_routes
from .scheduler import init_scheduler
from .scheduling import init_schedule_jobs
from .settings import SECRET_KEY
from .tasks import cleanup_browser_leftovers
from .worker import start_worker


class Config:
    # The Flask-APScheduler REST API lets anyone reaching port 5000 schedule arbitrary
    # Python functions; the dashboard does not use it.
    SCHEDULER_API_ENABLED = False


def create_app() -> Flask:
    app = Flask(__name__)
    app.config.from_object(Config)
    app.config["SECRET_KEY"] = SECRET_KEY

    init_scheduler(app)
    load_appdata()
    cleanup_browser_leftovers()
    init_schedule_jobs()
    start_worker()
    register_routes(app)

    return app
