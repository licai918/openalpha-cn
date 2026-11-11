"""`scripts/research/p7_windows.py` (`V2-P7-004`): the windows a P7 diagnostic may read, a panel
store that serves nothing past its window's last year and writes nothing, and the signal days a
window allows. Every store here is written at test time under `tmp_path`."""

from __future__ import annotations

import hashlib
import importlib
import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from types import ModuleType
from typing import Any, Final

import pytest
from panel_fixtures import AS_OF as PANEL_AS_OF
from panel_fixtures import YEAR, generate_panel, write_generated_panel

from openalpha_cn.domain.daily_prices import DAILY_DATASET
from openalpha_cn.domain.trading_calendar import (
    CalendarDay,
    TradingCalendar,
    build_trading_calendar,
)
from openalpha_cn.panel.catalog import ReadinessRequirement
from openalpha_cn.panel.store import ColumnSpec, PanelStorageError, PanelStore
from openalpha_cn.panel_ingest import daily_requirement

ROOT: Final[Path] = Path(__file__).resolve().parents[3]
RESEARCH: Final[Path] = ROOT / "scripts" / "research"


def _research_module(name: str) -> ModuleType:
    if str(RESEARCH) not in sys.path:
        sys.path.insert(0, str(RESEARCH))
    return importlib.import_module(name)


grid = _research_module("grid")
p7_windows = _research_module("p7_windows")
PROBE: Final[str] = "probe"
AS_OF: Final[datetime] = datetime(2026, 10, 9, tzinfo=UTC)


def weekday_calendar(first: date, last: date) -> TradingCalendar:
    days = [first + timedelta(days=offset) for offset in range((last - first).days + 1)]
    return build_trading_calendar(
        "SSE", [CalendarDay(calendar_date=day, is_trading=day.weekday() < 5) for day in days]
    )


@pytest.fixture
def three_years(tmp_path: Path) -> Path:
    """A store holding one probe partition in 2021, 2022 and 2024."""
    store = PanelStore(tmp_path / "panel")
    for year in (2021, 2022, 2024):
        store.write_partition(PROBE, year, (ColumnSpec("subject", "VARCHAR"),), [(str(year),)])
    return tmp_path / "panel"


def requirement(*years: int) -> ReadinessRequirement:
    return ReadinessRequirement(
        dataset=PROBE,
        as_of=AS_OF,
        years=years,
        required_dates=None,
        required_subjects=None,
        required_fields=None,
        max_staleness=None,
    )


def test_the_three_windows_are_the_protocols_stages_and_the_spent_holdout() -> None:
    stages = grid.PROTOCOL_STAGE_WINDOWS
    research, validation, second = (
        p7_windows.WINDOWS[name] for name in ("research", "validation", "second_use")
    )
    assert (research.first, research.last, research.ceiling_year) == (
        stages["discovery"].first,
        stages["discovery"].last,
        2021,
    )
    assert (validation.first, validation.last, validation.ceiling_year) == (
        stages["validation"].first,
        stages["validation"].last,
        2023,
    )
    assert (second.first, second.last, second.ceiling_year) == (
        stages["holdout"].first,
        date(2026, 9, 24),
        2026,
    )
    assert (research.last, validation.first) == (date(2021, 12, 31), date(2022, 1, 4))


def test_a_capped_store_serves_years_up_to_its_ceiling_and_records_them(three_years: Path) -> None:
    store = p7_windows.WindowCappedStore(three_years, window=p7_windows.RESEARCH)

    assert store.query(PROBE, year=2021, columns=("subject",)) == [("2021",)]
    assert store.served == ((PROBE, 2021),)
    assert store.registered_years(PROBE) == (2021,)
    assert [stamp[:2] for stamp in store.partition_stamps()] == [(PROBE, 2021)]


@pytest.mark.parametrize(
    "door",
    [
        lambda s: s.query(PROBE, year=2022, columns=("subject",)),
        lambda s: s.profile_query(PROBE, year=2022, columns=("subject",)),
        lambda s: s.read_coverage(PROBE, 2022),
        lambda s: s.partition_content_hash(PROBE, 2024),
        lambda s: s.read_visible_at(requirement(2021, 2022), year=2021, columns=("subject",)),
        lambda s: s.read_if_ready(requirement(2022), year=2022, columns=("subject",)),
        lambda s: s.assessed(requirement(2021, 2024)),
        lambda s: s.assess_readiness(requirement(2022)),
    ],
    ids=["query", "profile", "coverage", "hash", "visible", "ready", "assessed", "readiness"],
)
def test_a_capped_store_refuses_every_door_past_its_ceiling(
    three_years: Path, door: object
) -> None:
    store = p7_windows.WindowCappedStore(three_years, window=p7_windows.RESEARCH)

    with pytest.raises(p7_windows.DiagnosticWindowError, match="read_once"):
        door(store)  # type: ignore[operator]
    assert store.served == ()


def test_a_capped_store_writes_nothing(three_years: Path) -> None:
    catalog = three_years / "catalog.duckdb"
    before = hashlib.sha256(catalog.read_bytes()).hexdigest()
    store = p7_windows.WindowCappedStore(three_years, window=p7_windows.RESEARCH)

    for write in (
        lambda: store.write_partition(PROBE, 2021, (ColumnSpec("subject", "VARCHAR"),), [("x",)]),
        lambda: store.write_partitions(()),
        lambda: store.remove_partition(PROBE, 2021),
        lambda: store.merge_base(PROBE, 2021),
        lambda: store.record_coverage(object()),  # type: ignore[arg-type]
        lambda: store.discard_merge_bases(),
    ):
        with pytest.raises(p7_windows.DiagnosticWindowError, match="read-only"):
            write()
    store.query(PROBE, year=2021, columns=("subject",))
    assert hashlib.sha256(catalog.read_bytes()).hexdigest() == before


def test_a_capped_store_refuses_a_root_with_no_catalog_and_creates_nothing(tmp_path: Path) -> None:
    root = tmp_path / "nowhere" / "panel"

    with pytest.raises(p7_windows.DiagnosticWindowError, match="catalog"):
        p7_windows.WindowCappedStore(root, window=p7_windows.RESEARCH)
    assert not root.exists() and not root.parent.exists()


def test_a_research_plan_embargoes_a_label_that_would_end_in_2022() -> None:
    """Weekdays from Monday 2021-11-01: sessions 0, 20, 40 are 11-01, 11-29 and 12-27. At h=20
    the exits are sessions 21, 41 and 61; session 61 is in January 2022, past the research
    window, so 12-27 is embargoed -- on a calendar running into 2022 (the window decides) and on
    one ending 2021-12-31 (the calendar's end decides)."""
    for calendar in (
        weekday_calendar(date(2021, 11, 1), date(2022, 2, 28)),
        weekday_calendar(date(2021, 11, 1), date(2021, 12, 31)),
    ):
        plan = p7_windows.plan_signal_days(
            calendar,
            window=p7_windows.RESEARCH,
            first=date(2021, 11, 1),
            last=date(2021, 12, 31),
            stride=20,
            horizons=(20,),
        )
        assert plan.sessions == (date(2021, 11, 1), date(2021, 11, 29))
        assert plan.embargoed == (date(2021, 12, 27),)


def test_the_longest_horizon_decides_the_embargo() -> None:
    calendar = weekday_calendar(date(2021, 11, 1), date(2022, 2, 28))

    plan = p7_windows.plan_signal_days(
        calendar,
        window=p7_windows.RESEARCH,
        first=date(2021, 11, 1),
        last=date(2021, 12, 31),
        stride=10,
        horizons=(5, 20),
    )

    assert all(
        calendar.shift(calendar.next_trading_day(day), 20) <= date(2021, 12, 31)
        for day in plan.sessions
    )
    assert plan.embargoed and all(day not in plan.sessions for day in plan.embargoed)


@pytest.mark.parametrize(
    ("options", "said"),
    [
        ({"first": date(2014, 12, 31)}, "before"),
        ({"last": date(2022, 1, 4)}, "validation"),
        ({"first": date(2021, 11, 10), "last": date(2021, 11, 1)}, "before its first"),
        ({"first": date(2021, 11, 6)}, "not a session"),
        ({"stride": 0}, "stride"),
        ({"horizons": (0,)}, "horizon"),
    ],
)
def test_a_plan_outside_its_window_is_refused_by_name(
    options: dict[str, object], said: str
) -> None:
    arguments: dict[str, object] = {
        "window": p7_windows.RESEARCH,
        "first": date(2021, 11, 1),
        "last": date(2021, 12, 31),
        "stride": 20,
        "horizons": (20,),
        **options,
    }
    with pytest.raises(p7_windows.DiagnosticWindowError, match=said):
        p7_windows.plan_signal_days(
            weekday_calendar(date(2014, 12, 1), date(2022, 2, 28)), **arguments
        )  # type: ignore[arg-type]


# --- beyond the plan's list: doors the list reaches only through another door -------------------


@pytest.mark.parametrize(
    "door",
    [
        lambda s: s.read_if_ready(requirement(2021, 2022), year=2021, columns=("subject",)),
        lambda s: s.read_if_ready(requirement(2021), year=2022, columns=("subject",)),
        lambda s: s.read_visible_at(requirement(2021), year=2022, columns=("subject",)),
    ],
    ids=["ready-pooled", "ready-other-year", "visible-other-year"],
)
def test_a_read_is_refused_for_every_year_its_requirement_or_its_call_names(
    three_years: Path, door: object
) -> None:
    """The year asked for is inside the ceiling in the first case, the requirement is in the
    others; the window refuses by name before the base class can refuse for another reason."""
    store = p7_windows.WindowCappedStore(three_years, window=p7_windows.RESEARCH)

    with pytest.raises(p7_windows.DiagnosticWindowError, match="read_once"):
        door(store)  # type: ignore[operator]
    assert store.served == ()


@pytest.fixture
def generated(tmp_path: Path) -> tuple[Path, ReadinessRequirement]:
    """The generated panel's `daily` partition (ten sessions of 2026) and the requirement that
    makes it ready at the fixtures' `AS_OF`."""
    panel = generate_panel()
    write_generated_panel(PanelStore(tmp_path / "panel"), panel)
    ready = daily_requirement(
        panel.calendar(), years=(YEAR,), as_of=PANEL_AS_OF, max_staleness=None
    )
    return tmp_path / "panel", ready


def test_a_ready_read_inside_the_ceiling_hands_back_the_rows_and_records_the_partition(
    generated: tuple[Path, ReadinessRequirement],
) -> None:
    root, ready = generated
    window = p7_windows.DiagnosticWindow(
        name="fixture", first=date(2026, 1, 5), last=date(2026, 12, 31), ceiling_year=2026
    )
    store = p7_windows.WindowCappedStore(root, window=window)

    outcome = store.read_if_ready(ready, year=YEAR, columns=("subject", "close"))
    visible = store.read_visible_at(ready, year=YEAR, columns=("subject", "close"))

    assert outcome.readiness.state == "ready"
    assert outcome.rows_or_none is not None and len(outcome.rows_or_none) > 0
    assert visible.rows_or_none == outcome.rows_or_none
    assert store.served == ((DAILY_DATASET, YEAR),)


def test_the_research_window_refuses_the_same_requirement_before_it_reads_anything(
    generated: tuple[Path, ReadinessRequirement],
) -> None:
    root, ready = generated
    store = p7_windows.WindowCappedStore(root, window=p7_windows.RESEARCH)

    with pytest.raises(p7_windows.DiagnosticWindowError, match="read_once"):
        store.read_if_ready(ready, year=YEAR, columns=("subject", "close"))
    assert store.served == ()


@pytest.mark.parametrize(
    "door",
    [
        lambda s: s.read_if_ready(requirement(2021), year=2020, columns=("subject",)),
        lambda s: s.read_visible_at(requirement(2021), year=2020, columns=("subject",)),
        lambda s: s.query("not/a/dataset", year=2021, columns=("subject",)),
    ],
    ids=["ready-year-not-in-requirement", "visible-year-not-in-requirement", "malformed-dataset"],
)
def test_a_read_the_base_class_refuses_is_not_recorded_as_served(
    three_years: Path, door: object
) -> None:
    """The year asked for is inside the ceiling, so the window admits it; the base class then
    raises. Provenance must not list a partition that was never read."""
    store = p7_windows.WindowCappedStore(three_years, window=p7_windows.RESEARCH)

    with pytest.raises(PanelStorageError):
        door(store)  # type: ignore[operator]
    assert store.served == ()


STUB_ANSWER: Final[object] = object()

# Each door: a call the window must refuse, and a call it must pass on to the base class.
DOORS: Final[dict[str, tuple[Any, Any]]] = {
    "query": (
        lambda s: s.query(PROBE, year=2022, columns=("subject",)),
        lambda s: s.query(PROBE, year=2021, columns=("subject",)),
    ),
    "profile_query": (
        lambda s: s.profile_query(PROBE, year=2022, columns=("subject",)),
        lambda s: s.profile_query(PROBE, year=2021, columns=("subject",)),
    ),
    "read_coverage": (
        lambda s: s.read_coverage(PROBE, 2022),
        lambda s: s.read_coverage(PROBE, 2021),
    ),
    "partition_content_hash": (
        lambda s: s.partition_content_hash(PROBE, 2024),
        lambda s: s.partition_content_hash(PROBE, 2021),
    ),
    "assess_readiness": (
        lambda s: s.assess_readiness(requirement(2021, 2022)),
        lambda s: s.assess_readiness(requirement(2021)),
    ),
    "assessed": (
        lambda s: s.assessed(requirement(2021, 2024)),
        lambda s: s.assessed(requirement(2021)),
    ),
    "read_if_ready": (
        lambda s: s.read_if_ready(requirement(2021, 2022), year=2021, columns=("subject",)),
        lambda s: s.read_if_ready(requirement(2021), year=2021, columns=("subject",)),
    ),
    "read_visible_at": (
        lambda s: s.read_visible_at(requirement(2021, 2022), year=2021, columns=("subject",)),
        lambda s: s.read_visible_at(requirement(2021), year=2021, columns=("subject",)),
    ),
}


@pytest.fixture
def base_class_doors_stubbed(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """`PanelStore`'s own doors replaced by stubs that answer a marker and record the call, so
    what a capped door does is its own and not the base class's (several of the base class's doors
    call one another, and each refusal would otherwise be made twice)."""
    called: list[str] = []

    def stub(name: str) -> Any:
        def door(self: object, *args: object, **kwargs: object) -> object:
            called.append(name)
            return STUB_ANSWER

        return door

    for name in DOORS:
        monkeypatch.setattr(PanelStore, name, stub(name))
    return called


@pytest.mark.parametrize("name", list(DOORS))
def test_each_door_refuses_on_its_own_and_does_not_reach_the_base_class(
    three_years: Path, base_class_doors_stubbed: list[str], name: str
) -> None:
    store = p7_windows.WindowCappedStore(three_years, window=p7_windows.RESEARCH)

    with pytest.raises(p7_windows.DiagnosticWindowError, match="read_once"):
        DOORS[name][0](store)
    assert base_class_doors_stubbed == [] and store.served == ()


@pytest.mark.parametrize("name", list(DOORS))
def test_each_door_inside_the_ceiling_is_the_base_classs_answer_and_is_recorded(
    three_years: Path, base_class_doors_stubbed: list[str], name: str
) -> None:
    store = p7_windows.WindowCappedStore(three_years, window=p7_windows.RESEARCH)

    assert DOORS[name][1](store) is STUB_ANSWER
    assert base_class_doors_stubbed == [name]
    assert store.served == ((PROBE, 2021),)


def test_what_was_read_is_listed_with_its_hash_and_listing_it_is_not_a_read(
    three_years: Path,
) -> None:
    store = p7_windows.WindowCappedStore(three_years, window=p7_windows.RESEARCH)
    store.query(PROBE, year=2021, columns=("subject",))

    listed = store.partitions_read()

    assert listed == ((PROBE, 2021, PanelStore(three_years).partition_content_hash(PROBE, 2021)),)
    assert listed[0][2] is not None
    assert store.served == ((PROBE, 2021),)


def test_a_capped_store_is_opened_on_the_runtime_directorys_panel(tmp_path: Path) -> None:
    PanelStore(tmp_path / "panel").write_partition(
        PROBE, 2021, (ColumnSpec("subject", "VARCHAR"),), [("2021",)]
    )

    store = p7_windows.capped_panel_store(tmp_path, p7_windows.RESEARCH)

    assert store.root == tmp_path / "panel"
    assert store.window is p7_windows.RESEARCH
    assert store.query(PROBE, year=2021, columns=("subject",)) == [("2021",)]


def test_a_window_cannot_open_a_year_it_does_not_hold() -> None:
    with pytest.raises(p7_windows.DiagnosticWindowError, match="ceiling"):
        p7_windows.DiagnosticWindow(
            name="wide", first=date(2015, 1, 5), last=date(2021, 12, 31), ceiling_year=2022
        )
    with pytest.raises(p7_windows.DiagnosticWindowError, match="before it begins"):
        p7_windows.DiagnosticWindow(
            name="backwards", first=date(2021, 12, 31), last=date(2015, 1, 5), ceiling_year=2015
        )


def test_the_window_table_cannot_be_edited_at_run_time() -> None:
    with pytest.raises(TypeError):
        p7_windows.WINDOWS["all"] = p7_windows.RESEARCH  # type: ignore[index]
    assert set(p7_windows.WINDOWS) == {"research", "validation", "second_use"}


def test_no_plan_reaches_past_the_day_the_second_use_ended() -> None:
    calendar = weekday_calendar(date(2024, 1, 1), date(2026, 12, 31))

    with pytest.raises(p7_windows.DiagnosticWindowError, match="2026-09-24"):
        p7_windows.plan_signal_days(
            calendar,
            window=p7_windows.SECOND_USE,
            first=date(2026, 8, 3),
            last=date(2026, 9, 25),
            stride=5,
            horizons=(5,),
        )


def test_a_plan_names_what_it_embargoes_and_never_drops_it_silently() -> None:
    calendar = weekday_calendar(date(2021, 11, 1), date(2022, 2, 28))

    plan = p7_windows.plan_signal_days(
        calendar,
        window=p7_windows.RESEARCH,
        first=date(2021, 11, 1),
        last=date(2021, 12, 31),
        stride=5,
        horizons=(20,),
    )

    every_pick = calendar.trading_days_between(date(2021, 11, 1), date(2021, 12, 31))[::5]
    assert tuple(sorted(plan.sessions + plan.embargoed)) == every_pick
    assert plan.horizons == (20,) and plan.stride == 5
    assert plan.window is p7_windows.RESEARCH
    assert plan.embargoed == tuple(day for day in every_pick if day > plan.sessions[-1])


def test_a_label_that_exits_on_the_windows_last_day_is_kept_and_one_session_later_is_not() -> None:
    """Weekdays from Monday 2021-11-01: 2021-12-23 is session 38, the entry is session 39
    (12-24) and a 5-session label exits at session 44, 2021-12-31 -- the window's last day. A
    6-session label exits on 2022-01-03."""
    calendar = weekday_calendar(date(2021, 11, 1), date(2022, 2, 28))
    day = date(2021, 12, 23)

    def planned(horizon: int) -> Any:
        return p7_windows.plan_signal_days(
            calendar,
            window=p7_windows.RESEARCH,
            first=day,
            last=day,
            stride=1,
            horizons=(horizon,),
        )

    assert (planned(5).sessions, planned(5).embargoed) == ((day,), ())
    assert (planned(6).sessions, planned(6).embargoed) == ((), (day,))


def test_a_labels_exit_is_the_planners_rule_and_none_past_the_calendar() -> None:
    """`label_exit_day` is the rule `plan_signal_days` embargoes by: entered at the next close, left
    `horizon` sessions later. On a calendar that stops on 2021-12-31 (the research window's last
    day, as a reader capped at 2021 sees it), the 5-session label of 2021-12-23 exits that day and
    a 6-session one cannot be placed at all: `None`, never a guess."""
    calendar = weekday_calendar(date(2021, 11, 1), date(2021, 12, 31))
    day = date(2021, 12, 23)

    assert p7_windows.label_exit_day(calendar, day, 5) == date(2021, 12, 31)
    assert p7_windows.label_exit_day(calendar, day, 1) == date(2021, 12, 27)
    assert p7_windows.label_exit_day(calendar, day, 6) is None
    assert p7_windows.label_exit_day(calendar, date(2021, 12, 31), 1) is None


def _smoke_env() -> ModuleType:
    scripts = ROOT / "scripts"
    if str(scripts) not in sys.path:
        sys.path.insert(0, str(scripts))
    return importlib.import_module("p7_smoke_env")


@pytest.mark.parametrize("where", ["research", "checkout"])
def test_a_runtime_directory_on_the_research_root_or_the_main_checkout_is_refused(
    tmp_path: Path, where: str
) -> None:
    """A read-only connection still creates catalog.duckdb.lock/.gate beside the catalogue
    (panel/store.py:1309-1364), and a read-once measurement holds the catalogue's shared lock
    for its whole computation (store.py:2206-2222): neither root may be opened in place, and the
    refusal creates nothing beside the catalogue."""
    home = tmp_path / "home"
    research = home / "openalpha-research"
    checkout = tmp_path / "checkout"
    panel = research / "panel"
    panel.mkdir(parents=True)
    (panel / "a-sentinel").write_text("stored by the panel's last build\n", encoding="utf-8")
    (checkout / "runtime").mkdir(parents=True)
    runtime = panel if where == "research" else checkout / "runtime"

    with pytest.raises(p7_windows.RuntimeDirectoryRefusedError) as raised:
        p7_windows.checked_runtime_directory(
            runtime, research_runtime=research, main_checkout=checkout
        )

    said = str(raised.value)
    assert str(runtime.resolve()) in said
    assert ("openalpha-research" if where == "research" else "main checkout") in said
    # Non-vacuous: the catalogue's directory is the one the check must leave alone, and it holds
    # something to be left alone -- a lock or a gate file written there would fail this.
    assert sorted(entry.name for entry in panel.iterdir()) == ["a-sentinel"]
    assert not (runtime / p7_windows.CATALOG_FILE).exists()


def test_a_clone_under_the_home_directory_is_not_refused(tmp_path: Path) -> None:
    """Task 7 and the real read both run on a cp -c clone; a clone is neither P6's root nor a
    checkout, and a name that merely starts with the research root's is another directory."""
    home = tmp_path / "home"
    research = home / "openalpha-research"
    checkout = tmp_path / "checkout"
    (research / "panel").mkdir(parents=True)
    (checkout / "runtime").mkdir(parents=True)
    clone = home / "p7-panel-clone"
    (clone / "panel").mkdir(parents=True)

    assert (
        p7_windows.checked_runtime_directory(
            clone / "panel", research_runtime=research, main_checkout=checkout
        )
        == (clone / "panel").resolve()
    )
    assert (
        p7_windows.checked_runtime_directory(
            home / "openalpha-research-clone", research_runtime=research, main_checkout=checkout
        )
        == (home / "openalpha-research-clone").resolve()
    )


def test_a_runtime_directory_spelled_in_another_case_on_a_case_blind_volume_is_refused(
    tmp_path: Path,
) -> None:
    """APFS ignores case by default and `resolve()` keeps the case it is given: a path spelled
    `OpenAlpha-Research` is inside `openalpha-research` (the smoke's rule, copied)."""
    research = tmp_path / "home" / "openalpha-research"
    (research / "panel").mkdir(parents=True)
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    spelled = tmp_path / "home" / "OpenAlpha-Research"
    if not spelled.exists():
        pytest.skip("this volume tells case apart: another case names another directory")

    with pytest.raises(p7_windows.RuntimeDirectoryRefusedError, match="openalpha-research"):
        p7_windows.checked_runtime_directory(
            spelled / "panel", research_runtime=research, main_checkout=checkout
        )


def test_the_two_refusals_are_the_smokes_path_policy(tmp_path: Path) -> None:
    """The rules are the smoke's, not a second copy of them: the same paths are accepted and
    refused by both, over the spellings that can drift -- the roots themselves, their
    subdirectories, a name that merely starts with the research root's, `..` through it, and
    another case of it on a case-blind volume. The smoke's own whitespace and temporary-directory
    rules are left out: they are stricter, not different, and these paths break neither."""
    env = _smoke_env()
    home = tmp_path / "home"
    research = home / "openalpha-research"
    checkout = tmp_path / "checkout"
    (research / "panel").mkdir(parents=True)
    (checkout / "runtime").mkdir(parents=True)
    clone = home / "clone"
    clone.mkdir()
    paths = [
        research,
        research / "panel",
        research / "panel" / ".." / "panel",
        checkout,
        checkout / "runtime",
        clone,
        home / "openalpha-research-clone",
        home / "openalpha-research-clone" / "panel",
        research / ".." / "openalpha-research-clone",
    ]
    spelled = home / "OpenAlpha-Research" / "panel"
    # The spelling exists only on a case-blind volume; resolve() keeps the case it is given.
    if spelled.exists():
        paths.append(spelled)
    policy = env.PathPolicy(
        temporary_roots=(),
        main_checkout=checkout.resolve(),
        research_runtime=research.resolve(),
    )

    for where in paths:
        try:
            policy.checked("runtime directory", where)
            smoke_refused = False
        except env.SmokeEnvironmentError:
            smoke_refused = True
        try:
            p7_windows.checked_runtime_directory(
                where, research_runtime=research, main_checkout=checkout
            )
            mine_refused = False
        except p7_windows.RuntimeDirectoryRefusedError:
            mine_refused = True
        assert mine_refused == smoke_refused, where


def test_a_runtime_directory_spelled_through_dotdot_on_the_research_root_is_refused(
    tmp_path: Path,
) -> None:
    """`..` is a spelling, not an exception: the runtime directory is resolved before the rule."""
    research = tmp_path / "home" / "openalpha-research"
    (research / "panel").mkdir(parents=True)
    checkout = tmp_path / "checkout"
    checkout.mkdir()

    with pytest.raises(p7_windows.RuntimeDirectoryRefusedError, match="openalpha-research"):
        p7_windows.checked_runtime_directory(
            research / "panel" / ".." / "panel",
            research_runtime=research,
            main_checkout=checkout,
        )


def test_a_research_root_spelled_through_dotdot_is_refused_even_before_it_exists(
    tmp_path: Path,
) -> None:
    """A fresh machine's `~/openalpha-research` may not exist yet, so the identity branch has
    nothing to compare. A root spelled `home/sub/../openalpha-research` and a runtime directory
    under it must still be refused. Removing **both** `resolve()` calls together (the caller's
    and `_within`'s) lets this path evade the rule; either one alone is enough to catch it."""
    home = tmp_path / "home"
    (home / "sub").mkdir(parents=True)
    research = home / "openalpha-research"  # deliberately absent
    checkout = tmp_path / "checkout"
    spelled_root = home / "sub" / ".." / "openalpha-research"

    with pytest.raises(p7_windows.RuntimeDirectoryRefusedError, match="openalpha-research"):
        p7_windows.checked_runtime_directory(
            research / "panel", research_runtime=spelled_root, main_checkout=checkout
        )
