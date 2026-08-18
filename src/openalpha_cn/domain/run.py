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

from openalpha_cn.domain._identity import stable_model_id
from openalpha_cn.domain.run_mode import RunMode
from openalpha_cn.domain.time import ensure_aware
from openalpha_cn.domain.versioning import ContractVersions, single_version


class VersionRef(BaseModel):
    """A named component version captured for reproduction."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    component: str = Field(min_length=1, max_length=128)
    version: str = Field(min_length=1, max_length=256)


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

    schema_version: Literal["run-manifest/v2"] = "run-manifest/v2"
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


def upgrade_run_manifest_v1(old: BaseModel) -> BaseModel:
    """Upgrade a `run-manifest/v1` payload in place at read time. Safe, unlike its siblings.

    Roadmap section 8 forbids a transparent read-time upcast for the contracts whose stored
    *key* is content-derived, because the upcast silently moves a primary key that every
    reference still spells the old way. `runs.run_id` is caller-supplied and does not move, so
    this manifest is the one member of the `V2-P4-001` package where the upcast is exactly
    right: the field set is unchanged, the widened `mode` set contains every v1 value, and no
    stored key depends on the result.

    `DecisionLedger` and `ValidationResult` refuse instead -- see
    `domain/versioning.py::IdentityRewriteRequiredError` -- and `storage/migrations.py`'s
    `rewrite_contract_identities` rewrites all three on disk, so a migrated database never
    reaches either path.
    """
    if not isinstance(old, RunManifestV1):
        raise TypeError(f"expected a RunManifestV1, got {type(old).__name__}")
    payload = old.model_dump(mode="python")
    payload["schema_version"] = "run-manifest/v2"
    return RunManifest.model_validate(payload)


RUN_MANIFEST_ID_PATTERN: Final[str] = r"^run_[0-9a-f]{24}$"
"""Exactly what `stable_model_id(prefix="run", ...)` produces, and nothing else.

Attached to `DecisionLedger.run_manifest_id` so a caller cannot hand the ledger a placeholder
and get a decision whose provenance is a string somebody typed. `storage/factor_experiments.py`
draws the same line for `fxp_`/`fxc_` keys and states the reason there: a content address that
is only conventionally a content address stops being one the first time it is convenient.
"""


RUN_MANIFEST_VERSIONS: ContractVersions[RunManifest] = ContractVersions(
    name="run-manifest",
    current_version="run-manifest/v2",
    versions={"run-manifest/v1": RunManifestV1, "run-manifest/v2": RunManifest},
    upgrades={"run-manifest/v1": upgrade_run_manifest_v1},
)

CHECKPOINT_RECORD_VERSIONS: ContractVersions[CheckpointRecord] = single_version(
    "checkpoint-record", CheckpointRecord
)
