# Check of the quote checker's seventh round (commit 8b3c4b6)

Recorded as received on 2026-11-12 from a fresh-context reviewer that did
not write the code.

**Verdict: sound after these fixes.**

Reviewer: Claude Opus 5.5, fresh context, 2026-11-12. I checked 8b3c4b6 against its parent 041d282 in scratch clones under `/tmp/claude-0/-home-user-Land-Research/073c2925-b364-5a5f-90db-1d7fbc42afeb/scratchpad/review_qc7/`. The probes and their outputs are in `probes/` there. I edited nothing in the repository (`git status` is clean) and made no outside requests: every fetch went through `FakeFetcher`, and `pdftotext` was mocked.

| Checked | Status |
|---|---|
| Every address form in notes 10 to 13 removed, in agent text and in lowercase page text | holds (52 forms) |
| Ordinary text from 11 to 13 kept (`4.2.1.3/4.2.1.4`, `$3.50/sf`, `S.E.1/4`, `1½`, `Łódź`…) | holds in agent text. Does not hold in page text (F2). |
| Honest-quote URLs in 11 to 13 kept | holds (13 of 13, the F3 slugs included) |
| Echo URLs in 10 to 13 dropped | holds (17 of 17, the F4 encodings and the redirect included) |
| New address forms | does not hold (F1) |
| New echo forms | partly (F3) |
| Backtracking | no worse than the parent |

## Findings

**F1. MUST-FIX. Upper-case and mixed-case bare hosts reach `lead.md`, and part of this is a regression. Verified end to end.** The claim text `CLEARED by County-Attorney.Legal; confirmed at CLERK.ZONE and Records.Farm and bücher.zone; see 203.0.113.9?id=7` prints unchanged. The regression: the two-letter top-level names are no longer matched without regard to case, so `EVIL.LY`, `Evil.Ly`, `Clerk.Us` and `COUNTY.GA` now pass, and the parent removed each one. Other forms that pass: `Evil.Zone./x`, `Evil.Zone%2Fx`, `Evil.Zone :8443`, `https:\\Evil.Zone\x` (printed as `https: Evil.Zone x`), and lower-case IDN names such as `bücher.zone`, which fail the ASCII `\b`. pandoc gfm links none of them (verified by running). Fix: use one case-insensitive copy of the IANA top-level-name list, with Unicode labels, in place of both `_TLDS` and the lower-case branch.

**F2. SHOULD-FIX. The lower-case rule eats ordinary words in every page context, a negation among them (regression). Verified by running.** Page text is case-folded, so `permitted.outdoor`, `st.louis`, `art.iv`, `ft.worth`, `u.s.hwy 29`, `4.2.permitted`, `m-1.warehouses`, `are.not` and `is not.permitted` all become `(address removed)`. The parent kept each one. End to end, the page `…district.except where not.allowed by overlay` gives `(the quote) .except where (address removed) by overlay, s (cut)`, so the "not" is lost. The fix for F1 also fixes this, because none of those words is a top-level name.

**F3. SHOULD-FIX. The F3 fix now keeps echoes that the parent dropped. They are kept with a flag, not silently. Verified by running.** The page "No results for m-1 warehouses allowed here" is kept, and flagged, behind each of these segments: `/search.json/`, `.nsf`, `.ashx`, `.shtml`, `.xml`, `.php5`, `/search-for/`, `/search-page/`, `/results-page/` and `search%3Bjsessionid=1`. The last one fails because the code splits on `;` before decoding. Fix: count a segment as a request when it matches `^(request word)([-_](request word|page|for))*(\.[a-z0-9]{1,6})*$` once decoded, before the `;` split.

**F4. SHOULD-FIX. Test gaps (see the mutations below).** Every test host is lower case, so the lower-case rule hides the named-host branch: each of its `/`, `?`, `#`, `:\d` and `:/` parts can be deleted alone, and the tests still pass. Only one IPv6 form is tested. Of the script names, only `php` is tested.

**NOTES (verified by running):**
- The port and query parts eat values in run-together abbreviations: `Max.Ht:45`, `Ord.No:2026-04` and `Ft.Worth?` each become `(address removed)`. The parent kept the numbers.
- The IPv6 branches eat `1:2:3:4:5 ratio` and `12:00:00:00:00`, and `fee::due` becomes `(address removed)ue`. `10:30` and `10:30:15` are kept.
- Numeric hosts the parent removed now pass: `203.0.113.9/1.2`, `127.1/x`, `203.0.113/listing` and `0xcb.0x0.0x71.0x9/listing`. `203.0.113.9?id=7` passes in both versions.
- These losses predate this commit: `St.Louis/Kansas`, `U.S.Hwy/29`, `$5.25 sq.ft/yr`, `Sec. 4.2.1.3/a`, `Owner.Name`, `Union.City`.
- Two checks in `spaced` are redundant: `"+" in seg` and `"+" in _decoded(seg)`, because `_decoded` already turns `+` into a space. The `_path_words` docstring still says "+ or %20".

## Tests and mutations
- Full suite in a pristine clone of 8b3c4b6 (Python 3.13.18, venv `…/scratchpad/tv`): 629 tests, OK. The clone was still clean afterwards.
- I made 50 single-line mutations to the changed lines; 21 survived (`probes/mut7.out`). These matter:
  - each of the five named-host suffixes deleted alone;
  - the full IPv6 form deleted, or `{4,7}` changed to `{5,7}`;
  - the `::x` form deleted;
  - `aspx`, `jsp`, `do`, `html` or `pl` deleted;
  - `head[0] in REQUEST_PATH_WORDS` deleted;
  - the `xn--` top-level form deleted;
  - `_LABEL` made ASCII-only.
- The rest are equivalent or harmless: the two `+` checks, a `\b` deleted from the lookahead or from the lower-case rule, the spaces in the `//` branch, and `-` deleted from the labels.

## Not checked
- GitHub's renderer, markdown-it and VS Code (none is installed). The linking claims are by reading; pandoc only was run.
- Live hosts, real county URLs and real PDFs.
