"""Schema-validated optional model-backed research agent."""

import json

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from openalpha_cn.agents.base import AgentContext, AgentResult
from openalpha_cn.domain.signal import SignalFrame
from openalpha_cn.models.base import ModelProvider


class ModelProviderFailure(RuntimeError):
    """Raised when structured output remains invalid after the retry budget."""


class StructuredAgentPayload(BaseModel):
    """The only model-generated payload accepted by a structured agent."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    signal: SignalFrame
    rationale: str = Field(min_length=1, max_length=4000)


class StructuredSignalAgent:
    """Call a model provider and enforce the same signal contract as baseline agents."""

    def __init__(
        self,
        *,
        agent_id: str,
        evidence_families: frozenset[str],
        provider: ModelProvider,
        max_attempts: int = 2,
    ) -> None:
        if max_attempts < 1:
            raise ValueError("max_attempts must be at least one")
        self.agent_id = agent_id
        self.evidence_families = evidence_families
        self.provider = provider
        self.max_attempts = max_attempts

    def analyze(self, context: AgentContext) -> AgentResult:
        """Validate subject, clock, and evidence references across bounded retries."""
        allowed_evidence = {item.evidence_id for item in context.evidence}
        schema = StructuredAgentPayload.model_json_schema(mode="validation")
        user_payload = {
            "run_id": context.run_id,
            "subject": context.subject,
            "as_of": context.as_of.isoformat(),
            "evidence": [
                {
                    "evidence_id": item.evidence_id,
                    "kind": item.kind,
                    "summary": item.summary,
                }
                for item in context.evidence
            ],
        }
        last_error: Exception | None = None
        for _attempt in range(1, self.max_attempts + 1):
            try:
                raw = self.provider.generate_json(
                    system=(
                        "Return only the requested structured research payload. "
                        "Cite only supplied evidence IDs and abstain when insufficient."
                    ),
                    user=json.dumps(user_payload, ensure_ascii=False, sort_keys=True),
                    schema=schema,
                )
                payload = StructuredAgentPayload.model_validate(raw)
                self._validate_context(
                    payload=payload,
                    context=context,
                    allowed_evidence=allowed_evidence,
                )
                return AgentResult(
                    agent_id=self.agent_id,
                    signal=payload.signal,
                    rationale=payload.rationale,
                )
            except (ValidationError, ValueError) as error:
                last_error = error
        raise ModelProviderFailure(
            f"{self.agent_id} produced invalid structured output after "
            f"{self.max_attempts} attempts: {last_error}"
        )

    @staticmethod
    def _validate_context(
        *,
        payload: StructuredAgentPayload,
        context: AgentContext,
        allowed_evidence: set[str],
    ) -> None:
        signal = payload.signal
        if signal.subject != context.subject:
            raise ValueError("model signal subject does not match context")
        if signal.as_of != context.as_of:
            raise ValueError("model signal as_of does not match context")
        if not set(signal.evidence_ids).issubset(allowed_evidence):
            raise ValueError("model signal cites evidence outside the context")
