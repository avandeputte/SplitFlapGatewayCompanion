"""
Entrypoint: ``python -m app`` — the canonical way to run the companion.

Binds all interfaces by default (COMPANION_HOST=0.0.0.0) so the companion is
reachable on the host's LAN/public IP — not just localhost — which is also the
address it registers with the gateway.

IMPORTANT: launching with ``uvicorn app.main:app`` instead binds 127.0.0.1 by
default (uvicorn's default), which makes the registered LAN URL unreachable. Use
``python -m app``, or pass ``--host 0.0.0.0`` to uvicorn yourself.

Env: GATEWAY_URL (REQUIRED — the app refuses to start without it),
COMPANION_HOST (default 0.0.0.0), COMPANION_PORT (default 8000),
COMPANION_RELOAD (set truthy for auto-reload during development).

As a Home Assistant add-on there are no environment variables at all: Supervisor
writes the user's Configuration tab to ``/data/options.json``. The checks below run
*before* Config exists, so they consult that file themselves — see ``gateway_url``.
"""

import logging
import os

import uvicorn

from .config import addon_option, vestaboard_mirror_port


def gateway_url() -> str:
    """The gateway URL from the environment, falling back to the add-on's option.

    This preflight guard runs before Config is built, so it cannot go through the usual
    ``defaults <- gateway sync <- add-on options <- env`` merge — it has to read the
    add-on's options itself. Without that, a perfectly-configured add-on (the URL typed
    into the Configuration tab, no env var in sight) dies right here with
    "GATEWAY_URL is not set", which is exactly what it did.

    Env first, so a hand-run container still overrides the file.
    """
    return os.environ.get("GATEWAY_URL", "").strip() or addon_option("gateway_url")


def main() -> None:
    if not gateway_url():
        raise SystemExit(
            "FATAL: GATEWAY_URL is not set.\n"
            "The companion talks to your SplitFlapGateway over REST and has no\n"
            "default host -- set GATEWAY_URL and retry, e.g.:\n"
            "    GATEWAY_URL=http://192.168.1.50 python -m app\n"
            "As a Home Assistant add-on, set it in the Configuration tab instead."
        )
    host = os.environ.get("COMPANION_HOST", "0.0.0.0")
    port = int(os.environ.get("COMPANION_PORT", "8000"))
    reload = os.environ.get("COMPANION_RELOAD", "").lower() in ("1", "true", "yes", "on")
    # The add-on's log_level option, for the same reason (this is uvicorn's own level).
    uv_level = (os.environ.get("COMPANION_LOG_LEVEL")
                or addon_option("log_level", "INFO")).strip().lower()
    if uv_level not in ("debug", "info", "warning", "error", "critical"):
        uv_level = "info"

    log = logging.getLogger("companion")

    # A second listener on 7000 lets the Vestaboard Local API be reached where real boards
    # (and the ha-vestaboard HA integration, which hardcodes 7000) expect it. Skipped under
    # --reload: the reloader spawns its own process and dev doesn't need the mirror.
    vb_port = None if reload else vestaboard_mirror_port(port)
    if vb_port is None:
        log.info("binding %s:%d%s", host, port, " (reload)" if reload else "")
        uvicorn.run("app.main:app", host=host, port=port, reload=reload, log_level=uv_level)
        return

    log.info("binding %s:%d and %s:%d (Vestaboard Local API)", host, port, host, vb_port)
    _run_dual(host, port, vb_port, uv_level)


def _run_dual(host: str, port: int, vb_port: int, uv_level: str) -> None:
    """Serve the SAME app on two ports in one process. The primary runs the lifespan
    (startup registers with the gateway and starts the background loops); the mirror runs
    with ``lifespan="off"`` so those run exactly once — it only answers requests against the
    already-initialized app. One combined signal handler stops both together on SIGTERM
    (add-on stop) / SIGINT, since uvicorn's per-server handlers would otherwise clobber each
    other and leave one listener running."""
    import asyncio
    import signal

    from app.main import app as app_obj

    class _Server(uvicorn.Server):
        def install_signal_handlers(self) -> None:  # managed in _serve() for both at once
            pass

    primary = _Server(uvicorn.Config(
        app_obj, host=host, port=port, log_level=uv_level, lifespan="auto"))
    mirror = _Server(uvicorn.Config(
        app_obj, host=host, port=vb_port, log_level=uv_level, lifespan="off"))

    async def _serve() -> None:
        loop = asyncio.get_running_loop()

        def _stop() -> None:
            primary.should_exit = True
            mirror.should_exit = True

        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, _stop)
            except NotImplementedError:   # e.g. Windows
                pass
        await asyncio.gather(primary.serve(), mirror.serve())

    asyncio.run(_serve())


if __name__ == "__main__":
    main()
