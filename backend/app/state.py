"""
state.py — live display state, mirrored to the browser for the preview.

Holds the character shown per module (verbatim — the companion never maps text
to a fixed flap set; modules own their char maps and blank anything they lack)
plus the current target string and a little transport status for the UI pill.
"""

from __future__ import annotations

import threading


class DisplayState:
    def __init__(self, module_count: int):
        self._lock = threading.Lock()
        self.module_count = module_count
        # The actual character shown per module, preserved verbatim (accents and
        # any other Windows-1252 glyph included), so the preview mirrors exactly
        # what is sent to the modules.
        self.current_chars: list[str] = [" "] * module_count
        self.current_string: str = " " * module_count
        self.is_homed: bool = False
        # Populated by the active transport for the UI.
        self.transport_type: str = "sim"
        self.transport_connected: bool = False
        self.last_error: str | None = None
        # Which app / playlist (if any) is currently driving the display.
        self.active_app: str | None = None
        self.active_playlist: str | None = None

    def resize(self, module_count: int) -> None:
        with self._lock:
            self.module_count = module_count
            self.current_chars = [" "] * module_count
            self.current_string = " " * module_count
            self.is_homed = False

    def blank(self) -> None:
        """Reset every module to blank — the preview after a physical Home, where
        each module returns to flap 0 (the blank/home flap)."""
        with self._lock:
            self.current_chars = [" "] * self.module_count
            self.current_string = " " * self.module_count

    def set_module(self, grid_index: int, char: str) -> None:
        """Record that a module now shows ``char`` (updates preview state)."""
        with self._lock:
            if 0 <= grid_index < self.module_count:
                self.current_chars[grid_index] = char

    def set_target(self, clean_text: str) -> None:
        with self._lock:
            self.current_string = clean_text
            self.is_homed = True

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "module_count": self.module_count,
                "string": self.current_string,
                # Raw characters shown (accents preserved) — drives the live
                # preview so it matches what's on the modules.
                "chars": list(self.current_chars),
                "is_homed": self.is_homed,
                "active_app": self.active_app,
                "active_playlist": self.active_playlist,
                "transport": {
                    "type": self.transport_type,
                    "connected": self.transport_connected,
                    "last_error": self.last_error,
                },
            }
