"""A limit on how many calls one tenant can make to the API (the Throttling pattern: keep one tenant from using up what
the others need). At most `limit` calls in any window of one second per tenant: the times of the last `limit` calls are
kept, and a call is let through only if the oldest of them is more than a second old. The times are in memory, which is
right for one API process; with more than one, they would have to move to a shared store (Valkey), see ADR 0007.

It is asked after the caller has been shown to belong to the tenant and to be allowed the operation, so a call from
someone who is not a member, or one that is refused, never counts against the tenant (otherwise anyone who knows a
tenant id could use up that tenant's calls).
"""
from __future__ import annotations

import time
import uuid
from collections import deque
from typing import Callable

WINDOW = 1.0


class TenantThrottle:
    def __init__(self, limit: int, clock: Callable[[], float] = time.monotonic) -> None:
        if limit < 1:
            raise ValueError("the limit must be at least 1 call per second")
        self.limit, self._clock = limit, clock
        self._calls: dict[uuid.UUID, deque[float]] = {}

    def take(self, tenant_id: uuid.UUID) -> float:
        """0.0 if the call may go on (and is counted); otherwise the seconds until it could."""
        now = self._clock()
        q = self._calls.setdefault(tenant_id, deque())
        while q and now - q[0] >= WINDOW:
            q.popleft()
        if len(q) < self.limit:
            q.append(now)
            return 0.0
        return WINDOW - (now - q[0])
