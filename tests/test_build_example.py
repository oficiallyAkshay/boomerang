"""Tests for the worked example's builder.

The builder itself is covered by running it: ``examples/build_example.py
--check`` rebuilds the committed packet offline and compares it byte for byte,
and the repo's own checks run that. What is tested here is the one guard that
a ``--check`` run can never reach, because ``--check`` compares against a
packet that is already clean: the refusal at the end of a network rebuild,
which is what stops a packet full of live image sources from being committed
in the first place.

Nothing here opens the network or writes into ``examples/``.
"""

from __future__ import annotations

import build_example
import pytest

REMOTE = "https://cache.marriott.com/marriottassets/map_icon.png"


@pytest.mark.parametrize(
    "tag",
    [
        f'<img src="{REMOTE}">',
        f"<img src='{REMOTE}'>",
        f"<img src={REMOTE}>",
        f'<img  src = "{REMOTE}" alt="map">',
        f'<img SRC="{REMOTE}">',
    ],
)
def test_a_live_image_source_is_found_however_it_was_quoted(tag: str) -> None:
    """Double quoted, single quoted, unquoted, spaced out and upper case."""
    assert build_example.remote_sources(f"<div>{tag}</div>") == [REMOTE]


@pytest.mark.parametrize(
    "html",
    [
        '<img src="data:image/png;base64,iVBORw0KGgo=">',
        '<span class="img-alt">Lyft</span>',
        "<a>https://cache.marriott.com/marriottassets/map_icon.png</a>",
        '<img src="/local/logo.png">',
        "",
    ],
)
def test_a_packet_that_reaches_for_nothing_reports_nothing(html: str) -> None:
    """A data URI, alt text, a URL in prose and a relative path are all fine."""
    assert build_example.remote_sources(html) == []


def test_each_url_is_reported_once_in_the_order_it_appears() -> None:
    """One picture used twice is one thing to go and fix, not two."""
    other = "https://cdn.lyft.net/logo.png"
    html = f'<img src="{other}"><img src="{REMOTE}"><img src=\'{other}\'>'
    assert build_example.remote_sources(html) == [other, REMOTE]


def test_the_committed_packet_reaches_for_nothing() -> None:
    """What the guard is there to keep true, checked against the commit."""
    html = build_example.PACKET_HTML.read_text(encoding="utf-8")
    assert build_example.remote_sources(html) == []
