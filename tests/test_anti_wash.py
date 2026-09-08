from __future__ import annotations

from pathlib import Path

import pytest
from flop_work_exchange.policy import classify_operator_relationship

from flop_capability_tournament.constants import BENCH_DID, ROUTER_DID, SCOUT_DID
from flop_capability_tournament.exceptions import PolicyError, SafetyError
from tests.helpers import extract_spec, fresh_dids, make_tournament, passing_extract_payload


def test_family_dids_are_same_operator() -> None:
    assert classify_operator_relationship(SCOUT_DID, BENCH_DID) == "same_operator"
    assert classify_operator_relationship(SCOUT_DID, ROUTER_DID) == "same_operator"


def test_family_plus_outsider_is_related() -> None:
    outsider = fresh_dids(1)[0]
    assert classify_operator_relationship(SCOUT_DID, outsider) == "related"


def test_same_operator_deal_does_not_mint_independent_reputation(tmp_path: Path) -> None:
    tournament = make_tournament(tmp_path)
    sponsor = fresh_dids(1)[0]
    tournament.credit_paper(SCOUT_DID, "10")
    tournament.credit_paper(sponsor, "20")
    challenge = tournament.publish_challenge(publisher_did=BENCH_DID, spec=extract_spec())
    tournament.sponsor(challenge.challenge_id, sponsor_did=sponsor, amount_flop="5")
    tournament.enter(challenge.challenge_id, agent_did=SCOUT_DID)
    stored_entry = tournament.store.find_entry(challenge.challenge_id, SCOUT_DID)
    assert stored_entry is not None
    attempt = tournament.submit_attempt(
        challenge.challenge_id,
        agent_did=SCOUT_DID,
        payload=passing_extract_payload(),
    )
    assert attempt.operator_relationship == "same_operator"
    assert attempt.independent_reputation_eligible is False
    assert attempt.judge_relationship == "same_operator"
    tournament.evaluate(attempt.attempt_id)
    tournament.award(attempt.attempt_id)
    seller = tournament.store.load_profile(SCOUT_DID)
    assert seller.independent_completed_jobs == 0
    assert seller.independent_fee_volume_micro == 0
    assert seller.same_operator_completed_jobs == 1
    claims = seller.public_claims()
    assert claims["independent_completed_jobs"] == 0
    assert claims["independent_fee_volume_micro"] == 0


def test_cannot_relabel_family_as_independent(tmp_path: Path) -> None:
    tournament = make_tournament(tmp_path)
    sponsor = fresh_dids(1)[0]
    tournament.credit_paper(SCOUT_DID, "10")
    tournament.credit_paper(sponsor, "5")
    challenge = tournament.publish_challenge(publisher_did=BENCH_DID, spec=extract_spec())
    tournament.enter(challenge.challenge_id, agent_did=SCOUT_DID)
    with pytest.raises(SafetyError, match="same-operator"):
        tournament.submit_attempt(
            challenge.challenge_id,
            agent_did=SCOUT_DID,
            payload=passing_extract_payload(),
            relationship="independent",
        )


def test_self_deal_blocked_by_default(tmp_path: Path) -> None:
    tournament = make_tournament(tmp_path)
    publisher = fresh_dids(1)[0]
    tournament.credit_paper(publisher, "10")
    challenge = tournament.publish_challenge(publisher_did=publisher, spec=extract_spec())
    with pytest.raises(PolicyError, match="self-deals"):
        tournament.enter(challenge.challenge_id, agent_did=publisher)


def test_family_cannot_be_independent_judge(tmp_path: Path) -> None:
    tournament = make_tournament(tmp_path)
    sponsor = fresh_dids(1)[0]
    tournament.credit_paper(SCOUT_DID, "10")
    tournament.credit_paper(sponsor, "20")
    challenge = tournament.publish_challenge(publisher_did=BENCH_DID, spec=extract_spec())
    tournament.sponsor(challenge.challenge_id, sponsor_did=sponsor, amount_flop="5")
    tournament.enter(challenge.challenge_id, agent_did=SCOUT_DID)
    attempt = tournament.submit_attempt(
        challenge.challenge_id,
        agent_did=SCOUT_DID,
        payload=passing_extract_payload(),
    )
    evaluated = tournament.evaluate(attempt.attempt_id)
    assert evaluated.judge_relationship == "same_operator"
    assert evaluated.independent_reputation_eligible is False
    awarded, credential, _bundle, _path = tournament.award(attempt.attempt_id)
    assert credential.independent_reputation_eligible is False
    assert credential.judge_relationship == "same_operator"
    assert awarded.independent_reputation_eligible is False


def test_circular_counterparties_flag_wash_risk(tmp_path: Path) -> None:
    tournament = make_tournament(tmp_path)
    a, b, sponsor = fresh_dids(3)
    tournament.credit_paper(a, "20")
    tournament.credit_paper(b, "20")
    tournament.credit_paper(sponsor, "40")

    challenge1 = tournament.publish_challenge(publisher_did=a, spec=extract_spec())
    tournament.sponsor(challenge1.challenge_id, sponsor_did=sponsor, amount_flop="5")
    tournament.enter(challenge1.challenge_id, agent_did=b, relationship="independent")
    attempt1 = tournament.submit_attempt(
        challenge1.challenge_id,
        agent_did=b,
        payload=passing_extract_payload(),
        relationship="independent",
    )
    tournament.evaluate(attempt1.attempt_id)
    tournament.award(attempt1.attempt_id)

    challenge2 = tournament.publish_challenge(publisher_did=b, spec=extract_spec())
    tournament.sponsor(challenge2.challenge_id, sponsor_did=sponsor, amount_flop="5")
    tournament.enter(challenge2.challenge_id, agent_did=a, relationship="independent")
    attempt2 = tournament.submit_attempt(
        challenge2.challenge_id,
        agent_did=a,
        payload=passing_extract_payload(),
        relationship="independent",
    )
    stored = tournament.store.load_attempt(attempt2.attempt_id)
    assert stored.wash_risk is True
    assert stored.independent_reputation_eligible is False
    tournament.evaluate(attempt2.attempt_id)
    tournament.award(attempt2.attempt_id)
    profile_a = tournament.store.load_profile(a)
    profile_b = tournament.store.load_profile(b)
    assert profile_a.wash_flags >= 1
    assert profile_a.independent_completed_jobs == 0
    assert profile_b.independent_completed_jobs == 1
