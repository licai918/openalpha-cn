"""The fixture generator's own guards (`V2-P1-014`).

`tests/panel_fixtures.py` exists because a fixture that quietly lacks a form the live data has
lets a wrong implementation pass. A *generator* that quietly lacks it is the same defect with a
wider blast radius, so the contract is asserted here as three whole-table statements rather
than as one test per shape:

1. **Asking for a shape produces the shape.** Every id in `PANEL_SHAPES`, generated on its own,
   is confirmed present by its own detector.
2. **The shapeless panel has none of them.** A detector that answers `True` on
   `generate_panel()` is measuring nothing, and would make (1) vacuous.
3. **No detector reads the request.** Each detector is re-run against the shapeless artifact
   relabelled as if it carried the shape. This is the one failure mode that would make the
   whole table a tautology, and it is the only way to rule it out from the outside.

Each is a dict compared against a dict literal, for `panel_gate.GATE_CODE_BLOCKS`' reason: a
shape that stops working shows up as a diff on its own line, and a shape added to the table
without a working detector fails here rather than being waved through.
"""

from __future__ import annotations

from dataclasses import replace

import pytest
from panel_fixtures import (
    EVERY_SHAPE,
    PANEL_SHAPES,
    STORED_DATASETS,
    GeneratedPanel,
    PanelFixtureError,
    generate_panel,
)

from openalpha_cn.domain.panel_batch import PanelColumn

EXPECTED_SHAPE_IDS = (
    "adjustment.step_down",
    "calendar.mid_window_weekday_closure",
    "calendar.multi_session_recess",
    "daily.bar_without_valuation",
    "daily.close_moves_between_sessions",
    "daily.ex_rights_session",
    "financials.earlier_period_announced_later",
    "financials.same_day_duplicate_versions",
    "financials.second_statement_dataset",
    "financials.three_versions_of_one_key",
    "index.published_weights_do_not_sum_to_exactly_one_hundred",
    "industry.coverage_hole",
    "industry.session_adjacent_handover",
    "name_history.announcement_precedes_effect",
    "name_history.reform_prefixed_special_treatment",
    "price_limits.limit_free_sentinel",
    "suspension.resumption",
    "suspension.timed_interruption",
    "universe.delisted_security",
)
"""The closed set, spelled out.

Written as a literal rather than derived from `PANEL_SHAPES` so that adding a shape is a
visible diff on this list too -- the same reason `tests/unit/test_panel_gate_rules.py` spells
`GATE_CODE_BLOCKS` out instead of asserting a property of it.
"""


def test_the_shape_table_is_exactly_the_declared_closed_set() -> None:
    assert tuple(sorted(PANEL_SHAPES)) == EXPECTED_SHAPE_IDS
    assert tuple(sorted(EVERY_SHAPE)) == EXPECTED_SHAPE_IDS
    assert all(shape_id == shape.shape_id for shape_id, shape in PANEL_SHAPES.items())


def test_every_shape_names_at_least_one_dataset_and_cites_where_it_was_measured() -> None:
    """A shape with no provenance is a guess, which is the failure this table exists to stop.

    The citation is required to name the module the number came from, so a reviewer can go and
    re-read the measurement rather than take the sentence on trust."""
    assert {
        shape_id: (
            bool(shape.datasets),
            bool(shape.summary),
            "domain/" in shape.measurement,
            any(character.isdigit() for character in shape.measurement),
        )
        for shape_id, shape in PANEL_SHAPES.items()
    } == {shape_id: (True, True, True, True) for shape_id in EXPECTED_SHAPE_IDS}


def test_asking_for_a_shape_produces_a_panel_that_actually_carries_it() -> None:
    """Statement (1): the table, one shape at a time, detected on the artifact."""
    assert {
        shape_id: shape.detect(generate_panel(shapes=(shape_id,)))
        for shape_id, shape in PANEL_SHAPES.items()
    } == {shape_id: True for shape_id in EXPECTED_SHAPE_IDS}


def test_the_shapeless_panel_carries_none_of_the_declared_shapes() -> None:
    """Statement (2): without this, every detector could be `lambda _: True`."""
    shapeless = generate_panel()

    assert {shape_id: shape.detect(shapeless) for shape_id, shape in PANEL_SHAPES.items()} == {
        shape_id: False for shape_id in EXPECTED_SHAPE_IDS
    }


def test_no_detector_answers_from_the_request_instead_of_from_the_artifact() -> None:
    """Statement (3): the anti-tautology guard.

    The shapeless panel is relabelled to claim every shape and nothing else about it changes.
    A detector that consulted `panel.shapes` -- the one line that would make this whole module
    a self-fulfilling prophecy -- answers `True` here and is caught."""
    mislabelled = replace(generate_panel(), shapes=frozenset(EVERY_SHAPE))

    assert {shape_id: shape.detect(mislabelled) for shape_id, shape in PANEL_SHAPES.items()} == {
        shape_id: False for shape_id in EXPECTED_SHAPE_IDS
    }


def test_asking_for_every_shape_at_once_still_produces_every_one_of_them() -> None:
    """Shapes may imply one another; none may cancel another.

    `daily.ex_rights_session` moves a factor and `adjustment.step_down` moves a different one,
    `calendar.multi_session_recess` closes the day `calendar.mid_window_weekday_closure` closes
    -- the table's contract is that the union is still the union."""
    every = generate_panel(shapes=EVERY_SHAPE)

    assert {shape_id: shape.detect(every) for shape_id, shape in PANEL_SHAPES.items()} == {
        shape_id: True for shape_id in EXPECTED_SHAPE_IDS
    }


def test_an_unknown_shape_is_refused_by_name_rather_than_silently_ignored() -> None:
    """A typo in a shape request is a test that asserts against the shapeless panel."""
    with pytest.raises(PanelFixtureError, match=r"unknown panel shape\(s\) \['daily.ex_rights'\]"):
        generate_panel(shapes=("daily.ex_rights",))


def test_a_dataset_a_shape_did_not_add_is_refused_by_name_rather_than_raising_a_key_error() -> None:
    with pytest.raises(PanelFixtureError, match=r"carries no 'cashflow' batch"):
        generate_panel().batch("cashflow")


def test_a_column_the_batch_does_not_have_is_refused_with_the_ones_it_does() -> None:
    with pytest.raises(PanelFixtureError, match=r"'daily' has no column 'vwap'"):
        generate_panel().column("daily", "vwap")


def test_the_shapeless_panel_still_carries_every_dataset_the_writers_need() -> None:
    """`suspend_d` included: it needs one row for the partition to exist, and the shapeless
    panel's row is the untimed halt named in `panel_fixtures`' docstring."""
    shapeless = generate_panel()

    assert tuple(sorted(shapeless.batches)) == tuple(sorted(STORED_DATASETS))
    assert shapeless.batch("suspend_d").row_count == 1


def test_the_generator_reports_the_sessions_it_actually_left_open() -> None:
    """`sessions` is what every price grid and every gap measurement is built from, so a
    closure that did not reach it would silently make two shapes vacuous."""
    shapeless = generate_panel()
    closed = generate_panel(shapes=("calendar.multi_session_recess",))

    assert len(shapeless.sessions) == 10
    assert len(closed.sessions) == 8
    assert set(shapeless.sessions) - set(closed.sessions) == {
        day for day in shapeless.sessions if day.isoformat() in {"2026-01-08", "2026-01-09"}
    }


def _shapeless_with_column(dataset: str, column: PanelColumn) -> GeneratedPanel:
    """The shapeless panel with one column swapped, batch rebuilt through the real contract."""
    panel = generate_panel()
    batch = panel.batch(dataset)
    rebuilt = replace(
        batch,
        columns=tuple(
            column if existing.name == column.name else existing for existing in batch.columns
        ),
    )
    return replace(panel, batches={**panel.batches, dataset: rebuilt})


def test_a_detector_follows_the_stored_column_and_not_the_generators_intent() -> None:
    """Proof that `detect` is reading bytes: hand-edit the artifact and the answer moves.

    The shapeless panel's `daily` closes are a per-name constant. Replacing that one column
    with a moving series -- through `ColumnarPanelBatch` itself, so the row count, the clocks
    and the digest all still have to line up -- makes
    `daily.close_moves_between_sessions` answer `True` with no shape requested at all."""
    panel = generate_panel()
    original = panel.column("daily", "close")
    moving = PanelColumn(
        "close",
        "float",
        tuple(float(value) + index for index, value in enumerate(original)),  # type: ignore[arg-type]
    )

    edited = _shapeless_with_column("daily", moving)

    assert PANEL_SHAPES["daily.close_moves_between_sessions"].detect(panel) is False
    assert PANEL_SHAPES["daily.close_moves_between_sessions"].detect(edited) is True
