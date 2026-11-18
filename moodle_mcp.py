"""
Moodle MCP server for Israeli universities and colleges (default: TAU).

Site:  MOODLE_SITE=<key>  (see SITES below or run with --sites), or MOODLE_URL=<any Moodle>.

Token lookup order:
    1. MOODLE_TOKEN environment variable
    2. MOODLE_TOKEN_FILE, default .token next to this script   (one line, chmod 600)

Other settings: MOODLE_DOWNLOAD_DIR, MOODLE_MAX_DOWNLOAD_MB (200), MOODLE_DOWNLOAD_SECONDS,
MOODLE_SKIP_PREFLIGHT=1, MOODLE_DOWNLOAD_TOKEN_IN_QUERY=1 (see _fetch).
All settings are read when used: importing this module never exits, prints or touches the network.

Get a token:  .venv/bin/python moodle_mcp.py --login   (see moodle_login.py)
Run:          .venv/bin/python moodle_mcp.py           (stdio)
"""

import itertools
import json
import logging
import os
import re
import sys
import time
import datetime as dt
from concurrent.futures import ThreadPoolExecutor
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlencode, urlsplit

import httpx
from mcp.server.fastmcp import FastMCP

# Pure URL / file name checks (no network, no env); also reachable as moodle_mcp.<name>.
from moodle_safety import check_download_url, safe_filename, safe_subfolder, validate_base_url

# httpx/httpcore log every request URL at INFO (the fallback download puts the token in the URL).
# Keep it out of the MCP server log.
for _name in ("httpx", "httpcore"):
    logging.getLogger(_name).setLevel(logging.WARNING)

# Israeli institutions whose Moodle answered the public config endpoint (checked 2026-11).
# Pick one with MOODLE_SITE=<key>; anything else works through MOODLE_URL.
# Hosts with a year in the name (moodle26, moodle27...) move every academic year.
SITES = {
    # universities
    "tau": ("Tel Aviv University", "https://moodle.tau.ac.il"),
    "technion": ("Technion", "https://moodle26.technion.ac.il"),
    "bgu": ("Ben-Gurion University", "https://moodle.bgu.ac.il/moodle"),
    "ariel": ("Ariel University", "https://moodlearn.ariel.ac.il"),
    "openu": ("The Open University", "https://opal.openu.ac.il"),
    # engineering, design and general colleges
    "hit": ("HIT Holon Institute of Technology", "https://md.hit.ac.il"),
    "sce": ("SCE Sami Shamoon College of Engineering", "https://moodle.sce.ac.il"),
    "braude": ("Braude College of Engineering", "https://moodle27.braude.ac.il"),
    "azrieli": ("Azrieli College of Engineering Jerusalem", "https://moodle.jce.ac.il"),
    "jct": ("Jerusalem College of Technology (Lev)", "https://moodle.jct.ac.il"),
    "shenkar": ("Shenkar", "https://online.shenkar.ac.il"),
    "bezalel": ("Bezalel Academy of Arts and Design", "https://moodle.bezalel.ac.il"),
    "jamd": ("Jerusalem Academy of Music and Dance", "https://moodle.jamd.ac.il"),
    "mta": ("Academic College of Tel Aviv-Yaffo", "https://mama.mta.ac.il"),
    "colman": ("College of Management (Colman)", "https://moodle.colman.ac.il"),
    "ruppin": ("Ruppin Academic Center", "https://moodle.ruppin.ac.il"),
    "kinneret": ("Kinneret College", "https://moodle.kinneret.ac.il"),
    "wgalil": ("Western Galilee College", "https://moodle.wgalil.ac.il"),
    "achva": ("Achva Academic College", "https://moodle.achva.ac.il"),
    "zefat": ("Zefat Academic College", "https://moodle.zefat.ac.il"),
    "schechter": ("Schechter Institute", "https://moodle.schechter.ac.il"),
    # colleges of education
    "smkb": ("Kibbutzim College (Seminar HaKibbutzim)", "https://moodle26.smkb.ac.il"),
    "levinsky": ("Levinsky College of Education", "https://moodle.levinsky.ac.il"),
    "wingate": ("Levinsky-Wingate (Wingate campus)", "https://moodle4.wincol.ac.il"),
    "beitberl": ("Beit Berl College", "https://moodle2.beitberl.ac.il"),
    "oranim": ("Oranim College", "https://moodle25.oranim.ac.il"),
    "dyellin": ("David Yellin College", "https://online.dyellin.ac.il"),
    "kaye": ("Kaye College", "https://online.kaye.ac.il"),
    "washington": ("Givat Washington College", "https://moodle.washington.ac.il"),
    "talpiot": ("Talpiot College", "https://moodle4.talpiot.ac.il"),
    "shaanan": ("Shaanan College", "https://moodle4.shaanan.ac.il"),
    "orot": ("Orot Israel College", "https://moodle4.orot.ac.il"),
    "efrata": ("Efrata College", "https://moodle4.emef.ac.il"),
    "hemdat": ("Hemdat College", "https://moodle4.hemdat.ac.il"),
    "sakhnin": ("Sakhnin College", "https://moodle4.sakhnin.ac.il"),
    "qsm": ("Al-Qasemi Academy", "https://moodle4.qsm.ac.il"),
    "arabcol": ("Arab Academic College for Education, Haifa", "https://moodle4.arabcol.ac.il"),
}

DOWNLOAD_DEADLINE_S = 120  # one file download, start to end
PREFLIGHT_MAX_BYTES = 64 * 1024  # the identity preflight talks to a not yet verified host
_TOKEN_RE = re.compile(r"[A-Za-z0-9]{16,128}")  # Moodle tokens are PARAM_ALPHANUM
_TOKEN_PARAM_RE = re.compile(r"(\w*token=)[^&\s'\"<>]*", re.IGNORECASE)
_RESERVED_PARAMS = frozenset({"wstoken", "wsfunction", "moodlewsrestformat"})
_FRIENDLY = {
    "invalidtoken": "Moodle rejected the token (invalidtoken). Create a new token and save it.",
    "accessexception": "Moodle denied access (accessexception): the token may be expired or restricted, "
                       "or the mobile web service is disabled.",
}


class ConfigError(RuntimeError):
    """Bad MOODLE_* setting or token. Raised when the setting is used, never at import."""


class MoodleError(RuntimeError):
    """Moodle, HTTP or network failure. The message is short, scrubbed and never contains a URL."""

    def __init__(self, message: str, errorcode: str | None = None):
        super().__init__(message)
        self.errorcode = errorcode


def get_base() -> str:
    """The Moodle base URL (MOODLE_URL, else the MOODLE_SITE preset), read from the env on every call."""
    custom = os.environ.get("MOODLE_URL", "").strip()
    site = os.environ.get("MOODLE_SITE", "").strip().lower() or "tau"
    if not custom and site not in SITES:
        raise ConfigError(f"Unknown MOODLE_SITE {site[:40]!r}. Options: {', '.join(SITES)} (or set MOODLE_URL)")
    try:
        return validate_base_url(custom or SITES[site][1])
    except ValueError as e:
        raise ConfigError(f"Invalid MOODLE_URL: {e}") from None


def token_file() -> Path:
    raw = os.environ.get("MOODLE_TOKEN_FILE", "").strip()
    return Path(raw).expanduser() if raw else Path(__file__).resolve().parent / ".token"


def download_dir() -> Path:
    raw = os.environ.get("MOODLE_DOWNLOAD_DIR", "").strip()
    return Path(raw or Path.home() / "Downloads" / "moodle").expanduser().resolve()


def download_seconds() -> float:
    """Time budget of one download_course call (MOODLE_DOWNLOAD_SECONDS, default 45)."""
    try:
        sec = float(os.environ.get("MOODLE_DOWNLOAD_SECONDS", "").strip() or "45")
    except ValueError:
        raise ConfigError("MOODLE_DOWNLOAD_SECONDS must be a number") from None
    if not 0 <= sec <= 3600:
        raise ConfigError("MOODLE_DOWNLOAD_SECONDS must be between 0 and 3600")
    return sec


def max_download_bytes() -> int:
    raw = os.environ.get("MOODLE_MAX_DOWNLOAD_MB", "").strip() or "200"
    try:
        mb = float(raw)
    except ValueError:
        raise ConfigError("MOODLE_MAX_DOWNLOAD_MB must be a number") from None
    if not 0 < mb <= 100_000:
        raise ConfigError("MOODLE_MAX_DOWNLOAD_MB must be between 0 and 100000")
    return int(mb * 1024 * 1024)


mcp = FastMCP("moodle")
_client = httpx.Client(timeout=httpx.Timeout(30.0, connect=10.0), follow_redirects=False)
_userid: int | None = None
_preflight_ok: set[str] = set()  # bases whose identity preflight passed
_skip_warned: set[str] = set()  # MOODLE_SKIP_PREFLIGHT warning printed (once per process)
_perm_warned: set[str] = set()  # token files already warned about


def token() -> str:
    """Read the token lazily so the server still starts (and explains itself) without one.
    The token never appears in an error message."""
    tok = os.environ.get("MOODLE_TOKEN", "").strip()
    path = token_file()
    if not tok:
        try:
            raw = path.read_bytes()
        except FileNotFoundError:
            raw = b""
        except OSError as e:
            raise ConfigError(f"Cannot read the token file {path} ({type(e).__name__})") from None
        try:  # PowerShell 5.1 `>` writes UTF-16; Notepad may add a UTF-8 BOM
            tok = raw.decode("utf-16" if raw[:2] in (b"\xff\xfe", b"\xfe\xff") else "utf-8-sig").strip()
        except UnicodeDecodeError:
            raise ConfigError(f"The token file {path} is not valid text") from None
        if tok and os.name == "posix" and str(path) not in _perm_warned:
            try:
                too_open = bool(path.stat().st_mode & 0o077)
            except OSError:
                too_open = False
            if too_open:
                _perm_warned.add(str(path))
                print(f"moodle-mcp: WARNING {path} is readable by other users; run chmod 600 on it",
                      file=sys.stderr)
    if not tok:
        raise ConfigError(f"No Moodle token. Set MOODLE_TOKEN or put the token in {path}")
    if not _TOKEN_RE.fullmatch(tok):
        raise ConfigError("The Moodle token has an invalid format (expected 16-128 letters/digits)")
    return tok


def scrub(text) -> str:
    """Replace the token, and the value of any token=/wstoken= parameter, with ***."""
    text = str(text)
    try:
        tok = token()
    except ConfigError:
        tok = None
    if tok:
        text = text.replace(tok, "***")
    return _TOKEN_PARAM_RE.sub(r"\1***", text)


def _send(method: str, url: str, max_bytes: int | None = None, **kwargs) -> bytes:
    """One request through _client. Errors are MoodleError without the URL (httpx puts it in its
    messages, and the URL may carry the token)."""
    try:
        with _client.stream(method, url, follow_redirects=False, **kwargs) as r:
            if r.status_code != 200:
                raise MoodleError(f"Moodle answered HTTP {r.status_code}.")
            body = bytearray()
            for chunk in r.iter_bytes():
                body += chunk
                if max_bytes is not None and len(body) > max_bytes:
                    raise MoodleError("Moodle's reply was too large.")
            return bytes(body)
    except httpx.TimeoutException:
        raise MoodleError("Moodle did not answer in time (timeout).") from None
    except httpx.HTTPError as e:
        raise MoodleError(f"Could not reach Moodle ({type(e).__name__}).") from None


def _json(body: bytes):
    try:
        return json.loads(body)
    except (ValueError, RecursionError):
        raise MoodleError("Moodle returned an invalid (non-JSON) response.") from None


def _moodle_error(out) -> MoodleError | None:
    """The error in a Moodle reply ({"exception"/"errorcode", "message"}), scrubbed then truncated."""
    if not (isinstance(out, dict) and (out.get("exception") or "errorcode" in out)):
        return None
    code = out.get("errorcode") if isinstance(out.get("errorcode"), str) else None
    if code in _FRIENDLY:
        return MoodleError(_FRIENDLY[code], code)
    return MoodleError(scrub(f"{code}: {out.get('message') or out.get('error')}")[:300], code)


def _public_config(base: str) -> dict:
    """tool_mobile_get_public_config via the token-less nologin endpoint; returns its `data`."""
    body = [{"index": 0, "methodname": "tool_mobile_get_public_config", "args": {}}]
    url = f"{base}/lib/ajax/service-nologin.php?info=tool_mobile_get_public_config"
    out = _json(_send("POST", url, PREFLIGHT_MAX_BYTES, json=body))
    item = out[0] if isinstance(out, list) and out and isinstance(out[0], dict) else {}
    data = item.get("data") if not item.get("error", True) else None
    if not isinstance(data, dict):
        raise MoodleError("the site did not answer like a Moodle site.")
    return data


def _host_of(url) -> str | None:
    try:
        return urlsplit(url.strip()).hostname if isinstance(url, str) else None
    except ValueError:
        return None


def ensure_site_identity() -> None:
    """Before the token is first sent to a base: a token-less check that the host really is that
    Moodle (its public config names the same host). Cached per base."""
    base = get_base()
    if os.environ.get("MOODLE_SKIP_PREFLIGHT", "").strip() == "1":
        if not _skip_warned:
            _skip_warned.add(base)
            print("moodle-mcp: WARNING site identity preflight disabled (MOODLE_SKIP_PREFLIGHT=1)",
                  file=sys.stderr)
        return
    if base in _preflight_ok:
        return
    refuse = "Refusing to send your token: "
    try:
        data = _public_config(base)
    except MoodleError as e:
        raise MoodleError(refuse + f"could not verify the site ({e})") from None
    host = urlsplit(base).hostname
    if not any(_host_of(data.get(k)) == host for k in ("wwwroot", "httpswwwroot")):
        raise MoodleError(refuse + f"the site does not identify itself as {host}.")
    _preflight_ok.add(base)


def call(function: str, **params):
    """Call a Moodle web service function (REST, JSON). The token goes in the POST body only."""
    flat = _flatten(params)
    if _RESERVED_PARAMS & (set(params) | set(flat)):
        raise ValueError("wstoken, wsfunction and moodlewsrestformat cannot be passed as params")
    base = get_base()
    if function == "tool_mobile_get_public_config":
        return _public_config(base)  # public: never send the token for it
    tok = token()
    ensure_site_identity()
    data = {**flat, "wstoken": tok, "wsfunction": function, "moodlewsrestformat": "json"}
    out = _json(_send("POST", f"{base}/webservice/rest/server.php", data=data))
    err = _moodle_error(out)
    if err:
        raise err
    return out


def _flatten(obj, prefix=""):
    """Moodle expects arrays as courseids[0]=1&courseids[1]=2 ..."""
    flat = {}
    items = obj.items() if isinstance(obj, dict) else enumerate(obj)
    for k, v in items:
        key = f"{prefix}[{k}]" if prefix else str(k)
        if isinstance(v, (dict, list, tuple)):
            flat.update(_flatten(v, key))
        elif isinstance(v, bool):
            flat[key] = int(v)
        elif v is not None:
            flat[key] = v
    return flat


def userid() -> int:
    global _userid
    if _userid is None:
        _userid = call("core_webservice_get_site_info")["userid"]
    return _userid


def fmt_time(ts):
    return dt.datetime.fromtimestamp(ts).strftime("%d/%m/%Y %H:%M") if ts else None


class _Text(HTMLParser):
    """Moodle sends messages and descriptions as HTML; turn them into readable text."""
    BLOCK = {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "blockquote", "pre", "ul", "ol", "table"}

    def __init__(self):
        super().__init__()
        self.out, self.href, self.start, self.skip = [], None, 0, 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self.skip += 1
        if tag in self.BLOCK:
            self.out.append("\n")
        if tag == "li":
            self.out.append("- ")
        if tag == "a":
            self.href, self.start = dict(attrs).get("href"), len(self.out)

    def handle_endtag(self, tag):
        if tag in ("script", "style") and self.skip:
            self.skip -= 1
        if tag == "a" and self.href:
            if self.href.startswith("http") and self.href not in "".join(self.out[self.start:]):
                self.out.append(f" ({self.href})")
            self.href = None
        if tag in self.BLOCK:
            self.out.append("\n")

    def handle_data(self, data):
        if not self.skip:
            self.out.append(data)


def text(html, limit: int | None = None) -> str:
    if not html:
        return ""
    parser = _Text()
    parser.feed(str(html))
    parser.close()
    out = re.sub(r"[ \t\xa0]+", " ", "".join(parser.out))
    out = re.sub(r"\s*\n\s*", "\n", out).strip()
    return out[:limit].rstrip() + "…" if limit and len(out) > limit else out


def _safe(name, limit: int = 80) -> str:
    """A file or folder name that is safe on macOS, Windows and Linux (see moodle_safety.safe_filename)."""
    return safe_filename(name, limit)


def _my_courses(include_past: bool = False) -> list[dict]:
    now = time.time()
    courses = call("core_enrol_get_users_courses", userid=userid())
    return [c for c in courses if include_past or not (c.get("enddate") and c["enddate"] < now)]


def _parallel(fn, items):
    """Run fn over items a few at a time. Returns (item, result, error) per item."""
    def one(item):
        try:
            return item, fn(item), None
        except Exception as e:
            return item, None, str(e)[:200]
    items = list(items)
    if not items:
        return []
    with ThreadPoolExecutor(max_workers=min(6, len(items))) as pool:
        return list(pool.map(one, items))


def _plugin_files(plugins) -> list[dict]:
    return [{"filename": f.get("filename"), "fileurl": f.get("fileurl")}
            for p in plugins or [] for area in p.get("fileareas", []) for f in area.get("files", [])]


def _fetch(fileurl: str, path: Path, max_bytes: int | None = None) -> int:
    """Stream a Moodle file to path (replacing an older copy). Returns the size in bytes."""
    # Only <base>/webservice/pluginfile.php/... on exactly the Moodle host: the token goes with it.
    check_download_url(fileurl, get_base())
    cap = min(max_bytes, max_download_bytes()) if max_bytes else max_download_bytes()  # the user's limit wins
    root = download_dir()
    tmp = path.with_name(path.name + ".part")
    tok = token()
    ensure_site_identity()

    def inside() -> bool:  # resolve() follows symlinks, so a symlinked subfolder can't lead out
        return path.resolve().is_relative_to(root) and tmp.resolve().is_relative_to(root)
    if not inside():
        raise ValueError("Refusing to write outside the download directory")
    path.parent.mkdir(parents=True, exist_ok=True)
    if not inside():  # again, now that the folders exist
        raise ValueError("Refusing to write outside the download directory")
    if os.environ.get("MOODLE_DOWNLOAD_TOKEN_IN_QUERY", "").strip() == "1":
        # Fallback for Moodle sites that ignore a POST body token: the old ?token= way (the URL
        # with the token can end up in server/proxy logs, so it is opt-in).
        sep = "&" if urlsplit(fileurl).query else "?"
        req = _client.stream("GET", fileurl + sep + urlencode({"token": tok}), follow_redirects=False)
    else:  # pluginfile.php reads `token` from GET or POST; the body keeps it out of URLs and logs
        req = _client.stream("POST", fileurl, data={"token": tok}, follow_redirects=False)
    deadline = time.monotonic() + DOWNLOAD_DEADLINE_S
    too_big = MoodleError(f"File is larger than the {cap / 1048576:g} MB download limit")
    size = 0
    try:
        with req as r:
            if r.status_code != 200:
                raise MoodleError(f"Moodle answered HTTP {r.status_code} for this file")
            chunks, first = r.iter_bytes(), []
            if "application/json" in r.headers.get("content-type", ""):
                # A Moodle error is a small JSON reply; a course file that really is a .json
                # document is a normal download (what was read here is written first).
                head = b""
                for chunk in chunks:
                    first.append(chunk)
                    head += chunk
                    if len(head) > 65536 or time.monotonic() > deadline:
                        break
                try:
                    out = json.loads(head)
                except (ValueError, RecursionError):
                    out = None
                err = _moodle_error(out)
                if err is None and isinstance(out, dict) and isinstance(out.get("error"), str):
                    err = MoodleError(scrub(f"Moodle refused the download: {out['error']}")[:200])
                if err:
                    raise err
            declared = r.headers.get("content-length", "")
            if declared.isdigit() and int(declared) > cap:
                raise too_big
            with open(tmp, "wb") as fh:
                for chunk in itertools.chain(first, chunks):
                    size += len(chunk)
                    if size > cap:
                        raise too_big
                    if time.monotonic() > deadline:
                        raise MoodleError(f"Download took over {DOWNLOAD_DEADLINE_S} s")
                    fh.write(chunk)
        tmp.replace(path)
    except httpx.TimeoutException:
        raise MoodleError("Download timed out") from None
    except httpx.HTTPError as e:
        # httpx puts the request URL (maybe with the token) in its messages; never pass them on.
        raise MoodleError(f"Download failed: {type(e).__name__}") from None
    finally:
        tmp.unlink(missing_ok=True)
    return size


@mcp.tool()
def list_courses(include_past: bool = False) -> list[dict]:
    """List the courses I'm enrolled in (current by default)."""
    now = time.time()
    out = []
    for c in _my_courses(include_past):
        ended = bool(c.get("enddate") and c["enddate"] < now)
        out.append({"id": c["id"], "name": c["fullname"], "shortname": c["shortname"],
                    "progress": c.get("progress"), "ended": ended})
    return out


@mcp.tool()
def course_contents(course_id: int) -> list[dict]:
    """Sections, activities and files of a course."""
    sections = call("core_course_get_contents", courseid=course_id)
    out = []
    for s in sections:
        mods = []
        for m in s.get("modules", []):
            mods.append({
                "id": m["id"], "name": m["name"], "type": m["modname"], "url": m.get("url"),
                "files": [{"filename": f["filename"], "fileurl": f["fileurl"],
                           "size": f.get("filesize"), "modified": fmt_time(f.get("timemodified"))}
                          for f in m.get("contents", []) if f.get("type") == "file"],
            })
        out.append({"section": s["name"], "modules": mods})
    return out


@mcp.tool()
def upcoming_deadlines(days: int = 14) -> list[dict]:
    """Upcoming deadlines / action events (assignments, quizzes...) in the next N days."""
    now = int(time.time())
    res = call("core_calendar_get_action_events_by_timesort",
               timesortfrom=now, timesortto=now + days * 86400, limitnum=50)
    return [{"name": e["name"], "course": e.get("course", {}).get("fullname"),
             "due": fmt_time(e["timesort"]), "type": e.get("modulename"), "url": e.get("url")}
            for e in res.get("events", [])]


@mcp.tool()
def assignments(course_id: int) -> list[dict]:
    """Assignments of a course with due dates, my submission status and attached files."""
    res = call("mod_assign_get_assignments", courseids=[course_id])
    out = []
    for c in res.get("courses", []):
        for a in c.get("assignments", []):
            item = {"id": a["id"], "name": a["name"], "due": fmt_time(a.get("duedate")),
                    "cutoff": fmt_time(a.get("cutoffdate")), "submission_status": None,
                    "files": [{"filename": f["filename"], "fileurl": f["fileurl"]}
                              for f in a.get("introattachments", [])]}
            try:
                st = call("mod_assign_get_submission_status", assignid=a["id"])
            except Exception:
                st = {}
            last = st.get("lastattempt") or {}
            sub = last.get("submission") or last.get("teamsubmission") or {}
            item["submission_status"] = sub.get("status")
            if sub.get("status") == "submitted":
                item["submitted_on"] = fmt_time(sub.get("timemodified"))
                item["submitted_files"] = _plugin_files(sub.get("plugins"))
            if last.get("gradingstatus"):
                item["grading_status"] = last["gradingstatus"]
            fb = st.get("feedback") or {}
            if fb:
                comments = [text(e.get("text")) for p in fb.get("plugins", []) if p.get("type") == "comments"
                            for e in p.get("editorfields", [])]
                item["grade"] = text(fb.get("gradefordisplay"), 500)
                item["graded_on"] = fmt_time(fb.get("gradeddate"))
                item["feedback"] = "\n".join(c for c in comments if c)[:4000] or None
                item["feedback_files"] = _plugin_files(fb.get("plugins"))
            out.append(item)
    return out


@mcp.tool()
def grades(course_id: int) -> list[dict]:
    """My grade items in a course."""
    res = call("gradereport_user_get_grade_items", courseid=course_id, userid=userid())
    out = []
    for g in res.get("usergrades", []):
        for item in g.get("gradeitems", []):
            out.append({"item": item.get("itemname") or "Course total",
                        "grade": item.get("gradeformatted"), "range": item.get("rangeformatted"),
                        "percentage": item.get("percentageformatted")})
    return out


@mcp.tool()
def grades_overview() -> list[dict]:
    """My course total grade in every course, in one call."""
    names = {c["id"]: c["fullname"] for c in _my_courses(include_past=True)}
    res = call("gradereport_overview_get_course_grades", userid=userid())
    return [{"course_id": g["courseid"], "course": names.get(g["courseid"]), "grade": g.get("grade")}
            for g in res.get("grades", [])]


@mcp.tool()
def forum_discussions(course_id: int, limit: int = 10) -> list[dict]:
    """Latest discussions in a course's forums (announcements included)."""
    forums = call("mod_forum_get_forums_by_courses", courseids=[course_id])
    out = []
    for f in forums:
        res = call("mod_forum_get_forum_discussions", forumid=f["id"], perpage=limit,
                   sortorder=1)  # newest first
        for d in res.get("discussions", []):
            out.append({"forum": f["name"], "discussion_id": d.get("discussion"), "subject": d["subject"],
                        "author": d.get("userfullname"), "posted": fmt_time(d.get("created")),
                        "replies": d.get("numreplies"), "last_post": fmt_time(d.get("timemodified")),
                        "message": text(d.get("message"), 1500)})
    return out


@mcp.tool()
def forum_thread(discussion_id: int) -> list[dict]:
    """Every post of one forum discussion, oldest first (discussion_id from forum_discussions / whats_new)."""
    res = call("mod_forum_get_discussion_posts", discussionid=discussion_id,
               sortby="created", sortdirection="ASC")
    return [{"id": p["id"], "reply_to": p.get("parentid") if p.get("hasparent") else None,
             "author": (p.get("author") or {}).get("fullname"), "posted": fmt_time(p.get("timecreated")),
             "subject": p.get("subject"), "message": text(p.get("message"), 4000),
             "attachments": [{"filename": a.get("filename"), "fileurl": a.get("fileurl")}
                             for a in p.get("attachments", [])]}
            for p in res.get("posts", []) if not p.get("isdeleted")]


@mcp.tool()
def quizzes(course_id: int) -> list[dict]:
    """Quizzes of a course: when they open and close, time limit, my attempts and best grade."""
    res = call("mod_quiz_get_quizzes_by_courses", courseids=[course_id])
    out = []
    for q in res.get("quizzes", []):
        item = {"id": q["id"], "name": q["name"], "opens": fmt_time(q.get("timeopen")),
                "closes": fmt_time(q.get("timeclose")),
                "time_limit_minutes": q["timelimit"] // 60 if q.get("timelimit") else None,
                "attempts_allowed": q.get("attempts") or "unlimited", "max_grade": q.get("grade")}
        try:
            attempts = call("mod_quiz_get_user_attempts", quizid=q["id"], status="all").get("attempts", [])
            item["my_attempts"] = [{"attempt": a.get("attempt"), "state": a.get("state"),
                                    "started": fmt_time(a.get("timestart")),
                                    "finished": fmt_time(a.get("timefinish"))} for a in attempts]
            best = call("mod_quiz_get_user_best_grade", quizid=q["id"])
            item["best_grade"] = best.get("grade") if best.get("hasgrade") else None
        except Exception:
            pass
        out.append(item)
    return out


@mcp.tool()
def notifications(limit: int = 20, unread_only: bool = False) -> dict:
    """My Moodle notifications (the bell): graded work, forum posts, reminders. Newest first."""
    res = call("message_popup_get_popup_notifications", useridto=userid(), newestfirst=1,
               limit=max(1, min(limit, 100)))
    items = [{"subject": n.get("subject"), "received": fmt_time(n.get("timecreated")),
              "read": bool(n.get("read")), "kind": n.get("eventtype"),
              "text": text(n.get("fullmessagehtml") or n.get("fullmessage") or n.get("smallmessage"), 800),
              "url": n.get("contexturl")}
             for n in res.get("notifications", []) if not (unread_only and n.get("read"))]
    return {"unread_count": res.get("unreadcount"), "notifications": items}


@mcp.tool()
def calendar_events(days: int = 30) -> list[dict]:
    """Everything on my Moodle calendar in the next N days: exams, lecturer events, my own events, due dates."""
    now = int(time.time())
    names = {c["id"]: c["fullname"] for c in _my_courses()}
    res = call("core_calendar_get_calendar_events", events={"courseids": list(names)},
               options={"userevents": 1, "siteevents": 1, "timestart": now,
                        "timeend": now + days * 86400, "ignorehidden": 1})
    events = sorted(res.get("events", []), key=lambda e: e.get("timestart") or 0)
    return [{"name": e["name"], "course": names.get(e.get("courseid")), "type": e.get("eventtype"),
             "activity": e.get("modulename") or None, "start": fmt_time(e.get("timestart")),
             "end": fmt_time(e["timestart"] + e["timeduration"]) if e.get("timeduration") else None,
             "description": text(e.get("description"), 400)}
            for e in events]


@mcp.tool()
def whats_new(days: int = 7) -> dict:
    """What changed in all my current courses in the last N days: new or updated files, assignments,
    forum posts and other activities. Start here for "anything new?" questions."""
    since = int(time.time()) - days * 86400
    names = {c["id"]: c["fullname"] for c in _my_courses()}
    ids = list(names)
    files, other, errors = [], [], []

    def scan(cid):
        sections = call("core_course_get_contents", courseid=cid)
        try:
            updates = call("core_course_get_updates_since", courseid=cid, since=since).get("instances", [])
        except Exception:
            updates = []
        return sections, updates

    for cid, res, err in _parallel(scan, ids):
        if err:
            errors.append(f"{names[cid]}: {err}")
            continue
        sections, updates = res
        mods, reported = {}, set()
        for s in sections:
            for mod in s.get("modules", []):
                mods[mod["id"]] = (mod, s["name"])
                new = [f for f in mod.get("contents", [])
                       if f.get("type") == "file" and (f.get("timemodified") or 0) >= since]
                if not new:
                    continue
                reported.add(mod["id"])
                ts = max(f["timemodified"] for f in new)
                entry = {"course": names[cid], "course_id": cid, "section": s["name"], "name": mod["name"],
                         "type": mod["modname"], "updated": fmt_time(ts), "_ts": ts}
                if mod["modname"] in ("resource", "folder"):
                    entry["files"] = [{"filename": f["filename"], "fileurl": f["fileurl"]} for f in new[:15]]
                else:
                    entry["url"] = mod.get("url")
                files.append(entry)
        for inst in updates:
            mod, section = mods.get(inst.get("id"), (None, None))
            if not mod or inst.get("contextlevel") != "module" or mod["id"] in reported \
                    or mod["modname"] in ("label", "unilabel", "subsection", "forum", "assign"):
                continue
            other.append({"course": names[cid], "course_id": cid, "section": section, "name": mod["name"],
                          "type": mod["modname"], "url": mod.get("url"),
                          "changed": sorted({u.get("name") for u in inst.get("updates", [])})})

    assigns = []
    try:
        for c in call("mod_assign_get_assignments", courseids=ids).get("courses", []) if ids else []:
            for a in c.get("assignments", []):
                if (a.get("timemodified") or 0) >= since:
                    assigns.append({"course": names.get(c["id"]), "course_id": c["id"], "id": a["id"],
                                    "name": a["name"], "due": fmt_time(a.get("duedate")),
                                    "updated": fmt_time(a["timemodified"]), "_ts": a["timemodified"]})
    except Exception as e:
        errors.append(f"assignments: {str(e)[:200]}")

    posts = []
    try:
        forums = call("mod_forum_get_forums_by_courses", courseids=ids) if ids else []
    except Exception as e:
        forums = []
        errors.append(f"forums: {str(e)[:200]}")
    for f, res, err in _parallel(lambda f: call("mod_forum_get_forum_discussions", forumid=f["id"],
                                                perpage=10, sortorder=1), forums):
        if err:
            continue  # forums the student cannot read are not news
        for d in res.get("discussions", []):
            ts = d.get("timemodified") or 0
            if ts >= since:
                posts.append({"course": names.get(f.get("course")), "course_id": f.get("course"),
                              "forum": f["name"], "discussion_id": d.get("discussion"), "subject": d["subject"],
                              "started_by": d.get("userfullname"), "last_post_by": d.get("usermodifiedfullname"),
                              "replies": d.get("numreplies"), "last_post": fmt_time(ts),
                              "preview": text(d.get("message"), 300), "_ts": ts})

    def newest_first(rows):
        rows.sort(key=lambda r: r["_ts"], reverse=True)
        return [{k: v for k, v in r.items() if k != "_ts"} for r in rows]

    out = {"since": fmt_time(since), "files": newest_first(files), "assignments": newest_first(assigns),
           "forum_posts": newest_first(posts), "other_changes": other}
    if errors:
        out["errors"] = errors
    return out


@mcp.tool()
def download_file(fileurl: str, subfolder: str = "") -> str:
    """Download a course file (fileurl from course_contents / assignments) to the local downloads folder."""
    root = download_dir()
    target = (root / safe_subfolder(subfolder)).resolve()
    if not target.is_relative_to(root):
        raise ValueError("subfolder must stay inside the download directory")
    name = unquote(urlsplit(fileurl).path.rsplit("/", 1)[-1])
    if not name.strip(" ."):
        raise ValueError("URL has no file name")
    path = target / safe_filename(name)
    _fetch(fileurl, path)
    return str(path)


@mcp.tool()
def download_course(course_id: int, section: str = "", max_mb: int = 200) -> dict:
    """Download all files of a course (or only the sections whose name contains `section`) into
    one folder per course, with a subfolder per section. Files already downloaded and unchanged
    are skipped, so calling it again only fetches what is new. If `remaining` is above 0 it ran
    out of time: call it again to continue."""
    course = next((c for c in _my_courses(include_past=True) if c["id"] == course_id), None)
    root = download_dir() / _safe(course["fullname"] if course else f"course {course_id}")
    budget = time.time() + download_seconds()
    out = {"folder": str(root), "downloaded": [], "unchanged": 0, "remaining": 0,
           "too_large": [], "failed": [], "bytes": 0}
    seen = set()
    for s in call("core_course_get_contents", courseid=course_id):
        if section and section.lower() not in s["name"].lower():
            continue
        for mod in s.get("modules", []):
            if mod["modname"] not in ("resource", "folder"):
                continue  # pages and books are web content, not files to keep
            for f in mod.get("contents", []):
                if f.get("type") != "file" or f.get("isexternalfile"):
                    continue
                folder = root / _safe(s["name"])
                if mod["modname"] == "folder":
                    folder = folder / _safe(mod["name"])
                    for part in (f.get("filepath") or "/").strip("/").split("/"):
                        folder = folder / _safe(part) if part else folder
                path = folder / _safe(f["filename"], 150)
                if path in seen:  # two activities in a section with the same file name
                    path = path.with_stem(f"{path.stem} ({mod['id']})")
                seen.add(path)
                if not path.resolve().is_relative_to(download_dir()):
                    continue
                size, modified = f.get("filesize") or 0, f.get("timemodified") or 0
                if path.exists() and path.stat().st_size == size and path.stat().st_mtime >= modified:
                    out["unchanged"] += 1
                elif size > max_mb * 1024 * 1024:
                    out["too_large"].append({"filename": f["filename"], "mb": round(size / 1048576)})
                elif time.time() > budget:
                    out["remaining"] += 1
                else:
                    try:
                        out["bytes"] += _fetch(f["fileurl"], path, max_mb * 1024 * 1024)
                        out["downloaded"].append(str(path.relative_to(root)))
                    except Exception as e:
                        out["failed"].append({"filename": f["filename"], "error": str(e)[:150]})
    return out


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else list(argv)
    if "--sites" in args:
        for key, (name, url) in SITES.items():
            print(f"{key:11} {name:45} {url}")
        return 0
    try:
        base = get_base()
    except ConfigError as e:
        print(f"moodle-mcp: {e}", file=sys.stderr)
        return 2
    if "--login" in args:
        from moodle_login import login
        forced = next((m for m in ("browser", "password") if f"--{m}" in args), None)
        login(base, token_file(), forced)
    else:
        mcp.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
