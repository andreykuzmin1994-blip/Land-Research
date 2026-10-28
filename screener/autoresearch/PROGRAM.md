# AutoResearch for the land screener

The Karpathy loop, pointed at a score the agent cannot game. An agent
improves **one file**, `screener/rank.py`, and keeps a change only if the
ranking gets better at finding land that owners actually sold to
industrial developers.

## The score

`python -m screener.autoresearch.evaluate` answers one question: standing
in January 2016 with only the public data available then, how many of the
Fulton parcels that the owner sold, and someone then built an industrial
building on by 2025, would the ranking have put in its top 2%?

- **Pool:** about 3,300 Fulton parcels (5–50 acres) from the 2016 tax
  digest that pass the live tool's ownership and land-only screens.
- **Answer key:** the 51 pool parcels that the 2016 owner sold in 2016 or
  later at a real price (at least $50k per the county deed file) and that
  are at least half covered by a 2025-digest parcel carrying $1M+ of
  industrial improvements. Right-of-way deeds and sales to a development
  authority don't count.
- **Left out:** 44 parcels that got industrial buildings without such a
  sale (owner-users, corporate successors, replatted ids) are dropped from
  the pool entirely, so they count neither way.
- **Splits:** dev (39 positives, about 2,300 parcels) is what the loop
  sees. Test (12 positives) is sealed: a human checks it every ~20 kept
  experiments. The split is by ~5 km map cell, so test sites are in
  places the loop never saw.
- **Fixed size:** the top K is fixed at 2% of the pool, so inflating scores
  can't help. Only ranking real sites higher does. The hard screens are
  frozen into the evaluation set.
- **Baseline (2026-10-28):** 2 hits out of 39 on dev (top 46). Random
  ranking averages 0.86 (5th–95th percentile 0–3). The hand-built weights
  are barely better than chance, so there is a lot to learn.

Built by `build_eval.py` (humans only, between runs). It writes the frozen
files in `data/`. Blank in the evaluation set, because their values today
were often changed for the project or weren't knowable in 2016: zoning,
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
"why_seller": [...]}`. **Keep `score` on a 0–100 scale**, with the best
parcels near the top of it. `--decide` refuses scores outside 0–100,
because the live score adds a fixed 35% for zoning and slope on the same
scale. The live tool shows `why_*` in the briefs, so keep them true and
readable.

## Rules for the agent

1. Edit **only** `screener/rank.py`. An experiment is one commit that
   touches only that file; `--decide` refuses anything else.
2. Never open the files in `data/`, `experiments.tsv` or
   `best_ranking.json`. The score is the only feedback.
3. Keep `learned_score` pure and general. No lookups of specific values
   that single out a handful of parcels (exact acreages, land values or
   codes chosen to hit known answers). Prefer smooth curves and
   explainable signals.
4. One idea per experiment. Examples: reshape the access curve, weight
   industrial context more, use land class (commercial / industrial tracts),
   rethink which seller signals matter, combine signals (access ×
   context), reward size within 5–50 acres.
5. Keep `why_site` / `why_seller` honest. They are the user-facing
   explanation of the score.

The zoning, slope, utilities and incentive terms live in
`screener/scoring.py`, which the loop may not touch. They are fixed at
35% of the live score.

## The loop

On the current working branch, with no uncommitted changes. The frozen
key in `data/` is committed with the code.

```bash
python -m screener.autoresearch.evaluate --decide   # first run records the baseline
# then, for each experiment:
#   edit screener/rank.py (one idea)
git commit -am "exp: <what you changed>"
python -m screener.autoresearch.evaluate --decide
#   DISCARD -> run the exact `git reset --hard <parent>` it prints
#   KEEP    -> carry on from here
```

A change is kept only if it adds at least one hit over the best kept run
**and** beats it in at least 90% of clustered bootstrap resamples (whole
map cells are resampled, because sites cluster). Every run is appended to
`screener/autoresearch/experiments.tsv` (untracked, append-only).

If a human changes the evaluation itself (`evaluate.py`, `screens.py` or
`data/`), `--decide` refuses until a human runs
`python -m screener.autoresearch.evaluate --decide --new-baseline`.

Run a bounded batch ("run 15 experiments"), not an endless loop. Then
report the kept changes and the dev score, and ask a human to run
`python -m screener.autoresearch.evaluate --test`. Kept changes flow into
the live ranking, because the live tool scores with the same `rank.py`.

## Known limits (state them in reports)

- 39 dev positives, so scores move in steps of one site. That's why the
  noise bar exists. Expect a handful of keeps, not dozens.
- Flood maps, exits and rail main lines are today's versions, not 2016's.
  A site filled out of the floodplain looks better than it did then.
- The key rewards "sold to a developer who built". Land that would have
  been perfect but wasn't sold looks like a miss. Long-held land that never
  traded is the opportunity the live tool hunts for, so don't let the
  seller signals collapse entirely toward "already corporate".
- "Built on" means the parcel overlaps a 2025 parcel with $1M+ of
  industrial improvements. That's usually a new building, but can be an
  expansion of a neighbour's site.
- Your own pursue/pass marks (`screener/inputs/decisions.csv`) are not in
  the score yet. Add them as a second term once there are about 30.
