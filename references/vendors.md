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

## What the cleaner always removes

The list above is about marketing. This one is about capability, and it holds
whatever the vendor rules say, because a packet must not be able to act:
scripts, inline handlers, live URL schemes, every element that can load a
second document, and the CSS that can do the same. The list is written out once,
in the docstring at the top of `scripts/clean.py`, with the machine-readable
part in each `vendors/<name>/rules.json`, and `tests/test_clean.py` proves each
line of it with the exact markup a vendor would have to send.

Three mechanical notes. Images are inlined as base64, so a packet renders the
same offline and in the PDF, with no request going back to the vendor. Every
source is read the way a browser reads one, double quoted, single quoted or
bare, so no quoting style leaves a picture pointing back at a vendor, and an
image a rule holds at zero height, which is how a hidden spacer is sized, is
taken as a beacon and removed. Vendor CSS is scoped to that one receipt's card,
so two vendors sitting on one page do not overwrite each other's styles. A
promotional or tip module that prints an amount is kept: the guard that stops a
strip pattern carrying a figure away cannot tell a real amount from an
advertised one, so some marketing text does survive on a receipt.

Each vendor below has a machine-readable form at `vendors/<name>/rules.json`.
The `notes` field there must agree with the paragraph here. If they disagree,
one of them is wrong and the pull request that changes either should change
both. The sample beside each `rules.json` is a real message from that vendor
with every name, address, card number, amount, date, identifier and tracking
URL replaced, and `tests/test_vendors.py` checks each rule against it.

## Lyft

A Lyft ride receipt is a long styled email with a short receipt inside it. Kept
exactly as sent: the fare breakdown with its distance and duration label, every
surcharge and toll row, both tender rows with their Upfront Fare sublabels, the
pickup and dropoff rows with their times and addresses, and the receipt number.
Removed: the Add tip button at the top; the safety marketing module, heading,
shield panel and all; the ride safety summary widget, illustration and all; the
credit card rewards promo; the get help and more module, which is where the Tip
driver button lives; the authorization hold notice; the static route map, whose
URL carries the route geometry, and the OpenStreetMap credit it leaves behind;
the regulatory licence block naming the dispatching base, the vehicle plate and
the driver licence; the copyright and CPUC footer; and the Lyft app instruction
block.

Each of those three modules is matched at the table that wraps the whole thing,
never at the line of copy inside it, because every one of them is a picture
over a label: a pattern that took the label alone left a shield on a panel with
nothing written beside it, and left the help cluster as five rows of an icon
and a chevron with the words gone from between them.

The Lyft logo is the other thing a reader will notice, and it is not a rules
question. The logo URL answers 403 to anything that is not a mail client, so
the fetch records it as unfetchable and the cleaner prints the alt text the
vendor wrote, which is the word lyft. A packet never carries a broken image
icon. The hidden expense microdata
block has a pattern of its own and keeps its place anyway: it repeats the
charged total, and the amount guard skips a pattern that would take an amount
out of the receipt. Lyft's own styling renders the block as nothing, so it
costs the page nothing. Known gap: Lyft prints no Total row at all. The charged total is read out of the hidden microdata block
before the message is cleaned, and the visible total is the sum of the tender
rows, which stay.

## DoorDash

A DoorDash final receipt carries the itemised order, the subtotal, the delivery
and service fees, the tax, the Dasher tip row, any discount, the header total
and the final total charged. All of those stay, along with the weighted item
unit prices, both halves of every substitution pair, the payment line with its
card last four, and the store name. Removed: the open pixel, the marketing hero
illustration, the hidden preheader spacer, the adjustment policy boilerplate,
the Get Order Help button, and the footer table with its corporate address,
privacy link and help centre link. The rules also carry patterns for the track
order button and the hero image row of the confirmation variant, which this
sample does not have. The tip line is kept visible even though policy leaves
tips out of the claim, so the receipt and the claimed amount can be reconciled
by eye. Known gap: this template prints no date anywhere, so a DoorDash line
takes its date from the message headers.

## United

United sends two things worth rendering, and both are in this folder: the
eTicket itinerary and receipt, and the inflight Wi-Fi receipt, which use the
same HTML shell. Kept: the confirmation code, the passenger line, every flight
leg with its date and times, the fare, tax, security fee and facility charge
breakdown, the per passenger total and the ticket total, the eTicket number,
the Wi-Fi reference number and its charge, the method of payment line, and the
previous ticket value line, which is what settles who paid. Removed from the
eTicket: the MileagePlus accrual table and its earning notice, the wall of
legal boilerplate, and the Travel Ready Center promo strip. Removed from the Wi-Fi receipt: the survey block, the additional
information cross-sell, and the refund boilerplate. Removed from both: the Star
Alliance footer banner, the privacy and legal footer links, and the hidden
rows. The baggage allowance table has a pattern here too, and the table stays:
it prints 0.00 USD twice for the two free bags, those are amounts, and a
pattern that would take an amount with it is skipped and named on stderr.

## Uber

An Uber ride receipt keeps the total, every fare line item including
surcharges and promotions, the trip date and clock times, the pickup and
dropoff rows, the product tier, and the payment rows with the card last four
and the charge posting time. Removed: the app download and Download PDF
buttons; the static map image, whose query string carries the pickup and
dropoff coordinates; the social link row; the corporate address footer and the
account and terms link cluster; and the support panels, which are matched at
the wrapper that holds them, so the need help and forgot something headings and
their messages go with the buttons instead of being left behind them. Two
patterns are written and then skipped, so both of their blocks stay. The
Uber One cashback strip prints the $1.18 it credited, which is a figure on a
real receipt. The rate and tip module prints the driver's 4.92 rating, which
is not money but has the shape of money, and the amount guard does not gamble
on the difference; the module's controls are defused like every other link.
The rules also carry a pattern for the standalone rate your trip row that the
shorter Lime and cancelled ride templates use, which this sample does not
have.

Nothing here is rewritten, so this folder carries no `replace` pairs. Uber's
total row gives the word Total a cell at `width:100%`, and that cell once left
the amount beside it printing a character a line. What fixed it was not a
rewrite of the vendor's markup but the packet's own scoping: each receipt's
stylesheet is scoped to its own card, so the row lays itself out inside that
card and the total prints on one line with the vendor's markup untouched. The
`replace` field is still there for a vendor that needs it. It is optional, it
holds `[regex, replacement]` pairs applied with `re.sub` after the strip
patterns, and the amount guard covers a rewrite the same way it covers a
removal: a pair that would leave the fragment printing fewer money strings is
skipped and named on stderr.

## Uber Eats

Uber Eats order receipts arrive in the same shell as Uber ride receipts, from
the same sender, with a different set of modules. Kept: every basket line with
its quantity and options, the subtotal, the delivery and service fees, the tax,
every discount and credit row, the order total, the order date and the order
completed timestamp, both tender rows when a voucher and a card split the bill,
and the card last four. Removed: the rate and tip block for the courier; the
app download and Download PDF buttons; the merchant and dish photography, whose
paths identify the real merchant; the social link row; the corporate address
footer and the account and terms link cluster; and the support panel, taken at
the wrapper that holds it so its heading and its message go with the button.
The Uber One savings strip stays, because it prints the $14.55 it saved and the
amount guard will not take an amount out of a receipt. Uber Eats and Uber ride
mail share the sender domain uber.com, so the domain alone cannot name the
folder: detection prefers the vendor whose subject patterns fit as well as its
domain, which sends an order subject here and a trip subject to Uber. The
order total sits in the same row shape as an Uber ride total, and it stays on
one line for the same reason: the packet scopes each receipt's stylesheet to
its own card, so no `replace` pair is needed here either.

## Marriott

A Marriott stay confirmation keeps the property name and address, the
confirmation number, the check-in and check-out dates and times, the room
description, the rate line, the summary of charges, the taxes and fees line,
and the cancellation terms with their deadline. Removed: the cardmember bonus
points offer and the app download banner, each taken as the whole banner table,
so the check-in on the go, unlock your room and message the front desk lines go
with the banner rather than being left behind it; the loyalty tier and points
balance strip, the Epsilon and Adobe tracking pixels, the hidden preheader, the
Manage Stay and Go Now buttons, the footer link row, the unsubscribe and programme
terms block, the copyright and proprietary notice, and the confirmation
authenticity boilerplate. Known gap: this sample is a points redemption stay,
so its total is a points figure and there is no cash amount and no card last
four anywhere in it. The amount pattern is written so it also reads the
currency amount a cash rate confirmation puts in the same cells, and the cash
the guest actually settles appears on the folio, not here.

## Lufthansa

Lufthansa is the one folder whose sample is not a charge receipt. Kept: the
baggage tag number and its status, the drop off date, the itinerary block with
its flight number, departure and arrival codes, cities and dates, and the
passenger line. Removed: the app download banner, the rate this email feedback
widget, the assistance and report damaged baggage call to action, the preheader
lines, the service and contact link row, and the corporate footer with the
registered office, the executive board and the court registration. Known gap:
the sample is a baggage receipt, which is receipt shaped but carries no
monetary amount at all, so the folder has no amount pattern. Treat it as the
Lufthansa layout reference; a Lufthansa document that does carry a price puts
it in a right aligned cell after its label, the way every Lufthansa Group
template does.

## NJ TRANSIT MyTix

An NJ TRANSIT MyTix purchase receipt keeps the purchase date and timestamp, the
transaction sequence id, the ticket numbers, every ticket row with its product,
tariff, zone count, quantity and amount, and the payment details table with its
method, processor transaction id and charged amount. Removed: the refund policy
notice, the in-app feedback line, the sign-off block, and the centred footer
table with the copyright row. The inline year script and the Outlook
conditional wrappers are removed by the shared cleaning pass, which drops every
script and every comment before the vendor patterns run. This template carries
no tracking pixel, no promo block, no app banner and no social links. There is
no grand total row: the ticket row and the payment row carry the same figure,
and the amount is read from the payment row, because that is what was charged.

## Stripe receipt

Stripe's own hosted receipt template is sent on behalf of a merchant, so one
entry here covers every vendor that bills through Stripe, which makes it the
highest-leverage folder in the set. Kept: the hero amount, the paid date, every
line item with its quantity and service period, the subtotal, the total
excluding tax, the tax line with its jurisdiction and rate, the total, the
amount paid, the receipt and invoice numbers, and the payment method with its
card brand mark and last four. Removed: the download invoice and download
receipt buttons, the merchant support block with its support site, address and
phone, the Powered by Stripe badge, and the hidden preheader spacer. The sender
local part is `invoice+statements`, with the merchant's Stripe account id
appended for small merchants and dropped when a large merchant self-hosts the
same template on its own domain.

## Plain text confirmation

Most hotel confirmations, including Hotels.com and citizenM, arrive as
plaintext. There is no markup to strip. The body is rendered in a monospace
block under a small header carrying From, Date and Subject, so the reader can
see who sent it and when. The body text is reproduced verbatim, including any
folio breakdown and any line about fees payable at the property. Long lines are
allowed to wrap rather than being reflowed, so nothing shifts column. Removed
from the citizenM sample: the bare logo link, which a text part renders as a
naked URL in parentheses; the app cross-sell block; the marketing consent
disclaimer; the licensing footer; and the footer link row. A generic
unsubscribe line is removed too, when a message has one; this sample does not.
This folder has no sender domain on purpose, so it is reached only when no
vendor matched by domain and no other subject pattern matched. Known gap: the
citizenM message carries no amount and no stay date at all. It is a pointer
saying the folio is ready, so both patterns are null, the date comes from the
message headers, and the amount comes from the folio PDF that the traveller
uploads or the Gmail fallback fetches as an attachment.

## Hotel senders

A search folder, not a rendering folder. It has no sample, strips nothing and
reads no values. It exists for two reasons: so the second search pass asks
Hotels.com, citizenM, Hilton, Hyatt, IHG, Booking.com, Airbnb and Sonder
directly, because a hotel confirmation often has a subject line that the
generic first pass never matches, and so that mail from one of those senders is
detected as a hotel rather than falling through to the plaintext catch-all. Its
subject patterns are deliberately narrow, so a stay confirmation that arrives as
a text body from a domain not listed there still reaches the plaintext rules. A
chain that needs real cleaning gets its own folder with its own sample, the way
Marriott does.

## Airline senders

The same idea for the carriers with no folder of their own: Delta, American,
JetBlue, Alaska, Southwest, British Airways and Air Canada. No sample, no strip
patterns, no amount or date patterns. It exists so the second search pass asks
those senders directly, because an itinerary or a receipt from a carrier with
no folder would otherwise be found only if the generic pass happened to match
its subject. Its subject patterns name the carriers rather than the generic
eTicket wording, so a United eTicket still resolves to the United folder, which
is the one with a sample and real strip patterns.

## Generic vendor

A vendor with no entry in `vendors/` still renders. The cleaner applies the
shared rules only: scripts and tracking pixels go, links that match the generic
tracking patterns are unwrapped, images are inlined, and the vendor's CSS is
scoped to the receipt container. Nothing vendor-specific is guessed at, so a
promotional block the shared rules do not recognise will still appear in the
packet. That is the intended failure: an unrecognised block is visible and can
be reported as a missing vendor entry, rather than silently removing something
that turned out to be part of the receipt.
