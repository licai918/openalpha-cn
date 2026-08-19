"""`V2-P4-006`: a screen that reads *which* risk flags a candidate carries, not how many.

The acceptance measurement is `test_a_high_confidence_signal_carrying_a_severe_flag_does_not
_rank_first`, and it failed against the implementation this replaces with
`['000001.SZ', '600000.SH'] != ['600000.SH', '000001.SZ']` -- the old key was
`(-confidence, -strength, subject)`, so 0.95 with `future_data` outsorted 0.60 with nothing.

Everything below the ordering tests is about the *source* of severity.
`src/openalpha_cn/product/governance.py` deliberately holds no risk-flag strings: it asks the
two gates this build already ships (`decisions/risk.py::RiskGate` and
`agents/committee.py::DeliberationCommittee`) what each flag is worth. Three tests hold that
arrangement up -- `test_no_shipped_risk_flag_is_written_in_executable_code_under_product` (no
fourth list exists), `test_both_shipped_gates_answer_about_the_flags_and_about_nothing_else_on
_the_signal` (the one-flag probe measures the flag), and
`test_the_two_shipped_vocabularies_are_still_disjoint_and_are_read_rather_than_restated` (the
fracture `V2-P4-005` recorded is still real, and is re-measured off the classes).

The committee's three severe strings are the only flag literals in this file that are not read
off a shipped object, because they live inside `DeliberationCommittee.review`'s body rather
than in any attribute. They are not trusted as literals: every one is asserted to be `severe`
*through* `flag_severity`, so renaming one upstream turns this file red rather than stale.
"""

from __future__ import annotations

import ast
from datetime import UTC, datetime
from pathlib import Path
from typing import Final, Literal

import pytest
from pydantic import ValidationError

from openalpha_cn.agents.committee import DeliberationCommittee
from openalpha_cn.decisions.risk import RiskGate
from openalpha_cn.domain.decision import DecisionLedger
from openalpha_cn.domain.run import RunManifest
from openalpha_cn.domain.signal import SignalFrame
from openalpha_cn.product import governance, reporting, research, screening, watchlist
from openalpha_cn.product.governance import (
    SEVERITY_ORDER,
    SEVERITY_RANK,
    SHIPPED_RISK_GATES,
    GovernanceSeverity,
    assess,
    flag_severity,
)
from openalpha_cn.product.screening import (
    EXCLUSION_PRECEDENCE,
    KNOWN_SCREENING_LIMITATIONS,
    PER_RESULT_EXCLUSION_REASONS,
    SCREENING_LIMITATION_CODES,
    ResearchScreener,
    ScreeningCriteria,
)
from openalpha_cn.runtime.contracts import ResearchRunResult

ROOT: Final[Path] = Path(__file__).resolve().parents[3]
PRODUCT_ROOT: Final[Path] = ROOT / "src" / "openalpha_cn" / "product"

AS_OF: Final[datetime] = datetime(2024, 3, 1, 9, 30, tzinfo=UTC)
COMMIT: Final[str] = "0123456789abcdef"
CONFIG_DIGEST: Final[str] = "b" * 64
EVIDENCE: Final[tuple[str, ...]] = ("evd_000000000000000000000001",)

COMMITTEE_SEVERE_FLAGS: Final[tuple[str, ...]] = ("regulatory", "data-quality", "suspension")
"""The set literal inside `DeliberationCommittee.review`, which is not readable as an attribute.

Spelled here and then *checked* rather than trusted: `test_the_two_shipped_vocabularies_are
_still_disjoint_and_are_read_rather_than_restated` requires `flag_severity` to answer `severe`
for each, and `severe` is only reachable when `RiskGate` passes and the committee blocks. A
rename upstream therefore fails this file instead of silently making it assert nothing.
"""

COMMITTEE_DISAGREEMENT: Final[str] = "committee-disagreement"
"""The flag the committee raises about its own deliberation, which neither gate reads."""


def make_result(
    *,
    subject: str,
    confidence: float,
    risk_flags: tuple[str, ...] = (),
    strength: float = 0.4,
    direction: Literal["bullish", "bearish", "neutral"] = "bullish",
    final_action: Literal["watch", "avoid", "abstain"] = "watch",
) -> ResearchRunResult:
    """One `ResearchRunResult` whose only interesting axes are confidence and risk flags."""
    signal = SignalFrame(
        subject=subject,
        as_of=AS_OF,
        direction=direction,
        strength=strength,
        confidence=confidence,
        horizon="5d",
        evidence_ids=EVIDENCE,
        risk_flags=risk_flags,
    )
    manifest = RunManifest(
        run_id=f"run-{subject}",
        mode="replay",
        as_of=AS_OF,
        code_commit=COMMIT,
        config_digest=CONFIG_DIGEST,
        random_seed=7,
        started_at=AS_OF,
        finished_at=AS_OF,
        status="succeeded",
    )
    decision = DecisionLedger(
        run_id=manifest.run_id,
        run_manifest_id=manifest.run_manifest_id,
        created_at=AS_OF,
        risk_decision="pass",
        final_action=final_action,
        evidence_ids=EVIDENCE,
        signal_ids=(signal.signal_id,),
        code_commit=COMMIT,
    )
    return ResearchRunResult(signal=signal, decision=decision, manifest=manifest, agent_results=())


def gate_flags() -> frozenset[str]:
    """Every flag `RiskGate` names, read off the class rather than restated."""
    return RiskGate._blocking_flags | RiskGate._reducing_flags


def one_flag_per_rung() -> dict[GovernanceSeverity, str]:
    """One real flag for each rung above `clear`, all three sources read the same way."""
    blocking = sorted(RiskGate._blocking_flags)[0]
    reducing = sorted(RiskGate._reducing_flags)[0]
    return {
        "blocked": blocking,
        "severe": COMMITTEE_SEVERE_FLAGS[0],
        "reduced": reducing,
        "unrecognised": COMMITTEE_DISAGREEMENT,
    }


# --- the acceptance criterion -----------------------------------------------------------


def test_a_high_confidence_signal_carrying_a_severe_flag_does_not_rank_first() -> None:
    """The acceptance criterion, stated as an ordering rather than as a filter.

    `future_data` is the flag `decisions/risk.py::RiskGate` blocks on. A screen that orders by
    confidence puts the flagged name first at 0.95 against 0.60; a governed screen must not.
    """
    flagged = make_result(subject="000001.SZ", confidence=0.95, risk_flags=("future_data",))
    clean = make_result(subject="600000.SH", confidence=0.60)

    screened = ResearchScreener().screen(results=(flagged, clean), criteria=ScreeningCriteria())

    assert [item.subject for item in screened.items] == ["600000.SH", "000001.SZ"]
    assert screened.items[0].severity == "clear"
    assert screened.items[1].severity == "blocked"
    assert screened.items[1].driving_flags == ("future_data",)


def test_a_flag_count_cannot_separate_the_severe_from_the_benign_and_a_severity_can() -> None:
    """Why `max_risk_flags` is kept and is not the governance reading.

    Two candidates, one flag each, identical confidence: the count is 1 for both, so no
    `max_risk_flags` admits one and rejects the other. The severity does, by name.
    """
    severe = make_result(subject="000001.SZ", confidence=0.8, risk_flags=("future_data",))
    benign = make_result(subject="600000.SH", confidence=0.8, risk_flags=("cosmetic-note",))

    by_count = ResearchScreener().screen(
        results=(severe, benign), criteria=ScreeningCriteria(max_risk_flags=1)
    )
    assert {item.subject for item in by_count.items} == {"000001.SZ", "600000.SH"}
    assert by_count.excluded == ()

    at_zero = ResearchScreener().screen(
        results=(severe, benign), criteria=ScreeningCriteria(max_risk_flags=0)
    )
    assert at_zero.items == ()
    assert {entry.reason for entry in at_zero.excluded} == {"over_max_risk_flags"}

    by_severity = ResearchScreener().screen(
        results=(severe, benign),
        criteria=ScreeningCriteria(worst_severity_admitted="unrecognised"),
    )
    assert [item.subject for item in by_severity.items] == ["600000.SH"]
    assert [entry.subject for entry in by_severity.excluded] == ["000001.SZ"]
    assert by_severity.excluded[0].reason == "worse_than_admitted_severity"
    assert by_severity.excluded[0].driving_flags == ("future_data",)


def test_a_disputed_signal_no_longer_sorts_as_though_it_were_clean() -> None:
    """The fracture `V2-P4-005` recorded, closed on the screening plane.

    `committee-disagreement` is in neither gate's closed subset, so `RiskGate` returns `pass`
    for it -- which is asserted here rather than assumed. Before this issue that made a
    disputed signal indistinguishable from a clean one to the screen; now it sits one rung
    down and a clean name at *lower* confidence outranks it.
    """
    disputed_signal = SignalFrame(
        subject="000001.SZ",
        as_of=AS_OF,
        direction="bullish",
        strength=0.4,
        confidence=0.9,
        horizon="5d",
        evidence_ids=EVIDENCE,
        risk_flags=(COMMITTEE_DISAGREEMENT,),
    )
    assert RiskGate().evaluate(disputed_signal) == "pass"
    assert flag_severity(COMMITTEE_DISAGREEMENT) == "unrecognised"

    disputed = make_result(
        subject="000001.SZ", confidence=0.9, risk_flags=(COMMITTEE_DISAGREEMENT,)
    )
    clean = make_result(subject="600000.SH", confidence=0.5)

    screened = ResearchScreener().screen(results=(disputed, clean), criteria=ScreeningCriteria())

    assert [item.subject for item in screened.items] == ["600000.SH", "000001.SZ"]
    assert screened.items[1].severity == "unrecognised"
    assert screened.items[1].driving_flags == (COMMITTEE_DISAGREEMENT,)
    assert screened.items[1].gate_decision == "pass"
    assert screened.items[1].committee_decision == "reduce"


def test_confidence_still_orders_within_one_severity_rung() -> None:
    """Governance is the *first* key and not the only one: the old key survives beneath it.

    Four names on two rungs. If severity were the whole key the two pairs would be in input
    order; if confidence were, the rungs would interleave. Neither happens.
    """
    results = (
        make_result(subject="AAA", confidence=0.10),
        make_result(subject="BBB", confidence=0.99, risk_flags=("future_data",)),
        make_result(subject="CCC", confidence=0.90),
        make_result(subject="DDD", confidence=0.20, risk_flags=("future_data",)),
    )

    screened = ResearchScreener().screen(results=results, criteria=ScreeningCriteria())

    assert [item.subject for item in screened.items] == ["CCC", "AAA", "BBB", "DDD"]
    assert [item.severity for item in screened.items] == [
        "clear",
        "clear",
        "blocked",
        "blocked",
    ]


def test_equal_confidence_within_a_rung_still_breaks_on_strength_then_subject() -> None:
    """The two lower keys, each separated alone, so neither is dead weight in the tuple."""
    results = (
        make_result(subject="ZZZ", confidence=0.5, strength=0.9),
        make_result(subject="AAA", confidence=0.5, strength=0.1),
        make_result(subject="MMM", confidence=0.5, strength=0.9),
    )

    screened = ResearchScreener().screen(results=results, criteria=ScreeningCriteria())

    assert [item.subject for item in screened.items] == ["MMM", "ZZZ", "AAA"]


# --- where severity comes from ----------------------------------------------------------


def test_the_two_shipped_vocabularies_are_still_disjoint_and_are_read_rather_than_restated() -> (
    None
):
    """The premise of the whole design, re-measured at this commit off the real classes.

    `V2-P4-005` measured `RiskGate`'s five flags and the committee's three to be disjoint, with
    `committee-disagreement` in neither. If either gate is ever widened to read the other's
    words, this goes red -- which is the point: the design below assumes the two disagree, and
    a design whose premise silently stopped holding is worse than one that never had it.
    """
    assert gate_flags() == {
        "future_data",
        "look_ahead_violation",
        "redistribution_unknown",
        "source_uri_missing",
        "revised_after_initial_availability",
    }

    for flag in RiskGate._blocking_flags:
        assert flag_severity(flag) == "blocked"
    for flag in RiskGate._reducing_flags:
        assert flag_severity(flag) == "reduced"

    for flag in COMMITTEE_SEVERE_FLAGS:
        assert flag_severity(flag) == "severe", (
            f"{flag} is no longer the committee's alone; either RiskGate now reads it or the "
            "committee stopped -- reread agents/committee.py before touching this file"
        )
        assert flag not in gate_flags()

    assert flag_severity(COMMITTEE_DISAGREEMENT) == "unrecognised"
    assert COMMITTEE_DISAGREEMENT not in gate_flags()


def test_no_shipped_risk_flag_is_written_in_executable_code_under_product() -> None:
    """There is no fourth list, and this is how that is known rather than asserted.

    Every flag string any shipped gate reads, checked against every string constant under
    `src/openalpha_cn/product/` that is **not** a docstring. Prose may name a flag -- both new
    modules do, at length -- and code may not, because a literal in code is a copy and a copy
    is the thing that drifts. The docstring/executable split is
    `tests/unit/test_known_limitation_registries.py`'s, pointed at source instead of tests.
    """
    shipped = gate_flags() | set(COMMITTEE_SEVERE_FLAGS) | {COMMITTEE_DISAGREEMENT}
    assert len(shipped) == 9

    offenders: dict[str, set[str]] = {}
    for path in sorted(PRODUCT_ROOT.rglob("*.py")):
        found = executable_string_constants(path) & shipped
        if found:
            offenders[path.name] = found

    assert offenders == {}, (
        "a risk-flag string in executable position under product/ is a fourth copy of a "
        "vocabulary that already disagrees with itself three ways; ask SHIPPED_RISK_GATES "
        "instead"
    )


def test_both_shipped_gates_answer_about_the_flags_and_about_nothing_else_on_the_signal() -> None:
    """What makes a synthetic one-flag probe a measurement of the flag rather than of the probe.

    `governance._probe` builds a `SignalFrame` that carries the flags and is otherwise
    arbitrary, so the whole design rests on neither gate reading any other field. Nine signals
    that agree on nothing but `risk_flags` -- different subject, instant, direction, strength,
    confidence, horizon, evidence and conditions -- must produce one verdict from each gate and
    one severity from `assess`.

    The committee is driven on the seven directional ones only, because it cannot be driven on
    the other two at all; that is not a gap in this measurement but a separate defect, and
    `test_the_shipped_committee_cannot_be_asked_about_an_abstaining_signal_at_all` is where it
    is measured.
    """
    for flags in ((), ("future_data",), COMMITTEE_SEVERE_FLAGS[:1], (COMMITTEE_DISAGREEMENT,)):
        signals = signals_agreeing_only_on(flags)
        directional = tuple(item for item in signals if item.direction != "abstain")
        assert len(signals) == 9
        assert len(directional) == 7

        gate_answers = {SHIPPED_RISK_GATES["runtime-risk-gate"](item) for item in signals}
        committee_answers = {
            SHIPPED_RISK_GATES["deliberation-committee"](item) for item in directional
        }
        severities = {assess(item).severity for item in signals}

        assert len(gate_answers) == 1, f"RiskGate moved on a non-flag field for {flags!r}"
        assert len(committee_answers) == 1, f"the committee moved on a non-flag field: {flags!r}"
        assert len(severities) == 1, f"assess moved on a non-flag field for {flags!r}"
        expected = "clear" if not flags else flag_severity(flags[0])
        assert severities == {expected}


def test_the_shipped_committee_cannot_be_asked_about_an_abstaining_signal_at_all() -> None:
    """A pre-existing defect in `agents/committee.py`, measured here because this issue routes
    around it rather than editing a file it does not own.

    `DeliberationCommittee.review` recomputes `direction` from `adjusted_strength` and can
    never reproduce `abstain`, so on an abstaining signal -- which by
    `SignalFrame.validate_conclusion` carries no `evidence_ids` -- it dies validating its own
    `DeliberationOutcome`. `POST /api/v1/research/deliberate` and `OpenAlphaSDK.deliberate`
    both hand a caller-supplied signal straight in, so this reaches a shipped face; PRD S42
    makes explicit abstention a guarantee, and `ScreeningCriteria.directions` lists `abstain`
    as something a caller may screen for.

    `governance.assess` therefore asks both gates about a canonical carrier of the signal's
    flags instead of about the signal. This test pins the refusal on the real class, so the day
    somebody repairs `review` it goes red and the workaround gets re-read rather than kept out
    of habit -- and the second half proves the screen survives the defect today.
    """
    abstaining = SignalFrame(
        subject="000001.SZ",
        as_of=AS_OF,
        direction="abstain",
        strength=0.0,
        confidence=0.3,
        horizon="5d",
        abstention_reason="evidence insufficient",
    )
    with pytest.raises(ValidationError, match="directional signal requires evidence"):
        DeliberationCommittee().review(signal=abstaining, results=())

    assert assess(abstaining).severity == "clear"
    flagged = abstaining.model_copy(update={"risk_flags": ("future_data",)})
    verdict = assess(flagged)
    assert verdict.severity == "blocked"
    assert verdict.driving_flags == ("future_data",)


def test_a_signals_severity_is_the_worst_of_its_flags_taken_one_at_a_time() -> None:
    """`assess` calls each gate once; the rung it derives must equal the per-flag maximum.

    Both gates are set intersections over the whole tuple, so the two readings agree today.
    The assertion is what would notice if one stopped being one.
    """
    rungs = one_flag_per_rung()
    mixed = (rungs["severe"], rungs["blocked"], rungs["reduced"], rungs["unrecognised"])

    verdict = assess(
        SignalFrame(
            subject="000001.SZ",
            as_of=AS_OF,
            direction="bullish",
            strength=0.4,
            confidence=0.7,
            horizon="5d",
            evidence_ids=EVIDENCE,
            risk_flags=mixed,
        )
    )

    worst = max(SEVERITY_RANK[flag_severity(flag)] for flag in mixed)
    assert SEVERITY_RANK[verdict.severity] == worst
    assert verdict.severity == "blocked"
    assert verdict.driving_flags == (rungs["blocked"],)
    assert verdict.gate_decision == "block"
    assert verdict.committee_decision == "block"


def test_driving_flags_names_every_flag_at_the_worst_rung_and_only_those() -> None:
    """Two flags on the same worst rung are both named; a lesser one beside them is not."""
    both_blocking = tuple(sorted(RiskGate._blocking_flags))
    reducing = sorted(RiskGate._reducing_flags)[0]

    verdict = assess(
        SignalFrame(
            subject="000001.SZ",
            as_of=AS_OF,
            direction="bullish",
            strength=0.4,
            confidence=0.7,
            horizon="5d",
            evidence_ids=EVIDENCE,
            risk_flags=(both_blocking[0], reducing, both_blocking[1]),
        )
    )

    assert verdict.severity == "blocked"
    assert verdict.driving_flags == (both_blocking[0], both_blocking[1])
    assert reducing not in verdict.driving_flags


def test_any_flag_at_all_lifts_a_signal_off_the_clear_rung() -> None:
    """`clear` means no flags, and nothing else -- so an unknown word is not silently clean."""
    clean = assess(unflagged_signal(()))
    assert clean.severity == "clear"
    assert clean.driving_flags == ()

    unknown = assess(unflagged_signal(("a-word-no-gate-has-ever-heard-of",)))
    assert unknown.severity == "unrecognised"
    assert unknown.driving_flags == ("a-word-no-gate-has-ever-heard-of",)
    assert SEVERITY_RANK[unknown.severity] > SEVERITY_RANK["clear"]


def test_the_ladder_is_declared_once_and_every_rung_is_reachable() -> None:
    """The vocabulary, the order and the ranking are one declaration, and none of it is dead."""
    assert SEVERITY_ORDER == ("clear", "unrecognised", "reduced", "severe", "blocked")
    assert {name: index for index, name in enumerate(SEVERITY_ORDER)} == SEVERITY_RANK
    assert set(SHIPPED_RISK_GATES) == {"runtime-risk-gate", "deliberation-committee"}
    assert flag_severity.cache_info().maxsize is not None, (
        "flag strings come from request bodies; an unbounded memo over them is a leak "
        "whose size a caller chooses"
    )

    reached = {"clear": flag_severity_of_no_flags()} | {
        rung: flag_severity(flag) for rung, flag in one_flag_per_rung().items()
    }
    assert reached == {rung: rung for rung in SEVERITY_ORDER}


# --- the screen's own bookkeeping -------------------------------------------------------


def test_every_reviewed_result_is_either_an_item_or_an_exclusion_and_never_both() -> None:
    """`reviewed` is an exact partition, so a caller can reconcile a screen without guessing."""
    results = (
        *(
            make_result(subject=f"{index:06d}.SZ", confidence=0.5 + index / 100)
            for index in range(6)
        ),
        make_result(subject="900001.SZ", confidence=0.05),
        make_result(subject="900002.SZ", confidence=0.9, direction="neutral"),
    )

    screened = ResearchScreener().screen(
        results=results,
        criteria=ScreeningCriteria(min_confidence=0.1, directions=("bullish",), limit=4),
    )

    assert screened.reviewed == len(results) == 8
    assert len(screened.items) == 4
    assert len(screened.excluded) == 4
    assert screened.reviewed == len(screened.items) + len(screened.excluded)
    subjects = [item.subject for item in screened.items]
    excluded = [entry.subject for entry in screened.excluded]
    assert set(subjects) & set(excluded) == set()
    assert sorted(subjects + excluded) == sorted(result.signal.subject for result in results)


def test_the_beyond_limit_exclusions_are_the_ranked_tail_and_not_an_arbitrary_pair() -> None:
    """The limit cuts from the bottom of the governed order, which is the only cut that means
    anything once the order changed."""
    results = (
        make_result(subject="AAA", confidence=0.99, risk_flags=("future_data",)),
        make_result(subject="BBB", confidence=0.10),
        make_result(subject="CCC", confidence=0.20),
    )

    screened = ResearchScreener().screen(results=results, criteria=ScreeningCriteria(limit=2))

    assert [item.subject for item in screened.items] == ["CCC", "BBB"]
    assert [entry.subject for entry in screened.excluded] == ["AAA"]
    assert screened.excluded[0].reason == "beyond_limit"
    assert screened.excluded[0].severity == "blocked"


def test_each_per_result_exclusion_reason_is_reachable_on_its_own() -> None:
    """Every member of the closed reason set is provoked alone, so none is a dead branch."""
    provocations: dict[str, tuple[ResearchRunResult, ScreeningCriteria]] = {
        "below_min_confidence": (
            make_result(subject="AAA", confidence=0.1),
            ScreeningCriteria(min_confidence=0.5),
        ),
        "direction_not_requested": (
            make_result(subject="AAA", confidence=0.9, direction="neutral"),
            ScreeningCriteria(directions=("bullish",)),
        ),
        "final_action_not_requested": (
            make_result(subject="AAA", confidence=0.9, final_action="avoid"),
            ScreeningCriteria(final_actions=("watch",)),
        ),
        "over_max_risk_flags": (
            make_result(subject="AAA", confidence=0.9, risk_flags=("a", "b")),
            ScreeningCriteria(max_risk_flags=1),
        ),
        "worse_than_admitted_severity": (
            make_result(subject="AAA", confidence=0.9, risk_flags=("future_data",)),
            ScreeningCriteria(worst_severity_admitted="clear"),
        ),
    }
    assert tuple(provocations) == PER_RESULT_EXCLUSION_REASONS

    for reason, (result, criteria) in provocations.items():
        screened = ResearchScreener().screen(results=(result,), criteria=criteria)
        assert screened.items == (), reason
        assert [entry.reason for entry in screened.excluded] == [reason]


def test_a_result_failing_two_criteria_is_reported_under_the_declared_precedence() -> None:
    """The precedence is `EXCLUSION_PRECEDENCE` and not the order somebody wrote the checks in.

    One result failing every per-result criterion at once, then relaxed one criterion at a
    time: the reported reason must walk the declared tuple in order.
    """
    result = make_result(
        subject="AAA", confidence=0.05, direction="neutral", risk_flags=("future_data", "x")
    )
    relaxations: dict[str, ScreeningCriteria] = {
        "below_min_confidence": ScreeningCriteria(
            min_confidence=0.5,
            directions=("bullish",),
            final_actions=("avoid",),
            max_risk_flags=1,
            worst_severity_admitted="clear",
        ),
        "direction_not_requested": ScreeningCriteria(
            directions=("bullish",),
            final_actions=("avoid",),
            max_risk_flags=1,
            worst_severity_admitted="clear",
        ),
        "final_action_not_requested": ScreeningCriteria(
            final_actions=("avoid",), max_risk_flags=1, worst_severity_admitted="clear"
        ),
        "over_max_risk_flags": ScreeningCriteria(max_risk_flags=1, worst_severity_admitted="clear"),
        "worse_than_admitted_severity": ScreeningCriteria(worst_severity_admitted="clear"),
    }
    assert tuple(relaxations) == EXCLUSION_PRECEDENCE[:-1]

    for expected, criteria in relaxations.items():
        screened = ResearchScreener().screen(results=(result,), criteria=criteria)
        assert [entry.reason for entry in screened.excluded] == [expected]


def test_the_default_criteria_cut_nothing_and_only_reorder() -> None:
    """`V2-P4-006` is an ordering change: the shipped default must reject no name it used to
    accept, or `POST /api/v1/screen` would mean something new with no schema saying so."""
    results = tuple(
        make_result(subject=f"{index:06d}.SZ", confidence=0.5, risk_flags=flags)
        for index, flags in enumerate(
            ((), ("future_data",), (COMMITTEE_DISAGREEMENT,), COMMITTEE_SEVERE_FLAGS)
        )
    )

    screened = ResearchScreener().screen(results=results, criteria=ScreeningCriteria())

    assert ScreeningCriteria().worst_severity_admitted == SEVERITY_ORDER[-1]
    assert ScreeningCriteria().max_risk_flags is None
    assert len(screened.items) == len(results)
    assert screened.excluded == ()
    assert [item.severity for item in screened.items] == [
        "clear",
        "unrecognised",
        "severe",
        "blocked",
    ]


def test_an_abstaining_result_screens_without_raising_and_keeps_its_flags_reading() -> None:
    """The path the committee's defect would otherwise break, driven through the real screen.

    An abstention is a first-class outcome (PRD S42) and `ScreeningCriteria.directions` names
    it, so a screen must be able to rank one. It is here rather than folded into the
    governance test because `ResearchScreener` is the caller that would have raised.
    """
    abstaining_signal = SignalFrame(
        subject="000001.SZ",
        as_of=AS_OF,
        direction="abstain",
        strength=0.0,
        confidence=0.3,
        horizon="5d",
        abstention_reason="evidence insufficient",
        risk_flags=(COMMITTEE_DISAGREEMENT,),
    )
    manifest = RunManifest(
        run_id="run-abstain",
        mode="replay",
        as_of=AS_OF,
        code_commit=COMMIT,
        config_digest=CONFIG_DIGEST,
        random_seed=7,
        started_at=AS_OF,
        finished_at=AS_OF,
        status="succeeded",
    )
    abstaining = ResearchRunResult(
        signal=abstaining_signal,
        decision=DecisionLedger(
            run_id=manifest.run_id,
            run_manifest_id=manifest.run_manifest_id,
            created_at=AS_OF,
            risk_decision="pass",
            final_action="abstain",
            code_commit=COMMIT,
        ),
        manifest=manifest,
        agent_results=(),
    )

    screened = ResearchScreener().screen(
        results=(abstaining, make_result(subject="600000.SH", confidence=0.2)),
        criteria=ScreeningCriteria(directions=("abstain", "bullish")),
    )

    assert [item.subject for item in screened.items] == ["600000.SH", "000001.SZ"]
    assert screened.items[1].severity == "unrecognised"
    assert screened.items[1].driving_flags == (COMMITTEE_DISAGREEMENT,)
    assert screened.items[1].final_action == "abstain"


# --- the split ---------------------------------------------------------------------------


SHIPPED_BEFORE_THE_SPLIT: Final[frozenset[str]] = frozenset(
    {
        "RESEARCH_REPORT_VERSIONS",
        "WATCHLIST_ENTRY_VERSIONS",
        "ReportStore",
        "ResearchReport",
        "ResearchReportFactory",
        "ResearchScreener",
        "ScreeningCriteria",
        "ScreeningItem",
        "ScreeningResult",
        "WatchlistEntry",
        "WatchlistStore",
    }
)
"""`product/research.py.__all__` exactly as it stood at `5e18791`, before this issue split it."""


def test_the_facade_re_exports_the_same_objects_the_split_modules_declare() -> None:
    """The split moved no caller: identity, not equality, and in both directions.

    `sdk.py`, `api/app.py` and `runtime/composition.py` all import from `product.research` and
    none of them was touched, so a re-export that became a copy would give two classes with the
    same name -- which pydantic and `isinstance` both notice and a name check does not.
    """
    sources = (governance, reporting, screening, watchlist)

    assert set(research.__all__) >= SHIPPED_BEFORE_THE_SPLIT
    assert set(research.__all__) == {name for module in sources for name in module.__all__}

    for name in research.__all__:
        owners = [module for module in sources if name in module.__all__]
        assert len(owners) == 1, f"{name} is declared by {len(owners)} split modules"
        assert getattr(research, name) is getattr(owners[0], name), name


def test_each_split_module_carries_one_responsibility_and_not_the_others() -> None:
    """The half of this issue that is the split: three files, three subjects, no overlap."""
    assert set(screening.__all__) == {
        "EXCLUSION_PRECEDENCE",
        "KNOWN_SCREENING_LIMITATIONS",
        "PER_RESULT_EXCLUSION_REASONS",
        "SCREENING_LIMITATION_CODES",
        "ResearchScreener",
        "ScreeningLimitation",
        "ScreeningCriteria",
        "ScreeningExclusion",
        "ScreeningExclusionReason",
        "ScreeningItem",
        "ScreeningResult",
    }
    assert set(watchlist.__all__) == {
        "WATCHLIST_ENTRY_VERSIONS",
        "WatchlistEntry",
        "WatchlistStore",
    }
    assert set(reporting.__all__) == {
        "RESEARCH_REPORT_VERSIONS",
        "ReportStore",
        "ResearchReport",
        "ResearchReportFactory",
    }
    source = (PRODUCT_ROOT / "research.py").read_text(encoding="utf-8")
    assert "class " not in source, "the facade must re-export, not redeclare"


# --- helpers -----------------------------------------------------------------------------


def unflagged_signal(flags: tuple[str, ...]) -> SignalFrame:
    """One ordinary signal carrying `flags` and nothing else worth reading."""
    return SignalFrame(
        subject="000001.SZ",
        as_of=AS_OF,
        direction="bullish",
        strength=0.4,
        confidence=0.7,
        horizon="5d",
        evidence_ids=EVIDENCE,
        risk_flags=flags,
    )


def flag_severity_of_no_flags() -> GovernanceSeverity:
    """The `clear` rung, reached the only way it can be reached."""
    return assess(unflagged_signal(())).severity


def signals_agreeing_only_on(flags: tuple[str, ...]) -> tuple[SignalFrame, ...]:
    """Nine signals that share `risk_flags` and differ on every other readable field."""
    return (
        SignalFrame(
            subject="000001.SZ",
            as_of=AS_OF,
            direction="bullish",
            strength=1.0,
            confidence=1.0,
            horizon="1d",
            evidence_ids=("evd_a",),
            risk_flags=flags,
        ),
        SignalFrame(
            subject="600519.SH",
            as_of=datetime(1999, 12, 31, 23, 59, tzinfo=UTC),
            direction="bearish",
            strength=-1.0,
            confidence=0.0,
            horizon="999d",
            evidence_ids=("evd_b", "evd_c"),
            confirmation_conditions=("holds",),
            invalidation_conditions=("breaks",),
            risk_flags=flags,
        ),
        SignalFrame(
            subject="x",
            as_of=datetime(2100, 6, 1, tzinfo=UTC),
            direction="neutral",
            strength=0.0,
            confidence=0.5,
            horizon="20d",
            evidence_ids=("evd_d",),
            risk_flags=flags,
        ),
        SignalFrame(
            subject="y" * 128,
            as_of=AS_OF,
            direction="abstain",
            strength=0.0,
            confidence=0.25,
            horizon="5d",
            abstention_reason="evidence insufficient",
            risk_flags=flags,
        ),
        SignalFrame(
            subject="000002.SZ",
            as_of=AS_OF,
            direction="bullish",
            strength=0.01,
            confidence=0.99,
            horizon="250d",
            evidence_ids=("evd_e",),
            confirmation_conditions=("a", "b", "c"),
            risk_flags=flags,
        ),
        SignalFrame(
            subject="000003.SZ",
            as_of=AS_OF,
            direction="bearish",
            strength=-0.5,
            confidence=0.75,
            horizon="10d",
            evidence_ids=("evd_f",),
            invalidation_conditions=("x", "y"),
            risk_flags=flags,
        ),
        SignalFrame(
            subject="000004.SZ",
            as_of=AS_OF,
            direction="neutral",
            strength=0.14,
            confidence=0.33,
            horizon="3d",
            evidence_ids=("evd_g",),
            risk_flags=flags,
        ),
        SignalFrame(
            subject="000005.SZ",
            as_of=AS_OF,
            direction="abstain",
            strength=0.0,
            confidence=1.0,
            horizon="7d",
            abstention_reason="contradictory evidence",
            risk_flags=flags,
        ),
        SignalFrame(
            subject="000006.SZ",
            as_of=AS_OF,
            direction="bullish",
            strength=0.6,
            confidence=0.6,
            horizon="60d",
            evidence_ids=("evd_h", "evd_i", "evd_j"),
            risk_flags=flags,
        ),
    )


def executable_string_constants(path: Path) -> set[str]:
    """Every string constant in `path` that is not a docstring.

    A docstring is a bare string expression *statement*, whether it opens a module, class or
    function or trails an attribute -- identified by position rather than by content, which is
    `tests/unit/test_known_limitation_registries.py::_docstring_constants`' rule.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    docstrings: set[int] = set()
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if not isinstance(body, list):
            continue
        for statement in body:
            if (
                isinstance(statement, ast.Expr)
                and isinstance(statement.value, ast.Constant)
                and isinstance(statement.value.value, str)
            ):
                docstrings.add(id(statement.value))
    return {
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in docstrings
    }


def test_the_docstring_filter_is_what_makes_the_no_fourth_list_audit_mean_anything(
    tmp_path: Path,
) -> None:
    """The extractor's own test, without which the audit above could pass vacuously.

    `governance.py` names five real flags in its module docstring, on purpose, so the audit
    only means something if a docstring is skipped **and** a real literal is caught. An
    extractor that returned nothing at all would satisfy the first half and check nothing, so
    both directions are driven -- the second on a probe file naming the same flag twice, once
    in each position.
    """
    path = PRODUCT_ROOT / "governance.py"
    assert "future_data" in path.read_text(encoding="utf-8")
    assert "future_data" not in executable_string_constants(path)
    assert "runtime-risk-gate" in executable_string_constants(path)

    probe = tmp_path / "probe.py"
    probe.write_text('"""future_data, named in prose."""\n\nBANNED = "future_data"\n')
    assert executable_string_constants(probe) == {"future_data"}


# --- the limitations registry ------------------------------------------------------------


def test_the_declared_screening_limitations_are_the_closed_set_this_module_reports() -> None:
    """`KNOWN_SCREENING_LIMITATIONS` as an equality, which is what binds every code to the suite.

    Equality rather than membership, for `test_known_limitation_registries.py`'s reason: a
    membership assertion can see a code that was renamed and never a code that was removed.
    Three of the seven are driven for real by the tests named beside them; the other four are
    disclosures about what this screen deliberately does not do, and a test asserting about
    such a sentence would be asserting about the sentence.
    """
    assert {
        "a_severity_orders_a_list_and_changes_no_gate_decision_anywhere",
        "the_two_shipped_gates_disagree_and_this_screen_ranks_them_rather_than_reconciling",
        "an_unrecognised_flag_and_a_misspelling_of_a_named_one_are_the_same_rung",
        "the_committee_is_read_through_a_probe_because_it_refuses_an_abstaining_signal",
        "a_flag_count_is_kept_as_a_filter_and_is_not_a_governance_reading",
        "the_default_screen_admits_every_rung_and_only_reorders",
        "a_flag_severity_is_memoised_per_process_and_a_gate_swapped_at_runtime_is_not_seen",
    } == SCREENING_LIMITATION_CODES
    assert len(KNOWN_SCREENING_LIMITATIONS) == len(SCREENING_LIMITATION_CODES) == 7
    assert all(entry.detail for entry in KNOWN_SCREENING_LIMITATIONS)
    assert all(
        entry.code in entry.detail or entry.code not in entry.detail
        for entry in KNOWN_SCREENING_LIMITATIONS
    )


def test_a_misspelling_of_a_named_flag_demotes_it_and_promotes_the_candidate() -> None:
    """`an_unrecognised_flag_and_a_misspelling_of_a_named_one_are_the_same_rung`, driven.

    The direction is the uncomfortable one and is why the entry exists: a typo does not make a
    name look worse, it makes it look *better*. `future_data` is `blocked` and last; the same
    string with a hyphen is `unrecognised` and outranks it.
    """
    named = sorted(RiskGate._blocking_flags)[0]
    typo = named.replace("_", "-")
    assert typo != named

    assert flag_severity(named) == "blocked"
    assert flag_severity(typo) == "unrecognised"
    assert flag_severity(typo) == flag_severity("a-word-no-gate-has-ever-heard-of")

    screened = ResearchScreener().screen(
        results=(
            make_result(subject="000001.SZ", confidence=0.5, risk_flags=(named,)),
            make_result(subject="600000.SH", confidence=0.5, risk_flags=(typo,)),
        ),
        criteria=ScreeningCriteria(),
    )
    assert [item.subject for item in screened.items] == ["600000.SH", "000001.SZ"]
    assert [item.severity for item in screened.items] == ["unrecognised", "blocked"]


def test_a_severity_changes_no_ledger_and_no_runtime_gate_verdict() -> None:
    """`a_severity_orders_a_list_and_changes_no_gate_decision_anywhere`, driven.

    The demoted candidate's own `DecisionLedger` still says `pass`/`watch` and `RiskGate` still
    returns what it always returned. The screen re-orders a reading; it enforces nothing.
    """
    flagged = make_result(subject="000001.SZ", confidence=0.95, risk_flags=("future_data",))
    screened = ResearchScreener().screen(
        results=(flagged, make_result(subject="600000.SH", confidence=0.1)),
        criteria=ScreeningCriteria(),
    )

    assert screened.items[-1].subject == "000001.SZ"
    assert screened.items[-1].severity == "blocked"
    assert flagged.decision.risk_decision == "pass"
    assert flagged.decision.final_action == "watch"
    assert screened.items[-1].final_action == "watch"
    assert RiskGate().evaluate(flagged.signal) == "block"
    assert screened.items[-1].gate_decision == RiskGate().evaluate(flagged.signal)


def test_the_flag_severity_memo_does_not_see_a_gate_swapped_after_first_use(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`a_flag_severity_is_memoised_per_process_and_a_gate_swapped_at_runtime_is_not_seen`.

    Both directions: the memo holds the first answer across a swapped gate, and clearing it
    picks the new one up. Driven rather than described, because "harmless on the shipped path"
    is a claim about a staleness whose existence has to be shown first.
    """
    flag = "a-flag-invented-for-this-memo-test"
    flag_severity.cache_clear()
    try:
        assert flag_severity(flag) == "unrecognised"

        monkeypatch.setattr(governance, "_runtime_risk_gate", lambda signal: "block")
        assert flag_severity(flag) == "unrecognised", "the memo must hold the first answer"

        flag_severity.cache_clear()
        assert flag_severity(flag) == "blocked", "clearing it must pick the new gate up"
    finally:
        monkeypatch.undo()
        flag_severity.cache_clear()

    assert flag_severity(flag) == "unrecognised"
