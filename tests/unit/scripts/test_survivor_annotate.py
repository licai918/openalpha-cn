"""`scripts/survivor_annotate.py`: A's veto and B's factors beside the daily list.

The annotation is a **print-only** command for the human trader: it reads a finished survivor
day's journal and a clone of the panel, and prints one row per name the day's plan **acts on** --
its role in that plan, plan A's veto with the count and mean the rule was decided from, and plan
B's two limit-event factors with their percentile in the stored cross section. It writes
nothing, sends no request, admits no registration and is not bound by one (R-N2): every cell is
candidate reference, never part of the list, the paper book or the journal.

The roles come from the orders the user actually places (`paper.open_orders`, through
`plan_names`): `ENTRY` is a planned or carried buy, `EXIT` a live sell (so a carried sell is not
mistaken for a holding), `HOLD` a holding with no live sell, `WATCH` the next names the rule
would draw. The decision's `entries`/`exits` are a cross-check, not the source: a journal whose
orders and decision disagree is refused by name rather than printed with a role nobody can act
on.

The smoke banner fails on: it is derived from the journal's recorded `registration.path`, whose
`sha256` must equal the journal's, so a smoke day is bracketed with or without `--registration`;
when no registration can be resolved or verified, the first and last lines say the smoke status
is unknown rather than staying silent.

Everything here is built at test time: the panel is generated and written through the real
writers (hard rule 6), the B raw builds go through the real factor engine with the evaluator
seam planted, and the journal is a fabricated complete `openalpha-daily-survivor/v1` day. The
generated calendar is `panel_fixtures`' `SZSE` one, so the command's own exchange discovery is
exercised rather than assumed.

Three refusals carry the issue's boundaries: a session with no raw B build exits 1 and prints
the exact `openalpha factor build ...` line (review focus 3); a session inside the veto window
restated after its own 16:30 refuses by name rather than reproducing a different vetoed set
(R-N3a); and a window that reaches the start of the panel's calendar says so instead of being
blamed on a restatement.
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
    FactorEngineError,
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
CARRIED_BUY_CODE: Final[str] = "600519.SH"
CARRIED_SELL_CODE: Final[str] = "688981.SH"
"""Orders still live from an earlier evening: a BUY the user still places and a repeat SELL."""
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
EARLY_UP_VALUE: Final[float] = 9.0
"""A build stamped at noon on the session's own day: it did not know that day's close."""
PREVIOUS_UP_VALUE: Final[float] = 7.0
"""A build of the previous session, visible at the read but not this session's answer."""
NEXT_UP_VALUE: Final[float] = 8.0
"""A build stamped on the session after: it is not this session's answer either."""
SMOKE_REGISTRATION: Final[dict[str, Any]] = {"settings": {"smoke_run": {"issue": "V2-P7-019"}}}
VETO_RULE_ID: Final[str] = "market_bearish_5/v1"
"""Plan A's rule id: the journal's `paper.entry_veto.rule`, and the rule the window is read by."""
BASE_BOOK_NO_VETO_NOTE: Final[str] = (
    "该账簿不执行否决；vetoed 只是参考标注 / this book does not apply the veto; "  # noqa: RUF001
    "'vetoed' is a reference note"
)
"""The line a base (v1) journal prints directly under the header: it applies no veto (`Minor 3`)."""


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
    registration_path: Path

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


RECORDED_REGISTRATION_NAME: Final[str] = "p7-registration.json"
"""The bare name `daily_selection` records, resolved against the repository root and so usually
unresolvable from a runtime directory: the banner then says the smoke status is unknown."""


def _journal(
    *,
    session: date,
    config_id: str,
    paper: Mapping[str, Any] | None,
    opening: Mapping[str, Any] | None = None,
    registration_path: Path | None = None,
    recorded_path: str | None = None,
    recorded_sha256: str | None = None,
) -> dict[str, Any]:
    """A finished survivor day as the command files it: JSON, `result` present, wording last.

    `registration_path` records that file's absolute path and its real sha256, so the annotation
    can verify it by default; without one the journal records only the bare name the daily
    command writes, which is unresolvable and exercises the "not verified" banner.
    """
    result: dict[str, Any] = {"session": session.isoformat(), "wording": SURVIVOR_WORDING}
    if opening is not None:
        result["opening"] = dict(opening)
    else:
        result["paper"] = None if paper is None else dict(paper)
    registration: dict[str, Any] = {
        "path": (
            str(registration_path) if registration_path is not None else RECORDED_REGISTRATION_NAME
        ),
        "sha256": (
            hashlib.sha256(registration_path.read_bytes()).hexdigest()
            if registration_path is not None
            else "a" * 64
        ),
        "commit": "b" * 40,
        "config_id": config_id,
    }
    if recorded_path is not None:
        registration["path"] = recorded_path
    if recorded_sha256 is not None:
        registration["sha256"] = recorded_sha256
    return {
        "schema": daily.SURVIVOR_JOURNAL_SCHEMA,
        "session": session.isoformat(),
        "as_of": _evening(session).isoformat(),
        "held": [],
        "held_from": None,
        "registration": registration,
        "panel": {"caught_up": []},
        "result": result,
    }


def _plan(
    *,
    entries: Sequence[str] = (),
    exits: Sequence[str] = (),
    holdings: Sequence[str] = (),
    watch: Sequence[str] = (),
    buys: Sequence[str] = (),
    carried_buys: Sequence[str] = (),
    sells: Sequence[str] | None = None,
    carried_sells: Sequence[str] = (),
) -> dict[str, Any]:
    """The `result.paper` block the annotation reads: the orders, the book, and the decision.

    `sells` defaults to `exits` -- the evening's decided sells are the sell orders it planned --
    and the two carried lists are orders still live from earlier evenings, which the decision
    lists do not mention.
    """
    decided_sells = tuple(exits) if sells is None else tuple(sells)
    orders = (
        [{"ts_code": code, "side": "buy", "carried": False} for code in buys]
        + [{"ts_code": code, "side": "buy", "carried": True} for code in carried_buys]
        + [{"ts_code": code, "side": "sell", "carried": False} for code in decided_sells]
        + [{"ts_code": code, "side": "sell", "carried": True} for code in carried_sells]
    )
    return {
        "book": {"holdings": [{"ts_code": code} for code in holdings]},
        "open_orders": orders,
        "decision": {
            "exits": [{"ts_code": code, "reason": "lost_survivor"} for code in exits],
            "entries": [{"ts_code": code, "reason": "INITIAL_BUILD"} for code in entries],
            "watch_list": list(watch),
        },
    }


def _story_plan() -> dict[str, Any]:
    """A plan with every role, including the two orders carried over from earlier evenings."""
    return _plan(
        entries=(ENTRY_CODE,),
        exits=(EXIT_CODE,),
        holdings=(EXIT_CODE, CARRIED_SELL_CODE, HOLD_VETOED_CODE, HOLD_PLAIN_CODE),
        watch=(WATCH_CODE,),
        buys=(ENTRY_CODE,),
        carried_buys=(CARRIED_BUY_CODE,),
        carried_sells=(CARRIED_SELL_CODE,),
    )


def _veto_plan(*, vetoed: Sequence[str]) -> dict[str, Any]:
    """`_story_plan` as a v2 book's paper block: it carries the `entry_veto` block it filed.

    The block is the one `strategy_survivor_summary._paper_section` writes: the rule id and the
    survivors the window vetoed, sorted. Only a v2 book files it; a v1 book's paper block has no
    such key and prints the base-book note instead.
    """
    plan = _story_plan()
    plan["entry_veto"] = {"rule": VETO_RULE_ID, "vetoed": list(vetoed)}
    return plan


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
    registration = root / "registration.json"
    registration.write_text(json.dumps({"settings": {}}), encoding="utf-8")
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
    # The three builds that must not be this session's answer: before its close, on the day
    # before, and on the day after.
    early = _build(
        store,
        panel,
        LIMIT_UP_COUNT_20,
        as_of=datetime(session.year, session.month, session.day, 12, 0, tzinfo=SHANGHAI),
        subjects=subjects,
        values=lambda code: EARLY_UP_VALUE,
    )
    previous = _build(
        store,
        panel,
        LIMIT_UP_COUNT_20,
        as_of=_published(panel.sessions[TARGET_INDEX - 1]),
        subjects=subjects,
        values=lambda code: PREVIOUS_UP_VALUE,
    )
    following = _build(
        store,
        panel,
        LIMIT_UP_COUNT_20,
        as_of=_published(panel.sessions[TARGET_INDEX + 1]),
        subjects=subjects,
        values=lambda code: NEXT_UP_VALUE,
    )
    write_factor_panels(store, [limit_up, broken, rebuilt, early, previous, following])
    journal = _journal(
        session=session,
        config_id=CONFIG_ID,
        paper=_story_plan(),
        registration_path=registration,
    )
    path = _write_journal(runtime, CONFIG_ID, journal)
    return World(
        root=root,
        panel=panel,
        store=store,
        runtime=runtime,
        config_id=CONFIG_ID,
        session=session,
        journal_path=path,
        registration_path=registration,
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
        registration_path=root / "registration.json",
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
    """Every role appears; each name once, and a carried order is a row like any other.

    The exit names are also in the book's holdings -- a sale is still held until the open -- so
    the rows prove the role precedence rather than reading the book twice.
    """
    document = _document(capsys, story)

    assert document["kind"] == "survivor-annotation/v1"
    assert document["session"] == story.session.isoformat()
    assert document["config_id"] == CONFIG_ID
    assert document["wording"] == SURVIVOR_WORDING
    roles = [row["role"] for row in document["rows"]]
    assert roles == ["ENTRY", "ENTRY", "EXIT", "EXIT", "HOLD", "HOLD", "WATCH"]
    assert set(roles) == {"ENTRY", "EXIT", "HOLD", "WATCH"}
    assert [row["ts_code"] for row in document["rows"]] == [
        ENTRY_CODE,
        CARRIED_BUY_CODE,
        EXIT_CODE,
        CARRIED_SELL_CODE,
        HOLD_PLAIN_CODE,
        HOLD_VETOED_CODE,
        WATCH_CODE,
    ]
    assert "smoke" not in document


def test_a_carried_buy_and_a_carried_sell_are_both_rows_with_their_roles(
    capsys: pytest.CaptureFixture[str], story: World
) -> None:
    """The orders still live from earlier evenings are on the list the user acts on.

    Neither is in the evening's decision: a carried buy is not a new entry and a carried sell is
    not a new exit, so a role read off `decision.entries`/`decision.exits` would miss the buy and
    call the sell a `HOLD`.
    """
    document = _document(capsys, story)
    rows = _rows(document)
    journal = json.loads(story.journal_path.read_text(encoding="utf-8"))
    decision = journal["result"]["paper"]["decision"]
    decided_entries = {item["ts_code"] for item in decision["entries"]}
    decided_exits = {item["ts_code"] for item in decision["exits"]}
    assert CARRIED_BUY_CODE not in decided_entries
    assert CARRIED_SELL_CODE not in decided_exits

    assert rows[CARRIED_BUY_CODE]["role"] == "ENTRY"
    assert rows[CARRIED_SELL_CODE]["role"] == "EXIT"
    assert CARRIED_SELL_CODE not in {
        row["ts_code"] for row in document["rows"] if row["role"] == "HOLD"
    }


def test_a_journal_whose_orders_contradict_its_decision_is_refused_by_name(
    capsys: pytest.CaptureFixture[str], story: World
) -> None:
    """The decision is a cross-check: an order it does not name refuses rather than mis-roles."""
    plan = _story_plan()
    plan["open_orders"].append({"ts_code": HOLD_PLAIN_CODE, "side": "buy", "carried": False})
    config_id = "cfg-annotation-mismatch1"
    _write_journal(
        story.runtime,
        config_id,
        _journal(
            session=story.session,
            config_id=config_id,
            paper=plan,
            registration_path=story.registration_path,
        ),
    )

    code, out, err = _invoke(capsys, _arguments(story, session=story.session, config_id=config_id))
    assert code == 1
    assert out == ""
    assert "disagree" in err
    assert HOLD_PLAIN_CODE in err

    missing_sell = _story_plan()
    missing_sell["open_orders"] = [
        order
        for order in missing_sell["open_orders"]
        if not (order["side"] == "sell" and order["ts_code"] == EXIT_CODE)
    ]
    config_id = "cfg-annotation-mismatch2"
    _write_journal(
        story.runtime,
        config_id,
        _journal(
            session=story.session,
            config_id=config_id,
            paper=missing_sell,
            registration_path=story.registration_path,
        ),
    )
    code, out, err = _invoke(capsys, _arguments(story, session=story.session, config_id=config_id))
    assert code == 1
    assert out == ""
    assert "disagree" in err
    assert EXIT_CODE in err


def test_the_annotation_prints_the_header_every_role_and_the_journal_wording(
    capsys: pytest.CaptureFixture[str], story: World
) -> None:
    """The printed form: the candidate-reference header, one line per row, the wording last.

    The story journal is a base (v1) book's -- it carries no `paper.entry_veto` -- so the base-book
    note comes directly under the header (`Minor 3`); the rows follow it unchanged.
    """
    code, out, err = _invoke(capsys, _arguments(story, session=story.session))

    assert code == 0, err
    assert err == ""
    lines = out.splitlines()
    assert lines[0] == annotate.HEADER
    assert lines[1] == BASE_BOOK_NO_VETO_NOTE
    assert lines[-1] == SURVIVOR_WORDING
    body = lines[2:-1]
    assert len(body) == 7
    by_code = {line.split()[1]: line for line in body}
    assert by_code[ENTRY_CODE].startswith(f"ENTRY {ENTRY_CODE} | veto: none")
    assert by_code[CARRIED_BUY_CODE].startswith(f"ENTRY {CARRIED_BUY_CODE} | veto: none")
    assert by_code[EXIT_CODE].startswith(f"EXIT {EXIT_CODE} | veto: none")
    assert by_code[CARRIED_SELL_CODE].startswith(f"EXIT {CARRIED_SELL_CODE} | veto: none")
    vetoed = by_code[HOLD_VETOED_CODE]
    assert vetoed.startswith(f"HOLD {HOLD_VETOED_CODE} | veto: market_bearish_5/v1, vetoed")
    assert "events 1" in vetoed
    assert "mean -0.6500" in vetoed
    assert by_code[WATCH_CODE].startswith(f"WATCH {WATCH_CODE} | veto: none")
    assert "limit_up_count_20: not in the build" in by_code[WATCH_CODE]
    assert "broken_board_count_20: not in the build" in by_code[WATCH_CODE]
    assert "limit_up_count_20: 0.0000 (pct 0.500 of 7)" in by_code[ENTRY_CODE]


def test_a_base_v1_journal_says_the_book_applies_no_veto(
    capsys: pytest.CaptureFixture[str], story: World
) -> None:
    """A journal whose paper block has no `entry_veto` is a v1 book: it applies no veto.

    The note is exactly one line directly under the header -- the header stays first -- and the
    `--json` document carries the same fact as `book_applies_entry_veto: false`, so the printer
    stays a pure function of the document.
    """
    config_id = "cfg-annotation-basebook1"
    _write_journal(
        story.runtime,
        config_id,
        _journal(
            session=story.session,
            config_id=config_id,
            paper=_story_plan(),
            registration_path=story.registration_path,
        ),
    )

    code, out, err = _invoke(capsys, _arguments(story, session=story.session, config_id=config_id))
    assert code == 0, err
    lines = out.splitlines()
    assert lines[0] == annotate.HEADER
    assert lines[1] == BASE_BOOK_NO_VETO_NOTE
    assert lines.count(BASE_BOOK_NO_VETO_NOTE) == 1
    assert lines[-1] == SURVIVOR_WORDING
    assert lines[2].startswith("ENTRY ")
    assert len(lines) == 10  # header, the note, seven rows, the wording

    document = json.loads(
        _invoke(
            capsys,
            _arguments(story, session=story.session, config_id=config_id, json_output=True),
        )[1]
    )
    assert document["book_applies_entry_veto"] is False
    assert annotate.BASE_BOOK_NO_VETO_NOTE == BASE_BOOK_NO_VETO_NOTE


def test_a_v2_journal_prints_no_base_book_note_and_says_the_book_applies_the_veto(
    capsys: pytest.CaptureFixture[str], story: World
) -> None:
    """A journal that files `paper.entry_veto` is a v2 book: no base-book note is printed.

    Its document says `book_applies_entry_veto: true`; the printed first line stays the header and
    the first body line is a row, not a note.
    """
    config_id = "cfg-annotation-v2book001"
    _write_journal(
        story.runtime,
        config_id,
        _journal(
            session=story.session,
            config_id=config_id,
            paper=_veto_plan(vetoed=[HOLD_VETOED_CODE]),
            registration_path=story.registration_path,
        ),
    )

    code, out, err = _invoke(capsys, _arguments(story, session=story.session, config_id=config_id))
    assert code == 0, err
    lines = out.splitlines()
    assert lines[0] == annotate.HEADER
    assert BASE_BOOK_NO_VETO_NOTE not in out
    assert lines[1].startswith("ENTRY ")
    assert lines[-1] == SURVIVOR_WORDING

    code, out, err = _invoke(
        capsys,
        _arguments(story, session=story.session, config_id=config_id, json_output=True),
    )
    assert code == 0, err
    assert json.loads(out)["book_applies_entry_veto"] is True


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


# --- Minor 4: the journal's vetoed list against the window the annotation recomputes ------------


def test_a_v2_journals_vetoed_list_agrees_with_the_window(
    capsys: pytest.CaptureFixture[str], story: World
) -> None:
    """A v2 journal's `paper.entry_veto.vetoed` names only names the window vetoes: it prints.

    The story panel's scripted broken board vetoes exactly `HOLD_VETOED_CODE`, so a journal whose
    list is that name is consistent with the read and the annotation prints normally.
    """
    config_id = "cfg-annotation-veto-agree1"
    _write_journal(
        story.runtime,
        config_id,
        _journal(
            session=story.session,
            config_id=config_id,
            paper=_veto_plan(vetoed=[HOLD_VETOED_CODE]),
            registration_path=story.registration_path,
        ),
    )

    code, out, err = _invoke(capsys, _arguments(story, session=story.session, config_id=config_id))

    assert code == 0, err
    assert err == ""
    lines = out.splitlines()
    assert lines[0] == annotate.HEADER
    assert BASE_BOOK_NO_VETO_NOTE not in out
    assert any(HOLD_VETOED_CODE in line and "vetoed" in line for line in lines)


def test_a_v2_journals_vetoed_list_the_window_does_not_veto_is_refused_by_name(
    capsys: pytest.CaptureFixture[str], story: World
) -> None:
    """A name the journal records as vetoed that the window does not veto refuses (exit 2).

    The journal does not store the survivor names, so the check is one-way: every name the journal
    records as a vetoed survivor must be one the recomputation vetoes. `HOLD_PLAIN_CODE` is not
    vetoed by the window (it has no event at all), so it is the difference the refusal lists; no
    annotation is printed and the runtime and panel trees are byte-identical.
    """
    config_id = "cfg-annotation-veto-disagree1"
    _write_journal(
        story.runtime,
        config_id,
        _journal(
            session=story.session,
            config_id=config_id,
            paper=_veto_plan(vetoed=[HOLD_VETOED_CODE, HOLD_PLAIN_CODE]),
            registration_path=story.registration_path,
        ),
    )
    runtime_before = _tree(story.runtime)
    panel_before = _tree(story.panel_directory)

    code, out, err = _invoke(capsys, _arguments(story, session=story.session, config_id=config_id))

    assert code == 2
    assert out == ""
    assert HOLD_PLAIN_CODE in err
    assert VETO_RULE_ID in err
    assert "disagree" in err
    assert HOLD_VETOED_CODE not in err  # only the difference names are listed
    assert "nothing was written" in err
    assert _tree(story.runtime) == runtime_before
    assert _tree(story.panel_directory) == panel_before


@pytest.mark.parametrize(
    "block",
    [
        pytest.param({"vetoed": [HOLD_VETOED_CODE]}, id="no-rule"),
        pytest.param({"rule": 7, "vetoed": [HOLD_VETOED_CODE]}, id="rule-not-a-string"),
        pytest.param({"rule": VETO_RULE_ID, "vetoed": "600000.SH"}, id="vetoed-not-a-list"),
    ],
)
def test_an_unreadable_entry_veto_block_is_refused_as_a_journal_fault(
    capsys: pytest.CaptureFixture[str],
    story: World,
    request: pytest.FixtureRequest,
    block: Mapping[str, Any],
) -> None:
    """A malformed block is an ordinary journal fault (exit 1), not a veto disagreement (exit 2)."""
    config_id = f"cfg-annotation-veto-shape-{request.node.callspec.id}"
    plan = _story_plan()
    plan["entry_veto"] = block
    _write_journal(
        story.runtime,
        config_id,
        _journal(
            session=story.session,
            config_id=config_id,
            paper=plan,
            registration_path=story.registration_path,
        ),
    )

    code, out, err = _invoke(capsys, _arguments(story, session=story.session, config_id=config_id))

    assert code == 1
    assert out == ""
    assert "entry_veto" in err


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


def test_a_build_stamped_before_the_sessions_close_is_not_a_session_answer(
    capsys: pytest.CaptureFixture[str], story: World
) -> None:
    """A noon build on the session's own day did not know the close, so it answers nothing."""
    row = _rows(_document(capsys, story))[ENTRY_CODE]

    assert row["limit_up_count_20"]["value"] == 0.0
    assert row["limit_up_count_20"]["value"] != EARLY_UP_VALUE


def test_a_build_stamped_on_the_previous_session_is_not_this_sessions_answer(
    capsys: pytest.CaptureFixture[str], story: World
) -> None:
    """An earlier session's build is visible at the read and must still not answer this one."""
    row = _rows(_document(capsys, story))[ENTRY_CODE]

    assert row["limit_up_count_20"]["value"] == 0.0
    assert row["limit_up_count_20"]["value"] != PREVIOUS_UP_VALUE


def test_a_build_stamped_on_the_next_session_is_not_shown(
    capsys: pytest.CaptureFixture[str], story: World
) -> None:
    """A build published on the following day cannot substitute for this session's."""
    row = _rows(_document(capsys, story))[ENTRY_CODE]

    assert row["limit_up_count_20"]["value"] == 0.0
    assert row["limit_up_count_20"]["value"] != NEXT_UP_VALUE


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


def test_a_registered_year_with_no_build_for_the_session_is_refused_with_the_build_command(
    capsys: pytest.CaptureFixture[str], story: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Registered for the factor dataset, but no build answers this earlier session: build it.

    The story panel has B's raw builds for the fixture's session only. An earlier session of the
    same year passes the "year is registered" check and then finds no build stamped on its own day
    at or after its 16:30, which is the second, distinct missing-build branch: exit 1 with the same
    exact `openalpha factor build ...` line the bare fixture's first branch prints. The spy proves
    the read happened -- the first branch (year not registered) would have refused before it.
    """
    session = story.panel.sessions[TARGET_INDEX - 3]
    config_id = "cfg-annotation-nobuild-for-day"
    _write_journal(
        story.runtime,
        config_id,
        _journal(
            session=session,
            config_id=config_id,
            paper=_story_plan(),
            registration_path=story.registration_path,
        ),
    )
    reads: list[str] = []
    original = annotate.load_factor_observations

    def _counting(store: Any, definition: FactorDefinition, **kwargs: Any) -> Any:
        reads.append(definition.qualified_key)
        return original(store, definition, **kwargs)

    monkeypatch.setattr(annotate, "load_factor_observations", _counting)

    code, out, err = _invoke(capsys, _arguments(story, session=session, config_id=config_id))

    assert code == 1
    assert out == ""
    assert reads  # the year was registered and read; the refusal is the empty build set
    assert "the panel holds no raw build" in err
    assert err.splitlines()[-1] == (
        "openalpha factor build --factor limit_up_count_20/v1 --tier raw "
        f"--year {session.year} "
        f"--as-of {_published(session).isoformat()} "
        "--max-staleness-days <days>"
    )


def test_a_factor_partition_the_panel_cannot_read_is_not_reported_as_a_missing_build(
    capsys: pytest.CaptureFixture[str],
    story: World,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A registered year whose partition cannot be read says so, and is not a missing build.

    The category sentence is the assertion, not the engine's own text: the remedy for a damaged
    partition is not `openalpha factor build`, so that line must not be printed.
    """

    def _unreadable(*args: Any, **kwargs: Any) -> Any:
        raise FactorEngineError("a broken partition")

    monkeypatch.setattr(annotate, "load_factor_observations", _unreadable)

    code, out, err = _invoke(capsys, _arguments(story, session=story.session))

    assert code == 1
    assert out == ""
    assert "cannot read them at" in err
    assert "openalpha factor build" not in err


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


def test_a_window_reaching_the_calendar_start_names_the_calendar(
    capsys: pytest.CaptureFixture[str], bare: World
) -> None:
    """A January session whose predecessors the panel does not store says so, not "restated"."""
    session = bare.panel.sessions[0]  # 2026-01-05: the first session this panel stores
    config_id = "cfg-annotation-january"
    _write_journal(
        bare.runtime,
        config_id,
        _journal(session=session, config_id=config_id, paper=_story_plan()),
    )

    code, out, err = _invoke(capsys, _arguments(bare, session=session, config_id=config_id))

    assert code == 1
    assert out == ""
    assert "start of the calendar" in err
    assert "five-session window" in err
    assert "2025 trade_cal" in err
    assert "restat" not in err


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


def test_a_smoke_journal_banners_the_annotation_without_the_flag(
    capsys: pytest.CaptureFixture[str], story: World, tmp_path: Path
) -> None:
    """The banner is derived from the journal's own registration, not only from the flag."""
    registration = tmp_path / "smoke-registration.json"
    registration.write_text(json.dumps(SMOKE_REGISTRATION), encoding="utf-8")
    config_id = "cfg-annotation-smoke01"
    _write_journal(
        story.runtime,
        config_id,
        _journal(
            session=story.session,
            config_id=config_id,
            paper=_story_plan(),
            registration_path=registration,
        ),
    )

    code, out, err = _invoke(capsys, _arguments(story, session=story.session, config_id=config_id))
    assert code == 0, err
    lines = out.splitlines()
    assert lines[0] == daily.SMOKE_BANNER
    assert lines[-1] == daily.SMOKE_BANNER

    code, out, err = _invoke(
        capsys,
        _arguments(story, session=story.session, config_id=config_id, json_output=True),
    )
    assert code == 0, err
    assert json.loads(out)[daily.SMOKE_DOCUMENT_KEY] == daily.SMOKE_BANNER


def test_the_registration_flag_must_match_the_journals_sha256(
    capsys: pytest.CaptureFixture[str], story: World, tmp_path: Path
) -> None:
    """An override that is a different file is a usage error, not a silent banner source."""
    other = tmp_path / "another-registration.json"
    other.write_text(
        json.dumps({"settings": {}, "note": "a different registration"}), encoding="utf-8"
    )
    config_id = "cfg-annotation-smoke02"
    _write_journal(
        story.runtime,
        config_id,
        _journal(
            session=story.session,
            config_id=config_id,
            paper=_story_plan(),
            registration_path=story.registration_path,
        ),
    )

    with pytest.raises(SystemExit) as raised:
        annotate.main(
            [
                *_arguments(story, session=story.session, config_id=config_id),
                "--registration",
                str(other),
            ]
        )
    assert raised.value.code == 2
    err = capsys.readouterr().err
    assert "sha256" in err
    assert str(other) in err


def test_an_unresolvable_registration_prints_the_not_verified_line(
    capsys: pytest.CaptureFixture[str], story: World
) -> None:
    """No registration can be resolved or verified: the banner fails on, saying it is unknown."""
    config_id = "cfg-annotation-unknown1"
    _write_journal(
        story.runtime,
        config_id,
        _journal(session=story.session, config_id=config_id, paper=_story_plan()),
    )

    code, out, err = _invoke(capsys, _arguments(story, session=story.session, config_id=config_id))
    assert code == 0, err
    lines = out.splitlines()
    assert lines[0] == annotate.UNVERIFIED_SMOKE_BANNER
    assert lines[-1] == annotate.UNVERIFIED_SMOKE_BANNER

    code, out, err = _invoke(
        capsys,
        _arguments(story, session=story.session, config_id=config_id, json_output=True),
    )
    assert code == 0, err
    assert json.loads(out)[daily.SMOKE_DOCUMENT_KEY] == annotate.UNVERIFIED_SMOKE_BANNER


# --- parked: a relative recorded name resolved under the repository's docs/research --------


def _lookup_journal(
    story: World,
    monkeypatch: pytest.MonkeyPatch,
    *,
    config_id: str,
    repository: Path,
    recorded_sha256: str | None = None,
) -> str:
    """Write a journal recording only the bare registration name, resolved under `repository`.

    `daily_selection` records the registration's `path.name`; the repository the annotation
    resolves it from is monkeypatched here, so the real `docs/research/` is never touched.
    """
    monkeypatch.setattr(annotate, "REPOSITORY", repository)
    _write_journal(
        story.runtime,
        config_id,
        _journal(
            session=story.session,
            config_id=config_id,
            paper=_story_plan(),
            recorded_sha256=recorded_sha256,
        ),
    )
    return config_id


def test_a_recorded_name_found_once_with_a_matching_sha_verifies_the_registration(
    capsys: pytest.CaptureFixture[str],
    story: World,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exactly one file of the recorded name under docs/research, sha256 equal: no banner.

    The registration is not a smoke one, so a verified run prints the header first and its wording
    last -- not the "not verified" banner.
    """
    repository = tmp_path / "repo"
    registration = repository / "docs" / "research" / "p7-registration.json"
    registration.parent.mkdir(parents=True)
    registration.write_text(json.dumps({"settings": {}}), encoding="utf-8")
    config_id = _lookup_journal(
        story,
        monkeypatch,
        config_id="cfg-annotation-found-once",
        repository=repository,
        recorded_sha256=hashlib.sha256(registration.read_bytes()).hexdigest(),
    )

    code, out, err = _invoke(capsys, _arguments(story, session=story.session, config_id=config_id))

    assert code == 0, err
    lines = out.splitlines()
    assert lines[0] == annotate.HEADER
    assert lines[-1] == SURVIVOR_WORDING
    assert annotate.UNVERIFIED_SMOKE_BANNER not in out
    assert daily.SMOKE_BANNER not in out


def test_a_recorded_name_found_once_as_a_smoke_file_prints_the_smoke_banner(
    capsys: pytest.CaptureFixture[str],
    story: World,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A found-and-verified smoke registration brackets the print, first and last."""
    repository = tmp_path / "repo"
    registration = repository / "docs" / "research" / "p7-registration.json"
    registration.parent.mkdir(parents=True)
    registration.write_text(json.dumps(SMOKE_REGISTRATION), encoding="utf-8")
    config_id = _lookup_journal(
        story,
        monkeypatch,
        config_id="cfg-annotation-found-smoke",
        repository=repository,
        recorded_sha256=hashlib.sha256(registration.read_bytes()).hexdigest(),
    )

    code, out, err = _invoke(capsys, _arguments(story, session=story.session, config_id=config_id))

    assert code == 0, err
    lines = out.splitlines()
    assert lines[0] == daily.SMOKE_BANNER
    assert lines[-1] == daily.SMOKE_BANNER


def test_a_recorded_name_found_nowhere_keeps_the_unverified_banner(
    capsys: pytest.CaptureFixture[str],
    story: World,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Zero matches: the smoke status stays unknown, with the existing banner unchanged."""
    repository = tmp_path / "repo"
    repository.mkdir()
    config_id = _lookup_journal(
        story, monkeypatch, config_id="cfg-annotation-found-none", repository=repository
    )

    code, out, err = _invoke(capsys, _arguments(story, session=story.session, config_id=config_id))

    assert code == 0, err
    lines = out.splitlines()
    assert lines[0] == annotate.UNVERIFIED_SMOKE_BANNER
    assert lines[-1] == annotate.UNVERIFIED_SMOKE_BANNER


def test_a_recorded_name_found_more_than_once_keeps_the_unverified_banner(
    capsys: pytest.CaptureFixture[str],
    story: World,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two files of the recorded name in different subdirectories: ambiguous, so unknown."""
    repository = tmp_path / "repo"
    for subdirectory in ("first", "second"):
        path = repository / "docs" / "research" / subdirectory / "p7-registration.json"
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps({"settings": {}}), encoding="utf-8")
    config_id = _lookup_journal(
        story, monkeypatch, config_id="cfg-annotation-found-twice", repository=repository
    )

    code, out, err = _invoke(capsys, _arguments(story, session=story.session, config_id=config_id))

    assert code == 0, err
    lines = out.splitlines()
    assert lines[0] == annotate.UNVERIFIED_SMOKE_BANNER
    assert lines[-1] == annotate.UNVERIFIED_SMOKE_BANNER


def test_a_recorded_name_found_once_with_a_different_sha_says_why_it_is_unverified(
    capsys: pytest.CaptureFixture[str],
    story: World,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One match, sha256 different: the unverified banner stays but explains the mismatch."""
    repository = tmp_path / "repo"
    registration = repository / "docs" / "research" / "p7-registration.json"
    registration.parent.mkdir(parents=True)
    registration.write_text(json.dumps(SMOKE_REGISTRATION), encoding="utf-8")
    config_id = _lookup_journal(
        story,
        monkeypatch,
        config_id="cfg-annotation-found-mismatch",
        repository=repository,
        recorded_sha256="c" * 64,
    )

    code, out, err = _invoke(capsys, _arguments(story, session=story.session, config_id=config_id))

    assert code == 0, err
    lines = out.splitlines()
    assert lines[-1] == lines[0]
    assert lines[0].startswith(annotate.UNVERIFIED_SMOKE_BANNER)
    assert "sha256" in lines[0]
    assert str(registration) in lines[0]
    assert daily.SMOKE_BANNER not in out


def test_the_registration_flag_verifies_a_recorded_path_that_cannot_be_resolved(
    capsys: pytest.CaptureFixture[str], story: World, tmp_path: Path
) -> None:
    """The override supplies the file; the journal's sha256 still decides whether it is the one."""
    registration = tmp_path / "smoke-registration.json"
    registration.write_text(json.dumps(SMOKE_REGISTRATION), encoding="utf-8")
    config_id = "cfg-annotation-smoke03"
    _write_journal(
        story.runtime,
        config_id,
        _journal(
            session=story.session,
            config_id=config_id,
            paper=_story_plan(),
            recorded_path="(not resolvable)/registration.json",
            recorded_sha256=hashlib.sha256(registration.read_bytes()).hexdigest(),
        ),
    )

    code, out, err = _invoke(
        capsys,
        [
            *_arguments(story, session=story.session, config_id=config_id),
            "--registration",
            str(registration),
        ],
    )
    assert code == 0, err
    lines = out.splitlines()
    assert lines[0] == daily.SMOKE_BANNER
    assert lines[-1] == daily.SMOKE_BANNER


# --- the import limits ---------------------------------------------------------------------------


def test_the_script_directly_imports_no_paper_book_no_composition_build_storage_or_providers() -> (
    None
):
    """The annotation's own imports reach neither the paper book nor a provider.

    "Directly" is the whole claim, and the audit finding this test's name records: the script
    reuses `daily_selection`, and importing that loads `openalpha_cn.providers*` and
    `openalpha_cn.runtime.composition` transitively. That import is code, not an action; the
    runtime test below replaces those modules' construction and request entry points with
    tripwires and runs a whole annotation with none of them called.
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


def test_a_full_annotation_calls_no_paper_book_storage_and_no_provider(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    story: World,
) -> None:
    """The transitive imports are never used: each entry point raises if the annotation calls it."""
    from openalpha_cn.providers import akshare, chainlin, tushare
    from openalpha_cn.providers import file as file_provider
    from openalpha_cn.runtime import composition

    calls: list[str] = []

    def _tripwire(name: str) -> Callable[..., Any]:
        def called(*args: Any, **kwargs: Any) -> Any:
            calls.append(name)
            raise AssertionError(f"the annotation called {name}")

        return called

    monkeypatch.setattr(
        composition, "build_storage", _tripwire("runtime.composition.build_storage")
    )
    monkeypatch.setattr(daily, "build_storage", _tripwire("daily_selection.build_storage"))
    monkeypatch.setattr(tushare.TushareProvider, "__init__", _tripwire("TushareProvider"))
    monkeypatch.setattr(
        tushare.UrllibTushareTransport, "__init__", _tripwire("UrllibTushareTransport")
    )
    monkeypatch.setattr(
        tushare.UrllibTushareTransport, "post", _tripwire("UrllibTushareTransport.post")
    )
    monkeypatch.setattr(file_provider.FileProvider, "__init__", _tripwire("FileProvider"))
    monkeypatch.setattr(
        chainlin.ChainLinDataProvider, "__init__", _tripwire("ChainLinDataProvider")
    )
    monkeypatch.setattr(
        chainlin.UrllibChainLinTransport,
        "get_json",
        _tripwire("UrllibChainLinTransport.get_json"),
    )
    monkeypatch.setattr(akshare.AKShareProvider, "__init__", _tripwire("AKShareProvider"))

    code, out, err = _invoke(capsys, _arguments(story, session=story.session))

    assert code == 0, err
    assert out
    assert calls == []
