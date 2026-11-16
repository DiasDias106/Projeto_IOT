# Bike-Sharing Infrastructure Management — IdC 2026/27 (Grupo 15)

Plataforma IoT para monitorização e análise da rede de bike-sharing da Citi Bike em Jersey City e Hoboken, desenvolvida para a unidade curricular de Internet das Coisas (FCUL).

O desenho completo está em [design-plan.md](design-plan.md).

## Arquitetura

Serviços em Docker Compose, numa rede privada:

| Serviço | Tecnologia | Função | Porta no host |
|---|---|---|---|
| `client1` | Python 3.12, paho-mqtt (no host) | Reproduz `stage2_online.jsonl` com espera `Δts / speed`, QoS 1, reconexão automática | — |
| `mosquitto` | eclipse-mosquitto | Broker MQTT com TLS, utilizador e password, ACL por cliente | 1883 (TLS) |
| `processor` | Python 3.12, paho-mqtt, mysql-connector | Carrega o stage 1, valida, calcula distância, kWh e custo, guarda e notifica | — |
| `db` | MySQL | Persistência. Só o Processor escreve | — |
| `ml` | Python 3.12, Flask, scikit-learn | `POST /train`, `GET /clusters` (K-Means) | — |
| `nodered` | Node-RED + dashboard | Dashboard dos operadores, HTTPS, login no editor | 1880 (HTTPS) |

Todas as comunicações usam TLS, com certificados assinados por uma CA própria.

## Tópicos MQTT

| Tópico | Publica | Subscreve |
|---|---|---|
| `idc/fc15/status/<station_id>` | client1 | processor |
| `idc/fc15/trips/started` | client1 | processor |
| `idc/fc15/trips/ended` | client1 | processor |
| `idc/fc15/internal/live_summary` | processor (a cada 2 s) | nodered |
| `idc/fc15/internal/status/<station_id>` | processor | nodered |

## Base de dados

`stations`, `station_status` (`seq` único), `trips` (criada no `trip_started`, completada no `trip_ended`) e `station_clusters` (chave `(station_id, trained_at)`).

## Estrutura

```
dataset-IdC-2627/      # stations.json, stage1_offline.jsonl, stage2_online.jsonl
design-plan.md         # desenho da solução
certs/                 # certificados TLS gerados localmente (não versionados)
```

## Como correr

1. Copiar as variáveis de ambiente e preencher as passwords:
   ```bash
   cp .env.example .env
   ```
2. Gerar a CA e os certificados do broker, Node-RED, MySQL e ML para `certs/`.
3. Levantar os serviços (no arranque, o Processor carrega o stage 1 se a BD estiver vazia e pede o primeiro treino):
   ```bash
   docker compose up -d --build
   ```
4. Correr o publisher do stage 2 no host, com o fator de velocidade pretendido:
   ```bash
   python client1/publisher.py --speed 60
   ```
5. Abrir o dashboard em `https://localhost:1880`.

## Dataset

Dados abertos da Citi Bike entre 13 e 22 de março de 2024, com 83 estações (53 em Jersey City e 30 em Hoboken). Os timestamps estão em hora local (UTC−04:00).

| Ficheiro | Período | Mensagens |
|---|---|---|
| `stage1_offline.jsonl` | 13–19 mar | 53 724 (`seq` 1–53 724) |
| `stage2_online.jsonl` | 20–22 mar | 19 856 (`seq` 53 725–73 580) |

Tratamento de imperfeições (por prioridade): `MISSING_END` > `OUT_OF_AREA` > `OUTLIER_SHORT` > `OUTLIER_LONG` > `OK`. As viagens que nunca terminam são fechadas como `NEVER_ENDED`.
