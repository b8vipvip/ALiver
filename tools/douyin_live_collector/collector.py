from __future__ import annotations

import argparse
import json
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

VERSION = "0.11.0"


class CollectorError(RuntimeError):
    pass


def load_config(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise CollectorError(f"配置文件不存在：{path}")
    value = json.loads(path.read_text(encoding="utf-8"))
    required = ["aliver_url", "admin_token", "extension_id"]
    missing = [key for key in required if not str(value.get(key) or "").strip()]
    if missing:
        raise CollectorError(f"配置缺少字段：{', '.join(missing)}")
    return value


def post_json(config: dict[str, Any], path: str, payload: dict[str, Any]) -> dict[str, Any]:
    url = str(config["aliver_url"]).rstrip("/") + path
    request = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "X-ALiver-Token": str(config["admin_token"]),
            "User-Agent": f"ALiverDouyinCollector/{VERSION}",
        },
        method="POST",
    )
    timeout = float(config.get("request_timeout_seconds") or 8)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise CollectorError(f"ALiver HTTP {exc.code}: {detail}") from exc
    except OSError as exc:
        raise CollectorError(f"无法连接 ALiver：{exc}") from exc


def heartbeat_payload(config: dict[str, Any], *, connected: bool = True, error: str | None = None) -> dict[str, Any]:
    return {
        "extension_id": config["extension_id"],
        "collector_id": config.get("collector_id") or "aliver-douyin-live-companion",
        "connected": connected,
        "mate_version": config.get("mate_version"),
        "layout_mode": config.get("layout_mode"),
        "plugin_version": VERSION,
        "error": error,
    }


def send_heartbeat(config: dict[str, Any], *, connected: bool = True, error: str | None = None) -> None:
    post_json(config, "/api/douyin-live/heartbeat", heartbeat_payload(config, connected=connected, error=error))


def normalize_envelope(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    if not isinstance(value, dict):
        raise CollectorError("输入必须是 JSON object 或 array")
    if value.get("eventName") == "OPEN_LIVE_DATA":
        params = value.get("params") or {}
        return [item for item in params.get("payload", []) if isinstance(item, dict)]
    if value.get("event_name") == "OPEN_LIVE_DATA":
        return [item for item in value.get("payload", []) if isinstance(item, dict)]
    if "payload" in value and isinstance(value["payload"], list):
        return [item for item in value["payload"] if isinstance(item, dict)]
    if value.get("msg_type") or value.get("msg_type_str"):
        return [value]
    raise CollectorError("未识别到 OPEN_LIVE_DATA payload")


def forward(config: dict[str, Any], items: list[dict[str, Any]]) -> dict[str, Any]:
    return post_json(
        config,
        "/api/douyin-live/ingest",
        {
            "extension_id": config["extension_id"],
            "collector_id": config.get("collector_id") or "aliver-douyin-live-companion",
            "event_name": "OPEN_LIVE_DATA",
            "payload": items,
            "metadata": {
                "mate_version": config.get("mate_version"),
                "layout_mode": config.get("layout_mode"),
                "plugin_version": VERSION,
                "source": config.get("source") or "official_pipe_sdk",
            },
        },
    )


def heartbeat_loop(config: dict[str, Any], stop: threading.Event) -> None:
    interval = max(2.0, float(config.get("heartbeat_seconds") or 5))
    while not stop.wait(interval):
        try:
            send_heartbeat(config)
        except Exception as exc:  # noqa: BLE001 - collector must keep retrying
            print(f"[heartbeat] {exc}", file=sys.stderr, flush=True)


def stream_stdin(config: dict[str, Any]) -> int:
    stop = threading.Event()
    thread = threading.Thread(target=heartbeat_loop, args=(config, stop), daemon=True)
    thread.start()
    try:
        send_heartbeat(config)
        print("ALiver Douyin collector ready; waiting for OPEN_LIVE_DATA JSON lines.", flush=True)
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                items = normalize_envelope(json.loads(line))
                result = forward(config, items)
                print(json.dumps(result, ensure_ascii=False), flush=True)
            except Exception as exc:  # noqa: BLE001 - one bad message must not stop the stream
                print(f"[ingest] {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
        return 0
    finally:
        stop.set()
        try:
            send_heartbeat(config, connected=False)
        except Exception:
            pass


def replay(config: dict[str, Any], path: Path) -> int:
    value = json.loads(path.read_text(encoding="utf-8"))
    result = forward(config, normalize_envelope(value))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def main() -> int:
    default_config = Path(sys.argv[0]).resolve().with_name("douyin_collector.json")
    parser = argparse.ArgumentParser(description="Forward official Douyin OPEN_LIVE_DATA to ALiver")
    parser.add_argument("--config", type=Path, default=default_config)
    parser.add_argument("--replay", type=Path)
    parser.add_argument("--heartbeat-only", action="store_true")
    args = parser.parse_args()

    try:
        config = load_config(args.config)
        if args.replay:
            return replay(config, args.replay)
        if args.heartbeat_only:
            send_heartbeat(config)
            print("heartbeat ok")
            return 0
        return stream_stdin(config)
    except KeyboardInterrupt:
        return 130
    except Exception as exc:  # noqa: BLE001 - CLI boundary
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        time.sleep(0.1)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
