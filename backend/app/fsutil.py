"""One way to write a JSON file that must survive a crash.

Temp file, fsync, rename over the target: a kill mid-write leaves the previous
good file intact instead of truncated JSON that silently resets state on the
next start. The registry and the settings store each carried their own copy of
this dance; a file-integrity guarantee is exactly the kind of thing that should
exist once.
"""

from __future__ import annotations

import json
import os
from pathlib import Path


def atomic_write_json(path: Path, doc) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=2, ensure_ascii=False)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)
