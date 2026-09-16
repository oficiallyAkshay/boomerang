#!/usr/bin/env python3
"""Read only Gmail access, for hosts with no mail connector.

This is the only path that can pull attachments, which is how hotel folios
arrive. The scope is readonly and nothing else, the token lives in the
user's config directory with owner only permissions, and no message is kept
anywhere but the receipts directory the caller names.

The Google libraries are an optional extra. Every import of them happens
inside a function, so this module imports cleanly without them and only the
commands that talk to Gmail need them installed.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
from pathlib import Path

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]
PAGE_SIZE = 500
INSTALL_HINT = "Gmail access needs the optional extra: uv sync --extra gmail"


# ------------------------------------------------------------------ token


def token_path() -> Path:
    """Where the OAuth token lives."""
    return Path.home() / ".config" / "boomerang" / "token.json"


def ensure_token_dir(path: Path | None = None) -> Path:
    """Create the token directory owner only and return the token path."""
    target = Path(path) if path else token_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.parent.chmod(0o700)
    return target


def write_token(text: str, path: Path | None = None) -> Path:
    """Write the token with mode 600, under a 700 directory."""
    target = ensure_token_dir(path)
    target.touch(mode=0o600, exist_ok=True)
    target.chmod(0o600)
    target.write_text(text, encoding="utf-8")
    return target


def client_secret_path(explicit: str | Path | None = None) -> Path:
    """The OAuth client secret, from the flag or the environment."""
    value = explicit or os.environ.get("BOOMERANG_GMAIL_CLIENT_SECRET")
    if not value:
        raise SystemExit("Pass --client-secret PATH or set BOOMERANG_GMAIL_CLIENT_SECRET")
    return Path(value)


# ----------------------------------------------------------------- parsing


def decode_body(data: str | None) -> bytes:
    """Decode one base64url body, padding as Gmail omits it."""
    if not data:
        return b""
    padded = data + "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(padded.encode("ascii"))


def header_value(payload: dict, name: str) -> str:
    """One header by name, case insensitive, empty string when absent."""
    wanted = name.lower()
    for item in payload.get("headers") or []:
        if str(item.get("name", "")).lower() == wanted:
            return str(item.get("value", ""))
    return ""


def walk_parts(payload: dict) -> dict:
    """Walk a message payload for the first html body, text body and attachments.

    Returns {"html": str|None, "text": str|None, "attachments": [(filename, id)]}.
    """
    html_body: str | None = None
    text_body: str | None = None
    attachments: list[tuple[str, str]] = []

    queue = [payload or {}]
    while queue:
        part = queue.pop(0)
        queue.extend(part.get("parts") or [])
        body = part.get("body") or {}
        filename = part.get("filename") or ""
        if filename and body.get("attachmentId"):
            attachments.append((filename, body["attachmentId"]))
            continue
        data = body.get("data")
        if not data:
            continue
        mime = str(part.get("mimeType", "")).lower()
        if mime == "text/html" and html_body is None:
            html_body = decode_body(data).decode("utf-8", "replace")
        elif mime == "text/plain" and text_body is None:
            text_body = decode_body(data).decode("utf-8", "replace")

    return {"html": html_body, "text": text_body, "attachments": attachments}


# ------------------------------------------------------------------ google


def _google_modules():
    """Import the optional Google libraries, or say how to install them."""
    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
        from googleapiclient.discovery import build
    except ImportError:
        raise SystemExit(INSTALL_HINT) from None
    return Request, Credentials, InstalledAppFlow, build


def authorize(client_secret: Path, path: Path | None = None) -> Path:
    """Run the installed app flow once and store the token."""
    _, _, installed_app_flow, _ = _google_modules()
    flow = installed_app_flow.from_client_secrets_file(str(client_secret), SCOPES)
    credentials = flow.run_local_server(port=0)
    return write_token(credentials.to_json(), path)


def build_service(path: Path | None = None):
    """A Gmail service from the stored token, refreshed when stale."""
    request, credentials_class, _, build = _google_modules()
    target = Path(path) if path else token_path()
    if not target.exists():
        raise SystemExit("No token yet. Run: gmail_cli.py auth --client-secret PATH")
    credentials = credentials_class.from_authorized_user_file(str(target), SCOPES)
    if not credentials.valid and credentials.expired and credentials.refresh_token:
        credentials.refresh(request())
        write_token(credentials.to_json(), target)
    return build("gmail", "v1", credentials=credentials, cache_discovery=False)


class GmailSource:
    """The search and get pair fetch.py expects."""

    def __init__(self, service=None, token: Path | None = None) -> None:
        self._service = service
        self._token = token

    def service(self):
        if self._service is None:
            self._service = build_service(self._token)
        return self._service

    def search(self, query: str) -> list[str]:
        """Every message id matching the query, following pagination."""
        messages = self.service().users().messages()
        request = messages.list(userId="me", q=query, maxResults=PAGE_SIZE)
        ids: list[str] = []
        while request is not None:
            response = request.execute() or {}
            ids.extend(m["id"] for m in response.get("messages") or [])
            request = messages.list_next(request, response)
        return ids

    def get(self, rid: str) -> dict:
        """One message: bodies, headers and attachment bytes."""
        service = self.service()
        message = service.users().messages().get(userId="me", id=rid, format="full").execute() or {}
        payload = message.get("payload") or {}
        walked = walk_parts(payload)

        attachments: list[tuple[str, bytes]] = []
        for filename, attachment_id in walked["attachments"]:
            blob = (
                service.users()
                .messages()
                .attachments()
                .get(userId="me", messageId=rid, id=attachment_id)
                .execute()
                or {}
            )
            attachments.append((filename, decode_body(blob.get("data"))))

        return {
            "html": walked["html"],
            "text": walked["text"],
            "from": header_value(payload, "From"),
            "subject": header_value(payload, "Subject"),
            "date": header_value(payload, "Date"),
            "attachments": attachments,
        }


# --------------------------------------------------------------------- cli


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read only Gmail access for boomerang.")
    sub = parser.add_subparsers(dest="command", required=True)

    auth = sub.add_parser("auth", help="run the OAuth flow once")
    auth.add_argument("--client-secret", help="path to the OAuth client secret json")

    search = sub.add_parser("search", help="print matching message ids")
    search.add_argument("query", help="a Gmail search string")

    get = sub.add_parser("get", help="write one message to a receipts directory")
    get.add_argument("rid")
    get.add_argument("--out", type=Path, required=True)

    args = parser.parse_args(argv)

    if args.command == "auth":
        target = authorize(client_secret_path(args.client_secret))
        print(f"token written to {target}")
        return 0

    if args.command == "search":
        for rid in GmailSource().search(args.query):
            print(rid)
        return 0

    from fetch import RID_RE, write_message

    if not RID_RE.match(args.rid):
        raise SystemExit("That message id has characters a file name must not carry")
    args.out.mkdir(parents=True, exist_ok=True)
    meta = write_message(args.out, args.rid, GmailSource().get(args.rid))
    print(json.dumps(meta, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
