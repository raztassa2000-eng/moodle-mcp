"""_fetch: size limit, per-call cap, deadline, atomic replacement, where files may be written, the
opt-in ?token= fallback."""

from __future__ import annotations

import time as real_time
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from support import (
    FAKE_TOKEN,
    MB,
    PLUGINFILE,
    TAU_URL,
    ChunkStream,
    assert_inside,
    assert_no_leak,
    assert_no_token,
    files_under,
)


def _url(name: str = "file.pdf") -> str:
    return f"{TAU_URL}{PLUGINFILE}123/mod_resource/content/1/{name}"


def _chunks(total: int, size: int = 65536):
    """`total` bytes in chunks of `size` (a generator: nothing is produced until it is read)."""
    for i in range(0, total, size):
        yield b"x" * min(size, total - i)


def _symlink(link: Path, target: Path) -> None:
    try:
        link.symlink_to(target, target_is_directory=target.is_dir())
    except (OSError, NotImplementedError):
        pytest.skip("symlinks are not available for this account")


# ===================================================================================== size limit


def test_a_declared_size_over_the_limit_is_refused_before_reading_the_body(m, mock, dl_dir, monkeypatch):
    monkeypatch.setenv("MOODLE_MAX_DOWNLOAD_MB", "1")
    stream = ChunkStream(_chunks(2 * MB))
    mock.download = lambda _r: httpx.Response(200, headers={"content-length": str(2 * MB)}, stream=stream)
    with pytest.raises(m.MoodleError, match=r"larger than the 1 MB download limit") as info:
        m.download_file(_url())
    assert_no_leak(str(info.value))
    assert stream.produced == 0, "the declared size is enough to refuse"
    assert files_under(dl_dir) == []


@pytest.mark.parametrize("declared", ["", "abc", "-5", "1e9", "2 MB"], ids=repr)
def test_an_unusable_content_length_does_not_disable_the_limit(m, mock, dl_dir, monkeypatch, declared):
    monkeypatch.setenv("MOODLE_MAX_DOWNLOAD_MB", "1")
    headers = {"content-length": declared} if declared else {}
    stream = ChunkStream(_chunks(4 * MB))
    mock.download = lambda _r: httpx.Response(200, headers=headers, stream=stream)
    with pytest.raises(m.MoodleError, match="download limit"):
        m.download_file(_url())
    assert stream.produced < 64, "aborted as soon as the limit was crossed"
    assert files_under(dl_dir) == []


def test_an_overrun_in_the_middle_of_the_stream_stops_reading_and_deletes_the_partial_file(
    m, mock, dl_dir, monkeypatch
):
    monkeypatch.setenv("MOODLE_MAX_DOWNLOAD_MB", "1")
    seen = {}

    def peek(index):  # runs while the body is being streamed
        part = Path(str(dl_dir.resolve() / "file.pdf") + ".part")
        seen[index] = part.exists()

    stream = ChunkStream(_chunks(4 * MB), on_chunk=peek)
    mock.download = lambda _r: httpx.Response(200, stream=stream)
    with pytest.raises(m.MoodleError, match="download limit"):
        m.download_file(_url())
    assert seen.get(5) is True, "the data is collected in a .part file"
    assert stream.produced <= 18
    assert files_under(dl_dir) == [] and not list(dl_dir.rglob("*.part"))


@pytest.mark.parametrize(
    ("limit_mb", "size", "ok"),
    [
        ("1", MB - 1, True),
        ("1", MB, True),
        ("1", MB + 1, False),
        ("0.5", MB // 2, True),
        ("0.5", MB // 2 + 1, False),
        ("0.001", 1048, True),
        ("0.001", 1049, False),
    ],
)
def test_the_limit_is_exact(m, mock, dl_dir, monkeypatch, limit_mb, size, ok):
    monkeypatch.setenv("MOODLE_MAX_DOWNLOAD_MB", limit_mb)
    mock.download = lambda _r: httpx.Response(200, stream=ChunkStream(_chunks(size, 1000)))
    if ok:
        assert Path(m.download_file(_url())).stat().st_size == size
    else:
        with pytest.raises(m.MoodleError, match="download limit"):
            m.download_file(_url())
        assert files_under(dl_dir) == []


@pytest.mark.parametrize(("limit_mb", "shown"), [("1", "1 MB"), ("0.5", "0.5 MB"), ("250", "250 MB")])
def test_the_refusal_states_the_limit(m, mock, monkeypatch, limit_mb, shown):
    monkeypatch.setenv("MOODLE_MAX_DOWNLOAD_MB", limit_mb)
    mock.download = lambda _r: httpx.Response(200, headers={"content-length": str(300 * MB)}, content=b"")
    with pytest.raises(m.MoodleError) as info:
        m.download_file(_url())
    assert str(info.value) == f"File is larger than the {shown} download limit"


def test_a_refused_download_leaves_the_old_copy_untouched(m, mock, dl_dir, monkeypatch):
    path = Path(m.download_file(_url()))
    monkeypatch.setenv("MOODLE_MAX_DOWNLOAD_MB", "1")
    mock.download = lambda _r: httpx.Response(200, stream=ChunkStream(_chunks(3 * MB)))
    with pytest.raises(m.MoodleError):
        m.download_file(_url())
    assert path.read_bytes() == b"%PDF-fake"
    assert files_under(dl_dir) == [path]


JSON_TYPE = {"content-type": "application/json"}


def test_the_size_limit_still_applies_to_a_json_file_larger_than_the_sniffing_window(m, mock, dl_dir, monkeypatch):
    monkeypatch.setenv("MOODLE_MAX_DOWNLOAD_MB", "1")
    stream = ChunkStream(_chunks(3 * MB))  # not JSON at all, and far over the limit
    mock.download = lambda _r: httpx.Response(200, stream=stream, headers=JSON_TYPE)
    with pytest.raises(m.MoodleError, match="1 MB download limit") as info:
        m.download_file(_url("huge.json"))
    assert_no_leak(str(info.value))
    assert stream.produced < 64, "stopped as soon as the limit was crossed"
    assert files_under(dl_dir) == []


def test_the_size_limit_applies_to_bytes_read_while_sniffing(m, mock, dl_dir, monkeypatch):
    monkeypatch.setenv("MOODLE_MAX_DOWNLOAD_MB", "0.0001")  # 104 bytes
    mock.download = lambda _r: httpx.Response(200, content=b'{"data": "' + b"x" * 1000 + b'"}', headers=JSON_TYPE)
    with pytest.raises(m.MoodleError, match="download limit"):
        m.download_file(_url("small-but-over.json"))
    assert files_under(dl_dir) == []


def test_a_declared_json_size_over_the_limit_is_refused(m, mock, dl_dir, monkeypatch):
    monkeypatch.setenv("MOODLE_MAX_DOWNLOAD_MB", "1")
    headers = {**JSON_TYPE, "content-length": str(5 * MB)}
    mock.download = lambda _r: httpx.Response(200, headers=headers, stream=ChunkStream([b'{"a": 1}']))
    with pytest.raises(m.MoodleError, match="download limit"):
        m.download_file(_url("declared.json"))
    assert files_under(dl_dir) == []


def test_the_per_call_cap_applies_to_a_json_file_too(m, mock, dl_dir):
    mock.download = lambda _r: httpx.Response(200, stream=ChunkStream(_chunks(2 * MB)), headers=JSON_TYPE)
    with pytest.raises(m.MoodleError, match="1 MB download limit"):
        m._fetch(_url("x.json"), dl_dir.resolve() / "x.json", MB)
    assert files_under(dl_dir) == []


def test_the_deadline_still_applies_while_a_json_reply_is_being_sniffed(m, mock, dl_dir, monkeypatch):
    stream = ChunkStream((b"x" * 1024 for _ in range(50)))
    clock = SimpleNamespace(
        monotonic=lambda: 0.0 if stream.produced < 2 else 10_000.0,
        time=real_time.time,
        sleep=lambda s: None,
    )
    monkeypatch.setattr(m, "time", clock)
    mock.download = lambda _r: httpx.Response(200, stream=stream, headers=JSON_TYPE)
    with pytest.raises(m.MoodleError, match="Download took over 120 s"):
        m.download_file(_url("slow.json"))
    assert stream.produced < 50 and files_under(dl_dir) == []


def test_a_json_download_that_fails_half_way_leaves_no_part_file_and_keeps_the_old_copy(m, mock, dl_dir):
    path = Path(m.download_file(_url("data.json")))
    stream = ChunkStream([b'{"rows": [', b"1, 2, "], error=httpx.ReadError("lost"))
    mock.download = lambda _r: httpx.Response(200, stream=stream, headers=JSON_TYPE)
    with pytest.raises(m.MoodleError, match="Download failed: ReadError"):
        m.download_file(_url("data.json"))
    assert path.read_bytes() == b"%PDF-fake" and files_under(dl_dir) == [path]


# ============================================================================ the per-call cap


def test_fetch_returns_the_number_of_bytes_written(m, mock, dl_dir):
    mock.download = lambda _r: httpx.Response(200, content=b"abc" * 100)
    assert m._fetch(_url(), dl_dir.resolve() / "x.bin") == 300


@pytest.mark.parametrize(
    ("env_mb", "per_call_mb", "file_mb", "ok"),
    [
        ("1", 10, 2, False),  # the user's environment limit wins over a bigger per-call value
        ("5", 1, 2, False),  # a per-call value can only lower the limit
        ("5", 3, 2, True),
        ("1", 1, 1, True),
        ("5", None, 4, True),
        ("1", None, 2, False),
        ("1", 0, 2, False),  # 0 means "no per-call cap": the environment limit still applies
    ],
)
def test_the_per_call_cap_can_only_lower_the_users_limit(
    m, mock, dl_dir, monkeypatch, env_mb, per_call_mb, file_mb, ok
):
    monkeypatch.setenv("MOODLE_MAX_DOWNLOAD_MB", env_mb)
    mock.download = lambda _r: httpx.Response(200, stream=ChunkStream(_chunks(file_mb * MB)))
    target = dl_dir.resolve() / "x.bin"
    cap = None if per_call_mb is None else per_call_mb * MB
    if ok:
        assert m._fetch(_url(), target, cap) == file_mb * MB
    else:
        with pytest.raises(m.MoodleError, match="download limit"):
            m._fetch(_url(), target, cap)
        assert files_under(dl_dir) == []


# ================================================================================= the deadline


def test_the_deadline_is_two_minutes(m):
    assert m.DOWNLOAD_DEADLINE_S == 120


def test_a_download_that_takes_too_long_is_aborted_and_leaves_nothing(m, mock, dl_dir, monkeypatch):
    stream = ChunkStream((b"x" * 1024 for _ in range(10)))
    clock = SimpleNamespace(
        # the 3rd chunk arrives after the deadline
        monotonic=lambda: 0.0 if stream.produced < 3 else 10_000.0,
        time=real_time.time,
        sleep=lambda s: None,
    )
    monkeypatch.setattr(m, "time", clock)
    mock.download = lambda _r: httpx.Response(200, stream=stream)
    with pytest.raises(m.MoodleError) as info:
        m.download_file(_url("slow.pdf"))
    assert str(info.value) == "Download took over 120 s"
    assert_no_leak(str(info.value))
    assert stream.produced < 10, "stopped reading instead of draining the stream"
    assert files_under(dl_dir) == []


def test_a_download_within_the_deadline_is_unaffected(m, mock):
    mock.download = lambda _r: httpx.Response(200, content=b"abc")
    assert Path(m.download_file(_url("ok.bin"))).read_bytes() == b"abc"


# ====================================================================== .part and atomic replacement


def test_data_goes_to_a_part_file_that_replaces_the_target_only_at_the_end(m, mock, dl_dir):
    path = Path(m.download_file(_url()))
    target = dl_dir.resolve() / "file.pdf"
    seen = {}

    def peek(index):
        seen[index] = (Path(str(target) + ".part").exists(), target.read_bytes())

    mock.download = lambda _r: httpx.Response(200, stream=ChunkStream([b"new-1", b"new-2", b"new-3"], on_chunk=peek))
    m.download_file(_url())
    assert seen[1] == (True, b"%PDF-fake"), "while downloading, the old copy is still complete"
    assert path.read_bytes() == b"new-1new-2new-3"
    assert files_under(dl_dir) == [path]


def test_a_failed_download_keeps_the_old_copy_and_removes_the_part_file(m, mock, dl_dir):
    path = Path(m.download_file(_url()))
    stream = ChunkStream([b"new-1", b"new-2"], error=httpx.ReadError("connection lost"))
    mock.download = lambda _r: httpx.Response(200, stream=stream)
    with pytest.raises(m.MoodleError):
        m.download_file(_url())
    assert path.read_bytes() == b"%PDF-fake"
    assert files_under(dl_dir) == [path]


def test_a_json_refusal_keeps_the_old_copy(m, mock, dl_dir):
    path = Path(m.download_file(_url()))
    mock.download = lambda _r: httpx.Response(200, json={"error": "denied", "errorcode": "accessexception"})
    with pytest.raises(m.MoodleError):
        m.download_file(_url())
    assert path.read_bytes() == b"%PDF-fake" and files_under(dl_dir) == [path]


# ============================================================ where files may be written (the root)


def test_fetch_refuses_a_target_outside_the_download_dir_without_creating_anything(m, mock, tmp_path):
    outside = tmp_path / "elsewhere" / "deeper" / "x.pdf"
    with pytest.raises(ValueError, match="outside the download directory"):
        m._fetch(_url(), outside)
    assert not outside.parent.exists() and mock.download_requests() == []


def test_fetch_refuses_a_target_that_only_looks_inside(m, mock, dl_dir, tmp_path):
    sneaky = dl_dir / "a" / ".." / ".." / "escaped.pdf"
    with pytest.raises(ValueError, match="outside the download directory"):
        m._fetch(_url(), sneaky)
    assert not (tmp_path / "escaped.pdf").exists() and mock.download_requests() == []


def test_fetch_refuses_a_sibling_directory_with_the_same_prefix(m, mock, dl_dir, tmp_path):
    sibling = tmp_path / "downloads-evil" / "x.pdf"  # shares the string prefix of the download dir
    with pytest.raises(ValueError, match="outside the download directory"):
        m._fetch(_url(), sibling)
    assert not sibling.parent.exists()


def test_fetch_creates_missing_folders_inside_the_download_dir(m, mock, dl_dir):
    target = dl_dir.resolve() / "a" / "b" / "c" / "x.pdf"
    m._fetch(_url(), target)
    assert target.read_bytes() == b"%PDF-fake"


def test_a_symlinked_subfolder_pointing_out_of_the_download_dir_is_refused(m, mock, dl_dir, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    dl_dir.mkdir()
    _symlink(dl_dir / "link", outside)
    with pytest.raises(ValueError):
        m.download_file(_url(), "link")
    with pytest.raises(ValueError):
        m.download_file(_url(), "link/deeper")
    assert files_under(outside) == [] and mock.download_requests() == []


def test_a_symlink_deeper_in_the_subfolder_chain_is_refused(m, mock, dl_dir, tmp_path):
    outside = tmp_path / "outside"
    (outside / "deeper").mkdir(parents=True)
    (dl_dir / "week").mkdir(parents=True)
    _symlink(dl_dir / "week" / "x", outside)
    with pytest.raises(ValueError):
        m.download_file(_url(), "week/x/deeper")
    assert files_under(outside) == []


@pytest.mark.parametrize("name", ["file.pdf", "file.pdf.part"])
def test_a_symlinked_target_or_part_file_is_never_written_through(m, mock, dl_dir, tmp_path, name):
    victim = tmp_path / "victim.txt"
    victim.write_text("precious", encoding="utf-8")
    dl_dir.mkdir()
    _symlink(dl_dir / name, victim)
    with pytest.raises(ValueError, match="outside the download directory"):
        m.download_file(_url())
    assert victim.read_text(encoding="utf-8") == "precious"
    assert mock.download_requests() == [], "refused before the token was sent to the file URL"


def test_the_target_is_checked_again_after_its_folders_were_created(m, mock, dl_dir, tmp_path, monkeypatch):
    """A folder swapped for a symlink between the first check and the first byte must not redirect the file."""
    outside = tmp_path / "outside"
    outside.mkdir()
    dl_dir.mkdir(parents=True, exist_ok=True)  # so `week` is created by one mkdir call (no parent retry)
    week = dl_dir.resolve() / "week"
    real_mkdir = Path.mkdir
    swapped = []

    def mkdir_then_swap(self, *args, **kwargs):
        real_mkdir(self, *args, **kwargs)
        if self == week and not swapped:  # once (POSIX pathlib may call mkdir again for the parent)
            swapped.append(True)
            week.rmdir()
            _symlink(week, outside)

    monkeypatch.setattr(Path, "mkdir", mkdir_then_swap)
    with pytest.raises(ValueError, match="outside the download directory"):
        m._fetch(_url(), week / "x.pdf")
    assert files_under(outside) == [] and mock.download_requests() == []


def test_everything_that_is_written_stays_inside_the_download_dir(m, mock, dl_dir, tmp_path):
    before = set(tmp_path.rglob("*"))
    path = m.download_file(_url("a%20b.pdf"), "x/y")
    assert_inside(path, dl_dir)
    created = set(tmp_path.rglob("*")) - before
    assert all(p == dl_dir or dl_dir in p.parents for p in created), created


# ================================================================================ ?token= fallback


def test_the_fallback_uses_get_with_the_token_in_the_query(m, mock, monkeypatch):
    monkeypatch.setenv("MOODLE_DOWNLOAD_TOKEN_IN_QUERY", "1")
    mock.allow_token_in_url = True
    path = Path(m.download_file(_url()))
    assert path.read_bytes() == b"%PDF-fake"
    (request,) = mock.download_requests()
    assert request.method == "GET" and request.content == b""
    assert request.url.params["token"] == FAKE_TOKEN and str(request.url) == f"{_url()}?token={FAKE_TOKEN}"


def test_the_fallback_appends_to_an_existing_query(m, mock, monkeypatch):
    monkeypatch.setenv("MOODLE_DOWNLOAD_TOKEN_IN_QUERY", "1")
    mock.allow_token_in_url = True
    m.download_file(_url() + "?forcedownload=1")
    (request,) = mock.download_requests()
    assert str(request.url) == f"{_url()}?forcedownload=1&token={FAKE_TOKEN}"


@pytest.mark.parametrize("value", ["", "0", "true", "yes", "11", "on"])
def test_only_the_exact_value_1_enables_the_fallback(m, mock, monkeypatch, value):
    monkeypatch.setenv("MOODLE_DOWNLOAD_TOKEN_IN_QUERY", value)
    m.download_file(_url())
    (request,) = mock.download_requests()
    assert request.method == "POST" and FAKE_TOKEN not in str(request.url)


def test_the_fallback_still_checks_the_url_before_any_request(m, mock, dl_dir, monkeypatch):
    monkeypatch.setenv("MOODLE_DOWNLOAD_TOKEN_IN_QUERY", "1")
    with pytest.raises(ValueError):
        m.download_file(f"https://evil.example.com{PLUGINFILE}1/a.pdf")
    assert mock.requests == [] and not dl_dir.exists()


def test_fallback_errors_are_still_scrubbed_and_redirects_not_followed(m, mock, dl_dir, monkeypatch):
    monkeypatch.setenv("MOODLE_DOWNLOAD_TOKEN_IN_QUERY", "1")
    mock.allow_token_in_url = True
    leaky = f"{_url()}?token={FAKE_TOKEN}"
    request = httpx.Request("GET", leaky)
    for respond, message in [
        (lambda _r: httpx.ConnectError(f"failed {leaky}", request=request), "Download failed: ConnectError"),
        (lambda _r: httpx.ReadTimeout(f"slow {leaky}", request=request), "Download timed out"),
        (lambda _r: httpx.Response(500, text=leaky), "HTTP 500"),
        (lambda _r: httpx.Response(302, headers={"location": f"https://evil.example/?token={FAKE_TOKEN}"}), "HTTP 302"),
    ]:
        mock.download = respond
        with pytest.raises(m.MoodleError, match=message) as info:
            m.download_file(_url())
        assert_no_leak(str(info.value))
    # text written by the server itself is passed on, but with the token masked
    mock.download = lambda _r: httpx.Response(200, json={"error": f"bad {leaky}", "errorcode": "x"})
    with pytest.raises(m.MoodleError, match="x: ") as info:
        m.download_file(_url())
    assert_no_token(str(info.value))
    assert files_under(dl_dir) == []


def test_the_post_body_is_not_used_in_fallback_mode(m, mock, monkeypatch):
    monkeypatch.setenv("MOODLE_DOWNLOAD_TOKEN_IN_QUERY", "1")
    mock.allow_token_in_url = True
    m.download_file(_url())
    assert all(FAKE_TOKEN.encode() not in r.content for r in mock.requests)
