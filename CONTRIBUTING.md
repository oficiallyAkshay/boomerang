# Contributing

Thanks for looking. This page says what the project takes, what it declines,
and how to run the checks before you open a pull request.

## What this skill is for

Boomerang encodes general rules about reimbursement, not anyone's preferences.
A rule belongs here when it would hold for most people claiming travel back
from most companies. "Tips are not reimbursable travel cost" is that kind of
rule. "Our finance team wants one line per hotel stay" is not.

That is why the policy has two layers.

`policy.md` ships with the skill. It holds the general ruleset, and every
bullet carries the norm it rests on in parentheses. It is reviewed, tested, and
merged like code.

`policy.local.md` is the user's own layer. It is gitignored, it is read after
`policy.md`, and its lines win. A user copies `policy.local.example.md`, edits
it, and keeps their company's wording on their own machine. Local files are
never merged into this repo, and pull requests that add one will be closed.
The split exists so that a personal or employer-specific rule never has to be
argued about in public, and so nobody's finance policy leaks through a git
history.

## What to contribute

In rough order of how much it helps.

### 1. General rules for policy.md

The most valuable contribution. A rule, the norm it rests on, and a synthetic
test scenario that fails without the rule and passes with it.

Use one of the existing bases if it fits: the company did not cause the cost;
necessary to attend; standard practice for accountable reimbursement;
cash-equivalent; not cash-equivalent; personal benefit. If your rule needs a
new basis, say so in the pull request and explain it. If the honest basis is
"this is a preference", the rule belongs in the local layer instead, and the
bullet should carry the default-and-override marker.

### 2. Vendor entries

A vendor entry is `vendors/<name>/rules.json` plus a synthetic sample email
under the tests fixtures. The fields:

| Field | Meaning |
| --- | --- |
| `name` | Lowercase key, matching the folder name |
| `display` | The vendor name as it appears in a packet |
| `sender_domains` | Domains the receipt is sent from |
| `subject_patterns` | Regexes that match the receipt subject |
| `strip_regex` | Regexes removed from the HTML, applied with `re.S` |
| `unwrap_links_matching` | Regexes on `href`; matching links lose their wrapper |
| `amount_regex` | Regex capturing the total, or null |
| `date_regex` | Regex capturing the date, or null |
| `notes` | Plain sentence describing what is removed and what is kept |

Prove the strip regexes match. Add a synthetic sample, run `clean.py` over it,
and assert in a test that the promotional block is gone and that the amount,
the date, and the line items survive byte for byte. A strip regex with no test
will be asked for one. Keep `notes` in agreement with the vendor's paragraph in
`references/vendors.md`; the two are read side by side, and a contradiction is
a bug.

### 3. Mail providers and receipt source kinds

`fetch.py` talks to a source object with `search` and `get`. Another provider
means another class with that shape. A new receipt source kind, for example a
forwarded message or a screenshot upload, means saying how it reaches disk and
how `build.py` should render it.

### 4. Verified host install notes

`docs/hosts.md` lists where each host reads a skill folder. Every line there is
backed by a citation. If you add a host or correct a row, include the URL you
read it from and the date you read it. Anything you cannot cite goes in the
"Not verified" list at the bottom, not in the table.

### 5. Edge cases as fixtures

A trip shape the skill gets wrong is worth a fixture even without a fix. Build
it with the fixture maker, describe what should happen, and mark the test
expected to fail.

## Hard rules

- Synthetic data only. Never a real receipt, a real name, a real address, a
  real email, or a real message id. Invent them.
- The prose and privacy gate must pass. It scans every tracked file.
- No em dashes, anywhere, including code comments.
- One rule per pull request. A pull request that changes four rules gets
  reviewed as four arguments and merged as none.
- The pull request title is a plain sentence stating the outcome, for example
  "Airport parking is claimable on a drive-instead-of-fly trip". Not a ticket
  number, not a verb phrase.
- A rule that encodes one person's preference, or one company's policy, is
  declined. The answer is not "no", it is "put it in `policy.local.md`", and
  the reviewer will point you at `policy.local.example.md`.

## How to run the checks

```bash
uv sync --all-extras
uv run playwright install chromium
uv run pre-commit install
uv run pytest
```

`pre-commit install` wires the gate into `git commit`, so the prose and privacy
check, ruff, and markdownlint run before anything lands.

Coverage bar: changed lines at 90 percent or above. The CI job measures the
diff, not the whole repo, so a small change is held to the same standard as a
large one. If a line genuinely cannot be covered, say why in the pull request.
