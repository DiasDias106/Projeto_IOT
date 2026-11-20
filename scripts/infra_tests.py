"""Testes da infraestrutura (broker MQTT com TLS e ACL), a correr no host com a stack levantada.

    python scripts/infra_tests.py security          # TLS, autenticação e ACL
    python scripts/infra_tests.py listen --expect 19856
        # subscritor de teste: conta as mensagens do client1 (seq únicos e duplicados)

Usa as credenciais do .env e o certs/ca.crt.
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
    """Devolve 'ok' ou a razão da falha."""
    c, st = client(user, password, tls, ca)
    try:
        c.connect(HOST, PORT, keepalive=10)
    except (ssl.SSLError, OSError) as exc:
        return f"falhou no TLS/rede: {type(exc).__name__}: {exc}"
    c.loop_start()
    got = st["connected"].wait(timeout)
    c.disconnect()
    c.loop_stop()
    if not got:
        return "sem resposta do broker"
    return "ok" if not st["rc"].is_failure else f"recusado: {st['rc']}"


def plain_tcp_probe() -> str:
    """Envia um CONNECT MQTT em claro para a porta TLS e vê o que acontece."""
    connect_pkt = bytes([0x10, 0x10, 0x00, 0x04]) + b"MQTT" + bytes([0x04, 0x02, 0x00, 0x3C, 0x00, 0x04]) + b"test"
    with socket.create_connection((HOST, PORT), timeout=5) as s:
        s.sendall(connect_pkt)
        try:
            data = s.recv(16)
        except (socket.timeout, ConnectionResetError) as exc:
            return f"sem CONNACK ({type(exc).__name__})"
    if data[:1] == b"\x20":
        return "CONNACK recebido em claro (MAU)"
    return f"sem CONNACK (ligação fechada, {len(data)} bytes)"


def acl_check(pub_who: str, topic: str, sub_who: str, sub_topic: str) -> bool:
    """True se a mensagem publicada por pub_who em topic chega a sub_who."""
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
    check("client1 com TLS e password certa liga", r == "ok", r)
    r = try_connect(u, "password-errada")
    check("password errada é recusada", r.startswith("recusado"), r)
    r = try_connect(None, None)
    check("ligação anónima é recusada", r.startswith("recusado"), r)
    r = plain_tcp_probe()
    check("MQTT sem TLS na porta 1883 não é aceite", "MAU" not in r, r)
    r = try_connect(u, p, ca=ssl.get_default_verify_paths().cafile or None)
    check("cliente sem a CA do grupo não confia no broker", r != "ok", r)

    check("ACL: client1 -> status/+ chega ao processor",
          acl_check("CLIENT1", f"{PREFIX}/status/TEST", "PROCESSOR", f"{PREFIX}/status/+"))
    check("ACL: client1 NÃO consegue publicar em internal/#",
          not acl_check("CLIENT1", f"{PREFIX}/internal/live_summary", "NODERED", f"{PREFIX}/internal/#"))
    check("ACL: processor -> internal/# chega ao nodered",
          acl_check("PROCESSOR", f"{PREFIX}/internal/live_summary", "NODERED", f"{PREFIX}/internal/#"))
    check("ACL: nodered NÃO consegue ler os dados das estações",
          not acl_check("CLIENT1", f"{PREFIX}/trips/started", "NODERED", f"{PREFIX}/trips/#"))
    check("ACL: client1 NÃO consegue publicar fora de idc/fc15",
          not acl_check("CLIENT1", "idc/fc99/status/X", "PROCESSOR", "#"))

    print(f"\n{sum(results)}/{len(results)} testes passaram")
    return 0 if all(results) else 1


def run_listen(expect: int | None, idle_s: float) -> int:
    """Subscritor de teste (utilizador processor, sessão persistente, QoS 1)."""
    c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="test-listener", clean_session=False)
    c.username_pw_set(*creds("PROCESSOR"))
    c.tls_set(ca_certs=CA)
    c.reconnect_delay_set(1, 5)
    seqs: dict[int, int] = {}
    state = {"total": 0, "last": time.monotonic(), "first": None}

    def on_connect(cl, u, f, rc, p):
        print(f"ligado ({rc}, sessão retomada={f.session_present})", flush=True)
        cl.subscribe([(f"{PREFIX}/status/+", 1), (f"{PREFIX}/trips/+", 1)])

    def on_message(cl, u, m):
        seq = json.loads(m.payload)["seq"]
        seqs[seq] = seqs.get(seq, 0) + 1
        state["total"] += 1
        state["last"] = time.monotonic()
        state["first"] = state["first"] or state["last"]

    c.on_connect = on_connect
    c.on_message = on_message
    c.on_disconnect = lambda cl, u, f, rc, p: print(f"desligado ({rc})", flush=True)
    c.connect(HOST, PORT)
    c.loop_start()
    print(f"à escuta; termina após {idle_s:.0f}s sem mensagens", flush=True)
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
    print(f"recebidas={state['total']} seq únicos={len(seqs)} duplicados={dup} "
          f"duração={span:.1f}s ({state['total'] / span if span else 0:.0f} msg/s)")
    if seqs:
        lo, hi = min(seqs), max(seqs)
        missing = (hi - lo + 1) - len(seqs)
        print(f"seq {lo}..{hi}, em falta no intervalo: {missing}")
    if expect is not None:
        ok = len(seqs) == expect
        print(f"[{'PASS' if ok else 'FAIL'}] esperados {expect} seq únicos")
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
