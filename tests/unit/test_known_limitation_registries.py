"""Every `KNOWN_*` registry, held to the suite by structure rather than by convention.

`V2-P2-007` invented a binding and proved it works: each `KNOWN_EXECUTION_LIMITATIONS` code
must be the suffix of a test function declared in the same module, checked off that module's
AST. Three separate attempts to break it -- renaming a registry entry, renaming its test,
adding a fourth entry with no test -- each go red. **It was installed on exactly one of the
registries.** All the others were held together by the convention that somebody would
write a test, and the P2 review measured what that is worth: renaming
`KNOWN_UNIVERSE_LIMITATIONS.a_listed_only_registry_is_invisible_to_every_downstream_check` --
the entry `V2-P2-008`'s whole finding rests on, and the one
`tests/integration/test_injection_register.py`'s disclosed exclusion points a reader at by
name -- left the entire suite green. Its name occurred twice in the repository, both times
inside a docstring, which is prose the runner never reads.

## Why the mechanism is not copied as it stands

`007`'s form is "one test per entry, named after the entry". At three entries that is exactly
right. Across the thirty-four code-carrying registries there are **318** entries -- 69 in
`KNOWN_PANEL_LIMITATIONS` alone -- and it stops being right somewhere well before that, for
three reasons and not one:

1. **The volume is not the real objection; the shape is.** Most of these entries are
   disclosures about what the *upstream* does not serve -- "`stock_basic` carries no
   announcement date", "`index_member_all` has no revision history". There is no branch to
   drive and no refusal to provoke, so a test named after such an entry would assert something
   about the sentence rather than about the code, and a test that asserts about a sentence is
   the drift this whole idea exists to stop wearing the badge of the fix.
2. **`KNOWN_PANEL_LIMITATIONS` is derived.** `panel_doctor._limitations()` folds seven dataset
   registries one-for-one and `KNOWN_STORAGE_LIMITATIONS` plane-wide, so 64 of its 65 codes are
   already somebody else's. Requiring a test named after each would mean writing every one of
   those tests twice, in a module that has nothing to say about them.
3. **A per-entry test is only as strong as its body.** `007`'s three have real bodies because
   somebody wrote them that way; the AST check cannot tell `def test_<code>(): pass` from a
   proof. The naming convention is what is enforced, not the exercising.

`V2-P4-013` added the twenty-eighth (`KNOWN_WALK_FORWARD_LIMITATIONS`, eleven entries) and
moved them from 247 / 69, `V2-P4-014` added the twenty-ninth
(`KNOWN_BASELINE_LIMITATIONS`, ten entries) and moved them from 258 / 69, and `V2-P4-015` added
the thirtieth (`KNOWN_TREE_LIMITATIONS`, seven entries) and moved them from 268 / 69.

**Those two totals are the argument, so they are not left as prose.**
`test_the_registries_together_carry_the_entry_count_the_report_folds` holds `REGISTRY_ENTRY_COUNTS`
against them -- an **equality per registry** since `V2-P4-038`, where a floor stood until that
issue measured what a floor is worth: any non-negative net change satisfies it, so a registry
losing a real limitation while another gains one is green. The direction the argument needs is
still covered, because an equality covers both -- "one test per entry does not scale" stops being
true if the registries shrink back towards `007`'s three, and now they cannot shrink quietly.
The figures above are re-measured whenever a registry grows -- `V2-P3-017` added a twelfth
financial-statement limitation and moved them from 187 / 64, `V2-P3-016` added a whole
twentieth registry (`KNOWN_INDEX_PRICE_LIMITATIONS`, four entries) and moved them from 189 / 65,
`V2-P4-004` added a twenty-first (`KNOWN_CROSS_SECTION_LIMITATIONS`, seven entries) and moved
them from 197 / 69, and `V2-P4-005` added a twenty-second (`KNOWN_RANKING_LIMITATIONS`, seven
entries) and moved them from 204 / 69. `V2-P4-006` and `V2-P4-023` then arrived together in one
parallel wave, adding the twenty-third and twenty-fourth (`KNOWN_SCREENING_LIMITATIONS` and
`KNOWN_SHORTLIST_GATE_LIMITATIONS`, seven entries each) and moving them from 211 / 69 to
225 / 69. That pair is worth reading twice: neither knew about the other, **each wrote
`== 23` and `>= 218`**, and because the two edits were textually identical git merged them
without a conflict -- so the arithmetic below said twenty-three when twenty-four registries
were present, and only this module's own assertion, computed off the live table, said so. The
counts had already drifted twice before the floor existed:
`128 entries -- 59 in KNOWN_PANEL_LIMITATIONS` was still written here
when the counts were 134 and 62, because the P2 merge that folded in an eleventh registry updated
the arithmetic below and left the prose alone. That is the drift this module exists to stop,
arriving in this module, which is why the number is now executable rather than quoted.

## The binding that is installed instead

**Every declared `code` must appear as a string literal in executable test code.** Executable
is the load-bearing word and it is checked structurally: the literal must sit somewhere other
than a docstring, in a file matching `test_*.py`. That is exactly the line the P2 finding fell
on -- the renamed code *was* in the repository twice, in prose -- and it is a line an AST can
draw and a reviewer cannot forget to.

It is weaker than `007`'s and it is weaker in a way worth naming: it does not require the
reference to be a proof of the limitation, only that the registry and the suite agree on what
the limitation is *called*. What it does buy is the whole registry rather than three entries of
it. Every rename, every deletion and every addition now fails something, in every one of the
registries, without anybody remembering to install anything -- including in a registry that
does not exist yet, because `test_the_registry_table_is_every_known_registry_in_the_source_tree`
reads the source tree rather than this list. `007`'s stronger binding stays where it is; this
is the floor under all of them, not a replacement for it.

In practice the reference each registry now carries is a set literal of all of its codes,
compared for **equality** -- the form `KNOWN_ADJUSTMENT_LIMITATIONS` has had since `V2-P1-005`.
Equality rather than membership because a membership assertion is additive: it can see a code
that was renamed and never a code that was removed.

One phrase in this module's own docstring is a sentinel that
`test_prose_does_not_satisfy_the_binding_and_this_is_how_that_is_known` looks for --
`only_named_in_prose_and_therefore_not_bound` -- and the audit is required *not* to count it.
It is the extractor's own test: if the docstring filter ever broke, every prose mention in the
repository would start satisfying the rule and this module would go green while proving
nothing.

## What that weakness cost, once, and why nothing cheap closes it

`V2-P4-092` is the first consequence and it is worth reading rather than summarising. Two
entries of `KNOWN_BASELINE_LIMITATIONS` stood side by side and **contradicted each other** --
one said a leaked and a purged fold "both read exactly `-1.0`", the next said the mean rank ICs
of "`+1.0` and `-1.0`" were the numbers "that separate a leaked fold from a purged one" -- and
the second was false, measured `-1.0` in all four configurations of both corpora. Every
assertion in this module was green throughout, correctly: both `code`s appear in executable
test code, which is all this binding claims. **A binding on names cannot see a false sentence.**

The cheapest structural candidate was tried and measured before being declined: *every decimal
in a `detail` must be a number the suite evaluates*. It fails in both directions at once. The
false entry's two decimals are `-1.0` and `1.0`, and the suite evaluates both -- so the rule
would have been **satisfied** by exactly the sentence it was invented for. And of the 61 entries
whose `detail` carries a decimal, **38** name at least one number no test evaluates, because
most of them are measurements recorded in prose (`0.14%` of an ordinary session, `3/7` against
`3/8`, `795.78` of turnover) that no assertion has a reason to restate. Nothing catches the
defect; 38 alarms is what it costs.

So the answer is the honest one: this binding is about names, the `detail` beside a name is
prose, and the only thing that makes a `detail` true is somebody driving what it says.
`V2-P4-016` and `V2-P4-092` both did that by **rewriting** the entry rather than appending
around it, and the second left a test behind
(`test_no_configuration_of_either_corpus_lets_a_rank_ic_separate_a_leak_from_a_purge`) so the
sentence now has something under it. That is a per-entry discipline and not a mechanism, and
saying so here is the point -- a reader who meets this module should not leave it believing the
audit checks what an entry claims.
"""

from __future__ import annotations

import ast
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Final, Protocol

from openalpha_cn.backtest.alpha_baseline import KNOWN_BASELINE_LIMITATIONS
from openalpha_cn.backtest.alpha_tree import KNOWN_TREE_LIMITATIONS
from openalpha_cn.backtest.candidate_ranking import KNOWN_RANKING_LIMITATIONS
from openalpha_cn.backtest.cross_section import KNOWN_CROSS_SECTION_LIMITATIONS
from openalpha_cn.backtest.execution import KNOWN_EXECUTION_LIMITATIONS
from openalpha_cn.backtest.factor_experiment import KNOWN_EXPERIMENT_LIMITATIONS
from openalpha_cn.backtest.factor_ic import KNOWN_IC_LIMITATIONS
from openalpha_cn.backtest.factor_portfolio import KNOWN_QUANTILE_PORTFOLIO_LIMITATIONS
from openalpha_cn.backtest.factor_redundancy import KNOWN_REDUNDANCY_LIMITATIONS
from openalpha_cn.backtest.factor_tradeability import KNOWN_TRADEABILITY_LIMITATIONS
from openalpha_cn.backtest.shortlist_gate import KNOWN_SHORTLIST_GATE_LIMITATIONS
from openalpha_cn.backtest.walk_forward import KNOWN_WALK_FORWARD_LIMITATIONS
from openalpha_cn.domain.adjustment import KNOWN_ADJUSTMENT_LIMITATIONS
from openalpha_cn.domain.alpha_model import KNOWN_ALPHA_MODEL_LIMITATIONS
from openalpha_cn.domain.daily_prices import KNOWN_PRICE_LIMITATIONS
from openalpha_cn.domain.factor import KNOWN_FACTOR_SEAL_LIMITATIONS
from openalpha_cn.domain.factor_neutralization import KNOWN_NEUTRALIZATION_LIMITATIONS
from openalpha_cn.domain.financial_statements import KNOWN_FINANCIAL_STATEMENT_LIMITATIONS
from openalpha_cn.domain.index_membership import KNOWN_INDEX_MEMBERSHIP_LIMITATIONS
from openalpha_cn.domain.index_prices import KNOWN_INDEX_PRICE_LIMITATIONS
from openalpha_cn.domain.industry_classification import KNOWN_INDUSTRY_LIMITATIONS
from openalpha_cn.domain.labels import KNOWN_LABEL_LIMITATIONS
from openalpha_cn.domain.prediction_record import KNOWN_PREDICTION_RECORD_LIMITATIONS
from openalpha_cn.domain.price_limits import KNOWN_SUSPENSION_LIMITATIONS
from openalpha_cn.domain.stock_universe import KNOWN_UNIVERSE_LIMITATIONS
from openalpha_cn.factor_view import KNOWN_FACTOR_RUN_LIMITATIONS
from openalpha_cn.feature_matrix import KNOWN_FEATURE_MATRIX_LIMITATIONS
from openalpha_cn.model_view import KNOWN_MODEL_VIEW_LIMITATIONS
from openalpha_cn.panel.catalog import KNOWN_STORAGE_LIMITATIONS
from openalpha_cn.panel_doctor import KNOWN_PANEL_LIMITATIONS
from openalpha_cn.product.screening import KNOWN_SCREENING_LIMITATIONS
from openalpha_cn.runtime.router import KNOWN_ROUTING_LIMITATIONS
from openalpha_cn.shortlist_compare import KNOWN_COMPARISON_LIMITATIONS
from openalpha_cn.shortlist_view import KNOWN_SHORTLIST_VIEW_LIMITATIONS

ROOT: Final[Path] = Path(__file__).resolve().parents[2]
SOURCE_ROOT: Final[Path] = ROOT / "src" / "openalpha_cn"
TEST_ROOT: Final[Path] = ROOT / "tests"

PROSE_SENTINEL: Final[str] = "only_named_in_prose" + "_and_therefore_not_bound"
"""A string this module names in full **only** inside docstrings. See the module docstring.

Spliced from two halves rather than written out, because writing it out would put it in an
executable position in this very file and make the sentinel satisfy the rule it exists to prove
is unsatisfiable by prose. Neither half contains the whole, so the containment check below
holds for the same reason the equality one does.
"""


class _Limitation(Protocol):
    @property
    def code(self) -> str: ...


LIMITATION_REGISTRIES: Final[dict[str, Sequence[_Limitation]]] = {
    "KNOWN_EXECUTION_LIMITATIONS": KNOWN_EXECUTION_LIMITATIONS,
    "KNOWN_IC_LIMITATIONS": KNOWN_IC_LIMITATIONS,
    "KNOWN_QUANTILE_PORTFOLIO_LIMITATIONS": KNOWN_QUANTILE_PORTFOLIO_LIMITATIONS,
    "KNOWN_EXPERIMENT_LIMITATIONS": KNOWN_EXPERIMENT_LIMITATIONS,
    "KNOWN_FACTOR_RUN_LIMITATIONS": KNOWN_FACTOR_RUN_LIMITATIONS,
    "KNOWN_REDUNDANCY_LIMITATIONS": KNOWN_REDUNDANCY_LIMITATIONS,
    "KNOWN_TRADEABILITY_LIMITATIONS": KNOWN_TRADEABILITY_LIMITATIONS,
    "KNOWN_CROSS_SECTION_LIMITATIONS": KNOWN_CROSS_SECTION_LIMITATIONS,
    "KNOWN_RANKING_LIMITATIONS": KNOWN_RANKING_LIMITATIONS,
    "KNOWN_SHORTLIST_GATE_LIMITATIONS": KNOWN_SHORTLIST_GATE_LIMITATIONS,
    "KNOWN_WALK_FORWARD_LIMITATIONS": KNOWN_WALK_FORWARD_LIMITATIONS,
    "KNOWN_BASELINE_LIMITATIONS": KNOWN_BASELINE_LIMITATIONS,
    "KNOWN_TREE_LIMITATIONS": KNOWN_TREE_LIMITATIONS,
    "KNOWN_SCREENING_LIMITATIONS": KNOWN_SCREENING_LIMITATIONS,
    "KNOWN_SHORTLIST_VIEW_LIMITATIONS": KNOWN_SHORTLIST_VIEW_LIMITATIONS,
    "KNOWN_ROUTING_LIMITATIONS": KNOWN_ROUTING_LIMITATIONS,
    "KNOWN_COMPARISON_LIMITATIONS": KNOWN_COMPARISON_LIMITATIONS,
    "KNOWN_MODEL_VIEW_LIMITATIONS": KNOWN_MODEL_VIEW_LIMITATIONS,
    "KNOWN_FEATURE_MATRIX_LIMITATIONS": KNOWN_FEATURE_MATRIX_LIMITATIONS,
    "KNOWN_ALPHA_MODEL_LIMITATIONS": KNOWN_ALPHA_MODEL_LIMITATIONS,
    "KNOWN_PREDICTION_RECORD_LIMITATIONS": KNOWN_PREDICTION_RECORD_LIMITATIONS,
    "KNOWN_ADJUSTMENT_LIMITATIONS": KNOWN_ADJUSTMENT_LIMITATIONS,
    "KNOWN_PRICE_LIMITATIONS": KNOWN_PRICE_LIMITATIONS,
    "KNOWN_FINANCIAL_STATEMENT_LIMITATIONS": KNOWN_FINANCIAL_STATEMENT_LIMITATIONS,
    "KNOWN_INDEX_MEMBERSHIP_LIMITATIONS": KNOWN_INDEX_MEMBERSHIP_LIMITATIONS,
    "KNOWN_INDEX_PRICE_LIMITATIONS": KNOWN_INDEX_PRICE_LIMITATIONS,
    "KNOWN_INDUSTRY_LIMITATIONS": KNOWN_INDUSTRY_LIMITATIONS,
    "KNOWN_LABEL_LIMITATIONS": KNOWN_LABEL_LIMITATIONS,
    "KNOWN_NEUTRALIZATION_LIMITATIONS": KNOWN_NEUTRALIZATION_LIMITATIONS,
    "KNOWN_FACTOR_SEAL_LIMITATIONS": KNOWN_FACTOR_SEAL_LIMITATIONS,
    "KNOWN_SUSPENSION_LIMITATIONS": KNOWN_SUSPENSION_LIMITATIONS,
    "KNOWN_UNIVERSE_LIMITATIONS": KNOWN_UNIVERSE_LIMITATIONS,
    "KNOWN_STORAGE_LIMITATIONS": KNOWN_STORAGE_LIMITATIONS,
    "KNOWN_PANEL_LIMITATIONS": KNOWN_PANEL_LIMITATIONS,
}
"""The thirty-four registries whose entries are identified by a `code`, keyed by their own
names.

**None of `KNOWN_NEUTRALIZATION_LIMITATIONS`, `KNOWN_IC_LIMITATIONS`,
`KNOWN_REDUNDANCY_LIMITATIONS`, `KNOWN_QUANTILE_PORTFOLIO_LIMITATIONS`,
`KNOWN_TRADEABILITY_LIMITATIONS`, `KNOWN_CROSS_SECTION_LIMITATIONS`,
`KNOWN_RANKING_LIMITATIONS`, `KNOWN_SHORTLIST_GATE_LIMITATIONS`,
`KNOWN_EXPERIMENT_LIMITATIONS` or `KNOWN_FACTOR_RUN_LIMITATIONS` is folded into
`KNOWN_PANEL_LIMITATIONS`**, unlike the eight
dataset registries below them. `panel_doctor` folds a registry when it bounds a *fetched* dataset,
and all ten of these bound derived planes -- no upstream, no `DATASET_CADENCE` entry and nothing
for a health report to be fresh against. Folding any of them would put entries about a
regression's, a correlation's, a simulated round trip's, a participation cap's, a shortlist's, a
candidate list's, a publication verdict's, a sealed experiment report's or a public face's own
semantics into a report about ingest coverage, and would move
`test_the_registries_together_carry_the_entry_count_the_report_folds`' arithmetic
for registries the report cannot say anything about.

`KNOWN_FACTOR_RUN_LIMITATIONS` (`V2-P3-015`) is the eighteenth and the first to live on a *face*
rather than on a contract. It is here for the same reason as the rest: its five entries are what
`openalpha factor run`, `POST /api/v1/factors/run` and `OpenAlphaSDK.run_factor_experiment` do not
answer, and a code named only in prose is a code the suite has no opinion about.

`KNOWN_CROSS_SECTION_LIMITATIONS` (`V2-P4-004`) is the twenty-first and moved the totals from
197 / 69. Its seven entries bound a *selection* rather than a measurement -- what a shortlist is
not, what the hard filter is worth (0.14% of an ordinary session, measured), and what the
winsorization's clip block does to a top-N cut on each of the three tiers.

`KNOWN_RANKING_LIMITATIONS` (`V2-P4-005`) is the twenty-second and moved the totals from 204 / 69.
Its seven entries bound a *join*: a candidate ranking holds the panel plane's shortlist beside the
evidence plane's conclusions, so its entries say what it inherits without repairing (every caveat
on the funnel's order), what it cannot yet carry (a model prediction), what its 因子暴露 column is
and is not (a characteristic, never a fitted loading), and why D16's `绝不直接创建组合订单` is four
lint-imports contracts rather than a sentence.

`KNOWN_SCREENING_LIMITATIONS` (`V2-P4-006`) is the twenty-third. Its seven entries bound a
*reading*: a governed screen re-orders completed runs and writes nothing, so its entries say
what the ordering is not (an enforcement -- no ledger and no runtime gate verdict moves) and
what its severity source cannot do.

**Three of the seven were replaced in place by `V2-P4-029`/`V2-P4-030`/`V2-P4-036`, and the
count did not move.** They had recorded an open flag vocabulary: that a misspelling and an
unrecognised flag shared a rung (and that the misspelling therefore *promoted* its candidate),
that the committee half of the severity source had to be read through a synthetic probe because
`DeliberationCommittee.review` raised on an abstaining signal, and that `flag_severity` memoised
a severity it derived by running the gates. `domain/risk_flag.py` closed the vocabulary and the
first and third stopped being true; `V2-P4-029` made the committee total on `SignalFrame` and
the second did. What stands in their place bounds the *fix* rather than the defect: a stored
recovery row carrying a caller-injected flag is now refused rather than migrated, a severity is
a declaration about the vocabulary rather than a measurement of either gate, and the
`unrecognised` rung is kept for the wire although no signal can reach it. That is the shape a
registry entry is supposed to have when the thing it bounded is repaired -- replaced by what
the repair itself costs, not deleted.

`KNOWN_SHORTLIST_GATE_LIMITATIONS` (`V2-P4-023`) is the twenty-fourth. Its seven entries bound a
*verdict*: the third gate between a market and a published candidate list, sitting above
`V2-P4-004`'s per-security filter and below nothing. They say which denominator its coverage bar
divides by and the 3/7-against-3/8 measurement that chose it, that its freshness clock is the one
field the ranking's own identity excludes, that the clock counts calendar days because this leaf
reaches no trading calendar, and -- the one a reader is most likely to need -- that clearing it is
a coverage and age verdict and never a quality one.

Together the two moved the totals from 211 / 69 to 225 / 69.

`KNOWN_TREE_LIMITATIONS` (`V2-P4-015`) is the thirtieth, and it is the first registry whose
first entry is about a **dependency that was not taken**:
`this_is_a_histogram_boosting_of_the_kind_lightgbm_does_and_not_lightgbm` says which algorithm
ships and which research programme does not, and says outright that no accuracy comparison
against LightGBM was run because running one needs the library the decision declined. The other
six bound a second model rather than a first: what a histogram costs in resolution, that neither
baseline dominates the other and which corpus is where this one loses, that a column no split
used is absent rather than reported, that its hyperparameters are declared and nothing selects
them (a second and unaddressed leakage surface beside `V2-P4-013`'s purge), that its score has no
closed-form bound where the rank baseline's has one, and that every number it produced came off a
noiseless synthetic corpus. It moved the totals from 268 / 69 to 275 / 69.

`KNOWN_SHORTLIST_VIEW_LIMITATIONS` (`V2-P4-032` / `V2-P4-033`) is the twenty-fifth, and it is
the first that bounds an **adapter** rather than a study, a verdict or a dataset. Its four
entries say what the join between the panel plane and the two-stage funnel cannot recover:
that the winsorizer's clip block is reconstructed from a tie at the maximum and therefore
over-reports in the safe direction, that the cross section it screens is the newest one
*visible* at the `as_of` and may be older than it, that the evidence plane's answers are
supplied rather than run (this repository stores no `SignalFrame`, so a face that researched
every shortlisted name would make `researched_ratio` unable to be anything but `1.0`), and
that a neutralized-tier screen needs an exposure cross section this face does not load. It
moved the totals from 225 / 69 to 229 / 69.

`KNOWN_MODEL_VIEW_LIMITATIONS` (`V2-P4-021`) is the thirty-second and moved the totals from
285 / 69 to **294 / 69**. It is the second that bounds a *face* and the first that bounds one
whose answer is a **registration** rather than a reading: its nine entries say what a resolved
`feature_version` is not a claim about, why an evaluation stores nothing (every record it could
write would stand `unwitnessed`, so filling Story S32's register with backtests would bury the
`forward` rows), why only the daily half writes a `RunManifest`, that the daily fit purges and
does not embargo and what an embargo would have separated it from, that a prediction day is an
instant's zone date and never its pricing session, that the labels behind every fold are read at
one later `as_of` so the corpus's *shape* is today's even where its values are not, that the
`scored_ratio` floor is a coverage bar and never a quality one, that nothing on this face selects
a hyperparameter -- and that the neutralized tier is refused here by name.

`V2-P4-093` added no registry and one entry, `KNOWN_PREDICTION_RECORD_LIMITATIONS`'
`the_supersedes_edge_is_contract_only_because_no_face_offers_a_record_to_name`, which moved the
totals from 294 / 69 to **295 / 69** with the count of registries unchanged at thirty-two. It
belongs to the same family as `V2-P4-083`'s `load_index_prices`: a guard that cannot fail,
because nothing on any face can reach the input it guards.

The model chain's product acceptance then added **six** to `KNOWN_MODEL_VIEW_LIMITATIONS`, taking
it from nine to fifteen and the totals from 295 / 69 to **301 / 69**, again with the registry
count unchanged. They are worth reading as one group, because four of the six are the same
failure shape in four places: something this repository already knew, written where the user
cannot meet it. `V2-P4-099` found two of them stated one plane down in
`domain/prediction_record.py` and never reaching a body a caller pastes into a report -- an
unreachable `supersedes` and an `unwitnessed` standing no shipped face can produce. `V2-P4-100`
found a third in a `--help` sentence that was false (a retried daily run files a second record)
and a fourth in a docstring that was true and unfindable (`--subject` narrows a factor build and
not the market a model is offered). The remaining two are `V2-P4-097`'s single-column rank
invariance -- which is also the first entry with a *per-answer* companion, `model_view.
evaluation_invariances`, because the boundary is true of the family and the run's own column
count decides whether that run is standing on it -- and `V2-P4-098`'s measured `forward` record
whose fit read the panel after the outcome had printed.

`V2-P4-018` then added **three**, taking the totals from 301 / 69 to **304 / 69** with the
registry count still thirty-two. `V2-P4-008`/`V2-P4-009` added the **thirty-third**
(`KNOWN_ROUTING_LIMITATIONS`, seven entries, declared in `runtime/router.py`) and moved the
totals from 304 / 69 to **311 / 69** -- the first registry to arrive from `runtime/`, and the
first whose subject is a *selection* rather than a dataset or a study. `V2-P4-007` added the
**thirty-fourth** (`KNOWN_COMPARISON_LIMITATIONS`, seven entries, in `shortlist_compare.py`)
and moved them to **318 / 69**; both arrived on one branch, which is why the arithmetic here
moved twice rather than once.

Of `V2-P4-018`'s three, two are on `KNOWN_ALPHA_MODEL_LIMITATIONS` and both bound what a
shelf life is *not* -- it is wall time where a horizon counts sessions, and it leaves a verdict on
a stored record without leaving the bar that produced it. The third is on
`KNOWN_MODEL_VIEW_LIMITATIONS` and is the join between the two flags: an expired run is refused by
`--min-scored-ratio` and by nothing else, so a caller who declared a floor of `0.0` reads an
all-abstaining model as a clean success.

`KNOWN_ALPHA_MODEL_LIMITATIONS` (`V2-P4-011`) is the twenty-sixth, and it is the first that
bounds a **contract for something this repository does not yet build**: the quantitative
`AlphaModel` boundary, whose feature matrix, walk-forward split, baselines, content address and
prediction store are all downstream issues. Its eight entries said which of those it deliberately
did not decide -- that `feature_version` is a name and `V2-P4-012` owns the digest behind it;
that the leakage floor `PredictionBatch` installs (`as_of >= training_cutoff`) is not the purge
or the embargo `V2-P4-013` owns; that "before the outcome is known" needs a calendar and a store
and is `V2-P4-017`'s; that the fitted artifact carried no address at all, because `V2-P4-010`
gave the prefix and the digest field set to `V2-P4-016`; and that the reference model under
`backtest/` is not a baseline. Two more bound what the shape cannot force: nothing routes an
implementation through `artifact_for` or `prediction_batch_for`, and one abstained security can
leave a whole `CandidateRanking` carrying no prediction, because `rank_candidates` enforces
all-or-nothing per ranking while abstention answers per security.

**`V2-P4-016` is the first issue on this chain to find two of a registry's entries false rather
than incomplete**, and it rewrote both instead of appending around them: the artifact now
carries an address, and the count of Implementation Decision 11's fields it holds moved from six
to seven. Three entries were added for what an address does **not** prove -- that it identifies
what a fit consumed and not which rule chose those rows, that the seed inside it is read by no
model in this build (`V2-P0B-009`'s F87 one plane down, measured: two seeds, byte-identical
coefficients, two addresses), and that `UNKNOWN_CODE_COMMIT` is one constant shared by every
build with no git and no stamp -- plus one for what that issue deliberately did not narrow, the
manifest slot that still admits a `fct_` address where an `mdl_` belongs. Eleven entries; the
totals moved from 275 / 69 to **278 / 69**.

**The arithmetic above was 5 behind before this entry arrived**, and the module that exists to
stop that says so rather than quietly correcting it: at `c2c8e36` the prose and the floor both
read 229 while the live total measured 234. Whatever added those five did not re-measure, which
is the drift this file's own executable assertions -- not its prose -- are what caught. The
figures here are now the measured 247 / 69 and the floor below moves with them.

`KNOWN_FEATURE_MATRIX_LIMITATIONS` (`V2-P4-012`) is the twenty-seventh, and it is the answer to
one of the twenty-sixth's entries: `the_feature_version_is_a_name_this_contract_cannot_check`
said `V2-P4-012` owned the digest behind that name. Its five entries bound the plane that now
computes it -- what the two versions do **not** address (neither is a digest over the stored
values, and the third address that would be is `V2-P4-016`'s), what the transform's own
`imputed` code costs a matrix that will not stack two imputations, what a cross-sectional median
is measured over, what one unbuilt column does to a whole instant, and the gap between the
registry's *listed* set and a tradeable one. It moved the totals from 242 / 69 to 247 / 69.

`KNOWN_BASELINE_LIMITATIONS` (`V2-P4-014`) is the twenty-ninth and moved the totals from
258 / 69. Its ten entries bound a **fit and the numbers that judge one** -- the first registry
here that does both. Four say what the model is: a score that is a position inside one cross
section rather than a property of a security, coefficients that are marginal and therefore count
two redundant columns twice, a rank output that forecasts no return and carries no units, and a
tie it can see against the neutralised tier's tie it cannot. Three say what the numbers are not:
D13's *"新模型必须战胜基线"* is computed by nothing here, an evaluation's `predicted_at` is the
instant it simulates and proves nothing about when, and every figure so far was measured on a
leak fixture with no noise model. The last three are corrections rather than caveats -- that a
`backtest/` study cannot call `require_declared_features` however twice `feature_matrix.py` says
this issue would, that a minority leak moves this baseline's coefficient and not its ordering
(the opposite of what the issue expected before it measured), and that the two abstention
sentences are not yet Story S35's vocabulary.

Imported and named rather than reached through `importlib`: a test may import every one of
these directly, so a name-to-module indirection would buy nothing and would hide from the
import graph the very dependency this module is asserting about.
"""

CODELESS_REGISTRIES: Final[tuple[str, ...]] = ("KNOWN_CALENDAR_LOOKAHEAD",)
"""The one registry with no `code`, bound by its dates instead.

`KNOWN_CALENDAR_LOOKAHEAD`'s three entries are three reproductions of **one** defect rather
than three defects -- `panel_doctor._limitations()` says so by folding all three into the
single `the_published_schedule_can_be_amended_after_it_becomes_answerable` entry -- so its
natural key is `calendar_date`, and
`tests/unit/domain/test_trading_calendar.py::test_the_known_lookahead_registry_carries_its_measured_widths`
already pins all three as an exact dict. Listed here rather than left out so that "it has no
code" is a recorded judgement instead of an omission, and so
`test_the_registry_table_is_every_known_registry_in_the_source_tree` still accounts for it.
"""


def declared_codes() -> dict[str, tuple[str, ...]]:
    """Every registry's codes, keyed by the registry's own name."""
    return {
        name: tuple(item.code for item in registry)
        for name, registry in LIMITATION_REGISTRIES.items()
    }


def _docstring_constants(tree: ast.AST) -> set[int]:
    """The `id()` of every string constant that is a docstring or an attribute docstring.

    A docstring is a bare string expression **statement**, whether it opens a module, a class
    or a function, or trails a dataclass field -- which is the form every registry entry's own
    prose takes. Identified by position in a body rather than by content, so a sentence that
    happens to equal a code is still prose.
    """
    found: set[int] = set()
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if not isinstance(body, list):
            continue
        for statement in body:
            if (
                isinstance(statement, ast.Expr)
                and isinstance(statement.value, ast.Constant)
                and isinstance(statement.value.value, str)
            ):
                found.add(id(statement.value))
    return found


def _test_modules() -> Iterator[Path]:
    return (path for path in sorted(TEST_ROOT.rglob("test_*.py")))


def executable_string_literals() -> set[str]:
    """Every string literal the test suite *evaluates*, docstrings excluded.

    Restricted to `test_*.py`: a mention inside `tests/panel_fixtures.py`'s `measurement=`
    prose is an evaluated literal too, and it is still prose -- it describes a fixture rather
    than asserting anything about the registry.
    """
    literals: set[str] = set()
    for path in _test_modules():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        docstrings = _docstring_constants(tree)
        literals.update(
            node.value
            for node in ast.walk(tree)
            if isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and id(node) not in docstrings
        )
    return literals


def docstring_string_literals() -> set[str]:
    """The complement of the above: every literal the suite only *documents*."""
    prose: set[str] = set()
    for path in _test_modules():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        docstrings = _docstring_constants(tree)
        prose.update(
            node.value
            for node in ast.walk(tree)
            if isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and id(node) in docstrings
        )
    return prose


def _module_level_known_names(path: Path) -> Iterator[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in tree.body:
        targets: list[ast.expr] = []
        if isinstance(node, ast.Assign):
            targets = list(node.targets)
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
        for target in targets:
            if isinstance(target, ast.Name) and target.id.startswith("KNOWN_"):
                yield target.id


def test_the_registry_table_is_every_known_registry_in_the_source_tree() -> None:
    """The direction a hand-written list cannot cover: a *twenty-second* registry.

    `V2-P2-007`'s binding is installed per module, so a new `KNOWN_*` tuple arrives with no
    binding and nothing notices -- which is how eleven of them got here. The table above is
    therefore checked against the source tree rather than trusted, by AST rather than by
    import, so a registry that is declared and never referenced still counts.
    """
    found = {
        name
        for path in sorted(SOURCE_ROOT.rglob("*.py"))
        for name in _module_level_known_names(path)
    }

    assert found == set(LIMITATION_REGISTRIES) | set(CODELESS_REGISTRIES)
    assert len(LIMITATION_REGISTRIES) == 34
    assert set(LIMITATION_REGISTRIES) & set(CODELESS_REGISTRIES) == set()


def test_every_declared_limitation_code_is_named_in_executable_test_code() -> None:
    """The binding itself, over all thirty-four code-carrying registries and all their entries.

    A code that appears nowhere but in prose is a code the suite has no opinion about: rename
    it and everything stays green while every citation of it silently stops resolving. That is
    not a hypothesis -- it is what the P2 review measured on
    `a_listed_only_registry_is_invisible_to_every_downstream_check`, whose only two occurrences
    were both docstrings.

    The failure names the registry and the code rather than a count, because "127 of 128" tells
    a reader nothing about which sentence stopped being checked.
    """
    literals = executable_string_literals()

    unbound = {
        registry: tuple(code for code in codes if code not in literals)
        for registry, codes in declared_codes().items()
    }

    assert unbound == {registry: () for registry in unbound}, (
        "a declared limitation whose code the test suite never evaluates is prose; give it a "
        "set-literal assertion in its own module's tests"
    )


def test_prose_does_not_satisfy_the_binding_and_this_is_how_that_is_known() -> None:
    """The extractor's own test, without which the audit above could pass vacuously.

    If `_docstring_constants` ever stopped recognising docstrings, every prose mention in the
    suite would start counting as a binding and
    `test_every_declared_limitation_code_is_named_in_executable_test_code` would go green while
    checking nothing. The sentinel is a string this module names in its docstrings and nowhere
    else, so it has to land on the prose side of the split and never on the executable one.

    Containment rather than equality on the prose side, because the sentinel is a phrase inside
    a docstring and not a docstring of its own; equality *and* containment on the executable
    side, which is the direction that matters -- the audit above tests membership, so an
    exact-match escape would be enough to fool it, and containment rules out the rest.
    """
    assert any(PROSE_SENTINEL in text for text in docstring_string_literals())
    executable = executable_string_literals()
    assert PROSE_SENTINEL not in executable
    assert not any(PROSE_SENTINEL in text for text in executable)


DERIVED_REGISTRY: Final[str] = "KNOWN_PANEL_LIMITATIONS"
"""The one registry whose codes are deliberately **not** unique.

`panel_doctor._limitations()` folds seven registries into one tuple and four of them record
`silent_truncation_at_the_response_cap`, because the same defect really does recur at four
different caps. Its identity is therefore `(code, datasets)`, which
`tests/unit/test_panel_doctor_rules.py::test_a_limitation_is_identified_by_its_code_and_the_datasets_it_speaks_for`
asserts. Uniqueness below is a claim about the thirty registries a code is written into by hand.
"""

CODES_THAT_RECUR_ACROSS_REGISTRIES: Final[dict[str, frozenset[str]]] = {
    "silent_truncation_at_the_response_cap": frozenset(
        {
            "KNOWN_ADJUSTMENT_LIMITATIONS",
            "KNOWN_PRICE_LIMITATIONS",
            "KNOWN_INDEX_MEMBERSHIP_LIMITATIONS",
            "KNOWN_INDUSTRY_LIMITATIONS",
        }
    ),
    "no_revision_history": frozenset(
        {"KNOWN_ADJUSTMENT_LIMITATIONS", "KNOWN_INDEX_MEMBERSHIP_LIMITATIONS"}
    ),
    "a_neutralised_series_is_only_as_point_in_time_as_its_build_schedule": frozenset(
        {
            "KNOWN_IC_LIMITATIONS",
            "KNOWN_QUANTILE_PORTFOLIO_LIMITATIONS",
            "KNOWN_EXPERIMENT_LIMITATIONS",
        }
    ),
}
"""The three codes that are written by hand into more than one registry, and where.

Per-registry uniqueness has been asserted since `V2-P2-007`'s binding was widened; **across**
registries nothing was asserted at all, and `V2-P4-038` is what that cost. The binding one
section up tests membership in a set of every string literal the whole suite evaluates, so a
code carried by registry A is "bound" by a literal somebody wrote about registry B -- and a
foreign code is therefore the cheapest possible filler for a hole. Measured on `146698c`:
adding `the_cut_is_broken_by_subject_code_when_two_scores_tie`,
`KNOWN_CROSS_SECTION_LIMITATIONS`' own code, as a tenth `KNOWN_INDEX_MEMBERSHIP_LIMITATIONS`
entry left `tests/unit` at 2816 passed and the seven integration and contract modules that
touch a limitation registry at 233 passed.

So this is a table rather than a bare "no code appears twice", because global uniqueness is
**false today and rightly so**, in three places and for three different reasons:

- `silent_truncation_at_the_response_cap` names one defect that really does recur at four
  different response caps, which is the reason `DERIVED_REGISTRY` gives for
  `KNOWN_PANEL_LIMITATIONS` keying on `(code, datasets)` rather than on `code`.
- `no_revision_history` is the same sentence about two endpoints that each serve one snapshot
  per request and carry no revision instant.
- `a_neutralised_series_is_only_as_point_in_time_as_its_build_schedule` is one boundary of the
  neutralised tier restated on each of the three studies that read it, and it is the entry
  `V2-P4-026` **renamed** rather than appended around -- so all three had to move together and
  a table that pins where it lives is what makes the next such rename visible.

The value is the exact set of registries, not a count, so a recurrence that *moves* to a
different registry is as red as one that appears. `KNOWN_PANEL_LIMITATIONS` is excluded on
`DERIVED_REGISTRY`'s terms: 68 of its 69 codes are somebody else's by construction, so every
one of them would appear here and the table would say nothing.
"""

REGISTRY_ENTRY_COUNTS: Final[dict[str, int]] = {
    "KNOWN_EXECUTION_LIMITATIONS": 3,
    "KNOWN_IC_LIMITATIONS": 5,
    "KNOWN_QUANTILE_PORTFOLIO_LIMITATIONS": 8,
    "KNOWN_EXPERIMENT_LIMITATIONS": 6,
    "KNOWN_FACTOR_RUN_LIMITATIONS": 8,
    "KNOWN_REDUNDANCY_LIMITATIONS": 6,
    "KNOWN_TRADEABILITY_LIMITATIONS": 11,
    "KNOWN_CROSS_SECTION_LIMITATIONS": 7,
    "KNOWN_RANKING_LIMITATIONS": 7,
    "KNOWN_SHORTLIST_GATE_LIMITATIONS": 7,
    "KNOWN_WALK_FORWARD_LIMITATIONS": 11,
    "KNOWN_BASELINE_LIMITATIONS": 10,
    "KNOWN_TREE_LIMITATIONS": 7,
    "KNOWN_SCREENING_LIMITATIONS": 7,
    "KNOWN_SHORTLIST_VIEW_LIMITATIONS": 8,
    "KNOWN_ROUTING_LIMITATIONS": 7,
    "KNOWN_COMPARISON_LIMITATIONS": 7,
    "KNOWN_MODEL_VIEW_LIMITATIONS": 16,
    "KNOWN_FEATURE_MATRIX_LIMITATIONS": 5,
    "KNOWN_ALPHA_MODEL_LIMITATIONS": 13,
    "KNOWN_PREDICTION_RECORD_LIMITATIONS": 8,
    "KNOWN_ADJUSTMENT_LIMITATIONS": 6,
    "KNOWN_PRICE_LIMITATIONS": 6,
    "KNOWN_FINANCIAL_STATEMENT_LIMITATIONS": 12,
    "KNOWN_INDEX_MEMBERSHIP_LIMITATIONS": 9,
    "KNOWN_INDEX_PRICE_LIMITATIONS": 4,
    "KNOWN_INDUSTRY_LIMITATIONS": 10,
    "KNOWN_LABEL_LIMITATIONS": 8,
    "KNOWN_NEUTRALIZATION_LIMITATIONS": 5,
    "KNOWN_FACTOR_SEAL_LIMITATIONS": 3,
    "KNOWN_SUSPENSION_LIMITATIONS": 9,
    "KNOWN_UNIVERSE_LIMITATIONS": 7,
    "KNOWN_STORAGE_LIMITATIONS": 6,
}
"""How many entries each hand-written registry carries -- an equality, one registry per line.

`V2-P4-038`'s first half. What stood here was `sum(...) >= 301`, and a floor is satisfied by
**any** non-negative net change: a registry that loses a real limitation while anything else
gains one is green, and so is a registry that gains a foreign code and loses nothing. Both were
measured rather than argued.

Per registry rather than as one total, and that is the whole design choice. A single scalar
catches only the *net*, so a deletion in one registry masked by two additions in another passes
it; this fails naming the registry whose count moved. It is also the shape that survives the
merge a scalar did not: this module's own docstring records `V2-P4-006` and `V2-P4-023` each
writing `>= 218` in parallel, git merging two textually identical edits without a conflict, and
the arithmetic being wrong afterwards with nothing to say so. Two siblings adding to two
different registries edit two different lines here and git merges both correctly; two adding to
the *same* registry conflict on one line, which is the right outcome because only a person can
say whether the two additions are the same entry.

**It is red on every merge that adds an entry, and that is the cost being paid on purpose.**
The direction it does not cover is stated rather than left to be discovered: an entry replaced
by a differently-named entry inside one registry leaves the count alone. Nothing here can see
that, for `V2-P4-092`'s reason one section down -- a binding on names and counts cannot see what
an entry claims. What does see it is the registry's own module naming all of its codes at once:
measured by AST, 32 of the 34 have a literal collection somewhere under `tests/` whose members
are exactly that registry's code set, and `KNOWN_INDEX_MEMBERSHIP_LIMITATIONS` and the derived
`KNOWN_PANEL_LIMITATIONS` are the two that do not -- which is why the measured probe was built
on the first of them.

`KNOWN_PANEL_LIMITATIONS` is deliberately absent. Its count is not an independent fact: the
assertion above already pins it at `folded + plane_wide + 1`, so writing 69 here would be a
second hand-written number derived from the first, and a sibling adding to a folded dataset
registry would have to update both or make them disagree. The measured totals this module's
docstring quotes -- 318 across the thirty-four registries, 69 in the derived one -- are the sum
of the thirty-one entries here plus that derived equality, and are not written down a second
time as a scalar anybody can bump without re-measuring.
"""


def test_every_registry_entry_is_uniquely_identified_within_its_own_registry() -> None:
    """A duplicated code makes a registry's set literal smaller than the registry itself, so
    one of the two entries could be deleted without changing what the literal asserts."""
    counts = {
        registry: codes
        for registry, codes in declared_codes().items()
        if registry != DERIVED_REGISTRY
    }

    assert {registry: len(set(codes)) for registry, codes in counts.items()} == {
        registry: len(codes) for registry, codes in counts.items()
    }
    assert DERIVED_REGISTRY in declared_codes()


def test_the_registries_together_carry_the_entry_count_the_report_folds() -> None:
    """The per-registry equality under the volume argument in this module's docstring.

    Every assertion above is per-entry and would be satisfied by a registry of length zero.
    `KNOWN_PANEL_LIMITATIONS` is a fold of two kinds of source, and they are counted apart
    because they enter `_limitations()` differently: **eight** dataset registries fold in
    one-for-one against the datasets they bound (`V2-P3-016`'s `KNOWN_INDEX_PRICE_LIMITATIONS`
    is the eighth), `KNOWN_STORAGE_LIMITATIONS` folds in with an
    empty `datasets` because a storage boundary holds for every dataset at once, and one
    calendar code is the report's own -- `KNOWN_CALENDAR_LOOKAHEAD` is three reproductions of a
    single defect and is folded to one entry rather than three."""
    codes = declared_codes()
    folded = sum(
        len(codes[registry])
        for registry in (
            "KNOWN_UNIVERSE_LIMITATIONS",
            "KNOWN_ADJUSTMENT_LIMITATIONS",
            "KNOWN_PRICE_LIMITATIONS",
            "KNOWN_SUSPENSION_LIMITATIONS",
            "KNOWN_INDEX_MEMBERSHIP_LIMITATIONS",
            "KNOWN_INDEX_PRICE_LIMITATIONS",
            "KNOWN_INDUSTRY_LIMITATIONS",
            "KNOWN_FINANCIAL_STATEMENT_LIMITATIONS",
        )
    )
    plane_wide = len(codes["KNOWN_STORAGE_LIMITATIONS"])

    assert len(codes["KNOWN_PANEL_LIMITATIONS"]) == folded + plane_wide + 1
    assert all(codes[registry] for registry in codes)
    assert {
        registry: len(entries)
        for registry, entries in codes.items()
        if registry != DERIVED_REGISTRY
    } == REGISTRY_ENTRY_COUNTS


def test_no_code_recurs_across_two_registries_except_where_it_is_declared_to() -> None:
    """`V2-P4-038`'s second half: per-registry uniqueness was asserted, global uniqueness was not.

    Equality against `CODES_THAT_RECUR_ACROSS_REGISTRIES` rather than a bare "no code twice",
    because three codes genuinely recur and each has a reason recorded beside it. The value is the
    exact set of registries, so a recurrence that moves is as red as one that appears -- and a
    foreign code arriving in a registry it does not belong to, which is the measured probe, is a
    key the table does not have.
    """
    codes = declared_codes()
    homes: dict[str, set[str]] = {}
    for registry, entries in codes.items():
        if registry == DERIVED_REGISTRY:
            continue
        for code in entries:
            homes.setdefault(code, set()).add(registry)

    assert {
        code: frozenset(registries) for code, registries in homes.items() if len(registries) > 1
    } == CODES_THAT_RECUR_ACROSS_REGISTRIES
    assert set(CODES_THAT_RECUR_ACROSS_REGISTRIES) <= set(homes)
    assert all(len(registries) > 1 for registries in CODES_THAT_RECUR_ACROSS_REGISTRIES.values())
