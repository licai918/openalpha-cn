"""The paper book's evenings: its opening, and each evening as one crash-safe transaction
(`V2-P7-009`).

## The opening

A paper book is opened once per runtime directory, on the epoch session (the ordinal the
calendar counts from), by saving an **opening snapshot**: the cash it is given, nothing held,
nothing settled (`last_settled_session=None`) and nothing decided (`first_decision_session=None`),
so the first evening after it is the kernel's initial build. `open_book` writes it through
`paper_state.save_snapshot`, which holds the chain rules: the same opening again writes nothing,
and any other opening over a held book -- another amount or configuration on the same day, or a
restart on a later one -- is refused by name. Nothing reads a book that was never opened: every
reader refuses an empty store.

This module lives under `backtest/` and reaches no store: the snapshot store arrives as
`PaperStateDocumentStore`, the `Protocol` beside its consumer.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from openalpha_cn.backtest.paper_state import (
    PaperBookSnapshot,
    PaperStateDocumentStore,
    save_snapshot,
)
from openalpha_cn.domain.portfolio import PortfolioState

__all__ = ["open_book"]


def open_book(
    *,
    store: PaperStateDocumentStore,
    session: date,
    session_index: int,
    cash: Decimal,
    config_digest: str,
) -> PaperBookSnapshot:
    """Open the paper book on `session` (ordinal `session_index`) with `cash`, under the
    configuration whose `survivor_config_digest` is `config_digest`; return the opening snapshot.

    The same opening again writes nothing and returns it -- also once evenings have been settled
    on top of it, since it changes nothing. Any other opening over a store that holds a book is
    refused by name (`save_snapshot`). An amount that is not positive is refused before anything
    is written: the kernel sizes every buy from a positive equity, so a book opened without one
    could never decide, and the directory it was opened in would be spoiled for good.
    """
    if not cash.is_finite() or cash <= 0:
        raise ValueError(
            f"opening cash must be a positive amount: {cash}; the kernel sizes every buy from "
            "the book's equity, and a book is opened once per runtime directory"
        )
    snapshot = PaperBookSnapshot(
        config_digest=config_digest,
        session_index=session_index,
        session=session,
        book=PortfolioState(as_of=session, cash=cash),
        last_settled_session=None,
        first_decision_session=None,
    )
    save_snapshot(store, snapshot)
    return snapshot
