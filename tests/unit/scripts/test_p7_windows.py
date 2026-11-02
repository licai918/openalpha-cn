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

from openalpha_cn.domain.trading_calendar import (
    CalendarDay,
    TradingCalendar,
    build_trading_calendar,
)
from openalpha_cn.panel.catalog import ReadinessRequirement
from openalpha_cn.panel.store import ColumnSpec, PanelStore

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
    assert all(stamp[1] <= 2021 for stamp in store.partition_stamps())


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


def test_a_ready_read_inside_the_ceiling_is_served_and_recorded(three_years: Path) -> None:
    store = p7_windows.WindowCappedStore(three_years, window=p7_windows.RESEARCH)

    outcome = store.read_if_ready(requirement(2021), year=2021, columns=("subject",))

    assert store.served == ((PROBE, 2021),)
    assert outcome.readiness is not None


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
