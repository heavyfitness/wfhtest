"""Console + rotating-file logging for the pipeline."""
from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

_FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"

# Libraries that are far too chatty at INFO/DEBUG for a cron log.
_NOISY_LOGGERS = ("httpx", "httpcore", "urllib3", "googleapiclient", "anthropic")


def setup_logging(verbose: bool = False, log_file: str | Path = "logs/pipeline.log") -> None:
    """Log INFO (DEBUG with ``verbose``) to the console and DEBUG to a rotating file."""
    log_path = Path(log_file)
    log_path.parent.mkdir(parents=True, exist_ok=True)

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    root.handlers.clear()

    console = logging.StreamHandler()
    console.setLevel(logging.DEBUG if verbose else logging.INFO)
    console.setFormatter(logging.Formatter(_FORMAT))
    root.addHandler(console)

    file_handler = RotatingFileHandler(
        log_path, maxBytes=1_000_000, backupCount=5, encoding="utf-8"
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(logging.Formatter(_FORMAT))
    root.addHandler(file_handler)

    for name in _NOISY_LOGGERS:
        logging.getLogger(name).setLevel(logging.INFO if verbose else logging.WARNING)
