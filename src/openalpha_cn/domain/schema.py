"""Export checked-in JSON Schemas for the public domain contracts."""

import json
from pathlib import Path

from pydantic import BaseModel

from openalpha_cn.domain.decision import DecisionLedger
from openalpha_cn.domain.evidence import EvidenceSnapshot
from openalpha_cn.domain.run import RunManifest
from openalpha_cn.domain.signal import SignalFrame
from openalpha_cn.domain.validation import ValidationResult

CONTRACT_MODELS: dict[str, type[BaseModel]] = {
    "decision-ledger-v1": DecisionLedger,
    "evidence-snapshot-v1": EvidenceSnapshot,
    "run-manifest-v1": RunManifest,
    "signal-frame-v1": SignalFrame,
    "validation-result-v1": ValidationResult,
}


def export_schemas(output_dir: Path) -> tuple[Path, ...]:
    """Export canonical serialization schemas and return their paths."""
    output_dir.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    for name, model in CONTRACT_MODELS.items():
        path = output_dir / f"{name}.json"
        schema = model.model_json_schema(mode="serialization")
        path.write_text(
            json.dumps(schema, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        paths.append(path)
    return tuple(paths)


def main() -> None:
    """Export schemas to the repository documentation directory."""
    repository_root = Path(__file__).parents[3]
    export_schemas(repository_root / "docs" / "api" / "schemas")


if __name__ == "__main__":
    main()
