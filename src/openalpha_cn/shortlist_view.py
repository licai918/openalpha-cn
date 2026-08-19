"""The panel plane's bridge to the two-stage funnel, and one shortlist for all three faces.

`V2-P4-032` and `V2-P4-033`, which the independent product acceptance filed as the two Criticals
of this phase: the funnel's required input was produced nowhere, and nothing reached the chain
from any shipped surface.

## What was missing, measured

At `824ebff`, `grep -rn "ComponentCrossSection(" src` returned **nothing**. `openalpha factor
build` wrote raw, processed and neutralised tiers into a panel; `load_factor_observations` and its
two siblings read them back; and `CrossSectionScreen.select` required
`Sequence[ComponentCrossSection]` -- a type only `tests/` ever constructed, at nine sites. The two
halves of the pipeline were each sound and there was no join between them. The same measurement
one layer up: `CrossSectionScreen`, `rank_candidates` and `gate_shortlist` carried 159 passing
tests and not one of them started at a `CliRunner`, a `TestClient` or an `OpenAlphaSDK`, so the
whole chain was reachable only by importing six modules by hand.

## Why this module is on the panel side, and not in `backtest/`

It cannot be in `backtest/`, and that is a decision rather than an accident. The `lint-imports`
contract `backtest-no-numeric-stack-or-panel-plane` forbids `openalpha_cn.panel*`,
`openalpha_cn.factor_view` and `openalpha_cn.providers` to every module under `backtest/`, on full
transitive reachability and with no exemptions. An adapter that reads a stored partition and hands
back a `ComponentCrossSection` reaches a store by definition, so putting it there would mean
relaxing the one contract that makes "the studies touch no store" structural.

It is not in `factor_view.py` either, and that is the more interesting call, because that module
already reads all three tiers. `factor_view` answers "what did this factor's ordering correlate
with, over a **closed range** of prediction days" -- its `_PanelInputs` exists to cache per-session
reads *across a window*, its request contract carries a `--start` and an `--end`, and its output is
a sealed experiment. A shortlist is the other question: one `as_of`, one cross section, no labels,
no forward return, and an artifact nobody stores. Folding it in would have meant one request
contract carrying two disjoint halves and one error taxonomy meaning two things.

So it is a third `*_view.py` beside `panel_view.py` and `factor_view.py`, and it takes those two
modules' shape deliberately rather than inventing one: a request resolver that touches no store, a
typed fault hierarchy whose `reason` each channel looks its own envelope up by, a reader that
turns a panel refusal into `panel_unreadable`, and a renderer the CLI, HTTP and the SDK share so
they cannot come to describe three shortlists.

## The look-ahead, which is the dangerous part of this file

Aligning several factor tiers onto one cross section at one `as_of` is the single place in this
pipeline where a look-ahead is easiest to introduce, so this module opens **no new way to read**.
Every value it returns comes back through `load_factor_observations`,
`load_processed_factor_observations` or `load_neutralized_factor_observations`, which are the three
`read_visible_at` callers `V2-P3-002` and `V2-P3-019` built -- an observation's `available_time`
is the instant its build was run at, so a row stamped after the requested `as_of` is filtered out
one layer down and never reaches this module at all. The bars, the bands, the halts and the
registry come back through `panel_ingest`'s own loaders at the same `as_of`.

On top of that filter this module makes one more decision, and it is the one that needed a rule:
a year partition visible at an `as_of` holds **every** build up to it, so "the cross section at
`as_of`" is not "the rows that came back". `_resolve_instant` takes the newest instant each
declared component has a build at, and requires every component to agree on it -- `factor_view`'s
`the_three_tiers_must_have_been_built_at_the_same_instants` applied across components instead of
across tiers, and for its reason: a composite summing one factor's Friday against another's Monday
is one number over two markets. The resolved instant is reported on every answer
(`cross_section_as_of`) precisely because it may be older than the `as_of` that was asked for.

`tests/integration/test_shortlist_interfaces.py::
test_a_factor_value_stamped_after_the_requested_as_of_never_reaches_the_cross_section` drives both
halves off one store: two builds in one partition, an `as_of` between them, and the same request at
an `as_of` after both. Asserting only the first half would pass on an adapter that returned nothing.

## `clipped_subjects`, which is carried and not measured, and what that costs here

`ComponentCrossSection.clipped_subjects` is which securities the transform assigned its **upper**
bound. `cross_section.py` requires it to be carried in rather than recovered from the values,
because on the neutralised tier the residuals no longer show it -- and it is load-bearing:
`cut_inside_the_clip_block` is the funnel coverage code that refuses a cut taken from inside a
block of names a winsorizer tied together, and `ComponentScore.clipped` marks the terms.

Nothing in the stored observation contracts carries it. `FactorTransformStatistics` records
`winsorized_high_count` and the bounds, but those are partition *columns*
(`TRANSFORM_MANIFEST_DATA_COLUMNS`) that `load_factor_transform_manifests` deliberately does not
reassemble, and reading them would mean opening a partition by a route this module has no business
inventing. So the rule is stated rather than guessed:

- **raw** -- `frozenset()`. The raw tier is stored values in each factor's own units and no
  transform has touched them; `ComponentCrossSection` says so in its own docstring.
- **processed** -- the admitted subjects sharing the **largest** stored value, and empty when the
  declared transform's winsorization is `none`. This is the recovery `ComponentCrossSection`'s
  docstring names ("a caller can recover it as the names sharing the largest stored value"): every
  standardization this build declares is monotone, so the block the winsorizer tied together
  before standardization is still tied after it, at the maximum.
- **neutralized** -- the same set, computed on the **processed** partition of the same
  `(factor, transform)` at the same instant and carried across by subject. That is the one honest
  route: 41 names sharing one processed number carry 41 distinct residuals, so nothing recoverable
  from a neutralised cross section identifies them, and the tier below still does.

`the_clip_block_is_recovered_from_a_tie_and_may_over_report` is the limitation this leaves, and it
over-reports in the safe direction: a tie that a winsorization did not cause is read as a clip
block, which makes `cut_inside_the_clip_block` fire on a screen it need not have, and never the
reverse.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, DecimalException
from typing import ClassVar, Final, Literal, TypeVar
from zoneinfo import ZoneInfo

from pydantic import ValidationError

from openalpha_cn.backtest.candidate_ranking import (
    CandidateRanking,
    CandidateRankingError,
    build_ranking_manifest,
    rank_candidates,
)
from openalpha_cn.backtest.cross_section import (
    ComponentCrossSection,
    CrossSectionFunnel,
    CrossSectionScreen,
    ScoreComponent,
    ShortlistSpec,
    TwoStageFunnelError,
)
from openalpha_cn.backtest.execution import (
    AShareExecutionPolicy,
    MarketBar,
    published_limit_fields,
    suspended_at_the_close,
)
from openalpha_cn.backtest.factor_ic import (
    FACTOR_TIER_ORDER,
    TIER_ADMITTED_CODES,
    FactorTier,
)
from openalpha_cn.backtest.shortlist_gate import (
    ShortlistClearance,
    ShortlistGateError,
    ShortlistGateSpec,
    gate_shortlist,
)
from openalpha_cn.domain.factor import FactorDefinition, FactorError, FactorRegistry
from openalpha_cn.domain.factor_neutralization import (
    FactorNeutralizationRegistry,
    FactorNeutralizationSpec,
)
from openalpha_cn.domain.factor_transform import (
    FactorTransformRegistry,
    FactorTransformSpec,
)
from openalpha_cn.domain.horizon import COUNTABLE_HORIZON_PATTERN, is_countable_horizon
from openalpha_cn.domain.labels import halt_corpus_for_years
from openalpha_cn.domain.name_history import RiskWarning
from openalpha_cn.domain.signal import SignalFrame
from openalpha_cn.domain.trading_calendar import TradingCalendar, TradingCalendarError
from openalpha_cn.panel.catalog import PanelStorageError
from openalpha_cn.panel.store import PanelStore
from openalpha_cn.panel_factors import (
    FACTOR_DEFINITIONS,
    FACTOR_TRANSFORMS,
    FactorEngineError,
    load_factor_observations,
    load_processed_factor_observations,
)
from openalpha_cn.panel_ingest import (
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
    "KNOWN_SHORTLIST_VIEW_LIMITATIONS",
    "SHORTLIST_VIEW_LIMITATION_CODES",
    "SHORTLIST_VIEW_SCHEMA_VERSION",
    "ShortlistCrossSection",
    "ShortlistEvidence",
    "ShortlistPanelUnreadableError",
    "ShortlistRequestError",
    "ShortlistRunBlockedError",
    "ShortlistRunRequest",
    "ShortlistRunResult",
    "ShortlistViewError",
    "ShortlistViewLimitation",
    "clipped_from_the_tie_at_the_top",
    "load_shortlist_cross_section",
    "panel_store",
    "run_shortlist",
    "shortlist_components",
    "shortlist_evidence",
    "shortlist_request",
    "shortlist_rows",
    "shortlist_view",
]

SHORTLIST_VIEW_SCHEMA_VERSION: Final[str] = "shortlist-view/v1"
"""The version of the envelope `shortlist_view` renders, carried in the body.

A face's rendering is a contract of its own -- `factor_view.VIEW_SCHEMA_VERSION`'s reason: the
sealed records underneath already carry their own `schema_version`, and this says which *shape*
the three faces agreed to hand out around them.
"""

SHORTLIST_DATE_ZONE: Final[ZoneInfo] = ZoneInfo("Asia/Shanghai")
"""The zone a cross-section instant is resolved into a **session** in.

`factor_view.FACTOR_DATE_ZONE`'s constant restated for its reason rather than imported for
symmetry: an A-share session is a calendar day on the exchange's own clock, and a build stamped
2026-01-16T09:00Z is 17:00 in Shanghai -- after that session's close, and therefore about it. In
UTC the same instant is still 2026-01-16 and the two agree; two hours later they do not, and the
zone is what decides which session's bars stage two prices against.
"""


class ShortlistViewError(RuntimeError):
    """Base for every fault a shortlist face can report before a clearance exists.

    `FactorViewError`'s two fields and its reasons: a `reason` each channel looks its own envelope
    up by, so a fault added here with no row in a channel's table raises `KeyError` at that
    channel's boundary rather than being silently mis-enveloped; and a `disclosable` message that
    may cross a process boundary, because the store's filesystem location is configuration of this
    process and a response body echoing it would answer a question about the deployment to whoever
    could reach the port.

    **A refused gate is deliberately not one of these.** `gate_shortlist` returning a blocked
    `ShortlistClearance` is this pipeline answering, not failing: the blocks, the measurement they
    were read against and the funnel's own coverage code are all on the verdict, and the faces
    envelope it by `ShortlistRunResult.is_blocked`. Raising here would have made "the gate refused
    this list" indistinguishable, at a face, from "the panel could not be read".
    """

    reason: ClassVar[str] = "shortlist_view_error"

    def __init__(self, message: str, *, disclosable: str | None = None) -> None:
        super().__init__(message)
        self.disclosable: str = message if disclosable is None else disclosable


class ShortlistRequestError(ShortlistViewError):
    """The question cannot be put at all, whatever is in the store.

    A factor no registry declares, a weight that is not positive, a tier with no transform beside
    it, a naive `as_of`, a horizon `SignalFrame` cannot carry. Distinct from
    `ShortlistRunBlockedError` because the remedy is to edit the request rather than to build
    anything.
    """

    reason: ClassVar[str] = "bad_request"


class ShortlistPanelUnreadableError(ShortlistViewError):
    """A panel partition this screen needs cannot be read at the stated `as_of`.

    The exchange calendar, the registry, the price panel, the halt corpus or one of the factor
    partitions came back blocked. Not a verdict, because there is no list to put one on: these are
    the inputs a list would have been cut from.
    """

    reason: ClassVar[str] = "panel_unreadable"


class ShortlistRunBlockedError(ShortlistViewError):
    """The stored panel cannot answer this question as asked, and the refusal is the answer.

    No component has a stored cross section at or before the `as_of`; the declared components
    disagree about which instant is the newest one they share; the registry lists nobody on the
    session that cross section is about; the stored rows are not a cross section a screen can
    read. Every one is a conflict with the current state of the **panel** rather than a malformed
    question, and every one has a build as its remedy.

    **A fault in the evidence a caller supplied is deliberately not one of these.** A signal
    stamped at another `as_of`, one over another horizon, one with no `run_manifest_id` beside it
    -- `rank_candidates` refuses all three, and this face envelopes them as `bad_request`, because
    the remedy is to fix the request rather than to rebuild anything. They used to arrive here,
    and a caller who mistyped a signal's `as_of` was told `409` and to look at their panel.

    **This is the row that must not wear a 2xx.** A face that answered `200` with an empty list
    here would be the empty success `V2-P1-013` exists to make unavailable, arriving one plane up
    -- and it is the exact defect the product acceptance filed: a blocked list and a list with no
    members reaching the caller as the same `{"items": []}`.
    """

    reason: ClassVar[str] = "blocked"


@dataclass(frozen=True, slots=True, kw_only=True)
class ShortlistViewLimitation:
    """One named boundary on what a shortlist run can be trusted to mean."""

    code: str
    detail: str


KNOWN_SHORTLIST_VIEW_LIMITATIONS: Final[tuple[ShortlistViewLimitation, ...]] = (
    ShortlistViewLimitation(
        code="the_clip_block_is_recovered_from_a_tie_and_may_over_report",
        detail=(
            "KNOWN_SHORTLIST_VIEW_CLIP_BLOCK: `ComponentCrossSection.clipped_subjects` is which "
            "securities a winsorizer assigned its upper bound, and no stored observation carries "
            "that flag -- `FactorTransformStatistics.winsorized_high_count` is a partition column "
            "`load_factor_transform_manifests` deliberately does not reassemble. This module "
            "recovers the block as the admitted subjects sharing the largest stored **processed** "
            "value, which is the recovery `ComponentCrossSection`'s own docstring names. It is "
            "exact whenever a tie at the maximum was produced by the clip and over-reports when "
            "two securities simply had equal values: the consequence is that "
            "`cut_inside_the_clip_block` can refuse a screen it need not have, never that it "
            "fails to refuse one it should. On the raw tier the set is empty because nothing has "
            "been transformed, and for a `winsorization='none'` transform it is empty because "
            "nothing was clipped."
        ),
    ),
    ShortlistViewLimitation(
        code="the_cross_section_may_be_older_than_the_as_of_that_was_asked_for",
        detail=(
            "KNOWN_SHORTLIST_VIEW_STALENESS: the components are the newest stored cross section "
            "visible at the requested `as_of`, which is the only point-in-time correct choice and "
            "is **not** the same as `a cross section computed at the as_of`. A store whose newest "
            "build is three months old answers with three-month-old factor values, and the answer "
            "is not wrong -- it is what was knowable. `cross_section_as_of` is on every rendered "
            "body and on `ShortlistRunResult` for exactly this reason, and it is a different "
            "quantity from `ShortlistGateSpec.maximum_ranking_age_days`, which bounds how old the "
            "*ranking* is rather than how old the factor values in it are. Nothing here refuses a "
            "stale cross section; a caller that needs one to be fresh reads the instant and "
            "decides."
        ),
    ),
    ShortlistViewLimitation(
        code="the_evidence_plane_is_supplied_rather_than_run_by_this_module",
        detail=(
            "KNOWN_SHORTLIST_VIEW_EVIDENCE: `rank_candidates` joins each shortlisted name to the "
            "`SignalFrame` its research run produced, and this repository stores no signal -- "
            "`SQLiteRunRepository` holds `RunManifest` and `DecisionLedger`, and `ResearchReport` "
            "holds a `signal_id` rather than the frame. So a caller supplies the evidence plane's "
            "answers on the request (`evidence`), and a shortlisted name with no entry is "
            "`unresearched`, which is what `researched_ratio` measures. A run with no evidence at "
            "all is therefore the ordinary first answer -- the shortlist is 'which names are worth "
            "spending an evidence run on today' -- and the gate refuses to publish it as "
            "conclusions under any `minimum_researched_ratio` above zero. Nothing here calls "
            "`run_cycle`: a face that ran the evidence plane for every shortlisted name would "
            "make `researched_ratio` unable to be anything but 1.0, and the bar it exists to "
            "measure would be unreachable from a surface."
        ),
    ),
    ShortlistViewLimitation(
        code="a_neutralized_tier_screen_needs_exposures_this_face_does_not_load",
        detail=(
            "KNOWN_SHORTLIST_VIEW_NEUTRALIZED: `rank_candidates` refuses `exposures=None` on the "
            "neutralized tier, because an industry mean and a size slope have already been "
            "subtracted out of every score and a ranking that cannot say what was removed has no "
            "readable explanation. The cross section that says it is "
            "`IndustryMarketCapCrossSection`, whose loader reads `index_member_all` through "
            "`read_if_ready` and therefore answers only at an `as_of` at or after the newest "
            "stored assignment -- `V2-P4-027`'s issue, and the same bound "
            "`tests/integration/test_factor_build.py` measures. So the **adapter** here serves all "
            "three tiers and `run_shortlist` refuses `tier='neutralized'` by name rather than "
            "loading an exposure cross section at an instant that would not match the screen's. "
            "Screen on `raw` or `processed`, where nothing was projected out."
        ),
    ),
)

SHORTLIST_VIEW_LIMITATION_CODES: Final[frozenset[str]] = frozenset(
    limitation.code for limitation in KNOWN_SHORTLIST_VIEW_LIMITATIONS
)


# --- the request, which touches no store --------------------------------------------------------


@dataclass(frozen=True, slots=True, kw_only=True)
class ShortlistEvidence:
    """One shortlisted security's answer from the evidence plane.

    The pair `rank_candidates` requires together and refuses apart: a `SignalFrame` with no
    `run_manifest_id` is a conclusion with no reproducible declaration behind it, which is what
    roadmap section 9 measured `RunManifest` to have been missing. Carrying them as one record
    rather than as two parallel mappings is what makes "supplied a signal and forgot the manifest"
    unconstructible at this boundary instead of refused two calls later.
    """

    signal: SignalFrame
    run_manifest_id: str


@dataclass(frozen=True, slots=True, kw_only=True)
class ShortlistRunRequest:
    """One shortlist run's whole declaration, resolved. Built by `shortlist_request`.

    Nothing here has a default, `ShortlistSpec`'s rule and this repository's since `V2-P3-005`:
    every field moves which securities come out or whether the list may be published, and a
    decision that moves the answer is one the caller records making.
    """

    spec: ShortlistSpec
    transform: FactorTransformSpec | None
    neutralization: FactorNeutralizationSpec | None
    gate: ShortlistGateSpec
    as_of: datetime
    years: tuple[int, ...]
    exchange: str
    horizon: str
    code_commit: str
    config_digest: str
    evidence: Mapping[str, ShortlistEvidence]

    @property
    def tier(self) -> FactorTier:
        return self.spec.tier

    @property
    def definitions(self) -> tuple[FactorDefinition, ...]:
        return tuple(component.definition for component in self.spec.components)


def shortlist_components(
    declared: Sequence[Mapping[str, object]],
) -> tuple[tuple[str, float], ...]:
    """`[{"factor": ..., "weight": ...}, ...]` as the pairs `shortlist_request` takes.

    One converter rather than two, because the wire faces both receive the composite as objects --
    HTTP as JSON, the SDK as mappings a caller wrote -- while the command line receives it as
    `--component <key>=<weight>` and parses its own. A second copy of this on either face would be
    a second place a weight could be read out of the wrong key.

    Refuses an entry missing either half rather than defaulting one: `ScoreComponent.weight` has no
    default for `QuantilePortfolioSpec`'s reason -- it moves the answers, and a default is a
    decision nobody recorded making.
    """
    pairs: list[tuple[str, float]] = []
    for index, entry in enumerate(declared):
        unknown = sorted(set(entry) - {"factor", "weight"})
        if unknown:
            raise ShortlistRequestError(
                f"component {index} carries {unknown}, which this face does not declare; a "
                "component is a `factor` and a `weight`"
            )
        if "factor" not in entry or "weight" not in entry:
            raise ShortlistRequestError(
                f"component {index} is {dict(entry)!r}; each component names a `factor` and a "
                "`weight`, and neither has a default because both move which securities come out"
            )
        try:
            pairs.append((str(entry["factor"]), float(entry["weight"])))  # type: ignore[arg-type]
        except (TypeError, ValueError) as error:
            raise ShortlistRequestError(
                f"component {index}'s weight is {entry['weight']!r}, which is not a number: {error}"
            ) from error
    return tuple(pairs)


def shortlist_evidence(
    declared: Mapping[str, Mapping[str, object]],
) -> dict[str, ShortlistEvidence]:
    """`{subject: {"signal": ..., "run_manifest_id": ...}}` as the records `rank_candidates` joins.

    **`signal_id` is stripped before validation and verified after it**, which is
    `api/app.py::_parse_research_result`'s rule and its measured reason: `SignalFrame` is
    `extra="forbid"` with a computed `signal_id`, so it **rejects its own serialized form** and a
    caller who fetched a signal from this service and handed it back would be told `extra_forbidden`
    on a field this service put there. Verified rather than merely dropped, for that function's
    other reason: a caller that could hand back an unverified content address could hand back one
    that does not describe the frame beside it, which is the whole thing the address is for.

    One parser rather than two, because the command line's `--evidence <file>` and the HTTP body's
    `evidence` object carry the same bytes -- so a caller can produce one document and drive
    either, and neither face can come to accept a shape the other refuses.
    """
    joined: dict[str, ShortlistEvidence] = {}
    for subject, item in declared.items():
        unknown = sorted(set(item) - {"signal", "run_manifest_id"})
        if unknown:
            raise ShortlistRequestError(
                f"the evidence for {subject!r} carries {unknown}, which this face does not "
                'declare; an entry is `{"signal": <SignalFrame>, "run_manifest_id": "..."}`'
            )
        if "signal" not in item or "run_manifest_id" not in item:
            raise ShortlistRequestError(
                f"the evidence for {subject!r} is {dict(item)!r}; each entry names the "
                "`signal` its research run produced and that run's `run_manifest_id`, and neither "
                "is optional -- a conclusion with no reproducible declaration behind it is what "
                "roadmap section 9 measured RunManifest to have been missing"
            )
        raw = item["signal"]
        if not isinstance(raw, Mapping):
            raise ShortlistRequestError(
                f"the evidence for {subject!r} carries a signal of type "
                f"{type(raw).__name__}; a SignalFrame arrives as an object"
            )
        claimed = raw.get("signal_id")
        frame = {key: value for key, value in raw.items() if key != "signal_id"}
        try:
            signal = SignalFrame.model_validate(frame)
        except ValidationError as error:
            raise ShortlistRequestError(
                f"the evidence for {subject!r} carries a signal this build cannot read: {error}"
            ) from error
        if claimed is not None and claimed != signal.signal_id:
            raise ShortlistRequestError(
                f"the evidence for {subject!r} claims signal_id {claimed!r} and its content "
                f"hashes to {signal.signal_id!r}; a content address that does not describe the "
                "frame beside it is the one thing an address exists to make impossible"
            )
        joined[str(subject)] = ShortlistEvidence(
            signal=signal, run_manifest_id=str(item["run_manifest_id"])
        )
    return joined


def shortlist_request(
    *,
    components: Sequence[tuple[str, float]],
    tier: str,
    shortlist_size: int,
    position_capital: Decimal,
    as_of: datetime,
    years: Sequence[int],
    exchange: str,
    horizon: str,
    minimum_tradable_ratio: float,
    minimum_researched_ratio: float,
    maximum_ranking_age_days: int,
    code_commit: str,
    config_digest: str,
    transform: str | None = None,
    neutralization: str | None = None,
    evidence: Mapping[str, ShortlistEvidence] | None = None,
    factors: FactorRegistry = FACTOR_DEFINITIONS,
    transforms: FactorTransformRegistry = FACTOR_TRANSFORMS,
    neutralizations: FactorNeutralizationRegistry = FACTOR_NEUTRALIZATIONS,
) -> ShortlistRunRequest:
    """Resolve one face's parameters into the stated request all three of them ask.

    `factor_view.factor_request`'s arrangement, including the three registries as parameters
    carrying the build's own as defaults: no face passes them, so `openalpha shortlist run`, `POST
    /api/v1/shortlists/run` and `OpenAlphaSDK.run_shortlist` resolve against one set of
    declarations and cannot come to screen three different composites.

    Every fault raised here is `ShortlistRequestError`. **Nothing in this function touches a
    store**, so nothing it can say is a statement about the panel -- which is what keeps
    `bad_request` and `panel_unreadable` two separate rows of both channel tables rather than a
    distinction a reader has to make from the message text.
    """
    if tier not in FACTOR_TIER_ORDER:
        raise ShortlistRequestError(
            f"{tier!r} is not a declared factor tier; this build declares {list(FACTOR_TIER_ORDER)}"
        )
    resolved_tier: FactorTier = tier
    if not components:
        raise ShortlistRequestError(
            "a shortlist names no component; declare at least one `factor=weight` pair. A "
            "composite with no factor in it orders nothing, and `openalpha factor list` prints "
            "every factor this build declares"
        )
    declared: list[ScoreComponent] = []
    for token, weight in components:
        definition = _resolve_factor(token, registry=factors)
        try:
            declared.append(ScoreComponent(definition=definition, weight=weight))
        except ValueError as error:
            raise ShortlistRequestError(
                f"{definition.qualified_key} declares weight {weight!r}, which this contract "
                f"refuses: {error}"
            ) from error

    transform_spec = _resolve_transform(
        transform, tier=resolved_tier, registry=transforms, kind="transform"
    )
    neutralization_spec = (
        None
        if resolved_tier != "neutralized"
        else _resolve_neutralization(neutralization, registry=neutralizations)
    )

    if as_of.tzinfo is None or as_of.utcoffset() is None:
        raise ShortlistRequestError(
            f"--as-of must be a timezone-aware instant; got {as_of.isoformat()!r}. A "
            "point-in-time question answered in a guessed timezone is wrong by up to a session"
        )
    if not years:
        raise ShortlistRequestError(
            "a shortlist names no partition year; a factor partition is stored per year and a "
            "read that names none opens nothing"
        )
    if type(exchange) is not str or not exchange or exchange != exchange.strip():
        raise ShortlistRequestError(
            f"exchange must be a non-empty name with no surrounding whitespace; got {exchange!r}"
        )
    if not is_countable_horizon(horizon.strip()):
        raise ShortlistRequestError(
            f"--horizon {horizon!r} is not a horizon a SignalFrame can carry; `V2-P4-001` "
            f"narrowed SignalFrame.horizon to {COUNTABLE_HORIZON_PATTERN} -- a count of trading "
            "sessions -- because a calendar span holds a variable number of them and a future "
            "one's count is not knowable at all. Refused here rather than by "
            "`build_ranking_manifest`, which raises the same objection after a store has already "
            "been read and would therefore report a mistyped flag as a fact about the panel"
        )
    if len(code_commit.strip()) < 7:
        raise ShortlistRequestError(
            f"--code-commit must be at least 7 characters; got {code_commit!r}. Different code "
            "may cut a different list from the same rows, so an identity that ignored it would "
            "claim a reproducibility it cannot deliver"
        )
    try:
        spec = ShortlistSpec(
            components=tuple(declared),
            tier=resolved_tier,
            shortlist_size=shortlist_size,
            position_capital=position_capital,
        )
        gate = ShortlistGateSpec(
            minimum_tradable_ratio=minimum_tradable_ratio,
            minimum_researched_ratio=minimum_researched_ratio,
            maximum_ranking_age_days=maximum_ranking_age_days,
        )
    except (ValueError, DecimalException) as error:
        raise ShortlistRequestError(str(error)) from error

    supplied = dict(evidence or {})
    for subject, item in sorted(supplied.items()):
        if item.signal.subject != subject:
            raise ShortlistRequestError(
                f"the evidence filed under {subject!r} carries a signal about "
                f"{item.signal.subject!r}; a conclusion keyed by one security and about another "
                "is a join nothing downstream can undo"
            )
    return ShortlistRunRequest(
        spec=spec,
        transform=transform_spec,
        neutralization=neutralization_spec,
        gate=gate,
        as_of=as_of,
        years=tuple(sorted(set(int(year) for year in years))),
        exchange=exchange,
        horizon=horizon.strip(),
        code_commit=code_commit.strip(),
        config_digest=config_digest.strip(),
        evidence=supplied,
    )


def _resolve_factor(token: str, *, registry: FactorRegistry) -> FactorDefinition:
    """The definition `--component <token>=<weight>` names: a qualified key, or a `factor_id`.

    `factor_view.resolve_factor`'s rule, restated here rather than imported so that this module's
    refusals all name `--component` -- the flag the caller actually typed -- rather than
    `--factor`, which this face does not have. Told apart by shape: `FactorDefinition.key` is
    constrained to a plain panel identifier precisely so `qualified_key` can split on `/`.
    """
    name = token.strip()
    if not name:
        raise ShortlistRequestError(
            "--component names no factor; give `<qualified key>=<weight>` (`reversal_1d/v1=1.0`) "
            "or `<factor_id>=<weight>`. `openalpha factor list` prints every one of them"
        )
    try:
        return registry.get(name) if "/" in name else registry.by_id(name)
    except FactorError as error:
        raise ShortlistRequestError(
            f"{name!r} is not a factor this build declares. A --component names a qualified key "
            f"-- this build knows {list(registry.qualified_keys)} -- or the fct_ content address "
            "a stored partition carries. `openalpha factor list` prints both, beside what each "
            f"factor reads: {error}"
        ) from error


def _resolve_transform(
    token: str | None, *, tier: FactorTier, registry: FactorTransformRegistry, kind: str
) -> FactorTransformSpec | None:
    """The transform the derived tiers are addressed by, refused when the tier needs one.

    The raw tier is stored per factor and takes none; the processed and neutralised partitions
    hold **every** transform of a factor and are narrowed by `transform_id` in Python, so a read
    of either without one is not a narrower question -- it is an unanswerable one.
    """
    if tier == "raw":
        if token is not None and token.strip():
            raise ShortlistRequestError(
                f"--{kind} {token!r} was given for a raw-tier screen, which reads the factor's "
                "own stored values and applies nothing. Drop it, or screen on the processed tier"
            )
        return None
    if token is None or not token.strip():
        raise ShortlistRequestError(
            f"a {tier}-tier screen needs a --{kind}: that partition holds every transform of the "
            f"factor and is narrowed by the one you name. This build declares "
            f"{list(registry.qualified_keys)}"
        )
    try:
        return registry.get(token.strip())
    except ValueError as error:
        raise ShortlistRequestError(str(error)) from error


def _resolve_neutralization(
    token: str | None, *, registry: FactorNeutralizationRegistry
) -> FactorNeutralizationSpec:
    if token is None or not token.strip():
        raise ShortlistRequestError(
            "a neutralized-tier screen needs a --neutralization: that partition holds every "
            f"neutralisation of the factor and is narrowed by the one you name. This build "
            f"declares {list(registry.qualified_keys)}"
        )
    try:
        return registry.get(token.strip())
    except ValueError as error:
        raise ShortlistRequestError(str(error)) from error


# --- V2-P4-032: the adapter from the panel plane to `ComponentCrossSection` ----------------------


@dataclass(frozen=True, slots=True, kw_only=True)
class ShortlistCrossSection:
    """Everything `CrossSectionScreen.select` needs, read out of one store at one `as_of`.

    A record rather than three return values, because the three are only meaningful together: the
    components are a cross section *at* `as_of`, the universe is the registry's listed set on the
    session that instant belongs to, and the bars are that same session's. A caller handed them
    separately could pair a Friday cross section with a Monday universe and nothing would say so.

    `as_of` is the **resolved** instant -- the newest one every declared component has a stored
    build at, which is at or before the instant that was requested -- and not the request's own.
    See `the_cross_section_may_be_older_than_the_as_of_that_was_asked_for`.
    """

    as_of: datetime
    session: date
    universe: tuple[str, ...]
    components: tuple[ComponentCrossSection, ...]
    bars: Mapping[str, MarketBar]


def load_shortlist_cross_section(
    store: PanelStore, request: ShortlistRunRequest
) -> ShortlistCrossSection:
    """`V2-P4-032`: turn one store's stored factor tiers into the funnel's required input.

    The join that did not exist. `openalpha factor build` writes the tiers; the three
    `load_*_factor_observations` read them back; this projects them into the `(subject, value,
    coverage)` triples `ComponentCrossSection` carries and pairs them with the session's bars.

    **Every read is an existing loader, and there are exactly two `as_of`s in this function.**
    Nothing here opens a partition. The *factor tiers* are read at the caller's `as_of`, because
    that is the question -- "what was knowable when I asked" -- and `read_visible_at` filters an
    observation out by its `available_time`. Everything the screen then **prices** with is read at
    the resolved cross section's own instant, which is at or before it:

        factor tiers ....... request.as_of      (what was knowable when you asked)
        calendar, registry,
        bars, bands, halts,
        name histories ..... cross section's instant

    The asymmetry is the point rather than an oversight. A shortlist is priced with exactly what
    was knowable when its factor values were computed, so a fortnight-old cross section is offered
    to the market of its own session and not to a later one it never saw -- and the read is
    strictly more conservative than the request, never less.
    `tests/integration/test_shortlist_interfaces.py::
    test_a_fortnight_old_cross_section_is_still_priced_on_its_own_session` drives the case that
    separates the two: on a panel whose bars stop at the cross
    section's session, resolving the session from the *request* asks for a session the store does
    not hold and cannot answer at all.

    The session itself is the cross section's own day in `SHORTLIST_DATE_ZONE`: a build stamped
    2026-01-16T09:00Z is 17:00 in Shanghai, after that session's close and therefore about it.
    """
    by_component = {
        component.factor_id: _rows_for(store, component.definition, request)
        for component in request.spec.components
    }
    instant = _resolve_instant(by_component, request)
    calendar = _read(
        lambda: load_trading_calendar(
            store, exchange=request.exchange, years=request.years, as_of=instant
        ),
        store=store,
        what=f"the {request.exchange} trading calendar",
    )
    session = _pricing_session(instant, calendar=calendar)

    registry = _read(
        lambda: load_stock_universe(store, years=request.years, as_of=instant, max_staleness=None),
        store=store,
        what="the security registry",
    )
    universe = tuple(sorted(registry.listed_on(session)))
    if not universe:
        raise ShortlistRunBlockedError(
            f"the registry lists no security on {session.isoformat()}, which is the session the "
            f"stored cross section at {instant.isoformat()} is about. A screen over an empty "
            "market cuts nothing; build the stock_basic partition over that year first"
        )
    components = tuple(
        _component_cross_section(
            store,
            request,
            definition=component.definition,
            rows=by_component[component.factor_id],
            instant=instant,
        )
        for component in request.spec.components
    )
    return ShortlistCrossSection(
        as_of=instant,
        session=session,
        universe=universe,
        components=components,
        bars=_bars_on(
            store, request, session=session, universe=universe, calendar=calendar, as_of=instant
        ),
    )


def _rows_for(
    store: PanelStore, definition: FactorDefinition, request: ShortlistRunRequest
) -> tuple[tuple[str, float | None, str, datetime], ...]:
    """One component's stored rows on the declared tier, as `(subject, value, coverage, as_of)`.

    Projected here rather than downstream so the three tier contracts are walked once each, in the
    one function that knows which loader answers for which tier -- `factor_ic`'s three public
    wrappers' arrangement, which exists so that a caller cannot hand the processed loader's rows
    to the raw tier's admitted-code table.
    """
    tier = request.tier
    if tier == "raw":
        raw = _read(
            lambda: load_factor_observations(
                store, definition, years=request.years, as_of=request.as_of
            ),
            store=store,
            what=f"the raw {definition.qualified_key} observations",
        )
        return tuple((row.subject, row.value, row.coverage, row.as_of) for row in raw)
    transform = _declared_transform(request)
    if tier == "processed":
        processed = _read(
            lambda: load_processed_factor_observations(
                store, definition, transform, years=request.years, as_of=request.as_of
            ),
            store=store,
            what=f"the {transform.qualified_key} rows of {definition.qualified_key}",
        )
        return tuple((row.subject, row.value, row.coverage, row.as_of) for row in processed)
    neutralization = _declared_neutralization(request)
    residuals = _read(
        lambda: load_neutralized_factor_observations(
            store, definition, neutralization, years=request.years, as_of=request.as_of
        ),
        store=store,
        what=f"the {neutralization.qualified_key} residuals of {definition.qualified_key}",
    )
    return tuple((row.subject, row.value, row.coverage, row.as_of) for row in residuals)


def _declared_transform(request: ShortlistRunRequest) -> FactorTransformSpec:
    """The transform a derived-tier read is narrowed by, refused rather than assumed.

    `shortlist_request` already refuses a processed or neutralised screen with no transform, so
    this cannot fire through a resolved request -- and it is written anyway, for
    `shortlist_gate._measure`'s stated reason: a `ShortlistRunRequest` is a frozen dataclass and is
    still constructible directly, so this is the precondition stated at the read rather than
    inferred from a resolver two calls away. A bare `assert` would be the same statement with
    `-O` able to delete it.
    """
    if request.transform is None:
        raise ShortlistRequestError(
            f"a {request.tier}-tier read needs a transform to narrow the partition by, and this "
            "request carries none; `shortlist_request` is what resolves one"
        )
    return request.transform


def _declared_neutralization(request: ShortlistRunRequest) -> FactorNeutralizationSpec:
    """The neutralisation a residual read is narrowed by; `_declared_transform`'s twin."""
    if request.neutralization is None:
        raise ShortlistRequestError(
            "a neutralized-tier read needs a neutralisation to narrow the partition by, and this "
            "request carries none; `shortlist_request` is what resolves one"
        )
    return request.neutralization


def _resolve_instant(
    by_component: Mapping[str, Sequence[tuple[str, float | None, str, datetime]]],
    request: ShortlistRunRequest,
) -> datetime:
    """The one instant this cross section is at: the newest every declared component shares.

    `factor_view`'s `the_three_tiers_must_have_been_built_at_the_same_instants`, applied across
    components rather than across tiers and for the same reason -- a composite that summed one
    factor's Friday value against another's Monday value would be one number over two markets, and
    the ordering it produced would be unattributable to either.

    Refused rather than reconciled. Taking the newest instant every component *happens* to share
    would answer a screen whose first factor is a week stale with no sign that it is, and taking
    each component's own newest would be the mixed cross section above. Both refusals name the
    instants, because a caller told only `blocked` cannot act on it.
    """
    instants: dict[str, datetime | None] = {
        factor_id: max((as_of for _s, _v, _c, as_of in rows), default=None)
        for factor_id, rows in by_component.items()
    }
    keys = {
        component.factor_id: component.definition.qualified_key
        for component in request.spec.components
    }
    empty = sorted(keys[factor_id] for factor_id, at in instants.items() if at is None)
    if empty:
        raise ShortlistRunBlockedError(
            f"no {request.tier}-tier cross section of {empty} is stored in year(s) "
            f"{list(request.years)} and visible at {request.as_of.isoformat()}. A shortlist over "
            "no factor value at all is the empty success this plane exists to make unavailable -- "
            "build the tier first (`openalpha factor build --factor <key> --tier "
            f"{request.tier} --year <year>`), or ask at an as_of a stored build had reached"
        )
    newest = {factor_id: at for factor_id, at in instants.items() if at is not None}
    distinct = set(newest.values())
    if len(distinct) > 1:
        described = ", ".join(
            f"{keys[factor_id]} at {at.isoformat()}" for factor_id, at in sorted(newest.items())
        )
        raise ShortlistRunBlockedError(
            f"the declared components' newest stored cross sections visible at "
            f"{request.as_of.isoformat()} are {described}; they are not one cross section, and a "
            "composite summing one factor's value from one session against another's from another "
            "is one number over two markets. Build every declared component at the same instant, "
            "or screen on the subset that already shares one"
        )
    return distinct.pop()


def _pricing_session(instant: datetime, *, calendar: TradingCalendar) -> date:
    """The session stage two prices against: the cross section's own day, or the one before it.

    `previous_trading_day` when the instant lands on a non-session -- a build stamped on a Saturday
    is about the Friday close -- and the instant's own day when it is one. Both go through the
    stored calendar rather than a weekday rule, because a Chinese public holiday is not a weekend
    and a screen that priced through one would offer buys on a day the exchange was shut.
    """
    day = instant.astimezone(SHORTLIST_DATE_ZONE).date()
    try:
        return day if calendar.is_trading_day(day) else calendar.previous_trading_day(day)
    except TradingCalendarError as error:
        raise ShortlistRunBlockedError(
            f"the stored cross section is at {instant.isoformat()}, and the exchange calendar "
            f"cannot say which session {day.isoformat()} belongs to: {error}. Extend the calendar "
            "over that year, or ask at an as_of inside the one it covers"
        ) from error


def _component_cross_section(
    store: PanelStore,
    request: ShortlistRunRequest,
    *,
    definition: FactorDefinition,
    rows: Sequence[tuple[str, float | None, str, datetime]],
    instant: datetime,
) -> ComponentCrossSection:
    """One component's stored cross section at `instant`, as the funnel reads it.

    Narrowed to `instant` rather than to whatever came back, which is the second half of the
    look-ahead argument: `read_visible_at` filters out builds stamped *after* the `as_of`, and this
    filters out the older builds sitting in the same year partition -- so what reaches the screen
    is one cross section rather than a year of them stacked under one subject.

    **Not** narrowed to the registry, and the omission is deliberate rather than an oversight.
    `CrossSectionScreen._read_components` already drops a stored row for a security `universe` does
    not name -- "only securities in `subjects` are read" -- so a second filter here would be one
    rule in two places, and the two can disagree. It was written and then removed: a mutation that
    deleted it turned nothing red, because on any panel where the registry and the factor
    partition agree the two filters are the same filter. What the caller sees instead is the
    stored cross section as it was found, with the funnel's own census saying how much of it the
    screen used.
    """
    at_instant = tuple(
        (subject, value, coverage)
        for subject, value, coverage, row_as_of in rows
        if row_as_of == instant
    )
    try:
        return ComponentCrossSection(
            factor_id=definition.factor_id,
            values=at_instant,
            clipped_subjects=_clipped_subjects(
                store, request, definition=definition, rows=at_instant, instant=instant
            ),
        )
    except TwoStageFunnelError as error:
        raise ShortlistRunBlockedError(
            f"the stored {request.tier} cross section of {definition.qualified_key} at "
            f"{instant.isoformat()} is not one this screen can read: {error}"
        ) from error


def _clipped_subjects(
    store: PanelStore,
    request: ShortlistRunRequest,
    *,
    definition: FactorDefinition,
    rows: Sequence[tuple[str, float | None, str]],
    instant: datetime,
) -> frozenset[str]:
    """Which securities the declared winsorization assigned its **upper** bound.

    See this module's docstring and
    `the_clip_block_is_recovered_from_a_tie_and_may_over_report` for the whole argument. In short:
    nothing stored carries the flag, the recovery is the tie at the maximum admitted value, and on
    the neutralised tier that tie has to be read off the **processed** partition because a residual
    no longer shows it.
    """
    transform = request.transform
    if request.tier == "raw" or transform is None:
        return frozenset()
    if transform.winsorization.method == "none":
        return frozenset()
    if request.tier == "processed":
        return clipped_from_the_tie_at_the_top(rows, tier="processed")
    processed = _read(
        lambda: load_processed_factor_observations(
            store, definition, transform, years=request.years, as_of=request.as_of
        ),
        store=store,
        what=(
            f"the {transform.qualified_key} rows of {definition.qualified_key}, which say which "
            "residuals came from a clipped value"
        ),
    )
    return clipped_from_the_tie_at_the_top(
        tuple(
            (row.subject, row.value, row.coverage)
            for row in processed
            if row.as_of == instant and row.subject in {subject for subject, _v, _c in rows}
        ),
        tier="processed",
    )


def clipped_from_the_tie_at_the_top(
    rows: Sequence[tuple[str, float | None, str]], *, tier: FactorTier
) -> frozenset[str]:
    """The admitted subjects sharing the largest admitted value, or an empty set.

    Public and named after what it is rather than after what it measures, because it is a
    *recovery* rather than a reading: `the_clip_block_is_recovered_from_a_tie_and_may_over_report`
    is the limitation it leaves, and a rule a caller has to be able to read the disclosure of is a
    rule worth being able to call.

    `TIER_ADMITTED_CODES` rather than "has a number", which is the one cell that differs across the
    three tiers: a processed row coded `imputed` carries a value and is **not** admitted, so a
    median standing in for a missing input must not be read as a clip.
    """
    admitted = {
        subject: float(value)
        for subject, value, coverage in rows
        if value is not None and coverage in TIER_ADMITTED_CODES[tier] and math.isfinite(value)
    }
    if not admitted:
        return frozenset()
    largest = max(admitted.values())
    return frozenset(subject for subject, value in admitted.items() if value == largest)


def _bars_on(
    store: PanelStore,
    request: ShortlistRunRequest,
    *,
    session: date,
    universe: Sequence[str],
    calendar: TradingCalendar,
    as_of: datetime,
) -> dict[str, MarketBar]:
    """One session's bar and published band per security, as the execution policy's input type.

    `factor_view._PanelInputs.market_bar`'s rule and its three measured reasons, restated on one
    session rather than cached across a window:

    - **The band is always the exchange's own.** A security with no published band is simply
      absent, which stage two counts as `unbarred`; the derived fallback is measurably wrong on the
      Beijing board and on ST names outside the main board, so this face declines to price a
      session it has no published band for rather than pricing it from a rule the panel can
      contradict.
    - **`is_st` is `warning is not RiskWarning.none`**, made here and written out.
      `RiskWarning.__bool__` raises for every member including `none`, deliberately, so a truth
      test cannot merge `delisting_process` with `st` behind a reader's back -- somebody has to
      decide, and the decision is that all four warning states are "not an ordinary name" for band
      purposes.
    - **The board comes from the code's prefix**, because it decides the lot rule: a `688*` name
      filed under the main board would be sized under a 100-share multiple the exchange does not
      apply to it.

    `universe` narrows which bars are **built**, and that is a cost decision rather than a rule --
    unlike the registry filter `_component_cross_section` deliberately does not have.
    `CrossSectionScreen._filter` looks bars up by scored subject, so an extra key changes no
    verdict; what it changes is how many `MarketBar`s exist, and on a whole market that is five
    thousand objects and four `Decimal` conversions each. Nothing here can be told apart by a
    test, and nothing here decides anything, which is why it is written down as an economy
    instead of argued as a guard.
    """
    bars = _read(
        lambda: load_daily_bars(
            store, day=session, calendar=calendar, as_of=as_of, max_staleness=None
        ),
        store=store,
        what=f"the price bars for {session.isoformat()}",
    )
    limits = _read(
        lambda: load_price_limits(
            store, day=session, calendar=calendar, as_of=as_of, max_staleness=None
        ),
        store=store,
        what=f"the published limit bands for {session.isoformat()}",
    )
    halts = halt_corpus_for_years(
        _read(
            lambda: load_suspensions(store, years=request.years, as_of=as_of, max_staleness=None),
            store=store,
            what="the halt corpus",
        ),
        years=request.years,
    )
    names = _read(
        lambda: load_name_histories(store, years=request.years, as_of=as_of, max_staleness=None),
        store=store,
        what="the name histories",
    )
    listed = set(universe)
    priced: dict[str, MarketBar] = {}
    for subject, bar in bars.items():
        limit = limits.get(subject)
        if subject not in listed or limit is None:
            continue
        history = names.get(subject)
        priced[subject] = MarketBar(
            subject=subject,
            trade_date=session,
            board=_board(subject),
            previous_close=Decimal(str(bar.pre_close)),
            open=Decimal(str(bar.open)),
            high=Decimal(str(bar.high)),
            low=Decimal(str(bar.low)),
            close=Decimal(str(bar.close)),
            suspended=suspended_at_the_close(
                halts.state_on(session, subject), halts.timing_on(session, subject)
            ),
            is_st=history is not None and history.risk_warning_on(session) is not RiskWarning.none,
            **published_limit_fields(limit),
        )
    return priced


def _board(ts_code: str) -> Literal["main", "star", "growth", "bse"]:
    """The board a code belongs to, from its prefix; see `_bars_on` for why it is not defaulted."""
    if ts_code.startswith("688"):
        return "star"
    if ts_code.startswith("300"):
        return "growth"
    if ts_code.endswith(".BJ"):
        return "bse"
    return "main"


# --- V2-P4-033: one run, for the CLI, HTTP and the SDK ------------------------------------------


@dataclass(frozen=True, slots=True, kw_only=True)
class ShortlistRunResult:
    """One shortlist run's whole answer: what was read, what was cut, and whether it may ship.

    The three records are carried rather than collapsed because each answers a question the others
    cannot. `funnel` says which securities the market refused and why; `ranking` says which of the
    survivors the evidence plane has answers about; `clearance` says whether the list may be
    published at all and, when it may not, which bar it missed.

    **`is_blocked` is on this record and is read from the clearance rather than re-derived.**
    `ShortlistClearance` refuses `bool()`, `len()` and iteration -- including when it cleared -- so
    that "blocked" and "empty" cannot be merged by one line of caller code, and this property is
    what carries that guarantee to a face that has only JSON and a status code.
    """

    request: ShortlistRunRequest
    unused_evidence: tuple[str, ...]
    """Subjects the caller supplied an answer about that this cut did not reach, ascending.

    `rank_candidates` **refuses** a signal for a name the funnel did not shortlist -- "a name that
    reached the evidence plane without reaching the cut came from somewhere this record cannot
    describe" -- and that rule is right for the record. It is the wrong rule for a *face*: which
    names make the cut moves with the `as_of` and with every declared bar, so a caller who
    researched last week's fifty and asks about today's thirty would be refused for having done
    more work rather than less.

    So this face narrows the join and **says which answers it did not use**. Dropping them
    silently was the first version and a mutation proved it invisible: the caller's evidence
    vanished into a `researched_ratio` nobody could reconcile against what they had sent.
    """
    cross_section_as_of: datetime
    pricing_session: date
    universe: tuple[str, ...]
    components: tuple[ComponentCrossSection, ...]
    funnel: CrossSectionFunnel
    ranking: CandidateRanking
    clearance: ShortlistClearance

    @property
    def is_blocked(self) -> bool:
        return self.clearance.is_blocked


def run_shortlist(
    store: PanelStore,
    request: ShortlistRunRequest,
    *,
    built_at: datetime,
    execution: AShareExecutionPolicy | None = None,
) -> ShortlistRunResult:
    """Read the panel, cut the funnel, join the evidence plane, and gate the result.

    The one entry point all three faces call, which is what makes their answers one answer rather
    than three that agree today -- `run_factor_experiment`'s arrangement one plane over. It
    re-derives nothing: the shortlist is `CrossSectionScreen.select`'s, the ranking is
    `rank_candidates`', and the verdict is `gate_shortlist`'s.

    `execution` defaults to a plain `AShareExecutionPolicy`, which is the one defaulted argument in
    this module and is defaulted on purpose: the policy's `CostSchedule` moves the `ExecutionResult`
    a shortlisted name carries and moves **no** `filled`/`rejected` verdict, so it changes what a
    caller can read about a fill and not which names are on the list. A face that required it would
    be asking every caller to declare a schedule in order to ask a question the schedule does not
    decide.

    **The funnel is screened at the caller's `as_of` and not at the cross section's**, which is
    the mirror image of the rule `load_shortlist_cross_section` follows for its reads and is the
    same rule stated from the other end. `CrossSectionFunnel.as_of` and
    `CandidateRankingManifest.as_of` are "the instant this list is about" -- the question -- and
    `ShortlistGateSpec.maximum_ranking_age_days` is measured against it, so a list assembled today
    from a fortnight-old panel is a *fresh list over stale inputs* rather than a fortnight-old
    list. Which of the two it is, is exactly what `cross_section_as_of` says, and it is on every
    rendered answer for that reason. `rank_candidates` requires the two to agree, so this is one
    decision rather than two that could drift.

    **Nothing here runs the evidence plane.** See
    `the_evidence_plane_is_supplied_rather_than_run_by_this_module`: a face that researched every
    shortlisted name would make `researched_ratio` unable to be anything but `1.0`, and the bar
    `V2-P4-023` exists to measure would be unreachable from a surface.
    """
    if request.tier == "neutralized":
        raise ShortlistRequestError(
            "a neutralized-tier shortlist needs the industry and market-cap cross section its "
            "scores were neutralised against, and this face does not load one: that loader reads "
            "index_member_all through read_if_ready and answers only at an as_of at or after the "
            "newest stored assignment, which is V2-P4-027's issue rather than this one's. Screen "
            "on the raw or processed tier, where nothing was projected out"
        )
    section = load_shortlist_cross_section(store, request)
    screen = CrossSectionScreen(
        request.spec, execution=AShareExecutionPolicy() if execution is None else execution
    )
    try:
        funnel = screen.select(
            as_of=request.as_of,
            universe=section.universe,
            components=section.components,
            bars=section.bars,
        )
    except TwoStageFunnelError as error:
        raise ShortlistRunBlockedError(
            f"the stored cross section at {section.as_of.isoformat()} could not be screened: "
            f"{error}"
        ) from error

    shortlisted = {entry.subject for entry in funnel.shortlist}
    joined = {subject: item for subject, item in request.evidence.items() if subject in shortlisted}
    unused = tuple(sorted(set(request.evidence) - shortlisted))
    try:
        manifest = build_ranking_manifest(
            as_of=request.as_of,
            horizon=request.horizon,
            universe=list(section.universe),
            scoring_policy=request.spec,
            code_commit=request.code_commit,
            config_digest=request.config_digest,
            built_at=built_at,
        )
        ranking = rank_candidates(
            manifest=manifest,
            funnel=funnel,
            signals={subject: item.signal for subject, item in joined.items()},
            run_manifest_ids={subject: item.run_manifest_id for subject, item in joined.items()},
            exposures=None,
            predictions={},
        )
    except (CandidateRankingError, ValueError) as error:
        raise ShortlistRequestError(
            f"the shortlist at {request.as_of.isoformat()} could not be joined to the evidence "
            f"this request supplied: {error}"
        ) from error

    try:
        clearance = gate_shortlist(ranking=ranking, spec=request.gate)
    except ShortlistGateError as error:
        raise ShortlistRunBlockedError(
            f"the candidate list at {request.as_of.isoformat()} could not be gated: {error}"
        ) from error

    return ShortlistRunResult(
        request=request,
        unused_evidence=unused,
        cross_section_as_of=section.as_of,
        pricing_session=section.session,
        universe=section.universe,
        components=section.components,
        funnel=funnel,
        ranking=ranking,
        clearance=clearance,
    )


# --- the rendering the three faces share --------------------------------------------------------


def shortlist_view(result: ShortlistRunResult) -> dict[str, object]:
    """One shortlist run as data, for whichever face is handing it out.

    `panel_view`'s argument for existing at all: two renderings of one verdict that disagree about
    which fields exist is how a caller comes to believe a bar was cleared when the key was merely
    dropped. So the CLI's `--json`, `POST /api/v1/shortlists/run` and
    `OpenAlphaSDK.shortlist_view` emit these bytes and not three shapes that agree today.

    **`is_blocked` and `admitted` are the two keys the whole issue turns on.** `admitted` is `null`
    for a refused list and a JSON array -- possibly empty -- for an admitted one, so `null` and
    `[]` are the two answers the product acceptance found collapsed into one, and they are now
    two. `blocks` carries the bar, both sides of the comparison and the sentence that says what to
    do; `measurement` carries the numbers the verdict was read against on **both** verdicts,
    because a list that scraped over a bar and one that sailed over it are different facts.

    This reads `admitted_or_none` and `is_blocked` and never `bool()`, `len()` or iteration, all
    three of which raise on a `ShortlistClearance` *even when it cleared*.
    """
    clearance = result.clearance
    measurement = clearance.measurement
    admitted = clearance.admitted_or_none
    return {
        "schema_version": SHORTLIST_VIEW_SCHEMA_VERSION,
        "is_blocked": clearance.is_blocked,
        "gate_manifest_id": clearance.manifest.gate_manifest_id,
        "ranking_manifest_id": result.ranking.manifest.ranking_manifest_id,
        "ranking_content_digest": clearance.ranking_content_digest,
        "as_of": result.request.as_of.isoformat(),
        "horizon": result.request.horizon,
        "tier": result.request.tier,
        "cross_section": {
            "as_of": result.cross_section_as_of.isoformat(),
            "pricing_session": result.pricing_session.isoformat(),
            "universe_count": len(result.universe),
            "components": [
                {
                    "factor_id": component.factor_id,
                    "row_count": len(component.values),
                    "clipped_count": len(component.clipped_subjects),
                }
                for component in result.components
            ],
        },
        "funnel": {
            "coverage": result.funnel.coverage,
            "scored_count": result.funnel.scores.scored_count,
            "tradeable_count": result.funnel.tradeability.tradeable_count,
            "clip_block": result.funnel.clip_block,
            "tied_at_the_cut": result.funnel.tied_at_the_cut,
            "shortlist": [
                {"subject": entry.subject, "rank": entry.rank, "score": entry.score}
                for entry in result.funnel.shortlist
            ],
        },
        "measurement": {
            "universe_count": measurement.universe_count,
            "scored_count": measurement.scored_count,
            "tradeable_count": measurement.tradeable_count,
            "shortlist_count": measurement.shortlist_count,
            "candidate_count": measurement.candidate_count,
            "tradable_ratio": measurement.tradable_ratio,
            "researched_ratio": measurement.researched_ratio,
            "ranking_age_days": measurement.ranking_age_days,
        },
        "blocks": [
            {
                "code": block.code,
                "detail": block.detail,
                "measured": block.measured,
                "required": block.required,
            }
            for block in clearance.blocks
        ],
        "admitted": (
            None
            if admitted is None
            else [
                {
                    "subject": candidate.subject,
                    "rank": candidate.rank,
                    "score": candidate.score,
                    "direction": candidate.direction,
                    "confidence": candidate.confidence,
                    "run_manifest_id": candidate.run_manifest_id,
                    "risk_flags": list(candidate.risk_flags),
                }
                for candidate in admitted
            ]
        ),
        "unresearched": list(result.ranking.unresearched),
        "evidence_not_shortlisted": list(result.unused_evidence),
    }


def shortlist_rows(result: ShortlistRunResult) -> tuple[tuple[str, str, str, str], ...]:
    """The shortlist as `(rank, subject, score, evidence)` rows, for the one face a human reads.

    `evidence` is the candidate's direction and confidence when the evidence plane answered about
    it, and the word `unresearched` when it did not -- which is the column a reader of a refused
    list needs most, because `researched_ratio_below_floor` is a statement about exactly it.
    """
    answers = {candidate.subject: candidate for candidate in result.ranking.candidates}
    rows: list[tuple[str, str, str, str]] = []
    for entry in result.funnel.shortlist:
        candidate = answers.get(entry.subject)
        rows.append(
            (
                str(entry.rank),
                entry.subject,
                f"{entry.score:+.6f}",
                "unresearched"
                if candidate is None
                else f"{candidate.direction} @ {candidate.confidence:.2f}",
            )
        )
    return tuple(rows)


# --- reading the panel, once ---------------------------------------------------------------------


_PANEL_FAULTS: Final[tuple[type[Exception], ...]] = (
    PanelStorageError,
    FactorEngineError,
    NeutralizationEngineError,
    TradingCalendarError,
)
"""The refusals a stored panel raises when it cannot answer a read.

`factor_view._PANEL_FAULTS` restated, and the equality is pinned by
`tests/unit/test_shortlist_view.py::
test_this_face_calls_the_same_panel_faults_unreadable_as_the_factor_face` rather than left to
agree by inspection: which exceptions are facts about data
rather than defects in the code that read them is one question with one answer, and two faces that
answered it differently would put the same broken partition under two different status codes.
"""

_T = TypeVar("_T")


def _read(reader: Callable[[], _T], *, store: PanelStore, what: str) -> _T:
    """Run one panel read, turning its refusal into `ShortlistPanelUnreadableError`.

    The local message names the store and `disclosable` does not, `panel_view.stored_calendar`'s
    arrangement and for its reason: the CLI and the SDK are inside the process that owns the store,
    while a response body hands that path to whoever could reach the port.
    """
    try:
        return reader()
    except _PANEL_FAULTS as error:
        raise ShortlistPanelUnreadableError(
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
