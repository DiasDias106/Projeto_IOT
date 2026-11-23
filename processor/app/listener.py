"""T1 Listener: receive only (PLAN.md, section 1).

Subscribes to status/+ and trips/+ and puts the raw payload in the raw queue. No parsing, no
computing, no MySQL. A full raw queue blocks here, which slows the broker down (back-pressure)
instead of dropping messages.
"""
from __future__ import annotations

import logging
import queue
from typing import Callable

import paho.mqtt.client as mqtt

from .config import Config
from .contracts import RawMessage

log = logging.getLogger("T1-listener")


class Listener:
    """T1 is paho's own network thread (loop_start), not a thread we run loop_forever() in.

    With loop_forever() in a custom thread, paho thinks no loop thread exists and publish() from
    T3/T4 writes to the TLS socket directly, concurrently with the loop. That corrupts the stream
    (broker logs "Protocol error"). With loop_start(), publish() only queues the packet.
    """

    def __init__(self, cfg: Config, client: mqtt.Client, raw_queue: queue.Queue,
                 on_connected: Callable[[], None] | None = None):
        self.cfg = cfg
        self.client = client
        self.raw_queue = raw_queue
        self.on_connected = on_connected
        client.on_connect = self._on_connect
        client.on_disconnect = self._on_disconnect
        client.on_message = self._on_message

    def _on_connect(self, client, userdata, flags, reason_code, properties):
        if reason_code.is_failure:
            log.error("broker refused the connection: %s", reason_code)
            return
        log.info("connected to the broker; subscribing")
        # Subscribing in on_connect renews the subscription after every reconnect
        client.subscribe([(self.cfg.topic_status_sub, 1), (self.cfg.topic_trips_sub, 1)])
        if self.on_connected:
            self.on_connected()

    def _on_disconnect(self, client, userdata, flags, reason_code, properties):
        log.warning("disconnected from the broker (%s); reconnecting", reason_code)

    def _on_message(self, client, userdata, msg):
        self.raw_queue.put(RawMessage(source="mqtt", payload=msg.payload, topic=msg.topic))

    def start(self) -> None:
        self.client.connect_async(self.cfg.mqtt_host, self.cfg.mqtt_port, keepalive=60)
        self.client.loop_start()   # retries the first connection too

    def stop(self) -> None:
        self.client.disconnect()
        self.client.loop_stop()
