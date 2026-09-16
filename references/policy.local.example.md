# Local policy overrides

Copy this file to `policy.local.md` at the skill root, beside `policy.md`, and
edit it there. This template lives in `references/`, but Boomerang reads
`policy.local.md` when it is present and applies those lines on top of
`policy.md`, so the shipped ruleset stays intact. The copy is gitignored, which
keeps company-specific wording out of the repo.

```text
tips: out
alcohol: flag
upgrades: out
seat_fees: in
home_currency: USD
```

## Notes

- The finance team wants one line per hotel stay, not a line per night.
- Anything over 75 in a single meal line needs a sentence saying who was there.
