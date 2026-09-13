"""Reading a serialized research result back, and the one sentence every face refuses it with.

Carved out of `api/app.py` by `V2-P5-047`, which gave `openalpha validation record` and
`openalpha report create` to a CLI that had readers for both stores and a writer for neither.
Both new commands take the same serialized `ResearchRunResult` the two routes take -- it is what
`openalpha research run` prints -- so both need `parse_research_result`, and `cli.py` has no
import edge to `openalpha_cn.api` at all (`serve` reaches uvicorn by the string
`"openalpha_cn.api.app:app"`, deliberately). Importing the route module to reach a parser would
have created that edge and pulled FastAPI into every `openalpha version`.

A neutral top-level module rather than a home under `domain/`, for `batch_contracts.py`'s
reason and by its precedent: the integrity check is a **transport** concern -- it exists because
a caller can hand a record back over a wire or in a file -- and `domain` is forbidden from
importing `openalpha_cn.runtime`, where `ResearchRunResult` lives.

## The refusal text is the deliverable, not a by-product

`research_refusal_detail` returns the whole `{"reason", "message", "index", "subject", "field",
"claimed", "derived"}` body rather than a string, and `api/app.py` wraps it in an
`HTTPException` while `cli.py` prints its `message` on stderr. That direction matters: had the
CLI written its own sentence for a `signal_id` that does not describe its own content, the two
faces would have drifted within one edit, and a reader comparing them would have no way to know
which was current. `tests/integration/test_validation_and_report_writer_faces.py` asserts the
two byte-equal, which is only a meaningful assertion because one function writes both.
"""

from __future__ import annotations

from typing import Any

from openalpha_cn.runtime.contracts import ResearchRunResult

__all__ = [
    "ResearchIntegrityError",
    "parse_research_result",
    "research_refusal_detail",
]


class ResearchIntegrityError(ValueError):
    """One research result whose content-derived identifier does not describe its content.

    `V2-P4-041`. `parse_research_result` has always distinguished the three -- `signal_id`,
    `decision_id`, `run_manifest_id` -- and the routes flattened all three into
    `"Research result failed integrity validation."`, so a caller holding 5,545 results learned
    neither which record nor which of the three addresses had moved.

    A `ValueError` subclass so the existing `except (KeyError, TypeError, ValueError)` at each
    call site still catches it; what the routes now do is *ask* it which fault it is instead of
    discarding that. `claimed` and `derived` are both carried because the difference between
    them is the only actionable thing this route can offer: an edited record and an edited
    identifier need different fixes, and only the two values side by side tell them apart.
    """

    def __init__(
        self, *, reason: str, field: str, claimed: object, derived: str, subject: str
    ) -> None:
        super().__init__(f"research {field} does not match its content")
        self.reason = reason
        self.field = field
        self.claimed = claimed
        self.derived = derived
        self.subject = subject


def research_refusal_detail(error: Exception, *, index: int | None) -> dict[str, Any]:
    """The `{"reason", "message", ...}` body every face refuses a bad research result with.

    The shape is `_panel_detail`'s, which `docs/api/http.md` already documents as the thing a
    client switches on (`detail.reason`); the routes joining it was `V2-P4-041`'s point rather
    than an incidental tidy-up. `index` is `None` on `POST /api/v1/reports` and on both CLI
    writers, which parse one result rather than a list, and is carried as an explicit `null` so
    a client reads the same keys from every caller.

    `message` is deliberately self-contained -- it names the path, the subject, the claimed and
    the derived address, and the remedy -- because it is the whole of what a terminal shows. A
    CLI that printed `reason` and left the rest in a structure nobody renders would be a second,
    worse face on the same fault.
    """
    path = "research" if index is None else f"research[{index}]"
    if isinstance(error, ResearchIntegrityError):
        return {
            "reason": error.reason,
            "message": (
                f"{path} (subject {error.subject}) carries {error.field.rsplit('.', 1)[-1]} "
                f"{error.claimed!r} but its own content derives {error.derived!r}. All three "
                "identifiers on a research result are content-derived, so send the record back "
                "exactly as this service handed it out, or omit the identifier and let it be "
                "re-derived."
            ),
            "index": index,
            "subject": error.subject,
            "field": f"{path}.{error.field}",
            "claimed": error.claimed,
            "derived": error.derived,
        }
    return {
        "reason": "malformed_research_result",
        "message": (
            f"{path} is not a well-formed research result and was refused before any "
            f"identifier could be checked: {error}"
        ),
        "index": index,
        "subject": None,
        "field": path,
        "claimed": None,
        "derived": None,
    }


def parse_research_result(payload: dict[str, Any]) -> ResearchRunResult:
    """Rebuild a strict result while verifying its content-derived identifiers.

    Every computed identifier the response carried has to be stripped before validation and
    re-derived afterwards, because each contract is `extra="forbid"` and would otherwise
    reject its own serialized form. `V2-P4-025` adds a third such identifier --
    `RunManifest.run_manifest_id` -- and it is verified rather than merely dropped, for the
    same reason `signal_id` and `decision_id` are: a caller that could hand back an
    unverified manifest address could hand back one that does not describe the manifest
    beside it, which is the whole thing the address is for.
    """
    clean = {**payload}
    claimed_signal_id = clean.get("signal", {}).get("signal_id")
    claimed_decision_id = clean.get("decision", {}).get("decision_id")
    claimed_manifest_id = clean.get("manifest", {}).get("run_manifest_id")

    signal = {**clean["signal"]}
    signal.pop("signal_id", None)
    clean["signal"] = signal

    decision = {**clean["decision"]}
    decision.pop("decision_id", None)
    clean["decision"] = decision

    manifest = {**clean["manifest"]}
    manifest.pop("run_manifest_id", None)
    clean["manifest"] = manifest

    agent_results = []
    for item in clean.get("agent_results", []):
        agent = {**item}
        agent_signal = {**agent["signal"]}
        agent_signal.pop("signal_id", None)
        agent["signal"] = agent_signal
        agent_results.append(agent)
    clean["agent_results"] = agent_results

    result = ResearchRunResult.model_validate(clean)
    subject = result.signal.subject
    if claimed_signal_id != result.signal.signal_id:
        raise ResearchIntegrityError(
            reason="signal_id_mismatch",
            field="signal.signal_id",
            claimed=claimed_signal_id,
            derived=result.signal.signal_id,
            subject=subject,
        )
    if claimed_decision_id != result.decision.decision_id:
        raise ResearchIntegrityError(
            reason="decision_id_mismatch",
            field="decision.decision_id",
            claimed=claimed_decision_id,
            derived=result.decision.decision_id,
            subject=subject,
        )
    if claimed_manifest_id != result.manifest.run_manifest_id:
        raise ResearchIntegrityError(
            reason="run_manifest_id_mismatch",
            field="manifest.run_manifest_id",
            claimed=claimed_manifest_id,
            derived=result.manifest.run_manifest_id,
            subject=subject,
        )
    return result
