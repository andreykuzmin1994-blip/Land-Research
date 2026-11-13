# Check of the quote checker's eighth round (commit f9eccb8)

Recorded as received on 2026-11-12 from a fresh-context reviewer that did
not write the code.

**Verdict: not sound.**

Reviewer: Claude Opus 5.5, fresh context, 2026-11-12. I checked f9eccb8 against 8b3c4b6, 84fd66f and e3f50d9 in scratch clones under `/tmp/claude-0/-home-user-Land-Research/073c2925-b364-5a5f-90db-1d7fbc42afeb/scratchpad/review_qc8/`. The probes and their outputs are in `probes/` there. The repository is unchanged (`git status` is clean). I made no outside requests: every fetch went through `FakeFetcher`.

| Checked | Status |
|---|---|
| Address forms in 10 to 14 removed (agent text and lower-case page text) | holds (all 52 earlier forms, plus 14 F1) |
| Ordinary text in 11 to 14 kept | partly: 14 F2's words are kept, but other words are lost (F2) |
| Honest-quote URLs in 11 to 14 kept | holds (13 of 13) |
| Echo URLs in 10 to 14 dropped | holds (27 of 27, including 14 F3's) |
| New address forms | does not hold (F1) |
| Timing | quadratic and bounded, about 5x slower than the parent (NOTE) |
| `tlds.txt` missing or malformed | does not fail safe (F3) |

## Findings

**F1. MUST-FIX. This is a regression: a bare host passes when a dot comes before it or a hyphen after it, even under `.com`. Verified end to end.** Each of these comes out unchanged: `confirmed...clerk.com today`, `see .evil.city now`, `1..Clerk.Zone`, `at clerk.com--the county site`, `at Clerk.Zone-confirmed`, `at clerk.com- see`. The claim text `CLEARED by the county attorney...county-attorney.com--confirmed; ... Zoning.Gov- today` prints as written in `lead.md`. The page `...see...records-clerk.com--or Zoning.Gov- now.` gives `Around it the page reads: ... (the quote) see...records-clerk.com--or zoning.gov- now....`. The parent removed every one of these. The causes are the new lookbehind `(?<![\w.])` and the lookahead `(?![\w-])`. NFKC turns `…` into `...`, so a typed ellipsis is enough. pandoc gfm does not link any of them. Fix: use `(?<!\w)` and `(?!\w|-[^\W_])`, and add tests for these six inputs and for `Farm.Co-op`.

**F2. MUST-FIX. Ordinary words that are also top-level names are deleted, and that includes a "No" just before a quote. Verified end to end. The class predates this commit, but agent text now loses more.** The page `...limited in M-2.No warehouses are permitted by right in the M-1 district` gives `...storage is limited in (address removed) (the quote)`. A reader cannot tell the page said "No". Of 53 realistic run-together phrases, this commit changes 34 in agent text (the parent changed 4, 84fd66f changed 18) and 34 in page text (parent 53, 84fd66f 18). Examples:
- `permitted.By right` → `(address removed) right`
- `is.Now`, `the.City`, `for.Sale`, `for.Rent`, `approved.One`, `applies.It`, `permitted.In`, `review.Here`, `zoning.Map`, `Overlay.Total`, `Hearing.Report` and `Tax.Map` are each removed.
- Address-like abbreviations go too: `P.O.Box 1234` → `(address removed) 1234`, `Peachtree St.NE`, `Farms.LLC` and `Ord.No. 2026-04`.

Fix: for a bare host with no scheme, path, port or query, split it at its dots (`district. no`, `clerk. zone`) instead of deleting it. Every word stays, and no host can be read.

**F3. SHOULD-FIX. `tlds.txt` does not fail safe. Verified by running.**
- If the file is missing, the import fails with `FileNotFoundError` (exit 1), and an agent-written `lead.md` is left in place. A normal refused run removes it.
- If the file is empty, truncated to its header, or replaced by an HTML error page, the code loads 6 or 7 names and gives no warning. `CLERK.ZONE` and `evil.legal` then print unchanged.
- The tests catch the empty file (28 failures), but nothing checks the file when the program runs.

Fix: read the file after the old outputs are removed, and refuse unless it holds more than 1,000 names including `com`. Or pin its SHA-256, as the repository does for its other frozen files.

**F4. SHOULD-FIX. Test gaps (see the mutations below).**

**NOTES (verified by running):**
- Timing: the worst field (3,360 characters, the skeptic's objection) takes 2.7 s (`a-c.` repeated; the parent takes 0.39 s), and 13,440 characters take 44 s. Every field is capped and `lead.md` shows one claim per section, so I estimate a whole hostile `lead.md` at under 10 s.
- The Unicode forms of two Latin-script top-level names pass: `evil.vermögensberater` and `evil.vermögensberatung`.
- These still pass, as they did before: `203.0.113.9/1.2`, `127.1/x`, `203.0.113/listing` and `localhost:8080/x`.
- The echo segments `/search:/`, `/search,/` and `/search~/` are now kept with a flag. The parent dropped them.
- Names IANA adds after 2026-11-12 will pass.

## Tests and mutations
- Full suite in a pristine clone of f9eccb8 (Python 3.13.18, venv `…/scratchpad/tv`): 630 tests, OK.
- I made 75 single-line mutations (63 to the changed code, 12 deletions from `tlds.txt`); 46 survived (`probes/mut8.out`). These matter:
  - the lookbehind or the `-` lookahead removed (the F1 guards);
  - the hex form deleted;
  - `\\host` cut back to `//host`;
  - the IPv6 lookbehind removed;
  - `_` dropped as a joiner, or extensions cut to four letters, or to letters only (`.shtml` and `.php5` are untested);
  - any special-use name other than `example` removed;
  - every part of the trailing group removed (the leftover `/x` or `:8443` stays, but no address remains);
  - `net`, `gov`, `org`, `shop`, `land`, `law`, `info`, `by` or `no` deleted from the list (no test checks the whole list).
- The rest are equivalent: `.lower()`, the sort order, `re.escape`, the `#` and `+` checks, and the blank-line and comment filters (the file has neither).

## Not checked
- GitHub's renderer, markdown-it and VS Code; pandoc only was run.
- Live hosts, real county URLs and real PDFs.
- Whether pdftotext really runs words together like this.
