"""Workspace: the approved scope for one customer.

Status: partial
Purpose: list of approved accounts and regions for a customer engagement.
Inputs: workspace JSON file.
Outputs: ``Workspace``.
Planned dependencies: pydantic.
Notes: how the approved list is obtained (organisation API or supplied by hand) is an open
    question; see docs/OPEN_QUESTIONS.md.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from workload_graph.ids import AccountId


class Workspace(BaseModel):
    """Approved scope. Seeing a reference to another account never grants access to it."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    workspace_id: str
    customer: str
    approved_account_ids: list[AccountId]
    regions: list[str] = []
    # Fixed name for every member account (D40). Customers create one role with this name.
    role_name: str = "TamReadOnly"
    external_id: str | None = None

    def is_approved(self, account_id: str) -> bool:
        """True if ``account_id`` is in the approved list."""
        return account_id in self.approved_account_ids
