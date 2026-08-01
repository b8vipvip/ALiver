from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8-sig")


def test_console_refinement_uses_bounded_layout_passes():
    script = _read("app/static/console_refinement_v4.js")

    assert "const STABILIZE_DELAYS" in script
    assert "STABILIZE_DELAYS.forEach" in script
    assert "window.setTimeout(run, delay)" in script
    assert "new MutationObserver" not in script
    assert "wizard.nextElementSibling !== autoGrid" in script
    assert "wizard.parentElement !== director" not in script


def test_session_heading_is_kept_outside_embedded_workspace():
    script = _read("app/static/console_refinement_v4.js")

    assert "normalizeSessionHeading" in script
    assert "sessions.replaceChildren(heading, switcher, main, providers)" in script
    assert "if (item !== keeper) item.remove()" in script
    assert "createSessionHeading" in script


def test_session_workspace_is_hidden_until_its_primary_tab_is_active():
    style = _read("app/static/console_refinement_v4.css")

    inactive = style.index(".aliver-workspace > #tab-sessions {")
    active = style.index(".aliver-workspace > #tab-sessions.active")
    assert "display: none !important" in style[inactive:active]
    assert "display: block !important" in style[active:]
    assert "grid-column: 1 / -1 !important" in style
    assert "#tab-sessions .avatar-session-subpanel[hidden]" in style
    assert "#avatar-provider-subpanel:not([hidden])" in style
    assert (
        ".aliver-workspace > #tab-sessions,\n.aliver-workspace > #tab-sessions.active" not in style
    )


def test_console_assets_use_dsp_release_cache_key():
    loader = _read("app/static/gpt_in_speech_patch.js")
    refinement = _read("app/static/console_refinement_v4.js")

    assert "0.16.2" in loader
    assert "0.15.1-hotfix3" in refinement


def test_server_disables_polling_access_log_noise():
    bootstrap = _read("app/bootstrap.py")

    assert "access_log=False" in bootstrap
    assert 'log_level="info"' in bootstrap


def test_bridge_launcher_relies_on_fast_single_instance_lock():
    launcher = _read("scripts/run_bridge_windows.ps1")

    assert "Get-CimInstance Win32_Process" not in launcher
    assert "单实例检查由 Bridge 内部锁完成" in launcher
    assert "bridge.agent_sync" in launcher


def test_bridge_launcher_decodes_python_output_as_utf8():
    launcher = _read("scripts/run_bridge_windows.ps1")

    assert "System.Text.UTF8Encoding" in launcher
    assert "[Console]::OutputEncoding = $utf8NoBom" in launcher
    assert "$global:OutputEncoding = $utf8NoBom" in launcher
    assert "chcp.com 65001" in launcher
    assert '$env:PYTHONIOENCODING = "utf-8"' in launcher


def test_bridge_fast_startup_avoids_slow_platform_and_local_proxy_discovery():
    patch = _read("bridge/fast_startup_patch.py")
    package = _read("bridge/__init__.py")

    assert "sys.getwindowsversion" in patch
    assert "platform.platform" not in patch
    assert "trust_env=not local" in patch
    assert 'host in {"127.0.0.1", "localhost", "::1"}' in patch
    assert "install_bridge_fast_startup_patch()" in package


def test_collector_autostart_happens_after_bridge_websocket_connection():
    patch = _read("bridge/deferred_collector_startup_patch.py")
    wrapper = _read("bridge/control_guard_install_patch.py")

    connected = patch.index('print("Bridge connected to ALiver")')
    scheduled = patch.index("collector_task = asyncio.create_task", connected)
    assert connected < scheduled
    assert "await self.sync_registration()" in patch
    assert "await asyncio.to_thread(self.douyin_collector.autostart)" in patch
    assert "collector_autostart_delay_seconds" in patch
    assert "install_deferred_collector_startup_patch(agent_module)" in wrapper
