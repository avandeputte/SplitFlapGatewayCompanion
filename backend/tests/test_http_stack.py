"""One HTTP client stack: the companion runs on httpx2, and classic ``httpx`` is not a
dependency of the image. A stray ``import httpx`` (the httpx2 migration missed a file) is
invisible in tests — the transport tests stub the client — but fatal in production, where
classic httpx is absent: the import raises and the broad connect() handler reports the
gateway offline. This static scan is the guard against that recurring.
"""

import re
from pathlib import Path

APP = Path(__file__).resolve().parents[1] / "app"
_BARE_IMPORT = re.compile(r"^\s*import httpx\s*(#.*)?$", re.M)


def test_no_module_imports_classic_httpx():
    offenders = []
    for py in APP.rglob("*.py"):
        src = py.read_text("utf-8")
        # httpx2 imports read "import httpx2 as httpx"; only a BARE "import httpx" is classic.
        if _BARE_IMPORT.search(src):
            offenders.append(str(py.relative_to(APP.parent)))
    assert not offenders, (
        "these modules still import classic httpx (the image ships httpx2 only, so this "
        f"fails at runtime): {offenders}. Use `import httpx2 as httpx`.")
