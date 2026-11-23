"""Serviço ML em Flask sobre HTTPS (design-plan.md, secção 5).

  GET  /health     -> {"status": "ok"}
  POST /train      -> treina com os dados atuais e grava em station_clusters
  GET  /clusters   -> atribuições do treino mais recente
"""
from __future__ import annotations

import logging
import os
import threading

from flask import Flask, jsonify

from . import db
from .features import build_features
from .model import latest_clusters, train_and_store

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s [%(name)s] %(message)s")
app = Flask(__name__)
_train_lock = threading.Lock()   # um treino de cada vez


@app.get("/health")
def health():
    return jsonify(status="ok")


@app.post("/train")
def train():
    if not _train_lock.acquire(blocking=False):
        return jsonify(error="training already running"), 409
    conn = db.connect()
    try:
        result = train_and_store(conn, build_features(conn))
        return jsonify(result)
    except ValueError as exc:
        return jsonify(error=str(exc)), 422
    finally:
        conn.close()
        _train_lock.release()


@app.get("/clusters")
def clusters():
    conn = db.connect()
    try:
        return jsonify(latest_clusters(conn))
    finally:
        conn.close()


if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=5000,
        ssl_context=(os.environ.get("TLS_CERT", "/certs/server.crt"),
                     os.environ.get("TLS_KEY", "/certs/server.key")),
        threaded=True,
    )
