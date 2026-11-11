# 36 — Fixes, thresholds, and what still stands between this and the loop (2026-11-11)

The operator asked for the review and the threshold check to be finished
and the stop lifted. The review and the fixes are done. **The stop is not
lifted**: the answer key could not be rebuilt today, and two of the steps
that lifting needs depend on the rebuilt key. This note is the record and
the checklist. Counts by year and split only.

## What happened, in order

1. `05`: adversarial review of the merged rebuild. Verdict "clear after
   these fixes": four must-fixes (thresholds, a finer oracle than the
   rounded score, a baseline without a human, developers still counted as
   sellers) and seven smaller ones.
2. Operator decisions (2026-11-11): finding 4 is fixed by leaving out of
   year Y any built parcel bought at a real price on or after 1 January
   2015 and before the as-of date, middleman land flips included; this
   session makes the fixes and has them checked before lifting the stop.
3. First round of fixes in `evaluate.py`, `build_eval.py`, `PROGRAM.md`
   and the tests.
4. `06`: independent verification of that round. Verdict "clear after
   these fixes": three new must-fixes (N1 a refused `--decide` printed
   the score, N2 the import cache could score an older `rank.py`, N3
   `--null-test` was an unlogged scoring) and seven smaller ones.
5. Second round of fixes.
6. `08`: independent check of the second round, with one baseline and two
   no-op experiments run end to end through real git in a scratch clone
   (old key). Verdict "clear after these fixes": nothing found that lets
   a loop keeping to `PROGRAM.md`'s letter raise the score without
   ranking better; one must-fix (F1, the reset hint could delete a
   human's commit) and three should-fixes (F2 a fixed shuffle seed, F3
   same-day deeds in the builder, F4 a root-level file that hides
   itself).
7. Third round: F1 to F6 of `08` and its document corrections (the reset
   hint, a shuffle nobody can predict, same-day deeds, root-level names
   that shadow standard modules, a separate message when the second
   process cannot run, the test split held to the stability check).
8. `09`: independent check of the third round, again through real git in
   a scratch clone. Verdict "the third round is sound": no blocker or
   must-fix; three should-fixes (N1 a root `venv/` folder refused every
   run, N2 one branch of the reset hint could still remove a human's
   commit, N3 a root `csv.pyc` was not seen).
9. Fourth round: N1, N2, N3 and N6 of `09`. A few lines each, with tests;
   **not independently checked.** When a reset would remove a commit
   that is not a logged discard, the evaluator now prints "Do not reset"
   and no command.

## What the code does now that it did not at merge

| Review finding | Change |
|---|---|
| `05` 1, thresholds | `WIN_SHARE_TO_KEEP` 0.95 → 0.99; `MIN_GAIN` stays 2.0. `--null-test` runs 1,000 trials per setting by default, in two modes (noise per record; half the noise shared by a map cell in every year). |
| `05` 2, oracle | A decision prints the two rounded scores and their difference: no win share, no exact gain, no `recall`. A refused `--decide` prints no score. Every other scoring appends a `look`, `test` or `null` row with a hash of `rank.py`. |
| `05` 3, baseline | Only `--new-baseline` records a baseline, the first included. Any file under `screener/` changed since the best kept run refuses, documents and `tests/` aside. A new baseline prints the run it replaces and whether `rank.py` differs. After a DISCARD the printed reset takes back the experiment only (`08` F1). |
| `05` 4, developers as sellers | Builder: a built parcel bought at a real price since 1 January 2015 is left out of that year. **Not in the data yet.** |
| `05` 5 / `06` N6 / `08` F2, state and salts | Every record is scored again, in an order drawn fresh each run, by a fresh copy of `rank.py` in a new process under another hash seed. `hash` and `id` are refused. The refusal does not say how many records disagreed. |
| `05` 6, tests | `fields` is mandatory; `main()` is tested with a spy; the builder's cell, overlap and weight steps are functions with tests; the labels file carries a site hash per positive, and three data tests use it (they skip until the key is rebuilt). |
| `05` 7, empty log | The header is written when the log is missing or empty. |
| `05` 8, snapshot date | Builder: a parcel is left out of year Y when no priced deed of the first quarter's first sale day has the digest's owner as its seller. **Not in the data yet.** |
| `05` 10, reproducibility | The gzip files carry no timestamp; the deed file's SHA-256 goes into the meta. `meta.built` (the date) still changes the fingerprint from day to day. |
| `06` N2, stale bytecode | The bytes that are checked are compiled and run by the evaluator; `rank.py` is never loaded through the import cache. A linked `rank.py` is refused. |
| `06` N4, working directory | Git is asked in the repository root. |
| `06` N5 / `08` F4, code outside `screener/` | A root-level file or folder named like a standard module, or a module loaded from inside the repository but outside `screener/`, refuses every mode. |
| `06` N10 | `--decide` refuses a key without the new builder's meta; a `best_ranking.json` that does not give its logged score is refused. |

Not changed: `05` 9's numbers in the documents (they describe the old key
until the rebuild), `05` 11's test-split weakness, and `06` N7 (below).

## Thresholds: what has been measured

On the **old** key (fingerprint `3727a9a820ff`), with the shipped
`--null-test`, 1,000 trials per setting, `MIN_GAIN` 2.0 and
`WIN_SHARE_TO_KEEP` 0.99. A false keep is a keep between two rankings
made the same way.

| Noise (score points) | Per record | Half shared by the cell |
|---|---|---|
| 4 | 12 of 1,000 (1.2%) | 12 of 1,000 (1.2%) |
| 8 | 15 of 1,000 (1.5%) | 15 of 1,000 (1.5%) |
| 16 | 23 of 1,000 (2.3%) | 15 of 1,000 (1.5%) |

The verifier (`06`) re-ran one setting and got the same 23. The earlier
200-trial run at 95% (`04`'s open item) gave 0, 1 and 6 of 200 at noise
2, 4 and 8, and `05`'s 2,400-trial runs gave 1.9% and 4.8% at noise 4 and
8: the 95% rule missed the target. At 99% the rate is about 2% or under,
which was the target written down before the build. The largest-noise
case is 2.3% (95% interval roughly 1.5% to 3.4%); `05` measured that
`MIN_GAIN` 2.5 would bring it under 2% and judged 2.0 acceptable.

**These numbers are for the old key.** The rebuilt key has fewer positive
records, so they must be measured again on it before the stop is lifted.

## Why the key was not rebuilt

`build_eval` reads Georgia DOT's interstate-exit layer
(`rnhp.dot.ga.gov/hosting/rest/services/FCRS`). On 2026-11-11 from 13:51
to at least 15:05 that server answered "Token Required" for a public
layer, and its folder listings reported that its own portal component
was not running. It was still answering that at 19:19 UTC. The build
stops there (after the digests and the deed file, which were fetched).
Nothing was worked around: the old key is still in `data/`, unchanged
since merge.

The same layer feeds `exit_miles` in the live run, so an on-demand run
would fail in the same place while the outage lasts.

## Open decision for the operator (`06` N7)

The window rule starts on 1 January 2015, as decided. A developer who
bought **before** 2015, built, and sold the finished building within
about a year is therefore still counted as a seller in 2015 or 2016. On
the old key, positive records whose owner had bought at a real price in
2012 to 2014: dev 3 / 2 / 1 and test 2 / 2 / 1 for 2015 / 2016 / 2017,
none later. Opening the window three years earlier would leave those out
too. `PROGRAM.md` states the limit either way.

## To lift the stop

In one reviewed change:

1. Rebuild the key (`python -m screener.autoresearch.build_eval`) once
   the exits layer answers. Check on it what `06` lists under "Check on
   the rebuilt key": the meta, the counts by year and split against the
   old key, zero positives bought inside the window, the left-out counts
   by reason, and that the three `BuiltKeyTests` now run and pass.
2. Re-run `--control`, a plain look, and all six `--null-test` settings
   on the rebuilt key. If any setting is clearly over 2%, raise
   `MIN_GAIN` to 2.5 and run it again. Record the table here.
3. Refresh the numbers in `PROGRAM.md` and `README.md` (pools, sites,
   cells with positives, baseline, random).
4. Have the fourth round (item 9 above: `foreign_modules` and the reset
   hint in `evaluate.py`) read by someone who did not write it. The
   notes left as they are: `08` F7 to F9, `09` N4, N5 and N7.
5. Set `REVIEW_PENDING = ""` and remove the notice at the top of
   `PROGRAM.md`.

Then a human records the baseline where the loop will run
(`--decide --new-baseline`), and starts the loop from a fresh session that
reads only `PROGRAM.md`. No session that has opened `data/` or this
folder writes `exp:` commits.
