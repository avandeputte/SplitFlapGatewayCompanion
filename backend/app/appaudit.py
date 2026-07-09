"""
appaudit.py — static safety + validity checks for uploaded apps.

Uploaded apps are Python that the companion imports and runs in-process, so an
upload is a code-execution vector. This module statically inspects an app's
``app.py`` (via AST, WITHOUT executing it) and rejects clearly dangerous or
malformed code with a human-readable reason, before it is ever imported.

This is defense-in-depth, not a sandbox: a determined attacker can obfuscate
around static checks. It exists to stop obviously-malicious or broken uploads
(process/file/network abuse, code obfuscation, introspection escapes) with a
clear message, and to keep honest apps honest. Only upload apps you trust.
"""

from __future__ import annotations

import ast

# import <module> / from <module> import ... — rejected outright.
_BANNED_MODULES = {
    "subprocess": "run external programs",
    "ctypes": "execute arbitrary native code",
    "socket": "open raw network sockets",
    "socketserver": "open raw network sockets",
    "asyncio": "hijack the event loop",
    "multiprocessing": "spawn processes",
    "threading": "spawn threads",
    "_thread": "spawn threads",
    "pty": "spawn a pseudo-terminal",
    "marshal": "execute serialized code",
    "pickle": "execute serialized objects (deserialization RCE)",
    "shelve": "execute serialized objects",
    "importlib": "import modules dynamically",
    "imp": "import modules dynamically",
    "fcntl": "perform low-level OS control",
    "mmap": "map raw memory",
    "resource": "change OS resource limits",
    "signal": "install signal handlers",
    "gc": "walk the object graph (sandbox escape)",
    "builtins": "rebind built-in functions",
    "__builtin__": "rebind built-in functions",
    "ftplib": "open arbitrary network connections",
    "telnetlib": "open arbitrary network connections",
    "smtplib": "send email",
    "shutil": "modify the filesystem",
}

# Bare built-in calls — name() — that are rejected.
_BANNED_CALLS = {
    "eval": "evaluate arbitrary code",
    "exec": "execute arbitrary code",
    "compile": "compile arbitrary code",
    "__import__": "import modules dynamically",
    "breakpoint": "drop into a debugger",
    "input": "block the worker on stdin",
    "globals": "reach the global namespace (sandbox escape)",
    "memoryview": "access raw memory",
}

# os.<attr>(...) — process / filesystem / environment access.
_BANNED_OS_ATTRS = {
    "system", "popen", "popen2", "popen3", "popen4", "posix_spawn", "posix_spawnp",
    "spawnl", "spawnle", "spawnlp", "spawnlpe", "spawnv", "spawnve", "spawnvp", "spawnvpe",
    "exec", "execl", "execle", "execlp", "execlpe", "execv", "execve", "execvp", "execvpe",
    "fork", "forkpty", "kill", "killpg", "abort", "_exit",
    "remove", "unlink", "rmdir", "removedirs", "rename", "renames", "replace", "truncate",
    "ftruncate", "symlink", "link", "mkfifo", "mknod", "mkdir", "makedirs", "chmod", "chown",
    "chroot", "chdir", "putenv", "unsetenv", "setuid", "setgid", "seteuid", "setegid",
    "setsid", "setpgid", "environ", "environb", "getenv", "getenvb", "walk", "open", "write",
}

_BANNED_SYS_ATTRS = {"_getframe", "settrace", "setprofile", "exit", "modules"}

# Attribute access that reaches interpreter internals — classic escape chains.
_BANNED_ATTRS = {
    "__subclasses__", "__globals__", "__bases__", "__mro__", "__builtins__", "__code__",
    "__closure__", "__base__", "f_globals", "f_locals", "f_back", "gi_frame", "cr_frame",
}

# Path/os-style mutation methods (writing or deleting files).
_WRITE_METHODS = {
    "write_text", "write_bytes", "unlink", "rmdir", "rmtree", "touch", "symlink_to",
    "hardlink_to", "chmod", "lchmod",
}


def audit_python(src: str) -> list[str]:
    """Return a list of human-readable violations found in ``src`` (empty = clean).
    A syntax error is reported as a single violation."""
    try:
        tree = ast.parse(src)
    except SyntaxError as e:
        return [f"line {e.lineno or '?'}: syntax error: {e.msg}"]

    out: list[str] = []

    def add(node, msg):
        out.append(f"line {getattr(node, 'lineno', '?')}: {msg}")

    danger_strings = _BANNED_ATTRS | _BANNED_OS_ATTRS | set(_BANNED_CALLS)

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                root = a.name.split(".")[0]
                if root in _BANNED_MODULES:
                    add(node, f"imports '{a.name}' (used to {_BANNED_MODULES[root]}) — not allowed")

        elif isinstance(node, ast.ImportFrom):
            root = (node.module or "").split(".")[0]
            if root in _BANNED_MODULES:
                add(node, f"imports from '{node.module}' (used to {_BANNED_MODULES[root]}) — not allowed")
            elif root == "os":
                for a in node.names:
                    if a.name in _BANNED_OS_ATTRS:
                        add(node, f"imports os.{a.name} — process/file/env access is not allowed")

        elif isinstance(node, ast.Call):
            f = node.func
            if isinstance(f, ast.Name):
                if f.id in _BANNED_CALLS:
                    add(node, f"calls {f.id}() (used to {_BANNED_CALLS[f.id]}) — not allowed")
                elif f.id == "open":
                    mode = None
                    if len(node.args) > 1 and isinstance(node.args[1], ast.Constant):
                        mode = node.args[1].value
                    for kw in node.keywords:
                        if kw.arg == "mode" and isinstance(kw.value, ast.Constant):
                            mode = kw.value.value
                    if isinstance(mode, str) and any(c in mode for c in "wax+"):
                        add(node, f"opens a file for writing (mode {mode!r}) — writing files is not allowed")
                elif f.id in ("getattr", "setattr", "delattr") and len(node.args) > 1:
                    a1 = node.args[1]
                    if isinstance(a1, ast.Constant) and a1.value in danger_strings:
                        add(node, f"{f.id}(..., {a1.value!r}) — introspection escape is not allowed")
            elif isinstance(f, ast.Attribute):
                base = f.value
                if isinstance(base, ast.Name) and base.id == "os" and f.attr in _BANNED_OS_ATTRS:
                    add(node, f"calls os.{f.attr}() — process/file/environment access is not allowed")
                elif isinstance(base, ast.Name) and base.id == "sys" and f.attr in _BANNED_SYS_ATTRS:
                    add(node, f"calls sys.{f.attr}() — interpreter manipulation is not allowed")
                elif f.attr in _WRITE_METHODS:
                    add(node, f"calls .{f.attr}() — writing or deleting files is not allowed")

        elif isinstance(node, ast.Attribute):
            base = node.value
            if node.attr in _BANNED_ATTRS:
                add(node, f"accesses .{node.attr} — introspection/sandbox escape is not allowed")
            elif isinstance(base, ast.Name) and base.id == "os" and node.attr in ("environ", "environb"):
                add(node, "reads os.environ — accessing environment variables (secrets) is not allowed")
            elif isinstance(base, ast.Name) and base.id == "sys" and node.attr == "modules":
                add(node, "accesses sys.modules — interpreter manipulation is not allowed")

    # De-dupe while preserving order; cap the list so the message stays readable.
    seen, deduped = set(), []
    for m in out:
        if m not in seen:
            seen.add(m)
            deduped.append(m)
    return deduped[:15]


def find_fetch(src: str):
    """Return the module-level ``fetch`` FunctionDef (sync or async) or None."""
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return None
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "fetch":
            return node
    return None
