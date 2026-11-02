"""Registry of post-apply evaluators: ``fn(change: dict) -> 'keep' | 'rollback' | 'unknown'``."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

Evaluator = Callable[[dict[str, Any]], str]
EVALUATORS: dict[str, Evaluator] = {}
RESULTS = ("keep", "rollback", "unknown")


def register_evaluator(target: str, fn: Evaluator) -> None:
    EVALUATORS[target] = fn
