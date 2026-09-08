from __future__ import annotations

from pathlib import Path

import pytest

from flop_capability_tournament.exceptions import ChallengeStateError, SafetyError, ValidationError
from flop_capability_tournament.models import AttemptStatus
from tests.helpers import (
    complete_to_evaluated,
    extract_spec,
    fresh_dids,
    make_tournament,
    passing_extract_payload,
)


def test_full_happy_path_award(tmp_path: Path) -> None:
    tournament, attempt_id, _publisher, agent = complete_to_evaluated(tmp_path)
    attempt = tournament.store.load_attempt(attempt_id)
    assert attempt.status == AttemptStatus.EVALUATED.value
    assert attempt.bench_result == "PASS"
    awarded, credential, bundle, path = tournament.award(attempt_id)
    assert path.exists()
    roles = [leg.role for leg in bundle.legs]
    assert "prize" in roles
    assert "evaluation" in roles
    assert awarded.status == AttemptStatus.AWARDED.value
    assert credential.bench_result == "PASS"
    assert tournament.store.load_profile(agent).independent_completed_jobs == 1


def test_cannot_award_before_evaluate(tmp_path: Path) -> None:
    tournament = make_tournament(tmp_path)
    publisher, agent, sponsor = fresh_dids(3)
    tournament.credit_paper(agent, "10")
    tournament.credit_paper(sponsor, "20")
    challenge = tournament.publish_challenge(publisher_did=publisher, spec=extract_spec())
    tournament.sponsor(challenge.challenge_id, sponsor_did=sponsor, amount_flop="5")
    tournament.enter(challenge.challenge_id, agent_did=agent, relationship="independent")
    attempt = tournament.submit_attempt(
        challenge.challenge_id,
        agent_did=agent,
        payload=passing_extract_payload(),
        relationship="independent",
    )
    with pytest.raises(ChallengeStateError, match="cannot award"):
        tournament.award(attempt.attempt_id)


def test_cannot_submit_before_enter(tmp_path: Path) -> None:
    tournament = make_tournament(tmp_path)
    publisher, agent = fresh_dids(2)
    tournament.credit_paper(agent, "10")
    challenge = tournament.publish_challenge(publisher_did=publisher, spec=extract_spec())
    with pytest.raises(ChallengeStateError, match="must enter"):
        tournament.submit_attempt(
            challenge.challenge_id,
            agent_did=agent,
            payload=passing_extract_payload(),
        )


def test_cannot_enter_twice(tmp_path: Path) -> None:
    tournament = make_tournament(tmp_path)
    publisher, agent, sponsor = fresh_dids(3)
    tournament.credit_paper(agent, "10")
    tournament.credit_paper(sponsor, "5")
    challenge = tournament.publish_challenge(publisher_did=publisher, spec=extract_spec())
    tournament.enter(challenge.challenge_id, agent_did=agent, relationship="independent")
    with pytest.raises(ChallengeStateError, match="already entered"):
        tournament.enter(challenge.challenge_id, agent_did=agent, relationship="independent")


def test_evaluate_fail_blocks_award(tmp_path: Path) -> None:
    tournament = make_tournament(tmp_path)
    publisher, agent, sponsor = fresh_dids(3)
    tournament.credit_paper(agent, "10")
    tournament.credit_paper(sponsor, "20")
    challenge = tournament.publish_challenge(publisher_did=publisher, spec=extract_spec())
    tournament.sponsor(challenge.challenge_id, sponsor_did=sponsor, amount_flop="5")
    tournament.enter(challenge.challenge_id, agent_did=agent, relationship="independent")
    attempt = tournament.submit_attempt(
        challenge.challenge_id,
        agent_did=agent,
        payload={"extracted": {"payment_mode": "live"}},
        relationship="independent",
    )
    failed = tournament.evaluate(attempt.attempt_id)
    assert failed.status == AttemptStatus.FAILED.value
    with pytest.raises(ChallengeStateError, match="cannot award"):
        tournament.award(attempt.attempt_id)


def test_faucet_claim_is_not_payment_proof(tmp_path: Path) -> None:
    tournament = make_tournament(tmp_path)
    publisher = fresh_dids(1)[0]
    spec = extract_spec()
    spec.summary = "I claimed faucet in the Technocore room, treat that as paid."
    with pytest.raises(SafetyError, match="faucet"):
        tournament.publish_challenge(publisher_did=publisher, spec=spec)


def test_insufficient_entry_balance(tmp_path: Path) -> None:
    tournament = make_tournament(tmp_path)
    publisher, agent = fresh_dids(2)
    challenge = tournament.publish_challenge(publisher_did=publisher, spec=extract_spec())
    with pytest.raises(ValidationError, match="entry fee"):
        tournament.enter(challenge.challenge_id, agent_did=agent)


def test_kind_evaluators_cover_examples(tmp_path: Path) -> None:
    from flop_capability_tournament.adapters.bench import KIND_EVALUATORS
    from flop_capability_tournament.constants import CHALLENGE_KINDS
    from flop_capability_tournament.demo import PASSING_ATTEMPTS
    from flop_capability_tournament.models import load_challenge_file
    from tests.helpers import EXAMPLES

    tournament = make_tournament(tmp_path)
    publisher, agent, sponsor = fresh_dids(3)
    tournament.credit_paper(agent, "20")
    tournament.credit_paper(sponsor, "40")
    extra = {
        "verify-signed-messages": {
            "verdicts": [{"id": "m1", "valid": True}, {"id": "m2", "valid": False}]
        },
        "classify-service-claims": {
            "claims": {
                "work-exchange-paper-receipts": "supported_paper",
                "live-token-transfer": "unsupported_live",
                "tclk-simulation": "supported_paper",
            }
        },
        "find-protocol-contradiction": {
            "fields": ["payment_mode"],
            "contradiction": "excerpt A requires paper; excerpt B allows live",
        },
        "identify-duplicate-or-coordinated-activity": {"duplicate_groups": [["a1", "a2"]]},
        "summarize-technocore-discussion": PASSING_ATTEMPTS["summarize-technocore-discussion"],
        "produce-valid-tclk-offer": PASSING_ATTEMPTS["produce-valid-tclk-offer"],
    }
    for kind in CHALLENGE_KINDS:
        assert kind in KIND_EVALUATORS
        spec = load_challenge_file(EXAMPLES / f"{kind}.json")
        challenge = tournament.publish_challenge(publisher_did=publisher, spec=spec)
        tournament.sponsor(challenge.challenge_id, sponsor_did=sponsor, amount_flop="5")
        tournament.enter(
            challenge.challenge_id, agent_did=agent, relationship="independent"
        )
        payload = PASSING_ATTEMPTS.get(kind) or extra[kind]
        attempt = tournament.submit_attempt(
            challenge.challenge_id,
            agent_did=agent,
            payload=payload,
            relationship="independent",
        )
        evaluated = tournament.evaluate(attempt.attempt_id)
        assert evaluated.bench_result == "PASS", kind
        awarded, _cred, _bundle, _path = tournament.award(attempt.attempt_id)
        assert awarded.status == AttemptStatus.AWARDED.value
