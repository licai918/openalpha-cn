"""The product layer's one import surface, re-exporting the three contracts split out of it.

`V2-P4-006` measured this file carrying three unrelated responsibilities across 157 lines --
the screen, the watchlist storage Protocol, and the report Protocol plus its factory -- and
moved each into a module of its own:

- `product/screening.py` -- `ScreeningCriteria`, `ScreeningItem`, `ScreeningExclusion`,
  `ScreeningResult`, `ResearchScreener`, now governance-ordered.
- `product/governance.py` -- what a risk flag is *worth*, asked of the two gates this build
  already ships rather than restated as a fourth list of flag strings.
- `product/watchlist.py` -- `WatchlistStore`.
- `product/reporting.py` -- `ReportStore`, `ResearchReportFactory`.

**Nothing moved for a caller.** Every name this module exported before is re-exported here as
the same object, which is `runtime/contracts.py`'s arrangement for `ResearchRunRequest` and
`domain/report.py`'s for `ResearchReport`, applied a third time and for the same reason: a
split that renames the import path makes every consumer part of the change. Four places
outside this issue's ownership import from here -- `sdk.py`, `api/app.py`,
`runtime/composition.py`, and the probe module `tests/unit/test_import_layering.py::
test_storage_no_upward_deps_contract_rejects_indirect_leak_via_neutral_module` writes to disk
-- and none of them was touched.

`tests/unit/product/test_governed_screening.py::
test_the_facade_re_exports_the_same_objects_the_split_modules_declare` holds that identity --
`is`, not equality -- so a re-export that quietly became a copy is red.

This module is also named in `tests/unit/test_import_layering.py`'s `CONTRACT_ONLY_CONSUMERS`,
which requires it to reach `ResearchRunResult` through `runtime.contracts` and never
`runtime.engine`, and to reach no engine-owned storage module transitively. The re-exports
below inherit that: `product/screening.py` and `product/reporting.py` import from
`runtime.contracts`, and `product/governance.py`'s two gates (`decisions/risk.py`,
`agents/committee.py`) have `domain`-only closures, measured with grimp.
"""

from __future__ import annotations

from openalpha_cn.product.governance import (
    SEVERITY_ORDER,
    SEVERITY_RANK,
    GovernanceSeverity,
    GovernanceVerdict,
    assess,
    flag_severity,
)
from openalpha_cn.product.reporting import (
    RESEARCH_REPORT_VERSIONS,
    ReportStore,
    ResearchReport,
    ResearchReportFactory,
)
from openalpha_cn.product.screening import (
    EXCLUSION_PRECEDENCE,
    KNOWN_SCREENING_LIMITATIONS,
    PER_RESULT_EXCLUSION_REASONS,
    SCREENING_LIMITATION_CODES,
    ResearchScreener,
    ScreeningCriteria,
    ScreeningExclusion,
    ScreeningExclusionReason,
    ScreeningItem,
    ScreeningLimitation,
    ScreeningResult,
)
from openalpha_cn.product.watchlist import (
    WATCHLIST_ENTRY_VERSIONS,
    WatchlistEntry,
    WatchlistStore,
)

__all__ = [
    "EXCLUSION_PRECEDENCE",
    "KNOWN_SCREENING_LIMITATIONS",
    "PER_RESULT_EXCLUSION_REASONS",
    "RESEARCH_REPORT_VERSIONS",
    "SCREENING_LIMITATION_CODES",
    "SEVERITY_ORDER",
    "SEVERITY_RANK",
    "WATCHLIST_ENTRY_VERSIONS",
    "GovernanceSeverity",
    "GovernanceVerdict",
    "ReportStore",
    "ResearchReport",
    "ResearchReportFactory",
    "ResearchScreener",
    "ScreeningCriteria",
    "ScreeningExclusion",
    "ScreeningExclusionReason",
    "ScreeningItem",
    "ScreeningLimitation",
    "ScreeningResult",
    "WatchlistEntry",
    "WatchlistStore",
    "assess",
    "flag_severity",
]
