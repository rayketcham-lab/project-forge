"""Structured logging configuration using structlog."""

import logging
import sys
from typing import IO

import structlog


def configure_logging(stream: IO | None = None, level: int = logging.INFO) -> None:
    """Configure structlog with JSON rendering for production use."""
    output = stream or sys.stderr

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.stdlib.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(file=output),
        cache_logger_on_first_use=True,
    )

    # Also configure stdlib logging to route through structlog
    logging.basicConfig(format="%(message)s", stream=output, level=level, force=True)
