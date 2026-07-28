from __future__ import annotations

from typing import Any

from bridge.simli_sync import AUDIO_CHANNELS, AUDIO_RATE, AudioChunk, frame_time_seconds, interleaved_pcm16


async def receive_audio_current_sdk(self: Any) -> None:
    """Receive current Simli SDK audio frames; the iterator takes no rate argument."""
    try:
        from av.audio.resampler import AudioResampler
    except ImportError as exc:
        raise RuntimeError("缺少 PyAV，无法处理 Simli 返回音频。") from exc

    self._audio_resampler = AudioResampler(format="s16", layout="stereo", rate=AUDIO_RATE)
    async for frame in self.client.getAudioStreamIterator():
        if self.stop_event.is_set() or frame is None:
            break
        output_frames = self._audio_resampler.resample(frame)
        if output_frames is None:
            continue
        if not isinstance(output_frames, (list, tuple)):
            output_frames = [output_frames]
        for output_frame in output_frames:
            pcm = interleaved_pcm16(output_frame.to_ndarray())
            samples = len(pcm) // (2 * AUDIO_CHANNELS)
            if samples <= 0:
                continue
            timestamp = frame_time_seconds(output_frame)
            if timestamp is None:
                timestamp = frame_time_seconds(frame)
            chunk = AudioChunk(pcm=pcm, timestamp=timestamp, samples=samples)
            if self._first_audio_timestamp is None and timestamp is not None:
                self._first_audio_timestamp = timestamp
            await self._put_audio(chunk)
            self._metrics["audio_frames_received"] += 1
            self._audio_ready.set()
    self.stop_event.set()


def install_audio_iterator_compat(renderer_class: type) -> None:
    renderer_class._receive_audio = receive_audio_current_sdk
