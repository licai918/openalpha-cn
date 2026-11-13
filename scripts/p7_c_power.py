"""Plan C's event-level power calculation (`V2-P7-013`, plan C Task 3.1; user ruling D1).

The question this tool answers is **can C's registered test be measured at all**, not whether an
effect exists. C's one primary statistic is the LLM family's h=1 partial rank IC on the primary
scoring set, measured over the 120 forward sessions that follow C's registration. The agents are
only routed to securities that carry evidence, and v1's only evidence producer is 011's limit
events, so the measured cross section of a session is not the sample's size but the number of
**scored names**: the sampled survivors that carry at least one limit event. That is why the
calculation is event-level, and why it reports two scoring domains side by side -- the primary
random sample of `PRIMARY_SAMPLE_SIZE` (300) survivors and the full survivor census -- with a
`testable` / `not_testable` verdict on each (D1).

## What it reads

A **stored panel** and Step 0's **committed** registration, both read-only. Per stored session
of the panel's `trade_cal` between `--first` and `--last` it rebuilds the session the way the
daily survivor day rebuilds it -- `strategy_survivor_facts.build_facts_day` at the session's own
16:30 publication instant, then `domain.survivor_screen.screen` -- and counts the survivors that
`panel_limit_events.limit_events_on` reports an event for at that same instant. Those are
**point-in-time replays of the evening the day was filed**: a session's survivors and events are
what that evening's record would have shown, not the restated present. The calendar that names
the sessions is read at the last session's publication instant, so every session it lists is
already published when it is asked.

**No label and no return is read.** This is a count of survivors and events plus arithmetic on
the count; the module imports nothing that can produce an outcome (`model_view`,
`strategy_survivor_forward`), and no client exists here at all -- no Tushare, no DeepSeek, no
socket. The report is planning information, never evidence.

## The arithmetic

For one row (sample `s`, `None` meaning the census) and one observed session with `S` survivors
and `E` survivors carrying an event:

* the effective scored names are `round(E * min(s, S) / S)`, or `E` for the census
  (`s >= S` is the census too);
* a session is **measurable** iff that count is at least `MIN_SCORED_NAMES` (10);
* the measurable fraction of the observed sessions is projected onto the forward window:
  `round(fraction * WINDOW_SESSIONS)`, and the periods are that projection divided by the
  horizon (`horizon=1` gives one period per measurable session; `h=5` gives every fifth);
* the planning between-day sd is inflated by `sqrt(mean_survivors / mean_scored)` over the
  measured sessions -- a session scoring fewer names carries a noisier IC -- and
  `domain.incremental_ic.detectable_mean` turns it into the mean detectable at the target
  power: `(z_(1-level) + z_power) * sd / sqrt(periods)`;
* the verdict is `testable` iff the projection reaches `MIN_MEASURABLE_SESSIONS` (60) **and** a
  detectable mean is computable. The verdict is strictly a **measurability** verdict: a single
  score below the detectable mean is design information, never an effect-size gate, because no
  threshold was ruled.

`PLANNING_BETWEEN_DAY_SD` (0.15) is a **planning assumption of this batch**, not a measured
value; the report says so and the protocol's power table is filled only by a real run at L5.

## The command

    python scripts/p7_c_power.py --panel-dir DIR \
        --step0-registration docs/research/p7-registration.json \
        --first YYYY-MM-DD --last YYYY-MM-DD [--json PATH]

It prints the two rows; only `--json PATH` writes anything, and then exactly one report
(`POWER_REPORT_SCHEMA` = `openalpha-p7-c-power/v1`, atomic temporary file plus `os.replace`),
which is the file the registration writer's `--power-report` reads. Every refusal -- an
unreadable registration, a missing panel directory, an empty session range, a design the
arithmetic cannot answer -- exits 2 with the reason on stderr and nothing written.

**One signature deviation from the plan's sketch.** The plan writes
`historical_event_counts(store, *, first, last, book)`; the same sentence requires the
registration's `transform`, `min_coverage`, `max_carry_sessions` and `exchange` for the facts
day, which are the adapter's and not the book's. They are passed as a required `adapter`
argument rather than defaulted, because a defaulted adapter would silently rebuild the day with
settings the registration does not have.
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

PLANNING_BETWEEN_DAY_SD: Final[float] = 0.15
"""A planning assumption of this batch, not a measured value; the report says so."""

TARGET_POWER: Final[float] = 0.8
"""The design power the detectable mean is computed for."""

LEVEL: Final[float] = 0.05
"""D5's one-sided test level."""

TestVerdict = Literal["testable", "not_testable"]
VERDICT_TESTABLE: Final[TestVerdict] = "testable"
VERDICT_NOT_TESTABLE: Final[TestVerdict] = "not_testable"
"""The measurability verdict's two values, as machine text and as the printed wording."""

CANDIDATE_WORDING: Final[str] = "candidate research protocol planning (候选), not evidence"
NO_LABELS_WORDING: Final[str] = (
    "no labels and no returns were read: this counts stored survivors and their limit events only"
)
PLANNING_SD_NOTE: Final[str] = "a planning assumption of this batch, not a measured value"
VERDICT_NOTE: Final[str] = (
    "the verdict is a measurability verdict (projected measurable sessions reach the floor and a "
    "detectable mean is computable); the detectable mean is design information, never an "
    "effect-size gate -- no threshold was ruled"
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
    """The projected scored names per measured session; `None` when nothing is measurable."""
    observed_measurable_sessions: int
    measurable_sessions: int
    periods: int
    detectable_mean: float | None
    verdict: TestVerdict


def sample_label(sample: int | None) -> str:
    """The row's name: `n{sample}` for a sample, `census` for the whole survivor set."""
    return "census" if sample is None else f"n{sample}"


def _scored_names(count: SessionCount, sample: int | None) -> int:
    """The effective scored names of one session under `sample` (census: `sample is None`)."""
    if count.survivors <= 0:
        return 0
    if sample is None or sample >= count.survivors:
        return count.event_names
    return round(count.event_names * sample / count.survivors)


def _check_design(
    *,
    sessions: int,
    horizon: int,
    between_day_sd: float,
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
    if not math.isfinite(between_day_sd) or between_day_sd <= 0:
        raise PowerRequestError(
            f"the planning between-day sd must be positive and finite, got {between_day_sd!r}"
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
    between_day_sd: float = PLANNING_BETWEEN_DAY_SD,
    level: float = LEVEL,
    power: float = TARGET_POWER,
) -> PowerRow:
    """One scoring domain's projection over the observed sessions; see the module docstring.

    `sample` is the sample's size, or `None` for the census. The measurable fraction is taken
    over the **observed** sessions and projected onto the forward `sessions`; the sd inflation
    and the projected scored-name count are means over the **measurable** sessions, because
    those are the sessions the registered test would measure.
    """
    observed = tuple(counts)
    _check_design(
        sessions=sessions,
        horizon=horizon,
        between_day_sd=between_day_sd,
        level=level,
        power=power,
        sample=sample,
    )
    if not observed:
        raise PowerRequestError("there is no observed session to project from")
    scored = tuple(_scored_names(count, sample) for count in observed)
    measurable = tuple(index for index, names in enumerate(scored) if names >= MIN_SCORED_NAMES)
    projected = 0
    periods = 0
    projected_scored: int | None = None
    detectable: float | None = None
    if measurable:
        projected = round(len(measurable) / len(observed) * sessions)
        periods = projected if horizon <= 1 else projected // horizon
        mean_survivors = math.fsum(observed[index].survivors for index in measurable) / len(
            measurable
        )
        mean_scored = math.fsum(scored[index] for index in measurable) / len(measurable)
        projected_scored = round(mean_scored)
        if mean_survivors > 0 and mean_scored > 0:
            inflated = between_day_sd * math.sqrt(mean_survivors / mean_scored)
            if periods >= 2:
                detectable = detectable_mean(sd=inflated, periods=periods, level=level, power=power)
    verdict: TestVerdict = (
        VERDICT_TESTABLE
        if projected >= MIN_MEASURABLE_SESSIONS and detectable is not None
        else VERDICT_NOT_TESTABLE
    )
    return PowerRow(
        sample=sample_label(sample),
        sample_size=sample,
        projected_scored_names=projected_scored,
        observed_measurable_sessions=len(measurable),
        measurable_sessions=projected,
        periods=periods,
        detectable_mean=detectable,
        verdict=verdict,
    )


def _row_form(row: PowerRow) -> dict[str, Any]:
    """One row as JSON: the fields the report and the registration writer read."""
    return {
        "sample": row.sample,
        "sample_size": row.sample_size,
        "projected_scored_names": row.projected_scored_names,
        "observed_measurable_sessions": row.observed_measurable_sessions,
        "measurable_sessions": row.measurable_sessions,
        "periods": row.periods,
        "detectable_mean": row.detectable_mean,
        "verdict": row.verdict,
    }


def _planning_form(
    *,
    sessions: int,
    horizon: int,
    between_day_sd: float,
    level: float,
    power: float,
) -> dict[str, Any]:
    return {
        "window_sessions": sessions,
        "primary_horizon": horizon,
        "between_day_sd": between_day_sd,
        "between_day_sd_note": PLANNING_SD_NOTE,
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
    between_day_sd: float = PLANNING_BETWEEN_DAY_SD,
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
                between_day_sd=between_day_sd,
                level=level,
                power=power,
            ),
            event_power(
                observed,
                sample=None,
                sessions=sessions,
                horizon=horizon,
                between_day_sd=between_day_sd,
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
            between_day_sd=between_day_sd,
            level=level,
            power=power,
        ),
        "source": None if source is None else dict(source),
        "rows": rows,
    }


def render_report(report: Mapping[str, Any]) -> str:
    """The report as the printed lines: both rows, their verdicts, and the wording above."""
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
            f"{planning['primary_horizon']}, between-day sd {planning['between_day_sd']:g} "
            f"({PLANNING_SD_NOTE}), one-sided test level {planning['test_level']:g}, target power "
            f"{planning['target_power']:g}"
        ),
    ]
    source = report.get("source")
    if source:
        lines.append(
            "  Step 0 registration: " + ", ".join(f"{key} {source[key]}" for key in source)
        )
    for row in report["rows"].values():
        sample_size = row["sample_size"]
        heading = (
            f"{row['sample']} (primary set N={sample_size})"
            if sample_size is not None
            else f"{row['sample']} (all survivors)"
        )
        scored = (
            "absent"
            if row["projected_scored_names"] is None
            else str(row["projected_scored_names"])
        )
        detectable = "absent" if row["detectable_mean"] is None else f"{row['detectable_mean']:.4f}"
        lines.append(
            f"  {heading}: projected {scored} scored name(s) per measured session; observed "
            f"measurable {row['observed_measurable_sessions']} of "
            f"{report['sessions_observed']} session(s); projected measurable "
            f"{row['measurable_sessions']} of {planning['window_sessions']}; periods "
            f"{row['periods']}; detectable mean IC {detectable}; "
            f"verdict {row['verdict'].replace('_', ' ')}"
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
            "Project how many forward sessions of plan C's window would be measurable, for the "
            "primary N=300 sample and the full survivor census. Reads a stored panel only: no "
            "label, no return, no request, and no file unless --json is given."
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
        "--between-day-sd",
        type=float,
        default=PLANNING_BETWEEN_DAY_SD,
        help=f"the planning between-day sd (default {PLANNING_BETWEEN_DAY_SD}; an assumption)",
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
        between_day_sd=options.between_day_sd,
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
