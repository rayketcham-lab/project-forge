"""Structured logging configuration using structlog."""

import logging
import re
import sys
from collections.abc import Iterable
from typing import IO, Any

import structlog

_MASK = "[REDACTED]"
# Values shorter than this are not rewritten inside rendered tracebacks: a
# short substring can legitimately appear in unrelated log text, and scrubbing
# it would mangle legitimate content (see TestOverRedaction).
_MIN_SUBSTRING_LEN = 8

# Field names treated as secrets — their bound values are masked in output.
# Deliberately matches `_api_key`, `api_token`, `client_secret`, `password`,
# etc. wherever they appear in a key name, so BYO-LLM provider creds never leak.
_SECRET_KEY_RE = re.compile(
    r"(?:^|_)(?:api[_-]?key|secret|password|passwd|token|auth|credential)(?:$|_)",
    re.IGNORECASE,
)


def _is_secret_key(key: str) -> bool:
    """Whether a log field key names a secret (api_key, token, secret, ...)."""
    return bool(_SECRET_KEY_RE.search(key))


class RedactionProcessor:
    """Mask secret field values and scrub them from rendered event/traceback text.

    Runs after ``format_exc_info`` so both structured secret fields and secrets
    that appear inside a rendered exception string are covered.
    """

    def __call__(self, logger: Any, method_name: str, event_dict: dict[str, Any]) -> dict[str, Any]:
        secret_values: list[str] = []
        for key, value in list(event_dict.items()):
            if _is_secret_key(key):
                if isinstance(value, str):
                    secret_values.append(value)
                event_dict[key] = _MASK
        if secret_values:
            self._scrub_strings(event_dict, secret_values)
        return event_dict

    @staticmethod
    def _scrub_strings(event_dict: dict[str, Any], secret_values: Iterable[str]) -> None:
        candidates = [v for v in secret_values if len(v) >= _MIN_SUBSTRING_LEN]
        if not candidates:
            return
        for key in ("event", "exception"):
            text = event_dict.get(key)
            if isinstance(text, str):
                for value in candidates:
                    text = text.replace(value, _MASK)
                event_dict[key] = text


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
            RedactionProcessor(),
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(file=output),
        cache_logger_on_first_use=True,
    )

    # Also configure stdlib logging to route through structlog
    logging.basicConfig(format="%(message)s", stream=output, level=level, force=True)
