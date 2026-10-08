r"""
YTD Launcher — маленький трей-лаунчер для основной программы (app_qt.py).

Запуск: двойной клик по launcher.pyw (откроется через pythonw, без консоли)
или `pythonw launcher.pyw`.

Умеет:
  • показывать, запущена ли программа;
  • Запустить / Перезапустить / Остановить;
  • ставить/снимать автозапуск при старте Windows (ключ реестра HKCU ...\Run);
  • открыть папку программы.

«Выход» закрывает только лаунчер — сама программа продолжает работать.
"""

import os
import sys
import subprocess

try:
    import winreg
except ImportError:
    winreg = None

from PyQt6.QtWidgets import QApplication, QSystemTrayIcon, QMenu
from PyQt6.QtGui import (QIcon, QAction, QPixmap, QPainter, QColor, QFont, QPen,
                         QPolygonF)
from PyQt6.QtCore import Qt, QTimer, QRectF, QPointF

# ---------------------------------------------------------------- пути/константы
BASE_DIR        = os.path.dirname(os.path.abspath(__file__))
APP_SCRIPT      = os.path.join(BASE_DIR, "app_qt.py")
LAUNCHER_SCRIPT = os.path.join(BASE_DIR, "launcher.pyw")
ICON_PATH       = os.path.join(BASE_DIR, "icon.ico")

# Интерпретатор: берём pythonw.exe рядом с текущим (чтобы запуск был без консоли)
_PY = sys.executable
if os.path.basename(_PY).lower() == "python.exe":
    _cand = os.path.join(os.path.dirname(_PY), "pythonw.exe")
    if os.path.exists(_cand):
        _PY = _cand

# В автозапуск ставим САМ лаунчер (он живёт в трее и управляет программой)
AUTOSTART_CMD  = f'"{_PY}" "{LAUNCHER_SCRIPT}"'
RUN_KEY        = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_VALUE_NAME = "YTD Launcher"

# Флаги CreateProcess (Windows)
_CREATE_NO_WINDOW      = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
_DETACHED_PROCESS      = getattr(subprocess, "DETACHED_PROCESS", 0x00000008)
_CREATE_NEW_PROC_GROUP = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)


# ---------------------------------------------------------------- процессы app
def _app_pids():
    """PID'ы запущенных экземпляров программы (python/pythonw с app_qt.py)."""
    ps = (
        "Get-CimInstance Win32_Process -Filter \"Name='python.exe' or "
        "Name='pythonw.exe'\" | Where-Object { $_.CommandLine -like "
        "'*app_qt.py*' } | ForEach-Object { $_.ProcessId }"
    )
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps],
            capture_output=True, text=True, timeout=15,
            creationflags=_CREATE_NO_WINDOW,
        ).stdout
        return [int(x) for x in out.split() if x.strip().isdigit()]
    except Exception:
        return []


def _is_running():
    return bool(_app_pids())


def _start_app():
    """Запускает программу отдельным процессом (переживает закрытие лаунчера)."""
    if _is_running():
        return False
    subprocess.Popen(
        [_PY, APP_SCRIPT],
        cwd=BASE_DIR,
        creationflags=_DETACHED_PROCESS | _CREATE_NEW_PROC_GROUP,
        close_fds=True,
    )
    return True


def _stop_app():
    """Останавливает все экземпляры программы вместе с дочерними процессами
    (taskkill /T — закроет и браузер VK, если он был открыт)."""
    pids = _app_pids()
    for pid in pids:
        try:
            subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                           creationflags=_CREATE_NO_WINDOW,
                           capture_output=True, timeout=15)
        except Exception:
            pass
    return len(pids)


# ---------------------------------------------------------------- автозапуск
def _is_autostart():
    if winreg is None:
        return False
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            val, _ = winreg.QueryValueEx(k, RUN_VALUE_NAME)
            return bool(val)
    except FileNotFoundError:
        return False
    except OSError:
        return False


def _set_autostart(enable: bool) -> bool:
    if winreg is None:
        return False
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0,
                            winreg.KEY_WRITE) as k:
            if enable:
                winreg.SetValueEx(k, RUN_VALUE_NAME, 0, winreg.REG_SZ, AUTOSTART_CMD)
            else:
                try:
                    winreg.DeleteValue(k, RUN_VALUE_NAME)
                except FileNotFoundError:
                    pass
        return True
    except Exception:
        return False


# ---------------------------------------------------------------- single instance
_MUTEX_HANDLE = None

def _acquire_single_instance() -> bool:
    """True — мы единственный экземпляр, False — лаунчер уже запущен.
    Именованный мьютекс ОС освобождает при выходе процесса (в т.ч. при kill),
    в отличие от QSharedMemory, который может 'залипать' и блокировать запуск."""
    global _MUTEX_HANDLE
    if sys.platform != "win32":
        return True
    try:
        import ctypes
        k = ctypes.windll.kernel32
        _MUTEX_HANDLE = k.CreateMutexW(None, False, "YTD_Launcher_singleton")
        ERROR_ALREADY_EXISTS = 183
        return k.GetLastError() != ERROR_ALREADY_EXISTS
    except Exception:
        return True   # не удалось проверить — лучше запуститься, чем молчать


# ---------------------------------------------------------------- лаунчер
class Launcher:
    def __init__(self, app: QApplication):
        self.app = app
        self._icon_cache = self._build_icon()
        self.tray = QSystemTrayIcon(self._icon())
        self.tray.setToolTip("YTD — лаунчер")
        self.menu = QMenu()
        self.menu.aboutToShow.connect(self._rebuild_menu)
        self.tray.setContextMenu(self.menu)
        self.tray.activated.connect(self._on_activated)
        self._rebuild_menu()
        self.tray.show()

    # ------- иконка (рисуем сами, чтобы отличалась от красной иконки программы)
    def _icon(self) -> QIcon:
        return self._icon_cache

    def _draw_gear(self, p, cx, cy, r, color, hole_color):
        """Маленькая шестерёнка — признак «лаунчер/управление»."""
        p.save()
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(color)
        teeth = 8
        for i in range(teeth):
            p.save()
            p.translate(cx, cy)
            p.rotate(360.0 / teeth * i)
            p.drawRoundedRect(QRectF(-r * 0.22, -r * 1.25, r * 0.44, r * 0.6), 1.0, 1.0)
            p.restore()
        p.drawEllipse(QPointF(cx, cy), r * 0.85, r * 0.85)
        p.setBrush(hole_color)
        p.drawEllipse(QPointF(cx, cy), r * 0.36, r * 0.36)
        p.restore()

    def _build_icon(self) -> QIcon:
        pix = QPixmap(64, 64)
        pix.fill(Qt.GlobalColor.transparent)
        p = QPainter(pix)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        bg = QColor("#3949AB")                 # индиго — не как красный у программы
        p.setBrush(bg)
        p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(4, 4, 56, 56, 14, 14)
        # белый треугольник ▶ (слегка левее центра, чтобы освободить угол)
        p.setBrush(QColor("white"))
        p.drawPolygon(QPolygonF([QPointF(23, 19), QPointF(23, 45), QPointF(45, 32)]))
        # шестерёнка в правом нижнем углу
        self._draw_gear(p, 50, 50, 9, QColor("#FFD54F"), bg)
        p.end()
        return QIcon(pix)

    # ------- построение меню под текущее состояние
    def _rebuild_menu(self):
        self.menu.clear()
        running = _is_running()

        status = QAction(("● Программа запущена" if running
                          else "○ Программа остановлена"), self.menu)
        status.setEnabled(False)
        self.menu.addAction(status)
        self.menu.addSeparator()

        act_start = self.menu.addAction("Запустить")
        act_start.setEnabled(not running)
        act_start.triggered.connect(self._do_start)

        act_restart = self.menu.addAction("Перезапустить")
        act_restart.triggered.connect(self._do_restart)

        act_stop = self.menu.addAction("Остановить")
        act_stop.setEnabled(running)
        act_stop.triggered.connect(self._do_stop)

        self.menu.addSeparator()

        act_auto = self.menu.addAction("Запускать при старте Windows")
        act_auto.setCheckable(True)
        act_auto.setChecked(_is_autostart())
        act_auto.triggered.connect(self._do_toggle_autostart)

        self.menu.addSeparator()

        act_folder = self.menu.addAction("Открыть папку программы")
        act_folder.triggered.connect(self._do_open_folder)

        act_exit = self.menu.addAction("Выход (закрыть лаунчер)")
        act_exit.triggered.connect(self.app.quit)

    # ------- действия
    def _notify(self, title, text):
        try:
            self.tray.showMessage(title, text, self._icon(), 3000)
        except Exception:
            pass

    def _do_start(self):
        if _start_app():
            self._notify("YTD", "Программа запущена")
        else:
            self._notify("YTD", "Программа уже запущена")

    def _do_stop(self):
        n = _stop_app()
        self._notify("YTD", f"Остановлено процессов: {n}" if n else "Программа не запущена")

    def _do_restart(self):
        _stop_app()
        # небольшая пауза, чтобы профиль/порты освободились
        QTimer.singleShot(1500, self._restart_start)

    def _restart_start(self):
        _start_app()
        self._notify("YTD", "Программа перезапущена")

    def _do_toggle_autostart(self, checked: bool):
        ok = _set_autostart(checked)
        if ok:
            self._notify("YTD", "Автозапуск включён" if checked else "Автозапуск выключен")
        else:
            self._notify("YTD", "Не удалось изменить автозапуск")

    def _do_open_folder(self):
        try:
            os.startfile(BASE_DIR)   # noqa (Windows)
        except Exception:
            pass

    def _on_activated(self, reason):
        # Двойной клик — запустить программу, если ещё не запущена
        if reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            self._do_start()


def main():
    # Отдельный AppUserModelID — чтобы уведомления лаунчера работали корректно
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("YTD.Launcher.1.0")
        except Exception:
            pass

    # Один экземпляр: повторный запуск (двойной клик/автозапуск) просто выходит
    if not _acquire_single_instance():
        return

    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)   # живём в трее без окна

    if not QSystemTrayIcon.isSystemTrayAvailable():
        # без трея лаунчер бессмыслен — просто запускаем программу и выходим
        _start_app()
        return

    app._launcher = Launcher(app)  # держим ссылку, иначе соберёт GC
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
