"""The panel health report (`V2-P1-012`): what is missing, stale, duplicated or revised.

## This module aggregates; it does not re-diagnose

Eight datasets landed before this one and each brought its own health instrument:
`evaluate_readiness`'s twelve structured codes, seven `(code, detail)` limitation registries
plus the calendar's dated look-ahead list, `close_disagreements`, `explain_unpriced`,
`financial_ambiguity_report`, `PartitionCoverage.revised_row_count` and its label census.
Every one of them is *called* here and none is reimplemented. What this module adds is the
five things none of them could do alone:

1. **One contract for all of it.** A single closed code set, a category per code and a
   severity per code, so `V2-P1-013`'s gate can branch and `V2-P1-016`'s REST face can
   serialise without either of them parsing prose.
2. **Inherent limitation separated from this fetch's defect.** The registries describe what
   the dataset *structurally cannot* answer; the findings describe what went wrong *this
   time*. They are different fields of the report, and a finding that a known limitation
   could explain names that limitation in `related_limitations` rather than leaving the
   reader to guess.
3. **Cross-dataset consistency**, which no single-dataset check can see.
4. **Freshness judged per publication cadence.** One shared threshold is provably wrong in
   both directions, and `index_weight_requirement`'s docstring already records the shape:
   "a bound under about 35 days will refuse a completely healthy panel for most of every
   month, and one over a year hides a panel that stopped updating". A bound tight enough to
   catch a `daily` panel that stopped a fortnight ago is the first of those; a bound loose
   enough for the statement datasets is the second. It is a *fortnight* and not a session:
   see the warning under `_longest_closure` for what a real exchange calendar does to this
   bound, and `date_gap` for the check that actually finds a hole the next morning.
5. **A disclosed defect turned into a statement about *this* panel.** A registry entry is
   unconditional prose: `KNOWN_CALENDAR_LOOKAHEAD`'s three dates read the same whether the
   panel reaches them or not, which is how a real 2015 calendar partition produced
   `is_clean: true, findings: []` while carrying 132 days of proven look-ahead. The dataset
   modules cannot close that, because none of them can see the window this report was asked
   about. `_calendar_lookahead_check` can, and that is the whole of what it adds: the same
   disclosure, conditional on the horizon, with the dates named.

## Why the freshness bounds are here at all, when every `*_requirement` refuses to choose one

`panel_ingest.py`'s requirement builders take `max_staleness` and give it no default, on the
stated ground that any value would be choosing for the caller. That is right *there* and
would be wrong here, and the difference is what the number does. A bound on a
`ReadinessRequirement` **blocks a read**; a bound in this report **prints a line**. The cost
of a wrong bound is therefore a refused query in one place and a sentence in the other, so
this module may pick a default where the gate may not -- and every one of them is
overridable per dataset.

Three of the four bounds are *derived or cited* rather than picked:

- **daily** (`daily`, `daily_basic`, `adj_factor`, `stk_limit`): the exchange's own longest
  closure, read off the supplied `TradingCalendar`, plus one day. The arithmetic is exact:
  if the last session is `S` and the next is `S + gap`, then at any instant before `S + gap`
  closes, data reaching `S` is completely current, so staleness legitimately reaches `gap`.
  The extra day covers the publication lag (a session's rows become knowable at 16:30
  Asia/Shanghai, 1.5 hours after the 15:00 event instant the bound is measured from) and the
  timezone offset. No calendar, no bound: the waiver goes on the record instead.
- **monthly** (`index_weight`): the widest gap between the *month-end* sessions of the same
  calendar, plus the same day. A monthly publication is legitimately as old as the interval
  between two of them. A month the horizon stops inside is excluded, because the last session
  the calendar happens to know about in it is not that month's end; a month the horizon
  *starts* inside is kept, because its last visible session is its true month end (see
  `_longest_month_end_gap`). Fewer than two usable months, no bound.
- **quarterly** (the four statement datasets): the widest interval between two consecutive
  statutory disclosure deadlines -- the Q3 report is due 31 October and the next deadline,
  the annual report's, is 30 April, 182 days later. Filings arrive in bursts fixed by that
  rule and not by the trading calendar, so this one is cited rather than derived.
- **event-driven** (`stock_basic`, `namechange`, `suspend_d`, the two industry datasets):
  **no event-clock bound at all**, and that is a finding about the data rather than a gap
  here. A registry has no schedule -- 1999 holds exactly one termination in the whole corpus
  and 2013 holds eight lifecycle rows -- so any bound would refuse a healthy corpus. What
  the report gives instead is `DatasetHealth.fetch_age`, the age of the *fetch* rather than
  of the newest event, as a number with no verdict attached. `trade_cal` is separated out
  again as `published_in_advance`: its schedule is published a year ahead, so its newest
  event instant is in the future at any `as_of` inside the year and an event-clock bound is
  not merely unavailable but vacuous.

## Severity is a property of the code, and why `revised` and `duplicate` are notices

`blocking` is exactly `evaluate_readiness`'s verdict: the dataset cannot be read.
`warning` is a real inconsistency that does not block. `notice` is information the reader
asked for that is not a fault -- and revision and duplication are, on this corpus,
information. `V2-P1-011` measured that 81.7% of `fina_indicator`'s keys carry more than one
row and that both rows of a corrected pair share every clock; a report that called that a
defect would fire on every healthy financial partition, which is the failure mode this whole
module exists to avoid. The counts are reported; the verdict is not raised.

Severity is `HEALTH_CODE_SEVERITY`, a total function of the code, and not a decision taken at
each emission site. Every emission site had exactly one answer anyway, so the table loses
nothing; what it buys is that `V2-P1-013` can decide whether a code blocks it *without running
a report*, and that a change of verdict for any code is a one-line diff against a table a test
pins entry by entry rather than a severity argument somewhere in a 60-line function.

`PanelHealthReport.is_clean` is "no blocking and no warning", so a notice never makes a
healthy panel look sick. That is a load-bearing property in both directions and both are
pinned: a panel carrying only `revised_rows` / `duplicate_versions` / `ambiguous_filing` is
clean, and a panel carrying a `close_disagreement` is not.

## What the report cannot check, it says it could not check -- and where each half says it

`cross_checks` records every check that spans more than one dataset's own census by name, with
`ran` and, when it did not, why: silence from a check that never ran is otherwise
indistinguishable from silence from one that passed. A blocked or damaged dataset makes the
loads underneath a cross-check raise, and a doctor that propagated that would be a doctor that
crashes precisely on the panels it exists for -- so each check is wrapped, the failure becomes
a `check_unavailable` finding, and the rest of the report is still produced.

`calendar_lookahead` is the one entry there that opens no partition at all: its whole input is
the `TradingCalendar` the caller supplied, so it cannot fail that way, and the reason it is on
this list rather than being a per-dataset finding is that the calendar it judges is the same
one three datasets' `required_dates` were derived from. Its `ran=False` case is the ordinary
one -- a report given no calendar -- and it is exactly the case that must not read as clean.

`cross_checks` is only that half. The **per-dataset** checks that did not run are on
`DatasetHealth.readiness.checks_waived`, which is `panel/catalog.py`'s own contract
(`READINESS_WAIVABLE_CHECKS`) carried through untouched, and reading it is not optional for
anyone drawing a conclusion from an empty `findings`. It is not a rare case. Of the fifteen
datasets `DATASET_CADENCE` declares, exactly **three** carry a `required_dates` --
`daily`, `daily_basic` and `stk_limit`, all three derived from the supplied calendar -- and
the other twelve waive it structurally, each for its own recorded reason (`adj_factor` stores
a compressed step function with no per-session expectation; the statement datasets would need
the disclosure calendar of ~5,500 issuers; and so on). **`date_gap` can therefore only ever
fire on those three.** A mid-corpus session missing from an `adj_factor` partition whose last
session is still current produces zero findings, `is_clean is True`, and every cross-check
ran -- the fact that the question was not asked is on `checks_waived` and nowhere else.
`tests/integration/panel/test_panel_doctor.py` pins that hole as a known fact rather than
leaving it to be rediscovered, and pins the three-of-fifteen split so that a dataset silently
losing its date requirement fails a test here.

## Why this is a top-level module and not `panel/doctor.py`

`tests/unit/test_import_layering.py::
test_panel_package_has_zero_direct_edges_into_any_other_openalpha_cn_subpackage` pins
`openalpha_cn.panel` as importing no sibling subpackage at all, and this module must import
`domain` (for the limitation registries and the cross-dataset checks) as well as `panel`.
`panel_ingest.py` is the precedent and the reasoning is its own: the seam sits *above* the
package it must not be inside, so `openalpha_cn.panel`'s real import closure is unchanged by
this module's existence -- nothing moved out of `panel/` and `panel/` gained no edge. The
distinction the layering rule is actually about (a neutral module that lets a forbidden
runtime dependency keep existing while the metric stops seeing it) does not arise, because
the dependency runs `panel_doctor -> panel`, never back.
`tests/unit/test_panel_ingest_import_isolation.py` pins this module's own dependency set the
same way it pins `panel_ingest`'s.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta
from itertools import pairwise
from types import MappingProxyType
from typing import Final, Literal, Protocol

from openalpha_cn.domain.adjustment import (
    ADJ_FACTOR_DATASET,
    KNOWN_ADJUSTMENT_LIMITATIONS,
    AdjustmentError,
)
from openalpha_cn.domain.daily_prices import (
    DAILY_BASIC_DATASET,
    DAILY_BASIC_PANEL_COLUMNS,
    DAILY_DATASET,
    DAILY_PANEL_COLUMNS,
    KNOWN_PRICE_LIMITATIONS,
    PriceDataError,
    close_disagreements,
    close_index,
    priced_cross_section,
    session_returns,
)
from openalpha_cn.domain.financial_statements import (
    FINANCIAL_STATEMENT_DATASETS,
    KNOWN_FINANCIAL_STATEMENT_LIMITATIONS,
    FinancialStatementError,
    financial_ambiguity_report,
)
from openalpha_cn.domain.index_membership import (
    INDEX_WEIGHT_DATASET,
    INDEX_WEIGHT_PANEL_COLUMNS,
    KNOWN_INDEX_MEMBERSHIP_LIMITATIONS,
    IndexMembershipError,
)
from openalpha_cn.domain.industry_classification import (
    INDUSTRY_MEMBERSHIP_DATASET,
    INDUSTRY_TREE_DATASET,
    KNOWN_INDUSTRY_LIMITATIONS,
)
from openalpha_cn.domain.name_history import NAMECHANGE_DATASET
from openalpha_cn.domain.panel_batch import PanelBatchError
from openalpha_cn.domain.price_limits import (
    KNOWN_SUSPENSION_LIMITATIONS,
    PRICE_LIMIT_DATASET,
    PRICE_LIMIT_PANEL_COLUMNS,
    SUSPENSION_DATASET,
    SuspensionError,
    build_suspension_day,
    explain_unpriced,
)
from openalpha_cn.domain.stock_universe import (
    KNOWN_UNIVERSE_LIMITATIONS,
    STOCK_BASIC_DATASET,
    StockUniverseError,
)
from openalpha_cn.domain.trading_calendar import (
    CALENDAR_PANEL_COLUMNS,
    KNOWN_CALENDAR_LOOKAHEAD,
    TRADING_CALENDAR_DATASET,
    TradingCalendar,
    TradingCalendarError,
)
from openalpha_cn.panel.catalog import (
    DEFAULT_DATE_TIMEZONE,
    KNOWN_STORAGE_LIMITATIONS,
    READINESS_ISSUE_CODES,
    DatasetReadiness,
    PanelStorageError,
    PartitionCoverage,
    ReadinessRequirement,
)
from openalpha_cn.panel.store import PanelStore
from openalpha_cn.panel_ingest import (
    adjustment_requirement,
    daily_basic_requirement,
    daily_requirement,
    financial_statement_requirement,
    index_weight_requirement,
    industry_membership_requirement,
    industry_tree_requirement,
    load_adjustment_histories,
    load_daily_bars,
    load_daily_valuations,
    load_industry_histories,
    load_industry_trees,
    load_name_histories,
    load_statement_histories,
    load_stock_universe,
    load_suspensions,
    name_history_requirement,
    price_limit_requirement,
    stock_universe_requirement,
    suspension_requirement,
    trading_calendar_requirement,
)


class PanelDoctorError(RuntimeError):
    """Raised for a usage error in the report itself: an unknown dataset, or a report asked
    for a dataset whose publication cadence has never been declared."""


HealthCategory = Literal["missing", "stale", "duplicate", "revised", "inconsistent", "unanswerable"]
HealthSeverity = Literal["blocking", "warning", "notice"]
Cadence = Literal["daily", "monthly", "quarterly", "event_driven", "published_in_advance"]

HEALTH_CATEGORIES: Final[frozenset[str]] = frozenset(
    {"missing", "stale", "duplicate", "revised", "inconsistent", "unanswerable"}
)
"""The report's headings. The roadmap names four -- missing / stale / duplicate / revised --
and the other two are the gaps that survey found: `inconsistent` is where a fault only two
datasets together can show lands, and `unanswerable` is where a question that could not be
put at all lands (a requirement that inspected nothing, a partition holding information from
after `as_of`, a cross-check whose inputs would not load). Folding either into `missing`
would tell a reader that data is absent when the truth is that the check never ran."""

HEALTH_SEVERITIES: Final[frozenset[str]] = frozenset({"blocking", "warning", "notice"})
"""`blocking` is `evaluate_readiness`'s own verdict, carried through unchanged. `warning` is
an inconsistency that does not stop a read. `notice` is a measured fact the report was asked
for that is not a fault; see this module's docstring for why revision and duplication are
notices on this corpus and what would break if they were not."""

BLOCKS_A_READ: Final[frozenset[str]] = frozenset({"blocking", "warning"})
"""The severities `PanelHealthReport.is_clean` counts. Stated once, here, rather than inline
in the property: "is a notice excluded from the verdict?" is the question the whole notice
severity exists to answer, and it should be answerable by reading one name."""

READINESS_CODE_CATEGORY: Final[Mapping[str, HealthCategory]] = MappingProxyType(
    {
        "no_years_requested": "unanswerable",
        "empty_requirement": "unanswerable",
        "not_yet_knowable": "unanswerable",
        "partition_missing": "missing",
        "partition_file_missing": "missing",
        "partition_file_unreadable": "missing",
        "partition_row_count_mismatch": "inconsistent",
        "coverage_missing": "missing",
        "date_gap": "missing",
        "subject_missing": "missing",
        "field_missing": "missing",
        "coverage_stale": "stale",
        "stale": "stale",
    }
)
"""Every code `evaluate_readiness` emits, filed under one of this report's headings.

Stated as a table rather than derived from a naming convention, and pinned against
`READINESS_ISSUE_CODES` by equality in `tests/unit/test_panel_doctor_rules.py`: a fourteenth
code added upstream must fail a test here rather than arrive with no category and vanish from
every grouped view.

`partition_row_count_mismatch` is the one readiness code filed under `inconsistent` rather
than `missing`, and the reason is the reason `HEALTH_CATEGORIES` gives for having six
headings: the file may hold *more* rows than the record describes as easily as fewer, so
"missing" would tell a reader that data is absent when the truth is that two records of the
same partition disagree. `domain_rebuild_refused` is filed there for the same shape one plane
up -- one dataset contradicting itself rather than two datasets contradicting each other."""

DOCTOR_ISSUE_CODES: Final[frozenset[str]] = frozenset(
    {
        "subject_set_disagreement",
        "close_disagreement",
        "return_path_disagreement",
        "unexplained_unpriced",
        "ambiguous_filing",
        "duplicate_versions",
        "revised_rows",
        "check_unavailable",
        "domain_rebuild_refused",
        "event_after_as_of",
        "calendar_lookahead_in_horizon",
    }
)
"""The codes this module adds -- one per gap the eight dataset issues left open.

`subject_set_disagreement`, `close_disagreement`, `return_path_disagreement` and
`unexplained_unpriced` are cross-dataset: none of them is decidable from one dataset's
catalog. `ambiguous_filing` and `duplicate_versions` are the two facets of *duplicate*, which
had no code at all before this issue -- one counted from loaded filings, one read straight off
the catalog's label census so that it still answers when the filings will not load.
`revised_rows` is *revised*. `check_unavailable` is the report saying what it could not look
at.

`domain_rebuild_refused` is the one dimension readiness has no way to see. Readiness is
assessed from the catalog's own census -- files, subjects, fields, dates, freshness -- and a
row-level contradiction is invisible to every one of those. So a `suspend_d` partition serving
one security both an `R` row and an `S` row for one session was `READY` here and `CLEARED` by
`V2-P1-013`'s gate while `load_suspensions` on that same partition, at that same `as_of`,
raised. See `_rebuild_check`.

`event_after_as_of` is the **other** clock. `evaluate_readiness` compares one instant against
`as_of` -- `max_available_time`, which answers "could anyone have known this yet" -- and says
nothing about `last_event_time`, which answers "has this happened yet". Those come apart,
because nothing in `ColumnarPanelBatch` requires a row's event to precede its availability and
`trade_cal` is the reason: the exchange publishes next year's sessions this year, so a calendar
row's event is legitimately in the future. P2's product acceptance measured what that costs
everywhere else -- one `stock_basic` row whose listing is dated 2026-12-31 and which became
knowable on 2026-01-02 passed the batch contract, passed the writer, was reported `READY` here
and `CLEARED` by the gate, and then made `load_stock_universe` raise
`StockUniverseError: ... lists on 2026-12-31, after the 2026-08-11 registry snapshot`. The
exemption is by cadence rather than by dataset name: `published_in_advance` is the one cadence
that means "this dataset's newest event is normally in the future", and on the panel this was
measured against `trade_cal` was the only one of nine partitions whose `last_event_time`
post-dated the read. See `dataset_health`.

`calendar_lookahead_in_horizon` is the repository's own disclosed look-ahead, made conditional
on the panel. `KNOWN_CALENDAR_LOOKAHEAD` records three dates whose stored availability instant
this repository overstates, and `TradingCalendar.known_lookahead()` was built to report the
ones a given window covers "so `V2-P1-013`'s gate can see them" -- and then had no caller in
`src/` at all. A real 2015 calendar partition (365 rows, covering 2015-09-03 with 132 days of
look-ahead) therefore produced `is_clean: true, findings: []`, with the three dates appearing
only as a sentence in `limitations` that reads the same whether or not the panel reaches them.
See `_calendar_lookahead_check` for why the severity is `notice` and not `warning`."""

DOCTOR_CODE_CATEGORY: Final[Mapping[str, HealthCategory]] = MappingProxyType(
    {
        "subject_set_disagreement": "inconsistent",
        "close_disagreement": "inconsistent",
        "return_path_disagreement": "inconsistent",
        "unexplained_unpriced": "missing",
        "ambiguous_filing": "duplicate",
        "duplicate_versions": "duplicate",
        "revised_rows": "revised",
        "check_unavailable": "unanswerable",
        "domain_rebuild_refused": "inconsistent",
        "event_after_as_of": "unanswerable",
        "calendar_lookahead_in_horizon": "unanswerable",
    }
)
"""The heading each of this module's own codes files under.

Both new codes are `unanswerable` and `HEALTH_CATEGORIES`' own definition is why: it is where
"a partition holding information from after `as_of`" lands. `event_after_as_of` is exactly that
sentence with the *event* clock substituted for the availability one. `calendar_lookahead_in_
horizon` is the same shape at one remove -- the question "was this session's status knowable at
the instant the store claims" has no answer here, because `trade_cal` serves one snapshot and
no revision history -- and filing it under `inconsistent` would promise a second witness that
does not exist."""

PANEL_HEALTH_CODES: Final[frozenset[str]] = READINESS_ISSUE_CODES | DOCTOR_ISSUE_CODES
"""The closed set every finding's `code` is drawn from; `V2-P1-013` branches on it."""

HEALTH_CODE_CATEGORY: Final[Mapping[str, HealthCategory]] = MappingProxyType(
    {**READINESS_CODE_CATEGORY, **DOCTOR_CODE_CATEGORY}
)

HEALTH_CODE_SEVERITY: Final[Mapping[str, HealthSeverity]] = MappingProxyType(
    {
        **{code: "blocking" for code in sorted(READINESS_ISSUE_CODES)},
        "subject_set_disagreement": "warning",
        "close_disagreement": "warning",
        "return_path_disagreement": "warning",
        "unexplained_unpriced": "warning",
        "check_unavailable": "warning",
        "domain_rebuild_refused": "warning",
        "event_after_as_of": "warning",
        "ambiguous_filing": "notice",
        "duplicate_versions": "notice",
        "revised_rows": "notice",
        "calendar_lookahead_in_horizon": "notice",
    }
)
"""What each code means for `is_clean`, as a table rather than as an argument at each site.

Every readiness code is `blocking` because that is exactly what `evaluate_readiness` decided
by emitting it -- the doctor does not re-judge a verdict it did not make, and a table entry
that disagreed with `DatasetReadiness.state` would be a second opinion nobody asked for.

The three `notice` codes are the ones `V2-P1-011` measured to be ordinary on this corpus (see
this module's docstring). Everything the doctor itself concludes about *disagreement between
two datasets* is a `warning`, including `unexplained_unpriced`: a listed security with no bar
and no halt to account for it is a hole in the cross section whichever share of the session it
represents, and the write-time guard already refuses the shapes that are gross enough to see
without a second dataset. `check_unavailable` is a `warning` and not a `notice` because "I
could not look" must not read as "I looked and it was fine". `domain_rebuild_refused` is a
`warning` for the sharper version of that: the read this report exists to authorise is the
read that raised, and `GATE_BLOCKING_SEVERITIES` counts `warning`, so this is the severity at
which the gate stops clearing a partition its own reader will not return.
`event_after_as_of` is a `warning` for that same reason and is measured the same way: the read
it clears is the read that raised (`load_stock_universe`, on the partition this report called
`READY`).

**`calendar_lookahead_in_horizon` is the one code here that is deliberately a `notice`, and
the argument is not that it is harmless.** Three things decide it. It is an *inherent*
limitation of `trade_cal`, already carried in `KNOWN_PANEL_LIMITATIONS`, rather than a defect
of this fetch -- and `is_clean` is the verdict on the fetch, which is the separation this whole
module exists to keep (`KnownLimitation`'s docstring). It has **no remedy**: every blocking and
warning code in this table names an action (re-fetch, rebuild, name a session, narrow the
request), and this one cannot, because removing the defect needs a revision history the
endpoint does not serve. And a `warning` would refuse, permanently and with no way forward,
every panel whose calendar reaches 2015-09-03, 2015-09-04 or 2020-01-31 -- which is most
historical research this repository exists for -- and "a gate that refuses everything gets
switched off" is the argument this module already accepted twice, for the three measured
notices and for `unverified_daily_coverage`'s caveat form. What the `notice` buys over the
static prose it replaces is that it is *conditional*: it fires only when this panel's horizon
actually covers one of those dates, it names which, and it rides on the clearance
(`DependencyClearance.notices`) so a cleared caller holds it.

Pinned entry by entry in `tests/unit/test_panel_doctor_rules.py`: severity is the field
`V2-P1-013`'s gate branches on after `code`, and a silent demotion is the one change to this
module that would make a sick panel report `is_clean`."""


# --- publication cadence and the freshness bounds it implies ----------------------------------

DATASET_CADENCE: Final[Mapping[str, Cadence]] = MappingProxyType(
    {
        TRADING_CALENDAR_DATASET: "published_in_advance",
        STOCK_BASIC_DATASET: "event_driven",
        NAMECHANGE_DATASET: "event_driven",
        ADJ_FACTOR_DATASET: "daily",
        DAILY_DATASET: "daily",
        DAILY_BASIC_DATASET: "daily",
        SUSPENSION_DATASET: "event_driven",
        PRICE_LIMIT_DATASET: "daily",
        INDEX_WEIGHT_DATASET: "monthly",
        INDUSTRY_MEMBERSHIP_DATASET: "event_driven",
        INDUSTRY_TREE_DATASET: "event_driven",
        "income": "quarterly",
        "balancesheet": "quarterly",
        "cashflow": "quarterly",
        "fina_indicator": "quarterly",
    }
)
"""How often each ingested dataset publishes. Every dataset `panel_ingest.py` writes appears
here, pinned by a test, because a dataset with no declared cadence would otherwise be given
whatever bound the report's default happened to be -- which for `suspend_d` (a corpus with no
rows on a session where nothing was halted) would be a permanent false alarm."""

FRESHNESS_PUBLICATION_SLACK: Final[timedelta] = timedelta(days=1)
"""What the calendar-derived bounds add on top of the gap they measure.

The bounds are measured from `last_event_time`, the session's 15:00 Asia/Shanghai close,
while the rows become knowable at 16:30 and a caller's `as_of` may sit anywhere in the day.
One day absorbs both, and it is deliberately a whole day rather than the 1.5 hours the
publication lag actually is: this bound decides whether a line is printed, so erring towards
silence on a healthy panel is the right direction."""

QUARTERLY_DISCLOSURE_BOUND: Final[timedelta] = date(2028, 4, 30) - date(2027, 10, 31)
"""182 days: 31 October (the Q3 report deadline) to the following 30 April (the annual
report's), the widest interval between two consecutive statutory disclosure deadlines.

Written as the subtraction rather than as `timedelta(days=182)` so that the number cannot
drift away from the rule it comes from. The two years are arbitrary and non-leap; the
interval is a property of the calendar dates, not of those years."""


@dataclass(frozen=True, slots=True, kw_only=True)
class FreshnessPolicy:
    """The staleness bound applied to one dataset, and where the number came from.

    `basis` is not decoration. A bound that is `None` is a check that did not run, and the
    reader has to be able to tell "this dataset has no schedule so no bound is possible" from
    "no calendar was supplied so the bound could not be derived" -- the same distinction
    `DatasetReadiness.checks_waived` exists to make."""

    dataset: str
    cadence: Cadence
    max_staleness: timedelta | None
    basis: str


def _longest_closure(calendar: TradingCalendar) -> timedelta | None:
    """The widest gap between two consecutive open sessions, or `None` for a single session.

    ## What this is worth on a real calendar, which is less than it sounds

    The whole span of the supplied calendar is searched, so on any calendar containing a Spring
    Festival -- i.e. any real one covering a year -- the answer is that closure and not a
    weekend: the SSE/SZSE recess runs 11 or 12 calendar days from the last session before it to
    the first after, which with `FRESHNESS_PUBLICATION_SLACK` puts the daily bound at **12 to 13
    days for the whole year**. A `daily` panel that stopped updating last Tuesday is therefore
    *not* `stale` on this report, and will not be for a fortnight.

    That is the correct direction for a bound whose cost is a printed line -- a tighter one
    would call every healthy panel stale for a week after every Chinese New Year -- but it means
    `stale` is not the check that finds a panel that stopped. `date_gap` is, and it fires the
    next morning; the price-shaped datasets it can fire on are `daily`, `daily_basic` and
    `stk_limit`. `adj_factor` is the gap: it is on the daily cadence, it waives `required_dates`,
    and so a stop of under a fortnight in it is invisible to this report from both directions.
    A caller who needs a tighter bound than the whole-year closure states one through
    `panel_health_report`'s `freshness_overrides`, which records that it replaced the derived
    number; a caller who wants the bound derived from a *recent* stretch of the calendar passes
    a calendar covering only that stretch, since this reads whatever it is given."""
    days = calendar.trading_days
    if len(days) < 2:
        return None
    return max(later - earlier for earlier, later in pairwise(days))


def _longest_month_end_gap(calendar: TradingCalendar) -> timedelta | None:
    """The widest gap between the month-end sessions this calendar can actually see.

    A month whose last calendar day is past the horizon is skipped: the last session the
    calendar knows about in it is not that month's end, and treating it as one understates
    the gap by however much of the month is missing -- which would tighten the bound and
    report a healthy monthly panel as stale.

    A month the horizon *starts* inside is **not** skipped, and the asymmetry is deliberate
    rather than an oversight. Its last visible session is its true month-end session
    whenever it has one at all: every day after that session in the month is closed by
    definition, so a horizon beginning after it sees no session in that month and the month
    never enters this census. Skipping it as well would throw away a valid month-to-month gap
    -- on a horizon of 2026-05-15..2026-07-31 it is the difference between a 31-day bound and
    the true 32-day one."""
    horizon = calendar.horizon
    ends: dict[tuple[int, int], date] = {}
    for day in calendar.trading_days:
        next_month = (
            date(day.year + 1, 1, 1) if day.month == 12 else date(day.year, day.month + 1, 1)
        )
        if next_month - timedelta(days=1) > horizon.last_date:
            continue
        ends[(day.year, day.month)] = day
    ordered = sorted(ends.values())
    if len(ordered) < 2:
        return None
    return max(later - earlier for earlier, later in pairwise(ordered))


def freshness_policy(dataset: str, *, calendar: TradingCalendar | None = None) -> FreshnessPolicy:
    """The staleness bound for `dataset`, derived from `calendar` where the cadence allows.

    Raises `PanelDoctorError` for a dataset with no declared cadence rather than falling back
    to a default: a silent default here is exactly the shared threshold this module exists to
    replace.
    """
    cadence = DATASET_CADENCE.get(dataset)
    if cadence is None:
        raise PanelDoctorError(
            f"{dataset!r} has no declared publication cadence, so no freshness bound can be "
            f"chosen for it; declare one in DATASET_CADENCE (known: {sorted(DATASET_CADENCE)})"
        )
    if cadence == "daily":
        gap = _longest_closure(calendar) if calendar is not None else None
        if gap is None:
            return FreshnessPolicy(
                dataset=dataset,
                cadence=cadence,
                max_staleness=None,
                basis=(
                    "a daily bound is this exchange's longest closure plus the publication "
                    "slack, and no trading calendar with two sessions in it was supplied to "
                    "measure that closure from"
                ),
            )
        return FreshnessPolicy(
            dataset=dataset,
            cadence=cadence,
            max_staleness=gap + FRESHNESS_PUBLICATION_SLACK,
            basis=(
                f"this calendar's longest closure is {gap.days} day(s), plus "
                f"{FRESHNESS_PUBLICATION_SLACK.days} day of publication slack"
            ),
        )
    if cadence == "monthly":
        gap = _longest_month_end_gap(calendar) if calendar is not None else None
        if gap is None:
            return FreshnessPolicy(
                dataset=dataset,
                cadence=cadence,
                max_staleness=None,
                basis=(
                    "a monthly bound is the widest gap between month-end sessions, and the "
                    "supplied calendar does not cover two complete months to measure it from"
                ),
            )
        return FreshnessPolicy(
            dataset=dataset,
            cadence=cadence,
            max_staleness=gap + FRESHNESS_PUBLICATION_SLACK,
            basis=(
                f"the widest gap between two month-end sessions of this calendar is "
                f"{gap.days} day(s), plus {FRESHNESS_PUBLICATION_SLACK.days} day of "
                "publication slack"
            ),
        )
    if cadence == "quarterly":
        return FreshnessPolicy(
            dataset=dataset,
            cadence=cadence,
            max_staleness=QUARTERLY_DISCLOSURE_BOUND,
            basis=(
                "the widest interval between two consecutive statutory disclosure deadlines: "
                "31 October (Q3) to the following 30 April (annual)"
            ),
        )
    if cadence == "published_in_advance":
        return FreshnessPolicy(
            dataset=dataset,
            cadence=cadence,
            max_staleness=None,
            basis=(
                "the published schedule reaches the end of the year, so its newest event "
                "instant is ahead of any as_of inside that year and an event-clock bound "
                "cannot be exceeded by a healthy partition"
            ),
        )
    return FreshnessPolicy(
        dataset=dataset,
        cadence=cadence,
        max_staleness=None,
        basis=(
            "this dataset has no publication schedule -- a year with no rows is an ordinary "
            "year, not a missed fetch -- so no event-clock bound can be right; "
            "DatasetHealth.fetch_age reports how old the fetch is instead"
        ),
    )


# --- inherent limitations, from the eight dataset registries and the storage plane ------------


@dataclass(frozen=True, slots=True, kw_only=True)
class KnownLimitation:
    """One structural boundary of a stored dataset, as recorded by the module that owns it.

    Not a finding. These describe what the dataset can never answer, whoever fetched it and
    however well; a reader who cannot tell them apart from this fetch's defects will read a
    healthy panel as a sick one.

    `dates` is populated only for the calendar's entry, whose registry is a list of dated
    instances of one defect rather than a list of distinct defects.

    `datasets` is **empty** for the entries that come from `KNOWN_STORAGE_LIMITATIONS`, and
    that is a statement rather than an omission: those boundaries belong to the storage plane
    and hold for every dataset alike, so naming one would be false and naming all fifteen would
    make `known_limitations('adj_factor')` stop meaning "what the adjustment corpus cannot
    answer". `storage_limitations()` selects them; `panel_health_report` appends them to every
    report."""

    code: str
    datasets: tuple[str, ...]
    detail: str
    dates: tuple[date, ...] = ()


class _LimitationEntry(Protocol):
    """The shape the seven `(code, detail)` registries share.

    A structural type rather than a common base class: each dataset module owns its own
    limitation dataclass and none of them should have to import a shared one from here, which
    would invert the dependency (`domain -> panel_doctor`) the layering rules forbid."""

    @property
    def code(self) -> str: ...

    @property
    def detail(self) -> str: ...


def _limitations() -> tuple[KnownLimitation, ...]:
    registries: tuple[tuple[tuple[str, ...], Sequence[_LimitationEntry]], ...] = (
        ((STOCK_BASIC_DATASET,), KNOWN_UNIVERSE_LIMITATIONS),
        ((ADJ_FACTOR_DATASET,), KNOWN_ADJUSTMENT_LIMITATIONS),
        ((DAILY_DATASET, DAILY_BASIC_DATASET), KNOWN_PRICE_LIMITATIONS),
        ((SUSPENSION_DATASET, PRICE_LIMIT_DATASET), KNOWN_SUSPENSION_LIMITATIONS),
        ((INDEX_WEIGHT_DATASET,), KNOWN_INDEX_MEMBERSHIP_LIMITATIONS),
        (
            (INDUSTRY_MEMBERSHIP_DATASET, INDUSTRY_TREE_DATASET),
            KNOWN_INDUSTRY_LIMITATIONS,
        ),
        (FINANCIAL_STATEMENT_DATASETS, KNOWN_FINANCIAL_STATEMENT_LIMITATIONS),
    )
    collected = [
        KnownLimitation(code=entry.code, datasets=tuple(datasets), detail=entry.detail)
        for datasets, registry in registries
        for entry in registry
    ]
    collected.append(
        KnownLimitation(
            code="the_published_schedule_can_be_amended_after_it_becomes_answerable",
            datasets=(TRADING_CALENDAR_DATASET,),
            detail=(
                "trade_cal serves one snapshot per request and no revision history, so a "
                "mid-year amendment to the holiday schedule is answered from 1 January of "
                "that year as though it had always been known. "
                + "; ".join(
                    f"{instance.calendar_date.isoformat()} "
                    f"(announced {instance.announced_on.isoformat()}, "
                    f"{instance.lookahead_days} days of look-ahead)"
                    for instance in KNOWN_CALENDAR_LOOKAHEAD
                )
                + ". These are the reproduced instances, not an enumeration of every affected "
                "date; see KNOWN_CALENDAR_LOOKAHEAD."
            ),
            dates=tuple(sorted(instance.calendar_date for instance in KNOWN_CALENDAR_LOOKAHEAD)),
        )
    )
    collected.extend(
        KnownLimitation(code=entry.code, datasets=(), detail=entry.detail)
        for entry in KNOWN_STORAGE_LIMITATIONS
    )
    return tuple(collected)


KNOWN_PANEL_LIMITATIONS: Final[tuple[KnownLimitation, ...]] = _limitations()
"""Every entry of all eight dataset registries plus the storage plane's own, in one shape.

Seven of them are `(code, detail)` pairs and fold one-for-one. The calendar's is a list of
three dated instances of a *single* defect, so it folds into one entry carrying those dates:
mapping each date to its own limitation would tell a reader that the calendar has three
distinct problems, when it has one problem with three reproductions and an unknown number of
unreproduced ones.

`KNOWN_STORAGE_LIMITATIONS` folds in with an empty `datasets`, which is what keeps
`known_limitations()` a purely dataset-scoped question while still letting every report carry
them; see `KnownLimitation.datasets` and `storage_limitations()`."""


def known_limitations(datasets: Sequence[str]) -> tuple[KnownLimitation, ...]:
    """Every recorded limitation touching at least one of `datasets`, in registry order.

    Storage-plane limitations are deliberately **not** returned: they name no dataset, so
    `wanted & set(item.datasets)` is empty for them by construction. A caller asking "what can
    `adj_factor` not answer" gets the adjustment corpus' own boundaries and nothing else.
    `panel_health_report` adds `storage_limitations()` alongside this.
    """
    wanted = set(datasets)
    return tuple(item for item in KNOWN_PANEL_LIMITATIONS if wanted & set(item.datasets))


def storage_limitations() -> tuple[KnownLimitation, ...]:
    """The boundaries of the storage plane itself, which hold for every dataset alike.

    Selected by "names no dataset" rather than by re-reading `KNOWN_STORAGE_LIMITATIONS`, so
    that `KNOWN_PANEL_LIMITATIONS` stays the single list a reader has to know about and the two
    selectors provably partition it (`known_limitations` takes the entries that name a dataset,
    this one takes the rest).
    """
    return tuple(item for item in KNOWN_PANEL_LIMITATIONS if not item.datasets)


# --- cross-dataset composition ------------------------------------------------------------------


@dataclass(frozen=True, slots=True, kw_only=True)
class SubjectContainment:
    """A declared expectation that one dataset's subjects are a subset of another's.

    The direction is the whole content of the rule. On 2024-06-28 the three datasets served
    5,338 rows (`daily`), 5,387 (`adj_factor`) and 6,867 (`stk_limit`), and none of those
    differences is a defect: `daily` is a strict subset of `adj_factor` there
    (`domain/daily_prices.py`), and `stk_limit`'s 1,529 extra rows are funds and B shares
    (`domain/price_limits.py`). So the *superset* being wider is not a rule and produces
    nothing -- a report that called a 49-name or a 1,529-name gap a finding would fire on
    every real cross section. Only a name in the subset that the superset lacks is reported:
    a priced security with no adjustment factor cannot be adjusted, and one with no registry
    row cannot be placed in a universe.

    `related_limitations` names the recorded limitations that could account for a residue, so
    that a reader meeting one is told about the Beijing-board horizon rather than left to
    rediscover it."""

    subset: str
    superset: str
    detail: str
    related_limitations: tuple[str, ...]


SUBJECT_CONTAINMENTS: Final[tuple[SubjectContainment, ...]] = (
    SubjectContainment(
        subset=DAILY_DATASET,
        superset=ADJ_FACTOR_DATASET,
        detail=(
            "a security with a bar and no adjustment factor cannot be price-adjusted, so "
            "every cross section that includes it is refused"
        ),
        related_limitations=("silent_truncation_at_the_response_cap",),
    ),
    SubjectContainment(
        subset=DAILY_DATASET,
        superset=DAILY_BASIC_DATASET,
        detail=(
            "a security with a bar and no valuation row has no market capitalisation, so it "
            "drops out of every size-weighted or size-neutralised construction"
        ),
        related_limitations=("daily_basic_omits_the_beijing_board_before_2024",),
    ),
    SubjectContainment(
        subset=DAILY_DATASET,
        superset=PRICE_LIMIT_DATASET,
        detail=(
            "a security with a bar and no published band cannot have its limit touches "
            "decided, so execution falls back to an inferred band"
        ),
        related_limitations=(
            "stk_limit_starts_in_2007_and_reached_the_beijing_board_late",
            "stk_limit_covers_funds_and_b_shares_as_well_as_stocks",
        ),
    ),
    SubjectContainment(
        subset=DAILY_DATASET,
        superset=STOCK_BASIC_DATASET,
        detail=(
            "a security with a bar and no registry row cannot be placed in a universe, so it "
            "is invisible to survivorship-free reconstruction"
        ),
        related_limitations=(
            "a_priced_security_can_be_absent_from_the_registry",
            "ts_code_is_not_a_permanent_security_identity",
        ),
    ),
)
"""The containments this report checks, and nothing else.

Deliberately anchored on `daily`: it is the dataset every downstream construction reads, so a
name it carries that another dataset lacks is a name that will be silently dropped somewhere.
Rules in the other direction were considered and are absent because every one of them fires
on healthy real data."""


# --- the report's own vocabulary --------------------------------------------------------------


@dataclass(frozen=True, slots=True, kw_only=True)
class HealthFinding:
    """One thing the report has to say, as data rather than as prose.

    The field shape deliberately mirrors `ReadinessIssue`'s, because roughly half of these
    *are* readiness issues carried through with their codes intact -- `V2-P1-016` serialises
    one shape rather than two, and `V2-P1-013` branches on `code` without caring which half a
    finding came from.

    `datasets` is a tuple rather than a single name because a cross-dataset finding is about a
    pair, ordered (subset first, then superset; the dataset that disagrees first). `dates` and
    `items` are the finding's own subjects -- required dates that are absent, securities that
    disagree -- and are empty where the finding has none."""

    code: str
    category: HealthCategory
    severity: HealthSeverity
    datasets: tuple[str, ...]
    detail: str
    year: int | None = None
    count: int = 0
    dates: tuple[date, ...] = ()
    items: tuple[str, ...] = ()
    related_limitations: tuple[str, ...] = ()

    @property
    def dataset(self) -> str:
        """The dataset this finding is primarily about."""
        return self.datasets[0]


def _finding(
    code: str,
    *,
    datasets: tuple[str, ...],
    detail: str,
    year: int | None = None,
    count: int = 0,
    dates: tuple[date, ...] = (),
    items: tuple[str, ...] = (),
    related_limitations: tuple[str, ...] = (),
) -> HealthFinding:
    category = HEALTH_CODE_CATEGORY.get(code)
    severity = HEALTH_CODE_SEVERITY.get(code)
    if category is None or severity is None:  # pragma: no cover - the code set is closed
        raise PanelDoctorError(f"{code!r} is not one of the declared health codes")
    return HealthFinding(
        code=code,
        category=category,
        severity=severity,
        datasets=datasets,
        detail=detail,
        year=year,
        count=count,
        dates=dates,
        items=items,
        related_limitations=related_limitations,
    )


def findings_from_readiness(readiness: DatasetReadiness) -> tuple[HealthFinding, ...]:
    """Every issue of a readiness verdict, as findings, with its code unchanged.

    The codes are not renamed, re-bucketed or de-duplicated on the way through: `V2-P1-013`'s
    gate is specified against `evaluate_readiness`'s vocabulary, and a report that translated
    it would force the gate to know two."""
    return tuple(
        _finding(
            issue.code,
            datasets=(issue.dataset,),
            detail=issue.detail,
            year=issue.year,
            count=len(issue.missing_dates) + len(issue.missing_items),
            dates=issue.missing_dates,
            items=issue.missing_items,
        )
        for issue in readiness.issues
    )


def subject_containment_findings(
    subjects: Mapping[str, frozenset[str]],
) -> tuple[HealthFinding, ...]:
    """Check every declared containment both of whose datasets are present in `subjects`.

    A rule whose two datasets are not both available is not checked and produces nothing --
    which is why `PanelHealthReport.cross_checks` records separately that the containment
    check ran at all."""
    findings: list[HealthFinding] = []
    for rule in SUBJECT_CONTAINMENTS:
        if rule.subset not in subjects or rule.superset not in subjects:
            continue
        absent = tuple(sorted(subjects[rule.subset] - subjects[rule.superset]))
        if not absent:
            continue
        findings.append(
            _finding(
                "subject_set_disagreement",
                datasets=(rule.subset, rule.superset),
                detail=(
                    f"{len(absent)} subject(s) of {rule.subset} are absent from "
                    f"{rule.superset}: {rule.detail}"
                ),
                count=len(absent),
                items=absent,
                related_limitations=rule.related_limitations,
            )
        )
    return tuple(findings)


@dataclass(frozen=True, slots=True, kw_only=True)
class CrossCheckOutcome:
    """One cross-dataset check, and whether it actually ran.

    The falsifiable half of "this report covers missing / stale / duplicate / revised". A
    check that could not run reports `ran=False` with a reason, so that its silence is never
    read as a pass."""

    name: str
    datasets: tuple[str, ...]
    ran: bool
    skipped_reason: str | None = None
    finding_count: int = 0


@dataclass(frozen=True, slots=True, kw_only=True)
class DatasetHealth:
    """One dataset's verdict: its readiness, its freshness, and the counts behind them."""

    dataset: str
    readiness: DatasetReadiness
    freshness: FreshnessPolicy
    findings: tuple[HealthFinding, ...]
    years_requested: tuple[int, ...]
    event_age: timedelta | None
    """`as_of` minus the newest event instant, or `None` when nothing readable was found.

    Negative for a dataset published in advance, which is why it is a `timedelta` rather than
    a "days behind" integer that would invite a `max(0, ...)`."""
    fetch_age: timedelta | None
    """`as_of` minus the newest `fetched_at` of the coverage records read.

    The write clock, kept separate from the event clock on purpose: a backfill run today can
    produce a partition whose newest event is a month old, and for the event-driven datasets
    -- which have no schedule for an event-clock bound to be measured against -- this is the
    only freshness fact there is."""
    revised_row_count: int
    revision_labels: tuple[tuple[str, int], ...]

    @property
    def is_ready(self) -> bool:
        return self.readiness.is_ready


@dataclass(frozen=True, slots=True, kw_only=True)
class PanelHealthReport:
    """The whole verdict at one `as_of`: per dataset, across datasets, and what was not looked
    at.

    `limitations` is deliberately a sibling of `findings` and not merged into it. A structural
    boundary of a dataset and a defect of this fetch are different kinds of fact with different
    remedies -- one is disclosed, the other is fixed -- and a report that listed them together
    would teach its readers to skim both."""

    as_of: datetime
    datasets: tuple[DatasetHealth, ...]
    cross_dataset_findings: tuple[HealthFinding, ...]
    cross_checks: tuple[CrossCheckOutcome, ...]
    limitations: tuple[KnownLimitation, ...]

    @property
    def findings(self) -> tuple[HealthFinding, ...]:
        """Every finding, per-dataset first in the order the datasets were requested."""
        return (
            *(finding for health in self.datasets for finding in health.findings),
            *self.cross_dataset_findings,
        )

    @property
    def is_clean(self) -> bool:
        """Whether nothing blocking and nothing warning was found.

        Notices do not count: see this module's docstring for why revision and duplication
        counts are notices, and what a healthy financial partition would look like if they
        were not. `findings` can therefore be non-empty on a clean report, and a caller that
        wants "did the doctor say anything at all" must ask `findings`, not this."""
        return not any(finding.severity in BLOCKS_A_READ for finding in self.findings)

    @property
    def blocked_datasets(self) -> tuple[str, ...]:
        """The datasets a `V2-P1-013` gate would refuse, in request order."""
        return tuple(health.dataset for health in self.datasets if not health.is_ready)

    def codes(self) -> frozenset[str]:
        return frozenset(finding.code for finding in self.findings)

    def findings_with_code(self, code: str) -> tuple[HealthFinding, ...]:
        if code not in PANEL_HEALTH_CODES:
            raise PanelDoctorError(
                f"{code!r} is not one of the declared health codes {sorted(PANEL_HEALTH_CODES)}"
            )
        return tuple(finding for finding in self.findings if finding.code == code)

    def findings_in_category(self, category: str) -> tuple[HealthFinding, ...]:
        if category not in HEALTH_CATEGORIES:
            raise PanelDoctorError(
                f"{category!r} is not one of the declared categories {sorted(HEALTH_CATEGORIES)}"
            )
        return tuple(finding for finding in self.findings if finding.category == category)

    def dataset(self, name: str) -> DatasetHealth:
        for health in self.datasets:
            if health.dataset == name:
                return health
        raise PanelDoctorError(
            f"{name!r} was not one of the datasets this report covers "
            f"({[health.dataset for health in self.datasets]})"
        )


# --- building one dataset's health --------------------------------------------------------------

_INVALIDATES_COVERAGE: Final[frozenset[str]] = frozenset(
    {
        "partition_missing",
        "partition_file_missing",
        "partition_file_unreadable",
        "partition_row_count_mismatch",
        "coverage_missing",
        "coverage_stale",
    }
)
"""Readiness codes after which the stored coverage record does not describe what is on disk.

A dataset carrying any of these is withheld from everything read off its coverage record --
the cross-dataset composition check, and the `revised_rows` and `duplicate_versions` counts.
Its subject list and its label census are either absent or provably out of date, and reporting
either would manufacture a fact that is really just the stale record. One defect, one line: the
`coverage_stale` finding says the record no longer describes the partition, and the counts read
off that record are not then presented as measurements of the partition.

**The reach of this is the store's own write path plus two facts read off the file.**
`coverage_stale` is decided by comparing two catalog rows, so a partition replaced through
`PanelStore.write_partition` without a matching catalog write is caught; `partition_file_
missing` and `partition_file_unreadable` catch a deleted or truncated one; and
`partition_row_count_mismatch` catches an `mv` of a valid Parquet file holding a different
number of rows, which this note previously said "cannot be" caught because "nothing re-reads
the partition to corroborate the record". Something does now, once per requested year, off the
Parquet footer. What is still uncaught is a replacement of exactly the same length, or an edit
that changes values in place -- disclosed as `KNOWN_STORAGE_LIMITATIONS`'
`a_value_edited_in_place_leaves_the_census_intact` rather than left to be inferred from
here."""


def _requirement_for(
    dataset: str,
    *,
    as_of: datetime,
    years: tuple[int, ...],
    calendar: TradingCalendar | None,
    index_codes: Sequence[str],
    max_staleness: timedelta | None,
    date_timezone: str,
) -> tuple[ReadinessRequirement, str | None]:
    """The requirement this report puts to `dataset`, and why any check was dropped.

    Reuses `panel_ingest.py`'s builders wherever one exists, so the report asks each dataset
    the same question its own reader asks. The one thing this adds is a fallback: when a
    calendar-shaped requirement cannot be built -- no calendar was supplied, or the calendar
    does not cover a requested year -- the requirement is rebuilt with `required_dates`
    waived, so that the rest of the verdict (registration, files, coverage, subjects, fields,
    freshness) is still produced instead of the whole dataset dropping out of the report.
    """
    if dataset == TRADING_CALENDAR_DATASET:
        if calendar is not None:
            return (
                trading_calendar_requirement(exchange=calendar.exchange, years=years, as_of=as_of),
                None,
            )
        return (
            ReadinessRequirement(
                dataset=dataset,
                as_of=as_of,
                years=years,
                required_dates=None,
                required_subjects=None,
                required_fields=CALENDAR_PANEL_COLUMNS,
                max_staleness=max_staleness,
            ),
            "no calendar was supplied, so the exchange could not be required by name",
        )
    if dataset == STOCK_BASIC_DATASET:
        return (
            stock_universe_requirement(years=years, as_of=as_of, max_staleness=max_staleness),
            None,
        )
    if dataset == NAMECHANGE_DATASET:
        return name_history_requirement(years=years, as_of=as_of, max_staleness=max_staleness), None
    if dataset == ADJ_FACTOR_DATASET:
        return adjustment_requirement(years=years, as_of=as_of, max_staleness=max_staleness), None
    if dataset == SUSPENSION_DATASET:
        return suspension_requirement(years=years, as_of=as_of, max_staleness=max_staleness), None
    if dataset == INDUSTRY_MEMBERSHIP_DATASET:
        return (
            industry_membership_requirement(years=years, as_of=as_of, max_staleness=max_staleness),
            None,
        )
    if dataset == INDUSTRY_TREE_DATASET:
        return (
            industry_tree_requirement(years=years, as_of=as_of, max_staleness=max_staleness),
            None,
        )
    if dataset == INDEX_WEIGHT_DATASET:
        if len(index_codes) == 1:
            return (
                index_weight_requirement(
                    index_code=index_codes[0], years=years, as_of=as_of, max_staleness=max_staleness
                ),
                None,
            )
        return (
            ReadinessRequirement(
                dataset=dataset,
                as_of=as_of,
                years=years,
                required_dates=None,
                required_subjects=tuple(index_codes) or None,
                required_fields=INDEX_WEIGHT_PANEL_COLUMNS,
                max_staleness=max_staleness,
            ),
            None
            if index_codes
            else "no index code was named, so the index itself could not be required",
        )
    if dataset in FINANCIAL_STATEMENT_DATASETS:
        return (
            financial_statement_requirement(
                dataset=dataset, years=years, as_of=as_of, max_staleness=max_staleness
            ),
            None,
        )
    builder = {
        DAILY_DATASET: daily_requirement,
        DAILY_BASIC_DATASET: daily_basic_requirement,
        PRICE_LIMIT_DATASET: price_limit_requirement,
    }.get(dataset)
    if builder is None:
        raise PanelDoctorError(
            f"{dataset!r} is not a dataset this report knows how to require anything of "
            f"(known: {sorted(DATASET_CADENCE)})"
        )
    if calendar is not None:
        try:
            return (
                builder(
                    calendar,
                    years=years,
                    as_of=as_of,
                    max_staleness=max_staleness,
                    date_timezone=date_timezone,
                ),
                None,
            )
        except TradingCalendarError as error:
            note = f"required_dates could not be derived from the calendar: {error}"
    else:
        note = (
            "no trading calendar was supplied, so the sessions this dataset should hold "
            "could not be stated and date-hole detection did not run"
        )
    return (
        ReadinessRequirement(
            dataset=dataset,
            as_of=as_of,
            years=years,
            required_dates=None,
            required_subjects=None,
            required_fields=_PRICE_SHAPED_FIELDS[dataset],
            max_staleness=max_staleness,
        ),
        note,
    )


_PRICE_SHAPED_FIELDS: Final[Mapping[str, tuple[str, ...]]] = MappingProxyType(
    {
        DAILY_DATASET: DAILY_PANEL_COLUMNS,
        DAILY_BASIC_DATASET: DAILY_BASIC_PANEL_COLUMNS,
        PRICE_LIMIT_DATASET: PRICE_LIMIT_PANEL_COLUMNS,
    }
)


def _coverage_records(
    store: PanelStore, dataset: str, years: Sequence[int]
) -> tuple[PartitionCoverage, ...]:
    """The coverage records the catalog holds for `dataset` over `years`, skipping absent ones.

    The only guard is on the catalog file itself, which `read_coverage` would refuse to open.
    Everything else is already settled by the time this runs: `dataset_health` has called
    `assess_readiness` against the same catalog, so a malformed dataset name or an unreadable
    schema stamp has raised there, and a `(dataset, year)` with no row answers `None`.
    """
    if not store.catalog_path.exists():
        return ()
    return tuple(
        record
        for record in (store.read_coverage(dataset, year) for year in years)
        if record is not None
    )


def dataset_health(
    store: PanelStore,
    *,
    dataset: str,
    as_of: datetime,
    years: Sequence[int],
    calendar: TradingCalendar | None = None,
    index_codes: Sequence[str] = (),
    freshness: FreshnessPolicy | None = None,
    date_timezone: str = DEFAULT_DATE_TIMEZONE,
) -> DatasetHealth:
    """Assess one dataset: readiness against its own requirement, plus the catalog's counts.

    `revised_row_count` and `revision_labels` are always the catalog's own numbers, reported
    without a verdict. The two *findings* they would otherwise raise are withheld when
    readiness says the record no longer describes the partition (`_INVALIDATES_COVERAGE`),
    which is the same rule `_subject_check` applies to the subject list off the same record --
    "this partition holds two versions of some rows" is a claim about the partition, and it
    cannot be made from a record already known not to describe it.

    `event_after_as_of` is withheld under the same rule and for the same reason: it is read off
    `DatasetReadiness.last_event_time`, which is pooled from the coverage records readiness
    found usable, so on a partition whose record is stale it would be a claim about a write
    that is no longer there. It is exempted by **cadence** rather than by dataset name --
    `published_in_advance` is the declaration that a dataset's newest event is normally ahead of
    the reader, and `trade_cal` is its only member -- so a second such dataset gets the
    exemption by declaring its cadence rather than by being added to a list here. See
    `DOCTOR_ISSUE_CODES` for the measurement that motivated the code.

    It is withheld once more, and this one is `_rebuild_check`'s rule rather than
    `_subject_check`'s: **a partition readiness already refused as `not_yet_knowable` is not
    reported a second time here.** Both codes are about a row the reader should not be given,
    read off two different clocks, and on the three generated shapes that inject a row dated
    after the read (`financials.announced_after_the_as_of`,
    `index.publication_after_the_as_of`, `industry.reclassification_after_the_as_of`) both
    clocks move together -- so emitting both would double-count one defect in two vocabularies,
    which is exactly what `_rebuild_check` declines to do for a dataset readiness has already
    faulted. What is left is the case this code exists for and nothing else: a row that *was*
    knowable at `as_of`, so availability clears it, describing an event that has not happened."""
    policy = freshness or freshness_policy(dataset, calendar=calendar)
    requested = tuple(sorted(set(years)))
    requirement, note = _requirement_for(
        dataset,
        as_of=as_of,
        years=requested,
        calendar=calendar,
        index_codes=index_codes,
        max_staleness=policy.max_staleness,
        date_timezone=date_timezone,
    )
    readiness = store.assess_readiness(requirement)
    findings = list(findings_from_readiness(readiness))
    if note is not None:
        findings.append(
            _finding(
                "check_unavailable",
                datasets=(dataset,),
                detail=f"{dataset}: {note}",
            )
        )

    records = _coverage_records(store, dataset, requested)
    revised = sum(record.revised_row_count for record in records)
    labels: dict[str, int] = {}
    for record in records:
        for entry in record.revisions:
            labels[entry.label] = labels.get(entry.label, 0) + entry.row_count
    census = tuple(sorted(labels.items()))
    fetched = [record.fetched_at for record in records]
    describes_the_partition = not (
        {issue.code for issue in readiness.issues} & _INVALIDATES_COVERAGE
    )

    already_refused_on_the_other_clock = "not_yet_knowable" in {
        issue.code for issue in readiness.issues
    }
    if (
        describes_the_partition
        and not already_refused_on_the_other_clock
        and policy.cadence != "published_in_advance"
        and readiness.last_event_time is not None
        and readiness.last_event_time > as_of
    ):
        findings.append(
            _finding(
                "event_after_as_of",
                datasets=(dataset,),
                detail=(
                    f"{dataset} holds a row whose event instant is "
                    f"{readiness.last_event_time.isoformat()}, after the requested as_of "
                    f"{as_of.isoformat()}: the row was knowable by then but the event it "
                    f"describes had not happened. {dataset} publishes on the "
                    f"{policy.cadence} cadence, which carries no schedule ahead of itself, so "
                    "this is a row about the future rather than a publication in advance -- "
                    "and readiness cannot see it, because the only clock it compares against "
                    "as_of is availability"
                ),
                count=1,
                # The census' own newest event *date*, in the partition's declared timezone,
                # beside the instant the detail prints. A reader deciding whether a row is a
                # provider artefact or a real future-dated event needs the business date, and
                # deriving one from the instant here would be this module choosing a timezone
                # the coverage record already declares.
                dates=(() if readiness.last_event_date is None else (readiness.last_event_date,)),
            )
        )
    if revised and describes_the_partition:
        findings.append(
            _finding(
                "revised_rows",
                datasets=(dataset,),
                detail=(
                    f"{revised} stored row(s) of {dataset} carry a revision_time after the "
                    "instant they became available, so an earlier read of them answered "
                    "something else"
                ),
                count=revised,
            )
        )
    if len(census) > 1 and describes_the_partition:
        findings.append(
            _finding(
                "duplicate_versions",
                datasets=(dataset,),
                detail=(
                    f"{dataset} stores {len(census)} distinct revision labels "
                    f"({', '.join(f'{label}: {count}' for label, count in census)}), so more "
                    "than one version of some rows is present; the census counts them and "
                    "does not say which is current"
                ),
                count=sum(count for _, count in census),
                items=tuple(label for label, _ in census),
                related_limitations=tuple(
                    item.code
                    for item in known_limitations((dataset,))
                    if item.code
                    in ("update_flag_does_not_say_which_version_is_current", "no_revision_history")
                ),
            )
        )

    return DatasetHealth(
        dataset=dataset,
        readiness=readiness,
        freshness=policy,
        findings=tuple(findings),
        years_requested=requested,
        event_age=(
            as_of - readiness.last_event_time if readiness.last_event_time is not None else None
        ),
        fetch_age=(as_of - max(fetched)) if fetched else None,
        revised_row_count=revised,
        revision_labels=census,
    )


# --- the cross-dataset checks ---------------------------------------------------------------------


def _subject_check(
    store: PanelStore, healths: Sequence[DatasetHealth]
) -> tuple[tuple[HealthFinding, ...], CrossCheckOutcome]:
    usable: dict[str, frozenset[str]] = {}
    for health in healths:
        if {issue.code for issue in health.readiness.issues} & _INVALIDATES_COVERAGE:
            continue
        records = _coverage_records(store, health.dataset, health.years_requested)
        if not records:
            continue
        usable[health.dataset] = frozenset(
            subject for record in records for subject in record.subjects
        )
    checkable = [
        rule for rule in SUBJECT_CONTAINMENTS if rule.subset in usable and rule.superset in usable
    ]
    if not checkable:
        return (), CrossCheckOutcome(
            name="subject_containment",
            datasets=tuple(sorted(usable)),
            ran=False,
            skipped_reason=(
                "no declared containment had both of its datasets present with a coverage "
                "record that still describes what is on disk"
            ),
        )
    findings = subject_containment_findings(usable)
    involved = {rule.subset for rule in checkable} | {rule.superset for rule in checkable}
    return findings, CrossCheckOutcome(
        name="subject_containment",
        datasets=tuple(sorted(involved)),
        ran=True,
        finding_count=len(findings),
    )


_LOAD_FAILURES: Final[tuple[type[Exception], ...]] = (
    PanelStorageError,
    PanelBatchError,
    PriceDataError,
    AdjustmentError,
    SuspensionError,
    StockUniverseError,
    IndexMembershipError,
    FinancialStatementError,
    TradingCalendarError,
)
"""Every failure a cross-check's own loads can raise, caught so the check can report that it
did not run instead of taking the whole report down with it.

Deliberately a list of named domain errors rather than `Exception`: a `TypeError` out of this
module's own code is a bug in the doctor and must not be laundered into "a check could not
run"."""


def _close_check(
    store: PanelStore,
    *,
    days: Sequence[date],
    calendar: TradingCalendar,
    as_of: datetime,
    bounds: Mapping[str, timedelta | None],
) -> tuple[tuple[HealthFinding, ...], CrossCheckOutcome]:
    findings: list[HealthFinding] = []
    try:
        for day in days:
            bars = load_daily_bars(
                store,
                day=day,
                calendar=calendar,
                as_of=as_of,
                max_staleness=bounds.get(DAILY_DATASET),
            )
            valuations = load_daily_valuations(
                store,
                day=day,
                calendar=calendar,
                as_of=as_of,
                max_staleness=bounds.get(DAILY_BASIC_DATASET),
            )
            disagreements = close_disagreements(close_index(bars), close_index(valuations))
            if disagreements:
                findings.append(
                    _finding(
                        "close_disagreement",
                        datasets=(DAILY_DATASET, DAILY_BASIC_DATASET),
                        detail=(
                            f"{len(disagreements)} security(ies) on {day.isoformat()} have a "
                            "daily_basic close that daily does not corroborate; the two "
                            "datasets republish the same number, so they cannot both be right"
                        ),
                        count=len(disagreements),
                        dates=(day,),
                        items=tuple(sorted(item.ts_code for item in disagreements)),
                    )
                )
    except _LOAD_FAILURES as error:
        return _unavailable("close_agreement", (DAILY_DATASET, DAILY_BASIC_DATASET), error)
    return tuple(findings), CrossCheckOutcome(
        name="close_agreement",
        datasets=(DAILY_DATASET, DAILY_BASIC_DATASET),
        ran=True,
        finding_count=len(findings),
    )


def _unpriced_check(
    store: PanelStore,
    *,
    days: Sequence[date],
    calendar: TradingCalendar,
    as_of: datetime,
    years: Mapping[str, tuple[int, ...]],
    bounds: Mapping[str, timedelta | None],
) -> tuple[tuple[HealthFinding, ...], CrossCheckOutcome]:
    """Which listed securities had no bar, and whether a halt accounts for each one.

    ## Why every finding here is a `warning` and none is a `notice`

    An earlier cut graded this by the share of the day's listed names that bars and halts
    together cover, against `price_limits.MIN_EXPLAINED_SESSION_SHARE`. That constant does not
    mean what this ratio means. It is calibrated -- over 8,690 sessions -- as
    `(bars + halts) / the +/-20-session rolling median of the same quantity`: a measure of
    whether *this* session is thin **relative to its neighbours**, which is why it can exceed
    1.0 and why a market that grew inside a year does not trip it. The ratio here is
    `(priced + halted) / names listed that day`, an absolute coverage figure bounded by 1.0.
    Two different quantities cannot share one threshold, and reusing it graded three silently
    unpriced names out of twenty -- 15% of a cross section, gone with nothing to account for
    it -- as a `notice`, on a report that then called itself clean.

    So the threshold is gone rather than re-derived, and the argument for having none is the
    write-time guard's own: `panel_ingest._refuse_unexplained_thin_sessions` already refuses
    the sessions that are thin against their neighbours. What survives that guard and still
    has an unexplained missing bar is, by construction, a gap the relative measure could not
    see -- a systematic one, present on the neighbours too. That is precisely the shape this
    report exists to name, so it is a `warning` at any share, and `share` stays in the detail
    as the reported figure rather than as a verdict.
    """
    involved = (DAILY_DATASET, ADJ_FACTOR_DATASET, STOCK_BASIC_DATASET, SUSPENSION_DATASET)
    findings: list[HealthFinding] = []
    try:
        universe = load_stock_universe(
            store,
            years=years[STOCK_BASIC_DATASET],
            as_of=as_of,
            max_staleness=bounds.get(STOCK_BASIC_DATASET),
        )
        histories = load_adjustment_histories(
            store,
            years=years[ADJ_FACTOR_DATASET],
            as_of=as_of,
            max_staleness=bounds.get(ADJ_FACTOR_DATASET),
        )
        halts = load_suspensions(
            store,
            years=years[SUSPENSION_DATASET],
            as_of=as_of,
            max_staleness=bounds.get(SUSPENSION_DATASET),
        )
        for day in days:
            bars = load_daily_bars(
                store,
                day=day,
                calendar=calendar,
                as_of=as_of,
                max_staleness=bounds.get(DAILY_DATASET),
            )
            cross_section = priced_cross_section(
                bars, histories, universe=universe, calendar=calendar, day=day
            )
            halted_on_day = halts.get(day)
            explained = explain_unpriced(
                cross_section,
                halted_on_day if halted_on_day is not None else build_suspension_day(day, ()),
            )
            if not explained.unexplained:
                continue
            listed = len(cross_section.priced) + len(cross_section.unpriced)
            share = (len(cross_section.priced) + len(explained.halted)) / listed if listed else 1.0
            findings.append(
                _finding(
                    "unexplained_unpriced",
                    datasets=(DAILY_DATASET, SUSPENSION_DATASET),
                    detail=(
                        f"{len(explained.unexplained)} listed security(ies) had no bar on "
                        f"{day.isoformat()} and no whole-day halt to account for it; bars and "
                        f"halts together cover {share:.1%} of the {listed} name(s) listed on "
                        "that session"
                    ),
                    count=len(explained.unexplained),
                    dates=(day,),
                    items=explained.unexplained,
                    related_limitations=(
                        "intraday_halts_are_unmarked_before_2015",
                        "a_partial_cross_section_is_invisible_without_suspend_d",
                    ),
                )
            )
    except _LOAD_FAILURES as error:
        return _unavailable("unpriced_explained", involved, error)
    return tuple(findings), CrossCheckOutcome(
        name="unpriced_explained", datasets=involved, ran=True, finding_count=len(findings)
    )


def _return_path_check(
    store: PanelStore,
    *,
    days: Sequence[date],
    calendar: TradingCalendar,
    as_of: datetime,
    years: Mapping[str, tuple[int, ...]],
    bounds: Mapping[str, timedelta | None],
) -> tuple[tuple[HealthFinding, ...], CrossCheckOutcome]:
    """Recompute each session's return on both correct paths and report where they part.

    `session_returns` **raises** when the published `pre_close` and the one implied by the
    factor series disagree past the two rows' own publication precision, and also when a bar's
    own `pct_chg` does not corroborate the `close / pre_close` it publishes -- the second being
    the only one of the two that reads `close` at all. Either refusal is right for a reader and
    wrong for a health check, whose job is to survive the panel it is inspecting -- so both are
    caught per security and turned into a line."""
    findings: list[HealthFinding] = []
    try:
        histories = load_adjustment_histories(
            store,
            years=years[ADJ_FACTOR_DATASET],
            as_of=as_of,
            max_staleness=bounds.get(ADJ_FACTOR_DATASET),
        )
        for day in days:
            previous_day = calendar.previous_trading_day(day)
            bars = load_daily_bars(
                store,
                day=day,
                calendar=calendar,
                as_of=as_of,
                max_staleness=bounds.get(DAILY_DATASET),
            )
            previous = load_daily_bars(
                store,
                day=previous_day,
                calendar=calendar,
                as_of=as_of,
                max_staleness=bounds.get(DAILY_DATASET),
            )
            for ts_code in sorted(bars):
                earlier = previous.get(ts_code)
                factors = histories.get(ts_code)
                if earlier is None or factors is None:
                    continue
                try:
                    session_returns(
                        bars[ts_code],
                        previous_close=earlier.close,
                        previous_day=previous_day,
                        factors=factors,
                    )
                except (PriceDataError, AdjustmentError) as error:
                    findings.append(
                        _finding(
                            "return_path_disagreement",
                            datasets=(DAILY_DATASET, ADJ_FACTOR_DATASET),
                            detail=(
                                f"{ts_code} on {day.isoformat()}: the published return and the "
                                f"factor-adjusted one cannot both be computed -- {error}"
                            ),
                            count=1,
                            dates=(day,),
                            items=(ts_code,),
                        )
                    )
    except _LOAD_FAILURES as error:
        return _unavailable("return_paths", (DAILY_DATASET, ADJ_FACTOR_DATASET), error)
    return tuple(findings), CrossCheckOutcome(
        name="return_paths",
        datasets=(DAILY_DATASET, ADJ_FACTOR_DATASET),
        ran=True,
        finding_count=len(findings),
    )


def _ambiguity_check(
    store: PanelStore,
    *,
    datasets: Sequence[str],
    as_of: datetime,
    years: Mapping[str, tuple[int, ...]],
    bounds: Mapping[str, timedelta | None],
) -> tuple[tuple[HealthFinding, ...], CrossCheckOutcome]:
    """Count the filings of each statement dataset that carry more than one surviving version.

    Every dataset in `datasets` is visited, and each is read over **its own** announcement
    years. Four statement datasets are four separate endpoints with four separate partition
    sets, and `years_by_dataset` exists precisely so a caller can ask `cashflow` about a range
    `income` was not fetched for."""
    findings: list[HealthFinding] = []
    try:
        for dataset in datasets:
            histories = load_statement_histories(
                store,
                dataset=dataset,
                years=years[dataset],
                as_of=as_of,
                max_staleness=bounds.get(dataset),
            )
            summary = financial_ambiguity_report(dataset=dataset, histories=histories)
            if summary.is_clean:
                continue
            worst = sorted(
                summary.ambiguous_field_reads.items(), key=lambda item: (-item[1], item[0])
            )
            findings.append(
                _finding(
                    "ambiguous_filing",
                    datasets=(dataset,),
                    detail=(
                        f"{summary.ambiguous_filings} of {summary.filings} stored "
                        f"{dataset} filing(s) carry more than one surviving version, so a read "
                        f"of a disagreeing field is refused rather than answered; "
                        f"{summary.collapsed_versions} further version(s) collapsed because "
                        "they said the same thing"
                    ),
                    count=summary.ambiguous_filings,
                    items=tuple(f"{name}:{count}" for name, count in worst if count),
                    related_limitations=(
                        "a_correction_carries_no_instant_of_its_own",
                        "update_flag_does_not_say_which_version_is_current",
                    ),
                )
            )
    except _LOAD_FAILURES as error:
        return _unavailable("statement_ambiguity", tuple(datasets), error)
    return tuple(findings), CrossCheckOutcome(
        name="statement_ambiguity",
        datasets=tuple(datasets),
        ran=True,
        finding_count=len(findings),
    )


class _YearScopedLoader(Protocol):
    def __call__(
        self,
        store: PanelStore,
        *,
        years: Sequence[int],
        as_of: datetime,
        max_staleness: timedelta | None,
    ) -> object: ...


REBUILDABLE_DATASETS: Final[Mapping[str, _YearScopedLoader]] = MappingProxyType(
    {
        NAMECHANGE_DATASET: load_name_histories,
        ADJ_FACTOR_DATASET: load_adjustment_histories,
        SUSPENSION_DATASET: load_suspensions,
        INDUSTRY_MEMBERSHIP_DATASET: load_industry_histories,
        INDUSTRY_TREE_DATASET: load_industry_trees,
    }
)
"""The datasets `_rebuild_check` can rebuild, and the loader that rebuilds each.

**The year-scoped loaders whose reconstruction is defined on whatever years the caller named.**
Every entry has the same signature -- `(store, *, years, as_of, max_staleness)` -- and, more to
the point, every entry's builder assembles from the rows it is handed: a halt day is grouped
from that day's rows, a factor series and a rename corpus from the records present. None of
them refuses because a record it wants sits in a year the request did not ask for. That is the
property this check needs, because it must not manufacture a finding out of the caller's choice
of `--year`.

**`stock_basic` is deliberately absent, and it is the reason this table is a table.** Its
partitions are *lifecycle* years -- `cli._LIFECYCLE_YEAR_TARGETS`' sole member -- so a security
listed in 2009 and delisted in 2026 has its listing row in the 2009 partition and its delisting
row in the 2026 one. `build_stock_universe` refuses a delisting with no listing, correctly, as
a partial read. So `panel doctor --dataset stock_basic --year 2026` would have reported
`domain_rebuild_refused` on a perfectly sound registry: measured on a real panel, 21 securities,
`000004.SZ` first. That is the check being wrong about the panel rather than the panel being
wrong, which is the one failure mode a fail-closed report cannot afford. `load_stock_universe`
already states that it wants the store's whole lifecycle range; a rebuild check that supplied
less would be asking a question the caller did not.

The day-scoped loaders (`load_daily_bars`, `load_daily_valuations`, `load_price_limits`) are
absent for a different reason: they cannot run without naming a session, `--session` is already
how a caller names one, and `_close_check`, `_unpriced_check` and `_return_path_check` exercise
all three on the sessions named. `load_index_membership` is absent for the same reason with
`--index-code` in place of `--session`, and `load_statement_histories` takes a `dataset`
argument and is exercised by `_ambiguity_check`."""


def _rebuild_check(
    store: PanelStore,
    *,
    healths: Sequence[DatasetHealth],
    as_of: datetime,
    years: Mapping[str, tuple[int, ...]],
    bounds: Mapping[str, timedelta | None],
) -> tuple[tuple[HealthFinding, ...], CrossCheckOutcome]:
    """Rebuild every ready partition into its domain type, and report the ones that refuse.

    ## The dimension readiness cannot have

    `evaluate_readiness` reads the catalog: which files exist, which subjects and fields and
    dates they cover, how stale they are. Not one of those can see two rows that contradict
    each other, so a partition can satisfy every readiness dimension and still be unreadable.
    That is not hypothetical -- it is `V2-P1-013`'s own promise ("a failed dataset explicitly
    blocks downstream") failing one layer up, measured on a real 2026 `suspend_d` partition
    that `panel doctor` called `READY` (2,293 rows) and `data-check` `CLEARED`, and that
    `load_suspensions` refused. `data-check`'s whole deliverable is its exit code: a caller who
    ran it, was cleared, and then read had done everything the design asks and still crashed.

    ## Only datasets readiness already called ready

    A dataset readiness has already faulted is skipped, not rebuilt. Its loader would raise for
    the reason readiness just reported, and a second finding saying the same thing in a
    different vocabulary would double-count a single defect -- and, worse, would attach the
    *rebuild* code to a partition that is merely absent. So this check answers exactly one
    question, the one nothing else asks: **of the partitions we are about to call readable, do
    they read?**

    ## The two failure modes are kept apart

    A domain reconstruction that refuses (`SuspensionError` and its eight siblings) is a
    verdict about the rows -- `domain_rebuild_refused`, a `warning`. A `PanelStorageError` is
    the store declining to serve them at all, which after a ready verdict means something moved
    under the report between the census and the read; the check did not get to run, so that is
    `check_unavailable`. Distinguishing them matters because only the first is a statement
    about the data, and `check_unavailable` already means "I could not look".
    """
    subjects = tuple(
        health.dataset
        for health in healths
        if health.dataset in REBUILDABLE_DATASETS and health.is_ready
    )
    findings: list[HealthFinding] = []
    for dataset in subjects:
        try:
            REBUILDABLE_DATASETS[dataset](
                store,
                years=years[dataset],
                as_of=as_of,
                max_staleness=bounds.get(dataset),
            )
        except PanelStorageError as error:
            return _unavailable("domain_rebuild", subjects, error)
        except _LOAD_FAILURES as error:
            findings.append(
                _finding(
                    "domain_rebuild_refused",
                    datasets=(dataset,),
                    detail=(
                        f"{dataset} passed every readiness dimension and then refused to "
                        f"rebuild: {type(error).__name__}: {error}. The catalog census cannot "
                        "see a contradiction between two rows, so this partition would have "
                        "been cleared for a read that raises"
                    ),
                    count=1,
                    items=(dataset,),
                )
            )
    return tuple(findings), CrossCheckOutcome(
        name="domain_rebuild",
        datasets=subjects,
        ran=True,
        finding_count=len(findings),
    )


CALENDAR_LOOKAHEAD_CHECK: Final[str] = "calendar_lookahead"
"""The name `_calendar_lookahead_check` records itself under in `cross_checks`.

A constant rather than a literal at its two emission sites, because the name is matched
elsewhere by *absence*: `panel_gate.SESSION_SCOPED_CROSS_CHECKS` is the set of checks that
open a named session and therefore corroborate a waived `required_dates`, and this one opens
no session, compares no two datasets and corroborates nothing about coverage. Adding it there
would make a calendar the gate's evidence that a price partition's sessions are all present."""


def calendar_lookahead_findings(calendar: TradingCalendar) -> tuple[HealthFinding, ...]:
    """The proven look-ahead instances `calendar`'s own horizon covers, as findings.

    Public and pure for `subject_containment_findings`' reason: the rule is decidable from one
    argument and no store, so it is testable as a rule (`tests/unit/test_panel_doctor_rules.py`)
    while `_calendar_lookahead_check` below owns the "was there a calendar at all" half that
    only a report can answer.

    Empty is **not** a clean bill. `KNOWN_CALENDAR_LOOKAHEAD` holds three reproduced instances
    of a defect that recurs with every mid-year amendment to the holiday schedule, and
    `trade_cal` carries nothing that would let any code here enumerate the rest. The finding
    says so in its own detail, and the limitation keeps saying what the list is not.
    """
    covered = calendar.known_lookahead()
    if not covered:
        return ()
    return (
        _finding(
            "calendar_lookahead_in_horizon",
            datasets=(TRADING_CALENDAR_DATASET,),
            detail=(
                f"this calendar's horizon ({calendar.horizon.first_date.isoformat()}.."
                f"{calendar.horizon.last_date.isoformat()}) covers "
                f"{len(covered)} date(s) whose stored availability instant this repository "
                "overstates, so a point-in-time claim made over this window is known to be "
                "contaminated on them: "
                + "; ".join(
                    f"{instance.calendar_date.isoformat()} answered from "
                    f"{instance.claimed_available_from.isoformat()} but announced "
                    f"{instance.announced_on.isoformat()}, "
                    f"{instance.lookahead_days} days of look-ahead"
                    for instance in covered
                )
                + ". This is an inherent limitation of trade_cal and not a defect of this "
                "fetch, and the list is the reproduced instances rather than an enumeration"
            ),
            count=len(covered),
            dates=tuple(instance.calendar_date for instance in covered),
            related_limitations=(
                "the_published_schedule_can_be_amended_after_it_becomes_answerable",
            ),
        ),
    )


def _calendar_lookahead_check(
    calendar: TradingCalendar | None,
) -> tuple[tuple[HealthFinding, ...], CrossCheckOutcome]:
    """Report the proven calendar look-ahead instances this panel's own window covers.

    ## The check that had no caller

    `TradingCalendar.known_lookahead()` says in its docstring that it is "the interface
    `V2-P1-013`'s dependency gate needs", and until this function it had no caller anywhere in
    `src/` -- only tests. The consequence was measured on a real 2015 partition (365 rows,
    built from the same live endpoint the rest of the calendar contract is): `panel doctor
    --json` answered `is_clean: true, findings: []`, and the three dates appeared only inside
    `limitations`, in a sentence that reads identically whether the panel covers them or not.
    A disclosure that cannot tell those two panels apart is prose, not a check, which is
    exactly what P2's product acceptance called it.

    ## Why it is a cross-check and not a per-dataset finding

    The contamination is not `trade_cal`'s alone. Three datasets' `required_dates` are derived
    from this calendar (`daily`, `daily_basic`, `stk_limit`), so a window covering an amended
    date is a window in which those datasets' hole detection was decided against a session list
    this repository claims to have known earlier than it did -- whether or not `trade_cal` was
    among the datasets the request named. And a check with no calendar has to be visibly
    *unrun* rather than silently clean, which is what `cross_checks` is for: "silence from a
    check that never ran is otherwise indistinguishable from silence from one that passed".

    ## Severity

    `notice`, argued in full under `HEALTH_CODE_SEVERITY`. The short form: this is an inherent
    limitation of `trade_cal` rather than a defect of this fetch, it has no remedy on the read
    side, and a `warning` would permanently refuse every panel reaching 2015 or 2020.

    An empty result is **not** a clean bill. `KNOWN_CALENDAR_LOOKAHEAD` is three reproduced
    instances of a defect that recurs with every mid-year amendment to the holiday schedule,
    and `trade_cal` carries nothing that would let any code here enumerate the rest -- so the
    outcome below records that the check ran over the horizon it was given, and the limitation
    keeps saying what the list is not.
    """
    if calendar is None:
        return (), CrossCheckOutcome(
            name=CALENDAR_LOOKAHEAD_CHECK,
            datasets=(),
            ran=False,
            skipped_reason=(
                "no calendar was supplied, so there was no horizon to compare the proven "
                "look-ahead instances against"
            ),
        )
    findings = calendar_lookahead_findings(calendar)
    return findings, CrossCheckOutcome(
        name=CALENDAR_LOOKAHEAD_CHECK,
        datasets=(TRADING_CALENDAR_DATASET,),
        ran=True,
        finding_count=len(findings),
    )


def _unavailable(
    name: str, datasets: tuple[str, ...], error: Exception
) -> tuple[tuple[HealthFinding, ...], CrossCheckOutcome]:
    reason = f"{type(error).__name__}: {error}"
    finding = _finding(
        "check_unavailable",
        datasets=datasets,
        detail=f"the {name} cross-check could not run: {reason}",
    )
    return (finding,), CrossCheckOutcome(
        name=name, datasets=datasets, ran=False, skipped_reason=reason, finding_count=1
    )


def panel_health_report(
    store: PanelStore,
    *,
    as_of: datetime,
    datasets: Sequence[str],
    years: Sequence[int],
    calendar: TradingCalendar | None = None,
    index_codes: Sequence[str] = (),
    cross_section_days: Sequence[date] = (),
    years_by_dataset: Mapping[str, Sequence[int]] | None = None,
    freshness_overrides: Mapping[str, timedelta | None] | None = None,
    date_timezone: str = DEFAULT_DATE_TIMEZONE,
) -> PanelHealthReport:
    """Assess `datasets` at `as_of` and report everything that is wrong with them.

    `years` is what every dataset is asked about unless `years_by_dataset` overrides it. It is
    the caller's assertion of what *should* be there, not a reading of what is: passing
    `store.registered_years(...)` would make `partition_missing` unreachable by construction,
    which is the fail-open `load_stock_universe`'s docstring names for the same reason.

    An override in `years_by_dataset` follows the dataset **everywhere**, including into the
    cross-dataset checks -- `_unpriced_check` reads `suspend_d` over `suspend_d`'s years and
    `stock_basic` over `stock_basic`'s, not over `daily`'s. The resolved mapping is built once,
    below, and passed down; a cross-check taking a bare year tuple would silently read one
    dataset through another's window, and the resulting "no halt explains this missing bar"
    would be an artefact of the report rather than a fact about the panel.

    `cross_section_days` names the sessions the day-level cross-checks run on. They are not
    inferred, because "check every session" is a whole-corpus scan and "check the last one" is
    a guess about what the caller cares about.

    `freshness_overrides` replaces a dataset's derived bound; `None` for a dataset waives its
    freshness check on the record rather than removing the entry.

    Raises `PanelDoctorError` for a dataset with no declared cadence. Everything else that can
    go wrong -- a blocked dataset, a damaged partition, a calendar that does not reach a
    requested year, a cross-check whose inputs will not load -- becomes a finding, because a
    health report that raises on an unhealthy panel is a health report that is never there
    when it is needed.
    """
    requested = tuple(dict.fromkeys(datasets))
    per_dataset_years = dict(years_by_dataset or {})
    overrides = dict(freshness_overrides or {})
    years_for: dict[str, tuple[int, ...]] = {
        name: tuple(per_dataset_years.get(name, years)) for name in requested
    }

    healths: list[DatasetHealth] = []
    for name in requested:
        policy = freshness_policy(name, calendar=calendar)
        if name in overrides:
            policy = replace(
                policy,
                max_staleness=overrides[name],
                basis=f"stated by the caller, replacing: {policy.basis}",
            )
        healths.append(
            dataset_health(
                store,
                dataset=name,
                as_of=as_of,
                years=years_for[name],
                calendar=calendar,
                index_codes=index_codes,
                freshness=policy,
                date_timezone=date_timezone,
            )
        )

    bounds = {health.dataset: health.freshness.max_staleness for health in healths}
    cross_findings: list[HealthFinding] = []
    checks: list[CrossCheckOutcome] = []

    findings, outcome = _calendar_lookahead_check(calendar)
    cross_findings.extend(findings)
    checks.append(outcome)

    findings, outcome = _subject_check(store, healths)
    cross_findings.extend(findings)
    checks.append(outcome)

    findings, outcome = _rebuild_check(
        store, healths=healths, as_of=as_of, years=years_for, bounds=bounds
    )
    cross_findings.extend(findings)
    checks.append(outcome)

    in_scope = set(requested)
    days = tuple(cross_section_days)

    if days and calendar is not None and {DAILY_DATASET, DAILY_BASIC_DATASET} <= in_scope:
        findings, outcome = _close_check(
            store, days=days, calendar=calendar, as_of=as_of, bounds=bounds
        )
        cross_findings.extend(findings)
        checks.append(outcome)

    if (
        days
        and calendar is not None
        and {DAILY_DATASET, ADJ_FACTOR_DATASET, STOCK_BASIC_DATASET, SUSPENSION_DATASET} <= in_scope
    ):
        findings, outcome = _unpriced_check(
            store,
            days=days,
            calendar=calendar,
            as_of=as_of,
            years=years_for,
            bounds=bounds,
        )
        cross_findings.extend(findings)
        checks.append(outcome)

    if days and calendar is not None and {DAILY_DATASET, ADJ_FACTOR_DATASET} <= in_scope:
        findings, outcome = _return_path_check(
            store,
            days=days,
            calendar=calendar,
            as_of=as_of,
            years=years_for,
            bounds=bounds,
        )
        cross_findings.extend(findings)
        checks.append(outcome)

    statement_datasets = tuple(name for name in requested if name in FINANCIAL_STATEMENT_DATASETS)
    if statement_datasets:
        findings, outcome = _ambiguity_check(
            store,
            datasets=statement_datasets,
            as_of=as_of,
            years=years_for,
            bounds=bounds,
        )
        cross_findings.extend(findings)
        checks.append(outcome)

    return PanelHealthReport(
        as_of=as_of,
        datasets=tuple(healths),
        cross_dataset_findings=tuple(cross_findings),
        cross_checks=tuple(checks),
        limitations=(*known_limitations(requested), *storage_limitations()),
    )
