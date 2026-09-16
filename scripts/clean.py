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
  that is not a ``data:image/`` or ``data:font/`` URI, which becomes ``none``.
- An image ``src`` that is not http, https or ``data:image/``.
- Any ``<style`` or ``</style`` token left over once the complete style blocks
  have been taken out, so a message truncated in the middle of a stylesheet
  cannot swallow the text that follows it.

Three choices are worth stating plainly, because whoever reads a packet should
know what was done to the page in front of them.

1. Links. A tracking link is replaced by the text it wrapped. Four shapes are
   unwrapped for every vendor, listed once in ``GENERIC_UNWRAP``, and a
   rules.json adds the shapes that are its own in ``unwrap_links_matching``.
   Every other anchor keeps its tag and its styling but loses ``href``,
   ``target`` and any handler attribute, so the wording and the vendor look
   survive and nothing in a packet is clickable.
2. Images. ``inline_images`` reads from a cache directory and never opens a
   socket. Only the CLI, and only with ``--fetch-images``, downloads anything,
   and it downloads from the cleaned fragment, so a tracking pixel the cleaner
   has already removed is never requested. A fetch refuses a host that resolves
   to a loopback, private or link-local address, follows no redirect, and stops
   reading at five megabytes. The substring test for tracking pixels (open,
   track, pixel, beacon) runs on http and https sources only, because a base64
   data URI can contain those letters by chance and a logo is not a pixel. A
   URL that answers with something other than a picture goes into the cache's
   failure record, and an image named there is replaced by its alt text rather
   than left to render as a broken image icon in a packet. An image the cache
   simply does not hold is left as the vendor wrote it.
3. CSS. Style blocks are kept, not dropped, so the receipt still looks like the
   receipt. Every rule is sanitised, then scoped to the ``.rc`` receipt
   container, so one vendor's stylesheet cannot reach another vendor's receipt
   on the same page. Page break declarations are the one exception: a receipt
   that split across two pages would break the packet's one page per receipt
   arithmetic, so they go.
4. Amounts. A vendor ``strip_regex`` is applied on its own and kept only when
   the fragment still prints as many money strings as before. A pattern that
   would take an amount with it is skipped and named on stderr, because a
   promo row that shares a table row with the total is a bad reason to lose
   the total. The count reads the text a reader would see and not the markup
   around it, because a style attribute is full of decimals
   (``line-height:1.25rem``) and none of them is an amount.
5. Rewrites. A vendor may also carry an optional ``replace`` list of
   ``[regex, replacement]`` pairs, applied with ``re.sub`` behind the same
   amount guard. It is for markup a vendor laid out for a mail client's width
   that a packet's narrower column squeezes: an Uber total row gives the word
   Total a cell at ``width:100%``, which leaves the amount beside it one
   character per line. The pairs run first, ahead of every pass that
   sanitises, so a replacement can put a script or an iframe into the fragment
   and the fragment still comes out with nothing in it that can act.

``clean_dir`` cleans a whole directory, one receipt after another in filename
order. It is the stage the workflow runs beside ``cards.py`` and the folio
read. Sequential rather than pooled: the strip-pattern warnings come out in
the order the files are listed with nothing to collect or re-sort, and one
shared ``failed-images.json`` can no longer be written by two cleans at once.

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
import shutil
import socket
import sys
from email.utils import parseaddr
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, build_opener

SCOPE = ".rc"
# The name for no vendor rules at all, on the CLI and in what it prints.
GENERIC = "generic"
FETCH_TIMEOUT = 10
MAX_IMAGE_BYTES = 5 * 1024 * 1024
# What a fetch leaves in the cache beside the pictures: the URLs it asked for
# and got something other than a picture from. The name is not sixteen hex
# characters, so it can never collide with a cache entry.
FAILED_RECORD = "failed-images.json"
# The first bytes of the four raster formats a receipt ever uses. WEBP is the
# only one that needs two windows, because its marker sits after the RIFF size.
IMAGE_SIGNATURES = (
    b"\x89PNG\r\n\x1a\n",
    b"\xff\xd8\xff",
    b"GIF87a",
    b"GIF89a",
)

# What a directory clean carries across untouched. A text body, a folio and a
# receipt photo are already what the packet needs; only markup gets cleaned.
COPY_SUFFIXES = (".txt", ".pdf", ".png", ".jpg")
CLEAN_SUFFIX = ".html"

REQUIRED_KEYS = (
    "name",
    "display",
    "sender_domains",
    "subject_patterns",
    "strip_regex",
    "unwrap_links_matching",
    "notes",
)
LIST_KEYS = (
    "sender_domains",
    "subject_patterns",
    "strip_regex",
    "unwrap_links_matching",
)
# The keys whose every entry is a regex, checked when the rules are loaded.
PATTERN_KEYS = ("strip_regex", "subject_patterns", "unwrap_links_matching")

# The tracking link shapes every vendor sends, unwrapped for all of them. A
# rules.json lists only the shapes that are its own, so a sender domain that
# wraps its links in ``click.`` never has to say so.
GENERIC_UNWRAP = (r"click\.", r"/track", r"utm_", r"email\.")

# At rules that wrap other rules. Their contents get scoped; the wrapper line
# itself is left alone. Anything else at rule shaped (font-face, keyframes,
# page) holds descriptors rather than selectors, so its body is left alone too.
NESTED_AT_RULES = {"media", "supports", "layer", "container", "document", "scope"}

# The run of attributes between a tag's name and the bracket that closes the
# tag. A quoted value is taken whole, so a ``>`` written inside one is content
# and not the end of the tag: ``<a title="1 > 2" onclick="evil()">`` is read as
# one tag, and the handler on it is found and defused. Every regex that walks a
# tag uses this rather than ``[^>]*``, because a tag the pattern cut short is a
# tag whose remaining attributes were never looked at.
#
# Possessive, so the run is read once, left to right. A quote character can be
# taken either as the start of a quoted value or as one more plain character,
# and without the possessive quantifier a tag full of quotes that never reaches
# a bracket would be retried every way round.
ATTRS = r"""(?:"[^"]*"|'[^']*'|[^>])*+"""

COMMENT_RE = re.compile(r"<!--.*?-->", re.S)
SCRIPT_RE = re.compile(r"<script\b.*?</script\s*>", re.S | re.I)
LONE_SCRIPT_RE = re.compile(rf"</?script\b{ATTRS}>", re.I)
STYLE_RE = re.compile(rf"<style\b{ATTRS}>(.*?)</style\s*>", re.S | re.I)
# What is left once the complete blocks are out: an opener with no closer
# takes the CSS that follows it, up to the next tag, and the bare tokens go.
ORPHAN_STYLE_RE = re.compile(rf"<style\b{ATTRS}>([^<]*)", re.I)
LONE_STYLE_RE = re.compile(rf"</?style\b{ATTRS}>?", re.I)
HEAD_RE = re.compile(rf"<head\b{ATTRS}>.*?</head\s*>", re.S | re.I)
BODY_RE = re.compile(rf"<body\b{ATTRS}>(.*)</body\s*>", re.S | re.I)
DOCTYPE_RE = re.compile(rf"<!doctype{ATTRS}>", re.I)
WRAPPER_RE = re.compile(rf"</?(?:html|body)\b{ATTRS}>", re.I)
IMG_RE = re.compile(rf"<img\b{ATTRS}>", re.I)
ANCHOR_RE = re.compile(rf"<a\b({ATTRS})>(.*?)</a\s*>", re.S | re.I)
OPEN_ANCHOR_RE = re.compile(rf"<a\b({ATTRS})>", re.I)
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
    name: (re.compile(rf"<{name}\b{ATTRS}>", re.I), re.compile(rf"</{name}\s*>", re.I))
    for name in PAIRED_ELEMENTS
}
LONE_ELEMENT_RE = re.compile(rf"</?(?:{'|'.join(DANGEROUS_ELEMENTS)})\b{ATTRS}>", re.I)

TAG_RE = re.compile(rf"<([A-Za-z][-\w]*)({ATTRS})>")
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
# The only data URIs a receipt's CSS has any use for. A data:text/html in a
# url() is a document, not a picture, so it goes the way a remote URL does.
CSS_DATA_SCHEMES = ("data:image/", "data:font/")
# Comments and whitespace in front of a rule's selector or at-rule keyword.
# Taken off before the keyword is read, so a commented @media is still an
# at-rule and is never handed to the selector scoping as if it were one.
CSS_LEAD_RE = re.compile(r"\A\s*(?:/\*.*?\*/\s*)+", re.S)

# A money string as a receipt prints one. Counted before and after each vendor
# strip pattern, never parsed. The comma is there because a euro receipt writes
# its total as 88,60 and the guard has to see that amount to protect it.
MONEY_RE = re.compile(r"(?<![\d.,])\d{1,3}(?:,\d{3})*[.,]\d{2}(?!\d)|(?<![\d.,])\d+[.,]\d{2}(?!\d)")
# Everything between angle brackets, taken out before the counting so that the
# decimals CSS is made of are never mistaken for money.
MARKUP_RE = re.compile(r"<[^>]*>")


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
    is a picture or a font written inline, so no rule can reach the network and
    no rule can hand a browser a second document to parse. Everything else is
    left exactly as the vendor wrote it.
    """
    css = CSS_IMPORT_RE.sub("", css)
    css = CSS_BANNED_DECL_RE.sub("", css)
    css = CSS_BREAK_DECL_RE.sub("", css)

    def rewrite(found: re.Match[str]) -> str:
        value = next(group for group in found.groups() if group is not None)
        if _scheme_of(value).startswith(CSS_DATA_SCHEMES):
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
        lead = CSS_LEAD_RE.match(prelude)
        head = prelude[lead.end() :] if lead else prelude
        stripped = head.strip()
        if stripped.startswith("@"):
            named = re.match(r"@([-\w]+)", stripped)
            at_rule = named.group(1).lower() if named else ""
            inner = _scope_css(body, scope) if at_rule in NESTED_AT_RULES else body
            out.append(f"{prelude}{{{inner}}}")
        else:
            front = prelude[: lead.end()] if lead else ""
            out.append(f"{front}{_scope_selectors(head, scope)}{{{body}}}")
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


def _is_cached(cache_dir: Path, url: str) -> bool:
    """True when the cache holds bytes for this URL."""
    target = Path(cache_dir) / cache_name(url)
    return target.is_file() and target.stat().st_size > 0


def _looks_like_an_image(blob: bytes) -> bool:
    """True when these first bytes open a PNG, JPEG, GIF or WEBP file."""
    if blob.startswith(IMAGE_SIGNATURES):
        return True
    return blob.startswith(b"RIFF") and blob[8:12] == b"WEBP"


def failed_images(cache_dir: Path) -> set[str]:
    """The URLs a fetch into this cache asked for and got no picture from.

    A missing, unreadable or misshapen record reads as no failures at all, so
    a cache written by hand behaves exactly like one no fetch has touched.
    """
    try:
        loaded = json.loads((Path(cache_dir) / FAILED_RECORD).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return set()
    if not isinstance(loaded, list):
        return set()
    return {url for url in loaded if isinstance(url, str)}


def _record_failures(cache_dir: Path, failed: set[str]) -> None:
    """Merge this run's failures into the record, minus anything now cached.

    Merged rather than overwritten because a fetch is run one receipt at a
    time against one shared cache, and each run sees only its own URLs. The
    same holds for a run that could not reach a host at all: it learns nothing
    about that URL and so takes nothing away from the record. The one thing
    that does come out is a URL that answers with a picture on a later run,
    because at that point it is cached and the record would be describing a
    failure that is over.
    """
    cache_dir = Path(cache_dir)
    listed = sorted(
        url for url in failed_images(cache_dir) | failed if not _is_cached(cache_dir, url)
    )
    record = cache_dir / FAILED_RECORD
    if not listed:
        record.unlink(missing_ok=True)
        return
    record.write_text(json.dumps(listed, indent=2) + "\n", encoding="utf-8")


def drop_failed_images(html: str, cache_dir: Path) -> str:
    """Replace every image the cache records as unfetchable with its alt text.

    A URL that answered 403, 404 or with something that is not a picture will
    answer the same way inside a packet, where it renders as a browser's
    broken image icon on a page that is meant to read as a receipt. The tag
    becomes a ``span`` carrying the alt text the vendor wrote, which is empty
    when the vendor wrote none.

    An image the cache simply does not hold is left exactly as it is: an
    offline clean attempts no fetch, learns nothing about that URL, and has no
    business deciding the picture is gone.
    """
    failed = failed_images(cache_dir)
    if not failed:
        return html

    def replace(match: re.Match[str]) -> str:
        tag = match.group(0)
        if (_attr(tag, "src") or "") not in failed:
            return tag
        alt = html_module.unescape(_attr(tag, "alt") or "").strip()
        return f'<span class="img-alt">{html_module.escape(alt, quote=False)}</span>'

    return IMG_RE.sub(replace, html)


def inline_images(html: str, cache_dir: Path) -> str:
    """Apply a cache directory to a fragment. Never opens the network.

    A cached image becomes a data URI. An image the cache records as
    unfetchable becomes its alt text. Everything else is left alone.
    """
    cache_dir = Path(cache_dir)
    html = drop_failed_images(html, cache_dir)

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

    A URL that answers, and answers with something other than a picture, is
    written to the cache's failure record: an HTTP status that is not 200, or
    a body that opens with none of the four image signatures. A fetch that
    never got an answer at all, because the host refused or the network is
    down, records nothing, because the only thing learned there is that this
    machine was offline.
    """
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    fetched = 0
    failed: set[str] = set()
    seen: set[str] = set()
    for match in SRC_RE.finditer(html):
        url = match.group(2)
        if url in seen:
            continue
        seen.add(url)
        if _is_cached(cache_dir, url):
            continue
        if not _host_is_public(urlsplit(url).hostname or ""):
            print("clean: image not fetched (host refused)", file=sys.stderr)
            continue
        try:
            with urlopen(url, timeout=FETCH_TIMEOUT) as response:  # noqa: S310
                blob = response.read(MAX_IMAGE_BYTES + 1)
        except HTTPError as error:
            print(f"clean: image not fetched (HTTP {error.code})", file=sys.stderr)
            failed.add(url)
            continue
        except Exception as error:
            print(f"clean: image not fetched ({type(error).__name__})", file=sys.stderr)
            continue
        if len(blob) > MAX_IMAGE_BYTES:
            print("clean: image not fetched (over the size cap)", file=sys.stderr)
            continue
        if not _looks_like_an_image(blob):
            print("clean: image not fetched (the answer was not an image)", file=sys.stderr)
            failed.add(url)
            continue
        (cache_dir / cache_name(url)).write_bytes(blob)
        fetched += 1
    _record_failures(cache_dir, failed)
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
    problems.extend(_pattern_problems(loaded))
    problems.extend(_replace_problems(loaded))
    return problems


def _pattern_problems(loaded: dict) -> list[str]:
    """Every pattern a vendor wrote, compiled here rather than mid clean.

    A pattern that does not compile raises from wherever it was first used,
    which is halfway through a receipt and says nothing about which file the
    bad pattern is in. Compiling at load time names the key and the index.
    """
    problems: list[str] = []
    for key in PATTERN_KEYS:
        entries = loaded.get(key)
        if not isinstance(entries, list):
            continue
        for index, pattern in enumerate(entries):
            if not isinstance(pattern, str):
                continue
            try:
                re.compile(pattern)
            except re.error as error:
                problems.append(f"{key} {index} does not compile: {error}")
    return problems


def _replace_problems(loaded: dict) -> list[str]:
    """What is wrong with the optional replace field, if the rules carry one.

    The field is optional, so a rules.json without it is complete. A rules.json
    with it has to hold a list of two string pairs, because every pair is fed
    straight to ``re.sub`` as a pattern and a replacement, and both halves have
    to be something ``re.sub`` will accept.
    """
    if "replace" not in loaded:
        return []
    pairs = loaded["replace"]
    if not isinstance(pairs, list):
        return ["replace must be a list"]
    for pair in pairs:
        if not isinstance(pair, list) or len(pair) != 2:
            return ["replace must hold pairs"]
        if not all(isinstance(half, str) for half in pair):
            return ["replace pairs must hold two strings"]
    problems: list[str] = []
    for index, (pattern, replacement) in enumerate(pairs):
        try:
            compiled = re.compile(pattern)
        except re.error as error:
            problems.append(f"replace {index} does not compile: {error}")
            continue
        try:
            _stand_in_for(compiled).sub(replacement, "")
        except re.error as error:
            problems.append(f"replace {index} is not a usable replacement: {error}")
    return problems


def _stand_in_for(compiled: re.Pattern[str]) -> re.Pattern[str]:
    """A pattern that matches nothing but carries the same groups as this one.

    ``re.sub`` only reads a replacement template where the pattern matched, so
    a template naming a group that does not exist would go unnoticed until a
    receipt happened to match. Substituting on the empty string against this
    stand in, which has the same groups by number and by name, reads the
    template once at load time instead.
    """
    names = {index: name for name, index in compiled.groupindex.items()}
    parts = [
        f"(?P<{names[index]}>)" if index in names else "()"
        for index in range(1, compiled.groups + 1)
    ]
    return re.compile("".join(parts))


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


def _domain_fits(rule: dict, domain: str) -> bool:
    """True when a vendor claims this sender domain, or a parent of it."""
    for raw in rule.get("sender_domains") or []:
        candidate = raw.strip().lower().lstrip("@")
        if candidate and (domain == candidate or domain.endswith("." + candidate)):
            return True
    return False


def _subject_fits(rule: dict, subject: str) -> bool:
    """True when any of a vendor's subject patterns matches this subject."""
    return any(
        re.search(pattern, subject or "", re.I) for pattern in rule.get("subject_patterns") or []
    )


def detect_vendor(from_addr: str, subject: str, rules: dict) -> str | None:
    """Name the vendor from the sender domain, else from the subject, else None.

    Vendors with sender domains are tried first and catch alls last, then by
    name, so a subject that two vendors both claim goes to the specific one.

    Among the vendors that claim the sender domain, one whose subject patterns
    also fit wins over one that matches by domain alone. Uber rides and Uber
    Eats both send from uber.com and the subject line is the only thing that
    tells an order from a trip, so the domain cannot be the last word.
    """
    domain = _sender_domain(from_addr)
    order = sorted(rules, key=lambda name: (not (rules[name].get("sender_domains") or []), name))
    claimed = [name for name in order if domain and _domain_fits(rules[name], domain)]
    for name in claimed:
        if _subject_fits(rules[name], subject):
            return name
    if claimed:
        return claimed[0]
    for name in order:
        if _subject_fits(rules[name], subject):
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


def _printed_amounts(fragment: str) -> int:
    """How many money strings a fragment prints, reading its text only.

    The tags come out before the counting. A receipt's markup carries decimals
    that are not money and never were: ``line-height:1.25rem``,
    ``letter-spacing:0.15px``, ``width:33.33%``. Counting those would make the
    guard refuse to strip a promo module over a font size, so the count reads
    what a reader would see printed on the page and nothing else.
    """
    return len(MONEY_RE.findall(MARKUP_RE.sub(" ", fragment)))


def _apply_strip_patterns(body: str, rules: dict) -> tuple[str, list[str]]:
    """Apply a vendor's strip patterns, one at a time, keeping every amount.

    A pattern is kept only when the fragment still prints as many money
    strings as it did before. Vendor rows are written by the vendor, and a
    promo that shares a table row with the total is common enough that a
    pattern which takes the total with it has to lose rather than the total.

    Returns the fragment and the lines describing what was skipped. The lines
    come back rather than going to stderr here, so a caller cleaning a whole
    directory prints them in whatever order it is working through its files.
    """
    patterns = rules.get("strip_regex") or []
    vendor = str(rules.get("name") or GENERIC)
    warnings: list[str] = []
    kept = _printed_amounts(body)
    for index, pattern in enumerate(patterns):
        candidate = re.sub(pattern, "", body, flags=re.S | re.I)
        found = _printed_amounts(candidate)
        if found < kept:
            warnings.append(
                f"clean: {vendor} strip pattern {index} skipped, "
                "it would have taken an amount with it"
            )
            continue
        body = candidate
        kept = found
    return body, warnings


def _apply_replacements(body: str, rules: dict) -> tuple[str, list[str]]:
    """Apply a vendor's replace pairs, one at a time, keeping every amount.

    A strip pattern takes markup out. A replace pair rewrites it in place, for
    the cases where the vendor's own markup lays a receipt out for a mail
    client's width and not for a packet's. Each pair is a regex and a
    replacement, fed to ``re.sub`` the same way a strip pattern is, and guarded
    the same way: a pair whose result prints fewer money strings than the
    fragment did before is skipped and named, because a rewrite is no better a
    reason to lose a total than a removal is.

    Returns the fragment and the lines describing what was skipped, the same
    way ``_apply_strip_patterns`` does.
    """
    pairs = rules.get("replace") or []
    vendor = str(rules.get("name") or GENERIC)
    warnings: list[str] = []
    kept = _printed_amounts(body)
    for index, (pattern, replacement) in enumerate(pairs):
        candidate = re.sub(pattern, replacement, body, flags=re.S | re.I)
        found = _printed_amounts(candidate)
        if found < kept:
            warnings.append(
                f"clean: {vendor} replace pattern {index} skipped, "
                "it would have taken an amount with it"
            )
            continue
        body = candidate
        kept = found
    return body, warnings


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
    """Return the receipt fragment: vendor markup, scoped CSS, nothing live.

    The vendor's replace pairs run first, before anything that sanitises. A
    pair rewrites markup with text a rules.json author wrote, and text from a
    rules.json is text: putting it in ahead of the script removal, the element
    stripping and the attribute defusing means a replacement can no more leave
    something active in the fragment than the vendor's own markup can.
    """
    rules = rules or {}
    body = raw or ""

    body = COMMENT_RE.sub("", body)
    body, warnings = _apply_replacements(body, rules)
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

    body, stripped = _apply_strip_patterns(body, rules)
    for line in warnings + stripped:
        print(line, file=sys.stderr)

    body = _unwrap_links(body, [*GENERIC_UNWRAP, *(rules.get("unwrap_links_matching") or [])])
    body = _defuse_anchors(body)
    body = _defuse_tags(body)

    fragment = body.strip()
    scoped = _scope_css(sanitize_css(styles)).strip()
    if scoped:
        fragment = f"<style>{scoped}</style>\n{fragment}"
    if image_cache is not None:
        fragment = inline_images(fragment, Path(image_cache))
    return fragment


# ------------------------------------------------------------- whole directory


def _clean_one(
    source: Path, out_dir: Path, vendors_dir: Path, image_cache: Path | None, download: bool
) -> tuple[str, str]:
    """One receipt, cleaned and written out. Returns its name and its vendor.

    The vendor is read from the headers saved beside the message, exactly as
    the single file CLI reads them, so a directory run and a file run choose
    the same rules. It is a function of its own only because ``clean_dir``
    takes a ``fetch_images`` flag of the same name as the fetching function.
    """
    name, rules = detect_from_meta(source, vendors_dir)
    cleaned = clean_html(source.read_text(encoding="utf-8", errors="replace"), rules)
    if download:
        fetch_images(cleaned, image_cache)
    if image_cache is not None:
        cleaned = inline_images(cleaned, image_cache)
    (out_dir / source.name).write_text(cleaned + "\n", encoding="utf-8")
    return source.name, name


def clean_dir(
    src_dir: Path,
    out_dir: Path,
    vendors_dir: Path,
    image_cache: Path | None = None,
    fetch_images: bool = False,
) -> list[tuple[str, str]]:
    """Clean every receipt in a directory, in filename order, and carry the rest across.

    Each ``.html`` file is cleaned in turn, with the vendor for each read from
    its own ``.meta.json``. A ``.txt``, ``.pdf``, ``.png`` or ``.jpg`` beside
    them is copied through unchanged, because a plaintext body, a folio and a
    receipt photo are already what the packet wants. Anything else, the meta
    files included, stays behind.

    Returns ``(filename, vendor)`` for each file cleaned, in filename order,
    with ``"generic"`` for a receipt whose sender no rules claim.

    One receipt at a time, walking the sorted directory. Two things fall out of
    that: the amount guard warnings reach stderr in filename order with nothing
    to collect or re-sort, and the one ``failed-images.json`` a fetch shares
    across a whole directory is never written by two cleans at once.

    ``fetch_images`` is the one flag that opens a socket, it needs an
    ``image_cache`` to fill, and it downloads from the cleaned fragment, never
    from the raw message.
    """
    src_dir = Path(src_dir)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    if fetch_images and image_cache is None:
        raise ValueError("clean_dir: fetch_images needs an image_cache directory")
    cache = None if image_cache is None else Path(image_cache)

    listed: list[tuple[str, str]] = []
    for path in sorted(src_dir.iterdir()):
        if not path.is_file():
            continue
        suffix = path.suffix.lower()
        if suffix == CLEAN_SUFFIX:
            listed.append(_clean_one(path, out_dir, vendors_dir, cache, fetch_images))
        elif suffix in COPY_SUFFIXES:
            shutil.copy2(path, out_dir / path.name)
    return listed


# ------------------------------------------------------------------------ cli


def _run_dir(args: argparse.Namespace) -> int:
    """The ``--dir`` form: one directory in, one directory out.

    Every line it prints is in filename order, the vendor lines and the strip
    pattern warnings alike, because the receipts are cleaned in that order.
    """
    listed = clean_dir(
        args.dir,
        args.out,
        args.vendors,
        image_cache=args.images,
        fetch_images=args.fetch_images,
    )
    for name, vendor in listed:
        print(f"clean: {name} vendor {vendor}", file=sys.stderr)
    print(f"clean: wrote {len(listed)} receipts into {Path(args.out).as_posix()}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Clean a vendor receipt email for a packet.")
    parser.add_argument("source", type=Path, nargs="?", help="the saved receipt HTML")
    parser.add_argument("--dir", type=Path, help="clean every receipt in this directory at once")
    parser.add_argument(
        "--out", type=Path, required=True, help="where to write the fragment, or the directory"
    )
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

    if args.fetch_images and args.images is None:
        print("clean: --fetch-images needs --images DIR", file=sys.stderr)
        return 2
    if args.dir is not None:
        if args.source is not None:
            print("clean: give one receipt or --dir DIR, not both", file=sys.stderr)
            return 2
        if args.vendor:
            print(
                "clean: --vendor is for one receipt; --dir reads each saved meta file",
                file=sys.stderr,
            )
            return 2
        return _run_dir(args)
    if args.source is None:
        print("clean: give a receipt to clean, or --dir DIR", file=sys.stderr)
        return 2

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
