"""Canonical JSON Schemas for the public domain contracts.

Pure schema generation only -- no filesystem writes, no repository-path derivation.
`domain/` is the one package in this codebase with zero infrastructure dependencies
(ADR-0001's guardrail, enforced by Task 4's `domain-purity` import-linter contract); a
module that wrote files and hardcoded `Path(__file__).parents[N]` repository layout
lived here until V2-P0B-011 moved that IO to `openalpha_cn.schema_export`, which imports
`CONTRACT_MODELS`/`generate_schemas` from this module, not the other way around.

## Why the document names are derived (`V2-P4-001`)

`CONTRACT_MODELS` used to be a hand-written mapping of five `"<contract>-v1"` keys to five
model classes, and the checked-in files were named after those keys. That arrangement had
one true statement in it -- the file name, the model's `schema_version` default, and the
version registry's `current_version` all said `v1` -- and no mechanism keeping the three
true together. `test_schema_export.py`'s original guard was
`assert schema_version.endswith("/v1")`, which the roadmap correctly predicted would fail
by design at this issue; but replacing `/v1` with `/v2` would have reinstated exactly the
same weak assertion one version later, and the file names would have gone on claiming `v1`
for a `v2` document with nothing noticing.

So the names come from the registries instead: `schema_document_name` turns a
`current_version` into the file stem, `CONTRACT_MODELS` is built by walking
`CONTRACT_REGISTRIES`, and the model each key maps to is the one that registry's
`current_version` resolves to. Bumping a contract now renames its document, and the three
statements cannot drift apart because there is only one of them --
`tests/unit/domain/test_schema_export.py` ties the checked-in file set, each document's
`schema_version` const and each model's own field default back to the same registry.
"""

from typing import Any

from pydantic import BaseModel

from openalpha_cn.domain.decision import DECISION_LEDGER_VERSIONS
from openalpha_cn.domain.evidence import EVIDENCE_SNAPSHOT_VERSIONS
from openalpha_cn.domain.run import RUN_MANIFEST_VERSIONS
from openalpha_cn.domain.signal import SIGNAL_FRAME_VERSIONS
from openalpha_cn.domain.validation import VALIDATION_RESULT_VERSIONS
from openalpha_cn.domain.versioning import ContractVersions

CONTRACT_REGISTRIES: tuple[ContractVersions[Any], ...] = (
    DECISION_LEDGER_VERSIONS,
    EVIDENCE_SNAPSHOT_VERSIONS,
    RUN_MANIFEST_VERSIONS,
    SIGNAL_FRAME_VERSIONS,
    VALIDATION_RESULT_VERSIONS,
)
"""The five contracts this repository publishes a JSON Schema for, as their registries.

Registries rather than model classes: the registry is the thing that knows which version is
current, and that is the fact every name below is derived from.
"""


def schema_document_name(version: str) -> str:
    """Turn a `schema_version` value into the stem of its checked-in schema document.

    `"run-manifest/v2"` becomes `"run-manifest-v2"`. One character's difference, stated once,
    because it is the join between a value that appears inside the document and a name that
    appears on the filesystem -- and a join spelled at two call sites is a join that drifts.
    """
    return version.replace("/", "-")


CONTRACT_MODELS: dict[str, type[BaseModel]] = {
    schema_document_name(str(registry.current_version)): registry.versions[registry.current_version]
    for registry in CONTRACT_REGISTRIES
}


def generate_schemas() -> dict[str, dict[str, Any]]:
    """Return each contract's canonical serialization JSON Schema, keyed by name."""
    return {
        name: model.model_json_schema(mode="serialization")
        for name, model in CONTRACT_MODELS.items()
    }
