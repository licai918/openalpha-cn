"""Data provider contracts and built-in adapters."""

from openalpha_cn.providers.akshare import AKShareProvider
from openalpha_cn.providers.base import (
    DataProvider,
    PanelDataProvider,
    ProviderBatch,
    ProviderFailure,
    ProviderMetadata,
    ProviderRecord,
    ProviderRequest,
)
from openalpha_cn.providers.chainlin import ChainLinDataProvider
from openalpha_cn.providers.file import FileProvider
from openalpha_cn.providers.tushare import TushareProvider

__all__ = [
    "AKShareProvider",
    "ChainLinDataProvider",
    "DataProvider",
    "FileProvider",
    "PanelDataProvider",
    "ProviderBatch",
    "ProviderFailure",
    "ProviderMetadata",
    "ProviderRecord",
    "ProviderRequest",
    "TushareProvider",
]
