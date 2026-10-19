"""Coordinate public website requests across addresses, flows and reloads."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import math
import time

from .provider import ProviderError


class PublicRequestError(ProviderError):
    """A public HTTP operation failed, possibly with a server retry hint."""

    def __init__(self, message: str, *, retry_after: float | None = None):
        super().__init__(message)
        self.retry_after = retry_after


class PublicCooldownError(ProviderError):
    """A previous request failed; no new network request was attempted."""

    def __init__(self, seconds: int):
        self.retry_after = seconds
        super().__init__(f"DTEK public service retry paused for {seconds} seconds")


def retry_after_seconds(value: str | None) -> float | None:
    """Accept Retry-After seconds or an HTTP date; cap untrusted hints at 1h."""
    if not isinstance(value, str):
        return None
    try:
        seconds = float(value)
    except ValueError:
        try:
            date = parsedate_to_datetime(value)
            if date.tzinfo is None:
                return None
            seconds = (date - datetime.now(timezone.utc)).total_seconds()
        except (TypeError, ValueError, OverflowError):
            return None
    return min(3600.0, max(0.0, seconds)) if math.isfinite(seconds) else None


class PublicRequestGate:
    """One short queue and one failure cooldown per operator, not per account.

    The lock includes the bounded page/CSRF handshake and its retries. A waiting
    address sees a failure before it can send anything. Cooldowns fail fast;
    they never keep a setup flow or an unload waiting for minutes.
    """

    def __init__(self, *, clock=time.monotonic, sleep=asyncio.sleep):
        self._clock = clock
        self._sleep = sleep
        self._lock = asyncio.Lock()
        self._next_request = 0.0
        self._retry_at = 0.0
        self.failures = 0

    @property
    def retry_in(self) -> int:
        return math.ceil(max(0.0, self._retry_at - self._clock()))

    async def run(self, operation):
        async with self._lock:
            if delay := self.retry_in:
                raise PublicCooldownError(delay)
            if delay := max(0.0, self._next_request - self._clock()):
                await self._sleep(delay)
            try:
                result = await operation()
            except PublicRequestError as err:
                self.failures += 1
                delay = min(60 * 2 ** min(self.failures - 1, 4), 600)
                if err.retry_after is not None:
                    delay = max(delay, min(3600, err.retry_after))
                self._retry_at = self._clock() + delay
                raise
            else:
                self.failures = 0
                self._retry_at = 0.0
                return result
            finally:
                # Cancellation releases the lock without claiming a failure or
                # success. The next request still observes the normal spacing.
                self._next_request = self._clock() + 1.0


def operator_gate(hass, provider_id: str) -> PublicRequestGate:
    """HA-owned lifetime survives entry reloads without crossing HA instances."""
    gates = hass.data.setdefault("dtek_monitor_public_requests", {})
    if provider_id not in gates:
        gates[provider_id] = PublicRequestGate()
    return gates[provider_id]
