from __future__ import annotations

import threading
from collections.abc import Callable

from .exceptions import PipelineCancelled


class CancellationToken:
    def __init__(self, external_check: Callable[[], bool] | None = None) -> None:
        self._event = threading.Event()
        self._external_check = external_check

    def cancel(self) -> None:
        self._event.set()

    @property
    def is_cancelled(self) -> bool:
        if self._event.is_set():
            return True
        if self._external_check is None:
            return False
        try:
            return bool(self._external_check())
        except Exception:
            return False

    def raise_if_cancelled(self) -> None:
        if self.is_cancelled:
            raise PipelineCancelled("Pipeline cancelled by user.")
