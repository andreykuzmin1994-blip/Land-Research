# AutoResearch for the land screener

The Karpathy loop, pointed at a score the agent cannot game. An agent
improves **one file**, `screener/rank.py`, and keeps a change only if the
ranking gets better at finding land that industrial developers actually
bought.

## The score

`python -m screener.autoresearch.evaluate` answers one question: standing
in January 2018 with only the public data available then, how many of the
Fulton parcels that developers bought and built warehouses on by 2025 would
the ranking have put in its top 2%?

- **Pool:** about 3,000 Fulton land parcels (5–50 acres) from the 2018 tax
  digest, after the same ownership screens as the live tool.
- **Answer key:** pool parcels that, in the 2025 digest, are at least half
  covered by an industrial building worth $1M+ and have a new owner.
- **Splits:** dev (about three quarters of ~5 km map cells) is what the loop
  sees. Test (the rest) is sealed: humans check it every ~20 kept
  experiments to catch overfitting.
- **Why it can't be gamed:** loosening screens or inflating scores doesn't
  help. The top-K size is fixed, so only putting real developer sites
  higher raises the score.

Built by `build_eval.py` (humans only, between runs). It writes the frozen
files in `data/`. Zoning, utilities and road type are blank in the
evaluation set, because today's values were often changed *for* the
project. Owner names, parcel ids and coordinates are not in the records.

## Rules for the agent

1. Edit **only** `screener/rank.py`. Never edit `evaluate.py`,
   `build_eval.py`, `screens.py`, `settings.toml` or anything in `data/`,
   and never open the files in `data/`.
2. Don't touch the entitlement or utilities terms (`ENTITLEMENT_VALUE`,
   `SITE_WEIGHTS["entitlement"]`, `SITE_WEIGHTS["utilities"]`, `_utilities`).
   The evaluation set has no zoning or utility data, so the score can't
   measure them, and a change there would alter the live list without
   evidence.
3. Keep `score()` pure and generic. No lookups by parcel key, no
   coordinates, no thresholds fitted to single parcels.
4. One idea per experiment. Example ideas: reshape the access curve, weight
   industrial context more, use relief instead of steep share, reward
   longer holding periods, combine signals (e.g. access × context).

## The loop

On a branch named `autoresearch-screener/<date>`:

```bash
python -m screener.autoresearch.evaluate --decide   # first run records the baseline
# then, for each experiment:
#   edit screener/rank.py (one idea)
git commit -am "exp: <what you changed>"
python -m screener.autoresearch.evaluate --decide
#   DISCARD -> git reset --hard HEAD~1
#   KEEP    -> carry on from here
```

A change is kept only if it adds at least one hit **and** beats the
bootstrap noise of the best kept run. Every run is appended to
`screener/autoresearch/experiments.tsv` (untracked, append-only).

Run a bounded batch ("run 15 experiments"), not an endless loop. Then
report the kept changes and the dev score, and ask a human to run
`--test`. Kept changes flow into the live ranking automatically, because
the live tool uses the same `rank.py`.

## Known limits (be honest about them in reports)

- About 100 positives in total, so scores move in steps of one site.
  That's why the noise bar exists.
- Flood maps, contamination lists, exits and lidar are today's versions,
  not 2018's. A site filled out of the floodplain or graded flat before
  the 2018–19 lidar flight looks better than it did then.
- "Developer bought it" is a proxy for "good site". Sites a developer
  would have loved but couldn't buy look like misses.
- Your own pursue/pass marks (`screener/inputs/decisions.csv`) are not in
  the score yet. Add them once there are about 30.
