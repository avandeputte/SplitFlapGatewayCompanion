"""One HTTP client stack: the companion runs on httpx2, and classic ``httpx`` is not a
dependency of the image. A stray ``import httpx`` (the httpx2 migration missed a file) is
invisible in tests — the transport tests stub the client — but fatal in production, where
classic httpx is absent: the import raises and the broad connect() handler reports the
gateway offline. This static scan is the guard against that recurring.
"""

import re
from pathlib import Path

APP = Path(__file__).resolve().parents[1] / "app"
# Any import of classic ``httpx`` — bare, aliased, or ``from`` — but NOT ``httpx2`` (the
# lookahead rejects a following word char, so "httpx2 as httpx" / "from httpx2 import" pass).
_CLASSIC_IMPORT = re.compile(
    r"^\s*(?:import\s+httpx(?!\w)(?:\s+as\s+\w+)?|from\s+httpx(?!\w)\s+import\b).*$", re.M)


def test_no_module_imports_classic_httpx():
    offenders = []
    for py in APP.rglob("*.py"):
        src = py.read_text("utf-8")
        if _CLASSIC_IMPORT.search(src):
            offenders.append(str(py.relative_to(APP.parent)))
    assert not offenders, (
        "these modules still import classic httpx (the image ships httpx2 only, so this "
        f"fails at runtime): {offenders}. Use `import httpx2 as httpx`.")


def test_the_guard_flags_every_classic_import_form_but_not_httpx2():
    for form in ("import httpx", "import httpx as hx", "from httpx import Client",
                 "from httpx import Client, HTTPError", "    import httpx  # noqa"):
        assert _CLASSIC_IMPORT.search(form), f"guard missed a classic import: {form!r}"
    for ok in ("import httpx2 as httpx", "from httpx2 import Client", "import httpxfoo"):
        assert not _CLASSIC_IMPORT.search(ok), f"guard false-positived on: {ok!r}"
