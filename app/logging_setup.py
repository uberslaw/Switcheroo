from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler

from app.config import Settings
from app.diagnostics import desired_log_level, sync_log_level
from app.filesec import restrict_private_file


def setup_logging(settings: Settings) -> None:
    settings.log_file.parent.mkdir(parents=True, exist_ok=True)
    level = desired_log_level()
    root = logging.getLogger()
    root.setLevel(level)
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")

    file_handler = RotatingFileHandler(
        settings.log_file,
        maxBytes=2_000_000,
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setLevel(level)
    file_handler.setFormatter(formatter)

    console = logging.StreamHandler()
    console.setLevel(level)
    console.setFormatter(formatter)

    # Avoid duplicate handlers on reload.
    root.handlers.clear()
    root.addHandler(file_handler)
    root.addHandler(console)
    sync_log_level()
    logging.getLogger("switcheroo").info("Logging to %s", settings.log_file)
    restrict_private_file(settings.log_file)
