"""Step 6: STS session provider tests (Stubber for STS, injected clock)."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

import boto3
import pytest
from botocore.stub import Stubber
from wg_test_helpers import ACCT_A, ACCT_B, ACCT_C, FROZEN_NOW, REGION

from workload_graph.auth.session import (
    Credentials,
    OutOfScopeAccountError,
    StsSessionProvider,
)
from workload_graph.models import Workspace

FAKE_KEY = "ASIAFAKEFAKEFAKE0001"
FAKE_SECRET = "fake/secret/value/for/tests/only/0001"
FAKE_TOKEN = "fake-session-token-for-tests-only-0001"
WORKSPACE = Workspace(workspace_id="ws-1", customer="example-customer",
                      approved_account_ids=[ACCT_A, ACCT_B], role_name="ReadOnlyAudit",
                      external_id="example-external-id")


class Clock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


def _sts() -> tuple[Any, Stubber]:
    client = boto3.client("sts", region_name=REGION, aws_access_key_id="testing-not-a-key",
                          aws_secret_access_key="testing-not-a-secret")
    stubber = Stubber(client)
    stubber.activate()
    return client, stubber


def _response(expiration: datetime, n: int = 1) -> dict[str, Any]:
    return {"Credentials": {
        "AccessKeyId": FAKE_KEY[:-1] + str(n), "SecretAccessKey": FAKE_SECRET[:-1] + str(n),
        "SessionToken": FAKE_TOKEN[:-1] + str(n), "Expiration": expiration}}


def _expected(account: str = ACCT_A) -> dict[str, Any]:
    return {
        "RoleArn": f"arn:aws:iam::{account}:role/ReadOnlyAudit",
        "RoleSessionName": "workload-graph-inventory",
        "DurationSeconds": 3600,
        "ExternalId": "example-external-id",
    }


def _provider(client: Any, clock: Clock | None = None, **kw: Any) -> StsSessionProvider:
    return StsSessionProvider(WORKSPACE, client, purpose="inventory",
                              clock=clock or Clock(FROZEN_NOW), **kw)


def test_role_arn_is_built_from_account_and_role_name() -> None:
    client, _ = _sts()
    assert _provider(client).role_arn(ACCT_B) == "arn:aws:iam::222222222222:role/ReadOnlyAudit"


def test_external_id_and_purpose_session_name_are_passed() -> None:
    client, stubber = _sts()
    stubber.add_response("assume_role", _response(FROZEN_NOW + timedelta(hours=1)), _expected())
    creds = _provider(client).credentials_for(ACCT_A)
    stubber.assert_no_pending_responses()
    assert creds.access_key_id.startswith("ASIAFAKE")


def test_session_name_is_sanitised_and_bounded() -> None:
    client, _ = _sts()
    provider = StsSessionProvider(WORKSPACE, client, purpose="weekly scan/" + "x" * 80,
                                  clock=Clock(FROZEN_NOW))
    name = provider.session_name
    assert name.startswith("workload-graph-weekly-scan-")
    assert len(name) <= 64
    assert " " not in name and "/" not in name


def test_duration_above_one_hour_is_rejected() -> None:
    client, _ = _sts()
    with pytest.raises(ValueError):
        _provider(client, duration_seconds=7200)


def test_credentials_are_cached_until_shortly_before_expiry_and_then_refreshed() -> None:
    client, stubber = _sts()
    clock = Clock(FROZEN_NOW)
    stubber.add_response("assume_role", _response(FROZEN_NOW + timedelta(hours=1), 1),
                         _expected())
    stubber.add_response("assume_role", _response(FROZEN_NOW + timedelta(hours=2), 2),
                         _expected())
    provider = _provider(client, clock)
    first = provider.credentials_for(ACCT_A)
    clock.now = FROZEN_NOW + timedelta(minutes=50)
    assert provider.credentials_for(ACCT_A) is first
    clock.now = FROZEN_NOW + timedelta(minutes=56)
    second = provider.credentials_for(ACCT_A)
    assert second is not first
    assert second.access_key_id.endswith("2")
    stubber.assert_no_pending_responses()


def test_out_of_scope_account_raises_before_any_sts_call() -> None:
    calls: list[str] = []

    class RecordingSts:
        def assume_role(self, **_kwargs: Any) -> Any:
            calls.append("AssumeRole")
            raise AssertionError("must not be called")

    provider = StsSessionProvider(WORKSPACE, RecordingSts(), purpose="inventory",
                                  clock=Clock(FROZEN_NOW))
    with pytest.raises(OutOfScopeAccountError):
        provider.credentials_for(ACCT_C)
    assert calls == []


def test_invalid_account_id_is_rejected() -> None:
    client, _ = _sts()
    with pytest.raises(ValueError):
        _provider(client).credentials_for("123")


def test_repr_and_str_never_show_credentials() -> None:
    client, stubber = _sts()
    stubber.add_response("assume_role", _response(FROZEN_NOW + timedelta(hours=1)), _expected())
    provider = _provider(client)
    creds = provider.credentials_for(ACCT_A)
    for text in (repr(creds), str(creds), repr(provider), str(provider)):
        assert FAKE_SECRET[:-1] not in text
        assert FAKE_TOKEN[:-1] not in text
        assert FAKE_KEY[:-1] not in text


def test_credentials_are_never_written_to_logs(caplog: pytest.LogCaptureFixture) -> None:
    client, stubber = _sts()
    stubber.add_response("assume_role", _response(FROZEN_NOW + timedelta(hours=1)), _expected())
    with caplog.at_level(logging.DEBUG):
        _provider(client).credentials_for(ACCT_A)
    assert caplog.records, "provider should log the assume-role event"
    for record in caplog.records:
        line = record.getMessage() + repr(record.__dict__)
        assert FAKE_SECRET[:-1] not in line
        assert FAKE_TOKEN[:-1] not in line
        assert FAKE_KEY[:-1] not in line


def test_credentials_dataclass_is_immutable() -> None:
    creds = Credentials("a" * 16, "b", "c", FROZEN_NOW)
    with pytest.raises(AttributeError):
        creds.secret_access_key = "x"  # type: ignore[misc]
