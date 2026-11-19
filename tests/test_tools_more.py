"""Smoke tests for the upstream tools (unchanged by this PR), part 2: notifications, calendar, whats_new, and
"every tool lets a Moodle error through without the token in it"."""

from __future__ import annotations

import re
import time

import httpx
import pytest

from support import (
    DAY,
    FAKE_TOKEN,
    FILE_URL,
    NOW,
    PLUGINFILE,
    TAU_URL,
    TOOLS,
    assert_no_token,
    courses,
    fenced,
    stamp,
    unwrap,
)


# ============================================================================== notifications


NOTIFICATIONS = {
    "unreadcount": 2,
    "notifications": [
        {
            "subject": "Graded",
            "timecreated": NOW,
            "read": False,
            "eventtype": "grade",
            "fullmessagehtml": "<p>You got <b>95</b></p>",
            "contexturl": f"{TAU_URL}/mod/assign/view.php?id=5",
        },
        {"subject": "Old", "timecreated": NOW - DAY, "read": True, "fullmessage": "plain text"},
        {"subject": "Short", "read": False, "smallmessage": "tiny"},
    ],
}


def test_notifications_shape_and_request(m, mock):
    mock.on("message_popup_get_popup_notifications", NOTIFICATIONS)
    out = m.notifications(limit=5)
    assert out["unread_count"] == 2
    assert [(unwrap(n["subject"])[1], n["read"], unwrap(n["text"])[1]) for n in out["notifications"]] == [
        ("Graded", False, "You got 95"),
        ("Old", True, "plain text"),
        ("Short", False, "tiny"),
    ]
    assert out["notifications"][0] == {
        "subject": fenced("Graded", "notification.subject"),
        "received": stamp(NOW),
        "read": False,
        "kind": "grade",
        "text": fenced("You got 95", "notification.text"),
        "url": f"{TAU_URL}/mod/assign/view.php?id=5",
    }
    assert out["notifications"][2]["url"] is None, "a notification without a link has none"
    (fields,) = mock.forms_for("message_popup_get_popup_notifications")
    assert (fields["useridto"], fields["newestfirst"], fields["limit"]) == ("42", "1", "5")


def test_notifications_unread_only(m, mock):
    mock.on("message_popup_get_popup_notifications", NOTIFICATIONS)
    out = m.notifications(unread_only=True)["notifications"]
    assert [unwrap(n["subject"])[1] for n in out] == ["Graded", "Short"]


@pytest.mark.parametrize(("asked", "sent"), [(500, "100"), (100, "100"), (1, "1"), (0, "1"), (-5, "1")])
def test_notifications_limit_is_clamped_to_1_to_100(m, mock, asked, sent):
    mock.on("message_popup_get_popup_notifications", {"unreadcount": 0, "notifications": []})
    m.notifications(limit=asked)
    assert mock.forms_for("message_popup_get_popup_notifications")[0]["limit"] == sent


# ================================================================================ calendar_events


def test_calendar_events_sorted_with_course_names(m, mock):
    courses(mock)
    mock.on(
        "core_calendar_get_calendar_events",
        {
            "events": [
                {
                    "name": "Exam",
                    "courseid": 7,
                    "eventtype": "course",
                    "modulename": "",
                    "timestart": NOW + 2 * DAY,
                    "timeduration": 3600,
                    "description": "<p>Bring ID</p>",
                },
                {"name": "Lecture", "courseid": 7, "eventtype": "course", "timestart": NOW + 1000, "timeduration": 0},
                {"name": "Mine", "courseid": 0, "eventtype": "user", "modulename": "assign", "timestart": None},
            ]
        },
    )
    out = m.calendar_events(days=10)
    assert [unwrap(e["name"])[1] for e in out] == ["Mine", "Lecture", "Exam"]
    assert out[2] == {
        "name": fenced("Exam", "event.name"),
        "course": fenced("Algorithms", "course.name"),
        "type": "course",
        "activity": None,
        "start": stamp(NOW + 2 * DAY),
        "end": stamp(NOW + 2 * DAY + 3600),
        "description": fenced("Bring ID", "event.description"),
    }
    assert out[1]["end"] is None and out[0]["course"] is None and out[0]["activity"] == "assign"
    (fields,) = mock.forms_for("core_calendar_get_calendar_events")
    assert fields["events[courseids][0]"] == "7" and fields["options[userevents]"] == "1"
    assert int(fields["options[timeend]"]) - int(fields["options[timestart]"]) == 10 * DAY


# ================================================================================== whats_new


def _new_file(name, ts):
    return {"type": "file", "filename": name, "fileurl": f"{TAU_URL}{PLUGINFILE}1/{name}", "timemodified": ts}


def _whats_new_site(mock):
    courses(mock)
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
                        "url": f"{TAU_URL}/mod/x/view.php?id=11",
                        "contents": [_new_file("new.pdf", NOW - 100), _new_file("old.pdf", NOW - 30 * DAY)],
                    },
                    {
                        "id": 12,
                        "name": "Exercises",
                        "modname": "folder",
                        "url": f"{TAU_URL}/mod/x/view.php?id=12",
                        "contents": [_new_file("ex.pdf", NOW - 50)],
                    },
                    {
                        "id": 13,
                        "name": "Wiki",
                        "modname": "wiki",
                        "url": f"{TAU_URL}/mod/x/view.php?id=13",
                        "contents": [_new_file("w.html", NOW - 200)],
                    },
                    {
                        "id": 14,
                        "name": "Page",
                        "modname": "page",
                        "url": f"{TAU_URL}/mod/x/view.php?id=14",
                        "contents": [],
                    },
                    {
                        "id": 15,
                        "name": "Old stuff",
                        "modname": "resource",
                        "url": f"{TAU_URL}/mod/x/view.php?id=15",
                        "contents": [_new_file("o.pdf", NOW - 30 * DAY)],
                    },
                    {"id": 16, "name": "News", "modname": "forum", "url": f"{TAU_URL}/mod/x/view.php?id=16"},
                    {"id": 17, "name": "Label", "modname": "label"},
                ],
            }
        ],
    )
    mock.on(
        "core_course_get_updates_since",
        {
            "instances": [
                {
                    "id": 14,
                    "contextlevel": "module",
                    "updates": [{"name": "contentfiles"}, {"name": "configuration"}, {"name": "contentfiles"}],
                },
                {"id": 11, "contextlevel": "module", "updates": [{"name": "contentfiles"}]},
                {"id": 16, "contextlevel": "module", "updates": [{"name": "x"}]},
                {"id": 17, "contextlevel": "module", "updates": [{"name": "x"}]},
                {"id": 7, "contextlevel": "course", "updates": [{"name": "x"}]},
                {"id": 999, "contextlevel": "module", "updates": [{"name": "x"}]},
            ]
        },
    )
    mock.on(
        "mod_assign_get_assignments",
        {
            "courses": [
                {
                    "id": 7,
                    "assignments": [
                        {"id": 5, "name": "HW1", "duedate": NOW + DAY, "timemodified": NOW - 10},
                        {"id": 6, "name": "HW old", "duedate": 0, "timemodified": NOW - 30 * DAY},
                    ],
                }
            ]
        },
    )
    mock.on("mod_forum_get_forums_by_courses", [{"id": 1, "name": "News", "course": 7}])
    mock.on(
        "mod_forum_get_forum_discussions",
        {
            "discussions": [
                {
                    "discussion": 100,
                    "subject": "Exam date",
                    "userfullname": "Prof",
                    "usermodifiedfullname": "TA",
                    "numreplies": 3,
                    "timemodified": NOW - 20,
                    "message": "<p>See <b>site</b></p>",
                },
                {"discussion": 101, "subject": "Ancient", "timemodified": NOW - 30 * DAY},
            ]
        },
    )


def test_whats_new_collects_files_assignments_forum_posts_and_other_changes(m, mock):
    _whats_new_site(mock)
    out = m.whats_new(days=7)
    assert set(out) == {"since", "files", "assignments", "forum_posts", "other_changes"}
    assert re.fullmatch(r"\d\d/\d\d/\d{4} \d\d:\d\d", out["since"])
    assert [(unwrap(f["name"])[1], f["type"]) for f in out["files"]] == [
        ("Exercises", "folder"),
        ("Slides", "resource"),
        ("Wiki", "wiki"),
    ]
    slides = out["files"][1]
    assert slides["files"] == [
        {"filename": fenced("new.pdf", "file.name"), "fileurl": f"{TAU_URL}{PLUGINFILE}1/new.pdf"}
    ], "only the new file"
    assert out["files"][2]["url"] == f"{TAU_URL}/mod/x/view.php?id=13" and "files" not in out["files"][2]
    assert slides["course"] == fenced("Algorithms", "course.name") and slides["course_id"] == 7
    assert slides["section"] == fenced("Week 1", "section.name")
    assert out["other_changes"] == [
        {
            "course": fenced("Algorithms", "course.name"),
            "course_id": 7,
            "section": fenced("Week 1", "section.name"),
            "name": fenced("Page", "module.name"),
            "type": "page",
            "url": f"{TAU_URL}/mod/x/view.php?id=14",
            "changed": ["configuration", "contentfiles"],
        }
    ]
    assert [unwrap(a["name"])[1] for a in out["assignments"]] == ["HW1"]
    assert out["assignments"][0]["course"] == fenced("Algorithms", "course.name")
    (post,) = out["forum_posts"]
    assert (post["subject"], post["preview"], post["last_post_by"], post["course"]) == (
        fenced("Exam date", "forum.subject"),
        fenced("See site", "post.message"),
        fenced("TA", "forum.author"),
        fenced("Algorithms", "course.name"),
    )
    assert not any("_ts" in row for rows in (out["files"], out["assignments"], out["forum_posts"]) for row in rows)
    assert "errors" not in out


def test_whats_new_window_is_sent_to_moodle(m, mock):
    _whats_new_site(mock)
    m.whats_new(days=3)
    (fields,) = mock.forms_for("core_course_get_updates_since")
    assert fields["courseid"] == "7" and abs(int(fields["since"]) - (time.time() - 3 * DAY)) < 60
    assert mock.forms_for("mod_assign_get_assignments")[0]["courseids[0]"] == "7"


def test_whats_new_with_no_current_courses_asks_nothing_else(m, mock):
    mock.on("core_enrol_get_users_courses", [])
    out = m.whats_new()
    assert (out["files"], out["assignments"], out["forum_posts"], out["other_changes"]) == ([], [], [], [])
    assert mock.functions_called() == ["core_webservice_get_site_info", "core_enrol_get_users_courses"]


def test_whats_new_survives_a_course_whose_update_list_or_forums_cannot_be_read(m, mock):
    _whats_new_site(mock)
    mock.on(
        "core_course_get_updates_since", {"exception": "moodle_exception", "errorcode": "nopermission", "message": "x"}
    )
    mock.on(
        "mod_forum_get_forum_discussions",
        {"exception": "moodle_exception", "errorcode": "nopermission", "message": "x"},
    )
    out = m.whats_new()
    assert [unwrap(f["name"])[1] for f in out["files"]] == ["Exercises", "Slides", "Wiki"]
    assert out["other_changes"] == [] and out["forum_posts"] == []
    assert "errors" not in out, "an unreadable forum or update list is not news, and not an error"


def test_whats_new_reports_failures_without_the_token(m, mock):
    _whats_new_site(mock)
    boom = {"exception": "moodle_exception", "errorcode": "odd", "message": f"broken {FAKE_TOKEN} wstoken=ABC123456789"}
    mock.on("core_course_get_contents", boom)
    mock.on("mod_assign_get_assignments", boom)
    mock.on("mod_forum_get_forums_by_courses", boom)
    out = m.whats_new()
    assert len(out["errors"]) == 3
    assert {unwrap(e)[0] for e in out["errors"]} == {"error"}, "errors carry Moodle's text, so they are fenced"
    assert [unwrap(e)[1].split(":")[0] for e in out["errors"]] == ["Algorithms", "assignments", "forums"]
    for error in out["errors"]:
        assert_no_token(error)
        assert "ABC123456789" not in error


# =============================================================== every tool lets Moodle errors through

CALLS = {
    "list_courses": lambda m: m.list_courses(),
    "course_contents": lambda m: m.course_contents(7),
    "upcoming_deadlines": lambda m: m.upcoming_deadlines(),
    "assignments": lambda m: m.assignments(7),
    "grades": lambda m: m.grades(7),
    "grades_overview": lambda m: m.grades_overview(),
    "forum_discussions": lambda m: m.forum_discussions(7),
    "forum_thread": lambda m: m.forum_thread(100),
    "quizzes": lambda m: m.quizzes(7),
    "notifications": lambda m: m.notifications(),
    "calendar_events": lambda m: m.calendar_events(),
    "whats_new": lambda m: m.whats_new(),
    "download_file": lambda m: m.download_file(FILE_URL),
    "download_course": lambda m: m.download_course(7),
}

ERRORS = [
    pytest.param("invalidtoken", "Invalid token - token not found", id="invalidtoken"),
    pytest.param("accessexception", "Access control exception", id="accessexception"),
    pytest.param(
        "odd", f"broke with {FAKE_TOKEN} and wstoken=ABC123456789 and token=XYZ987654321", id="echoes-the-token"
    ),
]


def test_every_tool_is_covered_by_the_error_test():
    assert set(CALLS) == TOOLS


@pytest.mark.parametrize(("code", "message"), ERRORS)
@pytest.mark.parametrize("tool", sorted(CALLS))
def test_tools_propagate_a_moodle_error_without_the_token(m, mock, tool, code, message):
    reply = {"exception": "moodle_exception", "errorcode": code, "message": message}
    mock.fail_all(reply)
    mock.download = lambda _r: httpx.Response(200, json=reply)
    with pytest.raises(m.MoodleError) as info:
        CALLS[tool](m)
    assert info.value.errorcode == code
    text = f"{info.value} {info.value!r}"
    assert_no_token(text)
    assert "ABC123456789" not in text and "XYZ987654321" not in text
    assert "://" not in text


@pytest.mark.parametrize("tool", sorted(CALLS))
def test_tools_without_a_token_say_so_and_send_nothing(m, mock, tool, monkeypatch):
    monkeypatch.delenv("MOODLE_TOKEN")
    with pytest.raises(m.ConfigError, match="No Moodle token"):
        CALLS[tool](m)
    assert mock.requests == []
