from __future__ import annotations

from . import create_app
from .state import log_console


def run() -> None:
    app = create_app()
    log_console("Starting Flask with real file download logic, reversing logs, etc.")
    app.run(debug=True, host="0.0.0.0", port=5000)


if __name__ == "__main__":
    run()
