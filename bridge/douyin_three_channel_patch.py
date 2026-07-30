from __future__ import annotations

import importlib.util
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

from bridge import douyin_visible_collector as collector
from bridge import douyin_window_capture_patch as capture

ELECTRON_ACCESSIBILITY_FLAG = "--force-renderer-accessibility"

collector.DEFAULT_CONFIG.setdefault("enable_electron_accessibility", True)
collector.DEFAULT_CONFIG.setdefault("enable_windows_graphics_capture", True)
collector.DEFAULT_CONFIG.setdefault("allow_screen_capture_fallback", False)
collector.DEFAULT_CONFIG.setdefault("wgc_frame_timeout_seconds", 3.0)

_BASE_CAPTURE_TARGET: Any = None
_BASE_MANAGER_STOP: Any = None
_BASE_MANAGER_STATUS: Any = None


def _member_value(value: Any, name: str, default: Any = None) -> Any:
    member = getattr(value, name, default)
    if callable(member):
        try:
            return member()
        except TypeError:
            return member
    return member


def _frame_to_rgb(frame: Any):
    import numpy as np

    to_numpy = getattr(frame, "to_numpy", None)
    array = None
    if callable(to_numpy):
        try:
            array = to_numpy(copy=True)
        except TypeError:
            array = to_numpy()
    if array is None:
        buffer_method = getattr(frame, "buffer", None)
        if not callable(buffer_method):
            raise RuntimeError("Windows Graphics Capture 帧不支持 to_numpy 或 buffer")
        raw = buffer_method()
        width = int(_member_value(frame, "width", 0) or 0)
        height = int(_member_value(frame, "height", 0) or 0)
        if width <= 0 or height <= 0:
            raise RuntimeError("Windows Graphics Capture 返回了无效帧尺寸")
        array = np.frombuffer(raw, dtype=np.uint8).reshape((height, width, 4))

    value = np.asarray(array)
    if value.ndim != 3 or value.shape[2] < 3:
        raise RuntimeError(f"Windows Graphics Capture 返回了无法识别的帧形状：{value.shape}")
    # Windows Graphics Capture exposes BGRA/RGBA buffers depending on package
    # version. RapidOCR is tolerant of channel order; previews are normalized to
    # RGB using the common BGRA layout used by windows-capture.
    if value.shape[2] >= 4:
        value = value[:, :, :3][:, :, ::-1]
    else:
        value = value[:, :, :3]
    return value.copy()


class _WindowsGraphicsCaptureSession:
    def __init__(self, window_name: str) -> None:
        self.window_name = window_name
        self._lock = threading.RLock()
        self._frame_ready = threading.Event()
        self._frame: Any = None
        self._captured_at = 0.0
        self._error: str | None = None
        self._control: Any = None
        self._capture: Any = None
        self._thread_handle: Any = None
        self._started = False

    def start(self) -> None:
        if self._started:
            return
        try:
            from windows_capture import Frame, InternalCaptureControl, WindowsCapture
        except ImportError as exc:
            raise RuntimeError("缺少 windows-capture，无法使用 Windows Graphics Capture") from exc

        try:
            instance = WindowsCapture(
                cursor_capture=False,
                draw_border=False,
                monitor_index=None,
                window_name=self.window_name,
            )
        except TypeError:
            instance = WindowsCapture(
                cursor_capture=None,
                draw_border=None,
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
        if callable(start_free_threaded):
            self._thread_handle = start_free_threaded()
        else:
            thread = threading.Thread(target=instance.start, name="douyin-wgc", daemon=True)
            thread.start()
            self._thread_handle = thread
        self._started = True

    def frame(self, timeout: float):
        self.start()
        with self._lock:
            if self._frame is not None and time.time() - self._captured_at <= 8.0:
                return self._frame.copy(), self._captured_at
        self._frame_ready.clear()
        if not self._frame_ready.wait(max(0.5, timeout)):
            raise RuntimeError("Windows Graphics Capture 等待直播伴侣画面超时")
        with self._lock:
            if self._frame is None:
                raise RuntimeError(self._error or "Windows Graphics Capture 尚未取得画面")
            return self._frame.copy(), self._captured_at

    def stop(self) -> None:
        with self._lock:
            control = self._control
            self._control = None
        if control is not None:
            try:
                control.stop()
            except Exception:
                pass
        self._started = False


def _wgc_available() -> bool:
    return importlib.util.find_spec("windows_capture") is not None


def _query_process_command_line(pid: int) -> str:
    if pid <= 0:
        return ""
    try:
        import pythoncom
        import win32com.client

        pythoncom.CoInitialize()
        try:
            service = win32com.client.GetObject("winmgmts:")
            rows = service.ExecQuery(f"SELECT CommandLine FROM Win32_Process WHERE ProcessId={int(pid)}")
            for row in rows:
                return str(getattr(row, "CommandLine", "") or "")
        finally:
            pythoncom.CoUninitialize()
    except Exception:
        return ""
    return ""


def _selected_window_process(self: Any, window: Any | None = None) -> dict[str, Any]:
    if window is None:
        window = self._find_window()
    handle = int(getattr(window, "handle", 0) or 0) if window is not None else 0
    candidates = list(self._state.get("window_candidates") or [])
    selected = next((item for item in candidates if int(item.get("handle") or 0) == handle), None)
    if selected is None:
        selected = {"handle": handle, "title": getattr(window, "title", "") if window else ""}
    result = dict(selected)
    pid = int(result.get("pid") or 0)
    result["command_line"] = _query_process_command_line(pid)
    return result


def electron_accessibility_status(self: Any, *, refresh: bool = False) -> dict[str, Any]:
    now = time.time()
    cached = self._state.get("electron_accessibility")
    cached_at = float(self._state.get("electron_accessibility_checked_at") or 0.0)
    if not refresh and isinstance(cached, dict) and now - cached_at < 5.0:
        return dict(cached)

    window = self._find_window()
    process = _selected_window_process(self, window)
    command_line = str(process.get("command_line") or "")
    forced = ELECTRON_ACCESSIBILITY_FLAG.lower() in command_line.lower()
    available = bool(window and process.get("process_path"))
    result = {
        "available": available,
        "enabled": forced,
        "restart_required": bool(available and not forced),
        "flag": ELECTRON_ACCESSIBILITY_FLAG,
        "pid": int(process.get("pid") or 0),
        "process_path": str(process.get("process_path") or ""),
        "command_line": command_line,
        "window_handle": int(getattr(window, "handle", 0) or 0) if window else 0,
        "message": (
            "直播伴侣已使用 Chromium 强制无障碍模式启动"
            if forced
            else "需要在未开播时重启直播伴侣，才能强制暴露 Chromium 文本树"
            if available
            else "尚未找到直播伴侣主进程"
        ),
    }
    with self._lock:
        self._state["electron_accessibility"] = result
        self._state["electron_accessibility_checked_at"] = now
    return dict(result)


def restart_electron_accessibility(self: Any) -> dict[str, Any]:
    window = self._find_window()
    if window is None:
        raise RuntimeError("没有找到直播伴侣窗口，请先正常启动一次以定位安装路径")
    process = _selected_window_process(self, window)
    process_path = Path(str(process.get("process_path") or ""))
    if not process_path.exists():
        raise RuntimeError("无法读取直播伴侣可执行文件路径")
    current = electron_accessibility_status(self, refresh=True)
    if current.get("enabled"):
        return {**current, "restarted": False}

    try:
        import win32gui
    except ImportError as exc:
        raise RuntimeError("缺少 pywin32，无法安全重启直播伴侣") from exc

    hwnd = int(window.handle or 0)
    if hwnd:
        win32gui.PostMessage(hwnd, 0x0010, 0, 0)  # WM_CLOSE, never force-kill a live process.
    deadline = time.time() + 15.0
    while hwnd and time.time() < deadline:
        if not win32gui.IsWindow(hwnd):
            break
        time.sleep(0.25)
    if hwnd and win32gui.IsWindow(hwnd):
        raise RuntimeError("直播伴侣尚未退出。请确认退出提示并结束直播后，再点击无障碍模式重启")

    subprocess.Popen(
        [str(process_path), ELECTRON_ACCESSIBILITY_FLAG],
        cwd=str(process_path.parent),
        close_fds=True,
    )
    with self._lock:
        self._state["electron_accessibility_checked_at"] = 0.0
    deadline = time.time() + 25.0
    detected = None
    while time.time() < deadline:
        time.sleep(0.5)
        detected = self._find_window()
        if detected is not None:
            break
    result = electron_accessibility_status(self, refresh=True)
    result["restarted"] = True
    result["window_found"] = detected is not None
    return result


def _text_candidates(control: Any) -> list[str]:
    values: list[str] = []
    try:
        values.append(str(control.window_text() or ""))
    except Exception:
        pass
    info = getattr(control, "element_info", None)
    if info is not None:
        values.append(str(getattr(info, "name", "") or ""))
        values.append(str(getattr(info, "rich_text", "") or ""))
    try:
        for item in control.texts() or []:
            values.append(str(item or ""))
    except Exception:
        pass
    try:
        legacy = control.legacy_properties() or {}
        values.extend(str(legacy.get(key) or "") for key in ("Name", "Value", "Description", "Help"))
    except Exception:
        pass
    output: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = collector.clean_text(value)
        if text and text not in seen:
            seen.add(text)
            output.append(text)
    return output


def electron_accessibility_lines(self: Any, window: Any) -> list[dict[str, Any]]:
    if not bool(self._config.get("enable_electron_accessibility", True)):
        return []
    try:
        from pywinauto import Desktop
    except ImportError:
        return []

    status = electron_accessibility_status(self)
    root = Desktop(backend="uia").window(handle=int(window.handle or 0))
    try:
        root.wait("exists", timeout=2)
    except Exception:
        return []
    left, top, width, height = self._region_rect(window)
    right, bottom = left + width, top + height
    values: list[dict[str, Any]] = []
    seen: set[str] = set()
    try:
        descendants = root.descendants()
    except Exception:
        return []
    for control in descendants[:3000]:
        try:
            rect = control.rectangle()
            intersects = not (rect.right < left or rect.left > right or rect.bottom < top or rect.top > bottom)
            if not intersects:
                continue
            texts = _text_candidates(control)
        except Exception:
            continue
        for text in texts:
            if text in seen:
                continue
            seen.add(text)
            values.append(
                {
                    "text": text,
                    "source": "electron_accessibility",
                    "confidence": 1.0,
                    "bbox": [rect.left, rect.top, rect.right, rect.bottom],
                    "forced_accessibility": bool(status.get("enabled")),
                }
            )
    return values


def _session_for_window(self: Any, window: Any) -> _WindowsGraphicsCaptureSession:
    title = str(getattr(window, "title", "") or "直播伴侣")
    handle = int(getattr(window, "handle", 0) or 0)
    session = getattr(self, "_aliver_wgc_session", None)
    identity = (handle, title)
    if session is not None and getattr(self, "_aliver_wgc_identity", None) != identity:
        session.stop()
        session = None
    if session is None:
        session = _WindowsGraphicsCaptureSession(title)
        self._aliver_wgc_session = session
        self._aliver_wgc_identity = identity
    return session


def _windows_graphics_capture(self: Any, window: Any):
    if not bool(self._config.get("enable_windows_graphics_capture", True)):
        raise RuntimeError("Windows Graphics Capture 通道已关闭")
    if not _wgc_available():
        raise RuntimeError("缺少 windows-capture 2.x，请重新安装 requirements.txt")
    try:
        import win32gui

        if int(window.handle or 0) and win32gui.IsIconic(int(window.handle)):
            raise RuntimeError("直播伴侣已最小化；请恢复窗口以保证 Chromium 持续渲染")
    except ImportError:
        pass

    session = _session_for_window(self, window)
    image, captured_at = session.frame(float(self._config.get("wgc_frame_timeout_seconds") or 3.0))
    if not capture._image_has_content(image):
        raise RuntimeError("Windows Graphics Capture 返回了空画面")
    with self._lock:
        self._state["wgc_last_frame_at"] = collector.utc_iso()
        self._state["wgc_frame_timestamp"] = captured_at
        self._state["wgc_last_error"] = None
    return image


def _capture_target_window(self: Any, window: Any):
    region = self._config.get("ocr_region") or collector.DEFAULT_CONFIG["ocr_region"]
    image = None
    source = None
    wgc_error = None
    if bool(self._config.get("enable_windows_graphics_capture", True)):
        try:
            image = _windows_graphics_capture(self, window)
            source = "windows_graphics_capture"
        except Exception as exc:  # noqa: BLE001 - optional channel falls back with diagnostics
            wgc_error = f"{type(exc).__name__}: {exc}"
            with self._lock:
                self._state["wgc_last_error"] = wgc_error
    if image is None and bool(self._config.get("allow_screen_capture_fallback", False)):
        if _BASE_CAPTURE_TARGET is None:
            raise RuntimeError(wgc_error or "没有可用的窗口捕获实现")
        return _BASE_CAPTURE_TARGET(self, window)
    if image is None:
        raise RuntimeError(
            (wgc_error or "Windows Graphics Capture 未取得画面")
            + "；为避免误识别其他窗口，桌面截图兜底默认关闭"
        )

    crop_left, crop_top, crop_width, crop_height = capture._relative_region_pixels(
        int(image.shape[1]), int(image.shape[0]), dict(region)
    )
    crop = image[crop_top : crop_top + crop_height, crop_left : crop_left + crop_width].copy()
    if crop.size == 0:
        raise RuntimeError("OCR 区域超出 Windows Graphics Capture 画面，请重新自动校准")
    self._last_window_image = image
    self._last_region_image = crop
    with self._lock:
        self._state["capture_source"] = source
        self._state["last_capture_at"] = collector.utc_iso()
        self._state["last_capture_size"] = [int(image.shape[1]), int(image.shape[0])]
        self._state["last_region_pixels"] = [crop_left, crop_top, crop_width, crop_height]
        self._state["capture_safety"] = "windows_graphics_capture_window_surface"
        self._state["capture_foreground_required"] = False
        self._state["capture_occluders"] = []
        self._state["last_error"] = None
    return image, crop, source, (crop_left, crop_top, crop_width, crop_height)


def _events_from_lines(self: Any, lines: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return collector.parse_visible_lines(
        lines,
        confidence_threshold=float(self._config.get("confidence_threshold") or 0.72),
        capture_join_notices=bool(self._config.get("capture_join_notices", False)),
    )


def _collect_lines(self: Any, window: Any) -> list[dict[str, Any]]:
    mode = str(self._config.get("mode") or "hybrid")
    trace: list[dict[str, Any]] = []

    def record(name: str, lines: list[dict[str, Any]], error: str | None = None) -> bool:
        events = _events_from_lines(self, lines) if lines else []
        trace.append({"channel": name, "line_count": len(lines), "event_count": len(events), "error": error})
        return bool(events)

    if mode in {"hybrid", "uia"} and self._state.get("uia_available"):
        try:
            lines = self._uia_lines(window)
            if record("uia", lines) or mode == "uia":
                with self._lock:
                    self._state["active_source"] = "uia"
                    self._state["channel_trace"] = trace
                return lines
        except Exception as exc:  # noqa: BLE001
            record("uia", [], f"{type(exc).__name__}: {exc}")

    if mode in {"hybrid", "electron"} and bool(self._config.get("enable_electron_accessibility", True)):
        try:
            lines = electron_accessibility_lines(self, window)
            if record("electron_accessibility", lines) or mode == "electron":
                with self._lock:
                    self._state["active_source"] = "electron_accessibility"
                    self._state["channel_trace"] = trace
                return lines
        except Exception as exc:  # noqa: BLE001
            record("electron_accessibility", [], f"{type(exc).__name__}: {exc}")

    if mode in {"hybrid", "ocr", "wgc"}:
        try:
            lines = self._ocr_lines(window)
            for line in lines:
                line["source"] = "windows_graphics_capture"
            record("windows_graphics_capture", lines)
            with self._lock:
                self._state["active_source"] = "windows_graphics_capture"
                self._state["channel_trace"] = trace
            return lines
        except Exception as exc:
            record("windows_graphics_capture", [], f"{type(exc).__name__}: {exc}")
            with self._lock:
                self._state["channel_trace"] = trace
            raise

    with self._lock:
        self._state["channel_trace"] = trace
    return []


def probe_channels(self: Any) -> dict[str, Any]:
    window = self._find_window()
    if window is None:
        raise RuntimeError("没有找到直播伴侣窗口")
    channels: list[dict[str, Any]] = []
    for name, callback in (
        ("uia", lambda: self._uia_lines(window)),
        ("electron_accessibility", lambda: electron_accessibility_lines(self, window)),
        ("windows_graphics_capture", lambda: self._ocr_lines(window)),
    ):
        try:
            lines = callback()
            if name == "windows_graphics_capture":
                for line in lines:
                    line["source"] = name
            events = _events_from_lines(self, lines)
            channels.append(
                {
                    "channel": name,
                    "available": True,
                    "line_count": len(lines),
                    "event_count": len(events),
                    "sample_lines": lines[:12],
                    "sample_events": events[:8],
                    "error": None,
                }
            )
        except Exception as exc:  # noqa: BLE001
            channels.append(
                {
                    "channel": name,
                    "available": False,
                    "line_count": 0,
                    "event_count": 0,
                    "sample_lines": [],
                    "sample_events": [],
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
    result = {
        "window": window.as_dict(),
        "channels": channels,
        "electron_accessibility": electron_accessibility_status(self, refresh=True),
        "wgc_available": _wgc_available(),
        "created_at": collector.utc_iso(),
    }
    with self._lock:
        self._state["last_channel_probe"] = result
    return result


def _status(self: Any) -> dict[str, Any]:
    try:
        electron_accessibility_status(self)
    except Exception:
        pass
    with self._lock:
        self._state["wgc_available"] = _wgc_available()
        self._state.setdefault("channel_trace", [])
    return _BASE_MANAGER_STATUS(self)


def _stop(self: Any) -> dict[str, Any]:
    session = getattr(self, "_aliver_wgc_session", None)
    if session is not None:
        session.stop()
        self._aliver_wgc_session = None
        self._aliver_wgc_identity = None
    return _BASE_MANAGER_STOP(self)


def install_douyin_three_channel_patch() -> None:
    global _BASE_CAPTURE_TARGET, _BASE_MANAGER_STOP, _BASE_MANAGER_STATUS
    manager = collector.DouyinVisibleCollectorManager
    if getattr(manager, "_aliver_three_channel_patch", False):
        return
    _BASE_CAPTURE_TARGET = capture._capture_target_window
    _BASE_MANAGER_STOP = manager.stop
    _BASE_MANAGER_STATUS = manager.status
    capture._capture_target_window = _capture_target_window
    manager._capture_target_window = _capture_target_window
    manager._collect_lines = _collect_lines
    manager.electron_accessibility_status = electron_accessibility_status
    manager.restart_electron_accessibility = restart_electron_accessibility
    manager.electron_accessibility_lines = electron_accessibility_lines
    manager.probe_channels = probe_channels
    manager.status = _status
    manager.stop = _stop
    manager._aliver_three_channel_patch = True
