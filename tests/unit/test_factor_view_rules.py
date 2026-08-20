"""The factor face's request resolution and its document store (`V2-P3-015`).

Two contracts that touch no panel and therefore belong in a unit file: `factor_view.resolve_factor`
/ `factor_request`, which decide whether a question can be put at all, and
`storage.factor_experiments.FileExperimentStore`, which decides whether an answer may be written.

Every refusal below is driven with a `match=` narrow enough to say **which rule** refused. A bare
`pytest.raises(FactorRequestError)` would pass for any of a dozen reasons and would keep passing
after the rule it was written for was deleted.
"""

from __future__ import annotations

import math
import re
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, Final

import pytest

from openalpha_cn.domain.adjustment import AdjustmentHorizonError
from openalpha_cn.domain.daily_prices import PriceDataError
from openalpha_cn.domain.factor import FactorRegistry
from openalpha_cn.domain.horizon import parse_horizon
from openalpha_cn.domain.labels import LabelError, LabelWindow
from openalpha_cn.domain.stock_universe import UniverseHorizonError
from openalpha_cn.factor_view import (
    _LABEL_CORPUS_FAULTS,
    _LABEL_CORPUS_REMEDIES,
    FACTOR_DATE_ZONE,
    MISSING_INSTANTS_SHOWN,
    PANEL_STORE_PLACEHOLDER,
    FactorRequestError,
    FactorRunBlockedError,
    _refuse_tiers_over_different_instants,
    _unlabelled_corpus_refusal,
    _without_store_path,
    factor_request,
    resolve_factor,
)
from openalpha_cn.panel.store import PanelStore
from openalpha_cn.panel_factors import FACTOR_DEFINITIONS
from openalpha_cn.storage.factor_experiments import (
    CONTENT_DIGEST_PATTERN,
    EXPERIMENT_ID_PATTERN,
    ExperimentStoreError,
    FileExperimentStore,
)

REVERSAL: Final = FACTOR_DEFINITIONS.get("reversal_1d/v1")

VALID: Final[dict[str, Any]] = {
    "factor": "reversal_1d/v1",
    "transform": "cross_section_standard/v1",
    "neutralization": "industry_and_size/v1",
    "start": date(2026, 1, 8),
    "end": date(2026, 1, 9),
    "as_of": datetime(2026, 1, 17, 4, 0, tzinfo=UTC),
    "exchange": "SZSE",
    "horizon": "1d",
    "ic_method": "spearman",
    "min_securities": 4,
    "min_as_ofs": 2,
    "group_count": 2,
    "min_securities_per_group": 2,
    "position_capital": Decimal("100000"),
    "min_periods": 2,
    "participation_cap": Decimal("0.01"),
    "min_rebalances": 1,
    "redundancy_threshold": 0.8,
    "retention_floor": 0.4,
    "code_commit": "abcdef1234567",
}

IDENTITY: Final[str] = "fxp_0123456789abcdef01234567"
DIGEST: Final[str] = "fxc_fedcba9876543210fedcba98"
OTHER_DIGEST: Final[str] = "fxc_00000000000000000000000f"
BUILT_AT: Final[datetime] = datetime(2026, 1, 18, tzinfo=UTC)


# --- which factor `<id>` names -------------------------------------------------------------------


def test_a_qualified_key_and_a_content_address_resolve_to_one_definition() -> None:
    """Both spellings, and they are the same factor.

    The CLI faces a human, so `key/vN` is the documented form; a reader holding a stored
    observation has only `factor_id`, so refusing it would make the identity the partition
    actually carries the one thing that cannot be asked about.
    """
    by_key = resolve_factor("reversal_1d/v1")
    by_id = resolve_factor(REVERSAL.factor_id)

    assert by_key == by_id == REVERSAL
    assert by_id.qualified_key == "reversal_1d/v1"
    assert REVERSAL.factor_id.startswith("fct_")


def test_the_two_spellings_are_told_apart_by_the_separator_and_not_by_a_flag() -> None:
    """A token containing `/` is a qualified key; every other token is a content address.

    `FactorDefinition.key` is constrained to a plain panel identifier precisely so that
    `qualified_key` can split on `/`, which is what makes the dispatch total rather than a guess.
    Both directions of the wrong guess are driven: a key sent without its version is not a key,
    and a content address is not looked up as one.
    """
    with pytest.raises(FactorRequestError, match="is not a declared factor"):
        resolve_factor("reversal_1d/v9")
    with pytest.raises(FactorRequestError, match="is not a factor this build declares"):
        resolve_factor("reversal_1d")
    with pytest.raises(FactorRequestError, match="is not a factor this build declares"):
        resolve_factor("fct_not_a_real_content_address")


def test_an_empty_factor_names_the_two_forms_it_accepts() -> None:
    with pytest.raises(FactorRequestError, match="names no factor"):
        resolve_factor("   ")


def test_the_registry_is_a_parameter_so_a_probe_factor_needs_no_second_resolver() -> None:
    """`compute_factor`'s `evaluators` arrangement: the build's own table is the default.

    Asserted rather than assumed, because the alternative -- a module-level lookup a test has to
    monkeypatch -- is how a second resolution path comes into existence.
    """
    empty = FactorRegistry((REVERSAL,), notes=())

    assert resolve_factor("reversal_1d/v1", registry=empty) == REVERSAL
    with pytest.raises(FactorRequestError, match="is not a declared factor"):
        resolve_factor("return_on_equity_ttm/v1", registry=empty)


# --- which questions cannot be put at all --------------------------------------------------------


def test_a_well_formed_request_resolves_every_declared_parameter_into_the_four_specs() -> None:
    """The happy path, asserted field by field so a parameter dropped on the floor fails here.

    The four upstream specs are built from the caller's own numbers; a resolver that defaulted any
    of them would produce a request that looks right and answers a different question, which is
    the whole shape Task 39 measured.
    """
    request = factor_request(**VALID)

    assert request.definition == REVERSAL
    assert request.transform.qualified_key == "cross_section_standard/v1"
    assert request.neutralization.qualified_key == "industry_and_size/v1"
    assert (request.start, request.end) == (date(2026, 1, 8), date(2026, 1, 9))
    assert request.years == (2026,)
    assert request.horizon.text == "1d"
    assert (request.ic.method, request.ic.min_securities, request.ic.min_as_ofs) == (
        "spearman",
        4,
        2,
    )
    assert request.portfolio.group_count == 2
    assert request.portfolio.min_securities_per_group == 2
    assert request.portfolio.position_capital == Decimal("100000")
    assert request.portfolio.min_periods == 2
    assert request.tradeability.participation_cap == Decimal("0.01")
    assert request.tradeability.min_rebalances == 1
    assert request.survival.redundancy_threshold == 0.8
    assert request.survival.method == "spearman"
    assert (request.retention_floor, request.code_commit) == (0.4, "abcdef1234567")
    assert request.exchange == "SZSE"


@pytest.mark.parametrize(
    ("override", "message"),
    [
        ({"end": date(2026, 1, 7)}, "is after --end"),
        ({"as_of": datetime(2026, 1, 17, 4, 0)}, "timezone-aware"),
        ({"as_of": datetime(2026, 1, 8, 4, 0, tzinfo=UTC)}, "falls before --end"),
        ({"exchange": " SZSE "}, "no surrounding whitespace"),
        ({"exchange": ""}, "no surrounding whitespace"),
        ({"horizon": "1 fortnight"}, "horizon"),
        ({"retention_floor": 0.0}, r"must be in \(0, 1\]"),
        ({"retention_floor": 1.5}, r"must be in \(0, 1\]"),
        ({"code_commit": "abc"}, "at least 7 characters"),
        ({"transform": "no_such_transform/v1"}, "is not a declared transform"),
        ({"neutralization": "no_such_neutral/v1"}, "is not a declared neutralisation"),
        ({"min_securities": 2}, "min_securities"),
        ({"min_as_ofs": 1}, "min_as_ofs"),
        ({"group_count": 1}, "group_count"),
        ({"min_periods": 0}, "min_periods"),
        ({"participation_cap": Decimal("0")}, "participation_cap"),
        ({"redundancy_threshold": 1.5}, "redundancy_threshold"),
    ],
)
def test_a_request_that_cannot_be_put_is_refused_by_the_rule_that_refuses_it(
    override: dict[str, Any], message: str
) -> None:
    """Seventeen malformed requests, each matched against the rule that should stop it.

    The `match=` is the point rather than the exception type: every one of these raises
    `FactorRequestError`, so a test that only checked the class would pass for any of them and
    would go on passing after the rule it names was deleted. The last six are the upstream
    contracts' own floors surfacing through this resolver -- `MINIMUM_IC_SECURITIES` is 3,
    `MINIMUM_IC_AS_OFS` is 2, `MINIMUM_PORTFOLIO_GROUPS` is 2 -- which is what "this module
    declares no floor of its own" has to mean in practice.
    """
    with pytest.raises(FactorRequestError, match=message):
        factor_request(**{**VALID, **override})


def test_the_as_of_bound_is_compared_in_the_panels_own_zone() -> None:
    """`--as-of` at or after `--end`, dated in `FACTOR_DATE_ZONE` and not in UTC.

    16:00 UTC on the 8th is already the 9th in Asia/Shanghai, so an `--end` of the 9th is
    satisfied by it and an `--end` of the 10th is not. Comparing in any other zone would include
    or exclude a session by an artefact of the question, which is the mistake
    `build_label_window`'s `zone` parameter exists to make impossible one plane down.
    """
    late_on_the_eighth = datetime(2026, 1, 8, 16, 0, tzinfo=UTC)
    assert late_on_the_eighth.astimezone(FACTOR_DATE_ZONE).date() == date(2026, 1, 9)

    resolved = factor_request(**{**VALID, "as_of": late_on_the_eighth})
    assert resolved.as_of == late_on_the_eighth

    with pytest.raises(FactorRequestError, match="falls before --end"):
        factor_request(**{**VALID, "as_of": late_on_the_eighth, "end": date(2026, 1, 10)})


def test_a_range_spanning_two_years_asks_for_both_partition_years() -> None:
    """`years` is the closed range's own, ascending, and a run reads every one of them."""
    request = factor_request(**{**VALID, "start": date(2025, 12, 30), "end": date(2026, 1, 9)})

    assert request.years == (2025, 2026)


# --- the document store --------------------------------------------------------------------------


def test_the_store_admits_an_identical_re_derivation_and_refuses_a_different_one(
    tmp_path: Path,
) -> None:
    """`refuse_a_restated_experiment`'s two directions, enforced on two digests.

    The admitted direction is the one `FactorInputRef` lost and had to be given back: an identity
    that moves for nothing makes a rebuild unwritable and its predecessor unreproducible. The
    refused direction names both digests, because "the numbers moved" is only actionable if a
    reader can see which two answers are in play.
    """
    store = FileExperimentStore(tmp_path / "experiments")

    assert (
        store.put(
            experiment_id=IDENTITY, content_digest=DIGEST, built_at=BUILT_AT, payload='{"a":1}'
        )
        == "created"
    )
    assert (
        store.put(
            experiment_id=IDENTITY, content_digest=DIGEST, built_at=BUILT_AT, payload='{"a":1}'
        )
        == "unchanged"
    )
    with pytest.raises(ExperimentStoreError, match="already held at content"):
        store.put(
            experiment_id=IDENTITY,
            content_digest=OTHER_DIGEST,
            built_at=BUILT_AT,
            payload='{"a":2}',
        )


def test_an_unchanged_write_keeps_the_held_bytes(tmp_path: Path) -> None:
    """Two payloads with one content digest differ only in what no digest covers.

    `built_at` is a field of the document and outside every digest, exactly so that recomputing
    one experiment at two wall clocks reproduces one identity. A store that compared **bytes**
    would refuse the second run; a store that overwrote on `unchanged` would quietly rewrite a
    document a reader already holds. It does neither: the first bytes stay.
    """
    store = FileExperimentStore(tmp_path / "experiments")
    store.put(
        experiment_id=IDENTITY, content_digest=DIGEST, built_at=BUILT_AT, payload='{"built":1}'
    )

    outcome = store.put(
        experiment_id=IDENTITY,
        content_digest=DIGEST,
        built_at=BUILT_AT.replace(year=2027),
        payload='{"built":2}',
    )

    assert outcome == "unchanged"
    assert store.get(IDENTITY) == '{"built":1}'


def test_a_key_that_is_not_a_content_address_never_reaches_the_filesystem(tmp_path: Path) -> None:
    """The whole of the path safety, and it is a shape check rather than an escaping one.

    Sanitising would turn `../../etc/passwd` into a plausible key and store a document under it;
    refusing says the key did not come from `stable_model_id` at all. Both filename components are
    held, because both reach the filesystem.
    """
    store = FileExperimentStore(tmp_path / "experiments")

    for bad in ("../../etc/passwd", "fxp_", "fxp_XYZ0123456789abcdef0123", "", "fxp_0123"):
        with pytest.raises(ExperimentStoreError, match="is not an experiment_id"):
            store.put(experiment_id=bad, content_digest=DIGEST, built_at=BUILT_AT, payload="{}")
    with pytest.raises(ExperimentStoreError, match="is not a content_digest"):
        store.put(experiment_id=IDENTITY, content_digest="", built_at=BUILT_AT, payload="{}")
    with pytest.raises(ExperimentStoreError, match="is not a content_digest"):
        store.put(experiment_id=IDENTITY, content_digest="../x", built_at=BUILT_AT, payload="{}")
    assert list((tmp_path / "experiments").glob("*")) == []


def test_a_key_with_a_trailing_newline_is_not_this_stores_key_space(tmp_path: Path) -> None:
    """`$` matches before a final newline, so `^...$` with `.match` is not "and nothing else".

    `EXPERIMENT_ID_PATTERN`'s docstring claims the key space is "exactly what `stable_model_id`
    produces, and nothing else". Under `re.match` with a trailing `$` that claim was false in
    exactly one direction, and it is the direction that reaches the filesystem: Python's `$` also
    matches immediately *before* a final newline, so `"fxp_" + 24 hex + "\\n"` was accepted, written
    as a filename component, and listed back with the newline still in it -- a key no caller can
    retype and no operator can see in a directory listing.

    `domain/panel_batch.py` records the identical bug being reproduced against `cb9e8f4` one plane
    down, where `"close\\n"` became a Parquet column name, and states the rule this store now
    follows: matched with `re.fullmatch`, never `match` with a trailing `$`.

    Both filename components are driven, because both reach the filesystem by the same door, and
    the directory is asserted empty afterwards -- the same closing assertion the sibling above
    makes, and the one that says the refusal happened *before* the write rather than after it.
    """
    store = FileExperimentStore(tmp_path / "experiments")

    with pytest.raises(ExperimentStoreError, match="is not an experiment_id"):
        store.put(
            experiment_id=f"{IDENTITY}\n", content_digest=DIGEST, built_at=BUILT_AT, payload="{}"
        )
    with pytest.raises(ExperimentStoreError, match="is not a content_digest"):
        store.put(
            experiment_id=IDENTITY, content_digest=f"{DIGEST}\n", built_at=BUILT_AT, payload="{}"
        )
    with pytest.raises(ExperimentStoreError, match="is not an experiment_id"):
        store.get(f"{IDENTITY}\n")

    assert EXPERIMENT_ID_PATTERN.fullmatch(f"{IDENTITY}\n") is None
    assert CONTENT_DIGEST_PATTERN.fullmatch(f"{DIGEST}\n") is None
    assert store.list_ids() == ()
    assert list((tmp_path / "experiments").glob("*")) == []


def test_an_empty_store_answers_rather_than_raising(tmp_path: Path) -> None:
    """A runtime directory with no experiments in it is a fresh install, not a fault."""
    store = FileExperimentStore(tmp_path / "experiments")

    assert store.list_ids() == ()
    assert store.get(IDENTITY) is None


def test_a_partial_write_is_neither_listed_nor_served(tmp_path: Path) -> None:
    """The `.partial` file a crashed write leaves behind is not a document.

    `put` writes to a temporary name and `replace`s it, so a reader never sees half a payload; the
    listing skips anything that is not a well-formed pair of content addresses, so a crash cannot
    hand a caller a key that `get` then refuses.
    """
    root = tmp_path / "experiments"
    root.mkdir()
    (root / f"{IDENTITY}.{DIGEST}.json.partial").write_text('{"half":', encoding="utf-8")
    (root / "not-a-document.json").write_text("{}", encoding="utf-8")
    store = FileExperimentStore(root)

    assert store.list_ids() == ()
    assert store.get(IDENTITY) is None


def test_two_documents_under_one_identity_are_reported_against_the_directory(
    tmp_path: Path,
) -> None:
    """A contradiction `put` cannot produce, reported against the collection rather than a caller.

    `refuse_a_restated_experiment` refuses a `held` collection that is already inconsistent with
    itself before judging an arrival, "because a guard that reported the newcomer for a
    contradiction it inherited would name the wrong record". This is that rule on a directory --
    reachable only by a hand-placed file, which is why it is driven with one.
    """
    root = tmp_path / "experiments"
    root.mkdir()
    (root / f"{IDENTITY}.{DIGEST}.json").write_text("{}", encoding="utf-8")
    (root / f"{IDENTITY}.{OTHER_DIGEST}.json").write_text("{}", encoding="utf-8")
    store = FileExperimentStore(root)

    assert store.list_ids() == (IDENTITY,)
    with pytest.raises(ExperimentStoreError, match="is held under 2 content digests"):
        store.get(IDENTITY)


ISO_INSTANT: Final[re.Pattern[str]] = re.compile(
    r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\+\d{2}:\d{2}"
)
"""Every aware instant a refusal message renders, so their order can be read off the message."""


def test_a_retention_floor_of_exactly_one_is_the_strictest_declaration_and_is_accepted() -> None:
    """The interval is `(0, 1]`, driven **at** `1.0` rather than at `1.5`.

    The suite drove the upper bound from half a unit away, where `<=` and `<` agree, so a `<` --
    which refuses the strictest declaration a caller can make -- survived. `1.0` is not a corner
    case here but the most useful setting there is: it says "every ordering the transform touched
    has to survive", which is the one declaration under which the acceptance criterion's cell
    cannot be met by a partial improvement.

    `math.nextafter` gives the smallest float above `1.0`, so the refusing half is the comparison
    itself rather than a neighbourhood of it.
    """
    accepted = factor_request(**{**VALID, "retention_floor": 1.0})

    assert accepted.retention_floor == 1.0
    with pytest.raises(FactorRequestError, match=r"must be in \(0, 1\]"):
        factor_request(**{**VALID, "retention_floor": math.nextafter(1.0, 2.0)})


def test_the_blocked_message_lists_the_missing_instants_ascending_and_never_says_zero_more() -> (
    None
):
    """The cap arithmetic, driven **at** the cap, and the order the instants are listed in.

    `MISSING_INSTANTS_SHOWN` is a cap on a list a human reads, and both halves of the arithmetic
    around it were undecidable by any fixture: the integration cases have one or two missing
    instants, never five, so `rest <= 0` and `rest < 0` agreed on all of them -- and `rest < 0`
    appends the words "(and 0 more)" to a message that has just listed *everything*, which reads as
    "there are more and I am not telling you".

    The order is asserted for the same reason it is a separate finding: the instants are listed in
    the order `as_ofs` arrives in, that order is decided a hundred lines up by one `sorted`, and
    nothing downstream of that call preserves it -- every study re-sorts and every digest is over a
    set. This message is the only place the ordering is visible, and a refusal that lists a
    caller's missing days newest-first is a refusal they have to re-sort by hand.
    """
    request = factor_request(**VALID)
    start = datetime(2026, 1, 8, 9, 0, tzinfo=UTC)
    instants = tuple(start + timedelta(days=index) for index in range(MISSING_INSTANTS_SHOWN + 2))
    complete = dict.fromkeys(instants, object())

    def _refusal(as_ofs: tuple[datetime, ...]) -> str:
        with pytest.raises(FactorRunBlockedError) as blocked:
            _refuse_tiers_over_different_instants(
                request, as_ofs=as_ofs, processed=complete, neutralized={}
            )
        return str(blocked.value)

    at_the_cap = _refusal(instants[:MISSING_INSTANTS_SHOWN])
    over_the_cap = _refusal(instants)

    assert ISO_INSTANT.findall(at_the_cap) == [
        instant.isoformat() for instant in instants[:MISSING_INSTANTS_SHOWN]
    ]
    assert "more)" not in at_the_cap
    assert ISO_INSTANT.findall(over_the_cap) == [
        instant.isoformat() for instant in instants[:MISSING_INSTANTS_SHOWN]
    ]
    assert "(and 2 more)" in over_the_cap


def test_a_redaction_replaces_the_longer_spelling_of_the_store_path_before_the_shorter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`reverse=True` is the whole function, driven on a store whose two spellings nest.

    The rule this helper exists for is that `Path.resolve()` and the configured path are two
    spellings of one directory and **one can contain the other** -- on macOS every
    `/var/folders/...` temporary directory resolves to `/private/var/folders/...`, so the
    configured string appears verbatim inside the resolved one. Replace the shorter first and the
    longer one's prefix survives the substitution: `/private/var/.../panel` becomes
    `/private<placeholder>`, and the response body a caller could reach the port for still names a
    real directory on the host.

    Reproduced with a **relative** store root instead of a symlink, because the nesting is the
    property that matters and a symlink cannot produce it portably: `resolve()` prepends the
    working directory, so `var/panel` is a substring of `/.../var/panel` by construction, on every
    platform and with no `/private` in sight. The existing end-to-end assertion
    (`test_factor_interfaces.py::test_a_refusal_body_names_no_filesystem_path`) only detects this
    where the host actually symlinks its temporary directory, which is why it did not.
    """
    monkeypatch.chdir(tmp_path)
    store = PanelStore(Path("var") / "panel")
    spellings = (str(store.root), str(store.root.resolve()))

    redacted = _without_store_path(f"could not open {store.root.resolve()}/catalog.duckdb", store)

    assert spellings[0] in spellings[1] and spellings[0] != spellings[1]
    assert redacted == f"could not open {PANEL_STORE_PLACEHOLDER}/catalog.duckdb"
    assert str(tmp_path) not in redacted


def test_the_two_filename_patterns_are_stable_model_ids_own_output() -> None:
    """`fxp_`/`fxc_` and 24 lowercase hex characters, which is what `stable_model_id` produces.

    Pinned so the store's key space cannot drift away from the function that defines it: a
    prefix change one plane down would otherwise make every key unstorable at run time rather
    than here.
    """
    assert EXPERIMENT_ID_PATTERN.match(IDENTITY)
    assert CONTENT_DIGEST_PATTERN.match(DIGEST)
    assert not EXPERIMENT_ID_PATTERN.match(DIGEST)
    assert not CONTENT_DIGEST_PATTERN.match(IDENTITY)
    assert not EXPERIMENT_ID_PATTERN.match(IDENTITY.upper())
    assert MISSING_INSTANTS_SHOWN == 5


def test_every_anticipated_label_corpus_fault_has_a_remedy_row() -> None:
    """`_LABEL_CORPUS_FAULTS` is the `except` clause and `_LABEL_CORPUS_REMEDIES`' key set both.

    `V2-P4-060`'s lesson was two sites keeping the same list of refusals by hand until one of them
    caught something the other let escape. The two here are one tuple used twice, and this is what
    makes `_unlabelled_corpus_refusal`'s lookup total rather than merely untested: every class the
    `except` admits has a row, so the `next(...)` cannot run off the end.

    Both directions, because either alone is satisfiable by deleting the other side. The last two
    assertions are the reason the split exists at all: these three are `ValueError`s that
    `except LabelError` does not catch, which is the whole of `V2-P4-084`.
    """
    assert set(_LABEL_CORPUS_FAULTS) == set(_LABEL_CORPUS_REMEDIES)
    assert len(_LABEL_CORPUS_FAULTS) == 3
    for fault in _LABEL_CORPUS_FAULTS:
        assert issubclass(fault, ValueError)
        assert not issubclass(fault, LabelError)


@pytest.mark.parametrize(
    ("raised", "about", "spells"),
    [
        (UniverseHorizonError("beyond the snapshot"), "stock_basic", "stock_basic --year"),
        (AdjustmentHorizonError("before the first factor"), "adj_factor", "adj_factor --year"),
        (PriceDataError("the two paths disagree"), "daily and adj_factor", "re-fetch the session"),
    ],
)
def test_each_corpus_refusal_names_its_own_dataset_and_its_own_repair(
    raised: Exception, about: str, spells: str
) -> None:
    """A horizon subclass resolves to its base's row, and the three remedies are not one remedy.

    The subclasses are what the domain actually raises -- `factor_on` raises
    `AdjustmentHorizonError` and `listed_on` raises `UniverseHorizonError` -- so a table keyed on
    the bases has to reach them through `isinstance`, and this is where that is asserted rather
    than assumed.

    The third row is the one an earlier draft got wrong: it told a user to rebuild `daily` for a
    contradiction `adj_factor` was equally likely to have caused. `re-fetch the session` is the
    only repair that is true of a disagreement, and it is asserted here so the distinction cannot
    be flattened back into one `--dataset {about}` line.
    """
    window = LabelWindow(
        prediction_day=date(2026, 1, 7),
        entry_day=date(2026, 1, 8),
        exit_day=date(2026, 1, 9),
        sessions=(date(2026, 1, 8), date(2026, 1, 9)),
        horizon=parse_horizon("1d"),
        exchange="SZSE",
        zone=FACTOR_DATE_ZONE,
    )

    message = _unlabelled_corpus_refusal(
        PANEL_STORE_PLACEHOLDER, subject="000001.SZ", window=window, error=raised
    )

    assert f"the stored {about} rather than the window" in message
    assert spells in message
    assert str(raised) in message
    assert "000001.SZ could not be labelled over 2026-01-08..2026-01-09" in message
