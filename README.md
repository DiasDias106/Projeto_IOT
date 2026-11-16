# Bike-Sharing Infrastructure Management — IdC 2026/27

Plataforma IoT para monitorização e análise da rede de bike-sharing (Citi Bike — Jersey City e Hoboken), desenvolvida no âmbito da unidade curricular de Internet das Coisas (FCUL).

## Arquitetura

Serviços em Docker Compose:

| Serviço | Função |
|---|---|
| **MQTT Broker** (Mosquitto) | Recebe as mensagens das estações via MQTT sobre TLS |
| **Subscriber / Processor** | Carrega o dataset offline, subscreve os tópicos e guarda na BD |
| **DB** | Persistência de estações, estados e viagens |
| **ML Processor** | Clustering de estações por perfil de utilização |
| **Dashboard** | Visualização histórica e em tempo (quase) real |

O **Client 1** (publisher) reproduz o dataset online para o broker nos tópicos:

```
idc/fcXX/status/<station_id>
idc/fcXX/trips/started
idc/fcXX/trips/ended
```

## Estrutura

```
dataset-IdC-2627/      # stations.json, stage1_offline.jsonl, stage2_online.jsonl
```

## Como correr

1. Copiar as variáveis de ambiente e preencher:
   ```bash
   cp .env.example .env
   ```
2. Gerar os certificados TLS para a pasta `certs/` (não são versionados).
3. Levantar os serviços:
   ```bash
   docker compose up -d --build
   ```
4. Iniciar o publisher (stage 2) com o fator de velocidade pretendido (`REPLAY_SPEED`).

## Dataset

Dados abertos da Citi Bike (13–22 março 2024), 83 estações. Timestamps em hora local (UTC−04:00).
