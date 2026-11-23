"""Main / Bootstrap helpers (PLAN.md, section 2).

Stage 1 is loaded until the end exactly once: ingest_log holds a row per dataset file that was
fully committed. A crash in the middle leaves no row, so the next start feeds the file again; the
Writer's idempotent inserts make that safe. The orchestration (order, waiting for the queues) is
in main.py.
"""
from __future__ import annotations

import hashlib
import json
import logging
import queue
from datetime import datetime, timezone

from .contracts import RawMessage
from .worker import StationInfo

log = logging.getLogger("bootstrap")

STAGE1 = "stage1_offline.jsonl"
STATIONS = "stations.json"


def file_sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def is_loaded(conn, dataset: str, sha256: str) -> bool:
    """True if this exact file (same SHA-256) was already loaded to the end."""
    cur = conn.cursor()
    try:
        cur.execute("SELECT sha256 FROM ingest_log WHERE dataset = %s", (dataset,))
        row = cur.fetchone()
        conn.commit()
    finally:
        cur.close()
    if row is None:
        return False
    if row[0] != sha256:
        log.warning("%s changed since it was loaded (SHA-256 differs): loading it again", dataset)
        return False
    return True


def mark_loaded(conn, dataset: str, sha256: str, messages: int) -> None:
    cur = conn.cursor()
    try:
        cur.execute(
            "INSERT INTO ingest_log (dataset, sha256, messages, completed_at) VALUES (%s, %s, %s, %s) "
            "AS new ON DUPLICATE KEY UPDATE sha256 = new.sha256, messages = new.messages, "
            "completed_at = new.completed_at",
            (dataset, sha256, messages, datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0)))
        conn.commit()
    finally:
        cur.close()


def load_stations(conn, path: str) -> int:
    """Upsert the reference stations (idempotent). Returns the number of stations in the file."""
    with open(path, encoding="utf-8") as f:
        stations = json.load(f)
    rows = [(s["station_id"], s["station_uuid"], s["name"], float(s["lat"]), float(s["lon"]),
             int(s["capacity"]), s["city"]) for s in stations]
    cur = conn.cursor()
    try:
        cur.executemany(
            "INSERT INTO stations (station_id, station_uuid, name, lat, lon, capacity, city) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s) AS new ON DUPLICATE KEY UPDATE "
            "station_uuid = new.station_uuid, name = new.name, lat = new.lat, lon = new.lon, "
            "capacity = new.capacity, city = new.city",
            rows)
        conn.commit()
    finally:
        cur.close()
    log.info("%d stations loaded", len(rows))
    return len(rows)


def read_stations(conn) -> dict[str, StationInfo]:
    cur = conn.cursor()
    try:
        cur.execute("SELECT station_id, name, lat, lon, capacity FROM stations")
        result = {r[0]: StationInfo(r[1], float(r[2]), float(r[3]), int(r[4])) for r in cur.fetchall()}
        conn.commit()
    finally:
        cur.close()
    return result


def feed_stage1(raw_queue: queue.Queue, path: str) -> int:
    """Put every line of the file in the raw queue as RawMessage(source="stage1").

    Lines go through the same T2 Workers and T3 Writer as stage 2, so both stages get the same
    rules. Blank lines are skipped. Returns the number of messages sent.
    """
    n = 0
    with open(path, "rb") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            raw_queue.put(RawMessage(source="stage1", payload=line))
            n += 1
    return n
