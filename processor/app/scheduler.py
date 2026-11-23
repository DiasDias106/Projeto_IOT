"""T4 Scheduler (PLAN.md, sections 1 and 6).

Every SUMMARY_INTERVAL_S (wall clock): publishes idc/fc15/internal/live_summary.
Every RETRAIN_EVERY_HOURS of event time (newest stored ts): asks the ML service to retrain.
Training runs in a short-lived thread, one at a time, so T1 to T3 are never blocked.
"""
from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timedelta, timezone

import paho.mqtt.client as mqtt

from . import ml_client
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
        self.last_train_event_ts: datetime | None = None
        self._training = threading.Lock()

    def publish_summary(self) -> None:
        if not self.mqtt.is_connected():
            return
        data = self.counters.snapshot()
        if "active_trips" not in data:
            return   # the Writer has not built a snapshot yet
        data["updated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        self.mqtt.publish(self.cfg.topic_live_summary, json.dumps(data), qos=0)

    def trigger_training(self, reason: str, event_ts: datetime | None = None) -> bool:
        """Start a training in the background. False if one is already running.

        event_ts: event time the training covers (the next one is due RETRAIN_EVERY_HOURS later).
        """
        if not self._training.acquire(blocking=False):
            log.info("training already running; skipped (%s)", reason)
            return False
        self.last_train_event_ts = event_ts or self.counters.last_event_ts

        def run() -> None:
            try:
                log.info("requesting ML training (%s)", reason)
                ml_client.request_training(self.cfg)
            finally:
                self._training.release()

        threading.Thread(target=run, name="T4-training", daemon=True).start()
        return True

    def maybe_retrain(self) -> None:
        now = self.counters.last_event_ts
        if now is None:
            return
        if self.last_train_event_ts is None:
            self.last_train_event_ts = now
            return
        if now - self.last_train_event_ts >= timedelta(hours=self.cfg.retrain_every_hours):
            self.trigger_training(f"{self.cfg.retrain_every_hours} h of event time, now at {now.isoformat()}")

    def run(self) -> None:
        log.info("starting")
        while not self.stop_event.wait(self.cfg.summary_interval_s):
            for fn in (self.publish_summary, self.maybe_retrain):
                try:
                    fn()
                except Exception:
                    log.exception("error in %s()", fn.__name__)
