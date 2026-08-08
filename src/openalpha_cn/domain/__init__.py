"""Versioned domain contracts for OpenAlpha CN."""

from openalpha_cn.domain.decision import AgentDecision, DecisionLedger
from openalpha_cn.domain.evidence import EvidenceSnapshot
from openalpha_cn.domain.panel_batch import (
    ColumnarPanelBatch,
    PanelBatchError,
    PanelColumn,
    PanelColumnKind,
    TimelineColumns,
)
from openalpha_cn.domain.run import ArtifactDigest, CheckpointRecord, RunManifest, VersionRef
from openalpha_cn.domain.signal import SignalFrame
from openalpha_cn.domain.time import Timeline, ensure_aware, is_visible_at
from openalpha_cn.domain.trading_calendar import (
    CalendarDay,
    CalendarDayStatus,
    CalendarHorizon,
    CalendarHorizonError,
    TradingCalendar,
    TradingCalendarError,
    build_trading_calendar,
    trading_calendar_from_panel_rows,
)
from openalpha_cn.domain.validation import AttributionTerm, ValidationResult

__all__ = [
    "AgentDecision",
    "ArtifactDigest",
    "AttributionTerm",
    "CalendarDay",
    "CalendarDayStatus",
    "CalendarHorizon",
    "CalendarHorizonError",
    "CheckpointRecord",
    "ColumnarPanelBatch",
    "DecisionLedger",
    "EvidenceSnapshot",
    "PanelBatchError",
    "PanelColumn",
    "PanelColumnKind",
    "RunManifest",
    "SignalFrame",
    "Timeline",
    "TimelineColumns",
    "TradingCalendar",
    "TradingCalendarError",
    "ValidationResult",
    "VersionRef",
    "build_trading_calendar",
    "ensure_aware",
    "is_visible_at",
    "trading_calendar_from_panel_rows",
]
