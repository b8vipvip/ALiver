#!/usr/bin/env python3
"""Apply ALiver's maintained patch set to the vendored LiveTalking source."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LIVETALKING = ROOT / "services" / "livetalking"


def replace_once(path: Path, old: str, new: str) -> None:
    content = path.read_text(encoding="utf-8")
    if new in content:
        return
    if old not in content:
        raise RuntimeError(f"Patch context not found in {path}: {old[:100]!r}")
    path.write_text(content.replace(old, new, 1), encoding="utf-8")


def patch_routes() -> None:
    path = LIVETALKING / "server" / "routes.py"
    replace_once(
        path,
        "from server.avatar_routes import setup_avatar_routes\n",
        "from server.avatar_routes import setup_avatar_routes\n"
        "from aliver_integration import setup_aliver_routes\n",
    )
    replace_once(
        path,
        "    # 注册 avatar 生成相关的路由\n"
        "    setup_avatar_routes(app)\n\n"
        "    app.router.add_static('/', path='web')\n",
        "    # 注册 avatar 生成相关的路由\n"
        "    setup_avatar_routes(app)\n\n"
        "    # ALiver: authenticated realtime PCM input and health endpoints.\n"
        "    setup_aliver_routes(app)\n\n"
        "    app.router.add_static('/', path='web')\n",
    )


def patch_rtc_manager() -> None:
    path = LIVETALKING / "server" / "rtc_manager.py"
    replace_once(path, "import copy\n", "import copy\nimport os\n")
    replace_once(
        path,
        "        # 添加发送轨道\n"
        "        from server.webrtc import HumanPlayer\n"
        "        player = HumanPlayer(avatar_session)\n"
        "        pc.addTrack(player.audio)\n"
        "        pc.addTrack(player.video)\n",
        "        # 添加发送轨道。ALiver 默认只返回视频，直播声音继续由 CABLE-B 唯一路径输出。\n"
        "        from server.webrtc import HumanPlayer\n"
        "        env_video_only = os.environ.get('ALIVER_VIDEO_ONLY', '1').strip().lower() not in {'0', 'false', 'no'}\n"
        "        video_only = bool(params.get('video_only', env_video_only))\n"
        "        player = HumanPlayer(avatar_session, audio_enabled=not video_only)\n"
        "        if not video_only:\n"
        "            pc.addTrack(player.audio)\n"
        "        pc.addTrack(player.video)\n"
        "        logger.info('ALiver WebRTC session %s video_only=%s', sessionid, video_only)\n",
    )
    replace_once(
        path,
        "        transceiver = pc.getTransceivers()[1]\n"
        "        transceiver.setCodecPreferences(preferences)\n",
        "        transceiver = next(\n"
        "            (item for item in pc.getTransceivers() if getattr(item, 'kind', None) == 'video'),\n"
        "            None,\n"
        "        )\n"
        "        if transceiver is not None:\n"
        "            transceiver.setCodecPreferences(preferences)\n",
    )
    replace_once(
        path,
        "        from server.webrtc import HumanPlayer\n"
        "        player = HumanPlayer(avatar_session)\n"
        "        pc.addTrack(player.audio)\n"
        "        pc.addTrack(player.video)\n\n"
        "        await pc.setLocalDescription(await pc.createOffer())\n",
        "        from server.webrtc import HumanPlayer\n"
        "        video_only = os.environ.get('ALIVER_VIDEO_ONLY', '1').strip().lower() not in {'0', 'false', 'no'}\n"
        "        player = HumanPlayer(avatar_session, audio_enabled=not video_only)\n"
        "        if not video_only:\n"
        "            pc.addTrack(player.audio)\n"
        "        pc.addTrack(player.video)\n\n"
        "        await pc.setLocalDescription(await pc.createOffer())\n",
    )


def patch_webrtc() -> None:
    path = LIVETALKING / "server" / "webrtc.py"
    replace_once(
        path,
        "        self.__audio = PlayerStreamTrack(self, kind=\"audio\")\n"
        "        self.__video = PlayerStreamTrack(self, kind=\"video\")\n",
        "        self.__audio = (\n"
        "            PlayerStreamTrack(self, kind=\"audio\") if audio_enabled else None\n"
        "        )\n"
        "        self.__video = PlayerStreamTrack(self, kind=\"video\")\n",
    )
    replace_once(
        path,
        "    def __init__(\n"
        "        self, avatar_session, format=None, options=None, timeout=None, loop=False, decode=True\n"
        "    ):\n",
        "    def __init__(\n"
        "        self, avatar_session, format=None, options=None, timeout=None, loop=False, decode=True,\n"
        "        audio_enabled=True,\n"
        "    ):\n",
    )
    replace_once(
        path,
        "    def push_audio(self, frame, eventpoint=None):\n"
        "        from av import AudioFrame\n",
        "    def push_audio(self, frame, eventpoint=None):\n"
        "        if self.__audio is None:\n"
        "            return\n"
        "        from av import AudioFrame\n",
    )
    replace_once(
        path,
        "    def get_buffer_size(self) -> int:\n"
        "        return self.__video._queue.qsize()\n",
        "    def clear_queues(self) -> dict:\n"
        "        cleared = {}\n"
        "        for name, track in ((\"audio\", self.__audio), (\"video\", self.__video)):\n"
        "            count = 0\n"
        "            if track is not None:\n"
        "                while not track._queue.empty():\n"
        "                    try:\n"
        "                        track._queue.get_nowait()\n"
        "                        count += 1\n"
        "                    except queue.Empty:\n"
        "                        break\n"
        "            cleared[name] = count\n"
        "        return cleared\n\n"
        "    def get_buffer_size(self) -> int:\n"
        "        return self.__video._queue.qsize()\n",
    )


def main() -> None:
    required = LIVETALKING / "UPSTREAM.json"
    if not required.exists():
        raise SystemExit("Run scripts/vendor_livetalking.py first")
    patch_routes()
    patch_rtc_manager()
    patch_webrtc()
    print("Applied ALiver LiveTalking patch set")


if __name__ == "__main__":
    main()
