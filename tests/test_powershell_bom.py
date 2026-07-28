from pathlib import Path


def test_all_powershell_scripts_use_utf8_bom():
    scripts = sorted(Path("scripts").glob("*.ps1"))
    assert scripts, "No PowerShell scripts found under scripts/."
    missing = [str(path) for path in scripts if not path.read_bytes().startswith(b"\xef\xbb\xbf")]
    assert not missing, f"PowerShell scripts must be UTF-8 with BOM: {missing}"
