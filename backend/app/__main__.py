"""
Entrypoint: ``python -m app``.

Binds all interfaces by default (COMPANION_HOST=0.0.0.0) so the companion is
reachable on the host's LAN/public IP — not just localhost — which is also the
address it registers with the gateway. Override host/port with COMPANION_HOST /
COMPANION_PORT.
"""

import os

import uvicorn


def main() -> None:
    host = os.environ.get("COMPANION_HOST", "0.0.0.0")
    port = int(os.environ.get("COMPANION_PORT", "8000"))
    uvicorn.run("app.main:app", host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
