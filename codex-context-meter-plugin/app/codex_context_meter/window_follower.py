from __future__ import annotations

import ctypes
import sys
import time
from ctypes import wintypes


user32 = ctypes.WinDLL("user32", use_last_error=True)


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


user32.SetProcessDpiAwarenessContext.argtypes = [ctypes.c_void_p]
user32.SetProcessDpiAwarenessContext.restype = wintypes.BOOL
user32.IsWindow.argtypes = [wintypes.HWND]
user32.IsWindow.restype = wintypes.BOOL
user32.IsWindowVisible.argtypes = [wintypes.HWND]
user32.IsWindowVisible.restype = wintypes.BOOL
user32.IsIconic.argtypes = [wintypes.HWND]
user32.IsIconic.restype = wintypes.BOOL
user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(RECT)]
user32.GetWindowRect.restype = wintypes.BOOL
user32.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(RECT)]
user32.GetClientRect.restype = wintypes.BOOL
user32.ClientToScreen.argtypes = [wintypes.HWND, ctypes.POINTER(POINT)]
user32.ClientToScreen.restype = wintypes.BOOL
user32.GetSystemMetrics.argtypes = [ctypes.c_int]
user32.GetSystemMetrics.restype = ctypes.c_int
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


SWP_NOSIZE = 0x0001
SWP_NOZORDER = 0x0004
SWP_NOACTIVATE = 0x0010
SWP_ASYNCWINDOWPOS = 0x4000
SM_XVIRTUALSCREEN = 76
SM_YVIRTUALSCREEN = 77
SM_CXVIRTUALSCREEN = 78
SM_CYVIRTUALSCREEN = 79
FOLLOW_INTERVAL_SECONDS = 0.016


def window_rect(hwnd: int) -> tuple[int, int, int, int] | None:
    rect = RECT()
    if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
        return None
    return rect.left, rect.top, rect.right, rect.bottom


def overlay_bounds(host_hwnd: int) -> tuple[int, int, int, int] | None:
    client = RECT()
    if not user32.GetClientRect(host_hwnd, ctypes.byref(client)):
        return None
    top_left = POINT(client.left, client.top)
    bottom_right = POINT(client.right, client.bottom)
    if not user32.ClientToScreen(host_hwnd, ctypes.byref(top_left)):
        return None
    if not user32.ClientToScreen(host_hwnd, ctypes.byref(bottom_right)):
        return None
    left = max(
        top_left.x,
        user32.GetSystemMetrics(SM_XVIRTUALSCREEN),
    )
    top = max(
        top_left.y,
        user32.GetSystemMetrics(SM_YVIRTUALSCREEN),
    )
    right = min(
        bottom_right.x,
        user32.GetSystemMetrics(SM_XVIRTUALSCREEN)
        + user32.GetSystemMetrics(SM_CXVIRTUALSCREEN),
    )
    bottom = min(
        bottom_right.y,
        user32.GetSystemMetrics(SM_YVIRTUALSCREEN)
        + user32.GetSystemMetrics(SM_CYVIRTUALSCREEN),
    )
    if right <= left or bottom <= top:
        return None
    return left, top, right, bottom


def clamp_position(
    bounds: tuple[int, int, int, int],
    x: int,
    y: int,
    width: int,
    height: int,
) -> tuple[int, int]:
    left, top, right, bottom = bounds
    max_x = max(left, right - width)
    max_y = max(top, bottom - height)
    return max(left, min(max_x, x)), max(top, min(max_y, y))


def translated_position(
    previous_host: tuple[int, int, int, int],
    current_host: tuple[int, int, int, int],
    current_overlay: tuple[int, int, int, int],
    bounds: tuple[int, int, int, int],
) -> tuple[int, int]:
    width = current_overlay[2] - current_overlay[0]
    height = current_overlay[3] - current_overlay[1]
    x = current_overlay[0] + current_host[2] - previous_host[2]
    y = current_overlay[1] + current_host[1] - previous_host[1]
    return clamp_position(bounds, x, y, width, height)


def follow(overlay_hwnd: int, host_hwnd: int) -> None:
    previous_host = window_rect(host_hwnd)
    while (
        user32.IsWindow(overlay_hwnd)
        and user32.IsWindow(host_hwnd)
    ):
        current_host = window_rect(host_hwnd)
        if not current_host:
            break
        if user32.IsIconic(host_hwnd):
            time.sleep(FOLLOW_INTERVAL_SECONDS)
            continue
        if previous_host:
            host_changed = current_host != previous_host
            if host_changed:
                current_overlay = window_rect(overlay_hwnd)
                bounds = overlay_bounds(host_hwnd)
                if current_overlay and bounds:
                    target_x, target_y = translated_position(
                        previous_host,
                        current_host,
                        current_overlay,
                        bounds,
                    )
                    if (current_overlay[0], current_overlay[1]) != (target_x, target_y):
                        user32.SetWindowPos(
                            overlay_hwnd,
                            0,
                            target_x,
                            target_y,
                            0,
                            0,
                            SWP_NOSIZE
                            | SWP_NOZORDER
                            | SWP_NOACTIVATE
                            | SWP_ASYNCWINDOWPOS,
                        )
        previous_host = current_host
        time.sleep(FOLLOW_INTERVAL_SECONDS)


def main() -> None:
    if len(sys.argv) != 3:
        raise SystemExit(2)
    try:
        overlay_hwnd = int(sys.argv[1])
        host_hwnd = int(sys.argv[2])
    except ValueError:
        raise SystemExit(2) from None
    user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    if not user32.IsWindow(overlay_hwnd) or not user32.IsWindow(host_hwnd):
        raise SystemExit(3)
    follow(overlay_hwnd, host_hwnd)


if __name__ == "__main__":
    main()
