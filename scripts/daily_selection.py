"""The one daily selection command (`V2-P6-011`): after the close, today's candidates, target
weights and a registered prediction, under the registered configuration.

    uv run --no-sync --env-file .env python scripts/daily_selection.py --runtime-dir RT

Eight steps, in this order. The first that fails stops the run, is named, and sets a non-zero
exit; nothing after it runs.

1. **registration** -- read `docs/research/p6-registration.json` (`--registration`), the file the
   holdout ran on. It must be committed and byte-equal to `HEAD`'s, and the code running now must
   be its `code_commit`: `registry.admit_registered_code`, the holdout guard's own checks, over
   `REGISTERED_PATHS` plus this file. Forward results come from the registered code or not at all.
   A missing file exits `2`; a refused binding exits `3`.
2. **panel update** -- `openalpha panel build --incremental` for the year of the newest closed
   session, every target in `--dataset` (`DAILY_TARGETS` by default, plus the industry targets
   when the configuration reads industries), pinned to one `--as-of` for the day.
3. **panel doctor** -- `openalpha panel doctor` and the dependency gate `openalpha data-check`
   over those datasets, the year and the session. Not clean stops the run before any factor is
   built.
4. **factor build** -- every factor tier the configuration reads, at the session's signal instant
   (16:30 Shanghai), through `factor_view.build_factor_panel_set`. A tier already built at that
   instant is not built again.
5. **candidates** -- the session scored by `strategy_view.score_day`: the backtest's own feeds and
   scorer, so the ranking is the one a backtest of the registered configuration would trade on.
6. **target weights** -- the book's rebalance rule (`strategy_backtest.target_holdings`) over that
   ranking, the previous session's targets as the book held, on the configuration's rebalance
   schedule; equal weights of `1 / holding_count`, the rest cash.
7. **prediction** -- the day's scores registered in the prediction store before the next
   session opens, which is before any of their outcome has printed: the fitted model's own batch
   for a walk-forward source, and for a static or trailing-IC source a batch carrying the
   composite each security was ranked by.
8. **summary** -- printed, and written to the day's journal. A refused run prints the step, the
   reason and the Tushare requests it had spent.

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
import io
import json
import sys
from collections import Counter
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from enum import IntEnum
from pathlib import Path
from typing import Any, Final
from zoneinfo import ZoneInfo

REPOSITORY: Final[Path] = Path(__file__).resolve().parents[1]
RESEARCH_SCRIPTS: Final[Path] = REPOSITORY / "scripts" / "research"
if str(RESEARCH_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(RESEARCH_SCRIPTS))

import registry  # noqa: E402
from click import ClickException  # noqa: E402
from typer.main import get_command  # noqa: E402

from openalpha_cn import cli  # noqa: E402
from openalpha_cn.backtest.strategy_backtest import (  # noqa: E402
    target_holdings,
)
from openalpha_cn.domain.alpha_model import (  # noqa: E402
    ABSTAIN_INCOMPLETE_FEATURES,
    AlphaModelArtifact,
    AlphaModelDeclaration,
    Prediction,
    PredictionBatch,
)
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
)
from openalpha_cn.factor_view import (  # noqa: E402
    FactorViewError,
    build_factor_panel_set,
    factor_build_requests,
    resolve_factor,
)
from openalpha_cn.panel.catalog import DEFAULT_DATE_TIMEZONE  # noqa: E402
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
from openalpha_cn.providers.tushare import (  # noqa: E402
    TRADING_CALENDAR_DEFAULT_EXCHANGE,
    TushareTransport,
)
from openalpha_cn.runtime.provenance import resolve_code_commit  # noqa: E402
from openalpha_cn.storage.predictions import FilePredictionStore  # noqa: E402
from openalpha_cn.strategy_view import (  # noqa: E402
    SignalDay,
    StrategyRequest,
    StrategyViewError,
    score_day,
    strategy_request,
)

DAILY_SELECTION_SCHEMA: Final[str] = "openalpha-daily-selection/v1"
DEFAULT_REGISTRATION: Final[Path] = REPOSITORY / "docs" / "research" / "p6-registration.json"
JOURNAL_DIRECTORY: Final[str] = "daily_selection"
THIS_SCRIPT: Final[str] = "scripts/daily_selection.py"

DAILY_TARGETS: Final[tuple[str, ...]] = (
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
"""What one day's update fetches by default: every year-scoped target plus `fina_indicator`,
about 93 requests on an ordinary day (`V2-P6-003`'s measurement; the statement sweeps are 71 of
them). The two industry targets are added only when the configuration reads industries
(`INDUSTRY_TARGETS`): `index_member_all` alone is 62 requests."""

INDUSTRY_TARGETS: Final[tuple[str, ...]] = ("index_classify", "index_member_all")
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
NEXT_SESSION_OPEN: Final[time] = time(9, 30)
"""When the next session opens (Shanghai): the latest a day's scores may be registered."""
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


def admit_registration(path: Path, repo: Path) -> Registration:
    """Step 1: the committed registration, provided the running code is the registered code."""
    if not path.is_file():
        raise StepFailedError(
            "registration",
            f"{path} does not exist; the daily command runs the registered configuration and "
            "there is none to run",
            exit_code=DailyExit.no_registration,
        )
    try:
        _root, admitted = registry.admit_registered_code(path, repo, also_bound=(THIS_SCRIPT,))
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
    body = admitted.registered
    config = body.get("config")
    if not isinstance(config, dict):
        raise StepFailedError(
            "registration",
            f"{path} registers no configuration object",
            exit_code=DailyExit.no_registration,
        )
    settings = body.get("settings")
    seed = settings.get("random_seed", 0) if isinstance(settings, dict) else 0
    return Registration(
        path=path,
        sha256=hashlib.sha256(admitted.content).hexdigest(),
        commit=admitted.commit,
        code_commit=str(body.get("code_commit")),
        config=config,
        config_id=str(body.get("config_id")),
        seed=int(seed) if isinstance(seed, int) else 0,
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


def reads_industries(request: StrategyRequest, builds: Sequence[TierBuild]) -> bool:
    """Whether the configuration reads industry memberships: a cap, or a neutralized tier."""
    return request.spec.max_industry_weight is not None or any(
        build.tier == "neutralized" for build in builds
    )


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


# --- the steps ---------------------------------------------------------------------------------


@dataclass(slots=True, kw_only=True)
class DailyOptions:
    runtime_dir: Path
    registration: Path = DEFAULT_REGISTRATION
    repo: Path = REPOSITORY
    as_of: datetime | None = None
    targets: tuple[str, ...] | None = None
    max_staleness_days: int = DEFAULT_MAX_STALENESS_DAYS
    json_output: bool = False


def _stored_session(store: PanelStore, exchange: str, as_of: datetime) -> date | None:
    """The newest session published at `as_of`, by the stored calendar; `None` when the year's
    calendar is not stored yet."""
    year = as_of.astimezone(SHANGHAI).year
    try:
        calendar = load_trading_calendar(store, exchange=exchange, years=(year,), as_of=as_of)
        return newest_published_session(calendar, as_of=as_of)
    except Exception:  # any refusal means the year's calendar is not stored yet: step 2 builds it
        return None


def _panel_build(
    runtime_dir: Path, *, year: int, as_of: datetime, exchange: str, targets: Sequence[str]
) -> Invocation:
    arguments = ["panel", "build", "--runtime-dir", str(runtime_dir), "--year", str(year)]
    arguments += ["--as-of", as_of.isoformat(), "--exchange", exchange, "--incremental", "--json"]
    for target in targets:
        arguments += ["--dataset", target]
    return invoke(arguments)


def update_panel(
    runtime_dir: Path,
    *,
    session_year: int,
    as_of: datetime,
    exchange: str,
    targets: Sequence[str],
) -> dict[str, Any]:
    """Step 2: the incremental build of every target, and what it wrote."""
    built = _panel_build(
        runtime_dir, year=session_year, as_of=as_of, exchange=exchange, targets=targets
    )
    if built.exit_code != 0:
        raise StepFailedError(
            "panel update", f"`panel build` exited {built.exit_code}: {built.reason()}"
        )
    payloads = [body for body in built.payloads() if isinstance(body, dict)]
    report = payloads[-1] if payloads else {}
    return {
        "targets": list(targets),
        "year": session_year,
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
) -> None:
    """Step 3: `panel doctor` and the dependency gate, both clean, or the run stops."""
    arguments = ["--runtime-dir", str(runtime_dir), "--year", str(session.year)]
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


def rebalance_due(signal: SignalDay, request: StrategyRequest, *, has_book: bool) -> bool:
    """Whether today is a rebalance on the configuration's schedule (every
    `rebalance_every_sessions`-th session from its start), or the first day with no book."""
    return not has_book or signal.position % request.spec.rebalance_every_sessions == 0


def target_weights(
    signal: SignalDay, request: StrategyRequest, previous: Mapping[str, str]
) -> dict[str, Any]:
    """Step 6: today's book under the book's rule, and the move from yesterday's.

    On a rebalance day the held names that still rank within the band stay and the free slots
    are filled in rank order (`target_holdings`, the backtest's own rule). Between rebalances,
    and on a day the source held, yesterday's book is today's. Each name is `1 / holding_count`
    of the book, the book's equal position capital; what no name fills is cash.
    """
    spec = request.spec
    due = rebalance_due(signal, request, has_book=bool(previous))
    ranked = signal.scores.ranked
    if ranked is None or not due:
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
        "holding_count": spec.holding_count,
        "rebalance_every_sessions": spec.rebalance_every_sessions,
        "sessions_since_start": signal.position,
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
    }


COMPOSITE_MODEL_NAME: Final[str] = "daily_selection"
"""The declared name of every composite batch this command registers."""


def prediction_batch(
    signal: SignalDay,
    request: StrategyRequest,
    registration: Registration,
    *,
    predicted_at: datetime,
) -> PredictionBatch | None:
    """The day's scores as a `PredictionBatch`, or `None` when the source held.

    **Walk-forward:** the fit in use's own batch -- the one whose scores became the book's rows
    -- restamped with the instant it is registered at.

    **Static and trailing IC:** there is no fitted model, so the batch declares the composite
    itself. `family` is `strategy_<kind>`; `feature_version` is the registered `config_id`;
    `seed` and `code_commit` are the registration's; the hyperparameters name the combine rule
    and the registration's digest. The artifact's measured fields are measured, off the day's
    inputs rather than typed: `feature_ids` are the component keys, `parameters` the weights the
    composite was taken under, `training_cutoff` the newest instant any score row or counted IC
    it read became knowable, and `training_example_count` how many rows and ICs that was. Every
    ranked security carries its composite; a security carrying some component but not all
    abstains with `ABSTAIN_INCOMPLETE_FEATURES`.
    """
    scores = signal.scores
    if scores.ranked is None:
        return None
    source = request.source
    if source.walk_forward is not None:
        batch = signal.model_batch
        if batch is None:  # pragma: no cover - a ranked walk-forward day scored a batch
            raise StepFailedError("prediction", "the fit in use scored no batch")
        return PredictionBatch(
            as_of=batch.as_of,
            predicted_at=predicted_at,
            artifact=batch.artifact,
            predictions=batch.predictions,
        )
    if signal.knowable_through is None:  # pragma: no cover - a ranked day read some row
        raise StepFailedError("prediction", "the day's composite read no dated input")
    declaration = AlphaModelDeclaration(
        name=COMPOSITE_MODEL_NAME,
        family=f"strategy_{source.kind}",
        horizon=f"{request.spec.rebalance_every_sessions}d",
        feature_version=registration.config_id,
        seed=registration.seed,
        code_commit=registration.code_commit,
        hyperparameters=(
            ("combine", source.combine),
            ("registration_sha256", registration.sha256),
        ),
    )
    artifact = AlphaModelArtifact(
        declaration=declaration,
        feature_ids=tuple(sorted(source.component_keys)),
        training_cutoff=signal.knowable_through,
        training_example_count=signal.values_consumed,
        parameters=tuple(sorted((key, float(value)) for key, value in scores.weights.items())),
    )
    rows = [Prediction(ts_code=name, score=scores.scores[name]) for name in scores.ranked]
    rows += [
        Prediction(ts_code=name, abstention=ABSTAIN_INCOMPLETE_FEATURES)
        for name in scores.incomplete
    ]
    return PredictionBatch(
        as_of=signal.instant,
        predicted_at=predicted_at,
        artifact=artifact,
        predictions=tuple(sorted(rows, key=lambda row: row.ts_code)),
    )


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
    """Step 7: file `batch` unless this day is already registered under its declaration.

    `model daily-run` files a new record on every invocation, because `predicted_at` reaches the
    address; a daily command re-run the same evening must not. So the store is searched first
    for a record about the same instant under the same declaration: holding the same numbers it
    is the day's record and nothing is written; holding different ones, the registered record
    stands and this run is refused -- a second answer to one day would be a revision, which the
    prediction store exists to make impossible.

    **Only before the outcome starts.** The store calls a record `forward` when it held it before
    the outcome window's last close. That is the store's question; this command's is stricter,
    because the book trades the day's scores at the next session's open and every session after
    it prints part of the outcome. So a registration at or after the next session's open (09:30
    Shanghai) -- the command run late, or catching up a missed day -- is refused before anything
    is filed.
    """
    store = FilePredictionStore(runtime_dir / "predictions", clock=clock)
    for record_id in store.list_ids():
        held = store.get(record_id)
        if held is None or held.batch.as_of != batch.as_of:
            continue
        if held.batch.artifact.declaration != batch.artifact.declaration:
            continue
        if (held.batch.artifact, held.batch.predictions) != (batch.artifact, batch.predictions):
            raise StepFailedError(
                "prediction",
                f"{record_id} already registers {batch.as_of.isoformat()} under this "
                "declaration with other scores; the registered record stands and nothing was "
                "filed. The panel or a factor build changed after it was registered",
            )
        return held, "unchanged"
    day = batch.as_of.astimezone(SHANGHAI).date()
    opens = datetime.combine(calendar.next_trading_day(day), NEXT_SESSION_OPEN, tzinfo=SHANGHAI)
    deadline = outcome_known_at_for(batch, calendar=calendar, zone=SHANGHAI)
    now = clock()
    if now >= opens:
        raise StepFailedError(
            "prediction",
            f"the scores of {day.isoformat()} are traded from the next session's open, "
            f"{opens.isoformat()}, and it is {now.isoformat()}: part of their outcome has "
            f"printed already (all of it by {deadline.isoformat()}), so registered now they "
            "would not be a prediction made before its outcome. Nothing was filed",
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
    registration = admit_registration(options.registration, options.repo)
    store = PanelStore(runtime_dir / "panel")
    directory = journal_directory(runtime_dir, registration)
    first_as_of = options.as_of or clock()
    anchor = _registered_anchor(registration)
    # The configuration resolved once, about its own first day, before anything is fetched: a
    # configuration that cannot be put is a step-1 refusal, and which tiers and whether
    # industries are read decide steps 2 and 4.
    probe = day_request(registration.config, day=anchor, as_of=session_publication_instant(anchor))
    exchange = probe.exchange
    builds = tiers_read(probe)
    targets = options.targets or (
        DAILY_TARGETS + (INDUSTRY_TARGETS if reads_industries(probe, builds) else ())
    )

    with counted_transport(counts):
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
        if "panel" not in journal:
            panel = update_panel(
                runtime_dir,
                session_year=session.year,
                as_of=as_of,
                exchange=exchange,
                targets=targets,
            )
            journal = {**journal, "panel": {**panel, "requests": dict(sorted(counts.items()))}}
            write_journal(path, journal)

    check_panel(
        runtime_dir,
        datasets=doctor_datasets(targets),
        session=session,
        as_of=as_of,
        exchange=exchange,
    )
    request = day_request(registration.config, day=session, as_of=as_of)
    factors = build_factors(
        store,
        builds,
        session=session,
        as_of=as_of,
        exchange=request.exchange,
        max_staleness_days=options.max_staleness_days,
    )
    try:
        signal = score_day(store, request, day=session, anchor=anchor)
    except StrategyViewError as error:
        raise StepFailedError("candidates", error.disclosable) from error
    listed = candidates(signal, request, registration.config_id)
    prior_day, prior = previous_targets(directory, session)
    targets_today = {
        **target_weights(signal, request, prior),
        "previous_session": None if prior_day is None else prior_day.isoformat(),
    }
    batch = prediction_batch(signal, request, registration, predicted_at=clock())
    prediction: dict[str, Any] = {"registered": False, "reason": "the source held today"}
    if batch is not None:
        try:
            calendar = _outcome_calendar(store, request.exchange, session, as_of)
            record, outcome = register_prediction(
                runtime_dir, batch, calendar=calendar, clock=clock
            )
        except StepFailedError:
            raise
        except Exception as error:  # named and re-raised as the step's refusal, never swallowed
            raise StepFailedError("prediction", f"{type(error).__name__}: {error}") from error
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
        }
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
        f"target weights     {targets['decision']}, {len(targets['weights'])} name(s), "
        f"cash {targets['cash']}, turnover {targets['turnover']} "
        f"(previous: {targets['previous_session'] or 'none'})"
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
    lines.append(f"wording            {result['wording']}")
    return lines


LAUNCHD_LABEL: Final[str] = "com.openalpha.daily-selection"


def launchd_plist(*, repo: Path, runtime_dir: Path, log_dir: Path) -> str:
    """The launchd job that would run this command at 18:30 on weekdays. Printed, never installed.

    launchd has no exchange calendar, so it fires every weekday and the command decides: on a
    holiday the newest closed session is one whose journal is already complete, which prints
    the summary again with no request and no write.
    """
    command = (
        f'cd "{repo}" && uv run --no-sync --env-file .env python {THIS_SCRIPT} '
        f'--runtime-dir "{runtime_dir}"'
    )
    days = "\n".join(
        f"    <dict><key>Weekday</key><integer>{weekday}</integer>"
        "<key>Hour</key><integer>18</integer><key>Minute</key><integer>30</integer></dict>"
        for weekday in range(1, 6)
    )
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>{LAUNCHD_LABEL}</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/zsh</string>
    <string>-lc</string>
    <string>{command}</string>
  </array>
  <key>StartCalendarInterval</key>
  <array>
{days}
  </array>
  <key>StandardOutPath</key><string>{log_dir}/daily-selection.out.log</string>
  <key>StandardErrorPath</key><string>{log_dir}/daily-selection.err.log</string>
  <key>RunAtLoad</key><false/>
</dict>
</plist>
"""


def _instant(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise argparse.ArgumentTypeError(f"{value!r} carries no UTC offset")
    return parsed


def main(argv: Sequence[str] | None = None, *, clock: Callable[[], datetime] = _utc_now) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("--runtime-dir", type=Path, required=True)
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
        help="A panel target to update, repeatable (default: DAILY_TARGETS).",
    )
    parser.add_argument("--max-staleness-days", type=int, default=DEFAULT_MAX_STALENESS_DAYS)
    parser.add_argument("--top", type=int, default=None, help="How many candidates to print.")
    parser.add_argument("--json", action="store_true", help="Print the day's result as JSON.")
    parser.add_argument(
        "--launchd-plist",
        type=Path,
        default=None,
        metavar="LOG_DIR",
        help="Print the launchd job for this command, logging to LOG_DIR, and exit. Installs "
        "nothing.",
    )
    arguments = parser.parse_args(argv)
    if arguments.launchd_plist is not None:
        print(
            launchd_plist(
                repo=arguments.repo.resolve(),
                runtime_dir=arguments.runtime_dir.resolve(),
                log_dir=arguments.launchd_plist.resolve(),
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
