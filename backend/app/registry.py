"""The display registry — which walls exist, and which one is *the* one.

Phase 1 of multi-display support (docs/MULTI_DISPLAY_PLAN.md). Phase 0 made a
Display an object; this gives the set of them an identity and somewhere to live:

    data/displays.json              the registry (below)
    data/displays/<id>/app_settings.json    one settings store per display, ENTIRELY its own
    data/app_settings.json          the pre-migration file, left untouched as a backup

Three decisions are worth spelling out, because all three are easy to get quietly wrong:

**Every setting is per display — credentials included.** Not a preference: the gateway is
the *backup* for its wall's settings (main.setup_settings_sync mirrors the whole doc onto
it, and a rebuilt host restores from it). A companion-local file holding values shared
across displays would have no gateway to live on, so it could never be backed up or
restored — an invisible hole in the recovery story. The cost is entering an API key once
per wall; the alternative is a wall whose settings cannot be recovered from its own box.
(Adding a display copies the global settings from an existing one as a convenience, but it
is a COPY: from then on they are that display's, and they ride to that display's gateway.)

**The default is stored, not inferred.** `default_display` is a field in this file,
set by the user. It is what the display-less surfaces resolve to — the bare
`/api/...` routes, `/local-api/message` (a Vestaboard client sends no display id), an
MCP call with no `display` argument, an existing HACS entry. Guessing it from
"whichever wall is currently running something" would mean a playlist starting on the
office wall silently re-pointed everyone's scripts at it.

**Migration is one-way, so it does not destroy anything.** The existing
`app_settings.json` is COPIED into display `default`, not moved. A companion rolled
back to 1.x finds its old file exactly where it left it.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import threading
from dataclasses import asdict, dataclass, field
from pathlib import Path

log = logging.getLogger("companion.registry")

# Bumped when the on-disk shape changes in a way that needs migrating.
REGISTRY_VERSION = 1

DEFAULT_ID = "default"
DEFAULT_NAME = "SplitFlap"


def slugify(name: str, fallback: str = "display") -> str:
    """A stable, URL-safe display id from a human name ("Kitchen wall" -> kitchen-wall)."""
    import re
    s = re.sub(r"[^a-z0-9]+", "-", str(name or "").strip().lower()).strip("-")
    return s or fallback


@dataclass
class DisplayRecord:
    """One wall, as persisted. The runtime object that goes with it is display.Display."""

    id: str
    name: str = DEFAULT_NAME
    gateway_url: str = ""
    enabled: bool = True
    order: int = 0

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, doc: dict, *, order: int = 0) -> "DisplayRecord":
        rid = slugify(doc.get("id") or "", fallback="")
        if not rid:
            raise ValueError("display record has no id")
        return cls(
            id=rid,
            name=str(doc.get("name") or DEFAULT_NAME),
            gateway_url=str(doc.get("gateway_url") or "").strip(),
            enabled=bool(doc.get("enabled", True)),
            order=int(doc.get("order", order)),
        )


class DisplayRegistry:
    """Reads and writes data/displays.json, and owns the first-boot migration."""

    def __init__(self, data_dir: Path):
        self.data_dir = Path(data_dir)
        self.path = self.data_dir / "displays.json"
        self._lock = threading.RLock()
        self._records: list[DisplayRecord] = []
        self._default_id: str = DEFAULT_ID

    # -- disk ------------------------------------------------------------------
    def load(self) -> bool:
        """Read the registry. Returns False if there is nothing to read yet."""
        if not self.path.exists():
            return False
        try:
            doc = json.loads(self.path.read_text("utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            # A corrupt registry must not take the companion down with it — it would
            # be unrecoverable without shell access, which an add-on user hasn't got.
            log.error("could not read %s (%s); falling back to a single display", self.path, e)
            return False

        records = []
        for i, raw in enumerate(doc.get("displays") or []):
            try:
                records.append(DisplayRecord.from_dict(raw, order=i))
            except (ValueError, TypeError) as e:
                log.warning("skipping malformed display record %r: %s", raw, e)
        if not records:
            return False

        with self._lock:
            self._records = sorted(records, key=lambda r: r.order)
            wanted = str(doc.get("default_display") or "")
            self._default_id = wanted if any(r.id == wanted for r in self._records) \
                else self._records[0].id
        return True

    def save(self) -> None:
        """Atomic write: a crash mid-save must not leave a registry that lists no walls."""
        with self._lock:
            doc = {
                "_about": "Which displays this companion drives. Prefer editing in the app UI.",
                "version": REGISTRY_VERSION,
                "default_display": self._default_id,
                "displays": [r.to_dict() for r in sorted(self._records, key=lambda r: r.order)],
            }
        self.data_dir.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + ".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(doc, f, indent=2, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, self.path)

    # -- membership ------------------------------------------------------------
    def all(self) -> list[DisplayRecord]:
        with self._lock:
            return sorted(self._records, key=lambda r: r.order)

    def enabled(self) -> list[DisplayRecord]:
        return [r for r in self.all() if r.enabled]

    def get(self, display_id: str) -> DisplayRecord | None:
        with self._lock:
            return next((r for r in self._records if r.id == display_id), None)

    def ids(self) -> list[str]:
        return [r.id for r in self.all()]

    def add(self, *, name: str, gateway_url: str, display_id: str = "") -> DisplayRecord:
        with self._lock:
            base = slugify(display_id or name, fallback="display")
            rid, n = base, 2
            while any(r.id == rid for r in self._records):    # ids must be unique
                rid, n = f"{base}-{n}", n + 1
            rec = DisplayRecord(
                id=rid,
                name=str(name or DEFAULT_NAME),
                gateway_url=str(gateway_url or "").strip(),
                order=max((r.order for r in self._records), default=-1) + 1,
            )
            self._records.append(rec)
        self.save()
        log.info("registered display %r (%s)", rec.id, rec.gateway_url or "no gateway")
        return rec

    def update(self, display_id: str, **fields) -> DisplayRecord:
        with self._lock:
            rec = self.get(display_id)
            if rec is None:
                raise KeyError(display_id)
            for k in ("name", "gateway_url", "enabled", "order"):
                if k in fields and fields[k] is not None:
                    setattr(rec, k, fields[k])
            rec.gateway_url = str(rec.gateway_url or "").strip()
        self.save()
        return rec

    def remove(self, display_id: str) -> DisplayRecord:
        """Drop a display. Its settings directory is left on disk: removing a wall
        from the UI should not silently delete the playlists and triggers you built
        for it, and re-adding it with the same id gets them back."""
        with self._lock:
            rec = self.get(display_id)
            if rec is None:
                raise KeyError(display_id)
            if len(self._records) == 1:
                raise ValueError("cannot remove the only display")
            self._records.remove(rec)
            if self._default_id == display_id:      # never leave the default dangling
                self._default_id = self._records[0].id
                log.info("default display was removed; it is now %r", self._default_id)
        self.save()
        return rec

    # -- the default is a stored CHOICE ----------------------------------------
    @property
    def default_id(self) -> str:
        with self._lock:
            return self._default_id

    def set_default(self, display_id: str) -> None:
        with self._lock:
            if not any(r.id == display_id for r in self._records):
                raise KeyError(display_id)
            self._default_id = display_id
        self.save()
        log.info("default display is now %r", display_id)

    def adopt_env_gateways(self, urls: list[str]) -> list["DisplayRecord"]:
        """Follow a comma-delimited GATEWAY_URL: `http://kitchen,http://office`.

        The FIRST entry owns display `default` (see adopt_env_gateway). Every later entry
        that no display already points at becomes a new display, so a Home Assistant user
        can configure two walls from the single `gateway_url` option they already have,
        without touching the registry by hand.

        Entries only ever ADD. A display is never removed because it stopped appearing in
        the list: someone who adds a wall in the UI and later edits the env for an
        unrelated reason must not silently lose it, along with its playlists and triggers.
        """
        added = []
        for url in [u.strip() for u in urls if u and u.strip()][1:]:
            if any(r.gateway_url == url for r in self.all()):
                continue
            n = len(self.all()) + 1
            added.append(self.add(name=f"{DEFAULT_NAME} {n}", gateway_url=url,
                                  display_id=f"display-{n}"))
            log.info("GATEWAY_URL listed a gateway we had no display for: %s", url)
        return added

    def adopt_env_gateway(self, url: str) -> bool:
        """Keep display `default` pointed at whatever GATEWAY_URL / the add-on option says.

        The add-on's Configuration tab is where a Home Assistant user has always set the
        gateway, and it must keep working: if the registry silently outranked it, someone
        correcting a typo'd IP there would watch nothing happen and have no way to tell
        why. So the env owns display `default` — the wall it was configured for — while
        displays 2..n are the registry's (and the UI's) to manage.
        """
        url = str(url or "").strip()
        rec = self.get(DEFAULT_ID)
        if not url or rec is None or rec.gateway_url == url:
            return False
        log.info("display %r follows the configured gateway_url: %s -> %s",
                 DEFAULT_ID, rec.gateway_url or "unset", url)
        self.update(DEFAULT_ID, gateway_url=url)
        return True

    # -- first boot ------------------------------------------------------------
    def ensure(self, *, gateway_url: str = "", name: str = DEFAULT_NAME) -> "DisplayRegistry":
        """Load the registry, migrating a pre-1.9 install into it if there isn't one.

        `gateway_url` is what the companion was already configured with (the
        GATEWAY_URL env var / the add-on's gateway_url option). It seeds display
        `default`, so an existing install upgrades with zero configuration — and the
        add-on's single required option keeps meaning what it meant.
        """
        if self.load():
            return self

        migrated = migrate_settings(self.data_dir)
        with self._lock:
            self._records = [DisplayRecord(id=DEFAULT_ID, name=name,
                                           gateway_url=str(gateway_url or "").strip(),
                                           enabled=True, order=0)]
            self._default_id = DEFAULT_ID
        self.save()
        log.info("created display registry (%s)",
                 "migrated existing settings" if migrated else "fresh install")
        return self


# ---------------------------------------------------------------------------
# migration
# ---------------------------------------------------------------------------
def migrate_settings(data_dir: Path) -> bool:
    """Move a single-display install into the per-display layout. Returns True if
    there was anything to migrate.

    Everything comes across into display `default` and nothing is split out. A settings
    store that is not wholly per-display cannot work here: the gateway is the BACKUP for
    its wall's settings (main.setup_settings_sync mirrors the whole doc there, and a
    rebuilt host restores from it), so a companion-local file holding values for all
    displays would have no gateway to live on and could never be restored. Credentials
    included — the cost is entering an API key once per wall, which is the price of every
    wall's settings being recoverable from its own box.

    The old file is COPIED, never moved. This migration is one-way — a 1.x companion
    cannot read the new layout — so the thing it must not do is destroy the only copy
    of settings someone spent an evening building.
    """
    data_dir = Path(data_dir)
    legacy = data_dir / "app_settings.json"
    if not legacy.exists() or legacy.stat().st_size == 0:
        return False

    target = data_dir / "displays" / DEFAULT_ID / "app_settings.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        shutil.copy2(legacy, target)
        log.info("migrated %s -> %s (the original is kept as a backup)", legacy, target)
    return True
