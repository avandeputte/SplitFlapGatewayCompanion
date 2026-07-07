"""
transport/mqtt.py — drive the gateway over MQTT.

Publishes raw RS-485 frames to ``<prefix>/send`` (forwarded verbatim to the bus
by the gateway) and subscribes to ``<prefix>/rx`` to drain frames the modules
echo back. This mirrors splitflap-os's proven gateway transport, so animations
stay smooth (publishes are cheap and non-blocking).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
import time

from .base import DisplayTransport, frame_for

log = logging.getLogger("companion.transport.mqtt")


class MqttTransport(DisplayTransport):
    type_name = "mqtt"

    def __init__(
        self,
        broker: str,
        port: int = 1883,
        prefix: str = "splitflap",
        username: str = "",
        password: str = "",
        connect_timeout: float = 8.0,
    ):
        if not broker:
            raise ValueError("MQTT transport requires a broker host")
        self.broker = broker
        self.port = int(port)
        self.prefix = (prefix or "splitflap").strip().rstrip("/")
        self.send_topic = f"{self.prefix}/send"
        self.rx_topic = f"{self.prefix}/rx"
        self.username = username or ""
        self.password = password or ""
        self.connect_timeout = connect_timeout

        self._client = None
        self._connected = threading.Event()
        self._closed = False
        self._last_error: str | None = None
        # Most-recent frames echoed by modules (small ring for diagnostics).
        self._rx: list[str] = []

    # -- lifecycle ----------------------------------------------------------
    async def connect(self) -> None:
        await asyncio.get_running_loop().run_in_executor(None, self._connect_blocking)

    def _connect_blocking(self) -> None:
        try:
            import paho.mqtt.client as mqtt
        except ImportError as e:  # pragma: no cover
            self._last_error = "paho-mqtt not installed"
            raise RuntimeError(self._last_error) from e

        client_id = f"splitflap-companion-{os.getpid()}-{int(time.time())}"
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=client_id)
        if self.username:
            client.username_pw_set(self.username, self.password)
        client.on_connect = self._on_connect
        client.on_disconnect = self._on_disconnect
        client.on_message = self._on_message
        # Set early so close()/send see the client even if the initial connect
        # fails; paho's loop keeps retrying in the background.
        self._client = client

        log.info("MQTT connecting to %s:%s prefix=%s", self.broker, self.port, self.prefix)
        try:
            client.connect(self.broker, self.port, keepalive=30)
            client.loop_start()
        except Exception as e:
            # Do not raise: a bad host/broker leaves us offline but recoverable,
            # and the UI shows the reason rather than masquerading as sim.
            self._last_error = f"could not reach broker {self.broker}:{self.port}: {e}"
            log.warning("MQTT %s", self._last_error)
            return
        if not self._connected.wait(timeout=self.connect_timeout):
            self._last_error = f"timed out connecting to {self.broker}:{self.port}"
            log.warning("MQTT %s", self._last_error)
            return
        self._last_error = None

    def _on_connect(self, client, userdata, flags, reason_code, properties=None):
        ok = getattr(reason_code, "value", reason_code) == 0
        if ok:
            client.subscribe(self.rx_topic, qos=0)
            self._connected.set()
            log.info("MQTT connected; subscribed to %s", self.rx_topic)
        else:
            self._last_error = f"connect failed: {reason_code}"
            log.error("MQTT %s", self._last_error)

    def _on_disconnect(self, client, userdata, *args):
        self._connected.clear()
        if not self._closed:
            log.warning("MQTT disconnected; will auto-reconnect")

    def _on_message(self, client, userdata, msg):
        if msg.topic != self.rx_topic:
            return
        frame = self._extract_frame(msg.payload)
        if frame:
            self._rx.append(frame)
            del self._rx[:-64]  # keep last 64

    @staticmethod
    def _extract_frame(payload: bytes) -> str | None:
        try:
            text = payload.decode("utf-8", errors="ignore").strip()
        except Exception:
            return None
        if not text:
            return None
        if text[0] == "{":
            try:
                return str(json.loads(text).get("command") or "") or None
            except Exception:
                return None
        return text

    def _teardown(self) -> None:
        if self._client is not None:
            try:
                self._client.loop_stop()
            except Exception:
                pass
            try:
                self._client.disconnect()
            except Exception:
                pass

    async def close(self) -> None:
        self._closed = True
        self._teardown()

    # -- io -----------------------------------------------------------------
    @property
    def connected(self) -> bool:
        return self._connected.is_set()

    @property
    def last_error(self) -> str | None:
        return self._last_error

    async def send_frame(self, module_id: int, char: str) -> None:
        if self._client is None:
            raise RuntimeError("MQTT transport not connected")
        payload = frame_for(module_id, char).encode()
        if not self._connected.is_set():
            log.warning("MQTT not connected; frame may be dropped: %r", payload)
        info = self._client.publish(self.send_topic, payload=payload, qos=0)
        rc = getattr(info, "rc", 0)
        if rc != 0:
            self._last_error = f"publish failed rc={rc}"
            log.warning("MQTT %s: %r", self._last_error, payload)
