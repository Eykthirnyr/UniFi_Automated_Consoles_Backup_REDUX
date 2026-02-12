from __future__ import annotations

from collections import deque
from datetime import datetime, timezone
import itertools
import queue

console_log_buffer = deque(maxlen=2000)

SCHEDULED_BACKUP_TASK_PREFIX = "ScheduledBackup"
_DEFAULT_PRIORITY = 10
_HIGH_PRIORITY = 0
_sequence_counter = itertools.count()
task_queue: queue.PriorityQueue = queue.PriorityQueue()

current_task_status = {
    "running": False,
    "task_name": "",
    "step": "",
    "start_time": None,
    "total_items": 0,
    "completed_items": 0,
}


def log_console(message: str) -> None:
    timestamp_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{timestamp_utc}] {message}"
    print(line)
    console_log_buffer.append(line)


def is_task_running() -> bool:
    return current_task_status["running"]


def start_task(task_meta: dict) -> None:
    now_utc_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    current_task_status["running"] = True
    current_task_status["task_name"] = task_meta.get("task_name", "")
    current_task_status["step"] = task_meta.get("task_name", "")
    current_task_status["start_time"] = now_utc_str
    total_items = int(task_meta.get("total_items", 1) or 1)
    current_task_status["total_items"] = max(1, total_items)
    current_task_status["completed_items"] = 0


def update_current_task_progress(completed_items: int, step_msg: str | None = None) -> None:
    total_items = int(current_task_status.get("total_items", 1) or 1)
    current_task_status["completed_items"] = max(0, min(int(completed_items), total_items))
    if step_msg:
        current_task_status["step"] = step_msg


def end_task() -> None:
    current_task_status["running"] = False
    current_task_status["task_name"] = ""
    current_task_status["step"] = ""
    current_task_status["start_time"] = None
    current_task_status["total_items"] = 0
    current_task_status["completed_items"] = 0


def _queue_snapshot() -> list[dict]:
    # Accessing .queue provides a thread-safe snapshot while holding the mutex.
    with task_queue.mutex:
        ordered = sorted(list(task_queue.queue), key=lambda item: (item[0], item[1]))
        return [item[2] for item in ordered]


def queue_has_task_prefix(prefix: str) -> bool:
    return any(item["task_name"].startswith(prefix) for item in _queue_snapshot())


def current_task_has_prefix(prefix: str) -> bool:
    task_name = current_task_status.get("task_name") or ""
    step = current_task_status.get("step") or ""
    return task_name.startswith(prefix) or step.startswith(prefix)


def purge_queued_tasks_with_prefix(prefix: str) -> int:
    removed = 0
    with task_queue.mutex:
        items = list(task_queue.queue)
        task_queue.queue.clear()
        for item in items:
            task_meta = item[2]
            if task_meta["task_name"].startswith(prefix):
                removed += 1
                continue
            task_queue.queue.append(item)
    return removed


def _enqueue_task(
    task_name: str,
    func,
    args: list | None = None,
    kwargs: dict | None = None,
    *,
    priority: int = _DEFAULT_PRIORITY,
    total_items: int = 1,
) -> bool:
    task_meta = {
        "task_name": task_name,
        "func": func,
        "args": args or [],
        "kwargs": kwargs or {},
        "total_items": max(1, int(total_items or 1)),
    }
    task_queue.put((priority, next(_sequence_counter), task_meta))
    return True


def enqueue_task(
    task_name: str,
    func,
    args: list | None = None,
    kwargs: dict | None = None,
    total_items: int = 1,
) -> bool:
    return _enqueue_task(
        task_name,
        func,
        args=args,
        kwargs=kwargs,
        total_items=total_items,
    )


def enqueue_task_unbounded(
    task_name: str,
    func,
    args: list | None = None,
    kwargs: dict | None = None,
    *,
    priority: int | None = None,
    total_items: int = 1,
) -> bool:
    selected_priority = _HIGH_PRIORITY if task_name.startswith(SCHEDULED_BACKUP_TASK_PREFIX) else _DEFAULT_PRIORITY
    if priority is not None:
        selected_priority = priority
    return _enqueue_task(
        task_name,
        func,
        args=args,
        kwargs=kwargs,
        priority=selected_priority,
        total_items=total_items,
    )


def get_queue_items() -> list[str]:
    return [item["task_name"] for item in _queue_snapshot()]


def get_queue_total_items() -> int:
    return sum(int(item.get("total_items", 1) or 1) for item in _queue_snapshot())
