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

from pathlib import Path

import pytest
from panel_fixtures import (
    AS_OF,
    BASE_PERIOD,
    EARLIER_PERIOD,
    INDEX_CODE,
    LATE_ANNOUNCEMENT,
    YEAR,
    GeneratedPanel,
    generate_panel,
    write_generated_panel,
)

from openalpha_cn.domain.financial_statements import (
    AmbiguousReportError,
    build_statement_history,
)
from openalpha_cn.domain.industry_classification import (
    SW2021_TAXONOMY,
    build_security_industry_history,
)
from openalpha_cn.panel.store import PanelStore
from openalpha_cn.panel_doctor import PanelHealthReport, panel_health_report

HEALTHY_SHAPES = (
    "calendar.mid_window_weekday_closure",
    "universe.delisted_security",
    "adjustment.step_down",
    "daily.close_moves_between_sessions",
    "daily.ex_rights_session",
    "daily.bar_without_valuation",
    "suspension.timed_interruption",
    "suspension.resumption",
    "price_limits.limit_free_sentinel",
    "index.published_weights_do_not_sum_to_exactly_one_hundred",
    "financials.second_statement_dataset",
)
"""Every shape that is a form of *sound* data rather than an injected defect.

The three statement-ambiguity shapes are left out on purpose: `ambiguous_filing` is a real
`notice`, so a panel carrying them is still clean but no longer finding-free, and the point of
the healthy case is that a shape-rich panel produces **nothing at all** to report. The two
industry shapes carry no stored dataset here (see `panel_fixtures.STORED_DATASETS`).
"""


def _stored(tmp_path: Path, panel: GeneratedPanel) -> tuple[PanelStore, tuple[str, ...]]:
    store = PanelStore(tmp_path / "panel")
    return store, write_generated_panel(store, panel)


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


def test_a_session_return_is_computed_against_the_previous_session_and_not_against_itself(
    tmp_path: Path,
) -> None:
    """With a close that never moves, `pre_close` equals the bar's own close on every session
    and a check that compared a bar against itself agreed perfectly. Half a yuan a session is
    far more than `pre_close_tolerance` allows, so the substitution now lands outside it."""
    panel = generate_panel(shapes=("daily.close_moves_between_sessions",))

    store, datasets = _stored(tmp_path, panel)
    report = _report(store, panel, datasets)

    closes = {
        (subject, day): close
        for subject, day, close in panel.rows_of("daily", "trade_date", "close")
    }
    first, second = panel.sessions[0], panel.sessions[1]
    code = panel.securities[0]
    assert closes[(code, first.isoformat())] != closes[(code, second.isoformat())]
    assert report.findings_with_code("return_path_disagreement") == ()
    (check,) = [outcome for outcome in report.cross_checks if outcome.name == "return_paths"]
    assert check.ran
