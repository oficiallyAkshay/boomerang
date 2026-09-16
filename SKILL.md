---
name: boomerang
description: "Builds a reimbursement packet from a personal inbox: finds trip receipts, splits company-paid from self-paid, applies policy, outputs one PDF. Use for expense claims and travel reimbursement."
license: MIT
metadata: {icon: "🪃"}
compatibility: "Python 3.11+; pip install -r requirements.txt; Chrome or Edge on the machine for the PDF (else print the HTML); an email tool in the host or the bundled Gmail fallback."
---

# Boomerang

Someone spent their own money on a company's behalf and now has to claim it
back. Boomerang finds the trip receipts in their mailbox, decides which lines
the company owes, and builds one packet: a summary table, then the original
receipts, one per page. Nothing is sent anywhere; the user reviews and sends it
themselves.

## The split between code and judgment

The rule is simple. If a wrong answer costs money or credibility, it belongs in
a script. If a wrong answer costs one clarifying question, it belongs to you.

Code owns:

- Gmail query construction and windowing
- One-at-a-time fetch to disk
- Vendor email cleaning and image inlining
- The card fingerprint check
- Day tables, subtotals, and totals
- The PDF render and page count check

You own:

- Identifying the trip and setting the claimable window
- Classifying rides by endpoint
- Flagging anomalies
- Wording line descriptions

Never recompute a total by hand. Never eyeball a card fingerprint. Run the
script and read its output.

## Capability contract

Before anything else, run `python scripts/doctor.py` (or `uv run python
scripts/doctor.py`); if it reports something missing, run the command it
prints, with the user's consent, then continue. Never install a browser
download without asking.

Boomerang needs five capabilities. Check which ones the host gives you before
starting, and say plainly which are missing.

| Capability | On a connector host | With the bundled fallback |
| --- | --- | --- |
| search-email | The host's email search tool | `scripts/gmail_cli.py search QUERY` |
| read-email | The host's message read tool | `scripts/gmail_cli.py get RID --out DIR` |
| search-calendar | The host's calendar tool | Ask the user for the onsite dates |
| write-file | The host's file write tool | Shell redirection |
| run-python | The host's shell or code tool | Required; there is no substitute |

Commands below use the uv form, `uv run python scripts/<name>.py`. Without uv,
run `python scripts/<name>.py` after `pip install -r requirements.txt`; the
behaviour is identical. The render drives whichever of Google Chrome, Microsoft
Edge or a Playwright Chromium the machine has, in that order. Claude.ai and
Cowork have no browser at all, so there the deliverable is `packet.html`,
printed to PDF by the user: skip the page-count and splice steps below, and
list folio attachments as separate files.

Notes on the fallback:

- `gmail_cli.py auth --client-secret PATH` runs the OAuth flow once. The token
  lands at `~/.config/boomerang/token.json` with mode 600.
- The fallback is the only path that can download attachments. Hotel folios
  usually arrive as PDF attachments, and email connectors cannot fetch them.
- The fallback needs a host with a local browser and a loopback port for the
  OAuth flow: Claude Code, Cursor, or a local OpenClaw or Hermes. Anywhere
  else, use the host's email connector and ask for the folios as uploads.
- Prerequisite, done before the run: `uv sync --extra gmail`, or without uv
  `pip install google-api-python-client google-auth-oauthlib`.

Headroom is optional everywhere, and a host already running behind `headroom
proxy` or `headroom wrap` needs nothing else.

One host behavior matters more than the rest. An unapproved tool call can fail
silently: it is declined, nothing is returned, and the session carries on as if
the step had run. If a search or calendar call comes back empty or malformed,
retry it once, then tell the user what you tried and ask them to approve it.

## Workflow

Run these nine steps in order, except where one says otherwise. Show your work
at step 6 and stop there for correction.

### 1. Find the trip

Read the calendar for the onsite or working dates. Take the travel window from
the **final** eTicket, not the first one: itineraries get rebooked, so the
first eTicket has the wrong dates, though it is still the right place to read
who paid. If there is no calendar, ask for the onsite dates and nothing else.

### 2. Search the mailbox in two passes

Pass one is date-windowed generic terms: receipt, confirmation, your ride, your
order, eTicket. Pass two is one query per vendor: Lyft, Uber, DoorDash, United,
Delta, hotel chains, Hotels.com, citizenM, Lime.

Pad the window one day on each side: ride receipts arrive six to twenty hours
after the ride, and the airport legs get missed otherwise. The two passes ask
one mailbox different questions, so `fetch.py` builds both from the vendor
rules, does the padding, runs every query at once, and orders the results by
query rather than by whichever answered first:

```bash
uv run python scripts/fetch.py --start 2026-06-11 --end 2026-06-16 \
  --out receipts --vendors vendors
```

### 3. Fetch the bodies to disk, never into context

`fetch.py` writes each message as `<rid>.html`, `<rid>.txt`, `<rid>.meta.json`
and, for the first PDF attachment, `<rid>.pdf`, skipping what is already on
disk. Bodies come down three at a time (`--workers N` changes that), each
written by its own worker. `fetch_all` returns four lists in first seen order:
written, rejected, skipped, failed. Say the failed list out loud: those were
never fetched, and a packet built without them is short an unmentioned receipt.

One at a time is a rule about your context, not about the script. Read values
from the plaintext body. Open one full HTML per vendor, to learn that vendor's
layout, and no more. A single vendor email is 60 to 125 KB of tracking links.
Never load many raw emails into context. When the optional Headroom package is
installed, pass `--compact` and read values from `<rid>.compact.txt`, the same
body with the boilerplate squeezed out. The full body stays on disk and the
packet is still built from it.

### 4. Work out who paid

Two signals, both mechanical. The card fingerprint: a last-4 that appears on
exactly one receipt in the whole mailbox belongs to someone else.

```bash
uv run python scripts/cards.py receipts
```

The eTicket chain: read it oldest first. The phrase "previous ticket value
applied" means the base fare was paid earlier, by someone else. Restated, the
final eTicket settles the dates and the first eTicket settles who paid.

### 5. Apply the policy

Read `policy.md`. Then read `policy.local.md` if it exists, and let its lines
override the shipped defaults. The local file is gitignored, so it is where a
user keeps their own or their company's wording. If the two disagree, the
local file wins; mark the line as a local override in the candidate list. Copy
`references/policy.local.example.md` to `policy.local.md` at the skill root.

Stage 5 runs three things at once. Cleaning every receipt, the step 4 card
fingerprint, and reading the folio text all work on what is already on disk
and need nothing from each other, so start them together:

```bash
uv run python scripts/clean.py --dir receipts --out clean \
  --vendors vendors --images .image-cache --fetch-images
```

`--dir` cleans four at a time (`--workers N` changes that), carries the `.txt`,
`.pdf`, `.png` and `.jpg` receipts across untouched, and prints its warnings in
filename order; the one-receipt form still corrects a single file.
`--fetch-images` is the only step that opens a socket, and it downloads from
the cleaned fragment, so the pixels the cleaner just removed are never asked
for. `.image-cache` is gitignored: a working directory, not part of the packet.

The cleaner reads the vendor from the `<rid>.meta.json` saved beside each
message and prints the one it chose, so `--vendor` is for correcting it, and
`--vendor generic` forces the generic clean.

Build the candidate list with the defaults applied, and put the arithmetic in
the data, not your head: a refunded line carries `amt` and `refund`, a stipend
`rate` and `days`, a line paid abroad `local_amt` and `local_currency`, and
`build.py` sums them. Give every meal an `at`, so two from one slot are caught.

### 6. Show the candidate list, then correct it

Print the full candidate list before you build anything: every day, line and
amount, and the running total. Mark each default you applied so the user sees
it without asking. Then ask the standing question in the next section and wait.

### 7. Build, render, splice

The work narrows here: the build waits on the cleaned directory, the splice on
the render. Write `expense_data.json`, then render.

```bash
uv run python scripts/build.py expense_data.json --receipts clean \
  --out packet.html
```

Build from `clean`, the folder step 5 writes, never from `receipts`: a raw
vendor email still carries its tracking pixels, its live links and whatever
else was in the markup, and building from it puts all of that in the packet.

The schema is in `references/interfaces.md`. `build.py` validates before it
writes, and every problem it finds is a real problem: fix the data, not the
validator. The HTML is the master; re-render the PDF after every change.

```bash
uv run python scripts/render_pdf.py packet.html packet.pdf --expect 12
```

`build.py` prints `pages expected N`, the number `--expect` takes, and names on
stderr any line whose claimed value is not printed on its own receipt. Explain
every name: a hand-typed value, a netted refund and a receipt still to come are
three different answers. `render_pdf.py` exits 2 when `--expect` differs.

If any receipt is a PDF attachment, splice its pages in afterwards, so the
attachment sits behind its card page. The folio is `receipts/<rid>.pdf`:

```bash
uv run python scripts/attach_pdf.py packet.pdf expense_data.json \
  --receipts receipts --out packet_final.pdf
```

### 8. Restate the totals in every reply

Totals drift with each revision. After any change, however small, restate the
day subtotals, the expense total, the stipend if there is one, and the grand
total. Take them from `build.py`, not from memory.

### 9. Sweep once more the day before sending

Late receipts are the most common miss. Re-run the two-pass search the day
before the user sends the packet, with the same padded window. Add anything
new, rebuild, and restate the totals.

## Questions

Ask nothing up front. Reconstruct the trip, apply the defaults, show the list.

**Inferred silently**, with no question asked: who booked the flight and the
hotel; the trip window; personal days, meaning any day in the window with no
work and no travel; ride classification by endpoint; duplicate meals; meals the
company provided.

**Defaults shown in the list, not asked**: upgrades out; miles out; tips out;
deposits out; personal-day nights and rides out with the airport legs kept;
flight credits in.

**One standing question**, asked once, after the list:

> Anything you paid for outside this inbox, by cash, or without an email
> receipt?

It catches app-only receipts, a second mailbox, cash tolls, and parking.

**Ask only on real ambiguity.** There are three cases:

1. A ride on a working day that touches none of the four endpoints.
2. A change fee whose cause is unclear.
3. A stipend the user mentioned that no email confirms.

Raise any of these in the same message as the standing question at step 6,
never before the list is shown. Anything else, apply the default and show it.

## Receipt rendering and what gets stripped

Each receipt is rendered from the vendor's own email markup. Values, fonts,
styles, and logos are the vendor's. The result looks like what the vendor sent,
because it is.

Removed before rendering: the marketing, meaning tracking links and pixels, tip
and rating controls, promotional modules, app-download banners, hero images and
social footers; and everything that could act, meaning scripts, inline
handlers, live URL schemes, and every element that can load a second document.
Never altered: amounts, taxes and fees, dates and times, line items, addresses
printed inside the receipt, and the vendor's own logos and fonts.

Say this plainly to the user the first time you show them a rendered receipt.
The full list of both is in `references/vendors.md`, and the machine-readable
form is `vendors/<name>/rules.json`.

Two more rendering rules. Images are inlined as base64, or they break offline
and in the PDF. Vendor CSS is scoped to the receipt container, so two vendors
on one page do not fight.

Plaintext confirmations, which is how most hotel emails arrive, render as a
monospace block with From, Date and Subject headers above the body.

### PDF attachments

On a connector host, you cannot fetch attachments. Ask the user to upload the
folio, and say why. With the Gmail fallback, `fetch.py` writes attachments
beside the message as `<rid>.<n>.<ext>`.
Either way, list the receipt in `expense_data.json` with `"kind": "pdf"`.
`build.py` renders a card saying the attachment is embedded, and `attach_pdf.py`
splices the real pages in behind it.

To read a folio's values, use `attach_pdf.extract_text`, which returns the whole
PDF as text with the pages joined by form feeds. Read the numbers from that
rather than from a screenshot or a guess.

## Packet format, non-negotiable

Verify every line of this before you send the packet back to the user.

- [ ] One top-down table: day header, items, day subtotal; then Expenses,
      Stipend, Total. No side-by-side totals block
- [ ] No notes section, no Attn or Submitted by, no exclusions list, no Gmail
      message ids, no receipt anchor links
- [ ] Line descriptions say Hotel, Office, Airport, Home. Street addresses stay
      inside the vendor receipts
- [ ] No em dashes anywhere
- [ ] One receipt per PDF page
- [ ] The HTML is the master and the PDF was re-rendered after the last change

Run the gate on the built packet:

```bash
uv run python scripts/check_prose.py --packet packet.html
```

## Known traps

Each of these has cost a real packet a correction. Treat them as rules.

- **Do not** read who paid from the last eTicket. Dates come from the last one,
  who paid comes from the first one.
- **Do not** end the search window at hotel checkout. The return airport ride
  lands after it. Pad one day on each side.
- **Do not** add a notes section or a side-by-side totals block. One top-down
  table, nothing beside it.
- **Do not** run a single search pass. Two passes, generic then per vendor, or
  a return-leg ride goes missing.
- **Do not** claim a ride to an address that is not home, the airport, the
  hotel, or the office. A plausible-looking destination on a working day is
  still personal unless it touches one of the four.
- **Do not** claim two meals from the same slot. Duplicates within an hour:
  keep the first, flag the second.

## Out of scope

Boomerang does not do these, and should say so rather than improvise:

- Drafting or sending the reply email
- Multi-city trips, which run as separate packets
- Companions on the trip
- Company payment portals
- Recruiter-specific claim formats
- The tax treatment of stipends
- Mileage
- Virtual interviews
