"""moodle_safety.validate_base_url and check_download_url: pure functions, no network, no env."""

from __future__ import annotations

import pytest
from urllib.parse import unquote, urlsplit

import moodle_safety as s
from moodle_mcp import SITES
from support import FAKE_TOKEN, PLUGINFILE, TAU_HOST, TAU_URL

# ================================================================================ validate_base_url

VALID_BASES = [
    ("https://moodle.tau.ac.il", "https://moodle.tau.ac.il"),
    ("https://moodle.tau.ac.il/", "https://moodle.tau.ac.il"),
    ("https://moodle.tau.ac.il///", "https://moodle.tau.ac.il"),
    ("\t https://moodle.tau.ac.il \n", "https://moodle.tau.ac.il"),
    ("https://MOODLE.Tau.AC.il", "https://moodle.tau.ac.il"),
    ("HTTPS://moodle.tau.ac.il", "https://moodle.tau.ac.il"),
    ("https://moodle.tau.ac.il:443", "https://moodle.tau.ac.il"),
    ("https://moodle.tau.ac.il:", "https://moodle.tau.ac.il"),
    ("https://moodle.bgu.ac.il/moodle", "https://moodle.bgu.ac.il/moodle"),
    ("https://moodle.bgu.ac.il/moodle/", "https://moodle.bgu.ac.il/moodle"),
    ("https://moodle.example.edu:8443/lms", "https://moodle.example.edu:8443/lms"),
    ("https://moodle.example.edu:65535", "https://moodle.example.edu:65535"),
    ("https://xn--mdle-0ra.example.edu", "https://xn--mdle-0ra.example.edu"),
    ("https://192.168.1.10/moodle", "https://192.168.1.10/moodle"),
    ("https://[2001:db8::1]:8443/m", "https://[2001:db8::1]:8443/m"),
    ("https://localhost", "https://localhost"),
    ("http://localhost", "http://localhost"),
    ("http://localhost:80/", "http://localhost"),
    ("http://localhost:8080/", "http://localhost:8080"),
    ("http://LOCALHOST:8080/moodle/", "http://localhost:8080/moodle"),
    ("http://127.0.0.1:8000", "http://127.0.0.1:8000"),
    ("http://[::1]:8000", "http://[::1]:8000"),
    ("http://[::1]", "http://[::1]"),
]

INVALID_BASES = [
    pytest.param("", id="empty"),
    pytest.param("   \n", id="blank"),
    pytest.param("moodle.tau.ac.il", id="no-scheme"),
    pytest.param("//moodle.tau.ac.il", id="scheme-relative"),
    pytest.param("/moodle", id="path-only"),
    pytest.param("https://", id="no-host"),
    pytest.param("https:///path", id="empty-host-with-path"),
    pytest.param("https://:443", id="empty-host-with-port"),
    pytest.param("http://moodle.tau.ac.il", id="http-remote"),
    pytest.param("HTTP://moodle.tau.ac.il", id="http-remote-upper"),
    pytest.param("http://localhost.evil.com", id="http-localhost-lookalike"),
    pytest.param("http://evil.com/localhost", id="http-localhost-in-path"),
    pytest.param("http://127.0.0.1.evil.com", id="http-127-lookalike"),
    pytest.param("http://127.0.0.2", id="http-other-loopback"),
    pytest.param("http://0.0.0.0", id="http-any-address"),
    pytest.param("ftp://moodle.tau.ac.il", id="ftp"),
    pytest.param("file:///etc/passwd", id="file"),
    pytest.param("javascript:alert(1)", id="javascript"),
    pytest.param("data:text/plain,hi", id="data-url"),
    pytest.param("https://user@moodle.tau.ac.il", id="userinfo-user"),
    pytest.param("https://user:pw@moodle.tau.ac.il", id="userinfo-password"),
    pytest.param("https://@moodle.tau.ac.il", id="userinfo-empty"),
    pytest.param("https://moodle.tau.ac.il@evil.com", id="host-confusion-at"),
    pytest.param("https://evil.com\\@moodle.tau.ac.il", id="backslash-at"),
    pytest.param("https://moodle.tau.ac.il?x=1", id="query"),
    pytest.param("https://moodle.tau.ac.il/?x=1", id="query-after-slash"),
    pytest.param("https://moodle.tau.ac.il/moodle?token=abc", id="query-token"),
    pytest.param("https://moodle.tau.ac.il#frag", id="fragment"),
    pytest.param("https://moodle.tau.ac.il/#frag", id="fragment-after-slash"),
    pytest.param("https://moodle.tau .ac.il", id="space-in-host"),
    pytest.param("https://moodle.tau.ac.il/a b", id="space-in-path"),
    pytest.param("https://moodle.tau.ac.il/a\nb", id="newline-in-path"),
    pytest.param("https://moodle.tau.ac.il/a\rb", id="cr-in-path"),
    pytest.param("https://moodle.tau.ac.il/a\tb", id="tab-in-path"),
    pytest.param("https://moodle.tau.ac.il/\x00", id="nul"),
    pytest.param("https://moodle.tau.ac.il/\x07", id="bell"),
    pytest.param("https://moodle.tau.ac.il/a b", id="nbsp"),
    pytest.param("https://moodle.tau.ac.il/a b", id="line-separator"),
    pytest.param("https://moodle.tau.ac.il/​", id="zero-width-space"),
    pytest.param("https://moodle.tau.ac.il/‮", id="bidi-override"),
    pytest.param("https://moodle.tau.ac.il/﻿", id="bom-char"),
    pytest.param("https://moodle.tau.ac.il\\evil.com", id="backslash-host"),
    pytest.param("https://moodle.tau.ac.il/a\\b", id="backslash-path"),
    pytest.param("https://moodle.tau.ac.il:abc", id="junk-port"),
    pytest.param("https://moodle.tau.ac.il:-1", id="negative-port"),
    pytest.param("https://moodle.tau.ac.il:99999999", id="huge-port"),
    pytest.param("https://moodle.tau.ac.il:443:443", id="double-port"),
    pytest.param("https://[::1", id="unclosed-ipv6"),
    pytest.param("https://-bad.example.edu", id="leading-hyphen"),
    pytest.param("https://bad-.example.edu", id="trailing-hyphen"),
    pytest.param("https://a..example.edu", id="empty-label"),
    pytest.param("https://moodle.tau.ac.il.", id="trailing-dot-host"),
    pytest.param("https://my_moodle.example.edu", id="underscore-host"),
    pytest.param("https://mödle.example.edu", id="idn-latin"),
    pytest.param("https://מודל.example.edu", id="idn-hebrew"),
    pytest.param("https://moodle.tau.ac.il%2eevil.com", id="percent-in-host"),
    pytest.param("https://x.edu%40evil.com", id="encoded-at-in-host"),
    pytest.param("https://moodle.tau.ac.il/a/../b", id="dotdot-segment"),
    pytest.param("https://moodle.tau.ac.il/./a", id="dot-segment"),
    pytest.param("https://moodle.tau.ac.il/..", id="dotdot-only"),
    pytest.param("https://moodle.tau.ac.il/a/..", id="trailing-dotdot"),
]


@pytest.mark.parametrize(("raw", "expected"), VALID_BASES)
def test_validate_base_url_accepts_and_normalizes(raw, expected):
    assert s.validate_base_url(raw) == expected
    assert s.validate_base_url(expected) == expected, "normalizing twice must change nothing"


@pytest.mark.parametrize("raw", INVALID_BASES)
def test_validate_base_url_rejects_with_value_error(raw):
    with pytest.raises(ValueError):
        s.validate_base_url(raw)


@pytest.mark.parametrize("raw", [None, 5, 1.5, b"https://moodle.tau.ac.il", ["https://moodle.tau.ac.il"], object()])
def test_validate_base_url_rejects_non_strings(raw):
    with pytest.raises(ValueError):
        s.validate_base_url(raw)


@pytest.mark.parametrize(
    "raw",
    [
        "https://user:S3cr3tPassw0rd@moodle.example.edu",
        "https://moodle.example.edu/?token=S3cr3tPassw0rd",
        "http://S3cr3tPassw0rd.example.edu",
        "https://moodle.example.edu/S3cr3tPassw0rd/../x",
    ],
)
def test_validate_base_url_errors_do_not_echo_the_input(raw):
    with pytest.raises(ValueError) as info:
        s.validate_base_url(raw)
    assert "S3cr3tPassw0rd" not in str(info.value)


def test_local_hosts_are_exactly_localhost_and_loopback():
    assert s.LOCAL_HOSTS == {"localhost", "127.0.0.1", "::1"}


# ============================================================================== check_download_url

GOOD = f"{TAU_URL}{PLUGINFILE}123/mod_resource/content/1/file.pdf"

ACCEPTED_URLS = [
    GOOD,
    GOOD + "?forcedownload=1",
    GOOD + "?a=1&b=2",
    f"{TAU_URL}:443{PLUGINFILE}1/a.pdf",
    f"HTTPS://MOODLE.TAU.AC.IL{PLUGINFILE}1/a.pdf",
    f"{TAU_URL}{PLUGINFILE}1/My%20File%20%281%29.pdf",
    f"{TAU_URL}{PLUGINFILE}1/%D7%A9%D7%9C%D7%95%D7%9D.pdf",
    f"{TAU_URL}{PLUGINFILE}1/a%2520b.pdf",  # %25 is fine; only encoded dots and separators are refused
    f"{TAU_URL}{PLUGINFILE}1//a.pdf",
    f"{TAU_URL}{PLUGINFILE}1/...pdf",
    f"{TAU_URL}{PLUGINFILE}1/a..b.pdf",
]

REJECTED_URLS = [
    pytest.param("", id="empty"),
    pytest.param("/webservice/pluginfile.php/1/a.pdf", id="relative"),
    pytest.param(f"//{TAU_HOST}{PLUGINFILE}1/a.pdf", id="scheme-relative"),
    pytest.param("webservice/pluginfile.php/1/a.pdf", id="no-slash-relative"),
    pytest.param(f"http://{TAU_HOST}{PLUGINFILE}1/a.pdf", id="http-downgrade"),
    pytest.param(f"ftp://{TAU_HOST}{PLUGINFILE}1/a.pdf", id="ftp"),
    pytest.param(f"file://{TAU_HOST}{PLUGINFILE}1/a.pdf", id="file"),
    pytest.param("javascript:alert(1)", id="javascript"),
    pytest.param(f"https://evil.example.com{PLUGINFILE}1/a.pdf", id="other-host"),
    pytest.param(f"https://{TAU_HOST}.evil.com{PLUGINFILE}1/a.pdf", id="suffix-host"),
    pytest.param(f"https://x{TAU_HOST}{PLUGINFILE}1/a.pdf", id="prefix-host"),
    pytest.param(f"https://sub.{TAU_HOST}{PLUGINFILE}1/a.pdf", id="subdomain"),
    pytest.param(f"https://{TAU_HOST}.{PLUGINFILE}1/a.pdf", id="trailing-dot-host"),
    pytest.param(f"https://user@{TAU_HOST}{PLUGINFILE}1/a.pdf", id="userinfo"),
    pytest.param(f"https://user:pw@{TAU_HOST}{PLUGINFILE}1/a.pdf", id="userinfo-password"),
    pytest.param(f"https://@{TAU_HOST}{PLUGINFILE}1/a.pdf", id="userinfo-empty"),
    pytest.param(f"https://:@{TAU_HOST}{PLUGINFILE}1/a.pdf", id="userinfo-colon-only"),
    pytest.param(f"https://{TAU_HOST}@evil.com{PLUGINFILE}1/a.pdf", id="at-confusion"),
    pytest.param(f"https://evil.com\\@{TAU_HOST}{PLUGINFILE}1/a.pdf", id="backslash-at"),
    pytest.param(f"https://{TAU_HOST}:pw@evil.com{PLUGINFILE}1/a.pdf", id="password-confusion"),
    pytest.param(f"{TAU_URL}:8443{PLUGINFILE}1/a.pdf", id="other-port"),
    pytest.param(f"{TAU_URL}:80{PLUGINFILE}1/a.pdf", id="port-80"),
    pytest.param(f"{TAU_URL}:abc{PLUGINFILE}1/a.pdf", id="junk-port"),
    pytest.param(f"{TAU_URL}:99999{PLUGINFILE}1/a.pdf", id="huge-port"),
    pytest.param(f"{TAU_URL}/pluginfile.php/1/a.pdf", id="legacy-pluginfile-path"),
    pytest.param(f"{TAU_URL}/webservice/rest/server.php?x=1", id="rest-endpoint"),
    pytest.param(f"{TAU_URL}/lib/ajax/service.php", id="ajax-endpoint"),
    pytest.param(f"{TAU_URL}/webservice/pluginfile.php", id="no-trailing-slash"),
    pytest.param(f"{TAU_URL}/webservice/pluginfile.phpx/1/a.pdf", id="pluginfile-lookalike"),
    pytest.param(f"{TAU_URL}/other/webservice/pluginfile.php/1/a.pdf", id="nested-prefix"),
    pytest.param(f"{TAU_URL}/mod/resource/view.php?id=5", id="view-page"),
    pytest.param(f"{TAU_URL}/WEBSERVICE/PLUGINFILE.PHP/1/a.pdf", id="path-case"),
    pytest.param(f"{TAU_URL}{PLUGINFILE}1/../../../etc/passwd", id="dotdot"),
    pytest.param(f"{TAU_URL}{PLUGINFILE}1/./a.pdf", id="dot-segment"),
    pytest.param(f"{TAU_URL}{PLUGINFILE}1/%2e/a.pdf", id="encoded-dot-segment"),
    pytest.param(f"{TAU_URL}{PLUGINFILE}1/%2e%2e/%2e%2e/x", id="encoded-dotdot"),
    pytest.param(f"{TAU_URL}{PLUGINFILE}1/%2E%2E/x", id="encoded-dotdot-upper"),
    pytest.param(f"{TAU_URL}{PLUGINFILE}1/.%2e/x", id="dot-then-encoded-dot"),
    pytest.param(f"{TAU_URL}{PLUGINFILE}1/%2e./x", id="encoded-dot-then-dot"),
    pytest.param(f"{TAU_URL}{PLUGINFILE}1/%2E%2e/x", id="mixed-case-dotdot"),
    pytest.param(f"{TAU_URL}{PLUGINFILE}1/.%2E/x", id="mixed-case-dotdot-2"),
    pytest.param(f"{TAU_URL}{PLUGINFILE}1/%252e%252e/x", id="double-encoded-dotdot"),
    pytest.param(f"{TAU_URL}{PLUGINFILE}1%2fa.pdf", id="encoded-slash"),
    pytest.param(f"{TAU_URL}{PLUGINFILE}1%2Fa.pdf", id="encoded-slash-upper"),
    pytest.param(f"{TAU_URL}{PLUGINFILE}1%252fa.pdf", id="double-encoded-slash"),
    pytest.param(f"{TAU_URL}{PLUGINFILE}1%5ca.pdf", id="encoded-backslash"),
    pytest.param(f"{TAU_URL}{PLUGINFILE}1%5Ca.pdf", id="encoded-backslash-upper"),
    pytest.param(f"{TAU_URL}{PLUGINFILE}1%255ca.pdf", id="double-encoded-backslash"),
    pytest.param(f"{TAU_URL}{PLUGINFILE}1\\..\\a.pdf", id="backslashes"),
    pytest.param(f"{TAU_URL}{PLUGINFILE}1/a;jsessionid=1.pdf", id="semicolon"),
    pytest.param(f"{TAU_URL}{PLUGINFILE}1/a.pdf#frag", id="fragment"),
    pytest.param(f"{TAU_URL}{PLUGINFILE}1/a.pdf?x=1#frag", id="fragment-after-query"),
    pytest.param(f"{TAU_URL}{PLUGINFILE}1/a.pdf?x=#", id="empty-fragment"),
    pytest.param(f"{TAU_URL}{PLUGINFILE}1/a%00.pdf", id="encoded-nul"),
    pytest.param(f"{TAU_URL}{PLUGINFILE}1/a%0a.pdf", id="encoded-newline"),
    pytest.param(f"{TAU_URL}{PLUGINFILE}1/a%0d%0aHost:%20evil", id="encoded-crlf"),
    pytest.param(f"{TAU_URL}{PLUGINFILE}1/a.pdf\n", id="trailing-newline"),
    pytest.param(f"{TAU_URL}{PLUGINFILE}1/a\n.pdf", id="inner-newline"),
    pytest.param(f"{TAU_URL}{PLUGINFILE}1/a\r\nHost: evil/.pdf", id="crlf-injection"),
    pytest.param(f"{TAU_URL}{PLUGINFILE}1/a\t.pdf", id="tab"),
    pytest.param(f"{TAU_URL}{PLUGINFILE}1/a b.pdf", id="raw-space"),
    pytest.param(f" {GOOD}", id="leading-space"),
    pytest.param(f"{GOOD} ", id="trailing-space"),
    pytest.param(f"{TAU_URL}{PLUGINFILE}1/a\x00.pdf", id="nul"),
    pytest.param(f"{TAU_URL}{PLUGINFILE}1/‮txt.exe", id="bidi-override"),
    pytest.param(f"{TAU_URL}{PLUGINFILE}1/a​.pdf", id="zero-width"),
]


@pytest.mark.parametrize("url", ACCEPTED_URLS)
def test_check_download_url_accepts_moodle_pluginfile_urls(url):
    assert s.check_download_url(url, TAU_URL) == url


@pytest.mark.parametrize("url", REJECTED_URLS)
def test_check_download_url_rejects(url):
    with pytest.raises(ValueError):
        s.check_download_url(url, TAU_URL)


@pytest.mark.parametrize("url", REJECTED_URLS)
def test_check_download_url_errors_never_echo_the_url(url):
    secret_url = url + "&token=" + FAKE_TOKEN if "?" in url else url
    with pytest.raises(ValueError) as info:
        s.check_download_url(secret_url, TAU_URL)
    message = str(info.value)
    assert FAKE_TOKEN not in message
    assert "evil" not in message.lower()
    assert "://" not in message
    assert url.strip() not in message or url.strip() == ""


@pytest.mark.parametrize("url", [None, 5, b"https://moodle.tau.ac.il/webservice/pluginfile.php/1/a.pdf", [GOOD]])
def test_check_download_url_rejects_non_strings(url):
    with pytest.raises(ValueError):
        s.check_download_url(url, TAU_URL)


BASES = {
    "plain": "https://moodle.tau.ac.il",
    "path-prefix": "https://moodle.bgu.ac.il/moodle",
    "port-and-prefix": "https://moodle.example.edu:8443/lms",
    "localhost": "http://localhost:8080",
}


@pytest.mark.parametrize("base", BASES.values(), ids=BASES.keys())
def test_check_download_url_accepts_exactly_the_bases_own_pluginfile_path(base):
    good = f"{base}/webservice/pluginfile.php/1/a.pdf"
    assert s.check_download_url(good, base) == good


@pytest.mark.parametrize(
    ("base", "url"),
    [
        ("https://moodle.bgu.ac.il/moodle", "https://moodle.bgu.ac.il/webservice/pluginfile.php/1/a.pdf"),
        ("https://moodle.bgu.ac.il/moodle", "https://moodle.bgu.ac.il/moodle2/webservice/pluginfile.php/1/a.pdf"),
        ("https://moodle.bgu.ac.il/moodle", "https://moodle.bgu.ac.il/moodlex/webservice/pluginfile.php/1/a.pdf"),
        ("https://moodle.bgu.ac.il/moodle", "https://moodle.bgu.ac.il/Moodle/webservice/pluginfile.php/1/a.pdf"),
        ("https://moodle.bgu.ac.il/moodle", "https://moodle.bgu.ac.il/moodle/other/webservice/pluginfile.php/1/a"),
        ("https://moodle.bgu.ac.il/moodle", "https://moodle.bgu.ac.il/moodle/webservice/pluginfile.php"),
        ("https://moodle.bgu.ac.il/moodle", "https://moodle.bgu.ac.il/moodle/../webservice/pluginfile.php/1/a.pdf"),
        ("https://moodle.bgu.ac.il/moodle", "https://moodle.bgu.ac.il/moodle/webservice/pluginfile.php/1/%2e%2e/a"),
        ("https://moodle.example.edu:8443/lms", "https://moodle.example.edu/lms/webservice/pluginfile.php/1/a.pdf"),
        ("https://moodle.example.edu:8443/lms", "https://moodle.example.edu:443/lms/webservice/pluginfile.php/1/a"),
        ("https://moodle.example.edu:8443/lms", "https://moodle.example.edu:9443/lms/webservice/pluginfile.php/1/a"),
        ("http://localhost:8080", "https://localhost:8080/webservice/pluginfile.php/1/a.pdf"),
        ("http://localhost:8080", "http://localhost:8081/webservice/pluginfile.php/1/a.pdf"),
        ("http://localhost:8080", "http://localhost/webservice/pluginfile.php/1/a.pdf"),
        ("http://localhost:8080", "http://127.0.0.1:8080/webservice/pluginfile.php/1/a.pdf"),
        ("https://moodle.tau.ac.il", "http://moodle.tau.ac.il:443/webservice/pluginfile.php/1/a.pdf"),
    ],
)
def test_check_download_url_rejects_other_prefix_port_scheme_or_host(base, url):
    with pytest.raises(ValueError):
        s.check_download_url(url, base)


def test_check_download_url_port_zero_is_not_the_default_port():
    with pytest.raises(ValueError):
        s.check_download_url(f"{TAU_URL}:0{PLUGINFILE}1/a.pdf", TAU_URL)
    with pytest.raises(ValueError):
        s.check_download_url(f"http://localhost:0{PLUGINFILE}1/a.pdf", "http://localhost")


def test_check_download_url_ports_are_compared_exactly_in_both_directions():
    with_zero = f"https://moodle.example.edu:0{PLUGINFILE}1/a.pdf"
    assert s.check_download_url(with_zero, "https://moodle.example.edu:0") == with_zero
    for url in (f"https://moodle.example.edu{PLUGINFILE}1/a.pdf", f"https://moodle.example.edu:443{PLUGINFILE}1/a.pdf"):
        with pytest.raises(ValueError):
            s.check_download_url(url, "https://moodle.example.edu:0")
    default = f"https://moodle.example.edu:443{PLUGINFILE}1/a.pdf"
    assert s.check_download_url(default, "https://moodle.example.edu") == default


# The combinatorial property: whatever the input, an ACCEPTED url really points at <base>/webservice/
# pluginfile.php/ on the exact host, port and scheme, with no userinfo, dot segments or control characters.
HOST_PARTS = [
    "moodle.tau.ac.il",
    "MOODLE.TAU.AC.IL",
    "moodle.tau.ac.il:443",
    "moodle.tau.ac.il:8443",
    "moodle.tau.ac.il:80",
    "moodle.tau.ac.il:0",
    "moodle.tau.ac.il:",
    "moodle.tau.ac.il.evil.com",
    "evil.com",
    "moodle.tau.ac.il@evil.com",
    "evil.com@moodle.tau.ac.il",
    "user:pw@moodle.tau.ac.il",
    "evil.com\\@moodle.tau.ac.il",
    "moodle.tau.ac.il\\.evil.com",
]
SCHEMES = ["https", "HTTPS", "http", "ftp", ""]
TAILS = [
    "1/a.pdf",
    "1/a%20b.pdf",
    "1/../a.pdf",
    "1/./a.pdf",
    "1/%2e%2e/a.pdf",
    "1/.%2e/a.pdf",
    "1/%2e./a.pdf",
    "1/%2e/a.pdf",
    "1/a%2fb",
    "1/a%5cb",
    "1/a;b",
    "1/a%00b",
    "1/a%0ab",
    "1/%252e%252e/a",
    "1/a#b",
    "1/a\\b",
    "1/a b",
    "../../x",
    "",
]


@pytest.mark.parametrize("scheme", SCHEMES)
@pytest.mark.parametrize("host", HOST_PARTS)
def test_every_accepted_download_url_stays_on_the_moodle_pluginfile_path(scheme, host):
    for tail in TAILS:
        url = f"{scheme}://{host}{PLUGINFILE}{tail}"
        try:
            s.check_download_url(url, TAU_URL)
        except ValueError:
            continue
        parts = urlsplit(url)
        where = f"accepted {url!r}"
        assert parts.scheme == "https", where
        assert parts.hostname == TAU_HOST, where
        assert parts.port in (None, 443), where
        assert "@" not in parts.netloc and "\\" not in url and not parts.fragment, where
        path = unquote(parts.path)
        assert path.startswith(PLUGINFILE), where
        assert not {".", ".."} & set(path.split("/")), where
        assert ";" not in parts.path and "\x00" not in path and "\n" not in path, where


def test_the_combinatorial_property_is_not_vacuous():
    verdicts = {}
    for scheme in SCHEMES:
        for host in HOST_PARTS:
            for tail in TAILS:
                url = f"{scheme}://{host}{PLUGINFILE}{tail}"
                try:
                    s.check_download_url(url, TAU_URL)
                    verdicts[url] = True
                except ValueError:
                    verdicts[url] = False
    accepted = [u for u, ok in verdicts.items() if ok]
    assert len(accepted) >= 8, "the generator should accept some URLs"
    assert len(accepted) < len(verdicts) / 2, "and refuse most"
    assert f"https://{TAU_HOST}{PLUGINFILE}1/a.pdf" in accepted


# ===================================================================================== the presets


@pytest.mark.parametrize("key", list(SITES))
def test_every_preset_url_is_valid_normal_and_accepts_its_own_pluginfile_urls(key):
    name, url = SITES[key]
    assert name.strip() and url.startswith("https://")
    assert s.validate_base_url(url) == url, "a preset must already be in normal form"
    good = f"{url}/webservice/pluginfile.php/1/a.pdf"
    assert s.check_download_url(good, url) == good
    other = good.replace(urlsplit(url).hostname, "evil.example.com", 1)
    with pytest.raises(ValueError):
        s.check_download_url(other, url)


def test_presets_have_distinct_plain_keys_and_urls():
    assert all(k == k.strip().lower() and " " not in k for k in SITES)
    urls = [url for _name, url in SITES.values()]
    assert len(set(urls)) == len(urls) and "tau" in SITES
