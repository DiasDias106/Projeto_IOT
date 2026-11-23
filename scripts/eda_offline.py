"""Exploratory analysis of the offline dataset (IoT Planning, Track 2, Sprint 1).

Calibrates the outlier thresholds and gives the cleaning statistics for the report. It runs every
message through the Processor's own validation (processor/app/worker.py), so the counts match
what ends up in MySQL.

Usage (standard library only, no services needed):
    python scripts/eda_offline.py                       # stage 1
    python scripts/eda_offline.py --file dataset-IdC-2627/stage2_online.jsonl
"""
from __future__ import annotations

import argparse
import bisect
import json
import statistics
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "processor"))

from app.contracts import OUTLIER_LONG_S, OUTLIER_SHORT_S, RawMessage  # noqa: E402
from app.worker import StationInfo, parse  # noqa: E402

REBALANCE_MIN_BIKES = 3   # unexplained change of at least this many bikes = operator rebalancing


def quantiles(values: list[float], qs=(0.0, 0.01, 0.05, 0.25, 0.5, 0.75, 0.95, 0.99, 0.999, 1.0)) -> str:
    v = sorted(values)
    return " | ".join(f"p{q * 100:g}={v[min(len(v) - 1, int(q * len(v)))]:.0f}" for q in qs)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", type=Path, default=ROOT / "dataset-IdC-2627" / "stage1_offline.jsonl")
    ap.add_argument("--stations", type=Path, default=ROOT / "dataset-IdC-2627" / "stations.json")
    args = ap.parse_args()

    stations = {s["station_id"]: StationInfo(s["name"], s["lat"], s["lon"], s["capacity"])
                for s in json.loads(args.stations.read_text(encoding="utf-8"))}

    kinds: Counter = Counter()
    trip_flags: Counter = Counter()
    status_flags: Counter = Counter()
    rejected: Counter = Counter()
    status_flag_by_station: Counter = Counter()
    durations: list[float] = []
    statuses: dict[str, list[tuple[datetime, int]]] = defaultdict(list)
    departures: dict[str, list[datetime]] = defaultdict(list)
    arrivals: dict[str, list[datetime]] = defaultdict(list)

    with args.file.open("rb") as f:
        for line in f:
            if not line.strip():
                continue
            r = parse(RawMessage(source="stage1", payload=line.strip()), stations)
            kinds[r.kind] += 1
            row = r.row
            if r.kind == "rejected":
                rejected[row["reason"]] += 1
            elif r.kind == "station_status":
                status_flags[row["quality_flag"]] += 1
                if row["quality_flag"] != "OK":
                    status_flag_by_station[row["station_id"]] += 1
                statuses[row["station_id"]].append((row["ts"], row["num_bikes_available"]))
            elif r.kind == "trip_ended":
                trip_flags[row["quality_flag"]] += 1
                durations.append(row["duration_s"])
                if row["start_station_id"] in stations:
                    departures[row["start_station_id"]].append(row["started_at"])
                if row["end_station_id"] in stations:
                    arrivals[row["end_station_id"]].append(row["ended_at"])

    print(f"# EDA: {args.file.name}\n")
    print("## Messages\n")
    for k, n in kinds.most_common():
        print(f"- {k}: {n}")

    print("\n## Trip duration (s), all trip_ended\n")
    print(quantiles(durations))
    print(f"\nThresholds in use: OUTLIER_SHORT < {OUTLIER_SHORT_S} s, OUTLIER_LONG > {OUTLIER_LONG_S} s")
    print(f"- under threshold: {sum(d < OUTLIER_SHORT_S for d in durations)}"
          f" ({100 * sum(d < OUTLIER_SHORT_S for d in durations) / len(durations):.2f} %)")
    print(f"- over threshold: {sum(d > OUTLIER_LONG_S for d in durations)}"
          f" ({100 * sum(d > OUTLIER_LONG_S for d in durations) / len(durations):.2f} %)")

    print("\n## Trip quality flags (first match wins)\n")
    total = sum(trip_flags.values())
    for k, n in trip_flags.most_common():
        print(f"- {k}: {n} ({100 * n / total:.2f} %)")

    print("\n## Station status quality flags\n")
    for k, n in status_flags.most_common():
        print(f"- {k}: {n}")
    print("\nStations with most flagged status rows (candidate faulty sensors):")
    for sid, n in status_flag_by_station.most_common(10):
        print(f"- {sid} ({stations[sid].name}): {n}")

    print("\n## Rejected messages\n")
    print("\n".join(f"- {k}: {n}" for k, n in rejected.most_common()) or "- none")

    print("\n## Station coverage and update gaps\n")
    gaps = []
    for sid, rows in statuses.items():
        rows.sort()
        gaps += [(b[0] - a[0]).total_seconds() / 60 for a, b in zip(rows, rows[1:])]
    print(f"- stations with status updates: {len(statuses)} of {len(stations)}")
    print(f"- stations never sending status: {sorted(set(stations) - set(statuses)) or 'none'}")
    if gaps:
        print(f"- minutes between updates of the same station: {quantiles(gaps)}")
        print(f"- median {statistics.median(gaps):.1f} min; gaps over 6 h: {sum(g > 360 for g in gaps)}")

    print("\n## Operator rebalancing (bike changes not explained by trips)\n")
    events, bikes_moved = 0, 0
    per_station: Counter = Counter()
    for sid, rows in statuses.items():
        dep, arr = sorted(departures[sid]), sorted(arrivals[sid])
        for (t0, b0), (t1, b1) in zip(rows, rows[1:]):
            net_trips = ((bisect.bisect_right(arr, t1) - bisect.bisect_right(arr, t0))
                         - (bisect.bisect_right(dep, t1) - bisect.bisect_right(dep, t0)))
            unexplained = (b1 - b0) - net_trips
            if abs(unexplained) >= REBALANCE_MIN_BIKES:
                events += 1
                bikes_moved += abs(unexplained)
                per_station[sid] += abs(unexplained)
    print(f"- events (|unexplained change| >= {REBALANCE_MIN_BIKES} bikes): {events}")
    print(f"- bikes moved: {bikes_moved}")
    print("- most rebalanced stations: "
          + ", ".join(f"{sid} ({n})" for sid, n in per_station.most_common(5)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
