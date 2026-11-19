"""Fixtures for the offline suite.

* env is cleaned and pinned (tau, fake token, tmp download dir, token file that does not exist),
  so no test can read a developer's environment;
* the real `.token` file next to the script can never be opened by a test;
* module state (`_userid`, `_preflight_ok`, `_skip_warned`, `_perm_warned`) is reset around every test;
* the module's `_client` is swapped for a MockTransport client (redirects off), so no real network
  is possible; after every test the recorded requests are audited (`MoodleMock.audit`);
* tests marked `live` are skipped unless MOODLE_LIVE=1 (this suite has none).
"""

from __future__ import annotations

import os
import socket
from pathlib import Path

import httpx
import pytest

from support import ENV_VARS, FAKE_TOKEN, ROOT, MoodleMock


def pytest_collection_modifyitems(config, items):
    if os.environ.get("MOODLE_LIVE") == "1":
        return
    skip = pytest.mark.skip(reason="live network test; set MOODLE_LIVE=1 to run")
    for item in items:
        if "live" in item.keywords:
            item.add_marker(skip)


@pytest.fixture(autouse=True)
def _env(monkeypatch, tmp_path):
    for var in [k for k in os.environ if k.startswith("MOODLE_")] + list(ENV_VARS):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("MOODLE_SITE", "tau")
    monkeypatch.setenv("MOODLE_TOKEN", FAKE_TOKEN)
    monkeypatch.setenv("MOODLE_TOKEN_FILE", str(tmp_path / "no-such-token-file"))
    monkeypatch.setenv("MOODLE_DOWNLOAD_DIR", str(tmp_path / "downloads"))


@pytest.fixture(autouse=True)
def _never_read_the_real_token_file(monkeypatch):
    """A test that forgets to point MOODLE_TOKEN_FILE elsewhere must fail, not read a real token."""

    def guarded(original):
        def wrapper(self, *args, **kwargs):
            if self.name == ".token" and self.parent.resolve() == ROOT:
                raise AssertionError("a test tried to read the real .token file")
            return original(self, *args, **kwargs)

        return wrapper

    for name in ("read_bytes", "read_text"):
        monkeypatch.setattr(Path, name, guarded(getattr(Path, name)))


@pytest.fixture(autouse=True)
def _no_real_network(monkeypatch):
    """Name lookups and connections to anything but loopback fail loudly (asyncio and the MCP client use
    loopback sockets). Subprocesses get the same guard through tests/nonet/sitecustomize.py."""
    loopback = {"localhost", "127.0.0.1", "::1", "0.0.0.0", "::", ""}
    real_getaddrinfo, real_connect, real_connect_ex = (
        socket.getaddrinfo,
        socket.socket.connect,
        socket.socket.connect_ex,
    )

    def check(host) -> None:
        if isinstance(host, bytes):
            host = host.decode("ascii", "replace")
        if host is not None and str(host).lower() not in loopback:
            raise AssertionError(f"NETWORK BLOCKED by the test suite: {host!r}")

    def getaddrinfo(host, *args, **kwargs):
        check(host)
        return real_getaddrinfo(host, *args, **kwargs)

    def connect(self, address):
        check(address[0] if isinstance(address, tuple) else None)
        return real_connect(self, address)

    def connect_ex(self, address):
        check(address[0] if isinstance(address, tuple) else None)
        return real_connect_ex(self, address)

    monkeypatch.setattr(socket, "getaddrinfo", getaddrinfo)
    monkeypatch.setattr(socket.socket, "connect", connect)
    monkeypatch.setattr(socket.socket, "connect_ex", connect_ex)


def _reset_state(module) -> None:
    module._userid = None
    for name in ("_preflight_ok", "_skip_warned", "_perm_warned"):
        getattr(module, name).clear()  # once-per-process memory of the module


@pytest.fixture(autouse=True)
def m(_env):
    """The module under test, with process-wide caches reset before and after the test."""
    import moodle_mcp

    _reset_state(moodle_mcp)
    yield moodle_mcp
    _reset_state(moodle_mcp)


@pytest.fixture(autouse=True)
def mock(monkeypatch, m):
    """Swap `m._client` for a MockTransport client backed by a fake Moodle."""
    fake = MoodleMock()
    client = httpx.Client(transport=httpx.MockTransport(fake.handler), follow_redirects=False)
    monkeypatch.setattr(m, "_client", client)
    yield fake
    client.close()
    fake.audit()


@pytest.fixture
def dl_dir() -> Path:
    """The (not yet created) download directory selected through MOODLE_DOWNLOAD_DIR."""
    return Path(os.environ["MOODLE_DOWNLOAD_DIR"])
