"""download_file / _fetch: URL and name safety, the POST-body token, replacement of old copies and failures."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from support import (
    FAKE_TOKEN,
    PLUGINFILE,
    TAU_HOST,
    TAU_URL,
    ChunkStream,
    assert_inside,
    assert_no_leak,
    files_under,
    form,
    preflight_reply,
)

JSON_TYPE = {"content-type": "application/json"}


def _url(name: str = "file.pdf") -> str:
    return f"{TAU_URL}{PLUGINFILE}123/mod_resource/content/1/{name}"


def _leaky() -> str:
    return f"{_url()}?token={FAKE_TOKEN}"


def _nothing_happened(mock, dl_dir: Path) -> None:
    assert mock.requests == [], "no request of any kind may be made"
    assert not dl_dir.exists(), "not even the download directory may be created"


# ======================================================================================= happy path


def test_download_saves_the_file_inside_the_download_dir(m, mock, dl_dir):
    path = Path(m.download_file(_url()))
    assert path.read_bytes() == b"%PDF-fake"
    assert path.name == "file.pdf"
    assert path.parent == dl_dir.resolve()
    assert files_under(dl_dir) == [path], "no .part file may be left behind"
    assert isinstance(m.download_file(_url()), str)


def test_download_sends_the_token_in_the_post_body_only(m, mock):
    m.download_file(_url() + "?forcedownload=1")
    (request,) = mock.download_requests()
    assert request.method == "POST"
    assert FAKE_TOKEN not in str(request.url)
    assert request.url.query == b"forcedownload=1", "the original query is kept, nothing is added"
    assert form(request) == {"token": FAKE_TOKEN}
    assert all(FAKE_TOKEN not in v for v in request.headers.values())
    assert mock.rest_requests() == []


def test_download_runs_the_tokenless_identity_check_first_and_only_once(m, mock):
    m.download_file(_url("a.pdf"))
    m.download_file(_url("b.pdf"))
    assert [r.url.path.rsplit("/", 1)[-1] for r in mock.requests] == ["service-nologin.php", "a.pdf", "b.pdf"]
    assert FAKE_TOKEN.encode() not in mock.preflight_requests()[0].content


def test_a_failed_identity_check_means_no_download_request_and_no_folder(m, mock, dl_dir):
    mock.preflight = preflight_reply("https://evil.example.com")
    with pytest.raises(m.MoodleError, match="Refusing to send your token"):
        m.download_file(_url())
    assert mock.download_requests() == []
    assert not dl_dir.exists()


def test_skip_preflight_applies_to_downloads_too(m, mock, monkeypatch, capsys):
    monkeypatch.setenv("MOODLE_SKIP_PREFLIGHT", "1")
    mock.preflight = preflight_reply("https://evil.example.com")
    m.download_file(_url())
    assert mock.preflight_requests() == [] and len(mock.download_requests()) == 1
    assert "WARNING" in capsys.readouterr().err


def test_subfolders_are_created_inside_the_download_dir(m, mock, dl_dir):
    path = Path(m.download_file(_url(), "CS101/Week 1"))
    assert path == (dl_dir / "CS101" / "Week 1" / "file.pdf").resolve()
    assert_inside(path, dl_dir)


@pytest.mark.parametrize(
    ("subfolder", "expected"),
    [
        ("שיעורים/שבוע 1", ("שיעורים", "שבוע 1")),
        ("a//b/", ("a", "b")),
        ("a/./b", ("a", "b")),
        ("a/x:y/CON", ("a", "x y", "_CON")),
        ("~/x", ("~", "x")),
        ("  ", ()),
    ],
)
def test_odd_but_harmless_subfolders_stay_inside(m, mock, dl_dir, subfolder, expected):
    path = Path(m.download_file(_url(), subfolder))
    assert path == dl_dir.resolve().joinpath(*expected, "file.pdf")


def test_downloading_again_replaces_the_old_copy(m, mock, dl_dir):
    versions = iter([b"version 1", b"v2", b"version three"])
    mock.download = lambda _r: httpx.Response(200, content=next(versions))
    first = Path(m.download_file(_url()))
    second = Path(m.download_file(_url()))
    third = Path(m.download_file(_url()))
    assert first == second == third
    assert third.read_bytes() == b"version three"
    assert files_under(dl_dir) == [first], "replaced in place, no 'file (1).pdf', no .part"


def test_an_empty_file_is_saved(m, mock):
    mock.download = lambda _r: httpx.Response(200, content=b"")
    path = Path(m.download_file(_url("empty.txt")))
    assert path.read_bytes() == b""


def test_a_large_file_arrives_intact_in_many_chunks(m, mock):
    chunks = [bytes([i]) * 65536 for i in range(40)]  # 2.5 MiB
    mock.download = lambda _r: httpx.Response(200, stream=ChunkStream(iter(chunks)))
    path = Path(m.download_file(_url("big.bin")))
    assert path.read_bytes() == b"".join(chunks)


# ========================================================================== names from the URL


@pytest.mark.parametrize(
    ("url_name", "expected"),
    [
        ("My%20File%20%281%29.pdf", "My File (1).pdf"),
        ("%D7%A9%D7%9C%D7%95%D7%9D.pdf", "שלום.pdf"),
        ("a%2520b.pdf", "a%20b.pdf"),  # decoded exactly once
        ("G%3Aplanted.txt", "G planted.txt"),
        ("a%3Fb%2Ac.txt", "a b c.txt"),
        ("name.txt%3AZone.Identifier", "name.txt Zone.Identifier"),
        ("CON.txt", "_CON.txt"),
        ("con", "_con"),
        ("Nul.tar.gz", "_Nul.tar.gz"),
        ("com1.pdf", "_com1.pdf"),
        ("%E2%80%AEgnp.exe", "gnp.exe"),
        (".hidden", "hidden"),
        ("file.", "file"),
        ("...pdf", "pdf"),
        ("a..b.pdf", "a..b.pdf"),
    ],
)
def test_the_file_name_from_the_url_goes_through_safe_filename(m, mock, dl_dir, url_name, expected):
    path = Path(m.download_file(_url(url_name) + "?forcedownload=1"))
    assert path.name == expected
    assert path.parent == dl_dir.resolve()


def test_a_very_long_name_is_cut_but_keeps_its_extension(m, mock, dl_dir):
    path = Path(m.download_file(_url("x" * 400 + ".pdf")))
    assert len(path.name) <= 150 and path.name.endswith(".pdf")


@pytest.mark.parametrize("tail", ["", "%20", "%20%20", ".", "..", "%2e", "%2e%2e", " ", "/"])
def test_a_url_without_a_usable_file_name_is_rejected_before_any_request(m, mock, dl_dir, tail):
    with pytest.raises(ValueError):
        m.download_file(f"{TAU_URL}{PLUGINFILE}123/mod_resource/content/1/{tail}")
    _nothing_happened(mock, dl_dir)


BAD_URLS = [
    pytest.param(f"https://evil.example.com{PLUGINFILE}1/a.pdf", id="other-host"),
    pytest.param(f"https://{TAU_HOST}.evil.example.com{PLUGINFILE}1/a.pdf", id="lookalike-host"),
    pytest.param(f"https://{TAU_HOST}@evil.example.com{PLUGINFILE}1/a.pdf", id="userinfo-trick"),
    pytest.param(f"https://evil.example.com@{TAU_HOST}{PLUGINFILE}1/a.pdf", id="reverse-userinfo-trick"),
    pytest.param(f"https://user@{TAU_HOST}{PLUGINFILE}1/a.pdf", id="userinfo"),
    pytest.param(f"http://{TAU_HOST}{PLUGINFILE}1/a.pdf", id="http-downgrade"),
    pytest.param(f"{TAU_URL}:8443{PLUGINFILE}1/a.pdf", id="other-port"),
    pytest.param(f"{TAU_URL}/pluginfile.php/1/a.pdf", id="legacy-path"),
    pytest.param(f"{TAU_URL}/webservice/rest/server.php?x=1&name=a.pdf", id="rest-endpoint"),
    pytest.param(f"{TAU_URL}/mod/resource/view.php?id=5", id="view-page"),
    pytest.param(f"{TAU_URL}{PLUGINFILE}1/%2e%2e/%2e%2e/a.pdf", id="encoded-dotdot"),
    pytest.param(f"{TAU_URL}{PLUGINFILE}1/../../a.pdf", id="dotdot"),
    pytest.param(f"{TAU_URL}{PLUGINFILE}1%2fa.pdf", id="encoded-slash"),
    pytest.param(f"{TAU_URL}{PLUGINFILE}1%5ca.pdf", id="encoded-backslash"),
    pytest.param(f"{TAU_URL}{PLUGINFILE}1\\a.pdf", id="backslash"),
    pytest.param(f"{TAU_URL}{PLUGINFILE}1/a.pdf#x", id="fragment"),
    pytest.param(f"{TAU_URL}{PLUGINFILE}1/a%00.pdf", id="encoded-nul"),
    pytest.param(f"{TAU_URL}{PLUGINFILE}1/a\n.pdf", id="newline"),
    pytest.param("/webservice/pluginfile.php/1/a.pdf", id="relative"),
    pytest.param("", id="empty"),
]


@pytest.mark.parametrize("url", BAD_URLS)
def test_bad_urls_are_rejected_before_any_request_so_no_token_can_leave(m, mock, dl_dir, url):
    with pytest.raises(ValueError) as info:
        m.download_file(url)
    assert FAKE_TOKEN not in str(info.value) and "evil" not in str(info.value)
    _nothing_happened(mock, dl_dir)


@pytest.mark.parametrize("url", BAD_URLS)
def test_fetch_itself_rejects_bad_urls_too(m, mock, dl_dir, url):
    with pytest.raises(ValueError):
        m._fetch(url, dl_dir / "x.pdf")
    _nothing_happened(mock, dl_dir)


@pytest.mark.parametrize(
    "subfolder",
    ["../escape", "a/../../escape", "/abs", "\\abs", "C:\\x", "C:/x", "c:x", "..", "//srv/share", "a/ .. /b", "~/../x"],
)
def test_subfolder_escapes_are_rejected_before_any_request(m, mock, dl_dir, tmp_path, subfolder):
    with pytest.raises(ValueError):
        m.download_file(_url(), subfolder)
    _nothing_happened(mock, dl_dir)
    assert not (tmp_path / "escape").exists()


@pytest.mark.parametrize("subfolder", [5, ["a"], b"a", True])
def test_a_non_text_subfolder_is_rejected(m, mock, dl_dir, subfolder):
    with pytest.raises(ValueError):
        m.download_file(_url(), subfolder)
    _nothing_happened(mock, dl_dir)


# ============================================================================= configuration errors


def test_a_missing_token_is_a_config_error_and_nothing_is_sent(m, mock, dl_dir, monkeypatch):
    monkeypatch.delenv("MOODLE_TOKEN")
    with pytest.raises(m.ConfigError, match="No Moodle token"):
        m.download_file(_url())
    _nothing_happened(mock, dl_dir)


def test_a_junk_size_limit_is_a_config_error_and_nothing_is_sent(m, mock, dl_dir, monkeypatch):
    monkeypatch.setenv("MOODLE_MAX_DOWNLOAD_MB", "lots")
    with pytest.raises(m.ConfigError, match="MOODLE_MAX_DOWNLOAD_MB"):
        m.download_file(_url())
    _nothing_happened(mock, dl_dir)


def test_an_unusable_moodle_url_is_a_config_error_and_nothing_is_sent(m, mock, dl_dir, monkeypatch):
    monkeypatch.setenv("MOODLE_URL", "http://moodle.example.edu")
    with pytest.raises(m.ConfigError):
        m.download_file(_url())
    _nothing_happened(mock, dl_dir)


# ===================================================================================== failures


@pytest.mark.parametrize("status", [301, 302, 303, 307, 308, 400, 401, 403, 404, 410, 429, 500, 502, 503])
def test_http_errors_and_redirects_are_moodle_errors_without_url_or_token(m, mock, dl_dir, status):
    mock.download = lambda _r: httpx.Response(
        status, headers={"location": f"https://evil.example.com/steal?token={FAKE_TOKEN}"}, text=_leaky()
    )
    with pytest.raises(m.MoodleError) as info:
        m.download_file(_url())
    assert str(info.value) == f"Moodle answered HTTP {status} for this file"
    assert_no_leak(str(info.value))
    assert mock.foreign_requests() == [], "a redirect must never be followed"
    assert files_under(dl_dir) == []


NETWORK_ERRORS = [
    (httpx.ConnectError, "Download failed: ConnectError"),
    (httpx.ReadError, "Download failed: ReadError"),
    (httpx.RemoteProtocolError, "Download failed: RemoteProtocolError"),
    (httpx.ProxyError, "Download failed: ProxyError"),
    (httpx.TooManyRedirects, "Download failed: TooManyRedirects"),
    (httpx.ConnectTimeout, "Download timed out"),
    (httpx.ReadTimeout, "Download timed out"),
    (httpx.WriteTimeout, "Download timed out"),
    (httpx.PoolTimeout, "Download timed out"),
]


@pytest.mark.parametrize(("exc", "message"), NETWORK_ERRORS, ids=[e.__name__ for e, _ in NETWORK_ERRORS])
def test_network_errors_become_short_messages_without_the_url(m, mock, dl_dir, exc, message):
    mock.download = lambda r: exc(f"failed {r.url} {_leaky()}", request=httpx.Request("POST", _url()))
    with pytest.raises(m.MoodleError) as info:
        m.download_file(_url())
    assert str(info.value) == message
    assert_no_leak(f"{info.value} {info.value!r} {info.value.args}")
    assert info.value.__cause__ is None and info.value.__suppress_context__
    assert files_under(dl_dir) == []


def test_a_connection_lost_half_way_leaves_no_partial_file(m, mock, dl_dir):
    stream = ChunkStream([b"x" * 1000, b"y" * 1000], error=httpx.ReadError(f"lost {_leaky()}"))
    mock.download = lambda _r: httpx.Response(200, stream=stream)
    with pytest.raises(m.MoodleError, match="Download failed: ReadError") as info:
        m.download_file(_url())
    assert_no_leak(str(info.value))
    assert files_under(dl_dir) == []


# ====================================================================== JSON replies are errors


@pytest.mark.parametrize(
    ("payload", "code", "text"),
    [
        (
            {"exception": "moodle_exception", "errorcode": "invalidtoken", "message": "Invalid token"},
            "invalidtoken",
            "new token",
        ),
        ({"error": "Access control exception", "errorcode": "accessexception"}, "accessexception", "denied access"),
        ({"exception": "moodle_exception", "errorcode": "weird", "message": "it broke"}, "weird", "weird: it broke"),
        ({"error": "something went wrong"}, None, "Moodle refused the download: something went wrong"),
    ],
    ids=["invalidtoken", "accessexception", "other-exception", "error-string-only"],
)
@pytest.mark.parametrize("ctype", ["application/json", "application/json; charset=utf-8"])
def test_a_moodle_error_reply_is_an_error_not_a_file(m, mock, dl_dir, payload, code, text, ctype):
    mock.download = lambda _r: httpx.Response(200, json=payload, headers={"content-type": ctype})
    with pytest.raises(m.MoodleError) as info:
        m.download_file(_url("report.pdf"))
    assert info.value.errorcode == code
    assert text in str(info.value)
    assert_no_leak(str(info.value))
    assert files_under(dl_dir) == []


def test_error_text_in_a_json_reply_is_scrubbed(m, mock):
    payload = {"error": f"bad token {FAKE_TOKEN} wstoken=ZZZ999888777", "errorcode": "weird"}
    mock.download = lambda _r: httpx.Response(200, json=payload)
    with pytest.raises(m.MoodleError) as info:
        m.download_file(_url())
    assert FAKE_TOKEN not in str(info.value) and "ZZZ999888777" not in str(info.value)


@pytest.mark.parametrize("start", [150, 170, 180, 190, 195, 199])
def test_the_token_is_masked_before_the_short_refusal_text_is_cut(m, mock, start):
    prefix = "Moodle refused the download: "
    payload = {"error": "A" * (start - len(prefix)) + FAKE_TOKEN + " tail"}
    mock.download = lambda _r: httpx.Response(200, json=payload)
    with pytest.raises(m.MoodleError) as info:
        m.download_file(_url())
    assert len(str(info.value)) <= 200
    for i in range(len(FAKE_TOKEN) - 5):
        assert FAKE_TOKEN[i : i + 6] not in str(info.value)


@pytest.mark.parametrize(
    "body",
    [b"", b"not json at all", b'{"error": ', b"[" * 5000, b'{"x":"' + b"a" * 200_000 + b'"}'],
    ids=["empty", "garbage", "truncated", "deep", "over-64k"],
)
def test_a_json_typed_reply_that_cannot_be_read_as_a_moodle_error_is_saved_as_a_file(m, mock, dl_dir, body):
    mock.download = lambda _r: httpx.Response(200, content=body, headers=JSON_TYPE)
    path = Path(m.download_file(_url("data.json")))
    assert path.name == "data.json" and path.read_bytes() == body
    assert files_under(dl_dir) == [path], "the .part file is gone"


@pytest.mark.parametrize(
    "body",
    [
        b'{"course": "alg", "items": [1, 2, 3]}',
        b"[1, 2, 3]",
        b'[{"error": "x", "errorcode": "y"}]',
        b'{"results": [{"error": "none"}], "errorcount": 0}',
        b'{"error": {"nested": 1}}',
        b'{"error": 5}',
        b'{"exception": null}',
        b'{"exception": "", "rows": []}',
        b'"just a string"',
        b"42",
        b"null",
    ],
)
def test_a_json_document_that_is_not_a_moodle_error_is_a_normal_file(m, mock, dl_dir, body):
    mock.download = lambda _r: httpx.Response(200, content=body, headers=JSON_TYPE)
    path = Path(m.download_file(_url("data.json")))
    assert path.read_bytes() == body
    assert files_under(dl_dir) == [path]


def test_a_large_json_file_is_saved_intact_with_the_sniffed_bytes_written_first(m, mock, dl_dir):
    body = json.dumps({"rows": [f"row {i:05d} " + "x" * 90 for i in range(3000)]}).encode()
    assert len(body) > 250_000
    chunks = [body[i : i + 1000] for i in range(0, len(body), 1000)]
    mock.download = lambda _r: httpx.Response(200, stream=ChunkStream(iter(chunks)), headers=JSON_TYPE)
    path = Path(m.download_file(_url("big.json")))
    assert path.read_bytes() == body, "no byte lost, repeated or reordered at the 64 KiB sniffing boundary"
    assert files_under(dl_dir) == [path]


def test_a_json_file_replaces_an_older_copy(m, mock, dl_dir):
    path = Path(m.download_file(_url("data.json")))
    mock.download = lambda _r: httpx.Response(200, content=b'{"v": 2}', headers=JSON_TYPE)
    assert Path(m.download_file(_url("data.json"))) == path
    assert path.read_bytes() == b'{"v": 2}' and files_under(dl_dir) == [path]


@pytest.mark.parametrize(
    "payload",
    [
        {"exception": "x"},
        {"errorcode": "c"},
        {"error": "plain text"},
        {"exception": "moodle_exception", "errorcode": "invalidtoken", "message": f"token={FAKE_TOKEN}"},
    ],
    ids=["exception-only", "errorcode-only", "error-string", "invalidtoken"],
)
@pytest.mark.parametrize("trailer", [b"", b"\n", b"  \r\n"], ids=["bare", "newline", "padded"])
def test_a_moodle_error_json_is_still_refused_whatever_follows_it(m, mock, dl_dir, payload, trailer):
    mock.download = lambda _r: httpx.Response(200, content=json.dumps(payload).encode() + trailer, headers=JSON_TYPE)
    with pytest.raises(m.MoodleError) as info:
        m.download_file(_url("report.pdf"))
    assert_no_leak(str(info.value))
    assert files_under(dl_dir) == []


def test_a_json_error_arriving_in_tiny_chunks_is_still_detected(m, mock, dl_dir):
    body = json.dumps({"error": "nope", "errorcode": "accessexception"}).encode()
    mock.download = lambda _r: httpx.Response(
        200, stream=ChunkStream(body[i : i + 3] for i in range(0, len(body), 3)), headers=JSON_TYPE
    )
    with pytest.raises(m.MoodleError) as info:
        m.download_file(_url())
    assert info.value.errorcode == "accessexception"
    assert files_under(dl_dir) == []
