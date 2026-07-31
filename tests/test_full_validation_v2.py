from bridge.full_validation_v2 import (
    classify_capture_source,
    classify_channel_probe,
    summarize_steps,
    validation_step,
)


def test_capture_fallback_is_warning_not_false_pass():
    level, _ = classify_capture_source({"capture_source": "screen_region_clear"})
    assert level == "warning"

    level, _ = classify_capture_source({"capture_source": "windows_graphics_capture"})
    assert level == "passed"


def test_empty_available_channels_are_preflight_warning():
    level, message = classify_channel_probe(
        {
            "channels": [
                {"channel": "uia", "available": True, "line_count": 0, "event_count": 0},
                {"channel": "windows_graphics_capture", "available": True, "line_count": 0, "event_count": 0},
            ]
        }
    )

    assert level == "warning"
    assert "开播后" in message


def test_summary_distinguishes_warning_and_failure():
    steps = [
        validation_step("a", phase="preflight", level="passed", message="ok"),
        validation_step("b", phase="preflight", level="warning", message="check"),
    ]
    summary = summarize_steps(steps)
    assert summary["overall"] == "warning"
    assert summary["passed"] == 1
    assert summary["warning"] == 1
    assert summary["failed"] == 0

    steps.append(validation_step("c", phase="live", level="failed", message="bad"))
    assert summarize_steps(steps)["overall"] == "failed"
