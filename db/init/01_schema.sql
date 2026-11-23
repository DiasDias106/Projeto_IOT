-- Database schema (design-plan.md, section 4). Runs only on the first start of the db_data volume.
-- DATETIME stores local time as received (UTC-04:00, no clock change between 13 and 22 March).

CREATE TABLE IF NOT EXISTS stations (
    station_id    VARCHAR(16)  NOT NULL,
    station_uuid  CHAR(36)     NOT NULL,
    name          VARCHAR(128) NOT NULL,
    lat           DOUBLE       NOT NULL,
    lon           DOUBLE       NOT NULL,
    capacity      SMALLINT     NOT NULL,
    city          VARCHAR(32)  NOT NULL,
    PRIMARY KEY (station_id),
    UNIQUE KEY uq_station_uuid (station_uuid)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- One row per station_status message. It arrives only when the state changes:
-- the state is valid until the next row of the same station.
CREATE TABLE IF NOT EXISTS station_status (
    id                    BIGINT      NOT NULL AUTO_INCREMENT,
    seq                   BIGINT      NOT NULL,          -- unique across both files: makes the insert idempotent
    station_id            VARCHAR(16) NOT NULL,
    ts                    DATETIME    NOT NULL,
    num_bikes_available   SMALLINT    NOT NULL,          -- includes e-bikes (classic = bikes - ebikes)
    num_ebikes_available  SMALLINT    NOT NULL,
    num_bikes_disabled    SMALLINT    NOT NULL,
    num_docks_available   SMALLINT    NOT NULL,
    num_docks_disabled    SMALLINT    NOT NULL,
    is_installed          TINYINT(1)  NOT NULL,
    is_renting            TINYINT(1)  NOT NULL,
    is_returning          TINYINT(1)  NOT NULL,
    -- Data quality: suspicious rows are kept and flagged, never deleted (processor/PLAN.md, section 3)
    quality_flag          ENUM('OK', 'NEGATIVE_COUNT', 'EBIKES_GT_BIKES', 'OVER_CAPACITY') NOT NULL DEFAULT 'OK',
    source                ENUM('stage1', 'mqtt') NOT NULL,
    PRIMARY KEY (id),
    UNIQUE KEY uq_status_seq (seq),
    KEY idx_status_station_ts (station_id, ts),
    KEY idx_status_station_seq (station_id, seq),
    KEY idx_status_ts (ts),
    KEY idx_status_flag (quality_flag),
    CONSTRAINT fk_status_station FOREIGN KEY (station_id) REFERENCES stations (station_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- Row created at trip_started (ended_at = NULL) and completed at trip_ended.
-- Bikes in use = COUNT(*) WHERE ended_at IS NULL AND quality_flag IS NULL.
-- No FK on stations: some trips start or end outside the 83 stations (quality_flag = OUT_OF_AREA).
CREATE TABLE IF NOT EXISTS trips (
    trip_id             VARCHAR(32)  NOT NULL,           -- = ride_id
    rideable_type       ENUM('classic_bike', 'electric_bike') NOT NULL,
    member_casual       ENUM('member', 'casual') NOT NULL,
    started_at          DATETIME     NOT NULL,
    ended_at            DATETIME     NULL,
    duration_s          INT          NULL,
    start_station_id    VARCHAR(16)  NULL,
    start_station_name  VARCHAR(128) NULL,
    end_station_id      VARCHAR(16)  NULL,
    end_station_name    VARCHAR(128) NULL,
    start_lat           DOUBLE       NULL,
    start_lng           DOUBLE       NULL,
    end_lat             DOUBLE       NULL,
    end_lng             DOUBLE       NULL,
    distance_km         DOUBLE       NULL,               -- Haversine between stations (0 for round trips)
    energy_kwh          DOUBLE       NULL,               -- 15 Wh/km, electric_bike only
    cost_usd            DECIMAL(8,2) NULL,
    quality_flag        ENUM('OK', 'MISSING_END', 'MISSING_START', 'INVALID_DURATION', 'OUT_OF_AREA',
                             'OUTLIER_SHORT', 'OUTLIER_LONG', 'NEVER_ENDED') NULL,  -- NULL while the trip is open
    source              ENUM('stage1', 'mqtt') NULL,         -- source of the message that completed the row (trip_ended)
    seq_start           BIGINT       NULL,
    seq_end             BIGINT       NULL,
    PRIMARY KEY (trip_id),
    KEY idx_trips_started (started_at),
    KEY idx_trips_ended (ended_at),
    KEY idx_trips_start_station (start_station_id, started_at),
    KEY idx_trips_end_station (end_station_id, ended_at),
    KEY idx_trips_flag (quality_flag),
    KEY idx_trips_source_flag (source, quality_flag)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- One set of rows per training. trained_at in the key keeps the history of the profiles.
CREATE TABLE IF NOT EXISTS station_clusters (
    station_id   VARCHAR(16) NOT NULL,
    trained_at   DATETIME    NOT NULL,                   -- event time of the data used in the training
    cluster      TINYINT     NOT NULL,
    k            TINYINT     NOT NULL,
    silhouette   DOUBLE      NULL,
    features     JSON        NULL,                       -- feature values used, for the dashboard
    PRIMARY KEY (station_id, trained_at),
    KEY idx_clusters_trained (trained_at),
    CONSTRAINT fk_clusters_station FOREIGN KEY (station_id) REFERENCES stations (station_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- Dead-letter table: messages that cannot be stored in their own table (processor/PLAN.md, section 3).
-- The raw payload is kept for audit and replay. station_id is a best effort (payload, then topic).
CREATE TABLE IF NOT EXISTS rejected_messages (
    id              BIGINT       NOT NULL AUTO_INCREMENT,
    received_at     DATETIME(3)  NOT NULL,               -- processor wall clock (UTC)
    source          ENUM('stage1', 'mqtt') NOT NULL,
    topic           VARCHAR(255) NULL,
    msg_type        VARCHAR(32)  NULL,
    seq             BIGINT       NULL,
    station_id      VARCHAR(16)  NULL,
    event_ts        DATETIME     NULL,                   -- message ts, when it could be read
    reason          ENUM('OVERSIZE', 'INVALID_JSON', 'NOT_AN_OBJECT', 'UNKNOWN_TYPE', 'MISSING_FIELD',
                         'INVALID_VALUE', 'UNKNOWN_STATION', 'TOPIC_MISMATCH', 'DB_REJECTED') NOT NULL,
    detail          VARCHAR(255) NULL,
    payload         BLOB         NOT NULL,               -- raw bytes, at most 64 KiB
    payload_sha256  CHAR(64)     NOT NULL,               -- same bytes delivered twice are stored once
    PRIMARY KEY (id),
    UNIQUE KEY uq_rejected_payload (payload_sha256, reason),
    KEY idx_rejected_station (station_id, reason),
    KEY idx_rejected_received (received_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- One row per dataset file loaded to the end. No row = load again (inserts are idempotent).
CREATE TABLE IF NOT EXISTS ingest_log (
    dataset       VARCHAR(64) NOT NULL,
    sha256        CHAR(64)    NOT NULL,
    messages      INT         NOT NULL,
    completed_at  DATETIME    NOT NULL,                  -- UTC
    PRIMARY KEY (dataset)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- Data quality per station and issue, for the dashboard (which sensors send bad data).
-- Trips are attributed to their start station.
CREATE OR REPLACE SQL SECURITY INVOKER VIEW v_data_quality AS
    SELECT station_id, 'station_status' AS origin, quality_flag AS issue,
           COUNT(*) AS occurrences, MIN(ts) AS first_seen, MAX(ts) AS last_seen
      FROM station_status
     WHERE quality_flag <> 'OK'
     GROUP BY station_id, quality_flag
    UNION ALL
    SELECT start_station_id, 'trips', quality_flag,
           COUNT(*), MIN(started_at), MAX(started_at)
      FROM trips
     WHERE quality_flag IS NOT NULL AND quality_flag <> 'OK'
     GROUP BY start_station_id, quality_flag
    UNION ALL
    SELECT station_id, 'rejected_messages', reason,
           COUNT(*), MIN(COALESCE(event_ts, received_at)), MAX(COALESCE(event_ts, received_at))
      FROM rejected_messages
     GROUP BY station_id, reason;
