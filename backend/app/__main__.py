"""
Entrypoint: ``python -m app`` — the canonical way to run the companion.

Binds all interfaces by default (COMPANION_HOST=0.0.0.0) so the companion is
reachable on the host's LAN/public IP — not just localhost — which is also the
address it registers with the gateway.

IMPORTANT: launching with ``uvicorn app.main:app`` instead binds 127.0.0.1 by
default (uvicorn's default), which makes the registered LAN URL unreachable. Use
``python -m app``, or pass ``--host 0.0.0.0`` to uvicorn yourself.

Env: COMPANION_HOST (default 0.0.0.0), COMPANION_PORT (default 8000),
Env: GATEWAY_URL (REQUIRED — the app refuses to start without it),
COMPANION_HOST (default 0.0.0.0), COMPANION_PORT (default 8000),
COMPANION_RELOAD (set truthy for auto-reload during development).
"""

import logging
import os

import uvicorn


def main() -> None:
    if not os.environ.get("GATEWAY_URL", "").strip():
        raise SystemExit(
            "FATAL: GATEWAY_URL is not set.\n"
            "The companion talks to your SplitFlapGateway over REST and has no\n"
            "default host -- set GATEWAY_URL and retry, e.g.:\n"
            "    GATEWAY_URL=http://192.168.1.50 python -m app"
        )
    host = os.environ.get("COMPANION_HOST", "0.0.0.0")
    port = int(os.environ.get("COMPANION_PORT", "8000"))
    reload = os.environ.get("COMPANION_RELOAD", "").lower() in ("1", "true", "yes", "on")
    logging.getLogger("companion").info(
        "binding %s:%d%s", host, port, " (reload)" if reload else "")
    uvicorn.run("app.main:app", host=host, port=port, reload=reload, log_level="info")


if __name__ == "__main__":
    main()
