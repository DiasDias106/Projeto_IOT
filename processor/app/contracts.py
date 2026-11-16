"""Contratos entre as threads do Processor (design-plan.md, secção 3).

    Bootstrap / T1 Listener --RawMessage--> raw queue --> T2 Worker --CleanRecord--> clean queue --> T3 Writer

Alterar estes tipos afeta várias partes: combinar com o grupo antes.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

MessageType = Literal["station_status", "trip_started", "trip_ended"]

# Campos obrigatórios por tipo de mensagem (confirmados no dataset)
REQUIRED_FIELDS: dict[str, tuple[str, ...]] = {
    "station_status": (
        "type", "ts", "seq", "station_id",
        "num_bikes_available", "num_ebikes_available", "num_bikes_disabled",
        "num_docks_available", "num_docks_disabled",
        "is_installed", "is_renting", "is_returning",
    ),
    "trip_started": (
        "type", "ts", "seq", "ride_id", "rideable_type", "member_casual",
        "start_station_id", "start_lat", "start_lng",
    ),
    "trip_ended": (
        "type", "ts", "seq", "ride_id", "rideable_type", "member_casual",
        "started_at", "ended_at", "duration_s",
        "start_station_id", "start_lat", "start_lng",
        # end_station_id / end_lat / end_lng podem faltar -> quality_flag = MISSING_END
    ),
}

# Prioridade fixa: o primeiro que se aplica ganha (design-plan.md, secção 4, ponto 4)
QUALITY_PRIORITY: tuple[str, ...] = (
    "MISSING_END",
    "OUT_OF_AREA",
    "OUTLIER_SHORT",
    "OUTLIER_LONG",
    "OK",
)
# Viagem aberta para lá do limite de OUTLIER_LONG: fechada pelo Writer/Scheduler com este flag
NEVER_ENDED = "NEVER_ENDED"

# TODO(grupo): fixar depois da análise exploratória do stage 1 (design-plan.md, secção 7, ponto 2)
OUTLIER_SHORT_S = 60
OUTLIER_LONG_S = 3 * 60 * 60


@dataclass
class RawMessage:
    """Mensagem por tratar. Vem do ficheiro do stage 1 (source="stage1") ou do MQTT (source="mqtt")."""
    source: Literal["stage1", "mqtt"]
    payload: bytes
    topic: str | None = None


@dataclass
class CleanRecord:
    """Linha pronta a escrever. O Writer não calcula nada: só insere/atualiza.

    kind="station_status": row tem as colunas de station_status.
    kind="trip_started":   row tem as colunas de trips conhecidas no início (ended_at fica NULL).
    kind="trip_ended":     row tem todas as colunas de trips, incluindo distance_km, energy_kwh,
                           cost_usd e quality_flag.
    """
    kind: MessageType
    seq: int
    row: dict[str, Any] = field(default_factory=dict)
