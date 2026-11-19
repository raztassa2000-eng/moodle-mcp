"""READ_FUNCTIONS: call() only ever sends the listed read-only Moodle functions; is_write_function spots writes."""

from __future__ import annotations

import ast

import pytest

import moodle_safety as s
from support import ROOT

READ = "core_enrol_get_users_courses"


class StrSubclass(str):
    pass


# Not exactly an allowed name: refused before any request (the identity preflight included).
REFUSED = [
    pytest.param("core_message_send_instant_messages", id="write"),
    pytest.param("mod_assign_save_submission", id="write-2"),
    pytest.param(f"{READ}_x", id="near-miss"),
    pytest.param(READ.upper(), id="upper-case"),
    pytest.param(READ.title(), id="mixed-case"),
    pytest.param(f" {READ}", id="padded-left"),
    pytest.param(f"{READ}\n", id="newline"),
    pytest.param(f"{READ}\x00", id="nul"),
    pytest.param("", id="empty"),
    pytest.param(StrSubclass(READ), id="str-subclass"),
    pytest.param(None, id="none"),
    pytest.param(7, id="int"),
    pytest.param(READ.encode(), id="bytes"),
    pytest.param([READ], id="list"),
]


@pytest.mark.parametrize("function", REFUSED)
def test_call_refuses_everything_but_exact_allowed_names_before_any_request(m, mock, function):
    mock.on("core_message_send_instant_messages", [])  # even a Moodle that would accept it never hears of it
    with pytest.raises(PermissionError):
        m.call(function, userid=1)
    assert mock.requests == [], "no request at all, not even the preflight"


def test_read_functions_is_exactly_what_the_code_calls(m):
    """Every call(...) in moodle_mcp.py names an allowed function by a literal, and the list has no dead entries."""
    tree = ast.parse((ROOT / "moodle_mcp.py").read_text(encoding="utf-8"))
    calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call) and getattr(n.func, "id", None) == "call"]
    assert calls
    literal = [n for n in calls if n.args and isinstance(n.args[0], ast.Constant) and isinstance(n.args[0].value, str)]
    assert len(literal) == len(calls), "call() must always get a literal function name"
    used = {n.args[0].value for n in literal}
    assert used <= m.READ_FUNCTIONS
    # the public config goes through the token-less endpoint (_public_config), not through call()
    assert m.READ_FUNCTIONS - used <= {"tool_mobile_get_public_config", "core_webservice_get_site_info"}


def test_read_functions_is_frozen_and_holds_no_write(m):
    assert isinstance(m.READ_FUNCTIONS, frozenset) and "tool_mobile_get_public_config" in m.READ_FUNCTIONS
    assert [n for n in sorted(m.READ_FUNCTIONS) if m.is_write_function(n)] == []


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("core_blog_add_entry", True),
        ("mod_forum_add_discussion_post", True),
        ("core_message_send_instant_messages", True),
        ("mod_assign_save_submission", True),
        ("mod_quiz_start_attempt", True),
        ("core_enrol_unenrol_users", True),
        ("tool_mobile_get_autologin_key", True),  # no verb, but listed in HIGH_RISK
        (READ, False),  # the component "core_enrol" is not an "enrol" verb
        ("core_block_get_dashboard_blocks", False),
        ("mod_forum_get_discussion_posts", False),
        ("core_course_get_contents", False),
        ("mod_assign_get_submission_status", False),
    ],
)
def test_is_write_function(m, name, expected):
    assert m.is_write_function(name) is expected
    assert m.is_write_function(name.upper()) is expected  # Moodle resolves function names case-insensitively


def test_is_write_function_edges(m):
    assert m.is_write_function is s.is_write_function
    assert all(m.is_write_function(n) and m.is_write_function(n.upper()) for n in s.HIGH_RISK)
    for value in (None, 5, b"core_blog_add_entry", ["core_blog_add_entry"], ""):
        assert m.is_write_function(value) is False
