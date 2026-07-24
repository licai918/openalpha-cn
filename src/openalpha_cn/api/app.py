"""FastAPI application for OpenAlpha CN's versioned public HTTP surface."""

from fastapi import FastAPI

from openalpha_cn import __version__
from openalpha_cn.evidence.service import (
    EvidenceBuildRequest,
    EvidenceBuildResponse,
    build_evidence,
)


def create_app() -> FastAPI:
    """Create an isolated application instance for serving and tests."""
    application = FastAPI(
        title="OpenAlpha CN API",
        version=__version__,
        description="Evidence-traceable, point-in-time A-share research.",
    )

    @application.get("/health")
    def health() -> dict[str, str]:
        """Return a dependency-free liveness result."""
        return {"status": "ok", "version": __version__}

    @application.post("/api/v1/evidence/build")
    def evidence_build(request: EvidenceBuildRequest) -> EvidenceBuildResponse:
        """Normalize a provider batch into versioned evidence snapshots."""
        return build_evidence(request)

    return application


app = create_app()
