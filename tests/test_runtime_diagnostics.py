from __future__ import annotations

from bridge.runtime_diagnostics import redact


def test_redact_hides_nested_credentials():
    value = redact(
        {
            "api_key": "secret-key",
            "nested": {"bridge_token": "secret-token", "normal": "visible"},
        }
    )
    assert value["api_key"] == "***REDACTED***"
    assert value["nested"]["bridge_token"] == "***REDACTED***"
    assert value["nested"]["normal"] == "visible"
