from __future__ import annotations

from pathlib import Path
from typing import Any

from flop_capability_tournament.config import FeeSchedule, PolicyConfig, TournamentConfig
from flop_capability_tournament.identity import create_ephemeral_party, ensure_test_identity
from flop_capability_tournament.models import ChallengeSpec
from flop_capability_tournament.tournament import CapabilityTournament

EXAMPLES = Path(__file__).resolve().parents[1] / "examples" / "challenges"


def make_tournament(
    tmp_path: Path,
    *,
    fees: FeeSchedule | None = None,
    policy: PolicyConfig | None = None,
    backend: str = "paper",
) -> CapabilityTournament:
    config = TournamentConfig(
        state_dir=tmp_path,
        settlement_backend=backend,  # type: ignore[arg-type]
        fees=fees or FeeSchedule(),
        policy=policy or PolicyConfig(),
    )
    tournament = CapabilityTournament(config)
    ensure_test_identity(tmp_path)
    return tournament


def fresh_dids(count: int = 2) -> tuple[str, ...]:
    dids: list[str] = []
    for label in ("a", "b", "c", "d", "e")[:count]:
        _key, did = create_ephemeral_party(label)
        dids.append(did)
    return tuple(dids)


def extract_spec() -> ChallengeSpec:
    return ChallengeSpec.from_dict(
        {
            "title": "Extract settlement fields",
            "kind": "extract-structured-info",
            "version": "1.0.0",
            "summary": "Extract paper settlement fields from a local fixture.",
            "entry_fee_flop": "1",
            "prize_flop": "4",
            "fixture": {"text": "payment_mode is paper. TCLK is SIMULATION_ONLY."},
            "evaluation": {
                "expected": {
                    "payment_mode": "paper",
                    "tclk_mode": "SIMULATION_ONLY",
                    "settlement_execution": "DISABLED",
                    "operator_relationship": "same_operator",
                }
            },
        }
    )


def passing_extract_payload() -> dict[str, Any]:
    return {
        "extracted": {
            "payment_mode": "paper",
            "tclk_mode": "SIMULATION_ONLY",
            "settlement_execution": "DISABLED",
            "operator_relationship": "same_operator",
        }
    }


def complete_to_evaluated(
    tmp_path: Path,
    *,
    relationship: str = "independent",
    tournament: CapabilityTournament | None = None,
    publisher: str | None = None,
    agent: str | None = None,
    sponsor_amount: str = "5",
) -> tuple[CapabilityTournament, str, str, str]:
    tournament = tournament or make_tournament(tmp_path)
    pub, ag, sponsor = fresh_dids(3)
    publisher = publisher or pub
    agent = agent or ag
    tournament.credit_paper(agent, "10")
    tournament.credit_paper(sponsor, "20")
    challenge = tournament.publish_challenge(publisher_did=publisher, spec=extract_spec())
    tournament.sponsor(challenge.challenge_id, sponsor_did=sponsor, amount_flop=sponsor_amount)
    tournament.enter(
        challenge.challenge_id,
        agent_did=agent,
        relationship=relationship,
    )
    attempt = tournament.submit_attempt(
        challenge.challenge_id,
        agent_did=agent,
        payload=passing_extract_payload(),
        relationship=relationship,
    )
    tournament.evaluate(attempt.attempt_id)
    return tournament, attempt.attempt_id, publisher, agent
