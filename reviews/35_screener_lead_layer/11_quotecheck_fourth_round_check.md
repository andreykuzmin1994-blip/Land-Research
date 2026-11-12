# Check of the quote checker's fourth round (commit eb3a733)

Recorded as received on 2026-11-12 from a fresh-context reviewer that did
not write the code.

**Verdict: sound after these fixes.**

Reviewer: Claude Opus 5.5, fresh context, 2026-11-12. I checked eb3a733 in scratch clones only and edited nothing in /home/user/Land-Research. I made no outside requests: every fetch went through the test file's `FakeFetcher`, and PDFs went through a mocked `pdftotext`. The probes and their outputs are in `/tmp/claude-0/-home-user-Land-Research/073c2925-b364-5a5f-90db-1d7fbc42afeb/scratchpad/review_qc4/probes/`. I ran each probe against eb3a733 and against its parent (d1ba865).

| Claim | Status | What I ran |
|---|---|---|
| F1, typed path held to the three-words rule | partly | All `10` path echoes are now dropped: `/search/…+…`, `Special:Search/…`, hyphen and dot slugs, `%3F`, split words. But "typed" goes wider than the commit and INVESTIGATE.md say (see S1). |
| F1, 40 characters whatever separates them | holds | Hyphen, dot, underscore and `+` slugs of 40 characters or more are dropped. |
| F1, every redirect hop and the final address | holds | `10`'s `/go/7f3a` → `/search?q=…` is now dropped. So are a typed middle hop in a 3-hop chain and a relative `Location`. |
| F1, a run shared with a plain path is flagged | holds | `/tag/warehouses-allowed-here` and `/public-works/water-and-sewer` are kept and flagged. |
| F2, the words before a quote | holds | `10`'s `&` page now gives `...blocks and tracts zoned m-1 and m-2 are not (the quote)`. The e-mail case, a URL in front and a 100-character word in front all keep the "not". |
| F2, PDF quote at the top of a page | holds | `...warehouses on this land are not (page break) (the quote)`. Text is taken from the same order only. |
| F3, addresses in `_plain` | holds | All 8 of `10`'s cases now print `(address removed)`. |
| F4, Latin letters only | partly | U+02BB, U+1425 and U+05F2 are gone. The Saltillo letters pass (S2). |
| F6, refused or mistyped run | holds | A folder in place of the checked file, and `--with-skeptik`: `lead.md` is removed in both. |
| `parcel_id` must match its folder | holds | One NOTE below. |
| A link shows no fragment | holds | |
| Earlier holes | still closed | `06` B1 (geometry textarea, "No results" page) is dropped. The `08` F7 examples are kept. The `10` `;` query echo is now dropped. |

## Findings

**S1. SHOULD-FIX. Honest quotes are now dropped before any fetch (regression). Verified by running.** All 7 cases below were kept at d1ba865. All are now dropped with "the quote repeats words from its own source_url".
- File names with `%20` or `+`:
  - `…/Documents/Article%2012%20Light%20Industrial.pdf`, quote "Article 12, Light Industrial: warehouses are permitted by right".
  - S3 key `/Zoning+Map+Amendments+2026.pdf`, quote "Zoning map amendments, 2026: parcel 14F0001 0001 rezoned to M-1".
  - A redirect that lands on such a file is dropped too.
- A request word anywhere in a slug, not as its own segment:
  - `/planning/hearing-results/2026-03-board-of-commissioners-zoning-hearing`, quote "The board of commissioners approved RZ-26-004…".
  - `/2026/results-of-rezoning-vote-on-campbellton-road`.
  - `/2026/county-seeks-to-find-new-industrial-park-site`.
- An ArcGIS `/query` path whose service name is in the answer: `…/services/Fulton_County_Zoning_Districts/MapServer/2/query?…`, quote `"DISTRICT":"Fulton County Zoning District M-1"`.

The failure is in the safe direction (a quote is dropped, never kept), but it often hits the most relevant sentence of a code PDF. The agent cannot cite the file another way: spaces are refused and redirect hops are held to the same rules. INVESTIGATE.md says "a `search`… segment", but the code tests every word in the path.

Fix: count a request word only when it is a whole path segment (or the segment before the typed words). Treat a `%20` or `+` in the last segment as a file name when it has a document extension (.pdf, .docx and so on): flag it, do not drop it.

**S2. SHOULD-FIX. Look-alike double quotation marks still pass. Verified by running.** Claim text `ꞌꞌWarehouses are permitted by right, confirmed by the county attorneyꞌꞌ` (U+A78C, which is a Latin letter) gives `- ꞌꞌWarehouses are permitted by right, confirmed by the county attorneyꞌꞌ (zoning-1)`. U+A78B and the clicks U+01C0 to U+01C3 pass too. Fix: map the Saltillo letters to `'` before the `'{2,}` collapse, or keep only Latin letters that reduce to an ASCII letter plus marks.

**S3. SHOULD-FIX. Test gaps on the new guards (see mutations).**

**NOTES (all verified by running):**
- Echoes the design allows:
  - Plain-path echoes under 40 characters are kept and flagged.
  - `SearchResults.aspx/…` and `/s;q=…` are kept and flagged.
  - The `08` N1 short-word path `use-is-ok-by-law-in-m1-and-m2-yes` is kept with no flag.
- `_plain` now eats some ordinary text:
  - `Sec. 4.2.1.3/4.2.1.4` → `Sec. (address removed)`.
  - `Café.Résumé` → `(address removed)`.
  - `Note: // see` → `(address removed)`.
- `_plain` problems that predate this commit:
  - `permitted.Outdoor`, `Art.IV` and `St.Louis` are removed as addresses.
  - `§` is lost.
  - `1½ acres` becomes `11 2 acres`, which can be misread. Fix: map U+2044 to `/`.
- Running from inside the lead folder (`findings.json` or `./findings.json`) is refused: `folder name ''`. Fix: compare `path.resolve().parent.name`.
- A run on a file not named `findings.json` (for example `findings.JSON`) is refused, but the old `lead.md` is left in place.
- PDF page breaks:
  - A page footer between "not" and the quote hides the "not": `...resolution, page 11 of 40 (page break) (the quote)`.
  - A quote at the bottom of a page shows nothing of the next page.

## Tests and mutations

- `screener/tests/test_quotecheck.py`: 83 tests, OK.
- Full suite in a pristine clone of eb3a733 (Python 3.13.18, the venv at `…/scratchpad/tv`): 619 tests, OK, 3 skipped. The clone was still clean afterwards.
- 66 single-line mutations of the changed lines; 24 survived (`probes/mut.out`). These matter:
  - Dropping the `+` trigger alone, or the `%20` trigger alone.
  - `REQUEST_PATH_WORDS` cut to `{search, results}`.
  - The two-four-letter-word floor in `_shared_runs` (`>= 1`).
  - The 40-character rule tried at offset 0 only.
  - `_addresses` skipping the middle hops (`[2:]`).
  - The page-break guard: no length check, or the previous page taken from the reading order whatever the match's order.
  - The page-break text taken from the start of the previous page instead of its end.
  - `{` kept by `_plain`.
  - A dangling `lead.md` symlink no longer removed.
  - A case-insensitive folder compare.
- The rest of the survivors (about 11) give the same output, or a harmless difference such as `bü(address removed)`.

## Not checked

- GitHub's renderer and VS Code's preview (no rendering check this round).
- Live hosts, real county URL patterns, and real PDFs through `pdftotext`.
- DNS rebinding, and the items `08` left open.
