"""Processor configuration, read from environment variables (see docker-compose.yml and .env.example)."""
from __future__ import annotations

import os
from dataclasses import dataclass


def _env(name: str, default: str | None = None) -> str:
    value = os.environ.get(name, default)
    if value is None or value == "":
        raise RuntimeError(f"Missing environment variable: {name}")
    return value


@dataclass(frozen=True)
class Config:
    topic_prefix: str
    mqtt_host: str
    mqtt_port: int
    mqtt_user: str
    mqtt_password: str
    ca_cert: str
    db_host: str
    db_port: int
    db_name: str
    db_user: str
    db_password: str
    ml_url: str
    retrain_every_hours: int
    summary_interval_s: float
    dataset_dir: str
    worker_threads: int
    writer_batch_size: int
    writer_flush_s: float

    @classmethod
    def from_env(cls) -> "Config":
        return cls(
            topic_prefix=_env("TOPIC_PREFIX", "idc/fc15"),
            mqtt_host=_env("MQTT_HOST", "mosquitto"),
            mqtt_port=int(_env("MQTT_PORT", "1883")),
            mqtt_user=_env("MQTT_USER"),
            mqtt_password=_env("MQTT_PASSWORD"),
            ca_cert=_env("CA_CERT", "/certs/ca.crt"),
            db_host=_env("DB_HOST", "db"),
            db_port=int(_env("DB_PORT", "3306")),
            db_name=_env("DB_NAME", "bikesharing"),
            db_user=_env("DB_USER"),
            db_password=_env("DB_PASSWORD"),
            ml_url=_env("ML_URL", "https://ml:5000"),
            retrain_every_hours=int(_env("RETRAIN_EVERY_HOURS", "6")),
            summary_interval_s=float(_env("SUMMARY_INTERVAL_S", "2")),
            dataset_dir=_env("DATASET_DIR", "/dataset"),
            worker_threads=max(1, int(_env("WORKER_THREADS", "2"))),
            writer_batch_size=max(1, int(_env("WRITER_BATCH_SIZE", "500"))),
            writer_flush_s=float(_env("WRITER_FLUSH_S", "0.5")),
        )

    # Topics (design-plan.md, section 2)
    @property
    def topic_status_sub(self) -> str:
        return f"{self.topic_prefix}/status/+"

    @property
    def topic_trips_sub(self) -> str:
        return f"{self.topic_prefix}/trips/+"

    @property
    def topic_live_summary(self) -> str:
        return f"{self.topic_prefix}/internal/live_summary"

    def topic_internal_status(self, station_id: str) -> str:
        return f"{self.topic_prefix}/internal/status/{station_id}"
