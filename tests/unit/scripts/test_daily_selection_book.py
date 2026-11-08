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
from typing import Any, Final

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


def _tree(root: Path) -> dict[str, bytes | None]:
    """Every entry under `root`: a file's bytes, or `None` for a directory (so a read path that
    creates an empty directory is caught too)."""
    return {
        entry.relative_to(root).as_posix(): None if entry.is_dir() else entry.read_bytes()
        for entry in sorted(root.rglob("*"))
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
        ["--launchd-morning-plist", "logs"],
        ["--pin-worktree", "pinned"],
        ["--account-record", "record.json"],
    ],
    ids=[
        "morning-recheck",
        "factor-gaps",
        "launchd-plist",
        "launchd-morning-plist",
        "pin-worktree",
        "account-record",
    ],
)
def test_show_book_refuses_the_modes_it_cannot_be_combined_with(
    world: tds.World, monkeypatch: pytest.MonkeyPatch, others: list[str]
) -> None:
    """It is a mode of its own: another mode beside it would otherwise be silently ignored, and
    `--pin-worktree` in particular would pin and sync before `--show-book` was even looked at."""
    refused_calls: list[str] = []

    def refused(name: str) -> Any:
        def call(*args: object, **kwargs: object) -> None:
            refused_calls.append(name)
            raise AssertionError(f"--show-book ran {name} for a mode it refused")

        return call

    monkeypatch.setattr(daily, "pin_worktree", refused("pin_worktree"))
    monkeypatch.setattr(daily, "sync_pinned_environment", refused("sync_pinned_environment"))
    err = io.StringIO()
    with contextlib.redirect_stderr(err), pytest.raises(SystemExit) as exited:
        daily.main(["--runtime-dir", str(world.runtime), "--show-book", *others])

    assert exited.value.code == 2
    assert "--show-book cannot be combined with" in err.getvalue()
    assert refused_calls == []


def test_a_damaged_head_snapshot_is_refused_by_name_not_read_as_an_empty_book(
    tmp_path: Path,
) -> None:
    """`held_head` refuses a snapshot it cannot read (`PaperStateError`, a `RuntimeError`): a
    damaged book is not an empty one, and the refusal is printed, not raised as a traceback."""
    runtime = tmp_path / "runtime"
    snapshots = runtime / "paper" / "snapshots"
    snapshots.mkdir(parents=True)
    (snapshots / f"2026-04-07.pbs_{'0' * 24}.json").write_text("{not json", encoding="utf-8")

    out = io.StringIO()
    err = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = daily.main(["--runtime-dir", str(runtime), "--show-book"])

    assert code == 1
    assert out.getvalue() == ""
    assert "paper book:" in err.getvalue()
    assert "cannot be read" in err.getvalue()


def test_a_smoke_registration_with_no_book_banners_the_refusal_before_and_after(
    world: tds.World, tmp_path: Path
) -> None:
    """The banner opens the output, so a refusal is bannered too -- every output of a smoke
    registration carries it, the failing ones included."""
    smoke = _smoke_registration(tmp_path, world.registration)
    runtime = tmp_path / "no-book"

    out = io.StringIO()
    err = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = daily.main(
            ["--runtime-dir", str(runtime), "--registration", str(smoke), "--show-book"]
        )

    assert code == 1
    assert out.getvalue() == ""
    lines = err.getvalue().splitlines()
    assert lines[0] == lines[-1] == daily.SMOKE_BANNER
    assert "no paper book snapshot is held at all" in "\n".join(lines)


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


def _account_record_file(
    runtime: Path, session: Any, planned: list[dict[str, Any]], *, not_placed: list[str]
) -> Path:
    path = runtime / "paper" / "account-record" / f"{session.isoformat()}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "schema_version": "account-record/v1",
                "session": session.isoformat(),
                "orders": [
                    {
                        "order_id": order["order_id"],
                        "ts_code": order["ts_code"],
                        "side": order["side"],
                        "placed": order["order_id"] not in not_placed,
                        "filled_quantity": 0
                        if order["order_id"] in not_placed
                        else order["quantity"],
                        "filled_price": None
                        if order["order_id"] in not_placed
                        else order["reference_price"],
                    }
                    for order in planned
                ],
            }
        ),
        encoding="utf-8",
    )
    return path


def test_a_record_of_the_orders_the_user_placed_is_filed_with_the_days_reconciliation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    seeded: Path,  # noqa: F811  (the module-scope fixture, imported above)
) -> None:
    """The second evening consumes the record written for its session: every planned order is
    accounted for, the one the user says they did not place is named, and the block is in the
    day's result and in its journal -- the divergence is recorded, never absorbed."""
    world = survivor_day._world(tmp_path, monkeypatch, seeded)
    first = survivor_day._completed(world, capsys, survivor_day.DAY)
    planned = first["paper"]["trade_list"]["orders"]
    _account_record_file(
        world.runtime, survivor_day.NEXT, planned, not_placed=[planned[0]["order_id"]]
    )

    second = survivor_day._completed(world, capsys, survivor_day.NEXT)

    block = second["paper"]["reconciliation"]
    assert block["session"] == survivor_day.NEXT.isoformat()
    assert block["orders"] == len(planned)
    named = {(item["order_id"], item["kind"]) for item in block["divergences"]}
    assert (planned[0]["order_id"], "not_placed") in named
    assert all(kind in {"not_placed", "price_differs"} for _, kind in named)
    assert block["reconciled"] + len(block["divergences"]) == len(planned)
    filed = survivor_day._journal(world, survivor_day.NEXT)["result"]["paper"]["reconciliation"]
    assert filed == block


def test_a_record_naming_an_order_that_was_not_due_refuses_the_day_before_the_book_advances(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    seeded: Path,  # noqa: F811  (the module-scope fixture, imported above)
) -> None:
    """A record that names an order no evening planned is irreconcilable: the day stops at step 8,
    the session has no snapshot and no ledger row, and once the record is fixed the same day
    completes -- so nothing of the bad record was absorbed."""
    world = survivor_day._world(tmp_path, monkeypatch, seeded)
    first = survivor_day._completed(world, capsys, survivor_day.DAY)
    planned = first["paper"]["trade_list"]["orders"]
    path = _account_record_file(world.runtime, survivor_day.NEXT, planned, not_placed=[])
    body = json.loads(path.read_text("utf-8"))
    body["orders"].append(
        {
            "order_id": "an_order_no_evening_planned",
            "ts_code": planned[0]["ts_code"],
            "side": planned[0]["side"],
            "placed": True,
            "filled_quantity": 0,
            "filled_price": None,
        }
    )
    path.write_text(json.dumps(body), encoding="utf-8")

    code, _out, err = survivor_day._day(world, capsys, survivor_day.NEXT)

    assert code == 1
    assert any("paper book" in line and "an_order_no_evening_planned" in line for line in err)
    assert survivor_day._snapshots(world).get(survivor_day.NEXT) is None
    assert survivor_day._ledger_rows(world, tmp_path) == []
    assert not survivor_day._journal(world, survivor_day.NEXT).get("result")

    _account_record_file(world.runtime, survivor_day.NEXT, planned, not_placed=[])
    again = survivor_day._completed(world, capsys, survivor_day.NEXT)
    assert again["paper"]["reconciliation"]["orders"] == len(planned)


def test_a_settle_only_day_prints_and_files_the_records_reconciliation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    seeded: Path,  # noqa: F811  (the module-scope fixture, imported above)
) -> None:
    """NEXT run after its own call auction settles without a decision: there is no evening block,
    so the day's own catch-up entry carries the reconciliation and the text summary prints it --
    the record is consumed and the divergence is named rather than absorbed."""
    world = survivor_day._world(tmp_path, monkeypatch, seeded)
    first = survivor_day._completed(world, capsys, survivor_day.DAY)
    planned = first["paper"]["trade_list"]["orders"]
    _account_record_file(
        world.runtime, survivor_day.NEXT, planned, not_placed=[planned[0]["order_id"]]
    )
    late = survivor_day.after_the_auction(survivor_day.NEXT)

    code, out, err = survivor_day._run(world, capsys, as_of=late, clock=late, json_output=False)

    assert code == 0, err
    text = "\n".join(out.splitlines())
    assert "reconciled" in text and "orders against the account record" in text
    assert planned[0]["order_id"] in text and "not_placed" in text
    entry = next(
        item
        for item in survivor_day._journal(world, survivor_day.NEXT)["result"]["paper_skipped"][
            "catch_ups"
        ]
        if item["session"] == survivor_day.NEXT.isoformat()
    )
    assert entry["reconciliation"]["orders"] == len(planned)
    named = {(item["order_id"], item["kind"]) for item in entry["reconciliation"]["divergences"]}
    assert (planned[0]["order_id"], "not_placed") in named


def test_a_settle_only_day_with_no_record_says_the_books_assumption_stands_unverified(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    seeded: Path,  # noqa: F811  (the module-scope fixture, imported above)
) -> None:
    """A settle-only day with no record consumes none, and says so: the book's assumption stands
    unverified, never that it was checked."""
    world = survivor_day._world(tmp_path, monkeypatch, seeded)
    survivor_day._completed(world, capsys, survivor_day.DAY)
    late = survivor_day.after_the_auction(survivor_day.NEXT)

    code, out, err = survivor_day._run(world, capsys, as_of=late, clock=late, json_output=False)

    assert code == 0, err
    text = "\n".join(out.splitlines())
    assert f"no account record for {survivor_day.NEXT.isoformat()}" in text
    assert (
        "the paper book's assumption that every printed order was placed stands unverified" in text
    )


def test_morning_recheck_refuses_an_account_record(tmp_path: Path) -> None:
    """`--account-record` is the day's input, and the morning re-check consumes no day: it is
    refused explicitly rather than silently ignored, as `--show-book`'s other modes are."""
    err = io.StringIO()
    with contextlib.redirect_stderr(err), pytest.raises(SystemExit) as exited:
        daily.main(
            [
                "--runtime-dir",
                str(tmp_path / "runtime"),
                "--morning-recheck",
                "--account-record",
                str(tmp_path / "record.json"),
            ]
        )

    assert exited.value.code == 2
    assert "--morning-recheck cannot be combined with --account-record" in err.getvalue()
