from __future__ import annotations

import ctypes
import os
import threading
import time
from ctypes import wintypes
from functools import lru_cache
from typing import Any

from bridge.audio_capture import normalize_device_name
from bridge.runtime_diagnostics import event

MMSYSERR_NOERROR = 0
WAVERR_STILLPLAYING = 33
WAVE_FORMAT_PCM = 1
WAVE_MAPPER = 0xFFFFFFFF
CALLBACK_NULL = 0
WHDR_DONE = 0x00000001
MAXPNAMELEN = 32


class WAVEFORMATEX(ctypes.Structure):
    _fields_ = [
        ("wFormatTag", wintypes.WORD),
        ("nChannels", wintypes.WORD),
        ("nSamplesPerSec", wintypes.DWORD),
        ("nAvgBytesPerSec", wintypes.DWORD),
        ("nBlockAlign", wintypes.WORD),
        ("wBitsPerSample", wintypes.WORD),
        ("cbSize", wintypes.WORD),
    ]


class WAVEHDR(ctypes.Structure):
    _fields_ = [
        ("lpData", ctypes.c_void_p),
        ("dwBufferLength", wintypes.DWORD),
        ("dwBytesRecorded", wintypes.DWORD),
        ("dwUser", ctypes.c_size_t),
        ("dwFlags", wintypes.DWORD),
        ("dwLoops", wintypes.DWORD),
        ("lpNext", ctypes.c_void_p),
        ("reserved", ctypes.c_size_t),
    ]


class WAVEOUTCAPSW(ctypes.Structure):
    _fields_ = [
        ("wMid", wintypes.WORD),
        ("wPid", wintypes.WORD),
        ("vDriverVersion", wintypes.DWORD),
        ("szPname", wintypes.WCHAR * MAXPNAMELEN),
        ("dwFormats", wintypes.DWORD),
        ("wChannels", wintypes.WORD),
        ("wReserved1", wintypes.WORD),
        ("dwSupport", wintypes.DWORD),
    ]


@lru_cache(maxsize=1)
def _winmm():
    if os.name != "nt":
        raise RuntimeError("Windows waveOut audio is available only on Windows.")
    dll = ctypes.WinDLL("winmm", use_last_error=True)
    dll.waveOutGetNumDevs.argtypes = []
    dll.waveOutGetNumDevs.restype = wintypes.UINT
    dll.waveOutGetDevCapsW.argtypes = [ctypes.c_size_t, ctypes.POINTER(WAVEOUTCAPSW), wintypes.UINT]
    dll.waveOutGetDevCapsW.restype = wintypes.UINT
    dll.waveOutOpen.argtypes = [
        ctypes.POINTER(ctypes.c_void_p),
        wintypes.UINT,
        ctypes.POINTER(WAVEFORMATEX),
        ctypes.c_size_t,
        ctypes.c_size_t,
        wintypes.DWORD,
    ]
    dll.waveOutOpen.restype = wintypes.UINT
    dll.waveOutPrepareHeader.argtypes = [ctypes.c_void_p, ctypes.POINTER(WAVEHDR), wintypes.UINT]
    dll.waveOutPrepareHeader.restype = wintypes.UINT
    dll.waveOutWrite.argtypes = [ctypes.c_void_p, ctypes.POINTER(WAVEHDR), wintypes.UINT]
    dll.waveOutWrite.restype = wintypes.UINT
    dll.waveOutUnprepareHeader.argtypes = [ctypes.c_void_p, ctypes.POINTER(WAVEHDR), wintypes.UINT]
    dll.waveOutUnprepareHeader.restype = wintypes.UINT
    dll.waveOutReset.argtypes = [ctypes.c_void_p]
    dll.waveOutReset.restype = wintypes.UINT
    dll.waveOutClose.argtypes = [ctypes.c_void_p]
    dll.waveOutClose.restype = wintypes.UINT
    dll.waveOutGetErrorTextW.argtypes = [wintypes.UINT, wintypes.LPWSTR, wintypes.UINT]
    dll.waveOutGetErrorTextW.restype = wintypes.UINT
    return dll


def _error_text(code: int) -> str:
    if code == MMSYSERR_NOERROR:
        return "success"
    try:
        buffer = ctypes.create_unicode_buffer(256)
        result = _winmm().waveOutGetErrorTextW(code, buffer, len(buffer))
        if result == MMSYSERR_NOERROR and buffer.value:
            return buffer.value
    except Exception:
        pass
    return f"WinMM error {code}"


def _check(code: int, operation: str) -> None:
    if code != MMSYSERR_NOERROR:
        raise RuntimeError(f"{operation} failed: {_error_text(code)}")


def list_waveout_devices() -> list[dict[str, Any]]:
    dll = _winmm()
    rows: list[dict[str, Any]] = []
    for index in range(int(dll.waveOutGetNumDevs())):
        caps = WAVEOUTCAPSW()
        code = int(dll.waveOutGetDevCapsW(index, ctypes.byref(caps), ctypes.sizeof(caps)))
        if code != MMSYSERR_NOERROR:
            continue
        rows.append(
            {
                "index": index,
                "name": str(caps.szPname),
                "channels": int(caps.wChannels),
                "formats": int(caps.dwFormats),
                "is_mapper": False,
            }
        )
    return rows


def _name_match_score(candidate: str, wanted: str) -> int | None:
    left = normalize_device_name(candidate)
    right = normalize_device_name(wanted)
    if not left or not right:
        return None
    if left == right:
        return 0
    # WinMM device names are commonly truncated to 31 characters.
    if left.startswith(right) or right.startswith(left):
        return 1
    compact_left = left.replace(" ", "")
    compact_right = right.replace(" ", "")
    if compact_left.startswith(compact_right) or compact_right.startswith(compact_left):
        return 2
    important = [token for token in right.replace("(", " ").replace(")", " ").split() if len(token) >= 4]
    if important and all(token in left for token in important[:3]):
        return 3
    return None


def choose_waveout_device(
    devices: list[dict[str, Any]],
    *,
    preferred_name: str = "",
    preferred_index: int | None = None,
    auto_live_out: bool = True,
) -> dict[str, Any]:
    wanted = str(preferred_name or "").strip()
    if wanted:
        matches = [
            (score, row)
            for row in devices
            if (score := _name_match_score(str(row.get("name") or ""), wanted)) is not None
        ]
        if matches:
            return dict(sorted(matches, key=lambda item: (item[0], int(item[1]["index"])))[0][1])
        raise RuntimeError(f"未找到配置的 LIVE_OUT 音频设备：{wanted}")

    if preferred_index is not None:
        match = next((row for row in devices if int(row.get("index", -1)) == int(preferred_index)), None)
        if match:
            return dict(match)

    if auto_live_out:
        priorities = (
            "cable-b input",
            "live_out",
            "live out",
            "voicemeeter aux input",
            "voicemeeter vaio3 input",
        )
        for keyword in priorities:
            match = next(
                (
                    row
                    for row in devices
                    if keyword in normalize_device_name(str(row.get("name") or ""))
                ),
                None,
            )
            if match:
                return dict(match)

    return {
        "index": WAVE_MAPPER,
        "name": "Windows 默认播放设备",
        "channels": 2,
        "formats": 0,
        "is_mapper": True,
    }


class WindowsWaveOutStream:
    def __init__(
        self,
        *,
        device_index: int,
        sample_rate: int,
        channels: int,
        bits_per_sample: int = 16,
    ) -> None:
        if os.name != "nt":
            raise RuntimeError("Windows waveOut audio is available only on Windows.")
        self._dll = _winmm()
        self._lock = threading.RLock()
        self._handle = ctypes.c_void_p()
        self._closed = False
        self.sample_rate = int(sample_rate)
        self.channels = int(channels)
        self.bits_per_sample = int(bits_per_sample)
        block_align = self.channels * self.bits_per_sample // 8
        self._format = WAVEFORMATEX(
            wFormatTag=WAVE_FORMAT_PCM,
            nChannels=self.channels,
            nSamplesPerSec=self.sample_rate,
            nAvgBytesPerSec=self.sample_rate * block_align,
            nBlockAlign=block_align,
            wBitsPerSample=self.bits_per_sample,
            cbSize=0,
        )
        code = int(
            self._dll.waveOutOpen(
                ctypes.byref(self._handle),
                ctypes.c_uint(device_index).value,
                ctypes.byref(self._format),
                0,
                0,
                CALLBACK_NULL,
            )
        )
        _check(code, "waveOutOpen")

    def get_output_latency(self) -> float:
        # The renderer writes 20 ms PCM chunks; use one chunk as the conservative audible offset.
        return 0.02

    def write(self, data: bytes) -> None:
        if not data:
            return
        with self._lock:
            if self._closed or not self._handle:
                raise RuntimeError("waveOut stream is closed.")
            block_align = self.channels * self.bits_per_sample // 8
            usable = len(data) - (len(data) % block_align)
            if usable <= 0:
                return
            buffer = ctypes.create_string_buffer(data[:usable])
            header = WAVEHDR(
                lpData=ctypes.cast(buffer, ctypes.c_void_p),
                dwBufferLength=usable,
                dwBytesRecorded=0,
                dwUser=0,
                dwFlags=0,
                dwLoops=0,
                lpNext=None,
                reserved=0,
            )
            size = ctypes.sizeof(header)
            _check(
                int(self._dll.waveOutPrepareHeader(self._handle, ctypes.byref(header), size)),
                "waveOutPrepareHeader",
            )
            try:
                _check(int(self._dll.waveOutWrite(self._handle, ctypes.byref(header), size)), "waveOutWrite")
                while not self._closed and not (int(header.dwFlags) & WHDR_DONE):
                    time.sleep(0.001)
            finally:
                for _ in range(2000):
                    code = int(self._dll.waveOutUnprepareHeader(self._handle, ctypes.byref(header), size))
                    if code == MMSYSERR_NOERROR:
                        break
                    if code != WAVERR_STILLPLAYING:
                        _check(code, "waveOutUnprepareHeader")
                    time.sleep(0.001)

    def stop_stream(self) -> None:
        with self._lock:
            if not self._closed and self._handle:
                self._dll.waveOutReset(self._handle)

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            if self._handle:
                self._dll.waveOutReset(self._handle)
                self._dll.waveOutClose(self._handle)
                self._handle = ctypes.c_void_p()


def install_simli_waveout_patch(renderer_class: type) -> None:
    if getattr(renderer_class, "_aliver_waveout_v1", False):
        return
    original_open_audio_output = renderer_class._open_audio_output

    def patched_open_audio_output(self) -> None:
        if os.name != "nt":
            original_open_audio_output(self)
            return
        if not self.play_return_audio:
            self._metrics["warning"] = "play_return_audio=false：只同步画面，不向直播输出声音。"
            self._metrics["audio_output_backend"] = "disabled"
            return

        devices = list_waveout_devices()
        info = choose_waveout_device(
            devices,
            preferred_name=self.audio_output_device_name,
            preferred_index=self.audio_output_device_index,
            auto_live_out=self.auto_live_out,
        )
        selected_index = int(info["index"])
        try:
            stream = WindowsWaveOutStream(
                device_index=selected_index,
                sample_rate=48000,
                channels=2,
                bits_per_sample=16,
            )
        except Exception as exc:
            event(
                "simli_waveout_open_failed",
                device_index=selected_index,
                device_name=str(info.get("name") or selected_index),
                error=f"{type(exc).__name__}: {exc}",
            )
            raise RuntimeError(
                "无法打开 Windows waveOut 播放设备。请确认 LIVE_OUT 虚拟扬声器存在且未被独占。"
            ) from exc

        self._audio = None
        self._audio_stream = stream
        self._audio_output_latency = stream.get_output_latency()
        self._metrics.update(
            {
                "audio_output_backend": "winmm_waveout",
                "audio_output_device": str(info.get("name") or selected_index),
                "audio_output_device_index": selected_index,
                "audio_output_latency_ms": round(self._audio_output_latency * 1000, 1),
            }
        )
        if bool(info.get("is_mapper")):
            self._metrics["warning"] = (
                "未发现独立 LIVE_OUT 虚拟声卡，当前同步音频输出到 Windows 默认播放设备。"
            )
        event(
            "simli_waveout_opened",
            device_index=selected_index,
            device_name=str(info.get("name") or selected_index),
            mapper=bool(info.get("is_mapper")),
            latency_ms=round(self._audio_output_latency * 1000, 1),
        )

    renderer_class._open_audio_output = patched_open_audio_output
    renderer_class._aliver_waveout_v1 = True
