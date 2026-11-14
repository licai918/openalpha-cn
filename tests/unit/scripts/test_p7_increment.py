"""`scripts/research/p7_increment.py` (`V2-P7-028`): B's incremental rank information.

Plan B's two limit-event factors are read over the survivor composite, and the question is
whether they carry rank information about the h20 label that the composite does not already
carry: the partial rank IC of the oriented factor given the composite. The reading is
exploratory by construction (R-N6): the research window was P6's discovery data, so the
document carries the `EXPLORATORY, UNREGISTERED -- NOT EVIDENCE` banner, candidate wording and
no p-value field.

Everything here is computed at test time -- the one panel fixture is generated through the real
writers (AGENTS.md hard rule 6), and no `.parquet`, `.duckdb` or research store is read or
written.
"""

from __future__ import annotations

import hashlib
import importlib
import json
import random
import statistics
import sys
from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any, Final
from zoneinfo import ZoneInfo

import pytest
from strategy_fixtures import (
    COMMIT,
    TWO_YEAR_LAST,
    _two_year_panel,
    _write_by_year,
    daily_requirement,
)

from openalpha_cn.domain.daily_prices import DAILY_DATASET
from openalpha_cn.domain.incremental_ic import (
    REASON_TOO_FEW_NAMES,
    REASON_ZERO_RESIDUAL_VARIANCE,
)
from openalpha_cn.domain.price_limits import PRICE_LIMIT_DATASET
from openalpha_cn.domain.trading_calendar import (
    CalendarDay,
    TradingCalendar,
    build_trading_calendar,
)
from openalpha_cn.panel.store import PanelStore
from openalpha_cn.panel_factors import compute_factor, write_factor_panels
from openalpha_cn.panel_ingest import price_limit_requirement, session_publication_instant

ROOT: Final[Path] = Path(__file__).resolve().parents[3]
RESEARCH: Final[Path] = ROOT / "scripts" / "research"


def _research_module(name: str) -> ModuleType:
    if str(RESEARCH) not in sys.path:
        sys.path.insert(0, str(RESEARCH))
    return importlib.import_module(name)


grid = _research_module("grid")
p7_diagnostics = _research_module("p7_diagnostics")
p7_increment = _research_module("p7_increment")
p7_windows = _research_module("p7_windows")

SHANGHAI: Final[ZoneInfo] = ZoneInfo("Asia/Shanghai")
FIXTURE_WINDOW: Final = p7_windows.DiagnosticWindow(
    name="fixture", first=date(2017, 1, 3), last=date(2017, 12, 29), ceiling_year=2017
)
"""A read window over the generated 2017 calendar: the engine takes any window, the command line
only the research one."""
CODES: Final[tuple[str, ...]] = tuple(f"6000{n:02d}.SH" for n in range(24))
"""Two dozen names: enough for `MIN_NAMES` (four) with room for gaps."""
FORBIDDEN_P_VALUE_KEYS: Final[frozenset[str]] = frozenset(
    {"p", "p_value", "pvalue", "p_two_sided", "p_one_sided", "p_adjusted", "q", "q_value"}
)
"""No p-value, q-value or verdict field: R-N6's reading has no gate of its own."""


def weekday_calendar(first: date, last: date) -> TradingCalendar:
    days = [first + timedelta(days=offset) for offset in range((last - first).days + 1)]
    return build_trading_calendar(
        "SSE", [CalendarDay(calendar_date=day, is_trading=day.weekday() < 5) for day in days]
    )


CALENDAR: Final[TradingCalendar] = weekday_calendar(date(2016, 12, 1), date(2017, 12, 29))


def plan(
    *, first: date = FIXTURE_WINDOW.first, last: date = date(2017, 6, 30), stride: int = 20
) -> Any:
    return p7_windows.plan_signal_days(
        CALENDAR, window=FIXTURE_WINDOW, first=first, last=last, stride=stride, horizons=(20,)
    )


def table(seed: int) -> dict[str, float]:
    """A deterministic cross section of `CODES`; the same seed is the same table."""
    generator = random.Random(seed)
    return {code: generator.uniform(-1.0, 1.0) for code in CODES}


def fixed(day: date) -> dict[str, float]:
    return {code: float(index) for index, code in enumerate(CODES)}


def readings_of(
    sessions: Sequence[date],
    *,
    extra: Any = None,
    labels: Any = None,
) -> tuple[Any, ...]:
    """`increment_readings` over the seed-derived tables, with one callable replaceable."""
    return p7_increment.increment_readings(
        sessions=sessions,
        composite=lambda day: table(day.toordinal()),
        extra=extra if extra is not None else (lambda day: table(day.toordinal() + 1000)),
        labels=labels if labels is not None else (lambda day: table(day.toordinal() + 2000)),
    )


# --- the engine ---------------------------------------------------------------------------------


def test_a_planted_extra_factor_beyond_the_composite_gives_a_positive_mean() -> None:
    """The label is mostly the extra factor and a little the composite: the composite is
    residualised out, so the extra factor's partial IC is positive on every session and its mean
    is clearly above zero. A mutant that measured the plain rank IC instead would also be
    positive here, which is why the copy test below exists."""
    sessions = tuple(CALENDAR.trading_days[position] for position in (23, 44, 65, 86, 107, 128))
    composites: dict[date, dict[str, float]] = {}
    extras: dict[date, dict[str, float]] = {}
    labels: dict[date, dict[str, float]] = {}
    for session in sessions:
        composite = table(session.toordinal())
        extra = table(session.toordinal() + 1000)
        noise = table(session.toordinal() + 2000)
        composites[session] = composite
        extras[session] = extra
        labels[session] = {
            code: 0.8 * extra[code] + 0.1 * composite[code] + 0.1 * noise[code] for code in CODES
        }

    readings = readings_of(
        sessions,
        extra=lambda day: extras[day],
        labels=lambda day: labels[day],
    )

    values = [item.value for item in readings if item.value is not None]
    assert len(values) == len(sessions)
    assert statistics.fmean(values) > 0.2
    assert all(item.gaps == 0 for item in readings)
    assert all(item.n_scored == len(CODES) for item in readings)


def test_a_copy_of_the_composite_has_no_partial_rank_information() -> None:
    """The extra factor is the composite: its residual after residualising on the composite is
    exactly zero, so the statistic is undefined (the repository's `zero_residual_variance`), never
    a positive number. A near-copy is measurable and its mean is about zero."""
    sessions = (date(2017, 1, 3), date(2017, 1, 4), date(2017, 1, 5))
    composites = {session: table(session.toordinal()) for session in sessions}
    labels = {
        session: {
            code: composites[session][code] + 0.5 * table(session.toordinal() + 2000)[code]
            for code in CODES
        }
        for session in sessions
    }

    copied = readings_of(
        sessions,
        extra=lambda day: composites[day],
        labels=lambda day: labels[day],
    )
    assert [item.value for item in copied] == [None, None, None]
    assert {item.reason for item in copied} == {REASON_ZERO_RESIDUAL_VARIANCE}

    near = readings_of(
        sessions,
        extra=lambda day: {
            code: composites[day][code] + 0.5 * table(day.toordinal() + 1000)[code]
            for code in CODES
        },
        labels=lambda day: labels[day],
    )
    near_values = [item.value for item in near if item.value is not None]
    assert len(near_values) == len(sessions)
    assert abs(statistics.fmean(near_values)) < 0.2


def test_the_extra_is_oriented_by_the_declared_direction() -> None:
    """Both of B's factors are `lower_is_better`: the oriented value is the raw one negated, so a
    factor that rises with the return has a negative partial IC -- and the declared direction is
    what flips it. (`FACTORS[0]` is checked against the shipped definition, not a copy.)"""
    definition = p7_increment.FACTORS[0]
    assert definition.direction == "lower_is_better"
    assert p7_increment.orient(definition, {"600000.SH": 2.5}) == {"600000.SH": -2.5}

    session = date(2017, 1, 3)
    composite = table(11)
    raw = {code: float(index) for index, code in enumerate(CODES)}
    labels = dict(raw)
    unoriented = p7_increment.increment_readings(
        sessions=(session,),
        composite=lambda day: composite,
        extra=lambda day: raw,
        labels=lambda day: labels,
    )
    oriented = p7_increment.increment_readings(
        sessions=(session,),
        composite=lambda day: composite,
        extra=lambda day: p7_increment.orient(definition, raw),
        labels=lambda day: labels,
    )

    assert unoriented[0].value == pytest.approx(1.0)
    assert oriented[0].value == pytest.approx(-1.0)


def test_a_name_missing_any_of_the_three_values_is_a_gap_not_a_zero() -> None:
    """The measured scope is the default book's eligible names that carry all three values; a
    name missing its composite, its extra factor or its label is counted as a gap, and four
    measured names is the arithmetic's floor."""
    session = date(2017, 1, 3)
    eligible = CODES[:10]
    composite = {code: table(1)[code] for code in eligible}
    extra = {code: table(2)[code] for code in CODES[2:10]}
    labels = {code: table(3)[code] for code in CODES[3:10]}

    (measured,) = p7_increment.increment_readings(
        sessions=(session,),
        composite=lambda day: composite,
        extra=lambda day: extra,
        labels=lambda day: labels,
    )

    assert measured.eligible == len(eligible)
    assert measured.n_scored == 7
    assert measured.gaps == 3
    assert measured.value is not None
    assert measured.reason is None

    (nothing,) = p7_increment.increment_readings(
        sessions=(session,),
        composite=lambda day: {"600000.SH": 1.0, "600001.SH": 2.0},
        extra=lambda day: {"600002.SH": 1.0},
        labels=lambda day: {"600003.SH": 1.0},
    )
    assert nothing.eligible == 2
    assert nothing.n_scored == 0
    assert nothing.gaps == 2
    assert nothing.value is None
    assert nothing.reason == p7_increment.REASON_NO_COMPLETE_NAMES

    (too_few,) = p7_increment.increment_readings(
        sessions=(session,),
        composite=lambda day: {code: float(index) for index, code in enumerate(CODES[:3])},
        extra=lambda day: {code: float(index) for index, code in enumerate(CODES[:3])},
        labels=lambda day: {code: float(index) for index, code in enumerate(CODES[:3])},
    )
    assert too_few.reason == REASON_TOO_FEW_NAMES


# --- the document -------------------------------------------------------------------------------


def document() -> dict[str, Any]:
    chosen = plan()
    readings = {
        definition.qualified_key: readings_of(chosen.sessions)
        for definition in p7_increment.FACTORS
    }
    return p7_increment.increment_document(  # type: ignore[no-any-return]
        window=FIXTURE_WINDOW,
        plan=chosen,
        readings=readings,
        config={
            "as_of": "2017-07-03T00:00:00+08:00",
            "first": chosen.first.isoformat(),
            "last": chosen.last.isoformat(),
            "stride": chosen.stride,
            "horizon": p7_increment.HORIZON,
        },
        code_commit="0" * 40,
    )


def keys_of(value: object) -> set[str]:
    """Every mapping key in a JSON-ready value, recursively."""
    found: set[str] = set()
    if isinstance(value, Mapping):
        for key, item in value.items():
            found.add(str(key))
            found |= keys_of(item)
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        for item in value:
            found |= keys_of(item)
    return found


def test_the_document_carries_the_exploratory_banner_and_no_p_value() -> None:
    written = document()
    lines = p7_increment.increment_lines(written)
    text = json.dumps(written, sort_keys=True, ensure_ascii=False) + "\n".join(lines)

    assert written["kind"] == "p7-increment/v1"
    assert (
        written["banner"]
        == "EXPLORATORY, UNREGISTERED -- NOT EVIDENCE: fixture window 2017-01-03..2017-12-29"
    )
    assert lines[0] == written["banner"]
    assert written["wording"] == [
        p7_diagnostics.EXPLORATORY_WORDING,
        p7_diagnostics.IN_SAMPLE_WORDING,
        p7_diagnostics.CANDIDATE_WORDING,
    ]
    assert "candidate" in text
    assert "候选" in text
    assert not (keys_of(written) & FORBIDDEN_P_VALUE_KEYS)
    assert "p_value" not in text
    assert "p-value" not in text
    assert written["provenance"]["random_component"] == "none"
    assert "descriptive" in "\n".join(lines)


def test_the_document_records_one_row_per_sampled_session_and_its_summary() -> None:
    written = document()
    chosen = plan()

    assert [block["factor"] for block in written["factors"]] == [
        definition.qualified_key for definition in p7_increment.FACTORS
    ]
    assert written["plan"]["stride"] == 20
    assert written["plan"]["horizon"] == 20
    for block in written["factors"]:
        assert [row["session"] for row in block["days"]] == [
            session.isoformat() for session in chosen.sessions
        ]
        values = [row["value"] for row in block["days"] if row["value"] is not None]
        assert block["summary"]["n"] == len(values)
        assert block["summary"]["mean"] == pytest.approx(statistics.fmean(values))
        assert block["summary"]["sd"] == pytest.approx(statistics.stdev(values))
        assert block["summary"]["t"] is not None
        for row in block["days"]:
            assert set(row) == {"session", "eligible", "n", "gaps", "value", "reason"}

    lines = p7_increment.increment_lines(written)
    day_lines = [line for line in lines if ": partial rank IC " in line]
    assert len(day_lines) == sum(len(block["days"]) for block in written["factors"])
    for block in written["factors"]:
        for row in block["days"]:
            assert any(line.startswith(f"  {row['session']}: partial rank IC ") for line in lines)


def test_the_config_digest_is_recomputable_from_the_document() -> None:
    written = document()
    expected = hashlib.sha256(
        grid.canonical_json({"config": written["config"], "request": written["request"]}).encode(
            "utf-8"
        )
    ).hexdigest()

    assert written["provenance"]["config_digest"] == expected
    changed = dict(written["request"])
    changed["first"] = "2017-01-04"
    assert (
        hashlib.sha256(
            grid.canonical_json({"config": written["config"], "request": changed}).encode("utf-8")
        ).hexdigest()
        != expected
    )


# --- the command line ---------------------------------------------------------------------------


class StubSources:
    """`p7_increment._open_sources`' answer on the generated 2017 calendar, with no panel."""

    def __init__(self) -> None:
        self.calendar = CALENDAR
        self.composite = lambda day: fixed(day)
        self.labels = lambda day: {code: float(index) / 100.0 for index, code in enumerate(CODES)}
        self.extras = {
            definition.qualified_key: (lambda day: fixed(day))
            for definition in p7_increment.FACTORS
        }
        self.store = SimpleNamespace(partitions_read=lambda: ())


def stub_sources(monkeypatch: pytest.MonkeyPatch) -> StubSources:
    sources = StubSources()
    opened: list[Mapping[str, Any]] = []

    def answer(*args: object, **kwargs: Any) -> StubSources:
        opened.append(kwargs)
        return sources

    monkeypatch.setattr(p7_increment, "_open_sources", answer)
    return sources


def explore_2017(tmp_path: Path, *extra: str) -> list[str]:
    return [
        "explore",
        "--runtime-dir",
        str(tmp_path),
        "--as-of",
        "2017-07-03T00:00:00+08:00",
        "--first",
        "2017-01-03",
        "--last",
        "2017-06-30",
        *extra,
    ]


def test_explore_refuses_a_window_that_touches_2022_by_name(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    def opened(*args: object, **kwargs: object) -> None:
        raise AssertionError("a store was opened")

    monkeypatch.setattr(p7_increment, "_open_sources", opened)
    code = p7_increment.main(
        [
            "explore",
            "--runtime-dir",
            str(tmp_path),
            "--as-of",
            "2022-01-05T00:00:00+08:00",
            "--first",
            "2021-11-01",
            "--last",
            "2022-01-04",
            "--out",
            str(tmp_path / "out"),
        ]
    )

    assert code == 2
    error = capsys.readouterr().err
    assert "research" in error
    assert "2021-12-31" in error
    assert not (tmp_path / "out").exists()


def test_explore_refuses_a_first_day_before_the_research_window(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(
        p7_increment,
        "_open_sources",
        lambda *args, **kwargs: pytest.fail("a store was opened"),
    )

    assert (
        p7_increment.main(explore_2017(tmp_path, "--first", "2014-12-01", "--last", "2015-06-30"))
        == 2
    )
    assert "research" in capsys.readouterr().err


def test_explore_writes_once_and_a_rerun_is_a_no_op(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    opening = stub_sources(monkeypatch)
    out = tmp_path / "out"
    argv = explore_2017(tmp_path, "--out", str(out))

    assert p7_increment.main(argv) == 0
    assert opening is not None
    first_run = capsys.readouterr().out
    (json_path,) = out.glob("p7-increment-*.json")
    document = json.loads(json_path.read_text(encoding="utf-8"))
    stem = f"p7-increment-{document['provenance']['config_digest'][:16]}"
    assert json_path.name == f"{stem}.json"
    text_path = out / f"{stem}.txt"
    assert document["kind"] == "p7-increment/v1"
    assert text_path.read_text(encoding="utf-8").splitlines() == p7_increment.increment_lines(
        document
    )
    written = {
        path: (path.read_bytes(), path.stat().st_mtime_ns) for path in (json_path, text_path)
    }
    assert f"written: {json_path}" in first_run.splitlines()
    assert f"written: {text_path}" in first_run.splitlines()

    assert p7_increment.main(argv) == 0
    assert {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in written} == written
    second_run = capsys.readouterr().out.splitlines()
    assert not any(line.startswith("written:") for line in second_run)
    assert f"unchanged, nothing written: {json_path}" in second_run

    json_path.write_bytes(json_path.read_bytes() + b" ")
    assert p7_increment.main(argv) != 0
    assert "already holds a different document" in capsys.readouterr().err


def test_explore_without_out_prints_and_writes_nothing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    stub_sources(monkeypatch)
    runtime = tmp_path / "runtime"
    runtime.mkdir()

    assert p7_increment.main(explore_2017(runtime)) == 0
    printed = capsys.readouterr().out.splitlines()
    assert printed[0].startswith("EXPLORATORY, UNREGISTERED -- NOT EVIDENCE")
    assert list(runtime.iterdir()) == []


def test_explore_refuses_the_checkout_as_a_runtime_directory_before_opening_a_store(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def opened(*args: object, **kwargs: object) -> None:
        raise AssertionError("a store was opened")

    monkeypatch.setattr(p7_increment, "capped_panel_store", opened)

    assert p7_increment.main(explore_2017(ROOT)) == 2
    assert "main checkout" in capsys.readouterr().err


def test_explore_refuses_an_as_of_without_a_timezone_before_opening_anything(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(
        p7_increment,
        "_open_sources",
        lambda *args, **kwargs: pytest.fail("a store was opened"),
    )

    assert p7_increment.main(explore_2017(tmp_path, "--as-of", "2017-07-03T00:00:00")) == 2
    assert "timezone" in capsys.readouterr().err


# --- the panel sources --------------------------------------------------------------------------


def test_the_factor_read_chooses_the_1630_build_and_skips_non_finite_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The point-in-time build choice, copied from `p7_reading._FactorReads`: of three builds
    stamped on one session's date the 16:30 one is used, and a non-finite or non-computed value
    is not a cross-section member."""
    definition = p7_increment.FACTORS[0]
    day = date(2017, 1, 3)
    next_day = date(2017, 1, 4)
    published = session_publication_instant(day)

    def row(subject: str, when: datetime, value: float, coverage: str = "computed") -> Any:
        return SimpleNamespace(coverage=coverage, value=value, as_of=when, subject=subject)

    rows = (
        row(
            "600000.SH",
            datetime.combine(day, datetime.min.time(), tzinfo=SHANGHAI) + timedelta(hours=15),
            1.0,
        ),
        row("600000.SH", published, 2.0),
        row("600000.SH", published + timedelta(hours=4, minutes=30), 3.0),
        row("600001.SH", published, float("inf")),
        row("600002.SH", published, 4.0, coverage="insufficient_history"),
        row("600000.SH", session_publication_instant(next_day), 5.0),
    )
    monkeypatch.setattr(p7_increment, "load_factor_observations", lambda *args, **kwargs: rows)
    reads = p7_increment._FactorReads(object(), as_of=datetime(2017, 2, 1, tzinfo=UTC))  # type: ignore[arg-type]

    assert reads.values_on(definition, day) == {"600000.SH": 2.0}
    assert reads.values_on(definition, next_day) == {"600000.SH": 5.0}


def _limit_build(
    store: PanelStore, panel: Any, definition: Any, session: date, values: Mapping[str, float]
) -> Any:
    """One raw limit-event build over the generated panel, with an injected evaluator so the
    stored value is `values` rather than a count of bars the fixture never scripted."""
    instant = session_publication_instant(session)
    years = tuple(year for year in (session.year - 1, session.year) if year >= 2026)
    calendar = panel.calendar()

    return compute_factor(
        store,
        definition,
        as_of=instant,
        subjects=tuple(panel.securities),
        universe=frozenset(panel.securities),
        requirements={
            DAILY_DATASET: daily_requirement(
                calendar, years=years, as_of=instant, max_staleness=timedelta(days=30)
            ),
            PRICE_LIMIT_DATASET: price_limit_requirement(
                calendar, years=years, as_of=instant, max_staleness=timedelta(days=30)
            ),
        },
        code_commit=COMMIT,
        built_at=instant,
        evaluators={definition.qualified_key: lambda window: values[window.subject]},
    )


def test_the_panel_sources_read_each_raw_build_and_orient_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`_open_sources` over a generated panel: both B factors' stored raw builds are read for a
    session and the extra callable answers with the declared direction's negation. The panel is
    the generated two-year one (its calendar is SZSE); nothing is read from a research store."""
    monkeypatch.setattr(p7_increment, "EXCHANGE", "SZSE")
    panel = _two_year_panel()
    store = PanelStore(tmp_path / "panel")
    _write_by_year(store, panel)
    session = date(2027, 1, 4)
    expected = {code: float(index + 1) for index, code in enumerate(panel.securities)}
    write_factor_panels(
        store,
        [
            _limit_build(store, panel, definition, session, expected)
            for definition in p7_increment.FACTORS
        ],
    )
    window = p7_windows.DiagnosticWindow(
        name="fixture", first=session, last=TWO_YEAR_LAST, ceiling_year=2027
    )
    settings = p7_increment.ExploreSettings(as_of=panel.as_of, first=session, last=TWO_YEAR_LAST)

    sources = p7_increment._open_sources(tmp_path, window=window, settings=settings)

    reads = p7_increment._FactorReads(sources.store, as_of=panel.as_of)
    for definition in p7_increment.FACTORS:
        raw = dict(reads.values_on(definition, session))
        assert raw, f"the {definition.qualified_key} build was not read back"
        assert raw == {code: expected[code] for code in raw}
        oriented = dict(sources.extras[definition.qualified_key](session))
        assert oriented == {code: -value for code, value in raw.items()}
        assert oriented != raw
