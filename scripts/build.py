#!/usr/bin/env python3
"""Build a reimbursement packet from expense data.

Reads an expense_data.json, checks it against the schema in references/interfaces.md,
and renders one self contained HTML file: a cover block, one summary table, and
one section per receipt in the order the data lists them. The head carries a
content security policy that allows no script and no remote fetch of any kind,
so a receipt fragment cannot reach the network from inside a packet.

Money is handled with Decimal throughout. ``totals`` returns Decimal values
quantized to two places, so a packet's lines always add up to its total and
pennies never drift. Callers that want floats convert at the edge.

The packet owns its arithmetic. A refunded line carries ``amt`` and
``refund`` and the packet claims the difference; a stipend carries ``rate``
and ``days`` and the packet multiplies them; a line paid in another currency
carries ``local_amt`` and ``local_currency`` and the packet notes what the
receipt shows. Nothing upstream has to do the sum, so nothing upstream can do
it differently from the total.

Receipt files are optional at render time. A receipt whose file is not on disk
renders a visible placeholder rather than failing, so a packet can be built
before every receipt has been fetched. A pdf that cannot be opened says so on
its card and is reported by ``validate``, so a damaged file is never quietly
shown as an attachment of zero pages.

The CLI ends with two readings rather than two more errors. It prints the page
count to expect, and it names any line whose claimed value is not printed
anywhere on its own receipt, which is how a hand typed amount and a netted
refund get caught before a reviewer finds them.
"""

from __future__ import annotations

import argparse
import base64
import html
import json
import re
import sys
from datetime import UTC, datetime
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
    ".jpeg": "image",
}
# The order receipt files are looked for, so inference is deterministic when a
# rid somehow has more than one file on disk. A receipt that names its kind is
# looked up by that kind first, and this order decides the rest.
SUFFIX_ORDER = (".html", ".txt", ".pdf", ".png", ".jpg", ".jpeg")
IMAGE_MEDIA = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg"}

# A line description is a place in the packet, never a postal address: the
# street is inside the receipt, where the vendor printed it.
STREET_RE = re.compile(
    r"\b\d{1,5}\s+[A-Z][a-z]+\.?\s+"
    r"(St|Street|Ave|Avenue|Blvd|Boulevard|Rd|Road|Dr|Drive|Way|Ln|Lane"
    r"|Ter|Terrace|Pl|Place)\b"
)
MEAL_WORDS = ("breakfast", "brunch", "lunch", "dinner", "coffee", "snack")
MEAL_MINUTES = 60
# What a receipt fragment looks like when it never went through clean.py.
UNCLEANED_RE = re.compile(r"<script|<html|<head|on\w+=", re.I)
TAG_RE = re.compile(r"<[^>]*>")

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


def _check_amount(problems: list[str], where: str, amt: object, field: str = "amt") -> None:
    if not _is_number(amt):
        problems.append(f"{where}: {field} expected a number")
    elif _too_many_decimals(amt):
        problems.append(f"{where}: {field} has more than 2 decimals")


def _check_refund(problems: list[str], where: str, item: dict) -> None:
    """A refund is money the vendor gave back, so the packet claims the rest."""
    refund = item.get("refund")
    if refund is None:
        return
    before = len(problems)
    _check_amount(problems, where, refund, "refund")
    if len(problems) != before:
        return
    if refund < 0:
        problems.append(f"{where}: refund is less than zero")
        return
    amt = item.get("amt")
    if _is_number(amt) and refund > amt:
        problems.append(f"{where}: refund is more than amt")


def _check_local(problems: list[str], where: str, item: dict) -> None:
    """The amount the receipt printed, in the currency the receipt printed it in."""
    amount, currency = item.get("local_amt"), item.get("local_currency")
    if amount is None and currency is None:
        return
    if amount is None or currency is None:
        problems.append(f"{where}: local_amt and local_currency go together")
        return
    _check_amount(problems, where, amount, "local_amt")
    _check_packet_text(problems, where, "local_currency", currency)


def _timestamp(value: object) -> datetime | None:
    """One ``at`` value as a datetime, or None when it is not one.

    A value with no zone is read as UTC, so two lines are always comparable
    and a mixed pair never raises instead of reporting.
    """
    if not isinstance(value, str):
        return None
    try:
        when = datetime.fromisoformat(value)
    except ValueError:
        return None
    return when if when.tzinfo else when.replace(tzinfo=UTC)


def _check_item_extras(problems: list[str], where: str, item: dict) -> None:
    """The optional item fields, each checked only when it is there."""
    _check_refund(problems, where, item)
    _check_local(problems, where, item)
    desc = item.get("desc")
    if isinstance(desc, str) and STREET_RE.search(desc):
        problems.append(
            f"{where}: desc reads as a street address, "
            "line descriptions say Hotel, Office, Airport, Home"
        )
    at = item.get("at")
    if at is not None and _timestamp(at) is None:
        problems.append(f"{where}: at expected an ISO 8601 datetime")


def _meal_word(desc: object) -> bool:
    return isinstance(desc, str) and desc.strip().lower().startswith(MEAL_WORDS)


def _check_meal_duplicates(problems: list[str], where: str, items: list) -> None:
    """Two meals an hour apart on one day are one meal claimed twice.

    Only lines that carry a readable ``at`` are compared, so a day whose
    meals have no times says nothing rather than guessing.
    """
    meals = [
        (str(item.get("desc", "")), _timestamp(item.get("at")))
        for item in items
        if isinstance(item, dict) and _meal_word(item.get("desc"))
    ]
    timed = [(desc, when) for desc, when in meals if when is not None]
    for index, (desc, when) in enumerate(timed):
        for other_desc, other_when in timed[index + 1 :]:
            apart = abs((other_when - when).total_seconds()) / 60
            if apart <= MEAL_MINUTES:
                problems.append(
                    f'{where}: two meal lines within an hour, "{desc}" and "{other_desc}"'
                )


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
            _check_item_extras(problems, spot, item)
            rid = item.get("rid")
            if not isinstance(rid, str) or not RID_RE.match(rid):
                problems.append(f"{spot}: rid is not a valid id")
            elif rid not in receipt_rids:
                problems.append(f"{spot}: rid has no receipt")
        _check_meal_duplicates(problems, where, items)


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

        kind = receipt.get("kind")
        found = None
        on_disk: list[Path] = []
        if receipts_dir is not None and isinstance(rid, str):
            on_disk = receipt_files(receipts_dir, rid)
            found = _preferred_file(on_disk, kind)
        if len(on_disk) > 1 and kind is None:
            names = ", ".join(path.name for path in on_disk)
            problems.append(f"receipt {rid}: more than one file on disk ({names}), name the kind")
        if found is not None and found.suffix.lower() == ".pdf" and _pdf_pages(found) == 0:
            problems.append(f"receipt {rid}: pdf cannot be read")
        if found is not None and SUFFIX_KIND[found.suffix.lower()] == "html":
            body = found.read_text(encoding="utf-8", errors="replace")
            if UNCLEANED_RE.search(body):
                problems.append(f"receipt {rid}: looks uncleaned, run clean.py first")

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
    """The stipend line, which may state its total or state what makes it up."""
    stipend = data.get("stipend")
    if stipend is None:
        return
    if not isinstance(stipend, dict):
        problems.append("stipend: expected an object")
        return
    _check_packet_text(problems, "stipend", "desc", stipend.get("desc"))

    rate, days, amt = stipend.get("rate"), stipend.get("days"), stipend.get("amt")
    if rate is None and days is None:
        _check_amount(problems, "stipend", amt)
        return
    if rate is None or days is None:
        problems.append("stipend: rate and days go together")
        return

    before = len(problems)
    _check_amount(problems, "stipend", rate, "rate")
    _check_amount(problems, "stipend", days, "days")
    if len(problems) != before:
        return
    if rate < 0 or days < 0:
        problems.append("stipend: rate and days are counts, so neither is less than zero")
        return
    if amt is None:
        return
    _check_amount(problems, "stipend", amt)
    if _is_number(amt) and _decimal(amt) != (_decimal(rate) * _decimal(days)).quantize(CENTS):
        problems.append("stipend: amt is not rate times days")


def validate(data: dict, receipts_dir: Path | None = None) -> list[str]:
    """Check expense data against the schema. An empty list means valid.

    ``receipts_dir`` is optional. When it is given, four more problems are
    reported: a declared kind that disagrees with the file on disk, a pdf that
    cannot be opened, a rid with more than one file and no kind to choose
    between them, and an html receipt that still reads as raw vendor mail. A
    receipt with no file at all is never a problem: packets render
    placeholders.
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


def day_list(data: dict) -> list:
    """The days, or a validate shaped error rather than a surprise later.

    ``validate`` reports a days list of the wrong shape and stops the build.
    A caller that skipped it gets the same sentence here instead of an
    AttributeError from the middle of a subtotal.
    """
    days = data.get("days", [])
    if not isinstance(days, list):
        raise ValueError("days: expected a list")
    return days


def item_claim(item: dict) -> Decimal:
    """What the packet claims for one line: the amount, less any refund."""
    amount = _decimal(item.get("amt", 0))
    refund = item.get("refund")
    if refund is None:
        return amount
    return (amount - _decimal(refund)).quantize(CENTS)


def stipend_claim(stipend: dict) -> Decimal:
    """What the packet claims for the stipend: the stated total, or days times rate."""
    amt = stipend.get("amt")
    if amt is not None:
        return _decimal(amt)
    rate, days = stipend.get("rate"), stipend.get("days")
    if rate is None or days is None:
        raise ValueError("stipend: amt expected a number")
    return (_decimal(rate) * _decimal(days)).quantize(CENTS)


def totals(data: dict) -> dict:
    """Per day subtotals plus the packet totals, all Decimal quantized to cents.

    Returns {"days": [(label, subtotal)], "expenses": x, "stipend": y,
    "total": z}. Stipend is 0.00 when the data has none. Every figure is the
    claimed value, so a refunded line counts for what is left of it.
    """
    days: list[tuple[str, Decimal]] = []
    expenses = Decimal("0.00")
    for day in day_list(data):
        subtotal = Decimal("0.00")
        for item in day.get("items", []):
            subtotal += item_claim(item)
        subtotal = subtotal.quantize(CENTS)
        days.append((day.get("label", ""), subtotal))
        expenses += subtotal
    expenses = expenses.quantize(CENTS)

    stipend_entry = data.get("stipend")
    stipend = stipend_claim(stipend_entry) if stipend_entry else Decimal("0.00")
    return {
        "days": days,
        "expenses": expenses,
        "stipend": stipend,
        "total": (expenses + stipend).quantize(CENTS),
    }


# -------------------------------------------------------------------- render


def receipt_files(receipts_dir: Path, rid: str) -> list[Path]:
    """Every receipt file on disk for a rid, in the suffix order.

    A rid is a file name, never a path. It has to match ``RID_RE``, and the
    file it names has to resolve to somewhere inside the resolved receipts
    directory, so neither a traversal in the data nor a symlink on disk can
    pull a file from outside the folder the caller named into a packet. Two
    names that resolve to one file count once.
    """
    if not isinstance(rid, str) or not RID_RE.match(rid):
        return []
    try:
        root = Path(receipts_dir).resolve(strict=True)
    except OSError:
        return []
    found: list[Path] = []
    for suffix in SUFFIX_ORDER:
        candidate = root / f"{rid}{suffix}"
        if not candidate.is_file():
            continue
        resolved = candidate.resolve()
        if resolved.is_relative_to(root) and resolved not in found:
            found.append(resolved)
    return found


def _preferred_file(found: list[Path], kind: object) -> Path | None:
    """The file a receipt means: the one its kind names, else the first found."""
    if not found:
        return None
    if kind in KNOWN_KINDS:
        for path in found:
            if SUFFIX_KIND[path.suffix.lower()] == kind:
                return path
    return found[0]


def find_receipt_file(receipts_dir: Path, rid: str, kind: object = None) -> Path | None:
    """The receipt file for a rid, or None when none is on disk.

    A receipt that declares its ``kind`` gets that kind's file, whatever else
    shares the rid. Without a kind the suffix order decides, so inference
    stays deterministic.
    """
    return _preferred_file(receipt_files(receipts_dir, rid), kind)


def _pdf_pages(path: Path) -> int:
    try:
        from pypdf import PdfReader

        return len(PdfReader(str(path)).pages)
    except Exception:
        # A damaged or unreadable pdf reads as zero pages. Callers say so
        # rather than presenting the zero as a real count.
        return 0


def _receipt_text(path: Path) -> str | None:
    """What a receipt prints, as plain text, or None when it cannot be read.

    Only for the amount reading at the end of the CLI. HTML loses its tags,
    a pdf is read with pypdf, and an image is not read at all: a photo of a
    receipt says nothing a substring test can use.
    """
    kind = SUFFIX_KIND[path.suffix.lower()]
    if kind == "image":
        return None
    if kind == "pdf":
        try:
            from pypdf import PdfReader

            return "\n".join(page.extract_text() or "" for page in PdfReader(str(path)).pages)
        except Exception:
            return None
    body = path.read_text(encoding="utf-8", errors="replace")
    if kind == "html":
        body = TAG_RE.sub(" ", body)
    return html.unescape(body)


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
        return f'<div class="card">PDF attachment, {pages} pages, embedded in the PDF packet</div>'
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    media = IMAGE_MEDIA[suffix]
    return f'<img src="data:{media};base64,{encoded}" alt="Receipt image">'


def _count(value: object) -> str:
    """A whole count with no trailing zeros, for the stipend note."""
    number = Decimal(str(value)).normalize()
    whole = number.to_integral_value()
    return str(whole) if number == whole else str(number)


def _item_line(item: dict, currency: str) -> str:
    """One line description, with whatever the arithmetic owes the reader.

    A netted refund and a local currency each add a short note, so the amount
    column stays the claimed value and the reader can still see where it came
    from without opening the receipt.
    """
    parts = [str(item.get("desc", ""))]
    refund = item.get("refund")
    if refund is not None:
        parts.append(
            f" ({money(item.get('amt', 0), currency)}, {money(refund, currency)} refund netted)"
        )
    local, local_currency = item.get("local_amt"), item.get("local_currency")
    if local is not None and local_currency is not None:
        parts.append(f" (receipt shows {money(local, str(local_currency))})")
    return html.escape("".join(parts))


def _stipend_line(stipend: dict, currency: str) -> str:
    """The stipend description, with days times rate when that is what it is."""
    text = str(stipend.get("desc", ""))
    rate, days = stipend.get("rate"), stipend.get("days")
    if rate is not None and days is not None:
        text += f" ({_count(days)} x {money(rate, currency)})"
    return html.escape(text)


def _summary_table(data: dict, currency: str) -> str:
    figures = totals(data)
    rows = ['<table class="sum">', '<tr><th>Item</th><th class="amt">Amount</th></tr>']
    for day, (label, subtotal) in zip(day_list(data), figures["days"], strict=True):
        rows.append(f'<tr class="day"><td colspan="2">{html.escape(label)}</td></tr>')
        for item in day.get("items", []):
            desc = _item_line(item, currency)
            amount = money(item_claim(item), currency)
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
            f"<tr><td>{_stipend_line(stipend, currency)}</td>"
            f'<td class="amt">{money(stipend_claim(stipend), currency)}</td></tr>'
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
    day_list(data)
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
        f"{html.escape(str(data.get('trip', '')))}<br>"
        f"{html.escape(str(data.get('traveler', '')))}</div></div>",
        _summary_table(data, currency),
    ]

    for number, receipt in enumerate(data.get("receipts", []), start=1):
        rid = str(receipt.get("rid", ""))
        heading = html.escape(f"Receipt {number}: {receipt.get('title', '')}")
        body = _receipt_body(find_receipt_file(receipts_dir, rid, receipt.get("kind")))
        # The section is numbered, not identified. A rid is a mailbox id and
        # the packet shows no mailbox ids, in an attribute or anywhere else.
        out.append(
            f'<section class="rsec" data-receipt="{number}">'
            f'<div class="rhead">{heading}</div>'
            f'<div class="rc">{body}</div></section>'
        )

    out.append("</div></body></html>")
    return "\n".join(out)


# ----------------------------------------------------------------------- cli


def unprinted_amounts(data: dict, receipts_dir: Path) -> list[str]:
    """The description of every line whose claimed value is not on its receipt.

    A reading, not a rule. A value that is right and simply printed
    differently, a receipt that is an image, and a receipt not fetched yet all
    land here the same way, so the CLI names them and carries on.
    """
    kinds = {
        receipt.get("rid"): receipt.get("kind")
        for receipt in data.get("receipts", [])
        if isinstance(receipt, dict)
    }
    texts: dict[object, str | None] = {}
    unprinted: list[str] = []
    for day in day_list(data):
        for item in day.get("items", []):
            rid = item.get("rid")
            if rid not in texts:
                path = find_receipt_file(receipts_dir, str(rid), kinds.get(rid))
                texts[rid] = _receipt_text(path) if path is not None else None
            body = texts[rid]
            if body is None:
                continue
            claimed = item_claim(item)
            if any(form in body for form in (f"{claimed:f}", f"{claimed:,f}")):
                continue
            unprinted.append(str(item.get("desc", "")))
    return unprinted


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
    pages = 1 + len(data.get("receipts", []))
    print(f"pages expected {pages}, one more for every page the summary spills onto")
    for desc in unprinted_amounts(data, args.receipts):
        print(f"amount not printed on receipt: {desc}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
