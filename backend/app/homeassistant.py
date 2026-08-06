"""
homeassistant.py — Home Assistant MQTT auto-discovery for the companion.

When an MQTT broker is configured (or COMPANION_HA forces it — see the startup
gate in main.py), the companion publishes a small HA device over that broker.
The broker is companion-owned: firmware 3.0 dropped MQTT from the gateway, so
there is no gateway-side HA device anymore. The companion exposes the app- and
playlist-level controls; message-flashing and display readback are covered by
the REST-based custom integration (custom_components/splitflap), not here:

  * select  "App"       — run an installed app (or "Off" to stop); its state
                          shows the running app
  * select  "Playlist"  — run a saved playlist (or "Off"); state shows the
                          running playlist
  * button  "Stop"      — stop whatever is running

A Matrix Gateway adds its on-device kitchen timer and daily alarms, gated on the
wall's capabilities ("timer"/"alarms" feature tokens — caps.can_timer/can_alarms):

  * sensor "Timer ends" (timestamp; HA renders the live countdown), binary
    sensors "Timer" and "Alarm firing", number "Start timer (min)" and button
    "Stop timer / dismiss alarm"
  * per alarm slot 1-4: a switch (enabled), a text "time" (HH:MM) and a text
    "days" ('daily', 'weekdays', 'weekends' or 'mon,tue,…')

Command topics let HA automations start/stop apps, playlists and timers (receive
triggers from HA); the select states let HA see which app/playlist is active.

Commands arrive on the paho thread and are marshaled onto the asyncio loop via
run_coroutine_threadsafe, since the controller's run/stop are coroutines.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
import time
from datetime import datetime, timezone

from . import gateway

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
        self._gw: dict = {}                 # the polled timer/alarm cache (see _poll_gateway)
        self._last_timer_min = 5            # the number entity's idle state

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
    def _timer_caps(self) -> tuple[bool, bool]:
        """(can_timer, can_alarms) from the wall's capabilities — the gateway advertises
        the on-device kitchen timer and alarm slots as the "timer"/"alarms" feature
        tokens (device.Capabilities.can_timer/can_alarms). Each entity group publishes
        only when ITS capability is there."""
        try:
            caps = self.controller._caps()
            return bool(getattr(caps, "can_timer", False)), bool(getattr(caps, "can_alarms", False))
        except Exception:
            return False, False

    def _gateway_url(self) -> str:
        return str(self.config.transport.get("gateway_url") or "").strip()

    def _discovery(self) -> list[tuple[str, str, dict]]:
        # Only app/playlist-level controls. "Flash a message" and "what's on the
        # display" belong to the REST custom integration (custom_components/
        # splitflap), so we don't duplicate those; the select states show the
        # active app/playlist.
        d, av = self._device(), self._avail()
        apps = [a["name"] for a in self.plugins.app_list()]
        # Dict-merge (builtin first, saved wins a name collision) — the SAME shape the route/MCP
        # consumers use, so a legacy saved "All apps" can't show twice or diverge from them.
        pls = list({**self.plugins.builtin_playlists(),
                    **self.settings.get("saved_app_playlists", {})}.keys())
        out = [
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
        # The Matrix Gateway's kitchen timer + four daily alarms, gated per capability.
        # The timer's state is an END TIMESTAMP (empty when idle): HA renders a live
        # countdown from a stable value, so the 20 s state loop never causes drift.
        can_timer, can_alarms = self._timer_caps()
        if can_timer:
            out += [
                ("sensor", "timer_ends", {
                    "name": "Timer ends", "unique_id": f"{self.node}_timer_ends",
                    "state_topic": self._state("timer_ends"), "device_class": "timestamp",
                    "availability_topic": av, "icon": "mdi:timer-outline", "device": d}),
                ("binary_sensor", "timer_active", {
                    "name": "Timer", "unique_id": f"{self.node}_timer_active",
                    "state_topic": self._state("timer_active"), "device_class": "running",
                    "availability_topic": av, "device": d}),
                ("binary_sensor", "alarm_firing", {
                    "name": "Alarm firing", "unique_id": f"{self.node}_alarm_firing",
                    "state_topic": self._state("alarm_firing"), "device_class": "sound",
                    "availability_topic": av, "device": d}),
                ("number", "timer_minutes", {
                    "name": "Start timer (min)", "unique_id": f"{self.node}_timer_minutes",
                    "command_topic": self._cmd("timer_minutes"),
                    "state_topic": self._state("timer_minutes"),
                    "min": 1, "max": 1440, "step": 1, "mode": "box",
                    "availability_topic": av, "icon": "mdi:timer-play-outline", "device": d}),
                ("button", "timer_stop", {
                    "name": "Stop timer / dismiss alarm", "unique_id": f"{self.node}_timer_stop",
                    "command_topic": self._cmd("timer_stop"), "availability_topic": av,
                    "icon": "mdi:timer-off-outline", "device": d}),
            ]
        if can_alarms:
            for i in range(4):
                out += [
                    ("switch", f"alarm{i + 1}", {
                        "name": f"Alarm {i + 1}", "unique_id": f"{self.node}_alarm{i + 1}",
                        "command_topic": self._cmd(f"alarm{i + 1}"),
                        "state_topic": self._state(f"alarm{i + 1}"),
                        "availability_topic": av, "icon": "mdi:alarm", "device": d}),
                    ("text", f"alarm{i + 1}_time", {
                        "name": f"Alarm {i + 1} time", "unique_id": f"{self.node}_alarm{i + 1}_time",
                        "command_topic": self._cmd(f"alarm{i + 1}_time"),
                        "state_topic": self._state(f"alarm{i + 1}_time"),
                        "pattern": "^([01]?\\d|2[0-3]):[0-5]\\d$",
                        "availability_topic": av, "icon": "mdi:clock-outline", "device": d}),
                    ("text", f"alarm{i + 1}_days", {
                        "name": f"Alarm {i + 1} days", "unique_id": f"{self.node}_alarm{i + 1}_days",
                        "command_topic": self._cmd(f"alarm{i + 1}_days"),
                        "state_topic": self._state(f"alarm{i + 1}_days"),
                        "availability_topic": av, "icon": "mdi:calendar-week", "device": d}),
                ]
        return out

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
        cmds = ["app", "playlist", "stop"]
        can_timer, can_alarms = self._timer_caps()
        if can_timer:
            cmds += ["timer_minutes", "timer_stop"]
        if can_alarms:
            cmds += [f"alarm{i}{suf}" for i in range(1, 5) for suf in ("", "_time", "_days")]
        for k in cmds:
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
        gw = self._gw
        if gw.get("polled"):
            t = gw.get("timer") or {}
            if gw.get("can_timer"):
                self._client.publish(self._state("timer_ends"), gw.get("ends_iso", ""), retain=True)
                self._client.publish(self._state("timer_active"),
                                     "ON" if t.get("active") else "OFF", retain=True)
                self._client.publish(self._state("alarm_firing"),
                                     "ON" if t.get("alarmFiring") else "OFF", retain=True)
                mins = -(-int(t.get("remaining") or 0) // 60) if t.get("active") else self._last_timer_min
                self._client.publish(self._state("timer_minutes"), str(max(1, mins)), retain=True)
            for i, s in enumerate((gw.get("alarms") or [])[:4], start=1):
                self._client.publish(self._state(f"alarm{i}"),
                                     "ON" if s.get("enabled") else "OFF", retain=True)
                self._client.publish(self._state(f"alarm{i}_time"), s.get("time", "07:00"), retain=True)
                self._client.publish(self._state(f"alarm{i}_days"),
                                     gateway.mask_to_days(int(s.get("days") or 0x7F)), retain=True)

    def _poll_gateway(self) -> None:
        """Read the wall's timer + alarm state into the cache (blocking — call off the
        loop), each half only when its capability is advertised. The timer's end is
        stored as an ISO timestamp rounded to 5 s, so the 20 s republish shows a
        STABLE value HA can count down from without jitter."""
        can_timer, can_alarms = self._timer_caps()
        url = self._gateway_url()
        if not url or not (can_timer or can_alarms):
            return
        t = gateway.timer_get(url) if can_timer else {}
        alarms = gateway.alarms_get(url) if can_alarms else []
        ends = ""
        if t.get("active"):
            end = time.time() + int(t.get("remaining") or 0)
            end = round(end / 5.0) * 5.0
            ends = datetime.fromtimestamp(end, tz=timezone.utc).isoformat()
        self._gw = {"polled": True, "can_timer": can_timer, "timer": t,
                    "alarms": alarms, "ends_iso": ends}

    async def _state_loop(self) -> None:
        try:
            while True:
                await asyncio.sleep(20)
                await asyncio.to_thread(self._poll_gateway)
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
        try:
            await asyncio.to_thread(self._poll_gateway)   # commands change timer/alarm state
        except Exception as e:
            log.debug("HA gateway poll failed: %s", e)
        self.publish_state()

    def _app_id_by_name(self, name: str) -> str | None:
        for a in self.plugins.app_list():
            if a["name"].lower() == name.lower() or a["id"].lower() == name.lower():
                return a["id"]
        return None

    async def _alarm_edit(self, slot: int, field: str, payload: str) -> None:
        """Read-modify-write ONE alarm slot (the firmware's POST replaces all four)."""
        url = self._gateway_url()
        slots = await asyncio.to_thread(gateway.alarms_get, url)
        while len(slots) < 4:
            slots.append({"time": "07:00", "days": 0x7F, "enabled": False})
        s = slots[slot]
        if field == "enabled":
            s["enabled"] = payload.strip().upper() == "ON"
        elif field == "time":
            import re
            if not re.fullmatch(r"([01]?\d|2[0-3]):[0-5]\d", payload.strip()):
                log.info("HA: bad alarm time %r", payload)
                return
            s["time"] = payload.strip()
        elif field == "days":
            try:
                s["days"] = gateway.days_to_mask(payload)
            except ValueError as e:
                log.info("HA: %s", e)
                return
        await asyncio.to_thread(gateway.alarms_set, url, slots)

    def _timer_command_coro(self, topic: str, payload: str):
        """The Matrix timer/alarm command topics -> a coroutine (or None)."""
        if topic == self._cmd("timer_stop"):
            return asyncio.to_thread(gateway.timer_stop, self._gateway_url())
        if topic == self._cmd("timer_minutes"):
            try:
                mins = max(1, min(1440, int(float(payload))))
            except (TypeError, ValueError):
                return None
            self._last_timer_min = mins
            return asyncio.to_thread(gateway.timer_start, self._gateway_url(), mins * 60)
        for i in range(1, 5):
            if topic == self._cmd(f"alarm{i}"):
                return self._alarm_edit(i - 1, "enabled", payload)
            if topic == self._cmd(f"alarm{i}_time"):
                return self._alarm_edit(i - 1, "time", payload)
            if topic == self._cmd(f"alarm{i}_days"):
                return self._alarm_edit(i - 1, "days", payload)
        return None

    def _command_coro(self, topic: str, payload: str):
        """Map an incoming command to a controller coroutine (or None)."""
        off = payload.lower() in ("off", "stop", "")
        timer = self._timer_command_coro(topic, payload)
        if timer is not None:
            return timer
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
            pl = (self.settings.get("saved_app_playlists", {}).get(payload)
                  or self.plugins.builtin_playlists().get(payload))
            if pl:
                return self.controller.run_playlist(pl.get("entries", []), pl.get("loop", True), payload)
            log.info("HA: unknown playlist %r", payload)
        return None
