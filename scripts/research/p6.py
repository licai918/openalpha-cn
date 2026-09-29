"""The P6 research stage driver (`V2-P6-010`).

`docs/research/p6-protocol.md` is the pre-registered protocol, and this module is its stages as
code: every grid, every selection, every registration and the holdout verdict is computed here
from the protocol's constants and the research ledger, so no person types a grid, a survivor
list, a selection or a verdict. Each stage is one command::

    python scripts/research/p6.py <command> --runtime-dir <store> --ledger <ledger.jsonl>

Before any command does anything else it runs the section 1 precondition,
`openalpha factor stale-return-paths --runtime-dir <store> --exchange SSE --max-staleness-days 30`,
and refuses by name (`StaleReturnPathsError`) unless that printed `none` and exited 0.

Protocol clause -> code:

* **Section 1** -- the precondition: `precondition_argv`, `require_clean_return_paths`. The
  research code's commit enters every row this driver measures as the result's `code_commit`
  (`grid` itself records none; see "What the protocol says that the code does not" below). The
  reading instant `AS_OF` is every configuration's `as_of`.
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
  `config_id`, so `grid.run_grid` skips it); the finalists and the "BY rejected nothing" branch:
  `finalists`.
* **Section 6** -- `validation_configs`, and the choice by information ratio (ties to the lower
  mean turnover): `validation_selection`.
* **Section 7** -- the registration, which never runs the holdout: `register_holdout`; the one
  run and its verdict: `run_holdout_stage` and `evaluate_holdout`, with the maximum relative
  drawdown from `grid.result_max_relative_drawdown`.

**Nothing upstream is read from a file a person could edit.** Every command rebuilds the
configurations of the stages before it from the protocol's constants and the stored calendar,
recomputes each selection from the ledger, and refuses (`StageIncompleteError`) unless the stage
it reads holds exactly the configurations the protocol builds -- no missing one and no extra one,
because a family with a hand-added or a dropped row is not the protocol's family. The JSON
artifacts written next to the ledger are reports of those computations; they are written once
and a recomputation that disagrees with one is refused (`ArtifactConflictError`).

**A tie the protocol does not break is refused, not broken** (`UnresolvedTieError`): the protocol
breaks an information-ratio tie by the lower turnover and says nothing past that.

**One commit per stage where a configuration names one.** A walk-forward source carries its
`code_commit` (rule 8), so the same source at another commit is another configuration: resuming
the composition or validation stage at a commit other than the one its rows were measured at
would count one hypothesis twice. Those stages refuse (`StageCommitError`), as does every stage
asked to run from a checkout with uncommitted changes.

What the protocol says that the code does not (reported in `.superpowers/sdd/`, not repaired):

* Section 1 says `grid` records the research code's commit in every stage row. It does not;
  this driver records it in each result it writes (`code_commit`).
* Section 5 names no `combine` for the trailing-IC source. The request requires one;
  `zscore_sum`, the only combination the protocol names, is used, and the report says so.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from collections.abc import Callable, Mapping, Sequence
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
    "annualized_mean_net_excess_above": "0",
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
)
_FULL_COMMIT: Final[re.Pattern[str]] = re.compile(r"[0-9a-f]{40}")
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
    """Section 1's precondition did not answer `none` with exit 0."""


class ProtocolMismatchError(P6Error):
    """The build or the stored calendar is not the one the protocol was written against."""


class StageIncompleteError(P6Error):
    """A stage does not hold exactly the configurations the protocol builds for it."""


class UnresolvedTieError(P6Error):
    """Two configurations tie on information ratio and turnover where the choice matters."""


class UnrankableRowError(P6Error):
    """A measured configuration has no information ratio to rank it by."""


class StageCommitError(P6Error):
    """The checkout is not a clean commit, or not the commit the stage's rows were measured at."""


class NoFinalistsError(P6Error):
    """No configuration qualifies for the next stage, and the protocol names no fallback."""


class ArtifactConflictError(P6Error):
    """A report already written disagrees with what the ledger now computes."""


class HoldoutEvaluationError(P6Error):
    """The holdout's verdict cannot be computed against the registration it ran under."""


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
    """Refuse (`StaleReturnPathsError`) unless the stale-return-path detector printed exactly
    `none` and exited 0. Any other answer -- a stale build listed, a panel it could not read, a
    build of the CLI that lacks the command -- is not clean."""
    argv = precondition_argv(runtime_dir)
    outcome = runner(argv)
    if outcome.exit_code == 0 and outcome.stdout.strip() == "none":
        return
    said = (outcome.stdout.strip() or outcome.stderr.strip() or "nothing")[:2000]
    raise StaleReturnPathsError(
        f"section 1's precondition `openalpha {' '.join(argv)}` exited {outcome.exit_code} and "
        f"did not answer `none`; no stage runs on stale factor builds. It said: {said}"
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
    that no grid tried, or a grid other than the protocol's."""
    rows = {row.config_id: row for row in _stage_rows(ledger, stage)}
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
    """The one commit the stage's rows were measured at, or `None` for a stage with no row."""
    commits = {row.result.get("code_commit") for row in _stage_rows(ledger, stage)}
    if not commits:
        return None
    if len(commits) != 1 or not isinstance(next(iter(commits)), str):
        raise StageCommitError(
            f"the {stage} stage's rows name {sorted(map(str, commits))} as their commit; its "
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


def _measured(rows: Sequence[LedgerRow]) -> list[LedgerRow]:
    return [row for row in rows if "error" not in row.result]


def _rank_key(row: LedgerRow) -> tuple[float, float]:
    ratio, turnover = row.result.get("information_ratio"), row.result.get("mean_turnover")
    if not isinstance(ratio, int | float) or not isinstance(turnover, int | float):
        raise UnrankableRowError(
            f"configuration {row.config_id} has information ratio {ratio!r} and mean turnover "
            f"{turnover!r}; it cannot be ranked"
        )
    return (-float(ratio), float(turnover))


def rank_by_information_ratio(rows: Sequence[LedgerRow], take: int) -> list[LedgerRow]:
    """The best `take` rows by information ratio, a tie going to the lower mean turnover.

    Refuses (`UnresolvedTieError`) when two rows equal on both straddle the cut, since which one
    goes on is then a choice the protocol does not make."""
    ordered = sorted(rows, key=_rank_key)
    if 0 < take < len(ordered) and _rank_key(ordered[take - 1]) == _rank_key(ordered[take]):
        raise UnresolvedTieError(
            f"configurations {ordered[take - 1].config_id} and {ordered[take].config_id} tie on "
            "information ratio and mean turnover at the cut; the protocol breaks no further tie"
        )
    return ordered[:take]


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
    return {
        "schema": "openalpha-p6-survivors/v1",
        "stage": DISCOVERY,
        "family_size": grid.stage_family(ledger, DISCOVERY),
        "fdr_table": report.model_dump(mode="json"),
        "survivors": [_summary(row) for row in surviving],
        "chosen": [{"factor": factor, **_summary(row)} for factor, row in chosen],
        "components": components,
        "fallback": fallback,
        "stage_rows_sha256": _rows_digest(list(rows.values())),
    }


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
    """Step 2b's source: of step 2a's 19 rows, the highest information ratio (a tie to the lower
    turnover). Returns the 19 source configurations, the chosen one and its row."""
    sources = composition_source_configs(_components(ledger, sessions), sessions, code_commit)
    rows = _rows_for(ledger, COMPOSITION, sources, exact=False)
    measured = _measured(list(rows.values()))
    if not measured:
        raise NoFinalistsError("no step 2a score source was measured; step 2b has no source")
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

    The family is the whole composition stage (19 sources and step 2b's 35 new rows). A finalist
    is BY-rejected with a positive mean; the best five by information ratio go on, or all of them
    when fewer. **When BY rejects nothing** the best five measured configurations by information
    ratio go on regardless, and `no_configuration_passed_multiple_testing` is set with the note
    the report must carry. Rejections that all fail the mean leave no finalist, which the
    protocol provides no fallback for (`NoFinalistsError`).
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
    no_pass = report.discoveries == 0
    candidates = (
        _measured(list(rows.values()))
        if no_pass
        else [row for row in rows.values() if _passes(row, report)]
    )
    chosen = rank_by_information_ratio(candidates, take=FINALIST_COUNT)
    if not chosen:
        raise NoFinalistsError(
            f"BY rejected {report.discoveries} composition configuration(s) and none has a "
            "positive mean net excess; the protocol's fallback is written for no rejection only"
        )
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
    moved = {**config, "start": start, "end": end}
    if "walk_forward" in moved:
        moved["walk_forward"] = {**moved["walk_forward"], "code_commit": code_commit}
    return moved


def validation_configs(
    finalist_configs: Sequence[Config], sessions: Sequence[date], code_commit: str
) -> tuple[dict[str, Any], ...]:
    """Each finalist over section 6's window, a walk-forward one at the stage's commit."""
    start = stage_start(sessions, VALIDATION)
    end = stage_end(sessions, VALIDATION, label_sessions=grid.strategy_label_sessions({}))
    return tuple(_rewindowed(config, start, end, code_commit) for config in finalist_configs)


def validation_selection(
    ledger: Path, sessions: Sequence[date]
) -> tuple[dict[str, Any], dict[str, Any], LedgerRow]:
    """Section 6: of the finalists' validation rows, the highest information ratio of the net
    excess (a tie to the lower mean turnover). Returns the report of every finalist's result,
    the chosen configuration and its row."""
    commit = stage_commit(ledger, VALIDATION)
    if commit is None:
        raise StageIncompleteError(f"the validation stage has no row in {ledger}")
    _, finalist_configs = finalists(ledger, sessions)
    configs = validation_configs(finalist_configs, sessions, commit)
    rows = _rows_for(ledger, VALIDATION, configs, exact=True)
    measured = _measured(list(rows.values()))
    if not measured:
        raise NoFinalistsError("no finalist was measured over the validation window")
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


# --- section 7: registration and the holdout ------------------------------------------------------


def holdout_config(config: Config, sessions: Sequence[date], code_commit: str) -> dict[str, Any]:
    """The chosen configuration over section 7's window, a walk-forward one at `code_commit`."""
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


def register_holdout(
    ledger: Path, sessions: Sequence[date], registration: Path, code_commit: str
) -> str:
    """Write section 7's registration through `registry.register` and return its digest: the
    configuration section 6 chose over the holdout window, the criteria, the protocol settings
    and the commit. It does not run the holdout; committing the file is the next step."""
    commit = _clean_commit(code_commit)
    _, chosen, row = validation_selection(ledger, sessions)
    return registry.register(
        holdout_config(chosen, sessions, commit),
        holdout_criteria(row),
        registration,
        code_commit=commit,
        settings=grid.protocol_settings(),
    )


def _number(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float | str):
        raise HoldoutEvaluationError(f"{name} is {value!r}, not a number")
    return float(value)


def evaluate_holdout(
    result: Mapping[str, Any], criteria: Mapping[str, Any], validation_result: Mapping[str, Any]
) -> dict[str, Any]:
    """Section 7's verdict on the holdout's measurement, computed from the ledgered numbers.

    1. annualized net excess over the all-A equal-weight benchmark above the registered bound;
    2. the one-sided sign-flip p of the per-period excess (`grid.one_sided_p_value`) below its
       bound;
    3. the maximum relative drawdown over complete periods at most the registered multiple of the
       validation row's, which must be the drawdown the registration recorded.

    "通过" only when all three hold; otherwise "不通过", an unmeasured holdout included.
    """
    validation = grid.result_max_relative_drawdown(validation_result)
    if validation != criteria["validation_max_relative_drawdown"]:
        raise HoldoutEvaluationError(
            f"the validation row's maximum relative drawdown is {validation}, and the "
            f"registration recorded {criteria['validation_max_relative_drawdown']}"
        )
    multiple = _number(criteria["max_relative_drawdown_multiple_of_validation"], "the multiple")
    bounds = {
        "annualized_mean_net_excess": _number(
            criteria["annualized_mean_net_excess_above"], "the excess bound"
        ),
        "one_sided_p_excess": _number(criteria["one_sided_p_excess_below"], "the p bound"),
        "max_relative_drawdown": multiple * validation,
    }
    if "error" in result:
        items = {
            name: {"value": None, "threshold": bound, "passed": False}
            for name, bound in bounds.items()
        }
        return {"verdict": FAIL, "criteria": items, "error": result["error"]}
    values = {
        "annualized_mean_net_excess": _number(
            result["annualized_mean_net_excess"], "annualized_mean_net_excess"
        ),
        "one_sided_p_excess": grid.one_sided_p_value(
            _number(result["p_excess"], "p_excess"),
            _number(result["mean_net_excess"], "mean_net_excess"),
        ),
        "max_relative_drawdown": grid.result_max_relative_drawdown(result),
    }
    passed = {
        "annualized_mean_net_excess": values["annualized_mean_net_excess"]
        > bounds["annualized_mean_net_excess"],
        "one_sided_p_excess": values["one_sided_p_excess"] < bounds["one_sided_p_excess"],
        "max_relative_drawdown": values["max_relative_drawdown"] <= bounds["max_relative_drawdown"],
    }
    items = {
        name: {"value": values[name], "threshold": bounds[name], "passed": passed[name]}
        for name in bounds
    }
    return {"verdict": PASS if all(passed.values()) else FAIL, "criteria": items}


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
    verdict. The configuration is rebuilt from the ledger and the registration's commit, and
    `run_holdout` refuses it unless it is the registered one."""
    content = registration.read_bytes()
    registered = json.loads(content)
    _, chosen, validation_row = validation_selection(ledger, sessions)
    config = holdout_config(chosen, sessions, str(registered["code_commit"]))
    measure = grid.strategy_measure(backtest, excess_benchmark=grid.PRIMARY_EXCESS_BENCHMARK)
    row = registry.run_holdout(registration, ledger, repo, config, measure, clock=clock)
    if row["registration_sha256"] != hashlib.sha256(content).hexdigest():
        raise HoldoutEvaluationError("the registration changed while the holdout ran")
    criteria = registered["criteria"]
    if criteria["validation_config_id"] != validation_row.config_id:
        raise HoldoutEvaluationError(
            f"the registration compares with validation row {criteria['validation_config_id']} "
            f"and section 6 chose {validation_row.config_id}"
        )
    verdict = evaluate_holdout(row, criteria, validation_row.result)
    return {
        "schema": "openalpha-p6-holdout-verdict/v1",
        **verdict,
        "wording": "候选",
        "holdout_config_id": grid.config_id(config),
        "validation_config_id": validation_row.config_id,
        "registration_sha256": row["registration_sha256"],
        "registration_commit": row["registration_commit"],
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
) -> GridRun:
    """`grid.run_grid` one configuration at a time, so each is reported as it lands; resumable
    exactly as `run_grid` is."""
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
        )
        ran, skipped = ran + run.ran, skipped + run.skipped
        echo(
            f"[{index}/{len(configs)}] {'ran' if run.ran else 'skipped'} {_describe(config)} "
            f"{grid.config_id(config)[:12]}"
        )
    echo(f"{stage}: {ran} ran, {skipped} skipped, family {grid.stage_family(ledger, stage)}")
    return GridRun(stage=stage, ran=ran, skipped=skipped)


def run_discovery(
    ledger: Path,
    sessions: Sequence[date],
    backtest: Callable[..., StrategyBacktest],
    ic_series: Callable[..., ICSeries],
    code_commit: str,
    *,
    echo: Echo,
    clock: Clock = None,
) -> GridRun:
    """Section 4's stage: every discovery configuration the ledger does not hold yet."""
    measure = DiscoveryMeasure(
        backtest=backtest,
        ic_series=ic_series,
        sessions=tuple(sessions),
        code_commit=_clean_commit(code_commit),
    )
    return _run(
        ledger,
        DISCOVERY,
        discovery_configs(sessions),
        measure,
        label_sessions=discovery_label_sessions,
        sessions=sessions,
        echo=echo,
        clock=clock,
    )


def run_composition_sources(
    ledger: Path,
    sessions: Sequence[date],
    backtest: Callable[..., StrategyBacktest],
    code_commit: str,
    *,
    echo: Echo,
    clock: Clock = None,
) -> GridRun:
    """Section 5 step 2a: the 19 sources over the survivors' components."""
    head = _require_stage_commit(ledger, COMPOSITION, code_commit)
    configs = composition_source_configs(_components(ledger, sessions), sessions, head)
    return _run(
        ledger,
        COMPOSITION,
        configs,
        StrategyStageMeasure(backtest=backtest, code_commit=head),
        label_sessions=grid.strategy_label_sessions,
        sessions=sessions,
        echo=echo,
        clock=clock,
    )


def run_composition_strategies(
    ledger: Path,
    sessions: Sequence[date],
    backtest: Callable[..., StrategyBacktest],
    code_commit: str,
    *,
    echo: Echo,
    clock: Clock = None,
) -> GridRun:
    """Section 5 step 2b: the 36 strategies of the best source; `run_grid` skips the one that
    is step 2a's own configuration."""
    head = _require_stage_commit(ledger, COMPOSITION, code_commit)
    _, source, row = best_source(ledger, sessions, head)
    echo(f"step 2b source: {_describe(source)} {row.config_id[:12]}")
    return _run(
        ledger,
        COMPOSITION,
        composition_strategy_configs(source),
        StrategyStageMeasure(backtest=backtest, code_commit=head),
        label_sessions=grid.strategy_label_sessions,
        sessions=sessions,
        echo=echo,
        clock=clock,
    )


def run_validation(
    ledger: Path,
    sessions: Sequence[date],
    backtest: Callable[..., StrategyBacktest],
    code_commit: str,
    *,
    echo: Echo,
    clock: Clock = None,
) -> GridRun:
    """Section 6's stage: each finalist once over the validation window."""
    head = _require_stage_commit(ledger, VALIDATION, code_commit)
    _, finalist_configs = finalists(ledger, sessions)
    return _run(
        ledger,
        VALIDATION,
        validation_configs(finalist_configs, sessions, head),
        StrategyStageMeasure(backtest=backtest, code_commit=head),
        label_sessions=grid.strategy_label_sessions,
        sessions=sessions,
        echo=echo,
        clock=clock,
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
    checkout's commit and the repository. `default_environment` is the real one."""

    precondition: Callable[[Path], None]
    sessions: Callable[[Path], Sequence[date]]
    sdk: Callable[[Path], ResearchSDK]
    code_commit: Callable[[], str]
    repo: Path
    clock: Clock = None


def default_environment() -> Environment:
    return Environment(
        precondition=require_clean_return_paths,
        sessions=stored_sessions,
        sdk=lambda runtime_dir: OpenAlphaSDK(runtime_dir=runtime_dir),
        code_commit=lambda: resolve_code_commit(anchor=Path(__file__).resolve().parent),
        repo=REPO_ROOT,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in COMMANDS:
        command = commands.add_parser(name)
        command.add_argument("--runtime-dir", type=Path, required=True)
        command.add_argument("--ledger", type=Path, required=True)
        if name in ("register", "holdout"):
            command.add_argument("--registration", type=Path, default=DEFAULT_REGISTRATION)
        if name == "holdout":
            command.add_argument("--repo", type=Path, default=None)
    return parser


def _dispatch(arguments: argparse.Namespace, environment: Environment, echo: Echo) -> None:
    command, runtime_dir, ledger = arguments.command, arguments.runtime_dir, arguments.ledger
    sessions = tuple(environment.sessions(runtime_dir))
    artifacts = ledger.parent
    clock = environment.clock
    if command == "survivors":
        body = survivors(ledger, sessions)
        write_artifact(artifacts / "p6-survivors.json", body)
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
    sdk = environment.sdk(runtime_dir)
    if command == "holdout":
        repo = arguments.repo if arguments.repo is not None else environment.repo
        verdict = run_holdout_stage(
            ledger, sessions, arguments.registration, repo, sdk.run_strategy_backtest, clock=clock
        )
        write_artifact(artifacts / "p6-holdout-verdict.json", verdict)
        echo(f"保留期结论：{verdict['verdict']}（措辞：{verdict['wording']}）")
        for name, item in verdict["criteria"].items():
            echo(f"  {name}: {item['value']} vs {item['threshold']} -> {item['passed']}")
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
        )
    elif command == "composition-sources":
        run_composition_sources(
            ledger, sessions, sdk.run_strategy_backtest, commit, echo=echo, clock=clock
        )
    elif command == "composition-strategies":
        run_composition_strategies(
            ledger, sessions, sdk.run_strategy_backtest, commit, echo=echo, clock=clock
        )
    elif command == "validation":
        run_validation(ledger, sessions, sdk.run_strategy_backtest, commit, echo=echo, clock=clock)
        body, _, _ = validation_selection(ledger, sessions)
        write_artifact(artifacts / "p6-validation.json", body)
        echo(f"validation chose {body['chosen']}")
    elif command == "register":
        digest = register_holdout(ledger, sessions, arguments.registration, commit)
        echo(
            f"registered {arguments.registration} sha256={digest}; commit it before "
            "`holdout` runs -- the holdout has not run"
        )


def main(argv: Sequence[str] | None = None, *, environment: Environment | None = None) -> int:
    """Run one stage; exit 1 with the refusal's name when the protocol refuses it."""
    arguments = _parser().parse_args(argv)
    world = default_environment() if environment is None else environment
    try:
        world.precondition(arguments.runtime_dir)
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
