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

The command for the read is `scripts/research/p7_read.py` (`register [--dry-run]` and `read`); it
is a thin front end, and the library path stays `read_once`. Its exit codes are 0 (the
registration or the result was written), 2 (usage, or a `ReadingError` the registration cannot be
written with), 3 (refused before the claim) and 4 (`ExpectedPeriodsMismatch`: the calendar's count
is not the prior -- stop and report, nothing was written or claimed).

Whatever can be checked without outcome data is checked there, before anything is claimed, because
a failure after the claim spends the window:

* a reading covers its **whole** window, from the window's first session to its last (`first` and
  `last` are the window's own days). The window is read once (step 4), so a reading of part of it
  would spend all of it, and a first day that is not a session cannot be registered;
* the request reads the book's survivors (its `loser_decile_count` is the book's, as in `explore`);
* a stop rule's `minimum_periods` can be reached. The data-free bound `_most_periods` reads off
  the weekdays of the window, the stride and the rule's horizon is necessary but not sufficient: a
  rule at that bound can still be unreachable. The **exact** bound is read from the panel's own
  `trade_cal` before the claim (the calendar is not outcome data), through the same embargo
  `plan_signal_days` applies and the sampling `summarize` uses; a rule above it is refused at
  registration when a `runtime_dir` is given, and before the claim by `read_once`'s default
  preflight, with the exact number in the message. A larger rule could only ever be
  "not measurable";
* a primary registration's `expected_periods` -- the exact number of non-overlapping periods the
  plan expects this window's calendar to hold -- is checked against that same exact bound at
  registration (when a `runtime_dir` is given), by `_panel_preflight` before any claim, and by the
  command's own pre-check. The tool computes the count; a count other than the prior is an
  `ExpectedPeriodsMismatch` and stops the procedure -- it is reported, never adjusted to match the
  calendar. A secondary reading carries no count.

`read_once` refuses, in this order, each refusal its own exception, none of them writing anything:

0. `p7_windows.RuntimeDirectoryRefusedError` -- the runtime directory is inside P6's research root
   (`~/openalpha-research`) or inside the main checkout. A read-only connection still creates
   `catalog.duckdb.lock` and `catalog.duckdb.gate` beside the catalogue, and a read-once
   measurement holds the catalogue's shared lock for its whole computation: the panel read is an
   APFS clone (`cp -c`) somewhere else. This is checked before anything is read or claimed.
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
   read, whether it finished, crashed or had its files deleted afterwards. It is one read **per
   window**, whatever reading names it: every claim file in `READING_DIRECTORY` -- a reading's
   `<reading>.claim.json` or a `<window>.window-claim.json` -- records its window, and each one
   that names this window refuses, whether it is on disk now or any version of it was ever
   committed there on any ref; a claim file that cannot be read -- one naming no window in
   `WINDOWS`, a near miss such as "Validation" or "validation\\n" included -- and any git error
   refuse too. `registry._holdout_allowed` refuses any holdout row the same way, whatever
   registration wrote it. The rule holds for every window a reading opens, `validation` and
   `second_use` alike. Before any of it, a **shallow** repository is refused: its history stops
   at the cut, so a claim committed before it is invisible. The remedy is `git fetch
   --unshallow`, and it is an operational prerequisite of the real read: this repository's main
   checkout was shallow when this was written (2026-11-02). So is a repository holding a grafts
   file (`info/grafts`), which changes the parents git reports while the repository still says it
   is not shallow; replacement refs are not refused but ignored (`--no-replace-objects`).
5. `ValidationNotSpentError` -- a `second_use` reading requires the validation window's claim,
   `<validation>.window-claim.json`, to be **committed** and reachable from some ref or unexpired
   reflog entry (the same `_LOG_EVERYTHING` scan step 4 uses). A claim on disk but never committed
   is not enough: it can be deleted, and the validation window read again after 2024-2026 was
   seen. The other order needs no check: a validation read never looks at the second-use stretch.
6. `ReadingPreflightError` -- the panel catalogue lacks a year the read opens of a dataset
   (`preflight_problems`: it asks each dataset's `registered_years`, and for the primary reading
   it also reads the window's years and the year before of `trade_cal` -- the calendar is not
   outcome data -- to check the stop rule's `minimum_periods`): the window's years for every
   dataset, and the year before as well for those that read it (`YEAR_BEFORE_DATASETS`: the
   calendar, the label reader's and the facts day's) -- not for `stk_limit` and the factor
   datasets, which are read for signal days only. The same preflight checks the primary
   reading's stop rule `minimum_periods`, and the exact `expected_periods` it records, against
   the calendar's count (one arithmetic, `_exact_most_periods`; a count other than the prior is
   a named problem here, before the claim). Other datasets are still only listed: a
   partition that is listed but incomplete, or a signal day without a factor build, is met by
   the measurement, after the claim.

It then writes the window's claim and then the reading's, `<reading>.claim.json`, each with
`O_CREAT | O_EXCL` -- a second read racing this one loses there (`ReadingAlreadyClaimedError`) --
and only then measures. Both are filed beside the registration as `HEAD` holds it, whatever path
`read_once` was handed (a symbolic link elsewhere finds the same claims). From the claims on, the
read is spent:

7. the measure runs bound to `{reading, registration_sha256, registration_commit, claim_sha256}`.
   The default one is the diagnostics engine (`p7_diagnostics.run_diagnostics`) on
   `p7_windows.capped_panel_store`, every read and the content hash of every partition served
   taken inside one `PanelStore.reading()` hold, so the hashes are those of what was read. A
   refusal by the data (`p7_diagnostics.DAY_REFUSALS`) is the read's recorded `error`. A
   `DiagnosticWindowError` -- raised, or found anywhere behind a data refusal (`__cause__` or
   `__context__`) -- propagates unchanged, as does any other error, and the claim stays. A document
   that is not `p7_diagnostics.READ_ONCE_KIND`, or not bound to this claim, is refused
   (`ReadingConfigurationError`): the claim stays and no result is written;
8. a `primary` reading's stop rule is applied as written (`evaluate_stop_rule`). A metric with
   fewer than `minimum_periods` periods, or no summary at all, makes the rule `not measurable`:
   that is its own conclusion; it means the reading cannot be read in either direction, and does
   **not** mean there is no evidence;
9. `<reading>.result.json` and `<reading>.result.txt` are written with `O_EXCL`.

Committing the claims and the results is the caller's step, as committing the registration was,
and the result's text says so.

What this does not establish:

* A commit time is what the committer's clock (or `GIT_COMMITTER_DATE`) said, and `claimed_at` is
  what this machine's clock said. Pushing the registration commit to a remote someone else holds,
  before the read, is what makes its time witnessed; this module pushes nothing.
* The window rule is witnessed by the claim files naming the window and by nothing else. They
  are not in git until the caller commits them: deleting claims that were never committed gives
  the read back, and so does another worktree or clone of the repository, which does not hold
  them. **The claims must be committed, and the commit kept in this repository's refs (and, for
  another clone, fetched there), for the rule to survive another clone or worktree.** A claim
  committed once is found by step 4 after it is deleted for as long as git still holds a commit
  that added or changed it and some ref **or reflog entry** reaches that commit
  (`_LOG_EVERYTHING`): every ref (`--all`); every reflog entry until it expires (`--reflog`: a
  reset, an amend, a rebase, a detached commit, and every stash still in `git stash list`,
  `stash@{1}` and below included); every parent of every merge (`--full-history`; `-m` for a
  claim added in a merge commit itself); root commits; replacement refs ignored
  (`--no-replace-objects`), and a grafts file refused. What it does **not** see, plainly: a
  claim that was never committed; a claim whose only commits are no longer reachable from any
  ref or unexpired reflog entry (a dropped stash, an expired reflog) or have been pruned; and a
  history rewritten and garbage-collected. A shallow repository hides everything before its cut,
  which is why it is refused rather than read.
* Failing closed is permanent and global. A claim file that cannot be read, once committed on
  any ref, refuses every reading of **every** window for as long as a ref reaches it -- it might
  name any of them. The only remedy today is a history rewrite that drops it (and, once nothing
  reaches the old commits, the expiry of unreachable objects), which also destroys the real
  claims witnessing the reads already taken. An escape hatch is proposed for a later issue and
  is **not implemented**: when a claim's bytes cannot be read, take its window from the file
  name (`<window>.window-claim.json`) or from the committed `<reading>.registration.json` of the
  reading it is named for, refuse every window only when neither yields one, and for the rest
  keep a reviewed, committed void list keyed by the exact blob SHA of each unreadable file.
* It binds a procedure, not access to the data. Code that builds a `WindowCappedStore` for the
  validation window itself, calls `read_once` with a `measure` of its own, calls the default
  measure directly (`_panel_measure(runtime_dir)(body, VALIDATION, binding)` -- the tests do, on
  stand-in stores), builds a `PanelStore` on the panel root or opens the Parquet files reads the
  window with no claim. A claim says this path was taken once; it cannot say no other path was.
  The second-use ordering guard is procedural in the same way: a hand-written, committed
  validation window claim satisfies it -- and, as step 4 says, also marks the validation window
  read, whatever reading names it, so forging one spends that window's read. It does not require
  the validation window's **measurement** to have finished: a validation reading that claimed its
  window and crashed satisfies it too, which is what "the window is spent" means.
* `uv.lock` in the binding proves the lock file did not change, not that the running environment
  matches it (`registry`'s limit, unchanged).
* The preflight sees which years the catalogue lists and, for a primary reading, the calendar's
  rows; nothing more: a partition that is listed but incomplete, or a signal day without a factor
  build, is met by the measurement, after the claim -- and the read is spent on it.
* The second-use store serves 2026 whole, rows after 2026-09-24 included; keeping them out is the
  plan's label rule and the readers' (`p7_windows`), not the store's.
* The reading is taken under the knowledge at its registered `as_of`, not replayed at each
  session's publication instant: every partition is read as it stood at `as_of`, so a revision or
  a late name announcement published after a session -- or after the window -- enters that
  session's scores. `as_of` may be long after the window (the year cap, not `as_of`, enforces the
  economic date); the registration says why that instant is the one, and the result's text repeats
  it.

A reading of the validation window is a first out-of-sample reading, not confirmatory evidence
(`p7_diagnostics.READ_ONCE_WORDING`). A reading of the second-use window is not even that: the
window was P6's one-time holdout, already spent, and P7 was designed after seeing it, so its
reading is a secondary, non-confirmatory reading of a spent holdout
(`p7_diagnostics.SECOND_USE_READ_ONCE_WORDING`, and the result's first line says so). A stop rule
that is not triggered permits `V2-P7-006` to proceed and is not evidence either.
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
    read_once_banner,
    run_diagnostics,
)
from p7_windows import (
    CATALOG_FILE,
    RESEARCH,
    WINDOWS,
    DiagnosticWindow,
    DiagnosticWindowError,
    RuntimeDirectoryRefusedError,
    SignalPlan,
    capped_panel_store,
    checked_runtime_directory,
    plan_signal_days,
)

from openalpha_cn.backtest.decile_readout import DECILES
from openalpha_cn.domain.adjustment import ADJ_FACTOR_DATASET
from openalpha_cn.domain.daily_prices import DAILY_DATASET
from openalpha_cn.domain.factor_transform import FactorTransformError
from openalpha_cn.domain.horizon import MAX_HORIZON_COUNT
from openalpha_cn.domain.name_history import NAMECHANGE_DATASET
from openalpha_cn.domain.price_limits import PRICE_LIMIT_DATASET, SUSPENSION_DATASET
from openalpha_cn.domain.stock_universe import STOCK_BASIC_DATASET
from openalpha_cn.domain.survivor_book import SurvivorBookConfig
from openalpha_cn.domain.trading_calendar import TRADING_CALENDAR_DATASET, TradingCalendarError
from openalpha_cn.panel.catalog import PanelStorageError
from openalpha_cn.panel.store import PanelStore
from openalpha_cn.panel_factors import (
    FACTOR_DEFINITIONS,
    FACTOR_TRANSFORMS,
    factor_transform_manifest_dataset,
    processed_factor_dataset,
)
from openalpha_cn.panel_ingest import load_trading_calendar, session_publication_instant
from openalpha_cn.panel_view import PANEL_SUBDIRECTORY
from openalpha_cn.strategy_survivor_forward import (
    ADOPTED_PRIMARY_HORIZON,
    ADOPTED_PRIMARY_METRIC,
    ADOPTED_STOP_MEAN_AT_OR_BELOW,
    ADOPTED_STOP_TAIL_MEAN_AT_OR_BELOW,
    ADOPTED_STOP_TAIL_METRIC,
    LabelSource,
)

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
NOT_MEASURABLE_WORDING: Final[str] = (
    "the reading cannot be measured: the stop rule decided nothing, and this is not a reading of "
    "no evidence -- it is its own conclusion, and what follows is the user's to say"
)
"""The third conclusion; never the 'no evidence' wording."""
_KNOWLEDGE_LINE: Final[str] = (
    "read under the knowledge at {as_of} (-- the registrant's words: {reason}) -- every partition "
    "is read as it stood then, so a revision or name published after a session enters that "
    "session's scores; this is a recomputation at that instant, not a replay at each session's "
    "publication instant"
)

YEAR_BEFORE_DATASETS: Final[tuple[str, ...]] = (
    TRADING_CALENDAR_DATASET,
    DAILY_DATASET,
    ADJ_FACTOR_DATASET,
    SUSPENSION_DATASET,
    NAMECHANGE_DATASET,
    STOCK_BASIC_DATASET,
)
"""The datasets the read opens for the year before the window as well as the window's own years:
the calendar (from the year before), the label reader over `label_years` -- calendar, registry,
adjustment factors and halts, `model_view._LabelInputs`, and the bars of each label -- and the
facts day's history (`namechange` and `daily` from the first stored year through the signal
day's). The label reader refuses a missing `stock_basic` year in its range, so it is asked for
here although the facts day alone would read through the newest lifecycle year."""
PREFLIGHT_DATASETS: Final[tuple[str, ...]] = (*YEAR_BEFORE_DATASETS, PRICE_LIMIT_DATASET)
"""The upstream datasets the read opens, besides each composite factor's processed observations
and transform manifests (`_preflight_datasets`). `stk_limit` and the factor datasets are read for
signal days only, so for the window's own years only (`strategy_survivor_facts._read_facts_day`;
`model_view`: the factor store need not hold the year before)."""

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
        "expected_periods",
        "code_commit",
        "as_of_reason",
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


class ExpectedPeriodsMismatch(ReadingError):
    """The panel's calendar does not hold the number of periods the plan expects. It is its own
    error so the command can stop with an exit code of its own (4): a count that is not the
    prior is reported, never adjusted to match the calendar, and nothing is written or claimed."""


class ReadingPreflightError(registry.HoldoutRefusedError):
    """The check before the claim found a year of the window missing, a calendar the stop
    rule's bound needs unreadable, a `minimum_periods` the calendar cannot reach, or a calendar
    that does not make the `expected_periods` the registration records."""


class ForeignP7ScriptsError(registry.HoldoutRefusedError):
    """The imported `p7_windows`, `p7_diagnostics` or this module are not the repository's own."""


class ValidationNotSpentError(registry.HoldoutRefusedError):
    """A second-use read before the validation window's claim is committed."""


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


def adopted_stop_rule(*, score: str, expected_periods: int) -> StopRule:
    """The user's adopted stop rule (2026-11-07), for the validation reading: the score's h20
    rank IC and its worst-decile spread, at the numbers the forward protocol also uses
    (`strategy_survivor_forward.ADOPTED_*`), with `minimum_periods` the exact count the plan
    expects (`expected_periods`). No number is written here twice."""
    return StopRule(
        score=score,
        horizon=ADOPTED_PRIMARY_HORIZON,
        minimum_periods=expected_periods,
        any_of=(
            StopClause(
                metric=ADOPTED_PRIMARY_METRIC, mean_at_or_below=ADOPTED_STOP_MEAN_AT_OR_BELOW
            ),
            StopClause(
                metric=ADOPTED_STOP_TAIL_METRIC,
                mean_at_or_below=ADOPTED_STOP_TAIL_MEAN_AT_OR_BELOW,
            ),
        ),
    )


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
    settings: _Settings


@dataclass(frozen=True, slots=True, kw_only=True)
class _Committed:
    """The registration as `HEAD` holds it: its bytes (read once), its body, the commit that last
    touched it and its path relative to the repository root."""

    content: bytes
    body: Mapping[str, Any]
    commit: str
    relative: str


# --- the registration ----------------------------------------------------------------------------


def _body_of(
    *,
    reading: str,
    issue: str,
    role: Role,
    window: str,
    request: Mapping[str, Any],
    config: Mapping[str, Any],
    stop_rule: StopRule | None,
    expected_periods: int | None,
    code_commit: str,
    as_of_reason: str,
) -> dict[str, Any]:
    """The registration body `register_reading` writes, before any rule is applied. The command's
    `--dry-run` validates through this and `_registration_of`, so it exercises exactly the body a
    real registration would write instead of a second, drifting one."""
    try:
        request_form = grid.to_json_value(dict(request))
        config_form = grid.to_json_value(dict(config))
    except grid.ResearchLedgerError as error:
        raise ReadingError(f"the request or the configuration has no JSON form: {error}") from error
    return {
        "schema": READING_SCHEMA,
        "reading": reading,
        "issue": issue,
        "role": role,
        "window": window,
        "request": request_form,
        "config": config_form,
        "stop_rule": None if stop_rule is None else _rule_form(stop_rule),
        "expected_periods": expected_periods,
        "code_commit": code_commit,
        "as_of_reason": as_of_reason,
    }


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
    as_of_reason: str,
    expected_periods: int | None = None,
    runtime_dir: Path | None = None,
) -> str:
    """Write the reading's registration to `path` and return the SHA-256 of its bytes.

    Refuses (`ReadingError`) anything `read_once` would refuse to read: a reading name outside
    `[a-z0-9][a-z0-9-]*`, a `path` not named `<reading>.registration.json`, a window other than
    `validation` or `second_use` (the research window is `p7_diagnostics explore`'s), a primary
    reading without a stop rule or a secondary one with one, a stop rule whose score or horizon the
    request does not measure or whose `minimum_periods` the window cannot reach, a request that is
    not the whole window or whose loser count is not the book's, a request or a configuration the
    diagnostics cannot run, and a code commit that is not a full, clean commit id. Writing the
    same registration again returns the same digest; a different one at an existing path is
    refused. Committing it is the caller's step. Pass `code_commit` resolved once, before writing
    (`registry.register`'s reason).

    `minimum_periods` is bounded twice. With no `runtime_dir` (the default) only the data-free
    weekday bound applies; the exact calendar bound is applied by `read_once` before its claim.
    With `runtime_dir`, the runtime-directory rule is checked, then the panel's own `trade_cal` is
    read (the calendar is not outcome data) and a `minimum_periods` above what its embargo and the
    summary's sampling permit is refused -- and nothing is written.

    `as_of_reason` is the registration's answer, in one line, to why its configuration's `as_of` is
    the knowledge the reading is taken under: an `as_of` may be long after the window (the year
    cap, not `as_of`, enforces the economic date), and every partition is read as it stood at
    `as_of`, so later revisions and late renames enter in-window sessions' scores. It must be a
    non-empty single line (refused otherwise) and the result's text prints it verbatim.

    `expected_periods` is the exact number of non-overlapping periods the plan expects the
    window's calendar to hold. A `primary` reading must carry it and a `secondary` one must not;
    it is checked three times, always through the same arithmetic (`_exact_most_periods`): here
    when a `runtime_dir` is given (a mismatch is an `ExpectedPeriodsMismatch` and nothing is
    written), by `_panel_preflight` before any claim, and by the command's own pre-check. A
    calendar that makes a different count stops the procedure -- the prior is reported, never
    edited to match the calendar.

    The body this writes is a new format: no registration existed before `as_of_reason` and
    `expected_periods`, so `READING_SCHEMA`'s `.../v1` is its first format and adding the fields
    needs no migration -- this body is not one of hard rule 3's contract rows (it is admitted by
    `_BODY_KEYS` and `_registration_of` by hand, with no pydantic model and no `extra="forbid"`).
    """
    body = _body_of(
        reading=reading,
        issue=issue,
        role=role,
        window=window,
        request=request,
        config=config,
        stop_rule=stop_rule,
        expected_periods=expected_periods,
        code_commit=code_commit,
        as_of_reason=as_of_reason,
    )
    registered = _registration_of(body)
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
        runtime = None if runtime_dir is None else _checked_runtime(runtime_dir)
        if registered.stop_rule is not None and runtime is not None:
            try:
                store = capped_panel_store(runtime, registered.window)
                most = _exact_most_periods(
                    store,
                    window=registered.window,
                    settings=registered.settings,
                    horizon=registered.stop_rule.horizon,
                )
            except (DiagnosticWindowError, TradingCalendarError, PanelStorageError) as error:
                raise ReadingError(
                    f"the panel at {runtime} cannot be read to check the stop rule's "
                    f"minimum_periods: {error}"
                ) from error
            if registered.stop_rule.minimum_periods > most:
                raise ReadingError(
                    _unreachable_minimum(
                        window=registered.window,
                        settings=registered.settings,
                        rule=registered.stop_rule,
                        most=most,
                    )
                )
            problem = expected_periods_problem(
                body.get("expected_periods"),
                most,
                window=registered.window,
                horizon=registered.stop_rule.horizon,
            )
            if problem is not None:
                raise ExpectedPeriodsMismatch(problem)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    return hashlib.sha256(content).hexdigest()


def _checked_runtime(runtime_dir: Path) -> Path:
    """`checked_runtime_directory` folded into `register_reading`'s `ReadingError` contract."""
    try:
        return checked_runtime_directory(runtime_dir)
    except RuntimeDirectoryRefusedError as error:
        raise ReadingError(str(error)) from error


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
    reason = body["as_of_reason"]
    if not isinstance(reason, str) or not reason.strip() or reason != reason.strip():
        raise ReadingError(
            "the registration's as_of_reason must be one non-empty line saying why its as_of is "
            "the knowledge the reading is taken under"
        )
    if "\n" in reason or "\r" in reason:
        raise ReadingError(
            "the registration's as_of_reason must be one line: the result text prints it as one"
        )
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
                "'not measurable'"
            )
    expected = body.get("expected_periods")
    if role == "primary" and rule is not None:
        if type(expected) is not int or expected < 1:
            raise ReadingError(
                "a primary reading records expected_periods, the exact number of non-overlapping "
                f"periods the plan expects this window's calendar to hold; got {expected!r}"
            )
        if expected < rule.minimum_periods:
            raise ReadingError(
                f"the stop rule's minimum_periods {rule.minimum_periods} is more than "
                f"expected_periods {expected}: the rule could never be read at the count the "
                "plan expects"
            )
        explore = settings.explore
        most = _most_periods(
            explore.first, explore.last, stride=explore.stride, horizon=rule.horizon
        )
        if expected > most:
            raise ReadingError(
                f"expected_periods {expected} is more than the {window.name} window's weekdays "
                f"can hold ({most} at stride {explore.stride} and h={rule.horizon})"
            )
    elif expected is not None:
        raise ReadingError(
            "a secondary reading carries no expected_periods: it has no stop rule and no count "
            f"to check; got {expected!r}"
        )
    return _Registration(reading=reading, window=window, stop_rule=rule, settings=settings)


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


def _exact_most_periods(
    store: PanelStore, *, window: DiagnosticWindow, settings: _Settings, horizon: int
) -> int:
    """The most non-overlapping periods `store`'s own calendar can summarise, computed exactly as
    the measure plans and `summarize` samples: `plan_signal_days` over the calendar (which
    embargoes every signal day whose label would exit past `window.last`), then every
    `ceil(horizon / stride)`-th kept signal day (`p7_diagnostics.summarize`)."""
    explore = settings.explore
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
    return math.ceil(len(plan.sessions) / math.ceil(horizon / explore.stride))


def counted_periods(
    runtime_dir: Path,
    *,
    window: DiagnosticWindow,
    request: Mapping[str, Any],
    config: Mapping[str, Any],
    horizon: int,
) -> int:
    """The exact non-overlapping periods the panel's own calendar can summarise for this window,
    read before anything is claimed (the calendar is not outcome data). The public shell of
    `_exact_most_periods`: `register_reading`, `_panel_preflight` and the command all read the
    count through that one arithmetic, never a second one."""
    checked_runtime_directory(runtime_dir)
    settings = _settings_of(request, config, window)
    store = capped_panel_store(runtime_dir, window)
    return _exact_most_periods(store, window=window, settings=settings, horizon=horizon)


def expected_periods_problem(
    expected: int | None, most: int, *, window: DiagnosticWindow, horizon: int
) -> str | None:
    """What is wrong when the calendar's exact count is not the registration's prior, or `None`
    when it is (or when there is no prior to check). A mismatch stops the procedure: the message
    says both numbers and says to report it, never to edit the prior to match the calendar."""
    if expected is None or expected == most:
        return None
    return (
        f"the panel's calendar makes exactly {most} non-overlapping periods over the "
        f"{window.name} window {window.first}..{window.last} at h={horizon}, and the "
        f"registration expects {expected}: the count is not what the plan expects -- stop and "
        "report it; nothing was claimed and the window was not read"
    )


def _unreachable_minimum(
    *, window: DiagnosticWindow, settings: _Settings, rule: StopRule, most: int
) -> str:
    explore = settings.explore
    return (
        f"the stop rule's minimum_periods {rule.minimum_periods} cannot be reached: the panel's "
        f"calendar makes at most {most} non-overlapping periods at stride {explore.stride} and "
        f"h={rule.horizon} over {explore.first}..{explore.last} (a signal day whose label would "
        f"exit past {window.last} is embargoed, and the summary reads every "
        f"ceil({rule.horizon}/{explore.stride})th kept signal day); such a rule could only ever "
        "read 'not measurable'"
    )


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

    `not measurable` when any clause's metric has fewer than `minimum_periods` periods (or no
    finite mean, or the document no such summary -- a refused measurement has none): the rule
    cannot be read, and that is its own conclusion, not a reading of no evidence (`reason` says
    which metrics were unreadable); otherwise `triggered` when any clause's mean is at or below
    its bound, and `not triggered` when none is.
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
        outcome = "not measurable"
        reason: str | None = (
            "unreadable: " + "; ".join(unreadable) + " -- a gate that cannot be read is closed"
        )
    else:
        outcome = "triggered" if any(clause["met"] for clause in clauses) else "not triggered"
        reason = None
    wordings: dict[str, str] = {
        "triggered": STOP_TRIGGERED_WORDING,
        "not triggered": NOT_TRIGGERED_WORDING,
        "not measurable": NOT_MEASURABLE_WORDING,
    }
    return {
        "outcome": outcome,
        "clauses": clauses,
        "reason": reason,
        "wording": wordings[outcome],
    }


# --- the preflight -------------------------------------------------------------------------------


def preflight_problems(
    store: PanelStore, *, window: DiagnosticWindow, datasets: Sequence[str]
) -> tuple[str, ...]:
    """Each dataset of `datasets` that lists no partition for a year the read opens of it, by name
    with the years it lacks; `()` when every one lists them all. The years are the window's, and
    for a dataset of `YEAR_BEFORE_DATASETS` the year before it as well (the calendar, the label
    reader over `label_years` and the facts day's history read it); `stk_limit` and the factor
    datasets are read for signal days only, so a store whose factor builds begin with the window
    is not refused for the year before. Only `registered_years` is asked: no partition is opened,
    so a listed year may still be incomplete."""
    problems: list[str] = []
    for dataset in datasets:
        first = window.first.year - (1 if dataset in YEAR_BEFORE_DATASETS else 0)
        years = range(first, window.ceiling_year + 1)
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
    root). It then checks the stop rule's `minimum_periods` against the panel's own calendar,
    reading `trade_cal` (which is not outcome data) for the window's years and the year before."""
    checked_runtime_directory(runtime_dir)

    def preflight(body: Mapping[str, Any]) -> Sequence[str]:
        panel = runtime_dir / PANEL_SUBDIRECTORY
        if not (panel / CATALOG_FILE).is_file():
            return (f"{panel} holds no {CATALOG_FILE}: there is no panel catalogue to read",)
        settings = _settings_of(body["request"], body["config"], window)
        store = capped_panel_store(runtime_dir, window)
        problems = preflight_problems(
            store, window=window, datasets=_preflight_datasets(settings.explore.book)
        )
        if problems:
            return problems
        form = body.get("stop_rule")
        if form is None:
            return ()
        try:
            rule = _rule_of(form)
            most = _exact_most_periods(
                store, window=window, settings=settings, horizon=rule.horizon
            )
        except ReadingError as error:
            return (f"the stop rule cannot be read: {error}",)
        except DiagnosticWindowError as error:
            return (
                "the calendar the stop rule's bound needs cannot be planned before the claim "
                f"({error}); the rule's minimum_periods cannot be shown reachable",
            )
        except (TradingCalendarError, PanelStorageError) as error:
            return (
                "the calendar the stop rule's bound needs cannot be read before the claim "
                f"({error}); the rule's minimum_periods cannot be shown reachable",
            )
        if rule.minimum_periods > most:
            return (_unreachable_minimum(window=window, settings=settings, rule=rule, most=most),)
        problem = expected_periods_problem(
            body.get("expected_periods"), most, window=window, horizon=rule.horizon
        )
        if problem is not None:
            return (problem,)
        return ()

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
    checked_runtime_directory(runtime_dir)

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
    checked_runtime_directory(runtime_dir)
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
    if window.name == WINDOWS["second_use"].name:
        _require_the_validation_window_spent(root)
    check = _panel_preflight(runtime_dir, window) if preflight is None else preflight
    problems = tuple(check(committed.body))
    if problems:
        raise ReadingPreflightError(
            f"the {window.name} window is not read: the check before the claim found "
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
    log = registry._git(root, _NO_REPLACEMENTS, "log", "-1", "--format=%H", "HEAD", "--", relative)
    commit = log.stdout.decode().strip()
    if log.returncode != 0 or not commit:
        raise registry.RegistrationNotCommittedError(
            f"{relative} is not in the committed history of {root}'s HEAD; commit the "
            "registration before the reading"
        )
    held = registry._git(root, _NO_REPLACEMENTS, "show", f"HEAD:{relative}")
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


def _refuse_a_shaped_history(
    root: Path,
    *,
    refusal: type[RuntimeError],
    consequence: str,
    grafts_unreadable: str | None = None,
    grafts_present: str | None = None,
) -> None:
    """Refuse a repository whose history git reports reshaped -- shallow, or holding an
    `info/grafts` file -- naming the remedy and `consequence` (why the caller's question cannot
    be answered from the repository). The two grafts branches default to `consequence`;
    `_refuse_a_second_read` passes the wordings its grafts messages have always used, so those
    messages do not move. Both entry points call this first: the guarantee is the function's, not
    its caller's order."""
    shallow = registry._git(root, "rev-parse", "--is-shallow-repository")
    if shallow.returncode != 0 or shallow.stdout.strip() != b"false":
        said = (shallow.stdout or shallow.stderr).decode(errors="replace").strip()
        raise refusal(
            f"{root} is a shallow repository, or git cannot say that it is not ({said!r}): its "
            f"history stops at the cut, so {consequence}; run `git fetch --unshallow` first"
        )
    grafts = registry._git(root, "rev-parse", "--git-path", "info/grafts")
    if grafts.returncode != 0:
        raise refusal(
            f"git cannot say where {root}'s grafts file would be "
            f"({grafts.stderr.decode(errors='replace').strip()}); "
            f"{grafts_unreadable or consequence}"
        )
    named = Path(grafts.stdout.decode().strip())
    grafts_file = named if named.is_absolute() else root / named
    if grafts_file.exists():
        raise refusal(
            f"{grafts_file} is a grafts file: it changes the parents git reports for every "
            f"reader (and the repository still says it is not shallow), so "
            f"{grafts_present or consequence}; remove it, or convert it with `git replace "
            "--convert-graft-file`, whose replacement refs this module ignores"
        )


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
    committed on any ref. Every path is under `root` (built from the filed registration). A
    shallow repository, or one holding a grafts file, is refused first: its history stops at the
    cut, and a claim committed before it would be invisible to every check below."""
    _refuse_a_shaped_history(
        root,
        refusal=ReadingAlreadyClaimedError,
        consequence=(
            "a claim committed before it cannot be seen and the window cannot be shown unread"
        ),
        grafts_unreadable="a read that cannot be shown to be the first is refused",
        grafts_present="a claim it hides cannot be seen",
    )
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
        log = registry._git(root, *_LOG_EVERYTHING, "--format=%H", "--", relative)
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
    _refuse_any_claim_of_the_window(root, window)


def _require_the_validation_window_spent(root: Path) -> None:
    """Refuse unless the validation window's claim is committed and reachable from some ref.

    The artifact is `<validation>.window-claim.json`, written unconditionally by the first
    `read_once` of that window before its reading's claim, and named by window rather than by
    reading. It must be committed: a claim only on disk can be deleted, and the validation window
    read again after 2024-2026 was seen -- the order this check exists to prevent. The commit is
    looked for exactly as `_refuse_a_second_read` looks for a spent claim (`_LOG_EVERYTHING`):
    every ref, every unexpired reflog entry, every parent of every merge.
    """
    _refuse_a_shaped_history(
        root,
        refusal=ValidationNotSpentError,
        consequence=(
            "the validation window cannot be shown read first and the second_use read is refused"
        ),
    )
    relative = (READING_DIRECTORY / f"{WINDOWS['validation'].name}{WINDOW_CLAIM_SUFFIX}").as_posix()
    log = registry._git(root, *_LOG_EVERYTHING, "--format=%H", "--", relative)
    if log.returncode != 0:
        raise ValidationNotSpentError(
            f"git cannot tell whether {relative} was ever committed "
            f"({log.stderr.decode(errors='replace').strip()}); whether the validation window was "
            "read first cannot be shown, so the second_use read is refused"
        )
    commits = log.stdout.decode().split()
    on_disk = root / relative
    if commits:
        shown = registry._git(root, _NO_REPLACEMENTS, "show", f"{commits[-1]}:{relative}")
        try:
            named = json.loads(shown.stdout).get("window")
        except (ValueError, AttributeError):
            named = None
        if shown.returncode != 0 or named != WINDOWS["validation"].name:
            raise ValidationNotSpentError(
                f"{relative} was first committed at {commits[-1]} but does not read as a claim "
                "naming the validation window; the ordering cannot be shown, so the second_use "
                "read is refused"
            )
        return
    if on_disk.is_file():
        raise ValidationNotSpentError(
            f"{on_disk.relative_to(root).as_posix()} is on disk but was never committed: a "
            "claim that is not committed can be deleted, and the validation window read again "
            "after 2024-2026 was seen; commit it and keep that commit in this repository's refs, "
            "then read the second_use window"
        )
    raise ValidationNotSpentError(
        "the second_use window is read only after the validation window: no committed "
        f"{relative} exists. A 2024-2026 result seen first could still change the validation "
        "stop rule's numbers written afterwards; read the validation window first (its "
        "registration and stop rule committed before it), then commit its window claim"
    )


_NO_REPLACEMENTS: Final = "--no-replace-objects"
"""Every git call here that reads history passes this: a replacement ref (`git replace`,
`git replace --graft`) would otherwise change what a commit, its parents or a blob are for every
reader without rewriting any branch."""

_LOG_EVERYTHING: Final[tuple[str, ...]] = (
    _NO_REPLACEMENTS,
    "-c",
    "core.quotePath=false",
    "-c",
    "log.showRoot=true",
    "log",
    "--all",
    "--reflog",
    "--full-history",
)
"""Every `git log` over the claims: every ref (`--all`: branches, tags, remote-tracking refs,
`refs/stash`); every reflog entry until it expires (`--reflog`: a reset, an amend, a rebase, a
detached commit, a stash pushed down to `stash@{1}` and below); every parent of every merge
(`--full-history`: git's default history simplification follows a merge's TREESAME parent only,
which hides claims added and removed on a merged branch, and a stash's untracked-files parent);
root commits' files listed whatever the configuration says (`log.showRoot`; a stash's untracked
files are a root commit); names unquoted (`core.quotePath`); replacement refs ignored."""


def _is_claim_file(name: str) -> bool:
    """A reading's claim (`<reading>.claim.json`) or a window's (`<window>.window-claim.json`)."""
    return name.endswith(".claim.json") or name.endswith(WINDOW_CLAIM_SUFFIX)


def _refuse_any_claim_of_the_window(root: Path, window: DiagnosticWindow) -> None:
    """Refuse when any claim file in `READING_DIRECTORY` names `window` -- on disk now, or in any
    version ever added to or changed in that directory on any ref -- whatever reading wrote it.

    Every claim records its window, so each one witnesses the window's read; the window claim is
    one witness among them, not the only one. A claim file that cannot be read, and any git
    error, refuses: either might hide a claim of this window.
    """
    directory = root / READING_DIRECTORY
    on_disk = (
        sorted(path for path in directory.iterdir() if _is_claim_file(path.name))
        if directory.is_dir()
        else []
    )
    for path in on_disk:
        relative = path.relative_to(root).as_posix()
        try:
            content = path.read_bytes()
        except OSError as error:
            raise ReadingAlreadyClaimedError(
                f"{relative} cannot be read as a claim ({error}); a claim that cannot be read "
                "might name this window, so the read is refused"
            ) from error
        if _window_named(content, where=relative) == window.name:
            raise ReadingAlreadyClaimedError(
                f"{relative} names the {window.name} window: a claim of it is on disk, so the "
                "window had its one read, whatever reading names it"
            )
    log = registry._git(
        root,
        *_LOG_EVERYTHING,
        # Every parent's diff of a merge (a claim added only in a merge commit), additions never
        # paired into renames, and the names NUL-terminated and unquoted.
        "-m",
        "--no-renames",
        "-z",
        "--format=%H",
        "--name-only",
        "--diff-filter=AM",
        "--",
        READING_DIRECTORY.as_posix(),
    )
    if log.returncode != 0:
        raise ReadingAlreadyClaimedError(
            f"git log cannot list the claims ever committed in {READING_DIRECTORY.as_posix()} "
            f"({log.stderr.decode(errors='replace').strip()}); a read that cannot be shown to be "
            "the window's first is refused"
        )
    commit: str | None = None
    for token in log.stdout.split(b"\0"):
        # `-z` ends each commit's hash and each name with a NUL; the first name after a hash
        # follows a newline.
        line = token.removeprefix(b"\n").decode("utf-8", "surrogateescape")
        if registry._FULL_COMMIT.fullmatch(line):
            commit = line
            continue
        if not line or not _is_claim_file(line.rpartition("/")[2]):
            continue
        if commit is None:
            raise ReadingAlreadyClaimedError(
                f"git log listed {line} under no commit; a claim that cannot be placed might name "
                "this window, so the read is refused"
            )
        shown = registry._git(root, _NO_REPLACEMENTS, "show", f"{commit}:{line}")
        if shown.returncode != 0:
            raise ReadingAlreadyClaimedError(
                f"git cannot show {line} as committed at {commit} "
                f"({shown.stderr.decode(errors='replace').strip()}); a claim that cannot be read "
                "might name this window, so the read is refused"
            )
        if _window_named(shown.stdout, where=f"{line} at {commit}") == window.name:
            raise ReadingAlreadyClaimedError(
                f"a claim naming the {window.name} window was committed at {commit} ({line}); "
                "deleting it does not give the window's read back -- it had its one read"
            )


def _window_named(content: bytes, *, where: str) -> str:
    """The window a claim file names, or `ReadingAlreadyClaimedError` when it cannot be read as a
    claim (fail closed: an unreadable claim might name the window being asked for)."""
    try:
        claim = json.loads(content)
    except ValueError as error:
        raise ReadingAlreadyClaimedError(
            f"{where} cannot be read as a claim ({error}); a claim that cannot be read might name "
            "this window, so the read is refused"
        ) from error
    window = claim.get("window") if isinstance(claim, dict) else None
    if not isinstance(window, str):
        raise ReadingAlreadyClaimedError(
            f"{where} cannot be read as a claim: it names no window; a claim that cannot be read "
            "might name this window, so the read is refused"
        )
    if window not in WINDOWS:
        # "Validation", " validation", "validation\n" are not another window: a near miss of
        # this one's name is read as unreadable, never as some other window's claim.
        raise ReadingAlreadyClaimedError(
            f"{where} cannot be read as a claim: it names {window!r}, which is not a window "
            f"({sorted(WINDOWS)}); a claim that cannot be read might name this window, so the "
            "read is refused"
        )
    return window


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
    """The result as text, in the order this function appends it:

    - the read-once banner (from the registration, through `read_once_banner`);
    - the indented registration line, `the <role> reading registered for <issue>`, and the
      indented line naming the claim files that witness this window's one read and the refs to
      keep that commit in;
    - the knowledge clock, `_KNOWLEDGE_LINE` -- the registered `as_of` and, verbatim, the
      registrant's `as_of_reason` for why that is the knowledge the reading is under -- only
      when the registration carries both;
    - for a result with no `plan` (the measurement was refused), the `binding:` line naming
      what that refusal was bound to;
    - the `error` line when the measurement was refused, saying the refused read spent the one
      read;
    - the stop rule when the result carries one: the rule as written before the read, one
      indented `  <metric>: n ..., mean ..., at or below ...` line per clause, the
      `stop rule <outcome>` line (with its reason when it has one), and the conclusion's
      wording; or, when it carries none, the one line saying a reading with no stop rule
      decides nothing;
    - for a real measurement (`plan` present), `p7_diagnostics.diagnostic_lines` after its own
      banner -- the banner printed first above is that function's line 0.
    """
    window = WINDOWS[body["window"]]
    lines = [
        read_once_banner(
            body["reading"],
            window.name,
            f"{window.name} window {window.first.isoformat()}..{window.last.isoformat()}",
        ),
        f"  the {body['role']} reading registered for {body['issue']}",
        f"  this window's one read is witnessed by every claim file naming it in "
        f"{READING_DIRECTORY.as_posix()}: commit {body['reading']}.claim.json and "
        f"{window.name}{WINDOW_CLAIM_SUFFIX}, and keep that commit in this repository's refs, or "
        "another clone or worktree can read the window again",
    ]
    reason = body.get("as_of_reason")
    config_form = body.get("config")
    as_of = config_form.get("as_of") if isinstance(config_form, Mapping) else None
    if reason is not None and as_of is not None:
        lines.append("  " + _KNOWLEDGE_LINE.format(as_of=as_of, reason=reason))
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
