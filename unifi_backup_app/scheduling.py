from __future__ import annotations

from .data import appdata, add_app_log
from .scheduler import scheduler
from .state import (
    log_console,
    enqueue_task,
    enqueue_task_unbounded,
    MAX_QUEUE_SIZE,
    SCHEDULED_BACKUP_TASK_PREFIX,
    queue_has_task_prefix,
    current_task_has_prefix,
)
from .tasks import scheduled_backup_job_logic, test_cookie_access_logic


def scheduled_connectivity_check_job() -> None:
    log_console("APScheduler => scheduled_connectivity_check_job triggered")
    if enqueue_task("CookieTest", test_cookie_access_logic):
        add_app_log("Connectivity check queued.")
    else:
        add_app_log(
            f"Connectivity check skipped: queue is full (max {MAX_QUEUE_SIZE} tasks)."
        )


def scheduled_backup_job() -> None:
    log_console("APScheduler => scheduled_backup_job triggered")

    if current_task_has_prefix(SCHEDULED_BACKUP_TASK_PREFIX) or queue_has_task_prefix(
        SCHEDULED_BACKUP_TASK_PREFIX
    ):
        enqueue_task_unbounded(
            "ScheduledBackup => Pass1 => allConsoles",
            scheduled_backup_job_logic,
        )
        add_app_log(
            "Scheduled backup already running/queued. Added another scheduled backup to queue."
        )
        return

    enqueue_task_unbounded("ScheduledBackup => Pass1 => allConsoles", scheduled_backup_job_logic)
    add_app_log("Scheduled backup queued.")


def init_schedule_jobs() -> None:
    if scheduler.get_job("BackupJob"):
        scheduler.remove_job("BackupJob")
    if scheduler.get_job("ConnectivityCheckJob"):
        scheduler.remove_job("ConnectivityCheckJob")

    schedule = appdata["schedule"]

    if schedule["backup_enabled"]:
        b_val = schedule["backup_value"]
        b_unit = schedule["backup_unit"]
        if b_unit == "minutes":
            scheduler.add_job("BackupJob", scheduled_backup_job, trigger="interval", minutes=b_val)
        elif b_unit == "hours":
            scheduler.add_job("BackupJob", scheduled_backup_job, trigger="interval", hours=b_val)
        elif b_unit == "days":
            scheduler.add_job("BackupJob", scheduled_backup_job, trigger="interval", days=b_val)

    if schedule["check_enabled"]:
        c_val = schedule["check_value"]
        c_unit = schedule["check_unit"]
        if c_unit == "minutes":
            scheduler.add_job(
                "ConnectivityCheckJob",
                scheduled_connectivity_check_job,
                trigger="interval",
                minutes=c_val,
            )
        elif c_unit == "hours":
            scheduler.add_job(
                "ConnectivityCheckJob",
                scheduled_connectivity_check_job,
                trigger="interval",
                hours=c_val,
            )
        elif c_unit == "days":
            scheduler.add_job(
                "ConnectivityCheckJob",
                scheduled_connectivity_check_job,
                trigger="interval",
                days=c_val,
            )
