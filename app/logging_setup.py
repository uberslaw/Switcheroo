from __future__ import annotations

import json
import logging
from logging.handlers import RotatingFileHandler

from app.config import Settings
from app.diagnostics import desired_log_level, sync_log_level
from app.filesec import restrict_private_file


class JsonLogFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "name": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def setup_logging(settings: Settings) -> None:
    settings.log_file.parent.mkdir(parents=True, exist_ok=True)
    level = desired_log_level()
    root = logging.getLogger()
    root.setLevel(level)
    text_formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
    file_formatter: logging.Formatter = JsonLogFormatter() if settings.log_json else text_formatter

    file_handler = RotatingFileHandler(
        settings.log_file,
        maxBytes=2_000_000,
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setLevel(level)
    file_handler.setFormatter(file_formatter)

    console = logging.StreamHandler()
    console.setLevel(level)
    console.setFormatter(text_formatter)

    # Avoid duplicate handlers on reload.
    root.handlers.clear()
    root.addHandler(file_handler)
    root.addHandler(console)
    sync_log_level()
    logging.getLogger("switcheroo").info("Logging to %s", settings.log_file)
    restrict_private_file(settings.log_file)
