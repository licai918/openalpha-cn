"""Read-only point-in-time evidence lookup tool."""

from openalpha_cn.domain.evidence import EvidenceSnapshot
from openalpha_cn.tools.base import ToolMetadata, ToolRequest, ToolResult


class EvidenceLookupTool:
    """Look up evidence without crossing the request availability clock."""

    _metadata = ToolMetadata(
        tool_id="evidence.lookup",
        description="Find evidence by subject, optional kind, and point-in-time visibility.",
        read_only=True,
    )

    def __init__(self, *, evidence: tuple[EvidenceSnapshot, ...]) -> None:
        self.evidence = evidence

    @property
    def metadata(self) -> ToolMetadata:
        """Return the tool's read-only policy."""
        return self._metadata

    def execute(self, request: ToolRequest) -> ToolResult:
        """Return stable evidence IDs or an explicit no-data result."""
        matches = tuple(
            item.evidence_id
            for item in self.evidence
            if item.subject == request.subject
            and item.visible_at(request.as_of)
            and (request.kind is None or item.kind == request.kind)
        )
        if not matches:
            return ToolResult(
                status="no_data",
                no_data_reason="No visible evidence matched the tool request.",
            )
        return ToolResult(status="success", evidence_ids=matches)
