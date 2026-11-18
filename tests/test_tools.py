"""Smoke tests for the upstream tools (unchanged by this PR), part 1: the registry, courses, contents, deadlines,
assignments, grades, forums and quizzes. Each checks the shape of the result for a small hand-made Moodle payload;
the per-test wire audit (conftest) checks that requests only go to the configured host with the token in POST bodies."""

from __future__ import annotations

import asyncio
import time

import pytest

from support import (
    DAY,
    FILE_URL,
    NOW,
    OLD,
    TAU_URL,
    TOOLS,
    courses,
    stamp,
)


# ============================================================================== the tool registry


def test_exactly_the_fourteen_upstream_tools_are_registered(m):
    tools = asyncio.run(m.mcp.list_tools())
    assert {t.name for t in tools} == TOOLS
    assert all((t.description or "").strip() for t in tools), "every tool explains itself to the model"
    assert all(callable(getattr(m, name)) for name in TOOLS)


def test_download_tools_take_a_url_or_course_id_and_nothing_that_lifts_limits(m):
    schemas = {t.name: t.inputSchema for t in asyncio.run(m.mcp.list_tools())}
    assert set(schemas["download_file"]["properties"]) == {"fileurl", "subfolder"}
    assert set(schemas["download_course"]["properties"]) == {"course_id", "section", "max_mb"}


# ================================================================================== list_courses


def test_list_courses_hides_finished_courses_by_default(m, mock):
    courses(
        mock,
        {"id": 8, "fullname": "Future", "shortname": "FUT", "enddate": NOW + 10 * DAY},
        {"id": 9, "fullname": "Old", "shortname": "OLD", "enddate": 1},
    )
    assert m.list_courses() == [
        {"id": 7, "name": "Algorithms", "shortname": "ALG", "progress": 50.5, "ended": False},
        {"id": 8, "name": "Future", "shortname": "FUT", "progress": None, "ended": False},
    ]
    assert mock.forms_for("core_enrol_get_users_courses")[0]["userid"] == "42"
    assert [c["id"] for c in m.list_courses(include_past=True)] == [7, 8, 9]
    assert m.list_courses(include_past=True)[2]["ended"] is True


def test_list_courses_with_no_courses(m, mock):
    mock.on("core_enrol_get_users_courses", [])
    assert m.list_courses() == []


# ================================================================================= course_contents


def test_course_contents_lists_sections_activities_and_only_real_files(m, mock):
    mock.on(
        "core_course_get_contents",
        [
            {
                "name": "Week 1",
                "modules": [
                    {
                        "id": 11,
                        "name": "Slides",
                        "modname": "resource",
                        "url": f"{TAU_URL}/mod/resource/view.php?id=11",
                        "contents": [
                            {
                                "type": "file",
                                "filename": "a.pdf",
                                "fileurl": FILE_URL,
                                "filesize": 9,
                                "timemodified": OLD,
                            },
                            {"type": "url", "filename": "link", "fileurl": "https://elsewhere.example/x"},
                        ],
                    },
                    {"id": 12, "name": "Forum", "modname": "forum"},
                ],
            },
            {"name": "Empty", "modules": []},
        ],
    )
    assert m.course_contents(7) == [
        {
            "section": "Week 1",
            "modules": [
                {
                    "id": 11,
                    "name": "Slides",
                    "type": "resource",
                    "url": f"{TAU_URL}/mod/resource/view.php?id=11",
                    "files": [{"filename": "a.pdf", "fileurl": FILE_URL, "size": 9, "modified": stamp(OLD)}],
                },
                {"id": 12, "name": "Forum", "type": "forum", "url": None, "files": []},
            ],
        },
        {"section": "Empty", "modules": []},
    ]
    assert mock.forms_for("core_course_get_contents")[0]["courseid"] == "7"


# ============================================================================ upcoming_deadlines


def test_upcoming_deadlines_asks_for_the_window_and_reshapes_events(m, mock):
    mock.on(
        "core_calendar_get_action_events_by_timesort",
        {
            "events": [
                {
                    "name": "HW1",
                    "course": {"fullname": "Algorithms"},
                    "timesort": NOW + 3600,
                    "modulename": "assign",
                    "url": "u",
                },
                {"name": "Orphan", "timesort": NOW + 7200},
            ]
        },
    )
    assert m.upcoming_deadlines(3) == [
        {"name": "HW1", "course": "Algorithms", "due": stamp(NOW + 3600), "type": "assign", "url": "u"},
        {"name": "Orphan", "course": None, "due": stamp(NOW + 7200), "type": None, "url": None},
    ]
    (fields,) = mock.forms_for("core_calendar_get_action_events_by_timesort")
    assert int(fields["timesortto"]) - int(fields["timesortfrom"]) == 3 * DAY
    assert abs(int(fields["timesortfrom"]) - time.time()) < 60 and fields["limitnum"] == "50"


# ===================================================================================== assignments


def test_assignments_with_submission_grade_and_feedback(m, mock):
    plugin_file = {"filename": "mine.pdf", "fileurl": FILE_URL}
    mock.on(
        "mod_assign_get_assignments",
        {
            "courses": [
                {
                    "id": 7,
                    "assignments": [
                        {
                            "id": 5,
                            "name": "HW1",
                            "duedate": NOW + DAY,
                            "cutoffdate": 0,
                            "introattachments": [{"filename": "hw.pdf", "fileurl": FILE_URL, "extra": 1}],
                        },
                        {"id": 6, "name": "HW2", "duedate": 0},
                    ],
                }
            ]
        },
    )

    def status(fields):
        if fields["assignid"] == "6":
            return {"exception": "moodle_exception", "errorcode": "nopermission", "message": "no"}
        return {
            "lastattempt": {
                "submission": {
                    "status": "submitted",
                    "timemodified": NOW,
                    "plugins": [{"fileareas": [{"files": [plugin_file]}]}],
                },
                "gradingstatus": "graded",
            },
            "feedback": {
                "gradefordisplay": "<b>95</b> / 100",
                "gradeddate": NOW,
                "plugins": [
                    {"type": "comments", "editorfields": [{"text": "<p>Nice work</p>"}]},
                    {"type": "file", "fileareas": [{"files": [plugin_file]}]},
                ],
            },
        }

    mock.on("mod_assign_get_submission_status", status)
    first, second = m.assignments(7)
    assert first == {
        "id": 5,
        "name": "HW1",
        "due": stamp(NOW + DAY),
        "cutoff": None,
        "submission_status": "submitted",
        "files": [{"filename": "hw.pdf", "fileurl": FILE_URL}],
        "submitted_on": stamp(NOW),
        "submitted_files": [plugin_file],
        "grading_status": "graded",
        "grade": "95 / 100",
        "graded_on": stamp(NOW),
        "feedback": "Nice work",
        "feedback_files": [plugin_file],
    }
    assert second["submission_status"] is None and second["name"] == "HW2", "a failed status lookup is not fatal"
    assert mock.forms_for("mod_assign_get_assignments")[0]["courseids[0]"] == "7"


def test_assignments_without_any_courses(m, mock):
    mock.on("mod_assign_get_assignments", {"courses": [], "warnings": []})
    assert m.assignments(7) == []


# ========================================================================================= grades


def test_grades_lists_items_and_names_the_course_total(m, mock):
    mock.on(
        "gradereport_user_get_grade_items",
        {
            "usergrades": [
                {
                    "gradeitems": [
                        {
                            "itemname": "HW1",
                            "gradeformatted": "90",
                            "rangeformatted": "0-100",
                            "percentageformatted": "90 %",
                        },
                        {
                            "itemname": None,
                            "gradeformatted": "88",
                            "rangeformatted": "0-100",
                            "percentageformatted": "88 %",
                        },
                    ]
                }
            ]
        },
    )
    assert m.grades(7) == [
        {"item": "HW1", "grade": "90", "range": "0-100", "percentage": "90 %"},
        {"item": "Course total", "grade": "88", "range": "0-100", "percentage": "88 %"},
    ]
    fields = mock.forms_for("gradereport_user_get_grade_items")[0]
    assert (fields["courseid"], fields["userid"]) == ("7", "42")


def test_grades_overview_maps_course_ids_to_names(m, mock):
    courses(mock, {"id": 8, "fullname": "Old", "shortname": "OLD", "enddate": 1})
    mock.on(
        "gradereport_overview_get_course_grades",
        {"grades": [{"courseid": 7, "grade": "88"}, {"courseid": 8, "grade": "-"}, {"courseid": 99, "grade": "70"}]},
    )
    assert m.grades_overview() == [
        {"course_id": 7, "course": "Algorithms", "grade": "88"},
        {"course_id": 8, "course": "Old", "grade": "-"},
        {"course_id": 99, "course": None, "grade": "70"},
    ]


# ================================================================================ forums


def test_forum_discussions_per_forum_newest_first(m, mock):
    mock.on(
        "mod_forum_get_forums_by_courses",
        [{"id": 1, "name": "News", "course": 7}, {"id": 2, "name": "Q&A", "course": 7}],
    )
    mock.on(
        "mod_forum_get_forum_discussions",
        lambda fields: {
            "discussions": [
                {
                    "discussion": 100 + int(fields["forumid"]),
                    "subject": "Exam date",
                    "userfullname": "Prof",
                    "created": NOW - 100,
                    "numreplies": 2,
                    "timemodified": NOW,
                    "message": "<p>See <b>the site</b></p><script>alert(1)</script>",
                }
            ]
        },
    )
    out = m.forum_discussions(7, limit=3)
    assert [(d["forum"], d["discussion_id"]) for d in out] == [("News", 101), ("Q&A", 102)]
    assert out[0] == {
        "forum": "News",
        "discussion_id": 101,
        "subject": "Exam date",
        "author": "Prof",
        "posted": stamp(NOW - 100),
        "replies": 2,
        "last_post": stamp(NOW),
        "message": "See the site",
    }
    assert {f["perpage"] for f in mock.forms_for("mod_forum_get_forum_discussions")} == {"3"}
    assert {f["sortorder"] for f in mock.forms_for("mod_forum_get_forum_discussions")} == {"1"}


def test_forum_thread_orders_posts_and_drops_deleted_ones(m, mock):
    mock.on(
        "mod_forum_get_discussion_posts",
        {
            "posts": [
                {
                    "id": 1,
                    "hasparent": False,
                    "parentid": 0,
                    "author": {"fullname": "Prof"},
                    "timecreated": NOW,
                    "subject": "Hello",
                    "message": "<p>First</p>",
                    "attachments": [{"filename": "a.pdf", "fileurl": FILE_URL, "mimetype": "x"}],
                },
                {
                    "id": 2,
                    "hasparent": True,
                    "parentid": 1,
                    "author": None,
                    "timecreated": NOW + 1,
                    "subject": "Re",
                    "message": "x",
                },
                {"id": 3, "isdeleted": True, "message": "gone"},
            ]
        },
    )
    out = m.forum_thread(100)
    assert [p["id"] for p in out] == [1, 2]
    assert out[0]["reply_to"] is None and out[1]["reply_to"] == 1 and out[1]["author"] is None
    assert out[0]["message"] == "First" and out[0]["attachments"] == [{"filename": "a.pdf", "fileurl": FILE_URL}]
    fields = mock.forms_for("mod_forum_get_discussion_posts")[0]
    assert (fields["discussionid"], fields["sortby"], fields["sortdirection"]) == ("100", "created", "ASC")


# ================================================================================== quizzes


def test_quizzes_with_attempts_and_best_grade(m, mock):
    mock.on(
        "mod_quiz_get_quizzes_by_courses",
        {
            "quizzes": [
                {
                    "id": 3,
                    "name": "Midterm",
                    "timeopen": NOW,
                    "timeclose": NOW + DAY,
                    "timelimit": 3600,
                    "attempts": 0,
                    "grade": 100,
                },
                {"id": 4, "name": "Quick", "timelimit": 0, "attempts": 2, "grade": 10},
            ]
        },
    )
    mock.on(
        "mod_quiz_get_user_attempts",
        {"attempts": [{"attempt": 1, "state": "finished", "timestart": NOW - 60, "timefinish": NOW}]},
    )
    mock.on("mod_quiz_get_user_best_grade", lambda fields: {"hasgrade": fields["quizid"] == "3", "grade": 8.5})
    first, second = m.quizzes(7)
    assert first == {
        "id": 3,
        "name": "Midterm",
        "opens": stamp(NOW),
        "closes": stamp(NOW + DAY),
        "time_limit_minutes": 60,
        "attempts_allowed": "unlimited",
        "max_grade": 100,
        "my_attempts": [{"attempt": 1, "state": "finished", "started": stamp(NOW - 60), "finished": stamp(NOW)}],
        "best_grade": 8.5,
    }
    assert (second["time_limit_minutes"], second["attempts_allowed"], second["best_grade"]) == (None, 2, None)


def test_a_quiz_whose_attempts_cannot_be_read_is_still_listed(m, mock):
    mock.on("mod_quiz_get_quizzes_by_courses", {"quizzes": [{"id": 3, "name": "Midterm"}]})
    mock.on(
        "mod_quiz_get_user_attempts", {"exception": "moodle_exception", "errorcode": "nopermission", "message": "no"}
    )
    (quiz,) = m.quizzes(7)
    assert quiz["name"] == "Midterm" and "my_attempts" not in quiz and "best_grade" not in quiz


# ===================================================================================== text()


@pytest.mark.parametrize(
    ("html", "expected"),
    [
        (None, ""),
        ("", ""),
        ("plain text", "plain text"),
        ("<p>One</p><p>Two</p>", "One\nTwo"),
        ("<ul><li>a</li><li>b</li></ul>", "- a\n- b"),
        ("a<br>b", "a\nb"),
        ("<b>bold</b>  and\xa0\xa0space", "bold and space"),
        ("<script>alert(1)</script>shown<style>p{}</style>", "shown"),
        ('<a href="https://x.example/z">link</a>', "link (https://x.example/z)"),
        ('<a href="https://x.example/z">https://x.example/z</a>', "https://x.example/z"),
        ('<a href="/relative">local</a>', "local"),
        ('<a href="mailto:a@b.c">mail</a>', "mail"),
        ("<a>no href</a>", "no href"),
        ("&lt;tag&gt; &amp; more", "<tag> & more"),
    ],
)
def test_text_turns_moodle_html_into_readable_text(m, html, expected):
    assert m.text(html) == expected


def test_text_limit_cuts_with_an_ellipsis(m):
    assert m.text("x" * 100, 10) == "x" * 10 + "…"
    assert m.text("x" * 10, 10) == "x" * 10
