# Hosts

Where each host reads a skill folder, and what changes about running boomerang
there. Every row below was checked against the linked page on 2026-09-16. If a
detail is not on this page, it was not verified, and the "Not verified" list at
the bottom says so.

## Install in one line

`npx skills add oficiallyAkshay/boomerang` installs this skill into Claude
Code, Cursor, Codex and about seventy other agents. It is the cross-agent
installer from Vercel Labs, and it reads a repository whose `SKILL.md` sits at
the root, which is where boomerang keeps it. `-a claude-code` installs into one
named agent, `-g` installs for every project rather than the current one, and
`-y` takes the defaults. Anthropic's documented manual path is the same install
done by hand: clone the repository, then copy the folder into
`~/.claude/skills/boomerang/`, or into `.claude/skills/boomerang/` to scope it
to one project. Either way the runtime is the same afterwards,
`pip install -r requirements.txt` or `uv sync`, and
`python scripts/doctor.py` says what is still missing.

The per-host paths below are for the manual path, and for the hosts the
installer does not reach.

Sources: <https://github.com/vercel-labs/skills> and
<https://code.claude.com/docs/en/skills>

## Install locations

| Host | Where the skill folder goes | Scripts | Email and calendar |
| --- | --- | --- | --- |
| Claude Code | `~/.claude/skills/boomerang/`, or `.claude/skills/boomerang/` inside a project | Runs them through its shell | Whatever MCP servers the user has configured |
| Claude.ai and the Claude desktop app | A zip of `SKILL.md`, `policy.md`, `requirements.txt`, `LICENSE`, `scripts/`, `references/` and `vendors/`, uploaded under Customize, Skills | Anthropic's sandbox, PyPI installs but no browser binary | The account's Gmail connector, if enabled |
| Cowork | Not independently confirmed | Not independently confirmed | Not independently confirmed |
| Codex | `.agents/skills/` from the cwd up to the repo root, plus `$HOME/.agents/skills` and `/etc/codex/skills` | Its exec tool | MCP declared in config only |
| OpenClaw | `<workspace>/skills`, `.agents/skills`, or `~/.agents/skills` | Its exec tool runs Python | A user-configured MCP server or CLI |
| Hermes | `~/.hermes/skills/`, or `.hermes/skills/` and `.agents/skills/` after `hermes skills trust` | Terminal and code execution toolsets | Not covered by the cited page |
| Cursor | `.cursor/skills/` or `.agents/skills/` in a project, `~/.cursor/skills/` or `~/.agents/skills/` for every project, and it also reads `.claude/skills/` | The agent terminal | A user-added MCP server |

## Per-host notes

### Claude Code

The skill folder goes in `~/.claude/skills/boomerang/` for every project, or in
`.claude/skills/boomerang/` to scope it to one project. Scripts run through the
host's own shell. Email and calendar come from whichever MCP servers the user
has configured; there is no built-in mail tool to assume.

Sources: <https://code.claude.com/docs/en/agent-sdk/skills> and
<https://platform.claude.com/docs/en/agents-and-tools/agent-skills/overview>

### Claude.ai and the Claude desktop app

The one-line installer does not reach this host, so zip the skill folder and
upload it under Customize, Skills. The zip must have the skill folder itself as
its root, and it should contain only what the host needs to run: `SKILL.md`,
`policy.md`, `requirements.txt`, `LICENSE`, `scripts/`, `references/` and
`vendors/`. Leave out the tests, the CI workflows, the examples and
`pyproject.toml`. The description field is limited to 200 characters, which is
why boomerang keeps its description under that everywhere.

Scripts run in Anthropic's sandbox. PyPI packages can be installed while the
skill is loading, but there is no Chrome or Edge to drive and a Chromium
browser binary cannot be downloaded, so `render_pdf.py` does not work here:
the deliverable is `packet.html`, which the user prints to PDF. Email is
available through the account's Gmail connector when that connector is
enabled.

Source: <https://support.claude.com/en/articles/12512198-how-to-create-custom-skills>

### Cowork

Cowork shares the Claude.ai sandbox, so the same browser limit is expected to
apply and the deliverable there is `packet.html` rather than a PDF. That is a
carried-over expectation, not a checked fact. No first-party page found so far
gives a Cowork-specific path for uploading a custom skill folder: the plugins
page names Customize for plugins and mentions uploading a custom plugin file,
which is not the same thing. The install path is therefore in the "Not
verified" list below.

Source, for what it does cover:
<https://support.claude.com/en/articles/13837440-use-plugins-in-claude>

### Codex

Codex scans `.agents/skills` from the working directory upwards to the repo
root, so a folder at any level in between is picked up, and it also reads
`$HOME/.agents/skills` and `/etc/codex/skills`. Scripts run through its exec
tool. Email is reachable only through an MCP server declared in the
configuration; there is no bundled mail capability.

Source: <https://learn.chatgpt.com/docs/build-skills>

### OpenClaw

Skill folders are read from `<workspace>/skills`, `.agents/skills`, and
`~/.agents/skills`. The exec tool runs Python, which is what boomerang's
scripts need. Email comes from a user-configured MCP server or a command-line
tool.

Sources: <https://docs.openclaw.ai/tools/skills> and
<https://docs.openclaw.ai/tools/exec>

### Hermes

Skill folders are read from `~/.hermes/skills/`, `.hermes/skills/`, and
`.agents/skills/`. A project-local folder has to be trusted first, with
`hermes skills trust`; the user-level `~/.hermes/skills/` needs no trust step.
Hermes exposes terminal and code execution toolsets, which cover the
`run-python` capability.

Source: <https://hermes-agent.nousresearch.com/docs/user-guide/features/skills>

### Cursor

The skill folder goes in `.cursor/skills/` or `.agents/skills/` inside a
project, or in `~/.cursor/skills/` or `~/.agents/skills/` for every project.
Cursor also reads `.claude/skills/`, so an existing Claude Code install is
picked up without copying. Scripts run through the agent terminal. Email comes
from an MCP server the user adds.

Source: <https://cursor.com/docs/context/skills>

## The open specification

The open skills specification requires `name` and `description`. The
specification allows a description of up to 1024 characters, but Claude.ai caps
it at 200, so this skill keeps 200 as the limit everywhere.

It also allows a `metadata` map of string values. That is where boomerang's
icon lives, because Anthropic's skill validator rejects a top-level key it
does not know.

Sources: <https://agentskills.io/specification> and
<https://code.claude.com/docs/en/skills>

## Not verified

These are open questions, not claims. Do not write them into the table until
someone checks them and cites a page.

- Cowork: where a custom skill folder is installed from. No first-party page
  found gives the path
- Cowork's execution sandbox: what runs, with what installed, and under what
  limits
- Codex: exactly how skill scripts are executed by the exec tool
- OpenClaw: whether any first-party email connector exists beyond a
  user-configured MCP server
- Hermes: whether any first-party email connector exists beyond a
  user-configured MCP server
- Cursor: whether any first-party email connector exists beyond a user-added
  MCP server
