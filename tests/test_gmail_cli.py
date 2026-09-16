"""Tests for the pure parts of the Gmail source.

No network, no OAuth, no Google libraries. Messages are hand built dicts in
the shape the Gmail API returns, and the service is a fake.
"""

from __future__ import annotations

import base64
import stat
from pathlib import Path

import gmail_cli
import pytest
from gmail_cli import (
    GmailSource,
    client_secret_path,
    decode_body,
    ensure_token_dir,
    header_value,
    token_path,
    walk_parts,
    write_token,
)


def b64(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode("utf-8")).decode("ascii").rstrip("=")


MESSAGE = {
    "payload": {
        "mimeType": "multipart/mixed",
        "headers": [
            {"name": "From", "value": "reservations@harborview.example"},
            {"name": "Subject", "value": "Your stay is confirmed"},
            {"name": "Date", "value": "Thu, 4 Jun 2026 09:12:00 -0700"},
        ],
        "parts": [
            {
                "mimeType": "multipart/alternative",
                "body": {},
                "parts": [
                    {"mimeType": "text/plain", "body": {"data": b64("plain body")}},
                    {"mimeType": "text/html", "body": {"data": b64("<p>rich body</p>")}},
                ],
            },
            {
                "mimeType": "application/pdf",
                "filename": "folio.pdf",
                "body": {"attachmentId": "att-1"},
            },
        ],
    }
}


class FakeMessages:
    def __init__(self, pages: list[dict], message: dict, attachment: dict):
        self.pages = pages
        self.message = message
        self.attachment = attachment
        self.list_calls: list[dict] = []

    def list(self, **kwargs):
        self.list_calls.append(kwargs)
        return FakeRequest(self.pages[len(self.list_calls) - 1])

    def list_next(self, request, response):
        if len(self.list_calls) >= len(self.pages):
            return None
        return self.list(userId="me")

    def get(self, **kwargs):
        return FakeRequest(self.message)

    def attachments(self):
        return FakeAttachments(self.attachment)


class FakeAttachments:
    def __init__(self, attachment: dict):
        self.attachment = attachment

    def get(self, **kwargs):
        return FakeRequest(self.attachment)


class FakeRequest:
    def __init__(self, payload):
        self.payload = payload

    def execute(self):
        return self.payload


class FakeUsers:
    def __init__(self, messages: FakeMessages):
        self._messages = messages

    def messages(self):
        return self._messages


class FakeService:
    def __init__(self, messages: FakeMessages):
        self._users = FakeUsers(messages)

    def users(self):
        return self._users


# ------------------------------------------------------------------ parsing


def test_decode_body_pads_the_base64url_gmail_leaves_short():
    assert decode_body(b64("a folio")) == b"a folio"


def test_decode_body_on_nothing():
    assert decode_body(None) == b""
    assert decode_body("") == b""


def test_header_value_is_case_insensitive_and_defaults_to_empty():
    payload = MESSAGE["payload"]
    assert header_value(payload, "from") == "reservations@harborview.example"
    assert header_value(payload, "SUBJECT") == "Your stay is confirmed"
    assert header_value(payload, "Reply-To") == ""
    assert header_value({}, "From") == ""


def test_walk_parts_finds_both_bodies_and_the_attachment():
    walked = walk_parts(MESSAGE["payload"])
    assert walked["html"] == "<p>rich body</p>"
    assert walked["text"] == "plain body"
    assert walked["attachments"] == [("folio.pdf", "att-1")]


def test_walk_parts_on_a_single_part_message():
    payload = {"mimeType": "text/plain", "body": {"data": b64("just text")}}
    walked = walk_parts(payload)
    assert walked == {"html": None, "text": "just text", "attachments": []}


def test_walk_parts_on_an_empty_payload():
    assert walk_parts({}) == {"html": None, "text": None, "attachments": []}


def test_the_first_body_of_each_kind_wins():
    payload = {
        "parts": [
            {"mimeType": "text/html", "body": {"data": b64("first")}},
            {"mimeType": "text/html", "body": {"data": b64("second")}},
        ]
    }
    assert walk_parts(payload)["html"] == "first"


# ------------------------------------------------------------------- source


def test_search_follows_pagination_and_asks_for_full_pages():
    messages = FakeMessages(
        [{"messages": [{"id": "a"}, {"id": "b"}]}, {"messages": [{"id": "c"}]}],
        MESSAGE,
        {},
    )
    source = GmailSource(service=FakeService(messages))
    assert source.search("after:2026/06/07") == ["a", "b", "c"]
    assert messages.list_calls[0]["maxResults"] == 500
    assert messages.list_calls[0]["q"] == "after:2026/06/07"


def test_search_on_a_query_that_matches_nothing():
    messages = FakeMessages([{}], MESSAGE, {})
    assert GmailSource(service=FakeService(messages)).search("folio") == []


def test_get_returns_the_shape_fetch_expects():
    messages = FakeMessages([{}], MESSAGE, {"data": b64("%PDF folio")})
    result = GmailSource(service=FakeService(messages)).get("rid1")
    assert result["html"] == "<p>rich body</p>"
    assert result["text"] == "plain body"
    assert result["from"] == "reservations@harborview.example"
    assert result["subject"] == "Your stay is confirmed"
    assert result["date"] == "Thu, 4 Jun 2026 09:12:00 -0700"
    assert result["attachments"] == [("folio.pdf", b"%PDF folio")]


# -------------------------------------------------------------------- token


def test_the_token_lives_under_the_config_directory(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("HOME", str(tmp_path))
    assert token_path() == tmp_path / ".config" / "boomerang" / "token.json"


def test_the_token_directory_is_owner_only(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("HOME", str(tmp_path))
    target = ensure_token_dir()
    assert stat.S_IMODE(target.parent.stat().st_mode) == 0o700


def test_the_token_file_is_owner_only(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("HOME", str(tmp_path))
    target = write_token('{"token": "not a real one"}')
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    assert target.read_text(encoding="utf-8") == '{"token": "not a real one"}'


def test_rewriting_a_token_keeps_the_mode(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("HOME", str(tmp_path))
    write_token("first")
    target = write_token("second")
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    assert target.read_text(encoding="utf-8") == "second"


def test_the_scope_is_readonly_and_nothing_else():
    assert gmail_cli.SCOPES == ["https://www.googleapis.com/auth/gmail.readonly"]


# ------------------------------------------------------------ client secret


def test_client_secret_comes_from_the_flag(tmp_path: Path):
    assert client_secret_path(tmp_path / "secret.json") == tmp_path / "secret.json"


def test_client_secret_falls_back_to_the_environment(monkeypatch):
    monkeypatch.setenv("BOOMERANG_GMAIL_CLIENT_SECRET", "/tmp/secret.json")
    assert client_secret_path(None) == Path("/tmp/secret.json")


def test_client_secret_missing_says_what_to_do(monkeypatch):
    monkeypatch.delenv("BOOMERANG_GMAIL_CLIENT_SECRET", raising=False)
    with pytest.raises(SystemExit) as caught:
        client_secret_path(None)
    assert "--client-secret" in str(caught.value)


# --------------------------------------------------------------------- cli


def test_the_get_command_refuses_a_traversal_id(tmp_path: Path):
    with pytest.raises(SystemExit):
        gmail_cli.main(["get", "../escape", "--out", str(tmp_path)])
    assert list(tmp_path.iterdir()) == []


def test_the_get_command_writes_through_the_shared_writer(tmp_path: Path, monkeypatch, capsys):
    messages = FakeMessages([{}], MESSAGE, {"data": b64("%PDF folio")})
    monkeypatch.setattr(gmail_cli, "GmailSource", lambda: GmailSource(FakeService(messages)))
    assert gmail_cli.main(["get", "rid1", "--out", str(tmp_path)]) == 0
    assert (tmp_path / "rid1.html").read_text(encoding="utf-8") == "<p>rich body</p>"
    assert (tmp_path / "rid1.0.pdf").read_bytes() == b"%PDF folio"
    assert "rid1.0.pdf" in capsys.readouterr().out


def test_the_search_command_prints_one_id_per_line(tmp_path: Path, monkeypatch, capsys):
    messages = FakeMessages([{"messages": [{"id": "a"}, {"id": "b"}]}], MESSAGE, {})
    monkeypatch.setattr(gmail_cli, "GmailSource", lambda: GmailSource(FakeService(messages)))
    assert gmail_cli.main(["search", "folio"]) == 0
    assert capsys.readouterr().out.splitlines() == ["a", "b"]
