"""The agent family's three frozen forward rules (`V2-P7-013`, plan C Task 2.3).

Plan C registers an `agent_*` prediction family over Step 0's survivor census. Everything that
decides *what the family's records mean* has to be fixed before the first forward session and
has to be checkable from the stored protocol alone, so the decisions that are arithmetic are
here rather than in the evening command that files the records:

## `primary_sample` -- the registered draw, recomputable by a reviewer

The primary scoring set is a fixed-size subset of the day's survivors, ordered by
`sha256(f"{salt}|{session.isoformat()}|{ts_code}")` and truncated to `size`. That is the whole
rule. It has three properties a forward claim needs and a random draw does not give for free:

- **Reproducible without the code path.** The salt is in the registration, the session is the
  session, and the ordered set is the day's survivors -- so a reviewer can recompute yesterday's
  sample from the published protocol and Step 0's stored record, without trusting this run.
- **Stable under a re-run.** Same chip, same day, same set: the same names. A command that
  crashed at 21:00 and was re-run at 22:00 must not have scored a different sample.
- **Independent of the book.** The function takes no holdings and no watch list. Step 0's paper
  book must not be able to pull a name into, or out of, the primary set: that coupling is the
  one a critic would call post-hoc selection, and the defence is that the input is not there.

At or above the set's size the draw is the census -- the same ordering, truncated by the set's
own length. Not a special case and not an error: `V2-P7-013`'s registered `size` may legitimately
exceed a small day's census, and then every survivor is sampled.

## `coverage_set` -- a second, exploratory set, never a widening of the primary one

The coverage set is the survivor names Step 0's paper book and watch list happen to hold. It is
reported **separately** and labelled exploratory: it is a filter, the primary set is a draw, and
the two are never pooled. Pooling would let a name that was never sampled into the primary
statistic, and the draw would then depend on the book after all. The separation is structural --
`coverage_set` takes no salt and `primary_sample` takes no holdings -- and the evaluator is where
the two are reported apart (`strategy_agent_evaluation.py`, plan C Task 2.7).

## `score_of` -- an abstention is a code, never a zero

The engine's aggregated `SignalFrame` becomes the stored contract `Prediction` speaks: a signed
number, or `None` plus an abstention code. `0.0` is not a neutral opinion here -- a batch of
zeros is indistinguishable from a model that was never asked (`domain/alpha_model.py`'s
`Prediction` docstring makes the same argument on the label side). The codes are C's own closed
vocabulary: `agent_not_routed` (the router dropped every agent for want of evidence),
`agent_abstained` (agents ran and every one abstained), `model_invalid_output` (the model answered
invalidly after its retry budget). A reason outside that set is refused rather than stored:
`Prediction.abstention` is free text, so an arbitrary sentence would enter a *record* whose
meaning the evaluator then has to guess at.

## The salt, and Step 0's seed

`refuse_a_salt_tied_to_the_book` refuses a salt that is, or contains, Step 0's registered seed.
The test is deliberately over-inclusive: the canonical alphanumeric spelling is compared, and
every digit run in the salt is refused when it contains the seed's digits. A short seed therefore
refuses salts no reviewer would call derived -- that is the conservative direction, and the
practical seeds are date-like integers (`20260926`), where the rule is precise. C's salt is
registered in its own protocol and must be its own value; a salt copied from Step 0's seed would
make the two families' samples correlated by construction, which is the coupling the registration
check exists to prevent.
"""

from __future__ import annotations

import hashlib
import math
import re
from collections.abc import Sequence
from datetime import date
from typing import Final, Literal

AGENT_LLM_FAMILY: Final[str] = "agent_llm_structured/v1"
"""The one primary family (plan C decision D2): the LLM's incremental partial rank IC at h=1."""
AGENT_RULE_FAMILY: Final[str] = "agent_rule_baseline/v1"
"""The deterministic rule family: permanently exploratory, and the one `--no-llm` files."""
AGENT_HORIZON: Final[str] = "5d"
"""One record, read at h=1 and h=5. The stored horizon is the longer window's count."""

ABSTAIN_NOT_ROUTED: Final[str] = "agent_not_routed"
"""No agent was routed for the subject: the engine abstained before any agent ran."""
ABSTAIN_ALL_ABSTAINED: Final[str] = "agent_abstained"
"""Agents ran and every routed one abstained; the aggregate cites no evidence."""
ABSTAIN_INVALID_OUTPUT: Final[str] = "model_invalid_output"
"""The model answered invalidly after its retry budget (`agents.model.ModelProviderFailure`)."""
ABSTENTION_CODES: Final[frozenset[str]] = frozenset(
    {ABSTAIN_NOT_ROUTED, ABSTAIN_ALL_ABSTAINED, ABSTAIN_INVALID_OUTPUT}
)
"""C's closed abstention vocabulary; `score_of` refuses a reason outside it."""

ScoringSet = Literal["primary", "coverage"]
"""The two scoring sets. Filed as two records for one session, never as one pooled record."""

_DIRECTIONS: Final[frozenset[str]] = frozenset({"bullish", "bearish", "neutral", "abstain"})
_SIGN: Final[dict[str, float]] = {"bullish": 1.0, "bearish": -1.0, "neutral": 0.0}


def _refuse_a_blank_salt(salt: str) -> None:
    if not salt.strip():
        raise ValueError(
            "an agent sample salt must not be blank: the draw is sha256(salt|session|ts_code), "
            "so a blank salt is the same draw for every registration and is no draw at all"
        )


def _canonical(text: str) -> str:
    """The alphanumeric projection of `text`, lowercased: `book_seed_20260926` -> `bookseed...`."""
    return "".join(character for character in text.lower() if character.isalnum())


def refuse_a_salt_tied_to_the_book(salt: str, *, book_seed: int) -> None:
    """Refuse a salt that is Step 0's registered seed, or carries it, by name.

    The registration check calls this before C's salt is accepted (plan C Task 3.3). The
    comparison is canonical and digit-run based and deliberately over-inclusive; see the module
    docstring for the direction and why a date-like seed makes it precise.
    """
    _refuse_a_blank_salt(salt)
    seed = str(book_seed)
    canonical_salt = _canonical(salt)
    if canonical_salt == _canonical(seed):
        raise ValueError(
            f"the agent sample salt {salt!r} is Step 0's registered seed ({book_seed}), so the "
            "two families' samples would be correlated by construction; C's salt must be its own "
            "value and is registered in C's own protocol"
        )
    for run in re.findall(r"\d+", salt):
        if seed in run:
            raise ValueError(
                f"the agent sample salt {salt!r} carries Step 0's registered seed ({book_seed}) "
                f"in its digits ({run!r}); a salt derived from that seed draws a correlated "
                "sample, and C's salt must be an independent registered value"
            )


def primary_sample(
    survivors: Sequence[str], *, salt: str, session: date, size: int
) -> tuple[str, ...]:
    """The session's primary scoring set: the first `size` survivors by the registered order.

    `size >= len(survivors)` is the census. Duplicates in `survivors` are one name -- a survivor
    list is a set of securities, and a repeated row must not buy a name two places in the order.
    The output is sorted by `ts_code`, so two runs that agree on the *set* agree on the tuple.
    """
    _refuse_a_blank_salt(salt)
    if size < 1:
        raise ValueError(
            f"the agent sample size is {size}; a registered draw of zero names would file an "
            "empty scoring set, which is indistinguishable from a session that never ran"
        )
    ordered = sorted(set(survivors))
    ranked = sorted(
        ordered,
        key=lambda ts_code: hashlib.sha256(
            f"{salt}|{session.isoformat()}|{ts_code}".encode()
        ).hexdigest(),
    )
    return tuple(sorted(ranked[:size]))


def coverage_set(
    holdings: Sequence[str],
    watch_list: Sequence[str],
    survivors: Sequence[str],
) -> tuple[str, ...]:
    """The survivor names among the book's holdings and the watch list, sorted and deduplicated.

    Never passed to the primary statistic: this set is reported separately and exploratory
    (plan C decision D2). A name outside the day's survivor census is in no scoring set.
    """
    census = set(survivors)
    return tuple(sorted((set(holdings) | set(watch_list)) & census))


def score_of(
    direction: str, strength: float, abstention_reason: str | None
) -> tuple[float | None, str | None]:
    """Map one aggregated signal to a stored `Prediction`'s `(score, abstention)` pair.

    `direction` signs the strength: a bullish frame scores `+strength`, a bearish one
    `-strength`, and a neutral one `0.0`. An abstaining frame scores `None` and carries a code:
    `abstention_reason` when the caller supplies one of `ABSTENTION_CODES`, otherwise
    `ABSTAIN_ALL_ABSTAINED`.

    Every input is checked rather than normalised into place: an unknown direction, a
    non-finite strength, a directional frame carrying an abstention reason, or an abstaining
    frame whose strength is not zero would all be a signal contract violation that the stored
    `Prediction` cannot express. `-0.0` is normalised to `0.0`, because the two compare equal
    and address two different predictions (`domain/alpha_model.py::Prediction.normalize_score`).
    """
    if direction not in _DIRECTIONS:
        raise ValueError(
            f"the agent score mapping received direction {direction!r}; the engine's "
            f"SignalFrame speaks {sorted(_DIRECTIONS)}"
        )
    if not isinstance(strength, int | float) or not math.isfinite(float(strength)):
        raise ValueError(
            f"the agent score mapping received strength {strength!r}; a non-finite strength is "
            "not a score, and abstaining is how a signal says it has none"
        )
    value = float(strength)
    if direction == "abstain":
        if value != 0.0:
            raise ValueError(
                f"an abstaining signal carries strength {value!r}; `SignalFrame` requires zero "
                "strength for an abstention, and a non-zero one here is a directional number "
                "wearing an abstention's code"
            )
        if abstention_reason is None:
            return None, ABSTAIN_ALL_ABSTAINED
        if abstention_reason not in ABSTENTION_CODES:
            raise ValueError(
                f"the agent score mapping received abstention reason {abstention_reason!r}; "
                f"C's closed vocabulary is {sorted(ABSTENTION_CODES)}, and a free-text reason "
                "would enter a stored record the evaluator then has to interpret"
            )
        return None, abstention_reason
    if abstention_reason is not None:
        raise ValueError(
            f"a {direction} signal carries abstention reason {abstention_reason!r}; a frame is "
            "a number or a stated reason there is none, never both"
        )
    if not -1.0 <= value <= 1.0:
        raise ValueError(
            f"the agent score mapping received strength {value!r}, outside the SignalFrame "
            "range [-1, 1]"
        )
    score = _SIGN[direction] * value
    return (0.0 if score == 0.0 else score), None
