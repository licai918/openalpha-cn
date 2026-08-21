"""The model chain's two public faces: evaluate a declaration, and register today's prediction.

`V2-P4-021`, and it is the issue that makes `V2-P4-010` through `V2-P4-017` reachable. Those
eight issues built an `AlphaModel` protocol, a versioned feature matrix, a walk-forward split
with purge and embargo, two baselines, a content-addressed artifact and a prediction store --
and at `694f822` not one of them had a caller outside `tests/`. The measurement is the P3 phase
acceptance's own root cause, filed once and reproduced twice since:

> Every one of them has a green unit test, because the tests call the library directly. Not one
> of them has a green product path, because no test starts where the user stands.

`V2-P4-033` measured the same thing on the ranking chain -- 159 passing tests across `004`, `005`
and `023` and not one starting at a `CliRunner`, a `TestClient` or an `OpenAlphaSDK` -- and the
first end-to-end run over real data immediately found a defect three green suites had missed. So
this module exists to be *called from a face*, and every test that holds it starts at one.

## Two commands, because they are two questions

**`model evaluate`** asks whether a declaration would have ordered the market. It reads a range of
stored cross sections, labels each one, cuts the panel into walk-forward folds, fits the
declaration once per fold and reports `V2-P4-014`'s five statistics per fold. It stores nothing;
see `an_evaluation_registers_nothing_because_every_record_it_could_write_would_be_unwitnessed`.

**`model daily-run`** produces the prediction Story S32 calls 不可省 and registers it. It fits on
every labelled example whose outcome had already closed at the instant it predicts about, scores
the cross section at that instant, and hands the batch to `FilePredictionStore`, whose own clock
-- not the caller's -- decides the record's `standing`.

The two share a request resolver, a reader and a fault taxonomy, and they share exactly one
statistic: `scored_ratio`, which both faces put a declared floor under. Nothing else is common
because nothing else is the same question.

## Why this module is a fifth top-level research-plane family

`backtest/` is structurally impossible and that is the whole reason this issue exists.
`backtest-no-numeric-stack-or-panel-plane` forbids every module under that package
`openalpha_cn.panel*`, `openalpha_cn.feature_matrix` and every face, on full transitive
reachability and with no exemptions; `backtest-studies-touch-no-store` forbids the three
`PredictionBatch` producers `openalpha_cn.storage`, and `storage-no-upward-deps` forbids the
return edge. `storage/predictions.py`'s docstring states the consequence and leaves the join here
by name: *"nothing can hand this store a batch until a face above both does"*.

It is not `factor_view.py` either, for the reason that module's own boundary with
`shortlist_view.py` was drawn on: `factor_view` answers "what did this factor's ordering
correlate with, over a closed range of prediction days", and it does it **without fitting
anything**. A walk-forward fit is a different object -- it has a training span, a purge, an
artifact per fold and a stored prediction -- and folding it in would have produced one request
contract carrying two disjoint halves.

So it is `model_view.py`, beside `panel_view.py`, `factor_view.py` and `shortlist_view.py`, and
`model_` is the fifth prefix in `tests/unit/test_panel_ingest_import_isolation.py`'s
`RESEARCH_PLANE_PREFIXES`. It went red on arrival exactly as
`test_every_top_level_module_is_a_declared_leaf_or_a_member_of_a_discovered_family` promised a
fifth family would, and this is the remedy that test names first: join the discovered set and
take the two rows, rather than write a sentence excusing a face from them.

## `require_declared_features` finally has a caller, and the shape it needed

`V2-P4-012` built that check and could not call it; `V2-P4-014` was named as its first caller and
**structurally could not be** (`backtest/` may not import `openalpha_cn.feature_matrix`), which
that issue corrected in both modules to "whichever composition first holds a declaration and a
matrix -- `V2-P4-017`'s store or `V2-P4-021`'s faces, whichever arrives first". `017` did not, on
the ground that a store is handed a batch rather than a recipe. This is it.

The shape it needed is `--code-commit`'s, measured by `V2-P4-046`: a declared `feature_version`
that is *omitted* resolves from the recipe this request actually built, and one that is
**supplied** is checked against it and refuses by name when it disagrees. A face that required it
would be asking a caller to type a `feat_` digest nobody can compute by hand; a face that never
accepted one would make `feature_version` decorative, which is the exact state `V2-P4-012` built
the function to end. What the resolved form does *not* prove is written down as
`a_resolved_feature_version_is_not_a_declared_one`.

## Which instants a run is about, and why they are derived rather than typed

A walk-forward is intrinsically over many prediction days, so a face spelling one `--as-of` per
day is a face nobody runs a schedule through. `--start` and `--end` name the first and last
**prediction day** in `MODEL_DATE_ZONE`, and the instants come from
`feature_matrix.stored_cross_section_instants` -- the builds every declared column actually
shares, visible at the reading `--as-of`.

Two rules turn those instants into prediction days, and both are borrowed rather than invented:

- **A day is `instant.astimezone(zone).date()`**, which is `build_label_window`'s own first step
  and `walk_forward._prediction_day_of`'s join key. It is deliberately *not*
  `feature_matrix._session_for`'s pricing session: the pricing session is which market the values
  were computed from, and the prediction day is which question was asked -- `V2-P4-077` is the
  measurement that they are two clocks.
- **One day keeps its newest build.** Two builds on one prediction day are two answers to one
  question, and `labelled_panel` refuses a repeated prediction day outright. Taking the newest is
  what `feature_matrix._resolve_instant` would answer if asked at any instant after it, so this
  narrows the request rather than deciding anything the reader would not.

A day whose newest build resolves to the same *pricing session* as another day's is still refused,
by `build_feature_matrix` and in its own words, and this face envelopes that as `blocked`.

## The labels, and the one clock they are read at

Every panel read in a run happens at the reading `--as-of`, which is at or after `--end`. That is
`factor_view`'s rule (`a_run_is_evaluated_at_one_as_of_and_the_labels_are_read_at_it`) and it is
forced: an outcome is by definition not knowable at the instant it is predicted about, so a
labelling read made at each prediction instant would return nothing on every day. What the two
clocks buy is that the *features* are read at the prediction instant -- `read_visible_at` filters
a build stamped after it, one layer down -- while the *outcomes* are read at the instant somebody
sat down to evaluate. A run whose `--as-of` precedes `--end` is refused rather than answered
short.

## `RunManifest.alpha_model_versions`, filled here

`V2-P4-010` declared the slot and wrote "`V2-P4-016` fills it"; `016` measured that it could not
(*"`run_cycle` 那条路上没有任何 `AlphaModel`"*) and passed it to `017`/`021`; `017` measured that
it could not either, for the same reason one layer down, and left it *"still nobody's"*. It is
this face's, and only `daily-run`'s:

- **`daily-run` is a run.** It has a `mode` the enum already declares for it (`RunMode.daily`,
  whose docstring names this command), an `as_of`, a `code_commit`, a `config_digest`, a
  `random_seed` -- the declaration's own -- and exactly one quantitative artifact it consumed. So
  it writes a `RunManifest` with `alpha_model_versions=(AlphaModelRef(name, artifact_id),)`, which
  is the join `domain/run.py` spells out in its own field docstring.
- **`evaluate` is not.** It fits K artifacts, one per fold, and consumes none of them for a
  decision; a manifest naming K models would be recording a study as a production cycle.
  `an_evaluation_writes_no_run_manifest_because_it_took_no_decision` says so.

The `run_id` is derived from the record's own content address (`daily-<record_id>`) rather than
accepted, so a re-run that reproduces a prediction is a no-op on both stores instead of a
`DuplicateRecordError` on one of them -- `FilePredictionStore.put`'s `unchanged` outcome, given
the same shape on the run side.

## What `standing` is allowed to look like at a surface

`V2-P4-017` is unusually plain about what its three standings prove, and a face is exactly where
that plainness is easiest to lose: a green `forward` badge beside a prediction reads as
third-party attestation, and it is not one. Every rendered record therefore carries
`standing_proves` and `standing_does_not_prove` beside the code, filled from
`PREDICTION_STANDING_MEANINGS`, whose `forward` row states in the answer body that `predicted_at`
is unverifiable and that nothing here defends against whoever owns the disk.
`tests/integration/test_model_interfaces.py::
test_a_forward_standing_is_rendered_with_what_it_does_not_prove` holds it.

## Blocked is not empty, on both faces

`V2-P4-023` made the distinction inside the library by refusing `bool()` on a clearance;
`V2-P4-033` re-made it at a surface in two keys and a status code, after the product acceptance
measured a refused list and a legitimately empty one arriving as the same
`{"items":[],"excluded":[],"reviewed":0}`.

Here the declared bar is `minimum_scored_ratio`, and it is the one `V2-P4-014` built
`FoldEvaluation.scored_ratio` for: *"abstaining on the hard names is otherwise a free way to
win"*. It has no default on either face. Above the floor the answer is `is_blocked: false` with
`admitted` carrying the artifact addresses the run stands behind; below it the answer is
`is_blocked: true` with `admitted: null` and every bar it missed under `blocks`, at exit `1` and
HTTP `409`. The `measurement` body is byte-identical across the two, which is what stops a
fixture making them differ by accident.

**A refused daily run still registers its prediction**, and that is not an inconsistency. Story
S32 is about the prediction being persisted *before the outcome is known*, which is unconditional;
the floor is about whether the answer may be acted on, which is not. `run_shortlist` stores a
blocked shortlist for the same reason.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, tzinfo
from types import MappingProxyType
from typing import ClassVar, Final, Literal, Protocol, TypeVar
from zoneinfo import ZoneInfo

from openalpha_cn.backtest.alpha_baseline import (
    BASELINE_FAMILY,
    CrossSectionalRankModel,
    FoldEvaluation,
    evaluate_walk_forward,
)
from openalpha_cn.backtest.alpha_tree import TREE_FAMILY, BoostedRankTreeModel
from openalpha_cn.backtest.factor_ic import FactorTier
from openalpha_cn.backtest.walk_forward import (
    LabelledCrossSection,
    LabelledPanel,
    PanelSection,
    WalkForwardError,
    labelled_panel,
    walk_forward_folds,
)
from openalpha_cn.domain.adjustment import ADJ_FACTOR_DATASET, AdjustmentError, AdjustmentHistory
from openalpha_cn.domain.alpha_model import (
    AlphaModel,
    AlphaModelDeclaration,
    AlphaModelError,
    FittedAlphaModel,
    PredictionBatch,
    TrainingExample,
    TrainingSet,
)
from openalpha_cn.domain.daily_prices import DAILY_DATASET, DailyBar, PriceDataError
from openalpha_cn.domain.factor import FactorDefinition, FactorError, FactorRegistry
from openalpha_cn.domain.factor_neutralization import (
    FactorNeutralizationRegistry,
    FactorNeutralizationSpec,
)
from openalpha_cn.domain.factor_transform import FactorTransformRegistry, FactorTransformSpec
from openalpha_cn.domain.horizon import (
    COUNTABLE_HORIZON_PATTERN,
    HorizonError,
    ResearchHorizon,
    parse_horizon,
)
from openalpha_cn.domain.labels import (
    HaltCorpus,
    LabelError,
    LabelWindow,
    OutcomeLabel,
    build_label_window,
    halt_corpus_for_years,
    label_outcome,
)
from openalpha_cn.domain.panel_batch import PanelBatchError
from openalpha_cn.domain.prediction_record import PredictionRecord, PredictionStanding
from openalpha_cn.domain.price_limits import PRICE_LIMIT_DATASET, SUSPENSION_DATASET, PriceLimit
from openalpha_cn.domain.run import AlphaModelRef, RunManifest, VersionRef
from openalpha_cn.domain.run_mode import RunMode
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
from openalpha_cn.feature_matrix import (
    FeatureColumn,
    FeatureMatrix,
    FeatureMatrixError,
    FeatureMatrixRequest,
    FeatureMatrixSection,
    FeatureMatrixUnreadableError,
    FeatureMissingPolicy,
    FeatureSpec,
    build_feature_matrix,
    load_feature_cross_section,
    require_declared_features,
    stored_cross_section_instants,
)
from openalpha_cn.panel.catalog import PanelStorageError
from openalpha_cn.panel.store import PanelStore
from openalpha_cn.panel_factors import (
    FACTOR_DEFINITIONS,
    FACTOR_TRANSFORMS,
    FactorEngineError,
)
from openalpha_cn.panel_ingest import (
    load_adjustment_histories,
    load_daily_bars,
    load_price_limits,
    load_stock_universe,
    load_suspensions,
    load_trading_calendar,
)
from openalpha_cn.panel_neutralization import FACTOR_NEUTRALIZATIONS, NeutralizationEngineError
from openalpha_cn.panel_view import PANEL_STORE_PLACEHOLDER, panel_store

__all__ = [
    "KNOWN_MODEL_VIEW_LIMITATIONS",
    "MODEL_DATE_ZONE",
    "MODEL_FAMILIES",
    "MODEL_PANEL_DATASETS",
    "MODEL_VIEW_LIMITATION_CODES",
    "MODEL_VIEW_SCHEMA_VERSION",
    "PREDICTION_STANDING_MEANINGS",
    "AlphaModelFactory",
    "DailyRunRequest",
    "DailyRunResult",
    "ModelEvaluation",
    "ModelNotHeldError",
    "ModelPanelUnreadableError",
    "ModelPredictionStore",
    "ModelRequestError",
    "ModelRunBlockedError",
    "ModelRunRequest",
    "ModelViewError",
    "ModelViewLimitation",
    "PredictionWriteLike",
    "ResearchRunWriter",
    "daily_request",
    "daily_rows",
    "daily_view",
    "evaluate_model",
    "evaluation_rows",
    "evaluation_view",
    "feature_columns",
    "held_prediction",
    "model_evaluation_request",
    "panel_store",
    "prediction_view",
    "run_daily",
    "trainable_at",
]

MODEL_VIEW_SCHEMA_VERSION: Final[str] = "model-view/v1"
"""The version of the envelopes `evaluation_view` and `daily_view` render, carried in the body.

`shortlist_view.SHORTLIST_VIEW_SCHEMA_VERSION`'s reason: the sealed records underneath already
carry their own `schema_version`, and this says which *shape* the three faces agreed to hand out
around them.
"""

MODEL_DATE_ZONE: Final[ZoneInfo] = ZoneInfo("Asia/Shanghai")
"""The zone a prediction instant is resolved into a **prediction day** in.

`factor_view.FACTOR_DATE_ZONE` and `shortlist_view.SHORTLIST_DATE_ZONE` restated rather than
imported, which is those two modules' own arrangement and its reason: an A-share decision day is
a calendar day on the exchange's own clock, and a face that borrowed another plane's constant
would silently follow that plane the day it moved.

It is also the zone every `LabelWindow` in a run is dated in, which `labelled_panel` requires:
that function joins a label to a cross section by `as_of.astimezone(window.zone).date()`, so a
run that built its windows in one zone and its days in another would find no join at all.
"""


class AlphaModelFactory(Protocol):
    """What a row of `MODEL_FAMILIES` is: something that takes a declaration and returns a model.

    A call Protocol rather than `Callable[[AlphaModelDeclaration], AlphaModel]`, because both
    implementations are `kw_only` dataclasses and a positional `Callable` describes neither --
    which is exactly the gap a `# type: ignore` at the call site would paper over. Written out,
    the table's value type is checked against the two classes where the table is declared.
    """

    def __call__(self, *, declaration: AlphaModelDeclaration) -> AlphaModel:
        """Build an unfitted model from this declaration."""


MODEL_FAMILIES: Final[Mapping[str, AlphaModelFactory]] = MappingProxyType(
    {
        BASELINE_FAMILY: CrossSectionalRankModel,
        TREE_FAMILY: BoostedRankTreeModel,
    }
)
"""Every `family` this build can fit, and the implementation that answers to it.

A table rather than an `if`/`elif`, for `SHORTLIST_EXIT`'s reason one plane over: a third
implementation added to `backtest/` with no row here is refused by name at request time -- with
the two legal spellings in the message -- instead of falling through to whichever branch the
chain happened to end on.

Both values are the *unfitted* model's constructor and both take exactly one keyword-only
`declaration`, which is what makes the table's value type honest rather than `Any`. Neither
implementation is imported for its own sake: `evaluate_walk_forward` is typed against the
`AlphaModel` Protocol, so what this table hands it is a structural match and nothing here
depends on which module it came from.
"""

MODEL_PANEL_DATASETS: Final[Mapping[str, str]] = MappingProxyType(
    {
        TRADING_CALENDAR_DATASET: TRADING_CALENDAR_DATASET,
        STOCK_BASIC_DATASET: STOCK_BASIC_DATASET,
        DAILY_DATASET: "price",
        SUSPENSION_DATASET: "price",
        PRICE_LIMIT_DATASET: PRICE_LIMIT_DATASET,
        ADJ_FACTOR_DATASET: ADJ_FACTOR_DATASET,
    }
)
"""Every panel dataset this face reads, mapped to the `panel build` target that writes it.

`shortlist_view.SHORTLIST_PANEL_DATASETS`' arrangement and `V2-P4-078`'s reason: the refusal
message is the only thing a caller who gets one has to act on, and naming the partition without
naming the command leaves them to find `PANEL_BUILD_TARGETS` themselves. Keyed by dataset and
valued by target because the two vocabularies are not the same one -- `panel build --dataset
daily` is refused by name, so a remedy spelling `daily` would name a command that does not run.

**`adj_factor` is here and it is not on the shortlist's list**, which is the difference between
the two faces rather than an oversight on either. A shortlist prices a fill on one session and
never spans two; a label is a *return between two sessions*, so `label_outcome` requires an
`AdjustmentHistory` and `window_return` refuses a series that does not reach the window. A model
face without it can read a panel and label nothing in it.

**`namechange` is deliberately absent, and measured to be.** `shortlist run` needs it because
every `MarketBar` carries `is_st`; nothing on this face builds a `MarketBar`, and `label_outcome`
takes bars, factors, limits, halts and the registry and no name history at all.
"""

_T = TypeVar("_T")


class ModelViewError(RuntimeError):
    """Base for every fault a model face can report before a verdict exists.

    `ShortlistViewError`'s two fields and its reasons: a `reason` each channel looks its own
    envelope up by, so a fault added here with no row in a channel's table raises `KeyError` at
    that channel's boundary rather than being silently mis-enveloped; and a `disclosable` message
    that may cross a process boundary, because the store's filesystem location is configuration of
    this process and a response body echoing it would answer a question about the deployment to
    whoever could reach the port.

    **A run refused by its declared floor is deliberately not one of these.** The floor is this
    pipeline answering, not failing: the measurement, the bar and the artifact addresses are all
    on the result, and the faces envelope it by `is_blocked`. Raising here would have made "this
    model answered about too little of the market" indistinguishable, at a face, from "the panel
    could not be read".
    """

    reason: ClassVar[str] = "model_view_error"

    def __init__(self, message: str, *, disclosable: str | None = None) -> None:
        super().__init__(message)
        self.disclosable: str = message if disclosable is None else disclosable


class ModelRequestError(ModelViewError):
    """The question cannot be put at all, whatever is in the store.

    A factor no registry declares, a family no implementation answers to, a horizon that is not
    countable in sessions, a naive instant, a reading `as_of` before the range it reads, a
    declared `feature_version` that is not the recipe this request builds, a walk-forward schedule
    of no folds. Distinct from `ModelRunBlockedError` because the remedy is to edit the request
    rather than to build anything.
    """

    reason: ClassVar[str] = "bad_request"


class ModelPanelUnreadableError(ModelViewError):
    """A panel partition this run needs cannot be read at the stated `as_of`.

    The exchange calendar, the registry, the price panel, the published bands, the halt corpus,
    the adjustment factors or one of the factor partitions came back blocked. Not a verdict,
    because there is nothing to put one on: these are the inputs a fit would have consumed.
    """

    reason: ClassVar[str] = "panel_unreadable"


class ModelRunBlockedError(ModelViewError):
    """The stored panel cannot answer this question as asked, and the refusal is the answer.

    No declared column has a stored cross section inside the range; two prediction days resolve to
    one session's market; a cross section produced no labelled row; the purge left a fold with
    nothing to train on; a batch could not be scored. Every one is a conflict with the current
    state of the **panel** rather than a malformed question, and the remedy is a build or a wider
    range.

    **This is the row that must not wear a 2xx.** A face that answered `200` with an empty fold
    list here would be the empty success `V2-P1-013` exists to make unavailable, arriving on the
    model plane.
    """

    reason: ClassVar[str] = "blocked"


class ModelNotHeldError(ModelViewError):
    """Nothing is held under the prediction address a caller asked to retrieve, or it will not open.

    A separate row from `bad_request` because the remedy is different in kind: the address is well
    formed and this store has never seen it, which a caller fixes by running the model rather than
    by editing the question. A malformed address is `bad_request` and is refused before the store
    is touched at all, so "we looked and there is nothing" and "that is not an address" stay two
    answers rather than one `404` covering both.

    It also covers a held document that no longer addresses to its own name:
    `FilePredictionStore.get` re-derives the digest from the content, so a payload edited on disk
    is a refusal here rather than a prediction somebody reads numbers off.
    """

    reason: ClassVar[str] = "not_held"


@dataclass(frozen=True, slots=True, kw_only=True)
class ModelViewLimitation:
    """One named boundary on what a model run can be trusted to mean."""

    code: str
    detail: str


KNOWN_MODEL_VIEW_LIMITATIONS: Final[tuple[ModelViewLimitation, ...]] = (
    ModelViewLimitation(
        code="a_resolved_feature_version_is_not_a_declared_one",
        detail=(
            "`feature_version` omitted resolves from the recipe this request built, which is "
            "`--code-commit`'s arrangement and buys the same thing: a caller cannot type a "
            "`feat_` digest by hand, so a face that required one would be unusable and a face "
            "that never accepted one would leave `AlphaModelDeclaration.feature_version` "
            "decorative. What the resolved form proves is only that the artifact records the "
            "recipe it was fitted on. It does **not** prove that anybody intended that recipe: "
            "a caller who mistyped a `--feature` gets a different, self-consistent "
            "`feature_version` rather than a refusal. Supply `--feature-version` to make the "
            "recipe a claim `require_declared_features` checks."
        ),
    ),
    ModelViewLimitation(
        code="an_evaluation_registers_nothing_because_every_record_it_could_write_would_be_unwitnessed",
        detail=(
            "`evaluate_fold` dates every batch `predicted_at = section.as_of` -- a simulated "
            "prediction is made at the instant it simulates -- and that instant is in the past "
            "by construction, so `FilePredictionStore`'s own clock would stamp `recorded_at` "
            "after the outcome had already printed. Every record an evaluation could store "
            "would therefore stand `unwitnessed`: claimed in time, received late. Storing them "
            "would fill the register that Story S32 exists for with rows nobody may cite, and "
            "make the `forward` rows harder to find rather than easier. The alternative -- a "
            "register that holds evaluations as a *denominator*, so that 'how many models were "
            "tried' is a count rather than a recollection, which is what "
            "`domain/prediction_record.py` says a multiple-testing policy needs -- is real and "
            "is not built here: it wants a second store or a standing this issue has no mandate "
            "to add."
        ),
    ),
    ModelViewLimitation(
        code="an_evaluation_writes_no_run_manifest_because_it_took_no_decision",
        detail=(
            "`daily-run` fills `RunManifest.alpha_model_versions`; `evaluate` does not, and the "
            "asymmetry is the point. A daily run consumes exactly one fitted artifact to produce "
            "one answer, which is what a manifest records. An evaluation fits one artifact per "
            "fold and acts on none of them, so a manifest naming K of them would file a study as "
            "a production cycle and put K addresses into a `run_manifest_id` that is supposed to "
            "identify one run's inputs. The evaluation's artifact addresses are on its own "
            "answer instead, one per fold."
        ),
    ),
    ModelViewLimitation(
        code="the_daily_fit_purges_and_does_not_embargo",
        detail=(
            "`trainable_at` keeps every labelled example whose outcome window had closed at or "
            "before the instant the prediction is about, which is `WalkForwardFold.purged`'s "
            "comparison with the deadline supplied rather than derived. It applies no embargo. "
            "An embargo separates a training set from a **test block**, and a daily run has no "
            "test block -- the thing it predicts about has no outcome yet, which is the whole "
            "point of registering it. Widening the gap between the newest training label and the "
            "prediction day is a defensible policy against a label whose window overlaps the "
            "prediction day's own; it is not one this repository can measure the value of, so it "
            "is not offered as a flag nobody could choose a number for. `model evaluate` "
            "declares `--embargo-sessions` because there a block exists to be separated from."
        ),
    ),
    ModelViewLimitation(
        code="a_prediction_day_is_the_instants_own_zone_date_and_not_its_pricing_session",
        detail=(
            "Prediction days come from `instant.astimezone(MODEL_DATE_ZONE).date()`, which is "
            "`build_label_window`'s first step and `walk_forward._prediction_day_of`'s join key. "
            "`feature_matrix._session_for` answers a different question -- which session's market "
            "the values were computed from, under `V2-P4-077`'s 16:30 rule -- and the two come "
            "apart for a build stamped between midnight and the close. The consequence is real "
            "and is refused rather than hidden: two prediction days whose builds price the same "
            "session are refused by `build_feature_matrix`, in its own words, and this face "
            "reports that as `blocked`. What is *not* refused is the reverse -- one prediction "
            "day whose build prices a session two days old -- because a stale build is honest "
            "rather than wrong, and `cross_section_as_of` on every fold says which instant "
            "answered."
        ),
    ),
    ModelViewLimitation(
        code="the_evaluation_reads_its_labels_at_one_as_of_and_that_is_not_a_point_in_time_fit",
        detail=(
            "Every panel read in a run is made at the reading `as_of`, `factor_view`'s "
            "`a_run_is_evaluated_at_one_as_of_and_the_labels_are_read_at_it`. The *features* are "
            "still point-in-time -- each cross section is the stored build visible at its own "
            "prediction instant, filtered by `read_visible_at` one layer down -- but the "
            "corpus's shape is not: which securities the registry lists, which sessions the "
            "calendar holds and which factors the adjustment series carries are all read as they "
            "stand today. A security delisted after the range is absent from the whole "
            "evaluation rather than present in the folds that predate it. `V2-P4-013`'s purge is "
            "what keeps an *outcome* out of a fit; nothing here keeps today's registry out of "
            "yesterday's market."
        ),
    ),
    ModelViewLimitation(
        code="the_scored_ratio_floor_is_a_coverage_bar_and_never_a_quality_one",
        detail=(
            "`minimum_scored_ratio` divides the securities a model put a number on by the "
            "securities it was offered, and refuses the answer below the declared floor. It says "
            "nothing whatever about whether the numbers are any good: a model that scored every "
            "name with noise clears any floor, and one that abstained honestly on half the "
            "market clears none above 0.5. It exists because `FoldEvaluation.scored_ratio` "
            "exists -- `V2-P4-014`'s 'abstaining on the hard names is otherwise a free way to "
            "win' -- so that two models' headline numbers are comparable, and "
            "`KNOWN_SHORTLIST_GATE_LIMITATIONS`' own 'clearing it is a coverage verdict and "
            "never a quality one' transfers here unchanged."
        ),
    ),
    ModelViewLimitation(
        code="no_hyperparameter_is_selected_by_anything_on_this_face",
        detail=(
            "`--hyperparameter` is passed through to the declaration verbatim and reaches the "
            "artifact's address; nothing here searches, tunes or compares. That is "
            "`KNOWN_TREE_LIMITATIONS`' second leakage surface arriving at a surface: a caller "
            "who runs `model evaluate` ten times with ten settings and keeps the best has "
            "performed a model selection on the test blocks, and no record this face writes says "
            "so. `domain/prediction_record.py`'s "
            "`the_retrospective_half_of_decision_12s_third_clause_leaves_no_trace_in_a_record` "
            "is the same statement one plane down."
        ),
    ),
    ModelViewLimitation(
        code="a_neutralized_feature_column_is_refused_by_this_face",
        detail=(
            "A `--feature` on the neutralized tier resolves and then fails to read on any panel "
            "this build can produce inside a covered year: `load_neutralized_factor_observations` "
            "answers only where the residual partition holds a build, and "
            "`openalpha factor build --tier neutralized` refuses every instant before its year's "
            "last stored session (`V2-P4-026`). Rather than let that arrive as an empty column "
            "and a fit on nothing, the tier is refused at request time with the issue that owns "
            "the boundary named. `shortlist_view` refuses the same tier for a different reason "
            "-- it would need an exposure cross section -- and neither refusal is this face's to "
            "lift."
        ),
    ),
)

MODEL_VIEW_LIMITATION_CODES: Final[frozenset[str]] = frozenset(
    item.code for item in KNOWN_MODEL_VIEW_LIMITATIONS
)

PREDICTION_STANDING_MEANINGS: Final[Mapping[PredictionStanding, tuple[str, str]]] = (
    MappingProxyType(
        {
            "forward": (
                "this store held these bytes before the instant the outcome became knowable, "
                "and the batch says it was produced before it too",
                "that the batch was produced when it says it was. predicted_at is whatever the "
                "caller passed to predict and nothing in this repository can check it, and "
                "nothing here defends against whoever owns the disk -- an operator who can edit "
                "predicted_at can edit recorded_at, re-address the file, or set the machine "
                "clock before the write. A claim a third party could check would need a "
                "timestamp somebody else controls, and this repository has none",
            ),
            "unwitnessed": (
                "that the batch claims to have been produced before the outcome became knowable",
                "that it was held in time. This store's own clock read after the deadline, so "
                "the claim is uncorroborated -- which may be a slow disk and may be a backdated "
                "predicted_at, and this record cannot tell you which",
            ),
            "backfill": (
                "that this is a recomputation, stated as one: the batch was produced at or after "
                "the instant its outcome became knowable",
                "anything at all about foresight. Implementation Decision 14's 回溯重算 may not "
                "replace an original, and a backfill naming no earlier record corrects nothing",
            ),
        }
    )
)
"""What each of `V2-P4-017`'s three standings proves, and what it does not, in the answer body.

Two sentences per standing rather than one, because the second is the one a face is most likely
to lose. `domain/prediction_record.py` is unusually plain -- *"`predicted_at` is whatever the
caller passed to `predict`, and nothing in this repository can check it"*, *"none of this defends
against whoever owns the disk"* -- and a rendering that printed `"standing": "forward"` and
stopped would turn a local-first, single-user bookkeeping fact into what reads like an
attestation. The sentences travel *in the body*, not in documentation, because the body is what a
caller pastes into a report.

Keyed by the `PredictionStanding` literal, so a fourth standing added to that contract raises
`KeyError` here rather than rendering with no explanation at all.
"""

_PANEL_FAULTS: Final[tuple[type[Exception], ...]] = (
    PanelStorageError,
    FactorEngineError,
    NeutralizationEngineError,
    TradingCalendarError,
)
"""The refusals a stored panel raises when it cannot answer a read.

`shortlist_view._PANEL_FAULTS` restated for that module's own stated reason: which exceptions are
facts about data rather than defects in the code that read them is one question with one answer,
and two faces that answered it differently would put the same broken partition under two
different status codes on two channels.
"""

_REGISTRY_FAULTS: Final[tuple[type[Exception], ...]] = (
    *_PANEL_FAULTS,
    StockUniverseError,
    PanelBatchError,
)
"""The two further refusals the **registry** read can raise, `shortlist_view._REGISTRY_FAULTS`
restated. `load_stock_universe` is the one read here that can fail with a statement about the
stored registry's *shape* -- an orphan delisting row, a duplicated `ts_code` -- rather than about
its partitions."""

_LABEL_CORPUS_FAULTS: Final[tuple[type[Exception], ...]] = (
    StockUniverseError,
    AdjustmentError,
    PriceDataError,
)
"""The three refusals `label_outcome` raises beside `LabelError`, and `V2-P4-084` is why they are
named here rather than discovered.

That issue measured all three escaping `except LabelError` on the factor face and arriving as
`exit 5` / HTTP `500` with the message withheld -- a verdict about a stored corpus wearing "this
is a defect in the command". `factor_view._LABEL_CORPUS_FAULTS` is the same tuple for the same
seam, and this face reaches the same `label_outcome` through the same three modules.

The three module-level bases, **not** their horizon subclasses: `AdjustmentHorizonError` is an
`AdjustmentError` and `UniverseHorizonError` is a `StockUniverseError`, and both arms of a horizon
are the same fact about the same partition.
"""


class PredictionWriteLike(Protocol):
    """What a write reports back: the **held** record, and whether this call created it.

    Two read-only members rather than an import of `storage.predictions.PredictionWrite`, which
    is the whole point of the pair: this module names no concrete store, so
    `runtime/composition.py` stays the only place in the repository that decides where
    predictions live.

    `record` is the *held* document and not the arriving one -- on `unchanged` it is what was
    already on disk, so a caller that re-offered a prediction reads back the instant this store
    **first** held it. That is `PredictionWrite.record`'s own contract, restated here because a
    Protocol that did not say it would let a future store return the arriving copy and quietly
    move every `standing` a re-run reports.
    """

    @property
    def record(self) -> PredictionRecord:
        """The record now held under the address this write derived."""

    @property
    def outcome(self) -> Literal["created", "unchanged"]:
        """Whether this call wrote the document or found it already filed."""


class ModelPredictionStore(Protocol):
    """What `run_daily` needs of a prediction store, declared beside its consumer.

    **The direction is the opposite of `ShortlistDocumentStore`'s and the reason is measured, not
    stylistic.** That Protocol exists because `shortlist_view` sits *above* `openalpha_cn.storage`
    and the store may not import it, so the contract had to travel as opaque strings.
    `FilePredictionStore` works in typed `PredictionRecord`s -- `domain/` is below it, so it
    imports the contract outright, which is what lets `get` re-derive an address and refuse a
    document that no longer matches it. This module could therefore import
    `FilePredictionStore` directly and does not, for one reason: a face that named a concrete
    store would be the second place this repository decides where predictions live, and
    `runtime/composition.py` is the first. The Protocol is what lets `build_storage` stay the only
    answer to that question.

    `put` is deliberately narrow: no `record_id` parameter, because the address is derived from
    what is stored, and no `recorded_at`, because the store's own clock is the only thing standing
    between a caller who backdates `predicted_at` and a `forward` standing.
    """

    def put(
        self,
        *,
        batch: PredictionBatch,
        calendar: TradingCalendar,
        zone: tzinfo,
        supersedes: str | None = None,
    ) -> PredictionWriteLike:
        """Take custody of one batch and never write where something is already held."""

    def get(self, record_id: str) -> PredictionRecord | None:
        """The record held under `record_id`, or `None`."""

    def list_ids(self) -> tuple[str, ...]:
        """Every held address, ascending."""


class ResearchRunWriter(Protocol):
    """The two methods `run_daily` needs of the run repository, and no more.

    `RunRepository` in `runtime/repository.py` declares exactly what `ResearchEngine` calls, and
    widening it for this face would hand every service-layer consumer a method it has no business
    calling -- `runtime/composition.py`'s own argument for keeping `batch_store` concrete. So this
    is the narrowest Protocol that expresses "write a manifest, and tell me whether one is already
    filed under this id", which is what makes a repeated daily run idempotent instead of a
    `DuplicateRecordError`.
    """

    def append_run(self, manifest: RunManifest) -> None:
        """Append a run manifest without replacing an existing run."""

    def get_run(self, run_id: str) -> RunManifest | None:
        """Load a run manifest by id, or `None` when none is filed."""


# --- the request, resolved once so three faces ask one question ---------------------------------


@dataclass(frozen=True, slots=True, kw_only=True)
class ModelRunRequest:
    """Everything a run declares, resolved: the recipe, the range, the schedule and the floor.

    Built only by `model_evaluation_request` and `daily_request`, which is what makes the three
    faces ask one question -- `FactorRunRequest`'s arrangement and its reason. No field has a
    default here: a default is a decision nobody recorded making, and every one of these moves the
    answer.

    `declaration` carries a `feature_version` that may have been resolved rather than declared;
    `declared_feature_version` records which of the two it was, because
    `a_resolved_feature_version_is_not_a_declared_one` is a boundary a reader has to be able to
    see on the answer rather than infer from the command line.
    """

    declaration: AlphaModelDeclaration
    columns: tuple[FeatureColumn, ...]
    missing: FeatureMissingPolicy
    start: date
    """The first prediction day in the range, inclusive, dated in `MODEL_DATE_ZONE`."""
    end: date
    """The last prediction day in the range, inclusive."""
    as_of: datetime
    """The instant every panel read in this run is made at. At or after `end`."""
    years: tuple[int, ...]
    exchange: str
    horizon: ResearchHorizon
    minimum_scored_ratio: float
    config_digest: str
    declared_feature_version: str | None

    @property
    def feature_ids(self) -> tuple[str, ...]:
        """The recipe's columns, sorted and addressed, as the header every row aligns to."""
        return tuple(sorted(column.feature_id for column in self.columns))


@dataclass(frozen=True, slots=True, kw_only=True)
class EvaluationRequest:
    """A walk-forward schedule laid over one `ModelRunRequest`."""

    run: ModelRunRequest
    folds: int
    test_days_per_fold: int
    embargo_sessions: int


@dataclass(frozen=True, slots=True, kw_only=True)
class DailyRunRequest:
    """One prediction instant laid over one `ModelRunRequest`.

    `predict_at` is the instant the prediction is **about** and is not a clock this face reads:
    `FittedAlphaModel.predict`'s own rule, because a hidden `datetime.now()` would make every
    batch unreproducible. It is separate from `run.as_of`, which is when the labels behind the fit
    were read, and separate again from the wall clock that stamps `predicted_at`.
    """

    run: ModelRunRequest
    predict_at: datetime


def feature_columns(
    declared: Sequence[Mapping[str, object]],
    *,
    registry: FactorRegistry = FACTOR_DEFINITIONS,
    transforms: FactorTransformRegistry = FACTOR_TRANSFORMS,
    neutralizations: FactorNeutralizationRegistry = FACTOR_NEUTRALIZATIONS,
) -> tuple[FeatureColumn, ...]:
    """Resolve `{factor, tier, transform?, neutralization?}` mappings into declared columns.

    `shortlist_view.shortlist_components`' arrangement: the wire shape and the CLI token both land
    here, so a mistyped factor is one refusal with one message on three faces rather than three
    that agree today. The mappings are validated key by key rather than splatted into
    `FeatureColumn`, so a body naming `factors` gets a sentence about which key it should have
    used instead of a `TypeError`.
    """
    if not declared:
        raise ModelRequestError(
            "this model declares no feature; a fit over no column is a model whose parameters "
            "came from nothing. Declare at least one `--feature <factor>@<tier>`; `openalpha "
            "factor list` prints every factor and transform this build knows"
        )
    resolved: list[FeatureColumn] = []
    for entry in declared:
        unknown = sorted(set(entry) - {"factor", "tier", "transform", "neutralization"})
        if unknown:
            raise ModelRequestError(
                f"a declared feature carries {unknown}, which no column has; a column is a "
                "`factor`, a `tier`, and the `transform` and `neutralization` that tier is "
                "narrowed by"
            )
        tier = _resolve_tier(entry.get("tier"))
        if tier == "neutralized":
            raise ModelRequestError(
                "a neutralized-tier feature is refused by this face: the residual partition can "
                "only be built at or after its year's last stored session (V2-P4-026), so a "
                "column declared on it is empty at every instant a walk-forward asks about and "
                "the fit would be on nothing. Declare the raw or the processed tier; see "
                "KNOWN_MODEL_VIEW_LIMITATIONS"
            )
        try:
            resolved.append(
                FeatureColumn(
                    definition=_resolve_factor(entry.get("factor"), registry=registry),
                    tier=tier,
                    transform=_resolve_transform(entry.get("transform"), transforms=transforms),
                    neutralization=_resolve_neutralization(
                        entry.get("neutralization"), neutralizations=neutralizations
                    ),
                )
            )
        except FeatureMatrixError as error:
            raise ModelRequestError(str(error)) from error
    return tuple(resolved)


def _resolve_tier(token: object) -> FactorTier:
    if not isinstance(token, str) or not token.strip():
        raise ModelRequestError(
            "a declared feature names no tier; every column reads one of `raw`, `processed` or "
            "`neutralized`, and which one decides whether a transform is required"
        )
    name = token.strip()
    if name not in {"raw", "processed", "neutralized"}:
        raise ModelRequestError(
            f"{name!r} is not a factor tier; this build stores `raw`, `processed` and `neutralized`"
        )
    return name  # type: ignore[return-value]


def _resolve_factor(token: object, *, registry: FactorRegistry) -> FactorDefinition:
    """The definition `--feature <key>@<tier>` names, by qualified key or by `factor_id`.

    `shortlist_view._resolve_factor`'s two branches and its measured wording: a caller who
    mistyped a key needs the **keys**, not nineteen content addresses, and a caller holding a
    stored observation has only the address.
    """
    if not isinstance(token, str) or not token.strip():
        raise ModelRequestError(
            "a declared feature names no factor; give a qualified key (`reversal_1d/v1`) or a "
            "factor_id (`fct_...`) this build declares. `openalpha factor list` prints every one"
        )
    name = token.strip()
    try:
        return registry.get(name) if "/" in name else registry.by_id(name)
    except FactorError as error:
        raise ModelRequestError(
            f"{name!r} is not a factor this build declares -- the keys are "
            f"{list(registry.qualified_keys)}. `openalpha factor list` prints both spellings, and "
            "`openalpha factor describe --factor <key>` prints what each one says it does not "
            f"measure ({error})"
        ) from error


def _resolve_transform(
    token: object, *, transforms: FactorTransformRegistry
) -> FactorTransformSpec | None:
    if token is None or (isinstance(token, str) and not token.strip()):
        return None
    if not isinstance(token, str):
        raise ModelRequestError(f"a declared transform is {token!r}, which is not a name")
    try:
        return transforms.get(token.strip())
    except FactorError as error:
        raise ModelRequestError(
            f"{token.strip()!r} is not a transform this build declares -- it knows "
            f"{list(transforms.qualified_keys)}. `openalpha factor list` prints each one's floors"
        ) from error


def _resolve_neutralization(
    token: object, *, neutralizations: FactorNeutralizationRegistry
) -> FactorNeutralizationSpec | None:
    if token is None or (isinstance(token, str) and not token.strip()):
        return None
    if not isinstance(token, str):
        raise ModelRequestError(f"a declared neutralization is {token!r}, which is not a name")
    try:
        return neutralizations.get(token.strip())
    except FactorError as error:
        raise ModelRequestError(
            f"{token.strip()!r} is not a neutralization this build declares -- it knows "
            f"{list(neutralizations.qualified_keys)}"
        ) from error


def _model_request(
    *,
    columns: Sequence[FeatureColumn],
    name: str,
    family: str,
    horizon: str,
    seed: int,
    start: date,
    end: date,
    as_of: datetime,
    years: Sequence[int],
    exchange: str,
    minimum_scored_ratio: float,
    code_commit: str,
    config_digest: str,
    feature_version: str | None,
    hyperparameters: Sequence[tuple[str, bool | int | float | str]],
    missing: FeatureMissingPolicy,
) -> ModelRunRequest:
    """The half of a request both faces share, resolved once.

    `require_declared_features` runs **here**, against the recipe these columns address, which is
    the join `V2-P4-012` built it for and the earliest point at which a mismatch is knowable: the
    check needs a `FeatureSpec`, the spec needs the resolved columns, and nothing above this
    function has both before a panel read has already happened.
    """
    if family not in MODEL_FAMILIES:
        raise ModelRequestError(
            f"{family!r} is not a model family this build can fit; it answers to "
            f"{sorted(MODEL_FAMILIES)}. The family is fixed by the implementation that will be "
            "asked to fit the declaration, and is what tells a reader that two differently-named "
            "declarations went through the same code path"
        )
    if end < start:
        raise ModelRequestError(
            f"the range runs from {start.isoformat()} to {end.isoformat()}, which is backwards; "
            "a walk-forward is cut in time order and an empty range is an empty success"
        )
    instant = _aware(as_of, flag="as_of")
    if instant.astimezone(MODEL_DATE_ZONE).date() < end:
        raise ModelRequestError(
            f"this run reads its panel at {instant.isoformat()}, which is "
            f"{instant.astimezone(MODEL_DATE_ZONE).date().isoformat()} in {MODEL_DATE_ZONE} and "
            f"before the last prediction day it asks about ({end.isoformat()}). An outcome is "
            "not knowable at the instant it is predicted about, so the labels behind every fold "
            "are read at one later as_of; a run that read them earlier would find no closed "
            "window at all"
        )
    if not 0.0 <= minimum_scored_ratio <= 1.0:
        raise ModelRequestError(
            f"minimum_scored_ratio {minimum_scored_ratio!r} is outside [0, 1]; it is a floor "
            "under the fraction of the offered market a model answered about, and a fraction "
            "above one refuses every possible answer"
        )
    if not years:
        raise ModelRequestError(
            "this run names no partition year; the factor and price partitions are keyed by year "
            "and a read of none returns nothing to fit on"
        )
    spec = _spec_of(columns, missing=missing)
    try:
        parsed = parse_horizon(horizon)
    except HorizonError as error:
        raise ModelRequestError(
            f"{horizon!r} is not a horizon this repository can count in sessions: {error}. A "
            f"model horizon matches {COUNTABLE_HORIZON_PATTERN}, e.g. `5d`"
        ) from error
    try:
        declaration = AlphaModelDeclaration(
            name=name,
            family=family,
            horizon=parsed.text,
            feature_version=spec.feature_version if feature_version is None else feature_version,
            seed=seed,
            code_commit=code_commit,
            hyperparameters=tuple(hyperparameters),
        )
    except ValueError as error:
        raise ModelRequestError(f"this declaration cannot be put: {error}") from error
    try:
        require_declared_features(declaration, spec)
    except FeatureMatrixError as error:
        raise ModelRequestError(str(error)) from error
    if not re.fullmatch(r"[0-9a-f]{64}", config_digest):
        raise ModelRequestError(
            f"config_digest {config_digest!r} is not a 64-character hex digest; RunManifest "
            "requires one and a daily run files a manifest under it"
        )
    return ModelRunRequest(
        declaration=declaration,
        columns=tuple(columns),
        missing=missing,
        start=start,
        end=end,
        as_of=instant,
        years=tuple(sorted(set(years))),
        exchange=exchange,
        horizon=parsed,
        minimum_scored_ratio=minimum_scored_ratio,
        config_digest=config_digest,
        declared_feature_version=feature_version,
    )


def _spec_of(columns: Sequence[FeatureColumn], *, missing: FeatureMissingPolicy) -> FeatureSpec:
    """The recipe these columns address, or this face's refusal for a column stated twice."""
    try:
        return FeatureMatrixRequest(
            columns=tuple(columns),
            years=(1970,),
            exchange="",
            as_ofs=(datetime.fromtimestamp(0, tz=MODEL_DATE_ZONE),),
            missing=missing,
        ).spec
    except FeatureMatrixError as error:
        raise ModelRequestError(str(error)) from error


def _aware(instant: datetime, *, flag: str) -> datetime:
    if instant.tzinfo is None or instant.utcoffset() is None:
        raise ModelRequestError(
            f"{flag} {instant.isoformat()!r} carries no UTC offset; a point-in-time question "
            "answered in a guessed timezone is wrong by up to a session"
        )
    return instant


def model_evaluation_request(
    *,
    columns: Sequence[FeatureColumn],
    name: str,
    family: str,
    horizon: str,
    seed: int,
    start: date,
    end: date,
    as_of: datetime,
    years: Sequence[int],
    exchange: str,
    folds: int,
    test_days_per_fold: int,
    embargo_sessions: int,
    minimum_scored_ratio: float,
    code_commit: str,
    config_digest: str,
    feature_version: str | None = None,
    hyperparameters: Sequence[tuple[str, bool | int | float | str]] = (),
    missing: FeatureMissingPolicy = "abstain",
) -> EvaluationRequest:
    """One walk-forward evaluation's whole question, resolved and refused before a store is opened.

    The schedule's own three numbers are checked here and again by `walk_forward_folds`, and the
    duplication is one-way rather than accidental: this copy refuses a *request* that cannot be
    put at all (`bad_request`), while that one refuses a *panel* too short for the schedule
    (`blocked`), and the two have different remedies. Only the first is knowable without a read.
    """
    if folds < 1 or test_days_per_fold < 1:
        raise ModelRequestError(
            f"a walk-forward schedule of {folds} fold(s) of {test_days_per_fold} test day(s) "
            "needs at least one of each; a schedule of none is an empty success"
        )
    if embargo_sessions < 0:
        raise ModelRequestError(
            f"an embargo of {embargo_sessions} sessions would widen the training set past the "
            "purge that already cut it"
        )
    return EvaluationRequest(
        run=_model_request(
            columns=columns,
            name=name,
            family=family,
            horizon=horizon,
            seed=seed,
            start=start,
            end=end,
            as_of=as_of,
            years=years,
            exchange=exchange,
            minimum_scored_ratio=minimum_scored_ratio,
            code_commit=code_commit,
            config_digest=config_digest,
            feature_version=feature_version,
            hyperparameters=hyperparameters,
            missing=missing,
        ),
        folds=folds,
        test_days_per_fold=test_days_per_fold,
        embargo_sessions=embargo_sessions,
    )


def daily_request(
    *,
    columns: Sequence[FeatureColumn],
    name: str,
    family: str,
    horizon: str,
    seed: int,
    start: date,
    end: date,
    predict_at: datetime,
    as_of: datetime,
    years: Sequence[int],
    exchange: str,
    minimum_scored_ratio: float,
    code_commit: str,
    config_digest: str,
    feature_version: str | None = None,
    hyperparameters: Sequence[tuple[str, bool | int | float | str]] = (),
    missing: FeatureMissingPolicy = "abstain",
) -> DailyRunRequest:
    """One daily run's whole question: what to fit on, and which instant to predict about."""
    instant = _aware(predict_at, flag="predict_at")
    resolved = _model_request(
        columns=columns,
        name=name,
        family=family,
        horizon=horizon,
        seed=seed,
        start=start,
        end=end,
        as_of=as_of,
        years=years,
        exchange=exchange,
        minimum_scored_ratio=minimum_scored_ratio,
        code_commit=code_commit,
        config_digest=config_digest,
        feature_version=feature_version,
        hyperparameters=hyperparameters,
        missing=missing,
    )
    if instant.astimezone(MODEL_DATE_ZONE).date() <= end:
        raise ModelRequestError(
            f"this run predicts about {instant.isoformat()}, which is "
            f"{instant.astimezone(MODEL_DATE_ZONE).date().isoformat()} in {MODEL_DATE_ZONE} and "
            f"is not after the last training day it names ({end.isoformat()}). A daily run fits "
            "on outcomes that have already closed and predicts about a day that has none, so the "
            "prediction day has to be strictly later than the training range"
        )
    if instant > resolved.as_of:
        raise ModelRequestError(
            f"this run predicts about {instant.isoformat()} and reads its panel at "
            f"{resolved.as_of.isoformat()}, which is earlier; the cross section being predicted "
            "on is read at the reading as_of like everything else, so an instant after it is one "
            "no read can reach"
        )
    return DailyRunRequest(run=resolved, predict_at=instant)


# --- the panel, read once at one as_of ------------------------------------------------------


def _read(
    reader: Callable[[], _T],
    *,
    store: PanelStore,
    what: str,
    dataset: str | None = None,
    faults: tuple[type[Exception], ...] = _PANEL_FAULTS,
) -> _T:
    """Run one panel read, turning its refusal into `ModelPanelUnreadableError`.

    The local message names the store and `disclosable` does not, `shortlist_view._read`'s
    arrangement and its reason: the CLI and the SDK are inside the process that owns the store,
    while a response body hands that path to whoever could reach the port.
    """
    try:
        return reader()
    except faults as error:
        remedy = "" if dataset is None else _unbuilt_dataset_remedy(store, dataset=dataset)
        raise ModelPanelUnreadableError(
            f"{what} could not be read out of {store.root}: {error}{remedy}",
            disclosable=(
                f"{what} could not be read out of {PANEL_STORE_PLACEHOLDER}: "
                f"{_without_store_path(str(error), store)}{remedy}"
            ),
        ) from error


def _unbuilt_dataset_remedy(store: PanelStore, *, dataset: str) -> str:
    """The `panel build` line for a dataset this panel holds no partition of, or `""`.

    `shortlist_view._unbuilt_dataset_remedy` transplanted with its own bound intact: it fires on
    "no partition of this dataset at all" and on nothing else, because that is the one state in
    which `panel build` is unambiguously the whole answer. A dataset the store holds *some* year
    of can be short for reasons this function cannot tell apart, and a refusal that names a
    command which does not help is worse than one that names none.
    """
    if store.registered_years(dataset):
        return ""
    return (
        f". No {dataset} partition is registered in this panel at all, and this command reads "
        f"it. Build it first: `openalpha panel build --dataset "
        f"{MODEL_PANEL_DATASETS[dataset]} --year <year>`"
    )


def _without_store_path(message: str, store: PanelStore) -> str:
    """`message` with the store's own location replaced by a name for it.

    Both spellings, longest first, `panel_view._without_store_path`'s rule and its measured
    reason: `Path.resolve()` differs from the configured path wherever a component is a symlink,
    and replacing the shorter first would leave the longer one's prefix behind.
    """
    for path in sorted({str(store.root), str(store.root.resolve())}, key=len, reverse=True):
        message = message.replace(path, PANEL_STORE_PLACEHOLDER)
    return message


class _LabelInputs:
    """Everything a run reads out of the panel to turn a cross section into training examples.

    A class rather than a bag of locals for `factor_view._PanelInputs`' measured reason: the
    per-session reads are the expensive half, `load_daily_bars` and `load_price_limits` take one
    session per call by contract, and a walk-forward asks for one session's bars once per label
    window it appears in -- which on a `5d` horizon is six times.

    It is a **narrower** object than `factor_view._PanelInputs` rather than an import of it, and
    the narrowing is the point: nothing here builds a `MarketBar`, so no name history is read and
    `namechange` is not on `MODEL_PANEL_DATASETS`. Every read goes through `_read`, so a partition
    this panel does not hold is `panel_unreadable` with the `panel build` line that repairs it.
    """

    def __init__(self, store: PanelStore, request: ModelRunRequest) -> None:
        self._store = store
        self._as_of = request.as_of
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
            dataset=TRADING_CALENDAR_DATASET,
        )
        self.universe: StockUniverse = _read(
            lambda: load_stock_universe(store, years=years, as_of=as_of, max_staleness=None),
            store=store,
            what="the security registry",
            dataset=STOCK_BASIC_DATASET,
            faults=_REGISTRY_FAULTS,
        )
        self.adjustments: Mapping[str, AdjustmentHistory] = _read(
            lambda: load_adjustment_histories(store, years=years, as_of=as_of, max_staleness=None),
            store=store,
            what="the adjustment factors",
            dataset=ADJ_FACTOR_DATASET,
        )
        self.halts: HaltCorpus = halt_corpus_for_years(
            _read(
                lambda: load_suspensions(store, years=years, as_of=as_of, max_staleness=None),
                store=store,
                what="the halt corpus",
                dataset=SUSPENSION_DATASET,
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
                    as_of=self._as_of,
                    max_staleness=None,
                ),
                store=self._store,
                what=f"the price bars for {day.isoformat()}",
                dataset=DAILY_DATASET,
            )
        return self._bars[day]

    def limits_on(self, day: date) -> Mapping[str, PriceLimit]:
        if day not in self._limits:
            self._limits[day] = _read(
                lambda: load_price_limits(
                    self._store,
                    day=day,
                    calendar=self.calendar,
                    as_of=self._as_of,
                    max_staleness=None,
                ),
                store=self._store,
                what=f"the published limit bands for {day.isoformat()}",
                dataset=PRICE_LIMIT_DATASET,
            )
        return self._limits[day]

    def window(self, instant: datetime, *, horizon: ResearchHorizon) -> LabelWindow:
        """The sessions one prediction instant's outcome is measured over.

        `build_label_window` rather than a calculation of this module's own, which is
        `outcome_known_at_for`'s rule stated one plane up: a second reading of one calendar is a
        second thing that can disagree with it.
        """
        try:
            return build_label_window(
                as_of=instant, zone=MODEL_DATE_ZONE, horizon=horizon, calendar=self.calendar
            )
        except (LabelError, TradingCalendarError) as error:
            raise ModelRunBlockedError(
                f"the outcome window for a prediction at {instant.isoformat()} cannot be built on "
                f"the {self.calendar.exchange} calendar: {error}. Narrow the range to days whose "
                "outcome window the calendar reaches, or build the calendar over the year the "
                "window ends in"
            ) from error

    def label(self, ts_code: str, window: LabelWindow) -> OutcomeLabel | None:
        """One security's forward return over `window`, or `None` when it has no factor series.

        `None` rather than a refusal for a security with no stored adjustment history, which is
        `factor_view._PanelInputs.label`'s decision: `label_outcome` requires one, and a name that
        has none has no correct return. It leaves the label map, `labelled_panel` reports it on
        `PanelExclusion` with `NO_LABEL`, and it is visible on the answer rather than dropped.

        The three corpus faults are caught apart from `LabelError` and enveloped as
        `panel_unreadable`, which is `V2-P4-084`'s finding transplanted rather than rediscovered:
        `StockUniverseError`, `AdjustmentError` and `PriceDataError` are all statements about the
        *stored corpus* whose remedy is a build, while a `LabelError` is a statement about *this
        window* whose remedy is a different range.
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
            raise ModelRunBlockedError(
                f"{ts_code} could not be labelled over "
                f"{window.entry_day.isoformat()}..{window.exit_day.isoformat()}: {error}"
            ) from error
        except _LABEL_CORPUS_FAULTS as error:
            raise ModelPanelUnreadableError(
                f"{ts_code}'s outcome over "
                f"{window.entry_day.isoformat()}..{window.exit_day.isoformat()} could not be "
                f"priced out of {self._store.root}: {error}",
                disclosable=(
                    f"{ts_code}'s outcome over "
                    f"{window.entry_day.isoformat()}..{window.exit_day.isoformat()} could not be "
                    f"priced out of {PANEL_STORE_PLACEHOLDER}: "
                    f"{_without_store_path(str(error), self._store)}"
                ),
            ) from error


def _prediction_instants(store: PanelStore, request: ModelRunRequest) -> tuple[datetime, ...]:
    """The stored builds inside the declared range, one per prediction day, ascending.

    Three steps, and each is a rule borrowed rather than invented. See this module's docstring.
    """
    try:
        stored = stored_cross_section_instants(
            store, columns=request.columns, years=request.years, as_of=request.as_of
        )
    except FeatureMatrixError as error:
        raise ModelPanelUnreadableError(
            f"the declared feature columns could not be read out of {store.root}: {error}",
            disclosable=(
                "the declared feature columns could not be read out of "
                f"{PANEL_STORE_PLACEHOLDER}: {_without_store_path(str(error), store)}"
            ),
        ) from error
    newest: dict[date, datetime] = {}
    for instant in stored:
        day = instant.astimezone(MODEL_DATE_ZONE).date()
        if request.start <= day <= request.end:
            newest[day] = instant
    if not newest:
        raise ModelRunBlockedError(
            f"no stored cross section of {list(request.feature_ids)} falls between "
            f"{request.start.isoformat()} and {request.end.isoformat()} and is visible at "
            f"{request.as_of.isoformat()}. A walk-forward over no prediction day is an empty "
            "success -- build the declared columns over that range with `openalpha factor "
            "build`, or widen it"
        )
    # Not sorted, and a mutation sweep is what settled that. `sorted(newest.items())` here
    # survived every test, because it cannot change the answer: `stored_cross_section_instants`
    # returns ascending instants, `astimezone(...).date()` is monotone in the instant, and a
    # `dict` keeps insertion order -- so the days are already ascending and each one's value is
    # the newest instant it saw. Deleted rather than asserted, `feature_matrix._universe_for`'s
    # own deleted `sorted()` and `V2-P4-011`'s deleted duplicate check: a guarantee stated twice
    # is one guarantee plus a copy free to go stale. What holds the property is one layer down --
    # `labelled_panel` refuses a panel whose prediction days are not strictly increasing, so an
    # unordered answer here is a named refusal there rather than a silent reordering.
    return tuple(newest.values())


def _matrix(
    store: PanelStore, request: ModelRunRequest, *, as_ofs: Sequence[datetime]
) -> FeatureMatrix:
    """Every requested instant's cross section, under one recipe, or this face's refusal."""
    try:
        return build_feature_matrix(
            store,
            FeatureMatrixRequest(
                columns=request.columns,
                years=request.years,
                exchange=request.exchange,
                as_ofs=tuple(as_ofs),
                missing=request.missing,
            ),
        )
    except FeatureMatrixError as error:
        raise _matrix_refusal(error) from error


def _section(
    store: PanelStore, request: ModelRunRequest, *, as_of: datetime
) -> FeatureMatrixSection:
    """One instant's cross section, for the face that predicts about exactly one."""
    try:
        return load_feature_cross_section(
            store,
            FeatureMatrixRequest(
                columns=request.columns,
                years=request.years,
                exchange=request.exchange,
                as_ofs=(as_of,),
                missing=request.missing,
            ),
            as_of=as_of,
        )
    except FeatureMatrixError as error:
        raise _matrix_refusal(error) from error


def _matrix_refusal(error: FeatureMatrixError) -> ModelViewError:
    """Envelope one `feature_matrix` refusal under the row this face reports it as.

    Two rows and not one: `FeatureMatrixUnreadableError` is what a *store* said and its remedy is
    a build, while everything else that module raises is a statement about the request or about
    the shape of what is stored. The message is kept verbatim -- it names the instants, the
    columns and the sessions -- because a caller told only `blocked` cannot act on it.
    """
    if isinstance(error, FeatureMatrixUnreadableError):
        return ModelPanelUnreadableError(str(error))
    return ModelRunBlockedError(str(error))


def _labelled(
    inputs: _LabelInputs, *, sections: Sequence[FeatureMatrixSection], horizon: ResearchHorizon
) -> LabelledPanel:
    """Join every cross section to the outcomes built at its own instant.

    The labels are built for **every security the cross section carries**, not for the ones that
    happen to have a value: `PanelSection` keeps the cross section whole and `labelled_panel`
    reports each absence on `PanelExclusion`, which is `V2-P4-011`'s *scored or abstained, never
    absent* one layer up. A row with no label leaves visibly.
    """
    try:
        return labelled_panel(
            LabelledCrossSection(
                cross_section=section.cross_section,
                labels=tuple(
                    label
                    for ts_code in section.cross_section.subjects
                    for label in (
                        inputs.label(ts_code, inputs.window(section.as_of, horizon=horizon)),
                    )
                    if label is not None
                ),
            )
            for section in sections
        )
    except WalkForwardError as error:
        raise ModelRunBlockedError(
            f"the stored cross sections between could not be joined to their outcomes: {error}"
        ) from error


def trainable_at(panel: LabelledPanel, *, deadline: datetime) -> tuple[TrainingExample, ...]:
    """Every labelled example whose outcome window had closed at or before `deadline`.

    `WalkForwardFold.purged`'s comparison with the deadline **supplied** rather than derived from
    a test block, which is what a daily run needs and a fold cannot give it: a fold's deadline is
    the instant it is first asked at, and a daily run is asked about a day that has no labels at
    all.

    `<=` and not `<`, which is the same inequality that rule uses read from the other side:
    `PredictionBatch` admits `as_of == training_cutoff` -- *"training through last night's close
    and predicting as of it is what a daily model does"* -- and a fit that dropped a label closing
    on the very instant it predicts at would be stricter than the contract it feeds, for no
    reason either could state.

    Public, because it is the one rule this module applies that `backtest/` does not already own,
    and a rule a reader cannot call is a rule nobody can check.
    """
    return tuple(
        example
        for example in panel.examples
        if example.label.window.close_instant(example.label.window.exit_day) <= deadline
    )


def _model_of(declaration: AlphaModelDeclaration) -> AlphaModel:
    """The implementation `family` names, or this face's refusal naming the two that exist."""
    try:
        return MODEL_FAMILIES[declaration.family](declaration=declaration)
    except AlphaModelError as error:
        raise ModelRequestError(f"this declaration cannot be fitted: {error}") from error


# --- evaluate -----------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True, kw_only=True)
class ModelEvaluation:
    """One walk-forward evaluation's whole answer: what was read, what was fitted, and whether it
    clears the floor it declared.

    `folds` is `V2-P4-014`'s record carried by value, one per fold, so the artifact that produced
    each set of numbers is recoverable without a lookup. `is_blocked` is computed from the
    measured `scored_ratio` against the declared floor and is on this record rather than
    re-derived at each face, which is what carries the blocked-versus-empty guarantee to a channel
    that has only JSON and a status code.
    """

    request: EvaluationRequest
    prediction_days: tuple[date, ...]
    excluded: tuple[tuple[str, date, str], ...]
    folds: tuple[FoldEvaluation, ...]

    @property
    def offered_count(self) -> int:
        """How many security-days every fold's test block offered a model."""
        return sum(point.offered_count for fold in self.folds for point in fold.points)

    @property
    def scored_count(self) -> int:
        """How many of them the model put a number on."""
        return sum(point.scored_count for fold in self.folds for point in fold.points)

    @property
    def scored_ratio(self) -> float:
        """The fraction of the offered market this evaluation answered about.

        Pooled over every fold rather than averaged over the folds' own ratios, because the folds
        need not have the same number of test days and an average of ratios would weight a
        two-day fold like a twenty-day one.
        """
        return self.scored_count / self.offered_count

    @property
    def is_blocked(self) -> bool:
        return self.scored_ratio < self.request.run.minimum_scored_ratio


def evaluate_model(store: PanelStore, request: EvaluationRequest) -> ModelEvaluation:
    """Read the range, label it, cut it into folds, and fit the declaration once per fold.

    The one entry point all three faces call, which is what makes their answers one answer rather
    than three that agree today -- `run_shortlist`'s arrangement one plane over. It re-derives
    nothing: the matrix is `build_feature_matrix`'s, the join is `labelled_panel`'s, the split is
    `walk_forward_folds`' and every number is `evaluate_walk_forward`'s.

    It stores nothing; see
    `an_evaluation_registers_nothing_because_every_record_it_could_write_would_be_unwitnessed`.
    """
    run = request.run
    instants = _prediction_instants(store, run)
    matrix = _matrix(store, run, as_ofs=instants)
    inputs = _LabelInputs(store, run)
    panel = _labelled(inputs, sections=matrix.sections, horizon=run.horizon)
    try:
        folds = walk_forward_folds(
            panel,
            calendar=inputs.calendar,
            folds=request.folds,
            test_days_per_fold=request.test_days_per_fold,
            embargo_sessions=request.embargo_sessions,
        )
    except WalkForwardError as error:
        raise ModelRunBlockedError(
            f"this panel's {len(panel.prediction_days)} prediction day(s) cannot carry the "
            f"declared schedule of {request.folds} fold(s) of {request.test_days_per_fold} test "
            f"day(s): {error}"
        ) from error
    try:
        evaluations = evaluate_walk_forward(_model_of(run.declaration), folds)
    except AlphaModelError as error:
        raise ModelRunBlockedError(
            f"{run.declaration.name} could not be evaluated over this panel: {error}"
        ) from error
    return ModelEvaluation(
        request=request,
        prediction_days=panel.prediction_days,
        excluded=tuple((item.ts_code, item.prediction_day, item.reason) for item in panel.excluded),
        folds=evaluations,
    )


# --- daily run ----------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True, kw_only=True)
class DailyRunResult:
    """One daily run's whole answer: the fit, the batch, the record it was filed as, and the run.

    `record` is the **held** document rather than the arriving one, which is
    `PredictionWrite.record`'s own arrangement: on a re-run it is what was already on disk, so a
    caller reads back the instant this store *first* held the prediction rather than the instant
    it last saw it.
    """

    request: DailyRunRequest
    cross_section_as_of: datetime
    pricing_session: date
    training_day_count: int
    training_example_count: int
    record: PredictionRecord
    outcome: Literal["created", "unchanged"]
    run_id: str
    manifest: RunManifest
    manifest_outcome: Literal["created", "unchanged"]

    @property
    def offered_count(self) -> int:
        return len(self.record.batch.predictions)

    @property
    def scored_count(self) -> int:
        return len(self.record.batch.scored)

    @property
    def scored_ratio(self) -> float:
        return self.scored_count / self.offered_count

    @property
    def is_blocked(self) -> bool:
        return self.scored_ratio < self.request.run.minimum_scored_ratio


def run_daily(
    store: PanelStore,
    request: DailyRunRequest,
    *,
    predictions: ModelPredictionStore,
    runs: ResearchRunWriter,
    predicted_at: datetime,
    started_at: datetime,
) -> DailyRunResult:
    """Fit on what has already closed, score today's cross section, and register the answer.

    The order is the whole of Story S32 and is not an implementation detail: the batch is produced
    first, the store takes custody second and stamps `recorded_at` off its **own** clock, and the
    run manifest is filed last against the address the record came back with. A caller who
    backdates `predicted_at` therefore reaches `unwitnessed` and cannot reach `forward`.

    `predicted_at` is a parameter for `FittedAlphaModel.predict`'s reason -- a hidden
    `datetime.now()` here would make every batch unreproducible and every test order-dependent --
    and the composition root is what passes a clock it did not choose.
    """
    run = request.run
    instants = _prediction_instants(store, run)
    matrix = _matrix(store, run, as_ofs=instants)
    inputs = _LabelInputs(store, run)
    panel = _labelled(inputs, sections=matrix.sections, horizon=run.horizon)
    section = _section(store, run, as_of=request.predict_at)
    examples = trainable_at(panel, deadline=section.as_of)
    if not examples:
        raise ModelRunBlockedError(
            f"none of this panel's {len(panel.examples)} labelled example(s) from "
            f"{run.start.isoformat()}..{run.end.isoformat()} had an outcome window closed by "
            f"{section.as_of.isoformat()}, which is the instant this run predicts about. Every "
            "one of them would be an outcome the fit could not have known; widen the training "
            "range backwards, or predict about a later instant"
        )
    try:
        fitted: FittedAlphaModel = _model_of(run.declaration).fit(
            TrainingSet(feature_ids=panel.feature_ids, examples=examples)
        )
        batch = fitted.predict(
            section.cross_section, predicted_at=_aware(predicted_at, flag="predicted_at")
        )
    except (AlphaModelError, ValueError) as error:
        raise ModelRunBlockedError(
            f"{run.declaration.name} could not be fitted on the "
            f"{len(examples)} example(s) that had closed by {section.as_of.isoformat()}, or "
            f"could not score the cross section there: {error}"
        ) from error

    write = predictions.put(batch=batch, calendar=inputs.calendar, zone=MODEL_DATE_ZONE)
    record = write.record
    outcome = write.outcome
    run_id = f"daily-{record.record_id}"
    manifest, manifest_outcome = _file_run(
        runs,
        run_id=run_id,
        request=request,
        artifact_id=record.batch.artifact.artifact_id,
        started_at=_aware(started_at, flag="started_at"),
    )
    return DailyRunResult(
        request=request,
        cross_section_as_of=section.as_of,
        pricing_session=section.session,
        training_day_count=len({example.label.window.prediction_day for example in examples}),
        training_example_count=len(examples),
        record=record,
        outcome=outcome,
        run_id=run_id,
        manifest=manifest,
        manifest_outcome=manifest_outcome,
    )


def _file_run(
    runs: ResearchRunWriter,
    *,
    run_id: str,
    request: DailyRunRequest,
    artifact_id: str,
    started_at: datetime,
) -> tuple[RunManifest, Literal["created", "unchanged"]]:
    """File the manifest that finally fills `RunManifest.alpha_model_versions`.

    `V2-P4-010` declared the slot and named `V2-P4-016` for it; that issue measured that
    `run_cycle` has no `AlphaModel` on its path and passed it on; `V2-P4-017` measured the same
    thing from the store side. This is the first place in the repository that holds a fitted
    artifact and a run's identity at once, so this is where the join goes -- and the join is the
    one `domain/run.py` spells out in the field's own docstring:
    `AlphaModelRef(name=artifact.declaration.name, artifact_id=artifact.artifact_id)`.

    **The other three component planes stay empty and each is a statement.** `agent_versions` is
    empty because no agent ran; `model_versions` because no vendor model was called -- this run
    reads a stored panel and fits stdlib arithmetic over it; `prompt_versions` because the only
    prompt in this repository is a string literal `code_commit` already pins.

    Filed under an id derived from the prediction's own content address, so a re-run that
    reproduces a prediction finds its manifest already held and reports `unchanged` rather than
    raising `DuplicateRecordError` on the second of two stores that were both meant to be
    idempotent.
    """
    held = runs.get_run(run_id)
    if held is not None:
        return held, "unchanged"
    declaration = request.run.declaration
    manifest = RunManifest(
        run_id=run_id,
        mode=RunMode.daily,
        as_of=request.predict_at,
        code_commit=declaration.code_commit,
        config_digest=request.run.config_digest,
        alpha_model_versions=(AlphaModelRef(name=declaration.name, artifact_id=artifact_id),),
        random_seed=declaration.seed,
        environment=(VersionRef(component="feature_version", version=declaration.feature_version),),
        started_at=started_at,
        finished_at=started_at,
        status="succeeded",
    )
    runs.append_run(manifest)
    return manifest, "created"


def held_prediction(predictions: ModelPredictionStore, record_id: str) -> PredictionRecord:
    """One stored prediction, by the address its own body carried, or `ModelNotHeldError`.

    Raises rather than answering `None`, which is `held_shortlist`'s decision and its reason: a
    `record_id` is an address that was **printed by a run that produced it**, so nothing being
    held under one is a fault the caller can act on rather than an ordinary absence.

    A malformed address is `bad_request` and is refused before the store is touched, so "that is
    not an address" and "nothing is filed under that address" stay two answers.
    """
    if not re.fullmatch(r"prd_[0-9a-f]{24}", record_id):
        raise ModelRequestError(
            f"{record_id!r} is not a prediction address; a stored prediction is keyed by `prd_` "
            "and 24 lowercase hex characters, which is what `openalpha model daily-run` prints "
            "on its own answer"
        )
    held = predictions.get(record_id)
    if held is None:
        raise ModelNotHeldError(
            f"no prediction is held under {record_id}. The address is well formed and this "
            "installation has never filed one under it -- `openalpha model predictions` lists "
            "every address this runtime directory holds"
        )
    return held


# --- rendering, shared by the three faces --------------------------------------------------------


def _statistic(value: float | None) -> float | None:
    """A statistic, or `None`, and never a zero standing in for one.

    A one-line function so that the rule has one implementation and a mutation can find it:
    `FoldEvaluation` refuses a number where its coverage says there is none, and a face that
    rendered `mean_rank_ic: 0.0` for an unmeasured fold would undo that contract at the only
    boundary where it cannot be re-checked.
    """
    return value


def _fold_view(fold: FoldEvaluation) -> dict[str, object]:
    """One fold's headline and its per-day readings.

    `coverage` travels beside every statistic rather than being inferred from a `null`, because
    there are two different reasons a `rank_icir` is `null` -- the fold was not measured at all,
    or it was measured and its dispersion was exactly zero -- and a reader who saw only the
    `null` could not tell "no measurement" from "a measurement with no spread".
    """
    artifact = fold.artifact
    return {
        "first_test_day": fold.first_test_day.isoformat(),
        "artifact_id": artifact.artifact_id,
        "training_cutoff": artifact.training_cutoff.isoformat(),
        "training_example_count": artifact.training_example_count,
        "parameters": [{"feature_id": key, "value": value} for key, value in artifact.parameters],
        "coverage": fold.coverage,
        "test_day_count": fold.test_day_count,
        "measured_count": fold.measured_count,
        "mean_rank_ic": _statistic(fold.mean_rank_ic),
        "stdev_rank_ic": _statistic(fold.stdev_rank_ic),
        "rank_icir": _statistic(fold.rank_icir),
        "scored_ratio": fold.scored_ratio,
        "points": [
            {
                "prediction_day": point.prediction_day.isoformat(),
                "as_of": point.as_of.isoformat(),
                "offered_count": point.offered_count,
                "scored_count": point.scored_count,
                "paired_count": point.paired_count,
                "coverage": point.coverage,
                "rank_ic": _statistic(point.rank_ic),
            }
            for point in fold.points
        ],
    }


def _declaration_view(
    declaration: AlphaModelDeclaration, request: ModelRunRequest
) -> dict[str, object]:
    return {
        "name": declaration.name,
        "family": declaration.family,
        "horizon": declaration.horizon,
        "seed": declaration.seed,
        "code_commit": declaration.code_commit,
        "feature_version": declaration.feature_version,
        "feature_version_source": "declared" if request.declared_feature_version else "resolved",
        "feature_ids": list(request.feature_ids),
        "hyperparameters": [
            {"name": key, "value": value} for key, value in declaration.hyperparameters
        ],
    }


def evaluation_view(result: ModelEvaluation) -> dict[str, object]:
    """One walk-forward evaluation as the CLI's `--json`, HTTP and the SDK all render it.

    **`admitted` is `null` when the run was refused and a list when it was not**, which is
    `V2-P4-033`'s two keys transplanted and is the whole of the blocked-versus-empty guarantee at
    this boundary. `measurement` is what both answers share, byte for byte, so a caller comparing
    two runs one flag apart sees the bar move and nothing else.
    """
    run = result.request.run
    blocked = result.is_blocked
    return {
        "schema_version": MODEL_VIEW_SCHEMA_VERSION,
        "declaration": _declaration_view(run.declaration, run),
        "schedule": {
            "start": run.start.isoformat(),
            "end": run.end.isoformat(),
            "as_of": run.as_of.isoformat(),
            "exchange": run.exchange,
            "folds": result.request.folds,
            "test_days_per_fold": result.request.test_days_per_fold,
            "embargo_sessions": result.request.embargo_sessions,
            "prediction_days": [day.isoformat() for day in result.prediction_days],
        },
        "measurement": {
            "prediction_day_count": len(result.prediction_days),
            "fold_count": len(result.folds),
            "measured_fold_count": sum(1 for fold in result.folds if fold.coverage == "measured"),
            "offered_count": result.offered_count,
            "scored_count": result.scored_count,
            "scored_ratio": result.scored_ratio,
        },
        "folds": [_fold_view(fold) for fold in result.folds],
        "excluded": [
            {"ts_code": ts_code, "prediction_day": day.isoformat(), "reason": reason}
            for ts_code, day, reason in result.excluded
        ],
        "is_blocked": blocked,
        "admitted": None if blocked else [fold.artifact.artifact_id for fold in result.folds],
        "blocks": _scored_ratio_blocks(
            measured=result.scored_ratio,
            required=run.minimum_scored_ratio,
            scored=result.scored_count,
            offered=result.offered_count,
            about="the folds' test blocks",
        ),
        "limitations": [
            {"code": item.code, "detail": item.detail} for item in KNOWN_MODEL_VIEW_LIMITATIONS
        ],
    }


def _scored_ratio_blocks(
    *, measured: float, required: float, scored: int, offered: int, about: str
) -> list[dict[str, object]]:
    """The declared floor's verdict, as the list `V2-P4-033`'s `blocks` key carries.

    A list rather than a boolean, and each entry carries **both sides of the comparison** plus the
    counts they were read off: `ShortlistBlock`'s shape, and its reason -- a caller told `409` and
    nothing else cannot act on it, and one told a ratio without its numerator cannot tell a thin
    market from an abstaining model.
    """
    if measured >= required:
        return []
    return [
        {
            "code": "scored_ratio_below_floor",
            "measured": measured,
            "required": required,
            "detail": (
                f"{scored} of the {offered} securities offered across {about} carried a score, "
                f"which is {measured:.4f} against a floor of {required:.4f}. Abstaining is free, "
                "so a headline statistic is only comparable beside the fraction of the market it "
                "was taken over -- declare a floor this model can meet, or declare features more "
                "of the market carries"
            ),
        }
    ]


def prediction_view(record: PredictionRecord) -> dict[str, object]:
    """One stored prediction as every face renders it, standing included with what it proves.

    See `PREDICTION_STANDING_MEANINGS`. The two sentences travel in the body rather than in
    documentation because the body is what a caller pastes into a report, and a `"standing":
    "forward"` with nothing beside it reads as an attestation this repository cannot make.
    """
    proves, does_not = PREDICTION_STANDING_MEANINGS[record.standing]
    batch = record.batch
    return {
        "record_id": record.record_id,
        "standing": record.standing,
        "standing_proves": proves,
        "standing_does_not_prove": does_not,
        "supersedes": record.supersedes,
        "as_of": batch.as_of.isoformat(),
        "predicted_at": batch.predicted_at.isoformat(),
        "recorded_at": record.recorded_at.isoformat(),
        "outcome_known_at": record.outcome_known_at.isoformat(),
        "horizon": batch.horizon,
        "artifact_id": batch.artifact.artifact_id,
        "model_name": batch.artifact.declaration.name,
        "offered_count": len(batch.predictions),
        "scored_count": len(batch.scored),
        "predictions": [
            {"ts_code": item.ts_code, "score": item.score, "abstention": item.abstention}
            for item in batch.predictions
        ],
    }


def daily_view(result: DailyRunResult) -> dict[str, object]:
    """One daily run as the CLI's `--json`, HTTP and the SDK all render it.

    `admitted` is `null` when the declared floor refused the answer and the list of scored
    securities when it did not -- `evaluation_view`'s two keys, one face over. The **record is on
    the body either way**, because a refused run still registered its prediction: Story S32 is
    about a prediction being persisted before its outcome is known, which is unconditional, and
    the floor is about whether the answer may be acted on, which is not.
    """
    run = result.request.run
    blocked = result.is_blocked
    return {
        "schema_version": MODEL_VIEW_SCHEMA_VERSION,
        "declaration": _declaration_view(run.declaration, run),
        "training": {
            "start": run.start.isoformat(),
            "end": run.end.isoformat(),
            "as_of": run.as_of.isoformat(),
            "exchange": run.exchange,
            "day_count": result.training_day_count,
            "example_count": result.training_example_count,
        },
        "cross_section_as_of": result.cross_section_as_of.isoformat(),
        "pricing_session": result.pricing_session.isoformat(),
        "prediction": prediction_view(result.record),
        "write_outcome": result.outcome,
        "run_id": result.run_id,
        "run_manifest_id": result.manifest.run_manifest_id,
        "run_outcome": result.manifest_outcome,
        "alpha_model_versions": [
            {"name": ref.name, "artifact_id": ref.artifact_id}
            for ref in result.manifest.alpha_model_versions
        ],
        "measurement": {
            "offered_count": result.offered_count,
            "scored_count": result.scored_count,
            "scored_ratio": result.scored_ratio,
        },
        "is_blocked": blocked,
        "admitted": None if blocked else [item.ts_code for item in result.record.batch.scored],
        "blocks": _scored_ratio_blocks(
            measured=result.scored_ratio,
            required=run.minimum_scored_ratio,
            scored=result.scored_count,
            offered=result.offered_count,
            about="this cross section",
        ),
        "limitations": [
            {"code": item.code, "detail": item.detail} for item in KNOWN_MODEL_VIEW_LIMITATIONS
        ],
    }


def evaluation_rows(result: ModelEvaluation) -> tuple[tuple[str, str, str, str, str], ...]:
    """One row per fold for the terminal rendering: block, coverage, headline, spread, coverage.

    Strings rather than numbers, `shortlist_rows`' rule: a terminal rendering is a rendering, and
    formatting a `None` as `"not measured"` in the renderer would put the same decision in two
    places -- one of which would eventually print `0.00`.
    """
    return tuple(
        (
            fold.first_test_day.isoformat(),
            fold.coverage,
            _number(fold.mean_rank_ic),
            _number(fold.rank_icir),
            f"{fold.measured_count}/{fold.test_day_count} days, {fold.scored_ratio:.2%} scored",
        )
        for fold in result.folds
    )


def daily_rows(result: DailyRunResult) -> tuple[tuple[str, str], ...]:
    """The terminal rendering of one daily run, as label/value pairs in reading order."""
    record = result.record
    return (
        (
            "verdict",
            "REFUSED by ['scored_ratio_below_floor']" if result.is_blocked else "REGISTERED",
        ),
        ("record_id", record.record_id),
        ("standing", record.standing),
        ("standing means", PREDICTION_STANDING_MEANINGS[record.standing][0]),
        ("and does not prove", PREDICTION_STANDING_MEANINGS[record.standing][1]),
        ("as_of", record.batch.as_of.isoformat()),
        ("outcome_known_at", record.outcome_known_at.isoformat()),
        ("artifact_id", record.batch.artifact.artifact_id),
        ("scored", f"{result.scored_count} of {result.offered_count}"),
        ("write", result.outcome),
        ("run_id", result.run_id),
        ("run_manifest_id", result.manifest.run_manifest_id),
    )


def _number(value: float | None) -> str:
    """A statistic for a terminal, and `not measured` where there is none.

    Never `0.0000`: a zero that was measured and a zero that was never measurable are the same
    float and different facts, which is `MissingValuePolicy`'s rule and the one a terminal
    rendering is most likely to lose.
    """
    return "not measured" if value is None else f"{value:.4f}"


def _panel_section_days(sections: Sequence[PanelSection]) -> tuple[date, ...]:
    """The prediction days a labelled panel carries, for a caller that has only sections."""
    return tuple(section.prediction_day for section in sections)
