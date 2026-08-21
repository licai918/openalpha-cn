"""`V2-P4-010`: the manifest's three component planes, and what each one reaches.

`RunManifest` had two provenance slots and neither carried provenance. Measured on `d234e4b`
by driving a real `run_cycle` (see this module's sibling
`tests/integration/test_manifest_model_provenance.py` for the end-to-end half):

- `model_versions` held one `VersionRef(component=<agent_id>, version="baseline/v1")` per
  executed agent -- an agent id in a slot named for model versions, paired with a **constant**.
- `prompt_versions` was `()` on every path.

The constant is the part worth stating precisely, because it is why nobody noticed. A field
whose value never varies contributes a fixed string to the canonical JSON and therefore
nothing at all to the address; `"baseline/v1"` being factually wrong about an LLM-backed agent
cost nothing while every agent was deterministic, and would have started costing on the first
run that was not. The measurement that proves the cost had already arrived is in the sibling
module: two runs differing only in which vendor model answered produced the **same**
`run_manifest_id` and the **same** `decision_id`.

The three planes here are Implementation Decision 10's ("Manifest 分别标识确定性、量化与 LLM
组件") and S40's, and they are deliberately *not* one tuple with a discriminator -- see
`domain/run.py`'s `AlphaModelRef` for why a quantitative model reference is a different kind
of identifier from an agent id rather than the same kind wearing a label.
"""

from __future__ import annotations

import ast
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Final

import pytest

import openalpha_cn
from openalpha_cn.domain._identity import CONTENT_ADDRESS_PATTERN, stable_model_id
from openalpha_cn.domain.alpha_model import ALPHA_MODEL_ARTIFACT_PREFIX
from openalpha_cn.domain.factor import cross_section_digest, set_digest
from openalpha_cn.domain.run import (
    AgentProvenance,
    AgentVersion,
    AlphaModelRef,
    ArtifactDigest,
    RunManifest,
    VersionRef,
)
from openalpha_cn.domain.run_mode import RunMode

NOW: Final[datetime] = datetime(2026, 1, 16, 7, 0, tzinfo=UTC)
DIGEST: Final[str] = "a" * 64
ARTIFACT: Final[str] = "mdl_" + "0" * 24
OTHER_ARTIFACT: Final[str] = "mdl_" + "1" * 24
SOURCE_ROOT: Final[Path] = Path(openalpha_cn.__file__).resolve().parent


ADDRESS_BUILDERS: Final[frozenset[str]] = frozenset({"stable_model_id", "cross_section_digest"})
"""The two functions in this repository that take a caller's prefix and return a content address.

`set_digest` is the third builder and takes no prefix -- it stamps `set_` unconditionally -- so it
contributes its one prefix through `live_prefixes` calling it rather than through this set.
"""


def _seal_digest_prefixes() -> tuple[str, ...]:
    from openalpha_cn.panel_doctor import FACTOR_PLANE_SEALS

    return tuple(seal.digest_prefix for seal in FACTOR_PLANE_SEALS)


RUNTIME_PREFIX_SOURCES: Final[dict[str, Any]] = {
    "seal.digest_prefix": _seal_digest_prefixes,
}
"""The one call site whose prefix is an attribute rather than a literal or a module constant.

`panel_doctor` re-derives each factor tier's cross-section digest with
`cross_section_digest(cells, prefix=seal.digest_prefix)`, so the prefixes are the three
`FactorPlaneSeal`s' and there is nothing in that file's AST to read them off. Resolved by
importing the seals rather than by copying `"obs"`, `"prc"` and `"nrs"` here, and keyed by the
attribute's spelling so that a *second* attribute-shaped prefix raises instead of vanishing --
which is the property the hand-written list this replaces did not have.
"""


def live_prefixes() -> list[str]:
    """Every prefix a content address in this repository can carry, read off the source tree.

    Read by AST rather than by `grep`, and `V2-P4-016` is why both halves of that matter. The
    hand-written list this replaces claimed "twenty-five prefixes in use", asserted
    `len(prefixes) == 25` against **itself**, and was wrong in two directions at once:

    - it had gone stale -- `feat`, `set` and `xs` were all missing, and `V2-P4-012` added the
      first of those four issues earlier, because a list checked against its own length is a
      tautology; and
    - **six** of its twenty-five were not content-address prefixes at all. `factor_manifest_`,
      `factor_obs_`, `factor_proc_`, `factor_neut_` and the two `*mn_` variants are
      `panel_doctor.FactorPlaneSeal`'s **dataset-name** prefixes, matched by a text search for
      `prefix=` and belonging to a different question entirely. They are also the whole of what
      that list's "seven of them contain underscores" was counting, which is six.

    So the census is now over the builders rather than over the keyword, and it covers all three:
    `stable_model_id`, `cross_section_digest` (which `domain/factor_transform.py` and
    `domain/factor_neutralization.py` call with `obs`, `prc` and `nrs`) and `set_digest`. That
    matters here because a stored `mdl_...` is only unambiguous if no other builder stamps `mdl`,
    and `stable_model_id` alone cannot answer that.

    `feature_matrix.py` and `domain/alpha_model.py` pass a module constant rather than a literal,
    so a `Name` argument is resolved against its own module's top-level assignments -- a text
    search would report the constant's *name* as the prefix. Sorted with duplicates kept, so
    occurrences can be counted rather than only membership.
    """
    found: list[str] = [
        set_digest(()).split("_")[0],
        cross_section_digest(()).split("_")[0],
    ]
    for path in sorted(SOURCE_ROOT.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        constants: dict[str, str] = {
            target.id: node.value.value
            for node in tree.body
            if isinstance(node, ast.AnnAssign | ast.Assign)
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
            for target in ([node.target] if isinstance(node, ast.AnnAssign) else node.targets)
            if isinstance(target, ast.Name)
        }
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)):
                continue
            if node.func.id not in ADDRESS_BUILDERS:
                continue
            for keyword in node.keywords:
                if keyword.arg != "prefix":
                    continue
                if isinstance(keyword.value, ast.Constant) and isinstance(keyword.value.value, str):
                    found.append(keyword.value.value)
                elif isinstance(keyword.value, ast.Name):
                    found.append(constants[keyword.value.id])
                elif isinstance(keyword.value, ast.Attribute) and isinstance(
                    keyword.value.value, ast.Name
                ):
                    spelling = f"{keyword.value.value.id}.{keyword.value.attr}"
                    found.extend(RUNTIME_PREFIX_SOURCES[spelling]())
                else:  # pragma: no cover - a fourth spelling would need its own resolution
                    raise AssertionError(f"unresolvable prefix at {path}:{node.lineno}")
    return sorted(found)


def _manifest(**overrides: Any) -> RunManifest:
    """A manifest whose three component planes are all **occupied**.

    Occupied rather than defaulted on purpose. A new field asserted only where it happens to
    differ from its default is not asserted: `()` vs `(something,)` cannot tell "this slot
    reaches the address" from "adding any key at all reaches the address", and it cannot
    detect a slot that reaches the address only when empty.
    """
    fields: dict[str, Any] = {
        "run_id": "run_golden",
        "mode": RunMode.live,
        "as_of": NOW,
        "code_commit": "0123456789abcdef",
        "config_digest": DIGEST,
        "provider_payload_digests": (ArtifactDigest(name="tushare.daily", sha256=DIGEST),),
        "agent_versions": (AgentVersion(agent_id="market-agent", kind="deterministic"),),
        "model_versions": (VersionRef(component="openai-compatible", version="qwen-max"),),
        "prompt_versions": (),
        "alpha_model_versions": (AlphaModelRef(name="lgbm-baseline", artifact_id=ARTIFACT),),
        "random_seed": 7,
        "environment": (VersionRef(component="python", version="3.11.14"),),
        "started_at": NOW,
        "finished_at": NOW + timedelta(seconds=1),
        "status": "succeeded",
        "checkpoints": (),
    }
    return RunManifest(**{**fields, **overrides})


# --- the agent plane (S40) --------------------------------------------------------------


def test_an_agents_kind_alone_moves_the_run_address() -> None:
    """S40's whole content: the same agent, run as a different kind of thing, is a different run.

    The two manifests compared here share an `agent_id`, so this cannot pass through the roster
    having changed -- which is the only thing the pre-`V2-P4-010` `model_versions` could carry.
    Under that arrangement both sides serialised to
    `[{"component": "market-agent", "version": "baseline/v1"}]` and the addresses were equal.
    """
    deterministic = _manifest(
        agent_versions=(AgentVersion(agent_id="market-agent", kind="deterministic"),)
    )
    llm_backed = _manifest(
        agent_versions=(AgentVersion(agent_id="market-agent", kind="llm_backed"),)
    )

    assert deterministic.run_manifest_id != llm_backed.run_manifest_id


def test_the_agent_roster_still_reaches_the_run_address_after_leaving_model_versions() -> None:
    """The regression guard on the move itself, not on the new field's existence.

    Before this issue the roster reached `run_manifest_id` only by being stuffed into
    `model_versions`. Emptying that slot without giving the roster its own would have silently
    *removed* a declared input from the run's identity -- two runs put to different panels of
    agents would then share an address. Asserted against a roster that differs only by one
    member, and against the same roster in a different order, because an implementation that
    sorted or de-duplicated the roster would answer the first and fail the second.
    """
    one = _manifest(agent_versions=(AgentVersion(agent_id="market-agent", kind="deterministic"),))
    two = _manifest(
        agent_versions=(
            AgentVersion(agent_id="market-agent", kind="deterministic"),
            AgentVersion(agent_id="theme-agent", kind="deterministic"),
        )
    )
    reversed_order = _manifest(
        agent_versions=(
            AgentVersion(agent_id="theme-agent", kind="deterministic"),
            AgentVersion(agent_id="market-agent", kind="deterministic"),
        )
    )

    assert len({one.run_manifest_id, two.run_manifest_id, reversed_order.run_manifest_id}) == 3


def test_every_declared_agent_kind_is_a_distinct_answer() -> None:
    """Three kinds, three addresses -- so no two of them are spellings of each other.

    `learned` has no producer inside this repository yet; `V2-P4-011`'s `AlphaModel` and
    `V2-P4-014`'s baselines are what will make one. It is declared here rather than deferred
    because it is *reachable now* -- any agent a user writes may declare it, and the manifest
    has to be able to say so -- which is the difference between this and the unreachable
    `TradeabilityVerdict.not_in_registry` branch `V2-P4-005` deleted.
    """
    addresses = {
        kind: _manifest(
            agent_versions=(AgentVersion(agent_id="an-agent", kind=kind),)
        ).run_manifest_id
        for kind in ("deterministic", "learned", "llm_backed")
    }

    assert len(set(addresses.values())) == 3


def test_an_agent_that_is_not_llm_backed_may_not_claim_a_vendor_model() -> None:
    """The declaration an agent makes is constrained in both directions, not just filled in.

    A deterministic agent naming a vendor model, and an LLM-backed agent naming none, are the
    two ways this declaration goes wrong quietly: the first puts a string into `model_versions`
    that nothing was actually called with, and the second is exactly the state the pre-issue
    engine was in -- an LLM ran and the manifest recorded no model at all.
    """
    with pytest.raises(ValueError, match="only an llm_backed agent"):
        AgentProvenance(
            kind="deterministic", model=VersionRef(component="openai-compatible", version="qwen")
        )

    with pytest.raises(ValueError, match="llm_backed agent must name"):
        AgentProvenance(kind="llm_backed")


# --- the LLM plane ----------------------------------------------------------------------


def test_the_vendor_model_reaches_the_run_address() -> None:
    """What the measured collision needs in order to stop being possible.

    Two runs answered by different vendor models must not share an address. Varied alone,
    against a fixture whose `model_versions` is already occupied, so "any change to this slot"
    and "this slot changed from empty" are not the same assertion.
    """
    qwen = _manifest(
        model_versions=(VersionRef(component="openai-compatible", version="qwen-max"),)
    )
    deepseek = _manifest(
        model_versions=(VersionRef(component="openai-compatible", version="deepseek-chat"),)
    )

    assert qwen.run_manifest_id != deepseek.run_manifest_id


# --- the quantitative plane (the third slot) --------------------------------------------


def test_a_quantitative_model_artifact_reaches_the_run_address() -> None:
    """The slot `V2-P4-016` fills, asserted as a contract before it has a producer.

    Both halves matter and only the second is about this slot in particular: an occupied slot
    against an empty one, and two occupied slots differing only in the artifact address. The
    second is what distinguishes "the manifest records that a model was used" from "the
    manifest records *which* model was used", and it is the half `"baseline/v1"` failed.
    """
    none = _manifest(alpha_model_versions=())
    one = _manifest(
        alpha_model_versions=(AlphaModelRef(name="lgbm-baseline", artifact_id=ARTIFACT),)
    )
    other = _manifest(
        alpha_model_versions=(AlphaModelRef(name="lgbm-baseline", artifact_id=OTHER_ARTIFACT),)
    )

    assert len({none.run_manifest_id, one.run_manifest_id, other.run_manifest_id}) == 3


def test_a_quantitative_model_reference_must_be_something_the_one_hash_function_produced() -> None:
    """`DecisionLedger.run_manifest_id`'s guard, applied one contract earlier and for its reason.

    A content address that is only conventionally a content address stops being one the first
    time it is convenient, and a slot that accepts `"lgbm-baseline"` as an artifact reference
    would let `V2-P4-016` ship a model whose provenance is a string somebody typed. The pattern
    is tied to `stable_model_id`'s actual output rather than restated as a literal, so this
    fails if the two ever disagree -- there is exactly one hash function in this repository and
    this is the assertion that keeps the manifest naming *its* output.

    The pattern's docstring claims it matches everything that function returns, which a single
    `mdl_` example cannot support, so every prefix any content-address builder in `src/` is
    called with is run through it -- "generic enough for whichever prefix `V2-P4-016` picks"
    measured rather than intended.

    **The list is read off the source tree, and `V2-P4-016` is why.** It was written out by hand
    at `V2-P4-010`, asserted `len(prefixes) == 25` against *itself*, and by the time this issue
    needed it -- to find out which prefixes were already taken -- it was wrong twice over. It had
    gone stale, missing `feat`, `set` and `xs`, because a list checked against its own length
    cannot notice. And **six** of its twenty-five were `panel_doctor`'s **dataset-name** prefixes
    (`factor_obs_`, `factor_manifest_`, ...), swept up by a text search for `prefix=` and never
    an address's prefix at all -- they are also the whole of what its "seven of them contain
    underscores" was counting, which is six. The measured census is 23 distinct prefixes over 26
    call sites, and **none** contains an underscore.
    """
    prefixes = live_prefixes()
    reference = AlphaModelRef(name="lgbm-baseline", artifact_id=ARTIFACT)

    assert len(prefixes) == 26
    assert len(set(prefixes)) == 23
    assert ALPHA_MODEL_ARTIFACT_PREFIX in prefixes
    assert sum(1 for prefix in prefixes if "_" in prefix) == 0
    for prefix in prefixes:
        produced = stable_model_id(prefix=prefix, model=reference)
        assert re.fullmatch(CONTENT_ADDRESS_PATTERN, produced), produced
        assert AlphaModelRef(name="lgbm-baseline", artifact_id=produced).artifact_id == produced

    for refused in ("lgbm-baseline", "mdl_" + "0" * 23, "mdl_" + "g" * 24, "MDL_" + "0" * 24):
        with pytest.raises(ValueError, match="String should match pattern"):
            AlphaModelRef(name="lgbm-baseline", artifact_id=refused)


def test_the_model_artifact_prefix_is_one_no_other_contract_had_already_taken() -> None:
    """`V2-P4-016`'s first question, answered against the tree rather than against a memory.

    A prefix is the only thing that distinguishes two content addresses over two different
    contracts -- the digest half is 24 hex characters either way -- so a reused prefix would make
    a stored `mdl_...` ambiguous about which builder produced it. Measured by taking every prefix
    `stable_model_id` is called with anywhere in `src/` and asserting exactly one of them is
    this one.
    """
    prefixes = live_prefixes()

    assert prefixes.count(ALPHA_MODEL_ARTIFACT_PREFIX) == 1
    assert not any(
        other != ALPHA_MODEL_ARTIFACT_PREFIX
        and (
            other.startswith(f"{ALPHA_MODEL_ARTIFACT_PREFIX}_")
            or f"{ALPHA_MODEL_ARTIFACT_PREFIX}" == other.rstrip("_")
        )
        for other in prefixes
    )


def test_the_quantitative_plane_is_not_the_agent_plane() -> None:
    """The row's actual complaint, as a type-level fact rather than a naming convention.

    An agent id and a quantitative model artifact are different kinds of identifier -- one is a
    name a human chose, the other is a digest a build produced and a reader can re-derive -- and
    `V2-P4-011` is where that stops being an opinion: `models/base.py`'s `ModelProvider` is
    LLM-JSON-shaped and cannot express a panel fit/predict, so the thing that produces the
    second identifier is not the thing that produces the first. Keeping them in one
    `tuple[VersionRef, ...]` is what allowed an agent id to occupy the model slot for the whole
    of v1 without anything being able to object.
    """
    with pytest.raises(ValueError):
        AlphaModelRef(name="market-agent", artifact_id="market-agent")

    assert set(AgentVersion.model_fields) == {"agent_id", "kind"}
    assert set(AlphaModelRef.model_fields) == {"name", "artifact_id"}
