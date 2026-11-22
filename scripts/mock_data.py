"""Utilitário para gerar e injetar dados de teste válidos no sistema.

Uso:
    # 1. Carregar 500 viagens reais do stage1_offline.jsonl para o MySQL:
    .venv/bin/python scripts/mock_data.py stage1 --limit 500

    # 2. Transmitir telemetria live simulada por MQTT a cada 2 segundos:
    .venv/bin/python scripts/mock_data.py live --count 20 --interval 2

    # 3. Limpar dados de teste da tabela trips:
    .venv/bin/python scripts/mock_data.py clean
"""
from __future__ import annotations

import argparse
import datetime
import json
import math
import os
import random
import subprocess
import sys
import time
from pathlib import Path

import paho.mqtt.client as mqtt
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    if lat1 == lat2 and lon1 == lon2:
        return 0.0
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2.0) ** 2 +
         math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2.0) ** 2)
    c = 2.0 * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))
    return round(R * c, 3)


def calc_cost(member_casual: str, rideable_type: str, duration_s: int) -> float:
    mins = math.ceil(duration_s / 60.0)
    cost = 0.0
    if member_casual == "member":
        if mins > 45:
            cost += (mins - 45) * 0.20
        if rideable_type == "electric_bike":
            cost += mins * 0.25
    else:  # casual
        cost += 4.50
        if mins > 30:
            cost += (mins - 30) * 0.30
        if rideable_type == "electric_bike":
            cost += mins * 0.35
    return round(cost, 2)


def run_sql(sql: str) -> str:
    db_pass = os.environ.get("DB_ROOT_PASSWORD", "")
    cmd = [
        "docker", "compose", "exec", "-T", "db",
        "mysql", "-uroot", f"-p{db_pass}", "bikesharing",
        "-e", sql
    ]
    res = subprocess.run(cmd, capture_output=True, text=True, check=True)
    return res.stdout


def load_stage1_sample(limit: int) -> int:
    jsonl_path = ROOT / "dataset-IdC-2627" / "stage1_offline.jsonl"
    if not jsonl_path.exists():
        print(f"Ficheiro não encontrado: {jsonl_path}")
        return 0

    print(f"A processar {limit} viagens de {jsonl_path.name}...")
    inserted = 0
    values = []

    with open(jsonl_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            if rec.get("type") != "trip_ended":
                continue

            ride_id = rec.get("ride_id")
            rideable_type = rec.get("rideable_type", "classic_bike")
            member_casual = rec.get("member_casual", "member")
            started_at = rec.get("started_at", "")[:19].replace("T", " ")
            ended_at = rec.get("ended_at", "")[:19].replace("T", " ")
            duration_s = int(rec.get("duration_s", 0))

            start_id = rec.get("start_station_id") or "NULL"
            start_name = (rec.get("start_station_name") or "").replace("'", "\\'")
            end_id = rec.get("end_station_id") or "NULL"
            end_name = (rec.get("end_station_name") or "").replace("'", "\\'")

            start_lat = rec.get("start_lat")
            start_lng = rec.get("start_lng")
            end_lat = rec.get("end_lat")
            end_lng = rec.get("end_lng")

            dist_km = 0.0
            if None not in (start_lat, start_lng, end_lat, end_lng):
                dist_km = haversine_km(float(start_lat), float(start_lng), float(end_lat), float(end_lng))

            energy = round(0.015 * dist_km, 3) if rideable_type == "electric_bike" else 0.0
            cost = calc_cost(member_casual, rideable_type, duration_s)

            flag = "OK"
            if not end_id or end_id == "NULL":
                flag = "MISSING_END"
            elif duration_s < 60:
                flag = "OUTLIER_SHORT"
            elif duration_s > 3 * 3600:
                flag = "OUTLIER_LONG"

            v = (
                f"('{ride_id}', '{rideable_type}', '{member_casual}', '{started_at}', '{ended_at}', "
                f"{duration_s}, '{start_id}', '{start_name}', '{end_id}', '{end_name}', "
                f"{start_lat if start_lat is not None else 'NULL'}, {start_lng if start_lng is not None else 'NULL'}, "
                f"{end_lat if end_lat is not None else 'NULL'}, {end_lng if end_lng is not None else 'NULL'}, "
                f"{dist_km}, {energy}, {cost}, '{flag}')"
            )
            values.append(v)
            inserted += 1

            if len(values) >= 100:
                sql = (
                    "INSERT INTO trips (trip_id, rideable_type, member_casual, started_at, ended_at, "
                    "duration_s, start_station_id, start_station_name, end_station_id, end_station_name, "
                    "start_lat, start_lng, end_lat, end_lng, distance_km, energy_kwh, cost_usd, quality_flag) "
                    "VALUES " + ", ".join(values) + " "
                    "ON DUPLICATE KEY UPDATE started_at=VALUES(started_at);"
                )
                run_sql(sql)
                values.clear()

            if inserted >= limit:
                break

    if values:
        sql = (
            "INSERT INTO trips (trip_id, rideable_type, member_casual, started_at, ended_at, "
            "duration_s, start_station_id, start_station_name, end_station_id, end_station_name, "
            "start_lat, start_lng, end_lat, end_lng, distance_km, energy_kwh, cost_usd, quality_flag) "
            "VALUES " + ", ".join(values) + " "
            "ON DUPLICATE KEY UPDATE started_at=VALUES(started_at);"
        )
        run_sql(sql)

    count_res = run_sql("SELECT count(*) AS total_trips FROM trips;")
    print(f"Sucesso! {inserted} viagens inseridas na tabela trips.")
    print(count_res)
    return inserted


def run_live_simulation(count: int, interval: float) -> None:
    ca_cert = str(ROOT / "certs" / "ca.crt")
    host = os.environ.get("CLIENT1_MQTT_HOST", "localhost")
    port = int(os.environ.get("MQTT_PORT", "1883"))
    user = os.environ.get("MQTT_PROCESSOR_USER")
    passwd = os.environ.get("MQTT_PROCESSOR_PASSWORD")

    c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"sim-{random.randint(100, 999)}")
    if user:
        c.username_pw_set(user, passwd)
    c.tls_set(ca_certs=ca_cert)
    c.connect(host, port, keepalive=10)
    c.loop_start()

    print(f"A emitir {count} atualizações para 'idc/fc15/internal/live_summary' (intervalo: {interval}s)...")
    base_trips = 80
    base_energy = 25.0

    try:
        for i in range(count):
            active = random.randint(15, 55)
            bikes = random.randint(300, 420)
            ebikes = random.randint(90, 160)
            base_trips += random.randint(1, 3)
            base_energy += round(random.uniform(0.1, 0.4), 2)
            avg_dur = round(random.uniform(11.5, 15.0), 1)

            payload = {
                "active_trips": active,
                "total_bikes_avail": bikes,
                "total_ebikes_avail": ebikes,
                "stage2_trips": base_trips,
                "stage2_energy_kwh": round(base_energy, 2),
                "stage2_avg_duration_min": avg_dur,
                "updated_at": datetime.datetime.now().isoformat()
            }
            c.publish("idc/fc15/internal/live_summary", json.dumps(payload), qos=1)
            print(f"[{i+1}/{count}] Enviado: {payload['active_trips']} ativas, {payload['total_bikes_avail']} disponíveis, {payload['stage2_energy_kwh']} kWh")
            time.sleep(interval)
    finally:
        c.disconnect()
        c.loop_stop()
        print("Simulação live concluída.")


def clean_trips() -> None:
    run_sql("TRUNCATE TABLE trips;")
    print("Tabela trips limpa com sucesso (0 registos).")


def main() -> int:
    parser = argparse.ArgumentParser(description="Mock data injector para Citi Bike IdC")
    sub = parser.add_subparsers(dest="cmd", required=True)

    s1_parser = sub.add_parser("stage1", help="Injeta amostra de viagens reais do stage 1 no MySQL")
    s1_parser.add_argument("--limit", type=int, default=500, help="Número de viagens a injetar (default: 500)")

    live_parser = sub.add_parser("live", help="Simula fluxo MQTT live_summary em tempo real")
    live_parser.add_argument("--count", type=int, default=15, help="Número de mensagens (default: 15)")
    live_parser.add_argument("--interval", type=float, default=2.0, help="Intervalo em segundos (default: 2.0)")

    sub.add_parser("clean", help="Limpa todas as viagens da tabela trips")

    args = parser.parse_args()

    if args.cmd == "stage1":
        load_stage1_sample(args.limit)
    elif args.cmd == "live":
        run_live_simulation(args.count, args.interval)
    elif args.cmd == "clean":
        clean_trips()

    return 0


if __name__ == "__main__":
    sys.exit(main())
