"""Voice approval requires spoken intent and typed request ID confirmation."""

from __future__ import annotations

import re

from hive.core.approvals import ApprovalError, ApprovalStore
from hive.core.models import ApprovalRequest, ApprovalStatus


def approve_from_voice(store: ApprovalStore, approval_id: str, transcript: str,
                       typed_confirmation: str) -> ApprovalRequest:
    request = store.get(approval_id)
    if request.status != ApprovalStatus.PENDING:
        raise ApprovalError("Approval is no longer pending")
    normalized = re.sub(r"[^a-z0-9]", "", transcript.lower())
    spoken = any(f"{verb}{approval_id.lower()}" in normalized
                 for verb in ("approve", "manzoor", "ijazat"))
    if not spoken or typed_confirmation.strip().lower() != approval_id.lower():
        raise ApprovalError("Voice approval requires the spoken approval ID and exact typed confirmation")
    return store.decide(approval_id, approve=True, note="Voice intent and typed ID confirmed")
