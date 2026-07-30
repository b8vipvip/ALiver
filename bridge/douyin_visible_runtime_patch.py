from __future__ import annotations

import ctypes
import re
import time
from pathlib import Path
from typing import Any

from bridge import douyin_visible_collector as collector

_original_parse = collector.parse_visible_lines

# Douyin Live Companion is an Electron-style multi-process application. Some
# versions expose the visible title to PowerShell but not reliably through the
# narrow title-only EnumWindows filter used by the first collector version.
# Extend the persisted configuration before BridgeAgent creates the manager.
collector.DEFAULT_CONFIG.setdefault("process_name_pattern", r"^直播伴侣(?:\.exe)?$")
collector.DEFAULT_CONFIG.setdefault("process_path_pattern", r"webcast_mate|直播伴侣\.exe$")
collector.DEFAULT_CONFIG.setdefault("preferred_hwnd", "")


def _visual_rows(lines: list[dict[str, Any]]) -> list[dict[str, Any]]:
    positioned: list[dict[str, Any]] = []
    for item in lines:
        if str(item.get("source") or "") != "ocr":
            continue
        bbox = item.get("bbox")
        if not isinstance(bbox, list) or len(bbox) != 4:
            continue
        left, top, right, bottom = [float(value) for value in bbox]
        positioned.append(
            {
                **item,
                "_left": left,
                "_center_y": (top + bottom) / 2,
                "_height": max(1.0, bottom - top),
            }
        )
    positioned.sort(key=lambda item: (item["_center_y"], item["_left"]))

    groups: list[list[dict[str, Any]]] = []
    for item in positioned:
        target = None
        for group in reversed(groups[-4:]):
            center = sum(row["_center_y"] for row in group) / len(group)
            height = max(row["_height"] for row in group)
            if abs(item["_center_y"] - center) <= max(10.0, height * 0.75):
                target = group
                break
        if target is None:
            groups.append([item])
        else:
            target.append(item)

    merged: list[dict[str, Any]] = []
    for group in groups:
        if len(group) < 2:
            continue
        group.sort(key=lambda item: item["_left"])
        text = collector.clean_text(" ".join(str(item.get("text") or "") for item in group))
        if not text:
            continue
        boxes = [item["bbox"] for item in group]
        merged.append(
            {
                "text": text,
                "source": "ocr",
                "confidence": min(float(item.get("confidence") or 0.0) for item in group),
                "bbox": [
                    min(float(box[0]) for box in boxes),
                    min(float(box[1]) for box in boxes),
                    max(float(box[2]) for box in boxes),
                    max(float(box[3]) for box in boxes),
                ],
            }
        )
    return merged


def parse_visible_lines(
    lines: list[dict[str, Any]],
    *,
    confidence_threshold: float = 0.72,
    capture_join_notices: bool = False,
) -> list[dict[str, Any]]:
    expanded = list(lines)
    expanded.extend(_visual_rows(lines))
    return _original_parse(
        expanded,
        confidence_threshold=confidence_threshold,
        capture_join_notices=capture_join_notices,
    )


def _regex_matches(pattern: str, value: str) -> bool:
    if not pattern or not value:
        return False
    try:
        return re.search(pattern, value, re.I) is not None
    except re.error:
        return False


def candidate_score(
    *,
    title: str,
    process_name: str,
    process_path: str,
    class_name: str,
    visible: bool,
    iconic: bool,
    width: int,
    height: int,
    title_pattern: str,
    process_name_pattern: str,
    process_path_pattern: str,
) -> int | None:
    """Score a top-level window without requiring its title to be readable."""
    title_match = _regex_matches(title_pattern, title)
    process_match = _regex_matches(process_name_pattern, process_name)
    path_match = _regex_matches(process_path_pattern, process_path)
    known_title = "直播伴侣" in title
    known_process = process_name.lower() in {"直播伴侣", "直播伴侣.exe"}
    known_path = "webcast_mate" in process_path.lower()
    if not any((title_match, process_match, path_match, known_title, known_process, known_path)):
        return None

    score = 0
    if title_match:
        score += 100
    if known_title:
        score += 60
    if process_match:
        score += 150
    if known_process:
        score += 80
    if path_match:
        score += 130
    if known_path:
        score += 70
    if visible:
        score += 25
    if not iconic:
        score += 20
    if width >= 600 and height >= 400:
        score += 30
    if width >= 1000 and height >= 600:
        score += 15
    if class_name:
        score += 2
    return score


def _query_process_path(pid: int) -> str:
    """Read an executable path with PROCESS_QUERY_LIMITED_INFORMATION."""
    if pid <= 0:
        return ""
    try:
        from ctypes import wintypes

        process_query_limited_information = 0x1000
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(process_query_limited_information, False, int(pid))
        if not handle:
            return ""
        try:
            size = wintypes.DWORD(32768)
            buffer = ctypes.create_unicode_buffer(size.value)
            if kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
                return buffer.value
        finally:
            kernel32.CloseHandle(handle)
    except Exception:
        return ""
    return ""


def _parse_preferred_hwnd(value: Any) -> int | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return int(text, 0)
    except (TypeError, ValueError):
        return None


def _find_window(self):
    try:
        import win32gui
        import win32process
    except ImportError as exc:
        raise RuntimeError("缺少 pywin32，无法定位抖音直播伴侣窗口") from exc

    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        pass

    title_pattern = str(self._config.get("window_title_pattern") or r".*直播伴侣.*")
    process_name_pattern = str(self._config.get("process_name_pattern") or r"^直播伴侣(?:\.exe)?$")
    process_path_pattern = str(self._config.get("process_path_pattern") or r"webcast_mate|直播伴侣\.exe$")
    preferred_hwnd = _parse_preferred_hwnd(self._config.get("preferred_hwnd"))
    candidates: list[dict[str, Any]] = []

    def inspect(hwnd: int) -> dict[str, Any] | None:
        try:
            if not win32gui.IsWindow(hwnd):
                return None
            title = collector.clean_text(win32gui.GetWindowText(hwnd))
            left, top, right, bottom = win32gui.GetWindowRect(hwnd)
            width, height = max(0, right - left), max(0, bottom - top)
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
            process_path = _query_process_path(int(pid))
            process_name = Path(process_path).name if process_path else ""
            class_name = collector.clean_text(win32gui.GetClassName(hwnd))
            visible = bool(win32gui.IsWindowVisible(hwnd))
            iconic = bool(win32gui.IsIconic(hwnd))
        except Exception:
            return None
        score = candidate_score(
            title=title,
            process_name=process_name,
            process_path=process_path,
            class_name=class_name,
            visible=visible,
            iconic=iconic,
            width=width,
            height=height,
            title_pattern=title_pattern,
            process_name_pattern=process_name_pattern,
            process_path_pattern=process_path_pattern,
        )
        item = {
            "handle": int(hwnd),
            "handle_hex": f"0x{int(hwnd):X}",
            "pid": int(pid),
            "title": title,
            "process_name": process_name,
            "process_path": process_path,
            "class_name": class_name,
            "visible": visible,
            "iconic": iconic,
            "left": left,
            "top": top,
            "width": width,
            "height": height,
            "score": score,
        }
        candidates.append(item)
        return item if score is not None else None

    # Fast path for an explicitly pinned HWND and for the exact title exposed by
    # current Live Companion builds.
    selected: dict[str, Any] | None = None
    if preferred_hwnd:
        selected = inspect(preferred_hwnd)
    if selected is None:
        try:
            exact_hwnd = int(win32gui.FindWindow(None, "直播伴侣") or 0)
        except Exception:
            exact_hwnd = 0
        if exact_hwnd:
            selected = inspect(exact_hwnd)

    matches: list[dict[str, Any]] = []

    def callback(hwnd, _):
        item = inspect(int(hwnd))
        if item is not None:
            matches.append(item)

    if selected is None:
        win32gui.EnumWindows(callback, None)
        if matches:
            selected = max(
                matches,
                key=lambda item: (
                    int(item.get("score") or 0),
                    int(bool(item.get("visible"))),
                    int(not bool(item.get("iconic"))),
                    int(item.get("width") or 0) * int(item.get("height") or 0),
                ),
            )

    candidates.sort(
        key=lambda item: (
            int(item.get("score") or -1),
            int(item.get("width") or 0) * int(item.get("height") or 0),
        ),
        reverse=True,
    )
    with self._lock:
        self._state["window_candidates"] = candidates[:20]
        self._state["window_match_method"] = (
            "preferred_hwnd"
            if selected and preferred_hwnd and int(selected["handle"]) == preferred_hwnd
            else "exact_title_or_process_fallback"
            if selected
            else None
        )

    if selected is None:
        return None
    return collector.WindowSnapshot(
        str(selected.get("title") or "直播伴侣"),
        int(selected.get("left") or 0),
        int(selected.get("top") or 0),
        int(selected.get("width") or 0),
        int(selected.get("height") or 0),
        int(selected.get("handle") or 0),
    )


def _uia_lines(self, window):
    try:
        from pywinauto import Desktop
    except ImportError:
        return []
    left, top, width, height = self._region_rect(window)
    right, bottom = left + width, top + height
    desktop = Desktop(backend="uia")
    root = None
    if window.handle:
        try:
            root = desktop.window(handle=int(window.handle))
            root.wait("exists", timeout=1)
        except Exception:
            root = None
    if root is None:
        wrappers = desktop.windows(
            title_re=str(self._config.get("window_title_pattern") or r".*直播伴侣.*"),
            visible_only=False,
        )
        if not wrappers:
            return []
        root = max(wrappers, key=lambda item: item.rectangle().width() * item.rectangle().height())

    values: list[dict[str, Any]] = []
    seen: set[str] = set()
    for control in root.descendants():
        try:
            rect = control.rectangle()
            if rect.right < left or rect.left > right or rect.bottom < top or rect.top > bottom:
                continue
            text = collector.clean_text(control.window_text() or getattr(control.element_info, "name", ""))
        except Exception:
            continue
        if not text or text in seen:
            continue
        seen.add(text)
        values.append(
            {
                "text": text,
                "source": "uia",
                "confidence": 1.0,
                "bbox": [rect.left, rect.top, rect.right, rect.bottom],
            }
        )
    return values


def _collect_lines(self, window):
    mode = str(self._config.get("mode") or "hybrid")
    values: list[dict[str, Any]] = []
    uia_has_events = False
    if mode in {"hybrid", "uia"} and self._state.get("uia_available"):
        uia = self._uia_lines(window)
        if uia:
            values.extend(uia)
            uia_has_events = bool(
                parse_visible_lines(
                    uia,
                    confidence_threshold=float(self._config.get("confidence_threshold") or 0.72),
                    capture_join_notices=bool(self._config.get("capture_join_notices", False)),
                )
            )
            if uia_has_events:
                self._last_uia_data_at = time.time()
                with self._lock:
                    self._state["active_source"] = "uia"

    should_ocr = mode == "ocr" or (
        mode == "hybrid"
        and (
            not uia_has_events
            or time.time() - self._last_uia_data_at >= float(self._config.get("uia_fallback_seconds") or 4)
        )
    )
    if should_ocr and self._state.get("ocr_available"):
        ocr = self._ocr_lines(window)
        if ocr:
            had_uia = bool(values)
            values.extend(ocr)
            with self._lock:
                self._state["active_source"] = "hybrid" if had_uia else "ocr"
    if mode == "uia" and not self._state.get("uia_available"):
        raise RuntimeError("UIA 依赖不可用，请安装 pywinauto")
    if mode == "ocr" and not self._state.get("ocr_available"):
        raise RuntimeError("OCR 依赖不可用，请安装 mss、rapidocr、onnxruntime")
    return values


def install_visible_collector_runtime_patch() -> None:
    collector.parse_visible_lines = parse_visible_lines
    collector.DouyinVisibleCollectorManager._find_window = _find_window
    collector.DouyinVisibleCollectorManager._uia_lines = _uia_lines
    collector.DouyinVisibleCollectorManager._collect_lines = _collect_lines
