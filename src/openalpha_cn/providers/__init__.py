"""Data provider contracts and built-in adapters."""

from openalpha_cn.providers.base import (
    DataProvider,
    ProviderBatch,
    ProviderFailure,
    ProviderMetadata,
    ProviderRecord,
    ProviderRequest,
)
from openalpha_cn.providers.file import FileProvider

__all__ = [
    "DataProvider",
    "FileProvider",
    "ProviderBatch",
    "ProviderFailure",
    "ProviderMetadata",
    "ProviderRecord",
    "ProviderRequest",
]
