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
written with), 3 (refused -- before the claim or after it: look for a claim file) and 4
(`ExpectedPeriodsMismatch`: the calendar's count is not the prior -- stop and report it).

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
   it was at the registration's `code_commit`. `scripts/daily_selection.py` and
   `scripts/forward_report.py` are not bound here, by ruling (`V2-P7-024`, batch 4): no module this
   read imports or runs is either of them (`test_no_research_script_imports_a_daily_script`), so a
   later change to them cannot change a number this read wrote, and must not void its claim. The
   daily registration binds them; the tie between this reading and that registration is the
   configuration, which `V2-P7-006`'s registration test holds.
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

## The optional A/B section (`V2-P7-027`, `V2-P7-028`)

A registration may carry `ab_tests` (`AB_TESTS_SCHEMA`), one frozen section that makes the same
one read also decide plan A's entry veto and plan B's two limit-event factors. It is written only
when asked for (`adopted_ab_tests`, `p7_read.py register --ab-tests`), so a registration without
it has exactly the bytes and the behaviour it had before -- the one committed `v2-p7-024`
registration is the test of that.

`register_reading` and `read_once` validate the section through the one parser
`_registration_of` -> `_ab_tests_of`, so a hand-written section is held to what the writer writes.
The parser also stores every threshold on `AbTests`, and the measure applies them from the
registration body, never from the module's `AB_*` constants: the window, `h20`, `stride` 20,
`q`, the 60-observation floor, the veto rule and lookback, and each factor's declared direction
(which must be the factor definition's own, so it cannot be flipped after the read).

The measure wraps the engine's own score and label sources as it runs, so the A/B tests read the
cross sections, the labels and the sampled sessions the diagnostics document already records --
`p7_diagnostics.sampled_sessions`, the non-overlapping sample `summarize` reads. Plan A's test is
the mean h20 return of the survivors `market_bearish_5/v1` vetoes (each of the five sessions read
at its own 16:30 publication instant, `panel_limit_events.limit_events_on`) minus the mean over
all survivors, over the eligible scope; plan B's two tests are h20 rank ICs of the stored raw
factor, oriented by the declared direction, read through `load_factor_observations`. Every
two-sided p is `outcome_statistics.sign_flip_test`; the one-sided reading is
`grid.one_sided_p_value` in the registered direction; the mean, sd and t are
`decile_readout.summarize_series`. The measured tests face Benjamini-Hochberg
(`control_false_discovery_rate`, declared as independent-or-positively-dependent) at the
registered rate and with the **declared family of all three registered tests** (R-R1's literal
"across all three", and the controller's fix-round ruling): a test below the observation floor or
the p-value's minimum sample is `not_measurable`, is withheld from the reported verdicts -- it is
not a hypothesis whose q was large -- but it stays in the family denominator, so `m` never falls
to a data-dependent count. Not shrinking the family is the conservative reading. Each test's
outcome is one of `candidate`, `no_evidence` or `not_measurable`; the result's text prints one
line per test, each carrying the exploratory candidate wording, and the family line states the
declared family size, the reported count and the withheld count.

The A/B measurement is **isolated from the G-0 document** (`_measured_ab_tests`): the engine's
readings are already measured when it runs, so any exception in the A/B section (a per-session
limit-events read a restated row refuses, a factor manifest check, an empty plan, an empty
cross-section) writes a refused block -- every test `not_measurable`, the declared family, no
verdict, and a redacted `measurement refused: <Type>: <disclosable message>` reason -- while
`result.json` and `result.txt` carry the full G-0 secondary diagnostics as usual. A refusal in
the section may not cost the spent window the reading the claim was written for. The block is
held to the registration again before the result is written (`_require_ab_result`): frozen
values, outcome categories, the p-value/rank/rejection of each measured test, the candidate rule,
and the declared family with its reported and withheld tests; a mismatched block is recorded as
refused rather than discarding the measured G-0 document (`read_once`).

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
import statistics
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Final, Literal, get_args
from zoneinfo import ZoneInfo

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

from openalpha_cn.backtest.decile_readout import DECILES, read_cross_section, summarize_series
from openalpha_cn.backtest.factor_ic import TIER_ADMITTED_CODES
from openalpha_cn.backtest.multiple_testing import (
    HypothesisTest,
    MultipleTestingRequest,
    control_false_discovery_rate,
)
from openalpha_cn.backtest.outcome_statistics import (
    MINIMUM_INTERVAL_SAMPLE_SIZE,
    RandomizationTest,
    sign_flip_test,
)
from openalpha_cn.domain.adjustment import ADJ_FACTOR_DATASET
from openalpha_cn.domain.daily_prices import DAILY_DATASET
from openalpha_cn.domain.factor import FactorDirection, FactorError
from openalpha_cn.domain.factor_transform import FactorTransformError
from openalpha_cn.domain.horizon import MAX_HORIZON_COUNT
from openalpha_cn.domain.limit_events import LimitEvent
from openalpha_cn.domain.name_history import NAMECHANGE_DATASET
from openalpha_cn.domain.price_limits import PRICE_LIMIT_DATASET, SUSPENSION_DATASET
from openalpha_cn.domain.stock_universe import STOCK_BASIC_DATASET
from openalpha_cn.domain.survivor_book import SurvivorBookConfig
from openalpha_cn.domain.survivor_veto import (
    VETO_LOOKBACK_SESSIONS,
    VETO_RULE_ID,
    vetoed_codes,
)
from openalpha_cn.domain.trading_calendar import (
    TRADING_CALENDAR_DATASET,
    CalendarHorizonError,
    TradingCalendar,
    TradingCalendarError,
)
from openalpha_cn.panel.catalog import DEFAULT_DATE_TIMEZONE, PanelStorageError
from openalpha_cn.panel.store import PanelStore
from openalpha_cn.panel_factors import (
    FACTOR_DEFINITIONS,
    FACTOR_TRANSFORMS,
    factor_observation_dataset,
    factor_transform_manifest_dataset,
    load_factor_observations,
    processed_factor_dataset,
)
from openalpha_cn.panel_ingest import load_trading_calendar, session_publication_instant
from openalpha_cn.panel_limit_events import limit_events_on
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

# --- the A/B reading's registration section (`V2-P7-027`, `V2-P7-028`) ---------------------------

AB_TESTS_SCHEMA: Final = "p7-ab-tests/v1"
"""The optional `ab_tests` registration section's version. A registration without the section is
the `v2-p7-024` reading unchanged: the section is added only when asked for, so an old
registration's bytes and every decision `read_once` makes about it are identical."""

VETO_TEST_ID: Final = "market_bearish_5_vs_all_survivors"
"""Plan A's test: on each sampled session, the mean h20 return of the survivors
`market_bearish_5/v1` vetoes, minus the mean over all survivors."""

AB_CANDIDATE: Final = "candidate"
AB_NO_EVIDENCE: Final = "no_evidence"
AB_NOT_MEASURABLE: Final = "not_measurable"
AB_OUTCOMES: Final = (AB_CANDIDATE, AB_NO_EVIDENCE, AB_NOT_MEASURABLE)
"""The three conclusions a test of the reading may reach; each test reaches exactly one."""

AB_DEPENDENCE: Final = "independent-or-positively-dependent"
"""The Benjamini-Hochberg dependence declaration, stored on the document. R-R1 says BH, not
Benjamini-Yekutieli; the section does not make it a choice a later reader can flip."""

AB_SIGN_FLIP_TEST: Final = "sign_flip_test/two-sided-halved-one-sided"
"""The p-value procedure's name on every hypothesis handed to the FDR control: the two-sided
sign-flip randomization test of a series' mean about zero (`backtest.outcome_statistics`), read
one-sided in the pre-registered direction (`grid.one_sided_p_value`)."""

AB_FALSE_DISCOVERY_RATE: Final = 0.10
AB_MINIMUM_VETOED_OBSERVATIONS: Final = 60
AB_HORIZON: Final = 20
AB_STRIDE: Final = 20
"""R-R1's frozen numbers, written into the adopted section. `read_once` never reads them from
here: every threshold comes from the registration body, so a registration that froze other
numbers would be read as it froze them."""

AB_LIMIT_EVENT_FACTORS: Final = (
    ("limit_up_count_20/v1", "lower_is_better"),
    ("broken_board_count_20/v1", "lower_is_better"),
)
"""R-B1's two factors and their declared direction, written into the adopted section by their
`key/vN` -- the spelling every registration of this repository uses for a factor
(`book.composite_factors` included), so the version the read tested is on its face."""

AB_BLOCK_WORDING: Final = (
    "the A/B tests are exploratory candidates (探索性候选), never confirmatory evidence: plan A's "
    "veto and plan B's two factor tests were pre-registered together on one spent holdout window, "
    "and a rejection here is a candidate for a later pre-registered follow-up"
)
AB_CANDIDATE_WORDING: Final = (
    "an exploratory candidate (探索性候选): the registered direction holds and Benjamini-Hochberg "
    "leaves it rejected at the registered rate; never confirmatory evidence"
)
AB_NO_EVIDENCE_WORDING: Final = (
    "no evidence in this reading: it would be an exploratory candidate only if the registered "
    "direction held with a Benjamini-Hochberg-adjusted one-sided p at or below the registered "
    "rate; never confirmatory evidence"
)
AB_NOT_MEASURABLE_WORDING: Final = (
    "not measurable: an exploratory candidate cannot be read from it, and this is not a reading "
    "of no evidence; never confirmatory evidence"
)

_AB_KEYS: Final = frozenset(
    {
        "schema",
        "window",
        "issues",
        "horizon",
        "stride",
        "false_discovery_rate",
        "minimum_vetoed_observations",
        "veto",
        "factor_tests",
    }
)
_AB_VETO_KEYS: Final = frozenset({"rule", "lookback_sessions"})
_AB_FACTOR_KEYS: Final = frozenset({"factor", "direction"})
_AB_RESULT_KEYS: Final = frozenset(
    {
        "schema",
        "window",
        "horizon",
        "stride",
        "false_discovery_rate",
        "minimum_vetoed_observations",
        "dependence",
        "family_size",
        "family",
        "withheld",
        "wording",
        "tests",
    }
)
_AB_TEST_KEYS: Final = frozenset(
    {
        "id",
        "kind",
        "factor",
        "direction",
        "alternative",
        "horizon",
        "stride",
        "sampled_sessions",
        "measured_periods",
        "mean",
        "sd",
        "t",
        "p_two_sided",
        "p_one_sided",
        "p_adjusted",
        "exact",
        "sign_patterns",
        "bh_rank",
        "rejected",
        "outcome",
        "reason",
        "wording",
        "vetoed_observations",
        "minimum_vetoed_observations",
    }
)
_EXCHANGE_ZONE: Final = ZoneInfo(DEFAULT_DATE_TIMEZONE)
"""The zone a factor observation's `as_of` is dated in for a session: `factor_view.FACTOR_DATE_ZONE`
(`panel.catalog.DEFAULT_DATE_TIMEZONE`), which is what makes a 16:30 build belong to its session."""


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
    ab_tests: AbTests | None = None
    """The optional A/B section; `None` for a registration that carries none, which is every
    registration written before the section existed (`v2-p7-024`'s included)."""


@dataclass(frozen=True, slots=True, kw_only=True)
class _Committed:
    """The registration as `HEAD` holds it: its bytes (read once), its body, the commit that last
    touched it and its path relative to the repository root."""

    content: bytes
    body: Mapping[str, Any]
    commit: str
    relative: str


# --- the A/B section: what it says and what it must be -------------------------------------------


@dataclass(frozen=True, slots=True, kw_only=True)
class AbVetoSpec:
    """Plan A's frozen test: the rule, its window and the observation floor."""

    rule: str
    lookback_sessions: int
    minimum_observations: int


@dataclass(frozen=True, slots=True, kw_only=True)
class AbFactorSpec:
    """Plan B's one factor test: the factor by key and the direction it was declared with."""

    factor: str
    direction: str


@dataclass(frozen=True, slots=True, kw_only=True)
class AbEntry:
    """One registered test, in registration order: A first, then B's factors."""

    test_id: str
    kind: Literal["veto_difference", "factor_rank_ic"]
    alternative: Literal["mean_below_zero", "oriented_mean_above_zero"]
    factor: str | None
    direction: str | None


@dataclass(frozen=True, slots=True, kw_only=True)
class AbTests:
    """The `ab_tests` registration section, read: every threshold is the registration's own.

    A's alternative is fixed by the schema -- the vetoed survivors' mean h20 return is below the
    all-survivors mean -- and B's is `higher` on the direction-oriented rank IC. B's orientation
    is the registered `direction`, which `_ab_tests_of` holds to the factor definition's declared
    one, so a later branch cannot flip it after the read.
    """

    window: str
    issues: tuple[str, ...]
    horizon: int
    stride: int
    false_discovery_rate: float
    veto: AbVetoSpec
    factor_tests: tuple[AbFactorSpec, ...]

    @property
    def entries(self) -> tuple[AbEntry, ...]:
        return (
            AbEntry(
                test_id=VETO_TEST_ID,
                kind="veto_difference",
                alternative="mean_below_zero",
                factor=None,
                direction=None,
            ),
            *(
                AbEntry(
                    test_id=item.factor,
                    kind="factor_rank_ic",
                    alternative="oriented_mean_above_zero",
                    factor=item.factor,
                    direction=item.direction,
                )
                for item in self.factor_tests
            ),
        )

    @property
    def ids(self) -> tuple[str, ...]:
        return tuple(entry.test_id for entry in self.entries)


def adopted_ab_tests() -> dict[str, Any]:
    """R-R1's frozen `ab_tests` section, for the one merged read of the second-use window.

    The window, `h20`, `stride` 20, `q = 0.10`, the 60-observation floor, the veto rule, and B's
    two factor keys with their declared `lower_is_better` direction. This function writes a
    registration; the reader takes every one of these numbers from the committed body, never
    from this function, so a registration written with other numbers would be read as it froze
    them. The `issues` list names both issues this one reading is for (`V2-P7-027`'s veto and
    `V2-P7-028`'s factors; the registration's own `issue` is one id because that field is one).
    """
    return {
        "schema": AB_TESTS_SCHEMA,
        "window": "second_use",
        "issues": ["V2-P7-027", "V2-P7-028"],
        "horizon": AB_HORIZON,
        "stride": AB_STRIDE,
        "false_discovery_rate": AB_FALSE_DISCOVERY_RATE,
        "minimum_vetoed_observations": AB_MINIMUM_VETOED_OBSERVATIONS,
        "veto": {"rule": VETO_RULE_ID, "lookback_sessions": VETO_LOOKBACK_SESSIONS},
        "factor_tests": [
            {"factor": factor, "direction": direction}
            for factor, direction in AB_LIMIT_EVENT_FACTORS
        ],
    }


def ab_test_ids(form: Mapping[str, Any]) -> tuple[str, ...]:
    """The test ids an `ab_tests` body names, in registration order: A, then B's factors."""
    veto = form.get("veto")
    rule = veto.get("rule") if isinstance(veto, Mapping) else None
    if rule != VETO_RULE_ID:
        raise ReadingError(
            f"the ab_tests veto rule {rule!r} is not {VETO_RULE_ID!r}; no test ids to name"
        )
    return (
        VETO_TEST_ID,
        *(str(item["factor"]) for item in form["factor_tests"] if isinstance(item, Mapping)),
    )


def _ab_tests_of(form: object, settings: _Settings, window: DiagnosticWindow) -> AbTests:
    """The `ab_tests` section held to its schema and to R-R1's frozen shape; `ReadingError`.

    This is the one parser `register_reading` and `read_once` share (the latter through
    `_registration_of`), so a hand-written section is held to exactly what the writer writes.
    The values that decide the read -- q, the observation floor, h20, stride 20, the window, B's
    directions -- are *read here* and travel on `AbTests`; the reader applies them from there.
    """
    section = _require_keys("ab_tests", form, _AB_KEYS)
    if section["schema"] != AB_TESTS_SCHEMA:
        raise ReadingError(f"the ab_tests section is not a {AB_TESTS_SCHEMA} section")
    name = section["window"]
    if name != window.name:
        raise ReadingError(
            f"the ab_tests section names the {name!r} window and the registration opens the "
            f"{window.name!r} window: the window is frozen at registration and a read cannot "
            "move it"
        )
    if window.name != WINDOWS["second_use"].name:
        raise ReadingError(
            f"{AB_TESTS_SCHEMA} is the second_use read: the A/B tests were ruled for the spent "
            f"holdout window, not {window.name!r}; a hand-written section on another window is "
            "not a shape this schema implements"
        )
    issues = section["issues"]
    if (
        not isinstance(issues, list)
        or not issues
        or len({item for item in issues if isinstance(item, str)}) != len(issues)
        or any(not isinstance(item, str) or not _ISSUE.fullmatch(item) for item in issues)
    ):
        raise ReadingError(
            "the ab_tests section's issues must be a non-empty list of distinct V2 issue ids "
            "(it names both issues the one reading is for)"
        )
    horizon = section["horizon"]
    if type(horizon) is not int or horizon not in settings.explore.horizons:
        raise ReadingError(
            f"the ab_tests horizon {horizon!r} is not among the request's horizons "
            f"{list(settings.explore.horizons)}: a test reads the horizon the request plans"
        )
    stride = section["stride"]
    if type(stride) is not int or stride != settings.explore.stride:
        raise ReadingError(
            f"the ab_tests stride {stride!r} is not the request's stride "
            f"{settings.explore.stride}: the non-overlapping sample is the request's"
        )
    rate = section["false_discovery_rate"]
    if (
        type(rate) not in (int, float)
        or isinstance(rate, bool)
        or not math.isfinite(rate)
        or not 0 < rate < 1
    ):
        raise ReadingError(
            f"the ab_tests false_discovery_rate must be a finite number strictly between 0 and 1, "
            f"not {rate!r}"
        )
    minimum = section["minimum_vetoed_observations"]
    if type(minimum) is not int or minimum < 1:
        raise ReadingError(
            "the ab_tests minimum_vetoed_observations must be a whole number of at least 1, not "
            f"{minimum!r}"
        )
    veto = _require_keys("ab_tests veto", section["veto"], _AB_VETO_KEYS)
    if veto["rule"] != VETO_RULE_ID:
        raise ReadingError(
            f"the ab_tests veto rule {veto['rule']!r} is not {VETO_RULE_ID!r}, the one rule this "
            "schema implements"
        )
    lookback = veto["lookback_sessions"]
    if type(lookback) is not int or lookback != VETO_LOOKBACK_SESSIONS:
        raise ReadingError(
            f"the ab_tests veto lookback_sessions {lookback!r} is not "
            f"{VETO_LOOKBACK_SESSIONS!r}: the rule's window is frozen"
        )
    tests = section["factor_tests"]
    if not isinstance(tests, list) or not tests:
        raise ReadingError("the ab_tests section needs at least one factor test")
    entries: list[AbFactorSpec] = []
    for entry in tests:
        item = _require_keys("ab_tests factor test", entry, _AB_FACTOR_KEYS)
        factor = item["factor"]
        if not isinstance(factor, str) or not factor:
            raise ReadingError(f"the ab_tests factor test names no factor: {factor!r}")
        direction = item["direction"]
        if direction not in get_args(FactorDirection):
            raise ReadingError(
                f"the ab_tests direction {direction!r} of {factor} is not one a factor may be "
                f"declared with ({list(get_args(FactorDirection))})"
            )
        try:
            definition = FACTOR_DEFINITIONS.get(factor)
        except FactorError as error:
            raise ReadingError(
                f"the ab_tests factor {factor!r} is not declared as a factor's `key/vN`; the "
                f"reading can only test a factor the panel plane knows ({error})"
            ) from error
        if definition.direction != direction:
            raise ReadingError(
                f"the ab_tests direction {direction!r} of {factor} is not the factor's declared "
                f"direction {definition.direction!r}: the declared direction is frozen at the "
                "read and is never flipped afterwards"
            )
        if any(item.factor == factor for item in entries):
            raise ReadingError(f"the ab_tests section names {factor} twice")
        entries.append(AbFactorSpec(factor=factor, direction=direction))
    return AbTests(
        window=window.name,
        issues=tuple(issues),
        horizon=horizon,
        stride=stride,
        false_discovery_rate=float(rate),
        veto=AbVetoSpec(
            rule=veto["rule"], lookback_sessions=lookback, minimum_observations=minimum
        ),
        factor_tests=tuple(entries),
    )


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
    ab_tests: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """The registration body `register_reading` writes, before any rule is applied. The command's
    `--dry-run` validates through this and `_registration_of`, so it exercises exactly the body a
    real registration would write instead of a second, drifting one.

    `ab_tests` is the optional A/B section (`adopted_ab_tests` for the merged read). When it is
    `None` the key is not written at all: that is what keeps a registration without the section
    byte-identical to the ones `v2-p7-024` wrote.
    """
    try:
        request_form = grid.to_json_value(dict(request))
        config_form = grid.to_json_value(dict(config))
        ab_form = None if ab_tests is None else grid.to_json_value(dict(ab_tests))
    except grid.ResearchLedgerError as error:
        raise ReadingError(f"the request or the configuration has no JSON form: {error}") from error
    body: dict[str, Any] = {
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
    if ab_form is not None:
        body["ab_tests"] = ab_form
    return body


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
    ab_tests: Mapping[str, Any] | None = None,
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

    `ab_tests` is the optional, versioned A/B section (`AB_TESTS_SCHEMA`); it is validated by the
    same `_registration_of` every other path uses, written only when given, and absent from a
    registration that does not ask for it -- so the old bytes are the old bytes.
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
        ab_tests=ab_tests,
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
    _require_keys("registration", body, _BODY_KEYS, frozenset({"ab_tests"}))
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
    ab_tests = (
        None if body.get("ab_tests") is None else _ab_tests_of(body["ab_tests"], settings, window)
    )
    if ab_tests is not None and issue not in ab_tests.issues:
        raise ReadingError(
            f"the ab_tests issues {list(ab_tests.issues)} do not name the registration's issue "
            f"{issue!r}: the one reading must name both issues its tests are for"
        )
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
    return _Registration(
        reading=reading,
        window=window,
        stop_rule=rule,
        settings=settings,
        ab_tests=ab_tests,
    )


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


def _require_keys(
    what: str,
    value: object,
    keys: frozenset[str],
    optional: frozenset[str] = frozenset(),
) -> Mapping[str, Any]:
    """`value` as a JSON object holding every key of `keys` and no key outside `keys | optional`.

    The optional set is for a key a registration may carry and an older one does not (the
    `ab_tests` section): it must be admitted when present and must not be required when absent,
    which is the one shape hard rule 3's "old bytes stay readable" takes for this hand-parsed
    body.
    """
    if not isinstance(value, Mapping):
        raise ReadingError(f"the {what} must be a JSON object, not {value!r}")
    missing = sorted(keys - set(value))
    if missing:
        raise ReadingError(f"the {what} lacks {missing}")
    extra = sorted(set(value) - keys - optional)
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
    store: PanelStore,
    *,
    window: DiagnosticWindow,
    datasets: Sequence[str],
    year_before: Sequence[str] = (),
) -> tuple[str, ...]:
    """Each dataset of `datasets` that lists no partition for a year the read opens of it, by name
    with the years it lacks; `()` when every one lists them all. The years are the window's, and
    for a dataset of `YEAR_BEFORE_DATASETS` -- plus `year_before`, the caller's extra reading of
    the year before -- the year before it as well: the calendar, the label reader over
    `label_years` and the facts day's history read it. With the A/B section, `stk_limit` joins
    `year_before`: the veto's five-session window for the window's first signal day reaches into
    the year before, so a missing `stk_limit` partition there is named before the claim instead of
    met by the measurement after it. The factor datasets are read for signal days only, so a store
    whose factor builds begin with the window is not refused for the year before. Only
    `registered_years` is asked: no partition is opened, so a listed year may still be
    incomplete."""
    before = frozenset(YEAR_BEFORE_DATASETS) | frozenset(year_before)
    problems: list[str] = []
    for dataset in datasets:
        first = window.first.year - (1 if dataset in before else 0)
        years = range(first, window.ceiling_year + 1)
        held = set(store.registered_years(dataset))
        missing = [year for year in years if year not in held]
        if missing:
            problems.append(
                f"{dataset} lists no partition for {missing} of the {window.name} window "
                f"{window.first}..{window.last}"
            )
    return tuple(problems)


def _preflight_datasets(
    book: SurvivorBookConfig, ab_tests: AbTests | None = None
) -> tuple[str, ...]:
    """`PREFLIGHT_DATASETS` and, for each composite factor, its processed observations and its
    transform manifests (the two datasets `load_processed_factor_observations` opens); and, for a
    reading with the A/B section, each tested factor's raw observations, so a factor build the
    reading will need is checked for the window's years before the claim rather than met after it.
    """
    definitions = [FACTOR_DEFINITIONS.get(factor) for factor in book.composite_factors]
    tested = (
        []
        if ab_tests is None
        else [FACTOR_DEFINITIONS.get(item.factor) for item in ab_tests.factor_tests]
    )
    return (
        *PREFLIGHT_DATASETS,
        *(processed_factor_dataset(definition) for definition in definitions),
        *(factor_transform_manifest_dataset(definition) for definition in definitions),
        *(factor_observation_dataset(definition) for definition in tested),
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
        try:
            settings = _settings_of(body["request"], body["config"], window)
            ab_tests = (
                None
                if body.get("ab_tests") is None
                else _ab_tests_of(body["ab_tests"], settings, window)
            )
        except ReadingError as error:
            return (f"the registration's A/B tests cannot be read: {error}",)
        store = capped_panel_store(runtime_dir, window)
        problems = preflight_problems(
            store,
            window=window,
            datasets=_preflight_datasets(settings.explore.book, ab_tests),
            year_before=(PRICE_LIMIT_DATASET,) if ab_tests is not None else (),
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


# --- the A/B tests: the sample, the arithmetic and the document ----------------------------------


@dataclass(frozen=True, slots=True, kw_only=True)
class AbTestSeries:
    """One registered test's non-overlapping sample, as the measure collected it.

    `values` holds the measured periods -- one per sampled session that carried a value -- in
    ascending order; `sampled_sessions` is the plan's full sampled count, so a report can say how
    many periods were planned and how many were read. `vetoed_observations` is plan A's total
    number of labelled vetoed-survivor observations across the sample (what the 60-observation
    floor counts) and is `None` for a factor test. Nothing here decides an outcome: the thresholds
    are the registration's (`AbTests`) and the arithmetic is `evaluate_ab_tests`.
    """

    test_id: str
    kind: Literal["veto_difference", "factor_rank_ic"]
    factor: str | None
    sampled_sessions: int
    values: tuple[tuple[date, float], ...]
    vetoed_observations: int | None = None

    def __post_init__(self) -> None:
        # The id is checked against the registration by `evaluate_ab_tests`; this is only the
        # shape a series may have, so a caller cannot smuggle a second kind of number in.
        if not isinstance(self.test_id, str) or not self.test_id:
            raise ReadingError("an ab test series names no test")
        if self.kind not in ("veto_difference", "factor_rank_ic"):
            raise ReadingError(
                f"the ab test {self.test_id!r} has no kind the reading knows: {self.kind!r}"
            )
        if (self.kind == "factor_rank_ic") != (self.factor is not None):
            raise ReadingError(
                f"the ab test {self.test_id!r} names a factor exactly when it tests a factor"
            )
        if type(self.sampled_sessions) is not int or self.sampled_sessions < 1:
            raise ReadingError(
                f"the ab test {self.test_id!r}'s sampled_sessions must be a whole number of at "
                f"least 1, not {self.sampled_sessions!r}"
            )
        if self.kind == "veto_difference":
            if type(self.vetoed_observations) is not int or self.vetoed_observations < 0:
                raise ReadingError(
                    f"the ab test {self.test_id!r} must carry its vetoed observation count as a "
                    f"whole number of at least 0, not {self.vetoed_observations!r}"
                )
        elif self.vetoed_observations is not None:
            raise ReadingError(
                f"the ab test {self.test_id!r} tests a factor and carries no vetoed observation "
                f"count, not {self.vetoed_observations!r}"
            )
        sessions = [day for day, _ in self.values]
        if sessions != sorted(sessions) or len(set(sessions)) != len(sessions):
            raise ReadingError(
                f"the ab test {self.test_id!r}'s measured periods must be in strictly ascending "
                "date order, one per sampled session"
            )
        if len(sessions) > self.sampled_sessions:
            raise ReadingError(
                f"the ab test {self.test_id!r} measured {len(sessions)} period(s) of "
                f"{self.sampled_sessions} sampled: a sample cannot hold more than was planned"
            )
        for _, value in self.values:
            if not isinstance(value, int | float) or isinstance(value, bool):
                raise ReadingError(f"the ab test {self.test_id!r}'s period values must be numbers")
            if not math.isfinite(value):
                raise ReadingError(f"the ab test {self.test_id!r}'s period values must be finite")


def evaluate_ab_tests(spec: AbTests, series: Sequence[AbTestSeries]) -> dict[str, Any]:
    """R-R1's three tests applied as the registration froze them, then Benjamini-Hochberg.

    Each test's periods are summarised by `decile_readout.summarize_series` (the engine's own
    mean, sample sd and t over the non-overlapping sample); its two-sided p is
    `outcome_statistics.sign_flip_test`, the repository's randomization test of a mean about zero,
    and its one-sided p is `grid.one_sided_p_value` read in the pre-registered direction. The
    measured tests face `control_false_discovery_rate` at the registered rate, with the family
    size every document in this repository stores: the number of tests that produced a p-value.
    `not_measurable` tests stay out of the *reported verdicts* -- they are not hypotheses whose
    q-value was large -- but they stay in the **declared family**, which R-R1 fixes at all three
    tests (`len(spec.entries)`), so the family size never shrinks to a data-dependent count. The
    block names the family size and lists the withheld tests.

    A test is `candidate` when the FDR procedure rejects it **and** the registered direction holds;
    a measured test that is not a candidate is `no_evidence`. A test is `not_measurable` when its
    series holds fewer than `MINIMUM_INTERVAL_SAMPLE_SIZE` periods, or (plan A) when the vetoed
    observations total is below the registered floor; that is its own conclusion, never a reading
    of no evidence.
    """
    entries = spec.entries
    collected = tuple(series)
    if len(collected) != len(entries) or any(
        item.test_id != entry.test_id for item, entry in zip(collected, entries, strict=True)
    ):
        raise ReadingError(
            "the ab_tests series must carry each registered test exactly once, in registration "
            f"order ({list(spec.ids)}); got {[item.test_id for item in collected]!r}"
        )
    for item, entry in zip(collected, entries, strict=True):
        if item.kind != entry.kind or item.factor != entry.factor:
            raise ReadingError(
                f"the ab test series for {item.test_id!r} is not that registered test "
                f"({entry.kind}, factor {entry.factor!r})"
            )
    pending: list[dict[str, Any]] = []
    for item, entry in zip(collected, entries, strict=True):
        summary = summarize_series(list(item.values))
        reason: str | None = None
        if entry.kind == "veto_difference" and (
            item.vetoed_observations is not None
            and item.vetoed_observations < spec.veto.minimum_observations
        ):
            reason = (
                f"{item.vetoed_observations} vetoed survivor observation(s), fewer than the "
                f"registered minimum {spec.veto.minimum_observations}: the test cannot be measured"
            )
        if reason is None and summary.n < MINIMUM_INTERVAL_SAMPLE_SIZE:
            reason = (
                f"{summary.n} measured non-overlapping period(s), fewer than the "
                f"{MINIMUM_INTERVAL_SAMPLE_SIZE} a sign-flip p-value needs: the test cannot be "
                "measured"
            )
        record: dict[str, Any] = {
            "entry": entry,
            "item": item,
            "summary": summary,
            "reason": reason,
        }
        if reason is None:
            randomised = sign_flip_test(
                tuple(value for _, value in item.values),
                bootstrap_samples=grid.PROTOCOL_BOOTSTRAP_SAMPLES,
                random_seed=grid.PROTOCOL_RANDOM_SEED,
            )
            mean = summary.mean
            if mean is None:  # pragma: no cover - a measured series always has a mean
                raise ReadingError(f"the ab test {item.test_id!r} measured no mean")
            observed = -mean if entry.alternative == "mean_below_zero" else mean
            record["two_sided"] = randomised
            record["one_sided"] = grid.one_sided_p_value(randomised.p_value, observed)
        pending.append(record)
    measurable = [record for record in pending if record["reason"] is None]
    withheld = [record["entry"].test_id for record in pending if record["reason"] is not None]
    verdicts: dict[str, Any] = {}
    family: list[str] = []
    if measurable:
        # R-R1's family is **all three declared tests**, not the measured rows: the plan rules
        # Benjamini-Hochberg "across all three", and `m` must not fall to a data-dependent count
        # (the 60-observation floor and the period counts are data). The tests that produced no
        # p-value are withheld from the reported verdicts -- they are not hypotheses whose q was
        # large -- but they stay in the denominator, which is what `withheld_hypotheses` records.
        report = control_false_discovery_rate(
            MultipleTestingRequest(
                tests=tuple(
                    HypothesisTest(
                        hypothesis_id=record["entry"].test_id,
                        p_value=record["one_sided"],
                        test=AB_SIGN_FLIP_TEST,
                    )
                    for record in measurable
                ),
                family_size=len(entries),
                false_discovery_rate=spec.false_discovery_rate,
                dependence=AB_DEPENDENCE,
            )
        )
        verdicts = {verdict.hypothesis_id: verdict for verdict in report.verdicts}
        family = [
            verdict.hypothesis_id
            for verdict in sorted(report.verdicts, key=lambda verdict: verdict.rank)
        ]
    tests: list[dict[str, Any]] = []
    for record in pending:
        stored_entry: AbEntry = record["entry"]
        stored_item: AbTestSeries = record["item"]
        stored_summary = record["summary"]
        two_sided: RandomizationTest | None = record.get("two_sided")
        one_sided: float | None = record.get("one_sided")
        verdict = verdicts.get(stored_entry.test_id)
        test_reason: str | None
        if record["reason"] is not None:
            outcome = AB_NOT_MEASURABLE
            wording = AB_NOT_MEASURABLE_WORDING
            rejected: bool | None = None
            adjusted = rank = None
            test_reason = record["reason"]
        else:
            assert verdict is not None and two_sided is not None and one_sided is not None
            mean = stored_summary.mean
            assert mean is not None
            holds = mean < 0 if stored_entry.alternative == "mean_below_zero" else mean > 0
            rejected = verdict.rejected
            adjusted = verdict.q_value
            rank = verdict.rank
            test_reason = None
            if rejected and holds:
                outcome = AB_CANDIDATE
                wording = AB_CANDIDATE_WORDING
            else:
                outcome = AB_NO_EVIDENCE
                wording = AB_NO_EVIDENCE_WORDING
        tests.append(
            {
                "id": stored_entry.test_id,
                "kind": stored_entry.kind,
                "factor": stored_entry.factor,
                "direction": stored_entry.direction,
                "alternative": stored_entry.alternative,
                "horizon": spec.horizon,
                "stride": spec.stride,
                "sampled_sessions": stored_item.sampled_sessions,
                "measured_periods": stored_summary.n,
                "mean": stored_summary.mean,
                "sd": stored_summary.sd,
                "t": stored_summary.t,
                "p_two_sided": None if two_sided is None else two_sided.p_value,
                "p_one_sided": one_sided,
                "p_adjusted": adjusted,
                "exact": None if two_sided is None else two_sided.exact,
                "sign_patterns": None if two_sided is None else two_sided.sign_patterns,
                "bh_rank": rank,
                "rejected": rejected,
                "outcome": outcome,
                "reason": test_reason,
                "wording": wording,
                "vetoed_observations": stored_item.vetoed_observations,
                "minimum_vetoed_observations": (
                    spec.veto.minimum_observations
                    if stored_entry.kind == "veto_difference"
                    else None
                ),
            }
        )
    return {
        "schema": AB_TESTS_SCHEMA,
        "window": spec.window,
        "horizon": spec.horizon,
        "stride": spec.stride,
        "false_discovery_rate": spec.false_discovery_rate,
        "minimum_vetoed_observations": spec.veto.minimum_observations,
        "dependence": AB_DEPENDENCE,
        "family_size": len(entries),
        "family": family,
        "withheld": withheld,
        "wording": AB_BLOCK_WORDING,
        "tests": tests,
    }


def _finite_number(value: object) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool) and math.isfinite(value)


def _require_ab_result(spec: AbTests, document: Mapping[str, Any]) -> dict[str, Any]:
    """The `ab_tests` block of a measured document, held to the registration that claimed it.

    The block is written by the measure, but the measure is not trusted blindly: this is the same
    division `_measured` draws for the document's kind and binding. Every frozen value is read
    back against the registration; every outcome must be one of the three; a `not_measurable` test
    carries no p-value and no rank; a measured test carries both and its `candidate`/`no_evidence`
    outcome must agree with its own rejection and mean; the family and its size must be the
    measured tests, ranked by the FDR procedure's own order. A document that fails any of it is
    refused with the claim standing -- no result is written.
    """
    try:
        block = _require_keys("ab_tests result", document.get("ab_tests"), _AB_RESULT_KEYS)
    except ReadingError as error:
        raise ReadingConfigurationError(
            f"the registration carries ab_tests and the measure's block is not one: {error}"
        ) from error
    expected = {
        "schema": AB_TESTS_SCHEMA,
        "window": spec.window,
        "horizon": spec.horizon,
        "stride": spec.stride,
        "false_discovery_rate": spec.false_discovery_rate,
        "minimum_vetoed_observations": spec.veto.minimum_observations,
        "dependence": AB_DEPENDENCE,
    }
    for key, wanted in expected.items():
        if block[key] != wanted:
            raise ReadingConfigurationError(
                f"the measure's ab_tests {key} is {block[key]!r}, not the registration's "
                f"{wanted!r}; the claim stands, the read is spent, and no result is written"
            )
    if not isinstance(block["wording"], str) or "exploratory candidate" not in block["wording"]:
        raise ReadingConfigurationError(
            "the measure's ab_tests wording does not call its conclusions exploratory candidates"
        )
    tests = block["tests"]
    if not isinstance(tests, list) or len(tests) != len(spec.entries):
        raise ReadingConfigurationError(
            f"the measure's ab_tests carries {len(tests) if isinstance(tests, list) else tests!r} "
            f"test(s) and the registration {len(spec.entries)}"
        )
    measured_ids: list[str] = []
    withheld: list[str] = []
    ranks: dict[str, int] = {}
    for entry, raw in zip(spec.entries, tests, strict=True):
        try:
            item = _require_keys("ab_tests result test", raw, _AB_TEST_KEYS)
        except ReadingError as error:
            raise ReadingConfigurationError(str(error)) from error
        fixed = {
            "id": entry.test_id,
            "kind": entry.kind,
            "factor": entry.factor,
            "direction": entry.direction,
            "alternative": entry.alternative,
            "horizon": spec.horizon,
            "stride": spec.stride,
        }
        for key, wanted in fixed.items():
            if item[key] != wanted:
                raise ReadingConfigurationError(
                    f"the measure's ab_tests test {entry.test_id!r} carries {key} "
                    f"{item[key]!r}, not the registration's {wanted!r}"
                )
        if not isinstance(item["wording"], str) or "exploratory candidate" not in item["wording"]:
            raise ReadingConfigurationError(
                f"the measure's ab_tests test {entry.test_id!r} does not call its conclusion an "
                "exploratory candidate"
            )
        if not isinstance(item["reason"], str | None):
            raise ReadingConfigurationError(
                f"the measure's ab_tests test {entry.test_id!r} carries a non-text reason"
            )
        sampled, measured = item["sampled_sessions"], item["measured_periods"]
        if type(sampled) is not int or type(measured) is not int or not 0 <= measured <= sampled:
            raise ReadingConfigurationError(
                f"the measure's ab_tests test {entry.test_id!r} measured {measured!r} of "
                f"{sampled!r} sampled period(s)"
            )
        if entry.kind == "veto_difference":
            if type(item["vetoed_observations"]) is not int or item["vetoed_observations"] < 0:
                raise ReadingConfigurationError(
                    f"the measure's ab_tests test {entry.test_id!r} carries no vetoed "
                    "observation count"
                )
        elif item["vetoed_observations"] is not None:
            raise ReadingConfigurationError(
                f"the measure's ab_tests test {entry.test_id!r} tests a factor and carries a "
                "vetoed observation count"
            )
        outcome = item["outcome"]
        if outcome not in AB_OUTCOMES:
            raise ReadingConfigurationError(
                f"the measure's ab_tests test {entry.test_id!r} has outcome {outcome!r}, not one "
                f"of {list(AB_OUTCOMES)}"
            )
        if outcome == AB_NOT_MEASURABLE:
            if any(
                item[key] is not None
                for key in ("p_two_sided", "p_one_sided", "p_adjusted", "bh_rank", "rejected")
            ):
                raise ReadingConfigurationError(
                    f"the measure's ab_tests test {entry.test_id!r} is not measurable and carries "
                    "a p-value, rank or rejection"
                )
            if not item["reason"]:
                raise ReadingConfigurationError(
                    f"the measure's ab_tests test {entry.test_id!r} is not measurable and says "
                    "no reason"
                )
            withheld.append(entry.test_id)
            continue
        measured_ids.append(entry.test_id)
        mean = item["mean"]
        if not _finite_number(mean):
            raise ReadingConfigurationError(
                f"the measure's ab_tests test {entry.test_id!r} is measured and has no finite mean"
            )
        for key in ("p_two_sided", "p_one_sided", "p_adjusted"):
            value = item[key]
            if not _finite_number(value) or not 0 <= value <= 1:
                raise ReadingConfigurationError(
                    f"the measure's ab_tests test {entry.test_id!r} carries {key} {value!r}, not a "
                    "probability"
                )
        two, one = item["p_two_sided"], item["p_one_sided"]
        assert isinstance(two, int | float) and isinstance(one, int | float)
        if not math.isclose(min(one, 1 - one), two / 2, rel_tol=0.0, abs_tol=1e-9):
            raise ReadingConfigurationError(
                f"the measure's ab_tests test {entry.test_id!r} carries a one-sided p {one!r} that "
                f"is not the two-sided {two!r} halved in the registered direction"
            )
        rank = item["bh_rank"]
        if type(rank) is not int or rank < 1:
            raise ReadingConfigurationError(
                f"the measure's ab_tests test {entry.test_id!r} carries no Benjamini-Hochberg rank"
            )
        ranks[entry.test_id] = rank
        if type(item["rejected"]) is not bool:
            raise ReadingConfigurationError(
                f"the measure's ab_tests test {entry.test_id!r} carries no rejection flag"
            )
        holds = mean < 0 if entry.alternative == "mean_below_zero" else mean > 0
        wanted_outcome = AB_CANDIDATE if item["rejected"] and holds else AB_NO_EVIDENCE
        if outcome != wanted_outcome:
            raise ReadingConfigurationError(
                f"the measure's ab_tests test {entry.test_id!r} says {outcome!r} where its "
                f"rejection {item['rejected']!r} and mean {mean!r} make it {wanted_outcome!r}"
            )
    expected_family = len(spec.entries)
    if block["family_size"] != expected_family:
        raise ReadingConfigurationError(
            f"the measure's ab_tests family_size {block['family_size']!r} is not the declared "
            f"family of {expected_family} registered test(s): R-R1's family is all of them, "
            "measured or withheld"
        )
    if len(measured_ids) + len(withheld) != expected_family or sorted(ranks.values()) != list(
        range(1, len(measured_ids) + 1)
    ):
        raise ReadingConfigurationError(
            "the measure's ab_tests measured and withheld tests do not add up to the declared "
            "family, or their ranks are not 1..the measured-test count"
        )
    family = block["family"]
    if (
        not isinstance(family, list)
        or sorted(family) != sorted(measured_ids)
        or family != [name for name, _ in sorted(ranks.items(), key=lambda pair: pair[1])]
        or not isinstance(withheld, list)
        or sorted(withheld) != sorted(block["withheld"])
        or set(family) & set(withheld)
        or set(family) | set(withheld) != {entry.test_id for entry in spec.entries}
    ):
        raise ReadingConfigurationError(
            "the measure's ab_tests family is not the measured tests in Benjamini-Hochberg "
            "order plus the withheld tests, once each, across the declared family"
        )
    return dict(block)


_AB_PATH_TOKEN: Final = re.compile(r"(?<![\w.-])(?:~/|/)[^\s,;:)\]]+")
"""Anything that looks like an absolute or home path in a refusal message, redacted before the
message enters a result: an A/B refusal is a data verdict, and a result file is not a place to
learn where a store lives (`panel_view.PANEL_STORE_PLACEHOLDER`'s rule)."""

_AB_SECRET: Final = re.compile(r"(?i)\b(token|secret|password|passwd|api[_-]?key)\s*[=:]\s*\S+")
"""A credential-looking assignment, redacted the same way. The A/B measurement touches no
credential, so this can only catch a message that quotes one; redacting it costs nothing."""


def _refusal_reason(error: BaseException) -> str:
    """`measurement refused: <Type>: <disclosable message>` for one A/B refusal.

    The message is the error's own `disclosable` text where it declares one (the repository's
    convention for text a body may carry: `PanelStorageError.disclosable`, `ModelViewError`), and
    otherwise its `str`, with anything shaped like a path or a credential assignment replaced.
    The result file is written and kept for a spent window, so it must not be a channel for the
    store's location.
    """
    message = getattr(error, "disclosable", None)
    if not isinstance(message, str) or not message.strip():
        message = str(error)
    text = " ".join(message.split())
    text = _AB_PATH_TOKEN.sub("<path>", text)
    text = _AB_SECRET.sub(lambda match: f"{match.group(1)}=<redacted>", text)
    return f"measurement refused: {type(error).__name__}: {text or 'no message'}"


def _refused_ab_tests(spec: AbTests, error: BaseException) -> dict[str, Any]:
    """The A/B block written when this read's A/B measurement was refused.

    A refusal in the A/B section must not cost G-0 its secondary diagnostics: the one read is
    spent once the claim is written, and the G-0 document has already been measured when this
    block is built. So the block keeps every frozen value and writes each test `not_measurable`
    with the redacted refusal as its reason, family size the declared family, no reported verdict
    and every test withheld; the G-0 document goes to `result.json` and `result.txt` as usual. It
    is the same shape `_require_ab_result` accepts, so a refused block is still the registration's.
    """
    reason = _refusal_reason(error)
    tests: list[dict[str, Any]] = []
    for entry in spec.entries:
        veto = entry.kind == "veto_difference"
        tests.append(
            {
                "id": entry.test_id,
                "kind": entry.kind,
                "factor": entry.factor,
                "direction": entry.direction,
                "alternative": entry.alternative,
                "horizon": spec.horizon,
                "stride": spec.stride,
                "sampled_sessions": 0,
                "measured_periods": 0,
                "mean": None,
                "sd": None,
                "t": None,
                "p_two_sided": None,
                "p_one_sided": None,
                "p_adjusted": None,
                "exact": None,
                "sign_patterns": None,
                "bh_rank": None,
                "rejected": None,
                "outcome": AB_NOT_MEASURABLE,
                "reason": reason,
                "wording": AB_NOT_MEASURABLE_WORDING,
                "vetoed_observations": 0 if veto else None,
                "minimum_vetoed_observations": (spec.veto.minimum_observations if veto else None),
            }
        )
    return {
        "schema": AB_TESTS_SCHEMA,
        "window": spec.window,
        "horizon": spec.horizon,
        "stride": spec.stride,
        "false_discovery_rate": spec.false_discovery_rate,
        "minimum_vetoed_observations": spec.veto.minimum_observations,
        "dependence": AB_DEPENDENCE,
        "family_size": len(spec.entries),
        "family": [],
        "withheld": [entry.test_id for entry in spec.entries],
        "wording": AB_BLOCK_WORDING,
        "tests": tests,
    }


def ab_tests_lines(block: Mapping[str, Any]) -> list[str]:
    """The A/B block as text: one line for the family, then one line per test.

    Every test line names its conclusion and carries `exploratory candidate` (or 探索性候选) in
    its wording; no line upgrades a result to confirmatory, and a `not_measurable` line says why.
    The family line states the declared family size, how many tests were reported and how many
    were withheld from the verdicts but counted in that family.
    """
    tests = block["tests"]
    family_size = block["family_size"]
    reported = block["family"]
    withheld = block["withheld"]
    lines = [
        f"ab tests {block['schema']}: {len(tests)} test(s); declared family size {family_size}, "
        f"Benjamini-Hochberg at q={_number(block['false_discovery_rate'])}: "
        f"{len(reported)} measured test(s) reported, {len(withheld)} not measurable withheld "
        "from the verdicts but counted in the family; every conclusion is an exploratory "
        "candidate (探索性候选), never confirmatory evidence"
    ]
    for item in tests:
        details = [
            f"sampled {item['sampled_sessions']}",
            f"measured {item['measured_periods']}",
            f"mean {_number(item['mean'])}",
            f"t {_number(item['t'])}",
            f"one-sided p {_number(item['p_one_sided'])}",
            f"q {_number(item['p_adjusted'])}",
        ]
        if item["kind"] == "veto_difference":
            details.insert(
                2,
                f"vetoed observations {item['vetoed_observations']} "
                f"(minimum {item['minimum_vetoed_observations']})",
            )
        tail = f"{item['outcome']} -- {item['wording']}"
        if item["outcome"] == AB_NOT_MEASURABLE and item["reason"]:
            tail = f"{item['outcome']} ({item['reason']}) -- {item['wording']}"
        lines.append(f"  test {item['id']}: " + ", ".join(details) + f": {tail}")
    return lines


@dataclass(slots=True)
class _Captured:
    """What the engine read on its way through the samples: the eligible scope per session and
    every labelled h20 return. Collected by the two wrappers below so the A/B tests read the very
    cross sections, labels and sampling the diagnostics document already records -- no second
    facts day, no second label read, no forked engine."""

    scores: dict[date, tuple[tuple[str, float], ...]] = field(default_factory=dict)
    labels: dict[tuple[int, date, str], float] = field(default_factory=dict)


class _CapturingScores:
    """`PanelScoreSource`'s delegate, recording each session's eligible `composite` scope."""

    def __init__(self, delegate: PanelScoreSource, captured: _Captured) -> None:
        self.delegate = delegate
        self.captured = captured

    def scores_on(self, session: date) -> Mapping[str, tuple[tuple[str, float], ...]]:
        scored = self.delegate.scores_on(session)
        self.captured.scores[session] = scored.get(COMPOSITE, ())
        return scored


class _CapturingLabels:
    """One year's label reader, recording each labelled `(horizon, session, code)` return."""

    def __init__(self, delegate: LabelSource, captured: _Captured) -> None:
        self.delegate = delegate
        self.captured = captured

    @property
    def calendar(self) -> TradingCalendar:
        return self.delegate.calendar

    def window(self, instant: datetime, *, horizon: Any) -> Any:
        return self.delegate.window(instant, horizon=horizon)

    def label(self, ts_code: str, window: Any) -> Any:
        label = self.delegate.label(ts_code, window)
        if label is not None and label.is_labelled:
            self.captured.labels[(window.horizon.count, window.prediction_day, ts_code)] = (
                label.realized_return
            )
        return label


def _limit_events_reader(store: PanelStore) -> Callable[[date], tuple[LimitEvent, ...]]:
    """Each session's limit events, read at **that session's own** 16:30 publication instant.

    The veto's five-session window is assembled by `_vetoed_on`; this is its read, and the
    per-session instant is the point-in-time rule: a replay at session D's evening cannot see a
    row published after D's 16:30, and reading all five sessions at the read's own `as_of` would
    let a later restatement change an earlier session's events.
    """

    def events_on(session: date) -> tuple[LimitEvent, ...]:
        return limit_events_on(
            store,
            session=session,
            as_of=session_publication_instant(session),
            exchange=EXCHANGE,
        )

    return events_on


def _vetoed_on(
    day: date,
    *,
    calendar: TradingCalendar,
    events_for: Callable[[date], tuple[LimitEvent, ...]],
    lookback: int,
) -> frozenset[str]:
    """The rule's answer on `day`: the `lookback` sessions **ending at** `day`, newest first.

    A session further back than the fourth predecessor is never passed to `vetoed_codes`: the
    window is the rule's, and a rule that read six sessions would not be
    `market_bearish_5/v1`. Reading the calendar's predecessor (instead of subtracting days) is
    what makes the window five *sessions* across a holiday or a year boundary.
    """
    sessions = [day]
    current = day
    for _ in range(lookback - 1):
        try:
            current = calendar.previous_trading_day(current)
        except CalendarHorizonError as error:
            raise ReadingError(
                f"the veto's {lookback}-session window ending {day.isoformat()} reaches the "
                f"start of the calendar the reading loaded: {error}"
            ) from error
        sessions.append(current)
    return vetoed_codes([events_for(session) for session in sessions])


class _FactorReads:
    """One factor's stored **raw** observations, read one partition year at a time on first use.

    A build belongs to a session when its `as_of` is dated in `Asia/Shanghai` on that session; a
    session with more than one build answers with the one stamped at, or the earliest at or after,
    its 16:30 publication instant, and a build stamped before it is not a session answer at all
    (it did not know the close). Only `TIER_ADMITTED_CODES["raw"]` values are read, which is the
    same admission the factor plane's own IC reads use. The year read is the loader's own
    (`load_factor_observations`, point-in-time at the registration's `as_of`); the rows are
    dropped as soon as the wanted sessions' values are kept, `strategy_view._year_sections`'
    arrangement at this scale.
    """

    def __init__(self, store: PanelStore, *, as_of: datetime) -> None:
        self._store = store
        self._as_of = as_of
        self._years: dict[tuple[str, int], dict[date, dict[str, float]]] = {}

    def values_on(self, definition: Any, day: date) -> Mapping[str, float]:
        key = (definition.qualified_key, day.year)
        if key not in self._years:
            self._years[key] = self._read_year(definition, day.year)
        return self._years[key].get(day, {})

    def _read_year(self, definition: Any, year: int) -> dict[date, dict[str, float]]:
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


def _measure_ab_tests(
    *,
    calendar: TradingCalendar,
    plan: SignalPlan,
    settings: _Settings,
    captured: _Captured,
    spec: AbTests,
    events_for: Callable[[date], tuple[LimitEvent, ...]],
    factor_reads: _FactorReads,
) -> dict[str, Any]:
    """The three tests of R-R1 over the engine's own sampled sessions, measured and evaluated.

    The sample is `p7_diagnostics.sampled_sessions` -- the same every-`ceil(h/stride)`th signal
    day `summarize` reads -- so these p-values are over the non-overlapping periods of the
    registration, not over every session. Plan A's difference is taken over the eligible scope's
    labelled returns (the vetoed subset's mean less all survivors' mean); plan B's rank IC reuses
    `read_cross_section`, with the factor oriented by its declared direction, over the same scope
    and labels. Nothing here computes a standard error of its own: `summarize_series` gives the
    mean, sd and t, and the p-value is the repository's sign-flip test.

    Plan A's difference exists only on a sampled session that has at least one labelled vetoed
    survivor: a session with none contributes no period (it is planned and not measured, and the
    `sampled`/`measured` counts say so), while the observation floor counts exactly the labelled
    vetoed-survivor observations the differences were taken over.
    """
    sampled = _diagnostics.sampled_sessions(plan, spec.horizon)
    series: list[AbTestSeries] = []
    values: list[tuple[date, float]] = []
    vetoed_observations = 0
    for day in sampled:
        survivors = captured.scores.get(day)
        if not survivors:
            continue
        returns = {
            code: captured.labels[(spec.horizon, day, code)]
            for code, _ in survivors
            if (spec.horizon, day, code) in captured.labels
        }
        if not returns:
            continue
        vetoed = _vetoed_on(
            day,
            calendar=calendar,
            events_for=events_for,
            lookback=spec.veto.lookback_sessions,
        )
        vetoed_returns = sorted(returns[code] for code in vetoed if code in returns)
        if not vetoed_returns:
            continue
        values.append(
            (
                day,
                statistics.fmean(vetoed_returns) - statistics.fmean(returns.values()),
            )
        )
        vetoed_observations += len(vetoed_returns)
    series.append(
        AbTestSeries(
            test_id=VETO_TEST_ID,
            kind="veto_difference",
            factor=None,
            sampled_sessions=len(sampled),
            values=tuple(values),
            vetoed_observations=vetoed_observations,
        )
    )
    for entry in spec.factor_tests:
        definition = FACTOR_DEFINITIONS.get(entry.factor)
        sign = -1.0 if entry.direction == "lower_is_better" else 1.0
        values = []
        for day in sampled:
            survivors = captured.scores.get(day)
            if not survivors:
                continue
            lookup = factor_reads.values_on(definition, day)
            scored = [
                (code, sign * value)
                for code, _ in survivors
                if (value := lookup.get(code)) is not None
            ]
            if not scored:
                continue
            labelled = {
                code: captured.labels[(spec.horizon, day, code)]
                for code, _ in scored
                if (spec.horizon, day, code) in captured.labels
            }
            if not labelled:
                continue
            readout = read_cross_section(
                scored,
                labelled,
                loser_decile_count=settings.loser_decile_count,
                tails=settings.tails,
            )
            if readout.rank_ic is not None:
                values.append((day, readout.rank_ic))
        series.append(
            AbTestSeries(
                test_id=entry.factor,
                kind="factor_rank_ic",
                factor=entry.factor,
                sampled_sessions=len(sampled),
                values=tuple(values),
            )
        )
    return evaluate_ab_tests(spec, series)


def _measured_ab_tests(
    ab_tests: AbTests | None,
    *,
    calendar: TradingCalendar,
    plan: SignalPlan,
    settings: _Settings,
    captured: _Captured,
    store: PanelStore,
    as_of: datetime,
) -> dict[str, Any] | None:
    """The A/B block measured on its own, isolated from the G-0 document.

    The G-0 engine has already run and its document is built from `readings` whatever happens
    here: the one read is spent once the claim is written, so a refusal in the A/B section (a
    per-session limit-events read that a restated row refuses, a factor manifest check, a
    cross-section with no dispersion, an empty plan, anything) must not cost G-0 its secondary
    diagnostics. On any exception this returns the refused block (`_refused_ab_tests`): every
    test `not_measurable`, the declared family size, no verdict, and a redacted reason. No
    `DiagnosticWindowError` can escape from here: the G-0 engine's own window checks have already
    run, and `_vetoed_on`'s calendar edge is a `ReadingError` for exactly this reason.

    `None` when the registration carries no A/B section -- that is the old reading, unchanged.
    """
    if ab_tests is None:
        return None
    try:
        return _measure_ab_tests(
            calendar=calendar,
            plan=plan,
            settings=settings,
            captured=captured,
            spec=ab_tests,
            events_for=_limit_events_reader(store),
            factor_reads=_FactorReads(store, as_of=as_of),
        )
    except Exception as error:  # the isolation is the point; see the docstring
        return _refused_ab_tests(ab_tests, error)


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
            ab_tests = (
                None
                if body.get("ab_tests") is None
                else _ab_tests_of(body["ab_tests"], settings, window)
            )
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
            captured = _Captured()
            scores = _CapturingScores(
                PanelScoreSource(
                    store=store,
                    book=explore.book,
                    transform=FACTOR_TRANSFORMS.get(explore.transform),
                    exchange=EXCHANGE,
                    as_of=explore.as_of,
                    min_coverage=explore.min_coverage,
                    max_carry_sessions=explore.max_carry_sessions,
                ),
                captured,
            )
            cached_sessions = max(explore.horizons) + 2

            def labels_for(year: int) -> LabelSource:
                return _CapturingLabels(
                    panel_label_source(
                        store,
                        years=label_years(year, ceiling_year=window.ceiling_year),
                        exchange=EXCHANGE,
                        as_of=explore.as_of,
                        cached_sessions=cached_sessions,
                    ),
                    captured,
                )

            readings = run_diagnostics(request, scores=scores, labels_for=labels_for)
            ab_document = _measured_ab_tests(
                ab_tests,
                calendar=calendar,
                plan=plan,
                settings=settings,
                captured=captured,
                store=store,
                as_of=explore.as_of,
            )
            partitions = store.partitions_read()
        document = diagnostic_document(
            request,
            readings,
            config=explore.config(window),
            code_commit=body["code_commit"],
            partitions_read=partitions,
            binding=binding,
        )
        if ab_document is not None:
            document["ab_tests"] = ab_document
        return document

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
    if registered.ab_tests is not None:
        if "error" not in measured:
            try:
                result["ab_tests"] = _require_ab_result(registered.ab_tests, measured)
            except Exception as error:  # any mismatch must not discard the measured G-0 document
                # The G-0 document is already measured and the claim is already spent; an A/B
                # block that is not this registration's is recorded as refused instead of taking
                # the whole read down with it. The read-once design exists to keep the result a
                # spent window paid for, and a malformed block is exactly the case where refusing
                # would cost the read while the refused block still says what went wrong.
                result["ab_tests"] = _refused_ab_tests(registered.ab_tests, error)
    elif "ab_tests" in measured:
        raise ReadingConfigurationError(
            "the measure wrote an ab_tests block the registration does not carry; the claim "
            "stands, the read is spent, and no result is written"
        )
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
      banner -- the banner printed first above is that function's line 0;
    - when the registration carries the A/B section: its family line and one line per test
      (`ab_tests_lines`), each naming the conclusion and the exploratory-candidate wording; a
      refused measurement says instead that the A/B tests were not measured.
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
        if body.get("ab_tests") is None:
            lines.append("no stop rule: a secondary, non-confirmatory reading decides nothing")
        else:
            lines.append(
                "no stop rule: a secondary, non-confirmatory reading decides no stop rule; the "
                "A/B tests below are exploratory candidates only, never confirmatory evidence"
            )
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
    if body.get("ab_tests") is not None:
        block = result.get("ab_tests")
        if block is None:
            lines.append(
                "ab tests: not measured"
                + (
                    " -- the measurement was refused, and the one read is spent"
                    if "error" in result
                    else ""
                )
            )
        else:
            lines.extend(ab_tests_lines(block))
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
