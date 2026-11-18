"""Loaded automatically by every Python subprocess the suite starts (support.clean_env puts this folder on
PYTHONPATH): it refuses any name lookup or connection that is not loopback, so no test can ever talk to a
real Moodle server, not even by accident through the server process it spawned."""

import socket

_LOOPBACK = {"localhost", "127.0.0.1", "::1", "0.0.0.0", "::", ""}
_real_getaddrinfo = socket.getaddrinfo
_real_connect = socket.socket.connect
_real_connect_ex = socket.socket.connect_ex


def _check(host) -> None:
    if isinstance(host, bytes):
        host = host.decode("ascii", "replace")
    if host is not None and str(host).lower() not in _LOOPBACK:
        raise AssertionError(f"NETWORK BLOCKED by the test suite: {host!r}")


def _getaddrinfo(host, *args, **kwargs):
    _check(host)
    return _real_getaddrinfo(host, *args, **kwargs)


def _connect(self, address):
    if isinstance(address, tuple):
        _check(address[0])
    return _real_connect(self, address)


def _connect_ex(self, address):
    if isinstance(address, tuple):
        _check(address[0])
    return _real_connect_ex(self, address)


socket.getaddrinfo = _getaddrinfo
socket.socket.connect = _connect
socket.socket.connect_ex = _connect_ex
