"""T3 Writer (design-plan.md, secção 3 e 4).

Dono da única ligação ao MySQL. Inserts em batch e idempotentes. Depois de cada commit atualiza os
LiveCounters e publica idc/fc15/internal/status/<station_id>. Não calcula nada.

Regras:
  - station_status: INSERT IGNORE (seq é UNIQUE: duplicados de QoS 1 são ignorados).
  - trip_started: cria a linha em trips com ended_at = NULL.
  - trip_ended: completa a linha; se não existir (começou antes do stage 1), insere-a já completa.
  - Viagens abertas há mais de OUTLIER_LONG_S (tempo de evento): fechar com quality_flag = NEVER_ENDED.
  - Durante o bootstrap do stage 1 o cliente MQTT ainda não está ligado (o T1 só arranca depois).
    Não publicar internal/status nessa fase (por exemplo, só quando self.mqtt.is_connected()), ou
    ficam cerca de 16 500 mensagens QoS 1 em fila para o Node-RED.
"""
from __future__ import annotations

import json  # noqa: F401  (usado na implementação)
import queue
import threading

import paho.mqtt.client as mqtt

from . import db
from .config import Config
from .contracts import CleanRecord
from .counters import LiveCounters
from .threads import QueueConsumer


class Writer(QueueConsumer):
    def __init__(self, cfg: Config, clean_queue: queue.Queue, counters: LiveCounters,
                 mqtt_client: mqtt.Client, stop: threading.Event):
        super().__init__("T3-writer", clean_queue, stop)
        self.cfg = cfg
        self.counters = counters
        self.mqtt = mqtt_client
        self.conn = None
        self.batch: list[CleanRecord] = []

    def run(self) -> None:
        self.conn = db.connect(self.cfg)
        super().run()

    def handle(self, item: CleanRecord) -> None:
        """Acumula no batch e faz flush quando chegar ao tamanho definido."""
        # TODO(processor/T3)
        raise NotImplementedError

    def on_idle(self) -> None:
        """Fila vazia: fazer flush do que estiver no batch."""
        # TODO(processor/T3): if self.batch: self.flush()

    def flush(self) -> None:
        """Escreve o batch numa transação, faz commit, atualiza self.counters e publica
        cada station_status em self.cfg.topic_internal_status(station_id)."""
        # TODO(processor/T3)
        raise NotImplementedError
