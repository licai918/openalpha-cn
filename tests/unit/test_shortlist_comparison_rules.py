"""`V2-P4-007`'s comparison rules, on answers built to make one rule at a time observable.

The product path is `tests/integration/test_shortlist_comparison.py`, which drives
`openalpha shortlist compare` and `OpenAlphaSDK.compare_shortlists` over two real published
answers -- and it is the file that matters, because a green diff over two dictionaries proves
nothing about a feature nobody can reach. This file exists for the states that fixture cannot
produce cheaply: a refused answer beside an admitted one, an answer rendered by a shape this
build does not know, and each change code on its own.

The answers below carry exactly the keys `shortlist_compare` reads. That is a fixture and
therefore a thing that can drift from what `shortlist_view` really renders, so the binding
against the real renderer lives in the integration file --
`test_every_field_the_comparison_reports_is_the_one_the_answer_carried` reads each side of a
real comparison back against the real answer it came from.
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from typing import Any

import pytest

from openalpha_cn.shortlist_compare import (
    COMPARABLE_KEYS,
    COMPARISON_LIMITATION_CODES,
    KNOWN_COMPARISON_LIMITATIONS,
    REASON_CHANGES,
    SHORTLIST_COMPARISON_SCHEMA_VERSION,
    compare_shortlist_answers,
    shortlist_comparison_rows,
)
from openalpha_cn.shortlist_view import SHORTLIST_VIEW_SCHEMA_VERSION, ShortlistRequestError

DECLARATION: dict[str, Any] = {
    "tier": "raw",
    "transform": None,
    "neutralization": None,
    "exchange": "SSE",
    "years": [2026],
    "components": [{"factor_id": "fac_1", "factor": "reversal_1d/v1", "weight": 1.0}],
}


def _candidate(
    subject: str,
    *,
    rank: int,
    score: float,
    direction: str = "bullish",
    confidence: float = 0.7,
    risk_flags: tuple[str, ...] = (),
    run_manifest_id: str = "run_000000000000000000000001",
) -> dict[str, Any]:
    return {
        "subject": subject,
        "rank": rank,
        "score": score,
        "direction": direction,
        "confidence": confidence,
        "run_manifest_id": run_manifest_id,
        "risk_flags": list(risk_flags),
    }


def _answer(
    *,
    shortlist_id: str,
    ranked: tuple[dict[str, Any], ...],
    admitted: tuple[dict[str, Any], ...] | None,
    as_of: str = "2026-01-15T23:00:00+00:00",
    horizon: str = "5d",
    declaration: dict[str, Any] | None = None,
    schema_version: str = SHORTLIST_VIEW_SCHEMA_VERSION,
    blocks: tuple[str, ...] = (),
) -> dict[str, Any]:
    """One rendered answer, carrying every key `shortlist_compare` reads and nothing else."""
    return {
        "schema_version": schema_version,
        "shortlist_id": shortlist_id,
        "as_of": as_of,
        "horizon": horizon,
        "is_blocked": admitted is None,
        "declaration": DECLARATION if declaration is None else declaration,
        "cross_section": {"as_of": as_of},
        "blocks": [{"code": code} for code in blocks],
        "measurement": {
            "shortlist_count": len(ranked),
            "candidate_count": 0 if admitted is None else len(admitted),
        },
        "funnel": {
            "shortlist": [
                {"subject": item["subject"], "rank": item["rank"], "score": item["score"]}
                for item in ranked
            ]
        },
        "admitted": None if admitted is None else list(admitted),
    }


def _one(comparison: dict[str, Any], subject: str) -> dict[str, Any]:
    return next(entry for entry in comparison["entries"] if entry["subject"] == subject)


def test_the_registry_names_every_boundary_this_module_declares() -> None:
    """The set-literal binding every registry in this repository carries."""
    declared = {
        "the_store_cannot_say_which_answer_came_first",
        "a_rendered_answer_does_not_carry_the_cut_so_two_sizes_compare_as_churn",
        "two_answers_to_one_question_may_have_been_read_off_two_different_panels",
        "a_score_is_compared_by_equality_and_never_by_a_tolerance",
        "a_refused_answer_is_compared_rather_than_refused_and_the_block_is_reported",
        "an_added_or_removed_name_carries_no_change_codes_only_a_status",
        "a_comparison_is_derived_and_has_no_address_of_its_own",
    }

    assert declared == COMPARISON_LIMITATION_CODES
    assert len(KNOWN_COMPARISON_LIMITATIONS) == len(COMPARISON_LIMITATION_CODES)


def test_the_comparison_declares_a_version_of_its_own_and_not_the_answers() -> None:
    """Two shapes that can move independently, so neither constant may stand for the other."""
    comparison = compare_shortlist_answers(
        baseline=_answer(
            shortlist_id="sla_a", ranked=(_candidate("A", rank=1, score=1.0),), admitted=()
        ),
        current=_answer(
            shortlist_id="sla_b", ranked=(_candidate("A", rank=1, score=1.0),), admitted=()
        ),
    )

    assert comparison["schema_version"] == SHORTLIST_COMPARISON_SCHEMA_VERSION
    assert SHORTLIST_COMPARISON_SCHEMA_VERSION != SHORTLIST_VIEW_SCHEMA_VERSION


def test_a_name_published_on_one_side_only_is_a_changed_reason_and_not_a_removal() -> None:
    """`a_refused_answer_is_compared_rather_than_refused_and_the_block_is_reported`, driven.

    The whole point of deciding `status` on the screened list rather than on `admitted`: this
    name was cut on both days and published on one, which is the gate speaking and not the
    market. A comparison keyed on `admitted` would report it `removed`, and a reader would go
    looking for a name that never left.
    """
    ranked = (_candidate("A", rank=1, score=1.0),)

    comparison = compare_shortlist_answers(
        baseline=_answer(shortlist_id="sla_a", ranked=ranked, admitted=ranked),
        current=_answer(
            shortlist_id="sla_b",
            ranked=ranked,
            admitted=None,
            blocks=("researched_ratio_below_floor",),
        ),
    )

    entry = _one(comparison, "A")
    assert entry["status"] == "held"
    assert comparison["removed"] == []
    assert entry["changes"] == ["admission"]
    assert entry["baseline"]["admitted"] is True
    assert entry["current"]["admitted"] is False
    assert comparison["current"]["is_blocked"] is True
    assert comparison["current"]["blocks"] == ["researched_ratio_below_floor"]
    assert comparison["summary"]["reason_changed"] == 1


def test_each_reason_code_is_reachable_on_its_own_and_counts_towards_the_summary() -> None:
    """Every member of `REASON_CHANGES` except `admission`, one at a time.

    One at a time because a comparison that reported a single code for any difference at all
    would pass a test that changed three fields together, and because
    `test_the_registry_names_every_boundary_this_module_declares` cannot see whether a code the
    module can emit is ever emitted.
    """
    baseline_row = _candidate("A", rank=1, score=1.0)
    variants = {
        "direction": _candidate("A", rank=1, score=1.0, direction="bearish"),
        "risk_flags": _candidate("A", rank=1, score=1.0, risk_flags=("suspension",)),
        "backing_run": _candidate(
            "A", rank=1, score=1.0, run_manifest_id="run_000000000000000000000002"
        ),
    }

    for code, changed in variants.items():
        comparison = compare_shortlist_answers(
            baseline=_answer(
                shortlist_id="sla_a", ranked=(baseline_row,), admitted=(baseline_row,)
            ),
            current=_answer(shortlist_id="sla_b", ranked=(changed,), admitted=(changed,)),
        )

        assert _one(comparison, "A")["changes"] == [code], code
        assert comparison["summary"]["reason_changed"] == 1, code
        assert code in REASON_CHANGES


def test_confidence_and_score_are_reported_and_do_not_make_a_reason_changed() -> None:
    """`a_score_is_compared_by_equality_and_never_by_a_tolerance`, and its consequence.

    Both fields move, both are reported on the entry, both appear in `changes` -- and
    `reason_changed` stays at zero, because a continuous number needs a tolerance to be called
    changed and this build declares none. `rank_changed` is separately zero here, which is what
    separates "the ranking moved" from "the numbers under it drifted".
    """
    before = _candidate("A", rank=1, score=1.0, confidence=0.7)
    after = _candidate("A", rank=1, score=1.000_000_000_1, confidence=0.71)

    comparison = compare_shortlist_answers(
        baseline=_answer(shortlist_id="sla_a", ranked=(before,), admitted=(before,)),
        current=_answer(shortlist_id="sla_b", ranked=(after,), admitted=(after,)),
    )

    entry = _one(comparison, "A")
    assert entry["changes"] == ["confidence", "score"]
    assert entry["score_change"] == pytest.approx(1e-10)
    assert comparison["summary"]["reason_changed"] == 0
    assert comparison["summary"]["rank_changed"] == 0
    assert REASON_CHANGES & {"confidence", "score"} == set()


def test_an_added_or_removed_name_carries_a_status_and_no_change_codes() -> None:
    """`an_added_or_removed_name_carries_no_change_codes_only_a_status`, with its cost.

    The cost is the second half and it is the half worth holding: a day on which the whole list
    turned over reports `reason_changed: 0` beside a full `added` and `removed`, because there
    is no held name for a reason to have changed about.
    """
    comparison = compare_shortlist_answers(
        baseline=_answer(
            shortlist_id="sla_a",
            ranked=(_candidate("A", rank=1, score=1.0),),
            admitted=(_candidate("A", rank=1, score=1.0),),
        ),
        current=_answer(
            shortlist_id="sla_b",
            ranked=(_candidate("B", rank=1, score=2.0),),
            admitted=(_candidate("B", rank=1, score=2.0, direction="bearish"),),
        ),
    )

    assert (comparison["added"], comparison["removed"]) == (["B"], ["A"])
    assert _one(comparison, "A")["changes"] == []
    assert _one(comparison, "B")["changes"] == []
    assert _one(comparison, "A")["rank_change"] is None
    assert _one(comparison, "B")["score_change"] is None
    assert comparison["summary"]["reason_changed"] == 0
    assert comparison["summary"]["held"] == 0


def test_an_answer_rendered_by_a_shape_this_build_does_not_know_is_refused() -> None:
    """Both sides, and before the two are compared with each other.

    Two answers from a *newer* build agree with one another, so a rule that only compared the
    two `schema_version`s against each other would walk a body whose field names this module
    invented -- and every `KeyError` that followed would name a key rather than the shape.
    """
    good = _answer(shortlist_id="sla_a", ranked=(_candidate("A", rank=1, score=1.0),), admitted=())
    future = _answer(
        shortlist_id="sla_b",
        ranked=(_candidate("A", rank=1, score=1.0),),
        admitted=(),
        schema_version="shortlist-view/v2",
    )

    with pytest.raises(ShortlistRequestError, match=r"^the baseline answer is"):
        compare_shortlist_answers(baseline=future, current=good)
    with pytest.raises(ShortlistRequestError, match=r"^the current answer is"):
        compare_shortlist_answers(baseline=good, current=future)
    with pytest.raises(ShortlistRequestError, match="shortlist-view/v2"):
        compare_shortlist_answers(baseline=future, current=future)


def test_the_refusal_names_the_declaration_key_that_differs_and_not_the_object() -> None:
    """ "`declaration` differs" sends a reader to read two objects; the key sends them to one."""
    ranked = (_candidate("A", rank=1, score=1.0),)
    baseline = _answer(shortlist_id="sla_a", ranked=ranked, admitted=())
    current = _answer(
        shortlist_id="sla_b",
        ranked=ranked,
        admitted=(),
        declaration={**DECLARATION, "exchange": "SZSE", "years": [2025]},
    )

    with pytest.raises(ShortlistRequestError) as raised:
        compare_shortlist_answers(baseline=baseline, current=current)

    message = str(raised.value)
    assert "exchange" in message
    assert "years" in message
    assert "declaration" not in message
    assert "tier" not in message


def test_a_declaration_key_present_on_only_one_side_is_named_too() -> None:
    """The union in `_refuse_two_questions`, which an intersection would pass (sweep).

    Every other fixture here gives both sides the same key set and differs only in values, so
    `set(a) | set(b)` and `set(a) & set(b)` agree and a sweep flipping one for the other killed
    nothing. A key that exists on one side only is exactly the shape a *new* `declaration` field
    arrives in -- one build renders it, an older stored answer does not -- and reporting "these
    are the same question" about that pair is the failure this direction prevents.
    """
    ranked = (_candidate("A", rank=1, score=1.0),)
    older = {key: value for key, value in DECLARATION.items() if key != "neutralization"}

    with pytest.raises(ShortlistRequestError, match="neutralization"):
        compare_shortlist_answers(
            baseline=_answer(shortlist_id="sla_a", ranked=ranked, admitted=(), declaration=older),
            current=_answer(shortlist_id="sla_b", ranked=ranked, admitted=()),
        )


def test_two_answers_at_two_instants_to_one_question_are_compared_rather_than_refused() -> None:
    """`as_of` is the thing that moves, so it is deliberately not in `COMPARABLE_KEYS`.

    And the reverse: two runs at *one* instant are compared too, which is the case
    `two_answers_to_one_question_may_have_been_read_off_two_different_panels` is about --
    requiring the two instants to differ would refuse a real comparison of one question against
    a rebuilt panel.
    """
    ranked = (_candidate("A", rank=1, score=1.0),)
    same_instant = compare_shortlist_answers(
        baseline=_answer(shortlist_id="sla_a", ranked=ranked, admitted=()),
        current=_answer(shortlist_id="sla_b", ranked=ranked, admitted=()),
    )
    two_instants = compare_shortlist_answers(
        baseline=_answer(shortlist_id="sla_a", ranked=ranked, admitted=()),
        current=_answer(
            shortlist_id="sla_b",
            ranked=ranked,
            admitted=(),
            as_of="2026-01-16T12:00:00+00:00",
        ),
    )

    assert same_instant["held"] == two_instants["held"] == ["A"]
    assert two_instants["current"]["as_of"] == "2026-01-16T12:00:00+00:00"
    assert two_instants["current"]["cross_section_as_of"] == "2026-01-16T12:00:00+00:00"


def test_the_terminal_rows_read_the_same_way_on_every_status() -> None:
    """One `rank` column meaning one thing, whether the name entered, left or stayed."""
    comparison = compare_shortlist_answers(
        baseline=_answer(
            shortlist_id="sla_a",
            ranked=(
                _candidate("A", rank=1, score=1.0),
                _candidate("B", rank=2, score=0.5),
            ),
            admitted=(),
        ),
        current=_answer(
            shortlist_id="sla_b",
            ranked=(
                _candidate("A", rank=2, score=0.9),
                _candidate("C", rank=1, score=1.4),
            ),
            admitted=(),
        ),
    )

    assert shortlist_comparison_rows(comparison) == (
        ("held", "A", "1 -> 2 (-1)", "rank, score"),
        ("removed", "B", "2", "-"),
        ("added", "C", "1", "-"),
    )


def test_a_name_that_moved_up_carries_a_positive_rank_change() -> None:
    """The sign convention, on the direction a reader is most likely to assume backwards."""
    comparison = compare_shortlist_answers(
        baseline=_answer(
            shortlist_id="sla_a", ranked=(_candidate("A", rank=5, score=0.1),), admitted=()
        ),
        current=_answer(
            shortlist_id="sla_b", ranked=(_candidate("A", rank=2, score=0.4),), admitted=()
        ),
    )

    entry = _one(comparison, "A")
    assert entry["rank_change"] == 3
    assert entry["score_change"] == pytest.approx(0.3)
    assert comparison["summary"]["rank_changed"] == 1


def test_the_comparison_schema_version_is_the_literal_and_not_whatever_the_module_says() -> None:
    """The tautology a sweep found, replaced by an assertion that can fail.

    `test_the_comparison_declares_a_version_of_its_own_and_not_the_answers` compares the rendered
    body against `SHORTLIST_COMPARISON_SCHEMA_VERSION`, so mutating that constant moved both
    sides of the equality and the sweep walked straight past it. A version string is a wire
    contract three faces read; it is written out here so changing it is a diff.
    """
    assert SHORTLIST_COMPARISON_SCHEMA_VERSION == "shortlist-comparison/v1"


def test_a_declared_comparison_limitation_cannot_be_edited_after_it_is_declared() -> None:
    """`KNOWN_ROUTING_LIMITATIONS`' assertion, on the sibling registry and for its reason."""
    entry = KNOWN_COMPARISON_LIMITATIONS[0]

    with pytest.raises(FrozenInstanceError):
        entry.detail = "something else"  # type: ignore[misc]


def test_the_comparable_keys_are_the_two_a_shape_check_does_not_already_cover() -> None:
    """`schema_version` is checked separately and is deliberately not here (sweep).

    Written out because the removal is the interesting half: a third member restored here would
    be a second check of something `_refuse_two_questions` already refuses by name, and a sweep
    is what proved the old third member could not fire.
    """
    assert COMPARABLE_KEYS == ("horizon", "declaration")
