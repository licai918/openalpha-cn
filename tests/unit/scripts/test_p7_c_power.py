"""`scripts/p7_c_power.py`: plan C's event-level power calculation (plan Task 3.1, ruling D1).

The calculation is planning arithmetic over a **stored panel**: per stored session it rebuilds
that session's survivors (`build_facts_day` then `screen`) and counts the survivors that carry a
limit event (`limit_events_on`). From those counts it projects how many forward sessions of the
registered window would be measurable for two scoring domains -- the primary random sample of
N=300 and the full survivor census -- and gives each row a `testable` / `not_testable` verdict
with the reason it is not testable.

Fix round 1 (`review-batch3-1.md` F4 and F9) changed the model the tests below hold:

* the session sd is two-component -- `sqrt(sd_between**2 + 1/(n-1))` capped at 1, where
  `sd_between` is the non-sampling part of a session's IC sd and `n` the post-abstention scored
  names -- and the row reports the **maximum** over measurable sessions, so a printed sd is never
  greater than 1;
* the projection multiplies the expected event count by the planning abstention rate (F9), so a
  parameter standing in for observed model abstention lowers the scored names;
* the verdict gates on the previous G-C h1 bar (0.02) as a **planning detectability threshold**
  and needs at least 20 observed sessions before it projects anything.

Every reader below is scripted: the panel store is a placeholder, the calendar is a tuple of
sessions, and the builders are test doubles that record how they were called. Nothing here
touches a real panel, a network or a credential, and `main` without `--json` writes nothing.
"""

from __future__ import annotations

import ast
import hashlib
import importlib
import json
import math
import sys
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Final

import pytest
import research_repo

from openalpha_cn.domain.survivor_book import SurvivorBookConfig
from openalpha_cn.panel_factors import FACTOR_TRANSFORMS
from openalpha_cn.panel_ingest import session_publication_instant

ROOT: Final[Path] = Path(__file__).resolve().parents[3]
SOURCE: Final[Path] = ROOT / "scripts" / "p7_c_power.py"


def _script(name: str) -> Any:
    for directory in (ROOT / "scripts", ROOT / "scripts" / "research"):
        if str(directory) not in sys.path:
            sys.path.insert(0, str(directory))
    return importlib.import_module(name)


power = _script("p7_c_power")

BOOK: Final[SurvivorBookConfig] = SurvivorBookConfig(seed=0)
ADAPTER = SimpleNamespace(
    exchange="SSE",
    transform="cross_section_standard/v1",
    min_coverage=0.5,
    max_carry_sessions=250,
)
PANEL = object()
START: Final[date] = date(2020, 1, 6)


def _registration() -> Any:
    """Step 0's registration as the command's own seam hands it over."""
    return power.StepZeroRegistration(
        path=Path("docs/research/p7-registration.json"),
        config_id="caf-0000000000000001",
        registration_sha256="a" * 64,
        code_commit="b" * 40,
        survivor=SimpleNamespace(book=BOOK, adapter=ADAPTER),
    )


def _days(count: int) -> tuple[date, ...]:
    return tuple(START + timedelta(days=index) for index in range(count))


def _series(count: int, survivors: int, events: int) -> tuple[Any, ...]:
    """`count` sessions, each with the same survivor and event counts."""
    return tuple(power.SessionCount(day, survivors, events) for day in _days(count))


def _counts(*specs: tuple[date, int, int]) -> tuple[Any, ...]:
    return tuple(
        power.SessionCount(session, survivors, events) for session, survivors, events in specs
    )


class _Recording:
    """The scripted readers: every call is recorded, every answer is the test's."""

    def __init__(self, *, trading_days: tuple[date, ...], survivors: tuple[str, ...]) -> None:
        self.trading_days = trading_days
        self.survivors = survivors
        self.calendar: list[dict[str, Any]] = []
        self.builds: list[dict[str, Any]] = []
        self.days: list[Any] = []
        self.screens: list[dict[str, Any]] = []
        self.events: list[dict[str, Any]] = []
        self.events_by_session: dict[date, tuple[str, ...]] = {}

    def load_calendar(self, store: Any, *, exchange: str, years: Any, as_of: Any) -> Any:
        self.calendar.append(
            {"store": store, "exchange": exchange, "years": tuple(years), "as_of": as_of}
        )
        return SimpleNamespace(trading_days=self.trading_days)

    def build(self, **kwargs: Any) -> Any:
        self.builds.append(kwargs)
        day = SimpleNamespace(facts=())
        self.days.append(day)
        return day

    def screen(self, facts: Any, config: Any) -> Any:
        self.screens.append({"facts": facts, "config": config})
        return SimpleNamespace(
            survivors=tuple(SimpleNamespace(ts_code=code) for code in self.survivors)
        )

    def limit_events(self, store: Any, *, session: date, as_of: Any, exchange: str) -> Any:
        self.events.append(
            {"store": store, "session": session, "as_of": as_of, "exchange": exchange}
        )
        return tuple(
            SimpleNamespace(ts_code=code) for code in self.events_by_session.get(session, ())
        )


def _patched(
    monkeypatch: pytest.MonkeyPatch, *, trading_days: tuple[date, ...], survivors: tuple[str, ...]
) -> _Recording:
    readers = _Recording(trading_days=trading_days, survivors=survivors)
    monkeypatch.setattr(power, "load_trading_calendar", readers.load_calendar)
    monkeypatch.setattr(power, "build_facts_day", readers.build)
    monkeypatch.setattr(power, "screen", readers.screen)
    monkeypatch.setattr(power, "limit_events_on", readers.limit_events)
    return readers


def _tree(root: Path) -> list[str]:
    return sorted(path.relative_to(root).as_posix() for path in root.rglob("*"))


ADAPTER_BODY: Final[dict[str, Any]] = {
    "schema_version": "survivor-adapter/v1",
    "epoch_session": "2020-01-02",
    "exchange": "SSE",
    "transform": "cross_section_standard/v1",
    "min_coverage": 0.5,
    "max_carry_sessions": 250,
    "max_panel_catch_up_sessions": 10,
    "benchmark": "000905.SH",
    "prediction_horizon": "20d",
    "cost_policy": "protocol/v1",
    "paper_limits": {
        "max_position_weight": "1",
        "max_total_exposure": "1",
        "min_cash_weight": "0",
    },
    "unfilled_buy": "reject_whole_and_carry/v1",
}


def _committed_registration(repo: Path) -> Path:
    """A throwaway git repository holding one committed Step 0 survivor registration."""
    repo.mkdir(parents=True, exist_ok=True)
    research_repo.git(repo, "init", "-q", "--template=", "-b", "main")
    book = SurvivorBookConfig(seed=0)
    body = {
        "schema": "openalpha-research-registration/v1",
        "config": {
            "kind": "survivor_book/v1",
            "survivor_book": book.model_dump(mode="json", exclude_computed_fields=True),
            "adapter": ADAPTER_BODY,
        },
        "config_id": "caf-0000000000000001",
        "criteria": {"note": "not evidence"},
        "code_commit": "a" * 40,
        "settings": {"random_seed": 0},
    }
    path = repo / "docs" / "research" / "p7-registration.json"
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(body, sort_keys=True, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    research_repo.git(repo, "add", "-A")
    research_repo.git(repo, "commit", "-q", "-m", "Step 0's registration")
    return path


# --- the constants the protocol and the registration writer read --------------------------------


def test_the_public_constants_are_the_registered_designs() -> None:
    assert power.POWER_REPORT_SCHEMA == "openalpha-p7-c-power/v1"
    assert power.PRIMARY_SAMPLE_SIZE == 300
    assert power.WINDOW_SESSIONS == 120
    assert power.PRIMARY_HORIZON == 1
    assert power.MIN_SCORED_NAMES == 10
    assert power.MIN_MEASURABLE_SESSIONS == 60
    assert power.MIN_OBSERVED_SESSIONS == 20
    assert power.PLANNING_SD_BETWEEN == 0.15
    assert power.PLANNING_ABSTENTION_RATE == 0.10
    assert power.DETECTABLE_MEAN_THRESHOLD == 0.02
    assert power.TARGET_POWER == 0.8
    assert power.LEVEL == 0.05
    assert power.VERDICT_TESTABLE == "testable"
    assert power.VERDICT_NOT_TESTABLE == "not_testable"
    assert power.REASON_TOO_FEW_OBSERVED_SESSIONS == "too_few_observed_sessions"
    assert power.REASON_BELOW_MEASURABLE_FLOOR == "below_measurable_floor"
    assert power.REASON_DETECTABLE_ABOVE_THRESHOLD == "detectable_above_threshold"


# --- the counts: one stored session, its survivors, its survivors with an event -----------------


def test_the_counts_join_that_sessions_survivors_with_its_limit_events(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first, last = date(2020, 1, 2), date(2020, 1, 3)
    readers = _patched(
        monkeypatch,
        trading_days=(first, last),
        survivors=("000001.SH", "000002.SH"),
    )
    # 600519.SH carries an event but is not a survivor; 000001.SH is both.
    readers.events_by_session = {
        first: ("000001.SH", "600519.SH"),
        last: ("000002.SH",),
    }

    counts = power.historical_event_counts(
        PANEL, first=first, last=last, book=BOOK, adapter=ADAPTER
    )

    assert counts == (
        power.SessionCount(first, survivors=2, event_names=1),
        power.SessionCount(last, survivors=2, event_names=1),
    )
    assert [call["session"] for call in readers.builds] == [first, last]
    assert [call["session"] for call in readers.events] == [first, last]

    # The facts day is rebuilt the way the daily survivor day builds it: at the session's own
    # 16:30, with the registered adapter's settings and nothing held.
    build = readers.builds[0]
    assert build["as_of"] == session_publication_instant(first)
    assert build["config"] is BOOK
    assert build["factor_ids"] == BOOK.composite_factors
    assert build["transform"] is FACTOR_TRANSFORMS.get("cross_section_standard/v1")
    assert build["exchange"] == "SSE"
    assert build["min_coverage"] == 0.5
    assert build["max_carry_sessions"] == 250
    assert build["held"] == ()

    # The events are read at the same instant, on the same calendar.
    event_read = readers.events[0]
    assert event_read["as_of"] == session_publication_instant(first)
    assert event_read["exchange"] == "SSE"
    # The screen sees the facts day the builder returned, against the registration's book.
    assert readers.screens[0]["facts"] is readers.days[0].facts
    assert readers.screens[0]["config"] is BOOK


# --- the calendar iteration ---------------------------------------------------------------------


def test_the_sessions_are_the_stored_calendar_between_first_and_last(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    days = (
        date(2020, 1, 2),
        date(2020, 1, 3),
        date(2020, 1, 6),
        date(2020, 1, 7),
        date(2020, 1, 8),
    )
    readers = _patched(monkeypatch, trading_days=days, survivors=("000001.SH",))

    counts = power.historical_event_counts(
        PANEL, first=date(2020, 1, 3), last=date(2020, 1, 7), book=BOOK, adapter=ADAPTER
    )

    assert [count.session for count in counts] == [
        date(2020, 1, 3),
        date(2020, 1, 6),
        date(2020, 1, 7),
    ]
    assert len(readers.calendar) == 1
    calendar = readers.calendar[0]
    assert calendar["exchange"] == "SSE"
    assert calendar["years"] == (2020,)
    assert calendar["as_of"] == session_publication_instant(date(2020, 1, 7))

    with pytest.raises(power.PowerRequestError, match="2020-01-04"):
        power.historical_event_counts(
            PANEL, first=date(2020, 1, 4), last=date(2020, 1, 5), book=BOOK, adapter=ADAPTER
        )


# --- the F4 sd model and the F9 abstention parameter -------------------------------------------


def test_the_row_sd_is_bounded_by_one_at_the_thousand_survivor_small_cross_section() -> None:
    """Review F4's own case: 1000 survivors, 10 scored names -- sd <= 1 and not testable."""
    row = power.event_power(
        _series(power.MIN_OBSERVED_SESSIONS, 1000, 10), sample=None, abstention_rate=0.0
    )

    assert row.observed_measurable_sessions == 20
    assert row.sd == pytest.approx(math.sqrt(power.PLANNING_SD_BETWEEN**2 + 1 / 9))
    assert row.sd is not None and row.sd <= 1.0
    assert row.detectable_mean is not None and 0 < row.detectable_mean < 1
    assert row.detectable_mean > power.DETECTABLE_MEAN_THRESHOLD
    assert row.verdict == "not_testable"
    assert row.reason == "detectable_above_threshold"
    assert row.detectable_threshold == power.DETECTABLE_MEAN_THRESHOLD

    # The cap itself: at the largest admissible non-sampling sd the sampling part pushes the
    # two-component value past 1, and the row must still report 1 at most.
    capped = power.event_power(
        _series(power.MIN_OBSERVED_SESSIONS, 1000, 10),
        sample=None,
        sd_between=1.0,
        abstention_rate=0.0,
    )
    assert capped.sd == 1.0  # sqrt(1 + 1/9) is 1.054 without the cap
    assert capped.detectable_mean is not None and capped.detectable_mean <= 1.0


def test_the_verdict_flips_at_the_detectable_mean_threshold() -> None:
    """Review F4: the verdict depends on power, and 0.02 is the planning threshold."""
    above = power.event_power(
        _series(20, 1000, 190), sample=None, sd_between=0.05, abstention_rate=0.0
    )
    below = power.event_power(
        _series(20, 1000, 191), sample=None, sd_between=0.05, abstention_rate=0.0
    )

    assert above.measurable_sessions == below.measurable_sessions == 120
    assert above.detectable_mean is not None and below.detectable_mean is not None
    assert above.detectable_mean > power.DETECTABLE_MEAN_THRESHOLD >= below.detectable_mean
    assert above.verdict == "not_testable"
    assert above.reason == "detectable_above_threshold"
    assert below.verdict == "testable"
    assert below.reason is None
    assert above.detectable_threshold == below.detectable_threshold == 0.02


def test_a_detectable_mean_above_one_is_never_printed() -> None:
    counts = _series(power.MIN_OBSERVED_SESSIONS, 1000, 1000)
    report = power.power_report(
        counts,
        first=START,
        last=_days(power.MIN_OBSERVED_SESSIONS)[-1],
        sessions=2,
        sd_between=1.0,
        abstention_rate=0.0,
    )

    row = power.event_power(counts, sample=None, sessions=2, sd_between=1.0, abstention_rate=0.0)

    assert row.periods == 2
    assert row.detectable_mean == 1.0  # 1.758 would be printed without the cap
    text = power.render_report(report)
    assert "detectable mean IC 1.0000" in text
    assert "detectable mean IC 1.7" not in text


def test_fewer_than_twenty_observed_sessions_is_not_testable() -> None:
    row = power.event_power(_series(19, 1000, 400), sample=None, abstention_rate=0.0)

    assert row.observed_sessions == 19
    assert row.observed_measurable_sessions == 19
    assert row.measurable_sessions == 0
    assert row.periods == 0
    assert row.sd is None
    assert row.detectable_mean is None
    assert row.verdict == "not_testable"
    assert row.reason == "too_few_observed_sessions"

    at_the_floor = power.event_power(_series(20, 1000, 400), sample=None, abstention_rate=0.0)
    assert at_the_floor.observed_sessions == 20
    assert at_the_floor.measurable_sessions == 120
    assert at_the_floor.reason == "detectable_above_threshold"


def test_a_session_with_fewer_than_ten_scored_names_is_not_measurable() -> None:
    below = power.event_power(_series(20, 1000, 9), sample=None, abstention_rate=0.0)
    assert below.observed_sessions == 20
    assert below.observed_measurable_sessions == 0
    assert below.measurable_sessions == 0
    assert below.detectable_mean is None
    assert below.verdict == "not_testable"
    assert below.reason == "below_measurable_floor"

    at_the_floor = power.event_power(_series(20, 1000, 10), sample=None, abstention_rate=0.0)
    assert at_the_floor.observed_measurable_sessions == 20
    assert at_the_floor.measurable_sessions == 120
    assert at_the_floor.reason == "detectable_above_threshold"


def test_model_abstention_reduces_the_scored_names_and_can_make_a_session_unmeasurable() -> None:
    counts = _series(power.MIN_OBSERVED_SESSIONS, 1000, 100)

    without = power.event_power(counts, sample=None, abstention_rate=0.0)
    with_ten_percent = power.event_power(counts, sample=None, abstention_rate=0.10)
    with_thirty_percent = power.event_power(counts, sample=None, abstention_rate=0.30)

    assert without.projected_scored_names == 100
    assert with_ten_percent.projected_scored_names == 90
    assert with_thirty_percent.projected_scored_names == 70
    assert without.sd is not None and with_thirty_percent.sd is not None
    assert with_thirty_percent.sd > without.sd  # fewer scored names means a noisier session

    crossing = _series(power.MIN_OBSERVED_SESSIONS, 1000, 11)
    assert (
        power.event_power(crossing, sample=None, abstention_rate=0.0).observed_measurable_sessions
        == 20
    )
    assert (
        power.event_power(crossing, sample=None, abstention_rate=0.30).observed_measurable_sessions
        == 0
    )


def test_the_projection_is_the_expected_post_abstention_scored_count() -> None:
    counts = _series(power.MIN_OBSERVED_SESSIONS, 2000, 400)

    row300 = power.event_power(counts, sample=300, abstention_rate=0.10)
    census = power.event_power(counts, sample=None, abstention_rate=0.10)

    assert row300.projected_scored_names == round(400 * 300 / 2000 * (1 - 0.10)) == 54
    assert census.projected_scored_names == round(400 * (1 - 0.10)) == 360


# --- the power arithmetic, projected from the observed sessions ---------------------------------


def test_a_larger_measured_cross_section_can_detect_a_smaller_mean() -> None:
    counts = _series(power.MIN_OBSERVED_SESSIONS, 2000, 400)

    report = power.power_report(counts, first=_days(20)[0], last=_days(20)[-1])
    n300, census = report["rows"]["n300"], report["rows"]["census"]

    assert n300["sd"] > census["sd"]
    assert n300["detectable_mean"] > census["detectable_mean"]
    assert n300["verdict"] == census["verdict"] == "not_testable"
    assert n300["reason"] == census["reason"] == "detectable_above_threshold"

    narrow = power.event_power(counts, sample=100)
    wide = power.event_power(counts, sample=None)
    assert narrow.detectable_mean is not None and wide.detectable_mean is not None
    assert narrow.sd is not None and wide.sd is not None
    assert narrow.sd > wide.sd
    assert narrow.detectable_mean > wide.detectable_mean


def test_the_verdict_is_not_testable_below_sixty_projected_measurable_sessions() -> None:
    below = power.event_power(
        _counts(
            *(
                (day, 1000, 1000 if index < 9 else 3)
                for index, day in enumerate(_days(power.MIN_OBSERVED_SESSIONS))
            )
        ),
        sample=None,
        abstention_rate=0.0,
    )
    assert below.observed_sessions == 20
    assert below.observed_measurable_sessions == 9
    assert below.measurable_sessions == round(9 / 20 * power.WINDOW_SESSIONS) == 54
    assert below.reason == "below_measurable_floor"

    at_the_floor = power.event_power(
        _counts(
            *(
                (day, 1000, 1000 if index < 10 else 3)
                for index, day in enumerate(_days(power.MIN_OBSERVED_SESSIONS))
            )
        ),
        sample=None,
        sd_between=0.05,
        abstention_rate=0.0,
    )
    assert at_the_floor.measurable_sessions == 60
    assert at_the_floor.verdict == "testable"
    assert at_the_floor.reason is None


def test_a_horizon_longer_than_one_uses_non_overlapping_periods() -> None:
    counts = _series(power.MIN_OBSERVED_SESSIONS, 1000, 1000)

    row = power.event_power(counts, sample=None, horizon=5, abstention_rate=0.0)

    assert row.measurable_sessions == 120
    assert row.periods == 24


def test_the_primary_sample_can_be_unmeasurable_while_the_census_is_measurable() -> None:
    counts = _series(power.MIN_OBSERVED_SESSIONS, 2000, 40)

    report = power.power_report(counts, first=_days(20)[0], last=_days(20)[-1])

    assert report["rows"]["n300"]["reason"] == "below_measurable_floor"
    assert report["rows"]["census"]["reason"] == "detectable_above_threshold"


def test_event_power_refuses_a_design_it_cannot_answer() -> None:
    counts = (power.SessionCount(START, survivors=100, event_names=100),)
    for wrong in (
        {"sessions": 0},
        {"horizon": 0},
        {"sd_between": 0.0},
        {"sd_between": float("nan")},
        {"sd_between": 1.5},
        {"abstention_rate": -0.1},
        {"abstention_rate": 1.0},
        {"abstention_rate": float("nan")},
        {"level": 1.0},
        {"power": 1.0},
        {"sample": 0},
    ):
        with pytest.raises(power.PowerRequestError):
            power.event_power(counts, **{"sample": None, **wrong})
    with pytest.raises(power.PowerRequestError):
        power.event_power((), sample=None)
    with pytest.raises(power.PowerRequestError):
        power.event_power((power.SessionCount(START, survivors=3, event_names=4),), sample=None)
    with pytest.raises(power.PowerRequestError):
        power.SessionCount(START, survivors=-1, event_names=0)


# --- the report the registration writer reads ----------------------------------------------------


ROW_KEYS: Final[frozenset[str]] = frozenset(
    {
        "sample",
        "sample_size",
        "projected_scored_names",
        "observed_sessions",
        "observed_measurable_sessions",
        "measurable_sessions",
        "periods",
        "sd",
        "detectable_mean",
        "detectable_threshold",
        "verdict",
        "reason",
    }
)
PLANNING_KEYS: Final[frozenset[str]] = frozenset(
    {
        "window_sessions",
        "primary_horizon",
        "sd_between",
        "sd_between_note",
        "abstention_rate",
        "abstention_note",
        "min_observed_sessions",
        "detectable_mean_threshold",
        "threshold_note",
        "expected_count_note",
        "test_level",
        "target_power",
        "primary_sample_size",
        "min_scored_names",
        "min_measurable_sessions",
        "verdict_note",
    }
)


def test_the_report_is_the_writer_contract_and_round_trips(tmp_path: Path) -> None:
    counts = _series(power.MIN_OBSERVED_SESSIONS, 1000, 1000)
    first, last = _days(power.MIN_OBSERVED_SESSIONS)[0], _days(power.MIN_OBSERVED_SESSIONS)[-1]

    report = power.power_report(
        counts,
        first=first,
        last=last,
        sd_between=0.05,
        abstention_rate=0.0,
        source={"step0_registration": "docs/research/p7-registration.json"},
    )

    assert report["schema"] == power.POWER_REPORT_SCHEMA
    assert report["first"] == first.isoformat()
    assert report["last"] == last.isoformat()
    assert report["sessions_observed"] == 20
    planning = report["planning"]
    assert set(planning) == PLANNING_KEYS
    assert planning["window_sessions"] == 120
    assert planning["primary_horizon"] == 1
    assert planning["sd_between"] == 0.05
    assert planning["abstention_rate"] == 0.0
    assert planning["min_observed_sessions"] == 20
    assert planning["detectable_mean_threshold"] == 0.02
    assert planning["test_level"] == 0.05
    assert planning["target_power"] == 0.8
    assert planning["primary_sample_size"] == 300
    assert planning["min_scored_names"] == 10
    assert planning["min_measurable_sessions"] == 60
    for note in (
        "sd_between_note",
        "abstention_note",
        "threshold_note",
        "expected_count_note",
        "verdict_note",
    ):
        assert isinstance(planning[note], str) and planning[note]
    assert "expected count" in planning["expected_count_note"]
    assert "hypergeometric" in planning["expected_count_note"]
    assert "abstention" in planning["expected_count_note"]
    assert report["source"] == {"step0_registration": "docs/research/p7-registration.json"}

    rows = report["rows"]
    assert set(rows) == {"n300", "census"}
    for label, size in (("n300", 300), ("census", None)):
        row = rows[label]
        assert set(row) == ROW_KEYS
        assert row["sample"] == label
        assert row["sample_size"] == size
        assert row["observed_sessions"] == 20
        assert row["observed_measurable_sessions"] == 20
        assert row["measurable_sessions"] == 120
        assert row["periods"] == 120
        assert row["sd"] is not None and 0 < row["sd"] <= 1.0
        assert row["detectable_mean"] is not None and row["detectable_mean"] <= 1.0
        assert row["detectable_threshold"] == 0.02
        assert row["verdict"] == "testable"
        assert row["reason"] is None

    path = tmp_path / "p7-c-power.json"
    assert power.write_power_report(path, report) == path
    assert json.loads(path.read_text(encoding="utf-8")) == json.loads(json.dumps(report))
    assert sorted(item.name for item in tmp_path.iterdir()) == ["p7-c-power.json"]


def test_the_report_carries_no_label_or_return_and_prints_the_notes() -> None:
    counts = _series(power.MIN_OBSERVED_SESSIONS, 1000, 1000)
    report = power.power_report(
        counts,
        first=_days(20)[0],
        last=_days(20)[-1],
        sd_between=0.05,
        abstention_rate=0.0,
    )

    forbidden = {"label", "labels", "return", "returns", "outcome", "ic", "rank_ic", "realized"}
    keys: set[str] = set()

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                keys.add(str(key).lower())
                walk(item)
        elif isinstance(value, list | tuple):
            for item in value:
                walk(item)

    walk(report)
    assert keys.isdisjoint(forbidden)
    assert not any("label" in key or "return" in key for key in keys)

    text = power.render_report(report)
    assert "no labels and no returns were read" in text
    assert "not evidence" in text
    assert "候选" in text
    assert "expected count" in text
    assert "abstention" in text
    assert "Tushare" not in text and "DeepSeek" not in text


# --- the command line ----------------------------------------------------------------------------


def _main_arguments(panel: Path, tmp_path: Path, *extra: str) -> list[str]:
    return [
        "--panel-dir",
        str(panel),
        "--first",
        "2020-01-02",
        "--last",
        "2020-01-21",
        "--repo",
        str(tmp_path),
        *extra,
    ]


def test_main_without_json_prints_both_rows_and_writes_nothing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    panel = tmp_path / "panel"
    panel.mkdir()
    counts = _series(power.MIN_OBSERVED_SESSIONS, 2000, 400)
    monkeypatch.setattr(power, "_load_step_zero_registration", lambda path, repo: _registration())
    monkeypatch.setattr(power, "_open_panel", lambda path: PANEL)
    monkeypatch.setattr(power, "historical_event_counts", lambda store, **kwargs: counts)

    def refuse(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("main wrote a report without --json")

    monkeypatch.setattr(power, "write_power_report", refuse)
    before = _tree(tmp_path)

    code = power.main(_main_arguments(panel, tmp_path, "--sd-between", "0.05"))
    captured = capsys.readouterr()

    assert code == 0
    assert captured.err == ""
    assert "n300" in captured.out
    assert "census" in captured.out
    assert "N=300" in captured.out
    assert "observed 20 session(s)" in captured.out
    assert "sd " in captured.out
    assert "detectable mean IC" in captured.out
    assert "threshold 0.0200" in captured.out
    assert "verdict testable" in captured.out
    assert "verdict not testable" in captured.out
    assert "detectable above threshold" in captured.out
    assert "expected count" in captured.out
    assert "abstention" in captured.out
    assert "候选" in captured.out
    assert "not evidence" in captured.out
    assert _tree(tmp_path) == before


def test_main_with_json_writes_the_report_it_prints(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    panel = tmp_path / "panel"
    panel.mkdir()
    counts = _series(power.MIN_OBSERVED_SESSIONS, 2000, 400)
    monkeypatch.setattr(power, "_load_step_zero_registration", lambda path, repo: _registration())
    monkeypatch.setattr(power, "_open_panel", lambda path: PANEL)
    monkeypatch.setattr(power, "historical_event_counts", lambda store, **kwargs: counts)
    report_path = tmp_path / "power.json"

    code = power.main(
        _main_arguments(
            panel,
            tmp_path,
            "--sd-between",
            "0.05",
            "--abstention-rate",
            "0.10",
            "--json",
            str(report_path),
        )
    )
    captured = capsys.readouterr()

    assert code == 0
    assert f"written: {report_path}" in captured.out
    document = json.loads(report_path.read_text(encoding="utf-8"))
    assert document["schema"] == power.POWER_REPORT_SCHEMA
    assert set(document["rows"]) == {"n300", "census"}
    assert document["planning"]["sd_between"] == 0.05
    assert document["planning"]["abstention_rate"] == 0.10
    for label in ("n300", "census"):
        row = document["rows"][label]
        assert set(row) == ROW_KEYS
        assert row["observed_sessions"] == 20
        assert row["sd"] is not None
        assert row["detectable_threshold"] == 0.02
        assert row["verdict"] in {"testable", "not_testable"}


def test_the_old_between_day_sd_flag_is_gone(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)

    code = power.main(
        [
            "--panel-dir",
            str(tmp_path / "panel"),
            "--first",
            "2020-01-02",
            "--last",
            "2020-01-21",
            "--between-day-sd",
            "0.10",
        ]
    )
    captured = capsys.readouterr()

    assert code == 2
    assert "unrecognized arguments" in captured.err
    assert "--between-day-sd" in captured.err


def test_a_committed_step0_registration_is_read_and_named_in_the_report(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = tmp_path / "repo"
    registration = _committed_registration(repo)
    panel = tmp_path / "panel"
    panel.mkdir()
    counts = _series(power.MIN_OBSERVED_SESSIONS, 2000, 400)
    monkeypatch.setattr(power, "_open_panel", lambda directory: PANEL)
    monkeypatch.setattr(power, "historical_event_counts", lambda store, **kwargs: counts)
    report_path = tmp_path / "power.json"

    code = power.main(
        [
            "--panel-dir",
            str(panel),
            "--step0-registration",
            str(registration),
            "--first",
            "2020-01-02",
            "--last",
            "2020-01-21",
            "--repo",
            str(repo),
            "--json",
            str(report_path),
        ]
    )
    capsys.readouterr()

    assert code == 0
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["source"] == {
        "step0_registration": str(registration),
        "config_id": "caf-0000000000000001",
        "registration_sha256": hashlib.sha256(registration.read_bytes()).hexdigest(),
        "code_commit": "a" * 40,
    }


def test_main_refuses_an_unreadable_registration_by_name(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    panel = tmp_path / "panel"
    panel.mkdir()
    registration = tmp_path / "docs" / "research" / "p7-registration.json"
    report_path = tmp_path / "power.json"

    code = power.main(
        [
            "--panel-dir",
            str(panel),
            "--step0-registration",
            str(registration),
            "--first",
            "2020-01-02",
            "--last",
            "2020-01-21",
            "--repo",
            str(tmp_path),
            "--json",
            str(report_path),
        ]
    )
    captured = capsys.readouterr()

    assert code == 2
    assert "p7-registration.json" in captured.err
    assert "refused" in captured.err
    assert not report_path.exists()
    assert not registration.exists()


def test_main_refuses_dates_out_of_order(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)

    code = power.main(
        [
            "--panel-dir",
            str(tmp_path / "panel"),
            "--first",
            "2020-01-03",
            "--last",
            "2020-01-02",
            "--repo",
            str(tmp_path),
        ]
    )
    captured = capsys.readouterr()

    assert code == 2
    assert "2020-01-03" in captured.err
    assert "2020-01-02" in captured.err


def test_main_refuses_a_missing_panel_without_creating_it(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(power, "_load_step_zero_registration", lambda path, repo: _registration())
    panel = tmp_path / "absent-panel"

    code = power.main(
        [
            "--panel-dir",
            str(panel),
            "--first",
            "2020-01-02",
            "--last",
            "2020-01-21",
            "--repo",
            str(tmp_path),
        ]
    )
    captured = capsys.readouterr()

    assert code == 2
    assert "absent-panel" in captured.err
    assert not panel.exists()


# --- no client, no request, no live path ---------------------------------------------------------


FORBIDDEN_MODULES: Final[tuple[str, ...]] = (
    "tushare",
    "requests",
    "urllib",
    "urllib3",
    "httpx",
    "aiohttp",
    "socket",
    "openalpha_cn.models",
    "openalpha_cn.agents",
    "openalpha_cn.model_view",
    "openalpha_cn.providers",
    "openalpha_cn.strategy_survivor_forward",
)


def test_the_module_imports_no_transport_and_has_no_live_path() -> None:
    source = SOURCE.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(SOURCE))

    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)

    offenders = sorted(
        module
        for module in imported
        if any(module == banned or module.startswith(f"{banned}.") for banned in FORBIDDEN_MODULES)
    )
    assert offenders == []
    assert "registry" in imported  # not vacuous: the committed-registration reader is there
    assert "--live" not in source
    assert "TUSHARE_TOKEN" not in source
    assert "DEEPSEEK_API_KEY" not in source

    options = [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and node.value.startswith("--")
        and "live" in node.value
    ]
    assert options == []
