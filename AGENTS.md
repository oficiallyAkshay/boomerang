# boomerang

Builds a reimbursement packet from a personal inbox. See `SKILL.md` for the
workflow an agent runs to build one.

## Checks

Run these before committing:

```
uv run pytest
uv run pre-commit run --all-files
uv run python scripts/check_prose.py --packet examples/packet.html
uv run python examples/build_example.py --check
```

All four must pass. Coverage stays at the floor recorded in `pyproject.toml`;
changed lines hold to 90 percent, which CI enforces with diff-cover.

## Rules that bind agents here

- Synthetic data only. Real receipts, real names and anyone's personal or
  company policy are declined; see `.github/CONTRIBUTING.md`.
- No em dashes anywhere, code comments included.
- One rule, one vendor, or one fix per pull request, titled as a plain
  sentence stating the outcome.
- Never bypass a hook, and never edit a gate's own configuration to make it
  pass.
- Nothing identifying reaches a file or a commit message; the prose and
  privacy gate checks every commit against a hashed denylist.

See `.github/CONTRIBUTING.md` for the full contribution process, the vendor
and policy rule contracts, and what CI runs.
