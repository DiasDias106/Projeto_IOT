"""Processor: um processo, quatro threads, duas filas (design-plan.md, secção 3).

Ordem de arranque:
  1. T2 Worker e T3 Writer (para o stage 1 poder passar por eles).
  2. Bootstrap: se a BD estiver vazia, carrega estações + stage 1, espera que as filas esvaziem
     e pede o primeiro treino ao ML.
  3. T1 Listener (stage 2 por MQTT) e T4 Scheduler.
"""
from __future__ import annotations

import logging
import queue
import signal
import threading

from . import bootstrap, db, ml_client
from .config import Config
from .counters import LiveCounters
from .listener import Listener
from .mqtt_client import make_client
from .scheduler import Scheduler
from .worker import Worker
from .writer import Writer

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s [%(name)s] %(message)s")
log = logging.getLogger("main")


def load_station_ids(cfg: Config) -> set[str]:
    conn = db.connect(cfg)
    try:
        cur = conn.cursor()
        cur.execute("SELECT station_id FROM stations")
        return {row[0] for row in cur.fetchall()}
    finally:
        conn.close()


def main() -> None:
    cfg = Config.from_env()
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())

    raw_queue: queue.Queue = queue.Queue(maxsize=10_000)
    clean_queue: queue.Queue = queue.Queue(maxsize=10_000)
    counters = LiveCounters()
    mqtt_client = make_client(cfg)

    station_ids: set[str] = set()   # preenchido depois do bootstrap; o Worker lê a mesma instância
    worker = Worker(raw_queue, clean_queue, station_ids, stop)
    writer = Writer(cfg, clean_queue, counters, mqtt_client, stop)
    writer.start()

    # O Worker precisa dos IDs das estações: o bootstrap carrega-as antes de alimentar a fila,
    # por isso o Worker só arranca depois de as estações estarem na BD.
    loaded = False
    try:
        conn = db.connect(cfg)
        try:
            empty = not bootstrap.stations_loaded(conn)
        finally:
            conn.close()
        if empty:
            conn = db.connect(cfg)
            try:
                bootstrap.load_stations(conn, f"{cfg.dataset_dir}/stations.json")
            finally:
                conn.close()
        station_ids.update(load_station_ids(cfg))
        worker.start()
        if empty:
            n = bootstrap.feed_stage1(raw_queue, f"{cfg.dataset_dir}/stage1_offline.jsonl")
            log.info("%d mensagens do stage 1 na raw queue; a aguardar processamento", n)
            raw_queue.join()
            clean_queue.join()
            loaded = True
        else:
            log.info("BD já carregada: stage 1 não é recarregado")
    except NotImplementedError:
        log.warning("TODO: bootstrap ainda não implementado; stage 1 não carregado")
        if not worker.is_alive():
            station_ids.update(load_station_ids(cfg))
            worker.start()

    if loaded:
        ml_client.request_training(cfg)

    Listener(cfg, mqtt_client, raw_queue).start()
    Scheduler(cfg, counters, mqtt_client, stop).start()
    log.info("Processor pronto")

    stop.wait()
    log.info("a terminar")
    mqtt_client.disconnect()


if __name__ == "__main__":
    main()
