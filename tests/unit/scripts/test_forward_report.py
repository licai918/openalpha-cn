"""`scripts/forward_report.py`: the weekly forward-tracking report (`V2-P6-012`), fix round 1.

Fix round 1 replaces the first design, which priced each registered prediction as an isolated,
fresh-capital, buy-only trade and summed overlapping per-prediction returns. This module now
chains every admitted record into **one continuous book** -- the registered configuration's own
portfolio rules, sell side included -- and reduces its own non-overlapping periods with
`scripts.research.grid.strategy_result`, the same reduction the research grid gives the holdout.

## The hand-computed ledger

Three composite records are registered on `write_strategy_corpus`'s real panel (2026-01-05 through
2026-01-16, `600000.SH` base close 12.0 rising 0.5/session -- `close(i, j) = 10 + i + 0.5j` for
security index `i` and session index `j`; see `tests/panel_fixtures.py`). The registered
configuration: `holding_count=2`, `rebalance_every_sessions=3`, `position_capital=50000`
(comfortably under the 1% participation cap of 100,000 on a flat 10,000,000-yuan turnover, so no
sale is ever capped), anchored at session 0 (2026-01-05). Signal days 0, 3 and 6 give three
complete 3-session periods spanning the whole panel (2026-01-05 .. 2026-01-16); day 0 ranks
`600000.SH` and `600519.SH` top two, day 3 ranks `600000.SH` and `300750.SZ` top two (a sell), day
6 repeats day 3's ranking (no trade, a pure mark-to-market period).

**Period 1** (signal 01-05, entry 01-06, exit 01-08). Both buys at the entry session's open:

- `600000.SH` @ 12.5 (`close(2, 1)`): target `min(position_capital, cash, cap) = 50000`;
  `floor(50000 / (12.5*100)) = 40` lots (4000 shares) costs `50000.00` before fees, over budget,
  so the sizer steps down one lot to **3900 shares**: notional `48750.00`, commission
  `max(48750*0.00025, 5) = 12.1875` -> `12.19`, slippage `48750*0.001 = 48.75`, fees `60.94`,
  `48750.00 + 60.94 = 48810.94 <= 50000` accepted.
- `600519.SH` @ 13.5 (`close(3, 1)`): 37 lots (3700 shares, notional `49950.00`) plus fees
  (`12.49 + 49.95 = 62.44`) totals `50012.44 > 50000`, so the sizer steps down to **3600 shares**:
  notional `48600.00`, commission `12.15` (exact), slippage `48.60`, fees `60.75`,
  `48600.00 + 60.75 = 48660.75 <= 50000` accepted.
- `start_value = 100000.00` (an empty two-slot book, `position_capital * holding_count`);
  `cash_after_buys = 100000.00 - 48810.94 - 48660.75 = 2528.31`.
- Marked at the exit session's close: `600000.SH` @ 13.5 (`close(2, 3)`), `600519.SH` @ 14.5
  (`close(3, 3)`). `end_value = 2528.31 + 3900*13.5 + 3600*14.5 = 2528.31 + 52650.00 + 52200.00 =
  107378.31`.
- `net_return = (107378.31 - 100000.00) / 100000.00 = 0.0737831000`;
  `cost = (60.94 + 60.75) / 100000.00 = 0.0012169000`; `gross_return = 0.0750000000` (matches the
  raw price gain: `3900*(13.5-12.5) + 3600*(14.5-13.5) = 7500.00`, `7500.00/100000.00 = 0.075`).

**Period 2** (signal 01-08, entry 01-09, exit 01-13): sell all of `600519.SH`, keep `600000.SH`,
buy `300750.SZ`.

- Sell `600519.SH`, 3600 shares @ 15.0 (`close(3, 4)`; `3600*15.0 = 54000.00 <=` the 100,000
  participation cap, so the whole position is a legal sale): notional `54000.00`, commission
  `13.95`, stamp duty `54000*0.0005 = 27.00`, slippage `54.00`, fees `94.50`; proceeds `54000.00`
  (adjustment factors are all 1). `cash = 2528.31 + 54000.00 - 94.50 = 56433.81`.
- Buy `300750.SZ` @ 16.0 (`close(4, 4)`): budget `min(50000, 56433.81) = 50000`; 31 lots (3100
  shares, notional `49600.00`), commission `12.40`, slippage `49.60`, fees `62.00`,
  `49600.00 + 62.00 = 49662.00 <= 50000` accepted. `cash = 56433.81 - 49662.00 = 6771.81`.
- Marked at exit: `600000.SH` @ 15.0 (`close(2, 6)`), `300750.SZ` @ 17.0 (`close(4, 6)`).
  `end_value = 6771.81 + 3900*15.0 + 3100*17.0 = 6771.81 + 58500.00 + 52700.00 = 117971.81`.
- `start_value = 107378.31` (the continuous book: period 2 starts where period 1 ended).
  `net_return = (117971.81-107378.31)/107378.31 = 0.0986558645`;
  `cost = (94.50+62.00)/107378.31 = 0.0014574638`; `gross_return = 0.1001133283`.

**Period 3** (signal 01-13, entry 01-14, exit 01-16): the same two names, ranked the same way --
no trade, pure mark-to-market.

- Marked at exit: `600000.SH` @ 16.5 (`close(2, 9)`), `300750.SZ` @ 18.5 (`close(4, 9)`).
  `end_value = 6771.81 + 3900*16.5 + 3100*18.5 = 6771.81 + 64350.00 + 57350.00 = 128471.81`.
  `start_value = 117971.81`. `net_return = 10500.00/117971.81 = 0.0890043138`; `cost = 0` (no
  fills); `gross_return = net_return`.

**The statistics** (`grid.strategy_result`, unchanged, called once per benchmark). Net excess is
`net_return` less the period's own benchmark return (`period.benchmark_returns`, real
`000905.SH`/`equal_weight_all_a` series this panel's real index levels and bars give -- not
restated here, only read back and checked against the per-period arithmetic above):

- vs `000905.SH` (benchmark returns `0.0100000000, 0.0198019802, 0.0097087379`): excess
  `0.0637831000, 0.0788538843, 0.0792955759`, all positive. Cumulative (the plain sum):
  `0.2219325602`. `sign_flip_test` at n=3 (8 patterns): only the observed all-positive pattern and
  its exact negation reach the observed magnitude, so `p = 2/8 = 0.25`; the mean is positive, so
  the one-sided conversion gives `p/2 = 0.125`.
- vs `equal_weight_all_a` (benchmark returns `0.1143643240, 0.1034401052, 0.0926714448`): excess
  `-0.0405812240, -0.0047842407, -0.0036671310`, all negative. Cumulative `-0.0490325957`. The
  same two-pattern argument gives `p = 0.25`; the mean is negative, so the one-sided conversion
  gives `1 - p/2 = 0.875`.

This arithmetic was cross-checked with `Decimal` in a scratch script
(`scratchpad/task14forward/verify_continuous.py`) and against an independent driver that ran the
real `strategy_view.backtest_strategy` before this test was written.
"""

from __future__ import annotations

import hashlib
import importlib
import itertools
import os
import subprocess
import sys
from collections.abc import Sequence
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from types import ModuleType
from typing import Any, Final

import pytest
from strategy_fixtures import READ_AT, write_strategy_corpus

from openalpha_cn.backtest.strategy_backtest import EQUAL_WEIGHT_ALL_A, SignalDayScores
from openalpha_cn.panel.store import PanelStore
from openalpha_cn.panel_ingest import session_publication_instant
from openalpha_cn.storage.predictions import FilePredictionStore
from openalpha_cn.strategy_registration import (
    RegisteredConfiguration,
    registration_cutoff,
    signal_day_batch,
)
from openalpha_cn.strategy_view import SHANGHAI, SignalDay, strategy_request

ROOT: Final[Path] = Path(__file__).resolve().parents[3]
SCRIPTS: Final[Path] = ROOT / "scripts"
RESEARCH: Final[Path] = SCRIPTS / "research"


def _module(path: Path, name: str) -> ModuleType:
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))
    return importlib.import_module(name)


forward_report = _module(SCRIPTS, "forward_report")
daily = _module(SCRIPTS, "daily_selection")
grid = _module(RESEARCH, "grid")
registry = _module(RESEARCH, "registry")

BENCHMARK_905: Final[str] = "000905.SH"
EXCHANGE: Final[str] = "SZSE"
WINNER: Final[str] = "600000.SH"
SECOND: Final[str] = "600519.SH"
THIRD: Final[str] = "300750.SZ"
UNIVERSE: Final[tuple[str, ...]] = (
    "000001.SZ",
    "000002.SZ",
    "600000.SH",
    "600519.SH",
    "300750.SZ",
    "688981.SH",
    "002415.SZ",
    "601318.SH",
)

CONFIG_BASE: Final[dict[str, Any]] = {
    "components": (("reversal_1d/v1", "raw", Decimal("1")),),
    "combine": "zscore_sum",
    "transform": None,
    "neutralization": None,
    "exchange": EXCHANGE,
    "rebalance_every_sessions": 3,
    "holding_count": 2,
    "buffer_rank": None,
    "max_industry_weight": None,
    "position_capital": Decimal("50000"),
}


# --- a throwaway git repository, the registry's own hermetic pattern -----------------------------


def _git(repo: Path, *args: str, at: datetime | None = None) -> str:
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    env.update(
        {
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_AUTHOR_NAME": "forward-report-test",
            "GIT_AUTHOR_EMAIL": "forward-report-test@example.invalid",
            "GIT_COMMITTER_NAME": "forward-report-test",
            "GIT_COMMITTER_EMAIL": "forward-report-test@example.invalid",
        }
    )
    if at is not None:
        env["GIT_COMMITTER_DATE"] = at.isoformat()
        env["GIT_AUTHOR_DATE"] = at.isoformat()
    result = subprocess.run(
        ["git", *args], cwd=repo, env=env, capture_output=True, text=True, check=True
    )
    return result.stdout


def _commit_file(repo: Path, path: Path, message: str, *, at: datetime) -> None:
    _git(repo, "add", path.relative_to(repo).as_posix())
    _git(repo, "commit", "-q", "-m", message, at=at)


BOUND_FILE: Final[str] = "src/openalpha_cn/strategy.py"
CLOCK0: Final[datetime] = datetime(2026, 9, 26, 8, 0, tzinfo=UTC)


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A throwaway repository with one file under each bound pathspec, `scripts/forward_report.py`
    included -- the fix round's own point: this file must be as bound as `scripts/daily_selection
    .py` is. `registry._imported_package`/`_imported_scripts` are patched at this fixture's own
    `src`/`scripts/research`, the real import stays this checkout's."""
    root = (tmp_path / "repo").resolve()
    root.mkdir()
    _git(root, "init", "-q", "--template=")
    for relative in (
        BOUND_FILE,
        "scripts/research/grid.py",
        "scripts/forward_report.py",
        "pyproject.toml",
        "uv.lock",
    ):
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# fixture\n", encoding="utf-8")
    _commit_file(root, root / BOUND_FILE, "seed", at=CLOCK0)
    for relative in (
        "scripts/research/grid.py",
        "scripts/forward_report.py",
        "pyproject.toml",
        "uv.lock",
    ):
        _git(root, "add", relative)
    _git(root, "commit", "-q", "-m", "seed the rest", at=CLOCK0 + timedelta(seconds=1))
    monkeypatch.setattr(
        registry, "_imported_package", lambda: root / "src" / "openalpha_cn" / "__init__.py"
    )
    monkeypatch.setattr(
        registry,
        "_imported_scripts",
        lambda: (
            root / "scripts" / "research" / "grid.py",
            root / "scripts" / "research" / "registry.py",
        ),
    )
    return root


def _head(repo_path: Path) -> str:
    return _git(repo_path, "rev-parse", "HEAD").strip()


def register_config(
    repo_path: Path, config: dict[str, Any], *, settings: dict[str, Any] | None = None
) -> Path:
    """Write and commit a standing-forward registration in `repo_path`, the same shape the
    holdout's own registration carries (`registry.register`, unchanged)."""
    commit = _head(repo_path)
    path = repo_path / "docs" / "research" / "p6-registration.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    registry.register(
        config,
        forward_report.STANDING_FORWARD_CRITERIA,
        path,
        code_commit=commit,
        settings=grid.protocol_settings() if settings is None else settings,
    )
    _commit_file(repo_path, path, "register", at=CLOCK0 + timedelta(seconds=2))
    return path


# --- registered records, built through strategy_registration.signal_day_batch --------------------


def _request_for(config: dict[str, Any], *, probe_day: date) -> Any:
    """A `StrategyRequest` carrying `config`'s source and spec, for `signal_day_batch` alone --
    it reads only `.source` and `.spec.rebalance_every_sessions`, never `.start`/`.end`, so these
    two dates need only satisfy `strategy_request`'s own ordering, not name real sessions."""
    arguments = {
        key: value
        for key, value in config.items()
        if key not in {"start", "end", "as_of", "position_capital"}
    }
    end = probe_day + timedelta(days=1)
    return strategy_request(
        **arguments,
        position_capital=config.get("position_capital", Decimal("100000")),
        start=probe_day,
        end=end,
        as_of=session_publication_instant(end) + timedelta(hours=1),
    )


def _signal_day(day: date, ranked: Sequence[str]) -> SignalDay:
    """A hand-built `SignalDay` ranking `ranked` top to bottom -- no real factor panel needed,
    `strategy_registration.signal_day_batch` only reads `signal.scores` and the two knowability
    fields."""
    scores = {name: float(len(ranked) - index) for index, name in enumerate(ranked)}
    instant = session_publication_instant(day)
    return SignalDay(
        day=day,
        instant=instant,
        anchor=day,
        position=0,
        scores=SignalDayScores(
            day=day,
            ranked=tuple(ranked),
            scores=scores,
            incomplete=(),
            weights={"reversal_1d/v1@raw": 1.0},
            ic_weights=(),
            model_fit=None,
        ),
        fit=None,
        model_batch=None,
        knowable_through=instant,
        values_consumed=len(ranked),
        industries={},
        refit_day=None,
    )


def registered_batch(
    config: dict[str, Any], registered: RegisteredConfiguration, *, day: date, ranked: Sequence[str]
) -> Any:
    """One day's composite `PredictionBatch`, exactly as the daily command would register it: the
    book trades a session's scores at its own signal instant, so `predicted_at` is exactly that
    instant (`test_a_backtest_reading_the_registered_records_trades_as_the_configuration_would`'s
    own arrangement -- any later instant is look-ahead the book itself refuses)."""
    request = _request_for(config, probe_day=day)
    signal = _signal_day(day, ranked)
    return signal_day_batch(signal, request, registered, predicted_at=signal.instant)


def register_prediction(root: Path, batch: Any, *, calendar: Any, recorded_at: datetime) -> Any:
    store = FilePredictionStore(root / "predictions", clock=lambda: recorded_at)
    return store.put(batch=batch, calendar=calendar, zone=SHANGHAI).record


def prediction_store(root: Path, *, clock_at: datetime) -> FilePredictionStore:
    return FilePredictionStore(root / "predictions", clock=lambda: clock_at)


# --- the main fixture: three signal days, two rebalances, a sell ---------------------------------

AS_OF: Final[datetime] = datetime(2026, 1, 19, 8, 0, tzinfo=UTC)
"""After every one of the three records' own (conservative) outcome deadlines -- the third
record's, 2026-01-19T07:00Z, is the latest -- while the book itself only ever reads through
2026-01-16, the panel's real last session."""


def build_main_fixture(
    tmp_path: Path, repo_path: Path
) -> tuple[Path, Path, RegisteredConfiguration, tuple[date, ...]]:
    """The panel, the registration and the three registered records for the hand-computed ledger.

    Returns `(panel_root, registration_path, registered, sessions)`.
    """
    panel = write_strategy_corpus(tmp_path)
    sessions = panel.sessions
    config = dict(CONFIG_BASE, start=sessions[0])
    registration_path = register_config(repo_path, config)
    registered = RegisteredConfiguration(
        config_id=grid.config_id(config),
        registration_sha256=hashlib.sha256(registration_path.read_bytes()).hexdigest(),
        code_commit=_head(repo_path),
        seed=int(grid.protocol_settings()["random_seed"]),
    )

    plan = (
        (
            sessions[0],
            (WINNER, SECOND, *[name for name in UNIVERSE if name not in (WINNER, SECOND)]),
        ),
        (sessions[3], (WINNER, THIRD, *[name for name in UNIVERSE if name not in (WINNER, THIRD)])),
        (sessions[6], (WINNER, THIRD, *[name for name in UNIVERSE if name not in (WINNER, THIRD)])),
    )
    store = PanelStore(tmp_path / "panel")
    for day, ranked in plan:
        batch = registered_batch(config, registered, day=day, ranked=ranked)
        calendar = daily._outcome_calendar(store, EXCHANGE, day, AS_OF)
        register_prediction(tmp_path, batch, calendar=calendar, recorded_at=batch.as_of)
    return tmp_path, registration_path, registered, sessions


# --- Step 1: the hand-computed continuous book ----------------------------------------------------


def test_the_continuous_books_periods_and_statistics_match_hand_computation(
    tmp_path: Path, repo: Path
) -> None:
    panel_root, registration_path, registered, sessions = build_main_fixture(tmp_path, repo)
    store = PanelStore(panel_root / "panel")
    store_predictions = prediction_store(panel_root, clock_at=AS_OF)

    result = forward_report.forward_report(
        store, store_predictions, registration=registration_path, repo=repo, as_of=AS_OF
    )

    assert result.excluded == ()
    periods = result.backtest.periods
    assert [period.start for period in periods] == [sessions[0], sessions[3], sessions[6]]
    assert [period.end for period in periods] == [sessions[3], sessions[6], sessions[9]]

    expected = [
        (Decimal("0.0737831000"), Decimal("0.0012169000"), ("600000.SH", "600519.SH")),
        (Decimal("0.0986558645"), Decimal("0.0014574638"), ("300750.SZ", "600000.SH")),
        (Decimal("0.0890043138"), Decimal("0"), ("300750.SZ", "600000.SH")),
    ]
    for period, (net, cost, holdings) in zip(periods, expected, strict=True):
        assert period.net_return == net
        assert period.cost == cost
        assert period.gross_return == net + cost
        assert period.holdings == holdings

    assert [len(period.fills) for period in periods] == [2, 2, 0]
    assert periods[1].fills[0].side == "sell" and periods[1].fills[0].subject == SECOND
    assert periods[1].fills[1].side == "buy" and periods[1].fills[1].subject == THIRD

    stat_905 = result.statistics[BENCHMARK_905]
    stat_ew = result.statistics[EQUAL_WEIGHT_ALL_A]
    assert stat_905.cumulative_net_excess == Decimal("0.2219325602")
    assert stat_905.p_value == pytest.approx(0.25)
    assert stat_905.p_value_one_sided == pytest.approx(0.125)
    assert stat_905.exact is True
    assert stat_905.sign_patterns == 8
    assert stat_ew.cumulative_net_excess == Decimal("-0.0490325957")
    assert stat_ew.p_value == pytest.approx(0.25)
    assert stat_ew.p_value_one_sided == pytest.approx(0.875)

    assert result.config_id == registered.config_id


# --- Step 2: overlapping periods -- consecutive daily registrations ------------------------------


def test_consecutive_daily_registrations_give_the_continuous_books_own_non_overlapping_periods(
    tmp_path: Path, repo: Path
) -> None:
    """Four consecutive signal days, `rebalance_every_sessions=1`: the same two names held
    throughout (no trade past day 0), so this isolates the *period* mechanics fix-round point 2
    asks for, rather than re-deriving a second fee ledger. Every period's end is the next one's
    start -- share no session -- and the statistics see all of them, not a re-summed subset of
    independently priced predictions the way the first design did."""
    panel = write_strategy_corpus(tmp_path)
    sessions = panel.sessions
    config = dict(CONFIG_BASE, rebalance_every_sessions=1, start=sessions[0])
    registration_path = register_config(repo, config)
    registered = RegisteredConfiguration(
        config_id=grid.config_id(config),
        registration_sha256="0" * 64,
        code_commit=_head(repo),
        seed=int(grid.protocol_settings()["random_seed"]),
    )
    store = PanelStore(tmp_path / "panel")
    ranked = (WINNER, SECOND, *[name for name in UNIVERSE if name not in (WINNER, SECOND)])
    as_of = READ_AT
    for day in sessions[:4]:
        batch = registered_batch(config, registered, day=day, ranked=ranked)
        calendar = daily._outcome_calendar(store, EXCHANGE, day, as_of)
        register_prediction(tmp_path, batch, calendar=calendar, recorded_at=batch.as_of)

    store_predictions = prediction_store(tmp_path, clock_at=as_of)
    result = forward_report.forward_report(
        store, store_predictions, registration=registration_path, repo=repo, as_of=as_of
    )

    periods = result.backtest.periods
    assert len(periods) == 4
    for earlier, later in itertools.pairwise(periods):
        assert earlier.end == later.start
    assert len({period.start for period in periods} | {period.end for period in periods}) == 5
    for _name, stat in result.statistics.items():
        assert stat.result["period_count"] == 4
        assert stat.result["excluded_incomplete_periods"] == 0


# --- exclusions: never silent --------------------------------------------------------------------


def test_a_record_registered_at_or_after_its_cutoff_is_excluded_and_listed(
    tmp_path: Path, repo: Path
) -> None:
    panel = write_strategy_corpus(tmp_path)
    sessions = panel.sessions
    config = dict(CONFIG_BASE, start=sessions[0])
    registration_path = register_config(repo, config)
    registered = RegisteredConfiguration(
        config_id=grid.config_id(config),
        registration_sha256="0" * 64,
        code_commit=_head(repo),
        seed=int(grid.protocol_settings()["random_seed"]),
    )
    store = PanelStore(tmp_path / "panel")
    day = sessions[0]
    ranked = (WINNER, SECOND, *[n for n in UNIVERSE if n not in (WINNER, SECOND)])
    batch = registered_batch(config, registered, day=day, ranked=ranked)
    calendar = daily._outcome_calendar(store, EXCHANGE, day, READ_AT)
    late = registration_cutoff(calendar, day) + timedelta(minutes=1)
    register_prediction(tmp_path, batch, calendar=calendar, recorded_at=late)

    store_predictions = prediction_store(tmp_path, clock_at=READ_AT)
    with pytest.raises(forward_report.ForwardReportError, match="excluded"):
        forward_report.forward_report(
            store, store_predictions, registration=registration_path, repo=repo, as_of=READ_AT
        )


def test_a_record_whose_outcome_is_not_yet_knowable_is_excluded_though_its_signal_is_before_as_of(
    tmp_path: Path, repo: Path
) -> None:
    """`day6`'s signal (2026-01-13) is well before `as_of` (2026-01-17); its own outcome deadline
    (`~2026-01-19`, `signal_day_batch`'s horizon convention) is not. The minor fix-round point:
    a record can be registered in time and still excluded, because "in time" and "knowable" are
    two different clocks."""
    panel = write_strategy_corpus(tmp_path)
    sessions = panel.sessions
    config = dict(CONFIG_BASE, start=sessions[0])
    registration_path = register_config(repo, config)
    registered = RegisteredConfiguration(
        config_id=grid.config_id(config),
        registration_sha256="0" * 64,
        code_commit=_head(repo),
        seed=int(grid.protocol_settings()["random_seed"]),
    )
    store = PanelStore(tmp_path / "panel")
    as_of = READ_AT
    winners = (WINNER, SECOND, *[n for n in UNIVERSE if n not in (WINNER, SECOND)])
    for day in (sessions[0], sessions[3], sessions[6]):
        batch = registered_batch(config, registered, day=day, ranked=winners)
        calendar = daily._outcome_calendar(store, EXCHANGE, day, as_of)
        register_prediction(tmp_path, batch, calendar=calendar, recorded_at=batch.as_of)

    store_predictions = prediction_store(tmp_path, clock_at=as_of)
    result = forward_report.forward_report(
        store, store_predictions, registration=registration_path, repo=repo, as_of=as_of
    )

    assert len(result.excluded) == 1
    excluded = result.excluded[0]
    assert excluded.signal_day == sessions[6]
    assert excluded.signal_day < as_of.astimezone(SHANGHAI).date()
    assert excluded.reason == forward_report.OUTCOME_NOT_YET_KNOWABLE
    # day3's own outcome (its exit at session 7, well before as_of) is knowable, so it is admitted
    # too and the book continues one more period on it -- day6 is excluded, not the whole tail.
    periods = result.backtest.periods
    assert [period.start for period in periods] == [sessions[0], sessions[3]]
    assert [period.end for period in periods] == [sessions[3], sessions[6]]
    assert periods[0].net_return == Decimal("0.0737831000")


def test_a_record_declaring_a_different_configuration_is_excluded_and_listed(
    tmp_path: Path, repo: Path
) -> None:
    """A record whose declaration names a `config_id` this registration did not write -- a stale
    registration's record left in a shared store -- is not this registration's evidence."""
    panel = write_strategy_corpus(tmp_path)
    sessions = panel.sessions
    config = dict(CONFIG_BASE, start=sessions[0])
    registration_path = register_config(repo, config)
    registered = RegisteredConfiguration(
        config_id=grid.config_id(config),
        registration_sha256="0" * 64,
        code_commit=_head(repo),
        seed=int(grid.protocol_settings()["random_seed"]),
    )
    foreign = RegisteredConfiguration(
        config_id="f" * 64, registration_sha256="0" * 64, code_commit=_head(repo), seed=1
    )
    store = PanelStore(tmp_path / "panel")
    as_of = READ_AT
    winners = (WINNER, SECOND, *[n for n in UNIVERSE if n not in (WINNER, SECOND)])
    day = sessions[0]
    foreign_batch = registered_batch(config, foreign, day=day, ranked=winners)
    calendar = daily._outcome_calendar(store, EXCHANGE, day, as_of)
    register_prediction(tmp_path, foreign_batch, calendar=calendar, recorded_at=foreign_batch.as_of)

    store_predictions = prediction_store(tmp_path, clock_at=as_of)
    with pytest.raises(forward_report.ForwardReportError, match="excluded"):
        forward_report.forward_report(
            store, store_predictions, registration=registration_path, repo=repo, as_of=as_of
        )

    # A second copy of the store also carrying the registration's own record: the foreign one is
    # excluded and listed, the registration's own is chained.
    own_batch = registered_batch(config, registered, day=day, ranked=winners)
    register_prediction(tmp_path, own_batch, calendar=calendar, recorded_at=own_batch.as_of)
    result = forward_report.forward_report(
        store, store_predictions, registration=registration_path, repo=repo, as_of=as_of
    )
    assert {excluded.reason for excluded in result.excluded} == {
        forward_report.FOREIGN_TO_THE_REGISTRATION
    }
    assert len(result.backtest.periods) == 1


# --- binding: mandatory, config_id and settings checked, this file bound too ---------------------


def test_registration_and_repo_are_mandatory(tmp_path: Path, repo: Path) -> None:
    write_strategy_corpus(tmp_path)
    store = PanelStore(tmp_path / "panel")
    store_predictions = prediction_store(tmp_path, clock_at=READ_AT)
    with pytest.raises(TypeError):
        forward_report.forward_report(store, store_predictions, as_of=READ_AT)  # type: ignore[call-arg]


def test_a_registration_whose_config_id_does_not_match_its_own_config_is_refused(
    tmp_path: Path, repo: Path
) -> None:
    panel = write_strategy_corpus(tmp_path)
    sessions = panel.sessions
    config = dict(CONFIG_BASE, start=sessions[0])
    registration_path = register_config(repo, config)
    payload = registration_path.read_text(encoding="utf-8")
    doctored = payload.replace(grid.config_id(config), "0" * 64)
    assert doctored != payload
    registration_path.write_text(doctored, encoding="utf-8")
    _commit_file(repo, registration_path, "doctor the config_id", at=CLOCK0 + timedelta(seconds=3))

    store = PanelStore(tmp_path / "panel")
    store_predictions = prediction_store(tmp_path, clock_at=READ_AT)
    with pytest.raises(forward_report.ForwardReportError, match="config_id"):
        forward_report.forward_report(
            store, store_predictions, registration=registration_path, repo=repo, as_of=READ_AT
        )


def test_a_registration_with_no_usable_measurement_settings_is_refused(
    tmp_path: Path, repo: Path
) -> None:
    panel = write_strategy_corpus(tmp_path)
    sessions = panel.sessions
    config = dict(CONFIG_BASE, start=sessions[0])
    registration_path = register_config(
        repo, config, settings={"excess_benchmark": EQUAL_WEIGHT_ALL_A}
    )

    store = PanelStore(tmp_path / "panel")
    store_predictions = prediction_store(tmp_path, clock_at=READ_AT)
    with pytest.raises(forward_report.ForwardReportError, match="settings"):
        forward_report.forward_report(
            store, store_predictions, registration=registration_path, repo=repo, as_of=READ_AT
        )


def test_a_registration_whose_bound_code_has_not_moved_is_not_refused(
    tmp_path: Path, repo: Path
) -> None:
    panel_root, registration_path, _registered, _sessions = build_main_fixture(tmp_path, repo)
    store = PanelStore(panel_root / "panel")
    store_predictions = prediction_store(panel_root, clock_at=AS_OF)

    forward_report.forward_report(
        store, store_predictions, registration=registration_path, repo=repo, as_of=AS_OF
    )  # must not raise


def test_forward_reports_own_file_is_bound_the_way_daily_selections_own_file_is(
    tmp_path: Path, repo: Path
) -> None:
    """`scripts/forward_report.py` must be as bound as `scripts/daily_selection.py` -- a change to
    *only* this file after registration refuses the report, the same way a change to `src/` does."""
    panel_root, registration_path, _registered, _sessions = build_main_fixture(tmp_path, repo)
    (repo / "scripts" / "forward_report.py").write_text("# moved\n", encoding="utf-8")
    _commit_file(
        repo,
        repo / "scripts" / "forward_report.py",
        "move this file",
        at=CLOCK0 + timedelta(seconds=3),
    )

    store = PanelStore(panel_root / "panel")
    store_predictions = prediction_store(panel_root, clock_at=AS_OF)
    with pytest.raises(registry.SourceChangedError):
        forward_report.forward_report(
            store, store_predictions, registration=registration_path, repo=repo, as_of=AS_OF
        )


# --- read_registered_predictions: the race with a concurrent write -------------------------------


class _RacingStore:
    """A store that names a record `list_ids` promises but `get` no longer holds -- a write that
    raced a concurrent removal between the two calls."""

    def __init__(self, held: dict[str, Any]) -> None:
        self._held = held

    def list_ids(self) -> tuple[str, ...]:
        return (*sorted(self._held), "prd_" + "a" * 24)

    def get(self, record_id: str) -> Any:
        return self._held.get(record_id)


def test_a_record_removed_between_list_ids_and_get_is_skipped_not_raised(
    tmp_path: Path, repo: Path
) -> None:
    panel = write_strategy_corpus(tmp_path)
    sessions = panel.sessions
    config = dict(CONFIG_BASE, start=sessions[0])
    registered = RegisteredConfiguration(
        config_id=grid.config_id(config),
        registration_sha256="0" * 64,
        code_commit=_head(repo),
        seed=1,
    )
    day = sessions[0]
    winners = (WINNER, SECOND, *[n for n in UNIVERSE if n not in (WINNER, SECOND)])
    batch = registered_batch(config, registered, day=day, ranked=winners)
    store = PanelStore(tmp_path / "panel")
    calendar = daily._outcome_calendar(store, EXCHANGE, day, READ_AT)
    held = register_prediction(tmp_path, batch, calendar=calendar, recorded_at=batch.as_of)

    racing = _RacingStore({held.record_id: held})
    records = forward_report.read_registered_predictions(racing)

    assert records == (held,)


# --- a runnable entry: main() -----------------------------------------------------------------


def test_main_writes_a_json_report_under_the_runtime_directory(tmp_path: Path, repo: Path) -> None:
    panel_root, registration_path, registered, _sessions = build_main_fixture(tmp_path, repo)

    exit_code = forward_report.main(
        [
            "--runtime-dir",
            str(panel_root),
            "--registration",
            str(registration_path),
            "--repo",
            str(repo),
            "--as-of",
            AS_OF.isoformat(),
            "--json",
        ]
    )

    assert exit_code == 0
    reports = sorted((panel_root / "reports").glob("forward-*.json"))
    assert len(reports) == 1
    import json

    payload = json.loads(reports[0].read_text(encoding="utf-8"))
    assert payload["config_id"] == registered.config_id
    assert len(payload["periods"]) == 3
    assert payload["excluded"] == []
    assert payload["statistics"][BENCHMARK_905]["cumulative_net_excess"] == "0.2219325602"
