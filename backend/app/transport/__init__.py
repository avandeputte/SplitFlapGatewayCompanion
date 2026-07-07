"""Transport package: pick a display transport from config (lazy imports)."""

from __future__ import annotations

from .base import DisplayTransport, SimTransport, frame_for

__all__ = ["DisplayTransport", "SimTransport", "frame_for", "build_transport"]


def build_transport(transport_cfg: dict) -> DisplayTransport:
    """Construct the transport named by ``transport_cfg['type']``.

    Imports for mqtt/rest are deferred so ``sim`` mode needs no extra deps.
    """
    ttype = (transport_cfg.get("type") or "sim").lower()

    if ttype == "sim":
        return SimTransport()

    if ttype == "mqtt":
        from .mqtt import MqttTransport

        m = transport_cfg.get("mqtt", {})
        return MqttTransport(
            broker=m.get("broker", ""),
            port=m.get("port", 1883),
            prefix=m.get("prefix", "splitflap"),
            username=m.get("username", ""),
            password=m.get("password", ""),
        )

    if ttype == "rest":
        from .rest import RestTransport

        return RestTransport(gateway_url=transport_cfg.get("gateway_url", ""))

    raise ValueError(f"unknown transport type: {ttype!r}")
