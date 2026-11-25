"""
Окно проверки и обновления утилит (yt-dlp, yt-dlp-ejs, curl_cffi, ffmpeg, Node.js).
"""

import importlib.metadata
import os
import re
import shutil
import subprocess
import sys
import threading
import time

import requests
from PyQt6.QtCore import Qt, QObject, pyqtSignal
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView,
    QTextEdit, QCheckBox, QMessageBox
)

from config import settings, save_settings
from logger import log_message

_CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
_CHECK_INTERVAL_SEC = 7 * 24 * 3600  # 7 дней

PIP_PACKAGES = [
    ("yt-dlp", "Загрузка YouTube / VK / OK"),
    ("yt-dlp-ejs", "Решение YouTube n-challenge"),
    ("curl_cffi", "TLS-имперсонация для VK"),
]


def _get_pip_version(pkg_name: str) -> str:
    """Возвращает установленную версию Python-пакета или 'не установлен'."""
    try:
        return importlib.metadata.version(pkg_name)
    except importlib.metadata.PackageNotFoundError:
        return "не установлен"
    except Exception:
        return "неизвестно"


def _get_pypi_latest(pkg_name: str, timeout: float = 6.0) -> str | None:
    """Возвращает последнюю версию пакета на PyPI."""
    try:
        r = requests.get(f"https://pypi.org/pypi/{pkg_name}/json", timeout=timeout)
        if r.status_code == 200:
            return r.json().get("info", {}).get("version")
    except Exception as e:
        log_message(f"WARNING updater: не удалось проверить {pkg_name} на PyPI: {e}")
    return None


def _get_cli_version(cmd: list[str], pattern: str) -> str:
    """Запускает внешнюю утилиту и извлекает номер версии."""
    exe = cmd[0]
    if not shutil.which(exe):
        return "не найден в PATH"
    try:
        res = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=5,
            creationflags=_CREATE_NO_WINDOW,
            encoding="utf-8",
            errors="replace",
        )
        out = (res.stdout or "") + "\n" + (res.stderr or "")
        m = re.search(pattern, out)
        return m.group(1) if m else "установлен"
    except Exception:
        return "ошибка проверки"


def _parse_ver_tuple(v: str) -> tuple:
    """Преобразует строку версии в кортеж чисел для корректного сравнения."""
    parts = re.findall(r"\d+", v or "")
    return tuple(int(p) for p in parts)


def is_newer_version(installed: str, latest: str | None) -> bool:
    """True, если latest новее installed (или пакет не установлен)."""
    if not latest:
        return False
    if installed in ("не установлен", "неизвестно", ""):
        return True
    return _parse_ver_tuple(latest) > _parse_ver_tuple(installed)


def check_updates_if_due(on_available_cb) -> None:
    """Фоновая проверка обновлений yt-dlp раз в 7 дней. Не блокирует запуск UI."""
    if not settings.get("auto_check_updates", True):
        return
    last_check = float(settings.get("last_update_check", 0) or 0)
    if (time.time() - last_check) < _CHECK_INTERVAL_SEC:
        return

    def _worker():
        cur = _get_pip_version("yt-dlp")
        latest = _get_pypi_latest("yt-dlp", timeout=5.0)
        settings["last_update_check"] = int(time.time())
        save_settings(settings)
        if latest and is_newer_version(cur, latest):
            log_message(f"INFO Доступно обновление yt-dlp: {cur} -> {latest}")
            if on_available_cb:
                on_available_cb(cur, latest)
        elif latest:
            log_message(f"INFO Автопроверка обновлений: yt-dlp актуален ({cur})")

    threading.Thread(target=_worker, daemon=True).start()


class _UpdaterSignals(QObject):
    rows_ready = pyqtSignal(list)       # [(name, desc, installed, latest, status_text)]
    log_line = pyqtSignal(str)
    busy_changed = pyqtSignal(bool)
    update_finished = pyqtSignal(bool, str)


class UpdaterWindow(QWidget):
    """Окно проверки и обновления утилит приложения."""

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Обновления утилит")
        self.resize(740, 460)
        self._busy = False
        self._sig = _UpdaterSignals()
        self._sig.rows_ready.connect(self._populate_table)
        self._sig.log_line.connect(self._append_log)
        self._sig.busy_changed.connect(self._set_busy)
        self._sig.update_finished.connect(self._on_update_finished)

        self._setup_ui()
        self.check_versions()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        header = QLabel("Версии и обновления утилит")
        f = QFont()
        f.setPointSize(12)
        f.setBold(True)
        header.setFont(f)
        layout.addWidget(header)

        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(
            ["Компонент", "Назначение", "Установлено", "Доступно", "Статус"]
        )
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(False)
        self.table.setShowGrid(False)
        hdr = self.table.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        hdr.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        layout.addWidget(self.table, 2)

        self.log_box = QTextEdit()
        self.log_box.setReadOnly(True)
        self.log_box.setFont(QFont("Consolas", 9))
        self.log_box.setMaximumHeight(140)
        self.log_box.setPlaceholderText("Здесь появится вывод процесса обновления...")
        layout.addWidget(self.log_box, 1)

        controls = QHBoxLayout()
        self.chk_auto = QCheckBox("Автопроверка раз в 7 дней")
        self.chk_auto.setChecked(settings.get("auto_check_updates", True))
        self.chk_auto.toggled.connect(self._toggle_auto)
        controls.addWidget(self.chk_auto)

        controls.addStretch()

        self.btn_check = QPushButton("Проверить")
        self.btn_check.setProperty("secondary", True)
        self.btn_check.clicked.connect(self.check_versions)
        controls.addWidget(self.btn_check)

        self.btn_update_pip = QPushButton("Обновить yt-dlp и модули")
        self.btn_update_pip.clicked.connect(self._update_pip_packages)
        controls.addWidget(self.btn_update_pip)

        self.btn_update_winget = QPushButton("Обновить ffmpeg / Node (winget)")
        self.btn_update_winget.setProperty("secondary", True)
        self.btn_update_winget.setEnabled(bool(shutil.which("winget")))
        self.btn_update_winget.clicked.connect(self._update_winget_tools)
        controls.addWidget(self.btn_update_winget)

        layout.addLayout(controls)

    def _toggle_auto(self, checked: bool):
        settings["auto_check_updates"] = bool(checked)
        save_settings(settings)
        log_message(f"INFO Автопроверка обновлений: {'вкл' if checked else 'выкл'}")

    def _set_busy(self, busy: bool):
        self._busy = busy
        self.btn_check.setEnabled(not busy)
        self.btn_update_pip.setEnabled(not busy)
        self.btn_update_winget.setEnabled((not busy) and bool(shutil.which("winget")))

    def _append_log(self, line: str):
        self.log_box.append(line)

    def _populate_table(self, rows: list):
        self.table.setRowCount(len(rows))
        for r, (name, desc, cur, latest, status) in enumerate(rows):
            self.table.setItem(r, 0, QTableWidgetItem(name))
            self.table.setItem(r, 1, QTableWidgetItem(desc))
            self.table.setItem(r, 2, QTableWidgetItem(cur))
            self.table.setItem(r, 3, QTableWidgetItem(latest))
            self.table.setItem(r, 4, QTableWidgetItem(status))

    def check_versions(self):
        if self._busy:
            return
        self._sig.busy_changed.emit(True)
        self.log_box.clear()
        self._sig.log_line.emit("Проверяю установленные версии и PyPI...")

        def _worker():
            rows = []
            for pkg, desc in PIP_PACKAGES:
                cur = _get_pip_version(pkg)
                latest = _get_pypi_latest(pkg)
                if latest is None:
                    status = "нет связи с PyPI"
                elif is_newer_version(cur, latest):
                    status = "есть обновление"
                else:
                    status = "актуально"
                rows.append((pkg, desc, cur, latest or "—", status))

            ff_ver = _get_cli_version(["ffmpeg", "-version"], r"ffmpeg version ([^\s]+)")
            rows.append(("ffmpeg", "Конвертация MP3 / MP4", ff_ver, "через winget", "установлен" if "не найден" not in ff_ver else "не найден"))

            node_ver = _get_cli_version(["node", "-v"], r"v?([0-9]+\.[0-9]+\.[0-9]+)")
            rows.append(("Node.js", "Решение YouTube n-challenge", node_ver, "через winget", "установлен" if "не найден" not in node_ver else "не найден"))

            settings["last_update_check"] = int(time.time())
            save_settings(settings)

            self._sig.rows_ready.emit(rows)
            self._sig.log_line.emit("Проверка завершена.")
            self._sig.busy_changed.emit(False)

        threading.Thread(target=_worker, daemon=True).start()

    def _run_update_cmd(self, cmd: list[str], title: str):
        if self._busy:
            return
        self._sig.busy_changed.emit(True)
        self.log_box.clear()
        self._sig.log_line.emit(f"Запуск: {' '.join(cmd)}")
        log_message(f"INFO Обновление утилит ({title}): {' '.join(cmd)}")

        def _worker():
            try:
                proc = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    creationflags=_CREATE_NO_WINDOW,
                )
                for line in proc.stdout:
                    line = line.rstrip()
                    if line:
                        self._sig.log_line.emit(line)
                proc.wait()
                ok = proc.returncode == 0
                if ok:
                    log_message(f"SUCCESS Обновление завершено: {title}")
                    self._sig.update_finished.emit(True, title)
                else:
                    log_message(f"ERROR Ошибка обновления ({title}), код {proc.returncode}")
                    self._sig.update_finished.emit(False, f"{title} (код {proc.returncode})")
            except Exception as e:
                log_message(f"ERROR Ошибка запуска обновления ({title}): {e}")
                self._sig.log_line.emit(f"Ошибка: {e}")
                self._sig.update_finished.emit(False, str(e))
            finally:
                self._sig.busy_changed.emit(False)

        threading.Thread(target=_worker, daemon=True).start()

    def _update_pip_packages(self):
        pkgs = [pkg for pkg, _ in PIP_PACKAGES]
        cmd = [sys.executable, "-m", "pip", "install", "-U"] + pkgs
        self._run_update_cmd(cmd, "yt-dlp и модули")

    def _update_winget_tools(self):
        if not shutil.which("winget"):
            QMessageBox.warning(self, "winget не найден", "Утилита winget не найдена в системе.")
            return
        cmd = [
            "winget", "upgrade", "--id", "Gyan.FFmpeg",
            "--accept-package-agreements", "--accept-source-agreements",
        ]
        self._run_update_cmd(cmd, "ffmpeg (winget)")

    def _on_update_finished(self, ok: bool, msg: str):
        if ok:
            self.check_versions()
            QMessageBox.information(
                self,
                "Обновление завершено",
                f"Обновление ({msg}) выполнено успешно.\n\n"
                "Перезапустите программу, чтобы новые версии модулей подхватились в памяти."
            )
        else:
            QMessageBox.warning(
                self,
                "Ошибка обновления",
                f"Не удалось выполнить обновление: {msg}\nПодробности в окне вывода и log.txt."
            )

    def closeEvent(self, event):
        event.ignore()
        self.hide()
