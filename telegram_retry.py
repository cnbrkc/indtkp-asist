"""Bot API 429 backpressure shared by notifications, edits and private replies.

Only an explicit rejection is retried. Timeouts/connection errors are NOT
replayed: Telegram may already have accepted that message. State is per event
loop and token; no credentials are logged or persisted.
"""
from __future__ import annotations

import asyncio
import logging
import math
import time
import weakref
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

log = logging.getLogger(__name__)


@dataclass
class RetryGate:
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    until: float = 0.0

    async def wait(self) -> None:
        while True:
            deadline = self.until
            delay = deadline - time.monotonic()
            if delay > 0:
                await asyncio.sleep(delay)
            # getUpdates runs outside the send lock and can extend a cooldown
            # while this request sleeps. Recheck that new deadline before I/O.
            if self.until <= deadline:
                return

    def defer(self, body: dict) -> None:
        parameters = body.get("parameters")
        delay = parameters.get("retry_after", 1) if isinstance(parameters, dict) else 1
        if isinstance(delay, bool) or not isinstance(delay, (int, float)) or not math.isfinite(delay) or delay < 0:
            delay = 1
        # Telegram's deadline is a minimum, not a capped retry budget.
        self.until = max(self.until, time.monotonic() + delay + 0.25)
        log.warning("Bot API hız sınırı: %.2f sn beklenip aynı istek yeniden denenecek.", delay + 0.25)


_GATES: weakref.WeakKeyDictionary = weakref.WeakKeyDictionary()


def gate_for(token: str) -> RetryGate:
    gates = _GATES.setdefault(asyncio.get_running_loop(), {})
    return gates.setdefault(token, RetryGate())


async def request_with_retry(
    token: str, method: str, request: Callable[[], Awaitable[dict[str, Any]]],
) -> dict[str, Any]:
    gate = gate_for(token)

    async def run() -> dict[str, Any]:
        while True:
            await gate.wait()
            body = await request()
            if body.get("error_code") != 429 or body.get("ok"):
                return body
            gate.defer(body)

    # A long poll must never hold up notification sends or command replies.
    if method == "getUpdates":
        return await run()
    async with gate.lock:
        return await run()
