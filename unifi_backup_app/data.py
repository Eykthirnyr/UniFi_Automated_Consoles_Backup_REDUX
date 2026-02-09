from __future__ import annotations

import json
import re
from datetime import datetime, timezone, timedelta, tzinfo
from zoneinfo import ZoneInfo

from .settings import APPDATA_JSON, DEFAULT_TZ, AVAILABLE_TIMEZONES

appdata: dict = {}


def _parse_fixed_offset(tz_name: str) -> tzinfo | None:
    match = re.match(r"^(UTC|GMT)([+-])(\d{1,2})$", tz_name)
    if not match:
        return None
    sign = 1 if match.group(2) == "+" else -1
    hours = int(match.group(3))
    if hours > 14:
        return None
    return timezone(timedelta(hours=sign * hours))


def get_user_timezone() -> tzinfo:
    tz_name = appdata.get("tz_choice", DEFAULT_TZ)
    fixed_offset = _parse_fixed_offset(tz_name)
    if fixed_offset:
        return fixed_offset
    try:
        return ZoneInfo(tz_name)
    except Exception:
        return ZoneInfo("UTC")


def get_user_timezone_label() -> str:
    return appdata.get("tz_choice", DEFAULT_TZ)


def localize_utc_str_to_user_tz(utc_str: str) -> str:
    try:
        dt_utc = datetime.strptime(utc_str, "%Y-%m-%d %H:%M:%S")
        dt_utc = dt_utc.replace(tzinfo=timezone.utc)
        dt_local = dt_utc.astimezone(get_user_timezone())
        return dt_local.strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return utc_str


def load_appdata() -> None:
    if not APPDATA_JSON.exists():
        data = _default_appdata()
        appdata.clear()
        appdata.update(data)
        save_appdata()
        return

    with APPDATA_JSON.open("r", encoding="utf-8") as handle:
        data = json.load(handle)

    data = _normalize_appdata(data)
    appdata.clear()
    appdata.update(data)
    save_appdata()


def _default_appdata() -> dict:
    return {
        "master_logged_in": False,
        "last_cookie_check": None,
        "consoles": [],
        "logs": [],
        "smtp": {
            "enabled": False,
            "host": "",
            "port": 465,
            "username": "",
            "password": "",
            "sender": "",
            "recipients": "",
            "use_ssl": True,
            "notify_cookies_expired": True,
            "notify_connectivity_failed": True,
            "notify_backup_failed": True,
            "notify_backup_success": False,
        },
        "schedule": {
            "backup_enabled": True,
            "backup_value": 1,
            "backup_unit": "days",
            "check_enabled": True,
            "check_value": 4,
            "check_unit": "hours",
        },
        "tz_choice": DEFAULT_TZ,
    }


def _normalize_appdata(data: dict) -> dict:
    data.setdefault("master_logged_in", False)
    data.setdefault("last_cookie_check", None)
    data.setdefault("consoles", [])
    data.setdefault("logs", [])
    for console in data["consoles"]:
        if not isinstance(console, dict):
            continue
        console.setdefault("last_backup_status", "Unknown")
        console.setdefault("last_backup_time", None)
        console.setdefault("exclude_from_schedule", False)
    smtp = data.setdefault("smtp", {})
    smtp.setdefault("enabled", False)
    smtp.setdefault("host", "")
    smtp.setdefault("port", 465)
    smtp.setdefault("username", "")
    smtp.setdefault("password", "")
    smtp.setdefault("sender", "")
    smtp.setdefault("recipients", "")
    smtp.setdefault("use_ssl", True)
    smtp.setdefault("notify_cookies_expired", True)
    smtp.setdefault("notify_connectivity_failed", True)
    smtp.setdefault("notify_backup_failed", True)
    smtp.setdefault("notify_backup_success", False)
    schedule = data.setdefault("schedule", {})
    for key, default_val in [
        ("backup_enabled", True),
        ("backup_value", 1),
        ("backup_unit", "days"),
        ("check_enabled", True),
        ("check_value", 4),
        ("check_unit", "hours"),
    ]:
        schedule.setdefault(key, default_val)
    tz_choice = data.get("tz_choice", DEFAULT_TZ)
    if tz_choice not in AVAILABLE_TIMEZONES:
        tz_choice = DEFAULT_TZ
    data["tz_choice"] = tz_choice
    return data


def save_appdata() -> None:
    with APPDATA_JSON.open("w", encoding="utf-8") as handle:
        json.dump(appdata, handle, indent=2)


def add_app_log(message: str) -> None:
    now_utc_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    entry = {"timestamp": now_utc_str, "message": message}
    appdata["logs"].append(entry)
    appdata["logs"] = appdata["logs"][-300:]
    save_appdata()
