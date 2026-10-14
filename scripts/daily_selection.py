"""The one daily selection command (`V2-P6-011`): after the close, today's candidates, target
weights and a registered prediction, under the registered configuration.

    uv run --no-sync --env-file .env python scripts/daily_selection.py --runtime-dir RT

Eight steps, in this order. The first that fails stops the run, is named, and sets a non-zero
exit; nothing after it runs. Every unexpected fault inside a step is reported as that step's
refusal, with the Tushare requests spent so far.

1. **registration** -- read `docs/research/p6-registration.json` (`--registration`), the file the
   holdout ran on. It must be committed and byte-equal to `HEAD`'s, and the code running now must
   be its `code_commit`: `registry.admit_registered_code`, the holdout guard's own checks, over
   `REGISTERED_PATHS` plus this file. Forward results come from the registered code or not at all.
   A missing file exits `2`; a refused binding exits `3`. A scheduled run should stand in a
   checkout pinned at the registration (`--pin-worktree`), so development elsewhere cannot make
   it refuse.
2. **panel update** -- `openalpha panel build --incremental` for the year of the newest closed
   session, pinned to one `--as-of` for the day, over the targets the day's scoring reads
   (`targets_for`): the price base, every statement dataset a registered factor reads, and the
   industry targets on a day that reads industries (`industry_day`). When the session's outcome
   window or its next session reaches into the next calendar year, that year's `trade_cal` too.
3. **panel doctor** -- `openalpha panel doctor` and the dependency gate `openalpha data-check`
   over those datasets, the year and the session. Not clean stops the run before any factor is
   built.
4. **factor build** -- every factor tier the configuration reads, at the session's signal instant
   (16:30 Shanghai) and at every session since the tier's newest build that a missed run left
   unbuilt (at most `MAX_CATCH_UP_SESSIONS`; `V2-P6-032`), through
   `factor_view.build_factor_panel_set`. A tier already built at an instant is not built again.
5. **candidates** -- the session scored by `strategy_view.score_day`: the backtest's own feeds and
   scorer, so the ranking is the one a backtest of the registered configuration would trade on.
6. **target weights** -- the book's rebalance rule (`strategy_backtest.target_holdings`) over that
   ranking, the previous journalled targets as the book held, on the configuration's rebalance
   schedule counted from its `start`; equal weights of `1 / holding_count`, the rest cash.
7. **prediction** -- the day's scores registered in the prediction store before 09:15 on the next
   session, when the call auction that fixes the book's execution price begins:
   `strategy_registration.signal_day_batch` (the fit's own batch for a walk-forward source, the
   composite each security was ranked by otherwise).
8. **summary** -- printed, and written to the day's journal.

## Why steps 5 and 6 are not `shortlist run` and `portfolio construct`

The plan named those two commands. Neither implements the registered strategy, and using them
would publish a list and a book the research never measured:

- `shortlist run` orders by the weighted sum of the stored tier values over the registry's
  universe (`backtest/cross_section.py::CrossSectionScreen`); the strategy standardizes each
  component over the securities carrying every component and sums those (`zscore_sum` or
  `rank_sum`). It refuses the neutralized tier outright and cannot express a trailing-IC or
  walk-forward source at all, and its gate refuses any list nobody has researched unless the
  floor is zero.
- `portfolio construct` weights a shortlist by declared rank tiers under a 25% position cap and
  80% exposure, with no buffer band; the strategy holds `holding_count` names at equal capital,
  keeps a holding while it ranks within `buffer_rank`, and caps names per industry.

So the candidates are the strategy's own ranking and the targets its own rebalance rule, both
through the functions `run_strategy_backtest` uses, and the equality is tested rather than
asserted (`tests/unit/scripts/test_daily_selection.py`).

## What a day fetches (R2)

`targets_for` reads the configuration: `BASE_TARGETS` always (the calendar, the registry, the
three session-scoped price targets the scoring, the labels and the book read, and `index_daily`
for the protocol's 000905.SH benchmark), plus the target of every dataset a registered factor
reads -- the statement sweeps only for a configuration that reads statements. Industries are
refreshed only on a day whose scoring reads them (`industry_day`): every day for a neutralized
tier, whose build reads the whole cross section's industries; on a rebalance day for an industry
cap, whose only reader is the rebalance decision. On any other day nothing reads a membership,
so every security the day's scoring reads has exactly the membership a daily full refresh would
have given it -- none is read. `--full-update` fetches `FULL_UPDATE_TARGETS` instead, the whole
current year. The runbook carries the per-kind counts, measured from the code's own `BUDGET`
lines.

The survivor form (`V2-P7-016`; dispatched by the registration's `kind` in `V2-P7-008`) fetches
`targets_for_survivor`: the same rule plus `namechange`, the name corpus its eligibility facts
read -- 16 requests on an ordinary day against the P6 day's 15. Its step 2 takes a
`SurvivorPanelUpdate`: a store more sessions behind than the registration's
`adapter.max_panel_catch_up_sessions` (0..16, `survivor_panel_update`) is refused before any
request, with the `panel build` that fetches the oldest batch, and the year before's name
corpus is built again while its stored build is from before that year ended -- on the first
run of a year, whether that is in January or after a January of missed runs, and not again.

A year before whose stored session-scoped partitions stop short of its last session is refused
too, before any request and whatever the bound (`previous_year_sessions_missing`): the step
builds the session's year only, so it would never fill that tail, and the new year's first
`stk_limit` build would judge every security's year-boundary pair against the gap -- each
disputed pair fetched again at four requests, uncapped (about 20,000 requests at live scale,
A3 review) -- and leave a `date_gap` the doctor then blocks on. The refusal prints the `panel
build --incremental` that completes that year through its last session; it has to run before
the new year's first build. The P6 step has the same latent fault and is left as it is (it
must stay bit-identical).

That remedy builds no `trade_cal`, and the step counts the sessions it would catch up on, before
it builds anything, on the session's year's stored calendar. A store last run before a run stored
next year's calendar therefore meets a second refusal on the year's first session, once the year
before is complete: the year has no calendar to count on. It prints the `panel build --dataset
trade_cal` that stores it (`_calendar_refusal`).

Two consequences of fetching `namechange` for the session's year, which this step does not
remove (A3 review, Q1). Until the year's first rename announcement, Tushare serves no row for
the year, the panel refuses to write an empty partition, and step 2 fails on that target. A
session before the first announcement can never complete: the journal pins the day's `--as-of`,
and `namechange` is fetched at `min(year end, as_of)` (`cli._year_end_as_of`), so every retry of
that session asks the same empty window and spends a handful of requests doing so. The session
is lost, not delayed -- not a theoretical case: 2012's first announcement is dated 5 January,
after the first session on 4 January (the A-phase notes of the P7 step-0 plan). And it is not
new: P6's `--full-update` fetches `namechange` (`FULL_UPDATE_TARGETS`) and fails the same way
in early January.

## The registration's kind (`V2-P7-008`)

A registered configuration that names no `kind` is the P6 book, and every step above is its day,
exactly as before. One that names `kind: "survivor_book/v1"` is read after step 1 by
`strategy_survivor` -- its `survivor_book` and `adapter` blocks, nothing else -- and runs the
survivor day, `SURVIVOR_STEPS` (`_run_survivor_day`): registration, panel update, panel doctor,
factor build, paper head, facts, prediction, paper book, summary -- the paper book's writer lock
taken first, the day's clock and the book's holdings pinned in its own journal
(`RT/daily_survivor/`) before any request, and the forward record filed before the paper book.
Steps 1 to 7 run; step 8 refuses by name, after the record is filed, until `V2-P7-008` D5b wires
the paper book and the summary. Any other `kind`, or a survivor registration that cannot be read,
exits 2 at step 1 before any request; a survivor book whose seed is not the registration's exits
3. The survivor modules are imported only then: they load `backtest/paper.py`, whose audit hook a
P6 day does not install.

## Idempotent per trading day

The journal is `RT/daily_selection/<config_id[:16]>/<session>.json`. The day's `--as-of` is
written to it before anything is fetched, and every later run of that session uses it. A day
whose journal is complete is printed again and nothing runs: no request, no write. A day whose
panel update is journalled resumes after it. Every other step is idempotent on its own: a
partition written again with the same content is not rewritten (`PanelStore`), a factor tier
already built at the instant is skipped, and a prediction already registered for the day under
the same declaration is reused rather than filed twice (and a different one is refused).

## What the numbers are

Candidates, not recommendations: the ranking is a registered research configuration's, and the
holdout's verdict is what says whether it earned anything. Nothing here places an order or
states an expected return. The credential never passes through this file: `panel build` resolves
it inside `TushareProvider`, and the request counter wraps the transport and reads only each
request's `api_name`.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import importlib.metadata
import io
import json
import logging
import os
import platform
import plistlib
import re
import shlex
import shutil
import subprocess
import sys
import tomllib
from collections import Counter
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from enum import IntEnum
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final, cast
from zoneinfo import ZoneInfo

REPOSITORY: Final[Path] = Path(__file__).resolve().parents[1]
RESEARCH_SCRIPTS: Final[Path] = REPOSITORY / "scripts" / "research"
if str(RESEARCH_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(RESEARCH_SCRIPTS))

import grid  # noqa: E402  (scripts/research/grid.py: the holdout's own significance test)
import registry  # noqa: E402
from click import ClickException  # noqa: E402
from typer.main import get_command  # noqa: E402

from openalpha_cn import cli  # noqa: E402
from openalpha_cn.backtest.strategy_backtest import (  # noqa: E402
    StrategyBacktestError,
    target_holdings,
)
from openalpha_cn.domain.alpha_model import PredictionBatch  # noqa: E402
from openalpha_cn.domain.index_membership import (  # noqa: E402
    INDEX_WEIGHT_DATASET,
    INDEX_WEIGHT_INDEX_CODES,
)
from openalpha_cn.domain.index_prices import INDEX_DAILY_DATASET  # noqa: E402
from openalpha_cn.domain.name_history import NAMECHANGE_DATASET  # noqa: E402
from openalpha_cn.domain.prediction_record import (  # noqa: E402
    PredictionRecord,
    outcome_known_at_for,
)
from openalpha_cn.domain.trading_calendar import (  # noqa: E402
    TRADING_CALENDAR_DATASET,
    TradingCalendar,
    TradingCalendarError,
)
from openalpha_cn.factor_view import (  # noqa: E402
    FactorViewError,
    build_factor_panel_set,
    factor_build_requests,
    factor_build_years,
    resolve_factor,
)
from openalpha_cn.panel.catalog import DEFAULT_DATE_TIMEZONE, PanelStorageError  # noqa: E402
from openalpha_cn.panel.store import PanelStore  # noqa: E402
from openalpha_cn.panel_factors import (  # noqa: E402
    FACTOR_TRANSFORMS,
    FactorEngineError,
    factor_manifest_dataset,
    load_factor_manifests,
    load_factor_transform_manifests,
)
from openalpha_cn.panel_ingest import (  # noqa: E402
    load_trading_calendar,
    newest_published_session,
    session_publication_instant,
)
from openalpha_cn.panel_neutralization import (  # noqa: E402
    FACTOR_NEUTRALIZATIONS,
    load_factor_neutralization_manifests,
)
from openalpha_cn.panel_view import panel_store  # noqa: E402
from openalpha_cn.providers.base import ProviderFailure  # noqa: E402
from openalpha_cn.providers.tushare import (  # noqa: E402
    TRADING_CALENDAR_DEFAULT_EXCHANGE,
    TushareTransport,
)
from openalpha_cn.runtime.composition import build_storage  # noqa: E402
from openalpha_cn.runtime.provenance import resolve_code_commit  # noqa: E402
from openalpha_cn.strategy_registration import (  # noqa: E402
    UNVERIFIABLE,
    InputPartition,
    InputProvenance,
    ProvenanceLookup,
    RecordCheck,
    RegisteredConfiguration,
    Schedule,
    StrategyRegistrationError,
    WitnessedDay,
    batch_digest,
    input_provenance,
    registration_cutoff,
    session_record,
    signal_day_batch,
    witnessed_days,
)
from openalpha_cn.strategy_registration import schedule_of as registered_schedule_of  # noqa: E402
from openalpha_cn.strategy_view import (  # noqa: E402
    PROTOCOL_BENCHMARKS,
    SignalDay,
    StrategyRequest,
    StrategyViewError,
    score_day,
    strategy_request,
)

if TYPE_CHECKING:
    # Imported for their types only: at run time the survivor day imports these modules, and only
    # for a registration that names a `kind` (they load `backtest/paper.py`).
    from openalpha_cn.backtest.paper_state import PaperBookSnapshot
    from openalpha_cn.domain.factor_transform import FactorTransformSpec
    from openalpha_cn.domain.survivor_book import ScreenResult
    from openalpha_cn.runtime.composition import StorageContainer
    from openalpha_cn.strategy_survivor import SurvivorRegistration
    from openalpha_cn.strategy_survivor_book import PaperBookLock, PaperHead
    from openalpha_cn.strategy_survivor_facts import FactsDay
    from openalpha_cn.strategy_survivor_market import SessionOrdinals

DAILY_SELECTION_SCHEMA: Final[str] = "openalpha-daily-selection/v1"
DEFAULT_REGISTRATION: Final[Path] = REPOSITORY / "docs" / "research" / "p6-registration.json"
REGISTRATION_IN_REPOSITORY: Final[str] = "docs/research/p6-registration.json"
JOURNAL_DIRECTORY: Final[str] = "daily_selection"
THIS_SCRIPT: Final[str] = "scripts/daily_selection.py"

BASE_TARGETS: Final[tuple[str, ...]] = (
    "trade_cal",
    "stock_basic",
    "adj_factor",
    "price",
    "stk_limit",
    "index_daily",
)
"""What every day fetches: the calendar, the registry, the price targets the scoring, the labels
and the book read, and `index_daily` for the protocol's benchmark (the forward report's)."""

FULL_UPDATE_TARGETS: Final[tuple[str, ...]] = (
    "trade_cal",
    "stock_basic",
    "namechange",
    "adj_factor",
    "price",
    "stk_limit",
    "index_daily",
    "index_weight",
    "income",
    "balancesheet",
    "cashflow",
    "fina_indicator",
)
"""`--full-update`: every year-scoped target plus `fina_indicator`, whatever the configuration
reads -- the whole current year kept fresh for other readers of the same store."""

SURVIVOR_EXTRA_TARGETS: Final[tuple[str, ...]] = (NAMECHANGE_DATASET,)
"""What the survivor form's day fetches beyond the P6 rule (`targets_for_survivor`, `V2-P7-016`):
the name corpus, which `strategy_survivor_facts.build_facts_day` reads for every listed
security's ST status and which no P6 factor reads (T3 section 7 item 7; T6-T7 2.1)."""

INDUSTRY_MEMBERSHIP_TARGET: Final[str] = "index_member_all"
INDUSTRY_TARGETS: Final[tuple[str, ...]] = ("index_classify", INDUSTRY_MEMBERSHIP_TARGET)
UNCHECKED_BY_YEAR: Final[frozenset[str]] = frozenset({"stock_basic", *INDUSTRY_TARGETS})
"""Targets whose partitions are not the session's market (a registry lifecycle year, a taxonomy
vintage, membership-event years), so the doctor is not asked about them under the session's year.

Measured on a copy of `runtime/panel` for 2026-08-28: asked about `stock_basic --year 2026`
beside the session datasets, the doctor warns `subject_set_disagreement` -- 5,449 securities with
bars "absent from stock_basic" -- because the 2026 partition holds 2026's listings (123 rows), not
the market; asked about every lifecycle year it holds, it refuses for want of a `trade_cal` for
each. Without it the same panel is clean and the gate clears. The registry is still read whole by
every step that needs it (`load_stock_universe` reads every lifecycle year the store holds), and
those reads refuse on their own."""

DEFAULT_MAX_STALENESS_DAYS: Final[int] = 30
"""The freshness bound every factor build of this command states (`factor build
--max-staleness-days`): the value this repository's factor builds have used throughout."""

MAX_CATCH_UP_SESSIONS: Final[int] = 20
"""The most sessions a tier missed that step 4 builds before the day's (`V2-P6-032`): about four
weeks of missed runs. By the `V2-P6-006` measurement (about 1.2 s per factor and instant, all
three tiers) a 21-factor configuration spends roughly 25 s a session, so the bound keeps a
catch-up near eight minutes. A longer gap is refused with the command that builds it in batches,
rather than spent inside one scheduled run."""

DEFAULT_MAX_PANEL_CATCH_UP_SESSIONS: Final[int] = 10
"""The bound the design's survivor registration names for its step 2 (`V2-P7-016`): the most
sessions the step fetches in one run beyond an ordinary day's two (the stored last session again,
and the day). Not a default any registration falls back to: the registration names the bound in
its `adapter` block, whose reader (`strategy_survivor.SurvivorAdapterConfig`) requires the key and
bounds it to 0..16 (`strategy_survivor.MAX_PANEL_CATCH_UP_SESSIONS`), and the survivor day reads it
from there (`survivor_panel_update`, the one reader of the key).

A bound on **sessions**, not on requests. Each session it admits costs one request per
session-scoped API -- five, seven once `stk_limit` pages -- so ten admit 50 requests (70 paged)
beside the ordinary day's 16 (20 paged): the 66 (90) the design's 100 was sized against (T6-T7
2.4). That sum is not the day's ceiling. It leaves out, each data-dependent and stated on its
own `BUDGET` or `REFETCH` line where `panel build` spends it:

- the year before's name corpus built again on a year's first run (+1,
  `names_built_before_year_end`), and next year's calendar once the outcome window reaches into
  it (+1);
- the withdrawal re-checks: up to `cli.WITHDRAWAL_RECHECK_LIMIT` (20) `(dataset, session)`
  pairs, one request each, three when `stk_limit` pages;
- a slice that starts before the stored horizon for an older unconfirmed defect
  (`cli._incremental_start`): those sessions are fetched again and not counted here;
- the return-path reproductions, four requests per disputed pair and uncapped
  (`cli._judge_return_paths`), the year-boundary pairs included -- a year before that stops
  short of its last session is refused for that reason (`previous_year_sessions_missing`);
- the statement sweeps and industry targets of a configuration that reads them.

So the worst case passes 100: the bound holds the part that grows with missed sessions, and
nothing else. When the session's year holds nothing yet, the count is one more than the sessions
fetched beyond two (`panel_sessions_to_catch_up`), so there the bound errs by one on the safe
side. Three different bounds, kept apart: `MAX_CATCH_UP_SESSIONS` is the factor tiers' catch-up
(step 4), and `strategy_survivor_facts`' `max_carry_sessions` is how far back a halted
security's last bar is looked for."""
PANEL_CATCH_UP_KEY: Final[str] = "max_panel_catch_up_sessions"
"""The key of the registration's `adapter` block the bound is read from, as a refusal names it."""

SHANGHAI: Final[ZoneInfo] = ZoneInfo(DEFAULT_DATE_TIMEZONE)
_WEIGHT_QUANTUM: Final[Decimal] = Decimal("0.0000000001")
_STRATEGY_KEYS: Final[frozenset[str]] = frozenset(
    {
        "as_of",
        "benchmarks",
        "buffer_rank",
        "combine",
        "components",
        "end",
        "exchange",
        "holding_count",
        "max_industry_weight",
        "neutralization",
        "participation_cap",
        "position_capital",
        "prediction_ids",
        "rebalance_every_sessions",
        "slippage_rate",
        "start",
        "trailing_ic",
        "transform",
        "walk_forward",
    }
)
"""Every key of a registered configuration: the keyword arguments of
`OpenAlphaSDK.run_strategy_backtest`, which is what the research grid and the holdout call."""


class DailyExit(IntEnum):
    """What the command exits with."""

    done = 0
    step_failed = 1
    no_registration = 2
    code_not_registered = 3


STEPS: Final[tuple[str, ...]] = (
    "registration",
    "panel update",
    "panel doctor",
    "factor build",
    "candidates",
    "target weights",
    "prediction",
    "summary",
)
SURVIVOR_STEPS: Final[tuple[str, ...]] = (
    "registration",
    "panel update",
    "panel doctor",
    "factor build",
    "paper head",
    "facts",
    "prediction",
    "paper book",
    "summary",
)
"""The survivor book's day (`V2-P7-008`), in its order. The steps it shares with the P6 day's
helpers -- the first four and `prediction` -- keep the P6 day's numbers, so a refusal those
helpers raise reads the same in either day; `summary` is ninth here and eighth there, which is
why `StepFailedError` and `_step` take the sequence a step is numbered in."""


class StepFailedError(RuntimeError):
    """A step refused; the run stops there and says which step it was, numbered in `steps` --
    `STEPS` unless the survivor day says `SURVIVOR_STEPS`."""

    def __init__(
        self,
        step: str,
        message: str,
        *,
        exit_code: DailyExit = DailyExit.step_failed,
        steps: tuple[str, ...] = STEPS,
    ):
        super().__init__(f"step {steps.index(step) + 1} ({step}) failed: {message}")
        self.step = step
        self.exit_code = exit_code
        self.requests: dict[str, int] = {}
        """The Tushare requests the run had made when it stopped, by `api_name`: a refused run
        spends requests too, and the budget is counted either way."""


@contextlib.contextmanager
def _step(name: str, *, steps: tuple[str, ...] = STEPS) -> Iterator[None]:
    """Report any fault the step did not anticipate as that step's refusal, by name, numbered in
    `steps`.

    A `StepFailedError` passes through; anything else becomes one naming the step and the
    fault's type -- and its message, except for a provider failure, whose message can carry the
    request envelope and is never printed (the `panel build` rule, applied here too).
    """
    try:
        yield
    except StepFailedError:
        raise
    except Exception as error:
        detail = (
            type(error).__name__
            if isinstance(error, ProviderFailure)
            else f"{type(error).__name__}: {error}"
        )
        raise StepFailedError(name, f"an unexpected fault: {detail}", steps=steps) from error


# --- the configuration ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True, kw_only=True)
class Registration:
    """The admitted registration: what it registered and the code commit it binds."""

    path: Path
    sha256: str
    commit: str
    code_commit: str
    config: Mapping[str, Any]
    config_id: str
    seed: int
    settings: Mapping[str, Any]
    """The registration's measurement settings (`grid.protocol_settings`' keys): what the forward
    report's significance test runs under (`forward_summary`)."""

    @property
    def declared(self) -> RegisteredConfiguration:
        """What a composite record declares about this registration."""
        return RegisteredConfiguration(
            config_id=self.config_id,
            registration_sha256=self.sha256,
            code_commit=self.code_commit,
            seed=self.seed,
        )


def admit_registration(path: Path, repo: Path, *, also_bound: Sequence[str] = ()) -> Registration:
    """Step 1: the committed registration, provided the running code is the registered code.

    `also_bound` adds paths the registration binds beside this file -- the forward report adds its
    own (`scripts/forward_report.py`), since a forward report is as much its claim. The
    registration's `config_id` is re-derived from its `config` here, the one place either command
    admits a registration (`run_holdout`'s defence against two halves that disagree).
    """
    if not path.is_file():
        raise StepFailedError(
            "registration",
            f"{path} does not exist; the daily command runs the registered configuration and "
            "there is none to run",
            exit_code=DailyExit.no_registration,
        )
    try:
        root, admitted = registry.admit_registered_code(
            path, repo, also_bound=(THIS_SCRIPT, *also_bound)
        )
    except registry.HoldoutConfigurationError as error:
        raise StepFailedError(
            "registration", str(error), exit_code=DailyExit.no_registration
        ) from error
    except registry.HoldoutRefusedError as error:
        raise StepFailedError(
            "registration",
            f"{type(error).__name__}: {error}",
            exit_code=DailyExit.code_not_registered,
        ) from error
    admit_environment(root, code_commit=str(admitted.registered.get("code_commit")))
    body = admitted.registered
    config = body.get("config")
    if not isinstance(config, dict):
        raise StepFailedError(
            "registration",
            f"{path} registers no configuration object",
            exit_code=DailyExit.no_registration,
        )
    config_id = grid.config_id(config)
    if config_id != body.get("config_id"):
        raise StepFailedError(
            "registration",
            f"{path}'s config_id {body.get('config_id')!r} does not match its own config "
            f"(re-derived: {config_id!r}); the registration file may have been edited after it "
            "was written",
            # Not "no registration": there is one, and it contradicts itself -- the same class as
            # code that is not the registered code (round 16).
            exit_code=DailyExit.code_not_registered,
        )
    settings = body.get("settings")
    settings = dict(settings) if isinstance(settings, dict) else {}
    seed = settings.get("random_seed", 0)
    return Registration(
        path=path,
        sha256=hashlib.sha256(admitted.content).hexdigest(),
        commit=admitted.commit,
        code_commit=str(body.get("code_commit")),
        config=config,
        config_id=config_id,
        # A bool is an int to Python and not a seed: it is read as no seed.
        seed=seed if isinstance(seed, int) and not isinstance(seed, bool) else 0,
        settings=settings,
    )


REGISTRATION_KIND_KEY: Final[str] = "kind"
"""The key of a registered configuration that names its kind (`strategy_survivor.KIND_KEY`),
spelled here because reading it from that module would load the module for every P6 day."""


def survivor_registration(registration: Registration) -> SurvivorRegistration | None:
    """The survivor book's registration, or `None` for a configuration that names no `kind`: the
    P6 book, whose day runs as it always has (`V2-P7-008`).

    `openalpha_cn.strategy_survivor` is imported here and only for a configuration that names a
    `kind`: it imports `backtest/paper.py`, whose import installs an audit hook for the life of
    the process, and a P6 day keeps no paper book (`test_daily_selection_dispatch.py` runs one in
    a fresh interpreter and finds neither module loaded). A survivor registration the command
    cannot read -- an unknown `kind`, a missing or extra top-level key, a block that does not
    validate or a book block that is not its own stored form -- exits 2, as a missing one does;
    one whose book `seed` is not the registration's own exits 3, the class of a `config_id` that
    does not match its own config: there is a registration, and it contradicts itself.
    """
    if REGISTRATION_KIND_KEY not in registration.config:
        return None
    from openalpha_cn import strategy_survivor

    named = registration.settings.get("random_seed")
    try:
        return strategy_survivor.read_survivor_registration(
            registration.config,
            # `Registration.seed` reads a missing or non-integer seed as 0; the reader keeps that
            # rule, and is told which case this is so that a refusal says so.
            seed=registration.seed if type(named) is int else None,
        )
    except strategy_survivor.SurvivorRegistrationContradictionError as error:
        raise StepFailedError(
            "registration",
            str(error),
            exit_code=DailyExit.code_not_registered,
            steps=SURVIVOR_STEPS,
        ) from error
    except strategy_survivor.SurvivorRegistrationError as error:
        raise StepFailedError(
            "registration",
            str(error),
            exit_code=DailyExit.no_registration,
            steps=SURVIVOR_STEPS,
        ) from error


LOCKFILE: Final[str] = "uv.lock"
"""The registered third-party environment: bound with the code (`registry.REGISTERED_PATHS`), so
the checkout's copy is the one committed at the registration's `code_commit`."""


def _distribution_name(name: str) -> str:
    """PEP 503's normalised form, which is how `uv.lock` spells every name."""
    return re.sub(r"[-_.]+", "-", name).lower()


PYTHON_VERSION_FILE: Final[str] = ".python-version"
"""The interpreter the research ran on, by `major.minor`: what `uv` builds the project with."""

_VERSION_MARKERS: Final[frozenset[str]] = frozenset(
    {"python_version", "python_full_version", "implementation_version"}
)
_TEXT_MARKERS: Final[frozenset[str]] = frozenset(
    {
        "implementation_name",
        "os_name",
        "platform_machine",
        "platform_python_implementation",
        "platform_release",
        "platform_system",
        "platform_version",
        "sys_platform",
    }
)
_MARKER_TOKEN: Final = re.compile(
    r"\s*(?:(?P<text>'[^']*'|\"[^\"]*\")|(?P<op>===|==|!=|<=|>=|~=|<|>|\(|\))"
    r"|(?P<word>[A-Za-z_][A-Za-z0-9_]*))"
)
_RELEASE: Final = re.compile(r"\d+(?:\.\d+)*")


def marker_environment() -> dict[str, str]:
    """PEP 508's marker environment for the running interpreter, from the standard library."""
    implementation = sys.implementation.version
    implementation_version = f"{implementation.major}.{implementation.minor}.{implementation.micro}"
    if implementation.releaselevel != "final":
        implementation_version += f"{implementation.releaselevel[0]}{implementation.serial}"
    return {
        "implementation_name": sys.implementation.name,
        "implementation_version": implementation_version,
        "os_name": os.name,
        "platform_machine": platform.machine(),
        "platform_python_implementation": platform.python_implementation(),
        "platform_release": platform.release(),
        "platform_system": platform.system(),
        "platform_version": platform.version(),
        "python_full_version": platform.python_version(),
        "python_version": ".".join(platform.python_version_tuple()[:2]),
        "sys_platform": sys.platform,
    }


def _marker_tokens(marker: str) -> list[tuple[str, str]]:
    tokens: list[tuple[str, str]] = []
    position = 0
    while position < len(marker.rstrip()):
        found = _MARKER_TOKEN.match(marker, position)
        if found is None or found.end() == position:
            raise ValueError(f"marker {marker!r} cannot be read at {marker[position:]!r}")
        kind = found.lastgroup
        assert kind is not None
        tokens.append((kind, found.group(kind)))
        position = found.end()
    return tokens


def _release(version: str, marker: str) -> tuple[int, ...]:
    if _RELEASE.fullmatch(version) is None:
        raise ValueError(f"marker {marker!r} compares {version!r}, which is not a release number")
    return tuple(int(part) for part in version.split("."))


def _padded(left: tuple[int, ...], right: tuple[int, ...]) -> tuple[tuple[int, ...], ...]:
    width = max(len(left), len(right))
    return tuple(side + (0,) * (width - len(side)) for side in (left, right))


def _compare_versions(have: str, op: str, want: str, marker: str) -> bool:
    """`have op want` for release numbers: `==`/`!=` take a `.*` prefix, `~=` is PEP 440's."""
    if op == "===":
        return have == want
    if op in {"==", "!="} and want.endswith(".*"):
        prefix = _release(want[:-2], marker)
        release = _release(have, marker)
        release = release + (0,) * max(0, len(prefix) - len(release))
        return (release[: len(prefix)] == prefix) is (op == "==")
    left, right = _padded(_release(have, marker), _release(want, marker))
    if op == "~=":
        wanted = _release(want, marker)
        if len(wanted) < 2:
            raise ValueError(f"marker {marker!r} uses ~= with a single-part version")
        return left >= right and left[: len(wanted) - 1] == wanted[:-1]
    outcomes = {
        "==": left == right,
        "!=": left != right,
        "<": left < right,
        "<=": left <= right,
        ">": left > right,
        ">=": left >= right,
    }
    if op not in outcomes:
        raise ValueError(f"marker {marker!r} uses {op!r} on a version")
    return outcomes[op]


def _marker_comparison(
    left: tuple[str, str],
    op: str,
    right: tuple[str, str],
    marker: str,
    environment: Mapping[str, str],
) -> bool:
    """One `operand op operand` whose one side is a variable and the other a quoted value."""
    names = [value for kind, value in (left, right) if kind == "word"]
    if len(names) != 1:
        raise ValueError(f"marker {marker!r} compares {left[1]} with {right[1]}")
    (name,) = names
    if name not in _VERSION_MARKERS | _TEXT_MARKERS or name not in environment:
        raise ValueError(f"marker {marker!r} names {name!r}, which this evaluator does not read")

    def value(operand: tuple[str, str]) -> str:
        kind, token = operand
        return environment[token] if kind == "word" else token[1:-1]

    have, want = value(left), value(right)
    if op == "in":
        return have in want
    if op == "not in":
        return have not in want
    if name in _VERSION_MARKERS and left[0] == "word":
        return _compare_versions(have, op, want, marker)
    if op == "==":
        return have == want
    if op == "!=":
        return have != want
    raise ValueError(f"marker {marker!r} orders text with {op!r}")


def marker_holds(marker: str, environment: Mapping[str, str]) -> bool:
    """Whether a `uv.lock` marker holds in `environment`, or `ValueError` if it cannot be read.

    The PEP 508 subset `uv.lock` writes -- `or` over `and` over parenthesised groups or
    `variable op 'value'` comparisons -- evaluated with the standard library alone: `packaging`,
    which implements the full grammar, is only a test dependency here, and the daily command does
    not add a runtime one. Versions compare as release numbers, everything else as text. A marker
    outside that subset -- an `extra`, a variable it does not know, a version that is not a
    release number -- is an error, never a guess: the admission it feeds refuses instead.
    """
    tokens = _marker_tokens(marker)
    position = 0

    def peek() -> tuple[str, str] | None:
        return tokens[position] if position < len(tokens) else None

    def take() -> tuple[str, str]:
        nonlocal position
        token = peek()
        if token is None:
            raise ValueError(f"marker {marker!r} ends too early")
        position += 1
        return token

    def disjunction() -> bool:
        holds = conjunction()
        while peek() == ("word", "or"):
            take()
            holds = conjunction() or holds
        return holds

    def conjunction() -> bool:
        holds = atom()
        while peek() == ("word", "and"):
            take()
            holds = atom() and holds
        return holds

    def atom() -> bool:
        if peek() == ("op", "("):
            take()
            holds = disjunction()
            if take() != ("op", ")"):
                raise ValueError(f"marker {marker!r} has an unclosed group")
            return holds
        left = take()
        if left[0] == "op" or left[1] in {"and", "or", "in", "not"}:
            raise ValueError(f"marker {marker!r} has {left[1]!r} where a value belongs")
        op_kind, op = take()
        if op_kind == "word" and op == "not" and take() == ("word", "in"):
            op = "not in"
        elif not (op_kind == "op" and op not in {"(", ")"}) and op != "in":
            raise ValueError(f"marker {marker!r} has {op!r} where a comparison belongs")
        right = take()
        if right[0] == "op" or right[1] in {"and", "or", "in", "not"}:
            raise ValueError(f"marker {marker!r} has {right[1]!r} where a value belongs")
        return _marker_comparison(left, op, right, marker, environment)

    holds = disjunction()
    if position != len(tokens):
        raise ValueError(f"marker {marker!r} has {tokens[position][1]!r} after its end")
    return holds


def locked_versions(
    lock_text: str, *, environment: Mapping[str, str] | None = None
) -> dict[str, str | None]:
    """The version `uv.lock` locks of each package **for this interpreter**, by normalised name;
    `None` for one it locks no version of.

    A universal lock lists a package once per fork -- numpy 2.4.6 for Pythons before 3.12 and
    2.5.1 from 3.12, in this repository's own lock -- each entry with the `resolution-markers`
    it applies under. Only an entry whose markers hold for `environment` (the running
    interpreter's, by default) counts, and one with no markers applies everywhere. A package
    every entry of which is for other interpreters is absent: installed, it is not locked for
    this one. Two entries of different versions that both hold are a lock this cannot read, and a
    `ValueError`, as is a marker `marker_holds` cannot read.

    Dependency `marker`s are not evaluated: they decide whether a package is installed on an
    interpreter, not at which version, and a locked package that is not installed is not a
    difference (`environment_differences`).
    """
    where = marker_environment() if environment is None else environment
    document = tomllib.loads(lock_text)
    versions: dict[str, set[str | None]] = {}
    for package in document.get("package", []):
        if not isinstance(package, dict) or "name" not in package:
            continue
        markers = package.get("resolution-markers")
        if markers is not None:
            if not isinstance(markers, list) or not all(isinstance(m, str) for m in markers):
                raise ValueError(f"{package['name']}'s resolution-markers are not a marker list")
            if not any(marker_holds(marker, where) for marker in markers):
                continue
        version = package.get("version")
        versions.setdefault(_distribution_name(str(package["name"])), set()).add(
            None if version is None else str(version)
        )
    locked: dict[str, str | None] = {}
    for name, found in versions.items():
        if len(found) > 1:
            raise ValueError(
                f"{LOCKFILE} locks {name} at {sorted(map(str, found))} for this interpreter"
            )
        (locked[name],) = found
    return locked


def installed_distributions() -> dict[str, str]:
    """Every distribution the running interpreter can import, by normalised name."""
    return {
        _distribution_name(distribution.metadata["Name"]): distribution.version
        for distribution in importlib.metadata.distributions()
        if distribution.metadata["Name"]
    }


def environment_differences(
    locked: Mapping[str, str | None], installed: Mapping[str, str]
) -> list[str]:
    """Where the running environment is not the locked one, one sentence per distribution.

    Every **installed** distribution has to be in the lock at the locked version. A locked
    package that is not installed is not a difference: the lock covers every platform, Python
    version and extra (`akshare` is an extra, and one wheel per interpreter is listed), and a
    package the code needs and lacks fails at import rather than computing anything.
    """
    differences: list[str] = []
    for name, version in sorted(installed.items()):
        if name not in locked:
            differences.append(
                f"{name} {version} is installed and {LOCKFILE} does not lock it for this "
                "interpreter"
            )
        elif locked[name] is not None and locked[name] != version:
            differences.append(f"{name} {version} is installed and {LOCKFILE} pins {locked[name]}")
    return differences


def pinned_python(text: str, *, source: str) -> str:
    """The `major.minor` a `.python-version` names (`3.11`, `3.11.14`, `cpython@3.11`, ...)."""
    lines = [line.strip() for line in text.splitlines()]
    named = [line for line in lines if line and not line.startswith("#")]
    found = re.fullmatch(r"(?:[A-Za-z]+[@-])?(\d+)\.(\d+)(?:\.\d+)?", named[0]) if named else None
    if found is None:
        raise StepFailedError(
            "registration",
            f"{source} names no Python version this command can read: {text.strip()!r}",
            exit_code=DailyExit.code_not_registered,
        )
    return f"{found.group(1)}.{found.group(2)}"


def admit_interpreter(root: Path, *, code_commit: str) -> None:
    """The running interpreter is the one the research ran on, by `major.minor`, or the run stops.

    Read from the registered commit's `.python-version` (`git show <code_commit>:...`), so an
    edit to the checkout's working tree does not move it. Python's own `major.minor` changes the
    numerical libraries a lock resolves to (this repository's lock pins a different numpy on
    3.12), so a different interpreter is a different computation even with the lock obeyed.
    """
    source = f"{code_commit}:{PYTHON_VERSION_FILE}"
    run = registry._git(root, "show", source)
    if run.returncode != 0:
        raise StepFailedError(
            "registration",
            f"the registered commit has no {PYTHON_VERSION_FILE} ({source}), so the interpreter "
            "the research ran on is not known",
            exit_code=DailyExit.code_not_registered,
        )
    pinned = pinned_python(run.stdout.decode(errors="replace"), source=source)
    running = f"{sys.version_info.major}.{sys.version_info.minor}"
    if running != pinned:
        raise StepFailedError(
            "registration",
            f"the running interpreter ({sys.executable}) is Python {running}, and the registered "
            f"{PYTHON_VERSION_FILE} pins {pinned}. Run from the checkout --pin-worktree made, "
            "whose environment is built on that interpreter",
            exit_code=DailyExit.code_not_registered,
        )


def admit_environment(root: Path, *, code_commit: str) -> None:
    """Step 1's second half (`V2-P6-011`): the running interpreter is the registered one
    (`admit_interpreter`) and its third-party packages are exactly the ones `<root>/uv.lock`
    locks **for that interpreter** (`locked_versions`), or the run stops.

    The bound code is compared with the registration's commit (`registry.admit_registered_code`,
    whose bound paths include `uv.lock`), and that says nothing about what the interpreter
    actually imports: a scheduled run started from a shared virtual environment would compute
    with whatever that environment was last synced to. `--pin-worktree` gives the pinned checkout
    its own environment, synced from its own lock offline (`sync_pinned_environment`); this is
    the defence behind that -- it refuses a run whose environment is not that lock, by name.
    """
    admit_interpreter(root, code_commit=code_commit)
    lock = root / LOCKFILE
    if not lock.is_file():
        raise StepFailedError(
            "registration",
            f"{lock} does not exist, so the environment this run computes with cannot be "
            "compared with a registered one",
            exit_code=DailyExit.code_not_registered,
        )
    try:
        locked = locked_versions(lock.read_text(encoding="utf-8"))
    except (ValueError, tomllib.TOMLDecodeError) as error:
        raise StepFailedError(
            "registration",
            f"the running environment cannot be compared with {lock}: {error}",
            exit_code=DailyExit.code_not_registered,
        ) from error
    differences = environment_differences(locked, installed_distributions())
    if differences:
        shown = "; ".join(differences[:8])
        more = f" (and {len(differences) - 8} more)" if len(differences) > 8 else ""
        raise StepFailedError(
            "registration",
            f"the running interpreter ({sys.executable}) is not the environment {lock} pins: "
            f"{shown}{more}. Run from the checkout --pin-worktree made, whose environment is "
            "synced from its own lock",
            exit_code=DailyExit.code_not_registered,
        )


def _decimal(value: object, key: str) -> Decimal:
    try:
        return Decimal(str(value))
    except ArithmeticError as error:
        raise ValueError(f"{key} is not a number: {value!r}") from error


def strategy_arguments(config: Mapping[str, Any]) -> dict[str, Any]:
    """The registered configuration as `strategy_request`'s keywords, less the dates.

    The registration stores the configuration in the research ledger's canonical JSON -- dates
    and decimals as text, tuples as lists -- so this reads it back into the types
    `OpenAlphaSDK.run_strategy_backtest` took when the holdout measured it. A key that is not
    one of those arguments is refused rather than ignored: a configuration this command cannot
    read whole is not the one that was registered.
    """
    unknown = sorted(set(config) - _STRATEGY_KEYS)
    if unknown:
        raise ValueError(f"the configuration names {unknown}, which no backtest argument reads")
    arguments: dict[str, Any] = {
        "combine": config.get("combine", "zscore_sum"),
        "components": tuple(
            (str(factor), str(tier), _decimal(weight, "a component weight"))
            for factor, tier, weight in config.get("components", ())
        ),
        "prediction_ids": tuple(config.get("prediction_ids", ())),
        "transform": config.get("transform"),
        "neutralization": config.get("neutralization"),
        "exchange": config.get("exchange", TRADING_CALENDAR_DEFAULT_EXCHANGE),
        "rebalance_every_sessions": int(config["rebalance_every_sessions"]),
        "holding_count": int(config["holding_count"]),
        "buffer_rank": None if config.get("buffer_rank") is None else int(config["buffer_rank"]),
        "max_industry_weight": (
            None
            if config.get("max_industry_weight") is None
            else _decimal(config["max_industry_weight"], "max_industry_weight")
        ),
        "trailing_ic": config.get("trailing_ic"),
        "walk_forward": config.get("walk_forward"),
    }
    for key in ("position_capital", "participation_cap", "slippage_rate"):
        if config.get(key) is not None:
            arguments[key] = _decimal(config[key], key)
    if config.get("benchmarks") is not None:
        arguments["benchmarks"] = tuple(config["benchmarks"])
    return arguments


def anchor_of(config: Mapping[str, Any]) -> date:
    """The day a backtest of the configuration starts on: its rebalance and refit schedules
    count sessions from here."""
    return date.fromisoformat(str(config["start"]))


def _registered_anchor(registration: Registration) -> date:
    try:
        return anchor_of(registration.config)
    except (KeyError, ValueError) as error:
        raise StepFailedError(
            "registration",
            f"the registered configuration names no start date: {error}",
            exit_code=DailyExit.no_registration,
        ) from error


def day_request(config: Mapping[str, Any], *, day: date, as_of: datetime) -> StrategyRequest:
    """The configuration as a `StrategyRequest` read at `as_of` about `day`."""
    try:
        arguments = strategy_arguments(config)
    except (KeyError, TypeError, ValueError) as error:
        raise StepFailedError(
            "registration",
            f"the registered configuration cannot be read: {error}",
            exit_code=DailyExit.no_registration,
        ) from error
    try:
        return strategy_request(**arguments, start=day - timedelta(days=1), end=day, as_of=as_of)
    except StrategyViewError as error:
        raise StepFailedError(
            "registration",
            f"the registered configuration is not a request: {error}",
            exit_code=DailyExit.no_registration,
        ) from error


@dataclass(frozen=True, slots=True, kw_only=True)
class TierBuild:
    """One factor tier the configuration reads, and the specs that name it."""

    factor: str
    tier: str
    transform: str | None
    neutralization: str | None


_TIER_ORDER: Final[Mapping[str, int]] = {"raw": 0, "processed": 1, "neutralized": 2}


def tiers_read(request: StrategyRequest) -> tuple[TierBuild, ...]:
    """Every factor tier the source reads, each factor at the highest tier it needs.

    A tier's build writes the tiers beneath it at the same instant, so a factor read at `raw`
    and at `processed` is one `processed` build.
    """
    source = request.source
    wanted: dict[tuple[str, str | None, str | None], str] = {}
    transform = None if request.transform is None else request.transform.qualified_key
    neutralization = (
        None if request.neutralization is None else request.neutralization.qualified_key
    )
    if source.walk_forward is not None:
        for token in source.walk_forward.features:
            factor, _, rest = token.partition("@")
            tier, *specs = rest.split(":")
            specs += [""] * (2 - len(specs))
            key = (factor.strip(), specs[0] or None, specs[1] or None)
            wanted[key] = max(wanted.get(key, tier), tier, key=_TIER_ORDER.__getitem__)
    else:
        pairs = (
            source.trailing_ic.components
            if source.trailing_ic is not None
            else tuple((token, tier) for token, tier, _ in source.components)
        )
        for token, tier in pairs:
            key = (
                token,
                transform if tier != "raw" else None,
                neutralization if tier == "neutralized" else None,
            )
            wanted[key] = max(wanted.get(key, tier), tier, key=_TIER_ORDER.__getitem__)
    return tuple(
        TierBuild(factor=factor, tier=tier, transform=spec, neutralization=neutral)
        for (factor, spec, neutral), tier in sorted(wanted.items(), key=lambda item: item[0][0])
    )


def reads_neutralized(builds: Sequence[TierBuild]) -> bool:
    """Whether a factor build reads industries: a neutralized tier, every day it is built."""
    return any(build.tier == "neutralized" for build in builds)


def targets_for(builds: Sequence[TierBuild]) -> tuple[str, ...]:
    """The panel targets the day's scoring reads, industries aside: `BASE_TARGETS` plus the
    target writing each dataset a registered factor reads, in `PANEL_BUILD_TARGETS`' order."""
    writes = {
        dataset: target
        for target, datasets in cli.PANEL_BUILD_TARGETS.items()
        for dataset in datasets
    }
    read = {
        writes[dataset] for build in builds for dataset in resolve_factor(build.factor).datasets
    }
    wanted = set(BASE_TARGETS) | (read - set(INDUSTRY_TARGETS))
    return tuple(target for target in cli.PANEL_BUILD_TARGETS if target in wanted)


def targets_for_survivor(builds: Sequence[TierBuild]) -> tuple[str, ...]:
    """The survivor form's day (`V2-P7-016`): `targets_for` -- unchanged, and with it the P6
    day -- plus `SURVIVOR_EXTRA_TARGETS`, in `PANEL_BUILD_TARGETS`' order. For the four composite
    factors that is `BASE_TARGETS` and `namechange`."""
    wanted = set(targets_for(builds)) | set(SURVIVOR_EXTRA_TARGETS)
    return tuple(target for target in cli.PANEL_BUILD_TARGETS if target in wanted)


def outcome_horizon(request: StrategyRequest) -> int:
    """How many sessions a day's registered scores are about: the model's horizon for a
    walk-forward source, the rebalance interval otherwise (`strategy_registration`)."""
    model = request.source.walk_forward
    return model.horizon_sessions if model is not None else request.spec.rebalance_every_sessions


# --- running the CLI in-process, and counting what it asked the network ------------------------


class CountingTransport:
    """A `TushareTransport` that counts requests by `api_name` and forwards them untouched.

    It reads `api_name` and nothing else of the payload, stores nothing of it and prints
    nothing: the credential the provider put in the envelope passes through as it would pass
    through the HTTP transport itself.
    """

    def __init__(self, inner: TushareTransport, counts: Counter[str]) -> None:
        self._inner = inner
        self._counts = counts

    def post(self, payload: dict[str, Any]) -> dict[str, Any]:
        self._counts[str(payload.get("api_name"))] += 1
        return self._inner.post(payload)


@contextlib.contextmanager
def counted_transport(counts: Counter[str]) -> Iterator[None]:
    """Route `panel build`'s fetches through a `CountingTransport` for the duration.

    `cli._panel_transport` is the one seam that command fetches through (its docstring: "tests
    replace this and everything above it runs for real"). Whatever it is when this is entered --
    the HTTP transport, or a test's scripted one -- is what the counter wraps, and it is put back
    on the way out.
    """
    original = cli._panel_transport

    def counting() -> TushareTransport:
        return CountingTransport(original(), counts)

    cli._panel_transport = counting
    try:
        yield
    finally:
        cli._panel_transport = original


@dataclass(frozen=True, slots=True, kw_only=True)
class Invocation:
    exit_code: int
    stdout: str
    stderr: str

    def payloads(self) -> list[Any]:
        """Every stdout line that is a JSON document, in order."""
        found: list[Any] = []
        for line in self.stdout.splitlines():
            if line.startswith(("{", "[")):
                with contextlib.suppress(ValueError):
                    found.append(json.loads(line))
        return found

    def reason(self) -> str:
        """The command's own account of a refusal: the last twelve lines of its stderr and stdout
        that are findings rather than status or progress (`READY`, `CHECK`, `INFO`, a clean
        gate's `UNVERIFIED`, and `panel build`'s `INCREMENTAL`/`BUDGET`/`FETCHING`/`WROTE` lines
        are left out, so the refusal, which comes last, is never cut), else its last lines."""
        routine = (
            "READY ",
            "CHECK ",
            "INFO ",
            "UNVERIFIED ",
            "CLEARED ",
            "INCREMENTAL ",
            "BUDGET ",
            "FETCHING ",
            "WROTE ",
            "SESSIONS ",
            "AS-OF ",
            "HALTS ",
        )
        findings = [
            line
            for line in (*self.stderr.splitlines(), *self.stdout.splitlines())
            if line.strip() and not line.startswith(routine)
        ]
        if findings:
            return " | ".join(findings[-12:])
        text = (self.stderr.strip() or self.stdout.strip()).splitlines()
        return " | ".join(text[-6:]) if text else f"exit {self.exit_code} with no output"


def invoke(arguments: Sequence[str]) -> Invocation:
    """Run one `openalpha` command in this process and capture what it printed.

    Through the Typer app itself, so every flag is parsed, every guard runs and every exit code
    is the command's own. Without standalone mode a deliberate `typer.Exit` comes back as its
    code and a usage error as a `ClickException`, which is reported with its own message.
    """
    command = get_command(cli.app)
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            result = command.main(
                args=list(arguments), prog_name="openalpha", standalone_mode=False
            )
            code = int(result) if isinstance(result, int) else 0
        except ClickException as error:
            print(error.format_message(), file=sys.stderr)
            code = error.exit_code
        except SystemExit as error:
            code = error.code if isinstance(error.code, int) else 1
    return Invocation(exit_code=code, stdout=out.getvalue(), stderr=err.getvalue())


# --- the journal -------------------------------------------------------------------------------


def journal_directory(runtime_dir: Path, registration: Registration) -> Path:
    return runtime_dir / JOURNAL_DIRECTORY / registration.config_id[:16]


def read_journal(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    body = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(body, dict) or body.get("schema") != DAILY_SELECTION_SCHEMA:
        raise StepFailedError("summary", f"{path} is not a {DAILY_SELECTION_SCHEMA} journal")
    return body


def write_journal(path: Path, body: Mapping[str, Any]) -> None:
    """Write the day's journal whole, through a temporary file, unless it already says this."""
    text = json.dumps(body, sort_keys=True, indent=2, ensure_ascii=False) + "\n"
    if path.is_file() and path.read_text(encoding="utf-8") == text:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.partial")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)


def previous_targets(directory: Path, session: date) -> tuple[date | None, dict[str, str]]:
    """The newest journalled targets before `session`: what the book held coming into today."""
    if not directory.is_dir():
        return None, {}
    earlier = sorted(
        (
            date.fromisoformat(path.stem)
            for path in directory.glob("????-??-??.json")
            if date.fromisoformat(path.stem) < session
        ),
        reverse=True,
    )
    for day in earlier:
        body = read_journal(directory / f"{day.isoformat()}.json")
        if body is not None and "result" in body:
            return day, dict(body["result"]["targets"]["weights"])
    return None, {}


@dataclass(frozen=True, slots=True, kw_only=True)
class JournalledDay:
    """One session the command completed, as its journal says (`V2-P6-011`, for `V2-P6-012`).

    `decision` is step 6's: `rebalanced`, `held` (the source ranked nothing, so the book kept
    yesterday's) or `not a rebalance day`. `record_id` is the record step 7 registered or found,
    `None` on a day the source held -- which is evidence the book did not rebalance there, not a
    gap (`source_held`). `weights` are the targets the command printed.
    """

    session: date
    decision: str
    reason: str
    source_held: bool
    record_id: str | None
    weights: Mapping[str, str]
    as_of: datetime
    """The instant the day's run pinned its clock to: what a held day is re-scored at."""


def journalled_days(directory: Path) -> tuple[JournalledDay, ...]:
    """Every session the command completed under `directory` (`journal_directory`), ascending.

    A journal without a `result` -- a run refused or stopped before its summary -- is not a day
    the command listed anything on, and is left out: the next run catches its rebalance up
    (`Schedule.due`), and that run's journal says so.
    """
    if not directory.is_dir():
        return ()
    days: list[JournalledDay] = []
    for path in sorted(directory.glob("????-??-??.json")):
        body = read_journal(path)
        if body is None or "result" not in body:
            continue
        try:
            result = body["result"]
            targets, prediction = result["targets"], result["prediction"]
            days.append(
                JournalledDay(
                    session=date.fromisoformat(result["session"]),
                    decision=str(targets["decision"]),
                    reason=str(targets["reason"]),
                    source_held=bool(result["candidates"]["held"]),
                    record_id=(
                        str(prediction["record_id"]) if prediction.get("registered") else None
                    ),
                    weights=dict(targets["weights"]),
                    as_of=datetime.fromisoformat(str(result["as_of"])),
                )
            )
        except (KeyError, TypeError, AttributeError) as error:
            raise StepFailedError(
                "summary", f"{path} has no {error} where the journal of a completed day has one"
            ) from error
    return tuple(days)


def journalled_rebalances(directory: Path) -> tuple[tuple[date, str], ...]:
    """The sessions the journal says the command rebalanced on, each with the record it used.

    **The journal's word only**: a local file an edit could move a rebalance in. The forward
    report prices `forward_rebalances`, which re-derives these days from the prediction store
    and holds the journal to them. It differs from the fixed grid exactly where the command did:
    a day the source held is not a rebalance, and a missed or refused run's rebalance is made at
    the next run. A rebalance journalled without a record is refused.
    """
    rebalances: list[tuple[date, str]] = []
    for day in journalled_days(directory):
        if day.decision != "rebalanced":
            continue
        if day.record_id is None:
            raise StepFailedError(
                "summary",
                f"the journal of {day.session.isoformat()} rebalanced with no record registered; "
                "the candidate book it listed cannot be priced from the prediction store",
            )
        rebalances.append((day.session, day.record_id))
    return tuple(rebalances)


_LOG: Final = logging.getLogger("openalpha.daily_selection")

PROVENANCE_DIRECTORY: Final[str] = "daily_selection_provenance"
"""Under the runtime directory: each record's input provenance, `<digest>.json`, write-once."""

VERDICT_DIRECTORY: Final[str] = "reports/record_verdicts"
"""Under the runtime directory: each record's `verified` verdict, `<record>.<provenance>.json`,
write-once -- a record is recomputed once, and again only when its inputs have changed."""


def _request_at(registration: Registration) -> Callable[[date, datetime], StrategyRequest]:
    def request_for(day: date, as_of: datetime) -> StrategyRequest:
        return day_request(registration.config, day=day, as_of=as_of)

    return request_for


def _write_once(path: Path, body: Mapping[str, Any]) -> None:
    """Write a content-addressed document, or check the one already there says the same."""
    text = json.dumps(body, sort_keys=True, indent=2, ensure_ascii=False) + "\n"
    if path.is_file():
        if path.read_text(encoding="utf-8") != text:
            raise StepFailedError("prediction", f"{path} is held and says something else")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.partial")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)


def write_provenance(runtime_dir: Path, provenance: InputProvenance) -> Path:
    """File a record's input provenance before the record (`V2-P6-011` round 11).

    Its own small content-addressed file rather than a field of the record: a
    `PredictionRecord` is `extra="forbid"` under `alpha-prediction-record/v1`, and a field would
    be a contract change (hard rule 3); `supersedes` and the batch are taken. The file is named by
    the digest of what it says, written once and never rewritten -- the prediction store's own
    discipline -- and names the batch it was written for, so it binds to exactly one record.
    """
    path = runtime_dir / PROVENANCE_DIRECTORY / f"{provenance.digest}.json"
    _write_once(path, provenance.document())
    return path


def provenance_lookup(runtime_dir: Path, registration_sha256: str) -> ProvenanceLookup:
    """The provenance this registration's daily runs wrote for a record's batch, or `None`."""
    directory = runtime_dir / PROVENANCE_DIRECTORY
    held: dict[tuple[date, str], InputProvenance] = {}
    for path in sorted(directory.glob("*.json")) if directory.is_dir() else ():
        try:
            provenance = InputProvenance.from_document(json.loads(path.read_text(encoding="utf-8")))
        except (
            ValueError,
            TypeError,
            KeyError,
            IndexError,
            AttributeError,
            StrategyRegistrationError,
        ) as error:
            raise StepFailedError(
                "summary", f"{path} is not a provenance this command can read ({error!r})"
            ) from error
        if provenance.digest != path.stem:
            raise StepFailedError("summary", f"{path} is not what its name says it is")
        if provenance.registration_sha256 != registration_sha256:
            continue
        key = (provenance.session, provenance.batch_digest)
        if key in held:
            raise StepFailedError(
                "summary",
                f"two provenance files name the batch {provenance.batch_digest} of "
                f"{provenance.session.isoformat()}: {held[key].digest}.json and {path.name}; a "
                "filing has one, and neither can be told to be the one its run wrote",
            )
        held[key] = provenance

    def lookup(record: PredictionRecord) -> InputProvenance | None:
        day = record.batch.as_of.astimezone(SHANGHAI).date()
        return held.get((day, batch_digest(record.batch)))

    return lookup


class FileVerdicts:
    """`strategy_registration.VerdictCache` over `VERDICT_DIRECTORY`: one write-once file per
    verified record, provenance and partition set -- `<record>.<provenance>.<partitions>.json`.

    **A cache, inside the threat model and no further.** It saves recomputing a record whose
    inputs have not moved; it is not evidence. A kept verdict is trusted only while every
    partition it was verified under still hashes as it did (`RecordCheck` rechecks them before
    trusting it), so a bug or an upstream correction sends the record back to verification. Like
    every artifact here it is a local file: someone who controls this disk can write a verdict
    as easily as a record, and nothing local defends against that (`INTEGRITY`).
    """

    def __init__(self, runtime_dir: Path, *, clock: Callable[[], datetime]) -> None:
        self._directory = runtime_dir / VERDICT_DIRECTORY
        self._clock = clock

    def verified_under(
        self, record_id: str, provenance_digest: str
    ) -> tuple[tuple[InputPartition, ...], ...]:
        if not self._directory.is_dir():
            return ()
        kept = []
        for path in sorted(self._directory.glob(f"{record_id}.{provenance_digest}.*.json")):
            try:
                body = json.loads(path.read_text(encoding="utf-8"))
                if body["verdict"] != "verified":
                    raise ValueError(f"verdict {body['verdict']!r}")
                partitions = tuple(
                    InputPartition(
                        dataset=str(item[0]),
                        year=int(item[1]),
                        content_hash=str(item[2]),
                        rows_digest=str(item[3]),
                    )
                    for item in body["partitions"]
                )
            except (ValueError, TypeError, KeyError, IndexError, AttributeError):
                # Accidental corruption is inside the threat model, and a cache entry that does
                # not parse is a miss: the record is recomputed, and the file is named.
                _LOG.warning(
                    "ignoring the unreadable verdict file %s; the record is recomputed", path.name
                )
                continue
            kept.append(partitions)
        return tuple(kept)

    def keep(
        self, record_id: str, provenance_digest: str, partitions: tuple[InputPartition, ...]
    ) -> None:
        listed = [
            [item.dataset, item.year, item.content_hash, item.rows_digest] for item in partitions
        ]
        digest = hashlib.sha256(
            json.dumps(listed, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        path = self._directory / f"{record_id}.{provenance_digest}.{digest}.json"
        if path.is_file():
            return
        _write_once(
            path,
            {
                "record_id": record_id,
                "provenance": provenance_digest,
                "partitions": listed,
                "verdict": "verified",
                "verified_at": self._clock().isoformat(),
            },
        )


def forward_record_check(
    runtime_dir: Path, registration: Registration, *, as_of: datetime
) -> RecordCheck:
    """The check a forward book holds a record registered after its signal instant to
    (`strategy_view.backtest_strategy(verify_late=...)`): bound to this registration (a
    walk-forward record through its provenance), recomputed at its own filing time from the
    panel store under `runtime_dir`, and -- where an input it read was corrected since --
    admitted as `UNVERIFIABLE` and listed on the check's `unverifiable`. Verified verdicts are
    kept under `VERDICT_DIRECTORY`."""
    return RecordCheck(
        panel_store(runtime_dir),
        registration.declared,
        request_for=_request_at(registration),
        anchor=_registered_anchor(registration),
        provenance_for=provenance_lookup(runtime_dir, registration.sha256),
        verdicts=FileVerdicts(runtime_dir, clock=lambda: as_of),
    )


@dataclass(frozen=True, slots=True, kw_only=True)
class ForwardSchedule:
    """The forward book's days as the store witnesses them (`forward_rebalances`).

    `first_record` is where the witnessed period starts: the earliest session carrying an on-time
    record bound to the registration. `unprovable_holds` are the journal's held days before it --
    a hold leaves no record, so the store cannot tell one from a day the command never ran, and
    they are stated rather than counted.
    """

    rebalances: tuple[tuple[date, str], ...]
    days: tuple[WitnessedDay, ...]
    first_record: date
    unprovable_holds: tuple[date, ...]


def forward_rebalances(
    runtime_dir: Path, registration: Registration, *, through: date, as_of: datetime
) -> ForwardSchedule:
    """The sessions the command rebalanced on through `through`, each with its record, as the
    append-only prediction store witnesses them (`V2-P6-011`, for `V2-P6-012`).

    `strategy_registration.witnessed_days` re-derives every decision from the store, from the
    **store's** first record: the journal cannot choose where the forward book starts. A journal
    starting later is refused (a first day deleted); a journal day before the first record may
    only be a hold, and those are returned as `unprovable_holds`. From the first record on, a
    held day counts only when the journal completed it holding and the configuration, scored
    again, holds. Every completed day is then held to the journal -- decision and record -- and a
    difference refuses the book naming the day.
    """
    journalled = [
        day
        for day in journalled_days(journal_directory(runtime_dir, registration))
        if day.session <= through
    ]
    store = panel_store(runtime_dir)
    anchor = _registered_anchor(registration)
    request_for = _request_at(registration)
    # The next year too, when stored: a record on the year's last session is cut off at 09:15 on
    # the next year's first (`registration_cutoff`), as `_outcome_calendar` places its outcome.
    calendar = _stored_calendar(
        store,
        request_for(anchor, as_of).exchange,
        tuple(range(anchor.year, through.year + 2)),
        as_of,
    )
    if calendar is None:
        raise StepFailedError(
            "summary", f"the calendar from {anchor.year} to {through.year} is not stored"
        )
    records = build_storage(runtime_dir=runtime_dir, clock=lambda: as_of).prediction_store
    try:
        witnessed = witnessed_days(
            store,
            records,
            registration.declared,
            request_for=request_for,
            as_of=as_of,
            anchor=anchor,
            calendar=calendar,
            through=through,
            journalled_holds={
                day.session: day.as_of for day in journalled if day.decision == "held"
            },
            provenance_for=provenance_lookup(runtime_dir, registration.sha256),
        )
    except StrategyRegistrationError as error:
        raise StepFailedError("summary", str(error)) from error
    if not witnessed:
        raise StepFailedError(
            "summary",
            f"the prediction store holds no on-time record of this registration by {through}",
        )
    first = witnessed[0].session
    if not journalled or journalled[0].session > first:
        raise StepFailedError(
            "summary",
            f"the store's first record of this registration is of {first.isoformat()} and the "
            f"journal starts {journalled[0].session.isoformat() if journalled else 'nowhere'}; "
            "a journal missing its first days would move where the forward book starts",
        )
    before = [day for day in journalled if day.session < first]
    unheld = [day.session.isoformat() for day in before if day.decision != "held"]
    if unheld:
        raise StepFailedError(
            "summary",
            f"the journal says the command {unheld} ranked before the store's first record "
            f"({first.isoformat()}); the store is the witness",
        )
    derived = {day.session: (day.decision, day.record_id) for day in witnessed}
    written = {
        day.session: (day.decision, day.record_id) for day in journalled if day.session >= first
    }
    for session in sorted(set(derived) | set(written)):
        if derived.get(session) != written.get(session):
            raise StepFailedError(
                "summary",
                f"the journal of {session.isoformat()} says {written.get(session)} and the store "
                f"witnesses {derived.get(session)} (decision, record); the store is the witness, "
                "and a journal that disagrees with it is not a book to price",
            )
    return ForwardSchedule(
        rebalances=tuple(
            (day.session, day.record_id)
            for day in witnessed
            if day.decision == "rebalanced" and day.record_id is not None
        ),
        days=witnessed,
        first_record=first,
        unprovable_holds=tuple(day.session for day in before),
    )


INTEGRITY: Final[str] = (
    "integrity: every artifact here -- the prediction store and its recorded_at clock, the "
    "journal, the input provenance, the kept verdicts -- is a local file. The checks defend "
    "against honest operation, bugs, accidental corruption and upstream corrections; they do not "
    "defend against deliberate tampering by whoever controls this disk, which needs an external "
    "append-only witness"
)
"""The threat model, stated wherever a forward book is reported (`V2-P6-011` round 12)."""


def _book_statistics(periods: Sequence[Any]) -> dict[str, Any]:
    """Compounded net and per-benchmark returns and the mean period net, over `periods`."""
    net = Decimal(1)
    benchmarks: dict[str, Decimal] = {}
    for period in periods:
        net *= Decimal(1) + period.net_return
        for name, value in period.benchmark_returns.items():
            benchmarks[name] = benchmarks.get(name, Decimal(1)) * (Decimal(1) + value)
    count = len(periods)
    total = sum((period.net_return for period in periods), Decimal(0))
    return {
        "periods": count,
        "compounded_net_return": f"{(net - 1).quantize(_WEIGHT_QUANTUM):f}",
        "mean_period_net_return": (
            None if not count else f"{(total / count).quantize(_WEIGHT_QUANTUM):f}"
        ),
        "compounded_benchmark_returns": {
            name: f"{(value - 1).quantize(_WEIGHT_QUANTUM):f}"
            for name, value in sorted(benchmarks.items())
        },
    }


SIGNIFICANCE_TESTED: Final[str] = (
    "grid.strategy_result, the holdout's own: a sign-flip test of the non-overlapping periods' "
    "net excess over the registration's excess_benchmark (one-sided p), over complete periods "
    "only -- a period of at least the registered rebalance interval in sessions. A shorter "
    "period (the book's open last period, a catch-up's) is shown in the statistics and not "
    "tested. The periods are the sessions the command rebalanced on, so the tested ones need "
    "not be of equal length: each one's session count is shown. 000905.SH is reported beside "
    "the test from the same result's reported_* keys and tests nothing (the protocol's rule)"
)
"""What the significance rows are, stated on every forward report."""

_SIGNIFICANCE_KEYS: Final[tuple[str, ...]] = (
    "excess_benchmark",
    "period_count",
    "excluded_incomplete_periods",
    "net_excess",
    "mean_net_excess",
    "p_excess",
    "p_excess_one_sided",
    "p_excess_exact",
    "p_excess_sign_patterns",
)


def registered_excess_benchmark(settings: Mapping[str, Any]) -> str:
    """The benchmark a registration's forward excess is tested against: the name its settings
    recorded, which a registration made before `V2-P6-024` gives as `equal_weight_all_a` and keeps
    reading under that name. A registration that recorded none is refused: no default stands in
    for it, as none does for `bootstrap_samples` (`_registered_count`), because the protocol's
    default is not what that registration was measured against."""
    value = settings.get("excess_benchmark")
    if not isinstance(value, str) or not value.strip():
        raise StepFailedError(
            "summary",
            f"the registration's settings give excess_benchmark as {value!r}; the forward book "
            "is tested against the benchmark the registration recorded, and without one there is "
            "none to test it against",
        )
    return value


def priced_benchmarks(
    benchmarks: Sequence[str] | None, settings: Mapping[str, Any]
) -> tuple[str, ...]:
    """The benchmarks a forward book is priced with: the configuration's own (the protocol's two
    when it names none) and the registration's excess benchmark beside them when they lack it --
    so a registration whose recorded benchmark is no longer a default (`V2-P6-024`) is still
    tested against it rather than refused for a series nobody priced."""
    named = tuple(PROTOCOL_BENCHMARKS if benchmarks is None else benchmarks)
    return tuple(dict.fromkeys((*named, registered_excess_benchmark(settings))))


def _registered_count(settings: Mapping[str, Any], key: str) -> int:
    """`settings[key]` as an integer, refusing a missing one and a bool -- which Python counts
    as an int and no registration means as one (`Registration.seed`'s rule)."""
    value = settings.get(key)
    if not isinstance(value, int) or isinstance(value, bool):
        raise StepFailedError(
            "summary",
            f"the registration's settings give {key} as {value!r}; the forward book is tested "
            "under the registered measurement settings, and without an integer there is none to "
            "test it under",
        )
    return value


def _significance(book: Any, *, benchmark: str, samples: int, seed: int) -> dict[str, Any]:
    """`grid.strategy_result` of `book` -- one call, one source: the test against `benchmark`
    and, from the same result's `reported_*` keys, 000905.SH beside it (`grid.REPORTED_BENCHMARK`,
    which tests nothing). A book the test refuses (no complete period, a benchmark it has no
    return for) says why."""
    try:
        result = grid.strategy_result(
            book, excess_benchmark=benchmark, bootstrap_samples=samples, random_seed=seed
        )
    except StrategyBacktestError as error:
        return {benchmark: {"refused": str(error)}}
    complete = cast(list[bool], result["period_complete"])
    sessions = cast(list[int], result["period_sessions"])
    tested: dict[str, Any] = {key: result[key] for key in _SIGNIFICANCE_KEYS}
    tested["tested_periods"] = sum(complete)
    tested["tested_period_sessions"] = [
        count for count, whole in zip(sessions, complete, strict=True) if whole
    ]
    tested["bootstrap_samples"] = samples
    tested["random_seed"] = seed
    reported: dict[str, Any]
    if "reported_net_excess" in result:
        reported = {
            "reported": True,
            "net_excess": result["reported_net_excess"],
            "mean_net_excess": result["reported_mean_net_excess"],
            "tested_periods": tested["tested_periods"],
        }
    else:
        reported = {
            "refused": f"the book priced no {grid.REPORTED_BENCHMARK} return for every period"
        }
    return {benchmark: tested, grid.REPORTED_BENCHMARK: reported}


def forward_summary(
    book: Any,
    check: RecordCheck,
    schedule: ForwardSchedule,
    *,
    settings: Mapping[str, Any],
) -> dict[str, Any]:
    """What a forward report shows about the evidence behind its book (`V2-P6-012`).

    The book is priced as listed -- every on-time bound record, `UNVERIFIABLE` ones
    included -- and its statistics are computed twice: over every period, and over the periods
    whose record verified, excluding those an `UNVERIFIABLE` record opened. Both are shown, with
    the count and each corrected partition, so a reader sees whether the corrections matter;
    `unprovable_holds` and `INTEGRITY` are stated beside them.

    Each set is also tested as the holdout tested it (`grid.strategy_result`, `SIGNIFICANCE_
    TESTED`): net excess against the registration's `excess_benchmark`, with 000905.SH reported
    beside it from the same result, under the registration's own `bootstrap_samples` and
    `random_seed` -- `settings`, which has no default: a forward p-value under other settings
    than the registered ones is not the registered test.
    """
    samples = _registered_count(settings, "bootstrap_samples")
    seed = _registered_count(settings, "random_seed")
    primary = registered_excess_benchmark(settings)
    flagged = dict(check.unverifiable)
    opened = {session: record for session, record in schedule.rebalances}
    excluded = {session for session, record in opened.items() if record in flagged}
    kept = [period for period in book.periods if period.start not in excluded]
    subset = book.model_copy(update={"periods": tuple(kept)}) if book.periods else book
    return {
        UNVERIFIABLE: {
            "count": len(flagged),
            "records": [
                {"record_id": record, "corrected": list(changes)}
                for record, changes in sorted(flagged.items())
            ],
        },
        "statistics": {
            "all_periods": _book_statistics(book.periods),
            "excluding_unverifiable": _book_statistics(kept),
        },
        "significance": {
            "tested": SIGNIFICANCE_TESTED,
            "all_periods": _significance(book, benchmark=primary, samples=samples, seed=seed),
            "excluding_unverifiable": _significance(
                subset, benchmark=primary, samples=samples, seed=seed
            ),
        },
        "unprovable_holds": [day.isoformat() for day in schedule.unprovable_holds],
        "integrity": INTEGRITY,
    }


def forward_summary_lines(summary: Mapping[str, Any]) -> list[str]:
    """`forward_summary` as the lines a report prints, the unverifiable records first."""
    flagged = summary[UNVERIFIABLE]
    lines = [f"{UNVERIFIABLE}: {flagged['count']} record(s)"]
    for entry in flagged["records"]:
        lines.append(f"  {entry['record_id']} corrected: {', '.join(entry['corrected'])}")
    labels = (
        ("all_periods", "headline: the candidate book as listed"),
        (
            "excluding_unverifiable",
            f"sensitivity: the same book's periods excluding {flagged['count']} unverifiable "
            "records (a subset of one path, not a re-run; later periods keep the positions and "
            "costs those periods left)",
        ),
    )
    for name, label in labels:
        stats = summary["statistics"][name]
        lines.append(
            f"{label}: {stats['periods']} period(s), compounded net "
            f"{stats['compounded_net_return']}, mean period net {stats['mean_period_net_return']}, "
            f"benchmarks {json.dumps(stats['compounded_benchmark_returns'], sort_keys=True)}"
        )
    significance = summary["significance"]
    lines.append(f"significance: {significance['tested']}")
    for name, label in labels:
        for benchmark, shown in significance[name].items():
            head = f"  {label.split(':')[0]} vs {benchmark}: "
            if "refused" in shown:
                lines.append(head + f"not tested -- {shown['refused']}")
                continue
            if shown.get("reported"):
                lines.append(
                    head + f"reported beside the test, tests nothing; mean net excess "
                    f"{shown['mean_net_excess']} over the same {shown['tested_periods']} complete "
                    "period(s)"
                )
                continue
            draws = (
                "exact"
                if shown["p_excess_exact"]
                else f"{shown['p_excess_sign_patterns']} sign patterns, seed {shown['random_seed']}"
            )
            lines.append(
                head + f"{shown['tested_periods']} complete period(s) tested (sessions "
                f"{shown['tested_period_sessions']}), "
                f"{shown['excluded_incomplete_periods']} incomplete shown and not tested; "
                f"mean net excess {shown['mean_net_excess']}, one-sided p "
                f"{shown['p_excess_one_sided']} (two-sided {shown['p_excess']}, {draws})"
            )
    if summary["unprovable_holds"]:
        lines.append(
            "holds before the first record (unprovable, not counted): "
            + ", ".join(summary["unprovable_holds"])
        )
    lines.append(summary["integrity"])
    return lines


# --- the day's schedule --------------------------------------------------------------------------


def schedule_of(
    calendar: TradingCalendar,
    *,
    anchor: date,
    session: date,
    every: int,
    previous: date | None,
) -> Schedule:
    """The session's place on the rebalance schedule counted from `anchor`
    (`strategy_registration.schedule_of`, which `witnessed_days` reads the store with)."""
    try:
        return registered_schedule_of(
            calendar, anchor=anchor, session=session, every=every, previous=previous
        )
    except StrategyRegistrationError as error:
        raise StepFailedError("panel update", str(error)) from error


def industry_day(request: StrategyRequest, builds: Sequence[TierBuild], schedule: Schedule) -> bool:
    """Whether the day's scoring reads industry memberships.

    A neutralized tier reads the whole cross section's industries every day it is built. An
    industry cap reads them only in the rebalance decision, so only on a day a rebalance is due.
    On any other day nothing the day computes reads a membership.
    """
    capped = request.spec.max_industry_weight is not None
    return reads_neutralized(builds) or (capped and schedule.due)


# --- the steps ---------------------------------------------------------------------------------


@dataclass(slots=True, kw_only=True)
class DailyOptions:
    runtime_dir: Path
    registration: Path = DEFAULT_REGISTRATION
    repo: Path = REPOSITORY
    as_of: datetime | None = None
    targets: tuple[str, ...] | None = None
    full_update: bool = False
    max_staleness_days: int = DEFAULT_MAX_STALENESS_DAYS
    json_output: bool = False
    top: int | None = None
    """`--top`: how many candidates the P6 summary prints (`main` reads it there); a survivor day
    refuses it (`_refuse_p6_only_options`)."""


def _stored_calendar(
    store: PanelStore, exchange: str, years: Sequence[int], as_of: datetime
) -> TradingCalendar | None:
    """The stored calendar of those of `years` the store holds, or `None` when it holds none or
    they cannot be read at `as_of` (a refusal step 2's calendar build is the remedy for)."""
    held = set(store.registered_years(TRADING_CALENDAR_DATASET))
    stored = tuple(year for year in years if year in held)
    if not stored:
        return None
    try:
        return load_trading_calendar(store, exchange=exchange, years=stored, as_of=as_of)
    except (TradingCalendarError, PanelStorageError):
        return None


def _stored_session(store: PanelStore, exchange: str, as_of: datetime) -> date | None:
    """The newest session published at `as_of`, by the stored calendar of `as_of`'s year and the
    year before it -- so 1 January, and every day of a new year before its first session closes,
    find the previous year's last session. `None` when neither year's calendar is stored."""
    year = as_of.astimezone(SHANGHAI).year
    calendar = _stored_calendar(store, exchange, (year - 1, year), as_of)
    if calendar is None:
        return None
    try:
        return newest_published_session(calendar, as_of=as_of)
    except TradingCalendarError:
        return None


def _panel_build(
    runtime_dir: Path,
    *,
    year: int | Sequence[int],
    as_of: datetime,
    exchange: str,
    targets: Sequence[str],
) -> Invocation:
    arguments = ["panel", "build", "--runtime-dir", str(runtime_dir)]
    for one in (year,) if isinstance(year, int) else year:
        arguments += ["--year", str(one)]
    arguments += ["--as-of", as_of.isoformat(), "--exchange", exchange, "--incremental", "--json"]
    if INDUSTRY_MEMBERSHIP_TARGET in targets:
        # The two whole-market states (~4 requests) checked against the stored corpus, and the
        # 62 slices only when that check fails or there is no corpus yet -- see `panel build
        # --industry-sweep`.
        arguments += ["--industry-sweep", "states"]
    for target in targets:
        arguments += ["--dataset", target]
    return invoke(arguments)


def industry_sweep(budget: Sequence[str]) -> str | None:
    """How the day's `index_member_all` was fetched, read off `panel build`'s `BUDGET` lines, or
    `None` when it was not.

    The whole-market states on an ordinary day. The l1_code slices when there was no stored corpus
    to check against, and "FELL BACK" when the states were fetched and failed the self-check --
    62 more requests, which a persistent fallback would spend every industry day without anything
    else saying so; the printed summary carries this line for that reason.
    """
    prefix = f"BUDGET {INDUSTRY_MEMBERSHIP_TARGET} "
    lines = [line for line in budget if line.startswith(prefix)]
    if not lines:
        return None
    slices = next((line for line in lines if "l1_code slices" in line), None)
    if slices is None:
        return "whole-market states"
    count = slices[len(prefix) :].split(" ", 1)[0]
    _head, _marker, reason = slices.partition("not --year; ")
    reason = reason.removesuffix(")")
    if len(lines) > 1:
        return f"FELL BACK to the l1_code slices ({count} more requests): {reason}"
    return f"the l1_code slices ({count} requests): {reason or 'the default sweep'}"


def _budget(built: Invocation) -> list[str]:
    """The `BUDGET` lines `panel build` states before each fetch loop: the code's own count."""
    return [
        line
        for line in (*built.stderr.splitlines(), *built.stdout.splitlines())
        if line.startswith("BUDGET ")
    ]


FINANCIAL_INDICATOR_TARGET: Final[str] = "fina_indicator"
ANNOUNCEMENT_MONTH_TARGETS: Final[frozenset[str]] = frozenset(
    {"income", "balancesheet", "cashflow"}
)
"""Swept by announcement month under `panel build --incremental` (`V2-P6-018`): the trailing
months and a weekly rotation, carrying the rest. In January the trailing months reach into last
year's partition, so the update builds that year too."""


def financial_indicator_years(session_year: int, as_of: datetime) -> tuple[int, ...]:
    """The report-period years a day's `fina_indicator` sweep covers.

    `fina_indicator` is swept by report-period year, not by announcement year: a period year is
    its four quarter ends, each window opening on its period's last day. The year before the
    session's is always among them -- its annual report is announced in the session's year (by
    30 April) and every revision of it is served in that period's window -- and the session's
    own year once its first period (31 March) has ended. Before that it has no open window, and
    `panel build` refuses a period year none of whose windows served a registered filing: asked
    for the session's year alone, every day from 1 January to 30 March would be refused.
    """
    opens = datetime(session_year, 3, 31, tzinfo=SHANGHAI)
    return (session_year - 1, session_year) if as_of >= opens else (session_year - 1,)


def next_year_calendar_needed(calendar: TradingCalendar, *, session: date, horizon: int) -> bool:
    """Whether the session's outcome window -- the next session and `horizon` more -- or its
    registration cutoff reaches past the last session the session's year's calendar holds."""
    later = [day for day in calendar.trading_days if session < day <= date(session.year, 12, 31)]
    return len(later) < horizon + 1


@dataclass(frozen=True, slots=True, kw_only=True)
class SurvivorPanelUpdate:
    """What the survivor form's step 2 adds to the P6 one (`V2-P7-016`); `update_panel` without
    it is the P6 step, unchanged.

    `max_catch_up_sessions` (the registration's, `survivor_panel_update`): before any request,
    a store more sessions behind the day than this is refused, with the `panel build` that
    fetches the oldest batch, instead of spending five to seven requests a session inside one
    scheduled run. Before that, whatever the bound, a year before whose session-scoped
    partitions stop short of its last session is refused with the `panel build` that completes
    it (`previous_year_sessions_missing`), and a session's year with no stored calendar, on which
    the sessions are counted, with the `panel build` that stores it (`_calendar_refusal`). And
    the year before's name corpus is built again while its stored build is from before that year
    ended (`names_built_before_year_end`).
    """

    max_catch_up_sessions: int

    def __post_init__(self) -> None:
        bound = self.max_catch_up_sessions
        if type(bound) is not int or bound < 0:
            raise ValueError(
                f"max_catch_up_sessions must be a whole number of sessions, 0 or more, not "
                f"{bound!r}"
            )


def panel_sessions_to_catch_up(
    runtime_dir: Path,
    *,
    session: date,
    as_of: datetime,
    exchange: str,
    targets: Sequence[str],
) -> tuple[date, ...]:
    """The sessions step 2 would fetch for `targets` beyond an ordinary day's two, oldest first.

    `panel build --incremental` fetches each session-scoped target (`cli.INCREMENTAL_CENSUS`)
    from its stored horizon in the session's year -- the stored last session, again -- through
    the session, and the whole year up to it when that year holds no partition yet
    (`cli._incremental_start`). So the sessions beyond the overlap and the day are the open ones
    of the session's year after the earliest of those horizons and before the session, counted
    on the stored calendar of that year. When one of them holds nothing of the year yet, every
    session of the year before the session is counted: that target fetches them and the day,
    with no stored session to fetch again, so the count is then one more than the sessions it
    fetches beyond two (the refusal states what is fetched). Only that year: the step builds no
    other year's sessions, and a year before that stops short of its last session is refused on
    its own (`previous_year_sessions_missing`).

    The count is made on that year's stored calendar, so a year whose calendar is not stored is
    refused with the `panel build` that stores it (`_calendar_refusal`) -- a January restart
    after `previous_year_sessions_missing`'s remedy, which builds no `trade_cal`, is there.
    """
    census = _session_scoped_census(targets)
    if not census:
        return ()
    store = panel_store(runtime_dir)
    calendar = _stored_calendar(store, exchange, (session.year,), as_of)
    if calendar is None:
        if session.year not in store.registered_years(TRADING_CALENDAR_DATASET):
            raise StepFailedError(
                "panel update", _calendar_refusal(runtime_dir, session=session, exchange=exchange)
            )
        raise StepFailedError(
            "panel update",
            f"the {exchange} calendar of {session.year}, on which the sessions this update would "
            "fetch are counted, is stored but cannot be read",
        )
    start = _earliest_stored_horizon(store, census, session.year)
    return tuple(
        day
        for day in calendar.trading_days
        if day.year == session.year and day < session and (start is None or day > start)
    )


def previous_year_sessions_missing(
    runtime_dir: Path,
    *,
    session: date,
    as_of: datetime,
    exchange: str,
    targets: Sequence[str],
) -> tuple[date, ...]:
    """The open sessions of the year before the session's that its stored session-scoped
    partitions do not reach, oldest first: those after the earliest of their horizons through
    that year's last session, on that year's stored calendar (`V2-P7-016`).

    Step 2 builds the session's year only, so a session of the year before that a missed run left
    unfetched is never fetched by it. And the session's year's `stk_limit` build judges each
    security's first pair of the year against the last stored close of the year before
    (`cli._judge_return_paths`), which is then not the session before it: every disputed pair is
    fetched again one security at a time, four requests each and no cap, and the doctor's
    first-of-year check then blocks on the gap. Empty when the year before holds none of these
    partitions -- a store first built in the session's year has no tail to complete, and that
    judgement reads the year before only when it is stored."""
    census = _session_scoped_census(targets)
    year = session.year - 1
    if not census:
        return ()
    store = panel_store(runtime_dir)
    if not any(year in store.registered_years(dataset) for dataset in census):
        return ()
    calendar = _stored_calendar(store, exchange, (year,), as_of)
    if calendar is None:
        raise StepFailedError(
            "panel update",
            f"the {exchange} calendar of {year}, on which that year's stored session-scoped "
            "partitions are checked to reach its last session, is not stored or cannot be read",
        )
    start = _earliest_stored_horizon(store, census, year)
    return tuple(
        day for day in calendar.trading_days if day.year == year and (start is None or day > start)
    )


def _session_scoped_targets(targets: Sequence[str]) -> list[str]:
    """The targets among `targets` that `panel build --incremental` slices by session
    (`cli.INCREMENTAL_CENSUS`), in `cli.PANEL_BUILD_TARGETS`' order."""
    return [
        target
        for target in cli.PANEL_BUILD_TARGETS
        if target in targets and target in cli.INCREMENTAL_CENSUS
    ]


def _session_scoped_census(targets: Sequence[str]) -> list[str]:
    """The stored datasets whose horizons say where those targets' slices start."""
    return [
        dataset
        for target in _session_scoped_targets(targets)
        for dataset in cli.INCREMENTAL_CENSUS[target]
    ]


def _earliest_stored_horizon(store: PanelStore, census: Sequence[str], year: int) -> date | None:
    """The earliest last session the `census` partitions of `year` cover, or `None` when one of
    them is not stored or cannot say -- a target `panel build --incremental` then fetches whole,
    from the year's first session (`cli._incremental_start`)."""
    horizons = [cli._stored_horizon(store, dataset, year) for dataset in census]
    stored = [horizon for horizon in horizons if horizon is not None]
    return min(stored) if len(stored) == len(horizons) else None


def _panel_build_command(
    runtime_dir: Path,
    *,
    year: int,
    through: date,
    exchange: str,
    targets: Sequence[str],
    incremental: bool = True,
    session_scoped: bool = True,
) -> list[str]:
    """The `panel build` a refusal prints: the session-scoped targets together (so their horizons
    move as one, `cli._refuse_split_horizon`), for `year`, pinned at `through`'s publication
    instant; `--incremental` unless `incremental` is false (the full rebuild of those targets).

    `session_scoped` false names `targets` as they are given instead of the session-scoped ones
    among them: the calendar's remedy, `trade_cal` being none of those (`_calendar_refusal`)."""
    arguments = ["openalpha", "panel", "build", "--runtime-dir", str(runtime_dir)]
    arguments += ["--year", str(year)]
    arguments += ["--as-of", session_publication_instant(through).astimezone(UTC).isoformat()]
    arguments += ["--exchange", exchange]
    if incremental:
        arguments.append("--incremental")
    for target in _session_scoped_targets(targets) if session_scoped else targets:
        arguments += ["--dataset", target]
    return arguments


def _calendar_refusal(runtime_dir: Path, *, session: date, exchange: str) -> str:
    """Why step 2 fetched nothing when the session's year has no stored calendar, and the `panel
    build` that stores it: `trade_cal` alone, for the session's year, pinned at the session's own
    publication instant (readable at the day's `as_of`, which is no earlier), and not incremental
    -- a calendar is one request a year, written whole.

    The step counts the sessions it would catch up on, on that calendar, before it builds
    anything, so it cannot store the calendar itself. A store last run before a run stored the
    next year's (`next_year_calendar_needed`) lacks it on the year's first session, and
    `previous_year_sessions_missing`'s remedy -- the session-scoped targets of the year before --
    fetches no `trade_cal`."""
    year = session.year
    arguments = _panel_build_command(
        runtime_dir,
        year=year,
        through=session,
        exchange=exchange,
        targets=(TRADING_CALENDAR_DATASET,),
        incremental=False,
        session_scoped=False,
    )
    return "\n".join(
        [
            f"the {exchange} calendar of {year}, on which the sessions this update would fetch "
            "are counted, is not stored, and this step counts them before it builds anything; "
            "nothing was fetched. No earlier run stored it: a run stores next year's calendar "
            "only once its outcome window reaches into that year. Store it, pinned at "
            f"{session.isoformat()}'s publication, with:",
            "  " + shlex.join(arguments),
            "then rerun this command",
        ]
    )


def _previous_year_refusal(
    runtime_dir: Path,
    *,
    missing: Sequence[date],
    session: date,
    exchange: str,
    targets: Sequence[str],
) -> str:
    """Why step 2 fetched nothing, and the `panel build` that completes the year before: through
    its last session, before the session's year is built at all -- the session's year's own
    build would otherwise judge its year-boundary pairs against the gap."""
    year = session.year - 1
    stored = _earliest_stored_horizon(
        panel_store(runtime_dir), _session_scoped_census(targets), year
    )
    reach = f"stop at {stored.isoformat()}" if stored is not None else "are not all stored"
    arguments = _panel_build_command(
        runtime_dir, year=year, through=missing[-1], exchange=exchange, targets=targets
    )
    return "\n".join(
        [
            f"the stored session-scoped partitions of {year} {reach}: {len(missing)} open "
            f"session(s) of {year}, {missing[0].isoformat()} to {missing[-1].isoformat()}, are "
            f"missing from them, and this step builds {session.year} only; nothing was fetched. "
            f"Built over that gap, {session.year}'s first stk_limit build would judge each "
            "security's first pair of the year against a close that is not the session before "
            "it, fetch every disputed pair again at 4 requests each with no cap, and leave "
            f"{year} short of its last session for the doctor's first-of-year check. Complete "
            f"{year} first -- whatever the catch-up bound -- with:",
            "  " + shlex.join(arguments),
            "then rerun this command",
        ]
    )


def _panel_catch_up_refusal(
    runtime_dir: Path,
    *,
    missed: Sequence[date],
    bound: int,
    session: date,
    as_of: datetime,
    exchange: str,
    targets: Sequence[str],
) -> str:
    """Why step 2 fetched nothing, and the `panel build` that fetches the oldest batch: the
    session-scoped targets together (so their horizons move as one, `cli._refuse_split_horizon`),
    pinned at the last batch session's publication instant.

    Incremental, unless a stored horizon already lies past that session -- the session-scoped
    partitions disagree, as a run that died at `stk_limit` leaves them, and one of them reaches
    further than the batch. `cli._incremental_start` refuses an incremental build that would
    shorten a stored year, so the full rebuild of those targets is printed then: it fetches every
    session of the year through the batch, and is said to.

    What the run would have fetched is counted as the run counts it: the targets furthest behind
    fetch the missed sessions and the day, and the stored last session again when the year holds
    one -- a year that holds nothing yet is fetched from its first session, and has no such
    session to fetch again. Sessions, not requests: a target nearer the day fetches fewer, and
    `stk_limit` pages."""
    scoped = _session_scoped_targets(targets)
    per_session = sum(len(cli.PANEL_BUILD_TARGETS[target]) for target in scoped)
    batch = list(missed[: max(bound, 1)])
    store = panel_store(runtime_dir)
    overlap = _earliest_stored_horizon(store, _session_scoped_census(targets), session.year)
    fetched = len(missed) + (1 if overlap is None else 2)
    again = "" if overlap is None else f", {overlap.isoformat()} again"
    ahead = {
        dataset: horizon
        for dataset in _session_scoped_census(targets)
        if (horizon := cli._stored_horizon(store, dataset, session.year)) is not None
        and horizon > batch[-1]
    }
    arguments = _panel_build_command(
        runtime_dir,
        year=session.year,
        through=batch[-1],
        exchange=exchange,
        targets=targets,
        incremental=not ahead,
    )
    if ahead:
        calendar = _stored_calendar(store, exchange, (session.year,), as_of)
        whole = (
            0
            if calendar is None
            else sum(
                1 for day in calendar.trading_days if day.year == session.year and day <= batch[-1]
            )
        )
        sessions = f"all {whole}" if whole else "every one of the"
        fetch = (
            f"The stored {', '.join(sorted(ahead))} already reach "
            f"{max(ahead.values()).isoformat()}, past {batch[-1].isoformat()}, and an incremental "
            "build cannot shorten a stored year (`cli._incremental_start`), so the oldest "
            f"{len(batch)} are fetched by the full rebuild of the session-scoped targets: "
            f"{sessions} session(s) of {session.year} through {batch[-1].isoformat()}, at "
            f"{per_session} requests a session. Run:"
        )
    else:
        fetch = f"Fetch the oldest {len(batch)} with:"
    return "\n".join(
        [
            f"{len(missed)} missed session(s), {missed[0].isoformat()} to "
            f"{missed[-1].isoformat()}, lie between the stored panel and {session.isoformat()}: "
            f"more than the {bound} this step fetches in one run ({PANEL_CATCH_UP_KEY}); nothing "
            f"was fetched, where this run would have fetched {fetched} session(s) of the "
            "session-scoped targets furthest behind -- the missed ones"
            f"{again} and the day -- {fetched - 2} more than an ordinary day's two, at up to "
            f"{per_session} requests a session (more once stk_limit pages). {fetch}",
            "  " + shlex.join(arguments),
            "then rerun this command: it refuses again, naming the next batch, while more than "
            f"{bound} remain, and fetches the rest itself",
        ]
    )


def names_built_before_year_end(runtime_dir: Path, *, year: int, as_of: datetime) -> bool:
    """Whether the stored `namechange` partition of `year` was built before `year` ended, so a
    rename announced after that build and before the year's end is not in it (`V2-P7-016`).

    Its build's `as_of` against the instant `panel build` asks a past year's names at
    (`cli._year_end_as_of`: the year's last instant, never later than `as_of`). A partition with
    no coverage record cannot say, and counts as built before. `False` when the store holds no
    such partition: there is nothing to build again, and seeding a year the store never held is
    the bootstrap's, not a day's."""
    store = panel_store(runtime_dir)
    if year not in store.registered_years(NAMECHANGE_DATASET):
        return False
    coverage = store.read_coverage(NAMECHANGE_DATASET, year)
    return coverage is None or coverage.as_of < cli._year_end_as_of(year, as_of)


def update_panel(
    runtime_dir: Path,
    *,
    session: date,
    as_of: datetime,
    exchange: str,
    targets: Sequence[str],
    next_year: bool,
    survivor: SurvivorPanelUpdate | None = None,
) -> dict[str, Any]:
    """Step 2: the incremental build of every target, and what it wrote; `fina_indicator` in an
    invocation of its own over `financial_indicator_years`; then next year's calendar when the
    session's outcome window reaches into it.

    `survivor` is the survivor form's (`SurvivorPanelUpdate`, `V2-P7-016`): a year before that
    stops short of its last session, and a store more sessions behind than the bound, are refused
    before any request; the sessions fetched beyond an ordinary day are reported as `caught_up`;
    and the year before's `namechange` is built again (one request) while its stored build is
    from before that year ended (`names_built_before_year_end`). Left out, this is the P6 step,
    unchanged."""
    caught_up: tuple[date, ...] = ()
    if survivor is not None:
        missing = previous_year_sessions_missing(
            runtime_dir, session=session, as_of=as_of, exchange=exchange, targets=targets
        )
        if missing:
            raise StepFailedError(
                "panel update",
                _previous_year_refusal(
                    runtime_dir,
                    missing=missing,
                    session=session,
                    exchange=exchange,
                    targets=targets,
                ),
            )
        caught_up = panel_sessions_to_catch_up(
            runtime_dir, session=session, as_of=as_of, exchange=exchange, targets=targets
        )
        if len(caught_up) > survivor.max_catch_up_sessions:
            raise StepFailedError(
                "panel update",
                _panel_catch_up_refusal(
                    runtime_dir,
                    missed=caught_up,
                    bound=survivor.max_catch_up_sessions,
                    session=session,
                    as_of=as_of,
                    exchange=exchange,
                    targets=targets,
                ),
            )
    yearly = tuple(target for target in targets if target != FINANCIAL_INDICATOR_TARGET)
    built = _panel_build(
        runtime_dir, year=session.year, as_of=as_of, exchange=exchange, targets=yearly
    )
    if built.exit_code != 0:
        raise StepFailedError(
            "panel update", f"`panel build` exited {built.exit_code}: {built.reason()}"
        )
    payloads = [body for body in built.payloads() if isinstance(body, dict)]
    report = payloads[-1] if payloads else {}
    budget = _budget(built)
    if FINANCIAL_INDICATOR_TARGET in targets:
        periods = financial_indicator_years(session.year, as_of)
        swept = _panel_build(
            runtime_dir,
            year=periods,
            as_of=as_of,
            exchange=exchange,
            targets=(FINANCIAL_INDICATOR_TARGET,),
        )
        if swept.exit_code != 0:
            raise StepFailedError(
                "panel update",
                f"`panel build --dataset fina_indicator` for report-period years {list(periods)} "
                f"exited {swept.exit_code}: {swept.reason()}",
            )
        budget += _budget(swept)
    previous = [target for target in targets if target in ANNOUNCEMENT_MONTH_TARGETS]
    if previous and session.month == 1:
        # `V2-P6-018`: the incremental statement sweep re-sweeps the month before the stored
        # build's, which in January is last December -- a month of last year's partition. Keyed
        # on the **session**, not the clock: on New Year's Day the day is still 31 December,
        # whose year is the session's and whose previous year is not to be built.
        closing = _panel_build(
            runtime_dir,
            year=session.year - 1,
            as_of=as_of,
            exchange=exchange,
            targets=tuple(previous),
        )
        if closing.exit_code != 0:
            raise StepFailedError(
                "panel update",
                f"`panel build --year {session.year - 1}` for the statements' December re-sweep "
                f"exited {closing.exit_code}: {closing.reason()}",
            )
        budget += _budget(closing)
    if (
        survivor is not None
        and NAMECHANGE_DATASET in targets
        and names_built_before_year_end(runtime_dir, year=session.year - 1, as_of=as_of)
    ):
        # `V2-P7-016`: the name corpus is filed by announcement year and this step builds the
        # session's year only, so a rename announced in the year before after its last run --
        # an ST name announced on the evening of 31 December, in force on the first session
        # (T3 2.4(iv): half are announced the evening before) -- would never be read. So the
        # year before's is built again while its stored build is from before that year ended:
        # keyed on the stored state, not on January, because a first run in February (every
        # January run missed) needs it as much, and once one build at the year's end is stored
        # the next day asks nothing more. The session's year, not the clock's: on New Year's Day
        # the session is still 31 December, whose year is the one this step builds.
        names = _panel_build(
            runtime_dir,
            year=session.year - 1,
            as_of=as_of,
            exchange=exchange,
            targets=(NAMECHANGE_DATASET,),
        )
        if names.exit_code != 0:
            raise StepFailedError(
                "panel update",
                f"`panel build --year {session.year - 1} --dataset {NAMECHANGE_DATASET}`, the "
                f"year before's name corpus built again past its year end, exited "
                f"{names.exit_code}: {names.reason()}",
            )
        budget += _budget(names)
    if next_year:
        ahead = _panel_build(
            runtime_dir,
            year=session.year + 1,
            as_of=as_of,
            exchange=exchange,
            targets=(TRADING_CALENDAR_DATASET,),
        )
        if ahead.exit_code != 0:
            raise StepFailedError(
                "panel update",
                f"the session's outcome window reaches into {session.year + 1} and that year's "
                f"calendar could not be built (exit {ahead.exit_code}): {ahead.reason()}",
            )
    return {
        "targets": list(targets),
        "year": session.year,
        "next_year_calendar": next_year,
        "budget": budget,
        "industry_sweep": industry_sweep(budget),
        "sessions": report.get("sessions"),
        "partitions": report.get("partitions", []),
        **({} if survivor is None else {"caught_up": [day.isoformat() for day in caught_up]}),
    }


def doctor_datasets(targets: Sequence[str]) -> tuple[str, ...]:
    """The stored datasets the targets write, less those not partitioned by calendar year."""
    return tuple(
        sorted(
            {
                dataset
                for target in targets
                if target not in UNCHECKED_BY_YEAR
                for dataset in cli.PANEL_BUILD_TARGETS[target]
            }
        )
    )


def check_panel(
    runtime_dir: Path,
    *,
    datasets: Sequence[str],
    session: date,
    as_of: datetime,
    exchange: str,
    first_of_year: bool = False,
) -> None:
    """Step 3: `panel doctor` and the dependency gate, both clean, or the run stops.

    On the first session of a year the year before is asked about too: the day-level checks
    compare the session with the one before it (`return_paths` reads the previous close and the
    adjustment factors across it), and a calendar of the session's year alone cannot place that
    session -- measured: `check_unavailable`, which blocks.

    `fina_indicator` is asked about in an invocation of its own, over the years among those that
    are stored: its partitions are announcement years written only when something was announced,
    and a year's first sessions can precede its first announcement (`income`'s first 2023 row
    was announced on 4 January, the day after 2023's first session). A year the sweep wrote is
    stored, so the filter only drops a year nothing has been announced in yet.
    """
    years = [session.year - 1, session.year] if first_of_year else [session.year]
    announced = [dataset for dataset in datasets if dataset in ANNOUNCEMENT_YEAR_DATASETS]
    _doctor(
        runtime_dir,
        datasets=[dataset for dataset in datasets if dataset not in ANNOUNCEMENT_YEAR_DATASETS],
        years=years,
        session=session,
        as_of=as_of,
        exchange=exchange,
    )
    if announced:
        store = PanelStore(runtime_dir / "panel")
        stored = [
            year
            for year in years
            if all(year in store.registered_years(dataset) for dataset in announced)
        ]
        if stored:
            _doctor(
                runtime_dir,
                datasets=announced,
                years=stored,
                session=session,
                as_of=as_of,
                exchange=exchange,
            )


ANNOUNCEMENT_YEAR_DATASETS: Final[frozenset[str]] = frozenset({FINANCIAL_INDICATOR_TARGET})
"""Swept by report period, filed by announcement year: see `check_panel`."""


def _doctor(
    runtime_dir: Path,
    *,
    datasets: Sequence[str],
    years: Sequence[int],
    session: date,
    as_of: datetime,
    exchange: str,
) -> None:
    """One `panel doctor` and one `data-check` over `datasets` x `years`, both clean."""
    if not datasets:
        return
    arguments = ["--runtime-dir", str(runtime_dir)]
    for year in years:
        arguments += ["--year", str(year)]
    arguments += ["--session", session.isoformat(), "--as-of", as_of.isoformat()]
    arguments += ["--exchange", exchange]
    for dataset in datasets:
        arguments += ["--dataset", dataset]
    if {INDEX_WEIGHT_DATASET, INDEX_DAILY_DATASET} & set(datasets):
        # Without them the index datasets are `check_unavailable` -- "no index code was named,
        # so the index itself could not be required" -- which blocks: measured on a copy of
        # `runtime/panel` for 2026-08-28, and clean with the three the panel builds.
        for code in INDEX_WEIGHT_INDEX_CODES:
            arguments += ["--index-code", code]
    doctor = invoke(["panel", "doctor", *arguments, "--no-limitation-detail"])
    if doctor.exit_code != 0:
        raise StepFailedError(
            "panel doctor",
            f"`panel doctor` is not clean (exit {doctor.exit_code}): {doctor.reason()}",
        )
    gate = invoke(["data-check", *arguments])
    if gate.exit_code != 0:
        raise StepFailedError(
            "panel doctor",
            f"the dependency gate `data-check` refused (exit {gate.exit_code}): {gate.reason()}",
        )


def _built_instants(
    store: PanelStore, build: TierBuild, *, years: Sequence[int], as_of: datetime
) -> frozenset[datetime]:
    """Every signal instant in `years` at which the store holds `build`, through each tier's own
    manifests: a processed or neutralized tier counts at an instant only above a raw manifest of
    that instant."""
    definition = resolve_factor(build.factor)
    held = set(store.registered_years(factor_manifest_dataset(definition)))
    read = tuple(year for year in years if year in held)
    if not read:
        return frozenset()
    try:
        # The three tiers' manifests in one shared hold (`V2-P6-028`): a build of every tier
        # lands as one group write, and reads in three holds could see some tiers of it.
        with store.reading():
            raw = {
                manifest.manifest_id: manifest.as_of
                for manifest in load_factor_manifests(store, definition, years=read, as_of=as_of)
            }
            if not raw or build.tier == "raw":
                return frozenset(raw.values())
            assert build.transform is not None  # a processed or neutralized tier names one
            transform = FACTOR_TRANSFORMS.get(build.transform)
            processed = {
                manifest.transform_manifest_id: raw[manifest.source_manifest_id]
                for manifest in load_factor_transform_manifests(
                    store, definition, years=read, as_of=as_of
                )
                if manifest.source_manifest_id in raw
                and manifest.transform_id == transform.transform_id
            }
            if not processed or build.tier == "processed":
                return frozenset(processed.values())
            assert build.neutralization is not None
            neutralization = FACTOR_NEUTRALIZATIONS.get(build.neutralization)
            return frozenset(
                processed[manifest.source_transform_manifest_id]
                for manifest in load_factor_neutralization_manifests(
                    store, definition, years=read, as_of=as_of
                )
                if manifest.source_transform_manifest_id in processed
                and manifest.neutralization_id == neutralization.neutralization_id
            )
    except FactorEngineError:
        return frozenset()


def _built_at_instant(
    store: PanelStore, build: TierBuild, *, instant: datetime, as_of: datetime
) -> bool:
    """Whether the store already holds `build` at `instant`, through each tier's own manifest."""
    year = instant.astimezone(SHANGHAI).year
    return instant in _built_instants(store, build, years=(year,), as_of=as_of)


def _newest_build(
    store: PanelStore, build: TierBuild, *, through: datetime, as_of: datetime
) -> datetime | None:
    """The newest signal instant at or before `through` at which the store holds `build`,
    searched a year at a time from `through`'s; `None` when it holds the tier at none."""
    definition = resolve_factor(build.factor)
    last = through.astimezone(SHANGHAI).year
    years = sorted(
        (
            year
            for year in store.registered_years(factor_manifest_dataset(definition))
            if year <= last
        ),
        reverse=True,
    )
    for year in years:
        built = [
            instant
            for instant in _built_instants(store, build, years=(year,), as_of=as_of)
            if instant <= through
        ]
        if built:
            return max(built)
    return None


def sessions_to_build(
    store: PanelStore, build: TierBuild, *, session: date, exchange: str, as_of: datetime
) -> tuple[date, ...]:
    """The sessions step 4 builds `build` at, oldest first (`V2-P6-032`).

    Every open session after the tier's newest stored signal instant, through `session`, by the
    stored `trade_cal` (the calendar `_factor_years` reads) -- so a run the scheduler missed (the
    machine off or asleep) leaves no session without a cross section for the trailing-IC and
    walk-forward windows to find missing later. Nothing when the tier is built at `session`
    already; `session` alone when the store holds the tier at no instant at all, since a history
    is the research build's to make, not this step's.
    """
    instant = session_publication_instant(session)
    newest = _newest_build(store, build, through=instant, as_of=as_of)
    if newest is None:
        return (session,)
    if newest == instant:
        return ()
    years = tuple(range(newest.astimezone(SHANGHAI).year, session.year + 1))
    unstored = sorted(set(years) - set(store.registered_years(TRADING_CALENDAR_DATASET)))
    calendar = None if unstored else _stored_calendar(store, exchange, years, as_of)
    if calendar is None:
        raise StepFailedError(
            "factor build",
            f"{build.factor}@{build.tier} was last built at {newest.isoformat()}, and the "
            f"{exchange} calendar of {', '.join(map(str, unstored or years))}, which counts the "
            "sessions since, is not stored or cannot be read",
        )
    return tuple(
        day for day in calendar.trading_days if newest < session_publication_instant(day) <= instant
    )


def _factor_years(store: PanelStore, session: date) -> tuple[int, ...]:
    """The years every research build names for an instant in the session's year
    (`factor_view.factor_build_years`: that year and the two before it), as far as the stored
    calendar holds them.

    `V2-P6-027`, round 3. This used to be the session's year and the one before, and the research
    builds name three: a factor that reads `daily_basic` answers each security from its newest
    session in the years named, so a security halted since the year before last was valued in
    research and `insufficient_history` here. One shared span keeps the two from drifting again.
    A year the calendar does not hold cannot be read by either, so it is left out rather than
    named and refused.
    """
    stored = set(store.registered_years(TRADING_CALENDAR_DATASET))
    return tuple(year for year in factor_build_years(session.year) if year in stored) or (
        session.year,
    )


def build_factors(
    store: PanelStore,
    builds: Sequence[TierBuild],
    *,
    session: date,
    as_of: datetime,
    exchange: str,
    max_staleness_days: int,
    runtime_dir: Path,
) -> dict[str, Any]:
    """Step 4: every tier the configuration reads, at the session's signal instant and at every
    session a missed run left unbuilt since the tier's newest build (`sessions_to_build`).

    At most `MAX_CATCH_UP_SESSIONS` missed sessions a tier: a longer gap is refused before
    anything is built, naming the gap and the `openalpha factor build` that builds its oldest
    batch. `caught_up` lists the missed sessions built, for the summary and the day's journal.
    """
    instant = session_publication_instant(session)
    plans = {
        build: sessions_to_build(store, build, session=session, exchange=exchange, as_of=as_of)
        for build in builds
    }
    groups: dict[tuple[str, str | None, str | None, tuple[date, ...]], list[str]] = {}
    for build, days in plans.items():
        if days:
            key = (build.tier, build.transform, build.neutralization, days)
            groups.setdefault(key, []).append(build.factor)
    ordered = sorted(
        groups.items(),
        key=lambda item: (item[0][0], item[0][1] or "", item[0][2] or "", item[0][3]),
    )
    too_long = [
        (key, factors)
        for key, factors in ordered
        if len([day for day in key[3] if day != session]) > MAX_CATCH_UP_SESSIONS
    ]
    if too_long:
        raise StepFailedError(
            "factor build",
            _catch_up_refusal(
                store,
                too_long,
                session=session,
                exchange=exchange,
                max_staleness_days=max_staleness_days,
                runtime_dir=runtime_dir,
            ),
        )
    code_commit = resolve_code_commit()
    written: list[str] = []
    for (tier, transform, neutralization, days), factors in ordered:
        for year in sorted({day.year for day in days}):
            in_year = [day for day in days if day.year == year]
            try:
                requests = factor_build_requests(
                    factors=factors,
                    tier=tier,
                    transform=transform or "",
                    neutralization=neutralization or "",
                    as_ofs=[session_publication_instant(day) for day in in_year],
                    years=_factor_years(store, in_year[0]),
                    exchange=exchange,
                    max_staleness_days=max_staleness_days,
                    waive_max_staleness=False,
                    subjects=[],
                    supersedes_raw=[],
                    supersedes_processed=[],
                    supersedes_neutralized=[],
                    code_commit=code_commit,
                )
                reports = build_factor_panel_set(store, requests, built_at=as_of)
            except FactorViewError as error:
                raise StepFailedError("factor build", error.disclosable) from error
            written.extend(partition for report in reports for partition in report.partitions)
    caught_up = sorted({day for days in plans.values() for day in days if day != session})
    return {
        "instant": instant.isoformat(),
        "built": [f"{b.factor}@{b.tier}" for b in builds if plans[b]],
        "already_built": [f"{b.factor}@{b.tier}" for b in builds if not plans[b]],
        "caught_up": [day.isoformat() for day in caught_up],
        "partitions": sorted(set(written)),
    }


def _catch_up_command(
    store: PanelStore,
    factors: Sequence[str],
    *,
    tier: str,
    transform: str | None,
    neutralization: str | None,
    days: Sequence[date],
    exchange: str,
    max_staleness_days: int,
    runtime_dir: Path,
) -> str:
    """The `openalpha factor build` step 4 would run for `factors` at `days` (one year's)."""
    arguments = ["openalpha", "factor", "build", "--runtime-dir", str(runtime_dir)]
    for factor in factors:
        arguments += ["--factor", factor]
    arguments += ["--tier", tier]
    if transform:
        arguments += ["--transform", transform]
    if neutralization:
        arguments += ["--neutralization", neutralization]
    for year in _factor_years(store, days[0]):
        arguments += ["--year", str(year)]
    arguments += ["--exchange", exchange, "--max-staleness-days", str(max_staleness_days)]
    for day in days:
        arguments += ["--as-of", session_publication_instant(day).astimezone(UTC).isoformat()]
    return shlex.join(arguments)


def _catch_up_refusal(
    store: PanelStore,
    groups: Sequence[tuple[tuple[str, str | None, str | None, tuple[date, ...]], list[str]]],
    *,
    session: date,
    exchange: str,
    max_staleness_days: int,
    runtime_dir: Path,
) -> str:
    """Why step 4 built nothing, and the command that builds each refused gap's oldest batch."""
    lines: list[str] = []
    for (tier, transform, neutralization, days), factors in groups:
        missed = [day for day in days if day != session]
        oldest = [day for day in missed[:MAX_CATCH_UP_SESSIONS] if day.year == missed[0].year]
        names = ", ".join(f"{factor}@{tier}" for factor in factors)
        lines += [
            f"{names}: {len(missed)} missed session(s), {missed[0].isoformat()} to "
            f"{missed[-1].isoformat()}, more than the {MAX_CATCH_UP_SESSIONS} this step catches "
            "up (MAX_CATCH_UP_SESSIONS); nothing was built. Build the oldest "
            f"{len(oldest)} with:",
            "  "
            + _catch_up_command(
                store,
                factors,
                tier=tier,
                transform=transform,
                neutralization=neutralization,
                days=oldest,
                exchange=exchange,
                max_staleness_days=max_staleness_days,
                runtime_dir=runtime_dir,
            ),
        ]
    lines.append(
        "then rerun this command: it refuses again, naming the next batch, while more than "
        f"{MAX_CATCH_UP_SESSIONS} remain, and catches up the rest itself"
    )
    return "\n".join(lines)


class FactorGapError(ValueError):
    """`--factor-gaps` could not be answered."""


def factor_gaps(
    runtime_dir: Path, registration: Path, *, since: date, as_of: datetime
) -> dict[str, Any]:
    """`--factor-gaps SINCE`: every open session from `since` through the newest one closed at
    `as_of` at which a factor tier the registered configuration reads has no build.

    Read only, and it admits no code: it is the check for a store a checkout that catches
    nothing up has been running on -- the worktree pinned at a registration made before
    `V2-P6-032` builds only the day, so a missed run leaves a hole behind the newest build that
    step 4 never looks at again. The configuration is read from the registration file as
    registered (its `config_id` re-derived, as admission does).
    """
    try:
        body = json.loads(registration.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise FactorGapError(f"{registration} cannot be read: {error}") from error
    config = body.get("config") if isinstance(body, dict) else None
    if not isinstance(config, dict) or grid.config_id(config) != body.get("config_id"):
        raise FactorGapError(f"{registration} registers no configuration that is its config_id")
    try:
        anchor = anchor_of(config)
        probe = day_request(config, day=anchor, as_of=session_publication_instant(anchor))
    except (KeyError, ValueError, StepFailedError) as error:
        raise FactorGapError(f"the registered configuration cannot be read: {error}") from error
    store = panel_store(runtime_dir)
    through = _stored_session(store, probe.exchange, as_of)
    if through is None:
        raise FactorGapError(f"no stored {probe.exchange} session had closed at {as_of}")
    years = tuple(range(since.year, through.year + 1))
    unstored = sorted(set(years) - set(store.registered_years(TRADING_CALENDAR_DATASET)))
    calendar = None if unstored else _stored_calendar(store, probe.exchange, years, as_of)
    if calendar is None:
        raise FactorGapError(
            f"the {probe.exchange} calendar of {', '.join(map(str, unstored or years))} is not "
            "stored or cannot be read"
        )
    sessions = [day for day in calendar.trading_days if since <= day <= through]
    gaps: dict[str, list[str]] = {}
    for build in tiers_read(probe):
        built = _built_instants(store, build, years=years, as_of=as_of)
        gaps[f"{build.factor}@{build.tier}"] = [
            day.isoformat() for day in sessions if session_publication_instant(day) not in built
        ]
    return {"since": since.isoformat(), "through": through.isoformat(), "gaps": gaps}


def factor_gap_lines(report: Mapping[str, Any]) -> list[str]:
    lines = [f"factor gaps        open sessions {report['since']} to {report['through']}"]
    for tier, missing in report["gaps"].items():
        lines.append(
            f"{tier}: {len(missing)} session(s) without a build: {', '.join(missing)}"
            if missing
            else f"{tier}: no session without a build"
        )
    return lines


def target_weights(
    signal: SignalDay,
    request: StrategyRequest,
    previous: Mapping[str, str],
    *,
    schedule: Schedule,
) -> dict[str, Any]:
    """Step 6: today's book under the book's rule, and the move from yesterday's.

    On a day a rebalance is due (`Schedule.due`) the held names that still rank within the band
    stay and the free slots are filled in rank order (`target_holdings`, the backtest's own rule).
    Otherwise, and on a day the source held, yesterday's book is today's. Each name is
    `1 / holding_count` of the book, the book's equal position capital; what no name fills is
    cash.
    """
    spec = request.spec
    ranked = signal.scores.ranked
    if ranked is None or not schedule.due:
        names = tuple(sorted(previous))
        decided = "held" if ranked is None else "not a rebalance day"
    else:
        keep, buy = target_holdings(
            ranked, held=frozenset(previous), spec=spec, industries=signal.industries
        )
        names = tuple(sorted(keep)) + buy
        decided = "rebalanced"
    weight = (Decimal(1) / spec.holding_count).quantize(_WEIGHT_QUANTUM)
    weights = {name: f"{weight:f}" for name in sorted(names)}
    moved = sum(
        (
            abs(Decimal(weights.get(name, "0")) - Decimal(previous.get(name, "0")))
            for name in set(weights) | set(previous)
        ),
        Decimal(0),
    )
    return {
        "decision": decided,
        "reason": schedule.reason,
        "holding_count": spec.holding_count,
        "rebalance_every_sessions": spec.rebalance_every_sessions,
        "sessions_since_start": schedule.position,
        "scheduled_rebalance": schedule.scheduled.isoformat(),
        "weights": weights,
        "cash": f"{(Decimal(1) - weight * len(names)).quantize(_WEIGHT_QUANTUM):f}",
        "turnover": f"{(moved / 2).quantize(_WEIGHT_QUANTUM):f}",
    }


def candidates(signal: SignalDay, request: StrategyRequest, config_id: str) -> dict[str, Any]:
    """Step 5: the ranking cut at the buffer band (or the book), and its content address."""
    spec = request.spec
    cut = max(spec.holding_count, spec.buffer_rank or 0)
    ranked = signal.scores.ranked
    rows = (
        []
        if ranked is None
        else [
            {"rank": rank, "ts_code": name, "score": signal.scores.scores[name]}
            for rank, name in enumerate(ranked[:cut], start=1)
        ]
    )
    document = {"session": signal.day.isoformat(), "config_id": config_id, "candidates": rows}
    digest = hashlib.sha256(
        json.dumps(document, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return {
        "candidate_list_id": f"dsl_{digest[:24]}",
        "held": ranked is None,
        "ranked_count": 0 if ranked is None else len(ranked),
        "incomplete_count": len(signal.scores.incomplete),
        "candidates": rows,
        "weights": {key: value for key, value in sorted(signal.scores.weights.items())},
        "refit_day": None if signal.refit_day is None else signal.refit_day.isoformat(),
        "industries_read": len(signal.industries),
    }


def _outcome_calendar(
    store: PanelStore, exchange: str, session: date, as_of: datetime
) -> TradingCalendar:
    """The calendar the record's outcome instant is derived on: the session's year, and the
    next when it is stored, so a window crossing New Year can be placed."""
    stored = set(store.registered_years(TRADING_CALENDAR_DATASET))
    years = tuple(year for year in (session.year, session.year + 1) if year in stored)
    return load_trading_calendar(store, exchange=exchange, years=years, as_of=as_of)


def register_prediction(
    runtime_dir: Path,
    batch: PredictionBatch,
    *,
    calendar: TradingCalendar,
    clock: Callable[[], datetime],
) -> tuple[PredictionRecord, str]:
    """Step 7: file `batch` unless the session already has its record.

    `model daily-run` files a new record on every invocation, because `predicted_at` reaches the
    address; a daily command re-run the same evening must not. `session_record` finds the
    session's record under the same declaration: holding the same numbers it is reused and
    nothing is written; holding different ones, the registered record stands and this run is
    refused.

    **Only before the next session's call auction.** The store calls a record `forward` when it
    held it before the outcome window's last close. This command's rule is stricter, because the
    book trades the day's scores at the next open, whose price the call auction starting at 09:15
    fixes: a registration at or after `registration_cutoff` -- the command run late, or catching
    up a missed day -- is refused before anything is filed.
    """
    store = build_storage(runtime_dir=runtime_dir, clock=clock).prediction_store
    try:
        held = session_record(store, batch)
    except StrategyRegistrationError as error:
        raise StepFailedError("prediction", str(error)) from error
    if held is not None:
        return held, "unchanged"
    day = batch.as_of.astimezone(SHANGHAI).date()
    cutoff = registration_cutoff(calendar, day)
    deadline = outcome_known_at_for(batch, calendar=calendar, zone=SHANGHAI)
    now = clock()
    if now >= cutoff:
        raise StepFailedError(
            "prediction",
            f"the scores of {day.isoformat()} are traded at the next session's open, whose call "
            f"auction starts {cutoff.isoformat()}, and it is {now.isoformat()}: part of their "
            f"outcome is published already (all of it by {deadline.isoformat()}), so registered "
            "now they would not be a prediction made before its outcome. Nothing was filed",
        )
    written = store.put(batch=batch, calendar=calendar, zone=SHANGHAI)
    return written.record, written.outcome


# --- the whole run -----------------------------------------------------------------------------


def _utc_now() -> datetime:
    return datetime.now(UTC)


def run_daily_selection(
    options: DailyOptions, *, clock: Callable[[], datetime] = _utc_now
) -> dict[str, Any]:
    """Run the eight steps and return the day's journal; raise `StepFailedError` on a refusal,
    carrying the requests made before it."""
    counts: Counter[str] = Counter()
    try:
        return _run_daily_selection(options, clock=clock, counts=counts)
    except StepFailedError as error:
        error.requests = dict(sorted(counts.items()))
        raise


def _run_daily_selection(
    options: DailyOptions, *, clock: Callable[[], datetime], counts: Counter[str]
) -> dict[str, Any]:
    runtime_dir = options.runtime_dir
    with _step("registration"):
        registration = admit_registration(options.registration, options.repo)
        survivor = survivor_registration(registration)
    if survivor is not None:
        # The registration's `kind` decides the day (`V2-P7-008`); the survivor day numbers and
        # reports its own steps, so it runs outside this step's fault handler.
        return _run_survivor_day(options, registration, survivor, clock=clock, counts=counts)
    with _step("registration"):
        store = panel_store(runtime_dir)
        directory = journal_directory(runtime_dir, registration)
        first_as_of = options.as_of or clock()
        anchor = _registered_anchor(registration)
        # The configuration resolved once, about its own first day, before anything is fetched:
        # a configuration that cannot be put is a step-1 refusal, and which tiers and industries
        # it reads decide steps 2 and 4.
        probe = day_request(
            registration.config, day=anchor, as_of=session_publication_instant(anchor)
        )
        exchange = probe.exchange
        builds = tiers_read(probe)
        base = (
            FULL_UPDATE_TARGETS
            if options.full_update
            else options.targets
            if options.targets is not None
            else targets_for(builds)
        )

    with _step("panel update"), counted_transport(counts):
        session = _stored_session(store, exchange, first_as_of)
        if session is None:
            year = first_as_of.astimezone(SHANGHAI).year
            built = _panel_build(
                runtime_dir,
                year=year,
                as_of=first_as_of,
                exchange=exchange,
                targets=(TRADING_CALENDAR_DATASET,),
            )
            if built.exit_code != 0:
                raise StepFailedError(
                    "panel update", f"the {year} calendar could not be built: {built.reason()}"
                )
            session = _stored_session(store, exchange, first_as_of)
            if session is None:
                raise StepFailedError(
                    "panel update", f"no session had published at {first_as_of.isoformat()}"
                )
        if session < anchor:
            raise StepFailedError(
                "registration",
                f"the registered configuration starts on {anchor.isoformat()}, after the newest "
                f"closed session {session.isoformat()}",
                exit_code=DailyExit.no_registration,
            )
        path = directory / f"{session.isoformat()}.json"
        journal = read_journal(path)
        if journal is not None and journal.get("registration", {}).get("sha256") not in (
            None,
            registration.sha256,
        ):
            raise StepFailedError(
                "summary", f"{path} was written under another registration of this configuration"
            )
        if journal is not None and "result" in journal:
            result = dict(journal["result"])
            result["this_run"] = {"requests": {}, "request_count": 0, "wrote": False}
            return result
        if journal is None:
            # The day's clock is pinned before anything is fetched, so a retry of a step 2 that
            # stopped partway asks the same question: the partitions it already wrote are then
            # rewritten byte for byte, which `PanelStore` does not rewrite at all.
            journal = {
                "schema": DAILY_SELECTION_SCHEMA,
                "session": session.isoformat(),
                "as_of": first_as_of.isoformat(),
                "registration": {
                    "path": registration.path.name,
                    "sha256": registration.sha256,
                    "commit": registration.commit,
                    "config_id": registration.config_id,
                },
            }
            write_journal(path, journal)
        as_of = datetime.fromisoformat(journal["as_of"])
        calendar = _stored_calendar(
            store, exchange, tuple(range(anchor.year, session.year + 1)), as_of
        )
        if calendar is None:
            raise StepFailedError(
                "panel update",
                f"the {exchange} calendar from {anchor.year} to {session.year} is not stored; "
                "the rebalance schedule is counted on it",
            )
        prior_day, prior = previous_targets(directory, session)
        schedule = schedule_of(
            calendar,
            anchor=anchor,
            session=session,
            every=probe.spec.rebalance_every_sessions,
            previous=prior_day,
        )
        reads_industries = industry_day(probe, builds, schedule)
        if "panel" not in journal:
            targets = tuple(base) + tuple(
                target for target in INDUSTRY_TARGETS if reads_industries and target not in base
            )
            panel = update_panel(
                runtime_dir,
                session=session,
                as_of=as_of,
                exchange=exchange,
                targets=targets,
                next_year=next_year_calendar_needed(
                    calendar, session=session, horizon=outcome_horizon(probe)
                ),
            )
            journal = {**journal, "panel": {**panel, "requests": dict(sorted(counts.items()))}}
            write_journal(path, journal)

    with _step("panel doctor"):
        check_panel(
            runtime_dir,
            datasets=doctor_datasets(journal["panel"]["targets"]),
            session=session,
            as_of=as_of,
            exchange=exchange,
            first_of_year=session
            == min(day for day in calendar.trading_days if day.year == session.year),
        )
    with _step("factor build"):
        request = day_request(registration.config, day=session, as_of=as_of)
        factors = build_factors(
            store,
            builds,
            session=session,
            as_of=as_of,
            exchange=request.exchange,
            max_staleness_days=options.max_staleness_days,
            runtime_dir=runtime_dir,
        )
    with _step("candidates"):
        capped = request.spec.max_industry_weight is not None
        try:
            signal = score_day(
                store,
                request,
                day=session,
                anchor=anchor,
                read_industries=capped and schedule.due,
            )
        except StrategyViewError as error:
            raise StepFailedError("candidates", error.disclosable) from error
        listed = candidates(signal, request, registration.config_id)
    with _step("target weights"):
        targets_today = {
            **target_weights(signal, request, prior, schedule=schedule),
            "previous_session": None if prior_day is None else prior_day.isoformat(),
        }
    with _step("prediction"):
        try:
            batch = signal_day_batch(signal, request, registration.declared, predicted_at=clock())
        except StrategyRegistrationError as error:
            raise StepFailedError("prediction", str(error)) from error
        prediction: dict[str, Any] = {"registered": False, "reason": "the source held today"}
        if batch is not None:
            provenance = input_provenance(
                store,
                request,
                registration.declared,
                day=session,
                batch=batch,
                recorded_at=clock(),
            )
            write_provenance(runtime_dir, provenance)
            record, outcome = register_prediction(
                runtime_dir,
                batch,
                calendar=_outcome_calendar(store, request.exchange, session, as_of),
                clock=clock,
            )
            prediction = {
                "registered": True,
                "record_id": record.record_id,
                "outcome": outcome,
                "standing": record.standing,
                "as_of": record.batch.as_of.isoformat(),
                "outcome_known_at": record.outcome_known_at.isoformat(),
                "recorded_at": record.recorded_at.isoformat(),
                "scored": len(record.batch.scored),
                "abstained": len(record.batch.abstained),
                "provenance": provenance.digest,
            }
    with _step("summary"):
        result = {
            "session": session.isoformat(),
            "as_of": as_of.isoformat(),
            "registration": journal["registration"],
            "panel": journal["panel"],
            "factors": factors,
            "candidates": listed,
            "targets": targets_today,
            "prediction": prediction,
            "wording": "candidates of a registered research configuration; not an order, not a "
            "forecast of return",
        }
        write_journal(path, {**journal, "result": result})
    return {
        **result,
        "this_run": {
            "requests": dict(sorted(counts.items())),
            "request_count": sum(counts.values()),
            "wrote": True,
        },
    }


# --- the survivor book's day (`V2-P7-008`) -------------------------------------------------------


SURVIVOR_JOURNAL_SCHEMA: Final[str] = "openalpha-daily-survivor/v1"
SURVIVOR_JOURNAL_DIRECTORY: Final[str] = "daily_survivor"
"""Under the runtime directory: the survivor day's journal, `<config_id[:16]>/<session>.json` --
beside the P6 day's `daily_selection`, never inside it, so neither day's reader meets the other's
journals."""
PAPER_DIRECTORY: Final[str] = "paper"
"""Under the runtime directory: the paper book -- its snapshots and its ledger (`build_storage`),
its marker and its writer lock (`strategy_survivor_book`)."""
SURVIVOR_RESULT_KIND: Final[str] = "survivor_book/v1"
"""A survivor result's `kind` (`strategy_survivor_summary.SURVIVOR_DOCUMENT_KIND`), spelled here
because reading it from that module would load the paper book for every P6 day: `main` tells a
survivor result from a P6 one by it."""
SURVIVOR_OUTCOME_SESSIONS: Final[int] = 20
"""The record's horizon in sessions (`strategy_survivor_registration.SURVIVOR_HORIZON`, `20d`):
how far past the session step 2 makes sure the stored calendar reaches, as P6's
`outcome_horizon` does for its record."""
SETTLE_ONLY_COVERAGE: Final[float] = 0.0
"""The coverage floor a settle-only day reads its facts at: it decides nothing and files nothing,
so the registered floor, which guards a decision and a record, has nothing to guard (D4 review,
rule 3); the facts are still read, for the held statuses and the document."""


@dataclass(frozen=True, slots=True, kw_only=True)
class SurvivorDay:
    """A survivor day as steps 1 to 7 leave it for steps 8 and 9 -- the paper book and the
    summary, `V2-P7-008` D5b's: what the day read, pinned and filed, and the lock it holds."""

    options: DailyOptions
    registration: Registration
    survivor: SurvivorRegistration
    lock: PaperBookLock
    """The paper book's writer lock, held from before step 2 to the end of the day."""
    storage: StorageContainer
    store: PanelStore
    journal_path: Path
    journal: Mapping[str, Any]
    """As step 7 left it: the pin (`as_of`, `held`, `held_from`), `panel` and `prediction`."""
    session: date
    as_of: datetime
    """The journal-pinned clock: every read of the day and of its reruns is made at it, and it,
    never the clock, says whether the day is an evening or settles only (D3 review, rule 4)."""
    held: tuple[str, ...]
    """The journal-pinned holdings the facts were read with."""
    calendar: TradingCalendar
    ordinals: SessionOrdinals
    transform: FactorTransformSpec
    """The registered transform, resolved once (D3 review, rule 7)."""
    head: PaperHead
    facts: FactsDay
    screen: ScreenResult
    factors: Mapping[str, Any]
    prediction: Mapping[str, Any]
    settle_only: bool
    """The pinned `as_of` is at or after 09:15 of the next session: no record was filed, and no
    decision may be made (decision 2)."""


def survivor_builds(survivor: SurvivorRegistration) -> tuple[TierBuild, ...]:
    """Step 4's tiers for a survivor registration: every composite factor at the processed tier
    under the registered transform, not neutralized -- the one build `build_facts_day` reads for
    each, at the session's 16:30."""
    from openalpha_cn.strategy_survivor_registration import SURVIVOR_TIER

    transform = survivor.adapter.transform
    return tuple(
        TierBuild(factor=factor, tier=SURVIVOR_TIER, transform=transform, neutralization=None)
        for factor in survivor.book.composite_factors
    )


def survivor_panel_update(survivor: SurvivorRegistration) -> SurvivorPanelUpdate:
    """Step 2's survivor rules, the catch-up bound read where a registration holds it: its
    `adapter.max_panel_catch_up_sessions`, which the adapter reader requires and bounds to
    0..16 (`strategy_survivor.MAX_PANEL_CATCH_UP_SESSIONS`). The one reader of that key."""
    return SurvivorPanelUpdate(max_catch_up_sessions=survivor.adapter.max_panel_catch_up_sessions)


def survivor_journal_directory(runtime_dir: Path, registration: Registration) -> Path:
    return runtime_dir / SURVIVOR_JOURNAL_DIRECTORY / registration.config_id[:16]


def read_survivor_journal(path: Path) -> dict[str, Any] | None:
    """The survivor day's journal at `path`, or `None` when there is none.

    Its own reader, not P6's `read_journal`, which names P6's summary step (step 8 there, 9 here)
    and would read a P6 journal as the day's. A file that is not UTF-8 JSON, does not hold a
    mapping, is a P6 journal or is of another schema is refused by name -- step `summary`,
    numbered in `SURVIVOR_STEPS` -- and left as it is."""
    if not path.is_file():
        return None
    try:
        body = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as error:  # `UnicodeDecodeError` and `JSONDecodeError` both are
        raise StepFailedError(
            "summary",
            f"{path} is not a {SURVIVOR_JOURNAL_SCHEMA} journal: it is not UTF-8 JSON "
            f"({type(error).__name__}: {error}). It is left as it is",
            steps=SURVIVOR_STEPS,
        ) from error
    if not isinstance(body, dict):
        raise StepFailedError(
            "summary",
            f"{path} is not a {SURVIVOR_JOURNAL_SCHEMA} journal: it holds a "
            f"{type(body).__name__}, not a mapping. It is left as it is",
            steps=SURVIVOR_STEPS,
        )
    schema = body.get("schema")
    if schema == DAILY_SELECTION_SCHEMA:
        raise StepFailedError(
            "summary",
            f"{path} is a P6 journal ({DAILY_SELECTION_SCHEMA}), not the survivor day's "
            f"{SURVIVOR_JOURNAL_SCHEMA}: the two days keep their journals apart "
            f"({JOURNAL_DIRECTORY} and {SURVIVOR_JOURNAL_DIRECTORY}), so it was put there by hand. "
            "It is left as it is",
            steps=SURVIVOR_STEPS,
        )
    if schema != SURVIVOR_JOURNAL_SCHEMA:
        raise StepFailedError(
            "summary",
            f"{path} is not a {SURVIVOR_JOURNAL_SCHEMA} journal: its schema is {schema!r}. It is "
            "left as it is",
            steps=SURVIVOR_STEPS,
        )
    return body


def survivor_journal_complete(journal: Mapping[str, Any] | None) -> bool:
    """Whether a survivor journal is a finished day's, which a rerun prints again with no request
    and no write: it has filed its `result`. A day refused before its paper step files none and
    keeps its stages (D4 review, rule 2), so a rerun after a fix runs its evening."""
    return journal is not None and "result" in journal


def _refuse_p6_only_options(options: DailyOptions, survivor: SurvivorRegistration) -> None:
    """Refuse the P6 day's options that mean nothing on a survivor day, and a `--dataset` that
    leaves out a target the day reads, before anything is made (exit 2, no request, no
    directory); `_run_survivor_day` says what the others mean there."""
    if options.full_update:
        raise StepFailedError(
            "registration",
            "--full-update fetches FULL_UPDATE_TARGETS, the whole current year of every target "
            "the P6 day might read; a survivor day fetches what its own day reads "
            "(targets_for_survivor, or the --dataset given), and a store's full refresh is "
            "`openalpha panel build`'s, not a day's. Nothing was run",
            exit_code=DailyExit.no_registration,
            steps=SURVIVOR_STEPS,
        )
    if options.top is not None:
        raise StepFailedError(
            "registration",
            "--top cuts the P6 day's printed candidate list; a survivor day prints no candidate "
            "list to cut -- the paper book's plan is printed whole. Nothing was run",
            exit_code=DailyExit.no_registration,
            steps=SURVIVOR_STEPS,
        )
    if options.targets is not None:
        required = targets_for_survivor(survivor_builds(survivor))
        missing = [target for target in required if target not in options.targets]
        if missing:
            raise StepFailedError(
                "registration",
                f"--dataset names {list(options.targets)}, which leaves out {missing}: a survivor "
                f"day updates at least targets_for_survivor ({list(required)}), the panel its "
                "facts, factor builds and forward record read, so a --dataset may add targets and "
                "never narrow them -- a record filed from a panel the day did not update would "
                "not be the day's. Nothing was run",
                exit_code=DailyExit.no_registration,
                steps=SURVIVOR_STEPS,
            )


def _survivor_session(runtime_dir: Path, store: PanelStore, exchange: str, as_of: datetime) -> date:
    """The day's session as the P6 day finds it: the newest published at `as_of` by the stored
    calendar, which is built first (one request) when the store holds none to read it from."""
    session = _stored_session(store, exchange, as_of)
    if session is not None:
        return session
    year = as_of.astimezone(SHANGHAI).year
    built = _panel_build(
        runtime_dir, year=year, as_of=as_of, exchange=exchange, targets=(TRADING_CALENDAR_DATASET,)
    )
    if built.exit_code != 0:
        raise StepFailedError(
            "panel update",
            f"the {year} calendar could not be built: {built.reason()}",
            steps=SURVIVOR_STEPS,
        )
    session = _stored_session(store, exchange, as_of)
    if session is None:
        raise StepFailedError(
            "panel update", f"no session had published at {as_of.isoformat()}", steps=SURVIVOR_STEPS
        )
    return session


def _admit_epoch(
    store: PanelStore, *, epoch: date, session: date, exchange: str, as_of: datetime
) -> None:
    """Refuse, as a registration the day cannot run (exit 2), an epoch after the session -- P6's
    `session < anchor` -- and one the stored calendar does not open: the adapter reader admits
    any date, a Saturday included, and the paper book counts its sessions from its epoch."""
    if session < epoch:
        raise StepFailedError(
            "registration",
            f"the registered paper book opens on its epoch {epoch.isoformat()}, after the newest "
            f"closed session {session.isoformat()}",
            exit_code=DailyExit.no_registration,
            steps=SURVIVOR_STEPS,
        )
    calendar = _stored_calendar(store, exchange, (epoch.year,), as_of)
    if calendar is None:
        raise StepFailedError(
            "panel update",
            f"the {exchange} calendar of {epoch.year}, on which the registered epoch "
            f"{epoch.isoformat()} is checked to be an open session, is not stored or cannot be "
            "read",
            steps=SURVIVOR_STEPS,
        )
    if epoch not in calendar.trading_days:
        raise StepFailedError(
            "registration",
            f"the registered epoch {epoch.isoformat()} is not an open {exchange} session: the "
            "paper book opens on its epoch and counts every session from it (ordinal 0), so the "
            "epoch is the session before the first scheduled run",
            exit_code=DailyExit.no_registration,
            steps=SURVIVOR_STEPS,
        )


def _admit_epoch_reach(
    store: PanelStore,
    *,
    survivor: SurvivorRegistration,
    session: date,
    as_of: datetime,
    book_held: bool,
) -> None:
    """Refuse, as a registration the day cannot run (exit 2) and before any request, an epoch
    more than `1 + max_panel_catch_up_sessions` sessions before the session while no book is held
    (decision 3): `paper_head` refuses the same when it opens the book, at step 5 -- after the
    day's requests and factor builds, every night. A book once opened is past this question."""
    from openalpha_cn.strategy_survivor_market import SessionOrdinals

    if book_held:
        return
    adapter = survivor.adapter
    epoch = adapter.epoch_session
    calendar = _survivor_calendar(
        store, adapter.exchange, epoch=epoch, session=session, as_of=as_of
    )
    index = SessionOrdinals(calendar, epoch).index_of(session)
    bound = 1 + adapter.max_panel_catch_up_sessions
    if index > bound:
        raise StepFailedError(
            "registration",
            f"the paper book opens on its epoch {epoch.isoformat()}, {index} sessions before "
            f"this run's {session.isoformat()}, and an epoch may be at most {bound} sessions "
            "before the first run (1 + max_panel_catch_up_sessions): the sessions between were "
            "never caught up by the panel step. Register the epoch as the session before the "
            "first scheduled run. Nothing was fetched or written",
            exit_code=DailyExit.no_registration,
            steps=SURVIVOR_STEPS,
        )


def _survivor_calendar(
    store: PanelStore, exchange: str, *, epoch: date, session: date, as_of: datetime
) -> TradingCalendar:
    """The stored calendar from the epoch's year through the session's, every year of it: the
    paper book counts its sessions on it (`SessionOrdinals`), and a year missing would skip its
    sessions rather than refuse."""
    years = tuple(range(epoch.year, session.year + 1))
    unstored = sorted(set(years) - set(store.registered_years(TRADING_CALENDAR_DATASET)))
    calendar = None if unstored else _stored_calendar(store, exchange, years, as_of)
    if calendar is None:
        raise StepFailedError(
            "panel update",
            f"the {exchange} calendar of {', '.join(map(str, unstored or years))}, on which the "
            f"paper book counts its sessions from its epoch {epoch.isoformat()}, is not stored or "
            "cannot be read",
            steps=SURVIVOR_STEPS,
        )
    return calendar


def _held_now(
    storage: StorageContainer,
    *,
    registration: Registration,
    survivor: SurvivorRegistration,
    lock: PaperBookLock,
) -> tuple[PaperBookSnapshot | None, tuple[str, ...], dict[str, Any] | None]:
    """The paper book as this run finds it under its lock -- its head, the head's holdings and
    the snapshot they are read from (`None` when no book is open yet) -- read only once the book
    is checked to be this registration's (`strategy_survivor_book.require_bound`): what the day
    pins, and checks a pin against, before any request.

    A held book whose marker is missing or is another registration's, and a marker of another
    registration over no book, are refused here -- step `paper head`, nothing fetched or
    written -- and not at step 5 after the day's requests: pinned from another registration's
    book, the day's journal would hold holdings no later run of this registration could match."""
    from openalpha_cn import strategy_survivor_book as paper_book

    try:
        head = paper_book.require_bound(
            state_store=storage.paper_state_store,
            paper_directory=storage.paper_state_store.path.parent,
            registration=survivor,
            config_id=registration.config_id,
            lock=lock,
        )
    except paper_book.SurvivorDayError as error:
        raise StepFailedError(error.step, str(error), steps=SURVIVOR_STEPS) from error
    if head is None:
        return None, (), None
    pinned_from = {
        "session": head.session.isoformat(),
        "session_index": head.session_index,
        "snapshot_id": head.snapshot_id,
    }
    return head, paper_book.held_codes(head), pinned_from


def _require_pinned_head(
    head: PaperBookSnapshot | None,
    *,
    held: Sequence[str],
    held_from: Mapping[str, Any] | None,
    session: date,
    index: int,
    journal: Path,
) -> None:
    """The book this run found (`head`, through `_held_now`) against what the day's journal
    pinned -- `held`, read from the snapshot `held_from` names, or from no book at all -- checked
    before any request; `index` is the session's ordinal.

    Accepted: no book when the journal pinned none (step 5 opens it); the pinned snapshot itself,
    holding `held`; and **any** later head up to the session's own -- which only this session's
    own paper step can have written since its pin (a rerun after an earlier run of the day
    committed some of it). Its holdings are not compared: D3's evening reads the facts again with
    the holdings it finds when they differ from the pinned ones, and a committed session is
    completed from its own snapshot. Refused by name, with nothing fetched or written: a head
    past the session (a later session's run changed the book); a head older than the pinned
    snapshot, or the pinned index holding another snapshot or other names, or no book where the
    journal pinned one (the paper directory was replaced, moved or restored since)."""
    pinned = 0 if held_from is None else int(held_from["session_index"])
    remedy = (
        "Restore the paper directory this day pinned from (its snapshots and its ledger "
        f"together, from one backup), or -- to go on with the book as it is -- move this day's "
        f"journal {journal} aside, and run again: the day then pins the book it finds. Nothing "
        "was fetched or written"
    )
    if head is None:
        if held_from is not None:
            raise StepFailedError(
                "paper head",
                f"this day's journal pinned its holdings {list(held)} from the paper book's "
                f"snapshot {held_from['snapshot_id']} ({held_from['session']}, session index "
                f"{pinned}), and the runtime directory holds no paper book now: its paper "
                f"directory was moved or removed since, and a fresh book would not be the one the "
                f"journal pinned. {remedy}",
                steps=SURVIVOR_STEPS,
            )
        return
    if head.session_index > index:
        raise StepFailedError(
            "paper head",
            f"the paper book is settled through {head.session.isoformat()} (session index "
            f"{head.session_index}), after this day's {session.isoformat()} (index {index}): a "
            "later session's run changed the book since this day pinned its holdings, and a past "
            "evening is not run again. Nothing was fetched or written",
            steps=SURVIVOR_STEPS,
        )
    if head.session_index < pinned:
        raise StepFailedError(
            "paper head",
            f"the paper book's head is session index {head.session_index}, older than the "
            f"snapshot this day pinned its holdings from ({held_from and held_from['snapshot_id']}"
            f", index {pinned}): the paper book was replaced or restored since. {remedy}",
            steps=SURVIVOR_STEPS,
        )
    holds = _held_codes(head)
    replaced = held_from is not None and held_from.get("snapshot_id") != head.snapshot_id
    if head.session_index == pinned and (replaced or holds != tuple(held)):
        raise StepFailedError(
            "paper head",
            f"the paper book's head at session index {pinned} holds {list(holds)}"
            + (f" as {head.snapshot_id}" if replaced else "")
            + f", and this day's journal pinned {list(held)} from "
            + (f"{held_from['snapshot_id']}" if held_from is not None else "no book")
            + f": another run changed the book under the journal. {remedy}",
            steps=SURVIVOR_STEPS,
        )


def _held_codes(snapshot: PaperBookSnapshot) -> tuple[str, ...]:
    from openalpha_cn.strategy_survivor_book import held_codes

    return held_codes(snapshot)


def _nothing_eligible(session: date, screened: ScreenResult) -> str:
    reasons = Counter(reason for verdict in screened.verdicts for reason in verdict.reasons)
    counted = ", ".join(f"{reason} {count}" for reason, count in sorted(reasons.items()))
    return (
        f"no security is eligible on {session.isoformat()}: of the {len(screened.verdicts)} "
        f"assessed, the screen's reasons count {counted or 'nothing'}. An empty record is no "
        "registration (survivor_batch refuses one), so the day stops here, before its prediction "
        "and its paper book: nothing was filed or settled, and a rerun once the data is fixed "
        "runs the day"
    )


def _settle_only_prediction(as_of: datetime, cutoff: datetime) -> dict[str, Any]:
    return {
        "registered": False,
        "settle_only": True,
        "reason": (
            f"the day's pinned clock {as_of.isoformat()} is at or after {cutoff.isoformat()}, the "
            "next session's call auction: its scores would not be a prediction made before their "
            "outcome, so nothing is registered and no decision is made -- the day settles only "
            "(decision 2)"
        ),
    }


def _survivor_prediction(
    runtime_dir: Path,
    store: PanelStore,
    *,
    registration: Registration,
    survivor: SurvivorRegistration,
    facts: FactsDay,
    screened: ScreenResult,
    transform: FactorTransformSpec,
    session: date,
    as_of: datetime,
    clock: Callable[[], datetime],
) -> dict[str, Any]:
    """Step 7: the day's eligible composites as the forward record `survivor_book_composite/v1`
    (`strategy_survivor_registration`), its input provenance filed first and written once, then
    the record through P6's own `register_prediction` -- reused for a day already filed under
    the same declaration, refused for a different answer or once the next call auction began."""
    from openalpha_cn import strategy_survivor_registration as forward

    declared = registration.declared
    exchange = survivor.adapter.exchange
    try:
        batch = forward.survivor_batch(
            facts=facts,
            screen=screened,
            book=survivor.book,
            registered=declared,
            predicted_at=clock(),
        )
        provenance = forward.survivor_input_provenance(
            store,
            facts=facts,
            batch=batch,
            registered=declared,
            transform=transform,
            exchange=exchange,
            recorded_at=clock(),
        )
        forward.write_survivor_provenance(runtime_dir, provenance)
    except forward.SurvivorRegistrationError as error:
        raise StepFailedError("prediction", str(error), steps=SURVIVOR_STEPS) from error
    record, outcome = register_prediction(
        runtime_dir,
        batch,
        calendar=_outcome_calendar(store, exchange, session, as_of),
        clock=clock,
    )
    declaration = record.batch.artifact.declaration
    return {
        "registered": True,
        "record_id": record.record_id,
        "family": declaration.family,
        "horizon": declaration.horizon,
        "outcome": outcome,
        "standing": record.standing,
        "as_of": record.batch.as_of.isoformat(),
        "outcome_known_at": record.outcome_known_at.isoformat(),
        "recorded_at": record.recorded_at.isoformat(),
        "scored": len(record.batch.scored),
        "abstained": len(record.batch.abstained),
        "provenance": provenance.digest,
    }


def _opening_result(
    *,
    journal: Mapping[str, Any],
    session: date,
    as_of: datetime,
    head: PaperHead,
    factors: Mapping[str, Any],
) -> dict[str, Any]:
    """The result of a day on the epoch, which only opens the paper book."""
    from openalpha_cn.strategy_survivor_summary import SURVIVOR_WORDING

    snapshot = head.snapshot
    return {
        "kind": SURVIVOR_RESULT_KIND,
        "session": session.isoformat(),
        "session_index": head.session_index,
        "as_of": as_of.isoformat(),
        "registration": journal["registration"],
        "panel": journal["panel"],
        "factors": dict(factors),
        "opening": {
            "epoch_session": snapshot.session.isoformat(),
            "snapshot_id": snapshot.snapshot_id,
            "cash": f"{snapshot.book.cash}",
            "config_digest": snapshot.config_digest,
        },
        "prediction": {
            "registered": False,
            "reason": "the epoch session only opens the paper book: no facts are read and "
            "nothing is registered; the first evening, and the first record, are the next "
            "session's",
        },
        "wording": SURVIVOR_WORDING,
    }


def _survivor_paper_and_summary(
    day: SurvivorDay, *, clock: Callable[[], datetime], counts: Counter[str]
) -> dict[str, Any]:
    """Steps 8 and 9 of the survivor day: the paper book -- the catch-up, then the evening, or on
    a settle-only day the settlement alone -- and the summary, `V2-P7-008` D5b's. Until they are
    wired the day stops here by name, after its record was filed: exit 1 with the requests spent,
    and no `result` in the journal, so a rerun runs the paper book once it exists."""
    raise StepFailedError("paper book", "not wired yet (V2-P7-008 D5b)", steps=SURVIVOR_STEPS)


def _this_run(counts: Counter[str], *, wrote: bool) -> dict[str, Any]:
    return {
        "requests": dict(sorted(counts.items())) if wrote else {},
        "request_count": sum(counts.values()) if wrote else 0,
        "wrote": wrote,
    }


def _run_survivor_day(
    options: DailyOptions,
    registration: Registration,
    survivor: SurvivorRegistration,
    *,
    clock: Callable[[], datetime],
    counts: Counter[str],
) -> dict[str, Any]:
    """The survivor book's day, `SURVIVOR_STEPS`, every step inside `_step(..., steps=
    SURVIVOR_STEPS)` so that an unexpected fault is that step's refusal, numbered in the survivor
    order and printed with the requests spent. Glue only: what each step does is
    `strategy_survivor_book` (the paper book), `strategy_survivor_facts` (the facts day),
    `domain.survivor_screen` (the screen) and `strategy_survivor_registration` (the record).

    0. **The lock first.** Right after the registration is admitted, the paper book's writer
       lock (`paper_book_lock`) is taken and held to the end of the day; the token is what the
       paper book's functions require. Another run holding it is refused here, named under step
       1 (`registration`) -- the lock is taken before step 2 -- having fetched and written
       nothing.
    1. **Pinned before any request.** The day's journal, `RT/daily_survivor/<config_id[:16]>/
       <session>.json` (`openalpha-daily-survivor/v1`, its own reader), is written first with the
       day's `as_of` (as P6 pins it) and the book's holdings as found under the lock (`held`, and
       `held_from`, the snapshot they are read from): every later step of this run and of any
       rerun uses them. A journal with `result` is printed again with no request and no write. An
       epoch after the session (P6's `session < anchor`) or not an open session of the stored
       calendar exits 2 here, before the journal.
    2. **Panel update** as P6's, with `SurvivorPanelUpdate` from the registration's adapter
       (`survivor_panel_update`); journalled, and resumed after on a rerun. 3. **Doctor** and 4.
       **factor build** as P6's: the composite factors at the processed tier (`survivor_builds`).
    5. **Paper head** (`paper_head`, which binds the marker and opens the book on every run),
       checked against the pin (`_require_pinned_head`). A day on the epoch only opens the book,
       files its result and ends (exit 0).
    6. **Facts** with the pinned `held`, and the screen; nothing eligible is refused by name.
    7. **Prediction**, before the paper step so that a fault of the paper book never costs the
       record; journalled, and reused on a rerun. A day whose pinned `as_of` is at or after 09:15
       of the next session registers nothing and is no error: it settles only (decision 2).
    8.-9. The paper book and the summary: `_survivor_paper_and_summary` (D5b).

    **The P6 day's options here.** `--full-update` and `--top` have no survivor meaning and are
    refused before anything is made (exit 2). `--dataset` replaces the targets as on the P6 day
    (`options.targets or targets_for_survivor(builds)`), but only by a superset of
    `targets_for_survivor`, and anything narrower is refused the same way (a record must not be
    filed from a panel the day did not update); `--max-staleness-days` is the factor
    build's bound, as on the P6 day; `--as-of` pins the first run's clock; `--json` prints the
    day's result document; `--pin-worktree` is unchanged (it never reaches a day).
    """
    with _step("registration", steps=SURVIVOR_STEPS):
        _refuse_p6_only_options(options, survivor)
        # Imported here, never for a P6 day: it loads `backtest/paper.py` and its audit hook.
        from openalpha_cn import strategy_survivor_book as paper_book
    with contextlib.ExitStack() as held:
        with _step("registration", steps=SURVIVOR_STEPS):
            try:
                lock = held.enter_context(
                    paper_book.paper_book_lock(options.runtime_dir / PAPER_DIRECTORY)
                )
            except paper_book.SurvivorDayError as error:
                # `PaperBookBusyError` above all: the lock is taken before step 2, so its refusal
                # is step 1's, whatever step the paper book module names.
                raise StepFailedError("registration", str(error), steps=SURVIVOR_STEPS) from error
        return _survivor_day(options, registration, survivor, lock=lock, clock=clock, counts=counts)


def _survivor_day(
    options: DailyOptions,
    registration: Registration,
    survivor: SurvivorRegistration,
    *,
    lock: PaperBookLock,
    clock: Callable[[], datetime],
    counts: Counter[str],
) -> dict[str, Any]:
    """Steps 1 to 7 of the survivor day under the paper book's lock (`_run_survivor_day`)."""
    from openalpha_cn import strategy_survivor_book as paper_book
    from openalpha_cn.domain import survivor_screen
    from openalpha_cn.strategy_survivor_market import SessionOrdinals

    runtime_dir = options.runtime_dir
    adapter = survivor.adapter
    exchange, epoch = adapter.exchange, adapter.epoch_session
    with _step("registration", steps=SURVIVOR_STEPS):
        store = panel_store(runtime_dir)
        first_as_of = options.as_of or clock()
        builds = survivor_builds(survivor)
        targets = options.targets or targets_for_survivor(builds)
        # Resolved once, and the one object every reader of the processed tier is handed (D3
        # review, rule 7).
        transform = FACTOR_TRANSFORMS.get(adapter.transform)

    with _step("panel update", steps=SURVIVOR_STEPS), counted_transport(counts):
        session = _survivor_session(runtime_dir, store, exchange, first_as_of)
        _admit_epoch(store, epoch=epoch, session=session, exchange=exchange, as_of=first_as_of)
        path = survivor_journal_directory(runtime_dir, registration) / f"{session.isoformat()}.json"
        journal = read_survivor_journal(path)
        if journal is not None and journal.get("registration", {}).get("sha256") not in (
            None,
            registration.sha256,
        ):
            raise StepFailedError(
                "summary",
                f"{path} was written under another registration of this configuration",
                steps=SURVIVOR_STEPS,
            )
        if journal is not None and survivor_journal_complete(journal):
            return {**journal["result"], "this_run": _this_run(counts, wrote=False)}
        storage = build_storage(runtime_dir=runtime_dir, clock=clock)
        book, held_now, held_from_now = _held_now(
            storage, registration=registration, survivor=survivor, lock=lock
        )
        _admit_epoch_reach(
            store, survivor=survivor, session=session, as_of=first_as_of, book_held=book is not None
        )
        if journal is None:
            # The day's clock and the book's holdings, pinned before anything is fetched: a retry
            # asks the same questions of the same book (P6's pin, and the paper book's).
            journal = {
                "schema": SURVIVOR_JOURNAL_SCHEMA,
                "session": session.isoformat(),
                "as_of": first_as_of.isoformat(),
                "held": list(held_now),
                "held_from": held_from_now,
                "registration": {
                    "path": registration.path.name,
                    "sha256": registration.sha256,
                    "commit": registration.commit,
                    "config_id": registration.config_id,
                },
            }
            write_journal(path, journal)
        as_of = datetime.fromisoformat(journal["as_of"])
        held = tuple(journal["held"])
        held_from = journal["held_from"]
        calendar = _survivor_calendar(store, exchange, epoch=epoch, session=session, as_of=as_of)
        ordinals = SessionOrdinals(calendar, epoch)
        _require_pinned_head(
            book,
            held=held,
            held_from=held_from,
            session=session,
            index=ordinals.index_of(session),
            journal=path,
        )
        if "panel" not in journal:
            panel = update_panel(
                runtime_dir,
                session=session,
                as_of=as_of,
                exchange=exchange,
                targets=targets,
                next_year=next_year_calendar_needed(
                    calendar, session=session, horizon=SURVIVOR_OUTCOME_SESSIONS
                ),
                survivor=survivor_panel_update(survivor),
            )
            journal = {**journal, "panel": {**panel, "requests": dict(sorted(counts.items()))}}
            write_journal(path, journal)

    with _step("panel doctor", steps=SURVIVOR_STEPS):
        check_panel(
            runtime_dir,
            datasets=doctor_datasets(journal["panel"]["targets"]),
            session=session,
            as_of=as_of,
            exchange=exchange,
            first_of_year=session
            == min(day for day in calendar.trading_days if day.year == session.year),
        )
    with _step("factor build", steps=SURVIVOR_STEPS):
        factors = build_factors(
            store,
            builds,
            session=session,
            as_of=as_of,
            exchange=exchange,
            max_staleness_days=options.max_staleness_days,
            runtime_dir=runtime_dir,
        )
    with _step("paper head", steps=SURVIVOR_STEPS):
        try:
            head = paper_book.paper_head(
                state_store=storage.paper_state_store,
                registration=survivor,
                ordinals=ordinals,
                session=session,
                paper_directory=storage.paper_state_store.path.parent,
                config_id=registration.config_id,
                lock=lock,
            )
        except paper_book.SurvivorDayError as error:
            raise StepFailedError(error.step, str(error), steps=SURVIVOR_STEPS) from error
        # By the pinned clock, never this run's: a rerun is the day it was (D3 review, rule 4).
        cutoff = registration_cutoff(_outcome_calendar(store, exchange, session, as_of), session)
        settle_only = as_of >= cutoff
    if head.opened_only:
        with _step("summary", steps=SURVIVOR_STEPS):
            result = _opening_result(
                journal=journal, session=session, as_of=as_of, head=head, factors=factors
            )
            write_journal(path, {**journal, "result": result})
        return {**result, "this_run": _this_run(counts, wrote=True)}

    with _step("facts", steps=SURVIVOR_STEPS):
        try:
            facts = paper_book.facts_day(
                panel=store,
                registration=survivor,
                session=session,
                as_of=as_of,
                held=held,
                min_coverage=SETTLE_ONLY_COVERAGE if settle_only else None,
                transform=transform,
            )
        except paper_book.SurvivorDayError as error:
            raise StepFailedError(error.step, str(error), steps=SURVIVOR_STEPS) from error
        screened = survivor_screen.screen(facts.facts, survivor.book)
        if not settle_only and not any(verdict.eligible for verdict in screened.verdicts):
            raise StepFailedError(
                "facts", _nothing_eligible(session, screened), steps=SURVIVOR_STEPS
            )
    with _step("prediction", steps=SURVIVOR_STEPS):
        prediction = journal.get("prediction")
        if prediction is None:
            prediction = (
                _settle_only_prediction(as_of, cutoff)
                if settle_only
                else _survivor_prediction(
                    runtime_dir,
                    store,
                    registration=registration,
                    survivor=survivor,
                    facts=facts,
                    screened=screened,
                    transform=transform,
                    session=session,
                    as_of=as_of,
                    clock=clock,
                )
            )
            journal = {**journal, "prediction": prediction}
            write_journal(path, journal)
    day = SurvivorDay(
        options=options,
        registration=registration,
        survivor=survivor,
        lock=lock,
        storage=storage,
        store=store,
        journal_path=path,
        journal=journal,
        session=session,
        as_of=as_of,
        held=held,
        calendar=calendar,
        ordinals=ordinals,
        transform=transform,
        head=head,
        facts=facts,
        screen=screened,
        factors=factors,
        prediction=prediction,
        settle_only=settle_only,
    )
    return _survivor_paper_and_summary(day, clock=clock, counts=counts)


# --- the face ----------------------------------------------------------------------------------


def summary_lines(result: Mapping[str, Any], *, top: int) -> list[str]:
    """The summary the plan lists: session, list id, top N, weights, record id, requests."""
    listed = result["candidates"]
    targets = result["targets"]
    prediction = result["prediction"]
    run = result["this_run"]
    lines = [
        f"session            {result['session']} (as of {result['as_of']})",
        f"candidate list     {listed['candidate_list_id']}"
        + (" (the source held: no ranking)" if listed["held"] else ""),
    ]
    for row in listed["candidates"][:top]:
        lines.append(f"  {row['rank']:>4}  {row['ts_code']:<10} {row['score']:+.6f}")
    lines.append(
        f"target weights     {targets['decision']} ({targets['reason']}), "
        f"{len(targets['weights'])} name(s), cash {targets['cash']}, "
        f"turnover {targets['turnover']} (previous: {targets['previous_session'] or 'none'})"
    )
    for name, weight in targets["weights"].items():
        lines.append(f"  {name:<10} {weight}")
    if prediction["registered"]:
        lines.append(
            f"prediction         {prediction['record_id']} {prediction['standing']} "
            f"({prediction['outcome']}; outcome knowable {prediction['outcome_known_at']})"
        )
    else:
        lines.append(f"prediction         none: {prediction['reason']}")
    # A journal written before `V2-P6-032` has no `caught_up`; it caught nothing up.
    caught_up = result.get("factors", {}).get("caught_up")
    if caught_up:
        lines.append(f"factor catch-up    {', '.join(caught_up)}")
    lines.append(
        f"tushare requests   {run['request_count']} this run "
        f"{json.dumps(run['requests'], sort_keys=True)}"
        + ("" if run["wrote"] else " -- the day was already complete; nothing was run")
    )
    sweep = result.get("panel", {}).get("industry_sweep")
    if sweep:
        lines.append(f"industry sweep     {sweep}")
    lines.append(f"wording            {result['wording']}")
    return lines


def survivor_summary_lines(result: Mapping[str, Any]) -> list[str]:
    """What a survivor day prints without `--json` (`V2-P7-008`): the opening of a day on the
    epoch here; an evening's document through `strategy_survivor_summary.summary_lines`, which
    D5b fills. Never the P6 summary, whose keys a survivor result does not have."""
    if "opening" not in result:
        from openalpha_cn.strategy_survivor_summary import summary_lines as evening_lines

        return evening_lines(result)
    opening = result["opening"]
    run = result["this_run"]
    lines = [
        f"{'session':<18} {result['session']} (index {result['session_index']}, the paper "
        f"book's epoch; as of {result['as_of']})",
        f"{'book':<18} opened on its epoch with cash {opening['cash']} "
        f"({opening['snapshot_id']}); no evening is run on the epoch -- the first is the next "
        "session's; ledger equity is not evidence",
        f"{'prediction':<18} none: {result['prediction']['reason']}",
    ]
    caught_up = result.get("panel", {}).get("caught_up")
    if caught_up:
        lines.append(f"{'panel catch-up':<18} {', '.join(caught_up)}")
    built = result.get("factors", {}).get("caught_up")
    if built:
        lines.append(f"{'factor catch-up':<18} {', '.join(built)}")
    lines.append(
        f"{'tushare requests':<18} {run['request_count']} this run "
        f"{json.dumps(run['requests'], sort_keys=True)}"
        + ("" if run["wrote"] else " -- the day was already complete; nothing was run")
    )
    lines.append(f"{'wording':<18} {result['wording']}")
    return lines


# --- the scheduled run: a checkout pinned at the registration, and its launchd job ------------


def _git_output(repo: Path, *args: str, what: str) -> str:
    """Run git in `repo` through the registry's runner (no inherited `GIT_*`) and return stdout,
    or refuse naming what was being done."""
    run = registry._git(repo, *args)
    if run.returncode != 0:
        raise StepFailedError(
            "registration",
            f"could not {what}: {run.stderr.decode(errors='replace').strip()}",
        )
    return run.stdout.decode().strip()


def pin_worktree(registration: Path, repo: Path, directory: Path) -> str:
    """Create, or move, a detached worktree at the registration's commit; return that commit.

    The scheduled run stands there rather than in the development checkout. The commit is the one
    that last touched the registration on `repo`'s `HEAD`, refused unless the bound code there is
    exactly the registered `code_commit` (`registry.registered_checkout`) -- so the worktree
    passes the daily command's own admission however far development has moved on, and a commit
    in the development checkout can no longer cost a forward day. An existing `directory` must
    be a clean worktree of the same repository; it is moved to the commit, never overwritten.
    """
    try:
        root, commit, _code = registry.registered_checkout(
            registration, repo, also_bound=(THIS_SCRIPT,)
        )
    except registry.HoldoutRefusedError as error:
        raise StepFailedError(
            "registration",
            f"{type(error).__name__}: {error}",
            exit_code=DailyExit.code_not_registered,
        ) from error
    common = Path(_git_output(root, "rev-parse", "--git-common-dir", what="read the repository"))
    common = (root / common).resolve() if not common.is_absolute() else common.resolve()
    if not directory.exists():
        directory.parent.mkdir(parents=True, exist_ok=True)
        _git_output(
            root, "worktree", "add", "--detach", str(directory), commit, what="add the worktree"
        )
    else:
        theirs = Path(
            _git_output(directory, "rev-parse", "--git-common-dir", what="read the worktree")
        )
        theirs = (directory / theirs).resolve() if not theirs.is_absolute() else theirs.resolve()
        if theirs != common:
            raise StepFailedError(
                "registration", f"{directory} is not a worktree of {root}; nothing was changed"
            )
        dirty = _git_output(
            directory, "status", "--porcelain", "--untracked-files=all", what="read its status"
        )
        if dirty:
            raise StepFailedError(
                "registration",
                f"{directory} has uncommitted changes; a pinned checkout is never edited, and "
                "nothing was changed",
            )
        _git_output(directory, "checkout", "-q", "--detach", commit, what="move the worktree")
    return _git_output(directory, "rev-parse", "HEAD", what="read the worktree's commit")


PINNED_ENVIRONMENT: Final[str] = ".venv"
"""The pinned checkout's own environment, `<worktree>/.venv` (git-ignored)."""

ENV_FILE: Final[str] = ".env"
"""The credentials file, git-ignored: the main checkout's, and the link to it in the worktree."""


_EXTENDED_LENGTH: Final[str] = "\\\\?\\"
_EXTENDED_LENGTH_UNC: Final[str] = "\\\\?\\UNC\\"


def link_text_as_path(text: str, *, windows: bool = os.name == "nt") -> str:
    """The path a symbolic link was made to, from what `os.readlink` returned for it.

    On Windows `os.readlink` gives an absolute target back with the extended-length prefix --
    `\\\\?\\C:\\repo\\.env`, `\\\\?\\UNC\\server\\share\\.env` -- and no `Path` comparison takes
    that for `C:\\repo\\.env`, so `link_env_file` refused its own link the second time
    (`V2-P6-034`). The prefix is dropped on Windows only: on POSIX a link's text is its path,
    whatever it spells.
    """
    if not windows:
        return text
    if text.startswith(_EXTENDED_LENGTH_UNC):
        return "\\\\" + text[len(_EXTENDED_LENGTH_UNC) :]
    if text.startswith(_EXTENDED_LENGTH):
        return text[len(_EXTENDED_LENGTH) :]
    return text


def link_env_file(repo: Path, worktree: Path) -> str:
    """Link `<worktree>/.env` to `<repo>/.env` (`V2-P6-029`); return one line saying what happened.

    uv splits `--env-file` on whitespace -- `--env-file "/x/env test/.env"` fails with "No
    environment file found at: `test/.env`", and so does `UV_ENV_FILE` -- and the main checkout's
    path has a space in it. The scheduled run therefore loads the relative `.env` from the
    worktree it stands in, and this link is what that name resolves to. Only the link is made:
    the file is never opened, so no credential is read here. `.env` is git-ignored, so the
    worktree stays clean and `pin_worktree` can still move it.

    No `.env` in `repo` links nothing and says so. An existing link to the same file is finished
    work -- read back through `link_text_as_path`, so Windows' `\\\\?\\` spelling of it is too.
    Anything else called `.env` in the worktree -- an ordinary file, or a link to another
    file -- is refused and left as it was. A link the platform will not make (Windows without
    Developer Mode or an elevated shell) is a named refusal.
    """
    source = repo / ENV_FILE
    link = worktree / ENV_FILE
    if not source.is_file():
        return (
            f"no {source}; nothing was linked, and a run from {worktree} will find no "
            f"{ENV_FILE} (create it, then --pin-worktree again)"
        )
    if link.is_symlink():
        target = Path(os.path.normpath(link.parent / link_text_as_path(os.readlink(link))))
        if target != Path(os.path.normpath(source)):
            raise StepFailedError(
                "registration",
                f"{link} already links to {target}, not {source}; it was not changed",
            )
        return f"{link} is already linked to {source}"
    if link.exists():
        raise StepFailedError(
            "registration",
            f"{link} is a file of its own; it was not replaced by a link to {source}",
        )
    try:
        link.symlink_to(source)
    except OSError as error:
        raise StepFailedError(
            "registration",
            f"could not link {link} to {source}: {error}. On Windows a symbolic link needs "
            "Developer Mode on, or an elevated shell; nothing was linked",
        ) from error
    return f"linked {link} -> {source}"


def worktree_python(worktree: Path) -> str:
    """The `major.minor` the pinned checkout's `.python-version` names, or refuse."""
    source = worktree / PYTHON_VERSION_FILE
    if not source.is_file():
        raise StepFailedError(
            "registration",
            f"{source} does not exist, so the interpreter the pinned environment must be built on "
            "is not known; nothing was synced",
        )
    return pinned_python(source.read_text(encoding="utf-8"), source=str(source))


def environment_sync_command(
    worktree: Path, *, uv: Path, python: str
) -> tuple[list[str], dict[str, str]]:
    """The command that creates the pinned checkout's environment, and its environment variables.

    `uv sync --frozen --offline` against `<worktree>/uv.lock`, into `<worktree>/.venv`:
    `--frozen` installs the lock as it is and never re-resolves it, `--offline` and
    `--no-python-downloads` never touch the network -- a package uv's cache does not hold, or an
    interpreter not already on this machine, is a refusal, not a download. `--python` is the
    checkout's `.python-version` (`worktree_python`), and `--all-extras` (the dev group is uv's
    default) makes it the environment the research ran in, `uv sync --all-extras --dev`. The
    inherited `VIRTUAL_ENV`, `PYTHONPATH` and `UV_PYTHON` are dropped, so the sync cannot target,
    import from, or pick the interpreter of, the development checkout's environment.
    """
    environment = {
        key: value
        for key, value in os.environ.items()
        if key not in {"VIRTUAL_ENV", "PYTHONPATH", "UV_PROJECT_ENVIRONMENT", "UV_PYTHON"}
    }
    environment["UV_PROJECT_ENVIRONMENT"] = str(worktree / PINNED_ENVIRONMENT)
    command = [
        str(uv),
        "sync",
        "--frozen",
        "--offline",
        "--no-python-downloads",
        "--all-extras",
        "--python",
        python,
        "--project",
        str(worktree),
    ]
    return command, environment


def _run_sync(
    command: Sequence[str], *, environment: Mapping[str, str], cwd: Path
) -> subprocess.CompletedProcess[bytes]:
    """Run the sync. A seam, so the tests assert the command without ever running uv."""
    return subprocess.run(list(command), env=dict(environment), cwd=cwd, capture_output=True)


_UNCACHED = re.compile(r"`([A-Za-z0-9_.\-]+)==([^`\s]+)`")


def environment_python(environment: Path) -> str | None:
    """The full Python version a virtual environment was built on, from its `pyvenv.cfg`."""
    configuration = environment / "pyvenv.cfg"
    if not configuration.is_file():
        return None
    for line in configuration.read_text(encoding="utf-8").splitlines():
        key, _, value = line.partition("=")
        if key.strip() in {"version_info", "version"}:
            return value.strip()
    return None


def sync_pinned_environment(worktree: Path, *, uv: Path) -> Path:
    """Create or refresh `<worktree>/.venv` from `<worktree>/uv.lock`, offline, on the
    interpreter `<worktree>/.python-version` names; return its path.

    Refused by name when uv's cache lacks a package: the operator is told which, and the one
    online command that fills the cache -- which this command never runs. After the sync the
    environment's `pyvenv.cfg` is read back, and one built on another `major.minor` is refused.
    """
    python = worktree_python(worktree)
    command, environment = environment_sync_command(worktree, uv=uv, python=python)
    finished = _run_sync(command, environment=environment, cwd=worktree)
    if finished.returncode != 0:
        output = finished.stderr.decode(errors="replace")
        missing = sorted({f"{name}=={version}" for name, version in _UNCACHED.findall(output)})
        online = (
            f"UV_PROJECT_ENVIRONMENT={shlex.quote(str(worktree / PINNED_ENVIRONMENT))} "
            f"{shlex.quote(str(uv))} sync --frozen --all-extras --python {shlex.quote(python)} "
            f"--project {shlex.quote(str(worktree))}"
        )
        cause = (
            f"uv's cache holds no copy of {', '.join(missing)}"
            if missing
            else "uv refused: " + " | ".join(output.strip().splitlines()[-4:])
        )
        raise StepFailedError(
            "registration",
            f"the pinned environment {worktree / PINNED_ENVIRONMENT} could not be created offline "
            f"from {worktree / LOCKFILE}: {cause}. Nothing was downloaded. To fill the cache, run "
            f"once, online: {online} -- then --pin-worktree again",
        )
    built = environment_python(worktree / PINNED_ENVIRONMENT)
    if built is None or ".".join(built.split(".")[:2]) != python:
        raise StepFailedError(
            "registration",
            f"the pinned environment {worktree / PINNED_ENVIRONMENT} runs Python "
            f"{built or 'of no recorded version'}, and {worktree / PYTHON_VERSION_FILE} pins "
            f"{python}; the scheduled run would compute on an interpreter the research did not",
        )
    return worktree / PINNED_ENVIRONMENT


LAUNCHD_LABEL: Final[str] = "com.openalpha.daily-selection"


def launchd_plist(
    *,
    worktree: Path,
    runtime_dir: Path,
    log_dir: Path,
    env_file: str = ENV_FILE,
    uv: Path,
) -> str:
    """The launchd job that runs this command from the pinned worktree at 18:30 on weekdays.

    Printed, never installed. No shell runs it: `ProgramArguments` is the argument vector itself
    and `WorkingDirectory`/`EnvironmentVariables` set the rest, so no path is ever parsed by a
    shell and a `"`, `$` or space in one cannot break it or expand.

    **The interpreter is the pinned checkout's own**, `<worktree>/.venv/bin/python`, which
    `--pin-worktree` synced offline from the worktree's `uv.lock` (`sync_pinned_environment`).
    Its editable install of the project points at `<worktree>/src`, so no `PYTHONPATH` is set and
    no shared environment is named; `uv run --no-sync` is there only to load `--env-file`, and
    `UV_PROJECT_ENVIRONMENT` keeps it on the same environment. Step 1 then compares that
    interpreter's packages with the lock (`admit_environment`).

    **The env file is a name relative to `WorkingDirectory`, never a path** (`V2-P6-029`): uv splits
    `--env-file` on whitespace, so any path through the main checkout's directory fails (`No
    environment file found at: test/.env`). `--pin-worktree` links `<worktree>/.env` to the main
    checkout's (`link_env_file`); `env_file` containing whitespace is refused here. launchd has
    no exchange calendar,
    so it fires every weekday and the command decides: on a holiday the newest closed session is
    one whose journal is already complete, which prints the summary again with no request and no
    write.
    """
    if any(character.isspace() for character in env_file):
        raise ValueError(
            f"--env-file {env_file!r} contains whitespace: uv splits --env-file on whitespace, "
            f"so this job would fail at 18:30. Name the file relative to --worktree "
            f"(default {ENV_FILE}); --pin-worktree links {ENV_FILE} to the main checkout's"
        )
    job = {
        "Label": LAUNCHD_LABEL,
        "ProgramArguments": [
            str(uv),
            "run",
            "--no-sync",
            "--env-file",
            env_file,
            str(worktree / PINNED_ENVIRONMENT / "bin" / "python"),
            str(worktree / THIS_SCRIPT),
            "--runtime-dir",
            str(runtime_dir),
            "--repo",
            str(worktree),
            "--registration",
            str(worktree / REGISTRATION_IN_REPOSITORY),
        ],
        "WorkingDirectory": str(worktree),
        "EnvironmentVariables": {
            "UV_PROJECT_ENVIRONMENT": str(worktree / PINNED_ENVIRONMENT),
            "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
        },
        "StartCalendarInterval": [
            {"Weekday": weekday, "Hour": 18, "Minute": 30} for weekday in range(1, 6)
        ],
        "StandardOutPath": str(log_dir / "daily-selection.out.log"),
        "StandardErrorPath": str(log_dir / "daily-selection.err.log"),
        "RunAtLoad": False,
    }
    return plistlib.dumps(job, sort_keys=False).decode("utf-8")


def _instant(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise argparse.ArgumentTypeError(f"{value!r} carries no UTC offset")
    return parsed


def main(argv: Sequence[str] | None = None, *, clock: Callable[[], datetime] = _utc_now) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("--runtime-dir", type=Path, default=None)
    parser.add_argument("--registration", type=Path, default=DEFAULT_REGISTRATION)
    parser.add_argument("--repo", type=Path, default=REPOSITORY)
    parser.add_argument(
        "--as-of",
        type=_instant,
        default=None,
        help="Pin the day's clock (default: now); the newest session published then is the day.",
    )
    parser.add_argument(
        "--dataset",
        action="append",
        default=None,
        help="A panel target to update, repeatable, in place of the targets the configuration "
        "reads (industries are still added on a day that reads them).",
    )
    parser.add_argument(
        "--full-update",
        action="store_true",
        help="Update every year-scoped target (FULL_UPDATE_TARGETS), whatever the configuration "
        "reads.",
    )
    parser.add_argument("--max-staleness-days", type=int, default=DEFAULT_MAX_STALENESS_DAYS)
    parser.add_argument("--top", type=int, default=None, help="How many candidates to print.")
    parser.add_argument("--json", action="store_true", help="Print the day's result as JSON.")
    parser.add_argument(
        "--pin-worktree",
        type=Path,
        default=None,
        metavar="DIR",
        help="Create or move a detached worktree at the registration's commit, sync its own "
        "environment (<DIR>/.venv) offline from its uv.lock, print the commit and exit. The "
        "scheduled run stands there.",
    )
    parser.add_argument(
        "--launchd-plist",
        type=Path,
        default=None,
        metavar="LOG_DIR",
        help="Print the launchd job (run from --worktree), logging to LOG_DIR, and exit. "
        "Installs nothing.",
    )
    parser.add_argument("--worktree", type=Path, default=None, help="The pinned worktree.")
    parser.add_argument(
        "--env-file",
        default=ENV_FILE,
        help="The file uv loads, relative to --worktree (default: .env, which --pin-worktree "
        "links to <repo>/.env). Whitespace is refused: uv splits --env-file on it.",
    )
    parser.add_argument("--uv", type=Path, default=None, help="Default: the uv on PATH.")
    parser.add_argument(
        "--factor-gaps",
        type=date.fromisoformat,
        default=None,
        metavar="SINCE",
        help="Read only: list every open session from SINCE (YYYY-MM-DD) through the newest "
        "closed one at which a factor tier the registered configuration reads has no build, and "
        "exit 1 when there is one. Admits no code and writes nothing.",
    )
    arguments = parser.parse_args(argv)
    uv = (arguments.uv or Path(shutil.which("uv") or "uv")).resolve()
    try:
        if arguments.pin_worktree is not None:
            directory = arguments.pin_worktree.resolve()
            commit = pin_worktree(
                arguments.registration.resolve(), arguments.repo.resolve(), directory
            )
            environment = sync_pinned_environment(directory, uv=uv)
            print(f"pinned {directory} at {commit}; environment {environment} synced offline")
            print(link_env_file(arguments.repo.resolve(), directory))
            return int(DailyExit.done)
    except StepFailedError as error:
        print(str(error), file=sys.stderr)
        return int(error.exit_code)
    if arguments.runtime_dir is None:
        parser.error("--runtime-dir is required")
    if arguments.factor_gaps is not None:
        try:
            report = factor_gaps(
                arguments.runtime_dir,
                arguments.registration,
                since=arguments.factor_gaps,
                as_of=arguments.as_of or clock(),
            )
        except FactorGapError as error:
            print(f"factor gaps: {error}", file=sys.stderr)
            return int(DailyExit.step_failed)
        if arguments.json:
            print(json.dumps(report, sort_keys=True))
        else:
            print("\n".join(factor_gap_lines(report)))
        return int(DailyExit.step_failed if any(report["gaps"].values()) else DailyExit.done)
    if arguments.launchd_plist is not None:
        if arguments.worktree is None:
            parser.error("--launchd-plist needs --worktree, the checkout pinned by --pin-worktree")
        try:
            job = launchd_plist(
                worktree=arguments.worktree.resolve(),
                runtime_dir=arguments.runtime_dir.resolve(),
                log_dir=arguments.launchd_plist.resolve(),
                env_file=arguments.env_file,
                uv=uv,
            )
        except ValueError as error:
            parser.error(str(error))
        print(job, end="")
        return int(DailyExit.done)
    options = DailyOptions(
        runtime_dir=arguments.runtime_dir,
        registration=arguments.registration,
        repo=arguments.repo,
        as_of=arguments.as_of,
        targets=None if arguments.dataset is None else tuple(arguments.dataset),
        full_update=arguments.full_update,
        max_staleness_days=arguments.max_staleness_days,
        json_output=arguments.json,
        top=arguments.top,
    )
    try:
        result = run_daily_selection(options, clock=clock)
    except StepFailedError as error:
        print(str(error), file=sys.stderr)
        print(
            f"tushare requests   {sum(error.requests.values())} this run "
            f"{json.dumps(error.requests, sort_keys=True)}",
            file=sys.stderr,
        )
        return int(error.exit_code)
    if arguments.json:
        print(json.dumps(result, sort_keys=True, ensure_ascii=False))
    elif result.get("kind") == SURVIVOR_RESULT_KIND:
        print("\n".join(survivor_summary_lines(result)))
    else:
        top = arguments.top if arguments.top is not None else result["targets"]["holding_count"]
        print("\n".join(summary_lines(result, top=int(top))))
    return int(DailyExit.done)


if __name__ == "__main__":
    sys.exit(main())
