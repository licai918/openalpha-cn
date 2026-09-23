"""Every command line `openalpha model --help` prints, executed (`V2-P4-094`).

A `--help` example is the shortest path a reader has from "this command exists" to "this command
answered me", and both of the ones this module drives were broken -- measured, character for
character, on a panel built by the very `openalpha panel build` lines printed beside them:

    model evaluate  -> exit 1  adj_factor holds information that first became available at
                               2026-03-20T08:30:00+00:00, after the requested as_of
                               2026-01-20T04:00:00+00:00
    model daily-run -> exit 1  daily cannot be read at <the wall clock>: ['date_gap'];
                               110 required date(s) are absent from daily

**Three separate faults, and only the first was the one the issue was filed as.**

1. **`--as-of` is a partition-level clock**, so the printed `2026-01-20T04:00:00+00:00` refuses
   any 2026 panel holding a row published after it -- which is every 2026 panel still being
   built. Swept on a fifty-five-session panel: `2026-03-20T08:29:59Z` refused, `08:30:00Z`
   answered, and a February instant refused a schedule lying entirely inside January.
2. **The bound runs the other way too**, which nothing had written down. `daily_requirement`
   needs every session up to `--as-of` to be *present*, so an `--as-of` later than the newest
   session built is a `date_gap`. The reachable set is the intersection: on a whole-year panel,
   anything after its last session; on a partial one, a single sub-day interval. `--as-of`
   omitted defaults to the wall clock, which lands outside that interval on every panel that is
   not built up to today -- and `daily-run`'s example omitted it.
3. **`model evaluate`'s example could not run on any panel at all**, and `--as-of` had nothing to
   do with it: `--horizon 5d` over the seven prediction days of `2026-01-06..2026-01-14` purges
   the first fold's training set to nothing and `walk_forward_folds` refuses the schedule. This
   repository's own corpus recorded the reason -- `test_model_interfaces.py`'s module docstring
   says a five-day horizon empties every fold on a short panel -- and the help text next to it
   said `5d` anyway.

## Why a whole year, and why that is the example's own claim rather than this file's

The examples tell a reader to run `openalpha panel build --dataset ... --year <year>`, so the
panel they assume is a *year*, and a year-keyed partition is readable from the moment it ends.
That is what the corpus here is and what `--as-of 2027-01-01T00:00:00+08:00` reads. Factor builds
are written for the eleven sessions the examples name rather than for all 259, because a stored
cross section is only needed where a prediction day falls.

## What this file asserts that a hand-written invocation would not

The command lines are **parsed out of the docstrings** rather than copied here. A test holding its
own copy of an example is a test that stays green while the printed one rots, which is exactly the
failure being closed: the two invocations below were correct when they were written and nobody ran
them again. Only two tokens are substituted, and both are things a reader supplies from their own
installation -- `./runtime`, and the exchange, because `panel_fixtures` generates an SZSE calendar
while the commands default to SSE.
"""

from __future__ import annotations

import re
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Final

import pytest
from panel_fixtures import (
    ADJ_FACTOR_DATASET,
    DAILY_BASIC_DATASET,
    DAILY_DATASET,
    EXCHANGE,
    LAST_DAY,
    PRICE_LIMIT_DATASET,
    SECURITIES,
    STOCK_BASIC_DATASET,
    SUSPENSION_DATASET,
    TRADING_CALENDAR_DATASET,
    WINDOW_FIRST,
    YEAR,
    GeneratedPanel,
    generate_panel,
    write_generated_panel,
)
from typer.testing import CliRunner

from openalpha_cn.cli import app, model_daily_run_command, model_evaluate_command
from openalpha_cn.panel.store import PanelStore
from openalpha_cn.panel_factors import (
    FACTOR_DEFINITIONS,
    FactorPanel,
    compute_factor,
    write_factor_panels,
)
from openalpha_cn.panel_ingest import daily_requirement

REVERSAL: Final = FACTOR_DEFINITIONS.get("reversal_1d/v1")
SUBJECTS: Final[tuple[str, ...]] = SECURITIES[:-1]
COMMIT: Final[str] = "abcdef1234567"

MODEL_DATASETS: Final[tuple[str, ...]] = (
    TRADING_CALENDAR_DATASET,
    STOCK_BASIC_DATASET,
    ADJ_FACTOR_DATASET,
    DAILY_DATASET,
    DAILY_BASIC_DATASET,
    SUSPENSION_DATASET,
    PRICE_LIMIT_DATASET,
)
"""The seven a model run reads. `test_year_end_daily_run.py`'s list and its reason: a whole year
is 259 sessions, so the four datasets no `_LabelInputs` read touches are left out."""

BUILT_SESSIONS: Final[int] = 11
"""Cross sections for the first eleven sessions of 2026 -- 2026-01-06 through 2026-01-20.

Enough for every prediction day the examples name (`2026-01-06..2026-01-14`) and for the instant
`--predict-at` resolves to (`2026-01-16`). The first session of the year has no build because
`reversal_1d` declares `lookback_sessions=2`.
"""


def _build_instant(session: date) -> datetime:
    """17:00 Asia/Shanghai on `session`, after that session's 16:30 publication."""
    return datetime(session.year, session.month, session.day, 9, 0, tzinfo=UTC)


def _build(store: PanelStore, panel: GeneratedPanel, session: date) -> FactorPanel:
    """One raw cross section at `session`'s own 17:00, through the real engine."""
    instant = _build_instant(session)
    return compute_factor(
        store,
        REVERSAL,
        as_of=instant,
        subjects=SUBJECTS,
        universe=frozenset(panel.securities),
        requirements={
            "daily": daily_requirement(
                panel.calendar(), years=(YEAR,), as_of=instant, max_staleness=timedelta(days=30)
            )
        },
        code_commit=COMMIT,
        built_at=instant,
        evaluators={
            REVERSAL.qualified_key: lambda context: (SUBJECTS.index(context.subject) + 1) / 100.0
        },
    )


@pytest.fixture(scope="module")
def runtime_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """The whole of 2026 priced, with cross sections on the sessions the examples name."""
    root = tmp_path_factory.mktemp("help-examples")
    store = PanelStore(root / "panel")
    panel = generate_panel(
        shapes=("daily.close_moves_between_sessions",), window=(WINDOW_FIRST, LAST_DAY)
    )
    assert panel.sessions[-1] == panel.calendar().horizon.last_date, (
        "the priced window must reach the calendar's last session, or `--as-of` after the year "
        "is not the instant this file claims it is"
    )
    write_generated_panel(store, panel, datasets=MODEL_DATASETS)
    write_factor_panels(
        store, [_build(store, panel, session) for session in panel.sessions[1 : BUILT_SESSIONS + 1]]
    )
    return root


def _printed_examples(docstring: str) -> tuple[tuple[str, ...], ...]:
    """Every `openalpha model ...` invocation in `docstring`, with its continuations joined.

    Reads the rendered docstring rather than the source file, so a line the help formatter would
    show is the line that runs here.
    """
    unwrapped = docstring.replace("\\\n", " ")
    return tuple(
        tuple(re.split(r"\s+", line.strip())[1:])
        for line in unwrapped.splitlines()
        if line.strip().startswith("openalpha model ")
    )


_COMMANDS: Final[dict[str, object]] = {
    "model evaluate": model_evaluate_command,
    "model daily-run": model_daily_run_command,
}


@pytest.mark.parametrize("described", sorted(_COMMANDS))
def test_the_command_line_printed_in_the_help_runs_against_the_panel_it_describes(
    runtime_dir: Path, described: str
) -> None:
    """The two examples, executed. See this module's docstring for what each of them cost.

    Exit `0` and not merely "not `5`": both failures were honest refusals of a request that could
    not be met, so a test admitting exit `1` would have passed against the defect it is here for.
    """
    command = _COMMANDS[described]
    printed = _printed_examples(command.__doc__ or "")

    assert printed, f"{described} prints no example, and this file is its only reader"

    for argv in printed:
        arguments = [
            *(token.replace("./runtime", str(runtime_dir)) for token in argv),
            "--exchange",
            EXCHANGE,
        ]
        result = CliRunner().invoke(app, arguments)

        assert result.exit_code == 0, f"{' '.join(arguments)}\n{result.output}"


def test_the_help_examples_read_the_panel_at_an_instant_the_partition_gate_admits() -> None:
    """The `--as-of` literal is the point of the whole file, so it is asserted rather than left
    inside a command line a reader has to parse.

    A year-keyed partition is refused at every instant earlier than the newest row in it, and the
    calendar refuses every instant later than the newest session present. Both examples therefore
    have to name an instant after 2026's last session, and neither may leave `--as-of` to the wall
    clock -- which is what `daily-run`'s example did, and why it stopped on a `date_gap` about the
    clock rather than about the panel.
    """
    for command in _COMMANDS.values():
        for argv in _printed_examples(command.__doc__ or ""):
            assert "--as-of" in argv, f"{argv} leaves the reading instant to the wall clock"
            reading = datetime.fromisoformat(argv[argv.index("--as-of") + 1])
            assert reading.tzinfo is not None
            assert reading.date() > LAST_DAY, (
                f"{reading.isoformat()} is inside the year these examples read, and a partition "
                "is refused at every instant before its own newest row"
            )
