"""OpenAlpha CN public package."""

# Import for its module-level side effect only: `openalpha_cn.logging_setup` attaches a
# permanent, inert `NullHandler` to this package's logger namespace so a stray
# `logger.warning(...)`/`logger.error(...)` from any module in this package never falls
# through to Python's "handler of last resort" and prints straight to a host process's
# real stderr -- see `logging_setup.py`'s module docstring for the full rationale.
#
# This import belongs *here*, in the package's own `__init__.py`, rather than relying on
# `cli.py` or `api/app.py` (the only two modules that otherwise import `logging_setup`)
# to pull it in as a side effect of their own imports. Python always fully executes a
# package's `__init__.py` before importing any of its submodules, so this line runs for
# *any* use of this package -- `import openalpha_cn.sdk` alone, with neither `cli.py` nor
# `api/app.py` ever imported, included. A reviewer proved that gap concretely: before this
# import existed, a library embedder constructing `OpenAlphaSDK` directly got no
# `NullHandler` at all, and a real migration failure inside `OpenAlphaSDK.__init__` printed
# a bare, unformatted line to the host process's stderr. See
# `tests/unit/test_sdk_logging_backstop.py` for the fresh-interpreter proof.
#
# Installing a `NullHandler` is not "configuring" logging (it has no output, suppresses
# nothing but the last-resort fallback, and never blocks `caplog`/a real handler attached
# later) -- so this does not violate the "library code never configures" rule
# `logging_setup.py` and `configure_logging()` otherwise enforce; only the two real entry
# points call `configure_logging()` itself.
from openalpha_cn import logging_setup as _logging_setup  # noqa: F401

__version__ = "1.0.0"
