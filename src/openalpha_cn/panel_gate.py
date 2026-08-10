"""The fail-closed dependency gate (`V2-P1-013`): may this request read this panel?

The roadmap's acceptance is one sentence -- "assert blocking, not an empty success" -- and it
names the failure mode this repository keeps producing rather than a feature. Five instances
are on the record: stale coverage that reported `ready` with `issues == []` (`V2-P1-003`'s
review), a factor partition missing a session that reported `ready` with `issues == []` while
`adjusted_return` answered `-0.530973%` instead of `+2.742251%` (`V2-P1-006`'s), a thin cross
section that reported `ready` while 92.5% of it silently fell into `unpriced` (`V2-P1-008`'s),
and a session missing 15% of the listed names with nothing to account for it that the health
report called `is_clean` (`V2-P1-012`'s). In every one of them nothing raised, nothing was
`None`, and a downstream got a plausible number or an empty collection.

So this module's job is not "add an if". It is to make the empty success **unavailable**:

1. **The verdict is not a collection.** `DependencyClearance` refuses `bool()`, `len()` and
   iteration outright -- see the class docstring for why it refuses them on a *cleared*
   clearance too -- and `cleared` raises on a blocked one, exactly as `PanelReadOutcome.rows`
   does one layer down. The merged shape lives under `cleared_or_none`, a name that says what
   it is.
2. **Blocking is a table, not a judgement per site.** `GATE_CODE_BLOCKS` gives a verdict for
   every one of `PANEL_HEALTH_CODES`, so a code added upstream fails a test here rather than
   being waved through, and a code quietly demoted is a diff against a literal.
3. **A clearance is all or nothing.** A request naming five datasets of which three are sound
   is refused, because a partial clearance is the empty success itself: the downstream reads
   what it was given and produces a result missing the rest.

## What blocks, and the one argument that settles `warning`

`GATE_CODE_BLOCKS` blocks exactly the severities `PanelHealthReport.is_clean` counts,
`BLOCKS_A_READ` -- every `blocking` code and every `warning` code, and no `notice`. The two
tables are stated separately and asserted to agree rather than one being derived from the
other, so that a future demotion in `HEALTH_CODE_SEVERITY` has to be made twice and is visible
in one place.

**Warnings must block, and `V2-P1-006`'s Critical is the proof.** A factor partition missing
the row for an ex-rights session is `ready` with no issues, because `adjustment_requirement`
waives `required_dates` -- the stored series is a compressed step function with no per-session
expectation to be short of. Nothing in `evaluate_readiness`'s twelve codes can see that hole.
The only thing that can is `return_path_disagreement`, a `warning`: it recomputes the session's
return on both correct paths and finds that a published `pre_close` of 10.94 and the 11.30 the
surviving factors imply cannot both be true. A gate that blocked on `blocking` alone would
clear that panel and let `-0.530973%` through, which is the exact defect this issue exists to
prevent. `check_unavailable` carries the same weight from the other direction: "I could not
look" must never be read as "I looked and it was fine".

**Notices must not block, and the measurement is why.** `V2-P1-011` measured 81.7% of
`fina_indicator`'s keys carrying more than one row, and, on a real 53-security corpus driven
end to end, `ambiguous_filing` fires on 8.15% of `income`'s filings, 1.29% of
`balancesheet`'s, 15.80% of `cashflow`'s and 13.70% of `fina_indicator`'s
(`domain/financial_statements.py`). A gate that refused those would refuse every real financial
panel, be switched off, and protect nothing. Each of the three is also already refused *where it
is decidable*, and far more narrowly: `financial_ambiguity_report` refuses a read of the
individual *field* the surviving versions disagree about, which costs 0.19% to 3.16% of field
reads rather than a whole dataset. The notices ride on the clearance so a cleared caller still
sees them.

## "Daily" scopes what counts as failure, not whose failure counts

The roadmap says "a failed **daily** dataset explicitly blocks downstream". Read as "only
daily datasets' failures block", that is wrong and provably so: `stock_basic` is
`event_driven`, and a request for a cross section with the registry missing is `V2-P1-008`'s
92.5%-unpriced shape; `trade_cal` is `published_in_advance`, and without it `required_dates`
is waived on all three price datasets, so the daily blocks themselves stop being sound. This
gate therefore blocks on the failure of **any** dataset the request named.

Cadence still does the work the sentence points at, one level down: `panel_doctor`'s
`freshness_policy` derives the staleness bound *per cadence*, so `stale` already means "behind
for its own publication schedule". The same absolute age that blocks `adj_factor` (bound: the
exchange's longest closure plus a day) clears `income` (bound: the 182 days between the Q3 and
annual disclosure deadlines).

## `checks_waived`: neither a pass nor a blanket refusal

`DatasetReadiness.checks_waived` records the checks a requirement switched off. Of the fifteen
datasets `DATASET_CADENCE` declares, **twelve waive `required_dates`** -- measured, not
estimated -- so a gate that blocked on a waiver would refuse twelve datasets permanently.
Clearing on one, though, makes the gate weaker than it looks, which is how `V2-P1-006`'s
Critical happened.

The resolution is that neither horn is the question. What matters is whether *anything in this
request's scope* asked the question the waiver dropped. Exactly one dataset is both on the
`daily` cadence -- the only cadence with a per-session expectation at all -- and waives
`required_dates`: `adj_factor`. (`panel_doctor`'s own docstring names it as the gap; the
fifteen-dataset measurement is pinned in `tests/integration/panel/test_panel_gate.py`.) For
such a dataset the gate asks whether a **session-scoped cross-check actually ran over it**:
`close_agreement`, `unpriced_explained` or `return_paths`, the three `panel_doctor` checks that
open a session rather than comparing catalog records. If one did, the hole that would have
mattered is reachable and the dataset clears; if none did, the gate refuses with
`unverified_daily_coverage` rather than clearing on registration, files, fields and freshness
-- which is the precise set of checks the Task 29 panel passed while carrying its hole.

The residue is named rather than hidden: a hole in `adj_factor` on a session the factor did
**not** step across changes no answer -- `factor_on` carries the previous step forward, which
is what a step function means -- and the gate clears it. Both directions are pinned.

## Why this is a top-level module and not `panel/gate.py`

`tests/unit/test_import_layering.py` pins `openalpha_cn.panel` as importing no sibling
subpackage at all, and this module must import `domain` (for `TradingCalendar`) as well as
`panel` and `panel_doctor`. `panel_ingest.py` and `panel_doctor.py` are the precedents and the
reasoning is theirs: the seam sits *above* the package it must not be inside, so
`openalpha_cn.panel`'s real import closure is unchanged by this module's existence -- nothing
moved out of `panel/` and `panel/` gained no edge. The distinction the layering rule is about
(a neutral module that lets a forbidden runtime dependency keep existing while the metric stops
seeing it) does not arise, because the dependency runs `panel_gate -> panel_doctor -> panel`
and never back. `tests/unit/test_panel_ingest_import_isolation.py` pins this module's own
dependency set the way it pins the other two.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from types import MappingProxyType
from typing import Final

from openalpha_cn.domain.trading_calendar import TradingCalendar
from openalpha_cn.panel.catalog import DEFAULT_DATE_TIMEZONE
from openalpha_cn.panel.store import PanelStore
from openalpha_cn.panel_doctor import (
    PANEL_HEALTH_CODES,
    HealthFinding,
    HealthSeverity,
    PanelHealthReport,
    panel_health_report,
)


class PanelGateError(RuntimeError):
    """Raised for a usage error of the gate itself -- a request naming no dataset, a question
    about a dataset the request never named or a code the gate cannot issue -- and for every
    attempt to consume a clearance as though it were a collection or a blocked one as though
    it had cleared.

    Deliberately *not* raised for a sick panel: that is a `DependencyClearance` with blocks on
    it, because a caller has to be able to read the reasons rather than a traceback.
    """


GATE_BLOCKING_SEVERITIES: Final[frozenset[str]] = frozenset({"blocking", "warning"})
"""The severities this gate refuses on, stated once here rather than inline in the table.

The same set as `panel_doctor.BLOCKS_A_READ`, which `PanelHealthReport.is_clean` reads, and
asserted equal to it in `tests/unit/test_panel_gate_rules.py`. Stated separately because "what
a finding is" and "what this gate does about it" are two decisions, and deriving the second
from the first would let a demotion upstream widen the gate with nothing to fail.
"""

UNVERIFIED_DAILY_COVERAGE: Final[str] = "unverified_daily_coverage"
"""The gate's own refusal: a dataset with a per-session expectation whose sessions were never
required and which nothing in this request's scope read a session of.

Not a health code, because it is not a fault of the panel -- the panel may be perfect. It is a
statement about the pairing of this request with this report: the verdict available for that
dataset is not strong enough to act on. See this module's docstring for the measurement behind
"exactly one dataset is in this position today".
"""

GATE_REFUSAL_CODES: Final[frozenset[str]] = frozenset({UNVERIFIED_DAILY_COVERAGE})
"""Every code this gate issues that is not a `panel_doctor` finding. Closed, and disjoint from
`PANEL_HEALTH_CODES`, so `blocks_with_code` answers one question rather than two."""

GATE_CODE_BLOCKS: Final[Mapping[str, bool]] = MappingProxyType(
    {
        # `evaluate_readiness`'s own verdict: the dataset cannot be read at all.
        "no_years_requested": True,
        "empty_requirement": True,
        "not_yet_knowable": True,
        "partition_missing": True,
        "partition_file_missing": True,
        "partition_file_unreadable": True,
        "coverage_missing": True,
        "coverage_stale": True,
        "date_gap": True,
        "subject_missing": True,
        "field_missing": True,
        "stale": True,
        # Two datasets saying things that cannot both be true, and the report saying it could
        # not look. `return_path_disagreement` is the only code in this whole set that can see
        # a missing factor step, and `check_unavailable` is the difference between "I looked"
        # and "I could not"; see this module's docstring.
        "subject_set_disagreement": True,
        "close_disagreement": True,
        "return_path_disagreement": True,
        "unexplained_unpriced": True,
        "check_unavailable": True,
        # Measured to be ordinary on this corpus, and each already refused where it is
        # decidable rather than at the granularity of a whole dataset.
        "ambiguous_filing": False,
        "duplicate_versions": False,
        "revised_rows": False,
    }
)
"""What this gate does about each of `PANEL_HEALTH_CODES`, as a table rather than as a branch.

Total over the closed code set, so a twenty-first code added upstream fails
`tests/unit/test_panel_gate_rules.py` rather than arriving with no verdict and being waved
through -- which is the shape of fail-open this whole issue is about. Written out as a literal
for `HEALTH_CODE_SEVERITY`'s reason: this mapping decides whether a downstream read happens at
all, so a single entry flipped to `False` must be a diff against a block a test pins entry by
entry, not a line inside a function nobody re-reads.
"""

GATE_BLOCK_CODES: Final[frozenset[str]] = PANEL_HEALTH_CODES | GATE_REFUSAL_CODES
"""Every code a `GateBlock` may carry: the twenty health codes plus the gate's own refusals."""

SESSION_SCOPED_CROSS_CHECKS: Final[frozenset[str]] = frozenset(
    {"close_agreement", "unpriced_explained", "return_paths"}
)
"""The `panel_doctor` cross-checks that open a named session.

`subject_containment` and `statement_ambiguity` are deliberately absent. The first compares
subject lists taken off the catalog's coverage records and never reads a partition's sessions;
the second reads filings, which have no session census at all. Neither can testify that a
dataset's sessions are all there, so neither corroborates a waived `required_dates`.

Matched by name against `CrossCheckOutcome.name`, and pinned against a real report in
`tests/integration/panel/test_panel_gate.py` so that a rename in `panel_doctor` fails here
rather than silently turning the corroboration rule into a constant.
"""


@dataclass(frozen=True, slots=True, kw_only=True)
class DependencyRequest:
    """What a downstream is about to read, stated before it reads it.

    Every field that decides how hard the panel is examined is mandatory, for
    `ReadinessRequirement`'s reason: the most permissive request must not also be the easiest
    one to build. `calendar=None` and `sessions=()` are legitimate and are stated on the
    record, where the gate can refuse them -- they are not defaults that arrive by accident.

    `sessions` names the sessions the downstream will actually read. It is not inferred, for
    `panel_health_report`'s reason: "check every session" is a whole-corpus scan and "check the
    last one" is a guess about what the caller cares about. The gate's promise is scoped to
    these sessions and to `years`; a caller that clears one session and then reads a hundred
    has been promised nothing about the other ninety-nine.

    `panel_health_report`'s `date_timezone` is deliberately **not** exposed here. Every partition
    records the timezone its dates were derived in (`PartitionCoverage.date_timezone`) and the
    whole panel plane writes `DEFAULT_DATE_TIMEZONE`; a gate-level override would let a request
    judge a partition against a session boundary the partition was not written to, which is the
    one-day disagreement `panel/catalog.py` records that field to prevent. A caller with a
    genuine second convention has `panel_health_report` itself.
    """

    datasets: tuple[str, ...]
    as_of: datetime
    years: tuple[int, ...]
    sessions: tuple[date, ...]
    calendar: TradingCalendar | None
    index_codes: tuple[str, ...] = ()
    years_by_dataset: Mapping[str, Sequence[int]] | None = None
    freshness_overrides: Mapping[str, timedelta | None] | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class GateBlock:
    """One reason this request was refused.

    `finding` is the `panel_doctor` finding the block came from, carried whole so a caller told
    "blocked: date_gap" does not have to re-run the report to learn which dates. It is `None`
    for the gate's own refusals, which are not findings about the panel.
    """

    code: str
    dataset: str
    datasets: tuple[str, ...]
    severity: HealthSeverity
    detail: str
    year: int | None = None
    finding: HealthFinding | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class DependencyClearance:
    """Whether this request may proceed, and everything the refusal or the clearance rests on.

    ## This is a verdict, not a collection, and it refuses to be used as one

    `bool()`, `len()` and iteration all raise -- **including on a cleared clearance**. That is
    the deliberate part. `PanelReadOutcome` established one layer down that two different
    values are only half a fix, because `if not outcome.rows:` and `outcome.rows or []` merge
    blocked with ready-and-empty at run time while type-checking clean, and those are the ways
    people actually write this. An accessor that answered on a cleared clearance and raised on
    a blocked one would be worse than either: every test written against a healthy panel would
    pass and the line would fail only in production. `CalendarDayStatus.__bool__` already made
    this call for the same reason -- it raises for every member, not only for the unknown one.

    So the two states are reached by name: `is_blocked`, `cleared` (which raises when blocked)
    and `cleared_or_none` (the merged shape, under a name that says what it is). A caller that
    wants to treat blocked and cleared alike can still do it, but has to say so.

    `notices` are the findings that did not block -- measured facts about this corpus that are
    not defects -- and are present on a cleared clearance too, because "cleared" is a verdict
    rather than silence. `unverified_checks` is the other half of the same honesty:
    `DatasetReadiness.checks_waived` per dataset, so a caller drawing a conclusion from an
    empty `blocks` can see which questions were never put.
    """

    request: DependencyRequest
    report: PanelHealthReport
    blocks: tuple[GateBlock, ...]
    notices: tuple[HealthFinding, ...]
    unverified_checks: tuple[tuple[str, tuple[str, ...]], ...]
    cleared_or_none: tuple[str, ...] | None

    @property
    def is_blocked(self) -> bool:
        return self.cleared_or_none is None

    @property
    def cleared(self) -> tuple[str, ...]:
        """The datasets this request may read, or `PanelGateError` if it was refused."""
        if self.cleared_or_none is None:
            raise PanelGateError(
                f"this request is blocked by {sorted({block.code for block in self.blocks})}: "
                + "; ".join(f"{block.dataset}: {block.detail}" for block in self.blocks)
                + " -- use `cleared_or_none` to handle blocked and cleared together on purpose"
            )
        return self.cleared_or_none

    @property
    def blocked_datasets(self) -> tuple[str, ...]:
        """Every dataset carrying at least one block, in the order the blocks were raised."""
        seen: list[str] = []
        for block in self.blocks:
            if block.dataset not in seen:
                seen.append(block.dataset)
        return tuple(seen)

    def blocking_codes(self) -> frozenset[str]:
        return frozenset(block.code for block in self.blocks)

    def blocks_with_code(self, code: str) -> tuple[GateBlock, ...]:
        """Every block carrying `code`; raises for a code this gate cannot issue.

        `PanelHealthReport.findings_with_code`'s rule: a caller that asks about
        `parition_missing` and receives `()` reads the panel as healthy.
        """
        if code not in GATE_BLOCK_CODES:
            raise PanelGateError(
                f"{code!r} is not one of the codes this gate can issue {sorted(GATE_BLOCK_CODES)}"
            )
        return tuple(block for block in self.blocks if block.code == code)

    def blocks_for(self, dataset: str) -> tuple[GateBlock, ...]:
        """Every block against `dataset`; raises for a dataset the request never named."""
        self._require_requested(dataset)
        return tuple(block for block in self.blocks if block.dataset == dataset)

    def unverified(self, dataset: str) -> tuple[str, ...]:
        """The checks that did not run for `dataset`; raises for one the request never named.

        `()` here is the *strong* answer -- every check ran -- and it is unambiguous only
        because the guard above refuses a dataset outside the request first. Without that, an
        empty tuple would mean either "nothing was waived" or "I have never heard of it",
        which is the same conflation `blocks_for` and `blocks_with_code` refuse.
        """
        self._require_requested(dataset)
        for name, checks in self.unverified_checks:
            if name == dataset:
                return checks
        return ()

    def _require_requested(self, dataset: str) -> None:
        if dataset not in self.request.datasets:
            raise PanelGateError(
                f"{dataset!r} was not one of the datasets this request named "
                f"({list(self.request.datasets)})"
            )

    def __bool__(self) -> bool:
        raise PanelGateError(_NOT_A_COLLECTION)

    def __len__(self) -> int:
        raise PanelGateError(_NOT_A_COLLECTION)

    def __iter__(self) -> Iterator[str]:
        raise PanelGateError(_NOT_A_COLLECTION)


_NOT_A_COLLECTION: Final[str] = (
    "a clearance is a verdict, not a collection: `if not clearance:`, `clearance or []` and "
    "`len(clearance)` are the three lines that merged blocked with empty in every prior "
    "instance of this defect, so they raise here whether or not this request cleared. Ask "
    "`is_blocked`, read `cleared` (which raises when blocked), or name the merged shape as "
    "`cleared_or_none`"
)


def blocks_from_report(report: PanelHealthReport) -> tuple[GateBlock, ...]:
    """Every reason `report` gives this gate to refuse, in report order then rule order.

    Pure: the report is the only input, so the table above and the corroboration rule can both
    be exercised without a store (`tests/unit/test_panel_gate_rules.py`) while the injections
    prove the codes reach it (`tests/integration/panel/test_panel_gate.py`).
    """
    blocks = [
        GateBlock(
            code=finding.code,
            dataset=finding.dataset,
            datasets=finding.datasets,
            severity=finding.severity,
            detail=finding.detail,
            year=finding.year,
            finding=finding,
        )
        for finding in report.findings
        if GATE_CODE_BLOCKS[finding.code]
    ]
    blocks.extend(_uncorroborated_daily_coverage(report))
    return tuple(blocks)


def _uncorroborated_daily_coverage(report: PanelHealthReport) -> tuple[GateBlock, ...]:
    """Datasets whose per-session completeness this report neither required nor read.

    See this module's docstring for why the answer is neither "a waiver always blocks" nor "a
    waiver never does".
    """
    read_by: dict[str, set[str]] = {}
    for check in report.cross_checks:
        if not check.ran or check.name not in SESSION_SCOPED_CROSS_CHECKS:
            continue
        for dataset in check.datasets:
            read_by.setdefault(dataset, set()).add(check.name)
    return tuple(
        GateBlock(
            code=UNVERIFIED_DAILY_COVERAGE,
            dataset=health.dataset,
            datasets=(health.dataset,),
            severity="blocking",
            detail=(
                f"{health.dataset} publishes on the {health.freshness.cadence} cadence and its "
                "requirement waived required_dates, so nothing asked which sessions it should "
                "hold; and no session-scoped cross-check "
                f"({sorted(SESSION_SCOPED_CROSS_CHECKS)}) read it in this request, so nothing "
                "corroborated them either. A hole inside its window would be invisible to "
                "every check that did run -- name the price panel and at least one session, "
                "or narrow the request"
            ),
            finding=None,
        )
        for health in report.datasets
        if health.freshness.cadence == "daily"
        and "required_dates" in health.readiness.checks_waived
        and health.dataset not in read_by
    )


def require_datasets(store: PanelStore, request: DependencyRequest) -> DependencyClearance:
    """Decide whether `request` may read `store`, and say why not when it may not.

    Runs `panel_health_report` over exactly the datasets, years and sessions the request names
    -- the report is the whole evidence base, and this module adds no check of its own to it --
    then applies `GATE_CODE_BLOCKS` and the corroboration rule.

    Raises `PanelGateError` for a request naming no dataset (`no_years_requested`'s rule one
    layer up: a gate that inspected nothing must not answer "cleared"), and propagates
    `PanelDoctorError` for a dataset with no declared publication cadence rather than laundering
    it into a block, which would read as a defect of the panel rather than of the request.
    """
    if not request.datasets:
        requested = tuple(dict.fromkeys(request.datasets))
        raise PanelGateError(
            f"this gate was asked about no dataset at all ({list(requested)}); a check that "
            "inspected nothing must not report a clearance"
        )
    report = panel_health_report(
        store,
        as_of=request.as_of,
        datasets=request.datasets,
        years=request.years,
        calendar=request.calendar,
        index_codes=request.index_codes,
        cross_section_days=request.sessions,
        years_by_dataset=request.years_by_dataset,
        freshness_overrides=request.freshness_overrides,
        date_timezone=DEFAULT_DATE_TIMEZONE,
    )
    blocks = blocks_from_report(report)
    notices = tuple(finding for finding in report.findings if not GATE_CODE_BLOCKS[finding.code])
    unverified = tuple(
        (health.dataset, tuple(health.readiness.checks_waived))
        for health in report.datasets
        if health.readiness.checks_waived
    )
    return DependencyClearance(
        request=request,
        report=report,
        blocks=blocks,
        notices=notices,
        unverified_checks=unverified,
        cleared_or_none=None if blocks else tuple(dict.fromkeys(request.datasets)),
    )
