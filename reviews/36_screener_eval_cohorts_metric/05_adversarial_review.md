# 36 — Adversarial post-change review of the rebuilt screener evaluation

Reviewer: independent, fresh context, 2026-11-10, on `75836c0` (rebuild `a6bfa6c` + `c7054a0`).
A first reviewer with the same brief was killed by a container restart; I read its full command
log and re-ran what the findings rest on. Where a number comes from its saved output and not
my own run, it says "first reviewer's probe". Nothing tracked was edited; `--test` was not run;
no test score was computed. Counts are by as-of year and split only. Figures that describe
traits of the positives are withheld (see the end).

## Verdict: clear after these fixes

Findings 1-4 must be fixed before `REVIEW_PENDING` is cleared. The metric arithmetic itself is
right (checked against a brute-force reference), and the data matches the status note `04`.

## Findings, most severe first

**1. BLOCKER (for lifting as is) — the shipped thresholds miss the 2% target for larger changes.**
*Verified by running.* The shipped null (`null_test`: two rankings = base score + independent
noise), re-run with the shipped `score_orders` / `paired_win_share` and my own seeds, 2,400
trials per level: noise 4 → 45 false keeps (1.9%, 95% CI 1.4-2.5); noise 8 → 116 (4.8%, CI
4.0-5.8). The shipped 200-trial run (`nulltest_200.json`) already showed 6/200 = 3.0% at noise
8; 200 trials cannot resolve a 2% target. Why: at 95% the bootstrap is a nominal 5% test, and
`MIN_GAIN` only binds for small changes (gain SD 1.0 at noise 4, 1.5 at noise 8; first
reviewer's probe: 6.3% of 1,000 at noise 16). The docstring and comment "set with --null-test
on the key as built" (`evaluate.py:35,119`) are not true: `04` says the thresholds came from
the pre-change simulation. *Fix:* `WIN_SHARE_TO_KEEP = 0.99` (see the null section), re-run
and record `--null-test` with at least 1,000 trials per level, correct the two comments.

**2. MUST-FIX — the win share, the gain's sign and `recall` are a finer oracle than the rounded
score (risk 7 is not handled).** *Verified by running* (shipped functions): a +0.4 bump on a
three-record band of one allowed field that holds one dev site prints
`(+0.0); wins 65% of resamples` and plain-mode `recall 0.321`; the same bump on a band with no
positive prints `(-0.0); wins 0%` and `recall 0.32`. `graded` shows 12.2 in both. One
experiment thus answers "is there a known site in this band" with no visible score change.
Plain mode (no `--decide`) scores an uncommitted `rank.py` and logs nothing, so such questions
are unlimited and leave no trace. A large bump on the same band shows +0.4 graded and +1.0
hits, so one-decimal rounding never stopped band probing; only rule 3 and a human reading the
kept diffs do. *Fix:* on DISCARD print no win share (or only "below 95%") and the gain as the
difference of the rounded scores; drop `recall` or derive it from the rounded `graded`
(`evaluate.py:330,719-721`); make plain mode append a row (status `look`, with a hash of
`rank.py`) or refuse a dirty tree.

**3. MUST-FIX — a baseline can be created, and the bar reset, without a human.**
*Verified by running* in a scratch clone with the stop lifted in memory only:
(a) `--decide` with no `experiments.tsv` records BASELINE with no `--new-baseline`; both
record files are untracked, so every new container starts there (this review lost its
container once);
(b) after `rm experiments.tsv best_ranking.json`, `--decide` records BASELINE at a HEAD that
holds a discarded experiment;
(c) a commit that edits `screener/autoresearch/__init__.py`, followed by a `rank.py`-only
commit, is scored, not refused: only `HEAD~1..HEAD` is checked (`evaluate.py:697`), and
`EVAL_FILES` (`:112`) omits both `__init__.py` files, whose code runs inside the evaluator.
*Fix:* refuse when there is no baseline for this key unless `--new-baseline` is given; require
`git diff --name-only <best>..HEAD -- screener` to be exactly `screener/rank.py`; say in
PROGRAM.md that a human records the first baseline.

**4. MUST-FIX — blocker 2 is handled only to digest-year resolution; the buyer of the first
sale is still a "seller" in later years.** *Verified by running* `classify`: landowner sells
in June 2016, the developer builds in 2018 and sells the finished building in November 2018,
first shown in the 2019 digest → the developer's 2017 and 2018 records are `positive` (a sale
on 2 January 2019 is correctly left out). `built_window`'s docstring ("a sale before the first
date preceded the building") is wrong by up to a year, more if the assessor is late. A
middleman's land flip is also a positive, which may be intended. On the built key, positive
records whose as-of owner had itself bought the parcel at a real price inside the 2015-2019
window exist in every year from 2016 in dev and from 2017 in test; a probe on that trait shows
the residue is large enough to matter (counts and probe figures withheld). The records hold no
deed dates, so I could not split finished-building resales from land flips. *Fix (needs a
rebuild and a new baseline):* leave out of year Y a built-by-2025 parcel whose as-of owner
acquired it at `MIN_PRICE` or more on or after 1 January of the first cohort year; or have the
operator accept middleman sales in writing and leave out only in-window buyers who sell within
a year of the first digest that shows the building. Add the November-2018 chain as a test and
state the limit in PROGRAM.md.

**5. SHOULD-FIX — `rank.py` can tell records apart by call order, and can be non-deterministic.**
*Verified by running* `rank_problems` on sample sources: a mutable module-level list or dict,
a function attribute and a mutable default argument all pass (only `global` / `nonlocal` are
refused); so do `hash()` (salted per process) and `id()`. `ranking()` calls the scorer in file
order, the same every run, year by year, and neighbours in the file are often in the same
cell (649-919 block runs per dev year against 31-32 cells). A call counter is therefore a
record id, and a salted score re-rolls the false-keep chance on every commit. *Fix:* score
every record twice, the second pass in another order, and refuse if any score differs; add
`hash` and `id` to `RANK_BANNED_NAMES`.

**6. SHOULD-FIX — tests would not catch regressions of several handled items.** *Verified by
running* mutations in a scratch clone against `test_eval_key` + `test_rules`. Not caught:
`main()` no longer passing the allow-list (`fields = None`); `main()` printing unrounded
scores; in `build_eval.main`, weight 1 per record, the cell taken per year instead of once per
site, and the dev-on-test-ground drop removed; ties counted as bootstrap wins; `null_test`
scoring one ranking against itself. Caught: the sale-before-building bound, cells drawn per
year, the HEAD-only `rank.py` check. *Fix:* make `fields` mandatory in `ranking` / `evaluate`
(the default is still the old deny-list, `evaluate.py:237`); a spy test through `main()`; move
the weight, cell and overlap steps of `build_eval.main` into functions with tests; write a
site hash per positive into the labels file (not the records) so a data test can assert one
cell per site and weights that sum to 1.

**7. SHOULD-FIX — a zero-byte `experiments.tsv` corrupts the log.** *Verified by running:*
`_log_header_problem` accepts an empty file, `_append` then writes a row with no header
(`evaluate.py:635`), and every later `--decide` refuses "another version". *Fix:* write the
header when the file is missing or empty.

**8. SHOULD-FIX — the snapshot date was measured, not corrected (risk 3).** *By reading only.*
`04` reports that 25-44% of first-quarter deeds already show the buyer in that year's digest.
Those records pair the buyer with deed history cut at 1 January (the seller's holding period).
No build gate uses `snapshot_date_check`. *Fix:* drop from year Y a parcel with a priced
transfer in the first quarter whose grantor is not the digest owner, or cut deeds at the
digest's own date.

**9. NOTE — documentation that does not match the code or data.** PROGRAM.md says "38 dev
sites in 12 map cells": 10 dev cells hold positives (and 4 test cells; 32 and 16 cells in
all). "Nothing below about the top 20%" is 20% of the pool but 23-24% of the ranked parcels,
because K counts screened-out parcels. README says `--decide` refuses when "the experiment
commit touches any file besides rank.py": see 3(c). The other numbers check out *by running*:
pools 3,021-3,175, 164 positive records, 49 sites (38 dev, 11 test), K 42-45 a year (218 in
all), baseline 12.2 / 1.9, random 3.37 (2.09-4.82) / 0.86.

**10. NOTE — the build cannot be re-checked byte for byte.** *First reviewer's probe, and
reading.* The `.gz` files carry a gzip timestamp and `meta.built`, so an identical rebuild
changes the fingerprint. The build reads live county layers and a deed file the county
replaces every year, and the deed file's identity is not in `meta`. There is no "rebuild and
compare" mode.

**11. NOTE — smaller points.** `--test` has no guard and no log row, so looks at the test
split are not counted. The test split is 11 sites in 4 cells, 6 of them in one: a weak
backstop. The DISCARD message prints `git reset --hard HEAD~1`, which is not the best kept
commit if an earlier discard was left in place. The 0-100 check uses scores rounded to one
decimal. A re-platted parcel under a new id still counts as a second site (risk 5, second
half): not checkable without parcel ids.

## Pre-change risk review, item by item

| Item | Handled? | Regression test? | Comment |
|---|---|---|---|
| 1 keep rule keeps noise | Partly | Constants pinned only | +2.0 / 95% / hits guard; null shipped as `--null-test`. Target missed at noise 8 (finding 1). |
| 2 developer as seller | Partly | Yes for sales after the first digest that shows the building; none for the year before it | Finding 4. `04`: 15 records left out as sold after the building. |
| 3 snapshot date | Measured only | Function tested, no gate | Finding 8. Pool per cell is stable year on year (first reviewer's probe: no cell moves over 15%). |
| 4 allow-list | Yes | `evaluate()` yes, `main()` no | A spy through the real `main()` saw exactly the features and blanked fields (first reviewer's probe). Weights are in the labels file. |
| 5 one site, one cell | Yes | Not the build logic | On the data, linking records across years by map fields: 0 of 4,022 linked sites sit in two cells or two splits, none changes label; site weight per cell is a whole number in all 14 cells with positives; dev 38.0, test 11.0; no cell is in both splits. 2016 keeps all 3,173 records with split and cell unchanged (first reviewer's probe). |
| 6 credit for the middle | Yes | Yes | No credit past 10 x K; a keep is refused if weighted hits fall. |
| 7 score oracle | No in effect | Log rounding only | Finding 2. |
| 8 `_best_kept`, rounding | Yes | Yes | Last row of the chain; both rankings rescored from the saved order. |
| 8 old log header | Yes | Yes | Zero-byte file: finding 7. |
| 8 fingerprint, baseline | Partly | Yes for "log holds other keys" | Named cohorts, a missing file is an error, names hashed. No-log baseline: finding 3. |
| 8 `key_for`, banned strings | Yes | Yes | Keys unique across years; 2016 keys unchanged. |
| 9 process | Mostly | n/a | Folder on the do-not-open list; the build commit did not touch `rank.py`; notes give counts only; no test score in the notes I read. PROGRAM.md's examples and limits still hint at what sites look like (older text). |
| 10 hashes | Yes | Yes | Wording corrected; test cells frozen in `data/`. |
| 11 weights, sale-year view | Not taken up | – | Dev weight by year: 28 / 22 / 23 / 16 / 11%. Sites judged on a single year's view, 2015-2019: dev 3, 0, 1, 2, 2; test 0, 0, 1, 0, 0. |
| Percentile; credit at K | Decided | Yes | Position among screen-passing parcels; K = 2% of the pool including screened-out ones. |
| Bootstrap: re-rank or fixed credit | Decided | Yes | Fixed order, copies adjacent, K per draw. Matches my brute-force reference on 40 of 40 draws and on the full pool. |
| `best_ranking.json` format | Decided | Yes | `{commit, fingerprint, order: {year: [keys]}}`. |
| Log column meanings | In code only | Partly | `positives` = records (119), `sites` = 38.0, `k` = sum over years. Not written down. |
| Test share | Reported | No | 22% of sites; nothing enforces a range. |
| One deed file, one 2025 pull, one `screens` | Partly | `load()` yes | One 2025 pull; `load()` checks features, screens and cohorts across years; the deed file is not recorded. |

## Null test: assessment and measured rates

- Perturbing both sides is the right "neither is better" null and the conservative choice.
  One side fixed at the unperturbed base is not a null: noise makes the change worse (mean
  gain −0.4 to −1.3; 0-1.3% kept; first reviewer's probe, 1,000 trials each).
- It understates in one way: its noise is independent per record, while a real edit moves a
  site's yearly views and its neighbours together. Noise shared by a site's years: 4.3% at
  noise 4, 5.1% at noise 8. Half shared by the cell: 5.5% and 6.9% (first reviewer's probe,
  1,000 trials each, re-tabulated by me).
- The pre-change reviewer's kind of null (labels re-dealt with the real cluster sizes; 83
  one-step edits against the unchanged ranking; 1,500 deals; four variants; first reviewer's
  probe): 0.08-0.49% of trials kept on average, the worst single edit 1.0-4.6%, at least one
  false keep in 15 random edits 0.9-6.3%. Typical one-step edits are safe; the largest are not.
- Selection: after a lucky keep the bar is a lucky ranking, so later equal changes look worse,
  and the per-experiment rate is an upper bound there. Over a batch it compounds: 15
  experiments at 4.8% is about one false keep in every two batches.
- False keeps by threshold (hits guard on):

| Null | +2.0 / 95% (shipped) | +2.5 / 95% | +2.0 / 99% | +2.5 / 99% |
|---|---|---|---|---|
| Shipped, noise 4 (mine, 2,400) | 1.9% | 0.8% | 1.0% | 0.5% |
| Shipped, noise 8 (mine, 2,400) | 4.8% | 3.3% | 1.7% | 1.5% |
| Shipped, noise 16 (1,000) | 6.3% | 5.6% | 2.1% | 1.9% |
| Site-shared, noise 4 / 8 (1,000) | 4.3 / 5.1% | 2.6 / 4.6% | 1.6 / 1.8% | 1.3 / 1.8% |
| Cell-shared, noise 4 / 8 (1,000) | 5.5 / 6.9% | 4.4 / 6.9% | 1.3 / 1.7% | 1.2 / 1.7% |
| Re-dealt labels, worst single edit | 1.0-4.6% | 0.4-2.7% | 0.3-2.5% | 0.1-1.3% |

- **Thresholds I would accept:** `WIN_SHARE_TO_KEEP = 0.99` with `MIN_GAIN = 2.0` ("about
  2%": 1.0-2.1% across the noise nulls); `MIN_GAIN = 2.5` as well if 2% must hold for the
  worst single edit. Raising `MIN_GAIN` alone does not work. The cost: on the real key, of
  the 83 one-step edits, 1 passes the shipped rule (gain 2.03, share 0.966) and 0 pass either
  tightened rule. Also make `--null-test` default to at least 1,000 trials and add a
  site-shared noise mode.

## Not checked

- Anything that needs the county's files: the parcel ids behind the keys, geometry overlap
  between dev and test ground, deed dates of the positives, re-platted sites, the digests'
  export dates. `build_eval.py` was reviewed by reading, by its unit tests and by its outputs.
- Power of the tightened rule (how often a real improvement is kept).
- The test split beyond counts, cells and overlap.
- The static checker beyond the cases named in finding 5; it is documented as not a sandbox.
- Tests: 511 pass (`python -m unittest discover -s screener/tests -t .`). shapely was missing;
  the first reviewer had installed it into a scratch folder with `pip --target`, and I used that.

## Withheld from this file

Counts and probe figures that describe traits of the positives (finding 4) are kept out of the
repository. They are in the reviewer's scratch folder as `WITHHELD_secondleg_counts.json` and
`WITHHELD_secondleg_probe.json`, for the operator only.
