from __future__ import annotations

import hashlib
import importlib.util
import json
import re
import threading
import time
import urllib.error
import urllib.request
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = BASE_DIR / "douyin_visible_collector.local.json"

DEFAULT_CONFIG: dict[str, Any] = {
    "enabled": False,
    "extension_id": "",
    "collector_id": "douyin-visible-ui",
    "mode": "hybrid",
    "window_title_pattern": ".*直播伴侣.*",
    "scan_interval_seconds": 1.0,
    "heartbeat_seconds": 5.0,
    "confidence_threshold": 0.72,
    "uia_fallback_seconds": 4.0,
    "ocr_region": {"x": 0.782, "y": 0.405, "width": 0.205, "height": 0.555},
    "capture_comments": True,
    "capture_gifts": True,
    "capture_follows": True,
    "capture_shares": True,
    "capture_likes": False,
    "capture_join_notices": False,
}

IGNORED_TEXTS = {
    "互动消息",
    "在线观众榜",
    "展示本场直播的礼物消息",
    "展示本场观众榜单",
    "说点什么...",
    "发送",
    "主播中心",
    "互动玩法",
    "活动",
    "主播任务",
}

FOLLOW_RE = re.compile(r"^(?P<user>.+?)\s*(?:关注了你|关注了直播间)$")
GIFT_RE = re.compile(
    r"^(?P<user>.+?)\s*(?:送出(?:了)?|送给主播)\s*(?P<gift>.+?)(?:\s*[×xX*]\s*(?P<count>\d+))?$"
)
FANS_RE = re.compile(r"^(?P<user>.+?)\s*(?P<content>加入了粉丝团|粉丝团升级[^\s]*)$")
SHARE_RE = re.compile(r"^(?P<user>.+?)\s*(?:分享了直播间|分享了本场直播)$")
LIKE_RE = re.compile(r"^(?P<user>.+?)\s*(?P<content>点赞(?:了直播间|\s*[×xX*]\s*\d+)?)$")
JOIN_RE = re.compile(r"^(?P<user>.+?)\s*(?:来了|进入了?直播间)$")
COMMENT_RE = re.compile(r"^(?P<user>[^\s:：]{1,28})[\s:：]+(?P<content>.{1,500})$")


def utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def clean_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip())


def _event_id(source: str, text: str, event_type: str, user_name: str, content: str) -> str:
    raw = f"{source}|{text}|{event_type}|{user_name}|{content}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _make_event(
    *,
    source: str,
    confidence: float,
    raw_text: str,
    event_type: str,
    user_name: str,
    content: str,
    bbox: list[float] | None = None,
    confidence_threshold: float = 0.72,
) -> dict[str, Any]:
    return {
        "event_id": _event_id(source, raw_text, event_type, user_name, content),
        "event_type": event_type,
        "user_name": user_name[:160] or "观众",
        "content": content[:2000],
        "source": source,
        "confidence": round(float(confidence), 4),
        "confidence_threshold": round(float(confidence_threshold), 4),
        "raw_text": raw_text[:2000],
        "bbox": bbox,
        "observed_at": utc_iso(),
    }


def parse_visible_lines(
    lines: list[dict[str, Any]],
    *,
    confidence_threshold: float = 0.72,
    capture_join_notices: bool = False,
) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    seen: set[str] = set()
    for line in lines:
        text = clean_text(line.get("text"))
        if not text or text in IGNORED_TEXTS or len(text) > 600:
            continue
        if any(label in text for label in ("粉丝互动任务", "直播流量站", "展示本场")):
            continue
        source = str(line.get("source") or "unknown")
        confidence = float(line.get("confidence") or (1.0 if source == "uia" else 0.0))
        bbox = line.get("bbox") if isinstance(line.get("bbox"), list) else None

        event_type: str | None = None
        user = "观众"
        content = ""
        match = FOLLOW_RE.match(text)
        if match:
            event_type, user, content = "follow", match.group("user"), "关注了直播间"
        else:
            match = GIFT_RE.match(text)
            if match:
                count = int(match.group("count") or 1)
                event_type, user = "gift", match.group("user")
                content = f"送出了{match.group('gift').strip()} × {max(1, count)}"
            else:
                match = FANS_RE.match(text)
                if match:
                    event_type, user, content = "system", match.group("user"), match.group("content")
                else:
                    match = SHARE_RE.match(text)
                    if match:
                        event_type, user, content = "share", match.group("user"), "分享了直播间"
                    else:
                        match = LIKE_RE.match(text)
                        if match:
                            event_type, user, content = "like", match.group("user"), match.group("content")
                        else:
                            match = JOIN_RE.match(text)
                            if match and capture_join_notices:
                                event_type, user, content = "system", match.group("user"), "进入了直播间"
                            elif match:
                                continue
                            else:
                                match = COMMENT_RE.match(text)
                                if match:
                                    user = match.group("user")
                                    content = match.group("content").strip()
                                    if content in {"关注了你", "来了", "进入直播间"}:
                                        continue
                                    event_type = "comment"

        if not event_type or not content:
            continue
        event = _make_event(
            source=source,
            confidence=confidence,
            raw_text=text,
            event_type=event_type,
            user_name=clean_text(user),
            content=clean_text(content),
            bbox=bbox,
            confidence_threshold=confidence_threshold,
        )
        if event["event_id"] in seen:
            continue
        seen.add(event["event_id"])
        events.append(event)
    return events


@dataclass
class WindowSnapshot:
    title: str
    left: int
    top: int
    width: int
    height: int
    handle: int | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "left": self.left,
            "top": self.top,
            "width": self.width,
            "height": self.height,
            "handle": self.handle,
        }


class DouyinVisibleCollectorManager:
    def __init__(self, agent: Any) -> None:
        self.agent = agent
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._ocr_engine: Any = None
        self._config = self._load_config()
        self._state: dict[str, Any] = {
            "status": "stopped",
            "connected": False,
            "active_source": None,
            "window": None,
            "uia_available": importlib.util.find_spec("pywinauto") is not None,
            "ocr_available": all(
                importlib.util.find_spec(name) is not None for name in ("mss", "rapidocr", "numpy")
            ),
            "last_scan_at": None,
            "last_event_at": None,
            "last_error": None,
            "scan_count": 0,
            "raw_line_count": 0,
            "event_count": 0,
            "sent_count": 0,
            "duplicate_count": 0,
            "recent_lines": deque(maxlen=30),
            "recent_events": deque(maxlen=30),
        }
        self._recent_sent: dict[str, float] = {}
        self._last_uia_data_at = 0.0
        self._last_heartbeat_at = 0.0

    def _load_config(self) -> dict[str, Any]:
        if not CONFIG_PATH.exists():
            return dict(DEFAULT_CONFIG)
        try:
            value = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return dict(DEFAULT_CONFIG)
        merged = dict(DEFAULT_CONFIG)
        if isinstance(value, dict):
            merged.update(value)
            merged["ocr_region"] = {**DEFAULT_CONFIG["ocr_region"], **dict(value.get("ocr_region") or {})}
        return merged

    def _save_config(self) -> None:
        CONFIG_PATH.write_text(json.dumps(self._config, ensure_ascii=False, indent=2), encoding="utf-8")

    def status(self) -> dict[str, Any]:
        with self._lock:
            value = {key: item for key, item in self._state.items() if key not in {"recent_lines", "recent_events"}}
            value["recent_lines"] = list(self._state["recent_lines"])
            value["recent_events"] = list(self._state["recent_events"])
            value["config"] = dict(self._config)
            value["running"] = bool(self._thread and self._thread.is_alive())
            return value

    def update_config(self, values: dict[str, Any], *, persist: bool = True) -> dict[str, Any]:
        allowed = set(DEFAULT_CONFIG)
        with self._lock:
            for key, value in values.items():
                if key not in allowed:
                    continue
                if key == "ocr_region" and isinstance(value, dict):
                    region = {**self._config.get("ocr_region", {}), **value}
                    self._config[key] = {
                        "x": max(0.0, min(float(region.get("x", 0.78)), 1.0)),
                        "y": max(0.0, min(float(region.get("y", 0.40)), 1.0)),
                        "width": max(0.02, min(float(region.get("width", 0.21)), 1.0)),
                        "height": max(0.02, min(float(region.get("height", 0.56)), 1.0)),
                    }
                else:
                    self._config[key] = value
            mode = str(self._config.get("mode") or "hybrid").lower()
            self._config["mode"] = mode if mode in {"hybrid", "uia", "ocr"} else "hybrid"
            self._config["scan_interval_seconds"] = max(
                0.4, min(float(self._config.get("scan_interval_seconds") or 1.0), 10.0)
            )
            self._config["confidence_threshold"] = max(
                0.3, min(float(self._config.get("confidence_threshold") or 0.72), 0.99)
            )
            if persist:
                self._save_config()
        return self.status()

    def start(self, values: dict[str, Any] | None = None) -> dict[str, Any]:
        if values:
            self.update_config(values)
        if not str(self._config.get("extension_id") or "").strip():
            raise ValueError("请先选择专业总导演对应的 Chrome 扩展")
        with self._lock:
            self._config["enabled"] = True
            self._save_config()
            if self._thread and self._thread.is_alive():
                return self.status()
            self._stop.clear()
            self._state["status"] = "starting"
            self._state["last_error"] = None
            self._thread = threading.Thread(target=self._loop, name="douyin-visible-collector", daemon=True)
            self._thread.start()
        return self.status()

    def autostart(self) -> dict[str, Any]:
        if bool(self._config.get("enabled")) and str(self._config.get("extension_id") or "").strip():
            return self.start()
        return self.status()

    def stop(self) -> dict[str, Any]:
        with self._lock:
            self._config["enabled"] = False
            self._save_config()
            self._stop.set()
            thread = self._thread
        if thread and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=5)
        with self._lock:
            self._state["status"] = "stopped"
            self._state["connected"] = False
        self._heartbeat(connected=False)
        return self.status()

    def calibrate_default(self) -> dict[str, Any]:
        window = self._find_window()
        if window is None:
            raise RuntimeError("没有找到抖音直播伴侣窗口")
        # The current Live Companion layout places 互动消息 in the lower-right pane.
        self.update_config(
            {
                "window_title_pattern": ".*直播伴侣.*",
                "ocr_region": {"x": 0.782, "y": 0.405, "width": 0.205, "height": 0.555},
            }
        )
        with self._lock:
            self._state["window"] = window.as_dict()
        return self.status()

    def scan_once(self) -> dict[str, Any]:
        window = self._find_window()
        if window is None:
            raise RuntimeError("没有找到抖音直播伴侣窗口，请保持直播伴侣打开且不要最小化")
        lines = self._collect_lines(window)
        events = parse_visible_lines(
            lines,
            confidence_threshold=float(self._config.get("confidence_threshold") or 0.72),
            capture_join_notices=bool(self._config.get("capture_join_notices", False)),
        )
        events = [event for event in events if self._event_enabled(event)]
        new_events = self._dedupe(events)
        self._record_scan(window, lines, new_events)
        if new_events:
            self._send_events(new_events)
        return self.status()

    def _event_enabled(self, event: dict[str, Any]) -> bool:
        key = {
            "comment": "capture_comments",
            "gift": "capture_gifts",
            "follow": "capture_follows",
            "share": "capture_shares",
            "like": "capture_likes",
            "system": "capture_join_notices",
        }.get(str(event.get("event_type")))
        return bool(self._config.get(key, True)) if key else True

    def _dedupe(self, events: list[dict[str, Any]]) -> list[dict[str, Any]]:
        now = time.time()
        expiry = now - 180.0
        for key, seen_at in list(self._recent_sent.items()):
            if seen_at < expiry:
                self._recent_sent.pop(key, None)
        values: list[dict[str, Any]] = []
        for event in events:
            event_id = str(event.get("event_id") or "")
            if not event_id or event_id in self._recent_sent:
                with self._lock:
                    self._state["duplicate_count"] += 1
                continue
            self._recent_sent[event_id] = now
            values.append(event)
        return values

    def _loop(self) -> None:
        with self._lock:
            self._state["status"] = "running"
        while not self._stop.is_set():
            try:
                self.scan_once()
                with self._lock:
                    self._state["connected"] = True
                    self._state["last_error"] = None
            except Exception as exc:  # noqa: BLE001 - collector must survive transient UI changes
                with self._lock:
                    self._state["connected"] = False
                    self._state["last_error"] = f"{type(exc).__name__}: {exc}"
                self._heartbeat(connected=False, error=str(exc))
            if time.time() - self._last_heartbeat_at >= float(self._config.get("heartbeat_seconds") or 5):
                self._heartbeat(connected=True)
            self._stop.wait(float(self._config.get("scan_interval_seconds") or 1.0))
        with self._lock:
            self._state["status"] = "stopped"
            self._state["connected"] = False

    def _find_window(self) -> WindowSnapshot | None:
        try:
            import ctypes
            import win32gui
        except ImportError as exc:
            raise RuntimeError("缺少 pywin32，无法定位抖音直播伴侣窗口") from exc
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception:
            pass
        pattern = re.compile(str(self._config.get("window_title_pattern") or ".*直播伴侣.*"), re.I)
        candidates: list[WindowSnapshot] = []

        def callback(hwnd, _):
            if not win32gui.IsWindowVisible(hwnd) or win32gui.IsIconic(hwnd):
                return
            title = win32gui.GetWindowText(hwnd)
            if not title or not pattern.search(title):
                return
            left, top, right, bottom = win32gui.GetWindowRect(hwnd)
            width, height = max(0, right - left), max(0, bottom - top)
            if width >= 600 and height >= 400:
                candidates.append(WindowSnapshot(title, left, top, width, height, int(hwnd)))

        win32gui.EnumWindows(callback, None)
        return max(candidates, key=lambda item: item.width * item.height) if candidates else None

    def _region_rect(self, window: WindowSnapshot) -> tuple[int, int, int, int]:
        region = self._config.get("ocr_region") or DEFAULT_CONFIG["ocr_region"]
        left = window.left + int(window.width * float(region.get("x", 0.782)))
        top = window.top + int(window.height * float(region.get("y", 0.405)))
        width = max(40, int(window.width * float(region.get("width", 0.205))))
        height = max(80, int(window.height * float(region.get("height", 0.555))))
        return left, top, width, height

    def _collect_lines(self, window: WindowSnapshot) -> list[dict[str, Any]]:
        mode = str(self._config.get("mode") or "hybrid")
        values: list[dict[str, Any]] = []
        if mode in {"hybrid", "uia"} and self._state.get("uia_available"):
            uia = self._uia_lines(window)
            if uia:
                values.extend(uia)
                self._last_uia_data_at = time.time()
                with self._lock:
                    self._state["active_source"] = "uia"
        should_ocr = mode == "ocr" or (
            mode == "hybrid" and time.time() - self._last_uia_data_at >= float(self._config.get("uia_fallback_seconds") or 4)
        )
        if should_ocr and self._state.get("ocr_available"):
            ocr = self._ocr_lines(window)
            if ocr:
                values.extend(ocr)
                with self._lock:
                    self._state["active_source"] = "ocr" if not values[:-len(ocr)] else "hybrid"
        if mode == "uia" and not self._state.get("uia_available"):
            raise RuntimeError("UIA 依赖不可用，请安装 pywinauto")
        if mode == "ocr" and not self._state.get("ocr_available"):
            raise RuntimeError("OCR 依赖不可用，请安装 mss、rapidocr、onnxruntime")
        return values

    def _uia_lines(self, window: WindowSnapshot) -> list[dict[str, Any]]:
        try:
            from pywinauto import Desktop
        except ImportError:
            return []
        left, top, width, height = self._region_rect(window)
        right, bottom = left + width, top + height
        desktop = Desktop(backend="uia")
        wrappers = desktop.windows(title_re=str(self._config.get("window_title_pattern") or ".*直播伴侣.*"), visible_only=True)
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
                text = clean_text(control.window_text() or getattr(control.element_info, "name", ""))
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

    def _ocr_lines(self, window: WindowSnapshot) -> list[dict[str, Any]]:
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
            texts = list(result.txts or [])
            scores = list(result.scores or [])
            boxes = list(result.boxes or [])
            for index, text in enumerate(texts):
                box = boxes[index] if index < len(boxes) else None
                score = scores[index] if index < len(scores) else 0.0
                rows.append(self._ocr_row(text, score, box, left, top))
        else:
            legacy = result[0] if isinstance(result, tuple) and result else result
            for item in legacy or []:
                if not isinstance(item, (list, tuple)) or len(item) < 3:
                    continue
                rows.append(self._ocr_row(item[1], item[2], item[0], left, top))
        rows.sort(key=lambda item: ((item.get("bbox") or [0, 0])[1], (item.get("bbox") or [0, 0])[0]))
        return rows

    @staticmethod
    def _ocr_row(text: Any, score: Any, box: Any, offset_x: int, offset_y: int) -> dict[str, Any]:
        bbox = None
        if box is not None:
            try:
                xs = [float(point[0]) + offset_x for point in box]
                ys = [float(point[1]) + offset_y for point in box]
                bbox = [min(xs), min(ys), max(xs), max(ys)]
            except Exception:
                bbox = None
        return {"text": clean_text(text), "source": "ocr", "confidence": float(score or 0.0), "bbox": bbox}

    def _record_scan(
        self,
        window: WindowSnapshot,
        lines: list[dict[str, Any]],
        events: list[dict[str, Any]],
    ) -> None:
        with self._lock:
            self._state["window"] = window.as_dict()
            self._state["last_scan_at"] = utc_iso()
            self._state["scan_count"] += 1
            self._state["raw_line_count"] += len(lines)
            self._state["event_count"] += len(events)
            for line in lines[-12:]:
                self._state["recent_lines"].append(line)
            for event in events:
                self._state["recent_events"].append(event)
            if events:
                self._state["last_event_at"] = utc_iso()

    def _bridge_credentials(self) -> tuple[str, str]:
        bridge_id = str(self.agent.state.get("bridge_id") or "")
        token = str(self.agent.state.get("token") or "")
        if not bridge_id or not token:
            raise RuntimeError("Bridge 尚未完成注册")
        return bridge_id, token

    def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        bridge_id, token = self._bridge_credentials()
        payload = {**payload, "bridge_id": bridge_id}
        request = urllib.request.Request(
            self.agent.server_url.rstrip("/") + path,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json", "X-Bridge-Token": token},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"ALiver HTTP {exc.code}: {detail}") from exc

    def _heartbeat(self, *, connected: bool, error: str | None = None) -> None:
        extension_id = str(self._config.get("extension_id") or "")
        if not extension_id:
            return
        try:
            window = self._state.get("window") or {}
            self._post(
                "/api/douyin-live/bridge/heartbeat",
                {
                    "extension_id": extension_id,
                    "collector_id": self._config.get("collector_id") or "douyin-visible-ui",
                    "connected": connected,
                    "mode": self._config.get("mode") or "hybrid",
                    "window_title": window.get("title"),
                    "uia_available": self._state.get("uia_available"),
                    "ocr_available": self._state.get("ocr_available"),
                    "active_source": self._state.get("active_source"),
                    "error": error or self._state.get("last_error"),
                },
            )
            self._last_heartbeat_at = time.time()
        except Exception as exc:
            with self._lock:
                self._state["last_error"] = f"heartbeat: {type(exc).__name__}: {exc}"

    def _send_events(self, events: list[dict[str, Any]]) -> None:
        result = self._post(
            "/api/douyin-live/bridge/ingest",
            {
                "extension_id": self._config["extension_id"],
                "collector_id": self._config.get("collector_id") or "douyin-visible-ui",
                "events": events,
                "metadata": {
                    "mode": self._config.get("mode") or "hybrid",
                    "active_source": self._state.get("active_source"),
                    "window_title": (self._state.get("window") or {}).get("title"),
                    "uia_available": self._state.get("uia_available"),
                    "ocr_available": self._state.get("ocr_available"),
                    "ocr_region": self._config.get("ocr_region"),
                },
            },
        )
        with self._lock:
            self._state["sent_count"] += int(result.get("accepted") or 0)
