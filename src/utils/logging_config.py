"""Logging ra console + file logs/app.log."""
from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path


def setup_logging(log_path: str = "logs/app.log",
                  level: str = "INFO") -> logging.Logger:
    Path(log_path).parent.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s | %(levelname)-7s | %(name)s | %(message)s")
    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    console = logging.StreamHandler()
    console.setFormatter(fmt)
    root.addHandler(console)
    fileh = RotatingFileHandler(log_path, maxBytes=2_000_000,
                                backupCount=3, encoding="utf-8")
    fileh.setFormatter(fmt)
    root.addHandler(fileh)
    return logging.getLogger("app")
