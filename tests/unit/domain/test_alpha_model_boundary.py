"""The row's premise, measured: `ModelProvider` cannot express a panel fit/predict.

`V2-P4-011`'s row asserts it (`models/base.py:32-40` -- the class is at `:32` and
`generate_json`'s signature spans `:39-46`), `V2-P4-010` repeated it in
`AlphaModelRef`'s docstring, and neither measured it. This module does, three ways, because
"cannot express" is a claim about a type and a claim about a type is falsifiable:

1. **What the LLM boundary's members are and what they take** -- read off `models/base.py`'s
   own AST rather than off an import, so a member added later is seen even if nothing calls
   it.
2. **What comes back from one** -- a real `generate_json` payload, offered to
   `PredictionBatch.model_validate`, and the exact list of fields it cannot supply.
3. **Which protocol each side satisfies** -- `isinstance` against the two `runtime_checkable`
   protocols this contract declares.

The third is the one that would go red if somebody "unified" the two boundaries, and the
second is the one that says what unifying them would cost.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any, Final

import alpha_model_fixtures as fixtures
import pytest
from pydantic import ValidationError

from openalpha_cn.domain.alpha_model import (
    AlphaModel,
    FittedAlphaModel,
    PredictionBatch,
)
from openalpha_cn.models.base import ModelMetadata, ModelProvider

SOURCE: Final[Path] = (
    Path(__file__).resolve().parents[3] / "src" / "openalpha_cn" / "models" / "base.py"
)


def _protocol_members(class_name: str) -> dict[str, ast.FunctionDef]:
    """Every `def` declared directly on `class_name` in `models/base.py`, by name."""
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    declaration = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.ClassDef) and node.name == class_name
    )
    return {node.name: node for node in declaration.body if isinstance(node, ast.FunctionDef)}


def _annotations(function: ast.FunctionDef) -> tuple[tuple[str, str], ...]:
    """`(name, annotation source)` for every parameter except `self`."""
    arguments = [*function.args.args, *function.args.kwonlyargs]
    return tuple(
        (argument.arg, ast.unparse(argument.annotation))
        for argument in arguments
        if argument.arg != "self" and argument.annotation is not None
    )


class _StubLlmProvider:
    """A `ModelProvider` that answers with the best-shaped JSON an LLM could return.

    Deliberately generous: it is handed the cross section as prose and returns a per-security
    score for every name in it, which is the *most* the LLM boundary could ever give a caller
    asking for a panel prediction. What test 2 measures is what that most still is not.
    """

    def __init__(self, scores: dict[str, float]) -> None:
        self._scores = scores
        self.calls: list[tuple[str, str]] = []

    @property
    def metadata(self) -> ModelMetadata:
        return ModelMetadata(
            provider_id="stub",
            model="stub-1",
            credential_env_vars=(),
            structured_output=True,
        )

    def generate_json(self, *, system: str, user: str, schema: dict[str, Any]) -> dict[str, Any]:
        self.calls.append((system, user))
        return {"scores": dict(self._scores)}


@pytest.fixture
def fitted_reference_model() -> FittedAlphaModel:
    """The reference model under `backtest/`, fitted on real labels."""
    return fixtures.fitted_reference()


def test_the_llm_boundary_declares_no_member_that_could_receive_a_panel_fit() -> None:
    """`ModelProvider`'s whole surface is `metadata` and `generate_json`, and neither fits.

    Read off the AST so that a `fit` added to `models/base.py` tomorrow turns this red, which
    an import-and-`hasattr` check on a `Protocol` would not do as loudly: a Protocol's members
    are what a class happens to declare, and the point here is the *declaration*.
    """
    members = _protocol_members("ModelProvider")

    assert set(members) == {"metadata", "generate_json"}
    assert _annotations(members["generate_json"]) == (
        ("system", "str"),
        ("user", "str"),
        ("schema", "dict[str, Any]"),
    )
    assert members["generate_json"].returns is not None
    assert ast.unparse(members["generate_json"].returns) == "dict[str, Any]"


def test_no_parameter_of_the_llm_boundary_can_carry_an_as_of_or_a_security() -> None:
    """The specific loss: three parameters, all of them free text or an untyped mapping.

    A panel fit/predict is as-of sensitive per this repository's whole premise, and a security
    is its unit. `generate_json` has no slot for either: an `as_of` reaches it only by being
    rendered into `user`, where it stops being a `datetime` and becomes a sentence, and the
    securities come back inside a `dict[str, Any]` whose values are `Any` by declaration.
    """
    generate_json = _protocol_members("ModelProvider")["generate_json"]
    annotations = dict(_annotations(generate_json))

    assert set(annotations.values()) == {"str", "dict[str, Any]"}
    assert not any(
        "date" in annotation or "time" in annotation for annotation in annotations.values()
    ), "no parameter is dated, so an as_of can only be passed as prose"


def test_the_best_shaped_llm_answer_is_not_a_prediction_batch_and_names_what_it_lacks() -> None:
    """Offer `generate_json`'s output to `PredictionBatch` and read the refusal.

    This is the measurement the row's sentence is worth: the LLM boundary's return type is
    `dict[str, Any]`, so the question "is that a prediction batch" has an answer, and the
    answer is a list of four fields nobody at that boundary can supply -- when it was read as
    of, when it was produced, which fitted artifact produced it, and a validated per-security
    row. Those four are exactly what `V2-P4-011` declares and `V2-P4-016`/`V2-P4-017` consume.
    """
    provider: ModelProvider = _StubLlmProvider({"000001.SZ": 0.4, "600519.SH": -0.2})

    payload = provider.generate_json(
        system="You are a quantitative analyst.",
        user="Rank these securities as of 2026-06-10T08:30:00+00:00.",
        schema={"type": "object"},
    )

    assert payload == {"scores": {"000001.SZ": 0.4, "600519.SH": -0.2}}
    with pytest.raises(ValidationError) as error:
        PredictionBatch.model_validate(payload)
    missing = {item["loc"][0] for item in error.value.errors() if item["type"] == "missing"}
    assert missing == {"as_of", "predicted_at", "artifact", "predictions"}


def test_neither_boundary_satisfies_the_other(
    fitted_reference_model: FittedAlphaModel,
) -> None:
    """The two planes are disjoint at runtime, in both directions.

    `isinstance` against a `runtime_checkable` protocol checks member presence, which is
    precisely the level the row's claim lives at: an LLM provider has no `fit`, and a fitted
    quantitative model has no `generate_json`. `V2-P4-010` put an agent id and a vendor model
    in one `tuple[VersionRef, ...]` for the whole of v1 with nothing able to object; this is
    the same objection, one layer down, where the objecting can be automatic.
    """
    provider = _StubLlmProvider({"000001.SZ": 0.4})

    assert not isinstance(provider, AlphaModel)
    assert not isinstance(provider, FittedAlphaModel)
    assert isinstance(fitted_reference_model, FittedAlphaModel)
    assert not hasattr(fitted_reference_model, "generate_json")
    assert not hasattr(fitted_reference_model, "metadata")
