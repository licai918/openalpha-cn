"""The one daily selection command (`V2-P6-011`): after the close, today's candidates, target
weights and a registered prediction, under the registered configuration.

    uv run --no-sync --env-file .env python scripts/daily_selection.py --runtime-dir RT

Eight steps, in this order. The first that fails stops the run, is named, and sets a non-zero
exit; nothing after it runs. Every unexpected fault inside a step is reported as that step's
refusal, with the Tushare requests spent so far.

1. **registration** -- read `docs/research/p6-registration.json` (`--registration`), the file the
   holdout ran on. It must be committed and byte-equal to `HEAD`'s, and the code running now must
   be its `code_commit`: `registry.admit_registered_code`, the holdout guard's own checks, over
   `REGISTERED_PATHS` plus this file. Forward results come from the registered code or not at all.
   A missing file exits `2`; a refused binding exits `3`. A scheduled run should stand in a
   checkout pinned at the registration (`--pin-worktree`), so development elsewhere cannot make
   it refuse.
2. **panel update** -- `openalpha panel build --incremental` for the year of the newest closed
   session, pinned to one `--as-of` for the day, over the targets the day's scoring reads
   (`targets_for`): the price base, every statement dataset a registered factor reads, and the
   industry targets on a day that reads industries (`industry_day`). When the session's outcome
   window or its next session reaches into the next calendar year, that year's `trade_cal` too.
3. **panel doctor** -- `openalpha panel doctor` and the dependency gate `openalpha data-check`
   over those datasets, the year and the session. Not clean stops the run before any factor is
   built.
4. **factor build** -- every factor tier the configuration reads, at the session's signal instant
   (16:30 Shanghai), through `factor_view.build_factor_panel_set`. A tier already built at that
   instant is not built again.
5. **candidates** -- the session scored by `strategy_view.score_day`: the backtest's own feeds and
   scorer, so the ranking is the one a backtest of the registered configuration would trade on.
6. **target weights** -- the book's rebalance rule (`strategy_backtest.target_holdings`) over that
   ranking, the previous journalled targets as the book held, on the configuration's rebalance
   schedule counted from its `start`; equal weights of `1 / holding_count`, the rest cash.
7. **prediction** -- the day's scores registered in the prediction store before 09:15 on the next
   session, when the call auction that fixes the book's execution price begins:
   `strategy_registration.signal_day_batch` (the fit's own batch for a walk-forward source, the
   composite each security was ranked by otherwise).
8. **summary** -- printed, and written to the day's journal.

## Why steps 5 and 6 are not `shortlist run` and `portfolio construct`

The plan named those two commands. Neither implements the registered strategy, and using them
would publish a list and a book the research never measured:

- `shortlist run` orders by the weighted sum of the stored tier values over the registry's
  universe (`backtest/cross_section.py::CrossSectionScreen`); the strategy standardizes each
  component over the securities carrying every component and sums those (`zscore_sum` or
  `rank_sum`). It refuses the neutralized tier outright and cannot express a trailing-IC or
  walk-forward source at all, and its gate refuses any list nobody has researched unless the
  floor is zero.
- `portfolio construct` weights a shortlist by declared rank tiers under a 25% position cap and
  80% exposure, with no buffer band; the strategy holds `holding_count` names at equal capital,
  keeps a holding while it ranks within `buffer_rank`, and caps names per industry.

So the candidates are the strategy's own ranking and the targets its own rebalance rule, both
through the functions `run_strategy_backtest` uses, and the equality is tested rather than
asserted (`tests/unit/scripts/test_daily_selection.py`).

## What a day fetches (R2)

`targets_for` reads the configuration: `BASE_TARGETS` always (the calendar, the registry, the
three session-scoped price targets the scoring, the labels and the book read, and `index_daily`
for the protocol's 000905.SH benchmark), plus the target of every dataset a registered factor
reads -- the statement sweeps only for a configuration that reads statements. Industries are
refreshed only on a day whose scoring reads them (`industry_day`): every day for a neutralized
tier, whose build reads the whole cross section's industries; on a rebalance day for an industry
cap, whose only reader is the rebalance decision. On any other day nothing reads a membership,
so every security the day's scoring reads has exactly the membership a daily full refresh would
have given it -- none is read. `--full-update` fetches `FULL_UPDATE_TARGETS` instead, the whole
current year. The runbook carries the per-kind counts, measured from the code's own `BUDGET`
lines.

## Idempotent per trading day

The journal is `RT/daily_selection/<config_id[:16]>/<session>.json`. The day's `--as-of` is
written to it before anything is fetched, and every later run of that session uses it. A day
whose journal is complete is printed again and nothing runs: no request, no write. A day whose
panel update is journalled resumes after it. Every other step is idempotent on its own: a
partition written again with the same content is not rewritten (`PanelStore`), a factor tier
already built at the instant is skipped, and a prediction already registered for the day under
the same declaration is reused rather than filed twice (and a different one is refused).

## What the numbers are

Candidates, not recommendations: the ranking is a registered research configuration's, and the
holdout's verdict is what says whether it earned anything. Nothing here places an order or
states an expected return. The credential never passes through this file: `panel build` resolves
it inside `TushareProvider`, and the request counter wraps the transport and reads only each
request's `api_name`.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import importlib.metadata
import io
import json
import logging
import os
import platform
import plistlib
import re
import shlex
import shutil
import subprocess
import sys
import tomllib
from collections import Counter
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from enum import IntEnum
from pathlib import Path
from typing import Any, Final, cast
from zoneinfo import ZoneInfo

REPOSITORY: Final[Path] = Path(__file__).resolve().parents[1]
RESEARCH_SCRIPTS: Final[Path] = REPOSITORY / "scripts" / "research"
if str(RESEARCH_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(RESEARCH_SCRIPTS))

import grid  # noqa: E402  (scripts/research/grid.py: the holdout's own significance test)
import registry  # noqa: E402
from click import ClickException  # noqa: E402
from typer.main import get_command  # noqa: E402

from openalpha_cn import cli  # noqa: E402
from openalpha_cn.backtest.strategy_backtest import (  # noqa: E402
    StrategyBacktestError,
    target_holdings,
)
from openalpha_cn.domain.alpha_model import PredictionBatch  # noqa: E402
from openalpha_cn.domain.index_membership import (  # noqa: E402
    INDEX_WEIGHT_DATASET,
    INDEX_WEIGHT_INDEX_CODES,
)
from openalpha_cn.domain.index_prices import INDEX_DAILY_DATASET  # noqa: E402
from openalpha_cn.domain.prediction_record import (  # noqa: E402
    PredictionRecord,
    outcome_known_at_for,
)
from openalpha_cn.domain.trading_calendar import (  # noqa: E402
    TRADING_CALENDAR_DATASET,
    TradingCalendar,
    TradingCalendarError,
)
from openalpha_cn.factor_view import (  # noqa: E402
    FactorViewError,
    build_factor_panel_set,
    factor_build_requests,
    resolve_factor,
)
from openalpha_cn.panel.catalog import DEFAULT_DATE_TIMEZONE, PanelStorageError  # noqa: E402
from openalpha_cn.panel.store import PanelStore  # noqa: E402
from openalpha_cn.panel_factors import (  # noqa: E402
    FACTOR_TRANSFORMS,
    FactorEngineError,
    factor_manifest_dataset,
    load_factor_manifests,
    load_factor_transform_manifests,
)
from openalpha_cn.panel_ingest import (  # noqa: E402
    load_trading_calendar,
    newest_published_session,
    session_publication_instant,
)
from openalpha_cn.panel_neutralization import (  # noqa: E402
    FACTOR_NEUTRALIZATIONS,
    load_factor_neutralization_manifests,
)
from openalpha_cn.panel_view import panel_store  # noqa: E402
from openalpha_cn.providers.base import ProviderFailure  # noqa: E402
from openalpha_cn.providers.tushare import (  # noqa: E402
    TRADING_CALENDAR_DEFAULT_EXCHANGE,
    TushareTransport,
)
from openalpha_cn.runtime.composition import build_storage  # noqa: E402
from openalpha_cn.runtime.provenance import resolve_code_commit  # noqa: E402
from openalpha_cn.strategy_registration import (  # noqa: E402
    UNVERIFIABLE,
    InputPartition,
    InputProvenance,
    ProvenanceLookup,
    RecordCheck,
    RegisteredConfiguration,
    Schedule,
    StrategyRegistrationError,
    WitnessedDay,
    batch_digest,
    input_provenance,
    registration_cutoff,
    session_record,
    signal_day_batch,
    witnessed_days,
)
from openalpha_cn.strategy_registration import schedule_of as registered_schedule_of  # noqa: E402
from openalpha_cn.strategy_view import (  # noqa: E402
    SignalDay,
    StrategyRequest,
    StrategyViewError,
    score_day,
    strategy_request,
)

DAILY_SELECTION_SCHEMA: Final[str] = "openalpha-daily-selection/v1"
DEFAULT_REGISTRATION: Final[Path] = REPOSITORY / "docs" / "research" / "p6-registration.json"
REGISTRATION_IN_REPOSITORY: Final[str] = "docs/research/p6-registration.json"
JOURNAL_DIRECTORY: Final[str] = "daily_selection"
THIS_SCRIPT: Final[str] = "scripts/daily_selection.py"

BASE_TARGETS: Final[tuple[str, ...]] = (
    "trade_cal",
    "stock_basic",
    "adj_factor",
    "price",
    "stk_limit",
    "index_daily",
)
"""What every day fetches: the calendar, the registry, the price targets the scoring, the labels
and the book read, and `index_daily` for the protocol's benchmark (the forward report's)."""

FULL_UPDATE_TARGETS: Final[tuple[str, ...]] = (
    "trade_cal",
    "stock_basic",
    "namechange",
    "adj_factor",
    "price",
    "stk_limit",
    "index_daily",
    "index_weight",
    "income",
    "balancesheet",
    "cashflow",
    "fina_indicator",
)
"""`--full-update`: every year-scoped target plus `fina_indicator`, whatever the configuration
reads -- the whole current year kept fresh for other readers of the same store."""

INDUSTRY_MEMBERSHIP_TARGET: Final[str] = "index_member_all"
INDUSTRY_TARGETS: Final[tuple[str, ...]] = ("index_classify", INDUSTRY_MEMBERSHIP_TARGET)
UNCHECKED_BY_YEAR: Final[frozenset[str]] = frozenset({"stock_basic", *INDUSTRY_TARGETS})
"""Targets whose partitions are not the session's market (a registry lifecycle year, a taxonomy
vintage, membership-event years), so the doctor is not asked about them under the session's year.

Measured on a copy of `runtime/panel` for 2026-08-28: asked about `stock_basic --year 2026`
beside the session datasets, the doctor warns `subject_set_disagreement` -- 5,449 securities with
bars "absent from stock_basic" -- because the 2026 partition holds 2026's listings (123 rows), not
the market; asked about every lifecycle year it holds, it refuses for want of a `trade_cal` for
each. Without it the same panel is clean and the gate clears. The registry is still read whole by
every step that needs it (`load_stock_universe` reads every lifecycle year the store holds), and
those reads refuse on their own."""

DEFAULT_MAX_STALENESS_DAYS: Final[int] = 30
"""The freshness bound every factor build of this command states (`factor build
--max-staleness-days`): the value this repository's factor builds have used throughout."""

SHANGHAI: Final[ZoneInfo] = ZoneInfo(DEFAULT_DATE_TIMEZONE)
_WEIGHT_QUANTUM: Final[Decimal] = Decimal("0.0000000001")
_STRATEGY_KEYS: Final[frozenset[str]] = frozenset(
    {
        "as_of",
        "benchmarks",
        "buffer_rank",
        "combine",
        "components",
        "end",
        "exchange",
        "holding_count",
        "max_industry_weight",
        "neutralization",
        "participation_cap",
        "position_capital",
        "prediction_ids",
        "rebalance_every_sessions",
        "slippage_rate",
        "start",
        "trailing_ic",
        "transform",
        "walk_forward",
    }
)
"""Every key of a registered configuration: the keyword arguments of
`OpenAlphaSDK.run_strategy_backtest`, which is what the research grid and the holdout call."""


class DailyExit(IntEnum):
    """What the command exits with."""

    done = 0
    step_failed = 1
    no_registration = 2
    code_not_registered = 3


STEPS: Final[tuple[str, ...]] = (
    "registration",
    "panel update",
    "panel doctor",
    "factor build",
    "candidates",
    "target weights",
    "prediction",
    "summary",
)


class StepFailedError(RuntimeError):
    """A step refused; the run stops there and says which step it was."""

    def __init__(self, step: str, message: str, *, exit_code: DailyExit = DailyExit.step_failed):
        super().__init__(f"step {STEPS.index(step) + 1} ({step}) failed: {message}")
        self.step = step
        self.exit_code = exit_code
        self.requests: dict[str, int] = {}
        """The Tushare requests the run had made when it stopped, by `api_name`: a refused run
        spends requests too, and the budget is counted either way."""


@contextlib.contextmanager
def _step(name: str) -> Iterator[None]:
    """Report any fault the step did not anticipate as that step's refusal, by name.

    A `StepFailedError` passes through; anything else becomes one naming the step and the
    fault's type -- and its message, except for a provider failure, whose message can carry the
    request envelope and is never printed (the `panel build` rule, applied here too).
    """
    try:
        yield
    except StepFailedError:
        raise
    except Exception as error:
        detail = (
            type(error).__name__
            if isinstance(error, ProviderFailure)
            else f"{type(error).__name__}: {error}"
        )
        raise StepFailedError(name, f"an unexpected fault: {detail}") from error


# --- the configuration ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True, kw_only=True)
class Registration:
    """The admitted registration: what it registered and the code commit it binds."""

    path: Path
    sha256: str
    commit: str
    code_commit: str
    config: Mapping[str, Any]
    config_id: str
    seed: int
    settings: Mapping[str, Any]
    """The registration's measurement settings (`grid.protocol_settings`' keys): what the forward
    report's significance test runs under (`forward_summary`)."""

    @property
    def declared(self) -> RegisteredConfiguration:
        """What a composite record declares about this registration."""
        return RegisteredConfiguration(
            config_id=self.config_id,
            registration_sha256=self.sha256,
            code_commit=self.code_commit,
            seed=self.seed,
        )


def admit_registration(path: Path, repo: Path, *, also_bound: Sequence[str] = ()) -> Registration:
    """Step 1: the committed registration, provided the running code is the registered code.

    `also_bound` adds paths the registration binds beside this file -- the forward report adds its
    own (`scripts/forward_report.py`), since a forward report is as much its claim. The
    registration's `config_id` is re-derived from its `config` here, the one place either command
    admits a registration (`run_holdout`'s defence against two halves that disagree).
    """
    if not path.is_file():
        raise StepFailedError(
            "registration",
            f"{path} does not exist; the daily command runs the registered configuration and "
            "there is none to run",
            exit_code=DailyExit.no_registration,
        )
    try:
        root, admitted = registry.admit_registered_code(
            path, repo, also_bound=(THIS_SCRIPT, *also_bound)
        )
    except registry.HoldoutConfigurationError as error:
        raise StepFailedError(
            "registration", str(error), exit_code=DailyExit.no_registration
        ) from error
    except registry.HoldoutRefusedError as error:
        raise StepFailedError(
            "registration",
            f"{type(error).__name__}: {error}",
            exit_code=DailyExit.code_not_registered,
        ) from error
    admit_environment(root, code_commit=str(admitted.registered.get("code_commit")))
    body = admitted.registered
    config = body.get("config")
    if not isinstance(config, dict):
        raise StepFailedError(
            "registration",
            f"{path} registers no configuration object",
            exit_code=DailyExit.no_registration,
        )
    config_id = grid.config_id(config)
    if config_id != body.get("config_id"):
        raise StepFailedError(
            "registration",
            f"{path}'s config_id {body.get('config_id')!r} does not match its own config "
            f"(re-derived: {config_id!r}); the registration file may have been edited after it "
            "was written",
            # Not "no registration": there is one, and it contradicts itself -- the same class as
            # code that is not the registered code (round 16).
            exit_code=DailyExit.code_not_registered,
        )
    settings = body.get("settings")
    settings = dict(settings) if isinstance(settings, dict) else {}
    seed = settings.get("random_seed", 0)
    return Registration(
        path=path,
        sha256=hashlib.sha256(admitted.content).hexdigest(),
        commit=admitted.commit,
        code_commit=str(body.get("code_commit")),
        config=config,
        config_id=config_id,
        # A bool is an int to Python and not a seed: it is read as no seed.
        seed=seed if isinstance(seed, int) and not isinstance(seed, bool) else 0,
        settings=settings,
    )


LOCKFILE: Final[str] = "uv.lock"
"""The registered third-party environment: bound with the code (`registry.REGISTERED_PATHS`), so
the checkout's copy is the one committed at the registration's `code_commit`."""


def _distribution_name(name: str) -> str:
    """PEP 503's normalised form, which is how `uv.lock` spells every name."""
    return re.sub(r"[-_.]+", "-", name).lower()


PYTHON_VERSION_FILE: Final[str] = ".python-version"
"""The interpreter the research ran on, by `major.minor`: what `uv` builds the project with."""

_VERSION_MARKERS: Final[frozenset[str]] = frozenset(
    {"python_version", "python_full_version", "implementation_version"}
)
_TEXT_MARKERS: Final[frozenset[str]] = frozenset(
    {
        "implementation_name",
        "os_name",
        "platform_machine",
        "platform_python_implementation",
        "platform_release",
        "platform_system",
        "platform_version",
        "sys_platform",
    }
)
_MARKER_TOKEN: Final = re.compile(
    r"\s*(?:(?P<text>'[^']*'|\"[^\"]*\")|(?P<op>===|==|!=|<=|>=|~=|<|>|\(|\))"
    r"|(?P<word>[A-Za-z_][A-Za-z0-9_]*))"
)
_RELEASE: Final = re.compile(r"\d+(?:\.\d+)*")


def marker_environment() -> dict[str, str]:
    """PEP 508's marker environment for the running interpreter, from the standard library."""
    implementation = sys.implementation.version
    implementation_version = f"{implementation.major}.{implementation.minor}.{implementation.micro}"
    if implementation.releaselevel != "final":
        implementation_version += f"{implementation.releaselevel[0]}{implementation.serial}"
    return {
        "implementation_name": sys.implementation.name,
        "implementation_version": implementation_version,
        "os_name": os.name,
        "platform_machine": platform.machine(),
        "platform_python_implementation": platform.python_implementation(),
        "platform_release": platform.release(),
        "platform_system": platform.system(),
        "platform_version": platform.version(),
        "python_full_version": platform.python_version(),
        "python_version": ".".join(platform.python_version_tuple()[:2]),
        "sys_platform": sys.platform,
    }


def _marker_tokens(marker: str) -> list[tuple[str, str]]:
    tokens: list[tuple[str, str]] = []
    position = 0
    while position < len(marker.rstrip()):
        found = _MARKER_TOKEN.match(marker, position)
        if found is None or found.end() == position:
            raise ValueError(f"marker {marker!r} cannot be read at {marker[position:]!r}")
        kind = found.lastgroup
        assert kind is not None
        tokens.append((kind, found.group(kind)))
        position = found.end()
    return tokens


def _release(version: str, marker: str) -> tuple[int, ...]:
    if _RELEASE.fullmatch(version) is None:
        raise ValueError(f"marker {marker!r} compares {version!r}, which is not a release number")
    return tuple(int(part) for part in version.split("."))


def _padded(left: tuple[int, ...], right: tuple[int, ...]) -> tuple[tuple[int, ...], ...]:
    width = max(len(left), len(right))
    return tuple(side + (0,) * (width - len(side)) for side in (left, right))


def _compare_versions(have: str, op: str, want: str, marker: str) -> bool:
    """`have op want` for release numbers: `==`/`!=` take a `.*` prefix, `~=` is PEP 440's."""
    if op == "===":
        return have == want
    if op in {"==", "!="} and want.endswith(".*"):
        prefix = _release(want[:-2], marker)
        release = _release(have, marker)
        release = release + (0,) * max(0, len(prefix) - len(release))
        return (release[: len(prefix)] == prefix) is (op == "==")
    left, right = _padded(_release(have, marker), _release(want, marker))
    if op == "~=":
        wanted = _release(want, marker)
        if len(wanted) < 2:
            raise ValueError(f"marker {marker!r} uses ~= with a single-part version")
        return left >= right and left[: len(wanted) - 1] == wanted[:-1]
    outcomes = {
        "==": left == right,
        "!=": left != right,
        "<": left < right,
        "<=": left <= right,
        ">": left > right,
        ">=": left >= right,
    }
    if op not in outcomes:
        raise ValueError(f"marker {marker!r} uses {op!r} on a version")
    return outcomes[op]


def _marker_comparison(
    left: tuple[str, str],
    op: str,
    right: tuple[str, str],
    marker: str,
    environment: Mapping[str, str],
) -> bool:
    """One `operand op operand` whose one side is a variable and the other a quoted value."""
    names = [value for kind, value in (left, right) if kind == "word"]
    if len(names) != 1:
        raise ValueError(f"marker {marker!r} compares {left[1]} with {right[1]}")
    (name,) = names
    if name not in _VERSION_MARKERS | _TEXT_MARKERS or name not in environment:
        raise ValueError(f"marker {marker!r} names {name!r}, which this evaluator does not read")

    def value(operand: tuple[str, str]) -> str:
        kind, token = operand
        return environment[token] if kind == "word" else token[1:-1]

    have, want = value(left), value(right)
    if op == "in":
        return have in want
    if op == "not in":
        return have not in want
    if name in _VERSION_MARKERS and left[0] == "word":
        return _compare_versions(have, op, want, marker)
    if op == "==":
        return have == want
    if op == "!=":
        return have != want
    raise ValueError(f"marker {marker!r} orders text with {op!r}")


def marker_holds(marker: str, environment: Mapping[str, str]) -> bool:
    """Whether a `uv.lock` marker holds in `environment`, or `ValueError` if it cannot be read.

    The PEP 508 subset `uv.lock` writes -- `or` over `and` over parenthesised groups or
    `variable op 'value'` comparisons -- evaluated with the standard library alone: `packaging`,
    which implements the full grammar, is only a test dependency here, and the daily command does
    not add a runtime one. Versions compare as release numbers, everything else as text. A marker
    outside that subset -- an `extra`, a variable it does not know, a version that is not a
    release number -- is an error, never a guess: the admission it feeds refuses instead.
    """
    tokens = _marker_tokens(marker)
    position = 0

    def peek() -> tuple[str, str] | None:
        return tokens[position] if position < len(tokens) else None

    def take() -> tuple[str, str]:
        nonlocal position
        token = peek()
        if token is None:
            raise ValueError(f"marker {marker!r} ends too early")
        position += 1
        return token

    def disjunction() -> bool:
        holds = conjunction()
        while peek() == ("word", "or"):
            take()
            holds = conjunction() or holds
        return holds

    def conjunction() -> bool:
        holds = atom()
        while peek() == ("word", "and"):
            take()
            holds = atom() and holds
        return holds

    def atom() -> bool:
        if peek() == ("op", "("):
            take()
            holds = disjunction()
            if take() != ("op", ")"):
                raise ValueError(f"marker {marker!r} has an unclosed group")
            return holds
        left = take()
        if left[0] == "op" or left[1] in {"and", "or", "in", "not"}:
            raise ValueError(f"marker {marker!r} has {left[1]!r} where a value belongs")
        op_kind, op = take()
        if op_kind == "word" and op == "not" and take() == ("word", "in"):
            op = "not in"
        elif not (op_kind == "op" and op not in {"(", ")"}) and op != "in":
            raise ValueError(f"marker {marker!r} has {op!r} where a comparison belongs")
        right = take()
        if right[0] == "op" or right[1] in {"and", "or", "in", "not"}:
            raise ValueError(f"marker {marker!r} has {right[1]!r} where a value belongs")
        return _marker_comparison(left, op, right, marker, environment)

    holds = disjunction()
    if position != len(tokens):
        raise ValueError(f"marker {marker!r} has {tokens[position][1]!r} after its end")
    return holds


def locked_versions(
    lock_text: str, *, environment: Mapping[str, str] | None = None
) -> dict[str, str | None]:
    """The version `uv.lock` locks of each package **for this interpreter**, by normalised name;
    `None` for one it locks no version of.

    A universal lock lists a package once per fork -- numpy 2.4.6 for Pythons before 3.12 and
    2.5.1 from 3.12, in this repository's own lock -- each entry with the `resolution-markers`
    it applies under. Only an entry whose markers hold for `environment` (the running
    interpreter's, by default) counts, and one with no markers applies everywhere. A package
    every entry of which is for other interpreters is absent: installed, it is not locked for
    this one. Two entries of different versions that both hold are a lock this cannot read, and a
    `ValueError`, as is a marker `marker_holds` cannot read.

    Dependency `marker`s are not evaluated: they decide whether a package is installed on an
    interpreter, not at which version, and a locked package that is not installed is not a
    difference (`environment_differences`).
    """
    where = marker_environment() if environment is None else environment
    document = tomllib.loads(lock_text)
    versions: dict[str, set[str | None]] = {}
    for package in document.get("package", []):
        if not isinstance(package, dict) or "name" not in package:
            continue
        markers = package.get("resolution-markers")
        if markers is not None:
            if not isinstance(markers, list) or not all(isinstance(m, str) for m in markers):
                raise ValueError(f"{package['name']}'s resolution-markers are not a marker list")
            if not any(marker_holds(marker, where) for marker in markers):
                continue
        version = package.get("version")
        versions.setdefault(_distribution_name(str(package["name"])), set()).add(
            None if version is None else str(version)
        )
    locked: dict[str, str | None] = {}
    for name, found in versions.items():
        if len(found) > 1:
            raise ValueError(
                f"{LOCKFILE} locks {name} at {sorted(map(str, found))} for this interpreter"
            )
        (locked[name],) = found
    return locked


def installed_distributions() -> dict[str, str]:
    """Every distribution the running interpreter can import, by normalised name."""
    return {
        _distribution_name(distribution.metadata["Name"]): distribution.version
        for distribution in importlib.metadata.distributions()
        if distribution.metadata["Name"]
    }


def environment_differences(
    locked: Mapping[str, str | None], installed: Mapping[str, str]
) -> list[str]:
    """Where the running environment is not the locked one, one sentence per distribution.

    Every **installed** distribution has to be in the lock at the locked version. A locked
    package that is not installed is not a difference: the lock covers every platform, Python
    version and extra (`akshare` is an extra, and one wheel per interpreter is listed), and a
    package the code needs and lacks fails at import rather than computing anything.
    """
    differences: list[str] = []
    for name, version in sorted(installed.items()):
        if name not in locked:
            differences.append(
                f"{name} {version} is installed and {LOCKFILE} does not lock it for this "
                "interpreter"
            )
        elif locked[name] is not None and locked[name] != version:
            differences.append(f"{name} {version} is installed and {LOCKFILE} pins {locked[name]}")
    return differences


def pinned_python(text: str, *, source: str) -> str:
    """The `major.minor` a `.python-version` names (`3.11`, `3.11.14`, `cpython@3.11`, ...)."""
    lines = [line.strip() for line in text.splitlines()]
    named = [line for line in lines if line and not line.startswith("#")]
    found = re.fullmatch(r"(?:[A-Za-z]+[@-])?(\d+)\.(\d+)(?:\.\d+)?", named[0]) if named else None
    if found is None:
        raise StepFailedError(
            "registration",
            f"{source} names no Python version this command can read: {text.strip()!r}",
            exit_code=DailyExit.code_not_registered,
        )
    return f"{found.group(1)}.{found.group(2)}"


def admit_interpreter(root: Path, *, code_commit: str) -> None:
    """The running interpreter is the one the research ran on, by `major.minor`, or the run stops.

    Read from the registered commit's `.python-version` (`git show <code_commit>:...`), so an
    edit to the checkout's working tree does not move it. Python's own `major.minor` changes the
    numerical libraries a lock resolves to (this repository's lock pins a different numpy on
    3.12), so a different interpreter is a different computation even with the lock obeyed.
    """
    source = f"{code_commit}:{PYTHON_VERSION_FILE}"
    run = registry._git(root, "show", source)
    if run.returncode != 0:
        raise StepFailedError(
            "registration",
            f"the registered commit has no {PYTHON_VERSION_FILE} ({source}), so the interpreter "
            "the research ran on is not known",
            exit_code=DailyExit.code_not_registered,
        )
    pinned = pinned_python(run.stdout.decode(errors="replace"), source=source)
    running = f"{sys.version_info.major}.{sys.version_info.minor}"
    if running != pinned:
        raise StepFailedError(
            "registration",
            f"the running interpreter ({sys.executable}) is Python {running}, and the registered "
            f"{PYTHON_VERSION_FILE} pins {pinned}. Run from the checkout --pin-worktree made, "
            "whose environment is built on that interpreter",
            exit_code=DailyExit.code_not_registered,
        )


def admit_environment(root: Path, *, code_commit: str) -> None:
    """Step 1's second half (`V2-P6-011`): the running interpreter is the registered one
    (`admit_interpreter`) and its third-party packages are exactly the ones `<root>/uv.lock`
    locks **for that interpreter** (`locked_versions`), or the run stops.

    The bound code is compared with the registration's commit (`registry.admit_registered_code`,
    whose bound paths include `uv.lock`), and that says nothing about what the interpreter
    actually imports: a scheduled run started from a shared virtual environment would compute
    with whatever that environment was last synced to. `--pin-worktree` gives the pinned checkout
    its own environment, synced from its own lock offline (`sync_pinned_environment`); this is
    the defence behind that -- it refuses a run whose environment is not that lock, by name.
    """
    admit_interpreter(root, code_commit=code_commit)
    lock = root / LOCKFILE
    if not lock.is_file():
        raise StepFailedError(
            "registration",
            f"{lock} does not exist, so the environment this run computes with cannot be "
            "compared with a registered one",
            exit_code=DailyExit.code_not_registered,
        )
    try:
        locked = locked_versions(lock.read_text(encoding="utf-8"))
    except (ValueError, tomllib.TOMLDecodeError) as error:
        raise StepFailedError(
            "registration",
            f"the running environment cannot be compared with {lock}: {error}",
            exit_code=DailyExit.code_not_registered,
        ) from error
    differences = environment_differences(locked, installed_distributions())
    if differences:
        shown = "; ".join(differences[:8])
        more = f" (and {len(differences) - 8} more)" if len(differences) > 8 else ""
        raise StepFailedError(
            "registration",
            f"the running interpreter ({sys.executable}) is not the environment {lock} pins: "
            f"{shown}{more}. Run from the checkout --pin-worktree made, whose environment is "
            "synced from its own lock",
            exit_code=DailyExit.code_not_registered,
        )


def _decimal(value: object, key: str) -> Decimal:
    try:
        return Decimal(str(value))
    except ArithmeticError as error:
        raise ValueError(f"{key} is not a number: {value!r}") from error


def strategy_arguments(config: Mapping[str, Any]) -> dict[str, Any]:
    """The registered configuration as `strategy_request`'s keywords, less the dates.

    The registration stores the configuration in the research ledger's canonical JSON -- dates
    and decimals as text, tuples as lists -- so this reads it back into the types
    `OpenAlphaSDK.run_strategy_backtest` took when the holdout measured it. A key that is not
    one of those arguments is refused rather than ignored: a configuration this command cannot
    read whole is not the one that was registered.
    """
    unknown = sorted(set(config) - _STRATEGY_KEYS)
    if unknown:
        raise ValueError(f"the configuration names {unknown}, which no backtest argument reads")
    arguments: dict[str, Any] = {
        "combine": config.get("combine", "zscore_sum"),
        "components": tuple(
            (str(factor), str(tier), _decimal(weight, "a component weight"))
            for factor, tier, weight in config.get("components", ())
        ),
        "prediction_ids": tuple(config.get("prediction_ids", ())),
        "transform": config.get("transform"),
        "neutralization": config.get("neutralization"),
        "exchange": config.get("exchange", TRADING_CALENDAR_DEFAULT_EXCHANGE),
        "rebalance_every_sessions": int(config["rebalance_every_sessions"]),
        "holding_count": int(config["holding_count"]),
        "buffer_rank": None if config.get("buffer_rank") is None else int(config["buffer_rank"]),
        "max_industry_weight": (
            None
            if config.get("max_industry_weight") is None
            else _decimal(config["max_industry_weight"], "max_industry_weight")
        ),
        "trailing_ic": config.get("trailing_ic"),
        "walk_forward": config.get("walk_forward"),
    }
    for key in ("position_capital", "participation_cap", "slippage_rate"):
        if config.get(key) is not None:
            arguments[key] = _decimal(config[key], key)
    if config.get("benchmarks") is not None:
        arguments["benchmarks"] = tuple(config["benchmarks"])
    return arguments


def anchor_of(config: Mapping[str, Any]) -> date:
    """The day a backtest of the configuration starts on: its rebalance and refit schedules
    count sessions from here."""
    return date.fromisoformat(str(config["start"]))


def _registered_anchor(registration: Registration) -> date:
    try:
        return anchor_of(registration.config)
    except (KeyError, ValueError) as error:
        raise StepFailedError(
            "registration",
            f"the registered configuration names no start date: {error}",
            exit_code=DailyExit.no_registration,
        ) from error


def day_request(config: Mapping[str, Any], *, day: date, as_of: datetime) -> StrategyRequest:
    """The configuration as a `StrategyRequest` read at `as_of` about `day`."""
    try:
        arguments = strategy_arguments(config)
    except (KeyError, TypeError, ValueError) as error:
        raise StepFailedError(
            "registration",
            f"the registered configuration cannot be read: {error}",
            exit_code=DailyExit.no_registration,
        ) from error
    try:
        return strategy_request(**arguments, start=day - timedelta(days=1), end=day, as_of=as_of)
    except StrategyViewError as error:
        raise StepFailedError(
            "registration",
            f"the registered configuration is not a request: {error}",
            exit_code=DailyExit.no_registration,
        ) from error


@dataclass(frozen=True, slots=True, kw_only=True)
class TierBuild:
    """One factor tier the configuration reads, and the specs that name it."""

    factor: str
    tier: str
    transform: str | None
    neutralization: str | None


_TIER_ORDER: Final[Mapping[str, int]] = {"raw": 0, "processed": 1, "neutralized": 2}


def tiers_read(request: StrategyRequest) -> tuple[TierBuild, ...]:
    """Every factor tier the source reads, each factor at the highest tier it needs.

    A tier's build writes the tiers beneath it at the same instant, so a factor read at `raw`
    and at `processed` is one `processed` build.
    """
    source = request.source
    wanted: dict[tuple[str, str | None, str | None], str] = {}
    transform = None if request.transform is None else request.transform.qualified_key
    neutralization = (
        None if request.neutralization is None else request.neutralization.qualified_key
    )
    if source.walk_forward is not None:
        for token in source.walk_forward.features:
            factor, _, rest = token.partition("@")
            tier, *specs = rest.split(":")
            specs += [""] * (2 - len(specs))
            key = (factor.strip(), specs[0] or None, specs[1] or None)
            wanted[key] = max(wanted.get(key, tier), tier, key=_TIER_ORDER.__getitem__)
    else:
        pairs = (
            source.trailing_ic.components
            if source.trailing_ic is not None
            else tuple((token, tier) for token, tier, _ in source.components)
        )
        for token, tier in pairs:
            key = (
                token,
                transform if tier != "raw" else None,
                neutralization if tier == "neutralized" else None,
            )
            wanted[key] = max(wanted.get(key, tier), tier, key=_TIER_ORDER.__getitem__)
    return tuple(
        TierBuild(factor=factor, tier=tier, transform=spec, neutralization=neutral)
        for (factor, spec, neutral), tier in sorted(wanted.items(), key=lambda item: item[0][0])
    )


def reads_neutralized(builds: Sequence[TierBuild]) -> bool:
    """Whether a factor build reads industries: a neutralized tier, every day it is built."""
    return any(build.tier == "neutralized" for build in builds)


def targets_for(builds: Sequence[TierBuild]) -> tuple[str, ...]:
    """The panel targets the day's scoring reads, industries aside: `BASE_TARGETS` plus the
    target writing each dataset a registered factor reads, in `PANEL_BUILD_TARGETS`' order."""
    writes = {
        dataset: target
        for target, datasets in cli.PANEL_BUILD_TARGETS.items()
        for dataset in datasets
    }
    read = {
        writes[dataset] for build in builds for dataset in resolve_factor(build.factor).datasets
    }
    wanted = set(BASE_TARGETS) | (read - set(INDUSTRY_TARGETS))
    return tuple(target for target in cli.PANEL_BUILD_TARGETS if target in wanted)


def outcome_horizon(request: StrategyRequest) -> int:
    """How many sessions a day's registered scores are about: the model's horizon for a
    walk-forward source, the rebalance interval otherwise (`strategy_registration`)."""
    model = request.source.walk_forward
    return model.horizon_sessions if model is not None else request.spec.rebalance_every_sessions


# --- running the CLI in-process, and counting what it asked the network ------------------------


class CountingTransport:
    """A `TushareTransport` that counts requests by `api_name` and forwards them untouched.

    It reads `api_name` and nothing else of the payload, stores nothing of it and prints
    nothing: the credential the provider put in the envelope passes through as it would pass
    through the HTTP transport itself.
    """

    def __init__(self, inner: TushareTransport, counts: Counter[str]) -> None:
        self._inner = inner
        self._counts = counts

    def post(self, payload: dict[str, Any]) -> dict[str, Any]:
        self._counts[str(payload.get("api_name"))] += 1
        return self._inner.post(payload)


@contextlib.contextmanager
def counted_transport(counts: Counter[str]) -> Iterator[None]:
    """Route `panel build`'s fetches through a `CountingTransport` for the duration.

    `cli._panel_transport` is the one seam that command fetches through (its docstring: "tests
    replace this and everything above it runs for real"). Whatever it is when this is entered --
    the HTTP transport, or a test's scripted one -- is what the counter wraps, and it is put back
    on the way out.
    """
    original = cli._panel_transport

    def counting() -> TushareTransport:
        return CountingTransport(original(), counts)

    cli._panel_transport = counting
    try:
        yield
    finally:
        cli._panel_transport = original


@dataclass(frozen=True, slots=True, kw_only=True)
class Invocation:
    exit_code: int
    stdout: str
    stderr: str

    def payloads(self) -> list[Any]:
        """Every stdout line that is a JSON document, in order."""
        found: list[Any] = []
        for line in self.stdout.splitlines():
            if line.startswith(("{", "[")):
                with contextlib.suppress(ValueError):
                    found.append(json.loads(line))
        return found

    def reason(self) -> str:
        """The command's own account of a refusal: the last twelve lines of its stderr and stdout
        that are findings rather than status or progress (`READY`, `CHECK`, `INFO`, a clean
        gate's `UNVERIFIED`, and `panel build`'s `INCREMENTAL`/`BUDGET`/`FETCHING`/`WROTE` lines
        are left out, so the refusal, which comes last, is never cut), else its last lines."""
        routine = (
            "READY ",
            "CHECK ",
            "INFO ",
            "UNVERIFIED ",
            "CLEARED ",
            "INCREMENTAL ",
            "BUDGET ",
            "FETCHING ",
            "WROTE ",
            "SESSIONS ",
            "AS-OF ",
            "HALTS ",
        )
        findings = [
            line
            for line in (*self.stderr.splitlines(), *self.stdout.splitlines())
            if line.strip() and not line.startswith(routine)
        ]
        if findings:
            return " | ".join(findings[-12:])
        text = (self.stderr.strip() or self.stdout.strip()).splitlines()
        return " | ".join(text[-6:]) if text else f"exit {self.exit_code} with no output"


def invoke(arguments: Sequence[str]) -> Invocation:
    """Run one `openalpha` command in this process and capture what it printed.

    Through the Typer app itself, so every flag is parsed, every guard runs and every exit code
    is the command's own. Without standalone mode a deliberate `typer.Exit` comes back as its
    code and a usage error as a `ClickException`, which is reported with its own message.
    """
    command = get_command(cli.app)
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            result = command.main(
                args=list(arguments), prog_name="openalpha", standalone_mode=False
            )
            code = int(result) if isinstance(result, int) else 0
        except ClickException as error:
            print(error.format_message(), file=sys.stderr)
            code = error.exit_code
        except SystemExit as error:
            code = error.code if isinstance(error.code, int) else 1
    return Invocation(exit_code=code, stdout=out.getvalue(), stderr=err.getvalue())


# --- the journal -------------------------------------------------------------------------------


def journal_directory(runtime_dir: Path, registration: Registration) -> Path:
    return runtime_dir / JOURNAL_DIRECTORY / registration.config_id[:16]


def read_journal(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    body = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(body, dict) or body.get("schema") != DAILY_SELECTION_SCHEMA:
        raise StepFailedError("summary", f"{path} is not a {DAILY_SELECTION_SCHEMA} journal")
    return body


def write_journal(path: Path, body: Mapping[str, Any]) -> None:
    """Write the day's journal whole, through a temporary file, unless it already says this."""
    text = json.dumps(body, sort_keys=True, indent=2, ensure_ascii=False) + "\n"
    if path.is_file() and path.read_text(encoding="utf-8") == text:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.partial")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)


def previous_targets(directory: Path, session: date) -> tuple[date | None, dict[str, str]]:
    """The newest journalled targets before `session`: what the book held coming into today."""
    if not directory.is_dir():
        return None, {}
    earlier = sorted(
        (
            date.fromisoformat(path.stem)
            for path in directory.glob("????-??-??.json")
            if date.fromisoformat(path.stem) < session
        ),
        reverse=True,
    )
    for day in earlier:
        body = read_journal(directory / f"{day.isoformat()}.json")
        if body is not None and "result" in body:
            return day, dict(body["result"]["targets"]["weights"])
    return None, {}


@dataclass(frozen=True, slots=True, kw_only=True)
class JournalledDay:
    """One session the command completed, as its journal says (`V2-P6-011`, for `V2-P6-012`).

    `decision` is step 6's: `rebalanced`, `held` (the source ranked nothing, so the book kept
    yesterday's) or `not a rebalance day`. `record_id` is the record step 7 registered or found,
    `None` on a day the source held -- which is evidence the book did not rebalance there, not a
    gap (`source_held`). `weights` are the targets the command printed.
    """

    session: date
    decision: str
    reason: str
    source_held: bool
    record_id: str | None
    weights: Mapping[str, str]
    as_of: datetime
    """The instant the day's run pinned its clock to: what a held day is re-scored at."""


def journalled_days(directory: Path) -> tuple[JournalledDay, ...]:
    """Every session the command completed under `directory` (`journal_directory`), ascending.

    A journal without a `result` -- a run refused or stopped before its summary -- is not a day
    the command recommended anything on, and is left out: the next run catches its rebalance up
    (`Schedule.due`), and that run's journal says so.
    """
    if not directory.is_dir():
        return ()
    days: list[JournalledDay] = []
    for path in sorted(directory.glob("????-??-??.json")):
        body = read_journal(path)
        if body is None or "result" not in body:
            continue
        try:
            result = body["result"]
            targets, prediction = result["targets"], result["prediction"]
            days.append(
                JournalledDay(
                    session=date.fromisoformat(result["session"]),
                    decision=str(targets["decision"]),
                    reason=str(targets["reason"]),
                    source_held=bool(result["candidates"]["held"]),
                    record_id=(
                        str(prediction["record_id"]) if prediction.get("registered") else None
                    ),
                    weights=dict(targets["weights"]),
                    as_of=datetime.fromisoformat(str(result["as_of"])),
                )
            )
        except (KeyError, TypeError, AttributeError) as error:
            raise StepFailedError(
                "summary", f"{path} has no {error} where the journal of a completed day has one"
            ) from error
    return tuple(days)


def journalled_rebalances(directory: Path) -> tuple[tuple[date, str], ...]:
    """The sessions the journal says the command rebalanced on, each with the record it used.

    **The journal's word only**: a local file an edit could move a rebalance in. The forward
    report prices `forward_rebalances`, which re-derives these days from the prediction store
    and holds the journal to them. It differs from the fixed grid exactly where the command did:
    a day the source held is not a rebalance, and a missed or refused run's rebalance is made at
    the next run. A rebalance journalled without a record is refused.
    """
    rebalances: list[tuple[date, str]] = []
    for day in journalled_days(directory):
        if day.decision != "rebalanced":
            continue
        if day.record_id is None:
            raise StepFailedError(
                "summary",
                f"the journal of {day.session.isoformat()} rebalanced with no record registered; "
                "the book it recommended cannot be priced from the prediction store",
            )
        rebalances.append((day.session, day.record_id))
    return tuple(rebalances)


_LOG: Final = logging.getLogger("openalpha.daily_selection")

PROVENANCE_DIRECTORY: Final[str] = "daily_selection_provenance"
"""Under the runtime directory: each record's input provenance, `<digest>.json`, write-once."""

VERDICT_DIRECTORY: Final[str] = "reports/record_verdicts"
"""Under the runtime directory: each record's `verified` verdict, `<record>.<provenance>.json`,
write-once -- a record is recomputed once, and again only when its inputs have changed."""


def _request_at(registration: Registration) -> Callable[[date, datetime], StrategyRequest]:
    def request_for(day: date, as_of: datetime) -> StrategyRequest:
        return day_request(registration.config, day=day, as_of=as_of)

    return request_for


def _write_once(path: Path, body: Mapping[str, Any]) -> None:
    """Write a content-addressed document, or check the one already there says the same."""
    text = json.dumps(body, sort_keys=True, indent=2, ensure_ascii=False) + "\n"
    if path.is_file():
        if path.read_text(encoding="utf-8") != text:
            raise StepFailedError("prediction", f"{path} is held and says something else")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.partial")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)


def write_provenance(runtime_dir: Path, provenance: InputProvenance) -> Path:
    """File a record's input provenance before the record (`V2-P6-011` round 11).

    Its own small content-addressed file rather than a field of the record: a
    `PredictionRecord` is `extra="forbid"` under `alpha-prediction-record/v1`, and a field would
    be a contract change (hard rule 3); `supersedes` and the batch are taken. The file is named by
    the digest of what it says, written once and never rewritten -- the prediction store's own
    discipline -- and names the batch it was written for, so it binds to exactly one record.
    """
    path = runtime_dir / PROVENANCE_DIRECTORY / f"{provenance.digest}.json"
    _write_once(path, provenance.document())
    return path


def provenance_lookup(runtime_dir: Path, registration_sha256: str) -> ProvenanceLookup:
    """The provenance this registration's daily runs wrote for a record's batch, or `None`."""
    directory = runtime_dir / PROVENANCE_DIRECTORY
    held: dict[tuple[date, str], InputProvenance] = {}
    for path in sorted(directory.glob("*.json")) if directory.is_dir() else ():
        try:
            provenance = InputProvenance.from_document(json.loads(path.read_text(encoding="utf-8")))
        except (
            ValueError,
            TypeError,
            KeyError,
            IndexError,
            AttributeError,
            StrategyRegistrationError,
        ) as error:
            raise StepFailedError(
                "summary", f"{path} is not a provenance this command can read ({error!r})"
            ) from error
        if provenance.digest != path.stem:
            raise StepFailedError("summary", f"{path} is not what its name says it is")
        if provenance.registration_sha256 != registration_sha256:
            continue
        key = (provenance.session, provenance.batch_digest)
        if key in held:
            raise StepFailedError(
                "summary",
                f"two provenance files name the batch {provenance.batch_digest} of "
                f"{provenance.session.isoformat()}: {held[key].digest}.json and {path.name}; a "
                "filing has one, and neither can be told to be the one its run wrote",
            )
        held[key] = provenance

    def lookup(record: PredictionRecord) -> InputProvenance | None:
        day = record.batch.as_of.astimezone(SHANGHAI).date()
        return held.get((day, batch_digest(record.batch)))

    return lookup


class FileVerdicts:
    """`strategy_registration.VerdictCache` over `VERDICT_DIRECTORY`: one write-once file per
    verified record, provenance and partition set -- `<record>.<provenance>.<partitions>.json`.

    **A cache, inside the threat model and no further.** It saves recomputing a record whose
    inputs have not moved; it is not evidence. A kept verdict is trusted only while every
    partition it was verified under still hashes as it did (`RecordCheck` rechecks them before
    trusting it), so a bug or an upstream correction sends the record back to verification. Like
    every artifact here it is a local file: someone who controls this disk can write a verdict
    as easily as a record, and nothing local defends against that (`INTEGRITY`).
    """

    def __init__(self, runtime_dir: Path, *, clock: Callable[[], datetime]) -> None:
        self._directory = runtime_dir / VERDICT_DIRECTORY
        self._clock = clock

    def verified_under(
        self, record_id: str, provenance_digest: str
    ) -> tuple[tuple[InputPartition, ...], ...]:
        if not self._directory.is_dir():
            return ()
        kept = []
        for path in sorted(self._directory.glob(f"{record_id}.{provenance_digest}.*.json")):
            try:
                body = json.loads(path.read_text(encoding="utf-8"))
                if body["verdict"] != "verified":
                    raise ValueError(f"verdict {body['verdict']!r}")
                partitions = tuple(
                    InputPartition(
                        dataset=str(item[0]),
                        year=int(item[1]),
                        content_hash=str(item[2]),
                        rows_digest=str(item[3]),
                    )
                    for item in body["partitions"]
                )
            except (ValueError, TypeError, KeyError, IndexError, AttributeError):
                # Accidental corruption is inside the threat model, and a cache entry that does
                # not parse is a miss: the record is recomputed, and the file is named.
                _LOG.warning(
                    "ignoring the unreadable verdict file %s; the record is recomputed", path.name
                )
                continue
            kept.append(partitions)
        return tuple(kept)

    def keep(
        self, record_id: str, provenance_digest: str, partitions: tuple[InputPartition, ...]
    ) -> None:
        listed = [
            [item.dataset, item.year, item.content_hash, item.rows_digest] for item in partitions
        ]
        digest = hashlib.sha256(
            json.dumps(listed, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        path = self._directory / f"{record_id}.{provenance_digest}.{digest}.json"
        if path.is_file():
            return
        _write_once(
            path,
            {
                "record_id": record_id,
                "provenance": provenance_digest,
                "partitions": listed,
                "verdict": "verified",
                "verified_at": self._clock().isoformat(),
            },
        )


def forward_record_check(
    runtime_dir: Path, registration: Registration, *, as_of: datetime
) -> RecordCheck:
    """The check a forward book holds a record registered after its signal instant to
    (`strategy_view.backtest_strategy(verify_late=...)`): bound to this registration (a
    walk-forward record through its provenance), recomputed at its own filing time from the
    panel store under `runtime_dir`, and -- where an input it read was corrected since --
    admitted as `UNVERIFIABLE` and listed on the check's `unverifiable`. Verified verdicts are
    kept under `VERDICT_DIRECTORY`."""
    return RecordCheck(
        panel_store(runtime_dir),
        registration.declared,
        request_for=_request_at(registration),
        anchor=_registered_anchor(registration),
        provenance_for=provenance_lookup(runtime_dir, registration.sha256),
        verdicts=FileVerdicts(runtime_dir, clock=lambda: as_of),
    )


@dataclass(frozen=True, slots=True, kw_only=True)
class ForwardSchedule:
    """The forward book's days as the store witnesses them (`forward_rebalances`).

    `first_record` is where the witnessed period starts: the earliest session carrying an on-time
    record bound to the registration. `unprovable_holds` are the journal's held days before it --
    a hold leaves no record, so the store cannot tell one from a day the command never ran, and
    they are stated rather than counted.
    """

    rebalances: tuple[tuple[date, str], ...]
    days: tuple[WitnessedDay, ...]
    first_record: date
    unprovable_holds: tuple[date, ...]


def forward_rebalances(
    runtime_dir: Path, registration: Registration, *, through: date, as_of: datetime
) -> ForwardSchedule:
    """The sessions the command rebalanced on through `through`, each with its record, as the
    append-only prediction store witnesses them (`V2-P6-011`, for `V2-P6-012`).

    `strategy_registration.witnessed_days` re-derives every decision from the store, from the
    **store's** first record: the journal cannot choose where the forward book starts. A journal
    starting later is refused (a first day deleted); a journal day before the first record may
    only be a hold, and those are returned as `unprovable_holds`. From the first record on, a
    held day counts only when the journal completed it holding and the configuration, scored
    again, holds. Every completed day is then held to the journal -- decision and record -- and a
    difference refuses the book naming the day.
    """
    journalled = [
        day
        for day in journalled_days(journal_directory(runtime_dir, registration))
        if day.session <= through
    ]
    store = panel_store(runtime_dir)
    anchor = _registered_anchor(registration)
    request_for = _request_at(registration)
    # The next year too, when stored: a record on the year's last session is cut off at 09:15 on
    # the next year's first (`registration_cutoff`), as `_outcome_calendar` places its outcome.
    calendar = _stored_calendar(
        store,
        request_for(anchor, as_of).exchange,
        tuple(range(anchor.year, through.year + 2)),
        as_of,
    )
    if calendar is None:
        raise StepFailedError(
            "summary", f"the calendar from {anchor.year} to {through.year} is not stored"
        )
    records = build_storage(runtime_dir=runtime_dir, clock=lambda: as_of).prediction_store
    try:
        witnessed = witnessed_days(
            store,
            records,
            registration.declared,
            request_for=request_for,
            as_of=as_of,
            anchor=anchor,
            calendar=calendar,
            through=through,
            journalled_holds={
                day.session: day.as_of for day in journalled if day.decision == "held"
            },
            provenance_for=provenance_lookup(runtime_dir, registration.sha256),
        )
    except StrategyRegistrationError as error:
        raise StepFailedError("summary", str(error)) from error
    if not witnessed:
        raise StepFailedError(
            "summary",
            f"the prediction store holds no on-time record of this registration by {through}",
        )
    first = witnessed[0].session
    if not journalled or journalled[0].session > first:
        raise StepFailedError(
            "summary",
            f"the store's first record of this registration is of {first.isoformat()} and the "
            f"journal starts {journalled[0].session.isoformat() if journalled else 'nowhere'}; "
            "a journal missing its first days would move where the forward book starts",
        )
    before = [day for day in journalled if day.session < first]
    unheld = [day.session.isoformat() for day in before if day.decision != "held"]
    if unheld:
        raise StepFailedError(
            "summary",
            f"the journal says the command {unheld} ranked before the store's first record "
            f"({first.isoformat()}); the store is the witness",
        )
    derived = {day.session: (day.decision, day.record_id) for day in witnessed}
    written = {
        day.session: (day.decision, day.record_id) for day in journalled if day.session >= first
    }
    for session in sorted(set(derived) | set(written)):
        if derived.get(session) != written.get(session):
            raise StepFailedError(
                "summary",
                f"the journal of {session.isoformat()} says {written.get(session)} and the store "
                f"witnesses {derived.get(session)} (decision, record); the store is the witness, "
                "and a journal that disagrees with it is not a book to price",
            )
    return ForwardSchedule(
        rebalances=tuple(
            (day.session, day.record_id)
            for day in witnessed
            if day.decision == "rebalanced" and day.record_id is not None
        ),
        days=witnessed,
        first_record=first,
        unprovable_holds=tuple(day.session for day in before),
    )


INTEGRITY: Final[str] = (
    "integrity: every artifact here -- the prediction store and its recorded_at clock, the "
    "journal, the input provenance, the kept verdicts -- is a local file. The checks defend "
    "against honest operation, bugs, accidental corruption and upstream corrections; they do not "
    "defend against deliberate tampering by whoever controls this disk, which needs an external "
    "append-only witness"
)
"""The threat model, stated wherever a forward book is reported (`V2-P6-011` round 12)."""


def _book_statistics(periods: Sequence[Any]) -> dict[str, Any]:
    """Compounded net and per-benchmark returns and the mean period net, over `periods`."""
    net = Decimal(1)
    benchmarks: dict[str, Decimal] = {}
    for period in periods:
        net *= Decimal(1) + period.net_return
        for name, value in period.benchmark_returns.items():
            benchmarks[name] = benchmarks.get(name, Decimal(1)) * (Decimal(1) + value)
    count = len(periods)
    total = sum((period.net_return for period in periods), Decimal(0))
    return {
        "periods": count,
        "compounded_net_return": f"{(net - 1).quantize(_WEIGHT_QUANTUM):f}",
        "mean_period_net_return": (
            None if not count else f"{(total / count).quantize(_WEIGHT_QUANTUM):f}"
        ),
        "compounded_benchmark_returns": {
            name: f"{(value - 1).quantize(_WEIGHT_QUANTUM):f}"
            for name, value in sorted(benchmarks.items())
        },
    }


SIGNIFICANCE_TESTED: Final[str] = (
    "grid.strategy_result, the holdout's own: a sign-flip test of the non-overlapping periods' "
    "net excess over the registration's excess_benchmark (one-sided p), over complete periods "
    "only -- a period of at least the registered rebalance interval in sessions. A shorter "
    "period (the book's open last period, a catch-up's) is shown in the statistics and not "
    "tested. The periods are the sessions the command rebalanced on, so the tested ones need "
    "not be of equal length: each one's session count is shown. 000905.SH is reported beside "
    "the test from the same result's reported_* keys and tests nothing (the protocol's rule)"
)
"""What the significance rows are, stated on every forward report."""

_SIGNIFICANCE_KEYS: Final[tuple[str, ...]] = (
    "excess_benchmark",
    "period_count",
    "excluded_incomplete_periods",
    "net_excess",
    "mean_net_excess",
    "p_excess",
    "p_excess_one_sided",
    "p_excess_exact",
    "p_excess_sign_patterns",
)


def _registered_count(settings: Mapping[str, Any], key: str) -> int:
    """`settings[key]` as an integer, refusing a missing one and a bool -- which Python counts
    as an int and no registration means as one (`Registration.seed`'s rule)."""
    value = settings.get(key)
    if not isinstance(value, int) or isinstance(value, bool):
        raise StepFailedError(
            "summary",
            f"the registration's settings give {key} as {value!r}; the forward book is tested "
            "under the registered measurement settings, and without an integer there is none to "
            "test it under",
        )
    return value


def _significance(book: Any, *, benchmark: str, samples: int, seed: int) -> dict[str, Any]:
    """`grid.strategy_result` of `book` -- one call, one source: the test against `benchmark`
    and, from the same result's `reported_*` keys, 000905.SH beside it (`grid.REPORTED_BENCHMARK`,
    which tests nothing). A book the test refuses (no complete period, a benchmark it has no
    return for) says why."""
    try:
        result = grid.strategy_result(
            book, excess_benchmark=benchmark, bootstrap_samples=samples, random_seed=seed
        )
    except StrategyBacktestError as error:
        return {benchmark: {"refused": str(error)}}
    complete = cast(list[bool], result["period_complete"])
    sessions = cast(list[int], result["period_sessions"])
    tested: dict[str, Any] = {key: result[key] for key in _SIGNIFICANCE_KEYS}
    tested["tested_periods"] = sum(complete)
    tested["tested_period_sessions"] = [
        count for count, whole in zip(sessions, complete, strict=True) if whole
    ]
    tested["bootstrap_samples"] = samples
    tested["random_seed"] = seed
    reported: dict[str, Any]
    if "reported_net_excess" in result:
        reported = {
            "reported": True,
            "net_excess": result["reported_net_excess"],
            "mean_net_excess": result["reported_mean_net_excess"],
            "tested_periods": tested["tested_periods"],
        }
    else:
        reported = {
            "refused": f"the book priced no {grid.REPORTED_BENCHMARK} return for every period"
        }
    return {benchmark: tested, grid.REPORTED_BENCHMARK: reported}


def forward_summary(
    book: Any,
    check: RecordCheck,
    schedule: ForwardSchedule,
    *,
    settings: Mapping[str, Any],
) -> dict[str, Any]:
    """What a forward report shows about the evidence behind its book (`V2-P6-012`).

    The book is priced as recommended -- every on-time bound record, `UNVERIFIABLE` ones
    included -- and its statistics are computed twice: over every period, and over the periods
    whose record verified, excluding those an `UNVERIFIABLE` record opened. Both are shown, with
    the count and each corrected partition, so a reader sees whether the corrections matter;
    `unprovable_holds` and `INTEGRITY` are stated beside them.

    Each set is also tested as the holdout tested it (`grid.strategy_result`, `SIGNIFICANCE_
    TESTED`): net excess against the registration's `excess_benchmark`, with 000905.SH reported
    beside it from the same result, under the registration's own `bootstrap_samples` and
    `random_seed` -- `settings`, which has no default: a forward p-value under other settings
    than the registered ones is not the registered test.
    """
    samples = _registered_count(settings, "bootstrap_samples")
    seed = _registered_count(settings, "random_seed")
    primary = str(settings.get("excess_benchmark", grid.PRIMARY_EXCESS_BENCHMARK))
    flagged = dict(check.unverifiable)
    opened = {session: record for session, record in schedule.rebalances}
    excluded = {session for session, record in opened.items() if record in flagged}
    kept = [period for period in book.periods if period.start not in excluded]
    subset = book.model_copy(update={"periods": tuple(kept)}) if book.periods else book
    return {
        UNVERIFIABLE: {
            "count": len(flagged),
            "records": [
                {"record_id": record, "corrected": list(changes)}
                for record, changes in sorted(flagged.items())
            ],
        },
        "statistics": {
            "all_periods": _book_statistics(book.periods),
            "excluding_unverifiable": _book_statistics(kept),
        },
        "significance": {
            "tested": SIGNIFICANCE_TESTED,
            "all_periods": _significance(book, benchmark=primary, samples=samples, seed=seed),
            "excluding_unverifiable": _significance(
                subset, benchmark=primary, samples=samples, seed=seed
            ),
        },
        "unprovable_holds": [day.isoformat() for day in schedule.unprovable_holds],
        "integrity": INTEGRITY,
    }


def forward_summary_lines(summary: Mapping[str, Any]) -> list[str]:
    """`forward_summary` as the lines a report prints, the unverifiable records first."""
    flagged = summary[UNVERIFIABLE]
    lines = [f"{UNVERIFIABLE}: {flagged['count']} record(s)"]
    for entry in flagged["records"]:
        lines.append(f"  {entry['record_id']} corrected: {', '.join(entry['corrected'])}")
    labels = (
        ("all_periods", "headline: the book as recommended"),
        (
            "excluding_unverifiable",
            f"sensitivity: the same book's periods excluding {flagged['count']} unverifiable "
            "records (a subset of one path, not a re-run; later periods keep the positions and "
            "costs those periods left)",
        ),
    )
    for name, label in labels:
        stats = summary["statistics"][name]
        lines.append(
            f"{label}: {stats['periods']} period(s), compounded net "
            f"{stats['compounded_net_return']}, mean period net {stats['mean_period_net_return']}, "
            f"benchmarks {json.dumps(stats['compounded_benchmark_returns'], sort_keys=True)}"
        )
    significance = summary["significance"]
    lines.append(f"significance: {significance['tested']}")
    for name, label in labels:
        for benchmark, shown in significance[name].items():
            head = f"  {label.split(':')[0]} vs {benchmark}: "
            if "refused" in shown:
                lines.append(head + f"not tested -- {shown['refused']}")
                continue
            if shown.get("reported"):
                lines.append(
                    head + f"reported beside the test, tests nothing; mean net excess "
                    f"{shown['mean_net_excess']} over the same {shown['tested_periods']} complete "
                    "period(s)"
                )
                continue
            draws = (
                "exact"
                if shown["p_excess_exact"]
                else f"{shown['p_excess_sign_patterns']} sign patterns, seed {shown['random_seed']}"
            )
            lines.append(
                head + f"{shown['tested_periods']} complete period(s) tested (sessions "
                f"{shown['tested_period_sessions']}), "
                f"{shown['excluded_incomplete_periods']} incomplete shown and not tested; "
                f"mean net excess {shown['mean_net_excess']}, one-sided p "
                f"{shown['p_excess_one_sided']} (two-sided {shown['p_excess']}, {draws})"
            )
    if summary["unprovable_holds"]:
        lines.append(
            "holds before the first record (unprovable, not counted): "
            + ", ".join(summary["unprovable_holds"])
        )
    lines.append(summary["integrity"])
    return lines


# --- the day's schedule --------------------------------------------------------------------------


def schedule_of(
    calendar: TradingCalendar,
    *,
    anchor: date,
    session: date,
    every: int,
    previous: date | None,
) -> Schedule:
    """The session's place on the rebalance schedule counted from `anchor`
    (`strategy_registration.schedule_of`, which `witnessed_days` reads the store with)."""
    try:
        return registered_schedule_of(
            calendar, anchor=anchor, session=session, every=every, previous=previous
        )
    except StrategyRegistrationError as error:
        raise StepFailedError("panel update", str(error)) from error


def industry_day(request: StrategyRequest, builds: Sequence[TierBuild], schedule: Schedule) -> bool:
    """Whether the day's scoring reads industry memberships.

    A neutralized tier reads the whole cross section's industries every day it is built. An
    industry cap reads them only in the rebalance decision, so only on a day a rebalance is due.
    On any other day nothing the day computes reads a membership.
    """
    capped = request.spec.max_industry_weight is not None
    return reads_neutralized(builds) or (capped and schedule.due)


# --- the steps ---------------------------------------------------------------------------------


@dataclass(slots=True, kw_only=True)
class DailyOptions:
    runtime_dir: Path
    registration: Path = DEFAULT_REGISTRATION
    repo: Path = REPOSITORY
    as_of: datetime | None = None
    targets: tuple[str, ...] | None = None
    full_update: bool = False
    max_staleness_days: int = DEFAULT_MAX_STALENESS_DAYS
    json_output: bool = False


def _stored_calendar(
    store: PanelStore, exchange: str, years: Sequence[int], as_of: datetime
) -> TradingCalendar | None:
    """The stored calendar of those of `years` the store holds, or `None` when it holds none or
    they cannot be read at `as_of` (a refusal step 2's calendar build is the remedy for)."""
    held = set(store.registered_years(TRADING_CALENDAR_DATASET))
    stored = tuple(year for year in years if year in held)
    if not stored:
        return None
    try:
        return load_trading_calendar(store, exchange=exchange, years=stored, as_of=as_of)
    except (TradingCalendarError, PanelStorageError):
        return None


def _stored_session(store: PanelStore, exchange: str, as_of: datetime) -> date | None:
    """The newest session published at `as_of`, by the stored calendar of `as_of`'s year and the
    year before it -- so 1 January, and every day of a new year before its first session closes,
    find the previous year's last session. `None` when neither year's calendar is stored."""
    year = as_of.astimezone(SHANGHAI).year
    calendar = _stored_calendar(store, exchange, (year - 1, year), as_of)
    if calendar is None:
        return None
    try:
        return newest_published_session(calendar, as_of=as_of)
    except TradingCalendarError:
        return None


def _panel_build(
    runtime_dir: Path,
    *,
    year: int | Sequence[int],
    as_of: datetime,
    exchange: str,
    targets: Sequence[str],
) -> Invocation:
    arguments = ["panel", "build", "--runtime-dir", str(runtime_dir)]
    for one in (year,) if isinstance(year, int) else year:
        arguments += ["--year", str(one)]
    arguments += ["--as-of", as_of.isoformat(), "--exchange", exchange, "--incremental", "--json"]
    if INDUSTRY_MEMBERSHIP_TARGET in targets:
        # The two whole-market states (~4 requests) checked against the stored corpus, and the
        # 62 slices only when that check fails or there is no corpus yet -- see `panel build
        # --industry-sweep`.
        arguments += ["--industry-sweep", "states"]
    for target in targets:
        arguments += ["--dataset", target]
    return invoke(arguments)


def industry_sweep(budget: Sequence[str]) -> str | None:
    """How the day's `index_member_all` was fetched, read off `panel build`'s `BUDGET` lines, or
    `None` when it was not.

    The whole-market states on an ordinary day. The l1_code slices when there was no stored corpus
    to check against, and "FELL BACK" when the states were fetched and failed the self-check --
    62 more requests, which a persistent fallback would spend every industry day without anything
    else saying so; the printed summary carries this line for that reason.
    """
    prefix = f"BUDGET {INDUSTRY_MEMBERSHIP_TARGET} "
    lines = [line for line in budget if line.startswith(prefix)]
    if not lines:
        return None
    slices = next((line for line in lines if "l1_code slices" in line), None)
    if slices is None:
        return "whole-market states"
    count = slices[len(prefix) :].split(" ", 1)[0]
    _head, _marker, reason = slices.partition("not --year; ")
    reason = reason.removesuffix(")")
    if len(lines) > 1:
        return f"FELL BACK to the l1_code slices ({count} more requests): {reason}"
    return f"the l1_code slices ({count} requests): {reason or 'the default sweep'}"


def _budget(built: Invocation) -> list[str]:
    """The `BUDGET` lines `panel build` states before each fetch loop: the code's own count."""
    return [
        line
        for line in (*built.stderr.splitlines(), *built.stdout.splitlines())
        if line.startswith("BUDGET ")
    ]


FINANCIAL_INDICATOR_TARGET: Final[str] = "fina_indicator"
ANNOUNCEMENT_MONTH_TARGETS: Final[frozenset[str]] = frozenset(
    {"income", "balancesheet", "cashflow"}
)
"""Swept by announcement month under `panel build --incremental` (`V2-P6-018`): the trailing
months and a weekly rotation, carrying the rest. In January the trailing months reach into last
year's partition, so the update builds that year too."""


def financial_indicator_years(session_year: int, as_of: datetime) -> tuple[int, ...]:
    """The report-period years a day's `fina_indicator` sweep covers.

    `fina_indicator` is swept by report-period year, not by announcement year: a period year is
    its four quarter ends, each window opening on its period's last day. The year before the
    session's is always among them -- its annual report is announced in the session's year (by
    30 April) and every revision of it is served in that period's window -- and the session's
    own year once its first period (31 March) has ended. Before that it has no open window, and
    `panel build` refuses a period year none of whose windows served a registered filing: asked
    for the session's year alone, every day from 1 January to 30 March would be refused.
    """
    opens = datetime(session_year, 3, 31, tzinfo=SHANGHAI)
    return (session_year - 1, session_year) if as_of >= opens else (session_year - 1,)


def next_year_calendar_needed(calendar: TradingCalendar, *, session: date, horizon: int) -> bool:
    """Whether the session's outcome window -- the next session and `horizon` more -- or its
    registration cutoff reaches past the last session the session's year's calendar holds."""
    later = [day for day in calendar.trading_days if session < day <= date(session.year, 12, 31)]
    return len(later) < horizon + 1


def update_panel(
    runtime_dir: Path,
    *,
    session: date,
    as_of: datetime,
    exchange: str,
    targets: Sequence[str],
    next_year: bool,
) -> dict[str, Any]:
    """Step 2: the incremental build of every target, and what it wrote; `fina_indicator` in an
    invocation of its own over `financial_indicator_years`; then next year's calendar when the
    session's outcome window reaches into it."""
    yearly = tuple(target for target in targets if target != FINANCIAL_INDICATOR_TARGET)
    built = _panel_build(
        runtime_dir, year=session.year, as_of=as_of, exchange=exchange, targets=yearly
    )
    if built.exit_code != 0:
        raise StepFailedError(
            "panel update", f"`panel build` exited {built.exit_code}: {built.reason()}"
        )
    payloads = [body for body in built.payloads() if isinstance(body, dict)]
    report = payloads[-1] if payloads else {}
    budget = _budget(built)
    if FINANCIAL_INDICATOR_TARGET in targets:
        periods = financial_indicator_years(session.year, as_of)
        swept = _panel_build(
            runtime_dir,
            year=periods,
            as_of=as_of,
            exchange=exchange,
            targets=(FINANCIAL_INDICATOR_TARGET,),
        )
        if swept.exit_code != 0:
            raise StepFailedError(
                "panel update",
                f"`panel build --dataset fina_indicator` for report-period years {list(periods)} "
                f"exited {swept.exit_code}: {swept.reason()}",
            )
        budget += _budget(swept)
    previous = [target for target in targets if target in ANNOUNCEMENT_MONTH_TARGETS]
    if previous and session.month == 1:
        # `V2-P6-018`: the incremental statement sweep re-sweeps the month before the stored
        # build's, which in January is last December -- a month of last year's partition. Keyed
        # on the **session**, not the clock: on New Year's Day the day is still 31 December,
        # whose year is the session's and whose previous year is not to be built.
        closing = _panel_build(
            runtime_dir,
            year=session.year - 1,
            as_of=as_of,
            exchange=exchange,
            targets=tuple(previous),
        )
        if closing.exit_code != 0:
            raise StepFailedError(
                "panel update",
                f"`panel build --year {session.year - 1}` for the statements' December re-sweep "
                f"exited {closing.exit_code}: {closing.reason()}",
            )
        budget += _budget(closing)
    if next_year:
        ahead = _panel_build(
            runtime_dir,
            year=session.year + 1,
            as_of=as_of,
            exchange=exchange,
            targets=(TRADING_CALENDAR_DATASET,),
        )
        if ahead.exit_code != 0:
            raise StepFailedError(
                "panel update",
                f"the session's outcome window reaches into {session.year + 1} and that year's "
                f"calendar could not be built (exit {ahead.exit_code}): {ahead.reason()}",
            )
    return {
        "targets": list(targets),
        "year": session.year,
        "next_year_calendar": next_year,
        "budget": budget,
        "industry_sweep": industry_sweep(budget),
        "sessions": report.get("sessions"),
        "partitions": report.get("partitions", []),
    }


def doctor_datasets(targets: Sequence[str]) -> tuple[str, ...]:
    """The stored datasets the targets write, less those not partitioned by calendar year."""
    return tuple(
        sorted(
            {
                dataset
                for target in targets
                if target not in UNCHECKED_BY_YEAR
                for dataset in cli.PANEL_BUILD_TARGETS[target]
            }
        )
    )


def check_panel(
    runtime_dir: Path,
    *,
    datasets: Sequence[str],
    session: date,
    as_of: datetime,
    exchange: str,
    first_of_year: bool = False,
) -> None:
    """Step 3: `panel doctor` and the dependency gate, both clean, or the run stops.

    On the first session of a year the year before is asked about too: the day-level checks
    compare the session with the one before it (`return_paths` reads the previous close and the
    adjustment factors across it), and a calendar of the session's year alone cannot place that
    session -- measured: `check_unavailable`, which blocks.

    `fina_indicator` is asked about in an invocation of its own, over the years among those that
    are stored: its partitions are announcement years written only when something was announced,
    and a year's first sessions can precede its first announcement (`income`'s first 2023 row
    was announced on 4 January, the day after 2023's first session). A year the sweep wrote is
    stored, so the filter only drops a year nothing has been announced in yet.
    """
    years = [session.year - 1, session.year] if first_of_year else [session.year]
    announced = [dataset for dataset in datasets if dataset in ANNOUNCEMENT_YEAR_DATASETS]
    _doctor(
        runtime_dir,
        datasets=[dataset for dataset in datasets if dataset not in ANNOUNCEMENT_YEAR_DATASETS],
        years=years,
        session=session,
        as_of=as_of,
        exchange=exchange,
    )
    if announced:
        store = PanelStore(runtime_dir / "panel")
        stored = [
            year
            for year in years
            if all(year in store.registered_years(dataset) for dataset in announced)
        ]
        if stored:
            _doctor(
                runtime_dir,
                datasets=announced,
                years=stored,
                session=session,
                as_of=as_of,
                exchange=exchange,
            )


ANNOUNCEMENT_YEAR_DATASETS: Final[frozenset[str]] = frozenset({FINANCIAL_INDICATOR_TARGET})
"""Swept by report period, filed by announcement year: see `check_panel`."""


def _doctor(
    runtime_dir: Path,
    *,
    datasets: Sequence[str],
    years: Sequence[int],
    session: date,
    as_of: datetime,
    exchange: str,
) -> None:
    """One `panel doctor` and one `data-check` over `datasets` x `years`, both clean."""
    if not datasets:
        return
    arguments = ["--runtime-dir", str(runtime_dir)]
    for year in years:
        arguments += ["--year", str(year)]
    arguments += ["--session", session.isoformat(), "--as-of", as_of.isoformat()]
    arguments += ["--exchange", exchange]
    for dataset in datasets:
        arguments += ["--dataset", dataset]
    if {INDEX_WEIGHT_DATASET, INDEX_DAILY_DATASET} & set(datasets):
        # Without them the index datasets are `check_unavailable` -- "no index code was named,
        # so the index itself could not be required" -- which blocks: measured on a copy of
        # `runtime/panel` for 2026-08-28, and clean with the three the panel builds.
        for code in INDEX_WEIGHT_INDEX_CODES:
            arguments += ["--index-code", code]
    doctor = invoke(["panel", "doctor", *arguments, "--no-limitation-detail"])
    if doctor.exit_code != 0:
        raise StepFailedError(
            "panel doctor",
            f"`panel doctor` is not clean (exit {doctor.exit_code}): {doctor.reason()}",
        )
    gate = invoke(["data-check", *arguments])
    if gate.exit_code != 0:
        raise StepFailedError(
            "panel doctor",
            f"the dependency gate `data-check` refused (exit {gate.exit_code}): {gate.reason()}",
        )


def _built_at_instant(
    store: PanelStore, build: TierBuild, *, instant: datetime, as_of: datetime
) -> bool:
    """Whether the store already holds `build` at `instant`, through each tier's own manifest."""
    definition = resolve_factor(build.factor)
    years = (instant.astimezone(SHANGHAI).year,)
    if years[0] not in store.registered_years(factor_manifest_dataset(definition)):
        return False
    try:
        raw = {
            manifest.manifest_id
            for manifest in load_factor_manifests(store, definition, years=years, as_of=as_of)
            if manifest.as_of == instant
        }
        if not raw or build.tier == "raw":
            return bool(raw)
        assert build.transform is not None  # a processed or neutralized tier names one
        transform = FACTOR_TRANSFORMS.get(build.transform)
        processed = {
            manifest.transform_manifest_id
            for manifest in load_factor_transform_manifests(
                store, definition, years=years, as_of=as_of
            )
            if manifest.source_manifest_id in raw
            and manifest.transform_id == transform.transform_id
        }
        if not processed or build.tier == "processed":
            return bool(processed)
        assert build.neutralization is not None
        neutralization = FACTOR_NEUTRALIZATIONS.get(build.neutralization)
        return any(
            manifest.source_transform_manifest_id in processed
            and manifest.neutralization_id == neutralization.neutralization_id
            for manifest in load_factor_neutralization_manifests(
                store, definition, years=years, as_of=as_of
            )
        )
    except FactorEngineError:
        return False


def _factor_years(store: PanelStore, session: date) -> tuple[int, ...]:
    """The session's year and, when the calendar holds it, the year before: a lookback at the
    start of a year reaches back across it."""
    stored = set(store.registered_years(TRADING_CALENDAR_DATASET))
    return tuple(year for year in (session.year - 1, session.year) if year in stored) or (
        session.year,
    )


def build_factors(
    store: PanelStore,
    builds: Sequence[TierBuild],
    *,
    session: date,
    as_of: datetime,
    exchange: str,
    max_staleness_days: int,
) -> dict[str, Any]:
    """Step 4: every tier the configuration reads, at the session's signal instant."""
    instant = session_publication_instant(session)
    missing = [
        build
        for build in builds
        if not _built_at_instant(store, build, instant=instant, as_of=as_of)
    ]
    groups: dict[tuple[str, str | None, str | None], list[str]] = {}
    for build in missing:
        groups.setdefault((build.tier, build.transform, build.neutralization), []).append(
            build.factor
        )
    code_commit = resolve_code_commit()
    written: list[str] = []
    for (tier, transform, neutralization), factors in sorted(
        groups.items(), key=lambda item: (item[0][0], item[0][1] or "", item[0][2] or "")
    ):
        try:
            requests = factor_build_requests(
                factors=factors,
                tier=tier,
                transform=transform or "",
                neutralization=neutralization or "",
                as_ofs=[instant],
                years=_factor_years(store, session),
                exchange=exchange,
                max_staleness_days=max_staleness_days,
                waive_max_staleness=False,
                subjects=[],
                supersedes_raw=[],
                supersedes_processed=[],
                supersedes_neutralized=[],
                code_commit=code_commit,
            )
            reports = build_factor_panel_set(store, requests, built_at=as_of)
        except FactorViewError as error:
            raise StepFailedError("factor build", error.disclosable) from error
        written.extend(partition for report in reports for partition in report.partitions)
    return {
        "instant": instant.isoformat(),
        "built": [f"{b.factor}@{b.tier}" for b in missing],
        "already_built": [f"{b.factor}@{b.tier}" for b in builds if b not in missing],
        "partitions": sorted(set(written)),
    }


def target_weights(
    signal: SignalDay,
    request: StrategyRequest,
    previous: Mapping[str, str],
    *,
    schedule: Schedule,
) -> dict[str, Any]:
    """Step 6: today's book under the book's rule, and the move from yesterday's.

    On a day a rebalance is due (`Schedule.due`) the held names that still rank within the band
    stay and the free slots are filled in rank order (`target_holdings`, the backtest's own rule).
    Otherwise, and on a day the source held, yesterday's book is today's. Each name is
    `1 / holding_count` of the book, the book's equal position capital; what no name fills is
    cash.
    """
    spec = request.spec
    ranked = signal.scores.ranked
    if ranked is None or not schedule.due:
        names = tuple(sorted(previous))
        decided = "held" if ranked is None else "not a rebalance day"
    else:
        keep, buy = target_holdings(
            ranked, held=frozenset(previous), spec=spec, industries=signal.industries
        )
        names = tuple(sorted(keep)) + buy
        decided = "rebalanced"
    weight = (Decimal(1) / spec.holding_count).quantize(_WEIGHT_QUANTUM)
    weights = {name: f"{weight:f}" for name in sorted(names)}
    moved = sum(
        (
            abs(Decimal(weights.get(name, "0")) - Decimal(previous.get(name, "0")))
            for name in set(weights) | set(previous)
        ),
        Decimal(0),
    )
    return {
        "decision": decided,
        "reason": schedule.reason,
        "holding_count": spec.holding_count,
        "rebalance_every_sessions": spec.rebalance_every_sessions,
        "sessions_since_start": schedule.position,
        "scheduled_rebalance": schedule.scheduled.isoformat(),
        "weights": weights,
        "cash": f"{(Decimal(1) - weight * len(names)).quantize(_WEIGHT_QUANTUM):f}",
        "turnover": f"{(moved / 2).quantize(_WEIGHT_QUANTUM):f}",
    }


def candidates(signal: SignalDay, request: StrategyRequest, config_id: str) -> dict[str, Any]:
    """Step 5: the ranking cut at the buffer band (or the book), and its content address."""
    spec = request.spec
    cut = max(spec.holding_count, spec.buffer_rank or 0)
    ranked = signal.scores.ranked
    rows = (
        []
        if ranked is None
        else [
            {"rank": rank, "ts_code": name, "score": signal.scores.scores[name]}
            for rank, name in enumerate(ranked[:cut], start=1)
        ]
    )
    document = {"session": signal.day.isoformat(), "config_id": config_id, "candidates": rows}
    digest = hashlib.sha256(
        json.dumps(document, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return {
        "candidate_list_id": f"dsl_{digest[:24]}",
        "held": ranked is None,
        "ranked_count": 0 if ranked is None else len(ranked),
        "incomplete_count": len(signal.scores.incomplete),
        "candidates": rows,
        "weights": {key: value for key, value in sorted(signal.scores.weights.items())},
        "refit_day": None if signal.refit_day is None else signal.refit_day.isoformat(),
        "industries_read": len(signal.industries),
    }


def _outcome_calendar(
    store: PanelStore, exchange: str, session: date, as_of: datetime
) -> TradingCalendar:
    """The calendar the record's outcome instant is derived on: the session's year, and the
    next when it is stored, so a window crossing New Year can be placed."""
    stored = set(store.registered_years(TRADING_CALENDAR_DATASET))
    years = tuple(year for year in (session.year, session.year + 1) if year in stored)
    return load_trading_calendar(store, exchange=exchange, years=years, as_of=as_of)


def register_prediction(
    runtime_dir: Path,
    batch: PredictionBatch,
    *,
    calendar: TradingCalendar,
    clock: Callable[[], datetime],
) -> tuple[PredictionRecord, str]:
    """Step 7: file `batch` unless the session already has its record.

    `model daily-run` files a new record on every invocation, because `predicted_at` reaches the
    address; a daily command re-run the same evening must not. `session_record` finds the
    session's record under the same declaration: holding the same numbers it is reused and
    nothing is written; holding different ones, the registered record stands and this run is
    refused.

    **Only before the next session's call auction.** The store calls a record `forward` when it
    held it before the outcome window's last close. This command's rule is stricter, because the
    book trades the day's scores at the next open, whose price the call auction starting at 09:15
    fixes: a registration at or after `registration_cutoff` -- the command run late, or catching
    up a missed day -- is refused before anything is filed.
    """
    store = build_storage(runtime_dir=runtime_dir, clock=clock).prediction_store
    try:
        held = session_record(store, batch)
    except StrategyRegistrationError as error:
        raise StepFailedError("prediction", str(error)) from error
    if held is not None:
        return held, "unchanged"
    day = batch.as_of.astimezone(SHANGHAI).date()
    cutoff = registration_cutoff(calendar, day)
    deadline = outcome_known_at_for(batch, calendar=calendar, zone=SHANGHAI)
    now = clock()
    if now >= cutoff:
        raise StepFailedError(
            "prediction",
            f"the scores of {day.isoformat()} are traded at the next session's open, whose call "
            f"auction starts {cutoff.isoformat()}, and it is {now.isoformat()}: part of their "
            f"outcome is published already (all of it by {deadline.isoformat()}), so registered "
            "now they would not be a prediction made before its outcome. Nothing was filed",
        )
    written = store.put(batch=batch, calendar=calendar, zone=SHANGHAI)
    return written.record, written.outcome


# --- the whole run -----------------------------------------------------------------------------


def _utc_now() -> datetime:
    return datetime.now(UTC)


def run_daily_selection(
    options: DailyOptions, *, clock: Callable[[], datetime] = _utc_now
) -> dict[str, Any]:
    """Run the eight steps and return the day's journal; raise `StepFailedError` on a refusal,
    carrying the requests made before it."""
    counts: Counter[str] = Counter()
    try:
        return _run_daily_selection(options, clock=clock, counts=counts)
    except StepFailedError as error:
        error.requests = dict(sorted(counts.items()))
        raise


def _run_daily_selection(
    options: DailyOptions, *, clock: Callable[[], datetime], counts: Counter[str]
) -> dict[str, Any]:
    runtime_dir = options.runtime_dir
    with _step("registration"):
        registration = admit_registration(options.registration, options.repo)
        store = panel_store(runtime_dir)
        directory = journal_directory(runtime_dir, registration)
        first_as_of = options.as_of or clock()
        anchor = _registered_anchor(registration)
        # The configuration resolved once, about its own first day, before anything is fetched:
        # a configuration that cannot be put is a step-1 refusal, and which tiers and industries
        # it reads decide steps 2 and 4.
        probe = day_request(
            registration.config, day=anchor, as_of=session_publication_instant(anchor)
        )
        exchange = probe.exchange
        builds = tiers_read(probe)
        base = (
            FULL_UPDATE_TARGETS
            if options.full_update
            else options.targets
            if options.targets is not None
            else targets_for(builds)
        )

    with _step("panel update"), counted_transport(counts):
        session = _stored_session(store, exchange, first_as_of)
        if session is None:
            year = first_as_of.astimezone(SHANGHAI).year
            built = _panel_build(
                runtime_dir,
                year=year,
                as_of=first_as_of,
                exchange=exchange,
                targets=(TRADING_CALENDAR_DATASET,),
            )
            if built.exit_code != 0:
                raise StepFailedError(
                    "panel update", f"the {year} calendar could not be built: {built.reason()}"
                )
            session = _stored_session(store, exchange, first_as_of)
            if session is None:
                raise StepFailedError(
                    "panel update", f"no session had published at {first_as_of.isoformat()}"
                )
        if session < anchor:
            raise StepFailedError(
                "registration",
                f"the registered configuration starts on {anchor.isoformat()}, after the newest "
                f"closed session {session.isoformat()}",
                exit_code=DailyExit.no_registration,
            )
        path = directory / f"{session.isoformat()}.json"
        journal = read_journal(path)
        if journal is not None and journal.get("registration", {}).get("sha256") not in (
            None,
            registration.sha256,
        ):
            raise StepFailedError(
                "summary", f"{path} was written under another registration of this configuration"
            )
        if journal is not None and "result" in journal:
            result = dict(journal["result"])
            result["this_run"] = {"requests": {}, "request_count": 0, "wrote": False}
            return result
        if journal is None:
            # The day's clock is pinned before anything is fetched, so a retry of a step 2 that
            # stopped partway asks the same question: the partitions it already wrote are then
            # rewritten byte for byte, which `PanelStore` does not rewrite at all.
            journal = {
                "schema": DAILY_SELECTION_SCHEMA,
                "session": session.isoformat(),
                "as_of": first_as_of.isoformat(),
                "registration": {
                    "path": registration.path.name,
                    "sha256": registration.sha256,
                    "commit": registration.commit,
                    "config_id": registration.config_id,
                },
            }
            write_journal(path, journal)
        as_of = datetime.fromisoformat(journal["as_of"])
        calendar = _stored_calendar(
            store, exchange, tuple(range(anchor.year, session.year + 1)), as_of
        )
        if calendar is None:
            raise StepFailedError(
                "panel update",
                f"the {exchange} calendar from {anchor.year} to {session.year} is not stored; "
                "the rebalance schedule is counted on it",
            )
        prior_day, prior = previous_targets(directory, session)
        schedule = schedule_of(
            calendar,
            anchor=anchor,
            session=session,
            every=probe.spec.rebalance_every_sessions,
            previous=prior_day,
        )
        reads_industries = industry_day(probe, builds, schedule)
        if "panel" not in journal:
            targets = tuple(base) + tuple(
                target for target in INDUSTRY_TARGETS if reads_industries and target not in base
            )
            panel = update_panel(
                runtime_dir,
                session=session,
                as_of=as_of,
                exchange=exchange,
                targets=targets,
                next_year=next_year_calendar_needed(
                    calendar, session=session, horizon=outcome_horizon(probe)
                ),
            )
            journal = {**journal, "panel": {**panel, "requests": dict(sorted(counts.items()))}}
            write_journal(path, journal)

    with _step("panel doctor"):
        check_panel(
            runtime_dir,
            datasets=doctor_datasets(journal["panel"]["targets"]),
            session=session,
            as_of=as_of,
            exchange=exchange,
            first_of_year=session
            == min(day for day in calendar.trading_days if day.year == session.year),
        )
    with _step("factor build"):
        request = day_request(registration.config, day=session, as_of=as_of)
        factors = build_factors(
            store,
            builds,
            session=session,
            as_of=as_of,
            exchange=request.exchange,
            max_staleness_days=options.max_staleness_days,
        )
    with _step("candidates"):
        capped = request.spec.max_industry_weight is not None
        try:
            signal = score_day(
                store,
                request,
                day=session,
                anchor=anchor,
                read_industries=capped and schedule.due,
            )
        except StrategyViewError as error:
            raise StepFailedError("candidates", error.disclosable) from error
        listed = candidates(signal, request, registration.config_id)
    with _step("target weights"):
        targets_today = {
            **target_weights(signal, request, prior, schedule=schedule),
            "previous_session": None if prior_day is None else prior_day.isoformat(),
        }
    with _step("prediction"):
        try:
            batch = signal_day_batch(signal, request, registration.declared, predicted_at=clock())
        except StrategyRegistrationError as error:
            raise StepFailedError("prediction", str(error)) from error
        prediction: dict[str, Any] = {"registered": False, "reason": "the source held today"}
        if batch is not None:
            provenance = input_provenance(
                store,
                request,
                registration.declared,
                day=session,
                batch=batch,
                recorded_at=clock(),
            )
            write_provenance(runtime_dir, provenance)
            record, outcome = register_prediction(
                runtime_dir,
                batch,
                calendar=_outcome_calendar(store, request.exchange, session, as_of),
                clock=clock,
            )
            prediction = {
                "registered": True,
                "record_id": record.record_id,
                "outcome": outcome,
                "standing": record.standing,
                "as_of": record.batch.as_of.isoformat(),
                "outcome_known_at": record.outcome_known_at.isoformat(),
                "recorded_at": record.recorded_at.isoformat(),
                "scored": len(record.batch.scored),
                "abstained": len(record.batch.abstained),
                "provenance": provenance.digest,
            }
    with _step("summary"):
        result = {
            "session": session.isoformat(),
            "as_of": as_of.isoformat(),
            "registration": journal["registration"],
            "panel": journal["panel"],
            "factors": factors,
            "candidates": listed,
            "targets": targets_today,
            "prediction": prediction,
            "wording": "candidates of a registered research configuration; not an order, not a "
            "forecast of return",
        }
        write_journal(path, {**journal, "result": result})
    return {
        **result,
        "this_run": {
            "requests": dict(sorted(counts.items())),
            "request_count": sum(counts.values()),
            "wrote": True,
        },
    }


# --- the face ----------------------------------------------------------------------------------


def summary_lines(result: Mapping[str, Any], *, top: int) -> list[str]:
    """The summary the plan lists: session, list id, top N, weights, record id, requests."""
    listed = result["candidates"]
    targets = result["targets"]
    prediction = result["prediction"]
    run = result["this_run"]
    lines = [
        f"session            {result['session']} (as of {result['as_of']})",
        f"candidate list     {listed['candidate_list_id']}"
        + (" (the source held: no ranking)" if listed["held"] else ""),
    ]
    for row in listed["candidates"][:top]:
        lines.append(f"  {row['rank']:>4}  {row['ts_code']:<10} {row['score']:+.6f}")
    lines.append(
        f"target weights     {targets['decision']} ({targets['reason']}), "
        f"{len(targets['weights'])} name(s), cash {targets['cash']}, "
        f"turnover {targets['turnover']} (previous: {targets['previous_session'] or 'none'})"
    )
    for name, weight in targets["weights"].items():
        lines.append(f"  {name:<10} {weight}")
    if prediction["registered"]:
        lines.append(
            f"prediction         {prediction['record_id']} {prediction['standing']} "
            f"({prediction['outcome']}; outcome knowable {prediction['outcome_known_at']})"
        )
    else:
        lines.append(f"prediction         none: {prediction['reason']}")
    lines.append(
        f"tushare requests   {run['request_count']} this run "
        f"{json.dumps(run['requests'], sort_keys=True)}"
        + ("" if run["wrote"] else " -- the day was already complete; nothing was run")
    )
    sweep = result.get("panel", {}).get("industry_sweep")
    if sweep:
        lines.append(f"industry sweep     {sweep}")
    lines.append(f"wording            {result['wording']}")
    return lines


# --- the scheduled run: a checkout pinned at the registration, and its launchd job ------------


def _git_output(repo: Path, *args: str, what: str) -> str:
    """Run git in `repo` through the registry's runner (no inherited `GIT_*`) and return stdout,
    or refuse naming what was being done."""
    run = registry._git(repo, *args)
    if run.returncode != 0:
        raise StepFailedError(
            "registration",
            f"could not {what}: {run.stderr.decode(errors='replace').strip()}",
        )
    return run.stdout.decode().strip()


def pin_worktree(registration: Path, repo: Path, directory: Path) -> str:
    """Create, or move, a detached worktree at the registration's commit; return that commit.

    The scheduled run stands there rather than in the development checkout. The commit is the one
    that last touched the registration on `repo`'s `HEAD`, refused unless the bound code there is
    exactly the registered `code_commit` (`registry.registered_checkout`) -- so the worktree
    passes the daily command's own admission however far development has moved on, and a commit
    in the development checkout can no longer cost a forward day. An existing `directory` must
    be a clean worktree of the same repository; it is moved to the commit, never overwritten.
    """
    try:
        root, commit, _code = registry.registered_checkout(
            registration, repo, also_bound=(THIS_SCRIPT,)
        )
    except registry.HoldoutRefusedError as error:
        raise StepFailedError(
            "registration",
            f"{type(error).__name__}: {error}",
            exit_code=DailyExit.code_not_registered,
        ) from error
    common = Path(_git_output(root, "rev-parse", "--git-common-dir", what="read the repository"))
    common = (root / common).resolve() if not common.is_absolute() else common.resolve()
    if not directory.exists():
        directory.parent.mkdir(parents=True, exist_ok=True)
        _git_output(
            root, "worktree", "add", "--detach", str(directory), commit, what="add the worktree"
        )
    else:
        theirs = Path(
            _git_output(directory, "rev-parse", "--git-common-dir", what="read the worktree")
        )
        theirs = (directory / theirs).resolve() if not theirs.is_absolute() else theirs.resolve()
        if theirs != common:
            raise StepFailedError(
                "registration", f"{directory} is not a worktree of {root}; nothing was changed"
            )
        dirty = _git_output(
            directory, "status", "--porcelain", "--untracked-files=all", what="read its status"
        )
        if dirty:
            raise StepFailedError(
                "registration",
                f"{directory} has uncommitted changes; a pinned checkout is never edited, and "
                "nothing was changed",
            )
        _git_output(directory, "checkout", "-q", "--detach", commit, what="move the worktree")
    return _git_output(directory, "rev-parse", "HEAD", what="read the worktree's commit")


PINNED_ENVIRONMENT: Final[str] = ".venv"
"""The pinned checkout's own environment, `<worktree>/.venv` (git-ignored)."""


def worktree_python(worktree: Path) -> str:
    """The `major.minor` the pinned checkout's `.python-version` names, or refuse."""
    source = worktree / PYTHON_VERSION_FILE
    if not source.is_file():
        raise StepFailedError(
            "registration",
            f"{source} does not exist, so the interpreter the pinned environment must be built on "
            "is not known; nothing was synced",
        )
    return pinned_python(source.read_text(encoding="utf-8"), source=str(source))


def environment_sync_command(
    worktree: Path, *, uv: Path, python: str
) -> tuple[list[str], dict[str, str]]:
    """The command that creates the pinned checkout's environment, and its environment variables.

    `uv sync --frozen --offline` against `<worktree>/uv.lock`, into `<worktree>/.venv`:
    `--frozen` installs the lock as it is and never re-resolves it, `--offline` and
    `--no-python-downloads` never touch the network -- a package uv's cache does not hold, or an
    interpreter not already on this machine, is a refusal, not a download. `--python` is the
    checkout's `.python-version` (`worktree_python`), and `--all-extras` (the dev group is uv's
    default) makes it the environment the research ran in, `uv sync --all-extras --dev`. The
    inherited `VIRTUAL_ENV`, `PYTHONPATH` and `UV_PYTHON` are dropped, so the sync cannot target,
    import from, or pick the interpreter of, the development checkout's environment.
    """
    environment = {
        key: value
        for key, value in os.environ.items()
        if key not in {"VIRTUAL_ENV", "PYTHONPATH", "UV_PROJECT_ENVIRONMENT", "UV_PYTHON"}
    }
    environment["UV_PROJECT_ENVIRONMENT"] = str(worktree / PINNED_ENVIRONMENT)
    command = [
        str(uv),
        "sync",
        "--frozen",
        "--offline",
        "--no-python-downloads",
        "--all-extras",
        "--python",
        python,
        "--project",
        str(worktree),
    ]
    return command, environment


def _run_sync(
    command: Sequence[str], *, environment: Mapping[str, str], cwd: Path
) -> subprocess.CompletedProcess[bytes]:
    """Run the sync. A seam, so the tests assert the command without ever running uv."""
    return subprocess.run(list(command), env=dict(environment), cwd=cwd, capture_output=True)


_UNCACHED = re.compile(r"`([A-Za-z0-9_.\-]+)==([^`\s]+)`")


def environment_python(environment: Path) -> str | None:
    """The full Python version a virtual environment was built on, from its `pyvenv.cfg`."""
    configuration = environment / "pyvenv.cfg"
    if not configuration.is_file():
        return None
    for line in configuration.read_text(encoding="utf-8").splitlines():
        key, _, value = line.partition("=")
        if key.strip() in {"version_info", "version"}:
            return value.strip()
    return None


def sync_pinned_environment(worktree: Path, *, uv: Path) -> Path:
    """Create or refresh `<worktree>/.venv` from `<worktree>/uv.lock`, offline, on the
    interpreter `<worktree>/.python-version` names; return its path.

    Refused by name when uv's cache lacks a package: the operator is told which, and the one
    online command that fills the cache -- which this command never runs. After the sync the
    environment's `pyvenv.cfg` is read back, and one built on another `major.minor` is refused.
    """
    python = worktree_python(worktree)
    command, environment = environment_sync_command(worktree, uv=uv, python=python)
    finished = _run_sync(command, environment=environment, cwd=worktree)
    if finished.returncode != 0:
        output = finished.stderr.decode(errors="replace")
        missing = sorted({f"{name}=={version}" for name, version in _UNCACHED.findall(output)})
        online = (
            f"UV_PROJECT_ENVIRONMENT={shlex.quote(str(worktree / PINNED_ENVIRONMENT))} "
            f"{shlex.quote(str(uv))} sync --frozen --all-extras --python {shlex.quote(python)} "
            f"--project {shlex.quote(str(worktree))}"
        )
        cause = (
            f"uv's cache holds no copy of {', '.join(missing)}"
            if missing
            else "uv refused: " + " | ".join(output.strip().splitlines()[-4:])
        )
        raise StepFailedError(
            "registration",
            f"the pinned environment {worktree / PINNED_ENVIRONMENT} could not be created offline "
            f"from {worktree / LOCKFILE}: {cause}. Nothing was downloaded. To fill the cache, run "
            f"once, online: {online} -- then --pin-worktree again",
        )
    built = environment_python(worktree / PINNED_ENVIRONMENT)
    if built is None or ".".join(built.split(".")[:2]) != python:
        raise StepFailedError(
            "registration",
            f"the pinned environment {worktree / PINNED_ENVIRONMENT} runs Python "
            f"{built or 'of no recorded version'}, and {worktree / PYTHON_VERSION_FILE} pins "
            f"{python}; the scheduled run would compute on an interpreter the research did not",
        )
    return worktree / PINNED_ENVIRONMENT


LAUNCHD_LABEL: Final[str] = "com.openalpha.daily-selection"


def launchd_plist(
    *,
    worktree: Path,
    runtime_dir: Path,
    log_dir: Path,
    env_file: Path,
    uv: Path,
) -> str:
    """The launchd job that runs this command from the pinned worktree at 18:30 on weekdays.

    Printed, never installed. No shell runs it: `ProgramArguments` is the argument vector itself
    and `WorkingDirectory`/`EnvironmentVariables` set the rest, so no path is ever parsed by a
    shell and a `"`, `$` or space in one cannot break it or expand.

    **The interpreter is the pinned checkout's own**, `<worktree>/.venv/bin/python`, which
    `--pin-worktree` synced offline from the worktree's `uv.lock` (`sync_pinned_environment`).
    Its editable install of the project points at `<worktree>/src`, so no `PYTHONPATH` is set and
    no shared environment is named; `uv run --no-sync` is there only to load `--env-file`, and
    `UV_PROJECT_ENVIRONMENT` keeps it on the same environment. Step 1 then compares that
    interpreter's packages with the lock (`admit_environment`). launchd has no exchange calendar,
    so it fires every weekday and the command decides: on a holiday the newest closed session is
    one whose journal is already complete, which prints the summary again with no request and no
    write.
    """
    job = {
        "Label": LAUNCHD_LABEL,
        "ProgramArguments": [
            str(uv),
            "run",
            "--no-sync",
            "--env-file",
            str(env_file),
            str(worktree / PINNED_ENVIRONMENT / "bin" / "python"),
            str(worktree / THIS_SCRIPT),
            "--runtime-dir",
            str(runtime_dir),
            "--repo",
            str(worktree),
            "--registration",
            str(worktree / REGISTRATION_IN_REPOSITORY),
        ],
        "WorkingDirectory": str(worktree),
        "EnvironmentVariables": {
            "UV_PROJECT_ENVIRONMENT": str(worktree / PINNED_ENVIRONMENT),
            "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
        },
        "StartCalendarInterval": [
            {"Weekday": weekday, "Hour": 18, "Minute": 30} for weekday in range(1, 6)
        ],
        "StandardOutPath": str(log_dir / "daily-selection.out.log"),
        "StandardErrorPath": str(log_dir / "daily-selection.err.log"),
        "RunAtLoad": False,
    }
    return plistlib.dumps(job, sort_keys=False).decode("utf-8")


def _instant(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise argparse.ArgumentTypeError(f"{value!r} carries no UTC offset")
    return parsed


def main(argv: Sequence[str] | None = None, *, clock: Callable[[], datetime] = _utc_now) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("--runtime-dir", type=Path, default=None)
    parser.add_argument("--registration", type=Path, default=DEFAULT_REGISTRATION)
    parser.add_argument("--repo", type=Path, default=REPOSITORY)
    parser.add_argument(
        "--as-of",
        type=_instant,
        default=None,
        help="Pin the day's clock (default: now); the newest session published then is the day.",
    )
    parser.add_argument(
        "--dataset",
        action="append",
        default=None,
        help="A panel target to update, repeatable, in place of the targets the configuration "
        "reads (industries are still added on a day that reads them).",
    )
    parser.add_argument(
        "--full-update",
        action="store_true",
        help="Update every year-scoped target (FULL_UPDATE_TARGETS), whatever the configuration "
        "reads.",
    )
    parser.add_argument("--max-staleness-days", type=int, default=DEFAULT_MAX_STALENESS_DAYS)
    parser.add_argument("--top", type=int, default=None, help="How many candidates to print.")
    parser.add_argument("--json", action="store_true", help="Print the day's result as JSON.")
    parser.add_argument(
        "--pin-worktree",
        type=Path,
        default=None,
        metavar="DIR",
        help="Create or move a detached worktree at the registration's commit, sync its own "
        "environment (<DIR>/.venv) offline from its uv.lock, print the commit and exit. The "
        "scheduled run stands there.",
    )
    parser.add_argument(
        "--launchd-plist",
        type=Path,
        default=None,
        metavar="LOG_DIR",
        help="Print the launchd job (run from --worktree), logging to LOG_DIR, and exit. "
        "Installs nothing.",
    )
    parser.add_argument("--worktree", type=Path, default=None, help="The pinned worktree.")
    parser.add_argument("--env-file", type=Path, default=None, help="Default: <repo>/.env.")
    parser.add_argument("--uv", type=Path, default=None, help="Default: the uv on PATH.")
    arguments = parser.parse_args(argv)
    uv = (arguments.uv or Path(shutil.which("uv") or "uv")).resolve()
    try:
        if arguments.pin_worktree is not None:
            directory = arguments.pin_worktree.resolve()
            commit = pin_worktree(
                arguments.registration.resolve(), arguments.repo.resolve(), directory
            )
            environment = sync_pinned_environment(directory, uv=uv)
            print(f"pinned {directory} at {commit}; environment {environment} synced offline")
            return int(DailyExit.done)
    except StepFailedError as error:
        print(str(error), file=sys.stderr)
        return int(error.exit_code)
    if arguments.runtime_dir is None:
        parser.error("--runtime-dir is required")
    if arguments.launchd_plist is not None:
        if arguments.worktree is None:
            parser.error("--launchd-plist needs --worktree, the checkout pinned by --pin-worktree")
        repo = arguments.repo.resolve()
        print(
            launchd_plist(
                worktree=arguments.worktree.resolve(),
                runtime_dir=arguments.runtime_dir.resolve(),
                log_dir=arguments.launchd_plist.resolve(),
                env_file=(arguments.env_file or repo / ".env").resolve(),
                uv=uv,
            ),
            end="",
        )
        return int(DailyExit.done)
    options = DailyOptions(
        runtime_dir=arguments.runtime_dir,
        registration=arguments.registration,
        repo=arguments.repo,
        as_of=arguments.as_of,
        targets=None if arguments.dataset is None else tuple(arguments.dataset),
        full_update=arguments.full_update,
        max_staleness_days=arguments.max_staleness_days,
        json_output=arguments.json,
    )
    try:
        result = run_daily_selection(options, clock=clock)
    except StepFailedError as error:
        print(str(error), file=sys.stderr)
        print(
            f"tushare requests   {sum(error.requests.values())} this run "
            f"{json.dumps(error.requests, sort_keys=True)}",
            file=sys.stderr,
        )
        return int(error.exit_code)
    if arguments.json:
        print(json.dumps(result, sort_keys=True, ensure_ascii=False))
    else:
        top = arguments.top if arguments.top is not None else result["targets"]["holding_count"]
        print("\n".join(summary_lines(result, top=int(top))))
    return int(DailyExit.done)


if __name__ == "__main__":
    sys.exit(main())
