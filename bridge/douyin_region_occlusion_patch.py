from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from bridge import douyin_visible_collector as collector
from bridge import douyin_window_capture_patch as capture

_BASE_EXPORT_DIAGNOSTICS = capture.export_diagnostics


def _rects_overlap(
    first: tuple[int, int, int, int],
    second: tuple[int, int, int, int],
    *,
    minimum_pixels: int = 2,
) -> bool:
    left = max(first[0], second[0])
    top = max(first[1], second[1])
    right = min(first[2], second[2])
    bottom = min(first[3], second[3])
    return right - left > minimum_pixels and bottom - top > minimum_pixels


def _is_cloaked(hwnd: int) -> bool:
    try:
        import ctypes

        cloaked = ctypes.c_int(0)
        result = ctypes.windll.dwmapi.DwmGetWindowAttribute(
            hwnd,
            14,  # DWMWA_CLOAKED
            ctypes.byref(cloaked),
            ctypes.sizeof(cloaked),
        )
        return result == 0 and cloaked.value != 0
    except Exception:
        return False


def _blocking_windows(
    target_hwnd: int,
    region_rect: tuple[int, int, int, int],
) -> list[dict[str, Any]]:
    try:
        import win32gui
        import win32process
    except ImportError:
        return []

    handles: list[int] = []

    def collect(hwnd: int, output: list[int]) -> bool:
        output.append(int(hwnd))
        return True

    win32gui.EnumWindows(collect, handles)
    target_root = int(win32gui.GetAncestor(target_hwnd, 2) or target_hwnd)  # GA_ROOT
    target_index = next(
        (
            index
            for index, hwnd in enumerate(handles)
            if int(win32gui.GetAncestor(hwnd, 2) or hwnd) == target_root
        ),
        None,
    )
    if target_index is None:
        return []

    blockers: list[dict[str, Any]] = []
    seen_roots: set[int] = set()
    for hwnd in handles[:target_index]:
        try:
            root = int(win32gui.GetAncestor(hwnd, 2) or hwnd)
            if root == target_root or root in seen_roots:
                continue
            seen_roots.add(root)
            if not win32gui.IsWindowVisible(root) or win32gui.IsIconic(root) or _is_cloaked(root):
                continue
            left, top, right, bottom = [int(value) for value in win32gui.GetWindowRect(root)]
            rect = (left, top, right, bottom)
            if right <= left or bottom <= top or not _rects_overlap(region_rect, rect):
                continue
            _, pid = win32process.GetWindowThreadProcessId(root)
            blockers.append(
                {
                    "handle": root,
                    "handle_hex": hex(root),
                    "pid": int(pid),
                    "title": str(win32gui.GetWindowText(root) or ""),
                    "class_name": str(win32gui.GetClassName(root) or ""),
                    "rect": [left, top, right, bottom],
                }
            )
        except Exception:
            continue
    return blockers


def _screen_capture_if_region_clear(
    self: Any,
    window: Any,
    pixel_region: tuple[int, int, int, int],
):
    try:
        import mss
        import numpy as np
        import win32gui
    except ImportError:
        return None

    hwnd = int(window.handle or 0)
    if not hwnd or win32gui.IsIconic(hwnd):
        raise RuntimeError("直播伴侣窗口已最小化，无法使用屏幕截图兜底")

    crop_left, crop_top, crop_width, crop_height = pixel_region
    region_rect = (
        int(window.left) + crop_left,
        int(window.top) + crop_top,
        int(window.left) + crop_left + crop_width,
        int(window.top) + crop_top + crop_height,
    )
    blockers = _blocking_windows(hwnd, region_rect)
    with self._lock:
        self._state["capture_region_screen_rect"] = list(region_rect)
        self._state["capture_occluders"] = blockers
    if blockers:
        labels = [item.get("title") or item.get("class_name") or item.get("handle_hex") for item in blockers[:3]]
        raise RuntimeError(
            "直播伴侣的互动消息 OCR 区域被上层窗口实际覆盖："
            + "、".join(str(item) for item in labels)
            + "；请移开覆盖互动区的窗口"
        )

    with mss.mss() as sct:
        shot = sct.grab(
            {
                "left": int(window.left),
                "top": int(window.top),
                "width": int(window.width),
                "height": int(window.height),
            }
        )
    image = np.asarray(shot)[:, :, :3][:, :, ::-1].copy()
    return image if capture._image_has_content(image) else None


def _capture_target_window(self: Any, window: Any):
    hwnd = int(window.handle or 0)
    region = self._config.get("ocr_region") or collector.DEFAULT_CONFIG["ocr_region"]
    pixel_region = capture._relative_region_pixels(int(window.width), int(window.height), dict(region))

    image = None
    source = None
    if hwnd:
        image = capture._print_window_capture(hwnd, int(window.width), int(window.height))
        if image is not None:
            source = "printwindow"
    if image is None:
        image = _screen_capture_if_region_clear(self, window, pixel_region)
        if image is not None:
            source = "screen_region_clear"
    if image is None:
        raise RuntimeError("无法读取直播伴侣窗口内容，请保持窗口打开且不要最小化")

    crop_left, crop_top, crop_width, crop_height = capture._relative_region_pixels(
        int(image.shape[1]),
        int(image.shape[0]),
        dict(region),
    )
    crop = image[crop_top : crop_top + crop_height, crop_left : crop_left + crop_width].copy()
    if crop.size == 0:
        raise RuntimeError("OCR 区域超出直播伴侣窗口，请重新自动校准")

    self._last_window_image = image
    self._last_region_image = crop
    with self._lock:
        self._state["capture_source"] = source
        self._state["last_capture_at"] = collector.utc_iso()
        self._state["last_capture_size"] = [int(image.shape[1]), int(image.shape[0])]
        self._state["last_region_pixels"] = [crop_left, crop_top, crop_width, crop_height]
        self._state["capture_safety"] = (
            "window_surface" if source == "printwindow" else "ocr_region_unobstructed_screen_fallback"
        )
        self._state["capture_foreground_required"] = False
        if source == "printwindow":
            self._state["capture_occluders"] = []
        self._state["last_error"] = None
    return image, crop, source, (crop_left, crop_top, crop_width, crop_height)


def export_diagnostics(self: Any) -> dict[str, Any]:
    result = dict(_BASE_EXPORT_DIAGNOSTICS(self))
    path = Path(str(result.get("path") or ""))
    with self._lock:
        self._state["last_diagnostic_path"] = str(path)
        self._state["last_diagnostic_folder"] = str(path.parent)
    result["folder"] = str(path.parent)
    return result


def open_diagnostics_folder(self: Any, path: str | None = None) -> dict[str, Any]:
    raw_path = str(path or self._state.get("last_diagnostic_path") or "").strip()
    candidate = Path(raw_path) if raw_path else capture.DIAGNOSTIC_DIR
    folder = candidate if candidate.is_dir() else candidate.parent
    if not folder.exists():
        folder = capture.DIAGNOSTIC_DIR
        folder.mkdir(parents=True, exist_ok=True)
    if os.name != "nt" or not hasattr(os, "startfile"):
        raise RuntimeError("打开文件夹功能仅支持 Windows Bridge")
    os.startfile(str(folder))  # type: ignore[attr-defined]
    return {"opened": True, "folder": str(folder), "path": raw_path or str(folder)}


def install_douyin_region_occlusion_patch() -> None:
    manager = collector.DouyinVisibleCollectorManager
    if getattr(manager, "_aliver_region_occlusion_patch", False):
        return
    capture._capture_target_window = _capture_target_window
    manager._capture_target_window = _capture_target_window
    manager.export_diagnostics = export_diagnostics
    manager.open_diagnostics_folder = open_diagnostics_folder
    manager._aliver_region_occlusion_patch = True
