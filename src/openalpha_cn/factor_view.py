"""One factor experiment run, resolved and rendered once, for all three faces (`V2-P3-015`).

`V2-P3-014` sealed the artifact and stored nothing. This module is the public face the roadmap
names -- `factor run --factor <id> --start --end`, `POST /api/v1/factors/run`,
`OpenAlphaSDK.run_factor_experiment` -- and it is one module for `panel_view.py`'s reason: three
faces that resolve one request three ways answer three different questions, and every equivalence
between them is then a coincidence.

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
be able to answer out loud.** The neutralised tier is not on the same schedule as the other two
and cannot be: `panel_neutralization.load_industry_market_cap_cross_section` reads `daily_basic`
through `read_if_ready`, the unfiltered door, which refuses a partition whose newest row
post-dates the `as_of` -- so a residual for any day of year Y can only be *built* at an `as_of` at
or after Y's last stored session, and every residual of that build is stamped there. Reading them
back goes through `read_visible_at`, so an in-year read returns **empty rather than an error**,
which is the shape that makes this dangerous: a face that shrugged would report a two-tier
experiment, or a three-tier one whose neutralised row measured nothing, and the acceptance
criterion `V2-P3-014` exists for is decided on exactly that row.

So this module refuses. The `as_of`s are the **raw** tier's own stored cross sections inside the
range, and every other tier must carry a cross section at each one of them; a tier that does not
is `FactorRunBlockedError`, naming the missing instants and the tier. A mid-year `--start`/`--end`
over a neutralisation built at year end therefore produces a refusal that says which instants have
no residual, rather than an artifact whose loudest cell is `not_measured` for a reason nobody
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

**A freshness policy.** Every panel read here passes `max_staleness=None`. "Is this panel fresh
enough to read" is `panel doctor`'s and `data-check`'s question, and answering it a second time
here would be a second source of truth for it -- the same argument `panel_gate` makes for not
building its own `ReadinessRequirement`.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
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
    TradeabilitySpec,
    TradeabilityStudy,
)
from openalpha_cn.domain.adjustment import AdjustmentHistory
from openalpha_cn.domain.daily_prices import DailyBar
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
from openalpha_cn.domain.horizon import HorizonError, ResearchHorizon, parse_horizon
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
from openalpha_cn.domain.stock_universe import StockUniverse
from openalpha_cn.domain.trading_calendar import TradingCalendar, TradingCalendarError
from openalpha_cn.panel.catalog import DEFAULT_DATE_TIMEZONE, PanelStorageError
from openalpha_cn.panel.store import PanelStore
from openalpha_cn.panel_factors import (
    FACTOR_DEFINITIONS,
    FACTOR_TRANSFORMS,
    FactorEngineError,
    load_factor_observations,
    load_processed_factor_observations,
)
from openalpha_cn.panel_ingest import (
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
    load_neutralized_factor_observations,
)
from openalpha_cn.panel_view import PANEL_STORE_PLACEHOLDER, panel_store

__all__ = [
    "FACTOR_DATE_ZONE",
    "FACTOR_RUN_LIMITATION_CODES",
    "KNOWN_FACTOR_RUN_LIMITATIONS",
    "VIEW_SCHEMA_VERSION",
    "ExperimentDocumentStore",
    "ExperimentWrite",
    "FactorPanelUnreadableError",
    "FactorRequestError",
    "FactorRunBlockedError",
    "FactorRunLimitation",
    "FactorRunRequest",
    "FactorViewError",
    "attribution_rows",
    "experiment_view",
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
            "The neutralised tier cannot be on the same schedule as the other two inside a "
            "covered year: load_industry_market_cap_cross_section reads daily_basic through "
            "read_if_ready, which refuses a partition whose newest row post-dates the as_of, so a "
            "residual for any day of year Y can only be built at an as_of at or after Y's last "
            "stored session and every residual of that build is stamped there. Reading them back "
            "goes through read_visible_at, which filters rather than refuses, so an in-year read "
            "is EMPTY rather than an error. This face refuses such a run by name instead of "
            "reporting a three-tier artifact whose neutralised row measured nothing -- that row "
            "is the one V2-P3-014's acceptance criterion is decided on. V2-P4-026 (an "
            "as-of-sensitive session-level read of daily_basic) is the fix and is a hard "
            "precondition of V2-P4-013."
        ),
    ),
    FactorRunLimitation(
        code="nothing_in_this_repository_builds_a_factor_panel_from_a_command_line",
        detail=(
            "This face READS the three stored tiers; it computes none of them. Nothing in cli.py "
            "or scripts/ imports panel_factors or panel_neutralization, so compute_factor, "
            "apply_factor_transform and apply_factor_neutralization are libraries with no "
            "operator-reachable caller -- panel_neutralization's own docstring says so of itself. "
            "The consequence is exact and is not softened: `openalpha factor run` against a store "
            "built only by `openalpha panel build` finds no factor partition and is refused by "
            "name. Wiring a builder is V2-P3-002's missing caller rather than this issue's, and "
            "it is the one thing standing between this surface and an operator."
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
    one exception type for "this request cannot be put" whatever part of it was wrong. The
    registry's own message names every declared factor, which is the actionable half.
    """
    name = token.strip()
    if not name:
        raise FactorRequestError(
            "--factor names no factor; give a qualified key (`reversal_1d/v1`) or a factor_id "
            "(`fct_...`) this build declares"
        )
    try:
        return registry.get(name) if "/" in name else registry.by_id(name)
    except FactorError as error:
        raise FactorRequestError(str(error)) from error


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
            "neutralisation is the usual cause: its own build reads daily_basic through the "
            "unfiltered door, so a residual for any day of a year can only be computed at an "
            "as_of at or after that year's last stored session, and it is stamped there. Move "
            "--start/--end onto the instants the tier was built at, or build the missing tier for "
            "these days"
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
            periods.append(quantile_study.measure(section, bars=_bars_for(inputs, section, window)))
        except (FactorICError, FactorPortfolioError) as error:
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
