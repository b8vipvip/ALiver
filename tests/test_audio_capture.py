from array import array

from bridge.audio_capture import calculate_pcm16_levels


def test_calculate_pcm16_levels_silence():
    result = calculate_pcm16_levels(b"\x00\x00" * 16)
    assert result["rms"] == 0
    assert result["peak"] == 0
    assert result["dbfs"] == -96


def test_calculate_pcm16_levels_signal():
    samples = array("h", [0, 16384, -16384, 32767, -32767])
    result = calculate_pcm16_levels(samples.tobytes())
    assert result["rms"] > 0
    assert result["peak"] == 32767
    assert -10 < result["dbfs"] < 0
