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
    # P0 ölçümleme: yalnızca sayaçlar; davranış değişmez. Token/mesaj içeriği
    # tutulmaz, yalnızca API metot adları sayılır.
    rate_limit_count: int = 0
    retry_wait_seconds: float = 0.0
    lock_wait_seconds: float = 0.0
    cooldown_wait_seconds: float = 0.0
    rate_limits_by_method: dict[str, int] = field(default_factory=dict)

    def stats(self) -> dict[str, Any]:
        return {
            "rate_limit_count": self.rate_limit_count,
            "retry_wait_seconds": round(self.retry_wait_seconds, 2),
            "lock_wait_seconds": round(self.lock_wait_seconds, 2),
            "cooldown_wait_seconds": round(self.cooldown_wait_seconds, 2),
            "rate_limits_by_method": dict(self.rate_limits_by_method),
        }

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

    def defer(self, body: dict, method: str = "unknown") -> None:
        parameters = body.get("parameters")
        delay = parameters.get("retry_after", 1) if isinstance(parameters, dict) else 1
        if isinstance(delay, bool) or not isinstance(delay, (int, float)) or not math.isfinite(delay) or delay < 0:
            delay = 1
        # Telegram's deadline is a minimum, not a capped retry budget.
        wait_seconds = delay + 0.25
        self.until = max(self.until, time.monotonic() + wait_seconds)
        method_key = method if isinstance(method, str) and method else "unknown"
        self.rate_limit_count += 1
        self.retry_wait_seconds += wait_seconds
        self.rate_limits_by_method[method_key] = self.rate_limits_by_method.get(method_key, 0) + 1
        log.warning(
            "Bot API hız sınırı (metot=%s): %.2f sn beklenip aynı istek yeniden denenecek. "
            "[bu oturumda 429=%d, toplam retry bekleme=%.1f sn]",
            method_key, wait_seconds, self.rate_limit_count, self.retry_wait_seconds,
        )


_GATES: weakref.WeakKeyDictionary = weakref.WeakKeyDictionary()


def gate_for(token: str) -> RetryGate:
    gates = _GATES.setdefault(asyncio.get_running_loop(), {})
    return gates.setdefault(token, RetryGate())


_ZERO_STATS: dict[str, Any] = {
    "rate_limit_count": 0,
    "retry_wait_seconds": 0.0,
    "lock_wait_seconds": 0.0,
    "cooldown_wait_seconds": 0.0,
    "rate_limits_by_method": {},
}


def gate_stats(token: str) -> dict[str, Any]:
    """Sayaçların kopyası; kapı yoksa sıfır döner ve kapı YARATMAZ."""
    if not token:
        return dict(_ZERO_STATS)
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return dict(_ZERO_STATS)
    gates = _GATES.get(loop) or {}
    gate = gates.get(token)
    return gate.stats() if gate is not None else dict(_ZERO_STATS)


async def request_with_retry(
    token: str, method: str, request: Callable[[], Awaitable[dict[str, Any]]],
) -> dict[str, Any]:
    gate = gate_for(token)

    async def run() -> dict[str, Any]:
        while True:
            wait_started = time.monotonic()
            await gate.wait()
            gate.cooldown_wait_seconds += time.monotonic() - wait_started
            body = await request()
            if body.get("error_code") != 429 or body.get("ok"):
                return body
            gate.defer(body, method)

    # A long poll must never hold up notification sends or command replies.
    if method == "getUpdates":
        return await run()
    lock_started = time.monotonic()
    async with gate.lock:
        gate.lock_wait_seconds += time.monotonic() - lock_started
        return await run()
