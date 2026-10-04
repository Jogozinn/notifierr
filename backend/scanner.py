"""HTTP-free scanner entrypoint: python -m backend.scanner."""

import asyncio
import os
import signal


async def run() -> None:
    os.environ.setdefault("NOTIFIERR_ROLE", "scanner")
    from . import main as runtime

    if runtime.settings.process_role != "scanner":
        raise RuntimeError("Scanner entrypoint requires NOTIFIERR_ROLE=scanner")
    if not runtime.settings.background_poll_enabled:
        raise RuntimeError("Scanner requires BACKGROUND_POLL_ENABLED=true")
    if not runtime.settings.ebay_configured:
        raise RuntimeError("Scanner requires EBAY_CLIENT_ID and EBAY_CLIENT_SECRET")

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for name in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(name, stop.set)
        except NotImplementedError:  # Windows event loop
            signal.signal(name, lambda *_: loop.call_soon_threadsafe(stop.set))
    async with runtime.lifespan(runtime.app):
        runtime.logger.info("Scanner service started worker_id=%s", runtime.WORKER_ID)
        await stop.wait()
        runtime.logger.info("Scanner service stopping worker_id=%s", runtime.WORKER_ID)


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
