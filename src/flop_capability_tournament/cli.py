from __future__ import annotations

import argparse
import json
import sys
import tempfile
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from flop_work_exchange.receipts import verify_receipt

from flop_capability_tournament import __version__
from flop_capability_tournament.config import AdapterConfig, load_adapter_config, load_config
from flop_capability_tournament.constants import DEFAULT_PRODUCTION_STATE, MAX_CLI_JSON_CHARS
from flop_capability_tournament.credentials import router_capability_claim, verify_credential
from flop_capability_tournament.demo import run_demo
from flop_capability_tournament.exceptions import WorkExchangeError
from flop_capability_tournament.identity import (
    create_production_identity,
    create_test_identity,
    load_identity_meta,
)
from flop_capability_tournament.models import load_challenge_file
from flop_capability_tournament.ops import doctor, run_live_demo
from flop_capability_tournament.tournament import CapabilityTournament


def _print_json(value: Any, *, max_chars: int | None = None) -> None:
    text = json.dumps(value, indent=2, sort_keys=True, default=str)
    if max_chars is not None and len(text) > max_chars:
        text = text[:max_chars].rstrip() + "\n... [truncated]"
    print(text)


def _adapter_config_from_args(args: argparse.Namespace) -> AdapterConfig:
    config_path = Path(args.config) if getattr(args, "config", None) else None
    return load_adapter_config(config_path)


def _require_state_dir(args: argparse.Namespace) -> Path:
    if not getattr(args, "state_dir", None):
        raise SystemExit("--state-dir is required (demos/tests should pass a temp dir)")
    return Path(args.state_dir)


def _open(args: argparse.Namespace) -> CapabilityTournament:
    state_dir = _require_state_dir(args)
    config_path = Path(args.config) if getattr(args, "config", None) else None
    return CapabilityTournament(load_config(state_dir, config_path))


def _load_json_file(path: Path) -> dict[str, Any]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise SystemExit("attempt file must contain a JSON object")
    return raw


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="flop-capability-tournament",
        description=(
            "Continuous paper-FLOP market for proving what FLOP agents can do. "
            "Settlement execution is DISABLED. URLs and room fixtures are inert."
        ),
    )
    parser.add_argument(
        "--version", action="version", version=f"flop-capability-tournament {__version__}"
    )
    parser.add_argument(
        "--state-dir",
        type=Path,
        help=(
            "Local state directory. Required for write commands. Production path is "
            f"{DEFAULT_PRODUCTION_STATE}; demos and tests must pass a temp dir."
        ),
    )
    parser.add_argument("--config", type=Path, help="Fee/policy config (JSON, YAML, or TOML)")
    sub = parser.add_subparsers(dest="cmd", required=True)

    identity = sub.add_parser("identity", help="Tournament Ed25519 / did:key identity")
    identity_sub = identity.add_subparsers(dest="identity_cmd", required=True)
    identity_sub.add_parser("init", help="Create a test-only identity in --state-dir")
    prod = identity_sub.add_parser("init-production", help="Gated production identity (encrypted)")
    prod.add_argument("--confirm", required=True)
    identity_sub.add_parser("show", help="Show public identity metadata")

    publish = sub.add_parser("publish-challenge", help="Bench publishes a versioned challenge")
    publish.add_argument("--publisher-did", required=True)
    publish.add_argument("--spec", type=Path, required=True, help="Challenge spec JSON or YAML")

    sub.add_parser("list-challenges", help="List local challenges")

    enter = sub.add_parser("enter", help="Pay a paper entry fee for a challenge")
    enter.add_argument("--challenge-id", required=True)
    enter.add_argument("--agent-did", required=True)
    enter.add_argument(
        "--operator-relationship",
        choices=["independent", "same_operator", "related", "unknown"],
        default=None,
    )

    sponsor = sub.add_parser("sponsor", help="Fund a challenge prize pool (paper)")
    sponsor.add_argument("--challenge-id", required=True)
    sponsor.add_argument("--sponsor-did", required=True)
    sponsor.add_argument("--amount-flop", required=True)

    submit = sub.add_parser("submit-attempt", help="Submit a local attempt JSON (never a URL)")
    submit.add_argument("--challenge-id", required=True)
    submit.add_argument("--agent-did", required=True)
    submit.add_argument("--attempt-file", type=Path, required=True)
    submit.add_argument(
        "--operator-relationship",
        choices=["independent", "same_operator", "related", "unknown"],
        default=None,
    )

    evaluate = sub.add_parser("evaluate", help="Sentinel-screened Bench evaluation")
    evaluate.add_argument("--attempt-id", required=True)

    award = sub.add_parser("award", help="Pay paper prize and mint EvidenceCredential on PASS")
    award.add_argument("--attempt-id", required=True)

    show_cred = sub.add_parser("show-credential", help="Show and verify an EvidenceCredential")
    show_cred.add_argument("--credential-id", default=None)
    show_cred.add_argument("--attempt-id", default=None)

    show = sub.add_parser("show", help="Show a challenge with entries and attempts")
    show.add_argument("--challenge-id", required=True)

    verify_file = sub.add_parser("verify-receipt", help="Verify a receipt JSON file")
    verify_file.add_argument("receipt", type=Path)

    verify_cred = sub.add_parser("verify-credential", help="Verify an EvidenceCredential JSON file")
    verify_cred.add_argument("credential", type=Path)

    credit = sub.add_parser("paper-credit", help="Seed a paper ledger account (simulation only)")
    credit.add_argument("--account", required=True)
    credit.add_argument("--amount-flop", required=True)
    credit.add_argument("--reason", default="paper-seed")

    sub.add_parser("balances", help="Show paper ledger balances")

    demo = sub.add_parser("demo", help="Run two challenge kinds end-to-end on paper")
    demo.add_argument(
        "--state-dir",
        dest="demo_state_dir",
        type=Path,
        default=None,
        help="Optional temp/state dir; created if omitted",
    )
    live_demo = sub.add_parser(
        "live-demo",
        help="Paper challenge preferring local Scout/Bench/Router/Sentinel adapters",
    )
    live_demo.add_argument(
        "--state-dir",
        dest="demo_state_dir",
        type=Path,
        default=None,
        help="Optional temp/state dir; created if omitted",
    )
    doc = sub.add_parser(
        "doctor",
        help="Check adapter modes, paths, identity, and state isolation",
    )
    doc.add_argument(
        "--state-dir",
        dest="doctor_state_dir",
        type=Path,
        default=None,
        help="Optional state dir to inspect (not created)",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    try:
        return _dispatch(args)
    except WorkExchangeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


def _dispatch(args: argparse.Namespace) -> int:
    if args.cmd == "demo":
        state_dir = args.demo_state_dir or args.state_dir
        if state_dir is None:
            state_dir = Path(tempfile.mkdtemp(prefix="flop-capability-tournament-demo-"))
        result = run_demo(Path(state_dir))
        _print_json(result)
        return 0 if result.get("ok") else 2

    if args.cmd == "live-demo":
        state_dir = args.demo_state_dir or args.state_dir
        if state_dir is None:
            state_dir = Path(tempfile.mkdtemp(prefix="flop-capability-tournament-live-demo-"))
        result = run_live_demo(
            Path(state_dir),
            adapter_config=_adapter_config_from_args(args),
            config_path=Path(args.config) if getattr(args, "config", None) else None,
        )
        _print_json(result, max_chars=MAX_CLI_JSON_CHARS)
        ok = bool(result.get("ok")) and bool(result.get("verification", {}).get("ok"))
        return 0 if ok else 2

    if args.cmd == "doctor":
        state_dir = args.doctor_state_dir or args.state_dir
        config_path = Path(args.config) if getattr(args, "config", None) else None
        report = doctor(
            state_dir=Path(state_dir) if state_dir is not None else None,
            adapter_config=_adapter_config_from_args(args),
            config_path=config_path,
        )
        _print_json(report)
        return 0 if report.get("ok") else 1

    if args.cmd == "identity":
        state_dir = _require_state_dir(args)
        if args.identity_cmd == "init":
            _print_json(create_test_identity(state_dir))
            return 0
        if args.identity_cmd == "init-production":
            import getpass

            first = getpass.getpass("New FLOP Capability Tournament identity passphrase: ")
            second = getpass.getpass("Confirm passphrase: ")
            _print_json(
                create_production_identity(
                    state_dir=state_dir,
                    confirm=args.confirm,
                    passphrase=first,
                    passphrase_confirmation=second,
                )
            )
            return 0
        if args.identity_cmd == "show":
            _print_json(load_identity_meta(state_dir))
            return 0

    if args.cmd == "verify-receipt":
        payload = json.loads(Path(args.receipt).read_text(encoding="utf-8"))
        _print_json(verify_receipt(payload))
        return 0

    if args.cmd == "verify-credential":
        payload = json.loads(Path(args.credential).read_text(encoding="utf-8"))
        verification = verify_credential(payload)
        claim = router_capability_claim(payload)
        _print_json({"verification": verification, "router_claim": claim})
        return 0

    tournament = _open(args)
    if args.cmd == "publish-challenge":
        spec = load_challenge_file(Path(args.spec))
        challenge = tournament.publish_challenge(publisher_did=args.publisher_did, spec=spec)
        _print_json(challenge.to_dict())
        return 0
    if args.cmd == "list-challenges":
        _print_json([challenge.to_dict() for challenge in tournament.list_challenges()])
        return 0
    if args.cmd == "enter":
        entry, bundle, path = tournament.enter(
            args.challenge_id,
            agent_did=args.agent_did,
            relationship=args.operator_relationship,
        )
        _print_json(
            {
                "entry": entry.to_dict(),
                "receipt_bundle": bundle.to_dict(),
                "bundle_path": str(path),
            }
        )
        return 0
    if args.cmd == "sponsor":
        sponsorship = tournament.sponsor(
            args.challenge_id,
            sponsor_did=args.sponsor_did,
            amount_flop=args.amount_flop,
        )
        _print_json(sponsorship.to_dict())
        return 0
    if args.cmd == "submit-attempt":
        attempt = tournament.submit_attempt(
            args.challenge_id,
            agent_did=args.agent_did,
            payload=_load_json_file(Path(args.attempt_file)),
            relationship=args.operator_relationship,
        )
        _print_json(attempt.to_dict())
        return 0
    if args.cmd == "evaluate":
        _print_json(tournament.evaluate(args.attempt_id).to_dict())
        return 0
    if args.cmd == "award":
        attempt, credential, bundle, path = tournament.award(args.attempt_id)
        _print_json(
            {
                "attempt": attempt.to_dict(),
                "credential": credential.to_dict(),
                "receipt_bundle": bundle.to_dict(),
                "bundle_path": str(path),
                "credential_verification": verify_credential(credential.to_dict()),
            }
        )
        return 0
    if args.cmd == "show-credential":
        _print_json(
            tournament.show_credential(args.credential_id, attempt_id=args.attempt_id)
        )
        return 0
    if args.cmd == "show":
        _print_json(tournament.show(args.challenge_id))
        return 0
    if args.cmd == "paper-credit":
        tournament.credit_paper(args.account, args.amount_flop, args.reason)
        _print_json({"ok": True, "account": args.account, "balances": tournament.balances()})
        return 0
    if args.cmd == "balances":
        _print_json(tournament.balances())
        return 0
    raise SystemExit(f"unknown command: {args.cmd}")
