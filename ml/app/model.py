"""Station clustering (design-plan.md, section 5).

StandardScaler + K-Means for k = 3, 4, 5; the best silhouette wins. Clusters are renumbered by
turnover (0 = least used), so a cluster number keeps its meaning across trainings.
Each training writes a new set of rows in station_clusters (key station_id, trained_at).
"""
from __future__ import annotations

import json
from datetime import datetime

import pandas as pd
from sklearn.cluster import KMeans
from sklearn.metrics import silhouette_score
from sklearn.preprocessing import StandardScaler

from .features import FEATURE_COLUMNS

K_CANDIDATES = (3, 4, 5)


def fit(features: pd.DataFrame) -> tuple[int, float, pd.Series, pd.DataFrame]:
    """(k, silhouette, cluster per station, centroids in original units)."""
    x = StandardScaler().fit_transform(features[FEATURE_COLUMNS])
    best = None
    for k in K_CANDIDATES:
        if k >= len(features):
            continue
        labels = KMeans(n_clusters=k, n_init=10, random_state=42).fit_predict(x)
        score = float(silhouette_score(x, labels))
        if best is None or score > best[1]:
            best = (k, score, labels)
    if best is None:
        raise ValueError("not enough stations to cluster")
    k, score, labels = best
    raw = pd.Series(labels, index=features.index)
    order = features.groupby(raw)["trips_per_dock_per_day"].mean().sort_values().index
    remap = {old: new for new, old in enumerate(order)}
    clusters = raw.map(remap)
    centroids = features.groupby(clusters)[FEATURE_COLUMNS].mean()
    return k, score, clusters, centroids


def train_and_store(conn, data: tuple[pd.DataFrame, datetime | None]) -> dict:
    features, trained_at = data
    if trained_at is None or features.empty:
        raise ValueError("no data to train on")
    k, score, clusters, centroids = fit(features)
    trained_at = trained_at.replace(microsecond=0)
    rows = [(sid, trained_at, int(clusters[sid]), k, round(score, 4),
             json.dumps({c: round(float(features.at[sid, c]), 4) for c in FEATURE_COLUMNS}))
            for sid in features.index]
    cur = conn.cursor()
    try:
        # Same event time as an earlier training (no new data): replace that set
        cur.execute("DELETE FROM station_clusters WHERE trained_at = %s", (trained_at,))
        cur.executemany("INSERT INTO station_clusters (station_id, trained_at, cluster, k, silhouette, features) "
                        "VALUES (%s, %s, %s, %s, %s, %s)", rows)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
    return {
        "status": "trained",
        "trained_at": trained_at.isoformat(),
        "k": k,
        "silhouette_score": round(score, 4),
        "stations": len(rows),
        "cluster_sizes": {int(c): int(n) for c, n in clusters.value_counts().sort_index().items()},
        "centroids": {int(c): {f: round(float(v), 4) for f, v in row.items()} for c, row in centroids.iterrows()},
    }


def latest_clusters(conn) -> list[dict]:
    cur = conn.cursor(dictionary=True)
    try:
        cur.execute("SELECT c.station_id, s.name, s.lat, s.lon, c.cluster, c.k, c.silhouette, c.trained_at, c.features "
                    "FROM station_clusters c JOIN stations s USING (station_id) "
                    "WHERE c.trained_at = (SELECT MAX(trained_at) FROM station_clusters) ORDER BY c.station_id")
        rows = cur.fetchall()
        conn.commit()
    finally:
        cur.close()
    for r in rows:
        r["trained_at"] = r["trained_at"].isoformat()
        if isinstance(r["features"], (str, bytes)):
            r["features"] = json.loads(r["features"])
    return rows
