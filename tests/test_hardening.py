"""Guarantees about the modules themselves: importing has no side effects, moodle_safety stays pure, the
request-URL loggers stay quiet, and nothing but the MCP protocol is ever written to stdout."""

from __future__ import annotations

import ast
import json
import logging
import socket
import subprocess
import sys

import httpx
import pytest

import moodle_safety
from support import FAKE_TOKEN, PLUGINFILE, ROOT, TAU_URL, ChunkStream, clean_env, courses


def probe(code: str, env: dict | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(  # noqa: S603 - fixed interpreter, inline code, no shell
        [sys.executable, "-c", code],
        cwd=ROOT,
        env=clean_env() if env is None else env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=90,
        check=False,
    )


# ======================================================================= importing has no side effects


def test_importing_with_nothing_configured_prints_nothing_and_exits_cleanly():
    result = probe("import moodle_mcp")
    assert result.returncode == 0, result.stderr
    assert result.stdout == "" and result.stderr == ""


@pytest.mark.parametrize(
    "env",
    [
        {"MOODLE_SITE": "bogus"},
        {"MOODLE_URL": "http://evil.example"},
        {"MOODLE_URL": "https://user:pw@moodle.example.edu"},
        {"MOODLE_TOKEN": "not a token!!", "MOODLE_TOKEN_FILE": "/definitely/not/here"},
        {"MOODLE_MAX_DOWNLOAD_MB": "lots", "MOODLE_DOWNLOAD_SECONDS": "soon"},
        {"MOODLE_DOWNLOAD_DIR": "/proc/definitely/not/writable"},
    ],
    ids=["site", "url-http", "url-userinfo", "token", "numbers", "download-dir"],
)
def test_importing_with_broken_settings_does_not_fail_it_is_lazy(env):
    result = probe("import moodle_mcp", clean_env(**env))
    assert result.returncode == 0, result.stderr
    assert result.stdout == "" and result.stderr == ""


def test_importing_never_touches_the_network_the_disk_or_the_token_file(tmp_path):
    marker = tmp_path / "must-not-exist"
    code = f"""
import socket, builtins, pathlib
def boom(*a, **k):
    raise AssertionError("network access at import time")
socket.getaddrinfo = boom
socket.create_connection = boom
socket.socket.connect = boom
real_open = builtins.open
def guarded_open(file, *a, **k):
    if ".token" in str(file) or {str(marker)!r} in str(file):
        raise AssertionError("file access at import time: " + str(file))
    return real_open(file, *a, **k)
builtins.open = guarded_open
import moodle_mcp
assert not pathlib.Path({str(marker)!r}).exists()
"""
    env = clean_env(MOODLE_DOWNLOAD_DIR=str(marker), MOODLE_TOKEN_FILE=str(marker) + ".token")
    result = probe(code, env)
    assert result.returncode == 0, result.stderr
    assert result.stdout == "" and not marker.exists()


def test_module_level_state_and_http_client_defaults():
    result = probe(
        "import json, moodle_mcp as m\n"
        "t = m._client.timeout\n"
        "print(json.dumps([m._client.follow_redirects, t.connect, t.read, t.write, t.pool, m.mcp.name,\n"
        "                  m._userid, sorted(m._preflight_ok), sorted(m._skip_warned), sorted(m._perm_warned)]))\n"
    )
    assert result.returncode == 0, result.stderr
    follow, connect, read, write, pool, name, userid, ok, skip, perm = json.loads(result.stdout)
    assert follow is False, "redirects are never followed"
    assert (connect, read, write, pool) == (10, 30, 30, 30)
    assert name == "moodle" and userid is None and ok == skip == perm == []


# ================================================================================= moodle_safety is pure


def test_moodle_safety_imports_alone_silently_without_http_or_server_code():
    code = (
        "import sys\n"
        "before = set(sys.modules)  # (the suite's own network guard has already imported socket)\n"
        "import moodle_safety\n"
        "new = {name.split('.')[0] for name in set(sys.modules) - before}\n"
        "bad = new & {'moodle_mcp', 'httpx', 'httpcore', 'mcp', 'socket', 'subprocess', 'ssl', 'logging'}\n"
        "assert not bad, 'moodle_safety imported ' + ', '.join(sorted(bad))\n"
    )
    result = probe(code)
    assert result.returncode == 0, result.stderr
    assert result.stdout == "" and result.stderr == ""


def test_moodle_safety_source_has_no_network_environment_or_printing():
    tree = ast.parse((ROOT / "moodle_safety.py").read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            imported.add((node.module or "").split(".")[0])
    assert imported <= {"ipaddress", "re", "unicodedata", "urllib"}, imported
    calls = {n.func.id for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
    assert not calls & {"print", "open", "input", "exec", "eval", "__import__"}, calls
    assert "environ" not in {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}


def test_moodle_mcp_reexports_the_safety_helpers(m):
    for name in ("validate_base_url", "check_download_url", "safe_filename", "safe_subfolder"):
        assert getattr(m, name) is getattr(moodle_safety, name), name


def test_moodle_mcp_helpers_reject_what_the_pure_functions_reject(m):
    with pytest.raises(ValueError):
        m.validate_base_url("http://moodle.example.edu")
    assert m.safe_filename("a:b") == "a b" and m._safe("con") == "_con"


# ================================================================================ logging and stdout


def test_httpx_and_httpcore_loggers_are_kept_at_warning(m):
    for name in ("httpx", "httpcore"):
        assert logging.getLogger(name).level == logging.WARNING, name
        assert not logging.getLogger(name).isEnabledFor(logging.INFO), name


def _use_everything(m, mock, monkeypatch, query_token: bool):
    if query_token:
        monkeypatch.setenv("MOODLE_DOWNLOAD_TOKEN_IN_QUERY", "1")
        mock.allow_token_in_url = True
    courses(mock)
    mock.on("core_course_get_contents", [])
    m.call("core_course_get_contents", courseid=7)
    m.download_file(f"{TAU_URL}{PLUGINFILE}1/a.pdf?forcedownload=1")


@pytest.mark.parametrize("query_token", [False, True], ids=["post-body", "query-fallback"])
def test_info_logging_never_shows_the_token(m, mock, monkeypatch, caplog, query_token):
    caplog.set_level(logging.INFO)
    _use_everything(m, mock, monkeypatch, query_token)
    assert FAKE_TOKEN not in caplog.text
    assert not [r for r in caplog.records if r.name.startswith(("httpx", "httpcore"))], "no request log lines at all"


def test_control_the_request_log_would_show_the_token_if_the_guard_were_missing(m, mock, monkeypatch, caplog):
    """Proves the test above can fail: with httpx at INFO the fallback URL (with ?token=) is logged."""
    logger = logging.getLogger("httpx")
    logger.setLevel(logging.INFO)
    try:
        caplog.set_level(logging.INFO)
        _use_everything(m, mock, monkeypatch, query_token=True)
    finally:
        logger.setLevel(logging.WARNING)
    assert FAKE_TOKEN in caplog.text


def test_errors_never_end_up_in_the_log_with_the_token(m, mock, caplog):
    caplog.set_level(logging.DEBUG)
    leaky = f"{TAU_URL}{PLUGINFILE}1/a.pdf?token={FAKE_TOKEN}"
    mock.download = lambda _r: httpx.Response(200, stream=ChunkStream([b"x"], error=httpx.ReadError(leaky)))
    with pytest.raises(m.MoodleError):
        m.download_file(f"{TAU_URL}{PLUGINFILE}1/a.pdf")
    assert FAKE_TOKEN not in caplog.text


def test_nothing_is_written_to_stdout_by_the_tools_or_the_downloads(m, mock, capsys):
    courses(mock)
    mock.on("core_course_get_contents", [])
    mock.on("core_calendar_get_action_events_by_timesort", {"events": []})
    m.list_courses()
    m.course_contents(7)
    m.upcoming_deadlines()
    m.download_file(f"{TAU_URL}{PLUGINFILE}1/a.pdf")
    m.download_course(7)
    with pytest.raises(m.MoodleError):
        mock.on("core_course_get_contents", {"exception": "x", "errorcode": "invalidtoken", "message": "m"})
        m.course_contents(7)
    out, err = capsys.readouterr()
    assert out == "" and FAKE_TOKEN not in err


# ============================================================ the suite itself cannot reach a real server


def test_the_suite_blocks_lookups_and_connections_to_real_hosts():
    with pytest.raises(AssertionError, match="NETWORK BLOCKED"):
        socket.getaddrinfo("moodle.tau.ac.il", 443)
    with socket.socket() as sock, pytest.raises(AssertionError, match="NETWORK BLOCKED"):
        sock.connect(("93.184.216.34", 443))
    assert socket.getaddrinfo("localhost", 80), "loopback stays available (asyncio, the MCP client)"


def test_subprocesses_started_by_the_suite_are_offline_too():
    result = probe("import socket; socket.getaddrinfo('moodle.tau.ac.il', 443)")
    assert result.returncode != 0 and "NETWORK BLOCKED" in result.stderr
    assert probe("import socket; socket.getaddrinfo('localhost', 80); print('fine')").stdout.strip() == "fine"
