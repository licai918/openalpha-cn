"""`V2-P4-007`: what changed between two published shortlists (S44, S49).

`openalpha shortlist get`'s docstring names the workflow this module finishes -- *"run it, run it
again tomorrow, and compare the two"* -- and until now the comparing was done by hand.
`tests/integration/test_shortlist_workflow.py` step 4 does it with a `set` difference written
into the test, which is the whole of what a caller had.

## Why both addresses are arguments, and neither is "the last one"

The row is titled *vs 上次运行* -- against the previous run -- and this deployment cannot say
which run was previous. That is measured, not assumed:
`shortlist_view.KNOWN_SHORTLIST_VIEW_LIMITATIONS.the_stored_answer_is_addressed_by_content_and_
not_by_when_it_was_run` records that `shortlist_id` is `stable_answer_digest` over the answer, so
the store holds *the set of distinct answers a deployment has produced* and carries no clock: a
wall clock in the key would mint a new document for every repetition of one answer, which is
`FactorInputRef`'s own defect read backwards. `ShortlistDocumentStore.list_ids` is ascending by
address, which is ascending by sha256 and therefore by nothing.

So "previous" is the caller's knowledge and not the store's. Both addresses are named, the first
is the baseline, and the rendered body says which is which -- see
`KNOWN_COMPARISON_LIMITATIONS.the_store_cannot_say_which_answer_came_first`.

## What is compared, and the two planes it takes to answer the row

The row asks for three things -- 新增 / 移除 / 理由变化 -- and one plane of a rendered answer
cannot give all three. `funnel.shortlist` carries `(subject, rank, score)` for every name the
screen cut and is present on every answered run; `admitted` carries `direction`, `confidence`,
`risk_flags` and `run_manifest_id` and is `null` on a refused list and `[]` on a run with no
evidence. Entry and exit are read off the first; a changed *reason* can only come from the
second. Both are read, and each entry says which of the two it had on each side.

## The sign convention, stated once because it is the thing a reader will get wrong

`rank_change` and `score_change` both point the same way: **positive means the name moved up**.
`rank_change` is `baseline - current` (rank 5 to rank 2 is `+3`) and `score_change` is
`current - baseline` (a score that rose is positive). The two subtractions are written in
opposite orders precisely so the two readings are not, and
`test_a_name_that_stayed_carries_its_rank_change_and_the_sign_says_which_way` asserts each
against the pair it was derived from rather than against a literal.

## Why this is derived and never stored

A comparison is a reading of two documents that already have addresses; giving it a third would
mean a document whose content depends on two others, and whose address would have to move when
either did. `V2-P4-062`'s store holds answers; this holds nothing.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Final

from openalpha_cn.shortlist_view import (
    SHORTLIST_VIEW_SCHEMA_VERSION,
    ShortlistDocumentStore,
    ShortlistRequestError,
    held_shortlist,
)

SHORTLIST_COMPARISON_SCHEMA_VERSION: Final[str] = "shortlist-comparison/v1"
"""The shape three faces agree to hand this out in.

A version of its own beside `SHORTLIST_VIEW_SCHEMA_VERSION` and
`SHORTLIST_DOCUMENT_SCHEMA_VERSION`, which is this repository's arrangement wherever a second
shape wraps a first: the answer's shape is what a run produced, and this is what a reading of two
of them looks like. The two can move independently and a shared constant would say they cannot.
"""

REASON_CHANGES: Final[frozenset[str]] = frozenset(
    {"admission", "direction", "risk_flags", "backing_run"}
)
"""Which change codes count as 理由变化 -- a changed *reason* rather than a changed position.

Four discrete facts, and the exclusions are the argument. `rank` and `score` are left out because
a ranking moves whenever the panel does: every name's score changes on every session, so a
`reason_changed` that counted them would equal `held` on every real comparison and separate
nothing. `confidence` is left out for a sharper reason -- it is a continuous number, so counting
it would need a tolerance, and a tolerance is a parameter this repository has not measured. The
four here either changed or did not: a direction reversed, a risk flag appeared, the backing run
moved, or a name stopped being published at all.

`confidence` and `score` movement are still *reported* on every entry; they are simply not what
makes the summary's `reason_changed` count. See
`KNOWN_COMPARISON_LIMITATIONS.a_score_is_compared_by_equality_and_never_by_a_tolerance`.
"""


@dataclass(frozen=True, slots=True)
class ComparisonLimitation:
    """One named boundary on what a comparison of two published answers can be trusted to mean."""

    code: str
    detail: str


KNOWN_COMPARISON_LIMITATIONS: Final[tuple[ComparisonLimitation, ...]] = (
    ComparisonLimitation(
        code="the_store_cannot_say_which_answer_came_first",
        detail=(
            "KNOWN_COMPARISON_ORDER: `shortlist_id` is a content address and "
            "`ShortlistDocumentStore.list_ids` returns it ascending, which is ascending by "
            "sha256. Nothing in the store records when a document was written, by design -- "
            "`the_stored_answer_is_addressed_by_content_and_not_by_when_it_was_run` gives the "
            "reason. So this module cannot implement 'compare with the previous run' and does "
            "not pretend to: it compares two answers the caller named, in the order the caller "
            "named them, and the body repeats both addresses so a reader can check the "
            "direction. A caller who wants a run log wants the `RunManifest` plane."
        ),
    ),
    ComparisonLimitation(
        code="a_rendered_answer_does_not_carry_the_cut_so_two_sizes_compare_as_churn",
        detail=(
            "KNOWN_COMPARISON_CUT: `declaration` carries the tier, transform, neutralisation, "
            "exchange, years and components -- the whole of what decides the *ordering* -- and "
            "carries none of `--shortlist-size`, `--position-capital` or the three gate bars. "
            "So two answers to one question cut at three names and at ten are comparable by "
            "this module's own rule and will report seven names 'added' that were always there "
            "and merely below the old cut. `measurement.shortlist_count` is on both sides of "
            "the rendered body and a reader can see the two counts differ; nothing refuses it, "
            "because the size that was *asked for* is not in the answer and the size that "
            "*resulted* is a legitimate difference between two runs of one declaration. "
            "Recording the requested cut on the answer is `shortlist_view`'s change to make."
        ),
    ),
    ComparisonLimitation(
        code="two_answers_to_one_question_may_have_been_read_off_two_different_panels",
        detail=(
            "KNOWN_COMPARISON_PANEL: `declaration` is the resolved question and "
            "`the_two_versions_do_not_address_the_stored_values_they_were_read_from` is the "
            "same boundary one plane over. Two runs of one declaration at one `as_of` against a "
            "panel that was rebuilt in between produce two different answers, and this "
            "comparison reports the difference as movement without being able to say whether "
            "the market moved or the store did. `cross_section.as_of` is carried on both sides "
            "of the body, which separates the ordinary case -- two sessions -- from the "
            "surprising one, and is as far as the rendered answer can take a reader."
        ),
    ),
    ComparisonLimitation(
        code="a_score_is_compared_by_equality_and_never_by_a_tolerance",
        detail=(
            "KNOWN_COMPARISON_TOLERANCE: `score` and `confidence` are compared with `!=`, so a "
            "difference in the last place of a float is a reported change. That is deliberate "
            "and it is why neither is in `REASON_CHANGES`: a tolerance is a parameter, this "
            "repository declares no default for one anywhere on this plane, and inventing one "
            "here would make 'unchanged' mean whatever the constant happened to be. What the "
            "summary counts instead are the four discrete facts in `REASON_CHANGES`. A caller "
            "who wants a tolerance has both numbers on every entry."
        ),
    ),
    ComparisonLimitation(
        code="a_refused_answer_is_compared_rather_than_refused_and_the_block_is_reported",
        detail=(
            "KNOWN_COMPARISON_BLOCKED: `run_shortlist` stores every answer it renders, refused "
            "ones included, so a blocked answer is retrievable and comparable. Its `admitted` "
            "is `null` rather than `[]`, which `shortlist_view` calls the distinction the whole "
            "issue turns on, and this module keeps it: every entry on that side reports "
            "`admitted: false`, and a name published on the other side gets the `admission` "
            "change code. What it cannot do is tell a reader *why* from inside the comparison "
            "-- `is_blocked` and the `blocks` codes are carried on each side of the header for "
            "that, and the remedy lives on the answer rather than here."
        ),
    ),
    ComparisonLimitation(
        code="an_added_or_removed_name_carries_no_change_codes_only_a_status",
        detail=(
            "KNOWN_COMPARISON_STATUS: `changes` is computed for `held` entries only. A name "
            "that entered has no baseline to have moved from, so listing `rank`, `score`, "
            "`direction` and the rest for it would be listing the fact that it is new, five "
            "more times, in a field a caller filters on. `status` says it once. The "
            "consequence, stated rather than discovered: `summary.reason_changed` counts held "
            "names only, so a day on which the whole list turned over reports "
            "`reason_changed: 0` beside a large `added` and `removed`."
        ),
    ),
    ComparisonLimitation(
        code="a_comparison_is_derived_and_has_no_address_of_its_own",
        detail=(
            "KNOWN_COMPARISON_ADDRESS: nothing stores this and nothing addresses it. A "
            "content address over a reading of two addressed documents would have to move when "
            "either moved, and would be a third identity for a fact that is already fully "
            "determined by two -- `RUN_MANIFEST_UNADDRESSED_FIELDS`' reasoning applied to a "
            "whole document. The two `shortlist_id`s in the body are the address of this "
            "comparison; re-running it costs one store read per side and is free of a clock."
        ),
    ),
)
"""What a comparison of two published shortlists does not promise (`V2-P4-007`)."""

COMPARISON_LIMITATION_CODES: Final[frozenset[str]] = frozenset(
    limitation.code for limitation in KNOWN_COMPARISON_LIMITATIONS
)


def compare_held_shortlists(
    shortlists: ShortlistDocumentStore, *, baseline_id: str, current_id: str
) -> dict[str, object]:
    """Two held answers, reopened by address and compared (`V2-P4-007`).

    The read half, shared by `openalpha shortlist compare` and `OpenAlphaSDK.compare_shortlists`
    so the two cannot come to serve two shapes -- `shortlist_view`'s own argument for existing at
    all. Both addresses go through `held_shortlist`, so a malformed one is `bad_request` before
    the store is touched, an unheld one is `not_held`, and a document whose answer no longer
    hashes to its key does not open. Both sides are checked, and the baseline first, so a caller
    who typed one address wrong is told which.
    """
    baseline = held_shortlist(shortlists, baseline_id)
    current = held_shortlist(shortlists, current_id)
    return compare_shortlist_answers(baseline=baseline, current=current)


def compare_shortlist_answers(
    *, baseline: Mapping[str, Any], current: Mapping[str, Any]
) -> dict[str, object]:
    """One comparison of two rendered answers to the same question.

    Refuses two answers to *different* questions before comparing anything. A screen of one
    factor against a screen of another shares no name, so the arithmetic would report every name
    removed and every name added -- true about two lists and false about one market -- and a
    caller reading it would conclude the market turned over. `_refuse_two_questions` names the
    keys that differ rather than saying the two are incomparable, because the remedy is to pick a
    different pair and only the differing key says which one.
    """
    _refuse_two_questions(baseline=baseline, current=current)
    baseline_ranked = _ranked(baseline)
    current_ranked = _ranked(current)
    baseline_admitted = _admitted(baseline)
    current_admitted = _admitted(current)

    subjects = sorted(
        set(baseline_ranked) | set(current_ranked) | set(baseline_admitted) | set(current_admitted)
    )
    entries = [
        _entry(
            subject,
            baseline=_side(subject, ranked=baseline_ranked, admitted=baseline_admitted),
            current=_side(subject, ranked=current_ranked, admitted=current_admitted),
        )
        for subject in subjects
    ]
    added = [entry["subject"] for entry in entries if entry["status"] == "added"]
    removed = [entry["subject"] for entry in entries if entry["status"] == "removed"]
    held = [entry["subject"] for entry in entries if entry["status"] == "held"]
    return {
        "schema_version": SHORTLIST_COMPARISON_SCHEMA_VERSION,
        "baseline": _header(baseline),
        "current": _header(current),
        "declaration": dict(baseline["declaration"]),
        "horizon": baseline["horizon"],
        "added": added,
        "removed": removed,
        "held": held,
        "entries": entries,
        "summary": {
            "added": len(added),
            "removed": len(removed),
            "held": len(held),
            "rank_changed": sum(1 for entry in entries if "rank" in entry["changes"]),
            "reason_changed": sum(1 for entry in entries if REASON_CHANGES & set(entry["changes"])),
        },
    }


def shortlist_comparison_rows(
    comparison: Mapping[str, Any],
) -> tuple[tuple[str, str, str, str], ...]:
    """The comparison as `(status, subject, rank, changed)` rows, for the one face a human reads.

    `openalpha shortlist run` carries both a `--json` body and a terminal rendering for
    `shortlist_view`'s stated reason, and a comparison whose only face is a JSON object is one a
    scheduled job can consume and a person cannot. `rank` reads `5 -> 2 (+3)` for a name that
    moved and the single rank for one that entered or left, so the column means the same thing on
    every row without a reader having to know which status they are looking at.
    """
    rows: list[tuple[str, str, str, str]] = []
    for entry in comparison["entries"]:
        before = entry["baseline"]["rank"]
        after = entry["current"]["rank"]
        if before is not None and after is not None:
            rank = f"{before} -> {after} ({entry['rank_change']:+d})"
        else:
            rank = str(before if after is None else after)
        rows.append(
            (
                str(entry["status"]),
                str(entry["subject"]),
                rank,
                ", ".join(entry["changes"]) or "-",
            )
        )
    return tuple(rows)


COMPARABLE_KEYS: Final[tuple[str, ...]] = ("horizon", "declaration")
"""Which keys two answers must agree on before their candidate lists mean anything together.

`declaration` is `V2-P4-050`'s whole resolved question -- tier, transform, neutralisation,
exchange, years and the weighted components -- and it is compared as one object rather than key
by key so that a key *added* to it by a later row is compared without anybody remembering to add
it here. `horizon` is beside it because it is rendered at the top level rather than inside
`declaration`, and two lists over different horizons are two claims about different futures.
**`schema_version` was here and was removed rather than asserted** (mutation sweep). It was
dead: `_refuse_two_questions` checks each answer's shape against this build's own
`SHORTLIST_VIEW_SCHEMA_VERSION` *before* comparing the two with each other, so two answers that
disagreed about their shape were already refused by name and the entry here could never fire. A
sweep mutating it killed nothing, which is what a redundant check looks like from the outside --
and two checks of one thing is one check plus a place a later reader can satisfy instead of the
real one.

`as_of` is deliberately absent: it is the thing that is expected to differ, and requiring it to
differ would refuse the legitimate case of two runs at one instant against a panel that moved.
"""


_ABSENT: Final[object] = object()
"""What `.get` returns for a `declaration` key one side does not carry at all.

`None` cannot be the default and the reason was measured rather than reasoned about:
`declaration.neutralization` is rendered as `null` on **every** answer this build produces --
`run_shortlist` refuses `tier="neutralized"` by name, so nothing else is reachable -- so a
`.get(key)` comparing an *older* answer that predates the key against a current one carrying
`null` found them equal, dropped the key from `differing`, and the refusal that fired said the
two questions differ "on `[]`". A refusal that names nothing is the failure mode this whole
message exists to avoid, and it arrived by way of a mutation sweep that flipped the union below
to an intersection: the fixtures all gave both sides the same key set, so nothing separated
them. `test_a_declaration_key_present_on_only_one_side_is_named_too` is that case.

With this sentinel there is no reachable way for `differing` to come back empty here: it is only
entered when the two `declaration` objects are unequal, and two unequal mappings differ on at
least one key under a comparison that can see absence. So there is no fallback branch below, and
that is a claim rather than an omission.
"""


def _refuse_two_questions(*, baseline: Mapping[str, Any], current: Mapping[str, Any]) -> None:
    """Refuse two answers whose questions differ, naming every key that does.

    The shape is checked against this build's own `SHORTLIST_VIEW_SCHEMA_VERSION` before the two
    are checked against each other, and the order matters: two answers rendered by a *newer*
    build agree with each other and disagree with every key name read below, so an equality
    between them would let a comparison walk a body whose fields this module does not know.
    """
    for role, answer in (("baseline", baseline), ("current", current)):
        if answer.get("schema_version") != SHORTLIST_VIEW_SCHEMA_VERSION:
            raise ShortlistRequestError(
                f"the {role} answer is {answer.get('schema_version')!r} and this build compares "
                f"{SHORTLIST_VIEW_SCHEMA_VERSION!r}; a comparison reads named fields off both "
                "bodies, so a shape it does not know is refused rather than walked"
            )
    differing = tuple(key for key in COMPARABLE_KEYS if baseline.get(key) != current.get(key))
    if not differing:
        return
    if "declaration" in differing:
        declared = tuple(
            key
            for key in sorted(set(baseline["declaration"]) | set(current["declaration"]))
            if baseline["declaration"].get(key, _ABSENT) != current["declaration"].get(key, _ABSENT)
        )
        differing = (*(key for key in differing if key != "declaration"), *declared)
    raise ShortlistRequestError(
        "two shortlists can only be compared when they answer the same question, and these "
        f"differ on {list(differing)}; a comparison across two questions reports every name as "
        "added and every name as removed, which is arithmetic about two lists rather than an "
        "answer about one market. `openalpha shortlist list` prints every address this runtime "
        "directory holds"
    )


def _ranked(answer: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    """The screened list by subject: every name the cut kept, with its rank and score."""
    return {entry["subject"]: entry for entry in answer["funnel"]["shortlist"]}


def _admitted(answer: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    """The published candidates by subject, which is empty for a refused list.

    `answer["admitted"]` is `null` on a refused answer and a list -- possibly empty -- on an
    admitted one, and `shortlist_view` calls that the distinction the whole issue turns on. Both
    collapse to "no published candidate for this name" *here*, and the distinction is kept where
    it belongs: `is_blocked` on each side of the header says which of the two happened.
    """
    published = answer["admitted"]
    if published is None:
        return {}
    return {entry["subject"]: entry for entry in published}


def _side(
    subject: str,
    *,
    ranked: Mapping[str, Mapping[str, Any]],
    admitted: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    """One answer's reading of one name, with `None` for every fact it does not carry."""
    screen = ranked.get(subject)
    candidate = admitted.get(subject)
    return {
        "shortlisted": screen is not None,
        "admitted": candidate is not None,
        "rank": None if screen is None else screen["rank"],
        "score": None if screen is None else screen["score"],
        "direction": None if candidate is None else candidate["direction"],
        "confidence": None if candidate is None else candidate["confidence"],
        "risk_flags": None if candidate is None else list(candidate["risk_flags"]),
        "run_manifest_id": None if candidate is None else candidate["run_manifest_id"],
    }


def _entry(
    subject: str, *, baseline: Mapping[str, Any], current: Mapping[str, Any]
) -> dict[str, Any]:
    """One name's whole story across the two answers."""
    status = _status(baseline=baseline, current=current)
    rank_change = (
        None
        if baseline["rank"] is None or current["rank"] is None
        else baseline["rank"] - current["rank"]
    )
    score_change = (
        None
        if baseline["score"] is None or current["score"] is None
        else current["score"] - baseline["score"]
    )
    return {
        "subject": subject,
        "status": status,
        "baseline": dict(baseline),
        "current": dict(current),
        "rank_change": rank_change,
        "score_change": score_change,
        "changes": _changes(baseline=baseline, current=current) if status == "held" else [],
    }


def _status(*, baseline: Mapping[str, Any], current: Mapping[str, Any]) -> str:
    """`added`, `removed` or `held`, decided on the screened list rather than the published one.

    On the screen and not on `admitted`, because a name that was shortlisted on both days and
    published on neither has not entered or left anything -- the gate refused to publish it, and
    that is what the `admission` change code is for. Deciding this on `admitted` would report a
    refused list as a market in which every name disappeared.
    """
    if not baseline["shortlisted"]:
        return "added"
    if not current["shortlisted"]:
        return "removed"
    return "held"


def _changes(*, baseline: Mapping[str, Any], current: Mapping[str, Any]) -> list[str]:
    """Every way one held name differs between the two answers, in a stable order.

    Sorted so two callers comparing two comparisons see one order, and `!=` throughout: see
    `a_score_is_compared_by_equality_and_never_by_a_tolerance` for why no tolerance is applied
    to the two continuous fields.
    """
    moved: set[str] = set()
    if baseline["rank"] != current["rank"]:
        moved.add("rank")
    if baseline["score"] != current["score"]:
        moved.add("score")
    if baseline["admitted"] != current["admitted"]:
        moved.add("admission")
    if baseline["admitted"] and current["admitted"]:
        for field, code in (
            ("direction", "direction"),
            ("confidence", "confidence"),
            ("risk_flags", "risk_flags"),
            ("run_manifest_id", "backing_run"),
        ):
            if baseline[field] != current[field]:
                moved.add(code)
    return sorted(moved)


def _header(answer: Mapping[str, Any]) -> dict[str, Any]:
    """Which answer this side is, and the two facts that decide how to read its candidates.

    `is_blocked` and `blocks` travel because a side whose `admitted` is `null` is a refusal and
    not an empty market, and a reader who sees every name marked `admitted: false` needs the
    refusal's own codes to know why. `cross_section.as_of` travels beside the request's `as_of`
    because they are two different instants -- the request's is what was asked about and the
    cross section's is the newest stored one at or before it -- and
    `two_answers_to_one_question_may_have_been_read_off_two_different_panels` is read off
    exactly that pair.
    """
    return {
        "shortlist_id": answer["shortlist_id"],
        "as_of": answer["as_of"],
        "cross_section_as_of": answer["cross_section"]["as_of"],
        "is_blocked": answer["is_blocked"],
        "blocks": [block["code"] for block in answer["blocks"]],
        "shortlist_count": answer["measurement"]["shortlist_count"],
        "candidate_count": answer["measurement"]["candidate_count"],
    }
