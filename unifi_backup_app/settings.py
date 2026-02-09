from __future__ import annotations

import os
from pathlib import Path
def _get_env_bool(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


APP_DATA_DIR = Path(
    os.environ.get("APP_DATA_DIR", Path.cwd() / "unifi_app")
).resolve()
APP_DATA_DIR.mkdir(parents=True, exist_ok=True)

APPDATA_JSON = APP_DATA_DIR / "appdata.json"
COOKIES_JSON = APP_DATA_DIR / "cookies.json"
BACKUP_ROOT = APP_DATA_DIR / "backups"
DOWNLOAD_DIR = APP_DATA_DIR / "chrome_downloads"

BACKUP_ROOT.mkdir(parents=True, exist_ok=True)
DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)

SECRET_KEY = os.environ.get("SECRET_KEY", "REPLACE_WITH_A_STRONG_SECRET_KEY")
DEFAULT_TZ = os.environ.get("DEFAULT_TZ", "UTC")


def _build_timezones() -> list[str]:
    tzs = ["UTC"]
    for offset in range(-12, 15):
        if offset == 0:
            continue
        sign = "+" if offset > 0 else "-"
        tzs.append(f"UTC{sign}{abs(offset)}")
    for offset in range(-12, 15):
        if offset == 0:
            continue
        sign = "+" if offset > 0 else "-"
        tzs.append(f"GMT{sign}{abs(offset)}")
    return tzs


AVAILABLE_TIMEZONES = _build_timezones()

CHROME_HEADLESS = _get_env_bool("CHROME_HEADLESS", False)
CHROME_BINARY = os.environ.get("CHROME_BINARY")
CHROMEDRIVER_PATH = os.environ.get("CHROMEDRIVER_PATH")
