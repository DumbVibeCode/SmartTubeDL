"""
Общий слой управления браузером Chrome (Selenium + CDP) для модулей ВКонтакте и Одноклассников.
Убирает дублирование запуска/очистки профилей Chrome между vk_window_qt.py и ok_window_qt.py.
"""

import json
import os
import subprocess
import sys
import time

from logger import log_message

_CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

SELENIUM_OK = True
try:
    from selenium import webdriver
    from selenium.webdriver.chrome.service import Service
    from webdriver_manager.chrome import ChromeDriverManager
except Exception as _e:
    SELENIUM_OK = False
    webdriver = None
    Service = None
    ChromeDriverManager = None
    log_message(f"WARNING browser_helper: зависимости Selenium недоступны: {_e}")


def kill_profile_chrome(profile_dir: str, label: str = "Chrome") -> None:
    """Убивает зависшие процессы Chrome, держащие конкретный профиль
    (осиротевшие после аварийного завершения). Личный браузер не затрагивает."""
    if sys.platform != "win32":
        return
    marker = os.path.basename((profile_dir or "").rstrip("\\/"))
    if not marker:
        return
    ps = (
        "Get-CimInstance Win32_Process -Filter \"Name='chrome.exe' or Name='browser.exe'\" | "
        "Where-Object { $_.CommandLine -like '*" + marker + "*' } | "
        "ForEach-Object { Stop-Process -Id $_.ProcessId -Force "
        "-ErrorAction SilentlyContinue }"
    )
    try:
        subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps],
            capture_output=True,
            timeout=15,
            creationflags=_CREATE_NO_WINDOW,
        )
        time.sleep(0.5)
        log_message(f"INFO {label}: закрыты зависшие процессы браузера профиля {marker}")
    except Exception:
        pass


def clean_chrome_crash_markers(profile_dir: str, label: str = "Chrome") -> None:
    """Убирает следы аварийного завершения Chrome, из-за которых новый запуск
    падает с 'DevToolsActivePort file doesn't exist'."""
    try:
        for name in ("SingletonLock", "SingletonCookie", "SingletonSocket",
                     "DevToolsActivePort", "lockfile"):
            p = os.path.join(profile_dir, name)
            try:
                if os.path.lexists(p):
                    os.remove(p)
            except Exception:
                pass
        prefs_path = os.path.join(profile_dir, "Default", "Preferences")
        if os.path.exists(prefs_path):
            try:
                with open(prefs_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                prof = data.get("profile", {})
                prof["exited_cleanly"] = True
                prof["exit_type"] = "Normal"
                data["profile"] = prof
                with open(prefs_path, "w", encoding="utf-8") as f:
                    json.dump(data, f)
            except Exception:
                pass
    except Exception as e:
        log_message(f"WARNING {label}: очистка профиля: {e}")


def make_chrome_options(profile_dir: str, perf_logging: bool = True):
    """Создаёт стандартный набор ChromeOptions с выделенным профилем и CDP performance-логами."""
    opts = webdriver.ChromeOptions()
    opts.add_argument(f"--user-data-dir={profile_dir}")
    opts.add_argument("--start-maximized")
    opts.add_argument("--disable-blink-features=AutomationControlled")
    opts.add_argument("--disable-session-crashed-bubble")
    opts.add_argument("--restore-last-session=false")
    opts.add_argument("--no-first-run")
    opts.add_argument("--no-default-browser-check")
    opts.add_argument(f"user-agent={DEFAULT_USER_AGENT}")
    if perf_logging:
        opts.set_capability("goog:loggingPrefs", {"performance": "ALL"})
    return opts


def start_chrome_driver(profile_dir: str, label: str = "Chrome", perf_logging: bool = True):
    """Запускает Chrome с указанным профилем (с очисткой зависших процессов и ретраем)."""
    if not SELENIUM_OK:
        raise RuntimeError("Selenium / webdriver-manager не установлены.")
    os.makedirs(profile_dir, exist_ok=True)
    kill_profile_chrome(profile_dir, label=label)

    def _launch():
        opts = make_chrome_options(profile_dir, perf_logging=perf_logging)
        try:
            svc = Service(ChromeDriverManager().install())
            return webdriver.Chrome(service=svc, options=opts)
        except Exception:
            return webdriver.Chrome(options=opts)

    try:
        return _launch()
    except Exception as first_err:
        log_message(f"WARNING {label}: первый запуск Chrome не удался ({first_err}), повтор после очистки...")
        kill_profile_chrome(profile_dir, label=label)
        time.sleep(1.0)
        return _launch()


def quit_driver_safe(driver) -> None:
    """Аккуратно закрывает Selenium WebDriver без необработанных исключений."""
    if driver is None:
        return
    try:
        driver.quit()
    except Exception:
        pass
