# 35 — Quote checker: what was fixed, and the first skeptic run (2026-11-11)

Operator-requested: "review the quote checker and run the skeptic step".
This note says what was done after each review, what is still open, and
what the two skeptics found. The reviews themselves are `06` (first
review), `07` (verification of the fixes) and `08` (second verification),
each recorded as received.

## Where it stands

`quotecheck.py` was rewritten in three rounds. Each of the first two
rounds was checked by a fresh-context reviewer; **the third round has not
had an independent check** (it answers `08`; its tests pass). 75 tests in
`tests/test_quotecheck.py`.

| Review item | State after round 3 |
|---|---|
| `06` B1: a quote made of the request's own words | Mostly closed. 25 characters in a row anywhere in the address, or three words in a row or one long word from the query part, drops the claim. Not airtight (`08`, N1 residuals): a two-word chosen field name is kept and flagged in `lead.md`; values split inside words are kept with no flag. The skeptic is told to compare each quote with its address. |
| `06` B2: a file in `pages/` taken as the page | Closed (`07`). Every run fetches every page again; `pages/` is a record only. |
| `06` B3: redirects followed blind | Closed (`07`). Followed by hand, five at most, every hop under every rule; never off the cited site. |
| `06` M1 / `07` N2: `lead.md` not tied to the checked file | Replaced: the code now writes `lead.md`. `08` found three ways agent or page text could still render as a link or a quotation (F1, F2, F4); round 3 reduces agent text to letters, digits and plain punctuation and prints page words as code spans. Not re-verified. |
| `06` M2: schema not enforced | Closed (`07`). Unknown fields, sections and verdicts refuse the file; the skeptic writes its own `skeptic.json`. |
| `06` M3: no word boundary | Mostly closed (`08`, N9). Residual: a few rare separators; the words around each quote are saved and shown. |
| `06` M4: robots.txt | Closed (`08`, N8). Own parser, the same on every Python. |
| `06` M5 / `07` N5: refused names | Closed for the forms listed. Cannot be closed by an address check: base64 in a reader URL, short links, look-alike alphabets. The other CoStar Group sites are an operator decision. |
| `06` M6: private addresses | Closed for literal and resolved addresses. DNS rebinding is not (`07`, N12a). |
| `06` M7 / `07` N6: a crash leaves an old result | Closed (`08`). |
| `07` N3: outputs written through a link | Closed (`08`). |
| `07` N4: nothing ties the two runs together | Closed (`08`): the first run refuses a `skeptic.json`; the second needs the first run's hash of `findings.json`. |
| `07` N7: the first pass could starve the skeptic | Closed (`08`). |
| `08` F6: an agent could leave `headline.txt` | Changed in round 3: the headline is a command-line argument. Not re-verified. |
| `08` F7: honest quotes dropped on descriptive paths | Changed in round 3: the word rules look at the query part only. Not re-verified. |
| `08` F8: the "not" note missed common negations | Changed in round 3: the page's words before and after every shown quote are always printed. Not re-verified. |

Still open, by choice or by nature:

- Text hidden by a style sheet, struck-through text and invisible PDF
  text count as page text.
- A PDF quote that only reads across two columns is kept, marked
  `pdf_order: layout`, and flagged in `lead.md`.
- No total deadline on a slow reply; pacing is per process.
- The tool's User-Agent carries no contact address, and robots.txt groups
  that name AI agents (`ClaudeBot` and the like) are not applied to it.
  Both are operator decisions.
- `lead.md` runs to about 1,400 words for a lead with a skeptic, against
  the 500 the first design asked for. Most of it is addresses.

## Operator decisions this leaves open

1. An allow-list of government hosts for `quotecheck` (the only real
   close for the refused-names rule, `07` N5).
2. Whether to refuse the other CoStar Group listing sites by name.
3. A contact address in the User-Agent, and whether AI-agent robots.txt
   groups apply to this tool.
4. Carried over from the pilot: whether a robots.txt that answers 403 on a
   public ArcGIS host is treated as the root system treats it.

## The skeptic step, run for the first time

Two skeptic agents, one per pilot lead, each in a fresh context with the
brief and the checked findings only. `quotecheck` then fetched every page
of both files again. Results are on the `screener-data` branch under
`leads/<parcel>/`.

| Lead | Skeptic | Its own claims kept | All claims kept / dropped | Requests / searches |
|---|---|---|---|---|
| #6, 0 Irwin Rd | `killed` | 10 of 10 | 35 / 0 | 23 / 9 |
| #44, 601 Senoia Rd | `weakened` | 6 of 6 | 22 / 1 | 12 / 10 |

- **#6.** The developer told the county development authority on 28
  April 2026 that zoning is in hand and the main tenant secured, with a
  bond inducement of up to $220 million for three buildings on the whole
  assemblage. Not shown: a completed sale. It disputed three first-pass
  claims; the most useful is that the "estimated closing date" is the
  authority's bond closing, not a land purchase, so the first pass's
  `signs_under_contract` reads better as `signs_developer_pursuing`.
- **#44.** The plan label the screener's "likely" path rests on was
  replaced by a plan adopted in October 2025. The skeptic's placement of
  the parcel on the new map is its own reading of a map image, flagged as
  not quotable.

What the run showed about the step:

- The skeptic added facts the first pass had missed and corrected one of
  its readings. It cost about ten minutes a lead.
- Both skeptics stayed inside the rules by their own account, with two
  small slips reported (one directory listing of the lead folder; one
  pause that may have been a second short of a 10-second crawl delay).
  Nothing checks those accounts.
- The skeptics ran before the second and third rounds of fixes; their
  claims were re-checked by the final code.
- Search results shown to both skeptics included listing-site results.
  Neither opened one. The rules cannot stop a search engine returning
  them.

## Changes to the plan

- `INVESTIGATE.md` now describes three roles and the two `quotecheck`
  runs, and the skeptic's file format.
- `screener/README.md` has a section on checking a lead.
- The seven parcels of the #6 assemblage are marked `maybe` in
  `decisions/decisions.csv` (operator's choice, 2026-11-10), so they stay
  in the list until the deeds show a closing.
