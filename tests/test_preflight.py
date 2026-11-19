"""The identity preflight: before the token is first sent to a base, a token-less request checks that the
host answers like that Moodle. Cached per base; any doubt means the token is never sent."""

from __future__ import annotations

import json

import httpx
import pytest

from support import (
    FAKE_TOKEN,
    PREFLIGHT_OK,
    PREFLIGHT_PATH,
    TAU_HOST,
    TAU_URL,
    ChunkStream,
    assert_no_leak,
    preflight_reply,
)

FN = "core_course_get_contents"
REFUSED = "Refusing to send your token"


def _request() -> httpx.Request:
    return httpx.Request("POST", TAU_URL)


def _call(m, mock):
    mock.on(FN, [])
    return m.call(FN, courseid=1)


def _assert_refused_without_sending_the_token(m, mock, message: str | None = None):
    with pytest.raises(m.MoodleError) as info:
        _call(m, mock)
    assert str(info.value).startswith(REFUSED + ": "), str(info.value)
    assert_no_leak(str(info.value))
    assert mock.rest_requests() == [], "the token must not be sent after a failed identity check"
    assert mock.download_requests() == []
    assert len(m._preflight_ok) == 0, "a failed check is never cached"
    if message:
        assert message in str(info.value)


# ====================================================================================== request shape


def test_preflight_request_is_token_less_and_comes_first(m, mock):
    _call(m, mock)
    (pre,) = mock.preflight_requests()
    assert pre.method == "POST"
    assert (pre.url.scheme, pre.url.host, pre.url.path) == ("https", TAU_HOST, PREFLIGHT_PATH)
    assert pre.url.params["info"] == "tool_mobile_get_public_config"
    assert pre.headers["content-type"].startswith("application/json")
    assert json.loads(pre.content) == [{"index": 0, "methodname": "tool_mobile_get_public_config", "args": {}}]
    assert FAKE_TOKEN.encode() not in pre.content and FAKE_TOKEN not in str(pre.url)
    assert "wstoken" not in pre.content.decode() and "wstoken" not in str(pre.url)
    assert all(FAKE_TOKEN not in v for v in pre.headers.values())
    assert mock.requests.index(pre) < mock.requests.index(mock.rest_requests()[0])


def test_ensure_site_identity_returns_none_and_sends_no_rest_request(m, mock):
    assert m.ensure_site_identity() is None
    assert len(mock.preflight_requests()) == 1 and mock.rest_requests() == []
    assert m._preflight_ok == {TAU_URL}


def test_preflight_runs_once_per_base(m, mock):
    for _ in range(3):
        _call(m, mock)
    assert len(mock.preflight_requests()) == 1 and len(mock.rest_requests()) == 3


def test_a_new_base_gets_its_own_preflight(m, mock, monkeypatch):
    _call(m, mock)
    monkeypatch.setenv("MOODLE_URL", "https://moodle.example.edu")
    mock.allowed_hosts.add("moodle.example.edu")
    mock.preflight = preflight_reply("https://moodle.example.edu")
    _call(m, mock)
    assert len(mock.preflight_requests()) == 2
    assert m._preflight_ok == {TAU_URL, "https://moodle.example.edu"}
    monkeypatch.delenv("MOODLE_URL")
    _call(m, mock)  # back on tau: still cached
    assert len(mock.preflight_requests()) == 2


def test_a_local_http_dev_server_can_be_used(m, mock, monkeypatch):
    monkeypatch.setenv("MOODLE_URL", "http://localhost:8000")
    mock.allowed_hosts = {"localhost"}
    mock.preflight = preflight_reply("http://localhost:8000")
    _call(m, mock)
    assert str(mock.rest_requests()[0].url) == "http://localhost:8000/webservice/rest/server.php"


# ===================================================================== accepted and refused answers


@pytest.mark.parametrize(
    "answer",
    [
        pytest.param(preflight_reply(TAU_URL), id="wwwroot"),
        pytest.param(preflight_reply(None, TAU_URL), id="httpswwwroot-only"),
        pytest.param(preflight_reply("http://moodle.tau.ac.il", TAU_URL), id="http-and-https"),
        pytest.param(preflight_reply("https://evil.example.com", TAU_URL), id="https-key-matches"),
        pytest.param(preflight_reply("https://MOODLE.TAU.AC.IL"), id="uppercase"),
        pytest.param(preflight_reply("https://moodle.tau.ac.il/"), id="trailing-slash"),
        pytest.param(preflight_reply("  https://moodle.tau.ac.il  "), id="padded"),
        pytest.param(preflight_reply(TAU_URL, sitename="x", extra="ignored"), id="extra-keys"),
    ],
)
def test_matching_host_is_accepted(m, mock, answer):
    mock.preflight = answer
    m.ensure_site_identity()
    assert m._preflight_ok == {TAU_URL}


@pytest.mark.parametrize(
    "answer",
    [
        pytest.param(preflight_reply("https://evil.example.com"), id="other-host"),
        pytest.param(preflight_reply("https://moodle.tau.ac.il.evil.com"), id="suffix-host"),
        pytest.param(preflight_reply("https://evil.com/moodle.tau.ac.il"), id="host-in-path"),
        pytest.param(preflight_reply("https://evil.com?x=moodle.tau.ac.il"), id="host-in-query"),
        pytest.param(preflight_reply("https://moodle.tau.ac.il@evil.com"), id="userinfo-trick"),
        pytest.param(preflight_reply("https://xmoodle.tau.ac.il"), id="prefix-host"),
        pytest.param(preflight_reply("https://other.tau.ac.il"), id="sibling-host"),
        pytest.param(preflight_reply("https://moodle.tau.ac.il."), id="trailing-dot-host"),
        pytest.param(preflight_reply("https://[::1"), id="unparsable-url"),
        pytest.param(preflight_reply(""), id="empty-wwwroot"),
        pytest.param(preflight_reply(None), id="no-wwwroot"),
        pytest.param([{"error": False, "data": None}], id="null-data"),
        pytest.param([{"error": False}], id="no-data"),
        pytest.param([{"error": False, "data": [TAU_URL]}], id="data-is-a-list"),
        pytest.param([{"data": {"wwwroot": TAU_URL}}], id="no-error-flag"),
        pytest.param([{"error": True, "data": {"wwwroot": TAU_URL}}], id="error-flag"),
        pytest.param([{"error": 1, "data": {"wwwroot": TAU_URL}}], id="error-flag-truthy"),
        pytest.param([{"error": True, "exception": {"message": "nope"}}], id="error-exception"),
        pytest.param([], id="empty-list"),
        pytest.param([None], id="null-item"),
        pytest.param(["moodle"], id="string-item"),
        pytest.param({"wwwroot": TAU_URL}, id="dict-not-list"),
        pytest.param("moodle", id="string"),
        pytest.param(None, id="null"),
        pytest.param([{"error": False, "data": {"wwwroot": 42, "httpswwwroot": ["x"]}}], id="wrong-types"),
        pytest.param([{"error": False, "data": {"wwwroot": {"host": TAU_HOST}}}], id="wwwroot-is-a-dict"),
    ],
)
def test_other_answers_refuse_the_token(m, mock, answer):
    mock.preflight = (lambda _r: httpx.Response(200, content=b"null")) if answer is None else answer
    _assert_refused_without_sending_the_token(m, mock)


def test_the_refusal_names_the_expected_host(m, mock):
    mock.preflight = preflight_reply("https://evil.example.com")
    _assert_refused_without_sending_the_token(m, mock, message=f"identify itself as {TAU_HOST}")


# ================================================================================ failures


@pytest.mark.parametrize(
    "respond",
    [
        pytest.param(lambda _r: httpx.Response(200, text="<html>not moodle</html>"), id="html"),
        pytest.param(lambda _r: httpx.Response(200, content=b""), id="empty"),
        pytest.param(lambda _r: httpx.Response(404, text="nope"), id="404"),
        pytest.param(lambda _r: httpx.Response(403, text="nope"), id="403"),
        pytest.param(lambda _r: httpx.Response(500, text="boom"), id="500"),
        pytest.param(lambda _r: httpx.Response(503, text="boom"), id="503"),
        pytest.param(lambda _r: httpx.Response(302, headers={"location": "https://evil.example.com/"}), id="redirect"),
        pytest.param(lambda _r: httpx.Response(301, headers={"location": f"{TAU_URL}/elsewhere"}), id="own-redirect"),
        pytest.param(lambda _r: httpx.ConnectError(f"no route {TAU_URL}", request=_request()), id="connect-error"),
        pytest.param(lambda _r: httpx.ReadTimeout(f"slow {TAU_URL}", request=_request()), id="timeout"),
        pytest.param(lambda _r: httpx.ConnectTimeout(f"slow {TAU_URL}", request=_request()), id="connect-timeout"),
        pytest.param(
            lambda _r: httpx.Response(200, stream=ChunkStream([b"[{"], error=httpx.ReadError(TAU_URL))), id="cut"
        ),
    ],
)
def test_a_failed_check_never_lets_the_token_out(m, mock, respond):
    mock.preflight = respond
    _assert_refused_without_sending_the_token(m, mock, message="could not verify the site")
    assert mock.foreign_requests() == []


def test_a_redirect_target_is_never_requested(m, mock):
    mock.preflight = httpx.Response(302, headers={"location": "https://evil.example.com/lib/ajax/service-nologin.php"})
    _assert_refused_without_sending_the_token(m, mock, message="HTTP 302")
    assert [r.url.host for r in mock.requests] == [TAU_HOST]


def test_a_failed_check_is_retried_on_the_next_call(m, mock):
    mock.preflight = preflight_reply("https://evil.example.com")
    with pytest.raises(m.MoodleError):
        _call(m, mock)
    mock.preflight = PREFLIGHT_OK
    assert _call(m, mock) == []
    assert len(mock.preflight_requests()) == 2


def _padded_reply(total_bytes: int) -> bytes:
    """A valid preflight answer that is exactly `total_bytes` long."""

    def build(pad: str) -> bytes:
        return json.dumps(preflight_reply(TAU_URL, pad=pad), separators=(",", ":")).encode()

    return build("x" * (total_bytes - len(build(""))))


def test_the_answer_may_be_exactly_64_kib(m, mock):
    body = _padded_reply(64 * 1024)
    assert len(body) == 64 * 1024
    mock.preflight = httpx.Response(200, content=body)
    m.ensure_site_identity()
    assert m._preflight_ok == {TAU_URL}


def test_one_byte_more_than_64_kib_is_refused(m, mock):
    body = _padded_reply(64 * 1024 + 1)
    assert len(body) == 64 * 1024 + 1
    mock.preflight = httpx.Response(200, content=body)
    _assert_refused_without_sending_the_token(m, mock, message="too large")


def test_an_endless_answer_is_cut_off_early(m, mock):
    stream = ChunkStream((b"x" * 1024 for _ in range(10_000)))
    mock.preflight = lambda _r: httpx.Response(200, stream=stream)
    _assert_refused_without_sending_the_token(m, mock, message="too large")
    assert stream.produced <= 70, "the body must not be read to the end"


# ============================================================================ MOODLE_SKIP_PREFLIGHT


@pytest.mark.parametrize("value", ["1", " 1 ", "1\n"])
def test_skip_preflight_makes_no_identity_request(m, mock, monkeypatch, capsys, value):
    monkeypatch.setenv("MOODLE_SKIP_PREFLIGHT", value)
    mock.preflight = preflight_reply("https://evil.example.com")  # would be refused if it were asked
    assert _call(m, mock) == []
    assert mock.preflight_requests() == []
    assert m._preflight_ok == set()


@pytest.mark.parametrize("value", ["", "0", "true", "yes", "2", "on", "11"])
def test_only_the_exact_value_1_skips_the_preflight(m, mock, monkeypatch, value):
    monkeypatch.setenv("MOODLE_SKIP_PREFLIGHT", value)
    mock.preflight = preflight_reply("https://evil.example.com")
    _assert_refused_without_sending_the_token(m, mock)
    assert len(mock.preflight_requests()) == 1


def test_skipping_warns_once_per_process_on_stderr_only(m, mock, monkeypatch, capsys):
    monkeypatch.setenv("MOODLE_SKIP_PREFLIGHT", "1")
    _call(m, mock)
    _call(m, mock)
    monkeypatch.setenv("MOODLE_URL", "https://moodle.example.edu")  # even another site: still one warning
    mock.allowed_hosts.add("moodle.example.edu")
    _call(m, mock)
    out, err = capsys.readouterr()
    assert out == ""
    assert err.count("WARNING") == 1 and "MOODLE_SKIP_PREFLIGHT=1" in err
    assert FAKE_TOKEN not in err


def test_a_normal_run_prints_nothing(m, mock, capsys):
    _call(m, mock)
    assert capsys.readouterr() == ("", "")


# ===================================================================== the public config function


def test_public_config_is_answered_by_the_tokenless_endpoint(m, mock):
    # an impostor gets no token either
    mock.preflight = [{"error": False, "data": {"wwwroot": "https://evil.example.com", "sitename": "x"}}]
    assert m.call("tool_mobile_get_public_config") == {"wwwroot": "https://evil.example.com", "sitename": "x"}
    assert mock.rest_requests() == [] and len(mock.preflight_requests()) == 1
    for request in mock.requests:
        assert FAKE_TOKEN.encode() not in request.content
        assert "token" not in request.url.query.decode().lower().replace("tool_mobile", "")
        assert "wstoken" not in request.content.decode()


def test_public_config_works_without_a_token_and_ignores_params(m, mock, monkeypatch):
    monkeypatch.delenv("MOODLE_TOKEN")
    assert m.call("tool_mobile_get_public_config", ignored=1)["wwwroot"] == TAU_URL
    assert m._preflight_ok == set(), "reading the public config is not an identity check"


def test_public_config_still_rejects_reserved_params_before_any_request(m, mock):
    with pytest.raises(ValueError):
        m.call("tool_mobile_get_public_config", wstoken="x")
    assert mock.requests == []


@pytest.mark.parametrize(
    ("answer", "text"),
    [
        (httpx.Response(200, text="<html>"), "non-JSON"),
        (httpx.Response(500, text="x"), "HTTP 500"),
        ([{"error": True}], "did not answer like a Moodle site"),
        ([], "did not answer like a Moodle site"),
    ],
)
def test_public_config_failures_are_moodle_errors(m, mock, answer, text):
    mock.preflight = answer
    with pytest.raises(m.MoodleError, match=text):
        m.call("tool_mobile_get_public_config")
