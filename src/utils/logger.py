"""Fixed logger.py"""
from loguru import logger
import sys
from config.settings import settings


def setup_logger():
    """Configure loguru: file + console, level from settings."""
    log_dir = settings.LOG_DIR
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "trading_system.log"

    logger.remove()

    logger.add(
        log_file,
        rotation="10 MB",
        retention="30 days",
        level=settings.LOG_LEVEL,
        format="{time:YYYY-MM-DD HH:mm:ss} | {level} | {name}:{function}:{line} | {message}",
    )

    logger.add(
        sys.stderr,
        level=settings.LOG_LEVEL,
        format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | {message}",
    )
    return logger


setup_logger()
