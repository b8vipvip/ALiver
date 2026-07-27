from __future__ import annotations

import base64
import os
import subprocess
import wave
from datetime import datetime
from pathlib import Path
from typing import Any

DEFAULT_TEST_TEXT = (
    "你好，ChatGPT。这是 ALiver 虚拟麦克风测试。"
    "听到这句话以后，请回答测试成功。"
    " Hello ChatGPT, please reply test successful."
)


def _powershell_encoded(script: str) -> str:
    return base64.b64encode(script.encode("utf-16-le")).decode("ascii")


def _b64_utf8(value: str) -> str:
    return base64.b64encode(value.encode("utf-8")).decode("ascii")


def synthesize_windows_speech(
    text: str,
    output_path: Path,
    *,
    sample_rate: int,
    channels: int,
) -> dict[str, Any]:
    """Generate a PCM16 WAV using the Windows built-in System.Speech engine."""
    if os.name != "nt":
        raise RuntimeError("Windows speech synthesis is available only on Windows.")

    clean_text = " ".join(str(text or DEFAULT_TEST_TEXT).split()).strip()[:500]
    if not clean_text:
        clean_text = DEFAULT_TEST_TEXT

    output_path.parent.mkdir(parents=True, exist_ok=True)
    text_b64 = _b64_utf8(clean_text)
    path_b64 = _b64_utf8(str(output_path.resolve()))
    audio_channel = "Stereo" if channels >= 2 else "Mono"

    script = f"""
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Speech
$text = [System.Text.Encoding]::UTF8.GetString([System.Convert]::FromBase64String('{text_b64}'))
$path = [System.Text.Encoding]::UTF8.GetString([System.Convert]::FromBase64String('{path_b64}'))
$bits = [System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen
$channel = [System.Speech.AudioFormat.AudioChannel]::{audio_channel}
$format = [System.Speech.AudioFormat.SpeechAudioFormatInfo]::new({int(sample_rate)}, $bits, $channel)
$synth = New-Object System.Speech.Synthesis.SpeechSynthesizer
try {{
    $zhVoice = $synth.GetInstalledVoices() |
        Where-Object {{ $_.Enabled -and $_.VoiceInfo.Culture.Name -like 'zh-*' }} |
        Select-Object -First 1
    if ($null -ne $zhVoice) {{
        $synth.SelectVoice($zhVoice.VoiceInfo.Name)
    }}
    $voiceName = $synth.Voice.Name
    $synth.Rate = 0
    $synth.Volume = 100
    $synth.SetOutputToWaveFile($path, $format)
    $synth.Speak($text)
    Write-Output $voiceName
}} finally {{
    $synth.Dispose()
}}
"""

    result = subprocess.run(
        [
            "powershell.exe",
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-EncodedCommand",
            _powershell_encoded(script),
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=45,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "Unknown System.Speech error").strip()
        raise RuntimeError(f"Windows TTS generation failed: {detail}")
    if not output_path.exists() or output_path.stat().st_size <= 44:
        raise RuntimeError("Windows TTS did not create a valid WAV file.")

    return {
        "text": clean_text,
        "voice": (result.stdout or "").strip() or None,
        "wav_path": str(output_path),
    }


def play_gpt_in_test_speech(
    audio_manager,
    *,
    text: str = DEFAULT_TEST_TEXT,
) -> dict[str, Any]:
    """Synthesize real speech and play it into the configured GPT_IN virtual speaker."""
    device_index = audio_manager._resolve_key("gpt_in")
    pyaudio = audio_manager._load_backend() if hasattr(audio_manager, "_load_backend") else None
    if pyaudio is None:
        try:
            from bridge.audio_capture import _load_pyaudio
        except ModuleNotFoundError:
            from audio_capture import _load_pyaudio
        pyaudio = _load_pyaudio()

    audio = pyaudio.PyAudio()
    stream = None
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    wav_path = Path(audio_manager.capture_dir) / f"gpt-in-speech-{stamp}.wav"
    try:
        info = dict(audio.get_device_info_by_index(device_index))
        channels = max(1, min(int(info.get("maxOutputChannels") or 2), 2))
        sample_rate = int(float(info.get("defaultSampleRate") or 48000))
        synthesis = synthesize_windows_speech(
            text,
            wav_path,
            sample_rate=sample_rate,
            channels=channels,
        )

        with wave.open(str(wav_path), "rb") as reader:
            if reader.getsampwidth() != 2:
                raise RuntimeError("GPT_IN speech test WAV is not PCM16.")
            wav_channels = reader.getnchannels()
            wav_rate = reader.getframerate()
            total_frames = reader.getnframes()
            stream = audio.open(
                format=pyaudio.paInt16,
                channels=wav_channels,
                rate=wav_rate,
                output=True,
                output_device_index=device_index,
                frames_per_buffer=1024,
            )
            # A short leading silence prevents the browser from clipping the first syllable.
            stream.write(bytes(int(wav_rate * 0.25) * wav_channels * 2))
            while True:
                data = reader.readframes(1024)
                if not data:
                    break
                stream.write(data)
            # Keep the route silent briefly so ChatGPT can detect the end of the spoken turn.
            stream.write(bytes(int(wav_rate * 0.8) * wav_channels * 2))

        return {
            "played": True,
            "role": "gpt_in",
            "kind": "speech",
            "device": {
                "index": device_index,
                "name": str(info.get("name", device_index)),
            },
            "duration_seconds": round(total_frames / wav_rate, 3),
            "sample_rate": wav_rate,
            "channels": wav_channels,
            "text": synthesis["text"],
            "voice": synthesis["voice"],
            "wav_path": synthesis["wav_path"],
            "microphone_hint": (audio_manager._routes.get("gpt_in") or {}).get(
                "microphone_device_name"
            ),
            "message": (
                "A spoken test phrase was sent into GPT_IN. "
                "ChatGPT Live should transcribe it and reply after the route becomes silent."
            ),
        }
    finally:
        if stream is not None:
            try:
                stream.stop_stream()
            except Exception:
                pass
            stream.close()
        audio.terminate()
