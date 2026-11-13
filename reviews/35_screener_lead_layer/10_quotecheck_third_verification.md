# Third verification of `screener/quotecheck.py` (round 3)

Recorded as received on 2026-11-11 from a fresh-context reviewer that did
not write the code. One line was added by the launching session and is
marked as such, under "Tests and mutations".

**Verdict: sound after these fixes.**

Reviewer: Fable 5.2, fresh context, 2026-11-11, repo at 1b4deea. `08` read up to 763bcbf (it cites that commit's "address"→"request" wording), so this covers e52190a and 3295823. Nothing in the repo was edited (`git status` clean). No outside request. Fetching went through the test file's `FakeFetcher` seam, because the code refuses 127.0.0.1 by design. Rendering was checked with `pandoc` only (gfm, commonmark, markdown); GitHub's renderer was not available.

Evidence: `/tmp/claude-0/-home-user-Land-Research/073c2925-b364-5a5f-90db-1d7fbc42afeb/scratchpad/review_qc3/` (`probes/*.py`, `out/*.out`).

| | Status | What I ran |
|---|---|---|
| (a) inert `lead.md` (`08` F1, F2, F4) | partly | 20 hostile page quotes (backtick runs, trailing backslash, pipes, HTML, entities, fake closing quote) in three dialects: no link, each quote intact in one code element. Agent text: no `"` or markup survives. Addresses and look-alike quotes do (F3, F4). |
| (b) headline (`08` F6) | holds | 9 planted files (`headline.txt`, `lead.md`, `.part`, `pages/<name>.html` and others): `lead.md` identical. Residual F6. |
| (c) descriptive paths (`08` F7) | partly | Both path examples from `08` are kept. Its owner-name lookup is still dropped (F5). The narrowing reopened B1 for path echoes (F1). |
| (d) words around the quote (`08` F8) | partly | "isn't", "cannot be" and "never" after the quote now show. A "not" directly before the quote can still be lost (F2). |
| (e) echo (`06` B1, `08` N1) | partly | Query echoes with `+`, `%20`, mixed case, punctuation and fragment are dropped. Kept: path echoes, a redirect, a spell-corrected query. |

## Findings

**F1. MUST-FIX. Path echoes are kept, with no flag (regression of this round). Verified by running, old against new.**
- Claim `source_url` `https://city.example.gov/search/M-1+warehouses+allowed/`, page `<h1>Search Results for: M-1 warehouses allowed</h1>`, quote the same words. `lead.md`: ``- On the page: "`Search Results for: M-1 warehouses allowed`" (<https://city.example.gov/search/M-1+warehouses+allowed/>)``.
- `.../wiki/Special:Search/Warehouses_are_permitted_by_right_in_the_M-1_district` with a page that prints the words with spaces gives ``"`Warehouses are permitted by right in the M-1 district`"``.
- 763bcbf dropped both. Five of 17 cases went from dropped to kept (also hyphen slugs, dots, `%3F`).
- Older and still open: a cited `/go/7f3a` that redirects to `/search?q=warehouses+are+permitted...` is kept; the same page cited directly is dropped.
- Fix: run the rules on every address fetched, the final one included. For the path, at least list shared words in `words_shared_with_url` so the "show the request back" line prints.

**F2. MUST-FIX. The "not" just before a quote can be cut off. Verified by running.**
- Plain-text page `Lots & blocks & tracts zoned M-1 & M-2 are not permitted in the M-1 district by right.`, quote `permitted in the M-1 district by right`.
- `lead.md`: `- Around it the page reads: ...ts and blocks and tracts zoned m-1 and m-2 ar (cut) (the quote) ....`
- An e-mail address in front does the same: `...tact planning at (address removed). warehouse (cut) (the quote) ....`
- Cause: `_plain` lengthens the text, then keeps the first 45 characters. Fix: for the text before, keep the last 45 after reducing.

**F3. MUST-FIX. Some web addresses pass `_plain`. Output verified by running; GitHub linking by reading only.**
- Claim text `County file 1https://203.0.113.9/listing` is printed unchanged. So are `éhttps://203.0.113.9/x`, `1https://bücher.example/x`, `1https://intranet/x`, bare `203.0.113.9/listing` and `bücher.example/x`, in every agent field and in the "Around it" line from page text.
- Cause: the scheme pattern needs a word break before it; the host pattern knows only ASCII names ending in letters.
- pandoc does not link these. GitHub's matcher steps back over ASCII letters only, with no check of what comes before, so I expect a link from `https`.
- Fix: remove any run holding `:` `/` `/` whatever precedes it; treat dotted digits and non-ASCII labels as hosts.

**F4. SHOULD-FIX. Letters that look like double quotation marks pass. Verified by running.** `ʻʻwarehouses are permitted by rightʼʼ` (U+02BB, U+02BC), `ᐥclearedᐥ` (U+1425) and `ײ...ײ` (U+05F2) are printed unchanged. Fix: allow Latin-script letters only, which also narrows F3 and stops right-to-left text.

**F5. SHOULD-FIX. F7 is half closed. Verified by running.** `where=Owner+LIKE+'MAPCO+FARMS+LLC%'` quoting `"Owner":"MAPCO FARMS LLC "` is still dropped. So is an honest quote cited with a `#:~:text=` link. Say so in INVESTIGATE.md.

**F6. SHOULD-FIX. A refused run can leave an agent's `lead.md`. Verified by running.** With `findings.checked.json` as a non-empty folder and a hand-written `lead.md`: exit 2, `refused: the old findings.checked.json could not be removed (IsADirectoryError)`, and that `lead.md` is still there. A mistyped flag does the same. Fix: try both removals before refusing.

**NOTES (all run).**
- `parcel_id` `14F0001 - CLEARED zoning by right` passes: `# Lead check: parcel 14F0001 - CLEARED zoning by right`. Compare it with the folder name.
- A link carries free text: `<https://city.example.gov/code/article-9?note=VERIFIED-by-county-attorney#CLEARED-zoning-by-right-confirmed>`.
- `:+1:`, `:x:` and `$x$` in agent text render as emoji and maths in pandoc gfm.
- A PDF quote at the top of a page prints `... (the quote)` with nothing to show that the page before ended in "not". A negation more than 45 characters back is not shown.
- A `;`-separated query echo is kept but flagged.

## Tests and mutations

- `python -m unittest screener.tests.test_quotecheck`: 75 OK (Python 3.13.18).
- Full screener suite: not reproduced. `discover` gave 88 with 13 errors: every other module fails to import because `shapely` is not installed here, and installing it needs an outside host.
- *(Added by the launching session, not the reviewer: with `screener/requirements.txt` installed in a scratch virtual environment, the full suite in a fresh clone of 1b4deea ran 611 tests, OK, 3 skipped.)*
- 78 single-line mutations, 22 survived.
  - Untested guards: `_code` padding when a quote only ends in a backtick; `_plain` keeping `{}` or `^`; the U+02BA/U+02EE quote mapping; the once-decoded form of the 25-character rule (the only thing that catches a raw `+` path echo); `parcel_id` through `_plain`; `parcel_id` colon and length (each covered only by the other); the four-letter floor of the shared-words list; custom end tags; `label` and `tt` inline; U+2027; variation selectors supplement.
  - Equivalent or near: unescape once or never, no NFKC, `@` and `&` wording, one address pass, no `www` pattern, no gaps in the scheme pattern.

## Not checked

- GitHub's own renderer (F3 linking, emoji, maths) and VS Code's preview.
- Any live host; whether real county sites serve path-echo pages (F1 is shown on fake pages only).
- Real PDFs, DNS rebinding, the items `08` left open.
