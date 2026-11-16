# certs/

Os certificados TLS **não são versionados**. Cada pessoa gera os seus com:

```bash
bash scripts/gen-certs.sh
```

| Ficheiro | Usado por | SAN |
|---|---|---|
| `ca.crt` / `ca.key` | CA do grupo (assina os restantes) | — |
| `mosquitto.crt` / `.key` | broker MQTT | `mosquitto`, `localhost`, `127.0.0.1` |
| `db.crt` / `.key` | MySQL | `db` |
| `ml.crt` / `.key` | serviço ML (HTTPS) | `ml` |
| `nodered.crt` / `.key` | Node-RED (HTTPS) | `nodered`, `localhost`, `127.0.0.1` |

Os clientes (client1, processor, Node-RED, ML) validam o servidor com `ca.crt`.
Nunca partilhar o `ca.key`.
