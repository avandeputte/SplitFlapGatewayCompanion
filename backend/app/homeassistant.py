"""
homeassistant.py — Home Assistant MQTT auto-discovery for the companion.

When the gateway has Home Assistant enabled (or COMPANION_HA forces it), the
companion publishes a small HA device over the same MQTT broker the gateway uses.
It exposes only what is *unique to the companion* — apps and playlists — since
the gateway's own HA device already covers flashing a message and reporting the
display content, and we don't duplicate those:

  * select  "App"       — run an installed app (or "Off" to stop); its state
                          shows the running app
  * select  "Playlist"  — run a saved playlist (or "Off"); state shows the
                          running playlist
  * button  "Stop"      — stop whatever is running

Command topics let HA automations start/stop apps and playlists (receive
triggers from HA); the select states let HA see which app/playlist is active.

Commands arrive on the paho thread and are marshalled onto the asyncio loop via
run_coroutine_threadsafe, since the controller's run/stop are coroutines.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
import time

log = logging.getLogger("companion.ha")


# The display whose HA entities keep the historic, unsuffixed ids. Suffixing the
# default would orphan every existing entity and silently break any automation
# pointing at select.splitflap_companion_app.
DEFAULT_DISPLAY_ID = "default"


class HomeAssistant:
    def __init__(self, config, plugins, controller, display_id: str = "", display_name: str = ""):
        self.config = config
        self.plugins = plugins
        self.controller = controller
        self.settings = plugins.settings
        ha = config.effective.get("ha", {})
        node = ha.get("node_id", "splitflap-companion")
        tp = ha.get("topic_prefix", "splitflap-companion")
        # One HA device per DISPLAY. The node id and topic prefix come from config, which
        # is the same for every display, so two walls would otherwise publish to the same
        # topics under the same device identifier and fight over it — the second wall's
        # discovery would overwrite the first's, and its state would clobber it.
        #
        # The DEFAULT display keeps the unsuffixed ids it has always had, so an existing
        # Home Assistant install keeps its entities (a suffix here would orphan them and
        # silently break every automation pointing at select.splitflap_companion_app).
        suffix = f"_{display_id}" if display_id and display_id != DEFAULT_DISPLAY_ID else ""
        self.display_id = display_id or DEFAULT_DISPLAY_ID
        self.display_name = display_name or "SplitFlap"
        self.node = f"{node}{suffix}"
        self.tp = f"{tp}{suffix}"
        self.dp = ha.get("discovery_prefix", "homeassistant")
        self._client = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._task: asyncio.Task | None = None
        self._connected = threading.Event()
        self.active = False

    # -- topics -------------------------------------------------------------
    def _avail(self) -> str:
        return f"{self.tp}/availability"

    def _state(self, k: str) -> str:
        return f"{self.tp}/state/{k}"

    def _cmd(self, k: str) -> str:
        return f"{self.tp}/cmd/{k}"

    def _disc_topic(self, comp: str, obj: str) -> str:
        return f"{self.dp}/{comp}/{self.node}/{obj}/config"

    def _device(self) -> dict:
        # One device per wall, named after it, so a Home Assistant user with two displays
        # sees "SplitFlap Companion (Kitchen)" and "(Office)" rather than one device whose
        # controls drive whichever wall registered last.
        name = "SplitFlap Companion"
        if self.display_id != DEFAULT_DISPLAY_ID:
            name = f"{name} ({self.display_name})"
        return {"identifiers": [self.node], "name": name,
                "manufacturer": "SplitFlap", "model": "Gateway Companion"}

    # -- discovery + state --------------------------------------------------
    def _discovery(self) -> list[tuple[str, str, dict]]:
        # Only companion-unique controls. The gateway's own HA device already
        # covers "flash a message" and "what's on the display", so we don't
        # duplicate those; the select states show the active app/playlist.
        d, av = self._device(), self._avail()
        apps = [a["name"] for a in self.plugins.app_list()]
        pls = list(self.settings.get("saved_app_playlists", {}).keys())
        return [
            ("select", "app", {
                "name": "App", "unique_id": f"{self.node}_app",
                "command_topic": self._cmd("app"), "state_topic": self._state("app"),
                "options": ["Off"] + apps, "availability_topic": av,
                "icon": "mdi:apps", "device": d}),
            ("select", "playlist", {
                "name": "Playlist", "unique_id": f"{self.node}_playlist",
                "command_topic": self._cmd("playlist"), "state_topic": self._state("playlist"),
                "options": ["Off"] + pls, "availability_topic": av,
                "icon": "mdi:playlist-play", "device": d}),
            ("button", "stop", {
                "name": "Stop", "unique_id": f"{self.node}_stop",
                "command_topic": self._cmd("stop"), "availability_topic": av,
                "icon": "mdi:stop", "device": d}),
        ]

    def _app_name(self, app_id: str) -> str:
        m = self.plugins.manifest(app_id)
        return m["name"] if m and m.get("name") else app_id

    # -- lifecycle ----------------------------------------------------------
    async def start(self) -> bool:
        self._loop = asyncio.get_running_loop()
        broker = self.config.transport.get("mqtt", {}).get("broker")
        if not broker:
            log.warning("HA enabled but no MQTT broker configured — skipping")
            return False
        ok = await self._loop.run_in_executor(None, self._connect_blocking)
        if ok:
            self.active = True
            self._task = asyncio.create_task(self._state_loop())
        return ok

    def _connect_blocking(self) -> bool:
        try:
            import paho.mqtt.client as mqtt
        except ImportError:
            log.warning("paho-mqtt not installed — HA disabled")
            return False
        m = self.config.transport.get("mqtt", {})
        cid = f"splitflap-companion-ha-{os.getpid()}-{int(time.time())}"
        c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=cid)
        if m.get("username"):
            c.username_pw_set(m["username"], m.get("password") or "")
        c.will_set(self._avail(), "offline", retain=True)
        c.on_connect = self._on_connect
        c.on_message = self._on_message
        try:
            c.connect(m.get("broker"), int(m.get("port", 1883)), keepalive=30)
        except Exception as e:
            log.warning("HA MQTT connect failed: %s", e)
            return False
        c.loop_start()
        self._client = c
        if not self._connected.wait(timeout=8):
            log.warning("HA MQTT connect timed out")
            return False
        return True

    def _on_connect(self, client, userdata, flags, reason_code, properties=None):
        if getattr(reason_code, "value", reason_code) != 0:
            log.warning("HA MQTT connect rc=%s", reason_code)
            return
        self._connected.set()
        for k in ("app", "playlist", "stop"):
            client.subscribe(self._cmd(k), qos=0)
        client.publish(self._avail(), "online", retain=True)
        self.publish_discovery()
        self.publish_state()
        log.info("HA integration online (node=%s, prefix=%s)", self.node, self.dp)

    def publish_discovery(self) -> None:
        if not self._client:
            return
        for comp, obj, cfg in self._discovery():
            self._client.publish(self._disc_topic(comp, obj), json.dumps(cfg), retain=True)

    def publish_state(self) -> None:
        if not self._client:
            return
        c = self.controller
        self._client.publish(self._state("app"),
                             self._app_name(c.active_app) if c.active_app else "Off", retain=True)
        self._client.publish(self._state("playlist"), c.active_playlist or "Off", retain=True)

    async def _state_loop(self) -> None:
        try:
            while True:
                await asyncio.sleep(20)
                self.publish_state()
        except asyncio.CancelledError:
            pass

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        if self._client:
            try:
                self._client.publish(self._avail(), "offline", retain=True)
            except Exception:
                pass
            try:
                self._client.loop_stop()
                self._client.disconnect()
            except Exception:
                pass
        self.active = False

    def refresh_discovery(self) -> None:
        """Re-publish discovery when the app/playlist option lists change."""
        if self.active:
            self.publish_discovery()

    # -- command handling (paho thread) ------------------------------------
    def _on_message(self, client, userdata, msg):
        try:
            payload = msg.payload.decode("utf-8", "ignore").strip()
        except Exception:
            return
        coro = self._command_coro(msg.topic, payload)
        if coro is not None and self._loop is not None:
            asyncio.run_coroutine_threadsafe(self._run_then_publish(coro), self._loop)

    async def _run_then_publish(self, coro) -> None:
        try:
            await coro
        except Exception as e:
            log.warning("HA command error: %s", e)
        self.publish_state()

    def _app_id_by_name(self, name: str) -> str | None:
        for a in self.plugins.app_list():
            if a["name"].lower() == name.lower() or a["id"].lower() == name.lower():
                return a["id"]
        return None

    def _command_coro(self, topic: str, payload: str):
        """Map an incoming command to a controller coroutine (or None)."""
        off = payload.lower() in ("off", "stop", "")
        if topic == self._cmd("stop"):
            return self.controller.stop_app()
        if topic == self._cmd("app"):
            if off:
                return self.controller.stop_app()
            aid = self._app_id_by_name(payload)
            if aid:
                return self.controller.run_app(aid)
            log.info("HA: unknown app %r", payload)
        elif topic == self._cmd("playlist"):
            if off:
                return self.controller.stop_app()
            pl = self.settings.get("saved_app_playlists", {}).get(payload)
            if pl:
                return self.controller.run_playlist(pl.get("entries", []), pl.get("loop", True), payload)
            log.info("HA: unknown playlist %r", payload)
        return None
