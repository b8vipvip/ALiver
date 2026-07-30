from __future__ import annotations

import base64
import io
import json
import time
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any

from bridge import douyin_visible_collector as collector
from bridge.douyin_ocr_result_patch import _as_sequence, _safe_float

DIAGNOSTIC_DIR = Path(__file__).resolve().parent / "logs" / "douyin_visible"


def _relative_region_pixels(width: int, height: int, region: dict[str, Any]) -> tuple[int, int, int, int]:
    left = max(0, min(int(width * float(region.get("x", 0.782))), max(0, width - 1)))
    top = max(0, min(int(height * float(region.get("y", 0.405))), max(0, height - 1)))
    region_width = max(40, int(width * float(region.get("width", 0.205))))
    region_height = max(80, int(height * float(region.get("height", 0.555))))
    region_width = min(region_width, max(1, width - left))
    region_height = min(region_height, max(1, height - top))
    return left, top, region_width, region_height


def _image_has_content(image: Any) -> bool:
    try:
        import numpy as np

        array = np.asarray(image)
        if array.size == 0:
            return False
        sample = array[:: max(1, array.shape[0] // 120), :: max(1, array.shape[1] // 160)]
        return float(sample.std()) >= 2.0 and 1.0 < float(sample.mean()) < 254.0
    except Exception:
        return False


def _print_window_capture(hwnd: int, width: int, height: int):
    try:
        import ctypes
        import numpy as np
        import win32gui
        import win32ui
    except ImportError:
        return None

    window_dc = None
    source_dc = None
    memory_dc = None
    bitmap = None
    try:
        window_dc = win32gui.GetWindowDC(hwnd)
        if not window_dc:
            return None
        source_dc = win32ui.CreateDCFromHandle(window_dc)
        memory_dc = source_dc.CreateCompatibleDC()
        bitmap = win32ui.CreateBitmap()
        bitmap.CreateCompatibleBitmap(source_dc, width, height)
        memory_dc.SelectObject(bitmap)
        # PW_RENDERFULLCONTENT asks DWM/Chromium windows for their complete surface.
        rendered = int(ctypes.windll.user32.PrintWindow(hwnd, memory_dc.GetSafeHdc(), 0x00000002))
        if rendered == 0:
            return None
        info = bitmap.GetInfo()
        raw = bitmap.GetBitmapBits(True)
        array = np.frombuffer(raw, dtype=np.uint8)
        array.shape = (int(info["bmHeight"]), int(info["bmWidth"]), 4)
        image = array[:, :, :3][:, :, ::-1].copy()
        return image if _image_has_content(image) else None
    except Exception:
        return None
    finally:
        try:
            if bitmap is not None:
                win32gui.DeleteObject(bitmap.GetHandle())
        except Exception:
            pass
        try:
            if memory_dc is not None:
                memory_dc.DeleteDC()
        except Exception:
            pass
        try:
            if source_dc is not None:
                source_dc.DeleteDC()
        except Exception:
            pass
        try:
            if window_dc:
                win32gui.ReleaseDC(hwnd, window_dc)
        except Exception:
            pass


def _same_root_window(first: int, second: int) -> bool:
    try:
        import win32gui

        root_flag = 2  # GA_ROOT
        return int(win32gui.GetAncestor(first, root_flag) or first) == int(
            win32gui.GetAncestor(second, root_flag) or second
        )
    except Exception:
        return int(first) == int(second)


def _visible_screen_capture(window: Any):
    try:
        import mss
        import numpy as np
        import win32gui
    except ImportError:
        return None

    foreground = int(win32gui.GetForegroundWindow() or 0)
    hwnd = int(window.handle or 0)
    if not foreground or not hwnd or not _same_root_window(foreground, hwnd):
        raise RuntimeError(
            "直播伴侣被其他窗口遮挡，窗口内容捕获失败；为避免把浏览器文字误识别成弹幕，已拒绝桌面截图兜底"
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
    return image if _image_has_content(image) else None


def _capture_target_window(self, window: Any):
    hwnd = int(window.handle or 0)
    image = None
    source = None
    if hwnd:
        image = _print_window_capture(hwnd, int(window.width), int(window.height))
        if image is not None:
            source = "printwindow"
    if image is None:
        image = _visible_screen_capture(window)
        if image is not None:
            source = "screen_visible"
    if image is None:
        raise RuntimeError("无法读取直播伴侣窗口内容，请保持窗口打开、未最小化，并尝试将直播伴侣置于前台")

    region = self._config.get("ocr_region") or collector.DEFAULT_CONFIG["ocr_region"]
    crop_left, crop_top, crop_width, crop_height = _relative_region_pixels(
        int(image.shape[1]), int(image.shape[0]), dict(region)
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
            "window_surface" if source == "printwindow" else "foreground_screen_fallback"
        )
    return image, crop, source, (crop_left, crop_top, crop_width, crop_height)


def _ocr_lines(self, window) -> list[dict[str, Any]]:
    try:
        from rapidocr import RapidOCR
    except ImportError:
        return []

    if self._ocr_engine is None:
        self._ocr_engine = RapidOCR()
    _, crop, _, pixel_region = _capture_target_window(self, window)
    crop_left, crop_top, _, _ = pixel_region
    result = self._ocr_engine(crop)

    rows: list[dict[str, Any]] = []
    if hasattr(result, "txts"):
        texts = _as_sequence(getattr(result, "txts", None))
        scores = _as_sequence(getattr(result, "scores", None))
        boxes = _as_sequence(getattr(result, "boxes", None))
        for index, text in enumerate(texts):
            box = boxes[index] if index < len(boxes) else None
            score = _safe_float(scores[index]) if index < len(scores) else 0.0
            rows.append(
                self._ocr_row(
                    text,
                    score,
                    box,
                    int(window.left) + crop_left,
                    int(window.top) + crop_top,
                )
            )
    else:
        legacy = result[0] if isinstance(result, tuple) and len(result) > 0 else result
        for item in _as_sequence(legacy):
            if not isinstance(item, (list, tuple)) or len(item) < 3:
                continue
            rows.append(
                self._ocr_row(
                    item[1],
                    _safe_float(item[2]),
                    item[0],
                    int(window.left) + crop_left,
                    int(window.top) + crop_top,
                )
            )
    rows.sort(key=lambda row: ((row.get("bbox") or [0, 0])[1], (row.get("bbox") or [0, 0])[0]))
    return rows


def _encode_preview(image: Any, *, fmt: str, quality: int = 70) -> str | None:
    if image is None:
        return None
    try:
        from PIL import Image

        buffer = io.BytesIO()
        pil = Image.fromarray(image)
        kwargs = {"quality": quality, "optimize": True} if fmt.upper() == "JPEG" else {"optimize": True}
        pil.save(buffer, format=fmt, **kwargs)
        mime = "image/jpeg" if fmt.upper() == "JPEG" else "image/png"
        return f"data:{mime};base64,{base64.b64encode(buffer.getvalue()).decode('ascii')}"
    except Exception:
        return None


def preview(self) -> dict[str, Any]:
    window = self._find_window()
    if window is None:
        raise RuntimeError("没有找到抖音直播伴侣窗口")
    _capture_target_window(self, window)
    return {
        "window": window.as_dict(),
        "capture_source": self._state.get("capture_source"),
        "capture_safety": self._state.get("capture_safety"),
        "last_capture_at": self._state.get("last_capture_at"),
        "last_region_pixels": self._state.get("last_region_pixels"),
        "window_image": _encode_preview(getattr(self, "_last_window_image", None), fmt="JPEG", quality=60),
        "region_image": _encode_preview(getattr(self, "_last_region_image", None), fmt="PNG"),
    }


def probe_uia(self) -> dict[str, Any]:
    window = self._find_window()
    if window is None:
        raise RuntimeError("没有找到抖音直播伴侣窗口")
    try:
        from pywinauto import Desktop
    except ImportError as exc:
        raise RuntimeError("缺少 pywinauto，无法运行 UIA 探针") from exc

    root = Desktop(backend="uia").window(handle=int(window.handle or 0))
    root.wait("exists", timeout=2)
    region_left, region_top, region_width, region_height = self._region_rect(window)
    region_right = region_left + region_width
    region_bottom = region_top + region_height
    controls: list[dict[str, Any]] = []
    for control in root.descendants()[:1500]:
        try:
            info = control.element_info
            rect = control.rectangle()
            text = collector.clean_text(control.window_text() or getattr(info, "name", ""))
            if not text:
                continue
            intersects = not (
                rect.right < region_left
                or rect.left > region_right
                or rect.bottom < region_top
                or rect.top > region_bottom
            )
            controls.append(
                {
                    "text": text[:500],
                    "control_type": str(getattr(info, "control_type", "") or ""),
                    "automation_id": str(getattr(info, "automation_id", "") or ""),
                    "class_name": str(getattr(info, "class_name", "") or ""),
                    "bbox": [rect.left, rect.top, rect.right, rect.bottom],
                    "in_ocr_region": intersects,
                }
            )
        except Exception:
            continue
    controls.sort(key=lambda item: (not item["in_ocr_region"], item["bbox"][1], item["bbox"][0]))
    result = {
        "window": window.as_dict(),
        "control_count": len(controls),
        "region_control_count": sum(1 for item in controls if item["in_ocr_region"]),
        "controls": controls[:300],
        "created_at": collector.utc_iso(),
    }
    self._last_uia_probe = result
    with self._lock:
        self._state["uia_probe_count"] = result["control_count"]
        self._state["uia_region_control_count"] = result["region_control_count"]
    return result


def export_diagnostics(self) -> dict[str, Any]:
    window = self._find_window()
    if window is not None:
        try:
            _capture_target_window(self, window)
        except Exception as exc:
            with self._lock:
                self._state["last_error"] = f"diagnostic capture: {type(exc).__name__}: {exc}"
    try:
        probe = probe_uia(self)
    except Exception as exc:
        probe = {"error": f"{type(exc).__name__}: {exc}"}

    DIAGNOSTIC_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    bundle_path = DIAGNOSTIC_DIR / f"douyin-visible-diagnostics-{stamp}.zip"
    status = self.status()
    with zipfile.ZipFile(bundle_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("status.json", json.dumps(status, ensure_ascii=False, indent=2, default=str))
        archive.writestr("uia-probe.json", json.dumps(probe, ensure_ascii=False, indent=2, default=str))
        archive.writestr(
            "recent-lines.json",
            json.dumps(status.get("recent_lines") or [], ensure_ascii=False, indent=2, default=str),
        )
        archive.writestr(
            "recent-events.json",
            json.dumps(status.get("recent_events") or [], ensure_ascii=False, indent=2, default=str),
        )
        archive.writestr(
            "README.txt",
            "ALiver 抖音可视互动采集诊断包\n包含目标窗口截图、OCR 裁剪区、UIA 可访问树和最近识别记录。\n",
        )
        for name, image, fmt in (
            ("window.jpg", getattr(self, "_last_window_image", None), "JPEG"),
            ("ocr-region.png", getattr(self, "_last_region_image", None), "PNG"),
        ):
            data_url = _encode_preview(image, fmt=fmt, quality=65)
            if data_url:
                archive.writestr(name, base64.b64decode(data_url.split(",", 1)[1]))
    return {
        "path": str(bundle_path),
        "size_bytes": bundle_path.stat().st_size,
        "created_at": collector.utc_iso(),
    }


def clear_local_history(self) -> dict[str, Any]:
    with self._lock:
        self._state["recent_lines"].clear()
        self._state["recent_events"].clear()
        for key in (
            "scan_count",
            "raw_line_count",
            "event_count",
            "sent_count",
            "duplicate_count",
        ):
            self._state[key] = 0
        self._state["last_scan_at"] = None
        self._state["last_event_at"] = None
        self._state["last_error"] = None
    self._recent_sent.clear()
    return self.status()


def install_douyin_window_capture_patch() -> None:
    manager = collector.DouyinVisibleCollectorManager
    manager._capture_target_window = _capture_target_window
    manager._ocr_lines = _ocr_lines
    manager.preview = preview
    manager.probe_uia = probe_uia
    manager.export_diagnostics = export_diagnostics
    manager.clear_local_history = clear_local_history
