"""Vocabulary and configuration of the survivor book (`V2-P7-005`).

A *survivor book* holds at most `holding_count` securities. It first drops every security the
account cannot or should not hold (**eligibility**), then drops the worst-scoring tail of what is
left (**survivors**), and afterwards changes its holdings only when a holding stops being
eligible or stops being a survivor. Which survivor fills a vacancy is the job of a pluggable
selection rule (`domain/survivor_selection.py`); this module only names the things the three
layers talk about.

Everything here is pure Python on `Decimal`, `float` and `str`: no numeric or infrastructure
library, so the whole core can be tested without a panel (`AGENTS.md` hard rule 2). The design is
`docs/superpowers/specs/2026-10-13-p7-step0-survivor-book-design.md`; every number that is a
default below is a **pre-registration candidate** there, frozen only by `V2-P7-006`.
"""

from decimal import Decimal
from hashlib import sha256
from typing import Annotated, Final, Literal, Self, get_args

from pydantic import BaseModel, ConfigDict, Field, computed_field, model_validator

from openalpha_cn.domain._identity import stable_model_id
from openalpha_cn.domain.json_value import canonical_json_bytes

Board = Literal["main", "growth", "star", "bse"]
"""The vocabulary `backtest.execution.security_board` already answers in (`growth` is ChiNext).

Reused rather than re-derived: `V2-P6-031` removed three private copies of that classifier, and a
fourth spelling here would be the same defect again. The adapter fills `SecurityFacts.board` from
that function; this module never looks at a code's digits.
"""

EligibilityReason = Literal[
    "board_not_allowed",
    "listing_too_recent",
    "special_treatment",
    "suspended",
    "missing_factor_value",
    "illiquid",
]
ELIGIBILITY_REASON_ORDER: Final[tuple[EligibilityReason, ...]] = get_args(EligibilityReason)
"""The fixed order a verdict lists its reasons in, so two runs print the same line."""

FiniteFloat = Annotated[float, Field(allow_inf_nan=False)]

DEFAULT_COMPOSITE_FACTORS: Final[tuple[str, ...]] = (
    "residual_vol_60/v1",
    "return_vol_60/v1",
    "turnover_60/v1",
    "downside_vol_60/v1",
)

ILLIQUID_DROP_FRACTION: Final[Decimal] = Decimal("0.30")
"""The thinnest share of the statically eligible population the daily screen drops as `illiquid`.

The design (`docs/superpowers/specs/2026-10-13-p7-step0-survivor-book-design.md` section 6) keeps
the top 70% by 20-day average traded amount among the names that carry no static reason, so the cut
is the bottom `floor_share(population, ILLIQUID_DROP_FRACTION)` of that population, ordered by
`avg_amount` ascending and ties on `ts_code`. It is a rank, not an absolute turnover band:
`avg_amount` is in whatever unit the adapter chose and only its order matters. The weekly report
counts the exits whose verdict carries `illiquid` (`strategy_survivor_summary._illiquid_exits`), so
the daily and the weekly readings are the same cut. Frozen by `V2-P7-006a` on 2026-11-07.
"""

ILLIQUID_POLICY: Final[str] = "forced_exit"
"""What a holding below the liquidity line does: option (a), a forced exit.

A holding the screen calls `illiquid` has lost eligibility, so the policy sells the whole holding
at the next open with `EXIT_ELIGIBILITY_LOST`; it is never held through the line. The
holding-hysteresis alternative (b) -- a lower line a holding must fall below before it is sold --
is **not implemented**: it needs its own threshold chosen in advance and re-measured there, once
the forward samples show real turnover, through the process `V2-P7-006` sets. Option (a) is the
rule the exploratory period measured; `V2-P7-006a` froze it on 2026-11-07.
"""


class SurvivorBookConfig(BaseModel):
    """Every parameter of the survivor book. Frozen, versioned and content-addressed.

    `config_id` addresses the **stored form**: `Decimal("0.3")` and `Decimal("0.30")` are the same
    number and two different identities, and so are two orderings of `boards`. A registration is
    matched by that id, so one that is re-typed rather than read back verbatim will not match --
    which fails safe, and is why a registration stores this model's own JSON and reads it back.
    `composite_factors` keeps its order for a different reason: it pairs by position with
    `SecurityFacts.oriented_values`.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["survivor-book-config/v1"] = "survivor-book-config/v1"
    holding_count: int = Field(default=10, ge=1, le=50)
    capital: Decimal = Field(default=Decimal("350000"), gt=0)
    boards: tuple[Board, ...] = ("main",)
    """Shanghai and Shenzhen main boards only: the account cannot trade ChiNext (decision D12)."""
    liquidity_drop_fraction: Decimal = Field(default=ILLIQUID_DROP_FRACTION, ge=0, lt=1)
    min_listing_sessions: int = Field(default=250, ge=0)
    loser_decile_count: int = Field(default=2, ge=0, le=9)
    entry_floor_fraction: Decimal = Field(default=Decimal("0.25"), ge=0, lt=1)
    near_exit_fraction: Decimal = Field(default=Decimal("0.10"), ge=0, lt=1)
    cooldown_sessions: int = Field(default=20, ge=0)
    max_replacements_per_session: int = Field(default=2, ge=1)
    buy_order_expiry_sessions: int = Field(default=3, ge=1)
    lot_size: int = Field(default=100, ge=1)
    cost_reserve_fraction: Decimal = Field(default=Decimal("0.005"), ge=0, lt=Decimal("0.1"))
    """Held back from every buy's budget so fees and slippage cannot push the last buy past the
    cash that is left: ten buys of exactly `capital / 10` would otherwise overspend by the costs."""
    selection_rule: Literal["seeded_hash/v1"] = "seeded_hash/v1"
    seed: int = Field(ge=0)
    """No default on purpose: a registration has to name the seed it was frozen with."""
    composite_factors: tuple[str, ...] = DEFAULT_COMPOSITE_FACTORS

    @model_validator(mode="after")
    def _lists_are_nonempty_and_unique(self) -> Self:
        if not self.boards or len(set(self.boards)) != len(self.boards):
            raise ValueError("boards must be a non-empty list without repeats")
        if not self.composite_factors or len(set(self.composite_factors)) != len(
            self.composite_factors
        ):
            raise ValueError("composite_factors must be a non-empty list without repeats")
        return self

    def sizing_budget(self, equity: Decimal | None = None) -> Decimal:
        """What one new holding may spend: an equal share of `equity`, less the cost reserve.

        `equity` defaults to the starting `capital`; the daily flow passes the ledger's current
        equity, so a book that has grown or shrunk is still sized to what it is worth.
        """
        basis = self.capital if equity is None else equity
        if not basis.is_finite() or basis <= 0:
            raise ValueError("equity must be positive")
        return basis / self.holding_count * (1 - self.cost_reserve_fraction)

    @computed_field(return_type=str)  # type: ignore[prop-decorator]
    @property
    def config_id(self) -> str:
        return stable_model_id(prefix="sbc", model=self)


def survivor_config_digest(config: SurvivorBookConfig) -> str:
    """The whole SHA-256, 64 lowercase hex digits, of `config`'s canonical JSON (`V2-P7-017`).

    The bytes are the ones `config_id` addresses -- the stored form without computed fields,
    serialised by `json_value.canonical_json_bytes`, whose keywords are `stable_model_id`'s and
    which `runtime/provenance.compute_config_digest` hashes with too -- so `config_id` is `sbc_`
    and this digest's first 24 digits, never a second canonicalisation. It is kept whole for the
    use it has: a paper-book snapshot quotes it as which configuration its book is settled under
    (the width of `RunManifest.config_digest`) and compares it for equality; it is not an address.
    """
    payload = config.model_dump(mode="json", exclude_computed_fields=True)
    return sha256(canonical_json_bytes(payload)).hexdigest()


def floor_share(count: int, fraction: Decimal) -> int:
    """How many of `count` names a `fraction` of them covers, rounded down."""
    if count < 0:
        raise ValueError("count must not be negative")
    if not fraction.is_finite() or not 0 <= fraction <= 1:
        raise ValueError("fraction must lie between 0 and 1")
    return int(Decimal(count) * fraction)


def lot_quantity(budget: Decimal, price: Decimal, lot_size: int) -> int:
    """Whole lots of `lot_size` that `budget` buys at `price`, as a share count; 0 if none."""
    if not price.is_finite() or price <= 0:
        raise ValueError("price must be positive")
    if not budget.is_finite() or budget < 0:
        raise ValueError("budget must not be negative")
    if lot_size < 1:
        raise ValueError("lot_size must be at least 1")
    return int(budget // (price * lot_size)) * lot_size


class SecurityFacts(BaseModel):
    """What the screen needs to know about one security on one signal day. Plain values."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    ts_code: str = Field(min_length=1)
    board: Board
    listed_sessions: int = Field(ge=0)
    special_treatment: bool
    suspended: bool
    close: Decimal = Field(gt=0)
    """The raw (unadjusted) close: the price a lot is bought at, not a return input."""
    avg_amount: Decimal = Field(ge=0)
    """Trailing average traded amount, in the unit the adapter chose; only its order matters."""
    oriented_values: tuple[FiniteFloat | None, ...]
    """One processed factor value per `composite_factors` entry, sign-adjusted so that a higher
    number is better; `None` where the factor has no value for this security."""


class EligibilityVerdict(BaseModel):
    """Whether one security may be held, and every reason it may not."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    ts_code: str
    reasons: tuple[EligibilityReason, ...] = ()
    composite: FiniteFloat | None = None

    @model_validator(mode="after")
    def _reasons_are_ordered_and_an_eligible_name_is_scored(self) -> Self:
        positions = [ELIGIBILITY_REASON_ORDER.index(reason) for reason in self.reasons]
        if positions != sorted(set(positions)):
            raise ValueError("reasons must be unique and in ELIGIBILITY_REASON_ORDER")
        if not self.reasons and self.composite is None:
            raise ValueError("an eligible verdict must carry its composite")
        return self

    @property
    def eligible(self) -> bool:
        return not self.reasons


class RankedName(BaseModel):
    """A survivor and where it stands: rank 1 is the best composite among the survivors."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    ts_code: str
    composite: FiniteFloat
    rank: int = Field(ge=1)
    close: Decimal = Field(gt=0)
    """Carried so the policy can size a lot without going back to the facts."""


class ScreenResult(BaseModel):
    """The output of the eligibility-and-survivor screen for one signal day."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    verdicts: tuple[EligibilityVerdict, ...]
    """Every security that was assessed, ordered by `ts_code`."""
    survivors: tuple[RankedName, ...]
    """The eligible securities that remain after the loser tail is dropped, best first."""

    @model_validator(mode="after")
    def _the_screen_is_internally_consistent(self) -> Self:
        codes = [verdict.ts_code for verdict in self.verdicts]
        if codes != sorted(set(codes)):
            raise ValueError("verdicts must be unique and ordered by ts_code")
        if len({name.ts_code for name in self.survivors}) != len(self.survivors):
            raise ValueError("a security appears more than once among the survivors")
        if [name.rank for name in self.survivors] != list(range(1, len(self.survivors) + 1)):
            raise ValueError("survivors must be ranked 1..n in the order given")
        composites = [name.composite for name in self.survivors]
        if composites != sorted(composites, reverse=True):
            raise ValueError("survivors must be listed best first")
        eligible = {verdict.ts_code for verdict in self.verdicts if verdict.eligible}
        if not {name.ts_code for name in self.survivors} <= eligible:
            raise ValueError("every survivor must have an eligible verdict")
        return self
