# 36 — Risk review of the design (before any code)

Independent reviewer, 2026-11-10. Read: `01_design.md`, `PROGRAM.md`,
`build_eval.py`, `evaluate.py`, `fulton.py`, `rank.py`, `screens.py`.
Nothing tracked was edited. Probes ran offline on the current 2016 key and
the cached county deed file (scratchpad `risk36/`, not in the repo).

**Verdict: do not build as written.** Two points need a design change
(1, 2). The rest can be handled in the implementation if each gets a test.

Numbers marked *measured* come from the probes. They use one cohort
(2016), so they are a proxy: more cohorts steady each site's credit but
add no new map cells. Feature-level facts about positives are left out of
this file on purpose, because the loop's session can read `reviews/`.

## Risks, most severe first

**1. BLOCKER — the keep rule keeps noise.** *Measured:* with labels
re-dealt at random (same cluster sizes, so no ranking is truly better), a
random one-step change to the weights passed "gain ≥ 0.5 and ≥ 90% of
resamples" in 8–12% of trials (two runs, 800 and 1,000 trials). The old
rule passed 0.3%. In 100 simulated 15-experiment batches, 73% kept at
least one change, 1.3 on average, for a mean spurious gain of 1.2 graded
(largest 5.5). On the real labels the same random changes were kept 9% of
the time, the same rate as under the null, so a KEEP is weak evidence.
Why: only 11 of 31 dev cells hold positives (10, 5, 5, 4, 3, 2, 1×5), about
six effective clusters. The bootstrap resamples those, and more cohorts do
not add cells. The 0.5 floor is small against the scale: baseline 14.7,
random ranking 5.8 ± 1.4. The sealed test split is no backstop either: its
12 positives sit in 5 cells, 6 of them in one.
*Change:* set the floor and win share by this null test on the built key,
to a false-keep target written down now (suggest ≤ 2% per experiment). On
2016 alone that took gain ≥ 1.5 at 90% (1.2%) or gain ≥ 1.0 at 97.5%
(2.1%). Ship the null test as `--control` output.

**2. BLOCKER — later cohorts label the developer as the seller.** The
design says the existing rules handle a site already sold by year Y. They
do not when the buyer resells. `sold_after` only asks whether the year-Y
owner later sold at a real price; it does not ask whether the building was
already there. A developer's unnamed LLC buys in 2017, builds, and sells
the finished building in 2020: the site is a positive in 2015–2017 under
the landowner and again in 2018–2019 under the developer, with that year's
ownership features. The loop is then paid to rank "a developer just bought
it" first, the opposite of a lead. *Measured:* 21 of the 46 current
positives have a later real-price sale by a different owner; for each of
2017, 2018 and 2019, 6 of 46 were sold once before the as-of date and
again after it (an upper bound: some will fail the pool screens). Where a
year built is on file, 1–2 of the 6 are sales after the building; 2 of 20
in today's 2016 key already are.
*Change:* a site counts in year Y only if the year-Y owner's sale precedes
the building (`YrBlt` in `Tax_StructureCommercial.txt`, or the first
digest year showing the improvement); otherwise leave the record out, as
"built but not sold" is today. Test with a synthetic A → developer → REIT
deed chain.

**3. MUST-HANDLE — the snapshot date of each year is assumed, not
checked.** `load_cama` cuts deeds at 1 January Y, but the owner, class and
land value come from whatever date the county exported `Tax_Parcels<Y>`.
If a snapshot is later than January, a mid-year buyer is paired with the
previous owner's deed history, and values may already reflect the sale.
*Test at build, per cohort:* for pool parcels with one transfer deed near
the as-of date, tabulate whether the snapshot owner matches the grantor or
not by deed month. Drop or re-date a cohort that fails. Also compare pool
counts by jurisdiction year on year: South Fulton incorporated in 2017 and
`TaxDist` codes may differ.

**4. MUST-HANDLE — hidden fields are a deny-list.** `ranking()` passes
every record field not in `HIDDEN`. A new `site`, `cohort` or `weight`
field reaches `rank.py` unless someone remembers to add it; `weight`
exists only for positives. *Change:* pass an allow-list (`meta.features`
plus the blanked fields). Keep weights in the labels file, not in the
records. Test: a spy scorer sees exactly the allowed keys.

**5. MUST-HANDLE — one site in two cells.** The cell comes from each
year's centroid. A split, a road taking or re-drawn lines can move it, so
a site could be dev in one year and test in another, and its years would
not be drawn together. A replatted parcel gets a new id, so one deal can
count as two or three "sites". *Change:* assign the cell once per site.
Build must assert one block and one split per site, every 2016 record
keeping its current split, and no dev record overlapping a test record's
ground in any year.

**6. MUST-HANDLE — the credit curve pays for the unreviewed middle.**
Halving a percentile is worth 0.215 anywhere from 50% to 2%. Ten sites
moved from 50% to 25% earn 2.15 with nothing new in the reviewed list, and
that can outweigh two sites slipping from the top 2% to 5% (−0.57).
*Measured:* of 38 changes the proposed rule kept on real labels, 23 left
top-2% hits unchanged. *Change:* refuse a keep that lowers weighted hits,
and consider zero credit from 20–25%.

**7. MUST-HANDLE — score feedback is a finer oracle.** The agent sees
`graded` for every run, kept or not. 3,176 of 3,180 records are unique on
the map-derived fields (1,936 on exit distance and rail distance alone),
and those values repeat in every cohort, so a narrow band singles out one
site in all its years. Decimal feedback lets a loop find such bands
without opening `data/`. *Change:* print `graded` to the loop rounded to
0.1; compare unrounded values internally.

**8. MUST-HANDLE — log and best-run mechanics.**
- `_best_kept` does `int(r["hits"])`; weighted hits are not integers, and
  the best must be chosen by `graded`. Otherwise it crashes, or picks a
  different run than `best_ranking.json` holds and refuses forever.
- Do not compare against a rounded `graded` read from the log. Recompute
  the best run's score from its saved order in the same process.
- An existing `experiments.tsv` has the old header. Check the header and
  refuse with a clear message; never append under a mismatched one.
- A changed fingerprint makes the first `--decide` record a baseline with
  no `--new-baseline` and no "only `rank.py`" check. Use an explicit
  cohort list (not a glob), fail on a missing file, hash names with
  contents, and require `--new-baseline` when the log holds other keys.
- `key_for` reads the module-level `AS_OF`. In a loop over years, keys
  must take the year; assert they are unique across cohorts and unchanged
  for 2016.
- `RANK_BANNED_STRINGS` names only `fulton_2016`.
- Run time is not a risk: 0.12 s per decision per cohort *(measured)*.

**9. MUST-HANDLE — process.** `README.md` already tells the loop what the
developer sites look like. The "check on the built data" for time leaks
will produce more such facts. Notes after the build should carry counts by
cohort and split only. Add `reviews/36_*` to PROGRAM.md's do-not-open
list. The build commit must not touch `rank.py`. The builder should not
run `--test`. The design does not say who updates `PROGRAM.md`, `README.md`
and the tests, which the delegation does not cover.

**10. NOTE — the hashes are not anonymous.** *Measured:* 3,164 of 3,180
keys and all 46 positives reverse to public parcel ids in about a minute
using the county file; every block id reverses to its map cell from 3,600
guesses. `rank.py` never sees these, so the score is not affected, but
"no parcel ids or coordinates" is not true in effect. The frozen test
blocks should live in `data/`, not as a constant in `build_eval.py`, which
the loop may read.

**11. NOTE — weights.** 1 / years-positive is sound for units. On the
current positives it gives the 2015 and 2016 cohorts about 60% of the
total weight and 2019 about 8%, and puts roughly a quarter on views taken
under 12 months before the sale (15% today), when a deal is often already
in motion. Consider leaving out a site's sale-year view.

## Under-specified (two builders would differ)

- Percentile: rank over the pool or over ranked parcels; credit at rank K
  (45 / 2,229 is 2.019%, so the last hit earns 0.997).
- Bootstrap: re-rank inside each draw, or resample fixed credits.
- Format of `best_ranking.json` with several cohorts.
- What `hits`, `positives` and `sites` mean in the log.
- The acceptable test share and what happens outside it.
- Whether all cohorts must share one deed file, one 2025 pull and one
  `meta.screens` (assert it).

## Design decisions I would change

1. Calibrate the keep thresholds by a null test and fix the target now.
2. Add the "sale before the building" rule to the positive definition.
3. Add a no-regression guard on top-2% hits.
4. Put site weights in the labels file and pass features by allow-list.
5. Assign cell and split per site; keep the test-block list in `data/`.
6. State the expected gain honestly: steadier credit per site, no new
   independent clusters. Only more counties add those (`reviews/35`).
