"""`V2-P7-023`: a survivor registration's confirmatory sample, continued across registrations by
recompute equivalence (`strategy_survivor_continuation`). This part: the block, the chain's link
and what may be continued into what."""

from __future__ import annotations

import dataclasses
import json
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Final

import pytest
from panel_fixtures import EXCHANGE
from test_strategy_survivor_check import (
    evening_store,  # noqa: F401  (module fixture, reused)
    filing,
    frame_root,  # noqa: F401  (module fixture, reused)
)
from test_strategy_survivor_registration import (
    BOOK,
    MAX_CARRY_SESSIONS,
    MIN_COVERAGE,
    REGISTERED,
    SECOND_TRANSFORM,
    book_with,
)
from test_survivor_facts_panel import IDENTITY, SIGNAL

from openalpha_cn import strategy_survivor_continuation as continuation_module
from openalpha_cn.domain.survivor_book import SurvivorBookConfig
from openalpha_cn.panel.store import PanelStore
from openalpha_cn.panel_factors import FACTOR_TRANSFORMS
from openalpha_cn.strategy_registration import RegisteredConfiguration
from openalpha_cn.strategy_survivor import read_survivor_registration
from openalpha_cn.strategy_survivor_check import SurvivorRecordVerdict
from openalpha_cn.strategy_survivor_continuation import (
    AFTER_TRUNCATION,
    DIFFERS,
    IDENTICAL,
    PREDECESSOR_REFUSED,
    RECOMPUTE_FAILED,
    SURVIVOR_CONTINUATION_KEY,
    SURVIVOR_CONTINUATION_SCHEMA,
    ChainLink,
    ContinuationCandidate,
    ContinuationTarget,
    ContinuedDay,
    SurvivorContinuationError,
    continuation_block,
    continuation_bytes,
    continuation_target,
    continued_sample,
    judge_continued_day,
    no_continuation_section,
    read_survivor_continuation,
    require_continuable,
)
from openalpha_cn.strategy_survivor_forward import WitnessedRecord

SEED: Final[int] = 7
ADAPTER: Final[dict[str, Any]] = {
    "schema_version": "survivor-adapter/v1",
    "epoch_session": "2026-04-03",
    "exchange": "SSE",
    "transform": "cross_section_standard/v1",
    "min_coverage": 0.5,
    "max_carry_sessions": 250,
    "max_panel_catch_up_sessions": 10,
    "benchmark": "000905.SH",
    "prediction_horizon": "20d",
    "cost_policy": "protocol/v1",
    "paper_limits": {"max_position_weight": "1", "max_total_exposure": "1", "min_cash_weight": "0"},
    "unfilled_buy": "reject_whole_and_carry/v1",
}
PROTOCOL: Final[dict[str, Any]] = {"schema_version": "survivor-forward-protocol/v1", "k": 1}
"""Compared as JSON text only here; its validity is the forward module's business."""
OLD: Final[str] = "a" * 64
NEW: Final[str] = "b" * 64


def link(
    sha256: str,
    *,
    epoch: str = "2026-04-03",
    settings: dict[str, Any] | None = None,
) -> ChainLink:
    config = {
        "kind": "survivor_book/v1",
        "survivor_book": SurvivorBookConfig(seed=SEED).model_dump(
            mode="json", exclude_computed_fields=True
        ),
        "adapter": {**ADAPTER, "epoch_session": epoch},
    }
    return ChainLink(
        registration_sha256=sha256,
        config_id=f"rcf_{sha256[:24]}",
        code_commit="c" * 40,
        settings={"random_seed": SEED, "survivor_forward_protocol": PROTOCOL, **(settings or {})},
        survivor=read_survivor_registration(config, seed=SEED),
    )


def with_adapter(base: ChainLink, **changes: object) -> ChainLink:
    adapter = base.survivor.adapter.model_copy(update=changes)
    survivor = type(base.survivor)(book=base.survivor.book, adapter=adapter)
    return ChainLink(
        registration_sha256=base.registration_sha256,
        config_id=base.config_id,
        code_commit=base.code_commit,
        settings=base.settings,
        survivor=survivor,
    )


# --- the block ---------------------------------------------------------------------------------


def test_no_block_is_no_predecessor() -> None:
    assert read_survivor_continuation({"random_seed": 7}) is None


def test_the_block_written_for_a_registration_reads_back() -> None:
    block = continuation_block(path="docs/research/p7-survivor.json", sha256=OLD, config_id="rcf_x")
    assert block == {
        "schema_version": SURVIVOR_CONTINUATION_SCHEMA,
        "supersedes_path": "docs/research/p7-survivor.json",
        "supersedes_sha256": OLD,
        "supersedes_config_id": "rcf_x",
    }
    read = read_survivor_continuation({SURVIVOR_CONTINUATION_KEY: block})
    assert read is not None
    assert (read.supersedes_path, read.supersedes_sha256, read.supersedes_config_id) == (
        "docs/research/p7-survivor.json",
        OLD,
        "rcf_x",
    )


@pytest.mark.parametrize(
    ("changes", "named"),
    [
        ({"extra": 1}, "survivor_continuation.extra"),
        ({"supersedes_sha256": "A" * 64}, "survivor_continuation.supersedes_sha256"),
        ({"supersedes_path": "/abs/p.json"}, "survivor_continuation.supersedes_path"),
        ({"supersedes_path": "docs/../p.json"}, "survivor_continuation.supersedes_path"),
        ({"supersedes_path": "docs\\p.json"}, "survivor_continuation.supersedes_path"),
        ({"supersedes_path": "docs/p.txt"}, "survivor_continuation.supersedes_path"),
        ({"schema_version": "survivor-continuation/v2"}, "survivor_continuation.schema_version"),
        ({"supersedes_config_id": ""}, "survivor_continuation.supersedes_config_id"),
    ],
    ids=["extra", "sha", "absolute", "dotdot", "backslash", "suffix", "schema", "config_id"],
)
def test_a_block_that_does_not_read_is_refused_by_field(
    changes: dict[str, Any], named: str
) -> None:
    block = {**continuation_block(path="docs/p.json", sha256=OLD, config_id="rcf_x"), **changes}
    with pytest.raises(SurvivorContinuationError, match=named.replace(".", r"\.")):
        read_survivor_continuation({SURVIVOR_CONTINUATION_KEY: block})


def test_a_block_with_a_missing_field_is_refused_never_defaulted() -> None:
    block = continuation_block(path="docs/p.json", sha256=OLD, config_id="rcf_x")
    del block["supersedes_config_id"]
    with pytest.raises(SurvivorContinuationError, match="supersedes_config_id"):
        read_survivor_continuation({SURVIVOR_CONTINUATION_KEY: block})


def test_a_block_that_is_not_an_object_is_refused() -> None:
    with pytest.raises(SurvivorContinuationError, match="not a survivor-continuation/v1 object"):
        read_survivor_continuation({SURVIVOR_CONTINUATION_KEY: "docs/p.json"})


# --- what may be continued into what -----------------------------------------------------------


def test_a_compatible_predecessor_is_accepted() -> None:
    require_continuable(link(NEW, epoch="2026-05-08"), link(OLD))


def test_a_registration_naming_itself_is_refused() -> None:
    with pytest.raises(SurvivorContinuationError, match="names itself as its predecessor"):
        require_continuable(link(OLD, epoch="2026-05-08"), link(OLD))


@pytest.mark.parametrize(
    ("field", "value"),
    [("exchange", "SZSE"), ("prediction_horizon", "5d"), ("benchmark", "000300.SH")],
)
def test_another_exchange_horizon_or_benchmark_is_refused_by_name(field: str, value: str) -> None:
    predecessor = with_adapter(link(OLD), **{field: value})
    with pytest.raises(SurvivorContinuationError, match=f"adapter\\.{field}"):
        require_continuable(link(NEW, epoch="2026-05-08"), predecessor)


@pytest.mark.parametrize(
    "key", ["survivor_forward_protocol", "survivor_forward_power", "survivor_forward_stop_rule"]
)
def test_a_pre_registered_block_that_differs_is_refused_by_name(key: str) -> None:
    predecessor = link(OLD, settings={key: {"schema_version": "x", "v": 1}})
    successor = link(NEW, epoch="2026-05-08", settings={key: {"schema_version": "x", "v": 2}})
    with pytest.raises(SurvivorContinuationError, match=f"settings\\.{key}"):
        require_continuable(successor, predecessor)


def test_a_block_present_on_one_side_only_differs() -> None:
    predecessor = link(OLD, settings={"survivor_forward_power": {"schema_version": "x"}})
    with pytest.raises(SurvivorContinuationError, match="survivor_forward_power"):
        require_continuable(link(NEW, epoch="2026-05-08"), predecessor)


@pytest.mark.parametrize("epoch", ["2026-04-03", "2026-04-02"])
def test_a_predecessor_whose_epoch_is_not_before_is_refused(epoch: str) -> None:
    with pytest.raises(SurvivorContinuationError, match="is not before"):
        require_continuable(link(NEW, epoch=epoch), link(OLD))


def test_a_smoke_predecessor_never_enters_a_registration_that_is_not_one() -> None:
    smoke = link(OLD, settings={"smoke_run": {"issue": "V2-P7-019"}})
    with pytest.raises(SurvivorContinuationError, match="smoke registration"):
        require_continuable(link(NEW, epoch="2026-05-08"), smoke)
    # A smoke successor may continue a smoke predecessor: both are tests of the machinery.
    require_continuable(
        link(NEW, epoch="2026-05-08", settings={"smoke_run": {"issue": "V2-P7-019"}}), smoke
    )


def test_another_exchange_is_refused_before_a_later_epoch_or_a_smoke_predecessor() -> None:
    """Three refusals would fire here; the exchange is the one named, because the order is the
    contract (the first check a reader can rely on is the one that speaks)."""
    predecessor = with_adapter(link(OLD), exchange="SZSE", epoch_session=date(2026, 5, 8))
    predecessor = dataclasses.replace(
        predecessor, settings={**predecessor.settings, "smoke_run": {"issue": "V2-P7-019"}}
    )

    with pytest.raises(SurvivorContinuationError, match=r"adapter\.exchange") as raised:
        require_continuable(link(NEW, epoch="2026-05-08"), predecessor)

    message = str(raised.value)
    assert "epoch_session" not in message and "smoke registration" not in message


def test_another_protocol_block_is_refused_before_a_later_epoch() -> None:
    predecessor = link(
        OLD,
        epoch="2026-05-08",
        settings={"survivor_forward_power": {"schema_version": "x", "v": 1}},
    )
    successor = link(
        NEW,
        epoch="2026-05-08",
        settings={"survivor_forward_power": {"schema_version": "x", "v": 2}},
    )

    with pytest.raises(
        SurvivorContinuationError, match=r"settings\.survivor_forward_power"
    ) as raised:
        require_continuable(successor, predecessor)

    assert "is not before" not in str(raised.value)


def test_a_links_declaration_is_its_registrations() -> None:
    declared = link(OLD).declared
    assert (declared.registration_sha256, declared.config_id, declared.seed) == (
        OLD,
        f"rcf_{OLD[:24]}",
        SEED,
    )
    assert link(OLD).epoch == date(2026, 4, 3)
    assert json.loads(json.dumps(continuation_block(path="a.json", sha256=OLD, config_id="r")))


SUCCESSOR: Final[RegisteredConfiguration] = RegisteredConfiguration(
    config_id="rcf_0000successor0000000000",
    registration_sha256="ab" * 32,
    code_commit="1234567abcdef",
    seed=REGISTERED.seed,
)


def target(*, book: SurvivorBookConfig = BOOK, transform: Any = IDENTITY) -> ContinuationTarget:
    return ContinuationTarget(
        book=book,
        transform=transform,
        exchange=EXCHANGE,
        min_coverage=MIN_COVERAGE,
        max_carry_sessions=MAX_CARRY_SESSIONS,
        registered=SUCCESSOR,
    )


def candidate_of(
    held: Any, *, own: str = "verified", session: date = SIGNAL
) -> ContinuationCandidate:
    record = held.record
    verdict = SurvivorRecordVerdict(
        record_id=record.record_id,
        session=session,
        verdict=own,  # type: ignore[arg-type]
        scope=None if own == "verified" else "no_provenance",
        reason=None if own == "verified" else "scripted refusal",
    )
    return ContinuationCandidate(
        item=WitnessedRecord(session=session, ordinal=1, record_id=record.record_id, record=record),
        registration_sha256=REGISTERED.registration_sha256,
        own=verdict,
        provenance=held.provenance,
    )


@pytest.fixture
def recomputed(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    called: list[str] = []
    real = continuation_module.recompute_survivor_batch

    def counted(store: PanelStore, record: Any, **arguments: Any) -> Any:
        called.append(record.record_id)
        return real(store, record, **arguments)

    monkeypatch.setattr(continuation_module, "recompute_survivor_batch", counted)
    return called


# --- the target ---------------------------------------------------------------------------------


def test_a_target_is_read_off_the_successor_registration() -> None:
    successor = link(NEW, epoch="2026-05-08")
    adapter = successor.survivor.adapter

    read = continuation_target(successor)

    assert read.transform is FACTOR_TRANSFORMS.get("cross_section_standard/v1")
    assert read.registered == successor.declared
    assert (read.book, read.exchange, read.min_coverage, read.max_carry_sessions) == (
        successor.survivor.book,
        adapter.exchange,
        adapter.min_coverage,
        adapter.max_carry_sessions,
    )


# --- the bytes ---------------------------------------------------------------------------------


def test_two_batches_differing_only_in_the_registrations_identity_have_the_same_bytes(
    evening_store: PanelStore,  # noqa: F811
    tmp_path: Path,
) -> None:
    batch = filing(evening_store, tmp_path).record.batch
    declaration = batch.artifact.declaration.model_copy(
        update={
            "feature_version": SUCCESSOR.config_id,
            "code_commit": SUCCESSOR.code_commit,
            "seed": batch.artifact.declaration.seed + 1,
            "hyperparameters": (("registration_sha256", SUCCESSOR.registration_sha256),),
        }
    )
    renamed = batch.model_copy(
        update={"artifact": batch.artifact.model_copy(update={"declaration": declaration})}
    )
    assert continuation_bytes(renamed) == continuation_bytes(batch)

    first, *rest = batch.predictions
    rescored = batch.model_copy(
        update={"predictions": (first.model_copy(update={"score": first.score + 1e-12}), *rest)}
    )
    assert continuation_bytes(rescored) != continuation_bytes(batch)
    rehorizoned = batch.model_copy(
        update={
            "artifact": batch.artifact.model_copy(
                update={
                    "declaration": batch.artifact.declaration.model_copy(update={"horizon": "5d"})
                }
            )
        }
    )
    assert continuation_bytes(rehorizoned) != continuation_bytes(batch)


def test_two_batches_whose_declarations_name_another_model_have_other_bytes(
    evening_store: PanelStore,  # noqa: F811
    tmp_path: Path,
) -> None:
    batch = filing(evening_store, tmp_path).record.batch
    declaration = batch.artifact.declaration.model_copy(update={"name": "another-model"})
    renamed = batch.model_copy(
        update={"artifact": batch.artifact.model_copy(update={"declaration": declaration})}
    )

    assert continuation_bytes(renamed) != continuation_bytes(batch)


def test_two_batches_whose_declarations_are_another_family_have_other_bytes(
    evening_store: PanelStore,  # noqa: F811
    tmp_path: Path,
) -> None:
    batch = filing(evening_store, tmp_path).record.batch
    declaration = batch.artifact.declaration.model_copy(update={"family": "another-family"})
    renamed = batch.model_copy(
        update={"artifact": batch.artifact.model_copy(update={"declaration": declaration})}
    )

    assert continuation_bytes(renamed) != continuation_bytes(batch)


# --- one day, on the frame: identical, different, cannot be scored --------------------------


def test_a_day_the_successor_scores_the_same_is_identical(
    evening_store: PanelStore,  # noqa: F811
    tmp_path: Path,
    recomputed: list[str],
) -> None:
    """Another registration and another book that scores the same (only its capital differs):
    the day is the same batch, so it joins the successor's sample."""
    held = filing(evening_store, tmp_path)
    successor_book = book_with(capital=Decimal("400000"))
    assert successor_book.config_id != BOOK.config_id

    day = judge_continued_day(evening_store, candidate_of(held), target=target(book=successor_book))

    assert (day.outcome, day.reason) == (IDENTICAL, None)
    assert recomputed == [held.record.record_id]


def test_a_day_the_successor_scores_otherwise_differs(
    evening_store: PanelStore,  # noqa: F811
    tmp_path: Path,
) -> None:
    """Three of the four composite factors: other scores for the same names."""
    held = filing(evening_store, tmp_path)
    fewer = book_with(composite_factors=list(BOOK.composite_factors[:3]))

    day = judge_continued_day(evening_store, candidate_of(held), target=target(book=fewer))

    assert day.outcome == DIFFERS
    assert day.reason is not None and "differ" in day.reason


def test_a_day_whose_scores_are_equal_but_whose_training_cutoff_moved_differs(
    evening_store: PanelStore,  # noqa: F811
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The comparison is the whole batch, not the scores: the same names and numbers scored off
    another training history are another batch."""
    held = filing(evening_store, tmp_path)
    real = continuation_module.recompute_survivor_batch

    def shifted(store: PanelStore, record: Any, **arguments: Any) -> Any:
        again = real(store, record, **arguments)
        artifact = again.artifact.model_copy(
            update={"training_cutoff": again.artifact.training_cutoff - timedelta(days=1)}
        )
        return again.model_copy(update={"artifact": artifact})

    monkeypatch.setattr(continuation_module, "recompute_survivor_batch", shifted)

    day = judge_continued_day(evening_store, candidate_of(held), target=target())

    assert day.outcome == DIFFERS
    assert day.reason is not None and "differ" in day.reason


def test_a_day_the_successor_cannot_score_again_is_recompute_failed(
    evening_store: PanelStore,  # noqa: F811
    tmp_path: Path,
) -> None:
    """A transform the evening's store holds no 16:30 build of: a named refusal of the data."""
    held = filing(evening_store, tmp_path)

    day = judge_continued_day(
        evening_store, candidate_of(held), target=target(transform=SECOND_TRANSFORM)
    )

    assert day.outcome == RECOMPUTE_FAILED
    assert day.reason is not None


def test_a_day_its_own_check_refused_is_never_scored_again(
    evening_store: PanelStore,  # noqa: F811
    tmp_path: Path,
    recomputed: list[str],
) -> None:
    held = filing(evening_store, tmp_path)

    day = judge_continued_day(evening_store, candidate_of(held, own="refused"), target=target())

    assert (day.outcome, day.reason) == (PREDECESSOR_REFUSED, "scripted refusal")
    assert recomputed == []


def test_a_candidate_without_a_provenance_is_a_callers_error_and_is_not_scored(
    evening_store: PanelStore,  # noqa: F811
    tmp_path: Path,
    recomputed: list[str],
) -> None:
    """Only a refused record may be judged without the holdings its evening read."""
    held = filing(evening_store, tmp_path)
    candidate = dataclasses.replace(candidate_of(held), provenance=None)

    with pytest.raises(ValueError, match="carries no provenance"):
        judge_continued_day(evening_store, candidate, target=target())

    assert recomputed == []


def test_a_provenance_that_names_another_batch_is_a_callers_error(
    evening_store: PanelStore,  # noqa: F811
    tmp_path: Path,
    recomputed: list[str],
) -> None:
    held = filing(evening_store, tmp_path)
    candidate = dataclasses.replace(
        candidate_of(held),
        provenance=dataclasses.replace(held.provenance, batch_digest="0" * 64),
    )

    with pytest.raises(ValueError, match="provenance does not belong to") as raised:
        judge_continued_day(evening_store, candidate, target=target())

    assert candidate.item.session.isoformat() in str(raised.value)
    assert recomputed == []


def test_a_refused_day_without_a_reason_says_the_check_refused_it(
    evening_store: PanelStore,  # noqa: F811
    tmp_path: Path,
    recomputed: list[str],
) -> None:
    """A refused verdict names a reason in this build; a caller's stub that does not still gets
    a stated reason rather than a day with none."""
    held = filing(evening_store, tmp_path)
    candidate = dataclasses.replace(
        candidate_of(held, own="refused"),
        own=SimpleNamespace(verdict="refused", reason=None),
    )

    day = judge_continued_day(evening_store, candidate, target=target())

    assert (day.outcome, day.reason) == (
        PREDECESSOR_REFUSED,
        f"the predecessor's own check refused {held.record.record_id}",
    )
    assert recomputed == []


# --- the order and the truncation --------------------------------------------------------------


def _scripted(outcomes: dict[date, str]) -> tuple[list[date], Any]:
    asked: list[date] = []

    def judge(candidate: ContinuationCandidate) -> ContinuedDay:
        asked.append(candidate.item.session)
        outcome = outcomes[candidate.item.session]
        return ContinuedDay(
            candidate=candidate,
            outcome=outcome,  # type: ignore[arg-type]
            reason=None if outcome == IDENTICAL else f"scripted {outcome}",
        )

    return asked, judge


def test_the_first_day_that_is_not_identical_truncates_and_nothing_after_it_is_asked(
    evening_store: PanelStore,  # noqa: F811
    tmp_path: Path,
) -> None:
    held = filing(evening_store, tmp_path)
    days = [SIGNAL - timedelta(days=3), SIGNAL - timedelta(days=2), SIGNAL]
    candidates = [candidate_of(held, session=day) for day in reversed(days)]  # any order in
    asked, judge = _scripted({days[0]: IDENTICAL, days[1]: DIFFERS, days[2]: IDENTICAL})

    result = continued_sample(
        candidates, judge=judge, chain=(), sample_epoch=SIGNAL - timedelta(days=10)
    )

    assert asked == days[:2]
    assert [day.outcome for day in result.days] == [IDENTICAL, DIFFERS, AFTER_TRUNCATION]
    assert [c.item.session for c in result.merged] == [days[0]]
    assert result.truncated_at is not None and result.truncated_at.candidate.item.session == days[1]
    document = result.document()
    assert document["merged"] == 1 and document["candidates"] == 3
    assert document["truncated_at"] == days[1].isoformat()
    assert document["truncation"]["outcome"] == DIFFERS
    assert document["days"][2]["reason"] == f"after the truncation at {days[1].isoformat()}"


def test_every_identical_day_joins_and_nothing_is_truncated(
    evening_store: PanelStore,  # noqa: F811
    tmp_path: Path,
) -> None:
    held = filing(evening_store, tmp_path)
    days = [SIGNAL - timedelta(days=1), SIGNAL]
    asked, judge = _scripted({day: IDENTICAL for day in days})

    result = continued_sample(
        [candidate_of(held, session=day) for day in days],
        judge=judge,
        chain=(),
        sample_epoch=SIGNAL - timedelta(days=10),
    )

    assert asked == days
    assert [c.item.session for c in result.merged] == days
    assert result.truncated_at is None and result.document()["truncation"] is None


def test_two_records_for_one_session_or_one_before_the_epoch_refuse(
    evening_store: PanelStore,  # noqa: F811
    tmp_path: Path,
) -> None:
    held = filing(evening_store, tmp_path)
    _asked, judge = _scripted({SIGNAL: IDENTICAL})
    with pytest.raises(SurvivorContinuationError, match="two predecessor records for"):
        continued_sample(
            [candidate_of(held), candidate_of(held)],
            judge=judge,
            chain=(),
            sample_epoch=SIGNAL - timedelta(days=10),
        )
    with pytest.raises(SurvivorContinuationError, match="is not after the sample's epoch"):
        continued_sample([candidate_of(held)], judge=judge, chain=(), sample_epoch=SIGNAL)


def test_a_day_names_its_reason_exactly_when_it_is_not_identical(
    evening_store: PanelStore,  # noqa: F811
    tmp_path: Path,
) -> None:
    held = filing(evening_store, tmp_path)
    with pytest.raises(ValueError):
        ContinuedDay(candidate=candidate_of(held), outcome=IDENTICAL, reason="why")
    with pytest.raises(ValueError):
        ContinuedDay(candidate=candidate_of(held), outcome=DIFFERS, reason=None)


def test_a_registration_with_no_predecessor_says_so() -> None:
    section = no_continuation_section()
    assert section["chain"] == [] and section["merged"] == 0 and section["days"] == []
    assert section["statement"].startswith("this registration names no predecessor")
    assert section["wording"] and section["rule"]
