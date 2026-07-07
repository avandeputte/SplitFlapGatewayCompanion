"""
state.py — live display state, mirrored to the browser for the preview.

Holds the last-known flap index per module (so the preview can animate and so
the ``sync`` style can compute stagger distances) plus the current target
string and a little transport status for the UI status pill.
"""

from __future__ import annotations

import threading

from . import renderer


class DisplayState:
    def __init__(self, module_count: int):
        self._lock = threading.Lock()
        self.module_count = module_count
        # -1 means "unknown / not yet homed" for that module.
        self.current_indices: list[int] = [-1] * module_count
        self.current_string: str = " " * module_count
        self.is_homed: bool = False
        # Populated by the active transport for the UI.
        self.transport_type: str = "sim"
        self.transport_connected: bool = False
        self.last_error: str | None = None

    def resize(self, module_count: int) -> None:
        with self._lock:
            self.module_count = module_count
            self.current_indices = [-1] * module_count
            self.current_string = " " * module_count
            self.is_homed = False

    def set_module(self, grid_index: int, char: str) -> None:
        """Record that a module now shows ``char`` (updates preview state)."""
        with self._lock:
            if 0 <= grid_index < self.module_count:
                self.current_indices[grid_index] = renderer.char_to_index(char)

    def set_target(self, clean_text: str) -> None:
        with self._lock:
            self.current_string = clean_text
            self.is_homed = True

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "module_count": self.module_count,
                "indices": list(self.current_indices),
                "string": self.current_string,
                "chars": [
                    renderer.FLAP_CHARS[i] if 0 <= i < len(renderer.FLAP_CHARS) else " "
                    for i in self.current_indices
                ],
                "is_homed": self.is_homed,
                "flap_chars": renderer.FLAP_CHARS,
                "transport": {
                    "type": self.transport_type,
                    "connected": self.transport_connected,
                    "last_error": self.last_error,
                },
            }
