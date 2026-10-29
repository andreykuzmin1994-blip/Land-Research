# Risk & architecture review: track `screener-followups` (Agent 1)

Base `bf4d29b`. I wrote no code and changed no repo file. Probes ran in scratch only
(`probe/` next to this file), using `PYTHONDONTWRITEBYTECODE=1 -I`. They covered rank.py
sources built as strings, a copy of `owners.py`, and corpora that earlier reviewers had
already extracted. `git status --ignored screener/` was the same before and after.

Tier: `evaluate.py` is the screener's metric guard, so it gets Tier 2 (SR-15/SR-16 analog).
`checks.py`, `run.py` and `owners.py` feed live screens. `checks.map_checks` and
`owners.owner_type` also feed `build_eval` (a future key rebuild).

Baseline, re-measured read-only: `python -m screener.autoresearch.evaluate` gives
`pool 2318, positives 39, k 46, hits 2, score_range [6.5, 85.0], nonfinite 0`.

## Verified facts this review relies on (probes in scratch)

| # | Fact | Probe |
|---|---|---|
| V1 | **Code-object surgery reads any attribute by a runtime string and passes today's `rank_problems` on 3.11, 3.12 and 3.13.** The shape is `_t(None).gi_code.replace(co_names=(name,))`, then `type(_t)(code, {})`, then `next(fn(obj))`, with names built from `chr()`. It walked `__traceback__ → tb_frame → f_back → f_locals` and read a caller's local. It uses no `match`, no dunder `def`, no `metaclass=` and no dunder string constant, so **it survives every fix the requirement lists**. | `probe/bypass_code.py`, `probe/run_bypass.py` |
| V2 | **UTF-7 coding-cookie smuggling defeats the whole static check (3.11 and 3.13).** `rank_import_problems` parses `RANK_FILE.read_text()`, which sees `# note +AAo-HIDDEN = ...` as a comment. The importer honours `# coding: utf-7`, decodes `+AAo-` to a newline, and runs the hidden line. Parsing `importlib.util.decode_source(bytes)` instead catches it. | `probe/u7/rank_u7.py`, `probe/run_u7.py` |
| V3 | These pass `rank_problems` today: a dunder `def __instancecheck__`; a metaclass `@property def __match_args__` plus `class P(metaclass=M)` (no dunder strings needed); `typing.get_origin(typing.Type[int])`, which returns `type`; `typing.ABCMeta` and `typing.NamedTupleMeta`, which are metaclass constructors; `typing.get_args(List['x'])`, which returns a ForwardRef; `import math; math.isfinite = ...`, which rebinds the evaluator's own NaN guard because both share the `math` module; and `help()` / `print()`. | `probe/run_misc.py` |
| V4 | In the HOA rule, removing `THE` from `_HOA_COMPANY_RE` changes **0** owner types in the 114-name list, the 5,544 cached owners and the 791,537 deed grantors. In the grantor corpus, `CAMP CREEK VILLAGE TOWNHOMES INC HOA` is an association **only** because of a legal form placed *before* HOA. | `probe/hoa_probe.py`, `probe/hoa_list.py` (individual names printed as placeholders) |
| V5 | `SLOPE_PIXEL` is 10 × 10 in EPSG:3857, which is about 8.3 m on the ground at 33.7°N. One pixel covers about **69 m²**. `gis_acres == 0.0` only means the polygon is under about 20 m². Slope is requested for `p["_open_ground"]`, not for the parcel polygon. | code read: `checks.py:40,98,237` |

---

## Track (a): rank.py purity check (`evaluate.py`)

### HIGH

**A1. The requirement's string and spelling fixes alone do not close the getattr-by-string class (V1, V3).**
Any check on string *contents* is defeated by computed strings (`"_"*2+...`, `chr()`, `"".join`).
The bypass that survives is code-object surgery (V1).
*Mitigation:* refuse the **consumers and gateways**, not just the spellings:
1. **Refuse `ast.Match` entirely.** One message: "use if/elif". rank.py has no legitimate need for it. This closes positional and keyword class patterns, and any future pattern kind. It is simpler and more robust than refusing only `MatchClass`.
2. **Refuse attribute names matching `^(gi|cr|ag|tb|f|co)_`.** These are the frame, generator, coroutine, traceback and code-object families: `gi_code`, `cr_code`, `ag_code`, `f_code`, `tb_next`, `co_names` and the rest. They already include the banned `gi_frame`/`f_back`. Also refuse keyword-argument names starting with `co_` (`replace(co_names=...)`). Apply the rule to **attributes only**, not to Names: Names cannot reach attributes, and `f_access` is a legitimate variable name. **Never ban `replace`**: `str.replace` is legitimate.
3. **Refuse dunder-named `FunctionDef` / `AsyncFunctionDef` / `ClassDef`.** This covers class hooks and PEP 562 module `__getattr__`. Also refuse any `ClassDef.keywords` (`metaclass=`).
4. **Refuse the Name `type`** as defence in depth (FunctionType and metaclass construction). Steer the agent to `isinstance`. It is reachable anyway through `typing.get_origin`, which A5 covers.
5. **String constants** (defence in depth only; docstrings stay exempt): refuse a string that **fullmatches** `__\w+__`, or that **equals** a banned builtin (open/eval/exec/compile/getattr/setattr/delattr/globals/locals/vars/breakpoint) or a frame/code family name. **Exact match, never substring**: "mostly open ground" must pass. Do not put generic words such as "type" or "format" on the string list.

Then re-audit. With 1–4 plus A2 and A5 in place, I found no remaining getattr-by-string route on 3.11–3.13:
- `hasattr` returns a bool only.
- `dir` returns names only.
- `%`-formatting looks up mapping keys, not attributes.
- `type()` / metaclass objects with computed dunder keys still build, but nothing consumes them.
- `ForwardRef._evaluate` is private.

Record this in the README and decision note as "known routes closed", **not** "impossible".

**A2. The check must parse the bytes the importer will run (V2).**
*Mitigation:* `rank_import_problems` reads `RANK_FILE.read_bytes()` and refuses any coding declaration other than utf-8 or utf-8-sig (`tokenize.detect_encoding`, which may raise SyntaxError: treat that as a problem). It then calls `rank_problems(importlib.util.decode_source(data))`. Both are stdlib (SR-10). This is a different class from getattr-by-string, but it is the cheapest way through everything. Without it the README cannot honestly say "the file that runs is the file checked".

**A3. Overclaiming.** Note 07 says the frame-walk bypass was "material" and "Fix[ed]". The README says the check covers "frame attributes ... and the like".
*Mitigation:*
- Add a **dated correction** to 07 § "Risks found in review" 1: the positional `match` route, code-object surgery and UTF-7 survived 9269736 and are closed in this track. Do not rewrite history.
- The README must say exactly what is refused, and that the file is decoded as the importer decodes it.
- It must say this is **a drift guard, not a sandbox**: a rank.py written to cheat can still find a route (e.g. reading `data/` from disk), so every `exp:` diff needs a human read (SR-16 analog).
- It must list the residuals: computed-name `type()` / metaclass objects that nothing consumes, stale `.pyc` (A8), the live `screener.run` not running the check, and Python ≥3.14 (A5).

### MEDIUM

**A4. False positives would stall the loop.** A refusal of a legitimate edit makes the agent thrash or delete the guard's intent.
*Mitigation:* table-driven no-FP tests:
- today's rank.py;
- rank.py plus `math.exp`, `log1p`, `tanh`, `sqrt` and `isfinite`;
- `from typing import Any, Mapping, Optional`;
- `.get`, `.startswith`, `.upper` and `.replace`;
- f-strings with format specs;
- generator expressions, lambdas, `sorted(key=...)`, `isinstance`, dicts of weights, `try/except (TypeError, ValueError)`;
- the strings "open ground" and "type of owner";
- the six PROGRAM.md example experiments, written as small edits.

Refusal messages should name the alternative ("use if/elif", "use isinstance").

**A5. typing exposes metaclasses, `type`, ForwardRef and I/O (V3).** On Python ≥3.14, `ForwardRef.evaluate()` and `typing.evaluate_forward_ref` are public eval primitives.
*Mitigation (recommended, cheap):*
- Replace the "not a module" rule for typing with an **allowlist of annotation names**: Any, Optional, Union, Mapping, Sequence, Iterable, Callable, Dict, List, Tuple, Final, Literal, TYPE_CHECKING.
- Apply it to both `from typing import X` and `typing.X`.
- `__future__` and `math` keep the current rule.

If Agent 2 declines, document the 3.14 risk and ban `get_origin`, `get_args`, `ABCMeta`, `NamedTupleMeta`, `evaluate` and `evaluate_forward_ref` by name.

**A6. Child-process scoring: decision is NO for this track.** Every in-process route found so far (frames, `__globals__`, code objects) also reaches `builtins.open`. Once `open` is reachable, `data/` on disk yields the whole key: the features file maps feature values to keys, and the labels file lists the positive keys. So a child process that receives HIDDEN-stripped features adds no protection against any primitive the static check misses.

It would also add Tier-2 plumbing risk to the metric path:
- JSON round-trip of NaN and inf;
- order and tiebreak parity;
- `-I` and `sys.path`;
- platform spawn differences;
- test rework.

Real isolation needs filesystem confinement, which is not stdlib-portable (SR-10). Earmark it the same way SR-16 does: if a bypass ever shows up in an `exp:` diff, escalate to the human. Do not add a child process here.

**A7. Shared-module mutation (V3):** `math.isfinite = lambda v: True` disables `ranking()`'s nonfinite count. Garbage NaN orders would then pass `--decide`.
*Mitigation:* refuse `ast.Attribute` in Store or Del context whose value is a Name bound by an import in rank.py (track aliases). Low FP.

### LOW

- **A8.** A stale or crafted `screener/__pycache__/rank.*.pyc` is gitignored, so `--decide`'s status check misses it. There is also a check-then-import TOCTOU. Optional fix: compile and exec the exact checked source into a fresh `screener.rank` module (`compile` honours `__future__`; register it in `sys.modules`). Otherwise keep both as documented residuals (note 07 already lists the .pyc).
- **A9.** `print`, `help`, `license`, `credits`, `copyright`, `exit` and `quit` are I/O or interactive. `help()` can start a pager or block on stdin. Optionally add them to `RANK_BANNED_NAMES`, which costs nothing for a scorer.
- **A10.** Changing `evaluate.py` changes `EVAL_FILES`, so `--decide` refuses until a human runs `--new-baseline`. A key rebuild and new baseline are already pending (notes 07 and 08). No `experiments.tsv` exists here. State this in the decision note.

---

## Track (b): sub-pixel slope polygons (`checks.py`, `run.py`)

### MEDIUM

**B1. Wrong classifier in the requirement.**
- `gis_acres == 0` (under about 20 m²) misses polygons of about 20–100 m², which 3DEP also answers with nothing.
- Slope is asked for `_open_ground`, not the parcel.

*Mitigation:*
- Before any request in `slope_checks.work()`, skip when the **projected area of the geometry actually sent** is under a named constant tied to `SLOPE_PIXEL`: at least one pixel (about 70–100 m²). A few pixels is also defensible, because any surviving parcel with that little open ground must have a map/tax-record mismatch.
- A skipped parcel makes **0 requests** and gets **no `slope_error`**. Set a distinct key (e.g. `slope_skipped`) and keep `relief_ft`, `steep_pct` and `mean_slope_pct` as None, as today.
- **Do not** retry at a 1 m pixel. On a 9 m² stub that number means nothing, and it would add new cache keys and load (SR-11).

**B2. Crash class (06 R7).** An exception inside a worker propagates through `pool.map` and kills the run after the slope stage.
*Mitigation:* zero-area or degenerate polygons, `make_valid` GeometryCollections, line-only remnants and empty geometry must all classify as too small. Check `geom.is_empty` and the degree-area guard **before** `LocalProjection.around`. Nothing may raise.

**B3. Notes and flags.**
- "SLOPE CHECK FAILED … Re-run later" counts **only** `slope_error`.
- Add a separate note with counts only (SR-8), for example "N parcels: parcel-map shape too small to check (under one 10 m lidar pixel) … check the county map". It must not say "Re-run later".
- Key the note on geometry, so it also appears under `--no-slope`.
- `screens.flags()` lives in `screens.py`, which is in EVAL_FILES and not an allowed file, so **do not touch it**. Append a run-level flag in `run.py` after `screens.flags(p)` instead.

**B4. Truthiness fix at `checks.py:99`.** Make `gis_acres == 0.0` raise `acreage_mismatch`. Guard against a falsy `p["acres"]` (division).
`map_checks` is called by `build_eval`. The diff may only **add** keys or flags. It must not change any FEATURES field (`usable_acres`, `constrained_pct`, `*_pct`, `exit_miles`, `rail_ft`, `industrial_within_1mi`). Pin this with a test.

**B5. Decision: keep such a parcel ranked, flagged, with its 0% constraints, rather than reject it.** Reasons:
- A hard reject outside `screens.py` would be a hidden screen that the frozen eval meta screens don't know about.
- `screens.py` needs a human and a new baseline.
- A county GIS defect should not hide land (notes 07 and 08 take the same conservative stance).
- Only one Fulton parcel reaches this step.

Make it impossible to read as clean: the `acreage_mismatch` flag, the run-level flag and the summary note. Document this in the README. Follow-up for the human: a `screens.py` rule with a new baseline, if wanted.

### LOW

- **B6.** Thin shapes and multipolygons of slivers above the threshold can still come back empty. They will read as "SLOPE CHECK FAILED … Re-run later". This is a documented residual. Optionally, reclassify an empty answer for shapes under a few pixels after the request (still 2 requests at most).
- **B7.** Requests for normal parcels must not change, so params and cache keys stay pinned (06 gate). Note 06's "zero-pixel answers count as failed" is refined here. 06 is not an allowed file, so record that in this track's note.

---

## Track (c): `THE` in the HOA rule (`owners.py`)

- **C1 (LOW). The requirement's alternative is unsafe.** "Require a legal form right after HOA" would lose `CAMP CREEK VILLAGE TOWNHOMES INC HOA` (V4). Safer: **drop `THE` only** and keep the legal-form test position-agnostic (INC, LLC, L L C, CORP, CORPORATION). Update the comment at `owners.py:86-89`: Thế is a Vietnamese middle name.
- **C2. Accepted residual, unchanged:** a person-named company such as `<surname> HOA LLC` / `NGUYEN THE HOA INC` still reads as an association (07 residual 4).
- **C3. Corpus re-run** with the **real patched file**:
  - Copy the `verify/assoc-review/` scripts into the track's scratch. Do not overwrite the previous reviewer's evidence. `cmp.py` hardcodes its path.
  - Use `owners_base` from `bf4d29b`.
  - Run `t114.py`. Expect 109 association / 4 entity / 1 religious, unchanged.
  - Run `tcsv.py` on `screener-full/base` and `verify/screener-after`.
  - Run `tcorp.py` on `corpus/cache_owners.txt` and `corpus/grantors.txt`.
  - Run `tpairs.py`.
  - Expect **0 transitions** everywhere; list any that appear.
  - Repo docs carry **counts only** (SR-8). The grantor corpus holds individuals.

## Track (d): docs (`README.md`, optional `report.py`)

- **D1 (LOW). Drift paragraph.** State the observation with its date (2026-10-28→29):
  - 3DEP gave different slope statistics, with identical elevation statistics, for 464 of 2,110 identical requests (about 22%).
  - Fulton appraisal values changed overnight, moving about 19 parcels between built-on and ranked.
  - Name the thresholds that flip: `max_steep_pct` (share of open ground steeper than 15%), `min_usable_acres` and `max_improvement_value`.
  - Say that before/after diffs mix drift with code effects.
  - Recommend comparing on the same cache (no `--refresh`, within `cache_days`) **only after** checking which sources are cached (arcgis cache: 7 d; CAMA: 30 d).
  - No causal claims about the servers.
- **D2 (LOW, optional).** Add an "other reasons" line holding the sum of `reject_reasons[12:]`. Show it only when there are more than 12 reasons. Test both cases. `reason_key` output carries no names (SR-8).

---

## Where the requirement as written is wrong or unsafe

1. (a) The listed fixes (dunder strings, dunder defs, `metaclass=`, banning the match class pattern) do not close the class. Code-object surgery survives them (V1). Ban the gateway families and `match` as a whole.
2. (a) String checks must be exact-match defence in depth. Substring matching would refuse "open ground", and computed strings defeat them anyway.
3. (a) The UTF-7 / coding-cookie mismatch (V2) defeats the whole check. Decode the file the way the importer does.
4. (a) Child-process scoring buys nothing while disk is reachable (A6). Don't add it.
5. (b) `gis_acres == 0` is too narrow and measures the wrong geometry. Classify on the projected area of the geometry sent. "Its own flag" has to come from `run.py`, because `screens.py` is EVAL_FILES and not allowed.
6. (c) "Require a legal form" must not mean "right after HOA" (V4).

## Required tests beyond the happy path (append to `test_rules.py` / `test_integrity.py`; SR-6)

- **(a) Refusal tests.** Build every exploit source as a string. Refuse each of these:
  - the published exploit, verbatim;
  - the metaclass-property variant with no dunder strings;
  - V1 with `chr()` names;
  - a value-only `match` (policy);
  - `def __getattr__(name)` at module level;
  - `class P(metaclass=M)`;
  - each of `x.gi_code`, `x.cr_code`, `x.ag_code`, `x.f_code`, `x.tb_next` and `x.co_consts`;
  - `c.replace(co_names=())`;
  - `T = type(f)`;
  - non-allowlisted typing names;
  - `math.isfinite = abs` (if A7 is done);
  - `{"__match_args__": 1}` and `"f_back"`.
- **(a) UTF-7 tests.** Put a UTF-7 fixture in a TemporaryDirectory, with `RANK_FILE` and `_rank_origin` patched. `rank_import_problems()` must be non-empty, and `main()` must return 3 before the import: the marker file must not be created (reuse the `test_refused_before_import` pattern). Never call `main()` unpatched.
- **(a) No-FP table** as in A4.
- **(b) Tiny polygon.** Cover about 9 m² and about 50 m². Expect:
  - 0 `SLOPE_URL` calls;
  - no `slope_error`;
  - the distinct key and flag;
  - `acreage_mismatch` when `gis_acres` is 0.0;
  - the geometry note, without "Re-run later".
- **(b) Edge geometry.** Zero-area, degenerate, empty open ground and a GeometryCollection: no crash and no request.
- **(b) Mixed run.** One tiny parcel, one EMPTY_SLOPE parcel and one good parcel give both notes with correct N of M.
- **(b) Unchanged behaviour.** A normal parcel's slope params and cache key are unchanged. `map_checks` FEATURES fields are identical before and after. Ratios in 0.5–2.0 raise no mismatch. `--no-slope` still shows the geometry note.
- **(c) Individuals.** `NGUYEN THE HOA`, `TRAN THE HOA & NGUYEN THI LAN`, `NGUYEN HOA THE` and `THE HOA NGUYEN` are typed individual, and `attribute_rejects` returns `[]` for each.
- **(c) Associations.** `HIGH GROVE HOA INC`, `COTTAGES AT CASCADE HOA LLC THE`, `ESTATES AT CEDAR GROVE HOA INC THE` and `CAMP CREEK VILLAGE TOWNHOMES INC HOA` stay association.
- **(d)** The "other reasons" line, if implemented.

## Go / no-go gates for Agent 3

1. **Files.** `git diff --name-only bf4d29b` ⊆ {`screener/autoresearch/evaluate.py`, `owners.py`, `checks.py`, `run.py`, `report.py`, `screener/README.md`, `test_rules.py`, `test_integrity.py`, dated corrections to note 07 (and 08 only if stale), plus Agent 3's own new note}.
   - Untouched: `rank.py`, `screens.py`, `scoring.py`, `settings.toml`, `build_eval.py`, `data/`, `PROGRAM.md`, `arcgis.py`, `fulton.py`, `layers.py`, `geo.py` and the root invariants.
   - `requirements.txt` unchanged (SR-10). No model names.
2. **Evaluator scope.** The `evaluate.py` diff is confined to `rank_problems`, `rank_import_problems`, the `RANK_*` constants and docstrings. No changes to `ranking`, `hits_at_k`, `decide`, `paired_win_share`, `LOG_COLUMNS`, `EVAL_FILES`, `HIDDEN` or `K_SHARE`.
3. **Tests.** Root 847 OK. Screener 96 plus new OK. `python -m screener.tests.test_screener` runs 23. Both suites run with `HTTPS_PROXY` pointed at a dead port. `git status --porcelain --ignored screener/` is identical before and after. Changed files pass `python3.11 -m py_compile`.
4. **Plain evaluate.** `pool 2318, positives 39, k 46, hits 2, score_range [6.5, 85.0], nonfinite 0`. No `experiments.tsv` or `best_ranking.json` exists. `--decide`, `--test`, `--new-baseline` and `build_eval` were not run.
5. **(a) Refusals and FPs.** Every refusal test above fails on `bf4d29b` and passes after the change. V1 and V2 are refused. The no-FP table passes, including today's rank.py and the PROGRAM.md examples.
6. **(a) Docs.**
   - The README states what is refused, decode-as-importer, "drift guard, not a sandbox", the human review of `exp:` diffs, and the residuals.
   - Note 07 has a dated correction.
   - The decision note records the child-process NO and its reasoning.
7. **(b)** All gate-level (b) tests pass. A tiny parcel sends 0 live requests. Normal-parcel request params and cache keys are unchanged. FEATURES fields from `map_checks` are unchanged. The keep-and-flag decision is documented.
8. **(c)** The new owner tests and all existing ones pass. The corpus re-run shows 0 transitions (counts only in the repo), or each transition is listed and justified.
9. **(d)** The drift paragraph is dated and accurate. It names the thresholds and makes no causal overclaim.
10. **Data handling.** SR-8: no individual owner names in notes, flags, the README or the decision note. SR-11: no live requests from tests; any live spot-check is ≤2 requests at ≤1 req/s. No stray files in the repo; scratch only.

## Out of scope (record, don't fix here)

- A common-area / `# CA n` signal (note 08). `PEACHTREE HILLS PLACE MASTER CONDOMINIUM`.
- Rebuilding the frozen key and `--new-baseline` (human).
- Making the live `screener.run` enforce the purity check.
- Sandboxing rank.py (filesystem confinement).
- A `screens.py` rule that rejects unusable GIS geometry (human, new baseline).
- PROGRAM.md wording on refused constructs (human-only, note 07 residual 2).
- Python 3.14 typing changes beyond A5.
