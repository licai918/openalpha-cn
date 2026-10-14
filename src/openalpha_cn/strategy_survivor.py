"""The survivor book's registration, read and checked, and the paper book it is settled in
(`V2-P7-008`).

A survivor registration is a registered configuration whose `config` is exactly three keys:

    {"kind": "survivor_book/v1",
     "survivor_book": <SurvivorBookConfig.model_dump(mode="json", exclude_computed_fields=True)>,
     "adapter": {"schema_version": "survivor-adapter/v1", "epoch_session": "YYYY-MM-DD", ...}}

`scripts/daily_selection.py` dispatches on `kind` after step 1 admits the registration: a
configuration with no `kind` is the P6 book and runs the P6 day as it always has; one with this
kind is read here (`read_survivor_registration`) and runs the survivor day. The book's
parameters -- `capital` (the opening cash), `seed`, `composite_factors` -- live in the book block
only; the adapter block holds what the daily adapters read and repeats none of them.

Two identities, kept apart. `grid.config_id` of the whole registered `config` is the
registration's: the journal key and the prediction's `feature_version`. `survivor_config_digest`
of the book block alone is the paper book's (`SurvivorRegistration.digest`): a snapshot quotes it
as the configuration its book is settled under.

## Two classes of refusal

`SurvivorRegistrationError` is a registration the daily command cannot read -- an unknown kind, a
missing or extra top-level key, a block that does not validate, a book block that is not its own
stored form -- and the script exits 2 on it, as on a missing registration.
`SurvivorRegistrationContradictionError`, its subclass, is a registration read whole that
contradicts itself: today, a book `seed` that is not the registration's own `settings.random_seed`.
The script exits 3 on it, the class of a `config_id` that does not match its own config.

## Why the book block must be its own stored form

`SurvivorBookConfig.config_id` addresses the stored form: `"0.30"` and `"0.3"` are one number and
two identities (`survivor_book.py`). A block that validates without being exactly
`model_dump(mode="json", exclude_computed_fields=True)` of what it validates into -- a decimal
written as a float or an integer, a count written as a float, a default left out -- reads as a
configuration with an identity nobody registered, so it is refused, naming the fields that read
back otherwise. A block written as that dump reads back to the `config_id` and the digest of the
configuration it was dumped from.

## Why the daily command imports this module lazily

`make_paper_portfolio` needs `backtest/paper.py`, and importing that module installs an audit
hook for the life of the process (`KNOWN_PAPER_LIMITATIONS`,
`the_audit_hook_can_never_be_uninstalled`). A P6 day keeps no paper book and must not pay for one,
so the script imports this module only for a registration that names a `kind`, and
`tests/unit/scripts/test_daily_selection_dispatch.py` runs a P6 day in a fresh interpreter and
finds neither module loaded. Keep paper and ledger imports out of this module except the ones
`make_paper_portfolio` itself needs.

## The paper book (decision D15)

Whole-order rejection under the registered `paper_limits` -- `1`, `1`, `0` in the registration the
design writes, so cash and the market are the only constraints a buy meets -- at the protocol's
costs: `PROTOCOL_COSTS` with `PROTOCOL_SLIPPAGE_RATE` folded into the transfer-fee slot
(`slippage_folded_execution`), which charges what the P6 book charges for the same fill, to the
fen. Every `transfer_fee` the book records is therefore slippage, not a fee anybody levied.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Annotated, Any, Final, Literal, TypeVar

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StrictStr,
    ValidationError,
    field_validator,
)

from openalpha_cn.backtest.multi_day import PortfolioLedger
from openalpha_cn.backtest.paper import PaperPortfolio, slippage_folded_execution
from openalpha_cn.backtest.portfolio import PortfolioLimits
from openalpha_cn.domain.survivor_book import SurvivorBookConfig, survivor_config_digest
from openalpha_cn.panel_factors import FACTOR_TRANSFORMS
from openalpha_cn.strategy_view import PROTOCOL_COSTS, PROTOCOL_SLIPPAGE_RATE

__all__ = [
    "ADAPTER_BLOCK",
    "BOOK_BLOCK",
    "KIND_KEY",
    "MAX_PANEL_CATCH_UP_SESSIONS",
    "REGISTRATION_KEYS",
    "SURVIVOR_KIND",
    "PaperLimitsConfig",
    "SurvivorAdapterConfig",
    "SurvivorRegistration",
    "SurvivorRegistrationContradictionError",
    "SurvivorRegistrationError",
    "make_paper_portfolio",
    "read_survivor_registration",
    "registration_kind",
]

SURVIVOR_KIND: Final = "survivor_book/v1"
KIND_KEY: Final = "kind"
"""The key that decides the day. `scripts/daily_selection.py` spells it too, because reading it
from here would load this module for every P6 day."""
BOOK_BLOCK: Final = "survivor_book"
ADAPTER_BLOCK: Final = "adapter"
REGISTRATION_KEYS: Final[frozenset[str]] = frozenset({KIND_KEY, BOOK_BLOCK, ADAPTER_BLOCK})
"""Every top-level key of a survivor configuration, and no other."""

MAX_PANEL_CATCH_UP_SESSIONS: Final = 16
"""The most sessions the adapter may let step 2 catch up in one run. Each costs five requests
(`daily_selection.DEFAULT_MAX_PANEL_CATCH_UP_SESSIONS` states the arithmetic), so an ordinary
day's 16 plus 16 sessions is 96, the last such sum under the 100 the design sized a day against;
17 would be 101. It bounds the part of a day that grows with missed sessions, nothing else."""


class SurvivorRegistrationError(ValueError):
    """A survivor registration the daily command cannot read; the script exits 2."""


class SurvivorRegistrationContradictionError(SurvivorRegistrationError):
    """A survivor registration read whole that contradicts itself; the script exits 3."""


def _decimal_text(value: object) -> object:
    """A limit is a decimal written as text, as the registration's canonical JSON writes one: a
    float is binary and an integer is a re-typing of the stored form."""
    if isinstance(value, Decimal | str):
        return value
    raise ValueError(f'a limit is a decimal written as text, such as "1", not {value!r}')


DecimalText = Annotated[Decimal, BeforeValidator(_decimal_text)]


def _iso_date(value: object) -> object:
    """A session is written `YYYY-MM-DD`: an instant, a number or another spelling of the day is
    refused rather than converted."""
    if type(value) is date:
        return value
    if isinstance(value, str):
        try:
            day = date.fromisoformat(value)
        except ValueError:
            day = None
        if day is not None and day.isoformat() == value:
            return day
    raise ValueError(f"a session is a date written YYYY-MM-DD, not {value!r}")


class PaperLimitsConfig(BaseModel):
    """The paper book's `PortfolioLimits`, as registered: decimals written as text, within the
    bounds `PortfolioLimits` itself declares."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    max_position_weight: DecimalText = Field(gt=0, le=1)
    max_total_exposure: DecimalText = Field(gt=0, le=1)
    min_cash_weight: DecimalText = Field(ge=0, lt=1)


class SurvivorAdapterConfig(BaseModel):
    """What the survivor day's adapters read, beside the book: every field named, none defaulted.

    `epoch_session` is the session the paper book opens on (the session before the first
    scheduled run); `transform` the processed tier the composite factors are built and read
    under; `min_coverage` and `max_carry_sessions` the facts adapter's coverage floor and carry
    bound; `max_panel_catch_up_sessions` step 2's catch-up bound. Beside `schema_version`, five
    tags state what the code does and cannot be chosen otherwise in `survivor-adapter/v1`:
    `exchange`, the name of the calendar every panel build and the P6 day read (`SSE`; the facts
    adapter takes each code's exchange from its suffix, so this is the calendar's name only);
    `benchmark`, the protocol's CSI 500 (`000905.SH`, `strategy_view.PROTOCOL_BENCHMARKS[0]`),
    whose close settles the book's days and which the pre-registered evaluation is measured
    against -- the panel's `index_daily` stores only three indices, and a free string would admit
    a typo that fails every night and an evaluation nobody registered; the 20-session prediction
    horizon; the protocol's costs; and whole-order rejection with the buy carried. Widening any
    of them is a new schema version.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["survivor-adapter/v1"]
    epoch_session: Annotated[date, BeforeValidator(_iso_date)]
    exchange: Literal["SSE"]
    transform: StrictStr
    min_coverage: float = Field(gt=0, le=1, strict=True, allow_inf_nan=False)
    max_carry_sessions: int = Field(ge=1, strict=True)
    max_panel_catch_up_sessions: int = Field(ge=0, le=MAX_PANEL_CATCH_UP_SESSIONS, strict=True)
    benchmark: Literal["000905.SH"]
    prediction_horizon: Literal["20d"]
    cost_policy: Literal["protocol/v1"]
    paper_limits: PaperLimitsConfig
    unfilled_buy: Literal["reject_whole_and_carry/v1"]

    @field_validator("transform")
    @classmethod
    def _a_transform_this_build_declares(cls, value: str) -> str:
        if value not in FACTOR_TRANSFORMS.qualified_keys:
            raise ValueError(
                f"{value!r} is not a declared transform; this build knows "
                f"{list(FACTOR_TRANSFORMS.qualified_keys)}"
            )
        return value


@dataclass(frozen=True, slots=True)
class SurvivorRegistration:
    """A survivor registration read and checked: the book it runs and the adapters' settings."""

    book: SurvivorBookConfig
    adapter: SurvivorAdapterConfig

    @property
    def digest(self) -> str:
        """The paper book's identity: `survivor_config_digest` of the book, whole."""
        return survivor_config_digest(self.book)


def registration_kind(config: Mapping[str, Any]) -> str | None:
    """The `kind` a registered configuration names, or `None` when it names none (the P6 book).

    A `kind` that is not a name is refused: a configuration that has the key has declared itself
    something other than the P6 book, and what it declared cannot be read.
    """
    if KIND_KEY not in config:
        return None
    kind = config[KIND_KEY]
    if not isinstance(kind, str):
        raise SurvivorRegistrationError(
            f"the registered configuration's {KIND_KEY} is {kind!r}, not the name of a kind"
        )
    return kind


_Model = TypeVar("_Model", bound=BaseModel)


def _validated(model: type[_Model], block: object, name: str) -> _Model:
    """`block` as `model`, or a refusal naming every field that does not validate as
    `<block>.<field>`."""
    if not isinstance(block, Mapping):
        raise SurvivorRegistrationError(
            f"the {name} block is {type(block).__name__}, not a mapping of its fields"
        )
    try:
        return model.model_validate(dict(block))
    except ValidationError as error:
        problems = "; ".join(
            f"{'.'.join((name, *(str(part) for part in problem['loc'])))}: {problem['msg']}"
            for problem in error.errors()
        )
        raise SurvivorRegistrationError(
            f"the {name} block does not validate: {problems}"
        ) from error


def _json_text(value: object) -> str:
    """`value` as JSON text: `10` and `10.0`, or `1` and `true`, are equal in Python and not in
    the registration's bytes, which are what `config_id` addresses."""
    return json.dumps(value, sort_keys=True, ensure_ascii=False)


def read_survivor_registration(config: Mapping[str, Any], *, seed: int) -> SurvivorRegistration:
    """The survivor registration `config` is, checked against the registration's own `seed`.

    Refuses (`SurvivorRegistrationError`) a kind other than `SURVIVOR_KIND`, a top-level key
    missing or beside the three, a book or adapter block that does not validate, and a book block
    that is not its own stored form; refuses (`SurvivorRegistrationContradictionError`) a book
    `seed` other than `seed`. See the module docstring for each rule.
    """
    kind = registration_kind(config)
    if kind != SURVIVOR_KIND:
        raise SurvivorRegistrationError(
            f"the registered configuration's {KIND_KEY} is {kind!r}; the daily command runs "
            f"{SURVIVOR_KIND!r}, or the P6 book for a configuration that names no {KIND_KEY}"
        )
    keys = set(config)
    if keys != REGISTRATION_KEYS:
        extra, missing = sorted(keys - REGISTRATION_KEYS), sorted(REGISTRATION_KEYS - keys)
        raise SurvivorRegistrationError(
            f"a {SURVIVOR_KIND} configuration is exactly {sorted(REGISTRATION_KEYS)}"
            + (f"; it also names {extra}" if extra else "")
            + (f"; it lacks {missing}" if missing else "")
        )
    block = config[BOOK_BLOCK]
    book = _validated(SurvivorBookConfig, block, BOOK_BLOCK)
    stored = book.model_dump(mode="json", exclude_computed_fields=True)
    if _json_text(stored) != _json_text(dict(block)):
        differing = sorted(
            key
            for key in set(stored) | set(block)
            if key not in stored
            or key not in block
            or _json_text(stored[key]) != _json_text(block[key])
        )
        raise SurvivorRegistrationError(
            f"the {BOOK_BLOCK} block is not the stored form of the configuration it reads as "
            f"({book.config_id}): {differing} read back otherwise. A registration stores "
            "SurvivorBookConfig.model_dump(mode='json', exclude_computed_fields=True) verbatim: "
            "config_id addresses that form, and a re-typed spelling of the same values is not "
            "the form it addresses"
        )
    adapter = _validated(SurvivorAdapterConfig, config[ADAPTER_BLOCK], ADAPTER_BLOCK)
    if book.seed != seed:
        raise SurvivorRegistrationContradictionError(
            f"{BOOK_BLOCK}.seed is {book.seed} and the registration's own seed "
            f"(settings.random_seed) is {seed}; a registration that names two seeds contradicts "
            "itself"
        )
    return SurvivorRegistration(book=book, adapter=adapter)


def make_paper_portfolio(ledger: PortfolioLedger, limits: PaperLimitsConfig) -> PaperPortfolio:
    """The survivor book's paper portfolio over `ledger` (decision D15): `limits` as
    `PortfolioLimits`, field by field and by keyword, at the protocol's costs with its slippage
    folded into the transfer-fee slot."""
    return PaperPortfolio(
        ledger=ledger,
        limits=PortfolioLimits(
            max_position_weight=limits.max_position_weight,
            max_total_exposure=limits.max_total_exposure,
            min_cash_weight=limits.min_cash_weight,
        ),
        execution=slippage_folded_execution(PROTOCOL_COSTS, slippage_rate=PROTOCOL_SLIPPAGE_RATE),
    )
