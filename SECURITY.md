# Security

## Supported versions

The current `main` branch and the latest tag. Nothing older gets a fix.

## Reporting a vulnerability

Use GitHub's private vulnerability reporting on this repository:
https://github.com/oficiallyAkshay/boomerang/security/advisories/new

Never open a public issue for a vulnerability. Expect an acknowledgement
within seven days.

## Scope

boomerang turns a personal inbox into a reimbursement packet: it searches
your mailbox for receipts, cleans the vendor's own markup down to the part a
packet can show, and renders one PDF on your own machine. No credential is
required for the core flow; the optional Gmail fallback needs a client
secret you store yourself, at mode 600 inside a mode 700 directory in your
home config folder.

- ✅ reads your own mailbox, through the host's own mail tool by default
- ✅ renders the packet on your own machine, in a local browser (Chrome,
  then Edge, then Chromium, or whatever `BOOMERANG_BROWSER` names)
- ✅ strips every vendor receipt down to inert markup before it reaches a
  page: `<script>` blocks, inline handlers, tracking pixels, iframes and
  every other element or attribute that could act, whatever shape its tags
  come in, unterminated or not
- ✅ checks every commit with a deterministic prose-and-privacy gate, never
  a model call
- ❌ does not write to or delete anything in your mailbox
- ❌ does not send a receipt or the finished packet anywhere but where you
  send it yourself
- ❌ does not load a script, pixel or tracking link when the packet is
  opened
- ❌ does not let a vendor email run code
- ❌ does not send telemetry

If you find a way for a vendor email to run code, load a remote resource,
or leak a receipt or a credential outside the machine that renders it, that
is a vulnerability report, not a bug report.
