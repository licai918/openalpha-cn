"""The six known fixture-shape gaps, rebuilt from the generator alone (`V2-P1-014`).

Three review passes over `V2-P1-010`..`V2-P1-012` ran fresh mutants and every survivor came
from the same cause: the fixtures did not carry a form the live data has. Each was then closed
by hand in whichever file happened to hold the fixture -- `is_across_a_gap`'s band in
`tests/unit/domain/test_industry_classification.py`, the three-version key in
`tests/unit/domain/test_financial_statements.py`, the second statement dataset and the moving
close in `tests/integration/panel/test_panel_doctor.py`.

**Those hand fixtures are deliberately untouched.** What this file adds is the proof that the
same six mutants die against a panel built *only* out of `PANEL_SHAPES` -- so the coverage is a
property of the generator and survives a later migration off the hand-written builders, and so
a seventh dataset arriving in P2 inherits it instead of rediscovering it.

Since `V2-P2-000` it also carries three things the six mutants do not need and P2 does: the
`PanelShape.provokes` table checked in both directions against a real `panel_health_report`
(including the two shapes that make a generated panel *unhealthy*, one `warning` and one
`blocking`), the round trips that justify the generator hand-writing `namechange`'s and
`index_member_all`'s panel encodings, and `write_generated_panel`'s dataset selection.

The six, each with the mutant it kills:

- `industry.session_adjacent_handover` + `industry.coverage_hole`
  -> `is_not_calendar_adjacent`'s `> 1` widened to `> 400`
- `financials.earlier_period_announced_later`
  -> `latest_filing_on` keyed on the announcement rather than on the period
- `financials.three_versions_of_one_key`
  -> `value_of` comparing `values[1:2]` rather than `values[1:]`
- `financials.second_statement_dataset` + `financials.same_day_duplicate_versions`
  -> `_ambiguity_check` looping over `datasets[:1]`
- `daily.bar_without_valuation`
  -> `close_disagreements`' two arguments swapped
- `daily.close_moves_between_sessions`
  -> the return path taking the bar's own close as its previous close

Everything on disk here is built at run time. `scripts/verify_publication.py` blocks
`.parquet`/`.duckdb` from the tree, and `write_generated_panel` drives the batches through the
real `panel_ingest` writers into a `tmp_path` store, so every write-time guard runs against the
generated shapes rather than around them.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import Final

import pytest
from panel_fixtures import (
    AS_OF,
    BASE_PERIOD,
    EARLIER_PERIOD,
    INDEX_CODE,
    LATE_ANNOUNCEMENT,
    PANEL_SHAPES,
    UNCORROBORATED_RESTATEMENT,
    YEAR,
    GeneratedPanel,
    PanelFixtureError,
    generate_panel,
    write_generated_panel,
)

from openalpha_cn.domain.daily_prices import (
    DAILY_BASIC_DATASET,
    DAILY_DATASET,
    pre_close_tolerance,
)
from openalpha_cn.domain.financial_statements import (
    AmbiguousReportError,
    build_statement_history,
)
from openalpha_cn.domain.industry_classification import (
    SW2021_TAXONOMY,
    build_security_industry_history,
)
from openalpha_cn.domain.price_limits import SUSPENSION_DATASET
from openalpha_cn.panel.store import PanelStore
from openalpha_cn.panel_doctor import PanelHealthReport, panel_health_report
from openalpha_cn.panel_ingest import load_industry_histories, load_name_histories

HEALTHY_SHAPES = tuple(shape_id for shape_id, shape in PANEL_SHAPES.items() if not shape.provokes)
"""Every shape that is a form of *sound* data rather than an injected defect.

Derived from `PanelShape.provokes` rather than listed, because a hand-list is a second place
the same judgement is recorded and the two drift: the previous one carried a reason -- "the two
industry shapes and the two `name_history` shapes carry no stored dataset at all" -- that
stopped being true the moment `namechange` and `index_member_all` gained partitions, and
nothing failed. `EXPECTED_HEALTHY_SHAPES` below pins what the derivation currently yields, so
the membership is still a visible diff; what is gone is the possibility of the two disagreeing.

`calendar.multi_session_recess` is in for the same reason `mid_window_weekday_closure` is: a
closed weekday the calendar agrees is closed is sound data, and the session census that would
otherwise refuse the short partition reads the same calendar.
"""

EXPECTED_HEALTHY_SHAPES = (
    "calendar.mid_window_weekday_closure",
    "calendar.multi_session_recess",
    "universe.delisted_security",
    "universe.termination_on_the_newest_session",
    "suspension.halt_on_the_newest_session",
    "name_history.announcement_on_the_newest_session",
    "name_history.announcement_precedes_effect",
    "name_history.effect_after_every_priced_session",
    "name_history.reform_prefixed_special_treatment",
    "adjustment.step_down",
    "daily.close_moves_between_sessions",
    "daily.ex_rights_session",
    "daily.bar_without_valuation",
    "suspension.timed_interruption",
    "suspension.resumption",
    "price_limits.limit_free_sentinel",
    "price_limits.one_price_limit_up",
    "index.published_weights_do_not_sum_to_exactly_one_hundred",
    "financials.earlier_period_announced_later",
    "financials.second_statement_dataset",
    "financials.statement_dataset_without_a_revision_label",
    "industry.session_adjacent_handover",
    "industry.coverage_hole",
)
"""Twenty-three of the thirty-one, in table order. The eight left out, and why:

- `daily.uncorroborated_factor_step`, `adjustment.factor_series_stops_inside_the_window` and
  `universe.priced_security_absent_from_the_registry` are each a `warning`, and
  `financials.announced_after_the_as_of`, `index.publication_after_the_as_of` and
  `industry.reclassification_after_the_as_of` are each a `blocking` -- the shapes that exist
  precisely so a generated panel can be unhealthy, which is what `V2-P2`'s nine injection issues
  need. The three blocking ones are the same injection aimed at three datasets, which is
  `V2-P2-001`/`003`/`004` respectively. The two `V2-P4-084` added are the two halves of a
  registry and a factor series that do not reach as far as the price panel does, which is what
  `factor_view._PanelInputs.label` asks both of them for.
- `financials.same_day_duplicate_versions` and `financials.three_versions_of_one_key` produce a
  real `ambiguous_filing` **notice**, so a panel carrying them is still clean but no longer
  finding-free -- and the point of the healthy case is that a shape-rich panel produces
  **nothing at all** to report. `financials.earlier_period_announced_later` is *not* one of
  them: an older period re-announced later is ordinary filing behaviour and the doctor has
  nothing to say about it, so it belongs above.
"""


def _stored(
    tmp_path: Path, panel: GeneratedPanel, datasets: Iterable[str] | None = None
) -> tuple[PanelStore, tuple[str, ...]]:
    store = PanelStore(tmp_path / "panel")
    return store, write_generated_panel(store, panel, datasets=datasets)


WRITER_COUPLED_DATASETS: Final[tuple[frozenset[str], ...]] = (
    frozenset({DAILY_DATASET, DAILY_BASIC_DATASET, SUSPENSION_DATASET}),
)
"""Groups `write_generated_panel` refuses to split, so a shape's own `datasets` cannot name one
half and stop there.

`daily` and `daily_basic` are one call to `write_daily_panel`, and that call reads `suspend_d`
back **out of the store** for `_refuse_unexplained_thin_sessions` -- so a selection naming
`daily` alone is refused by name rather than quietly judging every session as having no halts.
Written as a table of groups instead of three `if`s because the closure below is one line over
it, and because a second such group (a fifth statement endpoint, say) is then a row rather than
another branch."""


def _shape_datasets(shape_id: str) -> tuple[str, ...]:
    """What a panel carrying exactly `shape_id` has to store for its own shape to be judged.

    `PanelShape.datasets` already declares which datasets each shape touches, and `V2-P2-000`
    gave `write_generated_panel` the ability to store a subset -- but nothing here used either,
    so every one of the parametrised cases below built and wrote all eleven datasets and ran a
    whole-panel health report over them. The cost was almost entirely independent of which shape
    was being tested, which is the signature of a fixed overhead rather than of work the assertion
    needs.

    Narrowing is sound and not merely faster, because the report is scoped by its `datasets`
    argument on both sides: `panel_health_report` assesses exactly what it is handed, and every
    cross-dataset check is gated on its own inputs being in scope, so a dataset left out can
    only remove findings. It cannot invent one -- which is what would make a `provokes` entry
    an accident of the panel's other ten datasets. The direction that *can* break is the
    opposite one, a declared code that stops arriving because the check producing it no longer
    has its inputs, and `test_a_shape_provokes_exactly_the_health_codes_it_declares` asserts
    equality rather than containment, so it is exactly what fails if `datasets` under-declares.
    """
    asked = set(PANEL_SHAPES[shape_id].datasets)
    for group in WRITER_COUPLED_DATASETS:
        if asked & group:
            asked |= group
    # Sorted rather than the set's own order, so two runs under different hash seeds hand
    # `write_generated_panel` the same argument even though it only reads it as a set.
    return tuple(sorted(asked))


def _report(
    store: PanelStore, panel: GeneratedPanel, datasets: tuple[str, ...]
) -> PanelHealthReport:
    return panel_health_report(
        store,
        as_of=AS_OF,
        datasets=datasets,
        years=(YEAR,),
        calendar=panel.calendar(),
        index_codes=(INDEX_CODE,),
        cross_section_days=(panel.sessions[-1],),
    )


# --- the panel the shapes build is a real, storable, healthy one ----------------------------


def test_a_shape_rich_panel_still_writes_through_every_real_guard_and_reports_nothing(
    tmp_path: Path,
) -> None:
    """The load-bearing case. A generator that can only produce panels the writers refuse, or
    panels the doctor always complains about, would make every test below meaningless: the
    finding they assert on could be an artefact of the fixture rather than of the mutant."""
    panel = generate_panel(shapes=HEALTHY_SHAPES)

    store, datasets = _stored(tmp_path, panel)
    report = _report(store, panel, datasets)

    assert report.findings == ()
    assert report.is_clean
    assert [health.readiness.state for health in report.datasets] == ["ready"] * len(datasets)


def test_the_shapeless_panel_is_storable_too_so_the_comparison_is_like_for_like(
    tmp_path: Path,
) -> None:
    """Otherwise "the shapeless panel does not have this shape" could just mean "the shapeless
    panel is not a panel"."""
    panel = generate_panel()

    store, datasets = _stored(tmp_path, panel)
    report = _report(store, panel, datasets)

    assert report.findings == ()
    assert report.is_clean


def test_the_healthy_set_is_the_shapes_that_claim_nothing_and_is_still_a_visible_list() -> None:
    assert HEALTHY_SHAPES == EXPECTED_HEALTHY_SHAPES
    assert set(HEALTHY_SHAPES) | {
        shape_id for shape_id, shape in PANEL_SHAPES.items() if shape.provokes
    } == set(PANEL_SHAPES)


# --- the provocation table, checked in both directions against a real report -----------------


@pytest.mark.parametrize("shape_id", sorted(PANEL_SHAPES))
def test_a_shape_provokes_exactly_the_health_codes_it_declares(
    shape_id: str, tmp_path: Path
) -> None:
    """The half of `PANEL_SHAPES` a detector cannot see.

    A detector answers "is the form in the artifact"; `provokes` answers "and what does the
    product say about it", which is a different question with a different failure mode. Until
    `V2-P2-000` every shape wrote a panel the doctor was silent about, so the entire
    "must report unhealthy" half of `panel_doctor` and `panel_gate` was reachable only by
    unlinking a Parquet file or asking about a year that was never built -- neither of which is
    a defect of the *data*, which is what the nine injection issues are about.

    Both directions are asserted by the same equality: a shape declaring `()` that starts
    complaining fails here, and so does a shape whose declared code stops arriving. Sorted
    rather than positional because the report's order is not part of anything's contract.

    Only the shape's own datasets are written and reported on -- see `_shape_datasets` for why
    that is sound as well as ~4x cheaper. It also makes the case narrower rather than merely
    faster: a `provokes` entry that arrived from one of the ten datasets the shape does not
    name would now be missing, so the equality is about this shape and not about the panel."""
    panel = generate_panel(shapes=(shape_id,))

    store, datasets = _stored(tmp_path, panel, _shape_datasets(shape_id))
    report = _report(store, panel, datasets)

    assert sorted({finding.code for finding in report.findings}) == sorted(
        PANEL_SHAPES[shape_id].provokes
    )


DEFECT_SHAPES = (
    "adjustment.factor_series_stops_inside_the_window",
    "daily.uncorroborated_factor_step",
    "financials.announced_after_the_as_of",
    "index.publication_after_the_as_of",
    "industry.reclassification_after_the_as_of",
    "universe.priced_security_absent_from_the_registry",
)
"""Every shape that makes a generated panel unhealthy, spelled out rather than derived.

`provokes` alone cannot name this set: `financials.same_day_duplicate_versions` declares two
codes and both are `notice`s, so a panel carrying it is still `is_clean`. Severity is
`panel_doctor`'s to assign, which is why the assertion below reads it off a real report instead
of recomputing it here."""


def test_the_defect_shapes_are_the_ones_that_make_a_generated_panel_unhealthy(
    tmp_path: Path,
) -> None:
    """`is_clean` is "no blocking and no warning", so the notice-producing shapes are not
    enough: a generator whose worst output is a notice cannot exercise a single branch that
    exists to refuse. Both severities are reached, and this pins which shape reaches which.

    The three `blocking` ones are the same injection -- a stored row that had not become
    knowable at the read -- aimed at `income`, `index_weight` and `index_member_all` in turn,
    which is `V2-P2-001`, `003` and `004`. They are listed separately rather than collapsed
    because each partition is judged on its own `max_available_time`, so a regression that lost
    one dataset's coverage census would leave the other two green."""
    severities: dict[str, dict[str, int]] = {}
    for shape_id in DEFECT_SHAPES:
        panel = generate_panel(shapes=(shape_id,))
        store, datasets = _stored(tmp_path / shape_id, panel, _shape_datasets(shape_id))
        report = _report(store, panel, datasets)
        counts: dict[str, int] = {}
        for finding in report.findings:
            counts[finding.severity] = counts.get(finding.severity, 0) + 1
        severities[shape_id] = counts
        assert report.is_clean is False

    assert severities == {
        "adjustment.factor_series_stops_inside_the_window": {"warning": 1},
        "daily.uncorroborated_factor_step": {"warning": 1},
        "financials.announced_after_the_as_of": {"blocking": 1, "warning": 1},
        "index.publication_after_the_as_of": {"blocking": 1},
        "industry.reclassification_after_the_as_of": {"blocking": 1},
        "universe.priced_security_absent_from_the_registry": {"warning": 1},
    }
    assert set(DEFECT_SHAPES) | set(HEALTHY_SHAPES) < set(PANEL_SHAPES)


MINIMUM_RETURN_PATH_GAP = 0.0327
"""3.27 percentage points: `V2-P1-006`'s Critical, `+2.742251%` against `-0.530973%`.

The floor the generated disagreement has to clear, in return space rather than in yuan of
`pre_close`, because "the two paths disagree" and "the two paths disagree by enough to matter"
are the two statements this fixture has to make and only the first is checkable from the shape
table."""


def test_the_unrecorded_factor_step_is_the_v2_p1_006_critical_and_not_a_rounding_gap(
    tmp_path: Path,
) -> None:
    """The finding has to come from a disagreement big enough to move an answer, not from one
    sitting a hair outside `pre_close_tolerance`. `V2-P1-006`'s Critical answered -0.530973%
    against a true +2.742251% -- 3.27 percentage points and the wrong sign -- and a fixture
    whose two paths differed by a tick would produce the same *code* while proving nothing
    about what the code is for. This is `SESSION_STEP`'s lesson applied to the second constant
    whose magnitude the shape table cannot see: `_has_uncorroborated_factor_step` only asks
    whether the gap clears half a published tick, so `UNCORROBORATED_RESTATEMENT` could be
    shrunk to 1.0005 with every shape assertion still green and the finding gone."""
    panel = generate_panel(shapes=("daily.uncorroborated_factor_step",))
    code, session = panel.securities[2], panel.sessions[-1]
    closes = {
        (str(subject), str(day)): float(close)  # type: ignore[arg-type]
        for subject, day, close in panel.rows_of("daily", "trade_date", "close")
    }
    pre_closes = {
        (str(subject), str(day)): float(value)  # type: ignore[arg-type]
        for subject, day, value in panel.rows_of("daily", "trade_date", "pre_close")
    }
    factors = {
        (str(subject), str(day)): float(value)  # type: ignore[arg-type]
        for subject, day, value in panel.rows_of("adj_factor", "factor_date", "adj_factor")
    }
    previous = panel.sessions[-2]

    store, datasets = _stored(tmp_path, panel)
    report = _report(store, panel, datasets)

    published_pre_close = pre_closes[(code, session.isoformat())]
    implied_pre_close = closes[(code, previous.isoformat())]
    close = closes[(code, session.isoformat())]
    published_return = close / published_pre_close - 1.0
    adjusted_return = close / implied_pre_close - 1.0
    assert factors[(code, previous.isoformat())] == factors[(code, session.isoformat())]
    assert abs(published_pre_close - implied_pre_close) > pre_close_tolerance(
        implied_pre_close, factor=1.0, previous_factor=1.0
    )
    assert published_return == pytest.approx(UNCORROBORATED_RESTATEMENT - 1.0, abs=1e-9)
    assert adjusted_return == pytest.approx(0.0, abs=1e-9)
    assert abs(published_return - adjusted_return) >= MINIMUM_RETURN_PATH_GAP
    (finding,) = report.findings_with_code("return_path_disagreement")
    assert finding.severity == "warning"
    assert finding.items == (code,)
    assert finding.dates == (session,)


def test_a_filing_announced_after_the_read_blocks_its_partition_and_nothing_else(
    tmp_path: Path,
) -> None:
    """Roadmap section 11's constraint, met rather than worked around: the refusal has to be
    attributable to the injected row, not to `not_yet_knowable`'s partition granularity. The
    same partition at the same `as_of` is `ready` without the row, so the year is not simply
    too young -- and every other dataset in the same panel stays `ready`, so the block is the
    income partition's and not the read's."""
    injected = generate_panel(shapes=("financials.announced_after_the_as_of",))
    clean = generate_panel()

    dirty_store, datasets = _stored(tmp_path / "injected", injected)
    clean_store, _ = _stored(tmp_path / "clean", clean)
    dirty = _report(dirty_store, injected, datasets)
    healthy = _report(clean_store, clean, datasets)

    assert (
        max(available for available in injected.batch("income").timeline.available_time)
        > injected.as_of
    )
    assert {health.dataset: health.readiness.state for health in healthy.datasets}["income"] == (
        "ready"
    )
    assert {
        health.dataset: health.readiness.state
        for health in dirty.datasets
        if health.readiness.state != "ready"
    } == {"income": "blocked"}
    (blocked,) = dirty.findings_with_code("not_yet_knowable")
    assert blocked.severity == "blocking"
    assert blocked.datasets == ("income",)


# --- the two encodings the generator writes by hand, checked against the real decoders -------


def test_the_generated_rename_rows_read_back_as_the_carriers_they_were_built_from(
    tmp_path: Path,
) -> None:
    """`namechange` was left unstored on the grounds that its panel encoding is produced by the
    provider's row splitting. It is not: `write_name_history`'s own docstring says this dataset
    stays one row per record precisely *because* a rename's effective date is published in its
    own announcement, so there is nothing to split. The encoding is a plain projection, and
    this is the proof that the projection and `name_histories_from_panel_rows` agree."""
    panel = generate_panel(
        shapes=(
            "name_history.announcement_precedes_effect",
            "name_history.reform_prefixed_special_treatment",
        )
    )

    store, _ = _stored(tmp_path, panel)
    histories = load_name_histories(
        store, years=(panel.year,), as_of=panel.as_of, max_staleness=None
    )

    assert set(histories) == set(panel.name_records)
    assert {code: history.records for code, history in histories.items()} == {
        code: tuple(sorted(records, key=lambda record: record.effective_from))
        for code, records in panel.name_records.items()
    }


def test_a_closed_industry_interval_is_stored_as_two_rows_and_folds_back_into_one(
    tmp_path: Path,
) -> None:
    """`index_member_all` is the one encoding here that really does split -- an opening row
    dated at `industry_from` and a closing row dated at `industry_through`, because the two
    ends became knowable at different instants. Writing it out is a second statement of the
    provider's projection only if nothing checks it against the decoder; `_fold_split_rows`
    lives in `domain/`, so this round trip is what makes it one statement.

    The row count is asserted, not just the fold: a generator that emitted one row per
    assignment would still read back correctly here while quietly making a 2026-01-09 hand-over
    visible to a reader standing on 2026-01-02."""
    panel = generate_panel(shapes=("industry.session_adjacent_handover", "industry.coverage_hole"))
    closed = sum(
        1
        for group in panel.industry_assignments.values()
        for assignment in group
        if assignment.effective_through is not None
    )
    total = sum(len(group) for group in panel.industry_assignments.values())

    store, _ = _stored(tmp_path, panel)
    histories = load_industry_histories(
        store, years=(panel.year,), as_of=panel.as_of, max_staleness=None
    )

    assert closed == 2
    assert panel.batch("index_member_all").row_count == total + closed
    assert {code: history.assignments for code, history in histories.items()} == {
        code: tuple(sorted(group, key=lambda item: item.effective_from))
        for code, group in panel.industry_assignments.items()
    }


# --- writing a subset, and refusing an incoherent one ----------------------------------------


def test_a_named_subset_writes_only_those_datasets_in_the_same_order(tmp_path: Path) -> None:
    """A test that reads one dataset paid for eleven before this, and a test asserting about a
    fixed dataset list broke the day the generator learned to carry a twelfth."""
    panel = generate_panel()
    store = PanelStore(tmp_path / "panel")

    stored = write_generated_panel(store, panel, datasets=("trade_cal", "stock_basic"))

    assert stored == ("trade_cal", "stock_basic")
    assert {
        dataset: store.registered_years(dataset) for dataset in ("trade_cal", "stock_basic")
    } == {"trade_cal": (YEAR,), "stock_basic": (YEAR,)}
    assert {dataset: store.registered_years(dataset) for dataset in ("daily", "income")} == {
        "daily": (),
        "income": (),
    }


def test_writing_daily_without_the_halts_it_reads_back_is_refused_rather_than_answered(
    tmp_path: Path,
) -> None:
    """`_refuse_unexplained_thin_sessions` reads `suspend_d` **out of the store**, so a
    selection that omits it does not skip the guard -- it runs it against an empty corpus,
    where every session has zero halts and the one withheld bar becomes unexplained. Refusing
    is the difference between a waiver a caller asked for and one they fell into."""
    panel = generate_panel()
    store = PanelStore(tmp_path / "panel")

    with pytest.raises(PanelFixtureError, match=r"halts=False"):
        write_generated_panel(store, panel, datasets=("trade_cal", "daily", "daily_basic"))


def test_a_dataset_the_panel_does_not_carry_is_refused_by_name(tmp_path: Path) -> None:
    panel = generate_panel()
    store = PanelStore(tmp_path / "panel")

    with pytest.raises(PanelFixtureError, match=r"carries no \['cashflow'\] batch to write"):
        write_generated_panel(store, panel, datasets=("trade_cal", "cashflow"))


# --- gap 1: the band between "adjacent" and "seventeen years" --------------------------------


def test_a_hand_over_across_a_closure_is_not_calendar_adjacent_and_is_still_not_a_gap() -> None:
    """`V2-P1-010`'s review widened `is_not_calendar_adjacent`'s `> 1` to `> 400` and every
    test passed: the fixtures held transitions one day apart and seventeen years apart and
    nothing in between. The measured shape that discriminates them is a hand-over across a
    weekend -- 237 of the corpus's 393 such transitions are exactly three calendar days."""
    panel = generate_panel(shapes=("industry.session_adjacent_handover",))
    history = build_security_industry_history(
        panel.securities[0],
        panel.industry_assignments[panel.securities[0]],
        taxonomy=SW2021_TAXONOMY,
    )

    (blind,) = history.reclassifications()
    (exact,) = history.reclassifications(sessions=panel.sessions)

    assert (exact.current.effective_from - exact.previous.effective_through).days == 3
    assert blind.is_not_calendar_adjacent is True
    assert exact.unclassified_sessions == 0
    assert exact.is_across_a_gap is False
    assert blind.unclassified_sessions is None
    assert blind.is_across_a_gap is True


def test_a_real_hole_is_counted_in_sessions_and_is_a_gap() -> None:
    """The other half of the pair. Without it, "is_across_a_gap is False" above would be
    satisfiable by a rule that answers `False` for everything."""
    panel = generate_panel(shapes=("industry.coverage_hole",))
    code = panel.securities[1]
    history = build_security_industry_history(
        code, panel.industry_assignments[code], taxonomy=SW2021_TAXONOMY
    )

    (change,) = history.reclassifications(sessions=panel.sessions)

    assert (change.current.effective_from - change.previous.effective_through).days == 8
    assert change.unclassified_sessions == 5
    assert change.is_across_a_gap is True


# --- gap 2: the newest announcement can carry the older period -------------------------------


def test_the_latest_filing_is_the_latest_period_and_not_the_latest_announcement() -> None:
    """`920403.BJ` re-announced its 2023 interim six weeks after its 2023 Q3, so ordering by
    announcement hands a reader a half-year report. Over a 76-security probe the two orderings
    disagree on 12 of `income`'s 3,796 answerable days."""
    panel = generate_panel(shapes=("financials.earlier_period_announced_later",))
    code = panel.securities[-1]
    history = build_statement_history(
        security=code,
        dataset="income",
        rows=panel.statement_rows("income")[code],
    )

    latest = history.latest_filing_on(LATE_ANNOUNCEMENT)

    assert [filing.period for filing in history.filings_on(LATE_ANNOUNCEMENT)] == [
        BASE_PERIOD,
        EARLIER_PERIOD,
    ]
    assert latest.period == BASE_PERIOD
    assert latest.announced_on < LATE_ANNOUNCEMENT


# --- gap 3: a key can carry more than two versions --------------------------------------------


def test_a_field_the_first_two_of_three_versions_agree_on_is_still_refused() -> None:
    """`value_of` compares `values[1:]`; narrowed to `values[1:2]` it answers where the
    contract refuses, and no fixture in the repository had a key with three rows until
    `V2-P1-011`'s review added one. The shape is not "three versions" but "three versions whose
    first two agree on the disputed column"."""
    panel = generate_panel(shapes=("financials.three_versions_of_one_key",))
    code = panel.securities[1]
    history = build_statement_history(
        security=code, dataset="income", rows=panel.statement_rows("income")[code]
    )

    (filing,) = history.filings

    assert len(filing.versions) == 3
    assert filing.values_of("total_revenue") == (101.0, 101.0, 777.0)
    with pytest.raises(AmbiguousReportError, match=r"3 versions disagree about 'total_revenue'"):
        filing.value_of("total_revenue")


# --- gap 4: four statement endpoints are four separate reads ---------------------------------


def test_every_stored_statement_dataset_is_checked_and_not_only_the_first(
    tmp_path: Path,
) -> None:
    """With only `income` stored, a build that read the first statement dataset and stopped
    passed every test in the repository."""
    panel = generate_panel(
        shapes=(
            "financials.second_statement_dataset",
            "financials.same_day_duplicate_versions",
        )
    )

    store, datasets = _stored(tmp_path, panel)
    report = _report(store, panel, datasets)

    (check,) = [outcome for outcome in report.cross_checks if outcome.name == "statement_ambiguity"]
    assert check.ran
    assert check.datasets == ("income", "cashflow")
    assert sorted(
        finding.datasets[0] for finding in report.findings_with_code("ambiguous_filing")
    ) == ["cashflow", "income"]


# --- gap 5: the two close indexes are not interchangeable ------------------------------------


def test_a_bar_with_no_valuation_behind_it_is_the_ordinary_direction_and_is_not_reported(
    tmp_path: Path,
) -> None:
    """`close_disagreements(bar_closes, valuation_closes)` iterates the valuations and looks the
    bars up, so the two arguments are not interchangeable -- but a fixture whose two grids match
    exactly cannot tell. `daily_basic` was a subset of `daily` on every session probed and never
    a superset."""
    panel = generate_panel(shapes=("daily.bar_without_valuation",))

    store, datasets = _stored(tmp_path, panel)
    report = _report(store, panel, datasets)

    missing = {
        key
        for key in panel.rows_of("daily", "trade_date")
        if key not in set(panel.rows_of("daily_basic", "trade_date"))
    }
    assert len(missing) == 1
    assert report.findings_with_code("close_disagreement") == ()
    (check,) = [outcome for outcome in report.cross_checks if outcome.name == "close_agreement"]
    assert check.ran


# --- gap 6: a bar's own close is not its previous close --------------------------------------

MEASURED_SESSION_MOVE = 0.06
"""000001.SZ closed 11.30 on 2026-06-11 and 11.24 on 2026-06-12 -- the move
`daily.close_moves_between_sessions`' own `measurement` cites, in yuan."""


def test_a_session_return_is_computed_against_the_previous_session_and_not_against_itself(
    tmp_path: Path,
) -> None:
    """With a close that never moves, `pre_close` equals the bar's own close on every session
    and a check that compared a bar against itself agreed perfectly. Half a yuan a session is
    far more than `pre_close_tolerance` allows, so the substitution now lands outside it.

    **The size of the step is asserted, not asserted-about.** `_has_moving_closes` only asks
    whether the closes differ at all, so the shape table cannot tell 0.5 from 0.005 -- and at
    0.005 this mutant is back inside the tolerance and survives with every shape assertion
    still green. The step is therefore required to clear the tolerance at the panel's widest
    close, and to be at least the 0.06 this shape's own `measurement` records for 000001.SZ's
    11.30 -> 11.24."""
    panel = generate_panel(shapes=("daily.close_moves_between_sessions",))

    store, datasets = _stored(tmp_path, panel)
    report = _report(store, panel, datasets)

    closes = {
        (subject, day): close
        for subject, day, close in panel.rows_of("daily", "trade_date", "close")
    }
    first, second = panel.sessions[0], panel.sessions[1]
    code = panel.securities[0]
    step = float(closes[(code, second.isoformat())]) - float(closes[(code, first.isoformat())])  # type: ignore[arg-type]
    widest = max(float(close) for close in closes.values())  # type: ignore[arg-type]
    assert closes[(code, first.isoformat())] != closes[(code, second.isoformat())]
    assert step > pre_close_tolerance(widest, factor=1.0, previous_factor=1.0)
    assert step >= MEASURED_SESSION_MOVE
    assert report.findings_with_code("return_path_disagreement") == ()
    (check,) = [outcome for outcome in report.cross_checks if outcome.name == "return_paths"]
    assert check.ran
