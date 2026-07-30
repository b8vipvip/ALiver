from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_bridge_launcher_does_not_treat_native_stderr_as_fatal():
    text = (ROOT / "scripts" / "run_bridge_windows.ps1").read_text(encoding="utf-8-sig")
    assert '$ErrorActionPreference = "Continue"' in text
    assert "System.Management.Automation.ErrorRecord" in text
    assert "$exitCode = $LASTEXITCODE" in text
    assert "2>&1" in text


def test_bridge_launcher_keeps_utf8_bom_for_windows_powershell():
    data = (ROOT / "scripts" / "run_bridge_windows.ps1").read_bytes()
    assert data.startswith(b"\xef\xbb\xbf")


def test_local_time_patch_treats_naive_backend_timestamps_as_utc():
    text = (ROOT / "app" / "static" / "local_time_patch.js").read_text(encoding="utf-8")
    assert "naiveIso" in text
    assert "text.replace(' ', 'T')}Z" in text
    assert "date.toLocaleString()" in text
