"""
transport/base.py — the display transport interface + the frame format.

Every transport emits the split-flap module's RS-485 wire frame,
``m<ID:02d>-<CHAR>\n`` (e.g. ``m05-A\n``) — the protocol the modules themselves
speak. It originates with the split-flap hardware/firmware (not with any one
driver); the SplitFlapGateway and splitflap-os are two projects that implement
it. The gateway forwards these frames verbatim to the RS-485 bus; the companion
posts them to its ``/api/rs485/send`` + ``/api/rs485/batch`` endpoints.
"""

from __future__ import annotations

import abc
import logging

log = logging.getLogger("companion.transport")


def frame_for(module_id: int, char: str) -> str:
    """Build the RS-485 ASCII frame for a single module (with trailing NL)."""
    return f"m{module_id:02d}-{char}\n"


class DisplayTransport(abc.ABC):
    """Serial-compatible-ish facade for pushing frames to the gateway."""

    type_name: str = "base"

    async def connect(self) -> None:  # pragma: no cover - trivial default
        return None

    async def close(self) -> None:  # pragma: no cover - trivial default
        return None

    @property
    def connected(self) -> bool:
        return True

    @property
    def last_error(self) -> str | None:
        return None

    @abc.abstractmethod
    async def send_frame(self, module_id: int, char: str) -> None:
        """Emit one frame for ``module_id`` showing ``char``."""
        raise NotImplementedError


class SimTransport(DisplayTransport):
    """No hardware: just logs frames. Lets the whole app run with no gateway."""

    type_name = "sim"

    async def send_frame(self, module_id: int, char: str) -> None:
        log.info("SIM  %s", frame_for(module_id, char).rstrip("\n"))
