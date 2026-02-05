from __future__ import annotations

from pathlib import Path

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager, ChromeType

from .settings import CHROME_HEADLESS, CHROME_BINARY, CHROMEDRIVER_PATH, DOWNLOAD_DIR


def get_selenium_driver() -> webdriver.Chrome:
    chrome_options = Options()
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--start-maximized")

    if CHROME_HEADLESS:
        chrome_options.add_argument("--headless=new")
        chrome_options.add_argument("--no-sandbox")
        chrome_options.add_argument("--disable-dev-shm-usage")
        chrome_options.add_argument("--window-size=1600,900")

    if CHROME_BINARY:
        chrome_options.binary_location = CHROME_BINARY

    prefs = {
        "download.default_directory": str(DOWNLOAD_DIR),
        "download.prompt_for_download": False,
        "download.directory_upgrade": True,
    }
    chrome_options.add_experimental_option("prefs", prefs)

    service = None
    if CHROMEDRIVER_PATH:
        chromedriver_path = Path(CHROMEDRIVER_PATH)
        if chromedriver_path.exists():
            service = Service(str(chromedriver_path))

    if service is None:
        chrome_type = ChromeType.CHROMIUM if CHROME_BINARY else ChromeType.GOOGLE
        service = Service(ChromeDriverManager(chrome_type=chrome_type).install())
    driver = webdriver.Chrome(service=service, options=chrome_options)
    return driver
