from __future__ import annotations

from collections import deque
from datetime import datetime, timezone
import queue

console_log_buffer = deque(maxlen=2000)

MAX_QUEUE_SIZE = 10
task_queue: queue.Queue = queue.Queue()
current_task_status = {
    "running": False,
    "task_name": "",
    "step": "",
    "start_time": None,
}


def log_console(message: str) -> None:
    timestamp_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{timestamp_utc}] {message}"
    print(line)
    console_log_buffer.append(line)


def is_task_running() -> bool:
    return current_task_status["running"]


def start_task(step_msg: str) -> None:
    now_utc_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    current_task_status["running"] = True
    current_task_status["task_name"] = step_msg
    current_task_status["step"] = step_msg
    current_task_status["start_time"] = now_utc_str


def end_task() -> None:
    current_task_status["running"] = False
    current_task_status["task_name"] = ""
    current_task_status["step"] = ""
    current_task_status["start_time"] = None


def queue_has_task_prefix(prefix: str) -> bool:
    return any(item[0].startswith(prefix) for item in list(task_queue.queue))


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
            if item[0].startswith(prefix):
                removed += 1
                continue
            task_queue.queue.append(item)
    return removed


def enqueue_task(
    task_name: str,
    func,
    args: list | None = None,
    kwargs: dict | None = None,
) -> bool:
    if task_queue.qsize() >= MAX_QUEUE_SIZE:
        return False
    task_queue.put((task_name, func, args or [], kwargs or {}))
    return True
