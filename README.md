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

Esquema em [db/init/01_schema.sql](db/init/01_schema.sql): `stations`, `station_status` (`seq` único), `trips` (criada no `trip_started`, completada no `trip_ended`) e `station_clusters` (chave `(station_id, trained_at)`).
Utilizadores em [db/init/02_users.sh](db/init/02_users.sh), todos com TLS obrigatório: o `processor` escreve os dados, o `ml` lê tudo e escreve só `station_clusters`, o `nodered` só lê.

## Estrutura

```
docker-compose.yml        # os 5 serviços da cloud
.env.example              # variáveis (copiar para .env)
design-plan.md            # desenho da solução
scripts/gen-certs.sh      # gera a CA e os certificados TLS
scripts/gen-env.py        # gera o .env com passwords aleatórias
scripts/infra_tests.py    # testes de TLS/ACL do broker e contagem de mensagens
certs/                    # certificados (gerados localmente, não versionados)
mosquitto/config/         # mosquitto.conf, acl, entrypoint.sh (passwords vêm do .env)
db/init/                  # esquema SQL e utilizadores (correm na 1.ª inicialização)
processor/app/            # Processor: main, bootstrap, listener (T1), worker (T2), writer (T3), scheduler (T4)
ml/app/                   # serviço ML: Flask, features, modelo
nodered/data/             # settings.js (HTTPS, login) e flows.json (dashboard)
client1/publisher.py      # Client 1, corre no host
dataset-IdC-2627/         # stations.json, stage1_offline.jsonl, stage2_online.jsonl
```

## Divisão do trabalho

A infraestrutura já funciona: Docker Compose, TLS em todas as ligações, broker com ACL, esquema da BD, Node-RED com HTTPS e login. Também está feito o código partilhado do Processor: configuração, contratos entre threads (`contracts.py`), ligações TLS ao MySQL, ao MQTT e ao ML, o T1 Listener e o arranque em `main.py`.

O que falta está marcado com `TODO(<parte>)` no código. Enquanto uma parte não estiver feita, a app arranca na mesma e mostra um aviso no log.

| Parte | Ficheiros | O que implementar |
|---|---|---|
| **client1** | `client1/publisher.py` | `replay()`: espera `Δts / speed`, publica com QoS 1 |
| **processor/bootstrap** | `processor/app/bootstrap.py` | `stations_loaded()`, `load_stations()`, `feed_stage1()` |
| **processor/T2** | `processor/app/worker.py`, `calc.py` | parse, validação, `quality_flag`, Haversine, kWh, custo |
| **processor/T3** | `processor/app/writer.py` | inserts em batch e idempotentes, contadores, `internal/status/<id>` |
| **processor/T4** | `processor/app/scheduler.py` | `live_summary` a cada 2 s, retreino a cada 6 h de tempo de evento |
| **ml** | `ml/app/features.py`, `model.py` | features por estação, K-Means (k = 3, 4, 5), `station_clusters` |
| **dashboard** | Node-RED (editor) → `nodered/data/flows.json` | páginas de histórico, página live, mapa de clusters |

Para não haver conflitos, combinem com o grupo antes de alterar os ficheiros partilhados: `contracts.py`, `01_schema.sql`, `acl`, `docker-compose.yml`.

## Como correr

1. Criar o `.env` com passwords aleatórias (com o Docker a correr). O script mostra a password do editor Node-RED, que deve ser guardada:
   ```bash
   python scripts/gen-env.py
   ```
   Em alternativa, `cp .env.example .env` e preencher à mão (instruções no próprio ficheiro).
2. Gerar a CA e os certificados (Git Bash no Windows):
   ```bash
   bash scripts/gen-certs.sh
   ```
3. Levantar os serviços. No arranque, o Processor carrega o stage 1 se a BD estiver vazia e pede o primeiro treino:
   ```bash
   docker compose up -d --build
   docker compose logs -f processor
   ```
4. Correr o publisher do stage 2 no host:
   ```bash
   pip install -r client1/requirements.txt
   python client1/publisher.py --speed 60
   ```
5. Abrir o editor em `https://localhost:1880` e o dashboard em `https://localhost:1880/ui`. O browser avisa sobre o certificado, que é da CA do grupo: aceitar ou importar `certs/ca.crt`.

6. (Opcional) Testar a infraestrutura: TLS, autenticação e ACL do broker; contagem das mensagens do client1:
   ```bash
   python scripts/infra_tests.py security
   python scripts/infra_tests.py listen --expect 19856   # noutro terminal: python client1/publisher.py --speed 0
   ```

Notas:
- O esquema e os utilizadores da BD só são criados na primeira inicialização. Depois de os alterar, recriar o volume com `docker compose down -v`. Isto apaga os dados.
- No Node-RED, os nós MQTT ligam a `mosquitto:1883` com TLS (CA `/certs/ca.crt`) e utilizador `nodered`. Os nós MySQL ligam a `db:3306` com o utilizador `nodered`. As passwords estão nas variáveis de ambiente `MQTT_PASSWORD` e `DB_PASSWORD` do container (usar `${MQTT_PASSWORD}` nos campos).

## Dataset

Dados abertos da Citi Bike entre 13 e 22 de março de 2024, com 83 estações (53 em Jersey City e 30 em Hoboken). Os timestamps estão em hora local (UTC−04:00).

| Ficheiro | Período | Mensagens |
|---|---|---|
| `stage1_offline.jsonl` | 13–19 mar | 53 724 (`seq` 1–53 724) |
| `stage2_online.jsonl` | 20–22 mar | 19 856 (`seq` 53 725–73 580) |

Tratamento de imperfeições (por prioridade): `MISSING_END` > `OUT_OF_AREA` > `OUTLIER_SHORT` > `OUTLIER_LONG` > `OK`. As viagens que nunca terminam são fechadas como `NEVER_ENDED`.
