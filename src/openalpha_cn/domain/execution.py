"""A filled or explicitly rejected simulated A-share execution.

Split out of `backtest/execution.py` (V2-P0B-012) because `domain/portfolio.py`'s
`PortfolioTransition.execution` field embeds one: `storage/portfolio.py` persists
`PortfolioTransition`, and `storage` cannot import anything under `openalpha_cn.backtest`
(forbidden by the `storage-no-upward-deps` import-linter contract). `ExecutionResult` was
already a plain data value with no dependency beyond stdlib `decimal`, so this is a pure
relocation -- the execution *engine* (`AShareExecutionPolicy`, `ExecutionRequest`,
`CostSchedule`, `MarketBar`) all stay in `backtest/execution.py`; none of it is needed by
storage, only the one result shape it returns.

`backtest/execution.py` re-exports `ExecutionResult` unchanged, so every existing
`from openalpha_cn.backtest.execution import ExecutionResult` keeps working.
"""

from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict


class ExecutionResult(BaseModel):
    """Filled or explicitly rejected simulated execution."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    status: Literal["filled", "rejected"]
    side: Literal["buy", "sell"]
    quantity: int
    reason: str | None = None
    filled_price: Decimal | None = None
    notional: Decimal = Decimal("0.00")
    commission: Decimal = Decimal("0.00")
    transfer_fee: Decimal = Decimal("0.00")
    stamp_duty: Decimal = Decimal("0.00")
    total_cost: Decimal = Decimal("0.00")
