"""T1 Listener: só recebe (design-plan.md, secção 3).

Subscreve status/+ e trips/+ e mete o payload em bruto na raw queue. Não faz parse, não calcula,
não toca no MySQL.
"""
from __future__ import annotations

import logging
import queue
import threading

import paho.mqtt.client as mqtt

from .config import Config
from .contracts import RawMessage

log = logging.getLogger("T1-listener")


class Listener(threading.Thread):
    def __init__(self, cfg: Config, client: mqtt.Client, raw_queue: queue.Queue):
        super().__init__(name="T1-listener", daemon=True)
        self.cfg = cfg
        self.client = client
        self.raw_queue = raw_queue
        client.on_connect = self._on_connect
        client.on_disconnect = self._on_disconnect
        client.on_message = self._on_message

    def _on_connect(self, client, userdata, flags, reason_code, properties):
        if reason_code.is_failure:
            log.error("ligação ao broker recusada: %s", reason_code)
            return
        log.info("ligado ao broker; a subscrever")
        # Subscrever no on_connect garante nova subscrição depois de cada reconexão
        client.subscribe([(self.cfg.topic_status_sub, 1), (self.cfg.topic_trips_sub, 1)])

    def _on_disconnect(self, client, userdata, flags, reason_code, properties):
        log.warning("desligado do broker (%s); a tentar reconectar", reason_code)

    def _on_message(self, client, userdata, msg):
        self.raw_queue.put(RawMessage(source="mqtt", payload=msg.payload, topic=msg.topic))

    def run(self) -> None:
        self.client.connect(self.cfg.mqtt_host, self.cfg.mqtt_port, keepalive=60)
        self.client.loop_forever(retry_first_connection=True)
