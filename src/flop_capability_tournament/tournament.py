from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from flop_work_exchange.amounts import micro_to_flop_string, parse_flop_to_micro
from flop_work_exchange.canonical import result_hash_for
from flop_work_exchange.config import OperatorRelationship
from flop_work_exchange.models import Receipt, now_iso
from flop_work_exchange.policy import (
    apply_deal_policy,
    classify_operator_relationship,
    detect_wash_risk,
    reject_payment_proof_claims,
)
from flop_work_exchange.receipts import sign_receipt, verify_receipt

from flop_capability_tournament.adapters.bench import evaluate_attempt
from flop_capability_tournament.config import TournamentConfig, write_resolved_config
from flop_capability_tournament.constants import (
    BENCH_DID,
    BENCH_FEE_ACCOUNT,
    TOURNAMENT_FEE_ACCOUNT,
    prize_pool_account,
)
from flop_capability_tournament.credentials import (
    EvidenceCredential,
    sign_credential,
    verify_credential,
)
from flop_capability_tournament.exceptions import (
    AdapterError,
    ChallengeStateError,
    NotLiveError,
    PolicyError,
    SafetyError,
    ValidationError,
)
from flop_capability_tournament.exchange_client import (
    InProcessWorkExchangeClient,
    WorkExchangeClient,
)
from flop_capability_tournament.identity import (
    ensure_test_identity,
    load_tournament_key,
    require_did,
)
from flop_capability_tournament.models import (
    Attempt,
    AttemptStatus,
    Challenge,
    ChallengeSpec,
    ChallengeStatus,
    Entry,
    Sponsorship,
    TournamentReceiptBundle,
    TournamentReceiptLeg,
)
from flop_capability_tournament.store import TournamentStore, new_id, require_status

ALLOWED = {
    "enter": {ChallengeStatus.PUBLISHED.value},
    "sponsor": {ChallengeStatus.PUBLISHED.value},
    "submit_attempt": {ChallengeStatus.PUBLISHED.value},
    "evaluate": {AttemptStatus.SUBMITTED.value},
    "award": {AttemptStatus.EVALUATED.value},
}


class CapabilityTournament:
    def __init__(
        self,
        config: TournamentConfig,
        *,
        exchange: WorkExchangeClient | None = None,
    ) -> None:
        self.config = config
        self.store = TournamentStore(config.resolved_state_dir())
        self.store.initialize()
        write_resolved_config(self.store.state_dir, config)
        ensure_test_identity(self.store.state_dir)
        if config.settlement_backend == "testnet" and exchange is None:
            self.exchange: WorkExchangeClient = InProcessWorkExchangeClient(
                self.store.state_dir,
                settlement_backend="testnet",
                policy=config.policy,
                known_family_dids=config.known_family_dids,
            )
        elif exchange is None:
            self.exchange = InProcessWorkExchangeClient(
                self.store.state_dir,
                policy=config.policy,
                known_family_dids=config.known_family_dids,
            )
        else:
            self.exchange = exchange

    @classmethod
    def open(cls, state_dir: Path, config_path: Path | None = None) -> CapabilityTournament:
        from flop_capability_tournament.config import load_config

        return cls(load_config(state_dir, config_path))

    def tournament_did(self) -> str:
        return str(ensure_test_identity(self.store.state_dir)["did"])

    def credit_paper(self, account: str, amount_flop: str, reason: str = "paper-seed") -> None:
        self.exchange.credit_paper(account, amount_flop, reason)

    def _screen(self, artifact_type: str, artifact: dict[str, Any]) -> Any:
        return self.exchange.screen(artifact_type, artifact)

    def _classify(
        self,
        buyer_did: str,
        seller_did: str,
        *,
        relationship: str | None = None,
    ) -> OperatorRelationship:
        classified = classify_operator_relationship(
            buyer_did,
            seller_did,
            known_family_dids=self.config.known_family_dids,
        )
        if relationship is None:
            return classified
        if relationship not in {"independent", "same_operator", "related", "unknown"}:
            raise ValidationError("invalid operator_relationship")
        if classified == "same_operator" and relationship == "independent":
            raise SafetyError(
                "cannot present same-operator family DIDs as independent counterparties"
            )
        return relationship  # type: ignore[return-value]

    def _open_paid_leg(
        self,
        *,
        buyer_did: str,
        seller_did: str,
        outcome: str,
        service: str,
        price_micro: int,
        relationship: str | None,
        notes: str,
    ) -> tuple[str, str, str, Any]:
        job = self.exchange.post_job(
            buyer_did=buyer_did,
            outcome=outcome,
            service=service,
            budget_micro=price_micro,
        )
        offer = self.exchange.submit_offer(
            job_id=job.job_id,
            seller_did=seller_did,
            price_micro=price_micro,
            notes=notes,
        )
        plan = self.exchange.route_job(job.job_id)
        if plan.qualification != "QUALIFIED_PLAN" or plan.selected_offer_id != offer.offer_id:
            raise AdapterError("Router did not select this offer: " + "; ".join(plan.reasons))
        deal = self.exchange.accept_offer(offer.offer_id, relationship=relationship)
        return job.job_id, offer.offer_id, deal.deal_id, deal

    def _sign_leg(
        self,
        *,
        role: str,
        prefix: str,
        job_id: str,
        buyer_did: str,
        seller_did: str,
        amount_micro: int,
        service: str,
        reason: str,
        result_hash: str,
        bench_result: str,
        relationship: str,
        independent_rep: bool,
        independent_fee: bool,
        tclk_deal_id: str,
        completed_at: str,
    ) -> TournamentReceiptLeg:
        key, tournament_did = load_tournament_key(self.store.state_dir)
        receipt = Receipt(
            job_id=job_id,
            buyer_did=buyer_did,
            seller_did=seller_did,
            operator_relationship=relationship,  # type: ignore[arg-type]
            service=service,
            price_flop=micro_to_flop_string(amount_micro),
            payment_mode="paper",
            tclk_deal_id=tclk_deal_id,
            result_hash=result_hash,
            bench_result=bench_result,
            completed_at=completed_at,
            settlement_status="simulated",
            schema="flop-capability-tournament.receipt.v0.1",
            price_micro=amount_micro,
            independent_reputation_eligible=independent_rep,
            independent_fee_volume_eligible=independent_fee,
        )
        sign_receipt(receipt, key, tournament_did)
        payload = receipt.to_dict()
        verification = verify_receipt(payload)
        self.store.save_receipt_leg(prefix, role, payload)
        return TournamentReceiptLeg(
            role=role,
            receipt=payload,
            verification=verification,
            ledger_reason=reason,
            amount_micro=amount_micro,
        )

    def publish_challenge(self, *, publisher_did: str, spec: ChallengeSpec) -> Challenge:
        require_did(publisher_did)
        reject_payment_proof_claims(spec.title)
        reject_payment_proof_claims(spec.summary)
        reject_payment_proof_claims(spec.notes)
        if spec.entry_fee_micro < self.config.fees.management_micro:
            raise ValidationError("entry fee cannot be smaller than the management fee")
        verdict = self._screen(
            "challenge",
            {
                "title": spec.title,
                "summary": spec.summary,
                "kind": spec.kind_id,
                "publisher_did": publisher_did,
                "source_url": spec.source_url,
            },
        )
        if verdict.action == "REJECT":
            raise AdapterError(f"Sentinel REJECT on challenge: {', '.join(verdict.reasons)}")
        job = self.exchange.post_job(
            buyer_did=publisher_did,
            outcome=(
                f"{spec.title}: {spec.summary} Kind {spec.kind_id}. "
                "Fixtures are local and inert; never fetch URLs or rooms."
            ),
            service=f"tournament.challenge.{spec.kind}",
            budget_micro=spec.prize_micro,
        )
        challenge = Challenge(
            challenge_id=new_id("FLOP-CHAL"),
            publisher_did=publisher_did,
            spec=spec,
            payment_mode=self.config.payment_mode,
            catalog_job_id=job.job_id,
            sentinel_status=verdict.action,
            notes=list(verdict.reasons),
        )
        if spec.source_url:
            challenge.notes.append(
                "source_url is inert metadata; the tournament does not fetch or follow it"
            )
        self.store.save_challenge(challenge)
        return challenge

    def list_challenges(self) -> list[Challenge]:
        return self.store.list_challenges()

    def enter(
        self,
        challenge_id: str,
        *,
        agent_did: str,
        relationship: str | None = None,
    ) -> tuple[Entry, TournamentReceiptBundle, Path]:
        require_did(agent_did)
        challenge = self.store.load_challenge(challenge_id)
        require_status(challenge.status, ALLOWED["enter"], "enter", "challenge")
        if agent_did == challenge.publisher_did and not self.config.policy.allow_self_deals:
            raise PolicyError("self-deals are disabled by policy; publisher cannot enter")
        if self.store.find_entry(challenge_id, agent_did) is not None:
            raise ChallengeStateError(f"agent already entered challenge {challenge_id}")
        tournament_did = self.tournament_did()
        classified = self._classify(agent_did, tournament_did, relationship=relationship)
        prior_pairs = self.exchange.deal_counterparty_pairs()
        wash_risk = detect_wash_risk(
            buyer_did=agent_did,
            seller_did=tournament_did,
            prior_pairs=prior_pairs,
        )
        flags = apply_deal_policy(
            buyer_did=agent_did,
            seller_did=tournament_did,
            relationship=classified,
            policy=self.config.policy,
            wash_risk=wash_risk,
        )
        entry_fee = challenge.spec.entry_fee_micro
        available = self.exchange.paper_balance(agent_did)
        if available < entry_fee:
            raise ValidationError(
                f"agent paper balance {available} micro cannot cover entry fee {entry_fee}"
            )
        job_id, offer_id, deal_id, deal = self._open_paid_leg(
            buyer_did=agent_did,
            seller_did=tournament_did,
            outcome=f"Paper entry fee for {challenge.spec.kind_id} ({challenge.challenge_id})",
            service="tournament.entry",
            price_micro=entry_fee,
            relationship=classified,
            notes="capability tournament entry",
        )
        pool = prize_pool_account(challenge.challenge_id)
        management = self.config.fees.management_micro
        contribution = self.config.fees.prize_pool_contribution_micro(entry_fee)
        self.exchange.transfer(
            debit_account=agent_did,
            credit_account=TOURNAMENT_FEE_ACCOUNT,
            amount_micro=management,
            reason="tournament-management-fee",
            job_id=job_id,
        )
        self.exchange.transfer(
            debit_account=agent_did,
            credit_account=pool,
            amount_micro=contribution,
            reason="prize-pool-entry",
            job_id=job_id,
        )
        result_text = json.dumps(
            {
                "challenge_id": challenge.challenge_id,
                "kind_id": challenge.spec.kind_id,
                "agent_did": agent_did,
                "leg": "entry",
            },
            sort_keys=True,
        )
        job = self.exchange.submit_result(job_id, result_text)
        self.exchange.verify(job_id)
        completed_at = now_iso()
        entry_leg = self._sign_leg(
            role="entry",
            prefix=f"{challenge.challenge_id}.{agent_did[-12:]}",
            job_id=job_id,
            buyer_did=agent_did,
            seller_did=tournament_did,
            amount_micro=entry_fee,
            service="tournament.entry",
            reason="entry-fee",
            result_hash=job.result_hash or result_hash_for(result_text),
            bench_result="PASS",
            relationship=deal.operator_relationship,
            independent_rep=False,
            independent_fee=False,
            tclk_deal_id=deal.tclk_deal_id or "tclk-paper-tournament-entry",
            completed_at=completed_at,
        )
        bundle = TournamentReceiptBundle(
            bundle_id=new_id("FLOP-BUNDLE"),
            challenge_id=challenge.challenge_id,
            attempt_id=None,
            tournament_did=tournament_did,
            legs=[entry_leg],
            created_at=completed_at,
        )
        path = self.store.save_bundle(bundle)
        entry = Entry(
            entry_id=new_id("FLOP-ENTRY"),
            challenge_id=challenge.challenge_id,
            agent_did=agent_did,
            entry_fee_micro=entry_fee,
            job_id=job_id,
            offer_id=offer_id,
            deal_id=deal_id,
            tclk_deal_id=deal.tclk_deal_id,
            operator_relationship=deal.operator_relationship,
            independent_reputation_eligible=flags["independent_reputation_eligible"],
            independent_fee_volume_eligible=flags["independent_fee_volume_eligible"],
            wash_risk=flags["wash_risk"] or deal.wash_risk,
        )
        challenge.prize_pool_micro = self.exchange.paper_balance(pool)
        self.store.save_entry(entry)
        self.store.save_challenge(challenge)
        return entry, bundle, path

    def sponsor(
        self,
        challenge_id: str,
        *,
        sponsor_did: str,
        amount_flop: str | None = None,
        amount_micro: int | None = None,
    ) -> Sponsorship:
        require_did(sponsor_did)
        challenge = self.store.load_challenge(challenge_id)
        require_status(challenge.status, ALLOWED["sponsor"], "sponsor", "challenge")
        if amount_micro is None:
            if amount_flop is None:
                raise ValidationError("amount_flop or amount_micro is required")
            amount_micro = parse_flop_to_micro(amount_flop)
        if amount_micro <= 0:
            raise ValidationError("sponsorship must be positive")
        available = self.exchange.paper_balance(sponsor_did)
        if available < amount_micro:
            raise ValidationError(
                f"sponsor paper balance {available} micro cannot cover {amount_micro}"
            )
        pool = prize_pool_account(challenge.challenge_id)
        self.exchange.transfer(
            debit_account=sponsor_did,
            credit_account=pool,
            amount_micro=amount_micro,
            reason="prize-pool-sponsor",
            job_id=challenge.catalog_job_id,
        )
        sponsorship = Sponsorship(
            sponsorship_id=new_id("FLOP-SPON"),
            challenge_id=challenge.challenge_id,
            sponsor_did=sponsor_did,
            amount_micro=amount_micro,
        )
        challenge.prize_pool_micro = self.exchange.paper_balance(pool)
        self.store.save_sponsorship(sponsorship)
        self.store.save_challenge(challenge)
        return sponsorship

    def submit_attempt(
        self,
        challenge_id: str,
        *,
        agent_did: str,
        payload: dict[str, Any],
        relationship: str | None = None,
    ) -> Attempt:
        require_did(agent_did)
        if not isinstance(payload, dict):
            raise ValidationError("attempt payload must be a JSON object")
        challenge = self.store.load_challenge(challenge_id)
        require_status(challenge.status, ALLOWED["submit_attempt"], "submit_attempt", "challenge")
        entry = self.store.find_entry(challenge_id, agent_did)
        if entry is None:
            raise ChallengeStateError("agent must enter before submitting an attempt")
        reject_payment_proof_claims(json.dumps(payload, sort_keys=True))
        verdict = self._screen(
            "attempt",
            {
                "challenge_id": challenge.challenge_id,
                "kind": challenge.spec.kind,
                "agent_did": agent_did,
                "payload": payload,
            },
        )
        result_text = json.dumps(payload, sort_keys=True)
        result_hash = result_hash_for(result_text)
        classified = self._classify(
            challenge.publisher_did, agent_did, relationship=relationship
        )
        judge_rel = classify_operator_relationship(
            agent_did,
            BENCH_DID,
            known_family_dids=self.config.known_family_dids,
        )
        if judge_rel == "same_operator" and classified == "independent":
            raise SafetyError(
                "cannot present same-operator family DIDs as independent judges or peers"
            )
        prior_pairs = self.exchange.deal_counterparty_pairs()
        wash_risk = detect_wash_risk(
            buyer_did=challenge.publisher_did,
            seller_did=agent_did,
            prior_pairs=prior_pairs,
        )
        flags = apply_deal_policy(
            buyer_did=challenge.publisher_did,
            seller_did=agent_did,
            relationship=classified,
            policy=self.config.policy,
            wash_risk=wash_risk,
        )
        if judge_rel == "same_operator":
            flags["independent_reputation_eligible"] = False
            flags["independent_fee_volume_eligible"] = False
        job_id, offer_id, deal_id, deal = self._open_paid_leg(
            buyer_did=challenge.publisher_did,
            seller_did=agent_did,
            outcome=(
                f"Capability attempt for {challenge.spec.kind_id} "
                f"by {agent_did}. Prize "
                f"{micro_to_flop_string(challenge.spec.prize_micro)} paper-FLOP."
            ),
            service=f"tournament.attempt.{challenge.spec.kind}",
            price_micro=challenge.spec.prize_micro,
            relationship=classified,
            notes="capability tournament attempt",
        )
        if verdict.action != "REJECT":
            self.exchange.submit_result(job_id, result_text, result_hash)
        attempt = Attempt(
            attempt_id=new_id("FLOP-ATTEMPT"),
            challenge_id=challenge.challenge_id,
            entry_id=entry.entry_id,
            agent_did=agent_did,
            payload=payload,
            result_hash=result_hash,
            job_id=job_id,
            offer_id=offer_id,
            deal_id=deal_id,
            tclk_deal_id=deal.tclk_deal_id,
            operator_relationship=deal.operator_relationship,
            independent_reputation_eligible=flags["independent_reputation_eligible"]
            and deal.independent_reputation_eligible,
            independent_fee_volume_eligible=flags["independent_fee_volume_eligible"]
            and deal.independent_fee_volume_eligible,
            wash_risk=flags["wash_risk"] or deal.wash_risk,
            sentinel_status=verdict.action,
            judge_relationship=judge_rel,
            notes=list(verdict.reasons),
        )
        if verdict.action == "REJECT":
            attempt.status = AttemptStatus.REJECTED.value
            attempt.notes.append("Sentinel REJECT; attempt is not eligible for evaluation")
        self.store.save_attempt(attempt)
        return attempt

    def evaluate(self, attempt_id: str) -> Attempt:
        attempt = self.store.load_attempt(attempt_id)
        require_status(attempt.status, ALLOWED["evaluate"], "evaluate", "attempt")
        challenge = self.store.load_challenge(attempt.challenge_id)
        if attempt.judge_relationship == "same_operator":
            attempt.independent_reputation_eligible = False
            attempt.independent_fee_volume_eligible = False
            attempt.notes.append(
                "same-operator family DIDs cannot be independent judges or peers"
            )
        verdict = evaluate_attempt(
            challenge, attempt, allow_local_exec=self.config.allow_local_exec
        )
        attempt.bench_result = verdict.result
        attempt.bench_notes = verdict.notes
        attempt.bench_evidence_id = verdict.evidence_id
        if attempt.job_id and attempt.sentinel_status != "REJECT":
            wx_job = self.exchange.verify(attempt.job_id)
            if wx_job.bench_result != "PASS" and verdict.result == "PASS":
                attempt.bench_result = "FAIL"
                attempt.bench_notes = (
                    f"{verdict.notes}; Work Exchange Bench adapter: {wx_job.bench_result}"
                )
        if attempt.bench_result == "PASS":
            attempt.status = AttemptStatus.EVALUATED.value
        else:
            attempt.status = AttemptStatus.FAILED.value
        self.store.save_attempt(attempt)
        return attempt

    def award(
        self, attempt_id: str
    ) -> tuple[Attempt, EvidenceCredential, TournamentReceiptBundle, Path]:
        if self.config.settlement_backend != "paper":
            raise NotLiveError("settlement_execution is DISABLED for non-paper backends")
        attempt = self.store.load_attempt(attempt_id)
        require_status(attempt.status, ALLOWED["award"], "award", "attempt")
        if attempt.bench_result != "PASS" or not attempt.result_hash:
            raise ValidationError("attempt is not Bench-PASS with a result hash")
        challenge = self.store.load_challenge(attempt.challenge_id)
        fees = self.config.fees
        needed = fees.award_reserve_micro(challenge.spec.prize_micro)
        pool = prize_pool_account(challenge.challenge_id)
        available = self.exchange.paper_balance(pool)
        if available < needed:
            raise ValidationError(
                f"prize pool {available} micro cannot cover prize+evaluation {needed}"
            )
        tournament_did = self.tournament_did()
        key, _did = load_tournament_key(self.store.state_dir)
        completed_at = now_iso()
        job_id = attempt.job_id or attempt.attempt_id
        self.exchange.transfer(
            debit_account=pool,
            credit_account=attempt.agent_did,
            amount_micro=challenge.spec.prize_micro,
            reason="prize-award",
            job_id=job_id,
        )
        self.exchange.transfer(
            debit_account=pool,
            credit_account=BENCH_FEE_ACCOUNT,
            amount_micro=fees.evaluation_micro,
            reason="bench-evaluation-fee",
            job_id=job_id,
        )
        prize_leg = self._sign_leg(
            role="prize",
            prefix=attempt.attempt_id,
            job_id=job_id,
            buyer_did=challenge.publisher_did,
            seller_did=attempt.agent_did,
            amount_micro=challenge.spec.prize_micro,
            service="tournament.prize",
            reason="prize-award",
            result_hash=attempt.result_hash,
            bench_result="PASS",
            relationship=attempt.operator_relationship or "unknown",
            independent_rep=attempt.independent_reputation_eligible,
            independent_fee=attempt.independent_fee_volume_eligible,
            tclk_deal_id=attempt.tclk_deal_id or "tclk-paper-tournament-prize",
            completed_at=completed_at,
        )
        eval_leg = self._sign_leg(
            role="evaluation",
            prefix=attempt.attempt_id,
            job_id=job_id,
            buyer_did=attempt.agent_did,
            seller_did=tournament_did,
            amount_micro=fees.evaluation_micro,
            service="tournament.evaluation",
            reason="bench-evaluation-fee",
            result_hash=attempt.result_hash,
            bench_result="PASS",
            relationship=attempt.judge_relationship or "related",
            independent_rep=False,
            independent_fee=False,
            tclk_deal_id="tclk-paper-tournament-evaluation",
            completed_at=completed_at,
        )
        credential = EvidenceCredential(
            issuer_did=tournament_did,
            subject_did=attempt.agent_did,
            challenge_id=challenge.challenge_id,
            challenge_kind=challenge.spec.kind_id,
            attempt_id=attempt.attempt_id,
            result_hash=attempt.result_hash,
            bench_result="PASS",
            issued_at=completed_at,
            bench_evidence_id=attempt.bench_evidence_id,
            operator_relationship=attempt.operator_relationship,
            independent_reputation_eligible=attempt.independent_reputation_eligible,
            judge_relationship=attempt.judge_relationship,
        )
        sign_credential(credential, key, tournament_did)
        verify_credential(credential.to_dict())
        cred_path = self.store.save_credential(credential)
        bundle = TournamentReceiptBundle(
            bundle_id=new_id("FLOP-BUNDLE"),
            challenge_id=challenge.challenge_id,
            attempt_id=attempt.attempt_id,
            tournament_did=tournament_did,
            legs=[prize_leg, eval_leg],
            created_at=completed_at,
        )
        bundle_path = self.store.save_bundle(bundle)
        profile = self.store.load_profile(attempt.agent_did)
        profile.record_completion(
            relationship=attempt.operator_relationship or "unknown",
            fee_volume_micro=challenge.spec.prize_micro,
            receipt_id=str(prize_leg.receipt.get("receipt_id") or ""),
            job_id=attempt.attempt_id,
            result_hash=attempt.result_hash,
            bench_result="PASS",
            independent_reputation_eligible=attempt.independent_reputation_eligible,
            independent_fee_volume_eligible=attempt.independent_fee_volume_eligible,
            wash_risk=attempt.wash_risk,
        )
        self.store.save_profile(profile)
        attempt.status = AttemptStatus.AWARDED.value
        attempt.credential_id = credential.credential_id
        attempt.receipt_bundle_id = bundle.bundle_id
        attempt.notes.append(f"credential:{credential.credential_id}")
        challenge.prize_pool_micro = self.exchange.paper_balance(pool)
        self.store.save_attempt(attempt)
        self.store.save_challenge(challenge)
        _ = cred_path
        return attempt, credential, bundle, bundle_path

    def show_credential(
        self, credential_id: str | None = None, *, attempt_id: str | None = None
    ) -> dict[str, Any]:
        payload: dict[str, Any] | None = None
        if credential_id:
            payload = self.store.load_credential(credential_id)
        elif attempt_id:
            payload = self.store.find_credential_for_attempt(attempt_id)
            if payload is None:
                raise ChallengeStateError(f"no credential for attempt {attempt_id}")
        else:
            raise ValidationError("credential_id or attempt_id is required")
        return {
            "credential": payload,
            "verification": verify_credential(payload),
        }

    def show(self, challenge_id: str) -> dict[str, Any]:
        challenge = self.store.load_challenge(challenge_id)
        return {
            "challenge": challenge.to_dict(),
            "entries": [entry.to_dict() for entry in self.store.list_entries(challenge_id)],
            "attempts": [attempt.to_dict() for attempt in self.store.list_attempts(challenge_id)],
            "prize_pool_micro": self.exchange.paper_balance(prize_pool_account(challenge_id)),
        }

    def balances(self) -> dict[str, int]:
        return self.exchange.balances()
