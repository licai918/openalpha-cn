"""`--show-book`: the paper book's own state, read only (`V2-P7-006a`).

The world is `test_daily_selection_smoke_banner.py`'s: a seeded panel and a committed survivor
registration. Two evenings run through `main` -- 6 April opens the book, 7 April fills its plan --
so the runtime directory's head snapshot holds names with lots and marks. Nothing here reaches the
network, and nothing in the runtime directory changes.
"""

from __future__ import annotations

import contextlib
import io
import json
import shutil
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Final

import pytest
import test_daily_selection as tds
import test_daily_selection_survivor_day as survivor_day
import test_forward_report_survivor as survivor_report
from test_daily_selection_survivor_day import seeded  # noqa: F401  (a module fixture, reused)

from openalpha_cn import strategy_survivor_summary
from openalpha_cn.backtest.paper_state import held_head
from openalpha_cn.storage.paper_state import FilePaperStateStore

daily = tds.daily

NEXT_AS_OF: Final[datetime] = datetime.fromisoformat("2026-04-07T18:30:00+08:00")
"""7 April's evening: it settles the plan 6 April's evening left for its open."""


def _tree(root: Path) -> dict[str, bytes]:
    return {
        entry.relative_to(root).as_posix(): entry.read_bytes()
        for entry in sorted(root.rglob("*"))
        if entry.is_file()
    }


def _evening(world: tds.World, day: date, as_of: datetime) -> None:
    """Run `day`'s evening through `main`, pinned at `as_of`."""
    world.market.today = day
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = daily.main(
            [
                "--runtime-dir",
                str(world.runtime),
                "--registration",
                str(world.registration),
                "--repo",
                str(world.repo),
                "--as-of",
                as_of.isoformat(),
                "--json",
            ],
            clock=lambda: as_of,
        )
    assert code == 0, err.getvalue()


def _held_world(root: Path, seeded_runtime: Path) -> tds.World:
    """The panel through 3 April and a committed survivor registration, then two evenings: the
    book opens on 6 April and its plan fills at 7 April's open, so the head holds names."""
    repo, registration = survivor_report._repository(
        root, survivor_day.CONFIG, settings=survivor_report.settings_with()
    )
    runtime = root / "runtime"
    shutil.copytree(seeded_runtime, runtime)
    world = tds.World(
        repo=repo,
        registration=registration,
        runtime=runtime,
        market=survivor_day.SurvivorMarket(open_days=tds.OPEN_2026),
    )
    with pytest.MonkeyPatch.context() as patch:
        tds._bind(patch, world.repo, world.market)
        _evening(world, survivor_day.DAY, survivor_day.DAY_AS_OF)
        _evening(world, survivor_day.NEXT, NEXT_AS_OF)
    return world


@pytest.fixture(scope="module")
def held_evening(tmp_path_factory: pytest.TempPathFactory, seeded: Path) -> tds.World:  # noqa: F811
    """A completed paper book holding the first evening's plan, run once through `main`."""
    return _held_world(tmp_path_factory.mktemp("held-book"), seeded)


@pytest.fixture
def world(held_evening: tds.World, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tds.World:
    """A copy of the held book's runtime directory, under its registration."""
    runtime = tmp_path / "runtime"
    shutil.copytree(held_evening.runtime, runtime)
    tds._bind(monkeypatch, held_evening.repo, held_evening.market)
    return tds.World(
        repo=held_evening.repo,
        registration=held_evening.registration,
        runtime=runtime,
        market=held_evening.market,
    )


def _show(
    world: tds.World,
    *extra: str,
    as_json: bool = True,
    registration: Path | None = None,
) -> tuple[int, str, list[str]]:
    arguments = [
        "--runtime-dir",
        str(world.runtime),
        "--registration",
        str(world.registration if registration is None else registration),
        "--show-book",
    ]
    if as_json:
        arguments.append("--json")
    arguments.extend(extra)
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = daily.main(arguments)
    return code, out.getvalue(), err.getvalue().splitlines()


def _smoke_registration(tmp_path: Path, registration: Path) -> Path:
    """The held book's registration, marked a smoke run: the marker alone decides the banner."""
    body = json.loads(registration.read_text(encoding="utf-8"))
    body.setdefault("settings", {})["smoke_run"] = {"issue": "V2-P7-019"}
    path = tmp_path / "smoke-registration.json"
    path.write_text(json.dumps(body, sort_keys=True), encoding="utf-8")
    return path


def test_show_book_refuses_a_runtime_directory_with_no_book_and_creates_nothing(
    tmp_path: Path,
) -> None:
    """No snapshot is not an empty book: the view is refused by name, and not even the runtime
    directory is created."""
    runtime = tmp_path / "runtime"

    out = io.StringIO()
    err = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = daily.main(["--runtime-dir", str(runtime), "--show-book"])

    assert code == 1
    assert out.getvalue() == ""
    assert "no paper book snapshot is held at all" in err.getvalue()
    assert not runtime.exists()


def test_show_book_lists_the_books_holdings_from_its_own_state_and_writes_nothing(
    world: tds.World, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every figure is the head snapshot's own -- cash, lots and their cost basis, the last mark --
    and the read changes no byte, creates nothing and asks the network nothing."""
    snapshot = held_head(FilePaperStateStore(world.runtime / "paper" / "snapshots"))
    assert snapshot is not None and snapshot.book.positions, "the second evening's book holds names"
    before = _tree(world.runtime)

    def refused(*args: object, **kwargs: object) -> None:
        raise AssertionError("--show-book made a request or ran a command")

    monkeypatch.setattr(daily, "invoke", refused)
    monkeypatch.setattr(daily.subprocess, "run", refused)

    code, out, err = _show(world)

    assert code == 0 and err == []
    view = json.loads(out)
    assert view["kind"] == daily.BOOK_VIEW_KIND
    assert (view["session"], view["session_index"]) == (
        snapshot.session.isoformat(),
        snapshot.session_index,
    )
    assert (view["snapshot_id"], view["config_digest"]) == (
        snapshot.snapshot_id,
        snapshot.config_digest,
    )
    assert view["cash"] == format(snapshot.book.cash, "f")
    assert view["market_value"] == format(snapshot.book.market_value, "f")
    assert view["equity"] == format(snapshot.book.equity, "f")
    assert view["positions"] == [
        {
            "ts_code": position.subject,
            "quantity": position.quantity,
            "cost_basis": format(position.cost_basis, "f"),
            "lots": [
                {
                    "open_date": lot.open_date.isoformat(),
                    "quantity": lot.quantity,
                    "cost_basis": format(lot.cost_basis, "f"),
                }
                for lot in position.lots
            ],
            "mark": format(snapshot.book.mark(position.subject), "f"),
            "market_value": format(
                Decimal(position.quantity) * snapshot.book.mark(position.subject), "f"
            ),
            "unrealised": format(
                Decimal(position.quantity) * snapshot.book.mark(position.subject)
                - position.cost_basis,
                "f",
            ),
        }
        for position in snapshot.book.positions
    ]
    assert view["pending_orders"] == len(snapshot.pending_orders)
    assert view["wording"] == strategy_survivor_summary.SURVIVOR_WORDING
    assert "ledger units" in view["marks"]
    assert _tree(world.runtime) == before


@pytest.mark.parametrize(
    "others",
    [
        ["--morning-recheck"],
        ["--factor-gaps", "2026-01-01"],
        ["--launchd-plist", "logs"],
    ],
    ids=["morning-recheck", "factor-gaps", "launchd-plist"],
)
def test_show_book_refuses_the_modes_it_cannot_be_combined_with(
    world: tds.World, others: list[str]
) -> None:
    """It is a mode of its own: another mode beside it would otherwise be silently ignored."""
    err = io.StringIO()
    with contextlib.redirect_stderr(err), pytest.raises(SystemExit) as exited:
        daily.main(["--runtime-dir", str(world.runtime), "--show-book", *others])

    assert exited.value.code == 2
    assert "--show-book cannot be combined with" in err.getvalue()


def test_show_book_carries_the_smoke_banner_of_a_smoke_registration(
    world: tds.World, tmp_path: Path
) -> None:
    """A smoke registration's listing cannot be read without the banner: stderr's first and last
    line, the text's first and last line, and the JSON's `smoke` key."""
    smoke = _smoke_registration(tmp_path, world.registration)

    code, out, err = _show(world, registration=smoke)
    assert code == 0
    assert err[0] == err[-1] == daily.SMOKE_BANNER
    assert json.loads(out)["smoke"] == daily.SMOKE_BANNER

    code, out, err = _show(world, as_json=False, registration=smoke)
    assert code == 0
    lines = out.splitlines()
    assert lines[0] == lines[-1] == daily.SMOKE_BANNER
    assert err[0] == err[-1] == daily.SMOKE_BANNER
    assert any("cost" in line for line in lines)
    assert "read-only" in "\n".join(lines).lower()
