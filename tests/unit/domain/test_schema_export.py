import json
from pathlib import Path

from openalpha_cn.domain.decision import DECISION_LEDGER_VERSIONS
from openalpha_cn.domain.evidence import EVIDENCE_SNAPSHOT_VERSIONS
from openalpha_cn.domain.run import RUN_MANIFEST_VERSIONS
from openalpha_cn.domain.schema import CONTRACT_MODELS, export_schemas
from openalpha_cn.domain.signal import SIGNAL_FRAME_VERSIONS
from openalpha_cn.domain.validation import VALIDATION_RESULT_VERSIONS

SCHEMA_DIR = Path(__file__).parents[3] / "docs" / "api" / "schemas"

# Each CONTRACT_MODELS key is "{contract-name}-v1"; its current version, per the V2-P0B-005
# version registry, is what the checked-in schema's schema_version const must match. This
# replaces a hardcoded ".../v1" check: it still catches an unregenerated schema file (the
# deep `checked_in == schema` comparison below already does that on its own), and it now
# also catches a registry whose current_version was never bumped to match a regenerated one.
CURRENT_VERSIONS = {
    "decision-ledger-v1": DECISION_LEDGER_VERSIONS.current_version,
    "evidence-snapshot-v1": EVIDENCE_SNAPSHOT_VERSIONS.current_version,
    "run-manifest-v1": RUN_MANIFEST_VERSIONS.current_version,
    "signal-frame-v1": SIGNAL_FRAME_VERSIONS.current_version,
    "validation-result-v1": VALIDATION_RESULT_VERSIONS.current_version,
}


def test_checked_in_contract_schemas_match_runtime_models() -> None:
    expected = {
        f"{name}.json": model.model_json_schema(mode="serialization")
        for name, model in CONTRACT_MODELS.items()
    }

    assert {path.name for path in SCHEMA_DIR.glob("*.json")} == set(expected)
    assert set(CURRENT_VERSIONS) == set(CONTRACT_MODELS)
    for filename, schema in expected.items():
        checked_in = json.loads((SCHEMA_DIR / filename).read_text(encoding="utf-8"))
        assert checked_in == schema
        name = filename.removesuffix(".json")
        assert checked_in["properties"]["schema_version"]["const"] == CURRENT_VERSIONS[name]


def test_schema_export_is_deterministic(tmp_path: Path) -> None:
    first = export_schemas(tmp_path / "first")
    second = export_schemas(tmp_path / "second")

    assert [path.name for path in first] == [path.name for path in second]
    assert [path.read_bytes() for path in first] == [path.read_bytes() for path in second]
