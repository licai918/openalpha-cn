"""`V2-P4-018`: a fit asked about a cross section past its shelf life abstains, and says so.

Story S35 asks a stale model to abstain **explicitly**. `V2-P4-011` built the shape
(`Prediction.abstention`, exactly one of a score and a reason) and left the vocabulary here;
`V2-P4-014` wrote two of the three sentences and deliberately interpolated no count into either,
so that one code binds one condition.

What is driven here is the *contract*: the comparison, the vocabulary, and the two mistakes the
comparison is not. What a stale fit does to a fold's reported statistics -- and therefore why an
abstention is not free -- is `tests/unit/backtest/test_stale_abstention.py`.
"""

from __future__ import annotations

import dataclasses
from datetime import UTC, datetime, timedelta

import alpha_model_fixtures as fixtures
import pytest

from openalpha_cn.domain.alpha_model import (
    ABSTAIN_INCOMPLETE_FEATURES,
    ABSTAIN_STALE_MODEL,
    ABSTAIN_UNRANKABLE_CROSS_SECTION,
    ABSTENTION_VOCABULARY,
    AlphaModelError,
    Prediction,
    PredictionBatch,
    abstention_code,
    artifact_for,
    prediction_batch_for,
)
from openalpha_cn.domain.prediction_record import prediction_record_for

AS_OF = datetime(2026, 6, 30, 8, 30, tzinfo=UTC)
"""The fixture cross section's own instant -- twenty-six days past a `1d` fit's cutoff."""

GAP = AS_OF - datetime(2026, 6, 4, 15, 0, tzinfo=fixtures.SHANGHAI)
"""`as_of` less the fit's `training_cutoff`, spelled out rather than read off the artifact.

Twenty-six days and ninety minutes. Written as a literal difference of two instants so that a
fixture whose calendar moved fails here, where the number is, rather than silently widening every
`shelf_life` below until nothing in this file expires.
"""


def test_the_gap_this_file_reasons_about_is_the_one_the_fixture_actually_produces() -> None:
    """The premise, asserted before anything is built on it.

    Every shelf life below is `GAP` plus or minus a second, so a fixture whose training cutoff
    moved would turn each of those tests into a tautology about a model that never expires.
    """
    assert fixtures.fitted_reference().artifact.training_cutoff == datetime(
        2026, 6, 4, 15, 0, tzinfo=fixtures.SHANGHAI
    )
    assert (
        fixtures.cross_section(as_of=AS_OF).as_of - GAP
        == fixtures.fitted_reference().artifact.training_cutoff
    )


def test_a_fit_asked_further_past_its_cutoff_than_its_shelf_life_abstains_on_every_row() -> None:
    """S35's `stale 模型显式弃权`: not a raise, not a zero, and not a missing row."""
    fitted = fixtures.fitted_reference()
    section = fixtures.cross_section(as_of=AS_OF)

    batch = fitted.predict(section, predicted_at=AS_OF, shelf_life=GAP - timedelta(seconds=1))

    assert batch.scored == ()
    assert {item.abstention for item in batch.abstained} == {ABSTAIN_STALE_MODEL}
    assert batch.subjects == section.subjects


def test_a_shelf_life_exactly_as_wide_as_the_gap_is_fresh() -> None:
    """Equality is admitted, mirroring the leakage floor's admitted equality at the other end.

    `PredictionBatch` refuses `as_of < training_cutoff` and admits `as_of == training_cutoff`
    because training through last night's close and predicting as of it is what a daily model
    does. The same reading at this end: a fit read on the last instant its author said it was good
    for is inside the declaration, not outside it. The pair below is one second apart and is the
    only thing that can tell `>` from `>=`.
    """
    fitted = fixtures.fitted_reference()
    section = fixtures.cross_section(as_of=AS_OF)

    assert fitted.predict(section, predicted_at=AS_OF, shelf_life=GAP).scored != ()
    assert (
        fitted.predict(section, predicted_at=AS_OF, shelf_life=GAP - timedelta(seconds=1)).scored
        == ()
    )


def test_an_undeclared_shelf_life_expires_nothing_and_is_a_sentence_rather_than_an_omission() -> (
    None
):
    """`None` is `V2-P4-013`'s reading of its own embargo width: `0` is a statement, not a switch.

    The library admits "this ask declares no shelf life" because a caller may deliberately not be
    asking the question. The *faces* do not leave it unsaid -- `_declaration_view` renders
    `shelf_life_days: null` -- which is `declared_feature_version`'s arrangement.
    """
    batch = fixtures.fitted_reference().predict(
        fixtures.cross_section(as_of=AS_OF), predicted_at=AS_OF, shelf_life=None
    )

    assert len(batch.scored) == 2
    assert {item.abstention for item in batch.abstained} == {
        "the declared feature carries no value for this security"
    }, "the one abstention on this fixture is the reference model's own, not a shelf life's"


def test_a_negative_shelf_life_is_refused_rather_than_expiring_every_possible_question() -> None:
    """It would expire a fit where the leakage floor already refuses, leaving no legal ask at all.

    Refused rather than tolerated because the two rules cut from opposite ends of one axis: the
    floor forbids `as_of < training_cutoff` and this forbids `as_of - training_cutoff >
    shelf_life`, so a negative span makes every remaining instant stale. A model that answers
    nothing whatever is a declaration about the model, not a shelf life.
    """
    fitted = fixtures.fitted_reference()

    with pytest.raises(AlphaModelError, match="which is negative"):
        fitted.predict(
            fixtures.cross_section(as_of=AS_OF),
            predicted_at=AS_OF,
            shelf_life=timedelta(seconds=-1),
        )
    assert fitted.artifact.is_stale_at(AS_OF, shelf_life=timedelta(0)) is True


def test_the_leakage_floor_and_the_shelf_life_are_two_rules_and_not_two_ends_of_one() -> None:
    """One artifact, both refusals, and they differ in kind rather than only in direction.

    Before the cutoff the batch does not exist: `PredictionBatch` raises, because the fit consumed
    an outcome realized after the instant the prediction claims to stand at, and no shelf life was
    involved in reaching that conclusion. Far after it the batch exists, answers about every
    security, and carries a stated reason -- which is a *disclosure* rather than a refusal, and is
    the whole reason `V2-P4-018` abstains where `V2-P4-011` raises.
    """
    fitted = fixtures.fitted_reference()
    cutoff = fitted.artifact.training_cutoff
    early = cutoff - timedelta(seconds=1)

    with pytest.raises(ValueError, match="the fit consumed an outcome"):
        fitted.predict(
            fixtures.cross_section(as_of=early), predicted_at=AS_OF, shelf_life=timedelta(days=999)
        )

    late = fitted.predict(
        fixtures.cross_section(as_of=AS_OF), predicted_at=AS_OF, shelf_life=timedelta(days=1)
    )
    assert isinstance(late, PredictionBatch)
    assert late.subjects == fixtures.cross_section(as_of=AS_OF).subjects
    assert fitted.artifact.is_stale_at(cutoff, shelf_life=timedelta(0)) is False


def test_the_gap_is_measured_to_the_instant_the_batch_stands_at_and_not_to_its_own_clock() -> None:
    """A mutation survivor, and the fixtures could not see it because they date both alike.

    Every batch in this repository's fixtures is produced at the instant it reads, so a mutant
    comparing `predicted_at` instead of `cross_section.as_of` passed everything. The two are
    different questions and only one of them is answerable:

    - `as_of` is the panel's -- the instant the cross section was read at -- and it is what the fit
      is being asked to speak *about*. It is the same instant the leakage floor uses.
    - `predicted_at` is the **caller's**, and `V2-P4-017` is unusually plain that nothing in this
      repository can check it. A shelf life measured against it would be one a caller could clear
      by backdating, or fail by running a batch late over a cross section that is perfectly fresh.

    Driven both ways on one artifact: a batch produced a year after the market it reads is scored,
    and a batch produced the same instant over a cross section a year past the cutoff is not.
    """
    fitted = fixtures.fitted_reference()
    fresh_section = fixtures.cross_section(
        as_of=fitted.artifact.training_cutoff + timedelta(hours=1)
    )
    shelf_life = timedelta(days=2)

    late = fitted.predict(
        fresh_section,
        predicted_at=fresh_section.as_of + timedelta(days=365),
        shelf_life=shelf_life,
    )
    assert late.scored != (), "a slow producer does not make a fresh fit stale"

    stale = fitted.predict(
        fixtures.cross_section(as_of=AS_OF), predicted_at=AS_OF, shelf_life=shelf_life
    )
    assert stale.scored == ()


def test_expiry_replaces_an_opinion_the_model_had_rather_than_one_it_never_formed() -> None:
    """The scores exist and are thrown away, which is what makes this a policy and not a gap.

    The same cross section under no shelf life produces three finite, distinct numbers. Under a
    shelf life narrower than the gap it produces three abstentions -- so what expiry replaces is a
    model that *did* have an opinion, which is exactly the case a reader would otherwise be unable
    to distinguish from a model that had nothing to say.
    """
    fitted = fixtures.fitted_reference()
    section = fixtures.cross_section(
        as_of=AS_OF,
        rows=(
            ("000001.SZ", (0.05, 0.05)),
            ("000002.SZ", (0.45, 0.04)),
            ("000003.SZ", (0.25, 0.03)),
        ),
    )

    opinionated = fitted.predict(section, predicted_at=AS_OF, shelf_life=None)
    expired = fitted.predict(section, predicted_at=AS_OF, shelf_life=timedelta(days=1))

    assert len({item.score for item in opinionated.scored}) == 3
    assert expired.scored == ()
    assert expired.subjects == opinionated.subjects


def test_expiring_is_not_an_amnesty_for_a_model_that_dropped_a_security() -> None:
    """The coverage refusal runs first, so a stale model that dropped a name is still refused.

    Ordering the two the other way round would make expiry a way to hide the invisible drop
    `prediction_batch_for` exists to make impossible -- every row would be overwritten with a
    stated reason and the missing one would never be noticed.
    """
    artifact = artifact_for(
        declaration=fixtures.declaration(), training_set=fixtures.training_set()
    )
    section = fixtures.cross_section(as_of=AS_OF)

    with pytest.raises(AlphaModelError, match=r"\['000003.SZ'\] carry no row"):
        prediction_batch_for(
            artifact=artifact,
            cross_section=section,
            predicted_at=AS_OF,
            shelf_life=timedelta(days=1),
            predictions=[
                Prediction(ts_code="000001.SZ", score=0.1),
                Prediction(ts_code="000002.SZ", score=0.2),
            ],
        )


def test_an_expired_batch_is_filed_apart_from_the_scored_one_it_replaced() -> None:
    """The verdict reaches the address, which is the only trace the declared span leaves.

    `PredictionRecord` addresses the batch, and the batch's rows differ, so two runs one shelf
    life apart are two records rather than one -- correct, because they are two different answers.
    What is *not* on either record is the threshold: a reader can recompute the gap from `as_of`
    and `artifact.training_cutoff`, both of which the batch carries, but not the bar it failed.
    `a_stale_record_carries_the_verdict_and_not_the_bar_it_failed` is that boundary named.
    """
    fitted = fixtures.fitted_reference()
    # Mid-month rather than `AS_OF`: `outcome_known_at_for` needs the session *after* this one, and
    # the fixture calendar's horizon ends on 2026-06-30.
    recordable = datetime(2026, 6, 15, 7, 0, tzinfo=UTC)
    section = fixtures.cross_section(as_of=recordable)
    calendar = fixtures.trading_calendar()
    recorded_at = recordable + timedelta(minutes=1)

    def _record(shelf_life: timedelta | None) -> object:
        return prediction_record_for(
            batch=fitted.predict(section, predicted_at=recordable, shelf_life=shelf_life),
            calendar=calendar,
            zone=fixtures.SHANGHAI,
            recorded_at=recorded_at,
        )

    scored = _record(None)
    expired = _record(timedelta(days=1))
    narrower = _record(timedelta(hours=12))

    assert scored.record_id != expired.record_id
    assert expired.record_id == narrower.record_id, (
        "two shelf lives that both expire this fit produce one answer and must produce one "
        "address; a threshold that reached the digest would give one prediction two names"
    )


# --- the vocabulary S35 asks for ---------------------------------------------------------------


def test_the_vocabulary_binds_one_code_to_each_of_the_three_conditions() -> None:
    """Three codes, three sentences, and no sentence carrying a number a code would parse out.

    `V2-P4-014` gave up interpolating `MINIMUM_RANK_SECURITIES` into its own sentence to make
    this possible, so the assertion that no sentence carries a digit is that decision held rather
    than a style rule.
    """
    assert set(ABSTENTION_VOCABULARY) == {
        "incomplete_features",
        "unrankable_cross_section",
        "stale_model",
    }
    assert len(set(ABSTENTION_VOCABULARY.values())) == 3
    assert not any(
        character.isdigit() for sentence in ABSTENTION_VOCABULARY.values() for character in sentence
    )


def test_every_sentence_this_repository_produces_reads_back_to_its_own_code() -> None:
    """The two `V2-P4-014` wrote and the one `V2-P4-018` did, through one lookup."""
    assert abstention_code(ABSTAIN_INCOMPLETE_FEATURES) == "incomplete_features"
    assert abstention_code(ABSTAIN_UNRANKABLE_CROSS_SECTION) == "unrankable_cross_section"
    assert abstention_code(ABSTAIN_STALE_MODEL) == "stale_model"


def test_a_reason_this_vocabulary_has_not_met_has_no_code_and_is_not_an_error() -> None:
    """`Prediction.abstention` stays free text, so a third-party model may state its own reason.

    Answering `None` rather than raising is the whole difference between a vocabulary that closes
    over what this repository produces and one that closes over what the contract admits. Raising
    would turn `V2-P4-011`'s "scored or abstained, never absent" back into an error path for
    exactly the models that did the right thing.
    """
    assert abstention_code("a reason from somebody else's model") is None
    assert Prediction(ts_code="000001.SZ", abstention="somebody else's reason").abstention


def test_the_reference_models_own_reason_is_outside_the_vocabulary_and_that_is_measured() -> None:
    """`backtest/alpha_model.py`'s `ABSTAIN_NO_VALUE` is deliberately not a fourth code.

    That module is a *reference* rather than a baseline -- it exists to prove the contract can be
    satisfied and driven -- and its sentence is its own. Coding it would make the vocabulary a
    list of every string any implementation ever wrote, which is the open set
    `abstention_code`'s `None` answer exists to keep out.
    """
    from openalpha_cn.backtest.alpha_model import ABSTAIN_NO_VALUE

    assert ABSTAIN_NO_VALUE not in ABSTENTION_VOCABULARY.values()
    assert abstention_code(ABSTAIN_NO_VALUE) is None


def test_the_vocabulary_cannot_be_edited_through_the_name_it_is_published_under() -> None:
    """A `MappingProxyType`, `ARTIFACT_UNADDRESSED_FIELDS`' shape and its reason.

    A caller that could insert a fourth code at runtime would make "one code binds one condition"
    true only of the import that read it first.
    """
    with pytest.raises(TypeError):
        ABSTENTION_VOCABULARY["invented"] = "a reason nobody declared"  # type: ignore[index]


def test_no_field_of_the_artifact_records_the_span_it_was_read_under() -> None:
    """The shelf life is a property of the ask, and keeping it off the artifact is the decision.

    Two arguments, both `V2-P4-016`'s own. A shelf life is a rule for judging a fit *later*, and
    that issue already refused to put a metric on the artifact for making the identity of a fit
    depend on how it was later judged. And it would have reached `record_id` through
    `PredictionBatch.artifact`, so one fitted model read under two spans that both admit it would
    have been filed under two names -- which
    `test_an_expired_batch_is_filed_apart_from_the_scored_one_it_replaced` is the other half of.
    """
    from openalpha_cn.domain.alpha_model import AlphaModelArtifact, AlphaModelDeclaration

    fields = set(AlphaModelArtifact.model_fields) | set(AlphaModelDeclaration.model_fields)

    assert not any("shelf" in name or "stale" in name for name in fields), sorted(fields)
    assert "shelf_life" in {
        field.name
        for field in dataclasses.fields(
            __import__("openalpha_cn.model_view", fromlist=["ModelRunRequest"]).ModelRunRequest
        )
    }
