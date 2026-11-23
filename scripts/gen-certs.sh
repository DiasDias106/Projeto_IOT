#!/usr/bin/env bash
# Creates the group CA and the server certificates (broker, MySQL, ML, Node-RED) in certs/.
# Usage: bash scripts/gen-certs.sh   (Git Bash on Windows, Linux or macOS)
# An existing CA is reused. To start over, delete certs/*.key and certs/*.crt.
set -euo pipefail
export MSYS_NO_PATHCONV=1   # Git Bash: do not turn "/CN=..." into a Windows path

cd "$(dirname "$0")/../certs"
DAYS=825

if [ ! -f ca.key ]; then
  openssl genrsa -out ca.key 4096
  # keyUsage is required by strict X.509 checks (Python 3.13+ default)
  openssl req -x509 -new -key ca.key -sha256 -days 3650 -subj "/CN=IdC fc15 CA" \
    -addext "basicConstraints=critical,CA:TRUE" -addext "keyUsage=critical,keyCertSign,cRLSign" -out ca.crt
  echo "CA created"
fi

issue() {
  local name=$1 sans=$2
  openssl genrsa -out "$name.key" 2048
  openssl req -new -key "$name.key" -subj "/CN=$name" -out "$name.csr"
  printf "subjectAltName=%s\nextendedKeyUsage=serverAuth\nkeyUsage=critical,digitalSignature,keyEncipherment\nauthorityKeyIdentifier=keyid,issuer\nsubjectKeyIdentifier=hash\n" "$sans" > "$name.ext"
  openssl x509 -req -in "$name.csr" -CA ca.crt -CAkey ca.key -CAcreateserial \
    -days "$DAYS" -sha256 -extfile "$name.ext" -out "$name.crt"
  rm -f "$name.csr" "$name.ext"
  echo "certificate $name issued ($sans)"
}

issue mosquitto "DNS:mosquitto,DNS:localhost,IP:127.0.0.1"
issue db        "DNS:db"
issue ml        "DNS:ml"
issue nodered   "DNS:nodered,DNS:localhost,IP:127.0.0.1"

# The services run as non-root users inside the containers and need to read the keys.
chmod 644 mosquitto.key db.key ml.key nodered.key
chmod 600 ca.key
