"""Limit events reach the evidence plane as `EvidenceSnapshot`s (`V2-P7-011`, Task 3).

The panel adapter produces the events; this module is the boundary that turns them into stored,
content-addressed evidence without inventing a fact. The split is the one this repository keeps
between `domain/` (the rule), the research-plane adapter (the read) and `evidence/` (the
normalisation), and it exists here for a measured reason rather than a layering taste: D1 puts
limit events on the **evidence plane** because they are discrete, per-security, ~10² rows a day,
while the panel plane holds the ~5.5k-row cross sections that are fatal to
`ParquetEvidenceStore`'s per-row rebuild-and-rehash (audit F71-F75, hard rule 1).

## The three kinds are the builder's own, not new vocabulary

`evidence/builder.py` already declares `limit_up`, `consecutive_board` and `broken_board` as
`market_event` kinds with the facts models they normalize, and `LimitEvent.facts()` is shaped
for exactly those models -- `close`, `high`, `pct_change`, `board_count`, `open_count`. So a
snapshot here goes through `EvidenceBuilder.build`, the one place a provider record becomes
evidence, rather than assembling `EvidenceSnapshot` by hand and re-deriving what the builder
already owns (the redistribution flag, the schema block, the quality flags).

## The four clocks, and why `ingested_time` is the one that moves

- `event_time` is the session's close, 15:00 Asia/Shanghai.
- `available_time` and `revision_time` are the session's own publication instant, 16:30
  Asia/Shanghai (`panel_ingest.session_publication_instant`, the same function the price rows'
  clock is built from). A limit event is knowable when the session it is about is knowable, and
  nothing about it is published later.
- `ingested_time` is when this run wrote it.

`evidence_id` folds `subject`, `kind`, `source_id`, `available_time` and the payload's content
hash -- and **not** `ingested_time` -- so re-running a session at a later clock is the same
batch of evidence, and `ParquetEvidenceStore.append` returns the partition that already exists
rather than a second copy. That is `test_rerunning_a_session_writes_no_second_partition`.

## A restated session is refused, not appended

`ParquetEvidenceStore.append` dedups only on the sorted `evidence_id` set: two runs that derive
**different** events for one session produce two partitions, both readable, and a reader asking
"what happened on D" gets two answers. `refuse_a_restated_session` asks the store for the
limit-kind evidence already visible at D's own publication instant and refuses by name when the
id set differs from the one this run derived. It is a check before the append, so the caller
controls the order; the CLI is the caller here.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Final, cast

from pydantic import JsonValue

from openalpha_cn.domain.daily_prices import SESSION_CLOSE_TIME
from openalpha_cn.domain.evidence import EvidenceSnapshot
from openalpha_cn.domain.limit_events import LimitEvent, LimitEventKind
from openalpha_cn.domain.time import Timeline, ensure_aware
from openalpha_cn.evidence.builder import EvidenceBuilder
from openalpha_cn.evidence.service import EvidenceStore
from openalpha_cn.panel_ingest import session_publication_instant
from openalpha_cn.providers.base import (
    ProviderBatch,
    ProviderMetadata,
    ProviderRecord,
    ProviderRequest,
)

PANEL_DERIVED_PROVIDER_ID: Final[str] = "openalpha-panel-derived"
"""The one provider id these rows name -- derived from the stored panel, not fetched from it.

Not `"tushare"`: `source_id` is what a reader uses to tell which boundary produced a fact, and
this one is `openalpha_cn`'s own arithmetic over two stored Tushare datasets. The licence and
the redistribution term below are Tushare's, because the values are.
"""

PANEL_DERIVED_METADATA: Final[ProviderMetadata] = ProviderMetadata(
    provider_id=PANEL_DERIVED_PROVIDER_ID,
    display_name="OpenAlpha CN panel-derived limit events",
    source_license=(
        "Tushare daily + stk_limit, research use; redistribution restricted by the provider's terms"
    ),
    redistribution="restricted",
    credential_env_vars=(),
    supported_datasets=("daily", "stk_limit"),
    caching_policy="local-permitted",
    rate_limit="not-applicable",
    freshness="the session's own 16:30 Asia/Shanghai publication",
    failure_semantics=(
        "Derived only from stored panel rows; a session that cannot be read raises and no "
        "event is invented."
    ),
)
"""The metadata every panel-derived evidence row carries.

`redistribution="restricted"` because Tushare's terms are, and the builder turns that into the
`redistribution_restricted` quality flag on every snapshot -- the honest marking, not a default
nobody read. `caching_policy="local-permitted"` because these rows are derived from a local
panel rather than a live response.
"""

_LIMIT_EVENT_KINDS: Final[tuple[LimitEventKind, ...]] = (
    "limit_up",
    "consecutive_board",
    "broken_board",
)


class LimitEvidenceConflictError(ValueError):
    """Raised when one session's stored limit events and a new run's disagree by `evidence_id`."""


def limit_event_snapshots(
    events: tuple[LimitEvent, ...],
    *,
    session: date,
    fetched_at: datetime,
) -> tuple[EvidenceSnapshot, ...]:
    """One `EvidenceSnapshot` per event, through `EvidenceBuilder`, in `evidence_id` order.

    `events` are one session's events (`panel_limit_events.limit_events_on` returns them for
    `session`); an event dated any other day is refused, because a batch of clocks derived from
    one session cannot represent another session's fact. `fetched_at` becomes the snapshot's
    `ingested_time` and the provider batch's fetch clock; it may not precede the session's
    publication instant (a write cannot ingest a fact before it was knowable).
    """
    if not events:
        return ()
    point = ensure_aware(fetched_at)
    published = session_publication_instant(session)
    event_time = datetime.combine(session, SESSION_CLOSE_TIME, tzinfo=published.tzinfo)
    for event in events:
        if event.trade_date != session:
            raise ValueError(
                f"{event.ts_code}'s event is dated {event.trade_date.isoformat()} and this batch "
                f"is for {session.isoformat()}; one batch carries one session's facts"
            )
    records = tuple(
        ProviderRecord(
            subject=event.ts_code,
            kind=event.kind,
            timeline=Timeline(
                event_time=event_time,
                available_time=published,
                ingested_time=point,
                revision_time=published,
            ),
            source_uri=f"panel://daily+stk_limit/{session.isoformat()}",
            summary=event.summary(),
            payload=cast(JsonValue, event.facts()),
        )
        for event in sorted(events, key=lambda item: item.ts_code)
    )
    batch = ProviderBatch(
        provider_id=PANEL_DERIVED_PROVIDER_ID,
        request=ProviderRequest(dataset="stk_limit", as_of=point),
        fetched_at=point,
        status="success",
        records=records,
    )
    return EvidenceBuilder().build(batch=batch, metadata=PANEL_DERIVED_METADATA)


def refuse_a_restated_session(
    evidence: EvidenceStore,
    *,
    session: date,
    arriving: tuple[EvidenceSnapshot, ...],
) -> None:
    """Refuse when the store already holds a different id set of limit events for `session`.

    The store is asked at the session's own publication instant, and only rows whose
    `available_time` is that instant are counted -- other sessions' events are visible then too.
    An empty arrival is a legitimate comparison: if the store holds events for the session and
    this run derived none, the panel was restated (or the store holds a claim this run cannot
    reproduce) and that is the same refusal.

    The parameter is named `evidence`, not `store`, deliberately: the **evidence** store is a
    different door from `PanelStore.query`, and `tests/unit/test_source_counts_match_the_tree.py`
    counts `.query(...)` calls on a store-named receiver as callers of the panel door. Naming
    this one `store` would have made that count say five where panel_ingest's own sentence (and
    the allowlist test behind it) says four panel callers.
    """
    published = session_publication_instant(session)
    for snapshot in arriving:
        if snapshot.timeline.available_time != published:
            raise ValueError(
                f"the arriving evidence is available at "
                f"{snapshot.timeline.available_time.isoformat()} and {session.isoformat()} "
                f"published at {published.isoformat()}; one refusal check is about one session"
            )
    stored: dict[str, EvidenceSnapshot] = {}
    for kind in _LIMIT_EVENT_KINDS:
        for snapshot in evidence.query(as_of=published, kind=kind):
            if snapshot.timeline.available_time == published:
                stored[snapshot.evidence_id] = snapshot
    arriving_ids = {snapshot.evidence_id for snapshot in arriving}
    if not stored or set(stored) == arriving_ids:
        return
    only_stored = sorted(set(stored) - arriving_ids)
    only_arriving = sorted(arriving_ids - set(stored))
    raise LimitEvidenceConflictError(
        f"the evidence store already holds {len(stored)} limit event(s) for "
        f"{session.isoformat()} and this run derived {len(arriving_ids)}; "
        f"{len(only_stored)} id(s) are only stored ({only_stored[:3]}) and {len(only_arriving)} "
        f"only arriving ({only_arriving[:3]}). Limit events are content-addressed facts, so two "
        "id sets for one session mean the panel was restated underneath a session that had "
        "already been written. Appending both would leave two answers to one question; "
        "reconcile the restatement (and give the corrected session a new identity) instead of "
        "writing a second version"
    )
