from __future__ import annotations

import time
from typing import Any

from bridge import douyin_visible_collector as collector

_original_parse = collector.parse_visible_lines


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
    collector.DouyinVisibleCollectorManager._collect_lines = _collect_lines
