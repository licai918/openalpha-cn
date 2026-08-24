"""`V2-P4-006`: a screen that reads *which* risk flags a candidate carries, not how many.

The acceptance measurement is `test_a_high_confidence_signal_carrying_a_severe_flag_does_not
_rank_first`, and it failed against the implementation this replaces with
`['000001.SZ', '600000.SH'] != ['600000.SH', '000001.SZ']` -- the old key was
`(-confidence, -strength, subject)`, so 0.95 with `future_data` outsorted 0.60 with nothing.

Everything below the ordering tests is about the *source* of severity, and `V2-P4-030` moved it.

`V2-P4-006` could not name a flag from inside `product/`: the vocabulary was open, three modules
read disjoint closed subsets of it, and writing the union down here would have been a fourth
list. So `governance.py` held no flag strings at all and obtained severity by *asking* the two
shipped gates -- driving a synthetic one-flag `SignalFrame` through each and reading the verdict
back out.

`domain/risk_flag.py::RiskFlag` now declares every flag **with what it is worth**, and both gates
derive their sets from it, so severity is read rather than inferred.
`test_no_shipped_risk_flag_is_written_in_executable_code_under_product` survives that move
unchanged and matters more than before -- `product/` still may not spell a flag, because a
literal here would be a copy of a vocabulary that now has an owner.
`tests/unit/domain/test_risk_flag.py` holds the vocabulary itself.

Three tests in this file used to assert the defect and now assert its absence, and each says so
where it stands: the two gates are no longer disjoint
(`test_the_two_shipped_gates_now_read_one_vocabulary_and_still_disagree_usefully`), a misspelling
is refused rather than demoted (`test_a_misspelling_of_a_named_flag_is_refused_rather_than_
promoting_the_candidate`), and the committee can be asked about an abstention
(`test_the_shipped_committee_can_now_be_asked_about_an_abstaining_signal`).
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
from openalpha_cn.domain.risk_flag import RISK_FLAGS, RiskFlag
from openalpha_cn.domain.run import RunManifest
from openalpha_cn.domain.signal import SignalFrame
from openalpha_cn.product import governance, reporting, research, screening, watchlist
from openalpha_cn.product.governance import (
    SEVERITY_ORDER,
    SEVERITY_RANK,
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

COMMITTEE_SEVERE_FLAGS: Final[tuple[RiskFlag, ...]] = tuple(
    flag for flag in RISK_FLAGS if flag.severity == "severe"
)
"""The flags the committee blocks on, **read off the vocabulary** rather than spelled here.

They used to be spelled out, because they lived in a set literal inside
`DeliberationCommittee.review`'s body and no attribute exposed them. `V2-P4-030` lifted them to
`domain/risk_flag.py`, so this file no longer has to name a flag to test one -- which is the
same property `test_no_shipped_risk_flag_is_written_in_executable_code_under_product` demands of
`product/` itself.
"""

COMMITTEE_DISAGREEMENT: Final[RiskFlag] = RiskFlag.committee_disagreement
"""The flag the committee raises about its own deliberation.

Neither gate read it before `V2-P4-030` -- so `RiskGate` answered `pass` on a signal the
committee had just recorded as disputed. It is `reduced` now, and both gates act on it.
"""


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


def gate_flags() -> frozenset[RiskFlag]:
    """Every flag `RiskGate` acts on, read off the class rather than restated."""
    return RiskGate._blocking_flags | RiskGate._reducing_flags


def flags_at(severity: GovernanceSeverity) -> tuple[RiskFlag, ...]:
    """Every declared flag sitting on `severity`, in declaration order."""
    return tuple(flag for flag in RISK_FLAGS if flag.severity == severity)


def one_flag_per_rung() -> dict[GovernanceSeverity, RiskFlag]:
    """One real flag for each rung a *signal* can reach above `clear`.

    Three rungs rather than four. `unrecognised` is no longer among them and cannot be: it means
    "this string is not a declared flag", and `SignalFrame.risk_flags` refuses such a string
    outright since `V2-P4-030`. The rung survives on the ladder because `flag_severity` takes a
    bare `str` and has to answer about one -- see
    `test_the_unrecognised_rung_survives_for_strings_but_no_signal_can_reach_it`.
    """
    return {rung: flags_at(rung)[0] for rung in ("blocked", "severe", "reduced")}


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

    The benign flag was `cosmetic-note` -- an invented string, which the open set accepted and
    scored at `unrecognised`. It is `source_uri_missing` now, a declared `reduced` flag, and the
    point survives the change intact: the count still cannot tell the two apart and the severity
    still can. What no longer exists is the *invented* flag, which is `V2-P4-030`.
    """
    severe = make_result(subject="000001.SZ", confidence=0.8, risk_flags=("future_data",))
    benign = make_result(
        subject="600000.SH", confidence=0.8, risk_flags=(RiskFlag.source_uri_missing,)
    )

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
        criteria=ScreeningCriteria(worst_severity_admitted="reduced"),
    )
    assert [item.subject for item in by_severity.items] == ["600000.SH"]
    assert [entry.subject for entry in by_severity.excluded] == ["000001.SZ"]
    assert by_severity.excluded[0].reason == "worse_than_admitted_severity"
    assert by_severity.excluded[0].driving_flags == ("future_data",)


def test_a_disputed_signal_no_longer_sorts_as_though_it_were_clean() -> None:
    """The fracture `V2-P4-005` recorded, closed on the screening plane and then at the gate.

    `committee-disagreement` was in neither gate's closed subset, so `RiskGate` returned `pass`
    for the one flag in the build guaranteed to be spelled correctly -- the committee raises it
    itself. `V2-P4-006` gave it a rung here (`unrecognised`, one above `clear`) without being
    able to change what the runtime gate did about it. `V2-P4-030` declared it `reduced`, so the
    gate now reduces on it and the rung moved with the declaration.

    Both facts are asserted rather than assumed, because the second is the one that was a
    fail-open hole: a screen that merely re-sorts a disputed name leaves the gate that decides
    whether to act on it saying `pass`.
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
    assert RiskGate().evaluate(disputed_signal) == "reduce"
    assert flag_severity(COMMITTEE_DISAGREEMENT) == "reduced"

    disputed = make_result(
        subject="000001.SZ", confidence=0.9, risk_flags=(COMMITTEE_DISAGREEMENT,)
    )
    clean = make_result(subject="600000.SH", confidence=0.5)

    screened = ResearchScreener().screen(results=(disputed, clean), criteria=ScreeningCriteria())

    assert [item.subject for item in screened.items] == ["600000.SH", "000001.SZ"]
    assert screened.items[1].severity == "reduced"
    assert screened.items[1].driving_flags == (COMMITTEE_DISAGREEMENT,)
    assert screened.items[1].gate_decision == "reduce"
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


def test_the_two_shipped_gates_now_read_one_vocabulary_and_still_disagree_usefully() -> None:
    """The premise of this design, inverted from what `V2-P4-006` had to assume.

    `V2-P4-005` measured `RiskGate`'s five flags and the committee's three to be **disjoint**,
    with `committee-disagreement` in neither, and the previous version of this test asserted
    exactly that -- because a severity read out of two gates' behaviour is only well defined
    while the two disagree about which words they know.

    `V2-P4-030` removed the disagreement about *words* and kept the one about *decisions*. Every
    declared flag now reaches both gates; what still differs, deliberately, is what each does
    with the `severe` band. That residual difference is what keeps `severe` a rung of its own
    rather than a synonym for `blocked`, and it is asserted here as an equality on the gate's
    own sets so that a gate quietly acquiring a private set again fails this file.
    """
    assert gate_flags() == set(RISK_FLAGS), "RiskGate must act on every declared flag"
    assert RiskGate._blocking_flags == set(flags_at("blocked"))
    assert RiskGate._reducing_flags == set(flags_at("severe")) | set(flags_at("reduced"))

    for flag in RiskGate._blocking_flags:
        assert flag_severity(flag) == "blocked"

    for flag in COMMITTEE_SEVERE_FLAGS:
        assert flag_severity(flag) == "severe"
        assert flag in gate_flags(), (
            f"{flag} used to reach RiskGate and return pass; that is the hole V2-P4-030 closed"
        )
        assert RiskGate().evaluate(unflagged_signal((flag,))) == "reduce"

    assert flag_severity(COMMITTEE_DISAGREEMENT) == "reduced"
    assert COMMITTEE_DISAGREEMENT in gate_flags()


def test_no_shipped_risk_flag_is_written_in_executable_code_under_product() -> None:
    """There is no second list under `product/`, and this is how that is known rather than asserted.

    Every declared flag, checked against every string constant under
    `src/openalpha_cn/product/` that is **not** a docstring. Prose may name a flag -- both
    modules do, at length -- and code may not, because a literal in code is a copy and a copy is
    the thing that drifts. The docstring/executable split is
    `tests/unit/test_known_limitation_registries.py`'s, pointed at source instead of tests.

    Unchanged by `V2-P4-030` except for the count, and that is worth saying: this test was
    written when `product/` had no vocabulary it was *allowed* to name, and it still passes now
    that one exists in `domain/`. The rule was never "product may not know the flags", it was
    "product may not be where they are written".
    `tests/unit/domain/test_risk_flag.py::test_no_other_module_declares_the_risk_flag_set` is
    the same audit over the whole source tree.
    """
    shipped = {flag.value for flag in RISK_FLAGS}
    assert len(shipped) == 10

    offenders: dict[str, set[str]] = {}
    for path in sorted(PRODUCT_ROOT.rglob("*.py")):
        found = executable_string_constants(path) & shipped
        if found:
            offenders[path.name] = found

    assert offenders == {}, (
        "a risk-flag string in executable position under product/ is a second copy of a "
        "vocabulary that has an owner; import domain/risk_flag.py::RiskFlag instead"
    )


def test_both_shipped_gates_answer_about_the_flags_and_about_nothing_else_on_the_signal() -> None:
    """Both gates read `risk_flags` and no other field, measured rather than assumed.

    This used to be the load-bearing justification for `governance._probe`: severity was read
    off a synthetic carrier of the flags, so the design rested on neither gate looking at any
    other field. The probe is gone -- `assess` asks about the real signal now -- but the
    property is still worth holding, because `GovernanceVerdict.gate_decision` and
    `committee_decision` are what the gates will actually do, and a gate that started reading
    `confidence` would make a severity and a verdict disagree with no test noticing.

    Nine signals that agree on nothing but `risk_flags` -- different subject, instant,
    direction, strength, confidence, horizon, evidence and conditions -- must produce one
    verdict from each gate and one severity from `assess`.

    **All nine, including the two abstaining ones.** The previous version drove the committee on
    seven, because `review` could not be handed an abstention at all; `V2-P4-029` fixed that,
    and driving the full nine is how this test stops silently excluding the case that used to
    crash.
    """
    for flags in ((), ("future_data",), COMMITTEE_SEVERE_FLAGS[:1], (COMMITTEE_DISAGREEMENT,)):
        signals = signals_agreeing_only_on(flags)
        assert len(signals) == 9
        assert sum(1 for item in signals if item.direction == "abstain") == 2

        gate_answers = {RiskGate().evaluate(item) for item in signals}
        committee_answers = {
            DeliberationCommittee().review(signal=item, results=()).risk_decision
            for item in signals
        }
        severities = {assess(item).severity for item in signals}

        assert len(gate_answers) == 1, f"RiskGate moved on a non-flag field for {flags!r}"
        assert len(committee_answers) == 1, f"the committee moved on a non-flag field: {flags!r}"
        assert len(severities) == 1, f"assess moved on a non-flag field for {flags!r}"
        expected = "clear" if not flags else flag_severity(flags[0])
        assert severities == {expected}


def test_the_shipped_committee_can_now_be_asked_about_an_abstaining_signal() -> None:
    """The defect this module used to route around, and the indirection that went with it.

    The previous version of this test asserted the **opposite**, and did so deliberately: it
    pinned `ValidationError: directional signal requires evidence` on the real class so that the
    day somebody repaired `review` it would go red and `governance.assess`'s workaround would be
    re-read rather than kept out of habit. `V2-P4-029` is that day. It went red exactly as
    designed, and this is the reread.

    `review` recomputed `direction` from `adjusted_strength` into a `Literal` with no `abstain`
    in it, so an abstaining signal -- which by `SignalFrame.validate_conclusion` carries no
    `evidence_ids` -- came back out directional and killed its own `DeliberationOutcome`. That
    is why `assess` used to ask both gates about a synthetic carrier of the flags rather than
    about the signal. It asks about the signal now.

    The second half is unchanged and is the reason this test still exists: an abstention is a
    first-class outcome (PRD S42) whose flags must still be rated.
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
    outcome = DeliberationCommittee().review(signal=abstaining, results=())
    assert outcome.adjusted_signal.direction == "abstain"
    assert outcome.risk_decision == "pass"

    assert assess(abstaining).severity == "clear"
    flagged = abstaining.model_copy(update={"risk_flags": (RiskFlag.future_data,)})
    verdict = assess(flagged)
    assert verdict.severity == "blocked"
    assert verdict.driving_flags == (RiskFlag.future_data,)
    assert verdict.gate_decision == "block"


def test_a_signals_severity_is_the_worst_of_its_flags_taken_one_at_a_time() -> None:
    """`assess` calls each gate once; the rung it derives must equal the per-flag maximum.

    Both gates are set intersections over the whole tuple, so the two readings agree today.
    The assertion is what would notice if one stopped being one.
    """
    rungs = one_flag_per_rung()
    mixed = (rungs["severe"], rungs["blocked"], rungs["reduced"])

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
    """`clear` means no flags, and nothing else -- every declared flag outranks it.

    This used to drive one invented string (`a-word-no-gate-has-ever-heard-of`) and assert it
    landed on `unrecognised`. That string is unconstructable now, so the claim is made the way
    it should always have been made: over the **whole vocabulary**, so a flag added at the
    gentlest severity still cannot be mistaken for a clean signal.
    """
    clean = assess(unflagged_signal(()))
    assert clean.severity == "clear"
    assert clean.driving_flags == ()

    for flag in RISK_FLAGS:
        verdict = assess(unflagged_signal((flag,)))
        assert verdict.driving_flags == (flag,)
        assert SEVERITY_RANK[verdict.severity] > SEVERITY_RANK["clear"], flag


def test_the_unrecognised_rung_survives_for_strings_but_no_signal_can_reach_it() -> None:
    """Where `unrecognised` went, which is the ladder's half of `V2-P4-030`.

    It meant "no shipped gate names this string", and the string that landed there most often
    was a **misspelling** -- which is how a typo of the build's most serious flag used to
    outrank the flag itself. `SignalFrame` refuses such a string now, so `assess` can never
    return this rung.

    The rung is kept rather than deleted, for a reason that is about the wire and not about
    tidiness: `ScreeningCriteria.worst_severity_admitted` is a field of a shipped request body,
    and removing a value a caller may already be sending would break `POST /api/v1/screen` in
    order to record a fact. `flag_severity` still answers it, because it takes a bare `str` and
    "that is not a flag" is a real answer to a real question.
    """
    assert "unrecognised" in SEVERITY_ORDER
    assert flag_severity("a-word-no-gate-has-ever-heard-of") == "unrecognised"
    assert ScreeningCriteria(worst_severity_admitted="unrecognised").worst_severity_admitted == (
        "unrecognised"
    )

    with pytest.raises(ValidationError, match="risk_flags"):
        unflagged_signal(("a-word-no-gate-has-ever-heard-of",))

    reachable = {assess(unflagged_signal((flag,))).severity for flag in RISK_FLAGS}
    assert "unrecognised" not in reachable


def test_the_ladder_is_declared_once_and_every_rung_is_reachable() -> None:
    """The vocabulary, the order and the ranking are one declaration, and none of it is dead.

    `SHIPPED_RISK_GATES` used to be asserted here, and its deletion is `V2-P4-036`: it called
    itself the single source for what counts as severe and **nothing read it**. Measured on the
    previous commit, adding an always-blocking third gate left `flag_severity('bogus-flag')` at
    `unrecognised`, and emptying the registry entirely left `flag_severity('future_data')` at
    `blocked`. It is not replaced by a registry that works, because a declared vocabulary leaves
    it nothing to do.

    The `lru_cache` bound was asserted here too, and it is gone for the same kind of reason: the
    memo existed because the answer was computed by building a `SignalFrame` and running a
    committee, and it was *bounded* because the keys came from request bodies. The answer is a
    dictionary lookup on a ten-member enum now.
    """
    assert SEVERITY_ORDER == ("clear", "unrecognised", "reduced", "severe", "blocked")
    assert {name: index for index, name in enumerate(SEVERITY_ORDER)} == SEVERITY_RANK
    assert not hasattr(governance, "SHIPPED_RISK_GATES")
    assert not hasattr(flag_severity, "cache_info")

    reached = {"clear": flag_severity_of_no_flags(), "unrecognised": flag_severity("not-a-flag")}
    reached |= {rung: flag_severity(flag) for rung, flag in one_flag_per_rung().items()}
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
            make_result(
                subject="AAA",
                confidence=0.9,
                risk_flags=(RiskFlag.source_uri_missing, RiskFlag.redistribution_restricted),
            ),
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
        subject="AAA",
        confidence=0.05,
        direction="neutral",
        risk_flags=(RiskFlag.future_data, RiskFlag.source_uri_missing),
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
        "reduced",
        "severe",
        "blocked",
    ]


def test_an_abstaining_result_screens_without_raising_and_keeps_its_flags_reading() -> None:
    """An abstention ranked through the real screen, which is where the defect would have landed.

    An abstention is a first-class outcome (PRD S42) and `ScreeningCriteria.directions` names
    it, so a screen must be able to rank one. It is here rather than folded into the governance
    test because `ResearchScreener` is the caller that would have raised -- and, until
    `V2-P4-029`, only did not because `assess` routed around the committee entirely.
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
    assert screened.items[1].severity == "reduced"
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
    assert "unrecognised" in executable_string_constants(path)

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
        "a_recovery_row_carrying_a_caller_injected_flag_is_refused_rather_than_migrated",
        "a_severity_is_declared_on_the_flag_and_is_not_a_measurement_of_either_gate",
        "a_flag_count_is_kept_as_a_filter_and_is_not_a_governance_reading",
        "the_default_screen_admits_every_rung_and_only_reorders",
        "the_unrecognised_rung_is_kept_for_the_wire_and_no_signal_can_reach_it",
    } == SCREENING_LIMITATION_CODES
    assert len(KNOWN_SCREENING_LIMITATIONS) == len(SCREENING_LIMITATION_CODES) == 7
    assert all(entry.detail for entry in KNOWN_SCREENING_LIMITATIONS)
    assert all(
        entry.code in entry.detail or entry.code not in entry.detail
        for entry in KNOWN_SCREENING_LIMITATIONS
    )


def test_a_misspelling_of_a_named_flag_is_refused_rather_than_promoting_the_candidate() -> None:
    """The harm `V2-P4-006` recorded and `V2-P4-030` removed, driven from both ends.

    The previous version of this test asserted the harm itself, under the entry
    `an_unrecognised_flag_and_a_misspelling_of_a_named_one_are_the_same_rung`: `future_data` was
    `blocked` and sorted last, the same string with a hyphen was `unrecognised` and **outranked
    it**. A typo did not make a name look worse; it made it look better, which is the worst
    possible direction for a governance reading to fail in.

    A misspelling is now refused at the contract. The refusal is asserted to name both the field
    and the offending value, because that is what makes it a repair rather than a different
    failure: a producer has to be able to see which string it got wrong.

    The second half is the part that could regress silently -- that the correctly spelled flag
    still carries its full weight through a real screen -- so it is driven rather than assumed.
    """
    named = sorted(RiskGate._blocking_flags)[0]
    typo = named.value.replace("_", "-")
    assert typo != named.value

    with pytest.raises(ValidationError) as caught:
        make_result(subject="000001.SZ", confidence=0.5, risk_flags=(typo,))
    assert "risk_flags" in str(caught.value)
    assert typo in str(caught.value)

    assert flag_severity(named) == "blocked"
    assert flag_severity(typo) == "unrecognised"

    screened = ResearchScreener().screen(
        results=(
            make_result(subject="000001.SZ", confidence=0.5, risk_flags=(named,)),
            make_result(subject="600000.SH", confidence=0.5),
        ),
        criteria=ScreeningCriteria(),
    )
    assert [item.subject for item in screened.items] == ["600000.SH", "000001.SZ"]
    assert [item.severity for item in screened.items] == ["clear", "blocked"]


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


def test_a_severity_no_longer_depends_on_what_a_gate_happens_to_answer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """What replaced `a_flag_severity_is_memoised_per_process_and_a_gate_swapped_at_runtime...`.

    That entry existed because `flag_severity` derived its answer by *running* the gates and
    memoised the result, so a gate swapped after first use was invisible until the cache was
    cleared. Both halves are gone: the memo, and the derivation it memoised.

    The replacement claim is stronger and is what is driven here -- a severity is a property of
    the flag, so swapping a gate wholesale cannot move one at all, with or without a cache. The
    gate swap is still performed rather than described, because "cannot be affected" is only
    worth asserting against something that would previously have affected it.

    What the swap *does* still move is `GovernanceVerdict.gate_decision`, which is the honest
    split: what a flag is worth is declared, and what a gate does about it is the gate's.
    """
    flagged = unflagged_signal((RiskFlag.source_uri_missing,))
    assert flag_severity(RiskFlag.source_uri_missing) == "reduced"
    assert assess(flagged).gate_decision == "reduce"

    class AlwaysBlocking:
        def evaluate(self, signal: SignalFrame) -> str:
            return "block"

    monkeypatch.setattr(governance, "RiskGate", AlwaysBlocking)

    assert flag_severity(RiskFlag.source_uri_missing) == "reduced"
    assert assess(flagged).severity == "reduced", "a gate cannot redefine what a flag is worth"
    assert assess(flagged).gate_decision == "block", "but it still reports what it would do"
