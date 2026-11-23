# Bike-Sharing Infrastructure Management

IoT platform that monitors and analyses the Citi Bike network in Jersey City and Hoboken.

## Team (Internet of Things 2026/27, FCUL, group 15.)

| Name | Student number |
|---|---|
| Francisco Dias | fc68462 |
| Luís Lima | fc62214 |
| Tiago Freitas | fc67976 |


## Architecture

Docker Compose services on one private network. Every connection uses TLS, with certificates signed by the group's own CA.

| Service | Technology | Role | Host port |
|---|---|---|---|
| `client1` | Python, paho-mqtt (runs on the host) | Replays `stage2_online.jsonl` (waits `Δts / speed`, QoS 1, reconnects) | none |
| `mosquitto` | Eclipse Mosquitto | MQTT broker, user and password, ACL per client | 1883 (TLS) |
| `processor` | Python, paho-mqtt, mysql-connector | Loads stage 1, validates, computes distance, kWh and cost, stores, notifies | none |
| `db` | MySQL 8.4 | Storage. Only the processor writes data | none |
| `ml` | Flask, scikit-learn | `POST /train`, `GET /clusters` (K-Means) | none |
| `nodered` | Node-RED + dashboard | Operator dashboard, editor login | 1880 (HTTPS) |

Full design: [design-plan.md](design-plan.md). Processor details: [processor/PLAN.md](processor/PLAN.md).

## Install

Linux (Fedora, Debian/Ubuntu, Arch), macOS or Git Bash on Windows:

```bash
git clone https://github.com/DiasDias106/Projeto_IOT.git
cd Projeto_IOT
bash scripts/setup.sh
```

`setup.sh` installs git, openssl, Python and Docker with Compose when they are missing. It then creates `certs/`, `.env` and `.venv`. It prints the Node-RED editor password once: save it. You can run it again safely, because it only adds what is missing. On Windows, install Docker Desktop and Python 3.12+ first.

Options: `--start` also starts the services, and `--reset` deletes the database volume first (all data).

## Run

```bash
docker compose up -d --build
docker compose logs -f processor        # wait for "Processor ready"
```

On the first start the processor loads stage 1 (about 5 s) and requests the first training. A restart does not load it again.

Replay stage 2 in another terminal:

```bash
. .venv/bin/activate                    # Windows: .venv\Scripts\activate
python client1/publisher.py --speed 60  # 60 = 1 h of data per minute; 0 = no waiting
```

- Dashboard: https://localhost:1880/ui
- Editor: https://localhost:1880 (user `admin`)

The browser warns about the certificate because the group CA signs it. Import `certs/ca.crt` into the browser, or accept the warning.

After a schema change (`db/init/`), recreate the database with `docker compose down -v`. This deletes the data.

## MQTT topics

| Topic | Publisher | Subscriber |
|---|---|---|
| `idc/fc15/status/<station_id>` | client1 | processor |
| `idc/fc15/trips/started` | client1 | processor |
| `idc/fc15/trips/ended` | client1 | processor |
| `idc/fc15/internal/live_summary` | processor, every 2 s | nodered |
| `idc/fc15/internal/status/<station_id>` | processor, retained | nodered |

## Database

Schema: [db/init/01_schema.sql](db/init/01_schema.sql). Users: [db/init/02_users.sh](db/init/02_users.sh). All users must use TLS. The `processor` user writes the data, `ml` reads everything and writes only `station_clusters`, and `nodered` only reads.

| Table / view | Content |
|---|---|
| `stations` | 83 reference stations |
| `station_status` | one row per status message (`seq` unique), with `quality_flag` |
| `trips` | created at `trip_started`, completed at `trip_ended`, with distance, kWh, cost and `quality_flag` |
| `station_clusters` | one set of rows per training, key `(station_id, trained_at)` |
| `rejected_messages` | messages that cannot be stored, with the raw payload and the reason |
| `ingest_log` | dataset files loaded to the end |
| `v_data_quality` | issues per station: shows which sensors send bad data |

## Data quality

Invalid data is never deleted.

- **Flagged in place.** Trips, first match wins: `MISSING_END` > `MISSING_START` > `INVALID_DURATION` > `OUT_OF_AREA` > `OUTLIER_SHORT` (< 60 s) > `OUTLIER_LONG` (> 3 h) > `OK`. Trips still open after 3 h become `NEVER_ENDED`. Station status: `NEGATIVE_COUNT` > `EBIKES_GT_BIKES` > `OVER_CAPACITY` > `OK`.
- **Rejected** (cannot fit the table): `INVALID_JSON`, `MISSING_FIELD`, `INVALID_VALUE`, `UNKNOWN_STATION`, `TOPIC_MISMATCH` and others, stored in `rejected_messages`.

Dashboard queries use `WHERE quality_flag = 'OK'`.

## Node-RED connections

Credentials come from the container's environment. In the user and password fields, write `${MQTT_USER}`, `${MQTT_PASSWORD}`, `${DB_USER}` and `${DB_PASSWORD}`. No password is stored in the flows.

- **MQTT** (`mqtt in` node): server `mosquitto`, port `1883`, *Use TLS* with CA `/certs/ca.crt` and *Verify server certificate*. It can only subscribe to `idc/fc15/internal/#`.
- **MySQL** (`Stackhero-MySQL` node): host `db`, port `3306`, database `bikesharing`, *Encrypt connection*. The SQL goes in `msg.topic`, the parameters in `msg.payload`. `node-red-node-mysql` does not support TLS, so it cannot be used.

## Tests and tools

```bash
. .venv/bin/activate
pip install -r processor/requirements.txt
(cd processor && python -m pytest tests)               # 57 unit tests
python scripts/eda_offline.py                          # stage 1 cleaning statistics for the report
python scripts/infra_tests.py security                 # broker TLS, authentication and ACL
python scripts/infra_tests.py listen --expect 19856    # with: python client1/publisher.py --speed 0
```

## Repository layout

```
docker-compose.yml      the cloud services
.env.example            variables (setup.sh creates .env from it)
design-plan.md          solution design
scripts/                setup, certificates, .env, EDA, infrastructure tests, mock data
certs/                  TLS certificates (generated locally, never committed)
mosquitto/config/       mosquitto.conf, acl, entrypoint.sh
db/init/                SQL schema and users (run on the first start only)
processor/              processor (app/), plan (PLAN.md), unit tests (tests/)
ml/                     ML service (Flask, features, K-Means)
nodered/data/           settings.js (HTTPS, login) and flows.json (dashboard)
client1/                stage 2 publisher (runs on the host)
dataset-IdC-2627/       stations.json, stage1_offline.jsonl, stage2_online.jsonl
```

## Dataset

Citi Bike open data, 13 to 22 March 2024, 83 stations (53 in Jersey City, 30 in Hoboken). Timestamps are local time (UTC−04:00).

| File | Period | Messages |
|---|---|---|
| `stage1_offline.jsonl` | 13–19 March | 53,724 (`seq` 1–53,724) |
| `stage2_online.jsonl` | 20–22 March | 19,856 (`seq` 53,725–73,580) |
