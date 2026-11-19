"""Start the real server over stdio, with no network and (mostly) no token.

* one session through the official `mcp` client: handshake, the 14 tools, errors that explain themselves;
* one raw JSON-RPC session that looks at every byte on stdout: it must be nothing but JSON-RPC, and the token
  must not show up in stdout or stderr.
"""

from __future__ import annotations

import asyncio
import json
import queue
import subprocess
import sys
import threading

import pytest

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from support import FAKE_TOKEN, ROOT, TOOLS, clean_env

SCRIPT = ROOT / "moodle_mcp.py"
EVIL_FILE = "https://evil.example.com/webservice/pluginfile.php/1/a.pdf"


def _server_env(tmp_path_factory, **extra: str) -> dict[str, str]:
    base = tmp_path_factory.mktemp("stdio")
    return clean_env(
        MOODLE_TOKEN_FILE=str(base / "no-such-token-file"), MOODLE_DOWNLOAD_DIR=str(base / "downloads"), **extra
    )


# ============================================================================ the official client


async def _official_session(env: dict[str, str], errlog_path) -> dict:
    params = StdioServerParameters(command=sys.executable, args=[str(SCRIPT)], env=env, cwd=str(ROOT))
    with open(errlog_path, "w", encoding="utf-8") as errlog:
        async with stdio_client(params, errlog=errlog) as (read, write):
            async with ClientSession(read, write) as session:
                init = await session.initialize()
                tools = (await session.list_tools()).tools
                results = {
                    # Only without a token: with one this tool would go and ask the real site. Every other
                    # call below is refused before any network access, whatever the environment.
                    "list_courses": await session.call_tool("list_courses", {}) if "MOODLE_TOKEN" not in env else None,
                    "foreign_url": await session.call_tool("download_file", {"fileurl": EVIL_FILE}),
                    "escaping_subfolder": await session.call_tool(
                        "download_file",
                        {"fileurl": "https://moodle.tau.ac.il/webservice/pluginfile.php/1/a.pdf", "subfolder": "../x"},
                    ),
                    "bad_arguments": await session.call_tool("grades", {"course_id": "not a number"}),
                    "unknown_tool": await session.call_tool("write_everything", {}),
                }
    return {"init": init, "tools": tools, "results": results}


def _run_official(env, tmp_path_factory) -> dict:
    errlog_path = tmp_path_factory.mktemp("stderr") / "stderr.txt"
    out = asyncio.run(asyncio.wait_for(_official_session(env, errlog_path), timeout=120))
    out["stderr"] = errlog_path.read_text(encoding="utf-8", errors="replace")
    return out


def _text(result) -> str:
    return " ".join(getattr(block, "text", "") for block in result.content)


@pytest.fixture(scope="module")
def no_token(tmp_path_factory):
    """A server with NO token at all."""
    return _run_official(_server_env(tmp_path_factory), tmp_path_factory)


@pytest.fixture(scope="module")
def with_token(tmp_path_factory):
    """The same server with a (fake) token in its environment."""
    return _run_official(_server_env(tmp_path_factory, MOODLE_TOKEN=FAKE_TOKEN), tmp_path_factory)


def test_handshake_works_without_a_token(no_token):
    init = no_token["init"]
    assert init.serverInfo.name == "moodle"
    assert init.protocolVersion
    assert init.capabilities.tools is not None
    assert "untrusted_moodle_content" in (init.instructions or ""), "the model is told what the fence means"


def test_the_server_advertises_exactly_the_fourteen_tools(no_token):
    names = [t.name for t in no_token["tools"]]
    assert len(names) == len(set(names))
    assert set(names) == TOOLS
    assert all(t.description and t.description.strip() for t in no_token["tools"])
    assert all(t.inputSchema.get("type") == "object" for t in no_token["tools"])


def test_a_tool_call_without_a_token_explains_what_to_do(no_token):
    result = no_token["results"]["list_courses"]
    assert result.isError is True
    assert "No Moodle token" in _text(result) and "MOODLE_TOKEN" in _text(result)
    assert "Traceback" not in _text(result)


@pytest.mark.parametrize("fixture", ["no_token", "with_token"])
def test_a_download_from_another_host_is_refused_with_no_network(request, fixture):
    results = request.getfixturevalue(fixture)["results"]
    for key in ("foreign_url", "escaping_subfolder"):
        assert results[key].isError is True, key
    assert "Moodle host" in _text(results["foreign_url"])
    assert "download directory" in _text(results["escaping_subfolder"])


def test_bad_arguments_and_unknown_tools_are_errors_not_crashes(no_token):
    assert no_token["results"]["bad_arguments"].isError is True
    assert no_token["results"]["unknown_tool"].isError is True


@pytest.mark.parametrize("fixture", ["no_token", "with_token"])
def test_the_token_never_appears_in_anything_the_client_sees_or_the_server_logs(request, fixture):
    session = request.getfixturevalue(fixture)
    seen = (
        " ".join(_text(r) for r in session["results"].values() if r is not None)
        + session["stderr"]
        + repr(session["init"])
    )
    assert FAKE_TOKEN not in seen


# ======================================================================== raw JSON-RPC, byte level


class RawSession:
    def __init__(self, env: dict[str, str]) -> None:
        self.proc = subprocess.Popen(  # noqa: S603 - fixed interpreter and repo-local script
            [sys.executable, str(SCRIPT)],
            cwd=ROOT,
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
        )
        self.lines: queue.Queue[str] = queue.Queue()
        self.raw: list[str] = []
        self.stderr: list[str] = []
        self.closed = False
        self.threads = [
            threading.Thread(target=self._pump, args=(self.proc.stdout, self.lines), daemon=True),
            threading.Thread(target=self._drain, daemon=True),
        ]
        for thread in self.threads:
            thread.start()

    @staticmethod
    def _pump(stream, sink) -> None:
        for line in stream:
            sink.put(line)

    def _drain(self) -> None:
        for line in self.proc.stderr:
            self.stderr.append(line)

    def send(self, message: dict) -> None:
        self.proc.stdin.write(json.dumps(message) + "\n")
        self.proc.stdin.flush()

    def wait_for(self, msg_id: int, timeout: float = 60) -> dict:
        while True:
            line = self.lines.get(timeout=timeout)
            self.raw.append(line)
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                continue  # kept in `raw`: the purity test inspects it
            if message.get("id") == msg_id:
                return message

    def close(self) -> None:
        """End the session (stdin EOF stops the server) and collect everything it wrote. Safe to call twice."""
        if self.closed:
            return
        self.closed = True
        try:
            self.proc.stdin.close()
            self.proc.wait(timeout=15)
        except Exception:  # noqa: BLE001
            self.proc.kill()
            self.proc.wait(timeout=15)
        for thread in self.threads:
            thread.join(timeout=10)
        self.proc.stdout.close()
        self.proc.stderr.close()
        while not self.lines.empty():
            self.raw.append(self.lines.get_nowait())


@pytest.fixture(scope="module")
def raw(tmp_path_factory):
    session = RawSession(_server_env(tmp_path_factory, MOODLE_TOKEN=FAKE_TOKEN))
    try:
        session.send(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "pytest", "version": "0"},
                },
            }
        )
        init = session.wait_for(1)
        session.send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        session.send({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        tools = session.wait_for(2)
        session.send(
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {"name": "download_file", "arguments": {"fileurl": EVIL_FILE}},
            }
        )
        call = session.wait_for(3)
        yield {"session": session, "init": init, "tools": tools, "call": call}
    finally:
        session.close()


def test_stdout_is_nothing_but_json_rpc(raw):
    lines = raw["session"].raw
    assert len(lines) >= 3, "server wrote nothing"
    for line in lines:
        message = json.loads(line)
        assert isinstance(message, dict) and message.get("jsonrpc") == "2.0", line


def test_the_raw_replies_match_the_official_client_view(raw):
    assert raw["init"]["result"]["serverInfo"]["name"] == "moodle"
    assert {t["name"] for t in raw["tools"]["result"]["tools"]} == TOOLS
    assert raw["call"]["result"]["isError"] is True


def test_the_token_is_in_neither_stdout_nor_stderr(raw):
    session = raw["session"]
    assert all(FAKE_TOKEN not in line for line in session.raw)
    session.close()  # flush: the stderr pump ends with the process
    assert FAKE_TOKEN not in "".join(session.stderr)
