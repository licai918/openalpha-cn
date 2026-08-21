"""The quantitative model boundary (`V2-P4-011`): fit a labelled panel, predict a cross section.

Implementation Decision 10, in full: *"建立独立于 `ModelProvider` 的量化模型边界。`AlphaModel`
消费版本化特征数据集并产出不可变预测批次。`ModelProvider` 继续治理结构化 LLM 推理。Manifest
分别标识确定性、量化与 LLM 组件。"* Story S25 names the same thing in one line: a dedicated
quantitative `AlphaModel` contract, **strictly separate** from `ModelProvider`.

## The premise, measured rather than repeated

`models/base.py:32` declares `ModelProvider` and `:39-46` its one method,
`generate_json(system: str, user: str, schema: dict[str, Any]) -> dict[str, Any]`. Three
parameters, all of them free text or an untyped mapping, and an untyped mapping back. The row
and `AlphaModelRef`'s docstring both say that cannot express a panel fit/predict;
`tests/unit/domain/test_alpha_model_boundary.py` is where it stops being said and starts being
measured, three ways: the boundary's declared members read off its own AST, the exact four
fields a real `generate_json` payload cannot supply to `PredictionBatch`, and `isinstance`
against the two `runtime_checkable` protocols below. The two planes are disjoint at run time in
both directions, which is the property `V2-P4-010` found missing one layer up -- an agent id and
a vendor model shared one `tuple[VersionRef, ...]` for the whole of v1 with nothing able to
object.

## Why this module is in `domain/`, which is not a preference

Four issues on this chain consume this contract, and each sits behind a `lint-imports` contract
that rules out somewhere else:

| consumer | where it lives | what it may not reach |
| --- | --- | --- |
| `017` predictions persisted | `storage/` | `agents`, `runtime`, `product`, **`backtest`** |
| `013` walk-forward split | `backtest/` | `panel`, `providers`, `api`, `product`, **`models`** |
| `010` the manifest's third slot | `runtime/` | -- |
| `015` a LightGBM baseline | wherever `numpy` is legal | -- |

`storage-no-upward-deps` is what rules out `backtest/`: `V2-P4-017` has to *deserialize* a
`PredictionBatch`, which needs the import, and that is the exact shape `V2-P0B-012` fixed five
times by moving the contract down into `domain/`. `backtest-no-numeric-stack-or-panel-plane` is
what rules out `models/`, and it rules it out **structurally** rather than on the row's
"strictly separate" alone: `openalpha_cn.models` is on that contract's forbidden list, so a
walk-forward study under `backtest/` could not import an `AlphaModel` declared beside the LLM
provider at all. The same contract forbids `panel`, `providers`, `api`, `product` and
`evidence`, and `backtest-studies-reach-no-composition-root` forbids `runtime`.

`tests/unit/domain/test_alpha_model_layering.py` runs that elimination off the parsed
configuration rather than off this paragraph, and its result is stated here exactly as it comes
out: **the contracts narrow thirteen subpackages to two, `domain` and `tools`, and not to one.**
The choice between those two is a judgement and is written as one -- `tools/` holds two
`ResearchTool` implementations and already imports `domain/`, while `domain/` is where
`V2-P0B-012` moved all five contracts `storage/` had to deserialize and where
`domain/labels.py`, the type `TrainingExample` is expressed in, already lives. What the gate
buys is that relaxing any of those contracts makes the elimination go red instead of making this
paragraph go stale.

`domain-purity` then does the second job: ADR-0003 ships no numerical stack, and that contract
forbids `numpy`, `pandas`, `scipy` and `sklearn` to the whole of `domain/`, dynamically
discovered siblings included. So "the contract is expressible without a numeric dependency" is
not a promise made here, it is a gate. The nine runtime dependencies are unchanged.

## And how a numeric stack stays possible tomorrow

`AlphaModel` and `FittedAlphaModel` are `Protocol`s, so an implementation satisfies them
**structurally** -- `V2-P4-015`'s LightGBM model imports nothing from this module and this
module imports nothing from it. `runtime/seeding.py`'s guarded `numpy.random` hook is the
precedent, and `AlphaModelDeclaration.seed` is the field that feeds it. What travels across the
boundary is `float | None`, `str`, `int` and `datetime`: an implementation may hold arrays
internally and must hand back stdlib scalars, which is what lets the contract be validated,
hashed by `domain/_identity.py::stable_model_id`, and stored without any of those layers
growing a numeric import.

`V2-P4-015` cannot put its model under `backtest/` -- `backtest-studies-touch-no-store` forbids
`numpy` per module and the whole-package contract forbids `sklearn`, `scipy` and `pandas` -- so
it must choose a home outside it. Nothing here has to move when it does, which is the point of
a structural boundary; **which** home is `V2-P4-015`'s to argue.

"Per module" is the load-bearing half and `V2-P4-093` measured it: a *new* `backtest/*.py` is on
neither enumerated source list, so `import numpy` in one clears `lint-imports` at
`8 kept, 0 broken`. What refuses it is `tests/unit/test_import_layering.py::
test_the_two_backtest_study_contracts_cover_every_module_in_the_package`, which makes the new
file join both lists -- and the numpy import then breaks the contract it just joined. The
conclusion holds; the mechanism is two steps rather than one.

## What `fit` is given, and what `predict` returns

The panel plane is cross-sectional and every read of it is as-of sensitive, so neither side of
this contract takes a bare array:

- `fit` takes a `TrainingSet`: rows keyed by `domain/labels.py`'s `OutcomeLabel`, which is
  `(security, LabelWindow)` plus the realized return or every reason there is none.
  `V2-P1-017` built `LabelSample` and called it "the unit a supervised training set is built
  out of"; this is that sentence taken literally. A training example whose label carries
  refusals is **refused at construction**, because `OutcomeLabel.realized_return` raises rather
  than returning `0.0` for a halted window and a training set that quietly read zero there
  would teach a model that halts are flat.
- `predict` takes a `FeatureCrossSection`: one `as_of`, one row per security, values aligned to
  a declared feature list. `backtest/cross_section.py`'s `ComponentCrossSection` is the same
  shape for one factor, and is deliberately *not* reused: it lives under `backtest/`, which
  `storage/` may not reach, and it carries `clipped_subjects`, a winsorizer's business.
- `predict` returns a `PredictionBatch`: the artifact that produced it, the `as_of` it was read
  at, the `predicted_at` it was produced at, and one row per security -- **scored or abstained,
  never absent**. `prediction_batch_for` is what makes that structural.

## The one refusal this contract installs, and the one it does not

`PredictionBatch` refuses `as_of < artifact.training_cutoff`: a model trained through an instant
cannot be asked to predict from before it. The cutoff is the **latest session close any training
window closed on**, not the latest prediction day, because a label's information is not knowable
until its window closes -- taking the prediction day would claim a cutoff earlier than the data
the fit actually consumed. `LabelWindow.close_instant` is the bridge, so the comparison is
instant-to-instant and needs no zone of its own.

Equality is admitted: training through last night's close and predicting as of last night's
close is what a daily production model does. That makes this a **floor and not a purge**.
Overlapping labels need a gap, `overlapping_windows` is the input that measures one, and
`V2-P4-013` owns purge and embargo.

## What is deliberately left to a named issue

- **`V2-P4-016` landed**, and the address is `AlphaModelArtifact.artifact_id`:
  `stable_model_id(prefix="mdl", ...)` over every declared field of the artifact and of the
  declaration inside it, with an audited and **empty** `ARTIFACT_UNADDRESSED_FIELDS`. It is an
  addition and not a redesign, exactly as this contract predicted: no declared field moved, so
  nothing that was stored had to be re-keyed. A `PredictionBatch` still names its model *by
  value*, which is strictly more than an address, and that is now checkable rather than
  rhetorical -- a batch can produce the address without a lookup. `AlphaModelArtifact` is a
  pydantic `BaseModel` for exactly one reason: `stable_model_id` takes one, and this repository
  hashes a model through one function (`V2-P4-037` files the defect a second one would be). The
  three `schema_version` fields here are `Literal` constants and **not** `ContractVersions`
  registries: `domain/schema.py` exports the five stable v1 boundaries Implementation
  Decision 1 names, none of these is one of them, and a version registry earns its migration
  machinery when something has stored a row -- which is `V2-P4-017`'s. `FactorTransformSpec`
  is the precedent: a versioned contract with a `Literal` and no registry. **`V2-P4-017` has now
  answered it, and the answer is that these three keep their `Literal`s.** That issue stores a
  `PredictionRecord` wrapping this batch, and `read_versioned` dispatches on the payload's
  *top-level* `schema_version` only -- so one registry at the document root is the whole of what a
  chain can hang from, and `PREDICTION_RECORD_VERSIONS` is it. The consequence runs the other way
  and is recorded there: bumping any of these three is a bump of that record too.
- **`V2-P4-012`** owns the versioned feature matrix. `feature_version` here is a declared string
  and `feature_ids` is a declared list; the producer that reads the panel plane, resolves the
  universe and stamps the version is that issue's.
- **`V2-P4-013`** owns purge and embargo, and `TrainingSet.overlaps` is the measurement it needs.
- **`V2-P4-014`** owns the linear/ranking baseline. `backtest/alpha_model.py` ships a
  single-feature *reference*, which exists to prove this contract can be satisfied and driven.
- **`V2-P4-017` landed**, and "before the outcome is known" resolved to one instant:
  `build_label_window(as_of, horizon).close_instant(exit_day)`, the close of the very window the
  outcome label will be measured over. `domain/prediction_record.py` derives it from a calendar
  rather than accepting it, and `storage/predictions.py` stamps custody from its own clock -- so
  a caller who backdates `predicted_at` reaches `unwitnessed` and cannot reach `forward`. The
  backfill rule turned out not to need a store rule at all: a recomputation is produced at or
  after the deadline and a forward batch before it, `predicted_at` reaches the record's address
  through this batch, so the two **cannot** share a key and there is nothing for a write to
  overwrite.
- **`V2-P4-018`** owns the abstention vocabulary. `Prediction.abstention` is free text here so
  that S35's "stale 即弃权" can arrive as a coded reason without the shape moving.
- **`backtest/candidate_ranking.py`'s `CandidatePrediction.model_artifact_id`** was free text
  until `V2-P4-016` and now carries `ALPHA_MODEL_ARTIFACT_ID_PATTERN`. `V2-P4-005` wrote "by
  whatever identity `V2-P4-011` gives it", `V2-P4-010` decided the identity was `V2-P4-016`'s,
  and that issue's answer is one prefix's addresses and nothing else. `AlphaModelRef.artifact_id`
  was deliberately **not** narrowed with it, and
  `the_manifest_slot_still_admits_an_address_from_another_plane` says what that leaves open.
- **`V2-P4-017` took persistence**, and settled one of the two things this address does not: the
  storage form. `V2-P4-015` asked whether several hundred `(str, float)` rows want something other
  than the artifact's own tuple, and the answer is no -- not on the numbers (a whole store round
  trip at 5,545 securities is 4.9 ms against a 4.55 s fit, tabulated in `storage/predictions.py`)
  but on the identity: the bytes that store writes **are** the canonical JSON its address is taken
  over, so a second storage form would be a second canonicalisation. A digest over the *training
  rows* rather than their count is still nobody's
  (`the_address_is_over_the_fit_and_not_over_the_rule_that_chose_it`).
- **Filling `RunManifest.alpha_model_versions` is `V2-P4-021`'s, and it is done.**
  `V2-P4-010`'s docstring said `V2-P4-016` would, and that was wrong for a reason that issue
  could not fix: `ResearchEngine.run_cycle` builds the manifest and there is no `AlphaModel`
  anywhere on that path. `V2-P4-017` reached the same conclusion from the store side. The first
  thing in this repository to hold a fitted artifact and a run's identity at once is
  `model_view.run_daily`, and it writes the one line this docstring named --
  `AlphaModelRef(name=artifact.declaration.name, artifact_id=artifact.artifact_id)` -- into a
  `mode=daily` manifest. `model evaluate` deliberately writes none: it fits one artifact per
  fold and consumes none of them for a decision. No helper is shipped here either, for the
  reason this bullet always gave: `domain/run.py` importing this module to build one would put
  the whole label and calendar import weight of the model plane behind every `RunManifest`.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from types import MappingProxyType
from typing import Final, Literal, Protocol, Self, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field, computed_field, field_validator, model_validator

from openalpha_cn.domain._identity import stable_model_id
from openalpha_cn.domain.horizon import COUNTABLE_HORIZON_PATTERN, ResearchHorizon
from openalpha_cn.domain.labels import (
    LabelOverlap,
    LabelSample,
    OutcomeLabel,
    overlapping_windows,
)
from openalpha_cn.domain.time import ensure_aware

MAX_FEATURE_COUNT: Final[int] = 4096
"""How many features one declaration may align to.

A range check on a stored list rather than a modelling opinion, `FactorTransformSpec
.min_cross_section`'s precedent: the bound is far above anything `V2-P4-012` is likely to build
and its job is to keep a malformed list from reaching a fit at all.
"""

ALPHA_MODEL_ARTIFACT_PREFIX: Final[str] = "mdl"
"""`V2-P4-016`'s answer to the half of the address `V2-P4-010` left open: the prefix.

Three letters, `dec`/`run`/`sig`/`val`/`fct`'s shape, and **not already taken** -- measured across
all three of this repository's address builders (`stable_model_id`, `domain/factor.py`'s
`cross_section_digest` and its `set_digest`). The census stood at 26 call sites carrying 23
distinct prefixes when this was written; `V2-P4-017`'s `prd` has since made it 27 and 24, which
is a number this docstring deliberately does not have to be right about --
`tests/unit/domain/test_manifest_component_provenance.py::live_prefixes` reads them off the
source tree by AST rather than from a hand-written list, because the list that existed had gone
stale *and* was wrong about what it contained -- see that function for both.

A prefix is not decoration. Two content addresses over two different contracts can only be told
apart by it, and `AlphaModelRef.artifact_id` accepts any address this repository computed -- so
what stops a `fct_` factor address occupying the quantitative model slot is that a producer
stamps `mdl_`, which is the direction `V2-P4-010` chose deliberately and
`the_manifest_slot_still_admits_an_address_from_another_plane` records the residue of.
"""

ALPHA_MODEL_ARTIFACT_ID_PATTERN: Final[str] = rf"^{ALPHA_MODEL_ARTIFACT_PREFIX}_[0-9a-f]{{24}}$"
"""Exactly what `stable_model_id(prefix="mdl", ...)` produces, and nothing else.

Derived from the prefix above rather than spelled out a second time, `experiment_payload`'s rule
about canonicalisation applied to a pattern: a second spelling of one fact is a second thing that
can disagree about it. `domain/run.py`'s `RUN_MANIFEST_ID_PATTERN` is the same idea pinned to
`run_`, and its docstring carries the reason both exist -- a content address that is only
conventionally a content address stops being one the first time it is convenient.

Attached to `backtest/candidate_ranking.py`'s `CandidatePrediction.model_artifact_id`, which
`V2-P4-005` left as free text "by whatever identity `V2-P4-011` gives it" and `V2-P4-011` handed
here. It is **not** attached to `AlphaModelRef.artifact_id`: see that contract for why the
manifest's reference stays at the generic `CONTENT_ADDRESS_PATTERN`.
"""

ARTIFACT_UNADDRESSED_FIELDS: Final[Mapping[str, str]] = MappingProxyType({})
"""Every `AlphaModelArtifact` field that is **recorded but not addressed** -- and there are none.

`RUN_MANIFEST_UNADDRESSED_FIELDS`' shape, empty, and empty as a measurement rather than as a
default. That mapping holds five kinds of field: two wall clocks, a lifecycle status, in-flight
recovery bookkeeping and an observed host fact. This contract carries none of the five. Its one
`datetime` is `training_cutoff`, which is the latest instant a training window closed on -- a
property of the data the fit consumed, not of the clock the fit ran at -- and the field that
*would* be a wall clock, `PredictionBatch.predicted_at`, is deliberately on the batch and not
here. So there is nothing to exclude, and an artifact re-derived from the same training set on
another day addresses to the same string without anything having to be kept out.

Passed to `stable_model_id` rather than merely documented, so the mapping is load-bearing: adding
a key to it really does remove that field from the address, and
`tests/unit/domain/test_alpha_model_address.py` partitions `AlphaModelArtifact.model_fields` and
`AlphaModelDeclaration.model_fields` against it, so field *n+1* fails until somebody either
measures it moving the address or writes down why it may not. `V2-P4-013`, `V2-P4-014` and
`V2-P4-015` each named one more of Implementation Decision 11's eleven fields while this
contract stood still; without this audit the fifteenth field across the two models would have
joined the digest with nobody deciding.

`exclude` reaches only the top level, so a field added to `AlphaModelDeclaration` cannot be kept
out through this mapping at all -- it would need an exclusion set of its own, and nothing has
asked for one. The audit covers both models' field sets for exactly that reason.
"""


class AlphaModelError(ValueError):
    """Raised for a malformed training set, cross section, or prediction batch.

    A `ValueError` subclass to match `domain/labels.py`'s `LabelError` and
    `domain/horizon.py`'s `HorizonError`, so a caller catching `ValueError` around a contract
    boundary keeps catching this one. It is deliberately *not* what an abstention is: a model
    declining to score one security is an answer this contract carries
    (`Prediction.abstention`), not a malformed question.
    """


@dataclass(frozen=True, slots=True, kw_only=True)
class AlphaModelLimitation:
    """One named boundary on what this contract can be trusted to say."""

    code: str
    detail: str


def validate_feature_ids(feature_ids: Sequence[str], *, role: str) -> None:
    """Refuse a feature list that is empty, unsorted, repeated, blank or too long.

    Public rather than module-private, and `V2-P4-012` is why: the producer that builds a
    versioned feature matrix declares a column list before it has a cross section to put it on,
    and it has to be held to exactly this rule. A second copy of these five checks beside it
    would be one check plus a place for this one to fall behind -- which is the ground
    `V2-P4-011` deleted its own duplicated check on -- so the rule stays here, with one
    implementation, and `feature_matrix.FeatureSpec` re-raises its refusal in its own vocabulary.

    **Strictly increasing** rather than merely unique, and that is the load-bearing half: every
    row in this module is positional, so two cross sections carrying the same feature *set* in
    two orders would produce two different predictions from one fitted model, silently. Sorting
    is the cheapest way to make "same set" and "same order" the same statement, and it is what
    lets `AlphaModelArtifact.require_features` be an equality check.
    """
    if not feature_ids:
        raise AlphaModelError(
            f"{role} names no feature; a model fitted on nothing has no input to vary and "
            "every prediction it makes is the same number"
        )
    if len(feature_ids) > MAX_FEATURE_COUNT:
        raise AlphaModelError(
            f"{role} names {len(feature_ids)} features, above MAX_FEATURE_COUNT "
            f"({MAX_FEATURE_COUNT})"
        )
    for feature_id in feature_ids:
        if not feature_id.strip():
            raise AlphaModelError(f"{role} names a feature with a blank id")
    if list(feature_ids) != sorted(set(feature_ids)):
        raise AlphaModelError(
            f"{role} names {list(feature_ids)}, which is not strictly increasing; feature "
            "values travel positionally, so an unsorted or repeated list is two cross sections "
            "that agree on which features they carry and disagree on which column each is in"
        )


def _unsign_zero(value: float) -> float:
    """Collapse `-0.0` onto `0.0`, so one number has one canonical spelling (`V2-P4-016`).

    The narrowest fix for a real hole in a content address. `-0.0 == 0.0` is `True` and every
    arithmetic use of a coefficient in this repository treats them identically -- nothing divides
    by one or reads its `copysign` -- so two artifacts differing only in the sign of a zero are
    the *same* fitted model. `json.dumps` spells them `-0.0` and `0.0`, so before this they
    addressed apart: two objects that compare equal with two content addresses, which is the
    direction an address exists to rule out.

    Neither shipped model can produce one, measured rather than assumed: `_pearson`'s covariance
    is a `sum(...)` whose start value is the integer `0`, and `0 + -0.0` is `+0.0`; a tree's leaf
    value is `math.fsum(...) / len(members)`, and `math.fsum` of any number of `-0.0`s is `+0.0`.
    The hole is in the *contract* rather than in either implementation -- `AlphaModel` is a
    Protocol, `nothing_forces_an_implementation_through_the_builders` says a caller may hand-build
    an artifact, and `validate_parameters` admitted `-0.0` -- so it is closed where the contract
    is, not where today's two arithmetics happen not to reach it.

    Only the sign of zero. `1` and `1.0` are left alone on purpose: they compare equal too, and
    `test_a_declaration_keeps_each_hyperparameter_at_the_type_it_was_given` is a shipped decision
    that a declaration records what its author wrote. There the address is the stricter relation
    and is right to be.
    """
    return 0.0 if value == 0.0 else value


def _validate_values(
    values: Sequence[float | None], *, feature_ids: Sequence[str], role: str
) -> None:
    """Refuse a row that is misaligned or carries a value no arithmetic survives."""
    if len(values) != len(feature_ids):
        raise AlphaModelError(
            f"{role} carries {len(values)} value(s) against {len(feature_ids)} feature(s); a "
            "row whose length disagrees with the header is a row whose columns are unknown"
        )
    for feature_id, value in zip(feature_ids, values, strict=True):
        if value is not None and not math.isfinite(float(value)):
            raise AlphaModelError(
                f"{role} carries {value!r} for {feature_id}, which is not a finite number; a "
                "non-finite term poisons every sum and every ordering built on it. A feature "
                "that has no value is None, which this contract can abstain on"
            )


@dataclass(frozen=True, slots=True, kw_only=True)
class FeatureRow:
    """One security's feature values at one `as_of`, aligned positionally to a feature list.

    `None` rather than a sentinel number for a missing feature, `FactorObservation`'s rule:
    an imputed value is a decision and this contract is not where it is taken. What a model
    does with a `None` is its own -- the reference under `backtest/` abstains on a row that
    carries no value at all, which is the answer `Prediction.abstention` exists for.
    """

    ts_code: str
    values: tuple[float | None, ...]


@dataclass(frozen=True, slots=True, kw_only=True)
class FeatureCrossSection:
    """Every security's features at **one** `as_of` -- the shape `predict` is asked about.

    A plain frozen dataclass rather than a pydantic model, and the reason is Implementation
    Decision 31: *"禁止在面板查询路径上做逐行 pydantic 重建与 hash 重算"*. This is a panel query
    path -- a whole-market cross section is ~5,500 rows -- while `PredictionBatch` is a stored
    artifact that has to be validated when it is read back. Inputs are dataclasses, outputs are
    pydantic, and that split is a contract rather than a taste.

    `as_of` is refused unless it is timezone-aware, through `domain/time.py::ensure_aware` so
    that the rule has one implementation. It is **not** normalised to UTC: nothing stores this
    type, and aware datetimes compare by instant regardless of the zone they are written in.
    """

    as_of: datetime
    feature_ids: tuple[str, ...]
    rows: tuple[FeatureRow, ...]

    def __post_init__(self) -> None:
        try:
            ensure_aware(self.as_of)
        except ValueError as error:
            raise AlphaModelError(
                f"a feature cross section is dated {self.as_of!r}, which carries no offset; "
                "an as_of without a zone cannot say what was knowable when, which is the one "
                "thing a point-in-time read is for"
            ) from error
        validate_feature_ids(self.feature_ids, role="a feature cross section")
        if not self.rows:
            raise AlphaModelError(
                "a feature cross section carries no security; there is nothing to predict "
                "about and an empty batch would be indistinguishable from a refused one"
            )
        seen: set[str] = set()
        for row in self.rows:
            if not row.ts_code.strip():
                raise AlphaModelError("a feature cross section carries a row naming no security")
            if row.ts_code in seen:
                raise AlphaModelError(
                    f"a feature cross section carries {row.ts_code} twice at one as_of; two "
                    "feature rows for one security is two reads, and which of them was scored "
                    "is not recoverable"
                )
            seen.add(row.ts_code)
            _validate_values(row.values, feature_ids=self.feature_ids, role=row.ts_code)

    @property
    def subjects(self) -> tuple[str, ...]:
        """Every security this cross section carries, in the order its rows arrived."""
        return tuple(row.ts_code for row in self.rows)

    def value(self, *, ts_code: str, feature_id: str) -> float | None:
        """One cell, by name in both directions, or `AlphaModelError`.

        Positional alignment is what this contract stores and what a fit consumes; a named
        lookup is what a test and a diagnostic need, and writing it once here is what keeps
        every caller from re-deriving the column index and getting it wrong somewhere.
        """
        try:
            column = self.feature_ids.index(feature_id)
        except ValueError as error:
            raise AlphaModelError(
                f"{feature_id!r} is not one of this cross section's features "
                f"({list(self.feature_ids)})"
            ) from error
        for row in self.rows:
            if row.ts_code == ts_code:
                return row.values[column]
        raise AlphaModelError(f"{ts_code!r} is not one of this cross section's securities")


@dataclass(frozen=True, slots=True, kw_only=True)
class TrainingExample:
    """One labelled observation: what was knowable, and what happened next.

    The label is a whole `OutcomeLabel` rather than a bare target float, and that carries three
    things a float cannot: which security and window it is about (`LabelSample`'s pair), the
    horizon the window spans, and the session the window closes on -- which is what
    `TrainingSet.training_cutoff` is measured from. An unlabelled window is refused here rather
    than dropped silently, so a caller that assembled one has to say what it means to do with
    it.
    """

    label: OutcomeLabel
    features: tuple[float | None, ...]

    def __post_init__(self) -> None:
        if not self.label.is_labelled:
            raise AlphaModelError(
                f"a training example carries an unlabelled window: {self.label.refusal_summary}. "
                "A refused window has no target -- OutcomeLabel.realized_return raises rather "
                "than returning 0.0 for exactly this reason, since a supervised target that "
                "silently reads zero for every halted name teaches the model that halts are flat"
            )

    @property
    def ts_code(self) -> str:
        """The security this example is about."""
        return self.label.ts_code

    @property
    def sample(self) -> LabelSample:
        """The `(security, window)` pair, in the type `overlapping_windows` takes."""
        return LabelSample(ts_code=self.label.ts_code, window=self.label.window)

    @property
    def target(self) -> float:
        """The realized adjusted return over this example's window."""
        return self.label.realized_return


@dataclass(frozen=True, slots=True, kw_only=True)
class TrainingSet:
    """What `fit` is given: one feature list, and labelled rows aligned to it.

    Four refusals at construction. The first is that an empty set has no cutoff and no
    parameters; the other three each close a way a training set can be wrong without *looking*
    wrong:

    - **misalignment** -- a row whose value count disagrees with `feature_ids`;
    - **two horizons** -- a set mixing `5d` and `10d` windows is two questions fitted as one,
      and the artifact could only record one of them;
    - **one security twice on one prediction day** -- `overlapping_windows` refuses that exact
      shape, and it is refused here too, cheaply, because a purge can only drop samples from
      one side of a split and this is not repairable by dropping.

    Ordinary overlap -- two prediction days one session apart sharing sessions -- is **not**
    refused: `LabelOverlap`'s docstring says why it is the ordinary shape of a daily-frequency
    label set. `overlaps` reports it, and `V2-P4-013` is what acts on it.
    """

    feature_ids: tuple[str, ...]
    examples: tuple[TrainingExample, ...]

    def __post_init__(self) -> None:
        validate_feature_ids(self.feature_ids, role="a training set")
        if not self.examples:
            raise AlphaModelError(
                "a training set carries no example; a fit over nothing produces a model whose "
                "training cutoff is undefined and whose parameters came from no data"
            )
        horizons = {example.label.window.horizon for example in self.examples}
        if len(horizons) > 1:
            raise AlphaModelError(
                f"a training set mixes horizons {sorted(horizon.text for horizon in horizons)}; "
                "a five-session target and a ten-session target are answers to different "
                "questions, and an artifact fitted on both could declare only one of them"
            )
        seen: set[tuple[str, date]] = set()
        for example in self.examples:
            _validate_values(example.features, feature_ids=self.feature_ids, role=example.ts_code)
            key = (example.ts_code, example.label.window.prediction_day)
            if key in seen:
                raise AlphaModelError(
                    f"{example.ts_code} carries two examples on "
                    f"{example.label.window.prediction_day.isoformat()}; that is either one "
                    "feature row with two targets or one horizon whose sessions moved, and "
                    "neither is repairable by dropping samples from one side of a split"
                )
            seen.add(key)

    @property
    def horizon(self) -> ResearchHorizon:
        """The one horizon every example's window spans."""
        return self.examples[0].label.window.horizon

    @property
    def training_cutoff(self) -> datetime:
        """The latest session close any example's outcome window closed on.

        The **exit** session and not the prediction day: a label's number is not knowable until
        its window closes, so a cutoff taken from prediction days would claim the fit stopped
        earlier than the information it actually consumed. `LabelWindow.close_instant` dates it
        in the window's own zone, which is what makes this comparable to a `FeatureCrossSection
        .as_of` without this contract owning a calendar or a zone of its own.
        """
        return max(
            example.label.window.close_instant(example.label.window.exit_day)
            for example in self.examples
        )

    @property
    def samples(self) -> tuple[LabelSample, ...]:
        """Every `(security, window)` pair, in the order the examples arrived."""
        return tuple(example.sample for example in self.examples)

    @property
    def overlaps(self) -> tuple[LabelOverlap, ...]:
        """Every pair of one security's windows that share a session -- `V2-P4-013`'s input.

        Computed on demand rather than at construction: it is quadratic in the number of
        samples per security, and a training set is built far more often than it is purged.
        """
        return overlapping_windows(self.samples)


class AlphaModelDeclaration(BaseModel):
    """What a model declares before it is fitted -- everything a reader needs but the data.

    A pydantic model rather than a dataclass because `V2-P4-016` addresses it through
    `domain/_identity.py::stable_model_id`, which takes a `BaseModel`. Every field here is one
    Implementation Decision 11 names -- but **not** every field D11 names is here, and the gap
    is enumerated in `d11_names_eleven_things_and_this_artifact_carries_seven`.

    **Every field here reaches the artifact's address, and there is no way to keep one out.**
    `stable_model_id`'s `exclude` reaches only the top level, so `ARTIFACT_UNADDRESSED_FIELDS`
    cannot name anything on this model; a field that had to be recorded and not addressed would
    need an exclusion set of its own, and nothing has asked for one.
    `tests/unit/domain/test_alpha_model_address.py` holds this field set against a per-field
    sweep, so a ninth field fails until it is measured to move the address.

    `name` and `family` are two facts, not one spelling of one. `name` is the handle a
    declaration's author chose (`"momentum_5d_ridge"`); `family` is fixed by the implementation
    that will be asked to fit it (`"linear"`, `"lightgbm"`), and it is what tells a reader that
    two differently-named declarations went through the same code path -- which `code_commit`
    cannot say, because one commit carries every family. `V2-P4-010`'s objection to
    `AgentVersion.version` does not apply: that field's value never varied, and both of these
    do.

    `hyperparameters` are **flat scalars**, sorted by key. A nested structure is refused rather
    than stringified: canonical JSON over a scalar list is one line per parameter in a stored
    artifact and in a diff, and a hyperparameter a reader cannot see there is one nobody checks.
    `V2-P4-015` is the first issue that could need otherwise, and widening this is its call.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    schema_version: Literal["alpha-model/v1"] = "alpha-model/v1"
    name: str = Field(min_length=1, max_length=128)
    family: str = Field(min_length=1, max_length=128)
    horizon: str = Field(pattern=COUNTABLE_HORIZON_PATTERN)
    feature_version: str = Field(min_length=1, max_length=128)
    seed: int = Field(ge=0)
    code_commit: str = Field(min_length=7, max_length=64)
    hyperparameters: tuple[tuple[str, bool | int | float | str], ...] = ()

    @field_validator("hyperparameters")
    @classmethod
    def validate_hyperparameters(
        cls, value: tuple[tuple[str, bool | int | float | str], ...]
    ) -> tuple[tuple[str, bool | int | float | str], ...]:
        keys = [key for key, _item in value]
        if list(keys) != sorted(set(keys)):
            raise ValueError(
                f"hyperparameters name {keys}, which is not strictly increasing; a repeated key "
                "is one parameter stated twice and the two can disagree, and an unsorted list "
                "gives one declaration two canonical spellings and therefore two addresses"
            )
        for key, item in value:
            if not key.strip():
                raise ValueError("a hyperparameter carries a blank name")
            if isinstance(item, float) and not math.isfinite(item):
                raise ValueError(f"hyperparameter {key!r} carries {item!r}, which is not finite")
        return tuple(
            (key, _unsign_zero(item) if isinstance(item, float) else item) for key, item in value
        )


class AlphaModelArtifact(BaseModel):
    """What a fit produced: the declaration, what the training set was, and what was learned.

    Carries **no declared id** and one computed one, which is `V2-P4-010`'s decision and
    `V2-P4-011`'s framing both kept: the field set below is byte-identical to what that issue
    shipped, and `artifact_id` is a `computed_field` that `stable_model_id
    (exclude_computed_fields=True)` is built to ignore, so the address cannot depend on itself
    and no stored payload had to move for it. The seven things Implementation Decision 11 names
    that belong to a *fit* are all reachable here -- training cutoff, horizon, feature version,
    parameters, seed, code version and now the content hash -- and the four it names that do not
    are absent by issue, not by oversight: see
    `d11_names_eleven_things_and_this_artifact_carries_seven` for where each went, including why
    the split policy and the metrics are on the fold and the evaluation rather than here.

    `feature_ids` and `training_example_count` are not on D11's list and are here anyway,
    because `artifact_for` measures them off the training set. They are what makes
    `require_features` possible and what makes two folds of one declaration distinguishable
    before anybody has computed an address.

    `parameters` is what `fit` learned, and it is here rather than inside the implementation for
    the same reason: an artifact whose coefficients live only in a Python object is one nobody
    can re-derive, compare, or re-address.
    `tests/unit/backtest/test_alpha_model_reference.py` proves the reference model can be
    rebuilt from this artifact alone and reproduce every prediction.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    schema_version: Literal["alpha-model-artifact/v1"] = "alpha-model-artifact/v1"
    declaration: AlphaModelDeclaration
    feature_ids: tuple[str, ...]
    training_cutoff: datetime
    training_example_count: int = Field(ge=1)
    parameters: tuple[tuple[str, float], ...] = ()

    @field_validator("training_cutoff")
    @classmethod
    def normalize_training_cutoff(cls, value: datetime) -> datetime:
        return ensure_aware(value)

    @field_validator("feature_ids")
    @classmethod
    def validate_artifact_feature_ids(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        validate_feature_ids(value, role="an alpha model artifact")
        return value

    @field_validator("parameters")
    @classmethod
    def validate_parameters(
        cls, value: tuple[tuple[str, float], ...]
    ) -> tuple[tuple[str, float], ...]:
        keys = [key for key, _item in value]
        if list(keys) != sorted(set(keys)):
            raise ValueError(
                f"parameters name {keys}, which is not strictly increasing; an unsorted or "
                "repeated list gives one fitted model two canonical spellings"
            )
        for key, item in value:
            if not math.isfinite(item):
                raise ValueError(f"parameter {key!r} carries {item!r}, which is not finite")
        return tuple((key, _unsign_zero(item)) for key, item in value)

    @computed_field(return_type=str)  # type: ignore[prop-decorator]
    @property
    def artifact_id(self) -> str:
        """Return this fit's content address (`V2-P4-016`).

        `stable_model_id(prefix="mdl", ...)` over every field below and every field of the
        declaration inside it, less `ARTIFACT_UNADDRESSED_FIELDS`, which is empty and says why.
        The digest inputs were chosen by measurement, not by reading Implementation Decision 11
        down the page: `tests/unit/backtest/test_artifact_address_collisions.py` tries five
        candidate definitions against artifacts fitted on `V2-P4-013`'s real folds and each one
        but this collides on a real case -- the declaration alone cannot tell two folds apart,
        dropping `parameters` cannot tell a model that learned `-0.75` from one that learned
        `0.0` on the same thirty-two rows, and dropping `training_example_count` cannot tell a
        fit on twenty-four rows from one on sixteen with the same cutoff.

        **What is not here is as decided as what is.** Implementation Decision 11's *split
        policy* and *metrics* fields are both absent, and neither is an oversight:

        - A **split policy** is how the training rows were chosen, and this address is over what
          the fit consumed. The half of a `WalkForwardFold` that changes the fit -- the purge and
          the embargo -- reaches this digest already, through the `training_cutoff` and
          `training_example_count` it left behind. The half that does not, the test block, is not
          an input to the fit at all: two folds differing only in `test_day_count` produce
          **byte-identical** artifacts (measured), so addressing the policy would give one fitted
          model two addresses, which is the failure direction this repository has paid for before
          (`FactorInputRef.fetched_at`). `V2-P4-014`'s `FoldEvaluation` already carries
          `first_test_day` beside the artifact, which is where a reader looks for the block.
        - A **metric** is a measurement *of* this artifact taken on rows it never trained on, so
          putting one here would make the identity of a fit depend on how it was later judged --
          and `FoldEvaluation` carries the artifact by value, so the artifact would end up
          containing the numbers that contain it. `V2-P3-014`'s split, reused: the declaration
          gets an id and the answer gets its own.

        Not cached, and `computed_field`s in this repository are not: ADR-0003 records the defect
        of reading one inside a per-security loop. Measured here at **0.017 ms** for the rank
        baseline's three parameters and **0.399 ms** for `V2-P4-015`'s 900-node encoded ensemble
        -- linear in the table and four orders below that model's 4.55 s fit -- so `V2-P4-015`'s
        question of whether hundreds of `(str, float)` rows want a different storage form is not
        the digest's to answer. Read it once outside a loop anyway, `panel_factors.py`'s rule.
        """
        return stable_model_id(
            prefix=ALPHA_MODEL_ARTIFACT_PREFIX,
            model=self,
            exclude=frozenset(ARTIFACT_UNADDRESSED_FIELDS),
        )

    def require_features(self, cross_section: FeatureCrossSection) -> None:
        """Refuse a cross section whose feature list is not this artifact's, by name.

        An equality check and not a subset one: the values travel positionally, so a cross
        section carrying an extra column shifts every feature after it, and one missing a
        column shifts them the other way. Both produce a number, which is what makes this
        worth refusing rather than tolerating.
        """
        if cross_section.feature_ids != self.feature_ids:
            missing = sorted(set(self.feature_ids) - set(cross_section.feature_ids))
            extra = sorted(set(cross_section.feature_ids) - set(self.feature_ids))
            raise AlphaModelError(
                f"{self.declaration.name} was fitted on {list(self.feature_ids)} and is asked "
                f"about {list(cross_section.feature_ids)} (missing {missing}, extra {extra}); "
                "feature values travel positionally, so a different list is a different model"
            )


def artifact_for(
    *,
    declaration: AlphaModelDeclaration,
    training_set: TrainingSet,
    parameters: tuple[tuple[str, float], ...] = (),
) -> AlphaModelArtifact:
    """Build an artifact whose three measured fields come from the training set, not the caller.

    `feature_ids`, `training_cutoff` and `training_example_count` are derived here rather than
    accepted, which is the difference between an artifact that records what the fit consumed and
    one that records what somebody typed. The declaration's horizon is checked against the
    training set's: a declaration that says `5d` fitted on `10d` windows is a model whose stated
    question is not the one its data answered, and `V2-P4-017` would store that number under the
    wrong horizon forever.
    """
    if declaration.horizon != training_set.horizon.text:
        raise AlphaModelError(
            f"{declaration.name} declares horizon {declaration.horizon!r} and its training set "
            f"carries {training_set.horizon.text!r} windows; the artifact can record only one, "
            "and a prediction stored under a horizon its fit never saw is unfalsifiable"
        )
    return AlphaModelArtifact(
        declaration=declaration,
        feature_ids=training_set.feature_ids,
        training_cutoff=training_set.training_cutoff,
        training_example_count=len(training_set.examples),
        parameters=parameters,
    )


class Prediction(BaseModel):
    """One security's forward number, or the stated reason there is none.

    Exactly one of `score` and `abstention`, never both and never neither. S35 asks for failed
    or stale models to abstain **explicitly**, and the shape that makes that possible has to
    exist before `V2-P4-018` fills in the vocabulary -- a contract that grew the field later
    would have had to grow the rule later too, which is `CandidatePrediction`'s own argument one
    plane up. Scoring `0.0` instead is the defect `OutcomeLabel.realized_return` refuses on the
    label side: a zero is a number, and a batch full of them is indistinguishable from a model
    that had an opinion.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    ts_code: str = Field(min_length=1, max_length=32)
    score: float | None = None
    abstention: str | None = Field(default=None, min_length=1, max_length=256)

    @field_validator("score")
    @classmethod
    def normalize_score(cls, value: float | None) -> float | None:
        """Collapse `-0.0` onto `0.0`, the third and last addressed float to get this.

        `V2-P4-016` closed the hole on `AlphaModelArtifact.parameters` and
        `AlphaModelDeclaration.hyperparameters` and this field was left out, which `V2-P4-093`
        measured: a score reaches an address too, through `PredictionBatch` and the
        `PredictionRecord` that carries one by value, so two batches that compared equal dumped
        two payloads and were filed under two `record_id`s -- one prediction with two names.

        **Not latent, which is a correction of what `V2-P4-093` was filed as.** That issue read
        "no shipped implementation produces the pair"; measured, `backtest/alpha_model.py`'s
        `predict` is `sign * (float(value) - centre)`, its `fit` learns `sign = -1.0` whenever
        the below-centre group realized the higher mean target, and `-1.0 * 0.0` is `-0.0`. So
        any security whose declared feature lands exactly on the learned centre under a negative
        sign is one the shipped reference model hands `-0.0`
        (`test_a_security_on_the_learned_centre_under_a_negative_sign_scores_positive_zero`). A
        float hitting a training mean exactly is a coincidence on a real panel rather than a
        certainty, so the *frequency* the issue guessed at was fair; the *source* was not.
        `_unsign_zero`'s own docstring is the argument for why only the sign of zero is touched.
        """
        return None if value is None else _unsign_zero(value)

    @model_validator(mode="after")
    def validate_exactly_one_answer(self) -> Self:
        if (self.score is None) == (self.abstention is None):
            raise ValueError(
                f"{self.ts_code} carries score={self.score!r} and abstention="
                f"{self.abstention!r}; a prediction is either a number or a stated reason there "
                "is none, and a row carrying both is two answers while a row carrying neither "
                "is a security the batch silently dropped"
            )
        if self.score is not None and not math.isfinite(self.score):
            raise ValueError(
                f"{self.ts_code} carries score={self.score!r}, which is not a finite number; "
                "abstain instead, which says so where a reader can see it"
            )
        return self

    @property
    def is_scored(self) -> bool:
        """Whether this row carries a number."""
        return self.score is not None


class PredictionBatch(BaseModel):
    """An immutable, timestamped set of forward numbers over one cross section.

    Implementation Decision 10's "不可变预测批次", and Story S32's "persisted before outcomes are
    known" is what every field here is for. Frozen, so a stored batch cannot be edited into
    agreement with what happened; `predicted_at` separate from `as_of`, so a reader can tell
    when it was produced from what it was produced about; and `artifact` **by value**, so the
    fit that produced it is recoverable without a lookup and without an address `V2-P4-016` has
    not defined yet.

    Two refusals, and they are two different mistakes:

    - `as_of < artifact.training_cutoff` -- the fit consumed an outcome realized after the
      instant the prediction claims to stand at. Leakage, and the floor `V2-P4-013` widens.
    - `predicted_at < as_of` -- the batch was produced before the features it read were
      readable.

    Rows are sorted by `ts_code` and every security appears once, so two batches over one
    universe are comparable field by field -- which is what `V2-P4-016` will address and
    `V2-P4-017` will diff.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    schema_version: Literal["alpha-prediction-batch/v1"] = "alpha-prediction-batch/v1"
    as_of: datetime
    predicted_at: datetime
    artifact: AlphaModelArtifact
    predictions: tuple[Prediction, ...]

    @field_validator("as_of", "predicted_at")
    @classmethod
    def normalize_instants(cls, value: datetime) -> datetime:
        return ensure_aware(value)

    @field_validator("predictions")
    @classmethod
    def validate_predictions(cls, value: tuple[Prediction, ...]) -> tuple[Prediction, ...]:
        if not value:
            raise ValueError(
                "a prediction batch carries no row; an empty batch is indistinguishable from a "
                "model that was never asked, and there is nothing for V2-P4-017 to store"
            )
        codes = [item.ts_code for item in value]
        if codes != sorted(set(codes)):
            raise ValueError(
                f"a prediction batch names {codes}, which is not strictly increasing; a "
                "repeated security is two forward numbers about one name, and an unsorted "
                "batch gives one answer two canonical spellings"
            )
        return value

    @model_validator(mode="after")
    def validate_the_batch_stands_after_what_it_was_fitted_on(self) -> Self:
        if self.as_of < self.artifact.training_cutoff:
            raise ValueError(
                f"this batch is dated {self.as_of.isoformat()} and its model was fitted through "
                f"{self.artifact.training_cutoff.isoformat()}; the fit consumed an outcome that "
                "was realized after the instant this prediction claims to stand at. Equality is "
                "admitted -- training through last night's close and predicting as of it is what "
                "a daily model does -- so this is a leakage floor and not a purge; V2-P4-013 "
                "owns the embargo that widens it"
            )
        if self.predicted_at < self.as_of:
            raise ValueError(
                f"this batch was produced at {self.predicted_at.isoformat()}, before the "
                f"{self.as_of.isoformat()} features it read were readable"
            )
        return self

    @property
    def horizon(self) -> str:
        """The horizon these numbers are about, from the artifact's declaration."""
        return self.artifact.declaration.horizon

    @property
    def subjects(self) -> tuple[str, ...]:
        """Every security this batch answers about, scored or abstained."""
        return tuple(item.ts_code for item in self.predictions)

    @property
    def scored(self) -> tuple[Prediction, ...]:
        """The rows carrying a number."""
        return tuple(item for item in self.predictions if item.is_scored)

    @property
    def abstained(self) -> tuple[Prediction, ...]:
        """The rows carrying a stated reason there is no number."""
        return tuple(item for item in self.predictions if not item.is_scored)


def prediction_batch_for(
    *,
    artifact: AlphaModelArtifact,
    cross_section: FeatureCrossSection,
    predicted_at: datetime,
    predictions: Iterable[Prediction],
) -> PredictionBatch:
    """Assemble a batch that answers about **every** security the cross section carried.

    The coverage check is the reason this function exists rather than each implementation
    calling `PredictionBatch(...)` itself. `PredictionBatch` cannot make it: it never sees the
    cross section, so a model that quietly dropped the names it found hard would produce a
    batch that validates. Dropping is exactly what `Prediction.abstention` is for, and the
    difference between abstaining and dropping is the difference between a disclosed refusal and
    an invisible one -- `ScoreCoverage.incomplete_components`' argument, one plane down.

    It is also the **only** place `artifact.require_features` runs on the driven path, and that
    is a deliberate reduction: the reference implementation used to call it too, and a mutation
    sweep measured that the copy here could be deleted with the whole suite still green, because
    the other one had already refused every mismatched cross section a test drove. Two copies of
    a check are one check plus a place for a future implementation to skip it, so the surviving
    copy is the one every implementation goes through rather than the one each has to remember.
    """
    artifact.require_features(cross_section)
    rows = tuple(sorted(predictions, key=lambda item: item.ts_code))
    answered = {item.ts_code for item in rows}
    offered = set(cross_section.subjects)
    unanswered = sorted(offered - answered)
    uninvited = sorted(answered - offered)
    if unanswered or uninvited:
        raise AlphaModelError(
            f"{artifact.declaration.name} was offered {len(offered)} securities and answered "
            f"about {len(answered)}: {unanswered} carry no row and {uninvited} were never in "
            "the cross section. A security a model found no answer for abstains, which a "
            "reader can see; a security it dropped is invisible"
        )
    return PredictionBatch(
        as_of=cross_section.as_of,
        predicted_at=predicted_at,
        artifact=artifact,
        predictions=rows,
    )


@runtime_checkable
class AlphaModel(Protocol):
    """The quantitative model boundary: a declaration, and a fit that produces a fitted model.

    `fit` returns a **new** object rather than mutating and returning `self`, and that is what
    makes `V2-P4-013` possible: a walk-forward evaluation fits one declaration once per fold,
    and folds that shared one mutable object would share one artifact -- so `V2-P4-016` could
    not address them apart and `V2-P4-017` would store K predictions against one training
    cutoff. `tests/unit/backtest/test_alpha_model_reference.py` measures that two folds produce two
    distinct artifacts.

    A `Protocol`, not a base class: `V2-P4-015`'s LightGBM implementation must live where
    `numpy` is legal, which is nowhere `domain/` may import from, and structural typing is what
    lets it satisfy this without either module importing the other. `ModelProvider` is the same
    idea for the LLM plane and stays exactly where it is.
    """

    @property
    def declaration(self) -> AlphaModelDeclaration:
        """What this model declares before it is fitted."""

    def fit(self, training_set: TrainingSet) -> FittedAlphaModel:
        """Fit this declaration on labelled rows, returning a new fitted model."""


@runtime_checkable
class FittedAlphaModel(Protocol):
    """A model that has been fitted, and can therefore be addressed and asked.

    Separate from `AlphaModel` so that "you cannot predict from a model nobody fitted" is a
    typing fact rather than a runtime check, and so that the thing `V2-P4-016` content-addresses
    and `V2-P4-017` stores has a name of its own.

    `predicted_at` is a parameter and not a clock this reads for itself: a prediction batch's
    timestamp is part of what S32 asks to be persisted before the outcome is known, and a hidden
    `datetime.now()` would make every batch unreproducible and every test order-dependent.
    """

    @property
    def artifact(self) -> AlphaModelArtifact:
        """Everything the fit recorded: the declaration, the training set, the parameters."""

    def predict(
        self, cross_section: FeatureCrossSection, *, predicted_at: datetime
    ) -> PredictionBatch:
        """Answer about every security in `cross_section`, scored or abstained."""


KNOWN_ALPHA_MODEL_LIMITATIONS: Final[tuple[AlphaModelLimitation, ...]] = (
    AlphaModelLimitation(
        code="the_address_is_over_the_fit_and_not_over_the_rule_that_chose_it",
        detail=(
            "V2-P4-016 addresses AlphaModelArtifact by its whole content: the declaration, the "
            "feature list, the training cutoff, the row count and the fitted parameters. What "
            "that does NOT identify is which rule selected those rows. The artifact records how "
            "many training examples there were and not which ones, so two different training "
            "sets of the same size, ending at the same cutoff, that a given model fits to the "
            "same parameters share one address -- and two walk-forward folds whose purge and "
            "embargo differ but whose surviving rows do not are correctly one artifact, which is "
            "the same fact seen from the useful side. Closing it would mean putting a digest of "
            "the training rows on the artifact, which is a field rather than a computed value "
            "and belongs to whichever issue first stores a training set. This entry named "
            "V2-P4-017 and that was wrong by one object: that issue stores a PredictionRecord, "
            "which carries the batch and through it the artifact, and never the rows the fit "
            "consumed. So the owner is still open, and the next issue to persist a TrainingSet "
            "inherits it rather than any issue on this chain having quietly closed it. What is "
            "structural today is that everything the fit consumed which the artifact does record "
            "reaches the address, measured field by field in "
            "tests/unit/domain/test_alpha_model_address.py."
        ),
    ),
    AlphaModelLimitation(
        code="the_manifest_slot_still_admits_an_address_from_another_plane",
        detail=(
            "RunManifest.alpha_model_versions holds AlphaModelRefs whose artifact_id is bound to "
            "CONTENT_ADDRESS_PATTERN -- any address this repository computed -- rather than to "
            "ALPHA_MODEL_ARTIFACT_ID_PATTERN, so a fct_ factor address or an rnk_ ranking "
            "address validates in the quantitative model slot. That is V2-P4-010's decision and "
            "V2-P4-016 left it standing rather than narrowing it, for a layering reason: "
            "domain/run.py would have to import domain/alpha_model.py to name the narrower "
            "pattern, which puts the whole label/adjustment/calendar import weight of the model "
            "contract behind every RunManifest, or it would have to spell the pattern a second "
            "time, which is what this repository refuses everywhere else. "
            "CandidatePrediction.model_artifact_id IS narrowed, because backtest/ may already "
            "import domain/alpha_model.py and a prediction can only have come from one kind of "
            "artifact. So what keeps the manifest slot honest is that a producer stamps mdl_. "
            "There is now exactly one producer -- model_view.run_daily, V2-P4-021's daily face, "
            "which reads artifact_id off an AlphaModelArtifact and can therefore only stamp "
            "mdl_ -- and the slot is still () on every other path, including every "
            "ResearchEngine.run_cycle. So the pattern is as wide as it always was and nothing "
            "in this build exercises the width; a second producer on another plane would."
        ),
    ),
    AlphaModelLimitation(
        code="a_seed_in_the_address_is_read_by_no_model_in_this_build",
        detail=(
            "AlphaModelDeclaration.seed is Implementation Decision 11's field and it reaches the "
            "content address, so two declarations that differ only in it address apart. No model "
            "in this repository reads it: backtest/alpha_model.py, backtest/alpha_baseline.py "
            "and backtest/alpha_tree.py each say 'carried and unused' because none of the three "
            "draws a number. Measured in "
            "tests/unit/backtest/test_artifact_address_collisions.py -- two seeds, "
            "byte-identical coefficients, two addresses -- so today the seed separates "
            "declarations without separating fits. This is V2-P0B-009's seam F87 one plane down: "
            "that issue built the mechanism (runtime/seeding.py really threads "
            "request.random_seed into every registered source) and honestly recorded that no "
            "production component consumes it. The address is right to carry it -- the first "
            "stochastic model makes it load-bearing with no contract change -- and until then it "
            "is a declared input rather than an observed one."
        ),
    ),
    AlphaModelLimitation(
        code="an_unknown_code_commit_is_one_constant_shared_by_every_build_that_has_none",
        detail=(
            "AlphaModelDeclaration.code_commit reaches the address, and V2-P0B-009 made the "
            "value real: resolve_code_commit() returns git rev-parse HEAD, with a literal "
            "'-dirty' suffix when the workspace is not clean, and falls back to a build-time "
            "stamp. Its bottom tier is UNKNOWN_CODE_COMMIT, a single constant returned for every "
            "wheel installed with no build stamp and no .git -- so two genuinely different "
            "builds addressed under it produce the same code version and, given the same data, "
            "the same artifact address. That is honest rather than plausible, which is the whole "
            "difference from the 'baseline/v1' V2-P4-010 found in the model slot, and it is "
            "still a real limit on what an mdl_ address proves. Nothing in domain/ can close it: "
            "domain-purity forbids the edge into runtime/, so the commit is a declared input a "
            "producer supplies, and this contract cannot check that it supplied a real one."
        ),
    ),
    AlphaModelLimitation(
        code="d11_names_eleven_things_and_this_artifact_carries_seven",
        detail=(
            "Implementation Decision 11 requires a model artifact to record the training "
            "cutoff, the target and horizon, the universe, the feature version, the "
            "preprocessing, the split policy, the parameters, the seed, the code version, the "
            "metrics and the content hash. AlphaModelArtifact carries seven of those eleven -- "
            "cutoff, horizon, feature version, parameters (declared and fitted), seed, code "
            "commit and, since V2-P4-016, the content hash -- plus two D11 does not name "
            "(feature_ids and training_example_count, both measured off the training set by "
            "artifact_for). The other four are each owned elsewhere and are absent rather than "
            "stubbed: the universe version and the preprocessing belong to the feature matrix "
            "and are V2-P4-012's (Story S26 names both, and a preprocessing policy is already "
            "content-addressed by FactorTransformSpec.transform_id, while feature_version is "
            "now a stable_model_id over both); the split policy and the metrics were left here "
            "by V2-P4-013 and V2-P4-014 respectively, and V2-P4-016 placed both OUTSIDE this "
            "contract on measurement rather than on taste -- a test block is not an input to a "
            "fit (two folds differing only in test_day_count produce byte-identical artifacts) "
            "and a metric is a judgement of the artifact taken on rows it never trained on, so "
            "FoldEvaluation carries first_test_day and every number beside the artifact it "
            "measured. The target is not a field because this repository has exactly one -- "
            "OutcomeLabel.realized_return -- so a slot for it would record a constant, which is "
            "the objection V2-P4-010 raised against AgentVersion.version."
        ),
    ),
    AlphaModelLimitation(
        code="the_feature_version_is_a_name_this_contract_cannot_check",
        detail=(
            "AlphaModelDeclaration.feature_version is a declared string and feature_ids is a "
            "declared list. Nothing here reads the panel plane, resolves a universe, or knows "
            "what a feature is made of -- V2-P4-012 owns the versioned feature matrix and "
            "Story S26 is its acceptance. So this contract can refuse a cross section whose "
            "feature list is not the fitted one (require_features) and cannot refuse one whose "
            "columns carry the same names computed a different way. That is the distinction "
            "V2-P4-010 drew between a name and a digest, sitting on the feature plane instead "
            "of the model plane. V2-P4-012 closed it there and not here: feature_matrix."
            "FeatureSpec.feature_version is a stable_model_id over the columns' content "
            "addresses and the preprocessing policy, and feature_matrix."
            "require_declared_features is the check at the join. This field stays a free "
            "string on purpose -- narrowing it to CONTENT_ADDRESS_PATTERN would refuse a model "
            "whose features came from somewhere that producer is not -- so the sentence above "
            "is still true of this contract, and a declaration that never meets a matrix is "
            "still one nobody can check."
        ),
    ),
    AlphaModelLimitation(
        code="nothing_forces_an_implementation_through_the_builders",
        detail=(
            "artifact_for derives feature_ids, training_cutoff and training_example_count from "
            "the TrainingSet, and prediction_batch_for checks that every offered security was "
            "answered about. Both are free functions, and AlphaModelArtifact and "
            "PredictionBatch are constructible directly, so an implementation can state a "
            "training cutoff its data does not support or answer about a subset. This is "
            "ComponentCrossSection.clipped_subjects' disclosure applied to a fit: a caller can "
            "hand-build the carrier and stamp it. What is structural is that a batch built "
            "either way is refused if its as_of precedes the cutoff it declares, so a lie about "
            "the cutoff has to be a lie in the direction that makes the model look worse."
        ),
    ),
    AlphaModelLimitation(
        code="the_leakage_floor_is_not_a_purge_or_an_embargo",
        detail=(
            "PredictionBatch refuses as_of < training_cutoff, and admits equality. That stops a "
            "model being asked about an instant before the last outcome it was fitted on, and "
            "it does nothing about overlapping labels: at horizon h two prediction days one "
            "session apart share h of the h+1 sessions each window spans, so a fold boundary "
            "drawn on the cutoff alone leaves the training set's last windows overlapping the "
            "test set's first. TrainingSet.overlaps reports every such pair through "
            "domain/labels.py's overlapping_windows, which is the input a purge needs. "
            "Implementation Decision 12 and V2-P4-013 own purging and embargo."
        ),
    ),
    AlphaModelLimitation(
        code="a_batch_cannot_tell_a_prediction_from_a_backfill",
        detail=(
            "predicted_at >= as_of is checkable here. Story S32's real requirement -- that a "
            "batch was produced before its observation window closed -- is not: the window's "
            "exit session is a function of a trading calendar and this contract deliberately "
            "owns none. This entry stays because it is still true of a batch **on its own**, "
            "which is the object most callers hold. V2-P4-017 answered it one layer up: a "
            "PredictionRecord carries the deadline, derived from a calendar rather than "
            "accepted, and a standing computed from it -- and it found that Implementation "
            "Decision 14's other half needs no overwrite rule, because a backfill and its "
            "original disagree about the predicted_at this batch carries and therefore address "
            "apart. What this contract contributes is that predicted_at exists, is separate "
            "from as_of, and is on a frozen model, so a stored batch cannot be edited into "
            "agreement with what happened."
        ),
    ),
    AlphaModelLimitation(
        code="an_abstention_can_empty_a_ranking_of_predictions",
        detail=(
            "Prediction.abstention answers per security, and "
            "backtest/candidate_ranking.py::rank_candidates enforces all-or-nothing per ranking "
            "-- 'a ranking in which some candidates carry a model prediction and others do not "
            "is a list ordered on two different statistics'. The two are consistent and their "
            "interaction is worth stating: one abstained name on a shortlist means that "
            "shortlist can carry no CandidatePrediction at all. That is the conservative "
            "outcome and it is not free, and which of the two rules gives way -- if either -- "
            "belongs to whichever issue first joins a batch to a ranking, not to this one."
        ),
    ),
    AlphaModelLimitation(
        code="the_reference_implementation_is_not_a_baseline",
        detail=(
            "backtest/alpha_model.py ships SingleFeatureAlphaModel, which reads one declared "
            "feature, learns a centre and a sign from the training set, and scores a cross "
            "section by the signed distance from that centre. It exists to prove this contract "
            "can be satisfied and driven end to end -- fit, artifact, predict, abstain, "
            "reproduce from the artifact alone -- and it does no cross-sectional "
            "standardization, reads no second feature, and has no evaluation. Story S29 and "
            "V2-P4-014 own the linear/ranking baseline; V2-P4-015 owns the tree one. Nothing "
            "here should be read as a claim about alpha."
        ),
    ),
)
"""Eleven named boundaries on what this contract says, in `KNOWN_LABEL_LIMITATIONS`' form.

Eight at `V2-P4-011`; `V2-P4-016` rewrote two of them and added three. The two rewritten are the
ones that had become **false**: `the_fitted_artifact_carries_no_content_address` described an
artifact with no id, and `d11_names_eleven_things_and_this_artifact_carries_six` counted the
content hash among the missing. The three added are what the address does not prove --
`the_address_is_over_the_fit_and_not_over_the_rule_that_chose_it`,
`a_seed_in_the_address_is_read_by_no_model_in_this_build` and
`an_unknown_code_commit_is_one_constant_shared_by_every_build_that_has_none` -- plus
`the_manifest_slot_still_admits_an_address_from_another_plane`, which is what that issue chose
*not* to narrow and why.

Every `code` is required to appear as a string literal in executable test code by
`tests/unit/test_known_limitation_registries.py`, which is what keeps a rename from silently
orphaning every citation of it.
"""
