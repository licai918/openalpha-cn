"""The agent report (`V2-P7-014`, decision D8): C's one reading, from stored records only.

Plan C registers one forward family over Step 0's survivor census, and this is the command the
registration binds to *read* it. It is deliberately a **separate script** from Step 0's weekly
report (D8): Step 0's report is never modified, and C's reading is printed by the code C's
registration names, so a report that produced a different number is not a hot patch but a new
registration.

## It measures stored records, and nothing else

The report never runs an agent and never calls a model (plan C task 3.5's review rule): the
evaluator it calls reads the two prediction stores through their record-store Protocol and the
labels through `model_view.OutcomeLabels`' structural Protocol. Step 0's runtime is opened
**read-only** -- its prediction store, its panel and its day journals are read, never written.
The run therefore answers the registered question from what was filed before the outcome:
`strategy_agent_evaluation.evaluate` applies the registered protocol, the registered window anchor
and the registered Step 0 configuration, and this script only states what it answered.

## What it prints, in the order a reader needs it

- **Candidate wording first.** `候选 / candidate`, not traded, and a passing run is not evidence.
- The registration's identity, the registered window (anchor, through, as-of).
- The records **witnessed** and the records **missing**, the latter with the evaluator's own
  reason per session, plus the sample size `n`, the power (the mean detectable at 0.8 from the
  window's measured dispersion -- a property of the design, never observed power) and the
  registration's D1 power verdict.
- The **abstention rate**, read from the stored LLM primary-set records: an abstention is a coded
  absence and never a `0.0` score (D3).
- **Model-alias changes**: the distinct `(response.model, system_fingerprint)` identities found in
  C's evening journals, whether they changed across sessions, and which expected journals are
  missing.
- The **primary arm**: before the registered window matures, counts and a descriptive mean with no
  test; once it has matured, the once-made sign-flip test (mean, two-sided and one-sided
  readings, level, exact/randomized) and one of the five fixed outcomes. `not_measurable` prints
  exactly the evaluator's wording and never "no evidence".
- The **exploratory block**, every arm labelled `EXPLORATORY`, its mean and its Benjamini-Yekutieli
  q-value read as "below q" / "at or above q" against the registered `q`, never pooled with the
  primary arm and never stated as a conclusion.
- The adopted D5 rules the evaluation applied (futility form and one-sided reading).

`--json PATH` writes the same facts as one document, atomically (temporary file plus
`os.replace`); without it the command prints and writes nothing.

## What it refuses by name (exit 2)

A runtime marked as a D7 dry-week scratch is refused **before anything is opened** -- its evenings
filed no prediction and are never part of C's forward sample. So are an uncommitted registration,
a configuration that is not `agent_forward/v1`, a Step 0 registration whose recorded
`registration_sha256` / `config_id` / `seed` differ from what C's registration froze, and any
evaluator refusal. A report that cannot state the facts refuses rather than printing a partial
one.

Wording is candidate throughout: engineering completion is not research validity, and a report
that prints a passing test is not evidence that the family has an increment.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Final

_SCRIPTS: Final[Path] = Path(__file__).resolve().parent
for _root in (_SCRIPTS, _SCRIPTS / "research"):
    if str(_root) not in sys.path:
        sys.path.insert(0, str(_root))

import registry  # noqa: E402

from openalpha_cn.domain.agent_forward import AGENT_LLM_FAMILY  # noqa: E402
from openalpha_cn.model_view import LabelReach, OutcomeLabels  # noqa: E402
from openalpha_cn.panel.store import PanelStore  # noqa: E402
from openalpha_cn.runtime.composition import (  # noqa: E402
    agent_journal_dir_at,
    prediction_store_at,
)
from openalpha_cn.strategy_agent_evaluation import (  # noqa: E402
    NOT_MEASURABLE_WORDING,
    AgentEvaluation,
    AgentEvaluationError,
    AgentForwardAnchor,
    AgentForwardPower,
    AgentForwardStep0,
    AgentProtocol,
    evaluate,
    read_agent_forward_anchor,
    read_agent_forward_power,
    read_agent_forward_step0,
    read_agent_protocol,
    refuse_a_dry_week_runtime,
)
from openalpha_cn.strategy_agent_forward import AGENT_SCRIPTS  # noqa: E402
from openalpha_cn.strategy_registration import SHANGHAI, RegisteredConfiguration  # noqa: E402
from openalpha_cn.strategy_survivor import (  # noqa: E402
    SurvivorRegistrationError,
    read_survivor_registration,
)

REPOSITORY: Final[Path] = _SCRIPTS.parent
"""The checkout this script lives in: the default repository and registration base."""
REGISTRATION_IN_REPOSITORY: Final[str] = "docs/research/p7-c-registration.json"
"""C's committed registration (plan C batch 3); a relative `--registration` resolves here."""
REPORT_SCHEMA: Final[str] = "openalpha-agent-report/v1"
"""The `--json` document's schema: the facts the printed report states, as one object."""
AGENT_REGISTRATION_KIND: Final[str] = "agent_forward/v1"
"""What C's registered configuration declares itself; the report reads no other kind."""
AGENT_JOURNAL_SCHEMA: Final[str] = "openalpha-agent-evening/v1"
"""The evening journal's schema: the one document the model-alias block is read from."""
CANDIDATE: Final[str] = "候选"
NOT_TRADED: Final[str] = "not traded"

OUTCOME_WORDINGS: Final[Mapping[str, str]] = {
    "not_yet": (
        "not_yet: the registered window has not filled, or a position's label window has not "
        "matured; only counts and a descriptive mean are shown, and the one registered test is "
        "not made until every position has matured"
    ),
    "candidate_increment": (
        "candidate_increment: 候选 / candidate, not evidence -- the registered one-sided reading "
        "passed at the registered level over the complete window; a passing reading over stored "
        "records is a candidate, never a proven increment"
    ),
    "no_increment_detected": (
        "no_increment_detected: the registered one-sided reading did not pass at the registered "
        "level over the complete window; that is the test's answer and not a claim that the "
        "family has no value"
    ),
    "futility_stop": (
        "futility_stop: the adopted D5 futility stop at forward session 60 -- a cumulative "
        "interim mean at or below zero -- ended the trial; it is a design stop, not a negative "
        "result"
    ),
    "not_measurable": NOT_MEASURABLE_WORDING,
}
"""The five fixed outcome wordings; `not_measurable` is exactly the evaluator's, D3/D5."""


class AgentReportError(ValueError):
    """The report cannot state its facts as asked; the command prints this and exits 2."""


@dataclass(frozen=True, slots=True, kw_only=True)
class ReportRegistration:
    """C's admitted registration: the identity every printed line and the evaluation carry."""

    config_id: str
    registration_sha256: str
    registration_commit: str
    code_commit: str
    settings: Mapping[str, Any]


def _seed_of(settings: object) -> int:
    """The registration's `settings.random_seed`, read as `Registration.seed` reads it."""
    if not isinstance(settings, Mapping):
        return 0
    seed = settings.get("random_seed", 0)
    return seed if isinstance(seed, int) and not isinstance(seed, bool) else 0


def _instant(named: str | None, clock: Callable[[], datetime]) -> datetime:
    """`--as-of` as an aware instant, or the wall clock; a naive instant is Shanghai's."""
    if named is None:
        point = clock()
    else:
        try:
            point = datetime.fromisoformat(named)
        except ValueError as error:
            raise AgentReportError(f"--as-of {named!r} is not ISO 8601 ({error})") from error
    return point if point.tzinfo is not None else point.replace(tzinfo=SHANGHAI)


def _through(named: str | None, as_of: datetime) -> date:
    """`--through` as a date, or the as-of instant's Shanghai date."""
    if named is None:
        return as_of.astimezone(SHANGHAI).date()
    try:
        return date.fromisoformat(named)
    except ValueError as error:
        raise AgentReportError(f"--through {named!r} is not YYYY-MM-DD ({error})") from error


def _registration_path(options: argparse.Namespace) -> Path:
    """C's registration: `--registration` (absolute, or relative to the repository)."""
    repository = Path(options.repo)
    named = options.registration
    path = Path(named) if named else repository / REGISTRATION_IN_REPOSITORY
    return path if path.is_absolute() else repository / path


def _admit(registration: Path, repository: Path) -> ReportRegistration:
    """Admit C's committed registration against the code running now, or refuse by name."""
    try:
        _root, admitted = registry.admit_registered_code(
            registration, repository, also_bound=AGENT_SCRIPTS
        )
    except registry.HoldoutRefusedError as error:
        raise AgentReportError(f"{type(error).__name__}: {error}") from error
    body = admitted.registered
    config = body.get("config")
    kind = config.get("kind") if isinstance(config, Mapping) else None
    if kind != AGENT_REGISTRATION_KIND:
        raise AgentReportError(
            f"C's registered configuration's kind is {kind!r}; the report reads "
            f"{AGENT_REGISTRATION_KIND!r} and no other"
        )
    config_id = body.get("config_id")
    code_commit = body.get("code_commit")
    settings = body.get("settings")
    if not isinstance(config_id, str) or not config_id.strip():
        raise AgentReportError("C's registration names no config_id")
    if not isinstance(code_commit, str) or not code_commit.strip():
        raise AgentReportError("C's registration names no code_commit")
    if not isinstance(settings, Mapping):
        raise AgentReportError("C's registration names no settings object")
    return ReportRegistration(
        config_id=config_id,
        registration_sha256=hashlib.sha256(admitted.content).hexdigest(),
        registration_commit=admitted.commit,
        code_commit=code_commit,
        settings=dict(settings),
    )


def _step0_reading(
    repository: Path,
    block: AgentForwardStep0,
) -> tuple[RegisteredConfiguration, Any, str]:
    """Step 0's committed registration, checked against the three facts C froze, and its book."""
    path = Path(block.path)
    if not path.is_absolute():
        path = repository / path
    try:
        _root, admitted = registry.committed_registration(path, repository)
    except registry.HoldoutRefusedError as error:
        raise AgentReportError(f"{type(error).__name__}: {error}") from error
    body = admitted.registered
    config = body.get("config")
    if not isinstance(config, Mapping):
        raise AgentReportError(
            f"{path} registers no configuration object; C's residualisation needs Step 0's book"
        )
    seed = _seed_of(body.get("settings"))
    try:
        survivor = read_survivor_registration(config, seed=seed)
    except SurvivorRegistrationError as error:
        raise AgentReportError(f"{type(error).__name__}: {error}") from error
    digest = hashlib.sha256(admitted.content).hexdigest()
    if digest != block.registration_sha256:
        raise AgentReportError(
            f"C's registration records agent_forward_step0.registration_sha256 "
            f"{block.registration_sha256}, but {path} is {digest}; the Step 0 registration C reads "
            "is not the one it froze, so the report refuses rather than measuring on it"
        )
    config_id = body.get("config_id")
    if config_id != block.config_id:
        raise AgentReportError(
            f"C's registration records agent_forward_step0.config_id {block.config_id!r}, but "
            f"{path} is {config_id!r}; the report measures the frozen Step 0 configuration"
        )
    if seed != block.seed:
        raise AgentReportError(
            f"C's registration records agent_forward_step0.seed {block.seed}, but {path} has "
            f"random_seed {seed}; the sampling frame is not the frozen one"
        )
    code_commit = body.get("code_commit")
    if not isinstance(code_commit, str):
        raise AgentReportError(f"{path} names no code_commit")
    registered = RegisteredConfiguration(
        config_id=config_id,
        registration_sha256=digest,
        code_commit=code_commit,
        seed=seed,
    )
    return registered, survivor.book, survivor.adapter.exchange


def _labels(
    step0_runtime_dir: Path,
    *,
    exchange: str,
    anchor: date,
    as_of: datetime,
) -> Any:
    """The shared `model_view.OutcomeLabels` reader over Step 0's panel at the report's as-of."""
    years = tuple(sorted({anchor.year, as_of.astimezone(SHANGHAI).year}))
    return OutcomeLabels(
        PanelStore(Path(step0_runtime_dir) / "panel"),
        LabelReach(as_of=as_of, years=years, exchange=exchange),
    )


def _stored_primary(
    runtime_dir: Path,
    *,
    clock: Callable[[], datetime],
    registration_sha256: str,
) -> dict[str, Any]:
    """The abstention rate, read from C's stored LLM primary-set records and never computed.

    An abstention is a row with no score: it is counted as a coded absence, never as a `0.0`
    value, and the rate is printed even when it is zero. A record of another registration is not
    C's and is left out (the evaluator refuses one; the rate is only C's own).
    """
    store = prediction_store_at(Path(runtime_dir), clock=clock)
    scored = 0
    abstained = 0
    sessions: set[date] = set()
    for record_id in store.list_ids():
        record = store.get(record_id)
        if record is None:
            continue
        declaration = record.batch.artifact.declaration
        if declaration.family != AGENT_LLM_FAMILY:
            continue
        hyperparameters = dict(declaration.hyperparameters)
        if hyperparameters.get("scoring_set") != "primary":
            continue
        if hyperparameters.get("registration_sha256") != registration_sha256:
            continue
        sessions.add(record.batch.as_of.astimezone(SHANGHAI).date())
        for prediction in record.batch.predictions:
            if prediction.score is None:
                abstained += 1
            else:
                scored += 1
    rows = scored + abstained
    return {
        "scored": scored,
        "abstained": abstained,
        "rows": rows,
        "rate": abstained / rows if rows else None,
        "sessions": tuple(sorted(sessions)),
    }


def _model_aliases(runtime_dir: Path, *, sessions: Sequence[date]) -> dict[str, Any]:
    """The distinct model identities C's evening journals recorded, and the journals missing.

    `deepseek-flash` is a moving alias, so the report names the `(response.model,
    system_fingerprint)` pairs it actually saw and whether they changed across sessions (plan C
    protocol §9): a change is reported, never grounds for re-registration. A session C filed a
    primary record for but whose evening journal is absent is named as missing.
    """
    directory = agent_journal_dir_at(Path(runtime_dir))
    recorded: set[str] = set()
    identities: dict[tuple[object, object], set[str]] = {}
    if directory.is_dir():
        for path in sorted(directory.glob("*.json")):
            try:
                body = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as error:
                raise AgentReportError(
                    f"{path} cannot be read as an evening journal ({error}); the report states "
                    "the model identities it saw and refuses one it cannot read"
                ) from error
            if not isinstance(body, Mapping) or body.get("schema") != AGENT_JOURNAL_SCHEMA:
                raise AgentReportError(
                    f"{path} is not a {AGENT_JOURNAL_SCHEMA} journal; the model-alias block reads "
                    "only the evening command's own account"
                )
            session = body.get("session")
            name = session if isinstance(session, str) else path.stem
            recorded.add(name)
            family = body.get("llm_family")
            exchanges = family.get("exchanges") if isinstance(family, Mapping) else None
            if not isinstance(exchanges, list):
                continue
            for exchange in exchanges:
                if isinstance(exchange, Mapping):
                    key = (exchange.get("model"), exchange.get("system_fingerprint"))
                    identities.setdefault(key, set()).add(name)
    missing = [session.isoformat() for session in sessions if session.isoformat() not in recorded]
    rendered = [
        {
            "model": key[0],
            "system_fingerprint": key[1],
            "sessions": sorted(names),
        }
        for key, names in sorted(
            identities.items(), key=lambda item: (str(item[0][0]), str(item[0][1]))
        )
    ]
    return {
        "journals": len(recorded),
        "identities": rendered,
        "changed": len(identities) > 1,
        "missing_journals": sorted(missing),
    }


def _test_view(test: Any) -> dict[str, Any] | None:
    if test is None:
        return None
    return {
        "two_sided_p_value": test.two_sided_p_value,
        "one_sided_p_value": test.one_sided_p_value,
        "exact": test.exact,
        "sign_patterns": test.sign_patterns,
        "random_seed": test.random_seed,
    }


def _exploratory_view(reading: Any, *, q: float) -> dict[str, Any]:
    if reading.rejected is None:
        verdict = "no q verdict"
    elif reading.rejected:
        verdict = "below q"
    else:
        verdict = "at or above q"
    return {
        "family": reading.family,
        "scoring_set": reading.scoring_set,
        "horizon": reading.horizon,
        "positions": reading.positions,
        "measurable": reading.measurable,
        "mean": reading.mean,
        "q_value": reading.q_value,
        "rejected": reading.rejected,
        "verdict": verdict,
        "q": q,
    }


def _view(
    *,
    registration: ReportRegistration,
    evaluation: AgentEvaluation,
    protocol: AgentProtocol,
    power: AgentForwardPower,
    anchor: AgentForwardAnchor,
    through: date,
    as_of: datetime,
    abstention: Mapping[str, Any],
    aliases: Mapping[str, Any],
) -> dict[str, Any]:
    """Every printed fact as one JSON-safe mapping (`--json` writes exactly this)."""
    primary = evaluation.primary
    return {
        "schema": REPORT_SCHEMA,
        "candidate": True,
        "not_traded": True,
        "registration": {
            "config_id": registration.config_id,
            "registration_sha256": registration.registration_sha256,
            "registration_commit": registration.registration_commit,
            "code_commit": registration.code_commit,
        },
        "window": {
            "anchor_session": anchor.session.isoformat(),
            "through": through.isoformat(),
            "as_of": as_of.isoformat(),
        },
        "records": {
            "positions": evaluation.sessions,
            "witnessed": evaluation.sessions - len(evaluation.missing),
            "missing": [
                {"session": item.session.isoformat(), "reason": item.reason}
                for item in evaluation.missing
            ],
            "measurable": evaluation.measurable,
            "n": evaluation.measurable,
            "unmeasured": primary.unmeasured,
        },
        "abstention": {
            "scored": abstention["scored"],
            "abstained": abstention["abstained"],
            "rows": abstention["rows"],
            "rate": abstention["rate"],
        },
        "model_aliases": {
            "journals": aliases["journals"],
            "identities": aliases["identities"],
            "changed": aliases["changed"],
            "missing_journals": aliases["missing_journals"],
        },
        "primary": {
            "family": primary.family,
            "scoring_set": primary.scoring_set,
            "horizon": primary.horizon,
            "positions": primary.positions,
            "measurable": primary.measurable,
            "missing": primary.missing,
            "unmeasured": primary.unmeasured,
            "mean": primary.mean,
            "test": _test_view(primary.test),
            "descriptive_only": primary.test is None,
        },
        "outcome": evaluation.outcome,
        "outcome_wording": OUTCOME_WORDINGS[evaluation.outcome],
        "power": {
            "periods": evaluation.power.periods,
            "sd": evaluation.power.sd,
            "level": evaluation.power.level,
            "power": evaluation.power.power,
            "mean": evaluation.power.mean,
            "basis": evaluation.power.basis,
            "registered_verdict": power.verdict,
            "registered_sample": power.sample,
            "registered_measurable_sessions": power.measurable_sessions,
            "registered_detectable_mean": power.detectable_mean,
        },
        "exploratory": [
            _exploratory_view(reading, q=protocol.q) for reading in evaluation.exploratory
        ],
        "adopted": list(evaluation.adopted),
    }


def _mean_text(value: float | None) -> str:
    return "none" if value is None else f"{value:.6g}"


def _render(view: Mapping[str, Any]) -> list[str]:
    """The report as printed lines; see the module docstring for the order and the wording."""
    registration = view["registration"]
    window = view["window"]
    records = view["records"]
    abstention = view["abstention"]
    aliases = view["model_aliases"]
    primary = view["primary"]
    power = view["power"]
    lines = [
        f"{CANDIDATE} / candidate: the agent report of {registration['config_id']} "
        f"(registration {registration['registration_sha256'][:12]}...)",
        f"{NOT_TRADED}: this report measures stored predictions only; a passing run is not "
        "evidence that the family has an increment",
        f"registration      {registration['config_id']} {registration['registration_sha256']} "
        f"(commit {registration['registration_commit']}); code {registration['code_commit']}",
        f"window            anchor {window['anchor_session']} through {window['through']} "
        f"as_of {window['as_of']}",
        f"records           {records['witnessed']} witnessed of {records['positions']} positions; "
        f"measurable n={records['n']}; unmeasured {records['unmeasured']}",
    ]
    missing = ", ".join(f"{item['session']}: {item['reason']}" for item in records["missing"])
    lines.append(f"missing           {missing or 'none'}")
    rate = abstention["rate"]
    rate_text = "n/a" if rate is None else f"{rate:.1%}"
    lines.append(
        f"abstention        {abstention['scored']} scored, {abstention['abstained']} abstained of "
        f"{abstention['rows']} rows ({rate_text}); an abstention is never a 0 score"
    )
    identities = "; ".join(
        f"{item['model']}/{item['system_fingerprint']} on {', '.join(item['sessions'])}"
        for item in aliases["identities"]
    )
    lines.append(
        f"model alias       {identities or 'none recorded'}; changed: "
        f"{'yes' if aliases['changed'] else 'no'}; missing: "
        f"{', '.join(aliases['missing_journals']) or 'none'}"
    )
    descriptive = (
        "; descriptive only, the registered test is not made before the window matures"
        if primary["descriptive_only"]
        else ""
    )
    lines.append(
        f"primary           {primary['family']}/{primary['scoring_set']}/h{primary['horizon']}: "
        f"mean {_mean_text(primary['mean'])} over {primary['measurable']} measurable of "
        f"{primary['positions']} positions{descriptive}"
    )
    if primary["test"] is not None:
        test = primary["test"]
        lines.append(
            f"primary test      mean {_mean_text(primary['mean'])} over n={primary['measurable']}: "
            f"two-sided p {test['two_sided_p_value']:.6g}, one-sided p "
            f"{test['one_sided_p_value']:.6g}, level {power['level']:g}, "
            f"{'exact' if test['exact'] else 'randomized'} sign-flip over "
            f"{test['sign_patterns']} sign patterns"
        )
    lines.append(
        f"power             n={power['periods']}, detectable mean {_mean_text(power['mean'])} at "
        f"power {power['power']:g} from the window's measured dispersion; a property of the "
        f"design, never observed power: {power['basis']}"
    )
    lines.append(
        f"D1 power verdict: {power['registered_verdict']} (sample {power['registered_sample']}, "
        f"measurable sessions {power['registered_measurable_sessions']}, detectable mean "
        f"{_mean_text(power['registered_detectable_mean'])})"
    )
    lines.append(f"outcome           {view['outcome_wording']}")
    if view["outcome"] != "not_yet":
        lines.extend(f"adopted           {rule}" for rule in view["adopted"])
    exploratory = view["exploratory"]
    if exploratory:
        for arm in exploratory:
            lines.append(
                f"EXPLORATORY       {arm['family']}/{arm['scoring_set']}/h{arm['horizon']}d: "
                f"mean {_mean_text(arm['mean'])}, q_value {_mean_text(arm['q_value'])}: "
                f"{arm['verdict']} (q={arm['q']:g}); never pooled with the primary statistic and "
                "never a conclusion"
            )
    else:
        lines.append(
            "EXPLORATORY       none reported yet: the registered window has not been satisfied, "
            "so no exploratory arm was tested"
        )
    return lines


def _write_json(path: Path, view: Mapping[str, Any]) -> None:
    """Write the report atomically: temporary file, flush, then one `os.replace`."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = (json.dumps(view, sort_keys=True, indent=2, ensure_ascii=False) + "\n").encode()
    temporary = path.with_name(f"{path.name}.tmp")
    temporary.write_bytes(payload)
    os.replace(temporary, path)


def _report(options: argparse.Namespace, *, clock: Callable[[], datetime]) -> int:
    runtime = Path(options.runtime_dir)
    step0_runtime = Path(options.step0_runtime_dir)
    try:
        refuse_a_dry_week_runtime(runtime)
        refuse_a_dry_week_runtime(step0_runtime)
    except AgentEvaluationError as error:
        raise AgentReportError(str(error)) from error
    repository = Path(options.repo)
    registration = _admit(_registration_path(options), repository)
    try:
        protocol = read_agent_protocol(registration.settings)
        power = read_agent_forward_power(registration.settings)
        step0_block = read_agent_forward_step0(registration.settings)
        anchor = read_agent_forward_anchor(registration.settings)
    except AgentEvaluationError as error:
        raise AgentReportError(f"{type(error).__name__}: {error}") from error
    step0_registered, book, exchange = _step0_reading(repository, step0_block)
    as_of = _instant(options.as_of, clock)
    through = _through(options.through, as_of)
    agent_store = prediction_store_at(runtime, clock=clock)
    step0_store = prediction_store_at(step0_runtime, clock=clock)
    labels = _labels(step0_runtime, exchange=exchange, anchor=anchor.session, as_of=as_of)
    try:
        evaluation = evaluate(
            protocol=protocol,
            agent_store=agent_store,
            step0_store=step0_store,
            labels=labels,
            book=book,
            registered_sha256=registration.registration_sha256,
            step0_registered=step0_registered,
            anchor_session=anchor.session,
            through=through,
            as_of=as_of,
            agent_runtime_dir=runtime,
        )
    except AgentEvaluationError as error:
        raise AgentReportError(f"{type(error).__name__}: {error}") from error
    abstention = _stored_primary(
        runtime, clock=clock, registration_sha256=registration.registration_sha256
    )
    aliases = _model_aliases(runtime, sessions=abstention["sessions"])
    view = _view(
        registration=registration,
        evaluation=evaluation,
        protocol=protocol,
        power=power,
        anchor=anchor,
        through=through,
        as_of=as_of,
        abstention=abstention,
        aliases=aliases,
    )
    print("\n".join(_render(view)))
    if options.json:
        _write_json(Path(options.json), view)
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="agent_report.py",
        description=(
            "C's report (D8): read the stored agent and Step 0 records, apply the registered "
            "evaluator, and print the candidate reading. Never runs an agent, never trades."
        ),
    )
    parser.add_argument(
        "--runtime-dir", required=True, help="C's runtime (prediction store and evening journals)"
    )
    parser.add_argument(
        "--step0-runtime-dir",
        required=True,
        help="Step 0's runtime directory, opened read-only (its panel and prediction store)",
    )
    parser.add_argument(
        "--registration",
        default=None,
        help=f"C's committed registration (default: {REGISTRATION_IN_REPOSITORY})",
    )
    parser.add_argument("--repo", default=str(REPOSITORY), help="the repository root")
    parser.add_argument("--as-of", default=None, help="pin the report's clock (ISO 8601)")
    parser.add_argument("--through", default=None, help="cut the window at a session (YYYY-MM-DD)")
    parser.add_argument(
        "--json",
        default=None,
        metavar="PATH",
        help="also write the report as one JSON document; without it the report prints only",
    )
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    clock: Callable[[], datetime] | None = None,
) -> int:
    """Print C's report, or refuse by name; return the exit code (0 report, 2 refusal)."""
    parser = _parser()
    try:
        options = parser.parse_args(list(argv) if argv is not None else None)
    except SystemExit as error:
        return int(error.code or 0)
    now = clock or (lambda: datetime.now(SHANGHAI))
    try:
        return _report(options, clock=now)
    except AgentReportError as error:
        print(f"{type(error).__name__}: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
