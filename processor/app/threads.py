"""Base comum das threads que consomem uma fila (T2 Worker e T3 Writer)."""
from __future__ import annotations

import logging
import queue
import threading


class QueueConsumer(threading.Thread):
    """Tira itens da fila e chama handle(item). Marca task_done para o Bootstrap poder esperar."""

    def __init__(self, name: str, in_queue: queue.Queue, stop: threading.Event):
        super().__init__(name=name, daemon=True)
        self.in_queue = in_queue
        self.stop_event = stop
        self.log = logging.getLogger(name)
        self._warned_todo = False

    def handle(self, item) -> None:  # implementado nas subclasses
        raise NotImplementedError

    def on_idle(self) -> None:
        """Chamado quando a fila esteve vazia 0,5 s (útil para fazer flush de batches)."""

    def run(self) -> None:
        self.log.info("a arrancar")
        while not self.stop_event.is_set():
            try:
                item = self.in_queue.get(timeout=0.5)
            except queue.Empty:
                self.on_idle()
                continue
            try:
                self.handle(item)
            except NotImplementedError:
                if not self._warned_todo:
                    self.log.warning("TODO: handle() ainda não implementado; itens ignorados")
                    self._warned_todo = True
            except Exception:
                self.log.exception("erro ao tratar item: %r", item)
            finally:
                self.in_queue.task_done()
