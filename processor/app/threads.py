"""Common base of the threads that consume a queue (T2 Workers)."""
from __future__ import annotations

import logging
import queue
import threading


class QueueConsumer(threading.Thread):
    """Takes items from the queue and calls handle(item). Marks task_done so Bootstrap can wait."""

    def __init__(self, name: str, in_queue: queue.Queue, stop: threading.Event):
        super().__init__(name=name, daemon=True)
        self.in_queue = in_queue
        self.stop_event = stop
        self.log = logging.getLogger(name)

    def handle(self, item) -> None:  # implemented by subclasses
        raise NotImplementedError

    def run(self) -> None:
        self.log.info("starting")
        while not (self.stop_event.is_set() and self.in_queue.empty()):
            try:
                item = self.in_queue.get(timeout=0.5)
            except queue.Empty:
                continue
            try:
                self.handle(item)
            except Exception:
                self.log.exception("error handling an item")
            finally:
                self.in_queue.task_done()
