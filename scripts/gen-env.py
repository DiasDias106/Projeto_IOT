"""Cria o .env a partir do .env.example, com passwords aleatórias.

Uso:  python scripts/gen-env.py            (não substitui um .env existente)
      python scripts/gen-env.py --force    (substitui)

O hash bcrypt da password do editor Node-RED é gerado com o bcryptjs da imagem do Node-RED
(precisa do Docker a correr). A password em texto é mostrada uma vez no fim: guardá-la.
"""
from __future__ import annotations

import argparse
import secrets
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
NODERED_IMAGE = "nodered/node-red:4.0.9"
RANDOM_KEYS_SUFFIX = ("_PASSWORD",)
RANDOM_KEYS = {"NODERED_CREDENTIAL_SECRET"}


def bcrypt_hash(password: str) -> str:
    """bcrypt hash for Node-RED. Uses the Python bcrypt package (installed by scripts/setup.sh);
    without it, falls back to bcryptjs inside the Node-RED image (needs Docker)."""
    try:
        import bcrypt
        return bcrypt.hashpw(password.encode(), bcrypt.gensalt(rounds=8)).decode()
    except ImportError:
        pass
    out = subprocess.run(
        ["docker", "run", "--rm", "--entrypoint", "node", NODERED_IMAGE,
         "-e", "console.log(require('bcryptjs').hashSync(process.argv[1], 8))", password],
        capture_output=True, text=True,
    )
    if out.returncode != 0:
        sys.exit("Could not hash the Node-RED password with Docker:\n" + out.stderr.strip()
                 + "\nRun scripts/setup.sh first (it installs bcrypt), or fix Docker.")
    return out.stdout.strip()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true", help="substituir o .env existente")
    args = ap.parse_args()

    target = ROOT / ".env"
    if target.exists() and not args.force:
        print(".env já existe (usar --force para substituir)")
        return 1

    admin_password = secrets.token_urlsafe(12)
    lines = []
    for line in (ROOT / ".env.example").read_text(encoding="utf-8").splitlines():
        key, sep, value = line.partition("=")
        if sep and not line.lstrip().startswith("#") and value == "":
            if key == "NODERED_ADMIN_PASSWORD_HASH":
                value = f"'{bcrypt_hash(admin_password)}'"   # plicas: o hash tem '$'
            elif key.endswith(RANDOM_KEYS_SUFFIX) or key in RANDOM_KEYS:
                value = secrets.token_urlsafe(24)
        lines.append(f"{key}{sep}{value}" if sep else line)

    target.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    print(f".env criado em {target}")
    print(f"Password do editor Node-RED (utilizador admin): {admin_password}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
