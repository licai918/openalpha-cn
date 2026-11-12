"""`V2-P7-013` Task 2.3: the agent sample, the coverage set and the score mapping are pure rules.

Plan C registers an `agent_*` prediction family over Step 0's survivors. Three decisions have to
be **frozen before a single forward session exists**, and each is a fact about the numbers rather
than about the evening command that will file them, so all three live in `domain/`:

- **Which names are scored.** `primary_sample` draws a fixed-size subset of the day's survivors
  from `sha256(salt | session | ts_code)`. The salt is registered, so the draw is reproducible
  from the protocol alone; it is a pure function of the salt, the session and the *set*, so a
  re-run cannot reshuffle the sample and a reviewer can recompute it with no code. At or above
  the set's size the draw is the census -- not a special case, the same ordering truncated.
- **Which names are additionally reported.** `coverage_set` is the survivor names among Step 0's
  paper holdings and watch list. It is a **second, exploratory** set and never a widening of the
  primary one: the primary set is a draw, the coverage set is a filter, and pooling them would
  let a coverage name that was never sampled enter the primary statistic.
- **What a signal is worth.** `score_of` maps the engine's aggregated `SignalFrame` onto the
  stored contract `Prediction` speaks: a signed number, or an abstention with a code -- **never a
  zero**, because a zero is a number and a batch full of them is indistinguishable from a model
  with an opinion (`domain/alpha_model.py::Prediction`).

The module imports no numeric stack and no sibling package: the arithmetic here is a hash and a
sign, and the layering test at the bottom of this file is the gate rather than a comment.
"""

from __future__ import annotations

import ast
import hashlib
import inspect
import math
from datetime import date
from pathlib import Path
from typing import Final

import grimp
import pytest

from openalpha_cn.domain import agent_forward

SESSION: Final[date] = date(2026, 11, 12)
NEXT_SESSION: Final[date] = date(2026, 11, 13)
SALT: Final[str] = "c-plan-c-batch-2-sample"
BOOK_SEED: Final[int] = 20260926
SURVIVORS: Final[tuple[str, ...]] = tuple(
    f"{index:06d}.SZ" if index % 2 else f"{index:06d}.SH" for index in range(1, 401)
)
"""A survivor census of 400 names, the shape `V2-P7-013`'s 300-name draw is taken from."""
DOMAIN_MODULE: Final[Path] = (
    Path(__file__).resolve().parents[3] / "src" / "openalpha_cn" / "domain" / "agent_forward.py"
)
FORBIDDEN_IMPORTS: Final[frozenset[str]] = frozenset(
    {"numpy", "pandas", "scipy", "sklearn", "duckdb", "sqlite3", "polars", "pyarrow"}
)
"""Hard rule 2's list, plus the two columnar libraries that rule's docstring names elsewhere."""


def _expected_draw(size: int) -> tuple[str, ...]:
    """The draw the plan specifies, computed here without calling the module under test."""
    ranked = sorted(
        SURVIVORS,
        key=lambda code: hashlib.sha256(
            f"{SALT}|{SESSION.isoformat()}|{code}".encode()
        ).hexdigest(),
    )
    return tuple(sorted(ranked[:size]))


def test_the_draw_is_a_pure_function_of_salt_session_and_set() -> None:
    """Same inputs, same sample; input order does not move it; the ordering rule is the plan's."""
    draw = agent_forward.primary_sample(SURVIVORS, salt=SALT, session=SESSION, size=300)

    assert draw == agent_forward.primary_sample(SURVIVORS, salt=SALT, session=SESSION, size=300)
    assert draw == agent_forward.primary_sample(
        tuple(reversed(SURVIVORS)), salt=SALT, session=SESSION, size=300
    )
    assert len(draw) == 300
    assert len(set(draw)) == 300
    assert set(draw) <= set(SURVIVORS)
    assert tuple(sorted(draw)) == draw
    # The ordering is recomputable from the protocol: sha256("salt|session|ts_code"), ascending.
    assert draw == _expected_draw(300)
    # A different session is a different draw; the sample does not leak across days.
    assert draw != agent_forward.primary_sample(
        SURVIVORS, salt=SALT, session=NEXT_SESSION, size=300
    )
    # A duplicate in the input is one name, not two rows.
    assert (
        agent_forward.primary_sample(
            SURVIVORS + SURVIVORS[:5], salt=SALT, session=SESSION, size=300
        )
        == draw
    )


def test_two_salts_give_different_draws_and_the_book_seed_is_refused_as_salt() -> None:
    """The salt has to be C's own; Step 0's seed is refused however it is spelled."""
    first = agent_forward.primary_sample(SURVIVORS, salt="c-salt-one", session=SESSION, size=300)
    second = agent_forward.primary_sample(SURVIVORS, salt="c-salt-two", session=SESSION, size=300)
    assert first != second

    for tied in (str(BOOK_SEED), f" {BOOK_SEED} ", f"seed-{BOOK_SEED}", f"book_seed_{BOOK_SEED}"):
        with pytest.raises(ValueError, match="seed"):
            agent_forward.refuse_a_salt_tied_to_the_book(tied, book_seed=BOOK_SEED)

    # The registration's own salt passes, and so does the blank refusal in the next test.
    agent_forward.refuse_a_salt_tied_to_the_book(SALT, book_seed=BOOK_SEED)


def test_a_blank_salt_is_refused() -> None:
    """An empty salt draws the same sample for every registration, which is no draw at all."""
    for blank in ("", "   ", "\t\n"):
        with pytest.raises(ValueError):
            agent_forward.refuse_a_salt_tied_to_the_book(blank, book_seed=BOOK_SEED)
        with pytest.raises(ValueError):
            agent_forward.primary_sample(SURVIVORS, salt=blank, session=SESSION, size=300)


def test_size_at_or_above_the_set_is_the_census() -> None:
    """The census is the same truncation, not a branch; a zero-sized sample is refused."""
    small = SURVIVORS[:7]

    assert agent_forward.primary_sample(small, salt=SALT, session=SESSION, size=7) == tuple(
        sorted(small)
    )
    assert agent_forward.primary_sample(small, salt=SALT, session=SESSION, size=8) == tuple(
        sorted(small)
    )
    assert agent_forward.primary_sample(small, salt=SALT, session=SESSION, size=300) == tuple(
        sorted(small)
    )
    with pytest.raises(ValueError):
        agent_forward.primary_sample(SURVIVORS, salt=SALT, session=SESSION, size=0)


def test_coverage_keeps_only_survivors_and_is_never_merged_into_primary() -> None:
    """Coverage is a filter over survivors; the two sets are separate by construction."""
    holdings = ("000002.SH", "999999.SZ")
    watch_list = ("000003.SZ", "000002.SH", "888888.SZ")

    coverage = agent_forward.coverage_set(holdings, watch_list, SURVIVORS)

    assert coverage == ("000002.SH", "000003.SZ")
    # A name outside the survivor census is in no set at all.
    assert "999999.SZ" not in coverage
    assert "888888.SZ" not in coverage
    # The primary draw cannot see the book, and coverage cannot see the salt: neither can widen
    # the other. The chosen size makes the two sets provably distinct on this input.
    primary = agent_forward.primary_sample(SURVIVORS, salt=SALT, session=SESSION, size=1)
    assert set(coverage) - set(primary)
    assert set(primary) - set(coverage)
    assert "holdings" not in inspect.signature(agent_forward.primary_sample).parameters
    assert "salt" not in inspect.signature(agent_forward.coverage_set).parameters


def test_abstain_maps_to_an_abstention_never_zero() -> None:
    """An abstention is `None` plus a code, not a `0.0` that reads like a neutral opinion."""
    score, abstention = agent_forward.score_of("abstain", 0.0, None)
    assert score is None
    assert abstention == agent_forward.ABSTAIN_ALL_ABSTAINED

    assert agent_forward.score_of("abstain", 0.0, agent_forward.ABSTAIN_NOT_ROUTED) == (
        None,
        agent_forward.ABSTAIN_NOT_ROUTED,
    )
    assert agent_forward.score_of("abstain", 0.0, "model_invalid_output") == (
        None,
        agent_forward.ABSTAIN_INVALID_OUTPUT,
    )
    # The one wrong shape: a directional frame with a reason is an abstention pretending to be a
    # score, and a zero must never stand in for one.
    with pytest.raises(ValueError):
        agent_forward.score_of("abstain", 0.5, None)


def test_strength_maps_to_score_and_nan_is_refused() -> None:
    """Direction signs the strength; neutral is zero; non-finite and unknown inputs are refused."""
    assert agent_forward.score_of("bullish", 0.75, None) == (0.75, None)
    assert agent_forward.score_of("bearish", 0.75, None) == (-0.75, None)
    assert agent_forward.score_of("neutral", 0.9, None) == (0.0, None)

    bearish_zero, _abstention = agent_forward.score_of("bearish", 0.0, None)
    assert bearish_zero is not None and math.copysign(1.0, bearish_zero) == 1.0, (
        "-0.0 and 0.0 compare equal but address two predictions (domain/alpha_model.py's "
        "`_unsign_zero`); the sign of zero is normalised here too"
    )

    for bad in (math.nan, math.inf, -math.inf):
        with pytest.raises(ValueError):
            agent_forward.score_of("bullish", bad, None)
    for unknown in ("sideways", "", "Bullish"):
        with pytest.raises(ValueError):
            agent_forward.score_of(unknown, 0.5, None)


def test_the_family_and_horizon_constants_are_the_protocol_and_records_agree() -> None:
    """The two family names and the horizon are the values the evaluator and the records share."""
    assert agent_forward.AGENT_LLM_FAMILY == "agent_llm_structured/v1"
    assert agent_forward.AGENT_RULE_FAMILY == "agent_rule_baseline/v1"
    assert agent_forward.AGENT_HORIZON == "5d"
    assert agent_forward.ABSTAIN_NOT_ROUTED == "agent_not_routed"
    assert agent_forward.ABSTAIN_ALL_ABSTAINED == "agent_abstained"
    assert agent_forward.ABSTAIN_INVALID_OUTPUT == "model_invalid_output"
    # The LLM family is the one `domain/llm_contamination.py` calls an agent family.
    assert agent_forward.AGENT_LLM_FAMILY.startswith("agent_")
    assert agent_forward.AGENT_RULE_FAMILY.startswith("agent_")


def test_agent_forward_imports_nothing_numeric() -> None:
    """Hard rule 2, over the live graph: no numeric stack, no sibling `openalpha_cn` package."""
    tree = ast.parse(DOMAIN_MODULE.read_text(encoding="utf-8"), filename=str(DOMAIN_MODULE))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and not node.level and node.module is not None:
            imported.add(node.module.split(".")[0])
    assert imported <= {
        "__future__",
        "ast",
        "collections",
        "datetime",
        "hashlib",
        "math",
        "re",
        "typing",
    }, imported

    graph = grimp.build_graph("openalpha_cn", cache_dir=None, include_external_packages=True)
    leaked = {
        name
        for name in FORBIDDEN_IMPORTS & graph.modules
        if graph.direct_import_exists(
            importer="openalpha_cn.domain.agent_forward", imported=name, as_packages=True
        )
    }
    assert not leaked, f"openalpha_cn.domain.agent_forward must not import {sorted(leaked)}"
    siblings = {
        name
        for name in graph.modules
        if name.count(".") == 1
        and name.startswith("openalpha_cn.")
        and name != "openalpha_cn.domain"
    }
    edges = {
        sibling
        for sibling in siblings
        if graph.direct_import_exists(
            importer="openalpha_cn.domain.agent_forward", imported=sibling, as_packages=True
        )
    }
    assert edges == set(), (
        "a domain module reaches no sibling openalpha_cn subpackage: "
        f"openalpha_cn.domain.agent_forward reaches {sorted(edges)}"
    )
