"""Deepslate Voice Bridge entrypoint."""

from __future__ import annotations

import asyncio
import logging
import signal

from app.bridge import Bridge
from app.config import Settings
from app.device_server import DeviceConnection, DeviceServer
from app.ha_client import HAClient

logger = logging.getLogger("bridge")


async def run() -> None:
    settings = Settings.load()
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    if not (settings.vendor_id and settings.org_id and settings.api_key):
        raise SystemExit(
            "Missing Deepslate credentials: set vendor_id, org_id and api_key "
            "in the add-on options (or DEEPSLATE_* env vars)."
        )

    ha = HAClient(settings.ha_url, settings.ha_token)

    async def handler_factory(conn: DeviceConnection) -> Bridge:
        bridge = Bridge(conn, settings, ha)
        await bridge.start()
        return bridge

    server = DeviceServer(settings, handler_factory)
    await server.start()
    logger.info("Deepslate Voice Bridge up — waiting for Voice PE devices")

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    await stop.wait()
    logger.info("shutting down")
    await server.stop()
    await ha.close()


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
