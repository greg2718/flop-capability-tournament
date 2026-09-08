from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from flop_work_exchange.adapters.settlement import TestnetSettlement
from flop_work_exchange.amounts import micro_to_flop_string, parse_flop_to_micro

from flop_capability_tournament.config import FeeSchedule, config_from_mapping, load_config
from flop_capability_tournament.constants import BENCH_FEE_ACCOUNT, TOURNAMENT_FEE_ACCOUNT
from flop_capability_tournament.exceptions import NotLiveError, ValidationError
from tests.helpers import (
    complete_to_evaluated,
    extract_spec,
    fresh_dids,
    make_tournament,
)


def test_parse_flop_exact_micro_no_float() -> None:
    assert parse_flop_to_micro("12") == 12_000_000
    assert parse_flop_to_micro("12.5") == 12_500_000
    assert micro_to_flop_string(12_500_000) == "12.5"
    with pytest.raises(ValidationError):
        parse_flop_to_micro("12.1234567")


def test_testnet_settlement_raises() -> None:
    rail = TestnetSettlement()
    with pytest.raises(NotLiveError, match="not live"):
        rail.credit("did:key:z6Mk", 1, "nope")


def test_tournament_testnet_backend_cannot_credit(tmp_path: Path) -> None:
    tournament = make_tournament(tmp_path, backend="testnet")
    with pytest.raises(NotLiveError):
        tournament.credit_paper("account", "1")


def test_non_paper_payment_mode_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match="payment_mode"):
        config_from_mapping(tmp_path, {"payment_mode": "live", "fees": {}})


def test_yaml_fee_config(tmp_path: Path) -> None:
    yaml_path = tmp_path / "fees.yaml"
    yaml_path.write_text(
        yaml.safe_dump(
            {
                "payment_mode": "paper",
                "fees": {
                    "management_micro": 1,
                    "evaluation_micro": 2,
                    "default_entry_micro": 3,
                    "default_prize_micro": 4,
                },
            }
        ),
        encoding="utf-8",
    )
    loaded = load_config(tmp_path, yaml_path)
    assert loaded.fees.management_micro == 1
    assert loaded.payment_mode == "paper"


def test_entry_and_prize_split(tmp_path: Path) -> None:
    fees = FeeSchedule(
        management_micro=100_000,
        evaluation_micro=50_000,
        default_entry_micro=1_000_000,
        default_prize_micro=4_000_000,
    )
    tournament = make_tournament(tmp_path, fees=fees)
    attempt_id = complete_to_evaluated(tmp_path, tournament=tournament)[1]
    attempt = tournament.store.load_attempt(attempt_id)
    agent = attempt.agent_did
    before_prize = tournament.exchange.paper_balance(agent)
    _awarded, _cred, bundle, _path = tournament.award(attempt_id)
    roles = {leg.role: leg.amount_micro for leg in bundle.legs}
    assert roles["prize"] == 4_000_000
    assert roles["evaluation"] == 50_000
    assert tournament.exchange.paper_balance(agent) == before_prize + 4_000_000
    assert tournament.exchange.paper_balance(TOURNAMENT_FEE_ACCOUNT) == 100_000
    assert tournament.exchange.paper_balance(BENCH_FEE_ACCOUNT) == 50_000


def test_prize_pool_must_cover_award(tmp_path: Path) -> None:
    tournament = make_tournament(tmp_path)
    publisher, agent = fresh_dids(2)
    tournament.credit_paper(agent, "10")
    challenge = tournament.publish_challenge(publisher_did=publisher, spec=extract_spec())
    tournament.enter(challenge.challenge_id, agent_did=agent, relationship="independent")
    from tests.helpers import passing_extract_payload

    attempt = tournament.submit_attempt(
        challenge.challenge_id,
        agent_did=agent,
        payload=passing_extract_payload(),
        relationship="independent",
    )
    tournament.evaluate(attempt.attempt_id)
    with pytest.raises(ValidationError, match="prize pool"):
        tournament.award(attempt.attempt_id)
