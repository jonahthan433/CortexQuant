"""Fixed logger.py"""
from loguru import logger
import sys
import os
from config.settings import settings

def setup_logger():
    """Configure loguru: file + console, level from settings."""
    log_dir = settings.LOG_DIR
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "trading_system.log"

    # Remove default handler
    logger.remove()

    # File handler: rotation 10MB, retention 30 days, level from settings
    logger.add(
        log_file,
        rotation="10 MB",
        retention="30 days",
        level=settings.LOG_LEVEL,
        format="{time:YYYY-MM-DD HH:mm:ss} | {level} | {name}:{function}:{line} | {message}",
    )

    # Console handler
    logger.add(
        sys.stderr,
        level=settings.LOG_LEVEL,
        format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | {message}",
    )
    return logger


# Configure at import time so all modules using `from loguru import logger`
# get the configured logger automatically.
setup_logger()
