from __future__ import annotations

from typing import Any

from bridge import douyin_visible_collector as collector


def _as_sequence(value: Any) -> list[Any]:
    """Convert RapidOCR list/tuple/NumPy outputs without boolean evaluation."""
    if value is None:
        return []
    if isinstance(value, (str, bytes)):
        return [value]
    tolist = getattr(value, "tolist", None)
    if callable(tolist):
        try:
            value = tolist()
        except Exception:
            pass
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    try:
        return list(value)
    except TypeError:
        return [value]


def _safe_float(value: Any) -> float:
    if value is None:
        return 0.0
    item = getattr(value, "item", None)
    if callable(item):
        try:
            value = item()
        except Exception:
            pass
    try:
        return float(value)
    except (TypeError, ValueError):
        values = _as_sequence(value)
        if not values:
            return 0.0
        try:
            return float(values[0])
        except (TypeError, ValueError):
            return 0.0


def _ocr_lines(self, window) -> list[dict[str, Any]]:
    try:
        import mss
        import numpy as np
        from rapidocr import RapidOCR
    except ImportError:
        return []

    if self._ocr_engine is None:
        self._ocr_engine = RapidOCR()

    left, top, width, height = self._region_rect(window)
    with mss.mss() as sct:
        shot = sct.grab({"left": left, "top": top, "width": width, "height": height})
    image = np.asarray(shot)[:, :, :3]
    result = self._ocr_engine(image)

    rows: list[dict[str, Any]] = []
    if hasattr(result, "txts"):
        texts = _as_sequence(getattr(result, "txts", None))
        scores = _as_sequence(getattr(result, "scores", None))
        boxes = _as_sequence(getattr(result, "boxes", None))
        for index, text in enumerate(texts):
            box = boxes[index] if index < len(boxes) else None
            score = _safe_float(scores[index]) if index < len(scores) else 0.0
            rows.append(self._ocr_row(text, score, box, left, top))
    else:
        if isinstance(result, tuple):
            legacy = result[0] if len(result) > 0 else None
        else:
            legacy = result
        for item in _as_sequence(legacy):
            if not isinstance(item, (list, tuple)) or len(item) < 3:
                continue
            rows.append(self._ocr_row(item[1], _safe_float(item[2]), item[0], left, top))

    rows.sort(key=lambda row: ((row.get("bbox") or [0, 0])[1], (row.get("bbox") or [0, 0])[0]))
    return rows


def install_douyin_ocr_result_patch() -> None:
    collector.DouyinVisibleCollectorManager._ocr_lines = _ocr_lines
