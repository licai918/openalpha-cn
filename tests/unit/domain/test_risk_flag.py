"""`RiskFlag` is the only declaration of the risk-flag vocabulary, held by a source-tree audit.

`V2-P4-030`. The set was written out **three times** and the three did not agree:
`decisions/risk.py::RiskGate` blocked on two strings and reduced on three,
`agents/committee.py` treated three others as severe from a set literal inside `review`'s body,
and `committee-disagreement` -- which the committee raises about its own deliberation -- was in
neither, so `RiskGate` answered `pass` on a signal the committee had just marked as disputed.
Their intersection was measured empty.

Worse than the disagreement was that the set was **open**. `SignalFrame.risk_flags` was
`tuple[str, ...]`, so a producer that spelled `future-data` instead of `future_data` did not
fail: the flag dropped from `blocked` to `unrecognised` and the candidate carrying it moved
**up** a governed screen. `V2-P4-006` measured that and could not repair it from inside
`product/`, naming the fix as closing the vocabulary where the flags are written.

This module holds the closed vocabulary the way `tests/unit/domain/test_run_mode.py` holds
`RunMode`: there is one declaration, everything else names it, and
`test_no_other_module_declares_the_risk_flag_set` reads the **source tree** rather than a
registered list, so a fourth copy fails without anybody remembering to register it.
"""

from __future__ import annotations

import ast
from datetime import UTC, datetime
from pathlib import Path
from typing import Final, get_args

import pytest
from pydantic import ValidationError

from openalpha_cn.agents.committee import DeliberationCommittee
from openalpha_cn.decisions.risk import RiskGate
from openalpha_cn.domain.risk_flag import RISK_FLAGS, RiskFlag, RiskFlagSeverity
from openalpha_cn.domain.signal import SignalFrame
from openalpha_cn.product.governance import SEVERITY_ORDER, flag_severity

SOURCE_ROOT: Final[Path] = Path(__file__).resolve().parents[3] / "src" / "openalpha_cn"
DECLARING_MODULE: Final[Path] = SOURCE_ROOT / "domain" / "risk_flag.py"

NOW: Final[datetime] = datetime(2026, 1, 16, 7, 0, tzinfo=UTC)


def signal(*flags: str) -> SignalFrame:
    """A directional frame whose only interesting field is `risk_flags`."""
    return SignalFrame(
        subject="000001.SZ",
        as_of=NOW,
        direction="neutral",
        strength=0.0,
        confidence=0.5,
        horizon="5d",
        evidence_ids=("evd_000000000000000000000001",),
        risk_flags=flags,  # type: ignore[arg-type]
    )


# --- the acceptance criterion -----------------------------------------------------------


def test_a_misspelled_flag_is_refused_instead_of_being_demoted_to_unrecognised() -> None:
    """`V2-P4-006`'s measured harm, as the refusal that replaces it.

    This is the whole issue in one assertion. `future-data` is one character away from the most
    serious flag the build has, and under the open set it was worth *less* than the flag it was
    a typo of -- `unrecognised` sits above `clear` but below `reduced`, so the candidate
    carrying it sorted above candidates whose flags were spelled correctly. A screen cannot
    repair that, because by the time it reads the string the information that a flag was
    intended is gone.

    The refusal names the field and the offending value, which is the second half of the fix:
    a producer that writes a typo now learns which string it got wrong instead of shipping a
    promotion.
    """
    with pytest.raises(ValidationError) as caught:
        signal("future-data")

    message = str(caught.value)
    assert "risk_flags" in message
    assert "future-data" in message

    assert flag_severity("future_data") == "blocked"


def test_every_declared_flag_is_accepted_and_keeps_its_bare_string_form() -> None:
    """The single-source property, exercised through the contract that now names the enum.

    Iterating `RISK_FLAGS` rather than listing the strings is the point: a member added to the
    enum is exercised here without this test being edited.

    The `str` comparison is not incidental. `RiskFlag` is a `StrEnum` precisely so that
    `model_dump(mode="json")` emits the member's **value**, which is what makes this narrowing
    move no stored `signal_id` -- see
    `test_closing_the_vocabulary_moved_no_stored_signal_id` below.
    """
    assert len(RISK_FLAGS) == len(set(RISK_FLAGS))

    for flag in RISK_FLAGS:
        frame = signal(flag.value)
        assert frame.risk_flags == (flag,)
        assert frame.risk_flags[0] == flag.value
        assert frame.model_dump(mode="json")["risk_flags"] == [flag.value]


def test_closing_the_vocabulary_moved_no_stored_signal_id() -> None:
    """Why narrowing `risk_flags` is a domain change and not a data migration.

    `signal_id` hashes the canonical JSON of these fields (`domain/_identity.py`), and
    `run_recovery.payload` stores whole `SignalFrame`s. A representation change here would not
    have failed -- it would have silently re-identified every stored signal that carries a flag,
    which is the failure mode `test_the_enum_serialises_to_the_bare_string_the_literal_did`
    exists for on `RunMode`.

    The identities are asserted against hard-coded digests rather than against "it still works",
    for the same reason: a moved digest is invisible to any test that recomputes both sides.
    Every digest below was read off the pre-narrowing tree at `146698c` and pasted here, so this
    test compares against the old contract rather than against itself.
    """
    assert signal().signal_id == "sig_c197f137d580c7963a9f2c31"
    assert signal("source_uri_missing").signal_id == "sig_b6f02554f392914e7da0535d"
    assert signal("committee-disagreement").signal_id == "sig_d6df614c361f4fc733109e3f"
    assert signal("future_data", "data-quality").signal_id == "sig_f8728ff996759b61b8b41990"

    assert signal().signal_id != signal("source_uri_missing").signal_id


# --- the vocabulary is one declaration ----------------------------------------------------


def test_every_flag_declares_what_it_is_worth_and_cannot_be_added_without_one() -> None:
    """Severity is carried **on** the member, so a flag with no severity is unconstructable.

    A parallel `Mapping[RiskFlag, ...]` beside the enum would have been the obvious shape and is
    the one this rejects: it can drift, because a member added to the enum and not to the map is
    a state the language permits. Carrying the severity in the member's own value makes that
    state unreachable -- `enum` cannot build a member from a one-element tuple -- so this test
    asserts the *range* rather than the totality it can no longer lose.
    """
    declared = set(get_args(RiskFlagSeverity))
    assert declared == {"reduced", "severe", "blocked"}

    for flag in RISK_FLAGS:
        assert flag.severity in declared

    assert declared < set(SEVERITY_ORDER), (
        "a flag's severity has to be a rung of the governance ladder; clear and unrecognised "
        "are the two rungs no flag can occupy -- clear means no flags, unrecognised means the "
        "string is not a flag at all"
    )


def _string_constants_outside_docstrings(path: Path) -> set[str]:
    """Every `str` literal in `path` that is not a module/class/function docstring.

    Prose has to be excluded or the audit becomes unfalsifiable: this repository documents the
    flag vocabulary at length -- `product/governance.py` and `product/screening.py` both name
    several flags in prose on purpose -- and counting a docstring would make every one of those
    modules an offender. Lifted from `tests/unit/domain/test_run_mode.py`, which needed the
    same split for the same reason.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    docstrings: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            first = node.body[0] if node.body else None
            if (
                isinstance(first, ast.Expr)
                and isinstance(first.value, ast.Constant)
                and isinstance(first.value.value, str)
            ):
                docstrings.add(id(first.value))
    return {
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in docstrings
    }


def test_no_other_module_declares_the_risk_flag_set() -> None:
    """The audit that keeps `V2-P4-030` closed once it is closed.

    "Declares the set" is read as "spells at least **two** of the flag names as executable
    string literals", which is what every one of the three former declarations looked like --
    `RiskGate._blocking_flags` was a two-element `frozenset`, so a threshold of three would have
    let the smallest of them back in. Two is loose enough that a module mentioning a single flag
    in passing does not trip it, and tight enough that no former declaration could return.

    This is `tests/unit/domain/test_run_mode.py::test_no_other_module_declares_the_mode_set`
    with the threshold moved to fit the sets being guarded, and it grants **no** exemption:
    unlike `RunMode`, there is no frozen v1 snapshot that has to restate an older vocabulary,
    because `SignalFrame` stayed at `signal-frame/v1` through this narrowing.
    """
    names = {flag.value for flag in RiskFlag}
    declared = {
        path: sorted(names & _string_constants_outside_docstrings(path))
        for path in sorted(SOURCE_ROOT.rglob("*.py"))
        if len(names & _string_constants_outside_docstrings(path)) >= 2
    }

    assert set(declared) == {DECLARING_MODULE}
    assert set(declared[DECLARING_MODULE]) == names


# --- both gates read the declaration -------------------------------------------------------


def test_both_gates_answer_about_every_declared_flag_and_agree_with_its_severity() -> None:
    """The two gates' verdicts are derived from the declaration, and this is what proves it.

    Not vacuous, despite both gates deriving their sets from `RiskFlag`: the map from a
    severity to a *decision* is written separately in each gate, and the two deliberately
    differ on `severe`. The runtime gate reduces on a `severe` flag while the committee blocks
    on it, which is what keeps the `severe` rung distinguishable from `blocked` on the
    governance ladder.

    Before this issue every one of the `severe` rows answered `pass` at `RiskGate` -- a
    regulatory halt or a suspension reached the runtime gate and cleared it -- and
    `committee-disagreement` answered `pass` there too. Those are the fail-open holes the closed
    vocabulary removes, and they are the reason no row below reads `pass`.
    """
    expected_gate = {"blocked": "block", "severe": "reduce", "reduced": "reduce"}
    expected_committee = {"blocked": "block", "severe": "block", "reduced": "reduce"}

    for flag in RISK_FLAGS:
        frame = signal(flag.value)
        gate = RiskGate().evaluate(frame)
        committee = DeliberationCommittee().review(signal=frame, results=()).risk_decision

        assert gate == expected_gate[flag.severity], flag
        assert committee == expected_committee[flag.severity], flag
        assert gate != "pass", f"{flag} reaches the runtime gate and clears it"


def test_the_committee_no_longer_hides_its_severe_set_inside_review() -> None:
    """`regulatory`, `data-quality` and `suspension` are readable without running a committee.

    They used to live in a set literal **inside** `DeliberationCommittee.review`'s body -- not a
    class attribute, not a module constant -- which is why `product/governance.py` had to read
    the committee by *behaviour*, driving a synthetic one-flag signal through `review` just to
    learn what a string was worth. Reading them off `RiskFlag` is what let that probe go.
    """
    severe = {flag.value for flag in RISK_FLAGS if flag.severity == "severe"}
    assert severe == {"regulatory", "data-quality", "suspension"}

    for value in sorted(severe):
        assert flag_severity(value) == "severe"


def test_the_disputed_flag_the_committee_raises_is_no_longer_worth_nothing() -> None:
    """`committee-disagreement` at the runtime gate, which was the sharpest hole in the set.

    The committee adds this flag itself, so it is the one string in the build guaranteed to be
    spelled correctly -- and `RiskGate` had never heard of it, so
    `RiskGate().evaluate(...)` returned `pass` on a signal the committee had just recorded as
    disputed. It is declared `reduced` rather than `severe`: a close debate is a reason to size
    down, not a reason to refuse, and calling it severe would have made every debated signal
    with `|debate_net| < 0.35` a block.
    """
    assert RiskFlag.committee_disagreement.severity == "reduced"
    assert RiskGate().evaluate(signal("committee-disagreement")) == "reduce"
    assert flag_severity("committee-disagreement") == "reduced"


def test_the_redistribution_flag_this_build_actually_writes_is_declared() -> None:
    """The drift an open set had already accumulated, found while closing it.

    `evidence/builder.py` writes `f"redistribution_{metadata.redistribution}"` whenever
    redistribution is not `allowed`, and `EvidenceSnapshot.redistribution` is
    `Literal["allowed", "restricted", "unknown"]` -- so the build can write
    `redistribution_restricted` or `redistribution_unknown`. `RiskGate` named only the second.

    All three shipped providers declare `redistribution="restricted"`
    (`providers/akshare.py`, `providers/tushare.py`, `providers/chainlin.py`), so
    `redistribution_restricted` is the **only** redistribution flag this build can produce in
    practice, and it was worth `unrecognised` at every gate while the one that was named could
    not be generated at all. Both are declared now, and both are `reduced`.
    """
    assert RiskFlag.redistribution_restricted.severity == "reduced"
    assert RiskFlag.redistribution_unknown.severity == "reduced"
    assert flag_severity("redistribution_restricted") == "reduced"
