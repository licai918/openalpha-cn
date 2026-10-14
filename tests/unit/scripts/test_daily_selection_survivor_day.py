"""`scripts/daily_selection.py`: the survivor book's day, steps 1 to 7 (`V2-P7-008`, task D5a).

A registration whose configuration names `kind: "survivor_book/v1"` is dispatched to the survivor
day (`test_daily_selection_dispatch.py`); this file runs that day for real. The paper book's writer
lock is taken first and held to the end of the day; the day's clock and the book's holdings are
pinned in its own journal before any request; then the panel update (`targets_for_survivor`, the
registered catch-up bound), the doctor, the four composite factors built at the processed tier,
the paper head opened and bound to the registration, the facts day read with the pinned holdings
and screened, and the forward record `survivor_book_composite/v1` filed -- before the paper step,
so that a fault of the paper book never costs the record. Step 8, the paper book, is D5b's: the day
stops there by name, with the requests it spent, so every day here ends at that stub or before it,
and a rerun of such a day is the ordinary case.

The market is `daily_market.Market` over 120 securities -- the registered transform refuses a cross
section under 100 (`CROSS_SECTION_STANDARD.min_cross_section`) -- and every weekday of 2026, with
each security's turnover its own, so that `turnover_60` has a cross section to standardise. The
panel is seeded once per module through the real `openalpha panel build` and copied into each
test's runtime directory; the registrations are committed into throwaway repositories with
`test_daily_selection`'s own helpers. Nothing reaches the network.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Final

import pytest
import test_daily_selection as tds
from daily_market import Market
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
from openalpha_cn.backtest.paper_state import held_head
from openalpha_cn.domain.adjustment import ADJ_FACTOR_DATASET
from openalpha_cn.domain.daily_prices import DAILY_BASIC_DATASET
from openalpha_cn.domain.index_prices import INDEX_DAILY_DATASET
from openalpha_cn.domain.price_limits import PRICE_LIMIT_DATASET
from openalpha_cn.domain.stock_universe import STOCK_BASIC_DATASET
from openalpha_cn.domain.survivor_book import SurvivorBookConfig
from openalpha_cn.domain.survivor_screen import screen
from openalpha_cn.domain.trading_calendar import TRADING_CALENDAR_DATASET
from openalpha_cn.panel.store import PanelStore
from openalpha_cn.panel_factors import FACTOR_TRANSFORMS
from openalpha_cn.panel_ingest import load_trading_calendar
from openalpha_cn.storage.paper_state import FilePaperStateStore
from openalpha_cn.storage.predictions import FilePredictionStore
from openalpha_cn.strategy_survivor_market import SessionOrdinals

daily = tds.daily

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

STUB: Final[str] = "step 8 (paper book) failed: not wired yet (V2-P7-008 D5b)"
NO_REQUEST: Final[str] = "tushare requests   0 this run {}"


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
    clock: datetime = RUN_CLOCK,
    extra: Sequence[str] = (),
    json_output: bool = True,
) -> tuple[int, str, list[str]]:
    """The day through `main`, as the scheduler runs it: no `--dataset`, so the day fetches its
    own targets. Returns the exit code, stdout and stderr's lines."""
    world.market.today = as_of.astimezone(daily.SHANGHAI).date()
    arguments = ["--runtime-dir", str(world.runtime), "--registration", str(world.registration)]
    arguments += ["--repo", str(world.repo), "--as-of", as_of.isoformat(), *extra]
    if json_output:
        arguments.append("--json")
    code = daily.main(arguments, clock=lambda: clock)
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


# --- the day, end to end ----------------------------------------------------------------------


def test_a_survivor_day_runs_steps_one_to_seven_and_stops_at_the_paper_book_by_name(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    seeded: Path,
) -> None:
    """Dispatched, the day fetches what an ordinary survivor day fetches (the 16 requests
    `test_daily_selection_survivor.py` pins), opens and binds the paper book on its epoch, and
    files the day's eligible composites as a forward record before it reaches step 8 -- which
    refuses until D5b, numbered in the survivor order, with the requests spent."""
    world = _world(tmp_path, monkeypatch, seeded)

    code, out, err = _run(world, capsys)

    assert code == 1 == daily.DailyExit.step_failed
    assert out == ""
    assert err[0] == STUB
    assert _requests(err[1]) == STEADY_SURVIVOR
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
    assert "result" not in journal
    # The paper book: bound to this registration, opened on its epoch, nothing settled.
    marker = json.loads((world.runtime / "paper" / "registration.json").read_text("utf-8"))
    assert marker["config_id"] == _config_id(world)
    assert marker["epoch_session"] == EPOCH.isoformat()
    head = held_head(FilePaperStateStore(world.runtime / "paper" / "snapshots"))
    assert head is not None
    assert (head.session, head.session_index, head.book.positions) == (EPOCH, 0, ())
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
    prediction = journal["prediction"]
    assert prediction["registered"] is True
    assert prediction["record_id"] == record_id
    assert prediction["family"] == "survivor_book_composite/v1"
    assert prediction["horizon"] == "20d"
    assert prediction["scored"] == len(eligible)
    assert prediction["outcome"] == "created"
    # Its provenance was filed before it, under its digest.
    filed = world.runtime / "daily_survivor_provenance" / f"{prediction['provenance']}.json"
    provenance = strategy_survivor_registration.read_survivor_provenance(
        filed,
        registration_sha256=journal["registration"]["sha256"],
    )
    assert provenance.held == ()
    assert provenance.session == DAY


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
    journalled: a rerun of the day spends no request, files no second record or provenance, and
    stops at the same step."""
    world = _world(tmp_path, monkeypatch, seeded)
    _run(world, capsys)
    before = _files(world.runtime)
    records = _records(world)

    code, _out, err = _run(world, capsys)

    assert code == 1
    assert err == [STUB, NO_REQUEST]
    assert _records(world) == records
    assert _files(world.runtime) == before


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
    nothing -- its scores would not precede their outcome -- and is no error: the day goes on as a
    settle-only day to the paper book."""
    late = datetime.fromisoformat("2026-04-07T10:00:00+08:00")
    world = _world(tmp_path, monkeypatch, seeded)

    code, _out, err = _run(world, capsys, as_of=late, clock=late)

    assert code == 1
    assert err[0] == STUB
    prediction = _journal(world)["prediction"]
    assert prediction["registered"] is False
    assert prediction["settle_only"] is True
    assert "call auction" in prediction["reason"]
    assert _records(world) == ()


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
    is moved aside the day runs as a first day, and a rerun of it as a rerun. (Pinned from the
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
    code, _out, err = _run(world, capsys)

    assert code == 1
    assert err[0] == STUB
    assert _requests(err[1]) == STEADY_SURVIVOR
    assert _journal(world)["held_from"] is None

    code, _out, err = _run(world, capsys)

    assert code == 1
    assert err == [STUB, NO_REQUEST]


def _rerun_pinned_from_a_held_book(
    world: tds.World, capsys: pytest.CaptureFixture[str]
) -> dict[str, Any]:
    """The day run once (it opens the book), its journal lost, and the day run again: the second
    pin is taken from a held book. Returns that journal."""
    _run(world, capsys)
    _journal_path(world).unlink()
    _code, _out, err = _run(world, capsys)
    assert err[0] == STUB, err
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
    assert journal["held"] == []
    assert journal["held_from"] == {
        "session": EPOCH.isoformat(),
        "session_index": 0,
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

    code, _out, err = _run(world, capsys, extra=[*extra, "--max-staleness-days", "45"])

    assert code == 1
    assert err[0] == STUB
    assert _journal(world)["panel"]["targets"] == list(targets)
    assert _requests(err[1])["index_weight"] >= 1
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
