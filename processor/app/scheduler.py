"""T4 Scheduler (design-plan.md, secção 3).

A cada SUMMARY_INTERVAL_S (relógio real): publica idc/fc15/internal/live_summary.
A cada RETRAIN_EVERY_HOURS de tempo de evento (counters.last_event_ts): pede POST /train ao ML.
Nunca bloqueia o T1 a T3 (o treino corre numa thread à parte).
"""
from __future__ import annotations

import json  # noqa: F401  (usado na implementação)
import logging
import threading

import paho.mqtt.client as mqtt

from . import ml_client  # noqa: F401
from .config import Config
from .counters import LiveCounters

log = logging.getLogger("T4-scheduler")


class Scheduler(threading.Thread):
    def __init__(self, cfg: Config, counters: LiveCounters, mqtt_client: mqtt.Client,
                 stop: threading.Event):
        super().__init__(name="T4-scheduler", daemon=True)
        self.cfg = cfg
        self.counters = counters
        self.mqtt = mqtt_client
        self.stop_event = stop
        self.last_train_event_ts = None   # tempo de evento do último treino
        self._warned = set()

    def publish_summary(self) -> None:
        """Publica self.counters.snapshot() em JSON no tópico live_summary (QoS 0 ou 1)."""
        # TODO(processor/T4)
        raise NotImplementedError

    def maybe_retrain(self) -> None:
        """Se passaram RETRAIN_EVERY_HOURS de tempo de evento desde o último treino, chama
        ml_client.request_training(self.cfg) numa thread à parte."""
        # TODO(processor/T4)
        raise NotImplementedError

    def _call(self, fn) -> None:
        try:
            fn()
        except NotImplementedError:
            if fn.__name__ not in self._warned:
                log.warning("TODO: %s() ainda não implementado", fn.__name__)
                self._warned.add(fn.__name__)
        except Exception:
            log.exception("erro em %s()", fn.__name__)

    def run(self) -> None:
        log.info("a arrancar")
        while not self.stop_event.wait(self.cfg.summary_interval_s):
            self._call(self.publish_summary)
            self._call(self.maybe_retrain)
