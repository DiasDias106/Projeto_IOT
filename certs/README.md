# certs/

TLS certificates are **not committed**. Each person generates their own with `bash scripts/setup.sh` (or `bash scripts/gen-certs.sh`).

| File | Used by | SAN |
|---|---|---|
| `ca.crt` / `ca.key` | group CA (signs the others) | none |
| `mosquitto.crt` / `.key` | MQTT broker | `mosquitto`, `localhost`, `127.0.0.1` |
| `db.crt` / `.key` | MySQL | `db` |
| `ml.crt` / `.key` | ML service (HTTPS) | `ml` |
| `nodered.crt` / `.key` | Node-RED (HTTPS) | `nodered`, `localhost`, `127.0.0.1` |

Clients (client1, processor, Node-RED, ML) verify the server with `ca.crt`. Never share `ca.key`.
