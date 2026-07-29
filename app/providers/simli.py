from __future__ import annotations

import time
from typing import Any

import httpx

from app.providers.base import AvatarProvider, ProviderResult

DEFAULT_API_BASE_URL = "https://api.simli.ai"
NETWORK_MODES = {"inherit", "no_proxy", "direct_env"}


class SimliProvider(AvatarProvider):
    """Bridge-managed realtime speech-to-video provider.

    The control server owns encrypted credentials and performs API validation.
    The Windows Bridge owns GPT_OUT capture, PCM conversion, the Simli SDK and
    the local avatar window that can be captured by streaming software.
    """

    provider_type = "simli"
    execution_mode = "bridge"

    def _runtime_config(self, overrides: dict[str, Any] | None = None) -> dict[str, Any]:
        values = dict(self.context.settings)
        values.update(overrides or {})
        api_key = str(self.context.credentials.get("api_key", "")).strip()
        face_id = str(values.get("face_id") or values.get("faceId") or "").strip()
        if not api_key:
            raise ValueError("Missing credentials.api_key")
        if not face_id:
            raise ValueError("Missing settings.face_id")

        transport = str(values.get("transport", "livekit")).strip().lower()
        if transport not in {"livekit", "p2p"}:
            raise ValueError("settings.transport must be livekit or p2p")
        model = str(values.get("model", "fasttalk")).strip().lower()
        if model not in {"fasttalk", "artalk"}:
            raise ValueError("settings.model must be fasttalk or artalk")
        network_mode = str(values.get("network_mode", "direct_env")).strip().lower()
        if network_mode not in NETWORK_MODES:
            raise ValueError("settings.network_mode must be inherit, no_proxy or direct_env")

        window_size = values.get("window_size", [720, 720])
        if not isinstance(window_size, list) or len(window_size) != 2:
            window_size = [720, 720]

        return {
            "api_key": api_key,
            "api_base_url": str(self.context.api_base_url or DEFAULT_API_BASE_URL).rstrip("/"),
            "face_id": face_id,
            "transport": transport,
            "model": model,
            "handle_silence": bool(values.get("handle_silence", True)),
            "max_session_length": max(60, min(int(values.get("max_session_length", 3600)), 14400)),
            "max_idle_time": max(10, min(int(values.get("max_idle_time", 300)), 3600)),
            "window_title": str(values.get("window_title", "ALiver Simli Avatar"))[:120],
            "window_size": [max(240, int(window_size[0])), max(240, int(window_size[1]))],
            "always_on_top": bool(values.get("always_on_top", False)),
            "play_return_audio": bool(values.get("play_return_audio", True)),
            "audio_output_device_index": values.get("audio_output_device_index"),
            "retry_count": max(1, min(int(values.get("retry_count", 2)), 8)),
            "retry_timeout": max(1.0, min(float(values.get("retry_timeout", 8.0)), 60.0)),
            "network_mode": network_mode,
            "low_latency_idle_trim": bool(values.get("low_latency_idle_trim", True)),
            "idle_trim_arm_seconds": max(
                0.25, min(float(values.get("idle_trim_arm_seconds", 0.8)), 5.0)
            ),
            "idle_trim_target_audio_ms": max(
                180, min(int(values.get("idle_trim_target_audio_ms", 420)), 1500)
            ),
            "idle_trim_target_video_ms": max(
                150, min(int(values.get("idle_trim_target_video_ms", 500)), 2000)
            ),
        }

    async def test_connection(self) -> ProviderResult:
        started = time.perf_counter()
        try:
            config = self._runtime_config()
        except ValueError as exc:
            return ProviderResult(success=False, error=str(exc))

        request_body = {
            "faceId": config["face_id"],
            "apiVersion": "v2",
            "handleSilence": config["handle_silence"],
            "maxSessionLength": 60,
            "maxIdleTime": 15,
            "model": config["model"],
        }
        try:
            async with httpx.AsyncClient(
                timeout=20.0,
                trust_env=config["network_mode"] == "inherit",
            ) as client:
                response = await client.post(
                    f"{config['api_base_url']}/compose/token",
                    headers={
                        "x-simli-api-key": config["api_key"],
                        "Content-Type": "application/json",
                    },
                    json=request_body,
                )
                response.raise_for_status()
                payload = response.json()
            if not payload.get("session_token"):
                return ProviderResult(
                    success=False,
                    error="Simli did not return session_token",
                    data={"response_keys": sorted(payload.keys())},
                )
            return ProviderResult(
                success=True,
                data={
                    "message": "Simli API key and Face ID are valid",
                    "face_id": config["face_id"],
                    "transport": config["transport"],
                    "model": config["model"],
                    "network_mode": config["network_mode"],
                    "execution_mode": self.execution_mode,
                },
                latency_ms=round((time.perf_counter() - started) * 1000),
            )
        except httpx.HTTPStatusError as exc:
            detail = exc.response.text[:500]
            return ProviderResult(
                success=False,
                error=f"Simli HTTP {exc.response.status_code}: {detail}",
                latency_ms=round((time.perf_counter() - started) * 1000),
            )
        except (httpx.HTTPError, ValueError) as exc:
            return ProviderResult(
                success=False,
                error=f"Simli connection failed: {exc}",
                latency_ms=round((time.perf_counter() - started) * 1000),
            )

    async def create_session(self, overrides: dict[str, Any]) -> ProviderResult:
        try:
            config = self._runtime_config(overrides)
        except ValueError as exc:
            return ProviderResult(success=False, error=str(exc))
        return ProviderResult(
            success=True,
            data={
                "execution_mode": "bridge",
                "command_type": "provider.start_session",
                "provider_type": self.provider_type,
                "config": config,
            },
        )

    async def stop_session(
        self, external_session_id: str | None, session_data: dict[str, Any]
    ) -> ProviderResult:
        return ProviderResult(
            success=True,
            external_session_id=external_session_id,
            data={
                "execution_mode": "bridge",
                "command_type": "provider.stop_session",
                "provider_type": self.provider_type,
            },
        )
