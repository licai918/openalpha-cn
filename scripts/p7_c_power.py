"""Plan C's event-level power calculation (`V2-P7-013`, plan C Task 3.1; ruling D1, fix round 1).

The question this tool answers is **can C's registered test be measured and powered**, not whether
an effect exists. C's one primary statistic is the LLM family's h=1 partial rank IC on the primary
scoring set, measured over the 120 forward sessions that follow C's registration. The agents are
only routed to securities that carry evidence, and v1's only evidence producer is 011's limit
events, so the measured cross section of a session is not the sample's size but the number of
**scored names**: the sampled survivors that carry at least one limit event, less the names a model
abstains on. That is why the calculation is event-level, and why it reports two scoring domains
side by side -- the primary random sample of `PRIMARY_SAMPLE_SIZE` (300) survivors and the full
survivor census -- with a `testable` / `not_testable` verdict and the reason on each (D1).

## What it reads

A **stored panel** and Step 0's **committed** registration, both read-only. Per stored session of
the panel's `trade_cal` between `--first` and `--last` it rebuilds the session the way the daily
survivor day rebuilds it -- `strategy_survivor_facts.build_facts_day` at the session's own 16:30
publication instant, then `domain.survivor_screen.screen` -- and counts the survivors that
`panel_limit_events.limit_events_on` reports an event for at that same instant. Those are
**point-in-time replays of the evening the day was filed**: a session's survivors and events are
what that evening's record would have shown, not the restated present. The calendar that names the
sessions is read at the last session's publication instant, so every session it lists is already
published when it is asked.

**No label and no return is read.** This is a count of survivors and events plus arithmetic on the
count; the module imports nothing that can produce an outcome (`model_view`,
`strategy_survivor_forward`), and no client exists here at all -- no Tushare, no DeepSeek, no
network. The report is planning information, never evidence.

## The projection (D1, F9)

For one row (`sample` = the sample's size, `None` = the census) and one observed session with `S`
survivors and `E` survivors carrying an event, the **expected post-abstention scored names** are

    round(E * min(sample, S) / S * (1 - abstention_rate))      (census: E * (1 - abstention_rate))

where `abstention_rate` (`PLANNING_ABSTENTION_RATE`, 0.10) is the planning share of scored names a
model abstains on -- an assumption, not a measurement; D7's dry week supplies the observed rate and
L5 may override it on the command line. A session is **measurable** iff that count is at least
`MIN_SCORED_NAMES` (10); the measurable fraction of the observed sessions is projected onto the
forward window (`round(fraction * WINDOW_SESSIONS)`), and the periods are that projection divided by
the horizon (`horizon=1` gives one period per measurable session; `h=5` gives every fifth).

**Two simplifications are stated, not hidden** (`EXPECTED_COUNT_NOTE`). The projection uses the
**expected** count, not the per-session count: for the primary sample the per-session count is
hypergeometric, so a session whose expected count is just above the floor is below it about half the
time. And the abstention rate is a parameter applied to the historical event count rather than an
observed score count. Both make the projection a plan, not a forecast.

## The session sd and the verdict (F4)

A session's IC standard deviation has a non-sampling part and a sampling part. `sd_between`
(`PLANNING_SD_BETWEEN`, 0.15) is the **non-sampling part** -- day-to-day variation in the true IC --
and is a planning assumption, not a measurement. With `n` post-abstention scored names on a session,

    session_sd = min(1.0, sqrt(sd_between**2 + 1 / (n - 1)))     n >= 2
    session_sd = 1.0                                             n == 1

and the row reports the **maximum** over its measurable sessions, because the narrowest measured
cross section is the noisiest one. The cap and the row maximum keep the printed sd at or below 1,
the largest value a rank IC can carry; `http`-style impossible sds (the one-component inflation
this replaces printed 1.5 for 10 scored names) cannot come out of it.

The detectable mean is `incremental_ic.detectable_mean(sd=row_sd, periods=periods,
level=0.05, power=0.80)`, capped at 1.0 so an impossible number is never printed. The verdict is
`testable` iff **all** of: at least `MIN_OBSERVED_SESSIONS` (20) historical sessions were observed
(nothing is projected from a handful of sessions), the projected measurable sessions reach
`MIN_MEASURABLE_SESSIONS` (60), and the detectable mean is at most `DETECTABLE_MEAN_THRESHOLD`
(0.02). Otherwise the row is `not_testable` with `reason` one of `too_few_observed_sessions`,
`below_measurable_floor`, `detectable_above_threshold` (in that precedence).

**What the 0.02 threshold is.** It is the previous G-C h1 pass bar ("h1 >= 0.02 and t >= 2, or
h5 >= 0.06"), used here only as a **planning detectability threshold**: it says the design could
detect an increment no larger than 0.02 at the target power. It is **not** a gate on the registered
test's magnitude -- D5's primary test is a one-sided sign-flip at alpha=0.05 on the mean, and the
magnitude is reported, never gated. Its provenance is in `THRESHOLD_NOTE`.

**The default assumptions make both rows not_testable, and the row says why.** With
`sd_between` 0.15 and at most 120 periods the detectable mean is at least
0.227 * 0.15 = 0.034, above 0.02, so a default run reports `detectable_above_threshold` whenever the
observed and measurable floors pass. That is the honest design conclusion, not a bug: the old 0.02
bar is not detectable at 120 sessions under that planning sd. L5 can lower `--sd-between` from a
measured dry-week value (for census-sized cross sections the two-component sd crosses 0.02 at about
0.071; for the primary sample's small cross sections it cannot reach 0.02 at any `sd_between`,
because the sampling part alone is already about 0.137 for ~54 scored names), and the verdict then
follows the threshold. The D1 branch is still the user's at L5.

## The command

    python scripts/p7_c_power.py --panel-dir DIR \
        --step0-registration docs/research/p7-registration.json \
        --first YYYY-MM-DD --last YYYY-MM-DD \
        [--sd-between 0.15] [--abstention-rate 0.10] [--json PATH]

It prints the two rows; only `--json PATH` writes anything, and then exactly one report
(`POWER_REPORT_SCHEMA` = `openalpha-p7-c-power/v1`, atomic temporary file plus `os.replace`),
which is the file the registration writer's `--power-report` reads. Every refusal -- an unreadable
registration, a missing panel directory, an empty session range, a design the arithmetic cannot
answer -- exits 2 with the reason on stderr and nothing written.

**One signature deviation from the plan's sketch.** The plan writes
`historical_event_counts(store, *, first, last, book)`; the same sentence requires the
registration's `transform`, `min_coverage`, `max_carry_sessions` and `exchange` for the facts day,
which are the adapter's and not the book's. They are passed as a required `adapter` argument rather
than defaulted, because a defaulted adapter would silently rebuild the day with settings the
registration does not have.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import math
import os
import sys
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Final, Literal

_SCRIPTS: Final[Path] = Path(__file__).resolve().parent
for _root in (_SCRIPTS, _SCRIPTS / "research"):
    if str(_root) not in sys.path:
        sys.path.insert(0, str(_root))

import registry  # noqa: E402

from openalpha_cn.domain.factor_transform import FactorTransformError  # noqa: E402
from openalpha_cn.domain.incremental_ic import detectable_mean  # noqa: E402
from openalpha_cn.domain.survivor_book import SurvivorBookConfig  # noqa: E402
from openalpha_cn.domain.survivor_screen import screen  # noqa: E402
from openalpha_cn.domain.trading_calendar import TradingCalendarError  # noqa: E402
from openalpha_cn.panel.store import PanelStorageError, PanelStore  # noqa: E402
from openalpha_cn.panel_factors import FACTOR_TRANSFORMS  # noqa: E402
from openalpha_cn.panel_ingest import (  # noqa: E402
    load_trading_calendar,
    session_publication_instant,
)
from openalpha_cn.panel_limit_events import LimitEventReadError, limit_events_on  # noqa: E402
from openalpha_cn.strategy_survivor import (  # noqa: E402
    SurvivorAdapterConfig,
    SurvivorRegistration,
    SurvivorRegistrationError,
    read_survivor_registration,
)
from openalpha_cn.strategy_survivor_facts import (  # noqa: E402
    SurvivorFactsError,
    build_facts_day,
)

POWER_REPORT_SCHEMA: Final[str] = "openalpha-p7-c-power/v1"
"""The `--json` report's schema: one planning table, read by C's registration writer."""

PRIMARY_SAMPLE_SIZE: Final[int] = 300
"""D1's primary scoring domain: a random 300 of the survivors (the registered `primary_size`)."""

WINDOW_SESSIONS: Final[int] = 120
"""D5's forwarding window: 120 sessions after C's registration commit."""

PRIMARY_HORIZON: Final[int] = 1
"""D5's one primary test horizon: the agent record is 5d, the test reads h=1."""

MIN_SCORED_NAMES: Final[int] = 10
"""D3: a session is measurable only with at least this many scored primary names."""

MIN_MEASURABLE_SESSIONS: Final[int] = 60
"""D3: fewer measurable positions than this over the window is `not_measurable`."""

MIN_OBSERVED_SESSIONS: Final[int] = 20
"""F4: no projection is made from fewer observed historical sessions than this."""

PLANNING_SD_BETWEEN: Final[float] = 0.15
"""The NON-SAMPLING part of a session's IC sd: a planning assumption, not a measured value."""

PLANNING_ABSTENTION_RATE: Final[float] = 0.10
"""F9: the planning share of scored names a model abstains on; an assumption, not a measurement."""

DETECTABLE_MEAN_THRESHOLD: Final[float] = 0.02
"""The previous G-C h1 pass bar, used as a PLANNING detectability threshold, not a magnitude
gate."""

TARGET_POWER: Final[float] = 0.8
"""The design power the detectable mean is computed for."""

LEVEL: Final[float] = 0.05
"""D5's one-sided test level."""

TestVerdict = Literal["testable", "not_testable"]
VERDICT_TESTABLE: Final[TestVerdict] = "testable"
VERDICT_NOT_TESTABLE: Final[TestVerdict] = "not_testable"
"""The measurability verdict's two values, as machine text and as the printed wording."""

TestReason = Literal[
    "too_few_observed_sessions", "below_measurable_floor", "detectable_above_threshold"
]
REASON_TOO_FEW_OBSERVED_SESSIONS: Final[TestReason] = "too_few_observed_sessions"
REASON_BELOW_MEASURABLE_FLOOR: Final[TestReason] = "below_measurable_floor"
REASON_DETECTABLE_ABOVE_THRESHOLD: Final[TestReason] = "detectable_above_threshold"
"""Why a row is `not_testable`, in precedence order: too few observations, floor, threshold."""

CANDIDATE_WORDING: Final[str] = "candidate research protocol planning (候选), not evidence"
NO_LABELS_WORDING: Final[str] = (
    "no labels and no returns were read: this counts stored survivors and their limit events only"
)
SD_BETWEEN_NOTE: Final[str] = (
    "the non-sampling part of a session IC sd; planning assumption, not measured"
)
ABSTENTION_NOTE: Final[str] = (
    "the planning share of scored names a model abstains on; planning assumption, not a "
    "measurement (D7's dry week supplies the observed rate and L5 may override it)"
)
THRESHOLD_NOTE: Final[str] = (
    "the 0.02 threshold is the previous G-C h1 pass bar ('h1 >= 0.02 and t >= 2' or "
    "'h5 >= 0.06'), used here only as a planning detectability threshold; it is not a gate on "
    "the registered test's magnitude"
)
EXPECTED_COUNT_NOTE: Final[str] = (
    "the projection uses the expected count E[event_names] * min(sample, survivors) / survivors, "
    "not the hypergeometric per-session count, so a session whose expected count is just above the "
    "floor is below it about half the time; observed model abstention reduces the scored names by "
    "abstention_rate -- both are stated simplifications, not measurements"
)
VERDICT_NOTE: Final[str] = (
    "the verdict is testable iff at least min_observed_sessions were observed, projected "
    "measurable sessions reach the floor, and the detectable mean is at most the threshold; "
    "otherwise it is not_testable with its reason. The detectable mean is design information, "
    "never an effect-size gate -- no threshold was ruled for the registered test's magnitude"
)

REPOSITORY: Final[Path] = _SCRIPTS.parent
STEP0_REGISTRATION_IN_REPOSITORY: Final[str] = "docs/research/p7-registration.json"


class PowerRequestError(ValueError):
    """A request or an input the calculation refuses by name, with nothing written."""


@dataclass(frozen=True, slots=True)
class SessionCount:
    """One stored session's census: its survivors and how many of them carry a limit event."""

    session: date
    survivors: int
    event_names: int

    def __post_init__(self) -> None:
        if self.survivors < 0:
            raise PowerRequestError(
                f"{self.session.isoformat()} has {self.survivors} survivors; a count cannot be "
                "negative"
            )
        if not 0 <= self.event_names <= self.survivors:
            raise PowerRequestError(
                f"{self.session.isoformat()} has {self.event_names} names with an event among "
                f"{self.survivors} survivors; an event names a security, so the count cannot "
                "exceed the survivors"
            )


@dataclass(frozen=True, slots=True)
class PowerRow:
    """One scoring domain's projection, and the measurability verdict it supports."""

    sample: str
    """`n300` for the primary sample, `census` for the whole survivor set."""
    sample_size: int | None
    """The sample's size, or `None` for the census."""
    projected_scored_names: int | None
    """The expected post-abstention scored names per measured session; `None` when not projected."""
    observed_sessions: int
    """The stored sessions the row was computed over."""
    observed_measurable_sessions: int
    """How many of those observed sessions are measurable."""
    measurable_sessions: int
    """The projected measurable sessions of the forward window; 0 when no projection is made."""
    periods: int
    """The test's periods: the projection itself at h=1, divided by the horizon above it."""
    sd: float | None
    """The row's session sd: the maximum over its measurable sessions, at most 1."""
    detectable_mean: float | None
    """The mean detectable at the target power, capped at 1; `None` when not computed."""
    detectable_threshold: float
    """The planning detectability threshold the verdict compares against (0.02)."""
    verdict: TestVerdict
    """`testable` only when every floor and the threshold pass."""
    reason: TestReason | None
    """Why the row is `not_testable`; `None` exactly when it is testable."""


def sample_label(sample: int | None) -> str:
    """The row's name: `n{sample}` for a sample, `census` for the whole survivor set."""
    return "census" if sample is None else f"n{sample}"


def _scored_names(count: SessionCount, sample: int | None, abstention_rate: float) -> int:
    """The expected post-abstention scored names of one session under `sample` (F9)."""
    if count.survivors <= 0:
        return 0
    if sample is None or sample >= count.survivors:
        drawn = float(count.event_names)
    else:
        drawn = count.event_names * sample / count.survivors
    return round(drawn * (1 - abstention_rate))


def _session_sd(scored_names: int, sd_between: float) -> float:
    """One session's IC sd: the non-sampling sd plus the 1/(n-1) sampling part, capped at 1 (F4)."""
    if scored_names <= 1:
        return 1.0
    return min(1.0, math.sqrt(sd_between**2 + 1.0 / (scored_names - 1)))


def _refusal_reason(
    *, observed_sessions: int, projected: int, detectable: float | None
) -> TestReason | None:
    """The first failing floor's reason, or `None` when the row is testable (F4)."""
    if observed_sessions < MIN_OBSERVED_SESSIONS:
        return REASON_TOO_FEW_OBSERVED_SESSIONS
    if projected < MIN_MEASURABLE_SESSIONS or detectable is None:
        return REASON_BELOW_MEASURABLE_FLOOR
    if detectable > DETECTABLE_MEAN_THRESHOLD:
        return REASON_DETECTABLE_ABOVE_THRESHOLD
    return None


def _check_design(
    *,
    sessions: int,
    horizon: int,
    sd_between: float,
    abstention_rate: float,
    level: float,
    power: float,
    sample: int | None,
) -> None:
    """The design's inputs, refused by name rather than answered with `nan`."""
    if not isinstance(sessions, int) or isinstance(sessions, bool) or sessions < 1:
        raise PowerRequestError(
            f"the forward window must be a positive whole number of sessions, got {sessions!r}"
        )
    if not isinstance(horizon, int) or isinstance(horizon, bool) or horizon < 1:
        raise PowerRequestError(f"the horizon must be a positive whole number, got {horizon!r}")
    if sample is not None and (
        not isinstance(sample, int) or isinstance(sample, bool) or sample < 1
    ):
        raise PowerRequestError(f"a sample size must be a positive whole number, got {sample!r}")
    if not math.isfinite(sd_between) or not 0 < sd_between <= 1:
        raise PowerRequestError(
            "the non-sampling part of a session IC sd must lie strictly between 0 and 1, got "
            f"{sd_between!r}"
        )
    if not math.isfinite(abstention_rate) or not 0 <= abstention_rate < 1:
        raise PowerRequestError(
            f"the planning abstention rate must lie in [0, 1), got {abstention_rate!r}"
        )
    if not math.isfinite(level) or not 0 < level < 0.5:
        raise PowerRequestError(
            f"the one-sided test level must lie strictly between 0 and 0.5, got {level!r}"
        )
    if not math.isfinite(power) or not 0 < power < 1:
        raise PowerRequestError(f"the power must lie strictly between 0 and 1, got {power!r}")


def event_power(
    counts: Sequence[SessionCount],
    *,
    sample: int | None,
    sessions: int = WINDOW_SESSIONS,
    horizon: int = PRIMARY_HORIZON,
    sd_between: float = PLANNING_SD_BETWEEN,
    abstention_rate: float = PLANNING_ABSTENTION_RATE,
    level: float = LEVEL,
    power: float = TARGET_POWER,
) -> PowerRow:
    """One scoring domain's projection over the observed sessions; see the module docstring.

    `sample` is the sample's size, or `None` for the census. Nothing is projected from fewer than
    `MIN_OBSERVED_SESSIONS` observed sessions: the row then keeps its observed counts and reports
    reason `too_few_observed_sessions`. The measurable fraction is taken over the observed sessions
    and projected onto the forward `sessions`; the sd and the scored-name count are over the
    **measurable** sessions, because those are the sessions the registered test would measure.
    """
    observed = tuple(counts)
    _check_design(
        sessions=sessions,
        horizon=horizon,
        sd_between=sd_between,
        abstention_rate=abstention_rate,
        level=level,
        power=power,
        sample=sample,
    )
    if not observed:
        raise PowerRequestError("there is no observed session to project from")
    scored = tuple(_scored_names(count, sample, abstention_rate) for count in observed)
    measurable = tuple(index for index, names in enumerate(scored) if names >= MIN_SCORED_NAMES)
    projected = 0
    periods = 0
    projected_scored: int | None = None
    sd: float | None = None
    detectable: float | None = None
    if len(observed) >= MIN_OBSERVED_SESSIONS and measurable:
        projected = round(len(measurable) / len(observed) * sessions)
        periods = projected if horizon <= 1 else projected // horizon
        projected_scored = round(math.fsum(scored[index] for index in measurable) / len(measurable))
        sd = max(_session_sd(scored[index], sd_between) for index in measurable)
        if periods >= 2:
            detectable = min(detectable_mean(sd=sd, periods=periods, level=level, power=power), 1.0)
    reason = _refusal_reason(
        observed_sessions=len(observed), projected=projected, detectable=detectable
    )
    verdict: TestVerdict = VERDICT_TESTABLE if reason is None else VERDICT_NOT_TESTABLE
    return PowerRow(
        sample=sample_label(sample),
        sample_size=sample,
        projected_scored_names=projected_scored,
        observed_sessions=len(observed),
        observed_measurable_sessions=len(measurable),
        measurable_sessions=projected,
        periods=periods,
        sd=sd,
        detectable_mean=detectable,
        detectable_threshold=DETECTABLE_MEAN_THRESHOLD,
        verdict=verdict,
        reason=reason,
    )


def _row_form(row: PowerRow) -> dict[str, Any]:
    """One row as JSON: the fields the report and the registration writer read."""
    return {
        "sample": row.sample,
        "sample_size": row.sample_size,
        "projected_scored_names": row.projected_scored_names,
        "observed_sessions": row.observed_sessions,
        "observed_measurable_sessions": row.observed_measurable_sessions,
        "measurable_sessions": row.measurable_sessions,
        "periods": row.periods,
        "sd": row.sd,
        "detectable_mean": row.detectable_mean,
        "detectable_threshold": row.detectable_threshold,
        "verdict": row.verdict,
        "reason": row.reason,
    }


def _planning_form(
    *,
    sessions: int,
    horizon: int,
    sd_between: float,
    abstention_rate: float,
    level: float,
    power: float,
) -> dict[str, Any]:
    return {
        "window_sessions": sessions,
        "primary_horizon": horizon,
        "sd_between": sd_between,
        "sd_between_note": SD_BETWEEN_NOTE,
        "abstention_rate": abstention_rate,
        "abstention_note": ABSTENTION_NOTE,
        "min_observed_sessions": MIN_OBSERVED_SESSIONS,
        "detectable_mean_threshold": DETECTABLE_MEAN_THRESHOLD,
        "threshold_note": THRESHOLD_NOTE,
        "expected_count_note": EXPECTED_COUNT_NOTE,
        "test_level": level,
        "target_power": power,
        "primary_sample_size": PRIMARY_SAMPLE_SIZE,
        "min_scored_names": MIN_SCORED_NAMES,
        "min_measurable_sessions": MIN_MEASURABLE_SESSIONS,
        "verdict_note": VERDICT_NOTE,
    }


def power_report(
    counts: Sequence[SessionCount],
    *,
    first: date,
    last: date,
    sessions: int = WINDOW_SESSIONS,
    horizon: int = PRIMARY_HORIZON,
    sd_between: float = PLANNING_SD_BETWEEN,
    abstention_rate: float = PLANNING_ABSTENTION_RATE,
    level: float = LEVEL,
    power: float = TARGET_POWER,
    source: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """The two-row planning report as JSON-ready data (`POWER_REPORT_SCHEMA`).

    Rows are keyed by their sample label -- `n{sample_size}` for the primary sample and
    `census` for the whole survivor set -- and `source` records what produced the counts (Step
    0's registration identity, when the command has it).
    """
    if last < first:
        raise PowerRequestError(
            f"the range ends {last.isoformat()} before it begins {first.isoformat()}"
        )
    observed = tuple(counts)
    rows = {
        row.sample: _row_form(row)
        for row in (
            event_power(
                observed,
                sample=PRIMARY_SAMPLE_SIZE,
                sessions=sessions,
                horizon=horizon,
                sd_between=sd_between,
                abstention_rate=abstention_rate,
                level=level,
                power=power,
            ),
            event_power(
                observed,
                sample=None,
                sessions=sessions,
                horizon=horizon,
                sd_between=sd_between,
                abstention_rate=abstention_rate,
                level=level,
                power=power,
            ),
        )
    }
    return {
        "schema": POWER_REPORT_SCHEMA,
        "wording": [CANDIDATE_WORDING, NO_LABELS_WORDING, VERDICT_NOTE],
        "first": first.isoformat(),
        "last": last.isoformat(),
        "sessions_observed": len(observed),
        "planning": _planning_form(
            sessions=sessions,
            horizon=horizon,
            sd_between=sd_between,
            abstention_rate=abstention_rate,
            level=level,
            power=power,
        ),
        "source": None if source is None else dict(source),
        "rows": rows,
    }


def _number(value: Any, *, digits: int = 4) -> str:
    return "absent" if value is None else f"{value:.{digits}f}"


def render_report(report: Mapping[str, Any]) -> str:
    """The report as the printed lines: both rows, their verdicts and reasons, and the notes."""
    planning = report["planning"]
    lines = [
        f"p7_c_power: {CANDIDATE_WORDING}",
        f"  {NO_LABELS_WORDING}",
        (
            f"  panel sessions {report['first']}..{report['last']}: "
            f"{report['sessions_observed']} stored session(s)"
        ),
        (
            f"  planning: forward window {planning['window_sessions']} session(s), primary horizon "
            f"{planning['primary_horizon']}, sd_between {planning['sd_between']:g}, abstention "
            f"rate {planning['abstention_rate']:g}, one-sided test level "
            f"{planning['test_level']:g}, target power {planning['target_power']:g}"
        ),
    ]
    source = report.get("source")
    if source:
        lines.append(
            "  Step 0 registration: " + ", ".join(f"{key} {source[key]}" for key in source)
        )
    lines.append(
        "  notes: "
        + " | ".join((SD_BETWEEN_NOTE, ABSTENTION_NOTE, EXPECTED_COUNT_NOTE, THRESHOLD_NOTE))
    )
    for row in report["rows"].values():
        sample_size = row["sample_size"]
        heading = (
            f"{row['sample']} (primary set N={sample_size})"
            if sample_size is not None
            else f"{row['sample']} (all survivors)"
        )
        if row["reason"] == REASON_TOO_FEW_OBSERVED_SESSIONS:
            projection = (
                f"no projection made (fewer than {MIN_OBSERVED_SESSIONS} observed sessions): "
                f"the observed {row['observed_measurable_sessions']} measurable session(s) are "
                "descriptive only"
            )
        else:
            projection = (
                f"projected scored names {_number(row['projected_scored_names'], digits=0)} per "
                f"measured session; projected measurable {row['measurable_sessions']} of "
                f"{planning['window_sessions']}; periods {row['periods']}"
            )
        reason = "" if row["reason"] is None else f" ({row['reason'].replace('_', ' ')})"
        lines.append(
            f"  {heading}: observed {row['observed_sessions']} session(s), measurable "
            f"{row['observed_measurable_sessions']} of them; {projection}; sd "
            f"{_number(row['sd'])}; detectable mean IC {_number(row['detectable_mean'])}; "
            f"threshold {row['detectable_threshold']:.4f}; "
            f"verdict {row['verdict'].replace('_', ' ')}{reason}"
        )
    lines.append(f"  {VERDICT_NOTE}")
    return "\n".join(lines)


def write_power_report(path: Path, report: Mapping[str, Any]) -> Path:
    """Write the report to `path` atomically (temporary file beside it, then `os.replace`)."""
    content = (
        json.dumps(dict(report), sort_keys=True, indent=2, ensure_ascii=False) + "\n"
    ).encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    replaced = False
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        replaced = True
    finally:
        if not replaced:
            with contextlib.suppress(OSError):
                os.unlink(temporary)
    return path


def trading_sessions_in(
    store: PanelStore, *, first: date, last: date, exchange: str
) -> tuple[date, ...]:
    """The stored `trade_cal` sessions between `first` and `last`, inclusive, ascending.

    The calendar is read at the last session's publication instant: every year the range spans
    was published by then, and the read names no instant the panel does not hold.
    """
    if last < first:
        raise PowerRequestError(
            f"the range ends {last.isoformat()} before it begins {first.isoformat()}"
        )
    calendar = load_trading_calendar(
        store,
        exchange=exchange,
        years=tuple(range(first.year, last.year + 1)),
        as_of=session_publication_instant(last),
    )
    return tuple(day for day in calendar.trading_days if first <= day <= last)


def historical_event_counts(
    store: PanelStore,
    *,
    first: date,
    last: date,
    book: SurvivorBookConfig,
    adapter: SurvivorAdapterConfig,
) -> tuple[SessionCount, ...]:
    """Each stored session's survivors and how many of them carry a limit event.

    The day is rebuilt the way the daily survivor day rebuilds it (`build_facts_day` at the
    session's own 16:30, then `screen`), and the events are read at the same instant; a name
    counts only when the event's security is one of that session's survivors. The adapter's
    `transform`, `min_coverage`, `max_carry_sessions` and `exchange` are the registration's,
    which is why they are passed beside the book (see the module docstring's deviation).
    """
    sessions = trading_sessions_in(store, first=first, last=last, exchange=adapter.exchange)
    if not sessions:
        raise PowerRequestError(
            f"the stored {adapter.exchange} calendar holds no session between "
            f"{first.isoformat()} and {last.isoformat()}; there is nothing to project from"
        )
    transform = FACTOR_TRANSFORMS.get(adapter.transform)
    counts: list[SessionCount] = []
    for session in sessions:
        published = session_publication_instant(session)
        day = build_facts_day(
            store=store,
            session=session,
            as_of=published,
            config=book,
            factor_ids=book.composite_factors,
            transform=transform,
            exchange=adapter.exchange,
            min_coverage=adapter.min_coverage,
            max_carry_sessions=adapter.max_carry_sessions,
            held=(),
        )
        survivor_codes = {name.ts_code for name in screen(day.facts, book).survivors}
        events = limit_events_on(store, session=session, as_of=published, exchange=adapter.exchange)
        event_names = len({event.ts_code for event in events} & survivor_codes)
        counts.append(
            SessionCount(session=session, survivors=len(survivor_codes), event_names=event_names)
        )
    return tuple(counts)


@dataclass(frozen=True, slots=True)
class StepZeroRegistration:
    """Step 0's committed registration as the command read it, with the identity it binds."""

    path: Path
    config_id: str
    registration_sha256: str
    code_commit: str
    survivor: SurvivorRegistration


def _seed_of(settings: object) -> int:
    """The registration's `settings.random_seed`, read as `Registration.seed` reads it."""
    if not isinstance(settings, Mapping):
        return 0
    seed = settings.get("random_seed", 0)
    return seed if isinstance(seed, int) and not isinstance(seed, bool) else 0


def _load_step_zero_registration(path: Path, repo: Path) -> StepZeroRegistration:
    """Step 0's registration from the committed history of `repo`, read as a survivor book."""
    try:
        _root, admitted = registry.committed_registration(path, repo)
    except registry.HoldoutRefusedError as error:
        raise PowerRequestError(f"{path}: {type(error).__name__}: {error}") from error
    body = admitted.registered
    config = body.get("config")
    if not isinstance(config, Mapping):
        raise PowerRequestError(
            f"{path} registers no configuration object; Step 0's book is unknown"
        )
    try:
        survivor = read_survivor_registration(config, seed=_seed_of(body.get("settings")))
    except SurvivorRegistrationError as error:
        raise PowerRequestError(f"{path}: {type(error).__name__}: {error}") from error
    config_id = body.get("config_id")
    code_commit = body.get("code_commit")
    if not isinstance(config_id, str) or not isinstance(code_commit, str):
        raise PowerRequestError(f"{path} names no config_id or code_commit")
    return StepZeroRegistration(
        path=path,
        config_id=config_id,
        registration_sha256=hashlib.sha256(admitted.content).hexdigest(),
        code_commit=code_commit,
        survivor=survivor,
    )


def _open_panel(panel_dir: Path) -> PanelStore:
    """The one place the command opens a store: the stored panel `panel_dir` (read-only)."""
    return PanelStore(panel_dir)


def _source_of(registration: StepZeroRegistration) -> dict[str, Any]:
    """What produced the counts: Step 0's registration identity and content digest."""
    return {
        "step0_registration": registration.path.as_posix(),
        "config_id": registration.config_id,
        "registration_sha256": registration.registration_sha256,
        "code_commit": registration.code_commit,
    }


def _date_argument(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError(f"{value!r} is not a YYYY-MM-DD date") from error


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="p7_c_power.py",
        description=(
            "Project how many forward sessions of plan C's window would be measurable and "
            "powered, for the primary N=300 sample and the full survivor census. Reads a stored "
            "panel only: no label, no return, no request, and no file unless --json is given."
        ),
    )
    parser.add_argument(
        "--panel-dir", required=True, help="a stored panel directory, read only, never created"
    )
    parser.add_argument(
        "--step0-registration",
        default=None,
        help=f"Step 0's committed registration (default: {STEP0_REGISTRATION_IN_REPOSITORY})",
    )
    parser.add_argument("--first", type=_date_argument, required=True, help="first session")
    parser.add_argument("--last", type=_date_argument, required=True, help="last session")
    parser.add_argument(
        "--json",
        dest="json_path",
        default=None,
        metavar="PATH",
        help=f"write the {POWER_REPORT_SCHEMA} report here; without it nothing is written",
    )
    parser.add_argument(
        "--sd-between",
        type=float,
        default=PLANNING_SD_BETWEEN,
        help=(
            "the non-sampling part of a session IC sd "
            f"(default {PLANNING_SD_BETWEEN}; a planning assumption)"
        ),
    )
    parser.add_argument(
        "--abstention-rate",
        type=float,
        default=PLANNING_ABSTENTION_RATE,
        help=(
            "the planning share of scored names a model abstains on "
            f"(default {PLANNING_ABSTENTION_RATE}; a planning assumption)"
        ),
    )
    parser.add_argument(
        "--level", type=float, default=LEVEL, help=f"the one-sided test level (default {LEVEL})"
    )
    parser.add_argument(
        "--power",
        type=float,
        default=TARGET_POWER,
        help=f"the design power (default {TARGET_POWER})",
    )
    parser.add_argument("--repo", default=str(REPOSITORY), help="the repository root")
    return parser


def _run(options: argparse.Namespace) -> int:
    first, last = options.first, options.last
    if last < first:
        raise PowerRequestError(
            f"--first {first.isoformat()} is after --last {last.isoformat()}: a range runs "
            "from its first session to its last"
        )
    repo = Path(options.repo)
    named = options.step0_registration
    registration_path = Path(named) if named else repo / STEP0_REGISTRATION_IN_REPOSITORY
    if not registration_path.is_absolute():
        registration_path = repo / registration_path
    registration = _load_step_zero_registration(registration_path, repo)
    panel_dir = Path(options.panel_dir)
    if not panel_dir.is_dir():
        raise PowerRequestError(
            f"{panel_dir} is not a directory: this command reads a stored panel and never "
            "creates one"
        )
    store = _open_panel(panel_dir)
    counts = historical_event_counts(
        store,
        first=first,
        last=last,
        book=registration.survivor.book,
        adapter=registration.survivor.adapter,
    )
    report = power_report(
        counts,
        first=first,
        last=last,
        sd_between=options.sd_between,
        abstention_rate=options.abstention_rate,
        level=options.level,
        power=options.power,
        source=_source_of(registration),
    )
    print(render_report(report))
    if options.json_path is not None:
        written = write_power_report(Path(options.json_path), report)
        print(f"written: {written}")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """The command line: 0 when it printed (and wrote, with `--json`), 2 on a named refusal."""
    parser = _parser()
    try:
        options = parser.parse_args(list(argv) if argv is not None else None)
    except SystemExit as error:
        return int(error.code or 0)
    try:
        return _run(options)
    except PowerRequestError as error:
        print(f"p7_c_power: refused: {error}", file=sys.stderr)
        return 2
    except (
        TradingCalendarError,
        PanelStorageError,
        SurvivorFactsError,
        LimitEventReadError,
        FactorTransformError,
    ) as error:
        print(f"p7_c_power: refused: {type(error).__name__}: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
