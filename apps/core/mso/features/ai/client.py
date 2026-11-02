"""Minimal Anthropic Messages API client over httpx (no SDK dependency)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

from mso.security import check_url, redact

ANTHROPIC_VERSION = "2023-06-01"


class AIError(RuntimeError):
    pass


@dataclass
class AIReply:
    text: str
    input_tokens: int
    output_tokens: int
    stop_reason: str | None
    model: str


def call(
    *,
    url: str,
    api_key: str,
    model: str,
    system: str,
    user: str,
    max_tokens: int,
    effort: str | None,
    timeout_s: float,
) -> AIReply:
    target = check_url(url, purpose="ai", resolve=False)
    body: dict[str, Any] = {
        "model": model,
        "max_tokens": max_tokens,
        "system": system,
        "messages": [{"role": "user", "content": user}],
    }
    if effort and effort != "none":
        body["output_config"] = {"effort": effort}
    headers = {
        "x-api-key": api_key,
        "anthropic-version": ANTHROPIC_VERSION,
        "content-type": "application/json",
    }
    try:
        with httpx.Client(timeout=timeout_s, follow_redirects=False) as c:
            r = c.post(target, json=body, headers=headers)
    except httpx.HTTPError as exc:
        raise AIError(f"Verbinding met AI-aanbieder mislukt: {type(exc).__name__}") from exc
    if r.status_code != 200:
        msg = ""
        try:
            msg = str((r.json().get("error") or {}).get("message", ""))[:200]
        except ValueError:
            pass
        raise AIError(redact(f"AI-aanbieder antwoordde {r.status_code}" + (f": {msg}" if msg else "")))
    try:
        data = r.json()
    except ValueError as exc:
        raise AIError("Onleesbaar antwoord van AI-aanbieder") from exc
    usage = data.get("usage") or {}
    reply = AIReply(
        text="\n".join(
            b.get("text", "") for b in data.get("content") or [] if b.get("type") == "text"
        ).strip(),
        input_tokens=int(usage.get("input_tokens") or 0)
        + int(usage.get("cache_creation_input_tokens") or 0)
        + int(usage.get("cache_read_input_tokens") or 0),
        output_tokens=int(usage.get("output_tokens") or 0),
        stop_reason=data.get("stop_reason"),
        model=str(data.get("model") or model),
    )
    return reply
