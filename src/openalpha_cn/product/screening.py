"""Governed screening: which flags a candidate carries decides where it sorts (`V2-P4-006`).

Split out of `product/research.py`, which carried three unrelated responsibilities in one file
-- this screen, the watchlist contract (`product/watchlist.py`) and the report contract
(`product/reporting.py`). `product/research.py` re-exports all three unchanged, so no caller
moved; see that module's docstring.

## What changed, and what a count could not do

The screen this replaces filtered on `min_confidence`, `directions`, `final_actions` and
`max_risk_flags`, then sorted by `(-confidence, -strength, subject)`. Every one of those reads
`risk_flags` as a **count** or not at all, so the ordering was confidence-first and a signal
carrying the most serious flag in the build sorted above a clean one as long as it was
confident. `tests/unit/product/test_governed_screening.py::
test_a_high_confidence_signal_carrying_a_severe_flag_does_not_rank_first` is that defect as an
ordering, and it failed against the previous implementation with
`['000001.SZ', '600000.SH'] != ['600000.SH', '000001.SZ']`.

`max_risk_flags` is **kept**, not removed: it is a field of a shipped API request body, and a
cap on how many flags a name may carry is a real thing a caller may want. What it is not is a
governance reading, and that is measured rather than argued --
`test_a_flag_count_cannot_separate_the_severe_from_the_benign_and_a_severity_can` builds two
candidates with one flag each and shows `max_risk_flags` admitting and ordering both
identically while `worst_severity_admitted` separates them.

## The ordering

    (SEVERITY_RANK[severity], -confidence, -strength, subject)

Governance first, and then the previous key unchanged beneath it. Confidence still orders
everything *within* a rung, which is the point: this is not "ignore confidence", it is "do not
let confidence buy a name past a flag the build's own gates object to". `severity` comes from
`product/governance.py`, which holds no flag strings of its own -- see that module for why the
single source of severity is the two shipped gates rather than a fourth list.

## Exclusions are returned rather than dropped

`assets/diagrams/openalpha-api-04-decision-products.svg` has said `返回排序与排除原因` --
"returns the ordering and the reasons for exclusion" -- since it was drawn, and the screen
returned no reasons at all: a caller saw a shorter list and could not tell a name that failed
`min_confidence` from one the limit cut. `ScreeningResult.excluded` closes that, one entry per
reviewed result that did not make `items`, carrying the governance verdict either way so that a
reader can see a name was excluded for confidence *and* what its flags were worth.

`reviewed == len(items) + len(excluded)` is therefore an exact partition rather than a summary,
and `test_every_reviewed_result_is_either_an_item_or_an_exclusion_and_never_both` holds it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final, Literal

from pydantic import BaseModel, ConfigDict, Field

from openalpha_cn.decisions.risk import RiskDecision
from openalpha_cn.product.governance import (
    SEVERITY_RANK,
    GovernanceSeverity,
    GovernanceVerdict,
    assess,
)
from openalpha_cn.runtime.contracts import ResearchRunResult

__all__ = [
    "EXCLUSION_PRECEDENCE",
    "KNOWN_SCREENING_LIMITATIONS",
    "PER_RESULT_EXCLUSION_REASONS",
    "SCREENING_LIMITATION_CODES",
    "ResearchScreener",
    "ScreeningCriteria",
    "ScreeningExclusion",
    "ScreeningExclusionReason",
    "ScreeningItem",
    "ScreeningLimitation",
    "ScreeningResult",
]

ScreeningExclusionReason = Literal[
    "below_min_confidence",
    "direction_not_requested",
    "final_action_not_requested",
    "over_max_risk_flags",
    "worse_than_admitted_severity",
    "beyond_limit",
]
"""Why one reviewed result is not in `ScreeningResult.items`, as a closed set."""

EXCLUSION_PRECEDENCE: Final[tuple[ScreeningExclusionReason, ...]] = (
    "below_min_confidence",
    "direction_not_requested",
    "final_action_not_requested",
    "over_max_risk_flags",
    "worse_than_admitted_severity",
    "beyond_limit",
)
"""The order the criteria are applied in, so a result failing two of them gets one stated reason.

**Read by `ResearchScreener._rejection`, not merely documented by it.** A declared order beside
a hand-written `if`-chain is two spellings of one rule that can disagree -- the shape
`domain/run_mode.py` and `V2-P4-003` exist to stop -- so the chain iterates this tuple and the
tuple is the only place the precedence is written.

`beyond_limit` is last by construction and is the one member `_rejection` never returns: it is
the only reason that cannot be decided about one result on its own. `PER_RESULT_EXCLUSION
_REASONS` is the rest, and `test_each_per_result_exclusion_reason_is_reachable_on_its_own`
provokes every one of them alone.
"""

PER_RESULT_EXCLUSION_REASONS: Final[tuple[ScreeningExclusionReason, ...]] = EXCLUSION_PRECEDENCE[
    :-1
]
"""Every reason decidable from one result and the criteria -- `EXCLUSION_PRECEDENCE` minus
`beyond_limit`. Sliced rather than restated, for the reason that tuple's docstring gives."""


@dataclass(frozen=True, slots=True, kw_only=True)
class ScreeningLimitation:
    """One named boundary on what a governed screen can be trusted to mean."""

    code: str
    detail: str


KNOWN_SCREENING_LIMITATIONS: Final[tuple[ScreeningLimitation, ...]] = (
    ScreeningLimitation(
        code="a_severity_orders_a_list_and_changes_no_gate_decision_anywhere",
        detail=(
            "This screen re-orders and optionally cuts a list of already-completed runs. It "
            "writes nothing: DecisionLedger.risk_decision is whatever the run recorded, "
            "RiskGate.evaluate returns the same verdict it always did, and a candidate this "
            "screen puts last is still a run whose ledger may say final_action=watch. So a name "
            "demoted here is demoted in the reading and not in the record, and a reader who "
            "took the ordering for an enforcement would be reading a presentation. D17's line "
            "one plane up is the same one -- ranking answers what is worth reviewing -- and "
            "V2-P4-005's contract states it from the other side by creating no order. What is "
            "NOT claimed is that the ledger is wrong: the two answer different questions, and "
            "ScreeningItem carries gate_decision and committee_decision precisely so a reader "
            "can see both without inferring either."
        ),
    ),
    ScreeningLimitation(
        code="the_two_shipped_gates_disagree_and_this_screen_ranks_them_rather_than_reconciling",
        detail=(
            "V2-P4-030 removed the disagreement about WORDS and kept the one about DECISIONS. "
            "Both gates now derive their sets from domain/risk_flag.py, so no flag is named by "
            "one and unknown to the other -- what remains is that the two act differently on "
            "the severe band, RiskGate reducing where the committee blocks. SEVERITY_ORDER puts "
            "RiskGate's block above the committee's on the argument that RiskGate is the "
            "runtime gate that stops a decision while the committee is optional by design "
            "(S41). That is still a precedence this module DECLARES rather than a "
            "reconciliation: nothing here derives one gate's verdict from the other's, and a "
            "caller who reads blocked as strictly worse than severe is reading this tuple and "
            "not a measurement. What is NOT claimed is that either gate is right, or that "
            "blocked really is worse than severe for every caller -- only that a list needs one "
            "order and this is the one, written down rather than implied."
        ),
    ),
    ScreeningLimitation(
        code="a_recovery_row_carrying_a_caller_injected_flag_is_refused_rather_than_migrated",
        detail=(
            "V2-P4-030 narrowed SignalFrame.risk_flags to a closed enum, which replaces the "
            "entry this slot used to hold (an_unrecognised_flag_and_a_misspelling_of_a_named"
            "_one_are_the_same_rung: a payload writing future-data instead of future_data was "
            "demoted from blocked to unrecognised and moved UP this screen). What the narrowing "
            "leaves behind is a read path. run_recovery.payload is the only place in this "
            "database a whole SignalFrame is stored, SQLiteRecoveryStore.get re-validates it "
            "through the model, and EvidenceSnapshot.payload is an unschema'd JsonValue that "
            "POST /api/v1/research/run accepts from a request body -- so a row written before "
            "this commit by a caller who put an undeclared string in quality_flags now fails to "
            "load, and the run it belonged to cannot resume. No migration pass refuses it by "
            "name, unlike the horizon narrowing's UnmigratableHorizonError. The difference is "
            "deliberate: 3m was a value the contract genuinely accepted and no constant could "
            "convert, whereas every flag this build ever WROTE is declared, so nothing "
            "openalpha itself produced is outlawed and there is no rewrite to perform. The cost "
            "of being wrong about that is one unreadable operational row whose documented "
            "remedy is already deletion, against a schema-version bump on every database. If it "
            "ever bites, the fix has a template: a _refuse_undeclared_stored_risk_flags pass "
            "beside _refuse_uncountable_stored_horizons in storage/migrations.py, reading the "
            "payload as raw JSON and naming the offending runs."
        ),
    ),
    ScreeningLimitation(
        code="a_severity_is_declared_on_the_flag_and_is_not_a_measurement_of_either_gate",
        detail=(
            "This slot used to hold the_committee_is_read_through_a_probe_because_it_refuses_an"
            "_abstaining_signal -- governance.assess could not call DeliberationCommittee on "
            "the signal it was given, because review recomputed direction into "
            "{bullish, bearish, neutral}, never reproduced abstain, and died on an abstaining "
            "frame's empty evidence_ids. V2-P4-029 fixed that and the probe is gone. What "
            "replaces the entry is what the repair exposed. Severity is now DECLARED on "
            "domain/risk_flag.py::RiskFlag rather than inferred from what a gate answers, so "
            "flag_severity is a statement about the vocabulary and not a measurement of this "
            "build's behaviour. A gate that stopped honouring its own severity band would not "
            "move any severity here; only tests/unit/domain/test_risk_flag.py::"
            "test_both_gates_answer_about_every_declared_flag_and_agree_with_its_severity "
            "would notice. That "
            "is the trade V2-P4-036 chose over a registry of gates: a declaration a reader can "
            "check beats a behaviour a reader has to run, but it is a declaration, and "
            "GovernanceVerdict carries gate_decision and committee_decision beside the severity "
            "precisely so the two can be compared without being conflated."
        ),
    ),
    ScreeningLimitation(
        code="a_flag_count_is_kept_as_a_filter_and_is_not_a_governance_reading",
        detail=(
            "ScreeningCriteria.max_risk_flags survives this issue because it is a field of a "
            "shipped request body (POST /api/v1/screen) and a cap on how many flags a name "
            "carries is a real thing to ask for. It is not a severity and cannot be made into "
            "one: two candidates carrying one flag each have the same count whichever flags "
            "those are, so no value of max_risk_flags admits the benign one and rejects the "
            "severe one. worst_severity_admitted is the field that separates them, and "
            "test_a_flag_count_cannot_separate_the_severe_from_the_benign_and_a_severity_can "
            "drives both on the same pair. A caller who reaches for the count expecting "
            "governance gets a filter that cannot see which flag it is filtering."
        ),
    ),
    ScreeningLimitation(
        code="the_default_screen_admits_every_rung_and_only_reorders",
        detail=(
            "worst_severity_admitted defaults to blocked -- the worst rung there is -- so the "
            "shipped default drops nothing and V2-P4-006 changes an order rather than a result "
            "set. That is deliberate: POST /api/v1/screen is a shipped face and a default that "
            "silently started rejecting names would have changed what the endpoint means with "
            "no schema saying so. The cost is that a caller who never sets the field gets a "
            "list whose worst names are merely LAST, not absent, and a caller who paginates or "
            "reads only the head of it will not see a blocked name at all. Naming the cut is "
            "therefore the caller's decision, and this entry is where that is disclosed rather "
            "than left to be discovered from a default value."
        ),
    ),
    ScreeningLimitation(
        code="the_unrecognised_rung_is_kept_for_the_wire_and_no_signal_can_reach_it",
        detail=(
            "This slot used to hold a_flag_severity_is_memoised_per_process_and_a_gate_swapped"
            "_at_runtime_is_not_seen. Both halves of that entry are gone: flag_severity no "
            "longer derives its answer by running the gates, so there is nothing to memoise, "
            "and the lru_cache(maxsize=512) it was bounded by -- bounded because flag strings "
            "arrived in request bodies -- is deleted. What replaces it is the rung that "
            "measurement lived on. unrecognised meant 'no shipped gate names this string', and "
            "V2-P4-030 closed the vocabulary at SignalFrame, so assess can never return it: a "
            "signal cannot carry an undeclared flag. The rung is NOT removed, because "
            "worst_severity_admitted is a field of a shipped request body and dropping a value "
            "a caller may already be sending would break POST /api/v1/screen in order to record "
            "a fact. So the ladder has five rungs of which a signal reaches four, "
            "worst_severity_admitted='unrecognised' behaves exactly as "
            "worst_severity_admitted='clear' would for any signal this build can construct, and "
            "a caller who sets it expecting to admit 'flags nobody recognises' is naming a set "
            "that is now empty by construction rather than by accident."
        ),
    ),
)
"""What a governed screen does not answer, as a closed registry rather than as prose.

The twenty-third `KNOWN_*` registry. Every entry is bound to the suite by
`tests/unit/test_known_limitation_registries.py`, which requires each `code` to appear as a
string literal in *executable* test code -- the P2 review measured that a code named only in
docstrings can be renamed with the whole suite staying green.

Not folded into `KNOWN_PANEL_LIMITATIONS`, for the reason the eight dataset registries are and
this one is not: `panel_doctor` folds a registry when it bounds a *fetched* dataset, and this
one bounds a reading of completed runs -- no upstream, no `DATASET_CADENCE` entry, and nothing
for a freshness report to be fresh against.
"""

SCREENING_LIMITATION_CODES: Final[frozenset[str]] = frozenset(
    limitation.code for limitation in KNOWN_SCREENING_LIMITATIONS
)


class ScreeningCriteria(BaseModel):
    """What a caller asks a screen for. Every field is a filter; none of them is the order."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    min_confidence: float = Field(default=0, ge=0, le=1)
    directions: tuple[Literal["bullish", "bearish", "neutral", "abstain"], ...] = ()
    final_actions: tuple[Literal["watch", "avoid", "abstain"], ...] = ()
    max_risk_flags: int | None = Field(default=None, ge=0)
    """A cap on how *many* flags a candidate may carry. Kept, and not a governance reading.

    See this module's docstring: a count cannot tell `future_data` from a cosmetic flag, which
    is the whole reason `worst_severity_admitted` exists beside it. Left at its shipped default
    of `None` (no cap) so that adding governance changed no existing caller's result set.
    """
    worst_severity_admitted: GovernanceSeverity = "blocked"
    """The worst `product/governance.py` rung a candidate may sit on and still be an item.

    Defaults to `blocked`, the worst rung there is, so the default screen **admits everything
    and merely reorders it**. That is deliberate: `V2-P4-006` is an ordering change, and a
    filter that silently started dropping names would have made the shipped
    `POST /api/v1/screen` body mean something new without its schema saying so. A caller that
    wants the cut asks for it by name.
    """
    limit: int = Field(default=100, ge=1, le=1000)


class ScreeningItem(BaseModel):
    """One candidate that survived the criteria, with the governance reading that placed it."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    subject: str
    run_id: str
    signal_id: str
    decision_id: str
    direction: str
    final_action: str
    confidence: float
    strength: float
    risk_flags: tuple[str, ...]
    severity: GovernanceSeverity
    driving_flags: tuple[str, ...]
    """Which of `risk_flags` put this candidate on `severity` -- the answer a count cannot give."""
    gate_decision: RiskDecision
    committee_decision: RiskDecision


class ScreeningExclusion(BaseModel):
    """One reviewed candidate that is not an item, and the single reason it is not."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    subject: str
    run_id: str
    reason: ScreeningExclusionReason
    severity: GovernanceSeverity
    driving_flags: tuple[str, ...]
    """Carried on every exclusion, including ones decided on confidence, so that a reader of a
    rejected name learns what its flags were worth without re-screening it."""


class ScreeningResult(BaseModel):
    """A governed screen: the ranked survivors, the rejected, and what was asked for."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    criteria: ScreeningCriteria
    items: tuple[ScreeningItem, ...]
    excluded: tuple[ScreeningExclusion, ...] = ()
    reviewed: int


class ResearchScreener:
    """Rank verified research results by governance first and by confidence within it."""

    def screen(
        self,
        *,
        results: tuple[ResearchRunResult, ...],
        criteria: ScreeningCriteria,
    ) -> ScreeningResult:
        """Return the ranked items and, for every other reviewed result, why it is not one."""
        admitted = SEVERITY_RANK[criteria.worst_severity_admitted]
        items: list[ScreeningItem] = []
        excluded: list[ScreeningExclusion] = []
        for result in results:
            verdict = assess(result.signal)
            reason = self._rejection(
                result=result, criteria=criteria, verdict=verdict, admitted=admitted
            )
            if reason is None:
                items.append(self._item(result=result, verdict=verdict))
            else:
                excluded.append(self._exclusion(result=result, verdict=verdict, reason=reason))
        items.sort(
            key=lambda item: (
                SEVERITY_RANK[item.severity],
                -item.confidence,
                -item.strength,
                item.subject,
            )
        )
        kept, cut = items[: criteria.limit], items[criteria.limit :]
        excluded.extend(
            ScreeningExclusion(
                subject=item.subject,
                run_id=item.run_id,
                reason="beyond_limit",
                severity=item.severity,
                driving_flags=item.driving_flags,
            )
            for item in cut
        )
        return ScreeningResult(
            criteria=criteria,
            items=tuple(kept),
            excluded=tuple(excluded),
            reviewed=len(results),
        )

    @staticmethod
    def _rejection(
        *,
        result: ResearchRunResult,
        criteria: ScreeningCriteria,
        verdict: GovernanceVerdict,
        admitted: int,
    ) -> ScreeningExclusionReason | None:
        """The first reason in `EXCLUSION_PRECEDENCE` this result fails, or `None`.

        The loop reads the declared tuple rather than restating its order as a chain of
        `return`s, so the constant is the rule and not a comment about one.
        """
        failed: dict[ScreeningExclusionReason, bool] = {
            "below_min_confidence": result.signal.confidence < criteria.min_confidence,
            "direction_not_requested": bool(criteria.directions)
            and result.signal.direction not in criteria.directions,
            "final_action_not_requested": bool(criteria.final_actions)
            and result.decision.final_action not in criteria.final_actions,
            "over_max_risk_flags": criteria.max_risk_flags is not None
            and len(result.signal.risk_flags) > criteria.max_risk_flags,
            "worse_than_admitted_severity": SEVERITY_RANK[verdict.severity] > admitted,
        }
        for reason in PER_RESULT_EXCLUSION_REASONS:
            if failed[reason]:
                return reason
        return None

    @staticmethod
    def _item(*, result: ResearchRunResult, verdict: GovernanceVerdict) -> ScreeningItem:
        return ScreeningItem(
            subject=result.signal.subject,
            run_id=result.manifest.run_id,
            signal_id=result.signal.signal_id,
            decision_id=result.decision.decision_id,
            direction=result.signal.direction,
            final_action=result.decision.final_action,
            confidence=result.signal.confidence,
            strength=result.signal.strength,
            risk_flags=result.signal.risk_flags,
            severity=verdict.severity,
            driving_flags=verdict.driving_flags,
            gate_decision=verdict.gate_decision,
            committee_decision=verdict.committee_decision,
        )

    @staticmethod
    def _exclusion(
        *,
        result: ResearchRunResult,
        verdict: GovernanceVerdict,
        reason: ScreeningExclusionReason,
    ) -> ScreeningExclusion:
        return ScreeningExclusion(
            subject=result.signal.subject,
            run_id=result.manifest.run_id,
            reason=reason,
            severity=verdict.severity,
            driving_flags=verdict.driving_flags,
        )
