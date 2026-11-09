from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import shutil
import time
from urllib.parse import urlparse
import uuid

from selenium.common.exceptions import (
    ElementClickInterceptedException,
    ElementNotInteractableException,
    StaleElementReferenceException,
    TimeoutException,
)
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait

from .data import add_app_log, appdata, save_appdata
from .notifications import (
    notify_backup_failed,
    notify_backup_success,
    notify_connectivity_failed,
    notify_cookies_expired,
)
from .selenium_client import (
    CHROME_PROFILE_ROOT,
    browser_session,
    kill_leftover_browser_processes,
    legacy_chromedriver_dirs,
    remove_leftover_profiles,
    short_error,
)
from .settings import BACKUP_ROOT, COOKIES_JSON, DOWNLOAD_DIR
from .state import (
    TaskCancelled,
    cancel_all_tasks,
    cancel_requested,
    cancellable_sleep,
    log_console,
    is_task_running,
    raise_if_cancelled,
    task_queue,
    current_task_status,
    update_current_task_progress,
)

UNIFI_HOME_URL = "https://unifi.ui.com/"
_LOGIN_HOSTS = ("account.ui.com", "sso.ui.com")

# UniFi backup page flow (11/2026): an inline "Download Backup" button opens a dialog
# on the same page; its primary "Download" button makes the console build the archive.
# The browser download only starts once it is ready, normally within 30-45 s.
OPEN_DIALOG_TIMEOUT = 60
CONFIRM_TIMEOUT = 30
DOWNLOAD_START_TIMEOUT = 90
DOWNLOAD_STALL_TIMEOUT = 120
DOWNLOAD_MAX_DURATION = 30 * 60
_PARTIAL_DOWNLOAD_SUFFIXES = (".crdownload", ".tmp", ".part")

SCHEDULED_ATTEMPTS = 3
RETRY_WAIT_SECONDS = 10

RESULT_SUCCESS = "success"
RESULT_FAILED = "failed"
RESULT_SESSION_EXPIRED = "session_expired"


class SessionExpired(Exception):
    pass


class BackupError(Exception):
    pass


def console_file_prefix(console_name: str) -> str:
    """Prefix of the stored backup files of a console (also used to list them)."""
    safe = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", console_name).strip(" .")
    return f"{safe or 'console'}_"


def cleanup_leftover_chrome() -> None:
    if is_task_running():
        return
    if not task_queue.empty():
        return
    cleanup_browser_leftovers()


def cleanup_browser_leftovers() -> None:
    """Kill orphaned browser processes and delete stale profiles/downloads. Idle only."""
    killed = kill_leftover_browser_processes()
    if killed:
        log_console(f"[Cleanup] Killed {killed} leftover browser process(es).")
    removed = remove_leftover_profiles()
    if removed:
        log_console(f"[Cleanup] Removed {removed} leftover Chrome profile/temp folder(s).")
    if DOWNLOAD_DIR.exists():
        for entry in DOWNLOAD_DIR.glob("session-*"):
            if entry.is_dir():
                shutil.rmtree(entry, ignore_errors=True)


def reset_processes_logic() -> None:
    add_app_log("Manual reset => starting cleanup of chrome/chromedriver processes.")
    log_console("Manual reset => starting cleanup of chrome/chromedriver processes.")
    cleanup_browser_leftovers()
    add_app_log("Manual reset => cleanup complete.")
    log_console("Manual reset => cleanup complete.")


def cancel_all_tasks_logic() -> tuple[int, bool]:
    """Empty the queue and stop the running task right away. Returns (dropped, was_running)."""
    dropped, was_running = cancel_all_tasks()
    if was_running:
        # Selenium calls can block for minutes (page loads, downloads): killing the
        # browser makes the running task fail fast and reach its cancellation check.
        kill_leftover_browser_processes()
    add_app_log(
        f"Cancel all => {dropped} queued task(s) removed"
        + (", running task stopping." if was_running else ", no task was running.")
    )
    return dropped, was_running


PURGE_MAX_AGE_DAYS = 90
PURGE_TASK_NAME = "PurgeOldFiles"


def _purge_roots() -> dict[str, list[Path]]:
    # appdata.json and cookies.json sit in APP_DATA_DIR itself and are never touched.
    return {
        "backups": [BACKUP_ROOT],
        "downloads": [DOWNLOAD_DIR],
        "chrome": [CHROME_PROFILE_ROOT, *legacy_chromedriver_dirs()],
    }


def _files_older_than(roots: list[Path], cutoff: float) -> list[tuple[Path, int]]:
    found = []
    for root in roots:
        if not root.exists():
            continue
        candidates = [root] if root.is_file() else root.rglob("*")
        for path in candidates:
            try:
                if path.is_symlink() or not path.is_file():
                    continue
                stat = path.stat()
            except OSError:
                continue
            if stat.st_mtime < cutoff:
                found.append((path, stat.st_size))
    return found


def _remove_empty_dirs(root: Path, keep_root: bool) -> None:
    if not root.is_dir() or root.is_symlink():
        return
    for dirpath, _, _ in os.walk(root, topdown=False):
        if keep_root and Path(dirpath) == root:
            continue
        try:
            os.rmdir(dirpath)  # only succeeds when the folder is empty
        except OSError:
            pass


def purge_preview(max_age_days: int = PURGE_MAX_AGE_DAYS) -> dict:
    cutoff = time.time() - max_age_days * 86400
    categories = {}
    for category, roots in _purge_roots().items():
        files = _files_older_than(roots, cutoff)
        categories[category] = {"files": len(files), "bytes": sum(size for _, size in files)}
    return {
        "max_age_days": max_age_days,
        "categories": categories,
        "total_files": sum(c["files"] for c in categories.values()),
        "total_bytes": sum(c["bytes"] for c in categories.values()),
    }


def purge_old_files_logic(max_age_days: int = PURGE_MAX_AGE_DAYS) -> None:
    # Runs in the worker, so no browser session or download is in progress meanwhile.
    cutoff = time.time() - max_age_days * 86400
    add_app_log(f"Purge => deleting files older than {max_age_days} days...")
    keep_roots = {BACKUP_ROOT, DOWNLOAD_DIR, CHROME_PROFILE_ROOT}
    summary = []
    for category, roots in _purge_roots().items():
        current_task_status["step"] = f"{PURGE_TASK_NAME} => {category}"
        count = freed = errors = 0
        for path, size in _files_older_than(roots, cutoff):
            raise_if_cancelled()
            try:
                path.unlink()
                count += 1
                freed += size
            except OSError as exc:
                errors += 1
                log_console(f"Purge => cannot delete {path} => {exc}")
        for root in roots:
            _remove_empty_dirs(root, keep_root=root in keep_roots)
        part = f"{category}: {count} file(s), {freed / 1e6:.1f} MB"
        if errors:
            part += f" ({errors} could not be deleted)"
        summary.append(part)
    add_app_log("Purge => done => " + " | ".join(summary))


def remove_old_cookie() -> None:
    if COOKIES_JSON.exists():
        COOKIES_JSON.unlink()
        add_app_log("Removed old cookies.json manually.")
        log_console("Removed old cookies.json manually.")


def store_cookies_json(cookies_data: list[dict]) -> None:
    with COOKIES_JSON.open("w", encoding="utf-8") as handle:
        json.dump(cookies_data, handle, indent=2)
    appdata["master_logged_in"] = True
    add_app_log("Cookies uploaded manually and stored.")
    log_console("Cookies uploaded manually and stored.")


def save_cookies(driver) -> None:
    cookies = driver.get_cookies()
    with COOKIES_JSON.open("w", encoding="utf-8") as handle:
        json.dump(cookies, handle, indent=2)
    add_app_log("Cookies saved to cookies.json")
    log_console("Cookies saved to cookies.json")


def load_cookies(driver) -> None:
    if not COOKIES_JSON.exists():
        return
    with COOKIES_JSON.open("r", encoding="utf-8") as handle:
        cookies = json.load(handle)
    driver.get(UNIFI_HOME_URL)
    cancellable_sleep(2)
    loaded = 0
    for cookie in cookies:
        try:
            driver.add_cookie(
                {
                    "name": cookie["name"],
                    "value": cookie["value"],
                    "domain": cookie["domain"],
                    "path": cookie.get("path") or "/",
                }
            )
            loaded += 1
        except Exception:
            continue
    add_app_log("Cookies loaded from cookies.json")
    log_console(f"Cookies loaded from cookies.json ({loaded}/{len(cookies)}).")


def _is_login_url(url: str) -> bool:
    parsed = urlparse(url.lower())
    if parsed.hostname in _LOGIN_HOSTS:
        return True
    return "/login" in parsed.path or "/mfa" in parsed.path


def manual_login_browser_logic() -> None:
    log_console("Starting manual_login_browser_logic() ...")
    try:
        with browser_session() as driver:
            driver.get(UNIFI_HOME_URL)
            add_app_log("Opened unifi.ui.com for manual login (2 min).")

            success = False
            for _ in range(120):
                cancellable_sleep(1)
                url_ = driver.current_url.lower()
                if "unifi.ui.com" in url_ and not _is_login_url(url_):
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
        save_appdata()


def _button_xpath(label: str, primary: bool = False) -> str:
    # Match the label wherever it sits: directly inside the <button> (the inline
    # "Download Backup" button since 11/2026) or in a <span class="content__..."> (the
    # dialog buttons). Hashed class suffixes and react-aria ids change on every UniFi
    # release, so they are never used.
    xpath = (
        f"//button[(normalize-space(.)='{label}' or .//span[normalize-space()='{label}'])"
    )
    if primary:
        xpath += " and contains(@class, 'primary')"
    return xpath + "]"


_VISIBLE_BUTTON_LABELS_JS = r"""
return Array.from(document.querySelectorAll('button'))
  .filter(b => b.getClientRects().length > 0)
  .map(b => (b.innerText || b.getAttribute('aria-label') || '').replace(/\s+/g, ' ').trim())
  .filter(t => t.length > 0 && t.length <= 60);
"""


def _visible_button_labels(driver) -> list[str]:
    """Labels of the buttons shown on the page, to diagnose the next UniFi UI change."""
    try:
        labels = driver.execute_script(_VISIBLE_BUTTON_LABELS_JS) or []
    except Exception:
        return []
    return list(dict.fromkeys(labels))


def _wait_for_button(driver, label: str, timeout: int, primary: bool = False):
    xpath = _button_xpath(label, primary)

    def _visible_button(drv):
        raise_if_cancelled()
        # The single page app can bounce to the login page at any time while loading.
        if _is_login_url(drv.current_url):
            raise SessionExpired(drv.current_url)
        for element in drv.find_elements(By.XPATH, xpath):
            if element.is_displayed() and element.is_enabled():
                return element
        return False

    try:
        return WebDriverWait(
            driver,
            timeout,
            poll_frequency=0.5,
            ignored_exceptions=(StaleElementReferenceException,),
        ).until(_visible_button)
    except TimeoutException:
        labels = _visible_button_labels(driver)
        try:
            url = driver.current_url
        except Exception:
            url = "?"
        log_console(f"Button '{label}' not found on {url} => visible buttons: {labels}")
        seen = ", ".join(f"'{text}'" for text in labels[:12]) or "none"
        raise BackupError(
            f"button '{label}' not found after {timeout}s (visible buttons: {seen})"
        ) from None


def _click(driver, element) -> None:
    driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", element)
    try:
        element.click()
    except (ElementClickInterceptedException, ElementNotInteractableException):
        driver.execute_script("arguments[0].click();", element)


def _wait_for_download(download_dir: Path) -> Path:
    """Wait for the browser download to start, then to finish, and return the file."""
    started_at = time.monotonic()
    last_change = started_at
    last_snapshot: tuple | None = None
    download_started = False

    while True:
        entries = []
        for path in download_dir.iterdir():
            try:
                if path.is_file():
                    entries.append((path.name, path.stat().st_size))
            except OSError:
                continue
        partial = [e for e in entries if e[0].lower().endswith(_PARTIAL_DOWNLOAD_SUFFIXES)]
        complete = [e for e in entries if e not in partial and e[1] > 0]
        snapshot = tuple(sorted(entries))

        # Finished = a non-empty file, no partial file left, and nothing moved since the
        # previous poll (Chrome may create the final name before the data is flushed).
        if complete and not partial and snapshot == last_snapshot:
            newest = max(complete, key=lambda e: (download_dir / e[0]).stat().st_mtime)
            return download_dir / newest[0]

        now = time.monotonic()
        if entries and not download_started:
            download_started = True
            log_console(f"Download started after {now - started_at:.0f}s.")
        if snapshot != last_snapshot:
            last_snapshot = snapshot
            last_change = now

        if not download_started and now - started_at > DOWNLOAD_START_TIMEOUT:
            raise BackupError(f"download did not start within {DOWNLOAD_START_TIMEOUT}s")
        if download_started and now - last_change > DOWNLOAD_STALL_TIMEOUT:
            raise BackupError(f"download stalled for {DOWNLOAD_STALL_TIMEOUT}s")
        if now - started_at > DOWNLOAD_MAX_DURATION:
            raise BackupError(f"download not finished after {DOWNLOAD_MAX_DURATION}s")
        cancellable_sleep(1)


def _store_backup_file(console_name: str, downloaded: Path) -> str:
    utc_date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    folder_path = BACKUP_ROOT / utc_date_str
    folder_path.mkdir(parents=True, exist_ok=True)

    prefix = console_file_prefix(console_name)
    target = folder_path / f"{prefix}{downloaded.name}"
    counter = 1
    while target.exists():
        # Keep multi-part extensions such as .tar.gz intact.
        stem, dot, ext = downloaded.name.partition(".")
        target = folder_path / f"{prefix}{stem}_{counter}{dot}{ext}"
        counter += 1
    shutil.move(str(downloaded), str(target))
    return target.name


def _backup_console_once(console: dict) -> tuple[str, str]:
    """One backup attempt. Returns (result, detail); never raises."""
    name = console["name"]
    # A private download folder per attempt: a stale file from an earlier attempt can
    # never be picked up and stored under the wrong console.
    download_dir = DOWNLOAD_DIR / f"session-{uuid.uuid4().hex}"
    try:
        download_dir.mkdir(parents=True, exist_ok=True)
        with browser_session(download_dir) as driver:
            load_cookies(driver)
            driver.get(console["backup_url"])

            _click(driver, _wait_for_button(driver, "Download Backup", OPEN_DIALOG_TIMEOUT))
            log_console(f"Backup => '{name}' => 'Download Backup' clicked, waiting for the dialog...")
            _click(driver, _wait_for_button(driver, "Download", CONFIRM_TIMEOUT, primary=True))
            log_console(f"Backup => '{name}' => 'Download' clicked, waiting for the download to start...")
            downloaded = _wait_for_download(download_dir)

        new_name = _store_backup_file(name, downloaded)
    except TaskCancelled:
        raise
    except SessionExpired as exc:
        console["last_backup_status"] = "Failed"
        appdata["master_logged_in"] = False
        add_app_log(f"Backup => '{name}' => redirected to login ({exc}) => master_logged_in=False")
        notify_cookies_expired(name, console["backup_url"])
        return RESULT_SESSION_EXPIRED, "session expired"
    except Exception as exc:
        if cancel_requested():
            # The browser was killed by "Cancel all": not a backup failure.
            raise TaskCancelled() from None
        detail = short_error(exc)
        console["last_backup_status"] = "Failed"
        add_app_log(f"Backup => '{name}' => failed => {detail}")
        return RESULT_FAILED, detail
    finally:
        shutil.rmtree(download_dir, ignore_errors=True)
        save_appdata()

    console["last_backup_status"] = "Success"
    console["last_backup_time"] = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    save_appdata()
    add_app_log(f"Backup => '{name}' => success => {new_name}")
    notify_backup_success(name, console["backup_url"], new_name)
    return RESULT_SUCCESS, new_name


def attempt_console_backup(console: dict) -> bool:
    if not appdata.get("master_logged_in", False):
        console["last_backup_status"] = "Failed"
        add_app_log(f"Backup => '{console['name']}' => Not logged in => fail.")
        return False
    result, detail = _backup_console_once(console)
    if result == RESULT_FAILED:
        notify_backup_failed(console["name"], console["backup_url"], detail)
    return result == RESULT_SUCCESS


def manual_backup_logic(console_id: int) -> None:
    # Resolved at run time: the console may have been edited or removed while queued.
    console = next((c for c in appdata["consoles"] if c["id"] == console_id), None)
    if console is None:
        add_app_log(f"Manual backup => console #{console_id} no longer exists => skipped.")
        return
    attempt_console_backup(console)


def scheduled_connectivity_check_logic() -> None:
    log_console("scheduled_connectivity_check_logic => start")
    test_cookie_access_logic()


def scheduled_backup_job_logic() -> None:
    if not appdata.get("master_logged_in", False):
        add_app_log("Scheduled backup => canceled => not logged in.")
        return

    all_cons = [c for c in appdata["consoles"] if not c.get("exclude_from_schedule")]
    if not all_cons:
        add_app_log("Scheduled backup => no consoles eligible (all excluded).")
        return

    add_app_log(f"Scheduled backup => running for {len(all_cons)} console(s).")
    total_items = len(all_cons)
    current_task_status["total_items"] = total_items

    for idx, console in enumerate(all_cons, start=1):
        result, detail = RESULT_FAILED, ""
        for attempt in range(1, SCHEDULED_ATTEMPTS + 1):
            raise_if_cancelled()
            update_current_task_progress(
                idx - 1,
                f"ScheduledBackup => {console['name']} ({idx}/{total_items}) => attempt {attempt}/{SCHEDULED_ATTEMPTS}",
            )
            result, detail = _backup_console_once(console)
            if result != RESULT_FAILED:
                if result == RESULT_SUCCESS and attempt > 1:
                    add_app_log(
                        f"{console['name']} => succeeded after retry (attempt {attempt}/{SCHEDULED_ATTEMPTS})."
                    )
                break
            if attempt < SCHEDULED_ATTEMPTS:
                current_task_status["step"] = (
                    f"ScheduledBackup => waiting before retry for {console['name']}"
                )
                cancellable_sleep(RETRY_WAIT_SECONDS)

        if result == RESULT_SESSION_EXPIRED:
            # Every remaining console would bounce to the login page as well.
            add_app_log(
                "Scheduled backup => session expired => remaining consoles skipped. Upload fresh cookies."
            )
            break
        if result == RESULT_FAILED:
            console["last_backup_status"] = f"Failed after {SCHEDULED_ATTEMPTS} retries"
            add_app_log(f"{console['name']} => failed after {SCHEDULED_ATTEMPTS} tries.")
            notify_backup_failed(console["name"], console["backup_url"], detail)

        update_current_task_progress(
            idx,
            f"ScheduledBackup => completed {idx}/{total_items} console(s)",
        )
    else:
        add_app_log("Scheduled backup => complete => all consoles processed.")

    current_task_status["step"] = "ScheduledBackup => Done"
    save_appdata()


def test_cookie_access_logic() -> None:
    log_console("Cookie test => start")
    was_logged_in = bool(appdata.get("master_logged_in", False))
    try:
        with browser_session() as driver:
            load_cookies(driver)
            driver.get(UNIFI_HOME_URL)
            # The portal redirects to the login page client side, sometimes late.
            for _ in range(10):
                curr_url = driver.current_url
                if _is_login_url(curr_url):
                    break
                cancellable_sleep(1)
    except Exception as exc:
        if isinstance(exc, TaskCancelled) or cancel_requested():
            raise TaskCancelled() from None
        detail = short_error(exc)
        add_app_log(f"Cookie test => error => {detail}")
        log_console(f"Cookie test => error => {detail}")
        notify_connectivity_failed("UniFi portal", UNIFI_HOME_URL, detail)
        return

    appdata["last_cookie_check"] = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    if "unifi.ui.com" not in urlparse(curr_url.lower()).netloc or _is_login_url(curr_url):
        appdata["master_logged_in"] = False
        save_appdata()
        add_app_log(f"Cookie test => invalid session (landed on {curr_url}).")
        log_console(f"Cookie test => invalid session (landed on {curr_url}).")
        if was_logged_in:
            notify_connectivity_failed(
                "UniFi portal", UNIFI_HOME_URL, f"Session invalid, redirected to {curr_url}"
            )
    else:
        appdata["master_logged_in"] = True
        save_appdata()
        add_app_log("Cookie test => session valid.")
        log_console("Cookie test => session valid.")
