#!/usr/bin/env python3
"""Clean a vendor receipt email down to the part a packet can show.

What comes out is the vendor's own markup, with its own fonts, colours and
values intact, minus everything that phones home or sells something: scripts,
the document wrapper, meta and link tags, tracking pixels, the promo and tip
modules named by the vendor rules, and every live link.

Three choices are worth stating plainly, because whoever reads a packet should
know what was done to the page in front of them.

1. Links. A tracking link named by ``unwrap_links_matching`` is replaced by the
   text it wrapped. Every other anchor keeps its tag and its styling but loses
   ``href``, ``target`` and any handler attribute, so the wording and the
   vendor look survive and nothing in a packet is clickable.
2. Images. ``inline_images`` reads from a cache directory and never opens a
   socket. Only the CLI, and only with ``--fetch-images``, downloads anything.
   The substring test for tracking pixels (open, track, pixel, beacon) runs on
   http and https sources only, because a base64 data URI can contain those
   letters by chance and a logo is not a pixel.
3. CSS. Style blocks are kept, not dropped, so the receipt still looks like the
   receipt. Every rule is scoped to the ``.rc`` receipt container first, so one
   vendor's stylesheet cannot reach another vendor's receipt on the same page.

Stdlib only. Regex over the markup, deliberately: these are email tables, the
input is one saved message at a time, and a parser dependency buys nothing here.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import mimetypes
import re
import sys
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import urlopen

SCOPE = ".rc"
FETCH_TIMEOUT = 10

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
HEAD_RE = re.compile(r"<head\b[^>]*>.*?</head\s*>", re.S | re.I)
BODY_RE = re.compile(r"<body\b[^>]*>(.*)</body\s*>", re.S | re.I)
DOCTYPE_RE = re.compile(r"<!doctype[^>]*>", re.I)
WRAPPER_RE = re.compile(r"</?(?:html|body)\b[^>]*>", re.I)
META_LINK_RE = re.compile(r"<(?:meta|link)\b[^>]*>", re.I)
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


# ------------------------------------------------------------------------ css


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


def fetch_images(html: str, cache_dir: Path) -> int:
    """Download every remote image not already cached. CLI only. Returns the count."""
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
        try:
            with urlopen(url, timeout=FETCH_TIMEOUT) as response:  # noqa: S310
                blob = response.read()
        except Exception as error:
            print(f"clean: image not fetched ({type(error).__name__})", file=sys.stderr)
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
    """The domain part of a From header, however it is wrapped."""
    text = (from_addr or "").strip().rstrip(">")
    if "@" not in text:
        return ""
    return text.rsplit("@", 1)[1].strip().strip("<>\"' ").lower()


def _vendor_order(rules: dict[str, dict]) -> list[str]:
    """Vendors with sender domains first, catch alls last, then by name."""
    return sorted(rules, key=lambda name: (not (rules[name].get("sender_domains") or []), name))


def detect_vendor(from_addr: str, subject: str, rules: dict) -> str | None:
    """Name the vendor from the sender domain, else from the subject, else None."""
    domain = _sender_domain(from_addr)
    order = _vendor_order(rules)
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


# ---------------------------------------------------------------------- clean


def clean_html(raw: str, rules: dict | None = None, image_cache: Path | None = None) -> str:
    """Return the receipt fragment: vendor markup, scoped CSS, nothing live."""
    rules = rules or {}
    body = raw or ""

    body = COMMENT_RE.sub("", body)
    styles = "".join(STYLE_RE.findall(body))
    body = STYLE_RE.sub("", body)
    body = SCRIPT_RE.sub("", body)
    body = LONE_SCRIPT_RE.sub("", body)
    body = HEAD_RE.sub("", body)

    inside = BODY_RE.search(body)
    if inside:
        body = inside.group(1)
    body = DOCTYPE_RE.sub("", body)
    body = WRAPPER_RE.sub("", body)
    body = META_LINK_RE.sub("", body)
    body = IMG_RE.sub(lambda m: "" if _is_tracking_pixel(m.group(0)) else m.group(0), body)

    for pattern in rules.get("strip_regex") or []:
        body = re.sub(pattern, "", body, flags=re.S)

    unwrap = rules.get("unwrap_links_matching") or []
    if unwrap:
        body = _unwrap_links(body, unwrap)
    body = _defuse_anchors(body)

    fragment = body.strip()
    scoped = _scope_css(styles).strip()
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
    parser.add_argument("--vendor", help="vendor name, for vendor specific rules")
    parser.add_argument("--vendors", type=Path, default=Path("vendors"), help="rules directory")
    parser.add_argument("--images", type=Path, help="image cache directory")
    parser.add_argument(
        "--fetch-images", action="store_true", help="download missing images into the cache"
    )
    args = parser.parse_args(argv)

    raw = args.source.read_text(encoding="utf-8", errors="replace")

    rules = None
    if args.vendor:
        available = load_vendor_rules(args.vendors)
        if args.vendor not in available:
            print(f"clean: no rules for vendor {args.vendor}", file=sys.stderr)
            return 2
        rules = available[args.vendor]

    if args.fetch_images:
        if args.images is None:
            print("clean: --fetch-images needs --images DIR", file=sys.stderr)
            return 2
        fetch_images(raw, args.images)

    cleaned = clean_html(raw, rules, args.images)
    if args.out.parent != Path(""):
        args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(cleaned + "\n", encoding="utf-8")
    print(f"clean: wrote {args.out.as_posix()}, {len(cleaned)} characters")
    return 0


if __name__ == "__main__":
    sys.exit(main())
