"""Shared deterministic research cycle used by live, replay, and backtest modes."""

import platform
from collections.abc import Callable, Sequence
from datetime import datetime
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from openalpha_cn.agents.base import AgentContext, AgentResult, ResearchAgent
from openalpha_cn.agents.baseline import baseline_agents
from openalpha_cn.decisions.risk import RiskGate
from openalpha_cn.domain.decision import AgentDecision, DecisionLedger
from openalpha_cn.domain.evidence import EvidenceSnapshot
from openalpha_cn.domain.run import ArtifactDigest, RunManifest, VersionRef
from openalpha_cn.domain.signal import SignalFrame
from openalpha_cn.domain.time import ensure_aware
from openalpha_cn.runtime.memory import MemoryEntry, ResearchMemory
from openalpha_cn.runtime.router import AgentRouter
from openalpha_cn.storage.sqlite import SQLiteRunRepository


class RunConflictError(RuntimeError):
    """Raised when a run ID is reused with different immutable inputs."""


class ResearchRunRequest(BaseModel):
    """All deterministic inputs to one research cycle."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    run_id: str = Field(min_length=1, max_length=128)
    mode: Literal["live", "replay", "backtest"]
    subject: str = Field(min_length=1, max_length=128)
    as_of: datetime
    evidence: tuple[EvidenceSnapshot, ...]
    code_commit: str = Field(min_length=7, max_length=64)
    config_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    random_seed: int

    @field_validator("as_of")
    @classmethod
    def normalize_as_of(cls, value: datetime) -> datetime:
        return ensure_aware(value)

    @model_validator(mode="after")
    def validate_evidence(self) -> Self:
        if any(item.subject != self.subject for item in self.evidence):
            raise ValueError("all evidence must match the requested subject")
        if any(not item.visible_at(self.as_of) for item in self.evidence):
            raise ValueError("evidence is not visible at request as_of")
        return self


class ResearchRunResult(BaseModel):
    """Signal, decision, manifest, and agent outputs from one cycle."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    signal: SignalFrame
    decision: DecisionLedger
    manifest: RunManifest
    agent_results: tuple[AgentResult, ...]


class ResearchEngine:
    """Run deterministic agents, aggregate a signal, apply risk, and persist."""

    def __init__(
        self,
        *,
        repository: SQLiteRunRepository,
        memory: ResearchMemory,
        clock: Callable[[], datetime],
        agents: Sequence[ResearchAgent] | None = None,
        router: AgentRouter | None = None,
        risk_gate: RiskGate | None = None,
    ) -> None:
        self.repository = repository
        self.memory = memory
        self.clock = clock
        self.agents = tuple(agents or baseline_agents())
        self.router = router or AgentRouter()
        self.risk_gate = risk_gate or RiskGate()

    def run_cycle(self, request: ResearchRunRequest) -> ResearchRunResult:
        """Execute the same evidence-to-decision path for every run mode."""
        existing_manifest = self.repository.get_run(request.run_id)
        run_time = existing_manifest.started_at if existing_manifest else ensure_aware(self.clock())
        context = AgentContext(
            run_id=request.run_id,
            subject=request.subject,
            as_of=request.as_of,
            evidence=request.evidence,
        )
        selected = self.router.route(agents=self.agents, evidence=request.evidence)
        agent_results = tuple(agent.analyze(context) for agent in selected)
        signal = self._aggregate(request=request, results=agent_results)
        risk_decision = self.risk_gate.evaluate(signal)
        final_action = self._final_action(signal=signal, risk_decision=risk_decision)
        signal_ids = tuple(result.signal.signal_id for result in agent_results)
        if signal.direction != "abstain":
            signal_ids = (*signal_ids, signal.signal_id)

        manifest = RunManifest(
            run_id=request.run_id,
            mode=request.mode,
            as_of=request.as_of,
            code_commit=request.code_commit,
            config_digest=request.config_digest,
            provider_payload_digests=tuple(
                ArtifactDigest(name=item.evidence_id, sha256=item.content_hash)
                for item in request.evidence
            ),
            model_versions=tuple(
                VersionRef(component=result.agent_id, version="baseline/v1")
                for result in agent_results
            ),
            prompt_versions=(),
            random_seed=request.random_seed,
            environment=(VersionRef(component="python", version=platform.python_version()),),
            started_at=run_time,
            finished_at=run_time,
            status="succeeded",
        )
        decision = DecisionLedger(
            run_id=request.run_id,
            created_at=run_time,
            agent_outputs=tuple(
                AgentDecision(
                    agent_id=result.agent_id,
                    signal_id=result.signal.signal_id,
                    recommendation=self._recommendation(result.signal),
                    rationale=result.rationale,
                )
                for result in agent_results
            ),
            routing_path=(*[result.agent_id for result in agent_results], "risk-gate"),
            risk_decision=risk_decision,
            final_action=final_action,
            evidence_ids=signal.evidence_ids,
            signal_ids=signal_ids,
            code_commit=request.code_commit,
            model_versions=manifest.model_versions,
            prompt_versions=manifest.prompt_versions,
        )
        result = ResearchRunResult(
            signal=signal,
            decision=decision,
            manifest=manifest,
            agent_results=agent_results,
        )
        self._persist_idempotently(result)
        self.memory.append(
            MemoryEntry(
                run_id=request.run_id,
                subject=request.subject,
                created_at=run_time,
                decision_id=decision.decision_id,
                signal_id=signal.signal_id,
                summary=f"{final_action}: {signal.direction} at confidence {signal.confidence:.2f}",
            )
        )
        return result

    def _persist_idempotently(self, result: ResearchRunResult) -> None:
        existing_manifest = self.repository.get_run(result.manifest.run_id)
        if existing_manifest is None:
            self.repository.append_run(result.manifest)
        elif existing_manifest != result.manifest:
            raise RunConflictError(
                f"run_id conflicts with an existing manifest: {result.manifest.run_id}"
            )

        existing_decision = self.repository.get_decision_for_run(result.manifest.run_id)
        if existing_decision is None:
            self.repository.append_decision(result.decision)
        elif existing_decision != result.decision:
            raise RunConflictError(
                f"run_id conflicts with an existing decision: {result.manifest.run_id}"
            )

    @staticmethod
    def _aggregate(
        *,
        request: ResearchRunRequest,
        results: tuple[AgentResult, ...],
    ) -> SignalFrame:
        if not results:
            return SignalFrame(
                subject=request.subject,
                as_of=request.as_of,
                direction="abstain",
                strength=0,
                confidence=0,
                horizon="5d",
                abstention_reason="No supported point-in-time evidence was available.",
            )
        strength = sum(result.signal.strength for result in results) / len(results)
        confidence = sum(result.signal.confidence for result in results) / len(results)
        direction: Literal["bullish", "bearish", "neutral"] = (
            "bullish" if strength > 0.15 else "bearish" if strength < -0.15 else "neutral"
        )
        evidence_ids = tuple(
            dict.fromkeys(
                evidence_id for result in results for evidence_id in result.signal.evidence_ids
            )
        )
        return SignalFrame(
            subject=request.subject,
            as_of=request.as_of,
            direction=direction,
            strength=strength,
            confidence=confidence,
            horizon="5d",
            evidence_ids=evidence_ids,
            confirmation_conditions=tuple(
                dict.fromkeys(
                    item for result in results for item in result.signal.confirmation_conditions
                )
            ),
            invalidation_conditions=tuple(
                dict.fromkeys(
                    item for result in results for item in result.signal.invalidation_conditions
                )
            ),
            risk_flags=tuple(
                sorted({item for result in results for item in result.signal.risk_flags})
            ),
        )

    @staticmethod
    def _recommendation(signal: SignalFrame) -> Literal["support", "oppose", "abstain"]:
        if signal.direction == "bullish":
            return "support"
        if signal.direction == "bearish":
            return "oppose"
        return "abstain"

    @staticmethod
    def _final_action(
        *,
        signal: SignalFrame,
        risk_decision: Literal["pass", "reduce", "block"],
    ) -> Literal["watch", "avoid", "abstain"]:
        if signal.direction == "abstain" or risk_decision == "block":
            return "abstain"
        if signal.direction == "bearish":
            return "avoid"
        if signal.direction == "bullish":
            return "watch"
        return "abstain"
