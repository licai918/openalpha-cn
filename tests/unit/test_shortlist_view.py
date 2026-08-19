"""The shortlist face's own rules: its request resolver, its taxonomy, and its clip-block recovery.

The three acceptance properties of `V2-P4-032` / `V2-P4-033` are driven from where a user stands,
in `tests/integration/test_shortlist_interfaces.py`, and deliberately not from here -- a test that
imports this module and calls it would pass on a tree where no face exists, which is the state the
product acceptance found and filed.

What is left for this file is the part a surface cannot separate: the pure rules. The request
resolver touches no store, so every refusal it can raise is provable without one; the fault
taxonomy has to line up with two envelope tables that live in two other modules; and the clip-block
recovery is a stated rule with a stated cost, so it is checked against the cases the rule names
rather than against whatever a fixture happened to contain.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Final

import pytest

from openalpha_cn.api.app import SHORTLIST_HTTP_STATUS
from openalpha_cn.cli import SHORTLIST_EXIT, PanelExit
from openalpha_cn.domain.signal import SignalFrame
from openalpha_cn.factor_view import _PANEL_FAULTS as FACTOR_PANEL_FAULTS
from openalpha_cn.shortlist_view import _PANEL_FAULTS as SHORTLIST_PANEL_FAULTS
from openalpha_cn.shortlist_view import (
    KNOWN_SHORTLIST_VIEW_LIMITATIONS,
    SHORTLIST_VIEW_LIMITATION_CODES,
    ShortlistEvidence,
    ShortlistNotHeldError,
    ShortlistPanelUnreadableError,
    ShortlistRequestError,
    ShortlistRunBlockedError,
    ShortlistViewError,
    _resolve_instant,
    clipped_from_the_tie_at_the_top,
    shortlist_components,
    shortlist_evidence,
    shortlist_request,
)

AS_OF: Final[datetime] = datetime(2026, 1, 16, 12, 0, tzinfo=UTC)
FIRST: Final[datetime] = datetime(2026, 1, 16, 9, 0, tzinfo=UTC)
SECOND: Final[datetime] = datetime(2026, 1, 16, 13, 0, tzinfo=UTC)

BASELINE: Final[dict[str, object]] = {
    "components": (("reversal_1d/v1", 1.0),),
    "tier": "raw",
    "shortlist_size": 2,
    "position_capital": Decimal("1250"),
    "as_of": AS_OF,
    "years": (2026,),
    "exchange": "SSE",
    "horizon": "5d",
    "minimum_tradable_ratio": 0.0,
    "minimum_researched_ratio": 0.0,
    "maximum_ranking_age_days": 3_650,
    "code_commit": "abcdef1234567",
    "config_digest": "d" * 64,
}


def _request(**overrides: object) -> object:
    return shortlist_request(**{**BASELINE, **overrides})  # type: ignore[arg-type]


# --- the taxonomy, and the two tables it has to line up with ------------------------------------


def test_every_shortlist_view_fault_has_a_row_in_both_channel_tables() -> None:
    """Every `ShortlistViewError` subclass's `reason` is a key of both envelope tables.

    `_shortlist_refusal` looks its status up by `error.reason` and `_shortlist_fail` looks its exit
    code up the same way -- `_factor_refusal`'s rule, which is what keeps a fault from being
    enveloped as whichever branch an `isinstance` chain happened to end on. The price is that a
    subclass added with no row raises `KeyError` at that boundary; this is what makes the
    `KeyError` unreachable in practice, and it reads the live class hierarchy so a fifth subclass
    arrives red rather than unguarded. `not_held` is `V2-P4-062`'s, and it is `404`/exit `1` rather
    than a shade of `bad_request` because a well-formed address nothing is held under and a token
    that is not an address at all have different remedies.

    The base class's own `reason` deliberately has no row: it is never raised, and giving it one
    would invite a future subclass to inherit an envelope instead of choosing one.
    """
    subclasses = {subclass.reason for subclass in ShortlistViewError.__subclasses__()}

    assert subclasses == {"bad_request", "panel_unreadable", "blocked", "not_held"}
    assert subclasses <= set(SHORTLIST_HTTP_STATUS)
    assert subclasses <= set(SHORTLIST_EXIT)
    assert ShortlistRequestError.reason == "bad_request"
    assert ShortlistPanelUnreadableError.reason == "panel_unreadable"
    assert ShortlistRunBlockedError.reason == "blocked"
    assert ShortlistNotHeldError.reason == "not_held"
    assert ShortlistViewError.reason not in SHORTLIST_HTTP_STATUS
    assert ShortlistViewError.reason not in SHORTLIST_EXIT


def test_the_refused_row_is_the_one_no_fault_can_reach_and_it_is_not_a_success() -> None:
    """`refused` is a **verdict**, so it is in both tables and is nothing's `reason`.

    That is the whole shape of this issue in one assertion. A gate that refuses a list has not
    failed -- it has answered -- so no exception carries `refused`; and a face that reported it as
    a `2xx`/`0` would be the empty success `V2-P1-013` exists to make unavailable. Both tables put
    it beside `blocked` rather than beside `answered`.

    **These two tables are twins and not merely siblings**, which is the opposite of what
    `FACTOR_HTTP_STATUS` and `FACTOR_EXIT` are: those have two rows that do not correspond
    (`not_found`, because only HTTP has a route whose path names a resource). This face has one
    route, no path parameter and no document store, so every situation either channel can be in
    is one the other can be in too -- and the key-set equality is what would go red if a row were
    added to one table and forgotten in the other, which is exactly how a fault comes to be a
    `KeyError` on one channel and an envelope on the other.
    """
    reasons = {subclass.reason for subclass in ShortlistViewError.__subclasses__()}

    assert "refused" not in reasons
    assert SHORTLIST_HTTP_STATUS["refused"] == 409
    assert SHORTLIST_HTTP_STATUS["answered"] == 200
    assert SHORTLIST_EXIT["refused"] == PanelExit.unhealthy
    assert SHORTLIST_EXIT["answered"] == PanelExit.ok
    assert SHORTLIST_EXIT["bad_request"] == PanelExit.bad_request
    assert set(SHORTLIST_HTTP_STATUS) == set(SHORTLIST_EXIT)


def test_this_face_calls_the_same_panel_faults_unreadable_as_the_factor_face() -> None:
    """Which exceptions are facts about data rather than defects is one question with one answer.

    Pinned rather than left to agree by inspection: two faces that answered it differently would
    put the same broken partition under two different status codes on two channels, and nothing
    would say so.
    """
    assert set(SHORTLIST_PANEL_FAULTS) == set(FACTOR_PANEL_FAULTS)


def test_the_known_shortlist_view_limitations_are_the_six_this_face_declares() -> None:
    """Equality rather than membership: a membership assertion can see a code that was renamed and
    never one that was removed. `KNOWN_ADJUSTMENT_LIMITATIONS`' form since `V2-P1-005`.

    Four until `V2-P4-049` and `V2-P4-062` added one each: what a resolved `run_manifest_id` does
    and does not prove about the conclusion beside it, and what a content-addressed answer store
    can and cannot say about when an answer was reached.
    """
    assert {
        "the_clip_block_is_recovered_from_a_tie_and_may_over_report",
        "the_cross_section_may_be_older_than_the_as_of_that_was_asked_for",
        "the_evidence_plane_is_supplied_rather_than_run_by_this_module",
        "a_neutralized_tier_screen_needs_exposures_this_face_does_not_load",
        "a_resolved_run_manifest_is_not_a_resolved_signal",
        "the_stored_answer_is_addressed_by_content_and_not_by_when_it_was_run",
    } == SHORTLIST_VIEW_LIMITATION_CODES
    assert len(KNOWN_SHORTLIST_VIEW_LIMITATIONS) == 6
    assert all(limitation.detail.strip() for limitation in KNOWN_SHORTLIST_VIEW_LIMITATIONS)


# --- the request resolver, which touches no store ------------------------------------------------


def test_a_processed_tier_screen_with_no_transform_is_refused_by_name() -> None:
    """That partition holds every transform of the factor and is narrowed by the one you name, so
    a read without one is not a narrower question -- it is an unanswerable one."""
    with pytest.raises(ShortlistRequestError, match="needs a --transform"):
        _request(tier="processed")


def test_a_raw_tier_screen_that_names_a_transform_is_refused_rather_than_ignored() -> None:
    """The raw tier applies nothing, so a `--transform` beside it is a caller who believes their
    values were standardised. Silently dropping it would leave that belief intact."""
    with pytest.raises(ShortlistRequestError, match="applies nothing"):
        _request(transform="cross_section_standard/v1")


def test_a_naive_as_of_is_refused_because_a_guessed_zone_is_wrong_by_up_to_a_session() -> None:
    with pytest.raises(ShortlistRequestError, match="timezone-aware"):
        _request(as_of=datetime(2026, 1, 16, 12, 0))


def test_a_factor_no_registry_declares_names_the_ones_that_are_declared() -> None:
    """The actionable half of a mistyped key is the keys, not their content addresses."""
    with pytest.raises(ShortlistRequestError, match="reversal_1d/v1"):
        _request(components=(("no_such_factor/v1", 1.0),))


def test_a_screen_with_no_component_is_refused_rather_than_ordering_nothing() -> None:
    with pytest.raises(ShortlistRequestError, match="names no component"):
        _request(components=())


def test_a_raw_tier_screen_may_declare_exactly_one_component() -> None:
    """`ShortlistSpec`'s own rule, surfaced through this resolver rather than restated by it: raw
    values carry each factor's own units, so summing two adds quantities that share no scale."""
    with pytest.raises(ShortlistRequestError, match="share no scale"):
        _request(components=(("reversal_1d/v1", 1.0), ("return_on_equity_ttm/v1", 1.0)))


def test_a_calendar_horizon_is_a_bad_request_and_not_a_fact_about_the_panel() -> None:
    """`3m` is a legal `HORIZON_PATTERN` value that `SignalFrame` has not accepted since
    `V2-P4-001`, and it is refused **here** rather than by `build_ranking_manifest`.

    That placement is the assertion. `build_ranking_manifest` raises the same objection -- but it
    runs after the store has been read, so a mistyped flag would come back as
    `blocked`/exit 1/409, which tells a caller to rebuild a panel that is perfectly fine.
    Nothing in `shortlist_request` touches a store, so everything it refuses is `bad_request`.
    """
    with pytest.raises(ShortlistRequestError, match="not a horizon a SignalFrame can carry"):
        _request(horizon="3m")


def test_a_short_code_commit_is_refused_because_different_code_cuts_a_different_list() -> None:
    with pytest.raises(ShortlistRequestError, match="at least 7 characters"):
        _request(code_commit="abc")


def test_a_request_that_names_no_year_opens_no_partition_and_says_so() -> None:
    with pytest.raises(ShortlistRequestError, match="names no partition year"):
        _request(years=())


def test_a_resolved_request_carries_the_declaration_and_nothing_defaulted() -> None:
    resolved = _request()

    assert resolved.tier == "raw"  # type: ignore[attr-defined]
    assert resolved.transform is None  # type: ignore[attr-defined]
    assert resolved.neutralization is None  # type: ignore[attr-defined]
    assert resolved.spec.shortlist_size == 2  # type: ignore[attr-defined]
    assert resolved.spec.position_capital == Decimal("1250")  # type: ignore[attr-defined]
    assert resolved.gate.minimum_researched_ratio == 0.0  # type: ignore[attr-defined]
    assert resolved.evidence == {}  # type: ignore[attr-defined]
    assert [item.qualified_key for item in resolved.definitions] == [  # type: ignore[attr-defined]
        "reversal_1d/v1"
    ]


# --- the wire converters both faces share --------------------------------------------------------


def test_a_component_missing_either_half_is_refused_rather_than_defaulted() -> None:
    """`ScoreComponent.weight` has no default because it moves the answers."""
    with pytest.raises(ShortlistRequestError, match="names a `factor` and a `weight`"):
        shortlist_components([{"factor": "reversal_1d/v1"}])
    with pytest.raises(ShortlistRequestError, match="does not declare"):
        shortlist_components([{"factor": "reversal_1d/v1", "weight": 1.0, "direction": "up"}])


def test_a_signal_handed_back_with_its_own_signal_id_is_accepted_and_verified() -> None:
    """`SignalFrame` is `extra="forbid"` with a computed `signal_id`, so it rejects its own
    serialized form -- a caller who fetched a signal from this service and handed it back would be
    told `extra_forbidden` on a field this service put there. Stripped and then **verified**,
    which is `_parse_research_result`'s rule: an unverified address could describe another frame.
    """
    frame = {
        "schema_version": "signal-frame/v1",
        "subject": "000001.SZ",
        "as_of": AS_OF.isoformat(),
        "direction": "bullish",
        "strength": 0.4,
        "confidence": 0.7,
        "horizon": "5d",
        "evidence_ids": ["evd_000000000000000000000001"],
        "confirmation_conditions": [],
        "invalidation_conditions": [],
        "risk_flags": [],
        "abstention_reason": None,
    }
    joined = shortlist_evidence(
        {"000001.SZ": {"signal": frame, "run_manifest_id": "rmf_" + "0" * 24}}
    )
    identity = joined["000001.SZ"].signal.signal_id

    round_tripped = shortlist_evidence(
        {
            "000001.SZ": {
                "signal": {**frame, "signal_id": identity},
                "run_manifest_id": "rmf_" + "0" * 24,
            }
        }
    )
    assert round_tripped["000001.SZ"].signal.signal_id == identity

    with pytest.raises(ShortlistRequestError, match="does not describe the frame beside it"):
        shortlist_evidence(
            {
                "000001.SZ": {
                    "signal": {**frame, "signal_id": "sig_deadbeefdeadbeefdeadbeef"},
                    "run_manifest_id": "rmf_" + "0" * 24,
                }
            }
        )


def test_evidence_filed_under_one_subject_and_about_another_is_refused() -> None:
    """A conclusion keyed by one security and about another is a join nothing downstream undoes.

    `rank_candidates` matches by the mapping's key and `SignalFrame` carries its own `subject`, so
    a mismatched pair would put `600000.SH`'s research on `000001.SZ`'s rank with both records
    internally consistent. Refused in the resolver, which touches no store, so it is `bad_request`
    rather than a statement about the panel.
    """
    with pytest.raises(ShortlistRequestError, match="a signal about"):
        _request(
            evidence={
                "000001.SZ": ShortlistEvidence(
                    signal=SignalFrame(
                        subject="600000.SH",
                        as_of=AS_OF,
                        direction="bullish",
                        strength=0.4,
                        confidence=0.7,
                        horizon="5d",
                        evidence_ids=("evd_000000000000000000000001",),
                    ),
                    run_manifest_id="rmf_" + "0" * 24,
                )
            }
        )


def test_an_evidence_entry_with_no_run_manifest_id_is_refused() -> None:
    """A conclusion with no reproducible declaration behind it is what roadmap section 9 measured
    `RunManifest` to have been missing."""
    with pytest.raises(ShortlistRequestError, match="run_manifest_id"):
        shortlist_evidence({"000001.SZ": {"signal": {}}})


# --- one instant, agreed by every declared component ---------------------------------------------


def test_a_composite_whose_components_disagree_about_their_newest_instant_is_refused() -> None:
    """`factor_view`'s `the_three_tiers_must_have_been_built_at_the_same_instants`, applied across
    components: a composite summing one factor's Friday value against another's Monday value is
    one number over two markets, and the refusal names both instants so a caller can act on it.
    """
    request = _request(
        components=(("reversal_1d/v1", 1.0), ("return_on_equity_ttm/v1", 1.0)),
        tier="processed",
        transform="cross_section_standard/v1",
    )
    keys = {
        component.factor_id: component.definition.qualified_key
        for component in request.spec.components  # type: ignore[attr-defined]
    }
    first, second = sorted(keys)
    rows = {
        first: (("000001.SZ", 1.0, "processed", FIRST),),
        second: (("000001.SZ", 2.0, "processed", SECOND),),
    }

    with pytest.raises(ShortlistRunBlockedError) as refusal:
        _resolve_instant(rows, request)  # type: ignore[arg-type]

    assert "not one cross section" in str(refusal.value)
    assert FIRST.isoformat() in str(refusal.value)
    assert SECOND.isoformat() in str(refusal.value)


def test_components_that_agree_resolve_to_the_one_instant_they_share() -> None:
    """The positive half, without which the refusal above could be a function that always raises."""
    request = _request(
        components=(("reversal_1d/v1", 1.0), ("return_on_equity_ttm/v1", 1.0)),
        tier="processed",
        transform="cross_section_standard/v1",
    )
    keys = sorted(
        component.factor_id
        for component in request.spec.components  # type: ignore[attr-defined]
    )
    rows = {
        keys[0]: (("000001.SZ", 1.0, "processed", FIRST),),
        keys[1]: (("000001.SZ", 2.0, "processed", FIRST),),
    }

    assert _resolve_instant(rows, request) == FIRST  # type: ignore[arg-type]


def test_a_component_with_no_stored_cross_section_is_named_rather_than_scored_as_empty() -> None:
    """An empty cross section is not a market with nothing in it; it is a tier nobody built."""
    request = _request()
    factor_id = request.spec.components[0].factor_id  # type: ignore[attr-defined]

    with pytest.raises(ShortlistRunBlockedError) as refusal:
        _resolve_instant({factor_id: ()}, request)  # type: ignore[arg-type]

    assert "reversal_1d/v1" in str(refusal.value)
    assert "openalpha factor build" in str(refusal.value)


# --- the clip block, recovered from a tie ---------------------------------------------------------


def test_the_clip_block_is_the_admitted_names_sharing_the_largest_value() -> None:
    """`KNOWN_SHORTLIST_VIEW_LIMITATIONS`'
    `the_clip_block_is_recovered_from_a_tie_and_may_over_report`, driven at its own rule.

    Three names clipped to one bound share one stored value after any monotone standardization, so
    the block is the tie at the maximum. The name **below** the bound is not in it, which is what
    separates this from "every name that has a value".
    """
    rows = (
        ("000001.SZ", 2.5, "processed"),
        ("000002.SZ", 2.5, "processed"),
        ("600000.SH", 2.5, "processed"),
        ("600519.SH", 0.4, "processed"),
        ("300750.SZ", -1.9, "processed"),
    )

    assert clipped_from_the_tie_at_the_top(rows, tier="processed") == {
        "000001.SZ",
        "000002.SZ",
        "600000.SH",
    }


def test_an_imputed_value_is_not_read_as_a_clip_even_when_it_is_the_largest() -> None:
    """`TIER_ADMITTED_CODES` rather than "has a number", which is the one cell that differs across
    the three tiers: a processed row coded `imputed` carries a value and is **not** admitted, so a
    median standing in for a missing input must not be read as a winsorizer's bound."""
    rows = (
        ("000001.SZ", 9.9, "imputed"),
        ("000002.SZ", 2.5, "processed"),
        ("600000.SH", 2.5, "processed"),
    )

    assert clipped_from_the_tie_at_the_top(rows, tier="processed") == {
        "000002.SZ",
        "600000.SH",
    }


def test_a_cross_section_with_no_admitted_value_has_no_clip_block() -> None:
    """`frozenset()` rather than a refusal: nothing was clipped because nothing was measured, and
    `max()` over an empty sequence is where this used to raise."""
    rows = (
        ("000001.SZ", None, "source_not_computed"),
        ("000002.SZ", None, "insufficient_cross_section"),
    )

    assert clipped_from_the_tie_at_the_top(rows, tier="processed") == frozenset()
