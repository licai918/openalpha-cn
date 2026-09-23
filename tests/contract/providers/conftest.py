"""Fixtures shared by the provider contract tests in this directory (V2-P0B-013).

Not promoted to `tests/conftest.py`: nothing here is needed outside `tests/contract/
providers/`.
"""

from __future__ import annotations

from typing import Any

import pytest


class FakeTushareTransport:
    """Doubles Tushare's `post(payload) -> dict` transport Protocol.

    Shared by `test_tushare_provider.py` and `test_tushare_dataset_descriptors.py`,
    which previously each defined a class under the identical name `FakeTransport` --
    the same Protocol, near-identical bodies, a genuine duplicate rather than just a
    naming collision. `test_tushare_provider.py`'s version additionally recorded the
    outgoing `payload` (asserted on by one of its tests); `test_tushare_dataset_
    descriptors.py`'s version never asserted on it. Recording is harmless whether or
    not a given test reads it, so this merged version always records.

    Deliberately NOT merged with the *other* `FakeTransport`-named doubles elsewhere in
    this suite -- `test_chainlin_provider.py`'s (renamed `FakeChainLinTransport`, doubles
    `get_json`) and `tests/unit/models/test_openai_compatible.py`'s (renamed
    `FakePostJsonTransport`, doubles `post_json`) -- those double different Protocols
    entirely and were never actual duplicates of each other or of this one.
    """

    def __init__(self, response: dict[str, Any]) -> None:
        self.response = response
        self.payload: dict[str, Any] | None = None

    def post(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.payload = payload
        return self.response


@pytest.fixture
def fake_tushare_transport() -> type[FakeTushareTransport]:
    """The `FakeTushareTransport` class itself, injected as a fixture so both call sites
    in this directory construct their own instances (`fake_tushare_transport({...})`)
    without either redefining the class."""
    return FakeTushareTransport
