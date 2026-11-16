"""Main / Bootstrap (design-plan.md, secção 3).

Se a tabela stations estiver vazia: carrega stations.json e mete cada linha do
stage1_offline.jsonl na raw queue (passa pelo mesmo T2/T3 que o stage 2). Nunca carrega duas vezes.
A orquestração (ordem e espera pelas filas) está em main.py.
"""
from __future__ import annotations

import json  # noqa: F401  (usado na implementação)
import queue

from .contracts import RawMessage  # noqa: F401


def stations_loaded(conn) -> bool:
    """True se a tabela stations já tiver linhas."""
    # TODO(processor/bootstrap): SELECT COUNT(*) FROM stations
    raise NotImplementedError


def load_stations(conn, path: str) -> int:
    """Insere as 83 estações de stations.json (e faz commit). Devolve o número de linhas inseridas."""
    # TODO(processor/bootstrap)
    raise NotImplementedError


def feed_stage1(raw_queue: queue.Queue, path: str) -> int:
    """Mete cada linha do ficheiro na raw queue como RawMessage(source="stage1", payload=linha).

    Devolve o número de mensagens enviadas.
    """
    # TODO(processor/bootstrap)
    raise NotImplementedError
