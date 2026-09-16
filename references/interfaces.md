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
{ "name": str, "display": str, "sender_domains": [str], "subject_patterns": [regex], "strip_regex": [regex applied with re.S over the HTML], "unwrap_links_matching": [regex on href, vendor specific only], "replace": [[regex, re.sub replacement]] (optional), "notes": str }
```

`unwrap_links_matching` carries only the tracking shapes that are a vendor's own. The four every
vendor sends, `click\.`, `/track`, `utm_` and `email\.`, are `clean.GENERIC_UNWRAP` and apply to
every receipt, generic ones included. Every pattern in `strip_regex`, `subject_patterns`,
`unwrap_links_matching` and `replace` is compiled when the rules load, so a pattern that does not
compile names its key and its index instead of raising halfway through a receipt. `replace` pairs
run before every pass that sanitises, so a replacement cannot put anything active into a fragment.

## Command lines

```text
CLI: build.py DATA.json --receipts DIR --out packet.html   # totals, then "pages expected N"
CLI: clean.py IN.html --out OUT.html [--vendor NAME|generic] [--vendors DIR] [--images DIR] [--fetch-images]
CLI: clean.py --dir RECEIPTS --out CLEAN [--vendors DIR] [--images DIR] [--fetch-images]
  # cleans every .html in RECEIPTS, one after another in filename order, reading each file's vendor
  # from its own <rid>.meta.json; .txt, .pdf, .png and .jpg are copied through unchanged and
  # everything else, meta files included, stays behind. One "clean: <file> vendor <name>" line
  # per receipt on stderr, in filename order, and the amount guard warnings in that order too
CLI: cards.py RECEIPTS_DIR      # reads .html, .txt and .pdf receipts, so folios are counted too
CLI: fetch.py --start YYYY-MM-DD --end YYYY-MM-DD --out DIR [--vendors DIR] [--dry-run] [--workers N]
  # runs the two passes as concurrent queries and merges the ids by query index, not by which
  # query answered first; --workers is how many bodies are fetched at once, 3 by default
  # writes <rid>.html, <rid>.txt, <rid>.meta.json; the first kept attachment of each kind is
  # <rid>.pdf | <rid>.png | <rid>.jpg and later ones of that kind <rid>.<n>.<ext>; a message
  # with no body and no kept attachment writes nothing and is named on stdout as empty
CLI: gmail_cli.py auth --client-secret PATH | search QUERY | get RID --out DIR ; token at ~/.config/boomerang/token.json chmod 600
CLI: render_pdf.py packet.html packet.pdf [--expect N]      # prints the page count; exits 2 when --expect differs
  # names the browser it rendered with on stderr; render() returns (pages, channel)
  # aborts every http and https request the page makes, so nothing is fetched while rendering;
  # names on stderr any receipt scaled below half size to fit its page
CLI: attach_pdf.py packet.pdf DATA.json --receipts DIR --out final.pdf          # prints the final page count
  # validates DATA.json against the receipts dir first and exits 2 listing the problems;
  # stamps the output /BoomerangSpliced and refuses a packet that already carries it
CLI: check_prose.py [--packet FILE]
  # with no flag: scans every tracked text file, UTF-16 ones included, for the em dash and the
  # hashed denylist (runs of up to 4 words), resolving the repo root and the denylist from its
  # own path so it runs from anywhere, and exiting 1 when the denylist is missing or empty
  # --packet FILE scans only that file, and adds the banned word, data-rid= and href="#
```

`--fetch-images` is the only path that opens a socket. It needs `--images DIR` and downloads what
the cache is missing, from the cleaned fragment rather than the raw message.

`build.py` prints the totals line, then `pages expected N`, which is 1 plus the receipt count and
one more for every page the summary spills onto. On stderr it names any line whose claimed value is
not printed on its own receipt. Neither reading stops the build.

`clean.py` without `--vendor` reads `<input stem>.meta.json` beside the input, takes `from` and
`subject` from it, and prints the vendor it chose, or `generic`, to stderr. `--vendor generic`
forces the generic clean, and `--vendor` is refused with `--dir`, which reads every meta file
itself.

## The stage that runs three ways

Cleaning, the card fingerprint and the folio read all take the receipts directory and write
nothing the other two need, so they run at once. `cards.py` and `attach_pdf.extract_text` need
no argument they did not already have; the cleaner's directory form is:

```text
clean.clean_dir(src_dir, out_dir, vendors_dir, image_cache=None, fetch_images=False)
  -> list[tuple[str, str]]   # (filename, vendor or "generic") per .html, in filename order
```

It raises `ValueError` when `fetch_images` is asked for with no `image_cache`. `fetch.fetch_all`
takes a `workers=3` argument, and its four lists stay in first seen order: a message the source
refuses is named on stdout and lands in `failed`, so the four lists always account for every rid.
