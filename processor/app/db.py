"""MySQL connection over TLS. Used by the T3 Writer, Bootstrap and main only."""
from __future__ import annotations

import logging
import time

import mysql.connector

from .config import Config

log = logging.getLogger(__name__)


def connect(cfg: Config, retries: int = 30, delay_s: float = 2.0):
    """Opens a TLS connection (verifies the server certificate with the group CA)."""
    last_error: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            conn = mysql.connector.connect(
                host=cfg.db_host,
                port=cfg.db_port,
                database=cfg.db_name,
                user=cfg.db_user,
                password=cfg.db_password,
                ssl_ca=cfg.ca_cert,
                ssl_verify_cert=True,
                ssl_verify_identity=True,
                autocommit=False,
            )
            log.info("connected to MySQL %s:%s (TLS)", cfg.db_host, cfg.db_port)
            return conn
        except mysql.connector.Error as exc:
            last_error = exc
            log.warning("MySQL unavailable (attempt %d/%d): %s", attempt, retries, exc)
            time.sleep(delay_s)
    raise RuntimeError("Could not connect to MySQL") from last_error
