from __future__ import annotations

from .data import appdata, add_app_log
from .scheduler import scheduler
from .state import (
    is_task_running,
    current_task_status,
    task_queue,
    log_console,
    current_task_has_prefix,
    queue_has_task_prefix,
)
from .tasks import scheduled_backup_job_logic, scheduled_connectivity_check_logic


def scheduled_connectivity_check_job() -> None:
    log_console("APScheduler => scheduled_connectivity_check_job triggered")
    task_queue.put(("ConnectivityCheck", scheduled_connectivity_check_logic, [], {}))


def scheduled_backup_job() -> None:
    log_console("APScheduler => scheduled_backup_job triggered")

    if current_task_has_prefix("ManualBackup-") or queue_has_task_prefix("ManualBackup-"):
        add_app_log("Manual backups running/queued => skipping scheduled backup.")
        return
    if is_task_running() and current_task_status["step"].startswith("ScheduledBackup =>"):
        add_app_log("Conflict: a scheduled backup is already running => skip new one.")
        return
    for item in list(task_queue.queue):
        if item[0].startswith("ScheduledBackup =>"):
            add_app_log("Conflict: a scheduled backup is queued => skip new one.")
            return

    task_queue.put(
        ("ScheduledBackup => Pass1 => allConsoles", scheduled_backup_job_logic, [], {})
    )


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
