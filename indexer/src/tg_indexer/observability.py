"""Private, bounded logs and terminal progress without Telegram content."""
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import sys
import time

LOGGER = logging.getLogger("tg_indexer")


def configure_logging(log_file: Path | None = None) -> None:
    LOGGER.setLevel(logging.INFO)
    LOGGER.propagate = False
    for handler in LOGGER.handlers[:]:
        LOGGER.removeHandler(handler)
        handler.close()
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s", "%H:%M:%S")
    console = logging.StreamHandler(sys.stderr)
    console.setFormatter(formatter)
    LOGGER.addHandler(console)
    if log_file is not None:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(log_file, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        os.close(fd)
        log_file.chmod(0o600)
        file_handler = PrivateRotatingFileHandler(log_file, maxBytes=5 * 1024 * 1024, backupCount=2)
        file_handler.setFormatter(formatter)
        LOGGER.addHandler(file_handler)
    # Third-party logs may contain request details; expose only our bounded metadata.
    logging.getLogger("telethon").setLevel(logging.CRITICAL)


class PrivateRotatingFileHandler(RotatingFileHandler):
    def _open(self):
        fd = os.open(self.baseFilename, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        return os.fdopen(fd, self.mode, encoding="utf-8")


class Progress:
    def __init__(self, label: str, total: int):
        self.label = label
        self.total = total
        self.current = 0
        self.details = "ожидание"
        self.last = float("-inf")

    def update(self, current: int | None = None, details: str | None = None, *, force=False):
        if current is not None:
            self.current = current
        if details is not None:
            self.details = details
        now = time.monotonic()
        if not force and now - self.last < 2:
            return
        self.last = now
        ratio = min(1, self.current / self.total) if self.total else 1
        filled = int(ratio * 20)
        bar = "#" * filled + "-" * (20 - filled)
        LOGGER.info("%s [%s] %3d%% %d/%d | %s", self.label, bar, int(ratio * 100), self.current, self.total, self.details)

    def finish(self, details: str):
        # History may end before the configured upper bound; do not invent messages.
        self.update(details="завершено; " + details, force=True)
