from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler

from app.config import Settings


def setup_logging(settings: Settings) -> None:
    settings.log_file.parent.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")

    file_handler = RotatingFileHandler(
        settings.log_file,
        maxBytes=2_000_000,
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)

    console = logging.StreamHandler()
    console.setFormatter(formatter)

    # Avoid duplicate handlers on reload.
    root.handlers.clear()
    root.addHandler(file_handler)
    root.addHandler(console)
    logging.getLogger("switcheroo").info("Logging to %s", settings.log_file)
