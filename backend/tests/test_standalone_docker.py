"""Running the plain Docker image, outside Home Assistant.

The companion registers its own URL with the gateway, so the gateway's "Companion" tab can
link back to it. Outside Home Assistant there is no Supervisor to ask, so the URL is detected
by opening a socket toward the gateway and reading back our own address.

In a BRIDGE-NETWORKED CONTAINER — which is how the README says to run it — that address is
172.17.0.x: ours on the docker0 bridge, and not routable from a gateway sitting out on the
LAN. The link is dead and nothing says so.

It cannot be caught by probing the URL, either, and that is the trap: the probe runs INSIDE the
container, where 172.17.0.x is reachable because it IS us. The check passes and the URL is
still useless. So the address itself is the thing to look at.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from app import main


@pytest.fixture
def in_container(monkeypatch):
    """Pretend we are inside a container, with nothing explicitly configured."""
    monkeypatch.setattr(main, "Path", Path)
    monkeypatch.setattr(Path, "exists", lambda self: str(self) == "/.dockerenv" or False)
    monkeypatch.setitem(main.config.effective, "companion_url", "")
    return monkeypatch


def _warnings(caplog):
    return [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]


def test_a_docker_bridge_address_is_called_out(in_container, caplog):
    with caplog.at_level(logging.WARNING, logger="companion"):
        main._warn_if_container_address("http://172.17.0.3:8000")
    msg = " ".join(_warnings(caplog))
    assert "172.17.0.3" in msg
    assert "COMPANION_PUBLIC_URL" in msg, "the warning has to name the fix, not just complain"


def test_a_user_defined_bridge_too(in_container, caplog):
    """Compose puts you on 172.18-31.x, not the default 172.17 bridge."""
    with caplog.at_level(logging.WARNING, logger="companion"):
        main._warn_if_container_address("http://172.20.0.5:8000")
    assert _warnings(caplog)


def test_a_real_lan_address_is_not_nagged_about(in_container, caplog):
    """--network host, or a bare install: the detected IP is the host's, and it is correct."""
    with caplog.at_level(logging.WARNING, logger="companion"):
        main._warn_if_container_address("http://192.168.1.42:8000")
    assert not _warnings(caplog)


def test_an_explicitly_set_url_is_never_second_guessed(monkeypatch, caplog):
    monkeypatch.setitem(main.config.effective, "companion_url", "http://192.168.1.42:8000")
    with caplog.at_level(logging.WARNING, logger="companion"):
        main._warn_if_container_address("http://172.17.0.3:8000")
    assert not _warnings(caplog), "the user told us; do not argue with them"


def test_outside_a_container_nothing_is_said(monkeypatch, caplog):
    monkeypatch.setitem(main.config.effective, "companion_url", "")
    monkeypatch.setattr(Path, "exists", lambda self: False)      # no /.dockerenv
    with caplog.at_level(logging.WARNING, logger="companion"):
        main._warn_if_container_address("http://172.17.0.3:8000")
    assert not _warnings(caplog)
