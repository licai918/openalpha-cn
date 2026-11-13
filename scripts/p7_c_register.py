"""The `V2-P7-013` agent-forward (plan C) registration's writer: the file that writes C once.

C's registration is written **once**, by hand, at gated step L5, after the D1 power report exists
and after the protocol document is committed. It binds the whole of `src/`, `scripts/research/`,
`pyproject.toml`, `uv.lock` **and C's two shipped scripts** (`scripts/agent_evening.py` and
`scripts/agent_report.py`, `strategy_agent_forward.AGENT_SCRIPTS`), so its `code_commit` is the
commit whose bytes produce C's records. That is why this writer lives in `scripts/` beside the
smoke scripts and not under a bound path: landing it inside a bound path would change the bytes the
registration binds. A later change to this file therefore moves no registration's binding, and
that is wanted, because it runs once.

## What the body freezes (D1/D2/D3/D4/D5/D7/D8)

`config` carries the three sample parameters C's evening command reads with no default -- `kind`,
`salt` and `primary_size` -- and `criteria` is the free JSON text of the decisions. `settings` is
`grid.protocol_settings()` plus five closed companion blocks that the evaluator, the report and
the chain test read by name:

- `agent_forward_protocol` (`agent-forward-protocol/v1`): D2's one primary statistic
  (the LLM family, the primary set, h=1), the three exploratory arms D5 names, the 120-session
  window, the D3 floors (`min_scored` 10, `min_measurable_sessions` 60), D5's interim at session
  60, the one-sided `level` 0.05, the exploratory `q` 0.10, and the sign-flip test's
  `bootstrap_samples`/`random_seed`;
- `agent_forward_power` (`agent-forward-power/v1`): D1's choice -- `sample` (`n300`/`census`), its
  `verdict`, whether the not-testable branch was the user's explicit `--register-untestable`
  decision, the power report's sha256, and the chosen row's measurable sessions and detectable
  mean;
- `agent_forward_step0` (`agent-step0-registration/v1`): the committed Step 0 registration C
  evaluates -- its repository-relative path, sha256, `config_id` and `seed`;
- `agent_forward_anchor` (`agent-forward-anchor/v1`): `--anchor`, the registered first forward
  session the evaluator's window starts at;
- `agent_forward_protocol_document` (`agent-forward-protocol-document/v1`): fix round 1's F7 --
  the committed protocol text's repository-relative path and sha256, so the registration names
  the exact prose it froze.

## The D1 power decision is made before the body, never by code choosing a threshold

The writer reads the `--power-report` document `scripts/p7_c_power.py` produced (zero requests, a
stored panel only), and refuses one that does not name this Step 0 registration's committed sha256
and `config_id` or that was planned under other floors (fix round 1, F5). It selects N=300 when
that row is `testable`; otherwise it selects the census
when that row is `testable` (`primary_size = CENSUS_DRAW_SIZE`, a draw large enough that the sample
is the whole survivor set); when neither is testable it **refuses**, because "register C as
not testable, or do not register it at all" is the user's choice at L5, not the writer's. The only
way to write that registration is the explicit `--register-untestable`, which the body records.
`--register-untestable` with a testable chosen set is refused in the other direction. The
detectable mean is design information; D1 ruled no effect-size threshold.

Fix round 2, N1: the planning values the decision was computed under are not the operator's to
choose at L5. The report's `planning.sd_between` must equal the calculator's own frozen
`PLANNING_SD_BETWEEN` (0.15), its abstention rate must be a fraction in [0, 1), and each row's
`detectable_mean` is **recomputed** from that row's `sd` and `periods` (at the registered level and
target power, capped at 1 as the calculator caps it) and refused on any mismatch. Under the frozen
sd both rows are `not_testable` for every panel, so D1's real outcome is "register as not testable
or do not register"; that is the user's L5 choice, and the writer records it.

## The signature deviation from the plan's sketch

Plan C task 3.3 sketched `c_registration_body(*, salt, step0_registration)`; the body also needs
the D1 power decision, the registered window anchor and the committed protocol text, so the shipped
constructor is
`c_registration_body(*, salt, step0_registration, power, anchor, protocol_document)` and returns
`(config, criteria, settings)`. Everything else the plan sketched is here.

## What the writer refuses, each by name and with nothing written

- an unverified vendor endpoint: `DEEPSEEK_V4_1_FLASH.verified_on` is `None` until gated step L2,
  so C is not registered against a model a human has not verified;
- a salt that is, or carries, Step 0's registered seed
  (`domain.agent_forward.refuse_a_salt_tied_to_the_book`), re-checked at read-back;
- Step 0's registration absent, uncommitted, edited after its commit, or of another `config.kind`;
- a missing or malformed power report, one that does not name this Step 0 registration or not the
  adopted planning floors (including the frozen `sd_between` and an abstention rate in [0, 1)),
  a row whose verdict or detectable mean its own fields do not support, and both rows
  `not_testable` without the user's flag (or the flag with a testable chosen set);
- an anchor that is a weekend, before today (Shanghai), or whose 16:30 publication instant has
  already passed at write time (N2: the commit can only come later, so that anchor is certain to
  precede it); the anchor is the first trading session strictly after C's registration commit's
  time, and the report checks the exact session against the commit's calendar;
- an uncommitted, dirty or edited protocol document (F7: the registration freezes its committed
  bytes), and a path outside `docs/research/*.json`;
- an uncommitted bound tree (`registry.REGISTERED_PATHS`, `AGENT_SCRIPTS` and the protocol text);
- a different registration already at the path, or any other registry refusal;
- a written digest that differs from the printed one (the file just written is removed).

## The read-back is the acceptance test at write time

`c_read_back` rebuilds the canonical document, reads it with `agent_evening.read_agent_registration`
-- the command that will run it -- and with every companion-block reader the evaluator and report
use, re-runs the salt rule, checks the recorded Step 0 identity against the committed registration,
re-checks the frozen protocol document's committed sha256, and refuses criteria that are empty or
claim a verdict. A body that does not read back here would be
refused at the first evening or the first report, after the window had already started.

## What it never does

The writer never stages, commits or pushes: the registration's commit is the user's step and the
forward clock starts from it. It makes no request and reads no panel. Its own file and the power
calculator are deliberately outside the bound set. It refuses to write the real
`docs/research/p7-c-registration.json` outside a repository whose bound tree is clean, and every
test of this module runs in a throwaway repository. Wording is candidate (`候选`) throughout:
engineering completion is not research validity.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import os
import re
import subprocess
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Final, Literal
from zoneinfo import ZoneInfo

_SCRIPTS: Final[Path] = Path(__file__).resolve().parent
for _root in (_SCRIPTS, _SCRIPTS / "research"):
    if str(_root) not in sys.path:
        sys.path.insert(0, str(_root))

import agent_evening  # noqa: E402
import grid  # noqa: E402
import p7_c_power  # noqa: E402
import p7_register  # noqa: E402
import registry  # noqa: E402

from openalpha_cn import strategy_agent_evaluation, strategy_survivor  # noqa: E402
from openalpha_cn.domain.agent_forward import (  # noqa: E402
    AGENT_LLM_FAMILY,
    refuse_a_salt_tied_to_the_book,
)
from openalpha_cn.domain.incremental_ic import detectable_mean  # noqa: E402
from openalpha_cn.models.deepseek import DEEPSEEK_V4_1_FLASH  # noqa: E402
from openalpha_cn.panel_ingest import session_publication_instant  # noqa: E402
from openalpha_cn.strategy_agent_evaluation import (  # noqa: E402
    ADOPTED_INTERIM_SESSION,
    ADOPTED_LEVEL,
    ADOPTED_MIN_MEASURABLE_SESSIONS,
    ADOPTED_MIN_SCORED,
    ADOPTED_Q,
    ADOPTED_SECONDARY_ARMS,
    ADOPTED_WINDOW_SESSIONS,
    AGENT_FORWARD_ANCHOR_KEY,
    AGENT_FORWARD_ANCHOR_SCHEMA,
    AGENT_FORWARD_POWER_KEY,
    AGENT_FORWARD_POWER_SCHEMA,
    AGENT_FORWARD_PROTOCOL_DOCUMENT_KEY,
    AGENT_FORWARD_PROTOCOL_DOCUMENT_SCHEMA,
    AGENT_FORWARD_PROTOCOL_KEY,
    AGENT_FORWARD_PROTOCOL_SCHEMA,
    AGENT_FORWARD_STEP0_KEY,
    AGENT_FORWARD_STEP0_SCHEMA,
    DETECTABLE_AT_POWER,
    AgentForwardProtocolDocument,
)
from openalpha_cn.strategy_agent_forward import AGENT_SCRIPTS  # noqa: E402

C_REGISTRATION_PATH: Final[str] = "docs/research/p7-c-registration.json"
"""C's registration, written once at L5 and committed by the user; the writer's default path."""
RESEARCH_DIRECTORY: Final[str] = p7_register.RESEARCH_DIRECTORY
"""The only directory a registration may be written in (`docs/research`)."""
AGENT_REGISTRATION_KIND: Final[str] = agent_evening.AGENT_REGISTRATION_KIND
"""What C's configuration declares itself: never a survivor or a P6 configuration, so it is
structurally outside `p7_register.committed_survivor_registrations`' chain."""
PRIMARY_SIZE: Final[int] = 300
"""D1's primary draw size: the plan's random ~300-survivor sample."""
CENSUS_DRAW_SIZE: Final[int] = 10**6
"""D1's census draw: a size at or above any survivor count, so `primary_sample` returns the census
(the draw is the whole set; the salt only orders it)."""
POWER_REPORT_SCHEMA: Final[str] = "openalpha-p7-c-power/v1"
"""The document `scripts/p7_c_power.py` writes and `--power-report` reads (D1's two rows)."""
PROTOCOL_DOCUMENT_PATH: Final[str] = "docs/research/p7-c-protocol.md"
"""The protocol text C's registration freezes by committed bytes (fix round 1, F7)."""
POWER_DETECTABLE_MEAN_THRESHOLD: Final[float] = 0.02
"""D1/F4's adopted detectable-mean threshold: a power row is testable only at or below it."""
POWER_MIN_OBSERVED_SESSIONS: Final[int] = 20
"""D1/F4's adopted floor on observed sessions before a row may project the window."""
POWER_PLANNING: Final[dict[str, object]] = {
    "window_sessions": ADOPTED_WINDOW_SESSIONS,
    "min_scored_names": ADOPTED_MIN_SCORED,
    "min_measurable_sessions": ADOPTED_MIN_MEASURABLE_SESSIONS,
    "test_level": ADOPTED_LEVEL,
    "target_power": DETECTABLE_AT_POWER,
    "detectable_mean_threshold": POWER_DETECTABLE_MEAN_THRESHOLD,
    "min_observed_sessions": POWER_MIN_OBSERVED_SESSIONS,
    "sd_between": p7_c_power.PLANNING_SD_BETWEEN,
}
"""The plan a `--power-report` must carry for D1 to decide C's sample (fix round 1, F5; the
planning sd is frozen at the power calculator's own constant by fix round 2, N1)."""
C_SCRIPTS: Final[tuple[str, ...]] = AGENT_SCRIPTS
"""C's two shipped scripts, one spelling with `strategy_agent_forward.AGENT_SCRIPTS`: the
registration writer, the evening command's admission and the report all bind the same pair."""
BOUND_PATHS: Final[tuple[str, ...]] = (
    *registry.REGISTERED_PATHS,
    *C_SCRIPTS,
    PROTOCOL_DOCUMENT_PATH,
)
"""What must be clean at `HEAD` for the registration's `code_commit` to be the tree's bytes: the
registry's paths, C's own scripts and the protocol text the registration freezes (the writer
and the power calculator are not bound)."""
FORBIDDEN_WORDING: Final[tuple[str, ...]] = ("proven", "验证有效", "已证实", "已验证", "证实有效")
"""Claim words the criteria may never carry: C is 候选, never a verified result."""
SHA256_ALGORITHM: Final[str] = "sha256"
_FULL_COMMIT: Final[re.Pattern[str]] = re.compile(r"[0-9a-f]{40}")
_HEX_SHA256: Final[re.Pattern[str]] = re.compile(r"[0-9a-f]{64}")
_SHANGHAI: Final[ZoneInfo] = ZoneInfo("Asia/Shanghai")
_NEXT_STEP: Final[str] = (
    "next: stage and commit only this one file yourself -- the message names V2-P7-013 -- then "
    "run the registration chain test; this writer never stages, commits or pushes anything."
)
"""What to do after a successful write. It names the step in words rather than as a shell line: the
writer is the only thing here that touches a registration, and it is not a commit helper."""


class RegistrationWriterError(ValueError):
    """A C registration that cannot be written as asked; nothing is written."""


class CheckoutRefusedError(RegistrationWriterError):
    """The checkout cannot be read, or `HEAD` is not a full commit."""


class BoundTreeRefusedError(RegistrationWriterError):
    """The bound code is not clean: the registration's `code_commit` would not be the tree's."""


class PathRefusedError(RegistrationWriterError):
    """The registration path is not a `.json` file under `docs/research/`."""


class AnchorRefusedError(RegistrationWriterError):
    """The anchor is not a weekday, is in the future, or is older than the lookback bound."""


class EndpointRefusedError(RegistrationWriterError):
    """The vendor endpoint a human must verify (L2) is still a placeholder."""


class SaltRefusedError(RegistrationWriterError):
    """The salt is, or carries, Step 0's registered seed."""


class Step0RefusedError(RegistrationWriterError):
    """Step 0's registration is not a committed survivor registration this writer can bind."""


class PowerReportRefusedError(RegistrationWriterError):
    """The D1 power report is missing, malformed, or says the chosen set is not testable."""


class ExistingRegistrationError(RegistrationWriterError):
    """The path already holds a different registration; a registration is written once."""


class DigestMismatchError(RegistrationWriterError):
    """The bytes `registry.register` wrote are not the bytes this writer printed."""


class RegistrationRefusedError(RegistrationWriterError):
    """`registry.register` refused for a reason other than an existing, different registration."""


class CriteriaRefusedError(RegistrationWriterError):
    """The criteria are empty, not text, or claim a verdict C never established."""


class ProtocolDocumentRefusedError(RegistrationWriterError):
    """The protocol document C's registration freezes is missing, uncommitted, dirty or changed."""


@dataclass(frozen=True, slots=True, kw_only=True)
class Step0Registration:
    """The committed Step 0 registration C evaluates: its path, digest, identity and book seed."""

    path: str
    registration_sha256: str
    config_id: str
    seed: int

    def __post_init__(self) -> None:
        if not self.path.strip():
            raise ValueError("Step 0's registration path must not be blank")
        if not _HEX_SHA256.fullmatch(self.registration_sha256):
            raise ValueError(
                f"Step 0's registration sha256 {self.registration_sha256!r} is not 64 hex digits"
            )
        if not self.config_id.strip():
            raise ValueError("Step 0's registration names no config_id")
        if isinstance(self.seed, bool) or not isinstance(self.seed, int):
            raise ValueError(f"Step 0's registered seed is {self.seed!r}, not an integer")


@dataclass(frozen=True, slots=True, kw_only=True)
class PowerDecision:
    """D1's selection: the chosen row, the user's untestable decision, and its report."""

    sample: Literal["n300", "census"]
    verdict: Literal["testable", "not_testable"]
    register_untestable: bool
    report_sha256: str
    measurable_sessions: int
    detectable_mean: float | None

    def __post_init__(self) -> None:
        if self.sample not in {"n300", "census"}:
            raise ValueError(f"the power decision's sample is {self.sample!r}")
        if self.verdict not in {"testable", "not_testable"}:
            raise ValueError(f"the power decision's verdict is {self.verdict!r}")
        if (self.verdict == "not_testable") != self.register_untestable:
            raise ValueError(
                "the power decision must record register_untestable exactly when the chosen set "
                "is not_testable: an untestable set is the user's explicit choice, and a testable "
                "one takes no flag"
            )
        if not _HEX_SHA256.fullmatch(self.report_sha256):
            raise ValueError(f"the power report sha256 {self.report_sha256!r} is not 64 hex digits")
        if (
            isinstance(self.measurable_sessions, bool)
            or not isinstance(self.measurable_sessions, int)
            or self.measurable_sessions < 0
        ):
            raise ValueError(
                f"the chosen power row's measurable_sessions is {self.measurable_sessions!r}"
            )
        if self.detectable_mean is not None and (
            isinstance(self.detectable_mean, bool)
            or not isinstance(self.detectable_mean, int | float)
            or not math.isfinite(self.detectable_mean)
            or self.detectable_mean < 0
        ):
            raise ValueError(f"the chosen power row's detectable_mean is {self.detectable_mean!r}")


CRITERIA: Final[dict[str, str]] = {
    "primary": (
        "主统计量（D5 唯一一条）：LLM 家族 agent_llm_structured/v1 在主集 primary 上的 h=1 偏秩 IC"
        "（对 Step 0 已存合成分残差化）。窗口是 C 登记提交之后的前 120 个前向会话，"
        "锚点在登记时冻结。"
        "单侧符号翻转检验 alpha=0.05，H1：均值 > 0，通过 iff 窗口均值 > 0 且单侧读数 <= 0.05。"
        "量级只描述、不是门槛。这是候选判据，不是已经成立的结果。"
    ),
    "futility": (
        "中期无效性（D5 采纳，保留全部守卫）：前 60 个位置全部到期且其中至少按比例可测下限"
        "（max(2, ceil(min_measurable_sessions * 60 / 120)) = 30）个位置带数值时，累积均值 <= 0 "
        "即停止 futility_stop，停止是 sticky。薄的或未到期的中期不是停止。"
    ),
    "interim": (
        "中期（D5）：只在第 60 个位置做一次中期看，报计数与描述性均值。窗口未满不给 p 值、"
        "不给结论词。"
    ),
    "not_measurable": (
        "可测性（D3/D5）：一个会话在 join 之后主集上至少 10 个记了分的名字才可测。120 个位置里"
        "可测少于 60 个即 not_measurable。这是它自己的正式结论，绝不读成「没有证据」，也不给 p 值。"
    ),
    "exploratory": (
        "探索性块（永远探索性）：规则家族 agent_rule_baseline/v1、h=5 与覆盖集三条臂合成一个 "
        "Benjamini-Yekutieli 族，q=0.10。只在主窗口满足之后才检验，结果不改变主结论，也永不升级为"
        "第二个主统计量。"
    ),
    "evidence_roster": (
        "证据名册 v1（D4）：v1 只有 V2-P7-011 的涨停、连板、炸板事件（limit_up、consecutive_board、"
        "broken_board，market_event 族）。资金流（moneyflow）、公告等不在 v1，须另立登记。"
    ),
    "evidence": (
        "确认性证据只来自本登记提交之后的 C 前向记录。测试通过、命令能跑都不是证据，"
        "任何读数都只到候选。"
    ),
    "power": (
        "功效（D1）：scripts/p7_c_power.py 在已存面板上零请求算出 n300 与 census 两行并逐行判定 "
        "testable 或 not_testable。n300 可测则主集取 N=300，只有普查（census）可测则主集取普查"
        "（primary_size=1000000 即不截断），两行都不可测时只有用户明确 --register-untestable "
        "才登记为不可测，否则写入器拒绝写入。可检出均值只是设计信息，不是效应量门槛。"
    ),
    "dry_week": (
        "干跑周（D7）：登记之前至多 5 个晚间、每晚上至多 600 次模型请求，只看运行指标"
        "（失败率、空白率、弃权率、无效输出率、延迟、tokens），绝不计算 IC 或收益。"
        "记录写在独立的临时运行目录，永远不被评估器或报告读取。"
    ),
    "report": (
        "报告（D8）：C 的报告是独立的 scripts/agent_report.py。Step 0 的周报脚本 "
        "scripts/forward_report.py 的字节不变、不被修改。"
    ),
    "model": (
        "模型身份（L2 未做）：deepseek-flash 是移动别名。每次模型交换记录 response.model 与 "
        "system_fingerprint，别名变化只报告、不重登记。thinking 关闭、temperature 0、错峰运行。"
        "DEEPSEEK_V4_1_FLASH.verified_on 仍为 None，L2 之前 live 调用被拒绝。"
    ),
    "forward_only": (
        "只前向（V2-P7-015）：只有本登记提交之后的 C 前向记录进入样本。不重放历史、"
        "不做历史 LLM 评估。沿用 72 小时滞后护栏。"
    ),
    "wording": (
        "候选（candidate）：一切结论词的强度只到候选。工程完成不是研究证据，"
        "测试通过不把任何一行升级。"
    ),
}
"""C's registered pass criteria (plan C sections 5-7): free JSON because `registry.register` only
serialises it, and read by the report, the protocol document and this suite. Every value is a
candidate, never a verdict."""


def _protocol_block() -> dict[str, Any]:
    """D2/D3/D5's `agent-forward-protocol/v1` block: every field required, no defaults."""
    return {
        "schema_version": AGENT_FORWARD_PROTOCOL_SCHEMA,
        "primary_family": AGENT_LLM_FAMILY,
        "primary_set": "primary",
        "primary_horizon": 1,
        "secondary": [
            {"family": family, "scoring_set": scoring_set, "horizon": horizon}
            for family, scoring_set, horizon in ADOPTED_SECONDARY_ARMS
        ],
        "window_sessions": ADOPTED_WINDOW_SESSIONS,
        "min_scored": ADOPTED_MIN_SCORED,
        "min_measurable_sessions": ADOPTED_MIN_MEASURABLE_SESSIONS,
        "interim_session": ADOPTED_INTERIM_SESSION,
        "level": ADOPTED_LEVEL,
        "q": ADOPTED_Q,
        "bootstrap_samples": grid.PROTOCOL_BOOTSTRAP_SAMPLES,
        "random_seed": grid.PROTOCOL_RANDOM_SEED,
    }


def _power_block(power: PowerDecision) -> dict[str, Any]:
    """D1's `agent-forward-power/v1` block: the chosen row and the report it came from."""
    return {
        "schema_version": AGENT_FORWARD_POWER_SCHEMA,
        "sample": power.sample,
        "verdict": power.verdict,
        "register_untestable": power.register_untestable,
        "report_sha256": power.report_sha256,
        "measurable_sessions": power.measurable_sessions,
        # A JSON integer zero is a fine detectable mean; the block's field is a strict float.
        "detectable_mean": None if power.detectable_mean is None else float(power.detectable_mean),
    }


def _step0_block(step0_registration: Step0Registration) -> dict[str, Any]:
    """`agent-step0-registration/v1`: the committed Step 0 registration C evaluates."""
    return {
        "schema_version": AGENT_FORWARD_STEP0_SCHEMA,
        "path": step0_registration.path,
        "registration_sha256": step0_registration.registration_sha256,
        "config_id": step0_registration.config_id,
        "seed": step0_registration.seed,
    }


def _anchor_block(anchor: date) -> dict[str, Any]:
    """`agent-forward-anchor/v1`: C's first forward session, the evaluator's window anchor."""
    return {"schema_version": AGENT_FORWARD_ANCHOR_SCHEMA, "session": anchor.isoformat()}


def _protocol_document_block(
    protocol_document: AgentForwardProtocolDocument,
) -> dict[str, Any]:
    """`agent-forward-protocol-document/v1`: the committed protocol text C freezes (F7)."""
    return {
        "schema_version": AGENT_FORWARD_PROTOCOL_DOCUMENT_SCHEMA,
        "path": protocol_document.path,
        "sha256": protocol_document.sha256,
    }


def c_registration_body(
    *,
    salt: str,
    step0_registration: Step0Registration,
    power: PowerDecision,
    anchor: date,
    protocol_document: AgentForwardProtocolDocument,
) -> tuple[dict[str, Any], dict[str, str], dict[str, Any]]:
    """C's `(config, criteria, settings)` for the registered salt, Step 0 binding, D1 decision,
    anchor and committed protocol text.

    Pure: returns fresh copies, so a caller changing one cannot move the frozen literals. The
    signature is a documented deviation from plan C task 3.3's two-argument sketch: the D1 power
    decision, the registered window anchor and the frozen protocol document are part of the body,
    so the body cannot be built without them (see the module docstring). `primary_size` is the
    sample the D1 decision chose: `PRIMARY_SIZE` for `n300`, `CENSUS_DRAW_SIZE` for `census`.
    """
    primary_size = PRIMARY_SIZE if power.sample == "n300" else CENSUS_DRAW_SIZE
    config: dict[str, Any] = {
        "kind": AGENT_REGISTRATION_KIND,
        "salt": salt,
        "primary_size": primary_size,
    }
    settings: dict[str, Any] = {
        **grid.protocol_settings(),
        AGENT_FORWARD_PROTOCOL_KEY: _protocol_block(),
        AGENT_FORWARD_POWER_KEY: _power_block(power),
        AGENT_FORWARD_STEP0_KEY: _step0_block(step0_registration),
        AGENT_FORWARD_ANCHOR_KEY: _anchor_block(anchor),
        AGENT_FORWARD_PROTOCOL_DOCUMENT_KEY: _protocol_document_block(protocol_document),
    }
    return config, copy.deepcopy(CRITERIA), settings


# --- the D1 power report ------------------------------------------------------------------------


@dataclass(frozen=True, slots=True, kw_only=True)
class _PowerRow:
    """One validated row of the power report: at least what the decision and the block read."""

    sample: str
    verdict: str
    measurable_sessions: int
    detectable_mean: float | None


@dataclass(frozen=True, slots=True, kw_only=True)
class _PowerPlan:
    """The planning values a row's verdict and detectable mean are recomputed under."""

    min_observed_sessions: int
    min_measurable_sessions: int
    level: float
    power: float


def _is_iso_date(value: object) -> bool:
    if not isinstance(value, str):
        return False
    try:
        date.fromisoformat(value)
    except ValueError:
        return False
    return True


def _finite_number(value: object, *, field: str, label: str, path: Path) -> float | None:
    """A finite non-negative JSON number, or `None` when the field is null; else a refusal."""
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise PowerReportRefusedError(
            f"the power report {path}'s {label!r} row {field} is {value!r}, not a number or null"
        )
    if not math.isfinite(value) or value < 0:
        raise PowerReportRefusedError(
            f"the power report {path}'s {label!r} row {field} is {value!r}, not a finite "
            "non-negative number"
        )
    return float(value)


def _power_row(
    block: object,
    *,
    label: str,
    path: Path,
    planning: _PowerPlan,
) -> _PowerRow:
    """One row of the power report, or a refusal naming the field that is not the contract's.

    Fix round 1 (F5): the row's `verdict` must be exactly what its own fields support, so a
    hand-written `"verdict": "testable"` cannot pass D1's gate:
    testable iff `observed_sessions >= min_observed_sessions` and
    `measurable_sessions >= min_measurable_sessions` and a detectable mean exists at or below the
    row's registered threshold.
    """
    if not isinstance(block, Mapping):
        raise PowerReportRefusedError(
            f"the power report {path}'s {label!r} row is {type(block).__name__}, not an object"
        )
    sample = block.get("sample")
    if sample != label:
        raise PowerReportRefusedError(
            f"the power report {path}'s {label!r} row names sample {sample!r}, not {label!r}"
        )
    size = block.get("sample_size")
    if size is not None and (isinstance(size, bool) or not isinstance(size, int) or size < 1):
        raise PowerReportRefusedError(
            f"the power report {path}'s {label!r} row sample_size is {size!r}, not a positive "
            "integer or null"
        )
    counts: dict[str, int] = {}
    for field in (
        "observed_sessions",
        "observed_measurable_sessions",
        "measurable_sessions",
        "periods",
    ):
        value = block.get(field)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise PowerReportRefusedError(
                f"the power report {path}'s {label!r} row {field} is {value!r}, not a "
                "non-negative integer"
            )
        counts[field] = value
    projected = block.get("projected_scored_names")
    if projected is not None and (
        isinstance(projected, bool) or not isinstance(projected, int) or projected < 0
    ):
        raise PowerReportRefusedError(
            f"the power report {path}'s {label!r} row projected_scored_names is {projected!r}, "
            "not a non-negative integer or null"
        )
    sd = _finite_number(block.get("sd"), field="sd", label=label, path=path)
    if sd is not None and sd <= 0:
        raise PowerReportRefusedError(
            f"the power report {path}'s {label!r} row sd is {sd!r}, not a positive number; the "
            "calculator's session sd has a positive planning part"
        )
    mean = _finite_number(
        block.get("detectable_mean"), field="detectable_mean", label=label, path=path
    )
    periods = counts["periods"]
    recomputed: float | None = None
    if sd is not None and periods >= 2:
        try:
            recomputed = min(
                detectable_mean(sd=sd, periods=periods, level=planning.level, power=planning.power),
                1.0,
            )
        except ValueError as error:
            raise PowerReportRefusedError(
                f"the power report {path}'s {label!r} row sd/periods do not form a design: {error}"
            ) from error
    if mean is None:
        if recomputed is not None:
            raise PowerReportRefusedError(
                f"the power report {path}'s {label!r} row detectable_mean is null but its own sd "
                f"{sd!r} and periods {periods} give {recomputed!r}; the field is recomputed from "
                "the row, never trusted"
            )
    elif recomputed is None:
        raise PowerReportRefusedError(
            f"the power report {path}'s {label!r} row detectable_mean is {mean!r} but the row has "
            f"no positive sd and at least two periods (sd {sd!r}, periods {periods}) to recompute "
            "it from"
        )
    elif not math.isclose(mean, recomputed, rel_tol=1e-9, abs_tol=1e-12):
        raise PowerReportRefusedError(
            f"the power report {path}'s {label!r} row detectable_mean is {mean!r}, not the "
            f"{recomputed!r} its own sd {sd!r} and periods {periods} give (fix round 2, N1: the "
            "mean is recomputed, never trusted)"
        )
    threshold = _finite_number(
        block.get("detectable_threshold"),
        field="detectable_threshold",
        label=label,
        path=path,
    )
    if threshold != POWER_DETECTABLE_MEAN_THRESHOLD:
        raise PowerReportRefusedError(
            f"the power report {path}'s {label!r} row detectable_threshold is {threshold!r}, not "
            f"the adopted {POWER_DETECTABLE_MEAN_THRESHOLD!r}"
        )
    reason = block.get("reason")
    if reason is not None and not isinstance(reason, str):
        raise PowerReportRefusedError(
            f"the power report {path}'s {label!r} row reason is {reason!r}, not text or null"
        )
    verdict = block.get("verdict")
    if verdict not in {"testable", "not_testable"}:
        raise PowerReportRefusedError(
            f"the power report {path}'s {label!r} row verdict is {verdict!r}, not "
            "'testable' or 'not_testable'"
        )
    expected_testable = (
        counts["observed_sessions"] >= planning.min_observed_sessions
        and counts["measurable_sessions"] >= planning.min_measurable_sessions
        and mean is not None
        and mean <= POWER_DETECTABLE_MEAN_THRESHOLD
    )
    if (verdict == "testable") != expected_testable:
        raise PowerReportRefusedError(
            f"the power report {path}'s {label!r} row says {verdict!r} but its own fields say "
            f"{'testable' if expected_testable else 'not_testable'}: observed_sessions "
            f"{counts['observed_sessions']}, measurable_sessions "
            f"{counts['measurable_sessions']}, detectable_mean {mean!r} (threshold "
            f"{POWER_DETECTABLE_MEAN_THRESHOLD})"
        )
    return _PowerRow(
        sample=label,
        verdict=verdict,
        measurable_sessions=counts["measurable_sessions"],
        detectable_mean=mean,
    )


def _power_planning(document: Mapping[str, object], *, path: Path) -> _PowerPlan:
    """The report's planning object, held to the adopted D1 plan (F5; frozen sd N1)."""
    planning = document.get("planning")
    if not isinstance(planning, Mapping):
        raise PowerReportRefusedError(
            f"the power report {path} has no planning object; the D1 decision is read under the "
            "plan it was computed with, not under a default"
        )
    for key, expected in POWER_PLANNING.items():
        value = planning.get(key)
        if value != expected:
            raise PowerReportRefusedError(
                f"the power report {path}'s planning.{key} is {value!r}, not the adopted "
                f"{expected!r}"
            )
    abstention = planning.get("abstention_rate")
    if (
        isinstance(abstention, bool)
        or not isinstance(abstention, int | float)
        or not math.isfinite(abstention)
    ):
        raise PowerReportRefusedError(
            f"the power report {path}'s planning.abstention_rate is {abstention!r}, not a finite "
            "number; the projection's abstention assumption is reported, not defaulted"
        )
    if not 0 <= abstention < 1:
        raise PowerReportRefusedError(
            f"the power report {path}'s planning.abstention_rate is {abstention!r}, not a fraction "
            "in [0, 1): the calculator's own bound"
        )
    return _PowerPlan(
        min_observed_sessions=POWER_MIN_OBSERVED_SESSIONS,
        min_measurable_sessions=ADOPTED_MIN_MEASURABLE_SESSIONS,
        level=ADOPTED_LEVEL,
        power=DETECTABLE_AT_POWER,
    )


def _names_the_same_registration(value: object, path: str) -> bool:
    """Whether `value` names the committed registration at repo-relative `path`.

    The power command records the path it was given, and the protocol runs it with a path
    relative to the repository; a user who passes an absolute path must not be refused by name
    alone, so the repository-relative tail is accepted too. The sha256 and config_id checked
    beside this are what actually bind the registration.
    """
    if not isinstance(value, str) or not value.strip():
        return False
    shown = Path(value).as_posix()
    return shown == path or shown.endswith("/" + path)


def _checked_power_source(
    document: Mapping[str, object], *, step0_registration: Step0Registration, path: Path
) -> None:
    """The report must have been computed on this Step 0 registration (fix round 1, F5)."""
    source = document.get("source")
    if not isinstance(source, Mapping):
        raise PowerReportRefusedError(
            f"the power report {path}'s source is {type(source).__name__}, not the Step 0 "
            "registration identity it was computed on; a report whose book C does not evaluate "
            "is not D1's evidence"
        )
    if not _names_the_same_registration(source.get("step0_registration"), step0_registration.path):
        raise PowerReportRefusedError(
            f"the power report {path}'s source.step0_registration is "
            f"{source.get('step0_registration')!r}, not this Step 0 registration "
            f"({step0_registration.path!r}); the D1 decision must be computed on the "
            "registration C evaluates"
        )
    for key, wanted in (
        ("config_id", step0_registration.config_id),
        ("registration_sha256", step0_registration.registration_sha256),
    ):
        value = source.get(key)
        if value != wanted:
            raise PowerReportRefusedError(
                f"the power report {path}'s source.{key} is {value!r}, not this Step 0 "
                f"registration's {wanted!r}; the D1 decision must be computed on the registration "
                "C evaluates"
            )
    code_commit = source.get("code_commit")
    if not isinstance(code_commit, str) or not code_commit.strip():
        raise PowerReportRefusedError(
            f"the power report {path}'s source names no Step 0 code_commit"
        )


def _read_power(
    path_value: str | Path,
    *,
    step0_registration: Step0Registration,
    register_untestable: bool,
) -> PowerDecision:
    """D1's selection from the `POWER_REPORT_SCHEMA` document `--power-report` names.

    The rule is D1's and is applied here, never guessed: `n300` when its row is testable, else the
    census when its row is testable, else a refusal whose only overrides are the user's explicit
    `--register-untestable` (then `n300` is recorded as not testable). A missing, unreadable,
    wrong-schema or malformed report is a refusal, not a default; so is one that does not name this
    Step 0 registration under the adopted plan (fix round 1, F5).
    """
    path = Path(path_value)
    if not path.is_file():
        raise PowerReportRefusedError(
            f"the power report {path} is not a file; --power-report names the "
            f"{POWER_REPORT_SCHEMA} document scripts/p7_c_power.py writes at L5"
        )
    try:
        content = path.read_bytes()
    except OSError as error:
        raise PowerReportRefusedError(f"the power report {path} cannot be read: {error}") from error
    try:
        document = json.loads(content)
    except ValueError as error:
        raise PowerReportRefusedError(f"the power report {path} is not JSON: {error}") from error
    if not isinstance(document, Mapping):
        raise PowerReportRefusedError(
            f"the power report {path} is {type(document).__name__}, not a "
            f"{POWER_REPORT_SCHEMA} object"
        )
    if document.get("schema") != POWER_REPORT_SCHEMA:
        raise PowerReportRefusedError(
            f"the power report {path} declares schema {document.get('schema')!r}, not "
            f"{POWER_REPORT_SCHEMA!r}; only the power calculator's report is read"
        )
    for field in ("first", "last"):
        if not _is_iso_date(document.get(field)):
            raise PowerReportRefusedError(
                f"the power report {path} names first/last as {document.get(field)!r}, not ISO "
                "dates"
            )
    planning = _power_planning(document, path=path)
    _checked_power_source(document, step0_registration=step0_registration, path=path)
    rows = document.get("rows")
    if not isinstance(rows, Mapping):
        raise PowerReportRefusedError(
            f"the power report {path}'s 'rows' are {type(rows).__name__}, not the "
            "n300/census mapping the writer reads"
        )
    parsed: dict[str, _PowerRow] = {}
    for label in ("n300", "census"):
        if label not in rows:
            raise PowerReportRefusedError(
                f"the power report {path} has no {label!r} row; D1 decides between N=300 and the "
                "census and requires both rows"
            )
        parsed[label] = _power_row(rows[label], label=label, path=path, planning=planning)
    n300, census = parsed["n300"], parsed["census"]
    if n300.verdict == "testable":
        chosen = n300
    elif census.verdict == "testable":
        chosen = census
    elif register_untestable:
        chosen = n300
    else:
        raise PowerReportRefusedError(
            f"the power report {path} says both the n300 and the census rows are not_testable: "
            "whether to register C as not testable (--register-untestable) or not to register it "
            "at all is the user's L5 choice, and this writer does not make it"
        )
    untestable = chosen.verdict == "not_testable"
    if register_untestable and not untestable:
        raise PowerReportRefusedError(
            "--register-untestable was given but the chosen scoring set "
            f"({chosen.sample}) is testable; the flag exists for the not_testable branch only"
        )
    return PowerDecision(
        sample="n300" if chosen.sample == "n300" else "census",
        verdict="not_testable" if untestable else "testable",
        register_untestable=untestable,
        report_sha256=hashlib.sha256(content).hexdigest(),
        measurable_sessions=chosen.measurable_sessions,
        detectable_mean=chosen.detectable_mean,
    )


# --- Step 0's committed registration ------------------------------------------------------------


def _repository_root(repo: Path) -> Path:
    return Path(repo).resolve()


def _resolve_against(repo: Path, value: str) -> Path:
    """A relative command-line path is relative to the repository, as `--path` and Step 0 are."""
    candidate = Path(value)
    return candidate if candidate.is_absolute() else repo / candidate


def _step0_facts(repo: Path, value: str) -> Step0Registration:
    """Step 0's committed survivor registration, read once for the four facts C records."""
    candidate = Path(value)
    if not candidate.is_absolute():
        candidate = repo / candidate
    try:
        root, admitted = registry.committed_registration(candidate, repo)
    except registry.HoldoutRefusedError as error:
        raise Step0RefusedError(
            f"C reads Step 0's committed registration {value}: {error}"
        ) from error
    body = admitted.registered
    config = body.get("config")
    if not isinstance(config, Mapping):
        raise Step0RefusedError(
            f"{value} registers no configuration object; C cannot name the book it evaluates"
        )
    kind = config.get(strategy_survivor.KIND_KEY)
    if kind != strategy_survivor.SURVIVOR_KIND:
        raise Step0RefusedError(
            f"{value}'s config.kind is {kind!r}, not {strategy_survivor.SURVIVOR_KIND!r}: C "
            "evaluates a Step 0 survivor registration and no other kind"
        )
    settings = body.get("settings")
    if not isinstance(settings, Mapping):
        raise Step0RefusedError(
            f"{value} registers no settings object, so C cannot record Step 0's seed"
        )
    seed = settings.get("random_seed")
    if isinstance(seed, bool) or not isinstance(seed, int):
        raise Step0RefusedError(f"{value}'s settings.random_seed is {seed!r}, not an integer")
    try:
        strategy_survivor.read_survivor_registration(config, seed=seed)
    except strategy_survivor.SurvivorRegistrationError as error:
        raise Step0RefusedError(
            f"{value} does not read as a survivor registration: {error}"
        ) from error
    config_id = body.get("config_id")
    if not isinstance(config_id, str) or not config_id.strip():
        raise Step0RefusedError(f"{value} names no config_id")
    try:
        relative = candidate.resolve().relative_to(_repository_root(root)).as_posix()
    except ValueError as error:
        raise Step0RefusedError(
            f"{value} is outside the repository {root}: only a committed registration of this "
            "repository is read"
        ) from error
    return Step0Registration(
        path=relative,
        registration_sha256=hashlib.sha256(admitted.content).hexdigest(),
        config_id=config_id,
        seed=seed,
    )


def _checked_step_zero(
    repo: Path,
    block: strategy_agent_evaluation.AgentForwardStep0,
    passed: Step0Registration,
) -> None:
    """The read-back block must still name the committed Step 0 registration and its facts."""
    fresh = _step0_facts(repo, block.path)
    problems: list[str] = []
    if fresh.registration_sha256 != block.registration_sha256:
        problems.append(
            f"sha256 {block.registration_sha256} is not the committed {fresh.registration_sha256}"
        )
    if fresh.config_id != block.config_id:
        problems.append(f"config_id {block.config_id} is not the committed {fresh.config_id}")
    if fresh.seed != block.seed:
        problems.append(f"seed {block.seed} is not the committed {fresh.seed}")
    if fresh != passed:
        problems.append("the read-back Step 0 facts are not the facts the writer read")
    if problems:
        raise Step0RefusedError(
            f"the registration's {AGENT_FORWARD_STEP0_KEY} block does not match the committed "
            f"Step 0 registration {block.path}: " + "; ".join(problems)
        )


# --- the canonical document and the read-back ---------------------------------------------------


def _canonical_bytes(
    config: Mapping[str, Any],
    criteria: Mapping[str, Any],
    settings: Mapping[str, Any],
    code_commit: str,
) -> bytes:
    """The bytes `registry.register` writes: the sibling writer's one canonical form."""
    return p7_register._document(config, criteria, settings, code_commit)


def _document(
    config: Mapping[str, Any],
    criteria: Mapping[str, Any],
    settings: Mapping[str, Any],
    code_commit: str,
) -> bytes:
    """The bytes the dry run prints and the write's digest is taken over (the test's patch seam)."""
    return _canonical_bytes(config, criteria, settings, code_commit)


def _refuse_unsafe_criteria(criteria: object) -> None:
    """The criteria must be non-empty text and claim no verdict C never established."""
    if not isinstance(criteria, Mapping):
        raise CriteriaRefusedError(
            f"the registration's criteria are {type(criteria).__name__}, not an object"
        )
    if not criteria:
        raise CriteriaRefusedError("the registration's criteria are empty")
    for key, value in criteria.items():
        if not isinstance(value, str) or not value.strip():
            raise CriteriaRefusedError(
                f"criteria[{key!r}] is not a non-empty string: the registration's criteria are text"
            )
        for forbidden in FORBIDDEN_WORDING:
            if forbidden in value:
                raise CriteriaRefusedError(
                    f"criteria[{key!r}] claims {forbidden!r}; C's wording is 候选 and never a "
                    "verified result"
                )


def c_read_back(
    config: Mapping[str, Any],
    criteria: Mapping[str, Any],
    settings: Mapping[str, Any],
    *,
    code_commit: str,
    step0_registration: Step0Registration,
    repo: Path,
) -> None:
    """Read the body with every reader the evening command, the report and the evaluator use.

    A body that does not read back here would be refused at the first evening or the first report,
    after C's clock had already started; this is the writer's acceptance test.
    """
    content = _canonical_bytes(config, criteria, settings, code_commit)
    try:
        document = json.loads(content)
    except ValueError as error:
        raise RegistrationWriterError(
            f"the registration's canonical document is not JSON: {error}"
        ) from error
    try:
        registration = agent_evening.read_agent_registration(
            document, content=content, commit=code_commit
        )
    except agent_evening.AgentRegistrationError as error:
        raise RegistrationWriterError(
            f"the registration does not read back as {agent_evening.AGENT_REGISTRATION_KIND!r}: "
            f"{error}"
        ) from error
    try:
        protocol = strategy_agent_evaluation.read_agent_protocol(settings)
        power = strategy_agent_evaluation.read_agent_forward_power(settings)
        step0 = strategy_agent_evaluation.read_agent_forward_step0(settings)
        anchor = strategy_agent_evaluation.read_agent_forward_anchor(settings)
        protocol_document = strategy_agent_evaluation.read_agent_forward_protocol_document(settings)
    except strategy_agent_evaluation.AgentEvaluationError as error:
        raise RegistrationWriterError(
            f"the registration's companion blocks do not read back: {error}"
        ) from error
    try:
        refuse_a_salt_tied_to_the_book(registration.salt, book_seed=step0.seed)
    except ValueError as error:
        raise SaltRefusedError(str(error)) from error
    _checked_step_zero(repo, step0, step0_registration)
    _checked_protocol_document(repo, protocol_document)
    expected_primary_size = PRIMARY_SIZE if power.sample == "n300" else CENSUS_DRAW_SIZE
    if registration.primary_size != expected_primary_size:
        raise RegistrationWriterError(
            f"the registration's config.primary_size is {registration.primary_size}, but the "
            f"D1 power block chose {power.sample} and that draw is {expected_primary_size} names; "
            "the body and the power decision disagree"
        )
    registered_arms = [(arm.family, arm.scoring_set, arm.horizon) for arm in protocol.secondary]
    if registered_arms != list(ADOPTED_SECONDARY_ARMS):
        raise RegistrationWriterError(
            f"the registration's exploratory arms {registered_arms} are not D5's adopted "
            f"{list(ADOPTED_SECONDARY_ARMS)}"
        )
    if anchor.session.weekday() >= 5:
        raise AnchorRefusedError(f"the registration's anchor {anchor.session} is not a weekday")
    _refuse_unsafe_criteria(criteria)


# --- the command line's own refusals ------------------------------------------------------------


def _now() -> datetime:
    """The write-time clock, aware in Shanghai: the anchor's 16:30 must still be ahead of it."""
    return datetime.now(_SHANGHAI)


def _git(repo: Path, *args: str) -> str:
    """`p7_register`'s hermetic git, with this writer's refusal."""
    try:
        return p7_register._git(repo, *args)
    except p7_register.RegistrationWriterError as error:
        raise CheckoutRefusedError(str(error)) from error


def _git_bytes(repo: Path, *args: str) -> bytes:
    """Hermetic git whose stdout is the raw bytes a sha256 is taken over.

    `_git` reads text, which a platform's newline translation could change; F7 compares the
    committed protocol document byte for byte, so it needs bytes.
    """
    environment = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    environment |= {
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_OPTIONAL_LOCKS": "0",
        "GIT_TERMINAL_PROMPT": "0",
    }
    try:
        finished = subprocess.run(
            ["git", *args],
            cwd=repo,
            env=environment,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            check=False,
            timeout=p7_register.GIT_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise CheckoutRefusedError(
            f"git {' '.join(args)} in {repo} could not run: {error}"
        ) from error
    if finished.returncode != 0:
        raise CheckoutRefusedError(
            f"git {' '.join(args)} in {repo} failed: "
            f"{finished.stderr.decode(errors='replace').strip() or 'no message'}"
        )
    return finished.stdout


def _repository() -> Path:
    """The top of the checkout the writer runs in."""
    try:
        return p7_register._repository()
    except p7_register.RegistrationWriterError as error:
        raise CheckoutRefusedError(str(error)) from error


def _refuse_a_dirty_bound_tree(repo: Path) -> None:
    dirty = _git(repo, "status", "--porcelain", "--untracked-files=all", "--", *BOUND_PATHS)
    if dirty:
        raise BoundTreeRefusedError(
            f"the bound code of {repo} has uncommitted changes ({dirty.splitlines()[0]} ...): "
            "the registration would name a commit whose bytes are not the tree's"
        )


def _protocol_document(repo: Path) -> AgentForwardProtocolDocument:
    """The committed protocol text C's registration freezes (fix round 1, F7).

    The registration freezes prose, not just code, so the writer requires the file to be
    committed, clean, and identical to `HEAD`'s bytes, and records its sha256; the read-back
    re-checks it against the tree.
    """
    path = repo / PROTOCOL_DOCUMENT_PATH
    if not path.is_file():
        raise ProtocolDocumentRefusedError(
            f"the protocol document {PROTOCOL_DOCUMENT_PATH} is not a file; C's registration "
            "freezes the protocol text written before it"
        )
    dirty = _git(
        repo, "status", "--porcelain", "--untracked-files=all", "--", PROTOCOL_DOCUMENT_PATH
    )
    if dirty:
        raise ProtocolDocumentRefusedError(
            f"the protocol document {PROTOCOL_DOCUMENT_PATH} is not committed and clean "
            f"({dirty.splitlines()[0]} ...); C's registration freezes the committed text, not a "
            "draft"
        )
    content = path.read_bytes()
    committed = _git_bytes(repo, "show", f"HEAD:{PROTOCOL_DOCUMENT_PATH}")
    if committed != content:
        raise ProtocolDocumentRefusedError(
            f"the bytes of {PROTOCOL_DOCUMENT_PATH} on disk are not the ones HEAD holds; C's "
            "registration freezes the committed text"
        )
    return AgentForwardProtocolDocument(
        path=PROTOCOL_DOCUMENT_PATH,
        sha256=hashlib.sha256(committed).hexdigest(),
    )


def _checked_protocol_document(repo: Path, block: AgentForwardProtocolDocument) -> None:
    """The read-back block must name the committed protocol text and its bytes."""
    if block.path != PROTOCOL_DOCUMENT_PATH:
        raise ProtocolDocumentRefusedError(
            f"the registration's {AGENT_FORWARD_PROTOCOL_DOCUMENT_KEY} names {block.path!r}, not "
            f"{PROTOCOL_DOCUMENT_PATH!r}"
        )
    fresh = _protocol_document(repo)
    if block.sha256 != fresh.sha256:
        raise ProtocolDocumentRefusedError(
            f"the registration's {AGENT_FORWARD_PROTOCOL_DOCUMENT_KEY} sha256 is {block.sha256}, "
            f"not the committed {fresh.sha256}; the frozen protocol text changed after the "
            "registration was built"
        )


def _refuse_an_unverified_endpoint() -> None:
    if DEEPSEEK_V4_1_FLASH.verified_on is None:
        raise EndpointRefusedError(
            f"the vendor endpoint {DEEPSEEK_V4_1_FLASH.name} has verified_on None: gated step L2 "
            "-- a human's reading of https://api-docs.deepseek.com/ -- has not filled the "
            "endpoint in, and the writer refuses to register C against a model nobody verified"
        )


def _checked_path(repo: Path, value: str) -> Path:
    """The registration path, resolved and held to `docs/research/*.json`."""
    candidate = Path(value)
    if not candidate.is_absolute():
        candidate = repo / candidate
    try:
        resolved = candidate.resolve()
    except OSError as error:
        raise PathRefusedError(f"{value} cannot be resolved: {error}") from error
    research = (repo / RESEARCH_DIRECTORY).resolve()
    try:
        resolved.relative_to(research)
    except ValueError as error:
        raise PathRefusedError(
            f"{value} is not under {RESEARCH_DIRECTORY}/: a registration is written there and "
            "nowhere else"
        ) from error
    if resolved.suffix != ".json":
        raise PathRefusedError(
            f"{value} is not a .json file under {RESEARCH_DIRECTORY}/: a registration is a "
            ".json file"
        )
    return resolved


def _checked_anchor(anchor: date, now: datetime) -> date:
    """The registered first forward session: a weekday, not before today, not yet published.

    Fix round 1, F3: the anchor is the first trading session **strictly after** C's registration
    commit's time, so a future weekday is the ordinary case, not a refusal. Fix round 2, N2: the
    commit can only come after this write, so an anchor whose 16:30 publication instant has
    already passed is certain to precede the commit and is refused on day 1 rather than found
    wrong by the L6 report. The report still checks the exact session against the commit's
    trading calendar.
    """
    current = now if now.tzinfo is not None else now.replace(tzinfo=_SHANGHAI)
    today = current.astimezone(_SHANGHAI).date()
    if anchor.weekday() >= 5:
        raise AnchorRefusedError(
            f"{anchor} is not a weekday: the anchor is the first trading session strictly after "
            "C's registration commit's time"
        )
    if anchor < today:
        raise AnchorRefusedError(
            f"{anchor} is before today ({today}, Shanghai): the anchor is the first trading "
            "session strictly after C's registration commit's time, so it cannot precede the "
            "registration; the writer checks weekday, not-before-today and that it has not yet "
            "published, and the report checks the exact session against the commit's trading "
            "calendar"
        )
    published = session_publication_instant(anchor)
    if published <= current:
        raise AnchorRefusedError(
            f"{anchor}'s 16:30 publication instant ({published.isoformat()}) has already passed "
            f"at {current.isoformat()}: the registration commit can only come later, so this "
            "anchor is certain to precede it"
        )
    return anchor


def _refuse_a_salt_tied_to_the_seed(salt: str, seed: int) -> None:
    try:
        refuse_a_salt_tied_to_the_book(salt, book_seed=seed)
    except ValueError as error:
        raise SaltRefusedError(str(error)) from error


def _date_argument(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError(f"{value!r} is not a YYYY-MM-DD date") from error


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Write the V2-P7-013 agent-forward registration (C), once, at gated step L5, after "
            "the protocol document and the D1 power report exist."
        )
    )
    parser.add_argument("--salt", required=True)
    parser.add_argument("--step0-registration", required=True)
    parser.add_argument("--power-report", required=True)
    parser.add_argument("--anchor", type=_date_argument, required=True)
    parser.add_argument("--path", default=C_REGISTRATION_PATH)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--register-untestable", action="store_true")
    return parser


def _write(arguments: argparse.Namespace) -> None:
    repo = _repository()
    head = _git(repo, "rev-parse", "HEAD")
    if not _FULL_COMMIT.fullmatch(head):
        raise CheckoutRefusedError(f"HEAD {head!r} is not a full 40-character commit id")
    _refuse_a_dirty_bound_tree(repo)
    _refuse_an_unverified_endpoint()
    path = _checked_path(repo, arguments.path)
    anchor = _checked_anchor(arguments.anchor, _now())
    step0 = _step0_facts(repo, arguments.step0_registration)
    _refuse_a_salt_tied_to_the_seed(arguments.salt, step0.seed)
    protocol_document = _protocol_document(repo)
    power = _read_power(
        _resolve_against(repo, arguments.power_report),
        step0_registration=step0,
        register_untestable=arguments.register_untestable,
    )
    config, criteria, settings = c_registration_body(
        salt=arguments.salt,
        step0_registration=step0,
        power=power,
        anchor=anchor,
        protocol_document=protocol_document,
    )
    c_read_back(
        config,
        criteria,
        settings,
        code_commit=head,
        step0_registration=step0,
        repo=repo,
    )
    content = _document(config, criteria, settings, head)
    config_id = grid.config_id(config)
    digest = hashlib.sha256(content).hexdigest()
    if arguments.dry_run:
        sys.stdout.write(content.decode("utf-8"))
        print(f"config_id {config_id}")
        print(f"sha256 {digest}")
        print("未写入：--dry-run only prints; nothing was written")
        return
    existed = path.exists()
    try:
        written = registry.register(config, criteria, path, code_commit=head, settings=settings)
    except registry.RegistrationError as error:
        if existed:
            raise ExistingRegistrationError(str(error)) from error
        raise RegistrationRefusedError(f"the registry refused the registration: {error}") from error
    if written != digest:
        if not existed:
            path.unlink(missing_ok=True)
        raise DigestMismatchError(
            f"the canonical document registry.register wrote ({written}) is not the one this "
            f"writer printed ({digest}): digest mismatch, so the file just written is removed "
            "and nothing is registered"
        )
    print(f"wrote {path.relative_to(repo).as_posix()}")
    print(f"config_id {config_id}")
    print(f"sha256 {written}")
    print(_NEXT_STEP)


def main(argv: Sequence[str] | None = None) -> int:
    """The command line: 0 when written or printed, 2 on every named refusal with nothing
    written."""
    arguments = _parser().parse_args(None if argv is None else list(argv))
    try:
        _write(arguments)
    except RegistrationWriterError as error:
        print(f"refused: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
