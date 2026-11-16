-- Esquema da BD (design-plan.md, secção 4). Corre só na primeira inicialização do volume db_data.
-- DATETIME guarda a hora local tal como chega (UTC-04:00, sem mudança de hora entre 13 e 22 de março).

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

-- Uma linha por mensagem station_status. Só chega quando o estado muda:
-- o estado vale até à linha seguinte da mesma estação.
CREATE TABLE IF NOT EXISTS station_status (
    id                    BIGINT      NOT NULL AUTO_INCREMENT,
    seq                   BIGINT      NOT NULL,          -- único nos dois ficheiros: torna o insert idempotente
    station_id            VARCHAR(16) NOT NULL,
    ts                    DATETIME    NOT NULL,
    num_bikes_available   SMALLINT    NOT NULL,          -- inclui as elétricas (clássicas = bikes - ebikes)
    num_ebikes_available  SMALLINT    NOT NULL,
    num_bikes_disabled    SMALLINT    NOT NULL,
    num_docks_available   SMALLINT    NOT NULL,
    num_docks_disabled    SMALLINT    NOT NULL,
    is_installed          TINYINT(1)  NOT NULL,
    is_renting            TINYINT(1)  NOT NULL,
    is_returning          TINYINT(1)  NOT NULL,
    PRIMARY KEY (id),
    UNIQUE KEY uq_status_seq (seq),
    KEY idx_status_station_ts (station_id, ts),
    KEY idx_status_ts (ts),
    CONSTRAINT fk_status_station FOREIGN KEY (station_id) REFERENCES stations (station_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- Linha criada no trip_started (ended_at = NULL) e completada no trip_ended.
-- Bicicletas em uso = COUNT(*) WHERE ended_at IS NULL.
-- Sem FK nas estações: há viagens de/para estações fora das 83 (quality_flag = OUT_OF_AREA).
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
    distance_km         DOUBLE       NULL,               -- Haversine entre estações (0 em viagens circulares)
    energy_kwh          DOUBLE       NULL,               -- 15 Wh/km só para electric_bike
    cost_usd            DECIMAL(8,2) NULL,
    quality_flag        ENUM('OK', 'MISSING_END', 'OUT_OF_AREA', 'OUTLIER_SHORT', 'OUTLIER_LONG', 'NEVER_ENDED') NULL,  -- NULL enquanto a viagem está aberta
    seq_start           BIGINT       NULL,
    seq_end             BIGINT       NULL,
    PRIMARY KEY (trip_id),
    KEY idx_trips_started (started_at),
    KEY idx_trips_ended (ended_at),
    KEY idx_trips_start_station (start_station_id, started_at),
    KEY idx_trips_end_station (end_station_id, ended_at),
    KEY idx_trips_flag (quality_flag)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- Um conjunto de linhas por treino. trained_at na chave permite ver a evolução dos perfis.
CREATE TABLE IF NOT EXISTS station_clusters (
    station_id   VARCHAR(16) NOT NULL,
    trained_at   DATETIME    NOT NULL,                   -- tempo de evento dos dados usados no treino
    cluster      TINYINT     NOT NULL,
    k            TINYINT     NOT NULL,
    silhouette   DOUBLE      NULL,
    features     JSON        NULL,                       -- valores das features usadas, para o dashboard
    PRIMARY KEY (station_id, trained_at),
    KEY idx_clusters_trained (trained_at),
    CONSTRAINT fk_clusters_station FOREIGN KEY (station_id) REFERENCES stations (station_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
