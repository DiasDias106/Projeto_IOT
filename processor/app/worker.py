"""T2 Worker (design-plan.md, secção 3).

Faz parse do JSON, valida os campos obrigatórios por tipo, define o quality_flag e, para
trip_ended, calcula distância (Haversine), kWh e custo. Mete um CleanRecord na clean queue.
Nunca toca no MySQL.
"""
from __future__ import annotations

import queue
import threading

from . import calc  # noqa: F401  (usado na implementação)
from .contracts import (  # noqa: F401
    OUTLIER_LONG_S,
    OUTLIER_SHORT_S,
    QUALITY_PRIORITY,
    REQUIRED_FIELDS,
    CleanRecord,
    RawMessage,
)
from .threads import QueueConsumer


class Worker(QueueConsumer):
    def __init__(self, raw_queue: queue.Queue, clean_queue: queue.Queue,
                 station_ids: set[str], stop: threading.Event):
        super().__init__("T2-worker", raw_queue, stop)
        self.clean_queue = clean_queue
        # IDs das 83 estações (para OUT_OF_AREA). Preenchido pelo main a partir da BD.
        self.station_ids = station_ids

    def handle(self, item: RawMessage) -> None:
        record = self.process(item)
        if record is not None:
            self.clean_queue.put(record)

    def process(self, raw: RawMessage) -> CleanRecord | None:
        """Converte uma RawMessage num CleanRecord, ou None se a mensagem for inválida.

        Passos:
          1. json.loads(raw.payload); mensagens que não são JSON são descartadas (log).
          2. Verificar REQUIRED_FIELDS[msg["type"]].
          3. Converter timestamps ISO para datetime local sem offset (guardar como chega).
          4. trip_ended: distância, energia, custo e quality_flag (ver quality_flag()).
        """
        # TODO(processor/T2)
        raise NotImplementedError

    def quality_flag(self, trip: dict) -> str:
        """Primeiro flag de QUALITY_PRIORITY que se aplica:
        MISSING_END > OUT_OF_AREA > OUTLIER_SHORT > OUTLIER_LONG > OK."""
        # TODO(processor/T2)
        raise NotImplementedError
