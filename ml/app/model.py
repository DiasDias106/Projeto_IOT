"""Clustering de estações (design-plan.md, secção 5).

StandardScaler + K-Means para k = 3, 4, 5; ganha o melhor silhouette.
Cada treino escreve um novo conjunto de linhas em station_clusters (chave station_id, trained_at).
"""
from __future__ import annotations

import pandas as pd

K_CANDIDATES = (3, 4, 5)


def train_and_store(conn, features: pd.DataFrame) -> dict:
    """Treina, escolhe o melhor k, insere em station_clusters e faz commit.

    Devolve um resumo para a resposta HTTP, por exemplo:
      {"trained_at": "...", "k": 4, "silhouette": 0.41, "stations": 83}
    """
    # TODO(ml)
    raise NotImplementedError


def latest_clusters(conn) -> list[dict]:
    """Atribuições do treino mais recente: [{"station_id", "cluster", "trained_at", ...}]."""
    # TODO(ml)
    raise NotImplementedError
