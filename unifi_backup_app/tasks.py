from __future__ import annotations

from datetime import datetime, timezone
import os
import time

import psutil
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

from .data import add_app_log, appdata, save_appdata
from .notifications import (
    notify_backup_failed,
    notify_backup_success,
    notify_connectivity_failed,
    notify_cookies_expired,
)
from .selenium_client import get_selenium_driver
from .settings import BACKUP_ROOT, COOKIES_JSON, DOWNLOAD_DIR
from .state import log_console, is_task_running, task_queue, current_task_status


def kill_leftover_chrome_processes() -> None:
    log_console("[Cleanup] Checking leftover Chrome/ChromeDriver processes...")
    for proc in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            cmdline_str = " ".join(proc.cmdline()).lower()
            name_str = (proc.name() or "").lower()
            if ("chrome" in name_str or "chromedriver" in name_str) or (
                "chrome" in cmdline_str or "chromedriver" in cmdline_str
            ):
                log_console(
                    f"[Cleanup] Killing leftover process PID={proc.pid} ({name_str})."
                )
                proc.kill()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue


def cleanup_leftover_chrome() -> None:
    if is_task_running():
        return
    if not task_queue.empty():
        return
    kill_leftover_chrome_processes()


def reset_processes_logic() -> None:
    add_app_log("Manual reset => starting cleanup of chrome/chromedriver processes.")
    log_console("Manual reset => starting cleanup of chrome/chromedriver processes.")
    kill_leftover_chrome_processes()
    add_app_log("Manual reset => cleanup complete.")
    log_console("Manual reset => cleanup complete.")


def remove_old_cookie() -> None:
    if COOKIES_JSON.exists():
        COOKIES_JSON.unlink()
        add_app_log("Removed old cookies.json manually.")
        log_console("Removed old cookies.json manually.")


def store_cookies_json(cookies_data: list[dict]) -> None:
    with COOKIES_JSON.open("w", encoding="utf-8") as handle:
        import json

        json.dump(cookies_data, handle, indent=2)
    appdata["master_logged_in"] = True
    add_app_log("Cookies uploaded manually and stored.")
    log_console("Cookies uploaded manually and stored.")


def save_cookies(driver) -> None:
    cookies = driver.get_cookies()
    with COOKIES_JSON.open("w", encoding="utf-8") as handle:
        import json

        json.dump(cookies, handle, indent=2)
    add_app_log("Cookies saved to cookies.json")
    log_console("Cookies saved to cookies.json")


def load_cookies(driver) -> None:
    if COOKIES_JSON.exists():
        import json

        with COOKIES_JSON.open("r", encoding="utf-8") as handle:
            cookies = json.load(handle)
        driver.get("https://unifi.ui.com/")
        time.sleep(2)
        for cookie in cookies:
            try:
                driver.add_cookie(
                    {
                        "name": cookie["name"],
                        "value": cookie["value"],
                        "domain": cookie["domain"],
                        "path": cookie["path"],
                    }
                )
            except Exception:
                continue
        add_app_log("Cookies loaded from cookies.json")
        log_console("Cookies loaded from cookies.json")


def manual_login_browser_logic() -> None:
    log_console("Starting manual_login_browser_logic() ...")
    driver = get_selenium_driver()
    try:
        driver.get("https://unifi.ui.com/")
        add_app_log("Opened unifi.ui.com for manual login (2 min).")

        success = False
        for _ in range(120):
            time.sleep(1)
            url_ = driver.current_url.lower()
            if "unifi.ui.com" in url_ and "/login" not in url_ and "/mfa" not in url_:
                success = True
                break

        if success:
            save_cookies(driver)
            appdata["master_logged_in"] = True
            add_app_log("Manual login success => master_logged_in=True.")
            log_console("Manual login => success => cookies saved.")
        else:
            add_app_log(
                "Manual login => timed out => user never left /login or /mfa."
            )
            log_console("Manual login => timed out => still on /login or /mfa.")
    finally:
        driver.quit()
        save_appdata()


def attempt_console_backup(console: dict) -> bool:
    name = console["name"]
    driver = get_selenium_driver()
    try:
        driver.get("https://unifi.ui.com/")
        time.sleep(2)
        load_cookies(driver)
        time.sleep(2)

        if not appdata.get("master_logged_in", False):
            console["last_backup_status"] = "Failed"
            add_app_log(f"Backup => '{name}' => Not logged in => fail.")
            return False

        driver.get(console["backup_url"])
        time.sleep(5)

        curr_url = driver.current_url.lower()
        if "/login" in curr_url or "/mfa" in curr_url:
            console["last_backup_status"] = "Failed"
            add_app_log(
                f"Backup => '{name}' => forced login => set master_logged_in=False"
            )
            appdata["master_logged_in"] = False
            save_appdata()
            notify_cookies_expired(name, console["backup_url"])
            kill_leftover_chrome_processes()
            return False

        main_btn = WebDriverWait(driver, 30).until(
            EC.element_to_be_clickable((By.XPATH, "//button[@name='backupDownload']"))
        )
        main_btn.click()
        time.sleep(3)

        second_btn = WebDriverWait(driver, 30).until(
            EC.element_to_be_clickable(
                (
                    By.XPATH,
                    "//button[@name='backupDownload' and contains(@class, 'css-network-qhqpn7')]",
                )
            )
        )
        second_btn.click()

        found_file = None
        for _ in range(60):
            possible = [
                f
                for f in os.listdir(DOWNLOAD_DIR)
                if (f.endswith(".unf") or f.endswith(".tar.gz"))
                and not f.endswith(".crdownload")
            ]
            if possible:
                possible.sort(
                    key=lambda x: os.path.getmtime(DOWNLOAD_DIR / x), reverse=True
                )
                found_file = possible[0]
                break
            time.sleep(1)

        if not found_file:
            console["last_backup_status"] = "Failed"
            add_app_log(f"Backup => '{name}' => no .unf/.tar.gz => fail.")
            notify_backup_failed(name, console["backup_url"], "No backup file after 60s")
            return False

        utc_date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        folder_path = BACKUP_ROOT / utc_date_str
        folder_path.mkdir(parents=True, exist_ok=True)

        oldpath = DOWNLOAD_DIR / found_file
        new_name = f"{name}_{found_file}"
        newpath = folder_path / new_name
        oldpath.rename(newpath)

        console["last_backup_status"] = "Success"
        console["last_backup_time"] = datetime.now(timezone.utc).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
        add_app_log(f"Backup => '{name}' => success => {new_name}")
        notify_backup_success(name, console["backup_url"], new_name)
        return True

    except Exception as exc:
        console["last_backup_status"] = "Failed"
        add_app_log(f"Backup => '{name}' => exception => {exc}")
        notify_backup_failed(name, console["backup_url"], str(exc))
        kill_leftover_chrome_processes()
        return False
    finally:
        driver.quit()
        save_appdata()


def scheduled_connectivity_check_logic() -> None:
    log_console("scheduled_connectivity_check_logic => start")
    test_cookie_access_logic()


def scheduled_backup_job_logic() -> None:
    if not appdata.get("master_logged_in", False):
        add_app_log("Scheduled backup => canceled => not logged in.")
        return

    add_app_log("Scheduled backup => pass#1 for all consoles.")
    pass1_fail = []
    all_cons = [c for c in appdata["consoles"] if not c.get("exclude_from_schedule")]
    if not all_cons:
        add_app_log("Scheduled backup => no consoles eligible (all excluded).")
        return
    for console in all_cons:
        current_task_status["step"] = f"ScheduledBackup => Pass1 => {console['name']}"
        ok = attempt_console_backup(console)
        if not ok:
            pass1_fail.append(console["id"])

    if pass1_fail:
        current_task_status["step"] = "ScheduledBackup => Wait10s => pass2"
        time.sleep(10)
        pass2_fail = []
        for cid in pass1_fail:
            console = next((x for x in all_cons if x["id"] == cid), None)
            if not console:
                continue
            current_task_status["step"] = (
                f"ScheduledBackup => Pass2 => {console['name']}"
            )
            ok2 = attempt_console_backup(console)
            if not ok2:
                pass2_fail.append(console["id"])
            else:
                console["last_backup_status"] = "Success"
                add_app_log(f"{console['name']} => pass2 => succeeded after retry")

        if pass2_fail:
            current_task_status["step"] = "ScheduledBackup => Wait10s => pass3"
            time.sleep(10)
            pass3_fail = []
            for cid in pass2_fail:
                console = next((x for x in all_cons if x["id"] == cid), None)
                if not console:
                    continue
                current_task_status["step"] = (
                    f"ScheduledBackup => Pass3 => {console['name']}"
                )
                ok3 = attempt_console_backup(console)
                if not ok3:
                    pass3_fail.append(console["id"])
                else:
                    console["last_backup_status"] = "Success"
                    add_app_log(f"{console['name']} => pass3 => succeeded after retry")

            if pass3_fail:
                for cid in pass3_fail:
                    console = next((x for x in all_cons if x["id"] == cid), None)
                    if console:
                        console["last_backup_status"] = "Failed after 3 retries"
                        add_app_log(f"{console['name']} => failed after 3 tries.")
        else:
            log_console("No fails remain after pass2 => skipping pass3.")
    else:
        log_console("No fails => skipping pass2/pass3.")

    add_app_log("Scheduled backup => complete => all passes done.")
    current_task_status["step"] = "ScheduledBackup => Done"
    save_appdata()


def test_cookie_access_logic() -> None:
    log_console("Cookie test => start")
    driver = get_selenium_driver()
    try:
        driver.get("https://unifi.ui.com/")
        time.sleep(2)
        load_cookies(driver)
        time.sleep(2)
        driver.get("https://unifi.ui.com/")
        time.sleep(2)
        curr_url = driver.current_url.lower()
        invalid_domain = "unifi.ui.com" not in curr_url or "account.ui.com" in curr_url
        appdata["last_cookie_check"] = datetime.now(timezone.utc).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
        if invalid_domain or "/login" in curr_url or "/mfa" in curr_url:
            appdata["master_logged_in"] = False
            save_appdata()
            add_app_log(f"Cookie test => invalid session (landed on {curr_url}).")
            log_console(f"Cookie test => invalid session (landed on {curr_url}).")
        else:
            appdata["master_logged_in"] = True
            save_appdata()
            add_app_log("Cookie test => session valid.")
            log_console("Cookie test => session valid.")
    finally:
        driver.quit()
