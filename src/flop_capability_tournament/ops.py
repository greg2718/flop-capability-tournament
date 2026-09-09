"""Ops helpers: doctor checks and the paper live-demo path."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from flop_work_exchange.receipts import verify_receipt

from flop_capability_tournament.adapters.bench import StubBenchAdapter
from flop_capability_tournament.adapters.factory import resolve_adapters
from flop_capability_tournament.adapters.router import StubRouterAdapter
from flop_capability_tournament.adapters.scout import StubScoutAdapter, candidates_payload
from flop_capability_tournament.adapters.sentinel import StubSentinelAdapter
from flop_capability_tournament.config import AdapterConfig, PolicyConfig, TournamentConfig
from flop_capability_tournament.constants import (
    BENCH_DID,
    BENCH_STATE,
    DEFAULT_PRODUCTION_STATE,
    DEFAULT_SCOUT_CANDIDATE_LIMIT,
    KNOWN_FAMILY_DIDS,
    LEGACY_SCOUT_STATE,
    MAC_BENCH_REPO,
    MAC_ROUTER_REPO,
    MAC_SCOUT_REPO,
    MAC_SENTINEL_REPO,
    ROUTER_DID,
    ROUTER_STATE,
    SCOUT_DID,
    SCOUT_STATE,
    SENTINEL_STATE,
    sibling_state_dirs,
)
from flop_capability_tournament.credentials import router_capability_claim, verify_credential
from flop_capability_tournament.demo import PASSING_ATTEMPTS
from flop_capability_tournament.exceptions import AdapterError, IsolationError, ValidationError
from flop_capability_tournament.identity import (
    TEST_IDENTITY_JSON,
    create_ephemeral_party,
    ensure_test_identity,
    load_identity_meta,
)
from flop_capability_tournament.models import load_challenge_file
from flop_capability_tournament.tournament import CapabilityTournament


def doctor(
    *,
    state_dir: Path | None = None,
    adapter_config: AdapterConfig | None = None,
    config_path: Path | None = None,
) -> dict[str, Any]:
    adapters = adapter_config or AdapterConfig()
    bundle = resolve_adapters(adapters)
    checks: list[dict[str, Any]] = [
        {
            "name": "payment_mode",
            "ok": True,
            "value": "paper",
            "note": "live FLOP rails are not available; faucet/wallet/transfer are out of scope",
        },
        {
            "name": "tclk_mode",
            "ok": True,
            "value": "SIMULATION_ONLY",
        },
        {
            "name": "settlement_execution",
            "ok": True,
            "value": "DISABLED",
        },
        {
            "name": "bench_allow_local_exec",
            "ok": True,
            "value": adapters.bench_allow_local_exec,
            "note": "default false; never pass --allow-local-exec unless explicitly enabled",
        },
        {
            "name": "no_self_validation",
            "ok": True,
            "value": {
                "allow_self_deals": False,
                "family_dids_independent_judges": False,
                "family_dids_independent_jurors": False,
            },
            "note": (
                "Same-operator Scout/Bench/Router/Sentinel/Work Exchange/Foundry/Tournament "
                "DIDs cannot be independent judges, jurors, or peers. Self-deals are disabled."
            ),
            "family_dids": {
                "scout": SCOUT_DID,
                "bench": BENCH_DID,
                "router": ROUTER_DID,
            },
        },
        _adapter_check("scout", adapters.scout_mode, bundle.scout.probe()),
        _adapter_check("bench", adapters.bench_mode, bundle.bench.probe()),
        _adapter_check("router", adapters.router_mode, bundle.router.probe()),
        _adapter_check("sentinel", adapters.sentinel_mode, bundle.sentinel.probe()),
        {
            "name": "mac_dev_paths",
            "ok": True,
            "value": {
                "scout": str(MAC_SCOUT_REPO),
                "bench": str(MAC_BENCH_REPO),
                "router": str(MAC_ROUTER_REPO),
                "sentinel": str(MAC_SENTINEL_REPO),
            },
            "exists": {
                "scout": MAC_SCOUT_REPO.exists(),
                "bench": MAC_BENCH_REPO.exists(),
                "router": MAC_ROUTER_REPO.exists(),
                "sentinel": MAC_SENTINEL_REPO.exists(),
            },
        },
        {
            "name": "sibling_state_isolation_rules",
            "ok": True,
            "forbidden": [str(path) for path in sibling_state_dirs()],
            "production_state": str(DEFAULT_PRODUCTION_STATE),
            "note": (
                "Capability Tournament must not use Scout/Bench/Router/Sentinel/"
                "Work Exchange/Foundry state dirs. Live Bench verify uses a temp --state-dir."
            ),
        },
    ]
    if state_dir is not None:
        checks.append(_isolation_check(state_dir))
        checks.append(_identity_check(state_dir))
        checks.append(_overlap_live_backends(state_dir))
    ok = all(bool(check.get("ok", True)) for check in checks)
    return {
        "ok": ok,
        "payment_mode": "paper",
        "tclk_mode": "SIMULATION_ONLY",
        "settlement_execution": "DISABLED",
        "not_live": [
            "faucet",
            "wallet",
            "token transfer",
            "Technocore payment endpoints",
            "settlement_execution",
        ],
        "adapter_modes": {
            "scout": adapters.scout_mode,
            "bench": adapters.bench_mode,
            "router": adapters.router_mode,
            "sentinel": adapters.sentinel_mode,
        },
        "config_path": str(config_path) if config_path is not None else None,
        "scout_candidate_limit": adapters.scout_candidate_limit,
        "scout_projection_db": (
            str(adapters.scout_projection_db) if adapters.scout_projection_db else None
        ),
        "scout_evidence_jsonl": (
            str(adapters.scout_evidence_jsonl) if adapters.scout_evidence_jsonl else None
        ),
        "scout_sqlite_timeout_seconds": adapters.scout_sqlite_timeout_seconds,
        "scout_max_db_bytes": adapters.scout_max_db_bytes,
        "router_db": str(adapters.router_db) if adapters.router_db else None,
        "router_fixture": str(adapters.router_fixture) if adapters.router_fixture else None,
        "checks": checks,
    }


def run_live_demo(
    state_dir: Path,
    *,
    adapter_config: AdapterConfig | None = None,
    config_path: Path | None = None,
) -> dict[str, Any]:
    """Run one paper challenge, preferring local adapters that actually probe OK.

    Uses ephemeral publisher/agent/sponsor DIDs — never family Scout/Bench/Router
    identities as competing agents, independent jurors, or independent judges.
    Self-deals are not synthesized. Settlement stays paper / DISABLED.
    """
    requested = adapter_config or AdapterConfig()
    notes: list[str] = []
    mid_run_errors: list[str] = []
    bundle = resolve_adapters(requested)
    scout, notes = _prefer_local(
        "scout", requested.scout_mode, bundle.scout, StubScoutAdapter(), notes
    )
    bench, notes = _prefer_local(
        "bench", requested.bench_mode, bundle.bench, StubBenchAdapter(), notes
    )
    sentinel, notes = _prefer_local(
        "sentinel", requested.sentinel_mode, bundle.sentinel, StubSentinelAdapter(), notes
    )
    router, notes = _prefer_local(
        "router", requested.router_mode, bundle.router, StubRouterAdapter(), notes
    )
    config = TournamentConfig(
        state_dir=state_dir,
        payment_mode="paper",
        settlement_backend="paper",
        policy=PolicyConfig(treat_unknown_as_independent=False, allow_same_operator_deals=True),
        adapters=requested,
        allow_local_exec=requested.bench_allow_local_exec,
    )
    tournament = CapabilityTournament(
        config,
        scout=scout,
        router=router,
        sentinel=sentinel,
        bench=bench,
    )
    ensure_test_identity(state_dir)
    _pub_key, publisher_did = create_ephemeral_party("publisher")
    _agent_key, agent_did = create_ephemeral_party("agent")
    _sponsor_key, sponsor_did = create_ephemeral_party("sponsor")
    if publisher_did == agent_did:
        raise AdapterError("live-demo refused to synthesize a self-deal")
    if any(did in KNOWN_FAMILY_DIDS for did in (publisher_did, agent_did, sponsor_did)):
        raise AdapterError("live-demo refused family DIDs as independent counterparties")
    tournament.credit_paper(agent_did, "10", "demo-agent-seed")
    tournament.credit_paper(sponsor_did, "20", "demo-sponsor-seed")

    examples = Path(__file__).resolve().parents[2] / "examples" / "challenges"
    spec = load_challenge_file(examples / "extract-structured-info.json")
    payload = PASSING_ATTEMPTS[spec.kind]
    try:
        challenge = tournament.publish_challenge(publisher_did=publisher_did, spec=spec)
    except AdapterError as exc:
        if getattr(tournament.sentinel, "kind", "") == "local":
            mid_run_errors.append(f"sentinel: {exc}")
        notes.append(f"sentinel: local screen failed ({exc}); falling back to stub")
        tournament.sentinel = StubSentinelAdapter()
        challenge = tournament.publish_challenge(publisher_did=publisher_did, spec=spec)

    catalog_job_id = challenge.catalog_job_id
    if not catalog_job_id:
        raise AdapterError("published challenge is missing catalog_job_id")
    candidates: list[Any] = []
    try:
        candidates = list(tournament.exchange.find_candidates(catalog_job_id))
    except AdapterError as exc:
        if getattr(tournament.scout, "kind", "") == "local":
            mid_run_errors.append(f"scout: {exc}")
        notes.append(f"scout: local find_candidates failed ({exc}); falling back to stub")
        tournament.scout = StubScoutAdapter()
        candidates = list(tournament.exchange.find_candidates(catalog_job_id))
    family_candidates = [item.did for item in candidates if item.did in KNOWN_FAMILY_DIDS]
    if family_candidates:
        notes.append(
            "scout: family DIDs in candidates are not independent jurors or judges "
            f"({len(family_candidates)} family hits omitted from judging)"
        )

    tournament.sponsor(challenge.challenge_id, sponsor_did=sponsor_did, amount_flop="5")
    try:
        entry, entry_bundle, _entry_path = tournament.enter(
            challenge.challenge_id,
            agent_did=agent_did,
            relationship="independent",
        )
    except AdapterError as exc:
        if getattr(tournament.router, "kind", "") == "local":
            mid_run_errors.append(f"router: {exc}")
        notes.append(f"router: local plan failed ({exc}); falling back to stub router")
        tournament.router = StubRouterAdapter()
        entry, entry_bundle, _entry_path = tournament.enter(
            challenge.challenge_id,
            agent_did=agent_did,
            relationship="independent",
        )
    try:
        attempt = tournament.submit_attempt(
            challenge.challenge_id,
            agent_did=agent_did,
            payload=payload,
            relationship="independent",
        )
    except AdapterError as exc:
        if getattr(tournament.sentinel, "kind", "") == "local":
            mid_run_errors.append(f"sentinel: {exc}")
        notes.append(f"sentinel: local attempt screen failed ({exc}); falling back to stub")
        tournament.sentinel = StubSentinelAdapter()
        if getattr(tournament.router, "kind", "") == "local":
            notes.append("router: falling back to stub after attempt-path adapter error")
            tournament.router = StubRouterAdapter()
        attempt = tournament.submit_attempt(
            challenge.challenge_id,
            agent_did=agent_did,
            payload=payload,
            relationship="independent",
        )
    try:
        attempt = tournament.evaluate(attempt.attempt_id)
    except AdapterError as exc:
        if getattr(tournament.bench, "kind", "") == "local":
            mid_run_errors.append(f"bench: {exc}")
        notes.append(f"bench: local verify failed ({exc}); falling back to stub")
        tournament.bench = StubBenchAdapter()
        attempt = tournament.evaluate(attempt.attempt_id)

    awarded, credential, prize_bundle, bundle_path = tournament.award(attempt.attempt_id)
    cred_payload = credential.to_dict()
    cred_verify = verify_credential(cred_payload)
    receipt_verifications: list[dict[str, Any]] = []
    for bundle_row in (entry_bundle, prize_bundle):
        for leg in bundle_row.legs:
            receipt_verifications.append(
                {
                    "role": leg.role,
                    "bundle": bundle_row.bundle_id,
                    "verification": dict(verify_receipt(leg.receipt)),
                }
            )
    limit = requested.scout_candidate_limit or DEFAULT_SCOUT_CANDIDATE_LIMIT
    candidate_dids = [item.did for item in candidates[:limit]]
    receipts_ok = all(
        bool(item["verification"].get("ok")) is True for item in receipt_verifications
    )
    ok = (
        bool(cred_verify.get("ok"))
        and receipts_ok
        and awarded.bench_result == "PASS"
        and awarded.status == "AWARDED"
        and not mid_run_errors
        and publisher_did != agent_did
    )
    return {
        "ok": ok,
        "state_dir": str(state_dir),
        "config_path": str(config_path) if config_path is not None else None,
        "tournament_did": tournament.tournament_did(),
        "exchange_did": tournament.exchange.exchange_did(),
        "publisher_did": publisher_did,
        "agent_did": agent_did,
        "sponsor_did": sponsor_did,
        "kind_id": spec.kind_id,
        "challenge_id": challenge.challenge_id,
        "entry_id": entry.entry_id,
        "attempt_id": awarded.attempt_id,
        "bench_result": awarded.bench_result,
        "status": awarded.status,
        "credential_id": credential.credential_id,
        "credential": cred_payload,
        "credential_verification": cred_verify,
        "router_claim": router_capability_claim(cred_payload),
        "receipt_verifications": receipt_verifications,
        "bundle_path": str(bundle_path),
        "sentinel_status": awarded.sentinel_status,
        "operator_relationship": awarded.operator_relationship,
        "judge_relationship": awarded.judge_relationship,
        "independent_reputation_eligible": awarded.independent_reputation_eligible,
        "wash_risk": awarded.wash_risk,
        "self_deal": False,
        "family_used_as_independent_agent": False,
        "candidates_from_scout": candidate_dids,
        "candidates_shown": len(candidate_dids),
        "candidates_limit": limit,
        "candidates_payload": candidates_payload(list(candidates[:limit]), limit=limit),
        "agent_public_claims": tournament.store.load_profile(agent_did).public_claims(),
        "balances": tournament.balances(),
        "payment_mode": "paper",
        "settlement_status": "simulated",
        "settlement_execution": "DISABLED",
        "tclk_mode": "SIMULATION_ONLY",
        "urls_are_inert": True,
        "adapter_kinds": {
            "scout": getattr(tournament.scout, "kind", "unknown"),
            "bench": getattr(tournament.bench, "kind", "unknown"),
            "router": getattr(tournament.router, "kind", "unknown"),
            "sentinel": getattr(tournament.sentinel, "kind", "unknown"),
        },
        "adapter_notes": notes,
        "adapter_errors": mid_run_errors,
        "not_live": ["faucet", "wallet", "token transfer", "settlement_execution"],
        "verification": cred_verify,
    }


def _prefer_local(
    name: str,
    mode: str,
    local_or_stub: Any,
    stub: Any,
    notes: list[str],
) -> tuple[Any, list[str]]:
    if mode != "local":
        notes.append(f"{name}: stub (default offline)")
        return stub, notes
    probe = local_or_stub.probe()
    if probe.get("ok"):
        notes.append(f"{name}: local ({probe.get('note') or 'probe ok'})")
        return local_or_stub, notes
    notes.append(
        f"{name}: local requested but unavailable ({probe.get('error')}); falling back to stub"
    )
    return stub, notes


def _adapter_check(name: str, mode: str, probe: dict[str, Any]) -> dict[str, Any]:
    ok = True if mode == "stub" else bool(probe.get("ok"))
    return {
        "name": f"adapter_{name}",
        "ok": ok,
        "mode": mode,
        "kind": probe.get("kind"),
        "probe": probe,
        "note": (
            "local mode fails closed when the sibling backend is missing; "
            "stub success is never labeled live"
            if mode == "local" and not probe.get("ok")
            else probe.get("note") or probe.get("error")
        ),
    }


def _isolation_check(state_dir: Path) -> dict[str, Any]:
    from flop_capability_tournament.constants import assert_isolated_state_dir

    try:
        resolved = assert_isolated_state_dir(state_dir)
    except IsolationError as exc:
        return {"name": "state_isolation", "ok": False, "error": str(exc)}
    return {"name": "state_isolation", "ok": True, "state_dir": str(resolved)}


def _identity_check(state_dir: Path) -> dict[str, Any]:
    try:
        meta = load_identity_meta(state_dir)
    except (ValidationError, IsolationError) as exc:
        exists = (state_dir.expanduser() / TEST_IDENTITY_JSON).exists()
        return {
            "name": "identity",
            "ok": True,
            "present": exists,
            "note": str(exc) if not exists else str(exc),
        }
    return {
        "name": "identity",
        "ok": True,
        "present": True,
        "did": meta.get("did"),
        "purpose": meta.get("purpose"),
        "persistent": meta.get("persistent"),
        "note": "public metadata only; private key is not read",
    }


def _overlap_live_backends(state_dir: Path) -> dict[str, Any]:
    forbidden = {
        "scout": SCOUT_STATE,
        "legacy_scout": LEGACY_SCOUT_STATE,
        "bench": BENCH_STATE,
        "router": ROUTER_STATE,
        "sentinel": SENTINEL_STATE,
    }
    resolved = state_dir.expanduser().resolve(strict=False)
    overlaps = {
        name: str(path)
        for name, path in forbidden.items()
        if resolved == path.expanduser().resolve(strict=False)
    }
    return {
        "name": "no_sibling_state_overlap",
        "ok": not overlaps,
        "overlaps": overlaps,
    }
