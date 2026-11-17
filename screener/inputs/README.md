# Your decisions

`decisions.csv` (created on the first run, kept out of git) records what
you decided about parcels. Each run reads it:

```csv
parcel_id,decision,date,note
09F030000140097,pursue,2026-11-02,called owner - open to offers
07 290001330986,pass,2026-11-02,power line easement across the middle
14F0063  LL0219,listed,2026-11-03,on the market with a broker
14F0071 0002,maybe,2026-11-04,ask about the access road
```

- `pass`: drop it from future lists.
- `listed`: it's already for sale, so drop it (`exclude_listed` in settings).
- `pursue`: mark it in `ranked.csv`. If a later run screens it out (e.g.
  a new building or owner), `summary.md` calls that out and
  `rejected.csv` keeps the mark.
- `maybe`: a mark in `ranked.csv` / `rejected.csv` only. It changes no
  score, flag, order or screen.

The last row for a parcel wins, so add a row to change your mind (a
`maybe` after a `pass` brings the parcel back). Any other value in the
`decision` column is ignored.

## Recording a decision from the command line

```bash
python -m screener.decide "14F0063  LL0219" pursue called owner, open to offers
python -m screener.decide 09F030000140097 maybe
python -m screener.decide "07 290001330986" pass --run screener/output/fulton-2026-11-02
```

It uses the newest run under `screener/output/` (and prints which) unless
you give `--run`. The id must match that run's `ranked.csv` or
`rejected.csv` exactly, spaces included, so quote ids with spaces; an
unknown id is refused and close matches are shown, never picked. Decisions
are `pursue`, `pass`, `maybe` or `listed`, any case. A `pass` prints that
the parcel will drop out of future runs before it is written. Each call
appends one row (it never rewrites the file) and also copies the parcel's
features from the run's `features.json` into `decision_features.jsonl`
here, so they outlive the run folder: a same-day re-run, deleting
`output/`, or the parcel dropping out after a pass. The note stays in
`decisions.csv` only. Rejected parcels have no feature snapshot, so a
decision on one needs `--no-features` (the decision is written, without
features). It also copies the parcel's report facts from the run's
`facts.json` (building fit, pond, soils, frontage, lead grade, score:
what its row showed) into `decision_facts.jsonl` here, a separate file
that is never an evaluation input; a run without `facts.json` records
the decision anyway and says so. It is offline.

## Recording the marks made on the viewer page

The Site Plan Viewer page (`python -m screener.viewer`, see the main
README) has Pursue, Maybe, Pass and Listed buttons. **Save my marks**
downloads them as `decisions-<run>.csv`; this records them:

```bash
python -m screener.decide --import decisions-fulton-2026-11-17.csv
```

```bash
python -m screener.decide --import decisions-fulton-2026-11-17.csv --run screener/output/fulton-2026-11-17
```

- The file names the run its page was built from. That run's folder must
  be the one used (the newest under `screener/output/`, or `--run`), so
  the features kept with each decision are that run's.
- Every row is checked before anything is written: an unknown parcel id,
  a value that is not `pursue`, `pass`, `maybe` or `listed`, a date that
  is not `YYYY-MM-DD` or lies in the future, or a parcel with no features
  (without `--no-features`) and nothing is recorded.
- Each mark is then recorded exactly as one call above would: a row
  appended here, its features and its report facts copied. The date is
  the day the mark was made on the page.
- A mark is skipped, and the command says so, when the same mark (same
  decision, date and note) is already among the parcel's rows, or when
  the parcel's last row here is newer than the mark. So importing a file
  twice adds nothing, and an old file never undoes a later decision. To
  record such a mark anyway, give it as a single decision (above).
- The page saves every mark it holds each time, not only the new ones.

## Editing the file by hand

Spreadsheets are fine: a UTF-8 byte-order mark, Windows line endings, a
last line without a newline, and notes with commas, quotes or line breaks
are all read correctly (save as CSV). Keep the `parcel_id` and `decision`
column names (spaces around a column name, as in `parcel_id, decision`,
are ignored by both `decide.py` and the run). Copy parcel ids exactly as they appear in `ranked.csv`,
including spaces.

Everything in this folder except this README is kept out of git
(`screener/.gitignore`): an untracked file here would make the
AutoResearch evaluator refuse to score.

These marks are also the future second half of the AutoResearch score
(see `screener/autoresearch/PROGRAM.md`). That step (Phase B) is a human
`--new-baseline` change and has not been made: today no decision reaches
the evaluation.
