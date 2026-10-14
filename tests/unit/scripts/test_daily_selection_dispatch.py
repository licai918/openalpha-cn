"""`scripts/daily_selection.py`: the registration's `kind` decides which day runs (`V2-P7-008`).

A registered configuration without a `kind` is the P6 book and runs the P6 day exactly as it did
-- `test_daily_selection.py` runs unmodified and is the measurement of that. One whose `kind` is
`survivor_book/v1` is read by `strategy_survivor` after step 1's admission and dispatched to the
survivor day, which is a step-1 refusal until D5 wires it. Every other `kind`, and every survivor
registration that cannot be read, is refused at step 1 before any request.

The registrations here are committed into a throwaway repository and admitted through the real
`admit_registration`, with `test_daily_selection.py`'s own repository and binding helpers; the
transport is its scripted market, so nothing reaches the network.

**Why a fresh interpreter.** `strategy_survivor` imports `backtest/paper.py`, whose import installs
an audit hook that no process can remove, so the script imports it only for a registration that
names a `kind`. Whether a P6 day loads it can only be seen in a process that has not loaded it
already -- the test process has, through `test_strategy_survivor_config.py` and every paper test
-- so the P6 day and, as the control that the probe can see a load at all, the survivor dispatch
each run in a child interpreter (`modules_a_day_loads`), as
`tests/unit/runtime/test_contracts_import_isolation.py` checks an import in one.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Final

import pytest
import test_daily_selection as tds
from daily_market import Market
from offline_guard import refusing_outbound_traffic

from openalpha_cn.domain.survivor_book import SurvivorBookConfig

ROOT: Final[Path] = Path(__file__).resolve().parents[3]
daily = tds.daily
SEED: Final[int] = tds.grid.PROTOCOL_RANDOM_SEED
"""The registration's own seed: `registry.register` writes `grid.protocol_settings()`."""
BOOK: Final[SurvivorBookConfig] = SurvivorBookConfig(seed=SEED)
SURVIVOR_CONFIG: Final[dict[str, Any]] = {
    "kind": "survivor_book/v1",
    "survivor_book": BOOK.model_dump(mode="json", exclude_computed_fields=True),
    "adapter": {
        "schema_version": "survivor-adapter/v1",
        "epoch_session": "2026-01-16",
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
    },
}
"""A survivor registration's configuration, as the design memo writes one (Part D)."""
WATCHED: Final[tuple[str, ...]] = ("openalpha_cn.backtest.paper", "openalpha_cn.strategy_survivor")
NOT_WIRED: Final[str] = (
    "step 1 (registration) failed: the survivor daily day is not wired yet (V2-P7-008 D5)"
)
NO_REQUEST: Final[str] = "tushare requests   0 this run {}"


def _survivor_world(
    root: Path, monkeypatch: pytest.MonkeyPatch, config: dict[str, Any]
) -> tds.World:
    """A committed registration of `config` and the scripted market, bound as
    `test_daily_selection._world` binds them -- with no panel: every day here stops at step 1."""
    repo, registration = tds._repository(root, config)
    market = Market(open_days=tds.OPEN_2026)
    tds._bind(monkeypatch, repo, market)
    return tds.World(repo=repo, registration=registration, runtime=root / "runtime", market=market)


def _refused(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    config: dict[str, Any],
) -> tuple[int, str]:
    """Run the day on a registration of `config`; it must stop at step 1 having asked nothing
    and written nothing."""
    return _stopped_at_registration(_survivor_world(tmp_path, monkeypatch, config), capsys)


def _stopped_at_registration(
    world: tds.World, capsys: pytest.CaptureFixture[str]
) -> tuple[int, str]:
    code, _body, err = tds._run(world, capsys)
    assert world.market.payloads == []
    assert not world.runtime.exists()
    assert NO_REQUEST in err
    return code, err


# --- the step numbers --------------------------------------------------------------------------


def test_the_survivor_steps_number_in_their_own_order_and_the_p6_steps_as_before() -> None:
    assert daily.SURVIVOR_STEPS == (
        "registration",
        "panel update",
        "panel doctor",
        "factor build",
        "paper head",
        "facts",
        "prediction",
        "paper book",
        "summary",
    )
    survivor = daily.SURVIVOR_STEPS
    assert str(daily.StepFailedError("prediction", "x", steps=survivor)) == (
        "step 7 (prediction) failed: x"
    )
    assert str(daily.StepFailedError("paper book", "x", steps=survivor)) == (
        "step 8 (paper book) failed: x"
    )
    assert str(daily.StepFailedError("summary", "x", steps=survivor)) == (
        "step 9 (summary) failed: x"
    )
    assert str(daily.StepFailedError("summary", "x")) == "step 8 (summary) failed: x"
    assert str(daily.StepFailedError("prediction", "x")) == "step 7 (prediction) failed: x"
    # The P6 helpers the survivor day reuses raise with P6's numbers; those steps keep them.
    for step in ("registration", "panel update", "panel doctor", "factor build", "prediction"):
        assert survivor.index(step) == daily.STEPS.index(step)


def test_a_fault_no_survivor_step_anticipated_is_numbered_in_the_survivor_order() -> None:
    with (
        pytest.raises(daily.StepFailedError) as survivor,
        daily._step("facts", steps=daily.SURVIVOR_STEPS),
    ):
        raise KeyError("x")
    with pytest.raises(daily.StepFailedError) as p6, daily._step("summary"):
        raise KeyError("x")

    assert str(survivor.value) == "step 6 (facts) failed: an unexpected fault: KeyError: 'x'"
    assert str(p6.value) == "step 8 (summary) failed: an unexpected fault: KeyError: 'x'"
    assert survivor.value.exit_code == p6.value.exit_code == daily.DailyExit.step_failed


# --- the dispatch ------------------------------------------------------------------------------


def test_a_configuration_without_a_kind_is_not_a_survivor_registration(tmp_path: Path) -> None:
    registration = daily.Registration(
        path=tmp_path / "p6-registration.json",
        sha256="0" * 64,
        commit="0" * 40,
        code_commit="0" * 40,
        config=tds.grid.to_json_value(tds.CONFIG),
        config_id=tds.grid.config_id(tds.CONFIG),
        seed=SEED,
        settings={},
    )
    assert daily.survivor_registration(registration) is None


def test_a_survivor_registration_is_admitted_and_dispatched_to_the_survivor_day(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Admitted end to end -- committed, bound to its code, its `config_id` re-derived -- read
    back as the book it registered, and handed to the survivor day, which refuses until D5 with
    exit 1 and the requests spent, as every refusal prints them."""
    world = _survivor_world(tmp_path, monkeypatch, SURVIVOR_CONFIG)

    survivor = daily.survivor_registration(daily.admit_registration(world.registration, world.repo))
    assert survivor is not None
    assert survivor.book == BOOK
    assert survivor.book.config_id == BOOK.config_id
    assert survivor.adapter.max_panel_catch_up_sessions == 10

    code, err = _stopped_at_registration(world, capsys)

    assert code == 1 == daily.DailyExit.step_failed
    assert err.splitlines() == [NOT_WIRED, NO_REQUEST]


def test_an_unknown_kind_exits_2_before_any_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    code, err = _refused(
        tmp_path, monkeypatch, capsys, {**SURVIVOR_CONFIG, "kind": "survivor_book/v2"}
    )

    assert code == 2 == daily.DailyExit.no_registration
    assert "step 1 (registration) failed" in err
    assert "'survivor_book/v2'" in err


def test_a_key_beside_the_three_blocks_exits_2_naming_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    code, err = _refused(tmp_path, monkeypatch, capsys, {**SURVIVOR_CONFIG, "start": "2026-01-12"})

    assert code == 2
    assert "step 1 (registration) failed" in err
    assert "'start'" in err


@pytest.mark.parametrize(
    ("block", "change", "named"),
    [
        pytest.param(
            "survivor_book", {"holding_count": 0}, "survivor_book.holding_count", id="book"
        ),
        pytest.param(
            "adapter",
            {"max_panel_catch_up_sessions": 17},
            "adapter.max_panel_catch_up_sessions",
            id="adapter",
        ),
        pytest.param("adapter", {"min_coverage": 0}, "adapter.min_coverage", id="coverage"),
    ],
)
def test_a_block_that_does_not_validate_exits_2_naming_the_field(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    block: str,
    change: dict[str, Any],
    named: str,
) -> None:
    config = {**SURVIVOR_CONFIG, block: {**SURVIVOR_CONFIG[block], **change}}

    code, err = _refused(tmp_path, monkeypatch, capsys, config)

    assert code == 2
    assert "step 1 (registration) failed" in err
    assert named in err


def test_a_book_seed_that_is_not_the_registrations_exits_3(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The class of a `config_id` that does not match its own config: there is a registration,
    and it contradicts itself."""
    other = SurvivorBookConfig(seed=SEED + 1).model_dump(mode="json", exclude_computed_fields=True)

    code, err = _refused(tmp_path, monkeypatch, capsys, {**SURVIVOR_CONFIG, "survivor_book": other})

    assert code == 3 == daily.DailyExit.code_not_registered
    assert "step 1 (registration) failed" in err
    assert str(SEED + 1) in err


# --- what a P6 day loads -----------------------------------------------------------------------

_IN_A_FRESH_INTERPRETER: Final[str] = (
    "import json, sys\n"
    "import test_daily_selection_dispatch as here\n"
    "print(json.dumps(here.modules_a_day_loads(sys.argv[1], sys.argv[2])))\n"
)


def modules_a_day_loads(form: str, root: str) -> dict[str, Any]:
    """Run one day of `form` -- `"p6"` end to end on `test_daily_selection`'s January world, or
    `"survivor"` up to its dispatch -- under `root`, and say which of `WATCHED` the process held
    before it and after. Meant for a fresh interpreter: in the test process both are loaded."""
    before = [name for name in WATCHED if name in sys.modules]
    monkeypatch = pytest.MonkeyPatch()
    out, err = io.StringIO(), io.StringIO()
    try:
        with (
            refusing_outbound_traffic(),
            contextlib.redirect_stdout(out),
            contextlib.redirect_stderr(err),
        ):
            world = (
                tds._world(Path(root), monkeypatch, Market(open_days=tds.OPEN_2026))
                if form == "p6"
                else _survivor_world(Path(root), monkeypatch, SURVIVOR_CONFIG)
            )
            world.market.today = tds.DAY_AS_OF.astimezone(daily.SHANGHAI).date()
            arguments = ["--runtime-dir", str(world.runtime)]
            arguments += ["--registration", str(world.registration), "--repo", str(world.repo)]
            arguments += ["--as-of", tds.DAY_AS_OF.isoformat(), "--json"]
            for target in tds.TARGETS:
                arguments += ["--dataset", target]
            code = daily.main(arguments, clock=lambda: tds.RUN_CLOCK)
    finally:
        monkeypatch.undo()
    return {
        "exit": code,
        "before": before,
        "after": [name for name in WATCHED if name in sys.modules],
        "stderr": err.getvalue(),
    }


def _in_a_fresh_interpreter(form: str, root: Path) -> dict[str, Any]:
    path = os.pathsep.join(
        part
        for part in (
            str(ROOT / "src"),
            str(ROOT / "tests"),
            str(ROOT / "tests" / "unit" / "scripts"),
            os.environ.get("PYTHONPATH"),
        )
        if part
    )
    completed = subprocess.run(
        [sys.executable, "-c", _IN_A_FRESH_INTERPRETER, form, str(root)],
        cwd=root.parent,
        env={**os.environ, "PYTHONPATH": path, "PYTHONDONTWRITEBYTECODE": "1"},
        capture_output=True,
        text=True,
        timeout=600,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    report: dict[str, Any] = json.loads(completed.stdout.strip().splitlines()[-1])
    return report


def test_a_p6_day_loads_neither_the_paper_book_nor_the_survivor_module(tmp_path: Path) -> None:
    """A P6 day, end to end, in a process that had loaded neither: it still has neither. A P6
    run never settles a paper book, so it must not install the paper module's audit hook."""
    report = _in_a_fresh_interpreter("p6", tmp_path / "p6")

    assert report["exit"] == 0, report["stderr"]
    assert report["before"] == []
    assert report["after"] == []


def test_a_survivor_dispatch_loads_both_which_is_what_the_p6_probe_would_see(
    tmp_path: Path,
) -> None:
    """The control: the same probe sees the survivor module, and the paper module it imports,
    load once a registration names a `kind` -- so an empty answer for the P6 day is a
    measurement and not a probe that cannot see."""
    report = _in_a_fresh_interpreter("survivor", tmp_path / "survivor")

    assert report["exit"] == 1, report["stderr"]
    assert report["stderr"].splitlines()[0] == NOT_WIRED
    assert report["before"] == []
    assert report["after"] == list(WATCHED)
