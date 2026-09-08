from __future__ import annotations

from pathlib import Path
from typing import Any

from flop_work_exchange.receipts import verify_receipt

from flop_capability_tournament.config import PolicyConfig, TournamentConfig
from flop_capability_tournament.credentials import router_capability_claim, verify_credential
from flop_capability_tournament.identity import create_ephemeral_party, ensure_test_identity
from flop_capability_tournament.models import ChallengeSpec, load_challenge_file
from flop_capability_tournament.tournament import CapabilityTournament

EXAMPLES = Path(__file__).resolve().parents[2] / "examples" / "challenges"

DEMO_KINDS = (
    "extract-structured-info.json",
    "detect-malicious-instructions.json",
)

PASSING_ATTEMPTS: dict[str, dict[str, Any]] = {
    "extract-structured-info": {
        "extracted": {
            "payment_mode": "paper",
            "tclk_mode": "SIMULATION_ONLY",
            "settlement_execution": "DISABLED",
            "operator_relationship": "same_operator",
        }
    },
    "detect-malicious-instructions": {
        "malicious": True,
        "signals": ["prompt_injection"],
    },
    "summarize-technocore-discussion": {
        "summary": (
            "Local fixture discussion of paper settlement. payment_mode is paper, "
            "operator_relationship is recorded, and settlement_execution is DISABLED."
        )
    },
    "produce-valid-tclk-offer": {
        "payment_mode": "paper",
        "tclk_mode": "SIMULATION_ONLY",
        "settlement_execution": "DISABLED",
        "buyer_did": "did:key:z6MkpGs1L6fYEsaXsDfyDfrTxbKVeZ3evuPaBj2x38KzupPd",
        "seller_did": "did:key:z6MkqqqEMxujBTEAvoanSx6pVBMMZzLP7gMUcmNVdYHS3BVk",
        "price_flop": "4",
        "protocol": "tclk/1",
    },
}


def _spec(name: str) -> ChallengeSpec:
    return load_challenge_file(EXAMPLES / name)


def _run_kind(
    tournament: CapabilityTournament,
    *,
    publisher_did: str,
    agent_did: str,
    sponsor_did: str,
    spec: ChallengeSpec,
    payload: dict[str, Any],
) -> dict[str, Any]:
    challenge = tournament.publish_challenge(publisher_did=publisher_did, spec=spec)
    tournament.sponsor(challenge.challenge_id, sponsor_did=sponsor_did, amount_flop="5")
    entry, entry_bundle, _entry_path = tournament.enter(
        challenge.challenge_id,
        agent_did=agent_did,
        relationship="independent",
    )
    attempt = tournament.submit_attempt(
        challenge.challenge_id,
        agent_did=agent_did,
        payload=payload,
        relationship="independent",
    )
    attempt = tournament.evaluate(attempt.attempt_id)
    awarded, credential, prize_bundle, bundle_path = tournament.award(attempt.attempt_id)
    cred_payload = credential.to_dict()
    cred_verify = verify_credential(cred_payload)
    receipt_verifications = []
    for bundle in (entry_bundle, prize_bundle):
        for leg in bundle.legs:
            receipt_verifications.append(
                {
                    "role": leg.role,
                    "bundle": bundle.bundle_id,
                    "verification": verify_receipt(leg.receipt),
                }
            )
    return {
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
        "entry_receipt_roles": [leg.role for leg in entry_bundle.legs],
        "award_receipt_roles": [leg.role for leg in prize_bundle.legs],
        "receipt_verifications": receipt_verifications,
        "bundle_path": str(bundle_path),
        "sentinel_status": awarded.sentinel_status,
        "operator_relationship": awarded.operator_relationship,
        "independent_reputation_eligible": awarded.independent_reputation_eligible,
    }


def run_demo(state_dir: Path) -> dict[str, Any]:
    """Run two challenge kinds with PASS, prize receipts, and verifiable credentials."""
    config = TournamentConfig(
        state_dir=state_dir,
        payment_mode="paper",
        settlement_backend="paper",
        policy=PolicyConfig(treat_unknown_as_independent=False, allow_same_operator_deals=True),
    )
    tournament = CapabilityTournament(config)
    ensure_test_identity(state_dir)
    _pub_key, publisher_did = create_ephemeral_party("publisher")
    _agent_key, agent_did = create_ephemeral_party("agent")
    _sponsor_key, sponsor_did = create_ephemeral_party("sponsor")
    tournament.credit_paper(agent_did, "10", "demo-agent-seed")
    tournament.credit_paper(sponsor_did, "20", "demo-sponsor-seed")

    runs = []
    for filename in DEMO_KINDS:
        spec = _spec(filename)
        payload = PASSING_ATTEMPTS[spec.kind]
        runs.append(
            _run_kind(
                tournament,
                publisher_did=publisher_did,
                agent_did=agent_did,
                sponsor_did=sponsor_did,
                spec=spec,
                payload=payload,
            )
        )

    all_receipts_ok = all(
        item["verification"]["ok"] is True
        for run in runs
        for item in run["receipt_verifications"]
    )
    all_creds_ok = all(run["credential_verification"]["ok"] is True for run in runs)
    all_pass = all(run["bench_result"] == "PASS" and run["status"] == "AWARDED" for run in runs)
    return {
        "ok": bool(all_receipts_ok and all_creds_ok and all_pass and len(runs) >= 2),
        "state_dir": str(state_dir),
        "tournament_did": tournament.tournament_did(),
        "exchange_did": tournament.exchange.exchange_did(),
        "publisher_did": publisher_did,
        "agent_did": agent_did,
        "sponsor_did": sponsor_did,
        "kinds": [run["kind_id"] for run in runs],
        "runs": runs,
        "agent_public_claims": tournament.store.load_profile(agent_did).public_claims(),
        "balances": tournament.balances(),
        "payment_mode": "paper",
        "settlement_status": "simulated",
        "settlement_execution": "DISABLED",
        "urls_are_inert": True,
        "tclk_mode": "SIMULATION_ONLY",
    }
