"""Call labels shared by the cost collectors and normalisers.

Status: real
Purpose: one place for the label of each cost call and the payload key it is stored under.
Inputs: none.
Outputs: label constants, ``payload_key``, ``split_payload_key``, ``LABELS_KEY``, ``LabelStatus``.
Planned dependencies: none.
Notes: ``RawSnapshot.payload`` maps a key to a list of pages, so each call is stored under
    ``"<Operation>:<label>"`` (Section 10.2 allows this form). The ``_labels`` metadata entry holds
    one ``{"label", "operation", "status", "code"}`` record per call, in call order.
"""

from __future__ import annotations

from typing import Literal

LABELS_KEY = "_labels"

SPEND_BY_SERVICE = "spend_by_service_account_monthly"
WINDOW_TOTAL = "window_total_by_account"
TAG_KEYS_SEEN = "tag_keys_seen"
TAG_SPEND_PREFIX = "spend_by_tag_monthly:"
RESOURCE_COSTS_PREFIX = "resource_costs_daily:"

ENROLMENT = "enrolment"
LIST_RECOMMENDATIONS = "list_recommendations"
SUMMARY_PREFIX = "summary_account:"

LabelStatus = Literal["ok", "empty", "denied", "not_enabled", "partial", "error", "skipped"]


def payload_key(operation: str, label: str) -> str:
    """``GetCostAndUsage:spend_by_service_account_monthly``."""
    return f"{operation}:{label}"


def split_payload_key(key: str) -> tuple[str, str]:
    """``(operation, label)`` for a payload key; the label may itself contain ``:``."""
    operation, _, label = key.partition(":")
    return operation, label
