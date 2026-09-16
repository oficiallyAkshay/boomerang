# Interfaces (wave 1 builders code against these; changes need the conductor)

All scripts are importable as top-level modules from scripts/ (pytest pythonpath) and runnable as
CLIs. Stdlib first; playwright and pypdf are the only runtime deps; google libs only inside
gmail_cli.py.

Each module's docstring is the reference for its own functions, so this page carries only the data
shapes and the command lines, which are the parts two scripts have to agree on.

## expense_data.json schema

```json
{ "company": str, "trip": str, "traveler": str, "currency": str (optional, default "USD"),
  "days": [ { "label": str, "items": [ { "desc": str, "amt": number, "rid": str } ] } ],
  "receipts": [ { "rid": str, "title": str, "vendor": str, "kind": "html"|"text"|"pdf"|"image" (optional; inferred from the file found) } ],
  "stipend": { "desc": str, "amt": number } (optional) }
```

rid matches `^[A-Za-z0-9_-]{1,64}$`. Every item rid must appear in receipts. Receipt files live in a
receipts dir as `<rid>.html | .txt | .pdf | .png | .jpg`, and a file has to resolve to somewhere
inside that dir, so a traversal in the data or a symlink on disk is never read. amt has at most 2
decimals. Every string that reaches the packet must be a non empty string free of the em dash and
of the standalone word "due": company, trip, traveler, each day label, each item desc, each receipt
title, and the stipend desc. vendor never reaches the packet and is checked for shape only.

## rules.json shape

Each vendor directory holds one, at `vendors/<name>/rules.json`.

```text
{ "name": str, "display": str, "sender_domains": [str], "subject_patterns": [regex], "strip_regex": [regex applied with re.S over the HTML], "unwrap_links_matching": [regex on href], "amount_regex": str|null, "date_regex": str|null, "notes": str }
```

## Command lines

```text
CLI: build.py DATA.json --receipts DIR --out packet.html
CLI: clean.py IN.html --out OUT.html [--vendor NAME] [--vendors DIR] [--images DIR] [--fetch-images]
CLI: cards.py RECEIPTS_DIR
CLI: fetch.py --start YYYY-MM-DD --end YYYY-MM-DD --out DIR [--vendors DIR] [--dry-run]
CLI: gmail_cli.py auth --client-secret PATH | search QUERY | get RID --out DIR ; token at ~/.config/boomerang/token.json chmod 600
CLI: render_pdf.py packet.html packet.pdf [--expect N]      # prints the page count; exits 2 when --expect differs
CLI: attach_pdf.py packet.pdf DATA.json --receipts DIR --out final.pdf          # prints the final page count
CLI: check_prose.py [--packet FILE]
```

`--fetch-images` is the only path that opens a socket. It needs `--images DIR` and downloads what
the cache is missing, from the cleaned fragment rather than the raw message.
