"""Stock screening, watchlist records, and evidence-linked report generation."""

from datetime import datetime
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, computed_field, field_validator

from openalpha_cn.domain._identity import stable_model_id
from openalpha_cn.domain.time import ensure_aware
from openalpha_cn.runtime.contracts import ResearchRunResult


class ScreeningCriteria(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    min_confidence: float = Field(default=0, ge=0, le=1)
    directions: tuple[Literal["bullish", "bearish", "neutral", "abstain"], ...] = ()
    final_actions: tuple[Literal["watch", "avoid", "abstain"], ...] = ()
    max_risk_flags: int | None = Field(default=None, ge=0)
    limit: int = Field(default=100, ge=1, le=1000)


class ScreeningItem(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    subject: str
    run_id: str
    signal_id: str
    decision_id: str
    direction: str
    final_action: str
    confidence: float
    strength: float
    risk_flags: tuple[str, ...]


class ScreeningResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    criteria: ScreeningCriteria
    items: tuple[ScreeningItem, ...]
    reviewed: int


class ResearchScreener:
    def screen(
        self,
        *,
        results: tuple[ResearchRunResult, ...],
        criteria: ScreeningCriteria,
    ) -> ScreeningResult:
        items = [
            ScreeningItem(
                subject=result.signal.subject,
                run_id=result.manifest.run_id,
                signal_id=result.signal.signal_id,
                decision_id=result.decision.decision_id,
                direction=result.signal.direction,
                final_action=result.decision.final_action,
                confidence=result.signal.confidence,
                strength=result.signal.strength,
                risk_flags=result.signal.risk_flags,
            )
            for result in results
            if result.signal.confidence >= criteria.min_confidence
            and (not criteria.directions or result.signal.direction in criteria.directions)
            and (
                not criteria.final_actions or result.decision.final_action in criteria.final_actions
            )
            and (
                criteria.max_risk_flags is None
                or len(result.signal.risk_flags) <= criteria.max_risk_flags
            )
        ]
        items.sort(key=lambda item: (-item.confidence, -item.strength, item.subject))
        return ScreeningResult(
            criteria=criteria,
            items=tuple(items[: criteria.limit]),
            reviewed=len(results),
        )


class WatchlistEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    subject: str = Field(min_length=1, max_length=128)
    tags: tuple[str, ...] = ()
    note: str = Field(default="", max_length=2000)
    created_at: datetime
    updated_at: datetime

    @field_validator("created_at", "updated_at")
    @classmethod
    def normalize_datetimes(cls, value: datetime) -> datetime:
        return ensure_aware(value)


class WatchlistStore(Protocol):
    """Extension contract for durable watchlist storage.

    Mirrors the `runtime.memory.ResearchMemory` precedent: the Protocol lives beside the
    `WatchlistEntry` model it manages, in the product layer (`product/`), not in
    `storage/`. `SQLiteWatchlistStore`'s full public surface is exactly `put`/`list`/
    `remove`, so this Protocol declares all three -- unlike the other storage Protocols
    in this task, there was no wider surface to narrow.
    """

    def put(self, entry: WatchlistEntry) -> None:
        """Create or intentionally update one local watchlist entry."""

    def list(self) -> tuple[WatchlistEntry, ...]:
        """List the local observation pool."""

    def remove(self, subject: str) -> bool:
        """Remove one watchlist entry; return whether it existed."""


class ResearchReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    run_id: str
    subject: str
    created_at: datetime
    title: str
    summary: str
    decision_id: str
    signal_id: str
    final_action: str
    evidence_ids: tuple[str, ...]
    risk_flags: tuple[str, ...]

    @field_validator("created_at")
    @classmethod
    def normalize_created_at(cls, value: datetime) -> datetime:
        return ensure_aware(value)

    @computed_field(return_type=str)  # type: ignore[prop-decorator]
    @property
    def report_id(self) -> str:
        return stable_model_id(prefix="rpt", model=self)


class ReportStore(Protocol):
    """Extension contract for durable research-report storage.

    Mirrors the `runtime.memory.ResearchMemory` precedent: the Protocol lives beside the
    `ResearchReport` model it manages, in the product layer (`product/`), not in
    `storage/`. `SQLiteReportStore`'s full public surface is exactly `append`/`get`/
    `list`, so this Protocol declares all three -- unlike the other storage Protocols in
    this task, there was no wider surface to narrow.
    """

    def append(self, report: ResearchReport) -> None:
        """Append one evidence-linked report, idempotent by report ID."""

    def get(self, report_id: str) -> ResearchReport | None:
        """Load a report by its content-derived ID."""

    def list(self, *, subject: str | None = None) -> tuple[ResearchReport, ...]:
        """List generated reports, optionally filtered by subject."""


class ResearchReportFactory:
    def build(self, result: ResearchRunResult) -> ResearchReport:
        return ResearchReport(
            run_id=result.manifest.run_id,
            subject=result.signal.subject,
            created_at=result.decision.created_at,
            title=f"{result.signal.subject} evidence-linked research report",
            summary=(
                f"{result.decision.final_action}: {result.signal.direction}; "
                f"confidence={result.signal.confidence:.2f}; "
                f"evidence={len(result.signal.evidence_ids)}"
            ),
            decision_id=result.decision.decision_id,
            signal_id=result.signal.signal_id,
            final_action=result.decision.final_action,
            evidence_ids=result.signal.evidence_ids,
            risk_flags=result.signal.risk_flags,
        )
