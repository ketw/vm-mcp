"""logs.py — one shared logger. Call setup_logging() once from app.main()."""

from __future__ import annotations

import logging
import sys

from .config import LOG_FILE

log = logging.getLogger("vm-mcp")


def setup_logging() -> None:
    if log.handlers:
        return
    fmt = logging.Formatter("%(asctime)s [vm-mcp] %(levelname)s %(message)s")
    for handler in (logging.FileHandler(LOG_FILE, encoding="utf-8"), logging.StreamHandler(sys.stdout)):
        handler.setFormatter(fmt)
        log.addHandler(handler)
    log.setLevel(logging.INFO)
