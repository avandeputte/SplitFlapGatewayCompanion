"""Transport package: the companion always drives the gateway over REST."""

from __future__ import annotations

from .base import DisplayTransport, SimTransport, frame_for

__all__ = ["DisplayTransport", "SimTransport", "frame_for", "build_transport"]


def build_transport(transport_cfg: dict) -> DisplayTransport:
    """Construct the display transport.

    The companion ALWAYS drives the gateway over REST — a whole page in one
    ``/api/rs485/batch`` request (Gateway 3.0+). There is no transport selector
    and no way to choose another; MQTT is used solely by the separate Home
    Assistant integration (see ``homeassistant.py``), never for the display.

    Raises ``ValueError`` only if no ``gateway_url`` is configured — the caller
    (:meth:`DisplayController._open_transport`) turns that into a sim no-op so the
    app still serves the preview with the reason shown in the status pill.
    """
    from .rest import RestTransport

    return RestTransport(gateway_url=transport_cfg.get("gateway_url", ""))
