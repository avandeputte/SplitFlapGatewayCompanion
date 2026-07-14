"""SplitFlapGatewayCompanion backend package."""

from pathlib import Path


def _read_version() -> str:
    """Single source of truth: the repo-root VERSION file (the same file the
    Docker image and CI tags read), so the version shown in the UI can never
    drift from the release again — as it did, stuck at 0.1.0 for many releases.
    Falls back to a literal only if the file somehow isn't packaged."""
    try:
        return (Path(__file__).resolve().parents[2] / "VERSION").read_text().strip()
    except OSError:
        return "1.0.0"


__version__ = "1.9.0-beta.16"
