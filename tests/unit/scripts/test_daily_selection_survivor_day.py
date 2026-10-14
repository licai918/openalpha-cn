"""`scripts/daily_selection.py`: the survivor book's day, end to end (`V2-P7-008`, tasks D5a and
D5b).

A registration whose configuration names `kind: "survivor_book/v1"` is dispatched to the survivor
day (`test_daily_selection_dispatch.py`); this file runs that day for real, through `main`. The
paper book's writer lock is taken first and held to the end of the day; the day's clock and the
book's holdings are pinned in its own journal before any request; then the panel update
(`targets_for_survivor`, the registered catch-up bound), the doctor, the four composite factors
built at the processed tier, the paper head opened and bound to the registration, the facts day
read with the pinned holdings and screened, and the forward record `survivor_book_composite/v1`
filed -- before the paper step, so that a fault of the paper book never costs the record. Then
(D5b) the paper book: every session the book is missing caught up without a decision, each
journalled as it commits, then the evening -- or, when the day's pinned clock is at or after 09:15
of the next session, the settlement alone -- and the summary, whose `result` is filed only for a
day that completed.

The market is `daily_market.Market` over 120 securities -- the registered transform refuses a cross
section under 100 (`CROSS_SECTION_STANDARD.min_cross_section`) -- and every weekday of 2026, with
each security's turnover its own, so that `turnover_60` has a cross section to standardise. The
panel is seeded once per module through the real `openalpha panel build` and copied into each
test's runtime directory; the registrations are committed into throwaway repositories with
`test_daily_selection`'s own helpers. The days run are 6 April (the first evening after the epoch,
3 April) and the sessions after it; a later day's panel update fetches the sessions it is behind.
The ledger replay is `tests/unit/backtest/test_paper_evening.py`'s own invariant, run on copies of
the real files. Nothing reaches the network.
"""

from __future__ import annotations

import hashlib
import importlib
import itertools
import json
import shlex
import shutil
import sys
import tempfile
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Final

import pytest
import test_daily_selection as tds
from daily_market import Market, compact, weekdays
from test_daily_selection_dispatch import SEED, SURVIVOR_CONFIG
from test_daily_selection_survivor import STEADY_SURVIVOR

from openalpha_cn import (
    cli,
    strategy_survivor,
    strategy_survivor_book,
    strategy_survivor_registration,
    strategy_survivor_summary,
)
from openalpha_cn.backtest.paper_evening import open_book
from openalpha_cn.backtest.paper_settlement import WRITE_OFF_REASON
from openalpha_cn.backtest.paper_state import PaperBookSnapshot, held_head, load_snapshot
from openalpha_cn.domain.adjustment import ADJ_FACTOR_DATASET
from openalpha_cn.domain.daily_prices import DAILY_BASIC_DATASET, DAILY_DATASET
from openalpha_cn.domain.index_prices import INDEX_DAILY_DATASET
from openalpha_cn.domain.portfolio import PortfolioTransition
from openalpha_cn.domain.price_limits import PRICE_LIMIT_DATASET, SUSPENSION_DATASET
from openalpha_cn.domain.stock_universe import STOCK_BASIC_DATASET
from openalpha_cn.domain.survivor_book import SurvivorBookConfig
from openalpha_cn.domain.survivor_screen import screen
from openalpha_cn.domain.trading_calendar import TRADING_CALENDAR_DATASET
from openalpha_cn.panel.catalog import PartitionNotYetKnowableError
from openalpha_cn.panel.store import PanelStore
from openalpha_cn.panel_factors import FACTOR_TRANSFORMS
from openalpha_cn.panel_ingest import load_trading_calendar, session_publication_instant
from openalpha_cn.storage.paper_state import FilePaperStateStore
from openalpha_cn.storage.portfolio import SQLitePortfolioLedger
from openalpha_cn.storage.predictions import FilePredictionStore
from openalpha_cn.strategy_registration import batch_digest
from openalpha_cn.strategy_survivor_facts import HeldStatus
from openalpha_cn.strategy_survivor_market import SessionOrdinals

daily = tds.daily

# The paper book's replay invariant is `test_paper_evening`'s own, imported from its directory
# under its own name, as `tests/unit/test_strategy_survivor_summary.py` imports it.
_EVENING_TESTS: Final[Path] = Path(__file__).resolve().parents[1] / "backtest"
if str(_EVENING_TESTS) not in sys.path:
    sys.path.insert(0, str(_EVENING_TESTS))
evenings: Any = importlib.import_module("test_paper_evening")

SECURITIES: Final[tuple[str, ...]] = tuple(f"{601000 + index}.SH" for index in range(120))
"""Main-board Shanghai codes (prefix 601), clear of the registry's retired and delisted rows
(`daily_market.RETIRED_ROWS`, 600211..600225, and 600199): 120, over the transform's 100."""
DAY: Final[date] = date(2026, 4, 6)
"""A Monday, the 66th session of 2026: the four composite factors look back 60 sessions."""
EPOCH: Final[date] = date(2026, 4, 3)
"""The session before the first scheduled run (decision 3): the paper book opens on it."""
SEEDED_AS_OF: Final[str] = "2026-04-06T12:00:00+08:00"
"""The seed build: 3 April has published, 6 April has not."""
DAY_AS_OF: Final[datetime] = datetime.fromisoformat("2026-04-06T18:30:00+08:00")
RUN_CLOCK: Final[datetime] = datetime.fromisoformat("2026-04-06T18:35:00+08:00")
SEED_TARGETS: Final[tuple[str, ...]] = (
    TRADING_CALENDAR_DATASET,
    STOCK_BASIC_DATASET,
    ADJ_FACTOR_DATASET,
    "price",
    PRICE_LIMIT_DATASET,
    INDEX_DAILY_DATASET,
)
"""Every survivor target but the name corpus, which the day fetches whole for its year anyway
(one request, `STEADY_SURVIVOR`) -- so a market whose names differ can run on the same seed."""
BOOK: Final[SurvivorBookConfig] = SurvivorBookConfig(seed=SEED, min_listing_sessions=60)
"""The design's book, but for the listing age: the stored calendar starts on 5 January, so it can
age a listing only as far as the day's 66 sessions (`listing_age_sessions`)."""

SURVIVOR_TARGETS: Final[tuple[str, ...]] = (
    TRADING_CALENDAR_DATASET,
    STOCK_BASIC_DATASET,
    ADJ_FACTOR_DATASET,
    "price",
    PRICE_LIMIT_DATASET,
    "namechange",
    INDEX_DAILY_DATASET,
)
"""`targets_for_survivor` of the four composite factors (`test_daily_selection_survivor.py`)."""

NEXT: Final[date] = date(2026, 4, 7)
THIRD: Final[date] = date(2026, 4, 8)
FOURTH: Final[date] = date(2026, 4, 9)
"""The sessions after `DAY`, ordinals 2, 3 and 4."""
NO_REQUEST: Final[str] = "tushare requests   0 this run {}"
REPRINTED: Final[str] = (
    "tushare requests   0 this run {} -- the day was already complete; nothing was run"
)
INJECTED: Final[str] = "step 8 (paper book) failed: an injected refusal of the paper book"
REFERENCE_NOTE: Final[str] = (
    "  real closes are references, not limit prices; the paper book fills at the open"
)


def evening_of(day: date) -> datetime:
    """`day`'s 18:30 in Shanghai: the clock a scheduled run pins for it."""
    return datetime.combine(day, time(18, 30), tzinfo=daily.SHANGHAI)


def after_the_auction(day: date) -> datetime:
    """10:00 of the weekday after `day`: past the call auction of its next session."""
    return datetime.combine(day + timedelta(days=1), time(10, 0), tzinfo=daily.SHANGHAI)


def survivor_config(
    *, epoch: date = EPOCH, book: SurvivorBookConfig = BOOK, **adapter: Any
) -> dict[str, Any]:
    """A survivor registration's configuration for this market: the design's adapter block
    (`test_daily_selection_dispatch.SURVIVOR_CONFIG`) with this file's epoch and book."""
    return {
        "kind": "survivor_book/v1",
        "survivor_book": book.model_dump(mode="json", exclude_computed_fields=True),
        "adapter": {**SURVIVOR_CONFIG["adapter"], "epoch_session": epoch.isoformat(), **adapter},
    }


CONFIG: Final[dict[str, Any]] = survivor_config()


@dataclass
class SurvivorMarket(Market):
    """`Market` over `SECURITIES`, each security's `daily_basic.turnover_rate` its own: the
    generated market's is 1.0 for every security, a cross section `turnover_60` cannot
    standardise."""

    securities: tuple[str, ...] = SECURITIES

    def _session_rows(self, api_name: str, day: date) -> tuple[list[str], list[list[Any]]]:
        fields, rows = super()._session_rows(api_name, day)
        if api_name != DAILY_BASIC_DATASET:
            return fields, rows
        column = fields.index("turnover_rate")
        return fields, [
            [*row[:column], 1.0 + self.securities.index(row[0]) / 100, *row[column + 1 :]]
            for row in rows
        ]


@dataclass
class SpecialTreatment(SurvivorMarket):
    """The same market, every security named under special treatment from the frame's start."""

    def _names(self, params: Mapping[str, Any]) -> list[list[Any]]:
        return [[code, f"ST{name}", *rest] for code, name, *rest in super()._names(params)]


@dataclass
class Halting(SurvivorMarket):
    """The same market, with the codes `halted` names halted for a whole session while still
    listed: no bar, no band and no valuation that session, and a halt in `suspend_d` -- the
    holding the paper book carries at its last mark."""

    halted: dict[date, tuple[str, ...]] = field(default_factory=dict)

    def _session_rows(self, api_name: str, day: date) -> tuple[list[str], list[list[Any]]]:
        fields, rows = super()._session_rows(api_name, day)
        stopped = self.halted.get(day, ())
        if api_name == SUSPENSION_DATASET:
            return fields, [*rows, *([code, compact(day), "S", None] for code in stopped)]
        if api_name in (DAILY_DATASET, DAILY_BASIC_DATASET, PRICE_LIMIT_DATASET):
            return fields, [row for row in rows if row[0] not in stopped]
        return fields, rows


@pytest.fixture(scope="module")
def seeded(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """The market's panel through 3 April, built once through the real `panel build`."""
    runtime = tmp_path_factory.mktemp("survivor-seed") / "runtime"
    market = SurvivorMarket(open_days=tds.OPEN_2026)
    market.today = datetime.fromisoformat(SEEDED_AS_OF).date()
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("TUSHARE_TOKEN", tds.SECRET_TOKEN)
        patch.setattr(cli, "_panel_transport", lambda: market)
        patch.setattr(cli, "_panel_clock", lambda: tds.WALL_CLOCK)
        arguments = ["panel", "build", "--runtime-dir", str(runtime), "--year", "2026"]
        arguments += ["--as-of", SEEDED_AS_OF, "--exchange", "SSE", "--json"]
        for target in SEED_TARGETS:
            arguments += ["--dataset", target]
        built = daily.invoke(arguments)
    assert built.exit_code == 0, built.reason()
    return runtime


def _world(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    seeded: Path | None,
    *,
    config: Mapping[str, Any] = CONFIG,
    market: Market | None = None,
) -> tds.World:
    """A committed survivor registration of `config`, `test_daily_selection`'s binding, and a copy
    of the seeded panel (none when `seeded` is `None`: the runtime directory does not exist)."""
    repo, registration = tds._repository(tmp_path, config)
    market = market or SurvivorMarket(open_days=tds.OPEN_2026)
    tds._bind(monkeypatch, repo, market)
    runtime = tmp_path / "runtime"
    if seeded is not None:
        shutil.copytree(seeded, runtime)
    return tds.World(repo=repo, registration=registration, runtime=runtime, market=market)


def _run(
    world: tds.World,
    capsys: pytest.CaptureFixture[str],
    *,
    as_of: datetime = DAY_AS_OF,
    clock: datetime | Callable[[], datetime] = RUN_CLOCK,
    extra: Sequence[str] = (),
    json_output: bool = True,
) -> tuple[int, str, list[str]]:
    """The day through `main`, as the scheduler runs it: no `--dataset`, so the day fetches its
    own targets. Returns the exit code, stdout and stderr's lines. `clock` is a fixed instant,
    or a clock of its own (`_ticking`)."""
    world.market.today = as_of.astimezone(daily.SHANGHAI).date()
    arguments = ["--runtime-dir", str(world.runtime), "--registration", str(world.registration)]
    arguments += ["--repo", str(world.repo), "--as-of", as_of.isoformat(), *extra]
    if json_output:
        arguments.append("--json")

    def read() -> datetime:
        return clock() if callable(clock) else clock

    code = daily.main(arguments, clock=read)
    out, err = capsys.readouterr()
    assert tds.SECRET_TOKEN not in out + err
    return code, out, err.splitlines()


def _requests(line: str) -> dict[str, int]:
    """The requests a refusal's last line reports."""
    head, _, counted = line.partition(" this run ")
    assert head.startswith("tushare requests"), line
    requests: dict[str, int] = json.loads(counted)
    assert int(head.split()[-1]) == sum(requests.values())
    return requests


def _config_id(world: tds.World) -> str:
    return str(json.loads(world.registration.read_text(encoding="utf-8"))["config_id"])


def _journal_path(world: tds.World, day: date = DAY) -> Path:
    return world.runtime / "daily_survivor" / _config_id(world)[:16] / f"{day.isoformat()}.json"


def _journal(world: tds.World, day: date = DAY) -> dict[str, Any]:
    body: dict[str, Any] = json.loads(_journal_path(world, day).read_text(encoding="utf-8"))
    return body


def _records(world: tds.World) -> tuple[str, ...]:
    return FilePredictionStore(world.runtime / "predictions", clock=lambda: RUN_CLOCK).list_ids()


def _files(runtime: Path) -> dict[str, str]:
    return {
        path.relative_to(runtime).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(runtime.rglob("*"))
        if path.is_file()
    }


def _survivor(config: Mapping[str, Any] = CONFIG) -> strategy_survivor.SurvivorRegistration:
    return strategy_survivor.read_survivor_registration(config, seed=SEED)


def _day(
    world: tds.World,
    capsys: pytest.CaptureFixture[str],
    day: date,
    *,
    clock: datetime | None = None,
    json_output: bool = True,
) -> tuple[int, str, list[str]]:
    """`day` run through `main` as the scheduler runs it, pinned at its 18:30 -- by default on a
    clock five minutes later."""
    at = evening_of(day)
    return _run(
        world,
        capsys,
        as_of=at,
        clock=at + timedelta(minutes=5) if clock is None else clock,
        json_output=json_output,
    )


def _completed(
    world: tds.World, capsys: pytest.CaptureFixture[str], day: date, **keywords: Any
) -> dict[str, Any]:
    """`day` run to completion: exit 0 and its result document, printed as JSON."""
    code, out, err = _day(world, capsys, day, **keywords)
    assert code == 0, err
    result: dict[str, Any] = json.loads(out)
    return result


def _snapshots(world: tds.World) -> FilePaperStateStore:
    return FilePaperStateStore(world.runtime / "paper" / "snapshots")


def _snapshot(world: tds.World, day: date) -> PaperBookSnapshot:
    return load_snapshot(_snapshots(world), day)


def _paper_copy(world: tds.World, tmp_path: Path) -> Path:
    """A copy of the runtime directory's paper book, to open without touching the real files."""
    copy = Path(tempfile.mkdtemp(prefix="paper-copy-", dir=tmp_path)) / "paper"
    shutil.copytree(world.runtime / "paper", copy)
    return copy


def _ledger_rows(world: tds.World, tmp_path: Path) -> list[PortfolioTransition]:
    return list(SQLitePortfolioLedger(_paper_copy(world, tmp_path) / "ledger.sqlite3").list())


def _attempts(settled: Mapping[str, Any]) -> SimpleNamespace:
    """A settled section as the replay invariant reads a session's result: its attempts, each
    with its attempt id and the id of the order it tried."""
    return SimpleNamespace(
        settlement=SimpleNamespace(
            attempts=[
                SimpleNamespace(
                    attempt_id=item["attempt_id"], order=SimpleNamespace(order_id=item["order_id"])
                )
                for item in settled["attempts"]
            ]
        )
    )


def _replays(world: tds.World, tmp_path: Path, *results: Mapping[str, Any]) -> None:
    """`test_paper_evening`'s replay invariant on the real files: the ledger, replayed from the
    opening with its rows' own figures, lands on every snapshot -- each session the documents
    settled, oldest first (each one's catch-ups, then its evening), one planned order filled at
    most once."""
    sessions = []
    for result in results:
        section = result["paper"] if result["paper"] is not None else result["paper_skipped"]
        sessions += [_attempts(day["settled"]) for day in section["catch_ups"]]
        if result["paper"] is not None:
            sessions.append(_attempts(result["paper"]["settled"]))
    copy = _paper_copy(world, tmp_path)
    book = evenings.Book(
        root=copy,
        store=FilePaperStateStore(copy / "snapshots"),
        ledger=SQLitePortfolioLedger(copy / "ledger.sqlite3"),
    )
    evenings.assert_the_ledger_replays_to_every_snapshot(book, BOOK.capital, sessions)


def _refused_evening(*_arguments: Any, **_keywords: Any) -> Any:
    raise strategy_survivor_book.SurvivorDayError(
        "paper book", "an injected refusal of the paper book"
    )


def _cut_at_the_result(patch: pytest.MonkeyPatch) -> None:
    """The power cut as the day's journal is written with its `result`: the paper book has
    committed the evening, and the journal never says so."""
    written = daily.write_journal

    def cut(path: Path, body: Mapping[str, Any]) -> None:
        if "result" in body:
            raise OSError("the power was cut as the journal's result was written")
        written(path, body)

    patch.setattr(daily, "write_journal", cut)


def _no_factor_build(*_arguments: Any, session: date, **_keywords: Any) -> dict[str, Any]:
    """Step 4 building nothing: the session's composite factors have no build at its 16:30."""
    return {
        "instant": daily.session_publication_instant(session).isoformat(),
        "built": [],
        "already_built": [],
        "caught_up": [],
        "partitions": [],
    }


def _orders(result: Mapping[str, Any]) -> list[str]:
    return [order["order_id"] for order in result["paper"]["trade_list"]["orders"]]


def _held(result: Mapping[str, Any]) -> list[str]:
    return [holding["ts_code"] for holding in result["paper"]["book"]["holdings"]]


# --- the day, end to end ----------------------------------------------------------------------


def test_a_survivor_day_runs_end_to_end_and_plans_the_initial_build(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    seeded: Path,
) -> None:
    """Dispatched, the first evening after the epoch fetches what an ordinary survivor day fetches
    (the 16 requests `test_daily_selection_survivor.py` pins), opens and binds the paper book on
    its epoch, files the day's eligible composites as a forward record, settles the session --
    nothing was held or ordered -- and plans the initial build: ten buys for the next open, each
    printed with its real close as a reference, never a limit price. The journal files its
    `result`; the snapshot of the session holds the plan; nothing is filed where the P6 day's
    readers look."""
    world = _world(tmp_path, monkeypatch, seeded)

    code, out, err = _run(world, capsys, json_output=False)

    assert code == 0 == daily.DailyExit.done, err
    assert err == []
    lines = out.splitlines()
    buys = [line for line in lines if line.startswith("  BUY ")]
    assert len(buys) == 10
    assert all(" reference: real close " in line for line in buys)
    assert REFERENCE_NOTE in lines
    assert not any(line.lstrip().startswith("SELL") for line in lines)
    assert [line for line in lines if line.startswith("tushare requests")] == [
        f"tushare requests   {sum(STEADY_SURVIVOR.values())} this run "
        f"{json.dumps(STEADY_SURVIVOR, sort_keys=True)}"
    ]
    journal = _journal(world)
    assert journal["schema"] == "openalpha-daily-survivor/v1"
    assert journal["session"] == DAY.isoformat()
    assert journal["as_of"] == DAY_AS_OF.isoformat()
    assert journal["held"] == []
    assert journal["held_from"] is None
    assert journal["registration"]["config_id"] == _config_id(world)
    assert journal["panel"]["targets"] == list(
        daily.targets_for_survivor(daily.survivor_builds(_survivor()))
    )
    assert journal["panel"]["caught_up"] == []
    result = journal["result"]
    assert result["kind"] == strategy_survivor_summary.SURVIVOR_DOCUMENT_KIND
    assert (result["session"], result["session_index"]) == (DAY.isoformat(), 1)
    assert result["prediction"] == journal["prediction"]
    assert result["panel"] == journal["panel"]
    assert result["paper"]["outcome"] == "created"
    assert result["paper"]["catch_ups"] == []
    assert Decimal(result["paper"]["book"]["equity"]) == BOOK.capital
    planned = result["paper"]["trade_list"]["orders"]
    assert [order["side"] for order in planned] == ["buy"] * 10
    assert journal["paper"]["mode"] == "evening"
    assert journal["paper"]["read_at"] == DAY_AS_OF.isoformat()
    # The paper book: bound to this registration, opened on its epoch, the session settled with
    # tonight's plan pending, and its own ledger, which no fill has reached yet.
    marker = json.loads((world.runtime / "paper" / "registration.json").read_text("utf-8"))
    assert marker["config_id"] == _config_id(world)
    assert marker["epoch_session"] == EPOCH.isoformat()
    head = held_head(FilePaperStateStore(world.runtime / "paper" / "snapshots"))
    assert head is not None
    assert (head.session, head.session_index, head.book.positions) == (DAY, 1, ())
    assert head.snapshot_id == result["paper"]["snapshot_id"]
    assert sorted(item.order.order_id for item in head.pending_orders) == sorted(_orders(result))
    assert _snapshot(world, EPOCH).book.cash == BOOK.capital
    assert (world.runtime / "paper" / "ledger.sqlite3").is_file()
    assert _ledger_rows(world, tmp_path) == []
    # Nothing a P6 reader could take for one of its journals or provenances.
    assert not (world.runtime / "daily_selection").exists()
    assert not (world.runtime / "daily_selection_provenance").exists()
    # The record: the day's eligible names, each scored by its composite, filed forward.
    survivor = _survivor()
    facts = strategy_survivor_book.facts_day(
        panel=PanelStore(world.runtime / "panel"),
        registration=survivor,
        session=DAY,
        as_of=DAY_AS_OF,
        held=(),
    )
    eligible = {
        verdict.ts_code: verdict.composite
        for verdict in screen(facts.facts, survivor.book).verdicts
        if verdict.eligible
    }
    assert 0 < len(eligible) < len(SECURITIES)
    (record_id,) = _records(world)
    record = FilePredictionStore(world.runtime / "predictions", clock=lambda: RUN_CLOCK).get(
        record_id
    )
    assert record is not None
    declaration = record.batch.artifact.declaration
    assert declaration.family == "survivor_book_composite/v1"
    assert declaration.horizon == "20d"
    assert declaration.feature_version == _config_id(world)
    assert {item.ts_code: item.score for item in record.batch.predictions} == eligible
    assert record.standing == "forward"
    assert record.batch.predicted_at == RUN_CLOCK  # the run's clock, not its pinned as_of
    prediction = journal["prediction"]
    assert prediction["registered"] is True
    assert prediction["record_id"] == record_id
    assert prediction["family"] == "survivor_book_composite/v1"
    assert prediction["horizon"] == "20d"
    assert prediction["scored"] == len(eligible)
    assert prediction["outcome"] == "created"
    # The filed record's own batch: what Part E finds the record's provenance by.
    assert prediction["batch_digest"] == batch_digest(record.batch)
    # Its provenance was filed before it, under its digest.
    filed = world.runtime / "daily_survivor_provenance" / f"{prediction['provenance']}.json"
    provenance = strategy_survivor_registration.read_survivor_provenance(
        filed,
        registration_sha256=journal["registration"]["sha256"],
    )
    assert provenance.held == ()
    assert provenance.session == DAY
    assert provenance.recorded_at == RUN_CLOCK
    assert provenance.batch_digest == prediction["batch_digest"]


@dataclass
class FirstRequestFails(SurvivorMarket):
    """The market, refusing every request -- after noting what the day's journal held when the
    first was made."""

    journal: Path | None = None
    seen: Any = None

    def post(self, payload: dict[str, Any]) -> dict[str, Any]:
        if self.seen is None:
            assert self.journal is not None
            self.seen = (
                json.loads(self.journal.read_text(encoding="utf-8"))
                if self.journal.is_file()
                else "no journal"
            )
        raise ConnectionError("the day's first request was refused")


def test_the_days_clock_and_holdings_are_pinned_on_disk_before_its_first_request(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    seeded: Path,
) -> None:
    """The journal holds the day's `as_of` and the book's holdings, as found under the lock,
    when the first request is made: every later step of this run and of any rerun reads them
    from there. The fetch fails, the day stops at step 2, and the pin stays."""
    market = FirstRequestFails(open_days=tds.OPEN_2026)
    world = _world(tmp_path, monkeypatch, seeded, market=market)
    market.journal = _journal_path(world)
    monkeypatch.setattr(cli, "TushareProvider", _without_backoff(cli.TushareProvider))

    code, _out, err = _run(world, capsys)

    assert code == 1
    assert err[0].startswith("step 2 (panel update) failed:")
    assert market.seen != "no journal"
    assert market.seen["as_of"] == DAY_AS_OF.isoformat()
    assert market.seen["held"] == []
    assert market.seen["held_from"] is None
    assert "panel" not in market.seen
    assert "panel" not in _journal(world)
    assert sum(_requests(err[-1]).values()) >= 1


def _without_backoff(provider: Callable[..., Any]) -> Callable[..., Any]:
    def made(*arguments: Any, **keywords: Any) -> Any:
        return provider(*arguments, **{**keywords, "sleep": lambda _seconds: None})

    return made


def test_a_rerun_of_a_day_stopped_at_the_paper_book_asks_nothing_and_files_nothing_again(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    seeded: Path,
) -> None:
    """The panel stage is journalled, the factors are built at the instant and the prediction is
    journalled: a rerun of a day the paper book refused spends no request, files no second record
    or provenance, and stops at the same step -- printing the day as far as it got, with the
    refusal as the reason no evening was run, and filing no `result`."""
    world = _world(tmp_path, monkeypatch, seeded)
    monkeypatch.setattr(strategy_survivor_book, "evening", _refused_evening)
    first = _run(world, capsys)
    before = _files(world.runtime)
    records = _records(world)

    code, out, err = _run(world, capsys)

    assert first[0] == code == 1
    assert first[2][0] == INJECTED
    assert err == [INJECTED, NO_REQUEST]
    assert _records(world) == records
    assert _files(world.runtime) == before
    assert "result" not in _journal(world)
    shown = json.loads(out)
    assert shown["paper"] is None
    assert shown["paper_skipped"] == {"reason": INJECTED, "catch_ups": []}
    assert shown["prediction"] == _journal(world)["prediction"]
    assert shown["this_run"] == {"requests": {}, "request_count": 0, "wrote": True}


def test_a_day_whose_journal_is_complete_is_printed_again_with_no_request_and_no_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    seeded: Path,
) -> None:
    """The epoch session only opens the paper book -- no facts, no prediction, no evening -- and
    ends cleanly, filing its result; run again, the day is printed from its journal, with no
    request and nothing written. Without `--json` the opening is printed, not a P6 summary."""
    world = _world(tmp_path, monkeypatch, seeded, config=survivor_config(epoch=DAY))

    code, out, err = _run(world, capsys)

    assert code == 0, err
    result = json.loads(out)
    assert result["kind"] == strategy_survivor_summary.SURVIVOR_DOCUMENT_KIND
    assert result["session"] == DAY.isoformat()
    assert result["session_index"] == 0
    assert result["opening"]["epoch_session"] == DAY.isoformat()
    head = held_head(FilePaperStateStore(world.runtime / "paper" / "snapshots"))
    assert head is not None
    assert result["opening"]["snapshot_id"] == head.snapshot_id
    assert result["opening"]["cash"] == f"{head.book.cash}"
    assert Decimal(result["opening"]["cash"]) == BOOK.capital
    assert result["prediction"]["registered"] is False
    assert result["this_run"]["request_count"] == sum(STEADY_SURVIVOR.values())
    assert result["this_run"]["wrote"] is True
    assert _records(world) == ()
    assert _journal(world)["result"] == {key: result[key] for key in result if key != "this_run"}
    before = _files(world.runtime)

    code, out, err = _run(world, capsys, json_output=False)

    assert code == 0, err
    assert out.splitlines()[0].startswith("session            2026-04-06 (index 0")
    assert (
        "tushare requests   0 this run {} -- the day was already complete; nothing was run"
        in out.splitlines()
    )
    assert _files(world.runtime) == before


def test_a_day_run_after_the_next_sessions_call_auction_settles_only_and_files_no_record(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    seeded: Path,
) -> None:
    """Decision 2: a day whose pinned clock is at or after 09:15 of the next session registers
    nothing -- its scores would not precede their outcome -- and is no error: the session is
    settled without a decision, and the day files its `result` with no evening in it. Printed, it
    says no order was planned tonight, and why."""
    late = datetime.fromisoformat("2026-04-07T10:00:00+08:00")
    world = _world(tmp_path, monkeypatch, seeded)
    read = _calls(monkeypatch, strategy_survivor_book, "facts_day")

    code, out, err = _run(world, capsys, as_of=late, clock=late)

    assert code == 0, err
    # It decides nothing and files nothing: its facts are read with no coverage floor (D4
    # review, rule 3).
    assert [call["min_coverage"] for call in read] == [0.0]
    prediction = _journal(world)["prediction"]
    assert prediction["registered"] is False
    assert prediction["settle_only"] is True
    assert "call auction" in prediction["reason"]
    assert _records(world) == ()
    result = json.loads(out)
    assert _journal(world)["result"] == {key: result[key] for key in result if key != "this_run"}
    assert _journal(world)["paper"]["mode"] == "settle_only"
    assert result["paper"] is None
    assert "settles only" in result["paper_skipped"]["reason"]
    (settled,) = result["paper_skipped"]["catch_ups"]
    assert (settled["session"], settled["session_index"], settled["outcome"]) == (
        DAY.isoformat(),
        1,
        "created",
    )
    snapshot = _snapshot(world, DAY)
    assert snapshot.snapshot_id == settled["snapshot_id"]
    assert snapshot.pending_orders == ()
    assert snapshot.first_decision_session is None
    assert snapshot.last_settled_session == 1

    code, out, _err = _run(world, capsys, as_of=late, clock=late, json_output=False)

    assert code == 0
    assert any("NONE PLANNED tonight -- no evening was run" in line for line in out.splitlines())
    assert REPRINTED in out.splitlines()


# --- the paper book and the summary, day after day (D5b) -------------------------------------


def _without_requests(text: str) -> list[str]:
    return [line for line in text.splitlines() if not line.startswith("tushare requests")]


def test_a_complete_day_is_printed_again_the_same_with_no_request_and_no_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    seeded: Path,
) -> None:
    """A journal with its `result` is the day: run again, it is printed from the journal -- the
    same lines but for the requests, which say nothing was run -- and every file of the runtime
    directory keeps its bytes. With `--json` the document is the journal's `result`."""
    world = _world(tmp_path, monkeypatch, seeded)
    _code, first, _err = _run(world, capsys, json_output=False)
    before = _files(world.runtime)

    code, again, err = _run(world, capsys, json_output=False)

    assert code == 0 and err == []
    assert _files(world.runtime) == before
    assert _without_requests(again) == _without_requests(first)
    assert REPRINTED in again.splitlines()

    code, shown, err = _run(world, capsys)

    assert code == 0 and err == []
    assert json.loads(shown) == {
        **_journal(world)["result"],
        "this_run": {"requests": {}, "request_count": 0, "wrote": False},
    }
    assert _files(world.runtime) == before


def test_the_next_session_fills_the_first_evenings_buys_at_its_open_and_the_ledger_replays(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    seeded: Path,
) -> None:
    """The second evening settles the first one's plan at its own open -- ten buys filled, ten
    ledger rows of the session -- and decides on the book that left: its equity is its cash and
    its holdings at their marks, its snapshot the session's, and the ledger replayed from the
    opening lands on every snapshot (`test_paper_evening`'s invariant, on the real files)."""
    world = _world(tmp_path, monkeypatch, seeded)
    first = _completed(world, capsys, DAY)

    second = _completed(world, capsys, NEXT)

    bought = {order["order_id"]: order for order in first["paper"]["trade_list"]["orders"]}
    assert sorted(second["paper"]["settled"]["filled"]) == sorted(bought)
    rows = _ledger_rows(world, tmp_path)
    assert [(row.status, row.order.side, row.after.as_of) for row in rows] == [
        ("filled", "buy", NEXT)
    ] * 10
    assert _held(second) == sorted(order["ts_code"] for order in bought.values())
    book = second["paper"]["book"]
    marked = sum(
        (Decimal(item["ledger_mark"]) * item["ledger_quantity"] for item in book["holdings"]),
        Decimal(0),
    )
    assert Decimal(book["equity"]) == Decimal(book["cash"]) + marked
    snapshot = _snapshot(world, NEXT)
    assert (second["paper"]["snapshot_id"], book["cash"]) == (
        snapshot.snapshot_id,
        f"{snapshot.book.cash}",
    )
    assert second["paper"]["outcome"] == "created"
    # The facts were read with the holdings the journal pinned: the first evening's book held
    # nothing, and the book the session settles from is that one, so nothing was read again.
    assert second["facts"]["held"] == [] == _journal(world, NEXT)["held"]
    assert "facts_read_again" not in second["paper"]
    assert second["prediction"]["registered"] is True
    assert len(_records(world)) == 2
    _replays(world, tmp_path, first, second)


def test_a_crash_between_the_snapshot_and_the_result_is_finished_by_a_rerun_that_writes_nothing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    seeded: Path,
) -> None:
    """The second evening commits -- its fills in the ledger, its snapshot saved -- and the power
    is cut as its journal's `result` is written. Run again the next morning, after the call
    auction: the journal's pinned clock, not this run's, makes the day an evening, which finds the
    session settled and derives it again from the journal's pinned inputs -- the same trade list,
    nothing written to the ledger or the snapshots -- and files the `result`."""
    world = _world(tmp_path, monkeypatch, seeded)
    _completed(world, capsys, DAY)
    evenings_run: list[Any] = []
    run_evening = strategy_survivor_book.evening

    def spied(**keywords: Any) -> Any:
        evenings_run.append(run_evening(**keywords))
        return evenings_run[-1]

    with pytest.MonkeyPatch.context() as patch:
        _cut_at_the_result(patch)
        patch.setattr(strategy_survivor_book, "evening", spied)
        code, _out, err = _day(world, capsys, NEXT)

    assert code == 1
    assert err[0] == (
        "step 9 (summary) failed: an unexpected fault: OSError: the power was cut as the "
        "journal's result was written"
    )
    journal = _journal(world, NEXT)
    assert "result" not in journal and "paper" not in journal
    (committed,) = evenings_run
    assert committed.result.outcome == "created"
    snapshot = _snapshot(world, NEXT)
    assert snapshot.snapshot_id == committed.result.snapshot.snapshot_id
    assert len(_ledger_rows(world, tmp_path)) == 10
    paper = _files(world.runtime / "paper")

    second = _completed(world, capsys, NEXT, clock=after_the_auction(NEXT))

    assert _files(world.runtime / "paper") == paper
    assert second["paper"]["outcome"] == "unchanged"
    assert second["paper"]["snapshot_id"] == snapshot.snapshot_id
    assert second["paper"]["trade_list"] == committed.result.trade_list.model_dump(mode="json")
    assert second["facts"]["held"] == journal["held"] == []
    assert second["this_run"]["request_count"] == 0
    assert _journal(world, NEXT)["result"] == {
        key: second[key] for key in second if key != "this_run"
    }


def test_a_missed_session_is_settled_without_a_decision_before_the_next_evening(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    seeded: Path,
) -> None:
    """No run on 7 April. The run of 8 April catches the panel and the factors up, settles 7
    April without a decision -- the first evening's buys fill at its open, nothing is planned, no
    record is filed for it -- and then runs its own evening, on the facts read again with the
    holdings the catch-up left (the prediction's were read with the ones pinned before it)."""
    world = _world(tmp_path, monkeypatch, seeded)
    first = _completed(world, capsys, DAY)

    third = _completed(world, capsys, THIRD)

    (caught,) = third["paper"]["catch_ups"]
    assert (caught["session"], caught["session_index"], caught["outcome"]) == (
        NEXT.isoformat(),
        2,
        "created",
    )
    assert sorted(caught["settled"]["filled"]) == sorted(_orders(first))
    assert _journal(world, THIRD)["catch_ups"] == [caught]
    assert not _journal_path(world, NEXT).exists()
    missed = _snapshot(world, NEXT)
    assert missed.snapshot_id == caught["snapshot_id"]
    assert missed.first_decision_session == 1
    assert not any(item.order.signal_session == 2 for item in missed.pending_orders)
    assert (third["session_index"], third["paper"]["outcome"]) == (3, "created")
    assert len(_records(world)) == 2
    bought = sorted(order["ts_code"] for order in first["paper"]["trade_list"]["orders"])
    assert third["facts"]["held"] == []
    assert third["paper"]["facts_read_again"]["held"] == bought
    assert third["panel"]["caught_up"] == [NEXT.isoformat()]
    assert third["factors"]["caught_up"] == [NEXT.isoformat()]
    _replays(world, tmp_path, first, third)

    _code, out, _err = _day(world, capsys, THIRD, json_output=False)

    lines = out.splitlines()
    assert "catch-up           1 session settled without a decision, oldest first" in lines
    assert f"panel catch-up     {NEXT.isoformat()}" in lines
    assert f"factor catch-up    {NEXT.isoformat()}" in lines


def test_each_session_a_catch_up_settles_is_journalled_as_it_commits(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    seeded: Path,
) -> None:
    """Two sessions missed; the power is cut in the second one's catch-up, after the first has
    committed. The first is in the day's journal already, and the run again settles the second
    and lists both."""
    world = _world(tmp_path, monkeypatch, seeded)
    _completed(world, capsys, DAY)
    settle = strategy_survivor_book.run_catch_up
    sessions: list[date] = []

    def cut_in_the_second(**keywords: Any) -> Any:
        sessions.append(keywords["session"])
        if len(sessions) == 2:
            raise RuntimeError("the power was cut in the second session's catch-up")
        return settle(**keywords)

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(strategy_survivor_book, "run_catch_up", cut_in_the_second)
        code, _out, err = _day(world, capsys, FOURTH)

    assert code == 1
    assert err[0] == (
        "step 8 (paper book) failed: an unexpected fault: RuntimeError: the power was cut in the "
        "second session's catch-up"
    )
    assert sessions == [NEXT, THIRD]
    journal = _journal(world, FOURTH)
    assert [day["session"] for day in journal["catch_ups"]] == [NEXT.isoformat()]
    assert journal["catch_ups"][0]["snapshot_id"] == _snapshot(world, NEXT).snapshot_id
    assert "result" not in journal
    assert THIRD not in _snapshots(world).sessions()

    fourth = _completed(world, capsys, FOURTH)

    days = fourth["paper"]["catch_ups"]
    assert [(day["session"], day["outcome"]) for day in days] == [
        (NEXT.isoformat(), "created"),
        (THIRD.isoformat(), "created"),
    ]
    assert days[0] == journal["catch_ups"][0]


def test_a_day_whose_facts_are_refused_files_nothing_and_a_rerun_after_the_fix_decides_it(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    seeded: Path,
) -> None:
    """The second session's factors were not built (step 4 built nothing): its facts are refused
    at step 6, so nothing is registered, nothing is settled and no `result` is filed. Run again
    the same evening once the build is made, the day is decided -- it was never settled
    automatically, so its evening can still run."""
    world = _world(tmp_path, monkeypatch, seeded)
    _completed(world, capsys, DAY)
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(daily, "build_factors", _no_factor_build)
        code, out, err = _day(world, capsys, NEXT)

    assert code == 1
    assert err[0].startswith("step 6 (facts) failed:")
    assert "MissingFactorBuildError" in err[0]
    assert out == ""
    journal = _journal(world, NEXT)
    assert "prediction" not in journal and "result" not in journal
    assert len(_records(world)) == 1
    assert NEXT not in _snapshots(world).sessions()

    second = _completed(world, capsys, NEXT)

    assert second["prediction"]["registered"] is True
    assert len(_records(world)) == 2
    assert (second["paper"]["outcome"], second["paper"]["catch_ups"]) == ("created", [])
    assert _snapshot(world, NEXT).snapshot_id == second["paper"]["snapshot_id"]


def test_a_day_left_undecided_past_the_next_call_auction_is_settled_by_the_next_runs_catch_up(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    seeded: Path,
) -> None:
    """D5a's accepted residual, pinned. The second session's facts are refused; the run again
    comes after the next call auction, with the journal's clock still the evening's: no record is
    held, and `register_prediction` refuses one now (exit 1). An evening never decides without its
    record, so the day stays incomplete -- the refusal and its requests printed, no `result` filed
    -- and the next session's run settles it without a decision before its own evening."""
    world = _world(tmp_path, monkeypatch, seeded)
    _completed(world, capsys, DAY)
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(daily, "build_factors", _no_factor_build)
        _day(world, capsys, NEXT)

    code, out, err = _day(world, capsys, NEXT, clock=after_the_auction(NEXT))

    assert code == 1
    assert err[0].startswith("step 7 (prediction) failed: the scores of 2026-04-07 are traded")
    assert len(err) == 2 and err[1].startswith("tushare requests")
    assert out == ""  # only a day its paper book refused is printed as far as it got
    journal = _journal(world, NEXT)
    assert "prediction" not in journal and "result" not in journal
    assert len(_records(world)) == 1
    assert NEXT not in _snapshots(world).sessions()

    third = _completed(world, capsys, THIRD)

    assert [(day["session"], day["outcome"]) for day in third["paper"]["catch_ups"]] == [
        (NEXT.isoformat(), "created")
    ]
    assert third["paper"]["outcome"] == "created"
    assert len(_records(world)) == 2


def test_a_holding_delisted_with_no_bar_is_written_off_at_its_whole_cost_basis_before_the_decision(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    seeded: Path,
) -> None:
    """A held name the registry reports delisted on the third session, with no bar on it: the
    facts day says `delisted`, the paper book writes it off before the decision -- the whole cost
    basis, no price, no cash -- and the summary says so."""
    world = _world(tmp_path, monkeypatch, seeded)
    first = _completed(world, capsys, DAY)
    second = _completed(world, capsys, NEXT)
    gone = _held(second)[0]
    position = {item.subject: item for item in _snapshot(world, NEXT).book.positions}[gone]
    basis = sum((lot.cost_basis for lot in position.lots), Decimal(0))
    world.market.delisted = ((gone, THIRD),)

    third = _completed(world, capsys, THIRD)

    assert {"ts_code": gone, "status": "delisted"} in third["facts"]["held_status"]
    (written,) = third["paper"]["settled"]["written_off"]
    assert (written["ts_code"], written["ledger_quantity"], written["cost_basis"]) == (
        gone,
        position.quantity,
        format(basis, "f"),
    )
    assert gone not in _held(third)
    tonight = [row for row in _ledger_rows(world, tmp_path) if row.after.as_of == THIRD]
    (off,) = [row for row in tonight if row.reason == WRITE_OFF_REASON]
    assert (off.order.subject, off.execution) == (gone, None)
    flows = Decimal(0)
    for row in tonight:
        if row.status == "filled" and row.execution is not None:
            execution = row.execution
            flows += (
                execution.notional - execution.total_cost
                if row.order.side == "sell"
                else -(execution.notional + execution.total_cost)
            )
    # The write-off moves no cash: the session's cash is the last one's and its fills alone.
    assert Decimal(third["paper"]["book"]["cash"]) == Decimal(second["paper"]["book"]["cash"]) + (
        flows
    )
    _replays(world, tmp_path, first, second, third)

    _code, out, _err = _day(world, capsys, THIRD, json_output=False)

    assert (
        f"  {gone} written off (ledger qty {position.quantity}): whole cost basis "
        f"{format(basis, 'f')}, no price"
    ) in out.splitlines()


def _unregistered(code: str) -> Callable[..., Any]:
    """The facts adapter, reporting the held `code` as missing from the registry."""
    read = strategy_survivor_book.build_facts_day

    def reported(**keywords: Any) -> Any:
        facts = read(**keywords)
        return replace(
            facts,
            held_status=tuple(
                HeldStatus(ts_code=item.ts_code, status="not_in_registry")
                if item.ts_code == code
                else item
                for item in facts.held_status
            ),
        )

    return reported


def test_a_paper_book_refusal_after_the_record_is_filed_keeps_it_and_a_fixed_rerun_completes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    seeded: Path,
) -> None:
    """A held status the paper book cannot settle -- a held code missing from the registry -- is
    the paper book's refusal, not the facts': the record was filed at step 7 and stays; step 8
    refuses by name (exit 1), the day is printed with no evening and files no `result`; once the
    data is fixed the rerun reuses the record and completes the day."""
    world = _world(tmp_path, monkeypatch, seeded)
    _completed(world, capsys, DAY)
    held = _held(_completed(world, capsys, NEXT))[0]
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(strategy_survivor_book, "build_facts_day", _unregistered(held))
        code, out, err = _day(world, capsys, THIRD)

    assert code == 1
    assert err[0].startswith(
        "step 8 (paper book) failed: the facts day of 2026-04-08 reports holdings the paper book "
        "cannot settle"
    )
    assert f"{held} is not_in_registry" in err[0]
    journal = _journal(world, THIRD)
    assert journal["prediction"]["registered"] is True
    assert "result" not in journal and "paper" not in journal
    records = _records(world)
    assert len(records) == 3
    assert THIRD not in _snapshots(world).sessions()
    shown = json.loads(out)
    assert (shown["paper"], shown["paper_skipped"]["reason"]) == (None, err[0])
    assert shown["prediction"] == journal["prediction"]

    third = _completed(world, capsys, THIRD)

    assert third["prediction"] == journal["prediction"]
    assert _records(world) == records
    assert third["paper"]["outcome"] == "created"


def test_a_held_evening_run_again_after_the_next_panel_update_reads_where_the_market_is_readable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    seeded: Path,
) -> None:
    """The second evening commits and its `result` is lost; the third session's day then updates
    the panel and stops at its facts. Run again at its pinned clock, the second evening's market
    can no longer be read there (its year's factors and index levels hold a later row), so the
    paper book reads it at the newest instant the panel can be read at: the evening derives the
    session it settled, unchanged, writing nothing, and the journal says where it read. Through
    `main`, straight: a committed day's completion runs no doctor and no factor build (D3 review,
    rule 5; they read at the pinned clock, and gate a decision already made) and reads its facts
    at that newest instant too."""
    world = _world(tmp_path, monkeypatch, seeded)
    _completed(world, capsys, DAY)
    with pytest.MonkeyPatch.context() as patch:
        _cut_at_the_result(patch)
        _day(world, capsys, NEXT)
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(daily, "build_factors", _no_factor_build)
        code, _out, err = _day(world, capsys, THIRD)
    assert code == 1
    assert err[0].startswith("step 6 (facts) failed:")
    paper = _files(world.runtime / "paper")

    second = _completed(world, capsys, NEXT, clock=after_the_auction(THIRD))

    assert second["paper"]["outcome"] == "unchanged"
    assert _files(world.runtime / "paper") == paper
    assert second["as_of"] == evening_of(NEXT).isoformat()
    stage = _journal(world, NEXT)["paper"]
    assert datetime.fromisoformat(stage["read_at"]) == daily.session_publication_instant(THIRD)


def test_a_halted_holding_is_carried_since_its_halt_began_and_undated_past_an_unreadable_journal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    seeded: Path,
) -> None:
    """A holding halted on the third and fourth sessions is carried at its last mark. Its halt's
    start comes from the earlier sessions' journals: on the third, the second's journal holds it
    with a bar, so it is carried since the third; on the fourth, with the third's journal
    unreadable, its start is unknown and left undated -- never refused, never guessed."""
    market = Halting(open_days=tds.OPEN_2026)
    world = _world(tmp_path, monkeypatch, seeded, market=market)
    _completed(world, capsys, DAY)
    halted = _held(_completed(world, capsys, NEXT))[0]
    market.halted = {THIRD: (halted,), FOURTH: (halted,)}

    third = _completed(world, capsys, THIRD)

    holding = {item["ts_code"]: item for item in third["paper"]["book"]["holdings"]}[halted]
    assert ("carried" in holding["flags"], holding["halted"]) == (True, True)
    assert holding["carried_since"] == THIRD.isoformat()

    _journal_path(world, THIRD).write_bytes(b"\xff\xfe not json")
    fourth = _completed(world, capsys, FOURTH)

    holding = {item["ts_code"]: item for item in fourth["paper"]["book"]["holdings"]}[halted]
    assert "carried" in holding["flags"]
    assert holding["carried_since"] is None


def test_a_second_run_while_the_first_is_in_its_paper_book_is_refused_at_step_one(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    seeded: Path,
) -> None:
    """Two runs of the day at once: the second, started while the first is settling the paper
    book, is refused at step 1 by name, having fetched, pinned and written nothing; the first
    completes the day."""
    world = _world(tmp_path, monkeypatch, seeded)
    meanwhile: list[tuple[int, str, list[str]]] = []
    run_evening = strategy_survivor_book.evening

    def with_a_second_run(**keywords: Any) -> Any:
        meanwhile.append(_run(world, capsys))
        return run_evening(**keywords)

    monkeypatch.setattr(strategy_survivor_book, "evening", with_a_second_run)

    code, out, err = _run(world, capsys)

    assert code == 0, err
    ((second, second_out, second_err),) = meanwhile
    assert (second, second_out) == (1, "")
    assert second_err[0].startswith(
        "step 1 (registration) failed: another run holds the paper book's writer lock"
    )
    assert second_err[1:] == [NO_REQUEST]
    assert json.loads(out)["paper"]["outcome"] == "created"


def test_the_provenance_is_filed_before_the_record(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    seeded: Path,
) -> None:
    """Written once, before the record it describes: a record is never on file without the
    fingerprint of what its day read."""
    world = _world(tmp_path, monkeypatch, seeded)
    seen: list[list[str]] = []
    register = daily.register_prediction

    def spied(*arguments: Any, **keywords: Any) -> Any:
        filed = world.runtime / "daily_survivor_provenance"
        seen.append(sorted(path.name for path in filed.glob("*.json")))
        return register(*arguments, **keywords)

    monkeypatch.setattr(daily, "register_prediction", spied)

    code, _out, err = _run(world, capsys)

    assert code == 0, err
    assert len(_records(world)) == 1
    assert seen == [[f"{_journal(world)['prediction']['provenance']}.json"]]


def test_a_day_is_routed_by_its_pinned_clock_and_one_too_late_to_register_files_nothing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    seeded: Path,
) -> None:
    """A day pinned at 18:30 is an evening day, whatever the clock of a rerun says (D3 review,
    rule 4): run again after the next session's call auction, it is not turned into a
    settle-only day -- and since its record can no longer be filed before its outcome, step 7
    refuses it by name before anything is written, with no stray provenance."""
    world = _world(tmp_path, monkeypatch, seeded)
    check_panel = daily.check_panel
    monkeypatch.setattr(daily, "check_panel", _injected)
    _code, _out, err = _run(world, capsys)
    assert err[0].startswith("step 3 (panel doctor) failed:")
    monkeypatch.setattr(daily, "check_panel", check_panel)
    late = datetime.fromisoformat("2026-04-07T10:00:00+08:00")

    code, _out, err = _run(world, capsys, clock=late)

    assert code == 1
    assert err[0].startswith("step 7 (prediction) failed:")
    assert "can no longer be registered or decided" in err[0]
    assert "the next session's run settles it without a decision" in err[0]
    assert err[1] == NO_REQUEST
    journal = _journal(world)
    assert journal["as_of"] == DAY_AS_OF.isoformat()
    assert "prediction" not in journal
    assert _records(world) == ()
    assert list((world.runtime / "daily_survivor_provenance").glob("*.json")) == []


# --- a day completed after its evening was committed, and one that can no longer be ---------

NEXT_AS_OF: Final[datetime] = datetime.fromisoformat("2026-04-07T18:30:00+08:00")
NEXT_CLOCK: Final[datetime] = datetime.fromisoformat("2026-04-07T18:35:00+08:00")


def _calls(monkeypatch: pytest.MonkeyPatch, owner: Any, name: str) -> list[dict[str, Any]]:
    """Every call of `owner.name` from here on, by its keywords; the call goes through."""
    calls: list[dict[str, Any]] = []
    original = getattr(owner, name)

    def recorded(*arguments: Any, **keywords: Any) -> Any:
        calls.append(keywords)
        return original(*arguments, **keywords)

    monkeypatch.setattr(owner, name, recorded)
    return calls


def test_a_day_whose_evening_was_committed_is_completed_after_the_next_sessions_update(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    seeded: Path,
) -> None:
    """Probe C, committed: the second evening (7 April) committed and its `result` was lost; the
    third session's run (8 April) has since stored 8 April and stopped at its facts. The rerun of
    7 April is a committed day's completion: no request, no doctor and no factor build (they gate
    a decision already made, and read at the pinned instant, which the newer panel no longer
    answers), the facts read at the newest readable instant -- 8 April's publication -- which C1
    and C3 hold to the bytes the evening read, and the factor builds taken from the journal."""
    world = _world(tmp_path, monkeypatch, seeded)
    _completed(world, capsys, DAY)
    with pytest.MonkeyPatch.context() as patch:
        _cut_at_the_result(patch)
        _day(world, capsys, NEXT)
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(daily, "build_factors", _no_factor_build)
        _code, _out, err = _day(world, capsys, THIRD)
    assert err[0].startswith("step 6 (facts) failed:"), err
    doctor = _calls(monkeypatch, daily, "check_panel")
    built = _calls(monkeypatch, daily, "build_factors")
    read = _calls(monkeypatch, strategy_survivor_book, "facts_day")
    records = _records(world)

    result = _completed(world, capsys, NEXT, clock=after_the_auction(THIRD))

    assert result["this_run"]["request_count"] == 0
    assert doctor == []
    assert built == []
    assert [(call["session"], call["as_of"]) for call in read] == [
        (NEXT, session_publication_instant(THIRD))
    ]
    assert _records(world) == records
    assert result["paper"]["outcome"] == "unchanged"
    assert result["factors"] == _journal(world, NEXT)["factors"]
    assert result["factors"]["built"] == sorted(
        f"{factor}@processed" for factor in BOOK.composite_factors
    )


def test_a_day_whose_evening_was_not_committed_is_refused_once_the_next_update_has_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    seeded: Path,
) -> None:
    """Probe C, not committed: 7 April's paper book refused, so its evening never committed; 8
    April's run has since stored 8 April and stopped at its facts. The panel can no longer be read
    as it stood at 7 April's pinned instant, and deciding on a later read could differ from the
    one the filed record used: refused by name before any request, no result filed -- the next
    run settles the session without a decision."""
    world = _world(tmp_path, monkeypatch, seeded)
    _completed(world, capsys, DAY)
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(strategy_survivor_book, "evening", _refused_evening)
        _code, _out, err = _day(world, capsys, NEXT)
    assert err[0] == INJECTED, err
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(daily, "build_factors", _no_factor_build)
        _code, _out, err = _day(world, capsys, THIRD)
    assert err[0].startswith("step 6 (facts) failed:"), err

    code, _out, err = _day(world, capsys, NEXT, clock=after_the_auction(THIRD))

    assert code == 1
    assert err[0].startswith("step 3 (panel doctor) failed:")
    assert "2026-04-08" in err[0]
    assert "this evening can no longer be completed" in err[0]
    assert "the next run settles it without a decision" in err[0]
    assert err[-1] == NO_REQUEST
    assert "result" not in _journal(world, NEXT)


def test_the_factor_builds_are_journalled_and_a_rerun_keeps_the_sessions_it_caught_up(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    seeded: Path,
) -> None:
    """The `factors` stage holds step 4's builds, the missed sessions it caught up included (D4
    review, rule 4, prints them): 8 April after a missed 7 April catches it up; its paper book
    refuses; the rerun -- whose step 4 builds nothing -- keeps what the first run caught up, and
    its result prints it."""
    world = _world(tmp_path, monkeypatch, seeded)
    _completed(world, capsys, DAY)
    assert _journal(world)["factors"]["built"] == sorted(
        f"{factor}@processed" for factor in BOOK.composite_factors
    )
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(strategy_survivor_book, "evening", _refused_evening)
        _code, _out, err = _day(world, capsys, THIRD)
    assert err[0] == INJECTED, err
    first = _journal(world, THIRD)["factors"]
    assert first["caught_up"] == [NEXT.isoformat()]

    result = _completed(world, capsys, THIRD)

    assert result["factors"]["caught_up"] == [NEXT.isoformat()]
    assert _journal(world, THIRD)["factors"] == first


def test_a_settle_only_day_with_nothing_eligible_goes_on_to_the_paper_book(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    seeded: Path,
) -> None:
    """The empty-eligible refusal guards a record: a settle-only day files none, so a screen that
    passes nobody does not stop it -- the paper book still settles the session."""
    late = datetime.fromisoformat("2026-04-07T10:00:00+08:00")
    world = _world(tmp_path, monkeypatch, seeded, market=SpecialTreatment(open_days=tds.OPEN_2026))

    code, _out, err = _run(world, capsys, as_of=late, clock=late)

    assert code == 0, err
    assert _journal(world)["prediction"]["settle_only"] is True
    assert _records(world) == ()


def _edited_journal(world: tds.World, **changes: Any) -> None:
    path = _journal_path(world)
    journal = json.loads(path.read_text(encoding="utf-8"))
    journal.update(changes)
    for stage in ("prediction", "catch_ups", "paper", "result"):
        journal.pop(stage, None)
    path.write_text(json.dumps(journal), encoding="utf-8")


@pytest.mark.parametrize(
    ("pinned_from", "named"),
    [
        pytest.param(
            {"session": DAY.isoformat(), "session_index": 1, "snapshot_id": "pbs_later"},
            "older than the snapshot this day pinned its holdings from (pbs_later, index 1)",
            id="pinned from a later snapshot",
        ),
        pytest.param(
            {"session": EPOCH.isoformat(), "session_index": 0, "snapshot_id": "pbs_replaced"},
            "holds [] as pbs_",
            id="the pinned index holding another snapshot",
        ),
    ],
)
def test_a_book_that_moved_back_or_was_replaced_under_the_journal_is_refused_by_name(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    seeded: Path,
    pinned_from: dict[str, Any],
    named: str,
) -> None:
    """The journal pinned its holdings from a snapshot the book no longer has: one later than the
    head on disk (the book was restored to an older state), or another snapshot at the same
    index with the same holdings (the book was replaced). Each refused before any request."""
    world = _world(tmp_path, monkeypatch, seeded)
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(strategy_survivor_book, "evening", _refused_evening)
        _code, _out, err = _run(world, capsys)
    assert err[0] == INJECTED, err
    _edited_journal(world, held=[], held_from=pinned_from)

    code, _out, err = _run(world, capsys)

    assert code == 1
    assert err[0].startswith("step 5 (paper head) failed:")
    assert named in err[0]
    assert "move this day's journal" in err[0]
    assert err[1] == NO_REQUEST


def test_a_book_a_later_sessions_run_has_settled_is_refused_for_an_earlier_day(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    seeded: Path,
) -> None:
    """6 April's evening is committed; a run for 3 April (the epoch) finds the book past its
    session: a past evening is not run again. Refused by name before any request -- and before
    the day's pin, so that "Nothing was fetched or written" is true (D5 joint review, Minor 3)."""
    world = _world(tmp_path, monkeypatch, seeded)
    _completed(world, capsys, DAY)
    on_the_epoch = datetime.fromisoformat("2026-04-03T18:30:00+08:00")
    before = _files(world.runtime)

    code, _out, err = _run(world, capsys, as_of=on_the_epoch, clock=on_the_epoch)

    assert code == 1
    assert err[0].startswith("step 5 (paper head) failed: the paper book is settled through")
    assert "a later session's run changed the book" in err[0]
    assert "Nothing was fetched or written" in err[0]
    assert err[1] == NO_REQUEST
    assert not _journal_path(world, EPOCH).exists()
    assert _files(world.runtime) == before


def test_the_facts_are_the_pinned_holdings_and_a_reused_record_keeps_its_own_provenance(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    seeded: Path,
) -> None:
    """The facts are read with the holdings the journal pinned, even when the book has moved on
    since within the session (here its evening was committed). The record -- whose scores do not
    depend on them -- is the one already filed, and the provenance the journal names is the one
    written before it, with the holdings its run read: no second provenance is filed for one
    record (D5 joint review, Minor 2 -- Part E's index refuses two naming one filing)."""
    world = _world(tmp_path, monkeypatch, seeded)
    with pytest.MonkeyPatch.context() as patch:
        _cut_at_the_result(patch)
        _run(world, capsys)
    (record,) = _records(world)
    (written,) = (world.runtime / "daily_survivor_provenance").glob("*.json")
    opening = _snapshot(world, EPOCH)
    pinned = [SECURITIES[0]]
    _edited_journal(
        world,
        held=pinned,
        held_from={
            "session": EPOCH.isoformat(),
            "session_index": 0,
            "snapshot_id": opening.snapshot_id,
        },
    )

    code, out, err = _run(world, capsys)

    assert code == 0, err
    assert json.loads(out)["facts"]["held"] == pinned
    prediction = _journal(world)["prediction"]
    assert prediction["outcome"] == "unchanged"
    assert prediction["record_id"] == record
    assert list((world.runtime / "daily_survivor_provenance").glob("*.json")) == [written]
    assert prediction["provenance"] == written.stem
    provenance = strategy_survivor_registration.read_survivor_provenance(
        written, registration_sha256=_journal(world)["registration"]["sha256"]
    )
    assert provenance.held == ()
    assert provenance.batch_digest == prediction["batch_digest"]


def test_the_panel_steps_bound_is_the_one_the_registration_names(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    seeded: Path,
) -> None:
    """A registration naming 3 on a store 4 sessions behind: step 2 refuses before any request
    (the design's 10 would have fetched them)."""
    config = survivor_config(epoch=date(2026, 4, 9), max_panel_catch_up_sessions=3)
    world = _world(tmp_path, monkeypatch, seeded, config=config)
    later = datetime.fromisoformat("2026-04-10T18:30:00+08:00")

    code, _out, err = _run(world, capsys, as_of=later, clock=later)

    assert code == 1
    assert err[0].startswith("step 2 (panel update) failed: 4 missed session(s)")
    assert "more than the 3 this step fetches in one run" in err[0]
    assert err[-1] == NO_REQUEST


NEW_YEAR_DAYS: Final[tuple[date, ...]] = weekdays(
    date(2026, 9, 28), date(2027, 2, 26), closed=(date(2027, 1, 1),)
)
"""From 28 September 2026, so that 4 January 2027 has the 60 sessions of history the composite
factors and the listing age need, to 26 February 2027, so that its 20-session outcome window can
be placed; 1 January 2027 is a holiday."""
FIRST_2027: Final[date] = date(2027, 1, 4)


@dataclass
class NewYearMarket(SurvivorMarket):
    """The survivor market across New Year, with what a real new year has from its first session:
    a rename announced (2027's first, so its name partition is not empty) and a registry event
    (a delisting, so the registry has a 2027 lifecycle year -- every factor build reads the
    registry through the session's year, and refuses `partition_missing` without one)."""

    delisted: tuple[tuple[str, date], ...] = ((SECURITIES[-2], date(2027, 1, 4)),)

    def _names(self, params: Mapping[str, Any]) -> list[list[Any]]:
        if str(params["start_date"])[:4] == "2027":
            return [[SECURITIES[5], "新名称", "20270104", None, "20270104", "改名"]]
        return super()._names(params)


@pytest.fixture(scope="module")
def new_year(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """The market's panel through 31 December 2026, and 2027's calendar, as the last run of
    2026 leaves a store (it stores the next year's calendar once the outcome window reaches it)."""
    runtime = tmp_path_factory.mktemp("survivor-new-year") / "runtime"
    market = NewYearMarket(open_days=NEW_YEAR_DAYS)
    market.today = date(2026, 12, 31)
    seeded_as_of = "2027-01-04T12:00:00+08:00"
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("TUSHARE_TOKEN", tds.SECRET_TOKEN)
        patch.setattr(cli, "_panel_transport", lambda: market)
        patch.setattr(cli, "_panel_clock", lambda: tds.WALL_CLOCK)
        for year, targets in ((2026, (*SEED_TARGETS, "namechange")), (2027, ("trade_cal",))):
            arguments = ["panel", "build", "--runtime-dir", str(runtime), "--year", str(year)]
            arguments += ["--as-of", seeded_as_of, "--exchange", "SSE", "--json"]
            for target in targets:
                arguments += ["--dataset", target]
            built = daily.invoke(arguments)
            assert built.exit_code == 0, built.reason()
    return runtime


def test_the_first_session_of_a_year_runs_to_its_record_asked_about_both_years(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    new_year: Path,
) -> None:
    """The design's open item: a first-of-year session, the name corpus among the doctor's
    datasets. 4 January 2027 is asked about with the year before (`first_of_year`), the doctor
    and the gate clear -- `namechange` included -- and the day runs to its record and its
    first evening."""
    config = survivor_config(epoch=date(2026, 12, 31))
    market = NewYearMarket(open_days=NEW_YEAR_DAYS)
    world = _world(tmp_path, monkeypatch, new_year, config=config, market=market)
    doctor = _calls(monkeypatch, daily, "check_panel")
    evening = datetime.fromisoformat("2027-01-04T18:30:00+08:00")

    code, _out, err = _run(world, capsys, as_of=evening, clock=evening)

    assert [(call["session"], call["first_of_year"]) for call in doctor] == [(FIRST_2027, True)]
    assert "namechange" in doctor[0]["datasets"]
    assert code == 0, err
    prediction = _journal(world, FIRST_2027)["prediction"]
    assert prediction["registered"] is True
    assert prediction["scored"] > 0


# --- the paper book's lock and binding -------------------------------------------------------


def test_a_second_run_while_the_paper_book_is_held_is_refused_at_step_one_having_asked_nothing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    seeded: Path,
) -> None:
    """One writer per paper book: while another run holds its lock, a run is refused before its
    step 2, by name, having fetched, pinned and written nothing."""
    world = _world(tmp_path, monkeypatch, seeded)

    with strategy_survivor_book.paper_book_lock(world.runtime / "paper"):
        code, _out, err = _run(world, capsys)

    assert code == 1
    assert err[0].startswith(
        "step 1 (registration) failed: another run holds the paper book's writer lock"
    )
    assert err[1] == NO_REQUEST
    assert world.market.payloads == []
    assert not (world.runtime / "daily_survivor").exists()


def test_a_paper_book_bound_to_another_registration_is_refused_at_the_paper_head(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    seeded: Path,
) -> None:
    world = _world(tmp_path, monkeypatch, seeded)
    survivor = _survivor()
    strategy_survivor_book.bind_paper_book(
        world.runtime / "paper",
        config_id="another registration",
        config_digest=survivor.digest,
        epoch_session=EPOCH,
    )

    code, _out, err = _run(world, capsys)

    assert code == 1
    assert err[0].startswith("step 5 (paper head) failed:")
    assert "differ in ['config_id']" in err[0]
    assert err[1] == NO_REQUEST
    assert not (world.runtime / "daily_survivor").exists()
    assert _records(world) == ()
    assert held_head(FilePaperStateStore(world.runtime / "paper" / "snapshots")) is None


def test_a_held_paper_book_without_its_marker_is_refused_at_the_paper_head(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    seeded: Path,
) -> None:
    world = _world(tmp_path, monkeypatch, seeded)
    survivor = _survivor()
    open_book(
        store=FilePaperStateStore(world.runtime / "paper" / "snapshots"),
        session=EPOCH,
        session_index=0,
        cash=survivor.book.capital,
        config_digest=survivor.digest,
    )

    code, _out, err = _run(world, capsys)

    assert code == 1
    assert err[0].startswith("step 5 (paper head) failed:")
    assert "no binding marker" in err[0]
    assert err[1] == NO_REQUEST
    assert not (world.runtime / "daily_survivor").exists()
    assert _records(world) == ()
    assert not (world.runtime / "paper" / "registration.json").exists()


def _open_book(world: tds.World, *, config_id: str) -> None:
    """A paper book opened on the epoch and bound to `config_id`, as a run of that registration
    opens one."""
    survivor = _survivor()
    panel = PanelStore(world.runtime / "panel")
    calendar = load_trading_calendar(panel, exchange="SSE", years=(2026,), as_of=DAY_AS_OF)
    with strategy_survivor_book.paper_book_lock(world.runtime / "paper") as lock:
        strategy_survivor_book.paper_head(
            state_store=FilePaperStateStore(world.runtime / "paper" / "snapshots"),
            registration=survivor,
            ordinals=SessionOrdinals(calendar, survivor.adapter.epoch_session),
            session=EPOCH,
            paper_directory=world.runtime / "paper",
            config_id=config_id,
            lock=lock,
        )


def test_a_book_of_another_registration_is_refused_before_the_day_pins_or_asks_anything(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    seeded: Path,
) -> None:
    """The holdings are pinned only from this registration's book: a paper directory holding
    another registration's book is refused before the pin and before any request -- so once it
    is moved aside the day runs as a first day, and again as a complete one. (Pinned from the
    foreign book, the session could never run again: the fresh book opened after the move is
    not the one the journal pinned.)"""
    world = _world(tmp_path, monkeypatch, seeded)
    _open_book(world, config_id="another registration")

    code, _out, err = _run(world, capsys)

    assert code == 1
    assert err[0].startswith("step 5 (paper head) failed:")
    assert "'another registration'" in err[0]
    assert err[1] == NO_REQUEST
    assert not (world.runtime / "daily_survivor").exists()

    (world.runtime / "paper").rename(world.runtime / "paper.another")
    code, out, err = _run(world, capsys)

    assert code == 0, err
    assert json.loads(out)["this_run"]["requests"] == STEADY_SURVIVOR
    assert _journal(world)["held_from"] is None

    code, out, err = _run(world, capsys)

    assert code == 0, err
    assert json.loads(out)["this_run"] == {"requests": {}, "request_count": 0, "wrote": False}


def _rerun_pinned_from_a_held_book(
    world: tds.World, capsys: pytest.CaptureFixture[str]
) -> dict[str, Any]:
    """The day run once (it opens the book and commits its first evening), its journal lost, and
    the day run again, cut before its result: the second pin is taken from a held book and the
    journal is left unfinished. Returns that journal."""
    _completed(world, capsys, DAY)
    _journal_path(world).unlink()
    with pytest.MonkeyPatch.context() as patch:
        _cut_at_the_result(patch)
        _code, _out, err = _run(world, capsys)
    assert err[0].startswith("step 9 (summary) failed:"), err
    return _journal(world)


def test_a_day_pinned_from_a_held_book_names_the_snapshot_it_pinned_from(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    seeded: Path,
) -> None:
    world = _world(tmp_path, monkeypatch, seeded)

    journal = _rerun_pinned_from_a_held_book(world, capsys)

    head = held_head(FilePaperStateStore(world.runtime / "paper" / "snapshots"))
    assert head is not None
    assert (head.session, head.session_index) == (DAY, 1)
    assert journal["held"] == list(strategy_survivor_book.held_codes(head))
    assert journal["held_from"] == {
        "session": DAY.isoformat(),
        "session_index": 1,
        "snapshot_id": head.snapshot_id,
    }


def test_a_day_whose_pinned_book_is_gone_is_refused_before_any_request_and_opens_none(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    seeded: Path,
) -> None:
    """The journal pinned holdings from a held book and the runtime directory holds no book now
    (its paper directory was moved): refused by name before any request, and no fresh book is
    opened under the journal that pinned another."""
    world = _world(tmp_path, monkeypatch, seeded)
    _rerun_pinned_from_a_held_book(world, capsys)
    (world.runtime / "paper").rename(world.runtime / "paper.moved")

    code, _out, err = _run(world, capsys)

    assert code == 1
    assert err[0].startswith("step 5 (paper head) failed:")
    assert "holds no paper book now" in err[0]
    assert "move this day's journal" in err[0]
    assert err[1] == NO_REQUEST
    assert held_head(FilePaperStateStore(world.runtime / "paper" / "snapshots")) is None
    assert not (world.runtime / "paper" / "registration.json").exists()


def test_a_book_that_is_not_the_one_the_journal_pinned_is_refused_at_the_paper_head(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    seeded: Path,
) -> None:
    """The head the paper head finds is checked against the holdings the day pinned: the same
    snapshot holding other names means another run changed the book under the journal, and the
    day is refused at step 5 by name, before its facts -- with nothing written."""
    world = _world(tmp_path, monkeypatch, seeded)
    with pytest.MonkeyPatch.context() as patch:
        # The first run stops at its paper book, so that the session is not settled.
        patch.setattr(strategy_survivor_book, "evening", _refused_evening)
        _run(world, capsys)
    head = held_head(FilePaperStateStore(world.runtime / "paper" / "snapshots"))
    assert head is not None
    path = _journal_path(world)
    journal = json.loads(path.read_text(encoding="utf-8"))
    pinned_from = {
        "session": EPOCH.isoformat(),
        "session_index": 0,
        "snapshot_id": head.snapshot_id,
    }
    journal.update(held=[SECURITIES[0]], held_from=pinned_from)
    del journal["prediction"]
    path.write_text(json.dumps(journal), encoding="utf-8")
    records = _records(world)

    code, _out, err = _run(world, capsys)

    assert code == 1
    assert err[0].startswith("step 5 (paper head) failed: the paper book's head at session index 0")
    assert "another run changed the book under the journal" in err[0]
    assert err[1] == NO_REQUEST
    assert _records(world) == records
    assert "prediction" not in _journal(world)


@pytest.mark.parametrize(
    ("epoch", "bound", "refused"),
    [
        pytest.param(date(2026, 3, 2), 10, 25, id="25 sessions back, bound 1 + 10"),
        pytest.param(date(2026, 3, 30), 3, 5, id="5 sessions back, bound 1 + 3"),
    ],
)
def test_an_epoch_further_back_than_the_panel_step_catches_up_is_refused_as_a_registration(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    seeded: Path,
    epoch: date,
    bound: int,
    refused: int,
) -> None:
    """Decision 3: the epoch is the session before the first scheduled run, and one more than
    `1 + max_panel_catch_up_sessions` sessions before the session is refused while no book is
    held -- as a registration the day cannot run (exit 2), before any request, the pin or the
    book: `paper_head`'s own check would refuse the same at step 5, after the day's requests,
    every night."""
    config = survivor_config(epoch=epoch, max_panel_catch_up_sessions=bound)
    world = _world(tmp_path, monkeypatch, seeded, config=config)

    code, _out, err = _run(world, capsys)

    assert code == 2 == daily.DailyExit.no_registration
    assert err[0].startswith(
        f"step 1 (registration) failed: the paper book opens on its epoch {epoch.isoformat()}, "
        f"{refused} sessions before this run's 2026-04-06, and an epoch may be at most "
        f"{1 + bound} sessions before the first run"
    )
    assert err[1] == NO_REQUEST
    assert not (world.runtime / "daily_survivor").exists()
    assert _records(world) == ()
    assert held_head(FilePaperStateStore(world.runtime / "paper" / "snapshots")) is None
    assert not (world.runtime / "paper" / "registration.json").exists()


# --- what the survivor day refuses before it starts -------------------------------------------


@pytest.mark.parametrize(
    ("option", "named"),
    [
        pytest.param(["--full-update"], "--full-update", id="full update"),
        pytest.param(["--top", "3"], "--top", id="top"),
        pytest.param(["--dataset", "trade_cal"], "--dataset", id="dataset narrower"),
        pytest.param(
            [
                "--dataset",
                "index_weight",
                *(
                    argument
                    for target in SURVIVOR_TARGETS
                    if target != "namechange"
                    for argument in ("--dataset", target)
                ),
            ],
            "--dataset",
            id="dataset without the name corpus",
        ),
    ],
)
def test_an_option_of_the_p6_day_is_refused_before_anything_is_made(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    option: list[str],
    named: str,
) -> None:
    """`--full-update` and `--top` mean nothing to a survivor day, and a `--dataset` that leaves
    out a target the day reads would let it file a forward record from a panel it did not update:
    each is refused as a registration the day cannot run, before any request and before the
    runtime directory exists."""
    world = _world(tmp_path, monkeypatch, None)

    code, _out, err = _run(world, capsys, extra=option)

    assert code == 2 == daily.DailyExit.no_registration
    assert err[0].startswith(f"step 1 (registration) failed: {named} ")
    assert err[1:] == [NO_REQUEST]
    assert world.market.payloads == []
    assert not world.runtime.exists()


def test_a_dataset_that_adds_targets_is_the_days_and_the_staleness_bound_the_factor_builds(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    seeded: Path,
) -> None:
    """`--dataset` may widen the day's targets -- here `index_weight` beside every survivor
    target -- and the day then updates and checks them all; `--max-staleness-days` is the factor
    build's bound, as on the P6 day."""
    world = _world(tmp_path, monkeypatch, seeded)
    built: list[int] = []
    build_factors = daily.build_factors

    def recorded(*arguments: Any, **keywords: Any) -> Any:
        built.append(keywords["max_staleness_days"])
        return build_factors(*arguments, **keywords)

    monkeypatch.setattr(daily, "build_factors", recorded)
    targets = (*SURVIVOR_TARGETS, "index_weight")
    extra = [argument for target in targets for argument in ("--dataset", target)]

    code, out, err = _run(world, capsys, extra=[*extra, "--max-staleness-days", "45"])

    assert code == 0, err
    assert _journal(world)["panel"]["targets"] == list(targets)
    assert json.loads(out)["this_run"]["requests"]["index_weight"] >= 1
    assert built == [45]


@pytest.mark.parametrize(
    ("epoch", "named"),
    [
        pytest.param(date(2026, 4, 7), "after the newest closed session", id="after the session"),
        pytest.param(date(2026, 4, 4), "not an open SSE session", id="a saturday"),
    ],
)
def test_an_epoch_the_day_cannot_count_from_is_refused_as_a_registration(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    seeded: Path,
    epoch: date,
    named: str,
) -> None:
    """An epoch after the day's session (P6's `session < anchor`), and one the stored calendar
    does not open (the config reader admits any date): exit 2 at step 1, before any request and
    before the day's journal."""
    world = _world(tmp_path, monkeypatch, seeded, config=survivor_config(epoch=epoch))

    code, _out, err = _run(world, capsys)

    assert code == 2 == daily.DailyExit.no_registration
    assert err[0].startswith("step 1 (registration) failed:")
    assert named in err[0]
    assert err[1:] == [NO_REQUEST]
    assert not (world.runtime / "daily_survivor").exists()


@pytest.mark.parametrize(
    ("body", "named"),
    [
        pytest.param(
            {"schema": "openalpha-daily-selection/v1", "session": "2026-04-06"},
            "is a P6 journal",
            id="a p6 journal",
        ),
        pytest.param(
            {"schema": "openalpha-daily-survivor/v0"},
            "'openalpha-daily-survivor/v0'",
            id="another schema",
        ),
        pytest.param("not a journal", "not a mapping", id="not a mapping"),
    ],
)
def test_a_journal_that_is_not_the_survivor_days_is_refused_by_name_before_any_request(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    seeded: Path,
    body: object,
    named: str,
) -> None:
    """The survivor day reads its journal with its own reader: a P6 journal, another schema or
    something else under its name is refused by name -- never read as the day's, and never
    numbered as the P6 day numbers its summary."""
    world = _world(tmp_path, monkeypatch, seeded)
    path = _journal_path(world)
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(body), encoding="utf-8")

    code, _out, err = _run(world, capsys)

    assert code == 1
    assert err[0].startswith("step 9 (summary) failed:")
    assert named in err[0]
    # What to do with it, not only that it is left as it is (D5 joint review, Minor 8).
    assert "It is left as it is: move it aside" in err[0]
    assert "run the day again" in err[0]
    assert err[1:] == [NO_REQUEST]


def test_a_journal_that_is_not_json_is_refused_by_name(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    seeded: Path,
) -> None:
    world = _world(tmp_path, monkeypatch, seeded)
    path = _journal_path(world)
    path.parent.mkdir(parents=True)
    path.write_bytes(b"\xff\xfe not json")

    code, _out, err = _run(world, capsys)

    assert code == 1
    assert err[0].startswith("step 9 (summary) failed:")
    assert "openalpha-daily-survivor/v1" in err[0]
    assert "It is left as it is: move it aside" in err[0]
    assert "it may be this day's own journal, damaged" in err[0]
    assert err[1:] == [NO_REQUEST]


# --- the facts step ----------------------------------------------------------------------------


def test_a_day_with_nothing_eligible_is_refused_at_the_facts_step_and_files_nothing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    seeded: Path,
) -> None:
    """Every security under special treatment: the facts day is complete and its coverage
    clears, and no name is eligible. An empty record is no registration, so the day is refused
    at step 6, by name; nothing is filed and the journal keeps its stages but no prediction."""
    world = _world(tmp_path, monkeypatch, seeded, market=SpecialTreatment(open_days=tds.OPEN_2026))

    code, _out, err = _run(world, capsys)

    assert code == 1
    assert err[0].startswith("step 6 (facts) failed: no security is eligible on 2026-04-06")
    assert "special_treatment" in err[0]
    assert _requests(err[1]) == STEADY_SURVIVOR
    assert _records(world) == ()
    assert not (world.runtime / "daily_survivor_provenance").exists()
    journal = _journal(world)
    assert "panel" in journal
    assert "prediction" not in journal


# --- an unexpected fault in each step -----------------------------------------------------------


def _injected(*_arguments: Any, **_keywords: Any) -> Any:
    raise RuntimeError("injected")


@pytest.mark.parametrize(
    ("owner", "name", "step", "spent"),
    [
        pytest.param(strategy_survivor_book, "paper_book_lock", "registration", 0, id="1"),
        pytest.param(daily, "update_panel", "panel update", 0, id="2"),
        pytest.param(daily, "check_panel", "panel doctor", 16, id="3"),
        pytest.param(daily, "build_factors", "factor build", 16, id="4"),
        pytest.param(strategy_survivor_book, "paper_head", "paper head", 16, id="5"),
        pytest.param(strategy_survivor_book, "facts_day", "facts", 16, id="6"),
        pytest.param(strategy_survivor_registration, "survivor_batch", "prediction", 16, id="7"),
        pytest.param(daily, "registration_cutoff", "panel update", 16, id="2, the cutoff"),
        pytest.param(strategy_survivor_book, "evening", "paper book", 16, id="8"),
        pytest.param(strategy_survivor_summary, "evening_document", "summary", 16, id="9"),
    ],
)
def test_a_fault_no_survivor_step_anticipated_is_that_steps_refusal_with_its_requests(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    seeded: Path,
    owner: Any,
    name: str,
    step: str,
    spent: int,
) -> None:
    """Every survivor step runs inside `_step(..., steps=SURVIVOR_STEPS)`: a fault it did not
    anticipate stops the day as that step's refusal, numbered in the survivor order, and the
    requests line is printed as for every other refusal -- never a traceback."""
    world = _world(tmp_path, monkeypatch, seeded)
    monkeypatch.setattr(owner, name, _injected)

    code, _out, err = _run(world, capsys)

    number = daily.SURVIVOR_STEPS.index(step) + 1
    assert code == 1
    assert err[0] == f"step {number} ({step}) failed: an unexpected fault: RuntimeError: injected"
    assert sum(_requests(err[1]).values()) == spent
    assert len(err) == 2


# --- the panel step's bound and the result's kind ------------------------------------------------


def test_the_panel_steps_catch_up_bound_is_the_registered_adapters() -> None:
    """One reader of `max_panel_catch_up_sessions`: the adapter block, bounded 0..16 there."""
    for bound in (0, 3, 16):
        survivor = _survivor(survivor_config(max_panel_catch_up_sessions=bound))
        assert daily.survivor_panel_update(survivor) == daily.SurvivorPanelUpdate(
            max_catch_up_sessions=bound
        )
    assert not hasattr(daily, "panel_catch_up_bound")
    assert SURVIVOR_CONFIG["adapter"]["max_panel_catch_up_sessions"] == (
        daily.DEFAULT_MAX_PANEL_CATCH_UP_SESSIONS
    )


def test_the_days_result_kind_is_the_survivor_documents() -> None:
    """`main` tells a survivor result from a P6 one by its `kind`, spelled in the script so that a
    P6 day need not import the summary module."""
    assert daily.SURVIVOR_RESULT_KIND == strategy_survivor_summary.SURVIVOR_DOCUMENT_KIND


def test_the_survivor_day_resolves_the_registered_transform_and_its_four_builds() -> None:
    survivor = _survivor()
    builds = daily.survivor_builds(survivor)

    assert [build.factor for build in builds] == list(BOOK.composite_factors)
    assert {(build.tier, build.transform, build.neutralization) for build in builds} == {
        ("processed", "cross_section_standard/v1", None)
    }
    assert FACTOR_TRANSFORMS.get(survivor.adapter.transform).qualified_key == builds[0].transform


# --- an evening and the call auction it plans for (the D5 joint review, Important 1) ---------


def _passed(day: date) -> str:
    """The line the ORDERS block prints under its header when the run that printed the plan was
    made after the call auction of `day`, the session the orders are for."""
    return (
        f"  the call auction of {day.isoformat()} has passed (it began {day.isoformat()}"
        "T09:15:00+08:00): these orders were for it, and this run was made after it -- the paper "
        "book fills them at that open whether or not they were placed"
    )


def test_an_evening_not_committed_by_the_next_call_auction_is_refused_and_settled_next_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    seeded: Path,
) -> None:
    """The review's probe: 6 April pinned at 18:30, its record filed at step 7 and its paper book
    refused. Run again on 7 April at 10:00 -- past the call auction its orders were for, before the
    next panel update -- the evening is not decided: step 8 refuses by name before anything is
    written, the day files no `result`, and 7 April's run settles 6 April without a decision
    before its own evening."""
    world = _world(tmp_path, monkeypatch, seeded)
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(strategy_survivor_book, "evening", _refused_evening)
        _code, _out, err = _run(world, capsys)
    assert err[0] == INJECTED, err
    paper = _files(world.runtime / "paper")
    records = _records(world)

    code, out, err = _run(world, capsys, clock=after_the_auction(DAY))

    assert code == 1
    assert err[0].startswith("step 8 (paper book) failed: ")
    assert "2026-04-07T09:15:00+08:00" in err[0]
    assert "the call auction of the next session has passed" in err[0]
    assert "nothing was decided or written" in err[0]
    assert "the next session's run settles 2026-04-06 without a decision" in err[0]
    assert err[1:] == [NO_REQUEST]
    journal = _journal(world)
    assert "result" not in journal and "paper" not in journal
    assert DAY not in _snapshots(world).sessions()
    assert _files(world.runtime / "paper") == paper
    assert _records(world) == records
    shown = json.loads(out)
    assert (shown["paper"], shown["paper_skipped"]["reason"]) == (None, err[0])

    nxt = _completed(world, capsys, NEXT)

    assert [(day["session"], day["outcome"]) for day in nxt["paper"]["catch_ups"]] == [
        (DAY.isoformat(), "created")
    ]
    assert nxt["paper"]["outcome"] == "created"
    assert _snapshot(world, DAY).pending_orders == ()


def test_an_evening_completed_or_printed_after_the_call_auction_it_planned_for_says_so(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    seeded: Path,
) -> None:
    """A committed evening completed the next morning -- its `result` lost to a power cut -- and
    the same plan printed again later: the ORDERS block says under its header that the call
    auction of 7 April, the session the orders were for, has passed. A plan printed before it
    says nothing of the kind; the line is the printing run's, never filed."""
    world = _world(tmp_path, monkeypatch, seeded)
    with pytest.MonkeyPatch.context() as patch:
        _cut_at_the_result(patch)
        code, _out, _err = _run(world, capsys)
    assert code == 1
    late = after_the_auction(DAY)

    code, out, err = _run(world, capsys, clock=late, json_output=False)

    assert code == 0, err
    lines = out.splitlines()
    (header,) = [line for line in lines if line.startswith("ORDERS FOR THE OPEN OF D+1")]
    assert ", 2026-04-07 -- trade list " in header
    assert lines[lines.index(header) + 1] == _passed(NEXT)
    assert lines[lines.index(header) + 2] == REFERENCE_NOTE
    assert "after_the_call_auction" not in json.dumps(_journal(world))

    code, out, _err = _run(world, capsys, clock=late + timedelta(days=2), json_output=False)

    assert code == 0
    assert REPRINTED in out.splitlines()
    assert _passed(NEXT) in out.splitlines()
    code, shown, _err = _run(world, capsys, clock=late)
    assert json.loads(shown)["this_run"] == {
        "requests": {},
        "request_count": 0,
        "wrote": False,
        "after_the_call_auction": {
            "session": NEXT.isoformat(),
            "call_auction": "2026-04-07T09:15:00+08:00",
        },
    }
    code, out, _err = _run(world, capsys, clock=RUN_CLOCK, json_output=False)
    assert code == 0
    assert not any("has passed" in line for line in out.splitlines())


# --- a catch-up committed and not journalled (the D5 joint review, Important 3) --------------


@pytest.mark.parametrize(
    "cut",
    [
        pytest.param(NEXT, id="the first missed session's entry"),
        pytest.param(THIRD, id="the second missed session's entry"),
    ],
)
def test_a_catch_up_committed_and_not_journalled_is_derived_again_and_documented_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    seeded: Path,
    cut: date,
) -> None:
    """6 April completes; 7 and 8 April are missed, and a name bought on 6 April is delisted on 8
    April with no bar. 9 April's run catches up 7 April -- the ten buys fill at its open -- and 8
    April -- the delisted holding is written off -- and the power is cut as `cut`'s catch-up is
    journalled, after its commit. The rerun derives that session again (C3: `unchanged`, nothing
    written) and files it: the ten fills and the write-off are in the rerun's document exactly
    once, the summary names the session re-derived, and the ledger's rows of the sessions the
    first run settled are byte for byte what it wrote."""
    world = _world(tmp_path, monkeypatch, seeded)
    first = _completed(world, capsys, DAY)
    bought = sorted(_orders(first))
    gone = sorted(order["ts_code"] for order in first["paper"]["trade_list"]["orders"])[0]
    world.market.delisted = ((gone, THIRD),)
    written = daily.write_journal

    def cut_as_journalled(path: Path, body: Mapping[str, Any]) -> None:
        filed = [entry["session"] for entry in body.get("catch_ups", ())]
        if filed and filed[-1] == cut.isoformat():
            raise OSError("the power was cut as a committed catch-up was journalled")
        written(path, body)

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(daily, "write_journal", cut_as_journalled)
        code, _out, err = _day(world, capsys, FOURTH)

    assert code == 1
    assert err[0] == (
        "step 8 (paper book) failed: an unexpected fault: OSError: the power was cut as a "
        "committed catch-up was journalled"
    )
    assert cut in _snapshots(world).sessions()
    assert FOURTH not in _snapshots(world).sessions()
    filed = [day["session"] for day in _journal(world, FOURTH).get("catch_ups", ())]
    assert filed == ([] if cut == NEXT else [NEXT.isoformat()])

    def rows_through_the_cut() -> list[str]:
        rows = _ledger_rows(world, tmp_path)
        return [row.model_dump_json() for row in rows if row.after.as_of <= cut]

    rows = rows_through_the_cut()

    fourth = _completed(world, capsys, FOURTH)

    days = fourth["paper"]["catch_ups"]
    assert [(day["session"], day["rederived"]) for day in days] == [
        (NEXT.isoformat(), cut == NEXT),
        (THIRD.isoformat(), cut == THIRD),
    ]
    assert [day["outcome"] for day in days if day["rederived"]] == ["unchanged"]
    assert sorted(days[0]["settled"]["filled"]) == bought
    filled = [order for day in days for order in day["settled"]["filled"]]
    filled += fourth["paper"]["settled"]["filled"]
    assert sorted(order for order in filled if order in bought) == bought
    written_off = [item for day in days for item in day["settled"]["written_off"]]
    written_off += fourth["paper"]["settled"]["written_off"]
    assert [item["ts_code"] for item in written_off] == [gone]
    assert days[1]["settled"]["written_off"] == written_off
    assert rows_through_the_cut() == rows
    assert _journal(world, FOURTH)["catch_ups"] == days
    _replays(world, tmp_path, first, fourth)

    _code, out, _err = _day(world, capsys, FOURTH, json_output=False)

    (line,) = [line for line in out.splitlines() if line.startswith("catch-up ")]
    assert line.endswith(
        f"oldest first; re-derived after an interrupted journal: {cut.isoformat()} (committed by "
        "an earlier run of the day, which stopped before journalling them; derived again now, "
        "nothing written)"
    )


# --- a rerun's clock and the step 7/8 crash windows (the D5 joint review, Minor 1 and 2) ------


def _ticking(start: datetime) -> Callable[[], datetime]:
    """A clock that has moved one second on each time it is read, from `start`: a rerun's clock
    is never the first run's, and every read of it differs from the last."""
    ticks = itertools.count()
    return lambda: start + timedelta(seconds=next(ticks))


def _cut_at_the_prediction(patch: pytest.MonkeyPatch) -> None:
    """The power cut as the journal's `prediction` stage is written: step 7 has filed the record
    (and its provenance before it), and the journal never says so."""
    written = daily.write_journal

    def cut(path: Path, body: Mapping[str, Any]) -> None:
        if "prediction" in body:
            raise OSError("the power was cut as the prediction was journalled")
        written(path, body)

    patch.setattr(daily, "write_journal", cut)


CUT_AT_THE_PREDICTION: Final[str] = (
    "step 7 (prediction) failed: an unexpected fault: OSError: the power was cut as the "
    "prediction was journalled"
)


def _provenance(world: tds.World) -> list[Path]:
    return sorted((world.runtime / "daily_survivor_provenance").glob("*.json"))


def test_a_record_filed_and_not_journalled_is_reused_with_its_own_provenance_on_a_moving_clock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    seeded: Path,
) -> None:
    """Minor 2: step 7 files the provenance, then the record, and the power is cut before the
    journal's `prediction` stage. A rerun on a clock of its own -- every read later than the
    first run's -- finds the record held for the session and reuses it: no second provenance is
    written (a moving `recorded_at` would give it another digest), and the journal names the held
    record's own provenance, whose batch digest is the record's."""
    world = _world(tmp_path, monkeypatch, seeded)
    with pytest.MonkeyPatch.context() as patch:
        _cut_at_the_prediction(patch)
        code, _out, err = _run(world, capsys, clock=_ticking(RUN_CLOCK))
    assert (code, err[0]) == (1, CUT_AT_THE_PREDICTION)
    (record,) = _records(world)
    (written,) = _provenance(world)

    code, _out, err = _run(world, capsys, clock=_ticking(RUN_CLOCK + timedelta(minutes=10)))

    assert code == 0, err
    assert _records(world) == (record,)
    assert _provenance(world) == [written]
    prediction = _journal(world)["prediction"]
    assert (prediction["record_id"], prediction["outcome"]) == (record, "unchanged")
    assert prediction["provenance"] == written.stem
    held = FilePredictionStore(world.runtime / "predictions", clock=lambda: RUN_CLOCK).get(record)
    assert held is not None
    provenance = strategy_survivor_registration.read_survivor_provenance(
        written, registration_sha256=_journal(world)["registration"]["sha256"]
    )
    assert provenance.batch_digest == prediction["batch_digest"] == batch_digest(held.batch)


def test_a_record_filed_and_not_journalled_run_again_after_the_call_auction_is_journalled(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    seeded: Path,
) -> None:
    """Minor 2, after the cutoff: the same crash window, run again on 7 April at 10:00. Step 7 no
    longer says "Nothing was filed" of a record that is filed: it journals the held record with
    its own provenance, writing nothing else, and step 8 refuses the evening by name -- the call
    auction it was for has passed (Important 1)."""
    world = _world(tmp_path, monkeypatch, seeded)
    with pytest.MonkeyPatch.context() as patch:
        _cut_at_the_prediction(patch)
        code, _out, err = _run(world, capsys, clock=_ticking(RUN_CLOCK))
    assert (code, err[0]) == (1, CUT_AT_THE_PREDICTION)
    (record,) = _records(world)
    (written,) = _provenance(world)

    code, _out, err = _run(world, capsys, clock=_ticking(after_the_auction(DAY)))

    assert code == 1
    assert err[0].startswith("step 8 (paper book) failed: ")
    assert "the call auction of the next session has passed" in err[0]
    assert err[1:] == [NO_REQUEST]
    journal = _journal(world)
    assert (journal["prediction"]["record_id"], journal["prediction"]["provenance"]) == (
        record,
        written.stem,
    )
    assert "result" not in journal
    assert _records(world) == (record,)
    assert _provenance(world) == [written]
    assert DAY not in _snapshots(world).sessions()


def test_a_held_record_whose_provenance_is_gone_is_refused_and_none_is_written_after_it(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    seeded: Path,
) -> None:
    """A provenance is written once, before its record, and never after it: a rerun that finds the
    record held and its provenance removed refuses by name at step 7, writing none."""
    world = _world(tmp_path, monkeypatch, seeded)
    with pytest.MonkeyPatch.context() as patch:
        _cut_at_the_prediction(patch)
        _run(world, capsys)
    (record,) = _records(world)
    (written,) = _provenance(world)
    written.unlink()

    code, _out, err = _run(world, capsys)

    assert code == 1
    assert err[0].startswith(f"step 7 (prediction) failed: {record} is filed for 2026-04-06, and ")
    assert "no provenance of this registration" in err[0]
    assert "never after it, so none is written now" in err[0]
    assert err[1:] == [NO_REQUEST]
    assert _provenance(world) == []
    assert "prediction" not in _journal(world)


def test_a_settle_only_day_cut_at_its_result_is_finished_by_a_rerun_that_writes_nothing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    seeded: Path,
) -> None:
    """The review's untested path: a day pinned after the next call auction settles 6 April
    alone, commits it, and the power is cut as its `result` is written. Run again through `main`,
    on a clock of its own, the journal's pinned clock makes it settle-only again: `settle_only`
    derives its own held session again (C3: `unchanged`), nothing is written to the paper book,
    the session's catch-up is listed once -- as its commit filed it -- and the `result` is filed."""
    late = after_the_auction(DAY)
    world = _world(tmp_path, monkeypatch, seeded)
    with pytest.MonkeyPatch.context() as patch:
        _cut_at_the_result(patch)
        code, _out, err = _run(world, capsys, as_of=late, clock=late)
    assert code == 1
    assert err[0] == (
        "step 9 (summary) failed: an unexpected fault: OSError: the power was cut as the "
        "journal's result was written"
    )
    journal = _journal(world)
    assert "result" not in journal and "paper" not in journal
    (filed,) = journal["catch_ups"]
    assert (filed["session"], filed["outcome"]) == (DAY.isoformat(), "created")
    assert filed["snapshot_id"] == _snapshot(world, DAY).snapshot_id
    paper = _files(world.runtime / "paper")

    code, out, err = _run(world, capsys, as_of=late, clock=_ticking(late + timedelta(hours=1)))

    assert code == 0, err
    result = json.loads(out)
    assert result["paper"] is None
    assert "settles only" in result["paper_skipped"]["reason"]
    assert result["paper_skipped"]["catch_ups"] == [filed]
    assert _files(world.runtime / "paper") == paper
    assert _records(world) == ()
    stage = _journal(world)["paper"]
    assert (stage["mode"], stage["snapshot_id"]) == ("settle_only", filed["snapshot_id"])
    assert _journal(world)["result"] == {key: result[key] for key in result if key != "this_run"}
    assert result["this_run"]["request_count"] == 0


def test_an_evening_not_committed_is_never_decided_on_a_later_read_of_the_market(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    seeded: Path,
) -> None:
    """Minor 1: the read at the newest readable instant is a committed day's completion only (D3
    review, rule 5). An evening not committed whose market cannot be read at its pinned clock
    -- a whole partition holding a row knowable after it -- is refused at step 8 with that fault,
    and is not decided on a later read, even where the panel could be read later."""
    world = _world(tmp_path, monkeypatch, seeded)
    monkeypatch.setattr(daily, "_readable_after", lambda _store, _error, *, at: at + timedelta(1))
    run_evening = strategy_survivor_book.evening
    read_at: list[datetime] = []

    def unreadable_at_the_pin(**keywords: Any) -> Any:
        read_at.append(keywords["as_of"])
        if keywords["as_of"] == DAY_AS_OF:
            raise PartitionNotYetKnowableError("index_daily 2026 holds a row knowable later")
        return run_evening(**keywords)

    monkeypatch.setattr(strategy_survivor_book, "evening", unreadable_at_the_pin)

    code, _out, err = _run(world, capsys)

    assert code == 1
    assert read_at == [DAY_AS_OF]
    assert err[0] == (
        "step 8 (paper book) failed: an unexpected fault: PartitionNotYetKnowableError: "
        "index_daily 2026 holds a row knowable later"
    )
    assert DAY not in _snapshots(world).sessions()
    assert "result" not in _journal(world)


# --- refused before the day pins anything (the D5 joint review, Minor 4 and 5) ----------------


def test_a_first_run_older_than_the_panels_newest_session_is_refused_before_any_request(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    seeded: Path,
) -> None:
    """Minor 4: 7 April's run updates the panel through 7 April and stops at its facts (the book
    opened on its epoch, nothing settled). A first run for 6 April -- `--as-of` its evening --
    would fetch and rewrite the store's partitions at that older instant before `panel build`
    refused it: a day not committed whose panel has moved past its session is refused at step 2
    before any request and before its pin, and nothing is written."""
    world = _world(tmp_path, monkeypatch, seeded)
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(daily, "build_factors", _no_factor_build)
        code, _out, err = _day(world, capsys, NEXT)
    assert (code, err[0].startswith("step 6 (facts) failed:")) == (1, True), err
    panel, paper = _files(world.runtime / "panel"), _files(world.runtime / "paper")
    assert panel and paper
    world.market.clear()

    code, _out, err = _run(world, capsys)

    assert code == 1
    assert err[0].startswith(
        "step 2 (panel update) failed: the panel holds sessions after 2026-04-06, through "
        "2026-04-07"
    )
    assert "no evening of it was committed" in err[0]
    assert "the next session's run settles it without a decision" in err[0]
    assert "Nothing was fetched or written" in err[0]
    assert err[1:] == [NO_REQUEST]
    assert world.market.payloads == []
    assert _files(world.runtime / "panel") == panel
    assert _files(world.runtime / "paper") == paper
    assert not _journal_path(world).exists()


def test_a_committed_evening_whose_journal_is_gone_is_refused_after_the_auction_with_its_remedy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    seeded: Path,
) -> None:
    """Minor 5: 6 April's evening is committed and its journal deleted by hand. Run again after
    the next call auction, the day would be pinned settle-only, and a session an evening decided
    is never settled again without its decision: it could never complete. Refused by name at the
    paper head -- the cause, and the remedy -- before any request and before its pin. The remedy
    works: `--as-of` the session's evening makes the day an evening again, which derives the
    committed session again, writing nothing, and prints that the call auction has passed."""
    world = _world(tmp_path, monkeypatch, seeded)
    first = _completed(world, capsys, DAY)
    _journal_path(world).unlink()
    late = after_the_auction(DAY)
    before = _files(world.runtime)

    code, _out, err = _run(world, capsys, as_of=late, clock=late)

    assert code == 1
    assert err[0].startswith(
        f"step 5 (paper head) failed: the paper book holds the evening of 2026-04-06, committed "
        f"as {first['paper']['snapshot_id']}"
    )
    assert f"its journal {_journal_path(world)} is missing" in err[0]
    assert "run the day again with --as-of 2026-04-06T18:30:00+08:00" in err[0]
    assert "Nothing was fetched or written" in err[0]
    assert err[1:] == [NO_REQUEST]
    assert _files(world.runtime) == before
    paper = _files(world.runtime / "paper")

    code, out, err = _run(world, capsys, clock=late, json_output=False)

    assert code == 0, err
    assert _files(world.runtime / "paper") == paper
    lines = out.splitlines()
    assert any(
        line.endswith("evening unchanged (settled earlier; derived again, nothing written)")
        for line in lines
    )
    assert _passed(NEXT) in lines
    assert _journal(world)["paper"]["snapshot_id"] == first["paper"]["snapshot_id"]


def test_a_settle_only_day_whose_journal_is_gone_is_completed_again_after_the_auction(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    seeded: Path,
) -> None:
    """The refusal of Minor 5 is only for an evening: a session settled without a decision,
    re-pinned settle-only once its journal is gone, is derived again by `settle_only` (C3:
    `unchanged`), writing nothing, and the day completes."""
    late = after_the_auction(DAY)
    world = _world(tmp_path, monkeypatch, seeded)
    code, _out, err = _run(world, capsys, as_of=late, clock=late)
    assert code == 0, err
    _journal_path(world).unlink()
    paper = _files(world.runtime / "paper")

    code, out, err = _run(world, capsys, as_of=late, clock=late)

    assert code == 0, err
    result = json.loads(out)
    assert result["paper"] is None
    assert [(day["session"], day["outcome"]) for day in result["paper_skipped"]["catch_ups"]] == [
        (DAY.isoformat(), "unchanged")
    ]
    assert _files(world.runtime / "paper") == paper


def test_an_epoch_whose_years_calendar_is_not_stored_is_refused_with_the_build_that_stores_it(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    seeded: Path,
) -> None:
    """Minor 8: a first run in early January whose epoch is the December session before it finds
    the epoch's year without a stored calendar. The refusal prints the `panel build` that stores
    it -- `trade_cal` alone, for the epoch's year, pinned at the epoch's publication -- as step 2's
    own calendar refusal does, having fetched nothing."""
    world = _world(tmp_path, monkeypatch, seeded, config=survivor_config(epoch=date(2025, 12, 31)))

    code, _out, err = _run(world, capsys)

    assert code == 1
    assert err[0].startswith(
        "step 2 (panel update) failed: the SSE calendar of 2025, on which the registered epoch "
        "2025-12-31 is checked to be an open session, is not stored"
    )
    assert err[1] == "  " + " ".join(
        [
            "openalpha panel build --runtime-dir",
            shlex.quote(str(world.runtime)),
            "--year 2025 --as-of 2025-12-31T08:30:00+00:00 --exchange SSE --dataset trade_cal",
        ]
    )
    assert err[2] == "then rerun this command"
    assert err[3:] == [NO_REQUEST]
    assert world.market.payloads == []
    assert not (world.runtime / "daily_survivor").exists()
