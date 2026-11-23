"""T2 Workers (PLAN.md, sections 1, 3 and 4).

Parse the JSON, validate it, set quality_flag and, for trip_ended, compute distance, kWh and cost.
Every message produces exactly one CleanRecord: a valid row, or a "rejected" row for the
dead-letter table. Nothing is thrown away. Workers never touch MySQL.

Several workers can run in parallel: the Writer's inserts do not depend on message order.
"""
from __future__ import annotations

import hashlib
import json
import logging
import queue
import re
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping

from . import calc
from .contracts import (
    DURATION_TOLERANCE_S,
    MAX_PAYLOAD_BYTES,
    MEMBER_TYPES,
    OUTLIER_LONG_S,
    OUTLIER_SHORT_S,
    REQUIRED_FIELDS,
    RIDEABLE_TYPES,
    CleanRecord,
    RawMessage,
)
from .threads import QueueConsumer

log = logging.getLogger("T2-worker")

# All dataset timestamps are UTC-04:00 (no DST change between 13 and 22 March). Stored without offset.
LOCAL_TZ = timezone(timedelta(hours=-4))

STATION_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,16}$")
RIDE_ID_RE = re.compile(r"^[A-Za-z0-9]{1,32}$")
MAX_NAME_LEN = 128

STATUS_COUNTS = (
    "num_bikes_available", "num_ebikes_available", "num_bikes_disabled",
    "num_docks_available", "num_docks_disabled",
)
STATUS_BOOLS = ("is_installed", "is_renting", "is_returning")


@dataclass(frozen=True)
class StationInfo:
    name: str
    lat: float
    lon: float
    capacity: int


class Rejected(Exception):
    """The message cannot be stored in its own table: it goes to rejected_messages."""

    def __init__(self, reason: str, detail: str = ""):
        super().__init__(reason, detail)
        self.reason = reason
        # detail can echo attacker-controlled text (topic, type): keep it printable and short
        self.detail = "".join(c if c.isprintable() else "?" for c in detail)[:255]


# ---------- value parsers (raise Rejected("INVALID_VALUE")) ----------

def _int(msg: Mapping[str, Any], key: str) -> int:
    v = msg[key]
    if isinstance(v, bool) or not isinstance(v, int):
        raise Rejected("INVALID_VALUE", f"{key} is not an integer")
    return v


def _bool01(msg: Mapping[str, Any], key: str) -> int:
    v = msg[key]
    if v in (0, 1, True, False) and not isinstance(v, float):
        return int(v)
    raise Rejected("INVALID_VALUE", f"{key} is not 0 or 1")


def _coord(msg: Mapping[str, Any], key: str, limit: float) -> float:
    v = msg[key]
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not -limit <= v <= limit:
        raise Rejected("INVALID_VALUE", f"{key} is not a valid coordinate")
    return float(v)


def _opt_coord(msg: Mapping[str, Any], key: str, limit: float) -> float | None:
    return None if msg.get(key) is None else _coord(msg, key, limit)


def _ts(msg: Mapping[str, Any], key: str) -> datetime:
    v = msg[key]
    try:
        dt = datetime.fromisoformat(v)
    except (TypeError, ValueError):
        raise Rejected("INVALID_VALUE", f"{key} is not an ISO 8601 timestamp") from None
    if dt.tzinfo is not None:
        dt = dt.astimezone(LOCAL_TZ).replace(tzinfo=None)
    return dt.replace(microsecond=0)


def _station_id(msg: Mapping[str, Any], key: str) -> str | None:
    v = msg.get(key)
    if v is None or v == "":
        return None
    if not isinstance(v, str) or not STATION_ID_RE.match(v):
        raise Rejected("INVALID_VALUE", f"{key} has an invalid format")
    return v


def _name(msg: Mapping[str, Any], key: str) -> str | None:
    v = msg.get(key)
    if v is None:
        return None
    if not isinstance(v, str) or len(v) > MAX_NAME_LEN:
        raise Rejected("INVALID_VALUE", f"{key} is not a valid name")
    return v


def _enum(msg: Mapping[str, Any], key: str, allowed: tuple[str, ...]) -> str:
    v = msg[key]
    if v not in allowed:
        raise Rejected("INVALID_VALUE", f"{key} is not one of {', '.join(allowed)}")
    return v


# ---------- quality flags (first match wins) ----------

def status_flag(row: Mapping[str, Any], capacity: int) -> str:
    if any(row[k] < 0 for k in STATUS_COUNTS):
        return "NEGATIVE_COUNT"
    if row["num_ebikes_available"] > row["num_bikes_available"]:
        return "EBIKES_GT_BIKES"
    if sum(row[k] for k in STATUS_COUNTS if k != "num_ebikes_available") > capacity:
        return "OVER_CAPACITY"
    return "OK"


def trip_flag(row: Mapping[str, Any], known_stations: Mapping[str, Any]) -> str:
    if row["end_station_id"] is None:
        return "MISSING_END"
    if row["start_station_id"] is None:
        return "MISSING_START"
    duration = row["duration_s"]
    elapsed = (row["ended_at"] - row["started_at"]).total_seconds()
    if duration <= 0 or elapsed < 0 or abs(elapsed - duration) > DURATION_TOLERANCE_S:
        return "INVALID_DURATION"
    if row["start_station_id"] not in known_stations or row["end_station_id"] not in known_stations:
        return "OUT_OF_AREA"
    if duration < OUTLIER_SHORT_S:
        return "OUTLIER_SHORT"
    if duration > OUTLIER_LONG_S:
        return "OUTLIER_LONG"
    return "OK"


# ---------- message -> record ----------

def _expected_type(topic: str | None) -> tuple[str | None, str | None]:
    """(message type, station_id) implied by the MQTT topic, or (None, None) if unknown."""
    if not topic:
        return None, None
    parts = topic.split("/")
    if len(parts) >= 2 and parts[-2] == "status":
        return "station_status", parts[-1]
    if parts[-2:] == ["trips", "started"]:
        return "trip_started", None
    if parts[-2:] == ["trips", "ended"]:
        return "trip_ended", None
    return None, None


def parse(raw: RawMessage, stations: Mapping[str, StationInfo]) -> CleanRecord:
    """Turn one raw message into one CleanRecord (valid row or rejected row). Never raises."""
    msg: Any = None
    try:
        if len(raw.payload) > MAX_PAYLOAD_BYTES:
            raise Rejected("OVERSIZE", f"{len(raw.payload)} bytes")
        try:
            msg = json.loads(raw.payload)
        except (UnicodeDecodeError, ValueError):
            raise Rejected("INVALID_JSON") from None
        if not isinstance(msg, dict):
            raise Rejected("NOT_AN_OBJECT")
        mtype = msg.get("type")
        if mtype not in REQUIRED_FIELDS:
            raise Rejected("UNKNOWN_TYPE", f"type={str(mtype)[:32]}")
        missing = [k for k in REQUIRED_FIELDS[mtype] if msg.get(k) is None]
        if missing:
            raise Rejected("MISSING_FIELD", ", ".join(missing))

        if raw.source == "mqtt":
            topic_type, topic_station = _expected_type(raw.topic)
            if topic_type != mtype:
                raise Rejected("TOPIC_MISMATCH", f"type {mtype} on topic {raw.topic}")
            if topic_station is not None and topic_station != msg.get("station_id"):
                raise Rejected("TOPIC_MISMATCH", "station_id differs from topic")

        seq = _int(msg, "seq")
        if seq <= 0:
            raise Rejected("INVALID_VALUE", "seq must be positive")

        if mtype == "station_status":
            return _station_status(msg, seq, raw.source, stations)
        if mtype == "trip_started":
            return _trip_started(msg, seq, raw.source)
        return _trip_ended(msg, seq, raw.source, stations)
    except Rejected as rej:
        return rejected_record(raw, rej.reason, rej.detail, msg)


def _station_status(msg, seq, source, stations) -> CleanRecord:
    station_id = _station_id(msg, "station_id")
    if station_id is None:
        raise Rejected("MISSING_FIELD", "station_id")
    info = stations.get(station_id)
    if info is None:
        raise Rejected("UNKNOWN_STATION", station_id)
    row: dict[str, Any] = {"seq": seq, "station_id": station_id, "ts": _ts(msg, "ts")}
    for k in STATUS_COUNTS:
        row[k] = _int(msg, k)
        if not -32768 <= row[k] <= 32767:
            raise Rejected("INVALID_VALUE", f"{k} out of range")
    for k in STATUS_BOOLS:
        row[k] = _bool01(msg, k)
    row["quality_flag"] = status_flag(row, info.capacity)
    row["source"] = source
    return CleanRecord(kind="station_status", seq=seq, row=row)


def _trip_common(msg) -> dict[str, Any]:
    ride_id = msg["ride_id"]
    if not isinstance(ride_id, str) or not RIDE_ID_RE.match(ride_id):
        raise Rejected("INVALID_VALUE", "ride_id has an invalid format")
    return {
        "trip_id": ride_id,
        "rideable_type": _enum(msg, "rideable_type", RIDEABLE_TYPES),
        "member_casual": _enum(msg, "member_casual", MEMBER_TYPES),
        "start_station_id": _station_id(msg, "start_station_id"),
        "start_station_name": _name(msg, "start_station_name"),
        "start_lat": _coord(msg, "start_lat", 90),
        "start_lng": _coord(msg, "start_lng", 180),
    }


def _trip_started(msg, seq, source) -> CleanRecord:
    row = _trip_common(msg)
    row["started_at"] = _ts(msg, "ts")
    row["seq_start"] = seq
    return CleanRecord(kind="trip_started", seq=seq, row=row)


def _trip_ended(msg, seq, source, stations) -> CleanRecord:
    row = _trip_common(msg)
    row.update(
        started_at=_ts(msg, "started_at"),
        ended_at=_ts(msg, "ended_at"),
        duration_s=_int(msg, "duration_s"),
        end_station_id=_station_id(msg, "end_station_id"),
        end_station_name=_name(msg, "end_station_name"),
        end_lat=_opt_coord(msg, "end_lat", 90),
        end_lng=_opt_coord(msg, "end_lng", 180),
        seq_end=seq,
        source=source,
    )
    if not -2**31 <= row["duration_s"] < 2**31:
        raise Rejected("INVALID_VALUE", "duration_s out of range")

    # Spec: distance between the start and end stations. Use the reference coordinates when both
    # stations are known (round trips give exactly 0), otherwise the coordinates in the message.
    start, end = stations.get(row["start_station_id"] or ""), stations.get(row["end_station_id"] or "")
    if start and end:
        distance = calc.haversine_km(start.lat, start.lon, end.lat, end.lon)
    elif row["end_lat"] is not None and row["end_lng"] is not None:
        distance = calc.haversine_km(row["start_lat"], row["start_lng"], row["end_lat"], row["end_lng"])
    else:
        distance = None
    row["distance_km"] = None if distance is None else round(distance, 4)
    energy = calc.energy_kwh(row["rideable_type"], distance)
    row["energy_kwh"] = None if energy is None else round(energy, 6)
    row["cost_usd"] = (calc.trip_cost_usd(row["member_casual"], row["rideable_type"], row["duration_s"])
                       if row["duration_s"] >= 0 else None)
    row["quality_flag"] = trip_flag(row, stations)
    return CleanRecord(kind="trip_ended", seq=seq, row=row)


def rejected_record(raw: RawMessage, reason: str, detail: str = "", msg: Any = None) -> CleanRecord:
    """Dead-letter row. Fields are taken from the payload only when they look safe."""
    fields = msg if isinstance(msg, dict) else {}
    seq = fields.get("seq")
    seq = seq if isinstance(seq, int) and not isinstance(seq, bool) and 0 < seq < 2**63 else None
    mtype = fields.get("type")
    mtype = mtype if isinstance(mtype, str) and len(mtype) <= 32 else None

    station_id = None
    for key in ("station_id", "start_station_id"):
        v = fields.get(key)
        if isinstance(v, str) and STATION_ID_RE.match(v):
            station_id = v
            break
    if station_id is None:
        _, topic_station = _expected_type(raw.topic)
        if topic_station and STATION_ID_RE.match(topic_station):
            station_id = topic_station

    event_ts = None
    try:
        event_ts = _ts(fields, "ts") if fields.get("ts") is not None else None
    except Rejected:
        pass

    row = {
        "received_at": datetime.now(timezone.utc).replace(tzinfo=None),
        "source": raw.source,
        "topic": raw.topic[:255] if raw.topic else None,
        "msg_type": mtype,
        "seq": seq,
        "station_id": station_id,
        "event_ts": event_ts,
        "reason": reason,
        "detail": detail[:255] or None,
        "payload": raw.payload[:MAX_PAYLOAD_BYTES],
        "payload_sha256": hashlib.sha256(raw.payload).hexdigest(),
    }
    return CleanRecord(kind="rejected", seq=seq, row=row)


class Worker(QueueConsumer):
    def __init__(self, index: int, raw_queue: queue.Queue, clean_queue: queue.Queue,
                 stations: Mapping[str, StationInfo], stop: threading.Event):
        super().__init__(f"T2-worker-{index}", raw_queue, stop)
        self.clean_queue = clean_queue
        # The 83 reference stations (for OUT_OF_AREA, capacity and coordinates). Filled by main.
        self.stations = stations

    def handle(self, item: RawMessage) -> None:
        record = parse(item, self.stations)
        if record.kind == "rejected":
            self.log.info("rejected %s message seq=%s: %s %s", item.source, record.seq,
                          record.row["reason"], record.row["detail"] or "")
        self.clean_queue.put(record)
