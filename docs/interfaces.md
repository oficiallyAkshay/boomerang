# Interfaces (wave 1 builders code against these; changes need the conductor)

All scripts are importable as top-level modules from scripts/ (pytest pythonpath) and runnable as
CLIs. Stdlib first; playwright and pypdf are the only runtime deps; google libs only inside
gmail_cli.py.

Each module's docstring is the reference for its own functions, so this page carries only the data
shapes and the command lines, which are the parts two scripts have to agree on.

## expense_data.json schema

```json
{ "company": str, "trip": str, "traveler": str, "currency": str (optional, default "USD"),
  "days": [ { "label": str, "items": [ { "desc": str, "amt": number, "rid": str,
    "refund": number (optional), "local_amt": number (optional), "local_currency": str (optional),
    "at": str (optional, ISO 8601 datetime) } ] } ],
  "receipts": [ { "rid": str, "title": str, "vendor": str, "kind": "html"|"text"|"pdf"|"image" (optional; inferred from the file found) } ],
  "stipend": { "desc": str, "amt": number, "rate": number (optional), "days": number (optional) } (optional) }
```

rid matches `^[A-Za-z0-9_-]{1,64}$`. Every item rid must appear in receipts. Receipt files live in a
receipts dir as `<rid>.html | .txt | .pdf | .png | .jpg | .jpeg`, and a file has to resolve to
somewhere inside that dir, so a traversal in the data or a symlink on disk is never read. amt has
at most 2 decimals. Every string that reaches the packet must be a non empty string free of the em
dash and of the standalone word "due": company, trip, traveler, each day label, each item desc,
each receipt title, and the stipend desc. vendor never reaches the packet and is checked for shape
only.

The packet does the arithmetic, so the optional fields state the parts rather than the answer.
`refund` is a number with at most 2 decimals, at least 0 and at most `amt`; the claimed value is
`amt - refund` and the row shows the amount with the refund netted. `local_amt` and
`local_currency` go together or not at all, and the row notes what the receipt itself shows. `at`
is an ISO 8601 datetime, and a value with no zone is read as UTC. A stipend states `amt`, or
`rate` and `days` together and the packet multiplies them; when it states all three, `amt` has to
equal `rate` times `days`. Totals are built from the claimed values throughout.

Two more things `validate` reports when it is given a receipts directory. A rid with more than one
file on disk is a problem unless `kind` names which one to use: `kind` wins, and the suffix order
`.html .txt .pdf .png .jpg .jpeg` decides only when there is no kind. An html receipt whose text
still holds `<script`, `<html`, `<head` or `on<word>=` reads as raw vendor mail and is a problem,
because a packet is built from the output of clean.py, not from the mailbox.

## rules.json shape

Each vendor directory holds one, at `vendors/<name>/rules.json`.

```text
{ "name": str, "display": str, "sender_domains": [str], "subject_patterns": [regex], "strip_regex": [regex applied with re.S over the HTML], "unwrap_links_matching": [regex on href], "amount_regex": str|null, "date_regex": str|null, "notes": str }
```

## Command lines

```text
CLI: build.py DATA.json --receipts DIR --out packet.html   # totals, then "pages expected N"
CLI: clean.py IN.html --out OUT.html [--vendor NAME|generic] [--vendors DIR] [--images DIR] [--fetch-images]
CLI: cards.py RECEIPTS_DIR      # reads .html, .txt and .pdf receipts, so folios are counted too
CLI: fetch.py --start YYYY-MM-DD --end YYYY-MM-DD --out DIR [--vendors DIR] [--dry-run]
  # writes <rid>.html, <rid>.txt, <rid>.meta.json; the first kept attachment of each kind is
  # <rid>.pdf | <rid>.png | <rid>.jpg and later ones of that kind <rid>.<n>.<ext>; a message
  # with no body and no kept attachment writes nothing and is named on stdout as empty
CLI: gmail_cli.py auth --client-secret PATH | search QUERY | get RID --out DIR ; token at ~/.config/boomerang/token.json chmod 600
CLI: render_pdf.py packet.html packet.pdf [--expect N]      # prints the page count; exits 2 when --expect differs
  # aborts every http and https request the page makes, so nothing is fetched while rendering;
  # names on stderr any receipt scaled below half size to fit its page
CLI: attach_pdf.py packet.pdf DATA.json --receipts DIR --out final.pdf          # prints the final page count
  # validates DATA.json against the receipts dir first and exits 2 listing the problems;
  # stamps the output /BoomerangSpliced and refuses a packet that already carries it
CLI: check_prose.py [--packet FILE]
```

`--fetch-images` is the only path that opens a socket. It needs `--images DIR` and downloads what
the cache is missing, from the cleaned fragment rather than the raw message.

`build.py` prints the totals line, then `pages expected N`, which is 1 plus the receipt count and
one more for every page the summary spills onto. On stderr it names any line whose claimed value is
not printed on its own receipt. Neither reading stops the build.

`clean.py` without `--vendor` reads `<input stem>.meta.json` beside the input, takes `from` and
`subject` from it, and prints the vendor it chose, or `generic`, to stderr. `--vendor generic`
forces the generic clean.
