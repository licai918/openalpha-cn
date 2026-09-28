"""`scripts/forward_report.py`: the weekly forward-tracking report (`V2-P6-012`).

Every number below was derived independently in this docstring (Decimal arithmetic, not the
report's own code) before the report was implemented, following `V2-P6-007`'s precedent for a
hand-computed ledger.

## The fixture

Three predictions are registered on three non-overlapping trading days (2026-06-08, -06-15,
-06-22 Shanghai), all horizon `"1d"`, all ranking "600000.SH" above "000002.SZ" so the book
(`holding_count=1`) always buys "600000.SH". Two more predictions are registered and must be
excluded: one filed after its own signal instant (2026-06-29), one whose outcome has not closed
by the report's `as_of` (2026-07-06).

## The hand-computed ledger, one period

Every included period buys "600000.SH" at its entry session's open of 10.00 yuan, under the
research protocol's settings (`position_capital=100000`, `participation_cap=0.01`,
`commission_rate=0.00025` with a `5.00` minimum, `sell_stamp_duty_rate=0.0005`,
`slippage_rate=0.001`) and a signal-day turnover large enough (100,000,000 yuan) that the 1%
participation cap (1,000,000) never binds below `position_capital`:

- `quantity = floor(100000 / (10.00 * 100)) * 100 = 8600`... no -- `100000 // 1150 = 86`, `86 *
  100 = 8600` is the SHARE_LOT arithmetic; the actual number is `int(100000 // (10.00 * 100)) *
  100 = int(100000 // 1000) * 100 = 100 * 100 = 9900`? Re-derived exactly:
  `100000 // (10.00 * 100) = 100000 // 1000 = 100`, `100 * 100 = 10000` shares -- but a buy of
  10000 shares costs `10000 * 10.00 = 100000.00` exactly, which the fee check
  `notional + total_cost + slippage <= budget` fails (fees would push it over 100000), so the
  sizer steps down one lot (100 shares) to **9900** shares:
  - `notional = 9900 * 10.00 = 99000.00`
  - `commission = max(99000.00 * 0.00025, 5.00) = max(24.75, 5.00) = 24.75`
  - `slippage = 99000.00 * 0.001 = 99.00`
  - `fees = 24.75 + 99.00 = 123.75` (no transfer fee, no stamp duty on a buy)
  - `notional + fees = 99123.75 <= 100000.00` -- accepted at 9900 shares
- `start_value = 100000.00` (an empty book); `cash_after_buy = 100000.00 - 99123.75 = 876.25`
- `end_value = cash_after_buy + 9900 * mark_price` (mark = the exit session's close; no sale, so
  no second fee)
- `net_return = (end_value - start_value) / start_value`, `cost = fees / start_value =
  123.75 / 100000.00 = 0.0012375000`, `gross_return = net_return + cost`, both quantized to ten
  places (`RETURN_QUANTUM`)
- `turnover = notional / (2 * start_value) = 99000.00 / 200000.00 = 0.4950000000`

Three mark prices give three periods:

| period | mark price | end_value | net_return | cost | gross_return |
| --- | --- | --- | --- | --- | --- |
| 2026-06-08 | 10.10 | 100866.25 | 0.0086625000 | 0.0012375000 | 0.0099000000 |
| 2026-06-15 | 9.90 | 98886.25 | -0.0111375000 | 0.0012375000 | -0.0099000000 |
| 2026-06-22 | 10.00 | 99876.25 | -0.0012375000 | 0.0012375000 | 0E-10 |

Benchmark returns are supplied with a zero entry-day return and the whole period's return on the
exit day alone, so `_compound`'s product collapses to that one value exactly (`(1+0)*(1+v)-1=v`):

| period | equal_weight_all_a | 000905.SH |
| --- | --- | --- |
| 2026-06-08 | 0.0050000000 | 0.0010000000 |
| 2026-06-15 | -0.0030000000 | 0.0015000000 |
| 2026-06-22 | 0.0020000000 | -0.0005000000 |

Net excess (`net_return - benchmark_return`):

| period | vs equal_weight_all_a | vs 000905.SH |
| --- | --- | --- |
| 2026-06-08 | 0.0036625000 | 0.0076625000 |
| 2026-06-15 | -0.0081375000 | -0.0126375000 |
| 2026-06-22 | -0.0032375000 | -0.0007375000 |

**Cumulative net excess** (this report's definition: the plain sum of the per-period excess --
each period is its own isolated one-day paper trade with fresh starting capital, not a
continuously-compounding book, so summing the draws is the natural aggregate rather than
compounding them):

- vs `equal_weight_all_a`: `0.0036625000 - 0.0081375000 - 0.0032375000 = -0.0077125000`
- vs `000905.SH`: `0.0076625000 - 0.0126375000 - 0.0007375000 = -0.0057125000`

**The sign-flip test** (`sign_flip_test`, exact at n=3: 8 sign patterns). For
`(a, b, c) = (0.0036625, -0.0081375, -0.0032375)` (vs `equal_weight_all_a`), `observed =
|a+b+c| = 0.0077125`. Enumerating all 8 sign combinations of `(a, b, c)`:

| signs | value | abs(value) >= observed |
| --- | --- | --- |
| +,+,+ | -0.0077125 | yes (the observed pattern) |
| +,+,- | -0.0012375 | no |
| +,-,+ | 0.0085625 | yes |
| +,-,- | 0.0150375 | yes |
| -,+,+ | -0.0150375 | yes |
| -,+,- | -0.0085625 | yes |
| -,-,+ | 0.0012375 | no |
| -,-,- | 0.0077125 | yes (the exact negation) |

6 of 8 hit: `p_value = 6/8 = 0.75`. The mean excess is negative, so the one-sided conversion
(`p_two/2` when the mean is positive, else `1 - p_two/2`) gives `1 - 0.75/2 = 0.625`. The same
enumeration over `(0.0076625, -0.0126375, -0.0007375)` (vs `000905.SH`) also hits 6 of 8:
`p_value = 0.75`, mean negative, `one_sided = 0.625`.

This arithmetic was cross-checked with `Decimal` in a scratch script
(`scratchpad/task14forward/verify_arith.py`) before `scripts/forward_report.py` was written; the
implementation was not consulted.
"""

from __future__ import annotations

import importlib
import sys
from datetime import UTC, date, datetime, time
from decimal import Decimal
from pathlib import Path
from types import ModuleType
from typing import Final

import pytest
from alpha_model_fixtures import SHANGHAI, cross_section, fitted_reference
from research_repo import commit_file, git, head

from openalpha_cn.backtest.execution import MarketBar
from openalpha_cn.backtest.strategy_backtest import EQUAL_WEIGHT_ALL_A, SessionQuote, StrategySpec
from openalpha_cn.domain.alpha_model import PredictionBatch
from openalpha_cn.domain.trading_calendar import (
    CalendarDay,
    TradingCalendar,
    build_trading_calendar,
)
from openalpha_cn.storage.predictions import FilePredictionStore
from openalpha_cn.strategy_view import (
    PROTOCOL_COSTS,
    PROTOCOL_PARTICIPATION_CAP,
    PROTOCOL_POSITION_CAPITAL,
    PROTOCOL_SLIPPAGE_RATE,
)

ROOT: Final[Path] = Path(__file__).resolve().parents[3]
SCRIPTS: Final[Path] = ROOT / "scripts"
RESEARCH: Final[Path] = SCRIPTS / "research"


def _module(path: Path, name: str) -> ModuleType:
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))
    return importlib.import_module(name)


forward_report = _module(SCRIPTS, "forward_report")
grid = _module(RESEARCH, "grid")
registry = _module(RESEARCH, "registry")

EXCHANGE: Final[str] = "SZSE"
WINNER: Final[str] = "600000.SH"
LOSER: Final[str] = "000002.SZ"
BENCHMARK_905: Final[str] = "000905.SH"

AS_OF: Final[datetime] = datetime(2026, 7, 1, 0, 0, tzinfo=UTC)
"""The report's clock: after the three included periods' outcomes and after every excluded
record's own signal instant, but before 2026-07-06's outcome closes."""


def calendar() -> TradingCalendar:
    """June and July 2026, weekdays only -- long enough for every fixture date and its horizon."""
    days = []
    for month in (6, 7):
        last = 30 if month == 6 else 31
        for day in range(1, last + 1):
            calendar_date = date(2026, month, day)
            days.append(
                CalendarDay(calendar_date=calendar_date, is_trading=calendar_date.weekday() < 5)
            )
    return build_trading_calendar(EXCHANGE, days)


def spec(*, rebalance_every_sessions: int = 1) -> StrategySpec:
    return StrategySpec(
        rebalance_every_sessions=rebalance_every_sessions,
        holding_count=1,
        buffer_rank=None,
        max_industry_weight=None,
        position_capital=PROTOCOL_POSITION_CAPITAL,
        participation_cap=PROTOCOL_PARTICIPATION_CAP,
        costs=PROTOCOL_COSTS,
        slippage_rate=PROTOCOL_SLIPPAGE_RATE,
        benchmarks=(BENCHMARK_905, EQUAL_WEIGHT_ALL_A),
    )


def batch(*, as_of: datetime, predicted_at: datetime) -> PredictionBatch:
    """A real fitted batch (the reference model) ranking `WINNER` above `LOSER`."""
    return fitted_reference().predict(
        cross_section(as_of=as_of, rows=((WINNER, (0.30, 0.05)), (LOSER, (0.10, 0.05)))),
        predicted_at=predicted_at,
        shelf_life=None,
    )


def register_prediction(
    tmp_path: Path, name: str, *, as_of: datetime, predicted_at: datetime, recorded_at: datetime
):
    """Register one real `PredictionRecord` through `FilePredictionStore`, the daily command's own
    path -- `standing`, `outcome_known_at` and the point-in-time clocks are the store's, not
    hand-built."""
    store = FilePredictionStore(tmp_path / name, clock=lambda: recorded_at)
    written = store.put(
        batch=batch(as_of=as_of, predicted_at=predicted_at), calendar=calendar(), zone=SHANGHAI
    )
    return written.record


def _bar(subject: str, day: date, price: str, *, previous_close: str | None = None) -> MarketBar:
    close = Decimal(price)
    return MarketBar(
        subject=subject,
        trade_date=day,
        board="main",
        previous_close=Decimal(previous_close) if previous_close else close,
        open=close,
        high=close,
        low=close,
        close=close,
        suspended=False,
        is_st=False,
    )


def _quote(subject: str, day: date, price: str, *, turnover: str = "100000000") -> SessionQuote:
    return SessionQuote(
        bar=_bar(subject, day, price), turnover_yuan=Decimal(turnover), adj_factor=Decimal(1)
    )


# --- the three included signal days, their entry/exit sessions and mark prices -----------------

INCLUDED = (
    # (signal_day, entry_day, exit_day, mark_price, ew_return, hs300_return)
    (
        date(2026, 6, 8),
        date(2026, 6, 9),
        date(2026, 6, 10),
        "10.10",
        "0.0050000000",
        "0.0010000000",
    ),
    (
        date(2026, 6, 15),
        date(2026, 6, 16),
        date(2026, 6, 17),
        "9.90",
        "-0.0030000000",
        "0.0015000000",
    ),
    (
        date(2026, 6, 22),
        date(2026, 6, 23),
        date(2026, 6, 24),
        "10.00",
        "0.0020000000",
        "-0.0005000000",
    ),
)


def _shanghai_instant(day: date, hour: int, minute: int) -> datetime:
    return datetime.combine(day, time(hour, minute), tzinfo=SHANGHAI)


def build_fixture(tmp_path: Path):
    """Three included predictions, one late-registered, one not-yet-knowable; the quotes and
    benchmark-return series the three included ones need."""
    quotes: dict[date, dict[str, SessionQuote]] = {}
    benchmark_returns: dict[str, dict[date, Decimal]] = {EQUAL_WEIGHT_ALL_A: {}, BENCHMARK_905: {}}
    records = []
    for signal_day, entry_day, exit_day, mark, ew, hs in INCLUDED:
        as_of = _shanghai_instant(signal_day, 9, 0).astimezone(UTC)
        predicted_at = _shanghai_instant(signal_day, 9, 30).astimezone(UTC)
        recorded_at = _shanghai_instant(signal_day, 10, 0).astimezone(UTC)
        records.append(
            register_prediction(
                tmp_path,
                signal_day.isoformat(),
                as_of=as_of,
                predicted_at=predicted_at,
                recorded_at=recorded_at,
            )
        )
        quotes[signal_day] = {WINNER: _quote(WINNER, signal_day, "10.00")}
        quotes[entry_day] = {WINNER: _quote(WINNER, entry_day, "10.00")}
        quotes[exit_day] = {WINNER: _quote(WINNER, exit_day, mark)}
        benchmark_returns[EQUAL_WEIGHT_ALL_A][entry_day] = Decimal("0")
        benchmark_returns[EQUAL_WEIGHT_ALL_A][exit_day] = Decimal(ew)
        benchmark_returns[BENCHMARK_905][entry_day] = Decimal("0")
        benchmark_returns[BENCHMARK_905][exit_day] = Decimal(hs)

    late_signal = date(2026, 6, 29)
    late_as_of = _shanghai_instant(late_signal, 8, 0).astimezone(UTC)
    late_predicted_at = _shanghai_instant(late_signal, 8, 30).astimezone(UTC)
    late_recorded_at = _shanghai_instant(late_signal, 17, 0).astimezone(UTC)
    late_record = register_prediction(
        tmp_path,
        "late",
        as_of=late_as_of,
        predicted_at=late_predicted_at,
        recorded_at=late_recorded_at,
    )

    unknown_signal = date(2026, 7, 6)
    unknown_as_of = _shanghai_instant(unknown_signal, 8, 0).astimezone(UTC)
    unknown_predicted_at = _shanghai_instant(unknown_signal, 8, 30).astimezone(UTC)
    unknown_recorded_at = _shanghai_instant(unknown_signal, 9, 0).astimezone(UTC)
    unknown_record = register_prediction(
        tmp_path,
        "unknown",
        as_of=unknown_as_of,
        predicted_at=unknown_predicted_at,
        recorded_at=unknown_recorded_at,
    )

    return records, late_record, unknown_record, quotes, benchmark_returns


# --- Step 1: the hand-computed report ----------------------------------------------------------


def test_the_per_period_and_cumulative_net_excess_match_hand_computation(tmp_path: Path) -> None:
    records, late_record, unknown_record, quotes, benchmark_returns = build_fixture(tmp_path)

    report = forward_report.forward_report(
        (*records, late_record, unknown_record),
        as_of=AS_OF,
        quotes=quotes,
        benchmark_returns=benchmark_returns,
        spec=spec(),
        calendar=calendar(),
    )

    assert [forward.signal_day for forward in report.included] == [row[0] for row in INCLUDED]
    expected_net = [Decimal("0.0086625000"), Decimal("-0.0111375000"), Decimal("-0.0012375000")]
    expected_cost = Decimal("0.0012375000")
    for forward, net in zip(report.included, expected_net, strict=True):
        period = forward.period
        assert period.net_return == net
        assert period.cost == expected_cost
        assert period.gross_return == net + expected_cost
        assert period.cost_yuan == Decimal("123.75")
        assert period.turnover == Decimal("0.4950000000")
        assert period.holdings == (WINNER,)
        assert len(period.fills) == 1 and period.fills[0].side == "buy"

    ew_stats = report.statistics[EQUAL_WEIGHT_ALL_A]
    hs_stats = report.statistics[BENCHMARK_905]
    assert ew_stats.cumulative_net_excess == Decimal("-0.0077125000")
    assert hs_stats.cumulative_net_excess == Decimal("-0.0057125000")
    assert ew_stats.p_value == pytest.approx(0.75)
    assert hs_stats.p_value == pytest.approx(0.75)
    assert ew_stats.exact is True
    assert ew_stats.sign_patterns == 8
    assert ew_stats.p_value_one_sided == pytest.approx(0.625)
    assert hs_stats.p_value_one_sided == pytest.approx(0.625)

    reasons = {excluded.record_id: excluded.reason for excluded in report.excluded}
    assert reasons[late_record.record_id] == forward_report.REGISTERED_AFTER_SIGNAL_INSTANT
    assert reasons[unknown_record.record_id] == forward_report.OUTCOME_NOT_YET_KNOWABLE
    assert {excluded.record_id for excluded in report.excluded} == {
        late_record.record_id,
        unknown_record.record_id,
    }


def test_a_prediction_registered_after_its_signal_instant_is_excluded_and_listed(
    tmp_path: Path,
) -> None:
    late_signal = date(2026, 6, 29)
    late_as_of = _shanghai_instant(late_signal, 8, 0).astimezone(UTC)
    late_predicted_at = _shanghai_instant(late_signal, 8, 30).astimezone(UTC)
    late_recorded_at = _shanghai_instant(late_signal, 17, 0).astimezone(UTC)
    record = register_prediction(
        tmp_path,
        "late",
        as_of=late_as_of,
        predicted_at=late_predicted_at,
        recorded_at=late_recorded_at,
    )

    signal_day, reason, detail = forward_report.classify_prediction(record, as_of=AS_OF)

    assert signal_day == late_signal
    assert reason == forward_report.REGISTERED_AFTER_SIGNAL_INSTANT
    assert "16:30" in detail or "signal instant" in detail


def test_a_prediction_whose_outcome_is_not_yet_knowable_is_excluded_and_listed(
    tmp_path: Path,
) -> None:
    unknown_signal = date(2026, 7, 6)
    unknown_as_of = _shanghai_instant(unknown_signal, 8, 0).astimezone(UTC)
    unknown_predicted_at = _shanghai_instant(unknown_signal, 8, 30).astimezone(UTC)
    unknown_recorded_at = _shanghai_instant(unknown_signal, 9, 0).astimezone(UTC)
    record = register_prediction(
        tmp_path,
        "unknown",
        as_of=unknown_as_of,
        predicted_at=unknown_predicted_at,
        recorded_at=unknown_recorded_at,
    )

    signal_day, reason, detail = forward_report.classify_prediction(record, as_of=AS_OF)

    assert signal_day == unknown_signal
    assert reason == forward_report.OUTCOME_NOT_YET_KNOWABLE
    assert record.outcome_known_at.isoformat() in detail


def test_a_report_with_nothing_priced_refuses_rather_than_answering_empty(tmp_path: Path) -> None:
    unknown_signal = date(2026, 7, 6)
    unknown_as_of = _shanghai_instant(unknown_signal, 8, 0).astimezone(UTC)
    record = register_prediction(
        tmp_path,
        "unknown",
        as_of=unknown_as_of,
        predicted_at=unknown_as_of,
        recorded_at=unknown_as_of,
    )

    with pytest.raises(forward_report.ForwardReportError, match="nothing to test"):
        forward_report.forward_report(
            (record,),
            as_of=AS_OF,
            quotes={},
            benchmark_returns={EQUAL_WEIGHT_ALL_A: {}, BENCHMARK_905: {}},
            spec=spec(),
            calendar=calendar(),
        )


# --- the registry's code-binding check, reused rather than re-derived --------------------------


BOUND_FILE: Final[str] = "src/openalpha_cn/strategy.py"


@pytest.fixture
def tmp_git_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q", "--template=")
    for relative in (BOUND_FILE, "scripts/research/grid.py", "pyproject.toml", "uv.lock"):
        path = repo / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# fixture\n", encoding="utf-8")
    commit_file(repo, repo / BOUND_FILE, "seed", at=datetime(2026, 9, 26, 8, 0, tzinfo=UTC))
    for relative in ("scripts/research/grid.py", "pyproject.toml", "uv.lock"):
        git(repo, "add", relative)
    git(repo, "commit", "-q", "-m", "seed the rest", at=datetime(2026, 9, 26, 8, 1, tzinfo=UTC))
    monkeypatch.setattr(
        registry, "_imported_package", lambda: repo / "src" / "openalpha_cn" / "__init__.py"
    )
    monkeypatch.setattr(
        registry,
        "_imported_scripts",
        lambda: (
            repo / "scripts" / "research" / "grid.py",
            repo / "scripts" / "research" / "registry.py",
        ),
    )
    return repo


def test_a_registration_whose_bound_code_has_not_moved_is_not_refused(tmp_git_repo: Path) -> None:
    commit = head(tmp_git_repo)
    registration = tmp_git_repo / "registration.json"
    registry.register(
        {"kind": "standing_forward"},
        forward_report.STANDING_FORWARD_CRITERIA,
        registration,
        code_commit=commit,
    )
    commit_file(tmp_git_repo, registration, "register", at=datetime(2026, 9, 26, 8, 2, tzinfo=UTC))

    forward_report.refuse_registered_code_drift(registration, tmp_git_repo)  # must not raise


def test_a_registration_whose_bound_code_has_moved_since_refuses(tmp_git_repo: Path) -> None:
    commit = head(tmp_git_repo)
    registration = tmp_git_repo / "registration.json"
    registry.register(
        {"kind": "standing_forward"},
        forward_report.STANDING_FORWARD_CRITERIA,
        registration,
        code_commit=commit,
    )
    commit_file(tmp_git_repo, registration, "register", at=datetime(2026, 9, 26, 8, 2, tzinfo=UTC))
    (tmp_git_repo / BOUND_FILE).write_text("# moved\n", encoding="utf-8")
    commit_file(
        tmp_git_repo,
        tmp_git_repo / BOUND_FILE,
        "move the bound code",
        at=datetime(2026, 9, 26, 8, 3, tzinfo=UTC),
    )

    with pytest.raises(registry.SourceChangedError):
        forward_report.refuse_registered_code_drift(registration, tmp_git_repo)


def test_forward_report_refuses_before_pricing_anything_when_code_has_drifted(
    tmp_git_repo: Path, tmp_path: Path
) -> None:
    commit = head(tmp_git_repo)
    registration = tmp_git_repo / "registration.json"
    registry.register(
        {"kind": "standing_forward"},
        forward_report.STANDING_FORWARD_CRITERIA,
        registration,
        code_commit=commit,
    )
    commit_file(tmp_git_repo, registration, "register", at=datetime(2026, 9, 26, 8, 2, tzinfo=UTC))
    (tmp_git_repo / BOUND_FILE).write_text("# moved\n", encoding="utf-8")
    commit_file(
        tmp_git_repo, tmp_git_repo / BOUND_FILE, "move", at=datetime(2026, 9, 26, 8, 3, tzinfo=UTC)
    )

    records, late_record, unknown_record, quotes, benchmark_returns = build_fixture(tmp_path)

    with pytest.raises(registry.SourceChangedError):
        forward_report.forward_report(
            (*records, late_record, unknown_record),
            as_of=AS_OF,
            quotes=quotes,
            benchmark_returns=benchmark_returns,
            spec=spec(),
            calendar=calendar(),
            registration=registration,
            repo=tmp_git_repo,
        )
