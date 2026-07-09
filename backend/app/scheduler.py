"""
scheduler.py — app triggers.

Runs each enabled app trigger's ``trigger(settings, conditions)`` on its interval
(with cooldown + failure backoff) and briefly interrupts the display when one
fires. Time-of-day schedules and quiet hours now live on the gateway (v3.0),
which owns quiet-time, so the companion no longer schedules anything itself.
"""

from __future__ import annotations

import asyncio
import logging
import time

log = logging.getLogger("companion.scheduler")


class Scheduler:
    def __init__(self, controller, plugins):
        self.c = controller
        self.plugins = plugins
        self.settings = plugins.settings
        self._cooldown: dict[str, float] = {}
        self._last_check: dict[str, float] = {}
        self._failures: dict[str, int] = {}
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        self._task = asyncio.create_task(self._trigger_loop())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    def last_fired(self, trig_id: str):
        return self._cooldown.get(trig_id)

    async def check_triggers(self) -> None:
        if not self.settings.get("triggers_enabled", True):
            return
        now = time.time()
        loop = asyncio.get_running_loop()
        triggers = self.settings.get("triggers", [])
        # Prune bookkeeping for triggers that no longer exist (renamed/deleted),
        # so these dicts can't grow without bound over a long uptime.
        live_ids = {t.get("id", "") for t in triggers}
        for d in (self._cooldown, self._last_check, self._failures):
            for stale in [k for k in d if k not in live_ids]:
                del d[stale]
        for trig in triggers:
            if not trig.get("enabled", True):
                continue
            tid = trig.get("id", "")
            app_id = trig.get("app", "")
            if not self.plugins.has_trigger(app_id):
                continue
            m = self.plugins.manifest(app_id) or {}
            interval = float(m.get("trigger_interval", 60))
            cooldown = float(trig.get("cooldown", m.get("trigger_cooldown", 300)))
            fails = self._failures.get(tid, 0)
            eff_interval = min(interval * (2 ** fails), 600) if fails else interval
            if now - self._last_check.get(tid, 0) < eff_interval:
                continue
            self._last_check[tid] = now
            if now - self._cooldown.get(tid, 0) < cooldown:
                continue
            try:
                fired = await loop.run_in_executor(
                    None, self.plugins.call_trigger, app_id, trig.get("conditions", {}))
                self._failures[tid] = 0
            except Exception as e:
                self._failures[tid] = fails + 1
                log.warning("trigger %s (%s) error #%d: %s", tid, app_id, fails + 1, e)
                continue
            if fired:
                self._cooldown[tid] = now
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
