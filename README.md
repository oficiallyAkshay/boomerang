<h1 align="center">🪃 boomerang</h1>

<p align="center">
  <b>Your money went out. Bring it back.</b>
  <br>
  Turn your inbox into a reimbursement claim.
</p>

<p align="center">
  <a href=".github/workflows/ci.yml"><img alt="CI" src="https://img.shields.io/github/actions/workflow/status/oficiallyAkshay/boomerang/ci.yml?branch=main&logo=githubactions&logoColor=white&label=CI"></a>
  <a href="https://codecov.io/gh/oficiallyAkshay/boomerang"><img alt="coverage" src="https://img.shields.io/codecov/c/github/oficiallyAkshay/boomerang?logo=codecov&logoColor=white"></a>
  <img alt="Python 3.11 or newer" src="https://img.shields.io/badge/python-3.11%2B-3776AB?logo=python&logoColor=white">
  <a href="references/vendors.md"><img alt="vendors covered" src="https://img.shields.io/badge/vendors-10-6f42c1?logo=databricks&logoColor=white"></a>
  <a href="LICENSE"><img alt="MIT licence" src="https://img.shields.io/badge/license-MIT-2f6f4e?logo=opensourceinitiative&logoColor=white"></a>
</p>

<p align="center">
  <sub>Runs on</sub>
  <br>
  <a href="references/hosts.md#claude-code"><img alt="Claude Code" src="https://img.shields.io/badge/Claude%20Code-3f3f46?logo=anthropic&logoColor=white"></a>
  <a href="references/hosts.md#claudeai-and-the-claude-desktop-app"><img alt="Claude.ai" src="https://img.shields.io/badge/Claude.ai-3f3f46?logo=claude&logoColor=white"></a>
  <a href="references/hosts.md#cursor"><img alt="Cursor" src="https://img.shields.io/badge/Cursor-3f3f46?logo=cursor&logoColor=white"></a>
  <a href="references/hosts.md#codex"><img alt="Codex" src="https://img.shields.io/badge/Codex-3f3f46"></a>
  <a href="references/hosts.md#openclaw"><img alt="OpenClaw" src="https://img.shields.io/badge/OpenClaw-3f3f46"></a>
  <a href="references/hosts.md#hermes"><img alt="Hermes" src="https://img.shields.io/badge/Hermes-3f3f46"></a>
  <br>
  <sub>Install paths and limits per host: <a href="references/hosts.md">references/hosts.md</a></sub>
</p>

<p align="center"><img alt="Inbox and calendar feed receipts of every kind into one PDF packet with the receipts behind the summary" src="assets/readme/flow.svg" width="900"></p>

<p align="center">
  <b><a href="examples/packet.pdf">See the example packet (PDF)</a></b> <b><a href="examples/packet.html">or the HTML master</a></b>
  <br>
  <sub>Every name, address, card and amount in it is fully synthetic.</sub>
</p>

You spent your own money on someone else's behalf. An onsite interview, a client trip, a contract gig. Now the receipts are scattered across your personal inbox and a company is waiting on a claim you haven't had time to build.

Boomerang builds it. Every receipt, the right total, one PDF.

| Summary page | Receipt pages |
| --- | --- |
| <img alt="Page one of the packet: the trip header, the days with their items and subtotals, and the Expenses, Stipend and Total rows" src="assets/readme/packet-page-1.png" width="232"> | <img alt="Eight receipt pages from the packet: two rides, a meal order, a grocery delivery, a flight eticket, a hotel stay, a bus fare and a software licence, one per page" src="assets/readme/packet-receipt-pages.png" width="465"> |

<p align="center">
  <sub>Page one and eight receipt pages from the synthetic example in <a href="examples/packet.pdf"><code>examples/packet.pdf</code></a>, each receipt fitted to a Letter page.</sub>
</p>

## Features

- **Every dollar captured.** Flights, hotel nights and resort fees, rides, scooters, transit, tolls and parking, meals on travel and working days. Rides that landed in your inbox a day late, airport Wi-Fi, the scooter to the office, the resort fee at checkout. Boomerang searches your whole trip window so nothing gets missed
- **The right split, mixed payees.** Some things they paid, some things you paid. Boomerang spots the company card, follows the eTicket chain, counts the flight credit in and the points out, drops the flight and room they covered, and claims the rest
- **Real receipts.** Rendered from the vendor's own email, markup and amounts untouched, one per page. Lyft looks like Lyft, United looks like United. Nobody has to ask what a line means
- **Multi-currency.** The claim carries the posted home-currency amount, and the line notes what it was in the local one
- **Multi-company per trip.** Two onsites inside one trip, with the shared flight and the shared nights split between them
- **Change fees and cancelled trips.** The fee you ate and the trip that never happened both land in the claim, labelled for the reviewer
- **Personal days handled.** The days you added for yourself come out, and the airport legs at either end stay in
- **Stipends and per diems.** Counted by the days you actually worked, and shown as their own rows above the total
- **Time saved.** One ask, one question back, one PDF. Days, subtotals, total. Send it and move on

Full ruleset in [`policy.md`](policy.md).

## How it works

<p align="center">
  <img alt="How boomerang works: two search passes feed a fetch step; cleaning, card fingerprinting and folio text run in parallel; the model applies the policy and shows a candidate list; then build, render and splice produce the packet" src="assets/diagram/architecture.svg" width="900">
</p>

Code does the mechanical work (search, fetch, clean, who paid, totals, render); the model supplies judgment (the trip, the policy, the candidate list, the one question). Cleaning, card fingerprinting and folio text run at the same time over the receipts on disk. The diagram is rendered once from [`assets/diagram/architecture.archify.json`](assets/diagram/architecture.archify.json) with Archify and committed; nothing here depends on it.

## Quick start

1. `npx skills add oficiallyAkshay/boomerang`, which installs the skill into
   Claude Code, Cursor, Codex and about seventy other agents. By hand instead:
   clone this repo and copy the folder into `~/.claude/skills/boomerang/`. On
   Claude.ai and Cowork there is no clone: zip the skill folder and upload it
   under Customize, Skills, as [`references/hosts.md`](references/hosts.md)
   sets out.

2. `pip install -r requirements.txt`, or `uv sync`. No browser download is
   needed when Chrome or Edge is already on the machine.

3. Ask your agent: "Build my reimbursement packet for the trip on June 11."

`python scripts/doctor.py` prints what is present, what is missing, and the one
command that fixes each thing.

## What you need

- Your email connected
- Your calendar connected (optional, helps find the dates)

## Contributing and license

Bug reports, vendor samples and fixes are welcome, and
[`.github/CONTRIBUTING.md`](.github/CONTRIBUTING.md) says what the project takes
and how to run the checks first.

MIT, see [`LICENSE`](LICENSE).
