"""
Structured logging with rotation.
"""
from __future__ import annotations

import logging
import logging.handlers
import os
import sys
from pathlib import Path


class ColorFormatter(logging.Formatter):
    COLORS = {
        logging.DEBUG: "\033[36m",
        logging.INFO: "\033[32m",
        logging.WARNING: "\033[33m",
        logging.ERROR: "\033[31m",
        logging.CRITICAL: "\033[35m",
    }
    RESET = "\033[0m"

    def format(self, record: logging.LogRecord) -> str:
        color = self.COLORS.get(record.levelno, self.RESET)
        record.levelname = f"{color}{record.levelname:<8}{self.RESET}"
        return super().format(record)


def setup_logger(name: str = "securitybot", level: str = "INFO", log_file: str = "./logs/bot.log") -> logging.Logger:
    """Configure and return a logger with console + rotating file output."""
    log_path = Path(log_file)
    log_path.parent.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger(name)
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))

    if logger.handlers:
        return logger

    fmt = "%(asctime)s │ %(levelname)s │ %(name)s │ %(message)s"
    datefmt = "%Y-%m-%d %H:%M:%S"

    # Console
    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(ColorFormatter(fmt, datefmt))
    logger.addHandler(console)

    # Rotating file (5 MB x 10 files)
    file_handler = logging.handlers.RotatingFileHandler(
        log_file, maxBytes=5 * 1024 * 1024, backupCount=10, encoding="utf-8"
    )
    file_handler.setFormatter(logging.Formatter(fmt, datefmt))
    logger.addHandler(file_handler)

    logger.propagate = False
    return logger
