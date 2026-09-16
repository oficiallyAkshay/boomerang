# Hosts

Where each host reads a skill folder, and what changes about running boomerang
there. Every row below was checked against the linked page on 2026-09-16. If a
detail is not on this page, it was not verified, and the "Not verified" list at
the bottom says so.

## Install locations

| Host | Where the skill folder goes | Scripts | Email and calendar |
| --- | --- | --- | --- |
| Claude Code | `~/.claude/skills/boomerang/`, or `.claude/skills/boomerang/` inside a project | Runs them through its shell | Whatever MCP servers the user has configured |
| Claude.ai and the Claude desktop app | Upload the folder as a zip under Settings, Capabilities | Anthropic's sandbox | The account's Gmail connector, if enabled |
| Cowork | Customize, Skills, add a skill folder | Not independently confirmed | Not independently confirmed |
| Codex | `.agents/skills/` in the project, or `~/.agents/skills/` | Its exec tool | MCP declared in config only |
| OpenClaw | `<workspace>/skills`, `.agents/skills`, or `~/.agents/skills` | Its exec tool runs Python | A user-configured MCP server or CLI |
| Hermes | `~/.hermes/skills/`, `.hermes/skills/`, or `.agents/skills/` | Terminal and code execution toolsets | Not covered by the cited page |
| Cursor | `.cursor/skills/` or `.agents/skills/`, and it also reads `.claude/skills/` | The agent terminal | A user-added MCP server |

## Per-host notes

### Claude Code

The skill folder goes in `~/.claude/skills/boomerang/` for every project, or in
`.claude/skills/boomerang/` to scope it to one project. Scripts run through the
host's own shell. Email and calendar come from whichever MCP servers the user
has configured; there is no built-in mail tool to assume.

Sources: <https://code.claude.com/docs/en/agent-sdk/skills> and
<https://platform.claude.com/docs/en/agents-and-tools/agent-skills/overview>

### Claude.ai and the Claude desktop app

Zip the skill folder and upload it under Settings, Capabilities. The
description field is limited to 200 characters, which is why boomerang keeps
its description under that everywhere.

Scripts run in Anthropic's sandbox, where extra packages cannot be installed at
runtime. PDF rendering may therefore need the fallback path rather than a fresh
Chromium install. Email is available through the account's Gmail connector when
that connector is enabled.

Source: <https://support.claude.com/en/articles/12512198-how-to-create-custom-skills>

### Cowork

Customize, then Skills, then add a skill folder. The same 200-character
description limit applies. How Cowork executes skill scripts is not
independently confirmed; assume nothing about the sandbox until it is checked.

Source: <https://support.claude.com/en/articles/12512198-how-to-create-custom-skills>

### Codex

The skill folder goes in `.agents/skills/` inside the project, or in
`~/.agents/skills/` for every project. Scripts run through its exec tool. Email
is reachable only through an MCP server declared in the configuration; there is
no bundled mail capability.

Source: <https://developers.openai.com/codex/skills/>

### OpenClaw

Skill folders are read from `<workspace>/skills`, `.agents/skills`, and
`~/.agents/skills`. The exec tool runs Python, which is what boomerang's
scripts need. Email comes from a user-configured MCP server or a command-line
tool.

Sources: <https://docs.openclaw.ai/tools/skills> and
<https://docs.openclaw.ai/tools/exec>

### Hermes

Skill folders are read from `~/.hermes/skills/`, `.hermes/skills/`, and
`.agents/skills/`. Hermes exposes terminal and code execution toolsets, which
cover the `run-python` capability.

Source: <https://hermes-agent.nousresearch.com/docs/user-guide/features/skills>

### Cursor

The skill folder goes in `.cursor/skills/` or `.agents/skills/`, and Cursor
also reads `.claude/skills/`, so an existing Claude Code install is picked up
without copying. Scripts run through the agent terminal. Email comes from an
MCP server the user adds.

Source: <https://cursor.com/docs/context/skills>

## The open specification

The open skills specification requires `name` and `description`. The
specification allows a description of up to 1024 characters, but Claude.ai caps
it at 200, so this skill keeps 200 as the limit everywhere.

Source: <https://agentskills.io/specification>

## Not verified

These are open questions, not claims. Do not write them into the table until
someone checks them and cites a page.

- Cowork's execution sandbox: what runs, with what installed, and under what
  limits
- Codex: exactly how skill scripts are executed by the exec tool
- OpenClaw: whether any first-party email connector exists beyond a
  user-configured MCP server
- Hermes: whether any first-party email connector exists beyond a
  user-configured MCP server
- Cursor: whether any first-party email connector exists beyond a user-added
  MCP server
