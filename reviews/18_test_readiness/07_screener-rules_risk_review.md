# Risk & architecture review — track `screener-rules` (Agent 1)

Base 29c051e. Reviewer wrote no code and changed no repo file. All probes ran offline against
`screener/cache/` (public tax-roll/CAMA/FLU data already on disk) from scripts in this scratch dir
(`probe/`). No names of individual owners appear below (SR-8).

Baseline re-measured (read-only): `evaluate` → dev hits 2/39, k 46, score_range [6.5, 85.0];
`--control` → mean 0.86, p5–p95 [0, 3].

Tier: evaluate.py is the screener's metric/decision logic → treat as Tier 2 (STANDING_RISKS § Change tiers).
Standing risks: SR-2/5/9/11/12 not applicable (no SQL, DB, network, secrets in this track). SR-6, SR-8,
SR-10, SR-13 (by analogy: experiments.tsv), SR-15/16 (by analogy: evaluate.py = screener metric) apply — see items.

---

## CRITICAL

**R1. `--new-baseline` does not reset the bar → the loop stays blocked even after the human re-records it (verified by simulation).**
`fingerprint()` hashes only `data/`. This track edits `evaluate.py` + `screens.py` (EVAL_FILES) and leaves
the fingerprint unchanged. `_best_kept(fp)` = `max(rows, key=hits)` over *all* ancestor baseline/keep rows, and
`max` returns the first of equal rows. So with an older baseline A (2 hits) and the human's new baseline B
(2 hits, same fp), the next rank.py-only experiment gets `best = A`, the `since` diff A..HEAD contains
evaluate.py/screens.py, and it is REFUSED: "A human must record a new baseline". Repeating `--new-baseline`
never helps. The only ways out are editing the append-only TSV or changing `data/`. The CONSTRAINT's "refuses until a
HUMAN runs --new-baseline" is false whenever a baseline row already exists (none in this container; the human's
machine is unknown).
*Mitigation:* a baseline row starts a new chain. `best` = max hits over {the latest baseline row for fp whose
commit is an ancestor} ∪ {keep rows appended after it that are ancestors}. Do not change `LOG_COLUMNS`. A new
column would misalign existing TSV headers, because `DictWriter` writes the header only for a new file.
*Test:* hermetic run of baseline A → EVAL_FILES change → `--new-baseline` B → rank-only commit C. C must be scored,
not refused. Run it with A.hits == B.hits and with A.hits > B.hits.

## HIGH

**R2. New guards inside `decide()` run after rank.py has already been imported and run.** `main()` calls
`evaluate()`, which runs `importlib.import_module("screener.rank")`, and only then calls `decide()`. By that point
module-level code in rank.py could already have rebound `evaluate._refuse`/`WIN_SHARE_TO_KEEP`, written
BEST_FILE or read `data/`, and an AST check would come too late.
*Mitigation:* run the static checks (AST purity and untracked files) in `main()` **before** anything imports rank.
Find the file with `importlib.util.find_spec("screener.rank").origin`, which does not run it, so the check reads
the same file that gets scored. Refuse in `--decide`. Also refuse, or at least don't score, in plain mode,
because an impure rank.py makes the plain score meaningless.

**R3. Simple AST allowlists can be bypassed.** Verified: `from typing import sys` returns the `sys` module, and
`import typing; typing.sys` does the same. Other routes: dunder walks (`().__class__.__base__.__subclasses__()`),
`getattr`, `globals()`/`vars()`, and `__builtins__`.
*Mitigation (simple, documented):* allow only the modules `__future__`, `typing` and `math`. `math` is required
because PROGRAM.md rule 3 asks for smooth curves. Refuse any imported name or attribute of an allowed module
that resolves to a module object (evaluate is trusted and can resolve it). Refuse any dunder Name or Attribute.
Refuse the names `open exec eval compile __import__ globals locals vars getattr setattr delattr breakpoint
input`. Refuse `global`/`nonlocal`. Only scan string constants for narrow tokens (`fulton_2016`, `labels`,
`experiments.tsv`, `best_ranking`, `.json`, `.gz`), and skip docstrings: the current rank.py docstring
contains "screener/autoresearch/ PROGRAM.md", so a scan for `autoresearch/` or `data` would refuse today's
file. Document it as a guard against accidental drift, not a sandbox (same stance as SR-16).
The **current rank.py must pass**; it imports only `__future__` and `typing.Any`.

**R4. A NaN guard on `score_range` endpoints misses NaN (verified).** A NaN in the middle of the scores
scrambles `sort()` and leaves both endpoints finite, so `if not isfinite(lo) or not isfinite(hi)` passes while
hits@K is garbage. *Mitigation:* check `math.isfinite` on **every** score in `ranking()`, return the count in the
result, refuse in `decide()` if it is above 0, and show it in plain-mode output. ±inf must be refused too.

**R5. Tightening same_owner moves years_held and the labels a lot, in both directions.** Probe on cached
5–50 ac Fulton parcels (6,398 owner/parcel pairs, 24,381 deeds), using a naive strict rule: `acquisition()`
changed for 480 pairs (131 by ≥10 years, mostly toward fewer years held) and `sold_after(2016)` flipped
True→False for 205 pairs. Sampled diffs include real fixes: different funds/LLCs that share a project name,
12-char prefix collisions, and a developer deed to an HOA. They also include regressions: co-owner tails
("… & SPOUSE"), ET AL, A/K/A, EXECUTOR/EXTR, INCORPORATED, one-letter misspellings, and "LIVING TRUST THE".
**All three current sub-rules match the spec example on their own:** 12-char prefix, ≥2 shared non-generic
words, and SequenceMatcher ≥0.85 on the first 20 chars. Fixing one is not enough. If "entity suffixes" is read as
`_ENTITY_RE`, which includes PARTNERS, both names reduce to "SOUTHFULTONLAND" and the example is still True.
*Mitigation:* write the rule down. Strip only legal-form suffixes (LLC/L L C/INC/INCORPORATED/CORP/CO/
COMPANY/LP/L P/LLP/LTD/THE), trust/estate role words and co-owner tails ("&"/AND …, ET AL, A/K/A). Then match on:
(i) equal core; (ii) prefix match only when the deed-side name is at the 30-char cap (52% of deed names are
29–30 chars long); (iii) same tokens in a different order; (iv) extra tokens that are only initials; (v) typo only
when lengths differ by ≤1 and the full-string ratio is ≥0.9. Put this change in its own commit so it can be
reverted alone. Write an offline before/after count on the cached deeds in scratch, with categories and a
hand-checked sample, and put counts only (no names) in the decision note.

**R6. The frozen key diverges from live, and it matters.** Evaluate does not call `attribute_rejects`,
`owners`, `zoning` or `scoring`, and `owner_type`/`years_held`/labels are frozen, so the dev score is
unchanged. The divergence is still material:
(a) about 11–13% of cached parcels that pass the attribute screens are owned by associations (411 with the narrow
pattern, 506 with a broad one, of 3,805). The frozen pool still holds them as certain negatives typed
'entity'/'individual'. That biases the seller signals the loop learns against entity owners, while the README
says positives were "mostly sold by companies".
(b) years_held and sold_after in the key use the old same_owner.
(c) VA-type owners are 'estate' with +25/+10.
*Recommendation:* after this change merges and R5 is validated, a **human** rebuilds the key (`build_eval`) and
records `--new-baseline`. Expect a new fingerprint and K≈2% of a ~10% smaller pool. Never do this in this track.

## MEDIUM

**R7. HOA matching as specified is unsafe at the edges.** Standalone **POA** often means *power of attorney*
on owner lines, and the cache holds a company named "POA HOLDINGS LLC". Standalone **HOA** is a common
Vietnamese given/family name, and owners.py is meant to be reused for Gwinnett/DeKalb. Bare **ASSOC** also
means ASSOCIATES: the cache has "… PARK ASSOC L P" and "… PIEDMONT ASSOC".
The spec's narrow list also misses about 19% of association parcels in the cache: TOWNHOME/TOWN HOME/TOWNHOUSE
ASSN, "HOMEOWNERS" alone or truncated ("HOMEOWNERS ASS", "ASSOCIAT ION"), misspellings
(HOMEOWNRS/HOMEOWERS/HOMESOWNERS/HMOWNERS), and NBHD/COMM abbreviations. HOAs written with "ASSOC" are typed
'individual' today (+10).
*Mitigation:* count HOA only with corporate context (INC/LLC/CORP/THE, or ≥4 tokens). Don't treat POA alone as
an association. Never match bare ASSOC or generic ASSOCIATION (trade, municipal and athletic associations, and
FEDERAL NATIONAL MORTGAGE ASSOCIATION). Whether to add TOWNHOME/HOMEOWNERS-alone/typos, which I recommend, and
generic "OWNERS ASSOCIATION" (business-park POAs) is a human decision.
Order: check after development_authority/developer/utility/government and before religious/estate/trust/entity.

**R8. VA and estate.** If ADMINISTRATION is dropped from `_ESTATE_RE` with nothing added, "VETERANS
ADMINISTRATION" becomes **individual**, which is worse: +10 and it passes the screens. Under "ADMINISTRATOR =
estate", "ADMINISTRATOR OF VETERANS AFFAIRS" (a common VA tax-roll form) stays estate. Verified today:
"SECRETARY OF VETERANS AFFAIRS" is individual, "SMALL BUSINESS ADMINISTRATION" is estate.
*Mitigation:* add government forms (VETERANS ADMINISTRATION|AFFAIRS, SECRETARY OF HOUSING/VETERANS, SMALL
BUSINESS/GENERAL SERVICES/FEDERAL … ADMINISTRATION). The government check already runs before estate. Estate
keeps `ADMINISTRAT(OR|ORS|RIX)\b`.

**R9. Surname-like developer patterns go beyond SCANNELL/DERMODY.** Verified as 'developer': individuals named
BREIT, SEEFRIED, WEEKS ROBINSON and HILLWOOD. PANATTONI, DALFEN and EXETER are the same kind of name. The entity
context has to be a legal-form suffix **or** a real-estate word, not the real-estate list alone, because "DALFEN
AMERICA CORP" and "EXETER 5400 LLC" must stay developer. Remaining false-positive risk (accepted): a family LLC
whose name is one of these surnames.

**R10. `decide()` fails open on git errors.** `_git` returns "" on failure, so the porcelain/untracked check
passes and a row is logged with commit "". *Mitigation:* refuse when HEAD can't be resolved, and make the new
checks fail closed.

**R11. The missing-BEST_FILE fix is too narrow as written.** Also refuse when BEST_FILE can't be parsed or its
`commit`/`fingerprint` doesn't match `best`. That fails closed on a torn write: the row is appended before
BEST_FILE is written. Also refuse when rows exist for fp but **none is an ancestor**, which happens after a reset
or branch from before the baseline. Today that case silently re-baselines with **no** touched-file or EVAL_FILES
guards. `--new-baseline` stays the human escape. This must be designed together with R1, or a commit-match check
causes the same permanent refusal.

**R12. The one-file diff is limited to `-- screener`.** A commit that adds a root-level helper.py alongside
rank.py passes. Check the whole commit (`git diff --name-only HEAD~1 HEAD`). Change the porcelain check to
`--untracked-files=all -- screener`; ignored files (experiments.tsv, best_ranking.json, cache/, output/,
`__pycache__`) are already excluded. Note that ignored dirs are importable as namespace packages (for example
`screener.cache.x`), so the R3 allowlist is the main guard and the untracked check is a second one.

**R13. Settings and placement.** The association screen goes in `attribute_rejects`, never `site_rejects`, and
reads any new key with `s.get(key, True)`. The test `SETTINGS` dict, build_eval's settings and the frozen meta
screens lack new keys, and `s[key]` raises KeyError. A setting is optional; development_authority has none.

**R14. decide() has no tests today, and new ones must be hermetic** (SR-6, SR-13 analog). Patch
`LOG_FILE`/`BEST_FILE` to a temp dir, and patch `_git`/`_is_ancestor`/`fingerprint`. Never shell out to the real
repo's git (state-dependent, flaky in worktrees). Never create `screener/autoresearch/experiments.tsv` or
`best_ranking.json`. Never run `--decide`/`--new-baseline`/`--test`/build_eval.

## LOW

**R15. The FLU fix is partly mis-specified.** 'Industrial Park' and 'Business Park' are already **industrial**,
because the industrial regex runs first. In the cached vocabulary of the 6 configured sources, the only real
defect is Fairburn 'Neighborhood Commercial' → residential. 'Office Park' and 'Parkway Commercial' don't appear,
so the fix is preventive. Must keep: 'Suburban Neighborhood'/'Suburban II Neighborhood' (~45k rows) residential;
'Rural/ Agricultural Neighborhood' rural; 'Conserve/ Preserve', 'Open Space', 'Private Open Space', 'Greenspace'
conservation; 'Industrial, Open Space' industrial. Add synthetic Parks/'Parks/Open Space'/'Parks & Recreation'
→ conservation. Consider 'Mobile Home Park' → residential, and 'Parking' → not conservation (today it's
conservation). Out of scope: FID `LandUses` comma lists that end in "Open Space" (for example 'Live-Work Regional,
…, Open Space') classify as conservation ('hard').

**R16. scoring.py.** NaN and +inf → 100 and −inf → 0 (verified). Treat any non-finite value as 0, add a
user-visible note in `why_site`, and don't print `nan` in `learned_score`. Also treat a non-numeric score as 0:
`float(None)` raises TypeError at the very end of a live run, after the slope checks, and loses the run.

**R17. Moving `unittest.main()`** is trivial. Check that `python -m screener.tests.test_screener` now runs 23
tests, not 18.

**R18. CI does not run the screener suite** (offline-tests.yml runs the root suite only, on Python 3.11). Changes
depend on local runs and must stay 3.11-compatible: no PEP 701 f-strings. Run `/usr/bin/python3.11 -m
py_compile` on changed files.

**R19. SR-8.** New tests and docs use synthetic individual names; organisation names are fine. test_screener.py
already holds real-looking individual names (pre-existing, out of scope).

**R20. Docs.** README: the "buyable" definition, the association owner type and screen, and the new evaluator
refusals. PROGRAM.md is for the human to edit, not this track: allowed imports and purity rules, what to do on
REFUSED (it only covers DISCARD/KEEP today), that untracked files under screener/ get refused, and recovery from
a missing best_ranking.json. run.py's funnel label "not built on, not government / developer / exempt" now also
covers associations; that file belongs to another track. Process conflict: STANDING_RISKS asks the decision note
to name the model, but the orchestrator forbids model names, so record "independent fresh-context reviewer".

---

## Go / no-go gates for Agent 3
1. Root suite: 636 OK. Screener suite: 23 + new tests OK. `python -m screener.tests.test_screener` runs 23.
2. `git diff --name-only 29c051e` ⊆ the allowed files. It does not touch rank.py, build_eval.py, data/,
   PROGRAM.md, arcgis/layers/checks/run/report, or any root invariant file. No new deps (SR-10). `LOG_COLUMNS`
   unchanged.
3. Plain `evaluate`: hits 2, positives 39, k 46, score_range [6.5, 85.0]. `--control`: mean 0.86, p5–p95 [0, 3].
   An extra count field is OK.
4. No experiments.tsv, best_ranking.json or stray files in the repo (`git status --porcelain --ignored screener/`).
5. R1 flow test passes: after `--new-baseline`, a rank-only commit is scored.
6. R11: missing/corrupt/mismatched BEST_FILE with rows for fp → exit 3, no row appended, no file written.
   Orphaned rows → refused. `--new-baseline` still records.
7. R2/R3: current rank.py passes; rank.py with `import math` passes. Fixtures with os/sys/json/pathlib/importlib
   imports, `from typing import sys`, open/eval/exec/getattr/globals, dunders or `global` → refused. The check
   runs before rank.py is imported: a refused fixture's side effect must not happen.
8. R12: an untracked non-ignored file under screener/ → refused; ignored files don't cause a refusal. A commit that
   touches rank.py plus any other file → refused.
9. R4/R16: one NaN score in the middle → reported and refused by decide; ±inf refused. `scoring.score` with
   NaN/inf/None → finite score, tuned part 0, visible note.
10. R7/R10/R13 owner tables, positives and negatives: the HOA forms → 'association' and an attribute reject;
   'ASSOCIATED …', FNMA, trade/municipal associations, '… ASSOC L P', 'POA HOLDINGS LLC', 'SMITH JOHN POA',
   'NGUYEN HOA', 'NGUYEN THI HOA' → not an association.
11. R9 developer tables (both directions); existing OwnerTests unchanged.
12. R8: VA/SBA/Secretary forms → government; '… ESTATE OF', 'ADMINISTRATOR', 'ADMINISTRATRIX' → estate.
13. R5: spec example False; every existing same_owner/acquisition/sold_after assertion holds; the legit-variant
    classes are True and the different-entity classes are False; the offline before/after counts and the
    hand-checked sample are reported (counts only).
14. R15: an FLU regression table over the real cached vocabulary is unchanged except 'Neighborhood Commercial'.
15. `python3.11 -m py_compile` passes on changed files. The decision note covers the frozen-key divergence (R6),
    recommends that a human rebuild the key and re-baseline, and lists the PROGRAM.md edits.
