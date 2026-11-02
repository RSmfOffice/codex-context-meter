from __future__ import annotations

import ctypes
import json
import os
import subprocess
import threading
import time
from ctypes import wintypes
from pathlib import Path


CREATE_NO_WINDOW = 0x08000000
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
kernel32.CreateEventW.argtypes = [
    ctypes.c_void_p,
    wintypes.BOOL,
    wintypes.BOOL,
    wintypes.LPCWSTR,
]
kernel32.CreateEventW.restype = wintypes.HANDLE
kernel32.SetEvent.argtypes = [wintypes.HANDLE]
kernel32.SetEvent.restype = wintypes.BOOL
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
kernel32.CloseHandle.restype = wintypes.BOOL


class ActiveThreadMonitor:
    """Continuously read Codex's currently selected sidebar thread title."""

    def __init__(self) -> None:
        self.script = Path(__file__).with_name("active_thread.ps1")
        self.title = ""
        self.error = ""
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._process: subprocess.Popen[str] | None = None
        self._thread: threading.Thread | None = None
        self._watchdog: threading.Thread | None = None
        self._last_update = 0.0
        self._spawned_at = 0.0
        self._refresh_requested = threading.Event()
        self._refresh_event_name = f"Local\\CodexContextMeterThreadRefresh-{os.getpid()}"
        self._refresh_event: int | None = None

    def start(self) -> None:
        if self._watchdog is not None and self._watchdog.is_alive():
            return
        self._stop.clear()
        if not self._refresh_event:
            handle = kernel32.CreateEventW(
                None,
                False,
                False,
                self._refresh_event_name,
            )
            if handle:
                self._refresh_event = int(handle)
        with self._lock:
            self._spawn_locked()
        self._watchdog = threading.Thread(
            target=self._watchdog_loop,
            name="codex-active-thread-watchdog",
            daemon=True,
        )
        self._watchdog.start()

    def _spawn_locked(self) -> None:
        process = subprocess.Popen(
            [
                "powershell.exe",
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(self.script),
                "-IntervalMs",
                "1200",
                "-RefreshEventName",
                self._refresh_event_name,
                "-ParentPid",
                str(os.getpid()),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=CREATE_NO_WINDOW,
        )
        self._process = process
        self._last_update = time.monotonic()
        self._spawned_at = self._last_update
        self._thread = threading.Thread(
            target=self._read_loop,
            args=(process,),
            name="codex-active-thread",
            daemon=True,
        )
        self._thread.start()

    def _read_loop(self, process: subprocess.Popen[str]) -> None:
        if process is None or process.stdout is None:
            return
        for line in process.stdout:
            if self._stop.is_set():
                break
            if process is not self._process:
                break
            try:
                state = json.loads(line)
            except (TypeError, ValueError):
                continue
            if not isinstance(state, dict):
                continue
            title = state.get("title")
            self.title = title.strip() if state.get("ok") is True and isinstance(title, str) else ""
            self.error = str(state.get("error") or "")
            self._last_update = time.monotonic()

    def _watchdog_loop(self) -> None:
        while not self._stop.is_set():
            self._refresh_requested.wait(1.5)
            if self._stop.is_set():
                return
            refresh = self._refresh_requested.is_set()
            self._refresh_requested.clear()
            with self._lock:
                process = self._process
            if process is None:
                return
            stale = time.monotonic() - self._last_update > 6.0
            aged = time.monotonic() - self._spawned_at > 8.0
            if process.poll() is None and not stale and not aged and not refresh:
                continue
            self._recycle_process(process)

    def _recycle_process(self, process: subprocess.Popen[str]) -> None:
        with self._lock:
            if self._process is process:
                self._process = None
        self._terminate_process(process)
        if self._stop.is_set():
            return
        with self._lock:
            if not self._stop.is_set():
                self._spawn_locked()

    def force_refresh(self) -> None:
        if not self._stop.is_set():
            if not self._refresh_event or not kernel32.SetEvent(self._refresh_event):
                self._refresh_requested.set()

    @staticmethod
    def _terminate_process(process: subprocess.Popen[str]) -> None:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=1.5)
            except subprocess.TimeoutExpired:
                process.kill()
                try:
                    process.wait(timeout=1.0)
                except subprocess.TimeoutExpired:
                    pass

    def close(self) -> None:
        self._stop.set()
        self._refresh_requested.set()
        with self._lock:
            process = self._process
            self._process = None
        if process is not None:
            self._terminate_process(process)
        if self._watchdog is not None:
            self._watchdog.join(timeout=2.0)
            self._watchdog = None
        if self._refresh_event:
            kernel32.CloseHandle(self._refresh_event)
            self._refresh_event = None
