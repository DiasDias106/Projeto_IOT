"""T3 Writer (PLAN.md, sections 1, 3 and 6).

Owns the only MySQL connection. Batched writes, one transaction per batch, all idempotent and
independent of message order:

  - station_status:  insert; a duplicate seq (QoS 1 redelivery) is a no-op.
  - trip_started:    creates the trips row with ended_at = NULL; never overwrites end data.
  - trip_ended:      completes the row, or inserts it complete if the start is missing or later.
  - rejected:        insert into rejected_messages; the same bytes are stored once.

Plain INSERT ... ON DUPLICATE KEY UPDATE is used instead of INSERT IGNORE, because IGNORE also
hides foreign key and data errors, which would silently drop rows.

Queue items are marked done only after their batch is committed, so clean_queue.join() means
"everything is in MySQL".

After each commit: publishes idc/fc15/internal/status/<station_id> (QoS 1, retained) and, at most
once per second, rebuilds the live snapshot and closes trips open for too long (NEVER_ENDED).
"""
from __future__ import annotations

import hashlib
import json
import logging
import queue
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping

import mysql.connector
import paho.mqtt.client as mqtt

from . import db
from .config import Config
from .contracts import NEVER_ENDED, OUTLIER_LONG_S, CleanRecord
from .counters import LiveCounters
from .worker import StationInfo

log = logging.getLogger("T3-writer")

STATUS_COLS = (
    "seq", "station_id", "ts", "num_bikes_available", "num_ebikes_available", "num_bikes_disabled",
    "num_docks_available", "num_docks_disabled", "is_installed", "is_renting", "is_returning",
    "quality_flag", "source",
)
TRIP_START_COLS = (
    "trip_id", "rideable_type", "member_casual", "started_at", "start_station_id",
    "start_station_name", "start_lat", "start_lng", "seq_start",
)
TRIP_END_COLS = (
    "trip_id", "rideable_type", "member_casual", "started_at", "ended_at", "duration_s",
    "start_station_id", "start_station_name", "end_station_id", "end_station_name",
    "start_lat", "start_lng", "end_lat", "end_lng", "distance_km", "energy_kwh", "cost_usd",
    "quality_flag", "source", "seq_end",
)
REJECTED_COLS = (
    "received_at", "source", "topic", "msg_type", "seq", "station_id", "event_ts", "reason",
    "detail", "payload", "payload_sha256",
)


def _insert(table: str, cols: tuple[str, ...]) -> str:
    return (f"INSERT INTO {table} ({', '.join(cols)}) "
            f"VALUES ({', '.join(['%s'] * len(cols))}) AS new ")


SQL = {
    "station_status": _insert("station_status", STATUS_COLS) + "ON DUPLICATE KEY UPDATE seq = station_status.seq",
    "trip_started": _insert("trips", TRIP_START_COLS)
    + "ON DUPLICATE KEY UPDATE seq_start = COALESCE(trips.seq_start, new.seq_start)",
    # source first: assignments run left to right, so it must read trips.seq_end before it changes.
    # A redelivered trip_ended (same seq_end) keeps its original source.
    "trip_ended": _insert("trips", TRIP_END_COLS) + "ON DUPLICATE KEY UPDATE "
    "source = IF(trips.seq_end <=> new.seq_end, trips.source, new.source), "
    + ", ".join(f"{c} = new.{c}" for c in TRIP_END_COLS if c not in ("trip_id", "source")),
    "rejected": _insert("rejected_messages", REJECTED_COLS) + "ON DUPLICATE KEY UPDATE payload_sha256 = rejected_messages.payload_sha256",
}
COLS = {"station_status": STATUS_COLS, "trip_started": TRIP_START_COLS,
        "trip_ended": TRIP_END_COLS, "rejected": REJECTED_COLS}
WRITE_ORDER = ("station_status", "trip_started", "trip_ended", "rejected")

CONNECTION_ERRORS = (mysql.connector.errors.OperationalError, mysql.connector.errors.InterfaceError)


def _event_ts(record: CleanRecord) -> datetime | None:
    row = record.row
    if record.kind == "station_status":
        return row["ts"]
    if record.kind == "trip_started":
        return row["started_at"]
    if record.kind == "trip_ended":
        return row["ended_at"]
    return None


class Writer(threading.Thread):
    def __init__(self, cfg: Config, clean_queue: queue.Queue, counters: LiveCounters,
                 mqtt_client: mqtt.Client, stations: Mapping[str, StationInfo], stop: threading.Event):
        super().__init__(name="T3-writer", daemon=True)
        self.cfg = cfg
        self.in_queue = clean_queue
        self.counters = counters
        self.mqtt = mqtt_client
        self.stations = stations
        self.stop_event = stop
        self.conn = None
        self.batch: list[CleanRecord] = []
        self.batch_started = 0.0
        self.latest: dict[str, dict[str, Any]] = {}   # newest station_status per station
        self.last_event_ts: datetime | None = None
        self.dirty = True
        self.last_refresh = 0.0
        self.republish = threading.Event()           # set by T1 on every (re)connect
        self.ready = threading.Event()

    # ---------- main loop ----------

    def run(self) -> None:
        log.info("starting")
        self.conn = db.connect(self.cfg)
        self._load_state()
        self.ready.set()
        while True:
            try:
                item = self.in_queue.get(timeout=0.2)
            except queue.Empty:
                item = None
            if item is not None:
                if not self.batch:
                    self.batch_started = time.monotonic()
                self.batch.append(item)
            if self.batch and (item is None or len(self.batch) >= self.cfg.writer_batch_size
                               or time.monotonic() - self.batch_started >= self.cfg.writer_flush_s):
                self.flush()
            if self.republish.is_set():
                self.republish.clear()
                self._publish_all_latest()
            if self.dirty and time.monotonic() - self.last_refresh >= 1.0:
                self._safe(self.refresh)
            if self.stop_event.is_set() and not self.batch and self.in_queue.empty():
                break
        self._safe(self.refresh)
        self.conn.close()
        log.info("stopped")

    def _safe(self, fn) -> None:
        try:
            fn()
        except CONNECTION_ERRORS as exc:
            log.warning("MySQL connection lost in %s (%s); reconnecting", fn.__name__, exc)
            self._reconnect()
        except Exception:
            log.exception("error in %s()", fn.__name__)

    def _reconnect(self) -> None:
        try:
            self.conn.close()
        except Exception:
            pass
        self.conn = db.connect(self.cfg, retries=1_000_000)

    # ---------- writes ----------

    def flush(self) -> None:
        """Write the batch in one transaction, retrying on connection loss. Never drops rows."""
        batch, self.batch = self.batch, []
        while True:
            try:
                self._write(batch)
                break
            except CONNECTION_ERRORS as exc:
                log.warning("MySQL connection lost during flush (%s); retrying the batch", exc)
                self._reconnect()
            except mysql.connector.Error as exc:
                log.warning("batch of %d failed (%s); writing row by row", len(batch), exc)
                self._rollback()
                self._write_one_by_one(batch)
                break
        self._after_commit(batch)
        for _ in batch:
            self.in_queue.task_done()

    def _write(self, batch: list[CleanRecord]) -> None:
        cur = self.conn.cursor()
        try:
            for kind in WRITE_ORDER:
                rows = [self._params(r) for r in batch if r.kind == kind]
                if rows:
                    cur.executemany(SQL[kind], rows)
            self.conn.commit()
        except Exception:
            self._rollback()
            raise
        finally:
            cur.close()

    def _write_one_by_one(self, batch: list[CleanRecord]) -> None:
        """Fallback after a data error: each row alone; a row MySQL refuses goes to rejected_messages."""
        for record in sorted(batch, key=lambda r: WRITE_ORDER.index(r.kind)):
            while True:
                try:
                    self._write([record])
                    break
                except CONNECTION_ERRORS:
                    self._reconnect()
                except mysql.connector.Error as exc:
                    if record.kind == "rejected":
                        log.error("could not store rejected message seq=%s: %s", record.seq, exc)
                        break
                    log.warning("MySQL refused %s seq=%s: %s", record.kind, record.seq, exc)
                    record = self._db_rejected(record, str(exc))

    def _db_rejected(self, record: CleanRecord, error: str) -> CleanRecord:
        payload = json.dumps(record.row, default=str, sort_keys=True).encode()
        row = {
            "received_at": datetime.now(timezone.utc).replace(tzinfo=None),
            "source": record.row.get("source") or "mqtt",
            "topic": None,
            "msg_type": record.kind,
            "seq": record.seq,
            "station_id": record.row.get("station_id") or record.row.get("start_station_id"),
            "event_ts": _event_ts(record),
            "reason": "DB_REJECTED",
            "detail": error[:255],
            "payload": payload,
            "payload_sha256": hashlib.sha256(payload).hexdigest(),
        }
        return CleanRecord(kind="rejected", seq=record.seq, row=row)

    def _rollback(self) -> None:
        try:
            self.conn.rollback()
        except Exception:
            pass

    @staticmethod
    def _params(record: CleanRecord) -> tuple:
        return tuple(record.row.get(c) for c in COLS[record.kind])

    # ---------- after commit ----------

    def _after_commit(self, batch: list[CleanRecord]) -> None:
        self.dirty = True
        changed: dict[str, dict[str, Any]] = {}
        for record in batch:
            ts = _event_ts(record)
            if ts and (self.last_event_ts is None or ts > self.last_event_ts):
                self.last_event_ts = ts
            if record.kind == "station_status":
                sid = record.row["station_id"]
                current = self.latest.get(sid)
                # Newest by event time (then seq), so a late or replayed message never hides a newer state
                if current is None or (record.row["ts"], record.seq) > (current["ts"], current["seq"]):
                    self.latest[sid] = record.row
                    changed[sid] = record.row
        for sid, row in changed.items():
            self._publish_status(sid, row)

    def _status_payload(self, sid: str, row: Mapping[str, Any]) -> dict[str, Any]:
        info = self.stations.get(sid)
        return {
            "station_id": sid,
            "name": info.name if info else None,
            "lat": info.lat if info else None,
            "lon": info.lon if info else None,
            "capacity": info.capacity if info else None,
            "ts": row["ts"].isoformat(),
            "seq": row["seq"],
            "num_bikes_available": row["num_bikes_available"],
            "num_ebikes_available": row["num_ebikes_available"],
            "num_classic_available": row["num_bikes_available"] - row["num_ebikes_available"],
            "num_bikes_disabled": row["num_bikes_disabled"],
            "num_docks_available": row["num_docks_available"],
            "num_docks_disabled": row["num_docks_disabled"],
            "is_installed": row["is_installed"],
            "is_renting": row["is_renting"],
            "is_returning": row["is_returning"],
            "quality_flag": row["quality_flag"],
        }

    def _publish_status(self, sid: str, row: Mapping[str, Any]) -> None:
        # During the stage 1 load T1 is not connected yet: skip, or ~16 500 QoS 1 messages pile up.
        if not self.mqtt.is_connected():
            return
        self.mqtt.publish(self.cfg.topic_internal_status(sid),
                          json.dumps(self._status_payload(sid, row)), qos=1, retain=True)

    def _publish_all_latest(self) -> None:
        for sid, row in list(self.latest.items()):
            self._publish_status(sid, row)
        log.info("published the state of %d stations", len(self.latest))

    # ---------- state ----------

    def _load_state(self) -> None:
        """Newest status per station and newest event time, so a restart continues where it was."""
        cur = self.conn.cursor(dictionary=True)
        try:
            cur.execute(
                f"SELECT {', '.join(STATUS_COLS)} FROM (SELECT *, ROW_NUMBER() OVER "
                "(PARTITION BY station_id ORDER BY ts DESC, seq DESC) AS rn FROM station_status) s WHERE rn = 1")
            self.latest = {r["station_id"]: r for r in cur.fetchall()}
            cur.execute("SELECT GREATEST(COALESCE((SELECT MAX(ts) FROM station_status), '1000-01-01'), "
                        "COALESCE((SELECT MAX(started_at) FROM trips), '1000-01-01'), "
                        "COALESCE((SELECT MAX(ended_at) FROM trips), '1000-01-01')) AS t")
            t = cur.fetchone()["t"]
            if isinstance(t, str):
                t = datetime.fromisoformat(t)
            self.last_event_ts = t if t and t.year > 1000 else None
            self.conn.commit()
        finally:
            cur.close()

    def refresh(self) -> None:
        """Close trips open too long, then rebuild the live snapshot from MySQL."""
        self.last_refresh = time.monotonic()
        cur = self.conn.cursor()
        try:
            if self.last_event_ts is not None:
                cutoff = self.last_event_ts - timedelta(seconds=OUTLIER_LONG_S)
                cur.execute("UPDATE trips SET quality_flag = %s "
                            "WHERE ended_at IS NULL AND quality_flag IS NULL AND started_at < %s",
                            (NEVER_ENDED, cutoff))
                if cur.rowcount:
                    log.info("%d trips closed as %s", cur.rowcount, NEVER_ENDED)
            cur.execute("SELECT COUNT(*) FROM trips WHERE ended_at IS NULL AND quality_flag IS NULL")
            (active,) = cur.fetchone()
            cur.execute("SELECT COUNT(*), COALESCE(SUM(energy_kwh), 0), COALESCE(AVG(duration_s), 0) "
                        "FROM trips WHERE source = 'mqtt' AND quality_flag = 'OK'")
            s2_trips, s2_kwh, s2_avg = cur.fetchone()
            cur.execute("SELECT COUNT(*), COALESCE(SUM(energy_kwh), 0), COALESCE(AVG(duration_s), 0) "
                        "FROM trips WHERE quality_flag = 'OK'")
            all_trips, all_kwh, all_avg = cur.fetchone()
            cur.execute("SELECT COUNT(*) FROM trips WHERE quality_flag IS NOT NULL AND quality_flag <> 'OK'")
            (flagged_trips,) = cur.fetchone()
            cur.execute("SELECT COUNT(*) FROM station_status WHERE quality_flag <> 'OK'")
            (flagged_status,) = cur.fetchone()
            cur.execute("SELECT COUNT(*) FROM rejected_messages")
            (rejected,) = cur.fetchone()
            self.conn.commit()
        except Exception:
            self._rollback()
            raise
        finally:
            cur.close()

        latest = list(self.latest.values())
        self.counters.set({
            "active_trips": int(active),
            "total_bikes_avail": sum(r["num_bikes_available"] for r in latest),
            "total_ebikes_avail": sum(r["num_ebikes_available"] for r in latest),
            "total_docks_avail": sum(r["num_docks_available"] for r in latest),
            "stations_empty": sum(1 for r in latest if r["num_bikes_available"] <= 0),
            "stations_full": sum(1 for r in latest if r["num_docks_available"] <= 0),
            "stage2_trips": int(s2_trips),
            "stage2_energy_kwh": round(float(s2_kwh), 3),
            "stage2_avg_duration_min": round(float(s2_avg) / 60, 2),
            "all_trips": int(all_trips),
            "all_energy_kwh": round(float(all_kwh), 3),
            "all_avg_duration_min": round(float(all_avg) / 60, 2),
            "flagged_trips": int(flagged_trips),
            "flagged_status": int(flagged_status),
            "rejected_messages": int(rejected),
        }, self.last_event_ts)
        self.dirty = False
