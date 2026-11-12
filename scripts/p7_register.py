"""The `V2-P7-006b` registration's writer: the file that writes the one registration.

The registration is written **once**, by hand, after the smoke's phase 1 said go; it binds the whole
of `src/`, `scripts/research/`, `pyproject.toml`, `uv.lock` and the two daily scripts, so its
`code_commit` is the commit whose bytes the smoke ran on. That is why this writer lives in
`scripts/`, beside the smoke scripts, and not under `scripts/research/`: landing the writer inside
a bound path would change the very bytes the smoke tested, and the registration would have to be
preceded by another smoke. A later change to this file therefore moves no registration's binding --
which is what is wanted, because it runs once.

The writer never stages or commits anything. It checks the tree it runs in, builds the body, reads
it back with every reader the daily command and the weekly report will use, and then either prints
the canonical document and its sha256 (`--dry-run`) or writes the file with `registry.register`.
The commit is the user's step, and the registration's own commit is the one the forward clock
starts after.

Every refusal is its own exception and exits 2 with nothing written; `main` is the command line and
`registration_body` is the pure constructor that the registration's chain test also reads.
"""

from __future__ import annotations

import copy
import re
import sys
from collections.abc import Mapping
from datetime import date
from pathlib import Path
from typing import Any, Final
from zoneinfo import ZoneInfo

_SCRIPTS: Final[Path] = Path(__file__).resolve().parent
for _root in (_SCRIPTS, _SCRIPTS / "research"):
    if str(_root) not in sys.path:
        sys.path.insert(0, str(_root))

import grid  # noqa: E402
import p7_smoke_env  # noqa: E402

from openalpha_cn import (  # noqa: E402
    strategy_survivor,
    strategy_survivor_continuation,
    strategy_survivor_forward,
)

REGISTRATION_PATH: Final[str] = "docs/research/p7-registration.json"
RESEARCH_DIRECTORY: Final[str] = "docs/research"
"""The only directory a registration may be written in."""
SHA256_ALGORITHM: Final[str] = "sha256"
_FULL_COMMIT: Final[re.Pattern[str]] = re.compile(r"[0-9a-f]{40}")
_SHANGHAI: Final[ZoneInfo] = ZoneInfo("Asia/Shanghai")


class RegistrationWriterError(ValueError):
    """A registration that cannot be written as asked; the message names the reason and nothing
    is written."""


EPOCH_LOOKBACK_DAYS: Final[int] = 16
"""The most calendar days an epoch may precede today (plan section 2): 11 sessions is the bound the
daily command's own calendar check makes exactly, and this is its conservative calendar upper
bound. The exact check is the first evening's step 1."""

FORWARD_PROTOCOL: Final[dict[str, Any]] = {
    "schema_version": strategy_survivor_forward.SURVIVOR_FORWARD_PROTOCOL_SCHEMA,
    "anchor_ordinal": 1,
    "primary_horizon": strategy_survivor_forward.ADOPTED_PRIMARY_HORIZON,
    "secondary_horizons": [5],
    "primary_metric": strategy_survivor_forward.ADOPTED_PRIMARY_METRIC,
    "secondary_metrics": ["survivors_minus_ew", "ew_minus_worst_decile"],
    "window_periods": strategy_survivor_forward.ADOPTED_WINDOW_PERIODS,
    "bootstrap_samples": grid.PROTOCOL_BOOTSTRAP_SAMPLES,
    "random_seed": grid.PROTOCOL_RANDOM_SEED,
    "q": grid.PROTOCOL_FALSE_DISCOVERY_RATE,
}
"""The forward protocol the registration freezes (plan section 1.2, Part E's recommended values):
the primary horizon, window, seed and test are the adopted constants; the two secondaries are the
family Part E defined."""

FORWARD_POWER: Final[dict[str, Any]] = {
    "schema_version": strategy_survivor_forward.SURVIVOR_FORWARD_POWER_SCHEMA,
    "test_level": 0.05,
    "target_power": 0.80,
    "critical_value": "t",
    "assumed_lag1_autocorrelation": 0.0,
    "minimum_periods": 30,
    "planned": [
        {
            "horizon": strategy_survivor_forward.ADOPTED_PRIMARY_HORIZON,
            "metric": strategy_survivor_forward.ADOPTED_PRIMARY_METRIC,
            "planning_sd": 0.18,
            "planning_means": [0.037, 0.05, 0.10],
            "sd_source": (
                "研究期约 0.14 与验证读观测 0.182 取较大者向下取两位；单次观测不是规划值"  # noqa: RUF001
            ),
        }
    ],
}
"""The power plan the registration freezes (plan section 1.2). D-1 (`planning_sd` 0.18) and D-2
(`minimum_periods` 30) are the user's adopted values (2026-11-12); the plan can state only the
primary test, because the h5 dispersion was never measured (D-3)."""

CRITERIA: Final[dict[str, str]] = {
    "primary": (
        "h20 秩 IC 单侧符号翻转：均值 > 0，p ≤ 0.05，K = 36 个非重叠位置，锚点序号 1。"
        "这是候选判据，不是已经成立的结论。"
    ),
    "stop_rule": (
        "秩 IC 均值 ≤ 0.02 或 ew_minus_worst_decile 均值 ≤ 0 即停止，中期在第 18 个累积位置、表"
        "累积均值 ≤ 0 即停止。"
    ),
    "interim": "第 18 个累积位置只报计数与描述性均值，K = 36 个位置到齐之前不做检验、不给结论词。",
    "secondary_family": (
        "族大小 3：h20 survivors_minus_ew、h20 ew_minus_worst_decile、h5 rank_ic，"
        "BY 任意相依，q = 0.10。"
    ),
    "evidence": (
        "确认性证据只来自本登记提交之后的前向记录，2022-2023 读数是第一次样本外读数，"
        "不是确认性证据。"
    ),
    "run_in": (
        "登记后约前 22 个会话为预声明磨合期：发现受绑定代码缺陷则换运行目录与纪元、"
        "重新登记（必须点名本登记），无缺陷则这些会话直接属于确认样本。"
    ),
    "eligibility": (
        "主板、上市 ≥250 会话、非 ST、非停牌、因子有值、流动性前 70%（illiquid = 强制退出），"
        "名称状态 unknown 按普通处理并逐日计数，2025-07-07 之后 5% 带宽证人恒为 0。"
    ),
    "wording": "候选，纸面账簿权益不是证据，N=10 单年结果主要由运气决定。",
}
"""The registration's pass criteria (plan section 1.3), free JSON because `registry.register` only
serialises it: the windows, the criterion, the family, the run-in and the eligibility rule the
forward report and the protocol document are read against. Every value is a candidate, never a
verdict."""


def registration_body(epoch: date) -> tuple[dict[str, Any], dict[str, str], dict[str, Any]]:
    """The registration's `(config, criteria, settings)` for `epoch`: plan sections 1.1 and 1.2.

    Pure: returns fresh copies, so a caller changing one cannot move the frozen literals."""
    config = p7_smoke_env.smoke_configuration(epoch)
    settings: dict[str, Any] = {
        **grid.protocol_settings(),
        "survivor_forward_protocol": copy.deepcopy(FORWARD_PROTOCOL),
        "survivor_forward_power": copy.deepcopy(FORWARD_POWER),
        "survivor_forward_stop_rule": strategy_survivor_forward.adopted_forward_stop_rule(),
    }
    return config, copy.deepcopy(CRITERIA), settings


def read_back(
    config: Mapping[str, Any], criteria: Mapping[str, str], settings: Mapping[str, Any]
) -> None:
    """Read the body with every reader the daily command and the weekly report use: a body that
    does not read back here would be refused at the first evening or the first report."""
    seed = settings.get("random_seed")
    strategy_survivor.read_survivor_registration(
        config, seed=seed if isinstance(seed, int) else None
    )
    protocol = strategy_survivor_forward.read_forward_protocol(settings)
    strategy_survivor_forward.read_forward_power(settings, protocol=protocol)
    strategy_survivor_forward.read_forward_stop_rule(settings, protocol=protocol)
    strategy_survivor_continuation.read_survivor_continuation(settings)
    if not criteria:
        raise RegistrationWriterError("the registration's criteria are empty")
