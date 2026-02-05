from __future__ import annotations

from datetime import datetime, timezone
import smtplib
from email.message import EmailMessage

from .data import appdata, get_user_timezone, get_user_timezone_label


def _smtp_config() -> dict:
    return appdata.get("smtp", {})


def _smtp_recipients(recipients_raw: str) -> list[str]:
    return [r.strip() for r in recipients_raw.split(",") if r.strip()]


def _format_local_time() -> str:
    tz = get_user_timezone()
    now_local = datetime.now(timezone.utc).astimezone(tz)
    tz_label = get_user_timezone_label()
    return f"{now_local.strftime('%Y-%m-%d %H:%M:%S')} {tz_label}"


def send_notification(subject: str, body: str) -> None:
    smtp = _smtp_config()
    if not smtp.get("enabled"):
        return

    host = smtp.get("host", "")
    port = int(smtp.get("port", 0) or 0)
    username = smtp.get("username", "")
    password = smtp.get("password", "")
    sender = smtp.get("sender", "")
    recipients_raw = smtp.get("recipients", "")
    use_ssl = bool(smtp.get("use_ssl", True))

    recipients = _smtp_recipients(recipients_raw)
    if not host or not port or not sender or not recipients:
        return

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = ", ".join(recipients)
    msg.set_content(body)

    if use_ssl:
        with smtplib.SMTP_SSL(host, port, timeout=20) as server:
            if username:
                server.login(username, password)
            server.send_message(msg)
    else:
        with smtplib.SMTP(host, port, timeout=20) as server:
            if username:
                server.login(username, password)
            server.send_message(msg)


def send_test_email() -> tuple[bool, str]:
    smtp = _smtp_config()
    if not smtp.get("enabled"):
        return False, "SMTP is disabled. Enable it before sending a test."

    host = smtp.get("host", "")
    port = int(smtp.get("port", 0) or 0)
    sender = smtp.get("sender", "")
    recipients_raw = smtp.get("recipients", "")
    recipients = _smtp_recipients(recipients_raw)
    if not host or not port or not sender or not recipients:
        return False, "Please fill host, port, sender, and recipients before testing."

    subject = "UniFi Backup: SMTP Test Email"
    body = (
        "This is a test email from UniFi Automated Consoles Backup.\n"
        f"Sent at: {_format_local_time()}\n"
    )
    try:
        send_notification(subject, body)
    except (smtplib.SMTPException, OSError) as exc:
        return False, f"SMTP test failed: {exc}"
    return True, "SMTP test email sent successfully."


def notify_cookies_expired(console_name: str, url: str) -> None:
    if not appdata.get("smtp", {}).get("notify_cookies_expired", False):
        return
    now = _format_local_time()
    subject = f"UniFi Backup: Cookies expired for {console_name}"
    body = (
        f"Cookies appear to be expired for console '{console_name}'.\n"
        f"Backup URL: {url}\n"
        f"Detected: {now}\n"
        "Action: Upload fresh cookies or re-login to renew the session."
    )
    send_notification(subject, body)


def notify_connectivity_failed(console_name: str, url: str, error: str) -> None:
    if not appdata.get("smtp", {}).get("notify_connectivity_failed", False):
        return
    now = _format_local_time()
    subject = f"UniFi Backup: Connectivity check failed for {console_name}"
    body = (
        f"Connectivity check failed for console '{console_name}'.\n"
        f"Backup URL: {url}\n"
        f"Error: {error}\n"
        f"Time: {now}\n"
    )
    send_notification(subject, body)


def notify_backup_failed(console_name: str, url: str, error: str) -> None:
    if not appdata.get("smtp", {}).get("notify_backup_failed", False):
        return
    now = _format_local_time()
    subject = f"UniFi Backup: Failed for {console_name}"
    body = (
        f"Backup failed for console '{console_name}'.\n"
        f"Backup URL: {url}\n"
        f"Error: {error}\n"
        f"Time: {now}\n"
    )
    send_notification(subject, body)


def notify_backup_success(console_name: str, url: str, filename: str) -> None:
    if not appdata.get("smtp", {}).get("notify_backup_success", False):
        return
    now = _format_local_time()
    subject = f"UniFi Backup: Success for {console_name}"
    body = (
        f"Backup succeeded for console '{console_name}'.\n"
        f"Backup URL: {url}\n"
        f"File: {filename}\n"
        f"Time: {now}\n"
    )
    send_notification(subject, body)
