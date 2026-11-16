"""Features por estação a partir do MySQL (design-plan.md, secção 5).

Só viagens com quality_flag = 'OK'.
"""
from __future__ import annotations

import pandas as pd

FEATURE_COLUMNS = [
    "trips_per_dock_per_day",   # turnover
    "net_flow",                 # chegadas - partidas
    "mean_duration_s",
    "pct_ebike",
    "pct_casual",
    "occupancy_rate",           # bikes / capacity, ponderado pelo tempo
    "pct_time_empty",
    "pct_time_full",
]


def build_features(conn) -> pd.DataFrame:
    """DataFrame indexado por station_id com as colunas FEATURE_COLUMNS.

    Notas:
      - station_status só chega quando o estado muda: cada linha vale até à seguinte da mesma
        estação (tempo vazio/cheio = soma desses intervalos).
      - num_ebikes_available está incluído em num_bikes_available.
    Devolve também, se útil, o tempo de evento máximo dos dados (para trained_at).
    """
    # TODO(ml)
    raise NotImplementedError
