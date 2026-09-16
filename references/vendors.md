# Vendor rendering and what is removed

Every receipt in a packet is rendered from the vendor's own email markup. The
vendor's fonts, colors, layout and logo survive, so a Lyft receipt still looks
like Lyft and a United eTicket still looks like United. What gets removed is
the part of a marketing email that is not the receipt.

The same two lists apply to every vendor on this page.

**Removed:** tracking links, tracking pixels, tip buttons and rating controls,
promotional modules, app-download banners, hero images, social footers, and
scripts.

**Never altered:** amounts, taxes and fees, dates and times, line items,
addresses printed inside the receipt, and the vendor's own logos and fonts. No
value in a receipt is ever recomputed, rounded, or reworded.

Two mechanical notes. Images are inlined as base64, so a packet renders the
same offline and in the PDF, with no request going back to the vendor. Vendor
CSS is scoped to the receipt container, so two vendors sitting on one page do
not overwrite each other's styles.

Each vendor below has a machine-readable form at `vendors/<name>/rules.json`.
The `notes` field there must agree with the paragraph here. If they disagree,
one of them is wrong and the pull request that changes either should change
both.

## Lyft

A Lyft ride receipt arrives as a styled HTML email built around a fare table.
The fare table, the pickup and dropoff addresses, the ride time, and every
charge line are kept exactly as sent. Removed: the tip prompt and its buttons,
the rate-your-driver controls, the promotional footer offering credit for
referrals, the map hero image, the app-store badges, and the tracking pixel at
the end of the body. Link wrappers on the remaining text are unwrapped so the
receipt reads as text rather than as a page of redirects.

## DoorDash

A DoorDash order receipt carries the itemised order, the subtotal, taxes and
fees, the tip line if one was added, and the order total. All of those stay.
Removed: the reorder and rate-your-order buttons, the promotional carousel of
other restaurants, the app-download module, the large header image, the social
icons, and tracking links on every remaining anchor. The tip line is kept
visible in the receipt even though policy leaves tips out of the claim, so the
receipt and the claimed amount can be reconciled by eye.

## United

United sends two things worth rendering: the eTicket confirmation and the
in-flight Wi-Fi receipt. On the eTicket, the flight segments, the fare
breakdown, the taxes, the ticket number, and the form of payment are kept; the
form of payment matters because it is what settles who paid. Removed: seat
upgrade offers, MileagePlus promotional blocks, the app-download banner, the
partner advertising footer, and the tracking pixels. Wi-Fi receipts are short
and keep the charge, the date, and the flight reference.

## Uber

Uber covers both rides and Lime scooter rentals, which arrive in the same
receipt shell. The trip or rental summary, the fare breakdown, the surcharge
lines, the time, and the endpoints are kept. Removed: the tip prompt, the
rating widget, the promotional module offering a discount on the next trip, the
map hero image, the app-store badges, and the tracking pixel. Scooter rentals
keep the unlock fee and the per-minute lines as separate rows, because policy
claims them as one ground transport line and the split has to stay visible.

## Plaintext

Most hotel confirmations, including Hotels.com and citizenM, arrive as
plaintext. There is no markup to strip. The body is rendered in a monospace
block under a small header carrying From, Date and Subject, so the reader can
see who sent it and when. The body text is reproduced verbatim, including the
folio breakdown and any line about fees payable at the property. Long lines are
allowed to wrap rather than being reflowed, so nothing shifts column.

## Generic vendor

A vendor with no entry in `vendors/` still renders. The cleaner applies the
shared rules only: scripts and tracking pixels go, links that match the generic
tracking patterns are unwrapped, images are inlined, and the vendor's CSS is
scoped to the receipt container. Nothing vendor-specific is guessed at, so a
promotional block the shared rules do not recognise will still appear in the
packet. That is the intended failure: an unrecognised block is visible and can
be reported as a missing vendor entry, rather than silently removing something
that turned out to be part of the receipt.
