#!/usr/bin/env python3
"""Clean a vendor receipt email down to the part a packet can show.

What comes out is the vendor's own markup, with its own fonts, colours and
values intact, minus everything that can act: no script can run, no element can
load a second document, and no value in the fragment can reach the network.

What ``clean_html`` always removes, whatever the vendor rules say:

- ``<script>`` blocks and stray script tags.
- Every inline handler attribute on every tag, anything matching ``on<word>=``.
- ``javascript:`` and ``data:text/html`` values in ``href``, ``src``,
  ``action``, ``data``, ``poster``, ``background`` and ``formaction``.
- The ``srcset`` attribute, wherever it appears.
- These elements and everything inside them: iframe, frame, frameset, object,
  embed, applet, form, input, button, textarea, select, base, link, meta, svg,
  math, video, audio, source, track, noscript and template.
- In ``<style>`` blocks and in ``style=""`` attributes: every ``@import`` rule,
  every declaration holding ``expression(``, ``-moz-binding`` or ``behavior:``,
  every ``page-break-*`` and ``break-*`` declaration, and every ``url(...)``
  whose scheme is not ``data:``, which becomes ``none``.
- An image ``src`` that is not http, https or ``data:image/``.
- Any ``<style`` or ``</style`` token left over once the complete style blocks
  have been taken out, so a message truncated in the middle of a stylesheet
  cannot swallow the text that follows it.

Three choices are worth stating plainly, because whoever reads a packet should
know what was done to the page in front of them.

1. Links. A tracking link named by ``unwrap_links_matching`` is replaced by the
   text it wrapped. Every other anchor keeps its tag and its styling but loses
   ``href``, ``target`` and any handler attribute, so the wording and the
   vendor look survive and nothing in a packet is clickable.
2. Images. ``inline_images`` reads from a cache directory and never opens a
   socket. Only the CLI, and only with ``--fetch-images``, downloads anything,
   and it downloads from the cleaned fragment, so a tracking pixel the cleaner
   has already removed is never requested. A fetch refuses a host that resolves
   to a loopback, private or link-local address, follows no redirect, and stops
   reading at five megabytes. The substring test for tracking pixels (open,
   track, pixel, beacon) runs on http and https sources only, because a base64
   data URI can contain those letters by chance and a logo is not a pixel.
3. CSS. Style blocks are kept, not dropped, so the receipt still looks like the
   receipt. Every rule is sanitised, then scoped to the ``.rc`` receipt
   container, so one vendor's stylesheet cannot reach another vendor's receipt
   on the same page. Page break declarations are the one exception: a receipt
   that split across two pages would break the packet's one page per receipt
   arithmetic, so they go.
4. Amounts. A vendor ``strip_regex`` is applied on its own and kept only when
   the fragment still holds as many money strings as before. A pattern that
   would take an amount with it is skipped and named on stderr, because a
   promo row that shares a table row with the total is a bad reason to lose
   the total.

Stdlib only. Regex over the markup, deliberately: these are email tables, the
input is one saved message at a time, and a parser dependency buys nothing here.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import html as html_module
import ipaddress
import json
import mimetypes
import re
import socket
import sys
from email.utils import parseaddr
from pathlib import Path
from urllib.error import URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, build_opener

SCOPE = ".rc"
# The name for no vendor rules at all, on the CLI and in what it prints.
GENERIC = "generic"
FETCH_TIMEOUT = 10
MAX_IMAGE_BYTES = 5 * 1024 * 1024

REQUIRED_KEYS = (
    "name",
    "display",
    "sender_domains",
    "subject_patterns",
    "strip_regex",
    "unwrap_links_matching",
    "amount_regex",
    "date_regex",
    "notes",
)
LIST_KEYS = (
    "sender_domains",
    "subject_patterns",
    "strip_regex",
    "unwrap_links_matching",
)

# At rules that wrap other rules. Their contents get scoped; the wrapper line
# itself is left alone. Anything else at rule shaped (font-face, keyframes,
# page) holds descriptors rather than selectors, so its body is left alone too.
NESTED_AT_RULES = {"media", "supports", "layer", "container", "document", "scope"}

COMMENT_RE = re.compile(r"<!--.*?-->", re.S)
SCRIPT_RE = re.compile(r"<script\b.*?</script\s*>", re.S | re.I)
LONE_SCRIPT_RE = re.compile(r"</?script\b[^>]*>", re.I)
STYLE_RE = re.compile(r"<style\b[^>]*>(.*?)</style\s*>", re.S | re.I)
# What is left once the complete blocks are out: an opener with no closer
# takes the CSS that follows it, up to the next tag, and the bare tokens go.
ORPHAN_STYLE_RE = re.compile(r"<style\b[^>]*>([^<]*)", re.I)
LONE_STYLE_RE = re.compile(r"</?style\b[^>]*>?", re.I)
HEAD_RE = re.compile(r"<head\b[^>]*>.*?</head\s*>", re.S | re.I)
BODY_RE = re.compile(r"<body\b[^>]*>(.*)</body\s*>", re.S | re.I)
DOCTYPE_RE = re.compile(r"<!doctype[^>]*>", re.I)
WRAPPER_RE = re.compile(r"</?(?:html|body)\b[^>]*>", re.I)
IMG_RE = re.compile(r"<img\b[^>]*>", re.I)
ANCHOR_RE = re.compile(r"<a\b([^>]*)>(.*?)</a\s*>", re.S | re.I)
OPEN_ANCHOR_RE = re.compile(r"<a\b([^>]*)>", re.I)
LINK_ATTR_RE = re.compile(
    r"""\s+(?:href|target|ping|rel|on\w+)\s*=\s*(?:"[^"]*"|'[^']*'|[^\s>]+)""", re.I
)
SRC_RE = re.compile(r"""src\s*=\s*(["'])(https?://[^"'\s]+)\1""", re.I)
PIXEL_HINT_RE = re.compile(r"open|track|pixel|beacon", re.I)
TINY_STYLE_RE = re.compile(r"(?:^|;)\s*(?:width|height)\s*:\s*[01](?:\.\d+)?\s*px", re.I)
BARE_PAGE_SELECTOR_RE = re.compile(r"(?<![\w.#\[-])(?:html|body)\b", re.I)

# Elements a receipt never needs and a packet must never carry: anything that
# loads a second document, anything interactive, and anything that can hold a
# script. Paired tags go with their contents; the void ones simply go.
DANGEROUS_ELEMENTS = (
    "iframe",
    "frame",
    "frameset",
    "object",
    "embed",
    "applet",
    "form",
    "input",
    "button",
    "textarea",
    "select",
    "base",
    "link",
    "meta",
    "svg",
    "math",
    "video",
    "audio",
    "source",
    "track",
    "noscript",
    "template",
)
# The void ones hold no content, so removing the tag removes the element.
VOID_ELEMENTS = ("base", "input", "link", "meta", "source", "track")
PAIRED_ELEMENTS = tuple(name for name in DANGEROUS_ELEMENTS if name not in VOID_ELEMENTS)
ELEMENT_BOUNDS = {
    name: (re.compile(rf"<{name}\b[^>]*>", re.I), re.compile(rf"</{name}\s*>", re.I))
    for name in PAIRED_ELEMENTS
}
LONE_ELEMENT_RE = re.compile(rf"</?(?:{'|'.join(DANGEROUS_ELEMENTS)})\b[^>]*>", re.I)

TAG_RE = re.compile(r"<([A-Za-z][-\w]*)([^>]*)>")
HANDLER_ATTR_RE = re.compile(r"""\s+on\w+\s*=\s*(?:"[^"]*"|'[^']*'|[^\s>]+)""", re.I)
SRCSET_ATTR_RE = re.compile(r"""\s+srcset\s*=\s*(?:"[^"]*"|'[^']*'|[^\s>]+)""", re.I)
URL_ATTR_RE = re.compile(
    r"""\s+(formaction|background|poster|action|href|src|data)\s*=\s*"""
    r"""("[^"]*"|'[^']*'|[^\s>]+)""",
    re.I,
)
STYLE_ATTR_RE = re.compile(r"""\s+style\s*=\s*("[^"]*"|'[^']*'|[^\s>]+)""", re.I)
URL_NOISE_RE = re.compile(r"[\s\x00-\x20\x7f]+")
BLOCKED_SCHEMES = ("javascript:", "data:text/html")
IMAGE_SCHEMES = ("http://", "https://", "data:image/")

CSS_IMPORT_RE = re.compile(r"@import\b[^;{}]*;?", re.I)
CSS_BANNED_DECL_RE = re.compile(
    r"[^;{}]*(?:expression\s*\(|-moz-binding|behavior\s*:)[^;{}]*;?", re.I
)
CSS_URL_RE = re.compile(r"""url\(\s*(?:"([^"]*)"|'([^']*)'|([^)]*?))\s*\)""", re.I)
# page-break-before and friends, and the modern break-before spelling. The
# lookbehind keeps word-break and line-break out of it.
CSS_BREAK_DECL_RE = re.compile(r"[^;{}]*(?<![-\w])(?:page-)?break-[-\w]+\s*:[^;{}]*;?", re.I)

# A money string as a receipt prints one. Counted before and after each
# vendor strip pattern, never parsed.
MONEY_RE = re.compile(r"\d+\.\d{2}")


# ------------------------------------------------------------------ attributes


def _attr(tag: str, name: str) -> str | None:
    """The value of one attribute of a tag, unquoted, or None."""
    pattern = rf"""(?<![-\w]){name}\s*=\s*("([^"]*)"|'([^']*)'|([^\s>]+))"""
    match = re.search(pattern, tag, re.I)
    if not match:
        return None
    for group in (2, 3, 4):
        if match.group(group) is not None:
            return match.group(group)
    return None


def _is_tracking_pixel(tag: str) -> bool:
    """True for a spacer or beacon image: one pixel, or a beacon style source."""
    for name in ("width", "height"):
        value = _attr(tag, name)
        if value is None:
            continue
        digits = re.match(r"\s*(\d+)", value)
        if digits and int(digits.group(1)) <= 1:
            return True
    if TINY_STYLE_RE.search(_attr(tag, "style") or ""):
        return True
    src = _attr(tag, "src") or ""
    if src.lower().startswith(("http://", "https://")) and PIXEL_HINT_RE.search(src):
        return True
    return False


def _unquote(raw: str) -> str:
    """An attribute value with one level of matching quotes taken off."""
    if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in "\"'":
        return raw[1:-1]
    return raw


def _scheme_of(value: str) -> str:
    """The value with entities decoded, whitespace squeezed out, lowercased.

    Whitespace and control characters are removed outright rather than
    stripped, because a scheme split across them (``java`` newline ``script:``)
    is read as one scheme by a browser and has to be read the same way here.
    """
    return URL_NOISE_RE.sub("", html_module.unescape(value)).lower()


# ------------------------------------------------------------------- elements


def _strip_element(html: str, name: str) -> str:
    """Remove every ``name`` element and everything nested inside it.

    Depth counted rather than matched non-greedily, so one element wrapped in
    another of the same name leaves no inner content behind. An element that is
    never closed takes the rest of the fragment with it, which is what a
    browser would treat as its content anyway.
    """
    opener, closer = ELEMENT_BOUNDS[name]
    out: list[str] = []
    index = 0
    while True:
        start = opener.search(html, index)
        if start is None:
            out.append(html[index:])
            return "".join(out)
        out.append(html[index : start.start()])
        if start.group(0).rstrip().endswith("/>"):
            index = start.end()
            continue
        depth = 1
        cursor = start.end()
        while depth:
            next_open = opener.search(html, cursor)
            next_close = closer.search(html, cursor)
            if next_close is None:
                cursor = len(html)
                break
            if next_open is not None and next_open.start() < next_close.start():
                depth += 1
                cursor = next_open.end()
            else:
                depth -= 1
                cursor = next_close.end()
        index = cursor


def _strip_elements(html: str) -> str:
    """Remove every dangerous element, with its contents, then any stray tag."""
    for name in PAIRED_ELEMENTS:
        html = _strip_element(html, name)
    return LONE_ELEMENT_RE.sub("", html)


def _defuse_tag(match: re.Match[str]) -> str:
    """One tag with its handlers, live URLs, srcset and CSS made harmless."""
    name, attrs = match.group(1), match.group(2)
    is_image = name.lower() == "img"

    attrs = HANDLER_ATTR_RE.sub("", attrs)
    attrs = SRCSET_ATTR_RE.sub("", attrs)

    def url_attr(found: re.Match[str]) -> str:
        attribute = found.group(1).lower()
        scheme = _scheme_of(_unquote(found.group(2)))
        if scheme.startswith(BLOCKED_SCHEMES):
            return ""
        if is_image and attribute == "src" and not scheme.startswith(IMAGE_SCHEMES):
            return ""
        return found.group(0)

    def style_attr(found: re.Match[str]) -> str:
        raw = found.group(1)
        quote = raw[0] if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in "\"'" else '"'
        cleaned = sanitize_css(_unquote(raw)).strip()
        if not cleaned:
            return ""
        return f" style={quote}{cleaned}{quote}"

    attrs = URL_ATTR_RE.sub(url_attr, attrs)
    attrs = STYLE_ATTR_RE.sub(style_attr, attrs)
    return f"<{name}{attrs}>"


def _defuse_tags(html: str) -> str:
    """Apply ``_defuse_tag`` to every opening tag in the fragment."""
    return TAG_RE.sub(_defuse_tag, html)


# ------------------------------------------------------------------------ css


def sanitize_css(css: str) -> str:
    """Drop what CSS can use to fetch or run, keep what makes a receipt look right.

    Four removals and one rewrite. ``@import`` rules go, because they pull a
    second stylesheet. Declarations holding ``expression(``, ``-moz-binding``
    or ``behavior:`` go, because each of those runs code in some browser. Page
    break declarations go, because the packet gives each receipt one page and
    counts on getting it. Every other ``url(...)`` becomes ``none`` unless it
    is a data URI, so no rule can reach the network. Everything else is left
    exactly as the vendor wrote it.
    """
    css = CSS_IMPORT_RE.sub("", css)
    css = CSS_BANNED_DECL_RE.sub("", css)
    css = CSS_BREAK_DECL_RE.sub("", css)

    def rewrite(found: re.Match[str]) -> str:
        value = next(group for group in found.groups() if group is not None)
        if _scheme_of(value).startswith("data:"):
            return found.group(0)
        return "none"

    return CSS_URL_RE.sub(rewrite, css)


def _scope_selectors(prelude: str, scope: str = SCOPE) -> str:
    """Prefix each selector in a comma list, rewriting bare html and body."""
    lead = prelude[: len(prelude) - len(prelude.lstrip())]
    parts = []
    for raw in prelude.split(","):
        selector = raw.strip()
        if not selector:
            continue
        rewritten = BARE_PAGE_SELECTOR_RE.sub(scope, selector)
        parts.append(rewritten if rewritten != selector else f"{scope} {selector}")
    if not parts:
        return prelude
    return lead + ", ".join(parts)


def _scope_css(css: str, scope: str = SCOPE) -> str:
    """Scope every rule in a stylesheet to the receipt container."""
    out: list[str] = []
    index = 0
    length = len(css)
    while index < length:
        if css.startswith("/*", index):
            end = css.find("*/", index + 2)
            end = length if end < 0 else end + 2
            out.append(css[index:end])
            index = end
            continue
        brace = css.find("{", index)
        if brace < 0:
            out.append(css[index:])
            break
        semicolon = css.find(";", index)
        if 0 <= semicolon < brace:
            out.append(css[index : semicolon + 1])
            index = semicolon + 1
            continue
        prelude = css[index:brace]
        body, index = _read_block(css, brace + 1)
        stripped = prelude.strip()
        if stripped.startswith("@"):
            named = re.match(r"@([-\w]+)", stripped)
            at_rule = named.group(1).lower() if named else ""
            inner = _scope_css(body, scope) if at_rule in NESTED_AT_RULES else body
            out.append(f"{prelude}{{{inner}}}")
        else:
            out.append(f"{_scope_selectors(prelude, scope)}{{{body}}}")
    return "".join(out)


def _read_block(css: str, start: int) -> tuple[str, int]:
    """Text of a brace block starting just after its open brace, and the index after it."""
    depth = 1
    index = start
    length = len(css)
    while index < length and depth:
        if css.startswith("/*", index):
            end = css.find("*/", index + 2)
            index = length if end < 0 else end + 2
            continue
        char = css[index]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
        index += 1
    if depth:
        return css[start:], length
    return css[start : index - 1], index


# --------------------------------------------------------------------- images


def cache_name(url: str) -> str:
    """The cache file name for an image URL."""
    return hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]


def _mime_for(url: str) -> str:
    return mimetypes.guess_type(urlsplit(url).path)[0] or "image/png"


def inline_images(html: str, cache_dir: Path) -> str:
    """Swap cached remote images for data URIs. Never opens the network."""
    cache_dir = Path(cache_dir)

    def replace(match: re.Match[str]) -> str:
        quote, url = match.group(1), match.group(2)
        try:
            blob = (cache_dir / cache_name(url)).read_bytes()
        except OSError:
            return match.group(0)
        if not blob:
            return match.group(0)
        encoded = base64.b64encode(blob).decode("ascii")
        return f"src={quote}data:{_mime_for(url)};base64,{encoded}{quote}"

    return SRC_RE.sub(replace, html)


class _NoRedirect(HTTPRedirectHandler):
    """A redirect handler that refuses instead of following.

    A vendor image URL that redirects is how a fetch ends up somewhere the
    host check never saw, so the fetch stops at the first hop.
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D102
        raise URLError(f"refused a redirect ({code})")


urlopen = build_opener(_NoRedirect).open


def _host_is_public(host: str) -> bool:
    """True when the host resolves to an address outside this machine and its network."""
    if not host:
        return False
    try:
        address = ipaddress.ip_address(socket.gethostbyname(host))
    except (OSError, ValueError):
        return False
    return not (
        address.is_loopback
        or address.is_private
        or address.is_link_local
        or address.is_reserved
        or address.is_multicast
        or address.is_unspecified
    )


def fetch_images(html: str, cache_dir: Path) -> int:
    """Download every remote image not already cached. CLI only. Returns the count.

    Three limits, all of them about where the bytes come from rather than what
    they contain. A host that resolves to a loopback, private or link-local
    address is refused, so a receipt cannot aim the fetch at this machine or
    the network around it. Redirects are refused, so no URL can leave the host
    that was checked. Each read stops at ``MAX_IMAGE_BYTES``.
    """
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    fetched = 0
    seen: set[str] = set()
    for match in SRC_RE.finditer(html):
        url = match.group(2)
        if url in seen:
            continue
        seen.add(url)
        target = cache_dir / cache_name(url)
        if target.is_file() and target.stat().st_size > 0:
            continue
        if not _host_is_public(urlsplit(url).hostname or ""):
            print("clean: image not fetched (host refused)", file=sys.stderr)
            continue
        try:
            with urlopen(url, timeout=FETCH_TIMEOUT) as response:  # noqa: S310
                blob = response.read(MAX_IMAGE_BYTES + 1)
        except Exception as error:
            print(f"clean: image not fetched ({type(error).__name__})", file=sys.stderr)
            continue
        if len(blob) > MAX_IMAGE_BYTES:
            print("clean: image not fetched (over the size cap)", file=sys.stderr)
            continue
        if blob:
            target.write_bytes(blob)
            fetched += 1
    return fetched


# ---------------------------------------------------------------------- links


def _unwrap_links(html: str, patterns: list[str]) -> str:
    """Replace anchors whose href matches any pattern with the text they wrapped."""

    def replace(match: re.Match[str]) -> str:
        href = _attr(match.group(1), "href") or ""
        for pattern in patterns:
            if re.search(pattern, href, re.I):
                return match.group(2)
        return match.group(0)

    return ANCHOR_RE.sub(replace, html)


def _defuse_anchors(html: str) -> str:
    """Keep every remaining anchor and its styling, drop what makes it live."""
    return OPEN_ANCHOR_RE.sub(lambda m: "<a" + LINK_ATTR_RE.sub("", m.group(1)) + ">", html)


# ---------------------------------------------------------------------- rules


def _rule_problems(loaded: object) -> list[str]:
    problems: list[str] = []
    if not isinstance(loaded, dict):
        return ["rules must hold a JSON object"]
    for key in REQUIRED_KEYS:
        if key not in loaded:
            problems.append(f"missing key {key}")
    for key in LIST_KEYS:
        if key in loaded and not isinstance(loaded[key], list):
            problems.append(f"{key} must be a list")
    if "name" in loaded and not isinstance(loaded["name"], str):
        problems.append("name must be a string")
    return problems


def load_vendor_rules(vendors_dir: Path) -> dict[str, dict]:
    """Read every vendors/<name>/rules.json. Nothing is compiled here."""
    vendors_dir = Path(vendors_dir)
    rules: dict[str, dict] = {}
    for path in sorted(vendors_dir.glob("*/rules.json")):
        loaded = json.loads(path.read_text(encoding="utf-8"))
        problems = _rule_problems(loaded)
        if problems:
            raise ValueError(f"{path.as_posix()}: " + "; ".join(problems))
        rules[loaded["name"]] = loaded
    return rules


def _sender_domain(from_addr: str) -> str:
    """The domain part of a From header, however it is wrapped.

    ``parseaddr`` does the unwrapping, so a display name, angle brackets and a
    trailing comment all fall away. A header carrying two addresses parses as
    nothing at all, so the first one is then read on its own: the vendor is
    whoever sent the message, never whoever was added after the comma.
    """
    header = from_addr or ""
    address = parseaddr(header)[1]
    if "@" not in address:
        address = parseaddr(header.split(",")[0])[1]
    if "@" not in address:
        return ""
    return address.rsplit("@", 1)[1].strip().lower()


def detect_vendor(from_addr: str, subject: str, rules: dict) -> str | None:
    """Name the vendor from the sender domain, else from the subject, else None.

    Vendors with sender domains are tried first and catch alls last, then by
    name, so a subject that two vendors both claim goes to the specific one.
    """
    domain = _sender_domain(from_addr)
    order = sorted(rules, key=lambda name: (not (rules[name].get("sender_domains") or []), name))
    if domain:
        for name in order:
            for raw in rules[name].get("sender_domains") or []:
                candidate = raw.strip().lower().lstrip("@")
                if candidate and (domain == candidate or domain.endswith("." + candidate)):
                    return name
    for name in order:
        for pattern in rules[name].get("subject_patterns") or []:
            if re.search(pattern, subject or "", re.I):
                return name
    return None


def read_meta(source: Path) -> dict:
    """The From and Subject fetch saved beside a receipt, or an empty dict.

    ``fetch.py`` writes ``<rid>.meta.json`` next to ``<rid>.html``. A file that
    is missing, unreadable or not an object leaves the cleaner where it would
    have been without it, on the generic rules.
    """
    path = Path(source).with_suffix(".meta.json")
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


def detect_from_meta(source: Path, vendors_dir: Path) -> tuple[str, dict | None]:
    """The vendor named by a receipt's saved headers, and its rules.

    Returns ``("generic", None)`` when there are no headers to read, when the
    headers name nobody the rules know, or when there are no rules at all.
    """
    meta = read_meta(source)
    if not meta:
        return GENERIC, None
    available = load_vendor_rules(vendors_dir)
    name = detect_vendor(str(meta.get("from") or ""), str(meta.get("subject") or ""), available)
    if name is None:
        return GENERIC, None
    return name, available[name]


# ---------------------------------------------------------------------- clean


def _apply_strip_patterns(body: str, rules: dict) -> str:
    """Apply a vendor's strip patterns, one at a time, keeping every amount.

    A pattern is kept only when the fragment still prints as many money
    strings as it did before. Vendor rows are written by the vendor, and a
    promo that shares a table row with the total is common enough that a
    pattern which takes the total with it has to lose rather than the total.
    """
    patterns = rules.get("strip_regex") or []
    vendor = str(rules.get("name") or "generic")
    kept = len(MONEY_RE.findall(body))
    for index, pattern in enumerate(patterns):
        candidate = re.sub(pattern, "", body, flags=re.S | re.I)
        found = len(MONEY_RE.findall(candidate))
        if found < kept:
            print(
                f"clean: {vendor} strip pattern {index} skipped, "
                "it would have taken an amount with it",
                file=sys.stderr,
            )
            continue
        body = candidate
        kept = found
    return body


def _take_styles(body: str) -> tuple[str, str]:
    """The fragment with every style block out of it, and the CSS that was in them.

    Complete blocks first. What is left is an opener with no closer, which a
    browser reads as a stylesheet running to the end of the document: the CSS
    up to the next tag is taken as CSS, the tokens themselves go, and the
    markup after them stays in the receipt instead of vanishing into a style.
    """
    styles = "".join(STYLE_RE.findall(body))
    body = STYLE_RE.sub("", body)
    styles += "".join(ORPHAN_STYLE_RE.findall(body))
    body = ORPHAN_STYLE_RE.sub("", body)
    return LONE_STYLE_RE.sub("", body), styles


def clean_html(raw: str, rules: dict | None = None, image_cache: Path | None = None) -> str:
    """Return the receipt fragment: vendor markup, scoped CSS, nothing live."""
    rules = rules or {}
    body = raw or ""

    body = COMMENT_RE.sub("", body)
    body, styles = _take_styles(body)
    body = SCRIPT_RE.sub("", body)
    body = LONE_SCRIPT_RE.sub("", body)
    body = HEAD_RE.sub("", body)

    inside = BODY_RE.search(body)
    if inside:
        body = inside.group(1)
    body = DOCTYPE_RE.sub("", body)
    body = WRAPPER_RE.sub("", body)
    body = _strip_elements(body)
    body = IMG_RE.sub(lambda m: "" if _is_tracking_pixel(m.group(0)) else m.group(0), body)

    body = _apply_strip_patterns(body, rules)

    unwrap = rules.get("unwrap_links_matching") or []
    if unwrap:
        body = _unwrap_links(body, unwrap)
    body = _defuse_anchors(body)
    body = _defuse_tags(body)

    fragment = body.strip()
    scoped = _scope_css(sanitize_css(styles)).strip()
    if scoped:
        fragment = f"<style>{scoped}</style>\n{fragment}"
    if image_cache is not None:
        fragment = inline_images(fragment, Path(image_cache))
    return fragment


# ------------------------------------------------------------------------ cli


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Clean a vendor receipt email for a packet.")
    parser.add_argument("source", type=Path, help="the saved receipt HTML")
    parser.add_argument("--out", type=Path, required=True, help="where to write the fragment")
    parser.add_argument(
        "--vendor",
        help="vendor name, or generic to force the generic clean; "
        "read from the saved headers when it is not given",
    )
    parser.add_argument("--vendors", type=Path, default=Path("vendors"), help="rules directory")
    parser.add_argument("--images", type=Path, help="image cache directory")
    parser.add_argument(
        "--fetch-images", action="store_true", help="download missing images into the cache"
    )
    args = parser.parse_args(argv)

    raw = args.source.read_text(encoding="utf-8", errors="replace")

    rules = None
    if args.vendor == GENERIC:
        print(f"clean: vendor {GENERIC}", file=sys.stderr)
    elif args.vendor:
        available = load_vendor_rules(args.vendors)
        if args.vendor not in available:
            print(f"clean: no rules for vendor {args.vendor}", file=sys.stderr)
            return 2
        rules = available[args.vendor]
    else:
        name, rules = detect_from_meta(args.source, args.vendors)
        print(f"clean: vendor {name}", file=sys.stderr)

    if args.fetch_images and args.images is None:
        print("clean: --fetch-images needs --images DIR", file=sys.stderr)
        return 2

    # Fetch from the cleaned fragment, never the raw message. The raw message
    # still holds the tracking pixels, and downloading one is exactly the
    # phone-home the cleaner exists to prevent.
    cleaned = clean_html(raw, rules)
    if args.fetch_images:
        fetch_images(cleaned, args.images)
    if args.images is not None:
        cleaned = inline_images(cleaned, args.images)
    if args.out.parent != Path(""):
        args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(cleaned + "\n", encoding="utf-8")
    print(f"clean: wrote {args.out.as_posix()}, {len(cleaned)} characters")
    return 0


if __name__ == "__main__":
    sys.exit(main())
