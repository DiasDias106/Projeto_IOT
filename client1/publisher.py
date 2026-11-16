"""Client 1: emula as estações e reproduz o stage2_online.jsonl no broker (design-plan.md, secção 1).

Corre no host:
    pip install -r client1/requirements.txt
    python client1/publisher.py --speed 60

Cada linha é publicada sem alterações no tópico do seu tipo, com QoS 1, esperando
Δts / speed entre mensagens consecutivas. Reconexão automática.
"""
from __future__ import annotations

import argparse
import json  # noqa: F401  (usado na implementação de replay)
import logging
import os
from pathlib import Path

import paho.mqtt.client as mqtt
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("client1")

TOPIC_PREFIX = os.environ.get("TOPIC_PREFIX", "idc/fc15")


def topic_for(msg: dict) -> str:
    """Tópico de cada tipo de mensagem (design-plan.md, secção 2)."""
    kind = msg["type"]
    if kind == "station_status":
        return f"{TOPIC_PREFIX}/status/{msg['station_id']}"
    if kind == "trip_started":
        return f"{TOPIC_PREFIX}/trips/started"
    if kind == "trip_ended":
        return f"{TOPIC_PREFIX}/trips/ended"
    raise ValueError(f"tipo desconhecido: {kind}")


def make_client(args: argparse.Namespace) -> mqtt.Client:
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="client1", clean_session=False)
    client.username_pw_set(os.environ["MQTT_CLIENT1_USER"], os.environ["MQTT_CLIENT1_PASSWORD"])
    client.tls_set(ca_certs=str(args.ca))
    client.reconnect_delay_set(min_delay=1, max_delay=30)
    client.on_connect = lambda c, u, f, rc, p: log.info("ligado ao broker (%s)", rc)
    client.on_disconnect = lambda c, u, f, rc, p: log.warning("desligado (%s); a reconectar", rc)
    return client


def replay(client: mqtt.Client, path: Path, speed: float) -> None:
    """Lê o ficheiro linha a linha e publica cada mensagem, preservando os intervalos originais.

    A decidir e descrever no relatório: medir a espera a partir do início (evita acumular
    atraso), o que fazer com mensagens publicadas durante uma desconexão (a fila do paho guarda as
    de QoS 1) e se o ts original é mantido (o plano diz: payload sem alterações).
    """
    # TODO(client1): para cada linha -> json.loads, esperar (Δts / speed), client.publish(
    #   topic_for(msg), payload=linha, qos=1)
    raise NotImplementedError


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Reproduz o dataset online no broker MQTT (TLS).")
    p.add_argument("--file", type=Path, default=ROOT / "dataset-IdC-2627" / "stage2_online.jsonl")
    p.add_argument("--speed", type=float, default=float(os.environ.get("REPLAY_SPEED", "60")),
                   help="fator de velocidade (60 = uma hora de dados num minuto)")
    p.add_argument("--host", default=os.environ.get("CLIENT1_MQTT_HOST", "localhost"))
    p.add_argument("--port", type=int, default=int(os.environ.get("MQTT_PORT", "1883")))
    p.add_argument("--ca", type=Path, default=ROOT / "certs" / "ca.crt")
    return p.parse_args()


def main() -> None:
    args = parse_args()
    client = make_client(args)
    client.connect(args.host, args.port, keepalive=60)
    client.loop_start()
    try:
        replay(client, args.file, args.speed)
    finally:
        client.loop_stop()
        client.disconnect()


if __name__ == "__main__":
    main()
