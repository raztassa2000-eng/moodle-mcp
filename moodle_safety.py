"""
Pure security helpers for moodle_mcp: no network, no token, no environment, no printing.

Moodle base URL validation, download URL checks and safe file / folder names.
moodle_mcp imports from here; this module never imports moodle_mcp.
"""

import ipaddress
import re
import unicodedata
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
