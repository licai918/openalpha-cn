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
4. **A clearance carries its own width.** `cleared` hands back `ClearedDataset` records rather
   than bare names, because for one dataset shape the evidence is session-local and a bare
   name reads as a whole year. See "the width of a clearance is part of it" below; this is
   the correction the `V2-P1-013` review forced.

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
fifteen-dataset measurement is pinned by
`test_exactly_one_declared_dataset_is_daily_and_waives_its_date_check`.) For such a dataset the
gate asks whether a **session-scoped cross-check actually ran over it**: `close_agreement`,
`unpriced_explained` or `return_paths`, the three `panel_doctor` checks that open a session
rather than comparing catalog records. If none did, the gate refuses with
`unverified_daily_coverage` rather than clearing on registration, files, fields and freshness
-- which is the precise set of checks the Task 29 panel passed while carrying its hole.

The residue is named rather than hidden: a hole in `adj_factor` on a session the factor did
**not** step across changes no answer -- `factor_on` carries the previous step forward, which
is what a step function means -- and the gate clears it. Both directions are pinned.

## The width of a clearance is part of it

An earlier cut of this module said that once a session-scoped cross-check had run, "the hole
that would have mattered is reachable and the dataset clears". **That sentence was false and
the review proved it on this repository's own fixture.** The three session-scoped checks run
on `cross_section_days`, which is `DependencyRequest.sessions` and nothing else, while
`cleared` handed back a bare `('adj_factor', ...)` granted over `request.years`. With the
`adj_factor` row for the ex-rights session removed, naming that session (or the one after it)
blocks with `return_path_disagreement`, and naming **any later session** clears -- ten sessions
in the fixture, so `2026-01-15` and `2026-01-16` both cleared with `is_clean=True` and
`notices=[]`, after which `adjusted_return` over `2026-01-12..2026-01-13` answered
`-0.530973%` against a true `+2.742251%`. That is Task 29's Critical, reproduced through the
supported writers, on a request the gate had cleared. `close_disagreement` and
`unexplained_unpriced` have the same shape: both block when the defective session is named and
both cleared when a different one was.

The read side cannot do better than this, and pretending otherwise is the defect.
`panel_ingest`'s own write-time census refuses a session dropped for *every* security and
accepts one dropped for a single security, and it records that this residue "cannot be closed
from the read side". Nor can the gate widen the corroboration by itself: bounding the sessions
a year implies needs `panel_ingest._sessions_published_through`, and this module is pinned to
importing `domain`, `panel` and `panel_doctor` only. Blocking every request whose years reach
past its named sessions was rejected for the reason the notices were: it refuses the ordinary
narrow request -- one session out of two hundred and forty-four -- and a gate that refuses
everything gets switched off.

So the gate states its width instead of overstating it. `cleared` returns `ClearedDataset`
records: the dataset, the years the year-scoped checks covered, the sessions a session-scoped
cross-check actually opened, and the caveats that remain open outside those sessions.
`ClearedDataset.corroborates` answers "did a cross-dataset check open that session for that
dataset", and a dataset in the `adj_factor` shape carries `unverified_daily_coverage` as a
caveat on the clearance -- so the code the gate used to fall silent about is now on the
artifact the caller must hold to read anything at all, rather than being resolved by a check
that only looked at one day. The sessions are recorded for **every** dataset the three checks
touch, not only for `adj_factor`, because the `close_disagreement` and the
`unexplained_unpriced` the review seeded on an unnamed session cleared for the same reason.

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
    HEALTH_CODE_CATEGORY,
    PANEL_HEALTH_CODES,
    DatasetHealth,
    HealthCategory,
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

GATE_CODE_CATEGORY: Final[Mapping[str, HealthCategory]] = MappingProxyType(
    {UNVERIFIED_DAILY_COVERAGE: "unanswerable"}
)
"""The report heading each of this gate's own codes files under.

`HEALTH_CODE_CATEGORY` is total over `PANEL_HEALTH_CODES` and says nothing about a code the
gate invented, so a facet that groups by category -- `V2-P1-016`'s REST surface is the first
-- would have had to special-case this one or drop it. `unanswerable` is the heading
`panel_doctor` already gives to "a question that could not be put at all", which is exactly
what a waived date check nothing corroborated is; filing it under `missing` would tell a
reader that rows are absent when the truth is that nobody looked.
"""

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
"""Every code a `GateBlock` or a `ClearedDataset` caveat may carry: the twenty health codes
plus the gate's own refusals. One closed set for both, because `unverified_daily_coverage` is
the same question at two strengths -- a refusal when nothing corroborated the dataset at all,
a caveat when something did but only over the sessions the request named."""

GATE_CODE_CATEGORIES: Final[Mapping[str, HealthCategory]] = MappingProxyType(
    {**HEALTH_CODE_CATEGORY, **GATE_CODE_CATEGORY}
)
"""The heading for every code in `GATE_BLOCK_CODES`, health codes and gate codes alike.

Total over that set and asserted so in `tests/unit/test_panel_gate_rules.py`: a caller
grouping blocks by category must not have to know which half a code came from, and a code with
no heading would drop out of a grouped view rather than failing a test."""

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
    has been promised nothing about the other ninety-nine, and since the `V2-P1-013` review
    the clearance *says so* -- see `ClearedDataset`.

    A key in `years_by_dataset` or `freshness_overrides` naming a dataset outside `datasets`
    raises rather than being dropped. `panel_health_report` resolves both mappings against the
    datasets it was given, so such a key changes nothing at all, and a caller who misspells one
    while narrowing a backfill window would otherwise be told the panel is fine by a gate that
    silently used the wide window.

    `panel_health_report`'s `date_timezone` is deliberately **not** exposed here. Every partition
    records the timezone its dates were derived in (`PartitionCoverage.date_timezone`) and the
    whole panel plane writes `DEFAULT_DATE_TIMEZONE`; a gate-level override would let a request
    judge a partition against a session boundary the partition was not written to, which is the
    one-day disagreement `panel/catalog.py` records that field to prevent. That is not a
    theoretical hazard and the exclusion is not a tidy-up: on one store at
    `as_of = 2026-01-16 09:00 UTC`, `Asia/Shanghai` reports `date_gap` and `is_clean=False`
    while `UTC` reports no code at all and `is_clean=True`, because the two zones disagree
    about whether the day's session had published. The measurement is pinned in
    `tests/integration/panel/test_panel_gate.py`. A caller with a genuine second convention has
    `panel_health_report` itself.
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

    `dataset` is the name the finding is *filed* under -- `HealthFinding.datasets[0]`, the
    subset or the disagreeing side of a pair -- and it is a label, not the answer to "is this
    dataset implicated". `datasets` is that answer, and `blocks_for` and `blocked_datasets`
    both read it: a `close_disagreement` is raised by `daily_basic` publishing a close `daily`
    does not corroborate, and a caller polling `blocks_for('daily_basic')` used to be told
    `()`, which is the confusion those two accessors exist to refuse.
    """

    code: str
    dataset: str
    datasets: tuple[str, ...]
    category: HealthCategory
    severity: HealthSeverity
    detail: str
    year: int | None = None
    finding: HealthFinding | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class ClearedDataset:
    """One dataset this request may read, and how far the permission actually reaches.

    `cleared` used to be a tuple of names, and a name is exactly as wide as its reader assumes.
    For every dataset but one that is harmless: the verdict rests on registration, files,
    fields, freshness and a calendar-derived date census, all of which are stated over `years`.
    For a dataset in the `adj_factor` shape -- `daily` cadence, `required_dates` waived -- the
    only evidence that its sessions are all there is a cross-check that opened the sessions in
    `DependencyRequest.sessions`, and nothing else. Handing that back as `'adj_factor'` is how
    the review found Task 29's wrong number reachable through a *cleared* gate.

    So the record carries the two scopes separately rather than folding them into one verdict:

    - `years` -- what the year-scoped checks covered, per dataset (`years_by_dataset` follows
      the dataset here as it does everywhere else).
    - `corroborated_sessions` -- the sessions a session-scoped cross-check actually opened over
      this dataset. **Not only for the `adj_factor` shape**: all three checks run on
      `cross_section_days` and nothing else, so `daily`, `daily_basic`, `suspend_d` and
      `stock_basic` are equally unexamined outside it, and the review demonstrated that too --
      a `close_disagreement` and an `unexplained_unpriced` seeded on `2026-01-15` both cleared
      a request that named `2026-01-13`. `()` means no session-scoped check read this dataset
      at all.
    - `caveats` -- the gate codes still open *outside* those sessions. Non-empty only for the
      `adj_factor` shape, where it holds `unverified_daily_coverage`: the same code the gate
      refuses with when nothing corroborated the dataset at all, carried here at the lower
      strength "something did, over these sessions only". It rides on the permission it
      qualifies rather than in a list beside it, because the two are the same fact.

    `corroborates` is deliberately a statement of fact rather than a verdict. `False` does not
    mean the session is broken; it means no cross-dataset check looked at it -- and for a
    dataset carrying `unverified_daily_coverage` that is the whole difference between a verdict
    and a guess. Folding it together with the year-scoped evidence into one `covers(session)`
    was tried and dropped: for `daily` the year census *does* reach the session and the
    cross-checks do not, so a single boolean would have to pick one of two true answers.
    """

    dataset: str
    years: tuple[int, ...]
    corroborated_sessions: tuple[date, ...] = ()
    caveats: tuple[str, ...] = ()

    def corroborates(self, session: date) -> bool:
        """Whether a session-scoped cross-check in this report opened `session` for this
        dataset."""
        return session in self.corroborated_sessions


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
    empty `blocks` can see which questions were never put. `cleared` is the third: it hands
    back `ClearedDataset` records, so the width of the permission travels with it.
    """

    request: DependencyRequest
    report: PanelHealthReport
    blocks: tuple[GateBlock, ...]
    notices: tuple[HealthFinding, ...]
    unverified_checks: tuple[tuple[str, tuple[str, ...]], ...]
    cleared_or_none: tuple[ClearedDataset, ...] | None

    @property
    def is_blocked(self) -> bool:
        return self.cleared_or_none is None

    @property
    def cleared(self) -> tuple[ClearedDataset, ...]:
        """What this request may read and how widely, or `PanelGateError` if it was refused."""
        if self.cleared_or_none is None:
            raise PanelGateError(
                f"this request is blocked by {sorted({block.code for block in self.blocks})}: "
                + "; ".join(f"{block.dataset}: {block.detail}" for block in self.blocks)
                + " -- use `cleared_or_none` to handle blocked and cleared together on purpose"
            )
        return self.cleared_or_none

    def cleared_for(self, dataset: str) -> ClearedDataset:
        """The permission granted for `dataset`, with its scope; raises when blocked.

        Raises for a dataset the request never named, `unverified`'s rule: a caller handed a
        default-shaped record for a name the gate never considered would read it as a
        permission.
        """
        self._require_requested(dataset)
        for entry in self.cleared:
            if entry.dataset == dataset:
                return entry
        raise PanelGateError(
            f"{dataset!r} was named by this request but the clearance carries no record for it"
        )

    @property
    def blocked_datasets(self) -> tuple[str, ...]:
        """Every dataset carrying at least one block, in the order the blocks were raised.

        Read off `GateBlock.datasets`, so **both** sides of a cross-dataset block are named.
        `close_disagreement` is filed under `daily` and is raised by a `daily_basic` close
        `daily` does not corroborate; naming only the first side made this property's own
        promise false for the second one.
        """
        seen: list[str] = []
        for block in self.blocks:
            for name in block.datasets:
                if name not in seen:
                    seen.append(name)
        return tuple(seen)

    def blocking_codes(self) -> frozenset[str]:
        return frozenset(block.code for block in self.blocks)

    def caveat_codes(self) -> frozenset[str]:
        """Every caveat riding on this clearance; raises when it was refused."""
        return frozenset(code for entry in self.cleared for code in entry.caveats)

    def blocks_with_code(self, code: str) -> tuple[GateBlock, ...]:
        """Every block carrying `code`; raises for a code this gate cannot issue.

        `PanelHealthReport.findings_with_code`'s rule: a caller that asks about
        `parition_missing` and receives `()` reads the panel as healthy.
        """
        self._require_issuable(code)
        return tuple(block for block in self.blocks if block.code == code)

    def cleared_with_caveat(self, code: str) -> tuple[ClearedDataset, ...]:
        """Every cleared dataset carrying `code` as a caveat; raises for an unissuable code.

        The same rule as `blocks_with_code`, and it matters more here: this accessor is how a
        caller asks "was anything cleared only narrowly", and a typo answering `()` reads as
        "no, everything was cleared outright".
        """
        self._require_issuable(code)
        return tuple(entry for entry in self.cleared if code in entry.caveats)

    def blocks_for(self, dataset: str) -> tuple[GateBlock, ...]:
        """Every block `dataset` is named in; raises for a dataset the request never named.

        Matched against `GateBlock.datasets` rather than `GateBlock.dataset`, for
        `blocked_datasets`'s reason: the `adj_factor` that is short of a security a
        `subject_set_disagreement` is about answered `()` here, which is "nothing wrong with
        that dataset" told to a caller polling dataset by dataset.
        """
        self._require_requested(dataset)
        return tuple(block for block in self.blocks if dataset in block.datasets)

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

    def _require_issuable(self, code: str) -> None:
        if code not in GATE_BLOCK_CODES:
            raise PanelGateError(
                f"{code!r} is not one of the codes this gate can issue {sorted(GATE_BLOCK_CODES)}"
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
            category=finding.category,
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


def cleared_datasets(
    report: PanelHealthReport, sessions: Sequence[date]
) -> tuple[ClearedDataset, ...]:
    """What `report`'s datasets may be read over, given the sessions the request named.

    Pure, and separate from `blocks_from_report` because it answers a different question: that
    one asks what is wrong, this one asks how wide the permission is where nothing is. Only
    meaningful when `blocks_from_report` is empty -- `require_datasets` calls it only then --
    but total over the report's datasets so that the width of every one of them is stated
    rather than only the interesting one's.
    """
    read_by = _session_scoped_reads(report)
    named = tuple(dict.fromkeys(sessions))
    return tuple(
        _cleared_dataset(health, named=named, was_read=health.dataset in read_by)
        for health in report.datasets
    )


def _cleared_dataset(
    health: DatasetHealth, *, named: tuple[date, ...], was_read: bool
) -> ClearedDataset:
    return ClearedDataset(
        dataset=health.dataset,
        years=health.years_requested,
        corroborated_sessions=named if was_read else (),
        caveats=(UNVERIFIED_DAILY_COVERAGE,) if _needs_session_corroboration(health) else (),
    )


def _needs_session_corroboration(health: DatasetHealth) -> bool:
    """Whether this dataset's sessions are attested by nothing but a session-scoped check.

    The `adj_factor` shape: a per-session expectation (only the `daily` cadence has one) that
    the requirement waived. Stated once and read twice -- by the refusal below and by the
    caveat `cleared_datasets` attaches -- because the two have to agree about which datasets
    they are talking about or the gate would refuse one set and qualify another.
    """
    return (
        health.freshness.cadence == "daily" and "required_dates" in health.readiness.checks_waived
    )


def _session_scoped_reads(report: PanelHealthReport) -> dict[str, set[str]]:
    """Which session-scoped cross-checks actually opened a session of each dataset."""
    read_by: dict[str, set[str]] = {}
    for check in report.cross_checks:
        if not check.ran or check.name not in SESSION_SCOPED_CROSS_CHECKS:
            continue
        for dataset in check.datasets:
            read_by.setdefault(dataset, set()).add(check.name)
    return read_by


def _uncorroborated_daily_coverage(report: PanelHealthReport) -> tuple[GateBlock, ...]:
    """Datasets whose per-session completeness this report neither required nor read.

    See this module's docstring for why the answer is neither "a waiver always blocks" nor "a
    waiver never does" -- and, under "the width of a clearance is part of it", why a dataset
    that *was* read still leaves `unverified_daily_coverage` open outside the sessions the
    request named, as a caveat on the clearance rather than as silence.
    """
    read_by = _session_scoped_reads(report)
    return tuple(
        GateBlock(
            code=UNVERIFIED_DAILY_COVERAGE,
            dataset=health.dataset,
            datasets=(health.dataset,),
            category=GATE_CODE_CATEGORY[UNVERIFIED_DAILY_COVERAGE],
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
        if _needs_session_corroboration(health) and health.dataset not in read_by
    )


def require_datasets(store: PanelStore, request: DependencyRequest) -> DependencyClearance:
    """Decide whether `request` may read `store`, and say why not when it may not.

    Runs `panel_health_report` over exactly the datasets, years and sessions the request names
    -- the report is the whole evidence base, and this module adds no check of its own to it --
    then applies `GATE_CODE_BLOCKS` and the corroboration rule.

    Raises `PanelGateError` for a request naming no dataset (`no_years_requested`'s rule one
    layer up: a gate that inspected nothing must not answer "cleared") and for a per-dataset
    override keyed on a dataset the request never named, which `panel_health_report` would
    otherwise drop without a word. Propagates `PanelDoctorError` for a dataset with no declared
    publication cadence rather than laundering it into a block, which would read as a defect of
    the panel rather than of the request.
    """
    requested = tuple(dict.fromkeys(request.datasets))
    if not requested:
        raise PanelGateError(
            "this gate was asked about no dataset at all; a check that inspected nothing must "
            "not report a clearance"
        )
    _require_overrides_in_scope("years_by_dataset", request.years_by_dataset, requested)
    _require_overrides_in_scope("freshness_overrides", request.freshness_overrides, requested)
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
        cleared_or_none=None if blocks else cleared_datasets(report, request.sessions),
    )


def _require_overrides_in_scope(
    name: str, overrides: Mapping[str, object] | None, requested: tuple[str, ...]
) -> None:
    """Refuse a per-dataset override keyed on a dataset this request never named.

    `panel_health_report` resolves both mappings against the datasets it was handed, so such a
    key is not merely unused -- it is *dropped*, and the dataset it was meant for is assessed
    over the request's default years or its derived freshness bound with nothing said. A caller
    narrowing a backfill window that way would be told the panel is fine on the strength of the
    window it was trying to replace.
    """
    stray = tuple(key for key in (overrides or {}) if key not in set(requested))
    if stray:
        raise PanelGateError(
            f"{name} names {list(stray)}, which this request did not ask about "
            f"({list(requested)}); such a key is dropped rather than applied, so the dataset "
            "it was meant for would be judged on the request's defaults instead"
        )
