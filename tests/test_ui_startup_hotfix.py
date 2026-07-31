from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8-sig")


def test_console_refinement_does_not_move_wizard_forever():
    script = _read("app/static/console_refinement_v4.js")

    assert "wizard.nextElementSibling !== autoGrid" in script
    assert "observer?.disconnect()" in script
    assert "requestAnimationFrame" in script
    assert "wizard.parentElement !== director" not in script


def test_session_heading_is_kept_outside_embedded_workspace():
    script = _read("app/static/console_refinement_v4.js")

    assert "normalizeSessionHeading" in script
    assert "sessions.replaceChildren(...(heading ? [heading] : [])" in script
    assert "if (item !== keeper) item.remove()" in script


def test_server_disables_polling_access_log_noise():
    bootstrap = _read("app/bootstrap.py")

    assert "access_log=False" in bootstrap
    assert 'log_level="info"' in bootstrap


def test_bridge_launcher_relies_on_fast_single_instance_lock():
    launcher = _read("scripts/run_bridge_windows.ps1")

    assert "Get-CimInstance Win32_Process" not in launcher
    assert "单实例检查由 Bridge 内部锁完成" in launcher
    assert "bridge.agent_sync" in launcher
