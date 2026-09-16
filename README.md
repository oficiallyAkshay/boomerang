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
</p>

You spent your own money on someone else's behalf. An onsite interview, a client trip, a contract gig. Now the receipts are scattered across your personal inbox and a company is waiting on a claim you haven't had time to build.

Boomerang builds it. Every receipt, the right total, one PDF.

| Summary page | Vendor receipts |
| --- | --- |
| <img alt="Packet summary table: days, subtotals, stipend and total" src="assets/readme/packet-summary.png" width="252"> | <img alt="Eight receipts from the packet: Lyft, Uber, Uber Eats, DoorDash, United, Marriott, NJ Transit and Stripe" src="assets/readme/receipts-grid.png" width="520"> |

<p align="center">
  <sub>The first page and the receipts from the synthetic example in <a href="examples/packet.pdf"><code>examples/packet.pdf</code></a>.</sub>
</p>

## What you get

- **Every receipt, found.** Rides that landed in your inbox a day late, airport Wi-Fi, the scooter to the office, the resort fee at checkout. Boomerang searches your whole trip window so nothing gets missed
- **The right split.** Some things they paid, some things you paid. Boomerang spots the company card, drops the flight and room they covered, and claims the rest
- **Real receipts, one per page.** Rendered from the vendor's own email, amounts untouched. Lyft looks like Lyft, United looks like United. Nobody has to ask what a line means
- **One PDF, one table, one number.** Days, subtotals, total. Send it and move on

## What it covers

- Flights, and who paid for them
- Hotels, resort fees, and desk charges
- Rides, scooters, transit, tolls, parking
- Meals on travel and working days
- Change fees and cancelled trips
- Personal days added to the trip
- Stipends and per diems
- Foreign currency

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

## Where it runs

Claude desktop app, Claude.ai, Claude Code, Cowork, Codex, OpenClaw, Hermes. Anywhere that reads a skill folder.

What each host can and cannot run is in [`references/hosts.md`](references/hosts.md).

## Example

[`examples/packet.pdf`](examples/packet.pdf) is a finished packet, and
[`examples/packet.html`](examples/packet.html) is the page it was printed from.
Every name, address, card and amount in it is synthetic.

## Contributing

Bug reports, vendor samples and fixes are welcome, and
[`.github/CONTRIBUTING.md`](.github/CONTRIBUTING.md) says what the project takes
and how to run the checks first.

## License

MIT
