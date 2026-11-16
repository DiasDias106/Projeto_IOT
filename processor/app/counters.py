"""Contadores em memória para o live_summary. O T3 Writer atualiza, o T4 Scheduler lê."""
from __future__ import annotations

import threading
from dataclasses import dataclass, field, fields
from datetime import datetime


@dataclass
class LiveCounters:
    bikes_in_use: int = 0
    trips_total: int = 0
    energy_kwh_total: float = 0.0
    duration_s_total: int = 0
    last_event_ts: datetime | None = None   # tempo de evento (ts) da última mensagem guardada
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False, compare=False)

    def update(self, **changes) -> None:
        """Ex.: counters.update(bikes_in_use=+1) soma; last_event_ts substitui."""
        with self._lock:
            for name, value in changes.items():
                if name == "last_event_ts":
                    self.last_event_ts = value
                else:
                    setattr(self, name, getattr(self, name) + value)

    def snapshot(self) -> dict:
        with self._lock:
            data = {f.name: getattr(self, f.name) for f in fields(self) if f.name != "_lock"}
        ts = data["last_event_ts"]
        data["last_event_ts"] = ts.isoformat() if ts else None
        data["avg_duration_s"] = (
            data["duration_s_total"] / data["trips_total"] if data["trips_total"] else 0.0
        )
        return data
