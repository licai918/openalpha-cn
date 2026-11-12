"""Model-provider boundary for optional structured LLM agents."""

from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field


class ModelCapabilities(BaseModel):
    """Machine-readable limits used for safe model selection."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    context_window: int | None = Field(default=None, gt=0)
    max_output_tokens: int | None = Field(default=None, gt=0)
    reasoning: bool = False
    usage_reporting: bool = False
    structured_output_modes: tuple[str, ...] = ("json_schema", "json_object")


class ModelMetadata(BaseModel):
    """Non-secret model provider capabilities and credential requirements."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    provider_id: str = Field(min_length=1, max_length=128)
    model: str = Field(min_length=1, max_length=256)
    credential_env_vars: tuple[str, ...]
    structured_output: bool
    capabilities: ModelCapabilities = ModelCapabilities()


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
        attempt: int = 1,
    ) -> dict[str, Any]:
        """Generate one JSON object conforming to the requested schema.

        `attempt` is the caller's 1-based retry number, for providers that record their
        exchanges: a retry must be a fresh request, not a replay of the answer that was already
        rejected. It is not part of what the vendor is asked.
        """
