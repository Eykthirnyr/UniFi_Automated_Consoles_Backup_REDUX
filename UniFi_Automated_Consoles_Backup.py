#!/usr/bin/env python3
import sys
import subprocess

REQUIRED_PACKAGES = [
    "flask",
    "flask_apscheduler",
    "requests",
    "selenium",
    "webdriver_manager",
    "psutil",
]


def check_and_install_dependencies() -> None:
    for pkg in REQUIRED_PACKAGES:
        try:
            __import__(pkg)
        except ImportError:
            print(f"[INFO] Missing '{pkg}'. Installing...")
            subprocess.run([sys.executable, "-m", "pip", "install", pkg], check=True)


check_and_install_dependencies()

from unifi_backup_app.runner import run


if __name__ == "__main__":
    run()
