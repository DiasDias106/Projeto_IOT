"""Cliente MQTT sobre TLS, partilhado: o T1 recebe e o T3/T4 publicam pelo mesmo cliente."""
from __future__ import annotations

import paho.mqtt.client as mqtt

from .config import Config


def make_client(cfg: Config, client_id: str = "processor") -> mqtt.Client:
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=client_id, clean_session=False)
    client.username_pw_set(cfg.mqtt_user, cfg.mqtt_password)
    client.tls_set(ca_certs=cfg.ca_cert)
    client.reconnect_delay_set(min_delay=1, max_delay=30)
    return client
