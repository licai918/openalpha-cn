"""`V2-P7-013` R3: the composition root is the only place these paths are opened.

The three stores this file opens alone -- the shortlist answers, the factor experiment documents
and the run repository -- were each constructed by hand in `cli.py` at the same
`runtime_dir`-relative literal `build_storage` uses, eight call sites in all (one experiment, two
run repository, five shortlist). That is hard rule 5's
second-composition-root defect waiting to happen: the literals agree today, and nothing makes them
keep agreeing. `shortlist_store_at`/`experiment_store_at`/`run_repository_at` are the one source
for each path, modelled on `prediction_store_at` (`V2-P7-023`) and `paper_state_store_at`
(`V2-P7-017`), and the AST pins below hold the faces to using them.

`research_engine(...)` is the same move for the engine itself. `sdk.py` and `api/app.py` each
assembled `ResearchEngine` by hand, with `api/app.py` doing it twice; the wiring is identical in
all three places, and the *asymmetry* is the load-bearing half: the SDK threads `agents` and
`features` through, and the REST face deliberately does not. A shared opener that defaulted those
two to something would silently change the REST face's engine, so the test below pins that the
defaults are `None` -- which is what "composed with none" means to `ResearchEngine.__init__`.

## Why the pins read the syntax tree and not the source bytes

The plan says so, and this tree has the measured reason: `ResearchEngine(` appears in comments in
both faces (`sdk.py:166`, `api/app.py:1817`), and `"shortlists"`/`"experiments"` appear inside
`composition.py`'s own docstrings in backticks. A regex over bytes would call all of those
construction sites. `ast` sees calls and string constants, and only those.
"""

from __future__ import annotations

import ast
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

from openalpha_cn.agents.base import FeaturePlane
from openalpha_cn.agents.baseline import baseline_agents
from openalpha_cn.decisions.risk import RiskGate
from openalpha_cn.runtime.composition import (
    EXPERIMENT_DIRECTORY,
    SHORTLIST_DIRECTORY,
    STATE_DATABASE,
    build_storage,
    experiment_store_at,
    research_engine,
    run_repository_at,
    shortlist_store_at,
)
from openalpha_cn.runtime.engine import ResearchEngine
from openalpha_cn.runtime.router import AgentRouter
from openalpha_cn.storage.factor_experiments import FileExperimentStore
from openalpha_cn.storage.shortlists import FileShortlistStore
from openalpha_cn.storage.sqlite import SQLiteRunRepository

ROOT: Final[Path] = Path(__file__).resolve().parents[2]
COMPOSITION: Final[Path] = ROOT / "src" / "openalpha_cn" / "runtime" / "composition.py"
CLI: Final[Path] = ROOT / "src" / "openalpha_cn" / "cli.py"
SDK: Final[Path] = ROOT / "src" / "openalpha_cn" / "sdk.py"
API: Final[Path] = ROOT / "src" / "openalpha_cn" / "api" / "app.py"
DAILY_SELECTION: Final[Path] = ROOT / "scripts" / "daily_selection.py"


def _files_under(root: Path) -> set[Path]:
    """Every file below `root`, relative to it, or nothing when the directory is absent.

    The comparison the plan asks for is a *file set*, not an `is_file` per expected name: an
    opener that also created a directory, a journal or a sidecar would move this set, and the
    whole point of a bare opener is that it moves nothing the bare constructor does not.
    """
    if not root.exists():
        return set()
    return {path.relative_to(root) for path in root.rglob("*") if path.is_file()}


def _called_names(path: Path) -> tuple[str, ...]:
    """The name or attribute at every call in `path`.

    `f(...)` gives `"f"`, and `x.f(...)` gives `"f"` -- the callee's own name, which is what the
    pins below compare.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        function = node.func
        if isinstance(function, ast.Name):
            names.append(function.id)
        elif isinstance(function, ast.Attribute):
            names.append(function.attr)
    return tuple(names)


def _string_constants(path: Path) -> tuple[str, ...]:
    """Every string constant in `path`; a docstring is one long constant, never its words."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return tuple(
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    )


def _calls_named(path: Path, name: str) -> tuple[ast.Call, ...]:
    """Every call in `path` whose callee's own name is `name` (`f(...)` or `x.f(...)`)."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: list[ast.Call] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        function = node.func
        callee = function.id if isinstance(function, ast.Name) else None
        if callee is None and isinstance(function, ast.Attribute):
            callee = function.attr
        if callee == name:
            found.append(node)
    return tuple(found)


def _keyword_names(call: ast.Call) -> set[str]:
    """The keyword names a call passes; a `**kwargs` entry is `None` and fails a pin by design."""
    return {keyword.arg for keyword in call.keywords}


# --- 1. one opener per store, identical to the hand-constructed store --------------------------


def test_shortlist_store_at_is_the_constructor_at_the_one_declared_path(tmp_path: Path) -> None:
    opened_root = tmp_path / "opened"
    direct_root = tmp_path / "direct"

    opened = shortlist_store_at(opened_root)
    direct = FileShortlistStore(direct_root / SHORTLIST_DIRECTORY)

    assert type(opened) is type(direct)
    assert isinstance(opened, FileShortlistStore)
    assert str(opened.path) == str(opened_root / "shortlists")
    assert opened.path == opened_root / SHORTLIST_DIRECTORY
    assert opened.path.relative_to(opened_root) == direct.path.relative_to(direct_root)
    assert _files_under(opened_root) == _files_under(direct_root)


def test_experiment_store_at_is_the_constructor_at_the_one_declared_path(tmp_path: Path) -> None:
    opened_root = tmp_path / "opened"
    direct_root = tmp_path / "direct"

    opened = experiment_store_at(opened_root)
    direct = FileExperimentStore(direct_root / EXPERIMENT_DIRECTORY)

    assert type(opened) is type(direct)
    assert isinstance(opened, FileExperimentStore)
    assert str(opened.path) == str(opened_root / "experiments")
    assert opened.path == opened_root / EXPERIMENT_DIRECTORY
    assert opened.path.relative_to(opened_root) == direct.path.relative_to(direct_root)
    assert _files_under(opened_root) == _files_under(direct_root)


def test_run_repository_at_is_the_constructor_at_the_one_declared_path(tmp_path: Path) -> None:
    opened_root = tmp_path / "opened"
    direct_root = tmp_path / "direct"

    opened = run_repository_at(opened_root)
    direct = SQLiteRunRepository(direct_root / STATE_DATABASE)

    assert type(opened) is type(direct)
    assert isinstance(opened, SQLiteRunRepository)
    assert str(opened.path) == str(opened_root / "state.sqlite3")
    assert opened.path == opened_root / STATE_DATABASE
    assert opened.path.relative_to(opened_root) == direct.path.relative_to(direct_root)
    assert _files_under(opened_root) == _files_under(direct_root)
    # The one opener that is not a pure value: `SQLiteRunRepository.__init__` creates its parent
    # and initializes its tables, and the opener must inherit exactly that and no migration.
    assert (opened_root / STATE_DATABASE).is_file()


# --- 2. `research_engine` is `ResearchEngine` with the two optional wires real ------------------


def _clock() -> datetime:
    return datetime(2026, 11, 12, 15, 30, tzinfo=UTC)


class _Features:
    """A real `FeaturePlane` (the Protocol is structural), cheap enough to pass as one."""

    @property
    def feature_ids(self) -> tuple[str, ...]:
        return ("momentum_20_sessions@v1",)

    def value(self, *, ts_code: str, feature_id: str) -> float | None:
        return None


def _agent_identities(engine: ResearchEngine) -> tuple[tuple[type[object], str, object], ...]:
    """What the engine records about its roster: each agent's type, id and provenance.

    The plan calls this the `a.identity` tuple. No agent class in this build has an `identity`
    attribute -- measured, not assumed; `ResearchAgent` declares `agent_id`, `evidence_families`,
    `feature_dependencies`, `provenance` and `analyze`. So the identity compared is the one the
    run manifest records: `(type(a), a.agent_id, a.provenance)`.
    """
    return tuple((type(agent), agent.agent_id, agent.provenance) for agent in engine.agents)


def test_research_engine_with_no_agents_is_the_bare_constructor(tmp_path: Path) -> None:
    storage = build_storage(runtime_dir=tmp_path / "runtime", clock=_clock)

    opened = research_engine(
        repository=storage.repository,
        memory=storage.memory,
        recovery_store=storage.recovery_store,
        clock=_clock,
    )
    baseline = ResearchEngine(
        repository=storage.repository,
        memory=storage.memory,
        clock=_clock,
        recovery_store=storage.recovery_store,
    )

    assert type(opened) is ResearchEngine
    assert type(opened) is type(baseline)
    assert _agent_identities(opened) == _agent_identities(baseline)
    assert len(opened.agents) == len(baseline.agents) > 0
    assert opened.features is None
    assert type(opened.router) is AgentRouter
    assert type(opened.risk_gate) is RiskGate
    assert type(opened.router) is type(baseline.router)
    assert type(opened.risk_gate) is type(baseline.risk_gate)


def test_research_engine_threads_agents_and_features_through(tmp_path: Path) -> None:
    storage = build_storage(runtime_dir=tmp_path / "runtime", clock=_clock)
    agent = baseline_agents()[0]
    features = _Features()
    assert isinstance(features, FeaturePlane)

    opened = research_engine(
        repository=storage.repository,
        memory=storage.memory,
        recovery_store=storage.recovery_store,
        clock=_clock,
        agents=[agent],
        features=features,
    )
    baseline = ResearchEngine(
        repository=storage.repository,
        memory=storage.memory,
        clock=_clock,
        recovery_store=storage.recovery_store,
        agents=[agent],
        features=features,
    )

    assert opened.agents == (agent,)
    assert opened.features is features
    assert _agent_identities(opened) == _agent_identities(baseline)


# --- 3. the faces go through the composition root, and the root names each path once -----------


def test_cli_constructs_no_store_the_composition_root_opens() -> None:
    called = _called_names(CLI)

    assert "FileShortlistStore" not in called
    assert "FileExperimentStore" not in called
    assert "SQLiteRunRepository" not in called


def test_neither_face_constructs_the_research_engine() -> None:
    assert "ResearchEngine" not in _called_names(SDK)
    assert "ResearchEngine" not in _called_names(API)


def test_each_face_passes_exactly_the_engine_wires_its_call_site_had() -> None:
    """The REST face passes no `agents`/`features`; the SDK passes both (plan §6's risk row).

    `research_engine`'s `None` defaults keep meaning what `ResearchEngine.__init__` means by
    them, but a call site that supplied a roster or a feature plane would silently change what
    the face runs while the default-only tests above stayed green (review 1, finding 4). The
    keywords are pinned exactly -- and no positional argument is allowed, which would bypass the
    keyword-name comparison -- so an `agents=`/`features=` added to `api/app.py`, or dropped from
    `sdk.py`, is red here.
    """
    api_calls = _calls_named(API, "research_engine")
    assert len(api_calls) == 2, f"expected both api/app.py call sites, found {len(api_calls)}"
    for call in api_calls:
        assert call.args == [], "research_engine is keyword-only; a positional argument is a bug"
        assert _keyword_names(call) == {
            "repository",
            "memory",
            "clock",
            "recovery_store",
        }, (
            "api/app.py must compose the engine with neither agents nor features; it passed "
            f"{sorted(name or '**kwargs' for name in _keyword_names(call))}"
        )

    sdk_calls = _calls_named(SDK, "research_engine")
    assert len(sdk_calls) == 1, f"expected the one sdk.py call site, found {len(sdk_calls)}"
    assert sdk_calls[0].args == []
    assert _keyword_names(sdk_calls[0]) == {
        "repository",
        "memory",
        "clock",
        "recovery_store",
        "agents",
        "features",
    }, "sdk.py must thread its agents and features through research_engine"


def test_the_daily_selection_script_opens_the_panel_through_the_panel_view() -> None:
    assert "PanelStore" not in _called_names(DAILY_SELECTION)


def test_the_composition_root_names_each_document_directory_exactly_once() -> None:
    constants = _string_constants(COMPOSITION)

    assert constants.count(SHORTLIST_DIRECTORY) == 1
    assert constants.count(EXPERIMENT_DIRECTORY) == 1
    assert constants.count("shortlists") == 1
    assert constants.count("experiments") == 1
