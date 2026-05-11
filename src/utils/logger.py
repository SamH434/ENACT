"""
Centralized logging configuration for ENACT.

Single configured logger used across all modules. Outputs to both console
(readable by humans) and a rotating log file (for later parsing).
"""

import logging
import logging.handlers
from pathlib import Path

# This is will work regardless of where the script is launched from
LOG_DIR = Path(__file__).resolve().parents[2] / "logs"
LOG_FILE = LOG_DIR / "enact.log"

_CONSOLE_FMT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
_FILE_FMT = "%(asctime)s | %(levelname)s | %(name)s | %(message)s"


def get_logger(name: str) -> logging.Logger:
    logger = logging.getLogger(name)
    """
    The if statement below lets every module call get_logger() without
    having to duplicate handlers.
    """
    if logger.handlers:
        return logger  # already configured

    logger.setLevel(logging.DEBUG)

    LOG_DIR.mkdir(parents=True, exist_ok=True)

    console = logging.StreamHandler()
    console.setLevel(logging.INFO)
    console.setFormatter(logging.Formatter(_CONSOLE_FMT, datefmt="%H:%M:%S"))
    logger.addHandler(console)

    """
    Rotating file handler maxes the log at 1 MB and
    keeps 3 backups, this is so the long running telemetry
    tool won't eventually fill up the disk
    """
    file_handler = logging.handlers.RotatingFileHandler(
        LOG_FILE, maxBytes=1_000_000, backupCount=3, encoding="utf-8"
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(logging.Formatter(_FILE_FMT, datefmt="%Y-%m-%d %H:%M:%S"))
    logger.addHandler(file_handler)

    logger.propagate = False
    return logger