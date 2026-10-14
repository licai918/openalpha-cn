"""`scripts/daily_selection.py`, the survivor form's panel step (`V2-P7-016`).

What the survivor book's day fetches, how far behind a stored panel it catches up in one run,
and whether what it fetched is what `survivor_facts.build_facts_day` reads -- New Year included.

The survivor form is dispatched by the registration's `kind` in `V2-P7-008`, so `main` cannot
reach it yet. These tests drive the seam that dispatcher calls -- `targets_for_survivor`,
`panel_catch_up_bound` and `update_panel(..., survivor=...)` -- through what the P6 tests use:
the scripted market (`daily_market.Market`), the real `openalpha panel build` in process, and
the command's own request counter (`counted_transport`). Nothing reaches the network; every
panel is generated at test time. The P6 step is measured beside each survivor number, on the
same market, so the two forms cannot drift apart unnoticed.
"""

from __future__ import annotations

import importlib
import shlex
import sys
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any, Final

import pytest
from daily_market import SECURITIES, Market, compact, response, weekdays

from openalpha_cn import cli, survivor_facts
from openalpha_cn.domain.adjustment import ADJ_FACTOR_DATASET
from openalpha_cn.domain.daily_prices import DAILY_BASIC_DATASET, DAILY_DATASET
from openalpha_cn.domain.index_prices import INDEX_DAILY_DATASET
from openalpha_cn.domain.name_history import NAMECHANGE_DATASET
from openalpha_cn.domain.price_limits import PRICE_LIMIT_DATASET, SUSPENSION_DATASET
from openalpha_cn.domain.stock_universe import STOCK_BASIC_DATASET
from openalpha_cn.domain.survivor_book import DEFAULT_COMPOSITE_FACTORS, SurvivorBookConfig
from openalpha_cn.domain.trading_calendar import TRADING_CALENDAR_DATASET
from openalpha_cn.factor_view import resolve_factor
from openalpha_cn.panel.catalog import PanelStorageError
from openalpha_cn.panel.store import PanelStore
from openalpha_cn.panel_factors import FACTOR_TRANSFORMS
from openalpha_cn.panel_ingest import load_name_histories, session_publication_instant
from openalpha_cn.providers.tushare import TUSHARE_STK_LIMIT_ROW_CAP

ROOT: Final[Path] = Path(__file__).resolve().parents[3]


def _script(name: str, directory: Path) -> ModuleType:
    if str(directory) not in sys.path:
        sys.path.insert(0, str(directory))
    return importlib.import_module(name)


daily = _script("daily_selection", ROOT / "scripts")

EXCHANGE: Final[str] = "SSE"
TRANSFORM: Final[str] = "cross_section_standard/v1"
SURVIVOR_BUILDS: Final[tuple[Any, ...]] = tuple(
    daily.TierBuild(factor=factor, tier="processed", transform=TRANSFORM, neutralization=None)
    for factor in DEFAULT_COMPOSITE_FACTORS
)
"""The four composite factors at the processed tier: what the survivor form's step 4 builds."""
WALL_CLOCK: Final[datetime] = datetime(2027, 6, 1, 4, 0, tzinfo=UTC)
"""The provider's wall clock: after every frame here, so it never binds what a fetch may know."""
OPEN_2026: Final[tuple[date, ...]] = weekdays(date(2026, 1, 5), date(2026, 12, 31))
NEW_YEAR: Final[tuple[date, ...]] = weekdays(
    date(2026, 12, 14), date(2027, 1, 15), closed=(date(2027, 1, 1),)
)
"""2026 opens on 14 December; 1 January 2027 is a holiday, so 4 January is 2027's first session."""
SEED_TARGETS: Final[tuple[str, ...]] = (
    TRADING_CALENDAR_DATASET,
    STOCK_BASIC_DATASET,
    ADJ_FACTOR_DATASET,
    "price",
    PRICE_LIMIT_DATASET,
    NAMECHANGE_DATASET,
    INDEX_DAILY_DATASET,
)
"""What a store the survivor book has been running on holds: every survivor target."""

READ_BY_BUILD_FACTS_DAY: Final[frozenset[str]] = frozenset(
    {
        TRADING_CALENDAR_DATASET,
        STOCK_BASIC_DATASET,
        NAMECHANGE_DATASET,
        SUSPENSION_DATASET,
        PRICE_LIMIT_DATASET,
        DAILY_DATASET,
    }
)
"""The panel datasets `survivor_facts._read_facts_day` reads besides the factor tiers: the
calendar, the registry, the name corpus, the halts, the session's bands and the bars it walks
back through. The factor tiers are step 4's, built from the datasets each factor declares.

Not taken on trust: the New Year test records what `build_facts_day` reads through a
`ReadRecordingStore` and asserts it is exactly this set, so a read added there later turns that
test red until it is named here -- and then the first test asks the survivor day to fetch it."""


class ReadRecordingStore(PanelStore):
    """`PanelStore`, recording the dataset of every gated read made through it.

    `assess_readiness` is the door every gated read passes: `assessed()` calls it, and
    `read_if_ready()` and `read_visible_at()` both go through `assessed()`. The one ungated read,
    `query()`, has a single caller in `src/`, which puts stored rows back rather than answering
    with them (its docstring); a catalog lookup (`registered_years`, `read_coverage`) reads no
    row and is not recorded."""

    def __init__(self, root: Path) -> None:
        super().__init__(root)
        self.read: set[str] = set()

    def assess_readiness(self, requirement: Any) -> Any:
        self.read.add(requirement.dataset)
        return super().assess_readiness(requirement)


def _evening(day: date) -> datetime:
    """18:30 Shanghai on `day`: the scheduled run's clock."""
    return datetime.combine(day, time(18, 30), tzinfo=daily.SHANGHAI)


@dataclass
class Store:
    runtime: Path
    market: Market

    @property
    def panel(self) -> PanelStore:
        return PanelStore(self.runtime / "panel")


def _seeded(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    market: Market,
    *,
    as_of: str,
    year: int,
) -> Store:
    """Every survivor target built through the real `panel build` against `market`, at `as_of`."""
    monkeypatch.setenv("TUSHARE_TOKEN", "sk-survivor-panel-step-token")
    monkeypatch.setattr(cli, "_panel_transport", lambda: market)
    monkeypatch.setattr(cli, "_panel_clock", lambda: WALL_CLOCK)
    market.today = datetime.fromisoformat(as_of).date()
    runtime = tmp_path / "runtime"
    arguments = ["panel", "build", "--runtime-dir", str(runtime), "--year", str(year)]
    arguments += ["--as-of", as_of, "--exchange", EXCHANGE, "--json"]
    for target in SEED_TARGETS:
        arguments += ["--dataset", target]
    seeded = daily.invoke(arguments)
    assert seeded.exit_code == 0, seeded.reason()
    market.clear()
    return Store(runtime=runtime, market=market)


def _update(
    store: Store,
    session: date,
    *,
    targets: Sequence[str],
    survivor: Any = None,
    next_year: bool = False,
) -> tuple[dict[str, Any], Counter[str]]:
    """Step 2 for `session` at its evening, and the requests it made by `api_name`. The market's
    own record is cleared first, so it holds this run's requests only."""
    store.market.clear()
    store.market.today = session
    counts: Counter[str] = Counter()
    keywords: dict[str, Any] = {} if survivor is None else {"survivor": survivor}
    with daily.counted_transport(counts):
        panel = daily.update_panel(
            store.runtime,
            session=session,
            as_of=_evening(session),
            exchange=EXCHANGE,
            targets=targets,
            next_year=next_year,
            **keywords,
        )
    return panel, counts


def _survivor(config: Mapping[str, Any] | None = None) -> Any:
    """The survivor form's step-2 rules, its bound read from a registered configuration."""
    return daily.SurvivorPanelUpdate(max_catch_up_sessions=daily.panel_catch_up_bound(config or {}))


def _horizon(store: Store, dataset: str, year: int) -> date | None:
    """The last session `dataset`'s stored `year` covers: where the incremental build slices
    from (`cli._stored_horizon`)."""
    return cli._stored_horizon(store.panel, dataset, year)


def _files(runtime: Path) -> dict[str, bytes]:
    return {
        path.relative_to(runtime).as_posix(): path.read_bytes()
        for path in sorted(runtime.rglob("*"))
        if path.is_file()
    }


# --- what the survivor day fetches ------------------------------------------------------------


def _written(targets: Sequence[str]) -> set[str]:
    return {dataset for target in targets for dataset in cli.PANEL_BUILD_TARGETS[target]}


def test_the_survivor_day_is_the_p6_day_plus_the_name_corpus_and_the_p6_day_is_unchanged() -> None:
    """The four composite factors read `daily`, `daily_basic` and `index_daily`, so the P6 rule
    gives exactly `BASE_TARGETS` for them, which carries every dataset `build_facts_day` reads
    but one: the name corpus. The survivor day adds that target, in `panel build`'s own order,
    and nothing else."""
    survivor = daily.targets_for_survivor(SURVIVOR_BUILDS)
    p6 = daily.targets_for(SURVIVOR_BUILDS)

    assert survivor == (
        TRADING_CALENDAR_DATASET,
        STOCK_BASIC_DATASET,
        ADJ_FACTOR_DATASET,
        "price",
        PRICE_LIMIT_DATASET,
        NAMECHANGE_DATASET,
        INDEX_DAILY_DATASET,
    )
    assert p6 == daily.BASE_TARGETS
    assert NAMECHANGE_DATASET not in daily.BASE_TARGETS
    factor_inputs = {
        dataset
        for factor in DEFAULT_COMPOSITE_FACTORS
        for dataset in resolve_factor(factor).datasets
    }
    assert factor_inputs == {DAILY_DATASET, DAILY_BASIC_DATASET, INDEX_DAILY_DATASET}
    read = READ_BY_BUILD_FACTS_DAY | factor_inputs
    assert read <= _written(survivor)
    assert read - _written(p6) == {NAMECHANGE_DATASET}


def test_a_composite_factor_that_reads_a_statement_brings_its_statement_target_too() -> None:
    """The survivor set is the P6 rule plus the name corpus, not a fixed list."""
    builds = (
        *SURVIVOR_BUILDS,
        daily.TierBuild(
            factor="book_to_price/v1", tier="processed", transform=TRANSFORM, neutralization=None
        ),
    )

    targets = daily.targets_for_survivor(builds)

    assert set(targets) == set(daily.targets_for(builds)) | {NAMECHANGE_DATASET}
    assert "balancesheet" in targets


# --- what an ordinary day costs -----------------------------------------------------------------


SEEDED: Final[str] = "2026-02-03T12:00:00+08:00"
"""2 February has published, 3 February has not. February, so that no January rule applies."""
DAY: Final[date] = date(2026, 2, 3)
STEADY_P6: Final[dict[str, int]] = {
    TRADING_CALENDAR_DATASET: 1,
    STOCK_BASIC_DATASET: 1,
    ADJ_FACTOR_DATASET: 2,
    SUSPENSION_DATASET: 2,
    DAILY_DATASET: 2,
    DAILY_BASIC_DATASET: 2,
    PRICE_LIMIT_DATASET: 2,
    INDEX_DAILY_DATASET: 3,
}
"""An ordinary day of `BASE_TARGETS` (T6-T7 2.2; the runbook's R2 "基础 15"): the calendar and
the registry once, the five session-scoped APIs on the stored last session (the overlap) and on
the day, the three indices once."""
STEADY_SURVIVOR: Final[dict[str, int]] = {**STEADY_P6, NAMECHANGE_DATASET: 1}
"""The same and the session year's name corpus: one request for the whole announcement year."""


@pytest.mark.parametrize("form", ["p6", "survivor"])
def test_an_ordinary_survivor_day_is_sixteen_requests_and_the_p6_day_still_fifteen(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, form: str
) -> None:
    store = _seeded(tmp_path, monkeypatch, Market(open_days=OPEN_2026), as_of=SEEDED, year=2026)

    if form == "p6":
        panel, counts = _update(store, DAY, targets=daily.targets_for(SURVIVOR_BUILDS))
        assert dict(counts) == STEADY_P6
        assert sum(counts.values()) == 15
        assert set(panel) == {
            "targets",
            "year",
            "next_year_calendar",
            "budget",
            "industry_sweep",
            "sessions",
            "partitions",
        }
    else:
        panel, counts = _update(
            store, DAY, targets=daily.targets_for_survivor(SURVIVOR_BUILDS), survivor=_survivor()
        )
        assert dict(counts) == STEADY_SURVIVOR
        assert sum(counts.values()) == 16
        assert panel["caught_up"] == []
    assert _horizon(store, DAILY_DATASET, 2026) == DAY


CAP: Final[int] = TUSHARE_STK_LIMIT_ROW_CAP
"""The provider's own cap, imported rather than copied: a one-shot `stk_limit` answer that
reaches it is refused as truncated, and the request is made again as pages of 4,000."""


@dataclass
class CappedBands(Market):
    """`Market`, with a band cross section past the endpoint's cap from `capped_from` on: `CAP`
    fund bands beside the market's own. A one-shot request is answered as the live endpoint
    answers it past the cap -- its first `CAP` rows and `has_more=True` -- and a `limit`/`offset`
    request as a page."""

    capped_from: date | None = None

    def post(self, payload: dict[str, Any]) -> dict[str, Any]:
        params = payload["params"]
        if payload["api_name"] != PRICE_LIMIT_DATASET or self.capped_from is None:
            return super().post(payload)
        day = datetime.strptime(str(params["trade_date"]), "%Y%m%d").date()
        if day < self.capped_from or day not in self.open_days:
            return super().post(payload)
        self.requests.append((PRICE_LIMIT_DATASET, dict(params)))
        fields, rows = self._session_rows(PRICE_LIMIT_DATASET, day)
        rows = [*rows, *([f"5{index:05d}.SH", compact(day), 1.1, 0.9] for index in range(CAP))]
        if "limit" not in params:
            return response(fields, rows[:CAP], has_more=True)
        offset, limit = int(params["offset"]), int(params["limit"])
        return response(fields, rows[offset : offset + limit], has_more=offset + limit < len(rows))


def test_once_stk_limit_pages_a_session_is_three_stk_limit_requests_and_the_day_twenty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """T6-T7 2.3: past the cap a session's `stk_limit` is one refused one-shot request and two
    pages, and the command's counter -- one count per POST, under its `api_name` -- shows them as
    three `stk_limit` requests a session: the ordinary survivor day becomes 20, and the paged
    answer is stored whole."""
    market = CappedBands(open_days=OPEN_2026, capped_from=date(2026, 2, 2))
    store = _seeded(tmp_path, monkeypatch, market, as_of=SEEDED, year=2026)

    _panel, counts = _update(
        store, DAY, targets=daily.targets_for_survivor(SURVIVOR_BUILDS), survivor=_survivor()
    )

    assert dict(counts) == {**STEADY_SURVIVOR, PRICE_LIMIT_DATASET: 2 * 3}
    assert sum(counts.values()) == 20
    asked = [params for name, params in market.requests if name == PRICE_LIMIT_DATASET]
    assert [(params["trade_date"], params.get("offset")) for params in asked] == [
        ("20260202", None),
        ("20260202", "0"),
        ("20260202", "4000"),
        ("20260203", None),
        ("20260203", "0"),
        ("20260203", "4000"),
    ]
    coverage = store.panel.read_coverage(PRICE_LIMIT_DATASET, 2026)
    assert coverage is not None
    assert {entry.event_date: entry.row_count for entry in coverage.dates}[DAY] == (
        len(SECURITIES) + CAP
    )


# --- the catch-up bound -------------------------------------------------------------------------


def test_the_catch_up_bound_is_ten_unless_the_registrations_adapter_block_names_another() -> None:
    assert daily.DEFAULT_MAX_PANEL_CATCH_UP_SESSIONS == 10
    assert daily.panel_catch_up_bound({}) == 10
    assert daily.panel_catch_up_bound({"adapter": {}}) == 10
    assert daily.panel_catch_up_bound({"adapter": {"max_panel_catch_up_sessions": 3}}) == 3
    assert daily.panel_catch_up_bound({"adapter": {"max_panel_catch_up_sessions": 0}}) == 0


@pytest.mark.parametrize(
    "config",
    [
        pytest.param({"adapter": {"max_panel_catch_up_sessions": -1}}, id="negative"),
        pytest.param({"adapter": {"max_panel_catch_up_sessions": True}}, id="bool"),
        pytest.param({"adapter": {"max_panel_catch_up_sessions": 2.5}}, id="float"),
        pytest.param({"adapter": {"max_panel_catch_up_sessions": "10"}}, id="text"),
        pytest.param({"adapter": {"max_panel_catch_up_sessions": None}}, id="null"),
        pytest.param({"adapter": [10]}, id="block not a mapping"),
    ],
)
def test_a_catch_up_bound_that_is_not_a_count_of_sessions_is_refused_as_a_registration(
    config: Mapping[str, Any],
) -> None:
    with pytest.raises(daily.StepFailedError) as refused:
        daily.panel_catch_up_bound(config)

    assert refused.value.step == "registration"
    assert refused.value.exit_code == daily.DailyExit.no_registration
    assert "max_panel_catch_up_sessions" in str(refused.value)


def test_the_survivor_rules_cannot_be_made_without_a_count_of_sessions() -> None:
    with pytest.raises(ValueError, match="max_catch_up_sessions"):
        daily.SurvivorPanelUpdate(max_catch_up_sessions=-1)
    with pytest.raises(ValueError, match="max_catch_up_sessions"):
        daily.SurvivorPanelUpdate(max_catch_up_sessions=True)
    with pytest.raises(TypeError):
        daily.SurvivorPanelUpdate()


AFTER_TEN: Final[date] = date(2026, 2, 17)
"""Ten sessions lie between the stored 2 February and it: 3 to 16 February."""
AFTER_ELEVEN: Final[date] = date(2026, 2, 18)


def test_a_gap_at_the_bound_is_fetched_in_one_run_at_five_requests_a_missed_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _seeded(tmp_path, monkeypatch, Market(open_days=OPEN_2026), as_of=SEEDED, year=2026)

    panel, counts = _update(
        store, AFTER_TEN, targets=daily.targets_for_survivor(SURVIVOR_BUILDS), survivor=_survivor()
    )

    missed = weekdays(date(2026, 2, 3), date(2026, 2, 16))
    assert len(missed) == 10
    assert panel["caught_up"] == [day.isoformat() for day in missed]
    assert sum(counts.values()) == 16 + 5 * 10
    assert _horizon(store, DAILY_DATASET, 2026) == AFTER_TEN


def test_a_gap_past_the_bound_is_refused_before_any_request_with_a_command_that_fills_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Eleven missed sessions under a bound of ten: refused before the first request, nothing
    written, the gap named, and the `panel build` that fetches its oldest ten printed. Run, that
    command moves every session-scoped partition to the tenth; the step then fetches the eleventh
    itself, as an ordinary day and one."""
    store = _seeded(tmp_path, monkeypatch, Market(open_days=OPEN_2026), as_of=SEEDED, year=2026)
    before = _files(store.runtime)
    targets = daily.targets_for_survivor(SURVIVOR_BUILDS)

    with pytest.raises(daily.StepFailedError) as refused:
        _update(store, AFTER_ELEVEN, targets=targets, survivor=_survivor())

    message = str(refused.value)
    assert message.startswith("step 2 (panel update) failed: ")
    assert "11 missed session(s), 2026-02-03 to 2026-02-17" in message
    assert "more than the 10 this step fetches in one run (max_panel_catch_up_sessions)" in message
    assert "nothing was fetched" in message
    assert store.market.payloads == []
    assert _files(store.runtime) == before

    command = next(
        line.strip()
        for line in message.splitlines()
        if line.strip().startswith("openalpha panel build")
    )
    arguments = shlex.split(command)
    assert arguments[arguments.index("--as-of") + 1] == (
        session_publication_instant(date(2026, 2, 16)).astimezone(UTC).isoformat()
    )
    flags = [arguments[index + 1] for index, flag in enumerate(arguments) if flag == "--dataset"]
    assert flags == [ADJ_FACTOR_DATASET, "price", PRICE_LIMIT_DATASET]
    assert "--incremental" in arguments
    built = daily.invoke(arguments[1:])
    assert built.exit_code == 0, built.reason()
    for dataset in (ADJ_FACTOR_DATASET, DAILY_DATASET, DAILY_BASIC_DATASET, PRICE_LIMIT_DATASET):
        assert _horizon(store, dataset, 2026) == date(2026, 2, 16)

    panel, counts = _update(store, AFTER_ELEVEN, targets=targets, survivor=_survivor())

    assert panel["caught_up"] == ["2026-02-17"]
    assert sum(counts.values()) == 16 + 5 * 1
    assert _horizon(store, DAILY_DATASET, 2026) == AFTER_ELEVEN


def test_a_registered_bound_is_the_one_the_step_holds_to(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _seeded(tmp_path, monkeypatch, Market(open_days=OPEN_2026), as_of=SEEDED, year=2026)
    registered = {"adapter": {"max_panel_catch_up_sessions": 1}}

    with pytest.raises(daily.StepFailedError, match="2 missed session"):
        _update(
            store,
            date(2026, 2, 5),
            targets=daily.targets_for_survivor(SURVIVOR_BUILDS),
            survivor=_survivor(registered),
        )
    assert store.market.payloads == []


def test_the_p6_update_has_no_catch_up_bound(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`survivor` left out is the P6 step, unchanged: the same eleven-session gap is fetched in
    one run, 15 + 5 x 11 requests, and its result carries no survivor key."""
    store = _seeded(tmp_path, monkeypatch, Market(open_days=OPEN_2026), as_of=SEEDED, year=2026)

    panel, counts = _update(store, AFTER_ELEVEN, targets=daily.targets_for(SURVIVOR_BUILDS))

    assert sum(counts.values()) == 15 + 5 * 11
    assert "caught_up" not in panel
    assert _horizon(store, DAILY_DATASET, 2026) == AFTER_ELEVEN


# --- across New Year ----------------------------------------------------------------------------


LAST_2026: Final[date] = date(2026, 12, 31)
FIRST_2027: Final[date] = date(2027, 1, 4)
LATE_ST: Final[str] = SECURITIES[3]
"""Renamed `*ST` on 4 January, announced on 31 December -- and published by the upstream only
after 31 December's run."""
RENAMED_2027: Final[str] = SECURITIES[5]
"""2027's first rename, announced on its first session."""
DELISTED: Final[str] = SECURITIES[-2]
"""Delisted on 2027's first session: a registry holds an event in the new year."""


class NamesAcrossNewYear(Market):
    """`Market` with the name corpus a real one has across New Year: an ST rename announced on
    31 December that the upstream serves only from 1 January on (T3 2.4(iv): half the ST names
    are announced the evening before they take effect), and 2027's first rename on 4 January."""

    def _names(self, params: Mapping[str, Any]) -> list[list[Any]]:
        year = str(params["start_date"])[:4]
        today = self.today or self.open_days[-1]
        if year == "2026":
            late = [[LATE_ST, "*ST乙", "20270104", None, "20261231", "其他风险警示"]]
            return [*super()._names(params), *(late if today >= date(2027, 1, 1) else [])]
        if year == "2027" and today >= FIRST_2027:
            return [[RENAMED_2027, "新名称", "20270104", None, "20270104", "改名"]]
        return []


def _new_year(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Store:
    market = NamesAcrossNewYear(open_days=NEW_YEAR, delisted=((DELISTED, FIRST_2027),))
    return _seeded(tmp_path, monkeypatch, market, as_of="2026-12-24T12:00:00+08:00", year=2026)


def _facts(
    store: Store, day: date, monkeypatch: pytest.MonkeyPatch, *, read: set[str] | None = None
) -> Any:
    """`build_facts_day` on `day` at its evening, over the step's store, every panel read real.

    The factor tiers are the one read replaced: they are step 4's, built at the session's 16:30
    from datasets the first test here shows the survivor day fetches, and the registered
    transform needs a cross section of 100 the scripted market does not have. The stand-in
    answers one row that carries no value -- a build exists at the instant, so the day is not
    refused for a missing build, and no security is scored -- and records what it was asked:
    the session's year, at its 16:30. The coverage floor is 0, so every other read of the day
    has to pass for the call to. Every other dataset the call reads is added to `read`."""
    asked: list[tuple[tuple[int, ...], datetime | None]] = []
    recording = ReadRecordingStore(store.runtime / "panel")

    def no_build(
        _store: PanelStore,
        _definition: Any,
        _spec: Any,
        *,
        years: Sequence[int],
        as_of: datetime,
        at: datetime | None = None,
    ) -> tuple[Any, ...]:
        asked.append((tuple(years), at))
        return (SimpleNamespace(subject="000000.SZ", value=None, coverage="not_in_universe"),)

    monkeypatch.setattr(survivor_facts, "load_processed_factor_observations", no_build)
    config = SurvivorBookConfig(seed=0, min_listing_sessions=0)
    try:
        facts = survivor_facts.build_facts_day(
            store=recording,
            session=day,
            as_of=_evening(day),
            config=config,
            factor_ids=config.composite_factors,
            transform=FACTOR_TRANSFORMS.get(TRANSFORM),
            exchange=EXCHANGE,
            min_coverage=0.0,
        )
    finally:
        if read is not None:
            read |= recording.read
    assert asked == [((day.year,), session_publication_instant(day))] * len(
        config.composite_factors
    )
    return facts


def _years_asked(store: Store, api_name: str) -> list[str]:
    return [str(params["start_date"])[:4] for params in store.market.asked(api_name)]


def test_across_new_year_the_survivor_day_serves_every_read_of_build_facts_day(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """31 December, then 2027's first session. Each day's step 2 leaves the store holding every
    partition `build_facts_day` reads for that session, before it runs: on 4 January every one of
    them has a 2027 partition this step wrote. And in January the step builds the year before's
    name corpus again, so the ST rename announced on 31 December after that day's run is read
    on the day it takes effect."""
    store = _new_year(tmp_path, monkeypatch)
    targets = daily.targets_for_survivor(SURVIVOR_BUILDS)

    december, _counts = _update(
        store, LAST_2026, targets=targets, survivor=_survivor(), next_year=True
    )
    assert december["caught_up"] == [
        "2026-12-24",
        "2026-12-25",
        "2026-12-28",
        "2026-12-29",
        "2026-12-30",
    ]
    assert _years_asked(store, NAMECHANGE_DATASET) == ["2026"]
    read_in_december: set[str] = set()
    before = {
        fact.ts_code: fact
        for fact in _facts(store, LAST_2026, monkeypatch, read=read_in_december).facts
    }

    january, counts = _update(store, FIRST_2027, targets=targets, survivor=_survivor())
    read_in_january: set[str] = set()
    after = {
        fact.ts_code: fact
        for fact in _facts(store, FIRST_2027, monkeypatch, read=read_in_january).facts
    }

    assert january["caught_up"] == []
    assert read_in_december == read_in_january == READ_BY_BUILD_FACTS_DAY
    for dataset in read_in_january:
        assert 2027 in store.panel.registered_years(dataset), dataset
    assert _years_asked(store, NAMECHANGE_DATASET) == ["2027", "2026"]
    # T6-T7's first session of a year (2.2, C1) is 11; January asks the year before's names too.
    assert sum(counts.values()) == 11 + 1
    assert before[LATE_ST].special_treatment is False
    assert after[LATE_ST].special_treatment is True
    assert DELISTED in before
    assert DELISTED not in after
    assert set(after) == set(SECURITIES) - {DELISTED}


def test_across_new_year_the_p6_day_leaves_build_facts_day_without_the_years_name_corpus(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The gap the survivor set closes, on the same market: the P6 day never fetches the name
    corpus, so 2027's first session has no 2027 partition of it and the facts read refuses."""
    store = _new_year(tmp_path, monkeypatch)
    p6 = daily.targets_for(SURVIVOR_BUILDS)
    _update(store, LAST_2026, targets=p6, next_year=True)
    _update(store, FIRST_2027, targets=p6)

    assert store.panel.registered_years(NAMECHANGE_DATASET) == (2026,)
    with pytest.raises(PanelStorageError, match="namechange") as refused:
        _facts(store, FIRST_2027, monkeypatch)
    assert "2027" in str(refused.value)
    assert "partition_missing" in str(refused.value)


# --- the year before's name corpus: built again until a build after its year end is stored ------


def _names_built_at(store: Store, year: int) -> datetime:
    """The `as_of` the stored `namechange` partition of `year` was built at."""
    coverage = store.panel.read_coverage(NAMECHANGE_DATASET, year)
    assert coverage is not None
    return coverage.as_of


def _names_stored(store: Store, year: int) -> int:
    """How many rows the stored `namechange` partition of `year` holds."""
    coverage = store.panel.read_coverage(NAMECHANGE_DATASET, year)
    assert coverage is not None
    return coverage.row_count


class SettledNames(NamesAcrossNewYear):
    """`NamesAcrossNewYear` with no rename announced after 31 December's run: built again in
    January, the year before's name corpus holds the rows already stored."""

    def _names(self, params: Mapping[str, Any]) -> list[list[Any]]:
        if str(params["start_date"])[:4] == "2026":
            return Market._names(self, params)
        return super()._names(params)


def test_the_year_befores_names_are_built_again_once_then_not_after_a_build_past_its_year_end(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """31 December's run stores 2026's name corpus as of that evening, before 2026 ended. The
    first session of 2027 builds it again, at the instant `panel build` asks a past year's names
    at (`cli._year_end_as_of`: the year's last instant). No rename was added, and the build's
    instant is stored all the same. From then on the stored 2026 build is past its year end, and
    the next session -- still January -- does not ask for it: an ordinary day."""
    market = SettledNames(open_days=NEW_YEAR, delisted=((DELISTED, FIRST_2027),))
    store = _seeded(tmp_path, monkeypatch, market, as_of="2026-12-24T12:00:00+08:00", year=2026)
    targets = daily.targets_for_survivor(SURVIVOR_BUILDS)
    _update(store, LAST_2026, targets=targets, survivor=_survivor(), next_year=True)
    assert _names_built_at(store, 2026) == _evening(LAST_2026)
    renames = _names_stored(store, 2026)

    _panel, first = _update(store, FIRST_2027, targets=targets, survivor=_survivor())

    assert _years_asked(store, NAMECHANGE_DATASET) == ["2027", "2026"]
    assert first[NAMECHANGE_DATASET] == 2
    year_end = cli._year_end_as_of(2026, _evening(FIRST_2027))
    assert year_end == datetime(2026, 12, 31, 23, 59, 59, tzinfo=daily.SHANGHAI)
    assert _names_built_at(store, 2026) == year_end
    assert _names_stored(store, 2026) == renames

    _panel, second = _update(store, date(2027, 1, 5), targets=targets, survivor=_survivor())

    assert _years_asked(store, NAMECHANGE_DATASET) == ["2027"]
    assert dict(second) == STEADY_SURVIVOR
    assert _names_built_at(store, 2026) == year_end


PAST_JANUARY: Final[tuple[date, ...]] = weekdays(
    date(2026, 12, 14), date(2027, 2, 5), closed=(date(2027, 1, 1),)
)
FIRST_FEBRUARY: Final[date] = date(2027, 2, 1)


def test_a_first_run_after_january_still_builds_the_year_befores_names_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every January run missed -- refused by the catch-up bound, filled by hand, or simply not
    run -- and the first run is on 1 February. The rule is the stored 2026 build's instant, not
    the session's month: that build is 31 December's evening, before 2026 ended, so the step
    builds 2026's names again and the `*ST` rename announced on 31 December after that run is
    read. Keyed on January, it would have been lost for good."""
    market = NamesAcrossNewYear(open_days=PAST_JANUARY, delisted=((DELISTED, FIRST_2027),))
    store = _seeded(tmp_path, monkeypatch, market, as_of="2026-12-24T12:00:00+08:00", year=2026)
    targets = daily.targets_for_survivor(SURVIVOR_BUILDS)
    _update(store, LAST_2026, targets=targets, survivor=_survivor(), next_year=True)
    assert _names_built_at(store, 2026) == _evening(LAST_2026)
    january = weekdays(FIRST_2027, date(2027, 1, 29))
    wide = _survivor({"adapter": {"max_panel_catch_up_sessions": len(january)}})

    panel, counts = _update(store, FIRST_FEBRUARY, targets=targets, survivor=wide)

    assert panel["caught_up"] == [day.isoformat() for day in january]
    assert _years_asked(store, NAMECHANGE_DATASET) == ["2027", "2026"]
    assert counts[NAMECHANGE_DATASET] == 2
    assert _names_built_at(store, 2026) == cli._year_end_as_of(2026, _evening(FIRST_FEBRUARY))
    names = load_name_histories(
        store.panel, years=(2026, 2027), as_of=_evening(FIRST_FEBRUARY), max_staleness=None
    )
    assert names[LATE_ST].name_on(FIRST_FEBRUARY) == "*ST乙"
