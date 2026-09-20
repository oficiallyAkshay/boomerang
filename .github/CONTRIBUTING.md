# Contributing

Boomerang encodes general reimbursement rules, not anyone's preferences.

## What we want

- **General rules for `policy.md`.** Each bullet carries the norm it rests on.
- **Vendor entries.** A `rules.json` plus one scrubbed synthetic sample.
- **Receipt sources.** Another mail provider, a forward, an upload.
- **Verified host notes.** A `references/hosts.md` row, with the URL and the
  date you read it. Anything uncitable goes in "Not verified".
- **Edge cases as fixtures.** A trip shape we get wrong, fix or no fix.

## What we decline

Real receipts. Real names. Anyone's personal or company policy. A rule that is
one team's preference is not a "no", it is a `policy.local.md` line: copy
`references/policy.local.example.md`, edit your copy, keep it on your machine.
It is gitignored, it is read after `policy.md`, and its lines win.

## Three hard rules

1. Synthetic data only, and the prose and privacy gate must pass.
2. No em dashes anywhere, code comments included.
3. One rule per pull request, titled as a plain sentence stating the outcome:
   "Airport parking is claimable on a drive-instead-of-fly trip".

## Run it

```bash
uv sync --all-extras
uv run python scripts/doctor.py
uv run playwright install chromium
uv run pre-commit install
uv run pytest
```

Chrome or Edge on the machine renders the PDF; line three is only for a machine
with neither, and line two says whether this is one of them. Coverage bar:
every line and branch, changed or not, CI enforces it at 100 percent.

## Install and configure

The one-line installer, `npx skills add oficiallyAkshay/boomerang`, drops the
skill into Claude Code, Cursor, Codex and about seventy other agents; the
per-host paths for a manual copy, or for a host the installer does not reach,
are in [`references/hosts.md`](../references/hosts.md). Either way the Python
side is `pip install -r requirements.txt` or `uv sync`, and
`python scripts/doctor.py` prints what is present, what is missing, and the
one command that fixes each thing. No browser download is needed when Chrome
or Edge is already on the machine.

| Setting | Where | Default |
| --- | --- | --- |
| The shared ruleset | [`policy.md`](../policy.md) | Ships with the skill, yours to edit |
| Your own overrides | `policy.local.md`, ignored by git ([template](../references/policy.local.example.md)) | Tips out, ride extras in, alcohol flagged, upgrades out, seat fees in, 60 minute meal window, USD |
| Browser for the PDF | `BOOMERANG_BROWSER` | Chrome, then Edge, then Chromium, first one found |
| Gmail fallback | `BOOMERANG_GMAIL_CLIENT_SECRET` | Off, the host's own email tool is used |

## Architecture

<p align="center">
  <img alt="How boomerang works: two search passes feed a fetch step; cleaning, card fingerprinting and folio text run in parallel; the model applies the policy and shows a candidate list; then one build step produces the packet" src="../assets/diagram/architecture.svg" width="900">
</p>

Two search passes, then cleaning, who paid and folio text in parallel, then
the model's judgment, then one packet. Drawn with Archify from
[`assets/diagram/architecture.archify.json`](../assets/diagram/architecture.archify.json);
rebuild it there if the flow changes.

## Badge recipes

The README's badge row carries only the rendered badges; the URL each one
reads from lives here. The clone and view counts come from
[`clonometer`](https://github.com/oficiallyAkshay/clonometer), run daily by
the `clonometer` workflow described in the table above, writing
`clones.json` and `views.json` to the `badges` branch. A shields
dynamic-json badge reads either file with a `query` of `$.badge` for the
combined form, `$.last7_short` for the week alone, or `$.total_short` for the
lifetime count, for example:

```
https://img.shields.io/badge/dynamic/json?url=https://raw.githubusercontent.com/oficiallyAkshay/boomerang/badges/clones.json&query=$.badge&label=clones&logo=github&logoColor=white
```

Until the owner adds the `TRAFFIC_TOKEN` secret and the repository is public,
every one of those badges reads "resource not found": GitHub's traffic
endpoints need the token, and shields cannot fetch a raw file from a private
repository either way.

The vendor count badge reads its number from `readmerlin.json`, whose
`counts.vendors` command counts the vendor folders that ship a sample; the
same figure `tests/test_vendors.py` checks against the README on every pull
request.

## Repository layout

The layout.

| Path | What it holds |
| --- | --- |
| `SKILL.md` | The workflow, run in order, with the traps that cost a packet |
| `AGENTS.md` | The short agent block every host reads; this file carries the rest |
| `policy.md` | The shipped ruleset, one bullet per rule |
| `scripts/` | Everything deterministic: fetch, clean, cards, build, render, splice, gate |
| `vendors/<name>/` | One `rules.json` and one scrubbed sample |
| `references/` | `interfaces.md`, `vendors.md`, `hosts.md`, `policy.local.example.md` |
| `tests/` | pytest, the fixture maker, the hashed denylist |
| `examples/` | The committed worked packet, built from the vendor samples |
| `readmerlin.json` | The count-source command behind the README's vendor badge |

**The split.** If a wrong answer costs money or credibility it belongs in a
script under `scripts/`. If it costs one clarifying question it belongs to the
agent, described in the SKILL.md prose. Totals, card fingerprints and page
counts are script work and never move into prose.

**The contract.** `references/interfaces.md` is what two scripts have to agree
on: the `expense_data.json` schema, the `rules.json` shape, and every command
line. The schema is `company`, `trip`, `traveler`, optional `currency`, then
`days[].items[]` of `desc`, `amt`, `rid` with optional `refund`, `local_amt`,
`local_currency`, `at`; `receipts[]` of `rid`, `title`, `vendor` with optional
`kind`; and an optional `stipend`. The packet does the arithmetic, so state the
parts and never the answer.

**Adding a vendor.** New folder `vendors/<name>/`, holding `rules.json` with
`name` matching the folder, `display`, `sender_domains`, `subject_patterns`,
`strip_regex` applied with `re.S`, `unwrap_links_matching`, an optional
`replace` of before and after pairs for markup a strip pattern cannot fix, the
optional vendor knowledge fields `arrival_lag_days`, `messages`, `tenders`,
`last4_pattern`, `line_categories`, `missing` and `endpoints`, and `notes` that
agree with the vendor's paragraph in `references/vendors.md`. The knowledge
fields are read by the model rather than by the cleaner, their kinds and
categories come from the closed sets in `references/interfaces.md`, and every
label they name has to be a label the sample really prints. Ship
a synthetic sample beside it, scrubbed of anything real and tag-balanced, so
each strip pattern takes a whole element. Prove every pattern fires on that
sample in `tests/test_vendors.py`. The amount guard skips any pattern that would
carry a money string away, so no vendor gets an allowance to lose a figure its
sample printed, and a promotional module that prints an amount stays on the
receipt for the same reason. Bump the vendor count in the README badge; the
test tells you the number.

**Adding a policy rule.** One bullet in the right section of `policy.md`, its
basis in brackets from the closed set: `necessary to attend`, `personal
benefit`, `cash-equivalent`, `the company did not cause the cost`, or none,
meaning standard practice for accountable reimbursement. A preference also
carries `(default; override in policy.local.md)`. Add a synthetic scenario that
fails without the rule and passes with it.

**The gates, run locally.**

```bash
uv run pre-commit run --all-files   # ruff, actionlint, zizmor, markdownlint, gitleaks, prose
uv run python scripts/check_prose.py                  # tracked files
uv run python scripts/check_prose.py --packet packet.html
uv run python examples/build_example.py --check       # after anything that alters a packet
```

**What CI runs.** The same gates plus the ones that need a runner. Every row
the `ci` gate waits on blocks the merge; the coverage upload does not, because
it cannot authenticate until the repository is public.

| Check | Runs on | Blocks merge |
| --- | --- | --- |
| pre-commit hooks, gitleaks skipped | `checks` | yes |
| packet prose and privacy gate | `checks` | yes |
| secrets scan over the whole history | `checks` | yes |
| verify actions are pinned | `checks` | yes |
| dependency audit | `checks` | yes |
| pytest with coverage | `test`, on 3.11 and 3.13 | yes |
| diff coverage at 100 percent | `test`, pull requests only | yes |
| example check | `test`, on 3.11 and 3.13 | yes |
| coverage upload | `test`, on 3.13 only | no |
| gate | `ci` | yes |

A pull request run takes about a minute and a half end to end: `checks` around
50 seconds, the two test legs about a minute each beside it, the gate in
seconds.

**What else runs, off the gate.** None of these block a merge; a finding is
triaged, not a ruleset failure.

| Workflow | What it does | Runs on |
| --- | --- | --- |
| `readme-check.yml` | Holds the README, `AGENTS.md` and `.github/CONTRIBUTING.md` to shape and honesty | Every push, every pull request, weekly |
| `codeql.yml` | Scans the Python source and the workflow files for known vulnerability patterns | Every push to main, every pull request, weekly |
| `scorecard.yml` | Rates the repository's own supply-chain hygiene and publishes the score | Every push to main, weekly |
| `dependency-review.yml` | Scans a pull request's manifest changes for a known-vulnerable package | Every pull request |
| `audit.yml` | The dependency audit split out of the pull request path, so an advisory published later still gets caught | Weekly, and any pull request touching `pyproject.toml` |
| `clonometer.yml` | Reads the daily clone and view counts the badges read from | Daily, on a schedule |
| `dependabot-auto-merge.yml` | Arms auto-merge on a Dependabot pull request; the `ci` gate still has to pass before it actually merges | Every Dependabot pull request |

**Test plan a change must satisfy.** Find your area and write the test that
proves the row before you open the pull request.

| Area | What a change there must prove |
| --- | --- |
| Vendor rules | Every strip pattern fires on the scrubbed sample, no pattern carries an amount away, the sample stays tag-balanced, and each subject pattern matches a subject the sample really prints |
| Cleaner | Nothing active survives: the probe block in `tests/test_clean.py` gains a case for the shape you changed |
| Packet builder | `validate` names the bad input as a problem, totals stay in `Decimal`, and the prose gate passes on the rendered packet |
| Renderer | The page map equals what pypdf counts, the render reaches no network, and a scaled receipt stays above the readable floor |
| Fetch | The order is deterministic whatever order the answers come back in, and one message that fails is surfaced without ending the run |
| Docs | Every path `SKILL.md` names exists, the description stays under 200 characters, and the README vendor badge matches the folder count |
| Example | `examples/build_example.py --check` matches byte for byte after an offline rebuild |

**Git.** One branch per pull request. Never commit to `main`. Never
`git add -A`. Plain-sentence titles. Merges are rebases.

**Privacy.** Nothing identifying reaches a file or a commit message. The gate
matches a hashed denylist, `tests/pii_denylist.sha256`, so the real values are
not in the tree. They must never enter the history either.
