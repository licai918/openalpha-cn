"""Command-line entry point for OpenAlpha CN."""

import json
import logging
import os
import platform
import sys
import textwrap
from collections.abc import Iterator, Mapping, Sequence, Set
from contextlib import contextmanager
from datetime import MAXYEAR, MINYEAR, UTC, date, datetime, time, timedelta
from decimal import Decimal
from enum import IntEnum, StrEnum
from pathlib import Path
from time import monotonic
from types import MappingProxyType
from typing import Annotated, Final, cast
from zoneinfo import ZoneInfo

import typer
import uvicorn
from pydantic import ValidationError

from openalpha_cn import __version__
from openalpha_cn.backtest.factor_experiment import FactorExperimentRecord
from openalpha_cn.backtest.factor_ic import MINIMUM_IC_SECURITIES, ICMethod
from openalpha_cn.backtest.factor_redundancy import MINIMUM_REDUNDANCY_SECURITIES
from openalpha_cn.backtest.multiple_testing import DependenceAssumption
from openalpha_cn.backtest.outcome_statistics import (
    OutcomeStatisticsError,
    OutcomeStatisticsReport,
    outcome_statistics_view,
)
from openalpha_cn.backtest.portfolio import PortfolioLimits
from openalpha_cn.backtest.portfolio_policy import (
    PortfolioConstruction,
    PortfolioConstructionError,
    PortfolioConstructionPolicy,
    candidates_from_shortlist_answer,
    construct_portfolio,
    construction_view,
)
from openalpha_cn.backtest.replay import ReplayCorpus
from openalpha_cn.backtest.segmented_reporting import (
    SegmentationPlan,
    SegmentedReport,
    SegmentedReportingError,
    segmented_report_view,
)
from openalpha_cn.backtest.turnover_variants import (
    TurnoverCostModel,
    TurnoverVariantError,
    TurnoverVariantReport,
    turnover_variant_view,
)
from openalpha_cn.config import ConfigError, load_config, load_dotenv, load_log_level
from openalpha_cn.domain.adjustment import ADJ_FACTOR_DATASET, AdjustmentError
from openalpha_cn.domain.daily_prices import (
    DAILY_AVAILABILITY_TIME,
    DAILY_BASIC_DATASET,
    DAILY_DATASET,
    PriceDataError,
)
from openalpha_cn.domain.factor import FactorError, FactorNote
from openalpha_cn.domain.financial_statements import (
    BALANCE_SHEET_DATASET,
    CASH_FLOW_DATASET,
    FINANCIAL_INDICATOR_DATASET,
    FINANCIAL_STATEMENT_DATASETS,
    INCOME_DATASET,
    FinancialStatementError,
)
from openalpha_cn.domain.index_membership import (
    INDEX_WEIGHT_DATASET,
    INDEX_WEIGHT_INDEX_CODES,
    IndexMembershipError,
)
from openalpha_cn.domain.index_prices import (
    INDEX_DAILY_DATASET,
    INDEX_PRICE_INDEX_CODES,
    IndexPriceError,
)
from openalpha_cn.domain.industry_classification import (
    INDUSTRY_MEMBERSHIP_DATASET,
    INDUSTRY_MEMBERSHIP_TAXONOMY,
    INDUSTRY_TAXONOMY_EFFECTIVE_FROM,
    INDUSTRY_TREE_DATASET,
    IndustryClassificationError,
)
from openalpha_cn.domain.name_history import NAMECHANGE_DATASET
from openalpha_cn.domain.panel_batch import ColumnarPanelBatch, PanelBatchError
from openalpha_cn.domain.price_limits import (
    PRICE_LIMIT_DATASET,
    SUSPENSION_DATASET,
    SuspensionError,
)
from openalpha_cn.domain.risk_flag import UndeclaredRiskFlagError
from openalpha_cn.domain.run_mode import RunMode
from openalpha_cn.domain.stock_universe import STOCK_BASIC_DATASET, StockUniverseError
from openalpha_cn.domain.trading_calendar import (
    TRADING_CALENDAR_DATASET,
    TradingCalendar,
    TradingCalendarError,
)
from openalpha_cn.evidence.service import build_provider_evidence, parse_serialized_evidence
from openalpha_cn.factor_view import (
    ACCEPTANCE_STEP,
    FactorBuildReport,
    FactorViewError,
    acceptance_rows,
    attribution_rows,
    build_factor_panels,
    build_rows,
    build_view,
    catalog_rows,
    everything_is_unmeasured,
    experiment_view,
    factor_build_request,
    factor_catalog,
    factor_entry,
    factor_request,
    run_factor_experiment,
    tier_rows,
)
from openalpha_cn.feature_matrix import FeatureColumn
from openalpha_cn.job_contracts import (
    MAX_OWNER_LENGTH,
    CatchUpPolicy,
    ScheduledJob,
    job_not_registered,
    scheduled_job_view,
)
from openalpha_cn.logging_setup import configure_logging
from openalpha_cn.model_view import (
    ModelEvaluation,
    ModelViewError,
    daily_request,
    daily_rows,
    daily_view,
    declared_hyperparameters,
    evaluate_model,
    evaluation_invariances,
    evaluation_rows,
    evaluation_view,
    feature_columns,
    held_prediction,
    held_prediction_view,
    held_predictions,
    limitation_pointer,
    model_evaluation_request,
    prediction_index_rows,
    prediction_index_view,
    prediction_standing_legend,
    run_daily,
)
from openalpha_cn.panel.catalog import DEFAULT_DATE_TIMEZONE, PanelStorageError
from openalpha_cn.panel.store import PanelStore, PartitionRef
from openalpha_cn.panel_doctor import (
    PanelDoctorError,
    PanelHealthReport,
    panel_health_report,
)
from openalpha_cn.panel_gate import (
    DependencyClearance,
    DependencyRequest,
    PanelGateError,
    require_datasets,
)
from openalpha_cn.panel_ingest import (
    _sessions_published_through,
    load_industry_trees,
    load_stock_universe,
    load_suspensions,
    load_trading_calendar,
    merge_panel_batches,
    session_publication_instant,
    split_panel_batch_by_year,
    write_adjustment_factors,
    write_daily_panel,
    write_financial_statements,
    write_index_prices,
    write_index_weights,
    write_industry_memberships,
    write_industry_tree,
    write_name_history,
    write_price_limits,
    write_stock_universe,
    write_suspensions,
    write_trading_calendar,
)
from openalpha_cn.panel_view import (
    PanelRequestError,
    PanelUnreadableError,
    clearance_payload,
    health_report_payload,
    panel_request,
    panel_store,
    stored_calendar,
)
from openalpha_cn.providers.akshare import AKShareProvider
from openalpha_cn.providers.base import (
    DataProvider,
    PanelDataProvider,
    ProviderFailure,
    ProviderMetadata,
    ProviderRequest,
)
from openalpha_cn.providers.chainlin import ChainLinDataProvider
from openalpha_cn.providers.file import FileProvider
from openalpha_cn.providers.tushare import (
    CURRENT_INDUSTRY_MEMBERSHIP,
    SUPERSEDED_INDUSTRY_MEMBERSHIP,
    TRADING_CALENDAR_DEFAULT_EXCHANGE,
    TushareProvider,
    TushareTransport,
    UrllibTushareTransport,
)
from openalpha_cn.runtime.composition import build_storage
from openalpha_cn.runtime.contracts import ResearchRunRequest
from openalpha_cn.runtime.provenance import compute_config_digest, resolve_code_commit
from openalpha_cn.scheduler import ScheduleHorizonError, TradingDayScheduler
from openalpha_cn.sdk import OpenAlphaSDK
from openalpha_cn.shortlist_compare import (
    compare_held_shortlists,
    shortlist_comparison_rows,
)
from openalpha_cn.shortlist_view import (
    ShortlistEvidence,
    ShortlistRunResult,
    ShortlistViewError,
    held_shortlist,
    named_untradeable,
    run_shortlist,
    shortlist_evidence,
    shortlist_request,
    shortlist_rows,
    shortlist_view,
)
from openalpha_cn.storage.factor_experiments import ExperimentStoreError, FileExperimentStore
from openalpha_cn.storage.jobs import JobAlreadyRanError, SQLiteJobStore
from openalpha_cn.storage.migrations import (
    REPAIR_APPLIED,
    MigrationFailedError,
    UnmigratableHorizonError,
    read_status,
)
from openalpha_cn.storage.parquet import read_parquet_records
from openalpha_cn.storage.predictions import FilePredictionStore, PredictionStoreError
from openalpha_cn.storage.shortlists import FileShortlistStore, ShortlistStoreError
from openalpha_cn.storage.sqlite import SQLiteRunRepository

logger = logging.getLogger(__name__)

app = typer.Typer(
    name="openalpha",
    help="Evidence-traceable A-share research.",
    no_args_is_help=True,
)
evidence_app = typer.Typer(help="Build and inspect point-in-time evidence.")
app.add_typer(evidence_app, name="evidence")
research_app = typer.Typer(help="Run evidence-linked multi-agent research.")
app.add_typer(research_app, name="research")
replay_app = typer.Typer(help="Validate frozen point-in-time replay corpora.")
app.add_typer(replay_app, name="replay")
report_app = typer.Typer(help="Export stored evidence-linked research reports.")
app.add_typer(report_app, name="report")
"""`V2-P5-022`. A group with one command rather than a top-level `openalpha report-export`, for
`panel`'s reason one block down: `GET /api/v1/reports` and `POST /api/v1/reports` have had no
CLI at all since they shipped (`tests/unit/test_surface_parity.py` lists both as REST-only), so
the group this command joins is the one those two will land in, and creating it now is cheaper
than renaming a top-level verb later."""
migrate_app = typer.Typer(help="Inspect and apply state.sqlite3 schema migrations.")
app.add_typer(migrate_app, name="migrate")
panel_app = typer.Typer(help="Build and examine the point-in-time panel plane.")
app.add_typer(panel_app, name="panel")
"""`V2-P1-015`'s two panel commands hang under a sub-app rather than at the top level, and the
reason is that `doctor` is already taken. The existing top-level `doctor` probes *provider*
credentials and declared capabilities; this issue's is a *panel* health report, and they answer
different questions about different things. Shadowing the first with the second would silently
change what `openalpha doctor` means for every existing caller, and renaming either is a
breaking change to a published command. Namespacing removes the collision instead of resolving
it: `openalpha doctor` and `openalpha panel doctor` coexist, and each name says which one it is.

`data-check` stays at the top level, where the roadmap names it, because it is not scoped to the
panel plane the way the other two are -- it is the question a CI job or a scheduled research run
asks before it reads anything at all.
"""

factor_app = typer.Typer(
    help=(
        "List what this build declares, compute the three stored tiers, and run one sealed "
        "three-tier experiment. Start with `openalpha factor list`."
    )
)
app.add_typer(factor_app, name="factor")
"""`V2-P3-015`'s command is `openalpha factor run`, which is the spelling the roadmap names.

A sub-app for `panel`'s reason rather than for symmetry: `run` on its own is already the shape
`research run` and `replay run` take, so a top-level `run` would be a third meaning for a verb two
sub-apps already own. `factor run` says which plane it is about, and it left room for the builder
`V2-P3-015` recorded as missing -- `factor build` was "the name that caller will want", deliberately
kept free.

`V2-P3-019` took it, and took two more beside it, because a run alone was unreachable and
unreadable in a way that was measured rather than argued:

- **`factor build`** is the caller `V2-P3-015` left free. Before it, `compute_factor`,
  `apply_factor_transform` and `apply_factor_neutralization` had no operator-reachable caller in
  the entire repository, so `factor run` against a store built by `openalpha panel build` was
  refused by name and `openalpha panel build --dataset factor_obs_...` answered that the dataset is
  not one of its thirteen build targets. There was no third door.
- **`factor list`** and **`factor describe`** are what `--factor`, `--transform` and
  `--neutralization` had no other source for. Nineteen factors are declared and the only way to
  discover one was to mistype it -- which answered with nineteen content addresses, the one
  spelling of the identity a human never types.

The four commands are in the order an operator meets them: `list` (what can I ask for), `describe`
(what does this one actually measure), `build` (put it in the store), `run` (score it).
"""


shortlist_app = typer.Typer(
    help=(
        "Cut the stored panel down to the names worth spending an evidence run on, and refuse to "
        "publish the list when it does not clear its declared bars. Start with `openalpha "
        "shortlist run --help`."
    )
)
app.add_typer(shortlist_app, name="shortlist")
"""`V2-P4-033`'s command is `openalpha shortlist run`, and it is a sub-app for `factor`'s reason.

`run` on its own is already the shape `research run`, `replay run` and `factor run` take, so a
top-level `run` would be a fourth meaning for a verb three sub-apps own. `shortlist run` says which
plane it is about.

It is `shortlist` rather than `screen`, and the distinction is load-bearing rather than
stylistic: `POST /api/v1/screen` already exists and ranks *verified research results* by explicit
criteria -- the evidence plane's own answers, after the fact. This is the other direction, and
PRD §3.2 draws it as two planes: the whole market is scored and filtered **without** `run_cycle`,
and the shortlist is what earns an evidence run. Naming both `screen` would have made "rank what
I have researched" and "decide what to research" one word.
"""


portfolio_app = typer.Typer(
    help=(
        "Turn one admitted shortlist into target weights under a declared, heuristic policy. "
        "Start with `openalpha portfolio construct --help`."
    )
)
app.add_typer(portfolio_app, name="portfolio")
"""`V2-P5-001`'s command is `openalpha portfolio construct`, and it is a sub-app for `factor`'s
and `shortlist`'s reason: `construct` alone would be a fifth top-level verb, and the plane the
verb acts on is the half a reader needs.

Deliberately not a flag on `shortlist run`. A shortlist is a list of names that earned an evidence
run, and a construction is a set of weights over one; folding them into one command would mean one
exit code covering "the gate refused the list" and "the caps could not place the capital", which
are different facts with different remedies. Keeping them apart is also what makes the refusal
below possible at all: a construction that takes a *held* answer can see that the gate said no.
"""


jobs_app = typer.Typer(
    help=(
        "Declare a trading-day schedule, ask what it owes, and run the sessions it owes. "
        "Start with `openalpha jobs run --help`."
    )
)
app.add_typer(jobs_app, name="jobs")
"""`V2-P5-013`'s commands, and the caller `V2-P5-010` said in its own row it was not writing.

That row shipped `job_contracts.py`, `storage/jobs.py` and `scheduler.py` and recorded the other
half as open by name: *"三个模块没有 CLI 命令、没有 REST 路由、不在 `build_storage` 里"*. Audit
`F98` carries the same sentence. Every guarantee those modules give was tested at its own
boundary against real SQLite; *an operator can run due jobs* was tested nowhere, because there
was no operator.

A sub-app for `factor`'s and `shortlist`'s reason, and the four commands are in the order an
operator meets them: `register` (declare the schedule), `list` (what is declared), `due` (what
does this one owe right now), `run` (do it).

## What this scheduler owns, and what it does not

It owns **when**: which trading sessions a job owes, at most one run per session, one process at
a time, and what to do about sessions that were missed. It does not own a general vocabulary of
work, and `openalpha jobs run` ships exactly one job body -- a point-in-time panel health report
at each owed session's own publication instant. That is a measurement rather than an ambition:
every other per-session action this build has takes between eight and twenty declared parameters
(`model daily-run` takes seventeen), and `scheduled_jobs` has no column that could hold them.
Storing a payload would be a change to a stored contract, which AGENTS.md rule 3 confines to the
closed `V2-P4-001` window. So the parameters are typed on the command line, where a crontab line
already carries them, and the row stays a schedule rather than becoming a task queue.

It is also the one per-session action that reaches **no network**: a scheduled job that hit a
paid provider on a timer is not something to ship by accident.
"""


model_app = typer.Typer(
    help=(
        "Evaluate one model declaration over a walk-forward schedule, and register today's "
        "prediction before its outcome is known. Start with `openalpha model evaluate --help`."
    )
)
app.add_typer(model_app, name="model")
"""`V2-P4-021`'s commands are `openalpha model evaluate` and `openalpha model daily-run`.

A sub-app for `factor`'s and `shortlist`'s reason: `run` on its own is already the shape four
sub-apps take, and `evaluate` on its own would say nothing about which plane it is on -- this
repository evaluates factors, screens and models, and two of the three already have a home.

**`daily-run` keeps the roadmap's own spelling rather than being renamed to `run`.** PRD S84 lists
the five commands a personal deployment is driven by as *"doctor / data-check / factor-run /
model-evaluate / daily-run"*, and `RunMode.daily`'s docstring names `daily-run` by that name as
the cycle it exists for. A hyphenated verb inside a sub-app is unusual here and the alternative
was worse: `openalpha model run` would be a fifth meaning for `run` and would say "run the model"
where the thing that happens is "register today's prediction".

The two commands are in the order an operator meets them: `evaluate` (would this declaration have
ordered the market), then `daily-run` (register what it says about today). `predictions` and
`prediction` are the two reads beside them, `shortlist list`/`shortlist get`'s pair.
"""


validation_app = typer.Typer(
    help=(
        "Aggregate stored outcome validations: gross beside net, cost drag in its own column, "
        "intervals, sample counts and BH-controlled q-values. Start with "
        "`openalpha validation statistics --help`."
    )
)
app.add_typer(validation_app, name="validation")
"""`V2-P5-007`/`V2-P5-008`'s command is `openalpha validation statistics`.

A sub-app for `factor`'s, `shortlist`'s, `portfolio`'s and `model`'s reason: `statistics` alone
would say nothing about which plane it is on, and this repository already computes statistics
over factors, screens and models. The plane here is the *outcome* plane -- results
`POST /api/v1/backtests/validate` and `OpenAlphaSDK.validate_outcome` have already written.

Deliberately not a flag on anything that produces one validation. A single result has no sample
size, no interval and no family, so a `--statistics` switch would have to answer a question its
own input cannot pose; the aggregate is a different verb over a different number of rows.
"""


class Redistribution(StrEnum):
    """Allowed source-data redistribution states."""

    allowed = "allowed"
    restricted = "restricted"
    unknown = "unknown"


@app.command()
def version() -> None:
    """Print the installed OpenAlpha CN version."""
    typer.echo(f"OpenAlpha CN {__version__}")


def _default_providers() -> list[DataProvider]:
    """Return the built-in providers `doctor` reports on.

    Construction never requires a credential or touches the network: each
    provider only reads its token lazily, inside `fetch()`. ChainLin's base
    URL is sourced from `CHAINLIN_API_BASE_URL`; when it is absent or empty
    the provider is still returned, with `is_configured=False`, instead of
    ever being pointed at an invented placeholder domain.
    """
    chainlin_base_url = os.environ.get("CHAINLIN_API_BASE_URL", "").strip() or None
    return [
        TushareProvider(),
        AKShareProvider(),
        ChainLinDataProvider(
            base_url=chainlin_base_url,
            api_key_env="CHAINLIN_API_KEY",
            source_license="user-held ChainLin subscription",
        ),
    ]


def _credential_report(provider: DataProvider) -> list[dict[str, str]]:
    """Report presence, never the value, of each declared credential env var."""
    return [
        {
            "env_var": env_var,
            "status": "present" if os.environ.get(env_var, "").strip() else "missing",
        }
        for env_var in provider.metadata.credential_env_vars
    ]


def _capability_report(provider: DataProvider) -> dict[str, object]:
    """Report the provider's declared licensing, rate-limit, and dataset coverage."""
    metadata = provider.metadata
    return {
        "provider_id": metadata.provider_id,
        "redistribution": metadata.redistribution,
        "rate_limit": metadata.rate_limit,
        "supported_datasets": list(metadata.supported_datasets),
    }


def _is_configured(provider: DataProvider) -> bool:
    """Return whether `provider` declares itself ready to be probed.

    Providers that need no external configuration (the common case) are
    always configured; a provider may opt out by exposing `is_configured`.
    """
    return bool(getattr(provider, "is_configured", True))


def _probe_subjects(provider: DataProvider, dataset: str) -> tuple[str, ...]:
    """Return the minimal subjects `dataset`'s `fetch()` contract requires for a probe.

    Most datasets accept an empty subject tuple, so this defaults to `()`. A provider
    whose contract requires at least one subject for a given dataset (AKShare's
    `stock_zh_a_hist`, for example) opts in by implementing an optional
    ``probe_subjects(dataset: str) -> tuple[str, ...]`` method -- the same
    `getattr`-based extension pattern `_is_configured` already uses. Because the hook
    lives on the provider, not here, adding datasets in P1 never requires touching
    this function: each provider stays responsible for the minimal input its own
    `fetch()` needs.
    """
    hook = getattr(provider, "probe_subjects", None)
    if hook is None:
        return ()
    subjects = hook(dataset)
    return tuple(str(subject) for subject in subjects)


def _probe_plane(provider: DataProvider, dataset: str) -> str:
    """Which of a provider's fetch methods one minimal request for `dataset` should go to.

    `_probe_subjects`' sibling, the same `getattr` seam, and it exists for the defect that
    seam was one half of. `_probe_report` called `fetch()` for every declared dataset, and
    four of Tushare's fifteen declare `serves_evidence_plane=False` -- a verbatim evidence
    record has no single `available_time` for them -- so `fetch()` refused all four with
    `configuration` **before any transport call**, in the same microsecond, in every
    environment. The probe was reporting "this account cannot fetch `stock_basic`" about an
    endpoint that returns 6,217 rows on the plane it is actually served on.

    A provider opts in by exposing `probe_plane(dataset) -> "evidence" | "panel"`; anything
    else, and any unrecognised answer, stays on the evidence plane, which is the only method
    `DataProvider` guarantees.
    """
    hook = getattr(provider, "probe_plane", None)
    if hook is None:
        return "evidence"
    return "panel" if hook(dataset) == "panel" else "evidence"


def _probe_once(provider: DataProvider, request: ProviderRequest) -> None:
    """Send one minimal request on whichever plane `_probe_plane` named."""
    if _probe_plane(provider, request.dataset) == "panel":
        panel_provider = cast(PanelDataProvider, provider)
        panel_provider.fetch_panel(request)
        return
    provider.fetch(request)


PROBE_FAILURE_STATES: Final[frozenset[str]] = frozenset({"authentication"})
"""The probe outcomes that make `doctor --probe` exit non-zero, as a closed set.

**One member**, and the reasoning behind that number is the whole of this decision.

`authentication` is a credential this provider *had* and the endpoint *rejected*. It is the
one probe outcome that is both about this side of the connection and unambiguous, and until
`V2-P1-018` it could not be produced at all: `providers/tushare.py::TUSHARE_CREDENTIAL_CODE`
records the measurement -- a wrong token answers `code=40101`, and the only code mapped to
`authentication` was `-2001`, which nothing here has ever observed. So the single most common
real failure was reported as `upstream`, advertised as retryable, and exited 0.

Four outcomes are deliberately *not* here, and three of them are the ones a naive "anything
that is not ok" rule would have swept in:

- `configuration`. R12's brief draws this line itself: "this dataset needs a parameter" must
  not count as a failure. After `_probe_subjects` and `_probe_plane` that reading is no longer
  reachable through this path, but the other one is -- an **absent** `TUSHARE_TOKEN` is
  `configuration` too, and a default install with no Tushare account is a normal state, already
  reported as `WARN credential ... missing` by the check next to this one. Failing the command
  for it would make `--probe` non-zero on almost every machine, which is how a check becomes a
  `|| true`.
- `rate_limit` is a fact about the next sixty seconds, and the provider already waits and
  retries within one (`providers/tushare.py::TUSHARE_RATE_LIMIT_DELAY`).
- `upstream` is the endpoint's own verdict about one interface. That is the **content** of the
  report Implementation Decision 33 asks for -- "which interfaces can this account actually
  reach" -- not a reason to refuse to publish it.
- `not_configured` is a provider that declined to be probed at all (ChainLin without a base
  URL), which is again the default install.

`probe_error` sits closest to the line and stays out by the same argument as `upstream`: it is
per-dataset, and the *report* is what carries it.
"""


def _probe_report(provider: DataProvider) -> dict[str, str]:
    """Make one minimal request per declared dataset and classify the outcome.

    `ProviderFailure` reports its declared, closed-`Literal` category
    verbatim. Anything else -- a bug, or a future or third-party provider
    that has not adopted the `ProviderFailure` contract -- is recorded as
    the doctor-level state `probe_error` without ever echoing the
    exception's message or repr, so this boundary can never leak a
    credential embedded in an unexpected error.

    "One minimal request" is now a fact rather than a description: the subjects come from
    `_probe_subjects` and the plane from `_probe_plane`, both of which the provider answers, so
    every declared dataset reaches the network. Before those two hooks, nine of Tushare's
    fifteen never did -- see `PROBE_FAILURE_STATES` and `_probe_plane` for the two causes and
    the measurement that separated them.
    """
    if not _is_configured(provider):
        return dict.fromkeys(provider.metadata.supported_datasets, "not_configured")
    as_of = datetime.now(UTC)
    results: dict[str, str] = {}
    for dataset in provider.metadata.supported_datasets:
        try:
            request = ProviderRequest(
                dataset=dataset,
                as_of=as_of,
                subjects=_probe_subjects(provider, dataset),
            )
            _probe_once(provider, request)
        except ProviderFailure as failure:
            results[dataset] = failure.category
            # Never `failure.args`/`str(failure)`: `ProviderFailure.message` (see
            # `providers/base.py`) can carry a credential or URL query string --
            # only the closed-`Literal` category, provider_id, and dataset name are
            # safe to log. This is the "Provider request failure path" call site
            # V2-P0B-007's brief names explicitly.
            logger.warning(
                "provider_probe_failed",
                extra={
                    "provider_id": failure.provider_id,
                    "category": failure.category,
                    "dataset": dataset,
                },
            )
        except Exception:
            results[dataset] = "probe_error"
        else:
            results[dataset] = "ok"
    return results


@app.command()
def doctor(
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Emit a machine-readable health report."),
    ] = False,
    probe: Annotated[
        bool,
        typer.Option(
            "--probe",
            help=(
                "Make one live request per provider dataset. Off by default; "
                "never required in CI and never needed for the credential or "
                "capability checks."
            ),
        ),
    ] = False,
) -> None:
    """Check runtime requirements, provider credentials, and declared capabilities.

    Also validates `OPENALPHA_*` config (Finding 2 fix): `main()` only ever resolves
    `OPENALPHA_LOG_LEVEL` before dispatch (see `load_log_level()`), specifically so an
    unrelated invalid `OPENALPHA_*` field never aborts dispatch to `doctor` -- the one
    command whose entire job is diagnosing exactly that kind of broken environment.
    So `doctor` calls `load_config()` itself, here, and reports a `ConfigError` as an
    ordinary `"config"` finding (both in `--json` and human output) instead of letting
    it propagate and kill the process before anything can be reported.
    """
    timezone_ok = datetime.now().astimezone().utcoffset() is not None
    python_ok = sys.version_info >= (3, 11)
    try:
        load_config()
        config_error: str | None = None
    except ConfigError as error:
        config_error = str(error)
    config_ok = config_error is None
    checks: dict[str, dict[str, object]] = {
        "python": {
            "ok": python_ok,
            "version": platform.python_version(),
            "minimum": "3.11",
        },
        "timezone": {
            "ok": timezone_ok,
            "name": str(datetime.now().astimezone().tzinfo),
        },
        "config": {
            "ok": config_ok,
            **({"error": config_error} if config_error is not None else {}),
        },
    }

    providers: dict[str, dict[str, object]] = {}
    warnings: list[str] = []
    probe_failures: list[str] = []
    for provider in _default_providers():
        provider_id = provider.metadata.provider_id
        credentials = _credential_report(provider)
        warnings.extend(
            f"{provider_id}: credential {credential['env_var']} is missing"
            for credential in credentials
            if credential["status"] == "missing"
        )
        report: dict[str, object] = {
            "capabilities": _capability_report(provider),
            "credentials": credentials,
        }
        if probe:
            outcomes = _probe_report(provider)
            report["probe"] = outcomes
            probe_failures.extend(
                f"{provider_id}/{dataset}: {result}"
                for dataset, result in outcomes.items()
                if result in PROBE_FAILURE_STATES
            )
        providers[provider_id] = report

    environment_ok = python_ok and timezone_ok and config_ok
    payload = {
        "status": "ok" if environment_ok and not probe_failures else "error",
        "checks": checks,
        "providers": providers,
        "warnings": warnings,
        # Always present with `--probe`, and empty is the interesting value: a caller reading
        # this to decide whether a scheduled build can run needs "the probe ran and found
        # nothing" to be distinguishable from "the probe did not run", which an absent key is
        # not. Without `--probe` the key is absent, because no probe happened.
        **({"probe_failures": probe_failures} if probe else {}),
    }
    if json_output:
        typer.echo(json.dumps(payload, ensure_ascii=False, sort_keys=True))
        # Falls through to the exit check rather than returning, and that is a fix rather than
        # a tidy-up. `doctor --json` used to `return` here, so **the one rendering a CI job
        # parses always exited 0** -- a malformed `OPENALPHA_*` value, a rejected credential, a
        # probe that could not reach an endpoint, all reported faithfully in the payload and all
        # indistinguishable from a clean run to `set -e`. `panel doctor` and `data-check` both
        # print their JSON and then raise; this is the same rule, and `PanelExit`'s docstring
        # already says why an exit code that cannot say "no" is not a check.
        raise typer.Exit(code=0 if payload["status"] == "ok" else 1)

    for name, check in checks.items():
        line = f"{'PASS' if check['ok'] else 'FAIL'} {name}"
        if not check["ok"] and "error" in check:
            line += f": {check['error']}"
        typer.echo(line)
    for provider_id, report in providers.items():
        capabilities = report["capabilities"]
        assert isinstance(capabilities, dict)
        datasets = ",".join(capabilities["supported_datasets"])
        typer.echo(f"INFO capability {provider_id} datasets={datasets}")
        credential_reports = report["credentials"]
        assert isinstance(credential_reports, list)
        for credential in credential_reports:
            if credential["status"] == "present":
                typer.echo(f"PASS credential {provider_id} {credential['env_var']}")
            else:
                typer.echo(f"WARN credential {provider_id} {credential['env_var']} missing")
        if probe:
            probe_results = report["probe"]
            assert isinstance(probe_results, dict)
            for dataset, result in probe_results.items():
                state = "FAIL" if result in PROBE_FAILURE_STATES else "PROBE"
                typer.echo(f"{state} {provider_id}/{dataset} {result}")
    if payload["status"] != "ok":
        raise typer.Exit(code=1)


_RUNTIME_DIR_HELP: Final[str] = (
    "One installation's whole state. The panel plane is `<runtime-dir>/panel` and sealed "
    "experiments are `<runtime-dir>/experiments`, the same directory `openalpha panel build` "
    "writes and the HTTP service reads."
)


@evidence_app.command("build")
def evidence_build(
    path: Annotated[Path, typer.Argument(exists=True, dir_okay=False, readable=True)],
    as_of: Annotated[str, typer.Option("--as-of", help="ISO-8601 point-in-time clock.")],
    source_id: Annotated[str, typer.Option("--source-id")],
    source_license: Annotated[str, typer.Option("--source-license")],
    redistribution: Annotated[
        Redistribution,
        typer.Option("--redistribution"),
    ] = Redistribution.restricted,
    runtime_dir: Annotated[
        Path | None, typer.Option("--runtime-dir", help=_RUNTIME_DIR_HELP)
    ] = None,
) -> None:
    """Build evidence from a user-owned CSV, JSON, JSONL, or Parquet file, and store it.

    **`--runtime-dir` and the append arrived with `V2-P5-013`, closing audit `F31`.** This
    command used to print its snapshots and throw them away, while
    `OpenAlphaSDK.build_file_evidence` and `POST /api/v1/evidence/build` both appended to the
    evidence store -- two faces of three agreeing and the command line the odd one out. A caller
    who built evidence from the terminal and then queried it found nothing, and could not tell
    "the file produced no events" from "the build discarded them".

    The default is `./runtime`, the same directory every other command in this CLI means by
    `--runtime-dir`, so a build and a subsequent `openalpha research run` see one store.

    The printed payload is unchanged and is still the whole response, so a caller piping this
    into `jq` keeps working; what changed is that the snapshots also survive the process.
    """
    runtime_dir = _resolved_runtime_dir(runtime_dir)

    point_in_time = datetime.fromisoformat(as_of.replace("Z", "+00:00"))
    metadata = ProviderMetadata(
        provider_id=source_id,
        display_name=source_id,
        source_license=source_license,
        redistribution=redistribution.value,
        credential_env_vars=(),
        caching_policy="local-permitted",
        rate_limit="not-applicable",
        freshness="defined-by-input-file",
        failure_semantics="Malformed or unreadable inputs raise ProviderFailure.",
    )
    provider = FileProvider(path=path, metadata=metadata, parquet_reader=read_parquet_records)
    response = build_provider_evidence(provider=provider, dataset="events", as_of=point_in_time)
    if response.items:
        # Through the composition root rather than `ParquetEvidenceStore(runtime_dir /
        # "evidence")`, v2 hard rule 5: `sdk.py` and `api/app.py` once assembled this store by
        # hand at the same path and drifted, and a third hand-assembly is that mistake again.
        build_storage(runtime_dir=runtime_dir, clock=_panel_clock).evidence_store.append(
            response.items
        )
    typer.echo(response.model_dump_json())


_CODE_COMMIT_HELP = (
    "Defaults to the real git commit this process is running from (a literal "
    "'-dirty' suffix when the workspace has uncommitted changes), or an explicit "
    "unknown marker outside a git workspace -- never a placeholder that merely "
    "looks like a real commit. See runtime/provenance.py#resolve_code_commit."
)
_CONFIG_DIGEST_HELP = (
    "Defaults to a SHA-256 of the resolved, non-secret OpenAlphaConfig -- never all "
    "zeros. See runtime/provenance.py#compute_config_digest."
)


def _resolved_code_commit(explicit: str | None) -> str:
    """Return `explicit` verbatim when given; otherwise resolve a real commit.

    Never touches git when the caller already supplied a value -- resolution only
    runs when `--code-commit` was genuinely omitted.
    """
    return explicit if explicit is not None else resolve_code_commit()


def _resolved_config_digest(explicit: str | None) -> str:
    """Return `explicit` verbatim when given; otherwise digest the effective config.

    Mirrors `serve`'s `ConfigError` handling (see its docstring): `load_config()` is
    only called here, lazily, when `--config-digest` was omitted and this command
    genuinely needs the resolved config -- an unrelated invalid `OPENALPHA_*` field
    never blocks a caller that passed `--config-digest` explicitly.
    """
    if explicit is not None:
        return explicit
    try:
        config = load_config()
    except ConfigError as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(code=1) from error
    return compute_config_digest(config)


def _resolved_runtime_dir(explicit: Path | None) -> Path:
    """Return `explicit` verbatim when given; otherwise ask `load_config()`.

    Every `--runtime-dir` option in this module funnels through here, and every one of
    them declares `None` -- never a path -- as its Typer default. That is the whole
    point: a path default makes "the caller omitted this" indistinguishable from "the
    caller asked for ./runtime", and while they look alike they are not, because
    `OPENALPHA_RUNTIME_DIR` is only allowed to decide the first.

    **`V2-P5-028`.** Twenty-eight commands previously wrote `= Path("./runtime")` as the
    option default and never consulted `load_config()` at all, so an exported
    `OPENALPHA_RUNTIME_DIR` lost to a compiled-in default -- the exact inversion
    `config.py`'s module docstring rules out ("an already-exported real environment
    variable always wins over ... a field's compiled-in default"). The production shape
    of it: `Dockerfile` sets `ENV OPENALPHA_RUNTIME_DIR=/data` next to `WORKDIR /data`
    and `VOLUME ["/data"]`, so `uvicorn openalpha_cn.api.app:app` serves
    `/data/state.sqlite3` while `docker exec ... openalpha migrate status` resolved
    `./runtime` against the working directory and reported on `/data/runtime/…`. That
    file does not exist, so the operator was told "schema version 0, 8 pending" about a
    database nobody serves, a decoy was created on the mounted volume as a side effect,
    and `migrate run` would have gone on to migrate the decoy. Measured before the fix,
    `openalpha jobs list` -- a command that only reads -- created *and migrated* one.

    Lazy, exactly as `_resolved_config_digest` above is lazy and for the same reason
    (P0.B Finding 2): `load_config()` validates every `OPENALPHA_*` field atomically, so
    it is called only when this command genuinely has no other way to know where state
    lives. A caller who passes `--runtime-dir` never touches config validation, and an
    unrelated invalid field -- a non-numeric `OPENALPHA_MAX_REQUEST_BYTES`, say -- can
    therefore never block them. A caller who omits it does get that validation, and a
    named `ConfigError` on stderr with exit 1 rather than a silent wrong directory,
    which is the right trade when the alternative is operating on the wrong database.
    """
    if explicit is not None:
        return explicit
    try:
        return load_config().runtime_dir
    except ConfigError as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(code=1) from error


@research_app.command("run")
def research_run(
    evidence_path: Annotated[Path, typer.Argument(exists=True, dir_okay=False, readable=True)],
    runtime_dir: Annotated[Path | None, typer.Option("--runtime-dir")] = None,
    run_id: Annotated[str, typer.Option("--run-id")] = "local-run",
    mode: Annotated[RunMode, typer.Option("--mode")] = RunMode.live,
    subject: Annotated[str, typer.Option("--subject")] = "",
    as_of: Annotated[str, typer.Option("--as-of")] = "",
    code_commit: Annotated[
        str | None, typer.Option("--code-commit", help=_CODE_COMMIT_HELP)
    ] = None,
    config_digest: Annotated[
        str | None, typer.Option("--config-digest", help=_CONFIG_DIGEST_HELP)
    ] = None,
    random_seed: Annotated[int, typer.Option("--random-seed")] = 7,
) -> None:
    """Run multi-agent research from serialized EvidenceSnapshot items.

    An evidence payload naming a `quality_flags` string the build never declared exits `1` with
    the flag and the vocabulary on **stderr** (`V2-P4-102`). It used to exit `1` with a rich,
    boxed Python traceback of `openalpha_cn` frames, which is the presentation `create_app`'s own
    docstring rules out for this repository -- "naming the specific variable, never a bare
    traceback". The message itself was already the right message; only its delivery was a stack
    trace, so what changed here is the delivery and not the code: a CI job already branching on
    `1` keeps working, and `openalpha replay run` has always reported the same fault as a
    `failures` row inside a report because `ReplayRunner` catches it per case.

    The catch is `UndeclaredRiskFlagError` and nothing wider. A bare `except ValueError` around
    this body would also swallow `parse_serialized_evidence`'s own refusals -- a mismatched
    `content_hash`, a non-object item -- and print the risk-flag vocabulary at somebody whose
    problem is a tampered digest.
    """
    runtime_dir = _resolved_runtime_dir(runtime_dir)

    raw = json.loads(evidence_path.read_text(encoding="utf-8"))
    raw_items = raw.get("items") if isinstance(raw, dict) else raw
    evidence = parse_serialized_evidence(raw_items)
    point_in_time = datetime.fromisoformat(as_of.replace("Z", "+00:00"))
    sdk = OpenAlphaSDK(runtime_dir=runtime_dir)
    try:
        result = sdk.run_research(
            ResearchRunRequest(
                run_id=run_id,
                mode=mode,
                subject=subject,
                as_of=point_in_time,
                evidence=evidence,
                code_commit=_resolved_code_commit(code_commit),
                config_digest=_resolved_config_digest(config_digest),
                random_seed=random_seed,
            )
        )
    except UndeclaredRiskFlagError as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(code=1) from error
    typer.echo(result.model_dump_json())


@replay_app.command("run")
def replay_run(
    corpus_path: Annotated[Path, typer.Argument(exists=True, dir_okay=False, readable=True)],
    runtime_dir: Annotated[Path | None, typer.Option("--runtime-dir")] = None,
    code_commit: Annotated[
        str | None, typer.Option("--code-commit", help=_CODE_COMMIT_HELP)
    ] = None,
    config_digest: Annotated[
        str | None, typer.Option("--config-digest", help=_CONFIG_DIGEST_HELP)
    ] = None,
    random_seed: Annotated[int, typer.Option("--random-seed")] = 7,
) -> None:
    """Run and validate a frozen replay corpus."""
    runtime_dir = _resolved_runtime_dir(runtime_dir)

    report = OpenAlphaSDK(runtime_dir=runtime_dir).replay(
        corpus=ReplayCorpus.load(corpus_path),
        code_commit=_resolved_code_commit(code_commit),
        config_digest=_resolved_config_digest(config_digest),
        random_seed=random_seed,
    )
    typer.echo(report.model_dump_json())


@report_app.command("export")
def report_export_command(
    report_id: Annotated[str, typer.Argument(help="The report's content-derived id, as `rpt_…`.")],
    runtime_dir: Annotated[
        Path | None, typer.Option("--runtime-dir", help=_RUNTIME_DIR_HELP)
    ] = None,
) -> None:
    """Print one stored report with its evidence, minus every payload its licence withholds.

    `V2-P5-022`. PRD Implementation Decision 27 asks for exactly one thing here -- 不导出
    Tushare 原始 payload -- and this is the command that makes it reachable from a terminal:

        openalpha report export rpt_0123456789abcdef --runtime-dir ./runtime > report.json

    What comes out is safe to hand to somebody else. Every evidence item the report cites is
    listed with its identity, its four clocks, its source, its licence and this repository's own
    one-line summary of it; the provider's own bytes travel **only** where
    `redistribution == "allowed"`, and everywhere else their place is held by a record naming
    the licence that kept them out. All three shipped providers declare `restricted`, so on a
    real runtime directory the usual answer is that no payload travels and the export says so
    per item rather than looking empty. The rule itself lives in `product/export.py`.

    Always JSON, `openalpha shortlist get`'s rule: this is a document being handed over, not a
    verdict this command is reaching, and a second terminal rendering of it would be a second
    shape for bytes whose whole purpose is to be one shape.

    Exits 0 when the report is held and 1 when no report has that id -- distinguished from an
    export with nothing in it, which is a report that cites evidence this store can no longer
    produce and which prints those citations under `evidence_not_recovered`.
    """
    runtime_dir = _resolved_runtime_dir(runtime_dir)

    export = OpenAlphaSDK(runtime_dir=runtime_dir).export_report(report_id)
    if export is None:
        typer.echo(f"No report is stored under {report_id}.", err=True)
        raise typer.Exit(code=1)
    typer.echo(export.model_dump_json())


@migrate_app.command("status")
def migrate_status(
    runtime_dir: Annotated[Path | None, typer.Option("--runtime-dir")] = None,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Emit a machine-readable status report."),
    ] = False,
) -> None:
    """Show the current schema version and applied/pending migrations.

    Also reports the two things `V2-P5-026` made visible, both empty on a healthy database:
    `repaired` lines for versions the counter had skipped and reconciliation has since resolved
    (`applied` = its effect was missing and was created, `verified` = its effect was already
    there), and `unrecorded` lines for a version `PRAGMA user_version` claims is behind us that
    `schema_migrations` has never named and that no schema inspection can settle. An
    `unrecorded` line is the only shape that needs a person: it means a data-rewrite migration
    was skipped, and neither re-running it nor recording it would be honest.
    """
    runtime_dir = _resolved_runtime_dir(runtime_dir)

    status = read_status(runtime_dir / "state.sqlite3")
    if json_output:
        payload = {
            "path": str(status.path),
            "current_version": status.current_version,
            "applied": [
                {"version": item.version, "name": item.name, "applied_at": item.applied_at}
                for item in status.applied
            ],
            "pending": [{"version": item.version, "name": item.name} for item in status.pending],
            "repaired": [
                {
                    "version": item.version,
                    "name": item.name,
                    "resolution": item.resolution,
                    "repaired_at": item.repaired_at,
                }
                for item in status.repairs
            ],
            "unrecorded": [
                {"version": item.version, "name": item.name} for item in status.unrecorded
            ],
        }
        typer.echo(json.dumps(payload, ensure_ascii=False, sort_keys=True))
        return
    typer.echo(f"schema version: {status.current_version}")
    for applied_item in status.applied:
        typer.echo(
            f"applied  {applied_item.version} {applied_item.name} at {applied_item.applied_at}"
        )
    for repaired_item in status.repairs:
        typer.echo(
            f"repaired {repaired_item.version} {repaired_item.name} "
            f"({repaired_item.resolution}) at {repaired_item.repaired_at}"
        )
    for pending_item in status.pending:
        typer.echo(f"pending  {pending_item.version} {pending_item.name}")
    for unrecorded_item in status.unrecorded:
        typer.echo(
            f"unrecorded {unrecorded_item.version} {unrecorded_item.name} "
            "(schema version is past it but nothing recorded it, and its effect cannot be "
            "established by inspecting the schema)"
        )


@migrate_app.command("run")
def migrate_run(
    runtime_dir: Annotated[Path | None, typer.Option("--runtime-dir")] = None,
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Show what would be applied without applying it."),
    ] = False,
) -> None:
    """Apply pending schema migrations, then construct every store once.

    Goes through `build_storage()` -- the same composition root `sdk.py`/`api/app.py`
    use -- rather than calling `run_migrations()` directly. A raw `run_migrations()` call
    never constructs a store, so on a fresh `runtime_dir` a migration deferred only
    because its table doesn't exist yet (e.g. the demo migration) would stay pending
    forever, no matter how many times this command runs: nothing would ever create that
    table. Routing through `build_storage()` constructs the stores as a side effect,
    which creates the table, so the *next* invocation of this command (or the next real
    SDK/API startup against the same directory) can actually apply it.
    """
    runtime_dir = _resolved_runtime_dir(runtime_dir)

    path = runtime_dir / "state.sqlite3"
    if dry_run:
        status = read_status(path)
        if not status.pending:
            typer.echo(f"schema version {status.current_version} is up to date; nothing to do")
            return
        typer.echo(
            f"would apply {len(status.pending)} migration(s) from version {status.current_version}:"
        )
        for pending_item in status.pending:
            typer.echo(f"  {pending_item.version} {pending_item.name}")
        return
    try:
        storage = build_storage(runtime_dir=runtime_dir, clock=lambda: datetime.now(UTC))
    except MigrationFailedError as error:
        typer.echo(
            f"migration {error.version} ({error.name}) failed and was rolled back; "
            f"schema version unchanged; backup at {error.backup_path}",
            err=True,
        )
        # One cause is reported verbatim and the rest are not, which is the same line
        # `logging_setup.py` draws: a migration's `apply()` is arbitrary code and its message
        # is unvetted, but `UnmigratableHorizonError` is a type this package owns and its
        # message *is* the remedy -- which run carries a horizon this build cannot accept, and
        # what to do about it. Without this the operator gets "migration 5 failed" and nothing
        # actionable, for a refusal that is deliberate rather than a fault.
        cause = error.__cause__
        if isinstance(cause, UnmigratableHorizonError):
            typer.echo(str(cause), err=True)
        raise typer.Exit(code=1) from error
    result = storage.migration_result
    status = read_status(path)
    for repair in result.repairs:
        # Printed before the migrated/pending lines because it is what made them possible: on
        # a database whose version numbers were reassigned by a registry reordering, the
        # skipped migration's effect is the precondition everything above it was waiting on.
        outcome = (
            "its effect was missing and has been created"
            if repair.resolution == REPAIR_APPLIED
            else "its effect was already present, so nothing was re-run"
        )
        typer.echo(
            f"repaired {repair.version} {repair.name}: schema version was already past it and "
            f"nothing had recorded it; {outcome}"
        )
    if not result.applied and not result.repairs and not status.pending:
        typer.echo(f"schema version {result.to_version} is up to date; nothing to do")
        return
    if result.applied:
        typer.echo(f"migrated {result.from_version} -> {result.to_version}")
        for applied_item in result.applied:
            typer.echo(f"  applied {applied_item.version} {applied_item.name}")
        if result.backup_path is not None:
            typer.echo(f"backup: {result.backup_path}")
    if status.pending:
        # Genuinely stuck, not "up to date": at least one migration's precondition
        # (typically a table owned by a store not yet constructed against this
        # `runtime_dir`) still isn't met. Say so instead of claiming completion.
        typer.echo(
            f"{len(status.pending)} migration(s) still pending at schema version "
            f"{status.current_version} (deferred until their preconditions are met, "
            "e.g. by normal application startup constructing the owning store):"
        )
        for pending_item in status.pending:
            typer.echo(f"  {pending_item.version} {pending_item.name}")
    if status.unrecorded:
        # The one shape reconciliation deliberately refuses to resolve on its own, surfaced
        # here rather than left in a log line nobody reads. Both available guesses are wrong:
        # re-running a data rewrite that may already have run can corrupt records, and
        # recording it unchecked fabricates the very history this engine exists to keep.
        typer.echo(
            f"{len(status.unrecorded)} migration(s) are below schema version "
            f"{status.current_version} but were never recorded, and their effect cannot be "
            "established by inspecting the schema (they rewrite data, not shape). Restore the "
            "pre-migration backup under `runtime/backups/` if these records matter, or accept "
            "the gap knowingly:"
        )
        for unrecorded_item in status.unrecorded:
            typer.echo(f"  {unrecorded_item.version} {unrecorded_item.name}")


@migrate_app.command("prune-backups")
def migrate_prune_backups(
    runtime_dir: Annotated[Path | None, typer.Option("--runtime-dir")] = None,
    keep: Annotated[
        int,
        typer.Option("--keep", min=0, help="How many of the newest backups to keep."),
    ] = 10,
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="List what would be removed without removing it."),
    ] = False,
) -> None:
    """Remove all but the newest `--keep` pre-migration backups under `runtime_dir/backups`.

    **This is the documented cleanup path `V2-P4-111` chose, and choosing it over an automatic
    retention cap was the whole decision.** A cap enforced inside `run_migrations` would have
    deleted whatever a user already had the next time they ran anything, and pre-migration
    backups are the user's data -- the one copy standing between a failed migration and a
    database. So nothing is removed unless a person runs this, `--dry-run` lists first, and
    `--keep` is explicit.

    The growth it exists to clean up is fixed at the source: a run that applies nothing now
    removes the backup it took, so a store whose migration defers permanently stops adding a
    139,264-byte file per process start. This command is for the pile that accumulated before
    that -- 128 files and 16 MB in the repository this row was measured in.

    Newest first, by the timestamp in the filename via `Path.stat().st_mtime`: a backup's value
    decays, and the copy taken before a migration that is still pending is the one an operator
    might restore from. Files that are not `.bak` are never touched, so a directory somebody has
    put something else in is left alone rather than tidied.

    Exits 0 whether or not anything was removed. A cleanup command that returned non-zero on an
    already-clean tree is a command that gets `|| true`-d in the first script that uses it, which
    is `PanelExit`'s own argument about `panel doctor`'s notices.
    """
    runtime_dir = _resolved_runtime_dir(runtime_dir)

    backups = runtime_dir / "backups"
    found = sorted(
        (path for path in backups.glob("*.bak") if path.is_file()),
        key=lambda path: (path.stat().st_mtime, path.name),
        reverse=True,
    )
    doomed = found[keep:]
    if not doomed:
        typer.echo(f"{len(found)} backup(s) under {backups}, keeping {keep}; nothing to remove")
        return
    freed = sum(path.stat().st_size for path in doomed)
    verb = "would remove" if dry_run else "removed"
    for path in doomed:
        if not dry_run:
            path.unlink()
        typer.echo(f"{verb} {path.name}")
    typer.echo(
        f"{verb} {len(doomed)} of {len(found)} backup(s) ({freed} bytes), keeping the newest "
        f"{min(keep, len(found))} under {backups}"
    )


@app.command()
def serve(
    host: Annotated[
        str | None,
        typer.Option(help="Bind address. Defaults to OPENALPHA_HOST, then 127.0.0.1."),
    ] = None,
    port: Annotated[
        int | None,
        typer.Option(help="Bind port. Defaults to OPENALPHA_PORT, then 8000.", min=1, max=65535),
    ] = None,
) -> None:
    """Serve the versioned REST API.

    Precedence for both `host` and `port`: this command's own `--host`/`--port`
    flag, when given, always wins; otherwise `OPENALPHA_HOST`/`OPENALPHA_PORT`
    (including a value merged in from `.env` by `main()`); otherwise the
    `127.0.0.1:8000` default `OpenAlphaConfig` declares.

    `server_header=False` is `V2-P5-012`, closing the second half of audit `F102`: the
    `Dockerfile` passed `--no-server-header` and this command did not, so one deployment of
    the same application advertised `server: uvicorn` and the other did not -- and the one
    that leaked is the one a developer runs. Named here rather than left to the deployment,
    because the two deployments disagreeing was the whole finding.
    """
    try:
        config = load_config()
    except ConfigError as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(code=1) from error
    uvicorn.run(
        "openalpha_cn.api.app:app",
        host=host if host is not None else config.host,
        port=port if port is not None else config.port,
        server_header=False,
    )


# --- the panel plane: build, examine, gate (V2-P1-015) ----------------------------------------


class PanelExit(IntEnum):
    """Every exit code `panel build`, `panel doctor` and `data-check` issue, as one table.

    ## Why the codes are distinct rather than "zero and non-zero"

    A CI job has three different remedies available and only the exit code to pick between
    them: re-fetch the data, edit the command line, or fix the credential. Collapsing them
    into `1` makes a scheduled build indistinguishable from a typo in it.

    - `ok` -- the command answered and the answer is "nothing is wrong".
    - `unhealthy` -- the **panel** is at fault. `data-check` was refused by the gate; `panel
      doctor` found at least one `blocking` or `warning` finding; `panel build` had a batch
      refused by a write-time guard, or could not read back something it needs. Matches the
      existing `doctor`/`migrate run` convention, where 1 already means "the thing you asked
      about is not in order".
    - `bad_request` -- the **request** could not be put at all: a dataset with no declared
      cadence (`PanelDoctorError`), an override naming a dataset the request never asked about
      (`PanelGateError`), an unparseable `--as-of`, a build target outside the closed table.
      Distinct from `unhealthy` because no amount of re-fetching fixes it.
    - `provider_failure` -- the fetch never happened: authentication, quota, transport or a
      response that would not decode. Distinct again, because the panel may be perfect.
    - `internal_error` -- **the command itself broke.** Nothing was judged: an exception no
      branch here anticipated reached the top of the command, or a build target this module's
      own closed table accepted turned out to have no branch that builds it. Distinct from
      `unhealthy` for the reason that matters most in this table: without it, a CLI that
      crashed exited 1 through Typer's default handler and was indistinguishable from a panel
      that failed its check, so a scheduled job would report "the data is bad" for a defect in
      this file. The remedy is a bug report, not a re-fetch.

      **That last sentence has been measured false four times, and every time by the same
      shape.** A refusal that is a verdict about stored data, raised where no `except` in the
      command's path anticipated it, arrives here -- and then this row tells a user to file a bug
      when the remedy was to fix their panel. `V2-P4-060` was `factor build` meeting an ordinary
      mid-window delisting; `V2-P4-070` was `shortlist run` meeting an interrupted registry
      backfill, on a store where `factor build` already answered `unhealthy` with the sentence
      naming the security; `V2-P4-080` was `shortlist run` **and** `factor run` meeting an
      ordinary two-clock rename, where no face answered; `V2-P4-084` was `factor run` meeting
      three more at one seam. None was a defect in this file.

      **The last two moved where the guard has to sit, which is why they are worth reading after
      the first two.** `060` and `070` were both refusals raised *by a read*, and both were fixed
      by widening the fault tuple that read is made through. `080` was not a read at all: the
      corpus loaded cleanly and `NameHistory.record_on` refused a question asked of it afterwards,
      inside `MarketBar(...)`, where no `faults=` argument can reach. So "anticipated at the read
      that raises it" was the wrong rule to have generalised -- the rule is that every refusal
      which is a verdict about data is anticipated **wherever it is raised**, and a read is only
      the commonest such place.

      **`084` is the same lesson a third time, and it says which sites to look at.** Its three
      escaped through a call that already had an `except` -- `factor_view._PanelInputs.label`
      caught `LabelError` and let `StockUniverseError`, `AdjustmentError` and `PriceDataError`
      past, all four being independent `ValueError` subclasses. So an anticipated seam is not a
      guarded one: what has to be enumerated is every refusal the callee can raise, not the one
      whose module the caller happens to have imported. `factor_view._LABEL_CORPUS_FAULTS` is
      that enumeration, and it is the `except` clause and the message table's key set both so
      the two cannot come apart.

      See `factor_view._REGISTRY_FAULTS` and `shortlist_view._REGISTRY_FAULTS` for the two reads,
      `factor_view._risk_warned_on`, `shortlist_view._risk_warned_on` and
      `factor_view._PanelInputs.label` for the three questions asked of what a read returned, and
      `tests/integration/test_partial_registry_faces.py`,
      `tests/integration/test_unnamed_session_faces.py` and
      `tests/integration/test_unlabelled_corpus_faces.py`, which drive stores at both faces and
      at both HTTP routes. The withholding of an unanticipated exception's own message stays
      right; being unanticipated is what was wrong.

    **2 is deliberately absent.** Click raises its own `UsageError` with exit code 2 for a
    misspelled flag or a missing required option, and that is not a code this module can take
    back. Reusing it would make "you typed the command wrong" and "the gate refused you" the
    same observation. See `CLICK_USAGE_EXIT_CODE`.

    ## `panel doctor`'s own semantics, and why a notice must not reach here

    `panel doctor` exits `unhealthy` exactly when `PanelHealthReport.is_clean` is false -- that
    is, when a finding's severity is in `panel_doctor.BLOCKS_A_READ` (`blocking` or `warning`),
    and never for a `notice`.

    The argument for the notices is measurement, not taste. `V2-P1-011` drove a real
    53-security corpus end to end and `ambiguous_filing` fired on 8.15% of `income`'s filings,
    1.29% of `balancesheet`'s, 15.80% of `cashflow`'s and 13.70% of `fina_indicator`'s, with
    81.7% of `fina_indicator`'s keys carrying more than one row. A command that returned
    non-zero on those would fail on every honest financial panel, be `|| true`-d in the first
    pipeline that used it, and then protect nothing at all -- which is strictly worse than
    exiting 0, because the exit code would still *look* like a check.

    The argument for the warnings is `V2-P1-006`'s Critical. `return_path_disagreement` is a
    `warning` and it is the only code in the whole set that can see a missing factor step: it
    is what stands between a caller and the `-0.530973%` a panel with that hole answers
    against a true `+2.742251%`. A doctor that only counted `blocking` would call that panel
    healthy. Reading `is_clean` rather than re-deciding here is deliberate: the same frozenset
    drives `panel_gate.GATE_BLOCKING_SEVERITIES`, so this command and `data-check` cannot come
    to disagree about which severities matter.

    They can still disagree about a *panel*, and that is not a defect -- see
    `tests/integration/test_cli_panel.py::
    test_the_doctor_and_the_gate_disagree_on_the_same_panel_and_both_are_right`. The gate adds
    one refusal of its own, `unverified_daily_coverage`, which is not a health code because it
    is not a fault of the panel; a request that named no session is refused by the gate while
    the doctor, correctly, reports nothing wrong.
    """

    ok = 0
    unhealthy = 1
    bad_request = 3
    provider_failure = 4
    internal_error = 5


CLICK_USAGE_EXIT_CODE: Final[int] = 2
"""Click's own `UsageError` exit code, recorded here so it stays reserved.

Not raised anywhere in this module. It is written down because the only way to keep `PanelExit`
unambiguous is to know which code is already spoken for by the layer underneath, and a future
addition to `PanelExit` that reached for "the next free number" would otherwise take it.
"""

PANEL_DATE_ZONE: Final[ZoneInfo] = ZoneInfo(DEFAULT_DATE_TIMEZONE)
"""The zone every panel date is derived in, taken from `panel/catalog.py` rather than restated.

`DependencyRequest` deliberately does not expose `date_timezone`, because a request that judged
a partition against a session boundary the partition was not written to reports a `date_gap`
that is an artefact of the question; the same reasoning applies to the loop bound this module
derives for `panel build`.
"""

PANEL_BUILD_TARGETS: Final[Mapping[str, tuple[str, ...]]] = MappingProxyType(
    {
        TRADING_CALENDAR_DATASET: (TRADING_CALENDAR_DATASET,),
        STOCK_BASIC_DATASET: (STOCK_BASIC_DATASET,),
        ADJ_FACTOR_DATASET: (ADJ_FACTOR_DATASET,),
        "price": (SUSPENSION_DATASET, DAILY_DATASET, DAILY_BASIC_DATASET),
        PRICE_LIMIT_DATASET: (PRICE_LIMIT_DATASET,),
        NAMECHANGE_DATASET: (NAMECHANGE_DATASET,),
        INDEX_WEIGHT_DATASET: (INDEX_WEIGHT_DATASET,),
        INDEX_DAILY_DATASET: (INDEX_DAILY_DATASET,),
        INCOME_DATASET: (INCOME_DATASET,),
        BALANCE_SHEET_DATASET: (BALANCE_SHEET_DATASET,),
        CASH_FLOW_DATASET: (CASH_FLOW_DATASET,),
        INDUSTRY_TREE_DATASET: (INDUSTRY_TREE_DATASET,),
        INDUSTRY_MEMBERSHIP_DATASET: (INDUSTRY_MEMBERSHIP_DATASET,),
        FINANCIAL_INDICATOR_DATASET: (FINANCIAL_INDICATOR_DATASET,),
    }
)
"""What `panel build --dataset X` fetches and writes, as a closed table in **build order**.

A target is a unit of work a `panel_ingest` writer accepts, which is not always one dataset.
`price` is the case that forces the distinction: `write_daily_panel` takes `daily` and
`daily_basic` together -- there is no supported way to write one partition without the other
having agreed with it -- and its `halts` argument has no default, so the halt corpus for the
same sessions has to be fetched and stored before the pair that consumes it. That is why
`--dataset daily` cannot mean anything (see `PANEL_BUILD_COUPLED_DATASETS`), which
`panel_ingest.write_daily_panel`'s own docstring names as a constraint on this command's surface
rather than an implementation detail of that one.

**`suspend_d`'s place inside `price` is this issue's scope choice, not a constraint from below,
and the earlier wording here overstated it.** The coupling `write_daily_panel` imposes is on
`daily` and `daily_basic` only; `write_suspensions(store, batches)` takes no calendar, needs no
partner and refuses nothing about being written alone. What is true is narrower: `price` needs a
stored halt corpus *for the same year*, so this target fetches it in the same pass rather than
requiring two invocations. The cost of folding it in is real and is recorded rather than hidden
-- a caller who only wants to backfill one year of halts (~28 rows on a measured session) has to
re-fetch that year's `daily` and `daily_basic` too (~5,338 rows per session across ~244
sessions). A standalone `suspend_d` target is therefore a sound addition, deliberately left to
`V2-P1-016` rather than taken here, because adding it changes this table and the closed-table
test that pins it.

The order this table declares is the order the two build phases run their targets in, and it is
a dependency order rather than an alphabetical one: `adj_factor`, `price` and `stk_limit` all
read the stored calendar, so `trade_cal` has to have been written first on a fresh store; the
three statement targets read the stored registry, so `stock_basic` comes before them; and
`index_member_all` reads the stored tree, so `index_classify` comes before it. The command
therefore ignores the order the `--dataset` flags arrived in, which
`tests/integration/test_cli_panel.py::
test_panel_build_runs_the_targets_in_dependency_order_and_not_in_flag_order` pins by driving them
backwards.

**Thirteen targets, and the last three are not per-year.** `V2-P1-015` shipped five and refused
the other eight by name, because each is a different fetch plan and wiring them from a transport
that issue could not exercise would have been surface with no test behind it. The consequence was
that `providers/tushare.py`'s fifteen datasets and `panel_ingest`'s twelve writers had eight
datasets nothing could build: `panel build --dataset income` said "not one of this command's
build targets" and `panel doctor --dataset income` therefore reported `partition_missing`
forever, which is the state P2's `002`/`003`/`004` gates and P3's whole factor stack were
specified against. The eight are wired here, each with its measured request shape:

- `namechange` -- one announcement year of the whole market per request. One request per `--year`.
- `index_weight` -- one index for one calendar month. `INDEX_WEIGHT_INDEX_CODES` x 12 months,
  36 requests per `--year`; see `_build_index_weights` for the interior-gap refusal.
- `income` / `balancesheet` / `cashflow` -- one `(security, announcement year)` window, and
  `ts_code` is mandatory, so one request per security in the stored registry: **5,881 requests
  per `--year`, per dataset** (measured 2026-08-11).
- `index_classify` -- one taxonomy vintage per request; two requests for the whole invocation.
- `index_member_all` -- one `(l1_code, is_new)` slice; 31 x 2 = 62 requests for the whole
  invocation.
- `fina_indicator` -- one `(security, report-period year)` window; 5,881 requests per period year,
  for the whole invocation.

`PANEL_BUILD_SPAN_TARGETS` is why the last three say "for the whole invocation" rather than "per
`--year`", and it is a fact about their requests rather than a convenience.

**Fourteen targets since `V2-P3-016`, and the fourteenth is the cheapest per-year one here.**
`index_daily` is one request per `(index, year)` -- `INDEX_PRICE_INDEX_CODES` x 1, **3 requests
per `--year`**, measured 2026-08-17 -- against `index_weight`'s 36 over the same three indices,
because a level series is dated by session and a composition is published monthly. It sits
immediately after `index_weight` in this order for a reason that is legibility rather than
dependency: neither reads the other, and a reader looking for "what does this build know about
沪深300" should find its composition and its level next to each other.
"""

PANEL_BUILD_COUPLED_DATASETS: Final[Mapping[str, str]] = MappingProxyType(
    {
        DAILY_DATASET: "price",
        DAILY_BASIC_DATASET: "price",
        SUSPENSION_DATASET: "price",
    }
)
"""The datasets that exist but cannot be built alone, each pointing at the target that owns it.

Stated separately from `PANEL_BUILD_TARGETS` so the refusal can carry the *reason*. Click would
otherwise reject `--dataset daily` as an unknown choice, which reads as "this repository does
not have a daily panel" -- the opposite of the truth. `V2-P1-007`'s coupling is the reason for
`daily` and `daily_basic`; `suspend_d` is here because *this command* fetches it inside `price`,
which is a scope decision -- see `PANEL_BUILD_TARGETS`.
"""

PANEL_BUILD_SPAN_TARGETS: Final[frozenset[str]] = frozenset(
    {INDUSTRY_TREE_DATASET, INDUSTRY_MEMBERSHIP_DATASET, FINANCIAL_INDICATOR_DATASET}
)
"""Targets whose unit of work is the **whole invocation** rather than one `--year`, and why.

`panel build` runs its targets year by year, oldest first. Three of the thirteen cannot be run
that way, and in each case the reason is a property of the endpoint rather than a preference:

- **`index_classify`** takes a `src` and nothing else. One request is one vintage's whole tree,
  dated at that vintage's own effective day, so running it once per year would fetch the same
  511 rows twelve times and write the same 2021 partition twelve times.
- **`index_member_all`** takes an `(l1_code, is_new)` slice and no date filter at all. One sweep
  of 62 requests is the whole 7,893-row corpus, which `write_industry_memberships` files into
  ~38 event-year partitions; a per-year loop would re-fetch all 62 for each year and, worse,
  hand the writer a corpus it would file into the same 38 partitions each time.
- **`fina_indicator`** is the one where a per-year loop would be *destructive*, and this is the
  finding that put this set here. Its request window filters `end_date` -- the report period --
  while its rows are dated and filed at `ann_date`. So a request for period year *P* returns
  rows announced in *P* (the three interim reports) and in *P+1* (the annual), and an
  announcement year *A* is assembled from **at least two** period years: the annual of *A-1*
  plus the interims of *A*. `PanelStore` replaces a partition whole, so a loop that wrote period
  year 2015 and then period year 2016 would replace announcement year 2016's annual-2015 rows
  with 2016's interims -- more rows than before, the same securities, and no guard anywhere that
  can see it. Accumulating every requested period year into one write is the only shape that is
  not silently lossy; `_refuse_shrinking_statement_years` covers the cross-invocation half.

Every member is also in `_UNPINNED_PARTITION_YEAR_TARGETS`, necessarily: a target that does not
run per year cannot write the year it was asked for.
"""

_UNPINNED_PARTITION_YEAR_TARGETS: Final[frozenset[str]] = frozenset(
    {
        STOCK_BASIC_DATASET,
        INDUSTRY_TREE_DATASET,
        INDUSTRY_MEMBERSHIP_DATASET,
        FINANCIAL_INDICATOR_DATASET,
    }
)
"""Targets whose partitions are not the `--year` that was asked for, and why that is legitimate.

Four, and each is exempt for its own measured reason rather than by family resemblance. Every
other target writes the year it was asked for, which is what `_audit_written_partitions` checks;
this set is the exemption, stated once so a future target cannot acquire it by accident.

- **`stock_basic`** has no date filter, so one request is the whole registry and
  `write_stock_universe` splits it into one partition per *lifecycle* year -- a security listed
  in 1991 goes to 1991 whatever `--year` said.
- **`index_classify`** has no date column at all. `providers/tushare.py` dates every node at its
  vintage's effective day, so SW2014's tree is a 2014 partition and SW2021's is a 2021 one, on
  every invocation and for every `--year`.
- **`index_member_all`** is filed by *membership event* year: an assignment that opened in 1993
  and closed in 2017 puts one row in each of those partitions. A 62-request sweep therefore
  lands in roughly 38 years at once.
- **`fina_indicator`** is asked for a report-period year and filed by announcement year, and the
  two are not the same year even in the ordinary case (`001278.SZ` announced its 2018 annual on
  2022-01-06). See `PANEL_BUILD_SPAN_TARGETS`.

Renamed from `_LIFECYCLE_YEAR_TARGETS`, which described the one member it used to have.
"""

_NEEDS_STORED_UNIVERSE: Final[frozenset[str]] = frozenset(FINANCIAL_STATEMENT_DATASETS)
"""Targets that cannot name their own subjects and read them out of the stored registry.

All four statement endpoints, and the reason is `_financial_statement_params`': `ts_code` is
**mandatory** on every one of them -- a request without it fails `code=50101`, and a comma-joined
list answers zero rows with `code=0` on three of the four -- so there is no cross-section fetch
and the securities have to come from somewhere. They come from `stock_basic`, exactly as the
session-scoped targets' sessions come from `trade_cal`, which is what `_NEEDS_STORED_CALENDAR`
says one dependency over. A caller may narrow it with `--subject`; nothing infers it.
"""

_NEEDS_STORED_INDUSTRY_TREE: Final[frozenset[str]] = frozenset({INDUSTRY_MEMBERSHIP_DATASET})
"""`index_member_all`, whose 31 `l1_code` slices are read off the stored `index_classify` tree.

The alternative was a 31-entry literal in this module, which would be a second copy of a table
the panel already stores and would go stale the day Shenwan adds an industry -- and would go
stale *silently*, because a missing `l1_code` is a slice nobody fetched rather than an error. The
tree is two requests and it is the join target for every membership row anyway, so requiring it
first costs one dependency and removes a constant that could drift.

**The vintage matters and is not defaulted.** `INDUSTRY_MEMBERSHIP_TAXONOMY` is SW2021 --
measured: every one of `index_member_all`'s 7,893 rows carries an SW2021 L1 code, and the
endpoint takes no `src` -- while `index_classify`'s own default is **SW2014**. Slicing SW2021
memberships by SW2014's 28 L1 codes would silently fetch a corpus missing three whole industries.
"""

_REGISTERED_PARTITION_RESUME: Final[frozenset[str]] = frozenset(
    {INDEX_WEIGHT_DATASET, INCOME_DATASET, BALANCE_SHEET_DATASET, CASH_FLOW_DATASET}
)
"""Targets `--resume` skips on a registered partition alone, with no census behind the skip.

Deliberately a separate, named set rather than "everything the session rule cannot judge",
because the evidence really is weaker and the difference should be legible at the call site as
well as in `_resumable_targets`' docstring, which is where the residue is stated and the test
that measures it is named. Four members: `index_weight` at 36 requests a year and the three
announcement-year statement targets at 5,881 each, which is the scale that makes a weak resume
worth more than no resume.

`namechange` is not here even though it is the same shape, for `trade_cal`'s reason: it is one
request a year, so skipping it saves nothing and costs a corpus the resumed build did not verify.
"""

_EMPTY_SESSION_IS_ORDINARY: Final[frozenset[str]] = frozenset({SUSPENSION_DATASET})
"""Datasets for which a session serving no rows is the normal case rather than a short fetch.

Exactly one, and `panel_ingest.write_suspensions` gives the measurement: a session on which
nothing was halted and nothing resumed serves **zero** `suspend_d` rows, so an absent session is
indistinguishable from an empty one by construction. Every other dataset here publishes on every
open session, so a `no_data` batch is handed to the writer unchanged and refused there -- the
guard that knows what a missing session costs is the one that should say so.
"""

_PANEL_WRITE_REFUSALS: Final[tuple[type[Exception], ...]] = (
    PanelStorageError,
    PanelBatchError,
    PriceDataError,
    AdjustmentError,
    SuspensionError,
    StockUniverseError,
    IndexMembershipError,
    IndexPriceError,
    IndustryClassificationError,
    FinancialStatementError,
    TradingCalendarError,
)
"""The write-time and read-back refusals `panel build` reports rather than crashes on.

Every one of them is a statement about the *data*, so they map to `PanelExit.unhealthy`. None of
them can carry a credential: the writers never see the token, and the batch's own `source_uri`
is `tushare://{dataset}/{subject}/{date}`.

**Equal, as a set, to `panel_doctor._LOAD_FAILURES`, and pinned that way by
`tests/unit/test_cli_panel_rules.py`.** That module already answers the question this one is
asking -- "which exceptions are facts about stored data rather than defects in the code that
read it" -- and the two lists were allowed to drift apart. The doctor named all nine; this named
four, so a `SuspensionError` out of `load_suspensions` in the middle of `panel build --dataset
price` was classified `PanelExit.internal_error`: exit 5, "a defect in the command, not a
verdict about the panel", with the exception's own message withheld on the grounds that an
unanticipated failure might carry a credential. That refusal names one ticker and one session.
Two modules disagreeing about what counts as a data fact is the drift the equality test exists
to stop; if a tenth domain error is added, both lists must learn it together.

**`IndustryClassificationError` is that tenth**, added when `panel build` gained the two industry
targets. It is raised by `write_industry_memberships`, `write_industry_tree`,
`build_industry_tree` (a vintage whose parent chain is broken -- which is what a partial read of
the tree partition looks like) and `load_industry_trees`, and `index_member_all`'s own fetch plan
goes *through* that loader to get its 31 `l1_code` slices. So a malformed stored tree stops a
build, and without this entry it would have stopped it as `internal_error` with the message
withheld -- `SuspensionError`'s defect exactly, in a dataset that had not been built yet when
that one was found. `panel_doctor._LOAD_FAILURES` learns it in the same edit, as that test
requires; no cross-check raises it today, and the set is one question's answer rather than two
modules' separate inventories of what they happen to catch.

**`IndexPriceError` is the eleventh** (`V2-P3-016`), and it arrives with a raiser rather than as
defence: `panel_ingest._refuse_unrebuildable_index_prices` runs the reader's own reconstruction
over every `index_daily` batch before it is stored, so a duplicated session or a null level is a
fact about the data reported as `unhealthy`. Without this entry it would have been
`internal_error` with the message withheld -- `SuspensionError`'s defect, on the dataset whose
rows are the regressor of every residual volatility in the cross section.
"""

_NEEDS_STORED_CALENDAR: Final[frozenset[str]] = frozenset(
    {ADJ_FACTOR_DATASET, "price", PRICE_LIMIT_DATASET}
)
"""Targets whose writer takes a `TradingCalendar` and therefore needs `trade_cal` in the store
already. `write_adjustment_factors` and `write_daily_panel` both refuse a year missing a session
the calendar reports open, and that census is the whole reason those writers require it."""

SESSION_SCOPED_DATASETS: Final[tuple[str, ...]] = (
    ADJ_FACTOR_DATASET,
    DAILY_DATASET,
    DAILY_BASIC_DATASET,
    PRICE_LIMIT_DATASET,
)
"""The datasets that must reach the **same last session** for a year to be assessable at all.

Four of the seven this command writes, and the other three are excluded for reasons rather than
by omission. `trade_cal` is a whole year including days that have not happened; `stock_basic` is
keyed by lifecycle year; and `suspend_d` legitimately holds nothing for a session on which
nothing was halted, so its last covered date is a fact about the market rather than about this
build's horizon (`_EMPTY_SESSION_IS_ORDINARY` says the same thing one layer down). Each of these
four publishes on every open session, so its last covered date **is** the horizon its build ran
to. See `_refuse_split_horizon`.
"""


def _panel_store(runtime_dir: Path) -> PanelStore:
    """The panel plane inside a runtime directory.

    `runtime_dir/panel`, beside `runtime_dir/state.sqlite3`, so one `--runtime-dir` names one
    installation's whole state exactly as it already does for `migrate` and `research run`.

    Delegates to `panel_view.panel_store` rather than restating the subdirectory: `V2-P1-016`'s
    HTTP app and SDK read the same store from the same `runtime_dir`, and three faces
    disagreeing about where it lives would make every equivalence between them a coincidence.
    """
    return panel_store(runtime_dir)


def _panel_transport() -> TushareTransport:
    """The HTTP boundary `panel build` fetches through.

    A named seam, and the only one: tests replace this and everything above it -- the provider's
    credential resolution, its request envelope, its point-in-time filter, its projection and
    every `panel_ingest` guard -- runs for real. `TushareTransport` has been an injectable
    `Protocol` since `V2-P0B-013`, so this adds no indirection that was not already there.
    """
    return UrllibTushareTransport()


def _panel_clock() -> datetime:
    """The wall clock `panel build` stamps its fetches and bounds its session loop with."""
    return datetime.now(UTC)


def _panel_fail(code: PanelExit, message: str) -> typer.Exit:
    """Print `message` on stderr and return the `typer.Exit` the caller must raise.

    Returned rather than raised so every exit in this section is a visible `raise` at its own
    call site, and so `mypy` sees the control flow without a `NoReturn` that a `try` block could
    swallow. Always stderr: `--json` output has to stay parseable on stdout even when the
    command is on its way to a non-zero exit, which is precisely when a caller most needs the
    structured reasons.
    """
    typer.echo(message, err=True)
    return typer.Exit(code=int(code))


@contextmanager
def _panel_command(name: str) -> Iterator[None]:
    """Wrap one panel command so a defect in it can never be read as a verdict about the panel.

    Without this, anything the command did not anticipate -- a `NotADirectoryError` from a
    `--runtime-dir` that names a file, an `AttributeError` in a rendering -- reaches Typer's own
    handler, which prints a traceback and exits **1**. That is `PanelExit.unhealthy`, so "the CLI
    crashed" and "the panel failed its check" arrive at a CI job as the same number. This turns
    the first into `PanelExit.internal_error`, which no data can produce.

    The exception's own message is deliberately withheld, `_fetch_panel`'s rule at this module's
    third credential boundary and for a stronger reason: an *unanticipated* exception can carry
    anything the frame it escaped was holding, and a traceback with locals carries the frames
    too. Only the type and the command name are printed. `typer.Exit` and `typer.Abort` are
    re-raised untouched -- both subclass `RuntimeError`, so a bare `except Exception` here would
    otherwise swallow every deliberate exit this module raises and turn `--json` on a blocked
    gate into a crash report.
    """
    try:
        yield
    except (typer.Exit, typer.Abort):
        raise
    except Exception as error:
        raise _panel_fail(
            PanelExit.internal_error,
            f"`{name}` did not finish: it raised an unhandled {type(error).__name__}. This is a "
            "defect in the command, not a verdict about the panel -- nothing was checked and "
            "nothing here should be read as a health result. The exception's own message is "
            "withheld because an unanticipated failure can carry whatever the frame it escaped "
            "was holding, including the credential",
        ) from error


def _panel_as_of(value: str) -> datetime:
    """Resolve `--as-of`, defaulting to the wall clock.

    A naive instant is refused rather than localised: every clock in this repository is
    timezone-aware, and guessing a zone for a point-in-time query is the one error that produces
    a plausible wrong answer instead of a failure.
    """
    if not value:
        return _panel_clock()
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise _panel_fail(
            PanelExit.bad_request,
            f"--as-of expects an ISO-8601 instant with an offset, e.g. "
            f"2026-01-17T04:00:00+00:00; got {value!r}",
        ) from error
    if parsed.tzinfo is None:
        raise _panel_fail(
            PanelExit.bad_request,
            f"--as-of {value!r} carries no UTC offset; a point-in-time question answered in a "
            "guessed timezone is wrong by up to a session",
        )
    return parsed


def _panel_sessions(values: Sequence[str]) -> tuple[date, ...]:
    days: list[date] = []
    for value in values:
        try:
            days.append(date.fromisoformat(value))
        except ValueError as error:
            raise _panel_fail(
                PanelExit.bad_request,
                f"--session expects an ISO-8601 date (YYYY-MM-DD); got {value!r}",
            ) from error
    return tuple(dict.fromkeys(days))


_CALENDAR_BUILD_REMEDY: Final[str] = (
    "Build it first: `openalpha panel build --dataset trade_cal --year <year>`"
)
"""`panel build`'s own way out of a missing calendar, which is only one way.

Deliberately not `panel_view.NO_CALENDAR_REMEDY`: that one offers "state on the record that
this run has no calendar", and `panel build` has no `--no-calendar` -- the writers it drives
take a `TradingCalendar` and refuse without one -- so offering it here would name an option
this command does not have. The read side has both ways out and uses the shared text.
"""


def _stored_calendar(
    store: PanelStore, *, exchange: str, years: Sequence[int], as_of: datetime
) -> TradingCalendar:
    """The stored exchange calendar for a *build*, or an exit naming the command to run first.

    `panel build`'s own, and the read side's is `panel_view.stored_calendar`. The two differ in
    the one thing that is not shareable -- which remedies exist on this channel -- and in
    nothing else, which is why this one is a build-time helper rather than a second general
    loader: `_panel_request` below goes through the shared resolver.
    """
    try:
        return load_trading_calendar(store, exchange=exchange, years=years, as_of=as_of)
    except (TradingCalendarError, PanelStorageError) as error:
        raise _panel_fail(
            PanelExit.unhealthy,
            f"the {exchange} calendar could not be read out of {store.root}: {error}. "
            f"{_CALENDAR_BUILD_REMEDY}",
        ) from error


def _panel_request(
    *,
    runtime_dir: Path,
    dataset: Sequence[str],
    year: Sequence[int],
    session: Sequence[str] | None,
    index_code: Sequence[str] | None,
    exchange: str,
    as_of: str,
    with_calendar: bool,
) -> tuple[PanelStore, DependencyRequest]:
    """Resolve the options `panel doctor` and `data-check` share into one stated request.

    Both commands build a `DependencyRequest` even though only one of them hands it to the gate,
    so the two cannot drift about what a request *is*. Its five mandatory fields are the point:
    `sessions=()` and `calendar=None` are legitimate answers and are recorded here as answers
    rather than arriving as defaults nobody chose -- which is why `--no-calendar` exists at all
    instead of the calendar being loaded opportunistically and silently skipped when absent.

    What is left here is exactly the part that is this channel's: parsing option *strings* into
    the values a request is made of, and turning the shared resolver's two faults into exit
    codes. The resolution itself -- the de-duplication, the calendar load, the refusals for a
    naive `as_of`, an empty dataset list and a malformed exchange -- is
    `panel_view.panel_request`, the same call `GET /api/v1/panel/*` and `OpenAlphaSDK` make.
    A near-copy of it here was the drift `panel_view.py` exists to prevent, on the request side
    instead of the rendering side: three faces that render one report identically but resolve
    the request three ways still answer three different questions.

    The two faults map onto the rows `PanelExit` already has for them, by the names
    `panel_view.py` gives them. `PanelRequestError` is `bad_request` -- no re-fetch fixes a
    naive `as_of`. `PanelUnreadableError` is `unhealthy`, which is the code the hand-written
    loader this replaced already used, and right for `panel doctor`'s reason: the request was
    well formed and the panel could not answer it.
    """
    instant = _panel_as_of(as_of)
    sessions = _panel_sessions(session or ())
    store = _panel_store(runtime_dir)
    try:
        request = panel_request(
            store,
            datasets=dataset,
            years=year,
            sessions=sessions,
            index_codes=index_code or (),
            as_of=instant,
            exchange=exchange,
            with_calendar=with_calendar,
        )
    except PanelRequestError as error:
        raise _panel_fail(PanelExit.bad_request, str(error)) from error
    except PanelUnreadableError as error:
        raise _panel_fail(PanelExit.unhealthy, str(error)) from error
    return store, request


# --- human-readable output --------------------------------------------------------------
#
# The structural renderings these two commands' `--json` emits live in
# `openalpha_cn/panel_view.py`, shared verbatim with `V2-P1-016`'s HTTP app and SDK. They
# were written as standalone functions here for exactly that reason: two renderings of one
# report that disagree about which fields exist is how a caller comes to believe a severity
# is absent when it was merely dropped.


def _echo_report(report: PanelHealthReport) -> None:
    for health in report.datasets:
        state = "READY" if health.is_ready else "BLOCKED"
        years = ",".join(str(year) for year in health.years_requested)
        waived = ",".join(health.readiness.checks_waived) or "-"
        typer.echo(
            f"{state} {health.dataset} years={years} cadence={health.freshness.cadence} "
            f"rows={health.readiness.row_count} waived={waived}"
        )
    for finding in report.findings:
        typer.echo(f"{finding.severity.upper()} {finding.dataset} {finding.code}: {finding.detail}")
    for check in report.cross_checks:
        outcome = (
            f"findings={check.finding_count}" if check.ran else f"skipped: {check.skipped_reason}"
        )
        typer.echo(f"CHECK {check.name} [{','.join(check.datasets)}] {outcome}")
    # Two lines and not one, keyed on whether an entry names a dataset. A boundary of
    # `adj_factor` and a boundary of the store itself are different claims with different
    # audiences -- one is answered by choosing a different dataset, the other never is -- and
    # merging them would tell a reader of `panel doctor --dataset namechange` that `namechange`
    # has limitations when what has them is the plane underneath it. `panel_doctor`'s
    # `known_limitations` / `storage_limitations` split is the same distinction one layer down.
    #
    # Deliberately a count and not the list on both lines: the dataset half returns up to 55
    # entries, each a paragraph, and a human report that buried its own findings under them
    # would teach its readers to skim both -- the exact failure `PanelHealthReport` keeps
    # `limitations` a sibling of `findings` to avoid.
    scoped = [item for item in report.limitations if item.datasets]
    plane = [item for item in report.limitations if not item.datasets]
    if scoped:
        typer.echo(
            f"INFO {len(scoped)} known limitation(s) of these datasets "
            "(structural, not defects of this fetch); --json carries them in full, "
            "--json --no-limitation-detail names them without their prose"
        )
    if plane:
        typer.echo(
            f"INFO {len(plane)} structural boundary(ies) of the panel store itself "
            "(true of every dataset alike); --json carries them in full, "
            "--json --no-limitation-detail names them without their prose"
        )


def _echo_clearance(clearance: DependencyClearance) -> None:
    cleared = clearance.cleared_or_none
    # `cleared or ()` deliberately avoided even here, where the value is already the merged
    # shape: it is the third of the three lines `DependencyClearance` names as the ones that
    # merged blocked with ready-and-empty, and writing it once anywhere makes it the house style.
    for entry in () if cleared is None else cleared:
        sessions = ",".join(day.isoformat() for day in entry.corroborated_sessions) or "-"
        caveats = ",".join(entry.caveats) or "-"
        years = ",".join(str(year) for year in entry.years)
        typer.echo(
            f"CLEARED {entry.dataset} years={years} corroborated_sessions={sessions} "
            f"caveats={caveats}"
        )
    for block in clearance.blocks:
        typer.echo(f"BLOCKED {block.dataset} {block.code}: {block.detail}")
    for notice in clearance.notices:
        typer.echo(f"NOTICE {notice.dataset} {notice.code}: {notice.detail}")
    for name, checks in clearance.unverified_checks:
        typer.echo(f"UNVERIFIED {name} {','.join(checks)}")


# --- panel build ------------------------------------------------------------------------------


def _build_targets(requested: Sequence[str]) -> frozenset[str]:
    asked = tuple(dict.fromkeys(requested))
    for name in asked:
        owner = PANEL_BUILD_COUPLED_DATASETS.get(name)
        if owner is not None:
            raise _panel_fail(
                PanelExit.bad_request,
                f"{name!r} cannot be built on its own: write_daily_panel takes {DAILY_DATASET} "
                f"and {DAILY_BASIC_DATASET} together -- there is no supported way to write one "
                "of those partitions without the other having agreed with it -- and its halts "
                f"argument has no default, so {SUSPENSION_DATASET} is fetched in the same loop. "
                f"Ask for --dataset {owner}",
            )
        if name not in PANEL_BUILD_TARGETS:
            raise _panel_fail(
                PanelExit.bad_request,
                f"{name!r} is not one of this command's build targets "
                f"({sorted(PANEL_BUILD_TARGETS)})",
            )
    return frozenset(asked)


def _build_subjects(requested: Sequence[str], targets: frozenset[str]) -> tuple[str, ...]:
    """Resolve `--subject`, refusing it for every target that does not take one.

    `--subject` narrows the securities the four statement targets fetch, which is the one place
    in this command where the caller can shrink a whole-market fetch: `income` for one year is
    one request per security in the stored registry, so naming three securities turns 5,881
    requests into 3. Everything it is good for is that; everything else it could be pointed at
    is a dataset whose partition **is** the whole market, and narrowing one of those does not
    produce a smaller panel but a wrong one -- `_stock_basic_params` refuses a filtered registry
    for that reason in the provider, and `PanelStore` replaces a partition whole, so a narrowed
    write destroys a full one.

    Refused rather than ignored. A flag the command silently drops is indistinguishable from a
    flag the caller never passed, which is `_build_years`' finding about `--year` and the reason
    that one became repeatable; the difference here is that the silently-ignored version would
    also have looked like it worked.
    """
    asked = tuple(dict.fromkeys(requested))
    if not asked:
        return ()
    stray = sorted(targets - _NEEDS_STORED_UNIVERSE)
    if stray:
        raise _panel_fail(
            PanelExit.bad_request,
            f"--subject narrows the securities a statement target fetches and {stray} "
            f"{'do' if len(stray) > 1 else 'does'} not take one: every other target's partition "
            "is the whole market, and a partition is replaced whole, so a narrowed write would "
            f"destroy a full one rather than build a smaller panel. Statement targets are "
            f"{sorted(_NEEDS_STORED_UNIVERSE)}",
        )
    return asked


def _year_as_of(year: int) -> datetime:
    """Midnight on 1 January of `year`, in the panel's own zone.

    What `trade_cal` is asked at: `_trade_cal_params` derives the fetched year from `as_of`'s
    Asia/Shanghai year, and `_calendar_publication_timeline` dates every row of a year as
    available from the start of that year, so this instant sees the whole year and no more.
    """
    return datetime(year, 1, 1, tzinfo=PANEL_DATE_ZONE)


def _year_end_as_of(year: int, now: datetime) -> datetime:
    """The last instant of `year` in the panel's zone, never later than this build's clock.

    What `namechange`, `income`, `balancesheet` and `cashflow` are asked at, and it has to be
    the *end* of the year rather than `_year_as_of`'s start. All four take a `{start_date,
    end_date}` window that the provider derives from `as_of`'s Asia/Shanghai year, and all four
    are clocked at the **announcement**: a row announced on 14 June is available from that day
    and no earlier, so a request made at 1 January fetches exactly the right window and
    `_decode_panel_rows` then drops every row in it except any announced on 1 January itself.
    `trade_cal` is the contrast that makes this a per-dataset choice rather than a global one:
    `_calendar_publication_timeline` dates a whole year's sessions as available from the start of
    that year, so `_year_as_of` is right there and would be wrong here. Measured 2026-08-11:
    `namechange` asked at the end of 2012 returns 320 rows whose announcement dates run
    2012-01-05..2012-12-31, and at the end of 2024, 330.

    `min(..., now)` is what makes the current year work. `_decode_panel_rows` bounds its filter at
    the earlier of `as_of` and the wall clock, so an `as_of` in December would already be safe --
    but `ColumnarPanelBatch.as_of` is a stored provenance field, and a partition that claims to
    answer as of a December that has not happened is a claim this command should not write down.
    The measured behaviour is the same either way: 381 `namechange` rows for 2026 on 2026-08-11.

    Refuses a year that has not begun, for `_build_sessions`' reason at the other end of the same
    question -- without it, `--year 2030` would fetch 2026's window, store a 2026 partition, and
    be caught only by `_audit_written_partitions`' misfiled-year check, which would report a
    fetch fault for what is a plain fact about the calendar.
    """
    opens_on = datetime(year, 1, 1, tzinfo=PANEL_DATE_ZONE)
    if now < opens_on:
        raise _panel_fail(
            PanelExit.bad_request,
            f"{year} had not begun at {now.isoformat()}; there is nothing to build yet",
        )
    return min(datetime(year, 12, 31, 23, 59, 59, tzinfo=PANEL_DATE_ZONE), now)


def _month_end_as_of(year: int, month: int, now: datetime) -> datetime | None:
    """The last instant of one calendar month, clamped at `now`, or `None` if it has not begun.

    `index_weight`'s request window. `_index_weight_params` derives `{start_date, end_date}` from
    `as_of`'s Asia/Shanghai *month* and the endpoint publishes on that month's last open session,
    so the instant has to sit at or after that session's 16:30 -- which the month's own last
    instant always does, and its first never does. Measured on 2026-08-11 for `000300.SH`: 19
    month-end requests (all twelve months of 2024, January to July of 2026) each returned exactly
    one 300-row publication, and 20 first-of-month requests over the same span returned `no_data`
    every time.

    `None` rather than a refusal for a month that has not begun, because a partial current year
    is the ordinary case rather than a fault: `_build_index_weights` is what decides whether the
    resulting gap is legitimate, and it can only decide that by looking at all twelve.
    """
    opens_on = datetime(year, month, 1, tzinfo=PANEL_DATE_ZONE)
    if now < opens_on:
        return None
    # The first of the following month, less one microsecond, is this month's last instant --
    # no table of month lengths and no leap-year branch, which is the arithmetic
    # `_index_weight_params` uses for the same boundary.
    next_month = datetime(year + month // 12, month % 12 + 1, 1, tzinfo=PANEL_DATE_ZONE)
    return min(next_month - timedelta(microseconds=1), now)


def _session_as_of(day: date) -> datetime:
    """The instant a session's cross section became knowable: 16:30 Asia/Shanghai.

    `DAILY_AVAILABILITY_TIME`, imported rather than restated -- it is the same constant
    `providers/tushare.py` dates `available_time` at and `panel_ingest` bounds its session
    census with, so a request built here cannot come to disagree with the row it asks for.
    """
    return datetime.combine(day, DAILY_AVAILABILITY_TIME, tzinfo=PANEL_DATE_ZONE)


def _build_sessions(calendar: TradingCalendar, year: int, now: datetime) -> tuple[date, ...]:
    """Every session this build has to fetch, and the bound is not a choice.

    A partition must hold every session the calendar reports open between 1 January of its year
    and the newest session that had **published** at `now` -- the lower bound because a partition
    that begins in March is exactly the hole `panel_ingest._session_census` exists for, the upper
    because a session becomes knowable at `DAILY_AVAILABILITY_TIME` (16:30 Asia/Shanghai) on its
    own day. A loop over anything narrower leaves a hole the panel's own reader will find; a loop
    over anything wider asks for sessions that have not published.

    **The upper bound is `panel_ingest._sessions_published_through`, imported rather than
    restated, and `V2-P4-063` is what restating it cost.** This function used to subtract a day
    unconditionally, which is that rule only for the part of the day before 16:30. Above it the
    two came apart by exactly one session, and that session is the one the whole price plane
    then disagreed about: `_price_requirement` clamps a dataset's `required_dates` at
    `_sessions_published_through`, so `panel doctor` *required* it; `_read_visible_price_session`
    refuses only what is past that bound, so a read would have *served* it; and
    `newest_published_session` resolves a shortlist's pricing session through it, so
    `shortlist run` *priced* against it.

    Measured on the real corpus (`V2-P4-063`'s own reproduction): `panel build --as-of
    2026-02-10T09:00Z` (17:00 Asia/Shanghai) stored eleven sessions ending 2026-02-09, and
    `panel doctor --as-of` the same literal instant answered `BLOCKING ... date_gap: 1 required
    date(s) are absent from daily, starting at 2026-02-10`, exit 1. Reproduced on the generated
    fixture by `CLOSE_CLOCK` in `tests/integration/test_cli_panel_horizon.py` -- a different
    instant and a different dataset, 2026-01-20T17:00+08 and `stk_limit`, because `adj_factor`
    waives `required_dates` and could not have shown it -- with the identical shape: eleven sessions
    ending 2026-01-19 against `date_gap ... starting at 2026-01-20`. The product contradicting
    itself about its own output at its own instant, which in CI is a hard failure with no correct
    `--as-of` to give it. Three rules against one; the one was here.

    Sharing the function rather than the arithmetic is the point: `min(date(year, 12, 31),
    published_through)` is `_price_requirement`'s own expression, so what this loop fetches and
    what a health check requires are now the same set by construction rather than by agreement.
    """
    opens_on = date(year, 1, 1)
    closes_on = min(date(year, 12, 31), _sessions_published_through(now, PANEL_DATE_ZONE))
    if closes_on < opens_on:
        raise _panel_fail(
            PanelExit.bad_request,
            f"no session of {year} had published at {now.isoformat()}; there is nothing to "
            "build yet",
        )
    return calendar.trading_days_between(opens_on, closes_on)


def _stored_horizon(store: PanelStore, dataset: str, year: int) -> date | None:
    """The last session `dataset`'s stored partition for `year` covers, or `None`.

    `None` for both "no partition" and "a partition with no coverage census", because neither
    can say what horizon the build that wrote it ran to, and `_refuse_split_horizon` may only
    refuse on evidence.
    """
    if year not in store.registered_years(dataset):
        return None
    coverage = store.read_coverage(dataset, year)
    if coverage is None or not coverage.dates:
        return None
    return max(entry.event_date for entry in coverage.dates)


def _refuse_split_horizon(
    store: PanelStore, *, sessions: Sequence[date], year: int, exchange: str, rewritten: Set[str]
) -> None:
    """Refuse a build whose horizon disagrees with a session-scoped partition already stored.

    ## The panel this stops from existing

    `panel build` reads its clock once per **invocation**, and the five targets are five
    invocations. A build that starts at 23:34 and finishes at 00:39 Asia/Shanghai therefore
    runs some of its targets against `today - 1` and the rest against `tomorrow - 1`, and the
    panel that lands cannot be assessed clean at **any** `as_of`. Measured by
    `tests/e2e/test_panel_chain_online.py` on this suite's own first build:

        daily / daily_basic / adj_factor   144 sessions, last 2026-08-07
        stk_limit                          145 sessions, last 2026-08-10

    Before `stk_limit`'s newest row is knowable, `panel doctor` reports `stk_limit
    not_yet_knowable`; at or after it, the calendar requires 2026-08-10 of the price panel,
    which does not have it, and the report is `daily date_gap`. Both refusals are correct and
    there is no third instant. The only remedy once it has happened is re-fetching the lagging
    targets, which cost 386s and 2,374s on that run.

    ## Why a refusal here rather than a warning, and why it is not a rule about time

    This runs **before the first session is fetched** -- after the calendar load, which is what
    `sessions` needs, and before any of the work. So the cost of being wrong is one command that
    has to be re-run with a flag, against forty-five minutes of fetching that has to be thrown
    away. It also does not reason about clocks at all: it compares the last session *this build
    would reach* against the last session a sibling partition *already covers*, both derived
    from the same stored calendar, so it catches a split horizon whatever produced it -- a clock
    that rolled over, a `--as-of` typo, a partial re-fetch a week later.

    ## Why `rewritten` is subtracted, and why that is the whole difference between a guard and
    ## a wall

    A disagreement is not always an accident: extending a stored year by a day is a legitimate
    thing to want. `PanelStore` replaces a partition **whole** -- there is no append -- so
    "extend the panel" means "rebuild every session-scoped partition of the year", and a rule
    that compared this build's horizon against every stored partition would refuse the very
    command that does it correctly. What matters is not whether the store disagrees *now* but
    whether it will still disagree when this build finishes, so the comparison is against the
    partitions this invocation is **not** going to replace.

    That makes the two remedies real rather than rhetorical, and they are the two the message
    offers: name every session-scoped target in one invocation, which reads one clock and moves
    them together, or pin `--as-of` at the stored horizon. Doing it one target at a time without
    pinning is the case that stays refused, and it is the case that produced the defect.

    The second remedy is only offered when it exists. A store written *before* this guard can
    already hold siblings that disagree with each other -- that is the panel the e2e suite
    measured -- and there is then no instant that agrees with all of them, so naming one would
    be a remedy that fails on the next run. `_pinning_remedy` says which case this is.
    """
    if not sessions:
        return
    reached = sessions[-1]
    stored = {
        dataset: horizon
        for dataset in SESSION_SCOPED_DATASETS
        if dataset not in rewritten
        and (horizon := _stored_horizon(store, dataset, year)) is not None
        and horizon != reached
    }
    if not stored:
        return
    listed = ", ".join(
        f"{dataset} stops at {horizon.isoformat()}" for dataset, horizon in sorted(stored.items())
    )
    raise _panel_fail(
        PanelExit.unhealthy,
        f"this build would reach {reached.isoformat()} and the {exchange} panel for {year} "
        f"already holds partitions that do not: {listed}. A panel whose session-scoped datasets "
        "stop on different days cannot be assessed clean at any as_of -- earlier than the "
        "newest partition's last row and the doctor reports not_yet_knowable, at or after it "
        "and it reports a date_gap in the older ones -- so this is refused before anything is "
        "fetched rather than after. Either build the session-scoped targets together in one "
        "invocation, which moves the horizon atomically because a partition is replaced whole, "
        f"{_pinning_remedy(stored)}",
    )


def _pinning_remedy(stored: Mapping[str, date]) -> str:
    """The `--as-of` that would make this build agree with `stored`, or why there is none.

    A remedy printed in a refusal is a promise, so it is only made when it can be kept. When
    every stored sibling stops on the same session there is an instant that reproduces it --
    midday on the day after, because `_build_sessions` bounds at the newest session that had
    published and midday is below the 16:30 publication, so that day's own session is not yet
    owed. Midday rather than any instant on that day: since `V2-P4-063` the bound reads the
    clock as well as the date, so an instant on the day after but *at or past* 16:30 would carry
    the horizon one session further and reproduce nothing.

    When they *disagree with each other*, no instant agrees with all of them, and offering the
    oldest one's would produce a build the next sibling refuses. That state cannot be created
    by a build this guard has seen, but it can be inherited: it is exactly the panel the e2e
    suite measured before `--as-of` existed. So the message says so instead.
    """
    horizons = set(stored.values())
    if len(horizons) > 1:
        return (
            "or -- since those partitions do not agree with each other either, so no single "
            "--as-of reproduces all of them -- rebuild every one of "
            f"{sorted(SESSION_SCOPED_DATASETS)} in one invocation"
        )
    pinned = datetime.combine(
        horizons.pop() + timedelta(days=1), time(12, 0), tzinfo=PANEL_DATE_ZONE
    )
    return f"or pin this build to the stored horizon with --as-of {pinned.isoformat()}"


def _fetch_panel(
    provider: TushareProvider,
    dataset: str,
    *,
    as_of: datetime,
    subjects: tuple[str, ...] = (),
) -> ColumnarPanelBatch:
    """One panel-plane fetch, reporting a refusal without ever echoing its message.

    `_probe_report`'s rule at this module's second credential boundary, and the reason is the
    same: `ProviderFailure.message` can carry the token or the URL query string it was sent in,
    so only the closed-`Literal` category, the provider id and the dataset name are safe to
    print or log.
    """
    try:
        return provider.fetch_panel(
            ProviderRequest(dataset=dataset, as_of=as_of, subjects=subjects)
        )
    except ProviderFailure as failure:
        logger.warning(
            "panel_fetch_failed",
            extra={
                "provider_id": failure.provider_id,
                "category": failure.category,
                "dataset": dataset,
            },
        )
        raise _panel_fail(
            PanelExit.provider_failure,
            f"provider {failure.provider_id} refused dataset {dataset}: {failure.category}. "
            "The failure's own message is withheld because it can carry the credential it was "
            "sent with; `openalpha doctor --probe` checks the credential itself",
        ) from failure


PANEL_PROGRESS_EVERY: Final[int] = 10
"""The smallest number of requests one `panel build` progress line may cover.

Ten, against a year-to-date build of ~145 sessions, so a target reports about fifteen times --
often enough that a stalled fetch is visible within a minute or two, rarely enough that the
lines are not themselves the output. The measured builds this was chosen against: `adj_factor`
386--444s, `price` ~1,000--2,374s, `stk_limit` ~330s, whole build ~30--50 minutes, during which
this command printed **nothing at all** and a caller could not tell a live fetch from a wedged
one without `lsof`.

A *floor* rather than the stride itself since the statement targets arrived: those loop over the
5,881 securities of the stored registry rather than over 145 sessions, and a line every ten would
be 589 of them for one dataset-year. `_progress_stride` is what keeps both readable, and this
constant is what keeps every loop that existed before them reporting exactly as it did.
"""

PANEL_PROGRESS_REPORTS: Final[int] = 40
"""At most about this many progress lines from one loop, whatever its length.

Forty, and the number is a compromise between two things that cannot both be had on a loop of
5,881 requests: few enough lines that they are not themselves the output, and short enough gaps
that a wedged fetch is visible. At the 1.1--4.6s per request measured on 2026-08-11 a whole-market
statement year is roughly two to seven hours, so forty reports is one every three to ten minutes;
ten reports would have been one every twenty, and one every two minutes would be nearly six
hundred lines. The `BUDGET` line is what covers the interval before the first report.

It binds only above 400 requests: below that `PANEL_PROGRESS_EVERY` is the larger of the two, so
every loop this command already ran keeps its existing cadence to the line -- 145 sessions and
even a 244-session year still report every ten, and
`tests/integration/test_cli_panel_horizon.py` pins the eleven-session case unchanged.
"""


def _progress_stride(total: int) -> int:
    """How often to report over a loop of `total` requests. See the two constants above."""
    return max(PANEL_PROGRESS_EVERY, -(-total // PANEL_PROGRESS_REPORTS))


def _echo_progress(
    datasets: Sequence[str], done: int, total: int, started: float, *, unit: str = "sessions"
) -> None:
    """One progress line on stderr: what, how far, how long, and how much longer.

    stderr rather than stdout, for `_panel_fail`'s reason at the other end of the same command:
    `--json` has to stay parseable on stdout, and a progress line interleaved into it would make
    every scripted caller's `json.loads` fail. The remaining estimate is linear in requests made,
    which is the right model for every loop that uses it -- each iteration is one round trip of
    the same shape -- and it is labelled `eta` rather than presented as a promise.

    `unit` names what is being counted, because the loops are no longer all sessions: the
    statement targets count `securities`, `index_weight` counts `index-months` and
    `index_member_all` counts `industry-slices`. The default keeps every existing line
    byte-identical.
    """
    elapsed = monotonic() - started
    rate = elapsed / done if done else 0.0
    typer.echo(
        f"FETCHING {'+'.join(datasets)} {done}/{total} {unit} "
        f"elapsed={elapsed:.0f}s eta={rate * (total - done):.0f}s",
        err=True,
    )


def _echo_budget(label: str, total: int, unit: str, reason: str) -> None:
    """State the size of a fetch **before** it starts, on stderr.

    The statement targets turned this command's unit of cost from minutes into hours: `income`
    for one year is one request per security in the registry, 5,881 of them, and at the 1.1--4.6s
    per round trip measured on 2026-08-11 that is between two and seven hours for one
    dataset-year. A caller who typed `--start 2015 --end 2026` has asked for twelve times that,
    per dataset, and the only honest moment to say so is before the first request rather than in
    an `eta` that appears after ten minutes.

    Printed for every fetch loop, not only the expensive ones, so that the number a caller reads
    is always the same kind of number. `_echo_progress` then tracks it.
    """
    typer.echo(f"BUDGET {label} {total} {unit} ({reason})", err=True)


def _session_batches(
    provider: TushareProvider, datasets: Sequence[str], sessions: Sequence[date]
) -> dict[str, list[ColumnarPanelBatch]]:
    """Fetch every named dataset for every session, one pass over the sessions.

    Takes a *set* of datasets rather than one, so the price target's three-in-one-loop shape --
    which `write_daily_panel`'s docstring predicted of this command, and which is what lets a
    session's halts be fetched beside the bars they explain -- is the same code path a
    single-dataset target uses. A second copy of this loop for the price panel would leave the
    `_EMPTY_SESSION_IS_ORDINARY` branch dead in one of the two.

    Reports progress every `_progress_stride` sessions and once at the end, after stating the
    size of the loop up front. This was the one loop in the command that took minutes rather
    than seconds; the statement targets' loop now takes hours, which is why the cadence and the
    budget line are shared rather than written here.
    """
    collected: dict[str, list[ColumnarPanelBatch]] = {name: [] for name in datasets}
    _echo_budget(
        "+".join(datasets),
        len(sessions) * len(datasets),
        "requests",
        f"{len(sessions)} sessions x {len(datasets)} whole-market cross sections",
    )
    started = monotonic()
    stride = _progress_stride(len(sessions))
    for index, day in enumerate(sessions, start=1):
        for name in datasets:
            batch = _fetch_panel(provider, name, as_of=_session_as_of(day))
            if batch.status == "no_data" and name in _EMPTY_SESSION_IS_ORDINARY:
                continue
            collected[name].append(batch)
        if index % stride == 0 or index == len(sessions):
            _echo_progress(datasets, index, len(sessions), started)
    return collected


def _subject_batches(
    provider: TushareProvider,
    dataset: str,
    *,
    subjects: Sequence[str],
    as_of: datetime,
    label: str,
    reason: str,
    extra: tuple[str, ...] = (),
) -> list[ColumnarPanelBatch]:
    """Fetch one dataset once per subject, keeping the batches that carried rows.

    The statement targets' loop. `_session_batches`' shape with the axes swapped: there the
    request is a whole-market cross section and the loop is over days, here the request is one
    security's window and the loop is over the registry, because `_financial_statement_params`'
    `ts_code` is mandatory and a comma-joined list answers zero rows rather than an error on
    three of the four endpoints.

    **A `no_data` subject is ordinary here, and that is measured rather than assumed.** It is the
    opposite of `_EMPTY_SESSION_IS_ORDINARY`, which is a closed set of one because every other
    session-scoped dataset publishes on every open session. A security that announced nothing in
    a given year is the common case, not the exception: `000013.SZ` served no `income` row for
    the 2024 window on 2026-08-11 while `000003.SZ` -- delisted in 2002 -- served three. So the
    filter is on the whole loop rather than on a named dataset, and what stands behind it is the
    caller's own refusal when *nothing at all* came back (see `_build_statement_panel`).

    `extra` carries the second subject `fina_indicator` needs, the report-period year. It is a
    request subject and never a stored one -- `subject_field` reads `ts_code` off the row -- which
    is the arrangement `index_member_all` already has for `is_new`.
    """
    collected: list[ColumnarPanelBatch] = []
    _echo_budget(label, len(subjects), "requests", reason)
    started = monotonic()
    stride = _progress_stride(len(subjects))
    for index, subject in enumerate(subjects, start=1):
        batch = _fetch_panel(provider, dataset, as_of=as_of, subjects=(subject, *extra))
        if batch.status == "success":
            collected.append(batch)
        if index % stride == 0 or index == len(subjects):
            _echo_progress((dataset,), index, len(subjects), started, unit="securities")
    return collected


def _build_price_panel(
    store: PanelStore,
    provider: TushareProvider,
    *,
    written: list[PartitionRef],
    sessions: Sequence[date],
    calendar: TradingCalendar,
    year: int,
    now: datetime,
    halts: bool,
) -> str:
    """Fetch the three price datasets session by session, then write them in dependency order.

    One loop over the sessions rather than three, which is what `write_daily_panel`'s docstring
    predicted of this command: the halt corpus for a session is fetched beside the bars it
    explains, so the strongest guard in that writer -- the one that refuses a session whose
    missing bars nothing accounts for -- is given a real corpus rather than the `None` that
    switches it off.

    Appends into the caller's `written` list rather than returning one of its own, because the
    `suspend_d` partition is stored *before* `write_daily_panel` is even called: a refusal from
    that writer leaves a real partition behind, and a list that only exists on the success path
    cannot say so. See `_stored_so_far`.
    """
    collected = _session_batches(provider, PANEL_BUILD_TARGETS["price"], sessions)
    halt_batches = collected[SUSPENSION_DATASET]
    if halt_batches:
        written.append(write_suspensions(store, halt_batches))
    corpus = None
    if halts:
        if not halt_batches:
            raise _panel_fail(
                PanelExit.unhealthy,
                f"{SUSPENSION_DATASET} served no rows for any session of {year}, so the halt "
                "corpus write_daily_panel's explained-share guard needs does not exist. That is "
                "a fetch to investigate, not a check to skip -- pass --no-halts to state on the "
                "record that this build waives that guard",
            )
        corpus = load_suspensions(store, years=(year,), as_of=now, max_staleness=None)
    written.extend(
        write_daily_panel(
            store,
            bars=collected[DAILY_DATASET],
            fundamentals=collected[DAILY_BASIC_DATASET],
            calendar=calendar,
            halts=corpus,
        )
    )
    return "corroborated" if corpus is not None else "waived"


def _stored_universe(store: PanelStore, *, now: datetime) -> tuple[str, ...]:
    """Every security the stored registry knows about, or an exit naming what to build first.

    `_NEEDS_STORED_CALENDAR`'s shape for the statement targets, and the same argument: the
    request needs something the panel already holds, so the build reads it rather than inventing
    it. What it reads is `stock_basic`, the only whole-market security list this repository has.

    ## Every security, not the ones that were listed that year

    The obvious saving is to skip a security that had not listed yet, or had already died -- and
    it is measurably unsound in **both** directions. `688981.SH` listed on the A-share market in
    2020 and its `income` window for 2015 returns five rows; `000003.SZ` was delisted in 2002
    and its `income` window for 2024 returns three. Both measured on 2026-08-11. An announcement
    year is a disclosure calendar rather than a trading calendar: pre-listing filings enter the
    corpus when a company registers, and restatements keep arriving long after a delisting. So a
    lifecycle filter would drop real filings, and would drop them silently -- the missing
    security would simply have no row, which is what "this company did not file" looks like.

    ## `require_years_through` rather than the bare registered years

    `load_stock_universe`'s own docstring names `store.registered_years(...)` as "the natural
    argument -- and it is also the trap", because passing it makes the request exactly equal to
    whatever the store happens to hold and a missing year can never be noticed. The switch it
    provides is used here: the read must cover a contiguous window through this build's own year,
    which on a complete ingest costs nothing (a live probe found lifecycle events in every year
    from 1990 to 2026) and on an incomplete one refuses rather than handing back a universe in
    which everything that died in the gap is still listed.

    `max_staleness=None` for `_build_price_panel`'s reason at the same layer: this read is asking
    which securities *exist*, not how fresh the registry is, and a bound chosen here would be
    chosen for the caller. A registry that has missed a month of listings yields a slightly small
    universe rather than a wrong one, and `--dataset stock_basic` in the same invocation refreshes
    it before this runs.
    """
    years = store.registered_years(STOCK_BASIC_DATASET)
    if not years:
        raise _panel_fail(
            PanelExit.unhealthy,
            f"the statement targets fetch one request per security and {STOCK_BASIC_DATASET} is "
            f"not in {store.root}: the endpoints require a ts_code and there is no cross-section "
            "fetch, so the registry is where the securities come from. Build it first: "
            "`openalpha panel build --dataset stock_basic --year <year>`, or name the securities "
            "with --subject",
        )
    universe = load_stock_universe(
        store,
        years=years,
        as_of=now,
        max_staleness=None,
        require_years_through=now.astimezone(PANEL_DATE_ZONE).year,
    )
    return tuple(entry.ts_code for entry in universe.securities)


def _build_statement_panel(
    store: PanelStore,
    provider: TushareProvider,
    *,
    dataset: str,
    subjects: Sequence[str],
    as_of: datetime,
    label: str,
    reason: str,
    extra: tuple[str, ...] = (),
) -> list[ColumnarPanelBatch]:
    """Fetch one statement dataset for every subject and refuse a sweep that served nothing.

    Returns the batches rather than writing them, because the three announcement-year targets
    and `fina_indicator` write at different moments -- per year and once per invocation
    respectively (`PANEL_BUILD_SPAN_TARGETS`) -- and a helper that wrote would have to know which.

    The refusal is the counterweight to `_subject_batches` treating `no_data` as ordinary. One
    security with nothing to report is the common case; **every** security with nothing to report
    is a fetch that did not work, and without this it would reach `write_financial_statements` as
    an empty list and be refused by `merge_panel_batches` with "needs at least one batch" -- a
    true sentence about a list, several layers from the fetch that produced it.
    """
    batches = _subject_batches(
        provider, dataset, subjects=subjects, as_of=as_of, label=label, reason=reason, extra=extra
    )
    if not batches:
        raise _panel_fail(
            PanelExit.unhealthy,
            f"{label}: none of the {len(subjects)} securities served a filing. A security that "
            "announced nothing in a window is ordinary and all of them is not, so this is a "
            "fetch to investigate rather than an empty partition to write",
        )
    return batches


def _build_index_weights(
    store: PanelStore, provider: TushareProvider, *, year: int, now: datetime
) -> list[PartitionRef]:
    """Fetch every index-month of one year and write them as one partition.

    One request is one index for one calendar month (`_index_weight_params`), and a year's
    partition has to arrive in **one** call: `PanelStore` replaces a partition whole and its key
    has no index dimension, so a per-index loop leaves the year holding whichever index went last
    -- which `write_index_weights`' own docstring names, along with the per-month loop its subject
    guard cannot see. This function is the caller that docstring asks for.

    ## Which months are allowed to be missing

    The endpoint publishes on the last open session of each month, so a full past year has twelve
    publications and the current year has one per month that has finished publishing. Measured on
    2026-08-11: `000300.SH` returns exactly one 300-row publication for each of 2024's twelve
    months and for 2026 January through July, and `no_data` for August, whose last open session
    had not arrived.

    A gap at either **end** is therefore legitimate -- an index that had not launched yet at the
    start of the year (`000852.SH` begins in 2014), and the months of the current year that have
    not published. A gap in the **middle** is not, and it is refused here rather than left to the
    read: `domain/index_membership.py::build_index_membership` does refuse a hole in the month
    sequence, but only when the partition is loaded, so without this the build reports success and
    the panel is unreadable from then on.

    An index that published in **no** month of the year contributes nothing and is not refused,
    because a year entirely before an index's launch has no interior to have a hole in. What
    stops that from quietly shrinking a stored year is `panel_ingest._refuse_to_drop_stored_
    subjects`, whose subject column is the index precisely so it can see one go missing.

    ## The three indices are the build's scope, and this command offers no way to widen it

    `INDEX_WEIGHT_INDEX_CODES` is not a limit on what the descriptor can fetch -- it takes any
    `index_code` -- but it is a limit on what has been *measured*: the response cap, the monthly
    cadence, the weight-sum tolerance and the constituent-count exceptions in
    `domain/index_membership.py` were all established on those three, and a fourth index would
    inherit the code without inheriting any of that. `--subject` is deliberately not repurposed
    here; adding an index is a measurement, not a flag.
    """
    batches: list[ColumnarPanelBatch] = []
    months = [month for month in range(1, 13) if _month_end_as_of(year, month, now) is not None]
    total = len(months) * len(INDEX_WEIGHT_INDEX_CODES)
    _echo_budget(
        f"{INDEX_WEIGHT_DATASET} year={year}",
        total,
        "requests",
        f"{len(INDEX_WEIGHT_INDEX_CODES)} indices x {len(months)} month-end publications",
    )
    started = monotonic()
    stride = _progress_stride(total)
    done = 0
    for index_code in INDEX_WEIGHT_INDEX_CODES:
        published: list[int] = []
        for month in months:
            instant = _month_end_as_of(year, month, now)
            assert instant is not None  # `months` is exactly the ones that resolved
            batch = _fetch_panel(
                provider, INDEX_WEIGHT_DATASET, as_of=instant, subjects=(index_code,)
            )
            done += 1
            if batch.status == "success":
                published.append(month)
                batches.append(batch)
            if done % stride == 0 or done == total:
                _echo_progress((INDEX_WEIGHT_DATASET,), done, total, started, unit="index-months")
        absent = [
            month
            for month in months
            if published and published[0] < month < published[-1] and month not in published
        ]
        if absent:
            raise _panel_fail(
                PanelExit.unhealthy,
                f"{index_code} published in months {published} of {year} and served nothing for "
                f"{absent}, which lie between two publications. A month inside an index's life "
                "with no publication is a hole rather than a horizon, and "
                "build_index_membership refuses one on every read -- so the partition this build "
                "would write could never be loaded",
            )
    if not batches:
        raise _panel_fail(
            PanelExit.unhealthy,
            f"none of {list(INDEX_WEIGHT_INDEX_CODES)} published a constituent weighting in "
            f"{year}; the earliest of the three begins in 2005 and the newest in 2014, so a year "
            "before that has no partition to write rather than an empty one",
        )
    return [write_index_weights(store, batches)]


def _build_index_prices(
    store: PanelStore, provider: TushareProvider, *, year: int, now: datetime
) -> list[PartitionRef]:
    """Fetch one year of levels for every index and write them as one partition (`V2-P3-016`).

    **Three requests per `--year`**, measured on 2026-08-17: one request is one index's whole
    calendar year (`_index_daily_params`), and `INDEX_PRICE_INDEX_CODES` has three members. That
    is the cheapest per-year target in this command -- `index_weight` covers the same three
    indices in 36 -- and the difference is the two datasets' cadences rather than an
    optimisation: a composition publishes monthly and a level publishes every session, so a
    year's worth of levels is one window where a year's worth of compositions is twelve.

    A year's partition has to arrive in **one** call for `_build_index_weights`' reason exactly:
    `PanelStore` replaces a partition whole and its key has no index dimension, so a per-index
    loop would leave the year holding whichever index went last.
    `panel_ingest._refuse_to_drop_stored_subjects` is what refuses that, and it can only see it
    because the subject column is the index.

    ## Which indices are allowed to serve nothing, and which gap is refused

    No interior-gap check, and that is the substantive difference from `_build_index_weights`.
    There, a month with no publication inside an index's life is a hole a monthly cadence makes
    visible and `build_index_membership` refuses on every read. Here the cadence is per session
    and the census belongs to the calendar: `index_price_requirement` states `required_dates`
    from the stored `trade_cal` and `required_subjects` as `MARKET_INDEX_CODE`, so a year missing
    a session of the series a factor reads is blocked at every read with a `date_gap` and a year
    missing the series entirely with a `subject_missing`. Re-deriving that here would be a second
    calendar for this command to disagree with the partition it just wrote.

    A year entirely before an index began is an empty response and not a fault -- `000905.SH` and
    `000852.SH` are both published only from their common 2004-12-31 base point, and `000300.SH`
    from 2002-01-04 -- but a year in which **none** of the three served a row has no partition to
    write, and saying so is better than writing an empty one that every later read reports as a
    date gap.

    ## The three indices are the build's scope, and this command offers no way to widen it

    `INDEX_PRICE_INDEX_CODES` is `INDEX_WEIGHT_INDEX_CODES` and the argument is that function's:
    the cap, the nullability and the return-path reconciliation in `domain/index_prices.py` were
    all measured on those three, and a fourth index would inherit the code without inheriting any
    of it. Only one of the three is reachable from a factor at all; the other two are stored so
    that a level and a composition are answerable for the same index or for neither.
    """
    _echo_budget(
        f"{INDEX_DAILY_DATASET} year={year}",
        len(INDEX_PRICE_INDEX_CODES),
        "requests",
        f"{len(INDEX_PRICE_INDEX_CODES)} indices x 1 whole-year window",
    )
    started = monotonic()
    batches: list[ColumnarPanelBatch] = []
    instant = _year_end_as_of(year, now)
    for done, index_code in enumerate(INDEX_PRICE_INDEX_CODES, start=1):
        batch = _fetch_panel(provider, INDEX_DAILY_DATASET, as_of=instant, subjects=(index_code,))
        if batch.status == "success":
            batches.append(batch)
        _echo_progress(
            (INDEX_DAILY_DATASET,), done, len(INDEX_PRICE_INDEX_CODES), started, unit="index-years"
        )
    if not batches:
        raise _panel_fail(
            PanelExit.unhealthy,
            f"none of {list(INDEX_PRICE_INDEX_CODES)} served a level in {year}; the earliest of "
            "the three is published from 2002-01-04 and the other two from their 2004-12-31 base "
            "point, so a year before that has no partition to write rather than an empty one",
        )
    return [write_index_prices(store, batches)]


def _build_industry_tree(
    store: PanelStore, provider: TushareProvider, *, now: datetime
) -> list[PartitionRef]:
    """Fetch every measured taxonomy vintage's whole tree, one partition each.

    Two requests, one per entry in `INDUSTRY_TAXONOMY_EFFECTIVE_FROM`, and both of them rather
    than only the one `index_member_all` speaks. The endpoint refuses an unmeasured `src` and
    answers a bare request with **SW2014** while every membership row is SW2021, so the vintage
    can never be defaulted; having refused the default, the remaining question is which of the two
    measured vintages to fetch, and the answer is that the set is closed and two requests long.
    Fetching only SW2021 would leave `panel doctor --dataset index_classify --year 2014` reporting
    `partition_missing` for a vintage this repository has measured, dated and can read back.

    `--year` does not scope this and cannot: the response carries no date column at all, so
    `providers/tushare.py` dates every node at its vintage's effective day and the partition year
    is 2014 or 2021 whatever was asked for. `_UNPINNED_PARTITION_YEAR_TARGETS` is the exemption.
    Measured on 2026-08-11: SW2014 is 359 nodes (28 L1 / 104 L2 / 227 L3) filed under 2014, and
    SW2021 is 511 (31 / 134 / 346) filed under 2021.
    """
    written: list[PartitionRef] = []
    _echo_budget(
        INDUSTRY_TREE_DATASET,
        len(INDUSTRY_TAXONOMY_EFFECTIVE_FROM),
        "requests",
        "one per measured taxonomy vintage; the partition year is the vintage's, not --year",
    )
    for taxonomy in sorted(INDUSTRY_TAXONOMY_EFFECTIVE_FROM):
        batch = _fetch_panel(provider, INDUSTRY_TREE_DATASET, as_of=now, subjects=(taxonomy,))
        if batch.status != "success":
            raise _panel_fail(
                PanelExit.unhealthy,
                f"{INDUSTRY_TREE_DATASET} served no node for vintage {taxonomy}, whose effective "
                f"date is {INDUSTRY_TAXONOMY_EFFECTIVE_FROM[taxonomy].isoformat()}. An empty tree "
                "would read as a taxonomy with no industries in it",
            )
        written.append(write_industry_tree(store, batch))
    return written


def _stored_level_one_codes(store: PanelStore, *, now: datetime) -> tuple[str, ...]:
    """The `l1_code` slices `index_member_all` is fetched in, read off the stored tree.

    `_stored_calendar`'s shape one dataset over: the request needs something the panel already
    holds. See `_NEEDS_STORED_INDUSTRY_TREE` for why it is read rather than written down here,
    and why the vintage is `INDUSTRY_MEMBERSHIP_TAXONOMY` rather than the endpoint's own default.
    """
    vintage_year = INDUSTRY_TAXONOMY_EFFECTIVE_FROM[INDUSTRY_MEMBERSHIP_TAXONOMY].year
    try:
        trees = load_industry_trees(store, years=(vintage_year,), as_of=now, max_staleness=None)
    except (PanelStorageError, IndustryClassificationError) as error:
        raise _panel_fail(
            PanelExit.unhealthy,
            f"the {INDUSTRY_MEMBERSHIP_TAXONOMY} industry tree could not be read out of "
            f"{store.root}: {error}. {INDUSTRY_MEMBERSHIP_DATASET} is fetched one l1_code slice "
            "at a time and the tree is where those codes come from. Build it first: `openalpha "
            "panel build --dataset index_classify --year <year>`",
        ) from error
    tree = trees.get(INDUSTRY_MEMBERSHIP_TAXONOMY)
    if tree is None:
        raise _panel_fail(
            PanelExit.unhealthy,
            f"the {vintage_year} tree partition holds "
            f"{sorted(trees)} and not {INDUSTRY_MEMBERSHIP_TAXONOMY}, which is the one vintage "
            f"every {INDUSTRY_MEMBERSHIP_DATASET} row is labelled with",
        )
    return tuple(node.index_code for node in tree.nodes_at("L1"))


def _build_industry_memberships(
    store: PanelStore, provider: TushareProvider, *, codes: Sequence[str], now: datetime
) -> list[PartitionRef]:
    """Sweep every `(l1_code, is_new)` slice and write the corpus as one partition per event year.

    Two subjects per request and both mandatory. The `l1_code` slice is what keeps a response
    under this table's lowest cap (3,000 rows against a 7,893-row corpus); the `is_new` state is
    the refusal the dataset exists for -- a bare request returns the 5,889 **current** assignments
    with no flag and no short count to notice the 2,004 superseded ones by, which is a current
    snapshot indistinguishable from a complete history.

    So this loop asks for both states, and it refuses a sweep that came back with no superseded
    assignment at all. That is the one shape `write_industry_memberships` names as invisible to
    its own subject guard: a current-only corpus carries *every* security and reads as a market in
    which nobody has ever been reclassified. Measured on 2026-08-11, `801010.SI` alone answers
    126 current and 116 superseded, so a whole sweep with none of the latter is not a quiet market.

    `codes` is resolved by the caller, from the stored tree, for the reason `_build_panel` resolves
    the calendar before the targets that need it: a store with no `index_classify` in it is refused
    after zero round trips rather than after some of the 62.
    """
    states = (CURRENT_INDUSTRY_MEMBERSHIP, SUPERSEDED_INDUSTRY_MEMBERSHIP)
    total = len(codes) * len(states)
    _echo_budget(
        INDUSTRY_MEMBERSHIP_DATASET,
        total,
        "requests",
        f"{len(codes)} {INDUSTRY_MEMBERSHIP_TAXONOMY} l1_code slices x {len(states)} membership "
        "states; the partition years are the membership events', not --year",
    )
    batches: list[ColumnarPanelBatch] = []
    superseded = 0
    started = monotonic()
    stride = _progress_stride(total)
    done = 0
    for code in codes:
        for state in states:
            batch = _fetch_panel(
                provider, INDUSTRY_MEMBERSHIP_DATASET, as_of=now, subjects=(code, state)
            )
            done += 1
            if batch.status == "success":
                batches.append(batch)
                if state == SUPERSEDED_INDUSTRY_MEMBERSHIP:
                    superseded += batch.row_count
            if done % stride == 0 or done == total:
                _echo_progress(
                    (INDUSTRY_MEMBERSHIP_DATASET,),
                    done,
                    total,
                    started,
                    unit="industry-slices",
                )
    if not batches:
        raise _panel_fail(
            PanelExit.unhealthy,
            f"none of the {total} {INDUSTRY_MEMBERSHIP_DATASET} slices served a row; an empty "
            "corpus would read as a market in which nothing has ever been classified",
        )
    if not superseded:
        raise _panel_fail(
            PanelExit.unhealthy,
            f"this sweep fetched {len(codes)} l1_code slices in both membership states and every "
            f"is_new={SUPERSEDED_INDUSTRY_MEMBERSHIP!r} slice came back empty, so the corpus is "
            "the current snapshot alone. That is the one shape write_industry_memberships' "
            "subject guard cannot see -- it carries every security and no history, and reads as "
            "a market in which nobody has ever been reclassified",
        )
    return list(write_industry_memberships(store, batches))


def _refuse_shrinking_statement_years(
    store: PanelStore, *, dataset: str, batches: Sequence[ColumnarPanelBatch]
) -> None:
    """Refuse a `fina_indicator` write that would replace a stored year with fewer rows.

    ## The partition this stops from being destroyed

    `fina_indicator`'s request window filters `end_date` and its rows are filed by `ann_date`, so
    an announcement year is assembled from at least two report-period years: the annual of *A-1*
    plus the three interim reports of *A*. A partition is replaced whole. So a build over period
    years 2015..2026 writes announcement year 2016 with ~23,000 rows, and a later `--year 2016`
    on its own would write the same partition with only that period year's interims -- fewer
    rows, the *same* securities, and therefore nothing for
    `panel_ingest._refuse_to_drop_stored_subjects` to see. The rows that vanish are every annual
    report in that year, which is the one filing a value factor cannot do without.

    `PANEL_BUILD_SPAN_TARGETS` closes the half of this that happens inside one invocation, by
    accumulating every requested period year into one write. This closes the half that happens
    *across* invocations, which no amount of accumulation can.

    ## Why a row count, and what it does not claim

    It is not a claim that upstream never withdraws a row. It is a statement about this build:
    a write that shrinks a stored announcement year is one whose period-year span does not
    reproduce what that year already holds, and the remedy -- widen `--start`/`--end` -- is the
    caller's. If the publisher genuinely did withdraw filings, the refusal names the partition
    and clearing it is a deliberate act rather than a side effect of a narrower re-run.

    Scoped to `fina_indicator` because it is the only target whose partitions straddle its
    requests. The three announcement-year statement endpoints write exactly the year they were
    asked for from one request per security, so the only way to shrink one of those is
    `--subject`, which `_refuse_to_drop_stored_subjects` already refuses by name.
    """
    merged = merge_panel_batches(batches)
    shrinking: list[str] = []
    for year, yearly in split_panel_batch_by_year(merged):
        existing = store.read_coverage(dataset, year)
        if existing is not None and yearly.row_count < existing.row_count:
            shrinking.append(f"{year} holds {existing.row_count} and would get {yearly.row_count}")
    if shrinking:
        raise _panel_fail(
            PanelExit.unhealthy,
            f"this {dataset} build would replace stored announcement years with fewer rows than "
            f"they hold: {shrinking}. Its request window filters the report period and its rows "
            "are filed by announcement date, so an announcement year is assembled from at least "
            "two period years -- the annual of the year before plus the interims of the year "
            "itself -- and a narrower span cannot reproduce a wider one. Widen --start/--end to "
            "cover the period years that fed those partitions, or clear them deliberately if the "
            "publisher has withdrawn the filings",
        )


def _build_panel(
    store: PanelStore,
    provider: TushareProvider,
    *,
    written: dict[str, list[PartitionRef]],
    targets: frozenset[str],
    subjects: Sequence[str],
    year: int,
    exchange: str,
    halts: bool,
    now: datetime,
) -> tuple[tuple[date, ...], str]:
    """Run every requested **year-scoped** target in `PANEL_BUILD_TARGETS`' declared order.

    `written` is the caller's, keyed by target and filled as each partition lands, for two
    reasons that a returned value cannot serve. A build is a sequence of whole-partition writes
    with no transaction around them, so when a later target is refused the earlier ones are
    already on disk and the command has to be able to *name* them (`_stored_so_far`). And a
    target that this function accepted but wrote nothing for is only visible as an absent key
    (`_audit_written_partitions`) -- the failure a fourteenth entry in `PANEL_BUILD_TARGETS` with
    no branch below produces, which would otherwise be an exit 0 with an empty `partitions` list.

    The three `PANEL_BUILD_SPAN_TARGETS` are not run here: their unit of work is the whole
    invocation rather than one year, which `_build_span_targets` does once after this loop has
    finished. That set's docstring says why for each of them, and the reason is never
    convenience -- for `fina_indicator` a per-year loop is silently destructive.
    """
    sessions: tuple[date, ...] = ()
    calendar: TradingCalendar | None = None
    halt_state = "not-applicable"
    universe: tuple[str, ...] = ()

    if TRADING_CALENDAR_DATASET in targets:
        written.setdefault(TRADING_CALENDAR_DATASET, []).append(
            write_trading_calendar(
                store,
                _fetch_panel(
                    provider,
                    TRADING_CALENDAR_DATASET,
                    as_of=_year_as_of(year),
                    subjects=(exchange,),
                ),
            )
        )
    if STOCK_BASIC_DATASET in targets:
        # `--year` does not scope this one and cannot: `stock_basic` has no date filter, so one
        # request is the whole registry and `write_stock_universe` splits it into one partition
        # per lifecycle year. Recorded in the command's help, in
        # `_UNPINNED_PARTITION_YEAR_TARGETS` (which exempts it from the partition-year audit)
        # rather than papered over.
        written.setdefault(STOCK_BASIC_DATASET, []).extend(
            write_stock_universe(store, _fetch_panel(provider, STOCK_BASIC_DATASET, as_of=now))
        )
    if targets & _NEEDS_STORED_UNIVERSE:
        # Resolved here rather than at the top of this function, and the position is the whole
        # point: after the `stock_basic` branch, so `--dataset stock_basic --dataset income` in
        # one invocation reads the registry this build just wrote; and before every remaining
        # target, so a store with no registry costs one round trip rather than an hour of
        # `price`. `_NEEDS_STORED_CALENDAR` sits at the same seam one line down for the same
        # reason.
        universe = tuple(subjects) or _stored_universe(store, now=now)
    if targets & _NEEDS_STORED_CALENDAR:
        calendar = _stored_calendar(store, exchange=exchange, years=(year,), as_of=now)
        sessions = _build_sessions(calendar, year, now)
        _refuse_split_horizon(
            store,
            sessions=sessions,
            year=year,
            exchange=exchange,
            # What this invocation will replace, resolved through the target table rather than
            # from the flags: `--dataset price` names one target and rewrites three datasets.
            rewritten={name for target in targets for name in PANEL_BUILD_TARGETS[target]},
        )
    if ADJ_FACTOR_DATASET in targets:
        assert calendar is not None  # guaranteed by `_NEEDS_STORED_CALENDAR` above
        written.setdefault(ADJ_FACTOR_DATASET, []).append(
            write_adjustment_factors(
                store,
                _session_batches(provider, (ADJ_FACTOR_DATASET,), sessions)[ADJ_FACTOR_DATASET],
                calendar=calendar,
            )
        )
    if "price" in targets:
        assert calendar is not None  # guaranteed by `_NEEDS_STORED_CALENDAR` above
        halt_state = _build_price_panel(
            store,
            provider,
            written=written.setdefault("price", []),
            sessions=sessions,
            calendar=calendar,
            year=year,
            now=now,
            halts=halts,
        )
    if PRICE_LIMIT_DATASET in targets:
        assert calendar is not None  # guaranteed by `_NEEDS_STORED_CALENDAR` above
        written.setdefault(PRICE_LIMIT_DATASET, []).append(
            write_price_limits(
                store,
                _session_batches(provider, (PRICE_LIMIT_DATASET,), sessions)[PRICE_LIMIT_DATASET],
                calendar=calendar,
            )
        )
    if NAMECHANGE_DATASET in targets:
        written.setdefault(NAMECHANGE_DATASET, []).append(
            write_name_history(
                store,
                _fetch_panel(provider, NAMECHANGE_DATASET, as_of=_year_end_as_of(year, now)),
            )
        )
    if INDEX_WEIGHT_DATASET in targets:
        written.setdefault(INDEX_WEIGHT_DATASET, []).extend(
            _build_index_weights(store, provider, year=year, now=now)
        )
    if INDEX_DAILY_DATASET in targets:
        written.setdefault(INDEX_DAILY_DATASET, []).extend(
            _build_index_prices(store, provider, year=year, now=now)
        )
    for dataset in (INCOME_DATASET, BALANCE_SHEET_DATASET, CASH_FLOW_DATASET):
        if dataset not in targets:
            continue
        written.setdefault(dataset, []).extend(
            write_financial_statements(
                store,
                _build_statement_panel(
                    store,
                    provider,
                    dataset=dataset,
                    subjects=universe,
                    # The **end** of the announcement year: these three filter `ann_date`, so a
                    # window taken at 1 January fetches the right year and the point-in-time
                    # filter then drops every row in it. See `_year_end_as_of`.
                    as_of=_year_end_as_of(year, now),
                    label=f"{dataset} year={year}",
                    reason=(
                        f"one per security; ts_code is mandatory on {dataset} and there is no "
                        "cross-section fetch"
                    ),
                ),
            )
        )
    return sessions, halt_state


def _build_span_targets(
    store: PanelStore,
    provider: TushareProvider,
    *,
    written: dict[str, list[PartitionRef]],
    targets: frozenset[str],
    subjects: Sequence[str],
    years: Sequence[int],
    now: datetime,
) -> None:
    """Run the `PANEL_BUILD_SPAN_TARGETS` once for the whole invocation, in table order.

    `_build_panel`'s counterpart for the three targets a per-year loop cannot serve, and it takes
    `years` rather than a year for the one of them that uses them at all: `fina_indicator`'s
    period years are exactly the years the caller named, accumulated into a single write because
    an announcement-year partition is assembled from several of them.

    Runs after the year loop so `stock_basic` and `index_classify` -- both of which a span target
    reads out of the store -- can have been written by the same invocation. That is the whole
    reason for the phase order rather than an accident of it: `openalpha panel build --dataset
    index_classify --dataset index_member_all` on a fresh store has to work, and it does, because
    the tree lands before the sweep that reads its `l1_code` slices.
    """
    universe: tuple[str, ...] = ()
    if targets & _NEEDS_STORED_UNIVERSE:
        # Before the first request of this phase rather than beside the branch that uses it: a
        # store with no registry would otherwise be refused after `index_member_all`'s 62 round
        # trips, which is the cost `_build_panel` resolves its own universe early to avoid.
        universe = tuple(subjects) or _stored_universe(store, now=now)
    if INDUSTRY_TREE_DATASET in targets:
        written.setdefault(INDUSTRY_TREE_DATASET, []).extend(
            _build_industry_tree(store, provider, now=now)
        )
    if targets & _NEEDS_STORED_INDUSTRY_TREE:
        codes = _stored_level_one_codes(store, now=now)
        written.setdefault(INDUSTRY_MEMBERSHIP_DATASET, []).extend(
            _build_industry_memberships(store, provider, codes=codes, now=now)
        )
    if FINANCIAL_INDICATOR_DATASET in targets:
        batches: list[ColumnarPanelBatch] = []
        for period_year in years:
            batches.extend(
                _build_statement_panel(
                    store,
                    provider,
                    dataset=FINANCIAL_INDICATOR_DATASET,
                    subjects=universe,
                    # `now`, not a year-derived instant. This endpoint's window comes from the
                    # period year in `extra`, so `as_of` does only its own job -- bounding what
                    # was knowable -- which is the split `_financial_indicator_params` exists for.
                    as_of=now,
                    label=f"{FINANCIAL_INDICATOR_DATASET} period-year={period_year}",
                    reason=(
                        "one per security; the report-period year is a request subject and the "
                        "partitions are announcement years"
                    ),
                    extra=(str(period_year),),
                )
            )
        _refuse_shrinking_statement_years(
            store, dataset=FINANCIAL_INDICATOR_DATASET, batches=batches
        )
        written.setdefault(FINANCIAL_INDICATOR_DATASET, []).extend(
            write_financial_statements(store, batches)
        )


def _all_refs(written: Mapping[str, Sequence[PartitionRef]]) -> list[PartitionRef]:
    """Every partition in a `written` mapping, flattened, in the order the targets ran."""
    return [ref for group in written.values() for ref in group]


def _stored_so_far(refs: Sequence[PartitionRef]) -> str:
    """What is on disk at the moment a build was refused, as a sentence a reader can act on.

    An earlier version of `panel_build` said "nothing partial was stored" here, and that was
    false whenever more than one partition was in flight. `_build_panel` writes whole partitions
    one after another with no transaction around them, so `panel build --dataset trade_cal
    --dataset price` that is refused by `write_daily_panel` leaves `trade_cal` **and**
    `suspend_d` in the store. Only the writer that raised is all-or-nothing; the build is not,
    and a caller deciding whether to re-run or to clear the runtime directory needs the
    difference.

    Takes a flat sequence rather than the per-year `written` mapping because a build now spans
    years (`--start`/`--end`): the partitions on disk when the fourth year is refused include
    the three that finished, and a mapping keyed by target alone cannot hold them.
    """
    if not refs:
        return "No partition had been written when this build stopped."
    listed = ", ".join(f"{ref.dataset}:{ref.year}({ref.row_count} rows)" for ref in refs)
    return (
        f"{len(refs)} partition(s) were written before this build stopped and are still "
        f"stored: {listed}. A build is a sequence of whole-partition writes with no transaction "
        "around them, so an earlier target's partition survives a later target's refusal."
    )


def _audit_written_partitions(
    written: Mapping[str, Sequence[PartitionRef]],
    *,
    targets: frozenset[str],
    year: int | None,
) -> None:
    """Refuse a build that answered `ok` without having built what it was asked for.

    Two failures, both of which reach a caller as an exit 0 with a `partitions` list that does
    not say what it looks like it says, and neither of which any writer below can see -- the
    writers are told what to store, not what was requested.

    `year` is `None` for the span phase, where there is no `--year` a partition could be checked
    against: `_build_span_targets` runs once per invocation and every one of its targets is in
    `_UNPINNED_PARTITION_YEAR_TARGETS` anyway. Check 1 still runs, and has to -- a fourteenth
    entry in `PANEL_BUILD_TARGETS` with no branch is exactly as invisible in that phase as in the
    other, and adding a phase without extending this audit is the same defect wearing a new coat.

    1. **A requested target wrote no partition at all.** This is what a fourteenth entry in
       `PANEL_BUILD_TARGETS` with no matching branch produces: `_build_targets`
       accepts the name because the table has it, every `if` misses, and the command reports
       `exit 0` with `"partitions": []` -- the empty success this whole issue exists to make
       unavailable, at the one layer where nothing downstream can catch it. It is
       `internal_error` rather than `unhealthy` because no data produced it: the table and the
       branches are both this module's, and they have come apart.

    2. **A partition landed in a year other than the one asked for.** A partition's year comes
       from `panel_partition_year`, which reads the *rows' own dates*; `--year` only bounds the
       sessions that were fetched and the corpus that is read back. So a `suspend_d` fetch that
       serves rows dated last year is stored as last year's partition while
       `_build_price_panel` reads `load_suspensions(years=(year,))` -- the corpus a *previous*
       run left behind -- and the build reports `halts: corroborated` about a year it did not
       corroborate, plus a partition nobody asked for. `load_suspensions` cannot catch it: this
       command passes `max_staleness=None`, which is necessary and not a shortcut, because
       `suspend_d`'s freshness is measured in *event* time and the most recent halt event can
       legitimately be days before `as_of` -- but waiving it means a five-year-old corpus and
       this year's are the same observation to that call. The year is the check that survives.

       Four targets are exempt and only those four (`_UNPINNED_PARTITION_YEAR_TARGETS`), each
       because its partition year is a property of the rows rather than of the request:
       `stock_basic`'s are lifecycle years, `index_classify`'s are taxonomy vintages,
       `index_member_all`'s are membership-event years and `fina_indicator`'s are announcement
       years derived from a report-period request.
    """
    silent = sorted(name for name in targets if not written.get(name))
    if silent:
        raise _panel_fail(
            PanelExit.internal_error,
            f"{silent} is in PANEL_BUILD_TARGETS but produced no partition, so this build "
            "reported success without building it. That is a defect in this command -- a "
            "target the table accepts and no branch builds -- not a fact about the panel. "
            f"{_stored_so_far(_all_refs(written))}",
        )
    if year is None:
        return
    misfiled = [
        f"{ref.dataset}:{ref.year}"
        for name, group in written.items()
        if name not in _UNPINNED_PARTITION_YEAR_TARGETS
        for ref in group
        if ref.year != year
    ]
    if misfiled:
        raise _panel_fail(
            PanelExit.unhealthy,
            f"--year {year} was asked for and {misfiled} was written: a partition's year comes "
            "from the dates in the rows the provider served, not from this flag, so the fetch "
            "returned another year's data. Anything this build read back for --year "
            f"{year} came from an earlier run rather than from this fetch, and any halt corpus "
            "it reported as corroborated was not corroborated by it. "
            f"{_stored_so_far(_all_refs(written))}",
        )


def _build_years(years: Sequence[int], start: int | None, end: int | None) -> tuple[int, ...]:
    """Resolve `--year` / `--start` / `--end` into the years this build will run, ascending.

    ## Why `--year` is repeatable, and why the old shape was the dangerous kind of wrong

    It used to be `Annotated[int, ...]`, which Click resolves by keeping the **last** value and
    discarding the rest without a word: `panel build --dataset trade_cal --year 2025 --year
    2026` printed `WROTE trade_cal year=2026` and 2025 simply never happened. Silently dropping
    an argument a caller passed is indistinguishable from that caller never having passed it,
    so nothing downstream -- not the exit code, not `--json`, not `panel doctor` run on a year
    nobody built -- can tell the two apart. It was also the same flag name `panel doctor`
    already took as a `list[int]`, so one word meant two things one command apart.

    ## Why the range form exists as well

    The P1 gate is `panel build --start 2015 --end 2026`, and writing that as twelve `--year`
    flags is a different sentence from the one the roadmap asks for. Both forms resolve here so
    there is exactly one place that decides what "the years" are; passing both is refused
    rather than merged, because `--year 2019 --start 2015 --end 2026` has two readings and
    neither is obviously the intended one.

    Every refusal below is `bad_request`: no re-fetch fixes an argument list.
    """
    named = tuple(dict.fromkeys(years))
    ranged = start is not None or end is not None
    if named and ranged:
        raise _panel_fail(
            PanelExit.bad_request,
            f"--year {list(named)} and --start/--end were both given, and a build cannot be "
            "scoped two ways at once: --year names the years, --start/--end names a closed "
            "range. Pass one form",
        )
    if not named and not ranged:
        raise _panel_fail(
            PanelExit.bad_request,
            "no year was given: pass --year (repeatable) or a closed --start/--end range. "
            "Nothing is inferred -- a build that picked its own years would be a fetch nobody "
            "asked for, against a quota this command does not own",
        )
    if ranged:
        if start is None or end is None:
            raise _panel_fail(
                PanelExit.bad_request,
                f"--start and --end are a closed range and only "
                f"{'--start' if end is None else '--end'} was given; the open end has no "
                "defensible default, so name both",
            )
        if end < start:
            raise _panel_fail(
                PanelExit.bad_request,
                f"--start {start} is after --end {end}; a build runs oldest year first and "
                "this range is empty",
            )
        named = tuple(range(start, end + 1))
    for value in named:
        if not MINYEAR <= value <= MAXYEAR:
            raise _panel_fail(
                PanelExit.bad_request,
                f"{value} is not a year this calendar can represent ({MINYEAR}-{MAXYEAR})",
            )
    return tuple(sorted(named))


def _resumable_targets(
    store: PanelStore,
    *,
    targets: frozenset[str],
    year: int,
    exchange: str,
    now: datetime,
) -> tuple[frozenset[str], tuple[date, ...]]:
    """Which of `targets` this year already holds in full, and the sessions that proves it.

    ## What `--resume` is, and what it deliberately is not

    It is **year-granular** and it reads only what the store already recorded. A target is
    skipped for a year when every session-scoped dataset it writes already reaches the last
    session this build would fetch -- `_stored_horizon` against `_build_sessions`, which is
    `_refuse_split_horizon`'s own comparison turned around.

    The horizon rather than the whole session set, and that is not a weaker check: both
    write-time censuses (`panel_ingest._session_census`, used by the price panel and by
    `_refuse_missing_factor_sessions`) require **every** open session from 1 January to the day
    before the fetch, so a partition that exists at all is complete up to the horizon its build
    ran to, and the horizon is the only remaining degree of freedom. Comparing the full census
    instead would be wrong rather than stricter: `adj_factor` is stored as a *compressed* step
    function whose census holds only the load-bearing sessions, so an equality against the
    calendar would never match and `--resume` would silently never resume. So the evidence is
    the data the writers already accepted -- no progress file, no second on-disk format,
    nothing to go stale.

    It is **not** intra-year resumption, and that is a judgement rather than an omission. A
    `PanelStore` partition is written whole (there is no append) and `_session_census` requires
    every open session from 1 January, so a half-fetched year cannot be stored as a readable
    partition at all -- it would have to live in a second format with its own staleness and
    integrity questions, and the failure mode of getting that wrong is a partition that *looks*
    complete and is not, which is the one failure this whole module is built to make impossible.
    What is bought instead is that the unit of loss for `--start 2015 --end 2026` is one year
    rather than twelve; the bounded retry in `TushareProvider._post` is what keeps a transient
    socket error from costing even that.

    `trade_cal`, `stock_basic` and `namechange` are never skipped and are not evidence for
    skipping anything else. Each is a **single** request -- twelve of them across the whole gate
    range, against the ~2,900 a year of `price` costs -- so skipping them would save nothing
    measurable while making a resumed build read a calendar it did not verify. `suspend_d` is not
    evidence either, for `_EMPTY_SESSION_IS_ORDINARY`'s reason: a session on which nothing was
    halted serves zero rows, so its census is a fact about the market. A complete `daily`
    partition is the stronger witness anyway -- `write_daily_panel` refuses a session whose
    missing bars nothing accounts for, so that partition existing means the halt corpus was read.

    ## The second rule, and it is weaker on purpose rather than by oversight

    `_REGISTERED_PARTITION_RESUME` -- `index_weight` and the three announcement-year statement
    targets -- is skipped on a **registered partition** alone, because there is nothing stronger
    to read. Their datasets have no session census: a security that announced nothing in a year
    is absent from the partition and indistinguishable from one that was never fetched, so
    "which securities should be here" has no answer the store can give. What the rule buys is the
    only thing that matters at this scale -- `income` alone is 5,881 requests per year, so a
    twelve-year build that dies in the eleventh costs one year rather than eleven.

    What it cannot see is a partition an earlier `--subject` run narrowed: that partition is
    registered, so `--resume` skips it, and the year stays narrow. The residue is left visible
    rather than argued away -- `tests/integration/test_cli_panel.py` pins exactly that case, so
    it is a measured limitation and not a claim -- and it is bounded on the other side by
    `panel_ingest._refuse_to_drop_stored_subjects`, which refuses the narrowing write itself
    whenever a wider partition is already there. Re-running without `--resume` is the remedy and
    is always available.

    ## `PANEL_BUILD_SPAN_TARGETS` are never skipped, and `fina_indicator` cannot be

    `index_classify` is two requests and `index_member_all` is 62, so for both the answer is
    `trade_cal`'s. `fina_indicator` is 5,881 requests per period year and is the one target here
    that would genuinely benefit -- and it is *structurally* unresumable, not merely unimplemented:
    it writes nothing until every requested period year has been fetched, because an announcement
    year is assembled from several of them, so there is no intermediate state for a resume to
    read. The lever a caller has instead is a narrower `--start`/`--end`, at the cost
    `_refuse_shrinking_statement_years` states.
    """
    skippable = {name for name in targets if name in _NEEDS_STORED_CALENDAR}
    registered = {
        name
        for name in targets & _REGISTERED_PARTITION_RESUME
        if year in store.registered_years(name)
    }
    if not skippable:
        return frozenset(registered), ()
    if year not in store.registered_years(TRADING_CALENDAR_DATASET):
        return frozenset(registered), ()
    calendar = _stored_calendar(store, exchange=exchange, years=(year,), as_of=now)
    sessions = _build_sessions(calendar, year, now)
    reached = sessions[-1]
    resumed = {
        target
        for target in skippable
        if all(
            _stored_horizon(store, name, year) == reached
            for name in PANEL_BUILD_TARGETS[target]
            if name in SESSION_SCOPED_DATASETS
        )
    }
    return frozenset(resumed | registered), sessions


_BUILD_AS_OF_HELP = (
    "ISO-8601 instant this build's session horizon is derived from; defaults to the wall "
    "clock. The session loop runs to this instant's Asia/Shanghai date minus one day, so "
    "passing the same value to every target of one panel is what makes them stop on the same "
    "session -- see `_refuse_split_horizon`, which refuses the build rather than letting them "
    "diverge. Every run reports the instant it used, as `as_of` in --json and as the AS-OF "
    "line otherwise, so a later re-fetch of one target can be pinned to it. It pins what this "
    "build *stamps* -- fetched_at and every row's ingested_time, which is what makes a re-fetch "
    "of an unchanged year a true no-op -- and never what the provider's point-in-time filter "
    "judges rows against, which is always the wall clock."
)

_BUILD_DATASET_HELP = (
    "A build target, repeatable. The thirteen this command builds, in the order it runs them: "
    f"{', '.join(PANEL_BUILD_TARGETS)}. Anything else is refused by name. One target is one "
    "unit of work a panel_ingest writer accepts, which is not always one dataset: 'price' is "
    "daily + daily_basic + suspend_d, because write_daily_panel takes the pair together and its "
    f"halts argument has no default. Four of them do not write the --year they were given, "
    f"because their partition year comes from the rows rather than from the request "
    f"({', '.join(sorted(_UNPINNED_PARTITION_YEAR_TARGETS))}): the registry is split by "
    "lifecycle year, the industry tree by taxonomy vintage, the memberships by event year, and "
    "fina_indicator is asked for a report-period year and filed by announcement year. The last "
    f"three of those ({', '.join(sorted(PANEL_BUILD_SPAN_TARGETS))}) run once for the whole "
    "invocation rather than once per year."
)

_BUILD_SUBJECT_HELP = (
    "A ts_code the statement targets fetch, repeatable. Only income, balancesheet, cashflow and "
    "fina_indicator take one -- naming it for any other target is refused rather than ignored, "
    "because their partitions are the whole market and a partition is replaced whole. Without "
    "it the securities come from the stored stock_basic registry, which is 5,881 requests per "
    "dataset per year; with it, one per name. Nothing is inferred from --year: a security that "
    "had not listed yet can still have filings announced in a window (688981.SH answers the "
    "2015 window) and one delisted in 2002 can still have filings announced in 2024 "
    "(000003.SZ), both measured, so no lifecycle filter is applied."
)


_BUILD_YEAR_HELP = (
    "A partition year to build, repeatable and mutually exclusive with --start/--end. Years "
    "run oldest first. Until V2-P1-019 this was a single value, which Click resolved by "
    "keeping the last one and discarding the rest in silence."
)

_BUILD_START_HELP = (
    "First year of a closed range to build, oldest first. Requires --end and refuses --year."
)

_BUILD_END_HELP = "Last year of a closed range to build, inclusive. Requires --start."

_BUILD_RESUME_HELP = (
    "Skip a target for a year whose stored partitions already reach the last session this "
    "build would fetch, so an interrupted multi-year build costs one year rather than all of "
    "them. Year-granular and evidence-based -- the census the writers already validated, not a "
    "progress file. trade_cal, stock_basic and namechange are never skipped, and there is no "
    "intra-year resumption. index_weight and the three announcement-year statement targets are "
    "skipped on a registered partition alone, which is weaker: it cannot tell a whole-market "
    "year from one an earlier --subject run narrowed. fina_indicator cannot be resumed at all, "
    "because it writes nothing until every requested period year has been fetched. See "
    "`_resumable_targets`. Off by default: a rebuild that quietly fetched nothing would be the "
    "wrong default for a command whose ordinary job is to replace what is there."
)


@panel_app.command("build")
def panel_build(
    dataset: Annotated[list[str], typer.Option("--dataset", help=_BUILD_DATASET_HELP)],
    year: Annotated[list[int] | None, typer.Option("--year", help=_BUILD_YEAR_HELP)] = None,
    start: Annotated[int | None, typer.Option("--start", help=_BUILD_START_HELP)] = None,
    end: Annotated[int | None, typer.Option("--end", help=_BUILD_END_HELP)] = None,
    subject: Annotated[
        list[str] | None, typer.Option("--subject", help=_BUILD_SUBJECT_HELP)
    ] = None,
    runtime_dir: Annotated[Path | None, typer.Option("--runtime-dir")] = None,
    exchange: Annotated[
        str, typer.Option("--exchange", help="Which exchange's calendar to fetch and read.")
    ] = TRADING_CALENDAR_DEFAULT_EXCHANGE,
    halts: Annotated[
        bool,
        typer.Option(
            "--halts/--no-halts",
            help=(
                "Whether write_daily_panel is given the year's halt corpus. --no-halts passes "
                "halts=None, which switches off the guard that refuses a session whose missing "
                "bars nothing accounts for. A recorded waiver, never a default."
            ),
        ),
    ] = True,
    as_of: Annotated[str, typer.Option("--as-of", help=_BUILD_AS_OF_HELP)] = "",
    resume: Annotated[bool, typer.Option("--resume", help=_BUILD_RESUME_HELP)] = False,
    json_output: Annotated[
        bool, typer.Option("--json", help="Emit a machine-readable build report.")
    ] = False,
) -> None:
    """Fetch and store one or more years of the panel plane through the real writers.

    Nothing here bypasses a write-time guard, and that is the whole design: the batches this
    command assembles go into `write_trading_calendar`, `write_stock_universe`,
    `write_adjustment_factors`, `write_suspensions`, `write_daily_panel` and
    `write_price_limits` unchanged, so a year missing a session the calendar reports open, a
    cross section that arrived short, or two datasets contradicting each other about a close is
    refused there and reported here rather than being stored.

    The credential is never read by this command. `TushareProvider` resolves `TUSHARE_TOKEN`
    itself, inside its own constructor, so the token exists in this process only inside the
    provider and the request envelope it posts -- and a `ProviderFailure`'s message, which can
    carry it, is never printed or logged (see `_fetch_panel`).

    A refusal partway through names the partitions already on disk rather than claiming none
    are: the writers are each all-or-nothing, the build across them is not.

    **One clock per invocation, and `--as-of` is how a caller keeps it across several.** The
    session loop is bounded at the horizon this instant implies, and the five targets are five
    invocations, so a build that crosses local midnight otherwise lands a panel that no `as_of`
    can assess cleanly. The default stays the wall clock -- pinning is the deliberate act, not
    the ordinary one -- and `_refuse_split_horizon` is what stops the default from being a trap:
    a build whose horizon disagrees with a partition already stored is refused before it fetches
    anything, with the exact `--as-of` that would resolve it. `--as-of` pins what this command
    *stamps*, never what the provider's point-in-time filter judges rows against; see the
    comment at the `TushareProvider` construction below and `TushareProvider._stamp`.

    **Years run oldest first, one `_build_panel` each, and a refusal stops the whole run.**
    That order is what makes `--resume` mean anything -- the years before the refusal are the
    ones already on disk -- and it is why the refusal names both what landed and the exact
    command that carries on from there. There is no transaction across years any more than
    there is one across targets.

    **Two phases, and the second is not an optimisation.** The year loop runs the ten year-scoped
    targets; `_build_span_targets` then runs the three in `PANEL_BUILD_SPAN_TARGETS` once for the
    whole invocation, because their requests carry no year (`index_classify`, `index_member_all`)
    or carry one that is not the partition's (`fina_indicator`, whose announcement years are
    assembled from several report-period years and which a per-year loop would silently truncate).
    The span phase runs second so that `stock_basic` and `index_classify`, which it reads out of
    the store, can have been written by the same invocation.

    **What a whole-market build now costs.** The five original targets were ~2,900 requests for a
    year. `income`, `balancesheet` and `cashflow` are one request per security each -- 5,881 on
    2026-08-11 -- so a single year of the four statement endpoints is ~23,500 round trips, and
    `--start 2015 --end 2026` is ~282,000. At the 1.1--4.6s per request measured on that date the
    twelve-year statement backfill is days rather than hours, and the account's own 500-per-minute
    quota is not the binding constraint at that latency. Every fetch loop therefore states its
    size before it starts (`_echo_budget`) and reports progress with an `eta` while it runs, and
    `--subject` is the lever that turns the registry sweep into a named handful.
    """
    runtime_dir = _resolved_runtime_dir(runtime_dir)

    with _panel_command("panel build"):
        targets = _build_targets(dataset)
        subjects = _build_subjects(subject or (), targets)
        years = _build_years(year or (), start, end)
        year_targets = targets - PANEL_BUILD_SPAN_TARGETS
        span_targets = targets & PANEL_BUILD_SPAN_TARGETS
        store = _panel_store(runtime_dir)
        now = _panel_as_of(as_of)
        # **One instant for the whole invocation, and the provider gets it too.** The clock this
        # command bounds its session loop with (`_build_sessions`) and the clock the writers run
        # their session census against (`panel_ingest._session_census`, which reads
        # `batch.fetched_at`) are the same rule applied at two layers, and they were being given
        # two different readings of `datetime.now()`.
        #
        # "The same rule" was false for one row and `V2-P4-114` made it true again: `V2-P4-063`
        # moved this loop onto `panel_ingest._sessions_published_through` and left the census
        # subtracting a day unconditionally, which is that rule only below 16:30. Both now call
        # that one function, so the claim is carried by a shared name rather than by two spellings
        # agreeing -- which is what `_build_sessions`' own docstring says the sharing is for.
        #
        # Within one invocation the mismatched clocks are the
        # cross-midnight defect in miniature: a `price` build that starts at 23:34 fetches the
        # sessions up to yesterday and then, if it finishes after 00:39, hands the writer batches
        # stamped with today -- so the census requires a session the loop was never asked for and
        # refuses the partition the build correctly assembled. Passing the same resolved instant
        # to both closes that, and it is what makes `--as-of` mean anything at all: pinning the
        # loop while the census still moved would only relocate the disagreement.
        #
        # The cost is stated rather than hidden: `fetched_at` becomes the instant the build
        # *started* rather than the instant each request returned, understating it by up to the
        # build's duration (~45 minutes on a whole year). That is immaterial to every consumer of
        # the field -- the census, whose bound is a whole day, and provenance -- and the value is
        # reported as `as_of` in this command's own output rather than being left to be inferred.
        #
        # **`stamped_at`, not `clock`.** `V2-P1-018` passed the resolved instant as the
        # provider's `clock`, which pinned what the provider *stamps* -- the point above -- and
        # also, silently, what it *judges* rows against: `TushareProvider._decode_panel_rows`
        # keeps a row only if it was knowable both at the request's `as_of` and at the instant
        # the fetch ran, and the second half read the same clock. A `--as-of` ahead of the wall
        # clock therefore stored a cross section that had not published. The provider now takes
        # the two apart: `stamped_at` is pinned here, `clock` stays the wall clock this module
        # reads its own default from, and the caller can no longer raise the ceiling on what a
        # fetch may know. `_panel_clock` rather than the provider's own default so that a test
        # which moves this module's clock moves the provider's too.
        provider = TushareProvider(transport=_panel_transport(), clock=_panel_clock, stamped_at=now)
        stored: list[PartitionRef] = []
        builds: list[dict[str, object]] = []
        for index, one_year in enumerate(years):
            resumed, covered = (
                _resumable_targets(
                    store, targets=year_targets, year=one_year, exchange=exchange, now=now
                )
                if resume
                else (frozenset[str](), ())
            )
            fetched = year_targets - resumed
            written: dict[str, list[PartitionRef]] = {}
            sessions: tuple[date, ...] = covered
            halt_state = "resumed" if resumed and not fetched else "not-applicable"
            try:
                if fetched:
                    sessions, halt_state = _build_panel(
                        store,
                        provider,
                        written=written,
                        targets=fetched,
                        subjects=subjects,
                        year=one_year,
                        exchange=exchange,
                        halts=halts,
                        now=now,
                    )
                    sessions = sessions or covered
            except _PANEL_WRITE_REFUSALS as error:
                raise _panel_fail(
                    PanelExit.unhealthy,
                    f"the panel refused this build: {error}. "
                    f"{_stored_so_far([*stored, *_all_refs(written)])}"
                    f"{_years_left(years, index)}",
                ) from error
            except Exception:
                # Everything else that can stop a build midway: a provider failure or a stated
                # refusal raised as `typer.Exit` from inside the loop, or something
                # unanticipated that `_panel_command` will turn into `internal_error`. None of
                # them can know how far the build got, and by then partitions are on disk.
                # Re-raised untouched -- this clause adds the one fact the raiser did not have,
                # and decides nothing.
                typer.echo(
                    f"{_stored_so_far([*stored, *_all_refs(written)])}{_years_left(years, index)}",
                    err=True,
                )
                raise
            _audit_written_partitions(written, targets=fetched, year=one_year)
            landed = _all_refs(written)
            stored.extend(landed)
            builds.append(
                {
                    "year": one_year,
                    "halts": halt_state,
                    "resumed": sorted(resumed),
                    "sessions": {
                        "first": sessions[0].isoformat() if sessions else None,
                        "last": sessions[-1].isoformat() if sessions else None,
                        "count": len(sessions),
                    },
                    "partitions": [
                        {"dataset": ref.dataset, "year": ref.year, "row_count": ref.row_count}
                        for ref in landed
                    ],
                }
            )

        span_written: dict[str, list[PartitionRef]] = {}
        if span_targets:
            try:
                _build_span_targets(
                    store,
                    provider,
                    written=span_written,
                    targets=span_targets,
                    subjects=subjects,
                    years=years,
                    now=now,
                )
            except _PANEL_WRITE_REFUSALS as error:
                raise _panel_fail(
                    PanelExit.unhealthy,
                    f"the panel refused this build: {error}. "
                    f"{_stored_so_far([*stored, *_all_refs(span_written)])}",
                ) from error
            except Exception:
                # `_build_panel`'s clause, for the phase that has no year to carry on from: the
                # span targets are one unit of work across the whole invocation, so there is no
                # `--start` that resumes them and `_years_left` would name a range that means
                # nothing here.
                typer.echo(
                    _stored_so_far([*stored, *_all_refs(span_written)]),
                    err=True,
                )
                raise
            _audit_written_partitions(span_written, targets=span_targets, year=None)
            stored.extend(_all_refs(span_written))

        payload = {
            "years": list(years),
            "exchange": exchange,
            "targets": sorted(targets),
            "halts": _one_halt_state(builds),
            # What the span phase wrote, kept apart from `builds` because it belongs to no year:
            # `index_classify`'s partitions are taxonomy vintages, `index_member_all`'s are
            # membership-event years and `fina_indicator`'s are announcement years assembled from
            # every requested period year. Folding them into one year's entry would attribute
            # them to a year that did not produce them.
            "span": {
                "targets": sorted(span_targets),
                "partitions": [
                    {"dataset": ref.dataset, "year": ref.year, "row_count": ref.row_count}
                    for ref in _all_refs(span_written)
                ],
            },
            # The instant this build's horizon came from, reported whether it was passed or
            # defaulted. It is what a later `--dataset`-at-a-time re-fetch of this same panel
            # has to be pinned to, and a value a caller can only get by being told: `sessions`
            # below names the last session, not the instant that bounded the loop.
            "as_of": now.isoformat(),
            # The span across every year this invocation covered. Identical to the single
            # year's own entry when one year was asked for, which is every caller that existed
            # before `--start`/`--end`; `builds` is where a multi-year run is legible.
            "sessions": _session_span(builds),
            "builds": builds,
            "partitions": [
                {"dataset": ref.dataset, "year": ref.year, "row_count": ref.row_count}
                for ref in stored
            ],
        }
        if json_output:
            typer.echo(json.dumps(payload, ensure_ascii=False, sort_keys=True))
            return
        typer.echo(f"AS-OF {now.isoformat()}")
        for entry in builds:
            span = cast(Mapping[str, object], entry["sessions"])
            if span["count"]:
                typer.echo(
                    f"SESSIONS {entry['year']} {span['count']} from {span['first']} to "
                    f"{span['last']}"
                )
            for name in cast(Sequence[str], entry["resumed"]):
                typer.echo(f"RESUMED {name} year={entry['year']} ({_resume_evidence(name)})")
            for ref in cast(Sequence[Mapping[str, object]], entry["partitions"]):
                typer.echo(f"WROTE {ref['dataset']} year={ref['year']} rows={ref['row_count']}")
        for landed_ref in _all_refs(span_written):
            typer.echo(
                f"WROTE {landed_ref.dataset} year={landed_ref.year} "
                f"rows={landed_ref.row_count} (span)"
            )
        typer.echo(f"HALTS {_one_halt_state(builds)}")


def _resume_evidence(target: str) -> str:
    """Why `--resume` skipped this target, in the output rather than only in a docstring.

    The two rules are not equally strong (`_resumable_targets`), and a caller reading `RESUMED
    income year=2024` has no way to know which one applied. Saying so on the line is what makes
    the weaker one a disclosure instead of a silence: a partition an earlier `--subject` run
    narrowed is registered, and this is the only place the difference is visible at the moment it
    matters.
    """
    if target in _REGISTERED_PARTITION_RESUME:
        return "partition registered; this rule does not check which securities it holds"
    return "already complete"


def _years_left(years: Sequence[int], index: int) -> str:
    """The command that carries on from the year a multi-year build stopped in, or nothing.

    Printed beside `_stored_so_far` for the same reason that sentence exists: a build across
    twelve years that dies in the fourth has eight left, and "re-run it" is not a remedy when
    the first three cost hours. Empty for a single-year build, which has nothing to carry on
    from and where the extra sentence would be noise.

    The finished years are named only when there are some. `Years [] finished` is a true
    sentence and a bad one, and the range below already says everything a build that stopped in
    its first year needs -- which is the same range it was given.
    """
    if len(years) == 1:
        return ""
    finished = list(years[:index])
    stopped = f"Years {finished} finished; {years[index]}" if finished else f"{years[index]}"
    return (
        f" {stopped} is the one that stopped. Carry on with "
        f"--start {years[index]} --end {years[-1]} --resume."
    )


def _one_halt_state(builds: Sequence[Mapping[str, object]]) -> str:
    """The halt state every built year reported, or `mixed` when they disagree.

    One invocation carries one `--halts` flag, so the states agree unless `--resume` skipped a
    year (`resumed`) beside one that was fetched. Total rather than "the last one wins": a
    single word that silently described one year of twelve is exactly the reporting this
    command's own `_audit_written_partitions` exists to refuse elsewhere.
    """
    states = {str(entry["halts"]) for entry in builds}
    return states.pop() if len(states) == 1 else "mixed"


def _session_span(builds: Sequence[Mapping[str, object]]) -> dict[str, object]:
    """First session, last session and total count across every year of one invocation."""
    spans = [cast(Mapping[str, object], entry["sessions"]) for entry in builds]
    covered = [span for span in spans if span["count"]]
    return {
        "first": covered[0]["first"] if covered else None,
        "last": covered[-1]["last"] if covered else None,
        "count": sum(cast(int, span["count"]) for span in spans),
    }


# --- panel doctor and data-check ---------------------------------------------------------------

_DATASET_HELP = "A dataset to assess, repeatable. Nothing is inferred: this is the caller's own "
_DATASET_HELP += "statement of what should be there."
_YEAR_HELP = (
    "A partition year to assess, repeatable. Deliberately the caller's assertion of what should "
    "be present rather than a reading of what is -- passing the stored years would make "
    "partition_missing unreachable by construction."
)
_SESSION_HELP = (
    "An ISO-8601 session the day-level cross-checks run on, repeatable. Not inferred: 'check "
    "every session' is a whole-corpus scan and 'check the last one' is a guess."
)
_CALENDAR_HELP = (
    "Whether to read the exchange calendar out of the panel. --no-calendar states on the record "
    "that this run has none, which switches off every session-scoped cross-check; the gate then "
    "refuses a daily-cadence dataset nothing corroborated rather than clearing it."
)


@panel_app.command("doctor")
def panel_doctor_command(
    dataset: Annotated[list[str], typer.Option("--dataset", help=_DATASET_HELP)],
    year: Annotated[list[int], typer.Option("--year", help=_YEAR_HELP)],
    runtime_dir: Annotated[Path | None, typer.Option("--runtime-dir")] = None,
    session: Annotated[list[str] | None, typer.Option("--session", help=_SESSION_HELP)] = None,
    index_code: Annotated[list[str] | None, typer.Option("--index-code")] = None,
    exchange: Annotated[str, typer.Option("--exchange")] = TRADING_CALENDAR_DEFAULT_EXCHANGE,
    as_of: Annotated[
        str, typer.Option("--as-of", help="ISO-8601 point-in-time clock; defaults to now.")
    ] = "",
    calendar: Annotated[bool, typer.Option("--calendar/--no-calendar", help=_CALENDAR_HELP)] = True,
    json_output: Annotated[
        bool, typer.Option("--json", help="Emit the health report as data.")
    ] = False,
    limitation_detail: Annotated[
        bool,
        typer.Option(
            "--limitation-detail/--no-limitation-detail",
            help=(
                "Whether --json carries each known limitation's prose. --no-limitation-detail "
                "keeps the code, the datasets and the dates and drops the paragraph."
            ),
        ),
    ] = True,
) -> None:
    """Report what is wrong with the stored panel at a stated `as_of`.

    Distinct from the top-level `doctor`, which probes *provider* credentials and declared
    capabilities. This one reads the panel: per-dataset readiness and freshness, the
    cross-dataset checks, and the datasets' own structural limitations kept separate from this
    fetch's defects.

    Exits non-zero exactly when the report is not `is_clean` -- one or more `blocking` or
    `warning` findings. A `notice` never does; see `PanelExit` for the measurement behind that.

    **`--json` is mostly the limitation prose and `--no-limitation-detail` is how to decline it**
    (`V2-P4-110`). Measured on a generated panel asked about `index_daily`: 16,936 bytes out, of
    which 14,359 (84.8%) were the paragraphs and 1,340 were the findings. The paragraphs do not
    depend on the panel -- they are the same bytes on every run against every store -- so a
    caller polling this command was carrying them for nothing. The flag keeps each entry's
    `code`, `datasets` and `dates` and drops only the paragraph; the default is unchanged,
    because a registry served only on request is a registry that stops being read.
    """
    runtime_dir = _resolved_runtime_dir(runtime_dir)

    with _panel_command("panel doctor"):
        store, request = _panel_request(
            runtime_dir=runtime_dir,
            dataset=dataset,
            year=year,
            session=session,
            index_code=index_code,
            exchange=exchange,
            as_of=as_of,
            with_calendar=calendar,
        )
        try:
            report = panel_health_report(
                store,
                as_of=request.as_of,
                datasets=request.datasets,
                years=request.years,
                calendar=request.calendar,
                index_codes=request.index_codes,
                cross_section_days=request.sessions,
            )
        except PanelDoctorError as error:
            raise _panel_fail(PanelExit.bad_request, str(error)) from error

        if json_output:
            typer.echo(
                json.dumps(
                    health_report_payload(report, limitation_detail=limitation_detail),
                    ensure_ascii=False,
                    sort_keys=True,
                )
            )
        else:
            _echo_report(report)
        if not report.is_clean:
            raise typer.Exit(code=int(PanelExit.unhealthy))


@app.command("data-check")
def data_check(
    dataset: Annotated[list[str], typer.Option("--dataset", help=_DATASET_HELP)],
    year: Annotated[list[int], typer.Option("--year", help=_YEAR_HELP)],
    runtime_dir: Annotated[Path | None, typer.Option("--runtime-dir")] = None,
    session: Annotated[list[str] | None, typer.Option("--session", help=_SESSION_HELP)] = None,
    index_code: Annotated[list[str] | None, typer.Option("--index-code")] = None,
    exchange: Annotated[str, typer.Option("--exchange")] = TRADING_CALENDAR_DEFAULT_EXCHANGE,
    as_of: Annotated[
        str, typer.Option("--as-of", help="ISO-8601 point-in-time clock; defaults to now.")
    ] = "",
    calendar: Annotated[bool, typer.Option("--calendar/--no-calendar", help=_CALENDAR_HELP)] = True,
    json_output: Annotated[
        bool, typer.Option("--json", help="Emit the clearance as data.")
    ] = False,
) -> None:
    """Run the fail-closed dependency gate and exit non-zero when it refuses.

    The exit code is the deliverable. A command that ran the gate, was refused, and still
    exited 0 would be no gate at all in a CI job or a scheduled run -- which is the "empty
    success" `V2-P1-013` exists to make unavailable, reappearing one layer up.

    A clearance is a verdict rather than a collection, and this command treats it as one: it
    asks `is_blocked` and reads `cleared_or_none`, never `bool()`, `len()` or iteration, all
    three of which raise here **even when the request cleared**.
    """
    runtime_dir = _resolved_runtime_dir(runtime_dir)

    with _panel_command("data-check"):
        store, request = _panel_request(
            runtime_dir=runtime_dir,
            dataset=dataset,
            year=year,
            session=session,
            index_code=index_code,
            exchange=exchange,
            as_of=as_of,
            with_calendar=calendar,
        )
        try:
            clearance = require_datasets(store, request)
        except (PanelGateError, PanelDoctorError) as error:
            raise _panel_fail(PanelExit.bad_request, str(error)) from error

        if json_output:
            typer.echo(json.dumps(clearance_payload(clearance), ensure_ascii=False, sort_keys=True))
        else:
            _echo_clearance(clearance)
        if clearance.is_blocked:
            raise typer.Exit(code=int(PanelExit.unhealthy))


FACTOR_EXIT: Final[Mapping[str, PanelExit]] = MappingProxyType(
    {
        "answered": PanelExit.ok,
        "blocked": PanelExit.unhealthy,
        "panel_unreadable": PanelExit.unhealthy,
        "bad_request": PanelExit.bad_request,
        "conflict": PanelExit.unhealthy,
        "internal_error": PanelExit.internal_error,
    }
)
"""What `factor run` exits with for each `factor_view` fault, as one table.

`api/app.py`'s `FACTOR_HTTP_STATUS` is the sibling of this and `PanelExit` is the vocabulary --
this command reuses that enum rather than declaring a second one, because a CI job that already
switches on 1/3/4/5 for `panel doctor` and `data-check` must not have to learn a fourth meaning
for the same numbers on a fourth command. The rows say which existing meaning each fault has:

- **`blocked` -> 1 (`unhealthy`)** -- the stored tiers could not answer. The *panel* is at fault:
  the range holds no cross section, or the three tiers were not built at the same instants. A
  re-fetch (or a build) is the remedy, which is exactly what `unhealthy` means on the other three
  commands.
- **`panel_unreadable` -> 1** -- a partition this run needs is missing, damaged, stale or holds
  rows that were not knowable at the stated `as_of`. The same class of fact one step earlier, and
  the code the hand-written calendar loader `_panel_request` replaced already used.
- **`bad_request` -> 3** -- the request cannot be put: a factor no registry declares, a range that
  runs backwards, an `--as-of` before `--end`. No amount of building fixes it.
- **`conflict` -> 1** -- the document store refused a second, different answer under a held
  `experiment_id`. `unhealthy` rather than a code of its own, and the choice is argued rather than
  assumed: this is `refuse_a_restated_experiment` firing, which means *the numbers moved between
  two runs of one declaration* -- a statement about what is stored, with "find out why" as its
  remedy, which is the row `unhealthy` already is.
- **`internal_error` -> 5** -- `_panel_command`'s row, unchanged: a defect in the command rather
  than a verdict about anything.

**There is no row for `answered`-with-a-bad-verdict, and that is deliberate.** `factor run` exits
`0` for an experiment that assembled, *including* one whose grid says `removed` on every cell. A
`removed` verdict is the report succeeding at its job -- it is the finding `V2-P3-014` exists to
make visible -- and an exit code that treated it as a failure would make every honest three-tier
report look like a broken command, which is `PanelExit`'s own measured argument about `notice`
arriving on the factor plane. The verdicts are in the body, first-class and unmissable, and
`--json` puts them in `document.artifact.attributions`.
"""


def _factor_fail(error: FactorViewError) -> typer.Exit:
    """One `factor_view` fault, enveloped by the row of `FACTOR_EXIT` it names.

    Looked up by `error.reason` rather than switched on by exception type, `_panel_refusal`'s rule
    one channel over: a fault added to `factor_view.py` with no row here raises `KeyError`, which
    `_panel_command` turns into exit 5 -- "the command is incomplete" -- instead of a silently
    mis-enveloped refusal. `str(error)` rather than `error.disclosable`, because this channel is
    inside the process that owns the store: naming it tells the operator nothing they did not
    configure, and it is the actionable half of a missing-partition message.
    """
    return _panel_fail(FACTOR_EXIT[error.reason], str(error))


_FACTOR_HELP: Final[str] = (
    "The factor to run: a qualified key (`reversal_1d/v1`) or a factor_id (`fct_...`). The key is "
    "the form for a human; the id is what a stored partition carries, and both resolve. "
    "`openalpha factor list` prints every declared key."
)
_FACTOR_START_HELP: Final[str] = (
    "First prediction day of the closed range, ISO-8601. A prediction day is the day a stored "
    "cross section was computed at -- not a session the forward return is priced on."
)
_FACTOR_END_HELP: Final[str] = "Last prediction day of the closed range, inclusive, ISO-8601."
_FACTOR_AS_OF_HELP: Final[str] = (
    "ISO-8601 instant every panel read is made at, and the instant the experiment is evaluated "
    "at; defaults to now. Must be at or after --end, because a forward return is priced on "
    "sessions after its prediction day."
)
_FACTOR_TRANSFORM_HELP: Final[str] = (
    "Which stored processed tier to read, by qualified key (`cross_section_standard/v1`). It "
    "selects a partition rather than computing one -- `openalpha factor build --tier processed` "
    "is what writes it -- so a key this store never built is refused as an unreadable panel. "
    "`openalpha factor list` prints the declared ones with the floors they impose."
)
_FACTOR_NEUTRALIZATION_HELP: Final[str] = (
    "Which stored neutralised tier to read, by qualified key (`industry_and_size/v1`). This is "
    "the tier the acceptance criterion is decided on, so a range whose residuals were built at "
    "other instants is refused rather than reported as a row that measured nothing."
)
_FACTOR_HORIZON_HELP: Final[str] = (
    "The forward window each prediction day is scored over, e.g. `1d`, `5d`, `20d`. Sessions of "
    "the stored exchange calendar, not calendar days, so the panel must reach past --end."
)
_FACTOR_IC_METHOD_HELP: Final[str] = (
    "`spearman` (rank IC) or `pearson`. It decides both the information coefficient and the "
    "redundancy correlation, so the two cannot be computed under different definitions."
)
_FACTOR_MIN_SECURITIES_HELP: Final[str] = (
    "Fewest admitted names a cross section may have and still be scored. Below it the day is "
    "reported as thin rather than correlated, and no default is offered because the number moves "
    "every verdict. This one option feeds TWO studies with different floors and the "
    f"higher binds, so the floor on this option is {MINIMUM_REDUNDANCY_SECURITIES}: the "
    f"information coefficient's own floor is {MINIMUM_IC_SECURITIES}, but the redundancy study "
    f"needs {MINIMUM_REDUNDANCY_SECURITIES}, because at {MINIMUM_REDUNDANCY_SECURITIES - 1} an "
    "untied rank correlation can only be +-0.5 or +-1 and no --redundancy-threshold at or below "
    f"0.5 distinguishes anything. V2-P4-104: this said {MINIMUM_IC_SECURITIES} until it was run."
)
_FACTOR_MIN_AS_OFS_HELP: Final[str] = (
    "Fewest scored prediction days the range must hold before a mean IC exists. Below it the tier "
    "reports `insufficient_as_ofs` and every attribution cell reading it is `not_measured`."
)
_FACTOR_GROUP_COUNT_HELP: Final[str] = (
    "How many quantile groups the cross section is cut into. The long-short spread is the top "
    "group minus the bottom one, so this decides what `mean_spread` is a spread *of*."
)
_FACTOR_MIN_PER_GROUP_HELP: Final[str] = (
    "Fewest names a quantile group may hold. A group thinner than this makes the period unscored "
    "rather than scored on one name."
)
_FACTOR_POSITION_CAPITAL_HELP: Final[str] = (
    "Cash allocated to each position, in yuan, as a decimal string. Real money against the real "
    "A-share lot rule (200 shares on STAR, a multiple of 100 elsewhere), so it decides how much "
    "of a thin name is actually buyable -- a `float` is refused because money that round-trips "
    "through binary floating point does not add up."
)
_FACTOR_MIN_PERIODS_HELP: Final[str] = (
    "Fewest scored rebalance periods before a mean spread exists. The portfolio twin of "
    "--min-as-ofs, and separate from it because a day can be scored for IC and unscored for the "
    "portfolio."
)
_FACTOR_PARTICIPATION_CAP_HELP: Final[str] = (
    "The largest share of a session's own traded value one position may take, as a decimal "
    "fraction (`0.01` is one percent). It is what turns a paper spread into a capacity statement: "
    "above the cap the name is reported as untradeable at that size rather than filled."
)
_FACTOR_MIN_REBALANCES_HELP: Final[str] = (
    "Fewest rebalances before a turnover figure exists. One rebalance is a portfolio that never "
    "turned over, so a mean over it would be a number about nothing."
)
_FACTOR_REDUNDANCY_THRESHOLD_HELP: Final[str] = (
    "Absolute correlation at or above which two vectors are called redundant, in (0, 1]. On this "
    "command it decides the survival row -- how much of the raw ordering each derived tier still "
    "carries -- which corroborates the attribution grid from a second direction."
)
_FACTOR_RETENTION_FLOOR_HELP: Final[str] = (
    "The line a verdict is decided at, in (0, 1]. A step that keeps less than this share of a "
    "statistic is `removed`; at or above it (and up to 1) it is `survives`. **This is the number "
    "the acceptance criterion is read off**, on the processed->neutralized step. A floor of zero "
    "would call every non-negative retention `survives` and is refused."
)
_FACTOR_NOTE_HELP: Final[str] = (
    "Prose recorded on the sealed record and deliberately outside every content address, so "
    "writing about an experiment cannot change its identity."
)
_FACTOR_EXCHANGE_HELP: Final[str] = (
    "Which stored exchange calendar the sessions are counted on. It decides the label windows and "
    "the readiness dates, so a calendar this store never fetched is refused rather than "
    "substituted."
)


@factor_app.command("run")
def factor_run_command(
    factor: Annotated[str, typer.Option("--factor", help=_FACTOR_HELP)],
    start: Annotated[str, typer.Option("--start", help=_FACTOR_START_HELP)],
    end: Annotated[str, typer.Option("--end", help=_FACTOR_END_HELP)],
    transform: Annotated[str, typer.Option("--transform", help=_FACTOR_TRANSFORM_HELP)],
    neutralization: Annotated[
        str, typer.Option("--neutralization", help=_FACTOR_NEUTRALIZATION_HELP)
    ],
    horizon: Annotated[str, typer.Option("--horizon", help=_FACTOR_HORIZON_HELP)],
    ic_method: Annotated[str, typer.Option("--ic-method", help=_FACTOR_IC_METHOD_HELP)],
    min_securities: Annotated[
        int, typer.Option("--min-securities", help=_FACTOR_MIN_SECURITIES_HELP)
    ],
    min_as_ofs: Annotated[int, typer.Option("--min-as-ofs", help=_FACTOR_MIN_AS_OFS_HELP)],
    group_count: Annotated[int, typer.Option("--group-count", help=_FACTOR_GROUP_COUNT_HELP)],
    min_securities_per_group: Annotated[
        int, typer.Option("--min-securities-per-group", help=_FACTOR_MIN_PER_GROUP_HELP)
    ],
    position_capital: Annotated[
        str, typer.Option("--position-capital", help=_FACTOR_POSITION_CAPITAL_HELP)
    ],
    min_periods: Annotated[int, typer.Option("--min-periods", help=_FACTOR_MIN_PERIODS_HELP)],
    participation_cap: Annotated[
        str, typer.Option("--participation-cap", help=_FACTOR_PARTICIPATION_CAP_HELP)
    ],
    min_rebalances: Annotated[
        int, typer.Option("--min-rebalances", help=_FACTOR_MIN_REBALANCES_HELP)
    ],
    redundancy_threshold: Annotated[
        float, typer.Option("--redundancy-threshold", help=_FACTOR_REDUNDANCY_THRESHOLD_HELP)
    ],
    retention_floor: Annotated[
        float, typer.Option("--retention-floor", help=_FACTOR_RETENTION_FLOOR_HELP)
    ],
    runtime_dir: Annotated[
        Path | None, typer.Option("--runtime-dir", help=_RUNTIME_DIR_HELP)
    ] = None,
    exchange: Annotated[
        str, typer.Option("--exchange", help=_FACTOR_EXCHANGE_HELP)
    ] = TRADING_CALENDAR_DEFAULT_EXCHANGE,
    as_of: Annotated[str, typer.Option("--as-of", help=_FACTOR_AS_OF_HELP)] = "",
    code_commit: Annotated[
        str | None, typer.Option("--code-commit", help=_CODE_COMMIT_HELP)
    ] = None,
    note: Annotated[str, typer.Option("--note", help=_FACTOR_NOTE_HELP)] = "",
    json_output: Annotated[
        bool, typer.Option("--json", help="Emit the sealed experiment document as data.")
    ] = False,
) -> None:
    """Run one factor's three-tier experiment over a closed range of prediction days.

    Reads the stored raw, processed and neutralised tiers, labels the forward returns off the same
    panel, drives `V2-P3-005`..`008` on each tier and seals the result into one immutable,
    content-addressed record under `runtime-dir/experiments`.

    **The tiers have to exist first.** `openalpha factor build` is what puts them there; a store
    built only by `openalpha panel build` holds no factor partition and this command is refused by
    name against it. `openalpha factor list` is what says which `--factor`, `--transform` and
    `--neutralization` are legal.

    **Fifteen of these options have no default, and that is the contract rather than an
    oversight.** Each is a floor or a policy one of the four upstream studies refuses to choose
    for a caller -- `MINIMUM_IC_SECURITIES` is 3 because two points always correlate perfectly,
    `MINIMUM_REDUNDANCY_SECURITIES` is 4 because a threshold over three ranks decides nothing, and
    the retention floor is the line the acceptance criterion's verdict is decided at. A default
    here would be a decision nobody recorded making, on numbers that move every verdict this
    command prints. `V2-P3-019` gave each of them a `--help` line saying which: fourteen of the
    seventeen showed a bare `[required]` and nothing else, on a command whose own docstring said
    the numbers move every verdict.

    Exits 0 for an experiment that assembled, whatever its verdicts say; 1 when the stored tiers
    could not answer; 3 when the request could not be put. See `FACTOR_EXIT`, and
    `factor_view.everything_is_unmeasured` for the one exit-0 answer this command prints a warning
    beside.
    """
    runtime_dir = _resolved_runtime_dir(runtime_dir)

    with _panel_command("factor run"):
        instant = _panel_as_of(as_of)
        try:
            request = factor_request(
                factor=factor,
                transform=transform,
                neutralization=neutralization,
                start=_factor_day(start, flag="--start"),
                end=_factor_day(end, flag="--end"),
                as_of=instant,
                exchange=exchange,
                horizon=horizon,
                ic_method=cast(ICMethod, ic_method),
                min_securities=min_securities,
                min_as_ofs=min_as_ofs,
                group_count=group_count,
                min_securities_per_group=min_securities_per_group,
                position_capital=_factor_amount(position_capital, flag="--position-capital"),
                min_periods=min_periods,
                participation_cap=_factor_amount(participation_cap, flag="--participation-cap"),
                min_rebalances=min_rebalances,
                redundancy_threshold=redundancy_threshold,
                retention_floor=retention_floor,
                code_commit=_resolved_code_commit(code_commit),
            )
            record, write = run_factor_experiment(
                _panel_store(runtime_dir),
                request,
                built_at=_panel_clock(),
                experiments=FileExperimentStore(runtime_dir / "experiments"),
                note=(
                    None
                    if not note.strip()
                    else FactorNote(subject=request.definition.qualified_key, summary=note)
                ),
            )
        except FactorViewError as error:
            raise _factor_fail(error) from error
        except (ExperimentStoreError, FactorError) as error:
            raise _panel_fail(PanelExit.unhealthy, str(error)) from error

        if json_output:
            typer.echo(
                json.dumps(experiment_view(record, write=write), ensure_ascii=False, sort_keys=True)
            )
            _warn_if_nothing_was_measured(record)
        else:
            _echo_experiment(record, write=write)


UNMEASURED_WARNING: Final[str] = (
    "WARNING every one of the six attribution cells is `not_measured`: this experiment assembled "
    "and measured nothing. Two of the three tiers carry no statistic, so no verdict was reached "
    "about anything -- reading the absence of a `removed` cell here as `the factor survived "
    "neutralisation` is the one wrong conclusion this grid makes easy. Each tier's own coverage "
    "code (above, and in --json under document.artifact.tiers[].ic.coverage) says why."
)
"""The line an all-`not_measured` grid is never printed without.

`FACTOR_EXIT` argues at length that exit `0` covers an experiment whose grid says `removed` on
every cell, because that is a finding. It said nothing about the grid that says `not_measured` on
every cell, which also exits `0`, also answers `200`, and is the opposite -- no finding at all --
while looking to a reader (or to a CI step grepping for `removed`) exactly like a clean pass. This
is the sentence that was missing, and it is a warning rather than a fourth exit code for
`factor_view.everything_is_unmeasured`'s stated reasons.

**On stderr in both modes**, which is `_panel_fail`'s rule and its reason: `--json` output has to
stay parseable on stdout, and a warning interleaved into the sealed envelope would corrupt exactly
the callers most likely to automate on it.
"""


def _warn_if_nothing_was_measured(record: FactorExperimentRecord) -> None:
    """Print `UNMEASURED_WARNING` on stderr when the grid measured nothing at all."""
    if everything_is_unmeasured(record):
        typer.echo(UNMEASURED_WARNING, err=True)


def _factor_day(value: str, *, flag: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as error:
        raise _panel_fail(
            PanelExit.bad_request,
            f"{flag} expects an ISO-8601 date (YYYY-MM-DD); got {value!r}",
        ) from error


def _factor_amount(value: str, *, flag: str) -> Decimal:
    """One money-or-fraction option as a `Decimal`, refusing anything a `Decimal` cannot hold.

    A string option converted here rather than a `float` option converted later, because both of
    these reach contracts that are `Decimal` on purpose -- `position_capital` is money and
    `participation_cap` is a fraction of a day's traded value that money is compared against, and
    a value that round-trips through a binary float is a budget that does not add up.
    """
    try:
        return Decimal(value)
    except ArithmeticError as error:
        raise _panel_fail(
            PanelExit.bad_request,
            f"{flag} expects a decimal number; got {value!r}",
        ) from error


ACCEPTANCE_MARKER: Final[str] = "  <- the acceptance criterion is read off this row"
"""What marks the two grid rows that carry the finding, on the one face a human reads.

The grid is six rows of four columns and `factor_experiment.py` says in prose which step the
roadmap's annotation is about -- `processed -> neutralized`, "a statistic that vanishes here was
the exposure, and no transform setting recovers it". A terminal that printed six identical-looking
rows left the reader to know that, and nothing in `docs/`, `README*` or `web/` said it: the six
verdict words themselves had zero occurrences outside the source. `factor_view.ACCEPTANCE_STEP` is
the declaration and this is the mark; `openalpha factor list` prints what each verdict means.
"""


def _echo_experiment(record: FactorExperimentRecord, *, write: str) -> None:
    """Print one sealed experiment: its identity, its three rows, its six cells and the answer.

    Both content addresses, because a reader has to be able to tell "the same experiment, run
    again" (`experiment_id` held, `content_digest` held) from "the same declaration, different
    numbers" -- the second is refused by the store and the first is a no-op, and the two addresses
    are what says which happened.

    The grid is printed whole and in `ATTRIBUTION_CELL_ORDER`, so a `not_measured` cell occupies
    its row rather than vanishing: a grid missing a cell and one whose cell has no number are two
    different claims.

    **Two things `V2-P3-019` added, both because a correct grid was being read wrongly.** The rows
    of `ACCEPTANCE_STEP` are marked, because six equal-looking rows do not say which one is the
    answer; and an all-`not_measured` grid gets `UNMEASURED_WARNING` on stderr, because exit `0`
    plus no `removed` cell reads as a pass and is not one.
    """
    typer.echo(f"experiment {record.experiment_id} content {record.content_digest} ({write})")
    typer.echo(f"factor     {record.artifact.spec.definition.qualified_key}")
    typer.echo(f"as_ofs     {len(record.artifact.tiers[0].as_ofs)}")
    typer.echo("tier            ic_coverage           mean_ic  mean_spread")
    for tier, coverage, mean_ic, mean_spread in tier_rows(record):
        typer.echo(f"{tier:<15} {coverage:<20} {mean_ic:>8}  {mean_spread}")
    marked = f"{ACCEPTANCE_STEP[0]}->{ACCEPTANCE_STEP[1]}"
    typer.echo("step                     statistic     retention  verdict")
    for step, statistic, retention, verdict in attribution_rows(record):
        marker = ACCEPTANCE_MARKER if step == marked else ""
        typer.echo(f"{step:<24} {statistic:<13} {retention:>9}  {verdict}{marker}")
    answer = ", ".join(f"{statistic}={verdict}" for statistic, verdict in acceptance_rows(record))
    typer.echo(f"answer     {marked} {answer}")
    typer.echo("verdicts   `openalpha factor list` prints what each of the six verdicts means")
    _warn_if_nothing_was_measured(record)


# --- what this build declares, and how to put it in a store (V2-P3-019) -------------------------


NOTE_WRAP_WIDTH: Final[int] = 96
"""How wide `factor describe` wraps a note, in characters.

The twenty shipped notes run from 705 to 4,830 characters and are written as single paragraphs,
so a terminal that printed them unwrapped would emit one line per note. 96 leaves room inside a
100-column line for the two-space indent that marks the prose apart from the fields above it.
"""


@factor_app.command("list")
def factor_list_command(
    json_output: Annotated[
        bool, typer.Option("--json", help="Emit the whole catalog, notes included, as data.")
    ] = False,
) -> None:
    """Print every factor, transform and neutralisation this build declares, and how to read a run.

    **The command that had to exist before any of the others could be used.** `factor run` takes
    `--factor`, `--transform` and `--neutralization`, and until this command there was no face, no
    route and no document that listed a legal value for any of the three: the only way to discover
    one was to mistype it, and the resulting refusal answered with nineteen `fct_` content
    addresses -- the one spelling of the identity a human never types.

    It also carries the two tables a `factor run` answer cannot be read without and which appeared
    nowhere in `docs/`, `README*` or `web/`: what each of the six verdicts means, and which of the
    six grid cells the acceptance criterion is decided on.

    Reads no store and takes no `--runtime-dir`: a declaration is a property of the build, so this
    answers the same on an empty machine. `--json` is `factor_view.factor_catalog()` verbatim,
    which is byte-for-byte what `GET /api/v1/factors` serves and what
    `OpenAlphaSDK.factor_catalog()` returns.
    """
    with _panel_command("factor list"):
        catalog = factor_catalog()
        if json_output:
            typer.echo(json.dumps(catalog, ensure_ascii=False, sort_keys=True))
            return
        _echo_catalog(catalog)


def _echo_catalog(catalog: Mapping[str, object]) -> None:
    """Render the catalog for a terminal: two lines per declaration, then the two tables.

    Two lines rather than one wide row, because the `decides` column runs to ninety characters on
    a value factor and a table that wrapped mid-field is harder to read than one that does not try.
    The note is a **size** here and the prose is `factor describe`'s; see `catalog_rows`.
    """
    counts = {
        kind: len([row for row in catalog_rows(catalog) if row[0] == kind])
        for kind in ("factor", "transform", "neutralization")
    }
    typer.echo(
        f"declared   {counts['factor']} factors, {counts['transform']} transforms, "
        f"{counts['neutralization']} neutralizations   (schema {catalog['schema_version']})"
    )
    typer.echo("kind            handle                                note")
    for kind, handle, decides, note in catalog_rows(catalog):
        typer.echo(f"{kind:<15} {handle:<37} {note}")
        typer.echo(f"                {decides}")
    typer.echo("")
    typer.echo("verdict       what `factor run` puts in the grid's last column")
    verdicts = catalog["verdicts"]
    assert isinstance(verdicts, list)
    for verdict in verdicts:
        typer.echo(f"{verdict['code']:<13} {verdict['meaning']}")
    typer.echo("")
    cells = catalog["attribution_cells"]
    assert isinstance(cells, list)
    acceptance = sorted(
        {str(cell["step"]) for cell in cells if cell["decides_the_acceptance_criterion"]}
    )
    typer.echo(f"acceptance  the criterion is read off {', '.join(acceptance)}")
    typer.echo(
        "next        `openalpha factor describe --factor <handle>` for one whole declaration"
    )
    typer.echo("            `openalpha factor build --factor <handle> --tier raw ...` to store it")


@factor_app.command("describe")
def factor_describe_command(
    factor: Annotated[
        str,
        typer.Option(
            "--factor",
            help="A factor's qualified key (`reversal_1d/v1`) or its `fct_` content address.",
        ),
    ] = "",
    transform: Annotated[
        str,
        typer.Option(
            "--transform", help="A transform's qualified key, e.g. `cross_section_standard/v1`."
        ),
    ] = "",
    neutralization: Annotated[
        str,
        typer.Option(
            "--neutralization",
            help="A neutralisation's qualified key, e.g. `industry_and_size/v1`.",
        ),
    ] = "",
    json_output: Annotated[
        bool, typer.Option("--json", help="Emit the declaration and its note as data.")
    ] = False,
) -> None:
    """Print one declaration whole, with the prose that says what it does *not* measure.

    **The prose is the deliverable.** Every shipped contract carries a note over 100 characters
    (`tests/unit/test_factor_engine_rules.py::test_every_shipped_contract_carries_its_prose`), and
    the factor notes are unusually candid -- `return_vol_60`'s says in full that it
    occupies `V2-P3-013`'s residual-volatility slot, is deliberately **not** named for a residual,
    and that neither residual is computable in this build. None of that was on any face: nineteen
    such disclosures existed in the source and reached no operator.

    Exactly one of the three options, because they name three registries rather than three
    spellings of one; a describe that guessed would answer about whichever it searched first.

    Reads no store, for `factor list`'s reason.
    """
    with _panel_command("factor describe"):
        try:
            entry = factor_entry(
                factor=factor or None,
                transform=transform or None,
                neutralization=neutralization or None,
            )
        except FactorViewError as error:
            raise _factor_fail(error) from error
        if json_output:
            typer.echo(json.dumps(entry, ensure_ascii=False, sort_keys=True))
            return
        _echo_declaration(entry)


def _echo_declaration(entry: Mapping[str, object]) -> None:
    """Render one catalog entry for a terminal: three fields, the declaration, the prose.

    The declaration is printed as **indented JSON of the whole `declaration` mapping** rather than
    as a hand-picked list of fields, and that is the same argument `experiment_view` makes about
    shipping the sealed document whole: a hand-written projection is a second rendering nothing
    holds, and a field dropped from it would be invisible to every check here. Printing the mapping
    means `tests/integration/test_factor_catalog.py::
    test_the_terminal_declaration_parses_back_to_the_declaration_the_data_face_serves` can parse
    the output and compare it for equality, so every key is asserted at once and by construction.
    """
    typer.echo(f"kind        {entry['kind']}")
    typer.echo(f"handle      {entry['handle']}")
    typer.echo(f"identity    {entry['identity']}")
    typer.echo("declaration")
    typer.echo(
        textwrap.indent(
            json.dumps(entry["declaration"], ensure_ascii=False, indent=2, sort_keys=True), "  "
        )
    )
    note = entry["note"]
    typer.echo("note")
    if note is None:
        typer.echo("  (this registry carries no prose about this contract)")
        return
    typer.echo(textwrap.indent(textwrap.fill(str(note), width=NOTE_WRAP_WIDTH), "  "))


_BUILD_TIER_HELP: Final[str] = (
    "The highest tier to store: `raw`, `processed` or `neutralized`. Every tier below it is stored "
    "too, so `--tier neutralized` writes all three. `--transform` is required for the last two and "
    "`--neutralization` for the last; naming one the tier does not use is refused rather than "
    "ignored. `--tier neutralized` succeeds at a prediction instant at or after that day's own "
    "close, on a day the exchange was open -- one session wide, and arithmetic rather than "
    "policy: the residual must carry the processed panel's own instant and both foreign reads are "
    "taken for the day that instant falls on. V2-P4-103: this option stated a far wider bound "
    "on the panel's own horizon until V2-P4-028 retracted it and left only this line behind, "
    "contradicting the paragraph above it in the same --help."
)
_BUILD_FACTOR_AS_OF_HELP: Final[str] = (
    "A prediction instant to compute a cross section at, ISO-8601 with an offset, repeatable. An "
    "instant rather than a date, because every one of a stored observation's four panel clocks is "
    "stamped with it and `factor run` groups its sample by it; `factor run --start/--end` then "
    "selects these by their Asia/Shanghai date. A later invocation may ADD an instant to a "
    "partition year it already holds (`V2-P4-071`): the write carries the stored builds forward, "
    "so nothing has to be recomputed and nothing is erased. What is still refused is a *second "
    "answer to one question* -- the same tier's policy at an as_of the year already holds, under "
    "a different declaration -- and that names what it replaces with --supersedes-<tier>."
)
_BUILD_FACTOR_YEAR_HELP: Final[str] = (
    "A partition year every read of this build is scoped to, repeatable, the same vocabulary "
    "`openalpha panel build --year` writes with. Named `--year` and not `--start/--end` because "
    "`factor run --start/--end` are prediction DAYS and these are partition YEARS. A session "
    "factor with a 125-session lookback at the start of a year needs the year before it too; and "
    "the statement partitions are keyed by ANNOUNCEMENT year, so five report periods usually need "
    "two of them. The registry is the exception and does not have to be counted: its partitions "
    "are keyed by LIFECYCLE year, so one year's partition is that year's listings rather than "
    "that year's market, and load_stock_universe reads every lifecycle year the store holds "
    "beneath this range on its own. Before V2-P4-059 it did not, and --year 2026 over a "
    "5,545-security store scored eleven names and exited 0."
)
_BUILD_STALENESS_HELP: Final[str] = (
    "How many days old the newest row of a read partition may be. Every panel_ingest requirement "
    "builder refuses to default this, so this command makes you state it or waive it with "
    "--waive-max-staleness rather than defaulting one -- a defaulted bound is silence about all "
    "six datasets at once. State it: V2-P4-100 measured that the waiver reaches an exit 1 on "
    "every tier of this command and never a looser read, so on this face the two options are not "
    "two."
)
_BUILD_WAIVE_STALENESS_HELP: Final[str] = (
    "Read with no freshness bound at all, on the record -- and measured NOT to reach a build. "
    "compute_factor reads through read_visible_at, which answers with the rows knowable at as_of "
    "rather than with the partition, so a waived bound would accept a slice reaching arbitrarily "
    "far short of as_of while every structural check cleared; the engine refuses it by name "
    "('State a bound') for every dataset the factor reads, and V2-P4-100 measured that on both "
    "the raw and the processed tier. The flag stays because the request contract has to be able "
    "to carry a waiver -- factor_view.factor_build_request refuses neither-nor-both without it, "
    "and the refusal a caller then meets names the rule rather than defaulting it away."
)
_BUILD_SUBJECT_FACTOR_HELP: Final[str] = (
    "A ts_code to evaluate, repeatable. Without it the subjects are every code the stored registry "
    "knows -- the whole membership rather than the day's listed cross section, so a delisted name "
    "is evaluated and coded `not_in_universe` instead of quietly vanishing from the census."
)
_BUILD_SUPERSEDES_HELP: Final[str] = (
    "A stored {tier} manifest_id this build deliberately replaces, repeatable. It does two "
    "things: a rebuild under a different --code-commit at an as_of the year already holds has to "
    "name what it supersedes, because two answers to one cross-section question may not sit side "
    "by side; and naming a build this call does NOT re-answer removes it, which is how a bad one "
    "leaves a year. Adding a new instant needs neither -- see --as-of. Three separate options "
    "because the three tiers keep three different manifest partitions, and each writer refuses a "
    "name no partition it touches holds."
)


@factor_app.command("build")
def factor_build_command(
    factor: Annotated[str, typer.Option("--factor", help=_FACTOR_HELP)],
    tier: Annotated[str, typer.Option("--tier", help=_BUILD_TIER_HELP)],
    as_of: Annotated[list[str], typer.Option("--as-of", help=_BUILD_FACTOR_AS_OF_HELP)],
    year: Annotated[list[int], typer.Option("--year", help=_BUILD_FACTOR_YEAR_HELP)],
    transform: Annotated[str, typer.Option("--transform", help=_FACTOR_TRANSFORM_HELP)] = "",
    neutralization: Annotated[
        str, typer.Option("--neutralization", help=_FACTOR_NEUTRALIZATION_HELP)
    ] = "",
    runtime_dir: Annotated[
        Path | None, typer.Option("--runtime-dir", help=_RUNTIME_DIR_HELP)
    ] = None,
    exchange: Annotated[
        str, typer.Option("--exchange", help=_FACTOR_EXCHANGE_HELP)
    ] = TRADING_CALENDAR_DEFAULT_EXCHANGE,
    max_staleness_days: Annotated[
        int | None, typer.Option("--max-staleness-days", help=_BUILD_STALENESS_HELP)
    ] = None,
    waive_max_staleness: Annotated[
        bool, typer.Option("--waive-max-staleness", help=_BUILD_WAIVE_STALENESS_HELP)
    ] = False,
    subject: Annotated[
        list[str] | None, typer.Option("--subject", help=_BUILD_SUBJECT_FACTOR_HELP)
    ] = None,
    supersedes_raw: Annotated[
        list[str] | None,
        typer.Option("--supersedes-raw", help=_BUILD_SUPERSEDES_HELP.format(tier="raw")),
    ] = None,
    supersedes_processed: Annotated[
        list[str] | None,
        typer.Option(
            "--supersedes-processed", help=_BUILD_SUPERSEDES_HELP.format(tier="processed")
        ),
    ] = None,
    supersedes_neutralized: Annotated[
        list[str] | None,
        typer.Option(
            "--supersedes-neutralized", help=_BUILD_SUPERSEDES_HELP.format(tier="neutralized")
        ),
    ] = None,
    code_commit: Annotated[
        str | None, typer.Option("--code-commit", help=_CODE_COMMIT_HELP)
    ] = None,
    json_output: Annotated[
        bool, typer.Option("--json", help="Emit the build report as data.")
    ] = False,
) -> None:
    """Compute one factor's stored tiers at the named instants and write them into the panel.

    **The command that makes `factor run` reachable.** A store built by `openalpha panel build`
    holds prices, filings, a registry, a calendar and an industry tree, and no factor partition at
    all; `openalpha panel build --dataset factor_obs_...` refuses, because a factor observation is
    derived rather than fetched and is no build target of that command. This is where it is
    derived. Nothing here bypasses a guard: `compute_factor`, `apply_factor_transform` and
    `apply_factor_neutralization` produce every number and the three `write_*_factor_panels`
    functions run every write-time check.

    The usual first invocation, against a panel `openalpha panel build --year 2026` wrote::

        openalpha factor build --factor reversal_1d/v1 --tier processed \\
          --transform cross_section_standard/v1 \\
          --as-of 2026-01-08T09:00:00+00:00 --as-of 2026-01-09T09:00:00+00:00 \\
          --year 2026 --max-staleness-days 30 --runtime-dir ./runtime

    and then `openalpha factor run --factor reversal_1d/v1 --start 2026-01-08 --end 2026-01-09 ...`
    reads what it stored.

    **That example said `--waive-max-staleness` until `V2-P4-100` ran it.** It exits `1`:
    `compute_factor` refuses a waived `max_staleness` for every dataset a factor reads, because
    it reads through `read_visible_at` and a waived bound accepts a slice reaching arbitrarily
    far short of `as_of` while every structural check clears. `V2-P4-094` found the model face's
    printed examples failing the same way; a `--help` example that has not been run is a claim
    like any other.

    **The third tier is the one that may refuse, and it refuses by name.** A residual has to carry
    the processed panel's own instant, and both foreign reads are taken for the day that instant
    falls on -- so it can only be computed at a prediction instant at or after that day's own
    close, on a day the exchange was open. Neither dataset states a whole-partition bound any
    more: `V2-P4-026` gave `daily_basic` an as-of-sensitive session-level read, and `V2-P4-028`
    put `index_member_all` on a day-scoped one, which is what took "at or after the last stored
    *assignment* of every membership year the read touches" out of this paragraph. The
    refusal says that, names the remedies, and **writes nothing**: a build that stored two tiers
    and gave up on the third would leave the exact store shape that makes `factor run` refuse one
    command later, about a different thing. See
    `the_builder_cannot_produce_a_residual_for_a_session_that_has_not_closed`, which
    `openalpha factor list --json` also serves.

    Exits 0 when everything asked for was stored; 1 when the panel could not answer; 3 when the
    request could not be put. `FACTOR_EXIT`'s rows, unchanged.
    """
    runtime_dir = _resolved_runtime_dir(runtime_dir)

    with _panel_command("factor build"):
        try:
            request = factor_build_request(
                factor=factor,
                tier=tier,
                transform=transform,
                neutralization=neutralization,
                as_ofs=[_factor_instant(value) for value in as_of],
                years=year,
                exchange=exchange,
                max_staleness_days=max_staleness_days,
                waive_max_staleness=waive_max_staleness,
                subjects=subject or [],
                supersedes_raw=supersedes_raw or [],
                supersedes_processed=supersedes_processed or [],
                supersedes_neutralized=supersedes_neutralized or [],
                code_commit=_resolved_code_commit(code_commit),
            )
            report = build_factor_panels(
                _panel_store(runtime_dir), request, built_at=_panel_clock()
            )
        except FactorViewError as error:
            raise _factor_fail(error) from error

        if json_output:
            typer.echo(json.dumps(build_view(report), ensure_ascii=False, sort_keys=True))
        else:
            _echo_build(report)


def _factor_instant(value: str) -> datetime:
    """One `--as-of` of `factor build`, refusing what `_panel_as_of` refuses.

    A separate function because `_panel_as_of` defaults an empty string to the wall clock, and a
    *prediction* instant must never be defaulted: a build stamped at "now" is a cross section
    nobody asked for at a day nobody named, and it would be stored under that instant forever.
    """
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise _panel_fail(
            PanelExit.bad_request,
            f"--as-of expects an ISO-8601 instant with an offset, e.g. "
            f"2026-01-08T09:00:00+00:00; got {value!r}",
        ) from error
    return parsed


def _echo_build(report: FactorBuildReport) -> None:
    """Print one build: what it wrote, and the census that says whether it wrote anything usable.

    The coverage census is the load-bearing half rather than decoration. A build that stored five
    thousand `input_missing` rows exits 0 and produced nothing, and the number of names that got a
    value is the first thing a caller needs before running an experiment over them -- especially
    against the shipped derived specs, whose `min_cross_section=100` turns a thinner market into a
    coded row for every name (see `the_shipped_transform_and_neutralisation_floors_exceed_a_thin_
    market`).

    All three tier rows always, including the ones this build did not ask for; see `build_rows`.
    """
    typer.echo(f"factor     {report.factor} ({report.factor_id})")
    typer.echo(f"tier       {report.tier}")
    typer.echo(
        f"as_ofs     {len(report.as_ofs)}: "
        f"{', '.join(instant.isoformat() for instant in report.as_ofs)}"
    )
    typer.echo(
        f"subjects   {report.subject_count} evaluated, universe "
        f"{', '.join(str(count) for count in report.universe_counts)} listed per as_of"
    )
    typer.echo("tier            builds  rows  coverage")
    for tier, builds, rows, coverage in build_rows(report):
        typer.echo(f"{tier:<15} {builds:>6}  {rows:>4}  {coverage}")
    typer.echo(f"partitions {len(report.partitions)}: {', '.join(report.partitions)}")
    typer.echo("next       `openalpha factor run --factor ... --start ... --end ...`")


# --- the shortlist, from the stored panel to a gated list (V2-P4-032 / V2-P4-033) ----------------


SHORTLIST_EXIT: Final[Mapping[str, PanelExit]] = MappingProxyType(
    {
        "answered": PanelExit.ok,
        "refused": PanelExit.unhealthy,
        "blocked": PanelExit.unhealthy,
        "panel_unreadable": PanelExit.unhealthy,
        "not_held": PanelExit.unhealthy,
        "bad_request": PanelExit.bad_request,
        "internal_error": PanelExit.internal_error,
    }
)
"""What `shortlist run` exits with for each situation, as one table.

`api/app.py`'s `SHORTLIST_HTTP_STATUS` is the sibling of this and `PanelExit` is the vocabulary --
`FACTOR_EXIT`'s arrangement and its reason: a CI job that already switches on 1/3/4/5 for `panel
doctor`, `data-check` and `factor run` must not have to learn a fifth meaning for the same numbers
on a fifth command.

**`refused` is the row this command exists for, and it must not be `ok`.** A scheduled job that
cut a shortlist, had it refused by the gate and exited `0` would be no gate at all -- the "empty
success" `V2-P1-013` exists to make unavailable, arriving on the plane the product acceptance
measured it on. `unhealthy` rather than a code of its own, because the remedy is the one that code
already names: research the names the list is missing, or rebuild the panel the coverage was
measured on.

**Exit `0` with an empty `admitted` list is a real answer and is deliberately not an error.** A
shortlist every name of which came back unresearched, under a `--min-researched-ratio 0` the caller
declared, was *admitted*: nothing refused it. The two are told apart on stdout by `is_blocked` and
by `admitted` being `null` rather than `[]`, and here by the exit code -- which is the whole point
of the pair, and the defect the acceptance filed was that at no surface could they be told apart at
all.
"""


def _shortlist_fail(error: ShortlistViewError) -> typer.Exit:
    """One `shortlist_view` fault, enveloped by the row of `SHORTLIST_EXIT` it names.

    Looked up by `error.reason` rather than switched on by exception type, `_factor_fail`'s rule:
    a fault added to `shortlist_view.py` with no row here raises `KeyError` inside
    `_panel_command`, which reports `internal_error` and says the table is incomplete, instead of
    being quietly enveloped as whichever branch an `isinstance` chain happened to end on.
    """
    return _panel_fail(SHORTLIST_EXIT[error.reason], error.disclosable)


_SHORTLIST_COMPONENT_HELP: Final[str] = (
    "One factor's contribution to the composite, as `<qualified key>=<weight>` "
    "(`reversal_1d/v1=1.0`). Repeatable. Both halves are required and neither has a default: the "
    "factor decides which column is read and the weight decides how much of the ordering it "
    "owns. A raw-tier screen may declare exactly one, because raw values carry each factor's own "
    "units and summing two of them adds quantities that share no scale."
)
_SHORTLIST_TIER_HELP: Final[str] = (
    "Which stored tier to screen on: `raw`, `processed` or `neutralized`. `processed` and "
    "`neutralized` need a --transform, because those partitions hold every transform of the "
    "factor and are narrowed by the one you name; `raw` takes neither --transform nor "
    "--neutralization, and both are refused rather than ignored on a tier that has no use for "
    "them. `neutralized` is refused by this command; see shortlist_view's "
    "KNOWN_SHORTLIST_VIEW_LIMITATIONS. A processed screen over a market thinner than the "
    "transform's min_cross_section is refused by name: the stored rows all read "
    "`insufficient_cross_section` and there is nothing to order."
)
_SHORTLIST_SIZE_HELP: Final[str] = (
    "How many names reach the evidence plane. No default: it is the cut, and a cut nobody chose "
    "is a list nobody can defend."
)
_SHORTLIST_CAPITAL_HELP: Final[str] = (
    "The notional budget stage two sizes one buy against, in yuan. It decides "
    "`below_board_minimum` -- a name at 300 yuan a share does not sell a 100-share lot for 10,000 "
    "yuan -- and it is not a portfolio weight: nothing here allocates. Must be below 10**26, "
    "which is the first budget whose own fill this build cannot price rather than a policy "
    "limit; see shortlist_view.POSITION_CAPITAL_CEILING."
)
_SHORTLIST_HORIZON_HELP: Final[str] = (
    "The one span every conclusion in this list is over, as a count of trading sessions (`5d`). "
    "`SignalFrame.horizon` accepts exactly this grammar, so a list declaring a calendar span "
    "could never be satisfied."
)
_SHORTLIST_TRADABLE_HELP: Final[str] = (
    "The floor under `tradeable / universe`. Divided by the universe rather than by the scored "
    "count, because a name with no price is dropped before stage two and would otherwise relieve "
    "the bar it exists to trip."
)
_SHORTLIST_RESEARCHED_HELP: Final[str] = (
    "The floor under `candidates / shortlisted`. With no --evidence supplied this is 0.0, so any "
    "floor above zero refuses the list by name -- which is the ordinary first answer: the "
    "shortlist says which names are worth an evidence run, and the gate refuses to publish them "
    "as conclusions until those runs have happened."
)
_SHORTLIST_AGE_HELP: Final[str] = (
    "The ceiling over `built_at - as_of`, in whole calendar days. 0 means `assembled the same day "
    "it is about`."
)
_SHORTLIST_EVIDENCE_HELP: Final[str] = (
    "Path to a JSON object mapping each researched subject to "
    '`{"signal": <SignalFrame>, "run_manifest_id": "..."}`. Omitted means nothing has been '
    "researched, which is a state this list reports rather than hides."
)
_SHORTLIST_CONFIG_DIGEST_HELP: Final[str] = (
    "The configuration this screen ran under, as a 64-character hex digest. Resolved from the "
    "process's own configuration when omitted."
)


@shortlist_app.command("run")
def shortlist_run_command(
    component: Annotated[list[str], typer.Option("--component", help=_SHORTLIST_COMPONENT_HELP)],
    tier: Annotated[str, typer.Option("--tier", help=_SHORTLIST_TIER_HELP)],
    shortlist_size: Annotated[int, typer.Option("--shortlist-size", help=_SHORTLIST_SIZE_HELP)],
    position_capital: Annotated[
        str, typer.Option("--position-capital", help=_SHORTLIST_CAPITAL_HELP)
    ],
    year: Annotated[list[int], typer.Option("--year", help=_BUILD_FACTOR_YEAR_HELP)],
    horizon: Annotated[str, typer.Option("--horizon", help=_SHORTLIST_HORIZON_HELP)],
    min_tradable_ratio: Annotated[
        float, typer.Option("--min-tradable-ratio", help=_SHORTLIST_TRADABLE_HELP)
    ],
    min_researched_ratio: Annotated[
        float, typer.Option("--min-researched-ratio", help=_SHORTLIST_RESEARCHED_HELP)
    ],
    max_ranking_age_days: Annotated[
        int, typer.Option("--max-ranking-age-days", help=_SHORTLIST_AGE_HELP)
    ],
    transform: Annotated[str, typer.Option("--transform", help=_FACTOR_TRANSFORM_HELP)] = "",
    neutralization: Annotated[
        str, typer.Option("--neutralization", help=_FACTOR_NEUTRALIZATION_HELP)
    ] = "",
    runtime_dir: Annotated[
        Path | None, typer.Option("--runtime-dir", help=_RUNTIME_DIR_HELP)
    ] = None,
    exchange: Annotated[
        str, typer.Option("--exchange", help=_FACTOR_EXCHANGE_HELP)
    ] = TRADING_CALENDAR_DEFAULT_EXCHANGE,
    as_of: Annotated[str, typer.Option("--as-of", help=_FACTOR_AS_OF_HELP)] = "",
    code_commit: Annotated[
        str | None, typer.Option("--code-commit", help=_CODE_COMMIT_HELP)
    ] = None,
    config_digest: Annotated[
        str | None, typer.Option("--config-digest", help=_SHORTLIST_CONFIG_DIGEST_HELP)
    ] = None,
    evidence: Annotated[
        Path | None, typer.Option("--evidence", help=_SHORTLIST_EVIDENCE_HELP)
    ] = None,
    json_output: Annotated[
        bool, typer.Option("--json", help="Emit the whole verdict as data.")
    ] = False,
) -> None:
    """Cut a shortlist out of the stored panel, join what has been researched, and gate it.

    **The command that makes `V2-P4-004`, `V2-P4-005` and `V2-P4-023` reachable at all.** Before
    it, the two-stage funnel's required input -- `ComponentCrossSection` -- was constructed
    nowhere in `src/`, and the whole chain could be driven only by importing six modules by hand.

    The usual invocation, against a panel `openalpha factor build` has written a tier into::

        openalpha shortlist run --component reversal_1d/v1=1.0 --tier raw \\
          --shortlist-size 50 --position-capital 100000 --year 2026 --horizon 5d \\
          --min-tradable-ratio 0.30 --min-researched-ratio 0.50 --max-ranking-age-days 1 \\
          --as-of 2026-01-16T09:00:00+00:00 --runtime-dir ./runtime

    **The factor tier has to exist first.** `openalpha factor build` is what puts it there; a
    store built only by `openalpha panel build` holds no factor partition and this command is
    refused by name against it. `openalpha factor list` says which `--component` and `--transform`
    are legal.

    **And so do five panel targets, which is the other half and `V2-P4-078`.** This command reads
    six panel datasets at the resolved cross section's own instant, and a panel short of any one
    of them is refused rather than screened::

        openalpha panel build --dataset trade_cal    --year <year>   # the exchange calendar
        openalpha panel build --dataset stock_basic  --year <year>   # the security registry
        openalpha panel build --dataset price        --year <year>   # bars, valuations, halts
        openalpha panel build --dataset stk_limit    --year <year>   # the published bands
        openalpha panel build --dataset namechange   --year <year>   # the rename corpus

    `namechange` is the one that catches people, and it caught this repository's own end-to-end
    suite: `--tier raw` does not need it, so a factor build over a panel without it is green and
    the shortlist over that same panel is red. It is read for `is_st` -- `MarketBar` carries a
    risk-warning flag per name, taken from the name in effect on the pricing session -- so a
    screen without it would price every special-treated name under an ordinary band. `adj_factor`
    is **not** in the list: `openalpha factor build` may want it, this command never opens it.

    **The `--as-of` a cross section was *built* at decides which session it is priced on, and
    that is not the day it falls on.** A session's bars publish at 16:30 Asia/Shanghai, so a
    build stamped anywhere from that day's midnight up to 16:30 is priced against the **previous**
    session -- the newest one that had published when its values were computed. `V2-P4-077` is
    what happened while this resolved the calendar day instead: every cross section built between
    midnight and the close was permanently unscreenable, at every `as_of` anyone could then ask
    at. `cross_section.pricing_session` is on every answer and says which session was used.

    **`--code-commit ""` is not the same as omitting `--code-commit`, and `V2-P4-046` is what
    happens when they are.** Both flags default to `None` -- *unset* -- rather than to `""`, which
    is what `openalpha run` and `openalpha replay` already do and what this command did not. With
    an empty-string default there was no value the parser could hand back that meant "the caller
    typed an empty one", so `code_commit or None` resolved it from git: over HTTP `""` was a `422`
    naming the seven-character rule, and here the same literal published a shortlist stamped with a
    commit the caller never declared. Omitting the flag still resolves server-side; declaring it
    empty is now refused on all three faces, which is what README means by calling them equivalent.

    **Exit `0` is not "the list shipped".** It is "the gate ran and did not refuse". A list of two
    names that nobody has researched, under `--min-researched-ratio 0`, is *admitted* and exits
    `0` with an empty `admitted` array -- while the same list under `--min-researched-ratio 0.5`
    is *refused* and exits `1` with `admitted: null` and the bar it missed. Those are two
    different answers and telling them apart is what this command was written for; see
    `SHORTLIST_EXIT`.
    """
    runtime_dir = _resolved_runtime_dir(runtime_dir)

    with _panel_command("shortlist run"):
        instant = _panel_as_of(as_of)
        try:
            request = shortlist_request(
                components=_shortlist_component_pairs(component),
                tier=tier,
                shortlist_size=shortlist_size,
                position_capital=_factor_amount(position_capital, flag="--position-capital"),
                as_of=instant,
                years=year,
                exchange=exchange,
                horizon=horizon,
                minimum_tradable_ratio=min_tradable_ratio,
                minimum_researched_ratio=min_researched_ratio,
                maximum_ranking_age_days=max_ranking_age_days,
                code_commit=_resolved_code_commit(code_commit),
                config_digest=_resolved_config_digest(config_digest),
                transform=transform or None,
                neutralization=neutralization or None,
                evidence=_shortlist_evidence(evidence),
            )
            result = run_shortlist(
                _panel_store(runtime_dir),
                request,
                built_at=_panel_clock(),
                runs=SQLiteRunRepository(runtime_dir / "state.sqlite3"),
                shortlists=FileShortlistStore(runtime_dir / "shortlists"),
            )
        except ShortlistViewError as error:
            raise _shortlist_fail(error) from error
        except ShortlistStoreError as error:
            raise _panel_fail(PanelExit.unhealthy, str(error)) from error

        if json_output:
            typer.echo(json.dumps(shortlist_view(result), ensure_ascii=False, sort_keys=True))
        else:
            _echo_shortlist(result)
        if result.is_blocked:
            raise typer.Exit(code=int(SHORTLIST_EXIT["refused"]))


_SHORTLIST_ADDRESS_HELP: Final[str] = (
    "The `shortlist_id` a run's own answer carried (`sla_` and 24 lowercase hex characters). It "
    "is on every `--json` body and in the last line of the terminal rendering; `openalpha "
    "shortlist list` prints every one this runtime directory holds."
)


@shortlist_app.command("get")
def shortlist_get_command(
    shortlist_id: Annotated[str, typer.Argument(help=_SHORTLIST_ADDRESS_HELP)],
    runtime_dir: Annotated[
        Path | None, typer.Option("--runtime-dir", help=_RUNTIME_DIR_HELP)
    ] = None,
) -> None:
    """Print one stored shortlist answer, by the content address its own body carried.

    `V2-P4-062`'s missing command. A run produced three content addresses and nothing held an
    answer under any of them, so "run it, run it again tomorrow, and compare the two" ended at
    the first step for anybody who had not thought to redirect `--json` into a file.

    What comes back is **what was published**, not a re-run: the bytes the store holds, with the
    address re-derived from the content before they are handed over, so a document edited on disk
    exits `1` rather than printing a shortlist somebody reads names off. It is therefore also the
    answer to "what did we say yesterday" on a panel that has since moved.

        openalpha shortlist get sla_0123456789abcdef01234567 --runtime-dir ./runtime

    Always JSON: a stored answer is a document rather than a verdict this command is making, and
    a terminal rendering of it would be a second shape for the same bytes. Exits 0 when the answer
    is held, 1 when it is not, 3 when the address is not one.
    """
    runtime_dir = _resolved_runtime_dir(runtime_dir)

    with _panel_command("shortlist get"):
        try:
            answer = held_shortlist(FileShortlistStore(runtime_dir / "shortlists"), shortlist_id)
        except ShortlistViewError as error:
            raise _shortlist_fail(error) from error
        except ShortlistStoreError as error:
            raise _panel_fail(PanelExit.unhealthy, str(error)) from error
        typer.echo(json.dumps(answer, ensure_ascii=False, sort_keys=True))


@shortlist_app.command("list")
def shortlist_list_command(
    runtime_dir: Annotated[
        Path | None, typer.Option("--runtime-dir", help=_RUNTIME_DIR_HELP)
    ] = None,
    json_output: Annotated[
        bool, typer.Option("--json", help="Emit the addresses as data.")
    ] = False,
) -> None:
    """Every shortlist answer this runtime directory holds, by content address, ascending.

    Addresses rather than bodies, `openalpha factor list`'s shape: an answer is kilobytes and a
    caller listing them wants to pick one. `openalpha shortlist get <id>` is the other half.

    A directory with nothing in it prints nothing and exits 0, which is the ordinary state of a
    fresh install rather than a fault.
    """
    runtime_dir = _resolved_runtime_dir(runtime_dir)

    with _panel_command("shortlist list"):
        held = FileShortlistStore(runtime_dir / "shortlists").list_ids()
        if json_output:
            typer.echo(json.dumps({"shortlist_ids": list(held)}, ensure_ascii=False))
        else:
            for shortlist_id in held:
                typer.echo(shortlist_id)


_SHORTLIST_BASELINE_HELP: Final[str] = (
    "The address of the answer being compared **against** -- the earlier one, in the ordinary "
    "reading. It is named rather than inferred because the store cannot say which answer came "
    "first: `shortlist_id` is a content address and `shortlist list` is ascending by sha256. See "
    "shortlist_compare.KNOWN_COMPARISON_LIMITATIONS."
)
_SHORTLIST_CURRENT_HELP: Final[str] = (
    "The address of the answer being compared. `added` is what this one has and the baseline "
    "does not; reversing the two arguments reverses `added` and `removed`."
)


@shortlist_app.command("compare")
def shortlist_compare_command(
    baseline_id: Annotated[str, typer.Argument(help=_SHORTLIST_BASELINE_HELP)],
    current_id: Annotated[str, typer.Argument(help=_SHORTLIST_CURRENT_HELP)],
    runtime_dir: Annotated[
        Path | None, typer.Option("--runtime-dir", help=_RUNTIME_DIR_HELP)
    ] = None,
    json_output: Annotated[
        bool, typer.Option("--json", help="Emit the whole comparison as data.")
    ] = False,
) -> None:
    """What changed between two published shortlists: added, removed, and reasons (`V2-P4-007`).

    `openalpha shortlist get`'s own docstring describes the workflow this finishes -- "run it,
    run it again tomorrow, and compare the two" -- and the comparing was the step a caller had to
    do by hand.

        openalpha shortlist run --as-of 2026-01-15T23:00:00+00:00 ... --json   # note the id
        openalpha shortlist run --as-of 2026-01-16T12:00:00+00:00 ... --json   # note the id
        openalpha shortlist compare sla_<yesterday> sla_<today>

    Both addresses are arguments and the **first is the baseline**: `shortlist_id` is a content
    address, so the store holds the set of distinct answers this deployment has produced and
    nothing that could order them. A command that guessed at "the previous run" would be
    inventing the ordering.

    The two answers must answer the same question. Two screens of different factors share no
    name, so the difference would report every name added and every name removed -- true about
    two lists, false about one market -- and that is refused by name with the key that differs.

    Exits 0 when the comparison is made, 1 when either address is well formed and nothing is
    held under it, 3 when an address is not one or the two questions differ.
    """
    runtime_dir = _resolved_runtime_dir(runtime_dir)

    with _panel_command("shortlist compare"):
        try:
            comparison = compare_held_shortlists(
                FileShortlistStore(runtime_dir / "shortlists"),
                baseline_id=baseline_id,
                current_id=current_id,
            )
        except ShortlistViewError as error:
            raise _shortlist_fail(error) from error
        except ShortlistStoreError as error:
            raise _panel_fail(PanelExit.unhealthy, str(error)) from error

        if json_output:
            typer.echo(json.dumps(comparison, ensure_ascii=False, sort_keys=True))
        else:
            _echo_comparison(comparison)


def _echo_comparison(comparison: Mapping[str, object]) -> None:
    """One comparison for a human: the two answers, the counts, then one row per security.

    The header names both addresses in the order they were given, because the whole body is
    directional and a reader who cannot see which side is which cannot read `added`. The summary
    line comes before the rows for `_echo_shortlist`'s reason: the fact a reader needs first must
    not have to be inferred by counting rows.
    """
    baseline = cast(Mapping[str, object], comparison["baseline"])
    current = cast(Mapping[str, object], comparison["current"])
    summary = cast(Mapping[str, int], comparison["summary"])
    typer.echo(f"baseline   {baseline['shortlist_id']} as of {baseline['as_of']}")
    typer.echo(f"current    {current['shortlist_id']} as of {current['as_of']}")
    for role, side in (("baseline", baseline), ("current", current)):
        if side["is_blocked"]:
            typer.echo(f"refused    the {role} answer was REFUSED by {side['blocks']}")
    typer.echo(
        f"summary    {summary['added']} added, {summary['removed']} removed, "
        f"{summary['held']} held ({summary['rank_changed']} moved rank, "
        f"{summary['reason_changed']} changed reason)"
    )
    typer.echo(f"{'status':<8} {'security':<12} {'rank':<18} changed")
    for status, subject, rank, changed in shortlist_comparison_rows(comparison):
        typer.echo(f"{status:<8} {subject:<12} {rank:<18} {changed}")


def _shortlist_component_pairs(declared: Sequence[str]) -> tuple[tuple[str, float], ...]:
    """`--component <key>=<weight>` as the pairs `shortlist_request` takes.

    Parsed here rather than as two parallel `--factor`/`--weight` lists, which was the obvious
    spelling and is the one that can silently go out of step: two lists of different lengths are a
    weight attached to the wrong factor, and there is no arrangement of `typer.Option` that makes
    that unconstructible. One token per component cannot.

    `rsplit` on the last `=`, because a `factor_id` cannot contain one and a qualified key cannot
    either -- so the split is unambiguous and a token with two of them is refused by the float
    conversion rather than silently truncated.
    """
    pairs: list[tuple[str, float]] = []
    for token in declared:
        head, separator, tail = token.rpartition("=")
        if not separator or not head.strip():
            raise _panel_fail(
                PanelExit.bad_request,
                f"--component {token!r} is not `<factor>=<weight>`; each component names a factor "
                "this build declares and the weight it carries in the composite, and neither has "
                "a default. `openalpha factor list` prints every factor",
            )
        try:
            pairs.append((head.strip(), float(tail)))
        except ValueError as error:
            raise _panel_fail(
                PanelExit.bad_request,
                f"--component {token!r} carries the weight {tail!r}, which is not a number",
            ) from error
    return tuple(pairs)


def _shortlist_evidence(path: Path | None) -> dict[str, ShortlistEvidence]:
    """`--evidence <file>` as the evidence-plane answers `rank_candidates` joins.

    A file rather than a repeatable flag, because the value is a whole `SignalFrame` per subject --
    a direction, a strength, a confidence, a horizon and its evidence ids -- and a command line
    that took those as flags would be a serialisation format invented at a terminal.

    Parsed by `shortlist_view.shortlist_evidence`, which is the same function the HTTP face's
    `evidence` field goes through, so one document drives either channel and neither can come to
    accept a shape the other refuses. This function's whole job is turning a *path* into the
    object that parser takes.
    """
    if path is None:
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except OSError as error:
        raise _panel_fail(
            PanelExit.bad_request, f"--evidence {path} could not be read: {error}"
        ) from error
    except json.JSONDecodeError as error:
        raise _panel_fail(
            PanelExit.bad_request, f"--evidence {path} is not valid JSON: {error}"
        ) from error
    if not isinstance(payload, dict):
        raise _panel_fail(
            PanelExit.bad_request,
            f"--evidence {path} holds a {type(payload).__name__}; it is a JSON object keyed by "
            'subject, each value `{"signal": <SignalFrame>, "run_manifest_id": "..."}`',
        )
    return shortlist_evidence(payload)


def _echo_shortlist(result: ShortlistRunResult) -> None:
    """Print one shortlist verdict: what was read, what was cut, and whether it may ship.

    The verdict line comes **first** and says `REFUSED` or `admitted` in words, because the one
    thing a reader must not have to infer is which of the two this is -- an empty table under a
    silent header reads identically for a refused list and for one nobody has researched, which is
    the defect this whole issue is about.

    **`unscored` is printed only when stage one dropped somebody**, and it is here because the
    `--json` face grew `funnel.excluded_by_coverage` for `V2-P4-044`. `listed -> scored` is a
    subtraction with no explanation beside it, and a human reading a refused list off a terminal
    needs that explanation more than a program does, not less. Omitted when every cell is zero, so
    a clean screen does not print a line of noughts -- the same rule the block lines follow.

    **`untradeable` is `unscored`'s sibling and `V2-P4-066` is why it exists.** `unscored`
    explained stage one and nothing explained stage two, so `listed -> scored -> tradeable` had an
    explanation under the first arrow and a bare subtraction under the second -- and the second is
    the one `--min-tradable-ratio` gates. The counts come first because "which rule" is the
    reading a person wants first, then the securities under each rule, bounded by
    `MAX_NAMED_UNTRADEABLE` with the residual stated. Omitted entirely when stage two refused
    nobody, which is `unscored`'s own rule; the `--json` face reports all four cells either way,
    because a program diffing two answers needs the zero and a person reading one does not.

    **`unresolved` and `unfinished` are two lines and not one** (`V2-P4-075`). Both count their
    names unresearched and they are different findings: an address this runtime directory holds
    no run for is a provenance claim nobody can stand behind, and an address it holds a *broken*
    run for is a run that needs looking at. One line saying "holds no run for" would have been
    false about the second.
    """
    clearance = result.clearance
    measurement = clearance.measurement
    if clearance.is_blocked:
        typer.echo(f"verdict    REFUSED by {[block.code for block in clearance.blocks]}")
    else:
        admitted = clearance.admitted
        typer.echo(f"verdict    admitted, {len(admitted)} candidate(s) may be published")
    typer.echo(f"gate       {clearance.manifest.gate_manifest_id}")
    typer.echo(
        f"cross      {result.cross_section_as_of.isoformat()} on session "
        f"{result.pricing_session.isoformat()} ({result.request.tier} tier)"
    )
    typer.echo(
        f"funnel     {measurement.universe_count} listed -> {measurement.scored_count} scored -> "
        f"{measurement.tradeable_count} tradeable -> {measurement.shortlist_count} shortlisted "
        f"({result.funnel.coverage})"
    )
    unscored = {
        code: count for code, count in result.funnel.scores.excluded_by_coverage if count > 0
    }
    if unscored:
        typer.echo(f"unscored   {unscored}")
    tradeability = result.funnel.tradeability
    untradeable = {code: count for code, count in tradeability.refused_by_verdict if count > 0}
    if untradeable:
        typer.echo(f"untradeable {untradeable}")
        named, withheld = named_untradeable(tradeability)
        for item in named:
            because = "" if item.reason is None else f" ({item.reason})"
            typer.echo(f"  {item.verdict:<20} {item.subject}{because}")
        if withheld > 0:
            typer.echo(f"  ... and {withheld} more, all counted above")
    researched = (
        "not measurable"
        if measurement.researched_ratio is None
        else f"{measurement.researched_ratio:.4f}"
    )
    typer.echo(
        f"measured   tradable={measurement.tradable_ratio:.4f} researched={researched} "
        f"age={measurement.ranking_age_days}d"
    )
    typer.echo("rank  subject        score        evidence")
    for rank, subject, score, evidence in shortlist_rows(result):
        typer.echo(f"{rank:<5} {subject:<14} {score:<12} {evidence}")
    if result.unresolvable_evidence:
        typer.echo(
            f"unresolved {list(result.unresolvable_evidence)} supplied a run_manifest_id this "
            "runtime directory holds no run for; each is counted unresearched",
            err=True,
        )
    if result.unfinished_evidence:
        typer.echo(
            f"unfinished {list(result.unfinished_evidence)} supplied a run_manifest_id this "
            "runtime directory holds a run for that did not finish; each is counted "
            "unresearched. Re-run the research rather than the screen",
            err=True,
        )
    for block in clearance.blocks:
        typer.echo(f"blocked    {block.code}: {block.detail}", err=True)
    typer.echo(f"held       {shortlist_view(result)['shortlist_id']}")


# --- the model plane: evaluate a declaration, register today's prediction (V2-P4-021) -----------


MODEL_EXIT: Final[Mapping[str, PanelExit]] = MappingProxyType(
    {
        "answered": PanelExit.ok,
        "refused": PanelExit.unhealthy,
        "blocked": PanelExit.unhealthy,
        "panel_unreadable": PanelExit.unhealthy,
        "not_held": PanelExit.unhealthy,
        "bad_request": PanelExit.bad_request,
        "internal_error": PanelExit.internal_error,
    }
)
"""What the four `model` commands exit with for each situation, as one table.

`SHORTLIST_EXIT`'s sibling and its reasoning unchanged: a CI job that already switches on 1/3/4/5
for `panel doctor`, `data-check`, `factor run` and `shortlist run` must not have to learn a sixth
meaning for the same numbers on a sixth command.

**`refused` must not be `ok`.** A scheduled `daily-run` whose model answered about a tenth of the
market, under a floor the operator declared it could not live with, exits `1` -- the "empty
success" `V2-P1-013` exists to make unavailable. The remedy is `unhealthy`'s own: declare features
more of the market carries, or rebuild the columns that are thin.

**Exit `0` with `admitted` empty is not reachable on either run command, and that is a difference
from `shortlist run` worth stating rather than discovering.** A shortlist may legitimately admit
nothing; an evaluation always carries at least one fold (`walk_forward_folds` refuses a schedule
of none) and a daily run always carries at least one security (`FeatureCrossSection` refuses an
empty cross section). So the `null`-versus-`[]` pair here separates *refused* from *answered* and
never *refused* from *empty* -- which is why `blocks` carries both sides of the comparison rather
than leaving an empty list to speak for itself.
"""


def _model_fail(error: ModelViewError) -> typer.Exit:
    """One `model_view` fault, enveloped by the row of `MODEL_EXIT` it names.

    Looked up by `error.reason` rather than switched on by exception type, `_shortlist_fail`'s
    rule: a fault added to `model_view.py` with no row here raises `KeyError` inside
    `_panel_command`, which reports `internal_error` and says the table is incomplete, instead of
    being quietly enveloped as whichever branch an `isinstance` chain happened to end on.
    """
    return _panel_fail(MODEL_EXIT[error.reason], error.disclosable)


_MODEL_FEATURE_HELP: Final[str] = (
    "One column of the feature matrix, as `<factor>@<tier>[:<transform>[:<neutralization>]]` "
    "(`reversal_1d/v1@raw`, `reversal_1d/v1@processed:cross_section_standard/v1`). Repeatable, "
    "and there is no default: the columns are the recipe `feature_version` is a digest of, so a "
    "column nobody declared is a model nobody can rebuild. `processed` requires a transform and "
    "`raw` refuses one. The `neutralized` tier is refused by this command; see "
    "model_view.KNOWN_MODEL_VIEW_LIMITATIONS."
)
_MODEL_NAME_HELP: Final[str] = (
    "The handle this declaration travels under (`momentum_5d_rank`). It reaches the artifact's "
    "content address and the run manifest's model slot, so two declarations sharing a name and "
    "differing anywhere else are still two addresses -- but a reader comparing them has only "
    "this to go on."
)
_MODEL_FAMILY_HELP: Final[str] = (
    "Which implementation fits this declaration. `cross_sectional_rank` is the stdlib rank "
    "baseline; `boosted_rank_trees` is the stdlib gradient-boosted one. Fixed by the code path "
    "rather than chosen freely -- it is what tells a reader that two differently-named "
    "declarations went through the same arithmetic."
)
_MODEL_HORIZON_HELP: Final[str] = (
    "The span every outcome in this run is measured over, as a count of trading sessions (`5d`). "
    "It decides the label window, the purge's reach, and the instant a registered prediction's "
    "outcome becomes knowable."
)
_MODEL_SEED_HELP: Final[str] = (
    "The declared seed. It reaches the artifact's address and the run manifest's random_seed. "
    "Neither shipped model draws a random number, so two seeds produce byte-identical "
    "coefficients and two addresses -- which is recorded rather than repaired; see "
    "KNOWN_ALPHA_MODEL_LIMITATIONS."
)
_MODEL_START_HELP: Final[str] = (
    "The first prediction day of the training range, inclusive, as YYYY-MM-DD in Asia/Shanghai. "
    "A stored cross section falls inside the range by the calendar day of the instant it was "
    "built at, which is the same derivation a label window uses."
)
_MODEL_END_HELP: Final[str] = "The last prediction day of the range, inclusive."
_MODEL_AS_OF_HELP: Final[str] = (
    "The instant every panel read in this run is made at, defaulting to the wall clock. It must "
    "be at or after --end, because an outcome is not knowable at the instant it is predicted "
    "about: the features are read at each prediction instant and the labels behind them at this "
    "one. See model_view's docstring for what the two clocks buy and what they do not."
)
_MODEL_FOLDS_HELP: Final[str] = (
    "How many contiguous test blocks the tail of the range is cut into. No default: a schedule "
    "nobody chose is a result nobody can defend."
)
_MODEL_TEST_DAYS_HELP: Final[str] = "How many prediction days each fold is evaluated on."
_MODEL_EMBARGO_HELP: Final[str] = (
    "How many sessions of separation to require between a surviving training label's close and "
    "the day a fold is first asked on, on top of the purge. 0 removes nothing and is a statement "
    "rather than a default."
)
_MODEL_SCORED_RATIO_HELP: Final[str] = (
    "The floor under `scored / offered`. Abstaining is free, so a headline statistic is only "
    "comparable beside the fraction of the market it was taken over. No default: below the floor "
    "the answer is refused with `admitted: null` and exit 1, and above it the same measurement is "
    "admitted with exit 0."
)
_MODEL_SHELF_LIFE_HELP: Final[str] = (
    "How many days past its training cutoff a fit may still be asked about a cross section. "
    "Beyond it every security abstains with a stated reason rather than being scored, so the run "
    "reports `scored_ratio: 0.0` and is refused by --min-scored-ratio. Omitted, no shelf life is "
    "declared and the answer body says so (`shelf_life_days: null`) rather than implying one."
)
_MODEL_FEATURE_VERSION_HELP: Final[str] = (
    "The recipe this declaration claims to have been fitted on (`feat_...`). Omitted, it is "
    "resolved from the columns declared above -- --code-commit's arrangement. Supplied, it is "
    "checked against them and refuses by name when it disagrees, which is what makes the "
    "declared version a claim rather than a decoration."
)
_MODEL_HYPERPARAMETER_HELP: Final[str] = (
    "One flat scalar hyperparameter, as `<name>=<value>`. Repeatable. Passed through verbatim to "
    "the declaration, so it reaches the artifact's address; nothing here searches or tunes. "
    "Values parse as int, then float, then bool (`true`/`false`), then string."
)
_MODEL_PREDICT_AT_HELP: Final[str] = (
    "The instant the prediction is about -- the stored cross section it scores. Strictly after "
    "--end, because a daily run fits on outcomes that have already closed and predicts about a "
    "day that has none. It is not the instant the batch is produced at: that is this process's "
    "clock, and it is what the store compares its own reading against."
)
_MODEL_CONFIG_DIGEST_HELP: Final[str] = (
    "The configuration this run ran under, as a 64-character hex digest. Resolved from the "
    "process's own configuration when omitted. A daily run files a RunManifest under it."
)


def _model_features(declared: Sequence[str]) -> tuple[FeatureColumn, ...]:
    """`--feature <factor>@<tier>[:<transform>[:<neutralization>]]` as resolved columns.

    One token per column rather than parallel `--factor`/`--tier`/`--transform` lists, which is
    `_shortlist_component_pairs`' measured reason: three lists of different lengths attach a
    transform to the wrong factor, and no arrangement of `typer.Option` makes that
    unconstructible.

    The grammar is `feature_matrix.FeatureColumn.feature_id`'s own, read backwards -- `@` between
    the factor and the tier, `:` between the tier and each spec -- so a caller can paste a
    `feature_id` off a stored artifact straight back into a command line.
    """
    columns: list[Mapping[str, object]] = []
    for token in declared:
        factor, separator, rest = token.partition("@")
        if not separator or not factor.strip():
            raise _panel_fail(
                PanelExit.bad_request,
                f"--feature {token!r} is not `<factor>@<tier>`; every column names the factor it "
                "reads and the stored tier it reads it on, and neither has a default. "
                "`openalpha factor list` prints every factor and transform this build declares",
            )
        parts = rest.split(":")
        if len(parts) > 3:
            raise _panel_fail(
                PanelExit.bad_request,
                f"--feature {token!r} carries {len(parts) - 1} spec(s) after its tier; a column "
                "is a tier, at most one transform and at most one neutralization",
            )
        columns.append(
            {
                "factor": factor.strip(),
                "tier": parts[0],
                "transform": parts[1] if len(parts) > 1 else None,
                "neutralization": parts[2] if len(parts) > 2 else None,
            }
        )
    try:
        return feature_columns(columns)
    except ModelViewError as error:
        raise _model_fail(error) from error


def _model_hyperparameters(
    declared: Sequence[str],
) -> tuple[tuple[str, bool | int | float | str], ...]:
    """`--hyperparameter <name>=<value>` as the sorted pairs a declaration takes.

    Sorted here rather than left to the contract's refusal, because an unsorted command line is
    not a claim: `AlphaModelDeclaration` refuses an unsorted tuple to keep one declaration from
    having two canonical spellings, and a caller typing flags in the order they think of them has
    made no statement about order. A **repeated** name is still refused, by that contract, because
    that one is a claim and the two can disagree.

    The sort itself is `model_view.declared_hyperparameters` rather than a `sorted` call of this
    module's own, which is `V2-P4-091`'s finding: this face and the HTTP one each spelled the rule
    once and the two spellings differed on the only input that can tell them apart. Parsing stays
    here -- a `<name>=<value>` token is this face's own shape -- and the ordering does not.
    """
    pairs: list[tuple[str, bool | int | float | str]] = []
    for token in declared:
        name, separator, raw = token.partition("=")
        if not separator or not name.strip():
            raise _panel_fail(
                PanelExit.bad_request,
                f"--hyperparameter {token!r} is not `<name>=<value>`",
            )
        pairs.append((name.strip(), _model_scalar(raw)))
    return declared_hyperparameters(pairs)


def _model_scalar(raw: str) -> bool | int | float | str:
    """One hyperparameter value, in the narrowest type that reads it back unchanged.

    Bool before int before float before string, and the order is the one that round-trips: `true`
    read as a string would make `--hyperparameter x=true` and a JSON body's `{"x": true}` two
    different declarations on two faces -- the equivalence `V2-P4-046` measured being broken, one
    flag over -- and `3` read as a float would reach the artifact's address as `3.0` and give one
    declaration two spellings.
    """
    text = raw.strip()
    if text.lower() in {"true", "false"}:
        return text.lower() == "true"
    try:
        return int(text)
    except ValueError:
        pass
    try:
        return float(text)
    except ValueError:
        return text


@model_app.command("evaluate")
def model_evaluate_command(
    feature: Annotated[list[str], typer.Option("--feature", help=_MODEL_FEATURE_HELP)],
    name: Annotated[str, typer.Option("--name", help=_MODEL_NAME_HELP)],
    family: Annotated[str, typer.Option("--family", help=_MODEL_FAMILY_HELP)],
    horizon: Annotated[str, typer.Option("--horizon", help=_MODEL_HORIZON_HELP)],
    seed: Annotated[int, typer.Option("--seed", help=_MODEL_SEED_HELP)],
    start: Annotated[str, typer.Option("--start", help=_MODEL_START_HELP)],
    end: Annotated[str, typer.Option("--end", help=_MODEL_END_HELP)],
    year: Annotated[list[int], typer.Option("--year", help=_BUILD_FACTOR_YEAR_HELP)],
    folds: Annotated[int, typer.Option("--folds", help=_MODEL_FOLDS_HELP)],
    test_days_per_fold: Annotated[
        int, typer.Option("--test-days-per-fold", help=_MODEL_TEST_DAYS_HELP)
    ],
    embargo_sessions: Annotated[int, typer.Option("--embargo-sessions", help=_MODEL_EMBARGO_HELP)],
    min_scored_ratio: Annotated[
        float, typer.Option("--min-scored-ratio", help=_MODEL_SCORED_RATIO_HELP)
    ],
    runtime_dir: Annotated[
        Path | None, typer.Option("--runtime-dir", help=_RUNTIME_DIR_HELP)
    ] = None,
    exchange: Annotated[
        str, typer.Option("--exchange", help=_FACTOR_EXCHANGE_HELP)
    ] = TRADING_CALENDAR_DEFAULT_EXCHANGE,
    as_of: Annotated[str, typer.Option("--as-of", help=_MODEL_AS_OF_HELP)] = "",
    shelf_life_days: Annotated[
        int | None, typer.Option("--shelf-life-days", help=_MODEL_SHELF_LIFE_HELP)
    ] = None,
    hyperparameter: Annotated[
        list[str] | None, typer.Option("--hyperparameter", help=_MODEL_HYPERPARAMETER_HELP)
    ] = None,
    feature_version: Annotated[
        str | None, typer.Option("--feature-version", help=_MODEL_FEATURE_VERSION_HELP)
    ] = None,
    code_commit: Annotated[
        str | None, typer.Option("--code-commit", help=_CODE_COMMIT_HELP)
    ] = None,
    config_digest: Annotated[
        str | None, typer.Option("--config-digest", help=_MODEL_CONFIG_DIGEST_HELP)
    ] = None,
    json_output: Annotated[
        bool, typer.Option("--json", help="Emit the whole evaluation as data.")
    ] = False,
) -> None:
    """Fit one declaration once per walk-forward fold and report what it ordered.

    **The command that makes `V2-P4-010` through `V2-P4-016` reachable at all.** Before it, the
    feature matrix, the walk-forward split and both baselines had no caller outside `tests/`.

    The usual invocation, against a panel `openalpha factor build` has written a tier into::

        openalpha model evaluate --feature reversal_1d/v1@raw --name reversal-rank \\
          --family cross_sectional_rank --horizon 1d --seed 7 \\
          --start 2026-01-06 --end 2026-01-14 --year 2026 \\
          --folds 2 --test-days-per-fold 2 --embargo-sessions 0 \\
          --min-scored-ratio 0.5 --as-of 2027-01-01T00:00:00+08:00 --runtime-dir ./runtime

    **`--as-of` reads a *partition*, so reading a year means standing after it** (`V2-P4-094`,
    and the granularity is `panel/catalog.py`'s own). The point-in-time check compares one instant
    per year partition -- the newest at which any row in it became knowable -- against `--as-of`,
    and refuses the whole partition when that instant is later; it does not filter rows. So an
    `--as-of` inside 2026 refuses a 2026 panel however narrow the range you asked about, and the
    refusal names the earliest instant that would read it. The bound the other way is the
    calendar: every session up to `--as-of` has to be *present*, so an `--as-of` past the newest
    session you have built is a `date_gap`. On a panel built for a whole year the usable interval
    is everything after its last session, which is the literal above; on a panel built to
    yesterday it is the hours between yesterday's 16:30 and today's.

    **`--horizon` and the schedule have to leave every fold something to learn from**, and `1d`
    above is measured rather than picked. `5d` over these seven prediction days purges the first
    fold's training set down to nothing and `walk_forward_folds` refuses the schedule outright --
    the example printed here said `5d` until `V2-P4-094`, and no panel could run it. A longer
    horizon wants a longer `--start..--end`, not a different `--as-of`.

    **The factor tier has to exist first, and so do five panel targets.** This command reads the
    declared columns out of the factor partitions and then labels every cross section it found,
    which needs the calendar, the registry, the bars, the published bands, the halt corpus and
    the adjustment factors::

        openalpha panel build --dataset trade_cal   --year <year>
        openalpha panel build --dataset stock_basic --year <year>
        openalpha panel build --dataset price       --year <year>   # bars, valuations, halts
        openalpha panel build --dataset stk_limit   --year <year>
        openalpha panel build --dataset adj_factor  --year <year>

    `adj_factor` is the one that catches people here and it is the one `shortlist run` does not
    need: a label is a return *between two sessions*, so `label_outcome` requires an adjustment
    series and refuses a window the series does not reach. `namechange` is **not** on the list,
    measured rather than assumed -- nothing here builds a `MarketBar`, so no name history is read.

    **Exit `0` is not "this model works".** It is "the schedule ran and the answer cleared the
    coverage floor you declared". Every statistic on it is a rank correlation over a handful of
    test days on stored data, `--min-scored-ratio` is a coverage bar and never a quality one, and
    nothing here controls for having tried ten declarations and kept this one. The `limitations`
    array on `--json` carries all of that in the body.
    """
    runtime_dir = _resolved_runtime_dir(runtime_dir)

    with _panel_command("model evaluate"):
        try:
            request = model_evaluation_request(
                columns=_model_features(feature),
                name=name,
                family=family,
                horizon=horizon,
                seed=seed,
                start=_model_day(start, flag="--start"),
                end=_model_day(end, flag="--end"),
                as_of=_panel_as_of(as_of),
                years=year,
                exchange=exchange,
                folds=folds,
                test_days_per_fold=test_days_per_fold,
                embargo_sessions=embargo_sessions,
                minimum_scored_ratio=min_scored_ratio,
                shelf_life_days=shelf_life_days,
                code_commit=_resolved_code_commit(code_commit),
                config_digest=_resolved_config_digest(config_digest),
                feature_version=feature_version,
                hyperparameters=_model_hyperparameters(hyperparameter or []),
            )
            result = evaluate_model(_panel_store(runtime_dir), request)
        except ModelViewError as error:
            raise _model_fail(error) from error

        if json_output:
            typer.echo(json.dumps(evaluation_view(result), ensure_ascii=False, sort_keys=True))
        else:
            _echo_evaluation(result)
        if result.is_blocked:
            raise typer.Exit(code=int(MODEL_EXIT["refused"]))


@model_app.command("daily-run")
def model_daily_run_command(
    feature: Annotated[list[str], typer.Option("--feature", help=_MODEL_FEATURE_HELP)],
    name: Annotated[str, typer.Option("--name", help=_MODEL_NAME_HELP)],
    family: Annotated[str, typer.Option("--family", help=_MODEL_FAMILY_HELP)],
    horizon: Annotated[str, typer.Option("--horizon", help=_MODEL_HORIZON_HELP)],
    seed: Annotated[int, typer.Option("--seed", help=_MODEL_SEED_HELP)],
    start: Annotated[str, typer.Option("--start", help=_MODEL_START_HELP)],
    end: Annotated[str, typer.Option("--end", help=_MODEL_END_HELP)],
    year: Annotated[list[int], typer.Option("--year", help=_BUILD_FACTOR_YEAR_HELP)],
    predict_at: Annotated[str, typer.Option("--predict-at", help=_MODEL_PREDICT_AT_HELP)],
    min_scored_ratio: Annotated[
        float, typer.Option("--min-scored-ratio", help=_MODEL_SCORED_RATIO_HELP)
    ],
    runtime_dir: Annotated[
        Path | None, typer.Option("--runtime-dir", help=_RUNTIME_DIR_HELP)
    ] = None,
    exchange: Annotated[
        str, typer.Option("--exchange", help=_FACTOR_EXCHANGE_HELP)
    ] = TRADING_CALENDAR_DEFAULT_EXCHANGE,
    as_of: Annotated[str, typer.Option("--as-of", help=_MODEL_AS_OF_HELP)] = "",
    shelf_life_days: Annotated[
        int | None, typer.Option("--shelf-life-days", help=_MODEL_SHELF_LIFE_HELP)
    ] = None,
    hyperparameter: Annotated[
        list[str] | None, typer.Option("--hyperparameter", help=_MODEL_HYPERPARAMETER_HELP)
    ] = None,
    feature_version: Annotated[
        str | None, typer.Option("--feature-version", help=_MODEL_FEATURE_VERSION_HELP)
    ] = None,
    code_commit: Annotated[
        str | None, typer.Option("--code-commit", help=_CODE_COMMIT_HELP)
    ] = None,
    config_digest: Annotated[
        str | None, typer.Option("--config-digest", help=_MODEL_CONFIG_DIGEST_HELP)
    ] = None,
    json_output: Annotated[
        bool, typer.Option("--json", help="Emit the whole answer as data.")
    ] = False,
) -> None:
    """Fit on what has already closed, score today's cross section, and register the answer.

    **The command Story S32 is about**, and the reason `V2-P4-017` built a store this repository
    could not fill::

        openalpha model daily-run --feature reversal_1d/v1@raw --name reversal-rank \\
          --family cross_sectional_rank --horizon 5d --seed 7 \\
          --start 2026-01-06 --end 2026-01-14 --year 2026 \\
          --predict-at 2026-01-16T09:00:00+00:00 --min-scored-ratio 0.5 \\
          --as-of 2027-01-01T00:00:00+08:00 --runtime-dir ./runtime

    `--as-of` is spelled out rather than defaulted, and `V2-P4-094` is why: it defaults to the
    wall clock, which is the right reading instant only on a panel built up to today. Against a
    stored 2026 panel it asks the calendar for every session between the newest one you built and
    now, and the run stops on a `date_gap` that is about the clock rather than about the panel.
    `model evaluate --help` carries the rule in full; the short form is that reading a year means
    standing after it.

    The training set is every labelled example whose outcome window had already closed at
    `--predict-at`; nothing that had not is offered to the fit, which is `V2-P4-013`'s purge with
    the deadline supplied rather than derived. The batch is then handed to the prediction store,
    which stamps `recorded_at` off **its own** clock -- so a caller who backdates reaches
    `unwitnessed` and cannot reach `forward`.

    **`--end` may be the last session you built.** A range reaching within `--horizon` sessions of
    it used to die reading price bars the panel does not hold yet -- `V2-P4-095` -- so a caller
    had to pull `--end` back `horizon + 1` sessions and nothing said so. Those cross sections are
    now skipped before they are labelled, which is the purge above arriving one step earlier;
    `training.day_count` on the answer is what actually trained.

    **What `standing` proves is on the answer, not in this help text.** `forward` means this
    store held the bytes before the outcome became knowable. It does **not** mean the batch was
    produced when it says it was: `predicted_at` is unverifiable by construction, and nothing here
    defends against whoever owns the disk. Both sentences are in the body and in the terminal
    rendering, because a badge with nothing beside it reads as an attestation this repository
    cannot make.

    **A refused run still registered its prediction.** Exit `1` under `--min-scored-ratio` says
    the answer may not be acted on; it does not say nothing was stored. Story S32 is about the
    prediction being persisted before the outcome is known, which is unconditional. The
    `record_id` is on the answer either way.

    **`--shelf-life-days` is how a stale fit says so.** `--start`/`--end` and `--predict-at` are
    independent, so a run may train on last year and predict about today; past the declared span
    every security abstains with a stated reason instead of being scored, which is Story S35. The
    span is **wall time, not sessions** -- a horizon counts open sessions and this repository
    refuses to convert one into the other, so a caller who means five sessions widens it for
    weekends. Omitted, no span is declared and the answer says so (`shelf_life_days: null`) rather
    than implying one. It refuses nothing on its own: an expired run reads `scored_ratio 0.0`,
    which is `--min-scored-ratio`'s to reject, and a floor of `0.0` admits it.

    This is also the command that finally fills `RunManifest.alpha_model_versions`: it files a
    `mode=daily` manifest naming the one artifact it consumed, under a `run_id` derived from the
    prediction's own address, so a re-run that reproduces the prediction is `unchanged` on both
    stores rather than a duplicate on one of them.

    **This face cannot reproduce one, and `V2-P4-100` measured what that costs.** `predicted_at`
    is this process's clock reading and it reaches the record's content address, so every
    invocation of this command files a new record and a new manifest -- a scheduled job that
    retries after a transient failure leaves two records for one prediction day. Neither taking
    `predicted_at` out of the address nor offering a flag to set it is the repair; see
    `model_view.KNOWN_MODEL_VIEW_LIMITATIONS`'
    `a_re_run_of_one_day_files_a_second_record_because_predicted_at_reaches_the_address` for the
    argument against each. `openalpha model predictions` lists what is held in custody order, so
    a second record for one day is visible rather than silent.
    """
    runtime_dir = _resolved_runtime_dir(runtime_dir)

    with _panel_command("model daily-run"):
        try:
            request = daily_request(
                columns=_model_features(feature),
                name=name,
                family=family,
                horizon=horizon,
                seed=seed,
                start=_model_day(start, flag="--start"),
                end=_model_day(end, flag="--end"),
                predict_at=_model_instant(predict_at, flag="--predict-at"),
                as_of=_panel_as_of(as_of),
                years=year,
                exchange=exchange,
                minimum_scored_ratio=min_scored_ratio,
                shelf_life_days=shelf_life_days,
                code_commit=_resolved_code_commit(code_commit),
                config_digest=_resolved_config_digest(config_digest),
                feature_version=feature_version,
                hyperparameters=_model_hyperparameters(hyperparameter or []),
            )
            now = _panel_clock()
            result = run_daily(
                _panel_store(runtime_dir),
                request,
                predictions=FilePredictionStore(runtime_dir / "predictions", clock=_panel_clock),
                runs=SQLiteRunRepository(runtime_dir / "state.sqlite3"),
                predicted_at=now,
                started_at=now,
            )
        except ModelViewError as error:
            raise _model_fail(error) from error
        except PredictionStoreError as error:
            raise _panel_fail(PanelExit.unhealthy, str(error)) from error

        if json_output:
            typer.echo(json.dumps(daily_view(result), ensure_ascii=False, sort_keys=True))
        else:
            for label, value in daily_rows(result):
                typer.echo(f"{label:<19} {value}")
        if result.is_blocked:
            raise typer.Exit(code=int(MODEL_EXIT["refused"]))


_PREDICTION_ADDRESS_HELP: Final[str] = (
    "The `record_id` a daily run's own answer carried (`prd_` and 24 lowercase hex characters). "
    "It is on every `--json` body and in the terminal rendering; `openalpha model predictions` "
    "prints every one this runtime directory holds."
)


@model_app.command("prediction")
def model_prediction_command(
    record_id: Annotated[str, typer.Argument(help=_PREDICTION_ADDRESS_HELP)],
    runtime_dir: Annotated[
        Path | None, typer.Option("--runtime-dir", help=_RUNTIME_DIR_HELP)
    ] = None,
) -> None:
    """Print one registered prediction, by the content address its own body carried.

    What comes back is **what was registered**, not a re-run: the bytes the store holds, with the
    address re-derived from the content before they are handed over, so a document edited on disk
    exits `1` rather than printing scores somebody trades on.

    Always JSON: a registered prediction is a document rather than a verdict this command is
    making. Exits 0 when it is held, 1 when it is not, 3 when the address is not one.

    **What comes back says what the model was**, which `V2-P4-098` found it did not. The `model`
    key carries the whole fitted artifact the record holds by value -- family, feature columns,
    resolved `feature_version`, `code_commit`, seed, hyperparameters, training cutoff, example
    count and coefficients -- so a prediction read a year later resolves to its declaration
    without a lookup. What it still cannot say is the range it trained over and the instant it
    read the panel at; the `limitations` array names both.
    """
    runtime_dir = _resolved_runtime_dir(runtime_dir)

    with _panel_command("model prediction"):
        try:
            record = held_prediction(
                FilePredictionStore(runtime_dir / "predictions", clock=_panel_clock), record_id
            )
        except ModelViewError as error:
            raise _model_fail(error) from error
        except PredictionStoreError as error:
            raise _panel_fail(PanelExit.unhealthy, str(error)) from error
        typer.echo(json.dumps(held_prediction_view(record), ensure_ascii=False, sort_keys=True))


@model_app.command("predictions")
def model_predictions_command(
    runtime_dir: Annotated[
        Path | None, typer.Option("--runtime-dir", help=_RUNTIME_DIR_HELP)
    ] = None,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Emit the register as data: the addresses and one row each."),
    ] = False,
) -> None:
    """Every registered prediction this runtime directory holds, oldest custody first.

    **In the order this store took custody of them, and that is `V2-P4-098`'s fix.** This command
    used to print `list_ids()` -- a sort over content digests, which is uncorrelated with time.
    Measured on five records, the one created third printed first, while the question a register
    is read for is *which of these did I commit to before the other*. Now the first column is the
    custody stamp and the rows are sorted on it.

    Each row says what it is -- the cross section it is about, its standing, the horizon, how much
    of the market it scored and which model produced it -- so a reader chooses which body to open
    instead of opening all of them. `openalpha model prediction <record_id>` is the body. The
    standings present are spelled out under the table, because a `forward` in a column reads as an
    attestation just as fast as a `forward` in a document and this repository can attest nothing.

    A directory with nothing in it prints nothing and exits 0, which is the ordinary state of a
    fresh install rather than a fault -- and is also, for this store, where the *denominator*
    `domain/prediction_record.py` says a multiple-testing policy needs would be counted from.
    """
    runtime_dir = _resolved_runtime_dir(runtime_dir)

    with _panel_command("model predictions"):
        try:
            held = held_predictions(
                FilePredictionStore(runtime_dir / "predictions", clock=_panel_clock)
            )
        except PredictionStoreError as error:
            raise _panel_fail(PanelExit.unhealthy, str(error)) from error
        if json_output:
            typer.echo(json.dumps(prediction_index_view(held), ensure_ascii=False))
            return
        if not held:
            return
        typer.echo(
            f"{'recorded_at':<26} {'as_of':<26} {'standing':<12} {'horizon':<8} "
            f"{'scored':<8} {'model':<24} record_id"
        )
        for recorded, as_of, standing, horizon, scored, model, record_id in prediction_index_rows(
            held
        ):
            typer.echo(
                f"{recorded:<26} {as_of:<26} {standing:<12} {horizon:<8} {scored:<8} "
                f"{model:<24} {record_id}"
            )
        for standing, proves, does_not in prediction_standing_legend(held):
            typer.echo(f"{standing} means      {proves}", err=True)
            typer.echo(f"and does not prove  {does_not}", err=True)


def _model_day(value: str, *, flag: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as error:
        raise _panel_fail(
            PanelExit.bad_request,
            f"{flag} expects an ISO-8601 date (YYYY-MM-DD); got {value!r}",
        ) from error


def _model_instant(value: str, *, flag: str) -> datetime:
    """Parse `--predict-at`, and refuse only what `model_view` structurally cannot see.

    **Only the parse.** `_panel_as_of` also refuses a naive instant, and a first draft copied that
    branch here; a mutation sweep deleted it and nothing went red, which was the right answer
    rather than a missing test. `daily_request` runs `_aware` on this value and refuses a naive
    one by name -- `predict_at '...' carries no UTC offset` -- so the copy here was one rule in
    two places with this one free to drift, and `V2-P4-011` deleted a duplicate check on the same
    ground. What is left is the half no contract below can do: a string that is not an instant at
    all never becomes a `datetime` to be checked.

    There is deliberately no wall-clock default either, which is where this parts company with
    `_panel_as_of`: `--predict-at` names the cross section a prediction is **about**, and
    defaulting it to "now" would register a prediction about whichever build happened to be
    newest, which is a decision nobody took.
    """
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise _panel_fail(
            PanelExit.bad_request,
            f"{flag} expects an ISO-8601 instant with an offset, e.g. "
            f"2026-01-16T09:00:00+00:00; got {value!r}",
        ) from error


def _echo_evaluation(result: ModelEvaluation) -> None:
    """Print one walk-forward evaluation: what was read, what was fitted, and whether it clears.

    The verdict line comes **first** and says `REFUSED` in words, `_echo_shortlist`'s rule and its
    reason: the one thing a reader must not have to infer is which of the two this is, and a table
    of folds under a silent header reads identically either way.

    A statistic that was not measured prints `not measured` rather than a number, which is
    `model_view._number`'s single implementation of the rule: a zero that was measured and a zero
    that was never measurable are the same float and different facts.
    """
    run = result.request.run
    if result.is_blocked:
        typer.echo("verdict    REFUSED by ['scored_ratio_below_floor']")
    else:
        typer.echo(f"verdict    admitted, {len(result.folds)} fitted artifact(s)")
    declaration = run.declaration
    typer.echo(f"model      {declaration.name} ({declaration.family}, {declaration.horizon})")
    typer.echo(f"features   {list(run.feature_ids)} at {declaration.feature_version}")
    typer.echo(
        f"panel      {len(result.prediction_days)} prediction day(s) "
        f"{result.prediction_days[0].isoformat()}..{result.prediction_days[-1].isoformat()} "
        f"read at {run.as_of.isoformat()}"
    )
    typer.echo(
        f"measured   scored={result.scored_count}/{result.offered_count} "
        f"({result.scored_ratio:.4f}) against a floor of {run.minimum_scored_ratio:.4f}"
    )
    for invariance in evaluation_invariances(run):
        typer.echo(f"invariance {invariance['code']}: {invariance['detail']}")
    typer.echo(
        f"{'block':<10} {'coverage':<20} {'mean_rank_ic':<12} {'rank_icir':<12} {'reach':<24}  fit"
    )
    for block, coverage, mean, icir, reach, fit in evaluation_rows(result):
        typer.echo(f"{block:<10} {coverage:<20} {mean:<12} {icir:<12} {reach:<24}  {fit}")
    typer.echo(f"{'limitations':<10} {limitation_pointer()}")
    if result.excluded:
        typer.echo(
            f"excluded   {len(result.excluded)} security-day(s) carried no training example; "
            "see `excluded` on the --json body for each one's reason",
            err=True,
        )
    if result.is_blocked:
        typer.echo(
            "blocked    scored_ratio_below_floor: "
            f"{result.scored_count} of the {result.offered_count} securities offered across the "
            f"folds' test blocks carried a score, which is {result.scored_ratio:.4f} against a "
            f"floor of {run.minimum_scored_ratio:.4f}",
            err=True,
        )


def main() -> None:
    """Run the command-line application.

    Loads `.env` (if present in the process's current working directory) into the
    real process environment before dispatching to any subcommand -- so `doctor`/
    `serve`/... see values that live only in `.env`, with an already-exported real
    environment variable always winning over the same name in `.env`. This is the
    *only* place in this package that does so: the Typer `app` object driven
    directly by `CliRunner` in tests is never routed through here, so exercising
    the CLI in tests never touches a real `.env` as a side effect. See
    `openalpha_cn/config.py` for the full precedence/discovery contract.

    Also configures structured logging (V2-P0B-007), once, before dispatching to any
    subcommand -- the other of this package's two logging entry points, alongside
    `api/app.py::create_app()`. Resolves *only* `OPENALPHA_LOG_LEVEL` for this, via
    `load_log_level()`, never the full `OpenAlphaConfig` -- an invalid `OPENALPHA_LOG_LEVEL`
    itself still fails loudly here with a named `ConfigError`, printed to stderr, since a
    scheduled job's logs being silently misconfigured is exactly the failure mode this
    guards against. Deliberately *not* `load_config()` (Finding 2, a P0.B review fix): an
    earlier version called `load_config()` here, which validates every `OPENALPHA_*` field
    atomically, so an invalid field with nothing to do with logging (e.g. a non-numeric
    `OPENALPHA_MAX_REQUEST_BYTES`) aborted dispatch to *every* command -- including
    `doctor`, whose entire job is diagnosing exactly that kind of broken environment, and
    `version`, which touches no config at all. Any other command that genuinely needs the
    full config still calls `load_config()` itself and fails with the same good named error
    at the point it actually needs it (see `serve`, and `doctor`'s own `"config"` finding).
    """
    load_dotenv()
    try:
        configure_logging(load_log_level())
    except ConfigError as error:
        typer.echo(str(error), err=True)
        raise SystemExit(1) from error
    app()


if __name__ == "__main__":
    main()


_CONSTRUCT_ADDRESS_HELP: Final[str] = (
    "The `shortlist_id` of the list to weight (`sla_` and 24 lowercase hex characters). It is on "
    "every `openalpha shortlist run --json` body; `openalpha shortlist list` prints every one "
    "this runtime directory holds."
)
_CONSTRUCT_TIER_HELP: Final[str] = (
    "One tier's share of the invested book, repeatable and ordered best-rank-first, e.g. "
    "`--tier-weight 0.5 --tier-weight 0.3 --tier-weight 0.2`. They must sum to exactly 1: they "
    "are shares of the invested book, and a vector summing to less is a second, undeclared cash "
    "position. Candidates are cut into that many contiguous rank blocks and each block splits its "
    "share equally."
)
_CONSTRUCT_POSITION_HELP: Final[str] = "Largest share of equity any one name may hold."
_CONSTRUCT_EXPOSURE_HELP: Final[str] = "Largest share of equity all names together may hold."
_CONSTRUCT_CASH_HELP: Final[str] = (
    "Smallest share of equity held as cash. Under long-only accounting this is "
    "`--max-total-exposure` restated (equity == cash + market value), so the tighter of the two "
    "binds and declaring both adds no constraint."
)
_CONSTRUCT_INDUSTRY_HELP: Final[str] = (
    "Largest share of equity any one industry may hold. **Refused on this face**, and by design: "
    "the shortlist a stored answer holds carries no industry for any name, so the cap could not "
    "be enforced and a report saying it held would be true and useless."
)
_CONSTRUCT_TURNOVER_HELP: Final[str] = (
    "Largest total absolute weight change this construction may ask for, both sides counted -- "
    "selling one 5% name and buying another is 0.10. A larger move is scaled down proportionally "
    "toward the previous book rather than refused."
)
_CONSTRUCT_PREVIOUS_HELP: Final[str] = (
    "One held weight of the book being moved from, repeatable, as `SUBJECT=WEIGHT` (e.g. "
    "`--previous-weight 000001.SZ=0.05`). Declared by you and never read from the ledger; a "
    "stale declaration produces a turnover number about a book that no longer exists."
)


def _construct_decimal(value: str, *, flag: str) -> Decimal:
    """One `--flag` as a `Decimal`, refused by name rather than by a traceback."""
    try:
        parsed = Decimal(value)
    except ArithmeticError as error:
        raise _panel_fail(
            PanelExit.bad_request, f"{flag} expects a decimal share of equity; got {value!r}"
        ) from error
    if not parsed.is_finite():
        raise _panel_fail(PanelExit.bad_request, f"{flag} expects a finite decimal; got {value!r}")
    return parsed


def _construct_previous(pairs: Sequence[str]) -> dict[str, Decimal]:
    """`--previous-weight SUBJECT=WEIGHT`, repeated, as one book."""
    book: dict[str, Decimal] = {}
    for pair in pairs:
        subject, separator, weight = pair.partition("=")
        if not separator or not subject.strip():
            raise _panel_fail(
                PanelExit.bad_request,
                f"--previous-weight expects SUBJECT=WEIGHT, e.g. 000001.SZ=0.05; got {pair!r}",
            )
        if subject.strip() in book:
            raise _panel_fail(
                PanelExit.bad_request,
                f"--previous-weight names {subject.strip()!r} twice; a book holds one weight per "
                "security, and two would make which one counts depend on flag order",
            )
        book[subject.strip()] = _construct_decimal(weight, flag="--previous-weight")
    return book


def _echo_construction(construction: PortfolioConstruction) -> None:
    """The terminal rendering, which must carry the heuristic label where the numbers are."""
    typer.echo(f"method: {construction.method}")
    typer.echo(
        f"invested {construction.invested_weight} / cash {construction.cash_weight} / "
        f"unallocated {construction.unallocated_weight}"
    )
    typer.echo(
        f"turnover {construction.turnover} of {construction.turnover_before_budget} requested"
        + (
            ""
            if construction.turnover_damping is None
            else f" (damped {construction.turnover_damping})"
        )
    )
    for breach in construction.caps_breached_after_turnover_damping:
        typer.echo(f"cap still breached after the turnover budget: {breach}")
    for target in construction.targets:
        typer.echo(
            f"  {target.rank:>4}  tier {target.tier}  {target.subject}  {target.weight}"
            + ("  (adjusted)" if target.was_adjusted else "")
        )


@portfolio_app.command("construct")
def portfolio_construct_command(
    shortlist_id: Annotated[str, typer.Argument(help=_CONSTRUCT_ADDRESS_HELP)],
    tier_weight: Annotated[list[str], typer.Option("--tier-weight", help=_CONSTRUCT_TIER_HELP)],
    max_position_weight: Annotated[
        str, typer.Option("--max-position-weight", help=_CONSTRUCT_POSITION_HELP)
    ] = "0.25",
    max_total_exposure: Annotated[
        str, typer.Option("--max-total-exposure", help=_CONSTRUCT_EXPOSURE_HELP)
    ] = "0.80",
    min_cash_weight: Annotated[
        str, typer.Option("--min-cash-weight", help=_CONSTRUCT_CASH_HELP)
    ] = "0",
    max_industry_weight: Annotated[
        str, typer.Option("--max-industry-weight", help=_CONSTRUCT_INDUSTRY_HELP)
    ] = "",
    turnover_budget: Annotated[
        str, typer.Option("--turnover-budget", help=_CONSTRUCT_TURNOVER_HELP)
    ] = "",
    previous_weight: Annotated[
        list[str] | None, typer.Option("--previous-weight", help=_CONSTRUCT_PREVIOUS_HELP)
    ] = None,
    runtime_dir: Annotated[
        Path | None, typer.Option("--runtime-dir", help=_RUNTIME_DIR_HELP)
    ] = None,
    json_output: Annotated[
        bool, typer.Option("--json", help="Emit the whole construction as data.")
    ] = False,
) -> None:
    """Weight one admitted shortlist under a declared heuristic policy (`V2-P5-001`).

    The usual invocation, against a `shortlist_id` `openalpha shortlist run` printed::

        openalpha portfolio construct sla_0123456789abcdef01234567 \\
          --tier-weight 0.5 --tier-weight 0.3 --tier-weight 0.2 \\
          --max-position-weight 0.10 --turnover-budget 0.30 \\
          --previous-weight 000001.SZ=0.05 --runtime-dir ./runtime

    **The weights are a heuristic and the answer says so.** `method` reads `heuristic, not
    optimized` on the terminal rendering and in `--json`, because nothing here maximises anything:
    the three steps are a tiered cut on rank, a bounded trim against the caps, and a proportional
    move toward the target bounded by the turnover budget. ADR-0003 is why there is no optimiser
    -- this build ships no numerical stack -- and the PRD attaches exactly this label as the
    condition of that decision.

    **A shortlist the gate refused has no weights.** `openalpha shortlist run` exits `1` and
    stores an answer whose `admitted` is `null` when the list missed a bar; this command refuses
    that answer by name rather than weighting the names it holds, because a portfolio built out
    of a refused list would launder the refusal into a set of numbers.

    **`--max-industry-weight` is refused here, and that is a measurement rather than a gap.** The
    shortlist face builds its ranking with no exposure cross section, so no stored answer carries
    an industry for any name; a cap over names with no industry is satisfied by every book. It is
    refused instead of being silently unenforceable, and `OpenAlphaSDK
    .construct_portfolio_from_ranking` is where it starts working the day exposures are loaded.

    **Three numbers are printed that a weight vector alone would not tell you**: `unallocated`
    is the weight the caps refused and cash absorbed, `turnover ... of ... requested` is the move
    made beside the move asked for, and any `cap still breached after the turnover budget` line is
    a limit the damped book is over -- damping is a partial move out of the book you declared, so
    a book that already breached a cap can still breach it, and re-trimming would spend the
    turnover the budget just refused.
    """
    runtime_dir = _resolved_runtime_dir(runtime_dir)

    with _panel_command("portfolio construct"):
        limits = PortfolioLimits(
            max_position_weight=_construct_decimal(
                max_position_weight, flag="--max-position-weight"
            ),
            max_total_exposure=_construct_decimal(max_total_exposure, flag="--max-total-exposure"),
            min_cash_weight=_construct_decimal(min_cash_weight, flag="--min-cash-weight"),
            max_industry_weight=(
                None
                if not max_industry_weight
                else _construct_decimal(max_industry_weight, flag="--max-industry-weight")
            ),
            turnover_budget=(
                None
                if not turnover_budget
                else _construct_decimal(turnover_budget, flag="--turnover-budget")
            ),
        )
        previous = _construct_previous(previous_weight or ())
        try:
            policy = PortfolioConstructionPolicy(
                tier_weights=tuple(
                    _construct_decimal(weight, flag="--tier-weight") for weight in tier_weight
                ),
                limits=limits,
            )
            answer = held_shortlist(FileShortlistStore(runtime_dir / "shortlists"), shortlist_id)
            construction = construct_portfolio(
                candidates=candidates_from_shortlist_answer(answer),
                policy=policy,
                previous=previous,
            )
        except ShortlistViewError as error:
            raise _shortlist_fail(error) from error
        except (PortfolioConstructionError, ValidationError) as error:
            raise _panel_fail(PanelExit.bad_request, str(error)) from error
        except ShortlistStoreError as error:
            raise _panel_fail(PanelExit.unhealthy, str(error)) from error

        if json_output:
            typer.echo(
                json.dumps(construction_view(construction), ensure_ascii=False, sort_keys=True)
            )
        else:
            _echo_construction(construction)


# --- V2-P5-013: the scheduling face -------------------------------------------------------
#
# `V2-P5-010` built the primitive and said in its own row that it was leaving the caller to a
# later row: no CLI command, no REST route, not in `build_storage`. This is that caller. What
# lives here is the *face* -- flags, exit codes and a rendering; every guarantee (the lease, the
# per-trading-day primary key, what `due` means) stays in `scheduler.py` and `storage/jobs.py`,
# so the CLI and `GET /api/v1/jobs` cannot come to answer two different things.


_JOB_ID_HELP: Final[str] = (
    "The operator's own name for this schedule, e.g. `daily-panel-check`. It is a name and not "
    "an address: it is the primary key of `scheduled_jobs` and half of the per-trading-day "
    "idempotency key `<job-id>@<session>`, so `@` is refused in it."
)

_JOB_CATCH_UP_HELP: Final[str] = (
    "What this job owes when it wakes to find sessions it never ran. `run-each-missed` returns "
    "every one of them, which is the only policy under which a gap in a point-in-time panel gets "
    "filled. `skip-missed` runs only the newest and advances past the rest, which is right for a "
    "job whose output is a snapshot of now and wrong for anything that accumulates."
)

_JOB_YEAR_HELP: Final[str] = (
    "A calendar year this schedule counts sessions on, repeatable. The stored `trade_cal` "
    "partitions for these years are what decides which days are open, so a job whose "
    "`last_fired_session` predates them is refused by name rather than answered with only the "
    "sessions the loaded calendar happens to see."
)

_JOB_AS_OF_HELP: Final[str] = (
    "ISO-8601 instant this question is asked at; defaults to now. It decides which sessions have "
    "published -- a session becomes knowable at 16:30 Asia/Shanghai -- and nothing else."
)

_JOB_RETRY_HELP: Final[str] = (
    "Attempt a session whose previous attempt finished and failed. Off by default and stated "
    "rather than automatic: a session that fails for a reason time does not fix would otherwise "
    "be retried on every wake-up, for ever."
)


class JobsCatchUp(StrEnum):
    """The two catch-up policies, spelled the way a command line spells things.

    A separate enum from `CatchUpPolicy` because the stored value is `run_each_missed` and the
    flag a person types is `--catch-up run-each-missed`; mapping the two here keeps the stored
    contract's spelling out of the terminal and the terminal's out of the database.
    """

    skip_missed = "skip-missed"
    run_each_missed = "run-each-missed"

    def policy(self) -> CatchUpPolicy:
        return (
            CatchUpPolicy.SKIP_MISSED
            if self is JobsCatchUp.skip_missed
            else CatchUpPolicy.RUN_EACH_MISSED
        )


def _job_owner() -> str:
    """Who this process is, for the lease.

    A hostname and a pid, which is what makes a stuck lease diagnosable -- `lease_owner` is the
    one column that answers "which machine is holding this". Truncated to the column's declared
    width rather than left to `ScheduledJob`'s validator, because a long hostname is not an
    operator error worth refusing a run over.
    """
    return f"{platform.node() or 'unknown-host'}:{os.getpid()}"[:MAX_OWNER_LENGTH]


def _job_store(runtime_dir: Path) -> SQLiteJobStore:
    """The schedule table inside a runtime directory, through the one composition root.

    `build_storage` rather than `SQLiteJobStore(runtime_dir / "state.sqlite3")` directly, which
    is v2 hard rule 5 and not a stylistic preference: `api/app.py` and `sdk.py` once assembled
    the same stores by hand and drifted, and a fourteenth hand-assembly here would be the same
    mistake with a new name. It also means these commands apply pending migrations exactly as
    every other face does.
    """
    return build_storage(runtime_dir=runtime_dir, clock=_panel_clock).job_store


def _job_calendar(
    runtime_dir: Path, *, exchange: str, years: Sequence[int], as_of: datetime
) -> TradingCalendar:
    """The stored exchange calendar this schedule counts sessions on.

    Loaded from the panel rather than constructed, because "which sessions exist" is a fact the
    exchange publishes and this repository stores, and a scheduler that generated weekdays would
    owe work on a national holiday. `stored_calendar` is fail-closed twice over, so a missing or
    stale `trade_cal` partition is a refusal here rather than a calendar that silently reads the
    gap as a holiday.
    """
    try:
        return stored_calendar(
            _panel_store(runtime_dir), exchange=exchange, years=tuple(years), as_of=as_of
        )
    except PanelRequestError as error:
        raise _panel_fail(PanelExit.bad_request, str(error)) from error
    except PanelUnreadableError as error:
        raise _panel_fail(PanelExit.unhealthy, str(error)) from error


def _job_scheduler(
    runtime_dir: Path, *, exchange: str, years: Sequence[int], as_of: datetime
) -> TradingDayScheduler:
    return TradingDayScheduler(
        store=_job_store(runtime_dir),
        calendar=_job_calendar(runtime_dir, exchange=exchange, years=years, as_of=as_of),
        clock=_panel_clock,
        owner=_job_owner(),
    )


@jobs_app.command("register")
def jobs_register_command(
    job_id: Annotated[str, typer.Argument(help=_JOB_ID_HELP)],
    catch_up: Annotated[JobsCatchUp, typer.Option("--catch-up", help=_JOB_CATCH_UP_HELP)],
    runtime_dir: Annotated[
        Path | None, typer.Option("--runtime-dir", help=_RUNTIME_DIR_HELP)
    ] = None,
) -> None:
    """Declare a trading-day schedule, or leave the declared one exactly as it is.

    **Idempotent by declaration and never by progress.** Re-running this is the ordinary case --
    a machine boots, a deployment script runs -- and it must not reset `last_fired_session`,
    because that would re-run every session since the last one. It also does **not** rewrite an
    existing job's `--catch-up`: changing a catch-up policy has a consequence measured in
    sessions of work, and applying it silently on the next restart is how a `skip-missed` job
    quietly becomes a `run-each-missed` one. To change a policy, delete the row deliberately.

    `--catch-up` has no default. It is the one field that decides whether a missed session is
    work or history, and the most permissive answer must not also be the easiest one to get.
    """
    runtime_dir = _resolved_runtime_dir(runtime_dir)

    with _panel_command("jobs register"):
        instant = _panel_clock()
        try:
            job = ScheduledJob(
                job_id=job_id,
                catch_up=catch_up.policy(),
                created_at=instant,
                updated_at=instant,
            )
        except ValidationError as error:
            raise _panel_fail(PanelExit.bad_request, str(error)) from error
        stored = _job_store(runtime_dir).register(job)
        if stored.created_at != instant:
            typer.echo(
                f"{job_id} was already declared as {stored.catch_up.value}"
                + (
                    ""
                    if stored.last_fired_session is None
                    else f", last fired {stored.last_fired_session.isoformat()}"
                )
            )
        else:
            typer.echo(f"registered {job_id} as {stored.catch_up.value}")


@jobs_app.command("list")
def jobs_list_command(
    runtime_dir: Annotated[
        Path | None, typer.Option("--runtime-dir", help=_RUNTIME_DIR_HELP)
    ] = None,
    json_output: Annotated[
        bool, typer.Option("--json", help="Emit the schedules as data.")
    ] = False,
) -> None:
    """Every schedule this installation holds, by name, ascending.

    Reads no calendar, which is why it takes no `--year`: what is *declared* is a fact about
    this database alone, and what is *owed* is a question for `openalpha jobs due`. Keeping them
    apart means a listing still answers on an installation whose `trade_cal` partition is
    missing -- which is exactly when an operator is looking at it.
    """
    runtime_dir = _resolved_runtime_dir(runtime_dir)

    with _panel_command("jobs list"):
        jobs = _job_store(runtime_dir).list_jobs()
        if json_output:
            typer.echo(
                json.dumps(
                    {"jobs": [scheduled_job_view(job) for job in jobs]},
                    ensure_ascii=False,
                    sort_keys=True,
                )
            )
            return
        if not jobs:
            typer.echo("no schedules are declared; see `openalpha jobs register --help`")
            return
        for job in jobs:
            last = "never" if job.last_fired_session is None else job.last_fired_session.isoformat()
            lease = "free" if job.lease_owner is None else f"held by {job.lease_owner}"
            typer.echo(f"{job.job_id}  {job.catch_up.value}  last fired {last}  lease {lease}")


@jobs_app.command("due")
def jobs_due_command(
    job_id: Annotated[str, typer.Argument(help=_JOB_ID_HELP)],
    year: Annotated[list[int], typer.Option("--year", help=_JOB_YEAR_HELP)],
    exchange: Annotated[
        str, typer.Option("--exchange", help=_FACTOR_EXCHANGE_HELP)
    ] = TRADING_CALENDAR_DEFAULT_EXCHANGE,
    as_of: Annotated[str, typer.Option("--as-of", help=_JOB_AS_OF_HELP)] = "",
    runtime_dir: Annotated[
        Path | None, typer.Option("--runtime-dir", help=_RUNTIME_DIR_HELP)
    ] = None,
    json_output: Annotated[bool, typer.Option("--json", help="Emit the answer as data.")] = False,
) -> None:
    """Which trading sessions this job owes right now, and which it is about to skip.

    `openalpha jobs run`'s dry run: the same question, none of the writes.

    **The answer is read off the calendar and off `last_fired_session`, never off the stored
    `next_fire_time`.** That column exists so a poller can `WHERE` on one indexed comparison, and
    it is recomputed from the calendar every time a job advances -- but a stored fire time is
    derived from a calendar that changes (a holiday is announced, a session is added for a
    make-up day), so treating it as the answer is how a job fires on a closed session and
    succeeds on nothing. The two questions asked instead are which sessions had published at
    `--as-of` (`panel_ingest.newest_published_session`, the one function that owns the 16:30
    rule) and which of them this job has already run.
    """
    runtime_dir = _resolved_runtime_dir(runtime_dir)

    with _panel_command("jobs due"):
        instant = _panel_as_of(as_of)
        scheduler = _job_scheduler(runtime_dir, exchange=exchange, years=year, as_of=instant)
        job = scheduler.store.get(job_id)
        if job is None:
            raise _panel_fail(PanelExit.bad_request, job_not_registered(job_id))
        try:
            due = scheduler.due(job_id, now=instant)
        except ScheduleHorizonError as error:
            raise _panel_fail(PanelExit.bad_request, str(error)) from error
        payload = {
            "job_id": due.job_id,
            "catch_up": job.catch_up.value,
            "last_fired_session": (
                None if job.last_fired_session is None else job.last_fired_session.isoformat()
            ),
            "published_through": due.published_through.isoformat(),
            "owed": [session.isoformat() for session in due.owed],
            "skipped": [session.isoformat() for session in due.skipped],
        }
        if json_output:
            typer.echo(json.dumps(payload, ensure_ascii=False, sort_keys=True))
            return
        typer.echo(f"{job_id} ({job.catch_up.value}) published through {due.published_through}")
        if not due.owed:
            typer.echo("  owes nothing")
        for session in due.owed:
            typer.echo(f"  owes {session.isoformat()}")
        for session in due.skipped:
            typer.echo(f"  would skip {session.isoformat()}")


@jobs_app.command("run")
def jobs_run_command(
    job_id: Annotated[str, typer.Argument(help=_JOB_ID_HELP)],
    dataset: Annotated[list[str], typer.Option("--dataset", help=_DATASET_HELP)],
    year: Annotated[list[int], typer.Option("--year", help=_JOB_YEAR_HELP)],
    exchange: Annotated[
        str, typer.Option("--exchange", help=_FACTOR_EXCHANGE_HELP)
    ] = TRADING_CALENDAR_DEFAULT_EXCHANGE,
    index_code: Annotated[list[str] | None, typer.Option("--index-code")] = None,
    as_of: Annotated[str, typer.Option("--as-of", help=_JOB_AS_OF_HELP)] = "",
    retry_failed: Annotated[bool, typer.Option("--retry-failed", help=_JOB_RETRY_HELP)] = False,
    runtime_dir: Annotated[
        Path | None, typer.Option("--runtime-dir", help=_RUNTIME_DIR_HELP)
    ] = None,
    json_output: Annotated[
        bool, typer.Option("--json", help="Emit the whole run as data.")
    ] = False,
) -> None:
    """Run the sessions this job owes, one at a time, under a lease (`V2-P5-013`).

    The crontab line this is meant to be::

        */10 * * * * openalpha jobs run daily-panel-check --dataset daily --dataset adj_factor \\
          --year 2026 --runtime-dir /srv/openalpha/runtime

    Fire it as often as you like. What makes that safe is not this command's care but SQLite's:
    the lease is one conditional `UPDATE`, and `job_runs.idempotency_key` -- `<job-id>@<session>`
    -- is a `PRIMARY KEY`, so a second attempt at one trading session is an `IntegrityError`
    rather than a race two processes can both win.

    ## What the work is, and why it is this and not something bigger

    A point-in-time panel health report over `--dataset`/`--year`, run at **each owed session's
    own publication instant** rather than at wall-clock now. So a job catching up on three
    sessions asks three different point-in-time questions, and a session whose rows had not
    landed yet answers `failed` while a later one answers `succeeded`.

    This build ships one job body rather than a vocabulary of them, and that is measured: every
    other per-session action here takes between eight and twenty declared parameters, and
    `scheduled_jobs` has no column to hold them -- adding one would be a change to a stored
    contract. It is also the only per-session action that reaches no network, which a job on a
    timer had better be.

    ## Exit codes

    - `0` -- every attempted session succeeded, or nothing was owed, or another process holds
      the lease. The last is deliberate: the work is being done, by somebody, and a cron line
      that fired while the previous run was still going has nothing to report.
    - `1` (`unhealthy`) -- at least one session's health report was not clean, or a session is
      owed and its previous attempt already finished (see `--retry-failed`). The panel is at
      fault, not the request.
    - `2` (`bad_request`) -- no such schedule, an unparseable `--as-of`, a calendar this store
      cannot answer for.

    ## Why the catch-up stops at the first failure

    `finish_session` deliberately does not advance `last_fired_session` past a failed run, so
    the session stays owed. But a *later* success in the same loop would move the watermark over
    it -- a daily ingest that failed on Monday and succeeded on Wednesday would report itself
    complete through Wednesday with Monday's hole still open, which is precisely the silent gap
    a point-in-time panel must not acquire. So the loop stops, and the sessions after the
    failure stay owed until the failure is dealt with.
    """
    runtime_dir = _resolved_runtime_dir(runtime_dir)

    with _panel_command("jobs run"):
        instant = _panel_as_of(as_of)
        store, request = _panel_request(
            runtime_dir=runtime_dir,
            dataset=dataset,
            year=year,
            session=None,
            index_code=index_code,
            exchange=exchange,
            as_of=as_of,
            with_calendar=True,
        )
        if request.calendar is None:  # pragma: no cover - `with_calendar=True` above
            raise _panel_fail(PanelExit.internal_error, "jobs run resolved no calendar")
        scheduler = TradingDayScheduler(
            store=_job_store(runtime_dir),
            calendar=request.calendar,
            clock=_panel_clock,
            owner=_job_owner(),
        )
        if scheduler.store.get(job_id) is None:
            raise _panel_fail(PanelExit.bad_request, job_not_registered(job_id))
        try:
            due = scheduler.due(job_id, now=instant)
        except ScheduleHorizonError as error:
            raise _panel_fail(PanelExit.bad_request, str(error)) from error

        payload: dict[str, object] = {
            "job_id": job_id,
            "claimed": False,
            "published_through": due.published_through.isoformat(),
            "owed": [session.isoformat() for session in due.owed],
            "skipped": [session.isoformat() for session in due.skipped],
            "attempts": [],
            "stopped_after": None,
        }
        attempts: list[dict[str, object]] = []
        # The lease and every run row are stamped with the **wall clock**, never with `--as-of`.
        # The two are different questions and conflating them was a real defect while this
        # command was being written: `--as-of` decides which sessions had published, and a lease
        # expiry or a `started_at` derived from it would put a live lock's expiry in the past
        # whenever an operator asked a point-in-time question about a week that has gone by.
        if scheduler.claim(job_id, now=_panel_clock()) is None:
            _echo_jobs_run(payload, json_output=json_output)
            return
        payload["claimed"] = True
        try:
            for session in due.skipped:
                scheduler.skip_to(job_id, session, now=_panel_clock())
            for session in due.owed:
                attempt = _attempt_one_session(
                    scheduler,
                    store,
                    request,
                    job_id=job_id,
                    session=session,
                    retry_failed=retry_failed,
                )
                attempts.append(attempt)
                if attempt["status"] != "succeeded":
                    payload["stopped_after"] = session.isoformat()
                    break
        finally:
            payload["attempts"] = attempts
            scheduler.release(job_id, now=_panel_clock())
        _echo_jobs_run(payload, json_output=json_output)
        if any(attempt["status"] != "succeeded" for attempt in attempts):
            raise typer.Exit(code=int(PanelExit.unhealthy))


def _attempt_one_session(
    scheduler: TradingDayScheduler,
    store: PanelStore,
    request: DependencyRequest,
    *,
    job_id: str,
    session: date,
    retry_failed: bool,
) -> dict[str, object]:
    """One owed session: open the run, do the work at *its* instant, close it.

    `already_attempted` is this command's own word and not a `JobRun.status`. It is what a
    session whose previous attempt finished looks like from here: the row holds the primary key,
    so `start_session` refuses it, and refusing it is right -- re-running an already-attempted
    trading session is a decision. `--retry-failed` is where the decision is stated.
    """
    try:
        if retry_failed and scheduler.store.run_for(job_id, session) is not None:
            scheduler.retry(job_id, session, now=_panel_clock())
        else:
            scheduler.start(job_id, session, now=_panel_clock())
    except JobAlreadyRanError:
        return {
            "session": session.isoformat(),
            "status": "already_attempted",
            "error_type": None,
            "remedy": (
                f"{job_id} already attempted {session.isoformat()} and that attempt finished. "
                "Re-running a trading session is a decision rather than a default; state it "
                "with `--retry-failed`"
            ),
        }

    report = panel_health_report(
        store,
        as_of=session_publication_instant(session),
        datasets=request.datasets,
        years=request.years,
        calendar=request.calendar,
        index_codes=request.index_codes,
        cross_section_days=request.sessions,
    )
    if report.is_clean:
        scheduler.succeed(job_id, session, now=_panel_clock())
        return {"session": session.isoformat(), "status": "succeeded", "error_type": None}
    worst = next(
        (finding.code for finding in report.findings if finding.severity == "blocking"),
        next((finding.code for finding in report.findings), "unhealthy"),
    )
    scheduler.fail(job_id, session, error_type=worst, now=_panel_clock())
    return {"session": session.isoformat(), "status": "failed", "error_type": worst}


def _echo_jobs_run(payload: Mapping[str, object], *, json_output: bool) -> None:
    """The two renderings of one run. The terminal one must not be the poorer story.

    A `--json`-only account of what was skipped or of a lease somebody else holds is an account
    the person reading the terminal never sees, which is where a policy becomes invisible at the
    moment it mattered.
    """
    if json_output:
        typer.echo(json.dumps(payload, ensure_ascii=False, sort_keys=True))
        return
    typer.echo(f"{payload['job_id']} published through {payload['published_through']}")
    if not payload["claimed"]:
        typer.echo("  another process holds the lease; nothing attempted")
        return
    for session in cast(Sequence[str], payload["skipped"]):
        typer.echo(f"  skipped {session} (skip-missed: advanced past it, no run recorded)")
    attempts = cast(Sequence[Mapping[str, object]], payload["attempts"])
    if not attempts:
        typer.echo("  owes nothing")
    for attempt in attempts:
        suffix = "" if attempt["error_type"] is None else f" ({attempt['error_type']})"
        typer.echo(f"  {attempt['session']} {attempt['status']}{suffix}")
        if remedy := attempt.get("remedy"):
            typer.echo(f"    {remedy}")
    if attempt_note := payload["stopped_after"]:
        typer.echo(
            f"  stopped after {attempt_note}; the sessions behind it stay owed rather than "
            "being run past. Deal with the failure, then `--retry-failed`"
        )


_STATISTICS_SIGNAL_HELP: Final[str] = (
    "One signal ID, repeatable. Each becomes one cohort and one hypothesis; its rows are the "
    "validations stored against it."
)
_STATISTICS_FAMILY_HELP: Final[str] = (
    "How many cohorts the study actually tested -- NOT how many --signal flags you passed. "
    "Required, and may not be below the number tested here (`V2-P5-007`)."
)
_STATISTICS_RATE_HELP: Final[str] = (
    "The false discovery rate the family is controlled at, strictly between 0 and 1."
)
_STATISTICS_DEPENDENCE_HELP: Final[str] = (
    "Declared dependence among the hypotheses. `independent-or-positively-dependent` is "
    "Benjamini-Hochberg; `arbitrary` adds Benjamini-Yekutieli's harmonic penalty. No default: "
    "the permissive reading must not be the cheapest one to ask for."
)
_STATISTICS_LEVEL_HELP: Final[str] = "Confidence level for the percentile-bootstrap interval."
_STATISTICS_SAMPLES_HELP: Final[str] = "How many bootstrap resamples the interval is taken over."
_STATISTICS_SEED_HELP: Final[str] = "Seed for the bootstrap, so the interval is reproducible."


_SEGMENTED_PLAN_HELP: Final[str] = (
    "Path to the JSON segmentation plan: the axes to cut by, each with the definition and "
    "source of its labels, and the baselines to report beside. Every label is declared here "
    "because a stored validation result names no security to derive one from."
)

_SEGMENTED_FAMILY_HELP: Final[str] = (
    "How many hypotheses the study actually tested. NOT the number of --signal flags and NOT "
    "the number of axes: cutting one cohort three ways tests however many buckets result, and "
    "a family declared before the cut publishes several chances to look skilful at the price "
    "of one. Refused when below the buckets this report tests."
)

_TURNOVER_BUFFER_HELP: Final[str] = (
    "The no-trade band, as a share of equity. A name whose requested move is at or below the "
    "band is not traded at all; a name above it moves the whole way. This is not "
    "--turnover-budget, which damps every move proportionally instead."
)

_TURNOVER_RATE_HELP: Final[str] = (
    "Cost per unit of turnover, both sides counted. Optional and with no default: without it "
    "the saving is reported in turnover and the report says why it publishes no figure in "
    "money, because an invented rate would multiply every turnover number in it."
)

_TURNOVER_RATE_DEFINITION_HELP: Final[str] = (
    "What --cost-per-unit-turnover covers -- commission only, commission and stamp duty, an "
    "impact estimate. Required whenever a rate is given, so a stored report says what its "
    "money figure meant."
)


def _echo_segmented_report(report: SegmentedReport) -> None:
    """One segmented report as a table, with the family printed once above all of the axes.

    The family line is **above** the axes and not repeated inside each, because a reader who
    sees a family line per axis will read four corrections where there is one. The `could` column
    is the one a plain q-value table has no room for: it says whether the bucket's own sample
    size could ever have produced a p-value small enough to clear the family's most permissive
    line, so a large q-value on a two-name bucket reads as resolution rather than as evidence.
    """
    family = report.statistics.multiple_testing
    procedure = (
        "Benjamini-Hochberg"
        if family.dependence == "independent-or-positively-dependent"
        else "Benjamini-Yekutieli"
    )
    typer.echo(
        f"family: ONE family of {family.family_size} across {len(report.axes)} axis/axes -- "
        f"{report.segment_hypotheses} segment bucket(s) + {report.benchmark_hypotheses} "
        f"benchmark row(s), {family.reported_hypotheses} reported, "
        f"{family.withheld_hypotheses} withheld"
    )
    typer.echo(
        f"control: {procedure} at q={family.false_discovery_rate}, "
        f"dependence={family.dependence} (penalty {family.dependence_penalty:.4f}), "
        f"{family.discoveries} discoveries"
    )
    typer.echo(
        f"resolution: {report.hypotheses_that_could_ever_reject} of "
        f"{family.reported_hypotheses} reported row(s) could ever have rejected in this family"
    )
    typer.echo(f"regimes: {report.regime_coverage.reason}")

    for axis in report.axes:
        typer.echo("")
        typer.echo(f"axis {axis.axis_id} -- {axis.definition} (source: {axis.source})")
        typer.echo(
            f"{'  segment':<26}{'n':>4}{'gross':>12}{'drag':>12}{'net':>12}"
            f"{'q':>10}{'could':>8}  verdict"
        )
        for segment in axis.segments:
            _echo_segment_row(report, segment.label, segment.cohort_id, segment)

    for benchmark in report.benchmarks:
        typer.echo("")
        typer.echo(
            f"benchmark {benchmark.benchmark_id} ({benchmark.kind}) -- {benchmark.definition}"
        )
        typer.echo(
            f"{'  row':<26}{'n':>4}{'gross':>12}{'drag':>12}{'net':>12}"
            f"{'q':>10}{'could':>8}  verdict"
        )
        _echo_segment_row(report, "benchmark", benchmark.cohort_id, benchmark)
        if benchmark.difference is None:
            typer.echo(f"  no paired difference -- {benchmark.comparison_absence_reason}")
        else:
            _echo_segment_row(
                report,
                "strategy - benchmark",
                benchmark.difference_cohort_id or "",
                benchmark,
                difference=True,
            )

    for axis in report.axes:
        for segment in axis.segments:
            if segment.statistics.absence_reason is not None:
                typer.echo("")
                typer.echo(
                    f"{segment.cohort_id}: no interval and no p-value -- "
                    f"{segment.statistics.absence_reason}"
                )


def _echo_segment_row(
    report: SegmentedReport,
    label: str,
    cohort_id: str,
    holder: object,
    *,
    difference: bool = False,
) -> None:
    """One row of the segmented table, for a bucket, a benchmark or a paired difference."""
    if difference:
        statistics = holder.difference  # type: ignore[attr-defined]
        capability = holder.difference_capability  # type: ignore[attr-defined]
    else:
        statistics = holder.statistics  # type: ignore[attr-defined]
        capability = holder.capability  # type: ignore[attr-defined]
    verdict = report.statistics.verdict_for(cohort_id)
    quantile = "--" if verdict is None else f"{verdict.q_value:.6f}"
    standing = (
        "not tested" if verdict is None else ("discovery" if verdict.rejected else "not rejected")
    )
    typer.echo(
        f"  {label:<24}{statistics.sample_size:>4}"
        f"{statistics.gross_active_return:>+12.6f}{statistics.cost_drag:>+12.6f}"
        f"{statistics.net_active_return:>+12.6f}{quantile:>10}"
        f"{('yes' if capability.can_ever_reject else 'no'):>8}  {standing}"
    )


def _echo_turnover_variants(report: TurnoverVariantReport) -> None:
    """Both arms, always, with the saving and the distance it bought printed as one line.

    The two arms are printed as two rows of one table rather than as two blocks, because the
    row this serves is a comparison and a reader who can scroll one arm out of view will.
    """
    typer.echo(f"method: {report.method}")
    typer.echo(f"band: {report.buffer} (no-trade, not a proportional turnover budget)")
    typer.echo("")
    typer.echo(f"{'arm':<14}{'turnover':>14}{'traded':>9}{'invested':>14}{'cost':>16}")
    for arm in (report.unbuffered, report.buffered):
        cost = "--" if arm.turnover_cost is None else f"{arm.turnover_cost}"
        typer.echo(
            f"{arm.label:<14}{arm.turnover!s:>14}{arm.names_traded:>9}"
            f"{arm.invested_weight!s:>14}{cost:>16}"
        )
    typer.echo("")
    typer.echo(
        f"the band saved {report.turnover_reduction} of turnover and put the book exactly "
        f"{report.deviation_from_intended_book} away from the one the ranking asked for -- "
        "these are the same number, one for one"
    )
    if report.cost_saved is None:
        typer.echo(f"no cost figure -- {report.cost_absence_reason}")
    else:
        typer.echo(f"cost saved: {report.cost_saved}")
    for subject in report.retained_positions:
        typer.echo(f"retained by the band though the ranking dropped it: {subject}")
    for subject in report.position_caps_breached:
        typer.echo(f"position cap still breached after the band: {subject}")


def _echo_outcome_statistics(report: OutcomeStatisticsReport) -> None:
    """Render one report as a table whose columns are the four the row asks for.

    `gross`, `drag` and `net` are printed side by side in that order so the middle column is
    visibly the difference between the two beside it, which is the whole reason `V2-P5-008`
    asks for it separately. `n` sits before the interval because an interval read without its
    sample size is the mistake the interval exists to make hard.
    """
    family = report.multiple_testing
    typer.echo(
        f"family: {family.family_size} hypotheses tested, {family.reported_hypotheses} reported, "
        f"{family.withheld_hypotheses} withheld"
    )
    procedure = (
        "Benjamini-Hochberg"
        if family.dependence == "independent-or-positively-dependent"
        else "Benjamini-Yekutieli"
    )
    typer.echo(
        f"control: {procedure} at q={family.false_discovery_rate}, "
        f"dependence={family.dependence} (penalty {family.dependence_penalty:.4f}), "
        f"{family.discoveries} discoveries"
    )
    typer.echo(
        f"interval: percentile bootstrap, {report.confidence_level:.0%} over "
        f"{report.bootstrap_samples} resamples from seed {report.random_seed}"
    )
    typer.echo("")
    typer.echo(
        f"{'cohort':<24}{'n':>4}{'gross':>12}{'drag':>12}{'net':>12}"
        f"{'unexplained':>14}{'interval':>26}{'q':>10}  verdict"
    )
    for cohort in report.cohorts:
        verdict = report.verdict_for(cohort.cohort_id)
        if cohort.interval is None:
            interval = "--"
            quantile = "--"
            standing = "not tested"
        else:
            interval = f"[{cohort.interval.lower:+.6f}, {cohort.interval.upper:+.6f}]"
            quantile = "--" if verdict is None else f"{verdict.q_value:.6f}"
            standing = (
                "not tested"
                if verdict is None
                else ("discovery" if verdict.rejected else "not rejected")
            )
        typer.echo(
            f"{cohort.cohort_id:<24}{cohort.sample_size:>4}"
            f"{cohort.gross_active_return:>+12.6f}{cohort.cost_drag:>+12.6f}"
            f"{cohort.net_active_return:>+12.6f}{cohort.unexplained_return:>+14.6f}"
            f"{interval:>26}{quantile:>10}  {standing}"
        )
    for cohort in report.cohorts:
        if cohort.absence_reason is not None:
            typer.echo("")
            typer.echo(f"{cohort.cohort_id}: no interval and no p-value -- {cohort.absence_reason}")


@validation_app.command("statistics")
def validation_statistics_command(
    signal: Annotated[list[str], typer.Option("--signal", help=_STATISTICS_SIGNAL_HELP)],
    family_size: Annotated[int, typer.Option("--family-size", help=_STATISTICS_FAMILY_HELP)],
    dependence: Annotated[str, typer.Option("--dependence", help=_STATISTICS_DEPENDENCE_HELP)],
    false_discovery_rate: Annotated[
        float, typer.Option("--false-discovery-rate", help=_STATISTICS_RATE_HELP)
    ] = 0.10,
    confidence_level: Annotated[
        float, typer.Option("--confidence-level", help=_STATISTICS_LEVEL_HELP)
    ] = 0.95,
    bootstrap_samples: Annotated[
        int, typer.Option("--bootstrap-samples", help=_STATISTICS_SAMPLES_HELP)
    ] = 1000,
    random_seed: Annotated[int, typer.Option("--random-seed", help=_STATISTICS_SEED_HELP)] = 0,
    runtime_dir: Annotated[
        Path | None, typer.Option("--runtime-dir", help=_RUNTIME_DIR_HELP)
    ] = None,
    json_output: Annotated[
        bool, typer.Option("--json", help="Emit the whole report as data.")
    ] = False,
) -> None:
    """Aggregate stored outcome validations, controlled for multiple testing (`V2-P5-008`, `007`).

    The usual invocation, against signal IDs `openalpha research run` printed::

        openalpha validation statistics --signal sig_a --signal sig_b \
          --family-size 40 --false-discovery-rate 0.10 \
          --dependence independent-or-positively-dependent --runtime-dir ./runtime

    **`--family-size` is not the number of `--signal` flags.** It is how many cohorts the study
    that produced these actually tested, and it is what every q-value is computed against. Forty
    signals swept and two reported is `--family-size 40`, and the two q-values it produces are
    twenty times the ones a two-cohort family would give. The only direction this command can
    check is that the declaration is not *below* the number of cohorts tested here, and it
    refuses that; the other direction is `the_family_size_is_declared_and_no_check_can_confirm_it`
    and is printed with the answer in `--json`.

    **Four columns, and the fourth is the point.** `gross` is what the position made against the
    benchmark, `drag` is the transaction cost as its own negative column, `net` is what was kept,
    and `unexplained` is the part of it `V2-P5-005`/`006` refuse to attribute -- on a held
    decision that is the whole selection return, so `unexplained` above `net` is the ordinary
    reading and not a fault.

    **A cohort with fewer than two observations gets no interval and no p-value.** Every resample
    of a single observation is that observation, so the interval would have zero width at any
    confidence level; the absence is printed with its reason, and the cohort is left out of the
    family rather than being counted as a hypothesis that failed to reject.

    Exits 0 when the report was produced, 3 when the request could not be put -- a signal with
    nothing stored, a family smaller than the cohorts tested, a dependence that is not one of the
    two -- and 1 when the runtime directory could not be opened.
    """
    runtime_dir = _resolved_runtime_dir(runtime_dir)

    with _panel_command("validation statistics"):
        if dependence not in ("independent-or-positively-dependent", "arbitrary"):
            raise _panel_fail(
                PanelExit.bad_request,
                f"--dependence must be `independent-or-positively-dependent` or `arbitrary`, "
                f"not {dependence!r}; it decides the correction and has no default",
            )
        try:
            report = OpenAlphaSDK(runtime_dir=runtime_dir).outcome_statistics(
                signal_ids=tuple(signal),
                family_size=family_size,
                false_discovery_rate=false_discovery_rate,
                dependence=cast(DependenceAssumption, dependence),
                confidence_level=confidence_level,
                bootstrap_samples=bootstrap_samples,
                random_seed=random_seed,
            )
        except (OutcomeStatisticsError, ValidationError) as error:
            raise _panel_fail(PanelExit.bad_request, str(error)) from error

        if json_output:
            typer.echo(
                json.dumps(outcome_statistics_view(report), ensure_ascii=False, sort_keys=True)
            )
        else:
            _echo_outcome_statistics(report)


@validation_app.command("segmented")
def validation_segmented_command(
    signal: Annotated[list[str], typer.Option("--signal", help=_STATISTICS_SIGNAL_HELP)],
    plan: Annotated[Path, typer.Option("--plan", help=_SEGMENTED_PLAN_HELP)],
    family_size: Annotated[int, typer.Option("--family-size", help=_SEGMENTED_FAMILY_HELP)],
    dependence: Annotated[str, typer.Option("--dependence", help=_STATISTICS_DEPENDENCE_HELP)],
    false_discovery_rate: Annotated[
        float, typer.Option("--false-discovery-rate", help=_STATISTICS_RATE_HELP)
    ] = 0.10,
    confidence_level: Annotated[
        float, typer.Option("--confidence-level", help=_STATISTICS_LEVEL_HELP)
    ] = 0.95,
    bootstrap_samples: Annotated[
        int, typer.Option("--bootstrap-samples", help=_STATISTICS_SAMPLES_HELP)
    ] = 1000,
    random_seed: Annotated[int, typer.Option("--random-seed", help=_STATISTICS_SEED_HELP)] = 0,
    runtime_dir: Annotated[
        Path | None, typer.Option("--runtime-dir", help=_RUNTIME_DIR_HELP)
    ] = None,
    json_output: Annotated[
        bool, typer.Option("--json", help="Emit the whole report as data.")
    ] = False,
) -> None:
    """Segment stored outcomes by declared cuts, tested in one family (`V2-P5-009`).

    The usual invocation, against signal IDs `openalpha research run` printed::

        openalpha validation segmented --signal sig_a --signal sig_b \
          --plan ./segments.json --family-size 22 --dependence arbitrary \
          --false-discovery-rate 0.10 --runtime-dir ./runtime

    **`--family-size` is not the number of `--signal` flags and it is not the number of axes.**
    Cutting one cohort by industry, by size and by regime tests however many buckets result, and
    every one of them is a hypothesis. Three axes over eight signals is commonly twenty-plus
    hypotheses, all in **one** family -- reporting each axis as its own correction would give
    three chances to find a rejection at the price of one. This command refuses a declaration
    below the buckets it tests; the other direction is the caller's and is printed with the
    answer in `--json`.

    **`--plan` is required because nothing here can derive a label.** A stored `ValidationResult`
    carries a `signal_id` and no ticker, so an industry or a market capitalisation cannot be
    looked up for it however much of `domain/daily_prices.py` is populated. The plan declares,
    per axis, the label for every signal **and** the `definition` and `source` behind those
    labels, so a bucket printed as `large` says what large meant and who said so. A signal with
    no label on a declared axis is refused by name rather than swept into an `unknown` bucket.

    **The `could` column is what a plain q-value table cannot say.** A bucket of three
    observations cannot produce a p-value below `2**-2`, and if that is above the family's most
    permissive critical value the bucket could not have been a discovery on any data at all. Its
    large q-value then measures the study's resolution, not the segment's skill, and `could`
    reads `no`.

    **Market regime is a classification the caller defines.** There is no default classifier
    here. An axis named `market_regime` in the plan gets the coverage line; a run whose testable
    evidence lies in a single regime reports `spans_multiple_regimes` false however many folds
    produced it.

    Exits 0 when the report was produced, 3 when the request could not be put -- an unreadable
    plan, a signal with nothing stored, a family below the buckets tested, an unlabelled signal
    -- and 1 when the runtime directory could not be opened.
    """
    runtime_dir = _resolved_runtime_dir(runtime_dir)

    with _panel_command("validation segmented"):
        if dependence not in ("independent-or-positively-dependent", "arbitrary"):
            raise _panel_fail(
                PanelExit.bad_request,
                f"--dependence must be `independent-or-positively-dependent` or `arbitrary`, "
                f"not {dependence!r}; it decides the correction and has no default",
            )
        try:
            declared = SegmentationPlan.model_validate_json(plan.read_text(encoding="utf-8"))
        except OSError as error:
            raise _panel_fail(
                PanelExit.bad_request, f"--plan could not be read: {plan}: {error}"
            ) from error
        except ValidationError as error:
            raise _panel_fail(
                PanelExit.bad_request, f"--plan is not a segmentation plan: {error}"
            ) from error

        try:
            report = OpenAlphaSDK(runtime_dir=runtime_dir).segmented_outcomes(
                signal_ids=tuple(signal),
                plan=declared,
                declared_family_size=family_size,
                false_discovery_rate=false_discovery_rate,
                dependence=cast(DependenceAssumption, dependence),
                confidence_level=confidence_level,
                bootstrap_samples=bootstrap_samples,
                random_seed=random_seed,
            )
        except (SegmentedReportingError, OutcomeStatisticsError, ValidationError) as error:
            raise _panel_fail(PanelExit.bad_request, str(error)) from error

        if json_output:
            typer.echo(
                json.dumps(segmented_report_view(report), ensure_ascii=False, sort_keys=True)
            )
        else:
            _echo_segmented_report(report)


@portfolio_app.command("turnover-variants")
def portfolio_turnover_variants_command(
    shortlist_id: Annotated[str, typer.Argument(help=_CONSTRUCT_ADDRESS_HELP)],
    tier_weight: Annotated[list[str], typer.Option("--tier-weight", help=_CONSTRUCT_TIER_HELP)],
    buffer: Annotated[str, typer.Option("--buffer", help=_TURNOVER_BUFFER_HELP)],
    max_position_weight: Annotated[
        str, typer.Option("--max-position-weight", help=_CONSTRUCT_POSITION_HELP)
    ] = "0.25",
    max_total_exposure: Annotated[
        str, typer.Option("--max-total-exposure", help=_CONSTRUCT_EXPOSURE_HELP)
    ] = "0.80",
    min_cash_weight: Annotated[
        str, typer.Option("--min-cash-weight", help=_CONSTRUCT_CASH_HELP)
    ] = "0",
    turnover_budget: Annotated[
        str, typer.Option("--turnover-budget", help=_CONSTRUCT_TURNOVER_HELP)
    ] = "",
    previous_weight: Annotated[
        list[str] | None, typer.Option("--previous-weight", help=_CONSTRUCT_PREVIOUS_HELP)
    ] = None,
    cost_per_unit_turnover: Annotated[
        str, typer.Option("--cost-per-unit-turnover", help=_TURNOVER_RATE_HELP)
    ] = "",
    cost_definition: Annotated[
        str, typer.Option("--cost-definition", help=_TURNOVER_RATE_DEFINITION_HELP)
    ] = "",
    runtime_dir: Annotated[
        Path | None, typer.Option("--runtime-dir", help=_RUNTIME_DIR_HELP)
    ] = None,
    json_output: Annotated[
        bool, typer.Option("--json", help="Emit the whole report as data.")
    ] = False,
) -> None:
    """The buffered book beside the unbuffered one, always both (`V2-P5-024`).

    The usual invocation, against a shortlist `openalpha shortlist run` held::

        openalpha portfolio turnover-variants sl_2026_03_02 \
          --tier-weight 0.5 --tier-weight 0.3 --tier-weight 0.2 \
          --buffer 0.01 --previous-weight 000001.SZ=0.05 --runtime-dir ./runtime

    **There is no flag that prints one arm.** That is the row: a high-turnover factor's gross
    edge read without its turnover beside it is not executable alpha, and a command that could
    print the flattering half would eventually be used to.

    **`--buffer` is a no-trade band and `--turnover-budget` is not.** The budget damps every
    move proportionally to hit a total, so every name trades a little; the band leaves each
    small move untraded and takes each large one whole. A policy carrying both gets the budget
    first, inside the construction, and the band second. They are different devices and neither
    substitutes for the other.

    **The saving and its price are one number, and the command says so.** Every unit of turnover
    the band saves is a unit of distance between the book you hold and the book the ranking
    asked for. Two lines you would not get from a weight vector: `retained by the band though
    the ranking dropped it` names a position a buffered run is still holding that its own
    ranking no longer admits, and `position cap still breached after the band` names a limit the
    suppressed trade would have brought back inside -- reported and never repaired, because
    repairing it would spend the turnover the band was asked to save.

    **`--cost-per-unit-turnover` has no default.** Without it the saving is reported in turnover
    and the answer says why there is no figure in money. A default rate would be a number this
    command invented and then multiplied by every turnover figure it printed. When it is given,
    `--cost-definition` is required so a stored report says what the money meant.

    Exits 0 when both arms were produced, 3 when the request could not be put -- a refused
    shortlist, a band outside `[0, 1]`, a rate without a definition -- and 1 when the store
    could not be opened.
    """
    runtime_dir = _resolved_runtime_dir(runtime_dir)

    with _panel_command("portfolio turnover-variants"):
        if cost_per_unit_turnover and not cost_definition:
            raise _panel_fail(
                PanelExit.bad_request,
                "--cost-per-unit-turnover needs --cost-definition; a rate whose meaning is not "
                "recorded produces a money figure nobody can reproduce or compare",
            )
        limits = PortfolioLimits(
            max_position_weight=_construct_decimal(
                max_position_weight, flag="--max-position-weight"
            ),
            max_total_exposure=_construct_decimal(max_total_exposure, flag="--max-total-exposure"),
            min_cash_weight=_construct_decimal(min_cash_weight, flag="--min-cash-weight"),
            turnover_budget=(
                None
                if not turnover_budget
                else _construct_decimal(turnover_budget, flag="--turnover-budget")
            ),
        )
        previous = _construct_previous(previous_weight or ())
        cost_model = (
            None
            if not cost_per_unit_turnover
            else TurnoverCostModel(
                cost_per_unit_turnover=_construct_decimal(
                    cost_per_unit_turnover, flag="--cost-per-unit-turnover"
                ),
                definition=cost_definition,
            )
        )
        try:
            policy = PortfolioConstructionPolicy(
                tier_weights=tuple(
                    _construct_decimal(weight, flag="--tier-weight") for weight in tier_weight
                ),
                limits=limits,
            )
            report = OpenAlphaSDK(runtime_dir=runtime_dir).turnover_variants(
                shortlist_id=shortlist_id,
                policy=policy,
                buffer=_construct_decimal(buffer, flag="--buffer"),
                previous=previous,
                cost_model=cost_model,
            )
        except ShortlistViewError as error:
            raise _shortlist_fail(error) from error
        except (
            TurnoverVariantError,
            PortfolioConstructionError,
            ValidationError,
        ) as error:
            raise _panel_fail(PanelExit.bad_request, str(error)) from error
        except ShortlistStoreError as error:
            raise _panel_fail(PanelExit.unhealthy, str(error)) from error

        if json_output:
            typer.echo(
                json.dumps(turnover_variant_view(report), ensure_ascii=False, sort_keys=True)
            )
        else:
            _echo_turnover_variants(report)
