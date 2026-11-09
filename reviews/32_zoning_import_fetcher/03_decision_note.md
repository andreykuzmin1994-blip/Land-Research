# 03 — zoning_import onto the shared fetcher (D1-11): reviewer decision

> **Tier 2.** Pre-change register `02_risk_review.md` (independent reviewer,
> before any code); dispositions `02_code_writer_response.md` (code writer).
> This note is the post-change review by a fresh context that wrote none of
> the code: what was checked, what was found, what changed, the verdict.
> Base `4fe8080` (PR 38 merge); reviewed commits `05c61fd`, `16b5b97`,
> `f519324`. Model names are omitted. Nothing pushed.

## Operator answers this round rests on (2026-11-09, binding, in chat)

"1 - Count them, jsut review, 3 - fix 4 - can you add this yourself. Start
building what you proposed keeping my answers in mind"

| | Answer | Honoured here |
|---|---|---|
| 1 | "Count them": D3 counts unlisted districts AND districts with unreadable setback values (D3-01 / D3-02). | Not this round's code; nothing here touches D3. |
| 2 | "just review": D2 stays as implemented (V/VE review-only, floodway still blocks); no flood-rule change. The null-beats-low question is NOT answered: no new root sub-score. | Confirmed: no change to `hazards.py`, `research.py` or any sub-score in this diff. |
| 3 | "fix": the research.py ArcGIS error-envelope and audit-path bugs, and this D1-11 zoning_import fetcher round. | Only the D1-11 half is in this diff; it is reviewed here. |
| 4 | "can you add this yourself": the agent, acting for the human between runs, applies the reviews/26 hazards prepare-mutation to `prepare.py`. | Not this round; `prepare.py` is untouched by this diff. |
| Step 2 | "Start building": the screener quick wins. | Not this round. |

## Verdict

**Approved after one small fix.** Every gate G1 to G7 of the register is met.
Every register item ZI-01 to ZI-13 is fixed or accepted with a reason that
holds. The fix is docstring-only (below). No UPDATE differs from the base
session on any fixture variant, so this change needs no new run or baseline.

## What was checked

- **Diff scope (SR-1, SR-2).** Files changed: `zoning_import.py`,
  `rules_scrape.py`, `README.md`, tests, a verbatim reference copy under
  `tests/`, and the review docs (plus one appended line in
  `reviews/29_operator_decisions/03_decision_note.md`). No human-owned file
  is touched: `sources.json`, `parameters.json`, `zoning_rules.json`,
  `rules_sources.json`, `building_prototypes.json`, `prepare.py` and
  `program.md` are not in `git diff --name-only 4fe8080..HEAD`. The
  `zoning_import.py` hunks are confined to the docstring, imports, constants,
  the HTTP section, the budget line in `run()` and the robots print. No SQL
  constant and no matching, overlay, plan or write function changed.
  `rules_scrape.py` changes only the moved import (two call-time imports) and
  the `user_agent` class attribute, which `__init__` and `_raw_get` now read.
  rules_scrape's own UA is unchanged.
- **D1 exception, no wider than the siblings (ZI-06).** `ZoningSession`
  inherits `_check`, `robots_for`, `fetch` and `_raw_get` from
  `PoliteFetcher`, so the verdict is the shared `rules_scrape.robots_verdict`
  and the prepared-URL denylist backstop applies. zoning_import defines no
  verdict and no `arcgis_*` logic of its own (static test). The exception
  line prints once per host, between `written:` and `requests:`, on both the
  exit-0 and exit-4 paths.
- **Equivalence (ZI-04).** The 13-variant differential test drives `run()`
  through the verbatim 4fe8080 session and the new one. Both give the same
  SQL with the same parameters in the same order, the same commits and the
  same exit code. Stdout is the same after removing exactly the two named
  diffs, plus the ZI-07 transport texts. The reference copy is pinned against
  `git show 4fe8080:zoning_import.py`; the pin skips cleanly when that commit
  is absent, as on a shallow CI checkout. The wire-URL test covers every
  query shape.
- **Fresh-interpreter imports (ZI-01).** Re-read `ImportCycleTests`: each
  module imports first and each CLI runs `--help`. The `__main__` double-copy
  case is covered. rules_scrape's top level has only side-effect-free
  constants (checked by AST), so loading the second copy is harmless.
- **Budget (ZI-02, ZI-08).** `request_budget` is a true upper bound: per hop
  it counts retries plus the robots.txt reads and their redirect hops. The
  per-source budget counts data requests only. Forced exhaustion gives
  `SourceError` and no traceback.
- **Independent probes (offline, scratch only, nothing kept).** These ran on
  a real `requests.Session` with a mounted offline adapter:
  - every request, robots.txt included, carried zoning_import's UA and
    `Accept: application/json`;
  - a `Set-Cookie` was not replayed;
  - a non-ASCII JSON body decoded the same as before;
  - an ArcGIS Online host behind a 403 robots read its layer under D1 and
    printed the one line;
  - a non-REST path on that host was refused;
  - a 302 from `gis.atlantaga.gov` to another site was refused with no
    request to the target;
  - `total_requests()` excluded the robots.txt GETs.
- **Tests.** `python -m unittest discover -s tests`: 1773 OK (base 1744).
  `python -m unittest discover -s screener/tests -t .`: 134 OK.
  `tests/zoning_golden_check.py` exits 0 ("identical to the ba4a780 record:
  12 parcels + the cycle"). The adjacency, hazards, site-fit and
  building-comps golden checks also exit 0. As the register says, the golden
  check never reaches this HTTP layer: it is necessary but not sufficient.
  The differential test is the proof.

## What was found and changed

| # | Sev | Finding | Change |
|---|---|---|---|
| R-1 | nit | `rules_scrape.py`'s module docstring and the `robots_verdict` docstring still named three tools as users of the one verdict. zoning_import is now the fourth. | Both docstrings now name zoning_import. No code change. |

Nothing else needed a change. Two observations, accepted as they stand:

- **Robots redirects in the budget count.** `total_requests()` excludes robots
  traffic by URL suffix (`/robots.txt`). A robots.txt redirect hop whose
  target also ends in `/robots.txt` is therefore excluded too, which
  contradicts the response's wording "counts as data". This only affects the
  per-source data budget, and only by at most `MAX_REDIRECTS` per host. The
  global backstop counts every request. Harmless.
- **The global budget is large by construction.** It is a backstop only;
  `MAX_REQUESTS_PER_SOURCE` stays the real guard, as the register asked.

## Live status

No live run. The only authorised database (`lr_zimp`, local) holds no Fulton
parcels, and this worktree's `sources.json` has no `zoning_sources` key, so
`make zoning-import` here exits before any request. No database or network
was used for this review. For the operator's next between-runs
`make zoning-import`, these are the only expected differences:

- up to four `robots: <host>: robots unavailable (403): ArcGIS public REST
  accepted by operator 2026-11-09` lines, one per ArcGIS Online host read;
- +1 per host contacted on the `requests:` line;
- on a misbehaving server only, the shared retry policy and its texts.

Any other difference in a count line is a regression to report, not to
accept.

## Standing risks

All hold as the register states. SR-1: a between-runs Tier 2 change to two
immutable-during-run modules. The new coupling is named: a future
`PoliteFetcher` change now also changes zoning ingestion, so such a change
is reviewed for all four tools. SR-8: messages carry hosts, statuses and
counts only, and the ArcGIS error message never prints. SR-10: no new
dependency. SR-11: stricter than before.

## Post-change adversarial review round 1

The operator's 2026-11-09 answers above still bind this round: D3 counts
unlisted districts and unreadable setback values, D2 stays as already
implemented (no flood-rule change), the null-beats-low question is
unanswered (no new root sub-score), the research.py fixes and this fetcher
round are wanted ("fix"), and the reviews/26 hazards prepare-mutation is
authorised ("can you add this yourself"). Two findings, both minor, both
documentation only. Each was checked against the code.

- **R1-1 — AUTORESEARCH_MECHANICS.md tool inventory (accepted, fixed).** The
  `zoning_import.py` entry did not say that the tool now goes through
  rules_scrape's shared fetcher and robots policy, but the `adjacency_import.py`
  entry did. The entry now says "through rules_scrape's shared fetcher and
  robots policy at 1 req/s per host", names the once-per-host D1 ArcGIS
  exception line, and points to this directory. No behaviour change.
- **R1-2 — robots.txt redirect hop in the per-source count (accepted, fixed in
  the docs; the code is kept).** The `ZoningSession.total_requests()`
  docstring and the ZI-08 disposition in `02_code_writer_response.md` both
  said a robots.txt redirect hop "counts as data". The code excludes every
  request whose URL ends in `/robots.txt`. A robots redirect normally lands on
  another `.../robots.txt`, so that hop is excluded. The behaviour is kept:
  the effect is at most `MAX_REDIRECTS` hops per host, and the global
  PoliteFetcher budget still counts every request. The docstring now
  describes the suffix rule exactly. The ZI-08 sentence in the response is
  superseded by this entry; the response file is left as written, as a
  record. New test `test_robots_redirect_hop_is_not_a_data_request`: a
  `robots.txt` that answers 301 to `www.<host>/robots.txt`, then one data
  GET. `total_requests()` is 1 and `calls` holds all 3 requests.

Not run in this round: `tests/zoning_golden_check.py`. It needs the local
corridor DB, which does not exist here. No code path changed, so the
offline equivalence proof from the main round stands.
