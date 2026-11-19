"""download_course end to end with a mocked course: folder layout, re-sync, limits, budget, failures, hostile names."""

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
    assert_inside,
    assert_no_leak,
    fenced,
    files_under,
    unwrap,
)

COURSE = {"id": 7, "fullname": "Algorithms: Intro/2026", "shortname": "ALG", "enddate": 0}
COURSE_FOLDER = "Algorithms Intro 2026"
OLD = 1_700_000_000  # a time in the past: files downloaded now are "newer" than this


def entry(name, tail, data=b"", *, filepath="/", modified=OLD, size=None, **extra):
    """A file entry of core_course_get_contents."""
    return {
        "type": "file",
        "filename": name,
        "filepath": filepath,
        "fileurl": f"{TAU_URL}{PLUGINFILE}{tail}",
        "filesize": len(data) if size is None else size,
        "timemodified": modified,
        **extra,
    }


def mod(mod_id, name, modname, *files):
    return {"id": mod_id, "name": name, "modname": modname, "contents": list(files)}


def setup_course(mock, sections, served=None, courses=(COURSE,)):
    mock.on("core_enrol_get_users_courses", list(courses))
    mock.on("core_course_get_contents", sections)
    if served is not None:
        mock.serve(served)


SLIDES_A, SLIDES_B = b"slides A", b"slides B, a different file with the same name"
EX1, EX2, GRAPHS = b"exercise one", b"exercise two, in a subfolder", b"graphs lecture"

STANDARD_SERVED = {
    f"{PLUGINFILE}11/slides.pdf": SLIDES_A,
    f"{PLUGINFILE}12/slides.pdf": SLIDES_B,
    f"{PLUGINFILE}13/ex1.pdf": EX1,
    f"{PLUGINFILE}13/ex2.pdf": EX2,
    f"{PLUGINFILE}21/graphs.pdf": GRAPHS,
}

STANDARD = [
    {
        "id": 1,
        "name": "Week 1",
        "modules": [
            mod(11, "Slides", "resource", entry("slides.pdf", "11/slides.pdf", SLIDES_A)),
            mod(12, "Slides again", "resource", entry("slides.pdf", "12/slides.pdf", SLIDES_B)),
            mod(
                13,
                "Exercises",
                "folder",
                entry("ex1.pdf", "13/ex1.pdf", EX1),
                entry("ex2.pdf", "13/ex2.pdf", EX2, filepath="/sub dir/"),
            ),
            mod(14, "Welcome", "label"),
            mod(15, "Reading", "page", entry("index.html", "15/index.html", b"<p>")),  # web content: not downloaded
            mod(16, "Link", "url", {"type": "url", "filename": "x", "fileurl": "https://elsewhere.example/x"}),
        ],
    },
    {
        "id": 2,
        "name": "Week 2: Graphs",
        "modules": [
            mod(21, "Lecture", "resource", entry("graphs.pdf", "21/graphs.pdf", GRAPHS)),
            mod(22, "Video", "resource", entry("v.mp4", "22/v.mp4", b"", isexternalfile=True)),
        ],
    },
]

STANDARD_PATHS = [
    "Week 1/slides.pdf",
    "Week 1/slides (12).pdf",
    "Week 1/Exercises/ex1.pdf",
    "Week 1/Exercises/sub dir/ex2.pdf",
    "Week 2 Graphs/graphs.pdf",
]
STANDARD_TOTAL = len(SLIDES_A + SLIDES_B + EX1 + EX2 + GRAPHS)


class _Raises(httpx.SyncByteStream):
    """A body that fails as soon as it is read."""

    def __init__(self, error):
        self._error = error

    def __iter__(self):
        raise self._error
        yield b""


def posix(paths):
    return [Path(p).as_posix() for p in paths]


@pytest.fixture
def course(mock, m):
    setup_course(mock, STANDARD, STANDARD_SERVED)
    return m


# ============================================================================== the happy path


def test_downloads_resources_and_folders_into_one_folder_per_section(course, mock, dl_dir):
    out = course.download_course(7)
    root = dl_dir.resolve() / COURSE_FOLDER
    assert set(out) == {"folder", "downloaded", "unchanged", "remaining", "too_large", "failed", "bytes"}
    assert out["folder"] == str(root)
    assert posix(out["downloaded"]) == STANDARD_PATHS
    assert (out["unchanged"], out["remaining"], out["too_large"], out["failed"]) == (0, 0, [], [])
    assert out["bytes"] == STANDARD_TOTAL
    assert sorted(posix(p.relative_to(root) for p in files_under(root))) == sorted(STANDARD_PATHS)
    assert (root / "Week 1" / "slides.pdf").read_bytes() == SLIDES_A
    assert (root / "Week 1" / "slides (12).pdf").read_bytes() == SLIDES_B, "same name in one section: kept apart"
    assert (root / "Week 1" / "Exercises" / "sub dir" / "ex2.pdf").read_bytes() == EX2
    assert not list(root.rglob("*.part"))


def test_labels_pages_urls_and_external_files_are_not_downloaded(course, mock):
    course.download_course(7)
    fetched = {r.url.path for r in mock.download_requests()}
    assert fetched == set(STANDARD_SERVED)


def test_the_token_goes_in_the_post_body_of_every_download_and_the_site_is_checked_once(course, mock):
    course.download_course(7)
    requests = mock.download_requests()
    assert len(requests) == 5 and {r.method for r in requests} == {"POST"}
    assert all(FAKE_TOKEN.encode() in r.content and FAKE_TOKEN not in str(r.url) for r in requests)
    assert len(mock.preflight_requests()) == 1


def test_a_second_run_skips_everything_that_is_unchanged(course, mock):
    course.download_course(7)
    before = len(mock.download_requests())
    out = course.download_course(7)
    assert (out["unchanged"], out["downloaded"], out["bytes"]) == (5, [], 0)
    assert len(mock.download_requests()) == before, "nothing is requested again"


def test_a_changed_file_is_replaced_in_place_and_the_rest_stays(course, mock, dl_dir):
    course.download_course(7)
    root = dl_dir.resolve() / COURSE_FOLDER
    new = b"slides A, second edition"
    served = dict(STANDARD_SERVED, **{f"{PLUGINFILE}11/slides.pdf": new})
    mock.serve(served)
    sections = [dict(STANDARD[0], modules=list(STANDARD[0]["modules"])), STANDARD[1]]
    future = int(real_time.time()) + 100_000
    sections[0]["modules"][0] = mod(
        11, "Slides", "resource", entry("slides.pdf", "11/slides.pdf", new, modified=future)
    )
    mock.on("core_course_get_contents", sections)
    out = course.download_course(7)
    assert posix(out["downloaded"]) == ["Week 1/slides.pdf"] and out["unchanged"] == 4
    assert (root / "Week 1" / "slides.pdf").read_bytes() == new
    assert len(files_under(root)) == 5, "replaced, not duplicated"


def test_a_newer_file_of_the_same_size_is_downloaded_again(course, mock, dl_dir):
    course.download_course(7)
    same_size = SLIDES_A[::-1]
    mock.serve(dict(STANDARD_SERVED, **{f"{PLUGINFILE}11/slides.pdf": same_size}))
    sections = [dict(STANDARD[0], modules=list(STANDARD[0]["modules"])), STANDARD[1]]
    newer = int(real_time.time()) + 100_000
    sections[0]["modules"][0] = mod(
        11, "Slides", "resource", entry("slides.pdf", "11/slides.pdf", same_size, modified=newer)
    )
    mock.on("core_course_get_contents", sections)
    out = course.download_course(7)
    assert posix(out["downloaded"]) == ["Week 1/slides.pdf"]
    assert (dl_dir.resolve() / COURSE_FOLDER / "Week 1" / "slides.pdf").read_bytes() == same_size


def test_a_third_file_with_the_same_name_gets_its_own_activity_id(mock, m, dl_dir):
    data = {i: f"copy {i}".encode() for i in (11, 12, 17)}
    sections = [
        {
            "id": 1,
            "name": "W",
            "modules": [mod(i, f"S{i}", "resource", entry("s.pdf", f"{i}/s.pdf", d)) for i, d in data.items()],
        }
    ]
    setup_course(mock, sections, {f"{PLUGINFILE}{i}/s.pdf": d for i, d in data.items()})
    out = m.download_course(7)
    assert posix(out["downloaded"]) == ["W/s.pdf", "W/s (12).pdf", "W/s (17).pdf"]


# ===================================================================================== filtering


@pytest.mark.parametrize(
    ("section", "expected"),
    [
        ("week 2", ["Week 2 Graphs/graphs.pdf"]),
        ("GRAPHS", ["Week 2 Graphs/graphs.pdf"]),
        ("Week 2: Graphs", ["Week 2 Graphs/graphs.pdf"]),
        ("week 1", STANDARD_PATHS[:4]),
        ("", STANDARD_PATHS),
        ("no such section", []),
    ],
)
def test_section_filter_is_a_case_insensitive_substring_of_the_section_name(course, section, expected):
    assert posix(course.download_course(7, section=section)["downloaded"]) == expected


def test_a_course_that_is_not_in_my_list_gets_a_generic_folder(mock, m, dl_dir):
    setup_course(mock, STANDARD, STANDARD_SERVED, courses=[])
    out = m.download_course(99)
    assert out["folder"] == str(dl_dir.resolve() / "course 99") and len(out["downloaded"]) == 5


def test_a_finished_course_is_still_found_by_id(mock, m, dl_dir):
    setup_course(mock, STANDARD, STANDARD_SERVED, courses=[dict(COURSE, enddate=1)])
    assert m.download_course(7)["folder"] == str(dl_dir.resolve() / COURSE_FOLDER)


def test_an_empty_course_creates_nothing(mock, m, dl_dir):
    setup_course(mock, [], {})
    out = m.download_course(7)
    assert out == {
        "folder": str(dl_dir.resolve() / COURSE_FOLDER),
        "downloaded": [],
        "unchanged": 0,
        "remaining": 0,
        "too_large": [],
        "failed": [],
        "bytes": 0,
    }
    assert not dl_dir.exists()


# ==================================================================================== size limits


def test_files_over_max_mb_are_listed_and_never_requested(mock, m, dl_dir):
    sections = [
        {
            "id": 1,
            "name": "W",
            "modules": [
                mod(1, "Big", "resource", entry("huge.iso", "1/huge.iso", size=300 * MB)),
                mod(2, "Edge", "resource", entry("edge.bin", "2/edge.bin", size=200 * MB)),
                mod(3, "Fine", "resource", entry("ok.pdf", "3/ok.pdf", b"ok")),
            ],
        }
    ]
    served = {f"{PLUGINFILE}3/ok.pdf": b"ok", f"{PLUGINFILE}2/edge.bin": b"e"}
    setup_course(mock, sections, served)
    out = m.download_course(7)
    assert out["too_large"] == [{"filename": fenced("huge.iso", "file.name"), "mb": 300}]
    assert posix(out["downloaded"]) == ["W/edge.bin", "W/ok.pdf"], "exactly max_mb is allowed"
    assert {r.url.path.rsplit("/", 1)[-1] for r in mock.download_requests()} == {"edge.bin", "ok.pdf"}


def test_max_mb_is_a_per_call_limit(course, mock):
    sections = [{"id": 1, "name": "W", "modules": [mod(1, "B", "resource", entry("b.bin", "1/b.bin", size=2 * MB))]}]
    mock.on("core_course_get_contents", sections)
    assert course.download_course(7, max_mb=1)["too_large"] == [{"filename": fenced("b.bin", "file.name"), "mb": 2}]
    assert mock.download_requests() == []


@pytest.mark.parametrize(("env_mb", "max_mb"), [("200", 1), ("1", 200), ("1", 1)])
def test_a_file_that_is_bigger_than_its_listing_claims_is_stopped_by_the_limit(
    mock, m, dl_dir, monkeypatch, env_mb, max_mb
):
    monkeypatch.setenv("MOODLE_MAX_DOWNLOAD_MB", env_mb)
    sections = [{"id": 1, "name": "W", "modules": [mod(1, "B", "resource", entry("b.bin", "1/b.bin", size=10))]}]
    setup_course(mock, sections, {f"{PLUGINFILE}1/b.bin": b"x" * (2 * MB)})
    out = m.download_course(7, max_mb=max_mb)
    assert out["downloaded"] == [] and out["bytes"] == 0
    assert [unwrap(e["filename"]) for e in out["failed"]] == [("file.name", "b.bin")]
    assert "1 MB download limit" in unwrap(out["failed"][0]["error"])[1]
    assert files_under(dl_dir) == []


# ======================================================================================= budget


def test_a_spent_time_budget_downloads_nothing_and_counts_what_is_left(course, mock, monkeypatch):
    monkeypatch.setenv("MOODLE_DOWNLOAD_SECONDS", "0")
    ticks = iter(range(10_000))  # the clock moves on every look, whatever its real resolution
    clock = SimpleNamespace(time=lambda: float(next(ticks)), monotonic=real_time.monotonic, sleep=real_time.sleep)
    monkeypatch.setattr(course, "time", clock)
    out = course.download_course(7)
    assert (out["downloaded"], out["remaining"], out["bytes"]) == ([], 5, 0)
    assert mock.download_requests() == []


def test_running_out_of_time_part_way_then_calling_again_finishes_the_job(course, mock, monkeypatch):
    clock = SimpleNamespace(
        time=lambda: real_time.time() + (10_000 if mock.download_requests() else 0),  # jumps after the first file
        monotonic=real_time.monotonic,
        sleep=real_time.sleep,
    )
    monkeypatch.setattr(course, "time", clock)
    first = course.download_course(7)
    assert posix(first["downloaded"]) == STANDARD_PATHS[:1] and first["remaining"] == 4
    monkeypatch.setattr(course, "time", real_time)
    second = course.download_course(7)
    assert second["unchanged"] == 1 and posix(second["downloaded"]) == STANDARD_PATHS[1:] and second["remaining"] == 0


@pytest.mark.parametrize("value", ["abc", "soon", "-1", "-0.5", "3600.5", "99999", "nan", "inf", "1,5"])
def test_a_bad_download_budget_is_a_config_error_and_nothing_is_downloaded(course, mock, monkeypatch, value):
    monkeypatch.setenv("MOODLE_DOWNLOAD_SECONDS", value)
    with pytest.raises(course.ConfigError, match="MOODLE_DOWNLOAD_SECONDS"):
        course.download_course(7)
    assert mock.download_requests() == []


@pytest.mark.parametrize("value", ["", "  ", "45", "1.5", "3600", " 10 "])
def test_a_valid_or_blank_download_budget_is_accepted(course, monkeypatch, value):
    monkeypatch.setenv("MOODLE_DOWNLOAD_SECONDS", value)
    assert len(course.download_course(7)["downloaded"]) == 5


# ====================================================================================== failures


def test_failed_files_are_reported_with_short_safe_messages_and_do_not_stop_the_rest(mock, m, dl_dir):
    leaky = f"{TAU_URL}{PLUGINFILE}1/net.pdf?token={FAKE_TOKEN}"
    sections = [
        {
            "id": 1,
            "name": "W",
            "modules": [
                mod(1, "ok", "resource", entry("ok.pdf", "1/ok.pdf", b"fine")),
                mod(2, "gone", "resource", entry("gone.pdf", "1/gone.pdf", b"x")),
                mod(3, "net", "resource", entry("net.pdf", "1/net.pdf", b"x")),
                mod(4, "tok", "resource", entry("tok.pdf", "1/tok.pdf", b"x")),
                mod(
                    5,
                    "evil",
                    "resource",
                    entry(
                        "evil.pdf",
                        "1/evil.pdf",
                        b"x",
                        fileurl="https://evil.example.com/webservice/pluginfile.php/1/evil.pdf",
                    ),
                ),
                mod(6, "last", "resource", entry("last.pdf", "1/last.pdf", b"fine too")),
            ],
        }
    ]
    served = {
        f"{PLUGINFILE}1/ok.pdf": b"fine",
        f"{PLUGINFILE}1/last.pdf": b"fine too",
        f"{PLUGINFILE}1/gone.pdf": httpx.Response(404, text=f"missing {leaky}"),
        f"{PLUGINFILE}1/net.pdf": httpx.Response(200, stream=_Raises(httpx.ReadError(f"lost {leaky}"))),
        f"{PLUGINFILE}1/tok.pdf": httpx.Response(
            200, json={"exception": "x", "errorcode": "invalidtoken", "message": leaky}
        ),
    }
    setup_course(mock, sections, served)
    out = m.download_course(7)
    assert posix(out["downloaded"]) == ["W/ok.pdf", "W/last.pdf"]
    errors = {unwrap(e["filename"])[1]: unwrap(e["error"])[1] for e in out["failed"]}
    assert list(errors) == ["gone.pdf", "net.pdf", "tok.pdf", "evil.pdf"]
    assert errors["gone.pdf"] == "Moodle answered HTTP 404 for this file"
    assert errors["net.pdf"] == "Download failed: ReadError"
    assert "invalidtoken" in errors["tok.pdf"]
    assert errors["evil.pdf"] == "Only file URLs on your Moodle host are allowed"
    for text in errors.values():
        assert len(text) <= 150
        assert_no_leak(text)
    assert mock.foreign_requests() == [], "a file URL on another host is never contacted"
    assert sorted(p.name for p in files_under(dl_dir)) == ["last.pdf", "ok.pdf"]


def test_an_invalid_token_stops_the_listing_itself(mock, m):
    mock.fail_all({"exception": "moodle_exception", "errorcode": "invalidtoken", "message": "bad"})
    with pytest.raises(m.MoodleError) as info:
        m.download_course(7)
    assert info.value.errorcode == "invalidtoken" and FAKE_TOKEN not in str(info.value)


# =========================================================================== hostile course data


def test_hostile_names_in_the_course_listing_cannot_leave_the_download_dir(mock, m, dl_dir, tmp_path):
    hostile = dict(COURSE, fullname="../../outside")
    sections = [
        {
            "id": 1,
            "name": "../../escape",
            "modules": [
                mod(1, "..", "folder", entry("../../evil.pdf", "1/a.pdf", b"a", filepath="/../../x/")),
                mod(2, "C:\\Windows", "folder", entry("CON.txt", "2/b.pdf", b"b", filepath="\\..\\..\\y\\")),
                mod(3, "r", "resource", entry("a:b*c?.pdf", "3/c.pdf", b"c")),
                mod(4, "r", "resource", entry("/etc/passwd", "4/d.pdf", b"d")),
            ],
        },
        {"id": 2, "name": "CON", "modules": [mod(5, "r", "resource", entry("NUL", "5/e.pdf", b"e"))]},
    ]
    served = {f"{PLUGINFILE}{i}/{n}.pdf": n.encode() for i, n in ((1, "a"), (2, "b"), (3, "c"), (4, "d"), (5, "e"))}
    setup_course(mock, sections, served, courses=[hostile])
    out = m.download_course(7)
    assert out["failed"] == [] and len(out["downloaded"]) == 5
    assert {p.resolve() for p in tmp_path.iterdir()} == {dl_dir.resolve()}, (
        "nothing was created beside the download dir"
    )
    for path in files_under(dl_dir):
        assert_inside(path, dl_dir)
        assert ".." not in path.relative_to(dl_dir).parts
        assert path.stem.upper() not in {"CON", "NUL"} and ":" not in path.name
    assert (
        dl_dir / "outside" / "escape" / "untitled" / "untitled" / "untitled" / "x" / "evil.pdf"
    ).read_bytes() == b"a"
    assert (dl_dir / "outside" / "_CON" / "_NUL").is_file()


def test_a_symlinked_section_folder_is_never_written_through(mock, m, dl_dir, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (dl_dir / COURSE_FOLDER).mkdir(parents=True)
    try:
        (dl_dir / COURSE_FOLDER / "Week 1").symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks are not available for this account")
    setup_course(mock, [STANDARD[0]], STANDARD_SERVED)
    out = m.download_course(7)
    assert files_under(outside) == []
    assert out["downloaded"] == [] and out["bytes"] == 0
    assert [r.url.path for r in mock.download_requests()] == [], "nothing under the link is even requested"
