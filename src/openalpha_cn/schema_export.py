"""Export checked-in JSON Schemas for the public domain contracts to disk (V2-P0B-011).

Holds the filesystem write and repository-path derivation that `domain/schema.py` used
to perform directly (`Path(__file__).parents[3]`, `Path.write_text`). `domain/` is the
one package in this codebase with zero infrastructure dependencies, per ADR-0001's
guardrail and Task 4's `domain-purity` import-linter contract; neither `forbidden_modules`
nor `test_import_layering.py`'s dynamic checks previously caught this module writing
files, because plain `pathlib` IO is not `duckdb`/`sqlite3`/a sibling subpackage import --
but it was still infrastructure work that did not belong beside pure schema generation.
This module depends on `domain.schema`, never the reverse.
"""

import json
from pathlib import Path

from openalpha_cn.domain.schema import CONTRACT_MODELS, generate_schemas


def export_schemas(output_dir: Path) -> tuple[Path, ...]:
    """Write each contract's canonical schema to `output_dir` and return the paths."""
    output_dir.mkdir(parents=True, exist_ok=True)
    schemas = generate_schemas()
    paths: list[Path] = []
    for name in CONTRACT_MODELS:
        path = output_dir / f"{name}.json"
        path.write_text(
            json.dumps(schemas[name], ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        paths.append(path)
    return tuple(paths)


def main() -> None:
    """Export schemas to the repository documentation directory."""
    repository_root = Path(__file__).resolve().parents[2]
    export_schemas(repository_root / "docs" / "api" / "schemas")


if __name__ == "__main__":
    main()
