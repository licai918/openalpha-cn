"""`web/src/types.ts`'s factor-definition mirror, against the model that serialises it.

`V2-P5-042`. The defect this file exists because of: `types.ts` declared

    required_fields: string[];

while every server this repository ships answers

    "required_fields": [{"column": "close", "dataset": "daily"}]

so `/factor-lab/<id>` rendered `所需字段：[object Object]` on every experiment page, for
every user, from the day page ③ landed.

## Why nothing caught it, which is the part worth fixing

Three guards were in place and all three were structurally blind to it:

1. `web/src/typesContractDrift.test.ts` names `FactorExperimentEnvelope` in
   `INTENTIONALLY_UNMAPPED_TYPES`, because `docs/api/schemas/` holds five contracts and
   none of them covers the factor experiment artifact. A type on that list is checked by
   nothing at all -- the exemption is per *type*, so every field inside it is exempt too.
2. `FactorExperimentPanel.test.tsx` **did** assert the rendered field list, as
   `/close、adj_factor/`. It passed, because `web/src/test/fixtures.ts` supplied
   `["close", "adj_factor"]` -- a fixture hand-written to satisfy the wrong type. A
   fixture is only as good as the contract it was copied from, and this one was copied
   from `types.ts` rather than from a server.
3. The CLI got it right the whole time (`factor_view._decides` prints `reads daily.close`),
   so the divergence was invisible to anyone reading Python.

A checked-in JSON schema under `docs/api/schemas/` was considered and rejected as the fix.
`GET /api/v1/factors/experiments/{id}` declares no `response_model` -- it returns
`JSONResponse(content=experiment_view(...))`, so the Python object *is* the contract. A
hand-written schema beside it would be a **second** hand-maintained mirror of the same
model, free to drift from it in precisely the way `types.ts` just did, and checking one
mirror against another proves only that two people made the same mistake. The five schemas
that *are* checked in are different in kind: the Python side emits and versions them.

So the check is against the model, and the schema it compares to is **generated** by
pydantic rather than written by anyone:
`FactorDefinition.model_json_schema()`. There is no third artifact to keep in step.

## What this covers, and the one link it takes on trust

The chain from the model to the browser has three edges:

    FactorDefinition  --(a)-->  the sealed artifact  --(b)-->  the HTTP body  --(c)-->  types.ts

- **(a) and (b)** are already held by `tests/integration/test_factor_interfaces.py`, which
  walks every scalar leaf of the served document, perturbs one at a time, and requires each
  to fail to reopen -- and asserts the body a face hands out is the document on disk.
- **(c)** is this file, and it was the unheld edge.

The link that is *measured but not re-run here* is that the definition on the wire is
`FactorDefinition`'s own serialisation. Measured directly, with `curl`, against
`openalpha serve` on a seeded temp runtime dir:

    GET /api/v1/factors/experiments/fxp_3c31ffda36fe1d75227eff70
    …"definition": {"direction": "lower_is_better", "family": "momentum_reversal",
      "key": "reversal_1d", "lookback_periods": null, "lookback_sessions": 2,
      "max_window_periods": null, "max_window_sessions": 2,
      "required_fields": [{"column": "close", "dataset": "daily"}],
      "schema_version": "factor-definition/v1", "version": 1}…

Ten keys, exactly `FactorDefinition`'s ten fields. Recording the measurement rather than
seeding a panel per run is the same trade `test_spa_addressability.py` makes when it builds
a synthetic `dist/` instead of requiring a real `pnpm build`: this stays a `tests/unit`
file, and the expensive end-to-end version of the same question already runs in
`tests/integration`.

## Under-declaration stays legal

`types.ts` mirrors six of the ten fields. That is not drift and is not failed here, for the
reason `typesContractDrift.test.ts` states for its own scope: a field the mirror does not
declare is a field no page renders. What *is* failed is a field the mirror declares that the
model does not have, or declares with a shape the model would never send -- which is the
defect above, and which this file goes red for.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Final

import pytest

from openalpha_cn.domain.factor import FactorDefinition

REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[2]
TYPES_MODULE: Final[Path] = REPO_ROOT / "web" / "src" / "types.ts"

BLOCK_COMMENT: Final[re.Pattern[str]] = re.compile(r"/\*.*?\*/", re.DOTALL)
LINE_COMMENT: Final[re.Pattern[str]] = re.compile(r"//[^\n]*")

MIRRORED_BLOCK: Final[str] = "definition"
"""The member of `FactorExperimentEnvelope` that mirrors `FactorDefinition`.

It sits at `document.artifact.spec.ic.definition`, and it is the only place in `types.ts`
that spells this model out.
"""


def _without_comments(source: str) -> str:
    """`types.ts` carries long JSDoc blocks between members; they are not the declaration."""
    return LINE_COMMENT.sub("", BLOCK_COMMENT.sub("", source))


def _balanced_block(source: str, opening: int) -> str:
    """The text between `source[opening]` (a `{`) and its matching `}`."""
    depth = 0
    for index in range(opening, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return source[opening + 1 : index]
    raise AssertionError(f"unbalanced braces from offset {opening} in {TYPES_MODULE}")


def _declared_members(block: str) -> dict[str, str]:
    """Every `name: type;` at the top level of one TypeScript object-type body."""
    members: dict[str, str] = {}
    depth = 0
    current = ""
    for character in block:
        if character in "{[(":
            depth += 1
        elif character in "}])":
            depth -= 1
        if character == ";" and depth == 0:
            name, _, declared = current.partition(":")
            if name.strip():
                members[name.strip()] = " ".join(declared.split())
            current = ""
            continue
        current += character
    return members


def _mirror_members() -> dict[str, str]:
    source = _without_comments(TYPES_MODULE.read_text(encoding="utf-8"))
    marker = re.search(rf"\b{MIRRORED_BLOCK}\s*:\s*\{{", source)
    assert marker is not None, (
        f"{TYPES_MODULE} no longer declares a `{MIRRORED_BLOCK}:` object literal. If the "
        f"mirror moved or was renamed, point this module at its new name -- do not delete "
        f"the check because the search stopped matching."
    )
    return _declared_members(_balanced_block(source, marker.end() - 1))


# --- the two vocabularies, reduced to one comparable shape ---------------------------------
#
# Deliberately coarse. This is not a TypeScript compiler and does not try to be: it collapses
# both sides to "what kind of thing goes here", which is the granularity at which
# `string[]` versus `array of {dataset, column}` is a difference. Bounds (`maxLength`,
# `minimum`) are on the model and not on the mirror by design, and are not compared.

STRING: Final[str] = "string"
NUMBER: Final[str] = "number"
NULLABLE_NUMBER: Final[str] = "number|null"
STRING_ARRAY: Final[str] = "string[]"


def _ts_kind(declared: str) -> tuple[str, frozenset[str] | None]:
    """One TypeScript type expression as `(kind, member values)`."""
    text = declared.strip().lstrip("|").strip()
    literals = re.findall(r'"([^"]*)"', text)
    if literals and re.fullmatch(r'\s*"[^"]*"\s*(\|\s*"[^"]*"\s*)*', text):
        return ("enum", frozenset(literals))
    if text == STRING:
        return (STRING, None)
    if text == NUMBER:
        return (NUMBER, None)
    if re.fullmatch(r"number\s*\|\s*null|null\s*\|\s*number", text):
        return (NULLABLE_NUMBER, None)
    if text == "string[]":
        return (STRING_ARRAY, None)
    array = re.fullmatch(r"\{(.+)\}\[\]", text)
    if array is not None:
        return ("object[]", frozenset(_declared_members(array.group(1) + ";")))
    return (text, None)


def _schema_kind(
    node: dict[str, object], defs: dict[str, object]
) -> tuple[str, frozenset[str] | None]:
    """One generated-JSON-Schema property as `(kind, member values)`, in `_ts_kind`'s words."""
    if "enum" in node:
        return ("enum", frozenset(node["enum"]))  # type: ignore[arg-type]
    if "const" in node:
        return ("enum", frozenset({node["const"]}))  # type: ignore[arg-type]
    if "anyOf" in node:
        options = node["anyOf"]
        assert isinstance(options, list)
        kinds = {option.get("type") for option in options}
        if kinds == {"integer", "null"} or kinds == {"number", "null"}:
            return (NULLABLE_NUMBER, None)
        return (f"anyOf{sorted(str(kind) for kind in kinds)}", None)
    if node.get("type") == "array":
        items = node.get("items")
        assert isinstance(items, dict)
        reference = items.get("$ref")
        if isinstance(reference, str):
            target = defs[reference.rsplit("/", 1)[-1]]
            assert isinstance(target, dict)
            properties = target.get("properties")
            assert isinstance(properties, dict)
            return ("object[]", frozenset(properties))
        if items.get("type") == "string":
            return (STRING_ARRAY, None)
        return ("array", None)
    if node.get("type") in {"integer", "number"}:
        return (NUMBER, None)
    if node.get("type") == "string":
        return (STRING, None)
    return (str(node.get("type")), None)


@pytest.fixture(name="model_properties")
def _model_properties() -> dict[str, tuple[str, frozenset[str] | None]]:
    schema = FactorDefinition.model_json_schema()
    defs = schema.get("$defs", {})
    return {
        name: _schema_kind(node, defs) for name, node in schema["properties"].items()
    }


def test_the_mirror_this_module_stands_for_is_the_one_on_disk() -> None:
    """The extraction, before anything is asserted with it.

    `V2-P4-038`'s rule in this file's terms: if the regex stopped matching, every assertion
    below would range over an empty dict and print green. The six names are pinned, so a
    field leaving the mirror is a red test that says which one.
    """
    members = _mirror_members()

    assert set(members) == {
        "key",
        "version",
        "family",
        "direction",
        "required_fields",
        "lookback_sessions",
    }, members


def test_no_mirrored_field_names_something_the_model_does_not_have(
    model_properties: dict[str, tuple[str, frozenset[str] | None]],
) -> None:
    """A field `types.ts` declares and `FactorDefinition` does not is drift, not licence."""
    invented = sorted(set(_mirror_members()) - set(model_properties))

    assert invented == [], (
        f"web/src/types.ts declares {invented} inside its `definition` mirror, and "
        f"FactorDefinition has no such field. The model serves "
        f"{sorted(model_properties)}."
    )


@pytest.mark.parametrize("field", sorted(_mirror_members()))
def test_each_mirrored_field_has_the_shape_the_model_serialises(
    field: str, model_properties: dict[str, tuple[str, frozenset[str] | None]]
) -> None:
    """The check the `[object Object]` defect needed.

    Parametrised per field so a failure names the field rather than a diff of six.
    `required_fields` is the row that was wrong: `string[]` on the left,
    `object[]{column, dataset}` on the right.
    """
    declared = _mirror_members()[field]

    assert _ts_kind(declared) == model_properties[field], (
        f"web/src/types.ts mirrors `{field}` as `{declared}`, which is not the shape "
        f"FactorDefinition serialises. Fix the mirror to match the model -- and check the "
        f"fixtures in web/src/test/fixtures.ts, which are hand-written and will happily "
        f"agree with a wrong mirror."
    )


def test_required_fields_is_pinned_by_name_and_not_only_by_the_sweep() -> None:
    """The one row this module was written for, spelled out rather than derived.

    The parametrised test above would still pass if `required_fields` quietly left the
    mirror -- there would simply be one less case. This is the assertion that fails if the
    field goes missing, and it states the shape in full so the expected answer is readable
    without running pydantic.
    """
    assert _ts_kind(_mirror_members()["required_fields"]) == (
        "object[]",
        frozenset({"dataset", "column"}),
    )
