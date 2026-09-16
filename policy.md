# Policy

The ruleset boomerang applies when it builds a packet. Copy it, edit it, or
override single lines in `policy.local.md`.

The basis for every rule below is standard practice for accountable
reimbursement, unless the line says otherwise. Where a rule rests on something
else, that basis is named in brackets at the end of it.

## Scope

- One destination per packet. Multi-city trips run as separate packets
- Multi-company supported: one trip, two onsites, same city. Flight and shared hotel nights split evenly by default; hotel nights and ground rides on a working day go entirely to that day's company. Two packets, two totals, each noting the shared fare
- Drove instead of flew: tolls and parking only. No mileage. Gas out by default

## Discovery

- Onsite date from calendar; travel window from the final eTicket
- Gmail in two passes, window padded a day each side
- Recruiter-forwarded confirmations carry the policy: what they booked, what they'll reimburse, fees payable at property
- Hotel folios arrive as attachments the connector can't fetch; ask for upload or use gmail_cli
- One mailbox only. Receipts elsewhere are collected by upload or forward
- Final sweep the day before sending

## Who paid

- A card last-4 seen on one receipt in the whole mailbox is someone else's
- Read eTicket chains oldest first; "previous ticket value applied" means the base fare was paid by someone else
- Flight credits are cash-equivalent, in. Miles, points, vouchers, out (cash-equivalent)
- Net refunds against their line; fully refunded items out

## Flight

- Company paid: base fare out; only necessary charges on the user's card in (checked bag; seat if no free option) (necessary to attend) (default; override in policy.local.md)
- User paid: original economy fare in (necessary to attend)
- Change fees: in when the change served the interview, out when it served the user. Ambiguous cases asked (necessary to attend)
- Upgrades, cabin changes, Economy Plus, priority boarding: out by default (personal benefit) (default; override in policy.local.md)

## Hotel

- Company paid: room and tax out; desk charges in (resort fee, parking, incidentals actually used) (necessary to attend)
- User paid: room and tax for working nights and required travel nights in (necessary to attend)
- Personal-day nights out. Refundable deposits and auth holds out. Minibar, laundry, room service out unless replacing a claimable meal (personal benefit)
- Mandatory hotel fees as one line

## Ground transport, regardless of who booked the trip

- Always in: home to airport, airport to hotel, hotel to airport, airport to home (necessary to attend)
- Always in: hotel to office and back on working days (necessary to attend)
- Personal days never remove the four airport legs, even when the return is days later. Only rides in between are out (necessary to attend)
- Ride tier, priority pickup, wait fees, airport surcharges in. Tips out (default; override in policy.local.md)
- Scooters, transit, tolls, parking in (default; override in policy.local.md)
- A ride must touch home, airport, hotel, or office; anything else is personal (the company did not cause the cost)

## Meals

- In on travel days and working days, one per meal slot (necessary to attend)
- Out when the company provided the meal (recruiter emails mentioning lunch, catering links) (the company did not cause the cost)
- Duplicates within an hour: keep the first, flag the second (default; override in policy.local.md)
- Alcohol and groceries flagged, not claimed (personal benefit) (default; override in policy.local.md)

## Stipend

- Only when stated in writing or by the user. One line, days worked only

## Cancelled or postponed onsite

- Change fees, non-refundable nights, hotel no-show charges in (necessary to attend)

## Currency

- Claim the card's posted home-currency amount; the receipt shows the local total. Note the conversion once per line (default; override in policy.local.md)
