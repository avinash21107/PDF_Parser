
from __future__ import annotations
import logging
from pathlib import Path

class LoggerConfig:
    """Encapsulates configuration for project loggers (OOP + SRP)."""

    def __init__(
        self,
        log_dir: str = "logs",
        log_file: str = "PDF_Parser.log",
        console_level: int = logging.INFO,
        file_level: int = logging.DEBUG,
        fmt: str = "%(asctime)s [%(levelname)s] %(name)s - %(message)s",
        datefmt: str = "%Y-%m-%d %H:%M:%S",
    ) -> None:
        self.log_dir = Path(log_dir)
        self.log_file = self.log_dir / log_file
        self.console_level = console_level
        self.file_level = file_level
        self.fmt = fmt
        self.datefmt = datefmt

    def ensure_log_dir(self) -> None:
        """Ensures the log directory exists (Single Responsibility)."""
        self.log_dir.mkdir(parents=True, exist_ok=True)

    def get_formatter(self) -> logging.Formatter:
        """Returns a reusable logging formatter."""
        return logging.Formatter(self.fmt, datefmt=self.datefmt)


def get_logger(name: str, log_dir: str = "logs") -> logging.Logger:
    """
    Factory function to create or retrieve a configured logger.

    Args:
        name: Name of the logger (usually __name__ of the calling module).
        log_dir: Directory where log files should be stored.

    Returns:
        logging.Logger: Configured logger instance.
    """
    config = LoggerConfig(log_dir=log_dir)
    config.ensure_log_dir()

    logger = logging.getLogger(name)
    logger.setLevel(logging.DEBUG)

    if not logger.handlers:
        formatter = config.get_formatter()

        file_handler = logging.FileHandler(config.log_file, encoding="utf-8")
        file_handler.setLevel(config.file_level)
        file_handler.setFormatter(formatter)

        console_handler = logging.StreamHandler()
        console_handler.setLevel(config.console_level)
        console_handler.setFormatter(formatter)

        logger.addHandler(file_handler)
        logger.addHandler(console_handler)

    return logger
