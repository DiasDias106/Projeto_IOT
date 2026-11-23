"""MySQL connection over TLS (user ml: reads everything, writes only station_clusters)."""
from __future__ import annotations

import os

import mysql.connector


def connect():
    return mysql.connector.connect(
        host=os.environ.get("DB_HOST", "db"),
        port=int(os.environ.get("DB_PORT", "3306")),
        database=os.environ.get("DB_NAME", "bikesharing"),
        user=os.environ["DB_USER"],
        password=os.environ["DB_PASSWORD"],
        ssl_ca=os.environ.get("CA_CERT", "/certs/ca.crt"),
        ssl_verify_cert=True,
        ssl_verify_identity=True,
        autocommit=False,
    )
