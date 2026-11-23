"""Client 1: emulates the stations and replays stage2_online.jsonl to the broker (design-plan.md, section 1).

Runs on the host:
    . .venv/bin/activate
    python client1/publisher.py --speed 60            # 1 h of data per minute
    python client1/publisher.py --speed 0             # no waiting (load test)
    python client1/publisher.py --from-seq 60000      # resume from a seq
    python client1/publisher.py --dry-run --speed 0   # only show what would be published

Design (for the report):
  - Payload: each line is published unchanged (the original ts is kept), on its type's topic,
    with QoS 1.
  - Timing: the wait is computed from the start, target = t0 + (ts - ts0) / speed, instead of adding
    Δts / speed per message. Delays (for example, during a reconnect) do not accumulate: the
    publisher catches up and is aligned again. Messages with the same ts go out back to back.
  - Reconnection: paho reconnects by itself (1 s to 30 s backoff). clean_session=False and QoS 1:
    what is published while disconnected stays in the client queue and is sent on reconnect. The
    broker keeps the session. Possible duplicates are ignored by the Processor (unique seq).
  - End: waits for the broker's PUBACK of every message before disconnecting.
  - Stop (Ctrl+C): shows the last published seq, to resume with --from-seq.
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
    """Topic of each message type (design-plan.md, section 2)."""
    kind = msg["type"]
    if kind == "station_status":
        return f"{TOPIC_PREFIX}/status/{msg['station_id']}"
    if kind == "trip_started":
        return f"{TOPIC_PREFIX}/trips/started"
    if kind == "trip_ended":
        return f"{TOPIC_PREFIX}/trips/ended"
    raise ValueError(f"unknown type: {kind}")


@dataclass
class Stats:
    published: int = 0
    acked: int = 0
    skipped: int = 0
    disconnects: int = 0
    max_lag_s: float = 0.0          # maximum lag behind the target (wall clock)
    last_seq: int | None = None
    last_ts: str | None = None
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def ack(self) -> None:
        with self._lock:
            self.acked += 1


class Connection:
    """Connection state shared by the paho thread and the replay loop."""

    def __init__(self, stats: Stats):
        self.stats = stats
        self.connected = threading.Event()
        self.fatal: str | None = None

    def on_connect(self, client, userdata, flags, reason_code, properties):
        if reason_code.is_failure:
            # Wrong credentials or not authorised: retrying will not help
            self.fatal = f"connection refused by the broker: {reason_code}"
            log.error(self.fatal)
            self.connected.set()
            return
        log.info("connected to the broker (session resumed: %s)", flags.session_present)
        self.connected.set()

    def on_disconnect(self, client, userdata, flags, reason_code, properties):
        self.connected.clear()
        if reason_code.is_failure:
            self.stats.disconnects += 1
            log.warning("disconnected from the broker (%s); reconnecting, messages stay queued",
                        reason_code)

    def on_connect_fail(self, client, userdata):
        log.warning("broker unreachable; retrying soon")

    def on_publish(self, client, userdata, mid, reason_code, properties):
        self.stats.ack()


def make_client(args: argparse.Namespace, conn: Connection) -> mqtt.Client:
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="client1", clean_session=False)
    client.username_pw_set(os.environ["MQTT_CLIENT1_USER"], os.environ["MQTT_CLIENT1_PASSWORD"])
    client.tls_set(ca_certs=str(args.ca))
    client.reconnect_delay_set(min_delay=1, max_delay=30)
    client.max_inflight_messages_set(args.inflight)
    client.max_queued_messages_set(0)   # 0 = unlimited queue while disconnected
    client.on_connect = conn.on_connect
    client.on_disconnect = conn.on_disconnect
    client.on_connect_fail = conn.on_connect_fail
    client.on_publish = conn.on_publish
    return client


def read_messages(path: Path, from_seq: int | None, stats: Stats) -> Iterator[tuple[str, dict]]:
    """Yields (original line, message) in order. Invalid lines are skipped (logged)."""
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
                log.warning("line %d skipped (%s)", lineno, exc)
                continue
            if from_seq is not None and msg.get("seq", 0) < from_seq:
                continue
            yield line, msg


def replay(client: mqtt.Client | None, path: Path, speed: float, stats: Stats,
           from_seq: int | None = None, limit: int | None = None,
           stop: threading.Event | None = None) -> Stats:
    """Publishes each message on its topic, keeping the original intervals divided by speed.

    speed <= 0 publishes without waiting. client=None is a dry run (shows the topic, publishes nothing).
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
            log.info("replay start: ts=%s seq=%s speed=%s", msg["ts"], msg.get("seq"),
                     speed if speed > 0 else "no waiting")

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
            # Payload unchanged. While disconnected, paho queues it (QoS 1) and sends it on reconnect.
            client.publish(topic, payload=line, qos=1)
        stats.published += 1
        stats.last_seq, stats.last_ts = msg.get("seq"), msg["ts"]

        now = time.monotonic()
        if now - last_progress >= PROGRESS_EVERY_S:
            last_progress = now
            log.info("progress: %d published, %d acknowledged, ts=%s, max lag=%.2fs",
                     stats.published, stats.acked, stats.last_ts, stats.max_lag_s)
    return stats


def wait_for_acks(stats: Stats, timeout_s: float) -> bool:
    deadline = time.monotonic() + timeout_s
    while stats.acked < stats.published and time.monotonic() < deadline:
        time.sleep(0.1)
    return stats.acked >= stats.published


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Replays the online dataset to the MQTT broker (TLS).")
    p.add_argument("--file", type=Path, default=ROOT / "dataset-IdC-2627" / "stage2_online.jsonl")
    p.add_argument("--speed", type=float, default=float(os.environ.get("REPLAY_SPEED", "60")),
                   help="speed factor (60 = one hour of data per minute; 0 = no waiting)")
    p.add_argument("--host", default=os.environ.get("CLIENT1_MQTT_HOST", "localhost"))
    p.add_argument("--port", type=int, default=int(os.environ.get("MQTT_PORT", "1883")))
    p.add_argument("--ca", type=Path, default=ROOT / "certs" / "ca.crt")
    p.add_argument("--from-seq", type=int, default=None, help="start at the first seq >= this")
    p.add_argument("--limit", type=int, default=None, help="publish at most N messages")
    p.add_argument("--inflight", type=int, default=100, help="unacknowledged QoS 1 messages in flight")
    p.add_argument("--ack-timeout", type=float, default=60.0,
                   help="seconds to wait for acknowledgements at the end")
    p.add_argument("--dry-run", action="store_true", help="do not connect to the broker; only show")
    return p.parse_args(argv)


def summary(stats: Stats, elapsed_s: float) -> None:
    rate = stats.published / elapsed_s if elapsed_s > 0 else 0.0
    log.info("done: %d published, %d acknowledged, %d lines skipped, %d disconnects, "
             "%.1fs (%.0f msg/s), max lag=%.2fs, last seq=%s",
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
    client.loop_start()   # paho's thread handles the connection and reconnects
    start = time.monotonic()
    try:
        log.info("connecting to %s:%d (TLS)...", args.host, args.port)
        conn.connected.wait()
        if conn.fatal:
            return 2
        start = time.monotonic()
        replay(client, args.file, args.speed, stats, args.from_seq, args.limit, stop)
    except KeyboardInterrupt:
        stop.set()
        log.warning("interrupted; to resume: --from-seq %s",
                    (stats.last_seq + 1) if stats.last_seq is not None else "<start>")
    finally:
        if not conn.fatal and not wait_for_acks(stats, args.ack_timeout):
            log.warning("%d messages not acknowledged by the broker",
                        stats.published - stats.acked)
        client.disconnect()
        client.loop_stop()
    summary(stats, time.monotonic() - start)
    return 0


if __name__ == "__main__":
    sys.exit(main())
