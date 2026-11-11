# AutoResearch for the land screener

The Karpathy loop, pointed at a score the agent cannot game. An agent
improves **one file**, `screener/rank.py`, and keeps a change only if the
ranking gets better at finding land that owners actually sold to
industrial developers.

> **Do not run the loop yet (2026-11-10).** The evaluation below was
> rebuilt and merged before its adversarial review and its keep-threshold
> check were finished. `--decide` refuses until a human clears that
> (`REVIEW_PENDING` in `evaluate.py`). If you are an agent asked to run
> experiments: stop and tell the human. The thresholds below may change.

## The score

`python -m screener.autoresearch.evaluate` asks one question five times,
once for each January from 2015 to 2019: standing there with only the
public data available then, how high would the ranking have put the
Fulton land that its owner went on to sell, before any building, to
someone who built an industrial building on it by 2025?

- **Pool:** for each of the five years, about 3,000 to 3,200 Fulton
  parcels (5–50 acres) from that year's tax digest that pass the live
  tool's ownership and land-only screens.
- **Answer key:** pool parcels that the owner of that January sold later
  at a real price (at least $50k per the county deed file), with the sale
  dated before the first digest that shows the building, and that are at
  least half covered by a 2025-digest parcel carrying $1M+ of industrial
  improvements. Right-of-way deeds and sales to a development authority
  don't count. A developer selling a finished building doesn't count
  either: that is not land an owner sold to a developer. Nor does anyone
  who bought the parcel at a real price since 1 January 2015: a site
  counts under the owner who held it before then, in the years that owner
  still had it.
- **Sites, not records.** The same site is in several years (164 positive
  records, 49 distinct sites). Each positive record is weighted so that
  every distinct site adds up to 1.
- **Left out:** parcels that got industrial buildings without such a sale
  (owner-users, corporate successors, replatted ids, sales after the
  building, buyers since 2015) are dropped from the pool entirely, so they
  count neither way. So is any parcel sold in the first quarter of the
  as-of year when the digest already shows the buyer.
- **Splits:** dev (38 sites) is what the loop sees. Test (11 sites) is
  sealed: a human checks it every ~20 kept experiments. The split is by
  ~5 km map cell, the same cells in every year, so test sites are in
  places the loop never saw.
- **Ranked within each year.** A parcel is compared only with the other
  parcels of its own year; results are added up over the years.
- **Two numbers.**
  - `hits`: sites in the top 2% of their year's pool (K, about 44
    parcels a year on dev).
  - `graded`: each site earns credit by where it ranks: full credit in
    the top 2%, half at about the top 6%, nothing below the top 20% of
    the pool (the pool counts parcels the screens reject, so that is
    nearer the top quarter of the parcels actually ranked). This is the
    number a change is judged on.
- **Fixed size:** K is fixed at 2% of the pool, so inflating scores can't
  help. Only ranking real sites higher does. The hard screens are frozen
  into the evaluation set.
- **Baseline (key rebuilt 2026-11-10):** graded 12.2 of 38 dev sites, 1.9
  hits. Random ranking averages graded 3.4 (5th–95th percentile 2.1–4.8)
  and 0.9 hits. Scores from earlier keys (a single 2016 year, whole hits)
  are not comparable.

Built by `build_eval.py` (humans only, between runs). It writes the frozen
files in `data/`. Blank in the evaluation set, because their values today
were often changed for the project or weren't knowable then: zoning,
utilities, road type, slope, contamination listings, Opportunity Zones and
jurisdiction. Owner names, parcel ids and coordinates are not in the
records.

## What `rank.py` can use

`learned_score(p)` receives one parcel record with these fields (any can
be None):

`acres`, `usable_acres`, `usable_after_slope`, `flood_pct`,
`floodway_pct`, `wetland_pct`, `water_pct`, `easement_pct`,
`protected_pct`, `constrained_pct`, `exit_miles`, `rail_ft`, `rail_owner`,
`industrial_within_1mi`, `land_value`, `class_code` (Georgia digest class,
e.g. R4/C5/I4/A5/V5), `land_use_code`, `owner_type` (individual / trust /
estate / entity / religious), `owner_state`, `owner_po_box`, `years_held`,
`sales_count`, `inherited`, `distress_owned`, `last_sale_price`.

It must return `{"score": float, "motivation": float, "why_site": [...],
"why_seller": [...]}`. **Keep `score` finite and on a 0–100 scale**, with
the best parcels near the top of it. `--decide` refuses NaN, infinite or
out-of-range scores,
because the live score adds a fixed 35% for zoning and slope on the same
scale. The live tool shows `why_*` in the briefs, so keep them true and
readable.

## Rules for the agent

1. Edit **only** `screener/rank.py`. An experiment is one commit whose
   whole diff touches only that file; `--decide` refuses anything else,
   and refuses untracked files under `screener/`.
2. Never open the files in `data/`, `experiments.tsv` or
   `best_ranking.json`, and never open `reviews/36_screener_eval_cohorts_metric/`
   (it discusses how the key was built). The score is the only feedback.
   A session that has read any of these must not write `exp:` commits.
   Score an idea once, with `--decide`, after committing it. Do not score
   variations to find out which parcels are the answers: every scoring,
   with or without `--decide`, is logged with a hash of `rank.py`, and a
   human reads the log.
3. Keep `learned_score` pure and general. No lookups of specific values
   that single out a handful of parcels (exact acreages, land values or
   codes chosen to hit known answers). Prefer smooth curves and
   explainable signals.
   A score depends only on the record it is given: no counters, caches
   or other state between calls. The evaluator asks for every score
   twice, in two different orders, and refuses the run if any differs.
4. One idea per experiment. Examples: reshape the access curve, weight
   industrial context more, use land class (commercial / industrial tracts),
   rethink which seller signals matter, combine signals (access ×
   context), reward size within 5–50 acres.
5. Keep `why_site` / `why_seller` honest. They are the user-facing
   explanation of the score.

### What `rank.py` may not use

The evaluator reads `rank.py`'s source and refuses it, before importing
it and in every mode, if it uses any of:

- imports other than `__future__`, `math` and `typing` (and from `typing`
  only `Any`, `Optional`, `Union`, `Mapping`, `Sequence`, `Iterable`,
  `Callable`, `Dict`, `List`, `Tuple`, `Final`, `Literal`,
  `TYPE_CHECKING`, for annotations);
- `open`, `eval`, `exec`, `compile`, `getattr`, `globals`, `vars`, `type`,
  `print`, `str.format`, and other builtins that read files, run code or
  read attributes by name; `hash` and `id` (they change from run to run);
- private names (`_x`), dunder names, frame or code attributes, dunder
  methods, class keywords such as `metaclass=`, and `match` statements;
- `global` / `nonlocal`, or changing an attribute of an imported module;
- strings that name the evaluation files.

The refusal names the alternative (if/elif, isinstance, plain functions).

The zoning, slope, utilities and incentive terms live in
`screener/scoring.py`, which the loop may not touch. They are fixed at
35% of the live score.

## The loop

On the current working branch, with no uncommitted changes. The frozen
key in `data/` is committed with the code. A human records the baseline
before the loop starts (`--decide --new-baseline`, on the commit the loop
starts from); the loop never does.

```bash
# for each experiment:
#   edit screener/rank.py (one idea)
git commit -am "exp: <what you changed>"
python -m screener.autoresearch.evaluate --decide
#   DISCARD -> run the exact `git reset --hard <commit>` it prints
#   KEEP    -> carry on from here
#   REFUSED -> see below
```

When `--decide` prints REFUSED, it logs nothing:

- commit, untracked-file or purity refusals: fix the commit or `rank.py`
  and run it again;
- NaN, infinite, out-of-range or changing scores: fix `rank.py`;
- "there is no baseline", "files other than screener/rank.py changed", or
  a best-run record (`best_ranking.json`) that is missing or doesn't
  match: **stop and ask a human.** The loop never runs `--new-baseline`.
  The log and the best-run record are not in git, so a new cloud session
  starts without them: that is a stop, not something to work around. If
  `best_ranking.json` was lost, a human checks out the best kept commit
  and re-records it with `--decide --new-baseline`.

A change is kept only if `graded` is at least 2.0 above the best kept
run, `hits` is not lower, **and** it beats the best kept run in at least
99% of clustered bootstrap resamples (whole map cells are resampled, every
year of a cell together, because sites cluster and repeat across years).
You are shown the two scores rounded to one decimal and their difference,
nothing finer; the comparison uses exact values. Every run is appended to
`screener/autoresearch/experiments.tsv` (untracked, append-only).

If a human changes anything under `screener/` other than `rank.py`
(`evaluate.py`, `screens.py`, `data/`, any other file), `--decide` refuses
until a human runs
`python -m screener.autoresearch.evaluate --decide --new-baseline`.

Run a bounded batch ("run 15 experiments"), not an endless loop. Then
report the kept changes and the dev score, and ask a human to run
`python -m screener.autoresearch.evaluate --test`. Kept changes flow into
the live ranking, because the live tool scores with the same `rank.py`.

## Known limits (state them in reports)

- 38 dev sites in 12 map cells. The bar (+2.0 graded and 95% of
  resamples) is set so that a change that is not an improvement is rarely
  kept; it also means small real improvements are often discarded. Expect
  a handful of keeps, not dozens.
- The five years are five views of mostly the same sites. They steady the
  score; they are not five times the evidence.
- Flood maps, exits and rail main lines are today's versions, not the
  as-of year's. A site filled out of the floodplain looks better than it
  did then.
- Each year's digest is the county's snapshot from about January, not
  exactly 1 January: a sale in the first weeks of a year may already show
  the buyer.
- The key rewards "sold to a developer who built". Land that would have
  been perfect but wasn't sold looks like a miss. Long-held land that never
  traded is the opportunity the live tool hunts for, so don't let the
  seller signals collapse entirely toward "already corporate".
- "Built on" means the parcel overlaps a 2025 parcel with $1M+ of
  industrial improvements. That's usually a new building, but can be an
  expansion of a neighbour's site.
- Your own pursue/pass marks (`screener/inputs/decisions.csv`) are not in
  the score yet. Add them as a second term once there are about 30.
