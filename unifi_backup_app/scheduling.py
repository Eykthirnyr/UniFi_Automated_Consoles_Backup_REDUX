from __future__ import annotations

from datetime import datetime, timezone, timedelta

from .data import appdata, add_app_log
from .scheduler import scheduler
from .state import (
    log_console,
    enqueue_task,
    enqueue_task_unbounded,
    SCHEDULED_BACKUP_TASK_PREFIX,
)
from .tasks import scheduled_backup_job_logic, test_cookie_access_logic

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
    enqueue_task("CookieTest", test_cookie_access_logic)
    add_app_log("Connectivity check queued.")


def scheduled_backup_job() -> None:
    global _last_backup_enqueue_at
    log_console("APScheduler => scheduled_backup_job triggered")

    total_items = _eligible_consoles_count()
    enqueue_task_unbounded(
        "ScheduledBackup => Pass1 => allConsoles",
        scheduled_backup_job_logic,
        total_items=total_items,
    )
    _last_backup_enqueue_at = datetime.now(timezone.utc)
    add_app_log("Scheduled backup queued.")


def backup_schedule_watchdog_job() -> None:
    schedule = appdata.get("schedule", {})
    if not schedule.get("backup_enabled", False):
        return

    backup_job = scheduler.get_job("BackupJob")
    if not backup_job:
        add_app_log("Backup watchdog: BackupJob missing, rebuilding schedule.")
        init_schedule_jobs()
        return

    global _last_backup_enqueue_at
    if _last_backup_enqueue_at is None:
        return

    interval = _backup_interval_delta()
    if datetime.now(timezone.utc) - _last_backup_enqueue_at > (interval + timedelta(minutes=1)):
        add_app_log("Backup watchdog: interval exceeded, forcing scheduled backup queue.")
        scheduled_backup_job()


def init_schedule_jobs() -> None:
    for job_id in ["BackupJob", "ConnectivityCheckJob", "BackupWatchdogJob"]:
        if scheduler.get_job(job_id):
            scheduler.remove_job(job_id)

    schedule = appdata["schedule"]

    if schedule["backup_enabled"]:
        b_val = max(1, int(schedule["backup_value"]))
        b_unit = schedule["backup_unit"]
        kwargs = {
            "id": "BackupJob",
            "func": scheduled_backup_job,
            "trigger": "interval",
            "coalesce": False,
            "max_instances": 1,
            "misfire_grace_time": 3600,
            "replace_existing": True,
        }
        if b_unit == "minutes":
            kwargs["minutes"] = b_val
        elif b_unit == "hours":
            kwargs["hours"] = b_val
        elif b_unit == "days":
            kwargs["days"] = b_val
        scheduler.add_job(**kwargs)

    if schedule["check_enabled"]:
        c_val = max(1, int(schedule["check_value"]))
        c_unit = schedule["check_unit"]
        kwargs = {
            "id": "ConnectivityCheckJob",
            "func": scheduled_connectivity_check_job,
            "trigger": "interval",
            "coalesce": False,
            "max_instances": 1,
            "misfire_grace_time": 3600,
            "replace_existing": True,
        }
        if c_unit == "minutes":
            kwargs["minutes"] = c_val
        elif c_unit == "hours":
            kwargs["hours"] = c_val
        elif c_unit == "days":
            kwargs["days"] = c_val
        scheduler.add_job(**kwargs)

    scheduler.add_job(
        id="BackupWatchdogJob",
        func=backup_schedule_watchdog_job,
        trigger="interval",
        minutes=1,
        coalesce=True,
        max_instances=1,
        replace_existing=True,
    )
