from __future__ import annotations

from pathlib import Path
from typing import Any

from flop_work_exchange.canonical import atomic_write_json, load_json_object
from flop_work_exchange.models import EvidenceProfile
from flop_work_exchange.store import new_id

from flop_capability_tournament.constants import assert_isolated_state_dir
from flop_capability_tournament.credentials import EvidenceCredential
from flop_capability_tournament.exceptions import ChallengeStateError
from flop_capability_tournament.models import (
    Attempt,
    Challenge,
    Entry,
    Sponsorship,
    TournamentReceiptBundle,
    did_slug,
)


class TournamentStore:
    def __init__(self, state_dir: Path) -> None:
        self.state_dir = assert_isolated_state_dir(state_dir)
        self.challenges_dir = self.state_dir / "challenges"
        self.entries_dir = self.state_dir / "entries"
        self.attempts_dir = self.state_dir / "attempts"
        self.sponsorships_dir = self.state_dir / "sponsorships"
        self.credentials_dir = self.state_dir / "credentials"
        self.bundles_dir = self.state_dir / "receipt_bundles"
        self.receipts_dir = self.state_dir / "receipts"
        self.profiles_dir = self.state_dir / "evidence_profiles"

    def initialize(self) -> None:
        self.state_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        for path in (
            self.challenges_dir,
            self.entries_dir,
            self.attempts_dir,
            self.sponsorships_dir,
            self.credentials_dir,
            self.bundles_dir,
            self.receipts_dir,
            self.profiles_dir,
        ):
            path.mkdir(parents=True, exist_ok=True)

    def save_challenge(self, challenge: Challenge) -> None:
        atomic_write_json(
            self.challenges_dir / f"{challenge.challenge_id}.json", challenge.to_dict()
        )

    def load_challenge(self, challenge_id: str) -> Challenge:
        path = self.challenges_dir / f"{challenge_id}.json"
        if not path.exists():
            raise ChallengeStateError(f"unknown challenge: {challenge_id}")
        return Challenge.from_dict(load_json_object(path))

    def list_challenges(self) -> list[Challenge]:
        return [
            Challenge.from_dict(load_json_object(path))
            for path in sorted(self.challenges_dir.glob("FLOP-CHAL-*.json"))
        ]

    def save_entry(self, entry: Entry) -> None:
        atomic_write_json(self.entries_dir / f"{entry.entry_id}.json", entry.to_dict())

    def load_entry(self, entry_id: str) -> Entry:
        path = self.entries_dir / f"{entry_id}.json"
        if not path.exists():
            raise ChallengeStateError(f"unknown entry: {entry_id}")
        return Entry.from_dict(load_json_object(path))

    def list_entries(self, challenge_id: str | None = None) -> list[Entry]:
        entries = [
            Entry.from_dict(load_json_object(path))
            for path in sorted(self.entries_dir.glob("FLOP-ENTRY-*.json"))
        ]
        if challenge_id is None:
            return entries
        return [entry for entry in entries if entry.challenge_id == challenge_id]

    def find_entry(self, challenge_id: str, agent_did: str) -> Entry | None:
        for entry in self.list_entries(challenge_id):
            if entry.agent_did == agent_did:
                return entry
        return None

    def save_sponsorship(self, sponsorship: Sponsorship) -> None:
        atomic_write_json(
            self.sponsorships_dir / f"{sponsorship.sponsorship_id}.json",
            sponsorship.to_dict(),
        )

    def list_sponsorships(self, challenge_id: str) -> list[Sponsorship]:
        return [
            Sponsorship.from_dict(load_json_object(path))
            for path in sorted(self.sponsorships_dir.glob("FLOP-SPON-*.json"))
            if load_json_object(path).get("challenge_id") == challenge_id
        ]

    def save_attempt(self, attempt: Attempt) -> None:
        atomic_write_json(self.attempts_dir / f"{attempt.attempt_id}.json", attempt.to_dict())

    def load_attempt(self, attempt_id: str) -> Attempt:
        path = self.attempts_dir / f"{attempt_id}.json"
        if not path.exists():
            raise ChallengeStateError(f"unknown attempt: {attempt_id}")
        return Attempt.from_dict(load_json_object(path))

    def list_attempts(self, challenge_id: str | None = None) -> list[Attempt]:
        attempts = [
            Attempt.from_dict(load_json_object(path))
            for path in sorted(self.attempts_dir.glob("FLOP-ATTEMPT-*.json"))
        ]
        if challenge_id is None:
            return attempts
        return [attempt for attempt in attempts if attempt.challenge_id == challenge_id]

    def save_credential(self, credential: EvidenceCredential) -> Path:
        path = self.credentials_dir / f"{credential.credential_id}.json"
        atomic_write_json(path, credential.to_dict())
        return path

    def load_credential(self, credential_id: str) -> dict[str, Any]:
        path = self.credentials_dir / f"{credential_id}.json"
        if not path.exists():
            raise ChallengeStateError(f"unknown credential: {credential_id}")
        return load_json_object(path)

    def find_credential_for_attempt(self, attempt_id: str) -> dict[str, Any] | None:
        for path in sorted(self.credentials_dir.glob("FLOP-CRED-*.json")):
            payload = load_json_object(path)
            if payload.get("attempt_id") == attempt_id:
                return payload
        return None

    def save_bundle(self, bundle: TournamentReceiptBundle) -> Path:
        name = bundle.attempt_id or bundle.challenge_id
        path = self.bundles_dir / f"{name}.json"
        atomic_write_json(path, bundle.to_dict())
        return path

    def save_receipt_leg(
        self, prefix: str, role: str, payload: dict[str, Any]
    ) -> Path:
        path = self.receipts_dir / f"{prefix}.{role}.json"
        atomic_write_json(path, payload)
        return path

    def load_profile(self, did: str) -> EvidenceProfile:
        path = self.profiles_dir / f"{did_slug(did)}.json"
        if not path.exists():
            return EvidenceProfile(did=did)
        return EvidenceProfile.from_dict(load_json_object(path))

    def save_profile(self, profile: EvidenceProfile) -> None:
        atomic_write_json(self.profiles_dir / f"{did_slug(profile.did)}.json", profile.to_dict())


def require_status(current: str, allowed: set[str], action: str, label: str) -> None:
    if current not in allowed:
        raise ChallengeStateError(
            f"cannot {action} {label} in status {current}; allowed: {', '.join(sorted(allowed))}"
        )


__all__ = ["TournamentStore", "new_id", "require_status"]
