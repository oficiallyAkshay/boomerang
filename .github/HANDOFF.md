# Handoff (throwaway)

Where work on this repo stopped on 2026-09-17 and what is next, collected from several build sessions. The newest entry wins where two disagree. Delete this file once the items are picked up; it is not documentation.

## boomerang (from the clonometer session)

### What's left
- Owner said no boomerang work during the clonometer session. When ready: set `TRAFFIC_TOKEN` (needs Contents write now, not only Administration read), delete `scripts/ci/clones_badge.py` and `tests/test_clones_badge.py`, replace `.github/workflows/badges.yml` with the ten-line consumer workflow pinned by SHA, repoint the README badge to the `$.badge` recipe over `clones.json`, rewrite "How usage is counted" to lifetime plus window, keep the vendor badge test. No other rebuild needed.



---

## boomerang (from the readmerlin build session)

- Worktree `.claude/worktrees/oss-readme-template-9aff21`, branch `claude/oss-readme-template-9aff21`, clean at 20f01e9. Nothing was changed there.
- `readmerlin check README.md --no-links` on that branch: 7 fails. No hero spec beside `assets/readme/flow.svg`; line 70 says the diagram was rendered with Archify; the host path pointer line under the Runs on row; headings "What you need" and "Contributing and license"; the vendors count badge has no `counts` source in a `readmerlin.json` (wire it to whatever the existing vendor test counts); the Python badge is not a link.
- Warnings worth acting on: the "or the HTML master" second link and the "fully synthetic" disclaimer, both on the owner's own kill list; `flow.svg` repeats one document glyph across all seven cards.
- These sit alongside the clonometer adoption steps in the boomerang section above. One PR can do both.



---

## boomerang (from the pierless session)

### What's left
- This session changed nothing in boomerang. It only ran from the worktree `boomerang/.claude/worktrees/gangplank-handoff-setup-d11157` (branch `claude/gangplank-handoff-setup-d11157`, clean); remove the worktree and delete the branch.



---

## boomerang (from the hero skill session, live state checked 2026-09-17)

### State
- Still PRIVATE. Local main equals origin at `f245a93`. No open PRs. No tags, by the owner's instruction.
- The legal boilerplate strip, which memory listed as the last open item, is merged (`107850c`, `997cf1f`). The 14-day clones badge job is on main.
- Six worktrees sit under `boomerang/.claude/worktrees/`. They are session workspaces for other projects (clonometer, gangplank, hero skill, README template, repo builder plan, a private consumer repo) and hold no boomerang work. They can be removed once those sessions are done. This session's worktree is clean.

### What's left
- The boomerang build session's entry below is the authority for this repo. It lists the clonometer adoption and the README work against the newer rules. This entry adds only the herofold item.
- Commit a `flow.hero.json` beside `assets/readme/flow.svg` once herofold exists, so the hero is reproducible.
- Owner steps: flip the repo public, upload the emoji social preview, set `TRAFFIC_TOKEN`, and ask GitHub support to purge pre-rewrite PR refs if that matters.



---

## boomerang (written by the boomerang build session, 2026-09-17; authoritative for this repo)

### State
- Private at github.com/oficiallyAkshay/boomerang. Main at `f245a93`, 67 commits, 45 PRs merged, none open, CI green. 992 tests, 99 percent coverage, prose and PII gates clean, example packet rebuilds byte for byte at 23 pages. History is one no-reply identity plus Dependabot. Ruleset 23517915 on main: PR required, required check `ci`, rebase merges, strict up-to-date off.
- Worktrees pruned. The worktree `oss-readme-template-9aff21` belongs to another session and was left alone on purpose.
- No secrets set on the repo. `TRAFFIC_TOKEN` was never created, so the badge workflow has only ever skipped with a notice.
- The session scratchpad holds `OSS-REPO-RULES.md`, `README-RULES.md`, `GANGPLANK-HANDOFF.md`, `CLONOMETER-HANDOFF.md`, the emoji icon PNGs and the filter-repo mailmap. It may be cleaned; the rules are mirrored in memory (`oss-repo-rules.md`, `readme-style.md`), the clonometer spec is in `~/Downloads/clonometer-handoff.md`.

### What's left, in order
1. Clonometer adoption, exactly as the clonometer session wrote above (delete the local script and its tests, ten-line consumer workflow pinned by SHA, `$.badge` recipe, rewrite "How usage is counted"). Add to that: the README badge `alt` and the anchor `#how-usage-is-counted` stay; `tests/test_vendors.py` keeps the vendor badge honest and must not be touched; the repo's README check in CI greps badge URLs, so run the full gate locally before pushing. One PR, verifier before auto-merge.
2. Badge while private: shields cannot read the branch. Either flip public or use clonometer's gist mirror once that lands (clonometer item 1), the same way a private consumer repo does.
3. README against the newer rules: "How it compares" still has categories as columns and the owner reversed that rule on 2026-09-17 (name the actual libraries, linked, every cell verified). Candidates to compare against are expense-report generators and receipt parsers a reader could click, not SaaS products. Then hold the README to the readmerlin shape from the clonometer session's list above (no Quick start section, install line in the hero, three-row security table, five-line limits, agent material in CONTRIBUTING) and run `readmerlin check` once it is on npm. Expect the owner to want the existing "Configuration and security" table trimmed to that three-row form.
4. Owner's click to go public. After it: upload the emoji social preview, confirm the CI, Codecov and skills.sh badges render, optionally ask GitHub support to purge the pre-rewrite objects still reachable through old PR refs.
5. Later, not promised: replace the hand-made flow infographic with a herofold hero once herofold exists. The Archify architecture SVG stays.

### Learnings (boomerang-specific)
- GitHub squash merges stamp the account's primary email as author. It leaked into 15 commits and cost a git-filter-repo rewrite (mailmap plus replace-text), a paused ruleset, a force push and re-created clones. Rebase merges only, squash disabled in repo settings from the first commit.
- Any change to an input of a byte-for-byte check (vendor rules, cleaner, fixture) rebuilds the example in the same PR, offline. The offline rebuild is `write_receipts`, `expense_data`, `clean_receipts(fetch=False)`, `build_packet`; a networked rebuild rewrites `examples/image-cache/failed-images.json` and puts remote `src` values in the packet.
- Loosening a pattern ships with a test for the false positive it can now admit. The comma-decimal money regex read "60,000 points" as money and kept a promo block.
- One CSS scope per embedded document (`.rc.rN`). A column squeeze that looked like one vendor's bug was another vendor's styles leaking through a shared scope, and the first fix (a width override) only masked it.
- A vendor knowledge base earns its place by carrying insight (arrival lag, message kinds and supersession, tenders, line categories, what the receipt lacks), each read off a real scrubbed sample and enforced by a test. Fields that answer one question, or patterns nothing consumes, get cut.
- Harvested real samples need a scrub, then two independent privacy reviews, then the hashed denylist gate. The raw copies never leave the scratchpad.
- Measure before keeping: Headroom compressed 0 to 2 percent here and became a docs mention; a parallel pool with no speedup was removed.
- Renderer: never shrink a receipt below legibility to keep "one receipt per page"; continue onto the next page and write a page map the splice step reads.
- No mandatory browser download: drive the machine's Chrome or Edge through the Playwright channel, with Playwright's Chromium only as fallback, and a doctor script that names what is missing.
- A monthly spend limit killed six agents mid-wave, and two later agents stalled at the 600 second watchdog. Recover by reading leftovers (uncommitted diffs, scratch files) before re-dispatching.
- In worktrees, commit with `PATH="$PWD/.venv/bin:$PATH"` or the pre-commit hook is not found.



---

## Owner steps (from the cross-repo checklist)

| Repo | Step only the owner can do |
|---|---|
| boomerang | `TRAFFIC_TOKEN` (Administration read, Contents write, this repo only); flip public; social preview |

---

## boomerang (correction from the boomerang build session, 2026-09-17, checked against main at f245a93)

- The readmerlin session's seven fails were measured on a stale worktree at `20f01e9`. On main these are already gone: the Archify sentence, the host path pointer line, the "What you need" and "Contributing and license" headings, the "or the HTML master" link and the "fully synthetic" disclaimer. Main's sections are Features, How it works, Quick start, Configuration and security, Common workflows, How it compares. `flow.svg` on main uses a specific icon per card.
- Still true on main and worth the same PR as the clonometer adoption: no hero spec beside `assets/readme/flow.svg`, the vendors count badge has no `counts` source in a `readmerlin.json` (point it at what `tests/test_vendors.py` counts), the Python badge is not a link, a Quick start section exists (the newer shape puts the install line in the hero), and "How it compares" uses categories. Re-run `readmerlin check` against main before acting on any older finding.
- Six worktrees under `boomerang/.claude/worktrees/` belong to other sessions (clonometer, gangplank handoff, hero skill, README template, repo builder plan, a private consumer repo). None holds boomerang work. Remove them when those sessions are finished; this session removed only its own.


