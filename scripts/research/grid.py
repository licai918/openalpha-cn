"""The research grid runner and the research ledger (`V2-P6-008`).

The research protocol (section 2 of `docs/superpowers/plans/2026-09-26-selection-ready.md`) asks
for numbers and boundaries that must never be typed by a person, and this module is where each is
computed:

* **The stages and their windows.** `STAGES` is the protocol's closed vocabulary and
  `PROTOCOL_STAGE_WINDOWS` its segments: discovery and composition 2015-01-05..2021-12-31,
  validation 2022-01-04..2023-12-29, holdout 2024-01-02 onwards; forward starts the day after the
  committed registration's commit date, which only `registry.run_forward` derives (through the
  same admission as the holdout guard) -- `run_grid` refuses the forward stage, because a boundary
  a caller could type would reopen the holdout window. `run_grid` reads a configuration's
  measured window from its `start` and `end` keys and nowhere else (an `as_of` clock legitimately
  sits years later), extends `end` by the configuration's label length in sessions, and refuses a
  window that leaves its stage **before measuring it**: the refusal is a row, so it counts in the
  family, and nothing was measured on a segment the stage may not see. That is what stops a
  "validation" configuration from reading the holdout.
* **The family size.** Every configuration a stage tried is one row of that stage in an
  append-only JSON Lines ledger, whether it produced a p-value or was refused, and
  `stage_family` counts the rows. `fdr_table` hands `control_false_discovery_rate` that count as
  `family_size` and only the rows carrying the named p-value as its tests, so a refused
  configuration is a *withheld* hypothesis -- `multiple_testing`'s conservative direction --
  rather than one that was never tried. **A configuration whose measurement crashed** (an error
  outside `DEFAULT_REFUSALS`, which stops the run and writes nothing) **must be fixed and re-run,
  never dropped from the grid**: the family counts it only once it has a row, so removing it from
  the grid instead removes a hypothesis that was tried.
* **The families.** Per the protocol, a stage's primary family is the sign-flip p-value of the
  per-period net excess over the all-A equal-weight benchmark (`p_excess`, the default
  `p_value_key`), and rank-IC p-values (`p_ic`) are a separate, secondary family of the same
  size. One `fdr_table` call controls one family; there is no combined family.
* **The non-overlapping sample.** `non_overlapping` takes one session in every `horizon`, so the
  labels the sign-flip test sees share no session. `embargo` drops the last `horizon` sessions of
  a segment. A strategy backtest's periods run from one signal close to the next and share no
  session by construction; a trailing period shorter than one rebalance interval is stored but
  excluded from the test and the means, and the row says how many were excluded.
* **The dependence assumption.** `fdr_table` asks for `arbitrary` (Benjamini-Yekutieli) and has
  no parameter that asks for anything else.

A configuration is data. `run_grid` hands each one to a `Measure` and reads only `start` and `end`,
and `strategy_measure` passes it to `OpenAlphaSDK.run_strategy_backtest` as keyword arguments
unchanged, so a score-source kind the SDK gains later is a key in a grid rather than a change
here. The ledger stores a configuration in its canonical JSON form (dates and datetimes as ISO
text, decimals as their exact text, tuples as lists, keys sorted), and a configuration's identity
is the SHA-256 of that form. A configuration is one row of its stage: a second is refused, which
is also what lets an interrupted `run_grid` resume by skipping what the ledger already holds.

The holdout is not written here by any public function. `run_grid` refuses the holdout stage and
`append_ledger` refuses it too; the only writer is `registry.run_holdout`, which goes through the
private `_append_holdout`: one `holdout_claim` row carrying the registration's digest, commit and
measurement settings, written **before** measuring, then one `measurement` row of the same
configuration carrying the same binding. A crash between the two leaves the claim, and the claim
alone is the one run.

What this module does not do: it computes no statistic of its own (`sign_flip_test` and
`control_false_discovery_rate` are the repository's; `one_sided_p_value` is the protocol's
declared derivation from the two-sided one), and it measures no rank IC (no SDK method returns a
per-as-of IC series; `non_overlapping_sign_flip` tests one a caller supplies).
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
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, Final, Literal, Protocol

from openalpha_cn.backtest.multiple_testing import (
    DependenceAssumption,
    HypothesisTest,
    MultipleTestingReport,
    MultipleTestingRequest,
    control_false_discovery_rate,
)
from openalpha_cn.backtest.outcome_statistics import sign_flip_test
from openalpha_cn.backtest.strategy_backtest import (
    EQUAL_WEIGHT_ALL_A,
    StrategyBacktest,
    StrategyBacktestError,
)
from openalpha_cn.strategy_view import StrategyViewError

LEDGER_SCHEMA: Final[str] = "openalpha-research-ledger/v2"
"""v2 rows carry a `kind` (`measurement` or `holdout_claim`)."""
RETIRED_LEDGER_SCHEMAS: Final[tuple[str, ...]] = ("openalpha-research-ledger/v1",)
"""Row versions this module refuses by name. v1 had no `kind`; no v1 research ledger was ever used
for research, so there is no migration: a v1 ledger is re-run."""

STAGES: Final[tuple[str, ...]] = ("discovery", "composition", "validation", "holdout", "forward")
"""The protocol's stages, and the only stage labels the ledger accepts."""
HOLDOUT_STAGE: Final[str] = "holdout"
FORWARD_STAGE: Final[str] = "forward"

RowKind = Literal["measurement", "holdout_claim"]
MEASUREMENT: Final[RowKind] = "measurement"
HOLDOUT_CLAIM: Final[RowKind] = "holdout_claim"
HOLDOUT_BINDING: Final[tuple[str, ...]] = ("registration_sha256", "registration_commit")
"""What every holdout row carries: the registration it ran under."""


@dataclass(frozen=True, slots=True)
class StageWindow:
    """A stage's first session day and last one; `last=None` is open-ended."""

    first: date
    last: date | None


PROTOCOL_STAGE_WINDOWS: Final[Mapping[str, StageWindow]] = {
    "discovery": StageWindow(date(2015, 1, 5), date(2021, 12, 31)),
    "composition": StageWindow(date(2015, 1, 5), date(2021, 12, 31)),
    "validation": StageWindow(date(2022, 1, 4), date(2023, 12, 29)),
    "holdout": StageWindow(date(2024, 1, 2), None),
}
"""Section 2's segments. Forward has no fixed window: it starts the day after the committed
registration's commit date, which `registry.run_forward` derives and passes to `_run_grid`."""

PROTOCOL_BOOTSTRAP_SAMPLES: Final[int] = 100_000
PROTOCOL_RANDOM_SEED: Final[int] = 20_260_926
PROTOCOL_FALSE_DISCOVERY_RATE: Final[float] = 0.10
PROTOCOL_DEPENDENCE: Final[DependenceAssumption] = "arbitrary"
PRIMARY_EXCESS_BENCHMARK: Final[str] = EQUAL_WEIGHT_ALL_A
"""The benchmark the primary family's excess return is measured against (the protocol's
decision); `000905.SH` is reported beside it and tests nothing."""
DEFAULT_P_VALUE_KEY: Final[str] = "p_excess"
"""The primary family's p-value. `p_ic` is the secondary family's, controlled by naming it."""

SESSIONS_PER_YEAR: Final[int] = 244
"""The trading sessions a year is annualised over, fixed by the protocol."""

Measure = Callable[[Mapping[str, object]], Mapping[str, object]]
"""One configuration in, one ledger result out. Raising a refusal records a failed row."""


class SettledMeasure(Protocol):
    """A measure that states the settings it measures under, which the holdout compares with the
    registration's before it runs."""

    @property
    def settings(self) -> Mapping[str, object]: ...

    def __call__(self, config: Mapping[str, object]) -> Mapping[str, object]: ...


DEFAULT_REFUSALS: Final[tuple[type[Exception], ...]] = (StrategyViewError, StrategyBacktestError)
"""The errors that mean *this configuration* cannot be measured -- a request the SDK refuses, a
tier not built, a look-ahead row, a backtest the book refuses. Recorded as a failed row, which
still counts in the family. Anything else stops the run and writes nothing."""


class ResearchLedgerError(ValueError):
    """A ledger that cannot be read or written the way the protocol requires."""


class StageWindowError(ResearchLedgerError):
    """A configuration's measured window leaves its stage. Recorded as a refused row."""


@dataclass(frozen=True, slots=True, kw_only=True)
class LedgerRow:
    """One row as read back: stage, kind, the configuration's identity and form, and its result."""

    line: int
    stage: str
    kind: RowKind
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


def protocol_settings(excess_benchmark: str = PRIMARY_EXCESS_BENCHMARK) -> dict[str, object]:
    """The measurement settings the protocol fixes, as a registration and a measure state them."""
    return {
        "bootstrap_samples": PROTOCOL_BOOTSTRAP_SAMPLES,
        "random_seed": PROTOCOL_RANDOM_SEED,
        "excess_benchmark": excess_benchmark,
        "sessions_per_year": SESSIONS_PER_YEAR,
    }


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
    if isinstance(body, dict) and body.get("schema") in RETIRED_LEDGER_SCHEMAS:
        raise ValueError(
            f"a {body['schema']} row predates the row kind; this ledger must be re-run into a "
            f"{LEDGER_SCHEMA} ledger (there is no migration)"
        )
    if not isinstance(body, dict) or body.get("schema") != LEDGER_SCHEMA:
        raise ValueError(f"expected an object whose schema is {LEDGER_SCHEMA!r}")
    stage, kind, identity = body["stage"], body["kind"], body["config_id"]
    config, result = body["config"], body["result"]
    if stage not in STAGES:
        raise ValueError(f"stage {stage!r} is not one of {STAGES}")
    if kind not in (MEASUREMENT, HOLDOUT_CLAIM) or (
        kind == HOLDOUT_CLAIM and stage != HOLDOUT_STAGE
    ):
        raise ValueError(f"kind {kind!r} is not a row kind of stage {stage!r}")
    if not isinstance(identity, str):
        raise ValueError("config_id must be text")
    if not (isinstance(config, dict) and isinstance(result, dict)):
        raise ValueError("config and result must be objects")
    recorded_at = datetime.fromisoformat(body["recorded_at"])
    if recorded_at.tzinfo is None:
        raise ValueError("recorded_at has no time zone")
    return LedgerRow(
        line=number,
        stage=stage,
        kind=kind,
        config_id=identity,
        config=config,
        result=result,
        recorded_at=recorded_at,
    )


def _checked_stage(stage: str) -> str:
    if stage not in STAGES:
        raise ResearchLedgerError(f"stage {stage!r} is not one of the protocol's stages {STAGES}")
    return stage


def _write(
    path: Path,
    existing: list[LedgerRow],
    stage: str,
    kind: RowKind,
    config: Mapping[str, object],
    result: Mapping[str, object],
    *,
    recorded_at: datetime,
) -> LedgerRow:
    """Append one row after checking it against `existing` (the ledger as held in memory, which is
    extended with the new row). Refuses a configuration its stage already holds as this kind."""
    if recorded_at.tzinfo is None:
        raise ResearchLedgerError("recorded_at must carry a time zone")
    identity = config_id(config)
    if any(
        row.stage == stage and row.kind == kind and row.config_id == identity for row in existing
    ):
        raise ResearchLedgerError(
            f"configuration {identity} is already a {kind} row of stage {stage!r} in {path}; a "
            "configuration is one hypothesis of its stage, and the row there is the one its "
            "family counts"
        )
    body = {
        "schema": LEDGER_SCHEMA,
        "stage": stage,
        "kind": kind,
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


def _append(
    path: Path,
    existing: list[LedgerRow],
    stage: str,
    config: Mapping[str, object],
    result: Mapping[str, object],
    *,
    recorded_at: datetime,
) -> LedgerRow:
    """A measurement row of a non-holdout stage."""
    if _checked_stage(stage) == HOLDOUT_STAGE:
        raise ResearchLedgerError(
            f"the {HOLDOUT_STAGE!r} stage is written only by registry.run_holdout, which claims "
            "the run under a committed registration before it measures"
        )
    return _write(path, existing, stage, MEASUREMENT, config, result, recorded_at=recorded_at)


def _append_holdout(
    path: Path,
    kind: RowKind,
    config: Mapping[str, object],
    result: Mapping[str, object],
    *,
    recorded_at: datetime,
) -> LedgerRow:
    """The holdout stage's only writer, for `registry.run_holdout` and nothing else.

    A `holdout_claim` is accepted only into a ledger with no holdout row at all. A `measurement` is
    accepted only after exactly that claim, for the claimed configuration and under the claimed
    registration. Both must carry `HOLDOUT_BINDING`.
    """
    missing = [key for key in HOLDOUT_BINDING if key not in result]
    if missing:
        raise ResearchLedgerError(f"a holdout row must carry its registration; {missing} absent")
    existing = list(read_ledger(path))
    holdout = [row for row in existing if row.stage == HOLDOUT_STAGE]
    if kind == HOLDOUT_CLAIM:
        if holdout:
            raise ResearchLedgerError(
                f"{path} already holds a holdout row (line {holdout[0].line}); "
                "the holdout runs once"
            )
    else:
        claims = [row for row in holdout if row.kind == HOLDOUT_CLAIM]
        if any(row.kind == MEASUREMENT for row in holdout):
            raise ResearchLedgerError(
                f"{path} already holds the holdout's measurement; it runs once"
            )
        if len(claims) != 1 or claims[0].config_id != config_id(config):
            raise ResearchLedgerError(
                "a holdout measurement is written only after the holdout_claim of the same "
                "configuration"
            )
        if any(claims[0].result.get(key) != result[key] for key in HOLDOUT_BINDING):
            raise ResearchLedgerError(
                "the holdout measurement does not carry the registration its claim was made under"
            )
    return _write(path, existing, HOLDOUT_STAGE, kind, config, result, recorded_at=recorded_at)


def append_ledger(
    path: Path,
    stage: str,
    config: Mapping[str, object],
    result: Mapping[str, object],
    *,
    recorded_at: datetime | None = None,
) -> None:
    """Append one configuration's row to `stage`. Refuses a stage outside `STAGES`, the holdout
    stage (only `registry.run_holdout` writes it), a configuration the stage already holds, and a
    value with no canonical JSON form. `recorded_at` defaults to now."""
    _append(
        path,
        list(read_ledger(path)),
        stage,
        config,
        result,
        recorded_at=_utc_now() if recorded_at is None else recorded_at,
    )


def stage_family(path: Path, stage: str) -> int:
    """The family size of `stage`: every measurement row of it, refused configurations included.

    A holdout claim is not a hypothesis and is not counted; its measurement is."""
    return sum(1 for row in read_ledger(path) if row.stage == stage and row.kind == MEASUREMENT)


def hypothesis_test_name(p_value_key: str) -> str:
    """The procedure a ledger p-value came from, as `HypothesisTest.test` records it."""
    return (
        f"{p_value_key}: two-sided sign-flip randomization test "
        "(openalpha_cn.backtest.outcome_statistics.sign_flip_test)"
    )


def fdr_table(
    path: Path, stage: str, q: float, *, p_value_key: str = DEFAULT_P_VALUE_KEY
) -> MultipleTestingReport:
    """Benjamini-Yekutieli false-discovery control over one family of one stage, at rate `q`.

    The family is `stage_family` -- every measurement row -- and the tests are the rows whose
    result carries `p_value_key`; the rest are reported as withheld. Each hypothesis is named by
    its configuration's identity. Refuses a stage with no row, and one where no row has the p-value.
    """
    family = stage_family(path, stage)
    if family == 0:
        raise ResearchLedgerError(f"stage {stage!r} has no row in {path}")
    tests: list[HypothesisTest] = []
    for row in read_ledger(path):
        if row.stage != stage or row.kind != MEASUREMENT or p_value_key not in row.result:
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


# --- stage windows -----------------------------------------------------------------------------


def _config_date(config: Mapping[str, object], key: str) -> date:
    value = config.get(key)
    if not isinstance(value, date) or isinstance(value, datetime):
        raise ResearchLedgerError(
            f"a configuration names its measured window as `start` and `end` dates; {key!r} is "
            f"{value!r}"
        )
    return value


def measured_window(
    config: Mapping[str, object], *, label_sessions: int, sessions: Sequence[date] = ()
) -> tuple[date, date]:
    """The first and last day a configuration's measurement reads outcomes from.

    `start` and `end` come from the configuration and nowhere else; the last day is `end` moved on
    by `label_sessions` trading sessions of `sessions`, the exchange calendar. A missing or
    misordered window, or a calendar that does not reach the label's end, is the caller's error.
    """
    first, last = _config_date(config, "start"), _config_date(config, "end")
    if last < first:
        raise ResearchLedgerError(f"a configuration's start {first} is after its end {last}")
    if label_sessions < 0:
        raise ResearchLedgerError(f"label_sessions must be at least 0, not {label_sessions}")
    if label_sessions == 0:
        return first, last
    calendar = tuple(sessions)
    at_or_before = [index for index, day in enumerate(calendar) if day <= last]
    if not at_or_before or at_or_before[-1] + label_sessions >= len(calendar):
        raise ResearchLedgerError(
            f"the calendar handed to the runner does not reach {label_sessions} session(s) past "
            f"{last}; the label's last day cannot be placed"
        )
    return first, calendar[at_or_before[-1] + label_sessions]


def stage_window(stage: str, *, forward_after: date | None = None) -> StageWindow:
    """The window a stage may measure; the forward stage's starts the day after `forward_after`."""
    if _checked_stage(stage) == FORWARD_STAGE:
        if forward_after is None:
            raise ResearchLedgerError(
                "the forward stage starts after the registration's commit date; pass it as "
                "forward_after (registry.forward_after)"
            )
        return StageWindow(forward_after + timedelta(days=1), None)
    return PROTOCOL_STAGE_WINDOWS[stage]


def check_window(stage: str, first: date, last: date, *, forward_after: date | None = None) -> None:
    """Raise `StageWindowError` unless `first..last` lies inside `stage`'s window."""
    window = stage_window(stage, forward_after=forward_after)
    if first < window.first or (window.last is not None and last > window.last):
        bound = "onwards" if window.last is None else f"to {window.last}"
        raise StageWindowError(
            f"the measured window {first}..{last} leaves the {stage} stage's window "
            f"{window.first} {bound}; it was not measured"
        )


def strategy_label_sessions(config: Mapping[str, object]) -> int:
    """A strategy backtest reads no outcome after `end`: its last period is marked at `end`."""
    return 0


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


def one_sided_p_value(two_sided_p: float, observed_mean: float) -> float:
    """The protocol's one-sided p for "mean > 0" from a symmetric two-sided sign-flip p-value:
    half of it when the observed mean is positive, one less half of it otherwise."""
    return two_sided_p / 2 if observed_mean > 0 else 1 - two_sided_p / 2


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
    period. Every series is stored; the test and the means read only complete periods (as many
    sessions as the rebalance interval), and `excluded_incomplete_periods` says how many were left
    out -- the trailing period a window ends inside is shorter and is not a like-for-like draw.
    """
    periods = backtest.periods
    interval = backtest.spec.rebalance_every_sessions
    complete = [p.sessions >= interval for p in periods]
    if not any(complete):
        raise StrategyBacktestError(
            f"the backtest has no complete {interval}-session period; there is nothing to test"
        )
    missing = [p.start for p in periods if excess_benchmark not in p.benchmark_returns]
    if missing:
        raise StrategyBacktestError(
            f"benchmark {excess_benchmark!r} has no return for the period starting {missing[0]}"
        )
    excess = tuple(p.net_return - p.benchmark_returns[excess_benchmark] for p in periods)
    tested = [
        (float(value), p) for value, p, full in zip(excess, periods, complete, strict=True) if full
    ]
    values = tuple(value for value, _ in tested)
    test = sign_flip_test(values, bootstrap_samples=bootstrap_samples, random_seed=random_seed)
    per_year = SESSIONS_PER_YEAR / statistics.fmean(p.sessions for _, p in tested)
    mean = statistics.fmean(values)
    stdev = statistics.stdev(values) if len(values) > 1 else None
    information_ratio = None if not stdev else mean / stdev * math.sqrt(per_year)
    return {
        "excess_benchmark": excess_benchmark,
        "period_count": len(periods),
        "period_starts": [p.start for p in periods],
        "period_ends": [p.end for p in periods],
        "period_sessions": [p.sessions for p in periods],
        "period_complete": complete,
        "excluded_incomplete_periods": complete.count(False),
        "net_return": [str(p.net_return) for p in periods],
        "benchmark_return": [str(p.benchmark_returns[excess_benchmark]) for p in periods],
        "net_excess": [str(value) for value in excess],
        "turnover": [str(p.turnover) for p in periods],
        "p_excess": test.p_value,
        "p_excess_one_sided": one_sided_p_value(test.p_value, mean),
        "p_excess_exact": test.exact,
        "p_excess_sign_patterns": test.sign_patterns,
        "p_excess_random_seed": test.random_seed,
        "mean_net_excess": mean,
        "stdev_net_excess": stdev,
        "information_ratio": information_ratio,
        "annualized_mean_net_excess": mean * per_year,
        "mean_turnover": statistics.fmean(float(p.turnover) for _, p in tested),
    }


@dataclass(frozen=True, slots=True)
class StrategyMeasure:
    """A `SettledMeasure` that calls `backtest(**config)` -- `OpenAlphaSDK.run_strategy_backtest`,
    say -- with the configuration unchanged, and reduces the answer with `strategy_result`."""

    backtest: Callable[..., StrategyBacktest]
    excess_benchmark: str
    bootstrap_samples: int = PROTOCOL_BOOTSTRAP_SAMPLES
    random_seed: int = PROTOCOL_RANDOM_SEED

    @property
    def settings(self) -> Mapping[str, object]:
        return {
            "bootstrap_samples": self.bootstrap_samples,
            "random_seed": self.random_seed,
            "excess_benchmark": self.excess_benchmark,
            "sessions_per_year": SESSIONS_PER_YEAR,
        }

    def __call__(self, config: Mapping[str, object]) -> Mapping[str, object]:
        return strategy_result(
            self.backtest(**config),
            excess_benchmark=self.excess_benchmark,
            bootstrap_samples=self.bootstrap_samples,
            random_seed=self.random_seed,
        )


def strategy_measure(
    backtest: Callable[..., StrategyBacktest],
    *,
    excess_benchmark: str,
    bootstrap_samples: int = PROTOCOL_BOOTSTRAP_SAMPLES,
    random_seed: int = PROTOCOL_RANDOM_SEED,
) -> StrategyMeasure:
    """A `StrategyMeasure` over `backtest` under the given settings."""
    return StrategyMeasure(
        backtest=backtest,
        excess_benchmark=excess_benchmark,
        bootstrap_samples=bootstrap_samples,
        random_seed=random_seed,
    )


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
    label_sessions: Callable[[Mapping[str, object]], int],
    sessions: Sequence[date] = (),
    refusals: tuple[type[Exception], ...] = DEFAULT_REFUSALS,
    clock: Callable[[], datetime] | None = None,
) -> GridRun:
    """Measure every configuration the ledger does not already hold for `stage`, one row each.

    Before measuring, each configuration's measured window (`measured_window`, with
    `label_sessions(config)` sessions of `sessions` past `end`) must lie inside the stage's window;
    one that does not is a refused row and is never measured. A configuration `measure` refuses
    (one of `refusals`) is a row whose result is its error. Both count in the family.
    `label_sessions` has no default because a label's length is the one thing about a window a
    configuration's dates do not say; `strategy_label_sessions` is the strategy backtest's.

    Two stages are refused here. The holdout runs through `registry.run_holdout`. The forward
    stage runs through `registry.run_forward`, which derives its boundary from the committed
    registration: a boundary a caller could type is a way back into the holdout window.
    """
    stage = _checked_stage(stage)
    if stage == HOLDOUT_STAGE:
        raise ResearchLedgerError(
            f"the {HOLDOUT_STAGE!r} stage runs once, through registry.run_holdout behind its "
            "registration guard, and never from a grid"
        )
    if stage == FORWARD_STAGE:
        raise ResearchLedgerError(
            f"the {FORWARD_STAGE!r} stage runs through registry.run_forward, which takes its "
            "boundary from the committed registration rather than from a caller"
        )
    return _run_grid(
        ledger,
        stage,
        configs,
        measure,
        label_sessions=label_sessions,
        sessions=sessions,
        forward_after=None,
        refusals=refusals,
        clock=clock,
    )


def _run_grid(
    ledger: Path,
    stage: str,
    configs: Sequence[Mapping[str, object]],
    measure: Measure,
    *,
    label_sessions: Callable[[Mapping[str, object]], int],
    sessions: Sequence[date],
    forward_after: date | None,
    refusals: tuple[type[Exception], ...],
    clock: Callable[[], datetime] | None,
) -> GridRun:
    """`run_grid`'s body, for it and for `registry.run_forward` (which alone passes
    `forward_after`, derived from a committed registration)."""
    if _checked_stage(stage) == HOLDOUT_STAGE:
        raise ResearchLedgerError("the holdout stage runs through registry.run_holdout only")
    stage_window(stage, forward_after=forward_after)
    now = _utc_now if clock is None else clock
    existing = list(read_ledger(ledger))
    ran = skipped = 0
    for config in configs:
        identity = config_id(config)
        if any(
            row.stage == stage and row.kind == MEASUREMENT and row.config_id == identity
            for row in existing
        ):
            skipped += 1
            continue
        first, last = measured_window(
            config, label_sessions=label_sessions(config), sessions=sessions
        )
        recorded: tuple[type[Exception], ...] = (StageWindowError, *refusals)
        try:
            check_window(stage, first, last, forward_after=forward_after)
            result = dict(measure(config))
        except recorded as error:
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
        command.add_argument("--stage", required=True, choices=STAGES)
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
