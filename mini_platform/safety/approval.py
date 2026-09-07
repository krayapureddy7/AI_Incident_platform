"""
Cryptographically bound human approval tokens.

A Tier-3 action halts until a human authorizes it. That authorization is carried
by a token, so the token is the only thing standing between a proposal and a
mutation of production infrastructure. It therefore has to be unforgeable and
non-transferable:

- **Unforgeable** -- the token carries an HMAC-SHA256 signature over its payload.
  Without the signing secret a caller cannot mint one, so knowing the token
  format is not enough to approve anything.
- **Bound to one proposal** -- the signed payload pins the action id, tool,
  service, environment, tenant, and a hash of the parameters. A token issued to
  restart one service cannot be replayed to scale a different one, and editing
  any bound field invalidates the signature.
- **Time-limited** -- an approval expires, so a token recovered from a log or a
  ticket long after the fact is inert.

Replay within the bound action is deliberately *not* blocked here: one run
verifies the same token more than once (the Verifier node, then the Tool
Gateway), and re-adjudication on resume is a design goal. Duplicate *execution*
is the Tool Gateway's idempotency responsibility, not the token's.

Signing secret
--------------
``APPROVAL_SIGNING_SECRET`` supplies the key. When it is unset the module
generates a random per-process key instead of falling back to a shipped default:
an unset secret must never mean a guessable one. Tokens then work within a
single process (tests, the demo, a one-shot CLI run) but not across a restart or
between the API and a separate approver process. Production deployments, and any
flow where the approver runs separately from the orchestrator, must set it.

Scope note
----------
This module answers "is this approval authentic and for this action?". It does
not answer "is this person allowed to approve?" -- that needs an authenticated
identity, which the API layer does not yet have. The ``approver`` field is
recorded in the audit trail as a claim, not as a verified identity.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
import warnings
from dataclasses import dataclass
from typing import Any, Dict, Optional

from app.tools.contracts import HUMAN_APPROVAL_TOKEN_PREFIX

#: Version of the approval token format and signing scheme.
__version__ = "1.0.0"

#: Payload schema version, embedded in every token so the format can evolve.
TOKEN_FORMAT_VERSION = 1

#: Default validity window for a freshly minted approval.
DEFAULT_TTL_SECONDS = 900

#: Small allowance for clock skew between the minting and verifying hosts.
CLOCK_SKEW_TOLERANCE_SECONDS = 30

#: Environment variable holding the HMAC signing key.
SIGNING_SECRET_ENV_VAR = "APPROVAL_SIGNING_SECRET"

_EPHEMERAL_SECRET: Optional[bytes] = None


class ApprovalTokenError(ValueError):
    """Raised when a token cannot be minted from the supplied proposal."""


@dataclass(frozen=True)
class ApprovalVerdict:
    """Outcome of verifying an approval token against a specific proposal."""

    valid: bool
    reason: str
    approver: Optional[str] = None
    expires_at: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "valid": self.valid,
            "reason": self.reason,
            "approver": self.approver,
            "expires_at": self.expires_at,
        }


# ---------------------------------------------------------------------------
# Signing key
# ---------------------------------------------------------------------------
def _signing_secret() -> bytes:
    """
    Resolve the HMAC key.

    Falls back to a random per-process key rather than a constant, so a missing
    configuration degrades to "approvals do not survive this process" instead of
    "approvals are forgeable by anyone reading the source".
    """
    global _EPHEMERAL_SECRET

    configured = os.environ.get(SIGNING_SECRET_ENV_VAR)
    if configured:
        return configured.encode("utf-8")

    if _EPHEMERAL_SECRET is None:
        _EPHEMERAL_SECRET = secrets.token_bytes(32)
        warnings.warn(
            f"{SIGNING_SECRET_ENV_VAR} is not set; approval tokens are signed with an "
            "ephemeral per-process key and will not verify after a restart or in "
            "another process. Set it for any multi-process or production deployment.",
            RuntimeWarning,
            stacklevel=2,
        )
    return _EPHEMERAL_SECRET


def reset_ephemeral_secret() -> None:
    """Drop the cached per-process key. Intended for tests."""
    global _EPHEMERAL_SECRET
    _EPHEMERAL_SECRET = None


# ---------------------------------------------------------------------------
# Binding
# ---------------------------------------------------------------------------
def _field(proposal: Any, name: str, default: Any = None) -> Any:
    """Read a field from either an ActionProposal dataclass or a plain dict."""
    if isinstance(proposal, dict):
        value = proposal.get(name, default)
    else:
        value = getattr(proposal, name, default)
    return default if value is None else value


def _parameters_digest(params: Any) -> str:
    """Stable hash of the proposal parameters, so edited arguments break the seal."""
    if not isinstance(params, dict):
        params = {}
    canonical = json.dumps(params, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def proposal_binding(proposal: Any) -> Dict[str, Any]:
    """
    Extract the identity an approval is issued against.

    Every field here is signed, so any divergence between the approved proposal
    and the executed one invalidates the token.
    """
    action_id = _field(proposal, "action_id", "")
    if not action_id:
        raise ApprovalTokenError("Proposal has no action_id; cannot bind an approval to it.")

    return {
        "action_id": str(action_id),
        "tool": str(_field(proposal, "tool_name", "")),
        "service": str(_field(proposal, "service", "")),
        "env": str(_field(proposal, "environment", "")),
        "tenant": str(_field(proposal, "tenant", "default")),
        "params_sha256": _parameters_digest(_field(proposal, "parameters", {})),
    }


# ---------------------------------------------------------------------------
# Encoding helpers
# ---------------------------------------------------------------------------
def _b64u_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64u_decode(text: str) -> bytes:
    padding = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + padding)


def _sign(payload_bytes: bytes) -> str:
    return _b64u_encode(hmac.new(_signing_secret(), payload_bytes, hashlib.sha256).digest())


# ---------------------------------------------------------------------------
# Mint / verify
# ---------------------------------------------------------------------------
def mint_approval_token(
    proposal: Any,
    approver: str = "unknown",
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
    now: Optional[float] = None,
) -> str:
    """
    Issue a signed approval for one specific proposal.

    Args:
        proposal: The ``ActionProposal`` (or its dict form) being approved.
        approver: Claimed identity of the approving human, recorded for audit.
        ttl_seconds: Validity window. Keep it short; approvals are for the
            incident in front of the approver, not standing authority.
        now: Issue time override, for tests.

    Returns:
        A token string carrying the ``TOKEN-HUMAN-APPROVED-`` prefix.
    """
    if ttl_seconds <= 0:
        raise ApprovalTokenError("ttl_seconds must be positive.")

    issued_at = time.time() if now is None else now
    payload = dict(proposal_binding(proposal))
    payload.update(
        {
            "v": TOKEN_FORMAT_VERSION,
            "approver": str(approver or "unknown"),
            "iat": round(issued_at, 3),
            "exp": round(issued_at + ttl_seconds, 3),
            "nonce": secrets.token_hex(8),
        }
    )

    payload_bytes = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return (
        f"{HUMAN_APPROVAL_TOKEN_PREFIX}{_b64u_encode(payload_bytes)}.{_sign(payload_bytes)}"
    )


def verify_approval_token(
    token: Optional[str],
    proposal: Any,
    now: Optional[float] = None,
) -> ApprovalVerdict:
    """
    Check that a token is authentic, unexpired, and issued for this proposal.

    Returns a verdict rather than raising, so a bad token is a policy rejection
    the safety engine can report -- never an exception that escapes the
    evaluation path.
    """
    if not token:
        return ApprovalVerdict(False, "No human approval token supplied.")

    if not token.startswith(HUMAN_APPROVAL_TOKEN_PREFIX):
        return ApprovalVerdict(False, "Approval token is malformed: unrecognised prefix.")

    body = token[len(HUMAN_APPROVAL_TOKEN_PREFIX):]
    encoded_payload, separator, signature = body.partition(".")
    if not separator or not encoded_payload or not signature:
        return ApprovalVerdict(False, "Approval token is malformed: missing signature.")

    try:
        payload_bytes = _b64u_decode(encoded_payload)
    except (ValueError, TypeError):
        return ApprovalVerdict(False, "Approval token is malformed: undecodable payload.")

    # Authenticate before parsing, so only signed bytes are ever interpreted.
    if not hmac.compare_digest(_sign(payload_bytes), signature):
        return ApprovalVerdict(False, "Approval token signature is invalid.")

    try:
        payload = json.loads(payload_bytes.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return ApprovalVerdict(False, "Approval token is malformed: unreadable payload.")
    if not isinstance(payload, dict):
        return ApprovalVerdict(False, "Approval token is malformed: payload is not an object.")

    if payload.get("v") != TOKEN_FORMAT_VERSION:
        return ApprovalVerdict(
            False, f"Approval token format version {payload.get('v')!r} is not supported."
        )

    approver = payload.get("approver")
    expires_at = payload.get("exp")

    current = time.time() if now is None else now
    if not isinstance(expires_at, (int, float)):
        return ApprovalVerdict(False, "Approval token has no expiry.", approver)
    if current > expires_at + CLOCK_SKEW_TOLERANCE_SECONDS:
        return ApprovalVerdict(
            False,
            f"Approval token expired at {expires_at}; approvals are not standing authority.",
            approver,
            expires_at,
        )

    try:
        expected = proposal_binding(proposal)
    except ApprovalTokenError as exc:
        return ApprovalVerdict(False, str(exc), approver, expires_at)

    for field_name, expected_value in expected.items():
        if payload.get(field_name) != expected_value:
            return ApprovalVerdict(
                False,
                (
                    f"Approval token was issued for a different action: {field_name} "
                    f"{payload.get(field_name)!r} does not match {expected_value!r}."
                ),
                approver,
                expires_at,
            )

    return ApprovalVerdict(
        True, f"Approval verified for action {expected['action_id']}.", approver, expires_at
    )
