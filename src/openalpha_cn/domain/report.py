"""An immutable, evidence-linked research report.

Split out of `product/research.py` (V2-P0B-012) so `storage/product.py` can persist
`ResearchReport` without importing `openalpha_cn.product` at all, forbidden by the
`storage-no-upward-deps` import-linter contract. `ResearchReport` was already a plain data
value (its fields are raw scalars derived from a `ResearchRunResult` by
`ResearchReportFactory`, which stays behind -- the report itself never references
`ResearchRunResult`), so this is a pure relocation.

`product/research.py` re-exports `ResearchReport`/`RESEARCH_REPORT_VERSIONS` unchanged
alongside the `ReportStore` Protocol and `ResearchReportFactory` that stay behind
(product-layer behavior, not needed by storage), so every existing
`from openalpha_cn.product.research import ResearchReport` keeps working.
"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, computed_field, field_validator

from openalpha_cn.domain._identity import stable_model_id
from openalpha_cn.domain.time import ensure_aware
from openalpha_cn.domain.versioning import ContractVersions, single_version


class ResearchReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    run_id: str
    subject: str
    created_at: datetime
    title: str
    summary: str
    decision_id: str
    signal_id: str
    final_action: str
    evidence_ids: tuple[str, ...]
    risk_flags: tuple[str, ...]

    @field_validator("created_at")
    @classmethod
    def normalize_created_at(cls, value: datetime) -> datetime:
        return ensure_aware(value)

    @computed_field(return_type=str)  # type: ignore[prop-decorator]
    @property
    def report_id(self) -> str:
        return stable_model_id(prefix="rpt", model=self)


RESEARCH_REPORT_VERSIONS: ContractVersions[ResearchReport] = single_version(
    "research-report", ResearchReport
)
