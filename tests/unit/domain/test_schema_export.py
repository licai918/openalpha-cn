import json
from pathlib import Path

from openalpha_cn.domain.schema import CONTRACT_MODELS, export_schemas

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
        assert checked_in["properties"]["schema_version"]["const"].endswith("/v1")


def test_schema_export_is_deterministic(tmp_path: Path) -> None:
    first = export_schemas(tmp_path / "first")
    second = export_schemas(tmp_path / "second")

    assert [path.name for path in first] == [path.name for path in second]
    assert [path.read_bytes() for path in first] == [path.read_bytes() for path in second]
