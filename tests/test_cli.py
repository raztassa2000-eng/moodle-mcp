"""main(): --sites, configuration errors (exit 2, message on stderr, nothing on stdout), --login, the server start,
and the same behaviour seen from outside, as a real `python moodle_mcp.py` process."""

from __future__ import annotations

import subprocess
import sys
from types import SimpleNamespace

import pytest

from support import FAKE_TOKEN, ROOT, clean_env

SCRIPT = ROOT / "moodle_mcp.py"


def table(m) -> list[str]:
    return [f"{key:11} {name:45} {url}" for key, (name, url) in m.SITES.items()]


@pytest.fixture
def server(m, monkeypatch):
    """Replaces the stdio server with a recorder, so main() can be run without blocking."""
    started = []
    monkeypatch.setattr(m.mcp, "run", lambda *args, **kwargs: started.append((args, kwargs)))
    return started


@pytest.fixture
def login(monkeypatch):
    """A stand-in `moodle_login` module that records how main() calls it."""
    calls = []
    stub = SimpleNamespace(login=lambda base, token_file, forced: calls.append((base, token_file, forced)))
    monkeypatch.setitem(sys.modules, "moodle_login", stub)
    return calls


# ===================================================================================== --sites


def test_sites_prints_one_row_per_preset_and_returns_0(m, mock, server, capsys):
    assert m.main(["--sites"]) == 0
    out, err = capsys.readouterr()
    assert out.splitlines() == table(m)
    assert len(out.splitlines()) == len(m.SITES) >= 30
    assert err == "" and mock.requests == [] and server == []


def test_sites_needs_no_token_and_ignores_a_broken_configuration(m, mock, monkeypatch, capsys):
    monkeypatch.delenv("MOODLE_TOKEN")
    monkeypatch.setenv("MOODLE_SITE", "bogus")
    monkeypatch.setenv("MOODLE_URL", "http://not-https.example")
    monkeypatch.setenv("MOODLE_MAX_DOWNLOAD_MB", "lots")
    assert m.main(["--sites"]) == 0
    assert capsys.readouterr().out.splitlines() == table(m)
    assert mock.requests == []


def test_sites_wins_over_every_other_flag(m, server, login, capsys):
    assert m.main(["--login", "--browser", "--sites"]) == 0
    assert capsys.readouterr().out.splitlines() == table(m)
    assert login == [] and server == []


def test_main_without_arguments_reads_sys_argv(m, monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["moodle_mcp.py", "--sites"])
    assert m.main() == 0
    assert capsys.readouterr().out.splitlines() == table(m)


def test_every_preset_row_names_a_https_url(m):
    for line in table(m):
        assert line.split()[-1].startswith("https://")


# ================================================================== configuration errors -> exit 2


@pytest.mark.parametrize(
    ("env", "value", "needle"),
    [
        ("MOODLE_SITE", "bogus", "bogus"),
        ("MOODLE_URL", "http://moodle.example.edu", "Invalid MOODLE_URL"),
        ("MOODLE_URL", "https://user:S3cr3tPw@moodle.example.edu", "Invalid MOODLE_URL"),
        ("MOODLE_URL", "https://moodle.example.edu/?token=S3cr3tPw", "Invalid MOODLE_URL"),
        ("MOODLE_URL", "junk", "Invalid MOODLE_URL"),
    ],
)
@pytest.mark.parametrize("args", [[], ["--login"]])
def test_bad_configuration_exits_2_with_a_message_on_stderr_only(
    m, server, login, capsys, monkeypatch, env, value, needle, args
):
    monkeypatch.setenv(env, value)
    assert m.main(args) == 2
    out, err = capsys.readouterr()
    assert out == "", "stdout is the MCP channel"
    assert err.startswith("moodle-mcp: ") and needle in err and "Traceback" not in err
    assert "S3cr3tPw" not in err and FAKE_TOKEN not in err
    assert server == [] and login == []


# ==================================================================================== the server


def test_default_run_starts_the_stdio_server_and_prints_nothing(m, server, capsys):
    assert m.main([]) == 0
    assert len(server) == 1
    assert capsys.readouterr() == ("", "")


def test_the_server_starts_without_a_token_so_it_can_explain_itself(m, server, monkeypatch):
    monkeypatch.delenv("MOODLE_TOKEN")
    assert m.main([]) == 0
    assert len(server) == 1


def test_the_server_starts_with_a_broken_token_too(m, server, monkeypatch):
    monkeypatch.setenv("MOODLE_TOKEN", "not a token!")
    assert m.main([]) == 0 and len(server) == 1


# ====================================================================================== --login


@pytest.mark.parametrize(
    ("args", "forced"),
    [
        (["--login"], None),
        (["--login", "--browser"], "browser"),
        (["--login", "--password"], "password"),
        (["--password", "--login"], "password"),
        (["--login", "--password", "--browser"], "browser"),
    ],
)
def test_login_hands_the_base_and_token_file_to_moodle_login(m, server, login, capsys, args, forced):
    assert m.main(args) == 0
    assert login == [(m.get_base(), m.token_file(), forced)]
    assert server == [], "--login does not start the server"
    assert capsys.readouterr().out == ""


# ================================================================================= as a process


def run(*args: str, env: dict | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(  # noqa: S603 - fixed interpreter and repo-local script, no shell
        [sys.executable, *args],
        cwd=ROOT,
        env=clean_env() if env is None else env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=90,
        check=False,
    )


def test_script_sites_works_with_nothing_configured(m):
    result = run(str(SCRIPT), "--sites")
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == table(m), "nothing but the table on stdout"
    assert result.stderr == ""


def test_script_sites_ignores_a_broken_configuration(m):
    env = clean_env(MOODLE_SITE="bogus", MOODLE_URL="http://evil.example", MOODLE_TOKEN_FILE="/no/such/file")
    result = run(str(SCRIPT), "--sites", env=env)
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == table(m)


@pytest.mark.parametrize(
    ("env", "needle"),
    [
        ({"MOODLE_URL": "http://evil.example"}, "Invalid MOODLE_URL"),
        ({"MOODLE_URL": "https://user:S3cr3tPw@evil.example"}, "Invalid MOODLE_URL"),
        ({"MOODLE_SITE": "bogus"}, "bogus"),
    ],
)
def test_script_with_a_bad_configuration_exits_2_and_never_starts_the_server(env, needle):
    result = run(str(SCRIPT), env=clean_env(MOODLE_TOKEN=FAKE_TOKEN, **env))
    assert result.returncode == 2, (result.stdout, result.stderr)
    assert result.stdout == ""
    assert result.stderr.startswith("moodle-mcp: ") and needle in result.stderr
    assert "Traceback" not in result.stderr
    assert FAKE_TOKEN not in result.stderr and "S3cr3tPw" not in result.stderr and "evil" not in result.stderr


def test_script_login_with_a_bad_configuration_exits_2_before_asking_for_anything():
    result = run(str(SCRIPT), "--login", env=clean_env(MOODLE_URL="http://evil.example"))
    assert result.returncode == 2 and result.stdout == "" and "Invalid MOODLE_URL" in result.stderr
