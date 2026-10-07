"""CLI/REST/SDK equivalence for what this build *declares* (`V2-P3-019`).

`tests/integration/test_factor_interfaces.py` proves the three faces answer one question about a
stored panel. This file proves they answer one question about the build itself -- which factors
exist, what each one measures, what the six verdicts mean, and which grid cell is the answer.

## Why there was nothing to test before

`factor run` takes `--factor`, `--transform` and `--neutralization`. Nineteen factors, one
transform and one neutralisation are declared, and **no face, route or document listed a legal
value for any of the three**. The only discovery channel was a typo, and a mistyped `--factor`
answered with nineteen `fct_` content addresses -- from a help text whose own words were "the key
is the form for a human". The nineteen `note_for` disclosures, several of which say in full what a
factor deliberately does *not* measure, reached nobody.

## The two audits, and why the second one is possible at all

`test_every_key_the_three_faces_render_is_held_by_the_seal` could do a per-key tamper audit on a
run because the document is sealed. A catalog has no seal -- but each of the three declarations is
**content-addressed by `stable_model_id` over exactly its declared fields**, which is the same
property one plane down. So `test_every_declared_key_is_held_by_the_content_address_beside_it`
walks every scalar leaf of every `declaration`, perturbs one at a time, rebuilds the contract, and
requires the result either to be refused or to carry a different address. That is 19 + 1 + 1
declarations audited key by key rather than a leaf count taken on faith.

The keys **outside** a declaration -- `kind`, `handle`, `identity`, `note`, and the four
build-level tables -- are not covered by any address, so they are asserted one at a time against
their own source in `test_the_keys_outside_a_declaration_are_each_asserted_against_their_source`.
That split is the whole lesson `panel_view.py` taught: 54 rendered keys, 100% line coverage, 19
never asserted.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any, Final

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from openalpha_cn.api.app import FACTOR_HTTP_STATUS, create_app
from openalpha_cn.backtest.factor_experiment import (
    ATTRIBUTION_CELL_ORDER,
    ATTRIBUTION_STEPS,
    ATTRIBUTION_VERDICT_CODES,
    ATTRIBUTION_VERDICT_ORDER,
)
from openalpha_cn.backtest.factor_ic import FACTOR_TIER_ORDER
from openalpha_cn.cli import FACTOR_EXIT, PanelExit, app
from openalpha_cn.domain.factor import FactorDefinition
from openalpha_cn.domain.factor_neutralization import FactorNeutralizationSpec
from openalpha_cn.domain.factor_transform import FactorTransformSpec
from openalpha_cn.factor_view import (
    ACCEPTANCE_STEP,
    ATTRIBUTION_VERDICT_MEANINGS,
    CATALOG_SCHEMA_VERSION,
    KNOWN_FACTOR_RUN_LIMITATIONS,
    FactorRequestError,
    factor_catalog,
    factor_entry,
)
from openalpha_cn.panel_factors import FACTOR_DEFINITIONS, FACTOR_TRANSFORMS
from openalpha_cn.panel_neutralization import FACTOR_NEUTRALIZATIONS
from openalpha_cn.sdk import OpenAlphaSDK

COMPUTED_KEYS: Final[frozenset[str]] = frozenset(
    {"factor_id", "transform_id", "neutralization_id", "qualified_key"}
)
"""The declaration keys that are the address rather than an input to it.

`stable_model_id` hashes the *declared* fields; `qualified_key` and the three `*_id`s are pydantic
computed fields, excluded from that hash by construction and re-derived on every load. Perturbing
one and rebuilding would therefore prove nothing -- the rebuilt model recomputes it -- so they are
named here and asserted directly instead (see
`test_the_keys_outside_a_declaration_are_each_asserted_against_their_source`).
"""

CONTRACTS: Final[dict[str, Any]] = {
    "factors": FactorDefinition,
    "transforms": FactorTransformSpec,
    "neutralizations": FactorNeutralizationSpec,
}
"""Which contract class rebuilds each catalog section's `declaration`, for the tamper audit."""


def _sdk(tmp_path: Path) -> OpenAlphaSDK:
    return OpenAlphaSDK(runtime_dir=tmp_path)


def _rest(tmp_path: Path) -> TestClient:
    return TestClient(create_app(runtime_dir=tmp_path))


def _cli(*arguments: str) -> Any:
    return CliRunner().invoke(app, list(arguments))


def _cli_json(*arguments: str) -> dict[str, Any]:
    result = _cli(*arguments)
    assert result.exit_code == int(PanelExit.ok), result.stderr
    parsed: dict[str, Any] = json.loads(result.stdout)
    return parsed


def _leaf_paths(node: object, prefix: tuple[str | int, ...] = ()) -> Iterator[tuple[Any, ...]]:
    if isinstance(node, dict):
        for key, value in node.items():
            yield from _leaf_paths(value, (*prefix, key))
    elif isinstance(node, list):
        for index, value in enumerate(node):
            yield from _leaf_paths(value, (*prefix, index))
    else:
        yield prefix


def _at(node: Any, path: tuple[Any, ...]) -> Any:
    for step in path:
        node = node[step]
    return node


def _perturb(value: object) -> object:
    """One scalar changed to a different scalar of a shape the contract might still admit."""
    if isinstance(value, bool):
        return not value
    if isinstance(value, int):
        return value + 1
    if isinstance(value, float):
        return value / 2.0
    if isinstance(value, str):
        return value + "x"
    return "perturbed"


def _inputs(declaration: dict[str, Any]) -> dict[str, Any]:
    """A declaration with its computed keys removed, which is what rebuilds the contract."""
    return {key: value for key, value in declaration.items() if key not in COMPUTED_KEYS}


# --- one catalog, three faces -------------------------------------------------------------------


def test_the_three_faces_serve_one_catalog(tmp_path: Path) -> None:
    """The whole point of the file: one declaration set, three ways in.

    Equality of the whole body rather than of a summary of it -- the notes travel inside it, so
    equality here is equality of every character of every disclosure. A face that truncated one
    would disagree with the other two rather than quietly shipping less.
    """
    over_http = _rest(tmp_path).get("/api/v1/factors")
    through_sdk = _sdk(tmp_path).factor_catalog()
    on_the_command_line = _cli_json("factor", "list", "--json")

    assert over_http.status_code == FACTOR_HTTP_STATUS["answered"]
    assert over_http.json() == through_sdk == on_the_command_line == factor_catalog()
    assert through_sdk["schema_version"] == CATALOG_SCHEMA_VERSION


def test_the_catalog_answers_before_any_panel_exists(tmp_path: Path) -> None:
    """A declaration is a property of the build, not of an installation.

    Driven on an empty `--runtime-dir` that holds no panel at all, because this is the command an
    operator reaches for **before** `openalpha panel build` -- it is how they find out what to
    build. A catalog that needed a store would be unreachable exactly when it is needed.
    """
    empty = tmp_path / "nothing-here"

    assert not (empty / "panel").exists()
    assert _cli_json("factor", "list", "--json")["schema_version"] == CATALOG_SCHEMA_VERSION
    assert _rest(empty).get("/api/v1/factors").status_code == 200
    assert not (empty / "panel").exists()


def test_every_declared_contract_is_in_the_catalog_and_nothing_else_is() -> None:
    """The three sections are the three registries, compared for equality.

    Against the registries themselves rather than against a hand-written count, which is this
    repository's own rule about tables: a count of nineteen would go on passing after a twentieth
    factor arrived, and would be the drift a set comparison cannot have.
    """
    catalog = factor_catalog()

    assert [entry["handle"] for entry in catalog["factors"]] == list(
        FACTOR_DEFINITIONS.qualified_keys
    )
    assert [entry["handle"] for entry in catalog["transforms"]] == list(
        FACTOR_TRANSFORMS.qualified_keys
    )
    assert [entry["handle"] for entry in catalog["neutralizations"]] == list(
        FACTOR_NEUTRALIZATIONS.qualified_keys
    )
    assert [entry["identity"] for entry in catalog["factors"]] == list(
        FACTOR_DEFINITIONS.factor_ids
    )
    assert {entry["kind"] for entry in catalog["factors"]} == {"factor"}
    assert {entry["kind"] for entry in catalog["transforms"]} == {"transform"}
    assert {entry["kind"] for entry in catalog["neutralizations"]} == {"neutralization"}


def test_the_whole_note_travels_and_is_the_registrys_own() -> None:
    """The prose is not summarised, clipped or re-worded on the way out.

    The measured shape this closes: nineteen disclosures existed in the source and reached no face.
    `return_vol_60`'s is the one the acceptance review quoted -- it says the factor held
    `V2-P3-013`'s residual-volatility slot until `V2-P3-016` filled it, is deliberately not named
    for a residual, and that the half of its old disclosure claiming no residual was computable
    stopped being true -- so it is asserted here character for character rather than by length,
    because a face that shipped the first sentence would pass a length check and lose the
    disclosure. The phrases below moved with the note when `V2-P3-016` corrected it, which is the
    binding working: a note whose second half the code had refuted could not stay green here.
    """
    catalog = factor_catalog()
    notes = {str(entry["handle"]): entry["note"] for entry in catalog["factors"]}

    assert notes == {
        handle: FACTOR_DEFINITIONS.note_for(handle) for handle in FACTOR_DEFINITIONS.qualified_keys
    }
    assert all(note is not None and len(note) > 100 for note in notes.values())
    residual = notes["return_vol_60/v1"]
    assert residual is not None
    assert "deliberately NOT named for a residual" in residual
    assert "The second stopped being true at V2-P3-016" in residual
    assert "it is TOTAL volatility" in residual
    assert "neither is computable in this build" not in residual


# --- the per-key audits -------------------------------------------------------------------------


def test_every_declared_key_is_held_by_the_content_address_beside_it() -> None:
    """Walk every scalar leaf of every declaration, perturb one, require the address to notice.

    This is `test_every_key_the_three_faces_render_is_held_by_the_seal` on the catalog, and it is
    possible for the same reason: `declaration` is `model_dump(mode="json")` of a contract whose
    identity is `stable_model_id` over exactly its declared fields, so a key that reached the
    rendering without reaching the address would survive this and nothing else in the repository
    would see it.

    A perturbation that the contract *refuses* counts as held -- more strongly than one that moves
    the address -- because it means the value could not have been declared at all. The two are
    counted separately so neither can be zero: an audit where every leaf refused would be measuring
    pydantic, and one where every leaf moved would mean no field is validated.
    """
    catalog = factor_catalog()
    moved: list[str] = []
    refused: list[str] = []
    survivors: list[str] = []
    for section, contract in CONTRACTS.items():
        for entry in catalog[section]:
            declaration = dict(entry["declaration"])
            for path in _leaf_paths(_inputs(declaration)):
                edited = json.loads(json.dumps(_inputs(declaration)))
                parent = _at(edited, path[:-1])
                parent[path[-1]] = _perturb(_at(edited, path))
                where = f"{entry['handle']}:{'.'.join(str(step) for step in path)}"
                try:
                    rebuilt = contract.model_validate(edited)
                except ValueError:
                    refused.append(where)
                    continue
                if rebuilt.model_dump(mode="json") == declaration:
                    survivors.append(where)
                else:
                    moved.append(where)

    assert survivors == [], f"{len(survivors)} declared key(s) nothing holds: {survivors}"
    assert moved and refused


def test_the_keys_outside_a_declaration_are_each_asserted_against_their_source() -> None:
    """The five entry keys and four catalog keys no content address covers, one at a time.

    Stated as nine separate comparisons rather than as one equality against a golden body, because
    a golden body is a second copy of the renderer and drifts with it. Each of these compares the
    served value against the thing it is a projection *of*.
    """
    catalog = factor_catalog()
    entry = catalog["factors"][0]
    definition = FACTOR_DEFINITIONS.definitions[0]

    assert set(catalog) == {
        "schema_version",
        "factors",
        "transforms",
        "neutralizations",
        "tiers",
        "verdicts",
        "attribution_cells",
        "run_limitations",
    }
    assert set(entry) == {"kind", "handle", "identity", "declaration", "note"}
    assert catalog["schema_version"] == CATALOG_SCHEMA_VERSION == "factor-catalog/v1"
    assert entry["kind"] == "factor"
    assert entry["handle"] == definition.qualified_key
    assert entry["identity"] == definition.factor_id
    assert entry["declaration"] == definition.model_dump(mode="json")
    assert entry["note"] == FACTOR_DEFINITIONS.note_for(definition.qualified_key)
    assert catalog["tiers"] == list(FACTOR_TIER_ORDER)
    assert catalog["run_limitations"] == [
        {"code": limitation.code, "detail": limitation.detail}
        for limitation in KNOWN_FACTOR_RUN_LIMITATIONS
    ]


def test_every_declared_verdict_carries_a_meaning_and_no_meaning_is_invented() -> None:
    """The verdict table is the declared vocabulary, exactly -- no more and no fewer.

    Six words decided every `factor run` answer and appeared in no document at all: `grep -r` over
    `docs/`, `README.md`, `README.en.md` and `web/` found `survives`, `removed`, `reversed`,
    `amplified`, `no_baseline` and `not_measured` zero times each. This is the table that fixes
    that, so it is bound to `AttributionVerdict` by equality -- a seventh verdict added upstream
    arrives here as a failure rather than as a cell a reader cannot look up.
    """
    catalog = factor_catalog()

    assert set(ATTRIBUTION_VERDICT_MEANINGS) == ATTRIBUTION_VERDICT_CODES
    assert [verdict["code"] for verdict in catalog["verdicts"]] == list(ATTRIBUTION_VERDICT_ORDER)
    assert [verdict["meaning"] for verdict in catalog["verdicts"]] == [
        ATTRIBUTION_VERDICT_MEANINGS[code] for code in ATTRIBUTION_VERDICT_ORDER
    ]
    assert all(len(meaning) > 40 for meaning in ATTRIBUTION_VERDICT_MEANINGS.values())
    # The one meaning that has to say the dangerous thing, because exit 0 does not.
    assert "NOT a pass" in ATTRIBUTION_VERDICT_MEANINGS["not_measured"]
    assert "acceptance criterion" in ATTRIBUTION_VERDICT_MEANINGS["removed"]


def test_the_acceptance_step_is_one_of_the_declared_steps_and_is_the_neutralisation_one() -> None:
    """`ACCEPTANCE_STEP` points at a step the grid really has, and at the right one.

    Two claims and both are needed. The membership half stops the pointer from outliving a change
    to `ATTRIBUTION_STEPS` -- a face marking a row the grid no longer prints would be worse than
    marking none. The identity half is the claim itself: `factor_experiment.py` says in prose that
    `processed -> neutralized` is "the step the roadmap's annotation is about", and this is that
    sentence as an executable assertion.

    The flag is also required to be on **exactly** the cells of that step, so a rendering cannot
    mark every row and satisfy the audit vacuously.
    """
    catalog = factor_catalog()
    flagged = {
        cell["step"]
        for cell in catalog["attribution_cells"]
        if cell["decides_the_acceptance_criterion"]
    }

    assert ACCEPTANCE_STEP in ATTRIBUTION_STEPS
    assert ACCEPTANCE_STEP == ("processed", "neutralized")
    assert flagged == {"processed->neutralized"}
    assert len(catalog["attribution_cells"]) == len(ATTRIBUTION_CELL_ORDER) == 6
    assert [cell["step"] for cell in catalog["attribution_cells"]] == [
        f"{source}->{target}" for source, target, _statistic in ATTRIBUTION_CELL_ORDER
    ]
    assert [cell["statistic"] for cell in catalog["attribution_cells"]] == [
        statistic for _source, _target, statistic in ATTRIBUTION_CELL_ORDER
    ]


# --- describing one declaration -----------------------------------------------------------------


def test_describe_is_one_answer_on_all_three_faces(tmp_path: Path) -> None:
    """One handle, three faces, one entry -- and it is the entry the catalog already listed.

    The second assertion is the one that stops the two surfaces drifting: a `describe` that built
    its own projection would be a second rendering of one declaration, which is exactly the shape
    this file's per-key audit could not see.
    """
    over_http = _rest(tmp_path).get("/api/v1/factors", params={"factor": "reversal_1d/v1"})
    through_sdk = _sdk(tmp_path).describe_factor(factor="reversal_1d/v1")
    on_the_command_line = _cli_json("factor", "describe", "--factor", "reversal_1d/v1", "--json")

    assert over_http.status_code == 200
    assert over_http.json() == through_sdk == on_the_command_line
    assert through_sdk in factor_catalog()["factors"]


def test_describe_resolves_a_content_address_and_names_the_other_two_registries(
    tmp_path: Path,
) -> None:
    """`--factor` takes both spellings; `--transform` and `--neutralization` take their own.

    The content-address direction is the one a reader holding a stored observation needs, and it is
    driven rather than assumed. The other two are named separately because they are separate
    registries, which is the whole reason `factor_entry` refuses to guess.
    """
    address = FACTOR_DEFINITIONS.get("reversal_1d/v1").factor_id
    sdk = _sdk(tmp_path)

    assert sdk.describe_factor(factor=address) == sdk.describe_factor(factor="reversal_1d/v1")
    assert sdk.describe_factor(transform="cross_section_standard/v1")["kind"] == "transform"
    assert sdk.describe_factor(neutralization="industry_and_size/v1")["kind"] == "neutralization"
    assert sdk.describe_factor(transform="cross_section_standard/v1")["note"] == (
        FACTOR_TRANSFORMS.note_for("cross_section_standard/v1")
    )


def test_describe_refuses_none_and_refuses_two_rather_than_choosing(tmp_path: Path) -> None:
    """Neither zero nor two handles is resolved by precedence, on every face.

    A face that silently preferred `--factor` would answer a question the caller did not ask, and
    the caller would have no way to tell -- the body looks exactly like a correct one.
    """
    with pytest.raises(FactorRequestError, match="name exactly one of"):
        factor_entry()
    with pytest.raises(FactorRequestError, match="name exactly one of"):
        factor_entry(factor="reversal_1d/v1", transform="cross_section_standard/v1")

    client = _rest(tmp_path)
    both = client.get(
        "/api/v1/factors",
        params={"factor": "reversal_1d/v1", "transform": "cross_section_standard/v1"},
    )
    assert both.status_code == FACTOR_HTTP_STATUS["bad_request"]
    assert both.json()["detail"]["reason"] == "bad_request"

    refused = _cli("factor", "describe", "--json")
    assert refused.exit_code == int(FACTOR_EXIT["bad_request"])
    assert "name exactly one of" in refused.stderr


def test_an_undeclared_transform_is_answered_with_the_declared_handles(tmp_path: Path) -> None:
    """The refusal names what *is* declared, because a caller who mistyped needs the list."""
    with pytest.raises(FactorRequestError, match="is not a transform this build declares"):
        factor_entry(transform="zscore/v1")

    refused = _rest(tmp_path).get("/api/v1/factors", params={"neutralization": "industry/v1"})
    assert refused.status_code == FACTOR_HTTP_STATUS["bad_request"]
    assert "industry_and_size/v1" in refused.json()["detail"]["message"]


# --- the refusal a human actually meets ---------------------------------------------------------


def test_a_mistyped_factor_is_answered_with_keys_rather_than_with_content_addresses() -> None:
    """The M-2 refusal, driven in both directions.

    The measured defect: `--factor ep` reached `FactorRegistry.by_id`, whose message names every
    declared `factor_id` -- nineteen opaque digests -- from a help text that had just said "the key
    is the form for a human". The keys were never printed anywhere.

    Both halves are asserted, because fixing one by breaking the other would be no fix: the message
    must name the qualified keys, and it must **not** turn into a wall of content addresses. The
    second half is checked against every declared address rather than against the `fct_` prefix,
    which the message mentions **once** on purpose -- telling a reader that the other spelling
    exists is the useful half; printing nineteen of them is the part that was useless.
    """
    with pytest.raises(FactorRequestError, match="is not a factor this build declares") as refusal:
        factor_entry(factor="ep")

    message = str(refusal.value)
    assert "reversal_1d/v1" in message
    assert "return_on_equity_ttm/v1" in message
    assert "openalpha factor list" in message
    assert message.count("fct_") == 1
    for address in FACTOR_DEFINITIONS.factor_ids:
        assert address not in message
    for handle in FACTOR_DEFINITIONS.qualified_keys:
        assert handle in message


def test_the_run_limitations_reach_a_face_at_all() -> None:
    """Five declared boundaries on what a run answers, none of which was on any surface.

    Including the one this issue rewrote: `factor build` exists now, so the builder's own residual
    bound is what the registry carries, and a caller can read it without opening the source.
    """
    codes = {str(limitation["code"]) for limitation in factor_catalog()["run_limitations"]}

    assert codes == {limitation.code for limitation in KNOWN_FACTOR_RUN_LIMITATIONS}
    assert "the_builder_cannot_produce_a_residual_for_a_session_that_has_not_closed" in codes
    assert "the_shipped_transform_and_neutralisation_floors_exceed_a_thin_market" in codes


# --- the terminal rendering ---------------------------------------------------------------------


def test_the_terminal_declaration_parses_back_to_the_declaration_the_data_face_serves() -> None:
    """`factor describe` prints the declaration whole, and this parses it back to prove it.

    A hand-picked list of fields would be a second rendering that nothing holds -- `panel_view.py`'s
    54-keys-19-unasserted shape, arriving on a command nobody would think to audit. Printing the
    mapping as indented JSON makes the whole thing checkable in one comparison, which is why the
    rendering is written that way.
    """
    printed = _cli("factor", "describe", "--factor", "reversal_1d/v1")
    assert printed.exit_code == int(PanelExit.ok), printed.stderr
    body = printed.stdout
    opening = body.index("{")
    closing = body.rindex("}") + 1

    assert json.loads(body[opening:closing]) == factor_entry(factor="reversal_1d/v1")["declaration"]
    assert "handle      reversal_1d/v1" in body
    assert f"identity    {FACTOR_DEFINITIONS.get('reversal_1d/v1').factor_id}" in body


def test_the_terminal_note_is_the_whole_note_with_the_line_breaks_added() -> None:
    """`factor describe` wraps the prose and drops none of it.

    Asserted by rejoining the wrapped lines rather than by looking for a substring, because a
    rendering that printed the first 300 characters would satisfy any substring check written
    against the opening sentence -- and the disclosures this command exists for are usually in the
    second half.
    """
    printed = _cli("factor", "describe", "--factor", "return_vol_60/v1")
    note = FACTOR_DEFINITIONS.note_for("return_vol_60/v1")
    assert note is not None
    tail = printed.stdout.split("note\n", 1)[1]
    rejoined = " ".join(line.strip() for line in tail.splitlines() if line.strip())

    assert rejoined == " ".join(note.split())
    assert len(note) > 1500
    assert max(len(line) for line in printed.stdout.splitlines()) <= 100


def test_the_terminal_list_names_every_handle_every_verdict_and_the_acceptance_step() -> None:
    """The human face of the catalog carries the three things a caller came for.

    Every handle, because that is the command's whole reason to exist; every verdict word, because
    they appeared in no document; and the acceptance step, because a six-row grid does not say
    which row is the answer. The note is a **size** here rather than the prose -- 55 KB of it in a
    terminal is not a rendering -- and the pointer to `factor describe` is what makes it findable.
    """
    printed = _cli("factor", "list")
    assert printed.exit_code == int(PanelExit.ok), printed.stderr
    body = printed.stdout

    for handle in FACTOR_DEFINITIONS.qualified_keys:
        assert handle in body
    assert "cross_section_standard/v1" in body
    assert "industry_and_size/v1" in body
    for verdict in ATTRIBUTION_VERDICT_ORDER:
        assert verdict in body
    assert "processed->neutralized" in body
    assert "openalpha factor describe" in body
    assert "openalpha factor build" in body
    # The prose itself is not here; its size is.
    note = FACTOR_DEFINITIONS.note_for("reversal_1d/v1")
    assert note is not None and note not in body
    assert f"{len(note)} chars" in body
