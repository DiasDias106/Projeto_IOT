#!/usr/bin/env bash
# Gera a CA do grupo e os certificados de servidor (broker, MySQL, ML, Node-RED) em certs/.
# Uso: bash scripts/gen-certs.sh   (Git Bash no Windows, Linux ou macOS)
# Uma CA já existente é reaproveitada. Para recomeçar do zero, apagar certs/*.key e certs/*.crt.
set -euo pipefail
export MSYS_NO_PATHCONV=1   # Git Bash: não converter "/CN=..." num caminho do Windows

cd "$(dirname "$0")/../certs"
DAYS=825

if [ ! -f ca.key ]; then
  openssl genrsa -out ca.key 4096
  openssl req -x509 -new -key ca.key -sha256 -days 3650 -subj "/CN=IdC fc15 CA" -out ca.crt
  echo "CA criada"
fi

issue() {
  local name=$1 sans=$2
  openssl genrsa -out "$name.key" 2048
  openssl req -new -key "$name.key" -subj "/CN=$name" -out "$name.csr"
  printf "subjectAltName=%s\nextendedKeyUsage=serverAuth\n" "$sans" > "$name.ext"
  openssl x509 -req -in "$name.csr" -CA ca.crt -CAkey ca.key -CAcreateserial \
    -days "$DAYS" -sha256 -extfile "$name.ext" -out "$name.crt"
  rm -f "$name.csr" "$name.ext"
  echo "certificado $name emitido ($sans)"
}

issue mosquitto "DNS:mosquitto,DNS:localhost,IP:127.0.0.1"
issue db        "DNS:db"
issue ml        "DNS:ml"
issue nodered   "DNS:nodered,DNS:localhost,IP:127.0.0.1"

# Os serviços correm como utilizadores não-root dentro dos containers e precisam de ler as chaves.
chmod 644 mosquitto.key db.key ml.key nodered.key
chmod 600 ca.key
