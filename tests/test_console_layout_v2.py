from pathlib import Path

import app
from bridge import agent_sync


ROOT = Path(__file__).resolve().parents[1]


def _read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def test_version_pair_is_bumped_for_live_debug_release():
    assert app.__version__ == "0.14.2"
    assert agent_sync.BRIDGE_VERSION == "0.10.2"


def test_bootstrap_loader_wires_the_dedicated_live_debug_workspace():
    loader = _read("app/static/gpt_in_speech_patch.js")
    layout = _read("app/static/console_layout_v2.js")
    validation = _read("app/static/live_debug_validation.js")

    assert "/static/console_layout_v2.js" in loader
    assert "/static/live_debug_validation.js" in loader
    assert "tab-simli-tuning" in layout
    assert "直播调试中心" in layout
    assert "live-debug-full-validation" in layout
    assert "aliver.full_validation" in validation


def test_director_and_collector_are_rehomed_by_layout_controller():
    layout = _read("app/static/console_layout_v2.js")

    assert "autoButton" not in layout  # renamed implementation should not regress to old branch
    assert "[data-tab=\"auto-director\"]" in layout
    assert "auto.classList.remove('tab-panel')" in layout
    assert "live-debug-collector-host" in layout
    assert "host.appendChild(collector)" in layout


def test_dashboard_assets_are_served_without_stale_browser_cache():
    bootstrap = _read("app/bootstrap.py")

    assert 'request.url.path.startswith("/static/")' in bootstrap
    assert "no-store, no-cache" in bootstrap
