"""Version-dispatched reads for stored contract payloads (V2-P0B-005).

Why this exists: eight of this package's pydantic contracts carry a literal
`schema_version` field (e.g. `Literal["run-manifest/v1"] = "run-manifest/v1"`) combined
with `extra="forbid"`. That combination is exactly right for catching accidental drift
today, and exactly wrong for a row a *newer* build wrote: `Literal` rejects the unfamiliar
value and `extra="forbid"` rejects any field the reader's version doesn't know about, so
`Model.model_validate_json(row)` fails hard the moment either model gains a version this
build doesn't have. Task 11 (`storage/migrations.py`) made the *table* upgradable; this
module makes the *row* upgradable, so a later phase (`V2-P4-001`) can cut three breaking
contract versions at once without stranding every research record written before it.

Design, in one paragraph: a `ContractVersions[T]` is a plain, hand-written registry --
mirroring `storage/migrations.py`'s `Migration` dataclass and `MIGRATIONS` tuple, not a
versioning framework -- mapping each `schema_version` value this build still knows how to
read to the pydantic model that validates a payload at that version, plus an `upgrades`
map from every non-current version to a function that turns a validated old-version
instance into a validated instance of the *next* version. `read_versioned()` is the single
entry point every storage read call site uses in place of `Model.model_validate_json`: it
parses the raw JSON once with the stdlib `json` module -- deliberately *not* pydantic --
and reads `schema_version` out of the resulting plain `dict` before any model sees the
payload. That ordering is the whole point: pydantic validation is exactly the step that
fails on an unfamiliar version, so it cannot be the mechanism used to *detect* one. Once
the version is known, the payload is validated against its own version's model (so a
genuinely malformed row -- e.g. a missing required field -- still raises a normal
pydantic `ValidationError`, distinguishable from an unrecognized version), and the
resulting instance is walked forward through `upgrades` until it reaches
`current_version`. A `schema_version` this build has never heard of -- the case that
matters most, a newer build's row reaching older code -- raises `UnknownSchemaVersionError`
naming the contract, the payload's version, and the versions this build supports, rather
than silently misreading it or guessing.

`single_version()` is a convenience for the majority of stored models that carry no
`schema_version` field at all (e.g. `PortfolioTransition`, `MemoryEntry`): it builds a
trivial one-entry `ContractVersions` keyed on `None` (the value `dict.get("schema_version")`
naturally returns when the key is absent), so every stored-row read in this package --
versioned or not -- goes through the same `read_versioned()` call. That uniformity is
deliberate: it means adding a real `schema_version` field to one of today's unversioned
models later is a registry edit at its own definition, not an audit of every call site
that reads it.

No v2 of any real contract is defined *here*: this module's own test suite
(`tests/unit/domain/test_versioning.py`) proves the upgrade-chain machinery end to end with
a synthetic, test-only "demo-widget" contract, exactly as `storage/migrations.py`'s demo
migration proved the table-migration engine without being one of the real breaking changes
it exists to enable. The real ones arrived at `V2-P4-001` and each registry lives at its own
contract's definition (`domain/run.py`, `domain/decision.py`, `domain/validation.py`), with a
frozen `*V1` snapshot class beside it. Two of the three register a **refusing** upgrade --
`IdentityRewriteRequiredError` below -- because their stored key is the model's own content
address and a transparent upcast would move it while every reference kept the old value.
"""

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Final, Generic, TypeVar, cast

from pydantic import BaseModel, ValidationError

T = TypeVar("T", bound=BaseModel)


class UnknownSchemaVersionError(ValueError):
    """Raised when a stored payload's `schema_version` is not in this build's registry.

    This is the explicit failure the brief requires: old code encountering a row written
    by a newer build must fail loudly, not silently misread or default it. The message
    names the contract, the version found in the payload, and every version this build
    knows how to read, so an operator can tell at a glance whether they need to upgrade
    this build or investigate a corrupted row.
    """

    def __init__(
        self,
        *,
        contract: str,
        found_version: object,
        supported_versions: tuple[str | None, ...],
    ) -> None:
        supported = ", ".join(repr(version) for version in supported_versions)
        message = (
            f"{contract}: stored schema_version {found_version!r} is not one this build "
            f"knows how to read (supported: {supported}). This row may have been written "
            "by a newer version of openalpha_cn than this one; refusing to silently "
            "misread it."
        )
        super().__init__(message)
        self.contract = contract
        self.found_version = found_version
        self.supported_versions = supported_versions


class IdentityRewriteRequiredError(ValueError):
    """Raised when a stored row's version can only be advanced by the storage migration.

    Roadmap section 8's conclusion, made into a type. Two of the three contracts
    `V2-P4-001` bumps carry a **content-derived** stored key -- `decisions.decision_id` and
    `validation_results.validation_id` -- and a transparent read-time upcast of such a row
    computes a *new* key while the table, and every column and payload that references it,
    still holds the old one. The row would read back fine and every reference to it would
    silently stop resolving, which is precisely the failure that section rules out: a P4
    contract bump may not be absorbed by a transparent read-time upcast. `run-manifest/v1`
    is the third and it upgrades on read, because `runs.run_id` is caller-supplied.

    So the upgrade registered for those versions refuses, and
    `storage/migrations.py::rewrite_contract_identities` -- which has the whole database in
    one transaction, and can therefore recompute a key *and* update everything that names it
    -- is the only path forward. A migrated database never raises this: every row is at the
    current version by the time any store reads it. Seeing it means migrations have not been
    run against this file, which is what the message says.
    """

    def __init__(self, *, contract: str, found_version: object) -> None:
        super().__init__(
            f"{contract}: stored schema_version {found_version!r} carries a content-derived "
            "identity that a read-time upgrade would move without updating the rows that "
            "reference it. Run `openalpha migrate run` against this database: "
            "storage/migrations.py's identity rewrite recomputes the identity and every "
            "reference to it in one transaction."
        )
        self.contract = contract
        self.found_version = found_version


STORED_DOCUMENT_FAULTS: Final[tuple[type[Exception], ...]] = (
    json.JSONDecodeError,
    UnknownSchemaVersionError,
    IdentityRewriteRequiredError,
    ValidationError,
)
"""Everything `read_versioned()` raises *about the stored bytes*, named once (`V2-P4-096`).

`model_view._OUTCOME_WINDOW_FAULTS` one plane down, and for the reason that issue stated: which
exceptions are facts about stored data rather than defects in the code that read them is one
question with one answer, and a store that answered it with a single `except json.JSONDecodeError`
would be the fourth call-site patch in a class that has now had three.

**Measured before the fix, on `FilePredictionStore.get` through all three product faces.** Four
documents, three exception types, one seam -- and every one of them arrived as `exit 5` with the
message withheld, a bare `500 text/plain`, and an unenveloped raise:

| the document | what `read_versioned` raised |
| --- | --- |
| truncated to half its bytes | `json.JSONDecodeError` |
| a `schema_version` this build has never heard of | `UnknownSchemaVersionError` |
| a JSON array rather than an object | `UnknownSchemaVersionError` (`version` reads as `None`) |
| one field retyped, still valid JSON | `pydantic.ValidationError` |

So the tuple is what a caller catches, not `json.JSONDecodeError`: three of the four faults are
not that type, and the one a power cut produces is the only one anybody would have thought of.

**`IdentityRewriteRequiredError` is in the tuple and is unreachable from every registry that
reads through it today**, because it comes from a *refusing upgrade* and only
`decisions`/`validation_results` register one -- both of which are read by
`storage/migrations.py`, which handles them by name. It is kept for `V2-P4-084`'s stated
precedent: a guard whose arm no corpus reaches is kept, and the reason is pinned in a test rather
than left for a mutation sweep to find and delete.

**What is deliberately *not* here is `RuntimeError`**, which `read_versioned` raises when an
upgrade chain fails to converge. That is a statement about the *registry* -- a `ContractVersions`
whose upgrades cycle -- and it is a defect in this build rather than a fact about a document. A
store that swallowed it would report its own bug as damaged data.
"""


@dataclass(frozen=True)
class ContractVersions(Generic[T]):
    """One contract's known-version registry plus its forward upgrade chain.

    `versions` maps every `schema_version` value this build can still read (`None` for a
    contract with no `schema_version` field, see `single_version()`) to the model that
    validates a payload at that version. `current_version` is the key in `versions` whose
    model is `T` -- the version `read_versioned()` always returns instances of. `upgrades`
    must supply, for every key in `versions` other than `current_version`, a function from
    a validated instance at that version to a validated instance of the *next* version
    (which may itself be non-current, if the chain has more than one hop).

    Construction validates that `current_version` is registered and that every
    non-current version has an upgrade path registered -- a registry that would strand a
    version mid-chain fails at import time, not the first time a stale row is read.
    """

    name: str
    current_version: str | None
    versions: Mapping[str | None, type[BaseModel]]
    upgrades: Mapping[str | None, Callable[[BaseModel], BaseModel]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.current_version not in self.versions:
            raise ValueError(
                f"{self.name}: current_version {self.current_version!r} is not in versions "
                f"{tuple(self.versions)!r}"
            )
        expected_upgrade_keys = set(self.versions) - {self.current_version}
        actual_upgrade_keys = set(self.upgrades)
        if actual_upgrade_keys != expected_upgrade_keys:
            raise ValueError(
                f"{self.name}: expected an upgrade registered for every non-current version "
                f"{sorted(expected_upgrade_keys, key=str)!r}, got "
                f"{sorted(actual_upgrade_keys, key=str)!r}"
            )


def single_version(name: str, model_cls: type[T]) -> ContractVersions[T]:
    """Build a trivial one-version registry for a model with no `schema_version` field.

    See the module docstring: this exists so every stored-row read in this package can
    go through `read_versioned()`, whether or not its model participates in the real
    `schema_version` versioning scheme yet.
    """
    return ContractVersions(name=name, current_version=None, versions={None: model_cls})


def read_versioned(registry: ContractVersions[T], raw: str | bytes) -> T:
    """Parse `raw`, dispatch on its `schema_version`, and return a current-version instance.

    `schema_version` is read from the plain `dict` produced by `json.loads`, before any
    pydantic model sees the payload -- see the module docstring for why this ordering is
    load-bearing. A version absent from `registry.versions` raises
    `UnknownSchemaVersionError`; a version present but malformed for its own model raises
    that model's ordinary `pydantic.ValidationError`, left to propagate unchanged so it is
    never confused with a version mismatch.
    """
    payload = json.loads(raw)
    version = payload.get("schema_version") if isinstance(payload, dict) else None
    model_cls = registry.versions.get(version)
    if model_cls is None:
        raise UnknownSchemaVersionError(
            contract=registry.name,
            found_version=version,
            supported_versions=tuple(registry.versions),
        )
    instance: BaseModel = model_cls.model_validate(payload)
    steps = 0
    while version != registry.current_version:
        if steps >= len(registry.versions):
            raise RuntimeError(
                f"{registry.name}: upgrade chain did not converge on {registry.current_version!r} "
                f"starting from {version!r}"
            )
        upgrade = registry.upgrades[version]
        instance = upgrade(instance)
        version = getattr(instance, "schema_version", None)
        steps += 1
    return cast(T, instance)
