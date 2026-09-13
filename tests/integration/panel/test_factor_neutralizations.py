"""The neutralisation against a real panel (`V2-P3-004`).

The acceptance is D8's third tier: *"报告可比较 raw / processed / neutralized 表现而不覆盖源观测"*.
Each half is asserted here against partitions on disk rather than against in-memory objects,
because the round trip is where a column that was never written, or one that decodes into the
wrong field, becomes visible.

## The five claims this file exists to hold

1. **Three tiers coexist and none is written over another.** The raw and processed partitions are
   asserted **byte-identical** before and after a neutralisation is written over the same year,
   and all three are read back in one test.
2. **A neutralised row names the exact processed row it came from, which names the exact raw
   row.** Both hops are performed as joins rather than described.
3. **The two foreign inputs come off the real store, and the arithmetic layer never sees one.**
   `load_industry_market_cap_cross_section` is driven against real partitions, and the industry a
   security gets is the one the *stored assignments* imply on that session -- including the
   hand-over that moves one name into a group of its own.
4. **What determines the answers is either in the identity or exempted by name**, audited off
   `apply_factor_neutralization`'s own signature.
5. **Every one of the 34 stored manifest columns is read back and held to a value.** `V2-P3-003`'s
   review measured what the absence of that costs: 23 stored columns whose values were never
   asserted could all be written as constants with 233 tests staying green.

## The frame, which is **two** panels and not one

The generated panel's eight securities. `SHAPES` requests two industry shapes so that the session
at `SESSION` carries seven names in one industry and one alone in another, which is
`thin_industry` under a floor of 2. The fixture's `daily_basic` writes `total_mv = 1.0` on every
row, which is a design with no within-industry dispersion at all -- so the market caps are
replaced with a spread before the panel is written, and the untouched version is kept for the
`degenerate_design` test.

`WIDE_SHAPES` adds a third and gives the same session **two admitted industries**, 2 names and 6,
with visibly different mean capitalisations. That second fixture is not a variant for its own
sake: with one admitted group the industry dummies decide nothing, so an engine that discarded
every industry code kept this file green. See `WIDE_SHAPES` for the measurement and for the two
tests that now fail on it.
"""

from __future__ import annotations

import dataclasses
import inspect
import math
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any, Final
from zoneinfo import ZoneInfo

import pytest
from panel_fixtures import (
    DAILY_BASIC_DATASET,
    INDUSTRY_MEMBERSHIP_DATASET,
    SECURITIES,
    YEAR,
    GeneratedPanel,
    generate_panel,
    write_generated_panel,
)

from openalpha_cn import panel_factors, panel_neutralization
from openalpha_cn.backtest.factor_ic import MINIMUM_IC_AS_OFS
from openalpha_cn.domain.daily_prices import DAILY_DATASET
from openalpha_cn.domain.factor_neutralization import (
    FactorNeutralizationError,
    FactorNeutralizationSpec,
    IndustryMarketCapCrossSection,
    NeutralizedFactorObservation,
    SecurityCharacteristic,
    build_industry_market_cap_cross_section,
)
from openalpha_cn.domain.factor_transform import (
    FactorTransformSpec,
    MissingValuePolicy,
    WinsorizationPolicy,
)
from openalpha_cn.domain.industry_classification import (
    INDUSTRY_MEMBERSHIP_TAXONOMY,
    INDUSTRY_TAXONOMY_EFFECTIVE_FROM,
    IndustryAnswer,
    IndustryAssignment,
)
from openalpha_cn.domain.panel_batch import (
    SUBJECT_COLUMN_NAME,
    ColumnarPanelBatch,
    PanelColumn,
    TimelineColumns,
)
from openalpha_cn.panel.catalog import (
    DEFAULT_DATE_TIMEZONE,
    PanelStorageError,
    ReadinessRequirement,
)
from openalpha_cn.panel.store import PanelStore
from openalpha_cn.panel_factors import (
    REVERSAL_1D,
    FactorEngineError,
    FactorPanel,
    ProcessedFactorPanel,
    apply_factor_transform,
    compute_factor,
    factor_observation_dataset,
    load_factor_observations,
    load_processed_factor_observations,
    processed_factor_dataset,
    write_factor_panels,
    write_processed_factor_panels,
)
from openalpha_cn.panel_ingest import (
    daily_requirement,
    load_daily_valuations,
    load_industry_histories,
    write_panel_batch,
)
from openalpha_cn.panel_neutralization import (
    INDUSTRY_AND_SIZE,
    NEUTRALIZATION_MANIFEST_DATA_COLUMNS,
    NEUTRALIZATION_MANIFEST_PANEL_COLUMNS,
    NeutralizedFactorPanel,
    apply_factor_neutralization,
    factor_neutralization_manifest_dataset,
    load_factor_neutralization_manifests,
    load_industry_market_cap_cross_section,
    load_neutralized_factor_observations,
    neutralized_factor_dataset,
    write_neutralized_factor_panels,
)

AS_OF: Final[datetime] = datetime(2026, 1, 17, 4, 0, tzinfo=UTC)
"""The fixture panel's own `as_of`: after the last session's `daily_basic` became knowable.

**It used to be the only instant that worked, and `V2-P4-026` changed that.** When this file was
written both foreign inputs went through loaders taking `PanelStore.read_if_ready`, whose
`not_yet_knowable` verdict is decided on a partition's **max** `available_time`, so a
`daily_basic` year was unreadable at every `as_of` inside the year it covers.
`load_daily_valuations` now reads one session at a time under an availability predicate, and
`test_a_mid_year_as_of_assembles_the_cross_section_on_a_partition_holding_a_later_revision`
drives the reversal. This constant stays what it is because every test below it was written
against a year-end build and comparing them is the point; the in-year instants are
`MID_YEAR_BUILD` and `LATER_BUILD`.

**`V2-P4-028` then did the same to the industry corpus**, so the sentence that used to stand
here -- "what has *not* changed is that `index_member_all` is still read whole partition" -- is
gone too. `load_industry_market_cap_cross_section` reads through
`panel_ingest.load_industry_cross_section`, and `wide_store`, which was this file's demonstration
of the refusal at `MID_WINDOW`, is now its demonstration of the answer.
"""

MID_WINDOW: Final[datetime] = datetime(2026, 1, 12, 4, 0, tzinfo=UTC)
"""Noon Asia/Shanghai on the sixth of ten sessions.

Five sessions had published at this instant (through 2026-01-09) and five had not, including the
12th's own. `V2-P4-026` turned this constant from "the instant that proves the whole build is
refused" into the instant every mid-year test is taken at.
"""

MID_YEAR_BUILD: Final[datetime] = datetime(2026, 1, 12, 9, 0, tzinfo=UTC)
"""17:00 Asia/Shanghai on 2026-01-12: after that session's own 16:30, four sessions before the
window ends. The instant a whole three-tier build is run at, in the year the panel covers."""

MID_YEAR_SESSION: Final[date] = date(2026, 1, 12)
"""`MID_YEAR_BUILD`'s own session -- the day the residuals built at that instant are about."""

LATER_BUILD: Final[datetime] = datetime(2026, 1, 13, 9, 0, tzinfo=UTC)
LATER_SESSION: Final[date] = date(2026, 1, 13)
"""A second in-year build instant and its session, so `min_as_ofs = 2` has two points to be
satisfied by inside one covered year. See
`test_two_in_year_builds_give_the_ic_floor_of_two_as_ofs_two_points_inside_one_year`.

**The pair was 01-12/01-13 rather than 01-09/01-12 because of a constraint `V2-P4-028` removed,
and the constant stays where it is.** `SHAPES` opens an assignment on 2026-01-12, so
`index_member_all`'s 2026 partition has a `max_available_time` of 2026-01-11T16:00Z, and while
this builder read through `load_industry_histories` every earlier `as_of` was refused whole with
`not_yet_knowable`. It is not any more --
`test_across_the_whole_window_only_the_industry_read_ever_refuses_an_in_year_as_of` measures that
all ten sessions of the window now assemble -- so the pair is free to move and is deliberately
not moved: every test below was written against these two instants, and re-pointing them would
change what those tests compare for no gain here.
"""

UNBLOCKED_BUILD: Final[datetime] = datetime(2026, 1, 9, 9, 0, tzinfo=UTC)
UNBLOCKED_SESSION: Final[date] = date(2026, 1, 9)
"""17:00 Asia/Shanghai on 2026-01-09, and the session it is about.

Strictly before `wide_store`'s 2026 membership partition's newest `available_time`
(2026-01-13T16:00Z, the assignment opening 2026-01-14), which is the condition `read_if_ready`
decides `not_yet_knowable` on -- so `load_industry_histories` refuses this store at this instant
and `test_a_residual_is_computed_at_an_instant_the_unfiltered_door_still_refuses` requires it to.
"""

ACROSS_THE_DATE_LINE: Final[datetime] = datetime(2026, 1, 12, 17, 0, tzinfo=UTC)
"""01:00 Asia/Shanghai on 2026-01-13, which is still 2026-01-12 in UTC.

The one instant on this window where the panel's own zone and UTC disagree about what day it is,
and therefore the only one at which `date_timezone` can be shown to reach the industry read. See
`test_the_declared_date_zone_reaches_the_industry_read_and_not_only_the_price_read`.
"""

HALTED_ON_THE_NINTH: Final[tuple[str, ...]] = ("601318.SH",)
"""The name the fixture halts on 2026-01-09, so `daily_basic` carries **no row** for it there.

Named rather than inlined because it is the load-bearing half of this file's answer to
`tests/unit/panel/test_visible_read_callers.py`'s objection: an absent row and a withheld session
are two different answers, and this constant is the absent one.
"""

SESSION: Final[date] = date(2026, 1, 16)
"""The last session of the fixture's window, and the day the two foreign inputs are read for.

A *session* rather than the `as_of`'s own calendar day, because `as_of` is a Saturday and
`load_daily_valuations` refuses a day the exchange was shut before touching a partition. Choosing
it is the caller's job for `compute_factor`'s reason -- a builder that derived it would be putting
a second policy decision inside an engine.
"""

BUILT_AT: Final[datetime] = datetime(2026, 3, 1, 9, 0, tzinfo=UTC)
COMMIT: Final[str] = "a1b2c3d"
INPUT_STALENESS_BOUND: Final[timedelta] = timedelta(days=5)

SHAPES: Final[tuple[str, ...]] = (
    "daily.close_moves_between_sessions",
    "industry.session_adjacent_handover",
)
"""Two shapes, and the second is what gives the session at `SESSION` two group sizes.

`industry.session_adjacent_handover` moves `SECURITIES[0]` into the second industry from
2026-01-12 and leaves it there, so on 2026-01-16 it is the only member of that group --
`thin_industry` under a floor of 2, while the other seven share the first industry.

`industry.coverage_hole` is not requested *here*, because this shape set is the one that produces
a `thin_industry` row: `SECURITIES[0]` alone in the second group is what makes the member floor
visible. `WIDE_SHAPES` below adds the coverage hole precisely to get the *other* arrangement, and
the two fixtures are both needed -- see that constant.

`industry_missing` is exercised through a narrowed cross section, exactly as `market_cap_missing`
is.
"""

WIDE_SHAPES: Final[tuple[str, ...]] = (*SHAPES, "industry.coverage_hole")
"""The same panel with a **second admitted industry**, which `SHAPES` alone cannot produce.

`industry.coverage_hole` puts `SECURITIES[1]` into the second industry from 2026-01-14, so on
2026-01-16 that group holds `SECURITIES[0]` **and** `SECURITIES[1]` -- two members, which clears a
floor of 2 -- while the other six share the first. Two groups of 2 and 6, and their mean
capitalisations are 2,375,000 against 5,375,000 because `CAP_BASE + CAP_STEP * index` is monotone
in the fixture's own security order and the two names in the small group are its first two.

**This fixture exists because its absence was a measured hole rather than because two groups are
tidier than one.** Under `SHAPES`, every path that reaches `apply_factor_neutralization` regresses
exactly **one** admitted group: the seven-name industry, with `SECURITIES[0]` coded
`thin_industry`. A group-demeaning engine and one that demeaned the whole cross section produce
identical residuals on a single group, so
`fit = _neutralize(subjects, ["_ONE_" for _ in groups], regressor, values)` -- an engine that
throws every industry code away and keeps only the size regression -- left all 146 tests in this
issue's three files green.

That mutation is not harmless anywhere else. On `test_factor_neutralization_rules._panel`'s
5,534-name cross section it moves residuals by 0.12..0.16 against a residual deviation of 1.00 --
and that probe assigns industries **at random**, so it carries no industry effect at all and is
the least favourable case the mutation has. On this fixture the gap is larger than the residuals'
own dispersion, which
`test_the_residuals_are_the_two_group_regression_the_stored_inputs_imply` asserts as a number
rather than describing. That test is what now fails on the mutation, and it fails on hand-computed
per-group residuals rather than on a shape.

The same single group is why `smallest_industry_size` and `largest_industry_size` were both 7 in
every stored manifest this file wrote, so swapping the two expressions that compute them changed
no assertion either;
`test_the_two_industry_size_columns_are_two_answers_and_not_one_on_a_two_group_market` is that
mutation's detector, and 2 against 6 is what makes it one.
"""

LONE: Final[str] = SECURITIES[0]
MEMBERSHIP_YEARS: Final[tuple[int, ...]] = (YEAR,)

OBSERVATIONS: Final[str] = factor_observation_dataset(REVERSAL_1D)
PROCESSED: Final[str] = processed_factor_dataset(REVERSAL_1D)
NEUTRALIZED: Final[str] = neutralized_factor_dataset(REVERSAL_1D)
NEUTRALIZATION_MANIFESTS: Final[str] = factor_neutralization_manifest_dataset(REVERSAL_1D)

CAP_BASE: Final[float] = 2_000_000.0
CAP_STEP: Final[float] = 750_000.0
"""The replacement `total_mv` grid: `CAP_BASE + CAP_STEP * index` over the eight names.

Distinct per security and monotone in the fixture's own security order, so the expected residuals
below are derivable from the order rather than copied out of a run. The fixture writes `1.0` on
every row, which is a design with **zero** within-industry dispersion -- `degenerate_design` --
and that version is kept for the test that asserts it.
"""


def _transform_spec(**overrides: Any) -> FactorTransformSpec:
    """A probe transform whose floor is 1, so the eight-name fixture panel can be processed."""
    settings: dict[str, Any] = {
        "key": "probe_zscore",
        "version": 1,
        "winsorization": WinsorizationPolicy(method="none"),
        "standardization": "zscore",
        "missing_values": MissingValuePolicy(
            not_in_universe="exclude",
            insufficient_history="exclude",
            ambiguous_filing="exclude",
            input_missing="exclude",
            undefined_value="exclude",
        ),
        "min_cross_section": 1,
        **overrides,
    }
    return FactorTransformSpec(**settings)


def _spec(**overrides: Any) -> FactorNeutralizationSpec:
    """A probe neutralisation whose floors fit an eight-name panel.

    `min_cross_section=2` and `min_industry_members=2`: the second is the contract's own floor and
    is what makes `SECURITIES[0]`'s one-member industry a `thin_industry` rather than a stored
    zero. The shipped `INDUSTRY_AND_SIZE` has a floor of 100 and is exercised separately, on the
    only assertion an eight-name panel can make about it.
    """
    settings: dict[str, Any] = {
        "key": "probe_neutral",
        "version": 1,
        "industry_level": "L1",
        "market_cap_measure": "total_mv",
        "market_cap_scale": "log",
        "participation": "measured_only",
        "min_industry_members": 2,
        "min_cross_section": 2,
        **overrides,
    }
    return FactorNeutralizationSpec(**settings)


def _with_market_caps(panel: GeneratedPanel) -> GeneratedPanel:
    """The generated panel with a `total_mv` that actually varies.

    Rebuilt off the fixture's own batch rather than hand-written, so every other column -- the
    closes `write_daily_panel` cross-checks against `daily`, the four clocks, the subjects -- is
    the fixture's and the only thing this function changes is the regressor.
    """
    batch = panel.batch(DAILY_BASIC_DATASET)
    caps = tuple(CAP_BASE + CAP_STEP * SECURITIES.index(str(subject)) for subject in batch.subjects)
    columns = tuple(
        PanelColumn(column.name, column.kind, caps) if column.name == "total_mv" else column
        for column in batch.columns
    )
    replaced: ColumnarPanelBatch = dataclasses.replace(batch, columns=columns)
    return dataclasses.replace(panel, batches={**panel.batches, DAILY_BASIC_DATASET: replaced})


WITHHELD_CLOSE: Final[date] = date(2026, 1, 6)
"""The event date of `SECURITIES[1]`'s **closing** row under `WIDE_SHAPES`.

`industry.coverage_hole` closes that security's first assignment here and opens its second on
2026-01-14. The close is the row a withholding fixture has to reach: withholding the *opening*
row of an interval that starts after `day` changes no answer at all, which is the property this
whole door rests on, so a fixture that moved it would refuse nothing and prove nothing.
"""

WITHHELD_UNTIL: Final[datetime] = datetime(2026, 1, 13, 16, 0, tzinfo=UTC)
"""Where `_with_a_withheld_membership_close` moves that row's `available_time`.

Chosen to equal the partition's existing `max_available_time` -- 2026-01-14 midnight
Asia/Shanghai, the 01-14 opening row's own instant -- so the doctored partition and the honest one
are indistinguishable from their catalog records and differ only in *which* rows a predicate at
`MID_WINDOW` returns.
"""


def _with_a_withheld_membership_close(panel: GeneratedPanel) -> GeneratedPanel:
    """`panel` with `SECURITIES[1]`'s 2026-01-06 closing row unavailable until `WITHHELD_UNTIL`.

    `providers/tushare.py::_taxonomy_backfill_timeline` dates a membership row's availability at
    its own event floored at the taxonomy, so this partition is one no provider in this repository
    can produce -- which is the point. The census check exists because that clock lives one
    package away and nothing in the store enforces it, and a threat model asserted only against
    corpora that satisfy it is asserted against nothing. `event_time` is left where the generator
    put it, so the census still counts the row on 2026-01-06.
    """
    batch = panel.batch(INDUSTRY_MEMBERSHIP_DATASET)
    timeline = batch.timeline
    zone = ZoneInfo(DEFAULT_DATE_TIMEZONE)
    withheld = tuple(
        subject == SECURITIES[1] and event.astimezone(zone).date() == WITHHELD_CLOSE
        for subject, event in zip(batch.subjects, timeline.event_time, strict=True)
    )
    assert sum(withheld) == 1, (
        "this fixture withholds exactly one row and needs the shape set to carry it; "
        f"{sum(withheld)} rows of {batch.subjects.count(SECURITIES[1])} matched"
    )
    moved = TimelineColumns(
        event_time=timeline.event_time,
        available_time=tuple(
            WITHHELD_UNTIL if hidden else original
            for hidden, original in zip(withheld, timeline.available_time, strict=True)
        ),
        ingested_time=tuple(
            max(WITHHELD_UNTIL, original) if hidden else original
            for hidden, original in zip(withheld, timeline.ingested_time, strict=True)
        ),
        revision_time=tuple(
            max(WITHHELD_UNTIL, original) if hidden else original
            for hidden, original in zip(withheld, timeline.revision_time, strict=True)
        ),
    )
    replaced: ColumnarPanelBatch = dataclasses.replace(batch, timeline=moved)
    return dataclasses.replace(
        panel, batches={**panel.batches, INDUSTRY_MEMBERSHIP_DATASET: replaced}
    )


@pytest.fixture
def panel() -> GeneratedPanel:
    return _with_market_caps(generate_panel(shapes=SHAPES))


@pytest.fixture
def flat_cap_panel() -> GeneratedPanel:
    """The fixture as generated: `total_mv = 1.0` on every row, which is a degenerate design."""
    return generate_panel(shapes=SHAPES)


@pytest.fixture
def store(tmp_path: Path, panel: GeneratedPanel) -> PanelStore:
    built = PanelStore(tmp_path / "panel")
    write_generated_panel(built, panel)
    return built


@pytest.fixture
def wide_panel() -> GeneratedPanel:
    """The panel whose session at `SESSION` carries two admitted industries. See `WIDE_SHAPES`."""
    return _with_market_caps(generate_panel(shapes=WIDE_SHAPES))


@pytest.fixture
def wide_store(tmp_path: Path, wide_panel: GeneratedPanel) -> PanelStore:
    built = PanelStore(tmp_path / "wide")
    write_generated_panel(built, wide_panel)
    return built


def _compute(store: PanelStore, panel: GeneratedPanel, **overrides: Any) -> FactorPanel:
    settings: dict[str, Any] = {
        "as_of": AS_OF,
        "subjects": panel.securities,
        "universe": frozenset(panel.securities),
        "requirements": {
            DAILY_DATASET: daily_requirement(
                panel.calendar(),
                years=(YEAR,),
                as_of=AS_OF,
                max_staleness=INPUT_STALENESS_BOUND,
            )
        },
        "code_commit": COMMIT,
        "built_at": BUILT_AT,
        **overrides,
    }
    return compute_factor(store, REVERSAL_1D, **settings)


def _process(source: FactorPanel) -> ProcessedFactorPanel:
    return apply_factor_transform(source, _transform_spec(), code_commit=COMMIT, built_at=BUILT_AT)


def _cross_section(
    store: PanelStore, panel: GeneratedPanel, spec: FactorNeutralizationSpec | None = None
) -> IndustryMarketCapCrossSection:
    return load_industry_market_cap_cross_section(
        store,
        _spec() if spec is None else spec,
        subjects=panel.securities,
        day=SESSION,
        as_of=AS_OF,
        calendar=panel.calendar(),
        membership_years=MEMBERSHIP_YEARS,
        max_staleness=None,
    )


def _neutralize(
    processed: ProcessedFactorPanel,
    characteristics: IndustryMarketCapCrossSection,
    spec: FactorNeutralizationSpec | None = None,
) -> NeutralizedFactorPanel:
    return apply_factor_neutralization(
        processed,
        _spec() if spec is None else spec,
        characteristics,
        code_commit=COMMIT,
        built_at=BUILT_AT,
    )


def _build(store: PanelStore, panel: GeneratedPanel) -> NeutralizedFactorPanel:
    return _neutralize(_process(_compute(store, panel)), _cross_section(store, panel))


def _partition_bytes(store: PanelStore, dataset: str, year: int) -> bytes:
    """The Parquet file behind one partition, so "untouched" is bytes rather than a claim.

    Reached through the store's own layout (`<root>/<dataset>/<year>/data.parquet`) because the
    point of this helper is to compare what is on disk, which no reader API exposes.
    """
    return (store.root / dataset / str(year) / "data.parquet").read_bytes()


# --- the two foreign inputs, read point-in-time --------------------------------------------------


def test_the_industry_and_size_cross_section_is_the_one_the_stored_panel_implies(
    store: PanelStore, panel: GeneratedPanel
) -> None:
    """The store-side half, driven against real partitions rather than a hand-built value.

    `SECURITIES[0]` handed over to the second industry on this very session, so a builder that
    resolved the assignment by "the last one that started" rather than by the one *covering* the
    day would put it in the first industry and give the panel a single 8-name group. The two
    answers differ on exactly one name and produce entirely different residuals for the other
    seven, because the group mean they are measured against changes.
    """
    cross = _cross_section(store, panel)

    assert cross.as_of == AS_OF
    assert cross.taxonomy == INDUSTRY_MEMBERSHIP_TAXONOMY
    assert cross.industry_level == "L1"
    assert cross.market_cap_measure == "total_mv"
    assert cross.without_industry == ()
    assert cross.without_market_cap == ()
    assert set(cross.subjects()) == set(panel.securities)

    groups = {item.subject: item.industry_code for item in cross.characteristics}
    assert groups[LONE] == "801120.SI"
    assert {groups[code] for code in panel.securities if code != LONE} == {"801780.SI"}
    assert cross.backfilled_count == 0


def test_the_market_caps_are_the_stored_ones_and_the_declared_measure_selects_them(
    store: PanelStore, panel: GeneratedPanel
) -> None:
    """`total_mv` and `circ_mv` are two columns and the spec picks one.

    The fixture writes `circ_mv = 1.0` and this file replaces `total_mv` with a spread, so the two
    measures produce visibly different cross sections off one partition -- which is what makes
    `market_cap_measure` a declaration rather than a formality.
    """
    by_total = _cross_section(store, panel)
    by_circ = _cross_section(store, panel, _spec(market_cap_measure="circ_mv"))

    caps = {item.subject: item.market_cap for item in by_total.characteristics}
    for code in caps:
        assert caps[code] == pytest.approx(CAP_BASE + CAP_STEP * SECURITIES.index(code)), code
    assert {item.market_cap for item in by_circ.characteristics} == {1.0}


def test_a_mid_year_as_of_assembles_the_cross_section_on_a_partition_holding_a_later_revision(
    store: PanelStore, panel: GeneratedPanel, wide_store: PanelStore, wide_panel: GeneratedPanel
) -> None:
    """The reverse pin of `V2-P3-004`'s sharpest constraint, now turned round a second time.

    This test began as `test_a_mid_year_as_of_cannot_assemble_the_second_cross_section_at_all`
    and asserted a `not_yet_knowable` refusal at `MID_WINDOW`. `V2-P4-026` turned over the
    `daily_basic` half; `V2-P4-028` turns over the other one, so the assertion that used to be
    a refusal on `wide_store` is now an answer. Turned round rather than deleted, which is the
    precedent `V2-P3-016` and `V2-P3-017` set for a pin whose subject was solved.

    **The two halves, now the same answer for two different reasons.** `store` holds a 2026
    membership partition whose newest event is 2026-01-12, already knowable at `MID_WINDOW`;
    `wide_store` is the same fixture plus one assignment **opening 2026-01-14**, which put that
    partition's newest `available_time` at 2026-01-13T16:00Z and used to make the whole read
    `not_yet_knowable`. Both assemble now, because `load_industry_cross_section` takes the day as
    an argument and a membership event later than that day cannot change who covered it.

    **The withheld row is withheld and the answer says so.** `SECURITIES[1]`'s assignment closed
    2026-01-06 and its next one opens 2026-01-14 -- a row `MID_WINDOW` cannot see and, on this
    day, must not need to. It lands in `without_industry` on `wide_store`, which is the corpus's
    own answer for 2026-01-09 and is unchanged by the revision this read is inside of.
    """
    section = load_industry_market_cap_cross_section(
        store,
        _spec(),
        subjects=panel.securities,
        day=date(2026, 1, 9),
        as_of=MID_WINDOW,
        calendar=panel.calendar(),
        membership_years=MEMBERSHIP_YEARS,
        max_staleness=None,
    )

    assert section.as_of == MID_WINDOW
    assert section.without_industry == ()
    assert {item.subject: item.market_cap for item in section.characteristics} == {
        subject: CAP_BASE + CAP_STEP * SECURITIES.index(subject)
        for subject in panel.securities
        if subject not in HALTED_ON_THE_NINTH
    }

    # The one name the read does *not* answer for is the one the fixture halts on that session,
    # so `daily_basic` carries no row for it at all. That is the whole objection to a filtered
    # read arriving as its own answer: an **absent** row lands in `without_market_cap` and a
    # **withheld** session raises, and the two are not the same shape. The seven that are here
    # are here with the values the stored partition holds, not merely present.
    assert section.without_market_cap == HALTED_ON_THE_NINTH

    revised = load_industry_market_cap_cross_section(
        wide_store,
        _spec(),
        subjects=wide_panel.securities,
        day=date(2026, 1, 9),
        as_of=MID_WINDOW,
        calendar=wide_panel.calendar(),
        membership_years=MEMBERSHIP_YEARS,
        max_staleness=None,
    )

    assert revised.as_of == MID_WINDOW
    assert revised.without_industry == (SECURITIES[1],)
    assert revised.without_market_cap == HALTED_ON_THE_NINTH
    assert {item.subject for item in revised.characteristics} == set(wide_panel.securities) - {
        SECURITIES[1],
        *HALTED_ON_THE_NINTH,
    }


def test_a_withheld_membership_row_and_an_absent_one_are_two_answers_on_this_builder(
    wide_store: PanelStore, wide_panel: GeneratedPanel, tmp_path: Path
) -> None:
    """The `V2-P4-034` standard, driven on the builder rather than on the read one layer down.

    Both situations reach this function as "no industry for this security on this day", and on
    `wide_store` they arrive from the same security:

    - **absent** -- `SECURITIES[1]`'s assignment closed 2026-01-06 and its next opens 2026-01-14,
      so nothing covers 2026-01-09 and nothing ever will. `without_industry`, which is data.
    - **withheld** -- the identical corpus with that **closing** row's `available_time` moved
      past `MID_WINDOW`. A bare row predicate then sees one interval opening at `LISTED_ON` and
      never closing, and answers 2026-01-09 with an industry the corpus says the security had
      already left -- so what ought to be `industry_missing` would have become a stored
      characteristic instead, which is a residual regressed against the wrong group.

    The two stores hold the same rows and the same partition `max_available_time`; they differ in
    one clock on one row. The doctored one is refused **by name**, and the sentinel underneath is
    that the same read of the same store answers once that clock has passed -- and answers with
    `SECURITIES[1]` back in `without_industry`, which is the honest store's answer. So the
    refusal is about visibility rather than about the corpus, and the assertion can tell the two
    apart rather than merely holding on this fixture.
    """
    withheld = PanelStore(tmp_path / "withheld" / "panel")
    write_generated_panel(withheld, _with_a_withheld_membership_close(wide_panel))

    honest = load_industry_market_cap_cross_section(
        wide_store,
        _spec(),
        subjects=wide_panel.securities,
        day=date(2026, 1, 9),
        as_of=MID_WINDOW,
        calendar=wide_panel.calendar(),
        membership_years=MEMBERSHIP_YEARS,
        max_staleness=None,
    )
    assert honest.without_industry == (SECURITIES[1],)

    with pytest.raises(PanelStorageError, match="whose event had already happened"):
        load_industry_market_cap_cross_section(
            withheld,
            _spec(),
            subjects=wide_panel.securities,
            day=date(2026, 1, 9),
            as_of=MID_WINDOW,
            calendar=wide_panel.calendar(),
            membership_years=MEMBERSHIP_YEARS,
            max_staleness=None,
        )

    once_visible = load_industry_market_cap_cross_section(
        withheld,
        _spec(),
        subjects=wide_panel.securities,
        day=date(2026, 1, 9),
        as_of=AS_OF,
        calendar=wide_panel.calendar(),
        membership_years=MEMBERSHIP_YEARS,
        max_staleness=None,
    )
    assert once_visible.without_industry == (SECURITIES[1],)


def test_a_stored_membership_year_the_caller_did_not_name_refuses_the_day_on_this_builder(
    store: PanelStore, panel: GeneratedPanel
) -> None:
    """The whole of what is left of
    `KNOWN_NEUTRALIZATION_LIMITATIONS
    .a_stored_membership_year_left_unread_refuses_the_day_rather_than_answering_it`, driven here.

    `membership_years` is the lever a caller has for narrowing the industry read, and narrowing
    it past the day being priced is the one shape that still refuses a build. An assignment's
    close is filed as its own row in its own year, so a stored year at or before `SESSION` that
    this call did not name can hold the close of an interval the cross section would otherwise
    report as current -- and the refusal names the year to add rather than handing back a cross
    section short by exactly the securities it could not speak for.

    Driven with the sentinel beside it: the identical call naming the year answers, so the
    refusal is about the narrowing and not about the store. `V2-P4-028` is why this is the
    remaining entry -- before it, this call was refused for a reason that had nothing to do with
    which years the caller named.
    """
    with pytest.raises(PanelStorageError, match="did not name"):
        load_industry_market_cap_cross_section(
            store,
            _spec(),
            subjects=panel.securities,
            day=SESSION,
            as_of=AS_OF,
            calendar=panel.calendar(),
            membership_years=(YEAR - 1,),
            max_staleness=None,
        )

    named = load_industry_market_cap_cross_section(
        store,
        _spec(),
        subjects=panel.securities,
        day=SESSION,
        as_of=AS_OF,
        calendar=panel.calendar(),
        membership_years=MEMBERSHIP_YEARS,
        max_staleness=None,
    )
    assert named.without_industry == ()


def test_a_backfilled_label_reaches_the_stored_characteristic_and_is_not_recomputed() -> None:
    """`is_backfilled` travels from `IndustryAnswer` to `SecurityCharacteristic` unchanged.

    **This test exists because a mutation survived, and the survivor is a fixture limit rather
    than a design fault.** Replacing `_industry_answer`'s `answer.is_backfilled` with a literal
    `False` left every test in this file, `test_factor_build.py`, `test_factor_run.py` and
    `test_industry_ingest.py` green -- 249 of them. `IndustryAnswer.is_backfilled` is
    `asked_for < taxonomy_effective_from`, SW2021's date is 2021-12-13, and **every day any
    generated panel prices is in 2026**, so no cross section this repository can assemble from a
    fixture has a backfilled row in it. The whole-build path cannot reach one either: a day before
    2021-12-13 has no `daily_basic` session on a 2026 calendar, so `load_daily_valuations` refuses
    it before any characteristic is built.

    So the fold is driven directly, with both answers in one mapping, rather than through a store
    that cannot express the case. `panel_ingest.load_industry_cross_section`'s own end of it is
    driven against real partitions at
    `tests/integration/panel/test_industry_ingest.py::
    test_a_security_with_no_assignment_covering_the_day_is_left_out_rather_than_raised`, which
    reads a 1995 day and asserts `is_backfilled is True`; what was untested is the two lines
    between that answer and a stored `SecurityCharacteristic`.
    """
    taxonomy_floor = INDUSTRY_TAXONOMY_EFFECTIVE_FROM[INDUSTRY_MEMBERSHIP_TAXONOMY]
    backfilled_day = taxonomy_floor - timedelta(days=1)
    inside_the_era = taxonomy_floor

    def answer(subject: str, day: date) -> IndustryAnswer:
        return IndustryAnswer(
            ts_code=subject,
            asked_for=day,
            assignment=IndustryAssignment(
                ts_code=subject,
                l1_code="801780.SI",
                l2_code="801782.SI",
                l3_code="857821.SI",
                effective_from=date(1993, 1, 1),
            ),
            taxonomy=INDUSTRY_MEMBERSHIP_TAXONOMY,
            taxonomy_effective_from=taxonomy_floor,
        )

    cross_section = {
        SECURITIES[0]: answer(SECURITIES[0], backfilled_day),
        SECURITIES[1]: answer(SECURITIES[1], inside_the_era),
    }

    before = panel_neutralization._industry_answer(cross_section, subject=SECURITIES[0])
    on_the_day = panel_neutralization._industry_answer(cross_section, subject=SECURITIES[1])

    assert before is not None
    assert on_the_day is not None
    assert before[1] is True
    assert on_the_day[1] is False
    assert panel_neutralization._industry_answer(cross_section, subject="000000.SZ") is None


def test_the_declared_date_zone_reaches_the_industry_read_and_not_only_the_price_read(
    store: PanelStore, panel: GeneratedPanel
) -> None:
    """`date_timezone` is a parameter of this builder and both of its reads resolve days with it.

    It used to reach only one. `load_industry_histories` resolved no day at all -- it returned
    histories and left the day to the caller -- so `date_timezone` was `load_daily_valuations`'
    alone, and forgetting to thread it to the industry read was not a mistake anyone could make.
    `V2-P4-028` makes it one: `load_industry_cross_section` decides *which day this read may
    speak for* in the declared zone, and a builder that dropped the argument would silently use
    Asia/Shanghai for one read and the caller's zone for the other.

    Driven at `ACROSS_THE_DATE_LINE` -- 2026-01-12T17:00Z, which is 01-13 in Asia/Shanghai and
    still 01-12 in UTC -- asked about 2026-01-13. The two zones give **two different named
    refusals** off one call, which is what makes this an assertion rather than a shape: in the
    panel's own zone the membership read admits the day and `daily_basic` refuses it because that
    session's 16:30 has not arrived, and in UTC the membership read refuses it first because the
    day is one the `as_of` cannot see at all.
    """
    with pytest.raises(PanelStorageError, match="that session had not published yet"):
        load_industry_market_cap_cross_section(
            store,
            _spec(),
            subjects=panel.securities,
            day=LATER_SESSION,
            as_of=ACROSS_THE_DATE_LINE,
            calendar=panel.calendar(),
            membership_years=MEMBERSHIP_YEARS,
            max_staleness=None,
        )

    with pytest.raises(PanelStorageError, match="becomes knowable at midnight UTC"):
        load_industry_market_cap_cross_section(
            store,
            _spec(),
            subjects=panel.securities,
            day=LATER_SESSION,
            as_of=ACROSS_THE_DATE_LINE,
            calendar=panel.calendar(),
            membership_years=MEMBERSHIP_YEARS,
            max_staleness=None,
            date_timezone="UTC",
        )


def test_a_session_whose_own_close_has_not_published_is_refused_at_a_mid_year_as_of(
    store: PanelStore, panel: GeneratedPanel
) -> None:
    """The new door's fail-closed direction, driven through the neutralisation's own builder.

    `MID_WINDOW` is noon Asia/Shanghai on 2026-01-12, so that session's own 16:30 has not
    arrived. Asking for it is refused by name before any partition is touched, rather than
    answered with a cross section in which every security lands in `without_market_cap` -- which
    is what a bare row predicate would have produced and is indistinguishable from a session
    `daily_basic` genuinely has no rows for.
    """
    with pytest.raises(PanelStorageError, match="that session had not published yet"):
        load_industry_market_cap_cross_section(
            store,
            _spec(),
            subjects=panel.securities,
            day=date(2026, 1, 12),
            as_of=MID_WINDOW,
            calendar=panel.calendar(),
            membership_years=MEMBERSHIP_YEARS,
            max_staleness=None,
        )


def test_an_empty_subject_list_is_refused_rather_than_answered_with_an_empty_cross_section(
    store: PanelStore, panel: GeneratedPanel
) -> None:
    with pytest.raises(FactorEngineError, match="needs at least one subject"):
        load_industry_market_cap_cross_section(
            store,
            _spec(),
            subjects=(),
            day=SESSION,
            as_of=AS_OF,
            calendar=panel.calendar(),
            membership_years=MEMBERSHIP_YEARS,
            max_staleness=None,
        )


# --- the residuals -------------------------------------------------------------------------------


def test_the_residuals_are_the_regression_the_stored_inputs_imply(
    store: PanelStore, panel: GeneratedPanel
) -> None:
    """Every stored residual derived by hand from the two stored cross sections.

    Not a property assertion. The expected numbers are computed here from the processed values and
    the stored `total_mv`s by the same algebra the closed form implements, so an engine that
    demeaned the wrong variable, used the wrong group, or skipped the slope would produce
    different numbers for every security rather than a differently shaped output.

    The six-name industry is the only one regressed: `SECURITIES[0]` is alone in its group and
    below the floor, and `SECURITIES[1]` has no group at all.
    """
    processed = _process(_compute(store, panel))
    cross = _cross_section(store, panel)
    result = _neutralize(processed, cross)

    regressed = [code for code in panel.securities if code != LONE]
    values = {item.subject: item.value for item in processed.observations}
    caps = {item.subject: math.log(item.market_cap) for item in cross.characteristics}
    mean_y = math.fsum(values[code] for code in regressed) / len(regressed)
    mean_x = math.fsum(caps[code] for code in regressed) / len(regressed)
    dy = {code: values[code] - mean_y for code in regressed}
    dx = {code: caps[code] - mean_x for code in regressed}
    slope = math.fsum(dx[code] * dy[code] for code in regressed) / math.fsum(
        dx[code] * dx[code] for code in regressed
    )

    assert result.statistics.market_cap_slope == pytest.approx(slope)
    assert sorted(result.values()) == sorted(regressed)
    for code in regressed:
        assert result.values()[code] == pytest.approx(dy[code] - slope * dx[code]), code


def test_the_residuals_are_the_two_group_regression_the_stored_inputs_imply(
    wide_store: PanelStore, wide_panel: GeneratedPanel
) -> None:
    """The same hand computation over **two** industries, which is what makes it about industries.

    The test above regresses a single admitted group, and on a single group the industry dummies
    decide nothing: demeaning by group and demeaning the whole cross section are the same
    arithmetic. So it -- and every other path in this file -- stayed green under
    `_neutralize(subjects, ["_ONE_" for _ in groups], regressor, values)`, an engine that discards
    the classification entirely and stores a pure size residual. Nothing in this issue's 146 tests
    could tell "industry **and** size were removed" from "size was removed" -- while the acceptance
    this issue is measured against is that the industry classification is used to do a *real
    industry* neutralisation.

    Here the two groups hold 2 and 6 names with mean capitalisations of 2,375,000 and 5,375,000,
    so the group a security is in moves its residual by much more than floating point. Both answers
    are computed below -- the per-group one the engine must produce, and the pooled one the
    discarding engine would -- and the assertions hold every security to the first and require the
    second to be materially different. The expected numbers come from the two stored cross
    sections by the algebra the closed form implements, not from a recorded run.
    """
    processed = _process(_compute(wide_store, wide_panel))
    cross = _cross_section(wide_store, wide_panel)

    result = _neutralize(processed, cross)

    values = {item.subject: item.value for item in processed.observations}
    caps = {item.subject: math.log(item.market_cap) for item in cross.characteristics}
    group_of = {item.subject: item.industry_code for item in cross.characteristics}
    members: dict[str, list[str]] = {}
    for code, group in group_of.items():
        members.setdefault(group, []).append(code)
    regressed = sorted(group_of)
    mean_y = {
        group: math.fsum(values[code] for code in names) / len(names)
        for group, names in members.items()
    }
    mean_x = {
        group: math.fsum(caps[code] for code in names) / len(names)
        for group, names in members.items()
    }
    dy = {code: values[code] - mean_y[group_of[code]] for code in regressed}
    dx = {code: caps[code] - mean_x[group_of[code]] for code in regressed}
    slope = math.fsum(dx[code] * dy[code] for code in regressed) / math.fsum(
        dx[code] * dx[code] for code in regressed
    )

    pooled_y = math.fsum(values[code] for code in regressed) / len(regressed)
    pooled_x = math.fsum(caps[code] for code in regressed) / len(regressed)
    flat_y = {code: values[code] - pooled_y for code in regressed}
    flat_x = {code: caps[code] - pooled_x for code in regressed}
    pooled_slope = math.fsum(flat_x[code] * flat_y[code] for code in regressed) / math.fsum(
        flat_x[code] * flat_x[code] for code in regressed
    )

    assert sorted(members) == ["801120.SI", "801780.SI"]
    assert sorted(len(names) for names in members.values()) == [2, 6]
    assert result.coverage_census()["neutralized"] == len(wide_panel.securities)
    assert result.coverage_census()["thin_industry"] == 0
    assert result.statistics.industry_count == 2
    assert result.statistics.market_cap_slope == pytest.approx(slope)
    assert sorted(result.values()) == regressed
    for code in regressed:
        assert result.values()[code] == pytest.approx(dy[code] - slope * dx[code]), code
        assert result.industries()[code] == group_of[code], code

    # The mutation this test exists for, stated as a number rather than as an intention: the
    # engine that threw the groups away would store `flat` for every name, and the gap is larger
    # than the residuals' own dispersion.
    flat = {code: flat_y[code] - pooled_slope * flat_x[code] for code in regressed}
    gap = max(abs(result.values()[code] - flat[code]) for code in regressed)
    assert result.statistics.residual_dispersion is not None
    assert gap > result.statistics.residual_dispersion


def test_a_one_member_industry_is_coded_rather_than_given_its_structural_zero(
    store: PanelStore, panel: GeneratedPanel
) -> None:
    """`thin_industry`, end to end on real partitions, and the number it refuses to store.

    `SECURITIES[0]` is alone in its industry on this session. Its residual would be exactly `0.0`
    -- it is its own group mean in both variables -- and a build that stored it under
    `neutralized` would put a structural constant into the column `V2-P3-005` correlates and
    `V2-P3-014` ranks, indistinguishable from a security genuinely at its industry's centre.
    """
    result = _build(store, panel)
    by_subject = {item.subject: item for item in result.observations}

    assert by_subject[LONE].coverage == "thin_industry"
    assert by_subject[LONE].value is None
    assert by_subject[LONE].industry_code is None
    assert by_subject[LONE].source_coverage == "processed"

    census = result.coverage_census()
    assert census["neutralized"] == 7
    assert census["thin_industry"] == 1
    assert census["industry_missing"] == 0
    assert census["market_cap_missing"] == 0
    assert census["not_a_participant"] == 0
    assert sum(census.values()) == len(panel.securities)


def test_a_security_the_classification_does_not_cover_is_its_own_code(
    store: PanelStore, panel: GeneratedPanel
) -> None:
    """`industry_missing`, and why it is not the same fact as `market_cap_missing`.

    The residue is real and permanent -- 2,694 of 2,776 listed names classified on 2015-06-30, a
    2.95% hole -- and it belongs to a *different dataset* from a missing capitalisation, so one
    code for both would report a classification problem as a price-feed one on every affected
    name. Driven through a narrowed cross section rather than through the fixture's coverage-hole
    shape, because that shape makes the membership partition unreadable at this `as_of`; see
    `test_a_membership_row_effective_after_the_as_of_blocks_the_whole_neutralisation`.
    """
    processed = _process(_compute(store, panel))
    cross = _cross_section(store, panel)
    dropped = next(item for item in cross.characteristics if item.subject != LONE)
    narrowed = build_industry_market_cap_cross_section(
        as_of=cross.as_of,
        taxonomy=cross.taxonomy,
        industry_level=cross.industry_level,
        market_cap_measure=cross.market_cap_measure,
        characteristics=[item for item in cross.characteristics if item is not dropped],
        without_industry=(dropped.subject,),
    )

    result = _neutralize(processed, narrowed)
    by_subject = {item.subject: item for item in result.observations}

    assert by_subject[dropped.subject].coverage == "industry_missing"
    assert by_subject[dropped.subject].value is None
    assert by_subject[dropped.subject].industry_code is None
    assert result.coverage_census()["industry_missing"] == 1
    assert result.statistics.participant_count == 6


def test_a_market_cap_the_partition_does_not_carry_is_its_own_code(
    store: PanelStore, panel: GeneratedPanel
) -> None:
    """`market_cap_missing`, driven by moving one name into the second residue.

    `daily_basic` genuinely omits Beijing-board names on historical sessions -- 60 of 3,843 on
    2020-03-02, all `.BJ` (`panel_ingest.load_daily_valuations`) -- so this is the market's shape
    rather than an invented one, and the code has to be distinguishable from `industry_missing`
    because the two point at different datasets and only one of them is a classification problem.
    """
    processed = _process(_compute(store, panel))
    cross = _cross_section(store, panel)
    dropped = next(item for item in cross.characteristics if item.subject != LONE)
    narrowed = build_industry_market_cap_cross_section(
        as_of=cross.as_of,
        taxonomy=cross.taxonomy,
        industry_level=cross.industry_level,
        market_cap_measure=cross.market_cap_measure,
        characteristics=[item for item in cross.characteristics if item is not dropped],
        without_market_cap=(dropped.subject,),
    )

    result = _neutralize(processed, narrowed)
    by_subject = {item.subject: item for item in result.observations}

    assert by_subject[dropped.subject].coverage == "market_cap_missing"
    assert by_subject[dropped.subject].value is None
    assert by_subject[dropped.subject].industry_code is None
    assert result.coverage_census()["market_cap_missing"] == 1
    assert result.coverage_census()["industry_missing"] == 0
    assert result.statistics.participant_count == 6


def test_a_design_with_no_within_industry_dispersion_produces_no_residual_at_all(
    tmp_path: Path, flat_cap_panel: GeneratedPanel
) -> None:
    """The fixture as generated -- `total_mv = 1.0` everywhere -- is a degenerate design.

    Whole-panel, and every row carries the code including the one that had its own reason for
    having no residual, `_uniform_neutralized_panel`'s stated judgement: "there is no neutralised
    cross section at this `as_of`" is the dominant fact, and reporting `thin_industry` for one
    name while the others said `degenerate_design` would suggest that one could have been
    regressed and merely lacked company.
    """
    built = PanelStore(tmp_path / "panel")
    write_generated_panel(built, flat_cap_panel)

    result = _build(built, flat_cap_panel)

    assert result.coverage_census()["degenerate_design"] == len(flat_cap_panel.securities)
    assert result.coverage_census()["thin_industry"] == 0
    assert result.values() == {}
    assert result.industries() == {}
    assert result.statistics.market_cap_slope is None
    assert result.statistics.market_cap_dispersion is None
    assert result.statistics.residual_dispersion is None
    assert result.statistics.participant_count == 7


def test_a_cross_section_thinner_than_the_declared_floor_is_a_whole_panel_code(
    store: PanelStore, panel: GeneratedPanel
) -> None:
    """The shipped `INDUSTRY_AND_SIZE`'s floor of 100 against an eight-name market.

    The one assertion a fixture panel can make about the *shipped* configuration, and it is worth
    making: a floor above the market produces a coded, auditable outcome with the participant
    count stored, rather than an exception -- which is what lets a backfill cross a historical
    `as_of` whose market was genuinely thin.
    """
    processed = _process(_compute(store, panel))
    cross = _cross_section(store, panel, INDUSTRY_AND_SIZE)

    result = _neutralize(processed, cross, INDUSTRY_AND_SIZE)

    assert result.coverage_census()["insufficient_cross_section"] == len(panel.securities)
    assert result.statistics.participant_count == 7
    assert result.values() == {}


def test_the_member_floor_admits_a_group_of_exactly_its_size_and_refuses_one_below(
    store: PanelStore, panel: GeneratedPanel
) -> None:
    """The boundary of `min_industry_members`, on both sides of it.

    The seven-name group is *exactly* the floor at 7, so a `>` where the code has `>=` codes the
    whole market `thin_industry` -- and a test that only drove the one-name group would not see
    it, because 1 fails both comparisons. Written after a mutant survived precisely that gap.
    """
    processed = _process(_compute(store, panel))
    cross = _cross_section(store, panel)

    at_the_floor = _neutralize(processed, cross, _spec(min_industry_members=7, min_cross_section=7))
    above_it = _neutralize(processed, cross, _spec(min_industry_members=8, min_cross_section=8))

    assert at_the_floor.coverage_census()["neutralized"] == 7
    assert at_the_floor.coverage_census()["thin_industry"] == 1
    assert at_the_floor.statistics.smallest_industry_size == 7
    assert above_it.coverage_census()["neutralized"] == 0
    assert above_it.coverage_census()["insufficient_cross_section"] == len(panel.securities)


def test_a_thin_industrys_members_do_not_count_toward_the_cross_section_floor(
    store: PanelStore, panel: GeneratedPanel
) -> None:
    """The ordering of steps 4 and 5, measured rather than argued.

    Eight names are eligible -- every one has an industry and a capitalisation -- and with a
    member floor of 7 only seven of them are admissible. A whole-panel floor of 8 therefore
    cannot be met. **The opposite order counts the eligible eight, clears the floor of 8, and then
    regresses seven of them under a manifest saying the cross section was eight**, which is a
    build reporting a market it did not score. The two assertions below are what tell the orders
    apart: the census must be `insufficient_cross_section` for everybody rather than
    `neutralized` for seven.
    """
    processed = _process(_compute(store, panel))
    cross = _cross_section(store, panel)

    result = _neutralize(processed, cross, _spec(min_industry_members=7, min_cross_section=8))

    assert result.coverage_census()["insufficient_cross_section"] == len(panel.securities)
    assert result.coverage_census()["neutralized"] == 0
    assert result.coverage_census()["thin_industry"] == 0
    assert result.statistics.participant_count == 7


def test_an_imputed_processed_value_enters_the_regression_only_when_the_rule_says_so(
    store: PanelStore, panel: GeneratedPanel
) -> None:
    """The participation rule, and it moves every residual rather than only the imputed row's.

    A narrowed universe gives one name `not_in_universe`, which the transform fills with the
    processed median under a `fill_cross_sectional_median` policy. Under `measured_only` that name
    is `not_a_participant`; under `measured_and_imputed` it joins its industry and moves that
    group's own mean, so every other security's residual moves with it.
    """
    excluded = SECURITIES[3]
    raw = _compute(store, panel, universe=frozenset(panel.securities) - {excluded})
    filling = _transform_spec(
        key="probe_fill",
        missing_values=MissingValuePolicy(
            not_in_universe="exclude",
            insufficient_history="exclude",
            ambiguous_filing="fill_cross_sectional_median",
            input_missing="fill_cross_sectional_median",
            undefined_value="fill_cross_sectional_median",
        ),
    )
    processed = apply_factor_transform(raw, filling, code_commit=COMMIT, built_at=BUILT_AT)
    cross = _cross_section(store, panel)

    strict = _neutralize(processed, cross, _spec(participation="measured_only"))
    inclusive = _neutralize(processed, cross, _spec(participation="measured_and_imputed"))
    by_subject = {item.subject: item for item in strict.observations}

    assert by_subject[excluded].coverage == "not_a_participant"
    assert by_subject[excluded].source_coverage == "source_not_computed"
    assert strict.statistics.participant_count == 6
    assert inclusive.statistics.participant_count == 6
    assert strict.manifest.neutralization_manifest_id != (
        inclusive.manifest.neutralization_manifest_id
    )


# --- the guards -----------------------------------------------------------------------------------


def test_a_cross_section_for_another_instant_is_refused_rather_than_joined(
    store: PanelStore, panel: GeneratedPanel
) -> None:
    """The substitute for "the engine cannot reach a store", and it is exact rather than a
    tolerance: both values carry an aware instant and the two must be the same one."""
    processed = _process(_compute(store, panel))
    cross = _cross_section(store, panel)
    shifted = dataclasses.replace(cross, as_of=cross.as_of + timedelta(days=1))

    with pytest.raises(FactorEngineError, match="the two instants must be the same one"):
        _neutralize(processed, shifted)


def test_a_cross_section_that_does_not_cover_the_panel_is_refused(
    store: PanelStore, panel: GeneratedPanel
) -> None:
    """The guard with no other detector.

    A name the second cross section never heard of resolves to the same `None` as a name it has no
    industry for -- so without this check, a cross section assembled for half the market would be
    stored as a market that was half unclassified.
    """
    processed = _process(_compute(store, panel))
    cross = _cross_section(store, panel)
    truncated = build_industry_market_cap_cross_section(
        as_of=cross.as_of,
        taxonomy=cross.taxonomy,
        industry_level=cross.industry_level,
        market_cap_measure=cross.market_cap_measure,
        characteristics=cross.characteristics[:2],
    )

    with pytest.raises(FactorEngineError, match="places them in none of its three collections"):
        _neutralize(processed, truncated)


def test_a_cross_section_at_another_level_or_measure_than_the_spec_declares_is_refused(
    store: PanelStore, panel: GeneratedPanel
) -> None:
    processed = _process(_compute(store, panel))
    cross = _cross_section(store, panel)

    with pytest.raises(FactorEngineError, match="files one taxonomy level's groups"):
        _neutralize(processed, dataclasses.replace(cross, industry_level="L2"))
    # The `match=` moved with the message: the refusal used to end "differ by up to 0.0196 on
    # residuals whose rms is 0.995", a figure this review retracted (see MEASURE_GAP_FLOOR). The
    # new phrase is just as narrow -- nothing else in this module says "different size variables".
    with pytest.raises(FactorEngineError, match="different size variables"):
        _neutralize(processed, dataclasses.replace(cross, market_cap_measure="circ_mv"))


def test_a_hand_built_capitalisation_of_zero_is_refused_by_name_under_both_scales(
    store: PanelStore, panel: GeneratedPanel
) -> None:
    """The path `_log_regressor` said had no domain error to have, driven in both directions.

    `build_industry_market_cap_cross_section` refuses a capitalisation that is not finite and
    strictly positive, and `_log_regressor`'s docstring used to conclude from that alone that a
    guard beside `math.log` would be a branch nothing could enter. Two of this repository's own
    sentences said otherwise -- `SecurityCharacteristic` is "a plain carrier with no validation of
    its own" and `IndustryMarketCapCrossSection`'s constructor "is not a boundary and validates
    nothing" -- and `characteristic_digest` already carried a refusal written for exactly this
    path. Measured, `SecurityCharacteristic(..., market_cap=0.0)` constructs, and the engine then
    raised a bare `ValueError("math domain error")` from inside a list comprehension, where a
    caller's `except FactorEngineError` could not see it.

    **Both scales, because the fault is the row and not the arithmetic.** Under `log` the old code
    raised the wrong type; under `level` it raised nothing at all and regressed on the zero, which
    is the worse of the two -- a slope and a dispersion stored as facts about a market. A guard
    that fired only under `log` would make a row's admissibility depend on a knob about output
    shape, which is the arrangement `_standardize_rank` rejects one plane down.

    The subject is asserted in the message rather than only the phrase, because
    `characteristic_digest`'s precedent is that the reader is holding thousands of rows and needs
    the one.
    """
    processed = _process(_compute(store, panel))
    cross = _cross_section(store, panel)
    subject = cross.characteristics[-1].subject
    zeroed = dataclasses.replace(
        cross,
        characteristics=(
            *cross.characteristics[:-1],
            dataclasses.replace(cross.characteristics[-1], market_cap=0.0),
        ),
    )

    for scale in ("log", "level"):
        with pytest.raises(FactorEngineError, match="not finite and strictly positive") as refusal:
            _neutralize(processed, zeroed, _spec(market_cap_scale=scale))
        assert subject in str(refusal.value)

    # The same row with the capitalisation the store served is neutralised, so the refusal above is
    # the zero and not the reshaping the test does to reach it.
    assert _neutralize(processed, cross).statistics.participant_count > 0


def test_a_processed_panel_that_does_not_own_its_rows_is_refused(
    store: PanelStore, panel: GeneratedPanel
) -> None:
    """What makes `(source_transform_manifest_id, subject, as_of)` a proved key rather than
    an assumed one."""
    processed = _process(_compute(store, panel))
    stray = dataclasses.replace(processed.observations[0], transform_manifest_id="ftm_elsewhere")
    tampered = dataclasses.replace(processed, observations=(stray, *processed.observations[1:]))

    with pytest.raises(FactorEngineError, match="would make that pointer name a build"):
        _neutralize(tampered, _cross_section(store, panel))


def test_a_panel_whose_spec_is_not_the_one_that_produced_its_rows_is_refused_at_the_write(
    store: PanelStore, panel: GeneratedPanel
) -> None:
    """`V2-P3-003`'s review defect, closed here at birth rather than after a measurement.

    The manifest columns come off `manifest` and the policy columns off `spec`, so a panel wearing
    another spec's label stores a manifest row whose `neutralization_id` and whose
    `market_cap_scale` describe two different builds -- and the identity self-check on the way back
    cannot see it, because the policy columns are not among the twelve it reassembles.
    """
    result = _build(store, panel)
    mislabelled = dataclasses.replace(result, spec=_spec(key="other_neutral"))

    with pytest.raises(FactorEngineError, match="are not one application"):
        write_neutralized_factor_panels(store, [mislabelled])


def test_a_panel_with_a_blank_taxonomy_is_refused_at_the_write(
    store: PanelStore, panel: GeneratedPanel
) -> None:
    """The one stored column hashed only inside `characteristic_digest` and nowhere else."""
    result = _build(store, panel)

    with pytest.raises(FactorEngineError, match="blank one tells a reader of the partition"):
        write_neutralized_factor_panels(store, [dataclasses.replace(result, industry_taxonomy=" ")])


def test_two_neutralisations_of_one_build_at_one_as_of_are_refused(
    store: PanelStore, panel: GeneratedPanel
) -> None:
    result = _build(store, panel)

    with pytest.raises(FactorEngineError, match="more than one application of"):
        write_neutralized_factor_panels(store, [result, result])


def test_an_empty_write_and_an_empty_panel_are_both_refused(
    store: PanelStore, panel: GeneratedPanel
) -> None:
    processed = _process(_compute(store, panel))

    with pytest.raises(FactorEngineError, match="needs at least one panel"):
        write_neutralized_factor_panels(store, [])
    with pytest.raises(FactorEngineError, match="at least one observation"):
        _neutralize(dataclasses.replace(processed, observations=()), _cross_section(store, panel))


def test_superseding_a_build_no_partition_holds_is_refused(
    store: PanelStore, panel: GeneratedPanel
) -> None:
    result = _build(store, panel)
    write_neutralized_factor_panels(store, [result])

    with pytest.raises(FactorEngineError, match="which no partition this write touches holds"):
        write_neutralized_factor_panels(store, [result], supersedes=["fnm_nothing"])


# --- the round trip ------------------------------------------------------------------------------


def test_three_tiers_coexist_and_the_two_below_are_byte_identical_after_the_write(
    store: PanelStore, panel: GeneratedPanel
) -> None:
    """D8's "不覆盖源观测", as bytes on disk rather than as a claim about code paths."""
    raw = _compute(store, panel)
    processed = _process(raw)
    write_factor_panels(store, [raw])
    write_processed_factor_panels(store, [processed])
    raw_before = _partition_bytes(store, OBSERVATIONS, YEAR)
    processed_before = _partition_bytes(store, PROCESSED, YEAR)

    write_neutralized_factor_panels(store, [_neutralize(processed, _cross_section(store, panel))])

    assert _partition_bytes(store, OBSERVATIONS, YEAR) == raw_before
    assert _partition_bytes(store, PROCESSED, YEAR) == processed_before
    assert len(load_factor_observations(store, REVERSAL_1D, years=(YEAR,), as_of=AS_OF)) == 8
    assert (
        len(
            load_processed_factor_observations(
                store, REVERSAL_1D, _transform_spec(), years=(YEAR,), as_of=AS_OF
            )
        )
        == 8
    )
    assert (
        len(
            load_neutralized_factor_observations(
                store, REVERSAL_1D, _spec(), years=(YEAR,), as_of=AS_OF
            )
        )
        == 8
    )


def test_a_neutralised_row_names_the_exact_processed_row_which_names_the_exact_raw_row(
    store: PanelStore, panel: GeneratedPanel
) -> None:
    """Both hops of the provenance chain, performed as joins rather than described.

    Three tiers, two pointers, no copies: a neutralised row's
    `(source_transform_manifest_id, subject, as_of)` is the exact key of a processed row, and that
    row's `(source_manifest_id, subject, as_of)` is the exact key of a raw one.
    """
    raw = _compute(store, panel)
    processed = _process(raw)
    result = _neutralize(processed, _cross_section(store, panel))
    write_factor_panels(store, [raw])
    write_processed_factor_panels(store, [processed])
    write_neutralized_factor_panels(store, [result])

    stored = load_neutralized_factor_observations(
        store, REVERSAL_1D, _spec(), years=(YEAR,), as_of=AS_OF
    )
    processed_rows = {
        (item.source_manifest_id, item.subject, item.as_of): item
        for item in load_processed_factor_observations(
            store, REVERSAL_1D, _transform_spec(), years=(YEAR,), as_of=AS_OF
        )
    }
    raw_rows = {
        (item.manifest_id, item.subject, item.as_of): item
        for item in load_factor_observations(store, REVERSAL_1D, years=(YEAR,), as_of=AS_OF)
    }
    processed_by_key = {
        (item.transform_manifest_id, item.subject, item.as_of): item
        for item in processed_rows.values()
    }

    assert len(stored) == 8
    for row in stored:
        parent = processed_by_key[(row.source_transform_manifest_id, row.subject, row.as_of)]
        assert parent.coverage == row.source_coverage
        grandparent = raw_rows[(parent.source_manifest_id, parent.subject, parent.as_of)]
        assert grandparent.factor_id == row.source_factor_id


def test_every_stored_column_survives_the_round_trip_with_its_value(
    store: PanelStore, panel: GeneratedPanel
) -> None:
    """Each of the eleven observation columns read back off the partition and held to a value.

    Coverage is worthless here and `V2-P3-003`'s review measured why: 23 stored columns whose
    names were asserted and whose values never were could all be written as constants with 233
    tests staying green. So this reads the decoded row and compares every field.

    **Built over a narrowed universe on purpose.** With every security computed, `source_coverage`
    is `"processed"` on all eight rows -- so a builder that wrote the constant `"processed"` into
    that column would round-trip perfectly and the assertion would prove nothing. Excluding one
    name gives the panel a `source_not_computed` row, which is what makes the comparison able to
    fail. Measured: a per-column falsification found exactly that gap.
    """
    excluded = SECURITIES[5]
    raw = _compute(store, panel, universe=frozenset(panel.securities) - {excluded})
    processed = _process(raw)
    result = _neutralize(processed, _cross_section(store, panel))
    write_neutralized_factor_panels(store, [result])

    stored = {
        item.subject: item
        for item in load_neutralized_factor_observations(
            store, REVERSAL_1D, _spec(), years=(YEAR,), as_of=AS_OF
        )
    }
    expected = {item.subject: item for item in result.observations}

    assert set(stored) == set(expected)
    assert {item.source_coverage for item in expected.values()} == {
        "processed",
        "source_not_computed",
    }
    for subject, row in stored.items():
        source: NeutralizedFactorObservation = expected[subject]
        assert row.as_of == source.as_of, subject
        assert row.coverage == source.coverage, subject
        assert row.industry_code == source.industry_code, subject
        assert row.neutralization_id == source.neutralization_id, subject
        assert row.neutralization_manifest_id == source.neutralization_manifest_id, subject
        assert row.source_factor_id == source.source_factor_id, subject
        assert row.source_transform_id == source.source_transform_id, subject
        assert row.source_transform_manifest_id == source.source_transform_manifest_id, subject
        assert row.source_coverage == source.source_coverage, subject
        if source.value is None:
            assert row.value is None, subject
        else:
            assert row.value == pytest.approx(source.value), subject


def test_the_two_observation_columns_no_decoder_reassembles_are_still_asserted(
    store: PanelStore, panel: GeneratedPanel
) -> None:
    """`neutralization_key` and `neutralization_version`, read straight off the partition.

    **These two are the only stored observation columns `NeutralizedFactorObservation` does not
    carry**, so `load_neutralized_factor_observations` never looks at them and no round-trip
    comparison can. They exist for `factor_key`/`transform_key`'s reason -- a reader querying the
    partition directly would otherwise need this build's registry to know what the rows are about
    -- and without this test they would be two columns rendered and never read, which is exactly
    the shape `V2-P3-003`'s review found 23 of. Measured: writing either as a constant left the
    three covering files green until this test existed.
    """
    result = _build(store, panel)
    write_neutralized_factor_panels(store, [result])

    rows = store.query(
        NEUTRALIZED,
        year=YEAR,
        columns=("subject", "neutralization_key", "neutralization_version"),
    )

    assert len(rows) == len(panel.securities)
    assert {str(row[1]) for row in rows} == {"probe_neutral"}
    assert {int(str(row[2])) for row in rows} == {1}
    assert {str(row[0]) for row in rows} == set(panel.securities)


def test_every_stored_manifest_column_is_read_back_and_held_to_a_value(
    store: PanelStore, panel: GeneratedPanel
) -> None:
    """All 34 manifest columns, off the partition, against values derived here.

    The instrument `V2-P3-003`'s review installed after finding that 23 rendered-but-unasserted
    columns could be written as constants without turning anything red. The census is recounted
    against the *observation* partition rather than copied off the panel, so the two datasets have
    to agree with each other and not merely with the object that wrote them.

    **Two of the 34 are asserted here and are nonetheless not separated here**, and saying so is
    the point rather than an apology. This fixture admits one industry, so
    `smallest_industry_size` and `largest_industry_size` are both 7 and the two assertions below
    cannot tell the columns apart -- swapping the expressions that compute them left this test
    green, which is exactly the "written as a constant and nothing goes red" shape one column
    further on. `test_the_two_industry_size_columns_are_two_answers_and_not_one_on_a_two_group_
    market` is the assertion that separates them, on `WIDE_SHAPES`' 2-and-6 market. The rows
    stay here so that the "every stored column" claim is literally true on one partition.
    """
    result = _build(store, panel)
    write_neutralized_factor_panels(store, [result])
    rows = store.query(
        NEUTRALIZATION_MANIFESTS, year=YEAR, columns=NEUTRALIZATION_MANIFEST_PANEL_COLUMNS
    )
    assert len(rows) == 1
    cells = dict(zip(NEUTRALIZATION_MANIFEST_PANEL_COLUMNS, rows[0], strict=True))

    stored_rows = load_neutralized_factor_observations(
        store, REVERSAL_1D, _spec(), years=(YEAR,), as_of=AS_OF
    )
    recounted = {code: 0 for code in result.coverage_census()}
    for row in stored_rows:
        recounted[row.coverage] += 1
    statistics = result.statistics
    manifest = result.manifest

    assert cells["subject"] == manifest.neutralization_manifest_id
    assert cells["neutralization_id"] == manifest.neutralization_id
    assert cells["neutralization_key"] == "probe_neutral"
    assert cells["neutralization_version"] == 1
    assert cells["source_factor_id"] == REVERSAL_1D.factor_id
    assert cells["source_factor_key"] == REVERSAL_1D.key
    assert cells["source_factor_version"] == REVERSAL_1D.version
    assert cells["source_transform_id"] == _transform_spec().transform_id
    assert cells["source_transform_manifest_id"] == manifest.source_transform_manifest_id
    assert cells["source_processed_digest"] == manifest.source_processed_digest
    assert cells["characteristic_digest"] == manifest.characteristic_digest
    assert cells["as_of_time"] == AS_OF
    assert cells["code_commit"] == COMMIT
    assert cells["industry_level"] == "L1"
    assert cells["market_cap_measure"] == "total_mv"
    assert cells["market_cap_scale"] == "log"
    assert cells["participation"] == "measured_only"
    assert cells["min_industry_members"] == 2
    assert cells["min_cross_section"] == 2
    assert cells["industry_taxonomy"] == INDUSTRY_MEMBERSHIP_TAXONOMY
    assert cells["participant_count"] == 7
    assert cells["industry_count"] == 1
    assert cells["smallest_industry_size"] == 7
    assert cells["largest_industry_size"] == 7
    assert cells["backfilled_industry_count"] == 0
    assert cells["market_cap_slope"] == pytest.approx(statistics.market_cap_slope)
    assert cells["market_cap_dispersion"] == pytest.approx(statistics.market_cap_dispersion)
    assert cells["residual_dispersion"] == pytest.approx(statistics.residual_dispersion)
    for code, count in recounted.items():
        assert cells[f"census_{code}"] == count, code
    assert set(cells) - {"subject"} == set(NEUTRALIZATION_MANIFEST_DATA_COLUMNS)


def test_the_two_industry_size_columns_are_two_answers_and_not_one_on_a_two_group_market(
    wide_store: PanelStore, wide_panel: GeneratedPanel
) -> None:
    """`smallest_industry_size` and `largest_industry_size`, on a market where they differ.

    `FactorNeutralizationStatistics.__post_init__` refuses a build whose smallest group is bigger
    than its largest -- "the two run backwards" -- and that guard had **no reachable driver**,
    because every stored manifest in this file came from a one-group cross section where the two
    numbers are equal by construction. Swapping `min` and `max` at the call site therefore changed
    nothing anybody asserted.

    Here the groups hold 2 and 6, so the two columns carry two different numbers and the pair is
    read back off the partition rather than off the object that wrote it -- which is what makes
    this an assertion about a stored column and not about a dataclass. `industry_count` and
    `participant_count` are held here too, for the same reason: 1 and 7 on the narrow fixture are
    also both derivable from a constant.
    """
    result = _neutralize(
        _process(_compute(wide_store, wide_panel)), _cross_section(wide_store, wide_panel)
    )
    write_neutralized_factor_panels(wide_store, [result])

    rows = wide_store.query(
        NEUTRALIZATION_MANIFESTS, year=YEAR, columns=NEUTRALIZATION_MANIFEST_PANEL_COLUMNS
    )
    assert len(rows) == 1
    cells = dict(zip(NEUTRALIZATION_MANIFEST_PANEL_COLUMNS, rows[0], strict=True))

    assert cells["smallest_industry_size"] == 2
    assert cells["largest_industry_size"] == 6
    assert cells["smallest_industry_size"] != cells["largest_industry_size"]
    assert cells["industry_count"] == 2
    assert cells["participant_count"] == len(wide_panel.securities)
    assert cells["census_neutralized"] == len(wide_panel.securities)
    assert cells["census_thin_industry"] == 0


def test_a_reassembled_manifest_reproduces_the_identity_it_was_stored_under(
    store: PanelStore, panel: GeneratedPanel
) -> None:
    result = _build(store, panel)
    write_neutralized_factor_panels(store, [result])

    stored = load_factor_neutralization_manifests(store, REVERSAL_1D, years=(YEAR,), as_of=AS_OF)

    assert len(stored) == 1
    assert stored[0].neutralization_manifest_id == result.manifest.neutralization_manifest_id
    assert stored[0] == result.manifest


def test_a_second_neutralisation_of_one_factor_shares_the_partition_and_reads_apart(
    store: PanelStore, panel: GeneratedPanel
) -> None:
    """The dataset-name budget's stated consequence, exercised rather than described.

    One `factor_neut_*` partition holds every neutralisation of the factor, so a year has to be
    written in one call across all of them -- and a read of one opens the rows of both and filters
    on `neutralization_id` in Python.
    """
    processed = _process(_compute(store, panel))
    cross = _cross_section(store, panel)
    by_log = _neutralize(processed, cross)
    level_spec = _spec(key="probe_level", market_cap_scale="level")
    by_level = _neutralize(processed, cross, level_spec)

    write_neutralized_factor_panels(store, [by_log, by_level])
    log_rows = load_neutralized_factor_observations(
        store, REVERSAL_1D, _spec(), years=(YEAR,), as_of=AS_OF
    )
    level_rows = load_neutralized_factor_observations(
        store, REVERSAL_1D, level_spec, years=(YEAR,), as_of=AS_OF
    )

    assert len(store.query(NEUTRALIZED, year=YEAR, columns=("subject",))) == 16
    assert len(log_rows) == 8
    assert len(level_rows) == 8
    assert {row.neutralization_id for row in log_rows} == {_spec().neutralization_id}
    assert {row.neutralization_id for row in level_rows} == {level_spec.neutralization_id}
    assert by_log.values() != by_level.values()


def test_a_merge_that_loses_a_stored_build_is_refused_on_this_plane_too(
    store: PanelStore, panel: GeneratedPanel, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`V2-P4-073` on the third plane, which shares the writer's shape and shared its hole.

    `write_neutralized_factor_panels` ran the catalog-side drop guard for `kind ==
    FACTOR_NEUTRALIZATION_MANIFEST_KIND` alone, so the neutralised observation merge -- whose
    subjects are securities and whose builds live in a `neutralization_manifest_id` column -- was
    audited by nothing. The probe over-matches `identity_columns` on that plane only; before the
    audit the write reported success over a partition that had lost the first policy's whole cross
    section.
    """
    processed = _process(_compute(store, panel))
    cross = _cross_section(store, panel)
    first = _neutralize(processed, cross)
    write_neutralized_factor_panels(store, [first])
    real = panel_factors.appended_to_the_stored_year
    monkeypatch.setattr(
        panel_neutralization,
        "appended_to_the_stored_year",
        lambda store, batch, year, *, build_column, identity_columns, superseded: real(
            store,
            batch,
            year,
            build_column=build_column,
            identity_columns=(
                identity_columns if build_column == SUBJECT_COLUMN_NAME else (SUBJECT_COLUMN_NAME,)
            ),
            superseded=superseded,
        ),
    )
    second = _neutralize(processed, cross, _spec(key="probe_level", market_cap_scale="level"))

    with pytest.raises(FactorEngineError, match="would drop") as raised:
        write_neutralized_factor_panels(store, [second])

    assert first.manifest.neutralization_manifest_id in str(raised.value)
    assert NEUTRALIZED in str(raised.value)


def test_a_second_neutralisation_is_added_and_a_restatement_is_still_refused(
    store: PanelStore, panel: GeneratedPanel
) -> None:
    """`V2-P4-071` on this plane, and the shared drop guard where it still fires.

    This asserted that re-writing one of two stored neutralisations on its own is refused, which
    was true of the whole-partition replace and is no longer what happens: `carry_stored_rows
    _forward` puts the other one back, so the write adds nothing and destroys nothing. See
    `test_factor_transforms.py::test_a_second_transform_written_on_its_own_is_added_and_a
    _restatement_is_still_refused` for the full argument -- the refusal protected against loss, it
    was never a rule that a partition may hold one policy.

    The guard's own case is unchanged and is what the second half drives: a **restatement** --
    the same neutralisation of the same processed build at the same instant under a different
    `code_commit` -- collides on `(neutralization_id, event_time)`, is therefore not carried, and
    is refused by name. `supersedes` still repairs it.
    """
    processed = _process(_compute(store, panel))
    cross = _cross_section(store, panel)
    first = _neutralize(processed, cross)
    second = _neutralize(processed, cross, _spec(key="probe_level", market_cap_scale="level"))
    write_neutralized_factor_panels(store, [first, second])

    # Re-writing one of them alone now keeps the other rather than dropping it.
    write_neutralized_factor_panels(store, [first])
    held = load_factor_neutralization_manifests(store, REVERSAL_1D, years=(YEAR,), as_of=AS_OF)
    assert {item.neutralization_manifest_id for item in held} == {
        first.manifest.neutralization_manifest_id,
        second.manifest.neutralization_manifest_id,
    }

    restated = apply_factor_neutralization(
        processed, _spec(), cross, code_commit="9876543210fedcba", built_at=BUILT_AT
    )
    assert restated.manifest.neutralization_manifest_id != first.manifest.neutralization_manifest_id
    with pytest.raises(FactorEngineError, match="would drop"):
        write_neutralized_factor_panels(store, [restated])

    assert write_neutralized_factor_panels(
        store, [restated], supersedes=[first.manifest.neutralization_manifest_id]
    )


# --- the determinant audit ------------------------------------------------------------------------


IDENTITY_ARGUMENTS: Final[frozenset[str]] = frozenset(
    {"panel", "spec", "characteristics", "code_commit"}
)
"""Parameters of `apply_factor_neutralization` that reach `neutralization_manifest_id`.

`panel` three times over -- `source_transform_id`, `source_transform_manifest_id` and
`source_processed_digest`; `spec` through `neutralization_id`; `characteristics` through
`characteristic_digest`; `code_commit` directly.
"""

EXEMPT_ARGUMENTS: Final[frozenset[str]] = frozenset({"built_at"})
"""The wall clock, deliberately out of the content address: re-applying the same neutralisation to
the same processed build must reproduce its identity, or a rebuild could never be written past
`_refuse_to_drop_a_stored_build`."""


def test_every_determinant_is_either_in_the_identity_or_exempted_by_name() -> None:
    """Read off the function's own signature, so a sixth parameter fails until it is classified.

    An equivalence test varies what a model declares and cannot show that the model declares
    everything -- which is `test_every_determinant_of_this_build_is_either_in_the_identity_or_
    exempted_by_name`'s argument, and the reason this audit reads `inspect.signature` instead of
    maintaining a list.
    """
    parameters = set(inspect.signature(apply_factor_neutralization).parameters)

    assert parameters == IDENTITY_ARGUMENTS | EXEMPT_ARGUMENTS
    assert set() == IDENTITY_ARGUMENTS & EXEMPT_ARGUMENTS


def test_the_neutralisation_takes_no_store_and_therefore_no_second_visibility_rule() -> None:
    """The structural half of the point-in-time claim, audited by signature.

    `apply_factor_transform` established the form: a function that cannot read a row cannot read
    one that was not knowable. This one needs two foreign datasets and still takes no store -- the
    substitute is that its second input is a *value* stamped with the instant it was read at, and
    `test_a_cross_section_for_another_instant_is_refused_rather_than_joined` is the measurement of
    the substitute.
    """
    signature = inspect.signature(apply_factor_neutralization)
    annotations = {
        name: str(parameter.annotation) for name, parameter in signature.parameters.items()
    }

    assert "store" not in signature.parameters
    assert "as_of" not in signature.parameters
    assert "universe" not in signature.parameters
    assert "date_timezone" not in signature.parameters
    assert not any("PanelStore" in text for text in annotations.values())
    assert not any("ReadinessRequirement" in text for text in annotations.values())


def test_a_source_panel_whose_numbers_moved_moves_the_neutralisation_identity(
    store: PanelStore, panel: GeneratedPanel
) -> None:
    """What makes exempting nothing on the `panel` argument a measurement rather than a promise.

    Built by hand because `apply_factor_transform` will not produce a panel whose manifest is
    unchanged and whose values are not -- and `ProcessedFactorPanel` is a public frozen dataclass
    that can.
    """
    processed = _process(_compute(store, panel))
    cross = _cross_section(store, panel)
    first = processed.observations[0]
    assert first.value is not None
    moved = dataclasses.replace(
        processed,
        observations=(
            dataclasses.replace(first, value=first.value + 1.0),
            *processed.observations[1:],
        ),
    )

    baseline = _neutralize(processed, cross)
    shifted = _neutralize(moved, cross)

    assert baseline.manifest.source_transform_manifest_id == (
        shifted.manifest.source_transform_manifest_id
    )
    assert baseline.manifest.source_processed_digest != shifted.manifest.source_processed_digest
    assert baseline.manifest.neutralization_manifest_id != (
        shifted.manifest.neutralization_manifest_id
    )


def test_a_cross_section_whose_industries_moved_moves_the_identity_too(
    store: PanelStore, panel: GeneratedPanel
) -> None:
    """The same instrument on the second input, which no manifest field names directly."""
    processed = _process(_compute(store, panel))
    cross = _cross_section(store, panel)
    relabelled = build_industry_market_cap_cross_section(
        as_of=cross.as_of,
        taxonomy=cross.taxonomy,
        industry_level=cross.industry_level,
        market_cap_measure=cross.market_cap_measure,
        characteristics=[
            dataclasses.replace(item, is_backfilled=True) for item in cross.characteristics
        ],
        without_industry=cross.without_industry,
        without_market_cap=cross.without_market_cap,
    )

    baseline = _neutralize(processed, cross)
    moved = _neutralize(processed, relabelled)

    assert baseline.values() == pytest.approx(dict(moved.values()))
    assert baseline.manifest.characteristic_digest != moved.manifest.characteristic_digest
    assert baseline.manifest.neutralization_manifest_id != (
        moved.manifest.neutralization_manifest_id
    )
    assert moved.statistics.backfilled_industry_count == 7


def test_re_running_the_same_neutralisation_reproduces_its_identity_and_is_writable(
    store: PanelStore, panel: GeneratedPanel
) -> None:
    """`built_at`'s exemption, measured: a rebuild at a different wall clock is the same build."""
    processed = _process(_compute(store, panel))
    cross = _cross_section(store, panel)
    first = _neutralize(processed, cross)
    again = apply_factor_neutralization(
        processed,
        _spec(),
        cross,
        code_commit=COMMIT,
        built_at=BUILT_AT + timedelta(days=30),
    )
    write_neutralized_factor_panels(store, [first])

    assert again.manifest.neutralization_manifest_id == (first.manifest.neutralization_manifest_id)
    assert write_neutralized_factor_panels(store, [again])


# --- the readiness contract -----------------------------------------------------------------------


def test_the_read_requirement_waives_the_three_checks_a_derived_partition_cannot_answer(
    store: PanelStore, panel: GeneratedPanel
) -> None:
    """`required_dates`, `required_subjects` and `max_staleness`, waived with `required_fields` not.

    The dates here are `as_of`s somebody chose to compute rather than sessions an exchange was
    open; the subjects are the cross section the read is *for*; a derived partition has no upstream
    to be stale against. The stored columns are not waived, because they are exactly what the
    decoder needs and a missing one would surface as a binder error rather than a verdict.
    """
    result = _build(store, panel)
    write_neutralized_factor_panels(store, [result])
    requirement: ReadinessRequirement = (store.read_visible_at.__self__ and None) or None  # type: ignore[assignment]

    from openalpha_cn.panel_neutralization import (
        neutralization_manifest_requirement,
        neutralized_factor_requirement,
    )

    for builder in (neutralized_factor_requirement, neutralization_manifest_requirement):
        built = builder(REVERSAL_1D, years=(YEAR,), as_of=AS_OF)
        assert built.required_dates is None
        assert built.required_subjects is None
        assert built.max_staleness is None
        assert built.required_fields is not None
        assert "subject" in built.required_fields
    assert requirement is None


def _build_at(
    store: PanelStore, panel: GeneratedPanel, *, as_of: datetime, day: date
) -> NeutralizedFactorPanel:
    """A whole three-tier build run at one instant, with every read taken at that instant.

    `_build`'s in-year twin. The only difference is that `as_of` and the session are arguments
    rather than the fixture's own year-end pair, which is what makes a mid-year build expressible
    at all -- before `V2-P4-026` no combination of the two below `AS_OF` reached the third tier.
    """
    source = _compute(
        store,
        panel,
        as_of=as_of,
        requirements={
            DAILY_DATASET: daily_requirement(
                panel.calendar(), years=(YEAR,), as_of=as_of, max_staleness=INPUT_STALENESS_BOUND
            )
        },
    )
    section = load_industry_market_cap_cross_section(
        store,
        _spec(),
        subjects=panel.securities,
        day=day,
        as_of=as_of,
        calendar=panel.calendar(),
        membership_years=MEMBERSHIP_YEARS,
        max_staleness=None,
    )
    return _neutralize(_process(source), section)


def test_a_residual_built_at_a_mid_year_as_of_is_visible_at_that_same_as_of(
    store: PanelStore, panel: GeneratedPanel
) -> None:
    """`V2-P4-026`'s acceptance criterion, end to end on partitions on disk.

    The roadmap states the acceptance as "an in-year `as_of` can read that day's residual".
    Every step is taken at
    `MID_YEAR_BUILD` -- 17:00 Asia/Shanghai on 2026-01-12, four sessions before the panel's window
    ends and eleven and a half months before the year does: the raw cross section, the transform,
    the industry-and-size read, the regression, the write, and the read back. The read back is the
    step that used to answer `()`.

    Three things are asserted rather than "not empty", because "not empty" is what an empty-shaped
    defect also satisfies:

    - every stored row carries `MID_YEAR_BUILD` as its own `as_of`, so the partition really was
      stamped in-year rather than at the fixture's year-end instant;
    - the stored residuals reproduce the in-memory ones the engine computed, to the value, so the
      round trip is a round trip and not a coincidence of counts;
    - a read one microsecond earlier still answers `()`. That is the point-in-time rule, and it
      staying true is what says this issue moved the *build's* clock rather than weakening the
      read's.
    """
    result = _build_at(store, panel, as_of=MID_YEAR_BUILD, day=MID_YEAR_SESSION)
    write_neutralized_factor_panels(store, [result])

    stored = load_neutralized_factor_observations(
        store, REVERSAL_1D, _spec(), years=(YEAR,), as_of=MID_YEAR_BUILD
    )
    a_moment_earlier = load_neutralized_factor_observations(
        store,
        REVERSAL_1D,
        _spec(),
        years=(YEAR,),
        as_of=MID_YEAR_BUILD - timedelta(microseconds=1),
    )

    assert MID_YEAR_BUILD < AS_OF
    assert panel.sessions[-1] > MID_YEAR_SESSION
    assert {row.as_of for row in stored} == {MID_YEAR_BUILD}
    assert {row.subject: row.value for row in stored} == {
        row.subject: row.value for row in result.observations
    }
    assert any(row.value is not None for row in stored)
    assert a_moment_earlier == ()


def test_a_residual_is_computed_at_an_instant_the_unfiltered_door_still_refuses(
    wide_store: PanelStore, wide_panel: GeneratedPanel
) -> None:
    """`V2-P4-028`'s acceptance as a **number** rather than as a reachable code path.

    `test_the_neutralised_tier_builds_at_the_mid_window_instants_it_used_to_refuse` drives the
    same change from the command line and can only show that the build happened: the shipped
    `industry_and_size/v1` declares `min_cross_section = 100`, so on an eight-name panel every
    neutralised row is `insufficient_cross_section` and no residual is ever computed there. This
    test uses the probe spec, whose floors an eight-name panel clears, and asserts the residuals.

    **The pairing is what makes it a measurement.** `UNBLOCKED_BUILD` is 17:00 Asia/Shanghai on
    2026-01-09, and `wide_store`'s 2026 membership partition holds an assignment opening
    2026-01-14, so its newest `available_time` is *after* that instant -- which is exactly the
    condition `read_if_ready` decides `not_yet_knowable` on. `load_industry_histories` is called
    on the same store at the same instant and is required to still refuse. The whole three-tier
    build runs anyway, and its cross section carries the corpus's own answer for that day:
    `SECURITIES[1]` unclassified because its assignment closed 2026-01-06 and the next has not
    opened, and the halted name without a capitalisation.
    """
    with pytest.raises(PanelStorageError, match="not_yet_knowable"):
        load_industry_histories(
            wide_store, years=MEMBERSHIP_YEARS, as_of=UNBLOCKED_BUILD, max_staleness=None
        )

    section = load_industry_market_cap_cross_section(
        wide_store,
        _spec(),
        subjects=wide_panel.securities,
        day=UNBLOCKED_SESSION,
        as_of=UNBLOCKED_BUILD,
        calendar=wide_panel.calendar(),
        membership_years=MEMBERSHIP_YEARS,
        max_staleness=None,
    )
    assert section.without_industry == (SECURITIES[1],)
    assert section.without_market_cap == HALTED_ON_THE_NINTH

    result = _build_at(wide_store, wide_panel, as_of=UNBLOCKED_BUILD, day=UNBLOCKED_SESSION)
    write_neutralized_factor_panels(wide_store, [result])
    stored = load_neutralized_factor_observations(
        wide_store, REVERSAL_1D, _spec(), years=(YEAR,), as_of=UNBLOCKED_BUILD
    )

    assert {row.as_of for row in stored} == {UNBLOCKED_BUILD}
    assert {row.subject: row.value for row in stored} == {
        row.subject: row.value for row in result.observations
    }
    assert any(row.value is not None for row in stored)


def test_two_in_year_builds_give_the_ic_floor_of_two_as_ofs_two_points_inside_one_year(
    store: PanelStore, panel: GeneratedPanel
) -> None:
    """What the fix is worth counted in the unit the IC floor is stated in.

    `factor_ic.MINIMUM_IC_AS_OFS` is 2 -- a sample standard deviation of one number does not
    exist -- and before `V2-P4-026` a covered year could contribute exactly **one** neutralised
    `as_of`, because every build of that year had to be stamped at or after its last session. Two
    points therefore needed two years, and the earliest assemblable year is 2021 (the SW2021
    availability floor), so a series had at most one point per year since.

    Here two builds are run at two consecutive in-year sessions and both are readable at the
    later one -- two `as_ofs` inside a single covered year, and therefore a satisfiable
    `min_as_ofs=2` at daily spacing rather than annual. The two are asserted to be *different*
    cross sections, not merely two rows: they are about different sessions, so their residuals
    differ. The earlier one is also shown to be invisible before its own instant, so "two points"
    is two point-in-time answers rather than two rows that arrived together.
    """
    first = _build_at(store, panel, as_of=MID_YEAR_BUILD, day=MID_YEAR_SESSION)
    second = _build_at(store, panel, as_of=LATER_BUILD, day=LATER_SESSION)
    write_neutralized_factor_panels(store, [first, second])

    stored = load_neutralized_factor_observations(
        store, REVERSAL_1D, _spec(), years=(YEAR,), as_of=LATER_BUILD
    )
    only_the_first = load_neutralized_factor_observations(
        store, REVERSAL_1D, _spec(), years=(YEAR,), as_of=MID_YEAR_BUILD
    )

    assert {row.as_of for row in stored} == {MID_YEAR_BUILD, LATER_BUILD}
    assert {row.as_of for row in only_the_first} == {MID_YEAR_BUILD}
    assert MID_YEAR_BUILD.year == LATER_BUILD.year == YEAR
    assert LATER_BUILD < AS_OF
    assert {row.subject: row.value for row in first.observations} != {
        row.subject: row.value for row in second.observations
    }


def test_across_the_whole_window_only_the_industry_read_ever_refuses_an_in_year_as_of(
    store: PanelStore, panel: GeneratedPanel, wide_store: PanelStore, wide_panel: GeneratedPanel
) -> None:
    """The census behind `V2-P4-026`'s headline numbers, retaken after `V2-P4-028` moved them.

    Every session of the fixture's ten-session window is tried at 17:00 Asia/Shanghai on itself,
    and for each one two questions are asked separately: does `daily_basic` answer, and does the
    whole cross section assemble. Naming the answers as *sets of sessions* rather than as counts
    is what makes the test say which dataset is responsible.

    **The measurement, in three readings.** Before `V2-P4-026` the cross section assembled on
    **1** of 10 sessions on both fixtures -- the year-end instant and nothing else. After it, on
    5 of 10 under `SHAPES` and 3 of 10 under `WIDE_SHAPES`: the missing prefix was exactly the
    sessions before each membership partition's newest assignment became knowable (2026-01-12 and
    2026-01-14). After `V2-P4-028` it is **10 of 10 on both**, and the shape that used to produce
    the shorter census -- `WIDE_SHAPES`' assignment opening 2026-01-14, the fixture's stand-in for
    the annual constituent review -- no longer refuses a single session of the window.

    **A census of "it answers everywhere" would say nothing on its own**, so what each session
    answered *with* is asserted beside it. `WIDE_SHAPES` puts `SECURITIES[1]` in a coverage hole
    from 2026-01-07 to 2026-01-13, and that security is in `without_industry` on exactly the
    sessions inside it and in the cross section on the others. That is the corpus's own answer
    moving with the day, which is the thing an as-of-insensitive read could not produce and a
    read that had simply stopped refusing would get wrong.
    """
    hole = (date(2026, 1, 7), date(2026, 1, 13))
    censuses = {}
    for label, a_store, a_panel in (
        ("SHAPES", store, panel),
        ("WIDE_SHAPES", wide_store, wide_panel),
    ):
        answered, assembled = [], []
        unclassified: dict[date, tuple[str, ...]] = {}
        for session in a_panel.sessions:
            as_of = datetime(session.year, session.month, session.day, 9, 0, tzinfo=UTC)
            load_daily_valuations(
                a_store, day=session, calendar=a_panel.calendar(), as_of=as_of, max_staleness=None
            )
            answered.append(session)
            section = load_industry_market_cap_cross_section(
                a_store,
                _spec(),
                subjects=a_panel.securities,
                day=session,
                as_of=as_of,
                calendar=a_panel.calendar(),
                membership_years=MEMBERSHIP_YEARS,
                max_staleness=None,
            )
            assembled.append(session)
            unclassified[session] = section.without_industry
        censuses[label] = (tuple(answered), tuple(assembled), unclassified)

    for label, (answered, assembled, _unclassified) in censuses.items():
        a_panel = panel if label == "SHAPES" else wide_panel
        assert answered == tuple(a_panel.sessions), label
        assert assembled == tuple(a_panel.sessions), label
        assert len(assembled) >= MINIMUM_IC_AS_OFS, label

    assert len(censuses["SHAPES"][1]) == 10
    assert set(censuses["SHAPES"][2].values()) == {()}
    assert censuses["WIDE_SHAPES"][2] == {
        session: ((SECURITIES[1],) if hole[0] <= session <= hole[1] else ())
        for session in wide_panel.sessions
    }
    assert len(censuses["WIDE_SHAPES"][1]) == 10


def test_a_neutralised_row_is_invisible_before_the_as_of_it_was_computed_at(
    store: PanelStore, panel: GeneratedPanel
) -> None:
    """The filtered read doing its job on this plane's own output.

    A neutralised row's `available_time` is the `as_of` it was computed at, so an earlier read
    sees nothing -- and `read_if_ready` would refuse the whole partition rather than answering
    with the rows that were knowable, which is why this module is on `FILTERED_READ_CALLERS`.
    """
    result = _build(store, panel)
    write_neutralized_factor_panels(store, [result])

    earlier = load_neutralized_factor_observations(
        store, REVERSAL_1D, _spec(), years=(YEAR,), as_of=AS_OF - timedelta(days=1)
    )
    at_the_time = load_neutralized_factor_observations(
        store, REVERSAL_1D, _spec(), years=(YEAR,), as_of=AS_OF
    )

    assert earlier == ()
    assert len(at_the_time) == 8


def test_a_residual_about_a_session_is_invisible_at_that_sessions_own_close(
    store: PanelStore, panel: GeneratedPanel
) -> None:
    """A build's own schedule decides when its residuals become visible, and nothing else does.

    This test was written as "the second hop of the mid-year problem": the build's `as_of` had to
    sit at or after the `daily_basic` partition's newest row, `neutralized_observation_batch`
    stamps **every** clock on **every** row with that `as_of`, so a residual about a session was
    invisible at that session. `V2-P4-026` removed the first clause; the assertions are unchanged
    and still hold, because what they measure is the *stamping* rule and that is unchanged.

    What they now say is the narrower and more useful thing. `_build` runs at `AS_OF`, the
    fixture's year-end instant, so its residuals are visible from `AS_OF` and not before -- a read
    taken at the very end of the session they are about returns **empty**, hours after every input
    row became knowable. That is not a granularity defect any more; it is a build that was
    scheduled late, and `test_a_residual_built_at_a_mid_year_as_of_is_visible_at_that_same_as_of`
    runs the same chain earlier and gets the residuals back at the earlier instant. The pair is
    what `KNOWN_IC_LIMITATIONS
    .a_neutralised_series_is_only_as_point_in_time_as_its_build_schedule` names.

    The shape a reader has to watch for is unchanged and is the sharp part:
    `load_neutralized_factor_observations` filters rows, so an `as_of` no build was stamped at is
    a plausible-looking short answer rather than an error.
    """
    result = _build(store, panel)
    write_neutralized_factor_panels(store, [result])
    last_session = panel.sessions[-1]
    end_of_that_day = datetime.combine(last_session, time(23, 59), tzinfo=UTC)

    at_the_close = load_neutralized_factor_observations(
        store, REVERSAL_1D, _spec(), years=(YEAR,), as_of=end_of_that_day
    )
    afterwards = load_neutralized_factor_observations(
        store, REVERSAL_1D, _spec(), years=(YEAR,), as_of=AS_OF
    )

    assert last_session == SESSION
    assert end_of_that_day < AS_OF
    assert at_the_close == ()
    assert {row.as_of for row in afterwards} == {AS_OF}
    assert len(afterwards) == len(panel.securities)


def test_a_partition_carrying_an_undeclared_code_is_refused_where_the_dataset_can_be_named(
    store: PanelStore, panel: GeneratedPanel
) -> None:
    """A build that knows an eighth coverage code is a partition this build cannot interpret."""
    result = _build(store, panel)
    write_neutralized_factor_panels(store, [result])
    batch = store.read_coverage(NEUTRALIZED, YEAR)
    assert batch is not None

    from openalpha_cn.panel_neutralization import _neutralized_code, _stored_residual

    with pytest.raises(FactorEngineError, match="does not declare"):
        _neutralized_code("squashed", dataset=NEUTRALIZED)
    with pytest.raises(FactorEngineError, match="not a finite number"):
        _stored_residual("inf", dataset=NEUTRALIZED)


def test_a_stored_manifest_row_whose_identity_does_not_reassemble_is_refused(
    store: PanelStore, panel: GeneratedPanel
) -> None:
    """The decoder's self-check, driven by writing a row under the wrong subject."""
    result = _build(store, panel)
    write_neutralized_factor_panels(store, [result])
    from openalpha_cn.panel_neutralization import neutralization_manifest_batch

    batch = neutralization_manifest_batch(result)
    tampered = dataclasses.replace(batch, subjects=("fnm_wrong",))
    write_panel_batch(store, tampered, year=YEAR)

    with pytest.raises(FactorEngineError, match="reassembles to"):
        load_factor_neutralization_manifests(store, REVERSAL_1D, years=(YEAR,), as_of=AS_OF)


def test_a_hand_built_cross_section_can_carry_numbers_the_store_never_had(
    store: PanelStore, panel: GeneratedPanel
) -> None:
    """The disclosed residue, asserted rather than left as a sentence in a docstring.

    Nothing at the arithmetic layer can tell a cross section assembled from the store from one a
    caller invented and stamped with the right instant -- exactly as nothing can tell a computed
    `FactorPanel` from a hand-assembled one. The obstacle is that
    `load_industry_market_cap_cross_section` is the only builder in `src/`, not that this is
    impossible, and this test is what keeps the docstring from overclaiming.
    """
    processed = _process(_compute(store, panel))
    invented = build_industry_market_cap_cross_section(
        as_of=AS_OF,
        taxonomy=INDUSTRY_MEMBERSHIP_TAXONOMY,
        industry_level="L1",
        market_cap_measure="total_mv",
        characteristics=[
            SecurityCharacteristic(
                subject=code,
                industry_code="801999.SI",
                market_cap=1_000.0 + index,
                is_backfilled=False,
            )
            for index, code in enumerate(panel.securities)
        ],
    )

    result = _neutralize(processed, invented)

    assert result.coverage_census()["neutralized"] == 8
    assert set(result.industries().values()) == {"801999.SI"}


def test_a_cross_section_with_a_non_positive_capitalisation_cannot_be_built_at_all(
    store: PanelStore, panel: GeneratedPanel
) -> None:
    with pytest.raises(FactorNeutralizationError, match="finite positive number"):
        build_industry_market_cap_cross_section(
            as_of=AS_OF,
            taxonomy=INDUSTRY_MEMBERSHIP_TAXONOMY,
            industry_level="L1",
            market_cap_measure="total_mv",
            characteristics=[
                SecurityCharacteristic(
                    subject=panel.securities[0],
                    industry_code="801999.SI",
                    market_cap=0.0,
                    is_backfilled=False,
                )
            ],
        )


def test_a_security_the_industry_corpus_never_carried_lands_in_the_residue(
    store: PanelStore, panel: GeneratedPanel
) -> None:
    """A code `index_member_all` has no history for at all, which is one of three "no answer"s.

    The corpus carries 14 codes `stock_basic` has never had and the registry carries names the
    corpus does not (`IndustryCoverageReport.unknown_to_registry`), so a subject with no history
    is a real shape rather than an invented one. It is folded into `without_industry` with the
    other two, because all three mean "this build has no industry for this security on this day".
    """
    stranger = "999999.SZ"

    cross = load_industry_market_cap_cross_section(
        store,
        _spec(),
        subjects=(*panel.securities, stranger),
        day=SESSION,
        as_of=AS_OF,
        calendar=panel.calendar(),
        membership_years=MEMBERSHIP_YEARS,
        max_staleness=None,
    )

    assert cross.without_industry == (stranger,)
    assert stranger not in {item.subject for item in cross.characteristics}


def test_a_day_inside_a_coverage_hole_lands_in_the_residue_rather_than_carrying_a_stale_label(
    tmp_path: Path,
) -> None:
    """The 49 measured coverage holes, at the only scale a fixture reproduces them.

    `000639.SZ` is unclassified for **4,103 sessions** inside its listed life
    (`KNOWN_INDUSTRY_LIMITATIONS.a_security_can_be_unclassified_inside_its_listed_life`), and
    carrying the previous label across such a gap would be an answer no row supports.
    `SecurityIndustryHistory.assignment_on` refuses the day, and this builder turns that refusal
    into `industry_missing` rather than into an exception -- which is the right shape, because on
    a 2015 cross section the residue is 3% of the market and an exception would refuse every day.
    """
    holed = _with_market_caps(
        generate_panel(shapes=("daily.close_moves_between_sessions", "industry.coverage_hole"))
    )
    built = PanelStore(tmp_path / "panel")
    write_generated_panel(built, holed)

    cross = load_industry_market_cap_cross_section(
        built,
        _spec(),
        subjects=holed.securities,
        day=date(2026, 1, 9),
        as_of=AS_OF,
        calendar=holed.calendar(),
        membership_years=MEMBERSHIP_YEARS,
        max_staleness=None,
    )

    assert cross.without_industry == (SECURITIES[1],)
    assert SECURITIES[1] not in {item.subject for item in cross.characteristics}
    assert set(cross.subjects()) == set(holed.securities)


def test_a_security_with_no_daily_basic_row_that_session_lands_in_the_other_residue(
    tmp_path: Path,
) -> None:
    """`daily_basic` omitting a name the bars carry, which is measured rather than invented.

    60 of 3,843 securities on 2020-03-02 had a bar and no valuation, all `.BJ`
    (`panel_ingest.load_daily_valuations`). The builder puts such a name in `without_market_cap`
    and not in `without_industry`, because it *has* an industry -- and the two codes point at
    different datasets, so collapsing them would report a price-feed gap as a classification one.
    """
    thin = _with_market_caps(
        generate_panel(
            shapes=(
                "daily.close_moves_between_sessions",
                "industry.session_adjacent_handover",
                "daily.bar_without_valuation",
            )
        )
    )
    built = PanelStore(tmp_path / "panel")
    write_generated_panel(built, thin)

    cross = load_industry_market_cap_cross_section(
        built,
        _spec(),
        subjects=thin.securities,
        day=SESSION,
        as_of=AS_OF,
        calendar=thin.calendar(),
        membership_years=MEMBERSHIP_YEARS,
        max_staleness=None,
    )

    assert cross.without_market_cap == (LONE,)
    assert cross.without_industry == ()


def test_a_processed_panel_whose_manifest_names_another_factor_is_refused(
    store: PanelStore, panel: GeneratedPanel
) -> None:
    """The first half of the input-side guard: the manifest and the definition must agree.

    They are produced together by `apply_factor_transform`, so this never fires on its output --
    and `ProcessedFactorPanel` is a public frozen dataclass that anybody can assemble otherwise.
    """
    processed = _process(_compute(store, panel))
    mismatched = dataclasses.replace(
        processed,
        manifest=processed.manifest.model_copy(update={"source_factor_id": "fct_elsewhere"}),
    )

    with pytest.raises(FactorEngineError, match="cannot be neutralised"):
        _neutralize(mismatched, _cross_section(store, panel))


def test_a_neutralised_row_from_another_build_is_refused_at_the_write(
    store: PanelStore, panel: GeneratedPanel
) -> None:
    """The output-side guard's row half: a pointer, written as a fact, at a build this partition
    does not hold."""
    result = _build(store, panel)
    stray = dataclasses.replace(result.observations[0], neutralization_manifest_id="fnm_elsewhere")
    tampered = dataclasses.replace(result, observations=(stray, *result.observations[1:]))

    with pytest.raises(FactorEngineError, match="is a pointer at a build this partition"):
        write_neutralized_factor_panels(store, [tampered])


def test_both_loaders_refuse_a_partition_the_readiness_rule_blocks(
    store: PanelStore, panel: GeneratedPanel
) -> None:
    """A year the store does not hold is blocked by `partition_missing`, not answered empty.

    Both readers report the issue codes rather than a bare failure, because a caller holding an
    empty tuple cannot tell "this year has no builds" from "this year could not be read".
    """
    result = _build(store, panel)
    write_neutralized_factor_panels(store, [result])

    with pytest.raises(FactorEngineError, match="cannot be read at"):
        load_neutralized_factor_observations(
            store, REVERSAL_1D, _spec(), years=(YEAR - 1,), as_of=AS_OF
        )
    with pytest.raises(FactorEngineError, match="cannot be read at"):
        load_factor_neutralization_manifests(store, REVERSAL_1D, years=(YEAR - 1,), as_of=AS_OF)
