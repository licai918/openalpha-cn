"""B's incremental rank information over the survivor composite (`V2-P7-028`).

Plan B adds two limit-event factors to the P7 factor registry -- `limit_up_count_20/v1` and
`broken_board_count_20/v1`, both declared `lower_is_better`. The survivor composite already ranks
the same cross section with four other factors, so the question this exploratory reading asks is
narrower than a rank IC: *does an oriented limit-event factor carry rank information about the
h20 label that the composite does not already carry?*

The statistic is the **partial rank IC**, the repository's own
`domain.incremental_ic.partial_rank_ic_with_reason`:

    partial_rank_ic = Pearson(residual(rank(extra) ~ rank(composite)),
                              residual(rank(label) ~ rank(composite)))

The sample is the plan's: `p7_diagnostics.sampled_sessions` gives the engine's signal days, and
because the command's stride is the h20 horizon (`STRIDE == HORIZON`), those days are already one
per label window. The summary is taken over them directly; a plan with any other stride is
refused by name rather than thinned a second time.

**Exploratory by construction (R-N6).** The research window (`p7_windows.RESEARCH`, 2015-01-05..
2021-12-31) was P6's discovery and composition data, and 2021 was already rehearsed: nothing here
is out of sample. The document carries the `EXPLORATORY, UNREGISTERED -- NOT EVIDENCE` banner,
candidate wording, and **no p-value field and no gate**; the `t` is a descriptive number, never a
test. A walk-forward model with B's features is a later forward pre-registration, not this script.

What is reused rather than re-implemented:

* the window (`p7_windows.RESEARCH`), the window-capped store, the session planner and the
  label source come from `p7_windows` and `p7_diagnostics`; the document borrows that module's
  exploratory wording, `config_digest` shape and exploratory one-write rule;
* the arithmetic comes from `domain.incremental_ic` (the partial IC and the non-overlapping
  sample) and the summary from `backtest.decile_readout.summarize_series`;
* the factor read follows `p7_reading._FactorReads`: one factor's stored **raw** observations,
  one partition year at a time on first use, admitted `TIER_ADMITTED_CODES["raw"]` values only,
  and a session's build is the earliest build stamped at or after that session's 16:30
  publication instant (an earlier build did not know the close). It is restated here rather than
  imported because this script stands alone: it is not the registered reading, and it must not
  acquire that module's registration machinery.

The command line is `explore --runtime-dir --as-of --first --last [--out]`. It opens only the
research window, by name, and refuses by name any day outside it; without `--out` it only prints.
It also refuses by name an `--as-of` before the last sampled session's h20 label has published
(the exit session's 16:30): a knowledge clock that early would turn every label of the sample into
a gap, which is a refusal rather than a measurement.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from types import MappingProxyType
from typing import Any, Final
from zoneinfo import ZoneInfo

import grid
from p7_diagnostics import (
    CANDIDATE_WORDING,
    COMPOSITE,
    DAY_REFUSALS,
    EXPLORATORY_WORDING,
    IN_SAMPLE_WORDING,
    SEED_NOTE,
    DiagnosticRequestError,
    PanelScoreSource,
    label_years,
    panel_label_source,
    sampled_sessions,
)
from p7_windows import (
    READ_ONCE_NOTE,
    RESEARCH,
    DiagnosticWindow,
    DiagnosticWindowError,
    SignalPlan,
    WindowCappedStore,
    capped_panel_store,
    checked_runtime_directory,
    label_exit_day,
    plan_signal_days,
)

from openalpha_cn.backtest.decile_readout import summarize_series
from openalpha_cn.backtest.factor_ic import TIER_ADMITTED_CODES
from openalpha_cn.domain.factor import FactorDefinition
from openalpha_cn.domain.horizon import HorizonUnit, ResearchHorizon
from openalpha_cn.domain.incremental_ic import partial_rank_ic_with_reason
from openalpha_cn.domain.survivor_book import SurvivorBookConfig
from openalpha_cn.domain.trading_calendar import TradingCalendar, TradingCalendarError
from openalpha_cn.model_view import OutcomeLabels
from openalpha_cn.panel.store import PanelStore
from openalpha_cn.panel_factors import (
    BROKEN_BOARD_COUNT_20,
    FACTOR_TRANSFORMS,
    LIMIT_UP_COUNT_20,
    FactorEngineError,
    load_factor_observations,
)
from openalpha_cn.panel_ingest import load_trading_calendar, session_publication_instant
from openalpha_cn.runtime.provenance import resolve_code_commit
from openalpha_cn.strategy_survivor_facts import DEFAULT_MAX_CARRY_SESSIONS, DEFAULT_MIN_COVERAGE

KIND: Final = "p7-increment/v1"
"""The document's kind. It is not `p7_diagnostics`' exploratory kind: this file answers a
question about two named factors, not G-0's secondary diagnostics."""

EXPLORATORY_BANNER: Final = "EXPLORATORY, UNREGISTERED -- NOT EVIDENCE"
"""The first line of the text, the same one `p7_diagnostics` writes for its exploratory kind: the
research window was read to choose these factors, so nothing here is out of sample."""

HORIZON: Final[int] = 20
"""The label horizon, in sessions. `p7_reading`'s registered A/B section measures h20 too."""
STRIDE: Final[int] = 20
"""One signal day every twenty sessions: with the label horizon equal to the stride, two sampled
labels never share a session, so no further thinning is needed."""
TRANSFORM: Final = "cross_section_standard/v1"
"""The preprocessing the composite is screened on, `p7_reading`'s default research transform."""
EXCHANGE: Final = "SSE"
"""The calendar and the book's exchange, as in `p7_diagnostics`."""

FACTORS: Final[tuple[FactorDefinition, ...]] = (LIMIT_UP_COUNT_20, BROKEN_BOARD_COUNT_20)
"""B's two factors, in the order the document records them; both declared `lower_is_better`."""
DEFAULT_BOOK: Final[SurvivorBookConfig] = SurvivorBookConfig(seed=0)
"""The default survivor book: the scope is the daily path's own eligible names, and the book's
seed picks holdings among the survivors and never enters the screen."""
FACTOR_ORIENTATION: Final[Mapping[str, float]] = MappingProxyType(
    {"higher_is_better": 1.0, "lower_is_better": -1.0}
)
"""The declared direction as a sign on values: `lower_is_better` is negated before the IC."""

REASON_NO_ELIGIBLE_NAMES: Final = "no_eligible_names"
REASON_NO_FACTOR_BUILD: Final = "no_factor_build_for_session"
REASON_NO_LABELS: Final = "no_labels_for_session"
REASON_NO_COMPLETE_NAMES: Final = "no_names_with_all_three_values"
"""This reading's own reasons, for the cases the arithmetic would otherwise fold into
`too_few_names`: a session where the book admitted nobody, one where the factor read no build at
all, one where no eligible name carried a label, and one where the values never all met."""

_EXCHANGE_ZONE: Final[ZoneInfo] = ZoneInfo("Asia/Shanghai")
_ROOT: Final[Path] = Path(__file__).resolve().parent
"""`resolve_code_commit`'s anchor: this script's directory, the repository's own rule."""

Composite = Callable[[date], Mapping[str, float]]
"""One session's cross section, keyed by `ts_code`."""
Extra = Callable[[date], Mapping[str, float]]
"""One session's factor cross section, **already oriented** by the declared direction."""
Labels = Callable[[date], Mapping[str, float]]
"""One session's realized h20 returns, only the labelled names."""


class IncrementError(ValueError):
    """A request or an input this reading refuses by name rather than measuring wrongly."""


@dataclass(frozen=True, slots=True, kw_only=True)
class SessionIncrement:
    """One sampled session's partial rank IC, the counts behind it, and why it may have none.

    `eligible` is the book's admitted scope on the session; `n_scored` is how many of those names
    carried the composite, the extra factor and a label; `gaps` is the rest. `value`/`reason` are
    `partial_rank_ic_with_reason`'s answer, or this reading's own named reason when there was no
    cross section to hand it.
    """

    session: date
    eligible: int
    n_scored: int
    gaps: int
    value: float | None
    reason: str | None


def orient(definition: FactorDefinition, values: Mapping[str, float]) -> dict[str, float]:
    """`values` with the factor's declared direction applied: `lower_is_better` is negated.

    The declaration is the registry's, never a per-reading choice: a factor declared
    `lower_is_better` and measured on the raw values would answer the opposite question.
    """
    sign = FACTOR_ORIENTATION[definition.direction]
    return {code: sign * value for code, value in values.items()}


def increment_readings(
    *,
    sessions: Sequence[date],
    composite: Composite,
    extra: Extra,
    labels: Labels,
) -> tuple[SessionIncrement, ...]:
    """One reading per session, or the named reason it has none; see the module docstring.

    The scope is the composite callable's keys -- the default book's eligible names. A name is
    measured only when it carries the composite, the oriented extra value and a label; anything
    else is a counted gap, never a zero. A day the data refuses is a named gap of that day; a
    window breach raises (`DiagnosticWindowError` is never a gap).
    """
    readings: list[SessionIncrement] = []
    for session in sessions:
        try:
            eligible_values = dict(composite(session))
            extra_values = dict(extra(session))
            labelled = dict(labels(session))
        except DiagnosticWindowError:
            raise
        except DAY_REFUSALS as error:
            readings.append(
                SessionIncrement(
                    session=session,
                    eligible=0,
                    n_scored=0,
                    gaps=0,
                    value=None,
                    reason=_refusal_reason(error),
                )
            )
            continue
        eligible = sorted(eligible_values)
        if not eligible:
            readings.append(
                SessionIncrement(
                    session=session,
                    eligible=0,
                    n_scored=0,
                    gaps=0,
                    value=None,
                    reason=REASON_NO_ELIGIBLE_NAMES,
                )
            )
            continue
        if not extra_values:
            readings.append(
                SessionIncrement(
                    session=session,
                    eligible=len(eligible),
                    n_scored=0,
                    gaps=len(eligible),
                    value=None,
                    reason=REASON_NO_FACTOR_BUILD,
                )
            )
            continue
        if not labelled:
            readings.append(
                SessionIncrement(
                    session=session,
                    eligible=len(eligible),
                    n_scored=0,
                    gaps=len(eligible),
                    value=None,
                    reason=REASON_NO_LABELS,
                )
            )
            continue
        complete = [code for code in eligible if code in extra_values and code in labelled]
        if not complete:
            readings.append(
                SessionIncrement(
                    session=session,
                    eligible=len(eligible),
                    n_scored=0,
                    gaps=len(eligible),
                    value=None,
                    reason=REASON_NO_COMPLETE_NAMES,
                )
            )
            continue
        value, reason = partial_rank_ic_with_reason(
            [extra_values[code] for code in complete],
            [eligible_values[code] for code in complete],
            [labelled[code] for code in complete],
        )
        readings.append(
            SessionIncrement(
                session=session,
                eligible=len(eligible),
                n_scored=len(complete),
                gaps=len(eligible) - len(complete),
                value=value,
                reason=reason,
            )
        )
    return tuple(readings)


def _refusal_reason(error: Exception) -> str:
    """The gap's name: the refusal's type and its `disclosable` message where it has one, so a
    document never carries a local store path."""
    return f"{type(error).__name__}: {getattr(error, 'disclosable', str(error))}"


def banner(window: DiagnosticWindow) -> str:
    """The first line of the text (`p7_diagnostics`' exploratory banner, with this window)."""
    return f"{EXPLORATORY_BANNER}: {window.name} window {window.first}..{window.last}"


def _window_form(window: DiagnosticWindow) -> dict[str, Any]:
    return {
        "name": window.name,
        "first": window.first.isoformat(),
        "last": window.last.isoformat(),
        "ceiling_year": window.ceiling_year,
    }


def _summary_of(readings: Sequence[SessionIncrement]) -> dict[str, Any]:
    """The descriptive summary over the plan's own sampled days: n, mean, sd, and t only.

    The plan's stride is the label horizon (`increment_document` refuses any other), so its
    sampled days are already one per non-overlapping h20 window; a second thinning would drop
    periods rather than protect them. `t` is descriptive: this document has no p-value and no
    gate.
    """
    summary = summarize_series(
        [(item.session, item.value) for item in readings if item.value is not None]
    )
    return {"n": summary.n, "mean": summary.mean, "sd": summary.sd, "t": summary.t}


def _factor_block(
    definition: FactorDefinition, readings: Sequence[SessionIncrement], plan: SignalPlan
) -> dict[str, Any]:
    return {
        "factor": definition.qualified_key,
        "direction": definition.direction,
        "orientation": FACTOR_ORIENTATION[definition.direction],
        "days": [
            {
                "session": item.session.isoformat(),
                "eligible": item.eligible,
                "n": item.n_scored,
                "gaps": item.gaps,
                "value": item.value,
                "reason": item.reason,
            }
            for item in readings
        ],
        "summary": _summary_of(readings),
    }


def increment_document(
    *,
    window: DiagnosticWindow,
    plan: SignalPlan,
    readings: Mapping[str, Sequence[SessionIncrement]],
    config: Mapping[str, Any],
    code_commit: str,
    partitions_read: Sequence[tuple[str, int, str | None]] = (),
) -> dict[str, Any]:
    """The reading as one JSON-ready document of kind `p7-increment/v1`.

    `readings` is keyed by the two factors' qualified keys, in any order; the document records
    them in `FACTORS`' order. `config_digest` is the SHA-256 of `grid.canonical_json` of the
    document's own `config` and `request`, so it can be recomputed from the document. There is no
    p-value field, no q and no verdict: R-N6's reading is exploratory.
    """
    if plan.stride != STRIDE:
        raise IncrementError(
            f"the plan's stride is {plan.stride} sessions, not the reading's {STRIDE}: with a "
            f"stride equal to the {HORIZON}-session horizon the plan's sampled days are already "
            "one per non-overlapping label window, and a finer stride would need a second "
            "thinning this reading does not take"
        )
    keys = tuple(definition.qualified_key for definition in FACTORS)
    if set(readings) != set(keys):
        raise IncrementError(
            f"the readings must cover exactly {list(keys)}; got {sorted(readings)}"
        )
    request_form: dict[str, Any] = {
        "window": _window_form(window),
        "first": plan.first.isoformat(),
        "last": plan.last.isoformat(),
        "stride": plan.stride,
        "horizon": HORIZON,
        "factors": list(keys),
    }
    config_form = grid.to_json_value(dict(config))
    digest = hashlib.sha256(
        grid.canonical_json({"config": config_form, "request": request_form}).encode("utf-8")
    ).hexdigest()
    return {
        "kind": KIND,
        "banner": banner(window),
        "wording": [EXPLORATORY_WORDING, IN_SAMPLE_WORDING, CANDIDATE_WORDING],
        "window": _window_form(window),
        "plan": {
            "first": plan.first.isoformat(),
            "last": plan.last.isoformat(),
            "stride": plan.stride,
            "horizon": HORIZON,
            "sessions": [session.isoformat() for session in plan.sessions],
            "embargoed": [session.isoformat() for session in plan.embargoed],
        },
        "request": request_form,
        "config": config_form,
        "factors": [
            _factor_block(definition, readings[definition.qualified_key], plan)
            for definition in FACTORS
        ],
        "provenance": {
            "code_commit": code_commit,
            "config_digest": digest,
            "random_component": "none",
            "partitions_read": [
                {"dataset": dataset, "year": year, "content_hash": content_hash}
                for dataset, year, content_hash in partitions_read
            ],
        },
    }


def increment_lines(document: Mapping[str, Any]) -> list[str]:
    """The document as text: the banner first, one line per sampled session, then its summary."""
    plan = document["plan"]
    provenance = document["provenance"]
    lines = [document["banner"], *(f"  {wording}" for wording in document["wording"])]
    lines.append(
        f"plan {plan['first']}..{plan['last']}, one signal day every {plan['stride']} sessions, "
        f"horizon {plan['horizon']}: {len(plan['sessions'])} signal days, "
        f"{len(plan['embargoed'])} embargoed (a label that would end past the window)"
    )
    lines.append(
        f"code {provenance['code_commit']}, config {provenance['config_digest']}, random "
        f"component {provenance['random_component']}, "
        f"{len(provenance['partitions_read'])} partition(s) read"
    )
    for block in document["factors"]:
        summary = block["summary"]
        lines.append(
            f"{block['factor']}, declared direction {block['direction']} (oriented "
            f"{_number(block['orientation'])}): {len(block['days'])} session(s), "
            f"{summary['n']} measurable period(s)"
        )
        for row in block["days"]:
            reason = "" if row["reason"] is None else f", {row['reason']}"
            lines.append(
                f"  {row['session']}: partial rank IC {_number(row['value'])}, n {row['n']}, "
                f"gaps {row['gaps']}, eligible {row['eligible']}{reason}"
            )
        lines.append(
            f"  mean {_number(summary['mean'])}, sd {_number(summary['sd'])}, n {summary['n']}, "
            f"t {_number(summary['t'])} (descriptive only: no test and no gate)"
        )
    return lines


def _number(value: object) -> str:
    return "absent" if value is None else f"{value:.4g}"


# --- the panel sources ---------------------------------------------------------------------------


class _FactorReads:
    """One factor's stored **raw** observations, read one partition year at a time on first use.

    `p7_reading._FactorReads`' rule, restated: a build belongs to a session when its `as_of` is
    dated in `Asia/Shanghai` on that session; a session with more than one build answers with the
    earliest one stamped at or after its 16:30 publication instant (a build stamped before it did
    not know the close); only `TIER_ADMITTED_CODES["raw"]` values are read; non-finite values are
    dropped. The loader is point-in-time at this reading's `as_of`.
    """

    def __init__(self, store: PanelStore, *, as_of: datetime) -> None:
        self._store = store
        self._as_of = as_of
        self._years: dict[tuple[str, int], dict[date, dict[str, float]]] = {}

    def values_on(self, definition: FactorDefinition, day: date) -> Mapping[str, float]:
        key = (definition.qualified_key, day.year)
        if key not in self._years:
            self._years[key] = self._read_year(definition, day.year)
        return self._years[key].get(day, {})

    def _read_year(self, definition: FactorDefinition, year: int) -> dict[date, dict[str, float]]:
        admitted = TIER_ADMITTED_CODES["raw"]
        builds: dict[date, dict[datetime, dict[str, float]]] = {}
        for row in load_factor_observations(
            self._store, definition, years=(year,), as_of=self._as_of
        ):
            if row.coverage not in admitted or row.value is None:
                continue
            if not math.isfinite(row.value):
                continue
            session = row.as_of.astimezone(_EXCHANGE_ZONE).date()
            builds.setdefault(session, {}).setdefault(row.as_of, {})[row.subject] = row.value
        chosen: dict[date, dict[str, float]] = {}
        for session, by_instant in builds.items():
            published = session_publication_instant(session)
            after = {
                instant: values for instant, values in by_instant.items() if instant >= published
            }
            if after:
                chosen[session] = after[min(after)]
        return chosen


def _oriented_extra(factor_reads: _FactorReads, definition: FactorDefinition) -> Extra:
    """A factor's session values, oriented by its declared direction."""

    def extra(day: date) -> Mapping[str, float]:
        return orient(definition, factor_reads.values_on(definition, day))

    return extra


@dataclass(frozen=True, slots=True, kw_only=True)
class Sources:
    """What `explore` reads: the calendar, the composite scope, the labels and both factors."""

    calendar: TradingCalendar
    composite: Composite
    labels: Labels
    extras: Mapping[str, Extra]
    store: WindowCappedStore


def _open_sources(
    runtime_dir: Path, *, window: DiagnosticWindow, settings: ExploreSettings
) -> Sources:
    """The one place the command opens a store: the panel inside `runtime_dir`, capped at the
    research window, and the calendar, the composite scope, the labels and both factor reads.

    `runtime_dir` is checked first by `p7_windows.checked_runtime_directory`: P6's research root
    and every checkout are refused before anything is opened, because a read-only connection still
    creates the catalogue's lock files beside it -- read an APFS clone instead.
    """
    checked_runtime_directory(runtime_dir)
    store = capped_panel_store(runtime_dir, window)
    calendar = load_trading_calendar(
        store,
        exchange=EXCHANGE,
        years=tuple(range(settings.first.year - 1, window.ceiling_year + 1)),
        as_of=settings.as_of,
    )
    scores = PanelScoreSource(
        store=store,
        book=settings.book,
        transform=FACTOR_TRANSFORMS.get(settings.transform),
        exchange=EXCHANGE,
        as_of=settings.as_of,
        min_coverage=DEFAULT_MIN_COVERAGE,
        max_carry_sessions=DEFAULT_MAX_CARRY_SESSIONS,
    )
    composites: dict[date, Mapping[str, float]] = {}

    def composite(day: date) -> Mapping[str, float]:
        if day not in composites:
            composites[day] = dict(scores.scores_on(day)[COMPOSITE])
        return composites[day]

    factor_reads = _FactorReads(store, as_of=settings.as_of)
    extras = {
        definition.qualified_key: _oriented_extra(factor_reads, definition)
        for definition in FACTORS
    }
    readers: dict[int, OutcomeLabels] = {}
    labelled: dict[date, Mapping[str, float]] = {}

    def labels(day: date) -> Mapping[str, float]:
        """The session's labelled eligible names, read once and shared by both factors.

        The window is anchored at the session's own 16:30 (`session_publication_instant`), never
        at the reading's `as_of`: what the label is must not move with when the reading ran. Only
        the book's eligible names are asked about, so a factor value on a name the book did not
        admit cannot leak into the reading.
        """
        if day in labelled:
            return labelled[day]
        reader = readers.get(day.year)
        if reader is None:
            reader = panel_label_source(
                store,
                years=label_years(day.year, ceiling_year=window.ceiling_year),
                exchange=EXCHANGE,
                as_of=settings.as_of,
                cached_sessions=HORIZON + 2,
            )
            readers[day.year] = reader
        label_window = reader.window(
            session_publication_instant(day),
            horizon=ResearchHorizon(count=HORIZON, unit=HorizonUnit.trading_days),
        )
        found: dict[str, float] = {}
        for code in sorted(composite(day)):
            label = reader.label(code, label_window)
            if label is None or not label.is_labelled:
                continue
            realized = label.realized_return
            if realized is not None:
                found[code] = realized
        labelled[day] = MappingProxyType(found)
        return labelled[day]

    return Sources(
        calendar=calendar,
        composite=composite,
        labels=labels,
        extras=MappingProxyType(extras),
        store=store,
    )


# --- the command line ----------------------------------------------------------------------------


@dataclass(frozen=True, slots=True, kw_only=True)
class ExploreSettings:
    """The `explore` command's settings, checked before any store is opened.

    `book` and `transform` default to the reading's own (`DEFAULT_BOOK`, `TRANSFORM`); the command
    line takes no option for either, and they are fields so a test can drive `_open_sources` over
    a generated panel whose composite factors are the fixture's rather than a fresh long-history
    build of the default book's.
    """

    as_of: datetime
    first: date
    last: date
    book: SurvivorBookConfig = DEFAULT_BOOK
    transform: str = TRANSFORM

    def config(self, window: DiagnosticWindow) -> dict[str, Any]:
        """What the document records as its configuration (and digests with the request)."""
        return {
            "window": window.name,
            "exchange": EXCHANGE,
            "as_of": self.as_of,
            "first": self.first,
            "last": self.last,
            "stride": STRIDE,
            "horizon": HORIZON,
            "transform": self.transform,
            "book": self.book.model_dump(mode="json"),
            "book_seed": SEED_NOTE,
            "factors": [definition.qualified_key for definition in FACTORS],
            "directions": {
                definition.qualified_key: definition.direction for definition in FACTORS
            },
            "min_coverage": DEFAULT_MIN_COVERAGE,
            "max_carry_sessions": DEFAULT_MAX_CARRY_SESSIONS,
        }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="p7_increment",
        description=(
            "B's exploratory partial rank IC over the survivor composite: unregistered, "
            "not evidence."
        ),
    )
    commands = parser.add_subparsers(dest="command", required=True)
    research = RESEARCH
    explore = commands.add_parser(
        "explore",
        help="read the research window's partial rank IC for B's two factors",
        description=(
            f"Read the research window ({research.first}..{research.last}) and nothing else; "
            "the validation window, the holdout and the forward_ab_1 window are read only "
            "through p7_reading.read_once."
        ),
    )
    explore.add_argument("--runtime-dir", type=Path, required=True)
    explore.add_argument("--as-of", required=True, help="an aware ISO instant every read is at")
    explore.add_argument("--first", type=date.fromisoformat, required=True)
    explore.add_argument("--last", type=date.fromisoformat, required=True)
    explore.add_argument(
        "--out", type=Path, help="write the JSON and text here (default: print only)"
    )
    return parser


def _refuse(command: str, error: object, code: int) -> int:
    print(f"p7_increment {command}: {error}", file=sys.stderr)
    return code


def _settings(arguments: argparse.Namespace, window: DiagnosticWindow) -> ExploreSettings:
    """Every setting checked, and the dates held to `window`, before anything is opened."""
    first, last = arguments.first, arguments.last
    if first < window.first:
        raise DiagnosticWindowError(
            f"the plan's first day {first} is before the {window.name} window's first day "
            f"{window.first}"
        )
    if last > window.last:
        raise DiagnosticWindowError(
            f"the plan's last day {last} is past the {window.name} window's last day "
            f"{window.last}; {READ_ONCE_NOTE}"
        )
    if last < first:
        raise DiagnosticWindowError(f"the plan's last day {last} is before its first day {first}")
    as_of = datetime.fromisoformat(arguments.as_of)
    if as_of.tzinfo is None or as_of.utcoffset() is None:
        raise DiagnosticRequestError(f"--as-of {arguments.as_of} carries no timezone")
    return ExploreSettings(as_of=as_of, first=first, last=last)


def _require_matured_as_of(
    calendar: TradingCalendar, sessions: Sequence[date], as_of: datetime
) -> None:
    """Refuse an `as_of` before the last sampled day's h20 label has published.

    The label of a session enters at the next close and leaves `HORIZON` sessions later
    (`p7_windows.label_exit_day`), and it is knowable at that exit session's own 16:30. A
    knowledge clock earlier than that turns every label of the sample into a gap; this reading
    refuses by name instead, after the store is open and before anything is measured.
    """
    if not sessions:
        return
    last = sessions[-1]
    exit_day = label_exit_day(calendar, last, HORIZON)
    if exit_day is None:
        raise IncrementError(
            f"the {HORIZON}-session label of the last sampled session {last.isoformat()} cannot "
            f"be placed on the {calendar.exchange} calendar, so --as-of {as_of.isoformat()} "
            "cannot be checked against it"
        )
    published = session_publication_instant(exit_day)
    if as_of < published:
        raise IncrementError(
            f"--as-of {as_of.isoformat()} is before the {HORIZON}-session label of the last "
            f"sampled session {last.isoformat()} is published at {published.isoformat()} "
            f"({exit_day.isoformat()} 16:30 Asia/Shanghai): every label of the sample would be a "
            "gap, so this reading refuses rather than measuring gaps"
        )


def _explore(arguments: argparse.Namespace) -> int:
    window = RESEARCH
    try:
        settings = _settings(arguments, window)
    except (DiagnosticWindowError, DiagnosticRequestError) as error:
        return _refuse("explore", error, 2)
    try:
        sources = _open_sources(arguments.runtime_dir, window=window, settings=settings)
        chosen = plan_signal_days(
            sources.calendar,
            window=window,
            first=settings.first,
            last=settings.last,
            stride=STRIDE,
            horizons=(HORIZON,),
        )
        sessions = sampled_sessions(chosen, HORIZON)
        _require_matured_as_of(sources.calendar, sessions, settings.as_of)
        readings = {
            definition.qualified_key: increment_readings(
                sessions=sessions,
                composite=sources.composite,
                extra=sources.extras[definition.qualified_key],
                labels=sources.labels,
            )
            for definition in FACTORS
        }
        document = increment_document(
            window=window,
            plan=chosen,
            readings=readings,
            config=settings.config(window),
            code_commit=resolve_code_commit(anchor=_ROOT),
            partitions_read=sources.store.partitions_read(),
        )
    except DiagnosticWindowError as error:
        return _refuse("explore", error, 2)
    except (DiagnosticRequestError, IncrementError) as error:
        return _refuse("explore", error, 2)
    except (FactorEngineError, TradingCalendarError) as error:
        return _refuse("explore", error, 1)
    except DAY_REFUSALS as error:
        return _refuse("explore", f"{type(error).__name__}: {error}", 1)
    lines = increment_lines(document)
    print("\n".join(lines))
    if arguments.out is None:
        return 0
    stem = f"p7-increment-{document['provenance']['config_digest'][:16]}"
    outputs = (
        (
            arguments.out / f"{stem}.json",
            (json.dumps(document, sort_keys=True, indent=2, ensure_ascii=False) + "\n").encode(
                "utf-8"
            ),
        ),
        (arguments.out / f"{stem}.txt", ("\n".join(lines) + "\n").encode("utf-8")),
    )
    conflict = _conflicting(outputs)
    if conflict is not None:
        return _refuse("explore", conflict, 1)
    for path, fresh in _write_missing(outputs):
        print(f"written: {path}" if fresh else f"unchanged, nothing written: {path}")
    return 0


def _conflicting(outputs: Sequence[tuple[Path, bytes]]) -> str | None:
    """`registry.register`'s rule, checked for every file before any is written: different bytes
    already on disk are refused."""
    for path, content in outputs:
        if path.exists() and path.read_bytes() != content:
            return (
                f"{path} already holds a different document; a reading is written once "
                "(the same configuration read again gave other bytes)"
            )
    return None


def _write_missing(outputs: Sequence[tuple[Path, bytes]]) -> tuple[tuple[Path, bool], ...]:
    """Write each file not yet on disk; the same bytes are not written again. `(path, written)`."""
    done: list[tuple[Path, bool]] = []
    for path, content in outputs:
        fresh = not path.exists()
        if fresh:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
        done.append((path, fresh))
    return tuple(done)


def main(argv: Sequence[str] | None = None) -> int:
    """`explore` reads the research window; there is no other command."""
    arguments = _parser().parse_args(argv)
    if arguments.command == "explore":
        return _explore(arguments)
    return _refuse("main", f"unknown command {arguments.command!r}", 2)


if __name__ == "__main__":
    raise SystemExit(main())
