"""Bench-shaped deterministic evaluators over local challenge fixtures.

Real Bench is https://github.com/greg2718/flop-bench. This stub never fetches
URLs or rooms and never runs local commands. Challenge kinds map onto passive
JSON / text checks so evaluation stays reproducible in tests and demos.
"""

from __future__ import annotations

import json
import re
import tempfile
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from flop_work_exchange.canonical import result_hash_for, sha256_json
from flop_work_exchange.models import BenchVerdict, Job
from flop_work_exchange.policy import reject_payment_proof_claims

from flop_capability_tournament.adapters.process import CommandResult, run_argv
from flop_capability_tournament.exceptions import (
    AdapterError,
    InertUrlError,
    SafetyError,
    ValidationError,
)
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

    kind = "stub"

    def __init__(
        self,
        lookup: Callable[[str], tuple[Challenge, Attempt] | None],
        *,
        allow_local_exec: bool = False,
    ) -> None:
        self.lookup = lookup
        self.allow_local_exec = allow_local_exec

    def probe(self) -> dict[str, Any]:
        return {
            "ok": True,
            "kind": self.kind,
            "note": (
                "offline kind checks on local fixtures; no flop-bench process; "
                "not independent Bench judging or reputation"
            ),
            "allow_local_exec": self.allow_local_exec,
        }

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


CommandRunner = Callable[..., CommandResult]
_SHA256_HEX = re.compile(r"^[0-9a-f]{64}$")


class StubBenchAdapter:
    """Offline Bench adapter.

    Real Bench verifies test specs into hash-chained evidence bundles via
    ``flop-bench verify --state-dir ...`` and keeps local exec behind
    ``--allow-local-exec``. This stub never executes local commands. It checks
    that a submitted result hash matches the result payload.
    """

    kind = "stub"

    def probe(self) -> dict[str, Any]:
        return {
            "ok": True,
            "kind": self.kind,
            "note": "offline hash check; no flop-bench process; not independent Bench reputation",
        }

    def verify_delivery(self, job: Job) -> BenchVerdict:
        if not job.result_text or not job.result_hash:
            return BenchVerdict(
                result="FAIL",
                evidence_id=None,
                notes="missing result_text or result_hash",
            )
        expected = result_hash_for(job.result_text)
        if job.result_hash != expected:
            return BenchVerdict(
                result="FAIL",
                evidence_id=None,
                notes="result_hash does not match sha256(result_text)",
            )
        evidence_id = (
            "ev-"
            + sha256_json(
                {
                    "job_id": job.job_id,
                    "result_hash": job.result_hash,
                    "adapter": "stub-bench",
                }
            )[:32]
        )
        return BenchVerdict(
            result="PASS",
            evidence_id=evidence_id,
            notes="stub hash check only; no local exec; not independent Bench reputation",
            local_exec=False,
        )


class LocalBenchAdapter:
    """Invoke ``flop-bench verify`` on a generated passive spec.

    Always uses an isolated temporary ``--state-dir`` (never
    ``~/.flop_agents/bench``). ``--allow-local-exec`` is omitted unless
    ``allow_local_exec=True`` (default false). Same-operator Bench results are
    not independent reputation.
    """

    kind = "local"

    def __init__(
        self,
        *,
        argv: Sequence[str] | None = None,
        allow_local_exec: bool = False,
        timeout_seconds: float = 60.0,
        run_command: CommandRunner | None = None,
    ) -> None:
        self.argv = [str(part) for part in argv] if argv else None
        self.allow_local_exec = allow_local_exec
        self.timeout_seconds = timeout_seconds
        self._run_command = run_command or run_argv

    def probe(self) -> dict[str, Any]:
        try:
            argv = self._resolved_argv()
        except AdapterError as exc:
            return {"ok": False, "kind": self.kind, "error": str(exc), "allow_local_exec": False}
        return {
            "ok": True,
            "kind": self.kind,
            "argv": argv,
            "allow_local_exec": self.allow_local_exec,
            "note": "passive flop-bench verify; temp --state-dir; not independent reputation",
        }

    def verify_delivery(self, job: Job) -> BenchVerdict:
        if not job.result_text or not job.result_hash:
            return BenchVerdict(
                result="FAIL",
                evidence_id=None,
                notes="missing result_text or result_hash",
            )
        digest = _hex_digest(job.result_hash)
        argv_prefix = self._resolved_argv()
        with tempfile.TemporaryDirectory(prefix="flop-ct-bench-") as raw_tmp:
            tmp = Path(raw_tmp)
            artifact = tmp / "result.txt"
            spec_path = tmp / "spec.json"
            bench_state = tmp / "bench-state"
            artifact.write_text(job.result_text, encoding="utf-8")
            spec_path.write_text(
                json.dumps(passive_delivery_spec(job, artifact, digest), indent=2, sort_keys=True)
                + "\n",
                encoding="utf-8",
            )
            command = [
                *argv_prefix,
                "verify",
                str(spec_path),
                "--state-dir",
                str(bench_state),
            ]
            if self.allow_local_exec:
                command.append("--allow-local-exec")
            result = self._run_command(command, timeout_seconds=self.timeout_seconds)
            return _verdict_from_bench_output(result)

    def _resolved_argv(self) -> list[str]:
        if self.argv:
            return list(self.argv)
        raise AdapterError(
            "LocalBenchAdapter: flop-bench CLI missing. Set FLOP_CT_BENCH_CLI or "
            "FLOP_CT_BENCH_REPO (expected `flop-bench verify --state-dir ...`)."
        )


def passive_delivery_spec(job: Job, artifact: Path, sha256_hex: str) -> dict[str, Any]:
    """Minimal flop-bench.test-spec.v0.1 passive spec for a job result artifact."""
    return {
        "schema_version": "flop-bench.test-spec.v0.1",
        "claim_id": job.job_id,
        "hypothesis": (
            f"Capability Tournament job {job.job_id} result artifact matches the recorded sha256."
        ),
        "requested_capabilities": [job.service or "tournament-delivery"],
        "mode": "passive",
        "procedure": [
            {"adapter": "file_exists", "path": str(artifact)},
            {"adapter": "file_sha256", "path": str(artifact), "sha256": sha256_hex},
        ],
        "assertions": [{"expect": "result artifact exists and matches result_hash"}],
        "failure_conditions": [
            "result artifact missing",
            "result artifact sha256 does not match job.result_hash",
        ],
        "provenance": {
            "source": "flop-capability-tournament",
            "job_id": job.job_id,
            "note": (
                "same-operator Bench verification is not independent peer "
                "reputation, independent judging, or independent fee volume"
            ),
        },
    }


def _hex_digest(result_hash: str) -> str:
    value = result_hash.removeprefix("sha256:")
    if not _SHA256_HEX.fullmatch(value):
        raise AdapterError("result_hash is not a 64-hex sha256 digest")
    return value


def _verdict_from_bench_output(result: CommandResult) -> BenchVerdict:
    payload = _parse_json_object(result.stdout) if result.stdout.strip() else None
    if payload is None:
        raise AdapterError(
            "flop-bench verify did not return JSON "
            f"(exit {result.returncode}): {_brief(result.stderr or result.stdout)}"
        )
    raw_result = str(payload.get("result") or "")
    if raw_result not in {"PASS", "FAIL", "PARTIAL"}:
        raise AdapterError(f"flop-bench verify returned unknown result: {raw_result!r}")
    evidence_id = payload.get("evidence_id")
    safety = payload.get("safety_report") if isinstance(payload.get("safety_report"), dict) else {}
    local_exec = bool(safety.get("local_execution")) if isinstance(safety, dict) else False
    notes = (
        "flop-bench verify; not independent Bench reputation; "
        f"local_exec={local_exec}; allow_local_exec_flag="
        f"{'--allow-local-exec' in result.argv}"
    )
    if result.returncode != 0 and raw_result == "PASS":
        raise AdapterError(
            f"flop-bench verify exit {result.returncode} but JSON result=PASS; failing closed"
        )
    return BenchVerdict(
        result=raw_result,  # type: ignore[arg-type]
        evidence_id=str(evidence_id) if evidence_id else None,
        notes=notes,
        local_exec=local_exec,
    )


def _parse_json_object(text: str) -> dict[str, Any] | None:
    try:
        loaded = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end <= start:
            return None
        try:
            loaded = json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            return None
    return loaded if isinstance(loaded, dict) else None


def _brief(text: str, limit: int = 240) -> str:
    compact = " ".join(text.split())
    if len(compact) <= limit:
        return compact
    return compact[:limit] + "…"
