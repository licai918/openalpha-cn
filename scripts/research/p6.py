"""The P6 research stage driver (`V2-P6-010`).

`docs/research/p6-protocol.md` is the pre-registered protocol, and this module is its stages as
code: every grid, every selection, every registration and the holdout verdict is computed here
from the protocol's constants and the research ledger, so no person types a grid, a survivor
list, a selection or a verdict. Each stage is one command::

    python scripts/research/p6.py <command> --runtime-dir <store> --ledger <ledger.jsonl>

The four commands that measure a grid -- `discovery`, `composition-sources`,
`composition-strategies`, `validation` (`WORKER_COMMANDS`) -- take `--workers N` (default 1;
`V2-P6-023`). With N >= 2 the configurations are measured in N spawned worker processes, each with
its own SDK and the BLAS/OpenMP thread counts pinned (`_worker_processes`), and **the ledger is the
one `--workers 1` writes**: this process alone appends every row, in configuration order, through
`grid`'s own writer (`grid.run_grid_in_pool`), so only `recorded_at` differs. A configuration the
ledger holds is skipped and never submitted; an interrupted run keeps every row it appended and
measures the rest again; an error that is not a refusal stops the run after the rows before it,
as the serial run stops. Each worker holds its own panel caches, so memory grows with N.
`holdout` takes no `--workers`: it runs one configuration once.

Before any command but the read-only `holdout-verdict` (`READ_ONLY_COMMANDS` says why) does
anything else, it runs the section 1 precondition,
`openalpha factor stale-return-paths --runtime-dir <store> --exchange SSE --max-staleness-days 30`,
and refuses by name (`StaleReturnPathsError`) unless it exited 0, printed exactly
`stale return-path builds: none` on stdout, and stated its `BUDGET stale-return-path-recompute N`
line on stderr before answering (`V2-P6-020`).

Protocol clause -> code:

* **Section 1** -- the precondition: `precondition_argv`, `require_clean_return_paths`. The
  research code's commit enters every row this driver measures as the result's `code_commit`
  (the measures here record it; `grid.run_grid` itself records none). The reading instant
  `AS_OF` is every configuration's `as_of`.
* **Section 2** -- each configuration's `end`: `stage_end`, the last stored-calendar session from
  which the configuration's label length in sessions still ends inside the stage. A strategy
  backtest's label length is 0 (`grid.strategy_label_sessions`: its last period is marked at
  `end`). A discovery configuration's is its IC label's, `horizon + 1`
  (`discovery_label_sessions`): an IC label enters on the session after the prediction day and
  exits `horizon` sessions after that (`domain.labels.build_label_window`). The holdout's bound is
  `HOLDOUT_LAST_SESSION`, section 7's last stored session.
* **Section 3** -- the measurement settings are `grid`'s protocol constants and the SDK's
  protocol defaults, which this driver never overrides; BY at `Q` on the whole stage family is
  `grid.fdr_table`.
* **Section 4** -- the 189 configurations: `discovery_configs`; one row per configuration with
  `p_excess` and `p_ic`: `DiscoveryMeasure`; the survivor rule, one tier per factor and the
  no-survivor fallback: `survivors`.
* **Section 5** -- step 2a's 19 sources: `composition_source_configs`; step 2b's source and 36
  strategies: `best_source`, `composition_strategy_configs` (the one equal to the source has its
  `config_id`, so `grid.run_grid` skips it); the finalists and the "no configuration passes"
  fallback (BY rejected nothing, or every rejected one has a mean <= 0): `finalists`. The
  trailing-IC source combines with `zscore_sum`, as section 5 states.
* **The tie rule before section 6** -- every selection of sections 4, 5 and 6 ranks by
  information ratio descending, then mean turnover ascending, then `config_id` ascending:
  `rank_by_information_ratio`, the only ranking here.
* **Section 6** -- `validation_configs`, and the choice: `validation_selection`.
* **Section 7** -- the registration, which never runs the holdout: `register_holdout`; the one
  run and its verdict: `run_holdout_stage` and `evaluate_holdout`, with the compounded annual
  relative return from `grid.result_compounded_annual_relative_return` and the maximum relative
  drawdown from `grid.result_max_relative_drawdown`, both over complete periods.

**Nothing upstream is read from a file a person could edit.** Every command rebuilds the
configurations of the stages before it from the protocol's constants and the stored calendar,
recomputes each selection from the ledger, and refuses (`StageIncompleteError`) unless the stage
it reads holds exactly the configurations the protocol builds -- no missing one and no extra one,
because a family with a hand-added or a dropped row is not the protocol's family. The JSON
artifacts written next to the ledger are reports of those computations; they are written once
and a recomputation that disagrees with one is refused (`ArtifactConflictError`).

**One commit from composition through validation, and the registration bound to it.** A
walk-forward source carries its `code_commit` (rule 8), so the same source at another commit is
another configuration. So:

* every row a stage writes -- a refused one included -- names the commit it ran at, and
  `stage_commit` reads the stage's one commit;
* the composition stage resumes only at its own commit, and the validation stage runs only at
  the composition commit (`run_validation`, `validation_selection`): the finalists are validated
  as they were measured, never re-run under other code with their names;
* a configuration carried to a later window keeps the commit it names; `_rewindowed` refuses to
  rewrite it;
* the registration may be written from a later clean checkout R only when the bound code
  (`BOUND_PATHS`: `registry.REGISTERED_PATHS` and this driver) is byte-identical to the
  validation commit V, at `HEAD` and in the working tree (`_require_validated_code`, reusing
  `registry`'s own check); the registered configuration keeps V. This was chosen over requiring
  R == V because a report or a protocol record is committed between validation and
  registration, and a byte-identical diff over the bound code is the same guarantee git gives
  the holdout guard; `run_holdout_stage` repeats the check before the one run;
* every command that records a commit (`LEDGER_WRITING_COMMANDS`) refuses to run with
  `openalpha_cn` or the research scripts imported from anywhere but this repository --
  `registry`'s own `ForeignPackageError` / `ForeignScriptsError` -- since the recorded commit
  names this checkout, not the code another checkout's editable install would run;
* every stage refuses a checkout with uncommitted changes (`_clean_commit`). The discovery stage
  may be resumed at another commit (a crash fixed); the survivors report lists every commit its
  rows name (`code_commits`).

**The holdout's checks come first, and its verdict is recoverable.** `run_holdout_stage` checks
the registration against the ledger (`_registered_holdout`: the configuration, the validation
row, its drawdown, every bound, the settings) and the bound code before `registry.run_holdout`
claims the one run. The verdict is then computed by `holdout_verdict`, which reads only the
ledger's holdout rows and is also the `holdout-verdict` command: a run that crashed after its
measurement row is judged there, and a claim with no measurement is "不通过" and says why.

**Where the driver fails closed rather than choosing.** A stage missing a configuration, holding
one the protocol does not build, or holding one twice (`StageIncompleteError`,
`DuplicateRowError`). A discovery stage in which all 189 configurations were refused: no row
carries `p_excess`, so `grid.fdr_table` refuses and there are no survivors to compute -- not the
no-survivor fallback, which is for a stage that was measured and found nothing. A candidate
whose information ratio is `None` (one complete period, or no variance): `UnrankableRowError`,
since no rule places it. A selection with no measured row at all (`NothingMeasuredError`).

**Reported, never selected on.** The secondary family's false-discovery table (`p_ic`, section 3)
is in the survivors report (`ic_fdr_table`); `grid.strategy_result` stores 000905.SH's series
and statistics under `reported_*` (section 3's parallel benchmark). No rule reads either.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import multiprocessing
import os
import pickle
import re
import subprocess
import sys
from collections import Counter
from collections.abc import Callable, Iterator, Mapping, Sequence
from concurrent.futures import ProcessPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Final, Protocol

import grid
import registry
from grid import GridRun, LedgerRow

from openalpha_cn.backtest.strategy_backtest import StrategyBacktest
from openalpha_cn.panel_factors import FACTOR_DEFINITIONS
from openalpha_cn.panel_ingest import load_trading_calendar
from openalpha_cn.panel_view import panel_store
from openalpha_cn.runtime.provenance import resolve_code_commit
from openalpha_cn.runtime.seeding import thread_count_pins
from openalpha_cn.sdk import OpenAlphaSDK
from openalpha_cn.strategy_view import ICSeries

REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[2]
DEFAULT_REGISTRATION: Final[Path] = REPO_ROOT / "docs" / "research" / "p6-registration.json"

# --- section 1 ---------------------------------------------------------------------------------

AS_OF: Final[datetime] = datetime(2026, 9, 26, 4, 0, tzinfo=UTC)
"""Section 1: every backtest and IC is read at this instant."""
EXCHANGE: Final[str] = "SSE"
MAX_STALENESS_DAYS: Final[int] = 30
FIRST_CALENDAR_YEAR: Final[int] = 2015
"""The first year a stage measures (section 2's discovery segment starts 2015-01-05)."""

# --- section 2 ---------------------------------------------------------------------------------

DISCOVERY: Final[str] = "discovery"
COMPOSITION: Final[str] = "composition"
VALIDATION: Final[str] = "validation"
HOLDOUT: Final[str] = grid.HOLDOUT_STAGE
HOLDOUT_LAST_SESSION: Final[date] = date(2026, 9, 24)
"""Sections 1 and 7: the research store's last trading day, which the holdout's labels end by."""
STAGE_STARTS: Final[Mapping[str, date]] = {
    DISCOVERY: date(2015, 1, 5),
    COMPOSITION: date(2017, 1, 3),
    VALIDATION: date(2022, 1, 4),
    HOLDOUT: date(2024, 1, 2),
}
"""Each stage's first measured session: the section 2 segments, and section 5's 2017 start."""

# --- section 4 ---------------------------------------------------------------------------------

PROTOCOL_FACTOR_COUNT: Final[int] = 21
TIERS: Final[tuple[str, ...]] = ("raw", "processed", "neutralized")
TRANSFORM: Final[str] = "cross_section_standard/v1"
NEUTRALIZATION: Final[str] = "industry_and_size/v1"
DISCOVERY_HORIZONS: Final[tuple[int, ...]] = (1, 5, 20)
COMBINE: Final[str] = "zscore_sum"
HOLDING_COUNT: Final[int] = 50
IC_METHOD: Final[str] = "spearman"
IC_MIN_SECURITIES: Final[int] = 100
FALLBACK_TIER: Final[str] = "processed"
Q: Final[float] = grid.PROTOCOL_FALSE_DISCOVERY_RATE

# --- section 5 ---------------------------------------------------------------------------------

SOURCE_REBALANCE_SESSIONS: Final[int] = 20
TRAILING_IC: Final[Mapping[str, object]] = {
    "ic_window_sessions": 488,
    "min_ic_observations": 120,
    "ic_method": "spearman",
    "horizon_sessions": 20,
    "min_ic_securities": 100,
}
NEGATIVE_IC: Final[tuple[str, ...]] = ("clip_to_zero", "keep_sign")
WALK_FORWARD: Final[Mapping[str, object]] = {
    "family": "boosted_rank_trees",
    "seed": 20_260_926,
    "train_sessions": 488,
    "refit_every_sessions": 122,
    "embargo_sessions": 20,
    "horizon_sessions": 20,
    "missing": "abstain",
}
WALK_FORWARD_GRID: Final[Mapping[str, tuple[object, ...]]] = {
    "tree_count": (50, 200),
    "max_depth": (2, 3),
    "learning_rate": (0.05, 0.1),
    "min_leaf_securities": (200, 1000),
}
HOLDING_COUNTS: Final[tuple[int, ...]] = (30, 50, 100)
REBALANCE_SESSIONS: Final[tuple[int, ...]] = (5, 10, 20)
BUFFER_MULTIPLE: Final[Decimal] = Decimal("1.5")
INDUSTRY_CAPS: Final[tuple[Decimal | None, ...]] = (None, Decimal("0.2"))
FINALIST_COUNT: Final[int] = 5
NO_PASS_NOTE: Final[str] = "阶段 2 无配置通过多重检验"

# --- section 7 ---------------------------------------------------------------------------------

PASS: Final[str] = "通过"
FAIL: Final[str] = "不通过"
HOLDOUT_CRITERIA: Final[Mapping[str, str]] = {
    "compounded_annualized_relative_return_above": "0",
    "one_sided_p_excess_below": "0.05",
    "max_relative_drawdown_multiple_of_validation": "2",
}
"""Section 7's three pass criteria, as data the registration carries."""

COMMANDS: Final[tuple[str, ...]] = (
    "discovery",
    "survivors",
    "composition-sources",
    "composition-strategies",
    "finalists",
    "validation",
    "register",
    "holdout",
    "holdout-verdict",
)
LEDGER_WRITING_COMMANDS: Final[frozenset[str]] = frozenset(
    {"discovery", "composition-sources", "composition-strategies", "validation", "register"}
)
"""The commands that record a commit (a ledger row or the registration) and so must run the code
of the checkout that commit names: `openalpha_cn` and the research scripts imported from this
repository (`registry`'s `ForeignPackageError` / `ForeignScriptsError`). `holdout` gets the same
two checks from `registry.run_holdout`'s own guard."""
WORKER_COMMANDS: Final[frozenset[str]] = frozenset(
    {"discovery", "composition-sources", "composition-strategies", "validation"}
)
"""The commands that measure a grid of configurations and so take `--workers N` (`V2-P6-023`).
Not `holdout`, which runs one configuration once through `registry.run_holdout`, and not
`register` or the reports, which measure nothing."""
READ_ONLY_COMMANDS: Final[frozenset[str]] = frozenset({"holdout-verdict"})
"""Commands exempt from section 1's precondition. `holdout-verdict` reads the ledger's holdout
rows and the registration and nothing in the panel store, and the one run it judges has already
happened: a panel that went stale afterwards changes nothing it reads, and must not stand
between the protocol and the verdict of its one holdout run."""
_FULL_COMMIT: Final[re.Pattern[str]] = re.compile(r"[0-9a-f]{40}")
CLEAN_RETURN_PATHS: Final[str] = "stale return-path builds: none"
"""The detector's whole stdout when no stored build is stale (`V2-P6-020`)."""
_RETURN_PATH_BUDGET: Final[re.Pattern[str]] = re.compile(
    r"BUDGET stale-return-path-recompute (0|[1-9][0-9]*) "
)
"""The cost line the detector states on stderr before it recomputes anything."""
_OPENALPHA: Final[str] = "import sys; from openalpha_cn.cli import app; app(prog_name='openalpha')"
"""The `openalpha` application, without `cli.main`'s `.env` load: the precondition reads the panel
store and needs no credential."""

Config = Mapping[str, Any]
Echo = Callable[[str], None]
Clock = Callable[[], datetime] | None


# --- refusals ----------------------------------------------------------------------------------


class P6Error(RuntimeError):
    """A stage this driver refuses to run or to compute. Each subclass is one refusal."""


class StaleReturnPathsError(P6Error):
    """Section 1's precondition did not answer `stale return-path builds: none` with exit 0."""


class ProtocolMismatchError(P6Error):
    """The build or the stored calendar is not the one the protocol was written against."""


class StageIncompleteError(P6Error):
    """A stage does not hold exactly the configurations the protocol builds for it."""


class DuplicateRowError(P6Error):
    """A stage holds one configuration twice; its family would count one hypothesis twice."""


class UnrankableRowError(P6Error):
    """A measured configuration has no information ratio to rank it by."""


class StageCommitError(P6Error):
    """The checkout is not a clean commit, or not the commit the stage's rows were measured at."""


class NothingMeasuredError(P6Error):
    """Every configuration a selection ranks was refused; there is nothing to choose from."""


class ArtifactConflictError(P6Error):
    """A report already written disagrees with what the ledger now computes."""


class HoldoutEvaluationError(P6Error):
    """The holdout's verdict cannot be computed against the registration it ran under."""


class HoldoutClaimMissingError(HoldoutEvaluationError):
    """The ledger holds holdout rows and no claim: `grid` writes a measurement only after its
    claim, so the ledger was edited by hand."""


class WorkerFactoryError(P6Error):
    """`--workers` was asked for and a worker process cannot build its SDK: the environment's
    factory does not pickle, or a worker started without the thread pins."""


# --- section 1: the precondition -----------------------------------------------------------------


@dataclass(frozen=True, slots=True, kw_only=True)
class CommandOutcome:
    """What one `openalpha` invocation answered."""

    exit_code: int
    stdout: str
    stderr: str


def precondition_argv(runtime_dir: Path) -> tuple[str, ...]:
    """Section 1's command line, after `openalpha`."""
    return (
        "factor",
        "stale-return-paths",
        "--runtime-dir",
        str(runtime_dir),
        "--exchange",
        EXCHANGE,
        "--max-staleness-days",
        str(MAX_STALENESS_DAYS),
    )


def run_openalpha(argv: Sequence[str]) -> CommandOutcome:
    """Run `openalpha <argv>` in a child process of this interpreter, and capture its answer."""
    completed = subprocess.run(
        [sys.executable, "-c", _OPENALPHA, *argv],
        capture_output=True,
        text=True,
        check=False,
    )
    return CommandOutcome(
        exit_code=completed.returncode, stdout=completed.stdout, stderr=completed.stderr
    )


def require_clean_return_paths(
    runtime_dir: Path, *, runner: Callable[[Sequence[str]], CommandOutcome] = run_openalpha
) -> None:
    """Refuse (`StaleReturnPathsError`) unless the stale-return-path detector exited 0, its
    stdout is exactly the one line `CLEAN_RETURN_PATHS`, and its stderr states the
    `BUDGET stale-return-path-recompute N` line -- the proof it reached the recomputation rather
    than answering before it. Any other answer -- a stale build listed, a panel it could not
    read, a build of the CLI that lacks the command, a bare `none`, a blank line around the
    answer -- is not clean."""
    argv = precondition_argv(runtime_dir)
    outcome = runner(argv)
    budgeted = any(_RETURN_PATH_BUDGET.match(line) for line in outcome.stderr.splitlines())
    if outcome.exit_code == 0 and outcome.stdout.splitlines() == [CLEAN_RETURN_PATHS] and budgeted:
        return
    said = (outcome.stdout.strip() or outcome.stderr.strip() or "nothing")[:2000]
    raise StaleReturnPathsError(
        f"section 1's precondition `openalpha {' '.join(argv)}` exited {outcome.exit_code} and "
        f"did not answer `{CLEAN_RETURN_PATHS}` after its budget line; no stage runs on stale "
        f"factor builds. It said: {said}"
    )


# --- section 2: windows ------------------------------------------------------------------------


def stored_sessions(runtime_dir: Path) -> tuple[date, ...]:
    """The stored exchange calendar's open sessions, every stage year, read at `AS_OF`."""
    calendar = load_trading_calendar(
        panel_store(runtime_dir),
        exchange=EXCHANGE,
        years=range(FIRST_CALENDAR_YEAR, HOLDOUT_LAST_SESSION.year + 1),
        as_of=AS_OF,
    )
    return tuple(calendar.trading_days)


def _stage_last(stage: str) -> date:
    window = grid.stage_window(stage)
    return HOLDOUT_LAST_SESSION if window.last is None else window.last


def stage_start(sessions: Sequence[date], stage: str) -> date:
    """The stage's first measured session, which must be a session of the stored calendar."""
    start = STAGE_STARTS[stage]
    if start not in sessions:
        raise ProtocolMismatchError(
            f"the {stage} stage starts on {start.isoformat()} and the stored calendar has no "
            "session that day; the calendar is not the one the protocol was written against"
        )
    return start


def stage_end(sessions: Sequence[date], stage: str, *, label_sessions: int) -> date:
    """Section 2: the last session from which `label_sessions` more sessions of `sessions` still
    end inside `stage` -- its window's last day, or `HOLDOUT_LAST_SESSION` for the holdout."""
    ordered = tuple(sessions)
    last = _stage_last(stage)
    inside = [index for index, day in enumerate(ordered) if STAGE_STARTS[stage] <= day <= last]
    if not inside or inside[-1] - label_sessions < inside[0]:
        raise ProtocolMismatchError(
            f"the stored calendar holds no {stage} session with {label_sessions} more before "
            f"{last.isoformat()}"
        )
    return ordered[inside[-1] - label_sessions]


def discovery_label_sessions(config: Config) -> int:
    """A discovery configuration's label length: its IC label enters on the session after the
    prediction day and exits `horizon` sessions after that, `horizon + 1` sessions past it. Its
    strategy backtest reads nothing past `end` (`grid.strategy_label_sessions`)."""
    return int(config["ic"]["horizon_sessions"]) + 1


# --- section 4: the discovery grid -------------------------------------------------------------


def protocol_factor_keys() -> tuple[str, ...]:
    """The build's declared factors, in declaration order, provided there are the protocol's 21."""
    keys = tuple(FACTOR_DEFINITIONS.qualified_keys)
    if len(keys) != PROTOCOL_FACTOR_COUNT:
        raise ProtocolMismatchError(
            f"the protocol's grid is {PROTOCOL_FACTOR_COUNT} factors and this build declares "
            f"{len(keys)}; a grid over another factor set is not the pre-registered one"
        )
    return keys


def tier_declarations(tiers: Sequence[str]) -> tuple[str | None, str | None]:
    """The request's transform and neutralization for components on `tiers`: the transform
    exactly when a tier is processed or neutralized, the neutralization exactly when one is
    neutralized (`strategy_view.strategy_request`'s rule)."""
    transform = TRANSFORM if {"processed", "neutralized"} & set(tiers) else None
    return transform, NEUTRALIZATION if "neutralized" in tiers else None


def discovery_configs(sessions: Sequence[date]) -> tuple[dict[str, Any], ...]:
    """Section 4's 189 configurations: 21 factors x 3 tiers x horizons {1, 5, 20}.

    Each is a single-component static score in the factor's declared direction (the view orients
    every stored value), 50 holdings, rebalanced every `horizon` sessions, no buffer, no industry
    cap, and the IC settings its measure asks `OpenAlphaSDK.factor_ic_series` for (`ic`).
    """
    start = stage_start(sessions, DISCOVERY)
    ends = {
        horizon: stage_end(sessions, DISCOVERY, label_sessions=horizon + 1)
        for horizon in DISCOVERY_HORIZONS
    }
    configs: list[dict[str, Any]] = []
    for factor in protocol_factor_keys():
        for tier in TIERS:
            transform, neutralization = tier_declarations((tier,))
            for horizon in DISCOVERY_HORIZONS:
                configs.append(
                    {
                        "components": ((factor, tier, Decimal(1)),),
                        "combine": COMBINE,
                        "transform": transform,
                        "neutralization": neutralization,
                        "start": start,
                        "end": ends[horizon],
                        "as_of": AS_OF,
                        "exchange": EXCHANGE,
                        "holding_count": HOLDING_COUNT,
                        "rebalance_every_sessions": horizon,
                        "buffer_rank": None,
                        "max_industry_weight": None,
                        "ic": {
                            "horizon_sessions": horizon,
                            "ic_method": IC_METHOD,
                            "min_securities": IC_MIN_SECURITIES,
                        },
                    }
                )
    return tuple(configs)


def _refusal(error: Exception) -> str:
    return f"{type(error).__name__}: {error}"


@dataclass(frozen=True, slots=True, kw_only=True)
class DiscoveryMeasure:
    """One discovery configuration as one ledger row carrying both families.

    The strategy half is `grid.strategy_result` over `backtest(**config without "ic")` against
    the all-A equal-weight benchmark (`p_excess`, the primary family). The IC half asks
    `ic_series` for the component's IC on every prediction day of the window and tests it with
    `grid.non_overlapping_sign_flip`, one session in every `horizon` (`p_ic`, the secondary
    family). A refused backtest is the row's `error`, as `grid.run_grid` would write it; a refused
    or empty IC series is `ic_error`, leaving the primary family's p-value in the row.
    """

    backtest: Callable[..., StrategyBacktest]
    ic_series: Callable[..., ICSeries]
    sessions: tuple[date, ...]
    code_commit: str

    def __call__(self, config: Config) -> Mapping[str, object]:
        strategy = {key: value for key, value in config.items() if key != "ic"}
        try:
            result: dict[str, object] = grid.strategy_result(
                self.backtest(**strategy), excess_benchmark=grid.PRIMARY_EXCESS_BENCHMARK
            )
        except grid.DEFAULT_REFUSALS as error:
            return {"error": _refusal(error), "code_commit": self.code_commit}
        result["code_commit"] = self.code_commit
        result.update(self._ic(config))
        return result

    def _ic(self, config: Config) -> dict[str, object]:
        ic = config["ic"]
        ((factor, tier, _),) = config["components"]
        horizon = int(ic["horizon_sessions"])
        try:
            series = self.ic_series(
                factor=factor,
                tier=tier,
                transform=config["transform"],
                neutralization=config["neutralization"],
                horizon_sessions=horizon,
                ic_method=ic["ic_method"],
                min_securities=ic["min_securities"],
                start=config["start"],
                end=config["end"],
                as_of=config["as_of"],
                exchange=config["exchange"],
            )
        except grid.DEFAULT_REFUSALS as error:
            return {"ic_error": _refusal(error)}
        values = {point.prediction_day: point.ic for point in series.points if point.ic is not None}
        days = tuple(day for day in self.sessions if config["start"] <= day <= config["end"])
        sampled = [values[day] for day in grid.non_overlapping(days, horizon) if day in values]
        if not sampled:
            return {"ic_error": "no session of the non-overlapping sample carries an IC"}
        test = grid.non_overlapping_sign_flip(values, days, horizon)
        return {
            "p_ic": test["p_value"],
            "p_ic_exact": test["exact"],
            "p_ic_sign_patterns": test["sign_patterns"],
            "p_ic_random_seed": test["random_seed"],
            "ic_sampled_sessions": test["sampled_sessions"],
            "ic_measured": test["measured"],
            "mean_ic": sum(sampled) / len(sampled),
        }


@dataclass(frozen=True, slots=True, kw_only=True)
class StrategyStageMeasure:
    """A stage 2 or validation configuration: `grid.strategy_result` against the primary
    benchmark, with the commit it ran at; a refusal is the row's `error` with that commit."""

    backtest: Callable[..., StrategyBacktest]
    code_commit: str

    def __call__(self, config: Config) -> Mapping[str, object]:
        try:
            result: dict[str, object] = grid.strategy_result(
                self.backtest(**config), excess_benchmark=grid.PRIMARY_EXCESS_BENCHMARK
            )
        except grid.DEFAULT_REFUSALS as error:
            return {"error": _refusal(error), "code_commit": self.code_commit}
        result["code_commit"] = self.code_commit
        return result


@dataclass(frozen=True, slots=True, kw_only=True)
class StageMeasureRecipe:
    """A stage's measure without the SDK methods it calls, which do not pickle: a
    `DiscoveryMeasure` over `discovery_sessions` when they are given, a `StrategyStageMeasure`
    otherwise, both at `code_commit`. The serial run and every worker process (`--workers`)
    build the stage's measure through `build`, so they measure with the same object."""

    code_commit: str
    discovery_sessions: tuple[date, ...] | None = None

    def build(
        self,
        backtest: Callable[..., StrategyBacktest],
        ic_series: Callable[..., ICSeries] | None = None,
    ) -> grid.Measure:
        if self.discovery_sessions is None:
            return StrategyStageMeasure(backtest=backtest, code_commit=self.code_commit)
        if ic_series is None:
            raise TypeError("a discovery measure reads the IC series as well as the backtest")
        return DiscoveryMeasure(
            backtest=backtest,
            ic_series=ic_series,
            sessions=self.discovery_sessions,
            code_commit=self.code_commit,
        )


# --- the ledger, read for a stage ----------------------------------------------------------------


def _stage_rows(ledger: Path, stage: str) -> list[LedgerRow]:
    return [
        row
        for row in grid.read_ledger(ledger)
        if row.stage == stage and row.kind == grid.MEASUREMENT
    ]


def _rows_for(
    ledger: Path, stage: str, expected: Sequence[Config], *, exact: bool
) -> dict[str, LedgerRow]:
    """The stage's row of each expected configuration, by `config_id`. Refuses a missing one,
    and with `exact` a row the protocol does not build: it would be a hypothesis of the family
    that no grid tried, or a grid other than the protocol's. Refuses a configuration the stage
    holds twice (`DuplicateRowError`): `grid.stage_family` would count it twice."""
    stage_rows = _stage_rows(ledger, stage)
    rows = {row.config_id: row for row in stage_rows}
    if len(rows) != len(stage_rows):
        counts = Counter(row.config_id for row in stage_rows)
        lines = sorted(row.line for row in stage_rows if counts[row.config_id] > 1)
        raise DuplicateRowError(
            f"the {stage} stage in {ledger} holds a configuration twice (lines {lines}); the "
            "family would count one hypothesis twice -- the ledger was edited by hand"
        )
    wanted = [grid.config_id(config) for config in expected]
    missing = [identity for identity in wanted if identity not in rows]
    extra = sorted(set(rows) - set(wanted)) if exact else []
    if missing or extra:
        raise StageIncompleteError(
            f"the {stage} stage in {ledger} lacks {len(missing)} configuration(s) the protocol "
            f"builds and holds {len(extra)} it does not (e.g. "
            f"{(missing + extra)[0]}); run the stage to completion with this driver"
        )
    return {identity: rows[identity] for identity in wanted}


def _clean_commit(code_commit: str) -> str:
    if not _FULL_COMMIT.fullmatch(code_commit):
        raise StageCommitError(
            f"the checkout's commit is {code_commit!r}, not a clean full commit id (a dirty or "
            "unknown checkout); a stage runs from committed code only"
        )
    return code_commit


def stage_commit(ledger: Path, stage: str) -> str | None:
    """The one commit the stage's rows were measured at, or `None` for a stage with no row.

    A row that names no commit (a row written before this driver recorded one) is passed over;
    a stage whose rows name none at all, or more than one, is refused."""
    rows = _stage_rows(ledger, stage)
    if not rows:
        return None
    commits = {
        row.result["code_commit"] for row in rows if isinstance(row.result.get("code_commit"), str)
    }
    if not commits:
        raise StageCommitError(
            f"the {stage} stage has {len(rows)} row(s) and no row names the commit it ran at"
        )
    if len(commits) != 1:
        raise StageCommitError(
            f"the {stage} stage's rows name {sorted(commits)} as their commit; its "
            "configurations cannot be rebuilt from one"
        )
    return str(next(iter(commits)))


def _require_stage_commit(ledger: Path, stage: str, code_commit: str) -> str:
    head = _clean_commit(code_commit)
    recorded = stage_commit(ledger, stage)
    if recorded is not None and recorded != head:
        raise StageCommitError(
            f"the {stage} stage's rows were measured at {recorded} and this checkout is {head}; "
            "a walk-forward configuration names its commit, so resuming here would count the "
            "same hypothesis twice. Resume at the stage's commit"
        )
    return head


def _composition_commit(ledger: Path) -> str:
    commit = stage_commit(ledger, COMPOSITION)
    if commit is None:
        raise StageIncompleteError(f"the composition stage has no row in {ledger}")
    return commit


def _require_composition_commit(ledger: Path, commit: str, what: str) -> None:
    """Validation runs the finalists as they were measured: at the composition commit."""
    composition = _composition_commit(ledger)
    if commit != composition:
        raise StageCommitError(
            f"{what} is {commit} and the composition stage was measured at {composition}; the "
            "finalists are validated at the commit they were measured at, or they are other "
            "configurations under the finalists' names"
        )


def _measured(rows: Sequence[LedgerRow]) -> list[LedgerRow]:
    return [row for row in rows if "error" not in row.result]


def _rank_key(row: LedgerRow) -> tuple[float, float, str]:
    ratio, turnover = row.result.get("information_ratio"), row.result.get("mean_turnover")
    if not isinstance(ratio, int | float) or not isinstance(turnover, int | float):
        raise UnrankableRowError(
            f"configuration {row.config_id} has information ratio {ratio!r} and mean turnover "
            f"{turnover!r}; it cannot be ranked"
        )
    return (-float(ratio), float(turnover), row.config_id)


def rank_by_information_ratio(rows: Sequence[LedgerRow], take: int) -> list[LedgerRow]:
    """The best `take` rows under the protocol's tie rule (before section 6): information ratio
    descending, then mean turnover ascending, then `config_id` ascending -- a last level that
    reads no result, so every selection is a total order."""
    return sorted(rows, key=_rank_key)[:take]


def _passes(row: LedgerRow, report: Any) -> bool:
    """Sections 4 and 5: BY-rejected in the primary family **and** a positive mean net excess.
    A configuration significant against its declared direction has a negative mean and fails."""
    verdict = report.verdict_for(row.config_id)
    mean = row.result.get("mean_net_excess")
    return (
        verdict is not None
        and bool(verdict.rejected)
        and "error" not in row.result
        and isinstance(mean, int | float)
        and mean > 0
    )


def _summary(row: LedgerRow) -> dict[str, object]:
    keys = (
        "error",
        "p_excess",
        "p_ic",
        "mean_net_excess",
        "annualized_mean_net_excess",
        "information_ratio",
        "mean_turnover",
        "max_relative_drawdown",
        "compounded_annual_relative_return",
        "reported_benchmark",
        "reported_mean_net_excess",
        "reported_information_ratio",
        "reported_compounded_annual_relative_return",
        "code_commit",
    )
    return {"config_id": row.config_id} | {
        key: row.result[key] for key in keys if key in row.result
    }


def _rows_digest(rows: Sequence[LedgerRow]) -> str:
    body = [[row.config_id, dict(row.result)] for row in rows]
    return hashlib.sha256(grid.canonical_json(body).encode("utf-8")).hexdigest()


def _fdr(ledger: Path, stage: str) -> Any:
    return grid.fdr_table(ledger, stage, Q)


# --- section 4: survivors ------------------------------------------------------------------------


def survivors(ledger: Path, sessions: Sequence[date]) -> dict[str, Any]:
    """Section 4's survivors and the components stage 2 is built from.

    Survivor: BY (arbitrary dependence, q = `Q`, the whole stage family) rejects its `p_excess`
    and its mean net excess is positive. Each factor with a survivor contributes one tier: the
    tier of its survivor with the highest information ratio (a tie to the lower turnover). With
    no survivor at all, every factor contributes its `processed` tier and `fallback` is true.
    """
    configs = discovery_configs(sessions)
    rows = _rows_for(ledger, DISCOVERY, configs, exact=True)
    report = _fdr(ledger, DISCOVERY)
    by_factor: dict[str, list[LedgerRow]] = {}
    tier_of: dict[str, str] = {}
    for config in configs:
        identity = grid.config_id(config)
        ((factor, tier, _),) = config["components"]
        tier_of[identity] = tier
        if _passes(rows[identity], report):
            by_factor.setdefault(factor, []).append(rows[identity])
    chosen: list[tuple[str, LedgerRow]] = []
    for factor in protocol_factor_keys():
        if factor in by_factor:
            (best,) = rank_by_information_ratio(by_factor[factor], take=1)
            chosen.append((factor, best))
    fallback = not chosen
    components = (
        [[factor, FALLBACK_TIER] for factor in protocol_factor_keys()]
        if fallback
        else [[factor, tier_of[row.config_id]] for factor, row in chosen]
    )
    surviving = [row for factor in by_factor for row in by_factor[factor]]
    with_ic = sum(1 for row in rows.values() if "p_ic" in row.result)
    measured_ic = with_ic > 0
    return {
        "schema": "openalpha-p6-survivors/v1",
        "stage": DISCOVERY,
        "family_size": grid.stage_family(ledger, DISCOVERY),
        "fdr_table": report.model_dump(mode="json"),
        # The secondary family (section 3): BY over `p_ic` on the same stage family, a row with
        # `ic_error` or `error` withheld. Reported only; no selection reads it.
        "ic_fdr_table": (
            grid.fdr_table(ledger, DISCOVERY, Q, p_value_key="p_ic").model_dump(mode="json")
            if measured_ic
            else None
        ),
        "ic_fdr_note": _secondary_family_note(list(rows.values()), with_ic),
        "code_commits": sorted(
            {
                row.result["code_commit"]
                for row in rows.values()
                if isinstance(row.result.get("code_commit"), str)
            }
        ),
        "survivors": [_summary(row) for row in surviving],
        "chosen": [{"factor": factor, **_summary(row)} for factor, row in chosen],
        "components": components,
        "fallback": fallback,
        "stage_rows_sha256": _rows_digest(list(rows.values())),
    }


def _secondary_family_note(rows: Sequence[LedgerRow], with_ic: int) -> str | None:
    """What the secondary family is missing, with its counts; `None` when every hypothesis has a
    `p_ic`. The survivors command prints it as a warning (sections 3 and 8 require the table)."""
    total = len(rows)
    if with_ic == total:
        return None
    ic_errors = sum(1 for row in rows if "ic_error" in row.result)
    errors = sum(1 for row in rows if "error" in row.result)
    counts = (
        f"次家族 {total} 个假设，{with_ic} 个有 p_ic（{ic_errors} 行 ic_error、{errors} 行 error）"
    )
    if with_ic == 0:
        return f"{counts}，§3/§8 次家族 FDR 表缺失"
    return f"{counts}，{total - with_ic} 个假设在次家族 FDR 表中 withheld"


def _components(ledger: Path, sessions: Sequence[date]) -> tuple[tuple[str, str], ...]:
    return tuple((factor, tier) for factor, tier in survivors(ledger, sessions)["components"])


# --- section 5: step 2a ---------------------------------------------------------------------------


def _strategy_base(start: date, end: date) -> dict[str, Any]:
    return {
        "combine": COMBINE,
        "start": start,
        "end": end,
        "as_of": AS_OF,
        "exchange": EXCHANGE,
        "holding_count": HOLDING_COUNT,
        "rebalance_every_sessions": SOURCE_REBALANCE_SESSIONS,
        "buffer_rank": None,
        "max_industry_weight": None,
    }


def _feature(factor: str, tier: str) -> str:
    """A walk-forward feature token. The model faces refuse a neutralized feature, so a
    neutralized survivor enters as its processed tier (section 5)."""
    if tier == "raw":
        return f"{factor}@raw"
    return f"{factor}@processed:{TRANSFORM}"


def composition_source_configs(
    components: Sequence[tuple[str, str]], sessions: Sequence[date], code_commit: str
) -> tuple[dict[str, Any], ...]:
    """Section 5 step 2a's 19 score sources over `components`, each with 50 holdings rebalanced
    every 20 sessions, no buffer, no industry cap, over 2017-01-03 to the stage's last session:
    the equal-weight static source, the trailing-IC source under each `negative_ic`, and the 16
    walk-forward models. `code_commit` is the commit the stage runs at (rule 8)."""
    start = stage_start(sessions, COMPOSITION)
    end = stage_end(sessions, COMPOSITION, label_sessions=grid.strategy_label_sessions({}))
    pairs = tuple((factor, tier) for factor, tier in components)
    transform, neutralization = tier_declarations([tier for _, tier in pairs])
    base = _strategy_base(start, end)
    static = {
        **base,
        "components": tuple((factor, tier, Decimal(1)) for factor, tier in pairs),
        "transform": transform,
        "neutralization": neutralization,
    }
    trailing = [
        {
            **base,
            "transform": transform,
            "neutralization": neutralization,
            "trailing_ic": {"components": pairs, **TRAILING_IC, "negative_ic": negative},
        }
        for negative in NEGATIVE_IC
    ]
    features = tuple(_feature(factor, tier) for factor, tier in pairs)
    forests = [
        {
            **base,
            "transform": None,
            "neutralization": None,
            "walk_forward": {
                **WALK_FORWARD,
                "features": features,
                "hyperparameters": dict(hyperparameters),
                "code_commit": code_commit,
            },
        }
        for hyperparameters in grid.expand_grid(WALK_FORWARD_GRID)
    ]
    return (static, *trailing, *forests)


def best_source(
    ledger: Path, sessions: Sequence[date], code_commit: str
) -> tuple[tuple[dict[str, Any], ...], dict[str, Any], LedgerRow]:
    """Step 2b's source: the first of step 2a's 19 rows under the protocol's tie rule
    (`rank_by_information_ratio`). Returns the 19 source configurations, the chosen one and its
    row."""
    sources = composition_source_configs(_components(ledger, sessions), sessions, code_commit)
    rows = _rows_for(ledger, COMPOSITION, sources, exact=False)
    measured = _measured(list(rows.values()))
    if not measured:
        raise NothingMeasuredError("no step 2a score source was measured; step 2b has no source")
    (best,) = rank_by_information_ratio(measured, take=1)
    config = next(config for config in sources if grid.config_id(config) == best.config_id)
    return sources, config, best


def composition_strategy_configs(source: Config) -> tuple[dict[str, Any], ...]:
    """Step 2b's 36 strategies of `source`: holdings {30, 50, 100} x rebalance {5, 10, 20} x
    buffer {none, holdings x 1.5} x industry cap {none, 0.2}. The one whose rules are step 2a's is
    `source` itself, with its `config_id`."""
    configs: list[dict[str, Any]] = []
    for holding in HOLDING_COUNTS:
        buffered = Decimal(holding) * BUFFER_MULTIPLE
        if buffered != buffered.to_integral_value():
            raise ProtocolMismatchError(f"a buffer of {buffered} names is not a whole rank")
        for rebalance in REBALANCE_SESSIONS:
            for buffer in (None, int(buffered)):
                for cap in INDUSTRY_CAPS:
                    configs.append(
                        {
                            **source,
                            "holding_count": holding,
                            "rebalance_every_sessions": rebalance,
                            "buffer_rank": buffer,
                            "max_industry_weight": cap,
                        }
                    )
    return tuple(configs)


# --- section 5: finalists -------------------------------------------------------------------------


def finalists(
    ledger: Path, sessions: Sequence[date]
) -> tuple[dict[str, Any], tuple[dict[str, Any], ...]]:
    """Section 5's finalists and their configurations, best first.

    The family is the whole composition stage (19 sources and step 2b's 35 new rows). A
    configuration passes when BY rejects it and its mean net excess is positive; the best five
    passing ones go on, or all of them when fewer. **When none passes** -- BY rejected nothing,
    or every one it rejected has a mean <= 0 -- the best five of every measured stage-2
    configuration go on regardless, and `no_configuration_passed_multiple_testing` is set with
    the note the report must carry. Ranking is `rank_by_information_ratio`'s three-level rule.
    """
    commit = stage_commit(ledger, COMPOSITION)
    if commit is None:
        raise StageIncompleteError(f"the composition stage has no row in {ledger}")
    sources, source, source_row = best_source(ledger, sessions, commit)
    expected = {
        grid.config_id(config): config
        for config in (*sources, *composition_strategy_configs(source))
    }
    rows = _rows_for(ledger, COMPOSITION, list(expected.values()), exact=True)
    report = _fdr(ledger, COMPOSITION)
    passing = [row for row in rows.values() if _passes(row, report)]
    no_pass = not passing
    candidates = _measured(list(rows.values())) if no_pass else passing
    chosen = rank_by_information_ratio(candidates, take=FINALIST_COUNT)
    if not chosen:
        raise NothingMeasuredError("no stage-2 configuration was measured; nothing can go on")
    body = {
        "schema": "openalpha-p6-finalists/v1",
        "stage": COMPOSITION,
        "family_size": grid.stage_family(ledger, COMPOSITION),
        "fdr_table": report.model_dump(mode="json"),
        "source": _summary(source_row),
        "no_configuration_passed_multiple_testing": no_pass,
        "note": NO_PASS_NOTE if no_pass else None,
        "finalists": [row.config_id for row in chosen],
        "results": [_summary(row) for row in chosen],
        "code_commit": commit,
        "stage_rows_sha256": _rows_digest(list(rows.values())),
    }
    return body, tuple(expected[row.config_id] for row in chosen)


# --- section 6: validation ------------------------------------------------------------------------


def _rewindowed(config: Config, start: date, end: date, code_commit: str) -> dict[str, Any]:
    """`config` over another window. A walk-forward source keeps the commit it names, which must
    be `code_commit`: a later stage runs the configuration an earlier one measured, and a
    rewritten commit would make it another configuration under the same name."""
    model = config.get("walk_forward")
    if model is not None and model["code_commit"] != code_commit:
        raise StageCommitError(
            f"the walk-forward source was measured at {model['code_commit']} and this stage "
            f"would run it at {code_commit}; a source is carried to a later window unchanged"
        )
    return {**config, "start": start, "end": end}


def validation_configs(
    finalist_configs: Sequence[Config], sessions: Sequence[date], code_commit: str
) -> tuple[dict[str, Any], ...]:
    """Each finalist over section 6's window, unchanged otherwise (`_rewindowed`)."""
    start = stage_start(sessions, VALIDATION)
    end = stage_end(sessions, VALIDATION, label_sessions=grid.strategy_label_sessions({}))
    return tuple(_rewindowed(config, start, end, code_commit) for config in finalist_configs)


def validation_selection(
    ledger: Path, sessions: Sequence[date]
) -> tuple[dict[str, Any], dict[str, Any], LedgerRow]:
    """Section 6: of the finalists' validation rows, the first under the protocol's tie rule
    (`rank_by_information_ratio`: the annualized information ratio of the net excess, then the
    lower mean turnover, then the lower `config_id`). Returns the report of every finalist's
    result, the chosen configuration and its row. The validation rows must have been measured at
    the composition commit (`StageCommitError`)."""
    commit = stage_commit(ledger, VALIDATION)
    if commit is None:
        raise StageIncompleteError(f"the validation stage has no row in {ledger}")
    _require_composition_commit(ledger, commit, "the validation stage's commit")
    _, finalist_configs = finalists(ledger, sessions)
    configs = validation_configs(finalist_configs, sessions, commit)
    rows = _rows_for(ledger, VALIDATION, configs, exact=True)
    measured = _measured(list(rows.values()))
    if not measured:
        raise NothingMeasuredError("no finalist was measured over the validation window")
    (best,) = rank_by_information_ratio(measured, take=1)
    chosen = next(config for config in configs if grid.config_id(config) == best.config_id)
    body = {
        "schema": "openalpha-p6-validation/v1",
        "stage": VALIDATION,
        "chosen": best.config_id,
        "results": [_summary(row) for row in rows.values()],
        "code_commit": commit,
        "stage_rows_sha256": _rows_digest(list(rows.values())),
    }
    return body, chosen, best


# --- section 7: registration and the holdout ----------------------------------------------------

BOUND_PATHS: Final[tuple[str, ...]] = tuple(
    dict.fromkeys((*registry.REGISTERED_PATHS, "scripts/research/p6.py"))
)
"""The code a registration binds to the validation commit: `registry.REGISTERED_PATHS` (which
already holds `scripts/research/`) and this driver, named so the binding says so."""


def holdout_config(config: Config, sessions: Sequence[date], code_commit: str) -> dict[str, Any]:
    """The chosen configuration over section 7's window, unchanged otherwise: a walk-forward one
    keeps `code_commit`, the commit it was validated at (`_rewindowed`)."""
    start = stage_start(sessions, HOLDOUT)
    end = stage_end(sessions, HOLDOUT, label_sessions=grid.strategy_label_sessions({}))
    return _rewindowed(config, start, end, code_commit)


def holdout_criteria(validation_row: LedgerRow) -> dict[str, object]:
    """Section 7's criteria as data, bound to the validation row the drawdown is compared with."""
    return {
        **HOLDOUT_CRITERIA,
        "validation_config_id": validation_row.config_id,
        "validation_max_relative_drawdown": grid.result_max_relative_drawdown(
            validation_row.result
        ),
    }


def _require_validated_code(repo: Path, validated: str, head: str) -> Path:
    """The repository root, provided `head` is its checked-out commit and `BOUND_PATHS` -- at
    `HEAD` and in the working tree -- are what they were at `validated` (`registry`'s own check,
    `SourceChangedError`). A commit that touched only documentation passes."""
    top = registry._git(repo, "rev-parse", "--show-toplevel")
    at = registry._git(repo, "rev-parse", "HEAD")
    if top.returncode != 0 or at.returncode != 0:
        raise StageCommitError(f"{repo} is not a git checkout with a commit")
    root = Path(top.stdout.decode().strip()).resolve()
    if at.stdout.decode().strip() != head:
        raise StageCommitError(f"{head} is not the checked-out commit of {root}")
    registry._refuse_a_changed_source(
        root,
        validated,
        paths=BOUND_PATHS,
        purpose="the registration binds the code the finalists were validated with",
    )
    return root


def register_holdout(
    ledger: Path, sessions: Sequence[date], registration: Path, code_commit: str, repo: Path
) -> str:
    """Write section 7's registration through `registry.register` and return its digest. It does
    not run the holdout; committing the file is the next step.

    **The binding to the validation commit V.** The registration may be written from a later
    clean checkout R (a report or a protocol record committed since), and only when the bound
    code (`BOUND_PATHS`) is byte-identical between V and R, at `HEAD` and in the working tree:
    `registry.register` records R as the code commit, `registry.run_holdout` later holds `HEAD`
    to R, and this check holds R to V, so the holdout measures with the code that chose the
    configuration. The configuration keeps V, the commit it was validated at (rule 8).
    """
    head = _clean_commit(code_commit)
    _, chosen, row = validation_selection(ledger, sessions)
    validated = str(stage_commit(ledger, VALIDATION))
    _require_validated_code(repo, validated, head)
    return registry.register(
        holdout_config(chosen, sessions, validated),
        holdout_criteria(row),
        registration,
        code_commit=head,
        settings=grid.protocol_settings(),
    )


def _number(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float | str):
        raise HoldoutEvaluationError(f"{name} is {value!r}, not a number")
    try:
        return float(value)
    except ValueError as error:
        raise HoldoutEvaluationError(f"{name} is {value!r}, not a number") from error


def _holdout_bounds(
    criteria: Mapping[str, Any], validation_result: Mapping[str, Any]
) -> dict[str, float]:
    """Each criterion's bound, once the registered validation drawdown is the ledger's."""
    validation = grid.result_max_relative_drawdown(validation_result)
    if validation != criteria["validation_max_relative_drawdown"]:
        raise HoldoutEvaluationError(
            f"the validation row's maximum relative drawdown is {validation}, and the "
            f"registration recorded {criteria['validation_max_relative_drawdown']}"
        )
    multiple = _number(criteria["max_relative_drawdown_multiple_of_validation"], "the multiple")
    return {
        "compounded_annualized_relative_return": _number(
            criteria["compounded_annualized_relative_return_above"], "the return bound"
        ),
        "one_sided_p_excess": _number(criteria["one_sided_p_excess_below"], "the p bound"),
        "max_relative_drawdown": multiple * validation,
    }


def evaluate_holdout(
    result: Mapping[str, Any], criteria: Mapping[str, Any], validation_result: Mapping[str, Any]
) -> dict[str, Any]:
    """Section 7's verdict on the holdout's measurement, computed from the ledgered numbers.

    1. the compounded annualized relative return against the all-A equal-weight benchmark,
       `(prod(1 + net) / prod(1 + benchmark)) ** (244 / complete-period sessions) - 1`
       (`grid.result_compounded_annual_relative_return`), above the registered bound;
    2. the one-sided sign-flip p of the per-period excess (`grid.one_sided_p_value`) below its
       bound;
    3. the maximum relative drawdown over complete periods at most the registered multiple of the
       validation row's, which must be the drawdown the registration recorded.

    All three read complete periods only. "通过" only when all three hold; otherwise "不通过",
    an unmeasured holdout included. Criterion 1 compounds, so it can fail while the arithmetic
    mean excess is significantly positive (a loss in a period the benchmark fell is divided by
    that period's small `1 + benchmark`).
    """
    bounds = _holdout_bounds(criteria, validation_result)
    if "error" in result:
        items = {
            name: {"value": None, "threshold": bound, "passed": False}
            for name, bound in bounds.items()
        }
        return {"verdict": FAIL, "criteria": items, "error": result["error"]}
    values = {
        "compounded_annualized_relative_return": grid.result_compounded_annual_relative_return(
            result
        ),
        "one_sided_p_excess": grid.one_sided_p_value(
            _number(result["p_excess"], "p_excess"),
            _number(result["mean_net_excess"], "mean_net_excess"),
        ),
        "max_relative_drawdown": grid.result_max_relative_drawdown(result),
    }
    passed = {
        "compounded_annualized_relative_return": values["compounded_annualized_relative_return"]
        > bounds["compounded_annualized_relative_return"],
        "one_sided_p_excess": values["one_sided_p_excess"] < bounds["one_sided_p_excess"],
        "max_relative_drawdown": values["max_relative_drawdown"] <= bounds["max_relative_drawdown"],
    }
    items = {
        name: {"value": values[name], "threshold": bounds[name], "passed": passed[name]}
        for name in bounds
    }
    return {"verdict": PASS if all(passed.values()) else FAIL, "criteria": items}


@dataclass(frozen=True, slots=True, kw_only=True)
class _Registered:
    """The registration as read once, and everything the ledger says it must agree with."""

    content: bytes
    body: Mapping[str, Any]
    config: dict[str, Any]
    validation_row: LedgerRow
    bounds: Mapping[str, float]


def _registered_holdout(ledger: Path, sessions: Sequence[date], registration: Path) -> _Registered:
    """Every check of the registration that the ledger can answer, made before anything runs:
    the configuration rebuilt from the ledger (at the validation commit) is the registered one;
    the registered validation row is section 6's choice; its registered drawdown is the ledger's;
    every criterion is a number; the settings are the protocol's."""
    content = registration.read_bytes()
    try:
        body = json.loads(content)
    except ValueError as error:
        raise HoldoutEvaluationError(f"{registration} is not JSON: {error}") from error
    if not isinstance(body, dict) or body.get("schema") != registry.REGISTRATION_SCHEMA:
        raise HoldoutEvaluationError(f"{registration} is not a {registry.REGISTRATION_SCHEMA}")
    _, chosen, validation_row = validation_selection(ledger, sessions)
    config = holdout_config(chosen, sessions, str(stage_commit(ledger, VALIDATION)))
    if grid.config_id(config) != body.get("config_id"):
        raise HoldoutEvaluationError(
            f"the ledger's choice over the holdout window is {grid.config_id(config)} and the "
            f"registration names {body.get('config_id')}"
        )
    criteria = body.get("criteria")
    if not isinstance(criteria, dict):
        raise HoldoutEvaluationError(f"{registration} carries no criteria")
    if criteria.get("validation_config_id") != validation_row.config_id:
        raise HoldoutEvaluationError(
            f"the registration compares with validation row {criteria.get('validation_config_id')}"
            f" and section 6 chose {validation_row.config_id}"
        )
    if body.get("settings") != grid.to_json_value(grid.protocol_settings()):
        raise HoldoutEvaluationError(
            f"the registered settings {body.get('settings')} are not the protocol's"
        )
    try:
        bounds = _holdout_bounds(criteria, validation_row.result)
    except KeyError as error:
        raise HoldoutEvaluationError(f"the registration's criteria lack {error}") from error
    return _Registered(
        content=content,
        body=body,
        config=config,
        validation_row=validation_row,
        bounds=bounds,
    )


def run_holdout_stage(
    ledger: Path,
    sessions: Sequence[date],
    registration: Path,
    repo: Path,
    backtest: Callable[..., StrategyBacktest],
    *,
    clock: Clock = None,
) -> dict[str, Any]:
    """Run the registered configuration once through `registry.run_holdout`, then compute the
    verdict through `holdout_verdict`, the read-only path a crashed run recovers by.

    Everything that can be checked before the one run is checked first: the registration against
    the ledger (`_registered_holdout`) and the bound code against the validation commit
    (`_require_validated_code`); `run_holdout` then applies its own guard before its claim."""
    registered = _registered_holdout(ledger, sessions, registration)
    head = registry._git(repo, "rev-parse", "HEAD").stdout.decode().strip()
    _require_validated_code(repo, str(stage_commit(ledger, VALIDATION)), head)
    measure = grid.strategy_measure(backtest, excess_benchmark=grid.PRIMARY_EXCESS_BENCHMARK)
    registry.run_holdout(registration, ledger, repo, registered.config, measure, clock=clock)
    return holdout_verdict(ledger, sessions, registration)


def holdout_verdict(ledger: Path, sessions: Sequence[date], registration: Path) -> dict[str, Any]:
    """Section 7's verdict from the ledger's holdout rows, reading and running nothing else.

    The rows must carry this registration's digest and configuration. A measurement row is
    judged by `evaluate_holdout`. A claim with no measurement is a run that started and did not
    finish: it is the one run, nothing it could have measured passes, and the verdict is "不通过"
    with a statement saying so. No holdout row at all is refused: the holdout has not run.
    """
    registered = _registered_holdout(ledger, sessions, registration)
    digest = hashlib.sha256(registered.content).hexdigest()
    rows = [row for row in grid.read_ledger(ledger) if row.stage == HOLDOUT]
    if not rows:
        raise HoldoutEvaluationError(f"the holdout has not run: {ledger} holds no holdout row")
    for row in rows:
        if row.result.get("registration_sha256") != digest:
            raise HoldoutEvaluationError(
                f"holdout row {row.line} was written under another registration"
            )
        if row.config_id != registered.body["config_id"]:
            raise HoldoutEvaluationError(f"holdout row {row.line} is not the registered config")
    claims = [row for row in rows if row.kind == grid.HOLDOUT_CLAIM]
    if not claims:
        raise HoldoutClaimMissingError(
            f"{ledger} holds {len(rows)} holdout row(s) and no claim; a measurement is written "
            "only after its claim, so the ledger was edited"
        )
    claim = claims[0]
    measured = [row for row in rows if row.kind == grid.MEASUREMENT]
    criteria = registered.body["criteria"]
    if measured:
        verdict = evaluate_holdout(measured[0].result, criteria, registered.validation_row.result)
        statement = f"measured once, claimed at {claim.recorded_at.isoformat()}"
    else:
        verdict = {
            "verdict": FAIL,
            "criteria": {
                name: {"value": None, "threshold": bound, "passed": False}
                for name, bound in registered.bounds.items()
            },
        }
        statement = (
            f"the holdout claim of {claim.recorded_at.isoformat()} has no measurement row: the "
            "one run started and did not finish, so nothing passes (不通过)"
        )
    return {
        "schema": "openalpha-p6-holdout-verdict/v1",
        **verdict,
        "statement": statement,
        "wording": "候选",
        "holdout_config_id": registered.body["config_id"],
        "validation_config_id": registered.validation_row.config_id,
        "registration_sha256": digest,
        "registration_commit": claim.result.get("registration_commit"),
    }


# --- running a stage ---------------------------------------------------------------------------


def _describe(config: Config) -> str:
    if "walk_forward" in config:
        model = config["walk_forward"]["hyperparameters"]
        source = "walk_forward " + ",".join(f"{k}={v}" for k, v in sorted(model.items()))
    elif "trailing_ic" in config:
        source = f"trailing_ic {config['trailing_ic']['negative_ic']}"
    else:
        source = " ".join(f"{factor}@{tier}" for factor, tier, _ in config["components"])
    return (
        f"{source} holding={config['holding_count']} rebalance="
        f"{config['rebalance_every_sessions']} buffer={config['buffer_rank']} "
        f"cap={config['max_industry_weight']} {config['start']}..{config['end']}"
    )


def _run(
    ledger: Path,
    stage: str,
    configs: Sequence[Config],
    measure: grid.Measure,
    *,
    label_sessions: Callable[[Mapping[str, object]], int],
    sessions: Sequence[date],
    echo: Echo,
    clock: Clock,
    code_commit: str,
) -> GridRun:
    """`grid.run_grid` one configuration at a time, so each is reported as it lands; resumable
    exactly as `run_grid` is. A row the runner refuses itself (a window outside the stage)
    carries `code_commit` like every row the measures write."""
    ran = skipped = 0
    for index, config in enumerate(configs, start=1):
        run = grid.run_grid(
            ledger,
            stage,
            (config,),
            measure,
            label_sessions=label_sessions,
            sessions=sessions,
            clock=clock,
            result_extra={"code_commit": code_commit},
        )
        ran, skipped = ran + run.ran, skipped + run.skipped
        echo(_progress(index, len(configs), config, ran=bool(run.ran)))
    total = GridRun(stage=stage, ran=ran, skipped=skipped)
    echo(_tally(ledger, total))
    return total


def _progress(index: int, count: int, config: Config, *, ran: bool) -> str:
    return (
        f"[{index}/{count}] {'ran' if ran else 'skipped'} {_describe(config)} "
        f"{grid.config_id(config)[:12]}"
    )


def _tally(ledger: Path, run: GridRun) -> str:
    return (
        f"{run.stage}: {run.ran} ran, {run.skipped} skipped, family "
        f"{grid.stage_family(ledger, run.stage)}"
    )


# --- running a stage in worker processes (`V2-P6-023`) -------------------------------------------


@dataclass(frozen=True, slots=True, kw_only=True)
class WorkerPool:
    """`--workers N` for N >= 2: the stage's configurations are measured in `workers` spawned
    processes, each of which builds its own SDK as `sdk(runtime_dir)` -- a module-level factory,
    since the SDK's bound methods do not pickle -- and its own measure from the stage's
    `StageMeasureRecipe`."""

    workers: int
    sdk: Callable[[Path], ResearchSDK]
    runtime_dir: Path


_WORKER_MEASURE: grid.Measure | None = None
"""The stage's measure in a worker process, built once by `_start_worker`."""


def _start_worker(
    sdk: Callable[[Path], ResearchSDK], runtime_dir: Path, recipe: StageMeasureRecipe
) -> None:
    """A worker process's start: refuse to measure without the thread pins, then build the SDK
    and the stage's measure once for every configuration this process measures."""
    global _WORKER_MEASURE
    unpinned = sorted(
        name for name, value in thread_count_pins().items() if os.environ.get(name) != value
    )
    if unpinned:
        raise WorkerFactoryError(f"a worker started without the thread pins {unpinned}")
    world = sdk(runtime_dir)
    _WORKER_MEASURE = recipe.build(world.run_strategy_backtest, world.factor_ic_series)


def _measure_in_worker(config: Mapping[str, object]) -> Mapping[str, object]:
    if _WORKER_MEASURE is None:
        raise RuntimeError("this process was not started as a stage worker (`_start_worker`)")
    return _WORKER_MEASURE(config)


@contextmanager
def _worker_processes(
    pool: WorkerPool, recipe: StageMeasureRecipe
) -> Iterator[ProcessPoolExecutor]:
    """`pool.workers` processes started with `spawn` (a forked DuckDB or thread pool is unsafe),
    each running `_start_worker`.

    **The thread pins are in each worker's environment from its first instruction.** A worker
    re-imports this driver before it runs anything handed to it, and so loads every library the
    driver does; a library reads its thread count when it loads. So the pins
    (`seeding.thread_count_pins`, ADR-0003's list) are put into this process's environment
    before the first worker is spawned, which every worker inherits, and this process's own
    values are restored once the pool is shut down. The shutdown cancels what no worker has
    started and waits for what one has, so no worker process outlives the pool.
    """
    pins = thread_count_pins()
    saved = {name: os.environ.get(name) for name in pins}
    os.environ.update(pins)
    try:
        executor = ProcessPoolExecutor(
            max_workers=pool.workers,
            mp_context=multiprocessing.get_context("spawn"),
            initializer=_start_worker,
            initargs=(pool.sdk, pool.runtime_dir, recipe),
        )
        try:
            yield executor
        finally:
            executor.shutdown(wait=True, cancel_futures=True)
    finally:
        for name, value in saved.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


def _run_in_workers(
    ledger: Path,
    stage: str,
    configs: Sequence[Config],
    recipe: StageMeasureRecipe,
    pool: WorkerPool,
    *,
    label_sessions: Callable[[Mapping[str, object]], int],
    sessions: Sequence[date],
    echo: Echo,
    clock: Clock,
) -> GridRun:
    """`_run`'s ledger and progress lines, measured by `pool`'s worker processes through
    `grid.run_grid_in_pool`: this process alone appends the rows, in configuration order, through
    `grid`'s own writer; a configuration the ledger holds is never submitted."""
    with _worker_processes(pool, recipe) as executor:
        run = grid.run_grid_in_pool(
            ledger,
            stage,
            configs,
            _measure_in_worker,
            executor=executor,
            label_sessions=label_sessions,
            sessions=sessions,
            clock=clock,
            result_extra={"code_commit": recipe.code_commit},
            landed=lambda index, config, ran: echo(_progress(index, len(configs), config, ran=ran)),
        )
    echo(_tally(ledger, run))
    return run


def _measure_stage(
    ledger: Path,
    stage: str,
    configs: Sequence[Config],
    recipe: StageMeasureRecipe,
    backtest: Callable[..., StrategyBacktest],
    ic_series: Callable[..., ICSeries] | None,
    *,
    label_sessions: Callable[[Mapping[str, object]], int],
    sessions: Sequence[date],
    echo: Echo,
    clock: Clock,
    pool: WorkerPool | None,
) -> GridRun:
    """The stage in this process (`_run`, with the measure over `backtest`), or with `pool` in
    its worker processes, which build the same measure over their own SDK and never call
    `backtest` or `ic_series`."""
    if pool is None:
        return _run(
            ledger,
            stage,
            configs,
            recipe.build(backtest, ic_series),
            label_sessions=label_sessions,
            sessions=sessions,
            echo=echo,
            clock=clock,
            code_commit=recipe.code_commit,
        )
    return _run_in_workers(
        ledger,
        stage,
        configs,
        recipe,
        pool,
        label_sessions=label_sessions,
        sessions=sessions,
        echo=echo,
        clock=clock,
    )


def run_discovery(
    ledger: Path,
    sessions: Sequence[date],
    backtest: Callable[..., StrategyBacktest],
    ic_series: Callable[..., ICSeries],
    code_commit: str,
    *,
    echo: Echo,
    clock: Clock = None,
    pool: WorkerPool | None = None,
) -> GridRun:
    """Section 4's stage: every discovery configuration the ledger does not hold yet."""
    head = _clean_commit(code_commit)
    return _measure_stage(
        ledger,
        DISCOVERY,
        discovery_configs(sessions),
        StageMeasureRecipe(code_commit=head, discovery_sessions=tuple(sessions)),
        backtest,
        ic_series,
        label_sessions=discovery_label_sessions,
        sessions=sessions,
        echo=echo,
        clock=clock,
        pool=pool,
    )


def run_composition_sources(
    ledger: Path,
    sessions: Sequence[date],
    backtest: Callable[..., StrategyBacktest],
    code_commit: str,
    *,
    echo: Echo,
    clock: Clock = None,
    pool: WorkerPool | None = None,
) -> GridRun:
    """Section 5 step 2a: the 19 sources over the survivors' components."""
    head = _require_stage_commit(ledger, COMPOSITION, code_commit)
    configs = composition_source_configs(_components(ledger, sessions), sessions, head)
    return _measure_stage(
        ledger,
        COMPOSITION,
        configs,
        StageMeasureRecipe(code_commit=head),
        backtest,
        None,
        label_sessions=grid.strategy_label_sessions,
        sessions=sessions,
        echo=echo,
        clock=clock,
        pool=pool,
    )


def run_composition_strategies(
    ledger: Path,
    sessions: Sequence[date],
    backtest: Callable[..., StrategyBacktest],
    code_commit: str,
    *,
    echo: Echo,
    clock: Clock = None,
    pool: WorkerPool | None = None,
) -> GridRun:
    """Section 5 step 2b: the 36 strategies of the best source; `run_grid` skips the one that
    is step 2a's own configuration."""
    head = _require_stage_commit(ledger, COMPOSITION, code_commit)
    _, source, row = best_source(ledger, sessions, head)
    echo(f"step 2b source: {_describe(source)} {row.config_id[:12]}")
    return _measure_stage(
        ledger,
        COMPOSITION,
        composition_strategy_configs(source),
        StageMeasureRecipe(code_commit=head),
        backtest,
        None,
        label_sessions=grid.strategy_label_sessions,
        sessions=sessions,
        echo=echo,
        clock=clock,
        pool=pool,
    )


def run_validation(
    ledger: Path,
    sessions: Sequence[date],
    backtest: Callable[..., StrategyBacktest],
    code_commit: str,
    *,
    echo: Echo,
    clock: Clock = None,
    pool: WorkerPool | None = None,
) -> GridRun:
    """Section 6's stage: each finalist once over the validation window, at the commit the
    composition stage measured it at (`StageCommitError` otherwise)."""
    head = _require_stage_commit(ledger, VALIDATION, code_commit)
    _require_composition_commit(ledger, head, "this checkout")
    _, finalist_configs = finalists(ledger, sessions)
    return _measure_stage(
        ledger,
        VALIDATION,
        validation_configs(finalist_configs, sessions, head),
        StageMeasureRecipe(code_commit=head),
        backtest,
        None,
        label_sessions=grid.strategy_label_sessions,
        sessions=sessions,
        echo=echo,
        clock=clock,
        pool=pool,
    )


def write_artifact(path: Path, body: Mapping[str, Any]) -> None:
    """Write a report once; the same report again is a no-op and a different one is refused."""
    content = (
        json.dumps(grid.to_json_value(body), sort_keys=True, indent=2, ensure_ascii=False) + "\n"
    ).encode("utf-8")
    if path.exists():
        if path.read_bytes() != content:
            raise ArtifactConflictError(
                f"{path} already holds a different report; the ledger now computes another one"
            )
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


# --- command line ------------------------------------------------------------------------------


class ResearchSDK(Protocol):
    """The two SDK methods the stages call."""

    @property
    def run_strategy_backtest(self) -> Callable[..., StrategyBacktest]: ...

    @property
    def factor_ic_series(self) -> Callable[..., ICSeries]: ...


@dataclass(frozen=True, slots=True, kw_only=True)
class Environment:
    """What a command reads from the world: the precondition, the stored calendar, the SDK, the
    checkout's commit and the repository. `default_environment` is the real one.

    `sdk` is also what each worker process builds its own SDK with under `--workers N` (N >= 2),
    so there it must pickle: a module-level function such as `open_sdk`, not a lambda
    (`WorkerFactoryError`)."""

    precondition: Callable[[Path], None]
    sessions: Callable[[Path], Sequence[date]]
    sdk: Callable[[Path], ResearchSDK]
    code_commit: Callable[[], str]
    repo: Path
    clock: Clock = None


def open_sdk(runtime_dir: Path) -> OpenAlphaSDK:
    """The real SDK over `runtime_dir`: module level, so a worker process can import it."""
    return OpenAlphaSDK(runtime_dir=runtime_dir)


def default_environment() -> Environment:
    return Environment(
        precondition=require_clean_return_paths,
        sessions=stored_sessions,
        sdk=open_sdk,
        code_commit=lambda: resolve_code_commit(anchor=Path(__file__).resolve().parent),
        repo=REPO_ROOT,
    )


def _worker_count(text: str) -> int:
    try:
        count = int(text)
    except ValueError:
        count = 0
    if count < 1:
        raise argparse.ArgumentTypeError(
            f"a worker count is a whole number of at least 1: {text!r}"
        )
    return count


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in COMMANDS:
        command = commands.add_parser(name)
        command.add_argument("--runtime-dir", type=Path, required=True)
        command.add_argument("--ledger", type=Path, required=True)
        if name in ("register", "holdout", "holdout-verdict"):
            command.add_argument("--registration", type=Path, default=DEFAULT_REGISTRATION)
        if name == "holdout":
            command.add_argument("--repo", type=Path, default=None)
        if name in WORKER_COMMANDS:
            command.add_argument(
                "--workers",
                type=_worker_count,
                default=1,
                help="measure configurations in N worker processes; the ledger is the same",
            )
    return parser


def _worker_pool(arguments: argparse.Namespace, environment: Environment) -> WorkerPool | None:
    """`--workers N` as a `WorkerPool`, `None` for one worker (the serial path); refuses a factory
    a worker process could not import before anything is measured."""
    workers = getattr(arguments, "workers", 1)
    if workers == 1:
        return None
    try:
        pickle.dumps(environment.sdk)
    except (pickle.PicklingError, AttributeError, TypeError) as error:
        raise WorkerFactoryError(
            f"--workers {workers} builds an SDK in each worker process, and the environment's "
            f"SDK factory {environment.sdk!r} cannot be sent to one ({error}); give a "
            "module-level function such as `open_sdk`"
        ) from error
    return WorkerPool(workers=workers, sdk=environment.sdk, runtime_dir=arguments.runtime_dir)


def _report_verdict(verdict: Mapping[str, Any], artifacts: Path, echo: Echo) -> None:
    """Write the verdict next to the ledger and print it."""
    write_artifact(artifacts / "p6-holdout-verdict.json", verdict)
    echo(f"保留期结论：{verdict['verdict']}（措辞：{verdict['wording']}）")
    echo(f"  {verdict['statement']}")
    for name, item in verdict["criteria"].items():
        echo(f"  {name}: {item['value']} vs {item['threshold']} -> {item['passed']}")


def _dispatch(arguments: argparse.Namespace, environment: Environment, echo: Echo) -> None:
    command, runtime_dir, ledger = arguments.command, arguments.runtime_dir, arguments.ledger
    pool = _worker_pool(arguments, environment)
    sessions = tuple(environment.sessions(runtime_dir))
    artifacts = ledger.parent
    clock = environment.clock
    if command == "survivors":
        body = survivors(ledger, sessions)
        write_artifact(artifacts / "p6-survivors.json", body)
        if body["ic_fdr_note"]:
            echo(f"warning: {body['ic_fdr_note']}")
        echo(f"survivors: {len(body['survivors'])}, fallback {body['fallback']}")
        echo(f"components: {body['components']}")
        return
    if command == "finalists":
        body, _ = finalists(ledger, sessions)
        write_artifact(artifacts / "p6-finalists.json", body)
        if body["note"]:
            echo(body["note"])
        echo(f"finalists: {body['finalists']}")
        return
    if command == "holdout-verdict":
        _report_verdict(holdout_verdict(ledger, sessions, arguments.registration), artifacts, echo)
        return
    sdk = environment.sdk(runtime_dir)
    if command == "holdout":
        repo = arguments.repo if arguments.repo is not None else environment.repo
        verdict = run_holdout_stage(
            ledger, sessions, arguments.registration, repo, sdk.run_strategy_backtest, clock=clock
        )
        _report_verdict(verdict, artifacts, echo)
        return
    commit = environment.code_commit()
    if command == "discovery":
        run_discovery(
            ledger,
            sessions,
            sdk.run_strategy_backtest,
            sdk.factor_ic_series,
            commit,
            echo=echo,
            clock=clock,
            pool=pool,
        )
    elif command == "composition-sources":
        run_composition_sources(
            ledger, sessions, sdk.run_strategy_backtest, commit, echo=echo, clock=clock, pool=pool
        )
    elif command == "composition-strategies":
        run_composition_strategies(
            ledger, sessions, sdk.run_strategy_backtest, commit, echo=echo, clock=clock, pool=pool
        )
    elif command == "validation":
        run_validation(
            ledger, sessions, sdk.run_strategy_backtest, commit, echo=echo, clock=clock, pool=pool
        )
        body, _, _ = validation_selection(ledger, sessions)
        write_artifact(artifacts / "p6-validation.json", body)
        echo(f"validation chose {body['chosen']}")
    elif command == "register":
        digest = register_holdout(
            ledger, sessions, arguments.registration, commit, environment.repo
        )
        echo(
            f"registered {arguments.registration} sha256={digest}; commit it before "
            "`holdout` runs -- the holdout has not run"
        )


def main(argv: Sequence[str] | None = None, *, environment: Environment | None = None) -> int:
    """Run one stage; exit 1 with the refusal's name when the protocol refuses it."""
    arguments = _parser().parse_args(argv)
    world = default_environment() if environment is None else environment
    try:
        if arguments.command not in READ_ONLY_COMMANDS:
            world.precondition(arguments.runtime_dir)
        if arguments.command in LEDGER_WRITING_COMMANDS:
            root = world.repo.resolve()
            registry._refuse_a_foreign_package(root)
            registry._refuse_foreign_scripts(root)
        _dispatch(arguments, world, print)
    except (
        P6Error,
        grid.ResearchLedgerError,
        registry.RegistrationError,
        registry.HoldoutRefusedError,
    ) as error:
        print(f"refused: {type(error).__name__}: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
