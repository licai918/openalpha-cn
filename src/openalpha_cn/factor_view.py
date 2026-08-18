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
from openalpha_cn.domain.adjustment import AdjustmentHistory
from openalpha_cn.domain.daily_prices import DAILY_BASIC_DATASET, DAILY_DATASET, DailyBar
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
from openalpha_cn.domain.name_history import NameHistory, RiskWarning
from openalpha_cn.domain.price_limits import PriceLimit
from openalpha_cn.domain.stock_universe import StockUniverse, StockUniverseError
from openalpha_cn.domain.trading_calendar import TradingCalendar, TradingCalendarError
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
    load_factor_observations,
    load_processed_factor_observations,
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
            "one V2-P3-014's acceptance criterion is decided on. Two refusals outside this module "
            "still bound which schedules are reachable at all: index_member_all is read whole "
            "partition (V2-P4-027) and no cross section before 2021-12-13 is assemblable."
        ),
    ),
    FactorRunLimitation(
        code="the_builder_cannot_produce_a_residual_before_its_years_stored_horizon",
        detail=(
            "`openalpha factor build` (V2-P3-019) computes and stores the raw and processed tiers "
            "at any prediction instant the panel covers, so a store built only by `openalpha "
            "panel build` now reaches `factor run`. It cannot put the third tier at an arbitrary "
            "instant either, and the bound is arithmetic rather than a policy -- but V2-P4-026 "
            "moved WHICH dataset states it, and the entry is narrower than it was. It used to "
            "name both foreign reads: daily_basic is now read one session at a time under a "
            "WHERE available_time <= as_of predicate (panel_ingest._read_visible_price_session), "
            "so it no longer bounds anything a caller can reach. What remains is "
            "load_industry_histories, which still takes read_if_ready and refuses a membership "
            "partition whose newest assignment post-dates the as_of, together with "
            "_refuse_a_cross_section_that_is_not_this_panels requiring the characteristic cross "
            "section's as_of to equal the processed panel's exactly. The two admit only a "
            "prediction instant at or after the last stored ASSIGNMENT of every membership year "
            "the read touches -- which for a whole-year corpus is the year's last "
            "reclassification rather than its last session, and on a real corpus is the annual "
            "constituent review. `--tier neutralized` at an earlier instant is refused BY NAME "
            "and writes nothing, rather than storing two tiers and leaving `factor run` to report "
            "the third as an empty in-range read. V2-P4-027 is where the remaining half is "
            "solved."
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


def _read(reader: Callable[[], _T], *, store: PanelStore, what: str) -> _T:
    """Run one panel read, turning its refusal into `FactorPanelUnreadableError`.

    The local message names the store and `disclosable` does not, `panel_view.stored_calendar`'s
    arrangement and for its reason: the CLI and the SDK are inside the process that owns the store
    and a message naming it tells them nothing they did not configure, while a response body hands
    that path to whoever could reach the port.
    """
    try:
        return reader()
    except _PANEL_FAULTS as error:
        raise FactorPanelUnreadableError(
            f"{what} could not be read out of {store.root}: {error}",
            disclosable=(
                f"{what} could not be read out of {PANEL_STORE_PLACEHOLDER}: "
                f"{_without_store_path(str(error), store)}"
            ),
        ) from error


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
        self.universe: StockUniverse = _read(
            lambda: load_stock_universe(store, years=years, as_of=as_of, max_staleness=None),
            store=store,
            what="the security registry",
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
            is_st=history is not None and history.risk_warning_on(day) is not RiskWarning.none,
            **published_limit_fields(limit),
        )

    def label(self, ts_code: str, window: LabelWindow) -> OutcomeLabel | None:
        """One security's forward return over `window`, or `None` when it cannot be labelled.

        `None` for a security with no stored adjustment history: `label_outcome` requires one and a
        name that has none has no correct return, so it is left out of the label map and counted by
        `ICCensus.unmatched_count`. That is the honest place for it -- an unmatched name is visible
        in the census beside the cross section it was dropped from, while a fabricated unit factor
        would be a return computed across a corporate action nobody saw.
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
    - The **registry** partitions are keyed by *lifecycle* year. `load_stock_universe` caps its
      snapshot at the day before the first year the store holds and this read skipped, so naming a
      prefix of the stored years silently shortens the universe -- which it reports through
      `UniverseCompleteness` rather than by refusing.

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
    `the_builder_cannot_produce_a_residual_before_its_years_stored_horizon`.
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
    """
    try:
        return write()
    except (FactorEngineError, NeutralizationEngineError, PanelStorageError) as error:
        raise FactorRunBlockedError(
            f"the {tier} partitions were refused: {error}. A partition is replaced whole, so a "
            f"rebuild that means to replace a stored build has to name it with {flag}, and every "
            "instant of one partition year has to be built in one invocation. Nothing this "
            "invocation computed after this point was written"
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

    **How far that is measured, stated rather than implied.** The *registry* half is driven:
    `tests/integration/test_factor_build.py::
    test_the_registry_is_read_at_each_prediction_instant_and_not_once_for_the_build` puts two
    instants eight days apart under a seven-day freshness bound, so a build that read the registry
    once succeeds where this one must be refused. The *calendar* half is **not separable on any
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
    universe = _read(
        lambda: load_stock_universe(
            store, years=request.years, as_of=as_of, max_staleness=request.max_staleness
        ),
        store=store,
        what="the security registry",
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


def _neutralized(
    store: PanelStore,
    request: FactorBuildRequest,
    *,
    panel: ProcessedFactorPanel,
    built_at: datetime,
) -> NeutralizedFactorPanel:
    """One residual cross section, or the named refusal that says why there cannot be one.

    The whole of `the_builder_cannot_produce_a_residual_before_its_years_stored_horizon` lives
    here, and `V2-P4-026` halved it. `load_industry_market_cap_cross_section` reads the industry
    memberships through `read_if_ready`, the **unfiltered** door, which refuses a partition whose
    newest row post-dates the `as_of` instead of filtering it; and
    `_refuse_a_cross_section_that_is_not_this_panels` requires the returned cross section's
    `as_of` to equal this panel's exactly, so the read cannot simply be made later. Those two
    together mean a residual exists only at a prediction instant at or after the last stored
    *assignment* of every membership year this read touches. `daily_basic` used to state the same
    bound one session tighter and no longer does: it is read one session at a time under an
    availability predicate, so a price partition never blocks a build a membership partition
    would have allowed.

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
    except _PANEL_FAULTS as error:
        message = (
            f"no {neutralization.qualified_key} cross section can be assembled at "
            "{cause}. This is "
            "the_builder_cannot_produce_a_residual_before_its_years_stored_horizon: the industry "
            "membership read takes the unfiltered door, which refuses a partition whose newest "
            "row post-dates the as_of, and the residual must carry the processed panel's own "
            "instant -- so a residual exists only at a prediction instant at or after the last "
            f"stored assignment of every year in {list(request.years)}. (The market-"
            "capitalisation read no longer states a bound of its own: V2-P4-026 gave it an "
            "as-of-sensitive session-level door.) Build --tier processed at this instant, or move "
            "--as-of to at or after the panel's horizon, or fetch the later sessions first. "
            "Nothing was written"
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
