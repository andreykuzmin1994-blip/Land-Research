# 39 — Screener: run-to-run digest, marks on the viewer page, lead checks after a run: decision note

**Decision: committed on `claude/cool-faraday-nvgyjj`, not merged.** After a
merge a human runs
`python -m screener.autoresearch.evaluate --decide --new-baseline` on the
merge commit, because `screener/decide.py`, `screener/viewer.py` and
`screener/viewer_template.html` changed. The score should not change: the
evaluator imports none of them. **The lead checks ship switched off and
have not been run on a server with the real Claude Code.**

Asked by the operator on 2026-11-17, after a list of ways to make the tool
more useful and autonomous: implement three of them. (1) run on a schedule
and report only what changed; (2) check every grade A/B lead automatically
after each run; (3) pursue/pass buttons in the viewer, to reach the about
30 decisions the second half of the score needs.

## Tier

- Digest (`ops/screener/changes.py`), marks and import (`decide.py`,
  `viewer.py`, `viewer_template.html`): **Tier 1**, one independent
  fresh-context reviewer.
- Lead checks (`ops/screener/investigate.sh`, `lead_queue.py`,
  `lead_fetch.py`, the `supervise.sh` and `install.sh` changes): **Tier 2**
  (an external service, accounts and a sandbox). An independent risk
  enumeration before the code (`01_risk_review_lead_checks.md`), an
  independent adversarial review after it, and a second look at the fixes
  (`03_second_look.md`).

All reviewers were fresh-context subagents that did not write the code and
were given the code and the stated intent, not the author's conclusions.
Model: the session's configured model for the author and every reviewer.

Untouched: `git diff df51bec -- screener/rank.py screener/scoring.py
screener/screens.py screener/settings.toml screener/autoresearch
screener/quotecheck.py` is empty, and so is the diff of every file of the
root system.

## What changed

- **`ops/screener/changes.py`** (new, stdlib, offline): `changes.md` in a
  run's folder. New and gone grade A and B leads with what became of each,
  the top 50 (new, left, moved 10 or more), new deeds and owners, the
  parcels marked pursue or maybe, the failed-check notes, and each lead's
  document check when `--leads` is given. The supervisor writes it after
  every finished run and puts its line of counts in the summary alert.
- **Marks on the viewer page**: Pursue / Maybe / Pass / Listed buttons, a
  note, keys, two filter chips, and "Save my marks", which downloads
  `decisions-<run>.csv`. The page writes no file and asks no server.
  `viewer.py` adds to each plan the parcels that can be marked (those in
  the run's `ranked.csv`).
- **`python -m screener.decide --import FILE`**: records the page's file
  through the same three files as single calls. `main` was split into
  `prepare` (reads and checks, writes nothing) and `commit`; the
  single-decision command line and its output are unchanged.
- **Lead checks**: see `ops/screener/README.md` § "Lead checks". The
  agents run as `screener-lead` on an owner-free lead card with one fetch
  tool; the quote check runs as `screener-check`; the run user copies one
  named file per agent out and publishes four files or nothing.
- **`supervise.sh`**: the digest and the stage; and one change to the
  existing fix hand-off (below).
- CI runs the three `ops/screener` test files.

## Review of the digest, the marks and the import (Tier 1)

Nine confirmed findings, all fixed, each with a test:

| # | Finding | Fix |
|---|---|---|
| 1 | An owner's name reached `changes.md` through `lead_found` (a contamination site named after the owner) | The site name is dropped; the finished text is searched for every owner name and mailing address of both runs and each is replaced |
| 2 | Re-importing a saved file silently undid a newer decision (the page saves every mark each time) | A mark already among the parcel's rows, or older than its last row, is skipped and named |
| 3 | The single-decision command line no longer accepted an option between the id and the decision | The original parser is back; `--import` has its own |
| 4 | The page offered marks for assemblage members that are not in the run; the import then refused the whole file | Only ranked parcels can be marked |
| 5 | Opening another page of the same run wiped the first page's marks | Marks of parcels a page does not show are kept as they are; nothing is stored at load |
| 6 | An earlier run that ranked nothing blocked every later digest | It is passed over |
| 7 | A parcel id outside the usual characters lost its inner spaces | Kept in a code span, or marked as not exact |
| 8 | A two-column `decisions.csv` got a duplicate on every re-import | Marks are compared on the columns the file has |
| 9 | A non-finite rank crashed; run metadata could add a heading; a note typed just before closing the page was lost | Fixed |

Fixed from its "plausible" list: bare addresses in county text, and a
parcel listed both as new to the top and as moved. Left: "all or nothing"
covers the checks, not a disk error half-way through the writes (a
re-import then completes without duplicates).

The first version of the owner check hid the county's own name in the
title on the real runs ("Fulton County" is an owner) and took 9 seconds.
Both were found by running it on the three stored runs of
`screener-data`; it now takes about a second and withholds nothing there.

## Adversarial review of the lead checks (Tier 2)

The reviewer built three real accounts with `sudo` in a container and ran
the scripts. Confirmed, and fixed:

| # | Finding | Fix |
|---|---|---|
| 1 | A command run as a sandbox account inherited the supervisor's working folder, the private checkout: a replaced `claude` could read `ranked.csv`, owners included | The stage leaves the checkout first; so does the existing fix hand-off (`escalate`), which had the same pattern |
| 2 | Under umask 002 the agent could replace the fetch tool and the card, or plant `json.py` beside the tool | The stage sets its umask and every mode by path; the tool loads `quotecheck.py` by path and never puts its folder on `sys.path` |
| 3 | The settings watch failed open four ways (unreadable working folder, an early return before the comparison, the account's copy of Claude Code not watched, no time limit and links followed) | It runs from `/` with a time limit, hashes files and lists links, fails closed, is compared first, and watches more |
| 4 | The skeptic could read what the investigator left in `/tmp` | Swept between agents. **Accepted:** not the account's home |
| 5 | The private-address rule checked one DNS answer and connected on another | The fetch tool pins a name to the addresses it checked. **Open:** `quotecheck.py` itself |
| 6 | One leftover that could not be removed stopped lead checks quietly and used up tries | The stage stops with an urgent alert and counts no try; the same for an agent that cannot start; given-up leads are counted in the alert |
| 7 | A lead list that could not be made was silent | Reported |
| 8 | "No process left" was read from a `sudo` that could not run | Asked by the run user's own `pgrep` |
| 9-12 | Disk, pace across calls, the installer, a leftover marker file, the card naming a contamination site | Floor asked more often (no total cap: accepted); a pause before each call; fixed |

Its list of tests that passed under a mutation was acted on: the tool list
and the quote check's command line are asserted exactly, there is a
stale-folder test, and the named-sites test no longer leans on a private
address.

## What is not verified

- Claude Code itself: the options, the one `Bash(...)` rule against
  `$(...)`, redirections and assignments, and what `Write` can reach.
- A run on a real server with three accounts, `adduser`, `apt` and
  systemd.
- `pdftotext` on hostile PDFs, and what the search tool shows of the
  three refused sites.

The README's "Turning it on" asks for these on the first run, with one
lead.

## Tests

- `python -m unittest discover -s screener/tests -t .`: all pass.
- `python -m unittest discover -s tests`: all pass (no root file changed).
- `python -m unittest discover -s ops/screener -p 'test_*.py'`,
  `ops/screener/test_supervise.sh`, `ops/screener/test_investigate.sh`:
  all pass, the last also under umask 002.
- By hand: the digest on the three stored Fulton runs; the page in
  Chromium (mark, filter, save, reload, a second page of the same run),
  then `decide --import` of the saved file against the stored run, twice.
