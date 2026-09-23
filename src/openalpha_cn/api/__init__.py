"""OpenAlpha CN HTTP API."""

from typing import TYPE_CHECKING, Any

from openalpha_cn.api.app import create_app

if TYPE_CHECKING:  # pragma: no cover - import-time typing only
    from fastapi import FastAPI

    app: FastAPI

__all__ = ["app", "create_app"]


def __getattr__(name: str) -> Any:
    """Forward `app` to `openalpha_cn.api.app`'s own lazy attribute (`V2-P4-111`).

    This file used to read `from openalpha_cn.api.app import app`, which is an *attribute
    access* and therefore defeated the laziness one module down entirely: `import
    openalpha_cn.api.app` imports this package first, so the eager name here built the
    application before the submodule was reached. Both had to move together, and the pair is
    driven by `tests/integration/test_import_time_filesystem.py` rather than reasoned about.
    """
    if name != "app":
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    from openalpha_cn.api import app as module

    return module.app
