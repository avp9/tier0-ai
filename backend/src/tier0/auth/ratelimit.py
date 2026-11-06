from __future__ import annotations

import time
from collections import defaultdict, deque
from collections.abc import Callable

_PRUNE_AT = 10_000


class LoginRateLimiter:
    """Counts failed logins per email and per client address in a sliding window.

    In memory, so it is per server process; with a few replicas that only multiplies the
    allowance a little.
    """

    def __init__(
        self,
        max_per_email: int,
        max_per_ip: int,
        window_seconds: float,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._limits = {"email": max_per_email, "ip": max_per_ip}
        self._window = window_seconds
        self._clock = clock
        self._failures: dict[tuple[str, str], deque[float]] = defaultdict(deque)

    def retry_after(self, email: str, ip: str) -> int | None:
        """Seconds to wait if either key is over its limit, else None."""
        waits = [self._wait(kind, key) for kind, key in (("email", email.lower()), ("ip", ip))]
        blocked = [w for w in waits if w > 0]
        return max(1, int(max(blocked) + 0.999)) if blocked else None

    def record_failure(self, email: str, ip: str) -> None:
        now = self._clock()
        for kind, key in (("email", email.lower()), ("ip", ip)):
            self._failures[(kind, key)].append(now)
        if len(self._failures) > _PRUNE_AT:
            self._prune(now)

    def record_success(self, email: str) -> None:
        self._failures.pop(("email", email.lower()), None)

    def _wait(self, kind: str, key: str) -> float:
        events = self._failures.get((kind, key))
        if not events:
            return 0.0
        now = self._clock()
        while events and now - events[0] >= self._window:
            events.popleft()
        if len(events) < self._limits[kind]:
            return 0.0
        return events[0] + self._window - now

    def _prune(self, now: float) -> None:
        for key in [k for k, v in self._failures.items() if not v or now - v[-1] >= self._window]:
            del self._failures[key]


class SlidingWindowLimiter:
    """At most ``max_events`` per key in any ``window_seconds``; every call counts."""

    def __init__(
        self,
        max_events: int,
        window_seconds: float,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._max = max_events
        self._window = window_seconds
        self._clock = clock
        self._events: dict[str, deque[float]] = defaultdict(deque)

    def hit(self, key: str) -> int | None:
        """Record one event. Returns seconds to wait if the key is over its limit, else None."""
        now = self._clock()
        events = self._events[key]
        while events and now - events[0] >= self._window:
            events.popleft()
        if len(events) >= self._max:
            return max(1, int(events[0] + self._window - now + 0.999))
        events.append(now)
        if len(self._events) > _PRUNE_AT:
            for stale in [
                k for k, v in self._events.items() if not v or now - v[-1] >= self._window
            ]:
                del self._events[stale]
        return None
