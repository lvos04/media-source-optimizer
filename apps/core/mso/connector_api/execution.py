"""Controle van het door de Hub ondertekende uitvoeringsverzoek (contract §8)."""

import base64
import hmac
import json
import re
from datetime import UTC, datetime
from typing import Annotated, Literal
from uuid import UUID

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from fastapi import Request
from pydantic import BaseModel, ConfigDict, Field

from mso import changes
from mso.auth import current_principal
from mso.backups import EVENT_HISTORY_KEY
from mso.change_actions import require_action
from mso.change_preview import canonical_json, digest
from mso.change_requests import resource_etag
from mso.config import get_settings
from mso.connector_api.errors import ConnectorError
from mso.connector_api.events import APP_ID
from mso.db import get_db

SHA = r"^sha256:[0-9a-f]{64}$"
ISO = r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$"
UUID_FORMAT = r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
Hash = Annotated[str, Field(pattern=SHA)]
Timestamp = Annotated[str, Field(pattern=ISO)]
Identifier = Annotated[str, Field(pattern=UUID_FORMAT)]
Text = Annotated[str, Field(min_length=1, max_length=256)]
Risk = Literal["R0", "R1", "R2", "R3"]
Mode = Literal["apply", "rollback"]


class StrictBody(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class ChangeTarget(StrictBody):
    app_id: Literal[APP_ID]
    change_id: int = Field(ge=1, le=2**63 - 1)


class ProposalActor(StrictBody):
    type: Text
    id: Text


class ApplyParameters(StrictBody):
    expected_preview_hash: Hash


class RollbackParameters(StrictBody):
    expected_prev: Hash


class Proposal(StrictBody):
    idempotency_key: Identifier
    action_id: Text
    target: ChangeTarget
    parameters: ApplyParameters | RollbackParameters
    reason: Annotated[str, Field(max_length=2000)]
    actor: ProposalActor
    expected_version: Hash
    risk_claim: Risk | None
    evidence_ids: list[Text] = Field(max_length=200)
    expires_in_seconds: int = Field(ge=1)


class ActionScope(StrictBody):
    action_id: Text
    target: ChangeTarget
    parameters_sha256: Hash


class Approval(StrictBody):
    approval_id: Identifier
    request_id: Identifier
    preview_hash: Hash
    resource_etag: Hash
    policy_hash: Hash
    risk_class: Risk
    approved_by: Text
    approved_at: Timestamp
    expires_at: Timestamp
    one_time_nonce: Identifier
    backup_checkpoint_id: Text | None
    action_scope: ActionScope


class PolicyDecision(StrictBody):
    risk_class: Risk
    rule_ids: list[Text] = Field(min_length=1, max_length=200)
    policy_hash: Hash


class ExecutionRequest(StrictBody):
    proposal: Proposal
    approval: Approval | None
    policy_decision: PolicyDecision
    issued_at: Timestamp
    expires_at: Timestamp
    nonce: Identifier
    signature: Annotated[str, Field(pattern=r"^[A-Za-z0-9_-]{86}$")]


class Readback(StrictBody):
    verified: bool
    results: list[str]


class ExecutionResult(StrictBody):
    change_id: int
    status: Literal["applied", "rolled_back"]
    preview_hash: Hash
    resource_etag: Hash
    before_hash: Hash
    after_hash: Hash
    readback: Readback


class DiscoveredAction(StrictBody):
    action_id: str
    target: str
    description: str
    rollback: bool
    min_risk: Risk
    requires_backup: bool


def _base64url(encoded: str) -> bytes:
    raw = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))
    if base64.urlsafe_b64encode(raw).decode().rstrip("=") != encoded:
        raise ValueError("Ongeldige base64url")
    return raw


def policy_pin() -> tuple[Ed25519PublicKey, str] | None:
    settings = get_settings()
    if not re.fullmatch(SHA, settings.hub_policy_hash):
        return None
    try:
        jwk = json.loads(settings.hub_policy_public_key)
        if not isinstance(jwk, dict) or jwk.get("kty") != "OKP" or jwk.get("crv") != "Ed25519":
            return None
        if set(jwk) - {"kty", "crv", "x", "kid", "use", "alg"}:
            return None
        if jwk.get("alg", "EdDSA") != "EdDSA" or jwk.get("use", "sig") != "sig":
            return None
        key = Ed25519PublicKey.from_public_bytes(_base64url(jwk["x"]))
    except (KeyError, TypeError, ValueError):
        return None
    return key, settings.hub_policy_hash


def require_pin(request: Request) -> tuple[Ed25519PublicKey, str]:
    pin = policy_pin()
    if pin is None:
        # Behoud de toegangscontrole van de uitgeschakelde routes.
        current_principal(request)
        raise ConnectorError(501, "capability_unavailable")
    return pin


def _time(value: str) -> datetime:
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    except ValueError:
        raise ConnectorError(422, "validation_error") from None


def verify_signature(body: ExecutionRequest, pin: tuple[Ed25519PublicKey, str]) -> None:
    key, _ = pin
    try:
        key.verify(_base64url(body.signature), canonical_json(body.model_dump(exclude={"signature"})))
    except (InvalidSignature, ValueError):
        raise ConnectorError(401, "unauthorized") from None


def verify_approval(body: ExecutionRequest, trusted_hash: str, mode: Mode, *, check_time: bool = True) -> Approval:
    issued, expires = _time(body.issued_at), _time(body.expires_at)
    if not issued < expires or expires - issued > changes.APPROVAL_TTL:
        raise ConnectorError(409, "approval_expired")
    approval = body.approval
    if approval is None:
        raise ConnectorError(409, "approval_required")
    approved, approval_expires = _time(approval.approved_at), _time(approval.expires_at)
    if not approved <= issued < approval_expires:
        raise ConnectorError(409, "approval_expired")
    if approval_expires - approved > changes.APPROVAL_TTL:
        raise ConnectorError(409, "approval_expired")
    if check_time and not issued <= changes._now() < min(expires, approval_expires):
        raise ConnectorError(409, "approval_expired")

    proposal, decision = body.proposal, body.policy_decision
    if not hmac.compare_digest(decision.policy_hash, trusted_hash):
        raise ConnectorError(403, "forbidden")
    if decision.risk_class not in ("R2", "R3") or approval.risk_class != decision.risk_class:
        raise ConnectorError(403, "forbidden")
    scope = approval.action_scope
    if (approval.policy_hash != decision.policy_hash or approval.request_id != proposal.idempotency_key
            or approval.resource_etag != proposal.expected_version or scope.action_id != proposal.action_id
            or scope.target != proposal.target or scope.parameters_sha256 != digest(proposal.parameters.model_dump())):
        raise ConnectorError(409, "approval_required")
    if (mode == "apply") != isinstance(proposal.parameters, ApplyParameters):
        raise ConnectorError(422, "validation_error")
    return approval


def verify_change(body: ExecutionRequest, approval: Approval, ch: dict, mode: Mode) -> None:
    # Het einde van elk herstel markeert atomair een nieuwe historie, ook na een onderbroken herstel.
    restored_at = get_db().scalar("SELECT updated_at FROM system_state WHERE key=%s", (EVENT_HISTORY_KEY,))
    if restored_at is not None and _time(approval.approved_at) <= restored_at:
        raise ConnectorError(409, "approval_expired")
    proposal, params = body.proposal, body.proposal.parameters
    require_action(ch, proposal.action_id)
    changes.check_preview(ch, approval.preview_hash)
    if isinstance(params, ApplyParameters):
        changes.check_preview(ch, params.expected_preview_hash)
    etag = resource_etag(ch) if mode == "rollback" else ch["preview_hash"]
    if (proposal.expected_version != etag or isinstance(params, RollbackParameters) and params.expected_prev != etag):
        raise ConnectorError(412, "precondition_failed", {"huidige_etag": etag})


def nonces(body: ExecutionRequest, approval: Approval) -> dict[str, UUID]:
    return {"nonce": UUID(body.nonce), "approval_nonce": UUID(approval.one_time_nonce)}
