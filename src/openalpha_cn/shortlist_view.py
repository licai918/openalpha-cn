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

## What the run keeps, and what it checks (`V2-P4-062`, `V2-P4-049`)

The two rows that turned this from a command into a workflow, and both are here rather than in
`backtest/` for the same reason the adapter above is: those contracts may reach no store, so the
funnel, the ranking and the gate cannot persist their own answers or resolve anything against a
stored run, and this is where a public face for them lives.

- **The answer is stored, by its own content address.** `shortlist_view` now mints
  `shortlist_id` -- `stable_answer_digest` over the whole rendered body, less the one key derived
  from a wall clock -- and `run_shortlist` hands the document to a `ShortlistDocumentStore` as the
  last thing it does. `factor_view.ExperimentDocumentStore` is the precedent for the Protocol and
  `storage/shortlists.py` is what satisfies it, with no import in either direction. Why the three
  addresses this body already carried could not be the key is measured there, not argued.
- **The evidence is resolved.** A supplied `run_manifest_id` used to be format-checked and nothing
  more, so an invented conclusion beside a well-formed literal cleared a `1.0` floor and was
  published with a provenance pointer that resolved to nothing. `stored_run_manifest_ids` is the
  set this deployment holds, evidence outside it is dropped before the ranking is built, and the
  names it was filed under are reported on `evidence_without_a_stored_run`. `V2-P4-075` narrows
  which stored runs resolve -- a `failed` or `interrupted` run is *held* and has not finished, and
  it used to clear a `1.0` floor -- and reports that second case apart, on
  `evidence_from_an_unfinished_run`. What that proves is bounded and the bound is written down:
  `a_resolved_run_manifest_is_not_a_resolved_signal`.
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, DecimalException
from hashlib import sha256
from types import MappingProxyType
from typing import ClassVar, Final, Literal, Protocol, TypeVar
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
    RefusedSecurity,
    ScoreComponent,
    ShortlistSpec,
    TradeabilityCensus,
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
from openalpha_cn.domain.daily_prices import DAILY_DATASET
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
from openalpha_cn.domain.name_history import (
    NAMECHANGE_DATASET,
    NameHistory,
    NameHistoryHorizonError,
    RiskWarning,
)
from openalpha_cn.domain.panel_batch import PanelBatchError
from openalpha_cn.domain.price_limits import PRICE_LIMIT_DATASET, SUSPENSION_DATASET
from openalpha_cn.domain.run import FINISHED_RUN_STATUSES, RunManifest
from openalpha_cn.domain.signal import SignalFrame
from openalpha_cn.domain.stock_universe import (
    STOCK_BASIC_DATASET,
    StockUniverse,
    StockUniverseError,
)
from openalpha_cn.domain.trading_calendar import (
    TRADING_CALENDAR_DATASET,
    TradingCalendar,
    TradingCalendarError,
)
from openalpha_cn.panel.catalog import PanelStorageError
from openalpha_cn.panel.store import PanelStore
from openalpha_cn.panel_factors import (
    FACTOR_DEFINITIONS,
    FACTOR_TRANSFORMS,
    FactorEngineError,
    factor_observation_dataset,
    load_factor_observations,
    load_processed_factor_observations,
    processed_factor_dataset,
)
from openalpha_cn.panel_ingest import (
    load_daily_bars,
    load_name_histories,
    load_price_limits,
    load_stock_universe,
    load_suspensions,
    load_trading_calendar,
    newest_published_session,
)
from openalpha_cn.panel_neutralization import (
    FACTOR_NEUTRALIZATIONS,
    NeutralizationEngineError,
    load_neutralized_factor_observations,
    neutralized_factor_dataset,
)
from openalpha_cn.panel_view import PANEL_STORE_PLACEHOLDER, panel_store

__all__ = [
    "KNOWN_SHORTLIST_VIEW_LIMITATIONS",
    "MAX_NAMED_UNTRADEABLE",
    "SHORTLIST_ANSWER_UNADDRESSED_KEYS",
    "SHORTLIST_DOCUMENT_SCHEMA_VERSION",
    "SHORTLIST_PANEL_DATASETS",
    "SHORTLIST_VIEW_LIMITATION_CODES",
    "SHORTLIST_VIEW_SCHEMA_VERSION",
    "ResearchRunStore",
    "ShortlistCrossSection",
    "ShortlistDocumentStore",
    "ShortlistEvidence",
    "ShortlistNotHeldError",
    "ShortlistPanelUnreadableError",
    "ShortlistRequestError",
    "ShortlistRunBlockedError",
    "ShortlistRunRequest",
    "ShortlistRunResult",
    "ShortlistViewError",
    "ShortlistViewLimitation",
    "ShortlistWrite",
    "StoredRunAddresses",
    "clipped_from_the_tie_at_the_top",
    "held_shortlist",
    "load_shortlist_cross_section",
    "named_untradeable",
    "open_shortlist",
    "panel_store",
    "run_shortlist",
    "shortlist_components",
    "shortlist_document",
    "shortlist_evidence",
    "shortlist_request",
    "shortlist_rows",
    "shortlist_view",
    "stable_answer_digest",
    "stored_run_manifest_ids",
]

SHORTLIST_VIEW_SCHEMA_VERSION: Final[str] = "shortlist-view/v1"
"""The version of the envelope `shortlist_view` renders, carried in the body.

A face's rendering is a contract of its own -- `factor_view.VIEW_SCHEMA_VERSION`'s reason: the
sealed records underneath already carry their own `schema_version`, and this says which *shape*
the three faces agreed to hand out around them.
"""

POSITION_CAPITAL_CEILING: Final[Decimal] = Decimal(10) ** 26
"""The first budget whose own fill this build cannot price. Exclusive: a capital must be below it.

`V2-P4-045`. `ShortlistSpec.position_capital` is `Field(gt=0)` and bounded nowhere above, while
every sibling numeric on this request is bounded on both sides -- `shortlist_size` at 10,000
(1,000 when this was measured; `V2-P4-031` restated it from the batch cap), each
weight at 1000, both ratios at 1. Measured through `TestClient` before this constant existed:
`1e25` answered `200`, `1e26` answered a bare `500` with `text/plain` `Internal Server Error`, and
so did `1e400`.

**The number is read off the execution policy rather than chosen.** Stage two sizes a buy with
`position_quantity`, which floors the lot count, so the notional it hands
`AShareExecutionPolicy.execute` is never above the capital itself. That policy's first act is
`(market.close * quantity).quantize(_CENT)`, and a value quantized to cents carries two digits
after the point -- so a notional at or above `10**26` needs more than the 28 significant digits
`decimal`'s default context holds, and `quantize` raises `InvalidOperation`. It is an
`ArithmeticError`, so it passed every `except TwoStageFunnelError` and `except ShortlistViewError`
on all three faces and arrived as an unhandled defect.

The ceiling is therefore the same at every close price **a price feed produces** -- measured
across `0.01` to `10000.00` rather than assumed, because `notional <= capital` makes *that*
bound a fact about the budget alone. `tests/integration/test_shortlist_interfaces.py::
test_the_largest_representable_capital_is_still_answered` drives `10**26 - 1` and requires `200`,
so a later "fix" that refused every large budget would fail rather than pass.

**`V2-P4-058` narrowed the sentence above, which said "the same at every close price" without
the qualifier.** The `notional <= capital` half is sound and re-measured. What it does not cover
is the *other* arithmetic on the path: `position_quantity` computes
`int(capital // (market.close * SHARE_LOT))`, and a **quotient**'s digit count grows as `close`
falls, while `MarketBar.close` is `Field(gt=0)` with no lower bound. Measured: `close=1e-12` with
`capital=1e20` -- four orders of magnitude *below* this ceiling, satisfying both bounds -- still
raises `InvalidOperation: [DivisionImpossible]`. Both halves are pinned in
`tests/unit/test_position_capital_ceiling.py`: `test_the_capital_ceiling_holds_across_the_price
_range_it_was_measured_over` for the true one, and `test_a_close_price_below_the_feeds_own
_resolution_still_overflows_under_the_ceiling` for the limit.

Neither a lower bound on `close` nor a `try/except InvalidOperation` was added, and the reason is
this repository's own standard rather than effort. A two-decimal price feed cannot reach it, so
there is no shipped defect here -- only a sentence that claimed more than was measured. A price
floor would be a number nobody can measure (this constant's own first line refuses that) and a
new refusal on rows that price correctly today; a catch would be a branch no input reaches, which
`position_quantity`'s docstring declines to write for exactly this reason, one call below. The
honest repair to a claim wider than its measurement is to narrow the claim.

**The bound is now on `ShortlistSpec.position_capital` as well, and this is the second of two
literals.** Commit `3e83587` applied the reported dependency: the field carries
`lt=Decimal(10) ** 26` in `backtest/cross_section.py`, beside the three siblings that already had
one, so a directly-constructed spec is bounded and not only one arriving through the three faces.
That leaves the number written twice, in two files, with nothing making them agree --
`V2-P4-058`, which also found `grep -rn POSITION_CAPITAL_CEILING tests/` returning nothing at
all. `tests/unit/test_position_capital_ceiling.py` reads the field's own metadata and requires
the two to be equal, so the pair cannot drift; the duplication itself stays, because
`backtest/cross_section.py` may not import this module -- `backtest-no-numeric-stack-or-panel
-plane` lists `openalpha_cn.shortlist_view` among its `forbidden_modules` -- so de-duplicating
would mean moving the constant under `domain/`, which is a relocation and not this row's work.
"""

SHORTLIST_DATE_ZONE: Final[ZoneInfo] = ZoneInfo("Asia/Shanghai")
"""The zone a cross-section instant is resolved into a **session** in.

`factor_view.FACTOR_DATE_ZONE`'s constant restated for its reason rather than imported for
symmetry: an A-share session is a calendar day on the exchange's own clock, and a build stamped
2026-01-16T09:00Z is 17:00 in Shanghai -- after that session's close, and therefore about it. In
UTC the same instant is still 2026-01-16 and the two agree; two hours later they do not, and the
zone is what decides which session's bars stage two prices against.

**The zone alone was not enough, which is `V2-P4-077`.** A calendar day turns over at midnight
and a session publishes at 16:30, so the sixteen hours between them are a day this instant is
*in* and a session it cannot see. `_pricing_session` reads the second clock too; see it.
"""

SHORTLIST_PANEL_DATASETS: Final[Mapping[str, str]] = MappingProxyType(
    {
        TRADING_CALENDAR_DATASET: TRADING_CALENDAR_DATASET,
        STOCK_BASIC_DATASET: STOCK_BASIC_DATASET,
        DAILY_DATASET: "price",
        PRICE_LIMIT_DATASET: PRICE_LIMIT_DATASET,
        SUSPENSION_DATASET: "price",
        NAMECHANGE_DATASET: NAMECHANGE_DATASET,
    }
)
"""Every panel dataset this face reads, mapped to the `panel build` target that writes it.

`V2-P4-078`. `shortlist run` reaches `NameHistory.risk_warning_on` through `_bars_on` for every
`MarketBar`'s `is_st`, so it **must** have `namechange`; `openalpha factor build --tier raw`
neither needs nor fetches it, `BUILD_TARGETS` in `tests/e2e/e2e_support.py` does not build it,
and the refusal a user then met named the partition and not the command. That is `V2-P4-067`(b)
measured from the other side, against a bar the same acceptance had already set:
`panel_view.NO_CALENDAR_REMEDY` names `openalpha panel build --dataset trade_cal --year <year>`
and every refusal on this face named nothing.

**Keyed by dataset and valued by target, because the two vocabularies are not the same one.**
`panel build --dataset daily` is refused by name -- `write_daily_panel` takes the bars, the
valuations and the halts together, so `PANEL_BUILD_COUPLED_DATASETS` sends the caller to
`price` -- and a remedy spelling `daily` would name a command that does not run. Six datasets,
five targets. `tests/integration/test_shortlist_build_prerequisites.py` holds every value
against `cli.PANEL_BUILD_TARGETS` and drives each target's absence through a face.

`adj_factor` is not here, measured rather than assumed: it is the sixth target the end-to-end
panel fetches, and with it omitted both `factor build --tier raw` and `shortlist run` still
answer. Naming it would send a user on a build measured in hours for a partition this face
never opens.
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


class ShortlistNotHeldError(ShortlistViewError):
    """Nothing is held under the content address a caller asked to retrieve, or it will not open.

    `V2-P4-062`. A separate row from `bad_request` because the remedy is different in kind: the
    address is well formed and this store has never seen it, which a caller fixes by running the
    shortlist rather than by editing the question. A malformed address -- one that is not
    `stable_answer_digest`'s own output -- is `bad_request` and is refused before this store is
    touched at all, so "we looked and there is nothing" and "that is not an address" stay two
    answers.

    It also covers a held document that does not reopen: `open_shortlist` recomputes the digest
    from the content and compares it against the key the document was filed under, so a payload
    edited on disk is a refusal here rather than an answer a caller reads names off.
    """

    reason: ClassVar[str] = "not_held"


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
            "`run_cycle`, and the reason first given for that has lapsed: it said a face that "
            "researched every shortlisted name would make `researched_ratio` unable to be "
            "anything but 1.0, which held only because `V2-P4-029` makes an abstaining run raise "
            "rather than leave a name unresearched. `V2-P4-049` retired it a second way -- since "
            "the `run_manifest_id` is resolved against the stored runs, evidence that resolves to "
            "nothing leaves its name unresearched and the ratio is reachable below 1.0 whether or "
            "not `029` is fixed. What still keeps `run_cycle` out is a layering fact rather than "
            "an arithmetic one: this is a panel-plane face and running the evidence plane would "
            "make a screen of the stored cross section also an agent invocation, which is a "
            "second command wearing one name."
        ),
    ),
    ShortlistViewLimitation(
        code="a_resolved_run_manifest_is_not_a_resolved_signal",
        detail=(
            "KNOWN_SHORTLIST_VIEW_RESOLUTION: `V2-P4-049` made the supplied `run_manifest_id` "
            "resolve against the stored runs, so evidence pointing at a run this deployment never "
            "made no longer counts toward `researched_ratio` and is reported as "
            "`evidence_without_a_stored_run`; `V2-P4-075` narrowed the resolving set to the runs "
            "that **finished**, so a held `failed` or `interrupted` run no longer counts either "
            "and is reported apart as `evidence_from_an_unfinished_run`. What is resolved is the "
            "**run**, and not the "
            "`SignalFrame` beside it: this repository stores no signal (see "
            "`the_evidence_plane_is_supplied_rather_than_run_by_this_module`), so there is nothing "
            "to resolve one against, and a caller who holds a real `run_manifest_id` can still "
            "file an invented conclusion under it. The frame is checked for internal consistency "
            "-- `shortlist_evidence` verifies its `signal_id` against its own content and "
            "`shortlist_request` refuses one keyed by one security and about another -- and that "
            "is a statement about the document rather than about who produced it. The property "
            "delivered is that a published `run_manifest_id` resolves to a run this deployment "
            "holds; it is not that the conclusion beside it came out of that run."
        ),
    ),
    ShortlistViewLimitation(
        code="the_stored_answer_is_addressed_by_content_and_not_by_when_it_was_run",
        detail=(
            "KNOWN_SHORTLIST_VIEW_STORE: `V2-P4-062`'s store is keyed by `shortlist_id`, which is "
            "`stable_answer_digest` over the rendered answer less "
            "`SHORTLIST_ANSWER_UNADDRESSED_KEYS`. Two runs producing one answer therefore produce "
            "one document and the second is `unchanged`, which is what makes a re-run cost "
            "nothing and a reader's copy stable -- and it means the store cannot say **how many "
            "times** an answer was reached or **when**, because a wall clock in the key would "
            "mint a new document for every repetition of one answer. The one rendered value left "
            "out of the address, `measurement.ranking_age_days`, is left out for that: it is "
            "`built_at - as_of`, so it moves every day the same shortlist is re-run. A caller who "
            "needs a run log wants the `RunManifest` plane, not this one; what this holds is the "
            "set of distinct answers this deployment has produced, each retrievable by the "
            "address its own answer carries."
        ),
    ),
    ShortlistViewLimitation(
        code="a_neutralized_tier_screen_needs_exposures_this_face_does_not_load",
        detail=(
            "KNOWN_SHORTLIST_VIEW_NEUTRALIZED: `rank_candidates` refuses `exposures=None` on the "
            "neutralized tier, because an industry mean and a size slope have already been "
            "subtracted out of every score and a ranking that cannot say what was removed has no "
            "readable explanation. The cross section that says it is "
            "`IndustryMarketCapCrossSection`. **THE REASON THIS ENTRY GIVES HAS CHANGED AND THE "
            "CHANGE IS PART OF IT.** It used to be that the loader read `index_member_all` "
            "through `read_if_ready` and therefore answered only at an `as_of` at or after the "
            "newest stored assignment -- `V2-P4-027`'s issue rather than this face's. `V2-P4-028` "
            "removed that: `panel_neutralization.load_industry_market_cap_cross_section` reads "
            "through `panel_ingest.load_industry_cross_section`, which is day-scoped, so the "
            "instant is no longer the obstacle. WHAT REMAINS IS THIS FACE'S OWN AND IS SMALLER: "
            "it loads no exposure cross section, and doing so needs three things a shortlist "
            "request does not carry -- the membership years to read, a trading calendar for the "
            "pricing session, and the neutralisation whose `industry_level` and "
            "`market_cap_measure` decide what the exposures ARE. So the **adapter** here serves "
            "all three tiers and `run_shortlist` refuses `tier='neutralized'` by name rather than "
            "loading an exposure cross section at an instant that would not match the screen's. "
            "Screen on `raw` or `processed`, where nothing was projected out."
        ),
    ),
    ShortlistViewLimitation(
        code="a_name_never_announced_inside_the_requested_years_is_screened_as_ordinary",
        detail=(
            "KNOWN_SHORTLIST_VIEW_NAMES: `MarketBar.is_st` is read off the stored rename corpus, "
            "and `load_name_histories` is scoped to the announcement years the run asked for. A "
            "security with no row in those years has no `NameHistory` at all, and "
            "`_risk_warned_on` answers `False` for it -- which is right for a name that has not "
            "been renamed and **wrong** for one that was put under special treatment in an "
            "earlier year and is still under it, because a rename is dated at its announcement "
            "and an announcement made in an earlier year is in a partition this read did not "
            "cover. `V2-P4-080` fixed the neighbouring case and deliberately did not fix "
            "this one: a security whose earliest row in the requested years takes effect *after* "
            "the session being priced is refused by name, because the corpus positively shows a "
            "rename this run cannot resolve. Absence shows nothing, and refusing on it would "
            "refuse almost every honest run -- most of the market has no rename announced in any "
            "one year. The remedy available to a caller is the same one the refusal names: ask "
            "for the announcement years that cover the security's last rename. "
            "`panel_ingest.load_name_histories` states the same bound from the read's side."
        ),
    ),
)

SHORTLIST_VIEW_LIMITATION_CODES: Final[frozenset[str]] = frozenset(
    limitation.code for limitation in KNOWN_SHORTLIST_VIEW_LIMITATIONS
)


SHORTLIST_DOCUMENT_SCHEMA_VERSION: Final[str] = "shortlist-document/v1"
"""The version of the envelope a stored shortlist answer is wrapped in.

A version of its own beside `SHORTLIST_VIEW_SCHEMA_VERSION` rather than reusing it, because the
two can move independently: the answer's shape is what three faces agreed to hand out, and this is
what a *document* on disk looks like around it. `FactorExperimentRecord` carries the same pair for
the same reason one plane over.
"""

MAX_NAMED_UNTRADEABLE: Final[int] = 50
"""How many refused securities `funnel.untradeable` names before it starts counting instead.

`V2-P4-066` asked for the names and this is the bound on them, stated here rather than left to be
discovered. `TradeabilityCensus.refused` names **every** refused security -- it is a record and
not a response -- and this is a rendering that goes into an HTTP body, a terminal and a stored
document, so it is the one place the length has to stop scaling with the market. `V2-P4-110` is
the row that measured what the other choice costs: a `422` echoing its own input reached
13.18 MiB.

Fifty rather than a round ten, chosen against the acceptance this row came from: the whole-market
screen it measured lost **nine** names at this tier, so fifty leaves the ordinary answer complete
and truncates only a day when the market itself is shut. `untradeable_not_named` carries the
residual so a truncated list is never mistaken for a short one, and the *counts* --
`refused_by_verdict` and `rejection_reasons` -- are exact whatever the length, because those are
bounded by the vocabulary rather than by the market.
"""

SHORTLIST_ANSWER_UNADDRESSED_KEYS: Final[frozenset[str]] = frozenset({"ranking_age_days"})
"""The rendered `measurement` keys that are recorded and **not** addressed by `shortlist_id`.

One member, and it is here for `RUN_MANIFEST_UNADDRESSED_FIELDS`' stated reason:
`ranking_age_days` is `built_at - as_of`, so it is a wall clock in disguise, and an identity that
moved for it would mint a new document every day the same shortlist was re-run --
`FactorInputRef`'s own defect, which had to be given back: "an identity that moves for nothing
makes a rebuild unwritable and its predecessor unreproducible."

It is a `frozenset` audited against the rendered body rather than a literal deleted inline, which
is this repository's shape for every such exclusion: `tests/integration/test_shortlist_workflow.py`
partitions `measurement`'s own keys against it, so a key added to the rendering is red until it is
either measured to move the address or given a reason here.
"""


def stable_answer_digest(answer: Mapping[str, object]) -> str:
    """`sla_...`: the content address of one rendered shortlist answer.

    The fourth address on this body, and the only one that names *this answer* -- see
    `storage/shortlists.py` for the three that were tried first and the case each one loses.
    Briefly: `ranking_manifest_id` addresses the question, `gate_manifest_id` addresses the
    question and the bars but not the supplied evidence, and `ranking_content_digest` addresses
    the researched candidates only, so two unrelated shortlists with no evidence share it.

    A free function over a mapping rather than `stable_model_id` over a model, which is the form
    this repository already uses wherever the thing addressed is not a pydantic model --
    `set_digest`, `characteristic_digest` and `ranking_content_digest` are the three precedents,
    and `candidate_ranking.py`'s docstring names that split explicitly. **The canonicalisation is
    `stable_model_id`'s own** -- sorted keys, fixed separators, `ensure_ascii=False`,
    `allow_nan=False`, sha256, first 24 hex characters -- because a second spelling of "canonical"
    is a second thing that can disagree about one.

    `SHORTLIST_ANSWER_UNADDRESSED_KEYS` is removed from `measurement` before hashing and from
    nowhere else; the key stays in the rendered body, which is the difference between *recorded*
    and *addressed*.
    """
    measurement = answer.get("measurement")
    if not isinstance(measurement, Mapping):
        raise ShortlistRequestError(
            "a shortlist answer carries a `measurement` object; this one carries "
            f"{type(measurement).__name__}, so there is nothing to address"
        )
    addressed = {
        **answer,
        "measurement": {
            key: value
            for key, value in measurement.items()
            if key not in SHORTLIST_ANSWER_UNADDRESSED_KEYS
        },
    }
    canonical = json.dumps(
        addressed, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode()
    return f"sla_{sha256(canonical).hexdigest()[:24]}"


ShortlistWrite = Literal["created", "unchanged"]
"""What the document store did with an arriving answer.

Two members and **no third**, and the missing one is the difference between this store and
`ExperimentDocumentStore`'s. There, "refused" is a real outcome: an `experiment_id` is the address
of a *declaration* and a second answer under one is a build that did not reproduce. Here the key
is the address of the answer itself, so two answers that differ have two keys and the refusal has
nothing to refuse.
"""


class ShortlistDocumentStore(Protocol):
    """The byte store one rendered shortlist answer is handed to.

    A `Protocol` so this module imports no store: `storage/shortlists.py` satisfies it
    structurally, and the import graph stays `shortlist_view -> {backtest, panel_*, domain}` with
    no edge into `openalpha_cn.storage`. `factor_view.ExperimentDocumentStore` is the precedent --
    declared beside the consumer, naming exactly the methods that consumer calls -- and it is the
    precedent that made this row solvable at all: `backtest/` may not reach a store, so the funnel,
    the ranking and the gate cannot persist their own answers, and this module is where a face for
    them lives.

    Every parameter is a string. Nothing here has an opinion about what a payload means, which is
    what keeps the store below `openalpha_cn.backtest` rather than above it.
    """

    def put(self, *, shortlist_id: str, payload: str) -> ShortlistWrite:
        """Hold `payload` under `shortlist_id`, keeping held bytes if something is there."""

    def get(self, shortlist_id: str) -> str | None:
        """The held payload for `shortlist_id`, or `None`."""

    def list_ids(self) -> tuple[str, ...]:
        """Every held `shortlist_id`, ascending."""


class ResearchRunStore(Protocol):
    """The stored runs a supplied `run_manifest_id` is resolved against (`V2-P4-049`).

    `ShortlistDocumentStore`'s arrangement and its reason, and one method rather than two: this
    module needs the set of `run_manifest_id`s a deployment holds and nothing else about a run.
    `runtime/repository.py::RunRepository` is the sibling Protocol on the same store and declares
    `get_run(run_id)`, which is the wrong key here -- `run_id` is the caller's name for a run and
    `run_manifest_id` is `stable_model_id`'s address of its whole declaration, and it is the second
    that a published candidate carries.

    **`list_runs` and not a lookup, because the store has no index on the address.**
    `SQLiteRunRepository` files a run under `run_id` with the manifest as an opaque payload;
    `run_manifest_id` is a `computed_field`, so there is no column to select on and the honest
    signature is the one that says so. See `stored_run_manifest_ids` for what that costs and the
    one call per shortlist run it costs it at.
    """

    def list_runs(self) -> tuple[RunManifest, ...]:
        """Every stored run manifest."""


@dataclass(frozen=True, slots=True, kw_only=True)
class StoredRunAddresses:
    """What one pass over the stored runs says about the addresses evidence may name.

    Two sets rather than one, and the split is `V2-P4-075`. `held` is every address this
    deployment holds a run for -- the literal claim `stored_run_manifest_ids` always made, and it
    stayed exactly true of a `RunManifest(status="failed")`. `finished` is the subset whose runs
    **produced the conclusions they were asked for** (`FINISHED_RUN_STATUSES`), and it is the one
    the evidence join resolves against.

    Kept apart rather than collapsed because the two differences have different remedies, which is
    `unused_evidence`'s own rule applied one step down: a name whose address is in neither is
    evidence about a run nobody made and the remedy is to run the research; a name in `held` and
    not in `finished` names a run this deployment *did* make and that did not finish, and the
    remedy is to go and look at why. Reported apart for that reason, on
    `evidence_without_a_stored_run` and `evidence_from_an_unfinished_run`.

    `finished <= held` by construction and `__post_init__` requires it, so a caller reading the
    difference of the two is reading a partition rather than whatever two independent passes
    happened to produce.
    """

    held: frozenset[str]
    finished: frozenset[str]

    def __post_init__(self) -> None:
        if not self.finished <= self.held:
            raise ShortlistRequestError(
                "a finished run's address is a stored run's address; "
                f"{sorted(self.finished - self.held)} is in neither order"
            )


def named_untradeable(
    tradeability: TradeabilityCensus,
) -> tuple[tuple[RefusedSecurity, ...], int]:
    """The refused securities a face may name, and how many it must leave unnamed.

    One decision rather than two, and `V2-P4-066`'s own mutation sweep is why it is a function.
    The `--json` body and the terminal rendering both truncate at `MAX_NAMED_UNTRADEABLE`, and
    with each face slicing for itself the two could disagree about what "the first fifty" means --
    or, as measured, one face could be held to the ceiling by a test while the other quietly
    read a stale copy of the number. A face that renders the pair renders one answer.

    The residual is returned beside the list rather than left to be recomputed from a length,
    because `len(refused) - MAX_NAMED_UNTRADEABLE` is the subtraction a reader gets wrong when
    nothing was truncated: it is negative, and a body reporting `-48` unnamed securities would be
    a rendering defect wearing a number.
    """
    named = tradeability.refused[:MAX_NAMED_UNTRADEABLE]
    return named, len(tradeability.refused) - len(named)


def stored_run_manifest_ids(runs: ResearchRunStore) -> StoredRunAddresses:
    """Every `run_manifest_id` this deployment holds a run for, and the finished ones (`V2-P4-075`).

    `V2-P4-049`, which the P4 re-acceptance measured like this: `run_manifest_id` was only
    format-checked and a supplied `SignalFrame` only had to hash to its own address, so an invented
    signal beside the literal `run_000000000000000000000000` cleared a `--min-researched-ratio 1.0`
    floor and published 25 candidates carrying `researched_ratio: 1.0`. The declared limitation
    said the evidence was *supplied rather than run*; it did not say it was unverifiable, and the
    published answer carried a provenance pointer that resolved to nothing.

    **Cost, stated rather than discovered.** `RunManifest.run_manifest_id` is a `computed_field`
    over the run's whole declaration and no column carries it, so this reads every stored run and
    recomputes the address. That is one pass per shortlist run and not one per shortlisted name --
    the set is built once in `run_shortlist` and the evidence is a membership test against it --
    and `list_runs` already validates every payload it returns, so the added work is the digest
    rather than the parse. Measured shape on the `runs` table: `SQLiteRunRepository.list_runs`
    over 100,000 stored runs is ~450 ms, which is what a deployment with 100,000 research runs
    would add to one shortlist. Giving the address a column and an index is the repair when that
    bites -- `V2-P4-002` did exactly that for `mode`, and its docstring is the template -- and it
    is a migration rather than this row's work.

    **`V2-P4-075`: holding a run is not the same as having finished one, and the gap was
    reachable.** The P4 fourth-round acceptance stored a `RunManifest(status="failed")` and a
    `RunManifest(status="interrupted")`, filed evidence for every shortlisted name against their
    addresses, and cleared `--min-researched-ratio 1.0` at `researched_ratio: 1.0` on both. The
    docstring above was *literally* true throughout -- the deployment did hold those runs -- and
    what was false was the thing built on it: `ShortlistGateBlock("researched_ratio_below_floor")`
    called that ratio "a fact about which runs finished". So the ratio now measures what the
    refusal says it measures, and the two sets are returned apart rather than the loose one being
    narrowed in place, so a caller can still be told which of the two it is.

    **The quantifier is over the address, not over a row.** `status` is in
    `RUN_MANIFEST_UNADDRESSED_FIELDS`, so an interrupted run and its successful re-run are one
    declaration at one address; `finished` is therefore "this deployment holds **a** finished run
    here", built by union rather than by last-writer-wins over a mapping. That is unreachable
    through `SQLiteRunRepository` today for a reason worth writing down rather than relying on --
    `runs` is keyed by `run_id`, `run_id` *is* addressed, and `append_run` refuses a second row
    under one `run_id`, so one address holds at most one row there -- but `ResearchRunStore` is a
    Protocol and a caller's own store is under no such constraint.
    """
    held: set[str] = set()
    finished: set[str] = set()
    for manifest in runs.list_runs():
        held.add(manifest.run_manifest_id)
        if manifest.status in FINISHED_RUN_STATUSES:
            finished.add(manifest.run_manifest_id)
    return StoredRunAddresses(held=frozenset(held), finished=frozenset(finished))


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
    neutralization_spec = _resolve_neutralization(
        neutralization, tier=resolved_tier, registry=neutralizations
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
    if not position_capital.is_finite() or position_capital >= POSITION_CAPITAL_CEILING:
        raise ShortlistRequestError(
            f"--position-capital {position_capital} is at or above "
            f"{POSITION_CAPITAL_CEILING}, which is the first budget whose own fill this build "
            "cannot price: stage two quantizes a notional to cents, and a notional that large "
            "needs more significant digits than decimal's context carries, so the screen would "
            "raise rather than answer. Declare a budget below it -- it is a notional per name "
            "and not a fund size, and nothing here allocates"
        )
    if not 7 <= len(code_commit.strip()) <= 64:
        raise ShortlistRequestError(
            f"--code-commit must be between 7 and 64 characters; got {code_commit!r}. Different "
            "code may cut a different list from the same rows, so an identity that ignored it "
            "would claim a reproducibility it cannot deliver"
        )
    if not re.fullmatch(r"[0-9a-f]{64}", config_digest.strip()):
        raise ShortlistRequestError(
            f"--config-digest must be 64 lowercase hex characters; got {config_digest!r}. "
            "Different configuration may cut a different list from the same rows, so an identity "
            "that ignored it would claim a reproducibility it cannot deliver"
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
    token: str | None, *, tier: FactorTier, registry: FactorNeutralizationRegistry
) -> FactorNeutralizationSpec | None:
    """The neutralisation a residual read is addressed by; `_resolve_transform`'s exact twin.

    **`tier` is a parameter for `V2-P4-050`'s reason.** This used to be reached only from the
    `neutralized` branch of `shortlist_request`, so a `--neutralization` declared beside `--tier
    raw` or `--tier processed` was accepted, never read and never mentioned: the caller asked for
    a neutralised screen, got a raw one, and the two answers were byte-identical. `--transform`
    on the raw tier has been refused by name since this module was written; a flag that moves
    nothing is the same defect whichever flag it is, and the asymmetry was the whole of it.

    Refused rather than honoured, because honouring it would mean loading the industry-and-size
    cross section this face deliberately does not load -- see
    `a_neutralized_tier_screen_needs_exposures_this_face_does_not_load`.
    """
    if tier != "neutralized":
        if token is not None and token.strip():
            raise ShortlistRequestError(
                f"--neutralization {token!r} was given for a {tier}-tier screen, which reads no "
                "residual and subtracts nothing. A neutralisation addresses the neutralized "
                "partition only, so this flag would have moved no security on this tier and no "
                "value in this answer. Drop it, or screen on the neutralized tier"
            )
        return None
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

    ## The sentence above was written before the panel could keep it (`V2-P4-061`)

    "Offered to the market of its own session" describes what this function *asks for*, and until
    `V2-P4-061` the panel could not answer for any session but the newest. `load_daily_bars` and
    `load_price_limits` read through `read_if_ready`, which judges `not_yet_knowable` on the
    newest `available_time` anywhere in the **year partition** -- so a store advancing a single
    session made every earlier cross section in that year unscreenable, and the offer this
    docstring, `docs/api/http.md` and `README.md` all describe was never made. Reproduced from the
    surface: two cross sections written into one store, the newest screening cleanly and the
    earlier one exiting `1` with `daily cannot be read at ...: ['not_yet_knowable']`. What it cost
    is the product's stated purpose -- two days' shortlists could not be compared, yesterday's
    could not be re-run, and a published list could not be audited after the fact.

    Both loaders now take `panel_ingest._read_visible_price_session`, the as-of-sensitive session
    read `V2-P4-026` built and wired to `load_daily_valuations` alone. **Both** had to move: the
    two are read together three lines apart in `_bars_on`, and moving the bars alone relocated the
    same refusal onto `stk_limit` rather than removing it.
    `tests/integration/test_shortlist_earlier_sessions.py` drives the pair from all three faces,
    and holds the withheld/absent separation the session read rests on.

    ## The wall did not fall then; it moved (`V2-P4-076`)

    `V2-P4-061` fixed the three datasets it named and no fixture could show what it left. On a
    real panel the earlier cross section was still refused, because **four** things beside the
    prices are read at the resolved instant and three of them still took `read_if_ready`:

        trading calendar ... trade_cal      calendar_publication   not part of the wall
        security registry .. stock_basic    calendar_static        moved by `V2-P4-076`
        halts .............. suspend_d      daily_close            moved by `V2-P4-076`
        name histories ..... namechange     calendar_static        moved by `V2-P4-076`

    Measured on a real panel: `stock_basic` at 2026-08-19T00:00+08 and `suspend_d` at
    2026-08-19T16:30+08, against a price panel whose newest session was the same day -- so the
    registry alone refused every cross section before that day's midnight. Reproduced from the
    surface with the earlier run exiting `1` on `the security registry could not be read ...
    ['not_yet_knowable']`, and behind it `suspend_d` and `namechange` with the same verdict.

    The three now take `panel_ingest._read_visible_event_dated_rows`, the per-event-date census
    reconciliation `V2-P4-027`/`034` built for `index_member_all`, each with its own availability
    rule and its own census bound -- `suspend_d`'s is the newest *published session* and not
    `as_of`'s calendar day, because a halt is knowable at 16:30 on its own `trade_date`.

    **The calendar is left where it is, and that is a measurement rather than an omission.**
    `_calendar_publication_timeline` dates every row of year Y available at 1 January of Y, so a
    year partition's newest availability instant is the earliest instant in it and
    `not_yet_knowable` has no way to fire at an `as_of` inside the year.

    `tests/integration/test_shortlist_whole_year_reads.py` drives all of it from the three faces,
    over a corpus built from the three `PANEL_SHAPES` entries that carry the form no fixture had
    -- a whole-year partition whose newest row lands on the newest session -- and
    `tests/integration/panel/test_event_dated_visible_reads.py` holds the withheld/absent
    separation per dataset.
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
        dataset=TRADING_CALENDAR_DATASET,
    )
    session = _pricing_session(instant, calendar=calendar)

    registry = _read_registry(
        lambda: load_stock_universe(store, years=request.years, as_of=instant, max_staleness=None),
        store=store,
    )
    # `listed_on` refuses two horizons -- a day past `snapshot_date`, and a day before the first
    # lifecycle year the read covered -- and `factor_view._computed` guards the identical call
    # because both of them fire there. Neither can fire here, and the difference is which day is
    # handed over. `factor_view` passes `as_of`'s own calendar date, which `request.years` does
    # not constrain at all; this face passes a **session**, which `newest_published_session` takes
    # out of a calendar loaded over `request.years` and never dates later than `instant` itself.
    # `load_trading_calendar` reads exactly the years it is given, and `load_stock_universe`
    # widens only downwards, so `years_read[0] <= request.years[0] <= session.year` and
    # `session <= min(instant.date(), 31 December of request.years[-1]) <= snapshot_date`. A day
    # the calendar cannot place is refused by `_pricing_session` before it ever reaches here.
    # Measured rather than argued, at `V2-P4-084`: 1,014 instants from 2025-06 to 2027-07 at
    # eighteen-hour steps against four year sets, over a store whose registry carries a lifecycle
    # year above the one the read asks for -- which is what makes the `snapshot_date` clamp bite,
    # and therefore the only shape on which the first horizon has anything to refuse. 962 of the
    # 4,056 pairs reached this line and 0 raised; the rest were refused earlier by the two
    # guarded reads.
    # `tests/integration/test_unlabelled_corpus_faces.py::
    # test_the_session_this_face_prices_is_always_one_the_loaded_registry_can_answer_for` pins it
    # against real reads, so a later change to either one fails there rather than exiting 5 at a
    # user.
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

    **All three reads carry `_unbuilt_factor_remedy` and the neutralised one is unreachable from
    any face** -- `run_shortlist`'s first statement refuses `tier == "neutralized"` outright,
    because this face loads no industry and market-cap cross section. It is written anyway, for
    `_declared_transform`'s stated reason: a `ShortlistRunRequest` is a frozen dataclass and is
    still constructible directly, so the read states its own remedy rather than inheriting one
    from a resolver two calls away. It is *not* claimed to be covered;
    `tests/integration/test_shortlist_request_time_identity.py::
    test_the_neutralized_tier_is_refused_by_this_face_before_any_partition_is_opened` pins the
    guard that makes it unreachable, so lifting that guard fails there rather than shipping an
    untested arm.
    """
    tier = request.tier
    if tier == "raw":
        raw = _read(
            lambda: load_factor_observations(
                store, definition, years=request.years, as_of=request.as_of
            ),
            store=store,
            what=f"the raw {definition.qualified_key} observations",
            remedy=_unbuilt_factor_remedy(store, definition=definition, tier="raw"),
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
            remedy=_unbuilt_factor_remedy(store, definition=definition, tier="processed"),
        )
        return tuple((row.subject, row.value, row.coverage, row.as_of) for row in processed)
    neutralization = _declared_neutralization(request)
    residuals = _read(
        lambda: load_neutralized_factor_observations(
            store, definition, neutralization, years=request.years, as_of=request.as_of
        ),
        store=store,
        what=f"the {neutralization.qualified_key} residuals of {definition.qualified_key}",
        remedy=_unbuilt_factor_remedy(store, definition=definition, tier="neutralized"),
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
    """The session stage two prices against: the newest one that had **published** at `instant`.

    Two clocks and not one, which is the whole of `V2-P4-077`. `previous_trading_day` when the
    instant lands on a non-session -- a build stamped on a Saturday is about the Friday close --
    goes through the stored calendar rather than a weekday rule, because a Chinese public holiday
    is not a weekend and a screen that priced through one would offer buys on a day the exchange
    was shut. That was the only clock this function read, and it is the wrong one on its own.

    ## What the calendar-day rule cost, measured

    A calendar day turns over at midnight and a session's bars publish at 16:30, so a cross
    section stamped 00:30 on a Friday sat *inside* Friday and could see nothing of it. This
    function returned Friday; the values had been computed from Thursday's close; the price plane
    refused the read as a look-ahead, correctly. Because the instant is stored **on the cross
    section**, that refusal was permanent -- `V2-P4-077` swept every `as_of` from before the build
    to days after it and every one exited `1`, half of them with "no cross section visible yet"
    and the rest with `daily cannot be read for 2026-01-16 ... that session had not published
    yet`. There was no gap between the two, and no later question that repaired it.

    Nor was the overnight rollover the shape of it. The window ran from midnight Asia/Shanghai to
    that session's own 16:30, so a build at 09:00 in Shanghai -- before the market opens, on an
    ordinary working morning -- produced an equally unscreenable cross section.

    `panel_ingest.newest_published_session` reads both clocks: `DAILY_AVAILABILITY_TIME` for when
    a session becomes knowable, then the calendar for which day that lands on. It is **never
    later** than the rule it replaces and differs from it only where that rule named a session
    that had not published -- so every instant that already answered answers identically, and the
    ones that could not answer at all now price the session their values were computed from. Both
    halves are computed over a year at half-hourly steps rather than argued: 16,735 instants, 0
    later, 8,518 identical, 8,217 different and 0 of those on a session the old rule could have
    been answered for. Those 8,217 are just under **half of every instant in the year**, which is
    the measurement that makes this an ordinary-use defect rather than an overnight one.

    **Nothing was relaxed to do it.** `_read_visible_price_session` still refuses a session past
    `_sessions_published_through`, and it is literally the same function this resolver asks, so
    the two cannot come apart. What changed is that this face stopped asking a question whose
    only honest answer was no; `panel doctor --session` is where that answer is still reachable.
    """
    try:
        return newest_published_session(
            calendar, as_of=instant, date_timezone=SHORTLIST_DATE_ZONE.key
        )
    except TradingCalendarError as error:
        day = instant.astimezone(SHORTLIST_DATE_ZONE).date()
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
        dataset=DAILY_DATASET,
    )
    limits = _read(
        lambda: load_price_limits(
            store, day=session, calendar=calendar, as_of=as_of, max_staleness=None
        ),
        store=store,
        what=f"the published limit bands for {session.isoformat()}",
        dataset=PRICE_LIMIT_DATASET,
    )
    halts = halt_corpus_for_years(
        _read(
            lambda: load_suspensions(store, years=request.years, as_of=as_of, max_staleness=None),
            store=store,
            what="the halt corpus",
            dataset=SUSPENSION_DATASET,
        ),
        years=request.years,
    )
    names = _read(
        lambda: load_name_histories(store, years=request.years, as_of=as_of, max_staleness=None),
        store=store,
        what="the name histories",
        dataset=NAMECHANGE_DATASET,
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
            is_st=_risk_warned_on(
                history, subject=subject, session=session, store=store, years=request.years
            ),
            **published_limit_fields(limit),
        )
    return priced


def _risk_warned_on(
    history: NameHistory | None,
    *,
    subject: str,
    session: date,
    store: PanelStore,
    years: Sequence[int],
) -> bool:
    """`MarketBar.is_st` for one security on one session, or a refusal naming both.

    `factor_view._risk_warned_on` restated, `_PANEL_FAULTS`' arrangement and its reason: which
    refusals are facts about stored data rather than defects in the code that read them is one
    question with one answer, and two faces that answered it differently would put the same corpus
    under two status codes on two channels. `tests/unit/test_shortlist_view.py::
    test_both_faces_refuse_an_unnamed_session_with_the_same_sentence` drives the two seams with one
    history rather than comparing two constants, which is the pin `V2-P4-070` showed a constant
    cannot be.

    ## Why this raises rather than defaulting, and `V2-P4-080` is why it raises *here*

    `NameHistory.record_on` refuses a day before its first record on purpose -- "an unrecorded name
    is unknown rather than equal to the earliest one on file" -- and `NameHistoryHorizonError`'s
    own docstring says that refusal is a verdict rather than a caller mistake. Until `V2-P4-080`
    the call sat bare inside `MarketBar(...)`, outside every `_read` guard, so `_PANEL_FAULTS`
    never saw it and one ordinary two-clock rename reached a user as `exit 5` on the CLI and `500
    text/plain` from the route -- with the sentence naming the security withheld, on the correct
    grounds that an unanticipated frame can be holding the credential.

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

    **`history is None` is deliberately still `False`, and it is a different state.** A security
    with no row in the announcement years read has had no rename announced in them, which is the
    ordinary condition of most of the market; a security whose earliest row takes effect *after*
    the session has had one, and the name it traded under before it is outside the corpus. The
    residue that leaves is disclosed as
    `a_name_never_announced_inside_the_requested_years_is_screened_as_ordinary`.
    """
    if history is None:
        return False
    try:
        return history.risk_warning_on(session) is not RiskWarning.none
    except NameHistoryHorizonError as error:
        raise ShortlistPanelUnreadableError(
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

    `factor_view._unnamed_session_refusal` restated word for word, and the restatement is a choice
    with a cost. `V2-P4-070`'s own lesson is that "two sites that duplicate the `what=` string and
    the fault list between them is how one of them comes to catch a refusal the other lets escape"
    -- which is why `_read_registry` exists *within* each module. Across the two modules there is
    no shared home that is not a new import edge between two faces, so the duplication stays and
    is held by an executable pin instead: `tests/unit/test_shortlist_view.py::
    test_both_faces_refuse_an_unnamed_session_with_the_same_sentence` drives both seams with one
    history and requires the two messages to be the same string, which is the form
    `V2-P4-070` proved a constant-to-constant assertion is not.

    `where` is the store's own location on the message carried as the exception's own text and
    `PANEL_STORE_PLACEHOLDER` on `disclosable`, which is `_read`'s arrangement and its reason: a
    message that stays inside the process that owns the store may name it, while one that may cross
    a boundary would hand that path to whoever could reach the port. Which of the two a given face
    prints is that face's decision, and they differ: `cli._shortlist_fail` prints `disclosable` on
    this face while `cli._factor_fail` prints `str(error)` on the other. This function makes both
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
    unresolvable_evidence: tuple[str, ...]
    """Subjects whose supplied `run_manifest_id` names no run this deployment holds, ascending.

    `V2-P4-049`. These names are **not** researched: their evidence is dropped before
    `rank_candidates` sees it, so each falls into `CandidateRanking.unresearched` and leaves
    `researched_ratio` exactly where a name with no evidence at all would leave it. That is the
    whole of the issue -- a well-formed address that resolves to nothing used to count.

    Carried beside `unused_evidence` rather than merged into it for that field's own reason: the
    two have different remedies. An answer about a name the cut did not reach is *correct evidence
    about a different question*; an answer pointing at a run nobody made is a provenance claim this
    deployment cannot stand behind, and the remedy is to run the research rather than to re-cut the
    list. Merging them would have made "you researched more names than I shortlisted" and "your
    provenance does not resolve" one line in a report.
    """
    unfinished_evidence: tuple[str, ...]
    """Subjects whose supplied `run_manifest_id` names a run this deployment holds and that did
    not finish, ascending.

    `V2-P4-075`. These names are **not** researched, exactly as `unresolvable_evidence`'s are and
    by the same arithmetic: the evidence is dropped before `rank_candidates` sees it, so each
    falls into `CandidateRanking.unresearched` and leaves `researched_ratio` where a name with no
    evidence at all leaves it. What is different is the *reason*, and it is carried separately for
    `unresolvable_evidence`'s own stated rule -- a run nobody made and a run that broke have
    different remedies, run the research or find out why the run broke -- and because a single
    bucket would have to describe a held run as one this deployment holds no run for.

    `FINISHED_RUN_STATUSES` is the vocabulary. `pending` and `running` land here too, which is the
    same statement about a run that has not got there yet.
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
    runs: ResearchRunStore,
    shortlists: ShortlistDocumentStore,
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

    **Nothing here runs the evidence plane, and it does resolve one.** See
    `the_evidence_plane_is_supplied_rather_than_run_by_this_module` for why `run_cycle` is not
    called, and `a_resolved_run_manifest_is_not_a_resolved_signal` for how far the resolution
    goes. `V2-P4-049`: evidence whose `run_manifest_id` names no stored run is dropped **before**
    `rank_candidates` sees it, so the name it was filed under is `unresearched` and contributes to
    `researched_ratio` exactly as a name with no evidence does. Dropped rather than refused,
    because a caller looping over a year of `as_of`s has to be able to keep going past it -- the
    reason `CandidateRankingError` is not what an unresearched name raises either -- and reported
    on `unresolvable_evidence` rather than silently, which is `unused_evidence`'s own measured
    lesson.

    **The answer is stored, here and not at each face**, which is `run_factor_experiment`'s rule:
    three write paths are three chances to write a different document under one key. It is the
    last thing that happens, so a run that could not be gated stores nothing, and the key is the
    answer's own `shortlist_id` so a re-run that reproduces an answer is a no-op rather than a
    second copy.
    """
    if request.tier == "neutralized":
        raise ShortlistRequestError(
            "a neutralized-tier shortlist needs the industry and market-cap cross section its "
            "scores were neutralised against, and this face does not load one: a shortlist "
            "request carries no membership years, no trading calendar and no neutralisation, and "
            "those three are what decide which exposures a cross section holds. The instant is no "
            "longer the obstacle -- V2-P4-028 made that loader day-scoped -- so what is missing "
            "here is a request contract rather than a readable partition. Screen on the raw or "
            "processed tier, where nothing was projected out"
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

    _refuse_a_component_the_panel_never_valued(request, section=section, funnel=funnel)

    shortlisted = {entry.subject for entry in funnel.shortlist}
    stored = stored_run_manifest_ids(runs)
    unresolvable = tuple(
        sorted(
            subject
            for subject, item in request.evidence.items()
            if item.run_manifest_id not in stored.held
        )
    )
    unfinished = tuple(
        sorted(
            subject
            for subject, item in request.evidence.items()
            if item.run_manifest_id in stored.held and item.run_manifest_id not in stored.finished
        )
    )
    supplied = {
        subject: item
        for subject, item in request.evidence.items()
        if item.run_manifest_id in stored.finished
    }
    joined = {subject: item for subject, item in supplied.items() if subject in shortlisted}
    unused = tuple(sorted(set(supplied) - shortlisted))
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

    result = ShortlistRunResult(
        request=request,
        unused_evidence=unused,
        unresolvable_evidence=unresolvable,
        unfinished_evidence=unfinished,
        cross_section_as_of=section.as_of,
        pricing_session=section.session,
        universe=section.universe,
        components=section.components,
        funnel=funnel,
        ranking=ranking,
        clearance=clearance,
    )
    answer = shortlist_view(result)
    shortlists.put(shortlist_id=str(answer["shortlist_id"]), payload=shortlist_document(answer))
    return result


def _stored_coverage(component: ComponentCrossSection) -> dict[str, int]:
    """How many of one component's stored rows carry each coverage code. Sums to `row_count`.

    **Not narrowed to the registry**, and that is the correction a surviving mutant forced rather
    than the first thing written. The first version filtered on the universe, on the reasoning that
    `CrossSectionScreen._read_components` does; deleting that filter turned nothing red, and the
    reason it could not is the reason it was wrong. `row_count` beside it is
    `len(component.values)`, unfiltered -- so a filtered breakdown would not have summed to the
    total it sits next to, and two adjacent numbers in one object would have been counting two
    different populations with nothing saying so.

    It is also the duplicated rule `_component_cross_section` already refused once, for the same
    reason: the registry filter lives in the screen, and a second copy here is a second place it
    can disagree. What the caller gets is the stored cross section **as it was found**, broken
    down; `admitted_count` beside it is the funnel's own count of how much of it the screen used,
    and the gap between the two is the reading, not an inconsistency.
    """
    counted: dict[str, int] = {}
    for _subject, _value, coverage in component.values:
        counted[coverage] = counted.get(coverage, 0) + 1
    return dict(sorted(counted.items()))


def _refuse_a_component_the_panel_never_valued(
    request: ShortlistRunRequest,
    *,
    section: ShortlistCrossSection,
    funnel: CrossSectionFunnel,
) -> None:
    """`V2-P4-044`: a component with no admitted value at all is a refusal, not a verdict.

    ## What the caller was told before this existed

    The **declared** configuration -- `compute_factor -> apply_factor_transform(
    cross_section_standard/v1) -> write_processed_factor_panels` -- over the shipped eight-security
    panel, asked through `TestClient`, answered `409` whose only block was
    `researched_ratio_not_measurable`: a bar on the *evidence plane*, whose implied remedy is to
    go and research names that do not exist. The stored rows all carried
    `insufficient_cross_section` and the census recorded `('not_valued', 8)`; neither that code nor
    `min_cross_section` appeared anywhere in the answer, and the real remedy -- screen a wider
    market, or declare a transform with a lower floor -- was stated nowhere.

    ## Why this is `blocked` rather than a richer verdict

    `ShortlistRunBlockedError` already owns exactly this territory: "the stored rows are not a
    cross section a screen can read ... every one is a conflict with the current state of the
    **panel** rather than a malformed question, and every one has a build as its remedy." A
    transform that declined the whole cross section wrote no value for anybody, so there is nothing
    to gate and the gate's own sentence is true but unactionable. The measurement a verdict body
    would have carried is all zeros here, so nothing is lost by refusing.

    **This is also what stops `researched_ratio_not_measurable` serving this cause.** That code is
    raised for every one of the five `FunnelCoverage`s that shortlists nobody, and it stays right
    for the four that are genuinely about a cut. This run never reaches it.

    ## Why `admitted_count` and not a rule of this module's own

    Read off `ScoreCensus.components`, which `CrossSectionScreen` computed with the tier tables
    `TIER_VALUE_CODES` and `TIER_ADMITTED_CODES`. Re-deciding "which stored codes carry a value"
    here would be that table's third copy, and the one cell the three tiers differ in (`processed`'s
    `imputed`) is exactly where a second copy would drift.

    **The trigger is one empty component and not an empty shortlist**, which is the distinction
    that keeps this narrow. A composite needs an admitted value on *every* declared component, so
    one empty component means nobody can be scored whatever the others hold. Two components that
    each valued somebody and share no subject is a different finding -- both factors did produce a
    cross section -- and it stays a verdict, readable through the `excluded_by_coverage` census
    `shortlist_view` now renders.
    """
    censuses = {census.factor_id: census for census in funnel.scores.components}
    sections = {component.factor_id: component for component in section.components}
    for declared in request.spec.components:
        factor_id = declared.definition.factor_id
        census = censuses.get(factor_id)
        if census is None or census.admitted_count > 0:
            continue
        codes = _stored_coverage(sections[factor_id])
        raise ShortlistRunBlockedError(
            f"the stored {request.tier} cross section of {declared.definition.qualified_key} at "
            f"{section.as_of.isoformat()} admits no value this screen can order: its "
            f"{sum(codes.values())} stored rows are coded {codes}, and the registry lists "
            f"{len(section.universe)} securities on that session. "
            f"{_why_nothing_was_valued(request, definition=declared.definition, codes=codes)}"
        )


def _why_nothing_was_valued(
    request: ShortlistRunRequest, *, definition: FactorDefinition, codes: Mapping[str, int]
) -> str:
    """The sentence that says what to do about an unvalued component, per stored code.

    Separate from the refusal above so the remedy is chosen by the code the *panel* wrote rather
    than by the tier that was asked for: `insufficient_cross_section` is
    `apply_factor_transform`'s whole-panel refusal and its remedy is the declared floor, while a
    partition of per-security misses is a factor-build question and has a different one.

    `definition` is the component that actually came back empty and not `request.definitions[0]`,
    which is the same thing only for a single-factor composite -- and the rebuild command this
    prints would otherwise name whichever factor happened to be declared first.
    """
    transform = request.transform
    if "insufficient_cross_section" in codes and transform is not None:
        floor = transform.min_cross_section
        return (
            "`insufficient_cross_section` is the whole-panel refusal apply_factor_transform "
            "writes when a session's cross section is thinner than the declared "
            f"min_cross_section: {transform.qualified_key} declares min_cross_section={floor} and "
            f"this session offered {codes['insufficient_cross_section']} -- so the transform "
            "standardized nobody and stored that code for every security, which is a fact about "
            f"the market's width rather than about any one name. Screen a market of at least "
            f"{floor} securities, declare a transform whose min_cross_section this market clears, "
            "or screen on the raw tier, which applies no cross-sectional statistic and therefore "
            "has no floor"
        )
    return (
        "Every stored row carries a code this tier does not admit a value from, so stage one had "
        f"nothing to order. `openalpha factor build --factor {definition.qualified_key} --tier "
        f"{request.tier} --year <year>` is what rebuilds it; `openalpha factor list` says what "
        "each code means"
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
    do about **that bar**; `measurement` carries the numbers the verdict was read against on
    **both** verdicts, because a list that scraped over a bar and one that sailed over it are
    different facts.

    ## `declaration`, and why `tier` alone was not a content address (`V2-P4-050`)

    The answer recorded `tier` and nothing else about the question it answered -- not the
    transform, the neutralisation, the exchange, the years or the components. On the processed
    tier the transform is what *chose the numbers*, and `CandidateRankingManifest.scoring_policy`
    is a `ShortlistSpec`, which carries none either: so the transform that produced a published
    shortlist was in neither content address, and two runs of one factor under two transforms were
    indistinguishable after the fact. `declaration` is the whole resolved question, rendered once.

    **`declaration.neutralization` is always `null` here and no test pins it otherwise, which is a
    fact about this face rather than a gap.** `run_shortlist` refuses `tier == "neutralized"` by
    name before anything is read, so a request that carries a neutralisation never reaches a
    rendering -- and on the other two tiers `_resolve_neutralization` now refuses one outright.
    Hardcoding this key to `None` is therefore an equivalent mutant, and it is written out rather
    than left as a hole for a later reader to close with an assertion that cannot be made. The key
    is rendered anyway so the envelope does not change shape on the day that limitation lifts.

    ## `excluded_by_coverage` and `stored_coverage` (`V2-P4-044`)

    A body that printed `row_count: 8` beside `scored_count: 0` said that six of the eight names
    went missing between two lines and never said where. `ScoreCensus.excluded_by_coverage` is the
    funnel's own answer -- `incomplete_components`, `not_admissible`, `not_valued` -- and each
    component now reports the `admitted_count` the tier tables gave it beside the panel's own
    `stored_coverage` codes. Together they separate "these rows carried no value this tier admits"
    from "these components valued different securities", which have different remedies and were
    one observation.

    The emptiest case of all does not reach here at all: a component whose cross section admits
    nothing is refused by `_refuse_a_component_the_panel_never_valued` before the gate runs, so
    the caller is told about `insufficient_cross_section` rather than about a researched ratio.

    ## `refused_by_verdict`, `rejection_reasons` and `untradeable` (`V2-P4-066`)

    `excluded_by_coverage` explains stage one and stops there, so `scored -> tradeable` was the
    subtraction with nothing beside it: a whole-market acceptance read `5542 scored -> 5533
    tradeable` and `tradable=0.9978`, and the words `halted`, `below_board_minimum` and `up_limit`
    were nowhere in the body. `--min-tradable-ratio` gates precisely that ratio, so a list could
    be refused for a market fact its reader had no way to look at.

    All three come off `TradeabilityCensus`, which has carried the first two since `V2-P4-005` and
    reached no face; `refused` is the names, added by this row. The four cells are always all four
    -- `ScoreCensus.excluded_by_coverage`'s rule, one tier down -- and `rejection_reasons` carries
    the execution policy's own strings rather than a re-derivation.

    **`untradeable` is bounded and `untradeable_not_named` is the residual.** See
    `MAX_NAMED_UNTRADEABLE` for the length and why a rendering has one where the record does not.
    The two count objects are exact at any market size, because they are keyed by a vocabulary.

    ## `shortlist_id`, the address that made the other three retrievable (`V2-P4-062`)

    The body carried three content addresses and there was nothing to address: no store held a
    shortlist and no route served one. None of the three could be the key -- `ranking_manifest_id`
    names the question, `gate_manifest_id` names the question and the bars but not the supplied
    evidence, and `ranking_content_digest` names the researched candidates only, so two unrelated
    shortlists with no evidence at all share it. `shortlist_id` is `stable_answer_digest` over
    everything above it, and it is computed **last**, over the finished body, so a key added to
    this rendering moves it without anyone having to remember to.

    This reads `admitted_or_none` and `is_blocked` and never `bool()`, `len()` or iteration, all
    three of which raise on a `ShortlistClearance` *even when it cleared*.
    """
    clearance = result.clearance
    measurement = clearance.measurement
    admitted = clearance.admitted_or_none
    request = result.request
    admitted_by_component = {
        census.factor_id: census.admitted_count for census in result.funnel.scores.components
    }
    answer: dict[str, object] = {
        "schema_version": SHORTLIST_VIEW_SCHEMA_VERSION,
        "is_blocked": clearance.is_blocked,
        "gate_manifest_id": clearance.manifest.gate_manifest_id,
        "ranking_manifest_id": result.ranking.manifest.ranking_manifest_id,
        "ranking_content_digest": clearance.ranking_content_digest,
        "as_of": request.as_of.isoformat(),
        "horizon": request.horizon,
        "tier": request.tier,
        "declaration": {
            "tier": request.tier,
            "transform": None if request.transform is None else request.transform.qualified_key,
            "neutralization": (
                None if request.neutralization is None else request.neutralization.qualified_key
            ),
            "exchange": request.exchange,
            "years": list(request.years),
            "components": [
                {
                    "factor_id": component.definition.factor_id,
                    "factor": component.definition.qualified_key,
                    "weight": component.weight,
                }
                for component in request.spec.components
            ],
        },
        "cross_section": {
            "as_of": result.cross_section_as_of.isoformat(),
            "pricing_session": result.pricing_session.isoformat(),
            "universe_count": len(result.universe),
            "components": [
                {
                    "factor_id": component.factor_id,
                    "row_count": len(component.values),
                    "clipped_count": len(component.clipped_subjects),
                    "admitted_count": admitted_by_component.get(component.factor_id, 0),
                    "stored_coverage": _stored_coverage(component),
                }
                for component in result.components
            ],
        },
        "funnel": {
            "coverage": result.funnel.coverage,
            "scored_count": result.funnel.scores.scored_count,
            "excluded_by_coverage": dict(result.funnel.scores.excluded_by_coverage),
            "tradeable_count": result.funnel.tradeability.tradeable_count,
            "refused_by_verdict": dict(result.funnel.tradeability.refused_by_verdict),
            "rejection_reasons": dict(result.funnel.tradeability.rejection_reasons),
            "untradeable": [
                {"subject": item.subject, "verdict": item.verdict, "reason": item.reason}
                for item in named_untradeable(result.funnel.tradeability)[0]
            ],
            "untradeable_not_named": named_untradeable(result.funnel.tradeability)[1],
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
        "evidence_from_an_unfinished_run": list(result.unfinished_evidence),
        "evidence_without_a_stored_run": list(result.unresolvable_evidence),
    }
    return {**answer, "shortlist_id": stable_answer_digest(answer)}


def shortlist_document(answer: Mapping[str, object]) -> str:
    """One rendered answer as the bytes a `ShortlistDocumentStore` holds.

    An envelope around the answer rather than the answer itself, so the document says what it is
    and what it is filed under without either fact having to be recovered from a filename --
    `FileShortlistStore` names the file after the key and carries no second component, which is
    only safe because the key is inside the bytes as well.

    The canonicalisation is `stable_answer_digest`'s, which is `stable_model_id`'s, for that
    function's stated reason: a second spelling of "canonical" is a second thing that can disagree
    about one. `open_shortlist` re-derives the digest from `answer` and compares, so a payload
    edited on disk does not merely differ from the original -- it does not open.
    """
    return json.dumps(
        {
            "schema_version": SHORTLIST_DOCUMENT_SCHEMA_VERSION,
            "shortlist_id": answer["shortlist_id"],
            "answer": answer,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def open_shortlist(payload: str) -> dict[str, object]:
    """Reopen a stored document, refusing one whose answer no longer hashes to its own key.

    `open_experiment`'s boundary, one plane over: the point at which immutability stops being a
    property of an object in one process and becomes an enforcement across two. The document
    carries the answer and the address it was filed under; this recomputes the address from the
    answer and refuses the pair if they disagree, so a document with one number edited does not
    parse rather than parsing into a shortlist somebody would read names off.

    What the seal is and is not is `factor_view`'s
    `the_seal_detects_an_edit_and_does_not_authenticate_one`, unchanged: it is integrity against a
    partial write and against a clobber, and never against an author who recomputed it.
    """
    try:
        document = json.loads(payload)
    except ValueError as error:
        raise ShortlistNotHeldError(f"a held shortlist document is not JSON: {error}") from error
    if not isinstance(document, Mapping) or not isinstance(document.get("answer"), Mapping):
        raise ShortlistNotHeldError(
            "a held shortlist document is an object carrying an `answer` object; this one is "
            f"{type(document).__name__}"
        )
    answer = dict(document["answer"])
    claimed = document.get("shortlist_id")
    stated = answer.get("shortlist_id")
    recomputed = stable_answer_digest(
        {key: value for key, value in answer.items() if key != "shortlist_id"}
    )
    if claimed != recomputed or stated != recomputed:
        raise ShortlistNotHeldError(
            f"a held shortlist document is filed under {claimed!r}, states {stated!r} and its "
            f"answer hashes to {recomputed!r}; a content address that does not describe the "
            "answer beside it is the one thing an address exists to make impossible"
        )
    return answer


def held_shortlist(shortlists: ShortlistDocumentStore, shortlist_id: str) -> dict[str, object]:
    """The answer held under `shortlist_id`, reopened, or `ShortlistNotHeldError`.

    `V2-P4-062`'s read half, shared by `openalpha shortlist get`, `GET
    /api/v1/shortlists/{shortlist_id}` and `OpenAlphaSDK.held_shortlist` so the three cannot come
    to serve three shapes -- `shortlist_view`'s own argument for existing at all.

    A malformed address is `bad_request` and never `not_held`, and the order of the two checks is
    what makes that true: the shape is refused here, before the store is asked, so "that is not an
    address" and "nothing is held under it" are two answers rather than one 404 covering both.
    The shape is stated here rather than imported from `storage/shortlists.py`, which this module
    may not import; `stable_answer_digest` is what defines it and the two are held equal by
    `tests/integration/test_shortlist_workflow.py`.

    **Three things are checked and not two.** `open_shortlist` proves the document is internally
    consistent -- its answer hashes to the address it carries -- and that is not the same as the
    document being the one asked for: a file *renamed* on disk is self-consistent under its old
    address and served under its new one. `FileShortlistStore.put` cannot produce that state,
    because it derives the filename from the payload it is handed; a hand on the filesystem can,
    which is the clobber half of what the seal is for. So the reopened answer's own
    `shortlist_id` is required to equal the key it was fetched by.
    """
    if not _SHORTLIST_ID.fullmatch(shortlist_id):
        raise ShortlistRequestError(
            f"{shortlist_id!r} is not a shortlist address; a stored answer is filed under the "
            "`shortlist_id` its own body carries (`sla_` and 24 lowercase hex characters). Run "
            "the shortlist and read `shortlist_id` off the answer, or list what is held"
        )
    payload = shortlists.get(shortlist_id)
    if payload is None:
        raise ShortlistNotHeldError(
            f"nothing is held under {shortlist_id}; this deployment has never produced that "
            "answer, or its runtime directory is not the one that did"
        )
    answer = open_shortlist(payload)
    if answer.get("shortlist_id") != shortlist_id:
        raise ShortlistNotHeldError(
            f"the document held under {shortlist_id} is a self-consistent answer addressed "
            f"{answer.get('shortlist_id')!r}; the store filed it under a key its own content does "
            "not carry, which is what a renamed document looks like from here"
        )
    return answer


_SHORTLIST_ID: Final[re.Pattern[str]] = re.compile(r"sla_[0-9a-f]{24}")
"""`stable_answer_digest`'s own output, as a shape a retrieval can refuse before touching a store.

`re.fullmatch` and never `re.match` with a trailing `$`, which is `storage/shortlists.py`'s
measured rule: Python's `$` also matches immediately before a final newline, so a `"$"`-anchored
pattern under `.match` accepts a token with a `\\n` on the end -- and this token is about to be
handed to a store that turns it into a filename.
"""


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

`factor_view._PANEL_FAULTS` restated, `SHORTLIST_DATE_ZONE`'s arrangement: which exceptions are
facts about data rather than defects in the code that read them is one question with one answer,
and two faces that answered it differently would put the same broken partition under two
different status codes on two channels.

**The pin is on the reads and not on this tuple, and `V2-P4-070` is why.**
`tests/unit/test_shortlist_view.py::
test_this_face_calls_the_same_panel_faults_unreadable_as_the_factor_face` used to assert
`set(shortlist_view._PANEL_FAULTS) == set(factor_view._PANEL_FAULTS)`, and that assertion was true
on the tree where this face exited `5` and answered `500` on a partition the factor face called
`panel_unreadable`. It was true because `V2-P4-060` widened the factor face at the *read* -- it
added `_REGISTRY_FAULTS` and passed it to `_read_registry` as a `faults=` argument -- and left the
module constant the test compared exactly as it found it. Two constants can be equal while the two
`except` clauses in force are not, so the equality was a statement about a name rather than about
behaviour. It now drives both faces' read seams with each fault type in turn, which is the thing
that diverged.
"""

_REGISTRY_FAULTS: Final[tuple[type[Exception], ...]] = (
    *_PANEL_FAULTS,
    StockUniverseError,
    PanelBatchError,
)
"""The two further refusals the **registry** read can raise, `factor_view._REGISTRY_FAULTS`
restated for the same reason the tuple above is.

`load_stock_universe` is the one read this module makes that can fail with a statement about the
stored registry's *shape* rather than about its partitions: `StockUniverseError` for an orphan
delisting row, a duplicated `ts_code` or a security filed against two exchanges, and
`PanelBatchError` for a lifecycle year the read was told to cover and does not.

Narrower than adding the two to `_PANEL_FAULTS` for `factor_view`'s stated reason, which applies
here unchanged: `_PANEL_FAULTS` also guards every factor-tier read on this face, and a
`PanelBatchError` out of one of those is a defect in this repository's own batch assembly rather
than a verdict about stored data. The read that can raise them is the read that catches them.
"""

_T = TypeVar("_T")


def _read(
    reader: Callable[[], _T],
    *,
    store: PanelStore,
    what: str,
    dataset: str | None = None,
    remedy: str = "",
    faults: tuple[type[Exception], ...] = _PANEL_FAULTS,
) -> _T:
    """Run one panel read, turning its refusal into `ShortlistPanelUnreadableError`.

    The local message names the store and `disclosable` does not, `panel_view.stored_calendar`'s
    arrangement and for its reason: the CLI and the SDK are inside the process that owns the store,
    while a response body hands that path to whoever could reach the port.

    `faults` is every read's answer by default and is widened by exactly one caller; see
    `_REGISTRY_FAULTS` for why the registry's two extra refusals go to the read that raises them
    rather than to all of them.

    `dataset` is a key of `SHORTLIST_PANEL_DATASETS` and is what `_unbuilt_dataset_remedy` needs
    to name a command. It is `None` for the factor-tier reads, because a `panel build` line
    appended to one of those would name the wrong plane; `remedy` is how those carry theirs
    instead.

    **`V2-P4-067`: the sentence this docstring used to carry was false for the case that
    matters.** It said the factor-tier reads "already refuse with `openalpha factor build ...`
    (see `_resolve_instant`)". `_resolve_instant` refuses when the read *succeeds and returns
    nothing*; this function refuses when the read *raises*, and a store with no partition at all
    reaches this one first. Measured through `openalpha shortlist run` against an empty runtime
    directory: `the raw reversal_1d/v1 observations could not be read out of ...:
    ['partition_missing', 'field_missing']` -- no command named anywhere in it. The exemption
    covered the class and left the instance uncovered, which is the shape this repository keeps
    finding.
    """
    try:
        return reader()
    except faults as error:
        if dataset is not None:
            remedy = _unbuilt_dataset_remedy(store, dataset=dataset)
        raise ShortlistPanelUnreadableError(
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

**Restated rather than imported**, `_PANEL_FAULTS`' arrangement and for its reason: this module
may not import `factor_view` (`lint-imports` keeps the two faces siblings), and a table copied
between two modules that cannot see each other is held equal by a test that can see both --
`tests/unit/test_shortlist_view.py::test_both_faces_name_a_tiers_partition_with_the_same_table`.
The three entries are the same three function objects, so the equality is on identity rather
than on spelling.

All three take the definition and nothing else. That is the measured fact that widened
`_unbuilt_factor_remedy` from `raw` to every tier in `V2-P4-067(b)`; see that function.
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
"""What each tier's `factor build` needs beyond `--factor`, `--tier` and `--year` (`V2-P5-048`).

`FACTOR_TIER_DATASETS`' arrangement and its reason exactly: restated rather than imported
because this module may not see `factor_view`, and held equal to that module's copy by
`tests/unit/test_shortlist_view.py::test_both_faces_spell_a_tiers_build_arguments_the_same_way`.
On **spelling** rather than identity, unlike the table above -- these are strings, and two
strings that must be one message are exactly the thing a copy lets drift.

`factor_view.FACTOR_TIER_BUILD_ARGUMENTS` carries the measurement behind the values: the remedy
both faces printed named no `--as-of`, so it exited `2` for every tier before the tier-specific
`--transform`/`--neutralization` refusals were ever reached.
"""


def _unbuilt_factor_remedy(store: PanelStore, *, definition: FactorDefinition, tier: str) -> str:
    """`_unbuilt_dataset_remedy`'s factor-plane twin, on the same boundary and for its reason.

    Fires only when no year of this tier's observation partition is registered at all. A store
    that holds *some* year of it can be short for reasons this function cannot tell apart -- the
    requested year is absent and the read says so itself -- and a refusal that names a command
    which does not help is worse than one that names none (`V2-P4-078`'s finding, restated here
    because the same trap is one line away).

    **It covered `raw` alone until the P4 ninth-wave acceptance measured the reason it gave.**
    The reason was that `neutralized` has "two spellings depending on the declared
    neutralization (`factor_neut_*` and `factor_neutmn_*`)". It has one:
    `panel_neutralization.neutralized_factor_dataset` takes the *definition* and no
    neutralisation at all, `factor_neutmn_*` is the manifest dataset rather than a second name
    for the observations, and `factor_proc_*`/`factor_procmn_*` are the same arrangement one
    plane down. `processed` was excluded by being bundled into that sentence rather than by a
    reason of its own -- the same paragraph conceded it "has one dataset name per definition".
    So all three tiers are named here now, and `factor_view.FACTOR_TIER_DATASETS` is the table
    both faces look the dataset up in, so neither face can drift to a different rule.
    """
    dataset = FACTOR_TIER_DATASETS.get(tier)
    if dataset is None or store.registered_years(dataset(definition)):
        return ""
    return (
        f". No {tier} partition of this factor is registered in this panel at all. Build it "
        f"first: `openalpha factor build --factor {definition.qualified_key} --tier {tier} "
        f"--year <year>{FACTOR_TIER_BUILD_ARGUMENTS[tier]}`"
    )


def _unbuilt_dataset_remedy(store: PanelStore, *, dataset: str) -> str:
    """The `panel build` line for a dataset this panel holds no partition of, or `""`.

    `V2-P4-078`, and `panel_view.NO_CALENDAR_REMEDY` is the bar it is written to: the message is
    the only thing a caller who gets this refusal has to act on, and naming the partition without
    naming the command leaves them to find `PANEL_BUILD_TARGETS` themselves. The target is looked
    up rather than spelled, because `--dataset daily` is refused by name and this is exactly the
    place a hand-written string would say it.

    **It fires on "no partition of this dataset at all" and on nothing else, deliberately.** That
    is the one state in which `panel build` is unambiguously the whole answer, and it is the state
    `V2-P4-078` was found in -- a panel built from five targets, screened by a command that needs
    six. A dataset the store holds *some* year of can be short for reasons this function cannot
    tell apart: a year-partitioned dataset is missing that year and says so itself, while
    `stock_basic` is partitioned by **lifecycle** year, so "the requested year is absent" is its
    ordinary healthy state (`load_stock_universe` reads every lifecycle year below the request)
    and a remedy keyed on it would be wrong far more often than right. A refusal that names a
    command which does not help is worse than one that names none.
    """
    if store.registered_years(dataset):
        return ""
    return (
        f". No {dataset} partition is registered in this panel at all, and this command reads "
        f"it. Build it first: `openalpha panel build --dataset "
        f"{SHORTLIST_PANEL_DATASETS[dataset]} --year <year>`"
    )


def _read_registry(reader: Callable[[], StockUniverse], *, store: PanelStore) -> StockUniverse:
    """The registry read, in one place because that is what keeps `_REGISTRY_FAULTS` in force.

    `factor_view._read_registry`'s arrangement and its measured reason, one face over: two sites
    that duplicate the `what=` string and the fault list between them is how one of them comes to
    catch a refusal the other lets escape. This face makes the read once, and it goes through here
    so that a second site cannot be added that quietly takes the default tuple -- which is the
    shape `V2-P4-070` was, with the second site in another module.
    """
    return _read(
        reader,
        store=store,
        what="the security registry",
        dataset=STOCK_BASIC_DATASET,
        faults=_REGISTRY_FAULTS,
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
