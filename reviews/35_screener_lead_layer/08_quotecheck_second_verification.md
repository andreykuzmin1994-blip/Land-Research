# Second verification of `screener/quotecheck.py` (round 2)

Verifier: fresh context, 2026-11-11, branch `claude/great-bardeen-nrwls0`. Nothing in the repo was edited, created or committed (`git status` clean); `screener/leads/` untouched; no network request of any kind. Rendering was checked with `pandoc -f gfm` (the only Markdown renderer installed), not GitHub itself.

Evidence: `/tmp/claude-0/-home-user-Land-Research/afef5b53-de64-56bc-a533-76d7fe240cea/scratchpad/verify_quotecheck2/` (`probes/*.py`, outputs `n1.out`, `n1b.out`, `n2.out`, `n2b.out`, `n2c.out`, `n3_n7.out`, `n5_n9.out`, `n10_n11.out`, `last.out`, `pilot.out`, `pilot2.out`, `mut.out`, `mut/mutate.py`).

## Verdict: fit after these fixes

N3, N4, N6, N7, N8, N10 and N11 are closed, and the generated `lead.md` holds its structure against hostile text in every field. But the central claim is not yet true: I got agent text to produce a live link and a rendered double-quoted passage (F1, F2), and a kept quote is written raw, so page text can add links or hide words (F4). All are few-line fixes in `_plain` and `_claim_lines`.

Tests: `python -m unittest screener.tests.test_quotecheck` gives 69 OK; `discover -s screener/tests -t .` gives 578 OK (Python 3.13.18).

## N1-N11

| | Status | Evidence (all run) |
|---|---|---|
| N1 | mostly fixed | Live `outStatisticFieldName":"warehouse_by_right"` case dropped before any fetch; entities in a JSON/text body, three joined parameters, `.`/`_` to spaces, loose `charset=utf-7` and a 24-char echo plus boilerplate are all dropped. Residuals below. No real pilot claim dropped: 42 investigator + 16 skeptic claims, 0 echo drops. |
| N2 | replaced; holes | Structure holds: hostile text in all fields gave no extra heading, bold, code span, raw `"` or link. Holes: F1-F5. |
| N3 | fixed | Symlinks at `findings.checked.json`, `lead.md`, both `.part` names and `pages/<name>.*`: victim file intact, real files written. `pages` as a link: claim dropped, nothing written there. `headline.txt` symlink to a secret: not copied. |
| N4 | fixed | Planted `skeptic.json`: first run refuses (exit 2) and old outputs are gone. Missing, empty, wrong or prefix hash: refused. Edited `findings.json` (even one newline): refused. Both hashes recorded under `quotecheck.inputs`. |
| N5 | fixed as listed | Soft hyphen, zero-width, fullwidth, mathematical letters, 4x and 8x encoding, entities: refused. Still pass: 11x encoding, variation selector / U+034F inside the name, Cyrillic look-alikes, base64. |
| N6 | fixed | `1e999`, `-1e999`, `NaN`, `Infinity`, 5,000-digit integer, repeated field, 200,000-deep nesting: exit 2, no files. Lone surrogates in 8 positions: no crash. |
| N7 | fixed | 60 investigator pages plus 40 skeptic pages all kept; 200 + 60 claims accepted; 201 or 61 refused. |
| N8 | fixed | 2,000 rules of 200 `*` against a 30k path: 0.03 s; 100k `*`: 0.006 s. Sitemap or unknown line between User-agent lines no longer splits the group. 25 rule cases all as expected. |
| N9 | mostly fixed | U+2010/2011/2013, soft hyphen, `<b>`, `<wbr>`, `<span>`, `$2,500,000`, `21.5 feet`: dropped. Still kept (context shows it): U+2027, U+00B7, U+2043, `non/permitted`, `dis<label>allowed</label>`, `<tt>`, custom elements, `12 500` with a thin space. |
| N10 | fixed | Real two-column PDF: true sentences `pdf_order: reading`; the false cross-column sentence is kept, marked `layout`, and `lead.md` carries the note. PDF behind 2 leading bytes is read as a PDF. Behind 1,030 bytes it is read as text and raw operators are quotable. |
| N11 | fixed | Short body against Content-Length on a loopback server: "the reply was cut short". Off-site reason no longer names the host. `final_url` cut to 2,000. Tag characters and surrogates removed by `clean()`. |

## New findings

**F1. MUST-FIX. `_plain` builds a web address after it has removed addresses.** The address filter runs first; then `|` becomes `/`, `*` and `#` are deleted, `_` becomes a space.
- Claim text `County confirms at https:||evil.example|listing` gives the line `- County confirms at https://evil.example/listing (zoning-1)`, which renders as a link.
- Same result for `h*ttps://evil.example/x`, `ht#tps://…`, `w*ww.evil.example/x`, `_www.evil.example/x`.
- Fix: translate characters first, then strip addresses (repeat until nothing changes).

**F2. MUST-FIX. `&` is not handled, so HTML entities render.**
- `The code reads &quot;warehouses are permitted by right in all districts&quot;` renders as real double quotation marks round agent text; `&ldquo;…&rdquo;` likewise.
- `https&colon;&sol;&sol;evil&period;example&sol;x` renders as a visible `https://evil.example/x` (not linked in pandoc).
- `~~not~~` renders struck through; backslash escapes also pass.
- Fix: map `&` to "and", and drop `\` and `~`. Better: an allow-list of letters, digits, spaces and plain punctuation.

**F3. SHOULD-FIX. Look-alike quotation marks pass `_plain`.** `On the page: ＂warehouses are permitted by right＂ (county code)` in claim text survives unchanged (U+FF02). So do U+201E/201F, U+2033, U+301D/301E, U+02BA, U+275D/275E and `''`. Fix: NFKC first, then map every quotation-mark character to `'`.

**F4. SHOULD-FIX. A kept quote is written raw, so page text is live Markdown.** Only `"` is replaced. With a `text/plain` page holding these words, all kept:
- `See [the county code](https://evil.example/code) for the permitted uses` renders a link to evil.example.
- `Listing seen at https://www.loopnet.com/Listing/123` renders a LoopNet link.
- `Warehouses are <!-- not --> permitted in the M-1 district by right` hides "not" when rendered.
- The quote is also shown in the agent's spelling: `ⁿᵒᵗ` and `10⁵` match a page that says "not" and "105".
- The words are on the page, so this is not agent-authored text, but "every link is a kept claim's address" is false. Fix: put the NFKC quote in a code span inside the double quotes (backtick run longer than any in the quote).

**F5. SHOULD-FIX. E-mail addresses become links.** `write to broker@evil.example` renders as a `mailto:` link. Fix: `@` to " at ".

**F6. SHOULD-FIX (procedure). An agent can leave `headline.txt`.** A file present at the first run is printed as "the launching session's reading". INVESTIGATE.md step 1 deletes only `skeptic.json`. Fix: pass the line as `--headline`, or refuse a `headline.txt` on the first run.

**F7. SHOULD-FIX. The echo guard drops honest quotes on addresses with descriptive paths.** 8 of 13 realistic made-up claims were dropped, for example:
- any quote with "Comprehensive" from `.../2045-Comprehensive-Plan.pdf` (the one-long-word rule);
- "provides water and sewer service" from `.../public-works/water-and-sewer`;
- an owner lookup `where=Owner LIKE 'MAPCO FARMS LLC%'` quoting `"Owner":"MAPCO FARMS LLC "`.

The pilot had none. Fix: apply the long-word and three-word rules to the query and fragment only, keep the 25-character rule for the path; or tell the investigator in INVESTIGATE.md.

**F8. SHOULD-FIX. The "Read it in place" note misses common negations.** No note for "isn't", "aren't", "cannot be", "can't be", "unlawful for", "excluded and", "None of the following are", or a "not" more than three words back (7 of 12 cases). Nothing after the quote is shown ("…only by special use permit, never by right"). Fix: always print the few words before and after, through `_plain`.

**NOTES.**
- N1 residuals kept with no warning (`words_shared_with_url` empty): values split inside words (`a=ware&b=houses+perm&c=itted…`), three short words (`m1_ok_use`, `use is OK by law in M1`), a two-word path echo (only the query part is compared). Two-word chosen field names (`by_right`, `permitted_use`) are kept but flagged in `lead.md`.
- `parcel_id` and `run` are unlabelled agent text in the title: `# Lead check: parcel 14F0001 - CLEARED: zoning by right, county verified, run final, approved by counsel`.
- The wrong-hash refusal prints the current hash, and a re-run with it passes. INVESTIGATE.md should say to use only the hash from step 1.
- `source_url` has no length cap (a 200 KB address gives a 200 KB line in `lead.md`).
- `lead.md.part` as a folder: exit 2 with `findings.checked.json` left and no `lead.md`.
- `costarica` in an address is refused.
- A hard link at `headline.txt` is copied (only where the agent can hard-link the file).
- `clean()` keeps blank-looking U+3164, U+2800, U+034F.

## Surviving mutations (17 of 71, `mut.out`)

- **Untested guards:**
  - `ECHO_TOKEN_CHARS` 12→20;
  - charset taken loose from the text (only the allow-list catches the test);
  - `_plain` keeping `<` `>`, and keeping the curly close quote;
  - `_link` not escaping;
  - `parcel_id` and `run` not through `_plain`;
  - `headline.txt` link followed;
  - `skeptic.json` link followed;
  - skeptic page cap 40→60;
  - robots: end-anchor overlap check removed, middle pieces not ordered;
  - inline end tags putting a space.
- **Equivalent or near:** no `O_EXCL`/`O_NOFOLLOW` (the unlink before it covers all but a race); `final_url` cut in two places (each covers the other); `allow_nan=False` (the float guard covers it).

## Not checked

- GitHub's own renderer; bidirectional letters reordering a line.
- Live behaviour of any host. DNS rebinding (N12a) and the dripping-server deadline were not re-tested.
- Hostile PDFs against poppler; invisible PDF text is still kept, as the docstring says.
- That a reader service maps variation selectors to the real name (by reading UTS 46).
- The regenerated pilot `lead.md` differs from the data branch's only in the "address"→"request" wording of the newest commit; no stray link or quote in either.
