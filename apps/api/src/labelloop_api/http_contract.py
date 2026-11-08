"""Document redacted boundary errors, never FastAPI's raw validation inputs."""

from pydantic import BaseModel, ConfigDict


class BoundaryError(BaseModel):
    model_config = ConfigDict(extra="forbid")

    detail: str


BOUNDARY_RESPONSES = {
    401: {"model": BoundaryError, "description": "Authentication required"},
    403: {"model": BoundaryError, "description": "Permission or CSRF refusal"},
    422: {"model": BoundaryError, "description": "Redacted invalid request"},
    503: {"model": BoundaryError, "description": "Dependency unavailable"},
}
