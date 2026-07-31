from __future__ import annotations

import asyncio
import json
import shutil
import threading
import time
import zipfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import __version__
from app.config import get_settings
from app.db import SessionLocal
from app.json_utils import loads
from app.models import (
    AudienceEvent,
    AutoDirectorConfig,
    AutoDirectorRun,
    AvatarSession,
    BridgeAgent,
    BrowserExtension,
    DirectorCommand,
    DirectorDecision,
    EventLog,
    ProviderConfig,
)
from app.pro_director_service import RUNNING_STATUSES
from app.security import decrypt_json
from app.voice_models import VoiceProfile

LIVE_RUN_ROOT = Path("data") / "live_runs"
SETTINGS_PATH = LIVE_RUN_ROOT / "settings.json"


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso(value: datetime | None = None) -> str:
    return (value or utcnow()).isoformat()


def json_safe(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [json_safe(item) for item in value]
    return value


def redact(value: Any, key: str = "") -> Any:
    lowered = key.lower()
    if any(token in lowered for token in ("password", "secret", "token", "api_key", "authorization")):
        return "***REDACTED***"
    if isinstance(value, dict):
        return {str(k): redact(v, str(k)) for k, v in value.items()}
    if isinstance(value, list):
        return [redact(item) for item in value]
    return json_safe(value)


def _write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(redact(value), ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def _append_jsonl(path: Path, value: Any) -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(redact(value), ensure_ascii=False, default=str) + "\n")


def _row_fields(row: Any, fields: tuple[str, ...]) -> dict[str, Any]:
    return {field: json_safe(getattr(row, field, None)) for field in fields}


class LiveRunRecorder:
    def __init__(self, root: Path = LIVE_RUN_ROOT) -> None:
        self.root = root
        self._lock = threading.RLock()
        self._state: dict[str, Any] = {
            "active": False,
            "run_id": None,
            "title": None,
            "source": None,
            "started_at": None,
            "stopped_at": None,
            "path": None,
            "bundle_path": None,
            "last_sample_at": None,
            "last_error": None,
            "record_count": 0,
            "include_audience_text": True,
        }
        self._seen: dict[str, set[str]] = {
            "audience": set(),
            "decision": set(),
            "log": set(),
        }
        self._command_versions: dict[str, str] = {}
        self._last_metric_monotonic = 0.0
        self._settings = self._load_settings()

    def _load_settings(self) -> dict[str, Any]:
        defaults = {
            "auto_start_on_director": True,
            "include_audience_text": True,
            "sample_interval_seconds": 2.0,
            "metric_interval_seconds": 5.0,
        }
        try:
            if SETTINGS_PATH.exists():
                value = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
                if isinstance(value, dict):
                    defaults.update(value)
        except (OSError, json.JSONDecodeError):
            pass
        return defaults

    def settings(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._settings)

    def update_settings(self, values: dict[str, Any]) -> dict[str, Any]:
        allowed = {
            "auto_start_on_director",
            "include_audience_text",
            "sample_interval_seconds",
            "metric_interval_seconds",
        }
        with self._lock:
            for key in allowed:
                if key in values:
                    self._settings[key] = values[key]
            self._settings["sample_interval_seconds"] = max(
                1.0, min(float(self._settings.get("sample_interval_seconds", 2.0)), 30.0)
            )
            self._settings["metric_interval_seconds"] = max(
                2.0, min(float(self._settings.get("metric_interval_seconds", 5.0)), 60.0)
            )
            self.root.mkdir(parents=True, exist_ok=True)
            _write_json(SETTINGS_PATH, self._settings)
            return dict(self._settings)

    def status(self) -> dict[str, Any]:
        with self._lock:
            value = dict(self._state)
            if value.get("active") and value.get("started_at"):
                started = datetime.fromisoformat(str(value["started_at"]))
                value["duration_seconds"] = round((utcnow() - started).total_seconds(), 1)
            else:
                value["duration_seconds"] = 0.0
            value["settings"] = dict(self._settings)
            return value

    def _run_dir(self) -> Path:
        path = self._state.get("path")
        if not path:
            raise RuntimeError("直播记录尚未启动")
        return Path(str(path))

    def _configuration_snapshot(self, db: Session) -> dict[str, Any]:
        app_settings = get_settings().model_dump()
        app_settings.pop("secret_key", None)
        app_settings.pop("admin_token", None)

        providers = []
        for row in db.scalars(select(ProviderConfig).order_by(ProviderConfig.created_at)).all():
            providers.append(
                {
                    **_row_fields(
                        row,
                        (
                            "id",
                            "name",
                            "provider_type",
                            "enabled",
                            "api_base_url",
                            "created_at",
                            "updated_at",
                        ),
                    ),
                    "settings": loads(row.settings_json, {}),
                    "credential_keys": sorted(decrypt_json(row.credentials_encrypted).keys()),
                }
            )

        bridges = []
        for row in db.scalars(select(BridgeAgent).order_by(BridgeAgent.created_at)).all():
            bridges.append(
                {
                    **_row_fields(
                        row,
                        (
                            "id",
                            "name",
                            "machine_name",
                            "version",
                            "status",
                            "last_seen_at",
                            "created_at",
                            "updated_at",
                        ),
                    ),
                    "capabilities": loads(row.capabilities_json, []),
                    "metadata": loads(row.metadata_json, {}),
                }
            )

        extensions = []
        for row in db.scalars(select(BrowserExtension).order_by(BrowserExtension.created_at)).all():
            extensions.append(
                {
                    **_row_fields(
                        row,
                        (
                            "id",
                            "name",
                            "browser_name",
                            "version",
                            "status",
                            "active_tab_url",
                            "last_seen_at",
                            "created_at",
                            "updated_at",
                        ),
                    ),
                    "metadata": loads(row.metadata_json, {}),
                }
            )

        sessions = [
            {
                **_row_fields(
                    row,
                    (
                        "id",
                        "provider_config_id",
                        "bridge_id",
                        "status",
                        "external_session_id",
                        "error_message",
                        "started_at",
                        "ended_at",
                        "created_at",
                        "updated_at",
                    ),
                ),
                "request": loads(row.request_json, {}),
                "response": loads(row.response_json, {}),
            }
            for row in db.scalars(select(AvatarSession).order_by(AvatarSession.created_at)).all()
        ]

        director_configs = []
        for row in db.scalars(select(AutoDirectorConfig).order_by(AutoDirectorConfig.created_at)).all():
            director_configs.append(
                {
                    **_row_fields(
                        row,
                        (
                            "id",
                            "extension_id",
                            "enabled",
                            "mode",
                            "api_base_url",
                            "model_name",
                            "last_dispatched_at",
                            "last_idle_prompt_at",
                            "last_event_at",
                            "created_at",
                            "updated_at",
                        ),
                    ),
                    "settings": loads(row.settings_json, {}),
                    "credential_keys": sorted(decrypt_json(row.credentials_encrypted).keys()),
                }
            )

        director_runs = [
            {
                **_row_fields(
                    row,
                    (
                        "id",
                        "config_id",
                        "status",
                        "phase",
                        "current_segment_index",
                        "started_at",
                        "paused_at",
                        "ended_at",
                        "current_segment_started_at",
                        "last_decision_at",
                        "next_cue_at",
                        "created_at",
                        "updated_at",
                    ),
                ),
                "rundown": loads(row.rundown_json, []),
                "state": loads(row.state_json, {}),
            }
            for row in db.scalars(select(AutoDirectorRun).order_by(AutoDirectorRun.created_at)).all()
        ]

        voice_profiles = []
        for row in db.scalars(select(VoiceProfile).order_by(VoiceProfile.created_at)).all():
            voice_profiles.append(
                {
                    **_row_fields(
                        row,
                        (
                            "id",
                            "extension_id",
                            "bridge_id",
                            "enabled",
                            "mode",
                            "style_preset",
                            "native_voice",
                            "style_instruction",
                            "auto_apply_style",
                            "auto_mute_chatgpt_tab",
                            "tts_api_base_url",
                            "tts_model",
                            "tts_voice",
                            "tts_speed_percent",
                            "created_at",
                            "updated_at",
                        ),
                    ),
                    "credential_keys": sorted(decrypt_json(row.credentials_encrypted).keys()),
                }
            )

        return {
            "captured_at": iso(),
            "server_version": __version__,
            "settings": app_settings,
            "providers": providers,
            "bridges": bridges,
            "extensions": extensions,
            "avatar_sessions": sessions,
            "auto_director_configs": director_configs,
            "auto_director_runs": director_runs,
            "voice_profiles": voice_profiles,
            "privacy": {
                "secrets_redacted": True,
                "contains_audience_text": bool(self._state.get("include_audience_text")),
            },
        }

    def start(
        self,
        db: Session,
        *,
        title: str | None = None,
        source: str = "manual",
        include_audience_text: bool | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            if self._state.get("active"):
                return self.status()
            now = utcnow()
            run_id = f"{now:%Y%m%d-%H%M%S}-{uuid4().hex[:8]}"
            run_dir = self.root / run_id
            run_dir.mkdir(parents=True, exist_ok=False)
            include_text = (
                bool(self._settings.get("include_audience_text", True))
                if include_audience_text is None
                else bool(include_audience_text)
            )
            self._state = {
                "active": True,
                "run_id": run_id,
                "title": (title or f"ALiver 直播 {now:%Y-%m-%d %H:%M}").strip(),
                "source": source,
                "started_at": now.isoformat(),
                "stopped_at": None,
                "path": str(run_dir),
                "bundle_path": None,
                "last_sample_at": None,
                "last_error": None,
                "record_count": 0,
                "include_audience_text": include_text,
            }
            self._seen = {"audience": set(), "decision": set(), "log": set()}
            self._command_versions = {}
            self._last_metric_monotonic = 0.0
            manifest = {
                "schema_version": 1,
                "run_id": run_id,
                "title": self._state["title"],
                "source": source,
                "server_version": __version__,
                "started_at": now.isoformat(),
                "stopped_at": None,
                "active": True,
                "files": {
                    "timeline": "timeline.jsonl",
                    "metrics": "metrics.jsonl",
                    "configuration_start": "configuration-start.json",
                    "configuration_stop": "configuration-stop.json",
                    "quality_summary": "quality-summary.json",
                },
            }
            _write_json(run_dir / "manifest.json", manifest)
            _write_json(run_dir / "configuration-start.json", self._configuration_snapshot(db))
            _append_jsonl(
                run_dir / "timeline.jsonl",
                {"kind": "run.started", "at": now.isoformat(), "data": self.status()},
            )
            return self.status()

    def record_external(self, kind: str, data: dict[str, Any]) -> None:
        with self._lock:
            if not self._state.get("active"):
                return
            payload = dict(data)
            if not self._state.get("include_audience_text", True):
                for key in ("content", "text", "raw_text", "instruction"):
                    if key in payload:
                        payload[key] = "[已按隐私设置省略]"
            _append_jsonl(
                self._run_dir() / "timeline.jsonl",
                {"kind": kind, "at": iso(), "data": payload},
            )
            self._state["record_count"] = int(self._state.get("record_count") or 0) + 1

    def _append_timeline(self, kind: str, at: datetime | None, data: dict[str, Any]) -> None:
        payload = dict(data)
        if not self._state.get("include_audience_text", True):
            for key in ("content", "raw_text", "instruction", "message"):
                if key in payload:
                    payload[key] = "[已按隐私设置省略]"
        _append_jsonl(
            self._run_dir() / "timeline.jsonl",
            {"kind": kind, "at": iso(at), "data": payload},
        )
        self._state["record_count"] = int(self._state.get("record_count") or 0) + 1

    def sample(self, db: Session) -> dict[str, Any]:
        with self._lock:
            if not self._state.get("active"):
                return self.status()
            try:
                started_at = datetime.fromisoformat(str(self._state["started_at"]))
                for row in db.scalars(
                    select(AudienceEvent)
                    .where(AudienceEvent.created_at >= started_at)
                    .order_by(AudienceEvent.created_at)
                ).all():
                    if row.id in self._seen["audience"]:
                        continue
                    self._seen["audience"].add(row.id)
                    payload = loads(row.payload_json, {})
                    self._append_timeline(
                        "audience.event",
                        row.created_at,
                        {
                            **_row_fields(
                                row,
                                (
                                    "id",
                                    "config_id",
                                    "event_type",
                                    "platform",
                                    "user_name",
                                    "content",
                                    "status",
                                    "score",
                                    "reason",
                                    "selected_command_id",
                                    "processed_at",
                                    "created_at",
                                    "updated_at",
                                ),
                            ),
                            "recognition": {
                                "source": payload.get("source"),
                                "confidence": payload.get("confidence"),
                                "raw_text": payload.get("raw_text"),
                                "bbox": payload.get("bbox"),
                            },
                        },
                    )

                for row in db.scalars(
                    select(DirectorDecision)
                    .where(DirectorDecision.created_at >= started_at)
                    .order_by(DirectorDecision.created_at)
                ).all():
                    if row.id in self._seen["decision"]:
                        continue
                    self._seen["decision"].add(row.id)
                    self._append_timeline(
                        "director.decision",
                        row.created_at,
                        {
                            **_row_fields(
                                row,
                                (
                                    "id",
                                    "config_id",
                                    "run_id",
                                    "event_id",
                                    "command_id",
                                    "decision_type",
                                    "instruction",
                                    "avatar_action",
                                    "priority",
                                    "reason",
                                    "created_at",
                                ),
                            ),
                            "context": loads(row.context_json, {}),
                            "result": loads(row.result_json, {}),
                        },
                    )

                for row in db.scalars(
                    select(DirectorCommand)
                    .where(DirectorCommand.created_at >= started_at)
                    .order_by(DirectorCommand.created_at)
                ).all():
                    version = (
                        f"{row.status}|{row.updated_at}|{row.error_message}|"
                        f"{row.dispatched_at}|{row.completed_at}"
                    )
                    if self._command_versions.get(row.id) == version:
                        continue
                    self._command_versions[row.id] = version
                    self._append_timeline(
                        "director.command",
                        row.updated_at or row.created_at,
                        {
                            **_row_fields(
                                row,
                                (
                                    "id",
                                    "extension_id",
                                    "command_type",
                                    "status",
                                    "priority",
                                    "error_message",
                                    "dispatched_at",
                                    "completed_at",
                                    "created_at",
                                    "updated_at",
                                ),
                            ),
                            "payload": loads(row.payload_json, {}),
                            "result": loads(row.result_json, {}),
                        },
                    )

                for row in db.scalars(
                    select(EventLog)
                    .where(EventLog.created_at >= started_at)
                    .order_by(EventLog.id)
                ).all():
                    key = str(row.id)
                    if key in self._seen["log"]:
                        continue
                    self._seen["log"].add(key)
                    self._append_timeline(
                        "runtime.log",
                        row.created_at,
                        {
                            **_row_fields(
                                row,
                                (
                                    "id",
                                    "level",
                                    "category",
                                    "message",
                                    "provider_id",
                                    "session_id",
                                    "bridge_id",
                                    "latency_ms",
                                    "created_at",
                                ),
                            ),
                            "details": loads(row.details_json, {}),
                        },
                    )

                metric_interval = float(self._settings.get("metric_interval_seconds", 5.0))
                if time.monotonic() - self._last_metric_monotonic >= metric_interval:
                    self._last_metric_monotonic = time.monotonic()
                    bridges = [
                        {
                            **_row_fields(
                                row,
                                ("id", "name", "version", "status", "last_seen_at"),
                            ),
                            "metadata": loads(row.metadata_json, {}),
                        }
                        for row in db.scalars(select(BridgeAgent)).all()
                    ]
                    extensions = [
                        {
                            **_row_fields(
                                row,
                                ("id", "name", "version", "status", "last_seen_at", "active_tab_url"),
                            ),
                            "metadata": loads(row.metadata_json, {}),
                        }
                        for row in db.scalars(select(BrowserExtension)).all()
                    ]
                    runs = [
                        {
                            **_row_fields(
                                row,
                                (
                                    "id",
                                    "config_id",
                                    "status",
                                    "phase",
                                    "current_segment_index",
                                    "last_decision_at",
                                    "next_cue_at",
                                ),
                            ),
                            "state": loads(row.state_json, {}),
                        }
                        for row in db.scalars(select(AutoDirectorRun)).all()
                    ]
                    _append_jsonl(
                        self._run_dir() / "metrics.jsonl",
                        {
                            "at": iso(),
                            "bridges": bridges,
                            "extensions": extensions,
                            "director_runs": runs,
                        },
                    )
                self._state["last_sample_at"] = iso()
                self._state["last_error"] = None
            except Exception as exc:
                self._state["last_error"] = f"{type(exc).__name__}: {exc}"
            return self.status()

    def _quality_summary(self, db: Session, stopped_at: datetime | None = None) -> dict[str, Any]:
        started_at = datetime.fromisoformat(str(self._state["started_at"]))
        end_at = stopped_at or utcnow()
        events = db.scalars(
            select(AudienceEvent)
            .where(AudienceEvent.created_at >= started_at, AudienceEvent.created_at <= end_at)
            .order_by(AudienceEvent.created_at)
        ).all()
        commands = db.scalars(
            select(DirectorCommand)
            .where(DirectorCommand.created_at >= started_at, DirectorCommand.created_at <= end_at)
            .order_by(DirectorCommand.created_at)
        ).all()
        decisions = db.scalars(
            select(DirectorDecision)
            .where(DirectorDecision.created_at >= started_at, DirectorDecision.created_at <= end_at)
            .order_by(DirectorDecision.created_at)
        ).all()
        logs = db.scalars(
            select(EventLog)
            .where(EventLog.created_at >= started_at, EventLog.created_at <= end_at)
            .order_by(EventLog.created_at)
        ).all()

        event_types = Counter(row.event_type for row in events)
        event_statuses = Counter(row.status for row in events)
        decision_types = Counter(row.decision_type for row in decisions)
        command_statuses = Counter(row.status for row in commands)
        log_levels = Counter(row.level for row in logs)
        viewers = {row.user_name for row in events if row.user_name and row.user_name not in {"观众", "匿名", "系统"}}
        latencies: list[float] = []
        command_by_id = {row.id: row for row in commands}
        confidences: list[float] = []
        sources: Counter[str] = Counter()
        for event in events:
            payload = loads(event.payload_json, {})
            confidence = payload.get("confidence")
            if isinstance(confidence, (int, float)):
                confidences.append(float(confidence))
            source = str(payload.get("source") or "")
            if source:
                sources[source] += 1
            command = command_by_id.get(str(event.selected_command_id or ""))
            if command and command.completed_at:
                latencies.append(max(0.0, (command.completed_at - event.created_at).total_seconds()))

        failed_commands = command_statuses.get("failed", 0)
        error_count = log_levels.get("ERROR", 0)
        warning_count = log_levels.get("WARNING", 0) + log_levels.get("WARN", 0)
        score = 100
        score -= min(30, failed_commands * 8)
        score -= min(30, error_count * 5)
        score -= min(10, warning_count * 2)
        if events and not decisions:
            score -= 20
        if events and not commands:
            score -= 20
        if latencies and sum(latencies) / len(latencies) > 15:
            score -= 10
        score = max(0, score)

        recommendations: list[str] = []
        if events and not decisions:
            recommendations.append("已识别互动但没有导演决策，请检查自动导演是否处于 live 状态。")
        if decisions and not commands:
            recommendations.append("已有导演决策但没有命令下发，请检查 Chrome 扩展在线和输入框状态。")
        if failed_commands:
            recommendations.append(f"有 {failed_commands} 条导演命令失败，建议查看 timeline.jsonl 中的错误详情。")
        if latencies and sum(latencies) / len(latencies) > 15:
            recommendations.append("互动到口播完成的平均延迟超过 15 秒，可缩短回答长度或降低导演冷却时间。")
        if sources and sources.get("screen_region_clear"):
            recommendations.append("本场使用过桌面区域 OCR 兜底，请保持直播伴侣互动区不被其他窗口遮挡。")
        if not recommendations:
            recommendations.append("本场核心链路整体正常；可继续根据观众停留和互动密度微调节目节奏。")

        return {
            "schema_version": 1,
            "run_id": self._state.get("run_id"),
            "title": self._state.get("title"),
            "started_at": started_at.isoformat(),
            "ended_at": end_at.isoformat(),
            "duration_seconds": round((end_at - started_at).total_seconds(), 1),
            "quality_score": score,
            "events": {
                "total": len(events),
                "by_type": dict(event_types),
                "by_status": dict(event_statuses),
                "unique_viewers": len(viewers),
                "recognition_sources": dict(sources),
                "average_confidence": (
                    round(sum(confidences) / len(confidences), 4) if confidences else None
                ),
            },
            "director": {
                "decisions": len(decisions),
                "decision_types": dict(decision_types),
                "commands": len(commands),
                "command_statuses": dict(command_statuses),
                "success_rate": (
                    round(command_statuses.get("completed", 0) / len(commands), 4)
                    if commands
                    else None
                ),
                "average_event_to_completion_seconds": (
                    round(sum(latencies) / len(latencies), 3) if latencies else None
                ),
                "maximum_event_to_completion_seconds": round(max(latencies), 3) if latencies else None,
            },
            "runtime": {
                "logs": len(logs),
                "levels": dict(log_levels),
                "errors": [
                    {
                        "category": row.category,
                        "message": row.message,
                        "at": row.created_at.isoformat(),
                    }
                    for row in logs
                    if row.level == "ERROR"
                ][:50],
            },
            "recommendations": recommendations,
            "privacy": {
                "secrets_redacted": True,
                "audience_text_included": bool(self._state.get("include_audience_text")),
            },
        }

    def _build_bundle(self) -> Path:
        run_dir = self._run_dir()
        bundle = run_dir.parent / f"aliver-live-run-{self._state['run_id']}.zip"
        with zipfile.ZipFile(bundle, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for path in sorted(run_dir.rglob("*")):
                if path.is_file():
                    archive.write(path, path.relative_to(run_dir))
        self._state["bundle_path"] = str(bundle)
        return bundle

    def export(self, db: Session) -> dict[str, Any]:
        with self._lock:
            if not self._state.get("active"):
                bundle = self._state.get("bundle_path")
                if bundle and Path(str(bundle)).exists():
                    return {**self.status(), "bundle_path": bundle}
                raise RuntimeError("没有正在记录或可导出的直播记录")
            self.sample(db)
            _write_json(self._run_dir() / "quality-summary.json", self._quality_summary(db))
            _write_json(self._run_dir() / "configuration-latest.json", self._configuration_snapshot(db))
            bundle = self._build_bundle()
            return {**self.status(), "bundle_path": str(bundle)}

    def stop(self, db: Session, *, reason: str = "manual") -> dict[str, Any]:
        with self._lock:
            if not self._state.get("active"):
                return self.status()
            self.sample(db)
            stopped_at = utcnow()
            _write_json(self._run_dir() / "configuration-stop.json", self._configuration_snapshot(db))
            summary = self._quality_summary(db, stopped_at)
            _write_json(self._run_dir() / "quality-summary.json", summary)
            self._append_timeline(
                "run.stopped",
                stopped_at,
                {"reason": reason, "quality_score": summary.get("quality_score")},
            )
            manifest_path = self._run_dir() / "manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest.update({"active": False, "stopped_at": stopped_at.isoformat(), "stop_reason": reason})
            _write_json(manifest_path, manifest)
            self._state["active"] = False
            self._state["stopped_at"] = stopped_at.isoformat()
            bundle = self._build_bundle()
            return {**self.status(), "quality_summary": summary, "bundle_path": str(bundle)}

    def list_runs(self, limit: int = 30) -> list[dict[str, Any]]:
        self.root.mkdir(parents=True, exist_ok=True)
        rows: list[dict[str, Any]] = []
        for run_dir in sorted((path for path in self.root.iterdir() if path.is_dir()), reverse=True):
            manifest_path = run_dir / "manifest.json"
            if not manifest_path.exists():
                continue
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            summary_path = run_dir / "quality-summary.json"
            summary = {}
            if summary_path.exists():
                try:
                    summary = json.loads(summary_path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    pass
            bundle = run_dir.parent / f"aliver-live-run-{manifest.get('run_id')}.zip"
            rows.append(
                {
                    **manifest,
                    "quality_score": summary.get("quality_score"),
                    "duration_seconds": summary.get("duration_seconds"),
                    "events": (summary.get("events") or {}).get("total"),
                    "commands": (summary.get("director") or {}).get("commands"),
                    "bundle_path": str(bundle) if bundle.exists() else None,
                }
            )
            if len(rows) >= limit:
                break
        return rows

    def bundle_for_run(self, run_id: str) -> Path | None:
        bundle = self.root / f"aliver-live-run-{run_id}.zip"
        if bundle.exists():
            return bundle
        run_dir = self.root / run_id
        if not run_dir.exists():
            return None
        previous = self._state
        try:
            self._state = {**previous, "run_id": run_id, "path": str(run_dir)}
            return self._build_bundle()
        finally:
            self._state = previous

    def auto_manage(self, db: Session) -> None:
        if not bool(self._settings.get("auto_start_on_director", True)):
            return
        active_runs = db.scalars(select(AutoDirectorRun)).all()
        running = next((row for row in active_runs if row.status in RUNNING_STATUSES), None)
        if running is not None and not self._state.get("active"):
            self.start(
                db,
                title=f"自动导演直播 · {running.phase}",
                source="director_auto",
                include_audience_text=bool(self._settings.get("include_audience_text", True)),
            )
        elif running is None and self._state.get("active") and self._state.get("source") == "director_auto":
            self.stop(db, reason="director_stopped")


live_run_recorder = LiveRunRecorder()


async def live_run_worker(stop_event: asyncio.Event) -> None:
    while not stop_event.is_set():
        try:
            with SessionLocal() as db:
                live_run_recorder.auto_manage(db)
                live_run_recorder.sample(db)
        except Exception as exc:
            with live_run_recorder._lock:
                live_run_recorder._state["last_error"] = f"{type(exc).__name__}: {exc}"
        interval = float(live_run_recorder.settings().get("sample_interval_seconds", 2.0))
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval)
        except TimeoutError:
            continue


def remove_run(run_id: str) -> bool:
    run_dir = LIVE_RUN_ROOT / run_id
    bundle = LIVE_RUN_ROOT / f"aliver-live-run-{run_id}.zip"
    removed = False
    if run_dir.exists() and run_dir.is_dir():
        shutil.rmtree(run_dir)
        removed = True
    if bundle.exists():
        bundle.unlink()
        removed = True
    return removed
