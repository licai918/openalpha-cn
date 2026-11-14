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

import argparse
import copy
import hashlib
import json
import os
import re
import subprocess
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Final, TypedDict
from zoneinfo import ZoneInfo

from pydantic import ValidationError

_SCRIPTS: Final[Path] = Path(__file__).resolve().parent
for _root in (_SCRIPTS, _SCRIPTS / "research"):
    if str(_root) not in sys.path:
        sys.path.insert(0, str(_root))

import daily_selection  # noqa: E402
import grid  # noqa: E402
import p7_smoke_env  # noqa: E402
import registry  # noqa: E402

from openalpha_cn import (  # noqa: E402
    strategy_survivor,
    strategy_survivor_continuation,
    strategy_survivor_forward,
)
from openalpha_cn.domain.survivor_book import SurvivorBookConfig  # noqa: E402
from openalpha_cn.domain.survivor_veto import VETO_RULE_ID  # noqa: E402

REGISTRATION_PATH: Final[str] = "docs/research/p7-registration.json"
RESEARCH_DIRECTORY: Final[str] = "docs/research"
"""The only directory a registration may be written in."""
SHA256_ALGORITHM: Final[str] = "sha256"
_FULL_COMMIT: Final[re.Pattern[str]] = re.compile(r"[0-9a-f]{40}")
_SHANGHAI: Final[ZoneInfo] = ZoneInfo("Asia/Shanghai")


# 用户按建议采纳（2026-11-12）：D-5 正式运行目录是新的（只写在运行手册里），D-6 受绑定代码的两处
# 接缝接受并列为协议文档的已知局限，D-8 写入器在 scripts/p7_register.py（受绑定路径之外）。
class RegistrationWriterError(ValueError):
    """A registration that cannot be written as asked; the message names the reason and nothing
    is written."""


class CheckoutRefusedError(RegistrationWriterError):
    """The checkout cannot be read, or does not stand where the writer needs it."""


class BoundTreeRefusedError(RegistrationWriterError):
    """The bound code is not clean: the registration's code would not be the tree's."""


class HeadNotOnMainError(RegistrationWriterError):
    """`HEAD` is not on `main`'s history, so the smoke did not stand on a `main` commit."""


class SmokeCommitRefusedError(RegistrationWriterError):
    """The smoke commit is not a full commit, or its bound bytes are not `HEAD`'s."""


class PathRefusedError(RegistrationWriterError):
    """The registration path is not a `.json` file under `docs/research/`."""


class EpochRefusedError(RegistrationWriterError):
    """The epoch is not a weekday, is in the future, or is older than the bound allows."""


class ExistingRegistrationError(RegistrationWriterError):
    """The path already holds a different registration; a registration is written once."""


class SupersedesRefusedError(RegistrationWriterError):
    """The predecessor is not a committed registration of this repository."""


class ChainRefusedError(RegistrationWriterError):
    """The registration chain would stop being one rooted chain."""


class ParallelOfRefusedError(RegistrationWriterError):
    """The base registration a parallel book names, or the pair of arguments itself, cannot be
    written as asked (`V2-P7-027`, R-N5)."""


class EntryVetoRefusedError(RegistrationWriterError):
    """A parallel book must carry plan A's live entry veto; `none` (the base book's rule) and
    anything else are refused by name."""


class RegistrationRefusedError(RegistrationWriterError):
    """`registry.register` refused for a reason other than an existing, different registration."""


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

# 用户按建议采纳（2026-11-12）：D-1 planning_sd = 0.18，D-2 minimum_periods = 30，
# D-3 只规划主检验（h5 的离散度从未测过，不写没有来源的 sd）。
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
        "秩 IC 均值 ≤ 0.02 或 ew_minus_worst_decile 均值 ≤ 0 即停止，中期在第 18 个累积位置、"
        "累积均值 ≤ 0 即停止。"
    ),
    "interim": (
        "第 18 个累积位置报计数与描述性均值，同时中期无效性停止规则生效"
        "（累积均值 ≤ 0 即停止，见 stop_rule），K = 36 个位置到齐之前不做主检验、不给结论词。"
    ),
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


def registration_body(
    epoch: date,
    *,
    book: SurvivorBookConfig | None = None,
    parallel_of: ParallelOf | None = None,
) -> tuple[dict[str, Any], dict[str, str], dict[str, Any]]:
    """The registration's `(config, criteria, settings)` for `epoch`: plan sections 1.1 and 1.2.

    Pure: returns fresh copies, so a caller changing one cannot move the frozen literals.

    `book` is the book's parameters when they are not the design's (a parallel book's: the base
    book's parameters as a `survivor-book-config/v2` carrying plan A's veto); `parallel_of` is the
    `{"path", "sha256"}` block naming the committed base registration the parallel book runs
    beside (`V2-P7-027`, R-N5), written under `settings.survivor_parallel_of`. The base body names
    neither, so its config, settings and `config_id` are the frozen ones still."""
    config = p7_smoke_env.smoke_configuration(epoch, book=book)
    # 用户按建议采纳（2026-11-12）：D-4 本登记不带任何 agent_* 块，方案 C 的映射只在协议文档里占位。
    settings: dict[str, Any] = {
        **grid.protocol_settings(),
        "survivor_forward_protocol": copy.deepcopy(FORWARD_PROTOCOL),
        "survivor_forward_power": copy.deepcopy(FORWARD_POWER),
        "survivor_forward_stop_rule": strategy_survivor_forward.adopted_forward_stop_rule(),
    }
    if parallel_of is not None:
        settings[SURVIVOR_PARALLEL_OF_KEY] = {
            "path": parallel_of["path"],
            "sha256": parallel_of["sha256"],
        }
    return config, copy.deepcopy(CRITERIA), settings


# --- Task 3: the registration chain (U1) ----------------------------------------------------------

_SURVIVOR_KIND: Final[str] = strategy_survivor.SURVIVOR_KIND
_KIND_KEY: Final[str] = strategy_survivor.KIND_KEY
_SMOKE_KEY: Final[str] = daily_selection.SMOKE_SETTINGS_KEY
_CONTINUATION_KEY: Final[str] = strategy_survivor_continuation.SURVIVOR_CONTINUATION_KEY

SURVIVOR_PARALLEL_OF_KEY: Final[str] = "survivor_parallel_of"
"""The `settings` key that names the base registration a parallel book runs beside (R-N5): the
block is `{"path", "sha256"}`, written by this writer and read by `daily_selection`'s plist
printer (Task 5b). A registration carrying it is outside the 006b chain -- never a root, never a
continuation -- and the chain test verifies every block against the committed base."""
_PARALLEL_KEY: Final[str] = SURVIVOR_PARALLEL_OF_KEY
_SHA256: Final[re.Pattern[str]] = re.compile(r"[0-9a-f]{64}")


class ParallelOf(TypedDict):
    """The base registration a parallel book runs beside: its repository-relative path and the
    sha256 of its committed bytes (`V2-P7-027`, Task 5; R-N5)."""

    path: str
    sha256: str


@dataclass(frozen=True, slots=True)
class ParallelBase:
    """A committed base registration read back for a parallel book: the block to record, and the
    base's book parameters as the `survivor-book-config/v2` a parallel registration stores."""

    path: str
    sha256: str
    book: SurvivorBookConfig
    config_id: str


def committed_survivor_registrations(repo: Path) -> dict[str, dict[str, Any]]:
    """The non-smoke survivor registrations git holds under `docs/research/*.json`, by their
    repository-relative path, read from `HEAD`'s bytes.

    The path filter is the plan's (`git ls-files 'docs/research/*.json'`); git's wildcard crosses
    `/`, so the listing also carries that directory's subtree (`docs/research/p7-readings/`), and
    the `kind`/smoke filters below are what choose the chain's members. A file the listing gives
    that `HEAD` does not hold (staged or untracked and never committed) is not a committed
    registration and is skipped. A P6 configuration (no `kind`) and a smoke
    registration (`settings.smoke_run`) are not part of the chain: the first is the paused book,
    the second is never evidence and never merges into a non-smoke sample (U6)."""
    listing = _git(repo, "ls-files", f"{RESEARCH_DIRECTORY}/*.json")
    registrations: dict[str, dict[str, Any]] = {}
    for relative in listing.splitlines():
        if not relative:
            continue
        shown = _git_process(repo, "show", f"HEAD:{relative}")
        if shown.returncode != 0:
            continue
        try:
            body = json.loads(shown.stdout)
        except ValueError:
            continue
        if not isinstance(body, dict):
            continue
        config = body.get("config")
        settings = body.get("settings")
        if not isinstance(config, Mapping) or not isinstance(settings, Mapping):
            continue
        if config.get(_KIND_KEY) != _SURVIVOR_KIND:
            continue
        if _SMOKE_KEY in settings:
            continue
        registrations[relative] = body
    return registrations


def _chain_roots(registrations: Mapping[str, Mapping[str, Any]]) -> list[str]:
    """The registrations whose settings carry neither `survivor_continuation` nor
    `survivor_parallel_of`: the chain's roots.

    A parallel registration is outside the chain by R-N5 (`V2-P7-027`): it runs beside the base
    book and never extends or forks it, so it is not a root and not counted in the one-root rule
    (`chain_problems`)."""
    return sorted(
        path
        for path, body in registrations.items()
        if isinstance(body.get("settings"), Mapping)
        and _CONTINUATION_KEY not in body["settings"]
        and _PARALLEL_KEY not in body["settings"]
    )


def _parallel_problems(
    path: str, block: object, registrations: Mapping[str, Mapping[str, Any]]
) -> list[str]:
    """Why `path`'s `survivor_parallel_of` block is not one a parallel registration may carry.

    Structural only -- the sha256 is checked against git by the chain test and by the writer
    (`_refuse_a_bad_parallel_block`), because this function has no repository. Shape, no
    self-reference, and a base that is a committed member of the set: the block names a base
    registration, never a path outside the registration set (a missing file, a smoke
    registration, or the parallel registration itself)."""
    if not isinstance(block, Mapping):
        return [f"{path}: its {_PARALLEL_KEY} is not an object, so its base is unreadable"]
    named = block.get("path")
    digest = block.get("sha256")
    problems: list[str] = []
    if not isinstance(named, str) or not named:
        problems.append(f"{path}: its {_PARALLEL_KEY} names no base registration path")
        return problems
    if not isinstance(digest, str) or not _SHA256.fullmatch(digest):
        problems.append(
            f"{path}: its {_PARALLEL_KEY} names no sha256 of the base registration's committed "
            f"bytes ({digest!r})"
        )
    if named == path:
        problems.append(
            f"{path}: its {_PARALLEL_KEY} names itself; a parallel registration runs beside a "
            "base registration, never beside itself"
        )
    elif named not in registrations:
        problems.append(
            f"{path}: it names {named}, which is not a committed survivor registration; a "
            f"parallel registration's {_PARALLEL_KEY} names the base it runs beside (R-N5)"
        )
    return problems


def chain_problems(registrations: Mapping[str, Mapping[str, Any]]) -> list[str]:
    """Why `registrations` is not one rooted chain; `[]` when it is (plan section 4, Task 3).

    Exactly one registration is a root (none when the set is empty); every other registration's
    `survivor_continuation` block reads, and names a registration in the set; and no two name the
    same predecessor -- a fork is a second chain nobody declared.

    A registration with `survivor_parallel_of` is outside the chain by R-N5 (`V2-P7-027`): it is
    neither a root nor a link, its block must be one of `_parallel_problems`, and naming a
    `survivor_continuation` in the same body is refused -- a parallel book is never both. A
    continuation that names a parallel registration as its predecessor is refused as well: the
    parallel book's records never enter the chain's confirmatory sample."""
    problems: list[str] = []
    roots: list[str] = []
    successors: dict[str, list[str]] = {}
    members: list[str] = []
    for path in sorted(registrations):
        settings = registrations[path].get("settings")
        if not isinstance(settings, Mapping):
            problems.append(f"{path}: its settings are not an object, so its chain is unreadable")
            continue
        try:
            continuation = strategy_survivor_continuation.read_survivor_continuation(settings)
        except strategy_survivor_continuation.SurvivorContinuationError as error:
            problems.append(f"{path}: {error}")
            continue
        if _PARALLEL_KEY in settings:
            problems.extend(_parallel_problems(path, settings[_PARALLEL_KEY], registrations))
            if continuation is not None:
                problems.append(
                    f"{path}: it names both {_PARALLEL_KEY} and {_CONTINUATION_KEY}; a parallel "
                    "registration runs beside its base book and never continues one (R-N5)"
                )
            continue
        members.append(path)
        if continuation is None:
            roots.append(path)
            continue
        successor = continuation.supersedes_path
        successors.setdefault(successor, []).append(path)
        if successor not in registrations:
            problems.append(
                f"{path}: it names {successor}, which is not a committed survivor registration"
            )
            continue
        named_settings = registrations[successor].get("settings")
        if isinstance(named_settings, Mapping) and _PARALLEL_KEY in named_settings:
            problems.append(
                f"{path}: it names {successor}, a parallel registration; a parallel book runs "
                "beside the chain and is never a predecessor its sample is continued from (R-N5)"
            )
    if len(roots) > 1:
        problems.append(f"more than one chain root: {roots}")
    elif members and not roots:
        problems.append(
            "no chain root: every registration names a predecessor, so the chain is a cycle or "
            "leaves the set"
        )
    for predecessor, named in sorted(successors.items()):
        if len(named) > 1:
            problems.append(f"{len(named)} registrations name {predecessor}: {named}")
    return problems


def _with_predecessor(repo: Path, settings: Mapping[str, Any], value: str) -> dict[str, Any]:
    """`settings` plus the `survivor_continuation` block naming the committed predecessor
    `--supersedes` gave."""
    candidate = Path(value)
    if not candidate.is_absolute():
        candidate = repo / candidate
    try:
        root, admitted = registry.committed_registration(candidate, repo)
    except (registry.RegistrationNotCommittedError, registry.HoldoutConfigurationError) as error:
        raise SupersedesRefusedError(f"{value}: {error}") from error
    try:
        relative = candidate.resolve().relative_to(Path(root).resolve()).as_posix()
    except ValueError as error:
        raise SupersedesRefusedError(
            f"{value} is outside the repository {root}: only a committed registration in this "
            "repository can be superseded"
        ) from error
    config_id = admitted.registered.get("config_id")
    if not isinstance(config_id, str) or not config_id:
        raise SupersedesRefusedError(f"{value}: the predecessor names no config_id")
    inherited = admitted.registered.get("settings")
    if isinstance(inherited, Mapping) and _PARALLEL_KEY in inherited:
        raise SupersedesRefusedError(
            f"{relative} is a parallel registration: a parallel book runs beside the base book "
            "and its records never enter a chain's sample, so it is never a predecessor a "
            "registration continues (R-N5)"
        )
    block = strategy_survivor_continuation.continuation_block(
        path=relative,
        sha256=hashlib.sha256(admitted.content).hexdigest(),
        config_id=config_id,
    )
    return {**settings, _CONTINUATION_KEY: block}


# --- Task 5: the parallel book beside the base book (`V2-P7-027`, R-N5) --------------------------


def _checked_entry_veto(value: str) -> str:
    """`--entry-veto`'s value: plan A's live rule, or a refusal that says why.

    A parallel book is A's veto book beside the base book. `none` is the base book's rule (R-N4):
    a second registration with no veto would be the base configuration again, so it is refused by
    name rather than written."""
    if value == VETO_RULE_ID:
        return value
    if value == "none":
        raise EntryVetoRefusedError(
            "--entry-veto none: a parallel registration is A's veto book beside the base book, "
            "and the base book is the one registered without a veto (R-N4); a second registration "
            f"with none would be the base configuration again. Name {VETO_RULE_ID}"
        )
    raise EntryVetoRefusedError(
        f"--entry-veto {value!r} is not a rule this build registers: a parallel book carries "
        f"{VETO_RULE_ID}, the rule plan A fixes"
    )


def _head_bytes(repo: Path, relative: str) -> bytes | None:
    """`HEAD:<relative>`'s bytes, or `None` when `HEAD` does not hold the path.

    Bytes on purpose: the parallel block's sha256 is over exactly the committed bytes, so the
    read cannot go through a text decode."""
    environment = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    environment |= {
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_OPTIONAL_LOCKS": "0",
        "GIT_TERMINAL_PROMPT": "0",
    }
    try:
        finished = subprocess.run(
            ["git", "show", f"HEAD:{relative}"],
            cwd=repo,
            env=environment,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            check=False,
            timeout=GIT_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise CheckoutRefusedError(
            f"git show HEAD:{relative} in {repo} could not run: {error}"
        ) from error
    if finished.returncode != 0:
        return None
    return finished.stdout


def _parallel_base(repo: Path, value: str, entry_veto: str) -> ParallelBase:
    """The committed base registration `--parallel-of` names, read back as a parallel book's base.

    Its bytes must be the ones `HEAD` holds (`registry.committed_registration`: an untracked file
    has no committed sha256, and an edit after the commit is no longer the registered bytes); it
    is a non-smoke survivor registration; it is not itself a parallel registration (a parallel
    book runs beside a base book, never beside another parallel one); and its `survivor_book`
    block is re-read as a `survivor-book-config/v2` carrying `entry_veto` -- the base's
    parameters, the parallel book's version and rule."""
    candidate = Path(value)
    if not candidate.is_absolute():
        candidate = repo / candidate
    try:
        root, admitted = registry.committed_registration(candidate, repo)
    except (registry.RegistrationNotCommittedError, registry.HoldoutConfigurationError) as error:
        raise ParallelOfRefusedError(f"{value}: {error}") from error
    try:
        relative = candidate.resolve().relative_to(Path(root).resolve()).as_posix()
    except ValueError as error:
        raise ParallelOfRefusedError(
            f"{value} is outside the repository {root}: a parallel registration names a "
            "committed registration of this repository as its base"
        ) from error
    body = admitted.registered
    config = body.get("config")
    settings = body.get("settings")
    if (
        not isinstance(config, Mapping)
        or config.get(_KIND_KEY) != _SURVIVOR_KIND
        or not isinstance(settings, Mapping)
    ):
        raise ParallelOfRefusedError(
            f"{relative} is not a survivor registration: a parallel registration runs beside the "
            f"survivor book's registration, and {relative} registers no {_SURVIVOR_KIND}"
        )
    if _SMOKE_KEY in settings:
        raise ParallelOfRefusedError(
            f"{relative} is a smoke registration: a parallel book runs beside the registered book "
            "and never beside a smoke run"
        )
    if _PARALLEL_KEY in settings:
        raise ParallelOfRefusedError(
            f"{relative} is itself a parallel registration: a parallel book runs beside the base "
            "registration, never beside another parallel book"
        )
    block = config.get(strategy_survivor.BOOK_BLOCK)
    if not isinstance(block, Mapping):
        raise ParallelOfRefusedError(
            f"{relative} registers no {strategy_survivor.BOOK_BLOCK} block: its parameters "
            "cannot be made a parallel book's"
        )
    try:
        book = SurvivorBookConfig(
            **{
                **block,
                "schema_version": "survivor-book-config/v2",
                "entry_veto": entry_veto,
            }
        )
    except ValidationError as error:
        raise ParallelOfRefusedError(
            f"{relative}'s book parameters are not a survivor-book-config/v2 a parallel book can "
            f"store: {error}"
        ) from error
    config_id = body.get("config_id")
    return ParallelBase(
        path=relative,
        sha256=hashlib.sha256(admitted.content).hexdigest(),
        book=book,
        config_id=config_id if isinstance(config_id, str) else "",
    )


def _refuse_a_bad_parallel_block(repo: Path, settings: Mapping[str, Any]) -> None:
    """Read the `survivor_parallel_of` block back against `HEAD` before anything is written.

    The sha256 must be the committed base's, exactly: a block naming another digest points a
    registration at bytes the base no longer holds, so it is refused by name here rather than
    written and discovered later."""
    block = settings.get(_PARALLEL_KEY)
    if block is None:
        return
    if not isinstance(block, Mapping) or set(block) != {"path", "sha256"}:
        raise ParallelOfRefusedError(
            f"the {_PARALLEL_KEY} block is {{'path', 'sha256'}}, not {block!r}: a parallel "
            "registration names one base registration"
        )
    named = block.get("path")
    digest = block.get("sha256")
    if not isinstance(named, str) or not named:
        raise ParallelOfRefusedError(
            f"the {_PARALLEL_KEY} block names no base registration path ({named!r})"
        )
    if not isinstance(digest, str) or not _SHA256.fullmatch(digest):
        raise ParallelOfRefusedError(
            f"the {_PARALLEL_KEY} block names no sha256 of {named}'s committed bytes ({digest!r})"
        )
    held = _head_bytes(repo, named)
    if held is None:
        raise ParallelOfRefusedError(
            f"the base registration {named} is not a file HEAD holds: a parallel registration "
            "names a committed base registration whose sha256 it records"
        )
    actual = hashlib.sha256(held).hexdigest()
    if actual != digest:
        raise ParallelOfRefusedError(
            f"the base registration {named} holds sha256 {actual} at HEAD, not the {digest} the "
            f"{_PARALLEL_KEY} block names: a parallel registration names the bytes it was built "
            "from"
        )


def _refuse_a_second_parallel(
    committed: Mapping[str, Mapping[str, Any]], block: ParallelOf, config_id: str
) -> None:
    """A base book has one parallel book, and one configuration is registered once (R-N5).

    Every candidate is a committed registration already read from `HEAD`; the refusal names the
    file an operator would have to look at."""
    for path in sorted(committed):
        settings = committed[path].get("settings")
        if not isinstance(settings, Mapping):
            continue
        existing = settings.get(_PARALLEL_KEY)
        if existing is None:
            continue
        if isinstance(existing, Mapping) and existing.get("path") == block["path"]:
            raise ParallelOfRefusedError(
                f"{path} is already the parallel registration of {block['path']}: a base book "
                f"has one parallel book, so configuration {config_id} cannot be registered a "
                "second time (R-N5)"
            )
        if committed[path].get("config_id") == config_id:
            raise ParallelOfRefusedError(
                f"{path} already registers configuration {config_id}: a parallel registration "
                "is registered once"
            )


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


# --- Task 2: the command line and its refusals ---------------------------------------------------

GIT_TIMEOUT_SECONDS: Final[float] = 60
"""Each git call's bound, as `p7_smoke_env`'s: the repository is small, so a slow call is stuck."""
_NEXT_STEP: Final[str] = (
    "next: stage and commit only this one file yourself -- the message names V2-P7-006b -- and "
    "then run the registration test; this writer never stages, commits or pushes anything."
)
_PARALLEL_NEXT_STEP: Final[str] = (
    "next: stage and commit only this one file yourself -- the message names V2-P7-027 -- and "
    "then run the registration test; a parallel registration is outside the 006b chain, and this "
    "writer never stages, commits or pushes anything."
)
"""What to do after a successful write. It names the step in words rather than as a shell line: the
writer is the only thing here that touches a registration, and it is not a commit helper."""


def _today() -> date:
    """Today on the Shanghai calendar: the epoch is a Shanghai session date."""
    return datetime.now(_SHANGHAI).date()


def _git_process(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """git in `repo`, hermetic: no inherited `GIT_*`, no global or system configuration, no
    optional lock, no prompt, no stdin -- as `p7_smoke_env._git` runs it."""
    environment = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    environment |= {
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_OPTIONAL_LOCKS": "0",
        "GIT_TERMINAL_PROMPT": "0",
    }
    try:
        return subprocess.run(
            ["git", *args],
            cwd=repo,
            env=environment,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            check=False,
            timeout=GIT_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise CheckoutRefusedError(
            f"git {' '.join(args)} in {repo} could not run: {error}"
        ) from error


def _git(repo: Path, *args: str) -> str:
    finished = _git_process(repo, *args)
    if finished.returncode != 0:
        raise CheckoutRefusedError(
            f"git {' '.join(args)} in {repo} failed: {finished.stderr.strip() or 'no message'}"
        )
    return finished.stdout.strip()


def _repository() -> Path:
    """The top of the checkout the writer runs in."""
    try:
        top = _git(Path.cwd(), "rev-parse", "--show-toplevel")
    except CheckoutRefusedError as error:
        raise CheckoutRefusedError(f"{Path.cwd()} is not a git checkout: {error}") from error
    return Path(top).resolve()


def _refuse_an_unwritable_checkout(repo: Path, head: str) -> None:
    """The writer's first step, `p7_smoke_env.check_commit`'s checks in the writer's own order."""
    if not _FULL_COMMIT.fullmatch(head):
        raise CheckoutRefusedError(f"HEAD {head!r} is not a full 40-character commit id")
    dirty = _git(repo, "status", "--porcelain", "--untracked-files=all", "--", *p7_smoke_env.BOUND)
    if dirty:
        raise BoundTreeRefusedError(
            f"the bound code of {repo} has uncommitted changes ({dirty.splitlines()[0]} ...): "
            "the registration would name a commit whose bytes are not the tree's"
        )
    on_main = _git_process(repo, "merge-base", "--is-ancestor", head, "refs/heads/main")
    if on_main.returncode != 0:
        raise HeadNotOnMainError(
            f"HEAD {head} is not on main's history: a registration is written from main's code"
        )
    try:
        p7_smoke_env.check_commit(repo, head)
    except p7_smoke_env.SmokeEnvironmentError as error:
        raise CheckoutRefusedError(str(error)) from error


def _refuse_a_smoke_commit(repo: Path, smoke: str) -> None:
    """The smoke commit must exist and bind the same bytes as `HEAD` (plan section 2, step 2)."""
    if not _FULL_COMMIT.fullmatch(smoke):
        raise SmokeCommitRefusedError(
            f"the smoke commit {smoke!r} is not a full 40-character commit id; the smoke commit "
            "is the commit the smoke's phase 1 ran on"
        )
    exists = _git_process(repo, "cat-file", "-e", f"{smoke}^{{commit}}")
    if exists.returncode != 0:
        raise SmokeCommitRefusedError(
            f"the smoke commit {smoke} is not a commit this repository holds"
        )
    compared = _git_process(repo, "diff", "--quiet", smoke, "HEAD", "--", *p7_smoke_env.BOUND)
    head = _git(repo, "rev-parse", "HEAD")
    if compared.returncode != 0:
        raise SmokeCommitRefusedError(
            f"the bound code differs between the smoke commit {smoke} and HEAD {head}: the "
            "registration must name the bytes the smoke tested, or the smoke would be rerun"
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


def _checked_epoch(epoch: date, today: date) -> date:
    """The planned epoch: a weekday, not in the future (Shanghai), and at most 16 calendar days
    before today (the 11-session bound's conservative calendar upper bound)."""
    if epoch.weekday() >= 5:
        raise EpochRefusedError(
            f"{epoch} is not a weekday: the epoch is the trading session before the first planned "
            "evening, and the exact check is the first evening's step 1"
        )
    if epoch > today:
        raise EpochRefusedError(
            f"{epoch} is later than today ({today}, Shanghai): the epoch is in the future"
        )
    age = (today - epoch).days
    if age > EPOCH_LOOKBACK_DAYS:
        raise EpochRefusedError(
            f"{epoch} is {age} calendar days before today ({today}): more than the "
            f"{EPOCH_LOOKBACK_DAYS}-day upper bound (11 sessions is the exact bound the first "
            "evening's step 1 checks)"
        )
    return epoch


def _document(
    config: Mapping[str, Any],
    criteria: Mapping[str, str],
    settings: Mapping[str, Any],
    code_commit: str,
) -> bytes:
    """The registration's canonical bytes, as `registry.register` writes them: a dry run prints
    these and their sha256, so a reviewer sees exactly what the write would produce."""
    body = {
        "schema": registry.REGISTRATION_SCHEMA,
        "config": grid.to_json_value(config),
        "config_id": grid.config_id(config),
        "criteria": grid.to_json_value(criteria),
        "code_commit": code_commit,
        "settings": grid.to_json_value(settings),
        "false_discovery_rate": grid.PROTOCOL_FALSE_DISCOVERY_RATE,
        "dependence": grid.PROTOCOL_DEPENDENCE,
    }
    return (json.dumps(body, sort_keys=True, indent=2, ensure_ascii=False) + "\n").encode()


def _date_argument(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError(f"{value!r} is not a YYYY-MM-DD date") from error


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Write the V2-P7-006b survivor registration, once, after the smoke's phase 1."
    )
    parser.add_argument("--epoch", type=_date_argument, required=True)
    parser.add_argument("--smoke-commit", required=True)
    parser.add_argument("--path", default=REGISTRATION_PATH)
    parser.add_argument("--supersedes")
    parser.add_argument(
        "--entry-veto",
        metavar="RULE",
        help="A parallel book's entry veto: the rule plan A fixes (market_bearish_5/v1). "
        "Given only with --parallel-of; the parallel registration is written as a "
        "survivor-book-config/v2 carrying it.",
    )
    parser.add_argument(
        "--parallel-of",
        metavar="REGISTRATION",
        help="The committed base registration the parallel book runs beside. Given only with "
        "--entry-veto; the parallel registration is outside the survivor chain (R-N5).",
    )
    parser.add_argument("--dry-run", action="store_true")
    return parser


def _refuse_a_half_parallel_pair(arguments: argparse.Namespace) -> None:
    """`--entry-veto` and `--parallel-of` are one registration's pair: a rule with no base, or a
    base with no rule, would write a registration that names only half of what it is."""
    if (arguments.entry_veto is None) == (arguments.parallel_of is None):
        return
    raise ParallelOfRefusedError(
        "--entry-veto and --parallel-of are one parallel registration's pair: the rule the book "
        "applies and the committed base book it runs beside. Given only one, nothing is written; "
        "give both, or neither for the base book (R-N5)"
    )


def _refuse_a_parallel_continuation(arguments: argparse.Namespace) -> None:
    """R-N5: a parallel registration is never a continuation, and a continuation is never a
    parallel registration; the two blocks in one body are refused by name."""
    if arguments.parallel_of is None or arguments.supersedes is None:
        return
    raise ParallelOfRefusedError(
        f"--parallel-of {arguments.parallel_of} with --supersedes {arguments.supersedes}: a "
        "parallel registration runs beside its base book and is never a continuation or a chain "
        f"link; it names its base in settings.{_PARALLEL_KEY} only (R-N5)"
    )


def _write(arguments: argparse.Namespace) -> None:
    _refuse_a_half_parallel_pair(arguments)
    _refuse_a_parallel_continuation(arguments)
    repo = _repository()
    head = _git(repo, "rev-parse", "HEAD")
    _refuse_an_unwritable_checkout(repo, head)
    _refuse_a_smoke_commit(repo, arguments.smoke_commit)
    path = _checked_path(repo, arguments.path)
    epoch = _checked_epoch(arguments.epoch, _today())
    committed = committed_survivor_registrations(repo)
    book = None
    base: ParallelBase | None = None
    parallel: ParallelOf | None = None
    if arguments.parallel_of is not None:
        entry_veto = _checked_entry_veto(arguments.entry_veto)
        base = _parallel_base(repo, arguments.parallel_of, entry_veto)
        book = base.book
        parallel = {"path": base.path, "sha256": base.sha256}
    config, criteria, settings = registration_body(epoch, book=book, parallel_of=parallel)
    config_id = grid.config_id(config)
    if base is not None and base.config_id == config_id:
        raise ParallelOfRefusedError(
            f"{base.path} already registers configuration {config_id}: the base book's "
            "parameters with the same veto and epoch are the same configuration, so a parallel "
            "registration would be a second copy of the base book"
        )
    if arguments.supersedes is not None:
        settings = _with_predecessor(repo, settings, arguments.supersedes)
    elif parallel is None:
        roots = _chain_roots(committed)
        if roots:
            raise ChainRefusedError(
                f"{roots[0]} is a committed survivor registration and no --supersedes was "
                "given: every survivor registration after the first must name the one it "
                "supersedes (U1)"
            )
    if parallel is not None:
        _refuse_a_second_parallel(committed, parallel, config_id)
        _refuse_a_bad_parallel_block(repo, settings)
    read_back(config, criteria, settings)
    proposed = {
        **committed,
        path.relative_to(repo).as_posix(): {"config": config, "settings": settings},
    }
    problems = chain_problems(proposed)
    if problems:
        raise ChainRefusedError("; ".join(problems))
    content = _document(config, criteria, settings, head)
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
        raise CheckoutRefusedError(
            f"the canonical document registry.register wrote ({written}) is not the one this "
            f"writer printed ({digest}): digest mismatch, so the file just written is removed "
            "and nothing is registered"
        )
    print(f"wrote {path.relative_to(repo).as_posix()}")
    print(f"config_id {config_id}")
    print(f"sha256 {written}")
    print(_PARALLEL_NEXT_STEP if parallel is not None else _NEXT_STEP)


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
