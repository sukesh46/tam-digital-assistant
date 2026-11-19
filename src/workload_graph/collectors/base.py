"""Collector protocol plus retry, budget and allowlist helpers.

Status: real
Purpose: shared I/O plumbing so every collector is read-only, bounded and never raises on
    denial or throttling.
Inputs: a client factory ``(service, account_id, region) -> boto3 client``, a scope, an
    injected ``sleep`` function.
Outputs: ``RawSnapshot`` via ``BaseCollector.collect``.
Planned dependencies: botocore.
Notes:
    - Every call goes through ``CallRecorder.call``, which refuses operations outside the
      collector's allowlist and anything that looks mutating.
    - Each actual API call (including retries) counts against ``scope.max_calls``.
    - Error notes carry only the operation name and error code, never messages or payloads.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, ClassVar, Protocol

from botocore.exceptions import BotoCoreError, ClientError

from workload_graph.ids import validate_account_id
from workload_graph.models import RawSnapshot, SnapshotStatus, SourceName
from workload_graph.ruleset import Ruleset

ACCESS_DENIED_CODES: frozenset[str] = frozenset({
    "AccessDenied", "AccessDeniedException", "UnauthorizedOperation",
    "UnauthorizedException", "AuthorizationError",
})
# ASSUMPTION: these are the throttling error codes returned by the services we call.
THROTTLING_CODES: frozenset[str] = frozenset({
    "Throttling", "ThrottlingException", "RequestLimitExceeded", "TooManyRequestsException",
    "RequestThrottled", "ThrottledException",
})
# RISK: a collector must never call anything that changes customer resources.
FORBIDDEN_PREFIXES: tuple[str, ...] = (
    "Create", "Delete", "Put", "Update", "Modify", "Run", "Start", "Stop", "Terminate",
    "Attach", "Detach", "Associate", "Disassociate", "Tag", "Untag",
)

ClientFactory = Callable[[str, str, str], Any]
"""``(service, account_id, region) -> client``. Any: boto3 client types differ per service."""

SleepFn = Callable[[float], None]


class ForbiddenOperationError(RuntimeError):
    """Raised when code tries to call an operation outside the read-only allowlist."""


class BudgetExceededError(Exception):
    """The per-collection call budget is used up."""


class _OperationError(Exception):
    def __init__(self, operation: str, code: str) -> None:
        super().__init__(f"{operation}: {code}")
        self.operation = operation
        self.code = code


class OperationDeniedError(_OperationError):
    """Access was denied for an operation."""


class ThrottledOutError(_OperationError):
    """An operation stayed throttled after all retries."""


class OperationFailedError(_OperationError):
    """An operation failed for another reason."""


@dataclass(frozen=True)
class CollectionScope:
    """What a collection run may do: time, budget, seeds and CloudTrail settings."""

    now: datetime
    max_calls: int = 200
    seed_resource_ids: tuple[str, ...] = ()
    lookback_days: int = 90
    window_minutes: int = 30
    creation_event_names: tuple[str, ...] = ()
    # SKELETON: provisional retry settings
    max_attempts: int = 5
    base_delay_seconds: float = 0.5

    @classmethod
    def from_ruleset(cls, ruleset: Ruleset, now: datetime,
                     seeds: Sequence[str] = ()) -> CollectionScope:
        """Build a scope from ruleset settings."""
        return cls(
            now=now,
            max_calls=ruleset.cloudtrail_max_calls_per_account,
            seed_resource_ids=tuple(sorted(set(seeds))),
            lookback_days=ruleset.cloudtrail_lookback_days,
            window_minutes=ruleset.cloudtrail_window_minutes,
            creation_event_names=tuple(ruleset.creation_event_names),
        )


class Collector(Protocol):
    """A read-only collector for one source."""

    source: ClassVar[SourceName]
    allowed_operations: ClassVar[frozenset[str]]

    def collect(self, client_factory: ClientFactory, account_id: str, region: str,
                scope: CollectionScope) -> RawSnapshot: ...


def _method_name(client: Any, operation: str) -> str:
    """Map a PascalCase operation to the client's snake_case method. Any: boto3 client."""
    mapping: dict[str, str] = client.meta.method_to_api_mapping
    for method, op in mapping.items():
        if op == operation:
            return method
    raise ForbiddenOperationError(f"operation {operation} is not offered by this client")


class CallRecorder:
    """Performs calls with allowlist, retry and budget; records pages, notes and outcomes."""

    def __init__(self, scope: CollectionScope, sleep: SleepFn,
                 allowed: frozenset[str]) -> None:
        self.scope = scope
        self._sleep = sleep
        self._allowed = allowed
        self.calls = 0
        self.operations_called: list[str] = []
        # Any: pages are raw boto3 response dicts.
        self.payload: dict[str, list[dict[str, Any]]] = {}
        self.notes: list[str] = []
        self.outcomes: list[str] = []
        self.budget_exhausted = False
        self._denied: set[str] = set()

    def call(self, client: Any, operation: str, **kwargs: Any) -> dict[str, Any]:
        """Call one operation with retries. Any: boto3 client, params and response."""
        if operation not in self._allowed or operation.startswith(FORBIDDEN_PREFIXES):
            raise ForbiddenOperationError(f"{operation} is not an allowed read-only operation")
        if operation in self._denied:
            raise OperationDeniedError(operation, "AccessDenied (earlier call)")
        method = getattr(client, _method_name(client, operation))
        for attempt in range(self.scope.max_attempts):
            if self.calls >= self.scope.max_calls:
                raise BudgetExceededError
            self.calls += 1
            self.operations_called.append(operation)
            try:
                response: dict[str, Any] = method(**kwargs)
            except ClientError as exc:
                code = str(exc.response.get("Error", {}).get("Code", "Unknown"))
                if code in ACCESS_DENIED_CODES:
                    self._denied.add(operation)
                    raise OperationDeniedError(operation, code) from None
                if code in THROTTLING_CODES:
                    if attempt + 1 < self.scope.max_attempts:
                        self._sleep(self.scope.base_delay_seconds * (2**attempt))
                        continue
                    raise ThrottledOutError(operation, code) from None
                raise OperationFailedError(operation, code) from None
            except BotoCoreError as exc:
                raise OperationFailedError(operation, type(exc).__name__) from None
            return {k: v for k, v in response.items() if k != "ResponseMetadata"}
        raise ThrottledOutError(operation, "Throttling")  # pragma: no cover

    def paginate(self, client: Any, operation: str, params: dict[str, Any], token_in: str,
                 token_out: str) -> Iterator[dict[str, Any]]:
        """Yield every page of a token-paginated operation. Any: boto3 values."""
        token: str | None = None
        seen: set[str] = set()
        while True:
            args = dict(params)
            if token:
                args[token_in] = token
            page = self.call(client, operation, **args)
            yield page
            next_token = page.get(token_out)
            if not next_token:
                return
            if next_token in seen:
                self.notes.append(f"{operation}: repeated pagination token; stopped")
                return
            seen.add(next_token)
            token = str(next_token)

    def record(self, operation: str, page: dict[str, Any], **metadata: str) -> None:
        """Store a page, adding collector metadata under ``_``-prefixed keys."""
        stored = dict(page)
        for key, value in sorted(metadata.items()):
            stored[f"_{key}"] = value
        self.payload.setdefault(operation, []).append(stored)

    def step(self, label: str, fn: Callable[[], None]) -> bool:
        """Run one collection step. Returns False when collection must stop (budget)."""
        try:
            fn()
        except BudgetExceededError:
            self.budget_exhausted = True
            self.notes.append(f"call budget of {self.scope.max_calls} calls exhausted "
                              f"during {label}")
            return False
        except OperationDeniedError as exc:
            self.outcomes.append("denied")
            self.notes.append(f"{exc.operation}: access denied ({exc.code})")
            return True
        except ThrottledOutError as exc:
            self.outcomes.append("error")
            self.notes.append(f"{exc.operation}: still throttled after "
                              f"{self.scope.max_attempts} attempts ({exc.code})")
            return True
        except OperationFailedError as exc:
            self.outcomes.append("error")
            self.notes.append(f"{exc.operation}: failed ({exc.code})")
            return True
        self.outcomes.append("ok")
        return True

    def pages_recorded(self) -> bool:
        """True if any API page (not metadata) was stored."""
        return any(pages for op, pages in self.payload.items() if not op.startswith("_"))

    def status(self, has_data: bool) -> SnapshotStatus:
        """Derive the snapshot status from the step outcomes."""
        if self.budget_exhausted:
            return "partial"
        failed = [o for o in self.outcomes if o != "ok"]
        if failed:
            if self.pages_recorded():
                return "partial"
            return "denied" if all(o == "denied" for o in failed) else "error"
        return "ok" if has_data else "empty"


class BaseCollector:
    """Shared ``collect`` implementation; subclasses implement ``_run``."""

    source: ClassVar[SourceName]
    allowed_operations: ClassVar[frozenset[str]]

    def __init__(self, sleep: SleepFn = time.sleep) -> None:
        self._sleep = sleep

    def collect(self, client_factory: ClientFactory, account_id: str, region: str,
                scope: CollectionScope) -> RawSnapshot:
        """Collect one source for one account and region. Never raises on denial."""
        validate_account_id(account_id)
        recorder = CallRecorder(scope, self._sleep, self.allowed_operations)
        self._run(recorder, client_factory, account_id, region, scope)
        draft = RawSnapshot(
            snapshot_id=f"{self.source}-{account_id}-{region}-{scope.now:%Y%m%dT%H%M%SZ}",
            source=self.source, account_id=account_id, region=region,
            collected_at=scope.now, operations_called=recorder.operations_called,
            payload=recorder.payload, status="ok", notes=recorder.notes,
        )
        return draft.model_copy(update={"status": recorder.status(draft.has_data())})

    def _run(self, recorder: CallRecorder, client_factory: ClientFactory, account_id: str,
             region: str, scope: CollectionScope) -> None:
        raise NotImplementedError
