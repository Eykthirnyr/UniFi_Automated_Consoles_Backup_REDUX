from __future__ import annotations

from datetime import datetime, timezone, timedelta
import io
import json
import os
import zipfile

from flask import (
    Response,
    flash,
    redirect,
    render_template,
    request,
    send_file,
    send_from_directory,
    url_for,
)

from .data import (
    appdata,
    get_user_timezone,
    get_user_timezone_label,
    localize_utc_str_to_user_tz,
    save_appdata,
)
from .notifications import send_test_email
from .scheduling import init_schedule_jobs
from .scheduler import scheduler
from .settings import AVAILABLE_TIMEZONES, BACKUP_ROOT, DEFAULT_TZ
from .state import (
    current_task_status,
    task_queue,
    enqueue_task,
    enqueue_task_unbounded,
    MAX_QUEUE_SIZE,
    SCHEDULED_BACKUP_TASK_PREFIX,
    queue_has_task_prefix,
    current_task_has_prefix,
)
from .tasks import (
    attempt_console_backup,
    remove_old_cookie,
    reset_processes_logic,
    store_cookies_json,
    test_cookie_access_logic,
)


def register_routes(app) -> None:
    @app.route("/")
    def dashboard():
        return render_template(
            "dashboard.html",
            appdata=appdata,
            available_tzs=AVAILABLE_TIMEZONES,
            tz_label=get_user_timezone_label(),
        )

    @app.route("/status_stream")
    def status_stream():
        def event_stream():
            while True:
                data = {
                    "current_task": current_task_status.copy(),
                    "queue_size": task_queue.qsize(),
                    "master_logged_in": appdata.get("master_logged_in", False),
                }
                last_cookie_check = appdata.get("last_cookie_check")
                data["last_cookie_check_local"] = (
                    localize_utc_str_to_user_tz(last_cookie_check)
                    if last_cookie_check
                    else ""
                )
                if data["current_task"].get("start_time"):
                    data["current_task"]["start_time_local"] = localize_utc_str_to_user_tz(
                        data["current_task"]["start_time"]
                    )
                    try:
                        dt_utc = datetime.strptime(
                            data["current_task"]["start_time"], "%Y-%m-%d %H:%M:%S"
                        ).replace(tzinfo=timezone.utc)
                        elapsed = datetime.now(timezone.utc) - dt_utc
                        data["current_task"]["elapsed_seconds"] = max(
                            0, int(elapsed.total_seconds())
                        )
                    except ValueError:
                        data["current_task"]["elapsed_seconds"] = None

                logs_reversed = reversed(appdata["logs"])
                data_logs = []
                for entry in logs_reversed:
                    local_ts = localize_utc_str_to_user_tz(entry["timestamp"])
                    data_logs.append(
                        {"timestamp": local_ts, "message": entry["message"]}
                    )
                data["logs"] = data_logs

                data_consoles = []
                for console in appdata["consoles"]:
                    local_time = ""
                    if console.get("last_backup_time"):
                        local_time = localize_utc_str_to_user_tz(console["last_backup_time"])
                    status_raw = console.get("last_backup_status", "")
                    if status_raw.startswith("Success") or "Succeeded after retry" in status_raw:
                        status_display = "Success"
                    elif "Failed after 3" in status_raw:
                        status_display = "Failed after 3 retries"
                    elif status_raw:
                        status_display = "Failed"
                    else:
                        status_display = "Failed"
                    data_consoles.append(
                        {
                            "id": console["id"],
                            "name": console["name"],
                            "backup_url": console.get("backup_url", ""),
                            "status": status_display,
                            "time": local_time,
                            "excluded": console.get("exclude_from_schedule", False),
                        }
                    )
                data["consoles"] = data_consoles
                queue_items = [item[0] for item in list(task_queue.queue)]
                data["queue_items"] = queue_items

                current_task_name = data["current_task"].get("task_name") or ""
                scheduled_running = current_task_name.startswith(
                    SCHEDULED_BACKUP_TASK_PREFIX
                )
                scheduled_positions = [
                    idx + 1
                    for idx, item in enumerate(queue_items)
                    if item.startswith(SCHEDULED_BACKUP_TASK_PREFIX)
                ]
                if scheduled_running:
                    data["scheduled_queue_position"] = 1
                    data["scheduled_queue_size"] = 1 + len(scheduled_positions)
                elif scheduled_positions:
                    data["scheduled_queue_position"] = scheduled_positions[0]
                    data["scheduled_queue_size"] = len(scheduled_positions)
                else:
                    data["scheduled_queue_position"] = 0
                    data["scheduled_queue_size"] = 0

                backup_job = scheduler.get_job("BackupJob")
                next_backup_str = "N/A"
                next_backup_seconds = 0
                if backup_job and backup_job.next_run_time:
                    now_ = datetime.now(backup_job.next_run_time.tzinfo)
                    delta = backup_job.next_run_time - now_
                    sec_ = int(delta.total_seconds())
                    if sec_ < 0:
                        sec_ = 0
                    next_backup_seconds = sec_
                    next_backup_str = _format_timedelta(delta)

                data["next_backup_time_str"] = next_backup_str
                data["next_backup_time_seconds"] = next_backup_seconds
                data["current_time_local"] = localize_utc_str_to_user_tz(
                    datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
                )

                yield "event: message\n" + "data: " + json.dumps(data) + "\n\n"
                import time

                time.sleep(1)

        return Response(event_stream(), mimetype="text/event-stream")

    @app.route("/manual_relogin", methods=["POST"])
    def manual_relogin():
        remove_old_cookie()
        appdata["master_logged_in"] = False
        save_appdata()
        flash("Cookies cleared. Please upload new cookies below.", "info")
        return redirect(url_for("dashboard", _anchor="manual-cookie-upload"))

    @app.route("/upload_cookies", methods=["POST"])
    def upload_cookies():
        cookies_file = request.files.get("cookies_file")
        if not cookies_file:
            flash("Please select a cookies JSON file to upload.", "danger")
            return redirect(url_for("dashboard"))

        try:
            cookies_data = json.load(cookies_file)
        except json.JSONDecodeError:
            flash("Invalid JSON file. Please upload a valid cookies JSON.", "danger")
            return redirect(url_for("dashboard"))

        if not isinstance(cookies_data, list):
            flash("Cookies JSON must be a list of cookie objects.", "danger")
            return redirect(url_for("dashboard"))

        store_cookies_json(cookies_data)
        save_appdata()
        flash("Cookies uploaded successfully. You are now logged in.", "success")
        return redirect(url_for("dashboard"))

    @app.route("/import_consoles", methods=["POST"])
    def import_consoles():
        consoles_file = request.files.get("consoles_file")
        if not consoles_file:
            flash("Please select a JSON file to import.", "danger")
            return redirect(url_for("dashboard"))

        try:
            payload = json.load(consoles_file)
        except json.JSONDecodeError:
            flash("Invalid JSON file. Please upload a valid export.", "danger")
            return redirect(url_for("dashboard"))

        if isinstance(payload, dict):
            consoles = payload.get("consoles", [])
            master_logged_in = payload.get("master_logged_in")
        elif isinstance(payload, list):
            consoles = payload
            master_logged_in = None
        else:
            flash("JSON must be a list of consoles or an object with a consoles key.", "danger")
            return redirect(url_for("dashboard"))

        if not isinstance(consoles, list):
            flash("Consoles must be a list of objects.", "danger")
            return redirect(url_for("dashboard"))

        sanitized = []
        for console in consoles:
            if not isinstance(console, dict):
                continue
            name = str(console.get("name", "")).strip()
            backup_url = str(console.get("backup_url", "")).strip()
            if not name or not backup_url:
                continue
            sanitized.append(
                {
                    "id": int(console.get("id", 0)) or 0,
                    "name": name,
                    "backup_url": backup_url,
                    "last_backup_status": console.get("last_backup_status", "Unknown"),
                    "last_backup_time": console.get("last_backup_time"),
                    "exclude_from_schedule": bool(
                        console.get("exclude_from_schedule", False)
                    ),
                }
            )

        if not sanitized:
            flash("No valid consoles found in the file.", "danger")
            return redirect(url_for("dashboard"))

        replace_existing = request.form.get("replace_existing") == "1"
        if replace_existing:
            appdata["consoles"] = sanitized
        else:
            existing_by_name = {c["name"]: c for c in appdata.get("consoles", [])}
            for console in sanitized:
                existing_by_name[console["name"]] = console
            appdata["consoles"] = list(existing_by_name.values())

        max_id = max((c["id"] for c in appdata["consoles"]), default=0)
        for console in appdata["consoles"]:
            if not console["id"]:
                max_id += 1
                console["id"] = max_id

        if master_logged_in is not None:
            appdata["master_logged_in"] = bool(master_logged_in)

        save_appdata()
        flash(f"Imported {len(sanitized)} consoles successfully.", "success")
        return redirect(url_for("dashboard"))

    @app.route("/update_smtp", methods=["POST"])
    def update_smtp():
        smtp = appdata.get("smtp", {})

        smtp["enabled"] = "smtp_enabled" in request.form
        smtp["host"] = request.form.get("smtp_host", "").strip()
        smtp["port"] = int(request.form.get("smtp_port", "465") or 465)
        smtp["username"] = request.form.get("smtp_username", "").strip()
        password = request.form.get("smtp_password", "").strip()
        if password:
            smtp["password"] = password
        smtp["sender"] = request.form.get("smtp_sender", "").strip()
        smtp["recipients"] = request.form.get("smtp_recipients", "").strip()
        smtp["use_ssl"] = request.form.get("smtp_use_ssl") == "1"
        smtp["notify_cookies_expired"] = "notify_cookies_expired" in request.form
        smtp["notify_connectivity_failed"] = "notify_connectivity_failed" in request.form
        smtp["notify_backup_failed"] = "notify_backup_failed" in request.form
        smtp["notify_backup_success"] = "notify_backup_success" in request.form

        appdata["smtp"] = smtp
        save_appdata()
        flash("SMTP settings updated.", "success")
        return redirect(url_for("dashboard"))

    @app.route("/test_smtp", methods=["POST"])
    def test_smtp():
        ok, message = send_test_email()
        flash(message, "success" if ok else "danger")
        return redirect(url_for("dashboard"))

    @app.route("/start_schedule_now", methods=["POST"])
    def start_schedule_now():
        already_running_or_queued = current_task_has_prefix(
            SCHEDULED_BACKUP_TASK_PREFIX
        ) or queue_has_task_prefix(SCHEDULED_BACKUP_TASK_PREFIX)

        enqueue_task_unbounded("ScheduledBackup => Pass1 => allConsoles", _start_backup)

        if already_running_or_queued:
            flash(
                "A scheduled/override backup task is already running. This request was queued and will run next.",
                "warning",
            )
        else:
            flash("Scheduled backup queued (manual override).", "success")
        return redirect(url_for("dashboard"))

    @app.route("/test_cookies", methods=["POST"])
    def test_cookies():
        if enqueue_task("CookieTest", test_cookie_access_logic):
            flash("Cookie test queued. Check logs for the result.", "info")
        else:
            flash(
                f"Queue is full (max {MAX_QUEUE_SIZE} tasks). Please wait for tasks to finish.",
                "danger",
            )
        return redirect(url_for("dashboard"))

    @app.route("/reset_processes", methods=["POST"])
    def reset_processes():
        if enqueue_task("ResetProcesses", reset_processes_logic):
            flash("Process reset queued. Check logs for cleanup status.", "info")
        else:
            flash(
                f"Queue is full (max {MAX_QUEUE_SIZE} tasks). Please wait for tasks to finish.",
                "danger",
            )
        return redirect(url_for("dashboard"))

    @app.route("/add_console", methods=["POST"])
    def add_console():
        name = request.form.get("name", "").strip()
        curl = request.form.get("backup_url", "").strip()
        if not name or not curl:
            flash("Name and Backup URL are required", "danger")
            return redirect(url_for("dashboard"))

        new_id = max((c["id"] for c in appdata["consoles"]), default=0) + 1
        console_obj = {
            "id": new_id,
            "name": name,
            "backup_url": curl,
            "last_backup_status": "Unknown",
            "last_backup_time": None,
            "exclude_from_schedule": False,
        }
        appdata["consoles"].append(console_obj)
        save_appdata()
        flash(f"Console '{name}' added.", "success")
        return redirect(url_for("dashboard"))

    @app.route("/remove_console/<int:cid>", methods=["POST"])
    def remove_console(cid):
        found = False
        for console in appdata["consoles"]:
            if console["id"] == cid:
                appdata["consoles"].remove(console)
                found = True
                break
        if found:
            save_appdata()
            flash("Console removed.", "success")
        else:
            flash("Console not found.", "danger")
        return redirect(url_for("dashboard"))

    @app.route("/toggle_console_schedule/<int:cid>", methods=["POST"])
    def toggle_console_schedule(cid):
        console = next((x for x in appdata["consoles"] if x["id"] == cid), None)
        if not console:
            flash("Console not found.", "danger")
            return redirect(url_for("dashboard"))
        console["exclude_from_schedule"] = not console.get("exclude_from_schedule", False)
        save_appdata()
        state = "excluded from" if console["exclude_from_schedule"] else "included in"
        flash(f"Console '{console['name']}' {state} scheduled backups.", "success")
        return redirect(url_for("dashboard"))

    @app.route("/manual_backup/<int:cid>", methods=["POST"])
    def manual_backup(cid):
        if not appdata.get("master_logged_in", False):
            flash("Not logged in. Please do manual login first.", "danger")
            return redirect(url_for("dashboard"))

        console = next((x for x in appdata["consoles"] if x["id"] == cid), None)
        if not console:
            flash("Console not found.", "danger")
            return redirect(url_for("dashboard"))

        if enqueue_task(f"ManualBackup-{console['name']}", attempt_console_backup, [console]):
            flash(f"Backup for '{console['name']}' queued...", "info")
        else:
            flash(
                f"Queue is full (max {MAX_QUEUE_SIZE} tasks). Please wait for tasks to finish.",
                "danger",
            )
        return redirect(url_for("dashboard"))

    @app.route("/update_schedule", methods=["POST"])
    def update_schedule():
        schedule = appdata["schedule"]

        schedule["backup_enabled"] = "backup_enabled" in request.form
        schedule["backup_value"] = int(request.form.get("backup_value", "1"))
        schedule["backup_unit"] = request.form.get("backup_unit", "days")

        schedule["check_enabled"] = "check_enabled" in request.form
        schedule["check_value"] = int(request.form.get("check_value", "4"))
        schedule["check_unit"] = request.form.get("check_unit", "hours")

        if schedule["backup_unit"] == "minutes" and schedule["backup_value"] < 15:
            schedule["backup_value"] = 15
            flash("Backup interval set to minimum of 15 minutes.", "warning")
        if schedule["check_unit"] == "minutes" and schedule["check_value"] < 15:
            schedule["check_value"] = 15
            flash("Check interval set to minimum of 15 minutes.", "warning")

        tz_choice = request.form.get("tz_choice", DEFAULT_TZ)
        if tz_choice not in AVAILABLE_TIMEZONES:
            tz_choice = DEFAULT_TZ
        appdata["tz_choice"] = tz_choice

        save_appdata()
        init_schedule_jobs()
        flash("Schedules & Timezone updated.", "success")
        return redirect(url_for("dashboard"))

    @app.route("/download_latest_backup/<int:cid>")
    def download_latest_backup(cid):
        console = next((x for x in appdata["consoles"] if x["id"] == cid), None)
        if not console:
            flash("Console not found.", "danger")
            return redirect(url_for("dashboard"))

        if not console.get("last_backup_time"):
            flash("No backup found for that console.", "danger")
            return redirect(url_for("dashboard"))

        day_str_utc = console["last_backup_time"].split(" ")[0]
        folder_path = BACKUP_ROOT / day_str_utc
        if not folder_path.exists():
            flash("No matching backup folder found.", "danger")
            return redirect(url_for("dashboard"))

        files = [f for f in os.listdir(folder_path) if f.startswith(console["name"] + "_")]
        if not files:
            flash("No matching backup file found for that console's last backup time.", "danger")
            return redirect(url_for("dashboard"))

        files.sort(key=lambda x: os.path.getmtime(folder_path / x), reverse=True)
        latest_file = files[0]
        return send_from_directory(str(folder_path), latest_file, as_attachment=True)

    @app.route("/download_today_backups", methods=["GET"])
    def download_today_backups():
        now_local = datetime.now(timezone.utc).astimezone(get_user_timezone())
        today_local_str = now_local.strftime("%Y-%m-%d")
        tz_label = get_user_timezone_label()
        today_str_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        folder_path = BACKUP_ROOT / today_str_utc
        if not folder_path.exists():
            flash(f"No backups found for today ({today_local_str} {tz_label}).", "danger")
            return redirect(url_for("dashboard"))

        file_list = os.listdir(folder_path)
        if not file_list:
            flash(f"No backups found for today ({today_local_str} {tz_label}).", "danger")
            return redirect(url_for("dashboard"))

        mem_zip = io.BytesIO()
        with zipfile.ZipFile(mem_zip, mode="w") as zf:
            for filename in file_list:
                full_path = folder_path / filename
                if full_path.is_file():
                    zf.write(full_path, arcname=filename)

        mem_zip.seek(0)
        zip_filename = f"Backups_{today_local_str}_{tz_label}.zip"
        return send_file(
            mem_zip,
            as_attachment=True,
            download_name=zip_filename,
            mimetype="application/zip",
        )

    @app.route("/console_history/<int:cid>")
    def console_history(cid):
        console = next((x for x in appdata["consoles"] if x["id"] == cid), None)
        if not console:
            return render_template(
                "history.html",
                console=None,
                files_list=[],
                tz_label=get_user_timezone_label(),
                back_link=url_for("dashboard"),
                page=1,
                total_pages=1,
            )

        console_name = console["name"]
        user_tz = get_user_timezone()
        page = max(int(request.args.get("page", "1") or "1"), 1)
        page_size = 100

        files_list = []
        for folder_name in os.listdir(BACKUP_ROOT):
            folder_path = BACKUP_ROOT / folder_name
            if not folder_path.is_dir():
                continue
            for fname in os.listdir(folder_path):
                if fname.startswith(console_name + "_"):
                    fpath = folder_path / fname
                    if fpath.is_file():
                        mtime = os.path.getmtime(fpath)
                        dt_utc = datetime.fromtimestamp(mtime, timezone.utc)
                        dt_local = dt_utc.astimezone(user_tz)
                        files_list.append(
                            {
                                "date_folder": folder_name,
                                "date_display": dt_local.strftime("%Y-%m-%d"),
                                "filename": fname,
                                "datetime_display": dt_local.strftime("%Y-%m-%d %H:%M:%S"),
                                "sort_ts": dt_utc.timestamp(),
                            }
                        )

        files_list.sort(key=lambda x: x["sort_ts"], reverse=True)
        total_items = len(files_list)
        total_pages = max(1, (total_items + page_size - 1) // page_size)
        page = min(page, total_pages)
        start_idx = (page - 1) * page_size
        end_idx = start_idx + page_size
        page_items = files_list[start_idx:end_idx]

        return render_template(
            "history.html",
            console=console,
            files_list=page_items,
            tz_label=get_user_timezone_label(),
            back_link=url_for("dashboard"),
            page=page,
            total_pages=total_pages,
        )

    @app.route("/download_logs")
    def download_logs():
        tz_label = get_user_timezone_label()
        output = io.StringIO()
        for entry in appdata.get("logs", []):
            local_ts = localize_utc_str_to_user_tz(entry["timestamp"])
            output.write(f"[{local_ts}] - {entry['message']}\n")
        mem = io.BytesIO(output.getvalue().encode("utf-8"))
        mem.seek(0)
        filename = f"logs_{datetime.now(timezone.utc).strftime('%Y-%m-%d_%H%M%S')}_{tz_label}.txt"
        return send_file(
            mem,
            as_attachment=True,
            download_name=filename,
            mimetype="text/plain",
        )

    @app.route("/export_consoles")
    def export_consoles():
        consoles_payload = {
            "consoles": [
                {
                    "name": console.get("name", ""),
                    "backup_url": console.get("backup_url", ""),
                    "exclude_from_schedule": console.get("exclude_from_schedule", False),
                }
                for console in appdata.get("consoles", [])
            ]
        }
        mem = io.BytesIO(json.dumps(consoles_payload, indent=2).encode("utf-8"))
        mem.seek(0)
        return send_file(
            mem,
            as_attachment=True,
            download_name="consoles_export.json",
            mimetype="application/json",
        )

    @app.route("/download_backup/<date_folder>/<path:filename>")
    def download_specific_backup(date_folder, filename):
        folder_path = BACKUP_ROOT / date_folder
        return send_from_directory(str(folder_path), filename, as_attachment=True)

    def _format_timedelta(td):
        total_seconds = int(td.total_seconds())
        if total_seconds < 0:
            return "N/A"
        hours, remainder = divmod(total_seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        days, hours = divmod(hours, 24)
        parts = []
        if days == 1:
            parts.append("1 day")
        elif days > 1:
            parts.append(f"{days} days")
        parts.append(f"{hours:02d}:{minutes:02d}:{seconds:02d}")
        return ", ".join(parts)

    def _start_backup():
        from .tasks import scheduled_backup_job_logic

        scheduled_backup_job_logic()


__all__ = ["register_routes"]
