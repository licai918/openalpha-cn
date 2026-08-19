"""`--year` scopes three datasets that partition by three different things (`V2-P4-059/060`).

The acceptance review drove the real CLI over a synthetic A-share market of 5,545 securities
and found the registry read taking `--year` literally:

    $ openalpha factor build --factor reversal_1d/v1 --tier raw \\
        --as-of 2026-02-09T09:00:00+00:00 --max-staleness-days 3 --year 2026 --json
    {"coverage": {"raw": {"computed": 11, "insufficient_history": 1}}, "subject_count": 12,
     "tier": "raw", "universe_counts": [12]}                                        exit=0

Eleven securities screened, written and published out of a store holding 5,545 -- because
`stock_basic` partitions by **lifecycle** year, so the 2026 partition is "the securities whose
life changed in 2026" and not "the market in 2026". The calendar and the price panel partition
by the year the data is *about*, which is what `--year` means for them and what makes one flag
over three datasets a question with three different answers. `funnel 12 listed -> 11 scored`
was the only trace, and it is a count of the read rather than of the store.

The same partitioning has a second face. A security that lists in 1996 and delists in 2026 puts
its listing row in the 1996 partition and its delisting row in the 2026 one, so a `--year 2026`
read finds a delisting with no listing -- which `stock_universe_from_panel_rows` correctly
refuses as a partial read. That refusal was reaching `_panel_command` as an *unanticipated*
exception, so the user was told the command was defective and its message -- the one sentence
that says what to do -- was withheld on the grounds that an unanticipated frame can hold a
credential. Both defects are the same root, and the fix is that the registry read resolves its
own partitions instead of borrowing the price panel's.

## Why the market here is 5,545 rather than a handful

`V2-P4-004` measured the real A-share market at 5,545 listed on 2026-08-14, and the defect is
invisible below market scale: at eight securities "the registry read returned four" reads as a
fixture detail. The cost is one panel build, module-scoped and copied per test.

Only `cli._panel_transport` is doubled -- the command's own declared seam. The provider's
credential resolution, its request envelope, its point-in-time filter, its projection, every
`panel_ingest` write guard and every readiness rule run for real.

## Two registry vintages out of one market

`stock_basic` is `ClockStrategy.calendar_static`, so a lifecycle row becomes knowable at
midnight on its own event date. A registry fetched before 2026-02-11 therefore does not carry
the 2026-02-11 delisting at all, and one fetched after it does. That is what separates the two
defects on one market: `BEFORE` is the store `V2-P4-059` was found on, `AFTER` the store
`V2-P4-060` was found on.
"""

from __future__ import annotations

import json
import shutil
from collections.abc import Iterator, Mapping, Sequence
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, Final

import pytest
from typer.testing import CliRunner

from openalpha_cn import cli
from openalpha_cn.cli import PanelExit, app
from openalpha_cn.domain.adjustment import ADJ_FACTOR_DATASET
from openalpha_cn.domain.daily_prices import DAILY_BASIC_DATASET, DAILY_DATASET
from openalpha_cn.domain.price_limits import PRICE_LIMIT_DATASET, SUSPENSION_DATASET
from openalpha_cn.domain.stock_universe import STOCK_BASIC_DATASET
from openalpha_cn.domain.trading_calendar import TRADING_CALENDAR_DATASET
from openalpha_cn.panel_ingest import (
    split_panel_batch_by_year,
    write_panel_batch,
    write_trading_calendar,
)
from openalpha_cn.panel_view import panel_store
from openalpha_cn.providers.base import ProviderRequest
from openalpha_cn.providers.tushare import TushareProvider

runner = CliRunner()

TOKEN: Final[str] = "sk-universe-scope-token-must-not-leak-40219"
EXCHANGE: Final[str] = "SSE"
YEAR: Final[int] = 2026

WHOLE_MARKET: Final[int] = 5_545
"""`V2-P4-004`'s measurement of the real A-share market on 2026-08-14, reused verbatim so the
number in this file's assertions is a measured market rather than a round one."""

SESSIONS: Final[tuple[date, ...]] = (date(2026, 2, 6), date(2026, 2, 9))
"""Two open sessions, which is exactly `reversal_1d`'s `max_window_sessions`. A third would
buy nothing and cost one more whole-market cross section in three datasets."""

HALT_SESSION: Final[date] = SESSIONS[0]
DELISTING_DAY: Final[date] = date(2026, 2, 11)
"""Mid-window and **not** a session: `delist_date` is exclusive, so a security's last session
is before it. Chosen after the price horizon so one market yields both registry vintages."""

MID_WINDOW_LISTING: Final[date] = SESSIONS[-1]
EARLY_2026_LISTING: Final[date] = date(2026, 1, 5)

BEFORE: Final[datetime] = datetime(2026, 2, 10, 8, 0, tzinfo=UTC)
"""16:00 Asia/Shanghai on 2026-02-10. Sessions publish through 2026-02-09 (`_build_sessions`
stops a day back), and the 2026-02-11 delisting is not knowable until 2026-02-10T16:00Z."""

AFTER: Final[datetime] = datetime(2026, 2, 12, 4, 0, tzinfo=UTC)

CODES: Final[tuple[str, ...]] = tuple(f"{600000 + index:06d}.SH" for index in range(WHOLE_MARKET))
DELISTED: Final[tuple[str, ...]] = CODES[:3]
"""Listed in the 1990s, delisted mid-window. Their *listing* rows live in 1996/1997/1998
partitions, which is what makes a `--year 2026` read of them a partial read."""

NEW_2026: Final[tuple[str, ...]] = CODES[3:15]
"""The twelve with a 2026 lifecycle event of their own -- the entire universe the defect saw."""

INCUMBENTS: Final[tuple[str, ...]] = CODES[15:]
HALTED: Final[frozenset[str]] = frozenset(INCUMBENTS[:20])
"""Full-day halt on `HALT_SESSION`: an `S` row with no `suspend_timing` and no bar beside it.
Inside `reversal_1d`'s two-session window, so the halt has a visible consequence in the census
rather than being decoration -- and it gives `write_daily_panel`'s explained-share guard a real
whole-market corpus to run against."""

FIRST_LIFECYCLE_YEAR: Final[int] = 1991
LAST_INCUMBENT_YEAR: Final[int] = 2025

CALENDAR_FIELDS = ["exchange", "cal_date", "is_open", "pretrade_date"]
REGISTRY_FIELDS = [
    "ts_code",
    "name",
    "exchange",
    "market",
    "list_status",
    "list_date",
    "delist_date",
]
FACTOR_FIELDS = ["ts_code", "trade_date", "adj_factor"]
BAR_FIELDS = [
    "ts_code",
    "trade_date",
    "open",
    "high",
    "low",
    "close",
    "pre_close",
    "pct_chg",
    "vol",
    "amount",
]
VALUATION_EXTRA_FIELDS = [
    "turnover_rate",
    "turnover_rate_f",
    "volume_ratio",
    "pe",
    "pe_ttm",
    "pb",
    "ps",
    "ps_ttm",
    "dv_ratio",
    "dv_ttm",
    "total_share",
    "float_share",
    "free_share",
    "total_mv",
    "circ_mv",
]
VALUATION_FIELDS = ["ts_code", "trade_date", "close", *VALUATION_EXTRA_FIELDS]
HALT_FIELDS = ["ts_code", "trade_date", "suspend_type", "suspend_timing"]
LIMIT_FIELDS = ["ts_code", "trade_date", "up_limit", "down_limit"]


def _lifecycle() -> dict[str, tuple[date, date | None]]:
    """Every security's `(listed_on, delisted_on)`, built once.

    A dict rather than three `tuple.index` lookups per call: at 5,545 securities the linear
    form is quadratic and was the whole cost of this module's fixture, which is a property of
    the double rather than of anything under test.
    """
    span = LAST_INCUMBENT_YEAR - FIRST_LIFECYCLE_YEAR + 1
    table: dict[str, tuple[date, date | None]] = {}
    for index, code in enumerate(DELISTED):
        table[code] = (date(1996 + index, 5, 6), DELISTING_DAY)
    for index, code in enumerate(NEW_2026):
        listed = MID_WINDOW_LISTING if index == len(NEW_2026) - 1 else EARLY_2026_LISTING
        table[code] = (listed, None)
    for index, code in enumerate(INCUMBENTS):
        table[code] = (date(FIRST_LIFECYCLE_YEAR + (index % span), 3, 4), None)
    return table


LIFECYCLE: Final[Mapping[str, tuple[date, date | None]]] = _lifecycle()
ORDINAL: Final[Mapping[str, int]] = {code: index for index, code in enumerate(CODES)}


def _trades_on(code: str, day: date) -> bool:
    listed, gone = LIFECYCLE[code]
    if day < listed:
        return False
    if gone is not None and day >= gone:
        return False
    return not (day == HALT_SESSION and code in HALTED)


def _compact(day: date) -> str:
    return day.strftime("%Y%m%d")


def _response(fields: Sequence[str], items: Sequence[Sequence[Any]]) -> dict[str, Any]:
    return {
        "code": 0,
        "msg": "",
        "data": {"fields": list(fields), "items": [list(i) for i in items], "has_more": False},
    }


def _close(code: str, day: date) -> float:
    """Distinct per security and rising per session, so `reversal_1d` has a real ratio to
    divide rather than a column of equal closes that any bug would reproduce."""
    return 10.0 + (ORDINAL[code] % 97) * 0.1 + (day - SESSIONS[0]).days * 0.01


class MarketTransport:
    """Every dataset `panel build` asks for, over the synthetic market this module declares."""

    def __init__(self) -> None:
        self.payloads: list[dict[str, Any]] = []

    def post(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.payloads.append(payload)
        api_name = str(payload["api_name"])
        params: Mapping[str, str] = payload["params"]
        if api_name == TRADING_CALENDAR_DATASET:
            year = int(params["start_date"][:4])
            open_days = {day for day in SESSIONS if day.year == year}
            items: list[list[Any]] = []
            previous: str | None = None
            day = date(year, 1, 1)
            while day <= date(year, 12, 31):
                items.append(
                    [params["exchange"], _compact(day), 1 if day in open_days else 0, previous]
                )
                if day in open_days:
                    previous = _compact(day)
                day += timedelta(days=1)
            return _response(CALENDAR_FIELDS, items)
        if api_name == STOCK_BASIC_DATASET:
            rows: list[list[Any]] = []
            for code in CODES:
                listed, gone = LIFECYCLE[code]
                rows.append(
                    [
                        code,
                        code,
                        EXCHANGE,
                        "主板",
                        "D" if gone is not None else "L",
                        _compact(listed),
                        None if gone is None else _compact(gone),
                    ]
                )
            return _response(REGISTRY_FIELDS, rows)
        text = params["trade_date"]
        session = date(int(text[:4]), int(text[4:6]), int(text[6:]))
        live = [code for code in CODES if _trades_on(code, session)]
        if api_name == ADJ_FACTOR_DATASET:
            return _response(FACTOR_FIELDS, [[c, _compact(session), 1.0] for c in live])
        if api_name == DAILY_DATASET:
            return _response(
                BAR_FIELDS,
                [
                    [c, _compact(session), *([_close(c, session)] * 5), 0.0, 1000.0, 10000.0]
                    for c in live
                ],
            )
        if api_name == DAILY_BASIC_DATASET:
            return _response(
                VALUATION_FIELDS,
                [
                    [
                        c,
                        _compact(session),
                        _close(c, session),
                        *([1.0] * len(VALUATION_EXTRA_FIELDS)),
                    ]
                    for c in live
                ],
            )
        if api_name == SUSPENSION_DATASET:
            if session != HALT_SESSION:
                return _response(HALT_FIELDS, [])
            return _response(
                HALT_FIELDS, [[c, _compact(session), "S", None] for c in sorted(HALTED)]
            )
        if api_name == PRICE_LIMIT_DATASET:
            return _response(
                LIMIT_FIELDS,
                [
                    [c, _compact(session), _close(c, session) * 1.1, _close(c, session) * 0.9]
                    for c in live
                ],
            )
        raise AssertionError(f"the CLI asked for an unscripted dataset: {api_name}")


def _build(runtime: Path, target: str) -> Any:
    return runner.invoke(
        app,
        [
            "panel",
            "build",
            "--dataset",
            target,
            "--runtime-dir",
            str(runtime),
            "--exchange",
            EXCHANGE,
            "--year",
            str(YEAR),
            "--json",
        ],
    )


@pytest.fixture(scope="module")
def markets(tmp_path_factory: pytest.TempPathFactory) -> Iterator[tuple[Path, Path]]:
    """The two registry vintages, built once for the module and copied per test.

    Module scope because the panel build is the expensive half -- two sessions of three
    whole-market cross sections, plus a 5,545-row registry split across 36 lifecycle years, and
    the registry twice -- and every test wants the same store. `PanelStore` replaces a partition
    whole and `factor build` writes into it, so each test gets its own copy rather than sharing
    one; a copy is a few megabytes and milliseconds, which is what makes the module-scoped build
    affordable.

    The second vintage is a re-fetch of `stock_basic` alone, at a clock past the delisting day.
    It is admitted by `_refuse_to_drop_stored_subjects` because it is strictly larger -- the same
    twelve listings plus three delisting rows -- which is what "the registry only grows within a
    past year" means, and it is how a real store acquires a termination.
    """
    monkeypatch = pytest.MonkeyPatch()
    scripted = MarketTransport()
    clock = {"now": BEFORE}
    monkeypatch.setenv("TUSHARE_TOKEN", TOKEN)
    monkeypatch.setattr(cli, "_panel_transport", lambda: scripted)
    monkeypatch.setattr(cli, "_panel_clock", lambda: clock["now"])

    before = tmp_path_factory.mktemp("before")
    for target in (TRADING_CALENDAR_DATASET, STOCK_BASIC_DATASET, "price"):
        result = _build(before, target)
        assert result.exit_code == PanelExit.ok, result.stderr

    after = tmp_path_factory.mktemp("after-parent") / "after"
    shutil.copytree(before, after)
    clock["now"] = AFTER
    result = _build(after, STOCK_BASIC_DATASET)
    assert result.exit_code == PanelExit.ok, result.stderr

    yield before, after
    monkeypatch.undo()


@pytest.fixture
def before_the_delisting(markets: tuple[Path, Path], tmp_path: Path) -> Path:
    runtime = tmp_path / "runtime"
    shutil.copytree(markets[0], runtime)
    return runtime


def _factor_build(runtime: Path, as_of: str, *extra: str) -> Any:
    return runner.invoke(
        app,
        [
            "factor",
            "build",
            "--factor",
            "reversal_1d/v1",
            "--tier",
            "raw",
            "--as-of",
            as_of,
            "--max-staleness-days",
            "3",
            "--runtime-dir",
            str(runtime),
            "--json",
            *extra,
        ],
    )


@pytest.fixture
def interrupted_backfill(tmp_path: Path) -> Path:
    """A store whose registry holds the newest lifecycle year and none of the ones beneath it.

    What an interrupted backfill leaves, and the one shape the widening above cannot rescue:
    there is no history in the store to read, so the three securities that died in 2026 really
    do arrive as delistings with no listing. Seeded partition by partition rather than through
    `panel build`, because that command writes **every** lifecycle year in one call and so can
    never produce this by itself.

    Small and hand-built: this is about which sentence reaches the user, not about scale, and it
    needs no price panel at all -- the registry read is the second thing `_computed` does.
    """
    runtime = tmp_path / "runtime"
    store = panel_store(runtime)
    provider = TushareProvider(token=TOKEN, transport=MarketTransport(), clock=lambda: AFTER)
    write_trading_calendar(
        store,
        provider.fetch_panel(
            ProviderRequest(dataset=TRADING_CALENDAR_DATASET, as_of=AFTER, subjects=(EXCHANGE,))
        ),
    )
    registry = provider.fetch_panel(ProviderRequest(dataset=STOCK_BASIC_DATASET, as_of=AFTER))
    for year, yearly in split_panel_batch_by_year(registry):
        if year == YEAR:
            write_panel_batch(store, yearly, year=year)
    return runtime


@pytest.fixture(scope="module")
def built_before(markets: tuple[Path, Path], tmp_path_factory: pytest.TempPathFactory) -> Any:
    """`factor build --year 2026` on the pre-delisting registry, run once for the module.

    A build over 5,545 securities is real work -- two whole-market cross sections and one
    readiness assessment per lifecycle year -- and the tests below make separate statements
    about one invocation's result rather than each provoking their own.
    """
    runtime = tmp_path_factory.mktemp("built-before") / "runtime"
    shutil.copytree(markets[0], runtime)
    return _factor_build(runtime, "2026-02-09T09:00:00+00:00", "--year", str(YEAR))


@pytest.fixture(scope="module")
def built_after(markets: tuple[Path, Path], tmp_path_factory: pytest.TempPathFactory) -> Any:
    """The same, on the registry vintage that carries the mid-window delisting."""
    runtime = tmp_path_factory.mktemp("built-after") / "runtime"
    shutil.copytree(markets[1], runtime)
    return _factor_build(runtime, "2026-02-11T09:00:00+00:00", "--year", str(YEAR))


# --- the market the store actually holds ---------------------------------------------------


def test_the_stored_registry_really_does_span_thirty_six_lifecycle_years(
    before_the_delisting: Path,
) -> None:
    """The premise of both defects, measured rather than assumed.

    `panel build --dataset stock_basic --year 2026` writes **every** lifecycle year, because
    the endpoint has no date filter and `write_stock_universe` splits one response into one
    partition per year (`cli._UNPINNED_PARTITION_YEAR_TARGETS`). So the narrowing below is
    entirely on the read side: the store holds the whole market either way.
    """
    years = panel_store(before_the_delisting).registered_years(STOCK_BASIC_DATASET)

    assert years[0] == FIRST_LIFECYCLE_YEAR
    assert years[-1] == YEAR
    assert len(years) == YEAR - FIRST_LIFECYCLE_YEAR + 1


# --- V2-P4-059: the silent narrowing -------------------------------------------------------


def test_a_single_year_build_screens_the_market_the_store_holds_not_that_years_listings(
    built_before: Any,
) -> None:
    """The defect, closed. Against `ae91ed2` this exact invocation exited **0** having scored
    eleven of 5,545 securities: `universe_counts: [12]`, `subject_count: 12`, and a published
    shortlist cut from those eleven. The registry read now resolves its own lifecycle years
    instead of borrowing the one `--year` gave the calendar and the price panel.
    """
    assert built_before.exit_code == PanelExit.ok, built_before.stderr
    payload = json.loads(built_before.stdout)

    assert payload["universe_counts"] == [WHOLE_MARKET]
    assert payload["subject_count"] == WHOLE_MARKET
    assert sum(payload["coverage"]["raw"].values()) == WHOLE_MARKET


def test_the_halted_and_the_newly_listed_are_the_only_names_without_a_value(
    built_before: Any,
) -> None:
    """The census, in full, so "the universe got bigger" cannot be satisfied by a bigger number
    with nothing behind it.

    Twenty-one of 5,545 have fewer than `reversal_1d`'s two sessions: the twenty halted for the
    whole of 2026-02-06 -- an `S` row with no `suspend_timing`, so the panel holds no bar for
    them and the halt corpus is what explains its absence -- and the one that listed on
    2026-02-09 and has only that day. Every other name has both closes and a value.
    """
    assert built_before.exit_code == PanelExit.ok, built_before.stderr

    assert json.loads(built_before.stdout)["coverage"]["raw"] == {
        "computed": WHOLE_MARKET - len(HALTED) - 1,
        "insufficient_history": len(HALTED) + 1,
    }


# --- V2-P4-060: the swallowed reason -------------------------------------------------------


def test_a_mid_window_delisting_does_not_reach_the_user_as_an_unhandled_exception(
    built_after: Any,
) -> None:
    """The defect, closed. Against `ae91ed2` this invocation exited **5** with

        `factor build` did not finish: it raised an unhandled StockUniverseError. ... The
        exception's own message is withheld because an unanticipated failure can carry
        whatever the frame it escaped was holding, including the credential

    which tells a user holding an ordinary market -- every market delists every year -- that
    the command is defective and checked nothing. The withholding is right and stays; what was
    wrong is that this failure was unanticipated. It no longer happens at all, because the read
    that produced the orphan delisting now reads the listing years too.
    """
    assert built_after.exit_code != PanelExit.internal_error, built_after.stderr
    assert "unhandled" not in built_after.stderr
    assert built_after.exit_code == PanelExit.ok, built_after.stderr


def test_a_delisted_security_is_scored_not_in_universe_rather_than_taking_the_build_down(
    built_after: Any,
) -> None:
    """What README already promises for a delisted name, now reachable.

    Without `--subject` the subjects are every code the stored registry knows **including the
    delisted ones**, while the universe is that day's listed cross section -- so a dead name is
    evaluated and scored `not_in_universe` rather than vanishing from the census. The three
    securities that died on 2026-02-11 are subjects and are not in the cross section, which is
    one of `compute_factor`'s five answers, and it is only reachable if their **listing** rows
    were read -- those live in 1996, 1997 and 1998.
    """
    assert built_after.exit_code == PanelExit.ok, built_after.stderr
    payload = json.loads(built_after.stdout)

    assert payload["subject_count"] == WHOLE_MARKET
    assert payload["universe_counts"] == [WHOLE_MARKET - len(DELISTED)]
    assert payload["coverage"]["raw"]["not_in_universe"] == len(DELISTED)


def test_a_registry_with_no_history_to_read_is_refused_by_name_and_not_withheld(
    interrupted_backfill: Path,
) -> None:
    """The residue of `V2-P4-060`, and the half of its fix that widening does not cover.

    Reading the listing years removes the *cause* on a store the ingest wrote whole. It cannot
    invent partitions that were never written, and an interrupted backfill leaves exactly that:
    a 2026 registry partition holding three delistings whose listing years the store has never
    heard of. `stock_universe_from_panel_rows` still refuses it, still correctly -- and that
    refusal must arrive as a verdict about the panel, with its own sentence, rather than as
    `internal_error` with the message withheld.

    The withheld form is still right for anything genuinely unanticipated; this failure is not
    one, which is what `factor_view._REGISTRY_FAULTS` records.
    """
    result = _factor_build(interrupted_backfill, "2026-02-11T09:00:00+00:00", "--year", str(YEAR))

    assert result.exit_code == PanelExit.unhealthy, result.stderr
    assert "unhandled" not in result.stderr
    assert "has a delisting row and no listing row" in result.stderr
    assert "this is a partial read" in result.stderr
    assert TOKEN not in result.stderr


# --- the remedy README used to state ------------------------------------------------------


def test_naming_the_earlier_lifecycle_years_is_not_the_remedy_and_is_no_longer_needed(
    before_the_delisting: Path,
) -> None:
    """`--year` is one scope over three datasets, so widening it to reach the registry's
    earlier partitions drags the calendar and the price panel along -- and neither has a 2010
    partition, nor could be given one without the ~282,000-request backfill `panel build --help`
    prices at "days rather than hours". The remedy README stated was unreachable; this pins that
    it is unreachable **and** that it is unnecessary, because the single-year form now answers.
    """
    widened = _factor_build(
        before_the_delisting, "2026-02-09T09:00:00+00:00", "--year", str(YEAR), "--year", "2010"
    )

    assert widened.exit_code == PanelExit.unhealthy
    assert "no partition is registered for trade_cal year=2010" in widened.stderr
