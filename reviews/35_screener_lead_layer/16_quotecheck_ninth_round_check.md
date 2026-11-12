# Check of the quote checker's ninth round (commit 4ba2d69)

Recorded as received on 2026-11-12 from a fresh-context reviewer that did
not write the code.

**Verdict: sound after these fixes.**

Reviewer: Claude Opus 5.5, fresh context, 2026-11-12. I checked 4ba2d69 against its parent in scratch clones under `/tmp/claude-0/-home-user-Land-Research/073c2925-b364-5a5f-90db-1d7fbc42afeb/scratchpad/review_qc9/`. The probes and their outputs are in `probes/` there. The repository is unchanged (`git status` is clean). I made no outside requests: every fetch went through `FakeFetcher`.

| Checked | Status |
|---|---|
| Address forms in notes 10 to 15: removed, or split so that nothing can be linked or pasted | holds (numeric hosts are a NOTE) |
| A split host cannot be read as an address | does not hold (F1) |
| Ordinary text in 11 to 15 keeps every word | partly (F2) |
| Honest-quote URLs kept | holds (13 of 13) |
| Echo URLs dropped | holds for 10 to 14 (27 of 27). The three NOTE forms in 15 are still kept, with a flag. The output is byte-identical to round 8. |
| A dotted host that survives whole | holds: only names no registry issues survive |
| Timing | holds |

## Findings

**F1. MUST-FIX. A split host still reads as an address, and a scheme next to it now shows. Verified by running.** The split stops linking and pasting. It does not stop reading. A person reads `county-attorney. com` and `CLERK. ZONE` as addresses: this is the "defanged" style that security teams use precisely so that people can still read the address. So the claim in INVESTIGATE.md and in the commit, "no host can be read", is false. Some forms that the parent removed now show both a scheme and a host:
- `County confirms at https:||evil.example|listing` gives `County confirms at https: evil. example listing`. The commit changed the test to expect this.
- `https∶∕∕county-attorney.com∕opinion` gives `https county-attorney. com opinion`.
- `https:⧸⧸evil.zone⧸x` gives `https: evil. zone x`.

My view: splitting is the right trade. Agent text can always spell `clerk dot zone`, and no filter can stop that without also eating ordinary prose. The bar this code can actually meet is "nothing can be linked or pasted". Fix:
- Restate the rule and INVESTIGATE.md to that bar.
- Remove a dotted word when a scheme word (`http`, `https`, `ftp`, `www`) stands just before it, whatever separates them.

**F2. SHOULD-FIX. Words are still lost when a dotted word runs into `/`, `:digit` or `?`. Verified by running. This predates the commit.**
- `Lot.No/Block 4` (one of 15's cases) gives `(address removed) 4`.
- `St.Louis/Kansas City` gives `(address removed) City`.
- `M-1.Warehouses/Distribution allowed` gives `(address removed) allowed`.
- `Zoning.Map/Plat 4`, `N.E.Quarter/Section 4`, `U.S.Hwy/29`, `$5.25 sq.ft/yr`, `Max.Ht:45`, `Ord.No:2026-04` and `Ft.Worth?` are each removed as well, in agent text and in page text.

The mutations show that this branch no longer protects anything except the visible path. Fix: when there is no scheme, `//` or `www`, split the word instead of removing it.

**F3. SHOULD-FIX. Test gaps (see the mutations below).**
- A dot next to a non-ASCII letter is untested. With an ASCII-only lookbehind, `café.zone` and `Łódź.Legal` come out whole.
- The new backslash line can be deleted without any test failing.
- Removal of a query, port, encoded slash or `:/` is untested.
- The tests build their expected output with `qc._DOTTED` itself, so they cannot catch a change to it.

**NOTES (verified by running):**
- These still pass whole, as before: `203.0.113.9`, `203.0.113.9/1.2`, `127.1/x`, `203.0.113/listing`, `3405803785/x` and `localhost:8080/x`. Some of them have a path or a port, which contradicts the commit's claim that such addresses are removed.
- `#` and `\` are turned into spaces before the address regex runs, so the `#` and `\\` branches in it are dead code.
- Only names no registry issues survive whole: `evil-.zone`, `evil..zone`, and `evil...zone` (made from U+2025 and U+2026).
- The other dots all end up split or turned into spaces. U+2024, U+FF0E, U+FE52 and the entity `&#46;` are split. U+3002 and U+FF61 become spaces. A host after digits or underscores is split. So is a host joined by a soft hyphen or a zero-width character.
- The removal loop: in 200,000 random inputs, none needed more than 2 rounds.
- The `keep_end` cut runs after the split and only drops text, so it cannot join a host back together.
- Every word stays in these, but they read oddly: `Fig. 3. a/3. b`, `the S. E.1/4`, `9 a. m.`.
- Timing: 0.16 to 0.20 s for 3,360 characters. The worst case at 13,440 characters takes 3.2 s; the growth is quadratic, but the field caps bound it.

## Tests and mutations
- Full suite in a pristine clone of 4ba2d69 (Python 3.13.18, venv `…/scratchpad/tv`): 631 tests, OK. The clone was still clean afterwards.
- I made 32 single-line mutations to the changed lines; 16 survived (`probes/mut9.out`).
  - These matter: the ASCII-only lookbehind, the ASCII-only lookahead, the backslash line deleted or changed, and the query, `%`, `:\d`, `:/`, trailing `\S*` and `\.?` parts of the path branch each deleted.
  - These are equivalent: the `#` deleted, `[/\\]` changed to `/`, and the loop cut to one round.

## Not checked
- GitHub's renderer, markdown-it and VS Code. The statements about linking are by reading only.
- Live hosts, real county URLs and real PDFs.
