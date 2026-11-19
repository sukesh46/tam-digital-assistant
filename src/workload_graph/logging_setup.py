"""Plain structured logging setup with credential redaction.

Status: real
Purpose: one ``key=value`` log format for the package, with a filter that redacts anything
    that looks like an AWS access key id, secret key or session token.
Inputs: log level, output stream.
Outputs: configured ``workload_graph`` logger.
Planned dependencies: logging.
Notes: # RISK: redaction is a safety net only; code must still never log credentials or full
    API payloads in the first place.
"""

from __future__ import annotations

import logging
import re
import sys
from typing import TextIO

REDACTED = "[REDACTED]"
ROOT_LOGGER = "workload_graph"
_HANDLER_MARK = "_workload_graph_handler"

_ACCESS_KEY_RE = re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b")
_SECRET_FIELD_RE = re.compile(
    r"(?i)((?:aws_)?(?:secret_?access_?key|session_?token|secret|password)['\"]?\s*[:=]\s*['\"]?)"
    r"([^\s'\",}]+)"
)


def redact(text: str) -> str:
    """Replace credential-like substrings with ``[REDACTED]``."""
    text = _ACCESS_KEY_RE.sub(REDACTED, text)
    return _SECRET_FIELD_RE.sub(lambda m: m.group(1) + REDACTED, text)


class RedactingFilter(logging.Filter):
    """Rewrites each record's message with credentials redacted."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = redact(record.getMessage())
        record.args = None
        return True


class KeyValueFormatter(logging.Formatter):
    """``time=... level=... logger=... msg="..."``."""

    def format(self, record: logging.LogRecord) -> str:
        message = record.getMessage().replace('"', '\\"')
        time = self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z")
        return f'time={time} level={record.levelname} logger={record.name} msg="{message}"'


def configure_logging(level: int = logging.INFO, stream: TextIO | None = None) -> logging.Logger:
    """Install (or replace) the package handler and return the package logger."""
    logger = logging.getLogger(ROOT_LOGGER)
    for handler in list(logger.handlers):
        if getattr(handler, _HANDLER_MARK, False):
            logger.removeHandler(handler)
    handler = logging.StreamHandler(stream or sys.stderr)
    setattr(handler, _HANDLER_MARK, True)
    handler.addFilter(RedactingFilter())
    handler.setFormatter(KeyValueFormatter())
    logger.addHandler(handler)
    logger.setLevel(level)
    return logger


def get_logger(name: str) -> logging.Logger:
    """Return a logger in the package hierarchy."""
    return logging.getLogger(name)
