"""The eligibility-and-survivor screen (`V2-P7-005`): facts in, verdicts and survivors out.

Pure functions over `SecurityFacts`. Three steps, in this order, and the order matters because
each one's population is the previous one's output:

1. **Static reasons** -- board, listing age, special treatment, suspension, a missing factor value
   and a lot the per-name budget cannot afford. Each is a fact about one security.
2. **Liquidity** -- the bottom `liquidity_drop_fraction` by trailing traded amount *within the
   allowed boards*. It is the one reason that depends on the other securities, which is why it is
   computed after the static ones and over the board-allowed population only.
3. **Survivors** -- of the eligible securities, drop the worst `loser_decile_count` tenths by
   composite. Ties break on `ts_code`, so the cut never depends on input order.
"""

import math
from collections.abc import Sequence
from decimal import Decimal

from openalpha_cn.domain.survivor_book import (
    ELIGIBILITY_REASON_ORDER,
    EligibilityReason,
    EligibilityVerdict,
    RankedName,
    ScreenResult,
    SecurityFacts,
    SurvivorBookConfig,
    floor_share,
)


def composite_of(values: Sequence[float | None]) -> float | None:
    """The mean of the oriented factor values, or `None` if any of them is missing.

    `math.fsum` rather than `sum`: it is exactly rounded, so the result does not depend on the
    platform's float summation and a pinned expectation means the same thing everywhere.
    """
    if not values or any(value is None for value in values):
        return None
    present = [value for value in values if value is not None]
    return math.fsum(present) / len(present)


def screen(
    facts: Sequence[SecurityFacts],
    config: SurvivorBookConfig,
    *,
    equity: Decimal | None = None,
) -> ScreenResult:
    """Assess every `facts` security; `equity` sizes the lot test (default: `config.capital`)."""
    codes = [fact.ts_code for fact in facts]
    if len(set(codes)) != len(codes):
        raise ValueError("a security appears more than once in the facts of one signal day")
    width = len(config.composite_factors)
    for fact in facts:
        if len(fact.oriented_values) != width:
            raise ValueError(
                f"{fact.ts_code}: {len(fact.oriented_values)} oriented values for {width} "
                "composite factors"
            )

    budget = config.sizing_budget(equity)
    reasons: dict[str, set[EligibilityReason]] = {fact.ts_code: set() for fact in facts}
    composites: dict[str, float | None] = {}
    for fact in facts:
        found = reasons[fact.ts_code]
        composites[fact.ts_code] = composite_of(fact.oriented_values)
        if fact.board not in config.boards:
            found.add("board_not_allowed")
        if fact.listed_sessions < config.min_listing_sessions:
            found.add("listing_too_recent")
        if fact.special_treatment:
            found.add("special_treatment")
        if fact.suspended:
            found.add("suspended")
        if composites[fact.ts_code] is None:
            found.add("missing_factor_value")
        if fact.close * config.lot_size > budget:
            found.add("unaffordable_lot")

    allowed = sorted(
        (fact for fact in facts if fact.board in config.boards),
        key=lambda fact: (fact.avg_amount, fact.ts_code),
    )
    for fact in allowed[: floor_share(len(allowed), config.liquidity_drop_fraction)]:
        reasons[fact.ts_code].add("illiquid")

    verdicts = tuple(
        EligibilityVerdict(
            ts_code=code,
            reasons=tuple(r for r in ELIGIBILITY_REASON_ORDER if r in reasons[code]),
            composite=composites[code],
        )
        for code in sorted(codes)
    )

    close = {fact.ts_code: fact.close for fact in facts}
    scored = [(v.ts_code, v.composite) for v in verdicts if v.eligible and v.composite is not None]
    worst_first = sorted(scored, key=lambda pair: (pair[1], pair[0]))
    dropped = len(worst_first) * config.loser_decile_count // 10
    best_first = sorted(worst_first[dropped:], key=lambda pair: (-pair[1], pair[0]))
    survivors = tuple(
        RankedName(ts_code=code, composite=composite, rank=position, close=close[code])
        for position, (code, composite) in enumerate(best_first, start=1)
    )
    return ScreenResult(verdicts=verdicts, survivors=survivors)
