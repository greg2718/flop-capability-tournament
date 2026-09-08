"""Bench-shaped deterministic evaluators over local challenge fixtures.

Real Bench is https://github.com/greg2718/flop-bench. This stub never fetches
URLs or rooms and never runs local commands. Challenge kinds map onto passive
JSON / text checks so evaluation stays reproducible in tests and demos.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any
from urllib.parse import urlparse

from flop_work_exchange.canonical import sha256_json
from flop_work_exchange.models import BenchVerdict, Job
from flop_work_exchange.policy import reject_payment_proof_claims

from flop_capability_tournament.exceptions import InertUrlError, SafetyError, ValidationError
from flop_capability_tournament.models import Attempt, Challenge

TCLK_REQUIRED = {
    "payment_mode": "paper",
    "tclk_mode": "SIMULATION_ONLY",
    "settlement_execution": "DISABLED",
}


def looks_like_url(value: str) -> bool:
    text = value.strip()
    if not text:
        return False
    lowered = text.lower()
    if (
        lowered.startswith("http://")
        or lowered.startswith("https://")
        or lowered.startswith("www.")
    ):
        return True
    parsed = urlparse(text)
    return parsed.scheme in {"http", "https", "git", "ssh"}


def reject_url_fetch(value: Any) -> None:
    blob = json.dumps(value, sort_keys=True)
    if "http://" in blob or "https://" in blob or "git://" in blob:
        raise InertUrlError(
            "challenge URLs/fixtures are inert metadata; the tournament never fetches "
            "or follows untrusted URLs or Technocore rooms"
        )


def _require_mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValidationError(f"{label} must be an object")
    return value


def _pass_verdict(
    challenge: Challenge, observations: list[dict[str, Any]], notes: str
) -> BenchVerdict:
    evidence_id = "ev-" + sha256_json(
        {
            "challenge_id": challenge.challenge_id,
            "kind": challenge.spec.kind_id,
            "observations": observations,
            "adapter": "tournament-bench-stub",
        }
    )[:32]
    return BenchVerdict(
        result="PASS",
        evidence_id=evidence_id,
        notes=notes,
        local_exec=False,
    )


def _fail_verdict(notes: str) -> BenchVerdict:
    return BenchVerdict(result="FAIL", evidence_id=None, notes=notes, local_exec=False)


def _expected(challenge: Challenge) -> dict[str, Any]:
    evaluation = challenge.spec.evaluation
    expected = evaluation.get("expected") if isinstance(evaluation, dict) else None
    if not isinstance(expected, dict):
        return {}
    return expected


def evaluate_extract_structured_info(challenge: Challenge, payload: dict[str, Any]) -> BenchVerdict:
    expected = _expected(challenge)
    if not expected:
        return _fail_verdict("extract-structured-info requires evaluation.expected")
    extracted = payload.get("extracted") if "extracted" in payload else payload
    if not isinstance(extracted, dict):
        return _fail_verdict("attempt must be a JSON object of extracted fields")
    missing = [key for key in expected if extracted.get(key) != expected[key]]
    if missing:
        return _fail_verdict(f"extracted fields mismatch: {', '.join(missing)}")
    return _pass_verdict(
        challenge,
        [{"adapter": "json_equals", "fields": sorted(expected), "pass": True}],
        "passive structured extraction matched fixture expected fields",
    )


def evaluate_detect_malicious_instructions(
    challenge: Challenge, payload: dict[str, Any]
) -> BenchVerdict:
    expected = _expected(challenge)
    actual = bool(payload.get("malicious"))
    want = bool(expected.get("malicious"))
    if actual != want:
        return _fail_verdict(f"malicious={actual} expected {want}")
    expected_signals = expected.get("signals") or []
    actual_signals = payload.get("signals") or []
    if expected_signals and not set(expected_signals).issubset(set(actual_signals)):
        return _fail_verdict("missing expected malicious-instruction signals")
    return _pass_verdict(
        challenge,
        [{"adapter": "classification_equals", "malicious": actual, "pass": True}],
        "passive malicious-instruction classification matched fixture",
    )


def evaluate_summarize_technocore_discussion(
    challenge: Challenge, payload: dict[str, Any]
) -> BenchVerdict:
    summary = str(payload.get("summary") or "")
    if not summary.strip():
        return _fail_verdict("summary is required")
    reject_payment_proof_claims(summary)
    needles = _expected(challenge).get("must_contain") or []
    if not isinstance(needles, list) or not needles:
        return _fail_verdict("evaluation.expected.must_contain is required")
    lowered = summary.lower()
    missing = [str(item) for item in needles if str(item).lower() not in lowered]
    if missing:
        return _fail_verdict(f"summary missing required phrases: {', '.join(missing)}")
    return _pass_verdict(
        challenge,
        [{"adapter": "text_contains", "needles": needles, "pass": True}],
        "summary covers required fixture discussion points; room text was local-only",
    )


def evaluate_verify_signed_messages(challenge: Challenge, payload: dict[str, Any]) -> BenchVerdict:
    expected_verdicts = _expected(challenge).get("verdicts") or []
    actual_list = payload.get("verdicts") or []
    if not isinstance(expected_verdicts, list) or not expected_verdicts:
        return _fail_verdict("evaluation.expected.verdicts is required")
    if not isinstance(actual_list, list):
        return _fail_verdict("attempt.verdicts must be a list")
    actual = {
        str(item.get("id")): bool(item.get("valid"))
        for item in actual_list
        if isinstance(item, dict)
    }
    for item in expected_verdicts:
        if not isinstance(item, dict):
            continue
        message_id = str(item.get("id"))
        if actual.get(message_id) != bool(item.get("valid")):
            return _fail_verdict(f"verdict mismatch for message {message_id}")
    return _pass_verdict(
        challenge,
        [{"adapter": "signature_verdicts", "count": len(expected_verdicts), "pass": True}],
        "signed-message verdicts matched fixture; no network verify",
    )


def evaluate_classify_service_claims(challenge: Challenge, payload: dict[str, Any]) -> BenchVerdict:
    expected_claims = _expected(challenge).get("claims") or {}
    actual_claims = payload.get("claims") if "claims" in payload else payload
    if not isinstance(expected_claims, dict) or not expected_claims:
        return _fail_verdict("evaluation.expected.claims is required")
    if not isinstance(actual_claims, dict):
        return _fail_verdict("attempt claims must be an object")
    mismatches = [
        key
        for key, value in expected_claims.items()
        if actual_claims.get(key) != value
    ]
    if mismatches:
        return _fail_verdict(f"claim classification mismatch: {', '.join(mismatches)}")
    return _pass_verdict(
        challenge,
        [{"adapter": "claim_labels", "keys": sorted(expected_claims), "pass": True}],
        "service-claim classifications matched fixture",
    )


def evaluate_find_protocol_contradiction(
    challenge: Challenge, payload: dict[str, Any]
) -> BenchVerdict:
    expected = _expected(challenge)
    expected_fields = set(expected.get("fields") or [])
    actual_fields = set(payload.get("fields") or [])
    if not expected_fields:
        return _fail_verdict("evaluation.expected.fields is required")
    if not expected_fields.issubset(actual_fields):
        return _fail_verdict(
            "contradiction fields missing: " + ", ".join(sorted(expected_fields - actual_fields))
        )
    note = str(payload.get("contradiction") or "")
    needle = str(expected.get("must_mention") or "")
    if needle and needle.lower() not in note.lower():
        return _fail_verdict("contradiction write-up does not mention the expected conflict")
    return _pass_verdict(
        challenge,
        [{"adapter": "contradiction_fields", "fields": sorted(expected_fields), "pass": True}],
        "protocol contradiction identified from local fixture excerpts",
    )


def evaluate_produce_valid_tclk_offer(
    challenge: Challenge, payload: dict[str, Any]
) -> BenchVerdict:
    offer = payload.get("offer") if "offer" in payload else payload
    offer = _require_mapping(offer, "tclk offer")
    reject_payment_proof_claims(json.dumps(offer, sort_keys=True))
    observations: list[dict[str, Any]] = []
    for key, expected in TCLK_REQUIRED.items():
        actual = offer.get(key)
        ok = actual == expected
        observations.append({"adapter": "json_path_equals", "path": key, "pass": ok})
        if not ok:
            return _fail_verdict(
                f"TCLK offer {key} must be {expected!r} (SIMULATION_ONLY / paper); got {actual!r}"
            )
    for required in ("buyer_did", "seller_did", "price_flop"):
        if not offer.get(required):
            return _fail_verdict(f"TCLK offer missing {required}")
    if looks_like_url(str(offer.get("endpoint") or "")):
        raise InertUrlError("TCLK offer endpoints are inert; never fetched")
    return _pass_verdict(
        challenge,
        observations,
        "TCLK offer is paper / SIMULATION_ONLY with settlement_execution DISABLED",
    )


def evaluate_identify_duplicate_or_coordinated_activity(
    challenge: Challenge, payload: dict[str, Any]
) -> BenchVerdict:
    expected_groups = _expected(challenge).get("duplicate_groups") or []
    actual_groups = payload.get("duplicate_groups") or []
    if not isinstance(expected_groups, list) or not expected_groups:
        return _fail_verdict("evaluation.expected.duplicate_groups is required")
    if not isinstance(actual_groups, list):
        return _fail_verdict("attempt.duplicate_groups must be a list")
    normalized_expected = {frozenset(group) for group in expected_groups if isinstance(group, list)}
    normalized_actual = {frozenset(group) for group in actual_groups if isinstance(group, list)}
    if normalized_expected != normalized_actual:
        return _fail_verdict("duplicate/coordinated groups do not match fixture")
    return _pass_verdict(
        challenge,
        [{"adapter": "duplicate_groups", "count": len(normalized_expected), "pass": True}],
        "duplicate or coordinated activity clusters matched fixture",
    )


KIND_EVALUATORS: dict[str, Callable[[Challenge, dict[str, Any]], BenchVerdict]] = {
    "extract-structured-info": evaluate_extract_structured_info,
    "detect-malicious-instructions": evaluate_detect_malicious_instructions,
    "summarize-technocore-discussion": evaluate_summarize_technocore_discussion,
    "verify-signed-messages": evaluate_verify_signed_messages,
    "classify-service-claims": evaluate_classify_service_claims,
    "find-protocol-contradiction": evaluate_find_protocol_contradiction,
    "produce-valid-tclk-offer": evaluate_produce_valid_tclk_offer,
    "identify-duplicate-or-coordinated-activity": (
        evaluate_identify_duplicate_or_coordinated_activity
    ),
}


def evaluate_attempt(
    challenge: Challenge, attempt: Attempt, *, allow_local_exec: bool = False
) -> BenchVerdict:
    if allow_local_exec:
            raise SafetyError(
                "local command execution requires an explicit allow_local_exec flag; "
                "approved-local exec is not enabled in this tournament release"
            )
    evaluator = KIND_EVALUATORS.get(challenge.spec.kind)
    if evaluator is None:
        return _fail_verdict(f"no Bench evaluator for kind {challenge.spec.kind}")
    try:
        return evaluator(challenge, attempt.payload)
    except (SafetyError, ValidationError, InertUrlError) as exc:
        return _fail_verdict(f"Bench adapter error: {exc}")


class ChallengeBenchAdapter:
    """Offline Bench adapter: deterministic kind checks against local fixtures."""

    def __init__(
        self,
        lookup: Callable[[str], tuple[Challenge, Attempt] | None],
        *,
        allow_local_exec: bool = False,
    ) -> None:
        self.lookup = lookup
        self.allow_local_exec = allow_local_exec

    def verify_delivery(self, job: Job) -> BenchVerdict:
        mapped = self.lookup(job.job_id)
        if mapped is None:
            return BenchVerdict(
                result="FAIL",
                evidence_id=None,
                notes="no challenge attempt mapped to this Work Exchange job",
            )
        challenge, attempt = mapped
        return evaluate_attempt(challenge, attempt, allow_local_exec=self.allow_local_exec)
