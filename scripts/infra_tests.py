"""Infrastructure tests (MQTT broker with TLS and ACL), run on the host with the stack up.

    python scripts/infra_tests.py security          # TLS, authentication and ACL
    python scripts/infra_tests.py listen --expect 19856
        # test subscriber: counts client1's messages (unique seq and duplicates)

Uses the credentials in .env and certs/ca.crt.
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import ssl
import sys
import threading
import time
import uuid
from pathlib import Path

import paho.mqtt.client as mqtt
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")
CA = str(ROOT / "certs" / "ca.crt")
HOST = os.environ.get("CLIENT1_MQTT_HOST", "localhost")
PORT = int(os.environ.get("MQTT_PORT", "1883"))
PREFIX = os.environ.get("TOPIC_PREFIX", "idc/fc15")


def creds(who: str) -> tuple[str, str]:
    return os.environ[f"MQTT_{who}_USER"], os.environ[f"MQTT_{who}_PASSWORD"]


def client(user: str | None, password: str | None, tls: bool = True,
           ca: str | None = CA) -> tuple[mqtt.Client, dict]:
    state = {"rc": None, "msgs": [], "connected": threading.Event()}
    c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"test-{uuid.uuid4().hex[:8]}")
    if user:
        c.username_pw_set(user, password)
    if tls:
        c.tls_set(ca_certs=ca)

    def on_connect(cl, u, f, rc, p):
        state["rc"] = rc
        state["connected"].set()

    c.on_connect = on_connect
    c.on_message = lambda cl, u, m: state["msgs"].append((m.topic, m.payload))
    return c, state


def try_connect(user, password, tls=True, ca=CA, timeout=5.0) -> str:
    """Returns 'ok' or the reason for the failure."""
    c, st = client(user, password, tls, ca)
    try:
        c.connect(HOST, PORT, keepalive=10)
    except (ssl.SSLError, OSError) as exc:
        return f"failed at TLS/network: {type(exc).__name__}: {exc}"
    c.loop_start()
    got = st["connected"].wait(timeout)
    c.disconnect()
    c.loop_stop()
    if not got:
        return "no answer from the broker"
    return "ok" if not st["rc"].is_failure else f"refused: {st['rc']}"


def plain_tcp_probe() -> str:
    """Sends a plaintext MQTT CONNECT to the TLS port and checks what happens."""
    connect_pkt = bytes([0x10, 0x10, 0x00, 0x04]) + b"MQTT" + bytes([0x04, 0x02, 0x00, 0x3C, 0x00, 0x04]) + b"test"
    with socket.create_connection((HOST, PORT), timeout=5) as s:
        s.sendall(connect_pkt)
        try:
            data = s.recv(16)
        except (socket.timeout, ConnectionResetError) as exc:
            return f"no CONNACK ({type(exc).__name__})"
    if data[:1] == b"\x20":
        return "CONNACK received in plaintext (BAD)"
    return f"no CONNACK (connection closed, {len(data)} bytes)"


def acl_check(pub_who: str, topic: str, sub_who: str, sub_topic: str) -> bool:
    """True if the message published by pub_who on topic reaches sub_who."""
    sub, sst = client(*creds(sub_who))
    sub.connect(HOST, PORT)
    sub.loop_start()
    sst["connected"].wait(5)
    sub.subscribe(sub_topic, qos=1)
    time.sleep(0.5)
    pub, pst = client(*creds(pub_who))
    pub.connect(HOST, PORT)
    pub.loop_start()
    pst["connected"].wait(5)
    marker = f"acl-test-{uuid.uuid4().hex}"
    pub.publish(topic, marker, qos=1).wait_for_publish(5)
    time.sleep(1.0)
    for c in (pub, sub):
        c.disconnect()
        c.loop_stop()
    return any(p == marker.encode() for _, p in sst["msgs"])


def run_security() -> int:
    results = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        results.append(ok)
        print(f"[{'PASS' if ok else 'FAIL'}] {name}{(' -> ' + detail) if detail else ''}")

    u, p = creds("CLIENT1")
    r = try_connect(u, p)
    check("client1 with TLS and the right password connects", r == "ok", r)
    r = try_connect(u, "wrong-password")
    check("wrong password is refused", r.startswith("refused"), r)
    r = try_connect(None, None)
    check("anonymous connection is refused", r.startswith("refused"), r)
    r = plain_tcp_probe()
    check("MQTT without TLS on port 1883 is not accepted", "BAD" not in r, r)
    r = try_connect(u, p, ca=ssl.get_default_verify_paths().cafile or None)
    check("client without the group CA does not trust the broker", r != "ok", r)

    check("ACL: client1 -> status/+ reaches the processor",
          acl_check("CLIENT1", f"{PREFIX}/status/TEST", "PROCESSOR", f"{PREFIX}/status/+"))
    check("ACL: client1 CANNOT publish to internal/#",
          not acl_check("CLIENT1", f"{PREFIX}/internal/live_summary", "NODERED", f"{PREFIX}/internal/#"))
    check("ACL: processor -> internal/# reaches nodered",
          acl_check("PROCESSOR", f"{PREFIX}/internal/live_summary", "NODERED", f"{PREFIX}/internal/#"))
    check("ACL: nodered CANNOT read the station data",
          not acl_check("CLIENT1", f"{PREFIX}/trips/started", "NODERED", f"{PREFIX}/trips/#"))
    check("ACL: client1 CANNOT publish outside idc/fc15",
          not acl_check("CLIENT1", "idc/fc99/status/X", "PROCESSOR", "#"))

    print(f"\n{sum(results)}/{len(results)} tests passed")
    return 0 if all(results) else 1


def run_listen(expect: int | None, idle_s: float) -> int:
    """Test subscriber (processor user, persistent session, QoS 1)."""
    c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="test-listener", clean_session=False)
    c.username_pw_set(*creds("PROCESSOR"))
    c.tls_set(ca_certs=CA)
    c.reconnect_delay_set(1, 5)
    seqs: dict[int, int] = {}
    state = {"total": 0, "last": time.monotonic(), "first": None}

    def on_connect(cl, u, f, rc, p):
        print(f"connected ({rc}, session resumed={f.session_present})", flush=True)
        cl.subscribe([(f"{PREFIX}/status/+", 1), (f"{PREFIX}/trips/+", 1)])

    def on_message(cl, u, m):
        seq = json.loads(m.payload)["seq"]
        seqs[seq] = seqs.get(seq, 0) + 1
        state["total"] += 1
        state["last"] = time.monotonic()
        state["first"] = state["first"] or state["last"]

    c.on_connect = on_connect
    c.on_message = on_message
    c.on_disconnect = lambda cl, u, f, rc, p: print(f"disconnected ({rc})", flush=True)
    c.connect(HOST, PORT)
    c.loop_start()
    print(f"listening; stops after {idle_s:.0f}s without messages", flush=True)
    try:
        while True:
            time.sleep(1)
            if state["first"] and time.monotonic() - state["last"] > idle_s:
                break
            if expect and len(seqs) >= expect and time.monotonic() - state["last"] > 2:
                break
    except KeyboardInterrupt:
        pass
    c.disconnect()
    c.loop_stop()
    dup = sum(n - 1 for n in seqs.values())
    span = (state["last"] - state["first"]) if state["first"] else 0
    print(f"received={state['total']} unique seq={len(seqs)} duplicates={dup} "
          f"duration={span:.1f}s ({state['total'] / span if span else 0:.0f} msg/s)")
    if seqs:
        lo, hi = min(seqs), max(seqs)
        missing = (hi - lo + 1) - len(seqs)
        print(f"seq {lo}..{hi}, missing in the range: {missing}")
    if expect is not None:
        ok = len(seqs) == expect
        print(f"[{'PASS' if ok else 'FAIL'}] expected {expect} unique seq")
        return 0 if ok else 1
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    sp = ap.add_subparsers(dest="cmd", required=True)
    sp.add_parser("security")
    lp = sp.add_parser("listen")
    lp.add_argument("--expect", type=int)
    lp.add_argument("--idle", type=float, default=20.0)
    a = ap.parse_args()
    return run_security() if a.cmd == "security" else run_listen(a.expect, a.idle)


if __name__ == "__main__":
    sys.exit(main())
