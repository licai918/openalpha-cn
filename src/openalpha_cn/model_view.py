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
from collections.abc import Callable, Iterable, Mapping, Sequence
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
    AlphaModelArtifact,
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
    "declared_hyperparameters",
    "evaluate_model",
    "evaluation_invariances",
    "evaluation_rows",
    "evaluation_view",
    "feature_columns",
    "held_prediction",
    "held_prediction_view",
    "held_predictions",
    "limitation_pointer",
    "model_evaluation_request",
    "panel_store",
    "prediction_index_rows",
    "prediction_index_view",
    "prediction_standing_legend",
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
        code="a_rank_statistic_sees_only_the_ordering_this_fit_induces",
        detail=(
            "`CrossSectionalRankModel` scores the sum of `coefficient x rank(column)` and every "
            "statistic this face reports is a rank correlation, which is invariant under every "
            "positive monotone transform of the score. Over a single declared column that "
            "leaves the sign of the coefficient as the only part of the fit `mean_rank_ic` and "
            "`rank_icir` can see: V2-P4-097 swept `--embargo-sessions` from 0 to 15, moved the "
            "training set from 780 examples to 2,640, and got `mean_rank_ic` identical to twelve "
            "decimal places at every step -- two `openalpha model evaluate` runs ten sessions of "
            "embargo apart printed byte-identical terminal output. The whole purged walk-forward "
            "ceremony above it is real and none of it reaches the headline. What does move is "
            "`folds[].parameters` (+0.180 to +0.212 across that sweep), which is why the "
            "coefficient is now the terminal rendering's last column and why `evaluation_"
            "invariances` says on each answer whether that answer is standing on this. "
            "`boosted_rank_trees` is not: a step function of one column is not a monotone "
            "transform of it."
        ),
    ),
    ModelViewLimitation(
        code="a_forward_standing_does_not_bound_the_instant_the_fit_read_the_panel",
        detail=(
            "`standing` compares two instants against the deadline -- when the batch says it was "
            "produced, and when this store held it. It says nothing about `as_of`, the instant "
            "every panel read behind the fit was made at, and the two can contradict each other: "
            "V2-P4-098 measured a record standing `forward` out of a run whose training `as_of` "
            "was 2026-04-01 against an `outcome_known_at` of 2026-03-30. `--as-of` defaults to "
            "the wall clock, so an ordinary run cannot reach this; one that names an instant "
            "later than its own clock can, and V2-P4-094 measured that the reachable `--as-of` "
            "set is pinned to the newest built session, which pushes callers toward late values "
            "rather than away from them. The instant is deliberately **not** added to the "
            "record. It is a caller-supplied value exactly as `predicted_at` is, so a reader "
            "meeting it in a stored document would take it for a bound on what the fit saw when "
            "nothing checked it -- the field-that-looks-like-proof V2-P4-017 refuses -- and "
            "adding it would take `alpha-prediction-record` to v2 through a refusing migration "
            "that moves every address already filed. **Blocking the contradiction was considered "
            "and declined, and the reason is not that it is unreachable** -- `put` hands back a "
            "record carrying `outcome_known_at`, so `run_daily` could compare after the write "
            "and add a second `blocks` entry without reading the calendar twice. It is declined "
            "because the block would be about a *claim* rather than about a leak. What a late "
            "`as_of` actually admits into a run is today's registry, calendar and adjustment "
            "shape, which is already declared by "
            "`the_evaluation_reads_its_labels_at_one_as_of_and_that_is_not_a_point_in_time_fit`; "
            "the outcome itself is kept out of the fit by `trainable_at`'s purge and by each "
            "cross section being read at its own prediction instant, both of which hold whatever "
            "`--as-of` says. So a run that reached this really may be sound, and refusing it "
            "would refuse a sound answer to protect a reader from over-reading a badge. The "
            "remedy for over-reading a badge is words: this entry, and the two faces that hold "
            "both numbers printing both -- `daily_view`'s `training.as_of` and the terminal "
            "rendering's `panel read at`, beside the deadline."
        ),
    ),
    ModelViewLimitation(
        code="the_supersedes_edge_is_unreachable_from_every_face_this_module_serves",
        detail=(
            "`PredictionRecord.supersedes` is what Implementation Decision 14's 回溯重算 is "
            "supposed to name -- the earlier record a recomputation corrects -- and no face "
            "offers it. `run_daily` is the only caller of `put` in `src/` and passes three "
            "keywords; there is no CLI flag, no field on `DailyRunRequest` or its HTTP body, and "
            "no SDK parameter. So the sentence a `backfill` is rendered with -- *a backfill "
            "naming no earlier record corrects nothing* -- describes a state no user of this "
            "product can leave. `domain/prediction_record.py` records the same fact one plane "
            "down under `the_supersedes_edge_is_contract_only_because_no_face_offers_a_record_"
            "to_name`, and V2-P4-099 measured that it never reaches a body: this registry is "
            "what a caller pastes into a report, and the contract's own is not rendered on any "
            "answer. Exposing the flag stays declined for that entry's reason -- the only honest "
            "argument is a `record_id` read off an earlier run, which is `held_prediction`'s "
            "address rather than a daily run's input."
        ),
    ),
    ModelViewLimitation(
        code="no_face_here_can_produce_an_unwitnessed_record_because_one_clock_stamps_both_instants",
        detail=(
            "`unwitnessed` is a third of `PredictionStanding` and describes a batch stamped in "
            "time that reached the store late. All three faces read one clock: the CLI takes "
            "`predicted_at` from `_panel_clock()` and hands `FilePredictionStore` the same "
            "callable, HTTP and the SDK hand both halves the container's clock. V2-P4-099 "
            "measured the two instants **equal** on every shipped path, so the window this "
            "standing describes is the duration of one `put` -- microseconds -- and the state is "
            "unreachable in practice. It is still not collapsible: `V2-P4-017` argues that "
            "folding it into `forward` makes it evidence it is not and folding it into "
            "`backfill` accuses a caller whose only fault may have been a slow disk, and a "
            "contract that could not express a slow disk would be wrong the first time a store "
            "lives somewhere a write can block. What is measured is that this build has no such "
            "store: `tests/integration/test_model_interfaces.py` reaches the standing only by "
            "injecting a clock that advances between the two readings."
        ),
    ),
    ModelViewLimitation(
        code="a_re_run_of_one_day_files_a_second_record_because_predicted_at_reaches_the_address",
        detail=(
            "`model daily-run` said re-running an identical day was `unchanged` on both stores. "
            "It is not, and cannot be through this face: `predicted_at` is this process's clock "
            "reading, it reaches `record_id` through the batch, and so every invocation files a "
            "new record and a new manifest. V2-P4-100 measured a scheduled job retrying after a "
            "transient failure leaving **two records for one prediction day**, which inflates "
            "every count taken over this store -- including the multiple-testing denominator "
            "`domain/prediction_record.py` says the register holds. The three repairs that look "
            "obvious are each refused, and by this repository's own arguments. Taking "
            "`predicted_at` out of the address is V2-P4-016's field and would let a backfill "
            "collide with the original it recomputes, which is the one collision "
            "`storage/predictions.py` rests on being impossible. A `--predicted-at` flag would "
            "hand a caller the field that is unverifiable *by construction* and make it a chosen "
            "one, which is the same move `put` refuses by having no `recorded_at` parameter. A "
            "scan of the register before each write -- 'is a record already held for this "
            "declaration and this `as_of`' -- is the access pattern "
            "`the_store_never_checks_that_its_own_clock_moved_forward` declines for the sibling "
            "question. So this is a constraint rather than a defect to repair here: two runs of "
            "one day really did produce two batches at two instants, the store records both "
            "truthfully, and what is missing is a way to say they are one forecast -- which is "
            "`supersedes`, and `the_supersedes_edge_is_unreachable_from_every_face_this_module_"
            "serves` is the entry above."
        ),
    ),
    ModelViewLimitation(
        code="a_subject_narrowed_factor_build_does_not_narrow_the_market_this_face_labels",
        detail=(
            "`openalpha factor build --subject` narrows what is *computed*; it does not narrow "
            "what is *offered*. `feature_matrix.py`'s cross section is the stored registry's "
            "listed set -- 'the rows are the universe' -- so a factor built over sixty names is "
            "read against every listed security on every prediction day, and every name with no "
            "stored value abstains. V2-P4-100 measured 348 scores over 33,090 security-days on a "
            "real panel: **1.05%**, which puts every meaningful `--min-scored-ratio` out of "
            "reach. On the shortlist chain `--subject` buys proportional work; here it buys a "
            "shorter build and nothing else. Widening the recipe rather than the floor is the "
            "remedy -- build the declared columns over the whole registry -- and lowering the "
            "floor to fit a narrow build is the move `the_scored_ratio_floor_is_a_coverage_bar_"
            "and_never_a_quality_one` exists to make visible."
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

_OUTCOME_WINDOW_FAULTS: Final[tuple[type[Exception], ...]] = (LabelError, TradingCalendarError)
"""The two refusals building an outcome window raises, named once for the **two** places that
build one -- and `V2-P4-088` is the second of those places going unguarded.

`_LabelInputs.window` had this `except` inline and it covered the training side alone. The
prediction side runs the identical computation inside the store -- `predictions.put` ->
`prediction_record_for` -> `outcome_known_at_for` -> `build_label_window` -- and `run_daily`
called `put` after its only `try` had closed. So one calculation had a verdict on one path and
`exit 5` / a bare `500` on the other, and which path a run took was decided by
`daily_request`'s own rule that `predict_at`'s date is **strictly after** `end`: the prediction
day is always later than every training day, so the guarded path could never fire for it and the
unguarded one always saw the furthest-reaching window.

Named as a tuple used by both call sites rather than duplicated as a second `except`, which is
`prediction_batch_for`'s own argument about `require_features`: two copies of a check are one
check plus a place for a future path to skip it. Both sites also share
`_outcome_window_refusal`, so there is one sentence as well as one rule.

`CalendarHorizonError` is a `TradingCalendarError`, and its docstring says it is "the one failure
that is *not* a caller mistake: the question was well formed and the exchange simply has not
published that far". Two product faces were reporting it as a defect in the command.

**Which of three refusals a caller actually meets is a fact about their panel, and `V2-P4-100`
measured the order.** This one needs a window that runs past the *calendar's* last published
session, and a calendar built to the end of its year reaches every window a mid-year panel can
ask for -- so on a panel built in August 2026 with `trade_cal` stored through December, the
calendar never refuses. What refuses first is the price plane, and which of its two sentences
arrives depends on the reading `as_of`:

- `as_of` inside what the panel holds -- `daily cannot be read for <session> ...: that session
  had not published yet`, from `_read_visible_price_session`, extended by
  `_window_reach_refusal` with the horizon and the prediction day that reached it (`V2-P4-099`).
- `as_of` past the newest stored session -- `date_gap`, `89 required date(s) are absent from
  daily`, because `daily_requirement` requires every session through the newest that had
  published at `as_of` and the partition has fallen behind.

So this refusal is reachable at a real year end, or on a runtime whose `trade_cal` was never
built forward, and not by simply lengthening `--horizon` on a mid-year panel.
`tests/integration/test_year_end_daily_run.py` drives a whole year of 2026 to reach it, which is
what that costs. What `V2-P4-088` was protecting holds under all three: exit `1` and a sentence a
caller can act on, never `exit 5` and never a bare `500`.
"""

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


def declared_hyperparameters(
    declared: Iterable[tuple[str, bool | int | float | str]],
) -> tuple[tuple[str, bool | int | float | str], ...]:
    """The declared hyperparameters in the one order a declaration has: sorted **by name alone**.

    `feature_columns`' arrangement for the other declared list, and `V2-P4-091` is why it is one
    function rather than a rule each face restates. Both faces sort, because
    `AlphaModelDeclaration` refuses an unsorted tuple to keep one declaration from having two
    canonical spellings, and the order a caller typed flags or wrote JSON keys in is not a claim.
    They sorted **differently**: the command line by `pair[0]` and the HTTP body by the whole
    `(name, value)` pair. Sorting by the pair reaches the values whenever two names tie, so
    `[{"name": "x", "value": 1}, {"name": "x", "value": "a"}]` made the sort compare `1 < "a"` and
    raise `TypeError` -- a caller's mistake arriving as `500 text/plain` on a face whose sibling
    answered `422` with the reason on it.

    A repeated name is still refused, and by the contract rather than here: it is the one thing
    about this list a caller *did* claim, and `AlphaModelDeclaration.validate_hyperparameters`
    already says why two entries under one name cannot both be honoured. Sorting by name keeps
    that refusal reachable instead of failing ahead of it on the values' types -- which is the
    whole difference between the two spellings, since a repetition is the only input on which
    they can disagree.

    Stable, so two entries sharing a name arrive in the order they were declared and the refusal
    names them in that order on every face.
    """
    return tuple(sorted(declared, key=lambda pair: pair[0]))


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


def _window_reach_refusal(
    error: ModelPanelUnreadableError, *, window: LabelWindow
) -> ModelPanelUnreadableError:
    """Say which two flags put a session inside a label window (`V2-P4-099`).

    The refusal underneath is exactly right about the panel -- *"daily cannot be read for
    2026-01-19 ...: that session had not published yet, because a session becomes knowable at
    16:30 Asia/Shanghai"* -- and says nothing about why **this run** wanted 2026-01-19. That
    acceptance measured `--horizon 8d`, `10d`, `12d`, `20d`, `60d` and `250d` producing that one
    sentence character for character while `5d` cleared it, because the first unpublished session
    a window reaches is the same session whatever the horizon: only the *first* read fails, and
    every horizon long enough reaches it first.

    So the wall is a joint function of the declared horizon and the last prediction day in the
    range, and neither appeared. The contrast is on this same face and is the standard:
    *"this panel's 54 prediction day(s) cannot carry the declared schedule of 40 fold(s) of 5 test
    day(s)"* names both halves of what a caller declared. `V2-P4-095` is the same wall met from
    the `--end` side on `daily-run`, which is why the remedy names both flags rather than the one
    this call site happens to be under.

    The inner message is kept **verbatim and first**, `_matrix_refusal`'s rule: it names the
    session, the instant and the publication rule, and a caller told only "shorten your horizon"
    could not tell this from a panel that was simply never built. `disclosable` is extended in
    step, because a clause naming two flags and three dates contains no path and belongs on both.

    **Every read behind a label window comes through here, not only the unpublished-session
    one**, so the clause is split: *why this run wanted that session* is true of all of them and
    is stated flatly, while the remedy is conditioned on the reason the inner sentence gives.
    A `daily` partition whose Parquet file is gone reaches this same `except`, and "shorten your
    horizon" would be advice that cannot work -- the failure mode `_unbuilt_dataset_remedy`
    refuses one layer down, in its own words: a refusal naming a command which does not help is
    worse than one naming none.
    """
    clause = (
        f". This run reached it because the {window.horizon.text} outcome window for the "
        f"prediction day {window.prediction_day.isoformat()} opens on "
        f"{window.entry_day.isoformat()} and exits on {window.exit_day.isoformat()}: the reach "
        "is the declared horizon and the last prediction day in the range together. Where that "
        "session has simply not published yet, either flag moves the window back inside what "
        "has -- a shorter --horizon, or a --start/--end range that stops earlier"
    )
    return ModelPanelUnreadableError(f"{error}{clause}", disclosable=f"{error.disclosable}{clause}")


def _outcome_window_refusal(
    error: Exception, *, instant: datetime, calendar: TradingCalendar
) -> ModelRunBlockedError:
    """The one sentence this face answers an unbuildable outcome window with.

    Shared by the two places that build one -- `_LabelInputs.window` for the training days, and
    `run_daily`'s hand-off to the prediction store for the day being predicted about. See
    `_OUTCOME_WINDOW_FAULTS` for why those were two answers until `V2-P4-088`.

    **The remedy names the command**, `_unbuilt_dataset_remedy`'s rule: the condition this fires
    on most often is a calendar that stops where the year does, and "build the calendar over the
    year the window ends in" -- what this used to say -- leaves an operator to work out both the
    dataset name and the year at three in the morning on the 31st of December. The year is the one
    after the calendar's own last session, because that is where a window opened on its last few
    sessions has to land, and a partition alone is not enough: `load_trading_calendar` reads the
    years the *request* declared, so an unstated `--year` is a calendar that stops just as short.

    The other remedy is deliberately "ask about a session", which is true on both paths: the
    training side narrows `--start`/`--end` and the prediction side moves `--predict-at`, and a
    sentence naming one flag would be wrong advice on the other half of its call sites.
    """
    return ModelRunBlockedError(
        f"the outcome window for a prediction at {instant.isoformat()} cannot be built on the "
        f"{calendar.exchange} calendar: {error}. Either build the calendar forward -- `openalpha "
        f"panel build --dataset {MODEL_PANEL_DATASETS[TRADING_CALENDAR_DATASET]} --year "
        f"{calendar.horizon.last_date.year + 1}`, then declare that year with `--year` -- or ask "
        "about a session whose outcome window this calendar already reaches"
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
        except _OUTCOME_WINDOW_FAULTS as error:
            raise _outcome_window_refusal(error, instant=instant, calendar=self.calendar) from error

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
        try:
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
        except ModelPanelUnreadableError as error:
            raise _window_reach_refusal(error, window=window) from error
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

    # The store seals the batch against the calendar's answer to when its outcome becomes
    # knowable, and derives that answer through the same `build_label_window` the training side
    # goes through -- so the same two refusals reach it, and `V2-P4-088` measured them arriving
    # unenveloped because this call sits after the `try` above rather than inside a guard of its
    # own. Guarded on the *call* rather than by re-deriving the window first: a run that computed
    # the deadline ahead of the store to check it would be a second reading of one calendar, and
    # the day the two readings stopped agreeing the guard would stop guarding without saying so.
    try:
        write = predictions.put(batch=batch, calendar=inputs.calendar, zone=MODEL_DATE_ZONE)
    except _OUTCOME_WINDOW_FAULTS as error:
        raise _outcome_window_refusal(
            error, instant=batch.as_of, calendar=inputs.calendar
        ) from error
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

    `invariances` is `V2-P4-097`'s key and is about the statistics on **this** answer -- see
    `evaluation_invariances`. `daily_view` deliberately has no counterpart, and the asymmetry is
    the one `V2-P4-099` asked about the other way round: a daily run reports no rank statistic at
    all, so there is nothing for a rank correlation's invariance to be a boundary on. What both
    faces do share is `limitations`, and both terminal renderings now say how many of them there
    are (`limitation_pointer`).
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
        "invariances": evaluation_invariances(run),
        "limitations": [
            {"code": item.code, "detail": item.detail} for item in KNOWN_MODEL_VIEW_LIMITATIONS
        ],
    }


def evaluation_invariances(run: ModelRunRequest) -> list[dict[str, object]]:
    """What this run's own arithmetic keeps out of its own reported statistics.

    A **list on the answer** rather than a tenth row of `KNOWN_MODEL_VIEW_LIMITATIONS`, and the
    two are different kinds of statement. The registry travels on every answer and is true of the
    face; this is true of *this* run and false of the next one -- `V2-P4-097` asked which of the
    two the single-feature invariance is, and it is both, so it is written twice on purpose. The
    registry entry states the boundary; this key states that the run in the caller's hand is
    standing on it, with the run's own column count rendered into the sentence rather than
    described in it. A boundary a reader has to check their own flags against is one they will
    not check.

    `blocks`' shape, deliberately: a coded list a caller can act on, empty when nothing applies.
    Empty is the load-bearing case -- a `boosted_rank_trees` fit over one column is a step
    function of it rather than a monotone transform of it, so its statistic really does see the
    fit, and an entry that appeared on every answer would say nothing about any of them.
    """
    if run.declaration.family != BASELINE_FAMILY:
        return []
    count = len(run.feature_ids)
    return [
        {
            "code": "a_rank_statistic_sees_only_the_ordering_this_fit_induces",
            "detail": (
                f"{BASELINE_FAMILY} scores the sum of `coefficient x rank(column)`, and every "
                "statistic on this answer is a rank correlation -- invariant under every positive "
                "monotone transform of the score. The coefficients on `folds[].parameters` "
                "therefore reach `mean_rank_ic` and `rank_icir` only through the ordering they "
                f"induce over this run's {count} declared column(s). Over a single column that "
                "ordering is the sign of its coefficient and nothing else, so the magnitude -- "
                "and with it every difference the purge, the embargo and the surviving training "
                "set made to the fit -- cannot reach the headline. V2-P4-097 measured exactly "
                "that: "
                "`--embargo-sessions` swept 0 to 15, the training set moved from 780 examples to "
                "2,640, `mean_rank_ic` was identical to twelve decimal places at every step, and "
                "the coefficient moved from +0.180 to +0.212. That coefficient is where "
                "V2-P4-014 measured a leak showing, so it is on `folds[].parameters` here and in "
                "the last column of the terminal rendering"
            ),
        }
    ]


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
    batch = record.batch
    return {
        **_prediction_index_entry(record),
        "supersedes": record.supersedes,
        "predicted_at": batch.predicted_at.isoformat(),
        "model": _artifact_view(batch.artifact),
        "predictions": [
            {"ts_code": item.ts_code, "score": item.score, "abstention": item.abstention}
            for item in batch.predictions
        ],
    }


def _artifact_view(artifact: AlphaModelArtifact) -> dict[str, object]:
    """The fit a stored prediction carries by value, rendered whole (`V2-P4-098`).

    That acceptance read a stored record and found it saying *"reversal-rank predicted these sixty
    numbers"* with no way to say what reversal-rank was, and concluded the record does not hold
    it. **The record holds all of it.** `PredictionBatch.artifact` is an `AlphaModelArtifact` by
    value, which carries the declaration -- family, horizon, seed, `code_commit`, resolved
    `feature_version`, hyperparameters -- plus the feature columns, the training cutoff, the
    example count and the fitted coefficients. It was this rendering that dropped it and printed
    two strings.

    So there is nothing to resolve and no face is needed to resolve it: `mdl_...` is an address
    for *comparing* two fits, not a key to look one up under, and a face that offered to resolve
    it would be offering to open a store that holds no artifacts. What is genuinely not here is
    the training range and the instant the panel was read at; see
    `a_forward_standing_does_not_bound_the_instant_the_fit_read_the_panel`.
    """
    declaration = artifact.declaration
    return {
        "artifact_id": artifact.artifact_id,
        "name": declaration.name,
        "family": declaration.family,
        "seed": declaration.seed,
        "code_commit": declaration.code_commit,
        "feature_version": declaration.feature_version,
        "feature_ids": list(artifact.feature_ids),
        "hyperparameters": [
            {"name": key, "value": value} for key, value in declaration.hyperparameters
        ],
        "training_cutoff": artifact.training_cutoff.isoformat(),
        "training_example_count": artifact.training_example_count,
        "parameters": [{"feature_id": key, "value": value} for key, value in artifact.parameters],
    }


def held_prediction_view(record: PredictionRecord) -> dict[str, object]:
    """One stored prediction as the two faces that hand out a record on its own render it.

    `prediction_view` plus `KNOWN_MODEL_VIEW_LIMITATIONS`, and the split is the point rather than
    an omission: `daily_view` embeds `prediction_view` under a body that already carries the
    registry once, and a nested second copy would be the same fifteen paragraphs twice in one
    answer. `openalpha model prediction` and `GET /api/v1/predictions/{record_id}` hand out the
    record and nothing else -- and they are exactly the faces a stored prediction is read through
    a year later, which is when the boundaries matter most and when nobody has the run's own
    output to hand.

    One function rather than each face appending its own key, `declared_hyperparameters`'
    finding: two faces that each spelled one rule differed on the only input that could tell them
    apart.
    """
    return {
        **prediction_view(record),
        "limitations": [
            {"code": item.code, "detail": item.detail} for item in KNOWN_MODEL_VIEW_LIMITATIONS
        ],
    }


def held_predictions(predictions: ModelPredictionStore) -> tuple[PredictionRecord, ...]:
    """Every held record, in the order this store took custody of them (`V2-P4-098`).

    **The register's index is a different question from the store's filing system, and this is
    where they part.** `FilePredictionStore.list_ids` reads its directory and sorts by name, which
    is a sort over content digests -- correct for a filing system, where the only requirement is
    that two runs list the same keys in the same order, and uncorrelated with time. The register
    exists to answer *which of these did I commit to first*, and a digest sort does not merely
    fail to answer it: measured on five records, the one created third sorted first, so the
    listing was actively misleading about the one thing it was read for.

    Ordered by `recorded_at`, not by `predicted_at`, and that is `standing`'s own choice made
    once more: `predicted_at` is whatever the caller passed to `predict` and nothing here can
    check it, while the custody stamp is the one instant a caller does not set. A register
    ordered by a field its subjects choose is a register that agrees to be told. The address
    breaks ties, so two records stamped in the same microsecond still list in one stable order.

    **What custody order is not is evidence**, and the record contract says so in its own words:
    `the_store_never_checks_that_its_own_clock_moved_forward` -- a clock that went backwards
    between two writes produces two records whose stored order contradicts their stamps, and
    `nothing_here_defends_against_whoever_owns_the_disk` covers the rest. This is bookkeeping a
    user can read, and reading every held document to build it is what it costs: one `get` per
    record, each of which re-derives an address (3.5 ms at market width, `storage/predictions.py`
    measures it). A listing is a rare read and a wrong order is a permanent one.
    """
    records = [
        record
        for record_id in predictions.list_ids()
        for record in (predictions.get(record_id),)
        if record is not None
    ]
    return tuple(sorted(records, key=lambda record: (record.recorded_at, record.record_id)))


def _prediction_index_entry(record: PredictionRecord) -> dict[str, object]:
    """What one record says about itself before anybody opens it.

    Shared by `prediction_view` and the register's listing rather than spelled twice, so a row in
    the index and the head of the body it points at cannot disagree about a standing or a date.

    The two `PREDICTION_STANDING_MEANINGS` sentences are on the **index row** as well as on the
    body, which is not decoration: that mapping's own docstring says a rendering printing
    `"standing": "forward"` and stopping turns a local-first bookkeeping fact into what reads like
    an attestation, and a column in a table does that at least as fast as a field in a document.
    """
    proves, does_not = PREDICTION_STANDING_MEANINGS[record.standing]
    batch = record.batch
    return {
        "record_id": record.record_id,
        "standing": record.standing,
        "standing_proves": proves,
        "standing_does_not_prove": does_not,
        "as_of": batch.as_of.isoformat(),
        "recorded_at": record.recorded_at.isoformat(),
        "outcome_known_at": record.outcome_known_at.isoformat(),
        "horizon": batch.horizon,
        "artifact_id": batch.artifact.artifact_id,
        "model_name": batch.artifact.declaration.name,
        "offered_count": len(batch.predictions),
        "scored_count": len(batch.scored),
    }


def prediction_index_view(records: Sequence[PredictionRecord]) -> dict[str, object]:
    """The whole register as the CLI's `--json`, HTTP and the SDK all render it.

    `record_ids` is kept, and kept first, because it is what `GET /api/v1/predictions` and
    `openalpha model predictions` already answered with and what `tests/e2e` reads -- but it now
    carries the **custody order** rather than the digest order, which is the fix rather than a
    compatible extension. `predictions` is the same list with each row saying what it is, so a
    reader can choose which body to open instead of opening all of them.

    Bodies stay out of it, `GET /api/v1/shortlists`' rule: a batch at market width is hundreds of
    kilobytes and a caller almost always wants one of them.
    """
    return {
        "record_ids": [record.record_id for record in records],
        "predictions": [_prediction_index_entry(record) for record in records],
    }


def prediction_index_rows(
    records: Sequence[PredictionRecord],
) -> tuple[tuple[str, str, str, str, str, str, str], ...]:
    """One row per held record for the terminal rendering, in custody order.

    `recorded_at` first, because the column a reader is looking for is the one the sort is on and
    a table sorted on a column it does not show is a table that looks arbitrary.
    """
    return tuple(
        (
            record.recorded_at.isoformat(),
            record.batch.as_of.isoformat(),
            record.standing,
            record.batch.horizon,
            f"{len(record.batch.scored)}/{len(record.batch.predictions)}",
            record.batch.artifact.declaration.name,
            record.record_id,
        )
        for record in records
    )


def prediction_standing_legend(
    records: Sequence[PredictionRecord],
) -> tuple[tuple[str, str, str], ...]:
    """Each standing present in a listing, once, with what it proves and what it does not.

    Once per *standing* rather than once per row, which is the one place the rule
    `PREDICTION_STANDING_MEANINGS` states has to bend to survive: two paragraphs against every
    line of a twenty-four row table is a table nobody reads, and the sentences would be lost by
    being printed. Ordered by first appearance in the listing, so the legend reads down in the
    same direction as the rows it explains.
    """
    seen: list[PredictionStanding] = []
    for record in records:
        if record.standing not in seen:
            seen.append(record.standing)
    return tuple((standing, *PREDICTION_STANDING_MEANINGS[standing]) for standing in seen)


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


def evaluation_rows(result: ModelEvaluation) -> tuple[tuple[str, str, str, str, str, str], ...]:
    """One row per fold for the terminal rendering: block, coverage, headline, spread, reach, fit.

    Strings rather than numbers, `shortlist_rows`' rule: a terminal rendering is a rendering, and
    formatting a `None` as `"not measured"` in the renderer would put the same decision in two
    places -- one of which would eventually print `0.00`.

    **`fit` is `V2-P4-097`'s column and it is last because it is the one that moves.** That
    acceptance swept `--embargo-sessions` from 0 to 15, moved the training set from 780 examples
    to 2,640, and got `mean_rank_ic` identical to twelve decimals on every one of them -- while
    the coefficient went from +0.180 to +0.212. The five columns this row used to carry were
    exactly the five that could not see the difference, so a caller comparing two runs of this
    family from a terminal was comparing two renderings that are byte-identical by construction.
    `_fit` is what they see instead, and `a_rank_statistic_sees_only_the_ordering_this_fit_induces`
    is why they needed to.
    """
    return tuple(
        (
            fold.first_test_day.isoformat(),
            fold.coverage,
            _number(fold.mean_rank_ic),
            _number(fold.rank_icir),
            f"{fold.measured_count}/{fold.test_day_count} days, {fold.scored_ratio:.2%} scored",
            _fit(fold.artifact),
        )
        for fold in result.folds
    )


def _fit(artifact: AlphaModelArtifact) -> str:
    """What one fold learned, in a line: the coefficients on the columns a caller declared.

    Keyed on the **artifact's own `feature_ids`** rather than on its family, which is
    `MODEL_FAMILIES`' rule applied to a rendering: a branch on `family` here would be the
    `if`/`elif` that table exists to avoid, and it would go stale the day a third family lands.
    What the two shipped families put in `parameters` differs in kind rather than in size --
    `CrossSectionalRankModel` stores one coefficient per declared column and
    `BoostedRankTreeModel` stores its whole ensemble under `t000.n000.edge`-shaped keys, two
    entries per node -- so "the entries a caller can read against the columns they declared" is
    the one selection that answers both without naming either.

    An ensemble is **counted rather than truncated**, because a terminal that printed the first
    six of fifty-six node parameters would look like a fit somebody could compare. The count is
    still a number that moves with the fit, which is the whole point of the column.

    A first draft carried a third arm -- a coefficient table plus `+n not on a declared column`,
    for a family storing both kinds -- and it is deleted rather than kept. Neither shipped family
    can reach it, so a mutation sweep cannot tell it from a wrong one, and it would have been
    this rendering deciding something no fitting code has decided yet. `--json` carries
    `folds[].parameters` whole either way; this line is a rendering, and a rendering that
    anticipates a family is the `if`/`elif` above under another name.
    """
    declared = frozenset(artifact.feature_ids)
    named = tuple((key, value) for key, value in artifact.parameters if key in declared)
    if not named:
        return f"{len(artifact.parameters)} parameter(s), none on a declared column"
    return ", ".join(f"{key}={value:.4f}" for key, value in named)


def daily_rows(result: DailyRunResult) -> tuple[tuple[str, str], ...]:
    """The terminal rendering of one daily run, as label/value pairs in reading order.

    **`panel read at` sits between the deadline and the artifact, and `V2-P4-098` is why.** That
    acceptance produced a record standing `forward` -- claimed and held before its outcome could
    be known -- out of a run whose panel reads were all made *after* the outcome had printed. The
    standing is correct about what it claims; it is simply not a statement about what the fit was
    allowed to see. The record cannot carry that instant (see
    `a_forward_standing_does_not_bound_the_instant_the_fit_read_the_panel`), but this face is
    holding both numbers at the moment it prints one of them, so it prints both.
    """
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
        ("panel read at", result.request.run.as_of.isoformat()),
        ("artifact_id", record.batch.artifact.artifact_id),
        ("scored", f"{result.scored_count} of {result.offered_count}"),
        ("write", result.outcome),
        ("run_id", result.run_id),
        ("run_manifest_id", result.manifest.run_manifest_id),
        ("limitations", limitation_pointer()),
    )


def limitation_pointer() -> str:
    """The one line both terminal faces carry in place of fifteen paragraphs (`V2-P4-099`).

    That acceptance found `evaluate`'s terminal carrying **none** of the named boundaries while
    `daily-run`'s printed the standing pair, and called the asymmetry unintended. It was. What a
    terminal may not do is print the registry: fifteen entries at a paragraph each buries the
    table of folds they are about, which is how a caller stops reading either.

    So both faces name the **count** and the flag that hands the text over. The count is the
    registry's own length rather than a number typed here, which is what makes this falsifiable --
    an entry added with this line left alone goes red at
    `test_both_model_terminal_faces_say_how_many_limitations_the_body_carries`, where "see the
    documentation" could never go red at all.
    """
    return (
        f"{len(KNOWN_MODEL_VIEW_LIMITATIONS)} named boundary(ies) on what this answer means; "
        "read them with --json"
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
