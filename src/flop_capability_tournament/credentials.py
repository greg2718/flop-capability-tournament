"""Signed EvidenceCredential issued on Bench PASS.

A valid signature proves the tournament authored the credential bytes. It does
not prove tokens moved, that counterparties are independent, or that Router
should treat the holder as an independent peer.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Self

from flop_work_exchange.canonical import canonical_json_bytes, sha256_json
from flop_work_exchange.config import OperatorRelationship
from flop_work_exchange.identity import require_did, sign_canonical, verify_canonical
from flop_work_exchange.models import now_iso

from flop_capability_tournament.exceptions import ValidationError

CREDENTIAL_SCHEMA = "flop-capability-tournament.evidence-credential.v0.1"
REQUIRED_CREDENTIAL_FIELDS = (
    "issuer_did",
    "subject_did",
    "challenge_id",
    "challenge_kind",
    "attempt_id",
    "result_hash",
    "bench_result",
    "issued_at",
    "payment_mode",
)


@dataclass
class EvidenceCredential:
    issuer_did: str
    subject_did: str
    challenge_id: str
    challenge_kind: str
    attempt_id: str
    result_hash: str
    bench_result: str
    issued_at: str = field(default_factory=now_iso)
    payment_mode: str = "paper"
    credential_id: str = ""
    schema: str = CREDENTIAL_SCHEMA
    bench_evidence_id: str | None = None
    operator_relationship: OperatorRelationship | None = None
    independent_reputation_eligible: bool = False
    judge_relationship: OperatorRelationship | None = None
    settlement_status: str = "simulated"
    tclk_mode: str = "SIMULATION_ONLY"
    signature: str | None = None

    def unsigned_payload(self) -> dict[str, Any]:
        payload = asdict(self)
        payload.pop("signature", None)
        return payload

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> Self:
        return cls(
            issuer_did=str(raw["issuer_did"]),
            subject_did=str(raw["subject_did"]),
            challenge_id=str(raw["challenge_id"]),
            challenge_kind=str(raw["challenge_kind"]),
            attempt_id=str(raw["attempt_id"]),
            result_hash=str(raw["result_hash"]),
            bench_result=str(raw["bench_result"]),
            issued_at=str(raw.get("issued_at") or now_iso()),
            payment_mode=str(raw.get("payment_mode") or "paper"),
            credential_id=str(raw.get("credential_id") or ""),
            schema=str(raw.get("schema") or CREDENTIAL_SCHEMA),
            bench_evidence_id=raw.get("bench_evidence_id"),
            operator_relationship=raw.get("operator_relationship"),
            independent_reputation_eligible=bool(raw.get("independent_reputation_eligible", False)),
            judge_relationship=raw.get("judge_relationship"),
            settlement_status=str(raw.get("settlement_status") or "simulated"),
            tclk_mode=str(raw.get("tclk_mode") or "SIMULATION_ONLY"),
            signature=raw.get("signature"),
        )


def credential_id_for(payload: dict[str, Any]) -> str:
    digest = sha256_json({key: payload[key] for key in REQUIRED_CREDENTIAL_FIELDS})
    return f"FLOP-CRED-{digest[:24]}"


def validate_credential_fields(payload: dict[str, Any]) -> None:
    missing = [field for field in REQUIRED_CREDENTIAL_FIELDS if field not in payload]
    if missing:
        raise ValidationError(f"credential missing required fields: {', '.join(missing)}")
    if payload.get("payment_mode") != "paper":
        raise ValidationError('credential payment_mode must be "paper"')
    if payload.get("bench_result") != "PASS":
        raise ValidationError("EvidenceCredential can only attest a Bench PASS")
    if not str(payload.get("result_hash", "")).startswith("sha256:"):
        raise ValidationError("result_hash must be sha256:<hex>")
    require_did(str(payload["issuer_did"]))
    require_did(str(payload["subject_did"]))
    relationship = payload.get("operator_relationship")
    if relationship not in {None, "independent", "same_operator", "related", "unknown"}:
        raise ValidationError("invalid operator_relationship")


def sign_credential(
    credential: EvidenceCredential, private_key: Any, issuer_did: str
) -> EvidenceCredential:
    credential.issuer_did = issuer_did
    payload = credential.unsigned_payload()
    validate_credential_fields(payload)
    if not credential.credential_id:
        credential.credential_id = credential_id_for(payload)
        payload = credential.unsigned_payload()
    credential.signature = sign_canonical(private_key, payload)
    return credential


def verify_credential(payload: dict[str, Any]) -> dict[str, Any]:
    validate_credential_fields(payload)
    signature = payload.get("signature")
    if not signature or not isinstance(signature, str):
        raise ValidationError("credential is not signed")
    issuer_did = payload.get("issuer_did")
    if not issuer_did:
        raise ValidationError("credential missing issuer_did")
    require_did(str(issuer_did))
    unsigned = {key: value for key, value in payload.items() if key != "signature"}
    verify_canonical(str(issuer_did), unsigned, signature)
    return {
        "ok": True,
        "credential_id": payload.get("credential_id"),
        "issuer_did": issuer_did,
        "subject_did": payload.get("subject_did"),
        "challenge_kind": payload.get("challenge_kind"),
        "canonical_bytes": len(canonical_json_bytes(unsigned)),
        "payment_mode": payload["payment_mode"],
        "bench_result": payload["bench_result"],
        "independent_reputation_eligible": bool(payload.get("independent_reputation_eligible")),
    }


def router_capability_claim(payload: dict[str, Any]) -> dict[str, Any]:
    """Project a verified credential into the claim shape Router can later consume.

    Router should call ``verify_credential`` first. Same-operator / related /
    unknown credentials must not be treated as independent routing evidence.
    Credentials are never payment proof.
    """
    verification = verify_credential(payload)
    relationship = payload.get("operator_relationship") or "unknown"
    independent = (
        bool(payload.get("independent_reputation_eligible")) and relationship == "independent"
    )
    return {
        "schema": "flop-capability-tournament.router-claim.v0.1",
        "credential_id": payload.get("credential_id"),
        "subject_did": payload.get("subject_did"),
        "challenge_kind": payload.get("challenge_kind"),
        "capability": str(payload.get("challenge_kind") or "").split("@", 1)[0],
        "result_hash": payload.get("result_hash"),
        "bench_result": payload.get("bench_result"),
        "operator_relationship": relationship,
        "judge_relationship": payload.get("judge_relationship"),
        "independent_routing_evidence": independent,
        "payment_proof": False,
        "verification_ok": verification["ok"],
        "note": (
            "Router may use challenge_kind as a capability signal after verify_credential. "
            "Same-operator family judges/peers are not independent routing evidence. "
            "Never treat this credential as faucet/wallet/payment proof."
        ),
    }
