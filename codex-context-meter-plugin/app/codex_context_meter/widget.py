from __future__ import annotations

import ctypes
import json
import os
import queue
import subprocess
import sys
import tempfile
import threading
import time
import traceback
from ctypes import wintypes
from pathlib import Path

import tkinter as tk
from tkinter import messagebox, simpledialog
from PIL import Image, ImageDraw, ImageFont, ImageTk

from .active_thread import ActiveThreadMonitor
from .handoff import create_handoff
from .session_stats import SessionSnapshot, SessionStatsTracker
from .team_dashboard import DEFAULT_DASHBOARD_URL, discover_team_root, ensure_dashboard


APP_NAME = "Codex Context Meter"
WIDTH = 238
HEIGHT = 82
SCALE = 2
TRANSPARENT = "#010203"
BG = "#151A22"
BORDER = "#344054"
TEXT = "#F4F7FB"
MUTED = "#9AA7B8"
TRACK = "#2A3442"
GREEN = "#43D39E"
AMBER = "#F5B942"
RED = "#FF6470"
CYAN = "#67D6FF"

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)


class RECT(ctypes.Structure):
    _fields_ = [
        ("left", wintypes.LONG),
        ("top", wintypes.LONG),
        ("right", wintypes.LONG),
        ("bottom", wintypes.LONG),
    ]


class POINT(ctypes.Structure):
    _fields_ = [
        ("x", wintypes.LONG),
        ("y", wintypes.LONG),
    ]


class MARGINS(ctypes.Structure):
    _fields_ = [
        ("cxLeftWidth", ctypes.c_int),
        ("cxRightWidth", ctypes.c_int),
        ("cyTopHeight", ctypes.c_int),
        ("cyBottomHeight", ctypes.c_int),
    ]


dwmapi = ctypes.WinDLL("dwmapi", use_last_error=True)
dwmapi.DwmSetWindowAttribute.argtypes = [
    wintypes.HWND,
    wintypes.DWORD,
    ctypes.c_void_p,
    wintypes.DWORD,
]
dwmapi.DwmSetWindowAttribute.restype = ctypes.c_long
dwmapi.DwmExtendFrameIntoClientArea.argtypes = [
    wintypes.HWND,
    ctypes.POINTER(MARGINS),
]
dwmapi.DwmExtendFrameIntoClientArea.restype = ctypes.c_long


user32.GetForegroundWindow.restype = wintypes.HWND
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user32.GetWindowThreadProcessId.restype = wintypes.DWORD
user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
user32.GetWindowTextLengthW.restype = ctypes.c_int
user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetWindowTextW.restype = ctypes.c_int
user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(RECT)]
user32.GetWindowRect.restype = wintypes.BOOL
user32.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(RECT)]
user32.GetClientRect.restype = wintypes.BOOL
user32.ClientToScreen.argtypes = [wintypes.HWND, ctypes.POINTER(POINT)]
user32.ClientToScreen.restype = wintypes.BOOL
user32.IsWindowVisible.argtypes = [wintypes.HWND]
user32.IsWindowVisible.restype = wintypes.BOOL
user32.IsIconic.argtypes = [wintypes.HWND]
user32.IsIconic.restype = wintypes.BOOL
user32.IsWindow.argtypes = [wintypes.HWND]
user32.IsWindow.restype = wintypes.BOOL
user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
user32.GetWindowLongW.restype = ctypes.c_long
user32.SetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_long]
user32.SetWindowLongW.restype = ctypes.c_long
user32.GetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
user32.SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
user32.SetWindowLongPtrW.restype = ctypes.c_ssize_t
user32.GetWindow.argtypes = [wintypes.HWND, wintypes.UINT]
user32.GetWindow.restype = wintypes.HWND
user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
user32.GetAncestor.restype = wintypes.HWND
user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
user32.GetAsyncKeyState.restype = ctypes.c_short
user32.SetWindowPos.argtypes = [
    wintypes.HWND,
    wintypes.HWND,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    wintypes.UINT,
]
user32.SetWindowPos.restype = wintypes.BOOL
user32.GetSystemMetrics.argtypes = [ctypes.c_int]
user32.GetSystemMetrics.restype = ctypes.c_int
user32.SetWindowRgn.argtypes = [wintypes.HWND, wintypes.HANDLE, wintypes.BOOL]
user32.SetWindowRgn.restype = ctypes.c_int
kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
kernel32.OpenProcess.restype = wintypes.HANDLE
kernel32.QueryFullProcessImageNameW.argtypes = [
    wintypes.HANDLE,
    wintypes.DWORD,
    wintypes.LPWSTR,
    ctypes.POINTER(wintypes.DWORD),
]
kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
kernel32.CloseHandle.restype = wintypes.BOOL
kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
kernel32.CreateMutexW.restype = wintypes.HANDLE
gdi32.CreateRoundRectRgn.argtypes = [
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
]
gdi32.CreateRoundRectRgn.restype = wintypes.HANDLE
gdi32.DeleteObject.argtypes = [wintypes.HANDLE]
gdi32.DeleteObject.restype = wintypes.BOOL


GWL_EXSTYLE = -20
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_TOPMOST = 0x00000008
WS_EX_NOACTIVATE = 0x08000000
GWLP_HWNDPARENT = -8
GW_OWNER = 4
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOZORDER = 0x0004
SWP_NOACTIVATE = 0x0010
HWND_NOTOPMOST = -2
GA_ROOT = 2
VK_LBUTTON = 0x01
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
SM_XVIRTUALSCREEN = 76
SM_YVIRTUALSCREEN = 77
SM_CXVIRTUALSCREEN = 78
SM_CYVIRTUALSCREEN = 79
FOLLOW_WATCHDOG_MS = 250
DRAG_INTERVAL_MS = 16
HOST_PROBE_INTERVAL = 0.5
STATS_INTERVAL_SECONDS = 0.18
UI_UPDATE_MS = 100
STATE_WRITE_INTERVAL_SECONDS = 2.0
CARD_RADIUS = 14
PAYLOAD_WARNING_BYTES = 32 * 1024 * 1024
PAYLOAD_CRITICAL_BYTES = 42 * 1024 * 1024


def _app_dir() -> Path:
    root = os.environ.get("LOCALAPPDATA") or str(Path.home())
    path = Path(root) / "CodexContextMeter"
    path.mkdir(parents=True, exist_ok=True)
    return path


APP_DIR = _app_dir()
CONFIG_PATH = APP_DIR / "config.json"
LOG_PATH = APP_DIR / "context-meter.log"
STATE_PATH = APP_DIR / "widget-state.json"


def log(message: str) -> None:
    try:
        with LOG_PATH.open("a", encoding="utf-8") as stream:
            stream.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {message}\n")
    except OSError:
        pass


def load_config() -> dict:
    defaults = {
        "right_offset": 22,
        "top_offset": 82,
        "manual_thread_id": None,
        "manual_pin": False,
        "alpha": 0.94,
        "payload_warning_bytes": PAYLOAD_WARNING_BYTES,
        "payload_critical_bytes": PAYLOAD_CRITICAL_BYTES,
        "team_dashboard_url": DEFAULT_DASHBOARD_URL,
        "team_board_root": str(discover_team_root() or ""),
    }
    try:
        data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            defaults.update(data)
    except (OSError, ValueError):
        pass
    return defaults


def save_config(config: dict) -> None:
    try:
        temporary = CONFIG_PATH.with_suffix(".tmp")
        temporary.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(CONFIG_PATH)
    except OSError as error:
        log(f"save config failed: {error}")


def write_state(
    snapshot: SessionSnapshot | None,
    thread_id: str | None,
    active_title: str = "",
    active_error: str = "",
    follow_state: dict | None = None,
    payload_warning_bytes: int = PAYLOAD_WARNING_BYTES,
    payload_critical_bytes: int = PAYLOAD_CRITICAL_BYTES,
) -> None:
    payload = {
        "ok": True,
        "pid": os.getpid(),
        "pythonExecutable": sys.executable,
        "workspacePath": snapshot.workspace_path if snapshot else "",
        "threadId": thread_id,
        "activeThreadTitle": active_title,
        "activeThreadError": active_error,
        "title": snapshot.title if snapshot else "",
        "estimatedTokens": snapshot.projected_tokens if snapshot else None,
        "contextWindow": snapshot.context_window if snapshot else None,
        "remainingTokens": snapshot.remaining_tokens if snapshot else None,
        "percent": round(snapshot.percent, 2) if snapshot else None,
        "cacheHitRate": round(snapshot.cache_hit_rate * 100, 4) if snapshot and snapshot.cache_hit_rate is not None else None,
        "exact": snapshot.exact if snapshot else None,
        "stale": snapshot.stale if snapshot else None,
        "requestPayloadBytes": snapshot.request_payload_bytes if snapshot else None,
        "imageCount": snapshot.image_count if snapshot else None,
        "imagePayloadBytes": snapshot.image_payload_bytes if snapshot else None,
        "maxSuccessfulRequestBytes": snapshot.max_success_payload_bytes if snapshot else None,
        "minFailedRequestBytes": snapshot.min_failure_payload_bytes if snapshot else None,
        "requestTooLarge": snapshot.request_too_large if snapshot else None,
        "requestError": snapshot.request_error if snapshot else "",
        "payloadWarningBytes": payload_warning_bytes,
        "payloadCriticalBytes": payload_critical_bytes,
        "payloadRisk": payload_risk(
            snapshot,
            payload_warning_bytes,
            payload_critical_bytes,
        ),
        "warningRisk": warning_state(
            snapshot,
            payload_warning_bytes,
            payload_critical_bytes,
        )[0],
        "warningText": warning_state(
            snapshot,
            payload_warning_bytes,
            payload_critical_bytes,
        )[1],
        "compactionCount": snapshot.compaction_count if snapshot else 0,
        "lastCompactionBeforeTokens": (
            snapshot.last_compaction_before_tokens if snapshot else 0
        ),
        "lastCompactionAfterTokens": (
            snapshot.last_compaction_after_tokens if snapshot else 0
        ),
        "observedCompactRatio": (
            snapshot.observed_compact_ratio if snapshot else None
        ),
        "lastCompactionAt": snapshot.last_compaction_at if snapshot else None,
        "nativeFollow": follow_state,
        "updatedAt": time.time(),
    }
    try:
        descriptor, temporary_name = tempfile.mkstemp(prefix="widget-state-", suffix=".tmp", dir=APP_DIR)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2)
        os.replace(temporary_name, STATE_PATH)
    except OSError as error:
        log(f"write state failed: {error}")


def payload_risk(
    snapshot: SessionSnapshot | None,
    warning_bytes: int = PAYLOAD_WARNING_BYTES,
    critical_bytes: int = PAYLOAD_CRITICAL_BYTES,
) -> str:
    if snapshot is None:
        return "unknown"
    current = snapshot.request_payload_bytes
    if snapshot.request_too_large or (
        snapshot.min_failure_payload_bytes > 0
        and current >= snapshot.min_failure_payload_bytes
    ):
        return "critical"
    if snapshot.min_failure_payload_bytes > 0:
        margin = max(
            2 * 1024 * 1024,
            snapshot.min_failure_payload_bytes // 20,
        )
        if current >= snapshot.min_failure_payload_bytes - margin:
            return "critical"
        if current >= snapshot.min_failure_payload_bytes * 0.8:
            return "warning"
        return "normal"
    if snapshot.max_success_payload_bytes > 0:
        if current >= snapshot.max_success_payload_bytes * 1.15:
            return "critical"
        if current >= snapshot.max_success_payload_bytes * 0.9:
            return "warning"
        return "normal"
    if current >= critical_bytes:
        return "critical"
    if current >= warning_bytes:
        return "warning"
    return "normal"


def context_risk(snapshot: SessionSnapshot | None) -> tuple[str, str]:
    if snapshot is None or snapshot.context_window <= 0:
        return "normal", ""
    ratio = snapshot.percent / 100.0
    observed = snapshot.observed_compact_ratio
    trigger = observed if observed and 0.5 <= observed <= 1.0 else 0.90
    if ratio >= max(0.70, trigger - 0.015):
        return "critical", "即将压缩"
    if ratio >= max(0.65, trigger - 0.04):
        return "warning", "接近压缩"
    if (
        snapshot.compaction_count >= 2
        and snapshot.previous_compaction_after_tokens > 0
        and snapshot.last_compaction_after_tokens
        > snapshot.previous_compaction_after_tokens * 1.25
        and ratio >= 0.55
    ):
        return "warning", "压缩后基线升高"
    return "normal", ""


def warning_state(
    snapshot: SessionSnapshot | None,
    payload_warning_bytes: int = PAYLOAD_WARNING_BYTES,
    payload_critical_bytes: int = PAYLOAD_CRITICAL_BYTES,
) -> tuple[str, str]:
    if snapshot is None:
        return "normal", ""
    request_risk = payload_risk(snapshot, payload_warning_bytes, payload_critical_bytes)
    token_risk, token_text = context_risk(snapshot)
    if snapshot.request_too_large:
        return "critical", f"413 已超限 · {compact_payload_bytes(snapshot.request_payload_bytes)}"
    if request_risk == "critical":
        if snapshot.min_failure_payload_bytes:
            return (
                "critical",
                f"请求体 {compact_payload_bytes(snapshot.request_payload_bytes)}"
                f"/{compact_payload_bytes(snapshot.min_failure_payload_bytes)} · 高风险",
            )
        if snapshot.max_success_payload_bytes:
            return (
                "critical",
                f"请求体 {compact_payload_bytes(snapshot.request_payload_bytes)} · 高于已验证值",
            )
        return (
            "critical",
            f"请求体 {compact_payload_bytes(snapshot.request_payload_bytes)} · 超保守预算",
        )
    if token_risk == "critical":
        return token_risk, token_text
    if request_risk == "warning":
        if snapshot.min_failure_payload_bytes:
            return (
                "warning",
                f"请求体 {compact_payload_bytes(snapshot.request_payload_bytes)}"
                f"/{compact_payload_bytes(snapshot.min_failure_payload_bytes)} · 提前预警",
            )
        if snapshot.max_success_payload_bytes:
            return (
                "warning",
                f"请求体 {compact_payload_bytes(snapshot.request_payload_bytes)} · 接近已验证值",
            )
        return (
            "warning",
            f"请求体 {compact_payload_bytes(snapshot.request_payload_bytes)} · 接近保守预算",
        )
    if token_risk == "warning":
        return token_risk, token_text
    if snapshot.last_compaction_at and time.time() - snapshot.last_compaction_at <= 30:
        after = (
            compact_tokens(snapshot.last_compaction_after_tokens)
            if snapshot.last_compaction_after_tokens
            else "校准中"
        )
        return (
            "info",
            f"已压缩 {compact_tokens(snapshot.last_compaction_before_tokens)}"
            f"→{after} · 第{snapshot.compaction_count}次",
        )
    if snapshot.image_payload_bytes >= 8_000_000:
        return (
            "warning",
            f"图片 {snapshot.image_count} 张 · "
            f"{compact_payload_bytes(snapshot.image_payload_bytes)}",
        )
    if snapshot.stale:
        return "warning", "等待下一次 usage 校准"
    return "normal", ""


def process_name(pid: int) -> str:
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return ""
    try:
        size = wintypes.DWORD(32768)
        buffer = ctypes.create_unicode_buffer(size.value)
        if not kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
            return ""
        return Path(buffer.value).name
    finally:
        kernel32.CloseHandle(handle)


def window_text(hwnd: int) -> str:
    length = user32.GetWindowTextLengthW(hwnd)
    if length <= 0:
        return ""
    buffer = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, buffer, length + 1)
    return buffer.value


def window_rect(hwnd: int) -> tuple[int, int, int, int] | None:
    rect = RECT()
    if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
        return None
    width = rect.right - rect.left
    height = rect.bottom - rect.top
    if width < 500 or height < 300:
        return None
    return rect.left, rect.top, rect.right, rect.bottom


def window_position(hwnd: int) -> tuple[int, int] | None:
    rect = RECT()
    if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
        return None
    return rect.left, rect.top


def client_rect_on_screen(hwnd: int) -> tuple[int, int, int, int] | None:
    client = RECT()
    if not user32.GetClientRect(hwnd, ctypes.byref(client)):
        return None
    top_left = POINT(client.left, client.top)
    bottom_right = POINT(client.right, client.bottom)
    if not user32.ClientToScreen(hwnd, ctypes.byref(top_left)):
        return None
    if not user32.ClientToScreen(hwnd, ctypes.byref(bottom_right)):
        return None
    width = bottom_right.x - top_left.x
    height = bottom_right.y - top_left.y
    if width < WIDTH or height < HEIGHT:
        return None
    return top_left.x, top_left.y, bottom_right.x, bottom_right.y


def virtual_screen_rect() -> tuple[int, int, int, int]:
    left = user32.GetSystemMetrics(SM_XVIRTUALSCREEN)
    top = user32.GetSystemMetrics(SM_YVIRTUALSCREEN)
    width = user32.GetSystemMetrics(SM_CXVIRTUALSCREEN)
    height = user32.GetSystemMetrics(SM_CYVIRTUALSCREEN)
    return left, top, left + width, top + height


def clamp_overlay_position(
    bounds: tuple[int, int, int, int],
    x: int,
    y: int,
    width: int = WIDTH,
    height: int = HEIGHT,
) -> tuple[int, int]:
    left, top, right, bottom = bounds
    max_x = max(left, right - width)
    max_y = max(top, bottom - height)
    return max(left, min(max_x, x)), max(top, min(max_y, y))


def overlay_bounds(hwnd: int) -> tuple[int, int, int, int] | None:
    client = client_rect_on_screen(hwnd)
    if not client:
        return None
    screen = virtual_screen_rect()
    left = max(client[0], screen[0])
    top = max(client[1], screen[1])
    right = min(client[2], screen[2])
    bottom = min(client[3], screen[3])
    if right - left < WIDTH or bottom - top < HEIGHT:
        return None
    return left, top, right, bottom


def desired_overlay_position(
    hwnd: int,
    right_offset: int,
    top_offset: int,
) -> tuple[int, int] | None:
    rect = window_rect(hwnd)
    bounds = overlay_bounds(hwnd)
    if not rect or not bounds:
        return None
    x = rect[2] - right_offset - WIDTH
    y = rect[1] + top_offset
    return clamp_overlay_position(bounds, x, y)


def is_codex_window(hwnd: int) -> bool:
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    if not pid.value:
        return False
    name = process_name(pid.value).lower()
    title = window_text(hwnd).lower()
    if name == "chatgpt.exe":
        return True
    return name == "codex.exe" or ("codex" in title and name in {"code.exe", "chatgpt.exe", "codex.exe"})


def find_codex_window() -> int | None:
    foreground = user32.GetForegroundWindow()
    if foreground and is_codex_window(foreground):
        return foreground
    matches: list[tuple[int, int]] = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def callback(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd) or user32.IsIconic(hwnd):
            return True
        if not is_codex_window(hwnd):
            return True
        rect = window_rect(hwnd)
        if rect:
            area = (rect[2] - rect[0]) * (rect[3] - rect[1])
            matches.append((area, hwnd))
        return True

    user32.EnumWindows(callback, 0)
    if not matches:
        return None
    return max(matches)[1]


def codex_is_foreground() -> bool:
    hwnd = user32.GetForegroundWindow()
    return bool(hwnd and is_codex_window(hwnd))


class NativeHostFollower:
    """Run the small-whale-style native follower in its own process."""

    def __init__(self, overlay_hwnd: int, host_hwnd: int) -> None:
        self.overlay_hwnd = int(overlay_hwnd)
        self.host_hwnd = int(host_hwnd)
        self.running = False
        self.error = ""
        self._process: subprocess.Popen | None = None
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(self.host_hwnd, ctypes.byref(pid))
        self.host_pid = int(pid.value)

    def start(self) -> bool:
        helper = Path(__file__).with_name("window_follower.py")
        if not helper.exists():
            self.error = f"Follower helper missing: {helper}"
            return False
        try:
            self._process = subprocess.Popen(
                [
                    sys.executable,
                    str(helper),
                    str(self.overlay_hwnd),
                    str(self.host_hwnd),
                ],
                cwd=str(helper.parent),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000),
            )
            time.sleep(0.05)
            self.running = self._process.poll() is None
            if not self.running:
                self.error = f"Follower exited with code {self._process.returncode}"
            return self.running
        except OSError as error:
            self.error = str(error)
            self.running = False
            return False

    def set_paused(self, paused: bool) -> None:
        return

    def wake(self) -> None:
        return

    def is_running(self) -> bool:
        if self._process is None:
            return False
        return_code = self._process.poll()
        if return_code is not None:
            self.running = False
            self.error = f"Follower exited with code {return_code}"
            return False
        self.running = True
        return True

    def stop(self) -> None:
        process = self._process
        self._process = None
        if process and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=1.0)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=1.0)
        self.running = False


_FONT_CACHE = {}


def font(size: int, bold: bool = False):
    cache_key = (size, bold)
    cached = _FONT_CACHE.get(cache_key)
    if cached is not None:
        return cached
    names = ["msyhbd.ttc", "msyh.ttc"] if bold else ["msyh.ttc", "msyhbd.ttc"]
    for name in names:
        path = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts" / name
        if path.exists():
            loaded = ImageFont.truetype(str(path), size * SCALE)
            _FONT_CACHE[cache_key] = loaded
            return loaded
    loaded = ImageFont.load_default()
    _FONT_CACHE[cache_key] = loaded
    return loaded


FONT_TITLE = font(10, True)
FONT_VALUE = font(14, True)
FONT_META = font(9)
FONT_RING = font(12, True)


def compact_tokens(value: int) -> str:
    value = max(0, int(value))
    if value < 10_000:
        return str(value)
    if value < 1_000_000:
        scaled = value / 1000
        return f"{scaled:.0f}K" if scaled >= 100 else f"{scaled:.1f}K"
    scaled = value / 1_000_000
    return f"{scaled:.1f}M"


def compact_payload_bytes(value: int) -> str:
    value = max(0, int(value))
    if value < 1_000_000:
        return f"{value / 1000:.0f}KB"
    return f"{value / 1_000_000:.1f}MB"


def cache_rate_text(snapshot: SessionSnapshot | None) -> str:
    if not snapshot or snapshot.cache_hit_rate is None:
        return "--"
    if snapshot.total_cached_input_tokens >= snapshot.total_input_tokens and snapshot.total_input_tokens > 0:
        return "100%"
    ratio = snapshot.cache_hit_rate * 100
    for digits in range(1, 5):
        rounded = round(ratio, digits)
        if rounded < 100:
            text = f"{rounded:.{digits}f}".rstrip("0").rstrip(".")
            return text + "%"
    return f"{ratio:.4f}".rstrip("0").rstrip(".") + "%"


def snapshot_signature(snapshot: SessionSnapshot | None) -> tuple | None:
    if snapshot is None:
        return None
    return (
        snapshot.thread_id,
        snapshot.title,
        snapshot.projected_tokens,
        snapshot.context_window,
        snapshot.remaining_tokens,
        snapshot.percent,
        snapshot.exact,
        snapshot.stale,
        snapshot.total_input_tokens,
        snapshot.total_cached_input_tokens,
        snapshot.request_payload_bytes,
        snapshot.image_count,
        snapshot.image_payload_bytes,
        snapshot.max_success_payload_bytes,
        snapshot.min_failure_payload_bytes,
        snapshot.request_too_large,
        snapshot.request_error,
        snapshot.compaction_count,
        snapshot.last_compaction_before_tokens,
        snapshot.last_compaction_after_tokens,
        snapshot.previous_compaction_after_tokens,
        snapshot.observed_compact_ratio,
        snapshot.last_compaction_at,
        bool(
            snapshot.last_compaction_at
            and time.time() - snapshot.last_compaction_at <= 30
        ),
    )


def trim_text(draw: ImageDraw.ImageDraw, text: str, max_width: int, text_font) -> str:
    if draw.textbbox((0, 0), text, font=text_font)[2] <= max_width:
        return text
    value = text
    while value and draw.textbbox((0, 0), value + "…", font=text_font)[2] > max_width:
        value = value[:-1]
    return value + "…"


def ring_color(percent: float) -> str:
    if percent >= 85:
        return RED
    if percent >= 60:
        return AMBER
    return CYAN


def enable_dpi_awareness() -> None:
    try:
        user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
        return
    except Exception:
        pass
    try:
        ctypes.WinDLL("shcore").SetProcessDpiAwareness(2)
    except Exception:
        pass


def render_meter_image(
    snapshot: SessionSnapshot | None,
    payload_warning_bytes: int = PAYLOAD_WARNING_BYTES,
    payload_critical_bytes: int = PAYLOAD_CRITICAL_BYTES,
) -> Image.Image:
    image = Image.new("RGB", (WIDTH * SCALE, HEIGHT * SCALE), BG)
    draw = ImageDraw.Draw(image)
    box = (1 * SCALE, 1 * SCALE, (WIDTH - 1) * SCALE, (HEIGHT - 1) * SCALE)
    draw.rounded_rectangle(
        box,
        radius=CARD_RADIUS * SCALE,
        fill=BG,
        outline=BORDER,
        width=SCALE,
    )

    percent = snapshot.percent if snapshot else 0.0
    color = ring_color(percent) if snapshot else MUTED
    warning_severity, warning_text = warning_state(
        snapshot,
        payload_warning_bytes,
        payload_critical_bytes,
    )
    center = (31 * SCALE, 41 * SCALE)
    radius = 21 * SCALE
    ring_box = (
        center[0] - radius,
        center[1] - radius,
        center[0] + radius,
        center[1] + radius,
    )
    draw.arc(ring_box, 0, 360, fill=TRACK, width=4 * SCALE)
    if snapshot:
        draw.arc(
            ring_box,
            -90,
            -90 + max(0.0, min(360.0, percent / 100 * 360)),
            fill=color,
            width=4 * SCALE,
        )
    ring_text = f"{round(percent):d}%" if snapshot else "--"
    draw.text(center, ring_text, fill=TEXT, font=FONT_RING, anchor="mm")

    right_x = 62 * SCALE
    max_title_width = (WIDTH - 76) * SCALE
    title = snapshot.title if snapshot else "等待 Codex 会话"
    title = trim_text(draw, title, max_title_width, FONT_TITLE)
    draw.ellipse(
        (right_x, 11 * SCALE, right_x + 5 * SCALE, 16 * SCALE),
        fill=(
            RED
            if warning_severity == "critical"
            else AMBER
            if warning_severity == "warning"
            else CYAN
            if warning_severity == "info"
            else color
        ),
    )
    draw.text(
        (right_x + 9 * SCALE, 8 * SCALE),
        title,
        fill=TEXT,
        font=FONT_TITLE,
    )

    if snapshot:
        prefix = "" if snapshot.exact else "~"
        value = f"{prefix}{compact_tokens(snapshot.projected_tokens)} / {compact_tokens(snapshot.context_window)}"
        draw.text((right_x, 25 * SCALE), value, fill=TEXT, font=FONT_VALUE)
        remaining = f"余 {compact_tokens(snapshot.remaining_tokens)}"
        rate = cache_rate_text(snapshot)
        draw.text((right_x, 48 * SCALE), remaining, fill=MUTED, font=FONT_META)
        draw.text((right_x + 67 * SCALE, 48 * SCALE), "·", fill=TRACK, font=FONT_META)
        draw.text((right_x + 77 * SCALE, 48 * SCALE), f"缓存 {rate}", fill=GREEN, font=FONT_META)
        if warning_text:
            warning_color = (
                RED
                if warning_severity == "critical"
                else AMBER
                if warning_severity == "warning"
                else CYAN
            )
            draw.text(
                (right_x, 63 * SCALE),
                trim_text(draw, warning_text, max_title_width, FONT_META),
                fill=warning_color,
                font=FONT_META,
            )
    else:
        draw.text((right_x, 28 * SCALE), "暂无会话用量", fill=MUTED, font=FONT_VALUE)
        draw.text((right_x, 50 * SCALE), "发送一条消息后显示", fill=MUTED, font=FONT_META)

    return image.resize((WIDTH, HEIGHT), Image.Resampling.LANCZOS)


class ContextMeterWidget:
    def __init__(self) -> None:
        config = load_config()
        self.root = tk.Tk()
        self.root.title(APP_NAME)
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", False)
        self.root.attributes("-alpha", float(config.get("alpha", 0.94)))
        self.root.configure(bg=BG)
        self.canvas = tk.Canvas(
            self.root,
            width=WIDTH,
            height=HEIGHT,
            bg=BG,
            bd=0,
            highlightthickness=0,
        )
        self.canvas.pack()
        self.photo: ImageTk.PhotoImage | None = None
        self.config = config
        if (
            not self.config.get("manual_pin", False)
            and self.config.get("manual_thread_id") is not None
        ):
            self.config["manual_thread_id"] = None
            save_config(self.config)
        self.tracker = SessionStatsTracker()
        self.active_thread = ActiveThreadMonitor()
        self.active_thread.start()
        self.snapshot: SessionSnapshot | None = None
        self.snapshot_thread_id: str | None = None
        self.recent_threads: list[tuple[str, str, float]] = []
        self.stats_lock = threading.Lock()
        self.stats_stop = threading.Event()
        self.stats_wake = threading.Event()
        self.worker_recent_threads: list[tuple[str, str, float]] = []
        self.worker_snapshot: SessionSnapshot | None = None
        self.worker_thread_id: str | None = None
        self.window_handle: int | None = None
        self.attached_owner: int | None = None
        self.host_follower: NativeHostFollower | None = None
        self.visible = False
        self.tick = 0
        self.last_state_write = 0.0
        self.last_state_key: tuple | None = None
        self.last_rendered_signature: tuple | None | object = object()
        self.drag_start: tuple[int, int, int, int] | None = None
        self.drag_distance = 0.0
        self.dragging = False
        self.drag_after_id: str | None = None
        self.last_host_probe = 0.0
        self.dashboard_events: queue.Queue[tuple[str, str]] = queue.Queue()
        self.dashboard_opening = False
        self.dashboard_lock = threading.Lock()
        self.click_monitor_stop = threading.Event()
        self.canvas.bind("<ButtonPress-1>", self._begin_drag)
        self.canvas.bind("<B1-Motion>", self._drag)
        self.canvas.bind("<ButtonRelease-1>", self._end_drag)
        self.canvas.bind("<Button-3>", self._open_menu)
        self.root.after(120, self._set_no_activate)
        self.root.after(2000, self.active_thread.force_refresh)
        self.root.after(0, self._follow_host)
        self.root.after(0, self._update)
        threading.Thread(target=self._stats_loop, name="codex-context-stats", daemon=True).start()
        threading.Thread(target=self._click_monitor_loop, name="codex-context-clicks", daemon=True).start()
        log("widget started")

    def _set_no_activate(self) -> None:
        try:
            hwnd = self._overlay_hwnd()
            log(f"overlay hwnd={hwnd}")
            extended = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
            user32.SetWindowLongW(
                hwnd,
                GWL_EXSTYLE,
                (extended | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE) & ~WS_EX_TOPMOST,
            )
            region = gdi32.CreateRoundRectRgn(
                0,
                0,
                WIDTH + 1,
                HEIGHT + 1,
                CARD_RADIUS * 2,
                CARD_RADIUS * 2,
            )
            if region and not user32.SetWindowRgn(hwnd, region, True):
                gdi32.DeleteObject(region)
            try:
                corner = ctypes.c_int(1)
                dwmapi.DwmSetWindowAttribute(
                    hwnd,
                    ctypes.c_uint(33),
                    ctypes.byref(corner),
                    ctypes.sizeof(corner),
                )
                transitions_disabled = ctypes.c_int(1)
                dwmapi.DwmSetWindowAttribute(
                    hwnd,
                    ctypes.c_uint(3),
                    ctypes.byref(transitions_disabled),
                    ctypes.sizeof(transitions_disabled),
                )
                non_client_disabled = ctypes.c_int(1)
                dwmapi.DwmSetWindowAttribute(
                    hwnd,
                    ctypes.c_uint(2),
                    ctypes.byref(non_client_disabled),
                    ctypes.sizeof(non_client_disabled),
                )
                border_none = ctypes.c_uint(0xFFFFFFFE)
                dwmapi.DwmSetWindowAttribute(
                    hwnd,
                    ctypes.c_uint(34),
                    ctypes.byref(border_none),
                    ctypes.sizeof(border_none),
                )
                margins = MARGINS(0, 0, 0, 0)
                dwmapi.DwmExtendFrameIntoClientArea(
                    hwnd,
                    ctypes.byref(margins),
                )
            except OSError:
                pass
        except Exception:
            log(traceback.format_exc())

    def _overlay_hwnd(self) -> int:
        handle = self.root.winfo_id()
        root_handle = user32.GetAncestor(handle, GA_ROOT)
        return int(root_handle or handle)

    def _selected_thread(self) -> str | None:
        manual = self.config.get("manual_thread_id") if self.config.get("manual_pin") else None
        if manual:
            return str(manual)
        return (
            self.tracker.thread_id_for_title(self.active_thread.title)
            or self.tracker.latest_thread_id()
        )

    def _stats_loop(self) -> None:
        last_catalog_refresh = 0.0
        while not self.stats_stop.is_set():
            try:
                manual = (
                    self.config.get("manual_thread_id")
                    if self.config.get("manual_pin")
                    else None
                )
                recent = self.tracker.recent_threads()
                resolved = None
                if self.active_thread.title:
                    resolved = self.tracker.thread_id_for_title(
                        self.active_thread.title,
                        rows=recent,
                    )
                    now = time.monotonic()
                    if resolved is None and now - last_catalog_refresh >= 1.0:
                        recent = self.tracker.recent_threads(force=True)
                        resolved = self.tracker.thread_id_for_title(
                            self.active_thread.title,
                            rows=recent,
                        )
                        last_catalog_refresh = now
                thread_id = str(manual) if manual else resolved or (recent[0][0] if recent else None)
                snapshot = self.tracker.snapshot(thread_id)
                with self.stats_lock:
                    self.worker_thread_id = thread_id
                    self.worker_snapshot = snapshot
                    self.worker_recent_threads = recent
            except Exception:
                log(traceback.format_exc())
            self.stats_wake.wait(STATS_INTERVAL_SECONDS)
            self.stats_wake.clear()

    def _click_monitor_loop(self) -> None:
        was_down = False
        while not self.click_monitor_stop.wait(0.02):
            is_down = bool(user32.GetAsyncKeyState(VK_LBUTTON) & 0x8000)
            if was_down and not is_down and codex_is_foreground():
                self.active_thread.force_refresh()
                self.stats_wake.set()
            was_down = is_down

    def _move_overlay(self, x: int, y: int, force_repaint: bool = False) -> bool:
        hwnd = self._overlay_hwnd()
        if window_position(hwnd) == (x, y) and not force_repaint:
            return True
        if not user32.SetWindowPos(
            hwnd,
            0,
            x,
            y,
            0,
            0,
            SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE,
        ):
            return False
        return True

    def _begin_drag(self, event) -> None:
        if not self.window_handle:
            return
        if self.host_follower:
            self.host_follower.set_paused(True)
        self.drag_start = (
            event.x_root - self.root.winfo_rootx(),
            event.y_root - self.root.winfo_rooty(),
            event.x_root,
            event.y_root,
        )
        self.drag_distance = 0.0
        self.dragging = True
        if self.drag_after_id is not None:
            self.root.after_cancel(self.drag_after_id)
        self.drag_after_id = self.root.after(1, self._drag_tick)

    def _drag_tick(self) -> None:
        self.drag_after_id = None
        if not self.dragging or not self.drag_start or not self.window_handle:
            return
        if not (user32.GetAsyncKeyState(VK_LBUTTON) & 0x8000):
            self._end_drag(None)
            return
        pointer_x = self.root.winfo_pointerx()
        pointer_y = self.root.winfo_pointery()
        self.drag_distance = max(
            self.drag_distance,
            abs(pointer_x - self.drag_start[2])
            + abs(pointer_y - self.drag_start[3]),
        )
        rect = window_rect(self.window_handle)
        bounds = overlay_bounds(self.window_handle)
        if not rect or not bounds:
            self.drag_after_id = self.root.after(DRAG_INTERVAL_MS, self._drag_tick)
            return
        x, y = clamp_overlay_position(
            bounds,
            pointer_x - self.drag_start[0],
            pointer_y - self.drag_start[1],
        )
        right_offset = rect[2] - x - WIDTH
        top_offset = y - rect[1]
        self.config["right_offset"] = right_offset
        self.config["top_offset"] = top_offset
        self._move_overlay(x, y)
        self.drag_after_id = self.root.after(DRAG_INTERVAL_MS, self._drag_tick)

    def _drag(self, _event) -> None:
        # Native drag polling owns the motion path. Tk motion events can be
        # coalesced and lag behind the pointer, so they intentionally do nothing.
        return

    def _end_drag(self, _event) -> None:
        self.dragging = False
        if self.drag_after_id is not None:
            try:
                self.root.after_cancel(self.drag_after_id)
            except tk.TclError:
                pass
            self.drag_after_id = None
        if self.drag_start:
            if self.drag_distance >= 6:
                save_config(self.config)
            else:
                self._reset_auto()
        self.drag_start = None
        if self.host_follower:
            self.host_follower.set_paused(False)

    def _reset_auto(self) -> None:
        self.config["manual_thread_id"] = None
        self.config["manual_pin"] = False
        save_config(self.config)
        self.stats_wake.set()
        self.tick = 0
        self._update()

    def _set_manual(self, thread_id: str) -> None:
        self.config["manual_thread_id"] = thread_id
        self.config["manual_pin"] = True
        save_config(self.config)
        self.stats_wake.set()
        self.tick = 0
        self._update()

    def _open_menu(self, event) -> None:
        with self.stats_lock:
            recent = list(self.worker_recent_threads)
        menu = tk.Menu(self.root, tearoff=False)
        if self.snapshot:
            menu.add_command(
                label=(
                    f"上下文 {self.snapshot.projected_tokens:,} / {self.snapshot.context_window:,}"
                    f" · 剩余 {self.snapshot.remaining_tokens:,}"
                ),
                state="disabled",
            )
            menu.add_command(
                label=f"当前会话缓存命中 {cache_rate_text(self.snapshot)}",
                state="disabled",
            )
            if self.snapshot.image_count:
                request_text = (
                    f"图片 {self.snapshot.image_count} 张 · "
                    f"{compact_payload_bytes(self.snapshot.image_payload_bytes)}"
                )
                menu.add_command(label=request_text, state="disabled")
            if self.snapshot.request_too_large:
                menu.add_command(label="上次请求：413 请求体超限", state="disabled")
            if self.snapshot.max_success_payload_bytes:
                menu.add_command(
                    label=(
                        "本会话已验证请求体 "
                        f"{compact_payload_bytes(self.snapshot.max_success_payload_bytes)}"
                    ),
                    state="disabled",
                )
            if self.snapshot.min_failure_payload_bytes:
                menu.add_command(
                    label=(
                        "本会话失败请求体 "
                        f"{compact_payload_bytes(self.snapshot.min_failure_payload_bytes)}"
                    ),
                    state="disabled",
                )
            if self.snapshot.compaction_count:
                after = (
                    compact_tokens(self.snapshot.last_compaction_after_tokens)
                    if self.snapshot.last_compaction_after_tokens
                    else "校准中"
                )
                menu.add_command(
                    label=(
                        f"已压缩 {self.snapshot.compaction_count} 次 · "
                        f"上次 {compact_tokens(self.snapshot.last_compaction_before_tokens)}"
                        f"→{after}"
                    ),
                    state="disabled",
                )
            severity, warning_text = warning_state(
                self.snapshot,
                int(self.config.get("payload_warning_bytes", PAYLOAD_WARNING_BYTES)),
                int(self.config.get("payload_critical_bytes", PAYLOAD_CRITICAL_BYTES)),
            )
            if warning_text:
                menu.add_command(label=f"预警：{warning_text}", state="disabled")
            menu.add_command(label="生成交接报告…", command=self._write_handoff)
            menu.add_separator()
        menu.add_command(
            label="打开 Codex 团队看板",
            command=self._open_team_dashboard,
            state="disabled" if self.dashboard_opening else "normal",
        )
        menu.add_separator()
        menu.add_command(label="自动跟随当前选中会话", command=self._reset_auto)
        sessions = tk.Menu(menu, tearoff=False)
        current = self.snapshot.thread_id if self.snapshot else None
        for thread_id, title, _timestamp in recent[:10]:
            label = ("✓ " if thread_id == current else "") + title[:28]
            sessions.add_command(label=label, command=lambda value=thread_id: self._set_manual(value))
        menu.add_cascade(label="选择会话", menu=sessions)
        menu.add_separator()
        menu.add_command(label="退出", command=self._quit)
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def _open_team_dashboard(self) -> None:
        with self.dashboard_lock:
            if self.dashboard_opening:
                return
            self.dashboard_opening = True

        def worker() -> None:
            try:
                result = ensure_dashboard(
                    self.config,
                    log_dir=APP_DIR,
                    open_browser=True,
                    log_callback=log,
                )
                self.dashboard_events.put(
                    (
                        "ok",
                        f"team dashboard opened url={result['url']} started={result['started']}",
                    )
                )
            except Exception as error:
                log(f"team dashboard open failed: {error}\n{traceback.format_exc()}")
                self.dashboard_events.put(("error", str(error)))
            finally:
                with self.dashboard_lock:
                    self.dashboard_opening = False

        threading.Thread(
            target=worker,
            name="codex-team-dashboard",
            daemon=True,
        ).start()

    def _drain_dashboard_events(self) -> None:
        while True:
            try:
                kind, message = self.dashboard_events.get_nowait()
            except queue.Empty:
                return
            if kind == "error":
                messagebox.showerror("Codex 团队看板", message, parent=self.root)
            else:
                log(message)

    def _quit(self) -> None:
        self.click_monitor_stop.set()
        self.stats_stop.set()
        self.stats_wake.set()
        if self.host_follower:
            self.host_follower.stop()
            self.host_follower = None
        self.active_thread.close()
        save_config(self.config)
        self.root.destroy()

    def _write_handoff(self) -> None:
        warning_bytes = int(
            self.config.get("payload_warning_bytes", PAYLOAD_WARNING_BYTES)
        )
        critical_bytes = int(
            self.config.get("payload_critical_bytes", PAYLOAD_CRITICAL_BYTES)
        )
        title = (
            self.snapshot.title
            if self.snapshot
            else self.active_thread.title or "未识别会话"
        )
        chain_name = simpledialog.askstring(
            "生成交接报告",
            "交接链名称（同一任务多次转接请保持一致）：",
            initialvalue=title,
            parent=self.root,
        )
        if not chain_name:
            return
        snapshot = self.snapshot
        metrics = {
            "projectedTokens": snapshot.projected_tokens if snapshot else 0,
            "contextWindow": snapshot.context_window if snapshot else 0,
            "remainingTokens": snapshot.remaining_tokens if snapshot else 0,
            "percent": snapshot.percent if snapshot else 0,
            "cacheHitRate": (
                snapshot.cache_hit_rate * 100
                if snapshot and snapshot.cache_hit_rate is not None
                else 0
            ),
            "requestPayloadBytes": snapshot.request_payload_bytes if snapshot else 0,
            "maxSuccessfulRequestBytes": (
                snapshot.max_success_payload_bytes if snapshot else 0
            ),
            "minFailedRequestBytes": (
                snapshot.min_failure_payload_bytes if snapshot else 0
            ),
            "imageCount": snapshot.image_count if snapshot else 0,
            "imagePayloadBytes": snapshot.image_payload_bytes if snapshot else 0,
            "payloadRisk": payload_risk(snapshot, warning_bytes, critical_bytes),
            "requestTooLarge": snapshot.request_too_large if snapshot else False,
            "warningRisk": warning_state(snapshot, warning_bytes, critical_bytes)[0],
            "warningText": warning_state(snapshot, warning_bytes, critical_bytes)[1],
            "compactionCount": snapshot.compaction_count if snapshot else 0,
            "lastCompactionBeforeTokens": (
                snapshot.last_compaction_before_tokens if snapshot else 0
            ),
            "lastCompactionAfterTokens": (
                snapshot.last_compaction_after_tokens if snapshot else 0
            ),
            "observedCompactRatio": (
                snapshot.observed_compact_ratio if snapshot else None
            ),
        }
        reason = (
            "request-413"
            if snapshot and snapshot.request_too_large
            else "request-payload"
            if metrics["payloadRisk"] in {"warning", "critical"}
            else "manual"
        )
        try:
            result = create_handoff(
                directory=APP_DIR / "handoffs",
                chain_name=chain_name,
                thread_id=self.snapshot_thread_id,
                thread_title=title,
                summary=f"继续完成任务：{title}",
                status="paused",
                next_action="补充当前任务的下一步，然后在新会话完整读取交接链。",
                reason=reason,
                metrics=metrics,
                workspace_root=snapshot.workspace_path if snapshot else None,
            )
            log(
                "handoff written: "
                f"{result['path']} chain={result['chainId']} "
                f"count={result['handoffCount']}"
            )
            messagebox.showinfo(
                "交接报告",
                (
                    f"当前快照：\n{result['path']}\n\n"
                    f"完整历史链：\n{result['chainContextPath']}\n\n"
                    f"项目入口：\n{result['pointerPath']}\n\n"
                    f"交接链：{result['chainId']}"
                ),
            )
        except Exception as error:
            log(f"write handoff failed: {error}")
            messagebox.showerror("交接报告", f"保存失败：\n{error}")

    def _cycle_session(self) -> None:
        with self.stats_lock:
            recent = list(self.worker_recent_threads)
        if len(recent) < 2:
            return
        current = self.snapshot.thread_id if self.snapshot else None
        ids = [row[0] for row in recent]
        try:
            index = ids.index(current)
        except ValueError:
            index = -1
        self.config["manual_thread_id"] = ids[(index + 1) % len(ids)]
        save_config(self.config)
        self.stats_wake.set()
        self._update()

    def _render(self, snapshot: SessionSnapshot | None) -> None:
        image = render_meter_image(
            snapshot,
            int(self.config.get("payload_warning_bytes", PAYLOAD_WARNING_BYTES)),
            int(self.config.get("payload_critical_bytes", PAYLOAD_CRITICAL_BYTES)),
        )
        self.photo = ImageTk.PhotoImage(image)
        self.canvas.delete("all")
        self.canvas.create_image(0, 0, image=self.photo, anchor="nw")
        self.canvas.create_rectangle(0, 0, WIDTH, HEIGHT, outline="", fill="", tags=("hit",))

    def _stop_host_follower(self) -> None:
        if self.host_follower:
            self.host_follower.stop()
            self.host_follower = None

    def _ensure_host_follower(self, hwnd: int) -> None:
        if self.host_follower:
            if self.host_follower.host_hwnd != hwnd or not self.host_follower.is_running():
                if self.host_follower.error:
                    log(self.host_follower.error)
                self._stop_host_follower()
        if self.host_follower:
            return
        follower = NativeHostFollower(
            self._overlay_hwnd(),
            hwnd,
        )
        if follower.start():
            log(f"native follower started host={hwnd} pid={follower.host_pid}")
        else:
            log(f"native follower unavailable: {follower.error}")
        self.host_follower = follower

    def _position(self) -> bool:
        now = time.monotonic()
        hwnd = self.window_handle
        should_probe = (
            not hwnd
            or not user32.IsWindow(hwnd)
            or not user32.IsWindowVisible(hwnd)
            or user32.IsIconic(hwnd)
            or now - self.last_host_probe >= HOST_PROBE_INTERVAL
        )
        if should_probe:
            hwnd = find_codex_window()
            self.last_host_probe = now
        if not hwnd:
            self.window_handle = None
            self._stop_host_follower()
            if self.attached_owner is not None:
                try:
                    user32.SetWindowLongPtrW(self._overlay_hwnd(), GWLP_HWNDPARENT, 0)
                except Exception:
                    pass
                self.attached_owner = None
            return False
        self.window_handle = hwnd
        try:
            overlay_hwnd = self._overlay_hwnd()
            if self.attached_owner != hwnd or user32.GetWindow(overlay_hwnd, GW_OWNER) != hwnd:
                user32.SetWindowLongPtrW(overlay_hwnd, GWLP_HWNDPARENT, hwnd)
                user32.SetWindowPos(
                    overlay_hwnd,
                    wintypes.HWND(HWND_NOTOPMOST),
                    0,
                    0,
                    0,
                    0,
                    SWP_NOSIZE | SWP_NOMOVE | SWP_NOACTIVATE,
                )
                if user32.GetWindow(overlay_hwnd, GW_OWNER) == hwnd:
                    self.attached_owner = hwnd
                else:
                    self.attached_owner = None
        except Exception:
            self.attached_owner = None
        right_offset = int(self.config.get("right_offset", 22))
        top_offset = int(self.config.get("top_offset", 82))
        target = desired_overlay_position(hwnd, right_offset, top_offset)
        if not target:
            return False
        x, y = target
        if not self.visible:
            self.root.deiconify()
            self.root.update_idletasks()
            self._move_overlay(x, y, force_repaint=True)
            self.root.lift()
            self.visible = True
            self._ensure_host_follower(hwnd)
        else:
            self._ensure_host_follower(hwnd)
            if (
                not self.host_follower
                or not self.host_follower.is_running()
            ) and not self._move_overlay(x, y):
                return False
        return True

    def _follow_host(self) -> None:
        try:
            if not self.dragging:
                if not self._position() and self.visible:
                    self.root.withdraw()
                    self.visible = False
        except Exception:
            log(traceback.format_exc())
        self.root.after(FOLLOW_WATCHDOG_MS, self._follow_host)

    def _update(self) -> None:
        try:
            self._drain_dashboard_events()
            self.tick += 1
            if self.dragging:
                self.root.after(UI_UPDATE_MS, self._update)
                return
            with self.stats_lock:
                thread_id = self.worker_thread_id
                worker_snapshot = self.worker_snapshot
                self.recent_threads = list(self.worker_recent_threads)
            if worker_snapshot is not self.snapshot or thread_id != self.snapshot_thread_id:
                self.snapshot_thread_id = thread_id
                self.snapshot = worker_snapshot
            rendered_signature = snapshot_signature(self.snapshot)
            if rendered_signature != self.last_rendered_signature:
                self._render(self.snapshot)
                self.last_rendered_signature = rendered_signature
            state_key = (
                thread_id,
                rendered_signature,
                self.active_thread.title,
                self.active_thread.error,
            )
            if (
                state_key != self.last_state_key
                or time.time() - self.last_state_write >= STATE_WRITE_INTERVAL_SECONDS
            ):
                write_state(
                    self.snapshot,
                    thread_id,
                    self.active_thread.title,
                    self.active_thread.error,
                    (
                        {
                            "running": self.host_follower.is_running(),
                            "pid": (
                                self.host_follower._process.pid
                                if self.host_follower._process
                                else None
                            ),
                            "error": self.host_follower.error,
                        }
                        if self.host_follower
                        else None
                    ),
                    int(
                        self.config.get(
                            "payload_warning_bytes",
                            PAYLOAD_WARNING_BYTES,
                        )
                    ),
                    int(
                        self.config.get(
                            "payload_critical_bytes",
                            PAYLOAD_CRITICAL_BYTES,
                        )
                    ),
                )
                self.last_state_key = state_key
                self.last_state_write = time.time()
        except Exception:
            log(traceback.format_exc())
        self.root.after(UI_UPDATE_MS, self._update)

    def run(self) -> None:
        self.root.mainloop()


def main() -> None:
    mutex = kernel32.CreateMutexW(None, False, "Local\\CodexContextMeter.Singleton")
    if ctypes.get_last_error() == 183:
        if mutex:
            kernel32.CloseHandle(mutex)
        return
    try:
        enable_dpi_awareness()
        ContextMeterWidget().run()
    except Exception:
        log(traceback.format_exc())
        raise
    finally:
        if mutex:
            kernel32.CloseHandle(mutex)


if __name__ == "__main__":
    main()
