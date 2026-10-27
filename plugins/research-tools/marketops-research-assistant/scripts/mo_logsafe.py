"""Keep provider credentials out of logs.

Import this before anything that can log a request. Two layers:

1. ``httpx`` / ``httpcore`` are put at WARNING, so their per-request INFO line (which carries
   the full URL, query string included) is never emitted.
2. A logging filter rewrites secrets out of record messages *before* a formatter wraps them,
   so a wrapped line cannot reassemble the value either.

This is the local fix for issue #86; it is deliberately a separate module so the upstream
change can be the same three lines in the server entrypoint.
"""
from __future__ import annotations

import logging
import re

# query-string credentials, header credentials, and bare token assignments
_RULES = (
    (re.compile(r"(api[_-]?key\s*=\s*)[^&\s\"']+", re.I), r"\1***"),
    (re.compile(r"((?:access[_-]?|refresh[_-]?|auth[_-]?)?token\s*=\s*)[^&\s\"']+", re.I), r"\1***"),
    (re.compile(r"(secret\s*=\s*)[^&\s\"']+", re.I), r"\1***"),
    (re.compile(r"(password\s*=\s*)[^&\s\"']+", re.I), r"\1***"),
    (re.compile(r"(Bearer\s+)[A-Za-z0-9._~+/=-]{8,}", re.I), r"\1***"),
    (re.compile(r"(X-API-KEY\s*[:=]\s*)[^\s\"']+", re.I), r"\1***"),
)


class ScrubFilter(logging.Filter):
    """Rewrite secrets in the record itself, so no handler or formatter can see them."""

    def filter(self, record: logging.LogRecord) -> bool:  # noqa: A003 - logging API name
        try:
            message = record.getMessage()
        except Exception:  # noqa: BLE001 - a broken record must not crash logging
            return True
        for pattern, replacement in _RULES:
            message = pattern.sub(replacement, message)
        record.msg = message
        record.args = ()
        return True


_FILTER = ScrubFilter()


def install() -> None:
    for name in ("httpx", "httpcore", "urllib3", "requests", "aiohttp"):
        logger = logging.getLogger(name)
        logger.setLevel(logging.WARNING)  # the per-request INFO line is the leak
        if _FILTER not in logger.filters:
            logger.addFilter(_FILTER)
    root = logging.getLogger()
    if _FILTER not in root.filters:
        root.addFilter(_FILTER)
    for handler in root.handlers:
        if _FILTER not in handler.filters:
            handler.addFilter(_FILTER)


install()
