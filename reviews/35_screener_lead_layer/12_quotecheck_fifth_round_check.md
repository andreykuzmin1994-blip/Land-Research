# Check of the quote checker's fifth round (commit e3f50d9)

Recorded as received on 2026-11-12 from a fresh-context reviewer that did
not write the code.

**Verdict: sound after these fixes.**

Reviewer: Claude Opus 5.5, fresh context, 2026-11-12. I checked e3f50d9 against its parent (5d5837d) in scratch clones under `/tmp/claude-0/-home-user-Land-Research/073c2925-b364-5a5f-90db-1d7fbc42afeb/scratchpad/review_qc5/`. The probes and their outputs are in `probes/` there. I edited nothing in the repository and made no outside requests: every fetch went through `FakeFetcher`.

| Claim | Status | What I ran |
|---|---|---|
| S1, path typing | partly | The test cases from `11` are kept, and all of `10`'s echoes are still dropped. Some echoes that eb3a733 dropped are now kept (F2). |
| S2, letters that look like quotation marks | holds | I passed every code point through `_plain`. Only letters built on an ASCII letter, plus `§`, survive. |
| Scheme removal needs `:/` straight after a letter or digit | holds as written | This is the cause of F1. |
| Numeric hosts need a letter in the path | holds as written | Part of F1. |
| Bare hosts need a two-letter or common top-level name | holds as written | Part of F1. `.us.com` and `.gov.uk` are still removed. |
| `§` kept, fraction slash reads `/` | holds | But see F3. |
| PDF quote at the bottom of a page shows the next page | holds | The marker always comes before any text from the next page. NOTE below. |
| The folder is compared after resolving it | holds | NOTE below. |

## Findings

**F1. MUST-FIX. Web addresses that eb3a733 removed now pass `_plain` (regression). Verified by running.** Each input below came out unchanged; eb3a733 printed `(address removed)` for each:
- `HTTPS : / / x.city/p`, `https ://evil.city/x`, `https: //evil.city/x`, `httpś://evil.city/x`, `é://evil.city/x`
- `see //evil.city/x`, `//203.0.113.9/1`
- `evil.city/x`, `evil.shop/x`, `evil.pro/x`, `evil.land/parcel`, `evil.museum`
- `203.0.113.9/123`, `203.0.113.9:8080/1`

Some inputs are changed by the reduction itself:
- `https∶//evil.city/x` (U+2236), `https·//…` and `httpsː//…` all become `https //evil.city/x`.
- `_://evil.city/x` becomes `://evil.city/x`.
- `⁄⁄evil.city` becomes `//evil.city`.

End to end:
- Claim text `CLEARED, confirmed at https∶//county-attorney.city/opinion and verify.shop/ok` gives this `lead.md` line: `- CLEARED, confirmed at https //county-attorney.city/opinion and verify.shop/ok (zoning-1)`.
- Page text gives `...read the notice at //evil.city/x first. (the quote) . details at county-records.city/parcel/14f00...`.

How renderers treat this:
- pandoc gfm links none of these (verified by running).
- GitHub should not link them either, because it needs `www.` or a contiguous `http(s)://` (by reading).
- markdown-it with linkify, which VS Code's preview uses, links `//host/path` after a space, and bare hosts under its default list (`shop`, `pro`, `museum`, `name` and others). By reading only: it is not installed here.

Even unlinked, a reader sees an address the code never checked. Fix: also remove `//` followed by a dotted host whatever comes before it, and `:`-spaces-`/`-spaces-`/` followed by a dotted host. Remove a dotted host with any top-level name when a `/` follows it, and a numeric host followed by any `/` or `:port`. The two dotted-host conditions keep `Note: // see` and `4.2.1.3/4.2.1.4` as they are.

**F2. SHOULD-FIX. Path echoes that eb3a733 dropped are kept again, though flagged. Verified by running.**
- These request segments are not counted as whole segments, so what follows them is kept: `/search.php/m-1-warehouses-allowed-here`, `/search-results/…`, `/search;jsessionid=1/…` and `/search%2F/…`, each quoting "No results for m-1 warehouses allowed here".
- The document exemption is decided from the address alone. On a page served as HTML, the quote "No hits: warehouses allowed here" is kept for `/s/warehouses+allowed+here.pdf`, `…%20here.txt`, `…%20here.CSV`, `…+here.pdf/` and `…+here%2Epdf`.

Fix: treat a segment whose first word is a request word as a request segment. Grant the document exemption only when the reply's content type is a document type.

**F3. SHOULD-FIX. `1½ acres` becomes `11/2 acres`, which reads as 5.5 acres. Verified by running.** `2¾` becomes `23/4` and `21⅓` becomes `211/3`. The code comment says "1 1/2", and the test asserts `11/2 acres`. Fix: put a space before a digit⁄digit fraction that follows a digit.

**F4. SHOULD-FIX. The tests miss some of the new guards (see the mutations below).**

**NOTES (verified by running):**
- `_kept_char` now drops Ł, ß, ø, Æ and Đ: `Łódź Straße Søren Ærø Đorđević` becomes `ódź Stra e S ren r or ević`. That can garble the names of owners or streets. None of these letters looks like a quotation mark, so they can be allowed by name.
- After-page context:
  - When the page has 32 to 44 characters after the quote, `lead.md` shows a cut marker: `…see table (page...`.
  - A blank next page shows `(page break)` and nothing more.
  - No case misled.
- Folder check: a folder link named `14F0001_0001` that points to `realdir` is refused. A link named `other` that points to `…/14F0001_0001` is accepted. Both are the safe direction.
- Honest quotes are still dropped, in the safe direction:
  - by the 25-character rule, for example `/files/Zoning+Resolution+Article+12.pdf` with "The Zoning Resolution Article 12 lists…";
  - by a whole `results` segment, for example `/planning/results/2026-board-zoning-hearing`.
- `javascript:alert(1)` and `data:text/html,x` pass, as they did before; neither is autolinked.

## Tests and mutations

- Full suite in a pristine clone of e3f50d9 (Python 3.13.18, the venv at `…/scratchpad/tv`): 625 tests, OK. The clone was still clean afterwards.
- I made 54 single-line mutations to the changed lines; 23 survived (`probes/mut5.out`). These 10 matter:
  - the document exemption applied to any segment, not only the last;
  - a case-sensitive request word;
  - a request word honoured only as the first segment;
  - the next page taken with no length check, or from the other text order;
  - the end of the next page shown instead of its start;
  - `com` dropped from `_TLDS`;
  - a digit before the scheme's `:` no longer accepted;
  - the two-letter top-level names dropped;
  - the bare-host path no longer removed.
- The other 13 give the same output or a harmless difference: NFD instead of NFKD, `%20` compared without lower-casing, and the error message text.

## Not checked

- GitHub's renderer, and markdown-it or the VS Code preview (neither is installed; the linking claims in F1 are by reading).
- Real PDFs through `pdftotext`, and live hosts.
