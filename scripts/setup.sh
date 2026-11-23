#!/usr/bin/env bash
# One-time setup: installs what the project needs, then prepares certs, .env and the Python venv.
#
# Usage:  bash scripts/setup.sh            install + prepare
#         bash scripts/setup.sh --start    ... and start the services (scripts/run.sh)
#         bash scripts/setup.sh --reset    ... and delete the DB volume first (docker compose down -v)
#         bash scripts/setup.sh --no-install   skip system packages (only certs, .env, venv)
#
# Installs: git, openssl, Python 3 with venv, Docker Engine with the Compose plugin.
# Supported: Fedora, Debian/Ubuntu, Arch, macOS (Homebrew). On Windows, use Git Bash with
# Docker Desktop and Python already installed: the script checks them and does the rest.
# Safe to run again: it only adds what is missing.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

INSTALL=1 START=0 RESET=0
for arg in "$@"; do
  case "$arg" in
    --no-install) INSTALL=0 ;;
    --start) START=1 ;;
    --reset) RESET=1; START=1 ;;
    -h|--help) sed -n 2,12p "$0"; exit 0 ;;
    *) echo "Unknown option: $arg" >&2; exit 2 ;;
  esac
done

say()  { printf '\n\033[1m==> %s\033[0m\n' "$*"; }
warn() { printf '\033[33m[!] %s\033[0m\n' "$*"; }
die()  { printf '\033[31m[x] %s\033[0m\n' "$*" >&2; exit 1; }
have() { command -v "$1" >/dev/null 2>&1; }

SUDO=""
if [ "$(id -u 2>/dev/null || echo 0)" != "0" ] && have sudo; then SUDO="sudo"; fi

OS="$(uname -s)"
DISTRO="" DISTRO_LIKE=""
if [ -r /etc/os-release ]; then
  # shellcheck disable=SC1091
  . /etc/os-release
  DISTRO="${ID:-}" DISTRO_LIKE="${ID_LIKE:-}"
fi

# ---------- 1. System packages ----------

install_fedora() {
  $SUDO dnf install -y git openssl python3 dnf-plugins-core
  if ! docker compose version >/dev/null 2>&1; then
    # Official Docker repository (signed packages)
    if dnf --version 2>/dev/null | grep -q '^dnf5'; then
      $SUDO dnf config-manager addrepo --overwrite --from-repofile=https://download.docker.com/linux/fedora/docker-ce.repo
    else
      $SUDO dnf config-manager --add-repo https://download.docker.com/linux/fedora/docker-ce.repo
    fi
    if ! $SUDO dnf install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin; then
      warn "Docker's repository has no packages for this Fedora release yet; using Fedora's own."
      $SUDO dnf install -y moby-engine docker-compose docker-buildx
    fi
  fi
}

install_debian() {
  $SUDO apt-get update
  $SUDO apt-get install -y ca-certificates curl git openssl python3 python3-venv python3-pip
  if ! docker compose version >/dev/null 2>&1; then
    local base="ubuntu"; [ "$DISTRO" = "debian" ] && base="debian"
    $SUDO install -m 0755 -d /etc/apt/keyrings
    $SUDO curl -fsSL "https://download.docker.com/linux/$base/gpg" -o /etc/apt/keyrings/docker.asc
    $SUDO chmod a+r /etc/apt/keyrings/docker.asc
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/$base ${VERSION_CODENAME:-$(. /etc/os-release; echo "$VERSION_CODENAME")} stable" \
      | $SUDO tee /etc/apt/sources.list.d/docker.list >/dev/null
    $SUDO apt-get update
    $SUDO apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
  fi
}

install_arch() {
  $SUDO pacman -Sy --needed --noconfirm git openssl python docker docker-compose docker-buildx
}

install_macos() {
  have brew || die "Install Homebrew first: https://brew.sh"
  brew install git openssl python
  if ! have docker; then
    brew install --cask docker
    warn "Open Docker Desktop once (Applications) and wait until it says it is running, then run this script again."
  fi
}

if [ "$INSTALL" = 1 ]; then
  say "Installing system packages"
  case "$OS" in
    Linux)
      case " $DISTRO $DISTRO_LIKE " in
        *" fedora "*|*" rhel "*) install_fedora ;;
        *" debian "*|*" ubuntu "*) install_debian ;;
        *" arch "*) install_arch ;;
        *) warn "Unknown Linux distribution ($DISTRO): install git, openssl, python3 (venv) and Docker with the Compose plugin yourself." ;;
      esac ;;
    Darwin) install_macos ;;
    MINGW*|MSYS*|CYGWIN*) warn "Windows: install Docker Desktop and Python 3.12+ yourself; Git Bash already has git and openssl." ;;
    *) warn "Unknown OS ($OS): install the packages yourself." ;;
  esac
fi

# ---------- 2. Docker running and usable ----------

say "Checking Docker"
have docker || die "docker not found. Install Docker (see https://docs.docker.com/engine/install/) and run again."

if ! docker info >/dev/null 2>&1 && [ "$OS" = "Linux" ] && have systemctl && [ -d /run/systemd/system ]; then
  systemctl is-active --quiet docker || $SUDO systemctl enable --now docker || true
fi

DOCKER="docker"
if ! docker info >/dev/null 2>&1; then
  if [ "$OS" = "Linux" ] && $SUDO docker info >/dev/null 2>&1; then
    if ! id -nG "$USER" | tr ' ' '\n' | grep -qx docker; then
      $SUDO groupadd -f docker
      $SUDO usermod -aG docker "$USER"
      warn "Added $USER to the 'docker' group. Log out and back in to use docker without sudo."
    fi
    DOCKER="$SUDO docker"
  else
    die "Docker is installed but not running. Start it (Docker Desktop, or 'sudo systemctl start docker') and run again."
  fi
fi
$DOCKER compose version >/dev/null 2>&1 || die "The Docker Compose plugin is missing ('docker compose')."
echo "Docker OK: $($DOCKER --version)"

# Two engines (for example Docker Desktop or rootless for the user, plus the system engine for sudo)
# keep separate containers: a stack started on one blocks the ports for the other.
if [ "$DOCKER" = "docker" ] && [ -n "$SUDO" ] && sudo -n docker info >/dev/null 2>&1; then
  if [ "$(docker info -f '{{.ID}}' 2>/dev/null)" != "$(sudo -n docker info -f '{{.ID}}' 2>/dev/null)" ] \
     && [ -n "$(sudo -n docker compose ps -q 2>/dev/null)" ]; then
    die "This project is running on the system Docker engine (sudo docker), but your 'docker' uses another engine. Stop it with 'sudo docker compose down' and run again."
  fi
fi

# ---------- 3. Python venv for client1 and the tools on the host ----------

say "Python virtual environment (.venv)"
PY=""
for candidate in python3.12 python3.13 python3 python; do
  if have "$candidate" && "$candidate" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' 2>/dev/null; then
    PY="$candidate"; break
  fi
done
[ -n "$PY" ] || die "Python 3.10 or newer not found."
[ -d .venv ] || "$PY" -m venv .venv
if [ -x .venv/bin/python ]; then VPY=.venv/bin/python; else VPY=.venv/Scripts/python.exe; fi
"$VPY" -m pip install --quiet --upgrade pip
"$VPY" -m pip install --quiet -r client1/requirements.txt bcrypt pytest
echo "venv OK: $("$VPY" --version)"

# ---------- 4. TLS certificates ----------

say "TLS certificates"
if [ -f certs/ca.crt ] && ! openssl x509 -in certs/ca.crt -noout -ext keyUsage 2>/dev/null | grep -q "Certificate Sign"; then
  warn "Old CA without keyUsage (rejected by Python 3.13+): generating new certificates."
  rm -f certs/*.crt certs/*.key certs/*.srl
fi
if [ -f certs/ca.crt ] && [ -f certs/mosquitto.crt ] && [ -f certs/db.crt ] && [ -f certs/ml.crt ] && [ -f certs/nodered.crt ]; then
  echo "certificates already present"
else
  bash scripts/gen-certs.sh >/dev/null
  echo "certificates generated in certs/"
fi

# ---------- 5. .env ----------

say "Environment file (.env)"
if [ -f .env ]; then
  # Add keys that .env.example gained since .env was created; never change existing values
  added=0
  while IFS= read -r line; do
    case "$line" in ''|'#'*) continue ;; esac
    key="${line%%=*}"
    if ! grep -q "^${key}=" .env; then
      value="${line#*=}"
      if [ -z "$value" ]; then
        [ "$key" = "NODERED_ADMIN_PASSWORD_HASH" ] && die "$key is missing in .env: run '$VPY scripts/gen-env.py --force' (new passwords)."
        value="$(openssl rand -base64 24 | tr -d '/+=\n')"   # a new secret
      fi
      printf '%s=%s\n' "$key" "$value" >> .env
      echo "added $key"
      added=1
    fi
  done < .env.example
  [ "$added" = 1 ] || echo ".env already complete"
else
  "$VPY" scripts/gen-env.py
fi

# ---------- 6. Start ----------

if [ "$RESET" = 1 ]; then
  say "Deleting the database volume (docker compose down -v)"
  $DOCKER compose down -v
fi
if [ "$START" = 1 ]; then
  bash scripts/run.sh start
  exit 0
fi

say "Done"
cat <<EOF
Next step: start the services. It waits until they are ready and prints the addresses to open.
  bash scripts/run.sh
EOF
