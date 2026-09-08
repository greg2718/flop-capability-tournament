from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field, fields
from enum import StrEnum
from pathlib import Path
from typing import Any, Self, TypeVar

from flop_work_exchange.amounts import parse_flop_to_micro, parse_micro
from flop_work_exchange.config import OperatorRelationship
from flop_work_exchange.identity import is_valid_ed25519_did
from flop_work_exchange.models import now_iso

from flop_capability_tournament.constants import CHALLENGE_KINDS
from flop_capability_tournament.exceptions import ValidationError

T = TypeVar("T")

KIND_ID_RE = re.compile(r"^([a-z0-9-]+)@(\d+\.\d+\.\d+)$")


class ChallengeKind(StrEnum):
    EXTRACT_STRUCTURED_INFO = "extract-structured-info"
    DETECT_MALICIOUS_INSTRUCTIONS = "detect-malicious-instructions"
    SUMMARIZE_TECHNOCORE_DISCUSSION = "summarize-technocore-discussion"
    VERIFY_SIGNED_MESSAGES = "verify-signed-messages"
    CLASSIFY_SERVICE_CLAIMS = "classify-service-claims"
    FIND_PROTOCOL_CONTRADICTION = "find-protocol-contradiction"
    PRODUCE_VALID_TCLK_OFFER = "produce-valid-tclk-offer"
    IDENTIFY_DUPLICATE_OR_COORDINATED_ACTIVITY = "identify-duplicate-or-coordinated-activity"


class ChallengeStatus(StrEnum):
    PUBLISHED = "PUBLISHED"
    CLOSED = "CLOSED"


class AttemptStatus(StrEnum):
    SUBMITTED = "SUBMITTED"
    REJECTED = "REJECTED"
    EVALUATED = "EVALUATED"
    FAILED = "FAILED"
    AWARDED = "AWARDED"


def _from_mapping(cls: type[T], raw: dict[str, Any]) -> T:
    allowed = {item.name for item in fields(cls)}  # type: ignore[arg-type]
    payload = {key: value for key, value in raw.items() if key in allowed}
    return cls(**payload)


def _require_str(raw: dict[str, Any], key: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValidationError(f"{key} is required")
    return value


def parse_kind_id(kind_id: str) -> tuple[str, str]:
    match = KIND_ID_RE.fullmatch(kind_id.strip())
    if not match:
        raise ValidationError(
            f"challenge kind id must look like extract-structured-info@1.0.0; got {kind_id!r}"
        )
    kind, version = match.group(1), match.group(2)
    if kind not in CHALLENGE_KINDS:
        raise ValidationError(f"unknown challenge kind {kind!r}; expected one of {CHALLENGE_KINDS}")
    return kind, version


def kind_id_for(kind: str, version: str) -> str:
    kind_id = f"{kind}@{version}"
    parse_kind_id(kind_id)
    return kind_id


@dataclass
class ChallengeSpec:
    title: str
    kind: str
    version: str
    summary: str
    fixture: dict[str, Any]
    evaluation: dict[str, Any]
    entry_fee_micro: int
    prize_micro: int
    schema: str = "flop-capability-tournament.challenge.v0.1"
    notes: str = ""
    source_url: str | None = None

    @property
    def kind_id(self) -> str:
        return kind_id_for(self.kind, self.version)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["kind_id"] = self.kind_id
        return payload

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> Self:
        if not isinstance(raw, dict):
            raise ValidationError("challenge spec must be an object")
        kind = str(raw.get("kind") or "")
        version = str(raw.get("version") or "1.0.0")
        if raw.get("kind_id"):
            parsed_kind, parsed_version = parse_kind_id(str(raw["kind_id"]))
            kind = kind or parsed_kind
            version = str(raw.get("version") or parsed_version)
            if kind != parsed_kind:
                raise ValidationError("kind does not match kind_id")
        if kind not in CHALLENGE_KINDS:
            raise ValidationError(
                f"unknown challenge kind {kind!r}; expected one of {', '.join(CHALLENGE_KINDS)}"
            )
        if not re.fullmatch(r"\d+\.\d+\.\d+", version):
            raise ValidationError("version must be semver like 1.0.0")
        entry_micro = raw.get("entry_fee_micro")
        if entry_micro is None:
            if raw.get("entry_fee_flop") is None:
                raise ValidationError("entry_fee_flop or entry_fee_micro is required")
            entry_micro = parse_flop_to_micro(raw["entry_fee_flop"])
        else:
            entry_micro = parse_micro(entry_micro)
        prize_micro = raw.get("prize_micro")
        if prize_micro is None:
            if raw.get("prize_flop") is None:
                raise ValidationError("prize_flop or prize_micro is required")
            prize_micro = parse_flop_to_micro(raw["prize_flop"])
        else:
            prize_micro = parse_micro(prize_micro)
        if entry_micro <= 0 or prize_micro <= 0:
            raise ValidationError("entry fee and prize must be positive")
        fixture = raw.get("fixture") or {}
        evaluation = raw.get("evaluation") or {}
        if not isinstance(fixture, dict):
            raise ValidationError("fixture must be an object")
        if not isinstance(evaluation, dict):
            raise ValidationError("evaluation must be an object")
        source_url = raw.get("source_url")
        if source_url is not None and not isinstance(source_url, str):
            raise ValidationError("source_url must be a string when provided")
        return cls(
            schema=str(raw.get("schema") or "flop-capability-tournament.challenge.v0.1"),
            title=_require_str(raw, "title"),
            kind=kind,
            version=version,
            summary=_require_str(raw, "summary"),
            fixture=dict(fixture),
            evaluation=dict(evaluation),
            entry_fee_micro=entry_micro,
            prize_micro=prize_micro,
            notes=str(raw.get("notes") or ""),
            source_url=source_url,
        )


@dataclass
class Challenge:
    challenge_id: str
    publisher_did: str
    spec: ChallengeSpec
    status: str = ChallengeStatus.PUBLISHED.value
    payment_mode: str = "paper"
    created_at: str = field(default_factory=now_iso)
    catalog_job_id: str | None = None
    sentinel_status: str | None = None
    prize_pool_micro: int = 0
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["spec"] = self.spec.to_dict()
        return payload

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> Self:
        spec = ChallengeSpec.from_dict(raw.get("spec") or {})
        payload = dict(raw)
        payload["spec"] = spec
        challenge = _from_mapping(cls, payload)
        challenge.notes = list(raw.get("notes") or [])
        challenge.spec = spec
        return challenge


@dataclass
class Entry:
    entry_id: str
    challenge_id: str
    agent_did: str
    entry_fee_micro: int
    status: str = "PAID"
    payment_mode: str = "paper"
    job_id: str | None = None
    offer_id: str | None = None
    deal_id: str | None = None
    tclk_deal_id: str | None = None
    operator_relationship: OperatorRelationship | None = None
    independent_reputation_eligible: bool = False
    independent_fee_volume_eligible: bool = False
    wash_risk: bool = False
    created_at: str = field(default_factory=now_iso)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> Self:
        return _from_mapping(cls, raw)


@dataclass
class Sponsorship:
    sponsorship_id: str
    challenge_id: str
    sponsor_did: str
    amount_micro: int
    payment_mode: str = "paper"
    created_at: str = field(default_factory=now_iso)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> Self:
        return _from_mapping(cls, raw)


@dataclass
class Attempt:
    attempt_id: str
    challenge_id: str
    entry_id: str
    agent_did: str
    payload: dict[str, Any]
    status: str = AttemptStatus.SUBMITTED.value
    payment_mode: str = "paper"
    created_at: str = field(default_factory=now_iso)
    result_hash: str | None = None
    evidence_path: str | None = None
    job_id: str | None = None
    offer_id: str | None = None
    deal_id: str | None = None
    tclk_deal_id: str | None = None
    operator_relationship: OperatorRelationship | None = None
    independent_reputation_eligible: bool = False
    independent_fee_volume_eligible: bool = False
    wash_risk: bool = False
    sentinel_status: str | None = None
    bench_result: str | None = None
    bench_notes: str | None = None
    bench_evidence_id: str | None = None
    judge_relationship: OperatorRelationship | None = None
    credential_id: str | None = None
    receipt_bundle_id: str | None = None
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> Self:
        attempt = _from_mapping(cls, raw)
        attempt.payload = dict(raw.get("payload") or {})
        attempt.notes = list(raw.get("notes") or [])
        return attempt


@dataclass
class TournamentReceiptLeg:
    role: str
    receipt: dict[str, Any]
    verification: dict[str, Any]
    ledger_reason: str
    amount_micro: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> Self:
        return cls(
            role=str(raw["role"]),
            receipt=dict(raw.get("receipt") or {}),
            verification=dict(raw.get("verification") or {}),
            ledger_reason=str(raw.get("ledger_reason") or ""),
            amount_micro=int(raw.get("amount_micro") or 0),
        )


@dataclass
class TournamentReceiptBundle:
    bundle_id: str
    challenge_id: str
    attempt_id: str | None
    tournament_did: str
    payment_mode: str = "paper"
    settlement_status: str = "simulated"
    settlement_execution: str = "DISABLED"
    created_at: str = field(default_factory=now_iso)
    legs: list[TournamentReceiptLeg] = field(default_factory=list)
    schema: str = "flop-capability-tournament.receipt-bundle.v0.1"

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "bundle_id": self.bundle_id,
            "challenge_id": self.challenge_id,
            "attempt_id": self.attempt_id,
            "tournament_did": self.tournament_did,
            "payment_mode": self.payment_mode,
            "settlement_status": self.settlement_status,
            "settlement_execution": self.settlement_execution,
            "created_at": self.created_at,
            "legs": [leg.to_dict() for leg in self.legs],
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> Self:
        legs = [
            TournamentReceiptLeg.from_dict(item)
            for item in (raw.get("legs") or [])
            if isinstance(item, dict)
        ]
        return cls(
            bundle_id=str(raw["bundle_id"]),
            challenge_id=str(raw["challenge_id"]),
            attempt_id=raw.get("attempt_id"),
            tournament_did=str(raw["tournament_did"]),
            payment_mode=str(raw.get("payment_mode") or "paper"),
            settlement_status=str(raw.get("settlement_status") or "simulated"),
            settlement_execution=str(raw.get("settlement_execution") or "DISABLED"),
            created_at=str(raw.get("created_at") or now_iso()),
            legs=legs,
            schema=str(raw.get("schema") or "flop-capability-tournament.receipt-bundle.v0.1"),
        )


def did_slug(did: str) -> str:
    if not is_valid_ed25519_did(did):
        return "invalid-did"
    return did.replace(":", "_")


def load_challenge_file(path: Path) -> ChallengeSpec:
    suffix = path.suffix.lower()
    text = path.read_text(encoding="utf-8")
    if suffix == ".json":
        raw: Any = json.loads(text)
    elif suffix in {".yaml", ".yml"}:
        import yaml

        raw = yaml.safe_load(text)
    else:
        raise ValidationError(f"unsupported challenge format: {suffix}")
    if not isinstance(raw, dict):
        raise ValidationError("challenge file must contain an object")
    return ChallengeSpec.from_dict(raw)
