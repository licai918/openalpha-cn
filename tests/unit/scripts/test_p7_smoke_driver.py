"""`scripts/p7_smoke.py`: the real-data smoke's driver (`V2-P7-019`, task 4), end to end.

A smoke root is prepared by `p7_smoke_env.prepare_smoke` from a throwaway `main` checkout
(`test_p7_smoke_env.py`'s) and `test_daily_selection_survivor_day.py`'s seeded panel (3 April), and
its pinned worktree is bound as the daily command's own tests bind a repository
(`test_daily_selection._bind`): the registry's import checks point at it and the panel's transport
at the scripted market. The driver then runs the real daily command, its morning re-check and the
weekly report in process, under the cap -- and the first night's catch-up from the remedy the
evening's refusal printed. Every count is checked against what reached the market. Nothing
reaches the network.
"""

from __future__ import annotations

import contextlib
import importlib
import io
import os
import shutil
import signal
import subprocess
import sys
import time
import types
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, Final

import pytest
import test_daily_selection as tds
import test_daily_selection_survivor_day as survivor_day
import test_p7_smoke_env as env_tests
from research_repo import git, head
from test_daily_selection_survivor import STEADY_SURVIVOR
from test_daily_selection_survivor_day import seeded  # noqa: F401  (a module fixture, reused)

from openalpha_cn import cli
from openalpha_cn.panel.store import _try_lock, _unlock

ROOT: Final[Path] = Path(__file__).resolve().parents[3]


def _script(name: str) -> Any:
    directory = ROOT / "scripts"
    if str(directory) not in sys.path:
        sys.path.insert(0, str(directory))
    return importlib.import_module(name)


driver = _script("p7_smoke")
env = _script("p7_smoke_env")
caps = _script("p7_smoke_requests")
daily = tds.daily
NOW: Final[datetime] = datetime(2026, 4, 1, 12, 0, tzinfo=UTC)
SESSION_REQUESTS: Final[int] = sum(
    len(cli.PANEL_BUILD_TARGETS[target]) for target in ("adj_factor", "price", "stk_limit")
)
"""Five: `adj_factor`, `daily`, `daily_basic`, `suspend_d`, `stk_limit` -- one each a session."""


@dataclass
class Smoke:
    root: Path
    manifest: Any
    market: survivor_day.SurvivorMarket


def _smoke(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    seeded_runtime: Path,
    *,
    epoch: date,
    caps_: dict[int, int] | None = None,
) -> Smoke:
    source = env_tests._source_repository(tmp_path)
    monkeypatch.setattr(daily, "_run_sync", env_tests._offline_sync)
    manifest = env.prepare_smoke(
        root=tmp_path / "smoke",
        source_repo=source,
        commit=head(source),
        source_panel=seeded_runtime / "panel",
        epoch=epoch,
        caps=caps_ or {1: 190, 2: 460},
        policy=env.PathPolicy(
            temporary_roots=(tmp_path / "volatile",),
            main_checkout=source,
            research_runtime=seeded_runtime,
        ),
        uv=Path("uv"),
        clock=lambda: NOW,
        book=survivor_day.BOOK,
        copy_tree=shutil.copytree,
    )
    market = survivor_day.SurvivorMarket(open_days=tds.OPEN_2026)
    tds._bind(monkeypatch, manifest.pinned, market)
    return Smoke(root=manifest.root, manifest=manifest, market=market)


def _drive(
    smoke: Smoke, capsys: pytest.CaptureFixture[str], *arguments: str, at: datetime
) -> tuple[int, str, list[str]]:
    command, *rest = arguments
    code = driver.main([command, "--smoke-root", str(smoke.root), *rest], clock=lambda: at)
    out, err = capsys.readouterr()
    assert tds.SECRET_TOKEN not in out + err
    assert env_tests.SENTINEL not in out + err
    return code, out, err.splitlines()


def _spent(smoke: Smoke) -> Any:
    return caps.RequestLedger(smoke.root / env.LEDGER_NAME).spent()


def _ends(smoke: Smoke) -> list[dict[str, Any]]:
    events = caps.RequestLedger(smoke.root / env.LEDGER_NAME).events()
    kinds = {event["id"]: event["kind"] for event in events if event["event"] == "start"}
    return [{**event, "kind": kinds[event["id"]]} for event in events if event["event"] == "end"]


_night = survivor_day.evening_of
"""A session's 18:30 in Shanghai: the scheduled evening's clock."""


# --- an evening ----------------------------------------------------------------------------------


def test_an_evening_through_the_driver_spends_exactly_what_reached_the_market(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    seeded: Path,  # noqa: F811
) -> None:
    smoke = _smoke(tmp_path, monkeypatch, seeded, epoch=survivor_day.EPOCH)
    smoke.market.today = survivor_day.DAY
    as_of = survivor_day.DAY_AS_OF.isoformat()

    code, out, err = _drive(
        smoke, capsys, "evening", "--", "--as-of", as_of, at=survivor_day.RUN_CLOCK
    )

    assert code == 0, err
    lines = out.splitlines()
    assert daily.SMOKE_BANNER in lines
    assert sum(line.startswith("  BUY ") for line in lines) == 10
    assert _spent(smoke).total == len(smoke.market.payloads) == sum(STEADY_SURVIVOR.values())
    logs = sorted(path.name for path in (smoke.root / env.LOGS_NAME).iterdir())
    assert logs == ["0001-evening.err", "0001-evening.out"]
    assert daily.SMOKE_BANNER in (smoke.root / env.LOGS_NAME / "0001-evening.out").read_text(
        encoding="utf-8"
    )

    code, out, _err = _drive(
        smoke, capsys, "evening", "--", "--as-of", as_of, at=survivor_day.RUN_CLOCK
    )

    assert code == 0
    assert "the day was already complete" in out
    assert _spent(smoke).total == len(smoke.market.payloads) == sum(STEADY_SURVIVOR.values())


def test_the_first_night_s_catch_up_runs_from_the_printed_remedy_under_the_cap(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    seeded: Path,  # noqa: F811
) -> None:
    """The panel stops at 3 April and the first evening is 21 April: eleven missed sessions, one
    more than the registered bound (10). Step 2 refuses before any request and prints the oldest
    batch; the remedy, run through the driver, fetches 3 April again and the ten oldest at five
    requests each; the evening then fetches the rest itself and plans the initial build."""
    smoke = _smoke(tmp_path, monkeypatch, seeded, epoch=date(2026, 4, 20))
    night = _night(date(2026, 4, 21))
    smoke.market.today = night.date()

    code, _out, err = _drive(
        smoke,
        capsys,
        "evening",
        "--",
        "--as-of",
        night.isoformat(),
        at=night + timedelta(minutes=5),
    )

    assert code == 1
    assert smoke.market.payloads == []
    remedy = next(
        line.strip()
        for line in err
        if line.lstrip().startswith("UV_PROJECT_ENVIRONMENT=") and " panel build " in line
    )
    assert "--incremental" in remedy

    code, _out, err = _drive(smoke, capsys, "remedy", remedy, at=night + timedelta(minutes=10))

    assert code == 0, err
    assert len(smoke.market.payloads) == 11 * SESSION_REQUESTS

    code, out, err = _drive(
        smoke,
        capsys,
        "evening",
        "--",
        "--as-of",
        night.isoformat(),
        at=night + timedelta(minutes=20),
    )

    assert code == 0, err
    assert sum(line.startswith("  BUY ") for line in out.splitlines()) == 10
    assert _spent(smoke).total == len(smoke.market.payloads)
    assert [end["kind"] for end in _ends(smoke)] == ["evening", "panel-build", "evening"]


def test_the_cap_stops_an_evening_mid_fetch_and_the_root_is_discarded(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    seeded: Path,  # noqa: F811
) -> None:
    """16 April with the panel at 3 April: eight missed sessions, within the bound, so the
    evening itself fetches ten sessions (fifty-six requests) -- against a phase cap of thirty."""
    smoke = _smoke(tmp_path, monkeypatch, seeded, epoch=date(2026, 4, 15), caps_={1: 30, 2: 460})
    night = _night(date(2026, 4, 16))
    smoke.market.today = night.date()

    code, _out, err = _drive(
        smoke,
        capsys,
        "evening",
        "--",
        "--as-of",
        night.isoformat(),
        at=night + timedelta(minutes=5),
    )

    assert code == caps.CAP_EXIT
    assert len(smoke.market.payloads) == 30
    text = "\n".join(err)
    assert "REQUEST CAP REACHED" in text
    assert "whole-year rebuild" in text
    spent = _spent(smoke)
    assert spent.total == 30
    assert spent.discarded is not None

    code, out, _err = _drive(smoke, capsys, "status", at=night + timedelta(minutes=6))
    assert code == 0
    assert any(line.startswith("discarded") and "cap" in line for line in out.splitlines())

    code, _out, err = _drive(
        smoke,
        capsys,
        "evening",
        "--",
        "--as-of",
        night.isoformat(),
        at=night + timedelta(minutes=7),
    )
    assert code == driver.REFUSED_EXIT
    assert any("discarded" in line for line in err)
    assert len(smoke.market.payloads) == 30


def test_a_morning_spends_one_request_and_a_weekly_report_none(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    seeded: Path,  # noqa: F811
) -> None:
    smoke = _smoke(tmp_path, monkeypatch, seeded, epoch=survivor_day.EPOCH)
    smoke.market.today = survivor_day.DAY
    _drive(
        smoke,
        capsys,
        "evening",
        "--",
        "--as-of",
        survivor_day.DAY_AS_OF.isoformat(),
        at=survivor_day.RUN_CLOCK,
    )
    morning = datetime(2026, 4, 7, 8, 30, tzinfo=daily.SHANGHAI)
    smoke.market.today = morning.date()

    code, out, err = _drive(
        smoke,
        capsys,
        "morning",
        "--",
        "--as-of",
        morning.isoformat(),
        at=morning + timedelta(minutes=5),
    )
    assert code == 0, err
    assert daily.SMOKE_BANNER in out.splitlines()

    report = datetime(2026, 4, 7, 10, 0, tzinfo=daily.SHANGHAI)
    code, out, err = _drive(smoke, capsys, "weekly", "--", "--as-of", report.isoformat(), at=report)

    assert code == 0, err
    assert {end["kind"]: end["count"] for end in _ends(smoke)} == {
        "evening": sum(STEADY_SURVIVOR.values()),
        "morning": 1,
        "weekly": 0,
    }


# --- remedies ------------------------------------------------------------------------------------


def _paths(tmp_path: Path) -> dict[str, Path]:
    pinned = tmp_path / "smoke" / env.PINNED_NAME
    return {
        "runtime": tmp_path / "smoke" / env.RUNTIME_NAME,
        "registration": pinned / env.REGISTRATION_PATH,
        "pinned": pinned,
    }


def test_a_printed_remedy_is_read_back_into_the_driver_s_own_invocation(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    runtime = str(paths["runtime"])
    batch = [
        "openalpha", "panel", "build", "--runtime-dir", runtime, "--year", "2026",
        "--as-of", "2026-10-22T16:30:00+08:00", "--exchange", "SSE", "--incremental",
        "--dataset", "adj_factor", "--dataset", "price", "--dataset", "stk_limit",
    ]  # fmt: skip
    calendar = [
        "openalpha", "panel", "build", "--runtime-dir", runtime, "--year", "2027",
        "--as-of", "2027-01-04T16:30:00+08:00", "--exchange", "SSE", "--dataset", "trade_cal",
    ]  # fmt: skip
    factors = [
        "openalpha", "factor", "build", "--runtime-dir", runtime, "--factor", "return_vol_60/v1",
        "--tier", "processed", "--transform", "cross_section_standard/v1", "--year", "2026",
        "--exchange", "SSE", "--max-staleness-days", "30", "--as-of", "2026-10-09T16:30:00+08:00",
    ]  # fmt: skip
    rerun = daily.rerun_command(
        daily.DailyOptions(
            runtime_dir=paths["runtime"],
            registration=paths["registration"],
            repo=paths["pinned"],
            as_of=datetime(2026, 11, 3, 18, 30, tzinfo=daily.SHANGHAI),
        )
    )

    assert driver.parse_remedy(daily.pinned_command(batch), **paths) == driver.Remedy(
        kind="panel-build", arguments=tuple(batch[1:])
    )
    assert driver.parse_remedy(daily.pinned_command(calendar), **paths).kind == "panel-build"
    assert driver.parse_remedy(daily.pinned_command(factors), **paths) == driver.Remedy(
        kind="factor-build", arguments=tuple(factors[1:])
    )
    assert driver.parse_remedy(rerun, **paths) == driver.Remedy(
        kind="evening", arguments=("--as-of", "2026-11-03T18:30:00+08:00")
    )


@pytest.mark.parametrize(
    ("change", "named"),
    [
        ("whole year", "whole-year rebuild"),
        ("research runtime", "runtime"),
        ("p6 registration", "registration"),
        ("doctor", "not a remedy"),
        ("no program", "no `openalpha`"),
    ],
)
def test_a_remedy_the_smoke_never_runs_is_refused_by_name(
    tmp_path: Path, change: str, named: str
) -> None:
    paths = _paths(tmp_path)
    runtime = str(paths["runtime"])
    build = ["openalpha", "panel", "build", "--runtime-dir", runtime, "--year", "2026"]
    build += ["--as-of", "2026-10-22T16:30:00+08:00", "--exchange", "SSE"]
    scoped = ["--dataset", "adj_factor", "--dataset", "price", "--dataset", "stk_limit"]
    line = {
        "whole year": daily.pinned_command([*build, *scoped]),
        "research runtime": daily.pinned_command(
            [*build[:4], str(Path.home() / "openalpha-research"), *build[5:], "--incremental"]
        ),
        "p6 registration": daily.rerun_command(
            daily.DailyOptions(
                runtime_dir=paths["runtime"],
                registration=paths["pinned"] / "docs" / "research" / "p6-registration.json",
                repo=paths["pinned"],
            )
        ),
        "doctor": daily.pinned_command(["openalpha", "panel", "doctor", "--runtime-dir", runtime]),
        "no program": "then rerun this command",
    }[change]

    with pytest.raises(driver.RemedyError, match=named):
        driver.parse_remedy(line, **paths)


def test_a_remedy_from_a_checkout_that_is_not_the_pinned_worktree_is_refused(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    seeded: Path,  # noqa: F811
) -> None:
    smoke = _smoke(tmp_path, monkeypatch, seeded, epoch=survivor_day.EPOCH)
    elsewhere = tmp_path / "elsewhere" / "src" / "openalpha_cn" / "__init__.py"
    monkeypatch.setattr(tds.registry, "_imported_package", lambda: elsewhere)
    line = daily.pinned_command(
        ["openalpha", "panel", "build", "--runtime-dir", str(smoke.manifest.runtime), "--year",
         "2026", "--as-of", "2026-04-06T16:30:00+08:00", "--exchange", "SSE", "--incremental",
         "--dataset", "adj_factor", "--dataset", "price", "--dataset", "stk_limit"]
    )  # fmt: skip

    code, _out, err = _drive(smoke, capsys, "remedy", line, at=survivor_day.RUN_CLOCK)

    assert code == int(daily.DailyExit.code_not_registered)
    assert any("ForeignPackageError" in entry for entry in err)
    assert smoke.market.payloads == []


# --- the driver's own guards ---------------------------------------------------------------------


def test_the_driver_sets_the_day_s_paths_itself(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    seeded: Path,  # noqa: F811
) -> None:
    smoke = _smoke(tmp_path, monkeypatch, seeded, epoch=survivor_day.EPOCH)

    code, _out, err = _drive(
        smoke, capsys, "evening", "--", "--runtime-dir", str(tmp_path), at=survivor_day.RUN_CLOCK
    )

    assert code == driver.REFUSED_EXIT
    assert any("--runtime-dir" in line for line in err)
    assert smoke.market.payloads == []


def test_one_invocation_at_a_time_in_a_smoke_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    seeded: Path,  # noqa: F811
) -> None:
    smoke = _smoke(tmp_path, monkeypatch, seeded, epoch=survivor_day.EPOCH)
    descriptor = os.open(smoke.root / driver.DRIVER_LOCK, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        assert _try_lock(descriptor, exclusive=True)
        code, _out, err = _drive(smoke, capsys, "evening", at=survivor_day.RUN_CLOCK)
    finally:
        _unlock(descriptor)
        os.close(descriptor)

    assert code == driver.REFUSED_EXIT
    assert any("another smoke invocation" in line for line in err)
    assert smoke.market.payloads == []


@pytest.mark.skipif(not hasattr(signal, "SIGHUP"), reason="POSIX signals")
def test_the_driver_puts_the_signal_handlers_back(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    seeded: Path,  # noqa: F811
) -> None:
    smoke = _smoke(tmp_path, monkeypatch, seeded, epoch=survivor_day.EPOCH)
    before = (signal.getsignal(signal.SIGTERM), signal.getsignal(signal.SIGHUP))

    _drive(smoke, capsys, "status", at=survivor_day.RUN_CLOCK)

    assert (signal.getsignal(signal.SIGTERM), signal.getsignal(signal.SIGHUP)) == before


def test_phase_two_is_entered_once_with_a_reason(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    seeded: Path,  # noqa: F811
) -> None:
    smoke = _smoke(tmp_path, monkeypatch, seeded, epoch=survivor_day.EPOCH)

    code, _out, _err = _drive(
        smoke, capsys, "phase", "--to", "2", "--reason", "the user's go", at=NOW
    )
    assert code == 0
    assert _spent(smoke).phase == 2
    code, _out, _err = _drive(smoke, capsys, "phase", "--to", "2", "--reason", "again", at=NOW)
    assert code == driver.REFUSED_EXIT


# --- what the reviews of tasks 2 and 3 left to the driver ----------------------------------------
#
# Each test below pins one of the requirements the reviews of `p7_smoke_requests.py` (task 2) and
# `p7_smoke_env.py` (task 3) handed to this driver; the requirement is named in its docstring.


class _Sink:
    """A transport that answers every request with an empty table and keeps the request: for the
    tests that stand in for the daily command, so the scripted market is not asked to answer
    payloads it never would see."""

    def __init__(self) -> None:
        self.payloads: list[dict[str, Any]] = []

    def post(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.payloads.append(payload)
        return {"code": 0, "msg": "", "data": {"fields": [], "items": []}}


def _sink(monkeypatch: pytest.MonkeyPatch) -> _Sink:
    sink = _Sink()
    monkeypatch.setattr(cli, "_panel_transport", lambda: sink)
    return sink


def _send(count: int) -> None:
    """`count` requests through the panel's transport seam, as `panel build` sends them."""
    transport = cli._panel_transport()
    for _ in range(count):
        transport.post({"api_name": "daily", "params": {}})


def _standing_in(monkeypatch: pytest.MonkeyPatch, body: Callable[[], int]) -> list[Sequence[str]]:
    """`daily_selection.main` replaced by `body`: the arguments the driver passed are kept."""
    seen: list[Sequence[str]] = []

    def main(argv: Sequence[str], *, clock: Callable[[], datetime]) -> int:
        del clock
        seen.append(list(argv))
        return body()

    monkeypatch.setattr(daily, "main", main)
    return seen


def _lock_is_held(root: Path) -> bool:
    descriptor = os.open(root / driver.DRIVER_LOCK, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        if _try_lock(descriptor, exclusive=True):
            _unlock(descriptor)
            return False
        return True
    finally:
        os.close(descriptor)


def test_the_driver_lock_is_held_from_the_manifest_read_to_the_last_ledger_append(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    seeded: Path,  # noqa: F811
) -> None:
    """Requirement 1: one writer -- the lock is held while the manifest is read again, while the
    ledger is read for what was spent, and at every append, the last one included."""
    smoke = _smoke(tmp_path, monkeypatch, seeded, epoch=survivor_day.EPOCH)
    seen: list[tuple[str, bool]] = []
    read = env.SmokeManifest.read
    spent = caps.RequestLedger.spent
    append = caps.RequestLedger.append

    def reading(cls: Any, root: Path) -> Any:
        seen.append(("read", _lock_is_held(smoke.root)))
        return read(root)

    def spending(ledger: Any) -> Any:
        seen.append(("spent", _lock_is_held(smoke.root)))
        return spent(ledger)

    def appending(ledger: Any, event: Any) -> None:
        seen.append((f"append {event['event']}", _lock_is_held(smoke.root)))
        append(ledger, event)

    monkeypatch.setattr(env.SmokeManifest, "read", classmethod(reading))
    monkeypatch.setattr(caps.RequestLedger, "spent", spending)
    monkeypatch.setattr(caps.RequestLedger, "append", appending)
    smoke.market.today = survivor_day.DAY

    code, _out, err = _drive(
        smoke,
        capsys,
        "evening",
        "--",
        "--as-of",
        survivor_day.DAY_AS_OF.isoformat(),
        at=survivor_day.RUN_CLOCK,
    )

    assert code == 0, err
    appends = [name for name, _held in seen if name.startswith("append")]
    assert appends == ["append start", "append end"]
    first_spent = next(index for index, (name, _held) in enumerate(seen) if name == "spent")
    reads = [index for index, (name, _held) in enumerate(seen) if name == "read"]
    assert reads and reads[-1] < first_spent and seen[reads[-1]][1]
    assert all(held for name, held in seen[first_spent:]), seen
    assert not _lock_is_held(smoke.root)


def test_a_ledger_emptied_after_the_manifest_was_read_is_refused_inside_the_lock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    seeded: Path,  # noqa: F811
) -> None:
    """Requirement 1: `run` reads the manifest again under the lock, so a ledger emptied between
    the caller's read and the lock -- which `spent()` would read as nothing spent -- is refused
    before a request or an append."""
    smoke = _smoke(tmp_path, monkeypatch, seeded, epoch=survivor_day.EPOCH)
    manifest = env.SmokeManifest.read(smoke.root)
    ledger = smoke.root / env.LEDGER_NAME
    ledger.write_text("", encoding="utf-8")
    smoke.market.today = survivor_day.DAY

    with pytest.raises(env.SmokeEnvironmentError, match="prepared"):
        driver.run(
            manifest,
            "evening",
            ["--as-of", survivor_day.DAY_AS_OF.isoformat()],
            clock=lambda: survivor_day.RUN_CLOCK,
        )

    assert smoke.market.payloads == []
    assert ledger.read_text(encoding="utf-8") == ""


@pytest.mark.parametrize("cut", ["to its prepared line", "one invocation out of the middle"])
def test_a_ledger_cut_back_is_refused_though_the_manifest_still_reads(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    seeded: Path,  # noqa: F811
    cut: str,
) -> None:
    """Requirement 2: `SmokeManifest.read` holds a ledger to its first event only, and a ledger
    cut back still opens with it -- and counts fewer requests. The driver holds the ledger to the
    invocation logs it writes (one pair per invocation, never overwritten) and to its own
    numbering and running totals."""
    smoke = _smoke(tmp_path, monkeypatch, seeded, epoch=survivor_day.EPOCH)
    smoke.market.today = survivor_day.DAY
    evening = ["evening", "--", "--as-of", survivor_day.DAY_AS_OF.isoformat()]
    assert _drive(smoke, capsys, *evening, at=survivor_day.RUN_CLOCK)[0] == 0
    assert _drive(smoke, capsys, *evening, at=survivor_day.RUN_CLOCK)[0] == 0
    ledger = smoke.root / env.LEDGER_NAME
    lines = ledger.read_bytes().split(b"\n")
    # prepared, start 1, end 1, start 2, end 2, and the empty string after the last newline
    kept = lines[:1] if cut == "to its prepared line" else [lines[0], *lines[3:5]]
    ledger.write_bytes(b"\n".join(kept) + b"\n")  # bytes: no CRLF on Windows
    env.SmokeManifest.read(smoke.root)  # the manifest's own check passes either way
    sent = len(smoke.market.payloads)

    code, _out, err = _drive(smoke, capsys, *evening, at=survivor_day.RUN_CLOCK)

    assert code == driver.REFUSED_EXIT
    assert any("smoke: refused:" in line and "ledger" in line for line in err)
    assert len(smoke.market.payloads) == sent
    assert ledger.read_bytes() == b"\n".join(kept) + b"\n"
    code, out, _err = _drive(smoke, capsys, "status", at=survivor_day.RUN_CLOCK)
    assert code == driver.REFUSED_EXIT  # M7: the status of a refused root is a refusal
    assert any(line.startswith("ledger") and "refused" in line for line in out.splitlines())


def _start(number: int, spent_before: int) -> dict[str, Any]:
    return {"event": "start", "id": number, "kind": "evening", "spent_before": spent_before}


def _end(number: int, count: int) -> dict[str, Any]:
    return {"event": "end", "id": number, "count": count}


def test_each_way_a_ledger_can_lose_an_invocation_is_named(tmp_path: Path) -> None:
    """Requirement 2, check by check: an invocation taken out of the numbering, a running total
    that does not add up, and logs of more invocations than the ledger records -- each alone."""
    logs = tmp_path / "logs"
    prepared = {"event": "prepared"}
    whole = [prepared, _start(1, 0), _end(1, 16), _start(2, 16), _end(2, 0)]

    assert driver.ledger_problem(whole, logs=logs) is None
    gap = [prepared, _start(1, 0), _end(1, 16), _start(3, 16), _end(3, 0)]
    assert "numbered [1, 3]" in str(driver.ledger_problem(gap, logs=logs))
    total = [prepared, _start(1, 0), _end(1, 16), _start(2, 0), _end(2, 0)]
    assert "started with 0 request(s) spent" in str(driver.ledger_problem(total, logs=logs))
    logs.mkdir()
    for name in ("0001-evening.out", "0001-evening.err", "0002-evening.out", "notes.txt"):
        (logs / name).write_text("", encoding="utf-8")
    assert driver.ledger_problem(whole, logs=logs) is None
    (logs / "0003-panel-build.err").write_text("", encoding="utf-8")
    assert "output of invocation 3" in str(driver.ledger_problem(whole, logs=logs))


def test_run_itself_refuses_what_the_command_line_would(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    seeded: Path,  # noqa: F811
) -> None:
    """`run` is the driver's entry for every invocation, not only the command line's: it refuses
    a kind it does not make, the whole-year rebuild, and a manifest that is not the root's."""
    smoke = _smoke(tmp_path, monkeypatch, seeded, epoch=survivor_day.EPOCH)
    manifest = env.SmokeManifest.read(smoke.root)
    build = ["panel", "build", "--runtime-dir", str(manifest.runtime), "--year", "2026"]
    build += ["--as-of", "2026-04-06T16:30:00+08:00", "--exchange", "SSE", "--dataset", "price"]

    with pytest.raises(driver.SmokeDriverError, match="not an invocation"):
        driver.run(manifest, "doctor", [], clock=lambda: NOW)
    with pytest.raises(driver.RemedyError, match="whole-year rebuild"):
        driver.run(manifest, "panel-build", build, clock=lambda: NOW)
    with pytest.raises(driver.SmokeDriverError, match="is not the manifest"):
        driver.run(replace(manifest, panel_files=99), "evening", [], clock=lambda: NOW)
    assert smoke.market.payloads == []
    events = caps.RequestLedger(smoke.root / env.LEDGER_NAME).events()
    assert [event["event"] for event in events] == ["prepared"]


def test_phase_two_needs_a_reason_and_a_root_that_runs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    seeded: Path,  # noqa: F811
) -> None:
    smoke = _smoke(tmp_path, monkeypatch, seeded, epoch=survivor_day.EPOCH)

    code, _out, err = _drive(smoke, capsys, "phase", "--to", "2", "--reason", "  ", at=NOW)
    assert code == driver.REFUSED_EXIT
    assert any("--reason" in line for line in err)
    caps.RequestLedger(smoke.root / env.LEDGER_NAME).append(
        {"event": "discarded", "reason": "a test's", "at": NOW.isoformat()}
    )
    code, _out, err = _drive(smoke, capsys, "phase", "--to", "2", "--reason", "go", at=NOW)
    assert code == driver.REFUSED_EXIT
    assert any("discarded" in line for line in err)
    assert _spent(smoke).phase == 1


@pytest.mark.parametrize("fault", [RuntimeError, KeyboardInterrupt])
def test_an_invocation_that_ends_on_a_fault_after_the_cap_still_exits_on_the_cap(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    seeded: Path,  # noqa: F811
    fault: type[BaseException],
) -> None:
    """Requirement 3: `run_capped` returns no `CappedRun` when the call ends on an exception after
    the cap stopped a request -- it records the end and raises. The driver reads `reached` from
    the ledger, prints the cap's lines and exits `CAP_EXIT`."""
    smoke = _smoke(tmp_path, monkeypatch, seeded, epoch=survivor_day.EPOCH, caps_={1: 30, 2: 460})
    sink = _sink(monkeypatch)

    def body() -> int:
        with contextlib.suppress(caps.RequestCapReached):
            _send(31)
        raise fault("a fault after the cap")

    _standing_in(monkeypatch, body)

    code, _out, err = _drive(smoke, capsys, "evening", at=survivor_day.RUN_CLOCK)

    assert code == caps.CAP_EXIT
    assert len(sink.payloads) == 30
    text = "\n".join(err)
    assert "REQUEST CAP REACHED  invocation 1 stopped: the daily request" in text
    assert "whole-year rebuild" in text
    (end,) = _ends(smoke)
    assert (end["reached"], end["fault"], end["count"]) == ("daily", fault.__name__, 30)
    assert _spent(smoke).discarded is not None


@pytest.mark.parametrize(("kind", "discarded"), [("evening", True), ("morning", False)])
def test_an_interrupted_invocation_exits_130_and_discards_only_a_panel_writer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    seeded: Path,  # noqa: F811
    kind: str,
    discarded: bool,
) -> None:
    smoke = _smoke(tmp_path, monkeypatch, seeded, epoch=survivor_day.EPOCH)
    sink = _sink(monkeypatch)

    def body() -> int:
        _send(1)
        raise KeyboardInterrupt

    seen = _standing_in(monkeypatch, body)

    code, _out, err = _drive(smoke, capsys, kind, at=survivor_day.RUN_CLOCK)

    assert code == driver.INTERRUPTED_EXIT
    assert any(line.startswith("smoke: interrupted") for line in err)
    assert ("--morning-recheck" in seen[0]) is (kind == "morning")
    assert len(sink.payloads) == 1
    (end,) = _ends(smoke)
    assert (end["fault"], end["count"]) == ("KeyboardInterrupt", 1)
    assert (_spent(smoke).discarded is not None) is discarded


@pytest.mark.skipif(not hasattr(signal, "SIGHUP"), reason="POSIX signals")
@pytest.mark.parametrize("name", ["SIGTERM", "SIGHUP"])
def test_a_terminating_signal_during_an_evening_is_an_interrupt_that_discards_the_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    seeded: Path,  # noqa: F811
    name: str,
) -> None:
    """Closing the terminal (SIGHUP) or a SIGTERM during an evening ends it as Ctrl-C does: the
    ledger records the end and the root is discarded. A handler of the test's own stands behind
    the driver's, so a driver that installed none would let the evening return, not kill the
    test run."""
    smoke = _smoke(tmp_path, monkeypatch, seeded, epoch=survivor_day.EPOCH)
    number = getattr(signal, name)
    reached: list[str] = []
    previous = signal.signal(number, lambda *_: reached.append("the test's own handler"))

    def body() -> int:
        os.kill(os.getpid(), number)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and not reached:
            time.sleep(0.01)
        return 0

    _standing_in(monkeypatch, body)
    try:
        code, _out, err = _drive(smoke, capsys, "evening", at=survivor_day.RUN_CLOCK)
    finally:
        restored = signal.getsignal(number)
        signal.signal(number, previous)

    assert reached == []
    assert code == driver.INTERRUPTED_EXIT, err
    assert _spent(smoke).discarded is not None
    assert restored is not None and restored is not signal.SIG_DFL  # the test's handler is back


def test_a_panel_build_that_fails_short_of_the_cap_exits_as_it_did_and_keeps_the_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    seeded: Path,  # noqa: F811
) -> None:
    """Requirement 4: a `panel build` that stops on its own fault part-way -- no cap, no
    exception -- exits with its own code and does not discard the root. Its stored partitions may
    then disagree, and the next evening's refusal prints the whole-year rebuild of the
    session-scoped targets, which `parse_remedy` refuses
    (`test_a_remedy_the_smoke_never_runs_is_refused_by_name[whole year-...]`)."""
    smoke = _smoke(tmp_path, monkeypatch, seeded, epoch=survivor_day.EPOCH)
    sink = _sink(monkeypatch)

    def invoke(arguments: Sequence[str]) -> Any:
        assert list(arguments[:2]) == ["panel", "build"]
        _send(2)
        return daily.Invocation(exit_code=1, stdout="", stderr="an injected provider failure\n")

    monkeypatch.setattr(daily, "invoke", invoke)
    line = daily.pinned_command(
        ["openalpha", "panel", "build", "--runtime-dir", str(smoke.manifest.runtime), "--year",
         "2026", "--as-of", "2026-04-06T16:30:00+08:00", "--exchange", "SSE", "--incremental",
         "--dataset", "adj_factor", "--dataset", "price", "--dataset", "stk_limit"]
    )  # fmt: skip

    code, _out, err = _drive(smoke, capsys, "remedy", line, at=survivor_day.RUN_CLOCK)

    assert code == 1
    assert "an injected provider failure" in err
    assert len(sink.payloads) == 2
    spent = _spent(smoke)
    assert (spent.total, spent.discarded) == (2, None)


def test_the_weekly_report_is_forward_report_s_and_the_morning_the_day_s_with_its_flag(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    seeded: Path,  # noqa: F811
) -> None:
    """Which command each kind runs, and with what. `test_a_morning_spends_one_request_and_a_
    weekly_report_none` cannot tell a weekly report from the daily command given its arguments:
    at 10:00 of the next day the daily command reprints the complete evening -- no request,
    exit 0 -- and the ledger still names the invocation `weekly`."""
    smoke = _smoke(tmp_path, monkeypatch, seeded, epoch=survivor_day.EPOCH)
    reported: list[list[str]] = []

    def report(argv: Sequence[str], *, clock: Callable[[], datetime]) -> int:
        del clock
        reported.append(list(argv))
        print("a stand-in weekly report")
        return 0

    monkeypatch.setattr(driver.forward_report, "main", report)
    days = _standing_in(monkeypatch, lambda: 0)
    paths = ["--runtime-dir", str(smoke.manifest.runtime)]
    paths += [
        "--registration",
        str(smoke.manifest.registration),
        "--repo",
        str(smoke.manifest.pinned),
    ]
    as_of = ["--as-of", "2026-04-07T10:00:00+08:00"]

    assert _drive(smoke, capsys, "weekly", "--", *as_of, at=NOW)[0] == 0
    assert _drive(smoke, capsys, "morning", "--", *as_of, at=NOW)[0] == 0
    assert _drive(smoke, capsys, "evening", "--", *as_of, at=NOW)[0] == 0

    assert reported == [[*paths, *as_of]]
    assert days == [[*paths, *as_of, "--morning-recheck"], [*paths, *as_of]]
    assert "a stand-in weekly report" in (smoke.root / env.LOGS_NAME / "0001-weekly.out").read_text(
        encoding="utf-8"
    )


def test_the_driver_and_the_cap_share_one_daily_selection_and_one_cli() -> None:
    """Requirement 5: the cap is put on `cli._panel_transport` by `p7_smoke_requests`'s
    `daily_selection`; it binds the driver's day only when that is the driver's own module, whose
    `cli` is the one `panel build` runs."""
    assert caps.daily_selection is driver.daily is daily
    assert env.daily_selection is daily
    assert driver.forward_report.daily_selection is daily
    assert driver.cli is daily.cli is cli


def test_a_cap_put_on_another_daily_selection_is_refused_before_anything_runs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    seeded: Path,  # noqa: F811
) -> None:
    """Requirement 5: a `p7_smoke_requests` holding another `daily_selection` -- one loaded from
    another path, say -- would count another `cli`'s requests and none of the day's."""
    smoke = _smoke(tmp_path, monkeypatch, seeded, epoch=survivor_day.EPOCH)
    monkeypatch.setattr(caps, "daily_selection", types.ModuleType("daily_selection"))
    smoke.market.today = survivor_day.DAY

    code, _out, err = _drive(
        smoke,
        capsys,
        "evening",
        "--",
        "--as-of",
        survivor_day.DAY_AS_OF.isoformat(),
        at=survivor_day.RUN_CLOCK,
    )

    assert code == driver.REFUSED_EXIT
    assert any("daily_selection" in line for line in err)
    assert smoke.market.payloads == []
    assert [
        event["event"] for event in caps.RequestLedger(smoke.root / env.LEDGER_NAME).events()
    ] == ["prepared"]


def test_status_reads_what_was_spent_as_at_least_while_an_invocation_has_no_end(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    seeded: Path,  # noqa: F811
) -> None:
    """Requirement 6: an interruption between the `discarded` and `end` appends loses that
    invocation's count, and a killed one never wrote it: `status` says `at least`."""
    smoke = _smoke(tmp_path, monkeypatch, seeded, epoch=survivor_day.EPOCH)
    smoke.market.today = survivor_day.DAY
    _drive(
        smoke,
        capsys,
        "evening",
        "--",
        "--as-of",
        survivor_day.DAY_AS_OF.isoformat(),
        at=survivor_day.RUN_CLOCK,
    )
    total = sum(STEADY_SURVIVOR.values())

    code, out, _err = _drive(smoke, capsys, "status", at=survivor_day.RUN_CLOCK)

    assert code == 0
    spent = next(line for line in out.splitlines() if line.startswith("spent"))
    assert f"{total} of 190" in spent and "at least" not in spent
    assert not any(line.startswith("unclosed") for line in out.splitlines())
    assert any(line.startswith("  #0001 evening") and "exit 0" in line for line in out.splitlines())

    caps.RequestLedger(smoke.root / env.LEDGER_NAME).append(
        {
            "event": "start",
            "id": 2,
            "kind": "evening",
            "arguments": [],
            "phase": 1,
            "cap": 190,
            "allowance": 190 - total,
            "spent_before": total,
            "at": survivor_day.RUN_CLOCK.isoformat(),
        }
    )
    code, out, _err = _drive(smoke, capsys, "status", at=survivor_day.RUN_CLOCK)

    assert code == 0
    spent = next(line for line in out.splitlines() if line.startswith("spent"))
    assert f"at least {total} of 190" in spent
    assert any(line.startswith("unclosed") and "[2]" in line for line in out.splitlines())
    assert any(line.startswith("  #0002 evening") and "no end" in line for line in out.splitlines())


def _research_panel(home: Path) -> Path:
    """A tiny tree shaped like P6's panel, where the prepare looks for it by default."""
    panel = home / "openalpha-research" / "panel"
    (panel / "daily" / "2026").mkdir(parents=True)
    (panel / "daily" / "2026" / "data.parquet").write_bytes(b"\x00" * 64)
    (panel / "catalog.duckdb").write_bytes(b"catalog")
    return panel


def _prepare_through_the_driver(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    checkout: Path,
) -> tuple[int, str, str, list[Path]]:
    """`prepare` as the user runs it, from `checkout` standing in for the driver's own checkout,
    with `HOME` in `tmp_path` and a policy that allows `tmp_path`."""
    home = tmp_path / "home"
    _research_panel(home)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setattr(driver, "REPOSITORY", checkout)
    monkeypatch.setattr(daily, "REPOSITORY", checkout)
    monkeypatch.setattr(daily, "_run_sync", env_tests._offline_sync)
    monkeypatch.setattr(env, "apfs_clone", shutil.copytree)
    policed: list[Path] = []

    def default(cls: Any, main_checkout: Path) -> Any:
        policed.append(main_checkout)
        return cls(
            temporary_roots=(tmp_path / "volatile",),
            main_checkout=main_checkout.resolve(),
            research_runtime=(Path.home() / "openalpha-research").resolve(),
        )

    monkeypatch.setattr(env.PathPolicy, "default", classmethod(default))
    code = driver.main(
        ["prepare", "--smoke-root", str(tmp_path / "smoke"), "--epoch", "2026-04-03",
         "--phase1-cap", "190", "--phase2-cap", "460", "--uv", "uv"],
        clock=lambda: NOW,
    )  # fmt: skip
    out, err = capsys.readouterr()
    assert env_tests.SENTINEL not in out + err
    return code, out, err, policed


def test_prepare_clones_its_own_checkout_and_p6_s_panel_by_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Requirement 7: the registration is written by `p7_smoke_env.py` itself, which is no bound
    path, so the code that writes it must be the code that is cloned -- the driver's own checkout;
    and the panel is P6's, `~/openalpha-research/panel`, never the repository's `runtime/panel`."""
    assert Path(driver.__file__).resolve().parents[1] == driver.REPOSITORY
    source = env_tests._source_repository(tmp_path)

    code, out, err, policed = _prepare_through_the_driver(tmp_path, monkeypatch, capsys, source)

    assert code == 0, err
    manifest = env.SmokeManifest.read(tmp_path / "smoke")
    assert manifest.commit == head(source)
    assert git(manifest.repo, "rev-parse", "HEAD^").strip() == head(source)
    assert manifest.source_panel == (tmp_path / "home" / "openalpha-research" / "panel").resolve()
    assert policed == [source]
    assert (manifest.caps[1], manifest.caps[2]) == (190, 460)
    for shown in (
        str(manifest.root),
        manifest.commit,
        manifest.registration_sha256,
        manifest.config_id,
        manifest.epoch.isoformat(),
        f"{manifest.panel_files} file(s)",
        "p7_smoke.py",
        "smoke evening",
    ):
        assert shown in out, shown
    assert "shallow" not in err


def test_prepare_refuses_a_checkout_whose_smoke_scripts_are_not_what_head_holds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Requirement 7, the other half: `p7_smoke_env.py` writes the registration and is no bound
    path, so the env's own check of the bound code passes over it; the driver refuses a checkout
    in which it -- or the driver, or the cap -- differs from `HEAD`, before anything is made."""
    source = env_tests._source_repository(tmp_path)
    (source / "scripts" / "p7_smoke_env.py").write_text("SMOKE = 'edited'\n", encoding="utf-8")

    code, _out, err, _policed = _prepare_through_the_driver(tmp_path, monkeypatch, capsys, source)

    assert code == driver.REFUSED_EXIT
    assert "scripts/p7_smoke_env.py" in err
    assert not (tmp_path / "smoke").exists()


def test_prepare_from_a_shallow_checkout_warns_once_and_prepares_as_before(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Requirement 9: the main checkout is a shallow repository, from which `git clone --local`
    falls back to a transport clone. The prepare says so in one line and does what it did."""
    origin = env_tests._source_repository(tmp_path / "origin")
    shallow = tmp_path / "shallow"
    git(tmp_path, "clone", "-q", "--depth", "1", origin.as_uri(), str(shallow))
    (shallow / ".env").write_text(f"TUSHARE_TOKEN={env_tests.SENTINEL}\n", encoding="utf-8")
    assert git(shallow, "rev-parse", "--is-shallow-repository").strip() == "true"

    code, out, err, _policed = _prepare_through_the_driver(tmp_path, monkeypatch, capsys, shallow)

    assert code == 0, err
    warnings = [line for line in err.splitlines() if "shallow" in line]
    assert len(warnings) == 1 and warnings[0].startswith("smoke: warning:")
    manifest = env.SmokeManifest.read(tmp_path / "smoke")
    assert manifest.commit == head(shallow) == head(origin)
    assert manifest.registration_sha256 in out


def test_a_path_flag_is_refused_however_it_is_spelt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    seeded: Path,  # noqa: F811
) -> None:
    """The daily command's parser takes an abbreviation of a long option, and its last
    occurrence: `--runtime /elsewhere` after the driver's `--runtime-dir` would run the day in
    another runtime directory."""
    smoke = _smoke(tmp_path, monkeypatch, seeded, epoch=survivor_day.EPOCH)
    elsewhere = str(tmp_path / "elsewhere")

    for extra in (
        ["--runtime", elsewhere],
        [f"--runtime-dir={elsewhere}"],
        ["--registr", elsewhere],
        ["--rep", elsewhere],
        ["--morning"],
        ["--pin-worktree", elsewhere],
        ["--factor-gaps=2026-01-05"],
    ):
        code, _out, err = _drive(smoke, capsys, "evening", "--", *extra, at=survivor_day.RUN_CLOCK)
        assert code == driver.REFUSED_EXIT, extra
        assert any("the driver's to set" in line for line in err), extra

    code, _out, err = _drive(smoke, capsys, "weekly", "--", "--repo", elsewhere, at=NOW)
    assert code == driver.REFUSED_EXIT
    assert smoke.market.payloads == []
    events = caps.RequestLedger(smoke.root / env.LEDGER_NAME).events()
    assert [event["event"] for event in events] == ["prepared"]


def test_a_remedy_that_joins_an_option_to_its_value_is_refused(tmp_path: Path) -> None:
    """`openalpha` takes `--runtime-dir=DIR` too, and the last one given: a remedy that adds one
    after the checked `--runtime-dir` would build another runtime directory."""
    paths = _paths(tmp_path)
    build = ["openalpha", "panel", "build", "--runtime-dir", str(paths["runtime"]), "--year"]
    build += ["2026", "--as-of", "2026-10-22T16:30:00+08:00", "--exchange", "SSE", "--incremental"]
    build += ["--dataset", "adj_factor", "--dataset", "price", "--dataset", "stk_limit"]
    joined = daily.pinned_command([*build, f"--runtime-dir={Path.home() / 'openalpha-research'}"])

    with pytest.raises(driver.RemedyError, match="its own word"):
        driver.parse_remedy(joined, **paths)


def test_a_remedy_is_read_from_stdin_and_refused_or_run_as_any_other(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    seeded: Path,  # noqa: F811
) -> None:
    """`pbpaste | smoke remedy -`: the pasted line, with the indent and newline it was printed
    with; the whole-year rebuild is refused before anything is sent or written."""
    smoke = _smoke(tmp_path, monkeypatch, seeded, epoch=survivor_day.EPOCH)
    build = ["openalpha", "panel", "build", "--runtime-dir", str(smoke.manifest.runtime)]
    build += ["--year", "2026", "--as-of", "2026-04-06T16:30:00+08:00", "--exchange", "SSE"]
    build += ["--dataset", "adj_factor", "--dataset", "price", "--dataset", "stk_limit"]
    monkeypatch.setattr(sys, "stdin", io.StringIO("  " + daily.pinned_command(build) + "\n"))

    code, _out, err = _drive(smoke, capsys, "remedy", "-", at=survivor_day.RUN_CLOCK)

    assert code == driver.REFUSED_EXIT
    assert any("whole-year rebuild" in line for line in err)
    assert smoke.market.payloads == []
    events = caps.RequestLedger(smoke.root / env.LEDGER_NAME).events()
    assert [event["event"] for event in events] == ["prepared"]


def test_the_smoke_root_is_the_pinned_worktree_s_own_when_it_is_not_given(tmp_path: Path) -> None:
    root = tmp_path / "r1"
    script = root / env.PINNED_NAME / "scripts" / "p7_smoke.py"
    script.parent.mkdir(parents=True)

    assert driver.default_smoke_root(script) is None
    (root / env.MANIFEST_NAME).write_text("{}\n", encoding="utf-8")
    assert driver.default_smoke_root(script) == root
    assert driver.default_smoke_root(tmp_path / "main" / "scripts" / "p7_smoke.py") is None


# --- the review of 8c49321 (fix round 1) ---------------------------------------------------------


def _line(smoke: Smoke, *build: str) -> str:
    """A printed `panel build` of this smoke's runtime, with `build` after `--runtime-dir`."""
    runtime = str(smoke.manifest.runtime)
    return daily.pinned_command(["openalpha", "panel", "build", "--runtime-dir", runtime, *build])


_BATCH: Final[tuple[str, ...]] = (
    "--year", "2026", "--as-of", "2026-04-06T16:30:00+08:00", "--exchange", "SSE",
    "--incremental", "--dataset", "adj_factor", "--dataset", "price", "--dataset", "stk_limit",
)  # fmt: skip
"""The oldest batch's `panel build` after its `--runtime-dir`, as step 2 prints it."""


def _invoking(monkeypatch: pytest.MonkeyPatch, body: Callable[[], Any]) -> list[Sequence[str]]:
    """`daily_selection.invoke` -- what runs a `panel build` or `factor build` -- replaced by
    `body`; the arguments the driver passed are kept."""
    seen: list[Sequence[str]] = []

    def invoke(arguments: Sequence[str]) -> Any:
        seen.append(list(arguments))
        return body()

    monkeypatch.setattr(daily, "invoke", invoke)
    return seen


def _failed(code: int) -> Any:
    return daily.Invocation(exit_code=code, stdout="", stderr="an injected failure\n")


def test_a_discarded_root_whose_discarded_line_was_taken_out_is_refused(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    seeded: Path,  # noqa: F811
) -> None:
    """I1: `Spent.discarded` only says a `discarded` line exists. An evening that ended on a
    fault (or on the cap) wrote one just before its `end`; without it, the `end` that records
    the fault is held to it, and the root stays refused -- the evening, `phase` and `status`."""
    smoke = _smoke(tmp_path, monkeypatch, seeded, epoch=survivor_day.EPOCH)
    sink = _sink(monkeypatch)
    calls: list[int] = []

    def body() -> int:
        calls.append(1)
        _send(1)
        if len(calls) == 1:
            raise KeyboardInterrupt
        return 0

    _standing_in(monkeypatch, body)
    assert _drive(smoke, capsys, "evening", at=survivor_day.RUN_CLOCK)[0] == driver.INTERRUPTED_EXIT
    ledger = smoke.root / env.LEDGER_NAME
    lines = ledger.read_bytes().split(b"\n")
    kept = [line for line in lines if b'"event": "discarded"' not in line]
    assert len(kept) == len(lines) - 1
    ledger.write_bytes(b"\n".join(kept))
    assert _spent(smoke).discarded is None  # the ledger's own reading cannot tell

    for command in (["evening"], ["phase", "--to", "2", "--reason", "go"], ["status"]):
        code, out, err = _drive(smoke, capsys, *command, at=survivor_day.RUN_CLOCK)
        assert code == driver.REFUSED_EXIT, command
        assert any("discarded" in line for line in [*err, *out.splitlines()]), command

    assert (len(calls), len(sink.payloads)) == (1, 1)
    assert ledger.read_bytes() == b"\n".join(kept)


def test_an_end_on_a_fault_or_the_cap_is_held_to_its_discarded_line(tmp_path: Path) -> None:
    """I1, the check alone: a panel writer's `end` with a fault or a stopped request needs the
    `discarded` line `run_capped` writes just before it; another kind's does not."""
    logs = tmp_path / "logs"

    def ledger(kind: str, end: dict[str, Any], *, discarded: bool) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = [{"event": "prepared"}, {**_start(1, 0), "kind": kind}]
        if discarded:
            events.append({"event": "discarded", "reason": f"invocation 1 ({kind}) wrote ..."})
        return [*events, {**_end(1, 1), **end}]

    for kind in ("evening", "panel-build"):
        for end in ({"fault": "KeyboardInterrupt", "reached": None}, {"reached": "daily"}):
            assert driver.ledger_problem(ledger(kind, end, discarded=True), logs=logs) is None
            problem = driver.ledger_problem(ledger(kind, end, discarded=False), logs=logs)
            assert "discarded" in str(problem), (kind, end)
    for kind in ("morning", "weekly", "factor-build"):
        end = {"fault": "KeyboardInterrupt", "reached": "namechange"}
        assert driver.ledger_problem(ledger(kind, end, discarded=False), logs=logs) is None
    clean = {"fault": None, "reached": None}
    assert driver.ledger_problem(ledger("evening", clean, discarded=False), logs=logs) is None


@pytest.mark.parametrize(
    ("kind", "sent", "forwarded", "discarded", "caps_"),
    [
        ("weekly", 1, 0, False, None),
        ("factor-build", 1, 0, False, None),
        ("morning", 4, 3, False, None),
        ("panel-build", 81, 80, True, {1: 80, 2: 460}),
    ],
)
def test_each_kind_s_ceiling_stops_it_and_only_a_panel_writer_discards_the_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    seeded: Path,  # noqa: F811
    kind: str,
    sent: int,
    forwarded: int,
    discarded: bool,
    caps_: dict[int, int] | None,
) -> None:
    """I2, `RULES`: a weekly report and a factor build send nothing (ceiling 0), a morning three
    (ceiling 3); a panel build has no ceiling of its own and stops at the phase's cap -- and is
    the one of these that discards the root."""
    smoke = _smoke(tmp_path, monkeypatch, seeded, epoch=survivor_day.EPOCH, caps_=caps_)
    sink = _sink(monkeypatch)

    def body() -> int:
        _send(sent)
        return 0

    if kind == "weekly":

        def report(argv: Sequence[str], *, clock: Callable[[], datetime]) -> int:
            return body()

        monkeypatch.setattr(driver.forward_report, "main", report)
        command = ["weekly"]
    elif kind == "morning":
        _standing_in(monkeypatch, body)
        command = ["morning"]
    else:
        _invoking(monkeypatch, body)
        factors = ["openalpha", "factor", "build", "--runtime-dir", str(smoke.manifest.runtime)]
        factors += ["--factor", "return_vol_60/v1", "--tier", "processed", "--year", "2026"]
        factors += ["--as-of", "2026-04-06T16:30:00+08:00", "--exchange", "SSE"]
        line = _line(smoke, *_BATCH) if kind == "panel-build" else daily.pinned_command(factors)
        command = ["remedy", line]

    code, _out, err = _drive(smoke, capsys, *command, at=survivor_day.RUN_CLOCK)

    assert code == caps.CAP_EXIT
    assert len(sink.payloads) == forwarded
    assert any(line.startswith("REQUEST CAP REACHED") for line in err)
    assert (_spent(smoke).discarded is not None) is discarded
    (end,) = _ends(smoke)
    assert (end["kind"], end["count"], end["reached"]) == (kind, forwarded, "daily")


@pytest.mark.parametrize(
    ("kind", "caps_", "needs"),
    [
        ("evening", {1: 21, 2: 460}, 22),
        ("panel-build", {1: 76, 2: 460}, 77),
        ("morning", {1: 0, 2: 460}, 1),
    ],
)
def test_an_invocation_short_of_its_headroom_is_refused_before_anything_is_written(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    seeded: Path,  # noqa: F811
    kind: str,
    caps_: dict[int, int],
    needs: int,
) -> None:
    """I2, `RULES`' headroom: an evening needs 22 of the cap to start, a panel build
    7 x (catch_up_bound + 1) = 77, a morning 1."""
    smoke = _smoke(tmp_path, monkeypatch, seeded, epoch=survivor_day.EPOCH, caps_=caps_)
    sink = _sink(monkeypatch)
    days = _standing_in(monkeypatch, lambda: 0)
    builds = _invoking(monkeypatch, lambda: daily.Invocation(exit_code=0, stdout="", stderr=""))
    command = ["remedy", _line(smoke, *_BATCH)] if kind == "panel-build" else [kind]

    code, _out, err = _drive(smoke, capsys, *command, at=survivor_day.RUN_CLOCK)

    assert code == driver.REFUSED_EXIT
    assert any(f"needs {needs} to start" in line for line in err)
    assert (days, builds, sink.payloads) == ([], [], [])
    events = caps.RequestLedger(smoke.root / env.LEDGER_NAME).events()
    assert [event["event"] for event in events] == ["prepared"]


@pytest.mark.parametrize("own", [4, 5, 6])
def test_a_build_that_fails_with_one_of_the_driver_s_own_codes_exits_7(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    seeded: Path,  # noqa: F811
    own: int,
) -> None:
    """I3: `panel build` exits 4 on a provider failure, 5 on an internal error and 6 on a busy
    catalog -- the driver's `CAP_EXIT`, `REFUSED_EXIT` and the code after them. A remedy that
    fails so exits `COMMAND_FAILED_EXIT`, and the closing line names the command's own code."""
    smoke = _smoke(tmp_path, monkeypatch, seeded, epoch=survivor_day.EPOCH)
    sink = _sink(monkeypatch)

    def body() -> Any:
        _send(2)
        return _failed(own)

    _invoking(monkeypatch, body)
    line = _line(smoke, *_BATCH)

    code, _out, err = _drive(smoke, capsys, "remedy", line, at=survivor_day.RUN_CLOCK)

    assert code == driver.COMMAND_FAILED_EXIT == 7
    assert any(f"exited 7 (the command's own {own})" in line for line in err)
    assert not any("REQUEST CAP REACHED" in line for line in err)
    assert len(sink.payloads) == 2
    assert _spent(smoke).discarded is None


@pytest.mark.parametrize(
    ("build", "named"),
    [
        (("--year", "2026", "--as-of", "--incremental", "--exchange", "SSE", "--dataset", "price"),
         "whole-year rebuild"),
        (("--year", "2026", "--incremental", "--dataset", "price", "--runtime-dir", "elsewhere"),
         "runtime"),
    ],
)  # fmt: skip
def test_a_remedy_is_judged_by_the_options_the_command_parses(
    tmp_path: Path, build: tuple[str, ...], named: str
) -> None:
    """M2: `--as-of --incremental` gives `--as-of` the value `--incremental` and leaves the build
    a whole rebuild; a second `--runtime-dir` is the one the command takes."""
    paths = _paths(tmp_path)
    line = daily.pinned_command(
        ["openalpha", "panel", "build", "--runtime-dir", str(paths["runtime"]), *build]
    )

    with pytest.raises(driver.RemedyError, match=named):
        driver.parse_remedy(line, **paths)


def test_a_remedy_s_runtime_spelt_with_a_tilde_is_not_this_smoke_s(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """M2: the command takes `~/...` as it is written -- a directory named `~` -- so a remedy
    that spells this smoke's runtime that way is another runtime."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    paths = _paths(tmp_path)
    tilde = "~/" + paths["runtime"].relative_to(tmp_path).as_posix()
    assert Path(tilde).expanduser() == paths["runtime"]
    line = daily.pinned_command(["openalpha", "panel", "build", "--runtime-dir", tilde, *_BATCH])

    with pytest.raises(driver.RemedyError, match="runtime"):
        driver.parse_remedy(line, **paths)


@pytest.mark.skipif(not hasattr(signal, "SIGHUP"), reason="POSIX signals")
def test_a_signal_the_caller_ignores_stays_ignored(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    seeded: Path,  # noqa: F811
) -> None:
    """M3: under `nohup` SIGHUP is ignored; closing the terminal must not end the evening."""
    smoke = _smoke(tmp_path, monkeypatch, seeded, epoch=survivor_day.EPOCH)
    previous = signal.signal(signal.SIGHUP, signal.SIG_IGN)
    during: list[Any] = []

    def body() -> int:
        during.append(signal.getsignal(signal.SIGHUP))
        os.kill(os.getpid(), signal.SIGHUP)
        return 0

    _standing_in(monkeypatch, body)
    try:
        code, _out, err = _drive(smoke, capsys, "evening", at=survivor_day.RUN_CLOCK)
        after = signal.getsignal(signal.SIGHUP)
    finally:
        signal.signal(signal.SIGHUP, previous)

    assert code == 0, err
    assert during == [signal.SIG_IGN] and after == signal.SIG_IGN
    assert _spent(smoke).discarded is None


class _Closed(io.TextIOBase):
    """A terminal whose reader went away: `| head` that has had its lines."""

    def __init__(self) -> None:
        super().__init__()
        self.attempts = 0

    def writable(self) -> bool:
        return True

    def write(self, text: str) -> int:
        self.attempts += 1
        raise BrokenPipeError(32, "Broken pipe")


def test_a_terminal_that_went_away_ends_nothing_and_the_log_keeps_every_line(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    seeded: Path,  # noqa: F811
) -> None:
    """M4: the log is written first, and a terminal that cannot be written to is written to no
    more -- the evening goes on, its root kept, every line in its log."""
    smoke = _smoke(tmp_path, monkeypatch, seeded, epoch=survivor_day.EPOCH)

    def body() -> int:
        print("the first chunk")
        print("the second chunk")
        return 0

    _standing_in(monkeypatch, body)
    closed = _Closed()
    monkeypatch.setattr(sys, "stdout", closed)

    code = driver.main(["evening", "--smoke-root", str(smoke.root)], clock=lambda: NOW)

    assert code == 0, capsys.readouterr().err
    assert closed.attempts == 1
    log = (smoke.root / env.LOGS_NAME / "0001-evening.out").read_text(encoding="utf-8")
    assert log == "the first chunk\nthe second chunk\n"
    assert _spent(smoke).discarded is None


def test_an_interrupt_says_only_what_the_ledger_can_hold(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    seeded: Path,  # noqa: F811
) -> None:
    """M5: an interruption between `start` and the call, or between `discarded` and `end`, leaves
    the invocation without an end: the message does not claim one was recorded."""
    smoke = _smoke(tmp_path, monkeypatch, seeded, epoch=survivor_day.EPOCH)

    def body() -> int:
        raise KeyboardInterrupt

    _standing_in(monkeypatch, body)

    code, _out, err = _drive(smoke, capsys, "evening", at=survivor_day.RUN_CLOCK)

    assert code == driver.INTERRUPTED_EXIT
    (message,) = [line for line in err if line.startswith("smoke: interrupted")]
    assert "recorded its end in the ledger, and" not in message
    assert "`status`" in message and "no end" in message


@pytest.mark.parametrize(
    "event",
    [
        {"event": "phase", "phase": 3, "reason": "a hand-written line"},
        {"event": "start", "id": 1, "kind": "evening", "arguments": [], "phase": 1},
    ],
)
def test_a_ledger_event_the_driver_cannot_read_refuses_the_root_by_name(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    seeded: Path,  # noqa: F811
    event: dict[str, Any],
) -> None:
    """M6: a phase with no cap, or a `start` without its running total, is refused as a ledger
    the driver cannot read (exit 5), not let out as a bare `KeyError`."""
    smoke = _smoke(tmp_path, monkeypatch, seeded, epoch=survivor_day.EPOCH)
    days = _standing_in(monkeypatch, lambda: 0)
    caps.RequestLedger(smoke.root / env.LEDGER_NAME).append(event)

    for command in (["evening"], ["status"], ["phase", "--to", "2", "--reason", "go"]):
        code, _out, err = _drive(smoke, capsys, *command, at=survivor_day.RUN_CLOCK)
        assert code == driver.REFUSED_EXIT, command
        assert any(line.startswith("smoke: refused:") for line in err), command
    assert days == []


class _Interrupting:
    """The market, with the signal `number` raised at the `at`-th request -- inside `panel
    build`, where `cli._panel_command` lets a `KeyboardInterrupt` through and click turns it into
    `Abort`, which the day's step 2 reports as one of its refusals."""

    def __init__(self, market: Any, number: int, *, at: int) -> None:
        self.market, self.number, self.at, self.seen = market, number, at, 0

    def post(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.seen += 1
        if self.seen == self.at:
            os.kill(os.getpid(), self.number)
        result: dict[str, Any] = self.market.post(payload)
        return result


@pytest.mark.skipif(not hasattr(signal, "SIGHUP"), reason="POSIX signals")
@pytest.mark.parametrize("name", ["SIGINT", "SIGTERM"])
@pytest.mark.parametrize("kind", ["evening", "remedy"])
def test_an_interrupt_inside_a_real_panel_build_discards_the_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    seeded: Path,  # noqa: F811
    name: str,
    kind: str,
) -> None:
    """N1 (found in this round): Ctrl-C or a SIGTERM while the real `panel build` fetches. The
    evening caught it as step 2's refusal and exited 1, and the root was kept on a panel copy the
    build had been writing; a remedy let click's `Abort` out of the driver. Both are an
    interruption: exit 130, and the root discarded. A recording handler of the test's own stands
    behind the driver's, so a driver that installed none kills nothing."""
    smoke = _smoke(tmp_path, monkeypatch, seeded, epoch=survivor_day.EPOCH)
    smoke.market.today = survivor_day.DAY
    number = getattr(signal, name)
    transport = _Interrupting(smoke.market, number, at=3)
    monkeypatch.setattr(cli, "_panel_transport", lambda: transport)
    reached: list[str] = []
    previous = signal.signal(number, lambda *_: reached.append("the test's own handler"))
    command = (
        ["evening", "--", "--as-of", survivor_day.DAY_AS_OF.isoformat()]
        if kind == "evening"
        else ["remedy", _line(smoke, *_BATCH)]
    )
    try:
        code, _out, err = _drive(smoke, capsys, *command, at=survivor_day.RUN_CLOCK)
    finally:
        signal.signal(number, previous)

    assert reached == []
    assert code == driver.INTERRUPTED_EXIT, err
    (end,) = _ends(smoke)
    assert end["fault"] == "KeyboardInterrupt", (end, err)
    assert _spent(smoke).discarded is not None


def test_a_remedy_its_command_would_refuse_as_a_usage_error_is_refused_before_anything_is_written(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    seeded: Path,  # noqa: F811
) -> None:
    """N2 (found in this round): typer runs a click of its own, and `daily_selection.invoke`
    catches the other click's `ClickException` -- so an unknown option escaped the build as
    typer's `NoSuchOption`, which `run_capped` recorded as a fault: the root was discarded and
    the driver ended on a traceback, with no request sent. The driver parses a remedy as the
    command does, strictly too, and refuses one it would refuse."""
    smoke = _smoke(tmp_path, monkeypatch, seeded, epoch=survivor_day.EPOCH)
    line = _line(smoke, *_BATCH, "--no-such-option")

    code, _out, err = _drive(smoke, capsys, "remedy", line, at=survivor_day.RUN_CLOCK)

    assert code == driver.REFUSED_EXIT
    assert any("--no-such-option" in entry for entry in err)
    assert smoke.market.payloads == []
    events = caps.RequestLedger(smoke.root / env.LEDGER_NAME).events()
    assert [event["event"] for event in events] == ["prepared"]


@pytest.mark.skipif(not hasattr(signal, "SIGHUP"), reason="POSIX signals")
def test_an_interrupt_the_command_turned_into_another_fault_is_an_interrupt_and_is_forgotten(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    seeded: Path,  # noqa: F811
) -> None:
    """N1: a command that catches the `KeyboardInterrupt` and raises something else in its place
    was interrupted all the same -- and the next invocation, not interrupted, is not taken for
    one. A morning, which keeps its root, so there is a next one."""
    smoke = _smoke(tmp_path, monkeypatch, seeded, epoch=survivor_day.EPOCH)
    reached: list[str] = []
    previous = signal.signal(signal.SIGTERM, lambda *_: reached.append("the test's own handler"))
    calls: list[int] = []

    def body() -> int:
        calls.append(1)
        if len(calls) == 1:
            try:
                os.kill(os.getpid(), signal.SIGTERM)
                time.sleep(5)
            except KeyboardInterrupt as error:
                raise RuntimeError("the command's own fault, in the interrupt's place") from error
        return 0

    _standing_in(monkeypatch, body)
    try:
        first = _drive(smoke, capsys, "morning", at=survivor_day.RUN_CLOCK)
        second = _drive(smoke, capsys, "morning", at=survivor_day.RUN_CLOCK)
    finally:
        signal.signal(signal.SIGTERM, previous)

    assert reached == []
    assert first[0] == driver.INTERRUPTED_EXIT, first[2]
    assert second[0] == 0, second[2]
    assert [end["fault"] for end in _ends(smoke)] == ["KeyboardInterrupt", None]
    assert _spent(smoke).discarded is None


# --- the re-review of b0fd0b9 (fix round 2) ------------------------------------------------------


_HANDLER_UNDER_A_HELD_LOCK: Final[str] = """
import os, signal, sys
sys.path.insert(0, sys.argv[1])
import p7_smoke as driver

flag = driver._INTERRUPTED
held = [value for value in getattr(flag, "__dict__", {}).values() if hasattr(value, "acquire")]
with driver._signals_as_exit():
    try:
        for lock in held:  # what the main thread holds while it clears or reads the flag
            lock.acquire()
        os.kill(os.getpid(), signal.SIGTERM)
        for _ in range(10_000_000):
            pass
    except KeyboardInterrupt:
        print("interrupted", flush=True)
"""


@pytest.mark.skipif(not hasattr(signal, "SIGHUP"), reason="POSIX signals")
def test_the_signal_handler_takes_no_lock_the_interrupted_code_may_hold() -> None:
    """R1: a `threading.Event` flag takes its own non-reentrant lock in `set()`, so a signal
    handled while the main thread was inside `clear()` hung the driver for good. The flag is
    written and read without a lock: the handler runs while every lock the flag holds is held."""
    environment = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    environment |= {"PYTHONDONTWRITEBYTECODE": "1", "PYTHONPATH": str(ROOT / "src")}
    try:
        finished = subprocess.run(
            [sys.executable, "-c", _HANDLER_UNDER_A_HELD_LOCK, str(ROOT / "scripts")],
            cwd=ROOT,
            env=environment,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
    except subprocess.TimeoutExpired:
        pytest.fail("the driver's signal handler hung on a lock its flag holds")

    assert finished.returncode == 0, finished.stderr
    assert finished.stdout.splitlines() == ["interrupted"]
