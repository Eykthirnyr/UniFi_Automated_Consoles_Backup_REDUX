from __future__ import annotations

import threading

from .data import add_app_log
from .state import task_queue, start_task, end_task, log_console
from .tasks import cleanup_leftover_chrome

_worker_thread: threading.Thread | None = None


def _worker_loop() -> None:
    while True:
        _, _, task_meta = task_queue.get()
        task_name = task_meta["task_name"]
        func = task_meta["func"]
        args = task_meta["args"]
        kwargs = task_meta["kwargs"]
        start_task(task_meta)
        add_app_log(f"Worker: Starting task '{task_name}'")
        log_console(f"[Worker] Starting task '{task_name}'")

        try:
            func(*args, **kwargs)
        except Exception as exc:
            add_app_log(f"Task '{task_name}' => ERROR: {exc}")
            log_console(f"[Worker] Task '{task_name}' => EXCEPTION: {exc}")

        end_task()
        task_queue.task_done()
        cleanup_leftover_chrome()


def start_worker() -> None:
    global _worker_thread
    if _worker_thread and _worker_thread.is_alive():
        return
    _worker_thread = threading.Thread(target=_worker_loop, daemon=True)
    _worker_thread.start()
