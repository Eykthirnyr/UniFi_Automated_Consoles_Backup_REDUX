from __future__ import annotations

from datetime import datetime, timezone, timedelta

from .data import appdata, add_app_log
from .scheduler import scheduler
from .state import (
    log_console,
    enqueue_task,
    enqueue_task_unbounded,
    queue_has_task_prefix,
    SCHEDULED_BACKUP_TASK_PREFIX,
)
from .tasks import scheduled_backup_job_logic, test_cookie_access_logic
from .worker import start_worker

COOKIE_TEST_TASK_NAME = "CookieTest"
INTERVAL_UNITS = ("minutes", "hours", "days")

_last_backup_enqueue_at: datetime | None = None


def _eligible_consoles_count() -> int:
    return len([c for c in appdata.get("consoles", []) if not c.get("exclude_from_schedule")])


def _backup_interval_delta() -> timedelta:
    schedule = appdata.get("schedule", {})
    value = max(1, int(schedule.get("backup_value", 1) or 1))
    unit = schedule.get("backup_unit", "days")
    if unit == "minutes":
        return timedelta(minutes=value)
    if unit == "hours":
        return timedelta(hours=value)
    return timedelta(days=value)


def scheduled_connectivity_check_job() -> None:
    log_console("APScheduler => scheduled_connectivity_check_job triggered")
    if queue_has_task_prefix(COOKIE_TEST_TASK_NAME):
        log_console("Connectivity check already queued => skipped.")
        return
    enqueue_task(COOKIE_TEST_TASK_NAME, test_cookie_access_logic)
    add_app_log("Connectivity check queued.")


def scheduled_backup_job() -> None:
    global _last_backup_enqueue_at
    log_console("APScheduler => scheduled_backup_job triggered")
    _last_backup_enqueue_at = datetime.now(timezone.utc)

    # A slow or failing run must not pile up a new pass at every interval.
    if queue_has_task_prefix(SCHEDULED_BACKUP_TASK_PREFIX):
        add_app_log("Scheduled backup skipped: a scheduled backup is already waiting in the queue.")
        return

    total_items = _eligible_consoles_count()
    enqueue_task_unbounded(
        "ScheduledBackup => Pass1 => allConsoles",
        scheduled_backup_job_logic,
        total_items=total_items,
    )
    add_app_log("Scheduled backup queued.")


def backup_schedule_watchdog_job() -> None:
    start_worker()

    schedule = appdata.get("schedule", {})
    if not schedule.get("backup_enabled", False):
        return

    backup_job = scheduler.get_job("BackupJob")
    if not backup_job:
        add_app_log("Backup watchdog: BackupJob missing, rebuilding schedule.")
        init_schedule_jobs()
        return

    if _last_backup_enqueue_at is None:
        return
    interval = _backup_interval_delta()
    if datetime.now(timezone.utc) - _last_backup_enqueue_at > (interval + timedelta(minutes=1)):
        add_app_log("Backup watchdog: interval exceeded, forcing scheduled backup queue.")
        scheduled_backup_job()


def _interval_kwargs(value, unit: str) -> dict:
    if unit not in INTERVAL_UNITS:
        unit = "days"
    return {unit: max(1, int(value))}


def init_schedule_jobs() -> None:
    global _last_backup_enqueue_at
    for job_id in ["BackupJob", "ConnectivityCheckJob", "BackupWatchdogJob"]:
        if scheduler.get_job(job_id):
            scheduler.remove_job(job_id)

    schedule = appdata["schedule"]

    if schedule["backup_enabled"]:
        scheduler.add_job(
            id="BackupJob",
            func=scheduled_backup_job,
            trigger="interval",
            coalesce=False,
            max_instances=1,
            misfire_grace_time=3600,
            replace_existing=True,
            **_interval_kwargs(schedule["backup_value"], schedule["backup_unit"]),
        )
        # The new job starts counting from now; without this reset the watchdog would
        # compare against the old timetable and force an extra backup.
        _last_backup_enqueue_at = datetime.now(timezone.utc)

    if schedule["check_enabled"]:
        scheduler.add_job(
            id="ConnectivityCheckJob",
            func=scheduled_connectivity_check_job,
            trigger="interval",
            coalesce=False,
            max_instances=1,
            misfire_grace_time=3600,
            replace_existing=True,
            **_interval_kwargs(schedule["check_value"], schedule["check_unit"]),
        )

    scheduler.add_job(
        id="BackupWatchdogJob",
        func=backup_schedule_watchdog_job,
        trigger="interval",
        minutes=1,
        coalesce=True,
        max_instances=1,
        replace_existing=True,
    )
