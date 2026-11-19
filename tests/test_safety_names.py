"""moodle_safety.safe_filename and safe_subfolder: names that are safe on macOS, Windows and Linux."""

from __future__ import annotations

import os
import unicodedata
from pathlib import Path, PurePosixPath, PureWindowsPath

import pytest

import moodle_safety as s

WINDOWS_DEVICES = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}

KEPT_AS_IS = [
    "report.pdf",
    "lecture 3 (final).pptx",
    "a-b_c.tar.gz",
    "שלום עולם.pdf",
    "Café – résumé.docx",
    "README",
    "x.y.z",
    "CONSOLE.txt",  # starts with CON but is not CON
    "communication.pdf",
    "COM10.txt",  # only COM1..COM9 are devices
    "NULL.txt",
    "AUX2.pdf",
    "a..b.pdf",
    "x" * 146 + ".pdf",  # exactly 150 characters
]

HOSTILE_NAMES = [
    "G:planted.txt",
    "name:stream",
    "name.txt:Zone.Identifier",
    "name.txt::$DATA",
    "C:\\Windows\\win.ini",
    "a\x00b.txt",
    "a\x00",
    "a\nb.txt",
    "a\tb.txt",
    "a\x1bb.txt",
    "a\x7fb.txt",
    "a\x85b.txt",
    "CON",
    "con",
    "Con.tar.gz",
    "con.txt",
    "NUL",
    "nul.pdf",
    "Prn.doc",
    "AUX",
    "COM1.txt",
    "com9",
    "LPT1.txt",
    "lpt9.doc",
    "COM\u00b9.txt",
    "LPT\u00b2",
    "Aux.",
    "con .txt",
    "NUL .txt",
    "file.",
    "file. . .",
    "file   ",
    "file . ",
    ".hidden",
    "...hidden",
    ".bashrc",
    "../../etc/passwd",
    "..\\..\\evil.exe",
    "a/b/c.txt",
    "a\\b.txt",
    "/etc/passwd",
    "\\\\server\\share\\x",
    "/",
    'a*b?c"d<e>f|g.txt',
    "x" * 500,
    "x" * 500 + ".pdf",
    "ש" * 400 + ".docx",
    "😀" * 100 + ".pdf",
    "x" * 149 + ".pdf",
    "\u202egnp.exe",
    "evil\u200b.exe",
    "\ufeffbom.txt",
    "a\u2028b.txt",
    "a\u00a0b.txt",
]


def _assert_safe_name(name: str, result: str, limit: int = 150) -> None:
    where = f"{name!r} -> {result!r}"
    assert result, where
    assert not any(ch in result for ch in '/\\:*?"<>|'), where
    assert not any(unicodedata.category(ch) in ("Cc", "Cf") for ch in result), where
    assert not result.startswith("."), where
    assert not result.endswith((".", " ")), where
    assert result not in {".", ".."}, where
    assert result.split(".")[0].rstrip(" ").upper() not in WINDOWS_DEVICES, where
    assert len(result) <= limit, where
    assert len(result.encode("utf-8")) <= s.MAX_NAME_BYTES, where
    assert Path(result).name == result and os.path.basename(result) == result, where
    assert PurePosixPath(result).name == result and PureWindowsPath(result).name == result, where


@pytest.mark.parametrize("name", KEPT_AS_IS)
def test_safe_filename_keeps_ordinary_names(name):
    assert s.safe_filename(name) == name


@pytest.mark.parametrize("name", HOSTILE_NAMES)
def test_safe_filename_neutralises_hostile_names(name):
    result = s.safe_filename(name)
    _assert_safe_name(name, result)


@pytest.mark.parametrize("name", HOSTILE_NAMES)
def test_safe_filename_is_idempotent(name):
    once = s.safe_filename(name)
    assert s.safe_filename(once) == once


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("G:planted.txt", "G planted.txt"),
        ("name.txt:Zone.Identifier", "name.txt Zone.Identifier"),
        ("a/b\\c.txt", "a b c.txt"),
        ("../../etc/passwd", "etc passwd"),
        ("  spaced   out  .pdf", "spaced out .pdf"),
        ("tab\tand\nnewline.txt", "tab and newline.txt"),
        ("\u202egnp.exe", "gnp.exe"),
        ("evil\u200b.exe", "evil.exe"),
        (".hidden", "hidden"),
        ("trailing dots...", "trailing dots"),
        ("a*b?c.txt", "a b c.txt"),
        ("con", "_con"),
        ("CON.txt", "_CON.txt"),
        ("Nul.tar.gz", "_Nul.tar.gz"),
        ("com1.pdf", "_com1.pdf"),
        ("COM\u00b9.txt", "_COM\u00b9.txt"),
        ("lpt9", "_lpt9"),
        ("aux.", "_aux"),
        ("NUL .txt", "_NUL .txt"),
        ("name:stream", "name stream"),
    ],
)
def test_safe_filename_exact_results(name, expected):
    assert s.safe_filename(name) == expected


@pytest.mark.parametrize(
    "name", ["", None, ".", "..", "...", "   ", " . ", " .. ", ". . .", "\x00", "\x00\x00", "\x01\x02", "/", "::"]
)
def test_safe_filename_never_returns_an_empty_name(name):
    assert s.safe_filename(name) == "untitled"


def test_safe_filename_accepts_non_strings():
    assert s.safe_filename(12345) == "12345"
    assert s.safe_filename(0) == "untitled"


@pytest.mark.parametrize(
    ("name", "ext"),
    [
        ("x" * 500 + ".pdf", ".pdf"),
        ("ש" * 400 + ".docx", ".docx"),
        ("😀" * 100 + ".pptx", ".pptx"),
        ("y" * 300 + ".tar", ".tar"),
    ],
)
def test_safe_filename_long_names_keep_a_short_extension(name, ext):
    result = s.safe_filename(name)
    assert result.endswith(ext), result
    _assert_safe_name(name, result)


def test_safe_filename_long_extension_is_cut_with_the_stem():
    result = s.safe_filename("x" * 200 + "." + "y" * 20)
    assert len(result) == 150
    _assert_safe_name("long ext", result)


@pytest.mark.parametrize("limit", [5, 6, 10, 80, 150, 1000])
def test_safe_filename_respects_the_limit_parameter(limit):
    result = s.safe_filename("x" * 400 + ".pdf", limit)
    assert len(result) <= limit
    assert len(result.encode()) <= s.MAX_NAME_BYTES
    assert result.endswith(".pdf")


@pytest.mark.parametrize("limit", [1, 2, 3])
def test_safe_filename_tiny_limit_keeps_one_character_and_the_extension(limit):
    # below len(ext) + 1 the extension wins over the limit (at least one stem character is kept)
    assert s.safe_filename("x" * 400 + ".pdf", limit) == "x.pdf"


@pytest.mark.parametrize("limit", [0, -5])
def test_safe_filename_nonsense_limit_still_returns_a_name(limit):
    assert s.safe_filename("abcdef", limit) == "a"


@pytest.mark.parametrize("char", ["ש", "é", "😀", "\U00020000"])
def test_safe_filename_cuts_multibyte_names_on_character_boundaries(char):
    result = s.safe_filename(char * 400 + ".pdf")
    assert result.endswith(".pdf")
    assert len(result.encode("utf-8")) <= s.MAX_NAME_BYTES
    stem = result[: -len(".pdf")]
    assert stem == char * len(stem), "a character was split or replaced"
    result.encode("utf-8").decode("utf-8")  # still valid UTF-8


def test_safe_filename_leaves_room_for_the_part_suffix():
    result = s.safe_filename("😀" * 400)
    assert len((result + ".part").encode()) <= 255


@pytest.mark.parametrize("device", sorted(WINDOWS_DEVICES))
def test_safe_filename_never_returns_a_windows_device_name(device):
    for name in (device, device.lower(), device + ".txt", device.title() + ".tar.gz", device + " .pdf", device + "."):
        assert s.safe_filename(name).split(".")[0].rstrip(" ").upper() not in WINDOWS_DEVICES, name


@pytest.mark.parametrize(
    "name",
    [
        "con." + "x" * 146,
        "con." + "x" * 400,
        "NUL." + "p" * 146,
        "aux." + "x" * 400 + ".pdf",
        "COM1" + "." * 3 + "y" * 300,
    ],
)
def test_safe_filename_device_prefix_stays_within_the_limit_and_is_idempotent(name):
    result = s.safe_filename(name)
    assert result.startswith("_"), result
    assert len(result) <= 150, f"{len(result)} characters"
    assert s.safe_filename(result) == result


def test_safe_filename_device_prefix_keeps_the_extension_when_the_name_is_cut():
    result = s.safe_filename("aux." + "x" * 400 + ".pdf")
    assert result.startswith("_aux.") and result.endswith(".pdf") and len(result) == 150


@pytest.mark.parametrize("limit", [5, 8, 20, 80])
def test_safe_filename_device_prefix_respects_every_limit(limit):
    result = s.safe_filename("con." + "x" * 200, limit)
    assert result.startswith("_con") and len(result) <= limit
    assert s.safe_filename(result, limit) == result


def test_safe_filename_device_prefix_in_multibyte_names_stays_within_the_byte_cap():
    result = s.safe_filename("nul." + "ש" * 300 + ".pdf")
    assert result.startswith("_nul.") and result.endswith(".pdf")
    assert len(result.encode("utf-8")) <= s.MAX_NAME_BYTES and len(result) <= 150
    assert s.safe_filename(result) == result


# ================================================================================ safe_subfolder


@pytest.mark.parametrize(
    ("sub", "expected"),
    [
        ("", ""),
        (None, ""),
        ("Algorithms", "Algorithms"),
        ("CS101/Week 1", "CS101/Week 1"),
        ("שיעורים/שבוע 1", "שיעורים/שבוע 1"),
        ("a/b/c", "a/b/c"),
        ("a\\b", "a/b"),
        ("week1/", "week1"),
        ("a//b", "a/b"),
        ("a/./b", "a/b"),
        (".", ""),
        (" / ", ""),
        ("a/CON/b", "a/_CON/b"),
        ("a/x:y", "a/x y"),
        ("a\x00b", "a b"),
        ("a/.../b", "a/untitled/b"),
        ("~/x", "~/x"),
        ("  a  ", "a"),
    ],
)
def test_safe_subfolder_returns_a_safe_relative_path(sub, expected):
    assert s.safe_subfolder(sub) == expected


@pytest.mark.parametrize(
    "sub",
    [
        "/etc",
        "/",
        "///",
        "\\abs",
        "C:\\Users\\x",
        "C:/Users/x",
        "c:x",
        "..",
        "../x",
        "a/../b",
        "a/../../b",
        "a\\..\\b",
        "a/ .. /b",
        "a/..\\b",
        "//server/share",
        "\\\\server\\share",
    ],
)
def test_safe_subfolder_rejects_absolute_paths_drives_and_dotdot(sub):
    with pytest.raises(ValueError):
        s.safe_subfolder(sub)


@pytest.mark.parametrize("sub", [5, 1.5, b"a/b", ["a"], {"a": 1}, True])
def test_safe_subfolder_rejects_non_text(sub):
    with pytest.raises(ValueError):
        s.safe_subfolder(sub)


@pytest.mark.parametrize("sub", ["a/b", "x:y/z", "CON/NUL", "\u202e/a", "week 1 / week 2", "a/\x00/b", "ש/ש"])
def test_safe_subfolder_result_is_always_relative_and_clean(sub):
    try:
        result = s.safe_subfolder(sub)
    except ValueError:
        return
    assert not result.startswith("/") and ":" not in result and "\\" not in result
    assert not any(unicodedata.category(c) in ("Cc", "Cf") for c in result)
    for part in filter(None, result.split("/")):
        _assert_safe_name(sub, part)
