"""
Pure security helpers for moodle_mcp: no network, no token, no environment, no printing.

Moodle base URL validation, download URL checks, safe file / folder names and the cleaning and
fencing of untrusted Moodle text. moodle_mcp imports from here; this module never imports moodle_mcp.
"""

import ipaddress
import re
import unicodedata
from html.parser import HTMLParser
from urllib.parse import unquote, urlsplit

LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}
_HOST_RE = re.compile(r"[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)*")
_DEFAULT_PORTS = {"https": 443, "http": 80}


def _has_bad_chars(s: str) -> bool:
    """Whitespace, control or format (bidi, zero-width) characters."""
    return any(c.isspace() or unicodedata.category(c) in ("Cc", "Cf") for c in s)


def _port(parts) -> int | None:
    """The port of a split URL, the scheme's default if none is given (an explicit 0 stays 0)."""
    return parts.port if parts.port is not None else _DEFAULT_PORTS.get(parts.scheme)


def validate_base_url(url: str) -> str:
    """Normalize a Moodle base URL ('https://host[:port][/path]'); raise ValueError if unsafe.

    https only (http only for localhost), no user name/password, query or fragment, a real host
    name, lower-case scheme and host, no trailing '/'.
    """
    if not isinstance(url, str) or not url.strip():
        raise ValueError("Moodle URL is empty")
    url = url.strip()
    if _has_bad_chars(url) or "\\" in url:
        raise ValueError("Moodle URL contains whitespace, control characters or backslashes")
    if "?" in url or "#" in url:
        raise ValueError("Moodle URL must not contain a query (?) or fragment (#)")
    try:
        parts = urlsplit(url)
        host = parts.hostname or ""
        port = parts.port
    except ValueError:
        raise ValueError("Moodle URL could not be parsed (check the host and port)") from None
    if "@" in parts.netloc:
        raise ValueError("Moodle URL must not contain a user name or password")
    if not host:
        raise ValueError("Moodle URL has no host name")
    scheme = parts.scheme.lower()
    if scheme == "http" and host not in LOCAL_HOSTS:
        raise ValueError("Moodle URL must use https:// (http is allowed only for localhost)")
    if scheme not in _DEFAULT_PORTS:
        raise ValueError("Moodle URL must start with https://")
    try:
        is_ip6 = ipaddress.ip_address(host).version == 6
    except ValueError:
        is_ip6 = False
        if not _HOST_RE.fullmatch(host):
            raise ValueError("Moodle URL has an invalid host name (use the ASCII form)") from None
    netloc = f"[{host}]" if is_ip6 else host
    if port is not None and port != _DEFAULT_PORTS[scheme]:
        netloc += f":{port}"
    path = parts.path.rstrip("/")
    if any(seg in (".", "..") for seg in path.split("/")):
        raise ValueError("Moodle URL path must not contain . or .. segments")
    return f"{scheme}://{netloc}{path}"


def check_download_url(url: str, base: str) -> str:
    """Allow only <base>/webservice/pluginfile.php/... on exactly the base scheme, host and port.

    The token is sent with this request, so it must never go anywhere else. Errors never
    contain the URL.
    """
    if not isinstance(url, str) or _has_bad_chars(url) or "\\" in url:
        raise ValueError("Invalid file URL")
    if "#" in url:
        raise ValueError("File URL must not contain a fragment")
    try:
        u, b = urlsplit(url), urlsplit(base)
        same_host = bool(u.hostname) and u.hostname == b.hostname
        same_port = _port(u) == _port(b)
    except ValueError:
        raise ValueError("File URL could not be parsed") from None
    if "@" in u.netloc or u.scheme != b.scheme or not same_host or not same_port:
        raise ValueError("Only file URLs on your Moodle host are allowed")
    if not u.path.startswith(b.path.rstrip("/") + "/webservice/pluginfile.php/"):
        raise ValueError("Only Moodle file URLs (/webservice/pluginfile.php/...) are allowed")
    low = u.path.lower()
    if ";" in u.path or any(e in low for e in ("%2e%2e", "%2f", "%5c", "%252e", "%252f", "%255c")):
        raise ValueError("File URL path must not contain ';' or encoded dots or separators")
    decoded = unquote(u.path)  # decode once, so `.%2e`, `%2E.` ... are seen as `..`
    if (
        any(s in ("..", ".") for s in decoded.split("/"))
        or "\\" in decoded
        or any(unicodedata.category(c) == "Cc" for c in decoded)
    ):
        raise ValueError("File URL path must not contain dot segments or control characters")
    return url


_RESERVED_NAMES = {"CON", "PRN", "AUX", "NUL"} | {f"{p}{d}" for p in ("COM", "LPT") for d in "123456789¹²³"}
_BAD_CHARS_RE = re.compile(r'[\\/:*?"<>|\x00-\x1f\x7f-\x9f]+')
MAX_NAME_BYTES = 240  # ext4 / APFS allow 255 bytes per name; leave room for ".part"


def safe_filename(name, limit: int = 150) -> str:
    """A file or folder name that is safe on macOS, Windows and Linux; never empty.

    Separators and control characters become spaces, bidi / zero-width characters are dropped,
    leading and trailing dots and spaces go (so no '..' or hidden files), Windows device names
    (CON, NUL, COM1.txt ...) get a '_' prefix. At most `limit` characters and MAX_NAME_BYTES
    UTF-8 bytes, keeping a short extension.
    """
    name = "".join(c for c in str(name or "") if unicodedata.category(c) != "Cf")
    name = _BAD_CHARS_RE.sub(" ", name)
    name = re.sub(r"\s+", " ", name).strip(" .")
    limit = max(1, limit)
    if name.split(".")[0].rstrip(" ").upper() in _RESERVED_NAMES:  # before the cut: limit still holds
        name = "_" + name
    if len(name) > limit or len(name.encode("utf-8")) > MAX_NAME_BYTES:
        stem, dot, ext = name.rpartition(".")
        if not (dot and stem and len(ext) <= 8):
            stem, ext = name, ""
        ext = "." + ext if ext else ""
        stem = stem[: max(1, limit - len(ext))]
        while len(stem) > 1 and len((stem + ext).encode("utf-8")) > MAX_NAME_BYTES:
            stem = stem[:-1]  # whole characters, never a split UTF-8 sequence
        name = (stem.rstrip(" .") + ext).strip(" .")
    return name or "untitled"


def safe_subfolder(sub) -> str:
    """A relative folder path ('a/b') with safe parts; no absolute paths, drives or '..'."""
    if sub is None or sub == "":
        return ""
    if not isinstance(sub, str):
        raise ValueError("subfolder must be text")
    if sub.startswith(("/", "\\")) or re.match(r"[A-Za-z]:", sub):
        raise ValueError("subfolder must be a relative path")
    parts = [p for p in re.split(r"[/\\]", sub) if p.strip() not in ("", ".")]
    if any(p.strip() == ".." for p in parts):
        raise ValueError("subfolder must stay inside the download directory")
    return "/".join(safe_filename(p) for p in parts)


# Web service functions that change data in Moodle (used by --doctor, never by a tool).
_WRITE_VERBS = re.compile(
    r"(?:^|_)(?:add|create|delete|remove|update|save|submit|send|set|unset|toggle|start|finish|"
    r"mark|block|unblock|lock|unlock|enrol|unenrol|join|leave|trigger|process|upload|edit|new|"
    r"launch|evaluate|approve|revert|reveal|insert|agree|confirm|decline|mute|unmute|enable|"
    r"end|login|copy|post|override|reset|import)(?:_|$)"
)
# fmt: off
HIGH_RISK = frozenset({
    "tool_mobile_call_external_functions", "tool_mobile_get_autologin_key",
    "core_user_add_user_device", "mod_wiki_get_page_for_editing",
    "mod_bigbluebuttonbn_get_join_url", "tool_mobile_validate_subscription_key",
    "core_message_message_processor_config_form",
    # writes in Moodle's mobile service that the verb regex misses
    "core_competency_grade_competency_in_course", "mod_bigbluebuttonbn_completion_validate",
    "mod_forum_prepare_draft_area_for_post", "report_insights_action_executed",
    "tool_dataprivacy_cancel_data_request", "tool_dataprivacy_contact_dpo",
})
# fmt: on


def is_write_function(name: str) -> bool:
    """Heuristic: does this web service function change data in Moodle?

    Names are <component>_<method> (core_enrol_get_users_courses); verbs are only looked for
    in the method part, so a component like core_enrol or core_block is not a write. Case is
    ignored (Moodle's database resolves function names case-insensitively).
    """
    if not isinstance(name, str):
        return False
    name = name.lower()
    method = name.split("_", 2)[2] if name.count("_") >= 2 else name
    return name in HIGH_RISK or bool(_WRITE_VERBS.search(method))


# Untrusted text: everything Moodle returns was written by someone else (lecturers, classmates,
# file names) and may try to give the model instructions.

MAX_HTML_DEPTH = 200  # html_to_text stops at this element nesting depth
# Invisible code points that are not Cf: variation selectors, combining grapheme joiner, Hangul fillers.
_INVISIBLE = frozenset(
    map(chr, [*range(0xFE00, 0xFE10), *range(0xE0100, 0xE01F0), 0x034F, 0x115F, 0x1160, 0x3164, 0xFFA0])
)
_FENCE_RE = re.compile(r"<(?=\s*/?\s*untrusted_moodle_content)", re.IGNORECASE)
_SOURCE_RE = re.compile(r"[a-z_.]{1,40}")


def _as_text(v) -> str | None:
    """Strings as they are, numbers as text, anything else (None, bool, list, dict) None."""
    if isinstance(v, str):
        return v
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return str(v)
    return None


def clean_text(s, limit: int | None = None) -> str:
    """NFKC-normalised text without control (except newline and tab), format (zero-width, bidi),
    surrogate or other invisible characters. Cut to `limit` characters (with "…") only if given."""
    s = unicodedata.normalize("NFKC", _as_text(s) or "")
    s = "".join(
        c for c in s if c in "\n\t" or (unicodedata.category(c) not in ("Cc", "Cf", "Cs") and c not in _INVISIBLE)
    )
    return s[:limit].rstrip() + "…" if limit is not None and len(s) > limit else s


class _TooDeep(Exception):
    pass


def _hides(tag: str, attrs) -> bool:
    """Does this element hide its content (script/style/template, `hidden`, display:none ...)?"""
    if tag in ("script", "style", "template") or any(k == "hidden" for k, _ in attrs):
        return True
    for k, v in attrs:
        if k == "style" and v:
            if "\\" in v:  # CSS escapes (d\isplay, \6e one) could hide text from a plain match
                return True
            v = re.sub(r"\s+|/\*.*?\*/", "", v.lower(), flags=re.DOTALL)
            if "display:none" in v or "visibility:hidden" in v:
                return True
    return False


class _VisibleText(HTMLParser):
    """Moodle sends messages and descriptions as HTML; collect the text a reader would see.
    Linear time: a stack of open elements, each popped once, and per-tag open counts."""

    BLOCK = {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "blockquote", "pre", "ul", "ol", "table"}
    VOID = set("area base br col embed hr img input link meta param source track wbr".split())

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self.stack: list[tuple[str, bool, str | None, int]] = []  # (tag, hides, http(s) href, start in out)
        self.hidden = 0  # open elements that hide their content
        self.open: dict[str, int] = {}

    def handle_starttag(self, tag, attrs):
        if tag in self.VOID:
            if tag in self.BLOCK and not self.hidden:
                self.out.append("\n")
            return
        if len(self.stack) >= MAX_HTML_DEPTH:
            raise _TooDeep
        hides = _hides(tag, attrs)
        visible = not (hides or self.hidden)
        href = next((v for k, v in attrs if k == "href"), None) if tag == "a" and visible else None
        if not (href and href.lower().startswith(("http://", "https://")) and not _has_bad_chars(href)):
            href = None
        if visible and tag in self.BLOCK:
            self.out.append("\n")
        if visible and tag == "li":
            self.out.append("- ")
        self.stack.append((tag, hides, href, len(self.out)))
        self.hidden += hides
        self.open[tag] = self.open.get(tag, 0) + 1

    def handle_endtag(self, tag):
        if not self.open.get(tag):  # stray end tag
            return
        while True:  # an end tag also closes the elements left open inside it
            name, hides, href, start = self.stack.pop()
            self.hidden -= hides
            self.open[name] -= 1
            if href and not self.hidden and href not in "".join(self.out[start:]):
                self.out.append(f" ({href})")
            if name == tag:
                break
        if tag in self.BLOCK and not self.hidden:
            self.out.append("\n")

    def handle_data(self, data):
        if not self.hidden:
            self.out.append(data)


def html_to_text(html) -> str:
    """Readable text of Moodle HTML: blocks as lines, <li> as "- ", links as "text (url)" for http(s)
    links. Hidden content (script/style/template, `hidden`, display:none, visibility:hidden) is dropped.
    Nesting deeper than MAX_HTML_DEPTH ends the text with "[…]"."""
    parser = _VisibleText()
    try:
        parser.feed(_as_text(html) or "")
        parser.close()
    except _TooDeep:
        parser.out.append("\n[…]")
    out = re.sub(r"[ \t\xa0]+", " ", "".join(parser.out))
    return re.sub(r"\s*\n\s*", "\n", out).strip()


def untrusted(text, source: str, *, html: bool = False, limit: int | None = None) -> str | None:
    """Fence third-party Moodle text so the model reads it as data:
    <untrusted_moodle_content source="SRC">CLEAN</untrusted_moodle_content>. None for empty or
    non-text input. `source` is a fixed label of ours; any fence tag inside the text is broken
    (`<\\/untrusted...`), so the text cannot close or fake the wrapper."""
    if not _SOURCE_RE.fullmatch(source):
        raise ValueError("source must be a short fixed label")
    text = _as_text(text)
    if text is None:
        return None
    text = clean_text(html_to_text(text) if html else text, limit)
    if not text.strip():
        return None
    text = _FENCE_RE.sub(r"<\\", text)
    return f'<untrusted_moodle_content source="{source}">{text}</untrusted_moodle_content>'
