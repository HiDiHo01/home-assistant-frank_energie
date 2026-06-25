from __future__ import annotations

import asyncio
import logging
from collections import deque
from collections.abc import Awaitable, Callable

_LOGGER = logging.getLogger(__name__)


class MutationQueue:
    """Serialize API mutations to avoid race conditions."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._queue: deque[Callable[[], Awaitable[None]]] = deque()

    async def add(self, mutation: Callable[[], Awaitable[None]]) -> None:
        """Queue and execute mutation safely."""
        async with self._lock:
            self._queue.append(mutation)
            while self._queue:
                current = self._queue.popleft()
                try:
                    await current()
                except Exception as err:
                    _LOGGER.error("Mutation failed: %s", err)
                    raise
