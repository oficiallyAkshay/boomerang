#!/usr/bin/env python3
"""Build a reimbursement packet from expense data.

Reads an expense_data.json, checks it against the schema in docs/interfaces.md,
and renders one self contained HTML file: a cover block, one summary table, and
one section per receipt in the order the data lists them. The head carries a
content security policy that allows no script and no remote fetch of any kind,
so a receipt fragment cannot reach the network from inside a packet.

Money is handled with Decimal throughout. ``totals`` returns Decimal values
quantized to two places, so a packet's lines always add up to its total and
pennies never drift. Callers that want floats convert at the edge.

Receipt files are optional at render time. A receipt whose file is not on disk
renders a visible placeholder rather than failing, so a packet can be built
before every receipt has been fetched. A pdf that cannot be opened says so on
its card and is reported by ``validate``, so a damaged file is never quietly
shown as an attachment of zero pages.
"""

from __future__ import annotations

import argparse
import base64
import html
import json
import re
import sys
from decimal import Decimal, InvalidOperation
from pathlib import Path

# The two prose rules live in check_prose, which is the gate that enforces
# them on the whole repo. Importing them here is what keeps a packet that
# validates and a packet that passes the gate the same packet.
from check_prose import BANNED_WORD_RE, EM_DASH

RID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
CENTS = Decimal("0.01")

KNOWN_KINDS = ("html", "text", "pdf", "image")
SUFFIX_KIND = {
    ".html": "html",
    ".txt": "text",
    ".pdf": "pdf",
    ".png": "image",
    ".jpg": "image",
}
# The order receipt files are looked for, so inference is deterministic when a
# rid somehow has more than one file on disk.
SUFFIX_ORDER = (".html", ".txt", ".pdf", ".png", ".jpg")
IMAGE_MEDIA = {".png": "image/png", ".jpg": "image/jpeg"}

CSS = """
body{font-family:-apple-system,Segoe UI,Helvetica,Arial,sans-serif;margin:0;color:#111;
background:#fff}
.page{max-width:900px;margin:0 auto;padding:48px 40px}
.cover{border-bottom:2px solid #111;padding-bottom:24px;margin-bottom:32px}
.cover h1{margin:0 0 6px;font-size:28px}
.cover .sub{color:#555;font-size:15px;line-height:1.5}
table.sum{width:100%;border-collapse:collapse;margin:0 0 28px;font-size:14px}
table.sum th{text-align:left;background:#f3f3f3;padding:8px 10px;border-bottom:1px solid #ddd}
table.sum td{padding:8px 10px;border-bottom:1px solid #eee;vertical-align:top}
table.sum td.amt,table.sum th.amt{text-align:right;white-space:nowrap;
font-variant-numeric:tabular-nums}
table.sum tr.day td{background:#fafafa;font-weight:600;padding-top:14px}
table.sum tr.sub td{font-weight:600;border-top:1px solid #ccc}
table.sum tr.total td{font-weight:700;font-size:17px;border-top:2px solid #111;padding-top:12px}
.rsec{page-break-before:always;page-break-after:always;margin-top:56px}
.rhead{background:#111;color:#fff;padding:12px 16px;font-size:15px;font-weight:600;
border-radius:6px 6px 0 0}
.rc{border:1px solid #ddd;border-top:0;border-radius:0 0 6px 6px;padding:16px;overflow:auto}
.rc img{max-width:100%}
.rc .plain{white-space:pre-wrap;font-family:ui-monospace,Menlo,monospace;font-size:12px;margin:0}
.card{border:1px dashed #999;border-radius:6px;padding:20px;font-size:14px;color:#333;
background:#fafafa}
.missing{color:#b00020;font-weight:600;font-size:14px}
@media print{.page{padding:24px}body{background:#fff}}
"""


# --------------------------------------------------------------------- money


def money(x: float, currency: str = "USD") -> str:
    """Format an amount for the packet.

    USD gets a symbol, everything else gets a trailing code, so no currency is
    silently shown with the wrong sign.
    """
    value = _decimal(x)
    sign = "-" if value < 0 else ""
    body = f"{abs(value):,.2f}"
    if currency == "USD":
        return f"{sign}${body}"
    return f"{sign}{body} {currency}"


def _decimal(value: object) -> Decimal:
    """Amount as a Decimal quantized to cents. Strings keep their exact digits.

    Junk raises rather than reading as zero. ``validate`` rejects a
    non-numeric amount before anything renders, so an amount that reaches
    here and cannot be read is a bug, and a packet that quietly totals a bug
    as 0.00 is worse than one that refuses to build.
    """
    try:
        return Decimal(str(value)).quantize(CENTS)
    except (InvalidOperation, ValueError) as error:
        raise ValueError(f"amount is not a number: {value!r}") from error


# ------------------------------------------------------------------ validate


def _is_number(value: object) -> bool:
    return isinstance(value, int | float | Decimal) and not isinstance(value, bool)


def _too_many_decimals(value: object) -> bool:
    """True when a number carries more precision than cents.

    Only ever called on a value ``_is_number`` has already accepted, so the
    Decimal conversion cannot fail.
    """
    exponent = Decimal(str(value)).normalize().as_tuple().exponent
    return isinstance(exponent, int) and exponent < -2


def _check_packet_text(
    problems: list[str],
    where: str,
    field: str,
    value: object,
    *,
    report_missing: bool = False,
) -> None:
    """Check one string that reaches the packet.

    Every such string has to be a non empty string free of the em dash and of
    the banned word, because the packet shows it to a reader. ``where`` locates
    the owning object and is empty for a top level field. ``report_missing``
    distinguishes a key the schema requires outright from one nested inside an
    object that is itself reported.
    """
    prefix = f"{where}: {field}" if where else f"{field}:"
    if value is None and report_missing:
        problems.append(f"{prefix} missing")
        return
    if not isinstance(value, str):
        problems.append(f"{prefix} expected a string")
        return
    if not value.strip():
        problems.append(f"{prefix} empty")
    if EM_DASH in value:
        problems.append(f"{prefix} contains an em dash")
    if BANNED_WORD_RE.search(value):
        problems.append(f"{prefix} contains a banned word")


def _check_amount(problems: list[str], where: str, amt: object) -> None:
    if not _is_number(amt):
        problems.append(f"{where}: amt expected a number")
    elif _too_many_decimals(amt):
        problems.append(f"{where}: amt has more than 2 decimals")


def _check_days(problems: list[str], data: dict, receipt_rids: set[str]) -> None:
    days = data.get("days")
    if days is None:
        problems.append("days: missing")
        return
    if not isinstance(days, list):
        problems.append("days: expected a list")
        return
    for index, day in enumerate(days):
        where = f"days[{index}]"
        if not isinstance(day, dict):
            problems.append(f"{where}: expected an object")
            continue
        _check_packet_text(problems, where, "label", day.get("label"))
        items = day.get("items")
        if not isinstance(items, list):
            problems.append(f"{where}: items expected a list")
            continue
        for position, item in enumerate(items):
            spot = f"{where}.items[{position}]"
            if not isinstance(item, dict):
                problems.append(f"{spot}: expected an object")
                continue
            _check_packet_text(problems, spot, "desc", item.get("desc"))
            _check_amount(problems, spot, item.get("amt"))
            rid = item.get("rid")
            if not isinstance(rid, str) or not RID_RE.match(rid):
                problems.append(f"{spot}: rid is not a valid id")
            elif rid not in receipt_rids:
                problems.append(f"{spot}: rid has no receipt")


def _check_receipts(problems: list[str], data: dict, receipts_dir: Path | None) -> set[str]:
    receipts = data.get("receipts")
    if receipts is None:
        problems.append("receipts: missing")
        return set()
    if not isinstance(receipts, list):
        problems.append("receipts: expected a list")
        return set()

    seen: set[str] = set()
    for index, receipt in enumerate(receipts):
        where = f"receipts[{index}]"
        if not isinstance(receipt, dict):
            problems.append(f"{where}: expected an object")
            continue
        rid = receipt.get("rid")
        if not isinstance(rid, str) or not RID_RE.match(rid):
            problems.append(f"{where}: rid is not a valid id")
        elif rid in seen:
            problems.append(f"{where}: rid appears twice")
        else:
            seen.add(rid)
        _check_packet_text(problems, where, "title", receipt.get("title"))
        # vendor never reaches the packet, so it is checked for shape only.
        vendor = receipt.get("vendor")
        if not isinstance(vendor, str):
            problems.append(f"{where}: vendor expected a string")
        elif not vendor.strip():
            problems.append(f"{where}: vendor empty")

        found = None
        if receipts_dir is not None and isinstance(rid, str):
            found = find_receipt_file(receipts_dir, rid)
        if found is not None and found.suffix.lower() == ".pdf" and _pdf_pages(found) == 0:
            problems.append(f"receipt {rid}: pdf cannot be read")

        kind = receipt.get("kind")
        if kind is None:
            continue
        if kind not in KNOWN_KINDS:
            problems.append(f"{where}: kind is not one of {', '.join(KNOWN_KINDS)}")
            continue
        if found is None:
            continue
        actual = SUFFIX_KIND[found.suffix.lower()]
        if actual != kind:
            problems.append(f"{where}: kind says {kind} but the file on disk is {actual}")
    return seen


def _check_stipend(problems: list[str], data: dict) -> None:
    stipend = data.get("stipend")
    if stipend is None:
        return
    if not isinstance(stipend, dict):
        problems.append("stipend: expected an object")
        return
    _check_packet_text(problems, "stipend", "desc", stipend.get("desc"))
    _check_amount(problems, "stipend", stipend.get("amt"))


def validate(data: dict, receipts_dir: Path | None = None) -> list[str]:
    """Check expense data against the schema. An empty list means valid.

    ``receipts_dir`` is optional. When it is given, a receipt whose declared
    kind disagrees with the file actually on disk is reported too, and so is a
    pdf on disk that cannot be opened, which would otherwise reach the packet
    as a card with no pages. A receipt with no file at all is never a problem:
    packets render placeholders.
    """
    problems: list[str] = []
    if not isinstance(data, dict):
        return ["expense data: expected an object"]

    for key in ("company", "trip", "traveler"):
        _check_packet_text(problems, "", key, data.get(key), report_missing=True)
    currency = data.get("currency")
    if currency is not None and not isinstance(currency, str):
        problems.append("currency: expected a string")

    receipt_rids = _check_receipts(problems, data, receipts_dir)
    _check_days(problems, data, receipt_rids)
    _check_stipend(problems, data)
    return problems


def load_expense_data(path: Path, receipts_dir: Path | None = None) -> dict:
    """Read and validate expense data. Raises ValueError listing every problem.

    ``receipts_dir`` is passed straight through to ``validate``, so a caller
    that knows where the receipts are gets the kind-versus-disk check and the
    unreadable-pdf check as well as the schema check.
    """
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    problems = validate(data, receipts_dir)
    if problems:
        raise ValueError("\n".join(problems))
    return data


# -------------------------------------------------------------------- totals


def totals(data: dict) -> dict:
    """Per day subtotals plus the packet totals, all Decimal quantized to cents.

    Returns {"days": [(label, subtotal)], "expenses": x, "stipend": y,
    "total": z}. Stipend is 0.00 when the data has none.
    """
    days: list[tuple[str, Decimal]] = []
    expenses = Decimal("0.00")
    for day in data.get("days", []):
        subtotal = Decimal("0.00")
        for item in day.get("items", []):
            subtotal += _decimal(item.get("amt", 0))
        subtotal = subtotal.quantize(CENTS)
        days.append((day.get("label", ""), subtotal))
        expenses += subtotal
    expenses = expenses.quantize(CENTS)

    stipend_entry = data.get("stipend")
    stipend = _decimal(stipend_entry["amt"]) if stipend_entry else Decimal("0.00")
    return {
        "days": days,
        "expenses": expenses,
        "stipend": stipend,
        "total": (expenses + stipend).quantize(CENTS),
    }


# -------------------------------------------------------------------- render


def find_receipt_file(receipts_dir: Path, rid: str) -> Path | None:
    """The receipt file for a rid, or None when none is on disk.

    A rid is a file name, never a path. It has to match ``RID_RE``, and the
    file it names has to resolve to somewhere inside the resolved receipts
    directory, so neither a traversal in the data nor a symlink on disk can
    pull a file from outside the folder the caller named into a packet.
    """
    if not isinstance(rid, str) or not RID_RE.match(rid):
        return None
    try:
        root = Path(receipts_dir).resolve(strict=True)
    except OSError:
        return None
    for suffix in SUFFIX_ORDER:
        candidate = root / f"{rid}{suffix}"
        if not candidate.is_file():
            continue
        resolved = candidate.resolve()
        if resolved.is_relative_to(root):
            return resolved
    return None


def _pdf_pages(path: Path) -> int:
    try:
        from pypdf import PdfReader

        return len(PdfReader(str(path)).pages)
    except Exception:
        # A damaged or unreadable pdf reads as zero pages. Callers say so
        # rather than presenting the zero as a real count.
        return 0


def _receipt_body(path: Path | None) -> str:
    """The inner HTML for one receipt card."""
    if path is None:
        return '<p class="missing">Receipt file not found</p>'
    suffix = path.suffix.lower()
    kind = SUFFIX_KIND[suffix]
    if kind == "html":
        # Inserted as is. Cleaning vendor markup is clean.py's job.
        return path.read_text(encoding="utf-8", errors="replace")
    if kind == "text":
        body = html.escape(path.read_text(encoding="utf-8", errors="replace"))
        return f'<pre class="plain">{body}</pre>'
    if kind == "pdf":
        pages = _pdf_pages(path)
        if pages == 0:
            return '<div class="card">PDF attachment could not be read</div>'
        return (
            f'<div class="card">PDF attachment, {pages} pages, ' "embedded in the PDF packet</div>"
        )
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    media = IMAGE_MEDIA[suffix]
    return f'<img src="data:{media};base64,{encoded}" alt="Receipt image">'


def _summary_table(data: dict, currency: str) -> str:
    figures = totals(data)
    rows = ['<table class="sum">', '<tr><th>Item</th><th class="amt">Amount</th></tr>']
    for day, (label, subtotal) in zip(data.get("days", []), figures["days"], strict=True):
        rows.append(f'<tr class="day"><td colspan="2">{html.escape(label)}</td></tr>')
        for item in day.get("items", []):
            desc = html.escape(str(item.get("desc", "")))
            amount = money(item.get("amt", 0), currency)
            rows.append(f'<tr><td>{desc}</td><td class="amt">{amount}</td></tr>')
        rows.append(
            '<tr class="sub"><td>Day subtotal</td>'
            f'<td class="amt">{money(subtotal, currency)}</td></tr>'
        )
    rows.append(
        '<tr class="sub"><td>Expenses</td>'
        f'<td class="amt">{money(figures["expenses"], currency)}</td></tr>'
    )
    stipend = data.get("stipend")
    if stipend:
        rows.append('<tr class="day"><td colspan="2">Stipend</td></tr>')
        rows.append(
            f'<tr><td>{html.escape(str(stipend.get("desc", "")))}</td>'
            f'<td class="amt">{money(stipend.get("amt", 0), currency)}</td></tr>'
        )
    rows.append(
        '<tr class="total"><td>Total</td>'
        f'<td class="amt">{money(figures["total"], currency)}</td></tr>'
    )
    rows.append("</table>")
    return "\n".join(rows)


def render_packet(data: dict, receipts_dir: Path) -> str:
    """The whole packet as one self contained HTML string."""
    receipts_dir = Path(receipts_dir)
    currency = data.get("currency") or "USD"
    company = html.escape(str(data.get("company", "")))
    title = f"Expense reimbursement packet, {company}"

    out = [
        "<!doctype html>",
        '<html lang="en"><head><meta charset="utf-8">',
        # Belt to the cleaner's braces. Even if a receipt fragment smuggled
        # something through, the packet loads no script and fetches nothing:
        # images have to be data URIs and there is no other source at all.
        '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; '
        "img-src data:; style-src 'unsafe-inline'; font-src data:\">",
        f"<title>{title}</title>",
        f"<style>{CSS}</style>",
        '</head><body><div class="page">',
        '<div class="cover"><h1>Expense reimbursement packet</h1>',
        f'<div class="sub"><b>{company}</b><br>'
        f'{html.escape(str(data.get("trip", "")))}<br>'
        f'{html.escape(str(data.get("traveler", "")))}</div></div>',
        _summary_table(data, currency),
    ]

    for number, receipt in enumerate(data.get("receipts", []), start=1):
        rid = str(receipt.get("rid", ""))
        heading = html.escape(f"Receipt {number}: {receipt.get('title', '')}")
        body = _receipt_body(find_receipt_file(receipts_dir, rid))
        out.append(
            f'<section class="rsec" data-rid="{html.escape(rid)}">'
            f'<div class="rhead">{heading}</div>'
            f'<div class="rc">{body}</div></section>'
        )

    out.append("</div></body></html>")
    return "\n".join(out)


# ----------------------------------------------------------------------- cli


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build a reimbursement packet.")
    parser.add_argument("data", type=Path, help="expense_data.json")
    parser.add_argument("--receipts", type=Path, required=True, help="receipts directory")
    parser.add_argument("--out", type=Path, required=True, help="packet html to write")
    args = parser.parse_args(argv)

    data = load_expense_data(args.data, args.receipts)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(render_packet(data, args.receipts), encoding="utf-8")

    figures = totals(data)
    currency = data.get("currency") or "USD"
    print(
        f"expenses {money(figures['expenses'], currency)}, "
        f"stipend {money(figures['stipend'], currency)}, "
        f"total {money(figures['total'], currency)}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
