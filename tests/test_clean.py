"""Tests for the receipt cleaner.

The vendor samples under ``vendors/`` are static, scrubbed copies of real
vendor mail, and ``tests/test_vendors.py`` is what proves each folder's rules
against them. This file tests the cleaner itself, and reaches for a sample only
where a real page of vendor markup is the point. Nothing here touches the
network: the tests that exercise downloading replace ``clean.urlopen`` and
``clean.socket.gethostbyname``, and two tests assert that the offline paths
would explode if they tried to open a socket.

The block headed "nothing active survives" is the probe set. Each test there
names one thing a vendor email can carry that could act inside a packet, and
asserts with the exact markup that it does not survive ``clean_html``.
"""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path
from urllib.error import HTTPError, URLError

import clean
import pytest

VENDORS_DIR = Path(__file__).resolve().parents[1] / "vendors"
# The folders this file reaches into. Every folder, sample or no sample, is
# covered by tests/test_vendors.py.
VENDOR_NAMES = ["doordash", "lyft", "plaintext", "uber", "united"]

# The eight bytes that open a PNG, and a body behind them. A fetch keeps what
# it downloads only when the answer starts like a picture, so the tests that
# download something hand it one of these rather than a line of prose.
PNG = b"\x89PNG\r\n\x1a\npretend pixels"


def sample_path(vendor: str) -> Path:
    suffix = "sample.txt" if vendor == "plaintext" else "sample.html"
    return VENDORS_DIR / vendor / suffix


@pytest.fixture(scope="module")
def rules() -> dict[str, dict]:
    return clean.load_vendor_rules(VENDORS_DIR)


@pytest.fixture(scope="module")
def samples() -> dict[str, str]:
    return {name: sample_path(name).read_text(encoding="utf-8") for name in VENDOR_NAMES}


def _explode(*args: object, **kwargs: object) -> None:
    """Stand in for urlopen where the code under test must never call it."""
    raise AssertionError("this code path must not open the network")


@pytest.fixture
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make any download attempt fail loudly rather than quietly succeed."""
    monkeypatch.setattr(clean, "urlopen", _explode)


# ------------------------------------------------------------- generic clean


def test_scripts_are_removed(no_network: None) -> None:
    raw = "<html><body><p>Fare</p><script>track()</script><script src='x.js'></body></html>"
    out = clean.clean_html(raw)
    assert "script" not in out.lower()
    assert "track()" not in out
    assert "<p>Fare</p>" in out


def test_wrapper_and_head_are_removed() -> None:
    raw = (
        "<!DOCTYPE html><html lang='en'><head><title>Receipt</title>"
        "<meta charset='utf-8'><link rel='stylesheet' href='v.css'></head>"
        "<body bgcolor='#fff'><p>Total</p></body></html>"
    )
    out = clean.clean_html(raw)
    assert out == "<p>Total</p>"


def test_body_without_wrapper_survives() -> None:
    assert clean.clean_html("<p>Just a fragment</p>") == "<p>Just a fragment</p>"


def test_empty_input_is_empty_output() -> None:
    assert clean.clean_html("") == ""
    assert clean.clean_html(None) == ""


def test_meta_and_link_outside_head_are_removed() -> None:
    raw = "<body><meta name='x' content='y'><link rel='next' href='z'><p>Keep</p></body>"
    assert clean.clean_html(raw) == "<p>Keep</p>"


def test_html_comments_are_removed() -> None:
    raw = "<body><!-- [if mso]> junk <![endif] --><p>Keep</p></body>"
    assert clean.clean_html(raw) == "<p>Keep</p>"


# -------------------------------------------------- nothing active survives

HANDLER_RE = re.compile(r"\bon\w+\s*=", re.I)

# Split by shape, not by danger: the void ones carry no content to remove.
PAIRED_ELEMENTS = [
    "iframe",
    "frame",
    "frameset",
    "object",
    "embed",
    "applet",
    "form",
    "button",
    "textarea",
    "select",
    "svg",
    "math",
    "video",
    "audio",
    "noscript",
    "template",
]
VOID_ELEMENTS = ["base", "input", "link", "meta", "source", "track"]

# One hostile fragment carrying every probe at once, so the fixture test below
# proves the guarantees on real vendor markup rather than on a toy string.
HOSTILE = (
    "<script>steal()</script>"
    '<img src="x" onerror="alert(1)">'
    '<body onload="alert(2)"><div onclick="alert(3)" onfocus="alert(4)">tap</div>'
    '<a href="javascript:alert(5)">go</a>'
    '<img srcset="https://tracker.example/a.png 1x" src="https://cdn.example.com/l.png">'
    '<iframe src="https://tracker.example/frame"></iframe>'
    '<form action="https://tracker.example/post"><input name="card"></form>'
    "<style>@import url(https://tracker.example/x.css);"
    "a{background:url(https://tracker.example/p.gif);width:expression(alert(6))}</style>"
)


@pytest.mark.parametrize(
    "tag",
    [
        '<body onload="alert(1)">x</body>',
        '<img src="https://cdn.example.com/l.png" onerror="alert(1)">',
        '<div onclick="alert(1)">tap</div>',
        '<span onfocus="alert(1)">focus</span>',
        "<td onmouseover=alert(1)>cell</td>",
        "<p ONLOAD='alert(1)'>shout</p>",
        '<table onblur="alert(1)"><tr><td>Fare</td></tr></table>',
    ],
)
def test_no_inline_handler_survives_on_any_tag(tag: str) -> None:
    out = clean.clean_html(tag)
    assert not HANDLER_RE.search(out), out


# A tag whose own attribute value holds a ">" is one tag, the way a browser
# reads it. A pattern that stopped at the first bracket stopped inside the
# quotes, left the rest of the attributes unread, and handed the packet a
# handler nobody had looked at. Every probe below carries a quoted bracket.


@pytest.mark.parametrize(
    "tag",
    [
        '<a title="1 > 2" onclick="evil()">x</a>',
        '<img alt="a>b" onerror="x">',
        "<div data-note='a > b' onmouseover=alert(1)>tap</div>",
        '<td title="<b>1 > 2</b>" onfocus="go()">Fare</td>',
    ],
)
def test_a_bracket_inside_a_quoted_value_does_not_end_the_tag(tag: str) -> None:
    out = clean.clean_html(tag)
    assert not HANDLER_RE.search(out), out
    assert "evil(" not in out
    assert "alert(1)" not in out


def test_a_live_scheme_is_refused_past_a_quoted_bracket() -> None:
    raw = '<a href="javascript:alert(1)" title="x>y">go</a>'
    out = clean.clean_html(raw)
    assert "javascript:" not in out
    assert 'title="x>y"' in out
    assert ">go</a>" in out


def test_srcset_goes_from_a_tag_that_carries_a_quoted_bracket() -> None:
    raw = (
        '<img alt="1 > 2" srcset="https://tracker.example/a.png 1x" '
        'src="https://cdn.example.com/l.png">'
    )
    out = clean.clean_html(raw)
    assert "srcset" not in out
    assert "tracker.example" not in out
    assert "cdn.example.com/l.png" in out


def test_a_tracking_pixel_with_a_bracket_in_its_alt_is_still_a_pixel() -> None:
    raw = '<p>Fare<img src="https://t.example/open?id=9" alt="a>b" width="1" height="1"></p>'
    assert clean.clean_html(raw) == "<p>Fare</p>"


@pytest.mark.parametrize("attribute", ["href", "src", "action", "data", "poster", "background"])
@pytest.mark.parametrize("value", ["javascript:alert(1)", "data:text/html,<script>x</script>"])
def test_no_live_scheme_survives_in_a_url_attribute(attribute: str, value: str) -> None:
    out = clean.clean_html(f'<div {attribute}="{value}">Fare</div>')
    assert "javascript:" not in out
    assert "data:text/html" not in out
    assert ">Fare</div>" in out


def test_formaction_is_treated_as_a_url_attribute() -> None:
    # button is removed outright, so formaction is probed on a tag that stays.
    out = clean.clean_html('<div formaction="javascript:alert(1)">Fare</div>')
    assert "javascript:" not in out


@pytest.mark.parametrize(
    "value",
    [
        "JaVaScRiPt:alert(1)",
        "  javascript:alert(1)",
        "java\tscript:alert(1)",
        "java\nscript:alert(1)",
        "&#106;avascript:alert(1)",
        "DATA:TEXT/HTML;base64,PHNjcmlwdD4=",
    ],
)
def test_a_live_scheme_written_awkwardly_is_still_refused(value: str) -> None:
    out = clean.clean_html(f'<div href="{value}">Fare</div>')
    assert "alert(1)" not in out
    assert "PHNjcmlwdD4" not in out
    assert ">Fare</div>" in out


def test_srcset_is_removed_wherever_it_appears() -> None:
    raw = (
        '<img src="https://cdn.example.com/l.png" srcset="https://tracker.example/a.png 2x">'
        "<source srcset='https://tracker.example/b.png'>"
    )
    out = clean.clean_html(raw)
    assert "srcset" not in out
    assert "tracker.example" not in out
    assert "https://cdn.example.com/l.png" in out


@pytest.mark.parametrize("element", PAIRED_ELEMENTS)
def test_a_dangerous_element_goes_with_its_contents(element: str) -> None:
    raw = f'<p>Before</p><{element} id="x">SECRET PAYLOAD</{element}><p>After</p>'
    out = clean.clean_html(raw)
    assert f"<{element}" not in out.lower()
    assert f"</{element}" not in out.lower()
    assert "SECRET PAYLOAD" not in out
    assert "<p>Before</p>" in out
    assert "<p>After</p>" in out


@pytest.mark.parametrize("element", VOID_ELEMENTS)
def test_a_void_dangerous_element_goes_on_its_own(element: str) -> None:
    out = clean.clean_html(f'<p>Fare</p><{element} name="x">')
    assert f"<{element}" not in out.lower()
    assert out == "<p>Fare</p>"


def test_nested_dangerous_elements_leave_nothing_behind() -> None:
    raw = "<p>Keep</p><form><form>INNER PAYLOAD</form>OUTER PAYLOAD</form>"
    out = clean.clean_html(raw)
    assert "form" not in out.lower()
    assert "PAYLOAD" not in out
    assert "<p>Keep</p>" in out


def test_a_self_closed_dangerous_element_takes_nothing_with_it() -> None:
    out = clean.clean_html('<p>Before</p><embed src="x.swf" /><p>After</p>')
    assert "embed" not in out.lower()
    assert out == "<p>Before</p><p>After</p>"


def test_an_unclosed_dangerous_element_takes_the_rest_of_the_fragment() -> None:
    """A browser reads everything after an unclosed iframe as its content."""
    out = clean.clean_html('<p>Keep</p><iframe src="https://tracker.example/f"><p>Gone</p>')
    assert out == "<p>Keep</p>"


def test_an_unquoted_attribute_value_is_read_the_same_way() -> None:
    assert "javascript:" not in clean.clean_html("<div href=javascript:alert(1)>Fare</div>")
    out = clean.clean_html("<div style=color:#123456>Fare</div>")
    assert "color:#123456" in out


def test_a_url_with_no_host_is_never_fetched(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(clean, "urlopen", _explode)
    assert clean._host_is_public("") is False
    assert clean.fetch_images('<img src="https:///bare/path.png">', tmp_path) == 0


def test_an_svg_carrying_a_script_is_removed_whole() -> None:
    raw = '<p>Fare</p><svg><script>alert(1)</script><circle r="9"/></svg>'
    out = clean.clean_html(raw)
    assert "svg" not in out.lower()
    assert "alert(1)" not in out
    assert "circle" not in out


# ------------------------------------------------------------- css sanitising


@pytest.mark.parametrize(
    ("css", "gone"),
    [
        ("@import url(https://tracker.example/x.css);", "tracker.example"),
        ('@import "https://tracker.example/x.css";', "tracker.example"),
        (".a{width:expression(alert(1))}", "expression"),
        (".a{-moz-binding:url(https://tracker.example/x.xml)}", "-moz-binding"),
        (".a{behavior:url(#default#time2)}", "behavior"),
    ],
)
def test_a_style_block_loses_what_can_fetch_or_run(css: str, gone: str) -> None:
    out = clean.clean_html(f"<style>{css}</style><p>Fare</p>")
    assert gone not in out
    assert "<p>Fare</p>" in out


def test_a_remote_url_in_a_style_block_becomes_none() -> None:
    css = ".a{background:url(https://tracker.example/p.gif)}.b{background:url('/local.png')}"
    out = clean.clean_html(f"<style>{css}</style><p>Fare</p>")
    assert ".rc .a{background:none}" in out
    assert ".rc .b{background:none}" in out


@pytest.mark.parametrize(
    "url",
    [
        "data:text/html,<script>steal</script>",
        "data:text/css,@import 'https://tracker.example/x.css';",
        "data:application/javascript,steal",
    ],
)
def test_a_data_uri_that_is_not_a_picture_or_a_font_becomes_none(url: str) -> None:
    """A data URI is only kept where it is what the declaration is asking for."""
    out = clean.clean_html(f"<style>.a{{background:url({url})}}</style>")
    assert ".rc .a{background:none}" in out
    assert "script" not in out.lower()


def test_an_inline_font_is_kept_the_way_an_inline_picture_is() -> None:
    css = "@font-face{font-family:V;src:url(data:font/woff2;base64,d09GMg)}"
    out = clean.clean_html(f"<style>{css}</style><p>x</p>")
    assert "url(data:font/woff2;base64,d09GMg)" in out


def test_a_data_uri_in_a_style_block_is_kept() -> None:
    css = ".a{background:url(data:image/gif;base64,R0lGODlh)}"
    out = clean.clean_html(f"<style>{css}</style><p>Fare</p>")
    assert ".rc .a{background:url(data:image/gif;base64,R0lGODlh)}" in out


@pytest.mark.parametrize(
    ("style", "gone"),
    [
        ("background:url(https://tracker.example/p.gif)", "tracker.example"),
        ("width:expression(alert(1))", "expression"),
        ("-moz-binding:url(https://tracker.example/x.xml)", "-moz-binding"),
        ("behavior:url(#default#time2)", "behavior"),
        ("@import url(https://tracker.example/x.css)", "tracker.example"),
    ],
)
def test_a_style_attribute_loses_what_can_fetch_or_run(style: str, gone: str) -> None:
    out = clean.clean_html(f'<div style="{style};color:#123456">Fare</div>')
    assert gone not in out
    assert "color:#123456" in out


def test_a_style_attribute_with_nothing_left_is_dropped() -> None:
    out = clean.clean_html('<div style="behavior:url(#default#time2)">Fare</div>')
    assert out == "<div>Fare</div>"


# --------------------------------------------------------------- image schemes


@pytest.mark.parametrize(
    "src",
    ["https://cdn.example.com/l.png", "http://cdn.example.com/l.png", "data:image/png;base64,AAA"],
)
def test_an_image_keeps_an_allowed_scheme(src: str) -> None:
    out = clean.clean_html(f'<img src="{src}" width="34" height="34">')
    assert src in out


@pytest.mark.parametrize(
    "src",
    [
        "data:text/html,<script>alert(1)</script>",
        "javascript:alert(1)",
        "file:///etc/passwd",
        "cid:part1.local",
        "//tracker.example/p.gif",
    ],
)
def test_an_image_loses_any_other_scheme(src: str) -> None:
    out = clean.clean_html(f'<img src="{src}" width="34" height="34" alt="Logo">')
    assert "src=" not in out
    assert 'alt="Logo"' in out


# ---------------------------------------------------------------- the probe


def test_the_hostile_probe_leaves_nothing_active() -> None:
    out = clean.clean_html(HOSTILE)
    assert not HANDLER_RE.search(out), out
    assert "<iframe" not in out.lower()
    assert "<script" not in out.lower()
    assert "javascript:" not in out
    assert "srcset" not in out
    assert "tracker.example" not in out
    assert "expression" not in out
    assert "<form" not in out.lower()
    assert "<input" not in out.lower()


def test_every_fixture_receipt_cleans_down_to_nothing_active(
    fixture_dir: Path, rules: dict
) -> None:
    """The whole fixture, each receipt spiked with the probe, through clean."""
    receipts = sorted((fixture_dir / "receipts").glob("*.html"))
    assert receipts
    for path in receipts:
        raw = path.read_text(encoding="utf-8")
        for vendor_rules in [None, *rules.values()]:
            out = clean.clean_html(raw + HOSTILE, vendor_rules)
            assert not HANDLER_RE.search(out), f"{path.name} kept a handler"
            assert "<iframe" not in out.lower(), f"{path.name} kept an iframe"
            assert "<script" not in out.lower(), f"{path.name} kept a script"
            assert "javascript:" not in out, f"{path.name} kept a javascript url"


# ---------------------------------------------------------------- css scoping


def test_style_blocks_are_kept_and_scoped() -> None:
    raw = (
        "<html><head><style>\n"
        "body{margin:0;background:#fff}\n"
        "html,body{padding:0}\n"
        ".foo{color:red}\n"
        ".foo, .bar td{font-size:14px}\n"
        "body.dark{color:#fff}\n"
        "</style></head><body><p class='foo'>Fare</p></body></html>"
    )
    out = clean.clean_html(raw)
    assert "<style>" in out
    assert ".rc{margin:0;background:#fff}" in out
    assert ".rc, .rc{padding:0}" in out
    assert ".rc .foo{color:red}" in out
    assert ".rc .foo, .rc .bar td{font-size:14px}" in out
    assert ".rc.dark{color:#fff}" in out
    assert "body{" not in out


def test_at_rules_keep_their_wrapper_and_scope_what_is_inside() -> None:
    css = (
        '@charset "utf-8";'
        "@media print{body{margin:0}.foo{display:none}}"
        "@font-face{font-family:Vendor;src:url(v.woff2)}"
        "/* a comment */"
        "@keyframes spin{from{opacity:0}to{opacity:1}}"
    )
    out = clean.clean_html(f"<style>{css}</style><p>x</p>")
    assert '@charset "utf-8";' in out
    assert "@media print{.rc{margin:0}.rc .foo{display:none}}" in out
    # The wrapper survives; the remote font URL inside it does not.
    assert "@font-face{font-family:Vendor;src:none}" in out
    assert "/* a comment */" in out
    assert "@keyframes spin{from{opacity:0}to{opacity:1}}" in out


def test_css_comments_and_unterminated_blocks_do_not_break_scoping() -> None:
    css = ".a{color:red/* } not a brace */}\n.b{color:blue"
    out = clean.clean_html(f"<style>{css}</style>")
    assert ".rc .a{color:red/* } not a brace */}" in out
    assert ".rc .b{color:blue}" in out


def test_a_comment_in_front_of_an_at_rule_leaves_it_an_at_rule() -> None:
    """The prelude carried the comment, so the keyword was never read.

    A stylesheet that writes ``/* Mobile */ @media ...`` on its own line put
    whitespace in front of the comment, which the scoping read as the start of
    a selector: the wrapper came out as ``.rc /* Mobile */ @media screen``,
    which is not a selector and is not an at-rule either, and the rules inside
    it were left unscoped.
    """
    css = "\n/* Mobile */ @media screen and (max-width:600px){.a{color:red}}"
    out = clean.clean_html(f"<style>{css}</style><p>x</p>")
    assert "@media screen and (max-width:600px){.rc .a{color:red}}" in out
    assert ".rc /*" not in out
    assert ".rc @media" not in out


def test_a_comment_in_front_of_a_selector_keeps_both() -> None:
    css = "\n/* the fare table */ .a{color:red}"
    out = clean.clean_html(f"<style>{css}</style><p>x</p>")
    assert "/* the fare table */" in out
    assert ".rc .a{color:red}" in out


def test_css_without_any_rule_is_passed_through() -> None:
    assert clean._scope_css("") == ""
    assert clean._scope_css("  \n ") == "  \n "
    assert clean._scope_css("{color:red}") == "{color:red}"


def test_empty_style_block_emits_no_style_tag() -> None:
    assert clean.clean_html("<style>   </style><p>x</p>") == "<p>x</p>"


# ------------------------------------------------------- unclosed style blocks


def test_a_style_block_that_is_never_closed_keeps_the_text_after_it() -> None:
    """A message truncated mid stylesheet must not swallow the receipt."""
    raw = "<body><style>.a{color:red}<p>Total $28.93</p></body>"
    out = clean.clean_html(raw)
    # The orphan CSS is scoped and re-emitted, like any other block, and the
    # only style tokens left in the fragment are that one closed block.
    assert out == "<style>.rc .a{color:red}</style>\n<p>Total $28.93</p>"
    assert out.lower().count("<style") == 1
    assert out.lower().count("</style") == 1


def test_a_stray_closing_style_tag_is_removed() -> None:
    out = clean.clean_html("<p>Fare</p></style><p>Total</p>")
    assert "style" not in out.lower()
    assert out == "<p>Fare</p><p>Total</p>"


def test_a_style_tag_cut_off_before_its_bracket_is_removed() -> None:
    out = clean.clean_html("<p>Fare</p><style type=")
    assert "<style" not in out.lower()
    assert out == "<p>Fare</p>"


# ------------------------------------------------------------- page breaks


@pytest.mark.parametrize(
    "declaration",
    [
        "page-break-before:always",
        "page-break-after:always",
        "page-break-inside:avoid",
        "break-before:page",
        "break-after:column",
        "break-inside:avoid",
    ],
)
def test_a_style_block_loses_every_page_break(declaration: str) -> None:
    out = clean.clean_html(f"<style>.a{{{declaration};color:red}}</style><p>Fare</p>")
    assert "break" not in out
    assert ".rc .a{color:red}" in out


@pytest.mark.parametrize(
    "declaration",
    ["page-break-after:always", "break-inside:avoid"],
)
def test_a_style_attribute_loses_every_page_break(declaration: str) -> None:
    out = clean.clean_html(f'<div style="{declaration};color:#123456">Fare</div>')
    assert "break" not in out
    assert 'style="color:#123456"' in out


def test_a_style_attribute_that_was_only_a_page_break_is_dropped() -> None:
    assert clean.clean_html('<tr style="page-break-inside:avoid"><td>Fare</td></tr>') == (
        "<tr><td>Fare</td></tr>"
    )


def test_word_break_is_not_a_page_break() -> None:
    out = clean.clean_html('<div style="word-break:break-all;color:#123456">Fare</div>')
    assert "word-break:break-all" in out


# -------------------------------------------------------------- tracking pixels


@pytest.mark.parametrize(
    "tag",
    [
        '<img src="https://example.com/a.gif" width="1" height="1">',
        '<img src="https://example.com/a.gif" height="1">',
        '<img src="https://example.com/o/open?id=9" width="600">',
        '<img src="https://example.com/t/track/9" width="600">',
        '<img src="https://example.com/pixel.gif" width="600">',
        '<img src="https://example.com/beacon" width="600">',
        '<img src="https://example.com/a.gif" style="width:1px;height:1px">',
    ],
)
def test_tracking_pixels_are_removed(tag: str) -> None:
    assert clean.clean_html(f"<body>{tag}<p>Keep</p></body>") == "<p>Keep</p>"


def test_real_images_survive() -> None:
    tag = '<img src="https://cdn.example.com/logo.png" width="34" height="34" alt="Vendor">'
    assert tag in clean.clean_html(f"<body>{tag}</body>")


def test_a_data_uri_logo_is_never_read_as_a_pixel() -> None:
    # The letters of the pixel hints can turn up inside base64 by chance, so the
    # hint test must apply to http sources only.
    tag = '<img src="data:image/png;base64,AAopenAAtrackAApixelAA" width="34" height="34">'
    assert tag in clean.clean_html(f"<body>{tag}</body>")


def test_attribute_lookup_ignores_lookalike_attributes() -> None:
    tag = '<img data-width="1" src="https://cdn.example.com/logo.png" width="90">'
    assert tag in clean.clean_html(tag)
    assert clean._attr(tag, "width") == "90"
    assert clean._attr(tag, "alt") is None


def test_attribute_lookup_handles_every_quoting_style() -> None:
    assert clean._attr("<img width='12'>", "width") == "12"
    assert clean._attr("<img width=12>", "width") == "12"


# ----------------------------------------------------------------- vendor strip


def test_lyft_strip_removes_the_promo_block(rules: dict, samples: dict) -> None:
    raw = samples["lyft"]
    assert "Rides = rewards" in raw
    out = clean.clean_html(raw, rules["lyft"])
    assert "Rides = rewards" not in out
    assert "Add tip" not in out
    assert "Tip driver" not in out
    # The fare table, both tender rows and the ride endpoints are untouched.
    assert "Lyft fare (5.30mi, 24m 15s)" in out
    assert "$31.20" in out
    assert "Lyft Cash" in out
    assert "Visa *4321" in out
    assert "Receipt #2264013875290446311" in out


def test_doordash_strip_removes_the_help_button_and_footer(rules: dict, samples: dict) -> None:
    raw = samples["doordash"]
    assert "Get Order Help" in raw
    out = clean.clean_html(raw, rules["doordash"])
    assert "Get Order Help" not in out
    assert "Privacy Policy" not in out
    assert "Help Center" not in out
    assert "Subtotal" in out
    assert "Dasher" in out
    assert "Northgate Market" in out
    assert "Final total charged" in out


@pytest.mark.parametrize("vendor", ["lyft", "doordash"])
def test_at_least_one_strip_pattern_matches_the_sample(
    vendor: str, rules: dict, samples: dict
) -> None:
    patterns = rules[vendor]["strip_regex"]
    assert any(re.search(p, samples[vendor], re.S) for p in patterns)


@pytest.mark.parametrize("vendor", VENDOR_NAMES)
def test_every_strip_pattern_compiles(vendor: str, rules: dict) -> None:
    for pattern in rules[vendor]["strip_regex"]:
        re.compile(pattern, re.S)


def test_strip_patterns_run_case_insensitively(rules: dict) -> None:
    """Vendors shout their markup sometimes, and the pattern is written lower case.

    The Lyft rules name the tip line in the case Lyft normally sends it. This
    is the same line in capitals, which has to go the same way.
    """
    raw = "<TABLE><TR><TD>100% OF TIPS GO TO DRIVERS.</TD></TR><TR><TD>Fare</TD></TR></TABLE>"
    out = clean.clean_html(raw, rules["lyft"])
    assert "TIPS GO TO DRIVERS" not in out
    assert "Fare" in out


def test_a_strip_pattern_that_would_take_an_amount_is_skipped(
    rules: dict, capsys: pytest.CaptureFixture
) -> None:
    """One table holding the total and the Add tip button keeps both."""
    raw = '<table><tr><td>Total $28.93</td><td><a href="#">Add tip</a></td></tr></table>'
    out = clean.clean_html(raw, rules["lyft"])
    assert "$28.93" in out
    assert "Add tip" in out
    assert "lyft strip pattern 0 skipped" in capsys.readouterr().err


def test_the_amount_guard_names_a_vendor_that_has_no_name(
    capsys: pytest.CaptureFixture,
) -> None:
    unnamed = {"strip_regex": ["<p>.*?</p>"]}
    assert clean.clean_html("<p>Total 28.93</p>", unnamed) == "<p>Total 28.93</p>"
    assert "generic strip pattern 0 skipped" in capsys.readouterr().err


def test_a_strip_pattern_that_takes_no_amount_still_runs(
    rules: dict, capsys: pytest.CaptureFixture
) -> None:
    raw = '<table><tr><td><a href="#">Add tip</a></td></tr></table><p>Total $28.93</p>'
    out = clean.clean_html(raw, rules["lyft"])
    assert "Add tip" not in out
    assert "$28.93" in out
    assert capsys.readouterr().err == ""


def test_a_comma_decimal_is_an_amount_the_guard_protects(
    capsys: pytest.CaptureFixture,
) -> None:
    """A euro receipt writes its total as 88,60 and the guard has to see it."""
    rules = {"name": "demo", "strip_regex": [r"<p class=\"promo\">.*?</p>"]}
    raw = '<p class="promo">Total 88,60 EUR</p><p>Fare 74,25 EUR</p>'
    out = clean.clean_html(raw, rules)
    assert "88,60" in out
    assert "clean: demo strip pattern 0 skipped" in capsys.readouterr().err


def test_the_decimals_in_a_style_attribute_are_not_amounts(
    rules: dict, capsys: pytest.CaptureFixture
) -> None:
    """A font size is not money, and a pattern is not skipped over one."""
    raw = (
        '<table style="line-height:1.25rem"><tr><td style="letter-spacing:0.15px">'
        '<a href="#">Add tip</a></td></tr></table><p>Total $28.93</p>'
    )
    out = clean.clean_html(raw, rules["lyft"])
    assert "Add tip" not in out
    assert "$28.93" in out
    assert capsys.readouterr().err == ""


# -------------------------------------------------------------- vendor replace


def test_a_replace_pair_rewrites_the_markup_in_place() -> None:
    """The field a vendor uses when the markup has to be reshaped, not removed."""
    rules = {"name": "demo", "replace": [[r'(class="title" style=")width:100%', r"\1width:auto"]]}
    raw = '<table><tr><td class="title" style="width:100%;font-size:32px">Total</td>'
    raw += '<td class="amount">$34.86</td></tr></table>'
    out = clean.clean_html(raw, rules)
    assert 'style="width:auto;font-size:32px"' in out
    assert "width:100%" not in out
    assert "$34.86" in out


def test_a_replace_pair_runs_case_insensitively_and_across_lines() -> None:
    rules = {"name": "demo", "replace": [[r"<td>total\n</td>", "<td>Total</td>"]]}
    assert clean.clean_html("<td>TOTAL\n</td>", rules) == "<td>Total</td>"


def test_a_replace_that_would_drop_an_amount_is_skipped(
    capsys: pytest.CaptureFixture,
) -> None:
    """A rewrite is no better a reason to lose a total than a removal is."""
    rules = {"name": "demo", "replace": [[r"<td>\$[0-9.]+</td>", "<td></td>"]]}
    raw = "<tr><td>Total</td><td>$34.86</td></tr>"
    out = clean.clean_html(raw, rules)
    assert "$34.86" in out
    assert "clean: demo replace pattern 0 skipped" in capsys.readouterr().err


def test_a_replacement_cannot_put_anything_active_into_the_fragment() -> None:
    """The pairs run before every pass that sanitises, so this is defused.

    A rules.json is a file in this repo and its replacements are text a person
    wrote, but text is text: running the pairs first means a replacement is
    cleaned exactly as hard as the vendor markup around it.
    """
    rules = {
        "name": "demo",
        "replace": [["PLACEHOLDER", "<script>alert(1)</script><iframe src=http://e></iframe>"]],
    }
    out = clean.clean_html("<p>PLACEHOLDER Total $34.86</p>", rules)
    assert "<script" not in out.lower()
    assert "alert(1)" not in out
    assert "<iframe" not in out.lower()
    assert "http://e" not in out
    assert "$34.86" in out


def test_a_replacement_that_opens_a_style_block_is_scoped_like_any_other(
    no_network: None,
) -> None:
    """Whatever a pair writes goes through the stylesheet handling too."""
    rules = {"name": "demo", "replace": [["PLACEHOLDER", "<style>a{color:red}</style>"]]}
    out = clean.clean_html("<p>PLACEHOLDER</p>", rules)
    assert "<style>.rc a{color:red}</style>" in out


def test_a_replace_pair_that_keeps_every_amount_prints_nothing(
    capsys: pytest.CaptureFixture,
) -> None:
    rules = {"name": "demo", "replace": [[r"width:100%", "width:auto"]]}
    out = clean.clean_html('<td style="width:100%">$34.86</td>', rules)
    assert "width:auto" in out
    assert capsys.readouterr().err == ""


def test_the_replace_guard_names_a_vendor_that_has_no_name(
    capsys: pytest.CaptureFixture,
) -> None:
    unnamed = {"replace": [[r"<p>.*?</p>", ""]]}
    assert clean.clean_html("<p>Total $28.93</p>", unnamed) == "<p>Total $28.93</p>"
    assert "generic replace pattern 0 skipped" in capsys.readouterr().err


def test_rules_with_no_replace_field_clean_exactly_as_before(rules: dict) -> None:
    """The field is optional, so every vendor that does not carry one is unaffected."""
    assert "replace" not in rules["lyft"]
    raw = "<table><tr><td>Lyft fare</td><td>$18.40</td></tr></table>"
    assert clean.clean_html(raw, rules["lyft"]) == clean.clean_html(raw, {"name": "lyft"})


# ----------------------------------------------------------------------- links


def test_tracking_anchors_are_unwrapped_to_plain_text(rules: dict) -> None:
    raw = (
        '<p><a href="https://click.example.com/x">See passes</a> and '
        '<a href="https://example.com/track/9">Open the app</a> and '
        '<a href="https://example.com/help?utm_source=email">Get help</a> and '
        '<a href="https://example.com/policy">Policy</a></p>'
    )
    out = clean.clean_html(raw, rules["lyft"])
    assert "See passes and" in out
    assert "Open the app" in out
    assert "Get help" in out
    assert out.count("<a") == 1
    assert ">Policy</a>" in out


def test_remaining_anchors_keep_styling_but_carry_no_href() -> None:
    raw = (
        '<a href="https://example.com/x" target="_blank" rel="noopener" '
        'onclick="go()" style="color:#ff00bf" class="cta">Policy</a>'
    )
    out = clean.clean_html(raw)
    assert "href" not in out
    assert "target" not in out
    assert "onclick" not in out
    assert 'style="color:#ff00bf"' in out
    assert 'class="cta"' in out
    assert ">Policy</a>" in out


def test_an_anchor_with_no_attributes_survives() -> None:
    assert clean.clean_html("<a>Plain</a>") == "<a>Plain</a>"


@pytest.mark.parametrize(
    "href",
    [
        "https://click.example.com/x",
        "https://example.com/track/9",
        "https://example.com/help?utm_source=email",
        "https://email.example.com/open",
    ],
)
def test_the_generic_tracking_shapes_are_unwrapped_for_every_vendor(href: str) -> None:
    """The four shapes live in clean.py now, so a vendor that lists none gets them."""
    for rules in ({}, {"unwrap_links_matching": []}, {"name": "demo"}):
        assert clean.clean_html(f'<a href="{href}">Text</a>', rules) == "Text"


def test_a_link_that_matches_no_tracking_shape_keeps_its_tag() -> None:
    assert clean.clean_html('<a href="https://example.com/policy">Policy</a>') == "<a>Policy</a>"


# ---------------------------------------------------------------------- images


def test_inline_images_replaces_only_what_is_cached(tmp_path: Path, no_network: None) -> None:
    cached = "https://cdn.example.com/logo.png"
    missing = "https://cdn.example.com/other.png"
    empty = "https://cdn.example.com/empty.jpg"
    (tmp_path / clean.cache_name(cached)).write_bytes(b"\x89PNG-ish")
    (tmp_path / clean.cache_name(empty)).write_bytes(b"")
    html = f'<img src="{cached}"><img src="{missing}"><img src=\'{empty}\'>'
    out = clean.inline_images(html, tmp_path)
    assert 'src="data:image/png;base64,' in out
    assert f'src="{missing}"' in out
    assert f"src='{empty}'" in out


def test_inline_images_guesses_the_mime_type_past_a_query_string(
    tmp_path: Path, no_network: None
) -> None:
    url = "https://cdn.example.com/logo.gif?v=3&s=2x"
    (tmp_path / clean.cache_name(url)).write_bytes(b"GIF89a")
    out = clean.inline_images(f'<img src="{url}">', tmp_path)
    assert "data:image/gif;base64,R0lGODlh" in out


def test_inline_images_leaves_data_uris_alone(tmp_path: Path, no_network: None) -> None:
    html = '<img src="data:image/png;base64,AAAA">'
    assert clean.inline_images(html, tmp_path) == html


def test_clean_html_inlines_when_a_cache_is_given(tmp_path: Path, no_network: None) -> None:
    url = "https://cdn.example.com/logo.png"
    (tmp_path / clean.cache_name(url)).write_bytes(b"bytes")
    out = clean.clean_html(f'<body><img src="{url}" width="34"></body>', None, tmp_path)
    assert "data:image/png;base64,Ynl0ZXM=" in out


def test_cache_name_is_the_sha256_prefix() -> None:
    name = clean.cache_name("https://cdn.example.com/logo.png")
    assert len(name) == 16
    assert re.fullmatch(r"[0-9a-f]{16}", name)


class _FakeResponse:
    def __init__(self, blob: bytes) -> None:
        self._blob = blob

    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(self, *exc: object) -> bool:
        return False

    def read(self, amount: int | None = None) -> bytes:
        return self._blob if amount is None else self._blob[:amount]


@pytest.fixture
def public_dns(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every host resolves to one routable address, so only the fetch is under test."""
    monkeypatch.setattr(clean.socket, "gethostbyname", lambda host: "93.184.216.34")


def test_fetch_images_downloads_into_the_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, public_dns: None
) -> None:
    wanted = "https://cdn.example.com/logo.png"
    already = "https://cdn.example.com/cached.png"
    blank = "https://cdn.example.com/blank.png"
    broken = "https://cdn.example.com/broken.png"
    cache = tmp_path / "images"
    cache.mkdir()
    (cache / clean.cache_name(already)).write_bytes(b"old")

    calls: list[str] = []

    def fake_urlopen(url: str, timeout: int = 0) -> _FakeResponse:
        calls.append(url)
        assert timeout == clean.FETCH_TIMEOUT
        if url == broken:
            raise OSError("no route")
        return _FakeResponse(b"" if url == blank else PNG)

    monkeypatch.setattr(clean, "urlopen", fake_urlopen)
    html = "".join(f'<img src="{url}">' for url in (wanted, wanted, already, blank, broken))
    assert clean.fetch_images(html, cache) == 1
    assert calls == [wanted, blank, broken]
    assert (cache / clean.cache_name(wanted)).read_bytes() == PNG
    assert (cache / clean.cache_name(already)).read_bytes() == b"old"
    assert not (cache / clean.cache_name(blank)).exists()


# ------------------------------------------------------- images that never came


def _answer_with(monkeypatch: pytest.MonkeyPatch, answer: object) -> None:
    """Make every fetch return these bytes, or raise this error."""

    def fake_urlopen(url: str, timeout: int = 0) -> _FakeResponse:
        if isinstance(answer, Exception):
            raise answer
        return _FakeResponse(answer)

    monkeypatch.setattr(clean, "urlopen", fake_urlopen)


@pytest.mark.parametrize(
    "answer",
    [
        HTTPError("https://cdn.example.com/logo.png", 403, "Forbidden", {}, None),
        HTTPError("https://cdn.example.com/logo.png", 404, "Not Found", {}, None),
        b'<?xml version="1.0"?><Error><Code>AccessDenied</Code></Error>',
        b"",
    ],
)
def test_an_answer_that_is_not_an_image_is_recorded_as_failed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, public_dns: None, answer: object
) -> None:
    """A status that is not 200, and a 200 carrying anything but a picture."""
    url = "https://cdn.example.com/logo.png"
    _answer_with(monkeypatch, answer)
    assert clean.fetch_images(f'<img src="{url}">', tmp_path) == 0
    assert clean.failed_images(tmp_path) == {url}
    assert not (tmp_path / clean.cache_name(url)).exists()


def test_a_fetch_that_never_got_an_answer_records_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, public_dns: None
) -> None:
    """Offline is a fact about this machine, not about the vendor's image."""
    _answer_with(monkeypatch, URLError("no route to host"))
    assert clean.fetch_images('<img src="https://cdn.example.com/logo.png">', tmp_path) == 0
    assert clean.failed_images(tmp_path) == set()
    assert list(tmp_path.iterdir()) == []


def test_the_failure_record_merges_across_fetches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, public_dns: None
) -> None:
    """Each receipt is fetched on its own, into one cache they all share."""
    first = "https://cdn.example.com/one.png"
    second = "https://cdn.example.com/two.png"
    _answer_with(monkeypatch, HTTPError(first, 403, "Forbidden", {}, None))
    clean.fetch_images(f'<img src="{first}">', tmp_path)
    clean.fetch_images(f'<img src="{second}">', tmp_path)
    assert clean.failed_images(tmp_path) == {first, second}


def test_a_fetch_that_resolves_no_host_takes_nothing_out_of_the_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A rebuild on a machine with no DNS must not empty a committed record.

    This is the shape that costs a packet: someone rebuilds the example, the
    vendor hosts do not resolve here, and every URL the record already named
    is one this run refused before it ever asked. Nothing was learned about
    any of them, so nothing comes out, and the offline pass still knows to
    print their alt text.
    """
    kept = [
        "https://cache.marriott.com/marriottassets/map_icon.png",
        "https://images.noti.swiss.com/lh_edialog/success.png",
    ]
    (tmp_path / clean.FAILED_RECORD).write_text(json.dumps(kept), encoding="utf-8")

    def no_dns(host: str) -> str:
        raise OSError("name or service not known")

    monkeypatch.setattr(clean.socket, "gethostbyname", no_dns)
    monkeypatch.setattr(clean, "urlopen", _explode)
    html = "".join(f'<img src="{url}">' for url in [*kept, "https://cdn.example.com/new.png"])
    assert clean.fetch_images(html, tmp_path) == 0
    assert clean.failed_images(tmp_path) == set(kept)


def test_a_fetch_that_learns_one_new_failure_keeps_the_rest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, public_dns: None
) -> None:
    """The record grows by what this run learned and loses nothing else."""
    old = "https://cache.marriott.com/marriottassets/map_icon.png"
    new = "https://cdn.example.com/logo.png"
    (tmp_path / clean.FAILED_RECORD).write_text(json.dumps([old]), encoding="utf-8")
    _answer_with(monkeypatch, HTTPError(new, 403, "Forbidden", {}, None))
    assert clean.fetch_images(f'<img src="{new}">', tmp_path) == 0
    assert clean.failed_images(tmp_path) == {old, new}


def test_a_url_that_answers_later_leaves_the_failure_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, public_dns: None
) -> None:
    url = "https://cdn.example.com/logo.png"
    _answer_with(monkeypatch, HTTPError(url, 503, "Unavailable", {}, None))
    clean.fetch_images(f'<img src="{url}">', tmp_path)
    assert clean.failed_images(tmp_path) == {url}
    _answer_with(monkeypatch, PNG)
    assert clean.fetch_images(f'<img src="{url}">', tmp_path) == 1
    assert clean.failed_images(tmp_path) == set()
    assert not (tmp_path / clean.FAILED_RECORD).exists()


def test_a_record_that_cannot_be_read_names_no_failures(tmp_path: Path) -> None:
    (tmp_path / clean.FAILED_RECORD).write_text("{not json", encoding="utf-8")
    assert clean.failed_images(tmp_path) == set()


def test_a_failed_image_becomes_its_alt_text(tmp_path: Path, no_network: None) -> None:
    url = "https://cdn.example.com/logo.png"
    (tmp_path / clean.FAILED_RECORD).write_text(json.dumps([url]), encoding="utf-8")
    html = f'<td><a><img height="35" src="{url}" alt="lyft" border="0"></a></td>'
    out = clean.inline_images(html, tmp_path)
    assert out == '<td><a><span class="img-alt">lyft</span></a></td>'


def test_a_failed_image_with_no_alt_leaves_an_empty_span(tmp_path: Path, no_network: None) -> None:
    url = "https://cdn.example.com/spacer.png"
    (tmp_path / clean.FAILED_RECORD).write_text(json.dumps([url]), encoding="utf-8")
    assert clean.inline_images(f'<img src="{url}">', tmp_path) == '<span class="img-alt"></span>'


def test_alt_text_reaches_the_packet_as_text_and_not_as_markup(
    tmp_path: Path, no_network: None
) -> None:
    url = "https://cdn.example.com/logo.png"
    (tmp_path / clean.FAILED_RECORD).write_text(json.dumps([url]), encoding="utf-8")
    out = clean.inline_images(f'<img src="{url}" alt="Ride &amp; roll < Lyft">', tmp_path)
    assert out == '<span class="img-alt">Ride &amp; roll &lt; Lyft</span>'


def test_an_image_no_fetch_ever_tried_is_left_exactly_as_it_is(
    tmp_path: Path, no_network: None
) -> None:
    """The offline path decides nothing: no record, no opinion."""
    failed = "https://cdn.example.com/gone.png"
    untried = "https://cdn.example.com/other.png"
    (tmp_path / clean.FAILED_RECORD).write_text(json.dumps([failed]), encoding="utf-8")
    html = f'<img src="{untried}" alt="other">'
    assert clean.inline_images(html, tmp_path) == html


def test_clean_html_prints_the_alt_text_of_a_failed_image(tmp_path: Path, no_network: None) -> None:
    url = "https://cdn.example.com/logo.png"
    (tmp_path / clean.FAILED_RECORD).write_text(json.dumps([url]), encoding="utf-8")
    out = clean.clean_html(f'<body><img src="{url}" alt="Lyft"></body>', None, tmp_path)
    assert out == '<span class="img-alt">Lyft</span>'


@pytest.mark.parametrize(
    "address",
    ["127.0.0.1", "10.0.0.4", "192.168.1.9", "169.254.169.254", "0.0.0.0", "224.0.0.1"],
)
def test_fetch_images_refuses_a_host_inside_the_network(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture, address: str
) -> None:
    monkeypatch.setattr(clean.socket, "gethostbyname", lambda host: address)
    monkeypatch.setattr(clean, "urlopen", _explode)
    assert clean.fetch_images('<img src="https://metadata.example/a.png">', tmp_path) == 0
    assert "host refused" in capsys.readouterr().err
    assert list(tmp_path.iterdir()) == []


def test_fetch_images_refuses_a_host_that_does_not_resolve(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def no_such_host(host: str) -> str:
        raise OSError("name or service not known")

    monkeypatch.setattr(clean.socket, "gethostbyname", no_such_host)
    monkeypatch.setattr(clean, "urlopen", _explode)
    assert clean.fetch_images('<img src="https://nowhere.example/a.png">', tmp_path) == 0


def test_fetch_images_stops_at_the_size_cap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture, public_dns: None
) -> None:
    oversized = b"x" * (clean.MAX_IMAGE_BYTES + 1)
    monkeypatch.setattr(clean, "urlopen", lambda *a, **k: _FakeResponse(oversized))
    assert clean.fetch_images('<img src="https://cdn.example.com/huge.png">', tmp_path) == 0
    assert "over the size cap" in capsys.readouterr().err
    assert list(tmp_path.iterdir()) == []


def test_fetch_images_asks_for_one_byte_more_than_the_cap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, public_dns: None
) -> None:
    """A server that never stops sending is cut off by the read, not by trust."""
    asked: list[int | None] = []

    class _Endless:
        def __enter__(self) -> _Endless:
            return self

        def __exit__(self, *exc: object) -> bool:
            return False

        def read(self, amount: int | None = None) -> bytes:
            asked.append(amount)
            return b"y" * (amount or 0)

    monkeypatch.setattr(clean, "urlopen", lambda *a, **k: _Endless())
    assert clean.fetch_images('<img src="https://cdn.example.com/endless.png">', tmp_path) == 0
    assert asked == [clean.MAX_IMAGE_BYTES + 1]


def test_the_fetch_opener_refuses_to_follow_a_redirect() -> None:
    handler = clean._NoRedirect()
    with pytest.raises(URLError, match="refused a redirect"):
        handler.redirect_request(None, None, 302, "Found", {}, "https://elsewhere.example/a.png")


# ----------------------------------------------------------------------- rules


def test_load_vendor_rules_reads_every_vendor(rules: dict) -> None:
    assert set(VENDOR_NAMES) <= set(rules)
    for name, rule in rules.items():
        assert rule["name"] == name
        assert isinstance(rule["notes"], str) and rule["notes"]


def test_load_vendor_rules_keeps_the_rules_as_plain_data(rules: dict) -> None:
    for rule in rules.values():
        for key in clean.LIST_KEYS:
            assert all(isinstance(item, str) for item in rule[key])


def test_load_vendor_rules_rejects_a_missing_key(tmp_path: Path) -> None:
    body = json.loads((VENDORS_DIR / "lyft" / "rules.json").read_text(encoding="utf-8"))
    del body["display"]
    target = tmp_path / "lyft"
    target.mkdir()
    (target / "rules.json").write_text(json.dumps(body), encoding="utf-8")
    with pytest.raises(ValueError, match="missing key display"):
        clean.load_vendor_rules(tmp_path)


def test_load_vendor_rules_rejects_a_key_of_the_wrong_shape(tmp_path: Path) -> None:
    body = json.loads((VENDORS_DIR / "lyft" / "rules.json").read_text(encoding="utf-8"))
    body["sender_domains"] = "lyft.com"
    body["name"] = 7
    target = tmp_path / "lyft"
    target.mkdir()
    (target / "rules.json").write_text(json.dumps(body), encoding="utf-8")
    with pytest.raises(ValueError, match="sender_domains must be a list"):
        clean.load_vendor_rules(tmp_path)


def write_lyft_with(tmp_path: Path, **overrides: object) -> None:
    body = json.loads((VENDORS_DIR / "lyft" / "rules.json").read_text(encoding="utf-8"))
    body.update(overrides)
    target = tmp_path / "lyft"
    target.mkdir()
    (target / "rules.json").write_text(json.dumps(body), encoding="utf-8")


def test_load_vendor_rules_accepts_rules_without_a_replace_field(tmp_path: Path) -> None:
    """replace is optional, so the rules that predate it still load."""
    write_lyft_with(tmp_path)
    assert "replace" not in clean.load_vendor_rules(tmp_path)["lyft"]


def test_load_vendor_rules_accepts_a_well_formed_replace_field(tmp_path: Path) -> None:
    write_lyft_with(tmp_path, replace=[["width:100%", "width:auto"]])
    assert clean.load_vendor_rules(tmp_path)["lyft"]["replace"] == [["width:100%", "width:auto"]]


def test_load_vendor_rules_rejects_a_replace_field_that_is_not_a_list(tmp_path: Path) -> None:
    write_lyft_with(tmp_path, replace="width:100%")
    with pytest.raises(ValueError, match="replace must be a list"):
        clean.load_vendor_rules(tmp_path)


@pytest.mark.parametrize("bad", [["one"], [["a", "b", "c"]], ["not a pair at all"]])
def test_load_vendor_rules_rejects_a_replace_entry_that_is_not_a_pair(
    bad: list, tmp_path: Path
) -> None:
    write_lyft_with(tmp_path, replace=bad)
    with pytest.raises(ValueError, match="replace must hold pairs"):
        clean.load_vendor_rules(tmp_path)


def test_load_vendor_rules_rejects_a_replace_pair_that_is_not_two_strings(tmp_path: Path) -> None:
    write_lyft_with(tmp_path, replace=[["width:100%", 7]])
    with pytest.raises(ValueError, match="replace pairs must hold two strings"):
        clean.load_vendor_rules(tmp_path)


@pytest.mark.parametrize("key", ["strip_regex", "subject_patterns", "unwrap_links_matching"])
def test_load_vendor_rules_rejects_a_pattern_that_does_not_compile(
    key: str, tmp_path: Path
) -> None:
    """Compiled here, so the file and the index are named, not a byte offset."""
    write_lyft_with(tmp_path, **{key: ["fine", "(unclosed"]})
    with pytest.raises(ValueError, match=f"{key} 1 does not compile"):
        clean.load_vendor_rules(tmp_path)


def test_a_pattern_key_of_the_wrong_shape_is_reported_once(tmp_path: Path) -> None:
    """The shape complaint stands on its own; the compiling passes over it."""
    write_lyft_with(tmp_path, strip_regex="not a list", subject_patterns=[7])
    with pytest.raises(ValueError, match="strip_regex must be a list") as raised:
        clean.load_vendor_rules(tmp_path)
    assert "does not compile" not in str(raised.value)


def test_load_vendor_rules_rejects_a_replace_pattern_that_does_not_compile(tmp_path: Path) -> None:
    write_lyft_with(tmp_path, replace=[["(unclosed", "x"]])
    with pytest.raises(ValueError, match="replace 0 does not compile"):
        clean.load_vendor_rules(tmp_path)


def test_load_vendor_rules_rejects_a_replacement_naming_a_group_that_is_not_there(
    tmp_path: Path,
) -> None:
    """re.sub reads a template only where it matched, so it is read here instead."""
    write_lyft_with(tmp_path, replace=[[r"(width):100%", r"\2:auto"]])
    with pytest.raises(ValueError, match="replace 0 is not a usable replacement"):
        clean.load_vendor_rules(tmp_path)


def test_load_vendor_rules_accepts_a_replacement_that_names_its_groups(tmp_path: Path) -> None:
    """By number and by name, because the stand in carries both."""
    write_lyft_with(tmp_path, replace=[[r"(?P<w>width):100%", r"\g<w>:auto"], [r"(a)(b)", r"\2\1"]])
    assert len(clean.load_vendor_rules(tmp_path)["lyft"]["replace"]) == 2


def test_load_vendor_rules_rejects_a_file_that_is_not_an_object(tmp_path: Path) -> None:
    target = tmp_path / "odd"
    target.mkdir()
    (target / "rules.json").write_text("[1, 2]", encoding="utf-8")
    with pytest.raises(ValueError, match="must hold a JSON object"):
        clean.load_vendor_rules(tmp_path)


def test_load_vendor_rules_on_an_empty_directory(tmp_path: Path) -> None:
    assert clean.load_vendor_rules(tmp_path) == {}


# ------------------------------------------------------------- vendor detection


@pytest.mark.parametrize(
    ("from_addr", "expected"),
    [
        ("no-reply@lyftmail.com", "lyft"),
        ("Lyft <no-reply@LYFTMAIL.COM>", "lyft"),
        ("receipts@mail.lyft.com", "lyft"),
        ("no-reply@doordash.com", "doordash"),
        ("unitedairlines@united.com", "united"),
        ("noreply@uber.com", "uber"),
    ],
)
def test_detect_vendor_by_sender_domain(from_addr: str, expected: str, rules: dict) -> None:
    assert clean.detect_vendor(from_addr, "", rules) == expected


@pytest.mark.parametrize(
    ("subject", "expected"),
    [
        ("Your ride with Teodoro", "lyft"),
        ("Your Cedar Street Noodle Bar order", "doordash"),
        ("eTicket Itinerary and Receipt", "united"),
        ("United Wi-Fi receipt", "united"),
        ("Your Uber receipt", "uber"),
        ("Your Harborview Hotel stay is confirmed", "plaintext"),
        ("Reservation for your stay", "plaintext"),
    ],
)
def test_detect_vendor_by_subject(subject: str, expected: str, rules: dict) -> None:
    assert clean.detect_vendor("someone@example.org", subject, rules) == expected


@pytest.mark.parametrize(
    ("from_addr", "subject"),
    [
        ("someone@example.org", "Weekly update"),
        ("someone@notlyft.com", "Weekly update"),
        ("", ""),
        ("not-an-address", "Weekly update"),
        (None, None),
    ],
)
def test_detect_vendor_returns_none(from_addr: str, subject: str, rules: dict) -> None:
    assert clean.detect_vendor(from_addr, subject, rules) is None


@pytest.mark.parametrize(
    ("from_addr", "expected"),
    [
        ("Lyft <no-reply@lyft.com> (via relay)", "lyft"),
        ("receipts@uber.com, evil@x.com", "uber"),
        ('"Noodle Bar, Cedar Street" <no-reply@doordash.com>', "doordash"),
        ("<unitedairlines@united.com>", "united"),
    ],
)
def test_a_wrapped_or_doubled_from_header_still_names_the_sender(
    from_addr: str, expected: str, rules: dict
) -> None:
    """The vendor is whoever sent the message, not whoever the header lists next."""
    assert clean.detect_vendor(from_addr, "", rules) == expected


def test_the_sender_domain_of_a_header_with_no_address_is_empty() -> None:
    assert clean._sender_domain("no address here") == ""
    assert clean._sender_domain(None) == ""


def test_a_catch_all_never_beats_a_vendor_with_a_domain(rules: dict) -> None:
    # The subject matches plaintext too, but doordash is tried first.
    assert clean.detect_vendor("", "Your dinner order confirmation", rules) == "doordash"


def test_the_sender_domain_wins_over_the_subject(rules: dict) -> None:
    assert clean.detect_vendor("x@uber.com", "eTicket Itinerary and Receipt", rules) == "uber"


def test_sender_domains_may_be_written_with_an_at_sign(rules: dict) -> None:
    loose = {"loose": dict(rules["lyft"], name="loose", sender_domains=["@lyft.com", " "])}
    assert clean.detect_vendor("x@lyft.com", "", loose) == "loose"


# --------------------------------------------------------------- vendor samples


@pytest.mark.parametrize("vendor", VENDOR_NAMES)
def test_no_sample_cleans_down_to_a_live_link(vendor: str, rules: dict, samples: dict) -> None:
    """Nothing in a cleaned receipt is clickable.

    An image source stays, because a receipt without its logo is not the
    receipt the vendor sent, and a stylesheet may still carry an
    ``a[href^="tel"]`` selector. What must not survive is an href attribute on
    a tag, which is the only thing a reader can click.
    """
    out = clean.clean_html(samples[vendor], rules[vendor])
    assert "href=" not in out.lower()
    assert "<script" not in out.lower()


def test_a_generic_clean_keeps_the_values_but_drops_the_links(samples: dict) -> None:
    out = clean.clean_html(samples["lyft"])
    assert "$31.20" in out
    assert "Rides = rewards" in out  # no vendor rules, so the promo block stays
    assert "href=" not in out.lower()


# ------------------------------------------------------------------------- cli


def test_cli_cleans_the_lyft_sample(tmp_path: Path, no_network: None) -> None:
    source = tmp_path / "in.html"
    shutil.copyfile(sample_path("lyft"), source)
    out = tmp_path / "out" / "clean.html"
    code = clean.main(
        [
            str(source),
            "--out",
            str(out),
            "--vendor",
            "lyft",
            "--vendors",
            str(VENDORS_DIR),
        ]
    )
    assert code == 0
    written = out.read_text(encoding="utf-8")
    assert "$31.20" in written
    assert "Lyft Cash" in written
    assert "<script" not in written
    assert "Rides = rewards" not in written
    assert "href=" not in written.lower()


def test_cli_without_a_vendor_is_the_generic_clean(
    tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    source = tmp_path / "in.html"
    source.write_text("<html><body><p>Fare</p></body></html>", encoding="utf-8")
    out = tmp_path / "out.html"
    assert clean.main([str(source), "--out", str(out)]) == 0
    assert out.read_text(encoding="utf-8") == "<p>Fare</p>\n"
    assert "clean: vendor generic" in capsys.readouterr().err


def write_meta(source: Path, **fields: str) -> None:
    """The sidecar fetch.py writes beside a saved message."""
    source.with_suffix(".meta.json").write_text(json.dumps(fields), encoding="utf-8")


def test_cli_reads_the_vendor_from_the_saved_headers(
    tmp_path: Path, capsys: pytest.CaptureFixture, no_network: None
) -> None:
    source = tmp_path / "in.html"
    shutil.copyfile(sample_path("lyft"), source)
    write_meta(source, **{"from": "Lyft <no-reply@lyftmail.com>", "subject": "Your ride"})
    out = tmp_path / "out.html"

    assert clean.main([str(source), "--out", str(out), "--vendors", str(VENDORS_DIR)]) == 0
    assert "clean: vendor lyft" in capsys.readouterr().err
    written = out.read_text(encoding="utf-8")
    assert "$31.20" in written
    assert "Rides = rewards" not in written


def test_cli_falls_back_to_the_subject_then_to_generic(
    tmp_path: Path, capsys: pytest.CaptureFixture, no_network: None
) -> None:
    source = tmp_path / "in.html"
    shutil.copyfile(sample_path("lyft"), source)
    write_meta(source, **{"from": "someone@example.org", "subject": "Your ride with Teodoro"})
    assert (
        clean.main([str(source), "--out", str(tmp_path / "a.html"), "--vendors", str(VENDORS_DIR)])
        == 0
    )
    assert "clean: vendor lyft" in capsys.readouterr().err

    write_meta(source, **{"from": "someone@example.org", "subject": "Weekly update"})
    assert (
        clean.main([str(source), "--out", str(tmp_path / "b.html"), "--vendors", str(VENDORS_DIR)])
        == 0
    )
    assert "clean: vendor generic" in capsys.readouterr().err
    assert "Rides = rewards" in (tmp_path / "b.html").read_text(encoding="utf-8")


def test_cli_ignores_a_meta_file_it_cannot_read(
    tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    source = tmp_path / "in.html"
    source.write_text("<p>Fare</p>", encoding="utf-8")
    source.with_suffix(".meta.json").write_text("not json at all", encoding="utf-8")
    assert clean.main([str(source), "--out", str(tmp_path / "o.html")]) == 0
    assert "clean: vendor generic" in capsys.readouterr().err

    source.with_suffix(".meta.json").write_text("[1, 2]", encoding="utf-8")
    assert clean.main([str(source), "--out", str(tmp_path / "o2.html")]) == 0
    assert "clean: vendor generic" in capsys.readouterr().err


def test_cli_vendor_generic_forces_the_generic_clean(
    tmp_path: Path, capsys: pytest.CaptureFixture, no_network: None
) -> None:
    """The headers say Lyft. The flag says otherwise, and the flag wins."""
    source = tmp_path / "in.html"
    shutil.copyfile(sample_path("lyft"), source)
    write_meta(source, **{"from": "Lyft <no-reply@lyftmail.com>", "subject": "Your ride"})
    out = tmp_path / "out.html"

    code = clean.main(
        [str(source), "--out", str(out), "--vendor", "generic", "--vendors", str(VENDORS_DIR)]
    )
    assert code == 0
    assert "clean: vendor generic" in capsys.readouterr().err
    assert "Rides = rewards" in out.read_text(encoding="utf-8")


def test_cli_rejects_an_unknown_vendor(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    source = tmp_path / "in.html"
    source.write_text("<p>x</p>", encoding="utf-8")
    code = clean.main(
        [
            str(source),
            "--out",
            str(tmp_path / "o.html"),
            "--vendor",
            "hertz",
            "--vendors",
            str(VENDORS_DIR),
        ]
    )
    assert code == 2
    assert "no rules for vendor hertz" in capsys.readouterr().err


def test_cli_refuses_to_fetch_without_a_cache(
    tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    source = tmp_path / "in.html"
    source.write_text("<p>x</p>", encoding="utf-8")
    code = clean.main([str(source), "--out", str(tmp_path / "o.html"), "--fetch-images"])
    assert code == 2
    assert "needs --images" in capsys.readouterr().err


def test_cli_fetches_then_inlines(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, public_dns: None
) -> None:
    url = "https://cdn.example.com/logo.png"
    source = tmp_path / "in.html"
    source.write_text(f'<body><img src="{url}" width="34"></body>', encoding="utf-8")
    monkeypatch.setattr(clean, "urlopen", lambda *a, **k: _FakeResponse(PNG))
    out = tmp_path / "o.html"
    code = clean.main(
        [
            str(source),
            "--out",
            str(out),
            "--images",
            str(tmp_path / "images"),
            "--fetch-images",
        ]
    )
    assert code == 0
    written = out.read_text(encoding="utf-8")
    assert "data:image/png;base64,iVBORw0KGgpwcmV0ZW5kIHBpeGVscw==" in written


def test_cli_fetches_from_the_cleaned_fragment_not_the_raw_message(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, public_dns: None
) -> None:
    """The raw message still holds the pixels. Fetching them is the phone-home."""
    logo = "https://cdn.example.com/logo.png"
    pixel = "https://example.com/o/open?id=9"
    source = tmp_path / "in.html"
    source.write_text(
        f'<body><img src="{logo}" width="34"><img src="{pixel}" width="1" height="1"></body>',
        encoding="utf-8",
    )

    asked: list[str] = []

    def fake_urlopen(url: str, timeout: int = 0) -> _FakeResponse:
        asked.append(url)
        return _FakeResponse(PNG)

    monkeypatch.setattr(clean, "urlopen", fake_urlopen)
    out = tmp_path / "o.html"
    code = clean.main(
        [
            str(source),
            "--out",
            str(out),
            "--images",
            str(tmp_path / "images"),
            "--fetch-images",
        ]
    )
    assert code == 0
    assert asked == [logo]
    written = out.read_text(encoding="utf-8")
    assert "data:image/png;base64,iVBORw0KGgpwcmV0ZW5kIHBpeGVscw==" in written
    assert "open?id=9" not in written


def test_cli_writes_next_to_the_working_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    Path("in.html").write_text("<p>Fare</p>", encoding="utf-8")
    assert clean.main(["in.html", "--out", "out.html"]) == 0
    assert Path("out.html").read_text(encoding="utf-8") == "<p>Fare</p>\n"


# ------------------------------------------------------- the whole directory


def write_rules(vendors_dir: Path, name: str, domain: str, strip: list[str]) -> None:
    """One vendors/<name>/rules.json, complete enough for load_vendor_rules."""
    folder = vendors_dir / name
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "rules.json").write_text(
        json.dumps(
            {
                "name": name,
                "display": name.title(),
                "sender_domains": [domain],
                "subject_patterns": [],
                "strip_regex": strip,
                "unwrap_links_matching": [],
                "notes": "",
            }
        ),
        encoding="utf-8",
    )


def receipts_dir(root: Path, count: int) -> Path:
    """A directory of plain receipts named so sorted order is obvious."""
    source = root / "receipts"
    source.mkdir()
    for index in range(count):
        (source / f"r{index:02d}.html").write_text(
            "<html><body><p>Fare $12.50</p></body></html>", encoding="utf-8"
        )
    return source


def test_a_directory_is_cleaned_one_receipt_at_a_time_in_filename_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Twelve receipts, cleaned in the order they are listed and no other."""
    source = receipts_dir(tmp_path, 12)
    seen: list[str] = []
    inner = clean.clean_html

    def record(raw, rules=None, image_cache=None):
        seen.append(raw)
        return inner(raw, rules, image_cache)

    monkeypatch.setattr(clean, "clean_html", record)

    listed = clean.clean_dir(source, tmp_path / "clean", VENDORS_DIR)

    assert listed == [(f"r{index:02d}.html", "generic") for index in range(12)]
    assert len(seen) == 12


def test_the_listing_is_in_filename_order_with_the_vendor_of_each_file(
    tmp_path: Path, no_network: None
) -> None:
    source = tmp_path / "receipts"
    source.mkdir()
    for name, sender in (
        ("b.html", "Lyft <no-reply@lyftmail.com>"),
        ("a.html", "Uber <receipts@uber.com>"),
        ("c.html", "Nobody <billing@an-unlisted-vendor.example>"),
    ):
        (source / name).write_text("<p>Fare $12.50</p>", encoding="utf-8")
        write_meta(source / name, **{"from": sender, "subject": "Your receipt"})

    listed = clean.clean_dir(source, tmp_path / "clean", VENDORS_DIR)

    assert listed == [("a.html", "uber"), ("b.html", "lyft"), ("c.html", "generic")]
    assert (tmp_path / "clean" / "a.html").read_text(encoding="utf-8") == "<p>Fare $12.50</p>\n"


def test_text_pdf_and_image_receipts_are_carried_across_untouched(tmp_path: Path) -> None:
    source = tmp_path / "receipts"
    (source / "nested").mkdir(parents=True)
    (source / "r.html").write_text("<p>Fare</p>", encoding="utf-8")
    (source / "r.txt").write_text("Fare 12.50", encoding="utf-8")
    (source / "r.pdf").write_bytes(b"%PDF-1.4 folio")
    (source / "r.png").write_bytes(b"\x89PNG here")
    (source / "r.jpg").write_bytes(b"\xff\xd8 photo")
    write_meta(source / "r.html", **{"from": "x@y.example", "subject": "s"})

    out = tmp_path / "clean"
    assert clean.clean_dir(source, out, VENDORS_DIR) == [("r.html", "generic")]

    assert (out / "r.txt").read_text(encoding="utf-8") == "Fare 12.50"
    assert (out / "r.pdf").read_bytes() == b"%PDF-1.4 folio"
    assert (out / "r.png").read_bytes() == b"\x89PNG here"
    assert (out / "r.jpg").read_bytes() == b"\xff\xd8 photo"
    # The meta files stay behind. A packet is built from what is in here, and
    # the saved headers are working notes, not part of the packet.
    assert sorted(path.name for path in out.iterdir()) == [
        "r.html",
        "r.jpg",
        "r.pdf",
        "r.png",
        "r.txt",
    ]


def test_a_directory_with_nothing_to_clean_lists_nothing(tmp_path: Path) -> None:
    source = tmp_path / "receipts"
    source.mkdir()
    (source / "r.txt").write_text("Fare", encoding="utf-8")

    assert clean.clean_dir(source, tmp_path / "clean", VENDORS_DIR) == []
    assert (tmp_path / "clean" / "r.txt").is_file()


def test_the_amount_guard_warnings_come_out_in_filename_order(
    tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    """Three receipts warn at once, and the lines still read one per file.

    The vendor names run backwards against the file names on purpose. If the
    warnings came out in any order but the file order, this is what says so.
    """
    vendors = tmp_path / "vendors"
    source = tmp_path / "receipts"
    source.mkdir()
    for name, vendor in (("a.html", "zulu"), ("b.html", "yankee"), ("c.html", "xray")):
        write_rules(vendors, vendor, f"{vendor}.example", ["<p>.*?</p>"])
        (source / name).write_text("<p>Total $28.93</p>", encoding="utf-8")
        write_meta(source / name, **{"from": f"billing@{vendor}.example", "subject": "Receipt"})

    listed = clean.clean_dir(source, tmp_path / "clean", vendors)

    assert listed == [("a.html", "zulu"), ("b.html", "yankee"), ("c.html", "xray")]
    reported = [line for line in capsys.readouterr().err.splitlines() if line.strip()]
    assert reported == [
        f"clean: {vendor} strip pattern 0 skipped, it would have taken an amount with it"
        for vendor in ("zulu", "yankee", "xray")
    ]
    # The guard held: every total is still on the page it belongs to.
    for name in ("a.html", "b.html", "c.html"):
        assert "$28.93" in (tmp_path / "clean" / name).read_text(encoding="utf-8")


def test_a_directory_clean_downloads_then_inlines(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = receipts_dir(tmp_path, 2)
    cache = tmp_path / ".image-cache"
    asked: list[Path] = []
    monkeypatch.setattr(clean, "fetch_images", lambda html, where: asked.append(where) or 0)
    monkeypatch.setattr(clean, "inline_images", lambda html, where: html + "<!--inlined-->")

    clean.clean_dir(source, tmp_path / "clean", VENDORS_DIR, cache, fetch_images=True)

    assert asked == [cache, cache]
    assert "<!--inlined-->" in (tmp_path / "clean" / "r00.html").read_text(encoding="utf-8")


def test_a_directory_clean_refuses_to_fetch_without_a_cache(tmp_path: Path) -> None:
    source = receipts_dir(tmp_path, 1)
    with pytest.raises(ValueError, match="needs an image_cache"):
        clean.clean_dir(source, tmp_path / "clean", VENDORS_DIR, fetch_images=True)


# ---------------------------------------------------------------- the dir cli


def test_the_dir_cli_cleans_every_receipt_and_names_each_vendor(
    tmp_path: Path, capsys: pytest.CaptureFixture, no_network: None
) -> None:
    source = tmp_path / "receipts"
    source.mkdir()
    shutil.copyfile(sample_path("lyft"), source / "a.html")
    write_meta(source / "a.html", **{"from": "Lyft <no-reply@lyftmail.com>", "subject": "ride"})
    (source / "b.html").write_text("<p>Fare $9.00</p>", encoding="utf-8")
    (source / "b.txt").write_text("Fare 9.00", encoding="utf-8")

    out = tmp_path / "clean"
    code = clean.main(
        [
            "--dir",
            str(source),
            "--out",
            str(out),
            "--vendors",
            str(VENDORS_DIR),
        ]
    )

    assert code == 0
    captured = capsys.readouterr()
    named = [line for line in captured.err.splitlines() if " vendor " in line]
    assert named == ["clean: a.html vendor lyft", "clean: b.html vendor generic"]
    assert f"clean: wrote 2 receipts into {out.as_posix()}" in captured.out
    assert "$31.20" in (out / "a.html").read_text(encoding="utf-8")
    assert (out / "b.txt").is_file()


def test_the_cli_refuses_a_receipt_and_a_directory_together(
    tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    code = clean.main([str(tmp_path / "in.html"), "--dir", str(tmp_path), "--out", str(tmp_path)])
    assert code == 2
    assert "not both" in capsys.readouterr().err


def test_the_cli_refuses_neither_a_receipt_nor_a_directory(
    tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    code = clean.main(["--out", str(tmp_path / "out.html")])
    assert code == 2
    assert "give a receipt to clean, or --dir DIR" in capsys.readouterr().err


def test_the_cli_refuses_a_vendor_name_for_a_whole_directory(
    tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    code = clean.main(["--dir", str(tmp_path), "--out", str(tmp_path), "--vendor", "lyft"])
    assert code == 2
    assert "--vendor is for one receipt" in capsys.readouterr().err


def test_the_dir_cli_refuses_to_fetch_without_a_cache(
    tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    code = clean.main(["--dir", str(tmp_path), "--out", str(tmp_path), "--fetch-images"])
    assert code == 2
    assert "needs --images" in capsys.readouterr().err


def test_thousands_separators_are_not_money() -> None:
    """A points balance like 60,000 must not stop a promo block from being stripped."""
    assert clean.MONEY_RE.findall("earn 60,000 bonus points") == []
    assert clean.MONEY_RE.findall("Total 214,90 EUR") == ["214,90"]
    assert clean.MONEY_RE.findall("$1,234.50 charged") == ["1,234.50"]
