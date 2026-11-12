# Check of the quote checker's eleventh round (commit 6b977ee)

Recorded as received on 2026-11-12 from a fresh-context reviewer that did
not write the code.

**Verdict: sound after these fixes.**

Reviewer: Claude Opus 5.5, fresh context, 2026-11-12. I checked 6b977ee against its parent 50a4de9 in clones under `/tmp/claude-0/-home-user-Land-Research/073c2925-b364-5a5f-90db-1d7fbc42afeb/scratchpad/review_qc11/`. Probes and outputs are in `probes/` there. The repository is unchanged. Every fetch went through `FakeFetcher`, I used node 22's URL parser offline, and I made no outside requests.

| Checked | Status |
|---|---|
| 17 F1 forms removed | holds: all 13 are removed, except `203.0.113.9` and `127.1/x`, which stay as the documented dotted-number trade |
| 17 F2 prose keeps every word | holds: 2 of 62 probe phrases lose a word, down from 30 |
| Nothing pasteable from `lead.md` | partly (F1, F3) |
| No ordinary word lost | does not hold (F2) |
| Honest quotes kept, echo URLs dropped | holds: 22 kept and 35 dropped, byte-identical to the parent |
| Earlier notes' forms and prose | unchanged from the parent, apart from the cases listed here |
| Timing | holds: at most 0.06 s for 3,360 characters |

**F1. MUST-FIX. A `//` address with a dotless or numeric host survives whole, though the documents now say `//` is removed. Verified by running, end to end.**
- Input (agent text): `see \\intranet\opinion or //3405803785?x`
- Output (`lead.md`): `see //intranet/opinion or //3405803785?x`
- The same happens in page context (`//intranet/share`) and in the headline. `//localhost` also stays.
- Node resolves these against any base: `//3405803785?x` becomes `https://203.0.113.9/?x`, and `//intranet/share` becomes `https://intranet/share`.
- By reading: on Windows, `//intranet/share` is a UNC (network share) path.
- This predates the commit.
- Fix: also remove `//` or `\\` followed directly by a letter or digit, for example `(?<![^\s(])//(?=[^\W_])\S*`. That still keeps "Note: // see below".

**F2. SHOULD-FIX. The new scheme and long-number branches delete ordinary words. Verified by running.** 86 of 114 probe phrases lose words, against 22 for the parent. Agent text and page text are both affected.
- `Tel:404-555-0100 for the clerk` → `(address removed) for the clerk`. This form is common on county pages.
- `Source data:2026 assessor roll` → `Source (address removed) assessor roll`
- `Zoning data:M-1 and M-2` → `Zoning (address removed) and M-2`
- `Case file:A-12 is closed`, `Questions about:the zoning`, `File:Zoning.Map.pdf shows M-1`, `Status HTTP:404`, `WS:12 mph` and `see the WWW: Ord.No. 26-04 page` each lose words the same way.
- Numbers: `Permit 20260412/3 was issued` → `Permit (address removed) was issued`. `Book 20261234/Page 5` → `Book (address removed) 5`. `Instrument 2019012345/1`, `Doc# 2026001234:2`, `Recorded 20261112:14:05` and `acct 0xABCDEF12/x` are removed too.
- Kept: `Max.Ht:45`, `Ord. 202604:2`, "mailto" in prose, `Tel.:404-555-0100` and `deadbeef12/x`.
- Fix:
  - Remove `data:` only in the shape `data:type/subtype[;,]` or `data:,`.
  - Drop `tel`, `about` and `blob` from the list and document that choice. A `blob:` address needs an inner `https:`, which is already removed.
  - Remove a bare long number or `localhost` only after `//` or a scheme. Without one, node rejects `3405803785/x`.

**F3. SHOULD-FIX. Two claims in the documents are still false. Verified by running. Both predate the commit.**
- "An address with a scheme is removed": `sms:+14045551234`, `ms-msdt:/id`, `search-ms:query=x`, `smb:intranet/share`, `webcal:intranet/cal`, `view-source:intranet` and `magnet:?xt=…` all print whole, and node accepts each one.
- "A hex host is removed": `0xcb.0.113.9/x` and `0xcb.29961/x` print whole, and node reads `http://0xcb.0.113.9/x` as `http://203.0.113.9/x`.
- pandoc gfm turns none of these into a link.
- Fix: name the schemes that are removed, or add the operating-system handler schemes to the list. Then either extend the hex branch to any dotted group that contains an `0x` part, or name that form in the dotted-number trade.

**F4. SHOULD-FIX. Test gaps (see the mutations below).**

**NOTE. Verified by running.** These forms are now split instead of removed: `https∶evil.zone`, `https — evil.zone`, `https` followed by U+2028 and a host, and `w​ww evil.zone` (a zero-width space inside `www`) all give `https evil. zone` or `www evil. zone`. That is a deliberate result of the 17 F2 narrowing, and none of them can be pasted.

## Tests and mutations
- Full suite in a pristine clone (Python 3.13.18, venv `…/scratchpad/tv`): 633 tests, OK. The clone was still clean afterwards.
- I made 49 single-line mutations of the changed lines. One did not compile, which leaves 48: 19 were caught and 29 survived (`probes/mut11.out`).
  - One survivor is equivalent: the `\b` before `:`.
  - Two only remove more text (bare `localhost`, `?` after a number), but no ordinary-text test catches them.
  - These are real gaps:
    - Each of `http`, `ftp`, `ftps`, `sftp`, `vbscript`, `tel`, `ws(s)`, `blob` and `about` can be dropped from the list.
    - Every `\b` can be removed.
    - The two-letter last label and the `xn--` part of `_NAMED_HOST` can be weakened.
    - In the gap: the slash can go, the leading spaces can go, and `[\s:/]*` can be cut to one character. `www` can be dropped.
    - Number branch: 9 digits instead of 8, the hex part dropped, `:` dropped, and the trailing `\S*` dropped.

## Not checked
- Real browsers' address bars and Windows Explorer. I relied on node's WHATWG parser only.
- GitHub's renderer, markdown-it and VS Code.
- Live hosts.
