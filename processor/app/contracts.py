"""Contracts between the Processor threads (PLAN.md, section 1).

    Bootstrap / T1 Listener --RawMessage--> raw queue --> T2 Workers --CleanRecord--> clean queue --> T3 Writer

Changing these types affects several parts: agree with the group first.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

MessageType = Literal["station_status", "trip_started", "trip_ended"]
RecordKind = Literal["station_status", "trip_started", "trip_ended", "rejected"]
Source = Literal["stage1", "mqtt"]

# Required (non-null) fields per message type. Station ids of trips are optional on purpose:
# a trip without a start or end station is a real trip and is stored with a quality_flag.
REQUIRED_FIELDS: dict[str, tuple[str, ...]] = {
    "station_status": (
        "type", "ts", "seq", "station_id",
        "num_bikes_available", "num_ebikes_available", "num_bikes_disabled",
        "num_docks_available", "num_docks_disabled",
        "is_installed", "is_renting", "is_returning",
    ),
    "trip_started": (
        "type", "ts", "seq", "ride_id", "rideable_type", "member_casual",
        "start_lat", "start_lng",
    ),
    "trip_ended": (
        "type", "ts", "seq", "ride_id", "rideable_type", "member_casual",
        "started_at", "ended_at", "duration_s", "start_lat", "start_lng",
    ),
}

RIDEABLE_TYPES = ("classic_bike", "electric_bike")
MEMBER_TYPES = ("member", "casual")

# Trip flags, fixed priority: the first that applies wins (PLAN.md, section 3)
TRIP_FLAG_PRIORITY: tuple[str, ...] = (
    "MISSING_END",
    "MISSING_START",
    "INVALID_DURATION",
    "OUT_OF_AREA",
    "OUTLIER_SHORT",
    "OUTLIER_LONG",
    "OK",
)
# Trip still open OUTLIER_LONG_S after its start (event time): closed by the Writer with this flag
NEVER_ENDED = "NEVER_ENDED"

# Station status flags, fixed priority
STATUS_FLAG_PRIORITY: tuple[str, ...] = (
    "NEGATIVE_COUNT",
    "EBIKES_GT_BIKES",
    "OVER_CAPACITY",
    "OK",
)

# Reasons for rejected_messages (dead-letter table)
REJECT_REASONS: tuple[str, ...] = (
    "OVERSIZE", "INVALID_JSON", "NOT_AN_OBJECT", "UNKNOWN_TYPE", "MISSING_FIELD",
    "INVALID_VALUE", "UNKNOWN_STATION", "TOPIC_MISMATCH", "DB_REJECTED",
)

OUTLIER_SHORT_S = 60                  # Citi Bike removes trips under 60 s as false starts
OUTLIER_LONG_S = 3 * 60 * 60
DURATION_TOLERANCE_S = 60             # duration_s vs ended_at - started_at
MAX_PAYLOAD_BYTES = 64 * 1024         # larger payloads are rejected before parsing (and stored truncated)


@dataclass
class RawMessage:
    """Unprocessed message. From the stage 1 file (source="stage1") or MQTT (source="mqtt")."""
    source: Source
    payload: bytes
    topic: str | None = None


@dataclass
class CleanRecord:
    """Row ready to write. The Writer computes nothing: it only inserts or updates.

    kind="station_status": row has the station_status columns (with quality_flag and source).
    kind="trip_started":   row has the trips columns known at the start (ended_at stays NULL).
    kind="trip_ended":     row has every trips column, including distance_km, energy_kwh,
                           cost_usd, quality_flag and source.
    kind="rejected":       row has the rejected_messages columns. seq may be None.
    """
    kind: RecordKind
    seq: int | None
    row: dict[str, Any] = field(default_factory=dict)
