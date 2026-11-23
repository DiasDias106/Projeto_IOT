#!/usr/bin/env bash
# Starts the services, waits until they are ready and prints the addresses to open.
#
# Usage:  bash scripts/run.sh            start (builds what changed), wait, print the URLs
#         bash scripts/run.sh restart    restart every service, wait, print the URLs
#         bash scripts/run.sh status     print the state of the services and the URLs
#         bash scripts/run.sh stop       stop the services (the data is kept)
#         bash scripts/run.sh logs [service]   follow the logs (Ctrl+C to leave)
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

say()  { printf '\n\033[1m==> %s\033[0m\n' "$*"; }
die()  { printf '\033[31m[x] %s\033[0m\n' "$*" >&2; exit 1; }

[ -f .env ] || die ".env not found. Run: bash scripts/setup.sh"

DOCKER="docker"
if ! docker info >/dev/null 2>&1; then
  if command -v sudo >/dev/null 2>&1 && sudo docker info >/dev/null 2>&1; then DOCKER="sudo docker"
  else die "Docker is not running. Start it (Docker Desktop, or 'sudo systemctl start docker') and run again."
  fi
fi
compose() { $DOCKER compose "$@"; }

port_busy() { (exec 3<>"/dev/tcp/127.0.0.1/$1") 2>/dev/null; }
check_ports() {
  local running port svc
  running="$(compose ps --services --status running 2>/dev/null || true)"
  for pair in 1883:mosquitto 1880:nodered; do
    port="${pair%%:*}" svc="${pair##*:}"
    if port_busy "$port" && ! grep -qx "$svc" <<<"$running"; then
      die "Port $port is already in use by another program. Find it with: sudo ss -ltnp 'sport = :$port' (a local mosquitto: sudo systemctl disable --now mosquitto)."
    fi
  done
}

# Waits until Node-RED is healthy and the processor has written "Processor ready" since it last started.
wait_ready() {
  local deadline=$((SECONDS + 300)) cid started health
  printf 'Waiting for the services (the first start also loads stage 1)'
  while :; do
    health="$(compose ps nodered --format '{{.Health}}' 2>/dev/null || true)"
    cid="$(compose ps -q processor 2>/dev/null || true)"
    if [ "$health" = healthy ] && [ -n "$cid" ]; then
      started="$($DOCKER inspect -f '{{.State.StartedAt}}' "$cid")"
      if $DOCKER logs --since "$started" "$cid" 2>&1 | grep -q "Processor ready"; then echo " ready."; return 0; fi
    fi
    if [ "$SECONDS" -ge "$deadline" ]; then
      echo
      compose ps
      die "Not ready after 5 minutes. See: bash scripts/run.sh logs processor (or nodered)"
    fi
    printf '.'; sleep 3
  done
}

print_urls() {
  local venv=". .venv/bin/activate"
  [ -d .venv/Scripts ] && venv=". .venv/Scripts/activate"
  cat <<EOF

  Dashboard (Client 2):  https://localhost:1880/ui
  Node-RED editor:       https://localhost:1880/   (user admin, password printed by setup.sh)
  MQTT broker (TLS):     mqtts://localhost:1883    (Client 1 connects here)

  The browser warns about the certificate: import certs/ca.crt, or accept the warning.

  Replay stage 2 (Client 1), in another terminal:
    $venv && python client1/publisher.py --speed 60
EOF
}

cmd="${1:-start}"
case "$cmd" in
  start)
    check_ports
    say "Starting the services"
    compose up -d --build
    wait_ready
    say "Running"; print_urls ;;
  restart)
    say "Restarting the services"
    compose restart
    wait_ready
    say "Running"; print_urls ;;
  status)
    compose ps
    if [ "$(compose ps nodered --format '{{.Health}}' 2>/dev/null || true)" = healthy ]; then print_urls
    else echo; echo "Node-RED is not running. Start with: bash scripts/run.sh"; fi ;;
  stop)
    say "Stopping the services (data kept)"
    compose stop ;;
  logs)
    shift; compose logs -f --tail 50 "$@" ;;
  -h|--help) sed -n 2,8p "$0" ;;
  *) die "Unknown command: $cmd (use start, restart, status, stop or logs)" ;;
esac
