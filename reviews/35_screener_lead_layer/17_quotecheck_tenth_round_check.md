# Check of the quote checker's tenth round (commit 50a4de9)

Recorded as received on 2026-11-12 from a fresh-context reviewer that did
not write the code.

**Verdict: sound after these fixes.**

Reviewer: Claude Opus 5.5, fresh context, 2026-11-12. I checked 50a4de9 against 4ba2d69 in clones under `/tmp/claude-0/-home-user-Land-Research/073c2925-b364-5a5f-90db-1d7fbc42afeb/scratchpad/review_qc10/`. Probes and outputs are in `probes/` there. The repository is unchanged. I made no outside requests: fetches went through `FakeFetcher`, and I used node's URL parser offline.

| Checked | Status |
|---|---|
| Address forms in notes 10 to 16 removed or split | holds. The same few survive whole as in round 9 (`evil-.zone`). |
| Nothing can be pasted as a working address | does not hold (F1) |
| No ordinary word lost | does not hold. 16 F2 is fixed (34 lost cases down to 12, and those 12 predate this commit), but there is a new loss (F2). |
| Scheme-word rule as described | partly (F3) |
| Honest quotes kept, echo URLs dropped | holds. Output is byte-identical to round 9. |
| Timing | holds: at most 0.22 s for 3,360 characters |

**F1. MUST-FIX. A scheme with a dotless or numeric host survives whole, and so does a `data:` URL. Verified by running, end to end.** This predates the commit, but INVESTIGATE.md and the `_plain` docstring now say such addresses are removed. That is false.
- Agent text `CLEARED: the opinion is at https:3405803785/opinion and data:text/html;base64,PHNjcmlwdD5hbGVydCgxKTwvc2NyaXB0Pg==` prints unchanged in `lead.md`. Node's URL parser (WHATWG, the standard browsers follow) reads the first address as `https://203.0.113.9/opinion`.
- The headline `See https:intranet/x` prints unchanged.
- The page context shows `http:localhost:8080/admin`.
- These also pass whole: `https:0xcb007109/x`, `https:2130706433`, `ftp:3405803785/x`, `file:/etc/passwd`, `javascript:alert(1)`, `203.0.113.9`, `127.1/x`, `3405803785/x` and `localhost:8080/x`.
- pandoc gfm turns none of them into a link.

Fix: remove a scheme word that is followed directly by `:` and then a character other than a space. Also remove `data:type/subtype[;,]…`, `javascript:…`, `localhost` followed by `:` or `/`, and a long integer or hex number followed by `/` or `:`. If any of these is left in, change the documentation to say so.

**F2. SHOULD-FIX. The scheme-word rule deletes ordinary words. Verified by running. This is new in this commit.** The parent kept every word in all 15 of my test phrases. This commit loses words in all 15, in agent text and in page text.
- `The portal uses HTTP 1.1 only` → `The portal uses (address removed) only`
- `served over HTTP/1.1` → `served over (address removed)` (also seen end to end in page context)
- `HTTP 404.Not found` → `(address removed) found`
- `Upload plans by FTP: Sec.4.2 applies` → `Upload plans by (address removed) applies`
- `county ftp site.Parcels are listed` → `county (address removed) are listed`
- `the www St.Louis office` → `the (address removed) office`

Plain mentions with no dotted word after them ("the ftp site", "http and https") are kept. Fix: in this branch, match only a named host (last label of two or more letters, or `xn--`) or a dotted quad. That fix passes the full suite (mutation 17).

**F3. SHOULD-FIX. The wording "whatever the reduction left between them" is wrong. Verified by running.** The rule only removes the host when the gap is spaces, colons or slashes. These keep the scheme and only split the host:
- `https;evil.zone` → `https;evil. zone`
- the same with `,`, `)`, `.`, `(…)` or ` - ` in the gap
- other schemes: `sftp: evil.zone`, `file:||evil.zone|x`, `wss:`, `mailto:clerk.zone`
- kept Latin look-alikes: `ḥttps:`, `þttps`, `ŵww`
- a scheme word inside another word: `xhttps`, `1https`, `éhttps`

None of these can be pasted (node says invalid), so they meet the restated rule. Look-alikes that NFKC folds (fullwidth, mathematical bold, circled letters, `ℎ`), Cyrillic `ѕ`, line breaks, tabs and U+2028 are all removed. Fix: say "spaces, colons or slashes".

**F4. SHOULD-FIX. Test gaps (see the mutations below).** No test has ordinary prose containing a scheme word.

## Tests and mutations
- Full suite in a pristine clone (Python 3.13.18, venv `…/scratchpad/tv`): 632 tests, OK. The clone was still clean afterwards.
- I made 33 single-line mutations. 19 were caught and 14 survived (`probes/mut10.out`).
  - These matter: the leading `\b` removed; the trailing `\b` removed; `ftps` dropped; `/` taken out of the gap (`http:/evil.zone` is then only split); the trailing `\S*` dropped; `_HOST` changed to `_LABEL`, which deletes any word after http or www and still passes.
  - The `_DOTTED` lookbehind removed: the change is cosmetic only.
  - These are equivalent: `*` to `+`, a named-host-only match (17), `|` added to the gap, `://` without spaces (the new branch covers it), and the loop cut to one round.
- The 16 F3 gaps (ASCII-only lookbehind and lookahead, the backslash line) are now caught.

## Not checked
- What browsers do when an address is pasted: I relied on node's WHATWG URL parser only.
- GitHub's renderer, markdown-it and VS Code.
- Live hosts.
