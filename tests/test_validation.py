"""Tool arguments are checked before anything is sent: a bad one is a ValueError (no request, no token in the
message), a good one at the edge of its range goes through."""

from __future__ import annotations

import pytest

from support import FAKE_TOKEN, FILE_URL, PLUGINFILE, TAU_URL, assert_no_token_fragment, files_under

NOT_INT = [True, False, "7", "", FAKE_TOKEN, 7.0, 7.5, float("nan"), None, [7], {"a": 7}, b"7"]
NOT_STR = [None, 5, 7.5, True, ["a"], {"a": "b"}, b"x"]
NOT_BOOL = [1, 0, 1.0, "true", "", None, [], FAKE_TOKEN]

# (name, how to call the tool with one value in that argument, lowest accepted, highest accepted)
INT_ARGS = [
    ("course_contents", lambda m, v: m.course_contents(v), 1, None),
    ("assignments", lambda m, v: m.assignments(v), 1, None),
    ("grades", lambda m, v: m.grades(v), 1, None),
    ("quizzes", lambda m, v: m.quizzes(v), 1, None),
    ("forum_discussions.course_id", lambda m, v: m.forum_discussions(v), 1, None),
    ("forum_discussions.limit", lambda m, v: m.forum_discussions(7, limit=v), 1, 50),
    ("forum_thread", lambda m, v: m.forum_thread(v), 1, None),
    ("download_course.course_id", lambda m, v: m.download_course(v), 1, None),
    ("download_course.max_mb", lambda m, v: m.download_course(7, max_mb=v), 1, 10000),
    ("upcoming_deadlines", lambda m, v: m.upcoming_deadlines(v), 1, 365),
    ("calendar_events", lambda m, v: m.calendar_events(v), 1, 365),
    ("whats_new", lambda m, v: m.whats_new(v), 1, 365),
    ("notifications.limit", lambda m, v: m.notifications(limit=v), None, None),
]
STR_ARGS = [  # (name, call, longest accepted)
    ("download_course.section", lambda m, v: m.download_course(7, section=v), 200),
    ("download_file.fileurl", lambda m, v: m.download_file(v), 2000),
    ("download_file.subfolder", lambda m, v: m.download_file(FILE_URL, subfolder=v), 200),
]
BOOL_ARGS = [
    ("list_courses.include_past", lambda m, v: m.list_courses(include_past=v)),
    ("notifications.unread_only", lambda m, v: m.notifications(unread_only=v)),
]


def bad_ints(lo, hi):
    low = [] if lo is None else [lo - 1, lo - 2, -(2**63)]
    high = [] if hi is None else [hi + 1, hi * 10, 2**63]
    return [*NOT_INT, *low, *high]


BAD = [
    *(
        pytest.param(call, bad, id=f"{name}={bad!r}"[:60])
        for name, call, lo, hi in INT_ARGS
        for bad in bad_ints(lo, hi)
    ),
    *(
        pytest.param(call, bad, id=f"{name}={str(bad)[:12]!r}")
        for name, call, size in STR_ARGS
        for bad in (*NOT_STR, "x" * (size + 1), FAKE_TOKEN * (size // 32 + 1))
    ),
    *(pytest.param(call, bad, id=f"{name}={bad!r}"[:60]) for name, call in BOOL_ARGS for bad in NOT_BOOL),
]


@pytest.mark.parametrize(("call", "bad"), BAD)
def test_a_bad_argument_raises_value_error_and_nothing_is_sent(m, mock, call, bad):
    with pytest.raises(ValueError) as info:
        call(m, bad)
    message = str(info.value)
    assert mock.requests == [], "no request, not even the identity check"
    assert FAKE_TOKEN not in message and len(message) < 100
    assert_no_token_fragment(message)
    assert not (isinstance(bad, str) and len(bad) > 5 and bad[:20] in message), "the bad value is not echoed back"


# --------------------------------------------------------------------------------------- good values


@pytest.fixture
def quiet_site(mock):
    """Every function answers an empty list (the site-info lookup keeps its real answer)."""
    mock.rest_default = []


@pytest.mark.parametrize(
    ("call", "value"),
    [
        pytest.param(call, value, id=f"{name}={value}")
        for name, call, lo, hi in INT_ARGS
        for value in sorted({lo, hi, 2**31 - 1 if hi is None else 5} - {None})
    ],
)
def test_integers_at_the_edge_of_their_range_are_accepted(m, mock, quiet_site, call, value):
    call(m, value)
    assert mock.requests, "it got as far as asking Moodle"


@pytest.mark.parametrize("value", [True, False])
def test_real_booleans_are_accepted(m, mock, quiet_site, value):
    assert m.list_courses(include_past=value) == []
    assert m.notifications(unread_only=value)["notifications"] == []


@pytest.mark.parametrize("size", [0, 1, 200])
def test_a_section_of_up_to_200_characters_is_accepted(m, mock, quiet_site, size):
    assert m.download_course(7, section="s" * size)["downloaded"] == []


def test_a_file_url_of_exactly_2000_characters_and_a_200_character_subfolder_are_accepted(m, mock, dl_dir):
    head = f"{PLUGINFILE}1/"
    tail = "/a.pdf"
    directory = "d" * (2000 - len(TAU_URL) - len(head) - len(tail))
    mock.serve({f"{head}{directory}{tail}": b"data"})
    subfolder = "./" * 99 + "ab"  # 200 characters that name one short folder
    assert len(subfolder) == 200
    path = m.download_file(f"{TAU_URL}{head}{directory}{tail}", subfolder=subfolder)
    assert [p.relative_to(dl_dir).as_posix() for p in files_under(dl_dir)] == ["ab/a.pdf"]
    assert path.startswith(str(dl_dir))


# -------------------------------------------------------------------------------- notifications.limit


@pytest.mark.parametrize(
    ("asked", "sent"), [(0, "1"), (-7, "1"), (1, "1"), (100, "100"), (1000, "100"), (2**40, "100")]
)
def test_notifications_limit_is_clamped_not_rejected(m, mock, asked, sent):
    mock.on("message_popup_get_popup_notifications", {"unreadcount": 0, "notifications": []})
    m.notifications(limit=asked)
    assert mock.forms_for("message_popup_get_popup_notifications")[0]["limit"] == sent


# ----------------------------------------------------------------------------------- the message


def test_the_message_says_which_argument_is_wrong(m):
    for call, name in [
        (lambda: m.course_contents(0), "course_id"),
        (lambda: m.forum_thread(-1), "discussion_id"),
        (lambda: m.upcoming_deadlines(366), "days"),
        (lambda: m.forum_discussions(7, 51), "limit"),
        (lambda: m.download_course(7, max_mb=0), "max_mb"),
        (lambda: m.download_course(7, section="x" * 201), "section"),
        (lambda: m.download_file("x" * 2001), "fileurl"),
        (lambda: m.download_file(FILE_URL, subfolder="x" * 201), "subfolder"),
        (lambda: m.list_courses(include_past="yes"), "include_past"),
        (lambda: m.notifications(unread_only=1), "unread_only"),
        (lambda: m.notifications(limit="5"), "limit"),
    ]:
        with pytest.raises(ValueError, match=name):
            call()
