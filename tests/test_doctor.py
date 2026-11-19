"""`--doctor`: OK/WARN/FAIL lines and a summary; never the token, a name or any other data from the site."""

from __future__ import annotations

import re
import subprocess
import sys

import pytest

from support import FAKE_TOKEN, ROOT, assert_no_token, assert_no_token_fragment, clean_env, preflight_reply

SITE_INFO = "core_webservice_get_site_info"
PRIVATE = ("Liya Cohen", "s1234567", "Secret Campus", "Quantum Gravity 101")
WRITES = ["core_message_send_instant_messages", "mod_assign_save_submission"]


def serve(m, mock, extra=(), drop=()):
    """The token's site info: every function this server uses (minus `drop`) plus `extra`, and private-looking data."""
    names = sorted(m.READ_FUNCTIONS - {"tool_mobile_get_public_config", *drop}) + list(extra)
    mock.on(
        SITE_INFO,
        {
            "userid": 42,
            "username": "s1234567",
            "fullname": "Liya Cohen",
            "sitename": "Secret Campus",
            "release": "4.1.2 (Build: 20230101)",
            "functions": [{"name": n, "version": "2022"} for n in [*names, "Quantum Gravity 101"]],
        },
    )


def doctor(m, capsys):
    """Run `--doctor`; returns (exit code, stdout lines) after checking what must hold for every run."""
    rc = m.main(["--doctor"])
    out, err = capsys.readouterr()
    assert_no_token(out + err)
    assert_no_token_fragment(out + err)
    assert "Traceback" not in out + err and not any(p in out + err for p in PRIVATE)
    lines = out.splitlines()
    assert all(re.match(r"(OK|WARN|FAIL): |doctor: \d+ ok, \d+ warn, \d+ fail$", line) for line in lines)
    return rc, lines


def warnings(lines):
    return [line for line in lines if line.startswith("WARN")]


@pytest.mark.parametrize(("writes", "summary"), [([], "5 ok, 0 warn, 0 fail"), (WRITES, "4 ok, 1 warn, 0 fail")])
def test_doctor_happy_path(m, mock, capsys, writes, summary):
    serve(m, mock, extra=writes)
    rc, lines = doctor(m, capsys)
    assert rc == 0 and lines[-1] == f"doctor: {summary}"
    assert len(warnings(lines)) == bool(writes) and all(w in " ".join(warnings(lines)) for w in writes)
    assert any("Moodle 4.1.2" in line for line in lines)
    assert len(mock.preflight_requests()) == 1 and mock.functions_called() == [SITE_INFO]


def test_doctor_caps_the_list_of_write_functions(m, mock, capsys):
    many = [f"core_demo_add_item_{i:02d}" for i in range(35)]
    serve(m, mock, extra=many)
    rc, lines = doctor(m, capsys)
    (warn,) = warnings(lines)
    assert rc == 0 and "35 write functions" in warn and warn.endswith("(+5 more)")
    assert many[29] in warn and many[30] not in warn


def test_doctor_warns_about_read_functions_the_token_lacks(m, mock, capsys):
    serve(m, mock, drop=["mod_quiz_get_user_attempts"])  # the public config is never expected in the token's list
    rc, lines = doctor(m, capsys)
    (warn,) = warnings(lines)
    assert rc == 0 and "mod_quiz_get_user_attempts" in warn and "tool_mobile_get_public_config" not in warn


@pytest.mark.parametrize("bad", [None, "not a token!"])
def test_doctor_without_a_usable_token_fails_and_sends_no_token(m, mock, capsys, monkeypatch, bad):
    if bad is None:
        monkeypatch.delenv("MOODLE_TOKEN")
    else:
        monkeypatch.setenv("MOODLE_TOKEN", bad)
    rc, lines = doctor(m, capsys)
    assert rc == 1 and lines[-1] == "doctor: 2 ok, 0 warn, 1 fail"
    assert any(line.startswith("FAIL: token") for line in lines)
    assert mock.rest_requests() == []


@pytest.mark.parametrize(("var", "value"), [("MOODLE_URL", "http://evil.example"), ("MOODLE_SITE", "bogus")])
def test_doctor_with_a_bad_configuration_fails_without_any_request(m, mock, capsys, monkeypatch, var, value):
    monkeypatch.setenv(var, value)
    rc, lines = doctor(m, capsys)
    assert rc == 1 and lines[0].startswith("FAIL: Moodle URL") and lines[-1] == "doctor: 0 ok, 0 warn, 1 fail"
    assert mock.requests == []


def test_doctor_stops_before_the_token_is_sent_when_the_site_is_not_the_configured_one(m, mock, capsys):
    mock.preflight = preflight_reply("https://evil.example")
    serve(m, mock, extra=WRITES)
    rc, lines = doctor(m, capsys)
    assert rc == 1 and lines[-1] == "doctor: 2 ok, 0 warn, 1 fail"
    assert any(line.startswith("FAIL: site identity") for line in lines)
    assert mock.rest_requests() == [] and all(b"wstoken" not in r.content for r in mock.requests)


def test_doctor_warns_when_the_identity_check_is_disabled(m, mock, capsys, monkeypatch):
    monkeypatch.setenv("MOODLE_SKIP_PREFLIGHT", "1")
    mock.preflight = preflight_reply("https://evil.example")  # would fail if the check ran
    serve(m, mock)
    rc, lines = doctor(m, capsys)
    assert rc == 0 and lines[-1] == "doctor: 4 ok, 1 warn, 0 fail"
    assert "MOODLE_SKIP_PREFLIGHT=1" in warnings(lines)[0] and mock.preflight_requests() == []


@pytest.mark.parametrize(
    "reply",
    [
        {"exception": "moodle_exception", "errorcode": "invalidtoken", "message": "Invalid token"},
        {"exception": "x", "errorcode": "odd", "message": f"boom {FAKE_TOKEN} wstoken={FAKE_TOKEN}"},
    ],
)
def test_doctor_reports_a_web_service_error_without_the_token(m, mock, capsys, reply):
    mock.on(SITE_INFO, reply)
    rc, lines = doctor(m, capsys)
    assert rc == 1 and lines[-1] == "doctor: 3 ok, 0 warn, 1 fail"
    assert any(line.startswith("FAIL: web service call failed") for line in lines)


def test_script_doctor_with_a_bad_url_exits_1_without_a_traceback(tmp_path):
    env = clean_env(MOODLE_URL="http://evil.example", MOODLE_TOKEN=FAKE_TOKEN, MOODLE_TOKEN_FILE=str(tmp_path / "none"))
    result = subprocess.run(
        [sys.executable, str(ROOT / "moodle_mcp.py"), "--doctor"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=90,
        check=False,
    )
    assert result.returncode == 1 and result.stderr == ""
    assert result.stdout.splitlines()[0].startswith("FAIL: Moodle URL")
    assert result.stdout.splitlines()[-1] == "doctor: 0 ok, 0 warn, 1 fail" and FAKE_TOKEN not in result.stdout
