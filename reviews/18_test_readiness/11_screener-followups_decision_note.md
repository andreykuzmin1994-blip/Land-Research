# Decision Note — Screener follow-ups (purity-check routes, sub-pixel slope parcels, THE in the HOA rule, drift docs)

> Screener-only change (`screener/`, governed by `screener/README.md`), following up four
> verified findings from the end-to-end review of `bf4d29b`. Between runs; no AutoResearch run
> active. Base `bf4d29b`. Reviewed like Tier 2, because `evaluate.py` is the screener's metric
> guard (SR-15/SR-16 analog). Three roles, each in an independent fresh context on the
> session's strongest available model: risk review (`11_screener-followups_risk_review.md`),
> implementation, then adversarial review + commit (this note). **Verdict: approved, with one
> reviewer fixup: review found a fourth route through the purity check, now closed (§ Risks).**

## What changed

**(a) `rank.py` purity check (`evaluate.py`: `rank_problems`, `rank_import_problems`, `RANK_*`).**
- The check reads the bytes the importer compiles. Any coding declaration other than UTF-8 is
  refused (`# coding: utf-7` could hide a line of code in a comment).
- It refuses whole gateways, not spellings:
  - `match` statements;
  - attributes of frames, generators, coroutines, tracebacks and code objects
    (`f_`, `gi_`, `cr_`, `ag_`, `tb_`, `co_`), and `co_...` or dunder keyword arguments;
  - dunder function and class names, and class keywords (`metaclass=`);
  - dunder names bound by `import ... as` / `except ... as` (reviewer fixup);
  - the names `type`, `print`, `help`, `exit`, `quit`, `license`, `credits` and `copyright`;
  - `typing` names outside an annotation allowlist;
  - assigning to or deleting an attribute of an imported name, and using a module as a value.
- As defence in depth, it refuses strings that are exactly a dunder, a frame/code attribute or a
  refused builtin. These are exact matches only, so "open ground" still passes.
- `ranking`, `hits_at_k`, `decide`, `paired_win_share`, `LOG_COLUMNS`, `EVAL_FILES`, `HIDDEN`
  and `K_SHARE` are unchanged.

**(b) Sub-pixel slope parcels (`checks.py`, `run.py`).**
- `slope_area_m2` measures the open ground actually sent to 3DEP. It never raises on empty,
  line-only or zero-area shapes. Below `SLOPE_MIN_M2` (100 m², at most one 10 m Web Mercator
  pixel), `slope_checks` sends **no request**, sets no `slope_error`, and sets `slope_skipped`.
- The `map_checks` truthiness bug is fixed: 0.0 GIS acres now raises `acreage_mismatch`.
- `run.py` adds a counts-only note keyed on geometry. It also appears with `--no-slope` and
  never says "Re-run later". `run.py` also appends a run-level flag after `screens.flags(p)`;
  `screens.py` is untouched.
- "SLOPE CHECK FAILED ... Re-run later" still counts only `slope_error`.

**(c) `owners.py`.** `THE` is no longer an HOA company word; the legal-form test is still
position-agnostic. Vietnamese names with the middle name Thế and the given name Hoa now type as
individuals.

**(d) Docs.**
- `screener/README.md` gains:
  - a precise "The purity check" section;
  - the keep-and-flag rule for sub-pixel parcels;
  - a dated paragraph on public-data drift.
- `report.py` adds an "other reasons" line when there are more than 12 reject reasons.
- Note 07 gets a dated correction to § "Risks found in review" 1.

## Verified (reviewer, independently)

- **Tests (SR-6).** Run with `PYTHONDONTWRITEBYTECODE=1` and HTTP(S) proxies pointed at a dead
  port:
  - root suite: 847 OK;
  - screener suite: 134 OK (96 + 34 from implementation + 4 from the reviewer), on 3.13 and on
    3.11.17;
  - `python -m screener.tests.test_screener`: 23 OK.

  `git status --porcelain --ignored` showed only the tracked changes before and after.
- **The tests fail on the base.** I extracted `bf4d29b` to scratch, laid the new test files over
  it and ran them:
  - 75 failures and 2 errors on the implementation's tests;
  - 5 failures on the reviewer's tests.

  The guard tests pass on the base, as they should: pinned FEATURES fields, slope params and
  cache key, ratios 0.5–2.0, the HOA companies, and 12 or fewer reasons.
- **Plain `python -m screener.autoresearch.evaluate`.** pool 2318, positives 39, k 46, hits 2,
  score_range [6.5, 85.0], nonfinite 0; unchanged after the fixup. No `experiments.tsv` or
  `best_ranking.json` exists. `--decide`, `--test`, `--new-baseline` and `build_eval` were not
  run.
- **Published exploits.** The match exploit (`rank_exploit.py`) and the `open` probe
  (`io_bypass_src.py`) from the e2e review are refused. Today's `rank.py` passes. The six
  PROGRAM.md example experiments and the realistic-edit table pass.
- **Tiny parcel.** The captured geometry of the real parcel measures 9.25 m², so it is skipped
  with 0 requests. A normal parcel's params and cache key are pinned unchanged.
- **Owner corpus (SR-8: counts only).** The scripts in the track's scratch use the real patched
  `owners.py` and the `bf4d29b` one (both checked with `cmp`). Every check shows 0 transitions:
  - 114-name list (re-run): 109 association, 4 entity, 1 religious, before and after;
  - 5,544 cached owners and 791,537 deed grantors (re-run);
  - base and after CSVs, 3,713 owners (implementer's output);
  - 18,265 `same_owner` pairs (implementer's output).
- **Drift numbers.** 464 of 2,110; 12 + 7 parcels. Both match the e2e report.
- **Invariants.**
  - Only allowed files changed.
  - Untouched: `rank.py`, `screens.py`, `scoring.py`, `settings.toml`, `build_eval.py`,
    `data/` and PROGRAM.md.
  - SR-10: stdlib only (`codecs`, `io`, `re`, `tokenize`); `requirements.txt` unchanged.
  - SR-12: no secret-shaped literals and no model names.
  - SR-8: notes and flags carry counts only.
  - SR-11: no live requests.

## Risks found in review, and what changed (reviewer fixups)

1. **A fourth route through the purity check (material).**
   - **The route.** `import typing as __builtins__` passed the patched check. Every function
     defined after it then resolves bare names it doesn't define in `typing`'s namespace, so
     `sys` and `operator` become plain names.
   - **Reproduced** on 3.11, 3.12 and 3.13 in a scratch harness. `operator.attrgetter`, with a
     name built by `chr()`, read `sys._getframe(1).f_locals`, which gave a caller's local, and
     `builtins.open`. `rank_problems` returned `[]`. It also passes at `bf4d29b`.
   - **Fix.** Refuse dunder names bound by `import ... as` or `except ... as`; every other way
     to bind a name was already refused. The fix adds `RankBuiltinsSwapTests` (4 tests: the
     exploit, a proof that the swap is real, the alias and handler forms, and plain aliases
     still passing) and updates the docstring, the README and the note 07 correction.
   - **Re-audit afterwards** (3.11–3.13): no further getattr-by-string route found.
     - With `__builtins__` fixed, bare names reach only real builtins and rank.py's own globals.
     - `hasattr` and `dir` return no values.
     - `%`-formatting reads mapping keys.
     - Implicit dunder lookups need definitions, which are refused.
     - Allowlisted `typing` aliases forward only static attribute names to `collections.abc`.
     - `ForwardRef._evaluate` is private before 3.14.

   Recorded as "known routes closed", not "impossible".

## Decisions

- **No child-process scoring (A6).** Every in-process route found so far also reaches `open`,
  and `data/` on disk gives the whole key. A child process that gets HIDDEN-stripped features
  would therefore add no protection. It would add Tier-2 plumbing risk: NaN/inf round-trip,
  order and tiebreak parity, `sys.path`, spawn differences. Real isolation needs filesystem
  confinement, which the stdlib can't give portably (SR-10). Earmarked SR-16-style: if a bypass
  ever shows up in an `exp:` diff, escalate to the human.
- **Sub-pixel parcels stay ranked and flagged, not rejected.** A reject outside `screens.py`
  would be a hidden screen that the frozen evaluation doesn't know about. Changing `screens.py`
  needs a human and a new baseline.
- **Note 06 refined (06 not edited).** "Zero-pixel answers count as failed slope checks" now
  applies only to open ground of 100 m² or more. Smaller open ground is not asked about and is
  not a failure. An empty answer for a larger, thin shape still counts as failed (at most 2
  requests).
- **`EVAL_FILES`.** `evaluate.py` changed, so `--decide` refuses until a human runs
  `--decide --new-baseline`. That is already pending from notes 07 and 08.

## Deviations

- The implementation went beyond A7 and also refuses an imported module used as a value
  (`m = math`), which closes aliasing around the read-only rule.
- `__init__` is now refused, because it is a dunder definition. The message tells the agent to
  use plain functions.
- A8 (exec the checked source) is not done; it would change `evaluate()`'s import and break the
  G2 scope. A planted `.pyc` and the check-then-import race remain README residuals.
- `STANDING_RISKS.md` asks the note to name the model behind each role. This session forbids
  model names, so the roles are described as above.

## Residual gaps / human follow-ups

1. **Rebuild the frozen key and record a new baseline** (pending since 07). Until then
   `--decide` refuses.
2. **PROGRAM.md (human-only).** List the refused constructs. The refusal messages name the
   alternatives (if/elif, isinstance, plain functions), but the loop agent first meets them as
   REFUSED.
3. **Purity-check residuals (README § "The purity check").**
   - a planted `.pyc`;
   - the check-then-import race;
   - mutating shared objects through methods (`Mapping.register`);
   - the live `screener.run` not running the check;
   - Python 3.14 evaluation hooks, which haven't been reviewed.
4. **Geometry edges.**
   - A `make_valid` GeometryCollection whose polygons sit inside a nested MultiPolygon measures
     0 m², so it is flagged as sub-pixel. Both `slope_area_m2` and `_esri_polygon` read only
     top-level Polygon parts, so no rings would have been sent anyway (at base the request
     carried an empty ring list). The label overstates the problem; the parcel is still flagged
     rather than read as clean. This is rare.
   - Thin slivers over 100 m² can still get an empty 3DEP answer and read as "failed".
5. **A `screens.py` hard screen for unusable GIS geometry**, if wanted (human decision, new
   baseline).
6. **Owner rules.** `<surname> HOA LLC` still types as an association (07 residual 4).
