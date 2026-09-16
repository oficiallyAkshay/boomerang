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
receipts, one per page. Nothing is sent anywhere; the user sends it themselves.

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

Before anything else, run `python scripts/doctor.py`; if it reports something
missing, run the command it prints, with the user's consent, then continue, and
never install a browser download without asking. Boomerang needs five
capabilities: check which ones the host gives you, and say plainly which are
missing.

| Capability | On a connector host | With the bundled fallback |
| --- | --- | --- |
| search-email | The host's email search tool | `scripts/gmail_cli.py search QUERY` |
| read-email | The host's message read tool | `scripts/gmail_cli.py get RID --out DIR` |
| search-calendar | The host's calendar tool | Ask the user for the onsite dates |
| write-file | The host's file write tool | Shell redirection |
| run-python | The host's shell or code tool | Required; there is no substitute |

A host running behind Headroom's proxy or wrapper compresses everything the
model reads, this skill included, and needs nothing else from you:
<https://github.com/headroomlabs-ai/headroom>.

Commands below use the uv form. Without uv, run `python scripts/<name>.py`
after `pip install -r requirements.txt`. The render drives whichever of Google
Chrome, Microsoft Edge or a Playwright Chromium the machine has. Claude.ai and
Cowork have no browser at all, so there the deliverable is `packet.html`,
printed to PDF by the user: skip the page-count and splice steps below, and
list folio attachments as separate files.

Notes on the fallback:

- `gmail_cli.py auth --client-secret PATH` runs the OAuth flow once. The token
  lands at `~/.config/boomerang/token.json` with mode 600.
- The fallback needs a host with a local browser and a loopback port for the
  OAuth flow: Claude Code, Cursor, or a local OpenClaw or Hermes. Anywhere
  else, use the host's email connector and ask for the folios as uploads.
- Prerequisite, done before the run: `uv sync --extra gmail`, or without uv
  `pip install google-api-python-client google-auth-oauthlib`.

One host behavior matters more than the rest. An unapproved tool call can fail
silently: it is declined, nothing comes back, and the session carries on as if
the step had run. If a search or calendar call comes back empty or malformed,
retry once, then say what you tried and ask the user to approve it.

## Workflow

Run these nine steps in order, except where one says otherwise. Show your work
at step 6 and stop there for correction.

### 1. Find the trip

Read the calendar for the onsite or working dates. Take the travel window from
the **final** eTicket, not the first one: itineraries get rebooked, so the first
eTicket has the wrong dates. If there is no calendar, ask for the onsite dates
and nothing else.

### 2. Search the mailbox in two passes

Pass one is date-windowed generic terms: receipt, confirmation, your ride, your
order, eTicket. Pass two is one query per vendor: Lyft, Uber, DoorDash, United,
Delta, hotel chains, Hotels.com, citizenM, Lime.

`fetch.py` builds both passes from the vendor rules, runs every query at once,
and orders results by query, not by whichever answered first. Pass one is padded
a day each side, because ride receipts arrive six to twenty hours late; pass two
is padded by each vendor's own lag. Read the vendor knowledge first, with
`--knowledge` and no window: every folder's lag, which subject is the money
record, what its tenders and charge lines mean, and what its receipt omits.

```bash
uv run python scripts/fetch.py --start 2026-06-11 --end 2026-06-16 \
  --out receipts --vendors vendors
```

### 3. Fetch the bodies to disk, never into context

`fetch.py` writes each message as `<rid>.html`, `<rid>.txt` and
`<rid>.meta.json`, and the first attachment of each kind as `<rid>.pdf`,
`<rid>.png` or `<rid>.jpg`, skipping what is already on disk. Bodies come down
three at a time (`--workers N` changes that), each written by its own worker.
`fetch_all` returns four lists in first seen order: written, rejected, skipped,
failed. Say the failed list out loud: those were never fetched, and a packet
built without them is short an unmentioned receipt.

One at a time is a rule about your context, not about the script. Read values
from the plaintext body. Open one full HTML per vendor, to learn that vendor's
layout, and no more. A single vendor email is 60 to 125 KB of tracking links.

### 4. Work out who paid

Two signals, both mechanical. The card fingerprint: a last-4 that appears on
exactly one receipt in the whole mailbox belongs to someone else.

```bash
uv run python scripts/cards.py receipts --vendors vendors
```

The eTicket chain: read it oldest first. The phrase "previous ticket value
applied" means the base fare was paid earlier, by someone else. Step 2's
knowledge names each vendor's tenders, so a points, wallet or previous_ticket
row did not settle in cash, and `--vendors` reads each vendor's own card line.

### 5. Apply the policy

Read `policy.md`. Then read `policy.local.md` if it exists. The local file is
gitignored, so it is where a user keeps their own or their company's wording:
where the two disagree it wins, and the candidate list marks that line as a
local override. Copy `references/policy.local.example.md` to `policy.local.md`
at the skill root.

Stage 5 runs three things at once. Cleaning every receipt, the step 4 card
fingerprint and reading the folio text all work on what is already on disk and
need nothing from each other, so start them together:

```bash
uv run python scripts/clean.py --dir receipts --out clean \
  --vendors vendors --images .image-cache --fetch-images
```

`--dir` cleans four at a time, carries the `.txt`, `.pdf`, `.png` and `.jpg`
receipts across untouched, and prints its warnings in filename order; the
one-receipt form still works. `--fetch-images` is the only step that opens a
socket, and it downloads from the cleaned fragment, so a removed pixel is never
asked for. `.image-cache` is gitignored: a working directory, not the packet.

The cleaner reads the vendor from the `<rid>.meta.json` saved beside each
message and prints the one it chose, so `--vendor` is for correcting it, and
`--vendor generic` forces the generic clean.

Build the candidate list with the defaults applied, and put the arithmetic in
the data, not your head: a refunded line carries `amt` and `refund`, a stipend
`rate` and `days`, a line paid abroad `local_amt` and `local_currency`, and
`build.py` sums them. Give every meal an `at`, so two from one slot are caught.
Classify each line with its vendor's category from step 2, and read a missing
fact as absent rather than zero: chase it in the folio, not in the confirmation.

### 6. Show the candidate list, then correct it

Print the full candidate list before you build anything: every day, line and
amount, and the running total. Mark each default you applied, then ask the
standing question in the next section and wait.

### 7. Build, render, splice

The build waits on the cleaned directory and the splice on the render. Write
`expense_data.json`, then build.

```bash
uv run python scripts/build.py expense_data.json --receipts clean \
  --out packet.html
```

Build from `clean`, the folder step 5 writes, never from `receipts`: a raw
vendor email carries its tracking pixels and its live links into the packet.

The schema is in `references/interfaces.md`. `build.py` validates before it
writes, and every problem it finds is real: fix the data, not the validator.

```bash
uv run python scripts/render_pdf.py packet.html packet.pdf
```

`build.py` prints `pages expected N`, a floor: every receipt starts on its own
page, and a long receipt continues onto the next page rather than shrinking
below readable size. Read the render's count and its `receipt N spans` lines,
then pass that count back as `--expect`, which exits 2 when it differs. Explain
every line `build.py` names on stderr as claiming what its receipt does not
print: a hand-typed value, a netted refund and a receipt to come all differ.

If any receipt is a PDF attachment, splice its pages in behind it afterwards.
The splice reads `packet.pdf.pages.json`, which the render writes beside the
PDF, so keep the two together. Step 5 copies a folio to `clean/<rid>.pdf`:

```bash
uv run python scripts/attach_pdf.py packet.pdf expense_data.json \
  --receipts clean --out packet_final.pdf
```

### 8. Restate the totals in every reply

Totals drift with each revision. After any change, however small, restate the
day subtotals, the expense total, the stipend if there is one, and the grand
total, taken from `build.py` rather than from memory.

### 9. Sweep once more the day before sending

Late receipts are the most common miss. Re-run the two-pass search the day
before the user sends, with the same padded window, then add anything new,
rebuild, and restate the totals.

## Questions

Ask nothing up front. Reconstruct the trip, apply the defaults, show the list.

**Inferred silently**, never asked: who booked the flight and the hotel; the
trip window; personal days, any day in the window with no work and no travel;
ride classification by endpoint; duplicate meals; meals the company provided.

**Defaults shown in the list, not asked**: upgrades out; miles out; tips out;
deposits out; personal-day nights and rides out with the airport legs kept;
flight credits in; alcohol and groceries flagged, not claimed; on a multi
company trip the flight and the shared nights split evenly; drove instead of
flew means tolls and parking in, no mileage and gas out; a cancelled or
postponed onsite keeps its change fees, non-refundable nights and no-show
charges.

**One standing question**, asked once, after the list:

> Anything you paid for outside this inbox, by cash, or without an email
> receipt?

**Ask only on real ambiguity.** Three cases:

1. A ride on a working day that touches none of the four endpoints.
2. A change fee whose cause is unclear.
3. A stipend the user mentioned that no email confirms.

Raise any of these in the same message as the standing question at step 6,
never before the list is shown. Anything else, apply the default and show it.

## Receipt rendering and what gets stripped

Each receipt is rendered from the vendor's own email markup. Values, fonts,
styles and logos are the vendor's, so the result looks like what the vendor
sent.

Removed before rendering: the marketing, meaning tracking links and pixels, tip
and rating controls, promotional modules, app-download banners, hero images and
social footers; and everything that could act, meaning scripts, inline handlers,
live URL schemes, and every element that can load a second document. Never
altered: amounts, taxes and fees, dates and times, line items, addresses printed
inside the receipt, and the vendor's own logos and fonts. Say this plainly the
first time you show a rendered receipt. The full list of both is in
`references/vendors.md`, the machine-readable form `vendors/<name>/rules.json`.

Three more rendering rules. Images are inlined as base64, or they break offline
and in the PDF. Vendor CSS is scoped to that one receipt's card, so two vendors
on one page do not fight. A promotional or tip module that carries an amount is
kept: the guard that stops a strip pattern carrying a figure away cannot tell a
real amount from an advertised one, so some marketing text survives. Say so.

Plaintext confirmations, which is how most hotel emails arrive, render as a
monospace block with From, Date and Subject headers above the body.

### PDF attachments

On a connector host, you cannot fetch attachments. Ask the user to upload the
folio, and say why. With the Gmail fallback, `fetch.py` names the first
attachment of each kind `<rid>.pdf`, `<rid>.png` or `<rid>.jpg`, and every later
one `<rid>.<n>.<ext>`. A later one cannot be claimed as a line of its own: a rid
holds no dot, so nothing in `expense_data.json` can name that file. Merge a
second folio into the first, or ask the user to attach it by hand.
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
- [ ] Every receipt starts on its own page; a long receipt continues onto the
      next page rather than shrinking below readable size
- [ ] The HTML is the master and the PDF was re-rendered after the last change

Run the gate on the built packet:

```bash
uv run python scripts/check_prose.py --packet packet.html
```

## Known traps

Each of these has cost a real packet a correction. Treat them as rules.

- **Do not** read who paid from the last eTicket. Dates come from the last one,
  who paid from the first.
- **Do not** end the search window at hotel checkout. The return airport ride
  lands after it.
- **Do not** run a single search pass, or a return-leg ride goes missing.
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
