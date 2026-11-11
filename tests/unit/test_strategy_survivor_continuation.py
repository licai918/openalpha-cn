"""`V2-P7-023`: a survivor registration's confirmatory sample, continued across registrations by
recompute equivalence (`strategy_survivor_continuation`). This part: the block, the chain's link
and what may be continued into what."""

from __future__ import annotations

import json
from datetime import date
from typing import Any, Final

import pytest

from openalpha_cn.domain.survivor_book import SurvivorBookConfig
from openalpha_cn.strategy_survivor import read_survivor_registration
from openalpha_cn.strategy_survivor_continuation import (
    SURVIVOR_CONTINUATION_KEY,
    SURVIVOR_CONTINUATION_SCHEMA,
    ChainLink,
    SurvivorContinuationError,
    continuation_block,
    read_survivor_continuation,
    require_continuable,
)

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


def test_a_links_declaration_is_its_registrations() -> None:
    declared = link(OLD).declared
    assert (declared.registration_sha256, declared.config_id, declared.seed) == (
        OLD,
        f"rcf_{OLD[:24]}",
        SEED,
    )
    assert link(OLD).epoch == date(2026, 4, 3)
    assert json.loads(json.dumps(continuation_block(path="a.json", sha256=OLD, config_id="r")))
