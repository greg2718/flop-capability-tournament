from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import yaml
from flop_work_exchange.amounts import parse_micro
from flop_work_exchange.canonical import atomic_write_json
from flop_work_exchange.config import PolicyConfig as PolicyConfig

from flop_capability_tournament.constants import (
    KNOWN_FAMILY_DIDS,
    TOURNAMENT_OPERATOR_GROUP,
    assert_isolated_state_dir,
)
from flop_capability_tournament.exceptions import ValidationError

PaymentMode = Literal["paper"]

DEFAULT_FEES = {
    "management_micro": 100_000,
    "evaluation_micro": 50_000,
    "default_entry_micro": 1_000_000,
    "default_prize_micro": 4_000_000,
}


def _package_data_dir() -> Path:
    return Path(__file__).resolve().parent / "data"


def default_fee_config_path() -> Path:
    return _package_data_dir() / "fees.json"


@dataclass(frozen=True)
class FeeSchedule:
    management_micro: int = 100_000
    evaluation_micro: int = 50_000
    default_entry_micro: int = 1_000_000
    default_prize_micro: int = 4_000_000

    def prize_pool_contribution_micro(self, entry_fee_micro: int) -> int:
        if entry_fee_micro < self.management_micro:
            raise ValidationError("entry fee cannot be smaller than the management fee")
        return entry_fee_micro - self.management_micro

    def award_reserve_micro(self, prize_micro: int) -> int:
        return prize_micro + self.evaluation_micro


@dataclass(frozen=True)
class TournamentConfig:
    state_dir: Path
    payment_mode: PaymentMode = "paper"
    settlement_backend: Literal["paper", "testnet"] = "paper"
    settlement_execution: Literal["DISABLED"] = "DISABLED"
    tclk_mode: Literal["SIMULATION_ONLY"] = "SIMULATION_ONLY"
    operator_group: str = TOURNAMENT_OPERATOR_GROUP
    known_family_dids: frozenset[str] = field(default_factory=lambda: KNOWN_FAMILY_DIDS)
    fees: FeeSchedule = field(default_factory=FeeSchedule)
    policy: PolicyConfig = field(default_factory=PolicyConfig)
    asset: str = "FLOP"
    micro_per_flop: int = 1_000_000
    allow_local_exec: bool = False

    def resolved_state_dir(self) -> Path:
        return assert_isolated_state_dir(self.state_dir)

    def exchange_state_dir(self) -> Path:
        return self.resolved_state_dir() / "work-exchange"


def _require_mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValidationError(f"{label} must be an object")
    return value


def _fee_schedule_from_mapping(raw: dict[str, Any]) -> FeeSchedule:
    fees = _require_mapping(raw.get("fees", {}), "fees") if "fees" in raw else dict(DEFAULT_FEES)
    merged = {**DEFAULT_FEES, **fees}
    return FeeSchedule(
        management_micro=parse_micro(merged["management_micro"]),
        evaluation_micro=parse_micro(merged["evaluation_micro"]),
        default_entry_micro=parse_micro(merged["default_entry_micro"]),
        default_prize_micro=parse_micro(merged["default_prize_micro"]),
    )


def _policy_from_mapping(raw: dict[str, Any]) -> PolicyConfig:
    if "policy" not in raw:
        return PolicyConfig()
    policy = _require_mapping(raw.get("policy"), "policy")
    return PolicyConfig(
        allow_same_operator_deals=bool(policy.get("allow_same_operator_deals", True)),
        same_operator_independent_reputation=bool(
            policy.get("same_operator_independent_reputation", False)
        ),
        same_operator_fee_volume_as_independent=bool(
            policy.get("same_operator_fee_volume_as_independent", False)
        ),
        allow_self_deals=bool(policy.get("allow_self_deals", False)),
        treat_unknown_as_independent=bool(policy.get("treat_unknown_as_independent", False)),
    )


def load_mapping(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    suffix = path.suffix.lower()
    if suffix == ".json":
        loaded = json.loads(text)
    elif suffix in {".yaml", ".yml"}:
        loaded = yaml.safe_load(text)
    elif suffix == ".toml":
        import tomllib

        loaded = tomllib.loads(text)
    else:
        raise ValidationError(f"unsupported config format: {suffix}")
    return _require_mapping(loaded, "config")


def config_from_mapping(state_dir: Path, raw: dict[str, Any]) -> TournamentConfig:
    payment_mode = raw.get("payment_mode", "paper")
    if payment_mode != "paper":
        raise ValidationError('payment_mode must be "paper"; live rails are not available')
    backend = raw.get("settlement_backend", "paper")
    if backend not in {"paper", "testnet"}:
        raise ValidationError('settlement_backend must be "paper" or "testnet"')
    family = raw.get("known_family_dids")
    known = KNOWN_FAMILY_DIDS
    if family is not None:
        if not isinstance(family, list) or not all(isinstance(item, str) for item in family):
            raise ValidationError("known_family_dids must be a list of DID strings")
        known = frozenset(family)
    return TournamentConfig(
        state_dir=state_dir,
        payment_mode="paper",
        settlement_backend="testnet" if backend == "testnet" else "paper",
        fees=_fee_schedule_from_mapping(raw),
        policy=_policy_from_mapping(raw),
        known_family_dids=known,
        operator_group=str(raw.get("operator_group", TOURNAMENT_OPERATOR_GROUP)),
        asset=str(raw.get("asset", "FLOP")),
        allow_local_exec=bool(raw.get("allow_local_exec", False)),
    )


def load_config(state_dir: Path, config_path: Path | None = None) -> TournamentConfig:
    path = config_path or default_fee_config_path()
    return config_from_mapping(state_dir, load_mapping(path))


def write_resolved_config(state_dir: Path, config: TournamentConfig) -> None:
    payload = {
        "payment_mode": config.payment_mode,
        "settlement_backend": config.settlement_backend,
        "settlement_execution": config.settlement_execution,
        "tclk_mode": config.tclk_mode,
        "operator_group": config.operator_group,
        "asset": config.asset,
        "micro_per_flop": config.micro_per_flop,
        "allow_local_exec": config.allow_local_exec,
        "known_family_dids": sorted(config.known_family_dids),
        "fees": {
            "management_micro": config.fees.management_micro,
            "evaluation_micro": config.fees.evaluation_micro,
            "default_entry_micro": config.fees.default_entry_micro,
            "default_prize_micro": config.fees.default_prize_micro,
        },
        "policy": {
            "allow_same_operator_deals": config.policy.allow_same_operator_deals,
            "same_operator_independent_reputation": (
                config.policy.same_operator_independent_reputation
            ),
            "same_operator_fee_volume_as_independent": (
                config.policy.same_operator_fee_volume_as_independent
            ),
            "allow_self_deals": config.policy.allow_self_deals,
            "treat_unknown_as_independent": config.policy.treat_unknown_as_independent,
        },
    }
    atomic_write_json(state_dir / "config.json", payload)
