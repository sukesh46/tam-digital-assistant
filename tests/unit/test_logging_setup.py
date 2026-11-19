"""Logging must never emit credentials, even if code tries to log them."""

from __future__ import annotations

import io
import logging

from workload_graph.logging_setup import REDACTED, configure_logging, get_logger

FAKE_KEY = "AKIAFAKEFAKEFAKE0002"
FAKE_SECRET = "fake/secret/value/for/tests/only/0002"
FAKE_TOKEN = "fake-session-token-for-tests-only-0002"


def test_sample_run_with_fake_credentials_logs_no_credential() -> None:
    stream = io.StringIO()
    configure_logging(level=logging.DEBUG, stream=stream)
    log = get_logger("workload_graph.sample")
    log.info("starting run account=%s", "111111111111")
    log.info("oops key=%s", FAKE_KEY)
    log.info("SecretAccessKey=%s SessionToken=%s", FAKE_SECRET, FAKE_TOKEN)
    log.info("dict %s", {"aws_secret_access_key": FAKE_SECRET, "aws_session_token": FAKE_TOKEN})
    output = stream.getvalue()
    assert "account=111111111111" in output
    assert FAKE_KEY not in output
    assert FAKE_SECRET not in output
    assert FAKE_TOKEN not in output
    assert REDACTED in output


def test_log_lines_are_plain_key_value() -> None:
    stream = io.StringIO()
    configure_logging(level=logging.INFO, stream=stream)
    get_logger("workload_graph.sample").info("hello")
    line = stream.getvalue().strip()
    assert "level=INFO" in line
    assert "logger=workload_graph.sample" in line
    assert 'msg="hello"' in line


def test_configure_logging_is_idempotent() -> None:
    stream = io.StringIO()
    configure_logging(stream=stream)
    configure_logging(stream=stream)
    get_logger("workload_graph.x").warning("once")
    assert stream.getvalue().count("once") == 1
