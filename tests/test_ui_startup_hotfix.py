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


def test_session_workspace_has_full_width_and_real_hidden_panels():
    style = _read("app/static/console_refinement_v4.css")

    assert ".aliver-workspace > #tab-sessions.active" in style
    assert "display: block !important" in style
    assert "grid-column: 1 / -1 !important" in style
    assert "#tab-sessions .avatar-session-subpanel[hidden]" in style
    assert "#avatar-provider-subpanel:not([hidden])" in style
    assert "#avatar-provider-subpanel,\n#avatar-provider-subpanel.embedded-provider-panel" not in style


def test_console_assets_use_new_hotfix_cache_key():
    loader = _read("app/static/gpt_in_speech_patch.js")
    refinement = _read("app/static/console_refinement_v4.js")

    assert "0.15.1-hotfix2" in loader
    assert "0.15.1-hotfix2" in refinement


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
