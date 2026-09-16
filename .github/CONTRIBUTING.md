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
uv run playwright install chromium
uv run pre-commit install
uv run pytest
```

Chrome or Edge on the machine renders the PDF; line two is only for a machine
with neither. Coverage bar: changed lines at 90 percent or above, CI enforces it.

## For agents

The layout.

| Path | What it holds |
| --- | --- |
| `SKILL.md` | The workflow, run in order, with the traps that cost a packet |
| `policy.md` | The shipped ruleset, one bullet per rule |
| `scripts/` | Everything deterministic: fetch, clean, cards, build, render, splice, gate |
| `vendors/<name>/` | One `rules.json` and one scrubbed sample |
| `references/` | `interfaces.md`, `vendors.md`, `hosts.md`, `policy.local.example.md` |
| `tests/` | pytest, the fixture maker, the hashed denylist |
| `examples/` | The committed worked packet, built from the vendor samples |

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
`strip_regex` applied with `re.S`, `unwrap_links_matching`, `amount_regex`,
`date_regex`, and `notes` that agree with the vendor's paragraph in
`references/vendors.md`. Ship a synthetic sample beside it, scrubbed of
anything real and tag-balanced, so each strip pattern takes a whole element.
Prove every pattern fires on that sample in `tests/test_vendors.py`. The amount
guard skips any pattern that would carry a money string away, so no vendor gets
an allowance to lose a figure its sample printed.

**Adding a policy rule.** One bullet in the right section of `policy.md`, its
basis in brackets from the closed set: `necessary to attend`, `personal
benefit`, `cash-equivalent`, `the company did not cause the cost`, or none,
meaning standard practice for accountable reimbursement. A preference also
carries `(default; override in policy.local.md)`. Add a synthetic scenario that
fails without the rule and passes with it.

**The gates, run locally.**

```bash
uv run pre-commit run --all-files                     # ruff, markdownlint, gitleaks, prose
uv run python scripts/check_prose.py                  # tracked files
uv run python scripts/check_prose.py --packet packet.html
uv run python examples/build_example.py --check       # after anything that alters a packet
```

**Git.** One branch per pull request. Never commit to `main`. Never
`git add -A`. Plain-sentence titles. Merges are rebases.

**Privacy.** Nothing identifying reaches a file or a commit message. The gate
matches a hashed denylist, `tests/pii_denylist.sha256`, so the real values are
not in the tree. They must never enter the history either.
