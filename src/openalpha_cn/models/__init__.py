"""Optional model provider contracts."""

from openalpha_cn.models.base import ModelMetadata, ModelProvider
from openalpha_cn.models.openai_compatible import (
    ModelConfigurationError,
    ModelResponseError,
    ModelTransportError,
    OpenAICompatibleProvider,
)

__all__ = [
    "ModelConfigurationError",
    "ModelMetadata",
    "ModelProvider",
    "ModelResponseError",
    "ModelTransportError",
    "OpenAICompatibleProvider",
]
