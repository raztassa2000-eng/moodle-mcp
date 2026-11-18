"""Settings read from the environment: get_base, token_file, download_dir, max_download_bytes, token, scrub."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from support import FAKE_TOKEN, ROOT, assert_no_token_fragment

# ===================================================================================== error types


def test_error_types(m):
    assert issubclass(m.ConfigError, RuntimeError)
    assert issubclass(m.MoodleError, RuntimeError)
    assert not issubclass(m.ConfigError, m.MoodleError)
    assert m.MoodleError("boom").errorcode is None
    err = m.MoodleError("boom", "somecode")
    assert (str(err), err.errorcode) == ("boom", "somecode")


# ========================================================================================= get_base


def test_get_base_defaults_to_tau(m, monkeypatch):
    monkeypatch.delenv("MOODLE_SITE")
    assert m.get_base() == "https://moodle.tau.ac.il"


def test_every_preset_resolves_to_its_url(m, monkeypatch):
    for key, (_name, url) in m.SITES.items():
        monkeypatch.setenv("MOODLE_SITE", key)
        assert m.get_base() == url, key


@pytest.mark.parametrize("value", ["BGU", " bgu ", "Bgu\n"])
def test_site_key_is_case_and_space_insensitive(m, monkeypatch, value):
    monkeypatch.setenv("MOODLE_SITE", value)
    assert m.get_base() == "https://moodle.bgu.ac.il/moodle"


@pytest.mark.parametrize("value", ["", "   "])
def test_blank_site_means_tau(m, monkeypatch, value):
    monkeypatch.setenv("MOODLE_SITE", value)
    assert m.get_base() == "https://moodle.tau.ac.il"


def test_get_base_is_read_on_every_call(m, monkeypatch):
    first = m.get_base()
    monkeypatch.setenv("MOODLE_SITE", "technion")
    assert m.get_base() != first
    monkeypatch.setenv("MOODLE_SITE", "tau")
    assert m.get_base() == first


def test_unknown_site_names_the_options(m, monkeypatch):
    monkeypatch.setenv("MOODLE_SITE", "bogus")
    with pytest.raises(m.ConfigError) as info:
        m.get_base()
    message = str(info.value)
    assert "bogus" in message and "tau" in message and "technion" in message and "MOODLE_URL" in message


def test_unknown_site_message_truncates_a_huge_value(m, monkeypatch):
    monkeypatch.setenv("MOODLE_SITE", "z" * 5000)
    with pytest.raises(m.ConfigError) as info:
        m.get_base()
    assert "z" * 100 not in str(info.value)


def test_moodle_url_overrides_site_even_an_unknown_one(m, monkeypatch):
    monkeypatch.setenv("MOODLE_SITE", "bogus")
    monkeypatch.setenv("MOODLE_URL", " https://LMS.Example.edu/moodle/ ")
    assert m.get_base() == "https://lms.example.edu/moodle"


@pytest.mark.parametrize("blank", ["", "  ", "\t"])
def test_blank_moodle_url_falls_back_to_the_site(m, monkeypatch, blank):
    monkeypatch.setenv("MOODLE_URL", blank)
    assert m.get_base() == "https://moodle.tau.ac.il"


@pytest.mark.parametrize(
    "bad",
    [
        "http://moodle.example.edu",
        "https://u:S3cr3tPw@moodle.example.edu",
        "https://moodle.example.edu/?a=1",
        "https://moodle.example.edu/#x",
        "https://moodle.example.edu:abc",
        "https://moodle.example.edu/a/../b",
        "ftp://moodle.example.edu",
        "junk",
    ],
)
def test_invalid_moodle_url_is_a_config_error_that_does_not_echo_it(m, monkeypatch, bad):
    monkeypatch.setenv("MOODLE_URL", bad)
    with pytest.raises(m.ConfigError) as info:
        m.get_base()
    assert "Invalid MOODLE_URL" in str(info.value)
    assert "S3cr3tPw" not in str(info.value)


# ====================================================================================== token_file


def test_token_file_default_is_dot_token_next_to_the_script(m, monkeypatch):
    monkeypatch.delenv("MOODLE_TOKEN_FILE")
    path = m.token_file()  # only computes the path: the real file is never opened
    assert path == ROOT / ".token"
    assert path.parent == Path(m.__file__).resolve().parent


@pytest.mark.parametrize("blank", ["", "   "])
def test_token_file_blank_env_means_default(m, monkeypatch, blank):
    monkeypatch.setenv("MOODLE_TOKEN_FILE", blank)
    assert m.token_file() == ROOT / ".token"


def test_token_file_from_env_expands_the_home_directory(m, monkeypatch, tmp_path):
    for var in ("HOME", "USERPROFILE"):
        monkeypatch.setenv(var, str(tmp_path))
    monkeypatch.setenv("MOODLE_TOKEN_FILE", "~/tok.txt")
    assert m.token_file() == tmp_path / "tok.txt"


# ===================================================================================== download_dir


def test_download_dir_is_the_resolved_env_path(m, dl_dir):
    assert m.download_dir() == dl_dir.resolve()
    assert m.download_dir().is_absolute()


def test_download_dir_does_not_create_anything(m, dl_dir):
    m.download_dir()
    assert not dl_dir.exists()


def test_download_dir_default_is_downloads_moodle_in_the_home(m, monkeypatch, tmp_path):
    for var in ("HOME", "USERPROFILE"):
        monkeypatch.setenv(var, str(tmp_path))
    monkeypatch.delenv("MOODLE_DOWNLOAD_DIR")
    assert m.download_dir() == (tmp_path / "Downloads" / "moodle").resolve()


def test_download_dir_blank_env_means_default(m, monkeypatch, tmp_path):
    for var in ("HOME", "USERPROFILE"):
        monkeypatch.setenv(var, str(tmp_path))
    monkeypatch.setenv("MOODLE_DOWNLOAD_DIR", "  ")
    assert m.download_dir() == (tmp_path / "Downloads" / "moodle").resolve()


def test_download_dir_relative_path_is_resolved_against_the_cwd(m, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MOODLE_DOWNLOAD_DIR", "rel/dl")
    assert m.download_dir() == (tmp_path / "rel" / "dl").resolve()


def test_download_dir_expands_the_home_directory(m, monkeypatch, tmp_path):
    for var in ("HOME", "USERPROFILE"):
        monkeypatch.setenv(var, str(tmp_path))
    monkeypatch.setenv("MOODLE_DOWNLOAD_DIR", "~/moodle-files")
    assert m.download_dir() == (tmp_path / "moodle-files").resolve()


# ============================================================================== max_download_bytes


def test_max_download_bytes_default_is_200_mb(m):
    assert m.max_download_bytes() == 200 * 1024 * 1024


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("5", 5 * 1024 * 1024),
        (" 7 ", 7 * 1024 * 1024),
        ("0.5", 512 * 1024),
        ("1e2", 100 * 1024 * 1024),
        ("100000", 100_000 * 1024 * 1024),
        ("", 200 * 1024 * 1024),
    ],
)
def test_max_download_bytes_from_env(m, monkeypatch, raw, expected):
    monkeypatch.setenv("MOODLE_MAX_DOWNLOAD_MB", raw)
    assert m.max_download_bytes() == expected


@pytest.mark.parametrize("raw", ["lots", "5MB", "1,5", "0", "-1", "-0.5", "100000.5", "1e9", "nan", "inf", "-inf"])
def test_max_download_bytes_junk_or_out_of_range_is_a_config_error(m, monkeypatch, raw):
    monkeypatch.setenv("MOODLE_MAX_DOWNLOAD_MB", raw)
    with pytest.raises(m.ConfigError, match="MOODLE_MAX_DOWNLOAD_MB"):
        m.max_download_bytes()


# ================================================================================== download_seconds


def test_download_seconds_default_is_45(m):
    assert m.download_seconds() == 45


@pytest.mark.parametrize(
    ("raw", "expected"), [("0", 0), ("1.5", 1.5), (" 7 ", 7), ("3600", 3600), ("1e2", 100), ("", 45), ("  ", 45)]
)
def test_download_seconds_from_env(m, monkeypatch, raw, expected):
    monkeypatch.setenv("MOODLE_DOWNLOAD_SECONDS", raw)
    assert m.download_seconds() == expected


@pytest.mark.parametrize("raw", ["abc", "soon", "1,5", "5s", "-1", "-0.001", "3600.5", "99999", "nan", "inf", "-inf"])
def test_download_seconds_junk_or_out_of_range_is_a_config_error(m, monkeypatch, raw):
    monkeypatch.setenv("MOODLE_DOWNLOAD_SECONDS", raw)
    with pytest.raises(m.ConfigError, match="MOODLE_DOWNLOAD_SECONDS") as info:
        m.download_seconds()
    assert isinstance(info.value, RuntimeError) and not isinstance(info.value, ValueError)


# ========================================================================================== token()

BAD_TOKENS = [
    pytest.param("a" * 15, id="too-short"),
    pytest.param("a" * 129, id="too-long"),
    pytest.param("abcd-efgh-ijkl-mnop-qrst-uvwx", id="dashes"),
    pytest.param("abcdefgh_ijklmnop_qrstuvwx", id="underscores"),
    pytest.param("abcdefgh ijklmnop qrstuvwx", id="inner-space"),
    pytest.param("Bearer a1b2c3d4e5f60718293a4b5c6d7e8f90", id="bearer-prefix"),
    pytest.param("token=a1b2c3d4e5f60718293a4b5c6d7e8f90", id="key-value"),
    pytest.param('"a1b2c3d4e5f60718293a4b5c6d7e8f90"', id="quoted"),
    pytest.param("a1b2c3d4e5f60718293a4b5c6d7e8f9١", id="non-ascii-digit"),
    pytest.param("ünïcödé-token-0123456789abcdef", id="unicode"),
    pytest.param("a1b2c3d4e5f60718\n293a4b5c6d7e8f90", id="inner-newline"),
]  # (a NUL cannot even be put in an environment variable; the token-file tests cover it)


@pytest.mark.parametrize("value", ["a" * 16, "a" * 128, FAKE_TOKEN, "A1b2C3d4E5f6G7h8", "0" * 40])
def test_token_accepts_alphanumeric_16_to_128(m, monkeypatch, value):
    monkeypatch.setenv("MOODLE_TOKEN", value)
    assert m.token() == value


def test_token_from_env_is_stripped(m, monkeypatch):
    monkeypatch.setenv("MOODLE_TOKEN", f"  {FAKE_TOKEN}\r\n")
    assert m.token() == FAKE_TOKEN


@pytest.mark.parametrize("value", BAD_TOKENS)
def test_bad_token_in_env_is_rejected_without_leaking_it(m, monkeypatch, value):
    monkeypatch.setenv("MOODLE_TOKEN", value)
    with pytest.raises(m.ConfigError) as info:
        m.token()
    for text in (str(info.value), repr(info.value)):
        assert value.strip() not in text
        for i in range(max(0, len(value) - 11)):
            assert value[i : i + 12] not in text, "token fragment leaked into the error"


def test_missing_token_names_the_file_but_never_a_token(m, monkeypatch):
    monkeypatch.delenv("MOODLE_TOKEN")
    with pytest.raises(m.ConfigError, match="No Moodle token") as info:
        m.token()
    assert "MOODLE_TOKEN" in str(info.value) and str(m.token_file()) in str(info.value)


@pytest.mark.parametrize("blank", ["", "   ", "\r\n"])
def test_blank_env_token_means_missing(m, monkeypatch, blank):
    monkeypatch.setenv("MOODLE_TOKEN", blank)
    with pytest.raises(m.ConfigError, match="No Moodle token"):
        m.token()


def _use_file(monkeypatch, path: Path, content: bytes) -> None:
    path.write_bytes(content)
    monkeypatch.delenv("MOODLE_TOKEN")
    monkeypatch.setenv("MOODLE_TOKEN_FILE", str(path))


@pytest.mark.parametrize(
    "content",
    [
        pytest.param(FAKE_TOKEN.encode(), id="plain"),
        pytest.param(FAKE_TOKEN.encode() + b"\n", id="lf"),
        pytest.param(FAKE_TOKEN.encode() + b"\r\n", id="crlf"),
        pytest.param(b"\n  " + FAKE_TOKEN.encode() + b"  \n\n", id="padded"),
        pytest.param(b"\xef\xbb\xbf" + FAKE_TOKEN.encode() + b"\r\n", id="utf8-bom"),
        pytest.param((FAKE_TOKEN + "\r\n").encode("utf-16"), id="utf16-powershell-5"),
        pytest.param(b"\xff\xfe" + FAKE_TOKEN.encode("utf-16-le"), id="utf16-le-bom"),
        pytest.param(b"\xfe\xff" + FAKE_TOKEN.encode("utf-16-be") + b"\x00\n", id="utf16-be-bom"),
    ],
)
def test_token_file_encodings(m, monkeypatch, tmp_path, content):
    _use_file(monkeypatch, tmp_path / "tok.txt", content)
    assert m.token() == FAKE_TOKEN


def test_token_file_is_used_when_env_is_empty(m, monkeypatch, tmp_path):
    (tmp_path / "tok.txt").write_text(FAKE_TOKEN + "\n", encoding="utf-8")
    monkeypatch.setenv("MOODLE_TOKEN", "")
    monkeypatch.setenv("MOODLE_TOKEN_FILE", str(tmp_path / "tok.txt"))
    assert m.token() == FAKE_TOKEN


def test_token_env_wins_over_the_file(m, monkeypatch, tmp_path):
    (tmp_path / "tok.txt").write_text("f" * 32, encoding="utf-8")
    monkeypatch.setenv("MOODLE_TOKEN_FILE", str(tmp_path / "tok.txt"))
    assert m.token() == FAKE_TOKEN


@pytest.mark.parametrize(
    "content",
    [
        pytest.param(b"short", id="short"),
        pytest.param(b"has-dashes-0123456789abcdef", id="dashes"),
        pytest.param(b"a1b2c3d4e5f60718\x00293a4b5c6d7e8f90", id="nul"),
        pytest.param(FAKE_TOKEN.encode() + b"\n# my token\n", id="comment-line"),
        pytest.param(FAKE_TOKEN.encode() + b"\n" + FAKE_TOKEN.encode(), id="two-lines"),
        pytest.param(b"token=" + FAKE_TOKEN.encode(), id="key-value"),
    ],
)
def test_bad_token_file_content_is_rejected_without_leaking_it(m, monkeypatch, tmp_path, content):
    _use_file(monkeypatch, tmp_path / "tok.txt", content)
    with pytest.raises(m.ConfigError) as info:
        m.token()
    assert_no_token_fragment(str(info.value))
    assert content.decode("utf-8", errors="replace").strip() not in str(info.value)


@pytest.mark.parametrize("content", [b"", b"\n\n", b"  \r\n", b"\xef\xbb\xbf", b"\xff\xfe"])
def test_empty_token_file_means_missing(m, monkeypatch, tmp_path, content):
    _use_file(monkeypatch, tmp_path / "tok.txt", content)
    with pytest.raises(m.ConfigError, match="No Moodle token"):
        m.token()


@pytest.mark.parametrize("content", [b"\x80\x81\x82abc", b"\xff\xfeA", b"\xfe\xffA"])
def test_undecodable_token_file_is_a_config_error(m, monkeypatch, tmp_path, content):
    _use_file(monkeypatch, tmp_path / "tok.txt", content)
    with pytest.raises(m.ConfigError, match="not valid text"):
        m.token()


def test_unreadable_token_file_is_a_config_error_with_the_exception_name_only(m, monkeypatch, tmp_path):
    folder = tmp_path / "a-directory"
    folder.mkdir()
    monkeypatch.delenv("MOODLE_TOKEN")
    monkeypatch.setenv("MOODLE_TOKEN_FILE", str(folder))
    with pytest.raises(m.ConfigError, match="Cannot read the token file") as info:
        m.token()
    assert "Error" in str(info.value)  # PermissionError / IsADirectoryError, never a traceback text


# ----------------------------------------------------------------------- group/world readable warning


def _as_posix(monkeypatch, m):
    """Run the module's POSIX-only permission check on any OS (it only looks at os.name and st_mode)."""
    monkeypatch.setattr(m, "os", SimpleNamespace(environ=os.environ, name="posix"))


def _fake_mode(monkeypatch, path: Path, mode: int) -> None:
    real = Path.stat

    def stat(self, **kwargs):
        st = real(self, **kwargs)
        if self == path:
            values = list(st)
            values[0] = mode
            return os.stat_result(values)
        return st

    monkeypatch.setattr(Path, "stat", stat)


@pytest.mark.parametrize("mode", [0o100644, 0o100640, 0o100604, 0o100666, 0o100660])
def test_open_token_file_warns_once_on_stderr_and_still_works(m, monkeypatch, tmp_path, capsys, mode):
    path = tmp_path / "tok.txt"
    _use_file(monkeypatch, path, FAKE_TOKEN.encode())
    _as_posix(monkeypatch, m)
    _fake_mode(monkeypatch, path, mode)
    assert m.token() == FAKE_TOKEN
    assert m.token() == FAKE_TOKEN
    out, err = capsys.readouterr()
    assert out == ""
    assert err.count("WARNING") == 1 and "chmod 600" in err and str(path) in err
    assert FAKE_TOKEN not in err


@pytest.mark.parametrize("mode", [0o100600, 0o100400, 0o100700])
def test_private_token_file_does_not_warn(m, monkeypatch, tmp_path, capsys, mode):
    path = tmp_path / "tok.txt"
    _use_file(monkeypatch, path, FAKE_TOKEN.encode())
    _as_posix(monkeypatch, m)
    _fake_mode(monkeypatch, path, mode)
    assert m.token() == FAKE_TOKEN
    assert capsys.readouterr() == ("", "")


def test_no_permission_warning_for_an_env_token_or_an_empty_file(m, monkeypatch, tmp_path, capsys):
    path = tmp_path / "tok.txt"
    _as_posix(monkeypatch, m)
    _fake_mode(monkeypatch, path, 0o100666)
    path.write_bytes(b"")
    monkeypatch.setenv("MOODLE_TOKEN_FILE", str(path))
    assert m.token() == FAKE_TOKEN  # the env token wins; the file is not even read
    monkeypatch.delenv("MOODLE_TOKEN")
    with pytest.raises(m.ConfigError):
        m.token()
    assert capsys.readouterr() == ("", "")


def test_no_permission_warning_off_posix(m, monkeypatch, tmp_path, capsys):
    path = tmp_path / "tok.txt"
    _use_file(monkeypatch, path, FAKE_TOKEN.encode())
    monkeypatch.setattr(m, "os", SimpleNamespace(environ=os.environ, name="nt"))
    _fake_mode(monkeypatch, path, 0o100666)
    assert m.token() == FAKE_TOKEN
    assert capsys.readouterr() == ("", "")


def test_a_stat_error_during_the_permission_check_is_not_fatal(m, monkeypatch, tmp_path, capsys):
    path = tmp_path / "tok.txt"
    _use_file(monkeypatch, path, FAKE_TOKEN.encode())
    _as_posix(monkeypatch, m)
    real = Path.stat

    def stat(self, **kwargs):
        if self == path:
            raise PermissionError("denied")
        return real(self, **kwargs)

    monkeypatch.setattr(Path, "stat", stat)
    assert m.token() == FAKE_TOKEN
    assert capsys.readouterr() == ("", "")


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permission bits")
def test_real_group_readable_file_warns_and_chmod_600_silences_it(m, monkeypatch, tmp_path, capsys):
    path = tmp_path / "tok.txt"
    _use_file(monkeypatch, path, FAKE_TOKEN.encode())
    path.chmod(0o644)
    assert m.token() == FAKE_TOKEN
    assert "chmod 600" in capsys.readouterr().err
    m._perm_warned.clear()
    path.chmod(0o600)
    assert m.token() == FAKE_TOKEN
    assert capsys.readouterr() == ("", "")


# ========================================================================================== scrub()

SECRET = "SECRETVALUE0123456789"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (f"https://x.example/y?token={SECRET}&a=1", "https://x.example/y?token=***&a=1"),
        (f"wstoken={SECRET}", "wstoken=***"),
        (f"boom wstoken={SECRET} bam", "boom wstoken=*** bam"),
        (f"token={SECRET}", "token=***"),
        (f"WSTOKEN={SECRET}", "WSTOKEN=***"),
        (f"Token={SECRET}", "Token=***"),
        (f"POST /server.php?wstoken={SECRET}&wsfunction=x", "POST /server.php?wstoken=***&wsfunction=x"),
        (f'<a href="x?token={SECRET}">link</a>', '<a href="x?token=***">link</a>'),
        (f"'token={SECRET}'", "'token=***'"),
        (f"a=1&token={SECRET}", "a=1&token=***"),
        ("token=", "token=***"),
    ],
)
def test_scrub_keeps_the_key_and_masks_the_value(m, text, expected):
    assert m.scrub(text) == expected
    assert SECRET not in m.scrub(text)


def test_scrub_masks_the_real_token_everywhere(m):
    assert m.scrub(FAKE_TOKEN) == "***"
    assert m.scrub(f"a {FAKE_TOKEN} b {FAKE_TOKEN}") == "a *** b ***"
    assert m.scrub(f"wstoken={FAKE_TOKEN}") == "wstoken=***"
    assert m.scrub(f"https://moodle.tau.ac.il/x?token={FAKE_TOKEN}&y=1") == "https://moodle.tau.ac.il/x?token=***&y=1"
    assert m.scrub(f"glued{FAKE_TOKEN}glued") == "glued***glued"


def test_scrub_is_idempotent(m):
    once = m.scrub(f"token={FAKE_TOKEN} wstoken=OTHER1234567890 {FAKE_TOKEN}")
    assert m.scrub(once) == once


@pytest.mark.parametrize(
    "text", ["Connection refused", "", "http://moodle.tau.ac.il/x?a=1&b=2", "a=b token", "no secret"]
)
def test_scrub_leaves_clean_text_alone(m, text):
    assert m.scrub(text) == text


@pytest.mark.parametrize("value", [123, None, ValueError("x"), b"bytes"])
def test_scrub_accepts_non_strings(m, value):
    assert m.scrub(value) == str(value)


def test_scrub_stringifies_an_exception_that_contains_the_token(m):
    out = m.scrub(RuntimeError(f"failed with {FAKE_TOKEN}"))
    assert FAKE_TOKEN not in out and "***" in out


def test_scrub_never_raises_when_the_token_is_missing_or_malformed(m, monkeypatch):
    monkeypatch.delenv("MOODLE_TOKEN")
    assert m.scrub("plain") == "plain"
    assert SECRET not in m.scrub(f"token={SECRET}")
    monkeypatch.setenv("MOODLE_TOKEN", "not a valid token!!")
    assert m.scrub("plain") == "plain"
    assert m.scrub("not a valid token!!") == "not a valid token!!"
