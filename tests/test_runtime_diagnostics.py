from __future__ import annotations

import numpy as np

from bridge.runtime_diagnostics import redact
from bridge.simli_crash_guard import _safe_gray


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


def test_safe_gray_returns_small_contiguous_luminance_array():
    image = np.zeros((120, 160, 3), dtype=np.uint8)
    image[..., 0] = 100
    gray = _safe_gray(image)
    assert gray.shape == (30, 40)
    assert gray.flags.c_contiguous
    assert float(gray.mean()) > 0
