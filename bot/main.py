"""
Bot entry point.
Run:  python -m bot.main
"""
import asyncio
import sys
from .core.logger import setup_logger
from .core.bot import main

log = setup_logger("securitybot.main")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("Received KeyboardInterrupt, exiting.")
        sys.exit(0)
    except Exception as e:
        log.exception("Fatal: %s", e)
        sys.exit(1)
