"""The selection layer (`V2-P7-005`): which eligible survivor fills a vacancy, in what order.

The default rule, `seeded_hash/v1`, **claims no skill**. It exists because a book needs *some*
deterministic order, and the *exploratory* findings behind this design (`docs/specs/v2/openalpha-cn-
v2-p7-decision-chain.md` section 3 -- labelled there as not evidence) suggest that whatever value
there is lies in the filter and in not trading, not in a ranking of the survivors. A better rule
-- an agent's score (plan C/A), a model's (plan B) -- replaces it by implementing
`SelectionRule` and being named in the registered configuration.

**Why the order depends on the session.** A queue that ignores the day puts the same names at
the head every time a vacancy opens, so a name that was sold and then re-qualifies is bought
straight back. `V2-P7-005`'s T1 simulation measured this on the research window (exploratory,
in-sample, not evidence): the day-independent queue turned the book over about twice as fast and
returned 1.3-1.6 percentage points a year less than a draw that is fresh each session; salting the
hash with the session removed the whole gap (`docs/research/p7-step0-validation/
T1-stratified-vs-random.md` section 5). So the draw is a function of the session as well; the
watch list is *today's* draw order, not a promise about tomorrow's.

**Determinism is the whole contract.** The order is a function of the candidate *set*, the seed
and the session alone: not of the order the candidates were passed in, and not of
`PYTHONHASHSEED`. Duplicates are collapsed, and the sort key `(digest, code)` is a total order
over the distinct codes, so no `set` or `dict` iteration order ever reaches the result. A first
prototype that drew from a `set` produced different numbers in different processes;
`tests/unit/domain/test_survivor_selection.py` runs the rule in child processes with different
hash seeds so that cannot come back unnoticed.
"""

from collections.abc import Iterable, Mapping
from hashlib import sha256
from types import MappingProxyType
from typing import Final, Protocol


class SelectionRule(Protocol):
    rule_id: str

    def order(self, candidates: Iterable[str], *, seed: int, session: int) -> tuple[str, ...]:
        """Return the distinct `candidates`, first pick first. Pure and deterministic."""
        ...


def _selection_digest(seed: int, session: int, ts_code: str) -> str:
    return sha256(f"{seed}|{session}|{ts_code}".encode()).hexdigest()


class SeededHashRule:
    """Order candidates by a hash of seed, session and code, whatever the input order."""

    rule_id: str = "seeded_hash/v1"

    def order(self, candidates: Iterable[str], *, seed: int, session: int) -> tuple[str, ...]:
        distinct = set(candidates)
        return tuple(
            sorted(distinct, key=lambda code: (_selection_digest(seed, session, code), code))
        )


SELECTION_RULES: Final[Mapping[str, SelectionRule]] = MappingProxyType(
    {SeededHashRule.rule_id: SeededHashRule()}
)


def selection_rule(rule_id: str) -> SelectionRule:
    try:
        return SELECTION_RULES[rule_id]
    except KeyError:
        known = ", ".join(sorted(SELECTION_RULES))
        raise ValueError(f"unknown selection rule {rule_id!r}; known: {known}") from None
