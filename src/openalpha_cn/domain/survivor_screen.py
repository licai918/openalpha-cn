"""The eligibility-and-survivor screen (`V2-P7-005`): facts in, verdicts and survivors out.

Pure functions over `SecurityFacts`. Three steps:

1. **Static reasons** -- board, listing age, special treatment, suspension and a missing factor
   value. Each is a fact about one security. (Whether the book can afford a lot is *not* one of
   them: it depends on the book's equity, and a holding that has risen past one lot's price must
   not be sold for it. The policy skips an entrant it cannot afford, and the survivor set stays
   a function of the market alone.)
2. **Liquidity** -- the bottom `liquidity_drop_fraction` by trailing traded amount among *every
   security on an allowed board*, whatever else is wrong with it. It is the one reason that
   depends on the other securities. Names that fail a static reason still count in that
   population (a suspended name's zero turnover is one of the thinnest, which is the literal
   reading of the design's "within the tradable boards"); that is a choice, pinned by a test.
3. **Survivors** -- of the securities with *no* reason, drop the worst `loser_decile_count` tenths
   by composite, so the cut is taken over the liquidity-and-eligibility survivors and nothing
   else. Ties break on `ts_code`, so no cut depends on input order.
"""

import math
from collections import Counter
from collections.abc import Sequence

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


def screen(facts: Sequence[SecurityFacts], config: SurvivorBookConfig) -> ScreenResult:
    """Assess every security in `facts`: why it may not be held, and who survives."""
    codes = [fact.ts_code for fact in facts]
    repeated = sorted(code for code, seen in Counter(codes).items() if seen > 1)
    if repeated:
        raise ValueError(f"securities listed more than once on one signal day: {repeated}")
    width = len(config.composite_factors)
    for fact in sorted(facts, key=lambda fact: fact.ts_code):
        if len(fact.oriented_values) != width:
            raise ValueError(
                f"{fact.ts_code}: {len(fact.oriented_values)} oriented values for {width} "
                "composite factors"
            )

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
