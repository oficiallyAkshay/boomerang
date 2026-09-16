# Local policy overrides

Copy this file to `policy.local.md` at the skill root, beside `policy.md`, and
edit it there. This template lives in `references/`, but Boomerang reads
`policy.local.md` when it is present and applies those lines on top of
`policy.md`, so the shipped ruleset stays intact. The copy is gitignored, which
keeps company-specific wording out of the repo.

## The keys

Each key names one rule in `policy.md` and replaces its default. Nothing else
is a key: a line Boomerang does not recognise here is read as prose, not as a
toggle. Omit a key to keep the shipped default, which is listed first below.

| Key | Values | The rule it overrides |
| --- | --- | --- |
| `tips` | `out`, `in` | Ground transport, the tips clause |
| `ride_extras` | `in`, `out` | Ground transport: ride tier, priority pickup, wait fees |
| `micromobility` | `in`, `out` | Ground transport: scooters, transit, tolls, parking |
| `alcohol` | `flag`, `out`, `in` | Meals, the alcohol and groceries clause |
| `upgrades` | `out`, `in` | Flight: upgrades, cabin changes, Economy Plus, priority boarding |
| `seat_fees` | `in`, `out` | Flight: seat if no free option |
| `duplicate_meal_minutes` | an integer, default `60` | Meals: the duplicate window |
| `home_currency` | an ISO 4217 code, default `USD` | Currency |

`in` claims the line, `out` drops it, and `flag` claims nothing but names the
line so the user sees it. `duplicate_meal_minutes` is the window inside which
a second meal counts as a duplicate, so `90` keeps the first of two meals
bought within an hour and a half. `home_currency` is the currency the posted
card amount is claimed in.

## The block

```text
tips: in
ride_extras: in
micromobility: in
alcohol: flag
upgrades: out
seat_fees: in
duplicate_meal_minutes: 60
home_currency: USD
```

That block changes one default: `tips: in`. `policy.md` drops tips, so with
this file in place a tip on a ride is claimed, and it stays on that ride's own
line rather than becoming a line of its own. Every other key above repeats the
shipped default and could be deleted without changing a thing.

## Notes

Prose below the block is advisory wording, applied after the toggles. It
cannot claim or drop a line; it shapes how a line that survives the toggles is
worded and grouped.

- The finance team wants one line per hotel stay, not a line per night.
- Anything over 75 in a single meal line needs a sentence saying who was there.
