"""plugin_install.py — uploading, vetting and removing user apps.

The whole install pipeline for an uploaded app ``.zip``: zip-bomb/traversal safety,
structural manifest validation, the static safety audit (appaudit), fetch() arity and
import checks, channel/quiz data validation, i18n-sidecar validation, settings scoping
(declare everything the code reads), and the copy into the user apps dir — plus
``delete_app``. Nothing here runs apps; that is plugins.py's job.

``rt`` in these functions is the PluginRuntime — the same object ``self`` names in the
thin delegating methods plugins.py keeps for compatibility (tests call the validators
through those, e.g. ``PluginRuntime._validate_channel``).
"""

from __future__ import annotations

import importlib.util
import io
import json
import re
import shutil
import tempfile
import zipfile
from pathlib import Path

from . import appaudit
from .catalog import CATALOG_KEYS, GLOBAL_STORAGE_KEYS
from .plugin_schema import infer_field


def install_zip(rt, data: bytes, *, enable: bool = True) -> dict:
    """Validate + install an uploaded app .zip into the user apps dir.

    The zip must contain exactly one ``manifest.json`` (the app folder). The
    app id is the containing folder's name (or the manifest ``id``). Returns
    {id, name, type}; raises ValueError with a human message on any problem.

    The upload is vetted before install: the manifest is structurally checked,
    a functional app's ``app.py`` is statically audited for disallowed /
    malicious operations (rejected with reasons if unsafe), its fetch()
    signature is verified, and every setting the code reads that isn't a global
    is declared as an app-level setting in the manifest (rewritten if needed).
    Only after the audit passes is the app imported to surface import errors.

    SECURITY: the static audit is defense-in-depth, not a sandbox; a vetted app
    still runs in-process. Only upload apps you trust.
    """
    from .plugins import _CHANNELISH

    with tempfile.TemporaryDirectory() as td:
        tdp = Path(td)
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                # The route caps the COMPRESSED size; a zip bomb is judged by
                # what it inflates to. In Docker the tempdir is tmpfs — RAM —
                # so an unchecked extractall is an OOM, not just a full disk.
                infos = z.infolist()
                if len(infos) > 512:
                    raise ValueError("zip has too many files (max 512)")
                total = sum(i.file_size for i in infos)
                if total > 64 * 1024 * 1024:
                    raise ValueError("zip expands too large (max 64 MB uncompressed)")
                for i in infos:
                    n = i.filename
                    if n.startswith("/") or ".." in Path(n).parts:
                        raise ValueError("unsafe path in zip")
                z.extractall(tdp)
        except zipfile.BadZipFile:
            raise ValueError("not a valid .zip file")

        manifests = [m for m in tdp.rglob("manifest.json") if "__MACOSX" not in m.parts]
        if len(manifests) != 1:
            raise ValueError("the zip must contain exactly one manifest.json (the app folder)")
        mpath = manifests[0]
        root = mpath.parent
        try:
            manifest = json.loads(mpath.read_text("utf-8"))
        except Exception as e:
            raise ValueError(f"invalid manifest.json: {e}")

        raw_id = root.name if root != tdp else (manifest.get("id") or manifest.get("name") or "app")
        app_id = re.sub(r"[^A-Za-z0-9_-]", "", str(raw_id)) or "app"

        validate_manifest(manifest)
        kind = manifest.get("type")
        if kind == "functional":
            app_py = root / "app.py"
            if not app_py.is_file():
                raise ValueError("functional app is missing app.py")
            src = app_py.read_text("utf-8", errors="replace")
            # 1) Static safety audit — BEFORE the module is ever executed.
            violations = appaudit.audit_python(src)
            if violations:
                raise ValueError(
                    "app rejected — app.py contains operations that are not allowed:\n  - "
                    + "\n  - ".join(violations))
            # 2) fetch() must exist with the right arity (checked statically).
            fn = appaudit.find_fetch(src)
            if fn is None:
                raise ValueError(
                    "app.py must define a fetch(settings, format_lines, get_rows, get_cols) function")
            if len(fn.args.args) < 4:
                raise ValueError(
                    "fetch() must accept (settings, format_lines, get_rows, get_cols)")
            # 3) Scope settings: declare every non-global setting the code reads
            #    as an app-level setting, rewriting the manifest if needed.
            if scope_manifest_settings(rt, manifest, src):
                mpath.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), "utf-8")
            # 4) Only now import it — the audit has cleared it — to surface
            #    import/syntax errors (missing deps, etc.).
            validate_fetch(app_py)
        elif kind in _CHANNELISH:
            validate_channel(root, quiz=(kind == "quiz"))
        validate_i18n_sidecars(root)

        rt.user_apps_dir.mkdir(parents=True, exist_ok=True)
        dest = rt.user_apps_dir / app_id
        if dest.exists():
            shutil.rmtree(dest)
        shutil.copytree(root, dest)

    if enable:
        rt.set_installed(app_id, True)   # also reloads
    else:
        rt.load()
    return {"id": app_id, "name": manifest.get("name"), "type": kind}


def validate_manifest(manifest) -> None:
    """Structural manifest checks with human-readable errors."""
    if not isinstance(manifest, dict):
        raise ValueError("manifest.json must be a JSON object")
    name = manifest.get("name")
    if not name or not isinstance(name, str):
        raise ValueError("manifest.json is missing a 'name'")
    if manifest.get("type") not in ("functional", "channel", "quiz"):
        raise ValueError("manifest 'type' must be 'functional', 'channel' or 'quiz'")
    settings = manifest.get("settings")
    if settings is not None:
        if not isinstance(settings, list):
            raise ValueError("manifest 'settings' must be a list")
        for i, st in enumerate(settings):
            if not isinstance(st, dict) or not st.get("key"):
                raise ValueError(f"manifest setting #{i + 1} must be an object with a 'key'")


def validate_i18n_sidecars(root: Path) -> None:
    """Reject a broken i18n/<lang>.json at upload rather than at render.
    Optional; only files matching the language pattern are held to shape."""
    from .plugins import _META_STR_KEYS, I18N_META_FILE

    i18n_dir = root / "i18n"
    if not i18n_dir.is_dir():
        return
    for f in sorted(i18n_dir.glob("*.json")):
        if not I18N_META_FILE.match(f.name):
            continue
        try:
            d = json.loads(f.read_text("utf-8"))
        except Exception as e:
            raise ValueError(f"invalid i18n/{f.name}: {e}")
        if not isinstance(d, dict):
            raise ValueError(f"i18n/{f.name} must be a JSON object")
        for k in _META_STR_KEYS:
            if k in d and not isinstance(d[k], str):
                raise ValueError(f"i18n/{f.name}: '{k}' must be a string")
        if "settings" in d and not isinstance(d["settings"], dict):
            raise ValueError(f"i18n/{f.name}: 'settings' must be an object")


def validate_channel(root: Path, quiz: bool = False) -> None:
    from .plugins import LANG_DATA_FILE

    dp = root / "data.json"
    if not dp.is_file():
        raise ValueError("channel app is missing data.json")
    # data.json is required (it is the fallback for any language that has no
    # translation); data_<lang>.json sidecars are optional and held to the same
    # shape, so a broken translation is rejected at upload rather than at render.
    files = [dp] + sorted(f for f in root.glob("data_*.json") if LANG_DATA_FILE.match(f.name))
    for f in files:
        try:
            data = json.loads(f.read_text("utf-8"))
        except Exception as e:
            raise ValueError(f"invalid {f.name}: {e}")
        if not isinstance(data, dict) or not (data.get("pages") or data.get("groups")):
            raise ValueError(
                f"channel app's {f.name} must have a non-empty 'pages' or 'groups' list")
        if quiz:
            # A quiz's every group is a [question, answer] pair — shown as a two-screen reveal.
            for g in data.get("groups") or []:
                if not (isinstance(g, list) and len(g) == 2
                        and all(isinstance(s, str) and s.strip() for s in g)):
                    raise ValueError(
                        f"quiz app's {f.name}: every group must be a [question, answer] pair")


def scope_manifest_settings(rt, manifest: dict, src: str) -> bool:
    """Ensure every non-global setting the app reads is declared as an app-level
    setting in the manifest. Adds inferred fields for settings the code reads
    but never declares, and drops a misleading ``global_key`` flag from any
    non-catalog (i.e. genuinely app-level) setting. Returns True if changed."""
    settings = manifest.setdefault("settings", [])
    if not isinstance(settings, list):
        return False
    declared, changed = set(), False
    for st in settings:
        if not isinstance(st, dict):
            continue
        k = st.get("key")
        if k:
            declared.add(k)
        it = st.get("inline_toggle")
        if isinstance(it, dict) and it.get("key"):
            declared.add(it["key"])
        # A non-catalog setting is app-level regardless of any global_key flag;
        # drop the misleading flag so the manifest is honest about its scope.
        if k and k not in CATALOG_KEYS and st.pop("global_key", None) is not None:
            changed = True
    for key, dflt in rt._scan_reads(src).items():
        if key in declared or key in GLOBAL_STORAGE_KEYS or key == "currency_symbol":
            continue
        f = infer_field(key, key, dflt)   # manifest uses the raw key
        f["note"] = "Auto-declared on upload — the app reads this setting."
        settings.append(f)
        declared.add(key)
        changed = True
    return changed


def validate_fetch(app_py: Path) -> None:
    spec = importlib.util.spec_from_file_location(f"_upload_check_{app_py.parent.name}", str(app_py))
    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
    except Exception as e:
        raise ValueError(f"app.py failed to import: {e}")
    if not (hasattr(mod, "fetch") and callable(mod.fetch)):
        raise ValueError("app.py has no fetch() function")


def delete_app(rt, app_id: str) -> None:
    """Remove a user-uploaded app entirely (built-ins can't be deleted)."""
    app_dir = rt._app_dir(app_id)
    if app_dir is None:
        raise KeyError(app_id)
    if rt.is_builtin(app_id):
        raise ValueError("built-in apps cannot be deleted")
    rt.settings.set_installed([a for a in rt.settings.installed_apps if a != app_id])
    shutil.rmtree(app_dir, ignore_errors=True)
    rt.load()
