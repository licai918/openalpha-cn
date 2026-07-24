"""Model-provider boundary for optional structured LLM agents."""

from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field


class ModelMetadata(BaseModel):
    """Non-secret model provider capabilities and credential requirements."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    provider_id: str = Field(min_length=1, max_length=128)
    model: str = Field(min_length=1, max_length=256)
    credential_env_vars: tuple[str, ...]
    structured_output: bool


class ModelProvider(Protocol):
    """Extension interface for providers that return structured JSON."""

    @property
    def metadata(self) -> ModelMetadata:
        """Return non-secret provider metadata."""

    def generate_json(
        self,
        *,
        system: str,
        user: str,
        schema: dict[str, Any],
    ) -> dict[str, Any]:
        """Generate one JSON object conforming to the requested schema."""
