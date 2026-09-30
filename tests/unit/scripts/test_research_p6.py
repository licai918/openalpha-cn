"""`scripts/research/p6.py`: the P6 research stage driver (`V2-P6-010`).

The pre-registered protocol (`docs/research/p6-protocol.md`) fixes every grid, every selection
rule and every pass criterion before a research run starts. This driver turns each stage into
code so that no person types a grid, a survivor list, a selection or a verdict. Each test here
pins one clause of the protocol and goes red when the clause is computed any other way:

* section 4: the 189-configuration discovery grid, each configuration's `end`, the measure that
  carries both families, the survivor rule (BY rejection **and** a positive mean -- the direction
  rule), one tier per factor, and the no-survivor fallback;
* section 5: the 19 score sources of step 2a, the best source and the 36 strategies of step 2b
  (the one equal to step 2a is not run twice), and the top-5 finalist rule with its "BY rejected
  nothing" branch;
* section 6: the validation choice by information ratio, ties to the lower turnover;
* section 7: the registration (never the holdout run), the maximum relative drawdown on complete
  periods, and the three pass criteria, each able to fail on its own;
* section 1: the stale-return-path precondition, run before every command and refused by name.

Nothing here touches a real research store. Selection rules are driven through hand-written
ledger rows; the end-to-end test drives every command through a fake SDK whose requests are
resolved by the real `strategy_view` resolvers, so a field name the SDK would refuse fails here.
"""

from __future__ import annotations

import _thread
import ast
import dataclasses
import hashlib
import importlib
import inspect
import json
import multiprocessing
import os
import signal
import statistics
import subprocess
import sys
import threading
from collections.abc import Callable, Iterator, Mapping, Sequence
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path
from time import monotonic, sleep
from types import ModuleType
from typing import Any, Final
from zoneinfo import ZoneInfo

import pytest
from panel_fixtures import EXCHANGE as FIXTURE_EXCHANGE
from panel_fixtures import GeneratedPanel
from panel_fixtures import _calendar_batch as calendar_batch
from research_repo import commit_file, git, head
from strategy_fixtures import READ_AT, REVERSAL, write_strategy_corpus

from openalpha_cn import strategy_view
from openalpha_cn.backtest.outcome_statistics import sign_flip_test
from openalpha_cn.backtest.strategy_backtest import (
    EQUAL_WEIGHT_ALL_A_HELD,
    StrategyBacktestError,
)
from openalpha_cn.domain.horizon import parse_horizon
from openalpha_cn.domain.labels import build_label_window
from openalpha_cn.domain.panel_batch import ColumnarPanelBatch, PanelColumn, TimelineColumns
from openalpha_cn.domain.trading_calendar import CalendarDay, build_trading_calendar
from openalpha_cn.domain.upstream_defects import UPSTREAM_DEFECT_DATA_COLUMNS
from openalpha_cn.panel.catalog import DEFAULT_DATE_TIMEZONE
from openalpha_cn.panel.store import PanelStore
from openalpha_cn.panel_ingest import (
    UPSTREAM_DEFECTS_DATASET,
    split_panel_batch_by_year,
    write_trading_calendar,
    write_upstream_defects,
)
from openalpha_cn.panel_view import panel_store
from openalpha_cn.runtime.seeding import thread_count_pins
from openalpha_cn.sdk import OpenAlphaSDK
from openalpha_cn.strategy_view import StrategyRequestError

ROOT: Final[Path] = Path(__file__).resolve().parents[3]
RESEARCH: Final[Path] = ROOT / "scripts" / "research"


def _research_module(name: str) -> ModuleType:
    if str(RESEARCH) not in sys.path:
        sys.path.insert(0, str(RESEARCH))
    return importlib.import_module(name)


grid = _research_module("grid")
registry = _research_module("registry")
p6 = _research_module("p6")

COMMIT: Final[str] = "0123456789abcdef0123456789abcdef01234567"
OTHER_COMMIT: Final[str] = "fedcba9876543210fedcba9876543210fedcba98"
AT: Final[datetime] = datetime(2026, 9, 27, 12, 0, tzinfo=UTC)


def _weekdays(first: date, last: date) -> tuple[date, ...]:
    days = (first + timedelta(days=offset) for offset in range((last - first).days + 1))
    return tuple(day for day in days if day.weekday() < 5)


SESSIONS: Final[tuple[date, ...]] = _weekdays(date(2015, 1, 1), date(2026, 12, 31))
"""A weekday calendar over every stage: the stored calendar's shape, generated at test time."""


# --- fakes -------------------------------------------------------------------------------------


@dataclass(frozen=True)
class _Period:
    start: date
    end: date
    sessions: int
    net_return: Decimal
    benchmark_returns: Mapping[str, Decimal]
    turnover: Decimal
    benchmark_unknowable_sessions: tuple[str, ...] = ()
    benchmark_members: int | None = None


@dataclass(frozen=True)
class _Spec:
    rebalance_every_sessions: int


@dataclass(frozen=True)
class _Backtest:
    spec: _Spec
    periods: tuple[_Period, ...]
    unknowable_crossings: tuple[Any, ...] = ()


def _backtest(
    nets: Sequence[str], *, benchmarks: Sequence[str] | None = None, interval: int = 20
) -> _Backtest:
    benches = benchmarks if benchmarks is not None else ["0"] * len(nets)
    start = date(2015, 1, 5)
    periods = tuple(
        _Period(
            start=start + timedelta(days=index),
            end=start + timedelta(days=index + 1),
            sessions=interval,
            net_return=Decimal(net),
            benchmark_returns={
                EQUAL_WEIGHT_ALL_A_HELD: Decimal(bench),
                "000905.SH": Decimal("0"),
            },
            turnover=Decimal("0.5"),
        )
        for index, (net, bench) in enumerate(zip(nets, benches, strict=True))
    )
    return _Backtest(spec=_Spec(interval), periods=periods)


@dataclass(frozen=True)
class _ICPoint:
    prediction_day: date
    ic: float | None


@dataclass(frozen=True)
class _ICSeries:
    points: tuple[_ICPoint, ...]


def _resolve(config: Mapping[str, Any]) -> None:
    """`OpenAlphaSDK.run_strategy_backtest`'s own resolution: its `components` default to `()`."""
    strategy_view.strategy_request(**{"components": (), **config})


def _digest(value: object) -> int:
    return int(hashlib.sha256(grid.canonical_json(value).encode()).hexdigest()[:8], 16)


@dataclass
class _FakeSDK:
    """Resolves every request with the real resolvers, then answers from a hash of it."""

    backtests: list[Mapping[str, object]] = field(default_factory=list)
    ic_calls: list[Mapping[str, object]] = field(default_factory=list)

    def run_strategy_backtest(self, **config: Any) -> _Backtest:
        _resolve(config)
        self.backtests.append(config)
        seed = _digest(config)
        nets = [f"{((seed >> (4 * k)) % 31 - 12) / 10000:.4f}" for k in range(4)]
        return _backtest(nets, interval=int(config["rebalance_every_sessions"]))

    def factor_ic_series(self, **request: Any) -> _ICSeries:
        strategy_view.ic_series_request(**request)
        self.ic_calls.append(request)
        seed = _digest(request)
        days = [day for day in SESSIONS if request["start"] <= day <= request["end"]][:3]
        return _ICSeries(
            tuple(_ICPoint(day, ((seed >> k) % 7 - 3) / 100) for k, day in enumerate(days))
        )


def _result(
    *,
    p: float = 0.9,
    mean: float = 0.001,
    ir: float | None = 0.1,
    turnover: float = 0.5,
    nets: Sequence[str] = ("0.01", "-0.005", "0.004"),
    benches: Sequence[str] | None = None,
) -> dict[str, object]:
    benches = benches if benches is not None else ["0"] * len(nets)
    return {
        "p_excess": p,
        "mean_net_excess": mean,
        "information_ratio": ir,
        "mean_turnover": turnover,
        "annualized_mean_net_excess": mean * 12.2,
        "net_return": list(nets),
        "benchmark_return": list(benches),
        "period_complete": [True] * len(nets),
        "period_sessions": [20] * len(nets),
        "code_commit": COMMIT,
        "excess_benchmark": EQUAL_WEIGHT_ALL_A_HELD,
    }


REFUSED_ROW: Final[Mapping[str, object]] = {
    "error": "StrategyRunBlockedError: x",
    "code_commit": COMMIT,
    "excess_benchmark": EQUAL_WEIGHT_ALL_A_HELD,
}
"""A refused row as this driver writes one: the refusal, the commit, and the benchmark."""


def _fill(
    ledger: Path,
    stage: str,
    configs: Sequence[Mapping[str, object]],
    special: Mapping[str, Mapping[str, object]] | None = None,
) -> None:
    """One hand-written row per configuration; `special` overrides a row by config id."""
    special = special or {}
    for config in configs:
        identity = grid.config_id(config)
        grid.append_ledger(ledger, stage, config, special.get(identity, _result()), recorded_at=AT)


def _discovery(factor: str, tier: str, horizon: int) -> Mapping[str, object]:
    for config in p6.discovery_configs(SESSIONS):
        ((key, level, _),) = config["components"]
        if (key, level, config["rebalance_every_sessions"]) == (factor, tier, horizon):
            return config
    raise AssertionError((factor, tier, horizon))


def _id(config: Mapping[str, object]) -> str:
    return str(grid.config_id(config))


def _no_guard() -> None:
    """The checkout guard of a test that is not about the checkout: it never refuses."""


# --- section 4: the discovery grid ---------------------------------------------------------------


def test_the_discovery_grid_is_21_factors_by_3_tiers_by_3_horizons_with_the_protocol_fields() -> (
    None
):
    configs = p6.discovery_configs(SESSIONS)

    assert len(configs) == 189
    assert len({_id(config) for config in configs}) == 189
    factors = {config["components"][0][0] for config in configs}
    assert len(factors) == 21
    assert {config["components"][0][1] for config in configs} == {
        "raw",
        "processed",
        "neutralized",
    }
    for config in configs:
        ((_, tier, weight),) = config["components"]
        horizon = config["rebalance_every_sessions"]
        assert horizon in (1, 5, 20)
        assert weight == Decimal("1")
        assert config["combine"] == "zscore_sum"
        assert config["holding_count"] == 50
        assert config["buffer_rank"] is None
        assert config["max_industry_weight"] is None
        assert config["start"] == date(2015, 1, 5)
        assert config["as_of"] == datetime(2026, 9, 26, 4, 0, tzinfo=UTC)
        assert config["exchange"] == "SSE"
        assert config["ic"] == {
            "horizon_sessions": horizon,
            "ic_method": "spearman",
            "min_securities": 100,
        }
        assert config["transform"] == (None if tier == "raw" else "cross_section_standard/v1")
        assert config["neutralization"] == (
            "industry_and_size/v1" if tier == "neutralized" else None
        )


def test_each_discovery_end_is_the_last_session_whose_ic_label_still_fits_the_stage() -> None:
    """An IC label enters on the session after its prediction day and exits `horizon` sessions
    later, so the last prediction day is `horizon + 1` sessions before the stage's last session
    (2021-12-31). The label's exit is placed here by the domain's own `build_label_window`."""
    first = date(2015, 1, 1)
    calendar = build_trading_calendar(
        "SSE",
        [
            CalendarDay(
                calendar_date=first + timedelta(days=offset),
                is_trading=(first + timedelta(days=offset)).weekday() < 5,
            )
            for offset in range((date(2026, 12, 31) - first).days + 1)
        ],
    )
    zone = ZoneInfo(DEFAULT_DATE_TIMEZONE)
    ends = {
        config["rebalance_every_sessions"]: config["end"]
        for config in p6.discovery_configs(SESSIONS)
    }

    assert ends == {1: date(2021, 12, 29), 5: date(2021, 12, 23), 20: date(2021, 12, 2)}
    for horizon, end in ends.items():
        window = build_label_window(
            as_of=datetime.combine(end, time(16, 0), zone),
            zone=zone,
            horizon=parse_horizon(f"{horizon}d"),
            calendar=calendar,
        )
        assert window.exit_day == date(2021, 12, 31)
        later = calendar.shift(end, 1)
        after = build_label_window(
            as_of=datetime.combine(later, time(16, 0), zone),
            zone=zone,
            horizon=parse_horizon(f"{horizon}d"),
            calendar=calendar,
        )
        assert after.exit_day > date(2021, 12, 31)


def test_the_end_is_counted_on_the_stored_calendar_not_on_weekdays() -> None:
    holiday = tuple(day for day in SESSIONS if day != date(2021, 12, 30))
    ends = {c["rebalance_every_sessions"]: c["end"] for c in p6.discovery_configs(holiday)}

    assert ends[1] == date(2021, 12, 28)


def test_a_strategy_stage_ends_on_its_last_session_and_the_holdout_on_the_protocol_day() -> None:
    assert p6.stage_end(SESSIONS, "composition", label_sessions=0) == date(2021, 12, 31)
    assert p6.stage_end(SESSIONS, "validation", label_sessions=0) == date(2023, 12, 29)
    assert p6.stage_end(SESSIONS, "holdout", label_sessions=0) == date(2026, 9, 24)
    short = tuple(day for day in SESSIONS if day != date(2026, 9, 24))
    assert p6.stage_end(short, "holdout", label_sessions=0) == date(2026, 9, 23)


def test_the_sessions_are_read_from_the_stored_calendar(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every stage year's partition, read at the protocol's instant through the gated loader."""
    first = date(2015, 1, 1)
    days = [
        CalendarDay(
            calendar_date=first + timedelta(days=offset),
            is_trading=(first + timedelta(days=offset)).weekday() < 5,
        )
        for offset in range((date(2026, 12, 31) - first).days + 1)
    ]
    store = panel_store(tmp_path)
    for _, part in split_panel_batch_by_year(calendar_batch(days)):
        write_trading_calendar(store, part)
    monkeypatch.setattr(p6, "EXCHANGE", FIXTURE_EXCHANGE)

    assert p6.stored_sessions(tmp_path) == SESSIONS


def test_a_stage_start_that_is_not_a_session_is_refused() -> None:
    closed = tuple(day for day in SESSIONS if day != date(2015, 1, 5))
    with pytest.raises(p6.ProtocolMismatchError, match="2015-01-05"):
        p6.discovery_configs(closed)


# --- section 4: the measure -----------------------------------------------------------------------


def test_the_discovery_measure_ledgers_the_strategy_and_the_non_overlapping_ic_sign_flip() -> None:
    config = _discovery("reversal_1d/v1", "processed", 5)
    days = [day for day in SESSIONS if config["start"] <= day <= config["end"]]
    ics = {day: (0.02 if index % 3 else -0.01) for index, day in enumerate(days[:40])}
    asked: list[Mapping[str, object]] = []

    def ic_series(**request: object) -> _ICSeries:
        asked.append(request)
        return _ICSeries(tuple(_ICPoint(day, value) for day, value in ics.items()))

    nets = ["0.01", "-0.004", "0.006", "0.002"]
    measure = p6.DiscoveryMeasure(
        backtest=lambda **kw: _backtest(nets, interval=5),
        ic_series=ic_series,
        sessions=SESSIONS,
        code_commit=COMMIT,
    )

    result = measure(config)

    expected = grid.strategy_result(
        _backtest(nets, interval=5), excess_benchmark=EQUAL_WEIGHT_ALL_A_HELD
    )
    assert result["excess_benchmark"] == EQUAL_WEIGHT_ALL_A_HELD  # V2-P6-024
    assert {key: result[key] for key in expected} == expected
    assert asked == [
        {
            "factor": "reversal_1d/v1",
            "tier": "processed",
            "transform": "cross_section_standard/v1",
            "neutralization": None,
            "horizon_sessions": 5,
            "ic_method": "spearman",
            "min_securities": 100,
            "start": config["start"],
            "end": config["end"],
            "as_of": config["as_of"],
            "exchange": "SSE",
        }
    ]
    sampled = [ics[day] for day in days[::5] if day in ics]
    test = sign_flip_test(tuple(sampled), bootstrap_samples=100_000, random_seed=20_260_926)
    assert result["p_ic"] == test.p_value
    assert result["ic_measured"] == len(sampled) == 8
    assert result["mean_ic"] == pytest.approx(statistics.fmean(sampled))
    assert result["code_commit"] == COMMIT


def test_a_refused_ic_leaves_the_primary_family_measured() -> None:
    config = _discovery("reversal_1d/v1", "raw", 1)

    def refuse(**request: object) -> _ICSeries:
        raise StrategyRequestError("no build")

    measure = p6.DiscoveryMeasure(
        backtest=lambda **kw: _backtest(["0.01", "0.02"], interval=1),
        ic_series=refuse,
        sessions=SESSIONS,
        code_commit=COMMIT,
    )
    result = measure(config)

    assert "p_excess" in result
    assert "p_ic" not in result
    assert result["ic_error"] == "StrategyRequestError: no build"


def test_the_discovery_measure_drives_the_real_sdk(tmp_path: Path) -> None:
    """The keyword arguments the measure hands both SDK methods are the ones they accept."""
    panel: GeneratedPanel = write_strategy_corpus(tmp_path)
    sdk = OpenAlphaSDK(runtime_dir=tmp_path)
    config = {
        "components": ((REVERSAL.qualified_key, "raw", Decimal("1")),),
        "combine": "zscore_sum",
        "transform": None,
        "neutralization": None,
        "start": panel.sessions[1],
        "end": panel.sessions[7],
        "as_of": READ_AT,
        "exchange": FIXTURE_EXCHANGE,
        "holding_count": 2,
        "rebalance_every_sessions": 2,
        "buffer_rank": None,
        "max_industry_weight": None,
        "ic": {"horizon_sessions": 1, "ic_method": "spearman", "min_securities": 3},
    }
    measure = p6.DiscoveryMeasure(
        backtest=sdk.run_strategy_backtest,
        ic_series=sdk.factor_ic_series,
        sessions=tuple(panel.sessions),
        code_commit=COMMIT,
    )

    result = measure(config)

    assert result["period_count"] >= 2
    assert result["ic_measured"] == 7
    assert 0 < result["p_ic"] <= 1
    assert "max_relative_drawdown" in result
    # V2-P6-024: the SDK's default benchmarks price the protocol's primary, and it is measured.
    assert result["excess_benchmark"] == EQUAL_WEIGHT_ALL_A_HELD
    assert "error" not in result


def test_a_stage_measure_tests_against_the_held_benchmark() -> None:
    """`V2-P6-024`: stage 2, validation and the holdout reduce against the held all-A benchmark."""
    nets = ["0.01", "-0.004", "0.006"]
    measure = p6.StrategyStageMeasure(
        backtest=lambda **kw: _backtest(nets, benchmarks=["0.002", "0", "-0.001"]),
        code_commit=COMMIT,
    )

    result = measure({})

    expected = grid.strategy_result(
        _backtest(nets, benchmarks=["0.002", "0", "-0.001"]),
        excess_benchmark=EQUAL_WEIGHT_ALL_A_HELD,
    )
    assert result == {**expected, "code_commit": COMMIT}
    assert result["net_excess"] == ["0.008", "-0.004", "0.007"]


# --- section 4: survivors -------------------------------------------------------------------------


def _survivor_ledger(tmp_path: Path, special: Mapping[str, Mapping[str, object]]) -> Path:
    ledger = tmp_path / "ledger.jsonl"
    _fill(ledger, "discovery", p6.discovery_configs(SESSIONS), special)
    return ledger


def test_a_survivor_needs_a_by_rejection_and_a_positive_mean(tmp_path: Path) -> None:
    """The direction rule: a configuration significant in the direction opposite its declaration
    has a negative mean and is not a survivor, however small its p-value."""
    winner = _discovery("momentum_20_sessions/v1", "raw", 5)
    reversed_ = _discovery("book_to_price/v1", "processed", 20)
    positive_but_weak = _discovery("amihud_60/v1", "neutralized", 1)
    ledger = _survivor_ledger(
        tmp_path,
        {
            _id(winner): _result(p=1e-6, mean=0.002, ir=0.9),
            _id(reversed_): _result(p=1e-6, mean=-0.002, ir=-0.9),
            _id(positive_but_weak): _result(p=0.2, mean=0.004, ir=1.5),
        },
    )
    report = grid.fdr_table(ledger, "discovery", 0.10)
    assert report.verdict_for(_id(reversed_)).rejected
    assert not report.verdict_for(_id(positive_but_weak)).rejected

    answer = p6.survivors(ledger, SESSIONS)

    assert answer["components"] == [["momentum_20_sessions/v1", "raw"]]
    assert answer["fallback"] is False
    assert [row["config_id"] for row in answer["survivors"]] == [_id(winner)]
    assert answer["family_size"] == 189


def test_each_surviving_factor_contributes_the_tier_of_its_best_information_ratio(
    tmp_path: Path,
) -> None:
    raw = _discovery("turnover_60/v1", "raw", 1)
    processed = _discovery("turnover_60/v1", "processed", 20)
    neutralized = _discovery("turnover_60/v1", "neutralized", 5)
    tied_low_turnover = _discovery("revenue_yoy/v1", "neutralized", 20)
    tied_high_turnover = _discovery("revenue_yoy/v1", "raw", 5)
    ledger = _survivor_ledger(
        tmp_path,
        {
            _id(raw): _result(p=1e-6, mean=0.001, ir=0.5, turnover=0.1),
            _id(processed): _result(p=1e-6, mean=0.001, ir=0.8, turnover=0.9),
            _id(neutralized): _result(p=1e-6, mean=0.001, ir=0.7, turnover=0.05),
            _id(tied_low_turnover): _result(p=1e-6, mean=0.001, ir=0.6, turnover=0.2),
            _id(tied_high_turnover): _result(p=1e-6, mean=0.001, ir=0.6, turnover=0.3),
        },
    )

    answer = p6.survivors(ledger, SESSIONS)

    assert answer["components"] == [
        ["turnover_60/v1", "processed"],
        ["revenue_yoy/v1", "neutralized"],
    ]


def test_a_tie_on_ratio_and_turnover_goes_to_the_lower_config_id(tmp_path: Path) -> None:
    """The protocol's third tie level: `config_id` ascending, a rule that reads no result."""
    one = _discovery("turnover_60/v1", "raw", 1)
    two = _discovery("turnover_60/v1", "processed", 1)
    lower, higher = sorted((one, two), key=_id)
    ledger = _survivor_ledger(
        tmp_path,
        {
            _id(higher): _result(p=1e-6, mean=0.001, ir=0.5, turnover=0.1),
            _id(lower): _result(p=1e-6, mean=0.001, ir=0.5, turnover=0.1),
        },
    )

    answer = p6.survivors(ledger, SESSIONS)

    assert answer["components"] == [["turnover_60/v1", lower["components"][0][1]]]


def test_with_no_survivor_every_factor_enters_stage_two_on_its_processed_tier(
    tmp_path: Path,
) -> None:
    only_negative = _discovery("reversal_1d/v1", "raw", 1)
    ledger = _survivor_ledger(tmp_path, {_id(only_negative): _result(p=1e-9, mean=-0.01)})

    answer = p6.survivors(ledger, SESSIONS)

    assert answer["fallback"] is True
    assert answer["survivors"] == []
    assert len(answer["components"]) == 21
    assert {tier for _, tier in answer["components"]} == {"processed"}


def test_the_survivors_are_computed_only_from_a_complete_discovery_stage(tmp_path: Path) -> None:
    ledger = tmp_path / "ledger.jsonl"
    _fill(ledger, "discovery", p6.discovery_configs(SESSIONS)[:188])

    with pytest.raises(p6.StageIncompleteError, match="1 configuration"):
        p6.survivors(ledger, SESSIONS)


@pytest.mark.parametrize(
    "benchmark",
    [pytest.param("equal_weight_all_a", id="the_old_primary"), pytest.param(None, id="unnamed")],
)
def test_a_row_measured_against_another_benchmark_is_refused_by_name(
    tmp_path: Path, benchmark: str | None
) -> None:
    """`V2-P6-024` (M4): a stage's family is one hypothesis family only when every measured row
    tested the same excess. A row against the retired `equal_weight_all_a` -- stage 1 as first
    run -- or one naming no benchmark is refused by name rather than selected on."""
    old = _discovery("reversal_1d/v1", "raw", 5)
    result = {key: value for key, value in _result().items() if key != "excess_benchmark"}
    if benchmark is not None:
        result["excess_benchmark"] = benchmark
    ledger = _survivor_ledger(tmp_path, {_id(old): result})

    with pytest.raises(p6.BenchmarkMismatchError, match=_id(old)) as refused:
        p6.survivors(ledger, SESSIONS)
    assert "equal_weight_all_a_held" in str(refused.value)


def test_a_refused_row_carries_the_primary_and_still_counts_in_the_family(tmp_path: Path) -> None:
    """A refused configuration measured nothing, but the driver writes the benchmark it was to
    be measured against into its row (`V2-P6-024` m1); it is a withheld hypothesis of the
    family, which it must still enlarge."""
    refused = _discovery("reversal_1d/v1", "raw", 5)
    ledger = _survivor_ledger(tmp_path, {_id(refused): REFUSED_ROW})

    answer = p6.survivors(ledger, SESSIONS)

    assert answer["family_size"] == 189
    assert answer["fdr_table"]["withheld_hypotheses"] == 1


def test_a_refused_row_without_a_benchmark_is_refused_by_name(tmp_path: Path) -> None:
    """No writer of this driver leaves the key out any more -- the measures' refusals and the
    runner's own (`result_extra`) carry it -- so a refused row without one was written by
    something else (a pre-round-2 ledger, a hand) and is not admitted as this family's."""
    refused = _discovery("reversal_1d/v1", "raw", 5)
    unmarked = {key: value for key, value in REFUSED_ROW.items() if key != "excess_benchmark"}
    ledger = _survivor_ledger(tmp_path, {_id(refused): unmarked})

    with pytest.raises(p6.BenchmarkMismatchError, match=_id(refused)):
        p6.survivors(ledger, SESSIONS)


@pytest.mark.parametrize("measure_kind", ["discovery", "stage"])
def test_a_measure_s_refusal_names_the_primary_benchmark(measure_kind: str) -> None:
    """m1: the row a measure writes for a refused backtest carries the benchmark it would have
    been measured against, like every measured row."""

    def refuse(**config: object) -> Any:
        raise StrategyRequestError("no build")

    measure: Any = (
        p6.DiscoveryMeasure(
            backtest=refuse, ic_series=refuse, sessions=SESSIONS, code_commit=COMMIT
        )
        if measure_kind == "discovery"
        else p6.StrategyStageMeasure(backtest=refuse, code_commit=COMMIT)
    )
    config = _discovery("reversal_1d/v1", "raw", 1) if measure_kind == "discovery" else {}

    assert measure(config) == {
        "error": "StrategyRequestError: no build",
        "code_commit": COMMIT,
        "excess_benchmark": grid.PRIMARY_EXCESS_BENCHMARK,
    }


def test_a_validation_row_against_another_benchmark_is_refused(tmp_path: Path) -> None:
    """The same rule on section 6's selection, which reads the finalists' stage too."""

    def results(configs: list[Mapping[str, object]]) -> Mapping[str, Any]:
        return {_id(configs[0]): {**_result(), "excess_benchmark": "equal_weight_all_a"}}

    ledger, _ = _validation_ledger(tmp_path, results)

    with pytest.raises(p6.BenchmarkMismatchError, match="equal_weight_all_a"):
        p6.validation_selection(ledger, SESSIONS)


def test_a_row_the_protocol_does_not_build_is_refused_rather_than_counted(tmp_path: Path) -> None:
    """A hand-added hypothesis would enlarge the family and could be a survivor no grid tried."""
    ledger = _survivor_ledger(tmp_path, {})
    extra = {**_discovery("reversal_1d/v1", "raw", 5), "holding_count": 30}
    grid.append_ledger(ledger, "discovery", extra, _result(p=1e-9, mean=0.01), recorded_at=AT)

    with pytest.raises(p6.StageIncompleteError, match="holds 1"):
        p6.survivors(ledger, SESSIONS)


# --- section 5: step 2a ---------------------------------------------------------------------------


COMPONENTS: Final = (
    ("momentum_20_sessions/v1", "raw"),
    ("book_to_price/v1", "neutralized"),
    ("revenue_yoy/v1", "processed"),
)


def test_the_composition_sources_are_the_protocols_19_configurations() -> None:
    configs = p6.composition_source_configs(COMPONENTS, SESSIONS, COMMIT)

    assert len(configs) == 19
    base = {
        "combine": "zscore_sum",
        "start": date(2017, 1, 3),
        "end": date(2021, 12, 31),
        "as_of": datetime(2026, 9, 26, 4, 0, tzinfo=UTC),
        "exchange": "SSE",
        "holding_count": 50,
        "rebalance_every_sessions": 20,
        "buffer_rank": None,
        "max_industry_weight": None,
    }
    static, clip, keep, *forests = configs
    assert static == {
        **base,
        "components": tuple((factor, tier, Decimal("1")) for factor, tier in COMPONENTS),
        "transform": "cross_section_standard/v1",
        "neutralization": "industry_and_size/v1",
    }
    trailing = {
        "components": COMPONENTS,
        "ic_window_sessions": 488,
        "min_ic_observations": 120,
        "ic_method": "spearman",
        "horizon_sessions": 20,
        "min_ic_securities": 100,
    }
    assert clip == {
        **base,
        "transform": "cross_section_standard/v1",
        "neutralization": "industry_and_size/v1",
        "trailing_ic": {**trailing, "negative_ic": "clip_to_zero"},
    }
    assert keep == {**clip, "trailing_ic": {**trailing, "negative_ic": "keep_sign"}}
    assert len(forests) == 16
    grids = set()
    for forest in forests:
        model = forest["walk_forward"]
        assert forest == {
            **base,
            "transform": None,
            "neutralization": None,
            "walk_forward": model,
        }
        assert model["family"] == "boosted_rank_trees"
        assert model["features"] == (
            "momentum_20_sessions/v1@raw",
            "book_to_price/v1@processed:cross_section_standard/v1",
            "revenue_yoy/v1@processed:cross_section_standard/v1",
        )
        assert {key: model[key] for key in model if key not in ("hyperparameters", "features")} == {
            "family": "boosted_rank_trees",
            "seed": 20_260_926,
            "code_commit": COMMIT,
            "train_sessions": 488,
            "refit_every_sessions": 122,
            "embargo_sessions": 20,
            "horizon_sessions": 20,
            "missing": "abstain",
        }
        hyper = model["hyperparameters"]
        grids.add(
            (
                hyper["tree_count"],
                hyper["max_depth"],
                hyper["learning_rate"],
                hyper["min_leaf_securities"],
            )
        )
    assert grids == {
        (trees, depth, rate, leaf)
        for trees in (50, 200)
        for depth in (2, 3)
        for rate in (0.05, 0.1)
        for leaf in (200, 1000)
    }


def test_every_protocol_configuration_resolves_through_the_real_request_resolvers() -> None:
    sources = p6.composition_source_configs(COMPONENTS, SESSIONS, COMMIT)
    strategies = p6.composition_strategy_configs(sources[-1])
    validation = p6.validation_configs(sources[:3], SESSIONS, COMMIT)
    for config in (*sources, *strategies, *validation):
        _resolve(config)
    for config in p6.discovery_configs(SESSIONS):
        _resolve({k: v for k, v in config.items() if k != "ic"})
        ((factor, tier, _),) = config["components"]
        strategy_view.ic_series_request(
            factor=factor,
            tier=tier,
            transform=config["transform"],
            neutralization=config["neutralization"],
            start=config["start"],
            end=config["end"],
            as_of=config["as_of"],
            exchange=config["exchange"],
            **config["ic"],
        )


# --- section 5: step 2b ---------------------------------------------------------------------------


def _composition_ledger(
    tmp_path: Path, components: Sequence[tuple[str, str]], special: Mapping[str, Any]
) -> tuple[Path, tuple[Mapping[str, object], ...]]:
    """A complete discovery stage whose survivors are `components`, then the 2a rows."""
    ledger = tmp_path / "ledger.jsonl"
    winners = {
        _id(_discovery(factor, tier, 5)): _result(p=1e-6, mean=0.002, ir=0.5)
        for factor, tier in components
    }
    _fill(ledger, "discovery", p6.discovery_configs(SESSIONS), winners)
    sources = p6.composition_source_configs(components, SESSIONS, COMMIT)
    _fill(ledger, "composition", sources, special)
    return ledger, sources


def test_step_2b_takes_the_best_source_and_does_not_run_the_step_2a_configuration_twice(
    tmp_path: Path,
) -> None:
    components = (("momentum_20_sessions/v1", "raw"),)
    sources = p6.composition_source_configs(components, SESSIONS, COMMIT)
    best, tied_higher_turnover = sources[4], sources[7]
    ledger, _ = _composition_ledger(
        tmp_path,
        components,
        {
            _id(best): _result(ir=0.9, turnover=0.2),
            _id(tied_higher_turnover): _result(ir=0.9, turnover=0.4),
            _id(sources[0]): _result(ir=0.3, turnover=0.01),
        },
    )
    sdk = _FakeSDK()

    run = p6.run_composition_strategies(
        ledger,
        SESSIONS,
        sdk.run_strategy_backtest,
        COMMIT,
        echo=lambda line: None,
        clock=lambda: AT,
        verify=_no_guard,
    )

    configs = p6.composition_strategy_configs(best)
    assert len(configs) == 36
    assert len({_id(config) for config in configs}) == 36
    assert [c for c in configs if _id(c) == _id(best)] == [best]
    assert (run.ran, run.skipped) == (35, 1)
    assert grid.stage_family(ledger, "composition") == 19 + 35
    assert all(call["walk_forward"] == best["walk_forward"] for call in sdk.backtests)
    rules = {
        (
            c["holding_count"],
            c["rebalance_every_sessions"],
            c["buffer_rank"],
            c["max_industry_weight"],
        )
        for c in configs
    }
    assert rules == {
        (holding, rebalance, buffer, cap)
        for holding, buffered in ((30, 45), (50, 75), (100, 150))
        for rebalance in (5, 10, 20)
        for buffer in (None, buffered)
        for cap in (None, Decimal("0.2"))
    }


def test_step_2b_refuses_to_resume_the_stage_at_another_commit(tmp_path: Path) -> None:
    """A walk-forward source names its code commit, so the same configuration at another commit is
    another hypothesis: the stage would count it twice."""
    ledger, _ = _composition_ledger(tmp_path, (("momentum_20_sessions/v1", "raw"),), {})

    with pytest.raises(p6.StageCommitError, match=COMMIT):
        p6.run_composition_strategies(
            ledger,
            SESSIONS,
            _FakeSDK().run_strategy_backtest,
            OTHER_COMMIT,
            echo=print,
            verify=_no_guard,
        )
    with pytest.raises(p6.StageCommitError, match="dirty"):
        p6.run_composition_strategies(
            ledger,
            SESSIONS,
            _FakeSDK().run_strategy_backtest,
            f"{COMMIT}-dirty",
            echo=print,
            verify=_no_guard,
        )


# --- section 5: finalists -------------------------------------------------------------------------


def _full_composition(
    tmp_path: Path, special: Callable[[list[Mapping[str, object]]], Mapping[str, Any]]
) -> tuple[Path, list[Mapping[str, object]]]:
    """Discovery + every stage-2 row: 19 sources (source 0 is the best, at IR 5) and the 35 step
    2b strategies, which `special` is handed and returns overriding rows for."""
    components = (("momentum_20_sessions/v1", "raw"),)
    sources = p6.composition_source_configs(components, SESSIONS, COMMIT)
    strategies = [
        c for c in p6.composition_strategy_configs(sources[0]) if _id(c) != _id(sources[0])
    ]
    rows = {_id(sources[0]): _result(ir=5.0), **special(strategies)}
    ledger, _ = _composition_ledger(tmp_path, components, rows)
    _fill(ledger, "composition", strategies, rows)
    return ledger, strategies


def test_the_finalists_are_the_best_five_passing_configurations_by_information_ratio(
    tmp_path: Path,
) -> None:
    def special(configs: list[Mapping[str, object]]) -> Mapping[str, Any]:
        passing = {_id(configs[k]): _result(p=1e-9, mean=0.002, ir=1.0 + k / 10) for k in range(6)}
        negative = {_id(configs[10]): _result(p=1e-9, mean=-0.002, ir=9.0)}
        weak = {_id(configs[11]): _result(p=0.5, mean=0.002, ir=8.0)}
        return {**passing, **negative, **weak}

    ledger, configs = _full_composition(tmp_path, special)

    answer, finalists = p6.finalists(ledger, SESSIONS)

    assert [_id(c) for c in finalists] == [_id(configs[k]) for k in (5, 4, 3, 2, 1)]
    assert answer["no_configuration_passed_multiple_testing"] is False
    assert answer["family_size"] == 54
    assert answer["fdr_table"]["family_size"] == 54


def test_fewer_than_five_passing_configurations_are_all_finalists(tmp_path: Path) -> None:
    def special(configs: list[Mapping[str, object]]) -> Mapping[str, Any]:
        return {_id(configs[20 + k]): _result(p=1e-9, mean=0.002, ir=float(k)) for k in range(2)}

    ledger, configs = _full_composition(tmp_path, special)

    _, finalists = p6.finalists(ledger, SESSIONS)

    assert [_id(c) for c in finalists] == [_id(configs[21]), _id(configs[20])]


def test_when_by_rejects_nothing_the_best_five_by_information_ratio_still_go_on_flagged(
    tmp_path: Path,
) -> None:
    def special(configs: list[Mapping[str, object]]) -> Mapping[str, Any]:
        return {_id(configs[10 + k]): _result(p=0.4, mean=-0.001, ir=6.0 + k) for k in range(6)}

    ledger, configs = _full_composition(tmp_path, special)

    answer, finalists = p6.finalists(ledger, SESSIONS)

    assert [_id(c) for c in finalists] == [_id(configs[10 + k]) for k in (5, 4, 3, 2, 1)]
    assert answer["no_configuration_passed_multiple_testing"] is True
    assert answer["note"] == "阶段 2 无配置通过多重检验"


def test_rejections_whose_means_are_all_negative_take_the_same_fallback(
    tmp_path: Path,
) -> None:
    """Section 5: "no configuration passes" is BY rejecting nothing **or** every rejected one
    having a mean <= 0. The best five by information ratio over every stage-2 configuration go
    on -- the rejected negative one included, since the fallback ranks all of them."""

    def special(configs: list[Mapping[str, object]]) -> Mapping[str, Any]:
        rejected = {_id(configs[12]): _result(p=1e-12, mean=-0.004, ir=20.0)}
        return {**rejected, **{_id(configs[k]): _result(ir=6.0 + k) for k in range(4)}}

    ledger, configs = _full_composition(tmp_path, special)

    answer, finalists = p6.finalists(ledger, SESSIONS)

    assert answer["fdr_table"]["discoveries"] == 1
    assert [_id(c) for c in finalists] == [_id(configs[k]) for k in (12, 3, 2, 1, 0)]
    assert answer["no_configuration_passed_multiple_testing"] is True
    assert answer["note"] == "阶段 2 无配置通过多重检验"


def test_the_finalist_cut_breaks_a_tie_by_the_lower_config_id(tmp_path: Path) -> None:
    def special(configs: list[Mapping[str, object]]) -> Mapping[str, Any]:
        passing = {_id(configs[k]): _result(p=1e-9, mean=0.002, ir=2.0 + k) for k in range(4)}
        tied = {_id(configs[k]): _result(p=1e-9, mean=0.002, ir=1.5) for k in (20, 21)}
        return {**passing, **tied}

    ledger, configs = _full_composition(tmp_path, special)

    _, finalists = p6.finalists(ledger, SESSIONS)

    lower = min(_id(configs[20]), _id(configs[21]))
    assert [_id(c) for c in finalists] == [*(_id(configs[k]) for k in (3, 2, 1, 0)), lower]


# --- section 6: validation ------------------------------------------------------------------------


def _validation_ledger(
    tmp_path: Path, results: Callable[[list[Mapping[str, object]]], Mapping[str, Any]]
) -> tuple[Path, list[Mapping[str, object]]]:
    def special(configs: list[Mapping[str, object]]) -> Mapping[str, Any]:
        return {_id(configs[k]): _result(p=1e-9, mean=0.002, ir=1.0 + k) for k in range(5)}

    ledger, _ = _full_composition(tmp_path, special)
    _, finalists = p6.finalists(ledger, SESSIONS)
    configs = list(p6.validation_configs(finalists, SESSIONS, COMMIT))
    _fill(ledger, "validation", configs, results(configs))
    return ledger, configs


def test_validation_chooses_the_best_information_ratio_and_breaks_a_tie_by_lower_turnover(
    tmp_path: Path,
) -> None:
    def results(configs: list[Mapping[str, object]]) -> Mapping[str, Any]:
        return {
            _id(configs[0]): _result(ir=0.7, turnover=0.9),
            _id(configs[1]): _result(ir=0.9, turnover=0.6),
            _id(configs[2]): _result(ir=0.9, turnover=0.4),
            _id(configs[3]): _result(ir=0.2, turnover=0.01),
        }

    ledger, configs = _validation_ledger(tmp_path, results)

    answer, chosen, row = p6.validation_selection(ledger, SESSIONS)

    assert chosen == configs[2]
    assert row.config_id == _id(configs[2])
    assert answer["chosen"] == _id(configs[2])
    assert len(answer["results"]) == 5
    assert configs[2]["start"] == date(2022, 1, 4)
    assert configs[2]["end"] == date(2023, 12, 29)


def test_validation_breaks_a_tie_on_both_by_the_lower_config_id(tmp_path: Path) -> None:
    def results(configs: list[Mapping[str, object]]) -> Mapping[str, Any]:
        return {_id(configs[k]): _result(ir=0.9, turnover=0.4) for k in (1, 3)}

    ledger, configs = _validation_ledger(tmp_path, results)

    answer, _, _ = p6.validation_selection(ledger, SESSIONS)

    assert answer["chosen"] == min(_id(configs[1]), _id(configs[3]))


# --- section 7: drawdown and the criteria ---------------------------------------------------------


def _holdout(
    *, p: float = 0.02, mean: float = 0.001, nets: Sequence[str] = ("0.3", "-0.2")
) -> dict[str, object]:
    """Relative levels 1.3 then 1.04 by default: a drawdown of exactly 0.2 and a compounded
    annual relative return of 1.04 ** (244 / 40) - 1 > 0."""
    return _result(p=p, mean=mean, nets=nets)


def _compounding_trap() -> dict[str, object]:
    """Eleven 20-session periods in which the benchmark doubles and the book gains 102%
    (excess +0.02, relative x1.01), then one in which the benchmark loses 90% and the book 91.1%
    (excess -0.011, relative 0.089 / 0.1 = x0.89) -- measured by `grid.strategy_result` itself."""
    nets = ["1.02"] * 11 + ["-0.911"]
    benches = ["1.0"] * 11 + ["-0.9"]
    return grid.strategy_result(
        _backtest(nets, benchmarks=benches, interval=20),
        excess_benchmark=EQUAL_WEIGHT_ALL_A_HELD,
    )


VALIDATION: Final = _result(nets=("0.1", "-0.1"))
"""Relative levels 1.1 then 0.99: a maximum relative drawdown of exactly 0.1."""
CRITERIA: Final = {
    "compounded_annualized_relative_return_above": "0",
    "one_sided_p_excess_below": "0.05",
    "max_relative_drawdown_multiple_of_validation": "2",
    "validation_config_id": "v" * 64,
    "validation_max_relative_drawdown": 0.1,
}


def test_the_holdout_passes_only_when_all_three_hold_and_the_drawdown_bound_is_inclusive() -> None:
    verdict = p6.evaluate_holdout(_holdout(), CRITERIA, VALIDATION)

    assert verdict["verdict"] == "通过"
    drawdown = verdict["criteria"]["max_relative_drawdown"]
    assert drawdown["value"] == pytest.approx(0.2)
    assert drawdown["threshold"] == pytest.approx(0.2)
    assert all(item["passed"] for item in verdict["criteria"].values())


@pytest.mark.parametrize(
    ("holdout", "failing"),
    [
        (_compounding_trap(), "compounded_annualized_relative_return"),
        (_holdout(p=0.1), "one_sided_p_excess"),
        (_holdout(nets=("0.3", "-0.21")), "max_relative_drawdown"),
    ],
)
def test_each_holdout_criterion_fails_the_verdict_on_its_own(
    holdout: dict[str, object], failing: str
) -> None:
    verdict = p6.evaluate_holdout(holdout, CRITERIA, VALIDATION)

    assert verdict["verdict"] == "不通过"
    assert {name for name, item in verdict["criteria"].items() if not item["passed"]} == {failing}


def test_the_compounded_return_fails_where_the_mean_excess_is_significantly_positive() -> None:
    """Section 7's criterion 1 is not implied by criterion 2 (version record, 2026-09-29).

    The arithmetic mean excess is (11 x 0.02 - 0.011) / 12 = 0.0174 > 0. Over twelve values the
    sign-flip test is exact: |sum| >= 0.209 needs all eleven +0.02 on one side (either sign on
    -0.011), 4 of 4096 patterns, so p = 4/4096 and the one-sided p is 2/4096 < 0.05. The relative
    level is 1.01 ** 11 x 0.89 = 0.9930 < 1, so the compounded annual relative return,
    0.9930 ** (244 / 240) - 1, is negative: the loss in the period the benchmark fell 90% is
    divided by 0.1, the gains in the periods it doubled by 2."""
    result = _compounding_trap()

    assert result["mean_net_excess"] == pytest.approx(0.209 / 12)
    assert result["p_excess"] == 4 / 4096
    assert grid.one_sided_p_value(result["p_excess"], result["mean_net_excess"]) < 0.05
    compounded = grid.result_compounded_annual_relative_return(result)
    assert compounded == pytest.approx((1.01**11 * 0.89) ** (244 / 240) - 1)
    assert compounded < 0
    verdict = p6.evaluate_holdout(result, CRITERIA, VALIDATION)
    assert verdict["criteria"]["compounded_annualized_relative_return"]["value"] == compounded


def test_a_compounded_relative_return_of_exactly_zero_does_not_pass() -> None:
    """Criterion 1 is strict: a book that matched its benchmark every period earned nothing."""
    level = _result(p=0.02, mean=0.001, nets=("0.3", "-0.2"), benches=("0.3", "-0.2"))

    verdict = p6.evaluate_holdout(level, CRITERIA, VALIDATION)

    item = verdict["criteria"]["compounded_annualized_relative_return"]
    assert (item["value"], item["passed"]) == (0.0, False)
    assert verdict["verdict"] == "不通过"


def test_the_one_sided_p_is_the_protocols_derivation_from_the_two_sided_one() -> None:
    """p = 0.08 two-sided with a positive mean is 0.04 one-sided (passes); with a negative mean it
    is 0.96 (fails) -- a two-sided reading would fail the first and pass nothing."""
    assert p6.evaluate_holdout(_holdout(p=0.08), CRITERIA, VALIDATION)["verdict"] == "通过"
    negative = p6.evaluate_holdout(_holdout(p=0.08, mean=-0.001), CRITERIA, VALIDATION)
    assert negative["criteria"]["one_sided_p_excess"]["value"] == pytest.approx(0.96)


def test_an_unmeasured_holdout_does_not_pass() -> None:
    verdict = p6.evaluate_holdout({"error": "StrategyRunBlockedError: x"}, CRITERIA, VALIDATION)
    assert verdict["verdict"] == "不通过"


def test_the_validation_drawdown_must_be_the_registered_one() -> None:
    with pytest.raises(p6.HoldoutEvaluationError):
        p6.evaluate_holdout(
            _holdout(), {**CRITERIA, "validation_max_relative_drawdown": 0.05}, VALIDATION
        )


# --- section 1: the precondition ------------------------------------------------------------------


def test_the_precondition_runs_the_protocols_command_line(tmp_path: Path) -> None:
    assert p6.precondition_argv(tmp_path) == (
        "factor",
        "stale-return-paths",
        "--runtime-dir",
        str(tmp_path),
        "--exchange",
        "SSE",
        "--max-staleness-days",
        "30",
    )
    seen: list[tuple[str, ...]] = []

    def runner(argv: Sequence[str]) -> p6.CommandOutcome:
        seen.append(tuple(argv))
        return p6.CommandOutcome(exit_code=0, stdout=CLEAN, stderr=BUDGET)

    p6.require_clean_return_paths(tmp_path, runner=runner)
    assert seen == [p6.precondition_argv(tmp_path)]


CLEAN: Final[str] = "stale return-path builds: none\n"
BUDGET: Final[str] = (
    "BUDGET stale-return-path-recompute 0 compute_factor calls (one per stored raw build ...)\n"
)


@pytest.mark.parametrize(
    ("exit_code", "stdout", "stderr"),
    [
        (1, "stale return-path builds: 1\nSTALE reversal_1d/v1 raw 2020 ...\n", BUDGET),
        (0, "stale return-path builds: 1\n", BUDGET),
        (1, CLEAN, BUDGET),
        (0, "none\n", BUDGET),
        (0, CLEAN + "stale return-path builds: none\n", BUDGET),
        (0, "\n" + CLEAN, BUDGET),
        (0, CLEAN + "\n", BUDGET),
        (0, CLEAN, ""),
        (0, CLEAN, "BUDGET stale-return-path-recompute many\n"),
        (2, "", "Usage: ... No such command 'stale-return-paths'."),
        (0, "", BUDGET),
    ],
)
def test_a_precondition_that_is_not_clean_is_refused_by_name(
    tmp_path: Path, exit_code: int, stdout: str, stderr: str
) -> None:
    def runner(argv: Sequence[str]) -> p6.CommandOutcome:
        return p6.CommandOutcome(exit_code=exit_code, stdout=stdout, stderr=stderr)

    with pytest.raises(p6.StaleReturnPathsError, match="stale-return-paths"):
        p6.require_clean_return_paths(tmp_path, runner=runner)


def test_the_default_runner_reports_a_command_the_cli_does_not_have() -> None:
    outcome = p6.run_openalpha(("no-such-command",))
    assert outcome.exit_code != 0


def _record_a_return_path_decision(root: Path, subject: str, session: date) -> None:
    """Record `subject`'s `session` as one whose return no witness decides, as the `stk_limit`
    target writes a `V2-P6-020` decision -- after the factor builds, so a build is now stale."""
    values: dict[str, object] = {
        "trade_date": session.isoformat(),
        "source_dataset": "stk_limit",
        "defect_kind": "pre_close_contradicts_adj_factor",
        "bar_close": 10.0,
        "valuation_close": 5.0,
        "previous_bar_close": 10.0,
        "up_limit": None,
        "down_limit": None,
        "valuation_repeats_previous_close": None,
        "list_date": None,
    }
    kinds = {"valuation_repeats_previous_close": "boolean"}
    at = datetime.combine(session, time(16, 0), ZoneInfo(DEFAULT_DATE_TIMEZONE))
    fetched = datetime(2026, 1, 20, tzinfo=UTC)
    write_upstream_defects(
        PanelStore(root / "panel"),
        ColumnarPanelBatch(
            provider_id="openalpha-cn/tests",
            dataset=UPSTREAM_DEFECTS_DATASET,
            kind=UPSTREAM_DEFECTS_DATASET,
            as_of=fetched,
            fetched_at=fetched,
            status="success",
            subjects=(subject,),
            timeline=TimelineColumns(
                event_time=(at - timedelta(hours=1),),
                available_time=(at,),
                ingested_time=(at,),
                revision_time=(at,),
            ),
            columns=tuple(
                PanelColumn(
                    name,
                    kinds.get(name, "string" if isinstance(values[name], str) else "float"),
                    (values[name],),
                )
                for name in UPSTREAM_DEFECT_DATA_COLUMNS
            ),
        ),
        year=session.year,
        source_datasets=frozenset({"stk_limit"}),
    )


def test_the_real_detector_admits_a_clean_store_and_refuses_one_with_a_stale_build(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`openalpha factor stale-return-paths` itself, in a child process, over a generated store:
    its raw `reversal_1d` builds are clean until a return-path decision is recorded inside one's
    window, which makes that build stale."""
    panel = write_strategy_corpus(tmp_path)
    monkeypatch.setattr(p6, "EXCHANGE", FIXTURE_EXCHANGE)

    p6.require_clean_return_paths(tmp_path)

    _record_a_return_path_decision(
        tmp_path, str(panel.batch("daily").subjects[0]), panel.sessions[5]
    )
    with pytest.raises(p6.StaleReturnPathsError, match="stale return-path builds: 1"):
        p6.require_clean_return_paths(tmp_path)


_FORCE_SLOW_QUERIES_BOOTSTRAP = """
import openalpha_cn.panel.store as store_module

_original_connect = store_module._connect


def _forced_connect(*args, **kwargs):
    # The real helper already ran (its own SET statements included) by the time this wrapper
    # sees the connection, so this does not race the fix's own setup the way patching
    # duckdb.connect itself would -- it only forces *later* queries on this connection to be
    # over DuckDB's default 2000ms progress_bar_time threshold, which is what "a query that
    # happens to run long" (V2-P6-021's actual trigger) looks like from the connection's own
    # point of view.
    connection = _original_connect(*args, **kwargs)
    connection.execute("SET progress_bar_time = 0")
    return connection


store_module._connect = _forced_connect

import sys

from openalpha_cn.cli import app

app(prog_name="openalpha")
"""
"""A subprocess bootstrap, not `p6._OPENALPHA`: it forces every query `PanelStore` runs after
this process starts to cross DuckDB's progress-bar threshold, which is how `V2-P6-021`'s bug
report was actually triggered (a query slow enough to pass `progress_bar_time`) rather than an
edge case. `run_openalpha`/`p6._OPENALPHA` runs the real CLI with no such forcing, so it cannot
tell a helper that disables the progress bar from one that does not -- both look clean when
every query finishes in milliseconds, which is also why the defect shipped unnoticed until it
met a slower real store.
"""


def test_the_stale_return_paths_precondition_stays_clean_stdout_when_duckdb_queries_run_slow(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`V2-P6-021`: reproduces the reported defect end to end -- a real `openalpha` subprocess,
    a real generated store, DuckDB's progress bar forced on for every query the precondition's
    connections run after they open -- and proves `panel/store.py::_connect` keeps stdout to
    exactly the one clean line `require_clean_return_paths` requires.

    This has to be a real subprocess captured the way `subprocess.run(capture_output=True)`
    captures it: DuckDB's progress bar is written straight to the process's stdout file
    descriptor, bypassing Python's `sys.stdout` object entirely, so `CliRunner.invoke(...)`
    (used throughout this test tree) never sees it either way and cannot exercise this guard --
    confirmed directly: a `CliRunner` invocation of a command that opens a DuckDB connection
    with `progress_bar_time=0` and runs a query shows the bar on the real terminal while
    `result.stdout` comes back clean, in-process, unconditionally.

    Mutation check performed while writing this test, not asserted here (asserting it would
    just re-derive `test_removing_the_helpers_config_turns_this_red` in
    `tests/unit/panel/test_store_progress_bar.py` through a much slower path): with
    `panel/store.py::_connect`'s two `SET enable_progress_bar...` calls removed, this exact test
    body produces `stale return-path builds: none` preceded by a page of `▕████...▏` progress-bar
    lines, and `require_clean_return_paths`'s own `stdout.splitlines() == [CLEAN_RETURN_PATHS]`
    check refuses it -- which is the real research driver's own gate, unmodified.
    """
    write_strategy_corpus(tmp_path)
    monkeypatch.setattr(p6, "EXCHANGE", FIXTURE_EXCHANGE)
    argv = p6.precondition_argv(tmp_path)

    completed = subprocess.run(
        [sys.executable, "-c", _FORCE_SLOW_QUERIES_BOOTSTRAP, *argv],
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.splitlines() == [p6.CLEAN_RETURN_PATHS], (
        f"stdout was not exactly the clean line -- likely progress-bar corruption: "
        f"{completed.stdout!r}"
    )


def test_a_json_command_reading_the_panel_store_stays_valid_json_when_duckdb_queries_run_slow(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The second half of `V2-P6-021`'s brief: "the same defect can corrupt every `--json`
    output of every command that reads the panel store", proven the same way -- a real
    subprocess, a real store, queries forced past the progress-bar threshold, `--json` stdout
    required to parse."""
    write_strategy_corpus(tmp_path)
    monkeypatch.setattr(p6, "EXCHANGE", FIXTURE_EXCHANGE)
    argv = [*p6.precondition_argv(tmp_path), "--json"]

    completed = subprocess.run(
        [sys.executable, "-c", _FORCE_SLOW_QUERIES_BOOTSTRAP, *argv],
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    body = json.loads(completed.stdout)  # raises if a progress bar landed in the stream
    assert body["stale"] == []


@pytest.mark.parametrize("command", [c for c in p6.COMMANDS if c != "holdout-verdict"])
def test_every_command_runs_the_precondition_first_and_stops_on_it(
    tmp_path: Path, command: str, capsys: pytest.CaptureFixture[str]
) -> None:
    touched: list[str] = []

    def refuse(runtime_dir: Path) -> None:
        raise p6.StaleReturnPathsError("`openalpha factor stale-return-paths` listed 3 builds")

    environment = p6.Environment(
        precondition=refuse,
        sessions=lambda runtime_dir: touched.append("sessions") or SESSIONS,  # type: ignore[func-returns-value]
        sdk=lambda runtime_dir: touched.append("sdk") or _FakeSDK(),  # type: ignore[func-returns-value]
        code_commit=lambda: touched.append("commit") or COMMIT,  # type: ignore[func-returns-value]
        repo=tmp_path / "checkout",
    )
    ledger = tmp_path / "ledger.jsonl"

    code = p6.main(
        [command, "--runtime-dir", str(tmp_path), "--ledger", str(ledger)], environment=environment
    )

    assert code == 1
    assert "StaleReturnPathsError" in capsys.readouterr().err
    # A ledger-writing command resolves its commit first (V2-P6-023); nothing else runs.
    assert touched == (["commit"] if command in p6.LEDGER_WRITING_COMMANDS else [])
    assert not ledger.exists()


# --- the whole chain, through the command line ----------------------------------------------------


def test_every_stage_runs_from_the_command_line_and_nothing_is_typed_by_hand(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q", "--template=")
    (repo / "README.md").write_text("research\n", encoding="utf-8")
    commit_file(repo, repo / "README.md", "initial", at=datetime(2026, 9, 26, 0, 0, tzinfo=UTC))
    research = repo / "scripts" / "research"
    monkeypatch.setattr(
        registry, "_imported_package", lambda: repo / "src" / "openalpha_cn" / "__init__.py"
    )
    monkeypatch.setattr(
        registry, "_imported_scripts", lambda: (research / "grid.py", research / "registry.py")
    )
    sdk = _FakeSDK()
    checks: list[Path] = []
    environment = p6.Environment(
        precondition=checks.append,
        sessions=lambda runtime_dir: SESSIONS,
        sdk=lambda runtime_dir: sdk,
        code_commit=lambda: head(repo),
        repo=repo,
    )
    runtime = tmp_path / "runtime"
    ledger = tmp_path / "research" / "ledger.jsonl"
    registration = repo / "docs" / "research" / "p6-registration.json"

    def run(command: str, *extra: str) -> str:
        code = p6.main(
            [command, "--runtime-dir", str(runtime), "--ledger", str(ledger), *extra],
            environment=environment,
        )
        captured = capsys.readouterr()
        assert code == 0, captured.err
        return captured.out

    out = run("discovery")
    assert "189/189" in out
    assert grid.stage_family(ledger, "discovery") == 189
    assert len(sdk.ic_calls) == 189
    assert "189 skipped" in run("discovery")  # resumable: nothing is measured twice

    run("survivors")
    survivors = json.loads((ledger.parent / "p6-survivors.json").read_text(encoding="utf-8"))
    assert survivors["family_size"] == 189
    assert survivors["fdr_table"]["family_size"] == 189

    run("composition-sources")
    assert grid.stage_family(ledger, "composition") == 19
    run("composition-strategies")
    assert grid.stage_family(ledger, "composition") == 54

    run("finalists")
    finalists = json.loads((ledger.parent / "p6-finalists.json").read_text(encoding="utf-8"))
    assert len(finalists["finalists"]) == len(set(finalists["finalists"])) <= 5

    run("validation")
    assert grid.stage_family(ledger, "validation") == len(finalists["finalists"])
    validation = json.loads((ledger.parent / "p6-validation.json").read_text(encoding="utf-8"))

    run("register", "--registration", str(registration))
    body = json.loads(registration.read_text(encoding="utf-8"))
    assert body["config"]["start"] == "2024-01-02"
    assert body["config"]["end"] == "2026-09-24"
    assert body["criteria"]["validation_config_id"] == validation["chosen"]
    assert grid.stage_family(ledger, "holdout") == 0  # registering never runs the holdout
    commit_file(repo, registration, "register", at=datetime(2026, 9, 27, 0, 0, tzinfo=UTC))

    real_run_holdout = registry.run_holdout

    def crash_after_the_measurement(*args: Any, **kwargs: Any) -> Any:
        real_run_holdout(*args, **kwargs)
        raise RuntimeError("the process died after the measurement row was written")

    monkeypatch.setattr(registry, "run_holdout", crash_after_the_measurement)
    holdout = ["--runtime-dir", str(runtime), "--ledger", str(ledger)]
    holdout += ["--registration", str(registration), "--repo", str(repo)]
    with pytest.raises(RuntimeError, match="died"):
        p6.main(["holdout", *holdout], environment=environment)
    monkeypatch.setattr(registry, "run_holdout", real_run_holdout)
    assert not (ledger.parent / "p6-holdout-verdict.json").exists()
    assert p6.main(["holdout", *holdout], environment=environment) == 1
    assert "HoldoutAlreadyRanError" in capsys.readouterr().err  # it ran once

    out = run("holdout-verdict", "--registration", str(registration))
    verdict = json.loads((ledger.parent / "p6-holdout-verdict.json").read_text(encoding="utf-8"))
    assert verdict["verdict"] in ("通过", "不通过")
    assert verdict["verdict"] in out
    rows = [row for row in grid.read_ledger(ledger) if row.stage == "holdout"]
    assert [row.kind for row in rows] == ["holdout_claim", "measurement"]
    validation_row = next(
        row for row in grid.read_ledger(ledger) if row.config_id == validation["chosen"]
    )
    computed = p6.evaluate_holdout(rows[1].result, body["criteria"], validation_row.result)
    assert (verdict["verdict"], verdict["criteria"]) == (
        computed["verdict"],
        grid.to_json_value(computed["criteria"]),
    )
    assert len(checks) == 10  # every command but the read-only `holdout-verdict`


# --- review round 1: one commit through composition and validation ------------------------------


def _static_finalists(configs: list[Mapping[str, object]]) -> Mapping[str, Any]:
    """Five step-2b strategies of the static source ahead of every other row: finalists that name
    no commit, so only the stage-commit rule -- not `_rewindowed` -- can stop them moving."""
    return {_id(configs[k]): _result(ir=9.0 + k) for k in range(5)}


def test_validation_refuses_to_run_at_a_commit_other_than_the_compositions(tmp_path: Path) -> None:
    """The finalists were measured at the composition commit; a validation run at another commit
    would validate other code under the finalists' names -- even a static source, which names no
    commit of its own."""
    ledger, _ = _full_composition(tmp_path, _static_finalists)
    _, finalists = p6.finalists(ledger, SESSIONS)
    assert not any("walk_forward" in config for config in finalists)

    with pytest.raises(p6.StageCommitError, match=COMMIT):
        p6.run_validation(
            ledger,
            SESSIONS,
            _FakeSDK().run_strategy_backtest,
            OTHER_COMMIT,
            echo=print,
            verify=_no_guard,
        )
    assert grid.stage_family(ledger, "validation") == 0


def test_the_validation_choice_refuses_rows_measured_at_another_commit(tmp_path: Path) -> None:
    ledger, _ = _full_composition(tmp_path, _static_finalists)
    _, finalists = p6.finalists(ledger, SESSIONS)
    assert not any("walk_forward" in config for config in finalists)
    configs = p6.validation_configs(finalists, SESSIONS, COMMIT)
    elsewhere = {**_result(), "code_commit": OTHER_COMMIT}
    _fill(ledger, "validation", configs, {_id(c): elsewhere for c in configs})

    with pytest.raises(p6.StageCommitError, match=OTHER_COMMIT):
        p6.validation_selection(ledger, SESSIONS)


def test_a_walk_forward_commit_is_carried_into_a_later_window_and_never_rewritten() -> None:
    forest = p6.composition_source_configs(COMPONENTS, SESSIONS, COMMIT)[-1]

    (moved,) = p6.validation_configs([forest], SESSIONS, COMMIT)
    assert moved["walk_forward"] == forest["walk_forward"]
    assert p6.holdout_config(forest, SESSIONS, COMMIT)["walk_forward"] == forest["walk_forward"]
    with pytest.raises(p6.StageCommitError, match=OTHER_COMMIT):
        p6.validation_configs([forest], SESSIONS, OTHER_COMMIT)


# --- review round 1: the registration is bound to the validation commit -------------------------


@pytest.fixture
def validated_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path, str]:
    """A repository holding a file under each bound path, committed at V; a ledger whose every
    stage was measured at V; and V itself."""
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q", "--template=")
    for name in (
        "src/openalpha_cn/strategy.py",
        "scripts/research/p6.py",
        "pyproject.toml",
        "uv.lock",
    ):
        (repo / name).parent.mkdir(parents=True, exist_ok=True)
        (repo / name).write_text("RULES = 1\n", encoding="utf-8")
        git(repo, "add", name)
    git(repo, "commit", "-q", "-m", "validated", at=datetime(2026, 9, 26, 0, 0, tzinfo=UTC))
    validated = head(repo)
    monkeypatch.setattr(sys.modules[__name__], "COMMIT", validated)
    research = repo / "scripts" / "research"
    monkeypatch.setattr(
        registry, "_imported_package", lambda: repo / "src" / "openalpha_cn" / "__init__.py"
    )
    monkeypatch.setattr(
        registry, "_imported_scripts", lambda: (research / "grid.py", research / "registry.py")
    )

    def results(configs: list[Mapping[str, object]]) -> Mapping[str, Any]:
        return {_id(configs[1]): _result(ir=0.9, turnover=0.4)}

    ledger, _ = _validation_ledger(tmp_path / "research", results)
    return repo, ledger, validated


def _commit(repo: Path, name: str, text: str, *, at: datetime) -> str:
    (repo / name).parent.mkdir(parents=True, exist_ok=True)
    (repo / name).write_text(text, encoding="utf-8")
    commit_file(repo, repo / name, f"change {name}", at=at)
    return head(repo)


def test_a_registration_after_a_documentation_commit_keeps_the_validated_code(
    validated_repo: tuple[Path, Path, str],
) -> None:
    """The bound code (`registry.REGISTERED_PATHS`, which holds `scripts/research/p6.py`) is
    unchanged from the validation commit V to this checkout R, so R may register; the configuration
    keeps V, the commit it was validated at."""
    repo, ledger, validated = validated_repo
    later = _commit(repo, "docs/notes.md", "results\n", at=datetime(2026, 9, 27, tzinfo=UTC))
    registration = repo / "docs" / "research" / "p6-registration.json"

    p6.register_holdout(ledger, SESSIONS, registration, later, repo)

    body = json.loads(registration.read_text(encoding="utf-8"))
    assert body["code_commit"] == later
    _, chosen, row = p6.validation_selection(ledger, SESSIONS)
    assert body["config_id"] == _id(p6.holdout_config(chosen, SESSIONS, validated))
    assert body["criteria"]["validation_config_id"] == row.config_id


def test_a_registration_names_the_commit_that_is_checked_out(
    validated_repo: tuple[Path, Path, str],
) -> None:
    """The registration records the commit it was written from; naming V while R is checked out
    would record a commit whose files were not the ones checked."""
    repo, ledger, validated = validated_repo
    _commit(repo, "docs/notes.md", "results\n", at=datetime(2026, 9, 27, tzinfo=UTC))
    registration = repo / "docs" / "research" / "p6-registration.json"

    with pytest.raises(p6.StageCommitError, match="checked-out"):
        p6.register_holdout(ledger, SESSIONS, registration, validated, repo)
    assert not registration.exists()


@pytest.mark.parametrize("where", ["committed", "working tree"])
def test_a_registration_after_the_bound_code_changed_is_refused(
    validated_repo: tuple[Path, Path, str], where: str
) -> None:
    repo, ledger, _ = validated_repo
    at = datetime(2026, 9, 27, tzinfo=UTC)
    if where == "committed":
        later = _commit(repo, "scripts/research/p6.py", "RULES = 2\n", at=at)
    else:
        later = head(repo)
        (repo / "src" / "openalpha_cn" / "strategy.py").write_text("RULES = 2\n", encoding="utf-8")
    registration = repo / "docs" / "research" / "p6-registration.json"

    with pytest.raises(registry.SourceChangedError):
        p6.register_holdout(ledger, SESSIONS, registration, later, repo)
    assert not registration.exists()


# --- review round 1: the holdout's checks come first, and its verdict is recoverable ------------


def _registered(repo: Path, ledger: Path) -> Path:
    registration = repo / "docs" / "research" / "p6-registration.json"
    p6.register_holdout(ledger, SESSIONS, registration, head(repo), repo)
    commit_file(repo, registration, "register", at=datetime(2026, 9, 27, tzinfo=UTC))
    return registration


@pytest.mark.parametrize(
    "tampered",
    [
        {"validation_config_id": "0" * 64},
        {"validation_max_relative_drawdown": 0.5},
        {"compounded_annualized_relative_return_above": "zero"},
    ],
)
def test_a_binding_the_ledger_contradicts_is_refused_before_the_holdout_is_claimed(
    validated_repo: tuple[Path, Path, str], tampered: dict[str, object]
) -> None:
    repo, ledger, _ = validated_repo
    registration = _registered(repo, ledger)
    body = json.loads(registration.read_text(encoding="utf-8"))
    body["criteria"].update(tampered)
    registration.write_text(json.dumps(body, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    commit_file(repo, registration, "tamper", at=datetime(2026, 9, 28, tzinfo=UTC))

    with pytest.raises(p6.HoldoutEvaluationError):
        p6.run_holdout_stage(ledger, SESSIONS, registration, repo, _FakeSDK().run_strategy_backtest)
    assert grid.stage_family(ledger, "holdout") == 0
    assert not [row for row in grid.read_ledger(ledger) if row.stage == "holdout"]


def test_a_claim_with_no_measurement_is_a_fail_and_says_so(
    validated_repo: tuple[Path, Path, str],
) -> None:
    """A run that claimed the holdout and died is still the one run (section 7), and nothing it
    could have measured passes."""
    repo, ledger, _ = validated_repo
    registration = _registered(repo, ledger)
    body = json.loads(registration.read_text(encoding="utf-8"))
    binding = {
        "registration_sha256": hashlib.sha256(registration.read_bytes()).hexdigest(),
        "registration_commit": head(repo),
    }
    grid._append_holdout(ledger, "holdout_claim", body["config"], binding, recorded_at=AT)

    verdict = p6.holdout_verdict(ledger, SESSIONS, registration)

    assert verdict["verdict"] == "不通过"
    assert "claim" in verdict["statement"]
    assert not any(item["passed"] for item in verdict["criteria"].values())


@pytest.mark.parametrize("foreign", ["registration", "configuration"])
def test_the_verdict_path_refuses_holdout_rows_that_are_not_this_registrations(
    validated_repo: tuple[Path, Path, str], foreign: str
) -> None:
    repo, ledger, _ = validated_repo
    registration = _registered(repo, ledger)
    body = json.loads(registration.read_text(encoding="utf-8"))
    digest = hashlib.sha256(registration.read_bytes()).hexdigest()
    config = body["config"]
    if foreign == "registration":
        digest = "f" * 64
    else:
        config = {**config, "holding_count": config["holding_count"] + 1}
    binding = {"registration_sha256": digest, "registration_commit": head(repo)}
    grid._append_holdout(ledger, "holdout_claim", config, binding, recorded_at=AT)

    with pytest.raises(p6.HoldoutEvaluationError, match=r"another|not the registered"):
        p6.holdout_verdict(ledger, SESSIONS, registration)


def test_the_holdout_runs_once_after_its_checks_and_is_judged_by_the_verdict_path(
    validated_repo: tuple[Path, Path, str],
) -> None:
    repo, ledger, _ = validated_repo
    registration = _registered(repo, ledger)

    verdict = p6.run_holdout_stage(
        ledger, SESSIONS, registration, repo, _FakeSDK().run_strategy_backtest
    )

    assert verdict == p6.holdout_verdict(ledger, SESSIONS, registration)
    assert verdict["statement"].startswith("measured once")
    kinds = [row.kind for row in grid.read_ledger(ledger) if row.stage == "holdout"]
    assert kinds == ["holdout_claim", "measurement"]


def test_the_verdict_path_refuses_a_holdout_that_has_not_run(
    validated_repo: tuple[Path, Path, str],
) -> None:
    repo, ledger, _ = validated_repo
    registration = _registered(repo, ledger)

    with pytest.raises(p6.HoldoutEvaluationError, match="has not run"):
        p6.holdout_verdict(ledger, SESSIONS, registration)


# --- review round 1: the secondary family, duplicates, commits -----------------------------------


def test_the_secondary_family_is_controlled_over_the_whole_stage_and_reported_only(
    tmp_path: Path,
) -> None:
    configs = p6.discovery_configs(SESSIONS)
    rows: dict[str, Mapping[str, object]] = {}
    for index, config in enumerate(configs):
        if index % 3 == 0:
            rows[_id(config)] = {**_result(), "ic_error": "StrategyRunBlockedError: no build"}
        else:
            rows[_id(config)] = {**_result(), "p_ic": 1e-9 if index == 1 else 0.5}
    ledger = tmp_path / "ledger.jsonl"
    _fill(ledger, "discovery", configs, rows)

    answer = p6.survivors(ledger, SESSIONS)

    table = answer["ic_fdr_table"]
    assert table == grid.fdr_table(ledger, "discovery", 0.10, p_value_key="p_ic").model_dump(
        mode="json"
    )
    assert table["family_size"] == 189
    assert table["withheld_hypotheses"] == 63
    assert table["discoveries"] == 1
    assert answer["fallback"] is True  # a p_ic discovery selects nothing


def test_a_stage_with_no_ic_p_value_reports_the_secondary_family_as_absent(tmp_path: Path) -> None:
    configs = p6.discovery_configs(SESSIONS)
    refused = REFUSED_ROW
    special = {
        _id(configs[0]): {**_result(p=0.01), "ic_error": "no build"},
        _id(configs[1]): refused,
    }
    answer = p6.survivors(_survivor_ledger(tmp_path, special), SESSIONS)

    assert answer["ic_fdr_table"] is None
    assert answer["ic_fdr_note"] == (
        "次家族 189 个假设，0 个有 p_ic（1 行 ic_error、1 行 error），§3/§8 次家族 FDR 表缺失"
    )


def test_the_survivors_command_warns_when_the_secondary_family_is_incomplete(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    configs = p6.discovery_configs(SESSIONS)
    rows = {
        _id(config): (
            {**_result(), "ic_error": "no build"} if index < 3 else {**_result(), "p_ic": 0.5}
        )
        for index, config in enumerate(configs)
    }
    ledger = tmp_path / "research" / "ledger.jsonl"
    _fill(ledger, "discovery", configs, rows)
    environment = p6.Environment(
        precondition=lambda runtime_dir: None,
        sessions=lambda runtime_dir: SESSIONS,
        sdk=lambda runtime_dir: _FakeSDK(),
        code_commit=lambda: COMMIT,
        repo=tmp_path,
    )

    code = p6.main(
        ["survivors", "--runtime-dir", str(tmp_path), "--ledger", str(ledger)],
        environment=environment,
    )

    out = capsys.readouterr().out
    assert code == 0
    note = (
        "次家族 189 个假设，186 个有 p_ic（3 行 ic_error、0 行 error），"
        "3 个假设在次家族 FDR 表中 withheld"
    )
    assert f"warning: {note}" in out
    assert json.loads((ledger.parent / "p6-survivors.json").read_text())["ic_fdr_note"] == note


def test_a_discovery_stage_that_measured_nothing_is_refused_not_taken_as_no_survivor(
    tmp_path: Path,
) -> None:
    """The fallback is for a stage that was measured and found nothing; 189 refusals measured
    nothing, and there is no p-value to control."""
    configs = p6.discovery_configs(SESSIONS)
    ledger = tmp_path / "ledger.jsonl"
    _fill(ledger, "discovery", configs, {_id(c): REFUSED_ROW for c in configs})

    with pytest.raises(grid.ResearchLedgerError, match="p_excess"):
        p6.survivors(ledger, SESSIONS)


def test_a_candidate_without_an_information_ratio_is_refused_rather_than_placed(
    tmp_path: Path,
) -> None:
    config = _discovery("amihud_60/v1", "raw", 5)
    ledger = _survivor_ledger(tmp_path, {_id(config): _result(p=1e-9, mean=0.01, ir=None)})

    with pytest.raises(p6.UnrankableRowError, match="information ratio None"):
        p6.survivors(ledger, SESSIONS)


def test_a_duplicated_ledger_row_is_refused_by_name(tmp_path: Path) -> None:
    ledger = _survivor_ledger(tmp_path, {})
    first = ledger.read_text(encoding="utf-8").splitlines()[0]
    with ledger.open("a", encoding="utf-8") as handle:
        handle.write(first + "\n")

    with pytest.raises(p6.DuplicateRowError, match="twice"):
        p6.survivors(ledger, SESSIONS)


def test_the_survivors_report_names_every_commit_the_discovery_rows_ran_at(tmp_path: Path) -> None:
    config = _discovery("reversal_1d/v1", "raw", 1)
    ledger = _survivor_ledger(tmp_path, {_id(config): {**_result(), "code_commit": OTHER_COMMIT}})

    assert p6.survivors(ledger, SESSIONS)["code_commits"] == sorted((COMMIT, OTHER_COMMIT))


def test_a_stage_commit_ignores_a_row_that_names_none_but_not_a_stage_of_only_those(
    tmp_path: Path,
) -> None:
    ledger = tmp_path / "ledger.jsonl"
    window = {"start": date(2017, 1, 3), "end": date(2021, 12, 31)}
    grid.append_ledger(ledger, "composition", {"i": 0, **window}, _result(), recorded_at=AT)
    grid.append_ledger(ledger, "composition", {"i": 1, **window}, {"error": "x"}, recorded_at=AT)
    grid.append_ledger(ledger, "validation", {"i": 0}, {"error": "x"}, recorded_at=AT)

    assert p6.stage_commit(ledger, "composition") == COMMIT
    with pytest.raises(p6.StageCommitError, match="no row"):
        p6.stage_commit(ledger, "validation")


def test_a_row_the_window_refuses_carries_the_stages_commit(tmp_path: Path) -> None:
    ledger = tmp_path / "ledger.jsonl"
    reaching_2022 = {**_discovery("reversal_1d/v1", "raw", 1), "end": date(2021, 12, 31)}

    p6._run(
        ledger,
        "discovery",
        (reaching_2022,),
        lambda config: {"p_excess": 0.5},
        label_sessions=p6.discovery_label_sessions,
        sessions=SESSIONS,
        echo=lambda line: None,
        clock=lambda: AT,
        code_commit=COMMIT,
    )

    (row,) = grid.read_ledger(ledger)
    assert row.result["error"].startswith("StageWindowError: ")
    assert row.result["code_commit"] == COMMIT
    assert row.result["excess_benchmark"] == grid.PRIMARY_EXCESS_BENCHMARK  # V2-P6-024 m1


# --- re-review: the imported code, the read-only verdict, the missing claim ----------------------

WRITING: Final[tuple[str, ...]] = (
    "discovery",
    "composition-sources",
    "composition-strategies",
    "validation",
    "register",
)


@pytest.mark.parametrize("command", WRITING)
def test_a_ledger_writing_command_refuses_code_imported_from_another_checkout(
    tmp_path: Path, command: str, capsys: pytest.CaptureFixture[str]
) -> None:
    """`code_commit` names this checkout; a stage measured with `openalpha_cn` or the research
    scripts imported from another (a worktree's editable install points at the main checkout's
    `src`) would record a commit that is not the code that ran."""
    touched: list[str] = []
    environment = p6.Environment(
        precondition=lambda runtime_dir: None,
        sessions=lambda runtime_dir: touched.append("sessions") or SESSIONS,  # type: ignore[func-returns-value]
        sdk=lambda runtime_dir: touched.append("sdk") or _FakeSDK(),  # type: ignore[func-returns-value]
        code_commit=lambda: touched.append("commit") or COMMIT,  # type: ignore[func-returns-value]
        repo=tmp_path / "checkout",
    )
    ledger = tmp_path / "ledger.jsonl"

    code = p6.main(
        [command, "--runtime-dir", str(tmp_path), "--ledger", str(ledger)], environment=environment
    )

    assert code == 1
    assert "ForeignPackageError" in capsys.readouterr().err
    assert touched == ["commit"]  # resolved before any check (V2-P6-023), and nothing else
    assert not ledger.exists()


def test_research_scripts_imported_from_another_checkout_are_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(
        registry,
        "_imported_package",
        lambda: tmp_path / "checkout" / "src" / "openalpha_cn" / "__init__.py",
    )
    environment = p6.Environment(
        precondition=lambda runtime_dir: None,
        sessions=lambda runtime_dir: SESSIONS,
        sdk=lambda runtime_dir: _FakeSDK(),
        code_commit=lambda: COMMIT,
        repo=tmp_path / "checkout",
    )
    ledger = tmp_path / "ledger.jsonl"

    code = p6.main(
        ["discovery", "--runtime-dir", str(tmp_path), "--ledger", str(ledger)],
        environment=environment,
    )

    assert code == 1
    assert "ForeignScriptsError" in capsys.readouterr().err
    assert not ledger.exists()


def test_the_read_only_verdict_does_not_depend_on_the_panel_staying_fresh(
    validated_repo: tuple[Path, Path, str], capsys: pytest.CaptureFixture[str]
) -> None:
    """Recovering a verdict reads the ledger and the registration only; a panel that went stale
    after the holdout ran must not stop the one run's verdict from being read."""
    repo, ledger, _ = validated_repo
    registration = _registered(repo, ledger)
    p6.run_holdout_stage(ledger, SESSIONS, registration, repo, _FakeSDK().run_strategy_backtest)

    def stale(runtime_dir: Path) -> None:
        raise p6.StaleReturnPathsError("stale return-path builds: 4")

    environment = p6.Environment(
        precondition=stale,
        sessions=lambda runtime_dir: SESSIONS,
        sdk=lambda runtime_dir: _FakeSDK(),
        code_commit=lambda: COMMIT,
        repo=repo,
    )
    arguments = ["holdout-verdict", "--runtime-dir", str(repo), "--ledger", str(ledger)]

    code = p6.main([*arguments, "--registration", str(registration)], environment=environment)

    assert code == 0, capsys.readouterr().err
    assert (ledger.parent / "p6-holdout-verdict.json").exists()


def test_holdout_rows_with_no_claim_are_refused_by_name(
    validated_repo: tuple[Path, Path, str],
) -> None:
    """`grid` writes a measurement only after its claim, so a ledger with one and not the other
    was edited by hand."""
    repo, ledger, _ = validated_repo
    registration = _registered(repo, ledger)
    body = json.loads(registration.read_text(encoding="utf-8"))
    row = {
        "schema": grid.LEDGER_SCHEMA,
        "stage": "holdout",
        "kind": "measurement",
        "config_id": body["config_id"],
        "config": body["config"],
        "result": {
            "registration_sha256": hashlib.sha256(registration.read_bytes()).hexdigest(),
            "registration_commit": head(repo),
        },
        "recorded_at": AT.isoformat(),
    }
    with ledger.open("a", encoding="utf-8") as handle:
        handle.write(grid.canonical_json(row) + "\n")

    with pytest.raises(p6.HoldoutClaimMissingError, match="no claim"):
        p6.holdout_verdict(ledger, SESSIONS, registration)


# --- V2-P6-023: the measuring stages in worker processes ------------------------------------------

WORKER_COMMANDS: Final[tuple[str, ...]] = (
    "discovery",
    "composition-sources",
    "composition-strategies",
    "validation",
)
SLOW: Final[int] = 49
"""A holding count the pooled fake answers only once a `LATE` configuration has finished, when it
runs in a worker process: an earlier configuration that finishes after a later one."""
LATE: Final[int] = 46
REFUSED: Final[int] = 48
"""A holding count the pooled fake's book refuses: a refused row the measure records."""
CRASH: Final[int] = 47
"""A holding count the pooled fake crashes on: an error that is not a refusal."""
KILLED: Final[int] = 45
"""A holding count on which a worker process dies outright, as an out-of-memory kill would."""
STALL: Final[int] = 44
"""A holding count a worker process measures for a minute, after marking `stalled` in the
runtime directory: a measurement an interrupt must not wait for."""
STALL_SECONDS: Final[float] = 60.0
LATE_WAIT_SECONDS: Final[float] = 30.0


def _read_calls(log: Path) -> list[dict[str, Any]]:
    """The complete lines of a call log other processes may be appending to right now: a last
    line without its newline is a write still in progress, not a torn record."""
    if not log.exists():
        return []
    text = log.read_text(encoding="utf-8")
    return [json.loads(line) for line in text.split("\n")[:-1] if line]


class _PooledSDK(_FakeSDK):
    """`_FakeSDK`, built in each worker process by `_pooled_sdk`, logging every backtest it
    answers to `calls.jsonl` in the runtime directory -- the process, the thread pins it ran
    under -- in the order they finished."""

    def __init__(self, runtime_dir: Path) -> None:
        super().__init__()
        self.log = runtime_dir / "calls.jsonl"

    def _wait_for_a_late_configuration(self) -> None:
        deadline = monotonic() + LATE_WAIT_SECONDS
        while all(entry["holding_count"] != LATE for entry in _read_calls(self.log)):
            if monotonic() > deadline:
                raise RuntimeError(
                    f"the SLOW configuration waited {LATE_WAIT_SECONDS:.0f} s for a LATE one to "
                    "finish in another worker, and none did: the pool is not measuring in parallel"
                )
            sleep(0.05)

    def run_strategy_backtest(self, **config: Any) -> _Backtest:
        holding = config["holding_count"]
        in_worker = multiprocessing.parent_process() is not None
        if holding == KILLED and in_worker:
            os._exit(1)
        if holding == STALL and in_worker:
            (self.log.parent / "stalled").touch()
            sleep(STALL_SECONDS)
        try:
            if holding == SLOW and in_worker:
                self._wait_for_a_late_configuration()
            if holding == CRASH:
                raise RuntimeError("a bug in the worker, not a refusal")
            if holding == REFUSED:
                raise StrategyBacktestError("the fake book refuses it")
            return super().run_strategy_backtest(**config)
        finally:
            entry = {
                "config_id": grid.config_id(config),
                "holding_count": holding,
                "pid": os.getpid(),
                "pins": {name: os.environ.get(name) for name in thread_count_pins()},
                "sigint_ignored": signal.getsignal(signal.SIGINT) == signal.SIG_IGN,
            }
            with self.log.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(entry) + "\n")


def _pooled_sdk(runtime_dir: Path) -> _PooledSDK:
    """The picklable, module-level factory a worker process builds its SDK with."""
    return _PooledSDK(runtime_dir)


def _unbuildable_sdk(runtime_dir: Path) -> _PooledSDK:
    """A factory that builds in this process and fails in every worker process."""
    if multiprocessing.parent_process() is not None:
        raise RuntimeError("this worker cannot open the store")
    return _PooledSDK(runtime_dir)


def _strategy_id(config: Mapping[str, object]) -> str:
    """The identity of the backtest request a discovery configuration makes."""
    return _id({key: value for key, value in config.items() if key != "ic"})


def _pooled_configs(*holdings: int) -> tuple[dict[str, Any], ...]:
    """The first discovery configurations, with the holding counts that steer the pooled fake."""
    return tuple(
        {**config, "holding_count": holding}
        for config, holding in zip(p6.discovery_configs(SESSIONS), holdings, strict=False)
    )


def _pooled_world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, at: datetime) -> p6.Environment:
    research = tmp_path / "checkout" / "scripts" / "research"
    monkeypatch.setattr(
        registry,
        "_imported_package",
        lambda: tmp_path / "checkout" / "src" / "openalpha_cn" / "__init__.py",
    )
    monkeypatch.setattr(
        registry, "_imported_scripts", lambda: (research / "grid.py", research / "registry.py")
    )
    return p6.Environment(
        precondition=lambda runtime_dir: None,
        sessions=lambda runtime_dir: SESSIONS,
        sdk=_pooled_sdk,
        code_commit=lambda: COMMIT,
        repo=tmp_path / "checkout",
        clock=lambda: at,
    )


def _discover(environment: p6.Environment, runtime: Path, ledger: Path, workers: int) -> int:
    runtime.mkdir(parents=True, exist_ok=True)
    arguments = ["discovery", "--runtime-dir", str(runtime), "--ledger", str(ledger)]
    return p6.main([*arguments, "--workers", str(workers)], environment=environment)


def _calls(runtime: Path) -> list[dict[str, Any]]:
    return _read_calls(runtime / "calls.jsonl")


def _rows_without_time(ledger: Path) -> list[dict[str, Any]]:
    return [
        {key: value for key, value in json.loads(line).items() if key != "recorded_at"}
        for line in ledger.read_text(encoding="utf-8").splitlines()
    ]


def _no_worker_left() -> bool:
    """No process this one started is still alive. `active_children` is the pool's own record
    of its workers (they are `multiprocessing` processes of this one) and joins any that have
    exited, on every platform -- unlike probing a pid with signal 0, which on Windows
    sends Ctrl-C to the console."""
    return not multiprocessing.active_children()


def _workers_alive_after_measuring(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """How many child processes were alive when each pooled run finished measuring, before its
    pool shut down: the proof that `_no_worker_left` afterwards observed real workers."""
    alive: list[int] = []
    measured = grid.run_grid_in_pool

    def counting(*args: Any, **kwargs: Any) -> Any:
        try:
            return measured(*args, **kwargs)
        finally:
            alive.append(len(multiprocessing.active_children()))

    monkeypatch.setattr(grid, "run_grid_in_pool", counting)
    return alive


def _cores(monkeypatch: pytest.MonkeyPatch, count: int = 8) -> None:
    """The machine's core count, which caps `--workers`, fixed so the tests do not depend on
    the host."""
    monkeypatch.setattr(os, "cpu_count", lambda: count)


def test_a_stage_measured_by_worker_processes_writes_the_serial_runs_ledger(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The rows `--workers 3` writes are the rows `--workers 1` writes -- the same
    configurations, in configuration order, the same results value for value, a refused row and a
    window-refused row included -- though the first configuration finished after a later one;
    only `recorded_at` differs. Each worker ran with the thread counts pinned; this process's
    environment is left as it was."""
    ignored_at_start = signal.getsignal(signal.SIGINT) == signal.SIG_IGN
    configs = list(_pooled_configs(SLOW, 50, REFUSED, LATE, 50, 50))
    configs[4] = {**configs[4], "end": date(2021, 12, 31)}  # its IC label leaves the stage
    monkeypatch.setattr(p6, "discovery_configs", lambda sessions: tuple(configs))
    for name in thread_count_pins():
        monkeypatch.delenv(name, raising=False)
    _cores(monkeypatch)
    alive = _workers_alive_after_measuring(monkeypatch)
    serial_world = _pooled_world(tmp_path, monkeypatch, AT)
    pooled_world = _pooled_world(tmp_path, monkeypatch, AT + timedelta(hours=1))
    serial, pooled = tmp_path / "serial" / "ledger.jsonl", tmp_path / "pooled" / "ledger.jsonl"

    assert _discover(serial_world, tmp_path / "serial-runtime", serial, 1) == 0
    serial_out = capsys.readouterr().out
    assert _discover(pooled_world, tmp_path / "pooled-runtime", pooled, 3) == 0
    pooled_out = capsys.readouterr().out

    assert _rows_without_time(pooled) == _rows_without_time(serial)
    assert [row.recorded_at for row in grid.read_ledger(pooled)] == [AT + timedelta(hours=1)] * 6
    assert [row.config_id for row in grid.read_ledger(pooled)] == [_id(c) for c in configs]
    results = [row.result for row in grid.read_ledger(pooled)]
    assert results[2] == {
        "error": "StrategyBacktestError: the fake book refuses it",
        "code_commit": COMMIT,
        "excess_benchmark": grid.PRIMARY_EXCESS_BENCHMARK,
    }
    assert results[4]["error"].startswith("StageWindowError: ")
    assert results[4]["code_commit"] == COMMIT
    assert results[4]["excess_benchmark"] == grid.PRIMARY_EXCESS_BENCHMARK
    assert pooled_out == serial_out  # the same progress lines, in the same order
    measured = _calls(tmp_path / "pooled-runtime")
    order = [entry["config_id"] for entry in measured]
    assert order.index(_strategy_id(configs[3])) < order.index(_strategy_id(configs[0]))
    assert _strategy_id(configs[4]) not in order  # a window refusal is never measured
    assert {entry["pid"] for entry in _calls(tmp_path / "serial-runtime")} == {os.getpid()}
    assert os.getpid() not in {entry["pid"] for entry in measured}
    assert all(entry["pins"] == thread_count_pins() for entry in measured)
    assert all(entry["sigint_ignored"] for entry in measured)  # a Ctrl-C is the parent's
    # The serial run is this process: its disposition is whatever this test started with (a
    # pytest launched with SIGINT ignored inherits that), untouched by the pooled run.
    assert {entry["sigint_ignored"] for entry in _calls(tmp_path / "serial-runtime")} == {
        ignored_at_start
    }
    assert (signal.getsignal(signal.SIGINT) == signal.SIG_IGN) == ignored_at_start
    assert not any(name in os.environ for name in thread_count_pins())
    assert len(alive) == 1 and alive[0] >= 2  # the workers were there while it measured ...
    assert _no_worker_left()  # ... and none outlived the pool


def test_a_stage_resumed_in_worker_processes_never_submits_what_the_ledger_holds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    configs = _pooled_configs(50, 50, 50, 50)
    monkeypatch.setattr(p6, "discovery_configs", lambda sessions: configs)
    ledger = tmp_path / "ledger.jsonl"
    for held in (configs[1], configs[3]):
        grid.append_ledger(ledger, "discovery", held, _result(), recorded_at=AT)
    runtime = tmp_path / "runtime"

    assert _discover(_pooled_world(tmp_path, monkeypatch, AT), runtime, ledger, 2) == 0

    out = capsys.readouterr().out
    assert sorted(entry["config_id"] for entry in _calls(runtime)) == sorted(
        _strategy_id(config) for config in (configs[0], configs[2])
    )
    assert [line.split()[1] for line in out.splitlines()[:4]] == ["ran", "skipped"] * 2
    assert "discovery: 2 ran, 2 skipped, family 4" in out
    assert [row.config_id for row in grid.read_ledger(ledger)] == [
        _id(configs[1]),
        _id(configs[3]),
        _id(configs[0]),
        _id(configs[2]),
    ]


def test_an_error_in_a_worker_stops_the_stage_after_the_rows_before_it_and_leaves_no_worker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The serial run's stop: the rows before the failing configuration land, the error
    propagates, and no row after it is written -- not even one a worker finished before the
    failure surfaced. The pool is shut down with no worker process left."""
    configs = _pooled_configs(SLOW, 50, CRASH, 50, LATE, 50)
    monkeypatch.setattr(p6, "discovery_configs", lambda sessions: configs)
    _cores(monkeypatch)
    alive = _workers_alive_after_measuring(monkeypatch)
    ledger, runtime = tmp_path / "ledger.jsonl", tmp_path / "runtime"

    with pytest.raises(RuntimeError, match="a bug in the worker"):
        _discover(_pooled_world(tmp_path, monkeypatch, AT), runtime, ledger, 3)

    assert [row.config_id for row in grid.read_ledger(ledger)] == [
        _id(configs[0]),
        _id(configs[1]),
    ]
    measured = _calls(runtime)
    assert _strategy_id(configs[4]) in [entry["config_id"] for entry in measured]
    assert len(alive) == 1 and alive[0] >= 2
    assert _no_worker_left()


def test_workers_need_a_factory_a_worker_process_can_import(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A worker builds its own SDK; a lambda does not pickle, so it is refused before anything
    is measured rather than failing inside the pool."""
    environment = _pooled_world(tmp_path, monkeypatch, AT)
    world = p6.Environment(
        precondition=environment.precondition,
        sessions=environment.sessions,
        sdk=lambda runtime_dir: _FakeSDK(),
        code_commit=environment.code_commit,
        repo=environment.repo,
    )
    ledger = tmp_path / "ledger.jsonl"

    assert _discover(world, tmp_path / "runtime", ledger, 2) == 1

    assert "WorkerFactoryError" in capsys.readouterr().err
    assert not ledger.exists()


def test_a_worker_that_cannot_start_is_a_refusal_by_name_not_a_traceback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    configs = _pooled_configs(50, 50)
    monkeypatch.setattr(p6, "discovery_configs", lambda sessions: configs)
    world = p6.Environment(
        precondition=lambda runtime_dir: None,
        sessions=lambda runtime_dir: SESSIONS,
        sdk=_unbuildable_sdk,
        code_commit=lambda: COMMIT,
        repo=tmp_path / "checkout",
    )
    _pooled_world(tmp_path, monkeypatch, AT)  # the foreign-package checks admit tmp_path
    ledger = tmp_path / "ledger.jsonl"

    assert _discover(world, tmp_path / "runtime", ledger, 2) == 1

    err = capsys.readouterr().err  # this process's own lines only, not a worker's
    (refusal,) = [line for line in err.splitlines() if line.startswith("refused: ")]
    assert refusal.startswith("refused: WorkerPoolBrokenError: ")
    assert "rerun" in refusal
    # The operator is told that a worker's failed commit check lands here too, and where to
    # find the two commits it compared.
    assert "CheckoutMovedError" in refusal and "stderr" in refusal
    assert grid.stage_family(ledger, "discovery") == 0
    assert _no_worker_left()


def test_a_worker_that_dies_mid_run_stops_the_stage_with_a_correct_prefix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A worker killed outright (an out-of-memory kill, say) breaks the pool: the run stops by
    name with exit 1, and the ledger holds a prefix of the configurations, each row complete."""
    configs = _pooled_configs(50, 50, KILLED, 50, 50)
    monkeypatch.setattr(p6, "discovery_configs", lambda sessions: configs)
    ledger, runtime = tmp_path / "ledger.jsonl", tmp_path / "runtime"

    assert _discover(_pooled_world(tmp_path, monkeypatch, AT), runtime, ledger, 2) == 1

    assert "refused: WorkerPoolBrokenError: " in capsys.readouterr().err
    written = [row.config_id for row in grid.read_ledger(ledger)]
    assert written == [_id(config) for config in configs[: len(written)]]
    assert len(written) <= 2
    assert _no_worker_left()


def test_the_worker_count_is_capped_at_the_machines_cores(monkeypatch: pytest.MonkeyPatch) -> None:
    _cores(monkeypatch, 4)
    arguments = ["discovery", "--runtime-dir", "runtime", "--ledger", "ledger.jsonl"]
    assert p6._parser().parse_args([*arguments, "--workers", "4"]).workers == 4
    with pytest.raises(SystemExit) as refused:
        p6._parser().parse_args([*arguments, "--workers", "5"])
    assert refused.value.code == 2


def test_a_result_dropped_for_a_row_another_writer_landed_says_so() -> None:
    """The serial run never measures a held configuration, so its lines are `ran` or
    `skipped`; a pooled run that measured one another writer landed first says why it skipped."""
    (config,) = _pooled_configs(50)
    ran = p6._landed_line(1, 2, config, "ran")
    held = p6._landed_line(2, 2, config, "held")
    dropped = p6._landed_line(2, 2, config, "measured-but-held")

    assert ran == p6._progress(1, 2, config, ran=True)
    assert held == p6._progress(2, 2, config, ran=False)
    assert dropped == f"{held} (measured, but the ledger already held it)"


@pytest.mark.parametrize("command", WORKER_COMMANDS)
def test_a_measuring_command_takes_a_worker_count_of_at_least_one(
    command: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    _cores(monkeypatch)
    arguments = [command, "--runtime-dir", "runtime", "--ledger", "ledger.jsonl"]
    assert p6._parser().parse_args(arguments).workers == 1
    assert p6._parser().parse_args([*arguments, "--workers", "3"]).workers == 3
    for count in ("0", "-2", "two"):
        with pytest.raises(SystemExit) as refused:
            p6._parser().parse_args([*arguments, "--workers", count])
        assert refused.value.code == 2


@pytest.mark.parametrize("command", [c for c in p6.COMMANDS if c not in WORKER_COMMANDS])
def test_no_other_command_takes_a_worker_count(command: str) -> None:
    """`holdout` runs once through `registry.run_holdout`; `register` and the reports measure
    nothing."""
    arguments = [command, "--runtime-dir", "runtime", "--ledger", "ledger.jsonl"]
    with pytest.raises(SystemExit) as refused:
        p6._parser().parse_args([*arguments, "--workers", "2"])
    assert refused.value.code == 2


# --- V2-P6-023 follow-up: the recorded commit is the code that ran; Ctrl-C stops a pool ----------


def test_a_ledger_writing_command_resolves_a_clean_commit_before_its_precondition(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The precondition takes minutes on the research store; the commit a stage records is the
    one checked out when the command started, and a dirty tree is refused before anything."""
    order: list[str] = []
    world = dataclasses.replace(
        _pooled_world(tmp_path, monkeypatch, AT),
        precondition=lambda runtime_dir: order.append("precondition"),
        code_commit=lambda: order.append("commit") or f"{COMMIT}-dirty",  # type: ignore[func-returns-value]
    )

    assert _discover(world, tmp_path / "runtime", tmp_path / "ledger.jsonl", 1) == 1

    assert "StageCommitError" in capsys.readouterr().err
    assert order == ["commit"]


class _Checkout:
    """The checkout's commit as `Environment.code_commit` answers it, moved on by the test."""

    def __init__(self) -> None:
        self.head = COMMIT
        self.appended = 0

    def moved(self) -> None:
        self.head = OTHER_COMMIT

    def clock_moving_after(self, appends: int) -> Callable[[], datetime]:
        """A clock read once per appended row that moves the checkout after `appends` rows."""

        def clock() -> datetime:
            self.appended += 1
            if self.appended == appends:
                self.moved()
            return AT

        return clock


@pytest.mark.parametrize("workers", [1, 2])
def test_a_commit_landing_during_the_precondition_refuses_the_stage_before_any_row(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    workers: int,
) -> None:
    configs = _pooled_configs(50, 50, 50)
    monkeypatch.setattr(p6, "discovery_configs", lambda sessions: configs)
    checkout = _Checkout()
    world = dataclasses.replace(
        _pooled_world(tmp_path, monkeypatch, AT),
        precondition=lambda runtime_dir: checkout.moved(),
        code_commit=lambda: checkout.head,
    )
    ledger, runtime = tmp_path / "ledger.jsonl", tmp_path / "runtime"

    assert _discover(world, runtime, ledger, workers) == 1

    err = capsys.readouterr().err
    assert "refused: CheckoutMovedError: " in err
    assert COMMIT in err and OTHER_COMMIT in err
    assert grid.stage_family(ledger, "discovery") == 0
    if workers > 1:
        assert _calls(runtime) == []  # refused before a worker was spawned
    assert _no_worker_left()


@pytest.mark.parametrize("workers", [1, 2])
def test_a_commit_landing_mid_stage_keeps_the_rows_before_it_and_appends_no_more(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    workers: int,
) -> None:
    """Rows already appended were measured at the recorded commit and stay; the next append
    finds the checkout moved and the stage stops by name."""
    configs = _pooled_configs(50, 50, 50, 50)
    monkeypatch.setattr(p6, "discovery_configs", lambda sessions: configs)
    checkout = _Checkout()
    world = dataclasses.replace(
        _pooled_world(tmp_path, monkeypatch, AT),
        code_commit=lambda: checkout.head,
        clock=checkout.clock_moving_after(2),
    )
    ledger = tmp_path / "ledger.jsonl"

    assert _discover(world, tmp_path / "runtime", ledger, workers) == 1

    err = capsys.readouterr().err
    assert "refused: CheckoutMovedError: " in err
    assert COMMIT in err and OTHER_COMMIT in err
    rows = grid.read_ledger(ledger)
    assert [row.config_id for row in rows] == [_id(configs[0]), _id(configs[1])]
    assert {row.result["code_commit"] for row in rows} == {COMMIT}
    assert _no_worker_left()


@pytest.fixture
def sigint_raises() -> Iterator[None]:
    """Python's own SIGINT handler for the test's duration, restored after. A pytest launched
    with SIGINT ignored (`trap '' INT`) inherits `SIG_IGN`, under which `interrupt_main` does
    nothing and an interrupt test would prove nothing."""
    previous = signal.signal(signal.SIGINT, signal.default_int_handler)
    try:
        yield
    finally:
        signal.signal(signal.SIGINT, previous)


@pytest.mark.usefixtures("sigint_raises")
def test_an_interrupted_pooled_run_stops_its_workers_at_once_and_keeps_a_correct_prefix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ctrl-C while a worker is a minute into a measurement: the run leaves within seconds,
    its workers terminated rather than waited for, and the ledger is a prefix of the stage.
    (`interrupt_main` delivers the interrupt to the main thread on every platform.)"""
    configs = _pooled_configs(50, STALL, 50)
    monkeypatch.setattr(p6, "discovery_configs", lambda sessions: configs)
    ledger, runtime = tmp_path / "ledger.jsonl", tmp_path / "runtime"
    interrupted: list[float] = []

    def interrupt_once_stalled() -> None:
        deadline = monotonic() + 30
        while not (runtime / "stalled").exists() and monotonic() < deadline:
            sleep(0.05)
        interrupted.append(monotonic())
        _thread.interrupt_main()

    watcher = threading.Thread(target=interrupt_once_stalled, daemon=True)
    watcher.start()
    with pytest.raises(KeyboardInterrupt):
        _discover(_pooled_world(tmp_path, monkeypatch, AT), runtime, ledger, 2)
    left = monotonic()
    watcher.join(timeout=5)

    assert (runtime / "stalled").exists()
    assert left - interrupted[0] < 10  # not the STALL_SECONDS the measurement had left
    written = [row.config_id for row in grid.read_ledger(ledger)]
    assert written == [_id(config) for config in configs[: len(written)]]
    assert len(written) <= 1
    assert _no_worker_left()


def test_the_pool_records_its_workers_where_a_prompt_stop_reads_them() -> None:
    """`_pool_workers` reads `ProcessPoolExecutor._processes`, a private attribute (Python 3.11
    has no public way to terminate a pool's workers). This pins what it relies on: while the pool
    runs, the attribute names its live worker processes, which are this process's children."""
    context = multiprocessing.get_context("spawn")
    with ProcessPoolExecutor(max_workers=1, mp_context=context) as executor:
        assert executor.submit(abs, -1).result() == 1
        workers = p6._pool_workers(executor)
        assert workers
        assert all(worker.is_alive() for worker in workers)
        assert set(workers) <= set(multiprocessing.active_children())
    assert _no_worker_left()


# --- V2-P6-023 review minors: before stage 2 freezes the code ------------------------------------


def test_stopping_workers_always_joins_them_even_where_the_pool_terminates_them_itself(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Python 3.14's `terminate_workers` terminates and does not join; `_stop_workers` then
    joins every worker it captured, and kills one still alive past the join. Here the pool's own
    terminate does nothing at all, so only the join-and-kill stops the busy worker."""
    monkeypatch.setattr(p6, "WORKER_STOP_SECONDS", 0.5)
    asked: list[str] = []
    context = multiprocessing.get_context("spawn")
    executor = ProcessPoolExecutor(max_workers=1, mp_context=context)
    try:
        executor.submit(sleep, 60)
        workers = p6._pool_workers(executor)
        assert workers
        executor.terminate_workers = lambda: asked.append("terminate_workers")  # type: ignore[attr-defined]

        p6._stop_workers(executor)

        assert asked == ["terminate_workers"]
        assert not any(worker.is_alive() for worker in workers)
    finally:
        executor.shutdown(wait=True, cancel_futures=True)
    assert _no_worker_left()


def test_the_driver_reads_head_before_any_heavy_import(tmp_path: Path) -> None:
    """`_p6_head` is the first non-standard-library import of `p6.py` and imports only the
    standard library, so the commit a run starts at is read before `openalpha_cn` (about a third
    of a second) loads -- and what it reads is HEAD, checked on a repository this test owns
    (the checkout's own HEAD may move while a long suite runs)."""
    tree = ast.parse((RESEARCH / "p6.py").read_text(encoding="utf-8"))
    imported = [
        alias.name.split(".")[0] if isinstance(node, ast.Import) else str(node.module).split(".")[0]
        for node in tree.body
        if isinstance(node, ast.Import | ast.ImportFrom)
        for alias in (node.names if isinstance(node, ast.Import) else node.names[:1])
    ]
    local = [name for name in imported if name not in sys.stdlib_module_names]
    assert local[0] == "_p6_head"
    assert local.index("_p6_head") < local.index("grid") < local.index("openalpha_cn")
    head_tree = ast.parse((RESEARCH / "_p6_head.py").read_text(encoding="utf-8"))
    head_imports = [
        alias.name.split(".")[0]
        for node in ast.walk(head_tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    ] + [
        str(node.module).split(".")[0]
        for node in ast.walk(head_tree)
        if isinstance(node, ast.ImportFrom) and node.module != "__future__"
    ]
    assert all(name in sys.stdlib_module_names for name in head_imports)

    p6_head = _research_module("_p6_head")
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q", "--template=")
    assert p6_head.read_head(repo) is None  # a repository with no commit has no HEAD
    (repo / "a.txt").write_text("a\n", encoding="utf-8")
    commit_file(repo, repo / "a.txt", "a", at=datetime(2026, 9, 26, tzinfo=UTC))
    assert p6_head.read_head(repo) == head(repo)
    (repo / "b.txt").write_text("b\n", encoding="utf-8")
    commit_file(repo, repo / "b.txt", "b", at=datetime(2026, 9, 27, tzinfo=UTC))
    assert p6_head.read_head(repo) == head(repo)  # HEAD as it is when asked
    assert p6_head.read_head(tmp_path / "no-repository") is None
    assert p6_head.ANCHOR == RESEARCH.resolve() == p6.DRIVER_ANCHOR
    started = p6.HEAD_AT_START
    assert started is not None and len(started) == 40
    assert set(started) <= set("0123456789abcdef")
    assert p6.default_environment().started_at == started


def test_a_commit_landing_while_the_driver_imports_is_refused_before_the_precondition(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    order: list[str] = []
    world = dataclasses.replace(
        _pooled_world(tmp_path, monkeypatch, AT),
        precondition=lambda runtime_dir: order.append("precondition"),
        code_commit=lambda: OTHER_COMMIT,
        started_at=COMMIT,
    )

    assert _discover(world, tmp_path / "runtime", tmp_path / "ledger.jsonl", 1) == 1

    err = capsys.readouterr().err
    assert "refused: CheckoutMovedError: " in err
    assert COMMIT in err and OTHER_COMMIT in err
    assert order == []


def _committing_sdk(runtime_dir: Path) -> _PooledSDK:
    """A factory that, in a worker, moves the checkout at `<runtime>/../repo` on before the
    worker's own commit check: the first worker commits, every other waits until it has."""
    if multiprocessing.parent_process() is not None:
        repo = runtime_dir.parent / "repo"
        try:
            os.close(os.open(runtime_dir / "committing", os.O_CREAT | os.O_EXCL))
        except FileExistsError:
            deadline = monotonic() + 30
            while not (runtime_dir / "moved").exists() and monotonic() < deadline:
                sleep(0.05)
        else:
            (repo / "later.txt").write_text("a commit that landed mid-run\n", encoding="utf-8")
            commit_file(repo, repo / "later.txt", "later", at=datetime(2026, 9, 28, tzinfo=UTC))
            (runtime_dir / "moved").touch()
    return _PooledSDK(runtime_dir)


def test_a_worker_that_starts_on_another_commit_fails_its_start_and_writes_no_row(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str],
) -> None:
    """HEAD moved on after this process last looked (and may move back before it looks again):
    each worker compares the checkout's commit, read after its own imports and SDK build, with
    the one the run recorded, and does not start on another."""
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q", "--template=")
    (repo / "README.md").write_text("research\n", encoding="utf-8")
    commit_file(repo, repo / "README.md", "initial", at=datetime(2026, 9, 26, tzinfo=UTC))
    recorded = head(repo)
    configs = _pooled_configs(50, 50, 50)
    monkeypatch.setattr(p6, "discovery_configs", lambda sessions: configs)
    world = dataclasses.replace(
        _pooled_world(tmp_path, monkeypatch, AT),
        sdk=_committing_sdk,
        code_commit=lambda: recorded,  # this process never sees the move
        anchor=repo,
    )
    ledger = tmp_path / "ledger.jsonl"

    assert _discover(world, tmp_path / "runtime", ledger, 2) == 1

    err = capfd.readouterr().err
    assert "refused: WorkerPoolBrokenError: " in err
    assert "CheckoutMovedError" in err and recorded in err
    assert grid.stage_family(ledger, "discovery") == 0
    assert _no_worker_left()


def test_a_ledger_named_with_other_letter_case_is_still_inside_the_checkout(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """On a case-insensitive filesystem (APFS by default) `CHECKOUT/research` is the checkout's
    `research` directory, and `resolve()` does not canonicalise the case; the check compares
    the directories themselves."""
    checkout = tmp_path / "checkout"
    (checkout / "research").mkdir(parents=True)
    if not (tmp_path / "CHECKOUT").exists():
        pytest.skip("this filesystem tells letter case apart; the path is another directory")
    touched: list[str] = []
    environment = p6.Environment(
        precondition=lambda runtime_dir: touched.append("precondition"),  # type: ignore[func-returns-value]
        sessions=lambda runtime_dir: SESSIONS,
        sdk=lambda runtime_dir: _FakeSDK(),
        code_commit=lambda: touched.append("commit") or COMMIT,  # type: ignore[func-returns-value]
        repo=checkout,
    )
    ledger = tmp_path / "CHECKOUT" / "Research" / "ledger.jsonl"

    code = p6.main(
        ["discovery", "--runtime-dir", str(tmp_path), "--ledger", str(ledger)],
        environment=environment,
    )

    assert code == 1
    assert "refused: LedgerInCheckoutError: " in capsys.readouterr().err
    assert touched == []
    assert not ledger.exists()


def test_a_ledger_beside_the_checkout_is_not_taken_for_one_inside_it(tmp_path: Path) -> None:
    """The comparison is by directory, so a sibling whose name only starts like the checkout's,
    or a checkout that does not exist yet, is not inside it."""
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    (tmp_path / "checkout-ledgers").mkdir()

    p6._refuse_a_ledger_in_the_checkout(tmp_path / "checkout-ledgers" / "l.jsonl", checkout)
    p6._refuse_a_ledger_in_the_checkout(tmp_path / "l.jsonl", checkout)
    p6._refuse_a_ledger_in_the_checkout(tmp_path / "l.jsonl", tmp_path / "missing")
    with pytest.raises(p6.LedgerInCheckoutError):
        p6._refuse_a_ledger_in_the_checkout(checkout / "deep" / "er" / "l.jsonl", checkout)


@pytest.mark.parametrize("command", WORKER_COMMANDS)
def test_a_ledger_inside_the_checkout_is_refused_before_anything_runs(
    tmp_path: Path, command: str, capsys: pytest.CaptureFixture[str]
) -> None:
    """A ledger inside the checkout dirties the tree with its first row, which would stop the
    stage at its second append; it is refused before the commit, the precondition or anything."""
    touched: list[str] = []
    checkout = tmp_path / "checkout"
    environment = p6.Environment(
        precondition=lambda runtime_dir: touched.append("precondition"),  # type: ignore[func-returns-value]
        sessions=lambda runtime_dir: touched.append("sessions") or SESSIONS,  # type: ignore[func-returns-value]
        sdk=lambda runtime_dir: touched.append("sdk") or _FakeSDK(),  # type: ignore[func-returns-value]
        code_commit=lambda: touched.append("commit") or COMMIT,  # type: ignore[func-returns-value]
        repo=checkout,
    )
    ledger = checkout / "research" / "ledger.jsonl"

    code = p6.main(
        [command, "--runtime-dir", str(tmp_path), "--ledger", str(ledger)], environment=environment
    )

    assert code == 1
    assert "refused: LedgerInCheckoutError: " in capsys.readouterr().err
    assert touched == []
    assert not ledger.exists()


@pytest.mark.parametrize(
    "stage",
    ["run_discovery", "run_composition_sources", "run_composition_strategies", "run_validation"],
)
def test_a_stage_function_cannot_be_called_without_naming_its_checkout_guard(stage: str) -> None:
    parameter = inspect.signature(getattr(p6, stage)).parameters["verify"]
    assert parameter.kind is inspect.Parameter.KEYWORD_ONLY
    assert parameter.default is inspect.Parameter.empty
