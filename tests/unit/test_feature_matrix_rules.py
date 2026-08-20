"""The feature matrix's own rules, reachable without a store (`V2-P4-012`).

`tests/unit/test_feature_matrix_grammar.py` holds the declaration and the two versions;
`tests/integration/test_feature_matrix_reads.py` drives the producer against a real panel. What
is here is the middle: the request's own refusals, the coverage table the cells are read through,
the three preprocessing policies at the two boundaries a real panel cannot cheaply reach, and the
limitation registry.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Final

import pytest

from openalpha_cn.domain.alpha_model import FeatureCrossSection, FeatureRow
from openalpha_cn.domain.factor import set_digest
from openalpha_cn.feature_matrix import (
    FEATURE_MATRIX_LIMITATION_CODES,
    KNOWN_FEATURE_MATRIX_LIMITATIONS,
    FeatureColumn,
    FeatureMatrix,
    FeatureMatrixBlockedError,
    FeatureMatrixRequest,
    FeatureMatrixSection,
    FeatureSpecError,
    _admitted_cells,
    _rows_after_preprocessing,
    feature_spec,
)
from openalpha_cn.panel_factors import FACTOR_DEFINITIONS, FACTOR_TRANSFORMS

REVERSAL: Final = FACTOR_DEFINITIONS.get("reversal_1d/v1")
TRANSFORM: Final = FACTOR_TRANSFORMS.get("cross_section_standard/v1")
INSTANT: Final[datetime] = datetime(2026, 1, 16, 9, 0, tzinfo=UTC)
RAW: Final = FeatureColumn(definition=REVERSAL, tier="raw")
PROCESSED: Final = FeatureColumn(definition=REVERSAL, tier="processed", transform=TRANSFORM)


def test_the_known_feature_matrix_limitations_are_the_five_this_plane_declares() -> None:
    """Equality rather than membership: a membership assertion sees a rename and never a removal.

    `KNOWN_SHORTLIST_VIEW_LIMITATIONS`' form, and the five are the five boundaries this plane
    has that nothing else on the chain records -- what the two versions do **not** address, what
    the transform's own imputation costs here, what a cross-sectional median is measured over,
    what one unbuilt column does to a whole instant, and the difference between listed and
    tradeable.
    """
    assert {
        "the_two_versions_do_not_address_the_stored_values_they_were_read_from",
        "a_processed_value_the_transform_imputed_is_read_as_missing",
        "a_median_fill_is_measured_over_the_admitted_cells_of_one_instant_only",
        "one_declared_column_missing_a_stored_build_refuses_the_whole_instant",
        "a_universe_version_says_who_was_listed_and_not_who_was_tradeable",
    } == FEATURE_MATRIX_LIMITATION_CODES
    assert len(KNOWN_FEATURE_MATRIX_LIMITATIONS) == 5
    assert all(limitation.detail.strip() for limitation in KNOWN_FEATURE_MATRIX_LIMITATIONS)


# --- the coverage table the cells are read through ---------------------------------------------


def _processed_row(coverage: str, *, value: float | None = 1.5) -> tuple[object, ...]:
    return (
        "000001.SZ",
        value,
        coverage,
        INSTANT,
        (REVERSAL.factor_id, TRANSFORM.transform_id),
    )


def test_a_processed_value_the_transform_imputed_is_read_as_missing() -> None:
    """`TIER_ADMITTED_CODES` and not `TIER_VALUE_CODES`, which differ in exactly this cell.

    A processed row under `imputed` carries a real number -- the transform's declared
    `fill_cross_sectional_median` produced it -- and it is read here as missing anyway, so this
    matrix's own declared policy is what decides it. Two imputations stacked make a cell nobody
    can attribute to either, and only one of the two is inside `feature_version`.

    Driven at this level rather than on a panel because producing an `imputed` processed row
    end to end needs a raw row with `input_missing` coverage, which is a property of the price
    fixture rather than of anything this module does. The table is what decides, and the table
    is what is exercised: both codes go through the same call with the same value.
    """
    imputed = _admitted_cells(PROCESSED, [_processed_row("imputed")], instant=INSTANT)  # type: ignore[arg-type]
    measured = _admitted_cells(PROCESSED, [_processed_row("processed")], instant=INSTANT)  # type: ignore[arg-type]

    assert imputed == {}
    assert measured == {"000001.SZ": 1.5}


def test_a_row_from_an_older_build_in_the_same_partition_is_not_this_cross_section() -> None:
    """A year partition visible at an `as_of` holds every build up to it.

    `shortlist_view._component_cross_section`'s narrowing, restated here because this module
    reads it out of a different shape. Asserting only that the matching row is kept would pass
    on a function that kept everything.
    """
    older = (
        "000002.SZ",
        9.9,
        "processed",
        datetime(2026, 1, 15, 9, 0, tzinfo=UTC),
        (
            REVERSAL.factor_id,
            TRANSFORM.transform_id,
        ),
    )

    assert _admitted_cells(  # type: ignore[arg-type]
        PROCESSED, [_processed_row("processed"), older], instant=INSTANT
    ) == {"000001.SZ": 1.5}


def test_a_row_written_under_another_specs_address_is_refused_and_names_both() -> None:
    """The read-time half of "versioned". See the integration test for the store-level case.

    The message carries the declared addresses and the stored ones, because the remedy differs:
    a caller who redefined the factor bumps its version, and a caller who declared the wrong
    definition changes the declaration.
    """
    foreign = ("000001.SZ", 1.5, "processed", INSTANT, (REVERSAL.factor_id, "ftx_" + "0" * 24))

    with pytest.raises(FeatureMatrixBlockedError) as refused:
        _admitted_cells(PROCESSED, [foreign], instant=INSTANT)  # type: ignore[arg-type]

    assert TRANSFORM.transform_id in str(refused.value)
    assert "ftx_" + "0" * 24 in str(refused.value)


# --- the three policies, at the two boundaries a panel cannot cheaply reach ---------------------


def test_a_median_over_a_column_with_no_admitted_cell_is_refused_rather_than_filled() -> None:
    """There is no median of nothing, and a column of `0.0` would be a fabricated market.

    The refusal names the column, because on a multi-column matrix the caller has to know which
    one to drop.
    """
    with pytest.raises(FeatureMatrixBlockedError, match="reversal_1d/v1@raw"):
        _rows_after_preprocessing(
            universe=("000001.SZ", "000002.SZ"),
            columns=(RAW,),
            cells={RAW.feature_id: {}},
            missing="cross_section_median",
            instant=INSTANT,
        )


def test_drop_security_that_empties_the_market_is_refused_rather_than_returned_empty() -> None:
    """`FeatureCrossSection` would refuse an empty row set anyway; this says which policy did it.

    The two refusals are not the same message: the contract's is "there is nothing to predict
    about", and a caller reading that would look for a store problem when what happened is that
    a declared policy removed every row.
    """
    with pytest.raises(FeatureMatrixBlockedError, match="drop_security"):
        _rows_after_preprocessing(
            universe=("000001.SZ", "000002.SZ"),
            columns=(RAW,),
            cells={RAW.feature_id: {}},
            missing="drop_security",
            instant=INSTANT,
        )


def test_abstain_keeps_a_security_no_column_has_a_value_for() -> None:
    """`V2-P4-011`'s *scored or abstained, never absent*, one layer up.

    The default, and the only policy under which the matrix asserts nothing the panel did not
    measure. Both securities keep a row and both cells are `None`.
    """
    rows = _rows_after_preprocessing(
        universe=("000001.SZ", "000002.SZ"),
        columns=(RAW,),
        cells={RAW.feature_id: {"000001.SZ": 0.25}},
        missing="abstain",
        instant=INSTANT,
    )

    assert rows == (
        FeatureRow(ts_code="000001.SZ", values=(0.25,)),
        FeatureRow(ts_code="000002.SZ", values=(None,)),
    )


def test_a_median_fill_is_the_median_of_the_admitted_cells_and_not_of_the_filled_ones() -> None:
    """An even sample averages the two middles, and the fill does not feed back into itself.

    Three securities, two admitted values, one hole: the fill is the mean of the two, and a
    second column's hole is filled from that column's own two rather than from the whole grid.
    """
    rows = _rows_after_preprocessing(
        universe=("000001.SZ", "000002.SZ", "000003.SZ"),
        columns=(PROCESSED, RAW),
        cells={
            PROCESSED.feature_id: {"000001.SZ": 1.0, "000002.SZ": 3.0},
            RAW.feature_id: {"000002.SZ": 10.0, "000003.SZ": 20.0},
        },
        missing="cross_section_median",
        instant=INSTANT,
    )

    assert rows == (
        FeatureRow(ts_code="000001.SZ", values=(1.0, 15.0)),
        FeatureRow(ts_code="000002.SZ", values=(3.0, 10.0)),
        FeatureRow(ts_code="000003.SZ", values=(2.0, 20.0)),
    )


# --- the request, which touches no store --------------------------------------------------------


def _request(**overrides: object) -> FeatureMatrixRequest:
    arguments: dict[str, object] = {
        "columns": (RAW,),
        "years": (2026,),
        "exchange": "SZSE",
        "as_ofs": (INSTANT,),
    }
    arguments.update(overrides)
    return FeatureMatrixRequest(**arguments)  # type: ignore[arg-type]


def test_a_request_naming_no_year_is_refused() -> None:
    """The factor partitions are keyed by year; a read of none opens no file."""
    with pytest.raises(FeatureSpecError, match="no year"):
        _request(years=())


def test_a_request_naming_no_instant_is_refused() -> None:
    """A matrix over no instant is the empty success this plane exists to make unavailable."""
    with pytest.raises(FeatureSpecError, match="no as_of"):
        _request(as_ofs=())


def test_the_requested_instants_must_be_strictly_increasing() -> None:
    """Both halves of "strictly": a repeat and a reversal, which are different mistakes.

    `V2-P4-013` splits this matrix in time, so the order is part of what a matrix *is* -- unlike
    the column order, which `feature_spec` normalises because it is not.
    """
    later = datetime(2026, 1, 17, 9, 0, tzinfo=UTC)

    with pytest.raises(FeatureSpecError, match="strictly increasing"):
        _request(as_ofs=(INSTANT, INSTANT))
    with pytest.raises(FeatureSpecError, match="strictly increasing"):
        _request(as_ofs=(later, INSTANT))


def test_a_requests_recipe_is_computed_from_its_own_columns() -> None:
    """One request cannot be handed one recipe and a fit another."""
    request = _request(columns=(PROCESSED, RAW))

    assert request.spec == feature_spec(columns=(RAW, PROCESSED))
    assert request.feature_version == request.spec.feature_version


# --- the matrix-level universe version ----------------------------------------------------------


def _section(*, session: date, universe: tuple[str, ...]) -> FeatureMatrixSection:
    instant = datetime(session.year, session.month, session.day, 9, 0, tzinfo=UTC)
    return FeatureMatrixSection(
        as_of=instant,
        session=session,
        universe=universe,
        universe_version=set_digest(universe),
        cross_section=FeatureCrossSection(
            as_of=instant,
            feature_ids=(RAW.feature_id,),
            rows=tuple(FeatureRow(ts_code=ts_code, values=(1.0,)) for ts_code in universe),
        ),
    )


def test_the_matrix_universe_version_pairs_each_market_with_the_session_it_was_read_for() -> None:
    """Neither half alone is the address, and the test shows why each is needed.

    Two matrices whose sections carry the **same markets on different sessions** must differ, or
    the version would say nothing about when; two whose sections carry **different markets on the
    same sessions** must differ, or it would say nothing about who. A `set_digest` over the
    memberships alone fails the first, and one over the sessions alone fails the second.
    """
    spec = feature_spec(columns=(RAW,))
    market = ("000001.SZ", "000002.SZ")
    baseline = FeatureMatrix(
        spec=spec,
        sections=(
            _section(session=date(2026, 1, 15), universe=market),
            _section(session=date(2026, 1, 16), universe=market),
        ),
    )
    moved_sessions = FeatureMatrix(
        spec=spec,
        sections=(
            _section(session=date(2026, 1, 15), universe=market),
            _section(session=date(2026, 1, 19), universe=market),
        ),
    )
    moved_market = FeatureMatrix(
        spec=spec,
        sections=(
            _section(session=date(2026, 1, 15), universe=market),
            _section(session=date(2026, 1, 16), universe=(*market, "600000.SH")),
        ),
    )

    assert baseline.universe_version != moved_sessions.universe_version
    assert baseline.universe_version != moved_market.universe_version
    assert moved_sessions.universe_version != moved_market.universe_version
    assert baseline.feature_version == moved_market.feature_version
