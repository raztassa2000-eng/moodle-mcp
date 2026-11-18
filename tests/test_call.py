"""call(): the REST request shape, parameter flattening, error mapping and "no leaks" guarantees."""

from __future__ import annotations

from urllib.parse import urlsplit

import httpx
import pytest

import moodle_mcp
from support import (
    FAKE_TOKEN,
    PREFLIGHT_PATH,
    REST_PATH,
    TAU_URL,
    ChunkStream,
    assert_no_leak,
    assert_no_token_fragment,
    form,
    preflight_reply,
)

FN = "core_course_get_contents"
LEAKY = f"{TAU_URL}{REST_PATH}?wstoken={FAKE_TOKEN}"


def _request() -> httpx.Request:
    return httpx.Request("POST", TAU_URL)


# ===================================================================================== request shape


def test_call_posts_the_token_in_the_body_only(m, mock):
    mock.on(FN, [])
    assert m.call(FN, courseid=7) == []
    (request,) = mock.rest_requests()
    assert request.method == "POST"
    assert str(request.url) == f"{TAU_URL}{REST_PATH}"
    assert request.url.query == b""
    assert request.headers["content-type"].startswith("application/x-www-form-urlencoded")
    assert form(request) == {
        "wstoken": FAKE_TOKEN,
        "wsfunction": FN,
        "moodlewsrestformat": "json",
        "courseid": "7",
    }
    for r in mock.requests:
        assert FAKE_TOKEN not in str(r.url)
        assert all(FAKE_TOKEN not in v for v in r.headers.values())
    for r in mock.preflight_requests():
        assert FAKE_TOKEN.encode() not in r.content


def test_call_without_params_sends_just_the_three_fields(m, mock):
    mock.on(FN, {})
    m.call(FN)
    assert set(mock.forms_for(FN)[0]) == {"wstoken", "wsfunction", "moodlewsrestformat"}


def test_call_never_follows_redirects_even_if_the_client_would(m, mock):
    mock.on(FN, httpx.Response(302, headers={"location": "https://evil.example.com/steal"}))
    with pytest.raises(m.MoodleError, match="HTTP 302"):
        m.call(FN)
    assert mock.foreign_requests() == []
    assert len(mock.rest_requests()) == 1


@pytest.mark.parametrize("key", list(moodle_mcp.SITES))
def test_every_preset_gets_its_rest_and_preflight_urls(m, mock, monkeypatch, key):
    url = moodle_mcp.SITES[key][1]
    host = urlsplit(url).hostname
    monkeypatch.setenv("MOODLE_SITE", key)
    mock.allowed_hosts = {host}
    mock.preflight = preflight_reply(url)
    mock.on(FN, [])
    m.call(FN, courseid=1)
    (pre,) = mock.preflight_requests()
    (rest,) = mock.rest_requests()
    assert str(rest.url) == f"{url}{REST_PATH}"
    assert str(pre.url) == f"{url}{PREFLIGHT_PATH}?info=tool_mobile_get_public_config"
    assert FAKE_TOKEN.encode() in rest.content and FAKE_TOKEN.encode() not in pre.content


# ====================================================================================== parameters


def test_params_are_flattened_the_way_moodle_expects(m, mock):
    mock.on(FN, [])
    m.call(
        FN,
        courseid=3,
        options=[{"name": "excludemodules", "value": True}, {"name": "x", "value": None}],
        ids=[5, 6],
        events={"courseids": [1, 2]},
        flag=False,
        ratio=1.5,
        title="שלום & more",
        skipped=None,
    )
    (fields,) = mock.forms_for(FN)
    for key, value in {
        "courseid": "3",
        "ids[0]": "5",
        "ids[1]": "6",
        "options[0][name]": "excludemodules",
        "options[0][value]": "1",
        "options[1][name]": "x",
        "events[courseids][0]": "1",
        "events[courseids][1]": "2",
        "flag": "0",
        "ratio": "1.5",
        "title": "שלום & more",
    }.items():
        assert fields[key] == value, key
    assert "options[1][value]" not in fields and "skipped" not in fields


def test_flatten_directly(m):
    assert m._flatten({"courseids": [1, 2]}) == {"courseids[0]": 1, "courseids[1]": 2}
    assert m._flatten({"a": [{"k": "v", "n": [3]}]}) == {"a[0][k]": "v", "a[0][n][0]": 3}
    assert m._flatten({"t": (7, 8)}) == {"t[0]": 7, "t[1]": 8}
    assert m._flatten({"s": "text", "n": 1.5, "z": 0}) == {"s": "text", "n": 1.5, "z": 0}
    assert m._flatten({"yes": True, "no": False, "none": None}) == {"yes": 1, "no": 0}
    assert m._flatten({"l": [True, None, 2]}) == {"l[0]": 1, "l[2]": 2}
    assert m._flatten({}) == {} and m._flatten({"a": [], "b": {}}) == {}


@pytest.mark.parametrize("name", ["wstoken", "wsfunction", "moodlewsrestformat"])
@pytest.mark.parametrize("value", ["evil", 1, None, ["x"], {"a": 1}])
def test_params_cannot_override_the_reserved_fields(m, mock, name, value):
    with pytest.raises(ValueError, match="cannot be passed"):
        m.call(FN, **{name: value})
    assert mock.requests == [], "nothing may be sent (not even the preflight)"


def test_param_names_and_values_cannot_smuggle_extra_fields(m, mock):
    mock.on(FN, [])
    m.call(FN, **{"courseid&wstoken=evil": 1, "plain": "1&wstoken=evil&wsfunction=mod_assign_save_submission"})
    (fields,) = mock.forms_for(FN)
    assert fields["wstoken"] == FAKE_TOKEN and fields["wsfunction"] == FN
    assert fields["courseid&wstoken=evil"] == "1"
    assert fields["plain"] == "1&wstoken=evil&wsfunction=mod_assign_save_submission"
    assert len(fields) == 5


def test_nested_dict_key_named_like_a_reserved_field_is_only_a_nested_field(m, mock):
    mock.on(FN, [])
    m.call(FN, options={"wstoken": "evil"})
    (fields,) = mock.forms_for(FN)
    assert fields["wstoken"] == FAKE_TOKEN and fields["options[wstoken]"] == "evil"


# ======================================================================== nothing is sent without config


def test_missing_token_fails_before_any_request(m, mock, monkeypatch):
    monkeypatch.delenv("MOODLE_TOKEN")
    with pytest.raises(m.ConfigError, match="No Moodle token"):
        m.call(FN)
    assert mock.requests == []


def test_bad_token_format_fails_before_any_request(m, mock, monkeypatch):
    monkeypatch.setenv("MOODLE_TOKEN", "not-a-token")
    with pytest.raises(m.ConfigError):
        m.call(FN)
    assert mock.requests == []


@pytest.mark.parametrize("url", ["http://moodle.example.edu", "https://u:p@moodle.example.edu", "junk"])
def test_invalid_moodle_url_fails_before_any_request(m, mock, monkeypatch, url):
    monkeypatch.setenv("MOODLE_URL", url)
    with pytest.raises(m.ConfigError):
        m.call(FN)
    assert mock.requests == []


def test_unknown_site_fails_before_any_request(m, mock, monkeypatch):
    monkeypatch.setenv("MOODLE_SITE", "bogus")
    with pytest.raises(m.ConfigError):
        m.call(FN)
    assert mock.requests == []


# ======================================================================================= Moodle errors


def _exception(code, message="raw moodle message"):
    return {"exception": "moodle_exception", "errorcode": code, "message": message}


def _error(code, message="raw moodle message"):
    return {"error": message, "errorcode": code}


@pytest.mark.parametrize("shape", [_exception, _error], ids=["exception", "error"])
@pytest.mark.parametrize("code", ["invalidtoken", "accessexception", "somethingweird"])
def test_moodle_error_replies_become_moodle_errors(m, mock, shape, code):
    mock.on(FN, shape(code))
    with pytest.raises(m.MoodleError) as info:
        m.call(FN, courseid=1)
    err = info.value
    assert err.errorcode == code and isinstance(err, RuntimeError)
    assert code in str(err)
    assert_no_leak(str(err))
    if code == "somethingweird":
        assert str(err) == f"{code}: raw moodle message"
    else:
        assert "raw moodle message" not in str(err), "friendly text replaces the raw message"


def test_the_two_friendly_codes_have_different_texts(m, mock):
    texts = {}
    for code in ("invalidtoken", "accessexception"):
        mock.on(FN, _exception(code))
        with pytest.raises(m.MoodleError) as info:
            m.call(FN)
        texts[code] = str(info.value)
    assert texts["invalidtoken"] != texts["accessexception"]
    assert "new token" in texts["invalidtoken"].lower()
    assert "mobile web service" in texts["accessexception"]


@pytest.mark.parametrize(
    ("reply", "code"),
    [
        ({"exception": "moodle_exception"}, None),
        ({"exception": "x", "errorcode": 5, "message": "m"}, None),
        ({"exception": "x", "errorcode": ["a"], "message": "m"}, None),
        ({"errorcode": None, "error": "m"}, None),
        ({"exception": "x", "errorcode": "c"}, "c"),
    ],
)
def test_odd_error_shapes_still_become_a_moodle_error(m, mock, reply, code):
    mock.on(FN, reply)
    with pytest.raises(m.MoodleError) as info:
        m.call(FN)
    assert info.value.errorcode == code and str(info.value).strip()


@pytest.mark.parametrize("shape", [_exception, _error], ids=["exception", "error"])
def test_error_messages_are_scrubbed(m, mock, shape):
    mock.on(FN, shape("odd", f"bad {FAKE_TOKEN} and wstoken=XYZ123456789 and token=QRS987654321"))
    with pytest.raises(m.MoodleError) as info:
        m.call(FN)
    text = f"{info.value} {info.value!r} {info.value.args}"
    assert FAKE_TOKEN not in text and "XYZ123456789" not in text and "QRS987654321" not in text


def test_long_error_messages_are_cut_to_300_characters(m, mock):
    mock.on(FN, _exception("big", "x" * 5000))
    with pytest.raises(m.MoodleError) as info:
        m.call(FN)
    assert len(str(info.value)) == 300


@pytest.mark.parametrize("start", [250, 268, 270, 280, 290, 295, 297, 298, 299, 300, 305])
def test_the_token_is_masked_before_the_message_is_cut(m, mock, start):
    """If the cut came first, a token straddling the 300th character would leak its first characters."""
    prefix = "oops: "  # f"{code}: " for code "oops"
    message = "A" * (start - len(prefix)) + FAKE_TOKEN + " the rest"
    mock.on(FN, _exception("oops", message))
    with pytest.raises(m.MoodleError) as info:
        m.call(FN)
    assert len(str(info.value)) <= 300
    assert_no_token_fragment(str(info.value), size=6)


@pytest.mark.parametrize("start", [280, 290, 295, 299])
def test_a_token_parameter_is_masked_before_the_message_is_cut(m, mock, start):
    prefix = "oops: "
    message = "A" * (start - len(prefix)) + "wstoken=SECRETVALUE0123456789"
    mock.on(FN, _exception("oops", message))
    with pytest.raises(m.MoodleError) as info:
        m.call(FN)
    assert "SECRET" not in str(info.value)


# ==================================================================== HTTP and network failures


@pytest.mark.parametrize("status", [301, 302, 303, 307, 308, 400, 401, 403, 404, 408, 429, 500, 502, 503, 504])
def test_http_error_status_becomes_a_moodle_error_without_url_or_token(m, mock, status):
    mock.on(FN, httpx.Response(status, text=f"error page mentioning wstoken={FAKE_TOKEN} {LEAKY}"))
    with pytest.raises(m.MoodleError) as info:
        m.call(FN, courseid=1)
    assert str(info.value) == f"Moodle answered HTTP {status}."
    assert_no_leak(str(info.value))


NETWORK_ERRORS = [
    (httpx.ConnectError, False),
    (httpx.ConnectTimeout, True),
    (httpx.ReadTimeout, True),
    (httpx.WriteTimeout, True),
    (httpx.PoolTimeout, True),
    (httpx.ReadError, False),
    (httpx.WriteError, False),
    (httpx.RemoteProtocolError, False),
    (httpx.LocalProtocolError, False),
    (httpx.ProxyError, False),
    (httpx.UnsupportedProtocol, False),
    (httpx.TooManyRedirects, False),
    (httpx.DecodingError, False),
    (httpx.NetworkError, False),
]


@pytest.mark.parametrize(("exc", "is_timeout"), NETWORK_ERRORS, ids=[e.__name__ for e, _ in NETWORK_ERRORS])
def test_network_errors_become_short_moodle_errors(m, mock, exc, is_timeout):
    mock.on(FN, lambda _form: exc(f"boom talking to {LEAKY}", request=_request()))
    with pytest.raises(m.MoodleError) as info:
        m.call(FN, courseid=1)
    err = info.value
    text = f"{err} {err!r} {err.args}"
    assert_no_leak(text)
    assert ("timeout" in str(err)) == is_timeout
    if not is_timeout:
        assert exc.__name__ in str(err)
    assert err.__cause__ is None and err.__suppress_context__, "the httpx error (with the URL) must not be chained"


def test_a_failure_in_the_middle_of_the_body_is_a_moodle_error(m, mock):
    stream = ChunkStream([b"[1, 2,"], error=httpx.ReadError(f"connection lost {LEAKY}"))
    mock.on(FN, lambda _form: httpx.Response(200, stream=stream))
    with pytest.raises(m.MoodleError, match="ReadError") as info:
        m.call(FN)
    assert_no_leak(str(info.value))


def test_a_corrupt_compressed_body_is_a_moodle_error(m, mock):
    stream = ChunkStream([b"this is not gzip"])
    mock.on(FN, lambda _form: httpx.Response(200, headers={"content-encoding": "gzip"}, stream=stream))
    with pytest.raises(m.MoodleError, match="DecodingError"):
        m.call(FN)


# ======================================================================================== bad bodies

NOT_JSON = [
    pytest.param(b"<html>Please log in</html>", id="html"),
    pytest.param(b"", id="empty"),
    pytest.param(b"   \n", id="blank"),
    pytest.param(b'{"courses": [', id="truncated"),
    pytest.param(b"\xff\xff\xff", id="not-utf8"),
    pytest.param(b"{'single': 'quotes'}", id="single-quotes"),
    pytest.param(b"[" * 300_000, id="absurdly-deep-nesting"),
    pytest.param(f"wstoken={FAKE_TOKEN}".encode(), id="echoes-the-token"),
]


@pytest.mark.parametrize("body", NOT_JSON)
def test_non_json_reply_is_a_moodle_error(m, mock, body):
    mock.on(FN, httpx.Response(200, content=body))
    with pytest.raises(m.MoodleError, match="non-JSON") as info:
        m.call(FN)
    assert_no_leak(str(info.value))
    assert info.value.errorcode is None


@pytest.mark.parametrize(
    "payload",
    [
        [],
        [{"id": 1}],
        {"courses": [], "warnings": [{"item": "x", "warningcode": "1", "message": "m"}]},
        {"exception": None, "courses": []},
        {"exception": "", "warnings": []},
        0,
        "text",
        True,
    ],
)
def test_valid_non_error_payloads_come_back_untouched(m, mock, payload):
    mock.on(FN, payload)
    assert m.call(FN) == payload


def test_a_json_null_reply_comes_back_as_none(m, mock):
    mock.on(FN, httpx.Response(200, content=b"null"))
    assert m.call(FN) is None


# ==================================================================================== userid()


def test_userid_is_looked_up_once_and_cached(m, mock):
    assert m.userid() == 42 and m.userid() == 42
    assert mock.functions_called() == ["core_webservice_get_site_info"]


def test_userid_cache_can_be_reset(m, mock):
    assert m.userid() == 42
    m._userid = None
    mock.on("core_webservice_get_site_info", {"userid": 77})
    assert m.userid() == 77


def test_userid_propagates_an_invalid_token(m, mock):
    mock.on("core_webservice_get_site_info", _exception("invalidtoken"))
    with pytest.raises(m.MoodleError) as info:
        m.userid()
    assert info.value.errorcode == "invalidtoken" and m._userid is None


# ==================================================================================== fmt_time


def test_fmt_time(m):
    import datetime as dt

    ts = 1_700_000_000
    assert m.fmt_time(ts) == dt.datetime.fromtimestamp(ts).strftime("%d/%m/%Y %H:%M")
    assert m.fmt_time(0) is None and m.fmt_time(None) is None
