"""Per-station features from MySQL (design-plan.md, section 5).

Trips: only quality_flag = 'OK'. Station status: every row (flagged rows still describe the state).
"""
from __future__ import annotations

from datetime import datetime

import numpy as np
import pandas as pd

FEATURE_COLUMNS = [
    "trips_per_dock_per_day",   # turnover
    "net_flow",                 # (arrivals - departures) per day
    "mean_duration_s",
    "pct_ebike",
    "pct_casual",
    "occupancy_rate",           # bikes / capacity, time-weighted
    "pct_time_empty",
    "pct_time_full",
]


def _read(conn, sql: str) -> pd.DataFrame:
    cur = conn.cursor()
    try:
        cur.execute(sql)
        cols = [c[0] for c in cur.description]
        return pd.DataFrame(cur.fetchall(), columns=cols)
    finally:
        cur.close()


def build_features(conn) -> tuple[pd.DataFrame, datetime | None]:
    """(DataFrame indexed by station_id with FEATURE_COLUMNS, newest event time in the data).

    Notes:
      - station_status arrives only on change: each row is valid until the next row of the same
        station; the last row is valid until the newest event time (forward fill over gaps).
      - num_ebikes_available is part of num_bikes_available.
    """
    stations = _read(conn, "SELECT station_id, capacity FROM stations").set_index("station_id")
    trips = _read(conn, "SELECT start_station_id, end_station_id, started_at, ended_at, duration_s, "
                        "rideable_type, member_casual FROM trips WHERE quality_flag = 'OK'")
    status = _read(conn, "SELECT station_id, ts, num_bikes_available, num_docks_available "
                         "FROM station_status ORDER BY station_id, seq")
    conn.commit()

    ends = [pd.to_datetime(trips["ended_at"]).max() if len(trips) else None,
            pd.to_datetime(status["ts"]).max() if len(status) else None]
    ends = [t for t in ends if t is not None and not pd.isna(t)]
    if not ends:
        return pd.DataFrame(columns=FEATURE_COLUMNS), None
    t_end = max(ends)
    starts = [pd.to_datetime(trips["started_at"]).min() if len(trips) else t_end,
              pd.to_datetime(status["ts"]).min() if len(status) else t_end]
    days = max((t_end - min(starts)).total_seconds() / 86400, 1 / 24)

    f = pd.DataFrame(index=stations.index)
    dep = trips.groupby("start_station_id")
    arrivals = trips.groupby("end_station_id").size()
    departures = dep.size()
    f["trips_per_dock_per_day"] = departures.reindex(f.index, fill_value=0) / stations["capacity"].clip(lower=1) / days
    f["net_flow"] = (arrivals.reindex(f.index, fill_value=0) - departures.reindex(f.index, fill_value=0)) / days
    f["mean_duration_s"] = dep["duration_s"].mean().reindex(f.index)
    f["pct_ebike"] = dep["rideable_type"].apply(lambda s: (s == "electric_bike").mean()).reindex(f.index)
    f["pct_casual"] = dep["member_casual"].apply(lambda s: (s == "casual").mean()).reindex(f.index)

    if len(status):
        st = status.copy()
        st["ts"] = pd.to_datetime(st["ts"])
        st["next_ts"] = st.groupby("station_id")["ts"].shift(-1).fillna(t_end)
        st["w"] = (st["next_ts"] - st["ts"]).dt.total_seconds().clip(lower=0)
        st = st.join(stations, on="station_id")
        st["occ"] = (st["num_bikes_available"] / st["capacity"].clip(lower=1)).clip(0, 1)
        st["empty"] = (st["num_bikes_available"] <= 0).astype(float)
        st["full"] = (st["num_docks_available"] <= 0).astype(float)
        g = st.groupby("station_id")
        wsum = g["w"].sum().replace(0, np.nan)
        for col, name in (("occ", "occupancy_rate"), ("empty", "pct_time_empty"), ("full", "pct_time_full")):
            f[name] = ((st[col] * st["w"]).groupby(st["station_id"]).sum() / wsum).reindex(f.index)

    f = f.reindex(columns=FEATURE_COLUMNS).astype(float).fillna(0.0)
    return f, t_end.to_pydatetime()
