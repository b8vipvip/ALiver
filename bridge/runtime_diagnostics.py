from __future__ import annotations

import argparse
import atexit
import asyncio
import faulthandler
import importlib.metadata
import json
import logging
import os
import platform
import shutil
import subprocess
import sys
import threading
import time
import traceback
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent
PROJECT_DIR = BASE_DIR.parent
LOG_DIR = BASE_DIR / "logs"
RUNS_DIR = LOG_DIR / "runs"
BUNDLES_DIR = LOG_DIR / "bundles"
STATE_PATH = LOG_DIR / "last-run.json"
DIAGNOSTICS_DIR = BASE_DIR / "diagnostics"

_LOCK = threading.RLock()
_STARTED = False
_RUN_ID = ""
_RUNTIME_LOG: Path | None = None
_EVENT_LOG: Path | None = None
_FAULT_LOG: Path | None = None
_RUNTIME_HANDLE: Any = None
_EVENT_HANDLE: Any = None
_FAULT_HANDLE: Any = None
_ORIGINAL_EXCEPTOOK = sys.excepthook
_ORIGINAL_THREADING_EXCEPTOOK = getattr(threading, "excepthook", None)

_SECRET_KEYS = {
    "api_key",
    "apikey",
    "authorization",
    "password",
    "secret",
    "token",
    "x-bridge-token",
}
_PACKAGE_NAMES = (
    "aliver",
    "simli-ai",
    "livekit",
    "livekit-api",
    "aiortc",
    "av",
    "opencv-python",
    "numpy",
    "PyAudioWPatch",
    "websockets",
)


def local_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="milliseconds")


def utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    temporary.replace(path)


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def redact(value: Any, *, key: str = "") -> Any:
    lowered = key.lower().replace("-", "_")
    if any(secret.replace("-", "_") in lowered for secret in _SECRET_KEYS):
        return "***REDACTED***"
    if isinstance(value, dict):
        return {str(item_key): redact(item_value, key=str(item_key)) for item_key, item_value in value.items()}
    if isinstance(value, list):
        return [redact(item) for item in value]
    if isinstance(value, tuple):
        return [redact(item) for item in value]
    if isinstance(value, str) and len(value) > 6000:
        return value[:6000] + "…<truncated>"
    return value


def package_versions() -> dict[str, str | None]:
    versions: dict[str, str | None] = {}
    for name in _PACKAGE_NAMES:
        try:
            versions[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            versions[name] = None
    return versions


def system_snapshot() -> dict[str, Any]:
    return {
        "captured_at": local_iso(),
        "platform": platform.platform(),
        "python_version": sys.version,
        "python_executable": sys.executable,
        "pid": os.getpid(),
        "cwd": str(Path.cwd()),
        "argv": list(sys.argv),
        "packages": package_versions(),
        "environment": {
            "PYTHONFAULTHANDLER": os.environ.get("PYTHONFAULTHANDLER"),
            "PYTHONUNBUFFERED": os.environ.get("PYTHONUNBUFFERED"),
            "OPENCV_OPENCL_RUNTIME": os.environ.get("OPENCV_OPENCL_RUNTIME"),
            "OMP_NUM_THREADS": os.environ.get("OMP_NUM_THREADS"),
        },
    }


def _write_runtime_line(text: str) -> None:
    with _LOCK:
        if _RUNTIME_HANDLE is not None:
            _RUNTIME_HANDLE.write(text.rstrip("\n") + "\n")
            _RUNTIME_HANDLE.flush()


def event(name: str, **details: Any) -> None:
    row = {
        "at": local_iso(),
        "utc": utc_iso(),
        "monotonic": round(time.monotonic(), 6),
        "run_id": _RUN_ID or None,
        "pid": os.getpid(),
        "thread": threading.current_thread().name,
        "event": name,
        "details": redact(details),
    }
    line = json.dumps(row, ensure_ascii=False, default=str)
    with _LOCK:
        if _EVENT_HANDLE is not None:
            _EVENT_HANDLE.write(line + "\n")
            _EVENT_HANDLE.flush()
        _write_runtime_line(f"[{row['at']}] [{name}] {json.dumps(row['details'], ensure_ascii=False, default=str)}")


def exception(name: str, exc: BaseException, **details: Any) -> None:
    event(
        name,
        exception_type=type(exc).__name__,
        exception_message=str(exc),
        traceback="".join(traceback.format_exception(type(exc), exc, exc.__traceback__)),
        **details,
    )


def _mark_state(status: str, **details: Any) -> None:
    current = _load_json(STATE_PATH)
    current.update(
        {
            "run_id": _RUN_ID or current.get("run_id"),
            "pid": os.getpid(),
            "status": status,
            "updated_at": local_iso(),
            "runtime_log": str(_RUNTIME_LOG) if _RUNTIME_LOG else current.get("runtime_log"),
            "event_log": str(_EVENT_LOG) if _EVENT_LOG else current.get("event_log"),
            "fault_log": str(_FAULT_LOG) if _FAULT_LOG else current.get("fault_log"),
            **redact(details),
        }
    )
    try:
        _write_json(STATE_PATH, current)
    except OSError:
        pass


def _collect_windows_events(output_path: Path, *, milliseconds: int = 7_200_000) -> None:
    if os.name != "nt":
        output_path.write_text("Windows event log collection skipped on non-Windows platform.\n", encoding="utf-8")
        return
    query = f"*[System[(Level=1 or Level=2) and TimeCreated[timediff(@SystemTime) <= {milliseconds}]]]"
    command = ["wevtutil", "qe", "Application", f"/q:{query}", "/f:text", "/rd:true", "/c:120"]
    try:
        completed = subprocess.run(command, capture_output=True, text=True, timeout=20, errors="replace")
        output_path.write_text(
            "COMMAND: " + " ".join(command) + "\n\n" + completed.stdout + "\n\nSTDERR:\n" + completed.stderr,
            encoding="utf-8",
        )
    except Exception as exc:  # pragma: no cover - platform dependent
        output_path.write_text(
            f"Failed to collect Windows Application events: {type(exc).__name__}: {exc}\n",
            encoding="utf-8",
        )


def _copy_if_exists(source: Path, destination: Path) -> None:
    if source.exists() and source.is_file():
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)


def _copy_recent_files(source_dir: Path, destination_dir: Path, *, since: datetime) -> int:
    if not source_dir.exists():
        return 0
    copied = 0
    for source in source_dir.rglob("*"):
        if not source.is_file():
            continue
        try:
            modified = datetime.fromtimestamp(source.stat().st_mtime, tz=timezone.utc)
        except OSError:
            continue
        if modified < since:
            continue
        relative = source.relative_to(source_dir)
        target = destination_dir / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            shutil.copy2(source, target)
            copied += 1
        except OSError:
            continue
    return copied


def _sanitized_copy(source: Path, destination: Path) -> None:
    if not source.exists():
        return
    try:
        data = json.loads(source.read_text(encoding="utf-8"))
        _write_json(destination, redact(data))
    except (OSError, ValueError, TypeError):
        destination.write_text("Unable to parse or copy this file safely.\n", encoding="utf-8")


def create_support_bundle(
    *,
    reason: str,
    exit_code: int | None = None,
    console_log: str | Path | None = None,
    minutes: int = 90,
) -> dict[str, Any]:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    BUNDLES_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    bundle_name = f"ALiver-Bridge-故障包-{stamp}"
    staging = BUNDLES_DIR / (bundle_name + ".tmp")
    zip_path = BUNDLES_DIR / (bundle_name + ".zip")
    if staging.exists():
        shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True, exist_ok=True)

    since = datetime.now(timezone.utc) - timedelta(minutes=max(5, int(minutes)))
    metadata = {
        "reason": reason,
        "exit_code": exit_code,
        "created_at": local_iso(),
        "run_state": redact(_load_json(STATE_PATH)),
        "system": system_snapshot(),
    }
    _write_json(staging / "00-故障包说明.json", metadata)
    (staging / "00-请先查看.txt").write_text(
        "这是 ALiver Bridge 自动故障包。\n"
        "请直接把整个 ZIP 文件发送给开发者，不需要逐个挑选日志。\n"
        "日志包含本机时间、UTC 时间、命令阶段、Python 异常、原生崩溃堆栈和最近的 Windows 应用程序错误。\n"
        "API Key、Bridge Token、密码等字段会自动脱敏。\n",
        encoding="utf-8",
    )

    _copy_recent_files(RUNS_DIR, staging / "bridge-运行日志", since=since)
    _copy_recent_files(DIAGNOSTICS_DIR, staging / "simli-会话诊断", since=since)
    if console_log:
        _copy_if_exists(Path(console_log), staging / "bridge-控制台" / Path(console_log).name)
    _copy_if_exists(STATE_PATH, staging / "bridge-last-run.json")
    _sanitized_copy(BASE_DIR / "bridge.local.json", staging / "配置-已脱敏" / "bridge.local.json")
    _sanitized_copy(BASE_DIR / "state.json", staging / "配置-已脱敏" / "state.json")
    _collect_windows_events(staging / "windows-Application-最近2小时.txt")

    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for source in staging.rglob("*"):
            if source.is_file():
                archive.write(source, source.relative_to(staging))
    shutil.rmtree(staging, ignore_errors=True)
    return {
        "created": True,
        "reason": reason,
        "exit_code": exit_code,
        "bundle_path": str(zip_path.resolve()),
        "logs_root": str(LOG_DIR.resolve()),
    }


def _previous_run_was_unclean(previous: dict[str, Any]) -> bool:
    if not previous or previous.get("status") != "running":
        return False
    previous_pid = previous.get("pid")
    if not isinstance(previous_pid, int) or previous_pid <= 0:
        return True
    if os.name != "nt":
        try:
            os.kill(previous_pid, 0)
            return False
        except OSError:
            return True
    try:
        completed = subprocess.run(
            ["tasklist", "/FI", f"PID eq {previous_pid}", "/FO", "CSV", "/NH"],
            capture_output=True,
            text=True,
            timeout=5,
            errors="replace",
        )
        return str(previous_pid) not in completed.stdout
    except Exception:
        return True


def start_runtime_logging(*, component: str, version: str) -> dict[str, Any]:
    global _STARTED, _RUN_ID, _RUNTIME_LOG, _EVENT_LOG, _FAULT_LOG
    global _RUNTIME_HANDLE, _EVENT_HANDLE, _FAULT_HANDLE
    with _LOCK:
        if _STARTED:
            return current_paths()
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        RUNS_DIR.mkdir(parents=True, exist_ok=True)
        previous = _load_json(STATE_PATH)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        _RUN_ID = f"{component}-{stamp}-pid{os.getpid()}"
        _RUNTIME_LOG = RUNS_DIR / f"{_RUN_ID}.log"
        _EVENT_LOG = RUNS_DIR / f"{_RUN_ID}.events.jsonl"
        _FAULT_LOG = RUNS_DIR / f"{_RUN_ID}.fault.log"
        _RUNTIME_HANDLE = _RUNTIME_LOG.open("a", encoding="utf-8", buffering=1)
        _EVENT_HANDLE = _EVENT_LOG.open("a", encoding="utf-8", buffering=1)
        _FAULT_HANDLE = _FAULT_LOG.open("a", encoding="utf-8", buffering=1)
        try:
            faulthandler.enable(_FAULT_HANDLE, all_threads=True)
        except Exception as exc:
            _write_runtime_line(f"faulthandler.enable failed: {type(exc).__name__}: {exc}")

        logger = logging.getLogger()
        logger.setLevel(logging.INFO)
        handler = logging.StreamHandler(_RUNTIME_HANDLE)
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
        logger.addHandler(handler)

        _STARTED = True
        _mark_state(
            "running",
            component=component,
            version=version,
            started_at=local_iso(),
            system=system_snapshot(),
        )
        event("bridge_runtime_started", component=component, version=version, paths=current_paths())

        def excepthook(exc_type, exc_value, exc_traceback):
            exception("sys_unhandled_exception", exc_value)
            _mark_state("crashed", crash_kind="sys_excepthook")
            _ORIGINAL_EXCEPTOOK(exc_type, exc_value, exc_traceback)

        sys.excepthook = excepthook

        if _ORIGINAL_THREADING_EXCEPTOOK is not None:

            def threading_hook(args):
                exception("thread_unhandled_exception", args.exc_value, thread_name=args.thread.name)
                _mark_state("crashed", crash_kind="threading_excepthook")
                _ORIGINAL_THREADING_EXCEPTOOK(args)

            threading.excepthook = threading_hook

        atexit.register(mark_graceful_exit)

    if _previous_run_was_unclean(previous):
        event("previous_bridge_run_unclean", previous=redact(previous))
        try:
            bundle = create_support_bundle(reason="检测到上一次 Bridge 非正常退出", minutes=180)
            event("previous_crash_bundle_created", **bundle)
        except Exception as exc:
            exception("previous_crash_bundle_failed", exc)
    return current_paths()


def install_asyncio_exception_handler(loop: asyncio.AbstractEventLoop) -> None:
    old_handler = loop.get_exception_handler()

    def handler(active_loop: asyncio.AbstractEventLoop, context: dict[str, Any]) -> None:
        exc = context.get("exception")
        if isinstance(exc, BaseException):
            exception("asyncio_unhandled_exception", exc, context=redact(context))
        else:
            event("asyncio_unhandled_exception", context=redact(context))
        if old_handler is not None:
            old_handler(active_loop, context)
        else:
            active_loop.default_exception_handler(context)

    loop.set_exception_handler(handler)
    event("asyncio_exception_handler_installed")


async def heartbeat_loop(status_provider: Any | None = None, *, interval_seconds: float = 2.0) -> None:
    while True:
        details: dict[str, Any] = {}
        if status_provider is not None:
            try:
                details["status"] = redact(status_provider())
            except Exception as exc:
                details["status_error"] = f"{type(exc).__name__}: {exc}"
        event("bridge_heartbeat", **details)
        _mark_state("running", heartbeat_at=local_iso())
        await asyncio.sleep(max(1.0, float(interval_seconds)))


def current_paths() -> dict[str, Any]:
    return {
        "run_id": _RUN_ID or None,
        "logs_root": str(LOG_DIR.resolve()),
        "runtime_log": str(_RUNTIME_LOG.resolve()) if _RUNTIME_LOG else None,
        "event_log": str(_EVENT_LOG.resolve()) if _EVENT_LOG else None,
        "fault_log": str(_FAULT_LOG.resolve()) if _FAULT_LOG else None,
        "bundles_dir": str(BUNDLES_DIR.resolve()),
        "simli_diagnostics_dir": str(DIAGNOSTICS_DIR.resolve()),
    }


def mark_graceful_exit() -> None:
    global _RUNTIME_HANDLE, _EVENT_HANDLE, _FAULT_HANDLE
    if not _STARTED:
        return
    try:
        event("bridge_runtime_stopping")
        _mark_state("stopped", stopped_at=local_iso())
    except Exception:
        pass
    for handle_name in ("_EVENT_HANDLE", "_RUNTIME_HANDLE", "_FAULT_HANDLE"):
        handle = globals().get(handle_name)
        if handle is not None:
            try:
                handle.flush()
                handle.close()
            except Exception:
                pass
            globals()[handle_name] = None


def _cli() -> int:
    parser = argparse.ArgumentParser(description="ALiver Bridge diagnostics")
    subparsers = parser.add_subparsers(dest="command", required=True)
    bundle_parser = subparsers.add_parser("bundle")
    bundle_parser.add_argument("--reason", default="手动导出")
    bundle_parser.add_argument("--exit-code", type=int, default=None)
    bundle_parser.add_argument("--console-log", default=None)
    bundle_parser.add_argument("--minutes", type=int, default=90)
    args = parser.parse_args()
    result = create_support_bundle(
        reason=args.reason,
        exit_code=args.exit_code,
        console_log=args.console_log,
        minutes=args.minutes,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
