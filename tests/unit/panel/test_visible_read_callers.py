"""Who may take the visibility-filtered read, and who may reach the evidence plane.

Two audits with one subject: `V2-P3-002`'s two structural claims, each held by a check rather
than by a sentence in a docstring.

## 1. The filtered read is an allowlist, exactly as `query()` is

`PanelStore.read_visible_at` hands back a partition **minus** every row whose `available_time`
post-dates `as_of`. That is a deliberately short answer, and P2 declined to make `query()` do
it for a reason that has not stopped being true: "a filtered read hands back a short partition,
and every consumer above this plane reads shortness as missing data rather than as withheld
data" -- `build_index_membership` refuses a gap in the month sequence,
`load_industry_histories`' `answerable_through` exists because a read stopping short of an
interval's closing row "reassembles an interval that never ends", and `build_stock_universe`
refuses a delisting whose listing was filtered away.

P3 did not overturn that argument; it built a second door and left the first one shut. What
changed is that shortness is now *stated*: `PanelVisibleReadOutcome.withheld_row_count` is a
number the caller cannot get rows without also getting, and `visible_last_event_time` says how
far the answer reaches, which the first number does not imply.

**This file's own claim about the type split was too strong and is corrected here.** It read
"the outcome is a different type, so a filtered read cannot be handed to a reader expecting a
whole partition without mypy objecting". Measured under `mypy --strict`:
`PanelVisibleReadOutcome.rows` and `PanelReadOutcome.rows` have the *identical* static type, the
three rebuilders above take **rows** rather than outcomes, and
`stock_universe_from_panel_rows(list(filtered.rows), ...)` therefore type-checks clean. The type
checker refuses exactly one thing -- passing a whole outcome where the other outcome was
expected. So the allowlist below is not a belt beside a brace; **it is the obstacle**, and
`tests/integration/panel/test_visibility_filtered_read.py::
test_the_two_read_outcomes_expose_rows_at_the_same_static_type` pins the fact that makes that
true. `tests/unit/panel/test_query_callers.py` established the form; this is the same instrument
on the second door, and it is deliberately an allowlist and not a ban -- a future reader with a
real need may take the path, and what it may not do is take it silently.

## 2. Factor observations are kept off the evidence plane by the import graph

`V2-P3-002` forbids factor observations from `ParquetEvidenceStore`. `panel_factors.py`'s
docstring states precisely how strong that is and this file is the executable half.

The claim is *not* that the store would reject them. `EvidenceSnapshot.kind` is
`str(min_length=1, max_length=64)`, so `EvidenceSnapshot(kind="factor_observation", ...)` is
constructible and `ParquetEvidenceStore.append` would take it; `evidence/builder.py`'s closed
`_NORMALIZERS` table refuses an unknown kind on the *normalisation* path from a `ProviderBatch`,
which is not the store's front door. The claim is that the module producing factor observations
has no edge to either package and cannot acquire one without failing a test -- which makes
writing the conversion a reviewed act rather than an available one. That is what a structural
obstacle is; "impossible" would be an overclaim, and this repository has already paid once for
a docstring that made one (`domain/panel_batch.py`'s "a future `panel-batch/v2` is detectable",
which it was not).
"""

from __future__ import annotations

import ast
from pathlib import Path

import grimp

from openalpha_cn.domain.panel_batch import (
    CLOCK_COLUMN_NAMES,
    RESERVED_COLUMN_NAMES,
    SUBJECT_COLUMN_NAME,
)
from openalpha_cn.panel.store import AVAILABILITY_COLUMN, EVENT_TIME_COLUMN, SUBJECT_COLUMN

ROOT = Path(__file__).resolve().parents[3]
SOURCE = ROOT / "src" / "openalpha_cn"

FILTERED_READ = "read_visible_at"
"""The `PanelStore` method that answers with a deliberately short partition."""

FILTERED_READ_REACHERS: dict[str, frozenset[str]] = {
    "panel_factors.py": frozenset(
        {
            "_read_dataset",
            "compute_factor",
            "load_factor_manifests",
            "load_factor_observations",
            "load_factor_transform_manifests",
            "load_processed_factor_observations",
        }
    ),
    "panel_neutralization.py": frozenset(
        {"load_factor_neutralization_manifests", "load_neutralized_factor_observations"}
    ),
    "panel_doctor.py": frozenset(
        {"_factor_seal_check", "_held_cross_sections", "_sealed_builds", "panel_health_report"}
    ),
    "panel_ingest.py": frozenset(
        {
            "_read_visible_event_dated_rows",
            "_read_visible_membership_rows",
            "_read_visible_price_session",
            "load_daily_bars",
            "load_daily_valuations",
            "load_industry_cross_section",
            "load_name_histories",
            "load_price_limits",
            "load_statement_histories",
            "load_stock_universe",
            "load_suspensions",
        }
    ),
}
"""Every `src/` **function** that may reach `read_visible_at`, keyed by the file it lives in.

`V2-P4-074`: the allowlist below this used to be scoped to a *file*, and a permission scoped to
a file is not the thing this module's docstring says it is. "Adding a name here is a deliberate
act with a review attached" was true only of the first caller in a module; every caller after
that arrived silently, because the file was already named. `V2-P4-061` added two loaders inside
`panel_ingest.py` and `V2-P4-083` added a third, and none of the three moved a line in this file
-- while the parallel narrative in `panel/catalog.py` was updated in the same commit, which is
what makes it an omission rather than a drawn boundary.

The grain is a function rather than a call site because the two things `V2-P4-061` and
`V2-P4-083` did were **not** new `read_visible_at` call sites: `panel_ingest.py` has had exactly
two of those since `V2-P4-027` and still has exactly two. What those issues added were new
*reachers* of an existing private helper. An allowlist keyed on the syntactic call site would
have stayed silent through both, so it would have been the same defect one level down. The
closure below therefore follows intra-module calls, and a loader that newly routes through
`_read_visible_price_session` has to be written down here.

What the entry does not promise is inter-module reachability: the closure stops at the file, for
the same reason the allowlist above it does -- a caller in another module cannot reach the method
without importing a name from a file that is already on this list, and that import is the review.
"""

FILTERED_READ_CALLERS: frozenset[str] = frozenset(FILTERED_READ_REACHERS)
"""Every `src/` file allowed to call `read_visible_at`, relative to `src/openalpha_cn`.

Derived from `FILTERED_READ_REACHERS` rather than restated, so the two tables cannot disagree
about which files are granted -- the drift this module already carries three corrections for.

The first entry is the factor engine -- the caller `V2-P3-002` added the method for. Its inputs
are year partitions being read at a mid-year `as_of`, which `read_if_ready` refuses whole
(roadmap section 11), and its output partition has the same shape when it is read back: an
observation's `available_time` is the `as_of` it was computed at, so a year of daily cross
sections has a `max_available_time` in December.

Adding a name here is a deliberate act with a review attached, which is the property this test
exists to create. A diff that grants it must say what the new caller does about shortness --
specifically, whether it can tell a withheld row from an absent one, because the three domain
rebuilders named in this module's docstring cannot.

**`panel_neutralization.py` (`V2-P3-004`) is the second, and here is its answer to that.** It
takes the filtered read in exactly two places -- `load_neutralized_factor_observations` and
`load_factor_neutralization_manifests` -- both of which read back **its own output partitions**,
whose rows have the factor plane's mid-year shape for the factor plane's reason. Neither
reassembles anything: a neutralised row is decoded on its own and carries every field it needs,
so a withheld row is a later `as_of` of the same factor rather than a hole in a structure. That
is precisely the property the three domain rebuilders lack -- `build_index_membership` refuses a
gap in a month sequence, `load_industry_histories` needs `answerable_through` because a read that
stops short of a closing row reassembles an interval that never ends, and `build_stock_universe`
refuses a delisting whose listing was filtered away.

What the entry is *not* spent on is the neutralisation's two **foreign** inputs. The industry
memberships and the market caps are read through `panel_ingest.load_industry_histories` and
`panel_ingest.load_daily_valuations`, which take `read_if_ready` -- the un-gated door that
refuses a partition whose newest row post-dates `as_of` rather than filtering it. So the newly
allowlisted module reads its foreign data at a *stricter* setting than the already-allowlisted
one reads its own.

**`V2-P4-026` moved one of those two, and `V2-P4-061` then moved the price side wholesale.**
`load_daily_valuations` was the first onto the filtered door through
`panel_ingest._read_visible_price_session`, and `load_daily_bars` and `load_price_limits`
followed it there -- the three price datasets are now read at one setting, which they have to be,
because they are read together and a whole-partition refusal on any one of them refuses the
cross section. `load_industry_histories` did not move and is not going to, for the reason the
paragraph above gives. The fourth entry below is that grant, and this paragraph is the correction
of the sentence above it.

**`V2-P4-027` then falsified the second half of that sentence's *reason*, and this paragraph is
that correction.** `load_industry_histories` is indeed still on the un-gated door and is staying
there -- but "the industry corpus cannot tell a withheld row from an absent one" turned out to be
a claim about *that signature* rather than about the dataset. It returns histories, whose only
bound is `SecurityIndustryHistory.answerable_through`, a **year**; a mid-year `as_of` has no
honest year to name, so on that door the objection really is unanswerable.
`panel_ingest.load_industry_cross_section` answers it two ways at once: it takes the **day** as an
argument and resolves it inside, so no interval with a withheld close escapes unbounded; and it
holds each partition's visible rows against that partition's own **date census**
(`PartitionCoverage.dates` counts rows per event date, and a membership row's `available_time` is
its own event floored at 2021-12-13), so a row the census counted and the predicate removed --
**withheld** -- and a row the census never counted -- **absent** -- are two different numbers and
any difference between them is a named refusal. That is an equality rather than
`_read_visible_price_session`'s pair of permitted shapes, and it is checked per year on every
read.

**`V2-P4-034` corrected what that equality is *between*, and the correction is not cosmetic.** It
was written as one comparison of two whole-year *totals*, and a sum cannot hold a claim about a
set of days: withhold a row the census counted and reveal a row it did not, both inside one
partition, and the two errors cancel exactly -- the totals match, the read is admitted, and the
cross section it hands back is short by a security that was knowable while carrying a membership
event from after the `as_of`. Measured at 824ebff on the five-row probe in
`tests/integration/panel/test_industry_ingest.py`; the one-sided halves of the same corpus were
refused correctly, which is why nothing saw it. The reconciliation is now per event date -- the
visible rows' own `event_time` counted by day and held against the census entry by entry -- and
the two faults are refused under separate names, the look-ahead first, because "a row was visible
before its own event" and "a row this read should have seen was held back" are two different
statements about the corpus and were reported by one message that could say neither.

**`V2-P4-028` finished the sentence three paragraphs up.** "The industry memberships and the
market caps are read through `panel_ingest.load_industry_histories` and
`panel_ingest.load_daily_valuations`, which take `read_if_ready`" is now false of both halves:
`panel_neutralization.load_industry_market_cap_cross_section` takes
`panel_ingest.load_industry_cross_section`, so the neutralisation's two foreign inputs are both
read at the *same* setting as its own output partitions rather than at a stricter one.
`load_industry_histories` is unmoved and keeps the un-gated door; what changed is who calls it,
and the answer in `src/` is now `panel_doctor` alone.

**`panel_doctor.py` (`V2-P3-019`) is the third, and its answer is the sharpest of the three
because it is the only caller here that is not reading a partition it wrote.** `_factor_seal_check`
takes the filtered read over the six derived partitions -- three tiers of answers and the three
manifest partitions that address them -- and holds each build's stored cross section against the
digest its manifest declares. A hash over a *short* cross section would be the exact failure this
allowlist exists to prevent, so the property that makes it sound is stated rather than assumed:
**a factor build is either wholly visible or wholly withheld.** Every write path on all three
tiers stamps all four clocks of every row with the build's own `as_of` (see
`panel_factors.factor_observation_batch`), so one build's rows share one `available_time` and
`read_visible_at` can never return a proper subset of one. A read at an earlier `as_of` sees fewer
builds; it never sees half of one, and a build the answer partition drops is dropped from the
manifest partition in the same breath. The report therefore has nothing to reassemble and no
interval to leave open -- which is `panel_neutralization`'s answer with the reason stated one
level deeper, because here a short read would produce a *false accusation* rather than a missing
row.

The un-filtered door was not an option: `read_if_ready` refuses a year partition whose newest
`available_time` post-dates `as_of`, which for a year of daily cross sections is December, so a
health report run at any instant inside the year would have been unable to look at the plane at
all -- which is the state `V2-P3-019` found and is fixing.

**`panel_ingest.py` (`V2-P4-026`, widened by `V2-P4-027`, `061`, `076` and `083`) is the fourth,
and its answer is the strongest of the four, because for this caller the question has a measured
answer rather than an argued one.** It has exactly **two** `read_visible_at` call sites and has
had since `V2-P4-027` -- both of them private helpers -- and **eight** loaders reach them, across
eleven datasets (`load_statement_histories` is one loader over the four in
`FINANCIAL_STATEMENT_DATASETS`). Each helper answers the objection with a different
measurement, which is the
whole shape of this grant and the reason a single sentence cannot cover it; each loader answers
it with its own dataset's clock, which is why they are enumerated in `FILTERED_READ_REACHERS`
one by one and not covered by the file's name.

The first is `_read_visible_price_session`, reached from `load_daily_bars`,
`load_daily_valuations` and `load_price_limits` -- one per price dataset, all three since
`V2-P4-061` -- and it always passes `filters={"trade_date": <one session>}`. It was the first
caller in `src/` to pass `filters` at all, and that is the whole of why it is sound:
`providers/tushare.py::_daily_close_timeline` dates every price row's `available_time` at
`DAILY_AVAILABILITY_TIME` on its own `trade_date`, so one session's rows carry one availability
instant, and `_build_visible_census_sql` takes the withheld count inside the caller's own
filters. **A session read is therefore all-or-nothing.** Measured on the generated fixture panel
at `as_of` 2026-01-12T04:00Z: 2026-01-09 answers 7 rows with `withheld_row_count == 0` -- the
eighth security was halted and has no row at all, so an *absent* row arrives as an absent row --
while 2026-01-12, 2026-01-13 and 2026-01-16 each answer 0 rows with `withheld_row_count == 8`.
"Withheld" and "absent" are two different pairs of numbers on this door, which is exactly what
`build_index_membership`, `load_industry_histories` and `build_stock_universe` cannot say.

The caller does not merely *observe* that difference, it **refuses on it**: a session with rows
withheld and none visible raises rather than answering `{}`, and a session with some of each
raises too, because that mixture is the all-or-nothing property failing and a partial session is
what would be indistinguishable from a thin one. Both refusals are named separately from the
readiness codes, and a session whose 16:30 has not arrived at `as_of` is refused before any
partition is touched. `tests/integration/panel/test_daily_panel_ingest.py` drives all four
doors.

The second is `_read_visible_event_dated_rows`, and it passes **no** `filters` at all -- there is
no filter that would make an event-driven dataset all-or-nothing, because a partial partition is
what an honest mid-year read of one returns. Its measurement is the partition's own **date
census** instead: `panel_coverage` records how many rows carry each event date, so the census
says exactly how many rows an `as_of` must see. The read counts the visible rows by their own
event date and refuses on any difference from the census, date by date -- an **equality**, not a
pair of permitted shapes, and per date rather than per year, for `V2-P4-034`'s reason above.

It had one caller, `load_industry_cross_section`; `V2-P4-076` took it to four and `V2-P4-083`
to five.

**`V2-P4-074` was that gap, and this is its fix rather than its description.** The allowlist
above is scoped to a *file*, so none of those four had to answer the question this module
exists to put -- `panel_ingest.py` was already granted, and a grant to a file is a grant to
every function anyone writes in it. That is not what the sentence "adding a name here is a
deliberate act with a review attached" describes. `FILTERED_READ_REACHERS` is now the
finer-grained table and `test_every_function_that_reaches_the_filtered_read_is_named_one_by_one`
is what makes it a gate; the per-dataset answers below were written before it existed and are
kept, because the table names functions and the objection is about corpora.

**The grain that closed it is the reacher and not the call site, which is a measured
correction to how the row was filed.** `V2-P4-061` is described as having "added two
`read_visible_at` callers". It added two *loaders* -- `load_daily_bars` and `load_price_limits`
-- and no call site: `panel_ingest.py` had two `read_visible_at` calls before it and has two
after. A call-site-granular allowlist, the first thing the row's acceptance line offers, would
therefore have stayed **silent through `V2-P4-061` as well**, and would have been the same
defect one level down. The audit follows intra-module calls for that reason. Running it also
surfaced a third unreviewed reacher the row does not mention: `load_statement_histories`
(`V2-P4-083`), whose entry is below.

- **the four `FINANCIAL_STATEMENT_DATASETS`** (`load_statement_histories`, `V2-P4-083`).
  `ClockStrategy.announcement` sets `event_time == available_time ==` midnight of the row's own
  `ann_date`, exactly as `calendar_static` does for `stock_basic` and `namechange`, so the bound
  is `_knowable_through_the_same_day` and the census equality is exact. Withheld against absent
  has the same second answer `stock_basic` has, arrived at from the other side: this reader
  already carries an explicit `answerable_through` rather than deriving one from its newest row,
  so a short read answers **narrowly** -- every day inside the years it covered gets the answer a
  reader standing on that day would have had -- rather than wrongly. That is the property
  `load_adjustment_histories` lacks and why that one is still on the whole-partition door:
  `compress_adjustment_batch` stores a step function, so a withheld row shortens a horizon the
  census cannot rebuild per security (`V2-P4-079`).

- **`index_member_all`** (`load_industry_cross_section`, `V2-P4-027`/`034`).
  `providers/tushare.py::_taxonomy_backfill_timeline` dates a row's availability at its own
  event floored at 2021-12-13, so once that floor is behind `as_of` the rows the predicate keeps
  are *exactly* the rows the census places at or before `as_of`'s day. Measured on the
  four-partition fixture at `as_of` 2024-06-30T04:00Z: 1993, 2003 and 2017 each answer
  census-count rows with 0 withheld, and 2024 answers 0 rows with 2 withheld against a census
  count of 0, because both of its events fall on 2024-07-29 and 2024-07-30. Being able to *see*
  the difference is not the whole grant here, because for this dataset the dangerous shortness is
  not a short row set at all -- it is an **interval with no end in it**. So the second half of
  the answer is that the caller returns no history: it takes the `day` as an argument, resolves
  it inside, and refuses a `day` later than the newest event `as_of` could see, a `day` whose
  year has a stored partition the read did not name, and an `as_of` before the taxonomy existed.
  `tests/integration/panel/test_industry_ingest.py` drives all of them, and holds the refused day
  against the answer the same day gives once the revision has taken effect, so that the refusal
  is shown to be protecting a real difference rather than being cautious about nothing.

- **`stock_basic`** (`load_stock_universe`). `_calendar_static_timeline` sets
  `available_time == event_time == midnight` on the row's own lifecycle date, so the census bound
  is `as_of`'s own day and the equality is exact. Withheld against absent has a second, stronger
  answer on this dataset and it predates this issue:
  `stock_universe_from_panel_rows` **refuses a termination whose listing is not in the rows** --
  a partial read is a named refusal rather than a shorter universe. The visibility predicate
  cannot produce that state, and the direction is the argument: a listing is never later than its
  own termination, so a filter that removes the later row can only ever leave a security
  *reported as still listed*, which is exactly what it was at the instant being read. What the
  filter must not do is the reverse, and the census is what says it did not.
  `StockUniverse.snapshot_date` is the upper horizon, unmoved by this issue: a day past it is
  `beyond_snapshot` rather than an answer.

- **`suspend_d`** (`load_suspensions`). This is the one where withheld and absent genuinely
  collapse in the *values*: an absent halt row and a withheld one both read as "not halted"
  (`backtest/execution.py::suspended_at_the_close` returns `False` for `None`, and 5,312 of
  2024-06-28's 5,338 priced names have no row at all). So the separation cannot come from the
  rows and it does not: a session the census counted and the predicate emptied is refused by
  name, and a session the census never counted is answered, because nobody was halted. The bound
  is `_sessions_published_through` and not `as_of`'s own day, because a halt is knowable at 16:30
  on its own `trade_date` -- reconciling against the calendar day instead would count the current
  session's halts as due from midnight and refuse every honest read taken before that session's
  close. `HaltCorpus.require_coverage` remains the guard that makes an absent row mean "nothing
  happened" rather than "nobody read the partition", and it is unchanged: this read still returns
  whole years.

- **`namechange`** (`load_name_histories`). `_calendar_static_timeline` again, dated at
  `ann_date`, so the bound is `as_of`'s own day and the equality is exact. `NameHistory` has
  deliberately **no upper horizon** -- the last record answers for every later day -- so a
  corpus short by a withheld announcement would answer with the *previous* name and no signal,
  which is why the census refusal rather than a short answer is the whole grant here. What the
  filter removes is announcements made after `as_of`, which is what "knowable at `as_of`" means
  and not a shortfall: a rename announced tomorrow was not readable at the whole-partition door
  either, it merely refused the entire year instead of the one row.

For all three of the new callers the availability instant is a **fixed function of the event
date**, so the reconciliation cannot disagree on a partition whose rows carry the provider's own
clock. It is a backstop, in the same sense `_read_visible_price_session`'s second refusal is one,
and `tests/integration/panel/test_event_dated_visible_reads.py` stores a partition whose
availability instants say something else in order to reach each of them.
"""


def _calls(tree: ast.AST, name: str) -> bool:
    """Whether `tree` calls `<something>.<name>(...)` anywhere.

    Matched on the attribute name alone, unlike `test_query_callers.py`'s `columns` keyword
    discriminator, because `read_visible_at` is not an English word that another class in this
    tree happens to use -- it exists once, on `PanelStore`.

    **The residue was named wrongly and is corrected.** It said "a call that splats its arguments
    is invisible to an AST allowlist". Measured: it is not -- `store.read_visible_at(*args)`,
    `store.read_visible_at(requirement, **kwargs)` and `store.read_visible_at(*args, **kwargs)`
    are all `ast.Call` nodes with an `ast.Attribute` func, so all three are caught, and so is a
    chained receiver such as `self._store.read_visible_at(...)`. Argument shape is irrelevant to
    this matcher; only the *callee expression* matters.

    What actually escapes is a call whose callee is not an attribute access at the call site:
    binding the method to a name first (`reader = store.read_visible_at; reader(...)`) or going
    through `getattr(store, "read_visible_at")(...)`. Both are measured in
    `test_the_detector_sees_the_call_shapes_it_claims_to_and_names_the_two_it_does_not`, which is
    the shape of a bypass a reviewer can recognise on sight -- which is the property this
    allowlist buys, since it never claimed to be unbypassable.
    """
    return any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == name
        for node in ast.walk(tree)
    )


def _defined_functions(tree: ast.AST) -> dict[str, ast.AST]:
    """Every `def` in the module, nested and method alike, keyed by its **qualified** name.

    Qualified -- `ClassName.method`, `outer.inner` -- rather than bare, because bare is a lossy
    flattening and the loss would be silent. Measured while writing this: `panel_factors.py`
    defines `__call__`, `as_of`, `coverage_census` and `values` more than once each, so a
    dict keyed on the bare name would let one definition's callees stand in for another's, and
    the closure could then miss a function that really reaches or admit one that does not.
    """
    found: dict[str, ast.AST] = {}

    def walk(node: ast.AST, prefix: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef):
                qualified = f"{prefix}{child.name}"
                found[qualified] = child
                walk(child, f"{qualified}.")
            elif isinstance(child, ast.ClassDef):
                walk(child, f"{prefix}{child.name}.")

    walk(tree, "")
    return found


def _called_names(node: ast.AST) -> set[str]:
    """Every name this function calls, whether through an attribute or bare.

    Deliberately wider than `_calls`, which matches attribute access only. A private helper in
    the same module is called bare (`_read_visible_price_session(...)`) and a method is called
    through `self`, so a closure that looked at attribute access alone would follow the second
    hop and not the first -- which is the hop `V2-P4-061` and `V2-P4-083` both took.
    """
    names: set[str] = set()
    for sub in ast.walk(node):
        if isinstance(sub, ast.Call):
            func = sub.func
            if isinstance(func, ast.Attribute):
                names.add(func.attr)
            elif isinstance(func, ast.Name):
                names.add(func.id)
    return names


def _functions_reaching(tree: ast.AST, name: str) -> frozenset[str]:
    """The transitive closure, within one module, of functions that reach `name`.

    A function is in the set if it calls `name` itself or calls something already in the set.
    `name` itself is never in the answer -- `read_visible_at` is defined in `panel/store.py`,
    not in any module this runs over.

    A call site carries a bare name (`_read_visible_price_session(...)`, `self._sealed_builds(...)`)
    and an AST pass has no types with which to say which definition it means, so a bare name
    resolves to **every** qualified definition ending in it. That is deliberately the
    over-approximating direction: for an allowlist, resolving one name to two definitions names
    an extra function in the table, while resolving it to the wrong one would drop a real
    reacher and leave the grant silent -- which is the failure `V2-P4-074` is about.
    """
    definitions = _defined_functions(tree)
    by_bare: dict[str, set[str]] = {}
    for qualified in definitions:
        by_bare.setdefault(qualified.rsplit(".", 1)[-1], set()).add(qualified)
    calls = {fn: _called_names(node) for fn, node in definitions.items()}

    reaching = {fn for fn, callees in calls.items() if name in callees}
    while True:
        grown = {
            fn
            for fn, callees in calls.items()
            if fn not in reaching
            and any(by_bare.get(callee, frozenset()) & reaching for callee in callees)
        }
        if not grown:
            return frozenset(reaching)
        reaching |= grown


def _source_modules() -> list[Path]:
    return sorted(SOURCE.rglob("*.py"))


CAUGHT_SHAPES: tuple[str, ...] = (
    "store.read_visible_at(requirement, year=2026, columns=())",
    "store.read_visible_at(*args)",
    "store.read_visible_at(requirement, **kwargs)",
    "store.read_visible_at(*args, **kwargs)",
    "self._store.read_visible_at(**kwargs)",
)
"""Call shapes this detector sees. The three splat forms are here because the docstring it
replaced claimed they were not."""

ESCAPING_SHAPES: tuple[str, ...] = (
    "reader = store.read_visible_at\nreader(requirement)",
    'getattr(store, "read_visible_at")(requirement)',
)
"""Call shapes this detector does not see: the callee is not an attribute access at the call."""


def test_the_detector_sees_the_call_shapes_it_claims_to_and_names_the_two_it_does_not() -> None:
    """`_calls`' own residue, measured rather than asserted from the shape of the code.

    Both directions are here for the reason the allowlist has two tests: the positive half stops
    the detector quietly matching nothing, and the negative half is the honest boundary. If a
    later change strengthens `_calls` -- resolving a bound-method alias, say -- the second loop
    goes red, and that is the intended signal: the docstring's residue paragraph is then wrong
    and has to be narrowed rather than left standing as a warning about a hole that closed.
    """
    for source in CAUGHT_SHAPES:
        assert _calls(ast.parse(source), FILTERED_READ), f"{source!r} should be caught"

    for source in ESCAPING_SHAPES:
        assert not _calls(ast.parse(source), FILTERED_READ), (
            f"{source!r} is now caught; `_calls` was strengthened, so this module's residue "
            "paragraph overstates the hole and must be narrowed"
        )


def test_only_the_allowlisted_modules_take_the_visibility_filtered_read() -> None:
    offenders = {
        str(path.relative_to(SOURCE))
        for path in _source_modules()
        if str(path.relative_to(SOURCE)) not in FILTERED_READ_CALLERS | {"panel/store.py"}
        and _calls(ast.parse(path.read_text(encoding="utf-8")), FILTERED_READ)
    }

    assert offenders == set(), (
        f"{sorted(offenders)} call PanelStore.{FILTERED_READ}(), which answers with a partition "
        "minus every row that was not knowable at as_of. Every consumer above this plane that "
        "was written against read_if_ready reads a short answer as missing data. If this caller "
        "can tell a withheld row from an absent one, add it to FILTERED_READ_CALLERS and say so "
        "in the diff"
    )


def test_every_function_that_reaches_the_filtered_read_is_named_one_by_one() -> None:
    """`V2-P4-074`: the grant is per function, so a second caller inside a granted file trips it.

    This is the assertion the file-scoped allowlist above could not make. `V2-P4-061` added
    `load_daily_bars` and `load_price_limits` onto `_read_visible_price_session` and `V2-P4-083`
    added `load_statement_histories` onto `_read_visible_event_dated_rows`; `panel_ingest.py` was
    already granted, so all three arrived without a line moving here -- in a file whose stated
    purpose is that they cannot.

    An **equality** rather than a subset check, in both directions and for two different reasons.
    A new name that is not listed is the gap this row was filed against. A listed name that no
    longer reaches is `test_the_allowlist_names_files_that_exist_and_actually_make_the_call`'s
    reason one grain finer: a permission nobody revoked, sitting under whatever gets written at
    that name next.
    """
    for name, declared in FILTERED_READ_REACHERS.items():
        tree = ast.parse((SOURCE / name).read_text(encoding="utf-8"))
        measured = _functions_reaching(tree, FILTERED_READ)

        assert measured == declared, (
            f"{name}: {sorted(measured - declared)} newly reach {FILTERED_READ}() and "
            f"{sorted(declared - measured)} no longer do. Every function here hands back a "
            "partition minus the rows that were not knowable at as_of. Add or remove the name "
            "in FILTERED_READ_REACHERS and say in the diff what the new one does about "
            "shortness -- whether it can tell a withheld row from an absent one"
        )


def test_the_closure_follows_the_hop_the_file_scoped_allowlist_missed() -> None:
    """The sentinel for the test above, and it is not decorative.

    `panel_ingest.py` has had exactly two `read_visible_at` call sites since `V2-P4-027`. If the
    closure only looked at those, its answer would be `{_read_visible_price_session,
    _read_visible_event_dated_rows}` -- a set the three loaders `V2-P4-061` and `V2-P4-083` added
    do not appear in, so the equality above would pass on a tree with the defect still in it.
    The measured gap between the two sets is what says the closure is doing the work.
    """
    tree = ast.parse((SOURCE / "panel_ingest.py").read_text(encoding="utf-8"))
    direct = {
        fn for fn, node in _defined_functions(tree).items() if FILTERED_READ in _called_names(node)
    }

    assert direct == {"_read_visible_price_session", "_read_visible_event_dated_rows"}
    assert direct < _functions_reaching(tree, FILTERED_READ)
    assert {"load_daily_bars", "load_price_limits", "load_statement_histories"} <= (
        _functions_reaching(tree, FILTERED_READ) - direct
    )


def test_the_closure_keys_on_qualified_names_because_bare_ones_collide_here() -> None:
    """The measurement behind `_defined_functions`' choice, kept because it is the reason.

    The first draft of this audit keyed definitions on the bare name and asserted the modules
    had no duplicates. They do: `panel_factors.py` defines `__call__`, `as_of`, `coverage_census`
    and `values` more than once each, across different classes. A bare-keyed dict silently keeps
    whichever it walked last, so one definition's callees would answer for another's. Nothing
    reaching `read_visible_at` is currently behind a colliding name -- which is exactly why this
    has to be checked rather than noticed.
    """
    factors = ast.parse((SOURCE / "panel_factors.py").read_text(encoding="utf-8"))
    qualified = _defined_functions(factors)
    bare = [name.rsplit(".", 1)[-1] for name in qualified]

    assert len(qualified) > len(set(bare)), (
        "panel_factors.py no longer defines any name twice, so the qualification this helper "
        "does is currently buying nothing measurable; keep it, and narrow this test's claim"
    )
    assert {"__call__", "as_of"} <= {name for name in bare if bare.count(name) > 1}


def test_the_allowlist_names_files_that_exist_and_actually_make_the_call() -> None:
    """An allowlist entry that no longer calls anything is a permission nobody revoked.

    `test_query_callers.py`'s own second test, for its own reason: the same failure mode as a
    `# noqa` left behind after the code moved, granting exactly the thing it was meant to
    constrain to whatever is written there next.
    """
    for name in FILTERED_READ_CALLERS:
        path = SOURCE / name
        assert path.is_file(), f"FILTERED_READ_CALLERS names {name}, which does not exist"
        assert _calls(ast.parse(path.read_text(encoding="utf-8")), FILTERED_READ), (
            f"FILTERED_READ_CALLERS names {name}, which no longer calls {FILTERED_READ}(); "
            "remove the entry rather than leaving the exemption standing"
        )


def test_the_engine_takes_the_filtered_read_and_not_the_un_gated_one() -> None:
    """The positive half, without which the allowlist above would be satisfied by a tree where
    the factor engine read nothing at all.

    Two directions, because they fail differently. The engine must reach rows through
    `read_visible_at` -- if it stopped, this file would be policing a door nobody uses. And it
    must **not** reach them through `query()`: `tests/unit/panel/test_query_callers.py` already
    fails on that, and asserting it here too is what keeps the two audits from being read as
    alternatives. `read_visible_at` runs the whole readiness rule table and compensates exactly
    one code; `query()` runs none of it.
    """
    engine = ast.parse((SOURCE / "panel_factors.py").read_text(encoding="utf-8"))

    assert _calls(engine, FILTERED_READ)
    assert not _calls(engine, "query")
    assert not _calls(engine, "profile_query")
    assert not _calls(engine, "read_if_ready")


def test_the_availability_column_this_module_filters_on_is_the_one_the_batch_contract_writes() -> (
    None
):
    """`panel/store.py` restates `"available_time"` rather than importing it, because
    `openalpha_cn.panel` imports no sibling subpackage at all. Two copies of a string that has
    to be identical is exactly the drift `PANEL_BATCH_SCHEMA_VERSIONS_READABLE` already carries
    a pin for, and this is that pin: the store's predicate names a column every panel row
    actually has, and the batch contract reserves the name so no provider column can shadow it.
    """
    assert AVAILABILITY_COLUMN in CLOCK_COLUMN_NAMES
    assert AVAILABILITY_COLUMN in RESERVED_COLUMN_NAMES


def test_the_two_columns_the_second_gate_reads_are_pinned_the_same_way() -> None:
    """`V2-P3-002`'s review added two more restated names, so they get the same pin.

    `read_visible_at` now aggregates `event_time` (to say how far the visible rows reach) and
    probes `subject` (to re-decide `subject_missing`), both by literal name in SQL. Each is
    restated in `panel/store.py` for the reason `AVAILABILITY_COLUMN` is -- `openalpha_cn.panel`
    imports no sibling subpackage -- and two copies of a string that has to be identical is the
    drift the assertion above exists for. A predicate naming a column no panel row carries would
    make the re-decided checks refuse every partition; a `subject` a provider column could shadow
    would make the probe answer about the wrong thing.
    """
    assert EVENT_TIME_COLUMN in CLOCK_COLUMN_NAMES
    assert EVENT_TIME_COLUMN in RESERVED_COLUMN_NAMES
    assert SUBJECT_COLUMN == SUBJECT_COLUMN_NAME
    assert SUBJECT_COLUMN in RESERVED_COLUMN_NAMES


def test_no_top_level_panel_module_can_reach_the_evidence_plane_at_all() -> None:
    """`V2-P3-002`'s "forbidden from `ParquetEvidenceStore`", as a live import-graph question.

    `ParquetEvidenceStore` is in `openalpha_cn.storage` and the normaliser that feeds it is in
    `openalpha_cn.evidence`. No `panel_*` module may import either, so the module that produces
    `FactorObservation`s has no way to hand one to that store without a diff that adds an edge
    a test refuses. `tests/unit/test_panel_ingest_import_isolation.py::
    test_no_top_level_panel_module_reaches_a_composition_root_or_a_credential` asks the same
    graph a broader question for a different reason (a credential, a composition root); this
    one is asserted here as well, over the discovered module list, because the reason it holds
    for the factor engine is `V2-P3-002`'s own and would survive a change to that one's
    forbidden set.
    """
    graph = grimp.build_graph("openalpha_cn")
    modules = sorted(
        f"openalpha_cn.{path.stem}"
        for path in SOURCE.glob("panel_*.py")
        if not path.stem.startswith("__")
    )

    assert "openalpha_cn.panel_factors" in modules
    for module in modules:
        for plane in ("openalpha_cn.storage", "openalpha_cn.evidence"):
            assert not graph.direct_import_exists(
                importer=module, imported=plane, as_packages=True
            ), f"{module} must not reach {plane}: factor observations write the panel plane only"


def test_the_evidence_store_is_reachable_from_somewhere_so_the_check_above_is_not_vacuous() -> None:
    """The sentinel for the assertion above: if `openalpha_cn.storage` had no importer at all,
    or `grimp` had stopped resolving these names, every `assert not ...` would pass while
    proving nothing about the boundary."""
    graph = grimp.build_graph("openalpha_cn")

    assert graph.direct_import_exists(
        importer="openalpha_cn.sdk", imported="openalpha_cn.storage", as_packages=True
    )
    assert graph.direct_import_exists(
        importer="openalpha_cn.panel_factors", imported="openalpha_cn.panel", as_packages=True
    )
