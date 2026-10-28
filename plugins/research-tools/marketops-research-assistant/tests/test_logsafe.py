"""Offline regression for issue #86: a provider key must never reach a log line.

Run with the isolated venv:
    python -m unittest discover -s tests -p 'test_logsafe.py' -v
"""
from __future__ import annotations

import io
import logging
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import mo_logsafe  # noqa: E402,F401 - installs the filter

SENTINEL = "SENTINEL_SECRET_abcdef123456"


class WrapFormatter(logging.Formatter):
    """Mimic a narrow terminal that wraps a long URL across lines."""

    def format(self, record: logging.LogRecord) -> str:
        text = super().format(record)
        return "\n".join(text[i:i + 31] for i in range(0, len(text), 31))


def _capture(logger_name: str, level: int, message: str, set_level: int | None = None) -> str:
    buffer = io.StringIO()
    handler = logging.StreamHandler(buffer)
    handler.setFormatter(WrapFormatter("%(message)s"))
    logger = logging.getLogger(logger_name)
    old_level, old_propagate = logger.level, logger.propagate
    logger.addHandler(handler)
    if set_level is not None:
        logger.setLevel(set_level)
    logger.propagate = False
    try:
        logger.log(level, message)
    finally:
        logger.removeHandler(handler)
        logger.setLevel(old_level)
        logger.propagate = old_propagate
    return buffer.getvalue()


def _squashed(text: str) -> str:
    return "".join(text.split())


class LogSafeTest(unittest.TestCase):
    def test_httpx_info_line_is_not_emitted_by_default(self):
        self.assertEqual(logging.getLogger("httpx").level, logging.WARNING)
        out = _capture("httpx", logging.INFO,
                       "HTTP Request: GET https://serpapi.com/account.json?api_key=" + SENTINEL)
        self.assertEqual(out, "", "httpx INFO must be suppressed at WARNING")

    def test_wrapped_url_cannot_reassemble_the_key(self):
        # force the logger to INFO: the filter, not the level, must do the work
        out = _capture("httpx", logging.INFO,
                       "HTTP Request: GET https://serpapi.com/account.json?api_key=" + SENTINEL
                       + '&engine=google "HTTP/1.1 200 OK"', set_level=logging.INFO)
        self.assertNotIn(SENTINEL, out)
        self.assertNotIn(SENTINEL, _squashed(out), "a wrapped line must not reassemble the key")
        self.assertIn("api_key=***", _squashed(out))

    def test_token_and_bearer_are_masked(self):
        out = _capture("httpcore", logging.INFO,
                       "GET https://x.example/dl?token=" + SENTINEL
                       + " Authorization: Bearer " + SENTINEL, set_level=logging.INFO)
        self.assertNotIn(SENTINEL, _squashed(out))
        self.assertIn("token=***", _squashed(out))
        self.assertIn("Bearer***", _squashed(out))

    def test_ordinary_log_line_is_untouched(self):
        out = _capture("marketops.test", logging.INFO, "read 24 pages, 0 failed", set_level=logging.INFO)
        self.assertIn("read 24 pages, 0 failed", out)


if __name__ == "__main__":
    unittest.main(verbosity=2)
