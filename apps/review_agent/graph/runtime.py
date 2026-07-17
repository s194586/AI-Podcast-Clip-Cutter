from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


@dataclass
class ReviewGraphRuntime:
    build_context: Callable[[], tuple[dict[str, Any], Any]]
    invoke_provider: Callable[[Any, dict[str, Any], str | None], Any]
    validate_decision: Callable[[dict[str, Any], Any, dict[str, Any]], dict[str, Any]]
    failed_result: Callable[[dict[str, Any], str, str | None, dict[str, Any]], dict[str, Any]]
    corrective_message: Callable[[dict[str, Any], Exception], str]
    failure_category: Callable[[Exception], str]
    cancellation_check: Callable[[], bool] | None
    retryable_errors: tuple[type[BaseException], ...]
    provider_errors: tuple[type[BaseException], ...]
    cancelled_errors: tuple[type[BaseException], ...]
    review_context: dict[str, Any] | None = None
    provider: Any = None
    decision: Any = None
    validated_result: dict[str, Any] | None = None
    first_validation_error: Exception | None = None
    final_error: Exception | None = None
    corrective_feedback: str | None = None
