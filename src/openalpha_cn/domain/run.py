"""Reproduction and checkpoint contracts for research runs.

`V2-P4-025` gives `RunManifest` the content-addressed identity roadmap section 9 measured it
to be missing. That section's experiment drove a real `run_cycle` with a fixed clock and
`run_id`, varied one input at a time, and read `decision_id` back: `code_commit` moved it,
`config_digest` and `random_seed` did not -- because neither is a field of any model
`stable_model_id` was applied to, and `RunManifest`, which owns them both, had no identity at
all. The correction that section draws is the one this module now implements: *a field only
reaches an identity if it is a field of the hashed model, and that has to be measured rather
than assumed.*

Two things follow, and they are separate:

- `run_manifest_id` (below) is the run-level content address. Changing `config_digest` or
  `random_seed` moves it, which is `V2-P4-025`'s stated acceptance.
- `DecisionLedger.run_manifest_id` (`domain/decision.py`) carries it onto the ledger, which is
  what makes "different configurations produce the same decision ID" -- PRD section 1.3 B6,
  still true after P0.B -- false. Carrying the address rather than copying `config_digest` and
  `random_seed` into the ledger means the ledger inherits *every* declared run input at once,
  including ones added later, instead of gaining a field per input.
"""

from collections.abc import Mapping
from datetime import datetime
from typing import Final, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, computed_field, field_validator, model_validator

from openalpha_cn.domain._identity import CONTENT_ADDRESS_PATTERN, stable_model_id
from openalpha_cn.domain.run_mode import RunMode
from openalpha_cn.domain.time import ensure_aware
from openalpha_cn.domain.versioning import (
    ContractVersions,
    IdentityRewriteRequiredError,
    single_version,
)


class VersionRef(BaseModel):
    """A named component version captured for reproduction."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    component: str = Field(min_length=1, max_length=128)
    version: str = Field(min_length=1, max_length=256)


AgentKind = Literal["deterministic", "learned", "llm_backed"]
"""What sort of thing an agent is, which is S40's whole requirement of a manifest.

Three values because Implementation Decision 10 names three components ("Manifest 分别标识
确定性、量化与 LLM 组件") and S40 names the same three as agents. `learned` has no producer
inside this repository yet -- `V2-P4-011`'s `AlphaModel` and `V2-P4-014`'s baselines are what
will make one -- and is declared anyway, because unlike the unreachable
`TradeabilityVerdict.not_in_registry` branch `V2-P4-005` deleted, this value is reachable
today by any agent a user writes. A manifest that could not record one would be recording a
falsehood about it, which is exactly the state `V2-P4-010` found.
"""


class AgentProvenance(BaseModel):
    """What an agent declares about what pins its behaviour, for the run manifest.

    Declared by the agent rather than inferred by the engine, and that is the load-bearing
    choice. The engine could tell `StructuredSignalAgent` from `MarketAgent` by its type, but
    only for the two implementations this repository happens to ship: any other
    `ModelProvider`-backed agent -- and `ResearchAgent` is a public extension point precisely
    so there can be others -- would be recorded as `deterministic`, which is a *silent wrong
    answer* about the one fact S40 asks the manifest for. An agent that does not declare this
    fails loudly at `agents.base.ResearchAgent`'s structural check instead.

    `model` is the vendor identity for an LLM-backed agent -- `ModelMetadata.provider_id` and
    `ModelMetadata.model`, the two things `code_commit` cannot pin because they live at
    somebody else's endpoint. It is constrained in both directions below.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    kind: AgentKind
    model: VersionRef | None = None

    @model_validator(mode="after")
    def validate_model_matches_kind(self) -> Self:
        if self.kind == "llm_backed" and self.model is None:
            raise ValueError("an llm_backed agent must name the vendor model it calls")
        if self.kind != "llm_backed" and self.model is not None:
            raise ValueError("only an llm_backed agent may name a vendor model")
        return self


class AgentVersion(BaseModel):
    """One agent that contributed to a run, and which implementation kind it is.

    No `version` field, and its absence is a measurement rather than an omission. The slot this
    replaces carried `VersionRef(component=<agent_id>, version="baseline/v1")` -- a constant,
    which is why it was wrong about `StructuredSignalAgent` for the whole of v1 at no cost: a
    field whose value never varies contributes a fixed string to the canonical JSON and
    therefore nothing to any address. What a deterministic agent's "version" actually is, is
    the commit its code was at, and `RunManifest.code_commit` already carries that; restating
    it once per agent would put the same fact in two places, which is the drift this repository
    pays for most often. The vendor model an LLM-backed agent calls is the part `code_commit`
    genuinely cannot pin, and it goes to `RunManifest.model_versions`, the slot named for it.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    agent_id: str = Field(min_length=1, max_length=128)
    kind: AgentKind


class AlphaModelRef(BaseModel):
    """A quantitative model artifact a run consumed, named by its content address.

    The manifest's third component plane, and a separate type from `VersionRef` on purpose.
    `V2-P4-011` is where the distinction stops being a matter of naming: `models/base.py`'s
    `ModelProvider` is LLM-JSON-shaped (`generate_json(system, user, schema) -> dict`) and
    cannot express a panel fit/predict, so the thing that produces a quantitative model version
    is not the thing that produces an agent id or a vendor model string -- and the identifiers
    differ in kind with it. An agent id and a vendor model are *names*: somebody chose them and
    a reader can only take them on trust. A model artifact reference is a *digest*: `V2-P4-016`
    computes it from the training cutoff, feature version, parameters, seed and code version
    (Implementation Decision 11), and a reader can re-derive it. Keeping all three in one
    `tuple[VersionRef, ...]` is what let an agent id occupy the model slot for the whole of v1
    with nothing able to object.

    `artifact_id` is therefore pattern-bound to `stable_model_id`'s output rather than left as
    free text -- the same guard `DecisionLedger.run_manifest_id` carries, for the same reason.
    Which prefix, and which fields the digest is taken over, were `V2-P4-016`'s to decide, and it
    decided: `mdl_` over the whole of an `AlphaModelArtifact`.

    **It stayed generic anyway, and that is a decision with a cost.** Narrowing this field to
    `domain/alpha_model.py`'s `ALPHA_MODEL_ARTIFACT_ID_PATTERN` would need this module to import
    that one, which puts the model contract's whole label/adjustment/calendar import weight
    behind every `RunManifest`, or to spell the pattern a second time, which is what
    `CONTENT_ADDRESS_PATTERN` exists to avoid. So a `fct_` factor address still validates in the
    quantitative model slot, and what keeps that honest is a producer stamping `mdl_` rather than
    a validator -- `the_manifest_slot_still_admits_an_address_from_another_plane` in
    `KNOWN_ALPHA_MODEL_LIMITATIONS`. `backtest/candidate_ranking.py`'s
    `CandidatePrediction.model_artifact_id` is the field that *did* narrow, because `backtest/`
    may already reach the model contract and a prediction has exactly one kind of source.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=128)
    artifact_id: str = Field(pattern=CONTENT_ADDRESS_PATTERN)


class ArtifactDigest(BaseModel):
    """A named SHA-256 digest for a provider payload or other artifact."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=256)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class CheckpointRecord(BaseModel):
    """An immutable recovery checkpoint reference."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=128)
    recorded_at: datetime
    state_digest: str = Field(pattern=r"^[0-9a-f]{64}$")

    @field_validator("recorded_at")
    @classmethod
    def normalize_recorded_at(cls, value: datetime) -> datetime:
        return ensure_aware(value)


RUN_MANIFEST_UNADDRESSED_FIELDS: Final[Mapping[str, str]] = {
    "started_at": (
        "the wall clock a run began on. Re-running one declaration must reproduce its "
        "run_manifest_id or the address cannot be used to recognise the same run, and the "
        "clock is the one field guaranteed to differ between two such runs. This repository "
        "has paid for the other arrangement twice: FactorBuildManifest keeps built_at out of "
        "its payload for exactly this reason, and V2-P3-002's FactorInputRef had to have "
        "fetched_at moved out of batch_digest after a byte-identical re-fetch moved every "
        "manifest_id derived from it"
    ),
    "finished_at": (
        "the wall clock a run ended on, and additionally not knowable when the manifest is "
        "first built; addressing it would give one run two identities, before and after"
    ),
    "status": (
        "the run's outcome, not its declaration. A run that is interrupted and re-run to "
        "success is the same declared run, and it would otherwise have one address per "
        "lifecycle state -- so a caller could never look up 'the run I asked for'"
    ),
    "checkpoints": (
        "recovery bookkeeping that grows while the run is in flight. Addressing it makes the "
        "address change mid-run, and makes it depend on how many times the process crashed"
    ),
    "environment": (
        "observed host facts rather than declared inputs. platform.python_version() moves on "
        "an interpreter patch upgrade, which would move the address of every stored run -- "
        "and, through DecisionLedger.run_manifest_id, every stored decision_id -- with no "
        "research input having changed"
    ),
}
"""Every `RunManifest` field that is **recorded but not addressed**, with why (`V2-P4-025`).

A mapping rather than a set because the reason is the load-bearing half: an exclusion with no
stated reason is indistinguishable from an oversight, and this is the list a later contributor
will be tempted to add to. `run_manifest_id` excludes exactly these keys, and
`tests/unit/domain/test_contract_identity.py::test_every_run_manifest_field_is_addressed_or_excluded_by_name`
partitions `RunManifest.model_fields` against this mapping, so field *n+1* fails until it is
either measured to move the address or named here -- the audit shape `V2-P3-002`,
`V2-P3-014` and `V2-P3-015` each reused.
"""


class RunManifest(BaseModel):
    """Everything required to reproduce and recover one research run."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    schema_version: Literal["run-manifest/v3"] = "run-manifest/v3"
    run_id: str = Field(min_length=1, max_length=128)
    mode: RunMode
    """Which cycle this run is, from the one declaration in `domain/run_mode.py`.

    `V2-P4-001` adds `paper` and `daily`. Widening an accepted set is forward-incompatible
    rather than backward-incompatible -- every v1 payload is still valid here, while a build
    that predates this one would reject a `paper` row -- and `schema_version` plus
    `UnknownSchemaVersionError` is exactly the mechanism for that direction, so the version is
    bumped rather than the widening being made silently.
    """
    as_of: datetime
    code_commit: str = Field(min_length=7, max_length=64)
    config_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    provider_payload_digests: tuple[ArtifactDigest, ...] = ()
    agent_versions: tuple[AgentVersion, ...] = ()
    """Which agents contributed to this run, and of what kind (`V2-P4-010`, S40).

    Its own slot rather than a re-use of `model_versions`, and the move is what the row asks
    for: an agent id is not a model version, and the two sharing a `tuple[VersionRef, ...]` is
    how an agent id came to occupy the model slot in the first place. The roster still has to
    reach `run_manifest_id` -- it is a declared input, chosen by whoever constructed the engine,
    and nothing else in this manifest carries it -- so emptying `model_versions` without adding
    this field would have quietly *removed* a declared input from the run's identity.

    In execution order, not sorted or de-duplicated: `routing_path` on the ledger records the
    same order, and two runs that put the same agents to work in a different order are two
    different declarations.
    """
    model_versions: tuple[VersionRef, ...] = ()
    """The vendor models this run called, as `provider_id` / `model` (`ModelMetadata`'s pair).

    What the field was always named for, and what it did not contain until `V2-P4-010`. The
    cost of the previous occupant was measured rather than argued: two `run_cycle`s differing
    only in which vendor model answered produced the same `run_manifest_id` *and* the same
    `decision_id`, because both recorded `version="baseline/v1"` -- roadmap section 9's finding
    ("different configurations produce the same decision ID") reproduced in the model plane
    after `V2-P4-025` closed it in the configuration plane. See
    `tests/integration/test_manifest_model_provenance.py`.

    Empty for a run with no LLM in it, which is a real answer rather than a missing one -- and
    one the previous arrangement could not give, since it wrote an entry per agent
    unconditionally.
    """
    prompt_versions: tuple[VersionRef, ...] = ()
    """The prompt artifacts this run used. Still `()` on every path, and correctly so.

    The row records this slot as "永远为空" and it stays empty here, because the alternative
    would be a second copy rather than a fact: the only prompt in this repository is the string
    literal in `agents/model.py::StructuredSignalAgent.analyze`, and a string literal is pinned
    by `code_commit`, which this manifest already carries. Filling this slot becomes a real
    statement when a prompt becomes an artifact with a life outside the source tree -- stored,
    edited without a commit, or selected at run time. No issue in the `010`-`021` chain makes
    one, so this is left declared and unfilled rather than fabricated.
    """
    alpha_model_versions: tuple[AlphaModelRef, ...] = ()
    """The quantitative model artifacts this run consumed. Still empty on every path.

    `V2-P4-010` wrote "`V2-P4-016` fills it" here, and that turned out to be wrong about which
    issue: `V2-P4-016` built the address this slot names -- `mdl_` over an `AlphaModelArtifact`,
    and the join is `AlphaModelRef(name=artifact.declaration.name,
    artifact_id=artifact.artifact_id)` -- but nothing on `ResearchEngine.run_cycle`'s path fits a
    model, so there is still nothing to put here. Whichever issue first composes a fit into a run
    fills it (`V2-P4-021`'s model faces, or `V2-P4-017`'s store), and the shape does not have to
    move when it does.

    The third plane, empty on every path this build can execute, and that is the honest state
    rather than a placeholder: no `AlphaModel` exists until `V2-P4-011`. An empty tuple is a
    legitimate answer here -- "this run used no quantitative model" is true of every run today
    and will stay true of some runs afterwards -- which is what distinguishes it from the
    hard-coded `"baseline/v1"` it sits beside in this issue's diff. A constant is invisible to
    the address forever; an empty tuple starts moving it the moment there is something to put
    in it.

    Added at `V2-P4-010` rather than later for one measured reason: a field added to `RunManifest`
    moves `run_manifest_id`, and through `DecisionLedger.run_manifest_id` every stored
    `decision_id`, `validation_id` and `report_id` with it -- the identity rewrite roadmap
    section 8 describes and `storage/migrations.py` pays for. Implementation Decision 36 says
    to bundle such changes rather than take one per issue, so the slot lands with the two the
    agent and model planes already required.
    """
    random_seed: int
    environment: tuple[VersionRef, ...] = ()
    started_at: datetime
    finished_at: datetime | None = None
    status: Literal["pending", "running", "succeeded", "failed", "interrupted"]
    checkpoints: tuple[CheckpointRecord, ...] = ()

    @field_validator("as_of", "started_at", "finished_at")
    @classmethod
    def normalize_datetimes(cls, value: datetime | None) -> datetime | None:
        return None if value is None else ensure_aware(value)

    @model_validator(mode="after")
    def validate_lifecycle(self) -> Self:
        is_terminal = self.status in {"succeeded", "failed", "interrupted"}
        if is_terminal and self.finished_at is None:
            raise ValueError("finished_at is required for a terminal run")
        if self.finished_at is not None and self.finished_at < self.started_at:
            raise ValueError("finished_at cannot precede started_at")
        if any(item.recorded_at < self.started_at for item in self.checkpoints):
            raise ValueError("checkpoint cannot precede started_at")
        return self

    @computed_field(return_type=str)  # type: ignore[prop-decorator]
    @property
    def run_manifest_id(self) -> str:
        """Return the content address of this run's **declaration** (`V2-P4-025`).

        `stable_model_id` over every field except `RUN_MANIFEST_UNADDRESSED_FIELDS`, so two
        runs that declare the same inputs share an address however long they took and however
        they ended, and two runs that declare anything different -- `config_digest` and
        `random_seed` included, which is section 9's whole point -- do not.

        The split is `V2-P3-014`'s, reused: an `experiment_id` is stamped on the declaration
        and a `content_digest` on the answer, so that "the same question, re-asked" and "a
        different answer" stay distinguishable. A manifest is the declaration half; the
        decision ledger and the validation result are the answers, and they keep their own
        addresses.
        """
        return stable_model_id(
            prefix="run", model=self, exclude=frozenset(RUN_MANIFEST_UNADDRESSED_FIELDS)
        )


class RunManifestV1(BaseModel):
    """The frozen `run-manifest/v1` shape, kept so a stored v1 row can still be read.

    Not a historical curiosity and not dead code: `read_versioned` validates a payload against
    its *own* version's model before upgrading it, so this class is what stands between a
    genuinely malformed v1 row (a normal `pydantic.ValidationError`) and one that is merely
    old. It differs from `RunManifest` in exactly two places -- the `schema_version` literal
    and the three-member `mode` set -- and it is written out rather than generated, because a
    generated snapshot of "whatever the current model was minus the new bits" describes the
    current model rather than what v1 actually accepted.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    schema_version: Literal["run-manifest/v1"] = "run-manifest/v1"
    run_id: str = Field(min_length=1, max_length=128)
    mode: Literal["live", "replay", "backtest"]
    as_of: datetime
    code_commit: str = Field(min_length=7, max_length=64)
    config_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    provider_payload_digests: tuple[ArtifactDigest, ...] = ()
    model_versions: tuple[VersionRef, ...] = ()
    prompt_versions: tuple[VersionRef, ...] = ()
    random_seed: int
    environment: tuple[VersionRef, ...] = ()
    started_at: datetime
    finished_at: datetime | None = None
    status: Literal["pending", "running", "succeeded", "failed", "interrupted"]
    checkpoints: tuple[CheckpointRecord, ...] = ()

    @field_validator("as_of", "started_at", "finished_at")
    @classmethod
    def normalize_datetimes(cls, value: datetime | None) -> datetime | None:
        return None if value is None else ensure_aware(value)


class RunManifestV2(BaseModel):
    """The frozen `run-manifest/v2` shape, for the same reason `RunManifestV1` is frozen.

    `V2-P4-025`'s version: the widened `mode` set, and the two component slots before
    `V2-P4-010` gave the agent roster and the quantitative plane their own. Written out rather
    than derived from `RunManifest` minus two names, because a derived snapshot describes
    today's model rather than what v2 actually accepted -- and the difference is the whole
    point of keeping one.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    schema_version: Literal["run-manifest/v2"] = "run-manifest/v2"
    run_id: str = Field(min_length=1, max_length=128)
    mode: RunMode
    as_of: datetime
    code_commit: str = Field(min_length=7, max_length=64)
    config_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    provider_payload_digests: tuple[ArtifactDigest, ...] = ()
    model_versions: tuple[VersionRef, ...] = ()
    prompt_versions: tuple[VersionRef, ...] = ()
    random_seed: int
    environment: tuple[VersionRef, ...] = ()
    started_at: datetime
    finished_at: datetime | None = None
    status: Literal["pending", "running", "succeeded", "failed", "interrupted"]
    checkpoints: tuple[CheckpointRecord, ...] = ()

    @field_validator("as_of", "started_at", "finished_at")
    @classmethod
    def normalize_datetimes(cls, value: datetime | None) -> datetime | None:
        return None if value is None else ensure_aware(value)


def upgrade_run_manifest_v1(old: BaseModel) -> BaseModel:
    """Upgrade a `run-manifest/v1` payload straight to the current version at read time.

    Roadmap section 8 forbids a transparent read-time upcast for the contracts whose stored
    *key* is content-derived, because the upcast silently moves a primary key that every
    reference still spells the old way. `runs.run_id` is caller-supplied and does not move,
    which is half of why this upcast is allowed; the other half, stated at `V2-P4-001` and
    still true of **v1 specifically**, is that no stored row referenced a manifest's content
    address at v1 -- `DecisionLedgerV1` has no `run_manifest_id`, because `V2-P4-025` is what
    created one. A v1 run row can therefore only sit beside v1 decisions, and nothing goes
    stale by advancing it.

    Straight to v3 rather than one hop to v2, and the jump is what makes the sentence above
    exact: `read_versioned` walks a chain, and stopping at v2 would hand the result to
    `refuse_run_manifest_v2_upgrade` below, whose refusal is about a database this one is not.
    `ContractVersions` permits an upgrade to return any later version, so the chain converges
    in one step.
    """
    if not isinstance(old, RunManifestV1):
        raise TypeError(f"expected a RunManifestV1, got {type(old).__name__}")
    payload = old.model_dump(mode="python")
    payload["schema_version"] = "run-manifest/v3"
    return RunManifest.model_validate(payload)


def upgrade_run_manifest_v2(old: BaseModel) -> RunManifest:
    """Advance a `run-manifest/v2` payload to v3. For `storage/migrations.py` only.

    Deliberately **not** the function registered against `"run-manifest/v2"` in
    `RUN_MANIFEST_VERSIONS` -- `refuse_run_manifest_v2_upgrade` is, and the split is the point.
    The upgrade itself is arithmetically trivial (two slots that did not exist default to `()`)
    and produces a perfectly correct manifest; what it cannot do at *read* time is fix up the
    rows that named the address it just moved. Doing it inside the migration's transaction can,
    which is why this is exported for one caller and refused for every other.
    """
    if not isinstance(old, RunManifestV2):
        raise TypeError(f"expected a RunManifestV2, got {type(old).__name__}")
    payload = old.model_dump(mode="python")
    payload["schema_version"] = "run-manifest/v3"
    return RunManifest.model_validate(payload)


def refuse_run_manifest_v2_upgrade(old: BaseModel) -> BaseModel:
    """Refuse to advance a v2 manifest at read time; the storage migration must do it.

    The criterion is `upgrade_run_manifest_v1`'s own, and between v1 and v2 it changed sides.
    That docstring licenses the v1 upcast because "no stored key depends on the result" --
    true when it was written, and false from `V2-P4-025` onwards, which put the manifest's
    content address into `DecisionLedger.run_manifest_id` and thereby into `decision_id`,
    `validation_id` and `report_id` behind it. `V2-P4-010` adds two fields to `RunManifest`, so
    the address of every stored v2 run moves; upcasting one on read would hand back a manifest
    whose address no decision names, and every reference to it would stop resolving with no
    exception raised anywhere. That is the silent half of the failure section 8 warns about,
    and it is worse here than the loud half.

    `storage/migrations.py`'s `rewrite_manifest_component_planes` (version 8) advances the run
    rows and re-points and re-keys everything behind them in one transaction, and
    `build_storage()` runs migrations before constructing any store -- so a database reached
    through the supported path never sees this.
    """
    raise IdentityRewriteRequiredError(
        contract="run-manifest", found_version=getattr(old, "schema_version", None)
    )


RUN_MANIFEST_ID_PATTERN: Final[str] = r"^run_[0-9a-f]{24}$"
"""Exactly what `stable_model_id(prefix="run", ...)` produces, and nothing else.

Attached to `DecisionLedger.run_manifest_id` so a caller cannot hand the ledger a placeholder
and get a decision whose provenance is a string somebody typed. `storage/factor_experiments.py`
draws the same line for `fxp_`/`fxc_` keys and states the reason there: a content address that
is only conventionally a content address stops being one the first time it is convenient.
"""


RUN_MANIFEST_VERSIONS: ContractVersions[RunManifest] = ContractVersions(
    name="run-manifest",
    current_version="run-manifest/v3",
    versions={
        "run-manifest/v1": RunManifestV1,
        "run-manifest/v2": RunManifestV2,
        "run-manifest/v3": RunManifest,
    },
    upgrades={
        "run-manifest/v1": upgrade_run_manifest_v1,
        "run-manifest/v2": refuse_run_manifest_v2_upgrade,
    },
)

CHECKPOINT_RECORD_VERSIONS: ContractVersions[CheckpointRecord] = single_version(
    "checkpoint-record", CheckpointRecord
)
