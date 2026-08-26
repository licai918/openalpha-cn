"""The factor plane's public face: build the tiers, list what is declared, run one experiment.

`V2-P3-014` sealed the artifact and stored nothing. `V2-P3-015` made a run reachable --
`factor run --factor <id> --start --end`, `POST /api/v1/factors/run`,
`OpenAlphaSDK.run_factor_experiment` -- and it is one module for `panel_view.py`'s reason: three
faces that resolve one request three ways answer three different questions, and every equivalence
between them is then a coincidence.

`V2-P3-019` added the two things a run was unreachable and unreadable without, and both were
measured absences rather than conveniences:

- **`factor build`** (see `build_factor_panels`). `V2-P3-015` recorded
  `nothing_in_this_repository_builds_a_factor_panel_from_a_command_line` and left `factor build`
  free "because this issue did not need it". The consequence was exact: a store built by
  `openalpha panel build` held no factor partition, `openalpha factor run` against it was refused
  by name, and `openalpha panel build --dataset factor_obs_...` answered that the dataset is not
  one of its build targets. The whole surface was unreachable to anybody who had not read
  `panel_factors.py`.
- **`factor list` / `factor describe`** (see `factor_catalog` and `factor_entry`). Nineteen
  factors, one transform and one neutralisation are declared, each with prose stating what it does
  and does not measure, and none of it was on any face. The only discovery channel was a typo, and
  a typo answered with nineteen content addresses -- the one form of the identity a human never
  types.

## What a run is

Read the three stored tiers of one factor over a closed range of prediction days, label the
forward returns off the same panel, drive the four `V2-P3-005`..`008` studies on each tier, and
hand the result to `build_factor_experiment`. Nothing here re-derives a number any of those
modules computes. What this module owns is the four decisions a face has to make and none of them
could:

1. **Which factor `<id>` means.** See `resolve_factor`.
2. **What `--start`/`--end` are the range of.** See `FactorRunRequest.start`.
3. **Where the artifact goes and who puts it there.** See `ExperimentDocumentStore`.
4. **What a shortfall does to the envelope.** See `FactorViewError`.

## `<id>` is the qualified key, and `factor_id` is accepted beside it

`--factor reversal_1d/v1`. The CLI faces a human, and `V2-P3-002` already stored the reason in a
different direction: a partition carries `factor_key`/`factor_version` **as well as** `factor_id`
because "`factor_id` is opaque: a reader querying the partition directly would otherwise need
this build's registry". A face made of one opaque content address would be that problem with the
registry lookup moved onto the operator.

`fct_...` is accepted too, and it is not a convenience: a reader holding a stored observation has
a `factor_id` and nothing else, so a face that refused it would make the identity the partition
actually carries the one thing you cannot ask about. The two are told apart by shape rather than
by a flag -- `FactorDefinition.key` is a plain panel identifier and `qualified_key` is `key/vN`,
so a token containing `/` is a qualified key and every other token is a content address.
`resolve_factor` is the whole of it and both directions are driven.

## `--start`/`--end` are prediction days, and the run is evaluated at `--as-of`

Three clocks, and collapsing any two of them would be wrong in a way that produces numbers:

- **`--start` / `--end`** bound the **prediction days**: the `as_of`s the stored cross sections
  were computed at. A cross section stamped outside the range is not in this experiment.
- **`--as-of`** is the instant the *experiment* is evaluated at, and it is what every panel read
  in this module is made at. It is necessarily **after** the prediction days, because a forward
  return is priced on sessions that had not happened when the factor value was stamped. An
  experiment is a backtest; `a_run_is_evaluated_at_one_as_of_and_the_labels_are_read_at_it`
  records what that is and is not.
- **`built_at`** is the wall clock the record was assembled at, out of every digest --
  `FactorExperimentRecord`'s own arrangement, unchanged.

**What a range that ends inside a covered year does, because this is the question the face has to
be able to answer out loud.** The neutralised tier may or may not be on the same schedule as the
other two, and this module cannot assume either. Until `V2-P4-026` it could not be:
`panel_neutralization.load_industry_market_cap_cross_section` read `daily_basic` through
`read_if_ready`, the unfiltered door, so a residual for any day of year Y could only be *built* at
an `as_of` at or after Y's last stored session. That is retracted -- `daily_basic` now has an
as-of-sensitive session-level read and a residual can be stamped at a mid-year instant -- and what
survives is the *schedule* question rather than the arithmetic one: a neutralisation is stamped at
the instants its builds were run at, whatever those were, and a store may hold a raw tier on one
schedule beside a neutralised tier on another. Reading a tier back goes through `read_visible_at`,
so an `as_of` no build was stamped at returns **empty rather than an error**, which is the shape
that makes this dangerous: a face that shrugged would report a two-tier experiment, or a
three-tier one whose neutralised row measured nothing, and the acceptance criterion `V2-P3-014`
exists for is decided on exactly that row.

So this module refuses. The `as_of`s are the **raw** tier's own stored cross sections inside the
range, and every other tier must carry a cross section at each one of them; a tier that does not
is `FactorRunBlockedError`, naming the missing instants and the tier. A `--start`/`--end` over
days the neutralisation was never built for therefore produces a refusal that says which instants
have no residual, rather than an artifact whose loudest cell is `not_measured` for a reason nobody
stated. `the_three_tiers_must_have_been_built_at_the_same_instants` is the entry, and
`tests/integration/test_factor_interfaces.py::
test_a_range_whose_neutralised_tier_was_never_built_is_refused_the_same_way_on_all_three_faces`
drives it on all three faces at once.

## Where the artifact goes, and who puts it there

`V2-P3-014` left this here by name: *"the reason this module does not do so is not a type boundary
but a layering one: `backtest/` may not import `storage/`, and `V2-P3-015` is where a public face
for a factor lives."*

The answer is a **document store keyed by `experiment_id`**, and the shape of it is decided by the
layering rather than around it:

- `experiment_payload` already renders a record as canonical JSON, and `open_experiment` already
  refuses a payload whose content no longer hashes to its seal. So the unit a store has to hold is
  a **string**, and the integrity check is the document's own.
- A store that held `FactorExperimentRecord` would have to import `openalpha_cn.backtest`, which
  `storage-no-upward-deps` forbids and `tests/unit/test_import_layering.py` measures directly on
  the live graph. `storage/factor_experiments.py` therefore takes `(experiment_id,
  content_digest, built_at, payload)` -- three strings and an instant -- and re-derives nothing.
- **The write-once half survives the narrowing, which is the part worth stating.**
  `refuse_a_restated_experiment` refuses an arriving record whose `experiment_id` matches a held
  one and whose `content_digest` does not, and admits an identical one. That rule is a comparison
  of **two digests**: a store that never opens the artifact can still enforce all of it, and
  `FileExperimentStore.put` does. What it cannot do is notice a payload whose seal was recomputed
  beside an edit -- `the_document_store_holds_bytes_and_re_derives_no_number` -- which is
  `the_seal_detects_an_edit_and_does_not_authenticate_one` arriving one plane out.

**Who puts it there is this module**, once, inside `run_factor_experiment`, through the
`ExperimentDocumentStore` `Protocol` -- so nothing here imports `openalpha_cn.storage` either. All
three faces call that one function with the store they were built with, so a run through the CLI,
over HTTP and through the SDK writes the same document under the same key with the same guard. A
face that stored its own result would be three write paths and three chances to differ.

## Layering, and why this is a top-level module rather than a `panel_*` one

It joins `openalpha_cn.backtest` (the five factor leaves and the execution policy),
`openalpha_cn.panel_factors` / `panel_neutralization` / `panel_ingest` / `panel_view` /
`openalpha_cn.panel` (the stored tiers and their inputs) and `openalpha_cn.domain`. That set is
exactly why it is **not** named `panel_*`: `tests/unit/test_panel_ingest_import_isolation.py`
discovers every `src/openalpha_cn/panel_*.py` from the directory and forbids all of them from
reaching `backtest`, and this module's whole job is to reach it. It is a *face*, beside `cli.py`,
`sdk.py` and `api/app.py`, and the direction is theirs -- everything it imports is below it, and
nothing below it imports this. `tests/unit/test_factor_view_layering.py` pins both halves,
including the absence that matters most: no edge into `openalpha_cn.storage`,
`openalpha_cn.runtime`, `openalpha_cn.api` or `openalpha_cn.providers`, so a rendering cannot see
a composition root or a credential.

## What is deliberately not here

**HTTP status codes and exit codes.** `cli.py` owns `FactorExit` and `api/app.py` owns
`FACTOR_HTTP_STATUS`, `panel_view.py`'s split and for its reason: what a channel does about a
refusal is a property of the channel. `FactorViewError.reason` is the fault's *name*, which is
what lets each channel look its own envelope up instead of re-deriving the taxonomy from
`isinstance` checks that drift.

**A fifth vocabulary for "not enough data".** `V2-P3-014` refused to add one and this module
refuses to add a sixth: every tier report carries its four upstream coverage codes intact, and the
only synthesis is the attribution grid's `not_measured`. What this module adds is a refusal for a
question it cannot put at all, which is a different fact with a different remedy.

**A freshness policy for a *run*.** Every panel read `run_factor_experiment` makes passes
`max_staleness=None`. "Is this panel fresh enough to read" is `panel doctor`'s and `data-check`'s
question, and answering it a second time here would be a second source of truth for it -- the same
argument `panel_gate` makes for not building its own `ReadinessRequirement`. A *build* is the one
place that cannot follow the rule, because `compute_factor` takes the requirement rather than
building one, and every requirement builder in `panel_ingest` refuses to default `max_staleness`;
so `factor build` makes the caller state a bound or waive it on the record. See
`factor_build_request`.

## Layering, restated for the builder half

The builder joins no package this module did not already join: `compute_factor`,
`apply_factor_transform` and the three writers are `panel_factors`, `apply_factor_neutralization`
and `load_industry_market_cap_cross_section` are `panel_neutralization`, and the six requirement
builders are `panel_ingest`. `tests/unit/test_factor_view_layering.py::
test_the_factor_face_joins_exactly_the_planes_it_renders` is an **equality**, so a builder that
reached for a composition root or a credential to find a universe would fail there rather than
being reviewed for.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from types import MappingProxyType
from typing import ClassVar, Final, Literal, Protocol, TypeVar
from zoneinfo import ZoneInfo

from openalpha_cn.backtest.execution import (
    AShareExecutionPolicy,
    MarketBar,
    published_limit_fields,
    suspended_at_the_close,
)
from openalpha_cn.backtest.factor_experiment import (
    ATTRIBUTION_CELL_ORDER,
    ATTRIBUTION_VERDICT_ORDER,
    AttributionVerdict,
    FactorExperimentError,
    FactorExperimentRecord,
    TierReport,
    build_factor_experiment,
    experiment_payload,
    open_experiment,
)
from openalpha_cn.backtest.factor_ic import (
    FACTOR_TIER_ORDER,
    MINIMUM_IC_SECURITIES,
    FactorICError,
    FactorICSpec,
    FactorICStudy,
    FactorTier,
    ICCrossSection,
    ICMethod,
    ICPoint,
    neutralized_cross_section,
    processed_cross_section,
    raw_cross_section,
)
from openalpha_cn.backtest.factor_portfolio import (
    FactorPortfolioError,
    PeriodPortfolio,
    QuantilePortfolioSpec,
    QuantilePortfolioStudy,
)
from openalpha_cn.backtest.factor_redundancy import (
    MINIMUM_REDUNDANCY_SECURITIES,
    FactorRedundancyError,
    RedundancyPoint,
    RedundancySpec,
    RedundancyStudy,
    correlate_cross_section,
    factor_vector,
)
from openalpha_cn.backtest.factor_tradeability import (
    FactorTradeabilityError,
    PeriodTradeability,
    SessionLiquidity,
    TradeabilitySpec,
    TradeabilityStudy,
    liquidity_from_amount,
)
from openalpha_cn.domain.adjustment import ADJ_FACTOR_DATASET, AdjustmentError, AdjustmentHistory
from openalpha_cn.domain.daily_prices import (
    DAILY_BASIC_DATASET,
    DAILY_DATASET,
    DailyBar,
    PriceDataError,
)
from openalpha_cn.domain.factor import (
    FactorDefinition,
    FactorError,
    FactorNote,
    FactorObservation,
    FactorRegistry,
)
from openalpha_cn.domain.factor_neutralization import (
    FactorNeutralizationRegistry,
    FactorNeutralizationSpec,
    NeutralizedFactorObservation,
)
from openalpha_cn.domain.factor_transform import (
    FactorTransformRegistry,
    FactorTransformSpec,
    ProcessedFactorObservation,
)
from openalpha_cn.domain.financial_statements import FINANCIAL_STATEMENT_DATASETS
from openalpha_cn.domain.horizon import HorizonError, ResearchHorizon, parse_horizon
from openalpha_cn.domain.index_prices import INDEX_DAILY_DATASET
from openalpha_cn.domain.labels import (
    HaltCorpus,
    LabelError,
    LabelWindow,
    OutcomeLabel,
    build_label_window,
    halt_corpus_for_years,
    label_outcome,
)
from openalpha_cn.domain.name_history import (
    NAMECHANGE_DATASET,
    NameHistory,
    NameHistoryHorizonError,
    RiskWarning,
)
from openalpha_cn.domain.panel_batch import PanelBatchError
from openalpha_cn.domain.price_limits import PriceLimit
from openalpha_cn.domain.stock_universe import (
    STOCK_BASIC_DATASET,
    StockUniverse,
    StockUniverseError,
)
from openalpha_cn.domain.trading_calendar import (
    CalendarDayStatus,
    TradingCalendar,
    TradingCalendarError,
)
from openalpha_cn.panel.catalog import (
    DEFAULT_DATE_TIMEZONE,
    PanelStorageError,
    ReadinessRequirement,
)
from openalpha_cn.panel.store import PanelStore, PartitionRef
from openalpha_cn.panel_factors import (
    FACTOR_DEFINITIONS,
    FACTOR_TRANSFORMS,
    FactorEngineError,
    FactorPanel,
    ProcessedFactorPanel,
    apply_factor_transform,
    compute_factor,
    factor_observation_dataset,
    load_factor_observations,
    load_processed_factor_observations,
    processed_factor_dataset,
    write_factor_panels,
    write_processed_factor_panels,
)
from openalpha_cn.panel_ingest import (
    daily_basic_requirement,
    daily_requirement,
    financial_statement_requirement,
    index_price_requirement,
    load_adjustment_histories,
    load_daily_bars,
    load_name_histories,
    load_price_limits,
    load_stock_universe,
    load_suspensions,
    load_trading_calendar,
)
from openalpha_cn.panel_neutralization import (
    FACTOR_NEUTRALIZATIONS,
    NeutralizationEngineError,
    NeutralizedFactorPanel,
    apply_factor_neutralization,
    load_industry_market_cap_cross_section,
    load_neutralized_factor_observations,
    neutralized_factor_dataset,
    write_neutralized_factor_panels,
)
from openalpha_cn.panel_view import PANEL_STORE_PLACEHOLDER, panel_store

__all__ = [
    "ACCEPTANCE_STEP",
    "ATTRIBUTION_VERDICT_MEANINGS",
    "CATALOG_SCHEMA_VERSION",
    "FACTOR_DATE_ZONE",
    "FACTOR_RUN_LIMITATION_CODES",
    "KNOWN_FACTOR_RUN_LIMITATIONS",
    "REQUIREMENT_BUILDERS",
    "VIEW_SCHEMA_VERSION",
    "ExperimentDocumentStore",
    "ExperimentWrite",
    "FactorBuildReport",
    "FactorBuildRequest",
    "FactorPanelUnreadableError",
    "FactorRequestError",
    "FactorRunBlockedError",
    "FactorRunLimitation",
    "FactorRunRequest",
    "FactorViewError",
    "acceptance_rows",
    "attribution_rows",
    "build_factor_panels",
    "build_rows",
    "build_view",
    "catalog_rows",
    "everything_is_unmeasured",
    "experiment_view",
    "factor_build_request",
    "factor_catalog",
    "factor_entry",
    "factor_request",
    "panel_store",
    "resolve_factor",
    "run_factor_experiment",
    "tier_rows",
]

_T = TypeVar("_T")

TierObservation = FactorObservation | ProcessedFactorObservation | NeutralizedFactorObservation
"""The three stored observation contracts, as the one type this module walks them under.

Every one of them carries `subject`, `as_of`, `value` and `coverage` under those names, which is
what `factor_ic.ic_cross_section`'s `(subject, value, coverage)` projection already relies on. The
union is written down rather than reached through `getattr` so the attribute access is checked:
a fourth tier contract added later fails here rather than at run time on a missing name.
"""


class FactorViewError(RuntimeError):
    """Base for every fault a factor face can report before an artifact exists.

    Carries the two things `PanelViewError` carries and for its reasons -- a `reason` each channel
    looks its own envelope up by, so a fault added here with no row in a channel's table is a
    `KeyError` at that channel's boundary rather than a silently mis-enveloped refusal, and a
    `disclosable` message that may cross a process boundary. The store's filesystem location is
    configuration of the process that holds it, and a response body that echoed it would answer a
    question about the deployment to whoever could reach the port.
    """

    reason: ClassVar[str] = "factor_view_error"

    def __init__(self, message: str, *, disclosable: str | None = None) -> None:
        super().__init__(message)
        self.disclosable: str = message if disclosable is None else disclosable


class FactorRequestError(FactorViewError):
    """The question cannot be put at all, whatever is in the store.

    A factor no registry declares, a range that runs backwards, an `--as-of` with no offset, a
    horizon that does not parse. Distinct from `FactorRunBlockedError` because the remedy is to
    edit the request rather than to build anything.
    """

    reason: ClassVar[str] = "bad_request"


class FactorPanelUnreadableError(FactorViewError):
    """A panel partition this run needs cannot be read at the stated `as_of`.

    The exchange calendar, the registry, the price panel or one of the three factor partitions
    came back blocked. Not a finding, because there is no report to put one on: these are the
    inputs a report would have been derived from.
    """

    reason: ClassVar[str] = "panel_unreadable"


class FactorRunBlockedError(FactorViewError):
    """The stored tiers cannot answer this question as asked, and the refusal is the answer.

    The range holds no stored cross section at all, or the three tiers were not built at the same
    instants, or one tier's studies refused a cross section they were handed. All of them are
    conflicts with the current state of the panel rather than defects in the request, and all of
    them have a remedy that is a build rather than an edit -- which is why they are one row of
    each channel's table and not `bad_request`'s.

    **This is the row that must not wear a 2xx.** `V2-P1-016` measured the shape: an endpoint that
    ran a gate, was refused, and still answered `200` is no gate at all. A run that could not
    assemble an experiment has produced no artifact, and a caller told otherwise would record a
    verdict about a factor nobody measured.
    """

    reason: ClassVar[str] = "blocked"


@dataclass(frozen=True, slots=True, kw_only=True)
class FactorRunLimitation:
    """One named boundary on what a factor run can be trusted to answer."""

    code: str
    detail: str


KNOWN_FACTOR_RUN_LIMITATIONS: Final[tuple[FactorRunLimitation, ...]] = (
    FactorRunLimitation(
        code="a_closed_day_and_an_unclosed_session_share_one_exit_code",
        detail=(
            "V2-P4-109. `openalpha factor build --tier neutralized` at a prediction instant on a "
            "Saturday and at one before an open session's own 16:30 close both exit 1 "
            "(FACTOR_EXIT['blocked'] is PanelExit.unhealthy). PanelExit's own docstring says the "
            "codes exist so a CI job can tell 'go fetch the data again' from 'change your "
            "command line', and those two states are on opposite sides of that line: a Saturday "
            "never becomes a session and no retry produces one, while an unclosed session "
            "becomes readable that afternoon. WHAT WAS FIXED IS THE MESSAGE, NOT THE CODE. Each "
            "state now names only its own remedy (see RESIDUAL_REMEDIES, keyed by the "
            "three-valued CalendarDayStatus), so a human reading the refusal is told the one "
            "thing that helps. A machine switching on the exit code alone still cannot tell them "
            "apart. THE CODE WAS DELIBERATELY NOT SPLIT AND THE REASON IS MEASURABLE: "
            "PanelExit.bad_request means 'no amount of re-fetching fixes it', and a day the "
            "loaded calendar reports `closed` can ALSO be a day whose trade_cal partition is "
            "merely short of it -- the calendar cannot distinguish 'the exchange was shut' from "
            "'this panel does not know that it was open', and for the second, re-fetching IS the "
            "remedy. Answering 3 there would stop a scheduled job retrying a panel a retry would "
            "repair, which is the more expensive of the two mistakes. The third verdict, "
            "`beyond_horizon`, is the one that is unambiguously a fetch, and it says so."
        ),
    ),
    FactorRunLimitation(
        code="the_unbuilt_factor_remedy_fires_only_when_no_year_of_the_tier_is_registered",
        detail=(
            "V2-P4-067(b). A tier read that RAISES -- an empty store, a year no partition "
            "covers, a damaged file -- now carries `openalpha factor build --factor <key> "
            "--tier <tier> --year <year>` on all three tiers and on both faces. WHAT IS NOT "
            "CLAIMED is that a refusal without that line means the panel is well. The remedy "
            "fires on ONE state and deliberately on no other: no year of that tier's "
            "observation partition is registered at all. A store holding SOME year of it can be "
            "short for reasons this layer cannot tell apart -- the requested year is absent and "
            "the engine's own sentence says so, the partition is present but unreadable at the "
            "stated as_of, the rows are there and stale -- and `openalpha factor build` is not "
            "the whole answer to any of those. V2-P4-078 measured the cost of getting this "
            "wrong on the panel plane: a refusal naming a command that does not help is worse "
            "than one naming none, because it sends the caller to rebuild something that is "
            "already built. The tier named in the line is the tier whose READ raised, not the "
            "tier `--tier` asked for: `run_factor_experiment` reads all three whatever the "
            "request says. The dataset is looked up in FACTOR_TIER_DATASETS on both faces, "
            "which is one table restated rather than two rules, so the two faces cannot drift "
            "into naming different partitions for one tier. THIS ENTRY REPLACES "
            "KNOWN_SHORTLIST_VIEW_LIMITATIONS.only_the_raw_tiers_unreadable_factor_refusal_"
            "names_the_command_that_builds_it, whose stated reason -- `neutralized` having two "
            "partition spellings -- was measured false: neutralized_factor_dataset is keyed by "
            "the definition alone and factor_neutmn_* is the manifest dataset, not a second "
            "name for the observations."
        ),
    ),
    FactorRunLimitation(
        code="the_three_tiers_must_have_been_built_at_the_same_instants",
        detail=(
            "An attribution is a difference between two readings of the same days, so "
            "FactorExperimentArtifact requires the three tier reports to carry one as_of tuple. "
            "THE REASON THIS RULE HAD TO EXIST IS NOT THE REASON IT STILL EXISTS, AND V2-P4-026 "
            "IS THE DIFFERENCE. It used to be arithmetic: load_industry_market_cap_cross_section "
            "read daily_basic through read_if_ready, so a residual for any day of year Y could "
            "only be built at an as_of at or after Y's last stored session, and the neutralised "
            "tier COULD NOT be put on the other two's schedule inside a covered year. It can "
            "now -- panel_ingest._read_visible_price_session reads one daily_basic session under "
            "a WHERE available_time <= as_of predicate, and "
            "tests/integration/panel/test_factor_neutralizations.py::"
            "test_a_residual_built_at_a_mid_year_as_of_is_visible_at_that_same_as_of builds one. "
            "So this is now a rule about SCHEDULES rather than a consequence of the storage "
            "contract: whatever instants a neutralisation was in fact built at, a three-tier "
            "report can only be assembled over instants all three tiers carry. The failure shape "
            "is unchanged and is why the rule is a refusal rather than a shrug -- reading a tier "
            "back goes through read_visible_at, which filters rather than refuses, so an as_of no "
            "build was stamped at is EMPTY rather than an error, and a face that shrugged would "
            "report a three-tier artifact whose neutralised row measured nothing. That row is the "
            "one V2-P3-014's acceptance criterion is decided on. ONE refusal outside this module "
            "still bounds which schedules are reachable at all, and it used to be two: no cross "
            "section before 2021-12-13 is assemblable. The other -- index_member_all read whole "
            "partition -- was V2-P4-027's issue and V2-P4-028 put this plane's read of it on a "
            "day-scoped door, so a membership year no longer states a schedule bound either."
        ),
    ),
    FactorRunLimitation(
        code="the_builder_cannot_produce_a_residual_for_a_session_that_has_not_closed",
        detail=(
            "`openalpha factor build` (V2-P3-019) computes and stores the raw and processed tiers "
            "at any prediction instant the panel covers, so a store built only by `openalpha "
            "panel build` now reaches `factor run`. The third tier is narrower than the other "
            "two, and THIS ENTRY HAS BEEN NARROWED TWICE, EACH TIME BY A DATASET LEAVING IT. It "
            "began as the_builder_cannot_produce_a_residual_before_its_years_stored_horizon and "
            "named both foreign reads. V2-P4-026 took daily_basic off it -- that dataset is read "
            "one session at a time under a WHERE available_time <= as_of predicate "
            "(panel_ingest._read_visible_price_session). V2-P4-028 took index_member_all off it: "
            "load_industry_market_cap_cross_section now reads through "
            "panel_ingest.load_industry_cross_section, which takes the DAY as an argument, so a "
            "membership partition whose newest assignment post-dates the as_of no longer refuses "
            "the build. The old entry's bound -- a prediction instant at or after the last stored "
            "ASSIGNMENT of every membership year the read touches, which on a real corpus is the "
            "annual constituent review -- IS GONE, and with it the reason the neutralised tier "
            "was a year-end operation. "
            "WHAT REMAINS IS ONE SESSION WIDE AND IS ARITHMETIC RATHER THAN POLICY. The residual "
            "must carry the processed panel's own instant "
            "(_refuse_a_cross_section_that_is_not_this_panels requires the characteristic cross "
            "section's as_of to equal it exactly), and the cross section is read for the day that "
            "instant falls on -- so `--tier neutralized` at an instant BEFORE that day's own "
            "16:30 close, or on a day the exchange was shut, is refused BY NAME and writes "
            "nothing, rather than storing two tiers and leaving `factor run` to report the third "
            "as an empty in-range read. The remedies are the same two and one of them is now "
            "cheap: move `--as-of` to after the session's close, or build `--tier processed` at "
            "this instant. A second refusal comes from the industry read and is a caller's own "
            "narrowing rather than a horizon: naming fewer `--year`s than the stored membership "
            "years at or before the day leaves an assignment's close unread, which is "
            "KNOWN_NEUTRALIZATION_LIMITATIONS."
            "a_stored_membership_year_left_unread_refuses_the_day_rather_than_answering_it."
        ),
    ),
    FactorRunLimitation(
        code="the_document_store_holds_bytes_and_re_derives_no_number",
        detail=(
            "FileExperimentStore takes an experiment_id, a content_digest and a payload string. "
            "It enforces the whole of refuse_a_restated_experiment on the two digests -- a second "
            "answer under one experiment_id is refused, an identical one is a no-op -- and it "
            "opens no artifact, because storage/ may not import backtest/. So a payload whose "
            "seal was recomputed beside an edit is admitted by the store and refused by "
            "open_experiment, exactly as the_seal_detects_an_edit_and_does_not_authenticate_one "
            "says one plane in. The store is an integrity boundary against a partial write and "
            "against a clobber, never against an author."
        ),
    ),
    FactorRunLimitation(
        code="a_run_is_evaluated_at_one_as_of_and_the_labels_are_read_at_it",
        detail=(
            "Every panel read this module makes is at the run's own --as-of, which is "
            "necessarily at or after the last prediction day: a forward return is priced on "
            "sessions that had not happened when the factor value was stamped. So the labels, the "
            "bars, the bands, the halt corpus and the registry are read with hindsight relative "
            "to each cross section, and that is what a backtest is. What it is not is a "
            "point-in-time claim about the label: nothing here says the return was knowable at "
            "the as_of it is correlated against, only that the factor value was. The factor "
            "values' own visibility is the panel plane's guarantee and is unchanged -- each "
            "stored row was computed from what read_visible_at admitted at its own instant."
        ),
    ),
    FactorRunLimitation(
        code="the_shipped_transform_and_neutralisation_floors_exceed_a_thin_market",
        detail=(
            "CROSS_SECTION_STANDARD declares min_cross_section=100 (derived: 1 / its 1% "
            "winsorization quantile) and INDUSTRY_AND_SIZE declares min_cross_section=100 as "
            "well. On a market narrower than that, both derived tiers store a coverage code for "
            "every name and no value, so the processed and neutralised rows of the report are "
            "measured-nothing rows and every attribution cell is not_measured -- a correct report "
            "of a build that could not standardize, and not a defect in this face. This module "
            "declares no floor of its own and cannot lower theirs; what it does is carry the "
            "codes through so the reason is readable rather than inferred from an absent number."
        ),
    ),
    FactorRunLimitation(
        code="a_name_never_announced_inside_the_requested_years_is_priced_as_ordinary",
        detail=(
            "MarketBar.is_st is read off the stored rename corpus, and load_name_histories is "
            "scoped to the announcement years the run asked for. A security with no row in those "
            "years has no NameHistory at all, and _risk_warned_on answers False for it -- which "
            "is right for a name that has not been renamed and WRONG for one that was put under "
            "special treatment in an earlier year and is still under it, because a rename is "
            "dated at its announcement and an announcement made in an earlier year is in a "
            "partition this read did not cover. V2-P4-080 fixed the neighbouring case and "
            "deliberately did not fix this one: a security whose earliest row in the requested "
            "years takes effect AFTER the session being priced is refused by name, because the "
            "corpus positively shows a rename this run cannot resolve. Absence shows nothing, and "
            "refusing on it would refuse almost every honest run -- most of the market has no "
            "rename announced in any one year. The remedy available to a caller is the same one "
            "the refusal names: ask for the announcement years that cover the security's last "
            "rename. panel_ingest.load_name_histories states the same bound from the read's side."
        ),
    ),
    FactorRunLimitation(
        code="a_security_with_no_stored_adjustment_history_is_counted_unmatched_not_refused",
        detail=(
            "_PanelInputs.label answers None for a security load_adjustment_histories returned no "
            "AdjustmentHistory for, so the name is left out of the label map and counted by "
            "ICCensus.unmatched_count rather than refusing the run. V2-P4-084 fixed the "
            "neighbouring case and deliberately did not fix this one, which is the same line "
            "V2-P4-080 drew for the rename corpus: a history that EXISTS and stops before the "
            "window is refused by name, because the corpus positively shows a span this run "
            "cannot price across, while an absent history shows nothing about why. What it costs "
            "is that two different situations reach one census cell -- a security the feed has no "
            "factors for at all, and one whose adj_factor year simply was not built -- and the "
            "count cannot separate them. The bound is narrower than it looks: panel_doctor's "
            "SUBJECT_CONTAINMENTS reports daily subjects absent from adj_factor as "
            "subject_set_disagreement, so a panel where this is happening at scale is already "
            "unhealthy before a run is asked, and 'a security with a bar and no adjustment factor "
            "cannot be price-adjusted' is that rule's own sentence."
        ),
    ),
    FactorRunLimitation(
        code="the_freshness_bar_is_waived_by_cadence_only_where_the_read_is_outside_the_engine",
        detail=(
            "V2-P4-064 took --max-staleness-days off the registry read (CADENCE_WAIVED_READS) "
            "and two reads it also reaches keep it. (1) The four quarterly statement datasets "
            "take the "
            "caller's session-cadence bar unchanged, so a build of a statement factor under "
            "--max-staleness-days 5 is refused for a partition whose newest announcement is "
            "older than five days -- which for a quarterly dataset is most of the year. That "
            "cannot be waived here: compute_factor._validate_requirements refuses a waived "
            "max_staleness for every dataset a factor READS, because a waived bound accepts a "
            "slice reaching arbitrarily far short of as_of while every structural check clears. "
            "Closing it means a per-cadence bound inside REQUIREMENT_BUILDERS' requirements, "
            "which is a change to what compute_factor is handed rather than to this flag. (2) "
            "index_member_all is event-driven and takes the bar anyway, because "
            "load_industry_market_cap_cross_section states ONE max_staleness for both it and "
            "daily_basic, which is on the session clock; splitting it is an edit to "
            "panel_neutralization.py. The generated fixture cannot show (2) -- measured, a "
            "--tier neutralized build is exit 0 at both 5 and 30 days on it -- because its "
            "membership partition's newest assignment sits inside the build window, while a real "
            "corpus's is the last annual constituent review."
        ),
    ),
)
"""What a factor run does not answer, as a closed registry rather than as prose.

Every entry is bound to the suite by `tests/unit/test_known_limitation_registries.py`, which
requires each `code` to appear as a string literal in *executable* test code -- the P2 review
measured that a code named only in docstrings can be renamed with the whole suite staying green.
"""

FACTOR_RUN_LIMITATION_CODES: Final[frozenset[str]] = frozenset(
    limitation.code for limitation in KNOWN_FACTOR_RUN_LIMITATIONS
)

FACTOR_DATE_ZONE: Final[ZoneInfo] = ZoneInfo(DEFAULT_DATE_TIMEZONE)
"""The zone a prediction day is dated in, taken from `panel/catalog.py` rather than restated.

Every stored panel date is derived in it, so a range bound compared in any other zone would
include or exclude a session by an artefact of the question rather than by the caller's request.
"""

VIEW_SCHEMA_VERSION: Final[str] = "factor-experiment-view/v1"
"""The version of the envelope `experiment_view` puts around a sealed document.

This envelope's own, and deliberately not the record's: the document inside carries three versions
of its own (`factor-experiment-record/v1`, `factor-experiment-artifact/v1`,
`factor-experiment/v1`) and they version different things. A face that reused one of those would
make a change to the transport look like a change to the contract.
"""

MISSING_INSTANTS_SHOWN: Final[int] = 5
"""How many missing instants a tier-schedule refusal names before it counts the rest.

A year is 244 prediction days, so a refusal that listed every one of them would be unreadable in a
terminal and unusable in a log line; a refusal that listed none would be a code with no remedy
attached. Five is the smallest number that still shows a *pattern* -- consecutive sessions read
differently from one isolated gap -- which is the actionable half.
"""

ExperimentWrite = Literal["created", "unchanged"]
"""What the document store did with an arriving artifact.

Two members and not three, because the third -- "refused" -- is an exception rather than an
outcome: a second answer under one `experiment_id` is a build that did not reproduce, and
`refuse_a_restated_experiment`'s whole argument is that the honest reading is a refusal naming
both digests rather than a second row a reader has to choose between.
"""


class ExperimentDocumentStore(Protocol):
    """The byte store a sealed experiment document is handed to.

    A `Protocol` so this module imports no store: `storage/factor_experiments.py` satisfies it
    structurally, and the import graph stays `factor_view -> {backtest, panel_*, domain}` with no
    edge into `openalpha_cn.storage`. `backtest/validation.py::ValidationStore` is the precedent --
    the Protocol lives beside the consumer and declares exactly the methods that consumer calls.

    Every parameter is a string or an instant. Nothing here has an opinion about what a payload
    means, which is what keeps the store below `openalpha_cn.backtest` rather than above it.
    """

    def put(
        self, *, experiment_id: str, content_digest: str, built_at: datetime, payload: str
    ) -> ExperimentWrite:
        """Hold `payload`, refusing a second answer under a held `experiment_id`."""

    def get(self, experiment_id: str) -> str | None:
        """The held payload for `experiment_id`, or `None`."""

    def list_ids(self) -> tuple[str, ...]:
        """Every held `experiment_id`, ascending."""


@dataclass(frozen=True, slots=True, kw_only=True)
class FactorRunRequest:
    """One resolved factor run: which factor, which days, which policies, at which instant.

    Built only by `factor_request`, which is what makes the three faces ask one question. Every
    field is a decision that moves the answers, and none of them has a default here for
    `FactorICSpec`'s stated reason -- a default is a decision nobody recorded making, and the four
    upstream specs each already refuse one for their own floors.
    """

    definition: FactorDefinition
    transform: FactorTransformSpec
    neutralization: FactorNeutralizationSpec
    start: date
    """The first prediction day in the range, inclusive, dated in `FACTOR_DATE_ZONE`.

    A **prediction day**: the day a stored cross section was computed at, not a session the return
    is priced on and not the instant the experiment is evaluated at. See this module's docstring
    for why the three clocks are separate.
    """
    end: date
    """The last prediction day in the range, inclusive."""
    as_of: datetime
    """The instant every panel read in this run is made at, and the instant the experiment is
    evaluated at. At or after `end`; see
    `a_run_is_evaluated_at_one_as_of_and_the_labels_are_read_at_it`."""
    exchange: str
    horizon: ResearchHorizon
    ic: FactorICSpec
    portfolio: QuantilePortfolioSpec
    tradeability: TradeabilitySpec
    survival: RedundancySpec
    retention_floor: float
    code_commit: str

    @property
    def years(self) -> tuple[int, ...]:
        """Every partition year the range touches, ascending."""
        return tuple(range(self.start.year, self.end.year + 1))


def resolve_factor(
    token: str, *, registry: FactorRegistry = FACTOR_DEFINITIONS
) -> FactorDefinition:
    """The definition `--factor <id>` names: a qualified key, or a `factor_id`.

    Told apart by shape rather than by a flag. `FactorDefinition.key` is constrained to a plain
    panel identifier precisely so that `qualified_key` can split on `/`, so a token containing one
    is `key/vN` and every other token is a content address. Both directions are supported for the
    reason this module's docstring gives: a human types the key, and a reader holding a stored
    observation has only the id.

    Refuses with `FactorRequestError` rather than letting `FactorError` out, because a face wants
    one exception type for "this request cannot be put" whatever part of it was wrong.

    **The two refusals do not carry the same list, and that is the correction `V2-P3-019` made.**
    `FactorRegistry.get` names every declared `qualified_key`, which is the actionable half of a
    mistyped key. `FactorRegistry.by_id` names every declared `factor_id`, which is the actionable
    half for a reader holding a stored observation and is *useless* to the caller who reaches it
    most often -- somebody who typed `--factor ep` and got nineteen opaque content addresses back
    from a help text that had just told them "the key is the form for a human". So the
    content-address branch is re-worded here rather than passed through: it keeps the registry's
    own sentence, names the **keys**, and points at `openalpha factor list`, which is where both
    spellings and every factor's prose actually live.
    """
    name = token.strip()
    if not name:
        raise FactorRequestError(
            "--factor names no factor; give a qualified key (`reversal_1d/v1`) or a factor_id "
            "(`fct_...`) this build declares. `openalpha factor list` prints every one of them"
        )
    if "/" in name:
        try:
            return registry.get(name)
        except FactorError as error:
            raise FactorRequestError(str(error)) from error
    try:
        return registry.by_id(name)
    except FactorError as error:
        raise FactorRequestError(
            f"{name!r} is not a factor this build declares. A --factor is a qualified key -- this "
            f"build knows {list(registry.qualified_keys)} -- or the fct_ content address a stored "
            "partition carries. The keys are listed here and the addresses are not: a caller "
            "holding an address already has it, and a caller who mistyped a key needs the keys. "
            "`openalpha factor list` prints both, beside what each factor reads, and `openalpha "
            "factor describe --factor <key>` prints what its note says it does not measure"
        ) from error


def factor_request(
    *,
    factor: str,
    transform: str,
    neutralization: str,
    start: date,
    end: date,
    as_of: datetime,
    exchange: str,
    horizon: str,
    ic_method: ICMethod,
    min_securities: int,
    min_as_ofs: int,
    group_count: int,
    min_securities_per_group: int,
    position_capital: Decimal,
    min_periods: int,
    participation_cap: Decimal,
    min_rebalances: int,
    redundancy_threshold: float,
    retention_floor: float,
    code_commit: str,
    factors: FactorRegistry = FACTOR_DEFINITIONS,
    transforms: FactorTransformRegistry = FACTOR_TRANSFORMS,
    neutralizations: FactorNeutralizationRegistry = FACTOR_NEUTRALIZATIONS,
) -> FactorRunRequest:
    """Resolve one face's parameters into the stated request all three of them ask.

    The three registries are parameters with the build's own as defaults, `compute_factor`'s
    `evaluators` arrangement: no face passes them, so `openalpha factor run`, `POST
    /api/v1/factors/run` and `OpenAlphaSDK.run_factor_experiment` all resolve against the same
    three declarations. They are parameters at all so a study over a probe transform can be driven
    without a second resolver coming into existence to drive it.

    Nothing is inferred. `--start`/`--end` are the caller's assertion about which prediction days
    to report on rather than a reading of which ones are stored -- `_PANEL_YEAR_QUERY`'s rule one
    plane over, and for its reason: passing the stored range would make "there is nothing here"
    unreachable by construction.

    Every fault raised here is `FactorRequestError`. Nothing in this function touches a store, so
    nothing it can say is a statement about the panel.
    """
    definition = resolve_factor(factor, registry=factors)
    try:
        transform_spec = transforms.get(transform.strip())
        neutralization_spec = neutralizations.get(neutralization.strip())
    except ValueError as error:
        raise FactorRequestError(str(error)) from error
    if end < start:
        raise FactorRequestError(
            f"--start {start.isoformat()} is after --end {end.isoformat()}; a closed range of "
            "prediction days runs forwards"
        )
    if as_of.tzinfo is None or as_of.utcoffset() is None:
        raise FactorRequestError(
            f"--as-of must be a timezone-aware instant; got {as_of.isoformat()!r}. A "
            "point-in-time question answered in a guessed timezone is wrong by up to a session"
        )
    if as_of.astimezone(FACTOR_DATE_ZONE).date() < end:
        raise FactorRequestError(
            f"--as-of {as_of.isoformat()} falls before --end {end.isoformat()} in "
            f"{FACTOR_DATE_ZONE}; a forward return is priced on sessions after its prediction "
            "day, so an experiment cannot be evaluated at an instant its own labels had not "
            "reached"
        )
    if type(exchange) is not str or not exchange or exchange != exchange.strip():
        raise FactorRequestError(
            f"exchange must be a non-empty name with no surrounding whitespace; got {exchange!r}"
        )
    try:
        parsed = parse_horizon(horizon)
    except HorizonError as error:
        raise FactorRequestError(str(error)) from error
    if not 0.0 < retention_floor <= 1.0:
        raise FactorRequestError(
            f"--retention-floor must be in (0, 1]; got {retention_floor!r}. A floor of zero calls "
            "every non-negative retention `survives` and would make the acceptance criterion's "
            "cell unreachable"
        )
    if len(code_commit.strip()) < 7:
        raise FactorRequestError(
            f"--code-commit must be at least 7 characters; got {code_commit!r}. Different code "
            "may compute a different number from the same rows, so an identity that ignored it "
            "would claim a reproducibility it cannot deliver"
        )
    if min_securities < MINIMUM_REDUNDANCY_SECURITIES:
        # V2-P4-104: stated here rather than left to the two `Field(ge=...)` bounds below.
        # One option feeds two studies with different floors, so a caller passing 3 used to be
        # refused by whichever spec happened to be constructed first -- `FactorICSpec` at 2 and
        # `RedundancySpec` at 3 -- and the message was pydantic's own: a model name the caller
        # has never heard of, a bound with no unit, and a link to a validation library. Neither
        # branch contained the string `--min-securities`, which is the only thing the caller can
        # actually edit.
        raise FactorRequestError(
            f"--min-securities must be at least {MINIMUM_REDUNDANCY_SECURITIES}; got "
            f"{min_securities!r}. This one option feeds two studies with different floors and "
            f"the higher one binds: the information coefficient needs "
            f"{MINIMUM_IC_SECURITIES} securities (the first cross section at which a "
            f"correlation of magnitude below one is attainable at all), and the redundancy "
            f"study needs {MINIMUM_REDUNDANCY_SECURITIES}, because at "
            f"{MINIMUM_REDUNDANCY_SECURITIES - 1} an untied rank correlation can only be "
            "+-0.5 or +-1, so no --redundancy-threshold at or below 0.5 distinguishes anything "
            "and the survival row would call every pair redundant"
        )
    try:
        ic = FactorICSpec(
            definition=definition,
            method=ic_method,
            min_securities=min_securities,
            min_as_ofs=min_as_ofs,
        )
        quantile = QuantilePortfolioSpec(
            definition=definition,
            group_count=group_count,
            min_securities_per_group=min_securities_per_group,
            position_capital=position_capital,
            min_periods=min_periods,
        )
        tradeability = TradeabilitySpec(
            participation_cap=participation_cap, min_rebalances=min_rebalances
        )
        survival = RedundancySpec(
            method=ic_method,
            min_securities=min_securities,
            min_as_ofs=min_as_ofs,
            redundancy_threshold=redundancy_threshold,
        )
    except ValueError as error:
        raise FactorRequestError(str(error)) from error
    return FactorRunRequest(
        definition=definition,
        transform=transform_spec,
        neutralization=neutralization_spec,
        start=start,
        end=end,
        as_of=as_of,
        exchange=exchange,
        horizon=parsed,
        ic=ic,
        portfolio=quantile,
        tradeability=tradeability,
        survival=survival,
        retention_floor=retention_floor,
        code_commit=code_commit.strip(),
    )


_PANEL_FAULTS: Final[tuple[type[Exception], ...]] = (
    PanelStorageError,
    FactorEngineError,
    NeutralizationEngineError,
    TradingCalendarError,
)
"""The refusals a stored panel raises when it cannot answer a read, mapped to `panel_unreadable`.

Every one of them is a statement about a *partition* -- missing, damaged, unprofiled, stale, or
holding rows that were not knowable at the requested instant. None of them can carry a credential:
the loaders never see a token and the batches' own `source_uri` is
`tushare://{dataset}/{subject}/{date}`. `cli._PANEL_WRITE_REFUSALS` is the precedent and the
equality it is pinned under is the same idea -- which exceptions are facts about data rather than
defects in the code that read it is one question with one answer.
"""

_REGISTRY_FAULTS: Final[tuple[type[Exception], ...]] = (
    *_PANEL_FAULTS,
    StockUniverseError,
    PanelBatchError,
)
"""The two further refusals the **registry** read can raise, and why they are not in the tuple
above.

`load_stock_universe` is the one read in this module that can fail with a statement about the
stored registry's *shape* rather than about its partitions: `StockUniverseError` for an orphan
delisting row, a duplicated `ts_code` or a security filed against two exchanges, and
`PanelBatchError` for a lifecycle year the read was told to cover and does not. Both are facts
about data, so both belong under `panel_unreadable` -- which is the answer
`cli._PANEL_WRITE_REFUSALS` and `panel_doctor._LOAD_FAILURES` have given for eleven error types,
pinned equal to each other, and which `_PANEL_FAULTS` cites as its precedent while listing
neither.

`V2-P4-060` is what that omission cost. A market carrying an ordinary mid-window delisting
reached `cli._panel_command` as an *unanticipated* `StockUniverseError`, so `factor build` exited
5 -- "a defect in the command, not a verdict about the panel ... nothing was checked" -- and the
exception's own message, the sentence naming the security and saying "this is a partial read, not
a security that appeared already delisted", was withheld because an unanticipated frame can be
holding the credential. The withholding is right and stays. What was wrong is that this failure
was unanticipated.

**Narrower than adding the two to `_PANEL_FAULTS`, deliberately.** That tuple also guards
`compute_factor`, the transform and neutralisation engines and every factor-partition read, and a
`PanelBatchError` out of one of those is a defect in this repository's own batch assembly rather
than a verdict about stored data. Laundering it into "the panel could not be read" would be the
same mistake pointing the other way. The read that can raise them is the read that catches them.

**The sentence that used to end this docstring was false, and `V2-P4-070` is what it cost.** It
read: "That is also what leaves `shortlist_view._PANEL_FAULTS` alone, which
`tests/unit/test_shortlist_view.py` pins equal to `_PANEL_FAULTS` so two faces cannot file one
broken partition under two status codes. The equality is still true and still says what it said."
The equality was true and it said nothing. Widening at the read rather than at the constant is the
right call and stands -- but it is exactly what a constant-to-constant assertion cannot see, so
what the pin actually held was that two names had four members each, while the shortlist face went
on reading the registry through its own four and letting these two escape. One store carrying an
interrupted registry backfill therefore reached a user as `exit 1` and a named sentence here,
`exit 5` and "an unhandled StockUniverseError ... the message is withheld" from `shortlist run`,
and `500 text/plain` from `POST /api/v1/shortlists/run`.

`shortlist_view` now restates `_REGISTRY_FAULTS` beside its own `_PANEL_FAULTS` and reads the
registry through its own `_read_registry`, and the pin drives **both faces' read seams** with each
fault type in turn rather than comparing the tuples. See
`tests/integration/test_partial_registry_faces.py` for the same store met from all three surfaces.
"""


_CROSS_SECTION_FAULTS: Final[tuple[type[Exception], ...]] = (*_PANEL_FAULTS, PriceDataError)
"""The one further refusal the **neutralisation cross section** can raise (`V2-P4-108`).

`load_industry_market_cap_cross_section` reads `daily_basic` for the day being priced through
`panel_ingest._read_visible_price_session`, and that door's first guard is the calendar: a day the
exchange was shut has no cross section to read, and it says so as a `PriceDataError`. That is a
verdict about the request meeting a stored calendar, and it was not in `_PANEL_FAULTS`, so
`openalpha factor build --tier neutralized --as-of <a Saturday>` exited **5** -- "a defect in the
command, not a verdict about the panel ... nothing was checked" -- with the refusal's own sentence
withheld, because an unanticipated frame can be holding the credential. `V2-P4-060`'s shape, one
refusal over: the withholding was right and the fault being unanticipated was not.

**Widened at the read rather than in `_PANEL_FAULTS`, and both alternatives were checked rather
than assumed.**

- **`_PANEL_FAULTS` itself must not grow this.** That tuple guards `compute_factor`, both derived
  engines and every factor-partition read, and it is *restated* by `shortlist_view` --
  `tests/unit/test_shortlist_view.py::
  test_this_face_calls_the_same_panel_faults_unreadable_as_the_factor_face` drives the **union**
  of the two modules' tuples through **both** faces' `_read`, so a member added here alone turns
  that pin red on the other face. The pin is right and the arrangement it enforces is
  `_REGISTRY_FAULTS`' own: the read that can raise a refusal is the read that catches it.
- **`_REGISTRY_FAULTS` does not widen alongside.** It is `(*_PANEL_FAULTS, ...)`, so it would have
  inherited this member automatically had the constant grown -- and it should not: `_read_registry`
  wraps `load_stock_universe`, which reads an event-dated lifecycle partition and asks no calendar
  whether a day is open. A fault a read cannot raise, listed in that read's tuple, is a dead entry
  of exactly the kind `test_the_registry_read_is_the_only_site_either_face_widens_for` refuses.
- **The tier is the whole of the hole**, measured at the same instant: `--tier raw` and
  `--tier processed` both exit `0` there, because neither reads a session-scoped price partition
  for the day being priced.

**Whether `shortlist_view` has the same hole is a separate question and is not closed here** --
that module is not this one's to edit. What can be said from this side: its price reads resolve
their session through `panel_ingest.newest_published_session`, which returns an **open** session by
construction, so this particular arm is not reachable the way it is here.
"""


def _read(
    reader: Callable[[], _T],
    *,
    store: PanelStore,
    what: str,
    remedy: str = "",
    faults: tuple[type[Exception], ...] = _PANEL_FAULTS,
) -> _T:
    """Run one panel read, turning its refusal into `FactorPanelUnreadableError`.

    The local message names the store and `disclosable` does not, `panel_view.stored_calendar`'s
    arrangement and for its reason: the CLI and the SDK are inside the process that owns the store
    and a message naming it tells them nothing they did not configure, while a response body hands
    that path to whoever could reach the port.

    `faults` is every read's answer by default and is widened by exactly one caller; see
    `_REGISTRY_FAULTS` for why the registry's two extra refusals go to the read that raises them
    rather than to all fourteen.

    `remedy` is the command line that repairs the state this read met, appended to **both**
    messages -- `V2-P4-067(b)`, on the face its own reproduction command names. Enveloping here
    rather than in `panel_factors` is what lets a refusal carry one at all, for the reason
    `_written`'s docstring gives: the engine raises the same sentence for a partition that was
    never built and for one that is damaged, and only this layer knows which command the caller
    ran. It is computed by `_unbuilt_factor_remedy` and is `""` whenever that function cannot
    name a command that would help.
    """
    try:
        return reader()
    except faults as error:
        raise FactorPanelUnreadableError(
            f"{what} could not be read out of {store.root}: {error}{remedy}",
            disclosable=(
                f"{what} could not be read out of {PANEL_STORE_PLACEHOLDER}: "
                f"{_without_store_path(str(error), store)}{remedy}"
            ),
        ) from error


FACTOR_TIER_DATASETS: Final[Mapping[str, Callable[[FactorDefinition], str]]] = MappingProxyType(
    {
        "raw": factor_observation_dataset,
        "processed": processed_factor_dataset,
        "neutralized": neutralized_factor_dataset,
    }
)
"""Each tier's observation dataset, as the one function that names it.

**All three take the definition and nothing else, and that is the measured fact that decided
`V2-P4-067(b)`'s boundary.** `shortlist_view._unbuilt_factor_remedy` shipped covering `raw` alone
and justified it by `neutralized` having "two partition spellings depending on the declared
neutralization (`factor_neut_*` and `factor_neutmn_*`)". There is no second spelling:
`factor_neutmn_*` is the **manifest** dataset, a sibling of the observations rather than an
alternative name for them, and `load_neutralized_factor_observations` says in its own docstring
that "the neutralisation is a filter here and the factor is the dataset". `factor_proc_*` and
`factor_procmn_*` are the same arrangement one plane down. So a `registered_years` question asked
about any tier is about the only partition that tier's observations can be in, and the failure
mode the old boundary was drawn to avoid -- answering "nothing is stored" for a panel that holds
the other spelling -- cannot occur.
`tests/integration/test_factor_unbuilt_remedy.py::
test_a_tier_stored_under_one_neutralisation_is_found_by_a_request_naming_another` drives that
rather than restating it: residuals written under one neutralisation are read back by a request
naming another, and the refusal that follows names no build command because the partition is
there.

A mapping rather than three `if`s, so `FACTOR_TIER_ORDER` and this table can be held equal by a
test -- a tier added to the vocabulary with no dataset function here is a tier whose refusal
would silently go back to naming nothing.
"""


FACTOR_TIER_BUILD_ARGUMENTS: Final[Mapping[str, str]] = MappingProxyType(
    {
        "raw": " --as-of <instant> --max-staleness-days <days>",
        "processed": " --as-of <instant> --max-staleness-days <days> --transform <transform>",
        "neutralized": (
            " --as-of <instant> --max-staleness-days <days>"
            " --transform <transform> --neutralization <key>"
        ),
    }
)
"""What each tier's `factor build` needs beyond `--factor`, `--tier` and `--year`.

**`V2-P5-048`, and it was found by executing the refusal rather than by reading it.** The remedy
this module prints said `openalpha factor build --factor … --tier <tier> --year <year>` and
stopped there, and that line does not run for **any** tier. **Three separate flags, found one at
a time by executing it**, which is why the count is worth writing down: `--as-of` is required, so
all three exited `2` with `Missing option '--as-of'`; then every tier exited `3` on
`--max-staleness-days`, which is refused-if-absent rather than Click-required and so is invisible
to any check that reads `Parameter.required`; then `processed` and `neutralized` exited `3` on
`--transform is required for --tier processed` and `--neutralization is required for --tier
neutralized`. All three refusals say in their own text that there is no honest default. A refusal
that names a remedy is making a claim about that remedy, and this one sent a reader from a
refusal about a missing partition to three more about missing flags.

The values are angle-bracket placeholders rather than invented literals for exactly the reason
`factor build` refuses a default: an instant, a transform and a neutralisation are declarations
about a study, and a message that guessed them would be answering a question it was not asked.
What the reader gets is the shape of the command with nothing required left out of it, which is
the most a refusal can honestly offer.

`tests/integration/test_documented_command_lines.py::
test_the_remedy_a_missing_neutralized_tier_prints_names_every_option_that_tier_requires` holds
this against the **live parameter list** rather than against a copy of it, so a `factor build`
option that becomes required with no row here is red.
"""


def _unbuilt_factor_remedy(store: PanelStore, *, definition: FactorDefinition, tier: str) -> str:
    """The `factor build` line for a tier this panel holds no partition of, or `""`.

    `shortlist_view._unbuilt_dataset_remedy`'s bound, transplanted with its reason intact: it
    fires on "no year of this tier is registered at all" and on nothing else. A store that holds
    *some* year of it can be short for reasons this function cannot tell apart -- the requested
    year is absent and the read says so itself -- and a refusal that names a command which does
    not help is worse than one that names none, which is `V2-P4-078`'s finding and the trap this
    bound is one line away from.

    `tier` is the tier whose **read raised**, not the tier the request named:
    `run_factor_experiment` reads all three whatever `--tier` says, so the actionable line is the
    one that builds the
    partition that could not be opened. Looked up in `FACTOR_TIER_DATASETS` rather than spelled,
    for `_unbuilt_dataset_remedy`'s reason: a hand-written prefix is exactly where a plane's
    naming rule and a message come apart.
    """
    dataset = FACTOR_TIER_DATASETS.get(tier)
    if dataset is None or store.registered_years(dataset(definition)):
        return ""
    return (
        f". No {tier} partition of this factor is registered in this panel at all. Build it "
        f"first: `openalpha factor build --factor {definition.qualified_key} --tier {tier} "
        f"--year <year>{FACTOR_TIER_BUILD_ARGUMENTS[tier]}`"
    )


def _read_registry(reader: Callable[[], StockUniverse], *, store: PanelStore) -> StockUniverse:
    """The registry read, in one place because it is one read made twice.

    `factor build` makes it per prediction instant and `factor run` once per run, and before
    `V2-P4-060` the two sites duplicated the `what=` string and the fault list between them --
    which is how one of them can come to catch a refusal the other lets escape. Both go through
    here now, so `_REGISTRY_FAULTS` is stated once and applies to whichever face made the call.
    """
    return _read(reader, store=store, what="the security registry", faults=_REGISTRY_FAULTS)


def _risk_warned_on(
    history: NameHistory | None,
    *,
    subject: str,
    session: date,
    store: PanelStore,
    years: Sequence[int],
) -> bool:
    """`MarketBar.is_st` for one security on one session, or a refusal naming both.

    `shortlist_view._risk_warned_on` restated, `_PANEL_FAULTS`' arrangement and its reason: which
    refusals are facts about stored data rather than defects in the code that read them is one
    question with one answer, and two faces that answered it differently would put the same corpus
    under two status codes on two channels.

    ## Why this raises rather than defaulting, and `V2-P4-080` is why it raises *here*

    `NameHistory.record_on` refuses a day before its first record on purpose -- "an unrecorded name
    is unknown rather than equal to the earliest one on file" -- and `NameHistoryHorizonError`'s
    own docstring says that refusal is a verdict rather than a caller mistake. Until `V2-P4-080`
    the call sat bare inside `MarketBar(...)`, outside every `_read` guard, so `_PANEL_FAULTS`
    never saw it: `factor run` over an ordinary two-clock rename exited `5` with the sentence
    naming the security withheld, and `shortlist run` and `POST /api/v1/shortlists/run` did the
    same thing on the same corpus. That is `V2-P4-070`'s shape one dataset over, and this is the
    seam it is anticipated at.

    Answering `False` in the caller is what the crash replaced and it would have been the worse
    of the two, but **not for the reason first written here**, which was measured false before it
    shipped. The first draft said the security would be sized under a band the exchange may not
    apply to it. It would not: `MarketBar.is_st` is read at exactly one site in this repository --
    `backtest/execution._price_band`, `ratio = Decimal("0.05") if market.is_st else
    _board_limit(market.board)` -- and only when `up_limit`/`down_limit` are absent. Both faces
    build every bar with `published_limit_fields(limit)` and build none at all when the limit is
    missing, so the derivation is unreachable from here and a wrong `is_st` moves no verdict today.

    What is wrong with `False` is what it *records*. `is_st` is a field whose contract is "the
    risk-warning state read off the name in effect on this session", stated in this module and
    written out rather than hidden in a truth test precisely so the collapse is visible; writing
    `False` into it for a security whose name nobody has would make the bar assert something the
    corpus does not support, and no reader of the bar could tell that apart from a name measured
    ordinary. It is latent rather than harmless: `KNOWN_CROSS_SECTION_LIMITATIONS.
    an_absent_published_band_is_refused_here_and_derived_by_the_execution_policy` records that the
    two planes already disagree about an absent band -- the funnel codes it `unbanded` and the
    policy derives one -- so the derivation is live code one caller away, and it is measurably
    wrong on 159 of the 5,338 priced names of 2024-06-28 (`domain/price_limits.py`).

    Returning `None` from `market_bar` instead was the other candidate and is worse in a quieter
    way: `None` there means "no stored bar or no published band", which `QuantilePortfolioStudy`
    counts as `unbarred`, so an unknown name would be reported as an unpriced one and the census
    would carry a sentence that is not true of it.

    **`history is None` is deliberately still `False`, and it is a different state.** A security
    with no row in the announcement years read has had no rename announced in them, which is the
    ordinary condition of most of the market; a security whose earliest row takes effect *after*
    the session has had one, and the name it traded under before it is outside the corpus. The
    residue that leaves is disclosed as
    `a_name_never_announced_inside_the_requested_years_is_priced_as_ordinary`.
    """
    if history is None:
        return False
    try:
        return history.risk_warning_on(session) is not RiskWarning.none
    except NameHistoryHorizonError as error:
        raise FactorPanelUnreadableError(
            _unnamed_session_refusal(
                str(store.root), subject=subject, session=session, years=years, error=error
            ),
            disclosable=_unnamed_session_refusal(
                PANEL_STORE_PLACEHOLDER,
                subject=subject,
                session=session,
                years=years,
                error=error,
            ),
        ) from error


def _unnamed_session_refusal(
    where: str,
    *,
    subject: str,
    session: date,
    years: Sequence[int],
    error: NameHistoryHorizonError,
) -> str:
    """What this face says when the rename corpus reaches no name for a priced session.

    `shortlist_view._unnamed_session_refusal` restated word for word, and the restatement is a
    choice with a cost. `V2-P4-060`'s own lesson is that two sites duplicating a `what=` string and
    a fault list is how one of them comes to catch a refusal the other lets escape -- which is why
    `_read_registry` exists *within* this module. Across the two face modules there is no shared
    home that is not a new import edge between two faces, so the duplication stays and is held by
    an executable pin instead: `tests/unit/test_shortlist_view.py::
    test_both_faces_refuse_an_unnamed_session_with_the_same_sentence` drives both seams with one
    history and requires the two messages to be the same string, which is the form `V2-P4-070`
    proved a constant-to-constant assertion is not.

    `where` is the store's own location on the message carried as the exception's own text and
    `PANEL_STORE_PLACEHOLDER` on `disclosable`, which is `_read`'s arrangement and its reason: a
    message that stays inside the process that owns the store may name it, while one that may cross
    a boundary would hand that path to whoever could reach the port. Which of the two a given face
    prints is that face's decision, and they differ: `cli._factor_fail` prints `str(error)` on this
    face while `cli._shortlist_fail` prints `disclosable` on the other. This function makes both
    available rather than choosing for them.

    The remedy names a year rather than spelling one, deliberately. The name the security traded
    under on `session` was established by an announcement outside the years this run read, and this
    function cannot say which year holds it: `load_name_histories` is scoped to the announcement
    years it is given, and how far back the security's last rename was is not knowable from a
    corpus that does not contain it. A remedy that named a year which does not help would be worse
    than one that names the dataset and the flag.
    """
    covered = ", ".join(str(year) for year in sorted(set(years)))
    return (
        f"the risk-warning state of {subject} on {session.isoformat()} could not be read out of "
        f"{where}: {error}. `MarketBar.is_st` is that state, so screening {subject} would file a "
        f"risk warning nobody knows as a known-clean one. The rename corpus is read one "
        f"announcement year at a time and this run read {covered}, so a security whose earliest "
        f"announcement in those years takes effect after the session being priced has no name on "
        f"it at all. Extend the corpus back to an announcement year that covers "
        f"{session.isoformat()} -- `openalpha panel build --dataset {NAMECHANGE_DATASET} --year "
        f"<year>` -- and ask this run for that year too."
    )


def _without_store_path(message: str, store: PanelStore) -> str:
    """`message` with the store's own location replaced by a name for it.

    Both spellings, longest first, which is `panel_view._without_store_path`'s rule and its
    measured reason: `Path.resolve()` differs from the configured path wherever a component is a
    symlink (every macOS `/var/...` temporary directory, for one), and replacing the shorter first
    would leave the longer one's prefix behind.
    """
    for path in sorted({str(store.root), str(store.root.resolve())}, key=len, reverse=True):
        message = message.replace(path, PANEL_STORE_PLACEHOLDER)
    return message


class _PanelInputs:
    """Everything a run reads out of the panel, cached per session where the contract is per
    session.

    A class rather than a bag of locals because the per-session reads are the expensive half and a
    run asks for one session's bars once per tier and once per label window. `load_daily_bars` and
    `load_price_limits` take one session per call by contract, so the caching is this module's to
    do -- and doing it here rather than at three call sites is what keeps the three tiers reading
    one panel.
    """

    def __init__(self, store: PanelStore, request: FactorRunRequest) -> None:
        self._store = store
        self._request = request
        self._bars: dict[date, Mapping[str, DailyBar]] = {}
        self._limits: dict[date, Mapping[str, PriceLimit]] = {}
        years = request.years
        as_of = request.as_of
        self.calendar: TradingCalendar = _read(
            lambda: load_trading_calendar(
                store, exchange=request.exchange, years=years, as_of=as_of
            ),
            store=store,
            what=f"the {request.exchange} trading calendar",
        )
        self.universe: StockUniverse = _read_registry(
            lambda: load_stock_universe(store, years=years, as_of=as_of, max_staleness=None),
            store=store,
        )
        self.adjustments: Mapping[str, AdjustmentHistory] = _read(
            lambda: load_adjustment_histories(store, years=years, as_of=as_of, max_staleness=None),
            store=store,
            what="the adjustment factors",
        )
        self.names: Mapping[str, NameHistory] = _read(
            lambda: load_name_histories(store, years=years, as_of=as_of, max_staleness=None),
            store=store,
            what="the name histories",
        )
        self.halts: HaltCorpus = halt_corpus_for_years(
            _read(
                lambda: load_suspensions(store, years=years, as_of=as_of, max_staleness=None),
                store=store,
                what="the halt corpus",
            ),
            years=years,
        )

    def bars_on(self, day: date) -> Mapping[str, DailyBar]:
        if day not in self._bars:
            self._bars[day] = _read(
                lambda: load_daily_bars(
                    self._store,
                    day=day,
                    calendar=self.calendar,
                    as_of=self._request.as_of,
                    max_staleness=None,
                ),
                store=self._store,
                what=f"the price bars for {day.isoformat()}",
            )
        return self._bars[day]

    def limits_on(self, day: date) -> Mapping[str, PriceLimit]:
        if day not in self._limits:
            self._limits[day] = _read(
                lambda: load_price_limits(
                    self._store,
                    day=day,
                    calendar=self.calendar,
                    as_of=self._request.as_of,
                    max_staleness=None,
                ),
                store=self._store,
                what=f"the published limit bands for {day.isoformat()}",
            )
        return self._limits[day]

    def market_bar(self, ts_code: str, day: date) -> MarketBar | None:
        """The stored bar and band as the execution policy's own input type, or `None`.

        `None` when the session has no bar or no published band for this security, which is the
        `unbarred` outcome one plane up rather than a refusal -- `QuantilePortfolioStudy.measure`
        counts a missing key and does not object to it.

        **The band is always the exchange's own.** A bar built without one falls back to
        `_price_band`'s derived rule, which `KNOWN_EXECUTION_LIMITATIONS` records is measurably
        wrong on the Beijing board and on ST names outside the main board, so this face declines to
        price a session it has no published band for rather than pricing it from a rule the panel
        can contradict. `is_st` is read off the stored name history for the neighbouring reason:
        `suspended_at_the_close` exists because collapsing a three-valued halt state into a bool
        has to happen somewhere and be visible, and inventing an ST flag would be the same
        collapse made silently.

        **The ST collapse is `warning is not RiskWarning.none`, and it is made here on purpose.**
        `RiskWarning.__bool__` raises for every member including `none`, deliberately, so that
        `if warning:` cannot merge `delisting_process` with `st` behind a reader's back --
        `_board`'s sibling landmine. `MarketBar.is_st` is a bool, so somebody has to decide, and
        the decision is that every one of the four warning states is "not an ordinary name" for
        band purposes. That is the merge `RiskWarning.star_st`'s own docstring says an ST filter
        wants; it is written out here rather than hidden in a truth test, and it reaches nothing
        on a session with a published band.
        """
        bar = self.bars_on(day).get(ts_code)
        limit = self.limits_on(day).get(ts_code)
        if bar is None or limit is None:
            return None
        history = self.names.get(ts_code)
        return MarketBar(
            subject=ts_code,
            trade_date=day,
            board=_board(ts_code),
            previous_close=Decimal(str(bar.pre_close)),
            open=Decimal(str(bar.open)),
            high=Decimal(str(bar.high)),
            low=Decimal(str(bar.low)),
            close=Decimal(str(bar.close)),
            suspended=suspended_at_the_close(
                self.halts.state_on(day, ts_code), self.halts.timing_on(day, ts_code)
            ),
            is_st=_risk_warned_on(
                history,
                subject=ts_code,
                session=day,
                store=self._store,
                years=self._request.years,
            ),
            **published_limit_fields(limit),
        )

    def label(self, ts_code: str, window: LabelWindow) -> OutcomeLabel | None:
        """One security's forward return over `window`, or `None` when it cannot be labelled.

        `None` for a security with no stored adjustment history: `label_outcome` requires one and a
        name that has none has no correct return, so it is left out of the label map and counted by
        `ICCensus.unmatched_count`. That is the honest place for it -- an unmatched name is visible
        in the census beside the cross section it was dropped from, while a fabricated unit factor
        would be a return computed across a corporate action nobody saw.

        ## Four refusals reach this call and only one of them is a `LabelError` (`V2-P4-084`)

        `label_outcome` asks three other modules questions, and each answers in its own vocabulary.
        All four are independent `ValueError` subclasses, so `except LabelError` caught one of
        them and let three past every guard on this face:

            StockUniverse.security       -> StockUniverseError, for a code the registry has no
                                            row for at all
            AdjustmentHistory.factor_on  -> AdjustmentHorizonError, for a day outside
                                            [covered_from, covered_through]
            daily_prices.session_returns -> PriceDataError, when `adj_factor` and `daily`
                                            disagree about one session's corporate action

        Measured before the fix, on the three stores in
        `tests/integration/test_unlabelled_corpus_faces.py`: `factor run` exited `5` with "it
        raised an unhandled StockUniverseError / AdjustmentHorizonError / PriceDataError ... The
        exception's own message is withheld", and `POST /api/v1/factors/run` answered `500` with a
        `text/plain` body Starlette wrote. That is `V2-P4-080`'s class one seam over: a domain
        error designed to be a verdict, raised where no `faults=` argument can reach it.

        The three now raise `FactorPanelUnreadableError` -- `exit 1` / `409 panel_unreadable` --
        while `LabelError` stays `FactorRunBlockedError`, and the split is not cosmetic.
        `LabelError` is what `label_outcome` says about **this window**: no bar on a session, a
        bar filed under the wrong day, a halt corpus that does not span it. The other three are
        statements about the stored corpus, and their remedy is a `panel build` rather than a
        different range.

        **The `LabelError` arm is unreachable from this face, and that was measured rather than
        noticed.** A mutant swapping its `FactorRunBlockedError` for `FactorPanelUnreadableError`
        -- which would file a window fault under the panel's row on the HTTP face -- survived both
        the file that drives this seam and `tests/integration/test_factor_run.py`, 28 tests, with
        nothing red. Nothing enters the branch: `halt_corpus_for_years` spans
        `min(years)-01-01..max(years)-12-31` from the same `request.years` the calendar every
        window is derived from was read over, so `require_coverage` has nothing outside it to
        find; and `window_return`'s missing-bar refusal is pre-empted by `_session_refusals`,
        which codes an absent bar `missing_bar` before any return is computed. It is kept rather
        than deleted -- deleting a guard is the fail-open direction and this reasoning spans three
        functions in two modules -- and both halves are pinned by
        `tests/integration/test_unlabelled_corpus_faces.py::
        test_the_label_error_arm_is_unreachable_from_this_face_and_here_is_each_reason`, which
        turns red if either stops holding.

        ## Where absence stops and unreadability starts, once per error

        `V2-P4-080` kept `history is None -> False` because most of the market has no rename in
        any one year and refusing on absence would refuse every honest run. Each of these three
        has the same line and it falls somewhere different:

        - **the registry.** A code the registry can *place* -- `not_yet_listed`, `delisted`,
          `beyond_snapshot` -- is already a `LabelRefusal` with its own code, collected beside
          every other reason the window cannot be priced, and stays one. `StockUniverse.security`
          refuses only a code it has no row for at all, deliberately: "an absent code is not a
          security that was never listed". Papering that over would put a name in the label map
          whose membership `is_listed` cannot answer for, which is exactly what
          `PricedCrossSection.unlisted_bars` exists to avoid doing silently.
        - **the factor series.** A security with **no** stored adjustment history is the `None`
          two paragraphs up and is left alone -- unmatched, counted, visible. A history that
          exists and does not reach the window is different: `factor_on` refuses outside its own
          bounds because "an unfetched factor is unknown rather than equal to the earliest one on
          file", and the fail-open it is refusing produces a return that is wrong by percentage
          points with the sign reversed (`_refuse_missing_factor_sessions` measures one:
          +2.742251% read back as -0.530973%).
        - **the price panel.** A *missing* bar is a finding: `_session_refusals` codes it
          `REFUSAL_MISSING_BAR`, and that is what a caller gets -- `window_return`'s own
          `LabelError` for the same condition is pre-empted by it, which is half of why the arm
          above is unreachable. What is refused here is two stored datasets **contradicting**
          each other about one session, which no amount of range-editing repairs.

        Absence is a finding in all three. A contradiction, and a corpus that does not reach, are
        not.

        ## Why not drop the security instead

        Returning `None` for these three was the other candidate and it is the fail-open this
        whole seam exists to refuse. `None` here means "unmatched", which `ICCensus.unmatched_count`
        reports as *this name had no forward return* -- a sentence that is true of a security with
        no adjustment history and false of one whose corpus contradicts itself. A reader of the
        census could not tell the two apart, and the second one is a panel defect that would then
        have shrunk the cross section silently and moved every statistic computed over it.
        """
        history = self.adjustments.get(ts_code)
        if history is None:
            return None
        bars = {
            day: session[ts_code]
            for day in window.sessions
            if ts_code in (session := self.bars_on(day))
        }
        limits = {
            day: band[ts_code]
            for day in window.sessions
            if ts_code in (band := self.limits_on(day))
        }
        try:
            return label_outcome(
                window,
                ts_code=ts_code,
                bars=bars,
                factors=history,
                limits=limits,
                halts=self.halts,
                universe=self.universe,
            )
        except LabelError as error:
            raise FactorRunBlockedError(
                f"{ts_code} could not be labelled over "
                f"{window.entry_day.isoformat()}..{window.exit_day.isoformat()}: {error}"
            ) from error
        except _LABEL_CORPUS_FAULTS as error:
            raise FactorPanelUnreadableError(
                _unlabelled_corpus_refusal(
                    str(self._store.root), subject=ts_code, window=window, error=error
                ),
                disclosable=_unlabelled_corpus_refusal(
                    PANEL_STORE_PLACEHOLDER, subject=ts_code, window=window, error=error
                ),
            ) from error


@dataclass(frozen=True, slots=True, kw_only=True)
class _LabelCorpusFault:
    """One refusal `label_outcome` can raise that is a verdict about the stored corpus.

    `about` is the partition the sentence is about and `remedy` the command line that repairs it,
    and they are two fields rather than one because **they do not always match**. A registry that
    has never heard of a code and a factor series that stops short are both fixed by building the
    named year of the named dataset; a `daily` row that contradicts `adj_factor` about one session
    is fixed by re-fetching that session, and neither dataset is the one at fault -- which one is
    wrong is precisely what the disagreement does not say. An earlier draft of this table had a
    single `--dataset {about} --year <year>` line for all three and told a user to rebuild `daily`
    for a contradiction that `adj_factor` was equally likely to have caused.
    """

    about: str
    why: str
    remedy: str


_LABEL_CORPUS_FAULTS: Final[tuple[type[Exception], ...]] = (
    StockUniverseError,
    AdjustmentError,
    PriceDataError,
)
"""The three refusals `label` anticipates beside `LabelError`, named once.

Used as the `except` clause **and** as `_LABEL_CORPUS_REMEDIES`' key set, so the set that is
caught and the set that has a message cannot come apart -- `_REGISTRY_FAULTS`' arrangement and
`V2-P4-060`'s lesson about two sites keeping the same list by hand.
`test_every_anticipated_label_corpus_fault_has_a_remedy_row` pins the two against each other.

The three module-level bases, **not** their horizon subclasses: `AdjustmentHorizonError` is an
`AdjustmentError` and `UniverseHorizonError` is a `StockUniverseError`, and both arms of a horizon
are the same fact about the same partition.
"""

_LABEL_CORPUS_REMEDIES: Final[Mapping[type[Exception], _LabelCorpusFault]] = {
    StockUniverseError: _LabelCorpusFault(
        about=STOCK_BASIC_DATASET,
        why=(
            "the registry has no lifecycle row for this code at all, so a run that dropped it "
            "would file a priced name as unmatched and no reader of the census could tell that "
            "apart from a name that never traded"
        ),
        remedy=(
            f"build the lifecycle year that carries its listing -- `openalpha panel build "
            f"--dataset {STOCK_BASIC_DATASET} --year <year>` -- and ask this run for that year "
            "too"
        ),
    ),
    AdjustmentError: _LabelCorpusFault(
        about=ADJ_FACTOR_DATASET,
        why=(
            "a series that stops short is not a security with no series at all (that one is "
            "already left out of the label map and counted), and carrying the nearest factor "
            "across the gap returns the unadjusted number wearing an adjusted one's name"
        ),
        remedy=(
            f"extend the factor series over the window -- `openalpha panel build --dataset "
            f"{ADJ_FACTOR_DATASET} --year <year>` -- and ask this run for that year too"
        ),
    ),
    PriceDataError: _LabelCorpusFault(
        about=f"{DAILY_DATASET} and {ADJ_FACTOR_DATASET}",
        why=(
            "which of the two is wrong is not decidable from the pair, so dropping this name "
            "would hide a panel defect rather than report it"
        ),
        remedy=(
            "re-fetch the session both datasets are about rather than rebuilding either one of "
            "them, and read the same session off `openalpha panel doctor --session <that "
            "session>`, which reports it as `return_path_disagreement`"
        ),
    ),
}
"""What each anticipated refusal is about, why answering anyway would be wrong, and the repair.

A table rather than a chain of `isinstance`, `domain/labels.py`'s `_LISTING_REFUSALS`' own
arrangement and its reason: the vocabulary and the branch are one object, so a fourth fault added
to `_LABEL_CORPUS_FAULTS` with no row here fails a test rather than being enveloped under
whichever dataset happened to be checked last.
"""


def _unlabelled_corpus_refusal(
    where: str, *, subject: str, window: LabelWindow, error: Exception
) -> str:
    """What this face says when a label window asks the corpus something it cannot answer.

    Not restated on the other face, and it could not be: `shortlist_view` labels nothing --
    `label_outcome` has exactly one caller in this repository. So unlike
    `_unnamed_session_refusal`, which `V2-P4-080` had to duplicate across two faces and hold
    together with `test_both_faces_refuse_an_unnamed_session_with_the_same_sentence`, this
    sentence has one home and needs no equality test to keep two copies from drifting.

    `where` is the store's own location on the message carried as the exception's own text and
    `PANEL_STORE_PLACEHOLDER` on `disclosable`, which is `_read`'s arrangement and its reason: a
    message that stays inside the process that owns the store may name it, while one that may
    cross a boundary would hand that path to whoever could reach the port. `cli._factor_fail`
    prints `str(error)` and `api/app.py`'s `_factor_refusal` sends `disclosable`; this function
    makes both available rather than choosing for them.

    **The domain's own sentence is carried through rather than summarised.** It is the half that
    says which day and by how much -- `factor_on` names the bound it refused past,
    `session_returns` names the two implied prices, the gap and the tolerance it cleared -- and a
    paraphrase would lose the number a user needs in order to decide whether to re-fetch one
    session or a decade.
    """
    fault = _LABEL_CORPUS_REMEDIES[
        next(base for base in _LABEL_CORPUS_FAULTS if isinstance(error, base))
    ]
    return (
        f"{subject} could not be labelled over "
        f"{window.entry_day.isoformat()}..{window.exit_day.isoformat()} out of {where}, and the "
        f"reason is the stored {fault.about} rather than the window: {error}. That is a verdict "
        f"about the panel rather than a range to edit -- {fault.why}. To repair it, "
        f"{fault.remedy}."
    )


def _board(ts_code: str) -> Literal["main", "star", "growth", "bse"]:
    """The board a code belongs to, from its prefix.

    Derived rather than defaulted to `"main"`: the board decides the lot rule
    (`AShareExecutionPolicy` requires 200 shares on STAR and a multiple of 100 elsewhere), so a
    `688*` name silently filed under the main board would be priced under a rule the exchange does
    not apply to it.
    """
    if ts_code.startswith("688"):
        return "star"
    if ts_code.startswith("300"):
        return "growth"
    if ts_code.endswith(".BJ"):
        return "bse"
    return "main"


def _in_range(instant: datetime, request: FactorRunRequest) -> bool:
    day = instant.astimezone(FACTOR_DATE_ZONE).date()
    return request.start <= day <= request.end


def _by_as_of(
    rows: Sequence[TierObservation], request: FactorRunRequest
) -> dict[datetime, list[TierObservation]]:
    """Group one tier's stored rows by their own `as_of`, keeping only the range's.

    A stored row's `as_of` **is** the cross section it belongs to -- all three observation
    contracts carry it and every write path stamps all four panel clocks with it -- so this
    grouping re-derives nothing. The range filter is applied in `FACTOR_DATE_ZONE`, for the reason
    that constant exists.
    """
    grouped: dict[datetime, list[TierObservation]] = {}
    for row in rows:
        if _in_range(row.as_of, request):
            grouped.setdefault(row.as_of, []).append(row)
    return grouped


def run_factor_experiment(
    store: PanelStore,
    request: FactorRunRequest,
    *,
    built_at: datetime,
    experiments: ExperimentDocumentStore,
    note: FactorNote | None = None,
) -> tuple[FactorExperimentRecord, ExperimentWrite]:
    """Read the three stored tiers, run the four studies on each, seal the artifact, store it.

    The one entry point all three faces call, which is what makes their answers one answer rather
    than three that agree today. It re-derives nothing: every number in the record was computed by
    `FactorICStudy`, `QuantilePortfolioStudy`, `TradeabilityStudy` or `RedundancyStudy`, and the
    grid is `build_factor_experiment`'s.

    The `as_of`s are the **raw** tier's own stored cross sections inside the range, ascending, and
    the other two tiers must carry a cross section at every one of them. That rule and what it
    costs are this module's docstring's third section and
    `the_three_tiers_must_have_been_built_at_the_same_instants`.

    Storing happens here rather than at each face for the reason `panel_view` renders in one
    place: three write paths are three chances to write a different document under one key. It is
    the last thing that happens, so a run that could not assemble an experiment stores nothing.
    """
    raw_rows = _by_as_of(
        _read(
            lambda: load_factor_observations(
                store, request.definition, years=request.years, as_of=request.as_of
            ),
            store=store,
            what=f"the raw {request.definition.qualified_key} observations",
            remedy=_unbuilt_factor_remedy(store, definition=request.definition, tier="raw"),
        ),
        request,
    )
    if not raw_rows:
        raise FactorRunBlockedError(
            f"no stored {request.definition.qualified_key} cross section is dated between "
            f"{request.start.isoformat()} and {request.end.isoformat()} and visible at "
            f"{request.as_of.isoformat()}; an experiment over no prediction day at all is the "
            "empty success this plane exists to make unavailable. Build the factor over that "
            "range first"
        )
    as_ofs = tuple(sorted(raw_rows))
    processed_rows = _by_as_of(
        _read(
            lambda: load_processed_factor_observations(
                store,
                request.definition,
                request.transform,
                years=request.years,
                as_of=request.as_of,
            ),
            store=store,
            what=f"the {request.transform.qualified_key} rows of that factor",
            remedy=_unbuilt_factor_remedy(store, definition=request.definition, tier="processed"),
        ),
        request,
    )
    neutralized_rows = _by_as_of(
        _read(
            lambda: load_neutralized_factor_observations(
                store,
                request.definition,
                request.neutralization,
                years=request.years,
                as_of=request.as_of,
            ),
            store=store,
            what=f"the {request.neutralization.qualified_key} residuals of that factor",
            remedy=_unbuilt_factor_remedy(store, definition=request.definition, tier="neutralized"),
        ),
        request,
    )
    _refuse_tiers_over_different_instants(
        request, as_ofs=as_ofs, processed=processed_rows, neutralized=neutralized_rows
    )
    inputs = _PanelInputs(store, request)
    by_tier: dict[FactorTier, dict[datetime, list[TierObservation]]] = {
        "raw": raw_rows,
        "processed": processed_rows,
        "neutralized": neutralized_rows,
    }
    reports = {
        tier: _tier_report(
            request, inputs, tier=tier, as_ofs=as_ofs, rows=by_tier[tier], raw=raw_rows
        )
        for tier in FACTOR_TIER_ORDER
    }
    try:
        record = build_factor_experiment(
            ic_spec=request.ic,
            portfolio_spec=request.portfolio,
            tradeability_spec=request.tradeability,
            survival_spec=request.survival,
            retention_floor=request.retention_floor,
            code_commit=request.code_commit,
            raw=reports["raw"],
            processed=reports["processed"],
            neutralized=reports["neutralized"],
            built_at=built_at,
            note=note,
        )
    except (FactorExperimentError, ValueError) as error:
        raise FactorRunBlockedError(
            f"the three tier reports could not be bound into one experiment: {error}"
        ) from error
    write = experiments.put(
        experiment_id=record.experiment_id,
        content_digest=record.content_digest,
        built_at=record.built_at,
        payload=experiment_payload(record),
    )
    return _held(experiments, record.experiment_id), write


def _held(experiments: ExperimentDocumentStore, experiment_id: str) -> FactorExperimentRecord:
    """The document the store now holds under `experiment_id`, reopened.

    **A face hands back what is stored, not what it just built**, and the difference is real
    rather than pedantic: `built_at` is a field of the record and is outside every digest, so a
    second run of one experiment produces a document that is byte-different and
    content-identical. The store keeps the first (`FileExperimentStore.put`'s `unchanged` branch),
    so a run that reproduced an existing artifact must answer with the artifact -- otherwise the
    CLI, the SDK and the HTTP face would return three bodies differing in a wall clock while all
    three agreed that nothing had changed, and `tests/integration/test_factor_interfaces.py::
    test_the_three_faces_seal_one_experiment_from_one_request` would be comparing three renderings
    of three records rather than one document.

    Reopening rather than trusting is the second half: `open_experiment` recomputes the seal, so
    a document that did not survive the round trip through the filesystem is a refusal here rather
    than a record a caller reads numbers off.
    """
    payload = experiments.get(experiment_id)
    if payload is None:
        raise FactorRunBlockedError(
            f"{experiment_id} was written and is not held; the document store accepted an "
            "artifact and cannot serve it back, so nothing here can be relied on"
        )
    try:
        return open_experiment(payload)
    except FactorExperimentError as error:
        raise FactorRunBlockedError(
            f"the held document for {experiment_id} does not reopen: {error}"
        ) from error


def _refuse_tiers_over_different_instants(
    request: FactorRunRequest,
    *,
    as_ofs: Sequence[datetime],
    processed: Mapping[datetime, object],
    neutralized: Mapping[datetime, object],
) -> None:
    """Refuse a run whose three tiers were not built at the same instants.

    Named separately from the assembly because the message is the deliverable: a caller told only
    "blocked" cannot tell "the neutralisation has not been built for these days" from "the
    transform was run under a different key", and the two have different remedies. The missing
    instants are listed rather than counted, capped at `MISSING_INSTANTS_SHOWN`.
    """
    for name, held in (("processed", processed), ("neutralized", neutralized)):
        missing = [instant for instant in as_ofs if instant not in held]
        if not missing:
            continue
        shown = ", ".join(instant.isoformat() for instant in missing[:MISSING_INSTANTS_SHOWN])
        rest = len(missing) - MISSING_INSTANTS_SHOWN
        more = "" if rest <= 0 else f" (and {rest} more)"
        spec = request.transform if name == "processed" else request.neutralization
        raise FactorRunBlockedError(
            f"the raw tier has {len(as_ofs)} stored cross section(s) in this range and the {name} "
            f"tier has none at {shown}{more} under {spec.qualified_key!r}. An attribution is a "
            "difference between two readings of the same days, so a three-tier report cannot be "
            f"assembled over three schedules -- and reporting one whose {name} row measured "
            "nothing would put the acceptance criterion's cell on a row that was never built. A "
            "neutralisation is the usual cause, because it is stamped at the instant its own "
            "build was run and that build is scheduled separately from the other two; since "
            "V2-P4-026 it CAN be run at a mid-year as_of, so this is a fact about the schedule "
            "rather than about the storage contract. Move --start/--end onto the instants the "
            "tier was built at, or build the missing tier for these days"
        )


def _tier_report(
    request: FactorRunRequest,
    inputs: _PanelInputs,
    *,
    tier: FactorTier,
    as_ofs: Sequence[datetime],
    rows: Mapping[datetime, Sequence[TierObservation]],
    raw: Mapping[datetime, Sequence[TierObservation]],
) -> TierReport:
    """One tier's four upstream answers over the whole range, whole and un-collapsed.

    The four studies run on the same `ICCrossSection` per `as_of`, which is `factor_portfolio`'s
    own arrangement and its reason: an IC and a spread computed over two admitted sets cannot be
    reconciled by pointing at different samples.
    """
    ic_study = FactorICStudy(request.ic)
    quantile_study = QuantilePortfolioStudy(request.portfolio, execution=AShareExecutionPolicy())
    turnover_study = TradeabilityStudy(request.tradeability, portfolio=request.portfolio)
    survival_study = RedundancyStudy(request.survival, identities=())
    points: list[ICPoint] = []
    periods: list[PeriodPortfolio] = []
    tradeability: list[PeriodTradeability] = []
    survival_points: list[RedundancyPoint] = []
    builds: set[str] = set()
    for instant in as_ofs:
        tier_rows = rows[instant]
        builds.update(_source_build(row, tier=tier) for row in tier_rows)
        window = _label_window(request, inputs, instant)
        labels = {
            subject: label
            for subject in sorted({row.subject for row in tier_rows})
            if (label := inputs.label(subject, window)) is not None
        }
        section = _cross_section(tier=tier, as_of=instant, rows=tier_rows, labels=labels)
        try:
            points.append(ic_study.measure(section))
            period = quantile_study.measure(section, bars=_bars_for(inputs, section, window))
            periods.append(period)
            tradeability.append(
                turnover_study.measure(
                    period,
                    cross_section=section,
                    liquidity=_liquidity_for(inputs, section, window),
                )
            )
        except (FactorICError, FactorPortfolioError, FactorTradeabilityError) as error:
            raise FactorRunBlockedError(
                f"the {tier} tier at {instant.isoformat()} could not be measured: {error}"
            ) from error
        if tier != "raw":
            survival_points.append(
                _survival_point(request, tier=tier, as_of=instant, raw=raw[instant], rows=tier_rows)
            )
    try:
        return TierReport(
            tier=tier,
            source_manifest_ids=tuple(sorted(builds)),
            ic=ic_study.summarize(points),
            portfolio=quantile_study.summarize(periods),
            turnover=turnover_study.turnover(periods),
            tradeability=turnover_study.summarize(tradeability),
            survival=None if tier == "raw" else survival_study.summarize(survival_points),
        )
    except (
        FactorICError,
        FactorPortfolioError,
        FactorTradeabilityError,
        FactorRedundancyError,
        ValueError,
    ) as error:
        raise FactorRunBlockedError(
            f"the {tier} tier's four studies could not be bound into one row: {error}"
        ) from error


def _label_window(request: FactorRunRequest, inputs: _PanelInputs, as_of: datetime) -> LabelWindow:
    """The forward window one prediction day is scored over, or a refusal naming the calendar.

    `FACTOR_DATE_ZONE` rather than a parameter, `build_label_window`'s own rule: an instant is not
    a session date until a timezone says so, and this repository records its answer once.
    """
    try:
        return build_label_window(
            as_of=as_of,
            zone=FACTOR_DATE_ZONE,
            horizon=request.horizon,
            calendar=inputs.calendar,
        )
    except (LabelError, TradingCalendarError) as error:
        raise FactorRunBlockedError(
            f"no {request.horizon.text} label window exists at {as_of.isoformat()}: {error}. The "
            "stored exchange calendar has to reach past the last prediction day, because a "
            "forward return is priced on the sessions after it"
        ) from error


def _source_build(row: TierObservation, *, tier: FactorTier) -> str:
    """The stored build one row was read from, under that tier's own column name.

    Three names for one idea, and they are not unified here: `manifest_id`,
    `transform_manifest_id` and `neutralization_manifest_id` are three different contracts' fields
    and `TierReport.source_manifest_ids` documents which one each tier carries. Dispatched on the
    row's own type rather than on `tier`, so a row filed under the wrong tier cannot contribute a
    plausible id from the wrong column.
    """
    if isinstance(row, FactorObservation):
        return row.manifest_id
    if isinstance(row, ProcessedFactorObservation):
        return row.transform_manifest_id
    return row.neutralization_manifest_id


def _cross_section(
    *,
    tier: FactorTier,
    as_of: datetime,
    rows: Sequence[TierObservation],
    labels: Mapping[str, OutcomeLabel],
) -> ICCrossSection:
    """One tier's cross section, through that tier's own public wrapper.

    Three wrappers rather than one call to `ic_cross_section`, because each of them carries its own
    tier's admitted-code table -- and which codes are admitted is precisely what a caller must not
    be able to choose.
    """
    try:
        if tier == "raw":
            return raw_cross_section(
                as_of=as_of,
                observations=[row for row in rows if isinstance(row, FactorObservation)],
                labels=labels,
            )
        if tier == "processed":
            return processed_cross_section(
                as_of=as_of,
                observations=[row for row in rows if isinstance(row, ProcessedFactorObservation)],
                labels=labels,
            )
        return neutralized_cross_section(
            as_of=as_of,
            observations=[row for row in rows if isinstance(row, NeutralizedFactorObservation)],
            labels=labels,
        )
    except FactorICError as error:
        raise FactorRunBlockedError(
            f"the {tier} cross section at {as_of.isoformat()} could not be paired with its "
            f"labels: {error}"
        ) from error


def _bars_for(
    inputs: _PanelInputs, section: ICCrossSection, window: LabelWindow
) -> dict[str, tuple[MarketBar, MarketBar]]:
    """The entry and exit bars each admitted name would trade on, keyed by the cross section.

    Keyed by `section.pairs` and not by the universe, which is `QuantilePortfolioStudy.measure`'s
    own rule: a key with no admitted pair is refused there rather than ignored. A name with no
    stored bar or no published band on either session is simply absent, which is the `unbarred`
    outcome one plane up.
    """
    bars: dict[str, tuple[MarketBar, MarketBar]] = {}
    for pair in section.pairs:
        entry = inputs.market_bar(pair.subject, window.entry_day)
        exit_bar = inputs.market_bar(pair.subject, window.exit_day)
        if entry is not None and exit_bar is not None:
            bars[pair.subject] = (entry, exit_bar)
    return bars


def _liquidity_for(
    inputs: _PanelInputs, section: ICCrossSection, window: LabelWindow
) -> dict[str, SessionLiquidity]:
    """Each admitted name's traded value on the entry session, in yuan, keyed by the cross section.

    **The entry session itself, which is one of the two readings the contract admits and is the
    diagnostic one.** `the_liquidity_session_is_the_callers_and_may_be_the_entry_session_itself`
    states the pair: the order fills at that session's close, so its turnover is realised by the
    time of the fill and was not known when the position was sized, which makes this "could this
    have been traded on that day" rather than "would we have believed it could". This face asks
    the first because the second needs a session the range does not name, and
    `TradeabilitySummary.sized_at_the_entry_session_count` carries which was asked so a reader of
    the artifact can see it rather than infer it.

    Keyed by `section.pairs` and not by the universe, which is `TradeabilityStudy.measure`'s own
    rule: a key with no admitted pair is refused there rather than ignored.

    A session whose stored `amount` is not positive is **left out** rather than offered as a zero.
    `SessionLiquidity` refuses a non-positive turnover outright -- "a security that traded nothing
    has no capacity rather than a capacity of zero" -- and its docstring says the caller declares
    that by not offering the row, which arrives one plane up as `unpriced_holdings` if the name was
    held and as nothing at all if it was not. Offering a zero would instead put a capacity of zero
    on the whole group through the `min`.
    """
    liquidity: dict[str, SessionLiquidity] = {}
    bars = inputs.bars_on(window.entry_day)
    for pair in section.pairs:
        bar = bars.get(pair.subject)
        if bar is None or bar.amount <= 0.0:
            continue
        liquidity[pair.subject] = liquidity_from_amount(
            subject=pair.subject, trade_date=window.entry_day, amount=bar.amount
        )
    return liquidity


def _survival_point(
    request: FactorRunRequest,
    *,
    tier: FactorTier,
    as_of: datetime,
    raw: Sequence[TierObservation],
    rows: Sequence[TierObservation],
) -> RedundancyPoint:
    """One `as_of`'s raw-against-this-tier correlation: the cross-tier self-pair.

    Through `correlate_cross_section` rather than `RedundancyStudy.measure`, for the reason
    `V2-P3-014`'s own fixtures give: `measure` keys the vectors it may need for an identity by
    factor **key**, and a cross-tier self-pair offers one key twice, so it refuses. This is the
    function whose own docstring names the cross-tier pair as the supported reading, and it takes
    `identity=None` -- which is the whole of what a self-pair could ever have, since an identity
    relates two factors.
    """
    try:
        left = factor_vector(
            as_of=as_of,
            tier="raw",
            definition=request.definition,
            rows=tuple((item.subject, item.value, item.coverage) for item in raw),
        )
        right = factor_vector(
            as_of=as_of,
            tier=tier,
            definition=request.definition,
            rows=tuple((item.subject, item.value, item.coverage) for item in rows),
        )
        return correlate_cross_section(left=left, right=right, spec=request.survival)
    except FactorRedundancyError as error:
        raise FactorRunBlockedError(
            f"the raw-against-{tier} survival correlation at {as_of.isoformat()} could not be "
            f"measured: {error}"
        ) from error


def experiment_view(record: FactorExperimentRecord, *, write: ExperimentWrite) -> dict[str, object]:
    """A sealed record as JSON-ready data: the two addresses, what the store did, the document.

    **This module invents no rendering of an experiment, and that is the design rather than an
    economy.** `panel_view` had to build one because a `PanelHealthReport` has no transport form of
    its own; a `FactorExperimentRecord` already has one, and it is a *sealed* one --
    `experiment_payload` renders the declared fields under `stable_model_id`'s canonicalisation and
    `open_experiment` refuses a payload whose content no longer hashes to the seal it carries. A
    second rendering here would be a second thing that can disagree about one document, and it
    would be a rendering the seal does not cover: a key dropped from it would be invisible to every
    check in this repository, which is exactly the shape `panel_view.py` was measured on -- 54
    rendered keys, 19 of them never asserted.

    So the document travels whole and the envelope is five keys, each separately falsifiable:

    - **`schema_version`** -- this envelope's own version. See `VIEW_SCHEMA_VERSION`.
    - **`experiment_id`** and **`content_digest`** -- projections of the document, carried so a
      caller can key on them without parsing, and recomputable from `document` by anybody who
      wants to check.
    - **`write`** -- what the store did with this artifact. See `ExperimentWrite`.
    - **`document`** -- `json.loads(experiment_payload(record))`, byte-for-byte the payload the
      store holds, so what a face returns and what a store keeps cannot come apart.

    `tests/integration/test_factor_interfaces.py::
    test_every_key_the_three_faces_render_is_held_by_the_seal` walks every scalar leaf of this body,
    perturbs exactly one at a time, and requires the result to fail to reopen -- which is
    `V2-P3-014`'s per-key audit arriving on the thing the faces actually hand out.
    """
    document: object = json.loads(experiment_payload(record))
    return {
        "schema_version": VIEW_SCHEMA_VERSION,
        "experiment_id": record.experiment_id,
        "content_digest": record.content_digest,
        "write": write,
        "document": document,
    }


def attribution_rows(record: FactorExperimentRecord) -> tuple[tuple[str, str, str, str], ...]:
    """The six cells as `(step, statistic, retention, verdict)` strings, in declared order.

    A rendering for a terminal and nothing more: `cli.py` prints these, and every one of the four
    strings is read off `TierAttribution` rather than recomputed. `ATTRIBUTION_CELL_ORDER` is the
    order, so a cell that is `not_measured` occupies its row instead of vanishing -- a grid missing
    a cell and one whose cell has no number are two different claims.
    """
    cells: list[tuple[str, str, str, str]] = []
    for from_tier, to_tier, statistic in ATTRIBUTION_CELL_ORDER:
        cell = record.artifact.attribution(
            from_tier=from_tier, to_tier=to_tier, statistic=statistic
        )
        cells.append(
            (
                f"{from_tier}->{to_tier}",
                statistic,
                "-" if cell.retention is None else repr(cell.retention),
                cell.verdict,
            )
        )
    return tuple(cells)


def tier_rows(record: FactorExperimentRecord) -> tuple[tuple[str, str, str, str], ...]:
    """The three tier rows as `(tier, ic coverage, mean_ic, mean_spread)` strings.

    `FACTOR_TIER_ORDER`'s order, and the two statistics are the two the grid attributes on, so a
    reader of the terminal output can see the numbers the verdicts were decided from beside the
    verdicts themselves.
    """
    rows: list[tuple[str, str, str, str]] = []
    for tier in FACTOR_TIER_ORDER:
        report = record.artifact.tier_report(tier)
        rows.append(
            (
                tier,
                report.ic.coverage,
                "-" if report.ic.mean_ic is None else repr(report.ic.mean_ic),
                "-" if report.portfolio.mean_spread is None else repr(report.portfolio.mean_spread),
            )
        )
    return tuple(rows)


def acceptance_rows(record: FactorExperimentRecord) -> tuple[tuple[str, str], ...]:
    """The `(statistic, verdict)` pairs of the one step a three-tier report is decided on.

    A projection of `attribution_rows`, and it exists because the grid is six rows of four columns
    and **one** of the three steps carries the finding. `factor_experiment.py` says so in prose --
    "this is the step the roadmap's annotation is about" -- and a face that printed six equal rows
    left the reader to know which. `ACCEPTANCE_STEP` is the declaration; this is the read of it.
    """
    return tuple(
        (statistic, cell.verdict)
        for from_tier, to_tier, statistic in ATTRIBUTION_CELL_ORDER
        if (from_tier, to_tier) == ACCEPTANCE_STEP
        and (
            cell := record.artifact.attribution(
                from_tier=from_tier, to_tier=to_tier, statistic=statistic
            )
        )
        is not None
    )


def everything_is_unmeasured(record: FactorExperimentRecord) -> bool:
    """Whether **every** cell of the grid is `not_measured`, which is the quietest bad answer.

    `docs/api/http.md` states that exit `0` includes an experiment whose grid says `removed` on
    every cell, and that is a finding: the report succeeded at its job. It did not state the other
    shape, and the acceptance review named it the most dangerous thing on this face -- a grid whose
    every cell is `not_measured` also exits `0` and also answers `200`, and a reader (or a CI step)
    that greps for `removed`, finds nothing and stops has concluded "this factor survived
    neutralisation" about two tiers that never computed a number.

    So it is a **declared property of the artifact rather than an exit code**, and both halves of
    that are deliberate:

    - Not an exit code, because `FACTOR_EXIT`'s row is "an experiment that assembled exits 0" and
      an all-`not_measured` experiment did assemble. Its tier reports carry the real reason -- each
      tier's own four coverage codes are on the record -- so a second, coarser signal on the
      envelope would be a fifth vocabulary for "not enough data", which `V2-P3-014` and this
      module both already refuse to add.
    - Not silent either. `cli._echo_experiment` prints a named line when this is true, `factor
      run --json` prints the same line on **stderr** so that stdout stays exactly the sealed
      envelope, and `docs/api/http.md` now carries the sentence it was missing.

    Read off `ATTRIBUTION_CELL_ORDER` rather than off `attribution_rows`, so a change to the
    terminal rendering cannot move this answer.
    """
    verdicts: set[AttributionVerdict] = {
        record.artifact.attribution(
            from_tier=from_tier, to_tier=to_tier, statistic=statistic
        ).verdict
        for from_tier, to_tier, statistic in ATTRIBUTION_CELL_ORDER
    }
    return verdicts == {"not_measured"}


# --- what this build declares: the catalog behind `--factor`, `--transform`, `--neutralization` ---


CATALOG_SCHEMA_VERSION: Final[str] = "factor-catalog/v1"
"""The version of the body `factor_catalog` and `factor_entry` hand out.

Its own, and deliberately not any declaration's: each entry carries its contract's own
`schema_version` inside `declaration`, and they version different things. A face that reused one
of those would make a change to the transport look like a change to a factor.
"""

ACCEPTANCE_STEP: Final[tuple[FactorTier, FactorTier]] = ("processed", "neutralized")
"""The one tier step a three-tier report's acceptance criterion is decided on.

Declared here rather than in `backtest/factor_experiment.py` because it is a statement about what
a *face* should point at, not about how a cell is computed -- that module owns
`ATTRIBUTION_STEPS`, treats all three as equals on purpose (each has its own remedy), and says in
prose which one the roadmap's annotation is about. This is that sentence as data, and
`tests/integration/test_factor_catalog.py::
test_the_acceptance_step_is_one_of_the_declared_steps_and_is_the_neutralisation_one` binds it back
to the declaration so a fourth step cannot leave this pointing at a cell the grid no longer has.
"""

ATTRIBUTION_VERDICT_MEANINGS: Final[Mapping[str, str]] = MappingProxyType(
    {
        "not_measured": (
            "one of the two tiers carries no statistic at all, so nothing was compared. NOT a "
            "pass: a grid that is not_measured everywhere reports no finding, and reading it as "
            "`the factor survived` is the mistake this row exists to make impossible"
        ),
        "no_baseline": (
            "both tiers measured and the EARLIER one's statistic is at or below zero, so there "
            "was nothing for the later tier to keep. A factor that never worked, not one whose "
            "edge was removed"
        ),
        "reversed": (
            "the later tier's statistic is negative: the step turned the bet around, which is a "
            "different finding from shrinking it"
        ),
        "amplified": (
            "retention above 1: the step made the statistic larger, which is what a factor whose "
            "exposure was working against it looks like"
        ),
        "removed": (
            "retention below the declared --retention-floor. On the processed->neutralized step "
            "this is the acceptance criterion firing: what the factor earned was the industry and "
            "the size exposure"
        ),
        "survives": "retention between the declared floor and 1: the step kept the statistic",
    }
)
"""One sentence per `AttributionVerdict`, for the faces that print or serve the grid.

Six verdicts decided every `factor run`'s answer and **not one of them was written down anywhere a
caller could reach** -- `grep -r survives docs/ README* web/` found nothing, so the vocabulary was
readable only by opening `backtest/factor_experiment.py`. The prose is here rather than there
because that module's own docstring is the normative statement and a second copy of it would be a
second thing that can disagree; what these are is the *short* form a terminal and a JSON body can
carry. `tests/integration/test_factor_catalog.py::
test_every_declared_verdict_carries_a_meaning_and_no_meaning_is_invented` holds the key set equal
to `ATTRIBUTION_VERDICT_CODES`, so a seventh verdict arrives here as a failure rather than as a
cell nobody can read.
"""


@dataclass(frozen=True, slots=True, kw_only=True)
class _Declared:
    """One declared contract, flattened to the three things every face needs of all three kinds."""

    kind: Literal["factor", "transform", "neutralization"]
    handle: str
    identity: str
    declaration: Mapping[str, object]
    note: str | None


def _declared_factors(registry: FactorRegistry) -> tuple[_Declared, ...]:
    return tuple(
        _Declared(
            kind="factor",
            handle=item.qualified_key,
            identity=item.factor_id,
            declaration=item.model_dump(mode="json"),
            note=registry.note_for(item.qualified_key),
        )
        for item in registry.definitions
    )


def _declared_transforms(registry: FactorTransformRegistry) -> tuple[_Declared, ...]:
    return tuple(
        _Declared(
            kind="transform",
            handle=item.qualified_key,
            identity=item.transform_id,
            declaration=item.model_dump(mode="json"),
            note=registry.note_for(item.qualified_key),
        )
        for item in registry.specs
    )


def _declared_neutralizations(registry: FactorNeutralizationRegistry) -> tuple[_Declared, ...]:
    return tuple(
        _Declared(
            kind="neutralization",
            handle=item.qualified_key,
            identity=item.neutralization_id,
            declaration=item.model_dump(mode="json"),
            note=registry.note_for(item.qualified_key),
        )
        for item in registry.specs
    )


def _entry(item: _Declared) -> dict[str, object]:
    """One declared contract as JSON-ready data: what it is called, what it is, what it says.

    **`declaration` is `model_dump(mode="json")` and not a rendering of this module's devising**,
    which is `experiment_view`'s argument arriving one plane over. Each of the three specs is
    content-addressed by `stable_model_id` over exactly its declared fields, so every key inside
    `declaration` is held by `identity`: perturb one and the address moves. A hand-written
    projection would be a second rendering the address does not cover -- a key dropped from it
    would be invisible to every check in this repository, which is precisely what `panel_view.py`
    was measured on (54 rendered keys, 19 never asserted).

    `handle` and `identity` are kind-agnostic projections of `declaration`'s own
    `qualified_key` and `factor_id`/`transform_id`/`neutralization_id`, carried so a client can
    key on one shape across all three kinds without knowing which field name each contract used.
    `note` is `None` for a contract this registry carries no prose about, which is an answer
    (`FactorRegistry.note_for`'s own rule) and not a fault.
    """
    return {
        "kind": item.kind,
        "handle": item.handle,
        "identity": item.identity,
        "declaration": dict(item.declaration),
        "note": item.note,
    }


def factor_catalog(
    *,
    factors: FactorRegistry = FACTOR_DEFINITIONS,
    transforms: FactorTransformRegistry = FACTOR_TRANSFORMS,
    neutralizations: FactorNeutralizationRegistry = FACTOR_NEUTRALIZATIONS,
) -> dict[str, object]:
    """Everything a caller needs to fill in `factor run`'s options and read its answer.

    `openalpha factor list --json`, `GET /api/v1/factors` and `OpenAlphaSDK.factor_catalog()` are
    this function and nothing else, so the three faces cannot come to describe three builds. The
    human table `openalpha factor list` prints is a *rendering* of this body (see `catalog_rows`);
    the body itself is what the equivalence is asserted on.

    Seven keys, each closing a hole the acceptance review measured:

    - **`factors`**, **`transforms`**, **`neutralizations`** -- the legal values of `--factor`,
      `--transform` and `--neutralization`. Before this there was no face, no route and no
      document that listed any of them, and the only discovery channel was a typo.
    - **`tiers`** -- `FACTOR_TIER_ORDER`, so a reader of a three-row report knows the order is
      declared rather than incidental.
    - **`verdicts`** -- `ATTRIBUTION_VERDICT_MEANINGS`, in `ATTRIBUTION_VERDICT_ORDER`. The six
      words the grid's last column speaks appeared in no document at all.
    - **`attribution_cells`** -- the six cells in `ATTRIBUTION_CELL_ORDER`, each flagged with
      whether it is the step the acceptance criterion is decided on. A grid of six equal-looking
      rows is what made "which row is the answer" unanswerable from the output.
    - **`run_limitations`** -- `KNOWN_FACTOR_RUN_LIMITATIONS`, which were declared in the source
      and reachable from no face.

    The **whole prose** travels, not a truncation of it: the notes run from 705 to 4,830
    characters and `tests/unit/test_factor_engine_rules.py::
    test_every_shipped_contract_carries_its_prose` requires each to exceed 100, so a face that
    clipped them would be the one thing a reader came for. What a *terminal* does about that is
    `catalog_rows`' problem and it solves it by printing lengths and a pointer to `factor
    describe`, not by shortening what the data carries.
    """
    return {
        "schema_version": CATALOG_SCHEMA_VERSION,
        "factors": [_entry(item) for item in _declared_factors(factors)],
        "transforms": [_entry(item) for item in _declared_transforms(transforms)],
        "neutralizations": [_entry(item) for item in _declared_neutralizations(neutralizations)],
        "tiers": list(FACTOR_TIER_ORDER),
        "verdicts": [
            {"code": code, "meaning": ATTRIBUTION_VERDICT_MEANINGS[code]}
            for code in ATTRIBUTION_VERDICT_ORDER
        ],
        "attribution_cells": [
            {
                "step": f"{from_tier}->{to_tier}",
                "statistic": statistic,
                "decides_the_acceptance_criterion": (from_tier, to_tier) == ACCEPTANCE_STEP,
            }
            for from_tier, to_tier, statistic in ATTRIBUTION_CELL_ORDER
        ],
        "run_limitations": [
            {"code": limitation.code, "detail": limitation.detail}
            for limitation in KNOWN_FACTOR_RUN_LIMITATIONS
        ],
    }


def factor_entry(
    *,
    factor: str | None = None,
    transform: str | None = None,
    neutralization: str | None = None,
    factors: FactorRegistry = FACTOR_DEFINITIONS,
    transforms: FactorTransformRegistry = FACTOR_TRANSFORMS,
    neutralizations: FactorNeutralizationRegistry = FACTOR_NEUTRALIZATIONS,
) -> dict[str, object]:
    """One declared contract, named by exactly one of the three handles.

    Three parameters rather than one polymorphic token, and the choice is argued rather than
    convenient: the three registries are three different contracts and a single `--handle` would
    have to guess which one a caller meant, so a factor and a transform that ever came to share a
    `qualified_key` would resolve arbitrarily. Naming the kind makes the question total, and it
    makes the refusal say which registry was searched.

    `--factor` accepts a `factor_id` as well, through `resolve_factor`, for that function's reason:
    a reader holding a stored observation has only the address. The other two take their qualified
    key alone, because no partition column carries a bare `transform_id` a human would be looking
    one up from -- `factor_proc_*` carries the qualified key beside it.

    Refuses `FactorRequestError` for none and for more than one, rather than picking a precedence:
    a face that silently preferred `--factor` would answer a question the caller did not ask.
    """
    named = {
        "factor": factor,
        "transform": transform,
        "neutralization": neutralization,
    }
    given = sorted(name for name, value in named.items() if value is not None and value.strip())
    if len(given) != 1:
        raise FactorRequestError(
            f"name exactly one of --factor, --transform or --neutralization; got {given}. The "
            "three are three registries rather than three spellings of one, so a describe that "
            "guessed would answer about whichever it searched first"
        )
    kind = given[0]
    if kind == "factor":
        assert factor is not None
        definition = resolve_factor(factor, registry=factors)
        return _entry(
            _Declared(
                kind="factor",
                handle=definition.qualified_key,
                identity=definition.factor_id,
                declaration=definition.model_dump(mode="json"),
                note=factors.note_for(definition.qualified_key),
            )
        )
    declared = (
        _declared_transforms(transforms)
        if kind == "transform"
        else _declared_neutralizations(neutralizations)
    )
    wanted = ((transform if kind == "transform" else neutralization) or "").strip()
    for item in declared:
        if item.handle == wanted:
            return _entry(item)
    raise FactorRequestError(
        f"{wanted!r} is not a {kind} this build declares; it knows "
        f"{[item.handle for item in declared]}. `openalpha factor list` prints every one of them "
        "with what it decides"
    )


def catalog_rows(catalog: Mapping[str, object]) -> tuple[tuple[str, str, str, str], ...]:
    """The catalog as `(kind, handle, what it decides, note size)` strings, for a terminal.

    A rendering and nothing more: `cli.py` prints these, every string is read off `catalog` rather
    than off a registry, and the middle column is the one fact per kind that decides whether the
    handle is the one a caller wants -- a factor's family and direction, a transform's
    standardization and floor, a neutralisation's level and floor. The floors are on both derived
    rows on purpose: `the_shipped_transform_and_neutralisation_floors_exceed_a_thin_market` is the
    reason an eight-name market reports `not_measured` everywhere, and the number that decides it
    is `min_cross_section`.

    The **note is reported by size rather than printed**. Nineteen notes averaging 2,800
    characters is 55 KB of prose in a terminal; a length beside a pointer to `factor describe` is
    the shape that makes them findable, and `--json` carries every one of them whole.
    """
    rows: list[tuple[str, str, str, str]] = []
    for key in ("factors", "transforms", "neutralizations"):
        entries = catalog[key]
        assert isinstance(entries, list)
        for entry in entries:
            declaration = entry["declaration"]
            note = entry["note"]
            rows.append(
                (
                    str(entry["kind"]),
                    str(entry["handle"]),
                    _decides(declaration),
                    "-" if note is None else f"{len(str(note))} chars",
                )
            )
    return tuple(rows)


def _decides(declaration: Mapping[str, object]) -> str:
    """The one line about a declaration that tells a caller whether it is the handle they want."""
    if declaration["schema_version"] == "factor-definition/v1":
        fields = declaration["required_fields"]
        assert isinstance(fields, list)
        reads = ", ".join(sorted({f"{item['dataset']}.{item['column']}" for item in fields}))
        return f"{declaration['family']}, {declaration['direction']}, reads {reads}"
    if declaration["schema_version"] == "factor-transform/v1":
        winsorization = declaration["winsorization"]
        assert isinstance(winsorization, dict)
        return (
            f"{winsorization['method']} winsorization, {declaration['standardization']} "
            f"standardization, min_cross_section {declaration['min_cross_section']}"
        )
    return (
        f"{declaration['industry_level']} industry, {declaration['market_cap_measure']} "
        f"({declaration['market_cap_scale']}), min_cross_section {declaration['min_cross_section']}"
    )


# --- building the three stored tiers: `openalpha factor build` (V2-P3-019) ----------------------


BuildTier = FactorTier
"""What `--tier` names: the **highest** tier a build stores, in `FACTOR_TIER_ORDER`'s vocabulary.

The same three words a report's rows are called by, deliberately reused rather than re-spelled:
`--tier processed` stores the raw and processed partitions, `--tier neutralized` stores all three,
and a caller who has read one report already knows what the third one is.
"""

REQUIREMENT_BUILDERS: Final[Mapping[str, Callable[..., ReadinessRequirement]]] = MappingProxyType(
    {
        DAILY_DATASET: daily_requirement,
        DAILY_BASIC_DATASET: daily_basic_requirement,
        INDEX_DAILY_DATASET: index_price_requirement,
        **{dataset: financial_statement_requirement for dataset in FINANCIAL_STATEMENT_DATASETS},
    }
)
"""Which `panel_ingest` builder states the readiness question for each dataset a factor can read.

A **closed table**, and the closure is the point rather than the dispatch. `compute_factor` refuses
to build its own `ReadinessRequirement` -- "a gate that built its own could ask a dataset a
different question from the one its own reader asks, and the two verdicts would drift" -- so a
builder has to supply one per dataset, and the only correct source is the module that also owns the
reader. A dataset with no row here is refused **by name** in `_requirements` rather than defaulted
to a weaker question: the six rows cover every dataset the twenty shipped factors declare, and a
factor reading a seventh dataset arrives as a refusal naming the dataset instead of a build whose
coverage codes were decided by a requirement nobody wrote. `V2-P3-017`'s
`deducted_earnings_yield_ttm` is the first factor to reach the `fina_indicator` row, which was
written with the other three statement rows and had had no reader until then.

`financial_statement_requirement` appears four times because the four statement endpoints share one
builder that re-derives each dataset's own `required_fields` from the dataset name; `daily` and
`daily_basic` have separate builders because each carries its own column projection.

`V2-P3-016`'s `index_daily` is the seventh row and the third to take a calendar, which is what
`_CALENDAR_SCOPED_REQUIREMENTS` says beside it: an index is quoted on every open session, so its
requirement can state `required_dates` and a factor whose window silently skipped one would pair
a security's return on day t with the market's on day t-1 for the rest of the window.
"""

CADENCE_WAIVED_READS: Final[frozenset[str]] = frozenset({STOCK_BASIC_DATASET})
"""The reads this face takes off the caller's `--max-staleness-days`, and why it is one name.

`V2-P4-064`. `--max-staleness-days` is a *session* bound -- its own refusal says what it is for,
"a price panel whose newest session is a month old has missed a month of the market" -- and that
sentence is only true of a dataset that publishes on every open session. `stock_basic` publishes
when a security lists or delists, so its age measures the market's own corporate-action calendar
and not this fetch, and a caller who wanted a five-day bound on the price panel had to set the
flag to 20--25 days to get any build at all -- which switches off the check the flag exists for.
Measured on a panel **one day old**: `the security registry cannot be read ...: ['stale'];
stock_basic reaches 2026-01-19T16:00Z, which is 17 days, 17:00:00 behind ... (tolerance 5 days)`.

**The same repository already gives this answer one command over.**
`panel_doctor.DATASET_CADENCE` declares five datasets `event_driven` and `freshness_policy`
returns `max_staleness=None` for that cadence, with the reason on the record: "a year with no
rows is an ordinary year, not a missed fetch". This is that rule, applied at the one read on this
face that both takes the caller's bar and is on an event clock.

**One of the five, and the other four are out for four different measured reasons rather than by
omission.** `test_the_waived_reads_are_a_named_subset_of_the_doctors_event_driven_set` states the
complement as a literal set and is what turns red if a sixth event-driven dataset is declared:

- `namechange` and `suspend_d` are read on the *run* path only, and every read there passes
  `max_staleness=None` as a literal already -- `FactorRunRequest` carries no freshness bound at
  all, and this module's own docstring says why ("A freshness policy for a *run*") -- so the flag
  never reaches them, and a row here would be a member no call site can use.
- `index_member_all` is read through `panel_neutralization
  .load_industry_market_cap_cross_section`, which states **one** `max_staleness` for it and for
  `daily_basic` together -- and `daily_basic` is on the session clock, so waiving there would
  waive a bound that is doing its job. Splitting it is an edit to that module.
- `index_classify` is not reachable from this face at all; `RESEARCH_PLANE_DATASETS` in
  `tests/unit/test_panel_ingest_import_isolation.py` says so, and this set is written in terms of
  the constants this module already imports for exactly that reason. Measured rather than
  reasoned: a draft that imported `INDUSTRY_TREE_DATASET`, `INDUSTRY_MEMBERSHIP_DATASET` and
  `SUSPENSION_DATASET` to spell a five-member set widened that audit's `named` set past this
  module's declared `reached` and turned five of its tests red.

**Not the quarterly datasets either, and that is a wall rather than a choice.** `income`,
`balancesheet`, `cashflow` and `fina_indicator` take the caller's bar unchanged, because
`compute_factor._validate_requirements` refuses a *waived* `max_staleness` for every dataset a
factor **reads** -- a waived bound there accepts a slice reaching arbitrarily far short of `as_of`
while every structural check clears, which is the wall
`test_the_waiver_this_command_offers_is_refused_by_the_engine_that_reads_the_bound` names. The
one name above is read *outside* that requirement set, which is what makes waiving it available
at all; so is `index_member_all`, which is why that one is a shared-argument problem rather than
an engine one. The statement half and the `index_member_all` half are recorded together as
`KNOWN_FACTOR_RUN_LIMITATIONS
.the_freshness_bar_is_waived_by_cadence_only_where_the_read_is_outside_the_engine`.
"""


def _event_clock_bound(dataset: str, requested: timedelta | None) -> timedelta | None:
    """`requested`, unless `dataset` is one this face reads off an event clock -- then no bound.

    A function rather than a conditional at the call site, so that the rule and the reason live
    together and a second read added to `CADENCE_WAIVED_READS` needs no second spelling of it.
    """
    return None if dataset in CADENCE_WAIVED_READS else requested


_CALENDAR_SCOPED_REQUIREMENTS: Final[frozenset[str]] = frozenset(
    {DAILY_DATASET, DAILY_BASIC_DATASET, INDEX_DAILY_DATASET}
)
"""Which of `REQUIREMENT_BUILDERS`' rows take a `TradingCalendar` as their first argument.

A named set rather than a tuple literal inside the loop, because it grew: the condition used to
be `name in (DAILY_DATASET, DAILY_BASIC_DATASET)` and the signature difference it encodes is
"this dataset publishes on every open session, so its requirement can state a date census". The
four statement builders take a `dataset=` keyword instead, because a filing has no calendar.
"""

MAX_BUILD_YEAR: Final[int] = 2999
"""The upper bound `--year` is refused past, so a typo cannot become a five-thousand-year read."""

MIN_BUILD_YEAR: Final[int] = 1990
"""The lower bound: the Shanghai exchange opened in 1990 and no panel partition predates it."""


@dataclass(frozen=True, slots=True, kw_only=True)
class FactorBuildRequest:
    """One resolved factor build: which factor, at which instants, over which partition years.

    Built only by `factor_build_request`, which is what makes the command line and the SDK ask one
    question -- `FactorRunRequest`'s arrangement one plane over and for its reason.
    """

    definition: FactorDefinition
    tier: BuildTier
    transform: FactorTransformSpec | None
    """`None` exactly when `tier == "raw"`. See `factor_build_request` for why an unused
    `--transform` is refused rather than ignored."""
    neutralization: FactorNeutralizationSpec | None
    """`None` unless `tier == "neutralized"`."""
    as_ofs: tuple[datetime, ...]
    """The prediction instants to compute a cross section at, ascending and distinct.

    **Instants and not dates**, which is the one place this surface declines to be convenient. A
    stored observation's four panel clocks are all stamped with the `as_of` the build was made at,
    `factor run` groups its sample by that instant, and a date would leave the time of day to be
    invented by whichever face happened to run -- so two faces would build two different panels
    from one command line. `factor run --start/--end` then selects these by their **date** in
    `FACTOR_DATE_ZONE`, which is the human end of the same contract.
    """
    years: tuple[int, ...]
    """Every partition year every read of this build is scoped to, ascending and distinct.

    One year set for every dataset, and the consequences are stated rather than smoothed over,
    because they are the two things that make a first build come back empty:

    - The **statement** partitions are keyed by *announcement* year, not by report period. A factor
      declaring `lookback_periods=5` needs five contiguous filings, which is at least two
      announcement years, and a one-year `--year` gets `insufficient_history` for the whole cross
      section rather than an error.
    - The **registry** partitions are keyed by *lifecycle* year, and this year set is therefore
      the one dataset it is **not** the whole scope of. A security's row lives in the year it
      listed, so the newest partition is that year's listings rather than that year's market, and
      `load_stock_universe` reads every lifecycle year the store holds beneath the range asked for
      here. This paragraph used to say the opposite -- that naming a prefix "silently shortens the
      universe, which it reports through `UniverseCompleteness` rather than by refusing" -- and
      `V2-P4-059` measured what that cost: `--year 2026` over a 5,545-security store scored
      **eleven** names and exited 0. The horizon rule it describes is unchanged and still caps the
      snapshot at the newest year read; what changed is that the years below it are supplied.

    The same vocabulary `openalpha panel build --year` writes with, so the two commands' year
    arguments name the same partitions.
    """
    exchange: str
    max_staleness: timedelta | None
    """The freshness bound every read of this build carries, or `None` on the record.

    `run_factor_experiment` passes `max_staleness=None` everywhere and argues that freshness is
    `panel doctor`'s question. A build cannot do the same, because it does not *make* the reads --
    it hands `compute_factor` a `ReadinessRequirement`, and every builder in `panel_ingest` refuses
    to default this field ("the caller states a bound or states `None` on the record"). So the face
    makes the caller do exactly that, and a waiver is a flag rather than an omission.
    """
    subjects: tuple[str, ...]
    """The securities to evaluate, or `()` to take every code the stored registry knows.

    `()` is not "no securities": it is the documented default source, and it is the registry's
    **whole** membership rather than the day's listed cross section, so a delisted name is
    evaluated and coded `not_in_universe` instead of vanishing. `compute_factor` requires the two
    sets separately for exactly that reason -- `not_in_universe` is one of its five answers, and a
    subject list that already equalled the universe would make it unreachable.
    """
    supersedes_raw: tuple[str, ...]
    supersedes_processed: tuple[str, ...]
    supersedes_neutralized: tuple[str, ...]
    """Three lists and not one, because they name three contracts' fields.

    A partition is replaced whole and `_refuse_to_drop_a_stored_build` refuses a write that would
    drop a `manifest_id` the target partition holds; `supersedes` is how a rebuild says it means
    to. The three writers hold three different manifest partitions, and each refuses a name **no
    partition it touches holds** -- so one merged list would make every rebuild fail on the two
    tiers the id did not belong to. `_source_build`'s argument arriving on the write side: three
    names for one idea, not unified, because unifying them would let a plausible id from the wrong
    column through.
    """
    code_commit: str


def factor_build_request(
    *,
    factor: str,
    tier: str,
    transform: str,
    neutralization: str,
    as_ofs: Sequence[datetime],
    years: Sequence[int],
    exchange: str,
    max_staleness_days: int | None,
    waive_max_staleness: bool,
    subjects: Sequence[str],
    supersedes_raw: Sequence[str],
    supersedes_processed: Sequence[str],
    supersedes_neutralized: Sequence[str],
    code_commit: str,
    factors: FactorRegistry = FACTOR_DEFINITIONS,
    transforms: FactorTransformRegistry = FACTOR_TRANSFORMS,
    neutralizations: FactorNeutralizationRegistry = FACTOR_NEUTRALIZATIONS,
) -> FactorBuildRequest:
    """Resolve one face's parameters into the stated build both of them run.

    `factor_request`'s sibling, with the three registries as parameters for its reason. Every fault
    here is `FactorRequestError`: nothing in this function touches a store, so nothing it can say
    is a statement about the panel.

    **An option that decides nothing for the requested tier is refused rather than ignored**, in
    both directions. `--tier raw` with a `--transform` is a caller who believes they asked for two
    tiers and will get one; `--tier processed` without one cannot be resolved at all. This
    repository refuses a no-op waiver everywhere else for the same reason --
    `write_factor_panels` refuses a `supersedes` that matches nothing because "a typo would
    silently turn the guard off for the write it accompanied" -- and a silently unused policy
    option is that shape with the guard replaced by a spec.

    **`--max-staleness-days` and `--waive-max-staleness` are exclusive and one is required.** Not a
    default, because `daily_requirement`, `stock_universe_requirement` and
    `financial_statement_requirement` each refuse to choose a bound for a caller and each says why:
    a price panel whose newest session is a month old has missed a month of the market. A face that
    defaulted it would be choosing silence on all six datasets at once.
    """
    definition = resolve_factor(factor, registry=factors)
    resolved_tier = _build_tier(tier)
    transform_spec = _tier_spec(
        "--transform",
        transform,
        wanted=resolved_tier in ("processed", "neutralized"),
        resolve=transforms.get,
        tier=resolved_tier,
    )
    neutralization_spec = _tier_spec(
        "--neutralization",
        neutralization,
        wanted=resolved_tier == "neutralized",
        resolve=neutralizations.get,
        tier=resolved_tier,
    )
    if type(exchange) is not str or not exchange or exchange != exchange.strip():
        raise FactorRequestError(
            f"exchange must be a non-empty name with no surrounding whitespace; got {exchange!r}"
        )
    if len(code_commit.strip()) < 7:
        raise FactorRequestError(
            f"--code-commit must be at least 7 characters; got {code_commit!r}. It is inside every "
            "manifest_id this build stamps, because different code may compute a different number "
            "from the same rows"
        )
    return FactorBuildRequest(
        definition=definition,
        tier=resolved_tier,
        transform=transform_spec,
        neutralization=neutralization_spec,
        as_ofs=_build_instants(as_ofs),
        years=_build_years(years),
        exchange=exchange,
        max_staleness=_build_staleness(max_staleness_days, waive_max_staleness),
        subjects=_distinct("--subject", subjects),
        supersedes_raw=_distinct("--supersedes-raw", supersedes_raw),
        supersedes_processed=_distinct("--supersedes-processed", supersedes_processed),
        supersedes_neutralized=_distinct("--supersedes-neutralized", supersedes_neutralized),
        code_commit=code_commit.strip(),
    )


def _build_tier(tier: str) -> BuildTier:
    """`--tier` as the declared `Literal`, resolved by search rather than widened by a cast.

    A loop over `FACTOR_TIER_ORDER` rather than `cast(FactorTier, tier)`, so the static type is
    narrowed by the same comparison that decides the refusal -- a fourth tier added upstream
    reaches this function as a value it can return rather than as a string a cast waved through.
    """
    for declared in FACTOR_TIER_ORDER:
        if declared == tier:
            return declared
    raise FactorRequestError(
        f"--tier must be one of {list(FACTOR_TIER_ORDER)}; got {tier!r}. It names the highest tier "
        "this build stores, and every tier below it is stored too"
    )


def _tier_spec(
    flag: str,
    handle: str,
    *,
    wanted: bool,
    resolve: Callable[[str], _T],
    tier: BuildTier,
) -> _T | None:
    """One tier-conditional spec option: required when the tier uses it, refused when it does not.

    See `factor_build_request` for why the second half is a refusal rather than a shrug.
    """
    name = handle.strip()
    if wanted and not name:
        raise FactorRequestError(
            f"{flag} is required for --tier {tier}, because that tier is computed from it and "
            "there is no default a spec would be honest to have. `openalpha factor list` prints "
            "every declared one"
        )
    if not wanted and name:
        raise FactorRequestError(
            f"{flag} {name!r} decides nothing for --tier {tier} and is refused rather than "
            "ignored; an option that is accepted and unused is one a caller reads as having taken "
            "effect"
        )
    if not name:
        return None
    try:
        return resolve(name)
    except ValueError as error:
        raise FactorRequestError(str(error)) from error


def _build_instants(as_ofs: Sequence[datetime]) -> tuple[datetime, ...]:
    """The prediction instants, refusing an empty set, a naive one and a repeated one."""
    if not as_ofs:
        raise FactorRequestError(
            "--as-of names no prediction instant; a build over no cross section at all would "
            "report success and store nothing, which is the empty success this plane refuses"
        )
    for instant in as_ofs:
        if instant.tzinfo is None or instant.utcoffset() is None:
            raise FactorRequestError(
                f"--as-of must be a timezone-aware instant; got {instant.isoformat()!r}. A "
                "point-in-time build made in a guessed timezone reads a different day's rows"
            )
    ordered = tuple(sorted(set(as_ofs)))
    if len(ordered) != len(as_ofs):
        raise FactorRequestError(
            "--as-of names the same instant twice; two builds of one factor at one as_of are two "
            "answers to one question, and write_factor_panels refuses to store both"
        )
    return ordered


def _build_years(years: Sequence[int]) -> tuple[int, ...]:
    """The partition years, refusing an empty set, a repeat and a year no panel can hold."""
    if not years:
        raise FactorRequestError(
            "--year names no partition year; every read this build makes is scoped to a year set, "
            "and compute_factor's own lookback refusal is stated in terms of it"
        )
    ordered = tuple(sorted(set(years)))
    if len(ordered) != len(years):
        raise FactorRequestError(f"--year names a year twice: {sorted(years)}")
    outside = [year for year in ordered if not MIN_BUILD_YEAR <= year <= MAX_BUILD_YEAR]
    if outside:
        raise FactorRequestError(
            f"--year {outside} is outside [{MIN_BUILD_YEAR}, {MAX_BUILD_YEAR}]; no panel partition "
            "predates the exchange, and a year past the bound is a typo that would be read as a "
            "very long scan"
        )
    return ordered


def _build_staleness(days: int | None, waived: bool) -> timedelta | None:
    """The freshness bound, as the recorded decision it has to be. See `FactorBuildRequest`."""
    if waived and days is not None:
        raise FactorRequestError(
            f"--waive-max-staleness and --max-staleness-days {days} state two different bounds; "
            "give one"
        )
    if waived:
        return None
    if days is None:
        raise FactorRequestError(
            "state --max-staleness-days N or --waive-max-staleness. Every panel_ingest requirement "
            "builder refuses to default this and says why: a price panel whose newest session is a "
            "month old has missed a month of the market, so a defaulted bound is silence about all "
            "six datasets at once"
        )
    if days < 1:
        raise FactorRequestError(
            f"--max-staleness-days must be at least 1; got {days}. Zero is not a tighter bound, it "
            "is a bound no stored partition can satisfy -- waive it explicitly instead"
        )
    return timedelta(days=days)


def _distinct(flag: str, values: Sequence[str]) -> tuple[str, ...]:
    """A repeated string option, refusing blanks and duplicates."""
    cleaned = [value.strip() for value in values]
    if any(not value for value in cleaned):
        raise FactorRequestError(f"{flag} was given an empty value")
    if len(set(cleaned)) != len(cleaned):
        raise FactorRequestError(f"{flag} names the same value twice: {sorted(cleaned)}")
    return tuple(cleaned)


@dataclass(frozen=True, slots=True, kw_only=True)
class FactorBuildReport:
    """What one `factor build` computed and stored, tier by tier.

    Carries counts and identities rather than the panels themselves: the panels are the store's
    now, and a report that shipped them would be a second copy of the partition for a caller to
    read numbers off instead of reading them back through `load_factor_observations` -- the
    visibility-filtered door every reader is supposed to take.
    """

    factor: str
    factor_id: str
    tier: BuildTier
    as_ofs: tuple[datetime, ...]
    subject_count: int
    universe_counts: tuple[int, ...]
    """One listed-cross-section size per `as_ofs` entry, in the same order.

    Reported because it is the number that most often explains an unusable build: both shipped
    derived specs declare `min_cross_section=100`, so a market narrower than that stores a coverage
    code for every name and no value, and a caller staring at an all-`not_measured` grid needs to
    see the size the build actually had rather than infer it.
    """
    manifest_ids: Mapping[str, tuple[str, ...]]
    """The stored build identities, keyed by tier. Empty for a tier this build did not store."""
    coverage: Mapping[str, Mapping[str, int]]
    """Per tier, how many observations each coverage code claimed.

    The census the write already validated, projected for a human. It is the honest answer to "did
    that work": a build that stored five thousand `input_missing` rows succeeded and produced
    nothing, and this is where that is visible without a second query.
    """
    partitions: tuple[str, ...]
    """Every partition written, as `dataset@year`, in write order."""


def build_factor_panels(
    store: PanelStore, request: FactorBuildRequest, *, built_at: datetime
) -> FactorBuildReport:
    """Compute the requested tiers at every requested instant, then store them.

    The one entry point both faces call. It re-derives nothing: `compute_factor`,
    `apply_factor_transform` and `apply_factor_neutralization` produce every number, and the three
    `write_*_factor_panels` functions run every write-time guard.

    **Every computation happens before the first write, and that ordering is the deliverable.**
    `write_factor_panels` already argues it for its own partitions ("a refusal changes nothing at
    all"); here it spans three writers, and the case it exists for is the one the acceptance review
    named: a neutralised tier that cannot be assembled at a requested instant would otherwise leave
    a raw and a processed partition on disk with no residual beside them -- which is exactly the
    store shape `the_three_tiers_must_have_been_built_at_the_same_instants` makes `factor run`
    refuse, reported one command too late and about a different thing.

    So a build that cannot finish stores nothing and says why, by name. See
    `the_builder_cannot_produce_a_residual_for_a_session_that_has_not_closed`.
    """
    computed = [
        _computed(store, request, as_of=as_of, built_at=built_at) for as_of in request.as_ofs
    ]
    panels = [panel for panel, _count in computed]
    processed: list[ProcessedFactorPanel] = []
    neutralized: list[NeutralizedFactorPanel] = []
    if request.transform is not None:
        transform = request.transform
        processed = [
            apply_factor_transform(
                panel, transform, code_commit=request.code_commit, built_at=built_at
            )
            for panel in panels
        ]
    if request.neutralization is not None:
        neutralized = [
            _neutralized(store, request, panel=panel, built_at=built_at) for panel in processed
        ]
    written = list(
        _written(
            lambda: write_factor_panels(store, panels, supersedes=request.supersedes_raw),
            tier="raw",
            flag="--supersedes-raw",
        )
    )
    if processed:
        written.extend(
            _written(
                lambda: write_processed_factor_panels(
                    store, processed, supersedes=request.supersedes_processed
                ),
                tier="processed",
                flag="--supersedes-processed",
            )
        )
    if neutralized:
        written.extend(
            _written(
                lambda: write_neutralized_factor_panels(
                    store, neutralized, supersedes=request.supersedes_neutralized
                ),
                tier="neutralized",
                flag="--supersedes-neutralized",
            )
        )
    return FactorBuildReport(
        factor=request.definition.qualified_key,
        factor_id=request.definition.factor_id,
        tier=request.tier,
        as_ofs=request.as_ofs,
        subject_count=len(panels[0].observations),
        universe_counts=tuple(count for _panel, count in computed),
        # Three names for one idea, and they are not unified here for `_source_build`'s reason:
        # `manifest_id`, `transform_manifest_id` and `neutralization_manifest_id` are three
        # different contracts' fields, and reading each off its own panel type is what stops a
        # plausible id from the wrong column being reported under the wrong tier.
        manifest_ids={
            "raw": tuple(sorted({panel.manifest.manifest_id for panel in panels})),
            "processed": tuple(
                sorted({panel.manifest.transform_manifest_id for panel in processed})
            ),
            "neutralized": tuple(
                sorted({panel.manifest.neutralization_manifest_id for panel in neutralized})
            ),
        },
        coverage={
            "raw": _census(
                [observation.coverage for panel in panels for observation in panel.observations]
            ),
            "processed": _census(
                [observation.coverage for panel in processed for observation in panel.observations]
            ),
            "neutralized": _census(
                [
                    observation.coverage
                    for panel in neutralized
                    for observation in panel.observations
                ]
            ),
        },
        partitions=tuple(f"{ref.dataset}@{ref.year}" for ref in written),
    )


def _written(
    write: Callable[[], Sequence[PartitionRef]], *, tier: BuildTier, flag: str
) -> Sequence[PartitionRef]:
    """One tier's write, with a refused write turned into this module's own `blocked`.

    The write-time guards raise the panel plane's own exception types, and every one of them here
    is a statement about what the store already holds rather than about the request: a partition is
    replaced whole, so `_refuse_to_drop_a_stored_build` refuses a call that would drop a stored
    `manifest_id`. Enveloping them here rather than at each face is what keeps `cli.py` from
    importing `panel_factors` to name them -- and it is what lets the refusal carry the **remedy**,
    which the guard itself cannot know because `supersedes` is three different options one plane up.

    **The remedy narrowed with `V2-P4-071` and the sentence had to narrow with it.** It used to
    say "every instant of one partition year has to be built in one invocation", which was the
    honest reading while the write was a whole-partition replace with nothing carried forward. Now
    `appended_to_the_stored_year` puts the stored builds back, so a *new* instant never reaches
    this branch at all and what does is a second answer to a question the year already answered.
    Telling a caller to rebuild the year would send them to do work that is no longer theirs.
    """
    try:
        return write()
    except (FactorEngineError, NeutralizationEngineError, PanelStorageError) as error:
        raise FactorRunBlockedError(
            f"the {tier} partitions were refused: {error}. Adding an instant to a partition year "
            "no longer needs the year rebuilt -- the write carries the stored builds forward "
            "(`V2-P4-071`) -- so what is left here is a build that answers a question the "
            f"partition already has an answer to, and a rebuild that means to replace one names "
            f"it with {flag}. Nothing this invocation computed after this point was written"
        ) from error


def _census(codes: Sequence[str]) -> dict[str, int]:
    """How many observations claimed each coverage code, ascending by code."""
    counts: dict[str, int] = {}
    for code in codes:
        counts[code] = counts.get(code, 0) + 1
    return dict(sorted(counts.items()))


def _computed(
    store: PanelStore, request: FactorBuildRequest, *, as_of: datetime, built_at: datetime
) -> tuple[FactorPanel, int]:
    """One raw cross section, and the size of the universe it was scored against.

    The calendar and the registry are read **at this instant** rather than once for the whole
    build, and that is not an economy foregone. Both loaders take an `as_of`; reading them once at
    the latest instant would let an earlier cross section's `required_dates` be derived from
    sessions that had not been published when it was stamped, which is look-ahead installed in the
    readiness question itself. Reading once at the earliest would under-cover the later ones. Per
    instant is the only setting that is right for every instant.

    **How far that is measured, stated rather than implied.** The *registry* half is driven by two
    tests in `tests/integration/test_factor_build.py`, one per direction, and it takes two because
    the directions fail differently (`V2-P4-113`).
    `test_the_registry_is_read_at_each_prediction_instant_and_not_once_for_the_build` covers the
    **early** read: a delisting between the two instants, and a registry pinned at `as_ofs[0]` is
    refused outright because the second instant's day is beyond the first instant's snapshot.
    (The sentence that stood here described that test as putting "two instants eight days apart
    under a seven-day freshness bound" -- its own separator until `V2-P4-064` removed it, and its
    docstring has said so since. It uses `max_staleness_days=30` and a delisting now.)

    `test_a_registry_read_at_the_last_instant_hands_an_earlier_one_a_security_that_had_not_listed`
    covers the **late** read, which is the look-ahead one and which nothing measured until
    `V2-P4-113`. It cannot be measured on `universe_counts`, and not because a fixture is thin:
    `stock_basic` is `calendar_static`, so a row is visible exactly when its lifecycle date is at
    or before the reading day, which is exactly when it can change `listed_on` for that day.
    Reading the registry later therefore cannot move any earlier instant's membership on any
    fixture. What it does move is `subjects`, taken from `universe.securities`, which is not
    date-filtered -- so a late read hands an earlier cross section a security whose listing had
    not happened yet, and that test counts it. The *calendar* half is **not separable on any
    fixture this suite has**, and the reason is a property of the fixture rather than of the code:
    `tests/panel_fixtures.py::_calendar_batch` stamps every session row's `available_time` at
    1 January, so the whole year's calendar is visible from the first instant and a read at any
    later one returns the same value. Separating it would need a `trade_cal` partition with
    staggered availability -- which is the real-world shape `KNOWN_CALENDAR_LOOKAHEAD` records and
    which nothing here generates. A mutation retargeting this one read at `request.as_ofs[0]`
    therefore survives; what is covered is the parameter, which every read in this function shares.
    """
    day = as_of.astimezone(FACTOR_DATE_ZONE).date()
    calendar = _read(
        lambda: load_trading_calendar(
            store, exchange=request.exchange, years=request.years, as_of=as_of
        ),
        store=store,
        what=f"the {request.exchange} trading calendar",
    )
    universe = _read_registry(
        lambda: load_stock_universe(
            store,
            years=request.years,
            as_of=as_of,
            max_staleness=_event_clock_bound(STOCK_BASIC_DATASET, request.max_staleness),
        ),
        store=store,
    )
    try:
        listed = universe.listed_on(day)
    except StockUniverseError as error:
        raise FactorRunBlockedError(
            f"the stored registry cannot say who was listed on {day.isoformat()}: {error}"
        ) from error
    subjects = request.subjects or tuple(entry.ts_code for entry in universe.securities)
    requirements = _requirements(request, calendar=calendar, as_of=as_of)
    panel = _read(
        lambda: compute_factor(
            store,
            request.definition,
            as_of=as_of,
            subjects=subjects,
            universe=listed,
            requirements=requirements,
            code_commit=request.code_commit,
            built_at=built_at,
        ),
        store=store,
        what=f"the {request.definition.qualified_key} cross section at {as_of.isoformat()}",
    )
    return panel, len(listed)


def _requirements(
    request: FactorBuildRequest, *, calendar: TradingCalendar, as_of: datetime
) -> dict[str, ReadinessRequirement]:
    """One `ReadinessRequirement` per dataset this factor reads, from `REQUIREMENT_BUILDERS`.

    A dataset with no builder is refused by name. `compute_factor` cross-checks each requirement it
    is handed -- the key, the `as_of` and the `required_fields` -- so a wrong one fails there
    rather than several layers down; what it cannot check is a requirement nobody built, and that
    is what this refusal is for.
    """
    missing = [name for name in request.definition.datasets if name not in REQUIREMENT_BUILDERS]
    if missing:
        raise FactorRequestError(
            f"{request.definition.qualified_key} reads {missing}, which this command has no "
            f"readiness builder for; it knows {sorted(REQUIREMENT_BUILDERS)}. A requirement "
            "invented here would ask a weaker question than the reader asks, which is exactly what "
            "compute_factor refuses to let a caller do"
        )
    built: dict[str, ReadinessRequirement] = {}
    for name in request.definition.datasets:
        builder = REQUIREMENT_BUILDERS[name]
        try:
            if name in _CALENDAR_SCOPED_REQUIREMENTS:
                built[name] = builder(
                    calendar, years=request.years, as_of=as_of, max_staleness=request.max_staleness
                )
            else:
                built[name] = builder(
                    dataset=name,
                    years=request.years,
                    as_of=as_of,
                    max_staleness=request.max_staleness,
                )
        except (TradingCalendarError, ValueError) as error:
            raise FactorRunBlockedError(
                f"the readiness question for {name} at {as_of.isoformat()} cannot be stated over "
                f"{list(request.years)}: {error}"
            ) from error
    return built


RESIDUAL_REMEDIES: Final[Mapping[CalendarDayStatus, str]] = MappingProxyType(
    {
        CalendarDayStatus.closed: (
            "Move --as-of to a session: the exchange was never open on that day, so no fetch "
            "and no later run produces one for it."
        ),
        CalendarDayStatus.trading: (
            "That session is open and has not published yet -- it becomes readable after its "
            "own 16:30 Asia/Shanghai close, so move --as-of to an instant after that, or build "
            "--tier processed now and the residual once the session has closed."
        ),
        CalendarDayStatus.beyond_horizon: (
            "The stored calendar does not reach that day at all, so whether it is a session is "
            "not yet knowable here: fetch the later sessions first "
            "(`openalpha panel build --dataset trade_cal --year <year>`)."
        ),
    }
)
"""One remedy per calendar verdict, because the three are answered by three different actions.

`V2-P4-109`. `V2-P4-108` made this refusal reachable and left it saying all of the remedies at
once: "Build --tier processed at this instant, or move --as-of to after the session's close, or
name the missing year, or fetch the later sessions first." Three of those four are wrong for a
Saturday and one is wrong for a session that simply has not closed, and both exit `1` --
`FACTOR_EXIT["blocked"]`, which is `PanelExit.unhealthy`. `PanelExit`'s own docstring says the
codes exist so a CI job can tell "re-fetch the data" from "edit the command line", and a message
carrying both remedies gives back with one hand what the code was split to provide.

**The verdict is three-valued and so is this table, deliberately.** `CalendarDayStatus` refuses
to be a `bool` for the reason that applies here exactly: "the calendar says this is not a
session" and "the calendar does not reach this day" have opposite remedies, and collapsing them
would put "fetch the later sessions" in front of somebody who asked about a Saturday.

**The exit code is not split and that is a decision with a reason**, recorded as
`a_closed_day_and_an_unclosed_session_share_one_exit_code`: `bad_request` means "no amount of
re-fetching fixes it", and a day reported `closed` here can also be a day whose `trade_cal`
partition is merely short -- for which re-fetching is the remedy. Answering `3` there would stop
a CI job retrying a panel a retry would repair.
"""


def _residual_remedy(calendar: TradingCalendar, *, day: date, years: Sequence[int]) -> str:
    """The remedy sentence for the one state this day is in, plus the narrowing a caller owns.

    The `--year` clause is appended rather than being a fourth row of `RESIDUAL_REMEDIES`,
    because it is orthogonal to the calendar verdict: naming fewer years than the stored
    membership years at or before the day leaves an assignment unread whether the day is a
    session or not. It is the one remedy in the old sentence that was never wrong, only
    undirected.
    """
    remedy = RESIDUAL_REMEDIES[calendar.day_status(day)]
    return (
        f"{remedy} If instead the industry read is what is short, --year names "
        f"{list(years)} and every stored membership year at or before that day has to be in it."
    )


def _neutralized(
    store: PanelStore,
    request: FactorBuildRequest,
    *,
    panel: ProcessedFactorPanel,
    built_at: datetime,
) -> NeutralizedFactorPanel:
    """One residual cross section, or the named refusal that says why there cannot be one.

    The whole of `the_builder_cannot_produce_a_residual_for_a_session_that_has_not_closed` lives
    here, and it has been narrowed twice. `V2-P4-026` took `daily_basic`'s year-wide bound off
    it; `V2-P4-028` took `index_member_all`'s off it, by putting
    `load_industry_market_cap_cross_section` on `panel_ingest.load_industry_cross_section` -- a
    door that takes the **day** as an argument, so a membership event later than the day being
    priced no longer refuses the read. What used to be "a residual exists only at a prediction
    instant at or after the last stored *assignment* of every membership year this read touches",
    which on a real corpus is the annual constituent review, is gone.

    What is left is one session wide and is arithmetic.
    `_refuse_a_cross_section_that_is_not_this_panels` requires the returned cross section's
    `as_of` to equal this panel's exactly, so the read cannot simply be made later, and the cross
    section is read for the day that instant falls on -- so an instant before that day's own
    close, or on a day the exchange was shut, has no session to read and is refused.

    The refusal is `blocked` rather than `bad_request` because it is a conflict with what the panel
    currently holds -- extending the panel is one remedy and moving `--as-of` forward is the other
    -- and it names both, plus the limitation code, so the message is actionable rather than a
    class name.
    """
    neutralization = request.neutralization
    if neutralization is None:  # pragma: no cover - `build_factor_panels` guards the call
        raise FactorRequestError("no neutralisation was resolved for this build")
    day = panel.as_of.astimezone(FACTOR_DATE_ZONE).date()
    subjects = tuple(observation.subject for observation in panel.observations)
    calendar = _read(
        lambda: load_trading_calendar(
            store, exchange=request.exchange, years=request.years, as_of=panel.as_of
        ),
        store=store,
        what=f"the {request.exchange} trading calendar",
    )
    try:
        section = load_industry_market_cap_cross_section(
            store,
            neutralization,
            subjects=subjects,
            day=day,
            as_of=panel.as_of,
            calendar=calendar,
            membership_years=request.years,
            max_staleness=request.max_staleness,
        )
    except _CROSS_SECTION_FAULTS as error:
        message = (
            f"no {neutralization.qualified_key} cross section can be assembled at "
            "{cause}. This is "
            "the_builder_cannot_produce_a_residual_for_a_session_that_has_not_closed: the "
            "residual must carry the processed panel's own instant, and both foreign reads are "
            "taken for the day that instant falls on -- so a residual exists only at a prediction "
            "instant at or after that day's own close, on a day the exchange was open, with "
            f"every stored membership year at or before it named in {list(request.years)}. "
            "(Neither read states a whole-partition bound any more: V2-P4-026 gave daily_basic an "
            f"as-of-sensitive session-level door and V2-P4-028 gave index_member_all a day-scoped "
            f"one.) {_residual_remedy(calendar, day=day, years=request.years)} Nothing was "
            "written"
        )
        prefix = f"{panel.as_of.isoformat()}: "
        raise FactorRunBlockedError(
            message.format(cause=prefix + str(error)),
            disclosable=message.format(cause=prefix + _without_store_path(str(error), store)),
        ) from error
    try:
        return apply_factor_neutralization(
            panel, neutralization, section, code_commit=request.code_commit, built_at=built_at
        )
    except (FactorEngineError, NeutralizationEngineError, ValueError) as error:
        raise FactorRunBlockedError(
            f"the {neutralization.qualified_key} residuals at {panel.as_of.isoformat()} could not "
            f"be computed: {error}"
        ) from error


BUILD_VIEW_SCHEMA_VERSION: Final[str] = "factor-build-view/v1"
"""The version of the body `build_view` hands out. Its own; see `VIEW_SCHEMA_VERSION`."""


def build_view(report: FactorBuildReport) -> dict[str, object]:
    """One build report as JSON-ready data, for `--json` and for the SDK's own rendering.

    Ten keys, every one a projection of `FactorBuildReport` and none of them recomputed. Unlike
    `experiment_view` there is no seal to ship whole -- a build stores partitions rather than a
    document -- so this **is** the rendering, which is why `tests/integration/test_factor_build.py::
    test_every_key_the_build_faces_render_is_separately_falsifiable` perturbs one key at a time and
    requires an assertion to notice each. That is `panel_view.py`'s measured lesson applied where
    it actually bites: 54 rendered keys with 100% line coverage and 19 never asserted.
    """
    return {
        "schema_version": BUILD_VIEW_SCHEMA_VERSION,
        "factor": report.factor,
        "factor_id": report.factor_id,
        "tier": report.tier,
        "as_ofs": [instant.isoformat() for instant in report.as_ofs],
        "subject_count": report.subject_count,
        "universe_counts": list(report.universe_counts),
        "manifest_ids": {tier: list(ids) for tier, ids in report.manifest_ids.items()},
        "coverage": {tier: dict(counts) for tier, counts in report.coverage.items()},
        "partitions": list(report.partitions),
    }


def build_rows(report: FactorBuildReport) -> tuple[tuple[str, str, str, str], ...]:
    """The build as `(tier, builds, observations, coverage)` strings, in `FACTOR_TIER_ORDER`.

    All three tiers always, so a tier this build did not store occupies its row reading `-` rather
    than vanishing: "the neutralised tier was not asked for" and "the neutralised tier is missing"
    are two different states of a store, and a reader about to run `factor run` needs to tell them
    apart. `attribution_rows`' rule, on the other command.
    """
    rows: list[tuple[str, str, str, str]] = []
    for tier in FACTOR_TIER_ORDER:
        builds = report.manifest_ids[tier]
        counts = report.coverage[tier]
        rows.append(
            (
                tier,
                "-" if not builds else str(len(builds)),
                "-" if not counts else str(sum(counts.values())),
                "-" if not counts else ", ".join(f"{code} {n}" for code, n in counts.items()),
            )
        )
    return tuple(rows)
