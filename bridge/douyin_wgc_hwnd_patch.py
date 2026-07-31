from __future__ import annotations

import os
import subprocess
import threading
import time
from typing import Any

from bridge import douyin_three_channel_patch as three_channel
from bridge import douyin_visible_collector as collector
from bridge import douyin_window_capture_patch as window_capture

_ORIGINAL_CAPTURE_TARGET: Any = None
_ORIGINAL_ELECTRON_LINES: Any = None
_COMMAND_LINE_CACHE: dict[int, str] = {}
_COMMAND_LINE_CACHE_LOCK = threading.RLock()


def _root_hwnd(hwnd: int) -> int:
    if hwnd <= 0:
        return 0
    try:
        import win32gui

        if not win32gui.IsWindow(hwnd):
            return 0
        return int(win32gui.GetAncestor(hwnd, 2) or hwnd)  # GA_ROOT
    except Exception:
        return int(hwnd)


def _safe_query_process_command_line(pid: int) -> str:
    """Read another process command line outside the Bridge COM apartment.

    Repeated win32com/WMI calls from the collector thread caused RPC_E_DISCONNECTED
    and native access-violation fault dumps on the user's Windows 10 machine. A
    short-lived PowerShell process contains COM failures outside the Bridge and
    the result is cached for the lifetime of each PID.
    """

    pid = int(pid or 0)
    if pid <= 0 or os.name != "nt":
        return ""
    with _COMMAND_LINE_CACHE_LOCK:
        if pid in _COMMAND_LINE_CACHE:
            return _COMMAND_LINE_CACHE[pid]

    script = (
        "$OutputEncoding=[Console]::OutputEncoding=[Text.UTF8Encoding]::new();"
        f"$p=Get-CimInstance Win32_Process -Filter \"ProcessId={pid}\" -ErrorAction SilentlyContinue;"
        "if($p){[Console]::Write($p.CommandLine)}"
    )
    creationflags = int(getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000))
    try:
        completed = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=5,
            check=False,
            creationflags=creationflags,
        )
        value = completed.stdout.strip() if completed.returncode == 0 else ""
    except Exception:
        value = ""
    with _COMMAND_LINE_CACHE_LOCK:
        _COMMAND_LINE_CACHE[pid] = value
    return value


class HwndWindowsGraphicsCaptureSession(three_channel._WindowsGraphicsCaptureSession):
    """Capture one previously validated Win32 window instead of title matching.

    Douyin Live Companion exposes more than one Chromium window with the same
    title during startup/reload. Title-based lookup can therefore select a
    hidden or transient Chrome_WidgetWin window that cannot be converted into a
    GraphicsCaptureItem. Passing the selected HWND keeps discovery and capture
    pointed at the same window.
    """

    def __init__(self, window_name: str, window_hwnd: int) -> None:
        super().__init__(window_name)
        self.window_hwnd = _root_hwnd(int(window_hwnd or 0))
        self.target_mode = "window_hwnd"

    def _new_capture(self, WindowsCapture: Any) -> Any:
        common = {
            "monitor_index": None,
            "window_hwnd": self.window_hwnd,
        }
        try:
            return WindowsCapture(
                cursor_capture=False,
                draw_border=False,
                **common,
            )
        except TypeError:
            try:
                return WindowsCapture(
                    cursor_capture=None,
                    draw_border=None,
                    **common,
                )
            except TypeError:
                # Compatibility only for an older windows-capture wheel. The
                # supported 2.x API accepts window_hwnd, so this path should be
                # visible in diagnostics rather than silently preferred.
                self.target_mode = "window_name_compatibility"
                return WindowsCapture(
                    cursor_capture=False,
                    draw_border=False,
                    monitor_index=None,
                    window_name=self.window_name,
                )

    def start(self) -> None:
        if self._started:
            return
        if not self.window_hwnd:
            raise RuntimeError("WGC 目标窗口句柄无效，拒绝退回模糊标题匹配")
        try:
            from windows_capture import Frame, InternalCaptureControl, WindowsCapture
        except ImportError as exc:
            raise RuntimeError("缺少 windows-capture，无法使用 Windows Graphics Capture") from exc

        instance = self._new_capture(WindowsCapture)
        self._capture = instance

        @instance.event
        def on_frame_arrived(frame: Frame, capture_control: InternalCaptureControl):
            try:
                image = three_channel._frame_to_rgb(frame)
                with self._lock:
                    self._frame = image
                    self._captured_at = time.time()
                    self._control = capture_control
                    self._error = None
                self._frame_ready.set()
            except Exception as exc:  # noqa: BLE001 - callback must not terminate capture thread
                with self._lock:
                    self._error = f"{type(exc).__name__}: {exc}"
                self._frame_ready.set()

        @instance.event
        def on_closed():
            with self._lock:
                self._error = "直播伴侣窗口已关闭，Windows Graphics Capture 会话结束"
            self._frame_ready.set()

        start_free_threaded = getattr(instance, "start_free_threaded", None)
        try:
            if callable(start_free_threaded):
                self._thread_handle = start_free_threaded()
            else:
                thread = threading.Thread(target=instance.start, name="douyin-wgc-hwnd", daemon=True)
                thread.start()
                self._thread_handle = thread
        except Exception as exc:
            raise RuntimeError(
                f"无法为已验证窗口 HWND=0x{self.window_hwnd:X} 创建 GraphicsCaptureItem：{exc}"
            ) from exc
        self._started = True

    def stop(self) -> None:
        with self._lock:
            control = self._control
            thread_handle = self._thread_handle
            self._control = None
            self._thread_handle = None
        for candidate in (control, thread_handle):
            stop = getattr(candidate, "stop", None)
            if callable(stop):
                try:
                    stop()
                except Exception:
                    pass
        self._started = False


def _session_for_window(self: Any, window: Any) -> HwndWindowsGraphicsCaptureSession:
    title = str(getattr(window, "title", "") or "直播伴侣")
    handle = _root_hwnd(int(getattr(window, "handle", 0) or 0))
    if not handle:
        raise RuntimeError("直播伴侣窗口句柄已经失效，请刷新窗口后重试")
    identity = (handle, title)
    session = getattr(self, "_aliver_wgc_session", None)
    if session is not None and getattr(self, "_aliver_wgc_identity", None) != identity:
        session.stop()
        session = None
    if not isinstance(session, HwndWindowsGraphicsCaptureSession):
        if session is not None:
            session.stop()
        session = HwndWindowsGraphicsCaptureSession(title, handle)
        self._aliver_wgc_session = session
        self._aliver_wgc_identity = identity
    with self._lock:
        self._state["wgc_target_hwnd"] = handle
        self._state["wgc_target_hwnd_hex"] = f"0x{handle:X}"
        self._state["wgc_target_title"] = title
        self._state["wgc_target_mode"] = session.target_mode
    return session


def _window_surface_fallback(self: Any, window: Any):
    hwnd = _root_hwnd(int(getattr(window, "handle", 0) or 0))
    if not hwnd:
        return None
    image = window_capture._print_window_capture(
        hwnd,
        int(getattr(window, "width", 0) or 0),
        int(getattr(window, "height", 0) or 0),
    )
    if image is None:
        return None
    with self._lock:
        self._state["printwindow_fallback_hwnd"] = hwnd
        self._state["printwindow_fallback_at"] = collector.utc_iso()
    return image


def _capture_target_window(self: Any, window: Any):
    try:
        return _ORIGINAL_CAPTURE_TARGET(self, window)
    except Exception as wgc_exc:
        image = _window_surface_fallback(self, window)
        if image is None:
            raise RuntimeError(
                f"{wgc_exc}；已绑定准确 HWND 并尝试 PrintWindow 窗口表面兜底，仍未取得画面"
            ) from wgc_exc

        region = self._config.get("ocr_region") or collector.DEFAULT_CONFIG["ocr_region"]
        crop_left, crop_top, crop_width, crop_height = window_capture._relative_region_pixels(
            int(image.shape[1]), int(image.shape[0]), dict(region)
        )
        crop = image[crop_top : crop_top + crop_height, crop_left : crop_left + crop_width].copy()
        if crop.size == 0:
            raise RuntimeError("PrintWindow 已取得窗口画面，但 OCR 区域超出范围，请重新自动校准")

        self._last_window_image = image
        self._last_region_image = crop
        with self._lock:
            self._state["capture_source"] = "printwindow"
            self._state["last_capture_at"] = collector.utc_iso()
            self._state["last_capture_size"] = [int(image.shape[1]), int(image.shape[0])]
            self._state["last_region_pixels"] = [crop_left, crop_top, crop_width, crop_height]
            self._state["capture_safety"] = "printwindow_exact_hwnd_surface"
            self._state["capture_foreground_required"] = False
            self._state["capture_occluders"] = []
            self._state["last_error"] = None
            self._state["wgc_fallback_reason"] = f"{type(wgc_exc).__name__}: {wgc_exc}"
        return image, crop, "printwindow", (crop_left, crop_top, crop_width, crop_height)


def _safe_electron_accessibility_lines(self: Any, window: Any) -> list[dict[str, Any]]:
    status = three_channel.electron_accessibility_status(self)
    if not bool(status.get("enabled")):
        return []
    # The first-level UIA implementation already reads the Chromium tree once
    # the process is launched with --force-renderer-accessibility. Reusing it
    # avoids a second pywinauto descendants traversal on every scan.
    lines = list(self._uia_lines(window) or [])
    for line in lines:
        line["source"] = "electron_accessibility"
        line["forced_accessibility"] = True
    return lines


def install_douyin_wgc_hwnd_patch() -> None:
    global _ORIGINAL_CAPTURE_TARGET, _ORIGINAL_ELECTRON_LINES
    manager = collector.DouyinVisibleCollectorManager
    if getattr(manager, "_aliver_wgc_hwnd_patch", False):
        return

    _ORIGINAL_CAPTURE_TARGET = three_channel._capture_target_window
    _ORIGINAL_ELECTRON_LINES = three_channel.electron_accessibility_lines
    three_channel._query_process_command_line = _safe_query_process_command_line
    three_channel._session_for_window = _session_for_window
    three_channel._capture_target_window = _capture_target_window
    three_channel.electron_accessibility_lines = _safe_electron_accessibility_lines
    window_capture._capture_target_window = _capture_target_window
    manager._capture_target_window = _capture_target_window
    manager.electron_accessibility_lines = _safe_electron_accessibility_lines
    manager._aliver_wgc_hwnd_patch = True
