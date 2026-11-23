"""Live snapshot for live_summary. The T3 Writer builds it from MySQL after commits, T4 reads it.

Built from the DB, not from in-memory increments, so it is exact after duplicates (QoS 1) and
after a restart.
"""
from __future__ import annotations

import threading
from datetime import datetime
from typing import Any


class LiveCounters:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._data: dict[str, Any] = {}
        self._last_event_ts: datetime | None = None

    def set(self, data: dict[str, Any], last_event_ts: datetime | None) -> None:
        with self._lock:
            self._data = dict(data)
            self._last_event_ts = last_event_ts

    @property
    def last_event_ts(self) -> datetime | None:
        """Event time (ts) of the newest stored message."""
        with self._lock:
            return self._last_event_ts

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            data = dict(self._data)
            ts = self._last_event_ts
        data["last_event_ts"] = ts.isoformat() if ts else None
        return data
