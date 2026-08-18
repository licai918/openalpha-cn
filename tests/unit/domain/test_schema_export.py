"""The checked-in contract schemas, held to the models and registries that produced them.

The guard this file used to carry was `assert schema_version.endswith("/v1")` -- a literal the
roadmap correctly predicted `V2-P4-001` would break by design. Replacing `/v1` with `/v2`
would have reinstated exactly the same weak assertion one version later: it would go on
passing while a document named `decision-ledger-v1.json` held a `decision-ledger/v2` schema,
and it would need editing again at v3.

So nothing here spells a version. Four statements are tied to each other instead, and the
knot is `domain/schema.py::CONTRACT_REGISTRIES`:

1. the set of documents on disk,
2. each document's `properties.schema_version.const`,
3. each registry's `current_version`, and
4. the `schema_version` default declared on the model class itself.

A bump that misses any one of those four goes red, and no line below has to be edited when
the next bump lands.
"""

import json
from pathlib import Path

from openalpha_cn.domain.schema import (
    CONTRACT_MODELS,
    CONTRACT_REGISTRIES,
    schema_document_name,
)
from openalpha_cn.schema_export import export_schemas

SCHEMA_DIR = Path(__file__).parents[3] / "docs" / "api" / "schemas"


def test_checked_in_contract_schemas_match_runtime_models() -> None:
    expected = {
        f"{name}.json": model.model_json_schema(mode="serialization")
        for name, model in CONTRACT_MODELS.items()
    }

    assert {path.name for path in SCHEMA_DIR.glob("*.json")} == set(expected)
    for filename, schema in expected.items():
        checked_in = json.loads((SCHEMA_DIR / filename).read_text(encoding="utf-8"))
        assert checked_in == schema


def test_every_document_name_registry_and_model_default_state_the_same_version() -> None:
    """The three-way tie that replaced `endswith("/v1")`, and cannot go stale on a bump.

    Each contract is asserted at its own current version rather than at a literal one, so the
    assertion says "the file name, the document, the registry and the class all agree" instead
    of "they all say v1" -- and the two halves that a bump can desynchronise (a regenerated
    document whose registry was never bumped; a bumped registry whose document was never
    renamed) are each covered by a different one of the three comparisons.
    """
    assert len(CONTRACT_REGISTRIES) == len(CONTRACT_MODELS)
    for registry in CONTRACT_REGISTRIES:
        version = registry.current_version
        assert isinstance(version, str)
        name = schema_document_name(version)

        model = CONTRACT_MODELS[name]
        assert model is registry.versions[version]
        assert model.model_fields["schema_version"].default == version

        checked_in = json.loads((SCHEMA_DIR / f"{name}.json").read_text(encoding="utf-8"))
        assert checked_in["properties"]["schema_version"]["const"] == version


def test_no_superseded_schema_document_is_left_behind() -> None:
    """A renamed document must not leave its predecessor on disk claiming to be current.

    The deep comparison above proves every *expected* document is right; it says nothing about
    an extra file. Before `V2-P4-001` there was nothing to say -- the names never changed --
    but a bump now renames a document, and a stale `…-v1.json` sitting beside its replacement
    is a published contract that no model produces and no test regenerates.
    """
    current = {f"{schema_document_name(str(r.current_version))}.json" for r in CONTRACT_REGISTRIES}

    assert {path.name for path in SCHEMA_DIR.glob("*.json")} == current


def test_schema_export_is_deterministic(tmp_path: Path) -> None:
    first = export_schemas(tmp_path / "first")
    second = export_schemas(tmp_path / "second")

    assert [path.name for path in first] == [path.name for path in second]
    assert [path.read_bytes() for path in first] == [path.read_bytes() for path in second]
