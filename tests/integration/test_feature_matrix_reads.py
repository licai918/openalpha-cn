"""One store, several instants, and the matrix each one produces (`V2-P4-012`).

The store-facing half of this issue; `tests/unit/test_feature_matrix_grammar.py` holds the
declaration and `tests/unit/test_feature_matrix_rules.py` the module's own rules.

## The corpus, and what each of its three builds separates

One generated panel and three cross sections of one factor, at three instants chosen so that the
two clocks this producer reads come apart:

    B1  2026-01-15T09:00Z   17:00 Shanghai, after that session's 16:30   session 2026-01-15
    B2  2026-01-16T04:00Z   12:00 Shanghai, before that session's 16:30  session 2026-01-15
    B3  2026-01-16T09:00Z   17:00 Shanghai, after that session's 16:30   session 2026-01-16

B2 is `V2-P4-077`'s shape and it is the one instant that cannot be read off a calendar day: its
own Shanghai day is 2026-01-16 and the market its values were computed from is 2026-01-15's. The
panel carries `universe.termination_on_the_newest_session`, so those two sessions have **different
markets** -- one name is listed on the 15th and terminated on the 16th -- which is what makes
"which session was this cut from" observable in the answer rather than only in a docstring.

The last of the eight securities is deliberately left out of every build's `subjects`, so it is a
listed name the factor partition has no row for at all. That is what the three missing-value
policies are measured on, and it is also `V2-P4-011`'s *scored or abstained, never absent* read one
layer up: the row is there, carrying `None`, rather than the security silently not being in the
matrix.
"""

from __future__ import annotations

import statistics
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Final

import pytest
from panel_fixtures import (
    AS_OF as PANEL_AS_OF,
)
from panel_fixtures import (
    SECURITIES,
    TERMINATED_SECURITY_INDEX,
    YEAR,
    GeneratedPanel,
    generate_panel,
    write_generated_panel,
)

from openalpha_cn.domain.alpha_model import AlphaModelDeclaration
from openalpha_cn.domain.factor import FactorDefinition, set_digest
from openalpha_cn.domain.panel_batch import ColumnarPanelBatch, PanelColumn, TimelineColumns
from openalpha_cn.domain.stock_universe import LISTING_EVENT, STOCK_BASIC_DATASET
from openalpha_cn.feature_matrix import (
    FeatureColumn,
    FeatureMatrixBlockedError,
    FeatureMatrixRequest,
    FeatureMatrixUnreadableError,
    FeatureMissingPolicy,
    FeatureSpecError,
    build_feature_matrix,
    load_feature_cross_section,
    require_declared_features,
    stored_cross_section_instants,
)
from openalpha_cn.panel.store import PanelStore
from openalpha_cn.panel_factors import (
    FACTOR_DEFINITIONS,
    FACTOR_TRANSFORMS,
    FactorPanel,
    apply_factor_transform,
    compute_factor,
    write_factor_panels,
    write_processed_factor_panels,
)
from openalpha_cn.panel_ingest import daily_requirement, write_stock_universe

REVERSAL: Final = FACTOR_DEFINITIONS.get("reversal_1d/v1")
MOMENTUM: Final = FACTOR_DEFINITIONS.get("momentum_20_sessions/v1")
COMMIT: Final[str] = "abcdef1234567"
EXCHANGE: Final[str] = "SZSE"
RAW_ID: Final[str] = "reversal_1d/v1@raw"

NARROW: Final = FACTOR_TRANSFORMS.get("cross_section_standard/v1").model_copy(
    update={"key": "cross_section_narrow", "min_cross_section": 2}
)
"""The shipped transform with its width floor lowered, and a key of its own.

`cross_section_standard/v1` declares `min_cross_section=100` and this panel lists eight names, so
every processed row on it comes back `insufficient_cross_section` and no processed column would
carry a number at all. `tests/integration/test_shortlist_interfaces.py` buys the real spec by
generating a 120-security panel; nothing measured here is about the width floor, so the floor
moves instead of the market.

**The key moves with it.** Keeping `cross_section_standard` would give this spec the shipped one's
`qualified_key` and a different `transform_id`, which is exactly the collision
`test_a_redefined_factor_that_kept_its_version_moves_the_version_and_not_the_ids` is about --
smuggling it into a fixture would make every id in this file ambiguous for no reason.
"""

PROCESSED_ID: Final[str] = "reversal_1d/v1@processed:cross_section_narrow/v1"

SUBJECTS: Final[tuple[str, ...]] = SECURITIES[:-1]
UNVALUED: Final[str] = SECURITIES[-1]
"""Listed, and in no build's `subjects`, so the partition holds no row for it."""

TERMINATED: Final[str] = SECURITIES[TERMINATED_SECURITY_INDEX]
"""The name `universe.termination_on_the_newest_session` retires on 2026-01-16."""

B1: Final[datetime] = datetime(2026, 1, 15, 9, 0, tzinfo=UTC)
B2: Final[datetime] = datetime(2026, 1, 16, 4, 0, tzinfo=UTC)
B3: Final[datetime] = datetime(2026, 1, 16, 9, 0, tzinfo=UTC)
BUILDS: Final[tuple[datetime, ...]] = (B1, B2, B3)

SESSION_15: Final[date] = date(2026, 1, 15)
SESSION_16: Final[date] = date(2026, 1, 16)

AT_B1: Final[datetime] = datetime(2026, 1, 15, 12, 0, tzinfo=UTC)
AT_B2: Final[datetime] = datetime(2026, 1, 16, 6, 0, tzinfo=UTC)
AT_B3: Final[datetime] = datetime(2026, 1, 16, 12, 0, tzinfo=UTC)
"""One `as_of` per build, each after its own build and before the next."""


def _raw_column(definition: FactorDefinition = REVERSAL) -> FeatureColumn:
    return FeatureColumn(definition=definition, tier="raw")


def _processed_column() -> FeatureColumn:
    return FeatureColumn(definition=REVERSAL, tier="processed", transform=NARROW)


def _request(
    *,
    as_ofs: tuple[datetime, ...],
    columns: tuple[FeatureColumn, ...] = (),
    missing: FeatureMissingPolicy = "abstain",
    years: tuple[int, ...] = (YEAR,),
) -> FeatureMatrixRequest:
    return FeatureMatrixRequest(
        columns=columns or (_raw_column(), _processed_column()),
        years=years,
        exchange=EXCHANGE,
        as_ofs=as_ofs,
        missing=missing,
    )


def _build(
    store: PanelStore,
    panel: GeneratedPanel,
    instant: datetime,
    *,
    definition: FactorDefinition = REVERSAL,
) -> FactorPanel:
    """One raw cross section, through the real engine and its documented evaluator seam.

    The evaluator is substituted rather than the factor arithmetic driven, for
    `test_shortlist_earlier_sessions.py`'s reason: what is under test is which market a stored
    cross section is offered to, not what the numbers are, and two builds computing the same
    numbers could not show it.
    """
    return compute_factor(
        store,
        definition,
        as_of=instant,
        subjects=SUBJECTS,
        universe=frozenset(panel.securities),
        requirements={
            "daily": daily_requirement(
                panel.calendar(), years=(YEAR,), as_of=instant, max_staleness=timedelta(days=30)
            )
        },
        code_commit=COMMIT,
        built_at=instant,
        evaluators={
            definition.qualified_key: lambda context: (SUBJECTS.index(context.subject) + 1) / 100.0
        },
    )


@pytest.fixture(scope="module")
def store(tmp_path_factory: pytest.TempPathFactory) -> Iterator[PanelStore]:
    """One panel and three cross sections on two tiers. See this module's docstring."""
    root = tmp_path_factory.mktemp("feature-matrix")
    built = PanelStore(root / "panel")
    panel = generate_panel(shapes=("universe.termination_on_the_newest_session",))
    write_generated_panel(built, panel)
    raws = [_build(built, panel, instant) for instant in BUILDS]
    write_factor_panels(built, raws)
    write_processed_factor_panels(
        built,
        [
            apply_factor_transform(raw, NARROW, code_commit=COMMIT, built_at=raw.as_of)
            for raw in raws
        ],
    )
    yield built


def test_the_row_set_is_the_market_and_an_unvalued_security_abstains(store: PanelStore) -> None:
    """The row set is the market, which is where this producer parts company with `V2-P4-032`.

    `shortlist_view._component_cross_section` deliberately does *not* narrow its stored rows to
    the registry, because `CrossSectionScreen._read_components` already drops a row for a
    security `universe` does not name and one rule in two places is two rules. There is no
    second filter here -- `FeatureCrossSection` carries whatever rows it is given and
    `AlphaModel.predict` answers about every one of them -- so the narrowing happens here or not
    at all.

    Both directions are asserted, because either alone passes on a mutant. A matrix built out of
    "the rows that came back" would drop the unvalued security, which the registry lists; a
    matrix that never narrowed to the registry would carry every security the partition holds.
    """
    section = load_feature_cross_section(store, _request(as_ofs=(AT_B1,)), as_of=AT_B1)

    assert section.subjects == section.universe
    assert UNVALUED in section.subjects
    assert section.cross_section.value(ts_code=UNVALUED, feature_id=RAW_ID) is None
    assert section.cross_section.value(ts_code=SECURITIES[0], feature_id=RAW_ID) is not None


def test_a_value_stamped_after_the_requested_as_of_never_reaches_the_matrix(
    store: PanelStore,
) -> None:
    """Both halves off one store, because asserting only the first passes on an empty answer.

    At `AT_B1` the store already holds B2 and B3 and neither is visible; at `AT_B3` all three
    are and the newest is what answers. A producer that read the whole partition would give the
    same cross section at both instants, and one that returned nothing would pass the first half
    alone.
    """
    early = load_feature_cross_section(store, _request(as_ofs=(AT_B1,)), as_of=AT_B1)
    late = load_feature_cross_section(store, _request(as_ofs=(AT_B3,)), as_of=AT_B3)

    assert early.as_of == B1
    assert late.as_of == B3


def test_the_session_a_matrix_is_cut_from_is_the_one_its_values_saw(store: PanelStore) -> None:
    """`V2-P4-077` on this plane: B2's own Shanghai day is not the market it was built from.

    B2 stands at noon Shanghai on 2026-01-16, four and a half hours before that session
    publishes, so its factor inputs stop at 2026-01-15's close. `newest_published_session`
    answers 2026-01-15 and the calendar-day rule would answer 2026-01-16 -- and on this panel the
    two markets differ by a name, so the two answers are separable rather than merely different
    in principle.
    """
    section = load_feature_cross_section(store, _request(as_ofs=(AT_B2,)), as_of=AT_B2)

    assert section.as_of == B2
    assert section.session == SESSION_15
    assert TERMINATED in section.universe


def test_an_old_cross_section_is_cut_from_its_own_market_and_not_from_a_later_one(
    tmp_path: Path,
) -> None:
    """The asymmetry between this module's two `as_of`s, on the one corpus that separates it.

    **This test exists because a mutation survived.** Every `as_of` in the module-scoped store is
    "after its own build and before the next", and on that corpus each one happens to resolve to
    the same session its build does -- so swapping the calendar and registry reads from the
    resolved instant to the request's `as_of` left all forty tests green. The assertion existed
    and could not separate the two answers, which is the shape
    `tests/integration/test_shortlist_earlier_sessions.py::EARLIER_AS_OF` documents one plane
    over: *"if a run's `as_of` and its cross section's instant fell on one session, a face that
    priced on the day the question was asked would be indistinguishable from one that priced on
    the day the values were computed"*.

    So this store holds **one** build, at B1 on 2026-01-15, and is asked at `AT_B3` on the
    evening of 2026-01-16. The resolved instant is B1 either way; what moves is the session and
    with it the market, because the panel retires a name on the 16th. Reading at the request's
    `as_of` gives 2026-01-16 and a market of seven; reading at the resolved instant gives
    2026-01-15 and a market of eight. Both are asserted, and so is the fact that the requested
    instant really is on the later session -- without that, the fixture could drift into being
    the same one-session case again and nobody would know.
    """
    store = PanelStore(tmp_path / "panel")
    panel = generate_panel(shapes=("universe.termination_on_the_newest_session",))
    write_generated_panel(store, panel)
    write_factor_panels(store, [_build(store, panel, B1)])
    section = load_feature_cross_section(
        store, _request(as_ofs=(AT_B3,), columns=(_raw_column(),)), as_of=AT_B3
    )

    assert section.as_of == B1
    assert AT_B3 > B1
    assert section.session == SESSION_15
    assert TERMINATED in section.universe
    assert section.universe_version == set_digest(section.universe)


def test_a_terminated_security_leaves_the_next_sessions_matrix_and_its_universe_version(
    store: PanelStore,
) -> None:
    """The universe version is the market, measured by moving the market and nothing else.

    B2 and B3 are two instants, two stored cross sections and the same requested `years`. The
    only thing that differs is which session they are about, and therefore who the registry
    lists. A `universe_version` derived from the requested years -- or from `years_read`, which
    `V2-P4-059`'s downward widening makes identical for both -- could not move here, and this one
    does.
    """
    earlier = load_feature_cross_section(store, _request(as_ofs=(AT_B2,)), as_of=AT_B2)
    later = load_feature_cross_section(store, _request(as_ofs=(AT_B3,)), as_of=AT_B3)

    assert later.session == SESSION_16
    assert TERMINATED not in later.universe
    assert set(earlier.universe) - set(later.universe) == {TERMINATED}
    assert earlier.universe_version == set_digest(earlier.universe)
    assert earlier.universe_version != later.universe_version


def test_two_instants_about_one_session_are_refused_rather_than_counted_twice(
    store: PanelStore,
) -> None:
    """B1 and B2 are two builds, two instants and one market.

    The refusal is keyed on the **session** rather than on the resolved instant, and B1/B2 is the
    pair that separates the two rules: they resolve to different `as_of`s, so an instant-keyed
    check answers happily and hands `V2-P4-013` two observations of 2026-01-15's market to split
    between two folds.
    """
    with pytest.raises(FeatureMatrixBlockedError, match="2026-01-15"):
        build_feature_matrix(store, _request(as_ofs=(AT_B1, AT_B2)))


def test_a_matrix_over_two_sessions_carries_one_recipe_and_two_markets(
    store: PanelStore,
) -> None:
    """What S26 asks for, assembled: one `feature_version`, one `universe_version` per market.

    The matrix-level universe version is a `set_digest` over the sections' own
    `(session, universe)` pairs, so it moves when any section's market does and not when the
    instants do. Measured against a one-section matrix rather than asserted: the two-section
    answer is neither section's own digest and is not the one-section matrix's.
    """
    matrix = build_feature_matrix(store, _request(as_ofs=(AT_B2, AT_B3)))
    alone = build_feature_matrix(store, _request(as_ofs=(AT_B2,)))

    assert len(matrix.sections) == 2
    assert matrix.feature_version == alone.feature_version
    assert matrix.feature_ids == (PROCESSED_ID, RAW_ID)
    assert [section.session for section in matrix.sections] == [SESSION_15, SESSION_16]
    assert matrix.universe_version not in {section.universe_version for section in matrix.sections}
    assert matrix.universe_version != alone.universe_version


def test_the_three_missing_value_policies_answer_three_different_matrices(
    store: PanelStore,
) -> None:
    """Preprocessing is declared, applied, and inside the address.

    The unvalued security is listed and missing on both columns, which is the one cell the three
    policies disagree about. `abstain` keeps it as `None`, `drop_security` removes the row, and
    `cross_section_median` fills it with the median of the column's admitted values -- so the
    three answers differ in the row count, in the cell, or in both, and no two of them can be
    confused.

    **The universe version does not move with the policy**, and that is asserted here rather than
    left implicit: `drop_security` answers about the same market and hands back fewer rows, so a
    `universe_version` taken over the *rows* would call two cuts of one market two markets. It is
    taken over the registry's listed set, which is why `subjects` and `universe` come apart under
    exactly one of the three.
    """
    answers = {
        policy: load_feature_cross_section(
            store, _request(as_ofs=(AT_B1,), missing=policy), as_of=AT_B1
        )
        for policy in ("abstain", "drop_security", "cross_section_median")
    }
    admitted = [
        value
        for ts_code in answers["abstain"].subjects
        if (value := answers["abstain"].cross_section.value(ts_code=ts_code, feature_id=RAW_ID))
        is not None
    ]

    assert answers["abstain"].cross_section.value(ts_code=UNVALUED, feature_id=RAW_ID) is None
    assert UNVALUED not in answers["drop_security"].subjects
    assert len(answers["drop_security"].subjects) == len(answers["abstain"].subjects) - 1
    assert answers["cross_section_median"].subjects == answers["abstain"].subjects
    assert answers["cross_section_median"].cross_section.value(
        ts_code=UNVALUED, feature_id=RAW_ID
    ) == statistics.median(admitted)
    assert answers["drop_security"].universe == answers["abstain"].universe
    assert answers["drop_security"].subjects != answers["drop_security"].universe
    assert len({answer.universe_version for answer in answers.values()}) == 1


def test_a_median_fill_is_taken_from_the_instant_it_fills_and_no_other(
    store: PanelStore,
) -> None:
    """The claim that makes `cross_section_median` look-ahead-free, driven rather than argued.

    The evaluator gives every build the same values, so a fill that leaked across instants would
    be invisible. What separates them is the **market**: 2026-01-16 has one security fewer, so
    its column has one admitted value fewer and its median moves. A fill computed over the whole
    partition, or over the newest cross section, would give both sections one number.
    """
    matrix = build_feature_matrix(
        store, _request(as_ofs=(AT_B2, AT_B3), missing="cross_section_median")
    )
    fills = [
        section.cross_section.value(ts_code=UNVALUED, feature_id=RAW_ID)
        for section in matrix.sections
    ]

    assert len(matrix.sections[0].universe) == len(matrix.sections[1].universe) + 1
    assert fills[0] != fills[1]


def test_a_column_with_no_stored_build_refuses_the_instant_by_name(store: PanelStore) -> None:
    """One column short is no matrix, and the refusal says which column.

    `momentum_20_sessions/v1` is a shipped factor this store has never built, so its partition is
    absent -- which the panel plane reports as a read it cannot make, not as an empty answer. The
    two states are separated in `test_a_declared_column_whose_build_is_not_yet_visible_is_blocked`,
    which asks about a partition that exists and holds nothing visible yet.
    """
    with pytest.raises(FeatureMatrixUnreadableError, match="momentum_20_sessions"):
        load_feature_cross_section(
            store,
            _request(as_ofs=(AT_B1,), columns=(_raw_column(), _raw_column(MOMENTUM))),
            as_of=AT_B1,
        )


def test_a_declared_column_whose_build_is_not_yet_visible_is_blocked(store: PanelStore) -> None:
    """The partition is there, the read succeeds, and nothing in it is visible yet.

    An `as_of` before the first build. This is the state a caller repairs by asking later or by
    building, and it is deliberately a different fault from the one above -- an operator told
    "unreadable" builds a panel and an operator told "no stored cross section" builds a tier.
    """
    with pytest.raises(FeatureMatrixBlockedError, match="no stored cross section"):
        load_feature_cross_section(
            store,
            _request(as_ofs=(AT_B1,), columns=(_raw_column(),)),
            as_of=datetime(2026, 1, 6, 12, 0, tzinfo=UTC),
        )


def test_two_columns_built_at_two_instants_are_refused_rather_than_mixed(tmp_path: Path) -> None:
    """`the_three_tiers_must_have_been_built_at_the_same_instants`, applied across columns.

    A store whose raw tier reaches B3 and whose processed tier stops at B1. Each column answers
    on its own -- both halves are asserted, so a producer that refused everything would fail here
    -- and the pair is refused, naming both instants, because a caller told only `blocked` cannot
    tell which column is stale.

    The module-scoped store cannot show this: it builds both tiers at all three instants, which
    is what every other test here needs.
    """
    store = PanelStore(tmp_path / "panel")
    panel = generate_panel()
    write_generated_panel(store, panel)
    raws = [_build(store, panel, instant) for instant in (B1, B3)]
    write_factor_panels(store, raws)
    write_processed_factor_panels(
        store, [apply_factor_transform(raws[0], NARROW, code_commit=COMMIT, built_at=B1)]
    )

    assert (
        load_feature_cross_section(
            store, _request(as_ofs=(AT_B3,), columns=(_raw_column(),)), as_of=AT_B3
        ).as_of
        == B3
    )
    assert (
        load_feature_cross_section(
            store, _request(as_ofs=(AT_B3,), columns=(_processed_column(),)), as_of=AT_B3
        ).as_of
        == B1
    )
    with pytest.raises(FeatureMatrixBlockedError) as refused:
        load_feature_cross_section(store, _request(as_ofs=(AT_B3,)), as_of=AT_B3)

    assert B1.isoformat() in str(refused.value)
    assert B3.isoformat() in str(refused.value)


def test_a_matrix_may_not_be_offered_to_a_declaration_that_names_another_recipe(
    store: PanelStore,
) -> None:
    """`require_declared_features` is what makes "versioned" executable.

    Both directions on one matrix: the declaration carrying this matrix's own `feature_version`
    is accepted, and the same declaration carrying the string `V2-P4-011`'s own fixtures use is
    refused. Without the second half the check would pass on a function that never raises;
    without the first it would pass on one that always does.
    """
    matrix = build_feature_matrix(store, _request(as_ofs=(AT_B1,)))
    declared = AlphaModelDeclaration(
        name="reference_momentum",
        family="linear",
        horizon="5d",
        feature_version=matrix.feature_version,
        seed=7,
        code_commit=COMMIT,
    )

    require_declared_features(declared, matrix.spec)
    with pytest.raises(FeatureSpecError, match="features/v1"):
        require_declared_features(
            declared.model_copy(update={"feature_version": "features/v1"}), matrix.spec
        )


EARLY_LISTED: Final[str] = "000005.SZ"
EARLY_LISTING: Final[date] = date(1996, 5, 6)
"""A security whose lifecycle row sits in a partition below every year this request names.

`V2-P4-059`'s shape, at the smallest size that reproduces it: `stock_basic` is keyed by the year
a security's life *changed*, so a name that listed in 1996 and never died has its only row in the
1996 partition and is invisible to a read that starts at 2026 -- which is how `factor build
--year 2026` over a 5,545-security market scored eleven.
"""


def _early_listing_batch() -> ColumnarPanelBatch:
    """One lifecycle row, shaped the way `providers/tushare.py` files a listing.

    Built here rather than taken from `panel_fixtures`, whose generator dates every lifecycle row
    at `LISTED_ON` inside the window: what this test needs is a *second lifecycle year*, which is
    a property of the partition layout rather than of any panel shape. `calendar_static` is the
    clock -- `available_time == event_time ==` midnight on the lifecycle date -- and the row is a
    listing alone, because `stock_universe_from_panel_rows` refuses a termination whose listing it
    cannot see and a listing with no termination is a security that is still trading.
    """
    midnight = datetime(
        EARLY_LISTING.year, EARLY_LISTING.month, EARLY_LISTING.day, tzinfo=UTC
    ) - timedelta(hours=8)
    return ColumnarPanelBatch(
        provider_id="tushare",
        dataset=STOCK_BASIC_DATASET,
        kind=STOCK_BASIC_DATASET,
        as_of=PANEL_AS_OF,
        fetched_at=PANEL_AS_OF,
        status="success",
        subjects=(EARLY_LISTED,),
        timeline=TimelineColumns(
            event_time=(midnight,),
            available_time=(midnight,),
            ingested_time=(midnight,),
            revision_time=(midnight,),
        ),
        columns=(
            PanelColumn("lifecycle_event", "string", (LISTING_EVENT,)),
            PanelColumn("lifecycle_date", "string", (EARLY_LISTING.isoformat(),)),
            PanelColumn("exchange", "string", (EXCHANGE,)),
        ),
    )


def test_the_universe_version_is_the_market_and_not_the_year_that_was_asked_for(
    tmp_path: Path,
) -> None:
    """`V2-P4-059`'s downward widening, and the version that has to survive it.

    One store, one request, one `years=(2026,)`, read twice with a 1996 lifecycle partition
    written in between. Nothing the caller says changes; the registry's answer does, because
    `load_stock_universe` reads every lifecycle year the store holds *below* the earliest
    requested one whether it was asked for or not. So the market grows by a name and the
    universe version moves with it.

    Asking for 1996 explicitly is **not** the alternative and is not tested as one: `years` is
    one scope over three datasets, so `years=(1996, 2026)` asks the calendar for a 1996 partition
    and the factor plane for a 1996 year, and on any store built the way `README` builds one
    neither exists. That is the measurement `load_stock_universe`'s own docstring records, and it
    is why the widening happens unasked.

    **This half alone does not pin the version**, and the pairing is stated rather than assumed:
    a version derived from `UniverseCompleteness.years_read` would also move here, since the read
    widened from `(2026,)` to `(1996, 2026)`.
    `test_a_terminated_security_leaves_the_next_sessions_matrix_and_its_universe_version` is the
    other half -- the market moves while `years_read` does not -- and only the two together
    leave `set_digest` over the listed set as the answer.
    """
    store = PanelStore(tmp_path / "panel")
    panel = generate_panel()
    write_generated_panel(store, panel)
    write_factor_panels(store, [_build(store, panel, B1)])
    request = _request(as_ofs=(AT_B1,), columns=(_raw_column(),))
    before = load_feature_cross_section(store, request, as_of=AT_B1)

    write_stock_universe(store, _early_listing_batch())
    after = load_feature_cross_section(store, request, as_of=AT_B1)

    assert store.registered_years(STOCK_BASIC_DATASET) == (EARLY_LISTING.year, YEAR)
    assert request.years == (YEAR,)
    assert EARLY_LISTED not in before.universe
    assert set(after.universe) - set(before.universe) == {EARLY_LISTED}
    assert after.universe_version != before.universe_version
    assert after.universe_version == set_digest(after.universe)
    assert after.cross_section.value(ts_code=EARLY_LISTED, feature_id=RAW_ID) is None


def test_a_stored_row_written_under_another_definition_of_the_same_key_is_refused(
    tmp_path: Path,
) -> None:
    """The hole a readable id cannot see and the loaders do not close.

    `factor_observation_dataset` is `factor_obs_<key>_v<n>` -- the factor's *handle* -- and
    `load_factor_observations`' own docstring says "The factor is the **dataset**, not a filter".
    So a redefinition that kept its version writes its builds into the partition a read of the
    original opens, and every one of them comes back. Measured here rather than assumed: one
    panel, one build under the shipped `reversal_1d/v1` and one under a `lookback_sessions`-3
    redefinition of the same handle, both in one partition, and the second refused when the first
    is what was declared.

    Both directions, because the refusal has to be about the address and not about the store: the
    same read at an `as_of` that sees only the declared build answers.

    This is what makes `feature_version` a claim about the numbers rather than only about the
    declaration -- `tests/unit/test_feature_matrix_grammar.py::
    test_a_redefined_factor_that_kept_its_version_moves_the_version_and_not_the_ids` is the
    declaration half of the same case.
    """
    store = PanelStore(tmp_path / "panel")
    panel = generate_panel()
    write_generated_panel(store, panel)
    redefined = REVERSAL.model_copy(update={"lookback_sessions": 3})
    write_factor_panels(
        store,
        [
            _build(store, panel, B1),
            _build(store, panel, B3, definition=redefined),
        ],
    )
    request = _request(as_ofs=(AT_B1,), columns=(_raw_column(),))

    assert redefined.qualified_key == REVERSAL.qualified_key
    assert redefined.factor_id != REVERSAL.factor_id
    assert load_feature_cross_section(store, request, as_of=AT_B1).as_of == B1
    with pytest.raises(FeatureMatrixBlockedError, match="redefinition"):
        load_feature_cross_section(store, request, as_of=AT_B3)


# --- which instants a caller can ask about (`V2-P4-021`) ----------------------------------------


def test_the_stored_instants_are_every_build_visible_at_the_as_of(store: PanelStore) -> None:
    """`stored_cross_section_instants` is what lets a face take a **range** of prediction days.

    `V2-P4-021` needed it: a walk-forward is intrinsically over many days, and a face that made a
    caller name each instant would be a face nobody runs a schedule through. It is here rather
    than in `model_view` because the read is this module's -- `_rows_for` is what knows which
    loader answers for which tier, and a second reader would be a second thing that can disagree
    about visibility.

    The `as_of` narrows it exactly as every other read here does: at `AT_B1` the store already
    holds B2 and B3 and neither is visible. Asserting only that would pass on a function that
    returned nothing, so both ends are driven.
    """
    columns = (_raw_column(),)

    assert stored_cross_section_instants(store, columns=columns, years=(YEAR,), as_of=AT_B1) == (
        B1,
    )
    assert (
        stored_cross_section_instants(store, columns=columns, years=(YEAR,), as_of=AT_B3) == BUILDS
    )


def test_the_stored_instants_are_the_intersection_and_not_the_union(tmp_path: Path) -> None:
    """An instant one column has a build at and another does not is not a candidate.

    `_resolve_instant` refuses to assemble a row out of two columns' different instants -- a row
    from one factor's Friday and another's Monday is a row about two markets -- so offering such
    an instant as a candidate would only move that refusal later, past a labelling read that
    costs a partition per session. The union is the shape a first draft had; this is the
    measurement that they differ.
    """
    store = PanelStore(tmp_path / "panel")
    panel = generate_panel()
    write_generated_panel(store, panel)
    raws = [_build(store, panel, B1), _build(store, panel, B3)]
    write_factor_panels(store, raws)
    write_processed_factor_panels(
        store, [apply_factor_transform(raws[0], NARROW, code_commit=COMMIT, built_at=B1)]
    )
    columns = (_raw_column(), _processed_column())

    shared = stored_cross_section_instants(store, columns=columns, years=(YEAR,), as_of=AT_B3)
    alone = stored_cross_section_instants(
        store, columns=(_raw_column(),), years=(YEAR,), as_of=AT_B3
    )

    assert shared == (B1,)
    assert alone == (B1, B3)


def test_a_request_declaring_no_column_has_no_instants_to_share(store: PanelStore) -> None:
    """Refused rather than answered `()`, because the two mean different things.

    An empty answer here would say "no build is visible", which is a statement about the store; a
    request with no column is a statement about the request, and `FeatureSpec` refuses one
    everywhere else for the same reason.
    """
    with pytest.raises(FeatureSpecError, match="no column was declared"):
        stored_cross_section_instants(store, columns=(), years=(YEAR,), as_of=AT_B3)
