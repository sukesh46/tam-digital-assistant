"""Session provider for assuming read-only roles via STS.

Status: partial
Purpose: hand out short-lived credentials for approved accounts only.
Inputs: ``Workspace`` (approved accounts, role name, external id), an STS client, a purpose
    string, an injected clock.
Outputs: ``SessionProvider`` protocol, ``StsSessionProvider``, ``Credentials``.
Planned dependencies: boto3.
Notes:
    - An account outside the approved list raises ``OutOfScopeAccountError`` before any STS call.
    - Credentials never appear in ``repr``/``str`` or logs.
    - Live wiring (a real STS client and boto3 sessions) is a SKELETON; tests use Stubber.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from workload_graph.ids import validate_account_id
from workload_graph.logging_setup import get_logger
from workload_graph.models import Workspace

MAX_DURATION_SECONDS = 3600
_SESSION_NAME_BAD = re.compile(r"[^\w+=,.@-]+")
_log = get_logger(__name__)


class OutOfScopeAccountError(PermissionError):
    """The account is not in the workspace's approved account list."""


@dataclass(frozen=True)
class Credentials:
    """Temporary credentials. ``repr`` and ``str`` never show the secret parts."""

    access_key_id: str = field(repr=False)
    secret_access_key: str = field(repr=False)
    session_token: str = field(repr=False)
    expiration: datetime

    def __repr__(self) -> str:
        return f"Credentials(access_key_id='***', expiration={self.expiration.isoformat()})"

    __str__ = __repr__


class SessionProvider(Protocol):
    """Provides credentials for approved accounts."""

    def credentials_for(self, account_id: str) -> Credentials: ...


def _utc_now() -> datetime:
    return datetime.now(UTC)


class StsSessionProvider:
    """Assumes ``arn:aws:iam::{account}:role/{role_name}`` through STS, with caching."""

    def __init__(
        self,
        workspace: Workspace,
        sts_client: Any,  # Any: a boto3 STS client or a test double
        purpose: str,
        clock: Callable[[], datetime] = _utc_now,
        duration_seconds: int = MAX_DURATION_SECONDS,
        refresh_margin: timedelta = timedelta(minutes=5),
    ) -> None:
        if not 900 <= duration_seconds <= MAX_DURATION_SECONDS:
            raise ValueError(f"duration must be between 900 and {MAX_DURATION_SECONDS} seconds")
        self._workspace = workspace
        self._sts = sts_client
        self._clock = clock
        self._duration = duration_seconds
        self._margin = refresh_margin
        cleaned = _SESSION_NAME_BAD.sub("-", f"workload-graph-{purpose}").strip("-")
        self.session_name = cleaned[:64]
        self._cache: dict[str, Credentials] = {}

    def __repr__(self) -> str:
        return (f"StsSessionProvider(workspace={self._workspace.workspace_id!r}, "
                f"session_name={self.session_name!r}, cached_accounts={sorted(self._cache)!r})")

    __str__ = __repr__

    def role_arn(self, account_id: str) -> str:
        """ARN of the read-only role in ``account_id``."""
        validate_account_id(account_id)
        return f"arn:aws:iam::{account_id}:role/{self._workspace.role_name}"

    def credentials_for(self, account_id: str) -> Credentials:
        """Return cached or fresh credentials for an approved account."""
        validate_account_id(account_id)
        if not self._workspace.is_approved(account_id):
            raise OutOfScopeAccountError(f"account {account_id} is not in the approved list")
        cached = self._cache.get(account_id)
        if cached is not None and self._clock() < cached.expiration - self._margin:
            return cached
        params: dict[str, Any] = {
            "RoleArn": self.role_arn(account_id),
            "RoleSessionName": self.session_name,
            "DurationSeconds": self._duration,
        }
        if self._workspace.external_id:
            params["ExternalId"] = self._workspace.external_id
        response = self._sts.assume_role(**params)
        raw = response["Credentials"]
        expiration = raw["Expiration"]
        if isinstance(expiration, str):
            expiration = datetime.fromisoformat(expiration)
        if expiration.tzinfo is None:
            expiration = expiration.replace(tzinfo=UTC)
        creds = Credentials(
            access_key_id=raw["AccessKeyId"],
            secret_access_key=raw["SecretAccessKey"],
            session_token=raw["SessionToken"],
            expiration=expiration.astimezone(UTC),
        )
        self._cache[account_id] = creds
        _log.info("assumed read-only role account=%s session=%s expires=%s",
                  account_id, self.session_name, creds.expiration.isoformat())
        return creds

    def boto3_session(self, account_id: str) -> Any:  # pragma: no cover
        """Return a boto3 Session for ``account_id``. Any: boto3.Session.

        SKELETON: live wiring requires approved roles; no test calls this.
        """
        import boto3

        creds = self.credentials_for(account_id)
        return boto3.Session(
            aws_access_key_id=creds.access_key_id,
            aws_secret_access_key=creds.secret_access_key,
            aws_session_token=creds.session_token,
        )
