"""Foutvorm en verzoekgrens voor de connector (contract §5)."""

import logging
from typing import Any
from uuid import UUID, uuid4

import psycopg
from fastapi import HTTPException
from fastapi.responses import JSONResponse
from psycopg_pool import PoolTimeout
from pydantic import BaseModel
from starlette.datastructures import Headers
from starlette.types import ASGIApp, Receive, Scope, Send

PREFIX = "/connector/v1"
MAX_BYTES = 1_048_576
LOGGER = logging.getLogger(__name__)
MESSAGES = {
    "unauthorized": "Meld je aan met een geldige sessie of machine-sleutel.",
    "forbidden": "Je hebt geen toegang tot deze handeling.",
    "not_found": "De gevraagde bron bestaat niet.",
    "method_not_allowed": "Deze methode is niet toegestaan.",
    "conflict": "De huidige toestand laat dit verzoek niet toe.",
    "preview_changed": "De voorvertoning is gewijzigd. Vraag een nieuw voorstel aan.",
    "approval_expired": "De goedkeuring of het uitvoeringsverzoek is verlopen of nog niet geldig.",
    "approval_required": "Een geldige, gebonden goedkeuring is vereist.",
    "nonce_reused": "De nonce van het uitvoeringsverzoek is al gebruikt.",
    "idempotency_key_required": "Idempotency-Key ontbreekt.",
    "idempotency_key_invalid": "Idempotency-Key moet één UUID zijn.",
    "idempotency_key_reused": "Deze Idempotency-Key hoort bij een ander verzoek.",
    "idempotency_in_progress": "Dit verzoek is nog bezig.",
    "precondition_failed": "De huidige versie komt niet overeen met de opgegeven versie.",
    "upstream_error": "De bronactie kon niet worden uitgevoerd of gecontroleerd.",
    "cursor_expired": "De cursor is verlopen. Begin opnieuw zonder cursor.",
    "payload_too_large": "Het verzoek is groter dan 1 MiB.",
    "validation_error": "Een of meer velden zijn ongeldig.",
    "rate_limited": "Te veel verzoeken. Probeer het later opnieuw.",
    "capability_unavailable": "Deze handeling is nog niet beschikbaar.",
    "dependency_unavailable": "Een benodigde dienst is tijdelijk onbereikbaar.",
    "internal": "Interne fout.",
}
STATUS_CODES = {
    401: "unauthorized", 403: "forbidden", 404: "not_found", 405: "method_not_allowed",
    409: "conflict", 412: "precondition_failed", 413: "payload_too_large", 422: "validation_error", 429: "rate_limited",
    500: "internal", 501: "capability_unavailable", 502: "upstream_error", 503: "dependency_unavailable",
}


def is_connector(path: str) -> bool:
    return path == PREFIX or path.startswith(PREFIX + "/")


class ErrorBody(BaseModel):
    code: str
    message: str
    details: dict[str, Any]
    trace_id: UUID


class ConnectorError(HTTPException):
    def __init__(self, status: int, code: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(status, MESSAGES[code])
        self.code = code
        self.safe_details = details


def error_response(status: int, code: str | None = None, details: dict[str, Any] | None = None,
                   headers: dict[str, str] | None = None) -> JSONResponse:
    code = code if code in MESSAGES else STATUS_CODES.get(status, "internal")
    body = ErrorBody(code=code, message=MESSAGES[code], details=details or {}, trace_id=uuid4())
    response = JSONResponse(body.model_dump(mode="json"), status_code=status,
                            headers={"Cache-Control": "no-store", **(headers or {})})
    if status in {429, 503}:
        response.headers.setdefault("Retry-After", "1")
    return response


class ConnectorRequestMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or (scope["path"] != "/readyz" and not is_connector(scope["path"])):
            await self.app(scope, receive, send)
            return

        async def reject() -> None:
            response = error_response(413, details={"max_bytes": MAX_BYTES}, headers={"Connection": "close"})
            await response(scope, receive, send)

        length = Headers(scope=scope).get("content-length", "0").lstrip("0")
        if length.isdecimal() and (len(length) > 7 or int(length) > MAX_BYTES):
            await reject()
            return

        body = bytearray()
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            chunk = message.get("body", b"")
            if len(body) + len(chunk) > MAX_BYTES:
                await reject()
                return
            body.extend(chunk)
            if not message.get("more_body"):
                break

        delivered = False

        async def replay() -> Any:
            nonlocal delivered
            if delivered:
                return await receive()
            delivered = True
            return {"type": "http.request", "body": bytes(body), "more_body": False}

        started = False

        async def send_response(message: Any) -> None:
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            await self.app(scope, replay, send_response)
        except Exception as exc:
            if started or scope["path"] == "/readyz":
                raise
            if isinstance(exc, (psycopg.OperationalError, PoolTimeout)):
                response = error_response(503)
            else:
                LOGGER.error("Onverwachte connectorfout: %s", type(exc).__name__)
                response = error_response(500)
            await response(scope, replay, send)
