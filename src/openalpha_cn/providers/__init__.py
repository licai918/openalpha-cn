"""Data provider contracts and built-in adapters."""

from openalpha_cn.providers.akshare import AKShareProvider
from openalpha_cn.providers.base import (
    DataProvider,
    ProviderBatch,
    ProviderFailure,
    ProviderMetadata,
    ProviderRecord,
    ProviderRequest,
)
from openalpha_cn.providers.file import FileProvider
from openalpha_cn.providers.tushare import TushareProvider

__all__ = [
    "AKShareProvider",
    "DataProvider",
    "FileProvider",
    "ProviderBatch",
    "ProviderFailure",
    "ProviderMetadata",
    "ProviderRecord",
    "ProviderRequest",
    "TushareProvider",
]
