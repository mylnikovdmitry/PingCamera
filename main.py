import flet as ft
import json
import os
import socket
import subprocess
import platform
import threading
import time
from datetime import datetime
from typing import Optional

# ─────────────────────────────────────────────
#  КОНСТАНТЫ
# ─────────────────────────────────────────────
PORTS_TO_CHECK = [
    80, 443, 554, 8000, 8080, 8888,
    37777, 34567, 5000, 5555,
    62078, 22, 23, 3389, 7,
]
SOCKET_TIMEOUT = 0.8
ASSETS_DIR     = "assets"
IS_WINDOWS     = platform.system() == "Windows"
BASE_DIR       = os.path.dirname(
    os.path.abspath(__file__))

STATUS_UNKNOWN  = "unknown"
STATUS_ONLINE   = "online"
STATUS_OFFLINE  = "offline"
STATUS_CHECKING = "checking"

DEVICE_TYPES = [
    "Камера", "Компьютер", "NVR",
    "Коммутатор", "Другое",
]

APP_BG      = "#1a1a20"
HEADER_BG   = "#0d0d14"
ROW_BG_EVEN = "#16161e"
ROW_BG_ODD  = "#1c1c26"

MARK_BG = {
    "none":   None,
    "red":    "#2a0d0d",
    "yellow": "#2a2000",
    "green":  "#0d2a0d",
    "blue":   "#0d1a2a",
}
MARK_COLORS = {
    "none":   None,
    "red":    ft.colors.RED_400,
    "yellow": ft.colors.YELLOW_600,
    "green":  ft.colors.GREEN_400,
    "blue":   ft.colors.BLUE_400,
}
STATUS_COLORS = {
    STATUS_UNKNOWN:  ft.colors.GREY_500,
    STATUS_ONLINE:   ft.colors.GREEN_400,
    STATUS_OFFLINE:  ft.colors.RED_400,
    STATUS_CHECKING: ft.colors.ORANGE_400,
}
STATUS_TEXT = {
    STATUS_UNKNOWN:  "—",
    STATUS_ONLINE:   "● ОНЛАЙН",
    STATUS_OFFLINE:  "● ОФФЛАЙН",
    STATUS_CHECKING: "○ Проверка...",
}
DEVICE_ICONS = {
    "Камера":     ft.icons.VIDEOCAM,
    "Компьютер":  ft.icons.COMPUTER,
    "NVR":        ft.icons.VIDEO_LIBRARY,
    "Коммутатор": ft.icons.DEVICE_HUB,
    "Другое":     ft.icons.DEVICES_OTHER,
}
TIMER_OPTIONS = {
    "Выкл":  0,
    "15 с":  15,
    "30 с":  30,
    "1 мин": 60,
    "5 мин": 300,
}
MARK_LABELS = {
    "none":   "— Нет",
    "red":    "🔴 Проблема",
    "yellow": "🟡 Вопрос",
    "green":  "🟢 ОК",
    "blue":   "🔵 В работе",
}

SORT_NONE    = ""
SORT_IP      = "ip"
SORT_STATUS  = "status"
SORT_PING    = "ping"
SORT_COMMENT = "comment"


# ─────────────────────────────────────────────
#  ХРАНИЛИЩЕ ПРОЕКТОВ
# ─────────────────────────────────────────────
def get_projects_dir() -> str:
    """
    Android: /data/user/0/<pkg>/files/projects/
    Windows: <папка main.py>/projects/
    """
    try:
        from jnius import autoclass
        PythonActivity = autoclass(
            "org.kivy.android.PythonActivity")
        activity  = PythonActivity.mActivity
        files_dir = activity.getFilesDir()
        base = str(files_dir.getAbsolutePath())
        d = os.path.join(base, "projects")
    except Exception:
        d = os.path.join(BASE_DIR, "projects")
    os.makedirs(d, exist_ok=True)
    return d


def get_assets_projects_dir() -> str:
    """
    assets/projects/ — встроенные проекты,
    упакованные в APK.
    """
    return os.path.join(
        BASE_DIR, ASSETS_DIR, "projects")


def install_bundled_projects():
    """
    При первом запуске копирует встроенные
    проекты из assets/projects/ в рабочую папку.
    Не перезаписывает уже существующие файлы
    (пользователь мог их изменить).
    """
    src = get_assets_projects_dir()
    dst = get_projects_dir()
    if not os.path.isdir(src):
        return 0
    copied = 0
    for fname in os.listdir(src):
        if not fname.endswith(".json"):
            continue
        src_file = os.path.join(src, fname)
        dst_file = os.path.join(dst, fname)
        if not os.path.exists(dst_file):
            try:
                with open(src_file, "r",
                          encoding="utf-8") as f:
                    data = f.read()
                with open(dst_file, "w",
                          encoding="utf-8") as f:
                    f.write(data)
                copied += 1
            except Exception:
                pass
    return copied


# ─────────────────────────────────────────────
#  МОДЕЛИ
# ─────────────────────────────────────────────
class Device:
    def __init__(
        self,
        ip:          str,
        comment:     str  = "",
        device_type: str  = "Камера",
        sound_alert: bool = False,
        mark:        str  = "none",
    ):
        self.ip          = ip
        self.comment     = comment
        self.device_type = device_type
        self.sound_alert = sound_alert
        self.mark        = mark
        self.status:     str            = STATUS_UNKNOWN
        self.ping_ms:    Optional[float] = None
        self.loss_pct:   Optional[float] = None
        self.last_check: Optional[str]   = None

    def to_dict(self) -> dict:
        return {
            "ip":          self.ip,
            "comment":     self.comment,
            "device_type": self.device_type,
            "sound_alert": self.sound_alert,
            "mark":        self.mark,
        }

    @staticmethod
    def from_dict(d: dict) -> "Device":
        return Device(
            ip=          d.get("ip",          ""),
            comment=     d.get("comment",     ""),
            device_type= d.get("device_type", "Камера"),
            sound_alert= d.get("sound_alert", False),
            mark=        d.get("mark",        "none"),
        )

    def ip_sort_key(self) -> tuple:
        try:
            return tuple(
                int(x)
                for x in self.ip.split("."))
        except Exception:
            return (999, 999, 999, 999)

    def ping_sort_key(self) -> float:
        if self.status == STATUS_ONLINE:
            return self.ping_ms or 0.0
        return 99999.0


class Project:
    def __init__(
        self,
        name:        str = "Новый проект",
        description: str = "",
    ):
        self.name        = name
        self.description = description
        self.devices:  list          = []
        self.filepath: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "name":        self.name,
            "description": self.description,
            "devices": [
                d.to_dict()
                for d in self.devices],
        }

    @staticmethod
    def from_dict(d: dict) -> "Project":
        p = Project(
            name=        d.get("name",
                               "Проект"),
            description= d.get("description",
                               ""),
        )
        p.devices = [
            Device.from_dict(dev)
            for dev in d.get("devices", [])
        ]
        return p


# ─────────────────────────────────────────────
#  PING
# ─────────────────────────────────────────────
def icmp_ping_windows(ip: str,
                      count: int = 1) -> tuple:
    try:
        result = subprocess.run(
            ["ping", "-n", str(count),
             "-w", "800", ip],
            capture_output=True,
            text=True,
            encoding="cp866",
            creationflags=
            subprocess.CREATE_NO_WINDOW,
            timeout=count * 2 + 1,
        )
        out = result.stdout.upper()
        if "TTL=" in out:
            import re
            m = re.search(
                r"(?:ВРЕМЯ|TIME)[<=](\d+)",
                out)
            ms = float(m.group(1)) if m else 1.0
            return True, ms
    except Exception:
        pass
    return False, 0.0


def icmp_ping_unix(ip: str,
                   count: int = 1) -> tuple:
    try:
        result = subprocess.run(
            ["ping", "-c", str(count),
             "-W", "1", ip],
            capture_output=True,
            text=True,
            timeout=count * 2 + 1,
        )
        if result.returncode == 0:
            import re
            m = re.search(
                r"time=([\d.]+)",
                result.stdout)
            ms = float(m.group(1)) if m else 1.0
            return True, ms
    except Exception:
        pass
    return False, 0.0


def tcp_check(ip: str) -> tuple:
    for port in PORTS_TO_CHECK:
        try:
            t0 = time.monotonic()
            s  = socket.socket(
                socket.AF_INET,
                socket.SOCK_STREAM)
            s.settimeout(SOCKET_TIMEOUT)
            r  = s.connect_ex((ip, port))
            ms = (time.monotonic() - t0) * 1000
            s.close()
            if r == 0:
                return True, round(ms, 1)
        except Exception:
            pass
    return False, 0.0


def smart_ping(ip: str) -> tuple:
    """
    ПК Windows : ICMP → TCP
    ПК Linux   : ICMP → TCP
    Android    : только TCP (нет root)
    """
    try:
        import android  # noqa
        is_android = True
    except ImportError:
        is_android = False

    if not is_android:
        if IS_WINDOWS:
            ok, ms = icmp_ping_windows(ip)
        else:
            ok, ms = icmp_ping_unix(ip)
        if ok:
            return True, round(ms, 1)

    return tcp_check(ip)


# ─────────────────────────────────────────────
#  ФАЙЛОВЫЕ ФУНКЦИИ
# ─────────────────────────────────────────────
def list_project_files() -> list:
    d = get_projects_dir()
    result = []
    try:
        for f in sorted(os.listdir(d)):
            if f.endswith(".json"):
                result.append(
                    os.path.join(d, f))
    except Exception:
        pass
    return result


def save_project(project: Project,
                 filepath: str):
    os.makedirs(
        os.path.dirname(filepath) or ".",
        exist_ok=True)
    with open(filepath, "w",
              encoding="utf-8") as fh:
        json.dump(
            project.to_dict(), fh,
            ensure_ascii=False, indent=2)
    project.filepath = filepath


def load_project_file(
        filepath: str) -> Project:
    with open(filepath, "r",
              encoding="utf-8") as fh:
        data = json.load(fh)
    p = Project.from_dict(data)
    p.filepath = filepath
    return p


def safe_filename(name: str) -> str:
    allowed = (
        "абвгдеёжзийклмнопрстуфхцчшщъыьэюя"
        "АБВГДЕЁЖЗИЙКЛМНОПРСТУФХЦЧШЩЪЫЬЭЮЯ"
        "abcdefghijklmnopqrstuvwxyz"
        "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
        "0123456789_- "
    )
    cleaned = "".join(
        c for c in name if c in allowed)
    return (cleaned.strip()
            .replace(" ", "_") or "project")


# ─────────────────────────────────────────────
#  ГЛАВНАЯ ФУНКЦИЯ
# ─────────────────────────────────────────────
def main(page: ft.Page):

    # Распаковываем встроенные проекты
    install_bundled_projects()

    page.title         = "Ping Camera Monitor"
    page.theme_mode    = ft.ThemeMode.DARK
    page.padding       = 0
    page.bgcolor       = APP_BG
    page.window_width  = 420
    page.window_height = 780

    # ── состояние ─────────────────────────────
    state = {
        "project":    Project(),
        "pinging":    False,
        "stop_timer": threading.Event(),
        "timer_sec":  0,
        "sort_col":   SORT_NONE,
        "sort_asc":   True,
    }

    def proj() -> Project:
        return state["project"]

    # ── шапка ─────────────────────────────────
    lbl_proj = ft.Text(
        "Новый проект",
        size=16,
        weight=ft.FontWeight.BOLD,
        color=ft.colors.CYAN_200,
        expand=True,
    )
    lbl_online = ft.Text(
        "● 0", size=15,
        weight=ft.FontWeight.BOLD,
        color=ft.colors.GREEN_400,
    )
    lbl_offline = ft.Text(
        "● 0", size=15,
        weight=ft.FontWeight.BOLD,
        color=ft.colors.RED_400,
    )
    lbl_total = ft.Text(
        "всего 0", size=13,
        color=ft.colors.GREY_500,
    )
    lbl_status = ft.Text(
        "Готово", size=12,
        color=ft.colors.GREEN_300,
    )

    def update_stats():
        devs    = proj().devices
        total   = len(devs)
        online  = sum(1 for d in devs
                      if d.status == STATUS_ONLINE)
        offline = sum(1 for d in devs
                      if d.status == STATUS_OFFLINE)
        lbl_online.value  = f"● {online}"
        lbl_offline.value = f"● {offline}"
        lbl_total.value   = f"всего {total}"

    # ── сортировка ────────────────────────────
    def get_sorted_devices() -> list:
        col  = state["sort_col"]
        asc  = state["sort_asc"]
        devs = list(proj().devices)
        if col == SORT_IP:
            devs.sort(
                key=lambda d: d.ip_sort_key(),
                reverse=not asc)
        elif col == SORT_STATUS:
            order = {
                STATUS_ONLINE:   0,
                STATUS_CHECKING: 1,
                STATUS_UNKNOWN:  2,
                STATUS_OFFLINE:  3,
            }
            devs.sort(
                key=lambda d: order.get(
                    d.status, 9),
                reverse=not asc)
        elif col == SORT_PING:
            devs.sort(
                key=lambda d: d.ping_sort_key(),
                reverse=not asc)
        elif col == SORT_COMMENT:
            devs.sort(
                key=lambda d: (
                    d.comment or "").lower(),
                reverse=not asc)
        return devs

    def set_sort(col: str):
        if state["sort_col"] == col:
            state["sort_asc"] = (
                not state["sort_asc"])
        else:
            state["sort_col"] = col
            state["sort_asc"] = True
        rebuild_col_header()
        refresh_devices()

    def reset_sort():
        state["sort_col"] = SORT_NONE
        state["sort_asc"] = True
        rebuild_col_header()
        refresh_devices()

    # ── строка устройства ─────────────────────
    def build_row(device: Device,
                  display_idx: int) -> ft.Container:
        sc  = STATUS_COLORS.get(
            device.status, ft.colors.GREY_500)
        stx = STATUS_TEXT.get(
            device.status, "—")
        bg  = MARK_BG.get(device.mark)
        if bg is None:
            bg = (ROW_BG_EVEN
                  if display_idx % 2 == 0
                  else ROW_BG_ODD)

        if (device.status == STATUS_ONLINE
                and device.ping_ms is not None):
            ping_txt = f"{device.ping_ms} мс"
            ping_clr = ft.colors.GREEN_300
        elif device.status == STATUS_OFFLINE:
            ping_txt = "—"
            ping_clr = ft.colors.GREY_600
        elif device.status == STATUS_CHECKING:
            ping_txt = "..."
            ping_clr = ft.colors.ORANGE_400
        else:
            ping_txt = "—"
            ping_clr = ft.colors.GREY_600

        mark_clr = MARK_COLORS.get(device.mark)
        mark_bar = ft.Container(
            width=4,
            bgcolor=(mark_clr
                     if mark_clr
                     else ft.colors.TRANSPARENT),
        )

        real_idx = proj().devices.index(device)

        col_ip = ft.Container(
            content=ft.Text(
                device.ip,
                size=13,
                weight=ft.FontWeight.W_600,
                color=ft.colors.CYAN_200,
                no_wrap=True,
            ),
            width=115,
        )

        col_comment = ft.Container(
            content=ft.Column([
                ft.Text(
                    device.comment or "—",
                    size=12,
                    color=ft.colors.WHITE,
                    no_wrap=True,
                ),
                ft.Text(
                    device.device_type,
                    size=10,
                    color=ft.colors.BLUE_200,
                ),
            ], spacing=0),
            expand=True,
        )

        col_status = ft.Container(
            content=ft.Column([
                ft.Text(
                    stx, size=11, color=sc,
                    weight=ft.FontWeight.BOLD,
                    no_wrap=True,
                ),
                ft.Text(
                    device.last_check or "",
                    size=9,
                    color=ft.colors.GREY_600,
                ),
            ], spacing=0),
            width=82,
        )

        col_ping = ft.Container(
            content=ft.Text(
                ping_txt,
                size=12,
                color=ping_clr,
                no_wrap=True,
            ),
            width=55,
        )

        col_btns = ft.Row([
            ft.IconButton(
                icon=ft.icons.REFRESH,
                icon_color=ft.colors.BLUE_300,
                icon_size=16,
                tooltip="Пинг",
                on_click=lambda e,
                d=device: _ping_one(d),
                padding=ft.padding.all(2),
            ),
            ft.IconButton(
                icon=ft.icons.EDIT,
                icon_color=ft.colors.AMBER_400,
                icon_size=16,
                tooltip="Редактировать",
                on_click=lambda e,
                i=real_idx: open_edit_dlg(i),
                padding=ft.padding.all(2),
            ),
            ft.IconButton(
                icon=ft.icons.DELETE_OUTLINE,
                icon_color=ft.colors.RED_300,
                icon_size=16,
                tooltip="Удалить",
                on_click=lambda e,
                i=real_idx: confirm_delete(i),
                padding=ft.padding.all(2),
            ),
        ], spacing=0, tight=True)

        return ft.Container(
            content=ft.Row([
                mark_bar,
                col_ip,
                col_comment,
                col_status,
                col_ping,
                col_btns,
            ], spacing=4,
               vertical_alignment=
               ft.CrossAxisAlignment.CENTER),
            bgcolor=bg,
            height=52,
            padding=ft.padding.symmetric(
                horizontal=4, vertical=2),
            border=ft.border.only(
                bottom=ft.BorderSide(
                    1, "#2a2a35")),
        )

    # ── список устройств ──────────────────────
    device_list = ft.ListView(
        expand=True, spacing=0,
        padding=ft.padding.all(0),
    )

    def refresh_devices():
        device_list.controls.clear()
        if not proj().devices:
            device_list.controls.append(
                ft.Container(
                    content=ft.Column([
                        ft.Icon(
                            ft.icons.VIDEOCAM_OFF,
                            size=48,
                            color=ft.colors.GREY_800,
                        ),
                        ft.Text(
                            "Устройства не добавлены",
                            color=ft.colors.GREY_700,
                            size=14,
                            text_align=
                            ft.TextAlign.CENTER,
                        ),
                        ft.Text(
                            "Нажмите + чтобы добавить",
                            color=ft.colors.GREY_800,
                            size=12,
                            text_align=
                            ft.TextAlign.CENTER,
                        ),
                    ], horizontal_alignment=
                    ft.CrossAxisAlignment.CENTER,
                    spacing=10),
                    alignment=ft.alignment.center,
                    padding=60,
                )
            )
        else:
            for i, dev in enumerate(
                    get_sorted_devices()):
                device_list.controls.append(
                    build_row(dev, i))
        update_stats()
        try:
            page.update()
        except Exception:
            pass

    # ── заголовок колонок ─────────────────────
    col_header_row = ft.Row(
        spacing=4,
        vertical_alignment=
        ft.CrossAxisAlignment.CENTER,
    )
    col_header_container = ft.Container(
        content=col_header_row,
        bgcolor="#0d0d18",
        padding=ft.padding.symmetric(
            horizontal=4, vertical=2),
        border=ft.border.only(
            bottom=ft.BorderSide(
                1, "#2a2a3a")),
    )

    def _sort_icon(col: str) -> str:
        if state["sort_col"] != col:
            return "↕"
        return "↑" if state["sort_asc"] else "↓"

    def _hdr_btn(label: str, col: str,
                 width=None,
                 expand=False) -> ft.Container:
        is_active = state["sort_col"] == col
        color = (ft.colors.CYAN_300
                 if is_active
                 else ft.colors.BLUE_300)
        btn = ft.TextButton(
            content=ft.Text(
                f"{label} {_sort_icon(col)}",
                size=11,
                color=color,
                weight=(ft.FontWeight.BOLD
                        if is_active
                        else ft.FontWeight.NORMAL),
                no_wrap=True,
            ),
            on_click=lambda e,
            c=col: set_sort(c),
            style=ft.ButtonStyle(
                padding=ft.padding.symmetric(
                    horizontal=2, vertical=0),
            ),
        )
        return ft.Container(
            content=btn,
            width=width,
            expand=expand,
        )

    def rebuild_col_header():
        col_header_row.controls = [
            ft.Container(width=4),
            _hdr_btn("IP адрес",
                     SORT_IP, width=115),
            _hdr_btn("Комментарий / Тип",
                     SORT_COMMENT, expand=True),
            _hdr_btn("Статус",
                     SORT_STATUS, width=82),
            _hdr_btn("Пинг",
                     SORT_PING, width=55),
            ft.Container(
                content=ft.IconButton(
                    icon=ft.icons.SORT,
                    icon_color=(
                        ft.colors.CYAN_400
                        if state["sort_col"]
                        else ft.colors.GREY_700),
                    icon_size=16,
                    tooltip="Сбросить сортировку",
                    on_click=lambda e:
                        reset_sort(),
                    padding=ft.padding.all(0),
                ),
                width=100,
            ),
        ]
        try:
            page.update()
        except Exception:
            pass

    rebuild_col_header()

    # ── пинг ──────────────────────────────────
    def _ping_one(device: Device):
        def _do():
            device.status  = STATUS_CHECKING
            device.ping_ms = None
            refresh_devices()
            ok, ms = smart_ping(device.ip)
            device.status     = (STATUS_ONLINE
                                  if ok
                                  else STATUS_OFFLINE)
            device.ping_ms    = ms if ok else None
            device.last_check = (
                datetime.now()
                .strftime("%H:%M:%S"))
            refresh_devices()
        threading.Thread(
            target=_do, daemon=True).start()

    def ping_all():
        if state["pinging"]:
            return
        if not proj().devices:
            lbl_status.value = "⚠ Список пуст"
            page.update()
            return
        state["pinging"] = True
        btn_ping.text    = "■ СТОП"
        btn_ping.bgcolor = ft.colors.RED_900
        n = len(proj().devices)
        lbl_status.value = (
            f"Опрос {n} устройств...")
        page.update()

        devices = proj().devices[:]
        for d in devices:
            d.status  = STATUS_CHECKING
            d.ping_ms = None
        refresh_devices()

        def _do():
            threads = []
            for d in devices:
                def _one(dev=d):
                    ok, ms = smart_ping(dev.ip)
                    dev.status = (
                        STATUS_ONLINE
                        if ok
                        else STATUS_OFFLINE)
                    dev.ping_ms = (
                        ms if ok else None)
                    dev.last_check = (
                        datetime.now()
                        .strftime("%H:%M:%S"))
                threads.append(
                    threading.Thread(
                        target=_one,
                        daemon=True))
            for t in threads:
                t.start()
            for t in threads:
                t.join()

            on  = sum(1 for d in devices
                      if d.status == STATUS_ONLINE)
            off = sum(1 for d in devices
                      if d.status == STATUS_OFFLINE)
            state["pinging"] = False
            btn_ping.text    = "▶ ПИНГ"
            btn_ping.bgcolor = ft.colors.GREEN_900
            lbl_status.value = (
                f"✓ Онлайн: {on}"
                f"  Оффлайн: {off}")
            refresh_devices()

        threading.Thread(
            target=_do, daemon=True).start()

    # ── таймер ────────────────────────────────
    lbl_timer = ft.Text(
        "", size=12,
        color=ft.colors.CYAN_400,
    )

    def _stop_timer():
        state["stop_timer"].set()
        lbl_timer.value = ""

    def _start_timer(sec: int):
        _stop_timer()
        if sec <= 0:
            page.update()
            return
        state["stop_timer"] = threading.Event()
        state["timer_sec"]  = sec

        def _loop():
            remaining = sec
            while not state["stop_timer"].wait(
                    timeout=1):
                remaining -= 1
                m, s = divmod(remaining, 60)
                lbl_timer.value = (
                    f"⏱ {m}м {s:02d}с"
                    if m > 0
                    else f"⏱ {s}с")
                try:
                    page.update()
                except Exception:
                    break
                if remaining <= 0:
                    remaining = sec
                    lbl_timer.value = (
                        "⏱ пингую...")
                    try:
                        page.update()
                    except Exception:
                        break
                    ping_all()

        threading.Thread(
            target=_loop, daemon=True).start()

    # ── тулбар ────────────────────────────────
    btn_ping = ft.ElevatedButton(
        text="▶ ПИНГ",
        icon=ft.icons.NETWORK_PING,
        bgcolor=ft.colors.GREEN_900,
        color=ft.colors.WHITE,
        on_click=lambda e: ping_all(),
    )

    dd_timer = ft.Dropdown(
        hint_text="Авто",
        width=90,
        height=40,
        text_size=12,
        bgcolor=ft.colors.GREY_900,
        border_color=ft.colors.GREY_700,
        options=[
            ft.dropdown.Option(k)
            for k in TIMER_OPTIONS],
        on_change=lambda e: _start_timer(
            TIMER_OPTIONS.get(
                e.control.value, 0)),
    )

    toolbar = ft.Container(
        content=ft.Column([
            ft.Row([
                btn_ping,
                ft.ElevatedButton(
                    text="+ Добавить",
                    bgcolor=ft.colors.BLUE_900,
                    color=ft.colors.WHITE,
                    on_click=lambda e:
                        open_edit_dlg(-1),
                ),
                ft.Container(expand=True),
                ft.IconButton(
                    icon=ft.icons.SAVE,
                    icon_color=ft.colors.AMBER_400,
                    tooltip="Сохранить",
                    on_click=lambda e:
                        quick_save(),
                ),
            ], spacing=8),
            ft.Row([
                ft.Text(
                    "Авто:", size=12,
                    color=ft.colors.GREY_500),
                dd_timer,
                lbl_timer,
            ], spacing=6),
        ], spacing=4),
        padding=ft.padding.symmetric(
            horizontal=10, vertical=8),
        bgcolor=HEADER_BG,
        border=ft.border.only(
            bottom=ft.BorderSide(1, "#2a2a3a")),
    )

    def quick_save():
        if not proj().filepath:
            open_save_dlg()
            return
        try:
            save_project(proj(), proj().filepath)
            lbl_status.value = "✓ Сохранено"
            page.update()
        except Exception as ex:
            lbl_status.value = f"⚠ {ex}"
            page.update()

    # ── диалог добавить/редактировать ─────────
    def open_edit_dlg(idx: int):
        is_new = idx < 0
        dev    = (Device("") if is_new
                  else proj().devices[idx])

        fld_ip = ft.TextField(
            label="IP адрес",
            value=dev.ip,
            hint_text="192.168.1.100",
            bgcolor=ft.colors.GREY_900,
            border_color=ft.colors.BLUE_400,
            autofocus=True,
            keyboard_type=
            ft.KeyboardType.NUMBER,
        )
        fld_comment = ft.TextField(
            label="Комментарий",
            value=dev.comment,
            hint_text="Камера входа",
            bgcolor=ft.colors.GREY_900,
            border_color=ft.colors.BLUE_400,
        )
        dd_type = ft.Dropdown(
            label="Тип устройства",
            value=dev.device_type,
            bgcolor=ft.colors.GREY_900,
            options=[
                ft.dropdown.Option(t)
                for t in DEVICE_TYPES],
        )
        dd_mark = ft.Dropdown(
            label="Маркировка",
            value=dev.mark,
            bgcolor=ft.colors.GREY_900,
            options=[
                ft.dropdown.Option(k, text=v)
                for k, v in MARK_LABELS.items()],
        )
        lbl_err = ft.Text(
            "", color=ft.colors.RED_400,
            size=12)

        def on_save(e):
            ip = fld_ip.value.strip()
            if not ip:
                lbl_err.value = "Введите IP"
                page.update()
                return
            parts = ip.split(".")
            if not (len(parts) == 4 and
                    all(p.isdigit() and
                        0 <= int(p) <= 255
                        for p in parts)):
                lbl_err.value = (
                    "Неверный формат IP")
                page.update()
                return
            if is_new:
                existing = [
                    d.ip
                    for d in proj().devices]
                if ip in existing:
                    lbl_err.value = (
                        "IP уже в списке")
                    page.update()
                    return
            dev.ip          = ip
            dev.comment     = (
                fld_comment.value.strip())
            dev.device_type = (
                dd_type.value or "Камера")
            dev.mark        = (
                dd_mark.value or "none")
            if is_new:
                proj().devices.append(dev)
            refresh_devices()
            dlg.open = False
            page.update()

        def on_cancel(e):
            dlg.open = False
            page.update()

        dlg = ft.AlertDialog(
            modal=True,
            title=ft.Text(
                "Добавить устройство"
                if is_new
                else "Редактировать",
                size=16),
            content=ft.Column([
                fld_ip,
                fld_comment,
                dd_type,
                dd_mark,
                lbl_err,
            ], spacing=10, tight=True,
               height=360,
               scroll=ft.ScrollMode.AUTO),
            actions=[
                ft.TextButton(
                    "Отмена",
                    on_click=on_cancel),
                ft.ElevatedButton(
                    "Сохранить",
                    on_click=on_save,
                    bgcolor=ft.colors.BLUE_800,
                    color=ft.colors.WHITE),
            ],
            bgcolor="#1a1a2e",
        )
        page.dialog = dlg
        dlg.open    = True
        page.update()

    # ── диалог удалить ────────────────────────
    def confirm_delete(idx: int):
        dev = proj().devices[idx]

        def _del(e):
            proj().devices.pop(idx)
            refresh_devices()
            dlg.open = False
            page.update()

        def _cancel(e):
            dlg.open = False
            page.update()

        dlg = ft.AlertDialog(
            modal=True,
            title=ft.Text("Удалить?"),
            content=ft.Text(
                f"{dev.ip}\n{dev.comment}",
                color=ft.colors.GREY_400),
            actions=[
                ft.TextButton(
                    "Отмена",
                    on_click=_cancel),
                ft.ElevatedButton(
                    "Удалить",
                    on_click=_del,
                    bgcolor=ft.colors.RED_800,
                    color=ft.colors.WHITE),
            ],
            bgcolor="#1a1a2e",
        )
        page.dialog = dlg
        dlg.open    = True
        page.update()

    # ── вкладка ПРОЕКТЫ ───────────────────────
    projects_list = ft.ListView(
        expand=True, spacing=2,
        padding=ft.padding.all(6),
    )

    def refresh_projects():
        projects_list.controls.clear()
        files = list_project_files()
        lbl_status.value = (
            f"Папка: {get_projects_dir()}"
            f" | Файлов: {len(files)}")

        if not files:
            projects_list.controls.append(
                ft.Container(
                    content=ft.Column([
                        ft.Icon(
                            ft.icons.FOLDER_OPEN,
                            size=48,
                            color=ft.colors.GREY_800,
                        ),
                        ft.Text(
                            "Нет сохранённых проектов",
                            color=ft.colors.GREY_700,
                            size=14,
                            text_align=
                            ft.TextAlign.CENTER,
                        ),
                        ft.Text(
                            get_projects_dir(),
                            color=ft.colors.GREY_800,
                            size=10,
                            text_align=
                            ft.TextAlign.CENTER,
                        ),
                    ], horizontal_alignment=
                    ft.CrossAxisAlignment.CENTER,
                    spacing=10),
                    alignment=ft.alignment.center,
                    padding=40,
                )
            )
        else:
            for fp in files:
                fname = os.path.basename(fp)
                try:
                    with open(fp, "r",
                              encoding="utf-8") as fh:
                        meta = json.load(fh)
                    pname = meta.get("name", fname)
                    ndev  = len(
                        meta.get("devices", []))
                    pdesc = meta.get(
                        "description", "")
                except Exception as ex:
                    pname = fname
                    ndev  = 0
                    pdesc = f"Ошибка: {ex}"

                def _load(path=fp):
                    def _do(e):
                        try:
                            p = load_project_file(
                                path)
                            state["project"] = p
                            lbl_proj.value   = p.name
                            state["sort_col"] = (
                                SORT_NONE)
                            state["sort_asc"] = True
                            _stop_timer()
                            dd_timer.value   = None
                            rebuild_col_header()
                            refresh_devices()
                            _switch_tab(0)
                            lbl_status.value = (
                                f"✓ Открыт:"
                                f" {p.name}")
                            page.update()
                        except Exception as ex:
                            lbl_status.value = (
                                f"⚠ Ошибка: {ex}")
                            page.update()
                    return _do

                def _del(path=fp):
                    def _do(e):
                        try:
                            os.remove(path)
                        except Exception:
                            pass
                        refresh_projects()
                    return _do

                tile = ft.Container(
                    content=ft.Row([
                        ft.Icon(
                            ft.icons.FOLDER,
                            color=ft.colors.AMBER_400,
                            size=28,
                        ),
                        ft.Column([
                            ft.Text(
                                pname,
                                size=14,
                                color=ft.colors.WHITE,
                                weight=
                                ft.FontWeight.W_500,
                            ),
                            ft.Text(
                                (f"{pdesc}  •  "
                                 if pdesc else "") +
                                f"{ndev} устройств",
                                size=11,
                                color=
                                ft.colors.GREY_500,
                            ),
                        ], spacing=2, expand=True),
                        ft.IconButton(
                            icon=ft.icons.OPEN_IN_NEW,
                            icon_color=
                            ft.colors.BLUE_300,
                            tooltip="Открыть",
                            on_click=_load(),
                        ),
                        ft.IconButton(
                            icon=ft.icons.DELETE_OUTLINE,
                            icon_color=
                            ft.colors.RED_300,
                            tooltip="Удалить",
                            on_click=_del(),
                        ),
                    ], spacing=8,
                       vertical_alignment=
                       ft.CrossAxisAlignment.CENTER),
                    bgcolor="#1a1a26",
                    padding=ft.padding.symmetric(
                        horizontal=10, vertical=8),
                    border_radius=8,
                    border=ft.border.all(
                        1, "#2a2a3a"),
                    on_click=_load(),
                )
                projects_list.controls.append(tile)
        page.update()

    # ── диалог сохранить ──────────────────────
    def open_save_dlg():
        default = (
            safe_filename(proj().name) + ".json")
        fld_name = ft.TextField(
            label="Название проекта",
            value=proj().name,
            bgcolor=ft.colors.GREY_900,
            border_color=ft.colors.BLUE_400,
            autofocus=True,
        )
        fld_file = ft.TextField(
            label="Имя файла (.json)",
            value=default,
            bgcolor=ft.colors.GREY_900,
            border_color=ft.colors.BLUE_400,
        )
        lbl_err = ft.Text(
            "", color=ft.colors.RED_400,
            size=12)

        def on_save(e):
            name  = fld_name.value.strip()
            fname = fld_file.value.strip()
            if not fname:
                lbl_err.value = (
                    "Введите имя файла")
                page.update()
                return
            if not fname.endswith(".json"):
                fname += ".json"
            proj().name    = name or proj().name
            lbl_proj.value = proj().name
            fp = os.path.join(
                get_projects_dir(), fname)
            try:
                save_project(proj(), fp)
                lbl_status.value = (
                    f"✓ Сохранено: {fname}")
                dlg.open = False
                page.update()
            except Exception as ex:
                lbl_err.value = f"Ошибка: {ex}"
                page.update()

        def on_cancel(e):
            dlg.open = False
            page.update()

        dlg = ft.AlertDialog(
            modal=True,
            title=ft.Text(
                "Сохранить проект", size=16),
            content=ft.Column([
                fld_name, fld_file, lbl_err,
            ], spacing=10, tight=True,
               height=200),
            actions=[
                ft.TextButton(
                    "Отмена",
                    on_click=on_cancel),
                ft.ElevatedButton(
                    "Сохранить",
                    on_click=on_save,
                    bgcolor=ft.colors.GREEN_800,
                    color=ft.colors.WHITE),
            ],
            bgcolor="#1a1a2e",
        )
        page.dialog = dlg
        dlg.open    = True
        page.update()

    # ── диалог новый проект ───────────────────
    def open_new_dlg():
        fld_name = ft.TextField(
            label="Название проекта",
            bgcolor=ft.colors.GREY_900,
            border_color=ft.colors.BLUE_400,
            autofocus=True,
        )

        def on_create(e):
            name = fld_name.value.strip()
            if not name:
                name = "Новый проект"
            state["project"] = Project(name=name)
            lbl_proj.value   = name
            state["sort_col"] = SORT_NONE
            state["sort_asc"] = True
            _stop_timer()
            dd_timer.value   = None
            rebuild_col_header()
            refresh_devices()
            _switch_tab(0)
            dlg.open = False
            page.update()

        def on_cancel(e):
            dlg.open = False
            page.update()

        dlg = ft.AlertDialog(
            modal=True,
            title=ft.Text(
                "Новый проект", size=16),
            content=ft.Column([
                fld_name,
            ], spacing=10, tight=True,
               height=80),
            actions=[
                ft.TextButton(
                    "Отмена",
                    on_click=on_cancel),
                ft.ElevatedButton(
                    "Создать",
                    on_click=on_create,
                    bgcolor=ft.colors.BLUE_800,
                    color=ft.colors.WHITE),
            ],
            bgcolor="#1a1a2e",
        )
        page.dialog = dlg
        dlg.open    = True
        page.update()

    # ── вкладка проектов UI ───────────────────
    projects_tab = ft.Column([
        ft.Container(
            content=ft.Row([
                ft.Text(
                    "Проекты",
                    size=17,
                    weight=ft.FontWeight.BOLD,
                    color=ft.colors.WHITE,
                    expand=True,
                ),
                ft.IconButton(
                    icon=ft.icons.CREATE_NEW_FOLDER,
                    icon_color=ft.colors.GREEN_400,
                    tooltip="Новый проект",
                    on_click=lambda e:
                        open_new_dlg(),
                ),
                ft.IconButton(
                    icon=ft.icons.SAVE,
                    icon_color=ft.colors.AMBER_400,
                    tooltip="Сохранить текущий",
                    on_click=lambda e:
                        open_save_dlg(),
                ),
                ft.IconButton(
                    icon=ft.icons.REFRESH,
                    icon_color=ft.colors.BLUE_400,
                    tooltip="Обновить",
                    on_click=lambda e:
                        refresh_projects(),
                ),
            ]),
            padding=ft.padding.symmetric(
                horizontal=12, vertical=8),
            bgcolor=HEADER_BG,
            border=ft.border.only(
                bottom=ft.BorderSide(
                    1, "#2a2a3a")),
        ),
        projects_list,
    ], spacing=0, expand=True)

    # ── навигация ─────────────────────────────
    content_area = ft.Container(expand=True)

    devices_view = ft.Column([
        toolbar,
        col_header_container,
        device_list,
    ], spacing=0, expand=True)

    def _switch_tab(idx: int):
        nav.selected_index = idx
        if idx == 0:
            content_area.content = devices_view
        elif idx == 1:
            refresh_projects()
            content_area.content = projects_tab
        try:
            page.update()
        except Exception:
            pass

    nav = ft.NavigationBar(
        selected_index=0,
        bgcolor=HEADER_BG,
        indicator_color=ft.colors.BLUE_900,
        destinations=[
            ft.NavigationBarDestination(
                icon=ft.icons.DEVICES_OUTLINED,
                selected_icon=ft.icons.DEVICES,
                label="Устройства",
            ),
            ft.NavigationBarDestination(
                icon=ft.icons.FOLDER_OUTLINED,
                selected_icon=ft.icons.FOLDER,
                label="Проекты",
            ),
        ],
        on_change=lambda e: _switch_tab(
            e.control.selected_index),
    )

    # ── шапка ─────────────────────────────────
    header = ft.Container(
        content=ft.Column([
            ft.Row([
                ft.Icon(
                    ft.icons.WIFI_TETHERING,
                    color=ft.colors.BLUE_300,
                    size=22,
                ),
                ft.Text(
                    "Ping Camera",
                    size=18,
                    weight=ft.FontWeight.BOLD,
                    color=ft.colors.CYAN_200,
                ),
                ft.Container(expand=True),
                lbl_proj,
            ], spacing=8),
            ft.Row([
                lbl_online,
                lbl_offline,
                lbl_total,
                ft.Container(expand=True),
            ], spacing=12),
        ], spacing=4),
        bgcolor=HEADER_BG,
        padding=ft.padding.symmetric(
            horizontal=12, vertical=8),
        border=ft.border.only(
            bottom=ft.BorderSide(1, "#2a2a3a")),
    )

    status_bar = ft.Container(
        content=lbl_status,
        bgcolor="#0a0a12",
        padding=ft.padding.symmetric(
            horizontal=12, vertical=4),
        border=ft.border.only(
            top=ft.BorderSide(1, "#2a2a3a")),
    )

    # ── сборка страницы ───────────────────────
    page.add(ft.Column([
        header,
        content_area,
        status_bar,
        nav,
    ], spacing=0, expand=True))

    _switch_tab(0)
    refresh_devices()


if __name__ == "__main__":
    ft.app(target=main, assets_dir=ASSETS_DIR)
