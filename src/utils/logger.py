"""
Centralized logging configuration for ENACT.

Single configured logger used across all modules. Outputs to both console
(human readable) and a rotating log file (structured for later parsing).

Important: all named loggers in ENACT share the SAME file handler instance.
Without this, every call to get_logger() would create its own FileHandler
with its own file descriptor pointing at enact.log, and rotation would fail
on Windows: rename() can't move a file that other processes hold open, and
15+ open handles from within ENACT itself effectively count as "other processes."
"""

import logging
import logging.handlers
from pathlib import Path

LOG_DIR = Path(__file__).resolve().parents[2] / "logs"
LOG_FILE = LOG_DIR / "enact.log"

_CONSOLE_FMT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
_FILE_FMT = "%(asctime)s | %(levelname)s | %(name)s | %(message)s"

# module-level singletons: built once on first use, reused everywhere.
# this is what fixes the "log rotation fails on Windows" bug: only one
# FileHandler exists, so only one open file descriptor exists, so rotation's
# rename step doesn't hit windows file-locking errors
_shared_file_handler: logging.Handler | None = None
_shared_console_handler: logging.Handler | None = None


# builds the shared console and file handlers exactly once
def _init_shared_handlers() -> tuple[logging.Handler, logging.Handler]:
    global _shared_file_handler, _shared_console_handler
    if _shared_file_handler is not None and _shared_console_handler is not None:
        return _shared_console_handler, _shared_file_handler

    LOG_DIR.mkdir(parents=True, exist_ok=True)

    console = logging.StreamHandler()
    console.setLevel(logging.INFO)
    console.setFormatter(logging.Formatter(_CONSOLE_FMT, datefmt="%H:%M:%S"))

    file_handler = logging.handlers.RotatingFileHandler(
        LOG_FILE, maxBytes=1_000_000, backupCount=3, encoding="utf-8"
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(logging.Formatter(_FILE_FMT, datefmt="%Y-%m-%d %H:%M:%S"))

    _shared_console_handler = console
    _shared_file_handler = file_handler
    return console, file_handler


def get_logger(name: str) -> logging.Logger:
    """Return a configured logger. Safe to call multiple times per module."""
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger  # already wired up for this name

    logger.setLevel(logging.DEBUG)

    # attach the shared handlers, not new ones. same handler instance is
    # added to every named logger, so there's only ever one file descriptor
    console, file_handler = _init_shared_handlers()
    logger.addHandler(console)
    logger.addHandler(file_handler)

    logger.propagate = False
    return logger