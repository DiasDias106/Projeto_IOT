"""Chamada ao serviço ML por HTTPS (design-plan.md, secção 5)."""
from __future__ import annotations

import logging

import requests

from .config import Config

log = logging.getLogger(__name__)


def request_training(cfg: Config, timeout_s: float = 300) -> dict | None:
    """POST /train. Devolve a resposta do ML, ou None se falhar (não deve parar o Processor)."""
    try:
        resp = requests.post(f"{cfg.ml_url}/train", verify=cfg.ca_cert, timeout=timeout_s)
        resp.raise_for_status()
        result = resp.json()
        log.info("Treino ML concluído: %s", result)
        return result
    except requests.RequestException as exc:
        log.warning("Falha ao pedir treino ao ML: %s", exc)
        return None
