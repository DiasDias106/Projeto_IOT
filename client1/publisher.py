"""Client 1: emula as estações e reproduz o stage2_online.jsonl no broker (design-plan.md, secção 1).

Corre no host:
    pip install -r client1/requirements.txt
    python client1/publisher.py --speed 60            # 1 h de dados por minuto
    python client1/publisher.py --speed 0             # sem esperas (teste de carga)
    python client1/publisher.py --from-seq 60000      # retomar a partir de um seq
    python client1/publisher.py --dry-run --speed 0   # só mostra o que publicaria

Desenho (para o relatório):
  - Payload: cada linha é publicada sem alterações (o ts original é mantido), no tópico do seu tipo,
    com QoS 1.
  - Tempo: a espera é calculada a partir do início, alvo = t0 + (ts - ts0) / speed, e não somando
    Δts / speed a cada mensagem. Assim os atrasos (por exemplo, numa reconexão) não se acumulam: o
    publisher recupera e volta a ficar alinhado. Mensagens com o mesmo ts saem seguidas.
  - Reconexão: o paho reconecta sozinho (1 s a 30 s de espera). clean_session=False e QoS 1: o que é
    publicado durante uma desconexão fica na fila do cliente e é enviado ao reconectar. O broker
    guarda a sessão. Duplicados possíveis são ignorados pelo Processor (seq único).
  - Fim: espera pela confirmação (PUBACK) de todas as mensagens antes de desligar.
  - Paragem (Ctrl+C): mostra o último seq publicado, para retomar com --from-seq.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Iterator

import paho.mqtt.client as mqtt
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("client1")

TOPIC_PREFIX = os.environ.get("TOPIC_PREFIX", "idc/fc15")
PROGRESS_EVERY_S = 10.0


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


@dataclass
class Stats:
    published: int = 0
    acked: int = 0
    skipped: int = 0
    disconnects: int = 0
    max_lag_s: float = 0.0          # atraso máximo face ao alvo (relógio real)
    last_seq: int | None = None
    last_ts: str | None = None
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def ack(self) -> None:
        with self._lock:
            self.acked += 1


class Connection:
    """Estado da ligação partilhado entre a thread do paho e o ciclo de replay."""

    def __init__(self, stats: Stats):
        self.stats = stats
        self.connected = threading.Event()
        self.fatal: str | None = None

    def on_connect(self, client, userdata, flags, reason_code, properties):
        if reason_code.is_failure:
            # Credenciais erradas ou não autorizado: não adianta continuar a tentar
            self.fatal = f"ligação recusada pelo broker: {reason_code}"
            log.error(self.fatal)
            self.connected.set()
            return
        log.info("ligado ao broker (sessão retomada: %s)", flags.session_present)
        self.connected.set()

    def on_disconnect(self, client, userdata, flags, reason_code, properties):
        self.connected.clear()
        if reason_code.is_failure:
            self.stats.disconnects += 1
            log.warning("desligado do broker (%s); a reconectar, as mensagens ficam em fila",
                        reason_code)

    def on_connect_fail(self, client, userdata):
        log.warning("broker inacessível; nova tentativa em breve")

    def on_publish(self, client, userdata, mid, reason_code, properties):
        self.stats.ack()


def make_client(args: argparse.Namespace, conn: Connection) -> mqtt.Client:
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="client1", clean_session=False)
    client.username_pw_set(os.environ["MQTT_CLIENT1_USER"], os.environ["MQTT_CLIENT1_PASSWORD"])
    client.tls_set(ca_certs=str(args.ca))
    client.reconnect_delay_set(min_delay=1, max_delay=30)
    client.max_inflight_messages_set(args.inflight)
    client.max_queued_messages_set(0)   # 0 = fila sem limite durante desconexões
    client.on_connect = conn.on_connect
    client.on_disconnect = conn.on_disconnect
    client.on_connect_fail = conn.on_connect_fail
    client.on_publish = conn.on_publish
    return client


def read_messages(path: Path, from_seq: int | None, stats: Stats) -> Iterator[tuple[str, dict]]:
    """Devolve (linha original, mensagem) por ordem. Linhas inválidas são ignoradas (log)."""
    with path.open("r", encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
                msg["_ts"] = datetime.fromisoformat(msg["ts"])
                topic_for(msg)
            except (ValueError, KeyError) as exc:
                stats.skipped += 1
                log.warning("linha %d ignorada (%s)", lineno, exc)
                continue
            if from_seq is not None and msg.get("seq", 0) < from_seq:
                continue
            yield line, msg


def replay(client: mqtt.Client | None, path: Path, speed: float, stats: Stats,
           from_seq: int | None = None, limit: int | None = None,
           stop: threading.Event | None = None) -> Stats:
    """Publica cada mensagem no seu tópico, preservando os intervalos originais divididos por speed.

    speed <= 0 publica sem esperas. client=None faz um dry-run (não publica, só mostra o tópico).
    """
    stop = stop or threading.Event()
    t0_wall: float | None = None
    ts0: datetime | None = None
    last_progress = time.monotonic()

    for line, msg in read_messages(path, from_seq, stats):
        if stop.is_set() or (limit is not None and stats.published >= limit):
            break
        ts = msg.pop("_ts")
        if t0_wall is None:
            t0_wall, ts0 = time.monotonic(), ts
            log.info("início do replay: ts=%s seq=%s speed=%s", msg["ts"], msg.get("seq"),
                     speed if speed > 0 else "sem esperas")

        if speed > 0:
            target = t0_wall + (ts - ts0).total_seconds() / speed
            delay = target - time.monotonic()
            if delay > 0:
                if stop.wait(delay):
                    break
            else:
                stats.max_lag_s = max(stats.max_lag_s, -delay)

        topic = topic_for(msg)
        if client is None:
            log.info("[dry-run] %s %s seq=%s", msg["ts"], topic, msg.get("seq"))
        else:
            # Payload sem alterações. Desligado: o paho guarda em fila (QoS 1) e envia ao reconectar.
            client.publish(topic, payload=line, qos=1)
        stats.published += 1
        stats.last_seq, stats.last_ts = msg.get("seq"), msg["ts"]

        now = time.monotonic()
        if now - last_progress >= PROGRESS_EVERY_S:
            last_progress = now
            log.info("progresso: %d publicadas, %d confirmadas, ts=%s, atraso máx=%.2fs",
                     stats.published, stats.acked, stats.last_ts, stats.max_lag_s)
    return stats


def wait_for_acks(stats: Stats, timeout_s: float) -> bool:
    deadline = time.monotonic() + timeout_s
    while stats.acked < stats.published and time.monotonic() < deadline:
        time.sleep(0.1)
    return stats.acked >= stats.published


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Reproduz o dataset online no broker MQTT (TLS).")
    p.add_argument("--file", type=Path, default=ROOT / "dataset-IdC-2627" / "stage2_online.jsonl")
    p.add_argument("--speed", type=float, default=float(os.environ.get("REPLAY_SPEED", "60")),
                   help="fator de velocidade (60 = uma hora de dados num minuto; 0 = sem esperas)")
    p.add_argument("--host", default=os.environ.get("CLIENT1_MQTT_HOST", "localhost"))
    p.add_argument("--port", type=int, default=int(os.environ.get("MQTT_PORT", "1883")))
    p.add_argument("--ca", type=Path, default=ROOT / "certs" / "ca.crt")
    p.add_argument("--from-seq", type=int, default=None, help="começar no primeiro seq >= este")
    p.add_argument("--limit", type=int, default=None, help="publicar no máximo N mensagens")
    p.add_argument("--inflight", type=int, default=100, help="mensagens QoS 1 por confirmar em voo")
    p.add_argument("--ack-timeout", type=float, default=60.0,
                   help="segundos a esperar pelas confirmações no fim")
    p.add_argument("--dry-run", action="store_true", help="não liga ao broker; só mostra")
    return p.parse_args(argv)


def summary(stats: Stats, elapsed_s: float) -> None:
    rate = stats.published / elapsed_s if elapsed_s > 0 else 0.0
    log.info("fim: %d publicadas, %d confirmadas, %d linhas ignoradas, %d desconexões, "
             "%.1fs (%.0f msg/s), atraso máx=%.2fs, último seq=%s",
             stats.published, stats.acked, stats.skipped, stats.disconnects,
             elapsed_s, rate, stats.max_lag_s, stats.last_seq)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    stats = Stats()
    stop = threading.Event()

    if args.dry_run:
        start = time.monotonic()
        replay(None, args.file, args.speed, stats, args.from_seq, args.limit, stop)
        summary(stats, time.monotonic() - start)
        return 0

    conn = Connection(stats)
    client = make_client(args, conn)
    client.connect_async(args.host, args.port, keepalive=60)
    client.loop_start()   # a thread do paho trata da ligação e das reconexões
    start = time.monotonic()
    try:
        log.info("a ligar a %s:%d (TLS)...", args.host, args.port)
        conn.connected.wait()
        if conn.fatal:
            return 2
        start = time.monotonic()
        replay(client, args.file, args.speed, stats, args.from_seq, args.limit, stop)
    except KeyboardInterrupt:
        stop.set()
        log.warning("interrompido; para retomar: --from-seq %s",
                    (stats.last_seq + 1) if stats.last_seq is not None else "<início>")
    finally:
        if not conn.fatal and not wait_for_acks(stats, args.ack_timeout):
            log.warning("%d mensagens sem confirmação do broker",
                        stats.published - stats.acked)
        client.disconnect()
        client.loop_stop()
    summary(stats, time.monotonic() - start)
    return 0


if __name__ == "__main__":
    sys.exit(main())
