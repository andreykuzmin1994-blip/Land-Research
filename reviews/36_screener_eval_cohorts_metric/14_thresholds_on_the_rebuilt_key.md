# 36 — The rebuilt key and its thresholds (2026-11-12)

The record that `07`'s checklist asks for. Counts by year and split only.

## The key

Rebuilt on 2026-11-12 under the builder rules of 2026-11-11, once Georgia
DOT's exits layer answered again. The operator kept the window rule's
start at 1 January 2015 (`07`, "Open decision"). It was rebuilt twice
more the same day:

1. After `11` found that the deed file's 30-character cut left two dev
   sites out (operator's decision: fix `owners.same_owner`).
2. After `13` found that the first version of that fix also matched
   different owners. The rule now applies to the deed seller only, at
   exactly 30 characters.

The key in `data/` (fingerprint `4eb1c1de9059`):

| Year | Dev pool | Dev positive records | Test pool | Test positive records |
|---|---|---|---|---|
| 2015 | 2,218 | 31 | 940 | 10 |
| 2016 | 2,216 | 28 | 945 | 10 |
| 2017 | 2,166 | 24 | 939 | 8 |
| 2018 | 2,130 | 14 | 912 | 6 |
| 2019 | 2,094 | 10 | 911 | 6 |

45 distinct sites, 35 dev and 10 test (test share 22%). Dev positives
sit in 11 map cells, test positives in 4. The old key had 49 sites (38 /
11).

Checked on it, by this session and by `11` and `13`:
- the meta carries `window_start` 2015-01-01 and the deed file's SHA-256, the same in every year;
- the gzip time is 0;
- two builds on one day are byte-identical;
- every site's weights sum to 1, each in one block and one split;
- no positive was bought at a real price inside the window;
- the three `BuiltKeyTests` run and pass;
- `11` replayed the build from the cache and read every positive's deed chain; no label contradicts its chain.

Still in the key, by design: a handful of 2015–2017 positives whose owner
bought in 2012–2014 (`11`, item 3). `PROGRAM.md` states the limit.

## Scores (dev only)

- `--control` (random ranking): graded mean 3.06 (5th–95th percentile 1.69–4.55), hits mean 0.82.
- A plain look at today's `rank.py`: graded 11.7, hits 2.4, 107 positive records, 35 sites, K 216.

## Null test

The shipped `--null-test`, 1,000 trials per setting, `WIN_SHARE_TO_KEEP`
0.99, on the key above. A false keep is a keep between two rankings made
the same way.

| Noise (score points) | `MIN_GAIN` 2.0, per record | 2.0, half shared by the cell | **2.5, per record** | **2.5, half shared by the cell** |
|---|---|---|---|---|
| 4 | 11 (1.1%) | 9 (0.9%) | 4 (0.4%) | 8 (0.8%) |
| 8 | 13 (1.3%) | 17 (1.7%) | 9 (0.9%) | 17 (1.7%) |
| 16 | 29 (2.9%) | 17 (1.7%) | 24 (2.4%) | 17 (1.7%) |

At 2.0 the largest-noise case was 2.9% (95% interval about 2.0% to 4.1%),
clearly over the 2% target, so `MIN_GAIN` was raised to 2.5 as `07` said.
At 2.5 the worst case is 2.4% (about 1.5% to 3.6%). That is the old key's
2.3%, which `05` judged acceptable. The cost is that a real gain of
between 2.0 and 2.5 graded points is now discarded.

On the day's first rebuild (fingerprint `d2a7c5649330`, before the name
fix), at 2.0: per record 0.6 / 1.0 / 2.4%, half shared by the cell
1.1 / 1.6 / 1.5% (noise 4 / 8 / 16).

## Reviews of the code since `07`

- Evaluator: rounds 4, 5 and 6 were each read by a reviewer who had not written them (`10`, `12`, `13`). The last round (hex-only log rows, a repository folder on `sys.path`) and the `MIN_GAIN` change are read in `15`.
- Owner-name rule: `11` and `13`.
