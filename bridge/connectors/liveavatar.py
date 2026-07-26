"""LiveAvatar LITE connector placeholder.

Next implementation milestone:
1. Request a short-lived LiveAvatar session token from the ALiver server.
2. Join LiveKit or Agora using the provider SDK.
3. Accept PCM frames captured from the ChatGPT browser output.
4. Publish audio frames to the avatar agent.
5. Subscribe to synchronized avatar audio/video tracks.
6. Render them in a local borderless player window for Douyin Live Companion.

The control plane and Bridge command route are already implemented; this module is
intentionally isolated so SDK changes do not affect the rest of ALiver.
"""
