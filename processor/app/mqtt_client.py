"""MQTT client over TLS, shared: T1 receives, T3 and T4 publish through the same client."""
from __future__ import annotations

import ssl

import paho.mqtt.client as mqtt

from .config import Config


def make_client(cfg: Config, client_id: str = "processor") -> mqtt.Client:
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=client_id, clean_session=False)
    client.username_pw_set(cfg.mqtt_user, cfg.mqtt_password)
    # Verifies the broker certificate and hostname against the group CA; TLS 1.2 or newer
    client.tls_set_context(ssl.create_default_context(cafile=cfg.ca_cert))
    client.reconnect_delay_set(min_delay=1, max_delay=30)
    client.max_queued_messages_set(10_000)
    return client
