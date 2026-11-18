# 41 — Screener: lead tally, agenda watch, answer-key what-if: decision note

**Decision: committed on `claude/cool-faraday-nvgyjj`, not merged.** The
tally and the agenda watch live in `ops/screener/`, outside `screener/`;
under `screener/` only `AGENTS_DESIGN.md` (a document) changed in this
round, so this round alone moves no score. The new baseline that
`reviews/39_screener_autonomy/02_decision_note.md` asks for after a merge
is still owed. **The agenda watch ships with no sources file, so its
timer does nothing until the operator writes one, and it has not been run
on a server under its own account.** No change to the answer key is
proposed.

Asked by the operator on 2026-11-18: build the three things the round
before had left out: a tally of why leads get killed (as proposals for
the screens), an agenda watcher between runs, and more Fulton years in
the answer key.

## Tier

- Lead tally (`ops/screener/lead_tally.py`): **Tier 1**, one independent
  fresh-context reviewer.
- Agenda watch (`ops/screener/agenda_watch.py`, `agenda-watch.sh`, the
  units, the `install.sh` block): **Tier 2** (outside sites, a new
  account, untrusted documents). An independent risk enumeration before
  the code (`02_risk_review_agenda_watch.md`), an independent adversarial
  review after it, and a short second look at the fixes.
- Answer key: no code and no data changed; a measurement in a scratch
  folder (`01_answer_key_whatif.md`).

All reviewers were fresh-context subagents that did not write the code
and were given the code and the stated intent, not the author's
conclusions. Model: the session's configured model for the author and
every reviewer. After three container restarts in the round before, most
likely caused by a reviewer's own test harness, every reviewer here
worked under written limits: no accounts, no `sudo`, no process kills,
no network, the wrappers never run with a sandbox account set.

Untouched: `git diff e532dcd -- screener/ ':!screener/AGENTS_DESIGN.md'`
is empty, and so is the diff of every file of the root system
(`prepare.py`, `parameters.json`, `sources.json`, `program.md`,
`zoning_rules.json`, `building_prototypes.json`, `rules_sources.json`,
`research.py`, `runner.py`).

## What changed

- **`lead_tally.py`** (new, stdlib, offline, no model): reads every
  lead's `findings.checked.json` and writes `LEADS_DIR/_tally.md`: how
  many leads were killed or weakened for each of seven fixed reasons
  (already built, a developer holds it, an operator holds it for itself,
  a plan is filed, zoning, shape, neighbours), whether the reason came
  from the skeptic's words or from a checked section, and how many of
  those parcels are still grade A or B in the newest run. Each reason
  carries a fixed sentence on where a screen could look. It proposes;
  it edits nothing. The lead-check stage reruns it after each batch.
- **`agenda_watch.py`**, **`agenda-watch.sh`**, **`screener-agenda.service`
  / `.timer`**, **`agenda_sources.example.json`**, the `--agenda-watch`
  block of `install.sh` (new): once a day, fixed code fetches the index
  pages the operator listed, reads the documents that are new, and
  searches them for the watched parcels' ids and site addresses, their
  streets and zoning case numbers in the titles, and industrial zoning
  words. Three steps: the run user prepares a job (ids and addresses
  only), the `screener-agenda` account makes every request and runs
  `pdftotext`, the run user checks the result against the job and writes
  `agenda-watch.md` and an alert of counts. No model is involved.
- `changes.py`, `lead_tally.py`: `&` no longer passes the text cleaning
  (see N5 below). `investigate.sh`: the tally call and the agenda
  folder in `sandbox_remove`'s pattern. CI runs the two new suites.

## The answer key

`01_answer_key_whatif.md`. A scratch harness reproduced today's key
exactly (45 sites, 35 development / 10 test) and then tried what the
operator could add:

| Change | Sites | Note |
|---|---|---|
| Today | 45 | 35 / 10 |
| Cohorts 2015 to 2021 added | 45 | more site-years, the same sites |
| The county's 2026 parcel layer | n/a | it has no improvement value |
| The live layer as a 2026 label | 48 | 36 / 12; +7 −4 |

More Fulton years do not make the key meaningfully larger. What would:
operator decisions (the second half of the score opens at about 30; 12
exist), more counties, or a different window rule. Each of those is the
operator's to decide, and `autoresearch/build_eval.py` and
`autoresearch/data/` are not the agent's to edit, so nothing was
changed.

## Review of the tally (Tier 1)

Ten findings, all fixed, each with a test:

1. A lead whose reason came only from a checked section was counted in
   no cell (one real lead). A reason now counts from the words or from a
   section, and the table says which.
2. A negated sentence was dropped whole, with the reason it carried.
3. `plain()` removed schemes before filtering characters, so
   `https"://…` came back together as a link. Filter first, then
   schemes, then `:/` and dots.
4. "Still grade A or B in the newest run" said more than the code did:
   a parcel missing from the newest run was looked up in older ones; an
   empty `ranked.csv` read as "the screens may already cover it".
5. The log's summary line named a "most common reason still coming
   through" when none was.
6. `--out` on a path that is not a regular file; a fixed temp name
   written through and left behind; a run folder name that is not
   UTF-8. Now `mkstemp` beside the target, a refusal, no leftover.
7. County and run text (reject reasons, rank, grade, the run's folder
   name) reached the file through a cleaner that passed addresses.
8. A section verdict such as `_a_` rendered as italics.
9. Wording: "the gap is open" claimed more than word matches against an
   earlier run can; a setting named without its section; the README's
   order differed from the tool's.
10. A field computed and never used.

On the 29 real leads against the run of 2026-11-13: 6 killed, 23
weakened, 27 of the 29 still grade A or B; operator 14, zoning 12,
neighbour 12, developer 11, plan 11, built 8, shape 2.

## Review of the agenda watch (Tier 2)

**Before the code**: the 17 risks listed in `02_risk_review_agenda_watch.md`. The
design changed on four of them: the parsing moved to its own account,
the supervisor's lock is never opened, a document is seen only once its
text was searched, and the per-source regular expression became literal
path prefixes.

**After the code**: 13 findings, all acted on.

| # | Finding | Fix |
|---|---|---|
| 1 | The "a screener run is active" check never fired: a oneshot unit is `activating` while it runs | `systemctl show -p ActiveState`; skip on active, activating, deactivating, reloading |
| 2 | High-priority false alarms: "Union City Council" for a parcel on Union Rd; bare "zoning" and "annex" as zoning cases | A street word must follow the name; street-only matches alert at normal priority; the bare words removed |
| 3 | Documents never read, or recorded read when not: links with a query dropped uncounted; `pdftotext` cut short recorded "read"; a notice page in place of a PDF; an item number read as a date; address spellings | Links not taken are counted and said; non-zero exit and the page cap give "partly read"; `pdf_only`; the age rule on first sight and two date shapes only; directionals, apostrophes, highway forms |
| 4 | `plain()` let `htt"ps://…` come back together | The same order as the tally's fix |
| 5 | A hostile result could push every real entry out with made-up "noted" addresses, or give up on a document in one run | "Noted" only on first sight or a storm; documents deduped and capped at `max_new` |
| 6 | The seen list was smaller than one index page | 8,000 kept against 5,000 links |
| 7 | Request discipline: a document sent on to another address was read as the document; the bytes of a refused download were not counted; a source the run never reached counted as a failed day | The final address is checked; the cap is counted; "skipped", apart from "failed" |
| 8 | The watch could stop for good after one alert: a problem was said once and never again; no time limit on the scan | Repeated weekly; the scan stops starting work after 25 minutes and still answers |
| 9 | The scan's output could fill the log | The first megabyte is kept |
| 10 | Tax-roll owner names of two words were not withheld, though the text said owners were | The promise is stated as it is: names of three words or more, in roll spelling (two-word names would blank street names, as the live trial showed) |
| 11 | Ids joined across line breaks, slashes and brackets | Up to a few spaces, dots or dashes, on one line |
| 12 | A backlog grew into a "storm" and was noted unread | Documents that wait are queued by name and read first; the share rule from 40 links |
| 13 | Two ways the report step was refused without a clear word: a run folder that went away, values of the wrong kind | Said as that; every field checked for its kind |

**Second look** (a new reviewer, the fixes only): 7 of the 13 closed, 6
partly, and nine new points. All nine were acted on:

| # | Finding | Fix | Test |
|---|---|---|---|
| N1 | New documents that lost their slot to the queue, or that the allowance ran out before, were neither read nor queued | What a run did not get to is queued after the read loop | `test_new_documents_the_allowance_did_not_reach_are_not_lost`, `test_requests_and_bytes_have_a_ceiling` |
| N2 | `HWY 92` matched "Hwy 920"; a street named like the city or the source was dropped ("South Fulton Parkway"); PIKE, XING, LOOP, RUN missing | A boundary after the number; the own-name filter removed (the street word already stops "Union City Council"); more street words, and, found while re-reading that change, a street's name now ends at its last street word ("DEER RUN TRL" is DEER RUN, not DEER). On the 1,582 addresses of the run of 2026-11-13 that changes three streets, each to the fuller name | `test_a_city_in_a_title_is_not_a_street_but_a_street_named_like_it_is`, `test_a_highway_number_ends_where_its_digits_end_and_more_street_words`, `test_streets_and_addresses_as_rolls_and_documents_write_them` |
| N3 | One slow download outlasted the wrapper's limit and lost the whole run, every day | Four minutes a request by the clock; it counts as a try of that document | `test_one_slow_request_is_stopped_by_the_clock` |
| N4 | A leftover of the scan that kept its output open held the wrapper before `kill_user` | The output goes through a side process bash does not wait for | `test_agenda.sh`, "a leftover of the scan…" |
| N5 | `&#104;ttps&#58;//…` passed the text cleaning in all three tools | `&` becomes " and " | `test_an_entity_cannot_spell_an_address_or_a_column`, and one each in `test_lead_tally.py`, `test_changes.py` |
| N6 | A hostile result could fill the state with "queued" addresses, which are never evicted | At most 40 a run | `test_a_result_cannot_make_a_long_queue`, `test_what_waits_to_be_read_is_never_forgotten` |
| N7 | "09F - 0700", typeset dashes and tabs were not matched | Up to three separators; dashes and blanks mapped to the plain ones | `test_an_id_as_documents_typeset_it` |
| N8 | A source skipped day after day never alerted | A skip streak counts toward "failing" on the third day | `test_a_source_not_reached_for_days_is_said_like_a_failing_one` |
| N9 | The "noisy scan" test passed by accident (one write) | The stand-in prints in 5,000 writes and its result must survive | `test_agenda.sh`, "what the scan prints…" |

The two shell checks for N4 and N9 were run against the wrapper as it
was before the fix: three checks fail there and pass now.

**This last round of fixes was checked by tests only, not by a third
reviewer.**

## Accepted, not fixed

- An agenda amended under the same address after it was read is not
  read again (risk 9). Said in the tool, the report and the README.
- `pdf_only` is opt-in: without it a notice page answering 200 at a
  document's address is "read". The example source sets it.
- A link whose address holds a raw space is counted as not taken, not
  read.
- A taken-over scan account can have one batch of documents "noted"
  unread, in a way the report and an alert say aloud, and can send the
  parcel ids and addresses it was given anywhere: that account has the
  network.
- A parcel id of digits only can coincide with another number. The
  report says so every time.

## Not verified

- A run on a server: `install.sh --agenda-watch`, the `screener-agenda`
  account, `sudo`, `kill_user`, the systemd unit states and timer. The
  offline tests run the wrapper as one user with a stand-in for the
  scan.
- The per-request time limit against a real slow server (tested with a
  made-up one).
- More than one source. The live trial was South Fulton's page alone,
  by hand, in one process: 4 street matches from titles (Roosevelt Hwy,
  Bishop Road, Spence Road, Campbell Drive), about two dozen zoning-case
  titles, 2 scans without text, 159 documents noted on first sight.
- Whether a city posts searchable packets at all. South Fulton's first
  documents were scans; for those only the title is searched.
- The tally's word patterns on leads other than the 29 it was written
  against.

## Tests

`python -m unittest discover -s ops/screener -p 'test_*.py'` (163),
`ops/screener/test_agenda.sh` (79), `ops/screener/test_investigate.sh`,
`ops/screener/test_supervise.sh`, the screener suite and the root suite:
all pass, and all run in CI's "Offline test suite".

## For the operator

1. Read `~/screener-ops/leads/_tally.md` after the next lead checks. It
   is a list of proposals; a change to `screens.py` or `settings.toml`
   is yours to make, with a new baseline.
2. To turn the agenda watch on: `ops/screener/README.md` § "Agenda
   watch", three steps. Read each site's robots.txt and terms before
   listing it.
3. The answer key: nothing to do. The lever is decisions on the viewer
   page (12 of about 30).
