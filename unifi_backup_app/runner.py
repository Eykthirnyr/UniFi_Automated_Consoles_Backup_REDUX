from __future__ import annotations

from . import create_app
from .state import log_console


def run() -> None:
    app = create_app()
    log_console("Starting Flask web server on port 5000.")
    # Never enable debug/reloader here: the reloader starts a second process that runs
    # its own scheduler and worker, so every backup ran twice and each process killed
    # the other's Chrome. Debug mode also exposes the Werkzeug console on the network.
    app.run(host="0.0.0.0", port=5000, debug=False, use_reloader=False, threaded=True)


if __name__ == "__main__":
    run()
