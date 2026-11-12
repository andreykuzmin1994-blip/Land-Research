# Check of the quote checker's sixth round (commit 84fd66f)

Recorded as received on 2026-11-12 from a fresh-context reviewer that did
not write the code. Not yet acted on.

**Verdict: sound after these fixes.**

Reviewer: fresh context, 2026-11-12. I checked 84fd66f against its parent e3f50d9 in scratch clones under `/tmp/claude-0/-home-user-Land-Research/073c2925-b364-5a5f-90db-1d7fbc42afeb/scratchpad/review_qc6/`; probes and outputs are in `probes/`. Nothing in the repository was edited, and every fetch went through `FakeFetcher` (real `pdftotext` on hand-made PDFs).

| Checked | Status |
|---|---|
| All `10`/`11`/`12` address forms removed by `_plain` | holds (all 14 F1 inputs, U+2236/·/ː, `_://`, `⁄⁄`, `verify.shop/ok`) |
| Invisible/format characters, fullwidth, IDN with path, uppercase, trailing punctuation | holds |
| Bare hosts under unlisted names, host:port, host?query, user@host | does not hold (F2) |
| Ordinary text kept | does not hold (F1) |
| F2 request segments (`search.php`, `search-results`, `;jsessionid`, `%2F`) | holds; drops honest quotes (F3) |
| PDF-only exemption, both directions | holds |
| F3 fractions, kept Latin letters | holds (one NOTE) |
| Backtracking | quadratic, bounded (NOTE) |

## Findings

**F1. MUST-FIX. Numbers with a slash are removed as addresses (regression). Verified by running; the parent kept each.** `Sec. 4.2.1.3/4.2.1.4 of the code` → `Sec. (address removed) of the code`. Likewise `See Sec. 27-4.3/27-4.4 and Table 4.1/4.2.`, `FAR 0.5/1.0`, `$3.50/sf` → `$(address removed)`, `18.2/acre`, `1.5/2.0 stories`, `PIN 14F.0001.0001/A`, `Rev. 2.1/2026`, `Exh. A.1/A.2`, `Fig. 3.a/3.b`. Survey legal descriptions are lost too: `the S.E.1/4 of the N.E.1/4` → `the (address removed) of the (address removed)`. The cause is the new `_HOST + "/\S*"` branch. The commit changed the test from `Sec. 4.2.1.3/4.2.1.4` to `Sec. 4.2.1.3 and 4.2.1.4`, so the test no longer catches this. Fix: in the host-with-path branch, require the last label to be letters only, two or more (`[^\W\d_]{2,}` or `xn--…`). Then restore the original test.

**F2. MUST-FIX (by the stated rule; it predates this commit, since e3f50d9). Readable addresses still reach `lead.md`. Verified end to end.** Claim text `CLEARED, confirmed by county-attorney.legal and records@clerk.zone, see clerk.zone:8443 or clerk.zone?id=7 or [2001:db8::7]:443` printed as `- CLEARED, confirmed by county-attorney.legal and records at clerk.zone, see clerk.zone:8443 or clerk.zone?id=7 or 2001:db8::7 :443 (zoning-1)`. Page words also give `...user at records.zone; not (the quote) details at evil.zone:443 and evil.zone?parce...`. The same holds for any name not in `_TLDS` (`.church`, `.legal`, `.realestate`, `.farm`, `.news`, `.zip`, `xn--bcher-kva.zone`), and for `evil.zone#x` and `evil.zone:/x`. eb3a733 removed these. By reading, none is autolinked by GitHub or linkify-it. Fix: treat a host followed by `[/?#]` or `:\d` as an address (with F1's letters-only last label). For bare hosts, use the IANA list or any lowercase all-letter last label; either keeps `permitted.Outdoor`.

**F3. SHOULD-FIX. A slug that begins with a request word now drops honest quotes before any fetch (regression; safe direction). Verified by running.** These were kept and flagged at e3f50d9; now each is dropped: `/find-your-zoning/zoning-districts/m-1-light-industrial` ("M-1 Light Industrial District: warehouses are permitted by right."), `/results-of-rezoning-vote/attachments/rz-26-004-staff-report`, `/lookup-tools/…`, `/query-builder/…`, `/Planning/Search-Zoning-Cases/RZ-26-004-Campbellton-Road`. This is `11` S1 again. Fix: count a segment as a request only when all its words are request words, script extensions (`php`, `aspx`…) or `;` parameters.

**F4. SHOULD-FIX. Encoded spaces other than a raw `+` or `%20` do not mark a path segment as typed. Verified by running; predates this commit.** `/s/warehouses%09allowed%09here`, `%C2%A0`, `%E3%80%80`, `%2B` and `%252B` all keep "No hits: warehouses allowed here". Each is flagged, not dropped. Fix: decide `spaced` from the fully decoded segment (any whitespace or `+`).

**F5. SHOULD-FIX. Test gaps (mutations below).**

**NOTES (verified by running):**
- Backtracking: `_ADDRESS` is quadratic. 20,000 characters of `a.` take 20 s (the parent took 11.5 s). `_plain` cuts input to 4×limit, so the worst field takes 0.5 s and a whole `lead.md` of capped fields about 1.7 s.
- PDF exemption: HTML served as `application/pdf`, HTML with `%PDF-` in front, and a `profile=pdf` type are all dropped (pdftotext fails). A real PDF served as `text/html` is kept. A server that renders the request into a real PDF is still kept by design. A `%20` `.txt` or `.csv` name with an honest quote is now dropped.
- `1¹⁄₂` still reads `11/2`. The after-context is cut before it is reduced, so a host cut short shows (`see county-records.cit...`).
- The new names remove run-on PDF words: `in Union.City Council` → `in (address removed) Council`; also `permitted.Land` and `Owner.Name`.
- The spaced-scheme branch (line 999) is redundant with the `//host` branch.

## Tests and mutations
- Full suite in a pristine clone of 84fd66f (Python 3.13.18, venv `…/scratchpad/tv`): 627 tests, OK. The clone was clean afterwards.
- 51 single-line mutations; 24 survived (`probes/mut6.out`). These matter:
  - `.endswith(".PDF")`: no end-to-end test that a real `%20` PDF is kept;
  - the re-check list without redirect hops, or without `source_url`;
  - `city`, `shop`, `pro`, `land`, `name`, `law` or `properties` dropped (the tests only use them with a path);
  - `_HOST` made ASCII-only;
  - a numeric host whose port needs a path.
- The rest are equivalent or harmless:
  - removing the branch on line 999 or its spaces;
  - `+` removed from the bare-host labels;
  - Ø and Þ untested;
  - the lookbehind and the ⅟ and ↉ ranges in `_FRACTIONS`.

## Not checked
- GitHub, markdown-it and VS Code rendering (linking claims are by reading).
- Live hosts and real county URLs.
- PDFs other than hand-made one-page files.
