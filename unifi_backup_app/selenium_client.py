from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
import shutil
import tempfile
from typing import Iterator

import psutil
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.remote.remote_connection import RemoteConnection

from .settings import CHROME_HEADLESS, CHROME_BINARY, CHROMEDRIVER_PATH
from .state import cancellable_sleep, log_console, raise_if_cancelled

# Every Chrome profile created by the app lives under this root. The path is passed
# on the Chrome command line, which lets the cleanup find our own leftover processes
# without touching any other Chrome running on the machine.
CHROME_PROFILE_ROOT = Path(tempfile.gettempdir()) / "unifi-backup-chrome"

# Upper bound for a single HTTP call to chromedriver. Selenium waits forever by
# default, so a hung Chrome could block the worker (and every queued task) for good.
DRIVER_COMMAND_TIMEOUT = 300
RemoteConnection.set_timeout(DRIVER_COMMAND_TIMEOUT)

_BROWSER_PROCESS_MARKERS = ("chrome", "chromium")

# Temp folders chromedriver creates on its own: the default profile used before
# 09/2026 (they filled /tmp with ~175 MB each) and scratch folders it still makes.
# They leak whenever chromedriver is killed instead of quitting.
_CHROMEDRIVER_TEMP_PATTERNS = (
    "org.chromium.Chromium.scoped_dir.*",
    ".org.chromium.Chromium.*",
    "com.google.Chrome.scoped_dir.*",
    ".com.google.Chrome.*",
)
_IN_CONTAINER = Path("/.dockerenv").exists() or Path("/run/.containerenv").exists()


def get_selenium_driver(download_dir: Path | None = None) -> webdriver.Chrome:
    CHROME_PROFILE_ROOT.mkdir(parents=True, exist_ok=True)
    profile_dir = Path(tempfile.mkdtemp(prefix="profile-", dir=CHROME_PROFILE_ROOT))

    chrome_options = Options()
    chrome_options.page_load_strategy = "eager"
    chrome_options.add_argument(f"--user-data-dir={profile_dir}")
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--start-maximized")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--disable-extensions")
    chrome_options.add_argument("--disable-background-networking")
    chrome_options.add_argument("--disable-renderer-backgrounding")
    chrome_options.add_argument("--disable-ipc-flooding-protection")
    chrome_options.add_argument("--remote-debugging-pipe")
    # Every session starts from an empty profile, so on-disk caches are never reused:
    # skip them. Without these flags a profile grew to ~175 MB per session (HTTP/JS
    # caches of the UniFi app plus ~40 MB of optimization-guide ML models).
    chrome_options.add_argument("--disk-cache-size=1")
    chrome_options.add_argument("--media-cache-size=1")
    chrome_options.add_argument("--aggressive-cache-discard")
    chrome_options.add_argument("--disable-gpu-shader-disk-cache")
    chrome_options.add_argument("--disable-component-update")
    chrome_options.add_argument(
        "--disable-features=OptimizationGuideModelDownloading,OptimizationHintsFetching,"
        "OptimizationTargetPrediction,OptimizationHints,Translate,MediaRouter"
    )

    if CHROME_HEADLESS:
        chrome_options.add_argument("--headless=new")
        chrome_options.add_argument("--window-size=1600,900")

    if CHROME_BINARY:
        chrome_options.binary_location = CHROME_BINARY

    prefs = {
        "download.prompt_for_download": False,
        "download.directory_upgrade": True,
        "profile.default_content_setting_values.automatic_downloads": 1,
    }
    if download_dir is not None:
        prefs["download.default_directory"] = str(download_dir)
    chrome_options.add_experimental_option("prefs", prefs)

    if CHROMEDRIVER_PATH and Path(CHROMEDRIVER_PATH).exists():
        service = Service(CHROMEDRIVER_PATH)
    else:
        # Selenium Manager (bundled with Selenium) fetches the chromedriver matching the
        # installed browser. webdriver_manager 4.0.1 returned a non-executable file
        # (THIRD_PARTY_NOTICES.chromedriver) with current Chrome versions.
        service = Service()

    try:
        driver = webdriver.Chrome(service=service, options=chrome_options)
    except Exception:
        # Chrome may have been half started: kill whatever runs on this profile.
        kill_processes(_processes_using_marker(str(profile_dir)))
        shutil.rmtree(profile_dir, ignore_errors=True)
        raise

    driver._unifi_profile_dir = profile_dir
    driver.set_page_load_timeout(120)
    driver.set_script_timeout(120)
    return driver


def open_driver_with_retries(
    download_dir: Path | None = None,
    max_attempts: int = 3,
    wait_seconds: int = 3,
) -> webdriver.Chrome:
    last_exc: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        raise_if_cancelled()
        try:
            return get_selenium_driver(download_dir)
        except Exception as exc:
            last_exc = exc
            log_console(
                f"Selenium startup failed (attempt {attempt}/{max_attempts}) => {short_error(exc)}"
            )
            if attempt < max_attempts:
                cancellable_sleep(wait_seconds)
    if last_exc:
        raise last_exc
    raise RuntimeError("Selenium startup failed without exception")


def close_driver(driver: webdriver.Chrome) -> None:
    """Quit the browser gracefully, then make sure nothing from this session survives."""
    tracked = _driver_process_tree(driver)
    service_process = getattr(getattr(driver, "service", None), "process", None)
    if service_process is not None and service_process.poll() is not None:
        # chromedriver is already dead (e.g. killed by "Cancel all"): quit() would only
        # wait on connection retries. Close the HTTP pool; processes are killed below.
        try:
            driver.command_executor.close()
        except Exception:
            pass
    else:
        try:
            driver.quit()
        except Exception as exc:
            log_console(f"[Cleanup] driver.quit() failed => {short_error(exc)}")

    profile_dir = getattr(driver, "_unifi_profile_dir", None)
    if profile_dir is not None:
        tracked.extend(_processes_using_marker(str(profile_dir)))
    killed = kill_processes(tracked)
    if killed:
        log_console(f"[Cleanup] Killed {killed} leftover browser process(es) after quit.")
    if profile_dir is not None:
        shutil.rmtree(profile_dir, ignore_errors=True)


@contextmanager
def browser_session(download_dir: Path | None = None) -> Iterator[webdriver.Chrome]:
    driver = open_driver_with_retries(download_dir)
    try:
        yield driver
    finally:
        close_driver(driver)


def kill_leftover_browser_processes() -> int:
    """Kill every browser process started by this app, including an open session."""
    candidates = _processes_using_marker(str(CHROME_PROFILE_ROOT))
    try:
        descendants = psutil.Process().children(recursive=True)
    except psutil.Error:
        descendants = []
    for proc in descendants:
        try:
            name = (proc.name() or "").lower()
        except psutil.Error:
            continue
        if any(marker in name for marker in _BROWSER_PROCESS_MARKERS):
            candidates.append(proc)
    return kill_processes(candidates)


def legacy_chromedriver_dirs() -> list[Path]:
    """chromedriver's own temp folders; only listed in a container, where nothing else runs Chrome."""
    if not _IN_CONTAINER:
        return []
    tmp_dir = Path(tempfile.gettempdir())
    return [entry for pattern in _CHROMEDRIVER_TEMP_PATTERNS for entry in tmp_dir.glob(pattern)]


def remove_leftover_profiles() -> int:
    """Delete Chrome profiles left behind by crashed sessions. Only call it when idle."""
    entries = list(CHROME_PROFILE_ROOT.iterdir()) if CHROME_PROFILE_ROOT.exists() else []
    entries.extend(legacy_chromedriver_dirs())
    for entry in entries:
        if entry.is_dir() and not entry.is_symlink():
            shutil.rmtree(entry, ignore_errors=True)
        else:
            entry.unlink(missing_ok=True)
    return len(entries)


def kill_processes(processes: list[psutil.Process]) -> int:
    seen: set[int] = set()
    alive: list[psutil.Process] = []
    for proc in processes:
        if proc.pid in seen:
            continue
        seen.add(proc.pid)
        try:
            # is_running() also checks the creation time, so a recycled PID is never killed.
            if proc.is_running() and proc.status() != psutil.STATUS_ZOMBIE:
                proc.kill()
                alive.append(proc)
        except psutil.Error:
            continue
    if alive:
        psutil.wait_procs(alive, timeout=5)
    return len(alive)


def short_error(exc: BaseException) -> str:
    # Selenium messages embed a full native stacktrace; the first line is what matters.
    text = str(exc).strip() or type(exc).__name__
    first_line = text.splitlines()[0]
    return first_line[:300]


def _driver_process_tree(driver: webdriver.Chrome) -> list[psutil.Process]:
    process = getattr(getattr(driver, "service", None), "process", None)
    if process is None:
        return []
    try:
        root = psutil.Process(process.pid)
        return [root, *root.children(recursive=True)]
    except psutil.Error:
        return []


def _processes_using_marker(marker: str) -> list[psutil.Process]:
    marker = marker.lower()
    found = []
    for proc in psutil.process_iter(["cmdline"]):
        try:
            cmdline = " ".join(proc.info.get("cmdline") or []).lower()
        except psutil.Error:
            continue
        if marker in cmdline:
            found.append(proc)
    return found
