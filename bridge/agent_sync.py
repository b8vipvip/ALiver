from __future__ import annotations

import asyncio

from bridge import agent, simli_session
from bridge.simli_sync import install_simli_sync_patch

BRIDGE_VERSION = "0.5.0"


def install() -> None:
    install_simli_sync_patch(simli_session)
    agent.BRIDGE_VERSION = BRIDGE_VERSION
    original_capabilities = agent.BridgeAgent.capabilities
    original_execute = agent.BridgeAgent.execute

    def capabilities() -> list[str]:
        values = list(original_capabilities())
        for item in ("provider.simli.av_sync", "audio.live_out.auto"):
            if item not in values:
                values.append(item)
        return values

    async def execute(self, command_type, payload):
        if command_type == "provider.simli.status":
            return self.simli.status()
        return await original_execute(self, command_type, payload)

    agent.BridgeAgent.capabilities = staticmethod(capabilities)
    agent.BridgeAgent.execute = execute


async def main() -> None:
    install()
    await agent.main()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
