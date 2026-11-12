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
from datetime import date, datetime
from pathlib import Path
from typing import Any, Final
from zoneinfo import ZoneInfo

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
    # 用户按建议采纳（2026-11-12）：D-4 本登记不带任何 agent_* 块，方案 C 的映射只在协议文档里占位。
    settings: dict[str, Any] = {
        **grid.protocol_settings(),
        "survivor_forward_protocol": copy.deepcopy(FORWARD_PROTOCOL),
        "survivor_forward_power": copy.deepcopy(FORWARD_POWER),
        "survivor_forward_stop_rule": strategy_survivor_forward.adopted_forward_stop_rule(),
    }
    return config, copy.deepcopy(CRITERIA), settings


# --- Task 3: the registration chain (U1) ----------------------------------------------------------

_SURVIVOR_KIND: Final[str] = strategy_survivor.SURVIVOR_KIND
_KIND_KEY: Final[str] = strategy_survivor.KIND_KEY
_SMOKE_KEY: Final[str] = daily_selection.SMOKE_SETTINGS_KEY
_CONTINUATION_KEY: Final[str] = strategy_survivor_continuation.SURVIVOR_CONTINUATION_KEY


def committed_survivor_registrations(repo: Path) -> dict[str, dict[str, Any]]:
    """The non-smoke survivor registrations git holds under `docs/research/*.json`, by their
    repository-relative path, read from `HEAD`'s bytes.

    The path filter is the plan's (`git ls-files 'docs/research/*.json'`, which matches only that
    one directory); a file it lists that `HEAD` does not hold (staged and never committed) is not
    a committed registration and is skipped. A P6 configuration (no `kind`) and a smoke
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
    """The registrations whose settings carry no `survivor_continuation`: the chain's root."""
    return sorted(
        path
        for path, body in registrations.items()
        if isinstance(body.get("settings"), Mapping) and _CONTINUATION_KEY not in body["settings"]
    )


def chain_problems(registrations: Mapping[str, Mapping[str, Any]]) -> list[str]:
    """Why `registrations` is not one rooted chain; `[]` when it is (plan section 4, Task 3).

    Exactly one registration is a root (none when the set is empty); every other registration's
    `survivor_continuation` block reads, and names a registration in the set; and no two name the
    same predecessor -- a fork is a second chain nobody declared."""
    problems: list[str] = []
    roots: list[str] = []
    successors: dict[str, list[str]] = {}
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
        if continuation is None:
            roots.append(path)
            continue
        successor = continuation.supersedes_path
        successors.setdefault(successor, []).append(path)
        if successor not in registrations:
            problems.append(
                f"{path}: it names {successor}, which is not a committed survivor registration"
            )
    if len(roots) > 1:
        problems.append(f"more than one chain root: {roots}")
    elif registrations and not roots:
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
    block = strategy_survivor_continuation.continuation_block(
        path=relative,
        sha256=hashlib.sha256(admitted.content).hexdigest(),
        config_id=config_id,
    )
    return {**settings, _CONTINUATION_KEY: block}


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
    parser.add_argument("--dry-run", action="store_true")
    return parser


def _write(arguments: argparse.Namespace) -> None:
    repo = _repository()
    head = _git(repo, "rev-parse", "HEAD")
    _refuse_an_unwritable_checkout(repo, head)
    _refuse_a_smoke_commit(repo, arguments.smoke_commit)
    path = _checked_path(repo, arguments.path)
    epoch = _checked_epoch(arguments.epoch, _today())
    config, criteria, settings = registration_body(epoch)
    committed = committed_survivor_registrations(repo)
    if arguments.supersedes is not None:
        settings = _with_predecessor(repo, settings, arguments.supersedes)
    else:
        roots = _chain_roots(committed)
        if roots:
            raise ChainRefusedError(
                f"{roots[0]} is a committed survivor registration and no --supersedes was "
                "given: every survivor registration after the first must name the one it "
                "supersedes (U1)"
            )
    read_back(config, criteria, settings)
    proposed = {
        **committed,
        path.relative_to(repo).as_posix(): {"config": config, "settings": settings},
    }
    problems = chain_problems(proposed)
    if problems:
        raise ChainRefusedError("; ".join(problems))
    content = _document(config, criteria, settings, head)
    config_id = grid.config_id(config)
    digest = hashlib.sha256(content).hexdigest()
    if arguments.dry_run:
        sys.stdout.write(content.decode("utf-8"))
        print(f"config_id {config_id}")
        print(f"sha256 {digest}")
        print("未写入：--dry-run only prints; nothing was written")
        return
    try:
        written = registry.register(config, criteria, path, code_commit=head, settings=settings)
    except registry.RegistrationError as error:
        raise ExistingRegistrationError(str(error)) from error
    if written != digest:
        raise CheckoutRefusedError(
            f"the canonical document registry.register wrote ({written}) is not the one this "
            f"writer printed ({digest}); refusing to claim a digest the file does not have"
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
