"""Fixtures shared across every test subtree (`unit/`, `integration/`, `contract/`).

Only fixtures genuinely needed from more than one top-level subtree live here. A fixture
needed by files that all sit under one subtree belongs in that subtree's own
`conftest.py` instead (see `tests/integration/storage/conftest.py` and
`tests/contract/providers/conftest.py`); a helper needed by exactly one file stays
defined in that file (see e.g. `tests/unit/tools/test_evidence_lookup.py`'s local `item`
fixture, which itself depends on `frozen_now` below).

V2-P0B-013.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from openalpha_cn.backtest.execution import MarketBar
from openalpha_cn.domain.evidence import EvidenceSnapshot
from openalpha_cn.domain.time import Timeline
from openalpha_cn.providers.base import ProviderMetadata

# --- the canonical point-in-time clock --------------------------------------------------
#
# This exact value -- 2026-07-24T10:30:00Z -- is load-bearing in four tests that assert a
# genuine point-in-time *visibility boundary*: a record available at :00 is visible, a
# record available at the following hour's :00 is not, and this instant must sit strictly
# between the two.
#   - tests/unit/tools/test_evidence_lookup.py
#   - tests/contract/providers/test_file_provider.py
#   - tests/integration/test_evidence_interfaces.py
#   - tests/integration/storage/test_parquet_evidence_store.py
# Every other consumer of `frozen_now` just needs *a* frozen, timezone-aware instant and
# does not depend on this specific value -- but they all share the same one so that a
# single, well-understood constant is the one source of truth instead of fourteen
# hand-copied literals.
FROZEN_NOW: datetime = datetime(2026, 7, 24, 10, 30, tzinfo=UTC)

# A second, deliberately distinct instant (30 minutes earlier, same day) used by tests that
# need *a* frozen anchor but perform no point-in-time visibility check at all: domain-model
# construction/validation tests and a plain repository round-trip. Keeping it numerically
# different from `FROZEN_NOW` -- rather than merging the two -- means nobody can accidentally
# come to depend on it lining up with the visibility-boundary tests' 10:00/11:00 records.
PLAIN_FROZEN_NOW: datetime = datetime(2026, 7, 24, 10, 0, tzinfo=UTC)


@pytest.fixture
def frozen_now() -> datetime:
    """The shared point-in-time clock. See `FROZEN_NOW` above for why this exact value."""
    return FROZEN_NOW


@pytest.fixture
def frozen_clock(frozen_now: datetime) -> Callable[[], datetime]:
    """A zero-arg `clock=` callable that always returns `frozen_now`."""
    return lambda: frozen_now


@pytest.fixture
def plain_frozen_now() -> datetime:
    """A frozen instant for tests with no point-in-time visibility scenario to protect.

    See `PLAIN_FROZEN_NOW` above for why it is intentionally distinct from `frozen_now`.
    """
    return PLAIN_FROZEN_NOW


# Behavioral guards for these fixtures (frozen-ness, distinctness) live in
# `tests/test_shared_fixtures.py`, not here: pytest's default collection patterns
# (`test_*.py` / `*_test.py`) do not pick up `test_*` functions declared inside a
# `conftest.py` when the suite is run normally via `testpaths`, so a `test_` function
# defined in this file would silently never run.


# --- shared factories --------------------------------------------------------------------


@pytest.fixture
def evidence(
    frozen_now: datetime,
) -> Callable[..., EvidenceSnapshot]:
    """Factory for a synthetic `EvidenceSnapshot` keyed by `kind`/`facts`, timestamped
    uniformly at `frozen_now` (event = available = ingested = revision).

    Collapses three call-for-call-identical helpers that built exactly this shape:
    `tests/integration/test_research_cycle.py`, `tests/integration/test_recovery_and_memory.py`,
    and `tests/unit/runtime/test_storage_protocol_doubles.py` (there named `_evidence`).
    Not shared with `tests/unit/agents/test_model_agent.py`'s local `theme_evidence`
    fixture (a fixed, zero-argument "theme" evidence with a different payload shape) or
    `tests/integration/storage/test_parquet_evidence_store.py`'s local `evidence` fixture
    (parameterized by `event_hour`/`available_hour` to drive its own point-in-time
    boundary tests) -- both are single-file, single-purpose helpers left where they are.
    """

    def _make(*, kind: str, facts: dict[str, object]) -> EvidenceSnapshot:
        return EvidenceSnapshot(
            subject="000001.SZ",
            kind=kind,
            timeline=Timeline(
                event_time=frozen_now,
                available_time=frozen_now,
                ingested_time=frozen_now,
                revision_time=frozen_now,
            ),
            source_id="synthetic.a-share",
            source_uri=f"fixture://{kind}/000001.SZ",
            source_license="CC0-1.0",
            redistribution="allowed",
            summary=f"Synthetic {kind}.",
            payload={
                "schema": "a-share-evidence/v1",
                "family": {
                    "limit_up": "market_event",
                    "theme": "theme",
                    "capital": "capital",
                }[kind],
                "facts": facts,
                "quality_flags": [],
            },
        )

    return _make


@pytest.fixture
def metadata() -> ProviderMetadata:
    """A synthetic `ProviderMetadata` for the generic "user-owned file" provider used by
    the point-in-time file-provider tests.

    Shared by `tests/contract/providers/test_file_provider.py` and
    `tests/integration/test_evidence_interfaces.py`, which previously each defined an
    equivalent but textually drifted factory -- no assertion in either file depends on the
    exact wording of `display_name`/`freshness`/`failure_semantics`, only on
    `provider_id="user.file"` and `redistribution="restricted"`, both preserved here.

    Deliberately NOT shared with `tests/unit/evidence/test_builder.py`'s own `metadata()`
    (kept local, not promoted to a fixture): that one must keep
    `provider_id="synthetic.a-share"` and `redistribution="allowed"` because
    `test_builder_normalizes_all_v1_a_share_evidence_families` asserts
    `item[0].source_id == "synthetic.a-share"` directly, and
    `test_builder_adds_quality_flags_without_losing_source_facts` relies on the base
    redistribution being *not* already `"restricted"` before its own override.
    """
    return ProviderMetadata(
        provider_id="user.file",
        display_name="User-owned file",
        source_license="user-supplied",
        redistribution="restricted",
        credential_env_vars=(),
        caching_policy="local-permitted",
        rate_limit="not-applicable",
        freshness="defined-by-input-file",
        failure_semantics="Malformed or unreadable inputs raise ProviderFailure.",
    )


@pytest.fixture
def bar() -> Callable[..., MarketBar]:
    """Factory for a flat (open == high == low == close) `MarketBar` on a given
    `trade_date`, used by multi-day portfolio simulation tests that only care about the
    day's closing mark, not intraday OHLC movement.

    Collapses `tests/unit/backtest/test_portfolio.py` and
    `tests/integration/test_portfolio_ledger_backtest.py`'s previously separate
    "close-only" `bar()` helpers (identical in every field except cosmetic differences:
    keyword-with-default vs. positional-required arguments, and `Decimal("10.00")` vs.
    `Decimal("10")` for `previous_close` -- numerically equal, and neither file asserts on
    its string form).

    Deliberately NOT shared with `tests/unit/backtest/test_execution.py`'s `bar(**updates)`
    kwargs-override helper: that one independently varies board/previous_close/open/high/
    low/close/suspended/is_st to drive execution-policy edge cases (star-market lot sizing,
    locked limit-up bars, commission math), a genuinely different need from a single
    flat closing price. See task-13-report.md for the full comparison.
    """

    def _make(trade_date: date, close: str = "10.00") -> MarketBar:
        price = Decimal(close)
        return MarketBar(
            subject="000001.SZ",
            trade_date=trade_date,
            board="main",
            previous_close=Decimal("10.00"),
            open=price,
            high=price,
            low=price,
            close=price,
            suspended=False,
            is_st=False,
        )

    return _make
