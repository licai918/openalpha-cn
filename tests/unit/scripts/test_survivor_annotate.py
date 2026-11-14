"""`scripts/survivor_annotate.py`: A's veto and B's factors beside the daily list.

The annotation is a **print-only** command for the human trader: it reads a finished survivor
day's journal and a clone of the panel, and prints one row per name the day's plan acts on --
its role in that plan, plan A's veto with the count and mean the rule was decided from, and plan
B's two limit-event factors with their percentile in the stored cross section. It writes
nothing, sends no request, admits no registration and is not bound by one (R-N2): every cell is
candidate reference, never part of the list, the paper book or the journal.

Everything here is built at test time: the panel is generated and written through the real
writers (hard rule 6), the B raw builds go through the real factor engine with the evaluator
seam planted, and the journal is a fabricated complete `openalpha-daily-survivor/v1` day. The
generated calendar is `panel_fixtures`' `SZSE` one, so the command's own exchange discovery is
exercised rather than assumed.

Two refusals carry the issue's boundaries: a session with no raw B build exits 1 and prints the
exact `openalpha factor build ...` line (review focus 3), and a session inside the veto window
restated after its own 16:30 refuses by name rather than reproducing a different vetoed set
(R-N3a) -- the panel store keeps no earlier version of a restated row, so the read raises
`PanelStorageError` and the annotation must convert it, not show the restated bytes.
"""

from __future__ import annotations

import ast
import hashlib
import importlib
import json
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta
from pathlib import Path
from types import MappingProxyType
from typing import Any, Final
from zoneinfo import ZoneInfo

import pytest
from panel_fixtures import (
    GeneratedPanel,
    _arrayed_rows,
    generate_limit_streak_panel,
    generate_panel,
    write_generated_panel,
)

from openalpha_cn.domain.daily_prices import DAILY_BASIC_DATASET, DAILY_DATASET
from openalpha_cn.domain.factor import FactorDefinition
from openalpha_cn.domain.panel_batch import ColumnarPanelBatch, PanelColumn, TimelineColumns
from openalpha_cn.domain.price_limits import PRICE_LIMIT_DATASET
from openalpha_cn.panel.store import PanelStore
from openalpha_cn.panel_factors import (
    BROKEN_BOARD_COUNT_20,
    LIMIT_UP_COUNT_20,
    FactorPanel,
    compute_factor,
    write_factor_panels,
)
from openalpha_cn.panel_ingest import (
    daily_requirement,
    load_suspensions,
    price_limit_requirement,
    session_publication_instant,
    write_daily_panel,
)
from openalpha_cn.strategy_survivor_summary import SURVIVOR_WORDING

ROOT: Final[Path] = Path(__file__).resolve().parents[3]
SHANGHAI: Final[ZoneInfo] = ZoneInfo("Asia/Shanghai")
COMMIT: Final[str] = "abcdef1234567"
STALENESS: Final[timedelta] = timedelta(days=30)


def _script(name: str) -> Any:
    for directory in (ROOT / "scripts",):
        if str(directory) not in sys.path:
            sys.path.insert(0, str(directory))
    return importlib.import_module(name)


annotate = _script("survivor_annotate")
daily = _script("daily_selection")

# --- the story the four roles and the two rules are read off -------------------------------------

FIRST: Final[date] = date(2026, 1, 5)
LAST: Final[date] = date(2026, 2, 27)
TARGET_INDEX: Final[int] = 30
"""Forty priced sessions, so the B factors' twenty-session reach is satisfied at the target."""
CONFIG_ID: Final[str] = "cfg-annotation-0001"
ENTRY_CODE: Final[str] = "000001.SZ"
EXIT_CODE: Final[str] = "000002.SZ"
"""Also held by the book: the exit decision wins the row, which is the plan's precedence."""
HOLD_VETOED_CODE: Final[str] = "600000.SH"
HOLD_PLAIN_CODE: Final[str] = "300750.SZ"
WATCH_CODE: Final[str] = "601318.SH"
"""Left out of both B builds, so its two factor cells are the `null` case."""
BROKEN_VALUES: Final[Mapping[str, float]] = MappingProxyType(
    {
        ENTRY_CODE: 2.0,
        EXIT_CODE: 1.0,
        HOLD_VETOED_CODE: 3.0,
        "600519.SH": 0.0,
        HOLD_PLAIN_CODE: 0.0,
        "688981.SH": 0.0,
        "002415.SZ": 0.0,
    }
)
"""B's broken-board values, planted through the evaluator seam: one number per subject."""
LATER_UP_VALUE: Final[float] = 5.0
"""A second `limit_up_count_20` build two hours after the close: the earliest build must win."""
SMOKE_REGISTRATION: Final[dict[str, Any]] = {"settings": {"smoke_run": {"issue": "V2-P7-019"}}}


@dataclass(frozen=True, slots=True)
class World:
    """A runtime directory holding one fabricated day journal, and the panel under it."""

    root: Path
    panel: GeneratedPanel
    store: PanelStore
    runtime: Path
    config_id: str
    session: date
    journal_path: Path

    @property
    def panel_directory(self) -> Path:
        return self.runtime / "panel"


def _published(day: date) -> datetime:
    return session_publication_instant(day)


def _evening(day: date) -> datetime:
    return datetime(day.year, day.month, day.day, 18, 30, tzinfo=SHANGHAI)


def _scripted(panel: GeneratedPanel, *, code: str, day: date) -> GeneratedPanel:
    """`panel` with one security's single broken board on `day`: touches its band, closes below.

    Every other name keeps the generator's flat price path, so the veto window ending at `day`
    holds exactly one event -- plan A's one-broken-board bearish case.
    """
    key = (code, day.isoformat())
    batches = dict(panel.batches)
    batches[DAILY_DATASET] = _arrayed_rows(
        panel.batch(DAILY_DATASET),
        date_column="trade_date",
        overrides={
            key: {
                "open": 10.0,
                "high": 11.2,
                "low": 9.9,
                "close": 10.5,
                "pre_close": 10.0,
                "pct_chg": 5.0,
            }
        },
    )
    batches[DAILY_BASIC_DATASET] = _arrayed_rows(
        panel.batch(DAILY_BASIC_DATASET),
        date_column="trade_date",
        overrides={key: {"close": 10.5}},
    )
    batches[PRICE_LIMIT_DATASET] = _arrayed_rows(
        panel.batch(PRICE_LIMIT_DATASET),
        date_column="trade_date",
        overrides={key: {"up_limit": 11.0, "down_limit": 9.0}},
    )
    return replace(panel, batches=MappingProxyType(batches))


def _requirements(panel: GeneratedPanel, as_of: datetime) -> dict[str, Any]:
    calendar = panel.calendar()
    return {
        DAILY_DATASET: daily_requirement(
            calendar, years=(panel.year,), as_of=as_of, max_staleness=STALENESS
        ),
        PRICE_LIMIT_DATASET: price_limit_requirement(
            calendar, years=(panel.year,), as_of=as_of, max_staleness=STALENESS
        ),
    }


def _build(
    store: PanelStore,
    panel: GeneratedPanel,
    definition: FactorDefinition,
    *,
    as_of: datetime,
    subjects: Sequence[str],
    values: Callable[[str], float],
) -> FactorPanel:
    return compute_factor(
        store,
        definition,
        as_of=as_of,
        subjects=subjects,
        universe=frozenset(panel.securities),
        requirements=_requirements(panel, as_of),
        code_commit=COMMIT,
        built_at=as_of,
        evaluators={definition.qualified_key: lambda context: values(context.subject)},
    )


def _journal(
    *,
    session: date,
    config_id: str,
    paper: Mapping[str, Any] | None,
    opening: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """A finished survivor day as the command files it: JSON, `result` present, wording last."""
    result: dict[str, Any] = {"session": session.isoformat(), "wording": SURVIVOR_WORDING}
    if opening is not None:
        result["opening"] = dict(opening)
    else:
        result["paper"] = None if paper is None else dict(paper)
    return {
        "schema": daily.SURVIVOR_JOURNAL_SCHEMA,
        "session": session.isoformat(),
        "as_of": _evening(session).isoformat(),
        "held": [],
        "held_from": None,
        "registration": {
            "path": "p7-registration.json",
            "sha256": "a" * 64,
            "commit": "b" * 40,
            "config_id": config_id,
        },
        "panel": {"caught_up": []},
        "result": result,
    }


def _plan(
    *,
    entries: Sequence[str] = (),
    exits: Sequence[str] = (),
    holdings: Sequence[str] = (),
    watch: Sequence[str] = (),
) -> dict[str, Any]:
    """The `result.paper` block `plan_names` and the decision's two lists are read out of."""
    return {
        "book": {"holdings": [{"ts_code": code} for code in holdings]},
        "open_orders": [{"ts_code": code, "side": "sell", "carried": False} for code in exits],
        "decision": {
            "exits": [{"ts_code": code, "reason": "lost_survivor"} for code in exits],
            "entries": [{"ts_code": code, "reason": "INITIAL_BUILD"} for code in entries],
            "watch_list": list(watch),
        },
    }


def _story_plan() -> dict[str, Any]:
    return _plan(
        entries=(ENTRY_CODE,),
        exits=(EXIT_CODE,),
        holdings=(EXIT_CODE, HOLD_VETOED_CODE, HOLD_PLAIN_CODE),
        watch=(WATCH_CODE,),
    )


def _write_journal(runtime: Path, config_id: str, journal: Mapping[str, Any]) -> Path:
    directory = daily.survivor_journal_directory(runtime, config_id)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{journal['session']}.json"
    path.write_text(json.dumps(journal, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return path


def _story(root: Path) -> World:
    panel = generate_limit_streak_panel(window=(FIRST, LAST))
    session = panel.sessions[TARGET_INDEX]
    panel = _scripted(panel, code=HOLD_VETOED_CODE, day=session)
    runtime = root / "runtime"
    store = PanelStore(runtime / "panel")
    write_generated_panel(store, panel)
    published = _published(session)
    subjects = tuple(code for code in panel.securities if code != WATCH_CODE)
    limit_up = _build(
        store,
        panel,
        LIMIT_UP_COUNT_20,
        as_of=published,
        subjects=subjects,
        values=lambda code: 0.0,
    )
    broken = _build(
        store,
        panel,
        BROKEN_BOARD_COUNT_20,
        as_of=published,
        subjects=subjects,
        values=lambda code: BROKEN_VALUES[code],
    )
    rebuilt = _build(
        store,
        panel,
        LIMIT_UP_COUNT_20,
        as_of=published + timedelta(hours=2),
        subjects=subjects,
        values=lambda code: LATER_UP_VALUE,
    )
    write_factor_panels(store, [limit_up, broken, rebuilt])
    journal = _journal(session=session, config_id=CONFIG_ID, paper=_story_plan())
    path = _write_journal(runtime, CONFIG_ID, journal)
    return World(
        root=root,
        panel=panel,
        store=store,
        runtime=runtime,
        config_id=CONFIG_ID,
        session=session,
        journal_path=path,
    )


@pytest.fixture(scope="module")
def story(tmp_path_factory: pytest.TempPathFactory) -> World:
    return _story(tmp_path_factory.mktemp("annotation-story"))


def _bare(root: Path) -> World:
    """A small panel with a complete journal and no factor build of any kind."""
    panel = generate_panel()
    runtime = root / "runtime"
    store = PanelStore(runtime / "panel")
    write_generated_panel(store, panel)
    session = panel.sessions[-1]
    journal = _journal(session=session, config_id=CONFIG_ID, paper=_story_plan())
    path = _write_journal(runtime, CONFIG_ID, journal)
    return World(
        root=root,
        panel=panel,
        store=store,
        runtime=runtime,
        config_id=CONFIG_ID,
        session=session,
        journal_path=path,
    )


@pytest.fixture(scope="module")
def bare(tmp_path_factory: pytest.TempPathFactory) -> World:
    return _bare(tmp_path_factory.mktemp("annotation-bare"))


# --- running the command -------------------------------------------------------------------------


def _arguments(
    world: World,
    *,
    session: date | None = None,
    json_output: bool = False,
    config_id: str | None = None,
) -> list[str]:
    arguments = [
        "--runtime-dir",
        str(world.runtime),
        "--config-id",
        world.config_id if config_id is None else config_id,
    ]
    if session is not None:
        arguments += ["--session", session.isoformat()]
    if json_output:
        arguments.append("--json")
    return arguments


def _invoke(capsys: pytest.CaptureFixture[str], arguments: Sequence[str]) -> tuple[int, str, str]:
    code = annotate.main(list(arguments))
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def _document(
    capsys: pytest.CaptureFixture[str],
    world: World,
    *,
    session: date | None = None,
) -> dict[str, Any]:
    code, out, err = _invoke(capsys, _arguments(world, session=session, json_output=True))
    assert code == 0, err
    assert err == ""
    return json.loads(out)


def _rows(document: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    return {row["ts_code"]: row for row in document["rows"]}


def _tree(root: Path) -> dict[str, str]:
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


# --- the four roles ------------------------------------------------------------------------------


def test_the_four_roles_appear_once_each(capsys: pytest.CaptureFixture[str], story: World) -> None:
    """The plan's names carry ENTRY, EXIT, HOLD and WATCH, and every name appears once.

    The exit name is also in the book's holdings -- a decided sale is still held until the open
    -- so the row proves the role precedence rather than reading the book twice.
    """
    document = _document(capsys, story)

    assert document["kind"] == "survivor-annotation/v1"
    assert document["session"] == story.session.isoformat()
    assert document["config_id"] == CONFIG_ID
    assert document["wording"] == SURVIVOR_WORDING
    roles = [row["role"] for row in document["rows"]]
    assert roles == ["ENTRY", "EXIT", "HOLD", "HOLD", "WATCH"]
    assert set(roles) == {"ENTRY", "EXIT", "HOLD", "WATCH"}
    assert [row["ts_code"] for row in document["rows"]] == [
        ENTRY_CODE,
        EXIT_CODE,
        HOLD_PLAIN_CODE,
        HOLD_VETOED_CODE,
        WATCH_CODE,
    ]


def test_the_annotation_prints_the_header_every_role_and_the_journal_wording(
    capsys: pytest.CaptureFixture[str], story: World
) -> None:
    """The printed form: the candidate-reference header, one line per row, the wording last."""
    code, out, err = _invoke(capsys, _arguments(story, session=story.session))

    assert code == 0, err
    assert err == ""
    lines = out.splitlines()
    assert lines[0] == annotate.HEADER
    assert lines[-1] == SURVIVOR_WORDING
    body = lines[1:-1]
    assert len(body) == 5
    by_code = {line.split()[1]: line for line in body}
    assert by_code[ENTRY_CODE].startswith(f"ENTRY {ENTRY_CODE} | veto: none")
    assert by_code[EXIT_CODE].startswith(f"EXIT {EXIT_CODE} | veto: none")
    vetoed = by_code[HOLD_VETOED_CODE]
    assert vetoed.startswith(f"HOLD {HOLD_VETOED_CODE} | veto: market_bearish_5/v1, vetoed")
    assert "events 1" in vetoed
    assert "mean -0.6500" in vetoed
    assert by_code[WATCH_CODE].startswith(f"WATCH {WATCH_CODE} | veto: none")
    assert "limit_up_count_20: not in the build" in by_code[WATCH_CODE]
    assert "broken_board_count_20: not in the build" in by_code[WATCH_CODE]
    assert "limit_up_count_20: 0.0000 (pct 0.500 of 7)" in by_code[ENTRY_CODE]


# --- plan A: the veto ----------------------------------------------------------------------------


def test_a_vetoed_name_shows_its_event_count_and_mean_weight(
    capsys: pytest.CaptureFixture[str], story: World
) -> None:
    """The vetoed name carries the rule, the answer and the arithmetic the rule was made of."""
    row = _rows(_document(capsys, story))[HOLD_VETOED_CODE]

    assert row["veto"] == {
        "rule": "market_bearish_5/v1",
        "vetoed": True,
        "event_count": 1,
        "mean_weight": -0.65,
    }


def test_a_name_with_no_event_shows_a_null_veto(
    capsys: pytest.CaptureFixture[str], story: World
) -> None:
    """A name absent from the window's events has no reading to print, and prints none."""
    rows = _rows(_document(capsys, story))

    assert rows[HOLD_PLAIN_CODE]["veto"] is None
    assert rows[ENTRY_CODE]["veto"] is None


def test_a_name_not_in_the_build_shows_a_null_factor_cell(
    capsys: pytest.CaptureFixture[str], story: World
) -> None:
    """B's two cells are `null` exactly when the name is not in the stored cross section."""
    rows = _rows(_document(capsys, story))
    row = rows[WATCH_CODE]

    assert row["limit_up_count_20"] is None
    assert row["broken_board_count_20"] is None
    present = rows[ENTRY_CODE]
    assert present["limit_up_count_20"] == {"value": 0.0, "pct": 0.5, "n": 7}
    assert present["broken_board_count_20"] == {"value": 2.0, "pct": 5.5 / 7, "n": 7}


# --- plan B: the two factors ---------------------------------------------------------------------


def test_a_cross_section_of_zeros_puts_every_name_at_the_fiftieth_percentile(
    capsys: pytest.CaptureFixture[str], story: World
) -> None:
    """Mid-rank / N: every name ties with every other, so every percentile is 0.5."""
    assert annotate.percentile(0.0, (0.0, 0.0, 0.0, 0.0)) == 0.5
    with pytest.raises(ValueError):
        annotate.percentile(7.0, ())
    rows = _rows(_document(capsys, story))
    for code in (ENTRY_CODE, EXIT_CODE, HOLD_VETOED_CODE, HOLD_PLAIN_CODE):
        assert rows[code]["limit_up_count_20"]["pct"] == 0.5


def test_the_earliest_raw_build_at_or_after_the_close_is_the_one_shown(
    capsys: pytest.CaptureFixture[str], story: World
) -> None:
    """A second build two hours later exists; the one stamped at the session's 16:30 is read."""
    row = _rows(_document(capsys, story))[ENTRY_CODE]

    assert row["limit_up_count_20"]["value"] == 0.0
    assert row["limit_up_count_20"]["value"] != LATER_UP_VALUE
    assert row["broken_board_count_20"]["value"] == BROKEN_VALUES[ENTRY_CODE]


# --- the command writes nothing and sends no request (R-N2) --------------------------------------


def test_the_annotation_writes_nothing(capsys: pytest.CaptureFixture[str], story: World) -> None:
    """The runtime directory and the panel under it are byte-identical before and after."""
    runtime_before = _tree(story.runtime)
    panel_before = _tree(story.panel_directory)

    code, out, err = _invoke(capsys, _arguments(story, session=story.session))

    assert code == 0, err
    assert out
    assert _tree(story.runtime) == runtime_before
    assert _tree(story.panel_directory) == panel_before


# --- the refusals --------------------------------------------------------------------------------


def test_a_missing_b_build_is_refused_with_the_build_command(
    capsys: pytest.CaptureFixture[str], bare: World
) -> None:
    """Exit 1 with the exact `openalpha factor build ...` line, and nothing written."""
    before = _tree(bare.runtime)

    code, out, err = _invoke(capsys, _arguments(bare, session=bare.session))

    assert code == 1
    assert out == ""
    lines = err.splitlines()
    assert "limit_up_count_20/v1" in lines[0]
    assert lines[-1] == (
        "openalpha factor build --factor limit_up_count_20/v1 --tier raw "
        f"--year {bare.session.year} "
        f"--as-of {_published(bare.session).isoformat()} "
        "--max-staleness-days <days>"
    )
    assert _tree(bare.runtime) == before


def test_an_epoch_day_journal_is_refused_by_name(
    capsys: pytest.CaptureFixture[str], bare: World
) -> None:
    """The epoch day only opens the book: there is no plan to annotate, and the refusal says so."""
    config_id = "cfg-annotation-epoch1"
    journal = _journal(
        session=bare.session,
        config_id=config_id,
        paper=None,
        opening={"snapshot_id": "pbs_epoch"},
    )
    _write_journal(bare.runtime, config_id, journal)

    code, out, err = _invoke(capsys, _arguments(bare, session=bare.session, config_id=config_id))

    assert code == 1
    assert out == ""
    assert "epoch session" in err
    assert "no plan" in err


def test_a_settle_only_day_is_refused_by_name(
    capsys: pytest.CaptureFixture[str], bare: World
) -> None:
    """A day that settled only ran no evening: no plan, refused rather than printed empty."""
    config_id = "cfg-annotation-settle"
    journal = _journal(session=bare.session, config_id=config_id, paper=None)
    _write_journal(bare.runtime, config_id, journal)

    code, out, err = _invoke(capsys, _arguments(bare, session=bare.session, config_id=config_id))

    assert code == 1
    assert out == ""
    assert "settled only" in err


def test_no_journal_is_refused_by_name(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    """A runtime directory with no journal of the configuration is refused before anything opens."""
    runtime = tmp_path / "runtime"

    code, out, err = _invoke(
        capsys,
        [
            "--runtime-dir",
            str(runtime),
            "--config-id",
            CONFIG_ID,
            "--session",
            date(2026, 1, 16).isoformat(),
        ],
    )

    assert code == 1
    assert out == ""
    assert "no survivor journal" in err
    assert not runtime.exists()


def test_an_incomplete_journal_is_refused_by_name(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """A journal with no `result` is a day whose steps stopped early: refused, never annotated."""
    runtime = tmp_path / "runtime"
    journal = _journal(session=date(2026, 1, 16), config_id=CONFIG_ID, paper=_story_plan())
    del journal["result"]
    _write_journal(runtime, CONFIG_ID, journal)

    code, out, err = _invoke(
        capsys,
        [
            "--runtime-dir",
            str(runtime),
            "--config-id",
            CONFIG_ID,
            "--session",
            date(2026, 1, 16).isoformat(),
        ],
    )

    assert code == 1
    assert out == ""
    assert "not finished" in err


def test_a_runtime_without_a_panel_catalog_is_refused_before_the_store_is_opened(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """`PanelStore` would mkdir its root, so the catalog is checked before the store is built."""
    runtime = tmp_path / "runtime"
    journal = _journal(session=date(2026, 1, 16), config_id=CONFIG_ID, paper=_story_plan())
    _write_journal(runtime, CONFIG_ID, journal)

    code, out, err = _invoke(
        capsys,
        [
            "--runtime-dir",
            str(runtime),
            "--config-id",
            CONFIG_ID,
            "--session",
            date(2026, 1, 16).isoformat(),
        ],
    )

    assert code == 1
    assert out == ""
    assert "catalog.duckdb" in err
    assert not (runtime / "panel").exists()
    assert sorted(path.name for path in runtime.iterdir()) == ["daily_survivor"]


# --- R-N3a: a restatement inside the window refuses, it does not reproduce -----------------------


def _row_indices(batch: ColumnarPanelBatch, *, date_column: str) -> tuple[str, ...]:
    for column in batch.columns:
        if column.name == date_column:
            return tuple(str(value) for value in column.values)
    raise AssertionError(f"{batch.dataset} has no {date_column} column")


def _rewritten(
    batch: ColumnarPanelBatch,
    *,
    date_column: str,
    overrides: Mapping[tuple[str, str], Mapping[str, object]] = MappingProxyType({}),
    later_revision: Mapping[str, datetime] = MappingProxyType({}),
) -> ColumnarPanelBatch:
    """`batch` with per-row `overrides` and, for named sessions, a later revision clock.

    `tests/integration/panel/test_veto_window.py`'s own helper, restated here because a helper a
    second file needs is state this file cannot import across the test subtrees and should not
    grow in the shared fixture module (its shape table is asserted on).
    """
    dates = _row_indices(batch, date_column=date_column)
    revision = tuple(
        later_revision.get(date_text, clock)
        for date_text, clock in zip(dates, batch.timeline.revision_time, strict=True)
    )
    as_of = max([batch.as_of, *revision])
    columns = tuple(
        PanelColumn(
            column.name,
            column.kind,
            tuple(
                overrides.get((subject, date_text), {}).get(column.name, value)
                for subject, date_text, value in zip(
                    batch.subjects, dates, column.values, strict=True
                )
            ),
        )
        for column in batch.columns
    )
    return ColumnarPanelBatch(
        provider_id=batch.provider_id,
        dataset=batch.dataset,
        kind=batch.kind,
        as_of=as_of,
        fetched_at=as_of,
        status="success",
        subjects=batch.subjects,
        timeline=TimelineColumns(
            event_time=batch.timeline.event_time,
            available_time=batch.timeline.available_time,
            ingested_time=batch.timeline.ingested_time,
            revision_time=revision,
        ),
        columns=columns,
    )


RESTATED_SECURITY_INDEX: Final[int] = 6
"""`panel_fixtures`' limit-streak security: the name `generate_limit_streak_panel` scripts."""


def _restate(
    store: PanelStore,
    panel: GeneratedPanel,
    *,
    day: date,
    revision_time: datetime,
    daily: Mapping[str, object],
) -> None:
    """Republish `day`'s bars and valuations with a later revision clock."""
    key = (panel.securities[RESTATED_SECURITY_INDEX], day.isoformat())
    bars = _rewritten(
        panel.batch(DAILY_DATASET),
        date_column="trade_date",
        overrides={key: daily},
        later_revision={day.isoformat(): revision_time},
    )
    valuations = _rewritten(
        panel.batch(DAILY_BASIC_DATASET),
        date_column="trade_date",
        overrides={key: {"close": daily["close"]}},
        later_revision={day.isoformat(): revision_time},
    )
    halts = load_suspensions(store, years=(panel.year,), as_of=panel.as_of, max_staleness=None)
    write_daily_panel(
        store, bars=[bars], fundamentals=[valuations], calendar=panel.calendar(), halts=halts
    )


def test_a_restatement_inside_the_veto_window_is_refused_and_writes_nothing(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """R-N3a: the vetoed set cannot be reproduced, so the window refuses and names the session."""
    panel = generate_limit_streak_panel()
    runtime = tmp_path / "runtime"
    store = PanelStore(runtime / "panel")
    write_generated_panel(store, panel)
    target = panel.sessions[6]
    earlier = panel.sessions[4]
    _restate(
        store,
        panel,
        day=earlier,
        revision_time=_published(earlier) + timedelta(hours=2),
        daily={
            "open": 12.50,
            "high": 13.35,
            "low": 12.50,
            "close": 12.90,
            "pre_close": 12.10,
            "pct_chg": (12.90 / 12.10 - 1.0) * 100.0,
        },
    )
    journal = _journal(session=target, config_id=CONFIG_ID, paper=_story_plan())
    _write_journal(runtime, CONFIG_ID, journal)
    runtime_before = _tree(runtime)
    panel_before = _tree(runtime / "panel")

    code, out, err = _invoke(
        capsys,
        [
            "--runtime-dir",
            str(runtime),
            "--config-id",
            CONFIG_ID,
            "--session",
            target.isoformat(),
        ],
    )

    assert code == 1
    assert out == ""
    assert earlier.isoformat() in err
    assert "restat" in err or "no earlier version" in err
    assert _tree(runtime) == runtime_before
    assert _tree(runtime / "panel") == panel_before


# --- the default session and the smoke banner ----------------------------------------------------


def test_the_default_session_is_the_newest_complete_journal(
    capsys: pytest.CaptureFixture[str], story: World
) -> None:
    """Without `--session` the newest *complete* day journal is annotated, not the newest file."""
    older = story.panel.sessions[10]
    _write_journal(
        story.runtime,
        story.config_id,
        _journal(session=older, config_id=story.config_id, paper=_story_plan()),
    )
    # A morning check of a later session is in the same directory and is not a day journal.
    morning = daily.survivor_journal_directory(story.runtime, story.config_id)
    morning.joinpath(f"{story.panel.sessions[11].isoformat()}.morning.json").write_text(
        "{}\n", encoding="utf-8"
    )

    document = _document(capsys, story)

    assert document["session"] == story.session.isoformat()


def test_a_smoke_registration_banners_the_annotation(
    capsys: pytest.CaptureFixture[str], story: World, tmp_path: Path
) -> None:
    """A smoke registration marks the printed form first and last and the JSON under `smoke`."""
    registration = tmp_path / "smoke-registration.json"
    registration.write_text(json.dumps(SMOKE_REGISTRATION), encoding="utf-8")
    arguments = [*_arguments(story, session=story.session), "--registration", str(registration)]

    code, out, err = _invoke(capsys, arguments)
    assert code == 0, err
    lines = out.splitlines()
    assert lines[0] == daily.SMOKE_BANNER
    assert lines[-1] == daily.SMOKE_BANNER

    code, out, err = _invoke(capsys, [*arguments, "--json"])
    assert code == 0, err
    assert json.loads(out)[daily.SMOKE_DOCUMENT_KEY] == daily.SMOKE_BANNER


# --- the import limits ---------------------------------------------------------------------------


def test_the_script_imports_no_paper_book_no_composition_build_storage_and_no_providers() -> None:
    """The annotation is a read-only print command: it cannot reach the paper book or a provider.

    "No `build_storage`" is checked by name and "no `providers`" by module, because both are one
    import away from a command that claims it sends no request and writes nothing.
    """
    script = ROOT / "scripts" / "survivor_annotate.py"
    tree = ast.parse(script.read_text(encoding="utf-8"), filename=str(script))
    modules: set[str] = set()
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.module is not None:
                modules.add(node.module)
            names.update(alias.name for alias in node.names)
    assert not [name for name in modules if name.startswith("openalpha_cn.backtest.paper")]
    assert not [name for name in modules if name.startswith("openalpha_cn.providers")]
    assert not [name for name in modules if name.startswith("openalpha_cn.runtime")]
    assert "build_storage" not in names
