"""The forward-registration protocol as a src contract (`V2-P7-013`, R1).

`openalpha_cn.strategy_forward_session` takes R1's family-agnostic protocol out of
`scripts/daily_selection.py`: placing a batch's outcome on the calendar, reusing a record the
session already holds, refusing at or after the next call auction, writing provenance before the
record, and the journal's `prediction` block. `scripts/daily_selection.py` keeps its CLI and
survivor-specific code and delegates here; Plan C's step imports this module and nothing from
`scripts/`.

These tests were red on the unchanged tree (`ModuleNotFoundError`) and pin the exact refusals and
ordering the move has to preserve, not just "a function exists".
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, Final

import grimp
import pytest
from panel_fixtures import EXCHANGE
from strategy_fixtures import READ_AT, REVERSAL, write_strategy_corpus

from openalpha_cn import strategy_forward_session as forward
from openalpha_cn.domain.prediction_record import outcome_known_at_for
from openalpha_cn.panel.store import PanelStore
from openalpha_cn.panel_ingest import session_publication_instant
from openalpha_cn.runtime.composition import build_storage
from openalpha_cn.strategy_registration import (
    RegisteredConfiguration,
    StrategyRegistrationError,
    batch_digest,
    registration_cutoff,
    signal_day_batch,
)
from openalpha_cn.strategy_view import score_day, strategy_request

REGISTERED: Final = RegisteredConfiguration(
    config_id="3" * 64, registration_sha256="0" * 64, code_commit="2" * 40, seed=20260926
)

_BASE: Final[dict[str, Any]] = {
    "components": ((REVERSAL.qualified_key, "raw", Decimal("1")),),
    "combine": "zscore_sum",
    "transform": None,
    "neutralization": None,
    "exchange": EXCHANGE,
    "rebalance_every_sessions": 3,
    "holding_count": 3,
    "buffer_rank": None,
    "max_industry_weight": None,
}
"""`tests/unit/scripts/test_daily_selection.py`'s static configuration, copied: the batch in
these tests is built the way the command's step 7 builds one."""


def _evening(day: date, *, hours: int = 2) -> datetime:
    return session_publication_instant(day) + timedelta(hours=hours)


def _read_at(day: date) -> datetime:
    return max(READ_AT, _evening(day))


def _corpus_batch(tmp_path: Path) -> tuple[Any, Any, date, datetime]:
    """A real session's scores as step 7 files them, with its outcome calendar and filing time."""
    panel = write_strategy_corpus(tmp_path)
    store = PanelStore(tmp_path / "panel")
    session = panel.sessions[1]
    request = strategy_request(
        **_BASE, start=session - timedelta(days=1), end=session, as_of=_read_at(session)
    )
    signal = score_day(store, request, day=session, anchor=panel.sessions[1])
    filed = _evening(session)
    batch = signal_day_batch(signal, request, REGISTERED, predicted_at=filed)
    assert batch is not None
    calendar = forward.outcome_calendar(store, request.exchange, session, request.as_of)
    return batch, calendar, session, filed


def _store(tmp_path: Path, clock: Any) -> Any:
    return build_storage(runtime_dir=tmp_path, clock=clock).prediction_store


def _files(tmp_path: Path) -> dict[str, bytes]:
    directory = tmp_path / "predictions"
    if not directory.is_dir():
        return {}
    return {path.name: path.read_bytes() for path in sorted(directory.glob("*.json"))}


def test_file_forward_record_files_once_and_then_reuses_the_record(tmp_path: Path) -> None:
    batch, calendar, _session, filed = _corpus_batch(tmp_path)
    clock = lambda: filed  # noqa: E731  (held still, as the command's own clock is)
    store = _store(tmp_path, clock)

    record, outcome = forward.file_forward_record(store, batch, calendar=calendar, clock=clock)
    filed_files = _files(tmp_path)

    assert (outcome, record.standing) == ("created", "forward")
    assert filed_files

    again, again_outcome = forward.file_forward_record(store, batch, calendar=calendar, clock=clock)

    assert (again.record_id, again_outcome) == (record.record_id, "unchanged")
    assert _files(tmp_path) == filed_files


def test_file_forward_record_refuses_at_the_call_auction_with_the_bare_message(
    tmp_path: Path,
) -> None:
    """`.detail` is the message without the script's `step N (prediction) failed: ` prefix -- the
    script, not this module, owns the step numbering."""
    batch, calendar, session, _filed = _corpus_batch(tmp_path)
    cutoff = registration_cutoff(calendar, session)
    deadline = outcome_known_at_for(batch, calendar=calendar, zone=forward.SHANGHAI)
    store = _store(tmp_path, lambda: cutoff)

    with pytest.raises(forward.ForwardSessionRefused) as refused:
        forward.file_forward_record(store, batch, calendar=calendar, clock=lambda: cutoff)

    detail = (
        f"the scores of {session.isoformat()} are traded at the next session's open, whose call "
        f"auction starts {cutoff.isoformat()}, and it is {cutoff.isoformat()}: part of their "
        f"outcome is published already (all of it by {deadline.isoformat()}), so registered "
        "now they would not be a prediction made before its outcome. Nothing was filed"
    )
    assert refused.value.detail == detail
    assert str(refused.value) == detail
    assert refused.value.__cause__ is None
    assert _files(tmp_path) == {}


def test_file_forward_record_wraps_a_conflicting_held_record(tmp_path: Path) -> None:
    batch, calendar, _session, filed = _corpus_batch(tmp_path)
    store = _store(tmp_path, lambda: filed)
    record, _outcome = forward.file_forward_record(
        store, batch, calendar=calendar, clock=lambda: filed
    )
    altered = batch.model_copy(update={"predictions": batch.predictions[:-1]})

    with pytest.raises(forward.ForwardSessionRefused) as refused:
        forward.file_forward_record(store, altered, calendar=calendar, clock=lambda: filed)

    cause = refused.value.__cause__
    assert isinstance(cause, StrategyRegistrationError)
    assert str(cause) == refused.value.detail
    assert refused.value.detail == (
        f"{record.record_id} already registers {batch.as_of.isoformat()} under this declaration "
        "with other scores; the registered record stands and nothing was filed. The panel or a "
        "factor build changed after it was registered"
    )


def test_file_with_provenance_reuses_a_held_record_and_writes_nothing(tmp_path: Path) -> None:
    batch, calendar, session, filed = _corpus_batch(tmp_path)
    store = _store(tmp_path, lambda: filed)
    record, _outcome = forward.file_forward_record(
        store, batch, calendar=calendar, clock=lambda: filed
    )
    calls: list[str] = []

    block = forward.file_with_provenance(
        store=store,
        batch=batch,
        now=filed + timedelta(minutes=5),
        cutoff=registration_cutoff(calendar, session),
        late=lambda now: f"late at {now.isoformat()}",
        held_provenance=lambda held: calls.append("held") or "digest-of-the-held-provenance",  # type: ignore[func-returns-value]
        write_provenance=lambda: calls.append("write") or "digest-written-now",  # type: ignore[func-returns-value]
        register=lambda batch: calls.append("register") or (record, "created"),  # type: ignore[func-returns-value]
    )

    assert calls == ["held"]
    assert (block["outcome"], block["provenance"]) == ("unchanged", "digest-of-the-held-provenance")
    assert block["record_id"] == record.record_id


def test_file_with_provenance_refuses_late_before_calling_anything(tmp_path: Path) -> None:
    batch, calendar, session, filed = _corpus_batch(tmp_path)
    store = _store(tmp_path, lambda: filed)
    cutoff = registration_cutoff(calendar, session)
    calls: list[str] = []

    with pytest.raises(forward.ForwardSessionRefused) as refused:
        forward.file_with_provenance(
            store=store,
            batch=batch,
            now=cutoff,
            cutoff=cutoff,
            late=lambda now: f"late at {now.isoformat()}",
            held_provenance=lambda held: calls.append("held") or "digest",  # type: ignore[func-returns-value]
            write_provenance=lambda: calls.append("write") or "digest",  # type: ignore[func-returns-value]
            register=lambda batch: calls.append("register") or (None, "created"),  # type: ignore[func-returns-value]
        )

    assert refused.value.detail == f"late at {cutoff.isoformat()}"
    assert refused.value.__cause__ is None
    assert calls == []


def test_file_with_provenance_writes_before_it_registers_and_returns_that_digest(
    tmp_path: Path,
) -> None:
    batch, calendar, session, filed = _corpus_batch(tmp_path)
    store = _store(tmp_path, lambda: filed)
    calls: list[str] = []

    def write_provenance() -> str:
        calls.append("write-provenance")
        return "digest-written-before-the-record"

    def register(queued: Any) -> tuple[Any, str]:
        calls.append("register")
        return forward.file_forward_record(store, queued, calendar=calendar, clock=lambda: filed)

    block = forward.file_with_provenance(
        store=store,
        batch=batch,
        now=filed,
        cutoff=registration_cutoff(calendar, session),
        late=lambda now: f"late at {now.isoformat()}",
        held_provenance=lambda held: "digest-of-the-held-provenance",
        write_provenance=write_provenance,
        register=register,
    )

    assert calls == ["write-provenance", "register"]
    assert (block["outcome"], block["provenance"]) == (
        "created",
        "digest-written-before-the-record",
    )


def test_prediction_block_is_the_thirteen_key_journal_stage(tmp_path: Path) -> None:
    batch, calendar, _session, filed = _corpus_batch(tmp_path)
    store = _store(tmp_path, lambda: filed)
    record, outcome = forward.file_forward_record(
        store, batch, calendar=calendar, clock=lambda: filed
    )

    block = forward.prediction_block(record, outcome, provenance="digest-of-the-provenance")

    assert set(block) == {
        "registered",
        "record_id",
        "family",
        "horizon",
        "outcome",
        "standing",
        "as_of",
        "outcome_known_at",
        "recorded_at",
        "scored",
        "abstained",
        "batch_digest",
        "provenance",
    }
    assert block["registered"] is True
    assert block["record_id"] == record.record_id
    assert block["family"] == record.batch.artifact.declaration.family
    assert block["horizon"] == record.batch.artifact.declaration.horizon
    assert block["outcome"] == outcome
    assert block["standing"] == record.standing
    assert block["as_of"] == record.batch.as_of.isoformat()
    assert block["outcome_known_at"] == record.outcome_known_at.isoformat()
    assert block["recorded_at"] == record.recorded_at.isoformat()
    assert block["scored"] == len(record.batch.scored)
    assert block["abstained"] == len(record.batch.abstained)
    assert block["batch_digest"] == batch_digest(record.batch)
    assert block["provenance"] == "digest-of-the-provenance"


def test_the_forward_session_module_reaches_no_face_and_no_storage_layer() -> None:
    """`D3`: registration modules take their stores injected. The module may read the panel
    plane and `strategy_registration`; it may not reach a face, the composition root, the engine,
    or `storage` (including `storage.predictions`, whose `put` it calls through the Protocol)."""
    graph = grimp.build_graph("openalpha_cn", cache_dir=None)
    upstream = graph.find_upstream_modules("openalpha_cn.strategy_forward_session")
    forbidden = (
        "openalpha_cn.agents",
        "openalpha_cn.api",
        "openalpha_cn.cli",
        "openalpha_cn.product",
        "openalpha_cn.runtime",
        "openalpha_cn.sdk",
        "openalpha_cn.shortlist_view",
        "openalpha_cn.storage",
    )
    reached = sorted(
        module
        for module in upstream
        if any(module == root or module.startswith(root + ".") for root in forbidden)
    )

    assert reached == []
