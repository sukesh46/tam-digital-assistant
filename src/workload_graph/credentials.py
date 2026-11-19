"""Credential modes for collection: ambient (local test) and assumed role (deployed).

Status: partial
Purpose: give the collectors a client factory for the accounts in scope, through one of two modes.
Inputs: account ids, region, and for the deployed mode an STS client and an external id.
Outputs: ``ClientFactory``, ``AmbientCredentials``, ``AssumedRoleCredentials``, ``TAM_ROLE_NAME``.
Planned dependencies: boto3, auth.session.
Notes:
    - Ambient mode serves exactly one account. STS must report that account, or nothing is read.
      It is for the test pad only.
    - Assumed-role mode calls ``sts:AssumeRole`` on ``TamReadOnly`` in each approved member account
      with an ExternalId. The deployed service runs under an ECS task role, so no IAM user exists.
    - Both modes hand back the same ``ClientFactory`` shape, so collectors do not know which
      mode is active.
    - Credentials are never logged or stored in the project.
"""

from __future__ import annotations

import re
import threading
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import boto3
from botocore.config import Config

from workload_graph.auth.session import StsSessionProvider
from workload_graph.ids import validate_account_id
from workload_graph.models import Workspace

TAM_ROLE_NAME = "TamReadOnly"
_REGION = re.compile(r"^[a-z]{2}-[a-z]+-\d$")

# Every client is bounded: a host that does not answer must fail in seconds, not minutes.
CLIENT_CONFIG = Config(connect_timeout=5, read_timeout=30,
                       retries={"max_attempts": 1, "mode": "standard"})

# (service, account_id, region) -> boto3-like client. Any: service client types differ.
ClientFactory = Callable[[str, str, str], Any]


def _utc_now() -> datetime:
    return datetime.now(UTC)


def validate_region(region: str) -> str:
    """Return ``region`` if it looks like ``ap-south-1``, otherwise raise ``ValueError``."""
    if _REGION.fullmatch(region) is None:
        raise ValueError("region must look like ap-south-1")
    return region


def ambient_client(service: str, _account_id: str, region: str) -> Any:
    """Client built from the ambient credential. Any: service client types differ by name."""
    factory: Any = boto3.client
    return factory(service, region_name=region, config=CLIENT_CONFIG)


def _sts_for(region: str) -> Any:
    factory: Any = boto3.client
    return factory("sts", region_name=region, config=CLIENT_CONFIG)


class AmbientCredentials:
    """Local test mode. One account, the ambient credential, verified with STS first."""

    def __init__(self, account_ids: tuple[str, ...], region: str,
                 sts_factory: Callable[[str], Any] = _sts_for) -> None:
        if len(account_ids) != 1:
            raise ValueError("ambient credentials serve one account")
        self._account_id = validate_account_id(account_ids[0])
        self._region = validate_region(region)
        self._sts_factory = sts_factory

    def verify(self) -> None:
        """Refuse unless STS says the ambient credential belongs to the account in scope."""
        caller = self._sts_factory(self._region).get_caller_identity()["Account"]
        if caller != self._account_id:
            raise ValueError("the credentials in this environment are for a different account")

    def client_factory(self) -> ClientFactory:
        return ambient_client


class AssumedRoleCredentials:
    """Deployed mode. STS AssumeRole into ``TamReadOnly`` in each approved member account."""

    def __init__(self, account_ids: tuple[str, ...], regions: tuple[str, ...], sts_client: Any,
                 external_id: str, purpose: str,
                 clock: Callable[[], datetime] = _utc_now) -> None:
        if not external_id:
            raise ValueError("an external id is required for the assumed-role mode")
        for region in regions:
            validate_region(region)
        workspace = Workspace(
            workspace_id=purpose, customer="unnamed-customer",
            approved_account_ids=list(account_ids), regions=list(regions),
            role_name=TAM_ROLE_NAME, external_id=external_id,
        )
        self._provider = StsSessionProvider(workspace, sts_client, purpose, clock=clock)
        self._lock = threading.Lock()

    def client_factory(self) -> ClientFactory:
        def factory(service: str, account_id: str, region: str) -> Any:
            # The provider caches per account and is not thread-safe; worker threads share it.
            with self._lock:
                creds = self._provider.credentials_for(account_id)
            # A session per client: the default boto3 session is not safe to share across threads.
            session: Any = boto3.Session(
                aws_access_key_id=creds.access_key_id,
                aws_secret_access_key=creds.secret_access_key,
                aws_session_token=creds.session_token,
            )
            return session.client(service, region_name=region, config=CLIENT_CONFIG)
        return factory
