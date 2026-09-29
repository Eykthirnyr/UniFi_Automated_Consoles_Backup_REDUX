from __future__ import annotations

import threading

from .data import add_app_log
from .state import (
    TaskCancelled,
    cancel_requested,
    end_task,
    is_task_cancelled,
    log_console,
    start_task,
    task_lock,
    task_queue,
)
from .tasks import cleanup_leftover_chrome

_worker_thread: threading.Thread | None = None
_worker_lock = threading.Lock()


def _run_task(task_meta: dict) -> None:
    task_name = task_meta["task_name"]
    if is_task_cancelled(task_meta):
        # Taken off the queue just before "Cancel all" cleared it.
        log_console(f"[Worker] Task '{task_name}' cancelled before start.")
        return
    start_task(task_meta)
    try:
        add_app_log(f"Worker: Starting task '{task_name}'")
        log_console(f"[Worker] Starting task '{task_name}'")
        task_meta["func"](*task_meta["args"], **task_meta["kwargs"])
    except Exception as exc:
        # Killing the browser on cancel surfaces as a WebDriver error, not TaskCancelled.
        if isinstance(exc, TaskCancelled) or cancel_requested():
            log_console(f"[Worker] Task '{task_name}' => cancelled.")
            add_app_log(f"Task '{task_name}' => cancelled by user.")
        else:
            log_console(f"[Worker] Task '{task_name}' => EXCEPTION: {exc}")
            add_app_log(f"Task '{task_name}' => ERROR: {exc}")
    finally:
        end_task()
        cleanup_leftover_chrome()


def _worker_loop() -> None:
    while True:
        _, _, task_meta = task_queue.get()
        try:
            with task_lock:
                _run_task(task_meta)
        except Exception as exc:
            # Logging or cleanup itself failed (disk full, ...): keep the worker alive,
            # otherwise every later task would wait in the queue forever.
            log_console(f"[Worker] Unexpected error around '{task_meta.get('task_name')}': {exc}")
        finally:
            task_queue.task_done()


def start_worker() -> None:
    global _worker_thread
    with _worker_lock:
        if _worker_thread and _worker_thread.is_alive():
            return
        if _worker_thread is not None:
            log_console("[Worker] Worker thread was dead, restarting it.")
        _worker_thread = threading.Thread(target=_worker_loop, name="backup-worker", daemon=True)
        _worker_thread.start()
