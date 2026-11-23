import json

import pytest

from app.contracts import MAX_PAYLOAD_BYTES, RawMessage
from app.worker import StationInfo, parse, status_flag

STATIONS = {
    "HB101": StationInfo("Hoboken Terminal", 40.73594, -74.03030, 24),
    "JC115": StationInfo("Grove St PATH", 40.71959, -74.04312, 20),
}

STATUS = {"type": "station_status", "ts": "2024-03-13T00:07:33-04:00", "station_id": "HB101",
          "num_bikes_available": 13, "num_ebikes_available": 3, "num_bikes_disabled": 1,
          "num_docks_available": 10, "num_docks_disabled": 0, "is_installed": 1, "is_renting": 1,
          "is_returning": 1, "seq": 1}
STARTED = {"type": "trip_started", "ts": "2024-03-13T00:19:07-04:00", "ride_id": "5B1C230EE0D7DE58",
           "rideable_type": "electric_bike", "member_casual": "member", "start_station_id": "HB101",
           "start_station_name": "Hoboken Terminal", "start_lat": 40.7359, "start_lng": -74.0303, "seq": 9}
ENDED = {"type": "trip_ended", "ts": "2024-03-13T00:29:07-04:00", "ride_id": "5B1C230EE0D7DE58",
         "rideable_type": "electric_bike", "member_casual": "member",
         "started_at": "2024-03-13T00:19:07-04:00", "ended_at": "2024-03-13T00:29:07-04:00",
         "duration_s": 600, "start_station_id": "HB101", "start_station_name": "Hoboken Terminal",
         "end_station_id": "JC115", "end_station_name": "Grove St PATH", "start_lat": 40.7359,
         "start_lng": -74.0303, "end_lat": 40.7196, "end_lng": -74.0431, "seq": 10}


def run(msg, source="stage1", topic=None, raw=None):
    payload = raw if raw is not None else json.dumps(msg).encode()
    return parse(RawMessage(source=source, payload=payload, topic=topic), STATIONS)


def with_(base, **changes):
    msg = dict(base)
    for k, v in changes.items():
        if v is ...:
            msg.pop(k)
        else:
            msg[k] = v
    return msg


# ---------- valid messages ----------

def test_status_ok():
    r = run(STATUS)
    assert r.kind == "station_status"
    assert r.row["quality_flag"] == "OK"
    assert r.row["ts"].isoformat() == "2024-03-13T00:07:33"
    assert r.row["source"] == "stage1"


def test_trip_started():
    r = run(STARTED)
    assert r.kind == "trip_started"
    assert r.row["trip_id"] == "5B1C230EE0D7DE58"
    assert r.row["seq_start"] == 9


def test_trip_ended_ok_uses_station_coordinates():
    r = run(ENDED)
    assert r.kind == "trip_ended"
    assert r.row["quality_flag"] == "OK"
    assert r.row["distance_km"] > 2
    assert r.row["energy_kwh"] == pytest.approx(r.row["distance_km"] * 0.015, rel=1e-3)
    assert r.row["cost_usd"] == 2.50


def test_round_trip_is_zero_km():
    r = run(with_(ENDED, end_station_id="HB101", end_lat=40.7361, end_lng=-74.0301))
    assert r.row["distance_km"] == 0.0
    assert r.row["energy_kwh"] == 0.0


def test_utc_timestamp_is_converted_to_local():
    r = run(with_(STATUS, ts="2024-03-13T04:07:33Z"))
    assert r.row["ts"].isoformat() == "2024-03-13T00:07:33"


# ---------- quality flags (kept in the table) ----------

@pytest.mark.parametrize("changes, flag", [
    ({"end_station_id": None}, "MISSING_END"),
    ({"end_station_id": ..., "end_lat": ..., "end_lng": ...}, "MISSING_END"),
    ({"start_station_id": None}, "MISSING_START"),
    ({"duration_s": 0}, "INVALID_DURATION"),
    ({"duration_s": 5000}, "INVALID_DURATION"),
    ({"end_station_id": "5329.03"}, "OUT_OF_AREA"),
    ({"ended_at": "2024-03-13T00:19:37-04:00", "duration_s": 30}, "OUTLIER_SHORT"),
    ({"ended_at": "2024-03-13T04:19:07-04:00", "duration_s": 4 * 3600}, "OUTLIER_LONG"),
])
def test_trip_flags(changes, flag):
    r = run(with_(ENDED, **changes))
    assert r.kind == "trip_ended"
    assert r.row["quality_flag"] == flag


def test_missing_end_without_coordinates_has_no_distance():
    r = run(with_(ENDED, end_station_id=..., end_lat=..., end_lng=...))
    assert r.row["distance_km"] is None
    assert r.row["energy_kwh"] is None
    assert r.row["cost_usd"] == 2.50


def test_trip_started_without_station_is_kept():
    r = run(with_(STARTED, start_station_id=..., start_station_name=...))
    assert r.kind == "trip_started"
    assert r.row["start_station_id"] is None


@pytest.mark.parametrize("changes, flag", [
    ({"num_bikes_available": -1}, "NEGATIVE_COUNT"),
    ({"num_ebikes_available": 14}, "EBIKES_GT_BIKES"),
    ({"num_docks_available": 11}, "OVER_CAPACITY"),
])
def test_status_flags(changes, flag):
    r = run(with_(STATUS, **changes))
    assert r.kind == "station_status"
    assert r.row["quality_flag"] == flag


def test_status_flag_priority():
    row = dict(num_bikes_available=-1, num_ebikes_available=5, num_bikes_disabled=0,
               num_docks_available=99, num_docks_disabled=0)
    assert status_flag(row, 10) == "NEGATIVE_COUNT"


# ---------- rejected (dead-letter) ----------

@pytest.mark.parametrize("raw, reason", [
    (b"not json", "INVALID_JSON"),
    (b"\xff\xfe", "INVALID_JSON"),
    (b"[1, 2]", "NOT_AN_OBJECT"),
    (json.dumps({"type": "hello", "seq": 1}).encode(), "UNKNOWN_TYPE"),
    (b"{" + b" " * MAX_PAYLOAD_BYTES + b"}", "OVERSIZE"),
])
def test_rejected_raw(raw, reason):
    r = run(None, raw=raw)
    assert r.kind == "rejected"
    assert r.row["reason"] == reason
    assert r.row["payload"] == raw[:MAX_PAYLOAD_BYTES]
    assert len(r.row["payload_sha256"]) == 64


@pytest.mark.parametrize("base, changes, reason", [
    (STATUS, {"num_docks_available": ...}, "MISSING_FIELD"),
    (STATUS, {"num_docks_available": None}, "MISSING_FIELD"),
    (STATUS, {"station_id": "XX999"}, "UNKNOWN_STATION"),
    (STATUS, {"num_bikes_available": "3"}, "INVALID_VALUE"),
    (STATUS, {"is_renting": 2}, "INVALID_VALUE"),
    (STATUS, {"ts": "yesterday"}, "INVALID_VALUE"),
    (STATUS, {"seq": -4}, "INVALID_VALUE"),
    (STATUS, {"station_id": "JC'; DROP TABLE trips;--"}, "INVALID_VALUE"),
    (ENDED, {"rideable_type": "scooter"}, "INVALID_VALUE"),
    (ENDED, {"start_lat": 123.0}, "INVALID_VALUE"),
    (ENDED, {"ride_id": "x" * 40}, "INVALID_VALUE"),
])
def test_rejected_fields(base, changes, reason):
    r = run(with_(base, **changes))
    assert r.kind == "rejected"
    assert r.row["reason"] == reason


def test_rejected_keeps_station_and_seq_for_the_dashboard():
    r = run(with_(STATUS, num_bikes_available="broken"))
    assert r.row["station_id"] == "HB101"
    assert r.row["seq"] == 1
    assert r.row["msg_type"] == "station_status"
    assert r.row["event_ts"].isoformat() == "2024-03-13T00:07:33"


# ---------- MQTT topic checks ----------

def test_topic_must_match_station():
    r = run(STATUS, source="mqtt", topic="idc/fc15/status/JC115")
    assert r.kind == "rejected" and r.row["reason"] == "TOPIC_MISMATCH"
    assert r.row["station_id"] == "HB101"


def test_topic_must_match_type():
    r = run(ENDED, source="mqtt", topic="idc/fc15/trips/started")
    assert r.kind == "rejected" and r.row["reason"] == "TOPIC_MISMATCH"


def test_topic_ok():
    assert run(STATUS, source="mqtt", topic="idc/fc15/status/HB101").kind == "station_status"
    assert run(STARTED, source="mqtt", topic="idc/fc15/trips/started").kind == "trip_started"
    r = run(ENDED, source="mqtt", topic="idc/fc15/trips/ended")
    assert r.kind == "trip_ended" and r.row["source"] == "mqtt"


def test_detail_is_sanitised():
    r = run(with_(STATUS, type="bad\ntype\x00"))
    assert "\n" not in r.row["detail"] and "\x00" not in r.row["detail"]


# ---------- whole dataset ----------

def test_dataset_messages_are_never_rejected():
    import os
    base = os.path.join(os.path.dirname(__file__), "..", "..", "dataset-IdC-2627")
    stations = {s["station_id"]: StationInfo(s["name"], s["lat"], s["lon"], s["capacity"])
                for s in json.load(open(os.path.join(base, "stations.json")))}
    kinds = {}
    for name in ("stage1_offline.jsonl", "stage2_online.jsonl"):
        with open(os.path.join(base, name), "rb") as f:
            for line in f:
                r = parse(RawMessage(source="stage1", payload=line.strip()), stations)
                kinds[r.kind] = kinds.get(r.kind, 0) + 1
    assert "rejected" not in kinds
    assert sum(kinds.values()) == 53_724 + 19_856
