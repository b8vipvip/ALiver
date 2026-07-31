from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def test_version_pair_is_bumped_for_native_voice_release():
    assert '__version__ = "0.15.1"' in _read("app/__init__.py")
    assert 'BRIDGE_PATCH_VERSION = "0.11.1"' in _read("bridge/startup_retry_patch.py")
    assert '"version": "0.1.4"' in _read("chrome_extension/manifest.json")


def test_bootstrap_loader_wires_live_debug_and_operations_workspaces():
    loader = _read("app/static/gpt_in_speech_patch.js")
    layout = _read("app/static/console_layout_v2.js")
    validation = _read("app/static/live_debug_validation_v2.js")

    assert "/static/console_layout_v2.js" in loader
    assert "/static/live_debug_validation.js" not in loader
    assert "/static/live_debug_validation_v2.js" in loader
    assert "/static/live_debug_recovery_ui.js" in loader
    assert "/static/wgc_hwnd_ui_patch.js" in loader
    assert "/static/audio_live_setup.js" in loader
    assert "/static/console_shell_v3.js" in loader
    assert "/static/live_run_console.js" in loader
    assert "/static/native_voice_lab_v2.js" in loader
    assert "/static/console_refinement_v4.js" in loader
    assert "/static/voice_lab.js" not in loader
    assert "tab-simli-tuning" in layout
    assert "直播调试中心" in layout
    assert "开播前一键检查" in validation
    assert "等待真实互动并验证" in validation
    assert "模拟观众进入与欢迎闭环" in validation
    assert "aliver.preflight_validation" in validation
    assert "aliver.live_validation" in validation


def test_live_audio_setup_exposes_lipsync_and_legacy_tts_commands():
    frontend = _read("app/static/audio_live_setup.js")
    bridge = _read("bridge/agent_sync.py")

    assert "一键配置直播语音与口型" in frontend
    assert "audio.live.auto_configure" in frontend
    assert "audio.live.auto_configure" in bridge
    assert "provider.vtube_studio.audio_mouth_fallback" in bridge
    assert "audio.gpt_out.play_tts" in bridge
    assert "voice.api_tts" in bridge


def test_wgc_version_patch_uses_observer_instead_of_competing_timer():
    patch = _read("app/static/wgc_hwnd_ui_patch.js")

    assert "const EXPECTED_BRIDGE_VERSION = '0.11.1'" in patch
    assert "const SERVER_VERSION = '0.15.1'" in patch
    assert "new MutationObserver" in patch
    assert "queueMicrotask" in patch
    assert "setInterval(applyVersionState" not in patch


def test_live_debug_recovery_assets_and_capabilities_are_exposed():
    bridge = _read("bridge/agent_sync.py")
    recovery = _read("bridge/live_debug_recovery_patch.py")
    director = _read("app/live_debug_director_recovery_patch.py")
    ui = _read("app/static/live_debug_recovery_ui.js")

    assert "douyin.visible.window_selection.v2" in bridge
    assert "douyin.visible.capture_freshness" in bridge
    assert "aliver.validation.auto_start_collector" in bridge
    assert "capture-metadata.json" in recovery
    assert "manager.start" in recovery
    assert "validation_run_recovered" in director
    assert "采集器若已停止" in ui


def test_sidebar_shell_adds_overview_live_runs_and_voice_lab():
    shell = _read("app/static/console_shell_v3.js")
    styles = _read("app/static/console_shell_v3.css")

    assert "直播工作台" in shell
    assert "直播记录" in shell
    assert "语音实验室" in shell
    assert "aliver-app-shell" in shell
    assert "aliver-sidebar" in styles
    assert "shell-readiness-grid" in styles


def test_director_and_collector_are_rehomed_by_layout_controller():
    layout = _read("app/static/console_layout_v2.js")

    assert "[data-tab=\"auto-director\"]" in layout
    assert "auto.classList.remove('tab-panel')" in layout
    assert "live-debug-collector-host" in layout
    assert "host.appendChild(collector)" in layout


def test_viewer_join_capture_and_welcome_logic_are_installed():
    bridge_defaults = _read("bridge/live_debug_defaults.py")
    welcome = _read("app/live_welcome_patch.py")
    ingest = _read("app/live_welcome_ingest_patch.py")

    assert 'DEFAULT_CONFIG["capture_join_notices"] = True' in bridge_defaults
    assert "进入了直播间" in welcome
    assert "welcome_per_viewer_cooldown_seconds" in welcome
    assert "welcome_max_per_minute" in welcome
    assert 'return "queued", 82' in ingest


def test_dashboard_assets_are_served_without_stale_browser_cache():
    bootstrap = _read("app/bootstrap.py")

    assert 'request.url.path.startswith("/static/")' in bootstrap
    assert "no-store, no-cache" in bootstrap
