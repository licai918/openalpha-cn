"""`scripts/research/grid.py`: the research grid runner and its ledger (`V2-P6-008`).

The research protocol (section 2 of `docs/superpowers/plans/2026-09-26-selection-ready.md`) puts
three numbers in code rather than in a person's hands: the family size a stage's p-values are
controlled against (every ledger row of the stage, failures included), the non-overlapping
sample the sign-flip tests run on (one session in every `horizon`), and the dependence assumption
the false-discovery control is asked for (`arbitrary`, Benjamini-Yekutieli). Each has a test here
that goes red when the number is computed any other way.
"""

from __future__ import annotations

import importlib
import json
import math
import statistics
import sys
from collections.abc import Mapping
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from types import ModuleType
from typing import Any, Final

import pytest
from panel_fixtures import EXCHANGE, GeneratedPanel
from strategy_fixtures import READ_AT, REVERSAL, write_strategy_corpus

from openalpha_cn.backtest.multiple_testing import (
    HypothesisTest,
    MultipleTestingRequest,
    control_false_discovery_rate,
)
from openalpha_cn.backtest.outcome_statistics import sign_flip_test
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

AT: Final[datetime] = datetime(2026, 9, 26, 12, 0, tzinfo=UTC)


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
    grid.append_ledger(ledger, "composite", {"i": 0}, {"p_excess": 0.5})
    grid.append_ledger(ledger, "composite", {"i": 1}, {"error": "refused"})

    assert grid.stage_family(ledger, "discovery") == 1
    assert grid.stage_family(ledger, "composite") == 2
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
    config = {"holding_count": 50, "start": date(2015, 1, 5), "weight": Decimal("0.5")}

    grid.append_ledger(ledger, "discovery", config, {"p_excess": 0.25}, recorded_at=AT)

    (row,) = _rows(ledger)
    assert row["schema"] == grid.LEDGER_SCHEMA
    assert row["stage"] == "discovery"
    assert row["config"] == {"holding_count": 50, "start": "2015-01-05", "weight": "0.5"}
    assert row["config_id"] == grid.config_id(config)
    assert len(row["config_id"]) == 64
    assert row["result"] == {"p_excess": 0.25}
    assert row["recorded_at"] == "2026-09-26T12:00:00+00:00"


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
    grid.append_ledger(ledger, "composite", {"i": 0}, {"p_excess": 0.0001})

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
        grid.fdr_table(ledger, "composite", 0.10)


# --- the runner --------------------------------------------------------------------------------


def test_the_runner_records_a_refused_configuration_as_a_row_of_the_family(tmp_path: Path) -> None:
    ledger = tmp_path / "ledger.jsonl"
    configs = grid.expand_grid({"holding_count": (30, 50, 100)})

    def measure(config: Mapping[str, object]) -> Mapping[str, object]:
        if config["holding_count"] == 50:
            raise StrategyRequestError("no cross section on the signal day")
        return {"p_excess": 0.5}

    run = grid.run_grid(ledger, "discovery", configs, measure, clock=lambda: AT)

    assert run.ran == 3
    assert run.skipped == 0
    assert grid.stage_family(ledger, "discovery") == 3
    refused = [row["result"] for row in _rows(ledger) if "error" in row["result"]]
    assert refused == [
        {"error": "StrategyRequestError: no cross section on the signal day"},
    ]


def test_the_runner_resumes_by_skipping_what_the_ledger_already_holds(tmp_path: Path) -> None:
    ledger = tmp_path / "ledger.jsonl"
    configs = grid.expand_grid({"holding_count": (30, 50)})
    calls: list[Mapping[str, object]] = []

    def measure(config: Mapping[str, object]) -> Mapping[str, object]:
        calls.append(config)
        return {"p_excess": 0.5}

    grid.run_grid(ledger, "discovery", configs[:1], measure, clock=lambda: AT)
    run = grid.run_grid(ledger, "discovery", configs, measure, clock=lambda: AT)

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
        grid.run_grid(ledger, "discovery", ({"i": 0},), measure, clock=lambda: AT)
    assert grid.stage_family(ledger, "discovery") == 0


def test_the_runner_will_not_run_the_holdout(tmp_path: Path) -> None:
    """The holdout runs once, through `registry.run_holdout`, behind the registration guard."""
    with pytest.raises(grid.ResearchLedgerError, match="registry"):
        grid.run_grid(
            tmp_path / "l.jsonl", grid.HOLDOUT_STAGE, ({"i": 0},), lambda c: {}, clock=lambda: AT
        )


def test_a_second_holdout_row_is_refused_by_the_ledger_itself(tmp_path: Path) -> None:
    ledger = tmp_path / "ledger.jsonl"
    grid.append_ledger(ledger, grid.HOLDOUT_STAGE, {"i": 0}, {"p_excess": 0.5})

    with pytest.raises(grid.ResearchLedgerError, match="once"):
        grid.append_ledger(ledger, grid.HOLDOUT_STAGE, {"i": 1}, {"p_excess": 0.5})


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


def test_the_runner_drives_the_sdk_and_ledgers_net_excess_and_its_sign_flip(
    runtime: tuple[Path, GeneratedPanel], tmp_path: Path
) -> None:
    root, panel = runtime
    sdk = OpenAlphaSDK(runtime_dir=root)
    configs = tuple(
        {**_base_config(panel), **point}
        for point in grid.expand_grid({"holding_count": (2, 3), "rebalance_every_sessions": (3,)})
    )
    measure = grid.strategy_measure(sdk.run_strategy_backtest, excess_benchmark="000905.SH")
    ledger = tmp_path / "ledger.jsonl"

    run = grid.run_grid(ledger, "discovery", configs, measure, clock=lambda: AT)

    assert run.ran == 2
    rows = _rows(ledger)
    for config, row in zip(configs, rows, strict=True):
        backtest = sdk.run_strategy_backtest(**config)
        excess = tuple(
            period.net_return - period.benchmark_returns["000905.SH"] for period in backtest.periods
        )
        result = row["result"]
        assert result["excess_benchmark"] == "000905.SH"
        assert result["net_excess"] == [str(value) for value in excess]
        assert result["period_count"] == len(backtest.periods)
        expected = sign_flip_test(
            tuple(float(value) for value in excess),
            bootstrap_samples=grid.PROTOCOL_BOOTSTRAP_SAMPLES,
            random_seed=grid.PROTOCOL_RANDOM_SEED,
        )
        assert result["p_excess"] == expected.p_value
        assert result["p_excess_exact"] is expected.exact
        sessions = [period.sessions for period in backtest.periods]
        assert result["period_sessions"] == sessions
        values = [float(value) for value in excess]
        mean = statistics.fmean(values)
        per_year = grid.SESSIONS_PER_YEAR / statistics.fmean(sessions)
        assert result["information_ratio"] == pytest.approx(
            mean / statistics.stdev(values) * math.sqrt(per_year), rel=1e-12
        )
        assert result["annualized_mean_net_excess"] == pytest.approx(mean * per_year, rel=1e-12)
    assert grid.fdr_table(ledger, "discovery", 0.10).family_size == 2


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
