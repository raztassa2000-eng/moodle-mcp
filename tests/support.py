"""Helpers shared by the whole suite (imported as `support`).

Nothing here talks to a real network or reads a real token: `MoodleMock` is the only "server".
"""

from __future__ import annotations

import datetime as dt
import os
import re
import time
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl

import httpx

ROOT = Path(__file__).resolve().parents[1]
FAKE_TOKEN = "a1b2c3d4e5f60718293a4b5c6d7e8f90"
TAU_HOST = "moodle.tau.ac.il"
TAU_URL = f"https://{TAU_HOST}"
REST_PATH = "/webservice/rest/server.php"
PREFLIGHT_PATH = "/lib/ajax/service-nologin.php"
PLUGINFILE = "/webservice/pluginfile.php/"
MB = 1024 * 1024

# Every setting the code reads; the autouse fixture clears them (and any other MOODLE_*) per test.
ENV_VARS = (
    "MOODLE_SITE",
    "MOODLE_URL",
    "MOODLE_TOKEN",
    "MOODLE_TOKEN_FILE",
    "MOODLE_DOWNLOAD_DIR",
    "MOODLE_MAX_DOWNLOAD_MB",
    "MOODLE_DOWNLOAD_SECONDS",
    "MOODLE_SKIP_PREFLIGHT",
    "MOODLE_DOWNLOAD_TOKEN_IN_QUERY",
)


def preflight_reply(wwwroot: str | None = TAU_URL, httpswwwroot: str | None = None, **top: Any) -> list:
    """What /lib/ajax/service-nologin.php answers for tool_mobile_get_public_config."""
    data = {}
    if wwwroot is not None:
        data["wwwroot"] = wwwroot
    if httpswwwroot is not None:
        data["httpswwwroot"] = httpswwwroot
    return [{"error": False, "data": data, **top}]


PREFLIGHT_OK = preflight_reply(TAU_URL, TAU_URL)


def form(request: httpx.Request) -> dict[str, str]:
    """Decode an application/x-www-form-urlencoded request body into a dict."""
    return dict(parse_qsl(request.content.decode("utf-8"), keep_blank_values=True))


def assert_no_token(text: str) -> None:
    """Fail if `text` contains the token or an un-masked `token=<value>` / `wstoken=<value>`."""
    assert FAKE_TOKEN not in text, f"token value leaked: {text!r}"
    assert not re.search(r"token=(?!\*\*\*)[^\s&]", text, re.IGNORECASE), f"token= leaked: {text!r}"


def assert_no_leak(text: str) -> None:
    """An error text shown to the model: no token, no URL, no endpoint names."""
    assert_no_token(text)
    for marker in ("://", "pluginfile", "server.php", "service-nologin"):
        assert marker not in text, f"{marker!r} leaked into {text!r}"


def assert_no_token_fragment(text: str, size: int = 8) -> None:
    """Not even a piece of the token (a cut-off token is still a leak)."""
    for i in range(len(FAKE_TOKEN) - size + 1):
        assert FAKE_TOKEN[i : i + size] not in text, f"token fragment leaked: {text!r}"


def clean_env(**extra: str) -> dict[str, str]:
    """os.environ without any MOODLE_* variable, plus `extra` (for subprocess tests). The child Python also
    loads tests/nonet/sitecustomize.py, which blocks every non-loopback connection."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("MOODLE_")}
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONPATH"] = os.pathsep.join(p for p in (str(ROOT / "tests" / "nonet"), env.get("PYTHONPATH", "")) if p)
    env.update(extra)
    return env


def files_under(directory: Path) -> list[Path]:
    """All regular files below `directory` (empty list if it does not exist)."""
    if not directory.exists():
        return []
    return sorted(p for p in directory.rglob("*") if p.is_file())


def assert_inside(path: Path | str, root: Path) -> None:
    resolved = Path(path).resolve()
    assert resolved.is_relative_to(root.resolve()), f"{resolved} escaped {root}"


def default_download(_request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, content=b"%PDF-fake", headers={"content-type": "application/pdf"})


class ChunkStream(httpx.SyncByteStream):
    """A response body that counts what was read and can fail half way (`error` after the chunks)."""

    def __init__(self, chunks: Iterable[bytes], error: Exception | None = None, on_chunk: Callable | None = None):
        self._chunks = chunks
        self._error = error
        self._on_chunk = on_chunk
        self.produced = 0

    def __iter__(self):
        for chunk in self._chunks:
            if self._on_chunk:
                self._on_chunk(self.produced)
            self.produced += 1
            yield chunk
        if self._error is not None:
            raise self._error


class MoodleMock:
    """A tiny fake Moodle behind httpx.MockTransport.

    Values registered with `on` / `preflight` / `download` may be:
      * a JSON-serialisable object      -> 200 JSON response
      * an httpx.Response               -> returned as is
      * an exception instance           -> raised (simulates network errors)
      * a callable                      -> called (REST: with the decoded form dict;
                                           preflight/download: with the request) and its
                                           return value is interpreted as above
    """

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.rest_calls: list[tuple[str, dict[str, str]]] = []
        self.preflight: Any = PREFLIGHT_OK
        self.rest: dict[str, Any] = {"core_webservice_get_site_info": {"userid": 42}}
        self.rest_default: Any = None
        self.download: Any = default_download
        # The wire audit (see `audit`): the only hosts allowed to be contacted, and whether the
        # opt-in MOODLE_DOWNLOAD_TOKEN_IN_QUERY fallback is part of this test.
        self.allowed_hosts: set[str] = {TAU_HOST}
        self.allow_token_in_url = False

    def on(self, function: str, value: Any) -> None:
        self.rest[function] = value

    def fail_all(self, value: Any) -> None:
        """Every REST function (the site-info lookup included) answers `value`."""
        self.rest.clear()
        self.rest_default = value

    def serve(self, files: dict[str, Any], missing: int = 404) -> None:
        """Downloads answer by URL path: {"/webservice/pluginfile.php/1/a.pdf": b"..." | Response}."""

        def handler(request: httpx.Request) -> httpx.Response:
            value = files.get(request.url.path)
            if value is None:
                return httpx.Response(missing, text="no such file")
            return value if isinstance(value, httpx.Response) else httpx.Response(200, content=value)

        self.download = handler

    # -- introspection helpers -------------------------------------------------------------
    def functions_called(self) -> list[str]:
        return [fn for fn, _ in self.rest_calls]

    def forms_for(self, function: str) -> list[dict[str, str]]:
        return [f for fn, f in self.rest_calls if fn == function]

    def preflight_requests(self) -> list[httpx.Request]:
        return [r for r in self.requests if r.url.path.endswith(PREFLIGHT_PATH)]

    def rest_requests(self) -> list[httpx.Request]:
        return [r for r in self.requests if r.url.path.endswith(REST_PATH)]

    def download_requests(self) -> list[httpx.Request]:
        return [r for r in self.requests if PLUGINFILE in r.url.path]

    def foreign_requests(self, host: str = TAU_HOST) -> list[httpx.Request]:
        return [r for r in self.requests if r.url.host != host]

    # -- transport -------------------------------------------------------------------------
    @staticmethod
    def _respond(value: Any, arg: Any) -> httpx.Response:
        if callable(value):
            value = value(arg)
        if isinstance(value, BaseException):
            raise value
        if isinstance(value, httpx.Response):
            return value
        return httpx.Response(200, json=value)

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        if path.endswith(PREFLIGHT_PATH):
            return self._respond(self.preflight, request)
        if path.endswith(REST_PATH):
            fields = form(request)
            function = fields.get("wsfunction", "")
            self.rest_calls.append((function, fields))
            if function in self.rest:
                return self._respond(self.rest[function], fields)
            if self.rest_default is not None:
                return self._respond(self.rest_default, fields)
            body = {"exception": "moodle_exception", "errorcode": "unmocked", "message": f"no mock for {function}"}
            return httpx.Response(200, json=body)
        if PLUGINFILE in path:
            return self._respond(self.download, request)
        return httpx.Response(404, text="not found")

    # -- the wire audit: runs after EVERY test (see conftest) ---------------------------------
    def audit(self) -> None:
        """Whatever a test did, the token only ever travelled in the POST body of a request to
        an allowed host, and the token-less identity check never carried it."""
        token = FAKE_TOKEN.encode()
        for r in self.requests:
            where = f"{r.method} {r.url.host}{r.url.path}"
            assert r.url.host in self.allowed_hosts, f"request to a foreign host: {where}"
            assert r.url.scheme == "https" or r.url.host in ("localhost", "127.0.0.1"), f"not https: {where}"
            try:
                body = r.content
            except httpx.RequestNotRead:
                body = b""
            assert all(FAKE_TOKEN not in v for v in r.headers.values()), f"token in a header: {where}"
            if r.url.path.endswith(PREFLIGHT_PATH):
                assert token not in body and FAKE_TOKEN not in str(r.url), f"token sent to the preflight: {where}"
            if FAKE_TOKEN in str(r.url):
                assert self.allow_token_in_url, f"token in the URL: {where}"
                assert r.method == "GET" and PLUGINFILE in r.url.path, f"token in the URL of: {where}"
            if token in body:
                assert r.method == "POST", f"token in the body of a non-POST: {where}"
                assert r.url.path.endswith(REST_PATH) or PLUGINFILE in r.url.path, f"token sent to: {where}"


# ---- shared by the tool smoke tests ---------------------------------------------------------------
TOOLS = {
    "list_courses",
    "course_contents",
    "upcoming_deadlines",
    "assignments",
    "grades",
    "grades_overview",
    "forum_discussions",
    "forum_thread",
    "quizzes",
    "notifications",
    "calendar_events",
    "whats_new",
    "download_file",
    "download_course",
}
NOW = int(time.time())
DAY = 86400
OLD = 1_700_000_000
ALG = {"id": 7, "fullname": "Algorithms", "shortname": "ALG", "progress": 50.5, "enddate": 0}
FILE_URL = f"{TAU_URL}{PLUGINFILE}11/mod_resource/content/1/a.pdf"


def stamp(ts: int) -> str:
    """How the tools print a time (fmt_time)."""
    return dt.datetime.fromtimestamp(ts).strftime("%d/%m/%Y %H:%M")


def courses(mock: MoodleMock, *extra: dict) -> None:
    mock.on("core_enrol_get_users_courses", [ALG, *extra])


# ---- third-party text comes back fenced (moodle_safety.untrusted) -------------------------------------
FENCE_RE = re.compile(r'<untrusted_moodle_content source="([a-z_.]{1,40})">(.*)</untrusted_moodle_content>', re.DOTALL)


def fenced(text: str, source: str) -> str:
    """The exact wrapper a tool puts around `text` (already clean) for the label `source`."""
    return f'<untrusted_moodle_content source="{source}">{text}</untrusted_moodle_content>'


def unwrap(value: Any) -> tuple[str, str]:
    """(source, text) of a fenced string; fails if `value` is anything else."""
    match = FENCE_RE.fullmatch(value) if isinstance(value, str) else None
    assert match, f"not a fenced value: {value!r}"
    return match.group(1), match.group(2)
