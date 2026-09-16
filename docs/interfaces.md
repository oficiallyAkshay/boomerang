# Interfaces (wave 1 builders code against these; changes need the conductor)

All scripts are importable as top-level modules from scripts/ (pytest pythonpath) and runnable as
CLIs. Stdlib first; playwright and pypdf are the only runtime deps; google libs only inside
gmail_cli.py.

## expense_data.json schema

```json
{ "company": str, "trip": str, "traveler": str, "currency": str (optional, default "USD"),
  "days": [ { "label": str, "items": [ { "desc": str, "amt": number, "rid": str } ] } ],
  "receipts": [ { "rid": str, "title": str, "vendor": str, "kind": "html"|"text"|"pdf"|"image" (optional; inferred from the file found) } ],
  "stipend": { "desc": str, "amt": number } (optional) }
```

rid matches `^[A-Za-z0-9_-]{1,64}$`. Every item rid must appear in receipts. Receipt files live in a
receipts dir as `<rid>.html | .txt | .pdf | .png | .jpg`. amt has at most 2 decimals. Every string
that reaches the packet must be a non empty string free of the em dash and of the standalone word
"due": company, trip, traveler, each day label, each item desc, each receipt title, and the stipend
desc. vendor never reaches the packet and is checked for shape only.

## build.py

```text
validate(data: dict, receipts_dir: Path | None = None) -> list[str]   # empty list means valid; each entry is a human-readable problem. With receipts_dir, a declared kind that disagrees with the file on disk is reported, and so is a pdf that cannot be opened, as `receipt <rid>: pdf cannot be read`. A receipt with no file at all is never a problem
load_expense_data(path: Path) -> dict        # raises ValueError(joined problems)
money(x: float, currency: str = "USD") -> str
render_packet(data: dict, receipts_dir: Path) -> str   # full HTML; summary table then one <section class="rsec" data-rid=...><div class="rhead">..</div><div class="rc">..</div></section> per receipt in receipts order; pdf-kind receipts render a card stating the attachment is embedded in the PDF; image-kind receipts render an <img> data URI
totals(data: dict) -> dict                   # {"days": [(label, subtotal)], "expenses": x, "stipend": y, "total": z}; every amount is a Decimal quantized to cents, so callers that want floats convert at the edge
CLI: build.py DATA.json --receipts DIR --out packet.html
```

## clean.py

```text
load_vendor_rules(vendors_dir: Path) -> dict[str, dict]      # vendors/<name>/rules.json
detect_vendor(from_addr: str, subject: str, rules: dict) -> str | None
clean_html(raw: str, rules: dict | None = None, image_cache: Path | None = None) -> str
inline_images(html: str, cache_dir: Path) -> str            # cache file name = sha256(url).hexdigest()[:16]; no network when file missing, leave src as is
CLI: clean.py IN.html --out OUT.html [--vendor NAME] [--vendors DIR] [--images DIR] [--fetch-images]
--fetch-images is the only path that opens a socket; it needs --images DIR and downloads what the cache is missing before inlining
rules.json: { "name": str, "display": str, "sender_domains": [str], "subject_patterns": [regex], "strip_regex": [regex applied with re.S over the HTML], "unwrap_links_matching": [regex on href], "amount_regex": str|null, "date_regex": str|null, "notes": str }
```

## cards.py

```text
find_last4(text: str) -> set[str]
fingerprint(receipts_dir: Path) -> dict[str, list[str]]     # last4 -> [rid...]
singletons(fp: dict) -> set[str]                            # last4 seen on exactly one receipt
CLI: cards.py RECEIPTS_DIR
```

## fetch.py

```text
@dataclass Query: terms: list[str]; from_domains: list[str]; after: date; before: date  (after/before already padded)
build_queries(window_start: date, window_end: date, vendor_rules: dict) -> list[Query]   # pass 1 generic terms, pass 2 one per vendor; pad one day each side
to_gmail(q: Query) -> str                                    # Gmail search syntax
fetch_all(source, queries: list[Query], out_dir: Path) -> list[str]   # a list subclass of the rids written, carrying .rejected (rids refused by the id pattern) and .skipped (rids whose meta file was already on disk); source.search(query_str)->list[str]; source.get(rid)->{"html": str|None, "text": str|None, "from": str, "subject": str, "date": str, "attachments": [(filename, bytes)]}; writes <rid>.html/.txt/.meta.json and attachments as <rid>.<n>.<ext>; one message at a time; skips rids already on disk
CLI: fetch.py --start YYYY-MM-DD --end YYYY-MM-DD --out DIR [--vendors DIR] --source gmail
```

## gmail_cli.py

```text
class GmailSource: search(query: str) -> list[str]; get(rid: str) -> dict (shape above)
CLI: gmail_cli.py auth --client-secret PATH | search QUERY | get RID --out DIR ; token at ~/.config/boomerang/token.json chmod 600
```

## render_pdf.py

```text
render(html_path: Path, pdf_path: Path) -> int              # page count after render
verify_pages(pdf_path: Path, expected: int) -> bool
CLI: render_pdf.py packet.html packet.pdf [--expect N]      # prints the page count; exits 2 when --expect differs
```

## attach_pdf.py

```text
extract_text(pdf_path: Path) -> str
splice(packet_pdf: Path, data: dict, receipts_dir: Path, out_pdf: Path) -> int   # inserts each pdf-kind receipt's pages right after its card page; card page index = summary_pages + receipt_index where summary_pages = total_pages - len(receipts); returns final page count
CLI: attach_pdf.py packet.pdf DATA.json --receipts DIR --out final.pdf          # prints the final page count
```

## check_prose.py

```text
scan(files: list[Path], denylist: set[str]) -> list[str]     # error strings
CLI: check_prose.py [--packet FILE]
```

## tests/fixtures/make_fixture.py

```text
make(out_dir: Path, seed: int = 1) -> Path
make_vendor_samples(out_dir: Path) -> None
```
