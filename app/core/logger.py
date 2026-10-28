"""Structured logging configuration using Loguru."""

from __future__ import annotations

from pathlib import Path
import sys
from typing import Any

from loguru import logger


def configure_logging(app_env: str = "production", debug: bool = False) -> None:
    logger.remove()

    log_level = "DEBUG" if debug else "INFO"
    log_format = (
        "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | "
        "<level>{level: <8}</level> | "
        "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> | "
        "<level>{message}</level>"
    )

    logger.add(sys.stdout, level=log_level, format=log_format, colorize=True, enqueue=True)

    log_dir = Path("logs")
    log_dir.mkdir(exist_ok=True)

    logger.add(
        log_dir / "app.log",
        level="INFO",
        format=log_format,
        rotation="10 MB",
        retention="30 days",
        compression="gz",
        enqueue=True,
        colorize=False,
    )

    logger.add(
        log_dir / "error.log",
        level="ERROR",
        format=log_format,
        rotation="10 MB",
        retention="90 days",
        compression="gz",
        enqueue=True,
        colorize=False,
        backtrace=True,
        diagnose=True,
    )


def get_logger(name: str) -> Any:
    return logger.bind(name=name)
