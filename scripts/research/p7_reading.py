"""One named, logged, single-use read of a protected window (`V2-P7-004`, for `V2-P7-024`).

`p7_diagnostics explore` opens the research window and nothing else. The validation window
(2022-01-04..2023-12-29) and the second-use stretch (2024-01-02..2026-09-24, P6's spent holdout)
are opened here: by name (`p7_windows.WINDOWS`), only after the reading's registration is committed
and a claim of the read is written. It is `registry.run_holdout`'s procedure, for a P7 reading.

`register_reading` writes `<reading>.registration.json` once -- the same bytes again return the
same digest, different bytes are refused: the reading's name, its issue, its role, its window, the
request and the configuration the diagnostics run with, its stop rule and the code commit. A
`primary` reading carries a stop rule written before the read, and none of the rule's numbers has
a default (they are the user's); a `secondary` one carries none. Every rule `register_reading`
applies is applied again by `read_once` before its claim, so a registration written by hand is
held to the same ones.

Whatever can be checked without outcome data is checked there, before anything is claimed, because
a failure after the claim spends the window:

* a reading covers its **whole** window, from the window's first session to its last (`first` and
  `last` are the window's own days). The window is read once (step 4), so a reading of part of it
  would spend all of it, and a first day that is not a session cannot be registered;
* the request reads the book's survivors (its `loser_decile_count` is the book's, as in `explore`);
* a stop rule's `minimum_periods` can be reached: it is at most the bound `_most_periods` reads off
  the weekdays of the window, the stride and the rule's horizon, with no data -- a larger one
  could only ever be "triggered: unreadable".

`read_once` refuses, in this order, each refusal its own exception, none of them writing anything:

1. `registry.RegistrationNotCommittedError` -- the registration is not in the committed history of
   the repository's `HEAD`, or the bytes on disk are not the bytes `HEAD` holds.
2. `registry.SourceChangedError` -- the bound code (`registry.REGISTERED_PATHS`: `src/`,
   `scripts/research/`, `pyproject.toml`, `uv.lock`) at `HEAD` or in the working tree is not what
   it was at the registration's `code_commit`.
3. `registry.ForeignPackageError`, `registry.ForeignScriptsError`, `ForeignP7ScriptsError` -- the
   `openalpha_cn`, the `grid` and `registry`, or the `p7_windows`, `p7_diagnostics` and this module
   this process imported are not the repository's own. Then `ReadingConfigurationError`: the
   registration is not one `register_reading` would write (not this schema, a window other than
   `validation` and `second_use`, a role and a stop rule that do not go together, a request or a
   configuration the diagnostics cannot run), or it is not filed as `<reading>.registration.json`
   in `READING_DIRECTORY`.
4. `ReadingAlreadyClaimedError` -- the reading's claim, the **window's** claim
   (`<window>.window-claim.json` in `READING_DIRECTORY`) or one of the reading's results is on
   disk, or was ever committed on any ref (`git log --all`): a read that was claimed is the one
   read, whether it finished, crashed or had its files deleted afterwards. The window's claim is
   what makes it one read **per window**, whatever reading names it -- `registry._holdout_allowed`
   refuses any holdout row the same way, whatever registration wrote it. The rule holds for every
   window a reading opens, `validation` and `second_use` alike.
5. `ReadingPreflightError` -- the panel catalogue lacks a year of the window, or the year before
   it (the calendar and the label reader read that one too), in a dataset the read opens
   (`preflight_problems`; it asks `registered_years` and reads no partition).

It then writes the window's claim and then the reading's, `<reading>.claim.json`, each with
`O_CREAT | O_EXCL` -- a second read racing this one loses there (`ReadingAlreadyClaimedError`) --
and only then measures. Both are filed beside the registration as `HEAD` holds it, whatever path
`read_once` was handed (a symbolic link elsewhere finds the same claims). From the claims on, the
read is spent:

6. the measure runs bound to `{reading, registration_sha256, registration_commit, claim_sha256}`.
   The default one is the diagnostics engine (`p7_diagnostics.run_diagnostics`) on
   `p7_windows.capped_panel_store`, every read and the content hash of every partition served
   taken inside one `PanelStore.reading()` hold, so the hashes are those of what was read. A
   refusal by the data (`p7_diagnostics.DAY_REFUSALS`) is the read's recorded `error`. A
   `DiagnosticWindowError` -- raised, or found anywhere behind a data refusal (`__cause__` or
   `__context__`) -- propagates unchanged, as does any other error, and the claim stays. A document
   that is not `p7_diagnostics.READ_ONCE_KIND`, or not bound to this claim, is refused
   (`ReadingConfigurationError`): the claim stays and no result is written;
7. a `primary` reading's stop rule is applied as written (`evaluate_stop_rule`). A metric with
   fewer than `minimum_periods` periods, or no summary at all, triggers it: a gate that cannot be
   read is closed;
8. `<reading>.result.json` and `<reading>.result.txt` are written with `O_EXCL`.

Committing the claim and the results is the caller's step, as committing the registration was.

What this does not establish:

* A commit time is what the committer's clock (or `GIT_COMMITTER_DATE`) said, and `claimed_at` is
  what this machine's clock said. Pushing the registration commit to a remote someone else holds,
  before the read, is what makes its time witnessed; this module pushes nothing.
* The claims are not in git until the caller commits them. Deleting claims that were never
  committed gives the read back, and so does another worktree or clone of the repository, which
  does not hold them: until they are committed (and, for another clone, fetched), the checkout
  that read is the only witness. A claim committed once is found by step 4 after it is deleted --
  but not after a history rewrite leaves it on no ref (a reset, a rebase or a filter, then a
  garbage collection).
* It binds a procedure, not access to the data. Code that builds a `WindowCappedStore` for the
  validation window itself, calls `read_once` with a `measure` of its own, calls the default
  measure directly (`_panel_measure(runtime_dir)(body, VALIDATION, binding)` -- the tests do, on
  stand-in stores), builds a `PanelStore` on the panel root or opens the Parquet files reads the
  window with no claim. A claim says this path was taken once; it cannot say no other path was.
* `uv.lock` in the binding proves the lock file did not change, not that the running environment
  matches it (`registry`'s limit, unchanged).
* The preflight sees which years the catalogue lists, nothing more: a partition that is listed but
  incomplete, or a signal day without a factor build, is met by the measurement, after the claim --
  and the read is spent on it.
* The second-use store serves 2026 whole, rows after 2026-09-24 included; keeping them out is the
  plan's label rule and the readers' (`p7_windows`), not the store's.

A reading is a first out-of-sample reading, not confirmatory evidence (`READ_ONCE_WORDING`); a
stop rule that is not triggered permits `V2-P7-006` to proceed and is not evidence either.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Final, Literal, get_args

import grid
import p7_diagnostics as _diagnostics
import p7_windows as _windows
import registry
from p7_diagnostics import (
    COMPOSITE,
    DAY_REFUSALS,
    DEFAULT_POWER_MEANS,
    DEFAULT_POWER_PERIODS,
    DEFAULT_POWER_TARGET,
    DEFAULT_POWER_TEST_LEVEL,
    EXCHANGE,
    METRICS,
    READ_ONCE_KIND,
    DiagnosticRequest,
    DiagnosticRequestError,
    ExploreSettings,
    PanelScoreSource,
    _gap_reason,
    _window_refusal_in,
    diagnostic_document,
    diagnostic_lines,
    label_years,
    panel_label_source,
    run_diagnostics,
)
from p7_windows import (
    CATALOG_FILE,
    RESEARCH,
    WINDOWS,
    DiagnosticWindow,
    DiagnosticWindowError,
    SignalPlan,
    capped_panel_store,
    plan_signal_days,
)

from openalpha_cn.backtest.decile_readout import DECILES
from openalpha_cn.domain.adjustment import ADJ_FACTOR_DATASET
from openalpha_cn.domain.daily_prices import DAILY_DATASET
from openalpha_cn.domain.factor_transform import FactorTransformError
from openalpha_cn.domain.horizon import MAX_HORIZON_COUNT
from openalpha_cn.domain.name_history import NAMECHANGE_DATASET
from openalpha_cn.domain.price_limits import PRICE_LIMIT_DATASET, SUSPENSION_DATASET
from openalpha_cn.domain.survivor_book import SurvivorBookConfig
from openalpha_cn.domain.trading_calendar import TRADING_CALENDAR_DATASET
from openalpha_cn.panel.store import PanelStore
from openalpha_cn.panel_factors import (
    FACTOR_DEFINITIONS,
    FACTOR_TRANSFORMS,
    factor_transform_manifest_dataset,
    processed_factor_dataset,
)
from openalpha_cn.panel_ingest import load_trading_calendar, session_publication_instant
from openalpha_cn.panel_view import PANEL_SUBDIRECTORY
from openalpha_cn.strategy_survivor_forward import LabelSource

READING_SCHEMA: Final = "openalpha-p7-reading-registration/v1"
STOP_RULE_SCHEMA: Final = "p7-stop-rule/v1"
READING_DIRECTORY: Final = Path("docs/research/p7-readings")
"""Where, under the repository, every reading's registration, claim and results are filed, and
every window's claim."""
WINDOW_CLAIM_SUFFIX: Final = ".window-claim.json"
"""`<window><suffix>` in `READING_DIRECTORY`: the claim that a window was read, by any reading."""
Role = Literal["primary", "secondary"]

STOP_TRIGGERED_WORDING: Final[str] = (
    "the stop rule written before the read is triggered: under G-0 the forward clock is not "
    "started, and the user is told there is no evidence"
)
NOT_TRIGGERED_WORDING: Final[str] = (
    "the stop rule written before the read is not triggered: this permits V2-P7-006 to proceed "
    "and is not evidence"
)

PREFLIGHT_DATASETS: Final[tuple[str, ...]] = (
    TRADING_CALENDAR_DATASET,
    DAILY_DATASET,
    ADJ_FACTOR_DATASET,
    SUSPENSION_DATASET,
    PRICE_LIMIT_DATASET,
    NAMECHANGE_DATASET,
)
"""The upstream datasets the read opens for every year of its window, besides each composite
factor's processed observations and transform manifests (`_preflight_datasets`). `stock_basic` is
read too and is not here: it is filed by lifecycle year, so a year with no partition is a year in
which nothing listed or died, not a gap (`strategy_survivor_facts._registry_year`)."""

_READING_NAME: Final = re.compile(r"[a-z0-9][a-z0-9-]*")
_ISSUE: Final = re.compile(r"V2-P[0-9][A-Z]?-[0-9]{3}[a-z]?")
_BODY_KEYS: Final = frozenset(
    {
        "schema",
        "reading",
        "issue",
        "role",
        "window",
        "request",
        "config",
        "stop_rule",
        "code_commit",
    }
)
_REQUEST_KEYS: Final = frozenset(
    {"first", "last", "stride", "horizons", "scores", "tails", "loser_decile_count"}
)
_CONFIG_KEYS: Final = frozenset(
    {"book", "transform", "exchange", "min_coverage", "max_carry_sessions", "as_of"}
)
_RULE_KEYS: Final = frozenset({"schema", "score", "horizon", "minimum_periods", "any_of"})
_CLAUSE_KEYS: Final = frozenset({"metric", "mean_at_or_below"})


class ReadingError(ValueError):
    """A registration that cannot be written as asked."""


class ReadingConfigurationError(registry.HoldoutRefusedError):
    """What the registration holds cannot be read once, or the measure's document is not this
    read's."""


class ReadingAlreadyClaimedError(registry.HoldoutRefusedError):
    """The reading was claimed already, has a result, or had its claim or result committed."""


class ReadingPreflightError(registry.HoldoutRefusedError):
    """The catalogue check before the claim found a year of the window missing."""


class ForeignP7ScriptsError(registry.HoldoutRefusedError):
    """The imported `p7_windows`, `p7_diagnostics` or this module are not the repository's own."""


@dataclass(frozen=True, slots=True, kw_only=True)
class StopClause:
    """One way the stop rule triggers: the metric's mean over the non-overlapping sample at or
    below `mean_at_or_below`."""

    metric: Literal["rank_ic", "ew_minus_worst_decile", "survivors_minus_ew", "top_minus_bottom"]
    mean_at_or_below: float

    def __post_init__(self) -> None:
        if self.metric not in METRICS:
            raise ReadingError(
                f"the stop clause's metric {self.metric!r} is not one the diagnostics summarise "
                f"({list(METRICS)})"
            )
        bound = self.mean_at_or_below
        if type(bound) not in (int, float) or not math.isfinite(bound):
            raise ReadingError(
                f"the stop clause's mean_at_or_below must be a finite number, not {bound!r}"
            )


@dataclass(frozen=True, slots=True, kw_only=True)
class StopRule:
    """Written before the read: the reading's `score` at `horizon` triggers the rule when any
    clause holds, or when a clause's metric has fewer than `minimum_periods` periods. No field has a
    default; the numbers are the user's."""

    score: str
    horizon: int
    minimum_periods: int
    any_of: tuple[StopClause, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.score, str) or not self.score:
            raise ReadingError(f"the stop rule names no score: {self.score!r}")
        for name, value in (("horizon", self.horizon), ("minimum_periods", self.minimum_periods)):
            if type(value) is not int or value < 1:
                raise ReadingError(f"the stop rule's {name} must be a whole number of at least 1")
        if not isinstance(self.any_of, tuple) or not self.any_of:
            raise ReadingError("the stop rule needs at least one clause")
        if any(not isinstance(clause, StopClause) for clause in self.any_of):
            raise ReadingError("every clause of the stop rule must be a StopClause")


Measure = Callable[[Mapping[str, Any], DiagnosticWindow, Mapping[str, Any]], dict[str, Any]]
"""`(registration body, window, binding) -> document`; the document must be a read-once one bound
to `binding`."""


@dataclass(frozen=True, slots=True, kw_only=True)
class _Settings:
    """The request and the configuration, read: the `explore` settings and the two readout ones."""

    explore: ExploreSettings
    tails: tuple[int, ...]
    loser_decile_count: int


@dataclass(frozen=True, slots=True, kw_only=True)
class _Registration:
    """What `read_once` needs of a registration whose every rule held."""

    reading: str
    window: DiagnosticWindow
    stop_rule: StopRule | None


@dataclass(frozen=True, slots=True, kw_only=True)
class _Committed:
    """The registration as `HEAD` holds it: its bytes (read once), its body, the commit that last
    touched it and its path relative to the repository root."""

    content: bytes
    body: Mapping[str, Any]
    commit: str
    relative: str


# --- the registration ----------------------------------------------------------------------------


def register_reading(
    *,
    path: Path,
    reading: str,
    issue: str,
    role: Role,
    window: str,
    request: Mapping[str, Any],
    config: Mapping[str, Any],
    stop_rule: StopRule | None,
    code_commit: str,
) -> str:
    """Write the reading's registration to `path` and return the SHA-256 of its bytes.

    Refuses (`ReadingError`) anything `read_once` would refuse to read: a reading name outside
    `[a-z0-9][a-z0-9-]*`, a `path` not named `<reading>.registration.json`, a window other than
    `validation` or `second_use` (the research window is `p7_diagnostics explore`'s), a primary
    reading without a stop rule or a secondary one with one, a stop rule whose score or horizon the
    request does not measure or whose `minimum_periods` the window cannot reach, a request that is
    not the whole window or whose loser count is not the book's, a request or a configuration the
    diagnostics cannot run, and a code commit that is not a full, clean commit id. Writing the
    same registration again returns the
    same digest; a different one at an existing path is refused. Committing it is the caller's
    step. Pass `code_commit` resolved once, before writing (`registry.register`'s reason).
    """
    try:
        request_form = grid.to_json_value(dict(request))
        config_form = grid.to_json_value(dict(config))
    except grid.ResearchLedgerError as error:
        raise ReadingError(f"the request or the configuration has no JSON form: {error}") from error
    body = {
        "schema": READING_SCHEMA,
        "reading": reading,
        "issue": issue,
        "role": role,
        "window": window,
        "request": request_form,
        "config": config_form,
        "stop_rule": None if stop_rule is None else _rule_form(stop_rule),
        "code_commit": code_commit,
    }
    _registration_of(body)
    expected = f"{reading}.registration.json"
    if path.name != expected:
        raise ReadingError(
            f"{path} is not named {expected}; a registration is named for its reading"
        )
    content = _json_bytes(body)
    if path.exists():
        if path.read_bytes() != content:
            raise ReadingError(
                f"{path} already holds a different registration; a registration is written once"
            )
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    return hashlib.sha256(content).hexdigest()


def _registration_of(body: Mapping[str, Any]) -> _Registration:
    """Every rule a registration is held to, in the order a refusal names; `ReadingError`."""
    _require_keys("registration", body, _BODY_KEYS)
    if body["schema"] != READING_SCHEMA:
        raise ReadingError(f"the registration is not a {READING_SCHEMA} file")
    reading = body["reading"]
    if not isinstance(reading, str) or not _READING_NAME.fullmatch(reading):
        raise ReadingError(
            f"the reading's name {reading!r} is not lower-case letters, digits and hyphens"
        )
    issue = body["issue"]
    if not isinstance(issue, str) or not _ISSUE.fullmatch(issue):
        raise ReadingError(f"the registration's issue {issue!r} is not a V2 issue id")
    name = body["window"]
    if name == RESEARCH.name:
        raise ReadingError(
            "the research window is not read once: it is read through p7_diagnostics explore"
        )
    window = WINDOWS.get(name) if isinstance(name, str) else None
    if window is None:
        raise ReadingError(
            f"the window {name!r} is not one a reading opens; it opens 'validation' or 'second_use'"
        )
    role = body["role"]
    if role not in get_args(Role):
        raise ReadingError(f"the role {role!r} is not 'primary' or 'secondary'")
    form = body["stop_rule"]
    if role == "secondary" and form is not None:
        raise ReadingError("a secondary reading carries no stop rule; it decides nothing")
    if role == "primary" and form is None:
        raise ReadingError("a primary reading needs its stop rule, written before the read")
    commit = body["code_commit"]
    if not isinstance(commit, str) or not registry._FULL_COMMIT.fullmatch(commit):
        raise ReadingError(
            f"code commit {commit!r} is not a full, clean git commit id; register from a "
            "committed checkout with no uncommitted change"
        )
    settings = _settings_of(body["request"], body["config"], window)
    rule = None if form is None else _rule_of(form)
    if rule is not None:
        if rule.score not in settings.explore.scores:
            raise ReadingError(
                f"the stop rule's score {rule.score!r} is not among the request's scores "
                f"{list(settings.explore.scores)}"
            )
        if rule.horizon not in settings.explore.horizons:
            raise ReadingError(
                f"the stop rule's horizon {rule.horizon} is not among the request's horizons "
                f"{list(settings.explore.horizons)}"
            )
        explore = settings.explore
        most = _most_periods(
            explore.first, explore.last, stride=explore.stride, horizon=rule.horizon
        )
        if rule.minimum_periods > most:
            raise ReadingError(
                f"the stop rule's minimum_periods {rule.minimum_periods} cannot be reached: "
                f"{explore.first}..{explore.last} holds {_weekdays(explore.first, explore.last)} "
                f"weekdays, so at most {most} non-overlapping signal days at stride "
                f"{explore.stride} and h={rule.horizon}; such a rule could only ever read "
                "'triggered: unreadable'"
            )
    return _Registration(reading=reading, window=window, stop_rule=rule)


def _weekdays(first: date, last: date) -> int:
    """The weekdays from `first` to `last`, both counted: a bound on the sessions between them."""
    weeks, rest = divmod((last - first).days + 1, 7)
    return weeks * 5 + sum(1 for day in range(rest) if (first.weekday() + day) % 7 < 5)


def _most_periods(first: date, last: date, *, stride: int, horizon: int) -> int:
    """A bound, read from no data, on the non-overlapping periods a plan of `first..last` can
    summarise: every `stride`-th session is a signal day (`plan_signal_days`), every
    `ceil(horizon / stride)`-th signal day is sampled (`p7_diagnostics.summarize`), and there are
    no more sessions than weekdays. Embargoed days and gaps only lower it."""
    signals = math.ceil(_weekdays(first, last) / stride)
    return math.ceil(signals / math.ceil(horizon / stride))


def _settings_of(request: object, config: object, window: DiagnosticWindow) -> _Settings:
    """The request and the configuration as the diagnostics will run them, every value checked
    before anything is opened; `ReadingError`."""
    request = _require_keys("request", request, _REQUEST_KEYS)
    config = _require_keys("config", config, _CONFIG_KEYS)
    first, last = _day("first", request["first"]), _day("last", request["last"])
    whole = (
        "; a reading covers its whole window, from its first session to its last -- the window is "
        "read once, so reading a part of it would spend all of it"
    )
    if first != window.first:
        side = "before" if first < window.first else "after"
        raise ReadingError(
            f"the request's first day {first} is {side} the {window.name} window's first day "
            f"{window.first}{whole}"
        )
    if last != window.last:
        side = "past" if last > window.last else "before"
        raise ReadingError(
            f"the request's last day {last} is {side} the {window.name} window's last day "
            f"{window.last}{whole}"
        )
    stride = _whole("stride", request["stride"], least=1)
    horizons = _wholes("horizons", request["horizons"], least=1, most=MAX_HORIZON_COUNT)
    tails = _wholes("tails", request["tails"], least=1, most=DECILES - 1)
    loser_decile_count = _whole("loser_decile_count", request["loser_decile_count"], least=0)
    scores = request["scores"]
    if not isinstance(scores, list) or not all(isinstance(score, str) for score in scores):
        raise ReadingError(f"the request's scores must be a list of names, not {scores!r}")
    if config["exchange"] != EXCHANGE:
        raise ReadingError(
            f"the exchange {config['exchange']!r} is not the calendar the diagnostics read "
            f"({EXCHANGE})"
        )
    as_of = _instant(config["as_of"])
    published = session_publication_instant(window.last)
    if as_of < published:
        raise ReadingError(
            f"the read's as_of {as_of.isoformat()} is before the {window.name} window's last day "
            f"was published ({published.isoformat()}); its last labels would not be known yet"
        )
    try:
        book = SurvivorBookConfig.model_validate(config["book"])
    except ValueError as error:
        raise ReadingError(f"the registered book is not a SurvivorBookConfig: {error}") from error
    if loser_decile_count != book.loser_decile_count:
        raise ReadingError(
            f"the request's loser_decile_count {loser_decile_count} is not the book's "
            f"{book.loser_decile_count}; the survivors read are the book's (explore takes the "
            "book's count)"
        )
    transform = config["transform"]
    try:
        FACTOR_TRANSFORMS.get(transform)
    except (FactorTransformError, TypeError) as error:
        raise ReadingError(
            f"the registered transform {transform!r} is not declared: {error}"
        ) from error
    known = (COMPOSITE, *book.composite_factors)
    unknown = [score for score in scores if score not in known]
    if unknown:
        raise ReadingError(f"the request's scores {unknown} are not among {list(known)}")
    min_coverage = config["min_coverage"]
    if type(min_coverage) not in (int, float) or not 0 <= min_coverage <= 1:
        raise ReadingError(f"min_coverage must be a number from 0 to 1, not {min_coverage!r}")
    settings = _Settings(
        explore=ExploreSettings(
            as_of=as_of,
            first=first,
            last=last,
            stride=stride,
            horizons=horizons,
            transform=transform,
            book=book,
            scores=tuple(scores),
            min_coverage=float(min_coverage),
            max_carry_sessions=_whole("max_carry_sessions", config["max_carry_sessions"], least=1),
        ),
        tails=tails,
        loser_decile_count=loser_decile_count,
    )
    # The engine's own checks of scores, tails and the loser count, on an empty plan of the window.
    _diagnostic_request(
        settings,
        SignalPlan(
            window=window,
            first=first,
            last=last,
            stride=stride,
            horizons=horizons,
            sessions=(),
            embargoed=(),
        ),
    )
    return settings


def _diagnostic_request(settings: _Settings, plan: SignalPlan) -> DiagnosticRequest:
    try:
        return DiagnosticRequest(
            plan=plan,
            scores=settings.explore.scores,
            tails=settings.tails,
            loser_decile_count=settings.loser_decile_count,
            power_means=DEFAULT_POWER_MEANS,
            power_periods=DEFAULT_POWER_PERIODS,
            power_target=DEFAULT_POWER_TARGET,
            power_test_level=DEFAULT_POWER_TEST_LEVEL,
        )
    except DiagnosticRequestError as error:
        raise ReadingError(str(error)) from error


def _require_keys(what: str, value: object, keys: frozenset[str]) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ReadingError(f"the {what} must be a JSON object, not {value!r}")
    missing = sorted(keys - set(value))
    if missing:
        raise ReadingError(f"the {what} lacks {missing}")
    extra = sorted(set(value) - keys)
    if extra:
        raise ReadingError(f"the {what} carries {extra}, which the read does not use")
    return value


def _day(name: str, value: object) -> date:
    if not isinstance(value, str):
        raise ReadingError(f"the request's {name} day must be an ISO date string, not {value!r}")
    try:
        return date.fromisoformat(value)
    except ValueError as error:
        raise ReadingError(f"the request's {name} day {value!r} is not an ISO date") from error


def _instant(value: object) -> datetime:
    if not isinstance(value, str):
        raise ReadingError(f"the read's as_of must be an ISO instant string, not {value!r}")
    try:
        instant = datetime.fromisoformat(value)
    except ValueError as error:
        raise ReadingError(f"the read's as_of {value!r} is not an ISO instant") from error
    if instant.tzinfo is None or instant.utcoffset() is None:
        raise ReadingError(f"the read's as_of {value} carries no timezone")
    return instant


def _whole(name: str, value: object, *, least: int) -> int:
    if type(value) is not int or value < least:
        raise ReadingError(f"the {name} must be a whole number of at least {least}, not {value!r}")
    return value


def _wholes(name: str, value: object, *, least: int, most: int) -> tuple[int, ...]:
    if (
        not isinstance(value, list)
        or not value
        or any(type(item) is not int or not least <= item <= most for item in value)
    ):
        raise ReadingError(
            f"the request's {name} must be a non-empty list of whole numbers from {least} to "
            f"{most}, not {value!r}"
        )
    return tuple(value)


def _rule_form(rule: StopRule) -> dict[str, Any]:
    return {
        "schema": STOP_RULE_SCHEMA,
        "score": rule.score,
        "horizon": rule.horizon,
        "minimum_periods": rule.minimum_periods,
        "any_of": [
            {"metric": clause.metric, "mean_at_or_below": clause.mean_at_or_below}
            for clause in rule.any_of
        ],
    }


def _rule_of(form: object) -> StopRule:
    rule = _require_keys("stop rule", form, _RULE_KEYS)
    if rule["schema"] != STOP_RULE_SCHEMA:
        raise ReadingError(f"the stop rule is not a {STOP_RULE_SCHEMA} rule")
    clauses = rule["any_of"]
    if not isinstance(clauses, list):
        raise ReadingError(f"the stop rule's any_of must be a list, not {clauses!r}")
    return StopRule(
        score=rule["score"],
        horizon=rule["horizon"],
        minimum_periods=rule["minimum_periods"],
        any_of=tuple(
            StopClause(**_require_keys("stop clause", clause, _CLAUSE_KEYS)) for clause in clauses
        ),
    )


# --- the stop rule -------------------------------------------------------------------------------


def evaluate_stop_rule(rule: StopRule, document: Mapping[str, Any]) -> dict[str, Any]:
    """The rule applied as written to `document["summary"][score][f"h{horizon}"]`.

    `triggered` when any clause's metric has fewer than `minimum_periods` periods (or no finite
    mean, or the document no such summary -- a refused measurement has none): the gate cannot be
    read, and a gate that cannot be read is closed (`reason` says so); otherwise `triggered` when
    any clause's mean is at or below its bound, and `not triggered` when none is.
    """
    summary = document.get("summary")
    by_score = summary.get(rule.score) if isinstance(summary, Mapping) else None
    block = by_score.get(f"h{rule.horizon}") if isinstance(by_score, Mapping) else None
    clauses: list[dict[str, Any]] = []
    unreadable: list[str] = []
    if not isinstance(block, Mapping):
        refused = document.get("error")
        unreadable.append(
            f"the document has no summary of {rule.score} at h={rule.horizon}"
            + ("" if refused is None else f" (the measurement was refused: {refused})")
        )
    for clause in rule.any_of:
        series = block.get(clause.metric) if isinstance(block, Mapping) else None
        n = series.get("n") if isinstance(series, Mapping) else None
        mean = series.get("mean") if isinstance(series, Mapping) else None
        met: bool | None = None
        if (
            type(n) is int
            and n >= rule.minimum_periods
            and isinstance(mean, int | float)
            and not isinstance(mean, bool)
            and math.isfinite(mean)
        ):
            met = mean <= clause.mean_at_or_below
        clauses.append(
            {
                "metric": clause.metric,
                "n": n,
                "mean": mean,
                "mean_at_or_below": clause.mean_at_or_below,
                "met": met,
            }
        )
        if met is None and isinstance(block, Mapping):
            unreadable.append(
                f"{clause.metric} has {n} period(s) and mean {mean}, and the rule needs at least "
                f"{rule.minimum_periods} with a finite mean"
            )
    if unreadable:
        outcome = "triggered"
        reason: str | None = (
            "unreadable: " + "; ".join(unreadable) + " -- a gate that cannot be read is closed"
        )
    else:
        outcome = "triggered" if any(clause["met"] for clause in clauses) else "not triggered"
        reason = None
    return {
        "outcome": outcome,
        "clauses": clauses,
        "reason": reason,
        "wording": STOP_TRIGGERED_WORDING if outcome == "triggered" else NOT_TRIGGERED_WORDING,
    }


# --- the preflight -------------------------------------------------------------------------------


def preflight_problems(
    store: PanelStore, *, window: DiagnosticWindow, datasets: Sequence[str]
) -> tuple[str, ...]:
    """Each dataset of `datasets` that lists no partition for a year the read opens, by name with
    the years it lacks; `()` when every one lists them all. The years are the window's and the one
    before it, which the calendar and the label reader also read (`label_years`). Only
    `registered_years` is asked: no partition is opened, so a listed year may still be
    incomplete."""
    years = tuple(range(window.first.year - 1, window.ceiling_year + 1))
    problems: list[str] = []
    for dataset in datasets:
        held = set(store.registered_years(dataset))
        missing = [year for year in years if year not in held]
        if missing:
            problems.append(
                f"{dataset} lists no partition for {missing} of the {window.name} window "
                f"{window.first}..{window.last}"
            )
    return tuple(problems)


def _preflight_datasets(book: SurvivorBookConfig) -> tuple[str, ...]:
    """`PREFLIGHT_DATASETS` and, for each composite factor, its processed observations and its
    transform manifests (the two datasets `load_processed_factor_observations` opens)."""
    definitions = [FACTOR_DEFINITIONS.get(factor) for factor in book.composite_factors]
    return (
        *PREFLIGHT_DATASETS,
        *(processed_factor_dataset(definition) for definition in definitions),
        *(factor_transform_manifest_dataset(definition) for definition in definitions),
    )


def _panel_preflight(
    runtime_dir: Path, window: DiagnosticWindow
) -> Callable[[Mapping[str, Any]], Sequence[str]]:
    """The default preflight: the catalogue of the panel inside `runtime_dir`, capped at `window`
    -- or, with no catalogue there, that problem, and no store opened (opening one would create its
    root)."""

    def preflight(body: Mapping[str, Any]) -> Sequence[str]:
        panel = runtime_dir / PANEL_SUBDIRECTORY
        if not (panel / CATALOG_FILE).is_file():
            return (f"{panel} holds no {CATALOG_FILE}: there is no panel catalogue to read",)
        book = _settings_of(body["request"], body["config"], window).explore.book
        store = capped_panel_store(runtime_dir, window)
        return preflight_problems(store, window=window, datasets=_preflight_datasets(book))

    return preflight


# --- the default measure -------------------------------------------------------------------------


def _panel_measure(runtime_dir: Path) -> Measure:
    """The registered diagnostics on the panel inside `runtime_dir`, capped at the window.

    The calendar, every score and label, and the content hash of every partition served are all
    read inside one `PanelStore.reading()` hold, so the hashes logged are those of the partitions
    the numbers came from (`WindowCappedStore.partitions_read` asks the catalogue when it is
    called). The hold is kept across the computation as well: writers wait for it, which a read of
    a cloned panel can afford. The document records the registration's `code_commit`, which steps
    2 and 3 of `read_once` hold the running code to.
    """

    def measure(
        body: Mapping[str, Any], window: DiagnosticWindow, binding: Mapping[str, Any]
    ) -> dict[str, Any]:
        try:
            settings = _settings_of(body["request"], body["config"], window)
        except ReadingError as error:
            raise ReadingConfigurationError(
                f"the registration cannot be measured: {error}"
            ) from error
        explore = settings.explore
        store = capped_panel_store(runtime_dir, window)
        with store.reading():
            calendar = load_trading_calendar(
                store,
                exchange=EXCHANGE,
                years=tuple(range(explore.first.year - 1, window.ceiling_year + 1)),
                as_of=explore.as_of,
            )
            plan = plan_signal_days(
                calendar,
                window=window,
                first=explore.first,
                last=explore.last,
                stride=explore.stride,
                horizons=explore.horizons,
            )
            request = _diagnostic_request(settings, plan)
            scores = PanelScoreSource(
                store=store,
                book=explore.book,
                transform=FACTOR_TRANSFORMS.get(explore.transform),
                exchange=EXCHANGE,
                as_of=explore.as_of,
                min_coverage=explore.min_coverage,
                max_carry_sessions=explore.max_carry_sessions,
            )
            cached_sessions = max(explore.horizons) + 2

            def labels_for(year: int) -> LabelSource:
                return panel_label_source(
                    store,
                    years=label_years(year, ceiling_year=window.ceiling_year),
                    exchange=EXCHANGE,
                    as_of=explore.as_of,
                    cached_sessions=cached_sessions,
                )

            readings = run_diagnostics(request, scores=scores, labels_for=labels_for)
            partitions = store.partitions_read()
        return diagnostic_document(
            request,
            readings,
            config=explore.config(window),
            code_commit=body["code_commit"],
            partitions_read=partitions,
            binding=binding,
        )

    return measure


# --- the read ------------------------------------------------------------------------------------


def read_once(
    registration: Path,
    *,
    repo: Path,
    runtime_dir: Path,
    clock: Callable[[], datetime] | None = None,
    measure: Measure | None = None,
    preflight: Callable[[Mapping[str, Any]], Sequence[str]] | None = None,
) -> dict[str, Any]:
    """Read the registered window once; see the module docstring for the order and the refusals.

    Returns the measured document, with `stop_rule` (`evaluate_stop_rule`) added for a primary
    reading; a refusal by the data returns `{"binding", "error"}` in its place. `measure` and
    `preflight` default to the panel inside `runtime_dir`.
    """
    now = _utc_now if clock is None else clock
    root, committed = _committed_reading(registration, repo)
    registry._refuse_a_changed_source(
        root,
        committed.body.get("code_commit"),
        purpose="a read-once reading measures with the registered code only",
    )
    registry._refuse_a_foreign_package(root)
    registry._refuse_foreign_scripts(root)
    _refuse_foreign_p7_scripts(root)
    try:
        registered = _registration_of(committed.body)
    except ReadingError as error:
        raise ReadingConfigurationError(
            f"{committed.relative} cannot be read once: {error}"
        ) from error
    _require_filed(committed.relative, registered.reading)
    window = registered.window
    # Beside the registration as `HEAD` holds it, never beside the path handed in: a link to the
    # registration from elsewhere must find the claims the filed one left.
    filed = root / committed.relative
    claim = filed.with_name(f"{registered.reading}.claim.json")
    window_claim = filed.with_name(f"{window.name}{WINDOW_CLAIM_SUFFIX}")
    result_json = filed.with_name(f"{registered.reading}.result.json")
    result_txt = filed.with_name(f"{registered.reading}.result.txt")
    _refuse_a_second_read(
        root,
        registered.reading,
        window,
        claim=claim,
        window_claim=window_claim,
        results=(result_json, result_txt),
    )
    check = _panel_preflight(runtime_dir, window) if preflight is None else preflight
    problems = tuple(check(committed.body))
    if problems:
        raise ReadingPreflightError(
            f"the {window.name} window is not read: the catalogue check before the claim found "
            + "; ".join(problems)
            + " -- nothing was claimed"
        )
    started = now()
    if started.tzinfo is None or started.utcoffset() is None:
        raise ValueError(f"the clock gave {started!r}, which carries no timezone")
    registration_sha256 = hashlib.sha256(committed.content).hexdigest()
    _write_new(
        window_claim,
        _json_bytes(
            {
                "reading": registered.reading,
                "registration_sha256": registration_sha256,
                "window": window.name,
                "claimed_at": started.isoformat(),
            }
        ),
    )
    claim_content = _json_bytes(
        {
            "reading": registered.reading,
            "registration_sha256": registration_sha256,
            "registration_commit": committed.commit,
            "window": window.name,
            "claimed_at": started.isoformat(),
        }
    )
    _write_new(claim, claim_content)
    binding = {
        "reading": registered.reading,
        "registration_sha256": registration_sha256,
        "registration_commit": committed.commit,
        "claim_sha256": hashlib.sha256(claim_content).hexdigest(),
    }
    measured = _measured(
        _panel_measure(runtime_dir) if measure is None else measure,
        committed.body,
        window,
        binding,
    )
    result = dict(measured)
    if registered.stop_rule is not None:
        result["stop_rule"] = evaluate_stop_rule(registered.stop_rule, measured)
    _write_new(result_json, _json_bytes(result))
    _write_new(
        result_txt, ("\n".join(reading_lines(result, committed.body)) + "\n").encode("utf-8")
    )
    return result


def _committed_reading(registration: Path, repo: Path) -> tuple[Path, _Committed]:
    """`registry._committed_registration`'s admission, for this module's schema."""
    if not registration.is_file():
        raise registry.RegistrationNotCommittedError(
            f"{registration} is not a file; there is no registration to hold the reading to"
        )
    content = registration.read_bytes()
    top = registry._git(repo, "rev-parse", "--show-toplevel")
    if top.returncode != 0:
        raise registry.RegistrationNotCommittedError(
            f"{repo} is not a git repository: {top.stderr.decode(errors='replace').strip()}"
        )
    root = Path(top.stdout.decode().strip()).resolve()
    try:
        relative = registration.resolve().relative_to(root).as_posix()
    except ValueError as error:
        raise registry.RegistrationNotCommittedError(
            f"{registration} is outside the repository {root}; only a file git holds can be a "
            "committed registration"
        ) from error
    log = registry._git(root, "log", "-1", "--format=%H", "HEAD", "--", relative)
    commit = log.stdout.decode().strip()
    if log.returncode != 0 or not commit:
        raise registry.RegistrationNotCommittedError(
            f"{relative} is not in the committed history of {root}'s HEAD; commit the "
            "registration before the reading"
        )
    held = registry._git(root, "show", f"HEAD:{relative}")
    if held.returncode != 0 or held.stdout != content:
        raise registry.RegistrationNotCommittedError(
            f"the bytes on disk at {relative} are not the ones HEAD holds; an edit after the "
            "commit is an unregistered change"
        )
    try:
        body = json.loads(content)
    except ValueError as error:
        raise ReadingConfigurationError(f"{relative} is not JSON: {error}") from error
    if not isinstance(body, dict) or body.get("schema") != READING_SCHEMA:
        raise ReadingConfigurationError(f"{relative} is not a {READING_SCHEMA} file")
    return root, _Committed(content=content, body=body, commit=commit, relative=relative)


def _require_filed(relative: str, reading: str) -> None:
    """The registration is `<reading>.registration.json` in `READING_DIRECTORY`, so its claim and
    results land where every reading's are."""
    directory, _, name = relative.rpartition("/")
    if directory != READING_DIRECTORY.as_posix():
        raise ReadingConfigurationError(
            f"{relative} is not in {READING_DIRECTORY.as_posix()}, where every reading is filed"
        )
    if name != f"{reading}.registration.json":
        raise ReadingConfigurationError(
            f"{relative} is filed as {name}, not {reading}.registration.json"
        )


def _imported_p7_scripts() -> tuple[Path, ...]:
    """Where this process imported the P7 scripts from: `p7_windows`, `p7_diagnostics`, then this
    module."""
    return (
        Path(_windows.__file__).resolve(),
        Path(_diagnostics.__file__).resolve(),
        Path(__file__).resolve(),
    )


def _refuse_foreign_p7_scripts(root: Path) -> None:
    scripts = root / registry.SCRIPTS_ROOT
    for script in _imported_p7_scripts():
        try:
            script.relative_to(scripts)
        except ValueError as error:
            raise ForeignP7ScriptsError(
                f"this process imported {script.name} from {script}, which is not under "
                f"{scripts}; the window guard, the diagnostics and this read would not be the code "
                "the registration binds"
            ) from error


def _refuse_a_second_read(
    root: Path,
    reading: str,
    window: DiagnosticWindow,
    *,
    claim: Path,
    window_claim: Path,
    results: Sequence[Path],
) -> None:
    """Refuse when the reading's claim, the window's claim or a result is on disk, or was ever
    committed on any ref. Every path is under `root` (built from the filed registration)."""
    whose = f"the {window.name} window"
    files = (
        (claim, "a claim", reading),
        (window_claim, "a window claim", whose),
        *((path, "a result", reading) for path in results),
    )
    for path, _, spent in files:
        if path.exists():
            first = _claimed_by(path) if path == window_claim else None
            raise ReadingAlreadyClaimedError(
                f"{path} exists: {spent} was claimed already"
                + ("" if first is None else f" (by {first})")
                + ", and a read that was claimed is the one read, whether it finished or crashed"
                + (" -- whatever reading names the window" if path == window_claim else "")
            )
    for path, what, spent in files:
        relative = path.relative_to(root).as_posix()
        log = registry._git(root, "log", "--all", "--format=%H", "--", relative)
        if log.returncode != 0:
            raise ReadingAlreadyClaimedError(
                f"git log cannot tell whether {relative} was ever committed "
                f"({log.stderr.decode(errors='replace').strip()}); a read that cannot be shown "
                "to be the first is refused, so the one read is not risked"
            )
        commits = log.stdout.decode().split()
        if commits:
            raise ReadingAlreadyClaimedError(
                f"{what} was committed at {commits[-1]} ({relative}); deleting the file does not "
                f"give the read back -- {spent} had its one read"
            )


def _claimed_by(window_claim: Path) -> str | None:
    """The reading a window claim names, for the refusal's message; `None` if it names none."""
    try:
        reading = json.loads(window_claim.read_bytes()).get("reading")
    except (OSError, ValueError, AttributeError):
        return None
    return reading if isinstance(reading, str) else None


def _measured(
    measure: Measure,
    body: Mapping[str, Any],
    window: DiagnosticWindow,
    binding: Mapping[str, Any],
) -> dict[str, Any]:
    """The measure's document, checked; a data refusal's recorded error; or the window refusal."""
    breach: DiagnosticWindowError | None = None
    try:
        document = measure(body, window, dict(binding))
    except DiagnosticWindowError:
        raise
    except DAY_REFUSALS as error:
        breach = _window_refusal_in(error)
        if breach is None:
            return {"binding": dict(binding), "error": _gap_reason(error)}
    if breach is not None:
        # Raised here, after the handler, as `p7_diagnostics.run_diagnostics` does: the window
        # refusal itself propagates, not the refusal it was found behind.
        raise breach
    kind = document.get("kind") if isinstance(document, Mapping) else None
    if kind != READ_ONCE_KIND or document.get("binding") != dict(binding):
        raise ReadingConfigurationError(
            f"the measure returned a {kind!r} document bound to "
            f"{document.get('binding') if isinstance(document, Mapping) else None!r}, which is "
            f"not a read-once document bound to this claim ({READ_ONCE_KIND}, {dict(binding)}); "
            "the claim stands, the read is spent, and no result is written"
        )
    return dict(document)


def reading_lines(result: Mapping[str, Any], body: Mapping[str, Any]) -> list[str]:
    """The result as text: the read-once banner (from the registration), the stop rule's outcome
    and wording, and then -- for a real measurement -- `p7_diagnostics.diagnostic_lines` after its
    own banner."""
    window = WINDOWS[body["window"]]
    lines = [
        f"PRE-REGISTERED READ-ONCE {body['reading']} -- A FIRST OUT-OF-SAMPLE READING, NOT "
        f"CONFIRMATORY EVIDENCE: {window.name} window {window.first.isoformat()}.."
        f"{window.last.isoformat()}",
        f"  the {body['role']} reading registered for {body['issue']}",
    ]
    if "plan" not in result:
        binding = result["binding"]
        lines.append("binding: " + ", ".join(f"{key} {binding[key]}" for key in sorted(binding)))
    if "error" in result:
        lines.append(f"the measurement was refused, and the one read is spent: {result['error']}")
    stop = result.get("stop_rule")
    if stop is None:
        lines.append("no stop rule: a secondary, non-confirmatory reading decides nothing")
    else:
        rule = body["stop_rule"]
        lines.append(
            f"stop rule, written before the read: {rule['score']} at h={rule['horizon']}, at "
            f"least {rule['minimum_periods']} periods, triggered if any of: "
            + "; ".join(
                f"{clause['metric']} mean at or below {clause['mean_at_or_below']:g}"
                for clause in rule["any_of"]
            )
        )
        for clause in stop["clauses"]:
            met = {True: "yes", False: "no", None: "unreadable"}[clause["met"]]
            lines.append(
                f"  {clause['metric']}: n {clause['n']}, mean {_number(clause['mean'])}, at or "
                f"below {clause['mean_at_or_below']:g}: {met}"
            )
        lines.append(
            f"stop rule {stop['outcome']}"
            + ("" if stop["reason"] is None else f" -- {stop['reason']}")
        )
        lines.append(f"  {stop['wording']}")
    if "plan" in result:
        lines.extend(diagnostic_lines(result)[1:])
    return lines


def _number(value: object) -> str:
    return "absent" if value is None else f"{value:.4g}"


def _json_bytes(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


def _write_new(path: Path, content: bytes) -> None:
    """Create `path` exclusively and write `content` to disk; a file already there is a read that
    got there first. Binary on every platform, so no newline is translated."""
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
    try:
        descriptor = os.open(path, flags, 0o644)
    except FileExistsError as error:
        raise ReadingAlreadyClaimedError(
            f"{path} appeared after the checks: another read got there first, and a reading has "
            "one read"
        ) from error
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())


def _utc_now() -> datetime:
    return datetime.now(UTC)
