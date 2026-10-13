"""The selection layer (`V2-P7-005`): which eligible survivor fills a vacancy, in what order.

The default rule, `seeded_hash/v1`, **claims no skill**. It exists because a book needs *some*
deterministic order, and the *exploratory* findings behind this design (`docs/specs/v2/openalpha-cn-
v2-p7-decision-chain.md` section 3 -- labelled there as not evidence) suggest that whatever value
there is lies in the filter and in not trading, not in a ranking of the survivors. A better rule
-- an agent's score (plan C/A), a model's (plan B) -- replaces it by implementing
`SelectionRule` and being named in the registered configuration.

**Determinism is the whole contract.** The order is a function of the candidate *set* and the
seed alone: not of the order the candidates were passed in, not of the day, and not of
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

    def order(self, candidates: Iterable[str], *, seed: int) -> tuple[str, ...]:
        """Return the distinct `candidates`, first pick first. Pure and deterministic."""
        ...


def _selection_digest(seed: int, ts_code: str) -> str:
    return sha256(f"{seed}|{ts_code}".encode()).hexdigest()


class SeededHashRule:
    """Order candidates by a seeded hash of their code, whatever the day or the input order."""

    rule_id: str = "seeded_hash/v1"

    def order(self, candidates: Iterable[str], *, seed: int) -> tuple[str, ...]:
        distinct = set(candidates)
        return tuple(sorted(distinct, key=lambda code: (_selection_digest(seed, code), code)))


SELECTION_RULES: Final[Mapping[str, SelectionRule]] = MappingProxyType(
    {SeededHashRule.rule_id: SeededHashRule()}
)


def selection_rule(rule_id: str) -> SelectionRule:
    try:
        return SELECTION_RULES[rule_id]
    except KeyError:
        known = ", ".join(sorted(SELECTION_RULES))
        raise ValueError(f"unknown selection rule {rule_id!r}; known: {known}") from None
