"""Canonical JSON Schemas for the public domain contracts.

Pure schema generation only -- no filesystem writes, no repository-path derivation.
`domain/` is the one package in this codebase with zero infrastructure dependencies
(ADR-0001's guardrail, enforced by Task 4's `domain-purity` import-linter contract); a
module that wrote files and hardcoded `Path(__file__).parents[N]` repository layout
lived here until V2-P0B-011 moved that IO to `openalpha_cn.schema_export`, which imports
`CONTRACT_MODELS`/`generate_schemas` from this module, not the other way around.
"""

from typing import Any

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


def generate_schemas() -> dict[str, dict[str, Any]]:
    """Return each contract's canonical serialization JSON Schema, keyed by name."""
    return {
        name: model.model_json_schema(mode="serialization")
        for name, model in CONTRACT_MODELS.items()
    }
