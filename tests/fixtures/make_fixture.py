#!/usr/bin/env python3
"""Synthetic receipt fixtures.

Everything here is invented: the company, the traveler, the hotel, the drivers,
the card, the message ids and every amount. Nothing is copied from a real
mailbox. Amounts come from a seeded random generator, so a given seed always
produces the same packet and the same totals.

The trip: a two night onsite, AUS to SEA and back, in June 2026. One travel
day, one working day, one personal day on which only the airport leg is kept.

This is a module the tests import through the ``fixture_dir`` fixture in
``tests/conftest.py``. It has no command line: ``synth`` is on the path only
when pytest puts it there, so a ``__main__`` block here could not be run.
"""

from __future__ import annotations

import base64
import json
import random
from pathlib import Path

from synth import money, pdf_bytes, png_bytes

COMPANY = "Northwind Labs, Inc."
TRAVELER = "Jordan Rivera"
TRIP = "AUS to SEA onsite, June 2026"
FIRST_NAME = TRAVELER.split()[0]
CARD_LAST4 = "4321"
HOTEL = "Harborview Hotel"
OFFICE = "Northwind office"

# Message ids, one per receipt. Generated once with secrets.token_hex(8) and
# frozen here so the fixture stays deterministic.
RIDS = {
    "flight": "e20f48c14c0aa535",
    "lyft1": "42460d7c6b5cb69b",
    "lyft2": "43da0fa3c12ff7e2",
    "hotel_conf": "6c4f66af039cb998",
    "lyft3": "06fe47acc4953dde",
    "scooter": "b90640a69fc35796",
    "dd1": "93db2911eb0e2dcd",
    "transit": "de472d8a39854951",
    "dd2": "a39b9d9258d1326a",
    "folio": "da775519438f74c6",
    "lyft4": "fde1b554db824a1e",
}

DRIVERS = ["Teodoro", "Marisol", "Osric", "Linnea"]


# ---------------------------------------------------------------- tiny binary


def png_data_uri(width: int, height: int, rgb: tuple[int, int, int]) -> str:
    return "data:image/png;base64," + base64.b64encode(png_bytes(width, height, rgb)).decode()


def _rows(lines: list[tuple[str, float]], total_label: str, total: float) -> str:
    cells = []
    for label, amount in lines:
        cells.append(
            '<tr><td style="padding:6px 0;color:#333">' + label + "</td>"
            '<td align="right" style="padding:6px 0;color:#333">' + money(amount) + "</td></tr>"
        )
    cells.append(
        '<tr><td style="padding:10px 0;border-top:1px solid #ddd;font-weight:700">'
        + total_label
        + '</td><td align="right" style="padding:10px 0;border-top:1px solid #ddd;'
        'font-weight:700">' + money(total) + "</td></tr>"
    )
    return "".join(cells)


# ------------------------------------------------------------------ renderers


def render_lyft(fields: dict) -> str:
    """A Lyft style ride receipt: pink logo, driver thanks, fare table, footer."""
    logo = png_data_uri(1, 1, (255, 0, 187))
    track = fields["track"]
    return f"""<html><body style="margin:0;padding:0;background:#f4f4f4">
<table width="100%" cellpadding="0" cellspacing="0" border="0"
 style="font-family:Helvetica,Arial,sans-serif;background:#f4f4f4">
<tr><td align="center" style="padding:24px 12px">
<table width="600" cellpadding="0" cellspacing="0" border="0" style="background:#ffffff">
<tr><td style="padding:24px 28px 8px 28px">
<img src="{logo}" width="34" height="34" alt="Lyft" style="background:#ff00bf;border-radius:6px">
</td></tr>
<tr><td style="padding:0 28px 4px 28px;font-size:22px;color:#11111f">
Thanks for riding with {fields["driver"]}</td></tr>
<tr><td style="padding:0 28px 18px 28px;font-size:13px;color:#6b6b76">
{fields["when"]}</td></tr>
<tr><td style="padding:0 28px">
<table width="100%" cellpadding="0" cellspacing="0" border="0" style="font-size:14px">
{_rows(fields["lines"], "Total", fields["total"])}
</table></td></tr>
<tr><td style="padding:14px 28px 4px 28px;font-size:13px;color:#333">
Visa *{CARD_LAST4}</td></tr>
<tr><td style="padding:0 28px 18px 28px;font-size:13px;color:#6b6b76">
Pickup {fields["pickup"]} at {fields["pickup_time"]}<br>
Dropoff {fields["dropoff"]} at {fields["dropoff_time"]}</td></tr>
<tr><td style="padding:16px 28px;background:#faf7fb;font-size:12px;color:#6b6b76">
Ride more, pay less with a ride pass.
<a href="https://example.com/track?u={track}a" style="color:#ff00bf">See passes</a><br>
Add a tip for {fields["driver"]} any time in the app.
<a href="https://example.com/track?u={track}b" style="color:#ff00bf">Open the app</a>
</td></tr>
</table></td></tr></table></body></html>
"""


def render_doordash(fields: dict) -> str:
    """A DoorDash style order receipt: red header, item lines, order tracking."""
    items = "".join(
        '<tr><td style="padding:5px 0;font-size:14px;color:#191919">'
        f'{count} x {name}</td><td align="right" style="padding:5px 0;font-size:14px">'
        f"{money(amount)}</td></tr>"
        for count, name, amount in fields["items"]
    )
    track = fields["track"]
    return f"""<html><body style="margin:0;padding:0;background:#f7f7f7">
<table width="100%" cellpadding="0" cellspacing="0" border="0"
 style="font-family:Helvetica,Arial,sans-serif;background:#f7f7f7">
<tr><td align="center" style="padding:24px 12px">
<table width="600" cellpadding="0" cellspacing="0" border="0" style="background:#ffffff">
<tr><td style="background:#eb1700;padding:18px 28px;color:#ffffff;font-size:20px">
Order receipt</td></tr>
<tr><td style="padding:22px 28px 6px 28px;font-size:18px;color:#191919">
Thanks for your order, {FIRST_NAME}</td></tr>
<tr><td style="padding:0 28px 16px 28px;font-size:13px;color:#767676">
{fields["store"]} on {fields["when"]}</td></tr>
<tr><td style="padding:0 28px">
<table width="100%" cellpadding="0" cellspacing="0" border="0">{items}</table></td></tr>
<tr><td style="padding:10px 28px 0 28px">
<table width="100%" cellpadding="0" cellspacing="0" border="0" style="font-size:14px">
{_rows(fields["lines"], "Total", fields["total"])}
</table></td></tr>
<tr><td style="padding:14px 28px;font-size:13px;color:#191919">
Paid with Visa Ending in {CARD_LAST4}</td></tr>
<tr><td style="padding:0 28px 22px 28px">
<a href="https://example.com/track?u={track}a"
 style="background:#eb1700;color:#ffffff;padding:10px 18px;text-decoration:none;
 font-size:14px;border-radius:4px">Track your order</a></td></tr>
<tr><td style="padding:16px 28px;background:#f2f2f2;font-size:11px;color:#767676">
You are receiving this because you placed an order.
<a href="https://example.com/track?u={track}b" style="color:#767676">Unsubscribe</a>
</td></tr>
</table></td></tr></table></body></html>
"""


def render_united(fields: dict) -> str:
    """A United style eTicket: confirmation code, itinerary, fare breakdown."""
    legs = "".join(
        '<tr><td style="padding:8px 0;font-size:14px;color:#20262b">'
        f'{flight}</td><td style="padding:8px 0;font-size:14px">{route}</td>'
        f'<td align="right" style="padding:8px 0;font-size:14px">{when}</td></tr>'
        for flight, route, when in fields["legs"]
    )
    return f"""<html><body style="margin:0;padding:0;background:#eef1f4">
<table width="100%" cellpadding="0" cellspacing="0" border="0"
 style="font-family:Helvetica,Arial,sans-serif;background:#eef1f4">
<tr><td align="center" style="padding:24px 12px">
<table width="620" cellpadding="0" cellspacing="0" border="0" style="background:#ffffff">
<tr><td style="background:#002244;padding:18px 28px;color:#ffffff;font-size:19px">
eTicket confirmation</td></tr>
<tr><td style="padding:22px 28px 4px 28px;font-size:13px;color:#4d5a64">
Confirmation code</td></tr>
<tr><td style="padding:0 28px 18px 28px;font-size:26px;letter-spacing:3px;color:#20262b">
{fields["code"]}</td></tr>
<tr><td style="padding:0 28px 6px 28px;font-size:13px;color:#4d5a64">
Traveler {TRAVELER}</td></tr>
<tr><td style="padding:0 28px">
<table width="100%" cellpadding="0" cellspacing="0" border="0">{legs}</table></td></tr>
<tr><td style="padding:14px 28px 0 28px">
<table width="100%" cellpadding="0" cellspacing="0" border="0" style="font-size:14px">
{_rows(fields["lines"], "Ticket total", fields["total"])}
</table></td></tr>
<tr><td style="padding:14px 28px 6px 28px;font-size:13px;color:#20262b">
Ticket billed to the {COMPANY} corporate card ending 8802</td></tr>
<tr><td style="padding:0 28px 22px 28px;font-size:13px;color:#20262b">
Checked bag {money(fields["bag"])} billed to Visa ending {CARD_LAST4}</td></tr>
</table></td></tr></table></body></html>
"""


def render_uber(fields: dict) -> str:
    """An Uber style receipt for a Lime scooter unlock and ride."""
    return f"""<html><body style="margin:0;padding:0;background:#ffffff">
<table width="100%" cellpadding="0" cellspacing="0" border="0"
 style="font-family:Helvetica,Arial,sans-serif">
<tr><td align="center" style="padding:24px 12px">
<table width="560" cellpadding="0" cellspacing="0" border="0">
<tr><td style="background:#000000;padding:16px 24px;color:#ffffff;font-size:18px">
Uber</td></tr>
<tr><td style="padding:22px 24px 4px 24px;font-size:20px;color:#000000">
Total {money(fields["total"])}</td></tr>
<tr><td style="padding:0 24px 18px 24px;font-size:13px;color:#6b6b6b">
Lime scooter on {fields["when"]}</td></tr>
<tr><td style="padding:0 24px">
<table width="100%" cellpadding="0" cellspacing="0" border="0" style="font-size:14px">
{_rows(fields["lines"], "Charged", fields["total"])}
</table></td></tr>
<tr><td style="padding:14px 24px;font-size:13px;color:#000000">
Visa &bull;&bull;&bull;&bull; {CARD_LAST4}</td></tr>
<tr><td style="padding:0 24px 22px 24px;font-size:13px;color:#6b6b6b">
{fields["start"]} to {fields["end"]}, {fields["minutes"]} minutes</td></tr>
</table></td></tr></table></body></html>
"""


def render_plaintext(fields: dict) -> str:
    """A hotel confirmation as a plain text mail body with headers."""
    return f"""From: reservations@harborview.example
Date: {fields["sent"]}
Subject: Your {HOTEL} stay is confirmed

{HOTEL}
Confirmation {fields["code"]}

Guest        {TRAVELER}
Check in     {fields["checkin"]}
Check out    {fields["checkout"]}
Room         King, 2 nights

Room rate    {money(fields["rate"])} per night
Estimated tax {money(fields["tax"])}
Facility fee {money(fields["fee"])} for the stay, payable at the front desk

Card on file Visa ending {CARD_LAST4}

Questions? Reply to this message and the front desk will pick it up.
"""


# -------------------------------------------------------------------- fixture


def _sample_fields(rng: random.Random) -> dict:
    """Every number the fixture needs, drawn once from the seeded generator."""
    fares = [round(rng.uniform(18.0, 46.0), 2) for _ in range(4)]
    tips = [round(rng.uniform(2.0, 7.0), 2) for _ in range(4)]
    airport_fee = round(rng.uniform(2.5, 4.5), 2)
    return {
        "fares": fares,
        "tips": tips,
        "airport_fee": airport_fee,
        "bag": 40.00,
        "base_fare": round(rng.uniform(410.0, 620.0), 2),
        "scooter_unlock": 1.00,
        "scooter_ride": round(rng.uniform(3.0, 9.0), 2),
        "dd_food": [round(rng.uniform(12.0, 28.0), 2) for _ in range(4)],
        "dd_fees": round(rng.uniform(4.0, 9.0), 2),
        "dd_tip": round(rng.uniform(3.0, 8.0), 2),
        "refund": round(rng.uniform(8.0, 14.0), 2),
        "room_rate": round(rng.uniform(159.0, 239.0), 2),
        "room_tax": round(rng.uniform(38.0, 62.0), 2),
        "facility_fee": round(rng.uniform(24.0, 44.0), 2),
        "transit": round(rng.uniform(4.0, 9.0), 2),
        "stipend": 75.00,
        "code": "".join(rng.choice("ABCDEFGHJKLMNPQRSTUVWXYZ") for _ in range(6)),
    }


def _ride(fields: dict, index: int, pickup: str, dropoff: str, when: str, airport: bool) -> dict:
    fare = fields["fares"][index]
    tip = fields["tips"][index]
    lines = [("Lyft fare", fare)]
    if airport:
        lines.append(("Airport fee", fields["airport_fee"]))
    lines.append(("Tip", tip))
    claim = round(fare + (fields["airport_fee"] if airport else 0.0), 2)
    return {
        "driver": DRIVERS[index],
        "when": when,
        "pickup": pickup,
        "dropoff": dropoff,
        "pickup_time": "7:42 AM" if index % 2 == 0 else "6:05 PM",
        "dropoff_time": "8:11 AM" if index % 2 == 0 else "6:28 PM",
        "lines": lines,
        "total": round(claim + tip, 2),
        "claim": claim,
        "track": f"{index}f2a9",
    }


def _build(seed: int) -> tuple[dict, dict]:
    """Return (expense_data, rendered files keyed by file name)."""
    rng = random.Random(seed)
    f = _sample_fields(rng)

    rides = [
        _ride(f, 0, "Home", "Airport", "Monday, June 8, 2026", True),
        _ride(f, 1, "Airport", HOTEL, "Monday, June 8, 2026", True),
        _ride(f, 2, HOTEL, OFFICE, "Tuesday, June 9, 2026", False),
        _ride(f, 3, HOTEL, "Airport", "Wednesday, June 10, 2026", True),
    ]

    dd_lines_1 = [
        ("Subtotal", round(f["dd_food"][0] + f["dd_food"][1], 2)),
        ("Fees and estimated tax", f["dd_fees"]),
        ("Dasher tip", f["dd_tip"]),
    ]
    dd1_total = round(sum(a for _, a in dd_lines_1), 2)
    dd1_claim = round(dd1_total - f["dd_tip"], 2)

    dd_lines_2 = [
        ("Subtotal", round(f["dd_food"][2] + f["dd_food"][3], 2)),
        ("Fees and estimated tax", f["dd_fees"]),
        ("Dasher tip", f["dd_tip"]),
        ("Refund, item out of stock", -f["refund"]),
    ]
    dd2_total = round(sum(a for _, a in dd_lines_2), 2)
    dd2_claim = round(dd2_total - f["dd_tip"], 2)

    scooter_total = round(f["scooter_unlock"] + f["scooter_ride"], 2)
    room_and_tax = round(f["room_rate"] * 2 + f["room_tax"], 2)
    ticket_total = round(f["base_fare"] + f["bag"], 2)

    files: dict[str, str | bytes] = {}
    files[f"{RIDS['lyft1']}.html"] = render_lyft(rides[0])
    files[f"{RIDS['lyft2']}.html"] = render_lyft(rides[1])
    files[f"{RIDS['lyft3']}.html"] = render_lyft(rides[2])
    files[f"{RIDS['lyft4']}.html"] = render_lyft(rides[3])
    files[f"{RIDS['dd1']}.html"] = render_doordash(
        {
            "store": "Cedar Street Noodle Bar",
            "when": "Tuesday, June 9, 2026",
            "items": [
                (1, "Spicy dan dan noodles", f["dd_food"][0]),
                (1, "Cucumber salad", f["dd_food"][1]),
            ],
            "lines": dd_lines_1,
            "total": dd1_total,
            "track": "7c31",
        }
    )
    files[f"{RIDS['dd2']}.html"] = render_doordash(
        {
            "store": "Lantern Row Kitchen",
            "when": "Monday, June 8, 2026",
            "items": [
                (1, "Roast chicken plate", f["dd_food"][2]),
                (1, "Side of greens", f["dd_food"][3]),
            ],
            "lines": dd_lines_2,
            "total": dd2_total,
            "track": "9b84",
        }
    )
    files[f"{RIDS['flight']}.html"] = render_united(
        {
            "code": f["code"],
            "legs": [
                ("UA 1417", "AUS to SEA", "Mon, June 8, 2026"),
                ("UA 2260", "SEA to AUS", "Wed, June 10, 2026"),
            ],
            "lines": [("Base fare and taxes", f["base_fare"]), ("Checked bag", f["bag"])],
            "total": ticket_total,
            "bag": f["bag"],
        }
    )
    files[f"{RIDS['scooter']}.html"] = render_uber(
        {
            "when": "Tuesday, June 9, 2026",
            "lines": [("Unlock", f["scooter_unlock"]), ("Ride time", f["scooter_ride"])],
            "total": scooter_total,
            "start": OFFICE,
            "end": HOTEL,
            "minutes": 11,
        }
    )
    files[f"{RIDS['hotel_conf']}.txt"] = render_plaintext(
        {
            "sent": "Thu, 4 Jun 2026 09:12:00 -0700",
            "code": f["code"][::-1],
            "checkin": "Monday, June 8, 2026",
            "checkout": "Wednesday, June 10, 2026",
            "rate": f["room_rate"],
            "tax": f["room_tax"],
            "fee": f["facility_fee"],
        }
    )
    files[f"{RIDS['folio']}.pdf"] = pdf_bytes(
        [
            [
                f"{HOTEL} folio page 1 of 2",
                f"Guest {TRAVELER}",
                "Night 1, Monday June 8, 2026",
                f"Room {money(f['room_rate'])}",
                f"Room tax {money(round(f['room_tax'] / 2, 2))}",
            ],
            [
                f"{HOTEL} folio page 2 of 2",
                f"Guest {TRAVELER}",
                "Night 2, Tuesday June 9, 2026",
                f"Room {money(f['room_rate'])}",
                f"Room tax {money(round(f['room_tax'] / 2, 2))}",
                f"Room and tax total {money(room_and_tax)}",
                f"Charged to Visa ending {CARD_LAST4}",
            ],
        ]
    )
    files[f"{RIDS['transit']}.png"] = png_bytes(200, 80, (28, 92, 148))

    data = {
        "company": COMPANY,
        "trip": TRIP,
        "traveler": TRAVELER,
        "currency": "USD",
        "days": [
            {
                "label": "Monday, June 8, travel out",
                "items": [
                    {
                        "desc": "Ride, home to airport",
                        "amt": rides[0]["claim"],
                        "rid": RIDS["lyft1"],
                    },
                    {"desc": "Checked bag, outbound", "amt": f["bag"], "rid": RIDS["flight"]},
                    {
                        "desc": "Ride, airport to hotel",
                        "amt": rides[1]["claim"],
                        "rid": RIDS["lyft2"],
                    },
                    {"desc": "Dinner, refund netted", "amt": dd2_claim, "rid": RIDS["dd2"]},
                ],
            },
            {
                "label": "Tuesday, June 9, working day",
                "items": [
                    {
                        "desc": "Ride, hotel to office",
                        "amt": rides[2]["claim"],
                        "rid": RIDS["lyft3"],
                    },
                    {"desc": "Transit day pass", "amt": f["transit"], "rid": RIDS["transit"]},
                    {
                        "desc": "Scooter, office to hotel",
                        "amt": scooter_total,
                        "rid": RIDS["scooter"],
                    },
                    {"desc": "Dinner", "amt": dd1_claim, "rid": RIDS["dd1"]},
                    {"desc": "Room and tax, 2 nights", "amt": room_and_tax, "rid": RIDS["folio"]},
                    {
                        "desc": "Mandatory hotel fee",
                        "amt": f["facility_fee"],
                        "rid": RIDS["hotel_conf"],
                    },
                ],
            },
            {
                "label": "Wednesday, June 10, personal day, airport leg kept",
                "items": [
                    {
                        "desc": "Ride, hotel to airport",
                        "amt": rides[3]["claim"],
                        "rid": RIDS["lyft4"],
                    },
                ],
            },
        ],
        "receipts": [
            {"rid": RIDS["flight"], "title": "eTicket, AUS to SEA to AUS", "vendor": "United"},
            {"rid": RIDS["lyft1"], "title": "Ride, home to airport", "vendor": "Lyft"},
            {"rid": RIDS["lyft2"], "title": "Ride, airport to hotel", "vendor": "Lyft"},
            {"rid": RIDS["hotel_conf"], "title": "Stay confirmation", "vendor": HOTEL},
            {"rid": RIDS["lyft3"], "title": "Ride, hotel to office", "vendor": "Lyft"},
            {"rid": RIDS["scooter"], "title": "Lime scooter, office to hotel", "vendor": "Uber"},
            {"rid": RIDS["dd1"], "title": "Order, dinner", "vendor": "DoorDash"},
            {"rid": RIDS["transit"], "title": "Day pass, photo receipt", "vendor": "City transit"},
            {"rid": RIDS["dd2"], "title": "Order, dinner with refund", "vendor": "DoorDash"},
            {"rid": RIDS["folio"], "title": "Folio, 2 nights", "vendor": HOTEL},
            {"rid": RIDS["lyft4"], "title": "Ride, hotel to airport", "vendor": "Lyft"},
        ],
        "stipend": {"desc": "Meal stipend, 1 day worked", "amt": f["stipend"]},
    }
    return data, files


def make(out_dir: Path, seed: int = 1) -> Path:
    """Write expense_data.json and receipts/ under out_dir. Returns the json path."""
    out_dir = Path(out_dir)
    receipts = out_dir / "receipts"
    receipts.mkdir(parents=True, exist_ok=True)
    data, files = _build(seed)
    for name, body in files.items():
        target = receipts / name
        if isinstance(body, bytes):
            target.write_bytes(body)
        else:
            target.write_text(body, encoding="utf-8")
    path = out_dir / "expense_data.json"
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return path
