"""The fixture generator's own guards (`V2-P1-014`).

`tests/panel_fixtures.py` exists because a fixture that quietly lacks a form the live data has
lets a wrong implementation pass. A *generator* that quietly lacks it is the same defect with a
wider blast radius, so the contract is asserted here as four whole-table statements rather
than as one test per shape:

1. **Asking for a shape produces the shape.** Every id in `PANEL_SHAPES`, generated on its own,
   is confirmed present by its own detector.
2. **The shapeless panel has none of them.** A detector that answers `True` on
   `generate_panel()` is measuring nothing, and would make (1) vacuous.
3. **No detector reads the request.** Each detector is re-run against the shapeless artifact
   relabelled as if it carried the shape. This is the one failure mode that would make the
   whole table a tautology, and it is the only way to rule it out from the outside.
4. **No detector answers for somebody else's shape.** The full 28x28 matrix, with the seven
   containments that really do hold declared in `CROSS_TRIGGERS`. (1)-(3) are all satisfied by
   a detector that is about something far wider than the shape it is filed under -- and the
   three look-ahead shapes stay three separate injections only because each reads its own
   dataset, which was true and untested until this statement.

Each is a dict compared against a dict literal, for `panel_gate.GATE_CODE_BLOCKS`' reason: a
shape that stops working shows up as a diff on its own line, and a shape added to the table
without a working detector fails here rather than being waved through.

A fourth, un-numbered guard sits at the bottom of the file:
`test_a_detector_follows_the_stored_column_and_not_the_generators_intent` hand-edits one column
of the shapeless panel *through* `ColumnarPanelBatch` and requires the answer to flip. It is
the only one that proves a detector reads bytes rather than any generator-side state.

Since `V2-P2-000` the table also carries `PanelShape.provokes` -- the health codes a shape
makes a real report emit. This file checks the claims are spellable; whether they are *true*
needs a store and lives in `tests/integration/panel/test_panel_shape_coverage.py`.
"""

from __future__ import annotations

import re
from dataclasses import replace
from pathlib import Path

import pytest
from panel_fixtures import (
    AS_OF,
    EVERY_SHAPE,
    EXCHANGE,
    PANEL_SHAPES,
    STATEMENT_DATASETS,
    STORED_DATASETS,
    YEAR,
    GeneratedPanel,
    PanelFixtureError,
    generate_panel,
)

from openalpha_cn.domain.financial_statements import (
    DATASETS_WITH_REVISION_LABEL,
    FINANCIAL_INDICATOR_DATASET,
    FINANCIAL_STATEMENT_DATASETS,
    INCOME_DATASET,
)
from openalpha_cn.domain.panel_batch import PanelColumn
from openalpha_cn.panel_doctor import PANEL_HEALTH_CODES

EXPECTED_SHAPE_IDS = (
    "adjustment.step_down",
    "calendar.mid_window_weekday_closure",
    "calendar.multi_session_recess",
    "daily.bar_without_valuation",
    "daily.close_moves_between_sessions",
    "daily.ex_rights_session",
    "daily.uncorroborated_factor_step",
    "financials.announced_after_the_as_of",
    "financials.earlier_period_announced_later",
    "financials.same_day_duplicate_versions",
    "financials.second_statement_dataset",
    "financials.statement_dataset_without_a_revision_label",
    "financials.three_versions_of_one_key",
    "index.publication_after_the_as_of",
    "index.published_weights_do_not_sum_to_exactly_one_hundred",
    "industry.coverage_hole",
    "industry.reclassification_after_the_as_of",
    "industry.session_adjacent_handover",
    "name_history.announcement_on_the_newest_session",
    "name_history.announcement_precedes_effect",
    "name_history.effect_after_every_priced_session",
    "name_history.reform_prefixed_special_treatment",
    "price_limits.limit_free_sentinel",
    "price_limits.one_price_limit_up",
    "suspension.halt_on_the_newest_session",
    "suspension.resumption",
    "suspension.timed_interruption",
    "universe.delisted_security",
    "universe.termination_on_the_newest_session",
)
"""The closed set, spelled out.

Written as a literal rather than derived from `PANEL_SHAPES` so that adding a shape is a
visible diff on this list too -- the same reason `tests/unit/test_panel_gate_rules.py` spells
`GATE_CODE_BLOCKS` out instead of asserting a property of it.
"""

EXPECTED_PROVOCATIONS = {
    "adjustment.step_down": (),
    "calendar.mid_window_weekday_closure": (),
    "calendar.multi_session_recess": (),
    "daily.bar_without_valuation": (),
    "daily.close_moves_between_sessions": (),
    "daily.ex_rights_session": (),
    "daily.uncorroborated_factor_step": ("return_path_disagreement",),
    "financials.announced_after_the_as_of": ("not_yet_knowable", "check_unavailable"),
    "financials.earlier_period_announced_later": (),
    "financials.same_day_duplicate_versions": ("ambiguous_filing", "duplicate_versions"),
    "financials.second_statement_dataset": (),
    "financials.statement_dataset_without_a_revision_label": (),
    "financials.three_versions_of_one_key": ("ambiguous_filing", "duplicate_versions"),
    "index.publication_after_the_as_of": ("not_yet_knowable",),
    "index.published_weights_do_not_sum_to_exactly_one_hundred": (),
    "industry.coverage_hole": (),
    "industry.reclassification_after_the_as_of": ("not_yet_knowable",),
    "industry.session_adjacent_handover": (),
    "name_history.announcement_on_the_newest_session": (),
    "name_history.announcement_precedes_effect": (),
    "name_history.effect_after_every_priced_session": (),
    "name_history.reform_prefixed_special_treatment": (),
    "price_limits.limit_free_sentinel": (),
    "price_limits.one_price_limit_up": (),
    "suspension.halt_on_the_newest_session": (),
    "suspension.resumption": (),
    "suspension.timed_interruption": (),
    "universe.delisted_security": (),
    "universe.termination_on_the_newest_session": (),
}
"""What each shape claims a `panel_health_report` will say about it, spelled out here too.

This file can only check the claim is *well formed* -- that every code named is one
`panel_doctor` declares. Whether the shape really provokes it, and whether a shape claiming
nothing really provokes nothing, needs a store and a report, and lives in
`tests/integration/panel/test_panel_shape_coverage.py`. Both halves are required: without the
second the table is a wish, and without the first a typo'd code would fail as "the report did
not emit `retrun_path_disagreement`" rather than as "there is no such code".
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


SOURCE_ROOT = Path(__file__).resolve().parents[2] / "src" / "openalpha_cn"
"""`src/openalpha_cn/`, reached from this file rather than from the working directory."""

_CITED_MODULE = re.compile(r"\b(?:domain|panel|panel_ingest|providers)/[a-z_]+\.py\b")


def _cited_modules(measurement: str) -> tuple[str, ...]:
    """Every `subpackage/module.py` the sentence points a reader at, in order, deduplicated."""
    return tuple(dict.fromkeys(_CITED_MODULE.findall(measurement)))


def test_every_measurement_cites_a_module_that_is_really_there() -> None:
    """The test above accepts any sentence containing the substring `domain/`, which a citation
    of a module that does not exist satisfies exactly as well as a real one -- and that is not
    hypothetical: `industry.coverage_hole` shipped citing a number
    (`002674.SZ`'s 45-session hole) that lives in `artifacts/`'s ledger and in
    `tests/unit/domain/test_industry_classification.py`, not in the module named beside it.
    Resolving the path is the cheap half of the check that would have caught it.

    What this still does **not** verify is that the *number* is in the file it names; that
    remains a reviewer's job, and the citation format exists to make it a short one."""
    assert {
        shape_id: (
            bool(_cited_modules(shape.measurement)),
            tuple(
                cited
                for cited in _cited_modules(shape.measurement)
                if not (SOURCE_ROOT / cited).is_file()
            ),
        )
        for shape_id, shape in PANEL_SHAPES.items()
    } == {shape_id: (True, ()) for shape_id in EXPECTED_SHAPE_IDS}


def test_every_declared_provocation_is_a_code_the_doctor_can_actually_emit() -> None:
    """A shape may claim a finding, and the claim has to be spellable.

    `PANEL_HEALTH_CODES` is the closed union of `evaluate_readiness`' codes and the doctor's
    own, so a `provokes` entry outside it can never be emitted by anything and would make the
    integration assertion fail for the wrong reason."""
    assert {
        shape_id: tuple(shape.provokes) for shape_id, shape in PANEL_SHAPES.items()
    } == EXPECTED_PROVOCATIONS
    assert {
        code for shape in PANEL_SHAPES.values() for code in shape.provokes
    } <= PANEL_HEALTH_CODES


def test_the_four_statement_endpoints_are_the_domains_own_four() -> None:
    """`financials.statement_dataset_without_a_revision_label` claims all four endpoints are
    stored and exactly one of them carries no `update_flag`. Both halves are counts, so a fifth
    endpoint arriving in `domain/financial_statements.py` has to fail here rather than leave
    the shape quietly covering four fifths of its own claim."""
    assert STATEMENT_DATASETS == FINANCIAL_STATEMENT_DATASETS
    assert set(DATASETS_WITH_REVISION_LABEL) == set(STATEMENT_DATASETS) - {
        FINANCIAL_INDICATOR_DATASET
    }
    assert set(STORED_DATASETS) & set(STATEMENT_DATASETS) == {INCOME_DATASET}


def test_every_shape_that_claims_a_finding_names_a_dataset_the_finding_is_about() -> None:
    """A provoking shape whose `datasets` did not overlap the report's request would produce
    its finding only by accident of what else the panel holds."""
    assert {
        shape_id: bool(set(shape.datasets) & set(STORED_DATASETS))
        for shape_id, shape in PANEL_SHAPES.items()
        if shape.provokes
    } == {shape_id: True for shape_id, codes in EXPECTED_PROVOCATIONS.items() if codes}


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


CROSS_TRIGGERS = {
    "calendar.mid_window_weekday_closure": ("calendar.multi_session_recess",),
    "daily.ex_rights_session": ("adjustment.step_down",),
    "daily.uncorroborated_factor_step": ("daily.close_moves_between_sessions",),
    "financials.same_day_duplicate_versions": ("financials.three_versions_of_one_key",),
    "financials.second_statement_dataset": (
        "financials.statement_dataset_without_a_revision_label",
    ),
    "name_history.announcement_precedes_effect": (
        "name_history.effect_after_every_priced_session",
    ),
    "suspension.timed_interruption": ("suspension.halt_on_the_newest_session",),
    "universe.delisted_security": ("universe.termination_on_the_newest_session",),
}
"""Detector -> the other shapes' panels it also answers `True` on. Eight, each declared.

Statement (4) below is "a detector answers `False` on somebody else's shape", and these are the
pairs where it does not. None is a detector reaching outside what it names; each is one shape's
panel really containing another shape's form, which is a fact about the generator and has to be
written down rather than allowed by a loose assertion. In order:

- `calendar.multi_session_recess` closes 2026-01-08 **and** 2026-01-09 to open the five-day gap
  it is named for. Both are weekdays inside the session window, which is
  `mid_window_weekday_closure` exactly. Set inclusion, and
  `test_asking_for_every_shape_at_once_still_produces_every_one_of_them`'s docstring already
  says so in prose; this is the same sentence as an assertion.
- `adjustment.step_down` moves `securities[1]`'s factor at `sessions[6]`, and `_pre_close_of`
  restates every published `pre_close` by whatever the factors did overnight. A corroborated
  restatement **is** an ex-rights session -- that is `_has_ex_rights_session`'s own definition,
  both halves of it -- so a factor step that the generator stays self-consistent about cannot
  fail to be one. The two shapes differ in which direction the factor moves, not in kind: the
  step-down entry's own measurement is 28 of 5,351 securities whose factors fell.
- `daily.close_moves_between_sessions` is the one pair that is not an implication of meaning,
  and it comes from the shapeless panel's own halt. `_missing_bars` withholds
  `securities[-1]`'s bar on `sessions[4]`, so that name's next stored bar is `sessions[5]`,
  whose `pre_close` is `sessions[4]`'s close -- a close no row carries. With a flat series the
  two are the same number and nothing shows; once the closes move they differ by one
  `SESSION_STEP`, with a flat `adj_factor` beside them, which is `_has_uncorroborated_factor_step`
  read literally. The detector is not wrong about the bytes: a reader holding this panel cannot
  tell a restatement from an unpublished session, and a detector that could would be reading
  the generator's intent rather than the artifact, which is the one thing
  `PanelShape.detect`'s docstring forbids. `panel_doctor` does not report it because the
  return-path check is session-scoped to the requested day (see `UNCORROBORATED_SECURITY_INDEX`),
  which is why the two shapes' `provokes` stay `()` and `("return_path_disagreement",)`.
- `financials.three_versions_of_one_key` puts three rows under one `(period, announcement)`
  key. Any two of them are two rows under one key that disagree, so the narrower shape is
  contained in the wider one by construction -- and `EXPECTED_PROVOCATIONS` records the
  consequence, both declaring the same two codes.
- `financials.statement_dataset_without_a_revision_label` stores all four statement endpoints,
  of which `second_statement_dataset` asks for two. Again containment, and the reverse
  direction is `False`: two datasets are not four.
- `name_history.effect_after_every_priced_session` is a rename announced 2026-01-14 and
  effective 2026-01-20, so its two clocks separate -- which is `announcement_precedes_effect`
  read literally, and the corpus fact that shape is filed under. What the newer shape adds is
  *where the effect lands*: `RENAME_EFFECTIVE_FROM` is 2026-08-02, seven months out but with
  `LISTED_ON`'s baseline record still under it, so `record_on` answers every session of the
  window; the newer shape drops that baseline, so the security's earliest stored record is the
  one past `WINDOW_LAST` and no session has a name at all. The reverse direction is `False`,
  and that asymmetry is the pair's whole content.
- `suspension.halt_on_the_newest_session` writes a **timed** halt, so it is a timed
  interruption as well as a newest-session one. The timing is forced rather than chosen: the
  price grid carries a bar for every name on every session except `_missing_bars`' one cell,
  and a whole-day halt sitting beside a stored bar is a contradiction rather than a shape. The
  reverse direction is `False`, and that is the pair's whole content -- `_timed_key` is
  `sessions[2]`, which is what makes "on the newest session" the thing the second shape adds.
- `universe.termination_on_the_newest_session` writes a delisting row, and a delisting row is
  what `universe.delisted_security` is. The two differ in the one thing this pair is filed
  under: the older shape's termination is dated `2026-01-05` so that the terminated name is in
  no session's cross section, and the newer one's is dated on the last session so that the
  *partition's newest availability instant* moves onto it. Again the reverse is `False`.

The table is a dict literal for `EXPECTED_PROVOCATIONS`' reason. A ninth pair appearing is a
diff on this list and a decision somebody makes, rather than a silently widened detector.
"""


def test_no_detector_answers_true_on_a_shape_that_is_not_its_own() -> None:
    """Statement (4): the scope guard, and the third way this table could be a tautology.

    (1) says a detector fires on its own shape and (2) says it is silent on the shapeless
    panel, and a detector can satisfy both while being about something much wider than the
    shape it is filed under. `_has_disclosure_after_the_as_of` is the live example: it reads
    `income`'s rows and nothing else, and that narrowness is the reason the three look-ahead
    shapes -- `income`, `index_weight`, `index_member_all` -- cannot answer for one another.
    Widening it to sweep every batch keeps (1), (2) and (3) green and turns three independent
    injections into one, which is precisely the property `V2-P2-001`/`003`/`004` are three
    issues for.

    So the whole matrix is pinned, not just its diagonal: 28 panels, each detector run against
    all of them. The declared cross-triggers are named in `CROSS_TRIGGERS` above with the
    reason each one is a containment rather than a leak.
    """
    panels = {shape_id: generate_panel(shapes=(shape_id,)) for shape_id in EVERY_SHAPE}

    detected = {
        shape_id: tuple(
            other
            for other in EXPECTED_SHAPE_IDS
            if other != shape_id and shape.detect(panels[other])
        )
        for shape_id, shape in PANEL_SHAPES.items()
    }

    assert detected == {
        shape_id: CROSS_TRIGGERS.get(shape_id, ()) for shape_id in EXPECTED_SHAPE_IDS
    }
    assert set(CROSS_TRIGGERS) <= set(EXPECTED_SHAPE_IDS)


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


def test_the_panel_carries_its_own_frame_and_not_only_its_rows() -> None:
    """`YEAR`/`AS_OF`/`EXCHANGE` were importable beside the generator and absent from what it
    returned, so every caller had to know which module a panel's partition year came from."""
    panel = generate_panel()

    assert (panel.year, panel.as_of, panel.exchange) == (YEAR, AS_OF, EXCHANGE)
    assert panel.calendar().exchange == panel.exchange
    assert {day.year for day in panel.sessions} == {panel.year}
    assert (
        max(
            available
            for batch in panel.batches.values()
            for available in batch.timeline.available_time
        )
        <= panel.as_of
    )


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


WIDEST_MEASURED_REAL_BAND: float = 1.4409
"""`300830.SZ` on its 2020-05-06 listing day: the widest `up_limit / pre_close` that is a real
band, over the 1,918,266-row scan in `domain/price_limits.py`."""

NARROWEST_MEASURED_SENTINEL: float = 115.61
"""`688808.SH` on 2026-04-29: 99999.999 against an 864.99 close, the narrowest ratio in the
same scan that is a sentinel. Nothing at all lands between the two."""


def test_the_generated_limit_free_band_is_one_the_exchange_actually_publishes() -> None:
    """`is_bounded` is a ratio test against `LIMIT_FREE_RATIO`'s 2.0, so the detector alone is
    satisfied by any wide-enough band -- 200.0 against a ten-yuan close is 20x and passes it
    while sitting squarely in the empty region between 1.4409x and 115.61x, a value the feed
    has never served. "A band the domain rule classifies as limit-free" and "the shape the data
    has" are therefore not the same statement, and this pins the second one: the generated pair
    is `(99999.999, 0.01)`, what SSE has published since 2023-06-21."""
    panel = generate_panel(shapes=("price_limits.limit_free_sentinel",))
    bands = {
        (str(subject), str(day)): (float(up), float(down))  # type: ignore[arg-type]
        for subject, day, up, down in panel.rows_of(
            "stk_limit", "trade_date", "up_limit", "down_limit"
        )
    }
    closes = {
        (str(subject), str(day)): float(close)  # type: ignore[arg-type]
        for subject, day, close in panel.rows_of("daily", "trade_date", "close")
    }
    key = (panel.securities[0], panel.sessions[0].isoformat())

    assert bands[key] == (99999.999, 0.01)
    assert bands[key][0] / closes[key] > NARROWEST_MEASURED_SENTINEL
    assert [
        band
        for other, band in bands.items()
        if other in closes and band[0] / closes[other] > WIDEST_MEASURED_REAL_BAND
    ] == [bands[key]]


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
