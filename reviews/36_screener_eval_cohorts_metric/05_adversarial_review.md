# 36 — Adversarial review after the change (commit `a6bfa6c`)

Independent reviewer, 2026-11-10. I did not write the change. Nothing
tracked was edited. `--test`, `--decide` and `build_eval.main` were not
run, the test split was not scored, and no network request was made:
build helpers were called read-only on the local cache with the network
blocked, and `decide()` in-process on a synthetic pool with temp files,
as the repo's tests do. Probes are in the session scratchpad
(`review36/`). Line numbers are those of `a6bfa6c`. This file carries
counts by year and split only; facts about what the positive sites look
like went to the operator in the review message.

**Verdict: GO with fixes.** The key is built as described and the scoring
arithmetic is correct. The loop must not run until the three MUST-FIX
items are done: keep `REVIEW_PENDING` (added in `c7054a0`) set until then.
Items 4 and 5 need a rebuild of `data/`, so do them before any baseline.

## What holds (checked, not assumed)

- Replaying the labelling from the cache gives the same pool and the same
  positives in all five years (3,175 / 3,173 / 3,123 / 3,058 / 3,021
  records; 40 / 39 / 40 / 26 / 19 positives).
- Each of the 49 ids' weights sums to 1; dev totals 38.0, test 11.0; no
  id has two cells. Every record's split follows `test_blocks.json` in
  every year and no block is in both splits. All 3,173 records of 2016
  keep their old split, block and features; the only change is the 7
  positives left out. No dev parcel shares more than 0.03% of its ground
  with a test parcel (the 1% tolerance is sound; it was never reached).
- `credit`, `score_orders`, the per-draw K, bootstrap copies and the
  draws match an independent re-implementation exactly, and are
  deterministic. The scorer receives only the allow-list.
- README and PROGRAM numbers (164 records, 49 sites, 38 / 11, K about 44,
  graded 12.2, hits 1.9, random 3.4 and 0.9) match what the code prints.

## Findings, most severe first

**1. MUST-FIX — the score feedback is a working oracle (risk 7 not
handled in effect).** Three leaks: (a) `evaluate.py:323` prints `recall`
to three decimals, which is `graded` to ±0.02, not ±0.05; (b) plain
`evaluate` (`main`, 791–810) scores an uncommitted `rank.py`, logs nothing
and can be run without limit; (c) the DECISION line (710–712) prints the
win share: moving one positive record by one score point shows "+0.0"
graded 93% of the time but "wins 63%", against "wins 0%" for a negative
record (60 trials each). *Failing scenario, run on dev using only printed
numbers:* 400 unlogged looks at narrow bands of two map-derived fields
found 47 bands that raise the printed score; the best 3 together are
KEPT (gain +3.4, hits +4.3, wins 97.0%) while touching 1.1% of the pool;
12 bands take dev from 12.2 to 21.5. Kept changes go straight into the
live ranking. *Fix:* drop `recall`; print KEEP or DISCARD and the failed
condition only, not the win share (log it to one decimal at most); make
every scored look a logged row with a per-batch budget, or refuse to
score a `rank.py` that is not a committed, logged experiment.

**2. MUST-FIX — the keep thresholds miss the 2% target, and `null_test`
understates.** `null_test` (399–401) adds independent noise to every
record-year. A change to `rank.py` moves a site the same way in all its
years, so that noise averages out where real changes do not. Measured on
dev, 1,200 trials each, rule as shipped (+2.0, 95%, hits not lower):

| Null (two noisy copies of the current ranking) | noise 4 | noise 8 |
|---|---|---|
| independent per record (as shipped) | 2.1% | 4.8% |
| same noise in every year of a site | 3.4% | 5.8% |

At 3.4–5.8% a 15-experiment batch keeps at least one non-improvement 40–
59% of the time. With the win share at 99% the four cells are 0.9%, 1.8%,
1.3%, 1.8%; raising `MIN_GAIN` to 3.0 at 95% leaves 0.0–4.8%. Power at
99% against 95% (site noise 4, 300 trials): small real gain kept 31%
against 50%, medium 80% against 92%. *Recommend:* `WIN_SHARE_TO_KEEP =
0.99` with `BOOTSTRAP = 2000` (1% of 500 is 5 draws), `MIN_GAIN = 2.0`
unchanged; make `null_test` draw noise per site; record its output here.
If the operator prefers 95%, write down the 3–6% rate instead of "rarely".

**3. MUST-FIX — a baseline can be recorded, and the bar removed, without
a human.** (a) With no `experiments.tsv` (fresh clone, or both untracked
files deleted) the next `--decide` records a baseline with no
`--new-baseline` and skips the only-`rank.py` check, which sits under `if
best is not None` (687–691): a commit that also edits `evaluate.py` was
recorded as the baseline in my probe. PROGRAM.md's loop block says the
first run does this; `04` says a human records it. (b) `_saved_best`
(612–624) accepts `"order": {}`; `decide` then prints "best kept 0.0" and,
on a synthetic pool, KEPT a ranking at 4.0 against a logged best of 8.4. (c) An empty log file
gets a row with no header (`_append`, 627–633) and every later run is
refused. *Fix:* require `--new-baseline` for every baseline; refuse when
the rescored best does not equal the best row's logged `graded` and
`hits`; write the header when the file is empty.

**4. SHOULD-FIX — "before any building" is not what the rule measures**
(`build_eval.py` 169–202). The clock is the first digest that shows $1M+
of industrial improvements, not the building. (a) Against the county's
year-built file, for the 20 sites with a new structure on record the
first digest comes 0–5 years after the year built (median 2; 1 would be
normal), so a finished building could be resold in that window and
count; in this key none did (each positive checked). (b) `built_window` calls a sale in the year before the first
digest "surely before"; a building first shown in 2019 went up during
2018, and `classify` returns "positive" for a developer who bought in
2017 and sold for $30M in December 2018 (run). 4 sites (3 dev, 1 test)
have their sale in that year. (c) 6 of 49 sites (4 dev, 2 test) fail a
cross-check of "before any building" against the structure file, and 1
of the 9 ids left out as "sold after the building" fails it the other
way. *Fix:* use year built where on file; treat the year before the
first digest as "can't be ordered".

**5. SHOULD-FIX — sites are parcel ids, not ground** (311–337). One dev
site is counted three times: two ids and the merged id that replaced
them, 3.0 of the 38 dev units in one cell. The 38 dev sites are 31
distinct sale dates within a cell (test: 11 sites, 9). 9 sites (8 dev, in 7 cells)
are positive in one year only and put a full unit on one record; dev
weight by year is 10.6 / 8.6 / 8.8 / 6.0 / 4.1, and 40% of dev weight
(25% of test) is on views taken under 12 months before the sale (risk
11, not taken up). *Fix:* merge ids that share ground; consider leaving
out the sale-year view.

**6. SHOULD-FIX — `rank.py` can keep state between calls.** A module-level
dict or a mutable default passes `rank_problems` (only `global` /
`nonlocal` are refused, 518). Records are scored in year order in one
process, so state recovers the year and each parcel's earlier years,
which the allow-list is meant to hide. *Fix:* score every record twice
in different orders and refuse if any score differs.

**7. SHOULD-FIX — docs.** PROGRAM.md:169 says 12 dev map cells hold
sites; it is 10 (11, 7, 5, 3, 3, 3, 3, 1, 1, 1). PROGRAM.md:34–35 says
test sites are "in places the loop never saw": 2 of 11 lie within 150 m
of a dev positive and 6 within 1.2 km. `04` reads the snapshot check as
late digests; split by month, January deeds show the seller in 38 of 45
cases and December deeds the buyer in 84 of 92, so the digests are from
about 1 January and the 61 of 186 mismatches (mostly February and March)
are more likely name matching. `--test` and `--null-test` are open to the loop.

**8. NOTE — tests.** 511 pass. Vacuous or thin: the null test only
asserts `0 <= false_keeps <= trials` (187–194); banned fields are checked
on the first 500 records only (389–390; all 15,550 are blank, checked);
`test_weights` would pass with a site summing to 2. Untested: everything
in `build_eval.main` (cell per site, the shared-ground drop, which never
fired, weights), `covered_share`, a parcel covered before the as-of year
(behaves: left out), that `main` passes the allow-list, and that nothing
printed is finer than one decimal.

## The pre-change risk review, item by item

| Risk | Status |
|---|---|
| 1 keep rule keeps noise | Partly: tightened and `--null-test` shipped; not calibrated on the built key; finding 2 |
| 2 developer as seller | Partly: rule and a real test; finding 4, and an intermediate buyer still counts in later years (finding 5) |
| 3 snapshot date | Partly: checked by quarter, logged, no pass rule; holds by month (finding 7). Jurisdiction counts logged, not recorded |
| 4 allow-list | Handled, spy test real; state is the gap (finding 6) |
| 5 one site, two cells | Partly: one cell per id, splits frozen, overlap drop; no merge of replatted ids (finding 5) |
| 6 credit for the middle | Handled: zero below 10 × K, hits may not fall; both tested |
| 7 finer oracle | Not handled in effect (finding 1) |
| 8 log mechanics | Mostly handled and tested; finding 3 |
| 9 process | Handled: README sentence removed, notes carry counts, `reviews/36` on the do-not-open list |
| 10 hashes | Handled: docstring is honest, test blocks live in `data/` |
| 11 weights, sale-year view | Not handled (finding 5) |
| Under-specified | Percentile, bootstrap, `best_ranking.json` and log columns are now fixed in code and tests. Not specified: the acceptable test share (22% as built, no bound) |
