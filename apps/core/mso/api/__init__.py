from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class NoQuery(BaseModel):
    """Een leesroute zonder opties weigert ook een impliciete verversopdracht."""

    model_config = ConfigDict(extra="forbid")


class ReadError(BaseModel):
    code: str
    correlation_id: str
    details: dict[str, Any] | None = None
    trace_id: UUID | None = None


class ReadAccessError(ReadError):
    detail: str
    message: str | None = None


class QueryFieldError(BaseModel):
    loc: list[str | int]
    msg: str


class ReadQueryError(ReadError):
    detail: list[QueryFieldError]
    message: list[QueryFieldError] | None = None


READ_RESPONSES = {
    401: {"model": ReadAccessError, "description": "Een geldige sessie of machine-sleutel is vereist."},
    403: {"model": ReadAccessError, "description": "De aanroeper heeft geen toegang tot deze leesroute."},
    422: {"model": ReadQueryError, "description": "Deze leesroute accepteert geen queryparameters."},
}
