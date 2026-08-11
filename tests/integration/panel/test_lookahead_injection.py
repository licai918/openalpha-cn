"""The three look-ahead injections, on the read side (`V2-P2-001`, `V2-P2-003`, `V2-P2-004`).

Each of the three issues asks the same question of a different dataset: **store a row the read
cannot yet know about, and prove the read refuses because of that row.** The write side already
refuses one (`tests/contract/panel/test_columnar_batch_parity.py` injects a one-microsecond
late row anywhere in a 64-row batch and both contracts reject it; four provider contracts
assert `no_data` with a reason that separates "not knowable" from "not there"). What had no
assertion anywhere was the **read** side: `PanelStore.query()`'s scan carries no
`available_time` predicate, so the only point-in-time gate a reader passes through is
`panel/catalog.py::evaluate_readiness`' partition-level `not_yet_knowable`, and no test drove a
future row into a partition and then asked a loader for it.

## The pairing is the whole method

An injection that only shows "the read refused" proves nothing: `not_yet_knowable` is judged per
partition and a partition is a year (`panel/catalog.py`'s "Disclosure" section), so *any* `as_of`
earlier than a year's newest row blocks that year for reasons that have nothing to do with
anything injected. Every test here therefore comes in a pair over **one** partition at **one**
`as_of`:

- the partition with the injected row(s) -> the loader refuses, naming `not_yet_knowable` and
  the instant the injected row became knowable;
- **the same partition with exactly those rows removed** -> the loader answers.

The second half is built by pruning the generated batch itself
(`_without_the_rows_the_read_cannot_see`) and driving it through the same real writer, not by
generating a differently-shaped panel. That is what makes "the refusal came from that row"
an assertion rather than an argument: nothing else about the partition moved.

A third test per issue pins the **boundary**. `evaluate_readiness` compares
`max_available_time > as_of`, so the read opens at the injected row's own availability instant
and is blocked one microsecond earlier -- the read-side counterpart of the `<=` boundary
`test_columnar_batch_parity.py` pins on the write side.

## What each read answers once it is allowed to

Refusing is half of a point-in-time guarantee; the other half is that the answer, once the row
is knowable, is the *new* one and that the earlier question keeps the *earlier* answer. So each
issue also asserts what the domain object says on both sides of the injected row's own business
date -- and those two clocks are not the same clock, which is the hazard each of these modules
names in its own docstring: the panel gate compares instants, `StatementHistory.filing_for`,
`IndexMembership.constituents_on` and `SecurityIndustryHistory.assignment_on` all compare plain
dates.

## What is deliberately not here

No row-level `available_time` filter is added to `PanelStore.query()`. `panel/catalog.py`'s
"Disclosure" section leaves that to P2 by name -- "splitting this into a partition-level gate
plus a row-level `available_time` filter ... is explicitly left to P2 rather than smuggled in
here" -- and the judgement recorded by this task is that it should not be taken. The column is
stored (every partition carries all four clocks, so `query()` can already select it) and
`_build_scan_sql` supports equality filters only, so taking it would mean a comparison grammar
as well. Neither is the reason to decline. The reason is that a filtered read hands back a
*short* partition, and every consumer above
this plane reads shortness as missing data rather than as withheld data --
`build_index_membership` refuses a gap in the month sequence, `load_industry_histories`'
`answerable_through` exists precisely because a read that stops short of an interval's closing
row "reassembles an interval that never ends", and `build_stock_universe` refuses a delisting
whose listing was filtered away. Turning a fail-closed refusal into a plausible-looking short
answer is the one trade this plane is built not to make. The partition-level gate stays, and
what P2 owes it is the proof that its refusals are attributable, which is this file.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from panel_fixtures import (
    AS_OF,
    BASE_ANNOUNCEMENT,
    BASE_PERIOD,
    FUTURE_ANNOUNCEMENT,
    FUTURE_PUBLICATION,
    INDEX_CODE,
    INDUSTRY_L1,
    RECLASSIFIED_FROM,
    RECLASSIFIED_THROUGH,
    THIRD_VERSION_VALUE,
    YEAR,
    GeneratedPanel,
    generate_panel,
    write_generated_panel,
)

from openalpha_cn.domain.financial_statements import INCOME_DATASET, StatementHistory
from openalpha_cn.domain.index_membership import (
    INDEX_WEIGHT_DATASET,
    IndexMembership,
    IndexMembershipHorizonError,
)
from openalpha_cn.domain.industry_classification import (
    INDUSTRY_MEMBERSHIP_DATASET,
    IndustryClassificationError,
    SecurityIndustryHistory,
)
from openalpha_cn.domain.panel_batch import ColumnarPanelBatch, PanelColumn, TimelineColumns
from openalpha_cn.panel.store import PanelStorageError, PanelStore
from openalpha_cn.panel_ingest import (
    load_index_membership,
    load_industry_histories,
    load_statement_histories,
)

DISPUTED_COLUMN = "total_revenue"
"""`income`'s first stored column, which every generated statement disagreement rides on."""

BASE_VALUE = 100.0
"""`securities[0]`'s value in `DISPUTED_COLUMN` on its original filing: `100.0 + index`."""


def _load_income(store: PanelStore, as_of: datetime) -> Mapping[str, StatementHistory]:
    return load_statement_histories(
        store, dataset=INCOME_DATASET, years=(YEAR,), as_of=as_of, max_staleness=None
    )


def _load_index(store: PanelStore, as_of: datetime) -> IndexMembership:
    return load_index_membership(
        store, index_code=INDEX_CODE, years=(YEAR,), as_of=as_of, max_staleness=None
    )


def _load_industry(store: PanelStore, as_of: datetime) -> Mapping[str, SecurityIndustryHistory]:
    return load_industry_histories(store, years=(YEAR,), as_of=as_of, max_staleness=None)


@dataclass(frozen=True, slots=True, kw_only=True)
class _LookAheadInjection:
    """One issue's vector: what to inject, where, and which read is supposed to refuse.

    A table rather than three hand-written pairs, for `panel_gate.GATE_CODE_BLOCKS`' reason --
    a dataset that stopped refusing shows up as one row's diff. `V2-P2-009` is the issue that
    turns this into the repository's injection register; this is its first three entries and
    the shape they should keep.
    """

    issue: str
    shape: str
    dataset: str
    future_rows: int
    refusal: str
    load: Callable[[PanelStore, datetime], object]


LOOKAHEAD_INJECTIONS = (
    _LookAheadInjection(
        issue="V2-P2-001",
        shape="financials.announced_after_the_as_of",
        dataset=INCOME_DATASET,
        future_rows=1,
        refusal=r"the income panel cannot be read at .*\['not_yet_knowable'\]",
        load=_load_income,
    ),
    _LookAheadInjection(
        issue="V2-P2-003",
        shape="index.publication_after_the_as_of",
        dataset=INDEX_WEIGHT_DATASET,
        future_rows=2,
        refusal=r"000300\.SH's composition cannot be read at .*\['not_yet_knowable'\]",
        load=_load_index,
    ),
    _LookAheadInjection(
        issue="V2-P2-004",
        shape="industry.reclassification_after_the_as_of",
        dataset=INDUSTRY_MEMBERSHIP_DATASET,
        future_rows=1,
        refusal=r"the industry classification cannot be read at .*\['not_yet_knowable'\]",
        load=_load_industry,
    ),
)
"""The three vectors, one per issue.

`future_rows` is 2 for `index_weight` and 1 for the other two because a publication is a set of
rows by construction -- `build_index_publication` refuses a constituent set whose weights do not
sum to 100 -- so the injectable unit there is a publication, not a row. It is declared rather
than derived so that a generator change which quietly widened an injection fails here.
"""


def _ids(injection: _LookAheadInjection) -> str:
    return injection.issue


def _rows_after(panel: GeneratedPanel, dataset: str) -> int:
    return sum(
        1 for available in panel.batch(dataset).timeline.available_time if available > panel.as_of
    )


def _knowable_at(panel: GeneratedPanel, dataset: str) -> datetime:
    """The instant the injected row(s) became knowable -- the partition's newest availability."""
    return max(panel.batch(dataset).timeline.available_time)


def _without_the_rows_the_read_cannot_see(
    panel: GeneratedPanel, dataset: str, *, as_of: datetime = AS_OF
) -> GeneratedPanel:
    """`panel` with `dataset`'s unknowable rows dropped and nothing else touched.

    Rebuilt through `ColumnarPanelBatch` itself, so the subject column, all four clocks and
    every data column still have to line up and the digest is recomputed -- the pruned batch is
    a batch the contract accepts, not a hand-made record. Its `as_of`/`fetched_at` move back to
    the read, which is what a corpus fetched *at* the read would carry; the partition's
    `max_available_time` is computed from the rows either way, so this cannot be what makes the
    pruned read pass.
    """
    batch = panel.batch(dataset)
    keep = [
        index for index, available in enumerate(batch.timeline.available_time) if available <= as_of
    ]
    timeline = batch.timeline
    pruned = ColumnarPanelBatch(
        provider_id=batch.provider_id,
        dataset=batch.dataset,
        kind=batch.kind,
        as_of=as_of,
        fetched_at=as_of,
        status="success",
        subjects=tuple(batch.subjects[index] for index in keep),
        timeline=TimelineColumns(
            event_time=tuple(timeline.event_time[index] for index in keep),
            available_time=tuple(timeline.available_time[index] for index in keep),
            ingested_time=tuple(timeline.ingested_time[index] for index in keep),
            revision_time=tuple(timeline.revision_time[index] for index in keep),
        ),
        columns=tuple(
            PanelColumn(column.name, column.kind, tuple(column.values[index] for index in keep))
            for column in batch.columns
        ),
        source_uri=batch.source_uri,
    )
    return replace(panel, batches={**panel.batches, dataset: pruned})


def _stored(root: Path, panel: GeneratedPanel, dataset: str) -> PanelStore:
    """One dataset of `panel`, through the real `panel_ingest` writer, into a fresh store."""
    store = PanelStore(root / "panel")
    write_generated_panel(store, panel, datasets=(dataset,))
    return store


# --- the three pairs, one partition and one as_of each ---------------------------------------


@pytest.mark.parametrize("injection", LOOKAHEAD_INJECTIONS, ids=_ids)
def test_a_row_the_read_cannot_see_blocks_it_and_the_same_partition_without_that_row_does_not(
    injection: _LookAheadInjection, tmp_path: Path
) -> None:
    """The pair `V2-P2-001`, `003` and `004` each reduce to.

    Same dataset, same partition year, same `as_of`, same writer. The only difference between
    the two stores is the rows whose `available_time` is after the read -- so a refusal that
    survived their removal would be the year's granularity talking, and one that did not is the
    row's. The refusal is required to name both the rule and the instant the injected row
    carries, because "blocked" alone is satisfied by every other readiness code too."""
    panel = generate_panel(shapes=(injection.shape,))
    pruned = _without_the_rows_the_read_cannot_see(panel, injection.dataset)
    knowable = _knowable_at(panel, injection.dataset)

    injected_store = _stored(tmp_path / "injected", panel, injection.dataset)
    pruned_store = _stored(tmp_path / "pruned", pruned, injection.dataset)

    assert _rows_after(panel, injection.dataset) == injection.future_rows
    assert _rows_after(pruned, injection.dataset) == 0
    assert (
        panel.batch(injection.dataset).row_count - pruned.batch(injection.dataset).row_count
        == injection.future_rows
    )
    assert knowable > AS_OF
    with pytest.raises(PanelStorageError, match=injection.refusal) as refused:
        injection.load(injected_store, AS_OF)
    assert knowable.isoformat() in str(refused.value)
    assert injection.load(pruned_store, AS_OF) is not None


@pytest.mark.parametrize("injection", LOOKAHEAD_INJECTIONS, ids=_ids)
def test_the_read_opens_at_the_instant_the_row_became_knowable_and_not_a_microsecond_before(
    injection: _LookAheadInjection, tmp_path: Path
) -> None:
    """`evaluate_readiness` compares `max_available_time > as_of`, so the boundary is inclusive
    on the read side exactly as `is_visible_at` is on the write side. One partition, two
    `as_of`s a microsecond apart: a test that only moved the clock to "much later" would pass
    against a rule that had drifted to `>=`, or to comparing dates instead of instants."""
    panel = generate_panel(shapes=(injection.shape,))
    knowable = _knowable_at(panel, injection.dataset)

    store = _stored(tmp_path, panel, injection.dataset)

    assert injection.load(store, knowable) is not None
    with pytest.raises(PanelStorageError, match=injection.refusal):
        injection.load(store, knowable - timedelta(microseconds=1))


@pytest.mark.parametrize("injection", LOOKAHEAD_INJECTIONS, ids=_ids)
def test_the_injection_is_confined_to_its_own_dataset(injection: _LookAheadInjection) -> None:
    """No shape may inject sideways. Each of the three aims at one partition, and a shape that
    also pushed another dataset's `max_available_time` past the read would make the pair above
    pass for two reasons at once -- which is how a fixture stops discriminating."""
    panel = generate_panel(shapes=(injection.shape,))

    assert {dataset: _rows_after(panel, dataset) for dataset in sorted(panel.batches)} == {
        dataset: (injection.future_rows if dataset == injection.dataset else 0)
        for dataset in sorted(panel.batches)
    }


# --- V2-P2-001: a filing announced after the read --------------------------------------------


def test_a_reader_at_the_as_of_gets_the_version_that_had_been_announced_and_no_other(
    tmp_path: Path,
) -> None:
    """The other half of `V2-P2-001`. Refusing the partition is only correct if the withheld row
    would in fact have changed the answer, so the restatement has to be a real one: `777.0`
    against the `100.0` the original filing carries in the same column.

    The panel gate and `filing_for` are two different clocks -- one compares instants, the other
    plain dates -- and this asserts both. A reader allowed to see the partition (`as_of` at the
    row's own availability) still gets `100.0` for a day before the restatement's `ann_date` and
    `777.0` from that day on; a reader standing at `AS_OF`, with the row pruned, has only the
    one filing and gets `100.0` for both days."""
    panel = generate_panel(shapes=("financials.announced_after_the_as_of",))
    pruned = _without_the_rows_the_read_cannot_see(panel, INCOME_DATASET)
    code = panel.securities[0]
    knowable = _knowable_at(panel, INCOME_DATASET)

    full = _load_income(_stored(tmp_path / "injected", panel, INCOME_DATASET), knowable)
    withheld = _load_income(_stored(tmp_path / "pruned", pruned, INCOME_DATASET), AS_OF)

    history = full[code]
    assert history.filing_for(BASE_PERIOD, BASE_ANNOUNCEMENT).value_of(DISPUTED_COLUMN) == (
        BASE_VALUE
    )
    assert history.filing_for(BASE_PERIOD, FUTURE_ANNOUNCEMENT).value_of(DISPUTED_COLUMN) == (
        THIRD_VERSION_VALUE
    )
    blind = withheld[code]
    assert len(blind.filings_on(FUTURE_ANNOUNCEMENT)) == 1
    assert blind.filing_for(BASE_PERIOD, FUTURE_ANNOUNCEMENT).value_of(DISPUTED_COLUMN) == (
        BASE_VALUE
    )


# --- V2-P2-003: a publication dated after the read -------------------------------------------


def test_a_membership_read_before_the_next_publication_answers_from_the_earlier_one(
    tmp_path: Path,
) -> None:
    """The other half of `V2-P2-003`, and the reason the block matters here rather than being a
    formality: the injected publication is a **rebalance**, so a read that leaked it would hand
    a January question a February membership.

    `publication_in_effect_on` is a business clock -- it looks up `published_on` and knows
    nothing about `as_of` -- so the only thing standing between a reader and the next
    composition is the partition gate. With the row pruned, the membership's horizon closes at
    the January publication and a question about the February one is refused by name rather
    than answered from the stale snapshot; with it, the January answer is *still* January's.

    **Not the same hazard as `scheduled_review_takes_effect_before_it_is_published`.** That one
    is the publisher's June/December review taking effect after the second Friday's close while
    `index_weight` does not carry the new list until month end -- 49,900 (name x session)
    answers naming a security that had already gone, a look-ahead *inside* the answer that no
    `as_of` can refuse. This injection is the other direction: a publication that exists in the
    store and must not be visible yet, which the `as_of` gate can and does refuse."""
    panel = generate_panel(shapes=("index.publication_after_the_as_of",))
    pruned = _without_the_rows_the_read_cannot_see(panel, INDEX_WEIGHT_DATASET)
    january, first, second, third = (
        panel.sessions[-1],
        panel.securities[0],
        panel.securities[1],
        panel.securities[2],
    )

    full = _load_index(
        _stored(tmp_path / "injected", panel, INDEX_WEIGHT_DATASET),
        _knowable_at(panel, INDEX_WEIGHT_DATASET),
    )
    withheld = _load_index(_stored(tmp_path / "pruned", pruned, INDEX_WEIGHT_DATASET), AS_OF)

    assert full.publication_dates == (january, FUTURE_PUBLICATION)
    assert full.constituents_on(january).members == (first, second)
    assert full.constituents_on(FUTURE_PUBLICATION).members == (second, third)
    (rebalance,) = full.rebalances()
    assert (rebalance.added, rebalance.removed) == ((third,), (first,))
    assert withheld.publication_dates == (january,)
    assert withheld.constituents_on(january).members == (first, second)
    with pytest.raises(IndexMembershipHorizonError, match=r"is after 000300\.SH's last publica"):
        withheld.constituents_on(FUTURE_PUBLICATION)


# --- V2-P2-004: an assignment that takes effect after the read -------------------------------


def test_an_industry_read_before_the_reclassification_refuses_the_day_it_covers(
    tmp_path: Path,
) -> None:
    """The other half of `V2-P2-004`. `index_member_all` carries no announcement column, so
    inside a vintage's era an assignment's availability *is* its `in_date`
    (`KNOWN_INDUSTRY_LIMITATIONS.no_announcement_and_no_revision_history`) -- which makes "a
    reclassification the read cannot see" exactly "an `in_date` after the `as_of`".

    The pruned partition still carries the old interval's **closing** row, dated the panel's
    last session and knowable well before the read, so what the reader loses is only the new
    interval. `assignment_on` then refuses the day the new assignment covers instead of
    carrying the superseded label across it -- the fail-closed direction the module's own
    `a_partial_year_read_cannot_see_an_interval_close` limitation is written about.

    **Not the same hazard as the SW2021 backfill.** `index_member_all` erased the 2021-12-13
    revision -- 1 of its 7,893 rows carries that `in_date` -- so 1,038,252 of the 9,566,861
    measurable (name x session) pairs before it are answered with a taxonomy published later,
    10.85%. That is a revision written *backwards* into rows whose `in_date` is decades old,
    which no `as_of` can see and this gate cannot refuse. What is injected here is the forward
    case: a row whose own `in_date` is after the read."""
    panel = generate_panel(shapes=("industry.reclassification_after_the_as_of",))
    pruned = _without_the_rows_the_read_cannot_see(panel, INDUSTRY_MEMBERSHIP_DATASET)
    code = panel.securities[2]

    full = _load_industry(
        _stored(tmp_path / "injected", panel, INDUSTRY_MEMBERSHIP_DATASET),
        _knowable_at(panel, INDUSTRY_MEMBERSHIP_DATASET),
    )
    withheld = _load_industry(
        _stored(tmp_path / "pruned", pruned, INDUSTRY_MEMBERSHIP_DATASET), AS_OF
    )

    assert full[code].assignment_on(RECLASSIFIED_THROUGH).l1_code == INDUSTRY_L1[0]
    assert full[code].assignment_on(RECLASSIFIED_FROM).l1_code == INDUSTRY_L1[1]
    assert withheld[code].assignment_on(RECLASSIFIED_THROUGH).l1_code == INDUSTRY_L1[0]
    assert withheld[code].is_classified_on(RECLASSIFIED_FROM) is False
    with pytest.raises(
        IndustryClassificationError,
        match=(
            rf"{RECLASSIFIED_FROM} is after {re.escape(code)}'s last assignment, "
            rf"which ended {RECLASSIFIED_THROUGH}"
        ),
    ):
        withheld[code].assignment_on(RECLASSIFIED_FROM)


def test_the_injected_dates_are_after_the_read_and_inside_the_same_partition_year() -> None:
    """A guard against the three pairs above passing for a reason that has nothing to do with
    look-ahead. `AS_OF` is 2026-01-17 and every one of these partitions is a 2026 partition, so
    if the generator's baseline rows were themselves unknowable at the read the pruned half
    would fail rather than pass -- and if the injected dates drifted out of 2026 the blocked
    half would be the partition-granularity refusal roadmap section 11 warns about, wearing the
    same code. Both are pinned as dates, in the module that owns them."""
    clean = generate_panel()

    assert datetime(2026, 1, 17, 4, 0, tzinfo=UTC) == AS_OF
    assert {FUTURE_ANNOUNCEMENT.year, FUTURE_PUBLICATION.year, RECLASSIFIED_FROM.year} == {YEAR}
    assert min(FUTURE_ANNOUNCEMENT, FUTURE_PUBLICATION, RECLASSIFIED_FROM) > AS_OF.date()
    assert AS_OF.date() > RECLASSIFIED_THROUGH
    assert {dataset: _rows_after(clean, dataset) for dataset in clean.batches} == {
        dataset: 0 for dataset in clean.batches
    }
