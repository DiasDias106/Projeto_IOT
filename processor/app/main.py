"""Processor: one process, 1 + N + 1 + 1 threads, two queues (PLAN.md, sections 1 and 2).

Start-up order:
  1. T3 Writer, then the T2 Workers (stage 1 goes through them like stage 2).
  2. Bootstrap: unless ingest_log says stage 1 is loaded, upsert the stations, feed stage 1 and
     wait until every message is committed, then mark it as loaded.
  3. T1 Listener (stage 2 over MQTT) and T4 Scheduler; first ML training if needed.

Shutdown (SIGTERM / SIGINT): disconnect MQTT, let the queues drain (max. 15 s), stop the threads.
"""
from __future__ import annotations

import logging
import os
import queue
import signal
import threading
import time

from . import bootstrap, db
from .config import Config
from .counters import LiveCounters
from .listener import Listener
from .mqtt_client import make_client
from .scheduler import Scheduler
from .worker import StationInfo, Worker
from .writer import Writer

logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"),
                    format="%(asctime)s %(levelname)s [%(name)s] %(message)s")
log = logging.getLogger("main")


def prepare_database(cfg: Config) -> tuple[dict[str, StationInfo], bool, str, bool]:
    """Returns (stations, stage1_needed, stage1_sha256, clusters_empty)."""
    stage1_path = os.path.join(cfg.dataset_dir, bootstrap.STAGE1)
    stage1_sha = bootstrap.file_sha256(stage1_path)
    conn = db.connect(cfg)
    try:
        needed = not bootstrap.is_loaded(conn, bootstrap.STAGE1, stage1_sha)
        if needed:
            bootstrap.load_stations(conn, os.path.join(cfg.dataset_dir, bootstrap.STATIONS))
        stations = bootstrap.read_stations(conn)
        cur = conn.cursor()
        cur.execute("SELECT EXISTS(SELECT 1 FROM station_clusters)")
        (has_clusters,) = cur.fetchone()
        cur.close()
        conn.commit()
    finally:
        conn.close()
    return stations, needed, stage1_sha, not has_clusters


def wait_drained(*queues: queue.Queue, timeout_s: float) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if all(q.unfinished_tasks == 0 for q in queues):
            return True
        time.sleep(0.1)
    return False


def main() -> None:
    cfg = Config.from_env()
    shutdown = threading.Event()   # signal received
    stop = threading.Event()       # threads must exit once their queue is empty
    signal.signal(signal.SIGTERM, lambda *_: shutdown.set())
    signal.signal(signal.SIGINT, lambda *_: shutdown.set())

    raw_queue: queue.Queue = queue.Queue(maxsize=10_000)
    clean_queue: queue.Queue = queue.Queue(maxsize=10_000)
    counters = LiveCounters()
    mqtt_client = make_client(cfg)

    stations, stage1_needed, stage1_sha, clusters_empty = prepare_database(cfg)
    log.info("%d reference stations", len(stations))

    writer = Writer(cfg, clean_queue, counters, mqtt_client, stations, stop)
    writer.start()
    writer.ready.wait()
    workers = [Worker(i + 1, raw_queue, clean_queue, stations, stop) for i in range(cfg.worker_threads)]
    for w in workers:
        w.start()

    if stage1_needed:
        t0 = time.monotonic()
        n = bootstrap.feed_stage1(raw_queue, os.path.join(cfg.dataset_dir, bootstrap.STAGE1))
        log.info("%d stage 1 messages queued; waiting until they are committed", n)
        raw_queue.join()
        clean_queue.join()   # the Writer marks items done only after commit
        conn = db.connect(cfg)
        try:
            bootstrap.mark_loaded(conn, bootstrap.STAGE1, stage1_sha, n)
        finally:
            conn.close()
        log.info("stage 1 loaded in %.1f s", time.monotonic() - t0)
    else:
        log.info("stage 1 already loaded (ingest_log): not loaded again")

    scheduler = Scheduler(cfg, counters, mqtt_client, stop)
    listener = Listener(cfg, mqtt_client, raw_queue, on_connected=writer.republish.set)
    listener.start()
    scheduler.start()
    if stage1_needed or clusters_empty:
        scheduler.trigger_training("stage 1 loaded" if stage1_needed else "no clusters yet",
                                   event_ts=writer.last_event_ts)
    log.info("Processor ready with %d worker threads", len(workers))

    shutdown.wait()
    log.info("stopping: disconnecting MQTT and draining the queues")
    listener.stop()
    if not wait_drained(raw_queue, clean_queue, timeout_s=15):
        log.warning("queues not empty after 15 s; %d raw and %d clean items lost",
                    raw_queue.unfinished_tasks, clean_queue.unfinished_tasks)
    stop.set()
    writer.join(timeout=10)
    log.info("stopped")


if __name__ == "__main__":
    main()
