from pathlib import Path

import pytest

from app.api import livetalking_cloud
from app.main import app


def test_livetalking_console_routes_are_registered() -> None:
    paths = set(app.openapi().get("paths", {}))
    assert "/api/dashboard/livetalking/config" in paths
    assert "/api/dashboard/livetalking/health" in paths
    assert "/api/dashboard/livetalking/bridge/start" in paths
    assert "/api/livetalking-viewer/config" in paths
    assert "/api/livetalking-viewer/session" in paths
    assert str(app.url_path_for("console_page")) == "/api/livetalking-console"
    assert str(app.url_path_for("viewer_page")) == "/api/livetalking-viewer"


def test_base_url_and_ws_url_normalization() -> None:
    assert (
        livetalking_cloud._normalize_base_url("https://gpu.example.com/aliver.html?debug=1")
        == "https://gpu.example.com"
    )
    assert (
        livetalking_cloud._normalize_base_url("http://127.0.0.1:8010/api/aliver/health")
        == "http://127.0.0.1:8010"
    )
    assert (
        livetalking_cloud._ws_url("https://gpu.example.com/live")
        == "wss://gpu.example.com/live/api/aliver/pcm"
    )
    with pytest.raises(ValueError):
        livetalking_cloud._normalize_base_url("file:///tmp/livetalking")


def test_console_configuration_encrypts_secrets(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "livetalking_cloud_console.json"
    monkeypatch.setattr(livetalking_cloud, "CONFIG_PATH", path)
    saved = livetalking_cloud._save_config(
        {
            "base_url": "https://gpu.example.com",
            "avatar_id": "avatar-01",
            "bridge_id": "bridge-01",
            "last_session_id": "session-01",
        },
        "super-secret-pcm-token",
        "viewer-secret-key",
    )
    raw = path.read_text(encoding="utf-8")
    assert "super-secret-pcm-token" not in raw
    assert "viewer-secret-key" not in raw
    assert saved["token"] == "super-secret-pcm-token"
    assert saved["viewer_key"] == "viewer-secret-key"
    assert saved["settings"]["last_session_id"] == "session-01"
    assert livetalking_cloud.verify_token(
        "viewer-secret-key",
        saved["viewer_key_hash"],
    )


def test_static_console_and_viewer_include_session_handoff() -> None:
    static_dir = Path(livetalking_cloud.STATIC_DIR)
    console = (static_dir / "livetalking_cloud.html").read_text(encoding="utf-8")
    viewer = (static_dir / "livetalking_viewer.html").read_text(encoding="utf-8")
    assert "/api/dashboard/livetalking/config" in console
    assert "/api/dashboard/livetalking/bridge/${action}" in console
    assert "CABLE-B Output" in console
    assert "aliver-livetalking-session" in viewer
    assert "/api/livetalking-viewer/session" in viewer
    assert "cloud_origin" in viewer
    assert "ALIVER_STREAM_TOKEN" not in viewer
