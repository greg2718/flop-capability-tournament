from __future__ import annotations

from pathlib import Path

from flop_work_exchange.constants import (
    BENCH_DID,
    KNOWN_FAMILY_AGENTS,
    KNOWN_FAMILY_DIDS,
    ROUTER_DID,
    SCOUT_DID,
    SENTINEL_DID,
)
from flop_work_exchange.exceptions import IsolationError, SafetyError

DEFAULT_PRODUCTION_STATE = Path.home() / ".flop_agents" / "capability-tournament"
TOURNAMENT_OPERATOR_GROUP = "local-flop-agent-family"

SCOUT_STATE = Path.home() / ".flop_agents" / "scout"
BENCH_STATE = Path.home() / ".flop_agents" / "bench"
ROUTER_STATE = Path.home() / ".flop_agents" / "router"
SENTINEL_STATE = Path.home() / ".flop_agents" / "sentinel"
WORK_EXCHANGE_STATE = Path.home() / ".flop_agents" / "work-exchange"
FOUNDRY_STATE = Path.home() / ".flop_agents" / "code-bounty-foundry"
LEGACY_SCOUT_STATE = Path.home() / ".flop_scout"

TOURNAMENT_FEE_ACCOUNT = "tournament-management-fees"
BENCH_FEE_ACCOUNT = "bench-evaluation-fees"

CHALLENGE_KINDS: tuple[str, ...] = (
    "extract-structured-info",
    "detect-malicious-instructions",
    "summarize-technocore-discussion",
    "verify-signed-messages",
    "classify-service-claims",
    "find-protocol-contradiction",
    "produce-valid-tclk-offer",
    "identify-duplicate-or-coordinated-activity",
)


def _path_overlaps(left: Path, right: Path) -> bool:
    resolved_left = left.expanduser().resolve(strict=False)
    resolved_right = right.expanduser().resolve(strict=False)
    if resolved_left == resolved_right:
        return True
    try:
        return resolved_left.is_relative_to(resolved_right) or resolved_right.is_relative_to(
            resolved_left
        )
    except ValueError:
        return False


def sibling_state_dirs() -> tuple[Path, ...]:
    return (
        SCOUT_STATE,
        BENCH_STATE,
        ROUTER_STATE,
        SENTINEL_STATE,
        WORK_EXCHANGE_STATE,
        FOUNDRY_STATE,
        LEGACY_SCOUT_STATE,
    )


def assert_isolated_state_dir(state_dir: Path) -> Path:
    resolved = state_dir.expanduser().resolve(strict=False)
    if state_dir.expanduser().is_symlink() or resolved.is_symlink():
        raise IsolationError("state directory must not be a symlink")
    for sibling in sibling_state_dirs():
        if _path_overlaps(resolved, sibling):
            raise IsolationError(f"state directory overlaps sibling agent state: {sibling}")
    return resolved


def assert_not_production_auto_init(state_dir: Path) -> None:
    resolved = state_dir.expanduser().resolve(strict=False)
    expected = DEFAULT_PRODUCTION_STATE.expanduser().resolve(strict=False)
    if resolved == expected:
        raise SafetyError(
            "refusing to auto-create identity in the production state directory; "
            "run identity init with explicit confirmation"
        )


def prize_pool_account(challenge_id: str) -> str:
    return f"prize-pool:{challenge_id}"


__all__ = [
    "BENCH_DID",
    "BENCH_FEE_ACCOUNT",
    "BENCH_STATE",
    "CHALLENGE_KINDS",
    "DEFAULT_PRODUCTION_STATE",
    "FOUNDRY_STATE",
    "KNOWN_FAMILY_AGENTS",
    "KNOWN_FAMILY_DIDS",
    "ROUTER_DID",
    "ROUTER_STATE",
    "SCOUT_DID",
    "SCOUT_STATE",
    "SENTINEL_DID",
    "SENTINEL_STATE",
    "TOURNAMENT_FEE_ACCOUNT",
    "TOURNAMENT_OPERATOR_GROUP",
    "WORK_EXCHANGE_STATE",
    "assert_isolated_state_dir",
    "assert_not_production_auto_init",
    "prize_pool_account",
    "sibling_state_dirs",
]
