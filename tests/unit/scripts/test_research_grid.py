"""`scripts/research/grid.py`: the research grid runner and its ledger (`V2-P6-008`).

The research protocol (section 2 of `docs/superpowers/plans/2026-09-26-selection-ready.md`) puts
three numbers in code rather than in a person's hands: the family size a stage's p-values are
controlled against (every ledger row of the stage, failures included), the non-overlapping
sample the sign-flip tests run on (one session in every `horizon`), and the dependence assumption
the false-discovery control is asked for (`arbitrary`, Benjamini-Yekutieli). Each has a test here
that goes red when the number is computed any other way.
"""

from __future__ import annotations

import _thread
import importlib
import json
import math
import multiprocessing
import statistics
import sys
import threading
from collections.abc import Mapping
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from time import monotonic
from types import ModuleType
from typing import Any, Final

import pytest
from panel_fixtures import EXCHANGE, GeneratedPanel
from research_repo import commit_file, git, head
from strategy_fixtures import READ_AT, REVERSAL, write_strategy_corpus

from openalpha_cn.backtest.multiple_testing import (
    HypothesisTest,
    MultipleTestingRequest,
    control_false_discovery_rate,
)
from openalpha_cn.backtest.outcome_statistics import sign_flip_test
from openalpha_cn.backtest.strategy_backtest import StrategyBacktestError, UnknowableCrossing
from openalpha_cn.sdk import OpenAlphaSDK
from openalpha_cn.strategy_view import StrategyRequestError

ROOT: Final[Path] = Path(__file__).resolve().parents[3]
RESEARCH: Final[Path] = ROOT / "scripts" / "research"


def _research_module(name: str) -> ModuleType:
    """Import one of `scripts/research/`'s modules the way running it as a script would.

    `registry.py` imports `grid` as a sibling, which is what `python scripts/research/registry.py`
    resolves through `sys.path[0]`; the directory goes on the path once so both names resolve to
    one module object here too.
    """
    if str(RESEARCH) not in sys.path:
        sys.path.insert(0, str(RESEARCH))
    return importlib.import_module(name)


grid = _research_module("grid")
registry = _research_module("registry")

AT: Final[datetime] = datetime(2026, 9, 26, 12, 0, tzinfo=UTC)
WINDOW: Final[dict[str, object]] = {"start": date(2015, 1, 5), "end": date(2015, 6, 30)}
"""A measured window inside the discovery segment, for runner tests that are not about windows."""
NO_LABEL = grid.strategy_label_sessions


def _rows(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


# --- the family --------------------------------------------------------------------------------


def test_family_size_counts_every_row_of_the_stage_including_failures(tmp_path: Path) -> None:
    ledger = tmp_path / "ledger.jsonl"
    for i in range(7):
        grid.append_ledger(ledger, "discovery", {"i": i}, {"p_ic": 0.5})
    grid.append_ledger(ledger, "discovery", {"i": 7}, {"error": "refused"})
    assert grid.stage_family(ledger, "discovery") == 8


def test_family_size_is_per_stage_and_zero_for_a_stage_never_run(tmp_path: Path) -> None:
    ledger = tmp_path / "ledger.jsonl"
    grid.append_ledger(ledger, "discovery", {"i": 0}, {"p_excess": 0.5})
    grid.append_ledger(ledger, "composition", {"i": 0}, {"p_excess": 0.5})
    grid.append_ledger(ledger, "composition", {"i": 1}, {"error": "refused"})

    assert grid.stage_family(ledger, "discovery") == 1
    assert grid.stage_family(ledger, "composition") == 2
    assert grid.stage_family(ledger, "validation") == 0
    assert grid.stage_family(tmp_path / "absent.jsonl", "discovery") == 0


def test_a_configuration_is_one_row_of_its_stage_and_a_second_is_refused(tmp_path: Path) -> None:
    """A re-run of a tried configuration is not a second hypothesis, and not a way to replace the
    first answer either: the row that is there is the one the family counts."""
    ledger = tmp_path / "ledger.jsonl"
    grid.append_ledger(ledger, "discovery", {"a": 1, "b": "x"}, {"p_excess": 0.5})

    with pytest.raises(grid.ResearchLedgerError, match="already"):
        grid.append_ledger(ledger, "discovery", {"b": "x", "a": 1}, {"p_excess": 0.01})
    grid.append_ledger(ledger, "validation", {"a": 1, "b": "x"}, {"p_excess": 0.2})

    assert [row["result"]["p_excess"] for row in _rows(ledger)] == [0.5, 0.2]


def test_a_ledger_row_carries_its_stage_configuration_identity_and_time(tmp_path: Path) -> None:
    ledger = tmp_path / "ledger.jsonl"
    config = {**WINDOW, "holding_count": 50, "weight": Decimal("0.5")}

    grid.append_ledger(ledger, "discovery", config, {"p_excess": 0.25}, recorded_at=AT)

    (row,) = _rows(ledger)
    assert row["schema"] == grid.LEDGER_SCHEMA
    assert row["stage"] == "discovery"
    assert row["config"] == {
        "end": "2015-06-30",
        "holding_count": 50,
        "start": "2015-01-05",
        "weight": "0.5",
    }
    assert row["config_id"] == grid.config_id(config)
    assert len(row["config_id"]) == 64
    assert row["result"] == {"p_excess": 0.25}
    assert row["recorded_at"] == "2026-09-26T12:00:00+00:00"


def test_append_ledger_refuses_a_row_whose_declared_window_leaves_its_stage(
    tmp_path: Path,
) -> None:
    """A hand-written row is a hypothesis of its stage's family like any measured one, so a
    validation row over 2024 -- the holdout's years -- is refused, not counted."""
    ledger = tmp_path / "ledger.jsonl"
    in_2024 = {"start": date(2024, 1, 2), "end": date(2025, 6, 30), "holding_count": 50}
    inside = {"start": date(2022, 1, 4), "end": date(2023, 12, 29), "holding_count": 50}

    with pytest.raises(grid.StageWindowError, match="2025-06-30"):
        grid.append_ledger(ledger, "validation", in_2024, {"p_excess": 0.001})
    with pytest.raises(grid.ResearchLedgerError, match="start"):
        grid.append_ledger(ledger, "validation", {"start": date(2022, 1, 4)}, {"p_excess": 0.1})
    grid.append_ledger(ledger, "validation", inside, {"p_excess": 0.2})

    assert grid.stage_family(ledger, "validation") == 1
    assert grid.fdr_table(ledger, "validation", 0.10).family_size == 1


def test_a_torn_ledger_line_is_refused_rather_than_skipped(tmp_path: Path) -> None:
    """A row that silently disappeared from a read would shrink the family it belongs to."""
    ledger = tmp_path / "ledger.jsonl"
    grid.append_ledger(ledger, "discovery", {"i": 0}, {"p_excess": 0.5})
    with ledger.open("a", encoding="utf-8") as handle:
        handle.write('{"schema": "openalpha-research-ledger/v1", "stage": "disc')

    with pytest.raises(grid.ResearchLedgerError, match="line 2"):
        grid.stage_family(ledger, "discovery")


def test_a_non_finite_result_is_refused(tmp_path: Path) -> None:
    with pytest.raises(grid.ResearchLedgerError):
        grid.append_ledger(tmp_path / "l.jsonl", "discovery", {"i": 0}, {"p_excess": float("nan")})


# --- the non-overlapping sample ------------------------------------------------------------------


def test_non_overlapping_takes_every_horizon_th_session() -> None:
    days = tuple(date(2026, 1, 1) + timedelta(days=i) for i in range(10))
    assert grid.non_overlapping(days, 3) == (days[0], days[3], days[6], days[9])


def test_non_overlapping_at_one_session_is_every_session_and_refuses_a_bad_input() -> None:
    days = tuple(date(2026, 1, 1) + timedelta(days=i) for i in range(4))
    assert grid.non_overlapping(days, 1) == days
    assert grid.non_overlapping((), 5) == ()
    with pytest.raises(ValueError, match="horizon"):
        grid.non_overlapping(days, 0)
    with pytest.raises(ValueError, match="ascending"):
        grid.non_overlapping((days[1], days[0]), 1)
    with pytest.raises(ValueError, match="ascending"):
        grid.non_overlapping((days[0], days[0]), 1)


def test_embargo_drops_the_sessions_whose_label_would_cross_the_segment_end() -> None:
    """An as-of's `horizon`-session label ends `horizon` sessions later; inside a segment of `n`
    sessions only the first `n - horizon` of them have a label that ends inside it."""
    days = tuple(date(2026, 1, 1) + timedelta(days=i) for i in range(10))
    assert grid.embargo(days, 3) == days[:7]
    assert grid.embargo(days, 10) == ()
    with pytest.raises(ValueError, match="horizon"):
        grid.embargo(days, 0)


def test_the_sign_flip_over_a_dated_series_reads_only_the_sampled_sessions() -> None:
    days = tuple(date(2026, 1, 1) + timedelta(days=i) for i in range(7))
    values = {day: (1.0 if index % 2 == 0 else -100.0) for index, day in enumerate(days)}
    del values[days[4]]  # an unmeasured as-of is absent, not zero

    answer = grid.non_overlapping_sign_flip(values, days, 2, bootstrap_samples=10, random_seed=1)

    # sampled: days[0], days[2], days[4] (unmeasured), days[6]
    assert answer["sampled_sessions"] == 4
    assert answer["measured"] == 3
    expected = sign_flip_test((1.0, 1.0, 1.0), bootstrap_samples=10, random_seed=1)
    assert answer["p_value"] == expected.p_value


# --- the grid ----------------------------------------------------------------------------------


def test_expand_grid_is_the_cartesian_product_in_one_order_whatever_the_key_order() -> None:
    one = grid.expand_grid({"b": (1, 2), "a": ("x", "y")})
    two = grid.expand_grid({"a": ["x", "y"], "b": [1, 2]})

    assert (
        one
        == two
        == (
            {"a": "x", "b": 1},
            {"a": "x", "b": 2},
            {"a": "y", "b": 1},
            {"a": "y", "b": 2},
        )
    )


def test_expand_grid_refuses_a_dimension_with_no_value() -> None:
    """A dimension with no value turns the whole product into nothing, and a stage of zero rows
    reads as a stage that found nothing rather than one that tried nothing."""
    with pytest.raises(ValueError, match="holding_count"):
        grid.expand_grid({"holding_count": (), "combine": ("zscore_sum",)})


def test_expand_grid_refuses_a_bare_string_dimension() -> None:
    """`{"combine": "zscore_sum"}` is a sequence of ten characters to `itertools.product`."""
    with pytest.raises(ValueError, match="combine"):
        grid.expand_grid({"combine": "zscore_sum"})


# --- the false-discovery table -------------------------------------------------------------------


def test_fdr_uses_arbitrary_dependence(tmp_path: Path) -> None:
    """`(0.0625, 0.625)` at rate 0.75 over a family of two: independence rejects both and arbitrary
    dependence (Benjamini-Yekutieli, penalty H_2 = 1.5) rejects one -- `multiple_testing`'s own
    example of the field changing the arithmetic."""
    ledger = tmp_path / "ledger.jsonl"
    grid.append_ledger(ledger, "discovery", {"i": 0}, {"p_excess": 0.0625})
    grid.append_ledger(ledger, "discovery", {"i": 1}, {"p_excess": 0.625})

    report = grid.fdr_table(ledger, "discovery", 0.75)

    assert report.dependence == "arbitrary"
    assert report.dependence_penalty == 1.5
    assert report.discoveries == 1
    assert report.family_size == 2


def test_the_fdr_family_is_every_row_and_a_failed_row_is_withheld(tmp_path: Path) -> None:
    ledger = tmp_path / "ledger.jsonl"
    for index, p_value in enumerate((0.001, 0.02, 0.3)):
        grid.append_ledger(ledger, "discovery", {"i": index}, {"p_excess": p_value})
    grid.append_ledger(ledger, "discovery", {"i": 3}, {"error": "refused"})
    grid.append_ledger(ledger, "composition", {"i": 0}, {"p_excess": 0.0001})

    report = grid.fdr_table(ledger, "discovery", 0.10)

    assert report.family_size == 4
    assert report.reported_hypotheses == 3
    assert report.withheld_hypotheses == 1
    rows = [row for row in _rows(ledger) if row["stage"] == "discovery"]
    expected = control_false_discovery_rate(
        MultipleTestingRequest(
            tests=tuple(
                HypothesisTest(
                    hypothesis_id=row["config_id"],
                    p_value=row["result"]["p_excess"],
                    test=grid.hypothesis_test_name("p_excess"),
                )
                for row in rows
                if "p_excess" in row["result"]
            ),
            family_size=4,
            false_discovery_rate=0.10,
            dependence="arbitrary",
        )
    )
    assert report == expected


def test_the_fdr_table_reads_the_p_value_it_is_named(tmp_path: Path) -> None:
    ledger = tmp_path / "ledger.jsonl"
    grid.append_ledger(ledger, "discovery", {"i": 0}, {"p_excess": 0.9, "p_ic": 0.001})
    grid.append_ledger(ledger, "discovery", {"i": 1}, {"p_excess": 0.8, "p_ic": 0.002})

    assert grid.fdr_table(ledger, "discovery", 0.10).discoveries == 0
    assert grid.fdr_table(ledger, "discovery", 0.10, p_value_key="p_ic").discoveries == 2


def test_the_fdr_table_refuses_a_stage_that_has_no_p_value_to_control(tmp_path: Path) -> None:
    ledger = tmp_path / "ledger.jsonl"
    grid.append_ledger(ledger, "discovery", {"i": 0}, {"error": "refused"})

    with pytest.raises(grid.ResearchLedgerError, match="1 row"):
        grid.fdr_table(ledger, "discovery", 0.10)
    with pytest.raises(grid.ResearchLedgerError, match="no row"):
        grid.fdr_table(ledger, "composition", 0.10)


# --- the runner --------------------------------------------------------------------------------


def test_the_runner_records_a_refused_configuration_as_a_row_of_the_family(tmp_path: Path) -> None:
    ledger = tmp_path / "ledger.jsonl"
    configs = tuple({**WINDOW, **c} for c in grid.expand_grid({"holding_count": (30, 50, 100)}))

    def measure(config: Mapping[str, object]) -> Mapping[str, object]:
        if config["holding_count"] == 50:
            raise StrategyRequestError("no cross section on the signal day")
        return {"p_excess": 0.5}

    run = grid.run_grid(
        ledger, "discovery", configs, measure, label_sessions=NO_LABEL, clock=lambda: AT
    )

    assert run.ran == 3
    assert run.skipped == 0
    assert grid.stage_family(ledger, "discovery") == 3
    refused = [row["result"] for row in _rows(ledger) if "error" in row["result"]]
    assert refused == [
        {"error": "StrategyRequestError: no cross section on the signal day"},
    ]


def test_the_runner_resumes_by_skipping_what_the_ledger_already_holds(tmp_path: Path) -> None:
    ledger = tmp_path / "ledger.jsonl"
    configs = tuple({**WINDOW, **c} for c in grid.expand_grid({"holding_count": (30, 50)}))
    calls: list[Mapping[str, object]] = []

    def measure(config: Mapping[str, object]) -> Mapping[str, object]:
        calls.append(config)
        return {"p_excess": 0.5}

    grid.run_grid(
        ledger, "discovery", configs[:1], measure, label_sessions=NO_LABEL, clock=lambda: AT
    )
    run = grid.run_grid(
        ledger, "discovery", configs, measure, label_sessions=NO_LABEL, clock=lambda: AT
    )

    assert (run.ran, run.skipped) == (1, 1)
    assert calls == [configs[0], configs[1]]
    assert grid.stage_family(ledger, "discovery") == 2


def test_an_error_the_runner_does_not_recognise_stops_the_run_and_writes_nothing(
    tmp_path: Path,
) -> None:
    ledger = tmp_path / "ledger.jsonl"

    def measure(config: Mapping[str, object]) -> Mapping[str, object]:
        raise KeyError("a bug, not a refusal")

    with pytest.raises(KeyError):
        grid.run_grid(
            ledger,
            "discovery",
            ({"i": 0, **WINDOW},),
            measure,
            label_sessions=NO_LABEL,
            clock=lambda: AT,
        )
    assert grid.stage_family(ledger, "discovery") == 0


def test_the_runner_will_not_run_the_holdout(tmp_path: Path) -> None:
    """The holdout runs once, through `registry.run_holdout`, behind the registration guard."""
    with pytest.raises(grid.ResearchLedgerError, match="registry"):
        grid.run_grid(
            tmp_path / "l.jsonl",
            grid.HOLDOUT_STAGE,
            ({"i": 0, "start": date(2024, 1, 2), "end": date(2024, 6, 28)},),
            lambda c: {},
            label_sessions=NO_LABEL,
            clock=lambda: AT,
        )


def test_append_ledger_will_not_write_the_holdout_stage(tmp_path: Path) -> None:
    """Only `registry.run_holdout` writes the holdout stage, through `_append_holdout`, which
    carries the registration it ran under; a public write would be a holdout row with no guard."""
    ledger = tmp_path / "ledger.jsonl"
    with pytest.raises(grid.ResearchLedgerError, match="run_holdout"):
        grid.append_ledger(ledger, grid.HOLDOUT_STAGE, {"i": 0}, {"p_excess": 0.5})
    assert grid.stage_family(ledger, grid.HOLDOUT_STAGE) == 0
    assert not ledger.exists()


def test_the_holdout_path_takes_one_claim_then_one_measurement_of_the_claimed_configuration(
    tmp_path: Path,
) -> None:
    ledger = tmp_path / "ledger.jsonl"
    binding = {"registration_sha256": "0" * 64, "registration_commit": "a" * 40}

    with pytest.raises(grid.ResearchLedgerError, match="claim"):
        grid._append_holdout(ledger, "measurement", {"i": 0}, binding, recorded_at=AT)
    grid._append_holdout(ledger, "holdout_claim", {"i": 0}, binding, recorded_at=AT)
    with pytest.raises(grid.ResearchLedgerError, match="once"):
        grid._append_holdout(ledger, "holdout_claim", {"i": 1}, binding, recorded_at=AT)
    with pytest.raises(grid.ResearchLedgerError, match="claim"):
        grid._append_holdout(ledger, "measurement", {"i": 1}, binding, recorded_at=AT)
    with pytest.raises(grid.ResearchLedgerError, match="registration"):
        grid._append_holdout(ledger, "measurement", {"i": 0}, {"p_excess": 0.5}, recorded_at=AT)
    grid._append_holdout(ledger, "measurement", {"i": 0}, {**binding, "p": 1.0}, recorded_at=AT)
    with pytest.raises(grid.ResearchLedgerError, match="once"):
        grid._append_holdout(ledger, "measurement", {"i": 0}, binding, recorded_at=AT)

    assert [row["kind"] for row in _rows(ledger)] == ["holdout_claim", "measurement"]
    assert grid.stage_family(ledger, grid.HOLDOUT_STAGE) == 1


# --- the stage windows -------------------------------------------------------------------------


def _weekdays(first: date, last: date) -> tuple[date, ...]:
    days = (first + timedelta(days=offset) for offset in range((last - first).days + 1))
    return tuple(day for day in days if day.weekday() < 5)


CALENDAR: Final[tuple[date, ...]] = _weekdays(date(2021, 11, 1), date(2024, 3, 29))


def _run_one(ledger: Path, stage: str, config: Mapping[str, object], **kwargs: Any) -> list[Any]:
    measured: list[Mapping[str, object]] = []

    def measure(value: Mapping[str, object]) -> Mapping[str, object]:
        measured.append(value)
        return {"p_excess": 0.5}

    grid.run_grid(ledger, stage, (config,), measure, clock=lambda: AT, **kwargs)
    return measured


def test_the_protocol_segments_are_code() -> None:
    assert grid.STAGES == ("discovery", "composition", "validation", "holdout")
    windows = grid.PROTOCOL_STAGE_WINDOWS
    assert windows["discovery"] == grid.StageWindow(date(2015, 1, 5), date(2021, 12, 31))
    assert windows["composition"] == windows["discovery"]
    assert windows["validation"] == grid.StageWindow(date(2022, 1, 4), date(2023, 12, 29))
    assert windows["holdout"] == grid.StageWindow(date(2024, 1, 2), None)


def test_a_configuration_reaching_into_the_holdout_is_refused_before_it_is_measured(
    tmp_path: Path,
) -> None:
    ledger = tmp_path / "ledger.jsonl"
    config = {"start": date(2023, 6, 1), "end": date(2024, 1, 5), "holding_count": 50}

    measured = _run_one(ledger, "validation", config, label_sessions=NO_LABEL)

    assert measured == []
    (row,) = _rows(ledger)
    assert row["result"]["error"].startswith("StageWindowError: ")
    assert "2024-01-05" in row["result"]["error"]
    assert grid.stage_family(ledger, "validation") == 1


def test_a_label_that_ends_past_the_stage_is_refused_and_one_inside_it_is_measured(
    tmp_path: Path,
) -> None:
    """Five sessions after Friday 24 December 2021 is Friday 31 December, inside discovery; five
    after Monday 27 December is Monday 3 January 2022, which is validation's."""
    ledger = tmp_path / "ledger.jsonl"
    inside = {"start": date(2021, 6, 1), "end": date(2021, 12, 24)}
    across = {"start": date(2021, 6, 1), "end": date(2021, 12, 27)}
    five = {"label_sessions": lambda config: 5, "sessions": CALENDAR}

    assert _run_one(ledger, "discovery", inside, **five) == [inside]
    assert _run_one(ledger, "discovery", across, **five) == []
    assert "2022-01-03" in _rows(ledger)[-1]["result"]["error"]


def test_a_configuration_starting_before_its_stage_is_refused(tmp_path: Path) -> None:
    ledger = tmp_path / "ledger.jsonl"
    config = {"start": date(2014, 12, 31), "end": date(2015, 6, 30)}

    assert _run_one(ledger, "discovery", config, label_sessions=NO_LABEL) == []
    assert _rows(ledger)[0]["result"]["error"].startswith("StageWindowError: ")


def test_there_is_no_forward_stage_in_the_research_tooling(tmp_path: Path) -> None:
    """The forward period is evaluated only through predictions registered before their outcomes
    (V2-P6-011's daily command, V2-P6-012's forward report), never through this grid."""
    ledger = tmp_path / "ledger.jsonl"
    covering_2024 = {"start": date(2024, 1, 2), "end": date(2025, 6, 30)}

    assert "forward" not in grid.STAGES
    with pytest.raises(grid.ResearchLedgerError, match="stage"):
        _run_one(ledger, "forward", covering_2024, label_sessions=NO_LABEL)
    with pytest.raises(grid.ResearchLedgerError, match="stage"):
        grid.append_ledger(ledger, "forward", covering_2024, {"p_excess": 0.5})
    with pytest.raises(TypeError):
        _run_one(
            ledger,
            "discovery",
            covering_2024,
            label_sessions=NO_LABEL,
            forward_after=date(2023, 12, 31),
        )
    assert not hasattr(registry, "run_forward")
    assert not hasattr(registry, "forward_after")
    assert not ledger.exists()


@pytest.mark.parametrize("stage", ["discovery", "composition", "validation"])
def test_no_stage_but_the_holdout_measures_anything_from_2024_on(
    tmp_path: Path, stage: str
) -> None:
    """The holdout window is open-ended, so every date from 2024-01-02 on -- 2026 included --
    belongs to it and is measured only by `registry.run_holdout`."""
    ledger = tmp_path / "ledger.jsonl"
    window = grid.PROTOCOL_STAGE_WINDOWS[stage]
    reaching_2026 = {"start": window.first, "end": date(2026, 8, 28)}
    inside_2026 = {"start": date(2026, 1, 5), "end": date(2026, 8, 28)}

    assert _run_one(ledger, stage, reaching_2026, label_sessions=NO_LABEL) == []
    assert _run_one(ledger, stage, inside_2026, label_sessions=NO_LABEL) == []
    errors = [row["result"]["error"] for row in _rows(ledger)]
    assert all(error.startswith("StageWindowError: ") for error in errors)
    assert len(errors) == 2


def test_a_ledger_of_the_first_row_version_is_refused_by_name(tmp_path: Path) -> None:
    """v1 rows had no `kind`; there is no v1 research ledger to migrate, so it is re-run."""
    ledger = tmp_path / "ledger.jsonl"
    v1 = {
        "schema": "openalpha-research-ledger/v1",
        "stage": "discovery",
        "config_id": "0" * 64,
        "config": {},
        "result": {},
        "recorded_at": "2026-09-26T12:00:00+00:00",
    }
    ledger.write_text(json.dumps(v1) + "\n", encoding="utf-8")

    assert grid.LEDGER_SCHEMA == "openalpha-research-ledger/v2"
    with pytest.raises(grid.ResearchLedgerError, match=r"line 1.*ledger/v1.*re-run"):
        grid.stage_family(ledger, "discovery")


@pytest.mark.parametrize(
    "config",
    [
        {"end": date(2015, 6, 30)},
        {"start": date(2015, 1, 5)},
        {"start": "2015-01-05", "end": date(2015, 6, 30)},
        {"start": datetime(2015, 1, 5, tzinfo=UTC), "end": date(2015, 6, 30)},
        {"start": date(2015, 6, 30), "end": date(2015, 1, 5)},
    ],
)
def test_a_configuration_must_name_its_window_as_two_ordered_dates(
    tmp_path: Path, config: dict[str, object]
) -> None:
    """The window is read from `start` and `end` and nowhere else -- an `as_of` clock in 2026 is
    not a measured window -- so a configuration without them cannot be placed in a stage."""
    ledger = tmp_path / "ledger.jsonl"
    with pytest.raises(grid.ResearchLedgerError, match="start"):
        _run_one(ledger, "discovery", config, label_sessions=NO_LABEL)
    assert not ledger.exists()


def test_an_as_of_clock_in_the_holdout_years_is_not_a_measured_window(tmp_path: Path) -> None:
    ledger = tmp_path / "ledger.jsonl"
    config = {**WINDOW, "as_of": datetime(2026, 8, 29, 4, 0, tzinfo=UTC)}

    assert _run_one(ledger, "discovery", config, label_sessions=NO_LABEL) == [config]


def test_a_calendar_that_does_not_reach_the_label_end_is_the_callers_error(tmp_path: Path) -> None:
    ledger = tmp_path / "ledger.jsonl"
    config = {"start": date(2021, 6, 1), "end": date(2024, 3, 27)}
    with pytest.raises(grid.ResearchLedgerError, match="calendar"):
        _run_one(ledger, "discovery", config, label_sessions=lambda c: 5, sessions=CALENDAR)
    assert not ledger.exists()


def test_a_stage_outside_the_protocol_vocabulary_is_refused(tmp_path: Path) -> None:
    ledger = tmp_path / "ledger.jsonl"
    with pytest.raises(grid.ResearchLedgerError, match="stage"):
        grid.append_ledger(ledger, "smoke", {"i": 0}, {"p_excess": 0.5})
    with pytest.raises(grid.ResearchLedgerError, match="stage"):
        _run_one(ledger, "discovery2", WINDOW, label_sessions=NO_LABEL)


# --- the strategy measurement, through the SDK ---------------------------------------------------


@pytest.fixture(scope="module")
def runtime(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, GeneratedPanel]:
    root = tmp_path_factory.mktemp("research-grid")
    return root, write_strategy_corpus(root)


def _base_config(panel: GeneratedPanel) -> dict[str, object]:
    return {
        "components": ((REVERSAL.qualified_key, "raw", Decimal("1")),),
        "combine": "zscore_sum",
        "start": panel.sessions[1],
        "end": panel.sessions[-1],
        "as_of": READ_AT,
        "exchange": EXCHANGE,
        "buffer_rank": None,
        "max_industry_weight": None,
    }


def test_the_holdout_drives_the_sdk_and_ledgers_net_excess_and_its_sign_flip(
    runtime: tuple[Path, GeneratedPanel], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The generated panel's sessions are in January 2026, inside the holdout window, so the one
    stage that may measure them is the holdout: registered, committed, then run once."""
    root, panel = runtime
    sdk = OpenAlphaSDK(runtime_dir=root)
    configs = ({**_base_config(panel), "holding_count": 2, "rebalance_every_sessions": 3},)
    measure = grid.strategy_measure(sdk.run_strategy_backtest, excess_benchmark="000905.SH")
    ledger = tmp_path / "ledger.jsonl"
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q", "--template=")
    (repo / "README.md").write_text("research\n", encoding="utf-8")
    commit_file(repo, repo / "README.md", "initial", at=datetime(2026, 1, 3, 0, 0, tzinfo=UTC))
    registration = repo / "registration.json"
    registry.register(
        configs[0], {}, registration, code_commit=head(repo), settings=measure.settings
    )
    commit_file(repo, registration, "register", at=datetime(2026, 1, 4, 0, 0, tzinfo=UTC))
    research = repo / "scripts" / "research"
    monkeypatch.setattr(
        registry, "_imported_package", lambda: repo / "src" / "openalpha_cn" / "__init__.py"
    )
    monkeypatch.setattr(
        registry, "_imported_scripts", lambda: (research / "grid.py", research / "registry.py")
    )

    registry.run_holdout(registration, ledger, repo, configs[0], measure, clock=lambda: AT)

    rows = [row for row in _rows(ledger) if row["kind"] == "measurement"]
    for config, row in zip(configs, rows, strict=True):
        backtest = sdk.run_strategy_backtest(**config)
        excess = tuple(
            period.net_return - period.benchmark_returns["000905.SH"] for period in backtest.periods
        )
        sessions = [period.sessions for period in backtest.periods]
        complete = [count == 3 for count in sessions]
        assert complete == [True, True, False]  # nine sessions, a signal every three
        result = row["result"]
        assert result["excess_benchmark"] == "000905.SH"
        assert result["net_excess"] == [str(value) for value in excess]
        assert result["period_sessions"] == sessions
        assert result["period_complete"] == complete
        assert result["period_count"] == 3
        assert result["excluded_incomplete_periods"] == 1
        assert result["unknowable_crossings"] == []
        assert result["unknowable_valuation_difference"] == "0.00"
        crossed = backtest.model_copy(
            update={
                "unknowable_crossings": (
                    UnknowableCrossing(
                        subject="600733.SH",
                        day=backtest.periods[0].end,
                        period_start=backtest.periods[0].start,
                        held_value=Decimal("1000.00"),
                        valuation_difference=Decimal("-120.50"),
                        share_of_book=Decimal("-0.0006025000"),
                    ),
                )
            }
        )
        assert grid.strategy_result(crossed, excess_benchmark="000905.SH")[
            "unknowable_crossings"
        ] == [f"600733.SH@{backtest.periods[0].end.isoformat()}"]
        assert (
            grid.strategy_result(crossed, excess_benchmark="000905.SH")[
                "unknowable_valuation_difference"
            ]
            == "-120.50"
        )
        values = [float(value) for value, full in zip(excess, complete, strict=True) if full]
        expected = sign_flip_test(
            tuple(values),
            bootstrap_samples=grid.PROTOCOL_BOOTSTRAP_SAMPLES,
            random_seed=grid.PROTOCOL_RANDOM_SEED,
        )
        assert result["p_excess"] == expected.p_value
        assert result["p_excess_exact"] is expected.exact
        mean = statistics.fmean(values)
        assert result["p_excess_one_sided"] == grid.one_sided_p_value(expected.p_value, mean)
        per_year = grid.SESSIONS_PER_YEAR / 3
        assert result["information_ratio"] == pytest.approx(
            mean / statistics.stdev(values) * math.sqrt(per_year), rel=1e-12
        )
        assert result["annualized_mean_net_excess"] == pytest.approx(mean * per_year, rel=1e-12)
        full = [p for p, whole in zip(backtest.periods, complete, strict=True) if whole]
        assert result["max_relative_drawdown"] == grid.max_relative_drawdown(
            [p.net_return for p in full], [p.benchmark_returns["000905.SH"] for p in full]
        )
        assert result["max_relative_drawdown"] == grid.result_max_relative_drawdown(result)
        assert result[
            "compounded_annual_relative_return"
        ] == grid.result_compounded_annual_relative_return(result)
    assert grid.stage_family(ledger, grid.HOLDOUT_STAGE) == 1


def test_a_backtest_with_no_complete_period_is_refused() -> None:
    """A window shorter than one rebalance interval has nothing the tests may read."""

    class Period:
        sessions = 2
        start = date(2026, 1, 5)
        benchmark_returns: Mapping[str, object] = {"000905.SH": 0}

    class Spec:
        rebalance_every_sessions = 3

    class Short:
        periods = (Period(),)
        spec = Spec()

    with pytest.raises(StrategyBacktestError, match="complete"):
        grid.strategy_result(Short(), excess_benchmark="000905.SH")


def test_the_maximum_relative_drawdown_on_a_hand_computed_series() -> None:
    """Section 7 of the protocol: relative level = prod(1 + net) / prod(1 + benchmark), and the
    maximum relative drawdown is its largest fall from the highest level before it, the start (1)
    included.

    net (0.10, -0.05, 0.02) against benchmark (0, 0.05, 0): levels 1.1, 1.045 / 1.05 and
    1.0659 / 1.05. The worst fall is the second, 1 - (1.045 / 1.05) / 1.1 = 1 - 0.95 / 1.05 = 2/21.
    """
    net = [Decimal("0.10"), Decimal("-0.05"), Decimal("0.02")]
    benchmark = [Decimal("0"), Decimal("0.05"), Decimal("0")]

    assert grid.max_relative_drawdown(net, benchmark) == pytest.approx(2 / 21, rel=1e-12)
    assert grid.max_relative_drawdown([Decimal("-0.1")], [Decimal("0")]) == pytest.approx(0.1)
    assert grid.max_relative_drawdown([Decimal("0.1"), Decimal("0.1")], [Decimal("0")] * 2) == 0.0
    assert grid.max_relative_drawdown([], []) == 0.0


def test_the_ledgered_drawdown_reads_only_complete_periods() -> None:
    """The trailing period a window ends inside is not a like-for-like period; a loss in it would
    move the drawdown to 1 - 1.0659 / 1.155."""
    result = {
        "net_return": ["0.10", "-0.05", "0.02", "0"],
        "benchmark_return": ["0", "0.05", "0", "0.10"],
        "period_complete": [True, True, True, False],
    }
    assert grid.result_max_relative_drawdown(result) == pytest.approx(2 / 21, rel=1e-12)


def test_the_compounded_annual_relative_return_on_a_hand_computed_series() -> None:
    """Section 7's criterion 1: (prod(1 + net) / prod(1 + benchmark)) ** (244 / sessions) - 1.

    net (0.10, -0.05) against benchmark (0, 0.05) over two 20-session periods: the relative
    level is 1.1 x 0.95 / 1.05 = 1.045 / 1.05, compounded over 244 / 40 = 6.1 periods a year."""
    value = grid.compounded_annual_relative_return(
        [Decimal("0.10"), Decimal("-0.05")], [Decimal("0"), Decimal("0.05")], [20, 20]
    )
    assert value == pytest.approx((1.045 / 1.05) ** 6.1 - 1, rel=1e-12)


def test_the_ledgered_compounded_return_reads_only_complete_periods() -> None:
    result = {
        "net_return": ["0.10", "-0.05", "-0.5"],
        "benchmark_return": ["0", "0.05", "0"],
        "period_sessions": [20, 20, 3],
        "period_complete": [True, True, False],
    }
    assert grid.result_compounded_annual_relative_return(result) == pytest.approx(
        (1.045 / 1.05) ** 6.1 - 1, rel=1e-12
    )


class _Period:
    def __init__(self, day: int, net: str, primary: str, reported: str | None) -> None:
        self.start = date(2026, 1, day)
        self.end = date(2026, 1, day + 1)
        self.sessions = 20
        self.net_return = Decimal(net)
        self.benchmark_returns = {"equal_weight_all_a": Decimal(primary)}
        if reported is not None:
            self.benchmark_returns["000905.SH"] = Decimal(reported)
        self.turnover = Decimal("0.5")


class _Spec:
    rebalance_every_sessions = 20


class _Book:
    def __init__(self, periods: list[_Period]) -> None:
        self.periods = tuple(periods)
        self.spec = _Spec()
        self.unknowable_crossings = ()


def test_the_reported_benchmark_is_ledgered_beside_the_primary_and_decides_nothing() -> None:
    """Section 3: 000905.SH is reported in parallel. Net (0.02, 0.01, 0.04) against it
    (0.01, -0.01, 0.00) is an excess of (0.01, 0.02, 0.04); every primary key is unchanged."""
    book = _Book(
        [
            _Period(5, "0.02", "0.00", "0.01"),
            _Period(6, "0.01", "0.00", "-0.01"),
            _Period(7, "0.04", "0.00", "0.00"),
        ]
    )

    result = grid.strategy_result(book, excess_benchmark="equal_weight_all_a", bootstrap_samples=10)

    excess = [0.01, 0.02, 0.04]
    per_year = 244 / 20
    assert result["reported_benchmark"] == "000905.SH"
    assert result["reported_benchmark_return"] == ["0.01", "-0.01", "0.00"]
    assert result["reported_net_excess"] == ["0.01", "0.02", "0.04"]
    assert result["reported_mean_net_excess"] == pytest.approx(statistics.fmean(excess))
    assert result["reported_information_ratio"] == pytest.approx(
        statistics.fmean(excess) / statistics.stdev(excess) * math.sqrt(per_year)
    )
    level = (1.02 * 1.01 * 1.04) / (1.01 * 0.99 * 1.00)
    assert result["reported_compounded_annual_relative_return"] == pytest.approx(
        level ** (244 / 60) - 1
    )
    primary = grid.strategy_result(
        _Book([_Period(5 + i, n, "0.00", None) for i, n in enumerate(("0.02", "0.01", "0.04"))]),
        excess_benchmark="equal_weight_all_a",
        bootstrap_samples=10,
    )
    assert "reported_benchmark" not in primary  # a book without it still measures
    assert {k: v for k, v in result.items() if not k.startswith("reported_")} == primary


def test_a_refused_row_carries_the_extra_keys_the_runner_is_handed(tmp_path: Path) -> None:
    ledger = tmp_path / "ledger.jsonl"
    outside = {"start": date(2023, 6, 1), "end": date(2024, 1, 5)}

    def refuse(config: Mapping[str, object]) -> Mapping[str, object]:
        raise StrategyRequestError("no cross section")

    for config, measure in ((outside, lambda c: {"p_excess": 0.5}), ({**WINDOW}, refuse)):
        stage = "validation" if config is outside else "discovery"
        grid.run_grid(
            ledger,
            stage,
            (config,),
            measure,
            label_sessions=NO_LABEL,
            clock=lambda: AT,
            result_extra={"code_commit": "a" * 40},
        )

    assert [row["result"]["code_commit"] for row in _rows(ledger)] == ["a" * 40] * 2
    assert _rows(ledger)[0]["result"]["error"].startswith("StageWindowError: ")
    assert _rows(ledger)[1]["result"]["error"].startswith("StrategyRequestError: ")


def test_the_one_sided_p_value_halves_the_two_sided_one_in_the_observed_direction() -> None:
    assert grid.one_sided_p_value(0.08, 0.001) == 0.04
    assert grid.one_sided_p_value(0.08, -0.001) == 0.96
    assert grid.one_sided_p_value(1.0, 0.0) == 0.5


def test_a_score_source_the_runner_does_not_know_is_passed_through_as_data() -> None:
    """The runner never reads a configuration: a score-source kind added after it was written is a
    keyword the SDK receives unchanged."""
    received: list[dict[str, object]] = []

    def backtest(**kwargs: object) -> object:
        received.append(kwargs)
        raise StrategyRequestError("this fake answers nothing")

    config = {"score_source": {"kind": "trailing_ic", "window_months": 36}, "holding_count": 50}
    measure = grid.strategy_measure(backtest, excess_benchmark="000905.SH")

    with pytest.raises(StrategyRequestError):
        measure(config)
    assert received == [config]


def test_the_protocol_constants_are_the_protocols() -> None:
    assert grid.PROTOCOL_BOOTSTRAP_SAMPLES == 100_000
    assert grid.PROTOCOL_RANDOM_SEED == 20_260_926
    assert grid.PROTOCOL_FALSE_DISCOVERY_RATE == 0.10
    assert grid.PRIMARY_EXCESS_BENCHMARK == "equal_weight_all_a"
    assert grid.SESSIONS_PER_YEAR == 244
    assert grid.protocol_settings() == {
        "bootstrap_samples": 100_000,
        "random_seed": 20_260_926,
        "excess_benchmark": "equal_weight_all_a",
        "sessions_per_year": 244,
    }
    primary = grid.strategy_measure(lambda **kw: None, excess_benchmark="equal_weight_all_a")
    assert primary.settings == grid.protocol_settings()


def test_the_command_line_prints_the_family_and_the_table(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    ledger = tmp_path / "ledger.jsonl"
    grid.append_ledger(ledger, "discovery", {"i": 0}, {"p_excess": 0.0625})
    grid.append_ledger(ledger, "discovery", {"i": 1}, {"error": "refused"})

    assert grid.main(["family", "--ledger", str(ledger), "--stage", "discovery"]) == 0
    assert capsys.readouterr().out == "2\n"
    assert grid.main(["fdr", "--ledger", str(ledger), "--stage", "discovery"]) == 0
    table = json.loads(capsys.readouterr().out)
    assert table == json.loads(grid.fdr_table(ledger, "discovery", 0.10).model_dump_json())
    assert (table["family_size"], table["withheld_hypotheses"]) == (2, 1)


# --- the pooled runner (V2-P6-023) ----------------------------------------------------------------

LATER: Final[datetime] = AT + timedelta(hours=5)


def _pooled_measure(config: Mapping[str, object]) -> Mapping[str, object]:
    """Module level, so a spawned worker imports it by name: bound methods and closures do not
    pickle."""
    if config.get("refused"):
        raise StrategyRequestError(f"no cross section for {config['i']}")
    return {"p_excess": 1 / (2 + int(str(config["i"]))), "i": config["i"], "on": config["end"]}


def _without_time(path: Path) -> list[dict[str, Any]]:
    return [{k: v for k, v in row.items() if k != "recorded_at"} for row in _rows(path)]


class _RecordingExecutor(ThreadPoolExecutor):
    """A thread pool that records the configurations submitted to it, in order."""

    def __init__(self, workers: int) -> None:
        super().__init__(max_workers=workers)
        self.submitted: list[Mapping[str, object]] = []

    def submit(self, fn: Any, /, *args: Any, **kwargs: Any) -> Any:
        self.submitted.append(args[1])
        return super().submit(fn, *args, **kwargs)


def test_a_pooled_run_writes_the_rows_a_serial_run_writes(tmp_path: Path) -> None:
    """The same configurations, in the same order, with the same results value for value -- a
    refused row and a window-refused row included -- measured by spawned worker processes; only
    `recorded_at` differs."""
    configs = (
        {"i": 0, **WINDOW},
        {"i": 1, **WINDOW, "refused": True},
        {"i": 2, "start": date(2021, 6, 1), "end": date(2022, 1, 5)},
        {"i": 3, **WINDOW},
        {"i": 4, **WINDOW},
    )
    serial, pooled = tmp_path / "serial.jsonl", tmp_path / "pooled.jsonl"
    extra = {"code_commit": "a" * 40}
    grid.run_grid(
        serial,
        "discovery",
        configs,
        _pooled_measure,
        label_sessions=NO_LABEL,
        clock=lambda: AT,
        result_extra=extra,
    )
    landed: list[tuple[int, str]] = []

    with ProcessPoolExecutor(2, mp_context=multiprocessing.get_context("spawn")) as executor:
        run = grid.run_grid_in_pool(
            pooled,
            "discovery",
            configs,
            _pooled_measure,
            executor=executor,
            label_sessions=NO_LABEL,
            clock=lambda: LATER,
            result_extra=extra,
            landed=lambda index, config, landing: landed.append((index, landing)),
        )

    assert run == grid.GridRun(stage="discovery", ran=5, skipped=0)
    assert _without_time(pooled) == _without_time(serial)
    assert {row["recorded_at"] for row in _rows(pooled)} == {LATER.isoformat()}
    errors = [row["result"].get("error", "") for row in _rows(pooled)]
    assert errors[1] == "StrategyRequestError: no cross section for 1"
    assert errors[2].startswith("StageWindowError: ")
    assert [row["result"]["code_commit"] for row in _rows(pooled)[1:3]] == ["a" * 40] * 2
    assert landed == [(index, "ran") for index in range(1, 6)]


def test_a_result_that_finishes_early_waits_for_every_earlier_row(tmp_path: Path) -> None:
    ledger = tmp_path / "ledger.jsonl"
    configs = tuple({"i": i, **WINDOW} for i in range(3))
    finished: list[int] = []
    later_done = threading.Semaphore(0)
    seen_at_landing: list[list[int]] = []

    def measure(config: Mapping[str, object]) -> Mapping[str, object]:
        if config["i"] == 0:  # the first configuration is the slow one
            assert later_done.acquire(timeout=10) and later_done.acquire(timeout=10)
        finished.append(int(str(config["i"])))
        if config["i"] != 0:
            later_done.release()
        return {"p_excess": 0.5}

    with ThreadPoolExecutor(max_workers=3) as executor:
        grid.run_grid_in_pool(
            ledger,
            "discovery",
            configs,
            measure,
            executor=executor,
            label_sessions=NO_LABEL,
            clock=lambda: AT,
            landed=lambda index, config, landing: seen_at_landing.append(list(finished)),
        )

    assert finished[-1] == 0  # the later two finished first ...
    assert sorted(seen_at_landing[0]) == [0, 1, 2]  # ... and were held until the first landed
    assert [row["config"]["i"] for row in _rows(ledger)] == [0, 1, 2]


def test_a_pooled_run_skips_what_the_ledger_holds_and_never_submits_it(tmp_path: Path) -> None:
    ledger = tmp_path / "ledger.jsonl"
    configs = tuple({"i": i, **WINDOW} for i in range(4))
    for held in (configs[0], configs[2]):
        grid.append_ledger(ledger, "discovery", held, {"p_excess": 0.25}, recorded_at=AT)
    landed: list[tuple[int, str]] = []

    with _RecordingExecutor(2) as executor:
        run = grid.run_grid_in_pool(
            ledger,
            "discovery",
            configs,
            _pooled_measure,
            executor=executor,
            label_sessions=NO_LABEL,
            clock=lambda: AT,
            landed=lambda index, config, landing: landed.append((index, landing)),
        )

    assert executor.submitted == [configs[1], configs[3]]
    assert (run.ran, run.skipped) == (2, 2)
    assert landed == [(1, "held"), (2, "ran"), (3, "held"), (4, "ran")]
    assert [row["config"]["i"] for row in _rows(ledger)] == [0, 2, 1, 3]


def test_a_configuration_repeated_in_one_pooled_run_is_measured_once(tmp_path: Path) -> None:
    """The serial runner skips a repeat because the first one's row is in the ledger by then; the
    pooled runner plans before that row lands and must still measure the configuration once."""
    ledger = tmp_path / "ledger.jsonl"
    config = {"i": 0, **WINDOW}

    with _RecordingExecutor(2) as executor:
        run = grid.run_grid_in_pool(
            ledger,
            "discovery",
            (config, dict(config)),
            _pooled_measure,
            executor=executor,
            label_sessions=NO_LABEL,
            clock=lambda: AT,
        )

    assert executor.submitted == [config]
    assert (run.ran, run.skipped) == (1, 1)
    assert grid.stage_family(ledger, "discovery") == 1


def test_a_row_another_writer_lands_during_a_pooled_run_is_not_written_twice(
    tmp_path: Path,
) -> None:
    """The serial runner reads the ledger before every configuration; a pooled run that planned
    hours earlier must read it again before every append, or a configuration another writer
    landed meanwhile becomes a second row -- a hypothesis the family counts twice."""
    configs = tuple({"i": i, **WINDOW} for i in range(3))

    def measured(ledger: Path) -> Any:
        def measure(config: Mapping[str, object]) -> Mapping[str, object]:
            if config["i"] == 0:  # a second terminal lands configuration 2 meanwhile
                grid.append_ledger(
                    ledger, "discovery", configs[2], {"p_excess": 0.9}, recorded_at=AT
                )
            return {"p_excess": 0.5}

        return measure

    serial, pooled = tmp_path / "serial.jsonl", tmp_path / "pooled.jsonl"
    for config in configs:
        grid.run_grid(
            serial,
            "discovery",
            (config,),
            measured(serial),
            label_sessions=NO_LABEL,
            clock=lambda: AT,
        )
    landed: list[tuple[int, str]] = []

    with ThreadPoolExecutor(max_workers=1) as executor:
        run = grid.run_grid_in_pool(
            pooled,
            "discovery",
            configs,
            measured(pooled),
            executor=executor,
            label_sessions=NO_LABEL,
            clock=lambda: AT,
            landed=lambda index, config, landing: landed.append((index, landing)),
        )

    assert [row["config"]["i"] for row in _rows(pooled)] == [2, 0, 1]
    assert _rows(pooled) == _rows(serial)
    assert grid.stage_family(pooled, "discovery") == grid.stage_family(serial, "discovery") == 3
    assert (run.ran, run.skipped) == (2, 1)
    assert landed == [(1, "ran"), (2, "ran"), (3, "measured-but-held")]


def test_an_error_in_a_pooled_measurement_stops_the_run_after_the_rows_before_it(
    tmp_path: Path,
) -> None:
    """Rows of the configurations before the failing one land; nothing after it does, not even a
    configuration that finished before the failure surfaced; the error propagates."""
    ledger = tmp_path / "ledger.jsonl"
    configs = tuple({"i": i, **WINDOW} for i in range(4))
    last_done = threading.Event()
    finished: list[int] = []

    def measure(config: Mapping[str, object]) -> Mapping[str, object]:
        if config["i"] == 0:
            assert last_done.wait(timeout=10)
        if config["i"] == 1:
            raise KeyError("a bug, not a refusal")
        finished.append(int(str(config["i"])))
        if config["i"] == 3:
            last_done.set()
        return {"p_excess": 0.5}

    with ThreadPoolExecutor(max_workers=4) as executor, pytest.raises(KeyError, match="a bug"):
        grid.run_grid_in_pool(
            ledger,
            "discovery",
            configs,
            measure,
            executor=executor,
            label_sessions=NO_LABEL,
            clock=lambda: AT,
        )

    assert 3 in finished
    assert [row["config"]["i"] for row in _rows(ledger)] == [0]


def test_a_window_that_cannot_be_placed_stops_a_pooled_run_where_it_stops_a_serial_one(
    tmp_path: Path,
) -> None:
    configs = ({"i": 0, **WINDOW}, {"i": 1, "start": date(2015, 1, 5)}, {"i": 2, **WINDOW})
    serial, pooled = tmp_path / "serial.jsonl", tmp_path / "pooled.jsonl"
    with pytest.raises(grid.ResearchLedgerError, match="'end'"):
        grid.run_grid(
            serial, "discovery", configs, _pooled_measure, label_sessions=NO_LABEL, clock=lambda: AT
        )

    with _RecordingExecutor(2) as executor, pytest.raises(grid.ResearchLedgerError, match="'end'"):
        grid.run_grid_in_pool(
            pooled,
            "discovery",
            configs,
            _pooled_measure,
            executor=executor,
            label_sessions=NO_LABEL,
            clock=lambda: AT,
        )

    assert executor.submitted == [configs[0]]
    assert _rows(pooled) == _rows(serial)


def test_the_pooled_runner_will_not_run_the_holdout(tmp_path: Path) -> None:
    ledger = tmp_path / "ledger.jsonl"
    with (
        _RecordingExecutor(1) as executor,
        pytest.raises(grid.ResearchLedgerError, match="run_holdout"),
    ):
        grid.run_grid_in_pool(
            ledger,
            "holdout",
            ({"i": 0, "start": date(2024, 1, 2), "end": date(2024, 6, 28)},),
            _pooled_measure,
            executor=executor,
            label_sessions=NO_LABEL,
        )
    assert executor.submitted == []
    assert not ledger.exists()


class _CheckoutMoved(Exception):
    """A caller's refusal to append once the code it runs has changed under it."""


def _refusing_from(call: int) -> Any:
    """A `before_append` hook that admits `call - 1` appends and refuses from the `call`-th."""
    calls: list[int] = []

    def before_append() -> None:
        calls.append(len(calls) + 1)
        if len(calls) >= call:
            raise _CheckoutMoved(f"refused append {len(calls)}")

    return before_append


def test_a_serial_runner_asks_before_every_append_and_appends_nothing_once_refused(
    tmp_path: Path,
) -> None:
    """`before_append` runs after the measurement and before its row -- a window-refused row
    included -- and a refusal stops the run with the rows before it kept."""
    ledger = tmp_path / "ledger.jsonl"
    configs = (
        {"i": 0, **WINDOW},
        {"i": 1, "start": date(2021, 6, 1), "end": date(2022, 1, 5)},
        {"i": 2, **WINDOW},
    )

    with pytest.raises(_CheckoutMoved, match="refused append 3"):
        grid.run_grid(
            ledger,
            "discovery",
            configs,
            _pooled_measure,
            label_sessions=NO_LABEL,
            clock=lambda: AT,
            before_append=_refusing_from(3),
        )

    assert [row["config"]["i"] for row in _rows(ledger)] == [0, 1]


def test_a_pooled_runner_asks_before_every_append_and_appends_nothing_once_refused(
    tmp_path: Path,
) -> None:
    ledger = tmp_path / "ledger.jsonl"
    configs = tuple({"i": i, **WINDOW} for i in range(4))

    with ThreadPoolExecutor(max_workers=2) as executor, pytest.raises(_CheckoutMoved):
        grid.run_grid_in_pool(
            ledger,
            "discovery",
            configs,
            _pooled_measure,
            executor=executor,
            label_sessions=NO_LABEL,
            clock=lambda: AT,
            before_append=_refusing_from(3),
        )

    assert [row["config"]["i"] for row in _rows(ledger)] == [0, 1]


def test_an_interrupt_reaches_a_pooled_run_while_it_waits_for_a_measurement(
    tmp_path: Path,
) -> None:
    """The parent waits for each result in short slices, so a Ctrl-C (here `interrupt_main`, the
    platform-neutral way to deliver one to the main thread) is raised within a slice rather than
    once a measurement of hours ends; the rows before it stay a correct prefix."""
    ledger = tmp_path / "ledger.jsonl"
    configs = tuple({"i": i, **WINDOW} for i in range(3))
    release = threading.Event()

    def measure(config: Mapping[str, object]) -> Mapping[str, object]:
        if config["i"] == 1:
            release.wait(timeout=60)
        return {"p_excess": 0.5}

    interrupter = threading.Timer(0.5, _thread.interrupt_main)
    began = monotonic()
    try:
        with ThreadPoolExecutor(max_workers=1) as executor:
            interrupter.start()
            with pytest.raises(KeyboardInterrupt):
                grid.run_grid_in_pool(
                    ledger,
                    "discovery",
                    configs,
                    measure,
                    executor=executor,
                    label_sessions=NO_LABEL,
                    clock=lambda: AT,
                )
            waited = monotonic() - began
            release.set()
    finally:
        release.set()
        interrupter.cancel()

    assert waited < 10
    assert [row["config"]["i"] for row in _rows(ledger)] == [0]
