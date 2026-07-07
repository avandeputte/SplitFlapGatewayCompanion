"""
scheduler.py — quiet hours, time-of-day schedules, and app triggers.

Faithful port of splitflap-os's _schedule_tick / _check_triggers, adapted to
asyncio background tasks. A schedule can turn the display off or start an app or
a saved playlist during a time window; quiet hours stop the display entirely; a
trigger runs an app's trigger() on an interval and briefly interrupts the
display when it fires.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime

import pytz

log = logging.getLogger("companion.scheduler")

DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


def in_window(start: str, end: str, t: str) -> bool:
    """True if HH:MM ``t`` is within [start, end); supports overnight windows."""
    if start <= end:
        return start <= t < end
    return t >= start or t < end


class Scheduler:
    def __init__(self, controller, plugins):
        self.c = controller
        self.plugins = plugins
        self.settings = plugins.settings
        self._active_schedule_id = None
        self._quiet_active = False
        self._trig_cooldown: dict[str, float] = {}
        self._trig_last_check: dict[str, float] = {}
        self._trig_failures: dict[str, int] = {}
        self._tasks: list[asyncio.Task] = []

    def start(self) -> None:
        self._tasks = [
            asyncio.create_task(self._schedule_loop()),
            asyncio.create_task(self._trigger_loop()),
        ]

    async def stop(self) -> None:
        for t in self._tasks:
            t.cancel()

    def force_reeval(self) -> None:
        self._active_schedule_id = None

    def last_fired(self, trig_id: str):
        return self._trig_cooldown.get(trig_id)

    # -- quiet hours + schedules -------------------------------------------
    def _now(self) -> datetime:
        tz = pytz.timezone(self.settings.get("timezone", "US/Eastern") or "US/Eastern")
        return datetime.now(tz)

    def is_quiet(self) -> bool:
        if not self.settings.get("quiet_hours_enabled", False):
            return False
        now = self._now()
        if DAYS[now.weekday()] not in self.settings.get("quiet_hours_days", []):
            return False
        t = now.strftime("%H:%M")
        return in_window(self.settings.get("quiet_hours_start", "22:00"),
                         self.settings.get("quiet_hours_end", "07:00"), t)

    async def tick(self) -> None:
        quiet = self.is_quiet()
        if quiet and not self._quiet_active:
            self._quiet_active = True
            await self.c.set_quiet(True)
            log.info("quiet hours: display stopped")
            return
        if not quiet and self._quiet_active:
            self._quiet_active = False
            self.c.state.quiet = False
            log.info("quiet hours ended")
        if quiet:
            return

        now = self._now()
        day = DAYS[now.weekday()]
        t = now.strftime("%H:%M")
        matched = None
        for s in self.settings.get("schedules", []):
            if not s.get("enabled", True) or day not in s.get("days", []):
                continue
            if in_window(s.get("start_time", "00:00"), s.get("end_time", "00:00"), t):
                matched = s
                break

        new_id = matched.get("id") if matched else None
        if new_id == self._active_schedule_id:
            return
        self._active_schedule_id = new_id
        if matched is None:
            return  # window ended — leave whatever the user has running

        action = matched.get("action", {})
        atype = action.get("type", "off")
        name = matched.get("name", "")
        if atype == "off":
            await self.c.stop_app()
            log.info("schedule '%s': display off", name)
        elif atype == "app":
            app_id = action.get("value", "")
            if self.plugins.manifest(app_id):
                try:
                    await self.c.run_app(app_id)
                    log.info("schedule '%s': app %s", name, app_id)
                except KeyError:
                    pass
        elif atype == "playlist":
            pl_name = action.get("value", "")
            pl = self.settings.get("saved_app_playlists", {}).get(pl_name)
            if pl:
                await self.c.run_playlist(pl.get("entries", []), pl.get("loop", True), pl_name)
                log.info("schedule '%s': playlist '%s'", name, pl_name)

    async def _schedule_loop(self) -> None:
        try:
            await self.tick()
            while True:
                await asyncio.sleep(60)
                try:
                    await self.tick()
                except Exception as e:
                    log.warning("schedule tick error: %s", e)
        except asyncio.CancelledError:
            pass

    # -- triggers ----------------------------------------------------------
    async def check_triggers(self) -> None:
        if not self.settings.get("triggers_enabled", True) or self._quiet_active:
            return
        now = time.time()
        loop = asyncio.get_running_loop()
        for trig in self.settings.get("triggers", []):
            if not trig.get("enabled", True):
                continue
            tid = trig.get("id", "")
            app_id = trig.get("app", "")
            if not self.plugins.has_trigger(app_id):
                continue
            m = self.plugins.manifest(app_id) or {}
            interval = float(m.get("trigger_interval", 60))
            cooldown = float(trig.get("cooldown", m.get("trigger_cooldown", 300)))
            fails = self._trig_failures.get(tid, 0)
            eff_interval = min(interval * (2 ** fails), 600) if fails else interval
            if now - self._trig_last_check.get(tid, 0) < eff_interval:
                continue
            self._trig_last_check[tid] = now
            if now - self._trig_cooldown.get(tid, 0) < cooldown:
                continue
            try:
                fired = await loop.run_in_executor(
                    None, self.plugins.call_trigger, app_id, trig.get("conditions", {}))
                self._trig_failures[tid] = 0
            except Exception as e:
                self._trig_failures[tid] = fails + 1
                log.warning("trigger %s (%s) error #%d: %s", tid, app_id, fails + 1, e)
                continue
            if fired:
                self._trig_cooldown[tid] = now
                secs = float(trig.get("display_seconds", m.get("trigger_display_seconds", 30)))
                try:
                    pages = await loop.run_in_executor(None, self.plugins.get_pages, app_id)
                except Exception:
                    pages = []
                if pages:
                    text = pages[0] if isinstance(pages[0], str) else str(pages[0].get("text", ""))
                    log.info("trigger fired: %s (%s)", trig.get("name", tid), app_id)
                    await self.c.fire_interrupt(text, secs)

    async def _trigger_loop(self) -> None:
        try:
            while True:
                await asyncio.sleep(10)
                try:
                    await self.check_triggers()
                except Exception as e:
                    log.warning("trigger loop error: %s", e)
        except asyncio.CancelledError:
            pass
