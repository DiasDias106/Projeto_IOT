"""Call to the ML service over HTTPS (design-plan.md, section 5)."""
from __future__ import annotations

import logging

import requests

from .config import Config

log = logging.getLogger(__name__)


def request_training(cfg: Config, timeout_s: float = 300) -> dict | None:
    """POST /train. Returns the ML response, or None on failure (must not stop the Processor)."""
    try:
        resp = requests.post(f"{cfg.ml_url}/train", verify=cfg.ca_cert, timeout=timeout_s)
        resp.raise_for_status()
        result = resp.json()
        log.info("ML training done: %s", result)
        return result
    except requests.RequestException as exc:
        log.warning("ML training request failed: %s", exc)
        return None
