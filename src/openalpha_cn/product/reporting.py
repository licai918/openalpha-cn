"""The evidence-linked research report: its storage contract and the factory that builds one.

Split out of `product/research.py` by `V2-P4-006`, which found three unrelated
responsibilities in one file: the screen (now `product/screening.py`), the watchlist contract
(`product/watchlist.py`), and this. `product/research.py` re-exports every name below
unchanged, so `from openalpha_cn.product.research import ReportStore` keeps working and no
caller moved.

`ResearchReport` and `RESEARCH_REPORT_VERSIONS` live in `domain/report.py` (V2-P0B-012, so
`storage/product.py` can persist them without importing upward) and are re-exported here for
the same reason `product/research.py` re-exported them before: the Protocol, the factory and
the shape they both speak about read as one contract at one import.
"""

from __future__ import annotations

from typing import Protocol

from openalpha_cn.domain.report import RESEARCH_REPORT_VERSIONS, ResearchReport
from openalpha_cn.runtime.contracts import ResearchRunResult

__all__ = [
    "RESEARCH_REPORT_VERSIONS",
    "ReportStore",
    "ResearchReport",
    "ResearchReportFactory",
]


class ReportStore(Protocol):
    """Extension contract for durable research-report storage.

    Mirrors the `runtime.memory.ResearchMemory` precedent: the Protocol lives in the
    product layer (`product/`), not in `storage/`. (`ResearchReport` itself moved to
    `domain.report` in V2-P0B-012, re-exported here unchanged -- see that module's
    docstring -- but this Protocol, being behavior rather than a stored data shape, stayed
    put.) `SQLiteReportStore`'s full public surface is exactly `append`/`get`/`list`, so
    this Protocol declares all three -- unlike the other storage Protocols in this task,
    there was no wider surface to narrow.
    """

    def append(self, report: ResearchReport) -> None:
        """Append one evidence-linked report, idempotent by report ID."""

    def get(self, report_id: str) -> ResearchReport | None:
        """Load a report by its content-derived ID."""

    def list(self, *, subject: str | None = None) -> tuple[ResearchReport, ...]:
        """List generated reports, optionally filtered by subject."""


class ResearchReportFactory:
    """Turn one completed research cycle into one immutable, evidence-linked report."""

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
