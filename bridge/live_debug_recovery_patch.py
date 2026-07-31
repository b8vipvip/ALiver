from __future__ import annotations

import asyncio
import ctypes
import json
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from bridge import douyin_visible_collector as collector
from bridge import douyin_wgc_hwnd_patch as wgc_patch
from bridge import full_validation_v2 as staged_validation

_BASE_FIND_WINDOW: Any = None
_BASE_WINDOW_AS_DICT: Any = None
_BASE_PREVIEW: Any = None
_BASE_EXPORT_DIAGNOSTICS: Any = None
_BASE_RUN_LIVE: Any = None


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _is_cloaked(hwnd: int) -> bool:
    try:
        value = ctypes.c_int(0)
        result = ctypes.windll.dwmapi.DwmGetWindowAttribute(
            int(hwnd),
            14,  # DWMWA_CLOAKED
            ctypes.byref(value),
            ctypes.sizeof(value),
        )
        return result == 0 and value.value != 0
    except Exception:
        return False


def _window_details(hwnd: int) -> dict[str, Any]:
    try:
        import win32gui
        import win32process

        valid = bool(hwnd and win32gui.IsWindow(hwnd))
        if not valid:
            return {"handle": int(hwnd or 0), "valid": False}
        left, top, right, bottom = [int(value) for value in win32gui.GetWindowRect(hwnd)]
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        return {
            "handle": int(hwnd),
            "handle_hex": f"0x{int(hwnd):X}",
            "valid": True,
            "visible": bool(win32gui.IsWindowVisible(hwnd)),
            "iconic": bool(win32gui.IsIconic(hwnd)),
            "cloaked": _is_cloaked(hwnd),
            "title": str(win32gui.GetWindowText(hwnd) or ""),
            "class_name": str(win32gui.GetClassName(hwnd) or ""),
            "pid": int(pid),
            "rect": [left, top, right, bottom],
            "width": max(0, right - left),
            "height": max(0, bottom - top),
        }
    except Exception as exc:
        return {
            "handle": int(hwnd or 0),
            "valid": False,
            "inspection_error": f"{type(exc).__name__}: {exc}",
        }


def _candidate_handles(hwnd: int) -> list[int]:
    try:
        import win32gui
        import win32process
    except ImportError:
        return [int(hwnd)] if hwnd else []

    hwnd = int(hwnd or 0)
    if not hwnd or not win32gui.IsWindow(hwnd):
        return []
    values: list[int] = []

    def add(value: Any) -> None:
        candidate = int(value or 0)
        if candidate and candidate not in values and win32gui.IsWindow(candidate):
            values.append(candidate)

    add(hwnd)
    try:
        add(win32gui.GetWindow(hwnd, 4))  # GW_OWNER
    except Exception:
        pass
    for flag in (3, 2):  # GA_ROOTOWNER, GA_ROOT
        try:
            add(win32gui.GetAncestor(hwnd, flag))
        except Exception:
            pass

    try:
        _, target_pid = win32process.GetWindowThreadProcessId(hwnd)
        target_title = str(win32gui.GetWindowText(hwnd) or "")

        def collect(candidate: int, _output: Any) -> bool:
            try:
                _, pid = win32process.GetWindowThreadProcessId(candidate)
                title = str(win32gui.GetWindowText(candidate) or "")
                if int(pid) != int(target_pid):
                    return True
                if "直播伴侣" in title or (target_title and title == target_title):
                    add(candidate)
            except Exception:
                pass
            return True

        win32gui.EnumWindows(collect, None)
    except Exception:
        pass
    return values


def _candidate_score(details: dict[str, Any], *, original: int) -> float:
    if not details.get("valid"):
        return float("-inf")
    if not details.get("visible") or details.get("iconic") or details.get("cloaked"):
        return float("-inf")
    width = int(details.get("width") or 0)
    height = int(details.get("height") or 0)
    if width < 500 or height < 300:
        return float("-inf")
    score = min(width * height, 4_000_000) / 1000.0
    if int(details.get("handle") or 0) == int(original or 0):
        score += 10_000.0
    title = str(details.get("title") or "")
    class_name = str(details.get("class_name") or "")
    if "直播伴侣" in title:
        score += 2_000.0
    if "Chrome_WidgetWin" in class_name:
        score += 200.0
    return score


def _select_capture_hwnd(hwnd: int) -> tuple[int, list[dict[str, Any]]]:
    original = int(hwnd or 0)
    details = [_window_details(candidate) for candidate in _candidate_handles(original)]
    for item in details:
        item["selection_score"] = _candidate_score(item, original=original)
    eligible = [item for item in details if item["selection_score"] != float("-inf")]
    if not eligible:
        return 0, details
    selected = max(eligible, key=lambda item: float(item["selection_score"]))
    return int(selected["handle"]), details


def _preferred_capture_hwnd(hwnd: int) -> int:
    selected, _ = _select_capture_hwnd(int(hwnd or 0))
    return selected


def _patched_window_as_dict(self: Any) -> dict[str, Any]:
    value = dict(_BASE_WINDOW_AS_DICT(self))
    value.update(_window_details(int(getattr(self, "handle", 0) or 0)))
    return value


def _patched_find_window(self: Any):
    window = _BASE_FIND_WINDOW(self)
    if window is None:
        with self._lock:
            self._state["window_selection_status"] = "not_found"
            self._state["window_candidates"] = []
        return None

    original_hwnd = int(getattr(window, "handle", 0) or 0)
    selected_hwnd, candidates = _select_capture_hwnd(original_hwnd)
    if not selected_hwnd:
        with self._lock:
            self._state["window_selection_status"] = "no_visible_capture_candidate"
            self._state["window_original_hwnd"] = original_hwnd
            self._state["window_candidates"] = candidates
        return None

    details = _window_details(selected_hwnd)
    rect = list(details.get("rect") or [window.left, window.top, window.left + window.width, window.top + window.height])
    selected = collector.WindowSnapshot(
        str(details.get("title") or window.title),
        int(rect[0]),
        int(rect[1]),
        max(0, int(rect[2]) - int(rect[0])),
        max(0, int(rect[3]) - int(rect[1])),
        selected_hwnd,
    )
    with self._lock:
        self._state["window_selection_status"] = "selected"
        self._state["window_original_hwnd"] = original_hwnd
        self._state["window_selected_hwnd"] = selected_hwnd
        self._state["window_selected_hwnd_hex"] = f"0x{selected_hwnd:X}"
        self._state["window_selected_visible"] = bool(details.get("visible"))
        self._state["window_selected_cloaked"] = bool(details.get("cloaked"))
        self._state["window_selected_iconic"] = bool(details.get("iconic"))
        self._state["window_candidates"] = candidates
    return selected


def _clear_capture_cache(self: Any, *, reason: str) -> None:
    self._last_window_image = None
    self._last_region_image = None
    with self._lock:
        for key in (
            "capture_source",
            "capture_safety",
            "last_capture_size",
            "last_region_pixels",
        ):
            self._state.pop(key, None)
        self._state["capture_current"] = False
        self._state["capture_historical"] = False
        self._state["capture_reset_at"] = _utc_iso()
        self._state["capture_reset_reason"] = reason
        self._state["capture_error"] = None


def _patched_preview(self: Any) -> dict[str, Any]:
    _clear_capture_cache(self, reason="preview_started")
    started = time.time()
    try:
        result = dict(_BASE_PREVIEW(self))
    except Exception as exc:
        self._last_window_image = None
        self._last_region_image = None
        with self._lock:
            self._state["capture_current"] = False
            self._state["capture_failed_at"] = _utc_iso()
            self._state["capture_error"] = f"{type(exc).__name__}: {exc}"
        raise

    current = self._last_window_image is not None and self._last_region_image is not None
    generated_at = _utc_iso()
    with self._lock:
        self._state["capture_current"] = current
        self._state["capture_historical"] = False
        self._state["capture_generated_at"] = generated_at
        self._state["capture_age_seconds"] = round(max(0.0, time.time() - started), 3)
        self._state["capture_error"] = None
    result.update(
        {
            "capture_current": current,
            "capture_historical": False,
            "capture_generated_at": generated_at,
            "capture_age_seconds": round(max(0.0, time.time() - started), 3),
        }
    )
    return result


def _patched_export_diagnostics(self: Any) -> dict[str, Any]:
    _clear_capture_cache(self, reason="diagnostics_started")
    started = time.time()
    result = dict(_BASE_EXPORT_DIAGNOSTICS(self))
    current = self._last_window_image is not None and self._last_region_image is not None
    metadata = {
        "capture_current": current,
        "capture_historical": False,
        "capture_generated_at": self._state.get("last_capture_at") if current else None,
        "capture_failed_at": None if current else _utc_iso(),
        "capture_error": None if current else self._state.get("last_error"),
        "capture_source": self._state.get("capture_source"),
        "elapsed_seconds": round(max(0.0, time.time() - started), 3),
        "window_selection_status": self._state.get("window_selection_status"),
        "window_selected_hwnd_hex": self._state.get("window_selected_hwnd_hex"),
    }
    with self._lock:
        self._state.update(metadata)
    path = Path(str(result.get("path") or ""))
    if path.exists() and path.is_file():
        try:
            with zipfile.ZipFile(path, "a", compression=zipfile.ZIP_DEFLATED) as archive:
                archive.writestr(
                    "capture-metadata.json",
                    json.dumps(metadata, ensure_ascii=False, indent=2, default=str),
                )
                if not current:
                    archive.writestr(
                        "capture-error.txt",
                        str(metadata.get("capture_error") or "本次没有取得新的直播伴侣画面；诊断包未写入历史缓存截图。"),
                    )
        except (OSError, zipfile.BadZipFile):
            pass
    result.update(metadata)
    return result


async def _run_live_with_auto_start(agent: Any, options: dict[str, Any]) -> list[dict[str, Any]]:
    manager = getattr(agent, "douyin_collector", None)
    if manager is None:
        return await _BASE_RUN_LIVE(agent, options)

    status = manager.status()
    auto_started = False
    if not status.get("running"):
        config = dict(status.get("config") or {})
        extension_id = str(config.get("extension_id") or "").strip()
        if not extension_id:
            return [
                staged_validation.validation_step(
                    "live.collector_event",
                    phase="live",
                    level="failed",
                    message="可视采集器未配置 Chrome 导演扩展；请先在采集器中选择扩展并保存",
                    data={"auto_start_attempted": False, "status": status},
                )
            ]
        try:
            manager.update_config({"capture_join_notices": True})
            baseline_scan = int(status.get("scan_count") or 0)
            baseline_scan_at = status.get("last_scan_at")
            await asyncio.to_thread(manager.start)
            auto_started = True
        except Exception as exc:
            return [
                staged_validation.validation_step(
                    "live.collector_event",
                    phase="live",
                    level="failed",
                    message=f"实况验证自动启动采集器失败：{type(exc).__name__}: {exc}",
                    data={"auto_start_attempted": True, "status": manager.status()},
                )
            ]

        deadline = time.monotonic() + 10.0
        ready = False
        while time.monotonic() < deadline:
            current = manager.status()
            new_scan = int(current.get("scan_count") or 0) > baseline_scan
            new_scan_at = bool(current.get("last_scan_at") and current.get("last_scan_at") != baseline_scan_at)
            if current.get("running") and (new_scan or new_scan_at):
                ready = True
                break
            if current.get("last_error") and time.monotonic() + 7.0 >= deadline:
                break
            await asyncio.sleep(0.25)
        if not ready:
            current = manager.status()
            return [
                staged_validation.validation_step(
                    "live.collector_event",
                    phase="live",
                    level="failed",
                    message=(
                        "采集器已自动启动，但 10 秒内没有完成一次有效扫描："
                        + str(current.get("last_error") or "请保持直播伴侣打开、非最小化，并检查互动区捕获")
                    ),
                    data={"auto_start_attempted": True, "auto_started": True, "status": current},
                )
            ]

    steps = await _BASE_RUN_LIVE(agent, options)
    if auto_started:
        for step in steps:
            data = dict(step.get("data") or {}) if isinstance(step.get("data"), dict) else {"result": step.get("data")}
            data["collector_auto_started"] = True
            step["data"] = data
    return steps


def install_live_debug_recovery_patch() -> None:
    global _BASE_FIND_WINDOW, _BASE_WINDOW_AS_DICT, _BASE_PREVIEW, _BASE_EXPORT_DIAGNOSTICS, _BASE_RUN_LIVE
    manager = collector.DouyinVisibleCollectorManager
    if getattr(manager, "_aliver_live_debug_recovery_v1", False):
        return

    _BASE_FIND_WINDOW = manager._find_window
    _BASE_WINDOW_AS_DICT = collector.WindowSnapshot.as_dict
    _BASE_PREVIEW = manager.preview
    _BASE_EXPORT_DIAGNOSTICS = manager.export_diagnostics
    _BASE_RUN_LIVE = staged_validation._run_live

    collector.WindowSnapshot.as_dict = _patched_window_as_dict
    manager._find_window = _patched_find_window
    manager.preview = _patched_preview
    manager.export_diagnostics = _patched_export_diagnostics
    # The old patch converted the selected visible window to GA_ROOT. Chromium can
    # return a hidden root there, so keep the original visible top-level HWND and
    # only switch when a better visible, non-cloaked candidate exists.
    wgc_patch._root_hwnd = _preferred_capture_hwnd
    staged_validation._run_live = _run_live_with_auto_start
    manager._aliver_live_debug_recovery_v1 = True
