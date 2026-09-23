"""Which digest inputs `V2-P4-016` chose, and which real case every other candidate collides on.

`V2-P4-062`'s method: do not argue about an address, try each candidate and measure where it
fails. Five definitions are driven here against artifacts fitted by `V2-P4-014`'s rank baseline on
`V2-P4-013`'s real walk-forward folds, over the leak corpus whose labels come from one close
series per security. Each candidate but the shipped one collides on -- or wrongly separates -- a
case that occurs in this repository's own study code:

| candidate | the real case it gets wrong |
| --- | --- |
| A the declaration alone | two folds of one schedule are one address |
| B the artifact less `parameters` | `-0.75` and `0.0` over the same 32 rows are one address |
| C the artifact less `training_example_count` | 24 rows and 16 at one cutoff are one address |
| **D the whole artifact (shipped)** | -- |
| E the whole artifact plus D11's split policy | one fit, two block lengths, **two** addresses |

E is the direction this repository has paid for before: `V2-P3-002`'s `FactorInputRef` moved
every `manifest_id` on a byte-identical re-fetch because `fetched_at` was inside the digest. A
fold's test block is not an input to the fit -- `WalkForwardFold.candidates` is every prediction
day *strictly before* the block and the embargo floor is anchored on its first day -- so a longer
block leaves the training set untouched, which is measured below rather than reasoned about.

The purge and the embargo, D11's split policy's other half, *do* change the fit, and they reach
the address the way everything else does: through the `training_cutoff` and
`training_example_count` they leave behind. That is why the policy is not a field.
"""

from __future__ import annotations

import re
import subprocess
from datetime import date
from pathlib import Path
from typing import Any, Final

import pytest
import walk_forward_fixtures as fixtures
from pydantic import BaseModel, ConfigDict

from openalpha_cn.backtest.alpha_baseline import BASELINE_FAMILY, CrossSectionalRankModel
from openalpha_cn.backtest.walk_forward import (
    LabelledPanel,
    WalkForwardFold,
    walk_forward_folds,
)
from openalpha_cn.domain._identity import stable_model_id
from openalpha_cn.domain.alpha_model import (
    ALPHA_MODEL_ARTIFACT_PREFIX,
    AlphaModelArtifact,
    AlphaModelDeclaration,
)
from openalpha_cn.runtime.provenance import UNKNOWN_CODE_COMMIT, resolve_code_commit

CALENDAR = fixtures.trading_calendar()
DAYS: Final[tuple[date, ...]] = fixtures.prediction_days()
BLOCK: Final[date] = DAYS[fixtures.FIRST_TEST_DAY_INDEX]


def _declaration(**overrides: Any) -> AlphaModelDeclaration:
    fields: dict[str, Any] = {
        "name": "rank_baseline",
        "family": BASELINE_FAMILY,
        "horizon": fixtures.HORIZON,
        "feature_version": "features/v1",
        "seed": 7,
        "code_commit": "0123456789abcdef",
    }
    return AlphaModelDeclaration(**{**fields, **overrides})


def _fit(fold: WalkForwardFold, **overrides: Any) -> AlphaModelArtifact:
    return (
        CrossSectionalRankModel(declaration=_declaration(**overrides))
        .fit(fold.training_set)
        .artifact
    )


def _fold(panel: LabelledPanel, *, count: int, embargo: int) -> WalkForwardFold:
    return WalkForwardFold(
        panel=panel,
        calendar=CALENDAR,
        first_test_day=BLOCK,
        test_day_count=count,
        embargo_sessions=embargo,
    )


def _schedule(aligned_from: int) -> tuple[WalkForwardFold, ...]:
    return walk_forward_folds(
        fixtures.panel(aligned_from=aligned_from),
        calendar=CALENDAR,
        folds=fixtures.FOLDS,
        test_days_per_fold=fixtures.TEST_DAYS_PER_FOLD,
        embargo_sessions=fixtures.EMBARGO_SESSIONS,
    )


class _ArtifactWithSplitPolicy(BaseModel):
    """Candidate E: what D11's *split policy* field would look like if it were on the artifact."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    artifact: AlphaModelArtifact
    first_test_day: date
    test_day_count: int
    embargo_sessions: int


def _a_declaration_alone(artifact: AlphaModelArtifact, fold: WalkForwardFold) -> str:
    return stable_model_id(prefix=ALPHA_MODEL_ARTIFACT_PREFIX, model=artifact.declaration)


def _b_without_parameters(artifact: AlphaModelArtifact, fold: WalkForwardFold) -> str:
    return stable_model_id(
        prefix=ALPHA_MODEL_ARTIFACT_PREFIX, model=artifact, exclude=frozenset({"parameters"})
    )


def _c_without_row_count(artifact: AlphaModelArtifact, fold: WalkForwardFold) -> str:
    return stable_model_id(
        prefix=ALPHA_MODEL_ARTIFACT_PREFIX,
        model=artifact,
        exclude=frozenset({"training_example_count"}),
    )


def _d_the_whole_artifact(artifact: AlphaModelArtifact, fold: WalkForwardFold) -> str:
    return artifact.artifact_id


def _e_with_the_split_policy(artifact: AlphaModelArtifact, fold: WalkForwardFold) -> str:
    return stable_model_id(
        prefix=ALPHA_MODEL_ARTIFACT_PREFIX,
        model=_ArtifactWithSplitPolicy(
            artifact=artifact,
            first_test_day=fold.first_test_day,
            test_day_count=fold.test_day_count,
            embargo_sessions=fold.embargo_sessions,
        ),
    )


_CANDIDATES: Final[dict[str, Any]] = {
    "A the declaration alone": _a_declaration_alone,
    "B the artifact minus parameters": _b_without_parameters,
    "C the artifact minus the row count": _c_without_row_count,
    "D the whole artifact": _d_the_whole_artifact,
    "E the whole artifact plus the split policy": _e_with_the_split_policy,
}


def _cases() -> dict[
    str,
    tuple[
        tuple[AlphaModelArtifact, WalkForwardFold], tuple[AlphaModelArtifact, WalkForwardFold], bool
    ],
]:
    """Four real pairs and what each one must do, built once because fitting is not free."""
    overlapping = _schedule(fixtures.ALIGNED_FROM_OVERLAPPING)
    adjacent = _schedule(fixtures.ALIGNED_FROM_ADJACENT)
    whole = fixtures.panel(aligned_from=fixtures.ALIGNED_FROM_OVERLAPPING)
    later = fixtures.panel(aligned_from=fixtures.ALIGNED_FROM_OVERLAPPING, days=DAYS[2:])
    long_span, short_span = _fold(whole, count=4, embargo=0), _fold(later, count=4, embargo=0)
    four, five = _fold(whole, count=4, embargo=2), _fold(whole, count=5, embargo=2)
    return {
        "two folds of one schedule": (
            (_fit(overlapping[0]), overlapping[0]),
            (_fit(overlapping[1]), overlapping[1]),
            False,
        ),
        "two corpora, one fold": (
            (_fit(overlapping[1]), overlapping[1]),
            (_fit(adjacent[1]), adjacent[1]),
            False,
        ),
        "a panel that starts later": (
            (_fit(long_span), long_span),
            (_fit(short_span), short_span),
            False,
        ),
        "one fit, two test blocks": ((_fit(four), four), (_fit(five), five), True),
    }


CASES: Final = _cases()


def test_each_pair_this_module_measures_on_is_the_pair_it_claims_to_be() -> None:
    """The corpus asserted before anything is asserted against it, `V2-P4-013`'s own order.

    A collision table is only evidence if the pairs really are what the table says. Each reading
    below is what makes one candidate wrong and no other: the first two folds differ everywhere,
    the two corpora differ **only** in the coefficient (same cutoff, same 32 rows), the later
    panel differs **only** in the row count (same cutoff, same coefficients), and the two test
    blocks are byte-identical fits.
    """
    (early, _), (late, _), _ = CASES["two folds of one schedule"]
    assert (early.training_example_count, late.training_example_count) == (16, 32)
    assert early.training_cutoff < late.training_cutoff
    assert early.parameters != late.parameters

    (left, _), (right, _), _ = CASES["two corpora, one fold"]
    assert left.training_cutoff == right.training_cutoff
    assert left.training_example_count == right.training_example_count == 32
    assert left.parameters == (("momentum_20d", -0.75), ("value_ep", 0.75))
    assert right.parameters == (("momentum_20d", 0.0), ("value_ep", 0.0))

    (whole, _), (later, _), _ = CASES["a panel that starts later"]
    assert whole.training_cutoff == later.training_cutoff
    assert whole.parameters == later.parameters
    assert (whole.training_example_count, later.training_example_count) == (24, 16)

    (four, four_fold), (five, five_fold), _ = CASES["one fit, two test blocks"]
    assert four_fold.test_day_count != five_fold.test_day_count
    assert four_fold.train_examples == five_fold.train_examples
    assert four == five


@pytest.mark.parametrize("case", sorted(CASES))
def test_the_shipped_address_gets_every_real_case_right(case: str) -> None:
    """Both directions, on four pairs: what differs addresses apart and what is one fit is one."""
    left, right, must_share = CASES[case]

    assert (_d_the_whole_artifact(*left) == _d_the_whole_artifact(*right)) is must_share


@pytest.mark.parametrize(
    ("candidate", "case"),
    [
        ("A the declaration alone", "two folds of one schedule"),
        ("A the declaration alone", "two corpora, one fold"),
        ("A the declaration alone", "a panel that starts later"),
        ("B the artifact minus parameters", "two corpora, one fold"),
        ("C the artifact minus the row count", "a panel that starts later"),
        ("E the whole artifact plus the split policy", "one fit, two test blocks"),
    ],
)
def test_every_rejected_candidate_is_wrong_about_a_case_this_repository_produces(
    candidate: str, case: str
) -> None:
    """The measurement that chose the digest inputs, kept so the choice can be re-checked.

    Each row is a candidate paired with a case it gets **wrong** while the shipped one gets it
    right. A, B and C fail direction one -- two artifacts that differ share an address. E fails
    direction two -- one fitted model gets two addresses because the block it was later tested on
    was longer, which is `V2-P3-002`'s defect wearing D11's vocabulary.
    """
    address = _CANDIDATES[candidate]
    left, right, must_share = CASES[case]

    assert (address(*left) == address(*right)) is not must_share
    assert (_d_the_whole_artifact(*left) == _d_the_whole_artifact(*right)) is must_share


def test_a_seed_no_model_reads_still_moves_the_address_and_nothing_else() -> None:
    """The row's dependency on `V2-P0B-009`'s real seed, measured rather than assumed.

    `runtime/seeding.py` really does thread `request.random_seed` into every registered source,
    and `AlphaModelDeclaration.seed` really does reach this address. What is **not** true is that
    anything reads it: all three models in this repository say "carried and unused" in their own
    docstrings, and this is that sentence as an assertion -- two seeds, byte-identical
    coefficients, two addresses. So the seed separates *declarations* and does not yet separate
    *fits*, and `a_seed_in_the_address_is_read_by_no_model_in_this_build` records the consequence.
    """
    folds = _schedule(fixtures.ALIGNED_FROM_OVERLAPPING)
    seven, eight = _fit(folds[0]), _fit(folds[0], seed=8)

    assert seven.parameters == eight.parameters
    assert seven.training_cutoff == eight.training_cutoff
    assert seven.artifact_id != eight.artifact_id


def test_a_real_commit_reaches_the_address_and_the_honest_unknown_is_a_constant(
    tmp_path: Path,
) -> None:
    """The other half of the row's dependency, and the hazard that survives it.

    `V2-P0B-009` replaced a literal `"development"` with a three-tier resolution and its top tier
    is real -- a 40-character SHA, `-dirty`-suffixed when the workspace is not clean. Driven
    against a throwaway repository rather than against this checkout, `test_provenance.py`'s own
    arrangement and for its reason: a suite that asserted the ambient commit would fail from a
    wheel installed with no `.git`, which is precisely the case the bottom tier exists for.

    That bottom tier is a **constant**. `UNKNOWN_CODE_COMMIT` is returned for every install with
    no build stamp and no `.git`, so two genuinely different builds addressed under it share a
    `code_commit` and therefore an address. It is honest rather than plausible -- which is the
    whole difference from the `"baseline/v1"` `V2-P4-010` found in the model slot -- and it is
    still a real limit on what an `mdl_` address proves.

    `runtime/` is imported here and not from `domain/`: `domain-purity` forbids that edge, which
    is why nothing in `domain/` can resolve a commit for a caller and why a declaration's
    `code_commit` is supplied rather than measured.
    """
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=tmp_path, check=True)
    (tmp_path / "file.txt").write_text("v1", encoding="utf-8")
    subprocess.run(["git", "add", "file.txt"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "initial"], cwd=tmp_path, check=True)

    resolved = resolve_code_commit(anchor=tmp_path)

    assert re.fullmatch(r"[0-9a-f]{40}", resolved), resolved
    assert not re.fullmatch(r"[0-9a-f]{7,40}(-dirty)?", UNKNOWN_CODE_COMMIT)

    folds = _schedule(fixtures.ALIGNED_FROM_OVERLAPPING)
    real = _fit(folds[0], code_commit=resolved)
    unknown = _fit(folds[0], code_commit=UNKNOWN_CODE_COMMIT)

    assert real.artifact_id != unknown.artifact_id
    assert real.parameters == unknown.parameters
