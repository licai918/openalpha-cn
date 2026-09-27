"""The research grid runner and the research ledger (`V2-P6-008`).

The research protocol (section 2 of `docs/superpowers/plans/2026-09-26-selection-ready.md`) asks
for three numbers that must never be typed by a person, and this module is where each is computed:

* **The family size.** Every configuration a stage tried is one row of that stage in an
  append-only JSON Lines ledger, whether it produced a p-value or was refused, and
  `stage_family` counts the rows. `fdr_table` hands `control_false_discovery_rate` that count as
  `family_size` and only the rows carrying a p-value as its tests, so a refused configuration is a
  *withheld* hypothesis -- `multiple_testing`'s conservative direction -- rather than one that was
  never tried.
* **The non-overlapping sample.** `non_overlapping` takes one session in every `horizon`, so the
  labels the sign-flip test sees share no session. `embargo` drops the last `horizon` sessions of
  a segment, which are the as-ofs whose label would end in the next segment. A strategy
  backtest's periods run from one signal close to the next and share no session by construction,
  so `strategy_measure` tests every period it is handed.
* **The dependence assumption.** `fdr_table` asks for `arbitrary` (Benjamini-Yekutieli) and has
  no parameter that asks for anything else.

A configuration is data. `run_grid` hands each one to a `Measure` and never reads it, and
`strategy_measure` passes it to `OpenAlphaSDK.run_strategy_backtest` as keyword arguments
unchanged, so a score-source kind the SDK gains later is a key in a grid rather than a change
here. The ledger stores a configuration in its canonical JSON form (dates and datetimes as ISO
text, decimals as their exact text, tuples as lists, keys sorted), and a configuration's identity
is the SHA-256 of that form. A configuration is one row of its stage: a second is refused, which
is also what lets an interrupted `run_grid` resume by skipping what the ledger already holds.

The holdout is not run here. `run_grid` refuses the holdout stage and `append_ledger` refuses a
second holdout row; `registry.run_holdout` is the one path to the first, behind
`registry.assert_holdout_allowed`.

What this module does not do: it computes no statistic of its own (`sign_flip_test` and
`control_false_discovery_rate` are the repository's), it measures no rank IC (no SDK method
returns a per-as-of IC series; `non_overlapping_sign_flip` tests one a caller supplies), and a
p-value here is two-sided, as `sign_flip_test`'s is.
"""

from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import math
import os
import statistics
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Final

from openalpha_cn.backtest.multiple_testing import (
    DependenceAssumption,
    HypothesisTest,
    MultipleTestingReport,
    MultipleTestingRequest,
    control_false_discovery_rate,
)
from openalpha_cn.backtest.outcome_statistics import sign_flip_test
from openalpha_cn.backtest.strategy_backtest import StrategyBacktest, StrategyBacktestError
from openalpha_cn.strategy_view import StrategyViewError

LEDGER_SCHEMA: Final[str] = "openalpha-research-ledger/v1"
HOLDOUT_STAGE: Final[str] = "holdout"

PROTOCOL_BOOTSTRAP_SAMPLES: Final[int] = 100_000
PROTOCOL_RANDOM_SEED: Final[int] = 20_260_926
PROTOCOL_FALSE_DISCOVERY_RATE: Final[float] = 0.10
PROTOCOL_DEPENDENCE: Final[DependenceAssumption] = "arbitrary"
DEFAULT_P_VALUE_KEY: Final[str] = "p_excess"
"""The p-value `fdr_table` controls unless told otherwise: the sign-flip test of the per-period
net-of-cost excess return, which is what the protocol's selection metric and holdout criteria are
measured on. A stage whose rows also carry `p_ic` controls that family by naming it."""

SESSIONS_PER_YEAR: Final[int] = 244
"""The trading sessions a year is annualised over: the Shanghai calendar's typical count (243 or
244 in 2015-2023). The protocol does not fix it; the annualised figures below are proportional to
its square root (information ratio) or to it (mean excess), so a different count rescales every
configuration alike and changes no ranking between two configurations of one rebalance interval."""

Measure = Callable[[Mapping[str, object]], Mapping[str, object]]
"""One configuration in, one ledger result out. Raising a refusal records a failed row."""

DEFAULT_REFUSALS: Final[tuple[type[Exception], ...]] = (StrategyViewError, StrategyBacktestError)
"""The errors that mean *this configuration* cannot be measured -- a request the SDK refuses, a
tier not built, a look-ahead row, a backtest the book refuses. Recorded as a failed row, which
still counts in the family. Anything else stops the run and writes nothing."""


class ResearchLedgerError(ValueError):
    """A ledger that cannot be read or written the way the protocol requires."""


@dataclass(frozen=True, slots=True, kw_only=True)
class LedgerRow:
    """One row as read back: the stage, the configuration's identity and form, and its result."""

    line: int
    stage: str
    config_id: str
    config: Mapping[str, Any]
    result: Mapping[str, Any]
    recorded_at: datetime


@dataclass(frozen=True, slots=True, kw_only=True)
class GridRun:
    """What one `run_grid` call did: rows it wrote and configurations the ledger already held."""

    stage: str
    ran: int
    skipped: int


# --- canonical form ---------------------------------------------------------------------------


def _encode(value: object) -> object:
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError(f"{value!r} is not a finite decimal")
        return str(value)
    if isinstance(value, datetime):
        if value.tzinfo is None:
            raise ValueError(f"{value!r} has no time zone; a ledger instant must be aware")
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Mapping):
        return dict(value)
    raise TypeError(f"{type(value).__name__} {value!r} has no canonical JSON form")


def canonical_json(value: object) -> str:
    """`value` as compact JSON with sorted keys, or `ResearchLedgerError` when it has no such form.

    A non-finite float or decimal, a naive datetime and an unknown type are refused rather than
    written, because a row that does not read back as what was measured is not a row of evidence.
    """
    try:
        return json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
            default=_encode,
        )
    except (TypeError, ValueError) as error:
        raise ResearchLedgerError(f"not writable to the research ledger: {error}") from error


def to_json_value(value: object) -> Any:
    """`value` as it reads back from the ledger: the canonical JSON form, decoded."""
    return json.loads(canonical_json(value))


def config_id(config: Mapping[str, object]) -> str:
    """The SHA-256 of a configuration's canonical JSON form: its identity within a stage."""
    return hashlib.sha256(canonical_json(config).encode("utf-8")).hexdigest()


# --- the ledger --------------------------------------------------------------------------------


def read_ledger(path: Path) -> tuple[LedgerRow, ...]:
    """Every row of the ledger at `path`, in order; `()` when there is no ledger yet.

    A line that is not a whole row -- a torn write, an edit -- is refused with its line number
    rather than skipped: a row that silently dropped out of a read would shrink its stage's family.
    """
    if not path.exists():
        return ()
    rows: list[LedgerRow] = []
    with path.open(encoding="utf-8") as handle:
        for number, line in enumerate(handle, start=1):
            try:
                rows.append(_parse_row(number, line))
            except (ValueError, TypeError, KeyError) as error:
                raise ResearchLedgerError(
                    f"{path} line {number} is not a research ledger row: {error}"
                ) from error
    return tuple(rows)


def _parse_row(number: int, line: str) -> LedgerRow:
    body = json.loads(line)
    if not isinstance(body, dict) or body.get("schema") != LEDGER_SCHEMA:
        raise ValueError(f"expected an object whose schema is {LEDGER_SCHEMA!r}")
    stage, identity = body["stage"], body["config_id"]
    config, result = body["config"], body["result"]
    if not (isinstance(stage, str) and isinstance(identity, str)):
        raise ValueError("stage and config_id must be text")
    if not (isinstance(config, dict) and isinstance(result, dict)):
        raise ValueError("config and result must be objects")
    recorded_at = datetime.fromisoformat(body["recorded_at"])
    if recorded_at.tzinfo is None:
        raise ValueError("recorded_at has no time zone")
    return LedgerRow(
        line=number,
        stage=stage,
        config_id=identity,
        config=config,
        result=result,
        recorded_at=recorded_at,
    )


def _checked_stage(stage: str) -> str:
    if not isinstance(stage, str) or not stage or stage != stage.strip():
        raise ResearchLedgerError(f"stage {stage!r} must be non-empty text with no outer space")
    return stage


def _append(
    path: Path,
    existing: list[LedgerRow],
    stage: str,
    config: Mapping[str, object],
    result: Mapping[str, object],
    *,
    recorded_at: datetime,
) -> LedgerRow:
    """Append one row after checking it against `existing`, which is the ledger as held in memory
    and is extended with the new row."""
    stage = _checked_stage(stage)
    if recorded_at.tzinfo is None:
        raise ResearchLedgerError("recorded_at must carry a time zone")
    identity = config_id(config)
    if stage == HOLDOUT_STAGE and any(row.stage == HOLDOUT_STAGE for row in existing):
        raise ResearchLedgerError(
            f"{path} already holds a {HOLDOUT_STAGE!r} row; the holdout runs once"
        )
    if any(row.stage == stage and row.config_id == identity for row in existing):
        raise ResearchLedgerError(
            f"configuration {identity} is already a row of stage {stage!r} in {path}; a "
            "configuration is one hypothesis of its stage, and the row there is the one its "
            "family counts"
        )
    body = {
        "schema": LEDGER_SCHEMA,
        "stage": stage,
        "config_id": identity,
        "config": to_json_value(config),
        "result": to_json_value(result),
        "recorded_at": recorded_at.isoformat(),
    }
    line = canonical_json(body)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    row = _parse_row(len(existing) + 1, line)
    existing.append(row)
    return row


def append_ledger(
    path: Path,
    stage: str,
    config: Mapping[str, object],
    result: Mapping[str, object],
    *,
    recorded_at: datetime | None = None,
) -> None:
    """Append one configuration's row to `stage`. Refuses a configuration the stage already holds,
    a second holdout row, and a value with no canonical JSON form. `recorded_at` defaults to now."""
    _append(
        path,
        list(read_ledger(path)),
        stage,
        config,
        result,
        recorded_at=_utc_now() if recorded_at is None else recorded_at,
    )


def stage_family(path: Path, stage: str) -> int:
    """The family size of `stage`: every row of it, refused configurations included."""
    return sum(1 for row in read_ledger(path) if row.stage == stage)


def hypothesis_test_name(p_value_key: str) -> str:
    """The procedure a ledger p-value came from, as `HypothesisTest.test` records it."""
    return (
        f"{p_value_key}: two-sided sign-flip randomization test "
        "(openalpha_cn.backtest.outcome_statistics.sign_flip_test)"
    )


def fdr_table(
    path: Path, stage: str, q: float, *, p_value_key: str = DEFAULT_P_VALUE_KEY
) -> MultipleTestingReport:
    """Benjamini-Yekutieli false-discovery control over one stage, at rate `q`.

    The family is `stage_family` -- every row -- and the tests are the rows whose result carries
    `p_value_key`; the rest are reported as withheld. Each hypothesis is named by its
    configuration's identity. Refuses a stage with no row, and one where no row has the p-value.
    """
    family = stage_family(path, stage)
    if family == 0:
        raise ResearchLedgerError(f"stage {stage!r} has no row in {path}")
    tests: list[HypothesisTest] = []
    for row in read_ledger(path):
        if row.stage != stage or p_value_key not in row.result:
            continue
        p_value = row.result[p_value_key]
        if isinstance(p_value, bool) or not isinstance(p_value, int | float):
            raise ResearchLedgerError(
                f"{path} line {row.line} carries {p_value_key}={p_value!r}, which is not a number"
            )
        tests.append(
            HypothesisTest(
                hypothesis_id=row.config_id,
                p_value=float(p_value),
                test=hypothesis_test_name(p_value_key),
            )
        )
    if not tests:
        raise ResearchLedgerError(
            f"stage {stage!r} has {family} row(s) in {path} and none carries {p_value_key!r}; "
            "there is no p-value to control"
        )
    return control_false_discovery_rate(
        MultipleTestingRequest(
            tests=tuple(tests),
            family_size=family,
            false_discovery_rate=q,
            dependence=PROTOCOL_DEPENDENCE,
        )
    )


# --- sampling ----------------------------------------------------------------------------------


def _checked_sessions(sessions: Sequence[date], horizon_sessions: int) -> tuple[date, ...]:
    if horizon_sessions < 1:
        raise ValueError(f"horizon_sessions must be at least 1, not {horizon_sessions}")
    ordered = tuple(sessions)
    if any(later <= earlier for earlier, later in itertools.pairwise(ordered)):
        raise ValueError("the sessions must be strictly ascending, with no repeat")
    return ordered


def non_overlapping(as_ofs: Sequence[date], horizon_sessions: int) -> tuple[date, ...]:
    """One as-of in every `horizon_sessions`, starting at the first.

    `as_ofs` are consecutive trading sessions, so two picks are `horizon_sessions` sessions apart
    and their `horizon_sessions`-session labels share no session.
    """
    return _checked_sessions(as_ofs, horizon_sessions)[::horizon_sessions]


def embargo(sessions: Sequence[date], horizon_sessions: int) -> tuple[date, ...]:
    """A segment's sessions without its last `horizon_sessions`, whose labels end past the segment.

    The protocol's gap between two segments: an as-of `horizon_sessions` sessions or fewer from a
    segment's end is labelled by sessions of the next segment, so it is not an as-of of this one.
    """
    ordered = _checked_sessions(sessions, horizon_sessions)
    return ordered[: max(0, len(ordered) - horizon_sessions)]


def non_overlapping_sign_flip(
    values: Mapping[date, float],
    sessions: Sequence[date],
    horizon_sessions: int,
    *,
    bootstrap_samples: int = PROTOCOL_BOOTSTRAP_SAMPLES,
    random_seed: int = PROTOCOL_RANDOM_SEED,
) -> dict[str, object]:
    """The sign-flip test of a dated series (a rank IC per as-of) on its non-overlapping sample.

    The sample is taken over `sessions`, not over the dates that carry a value, so an as-of that
    measured nothing is a gap in the sample rather than a reason to shift every later pick.
    """
    sampled = non_overlapping(sessions, horizon_sessions)
    measured = tuple(values[day] for day in sampled if day in values)
    if not measured:
        raise ValueError("no sampled session carries a value; there is nothing to test")
    if not all(math.isfinite(value) for value in measured):
        raise ValueError("a sampled value is not finite")
    test = sign_flip_test(measured, bootstrap_samples=bootstrap_samples, random_seed=random_seed)
    return {
        "p_value": test.p_value,
        "exact": test.exact,
        "sign_patterns": test.sign_patterns,
        "random_seed": test.random_seed,
        "sampled_sessions": len(sampled),
        "measured": len(measured),
    }


# --- the strategy measurement ------------------------------------------------------------------


def strategy_result(
    backtest: StrategyBacktest,
    *,
    excess_benchmark: str,
    bootstrap_samples: int = PROTOCOL_BOOTSTRAP_SAMPLES,
    random_seed: int = PROTOCOL_RANDOM_SEED,
) -> dict[str, object]:
    """One backtest as a ledger result: its per-period series and the sign-flip test of its excess.

    Excess is each period's net-of-cost return less `excess_benchmark`'s return over the same
    period. Every series is stored so every figure beside it can be recomputed from the row.
    """
    periods = backtest.periods
    if not periods:
        raise StrategyBacktestError("the backtest produced no period; there is nothing to test")
    missing = [p.start for p in periods if excess_benchmark not in p.benchmark_returns]
    if missing:
        raise StrategyBacktestError(
            f"benchmark {excess_benchmark!r} has no return for the period starting {missing[0]}"
        )
    excess = tuple(p.net_return - p.benchmark_returns[excess_benchmark] for p in periods)
    values = tuple(float(value) for value in excess)
    test = sign_flip_test(values, bootstrap_samples=bootstrap_samples, random_seed=random_seed)
    sessions = [p.sessions for p in periods]
    per_year = SESSIONS_PER_YEAR / statistics.fmean(sessions)
    mean = statistics.fmean(values)
    stdev = statistics.stdev(values) if len(values) > 1 else None
    information_ratio = None if not stdev else mean / stdev * math.sqrt(per_year)
    return {
        "excess_benchmark": excess_benchmark,
        "period_count": len(periods),
        "period_starts": [p.start for p in periods],
        "period_ends": [p.end for p in periods],
        "period_sessions": sessions,
        "net_return": [str(p.net_return) for p in periods],
        "benchmark_return": [str(p.benchmark_returns[excess_benchmark]) for p in periods],
        "net_excess": [str(value) for value in excess],
        "turnover": [str(p.turnover) for p in periods],
        "p_excess": test.p_value,
        "p_excess_exact": test.exact,
        "p_excess_sign_patterns": test.sign_patterns,
        "p_excess_random_seed": test.random_seed,
        "mean_net_excess": mean,
        "stdev_net_excess": stdev,
        "information_ratio": information_ratio,
        "annualized_mean_net_excess": mean * per_year,
        "mean_turnover": statistics.fmean(float(p.turnover) for p in periods),
    }


def strategy_measure(
    backtest: Callable[..., StrategyBacktest],
    *,
    excess_benchmark: str,
    bootstrap_samples: int = PROTOCOL_BOOTSTRAP_SAMPLES,
    random_seed: int = PROTOCOL_RANDOM_SEED,
) -> Measure:
    """A `Measure` that calls `backtest(**config)` -- `OpenAlphaSDK.run_strategy_backtest`, say --
    with the configuration unchanged, and reduces the answer with `strategy_result`."""

    def measure(config: Mapping[str, object]) -> Mapping[str, object]:
        return strategy_result(
            backtest(**config),
            excess_benchmark=excess_benchmark,
            bootstrap_samples=bootstrap_samples,
            random_seed=random_seed,
        )

    return measure


# --- the grid ----------------------------------------------------------------------------------


def expand_grid(grid: Mapping[str, Sequence[object]]) -> tuple[Mapping[str, object], ...]:
    """Every combination of the grid's values, one configuration each, in one fixed order.

    Keys are taken in sorted order and each dimension's values in the order given, so the same grid
    written with its keys in another order is the same sequence. A dimension with no value is
    refused: it would make the product empty, and an empty stage reads as one that found nothing.
    So is a dimension given as a bare string, which would expand one value per character.
    """
    keys = sorted(grid)
    text = [key for key in keys if isinstance(grid[key], str | bytes)]
    if text:
        raise ValueError(
            f"grid dimension(s) {text} are text, which would expand one value per character; "
            "give a sequence of values"
        )
    empty = [key for key in keys if len(grid[key]) == 0]
    if empty:
        raise ValueError(f"grid dimension(s) {empty} carry no value")
    return tuple(
        dict(zip(keys, values, strict=True))
        for values in itertools.product(*(tuple(grid[key]) for key in keys))
    )


def run_grid(
    ledger: Path,
    stage: str,
    configs: Sequence[Mapping[str, object]],
    measure: Measure,
    *,
    refusals: tuple[type[Exception], ...] = DEFAULT_REFUSALS,
    clock: Callable[[], datetime] | None = None,
) -> GridRun:
    """Measure every configuration the ledger does not already hold for `stage`, one row each.

    A configuration `measure` refuses (one of `refusals`) is a row whose result is its error, and
    it counts in the family. The holdout stage is refused: it runs through `registry.run_holdout`.
    """
    stage = _checked_stage(stage)
    if stage == HOLDOUT_STAGE:
        raise ResearchLedgerError(
            f"the {HOLDOUT_STAGE!r} stage runs once, through registry.run_holdout behind its "
            "registration guard, and never from a grid"
        )
    now = _utc_now if clock is None else clock
    existing = list(read_ledger(ledger))
    ran = skipped = 0
    for config in configs:
        identity = config_id(config)
        if any(row.stage == stage and row.config_id == identity for row in existing):
            skipped += 1
            continue
        try:
            result = dict(measure(config))
        except refusals as error:
            result = {"error": f"{type(error).__name__}: {error}"}
        _append(ledger, existing, stage, config, result, recorded_at=now())
        ran += 1
    return GridRun(stage=stage, ran=ran, skipped=skipped)


def _utc_now() -> datetime:
    return datetime.now(UTC)


# --- command line ------------------------------------------------------------------------------


def main(argv: Sequence[str] | None = None) -> int:
    """`family` prints a stage's family size; `fdr` prints its false-discovery table as JSON."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("family", "fdr"):
        command = commands.add_parser(name)
        command.add_argument("--ledger", type=Path, required=True)
        command.add_argument("--stage", required=True)
        if name == "fdr":
            command.add_argument("--q", type=float, default=PROTOCOL_FALSE_DISCOVERY_RATE)
            command.add_argument("--p-value-key", default=DEFAULT_P_VALUE_KEY)
    arguments = parser.parse_args(argv)
    if arguments.command == "family":
        print(stage_family(arguments.ledger, arguments.stage))
        return 0
    report = fdr_table(
        arguments.ledger, arguments.stage, arguments.q, p_value_key=arguments.p_value_key
    )
    print(report.model_dump_json(indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
