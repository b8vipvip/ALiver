from __future__ import annotations

import os
import threading
import time
from pathlib import Path
from typing import Any

from bridge import douyin_three_channel_patch as three
from bridge import douyin_visible_collector as collector

_BASE_FRAME_TO_RGB = three._frame_to_rgb
_BASE_RESTART_ACCESSIBILITY = three.restart_electron_accessibility


def _frame_to_rgb(frame: Any):
    """Accept the public windows-capture 2.x Frame API as well as older builds."""
    import numpy as np

    frame_buffer = getattr(frame, "frame_buffer", None)
    if frame_buffer is not None:
        value = np.asarray(frame_buffer)
        if value.ndim != 3 or value.shape[2] < 3:
            raise RuntimeError(f"Windows Graphics Capture 返回了无法识别的帧形状：{value.shape}")
        if value.shape[2] >= 4:
            value = value[:, :, :3][:, :, ::-1]
        else:
            value = value[:, :, :3]
        return value.copy()
    return _BASE_FRAME_TO_RGB(frame)


class _HwndWindowsGraphicsCaptureSession(three._WindowsGraphicsCaptureSession):
    def __init__(self, hwnd: int, window_name: str) -> None:
        super().__init__(window_name)
        self.hwnd = int(hwnd)

    def start(self) -> None:
        if self._started:
            return
        try:
            from windows_capture import Frame, InternalCaptureControl, WindowsCapture
        except ImportError as exc:
            raise RuntimeError("缺少 windows-capture，无法使用 Windows Graphics Capture") from exc

        kwargs = {
            "cursor_capture": False,
            "draw_border": False,
            "monitor_index": None,
            "window_hwnd": self.hwnd,
        }
        try:
            instance = WindowsCapture(**kwargs)
        except TypeError:
            # Older windows-capture builds do not expose window_hwnd. Keep a
            # compatibility path, but current requirements use 2.x.
            instance = WindowsCapture(
                cursor_capture=False,
                draw_border=False,
                monitor_index=None,
                window_name=self.window_name,
            )
        self._capture = instance

        @instance.event
        def on_frame_arrived(frame: Frame, capture_control: InternalCaptureControl):
            try:
                image = _frame_to_rgb(frame)
                with self._lock:
                    self._frame = image
                    self._captured_at = time.time()
                    self._control = capture_control
                    self._error = None
                self._frame_ready.set()
            except Exception as exc:  # noqa: BLE001 - callback must survive bad frames
                with self._lock:
                    self._error = f"{type(exc).__name__}: {exc}"
                self._frame_ready.set()

        @instance.event
        def on_closed():
            with self._lock:
                self._error = "直播伴侣窗口已关闭，Windows Graphics Capture 会话结束"
            self._frame_ready.set()

        start_free_threaded = getattr(instance, "start_free_threaded", None)
        if callable(start_free_threaded):
            self._thread_handle = start_free_threaded()
        else:
            thread = threading.Thread(target=instance.start, name="douyin-wgc-hwnd", daemon=True)
            thread.start()
            self._thread_handle = thread
        self._started = True


def _session_for_window(self: Any, window: Any) -> _HwndWindowsGraphicsCaptureSession:
    title = str(getattr(window, "title", "") or "直播伴侣")
    handle = int(getattr(window, "handle", 0) or 0)
    if not handle:
        raise RuntimeError("直播伴侣窗口没有有效 HWND，无法建立 Windows Graphics Capture")
    session = getattr(self, "_aliver_wgc_session", None)
    identity = (handle, title)
    if session is not None and getattr(self, "_aliver_wgc_identity", None) != identity:
        session.stop()
        session = None
    if session is None:
        session = _HwndWindowsGraphicsCaptureSession(handle, title)
        self._aliver_wgc_session = session
        self._aliver_wgc_identity = identity
    return session


def _launcher_path() -> Path:
    appdata = Path(os.environ.get("APPDATA") or Path.home())
    return appdata / "ALiver" / "restart-douyin-accessibility.cmd"


def _write_accessibility_launcher(process_path: Path, pid: int) -> Path:
    path = _launcher_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    escaped = str(process_path).replace("%", "%%")
    path.write_text(
        "@echo off\r\n"
        "chcp 65001 >nul\r\n"
        "echo 请先正常关闭抖音直播伴侣。关闭后，本窗口会自动用无障碍参数重新启动。\r\n"
        ":wait_loop\r\n"
        f'tasklist /FI "PID eq {int(pid)}" | find "{int(pid)}" >nul\r\n'
        "if not errorlevel 1 (timeout /t 1 /nobreak >nul & goto wait_loop)\r\n"
        f'start "" "{escaped}" --force-renderer-accessibility\r\n'
        "exit /b 0\r\n",
        encoding="utf-8-sig",
    )
    return path


def restart_electron_accessibility(self: Any) -> dict[str, Any]:
    try:
        return _BASE_RESTART_ACCESSIBILITY(self)
    except Exception as exc:  # noqa: BLE001 - convert Windows UIPI denial into an actionable result
        winerror = int(getattr(exc, "winerror", 0) or 0)
        text = f"{type(exc).__name__}: {exc}"
        denied = winerror == 5 or "拒绝访问" in text or "Access is denied" in text
        if not denied:
            raise
        window = self._find_window()
        process = three._selected_window_process(self, window)
        process_path = Path(str(process.get("process_path") or ""))
        if not process_path.exists():
            raise RuntimeError(text) from exc
        launcher = _write_accessibility_launcher(process_path, int(process.get("pid") or 0))
        result = three.electron_accessibility_status(self, refresh=True)
        result.update(
            {
                "restarted": False,
                "manual_close_required": True,
                "permission_mismatch": True,
                "launcher_path": str(launcher),
                "error": text,
                "message": (
                    "Bridge 与直播伴侣权限级别不同，Windows 拒绝发送关闭消息。"
                    "已生成等待式重启脚本；正常关闭直播伴侣后运行该脚本即可启用 Chromium 无障碍树。"
                ),
            }
        )
        with self._lock:
            self._state["electron_accessibility"] = result
        return result


def install_douyin_validation_fix() -> None:
    manager = collector.DouyinVisibleCollectorManager
    if getattr(manager, "_aliver_validation_fix_v1", False):
        return
    three._frame_to_rgb = _frame_to_rgb
    three._session_for_window = _session_for_window
    three.restart_electron_accessibility = restart_electron_accessibility
    manager.restart_electron_accessibility = restart_electron_accessibility
    manager._aliver_validation_fix_v1 = True
