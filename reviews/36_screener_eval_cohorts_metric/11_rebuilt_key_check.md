# Check of the rebuilt key (2026-11-12)

Recorded as received from a fresh-context checker that did not build the
key. The files it checked are the ones committed in "answer key rebuilt
under the 2026-11-11 builder rules" on `claude/magical-dirac-0kll8l`
(uncommitted at the time of the check; unchanged since).

**Check of the rebuilt key (2026-11-12, uncommitted files in `screener/autoresearch/data/`)**
Checker: Claude Opus 5.5, fresh context, read only. Scripts and outputs are in /tmp/claude-0/-home-user-Land-Research/073c2925-b364-5a5f-90db-1d7fbc42afeb/scratchpad/keycheck_agent/. Nothing in the repository was changed (`git status` shows only the 10 key files that were already modified).

**Verdict: sound after these fixes.** No record has a wrong label. But a name-matching flaw leaves out two dev sites (6 records) that should be positives.

**How I checked.** I re-ran the builder's own code (`fetch_parcels`, `attribute_rejects`, `fetch_built`, `covered_share`, `classify`) from the cache only, with the network blocked, and read the deeds with `fulton._read_cama_zip`, the loader that `read_cama` uses. The replay gives exactly the files: the same pools, keys, positives, blocks and deed features in every year.

| Item | Result |
|---|---|
| 1. Built parcels dropped by the first-quarter rule | 2015: 1, right. 2016: 1, **wrong** (a true January owner). 2017: 1, doubtful, but the window rule drops it anyway. 2018 and 2019: none built. |
| 2. Positive chains (141 records, 43 sites) | None contradicts its label. Each owner sold at ≥ $50k after the as-of date and before the building, and no one else had a priced deed in the window. |
| 3. 2015–17 positives whose owner bought at ≥ $50k in 2012–14 (purchase deed; any such deed in brackets) | 2015: dev 3 (4) of 30, test 2 (2) of 10. 2016: dev 2 (3) of 26, test 2 (2) of 10. 2017: dev 1 (2) of 23, test 1 (1) of 8. |
| 4. Old key → new key, pool and positive records | 2015 dev 2,228→2,217 (30→30), test 947→940 (10→10). 2016 dev 2,224→2,214 (29→26), test 949→945 (10→10). 2017 dev 2,179→2,165 (29→23), test 944→939 (11→8). 2018 dev 2,141→2,129 (19→13), test 917→912 (7→6). 2019 dev 2,107→2,093 (12→9), test 914→911 (7→6). Sites: dev 38→33, test 11→10. 23 old positive records were lost: 22 to the window rule (dev 17, test 5) and 1 to the first-quarter rule (2017 dev; that owner also bought inside the window). None became a negative, and there are no new positives. |
| 5. File problems | None. No duplicate keys within or across years. No parcel is in both splits. Every banned field is present and blank. Every record's cohort matches its file. The sites list matches the positives. Every site's weights sum to 1, with one block and one split. Splits match `test_blocks.json`. `evaluate.load()` loads the key cleanly (fingerprint `d2a7c5649330`). |

**Findings**

**SHOULD-FIX 1: names cut at 30 characters fail `same_owner`, so two dev sites are left out.**
- **Site `13 0129  LL0455`.** The 2016 digest owner sold on 21 Jan 2016 for $515,304 (plus a $0 deed the same day). The building first appears in the 2024 digest. The deed file cuts "…CO INC" to "…COMPANY I", and `same_owner` reads the lone "I" as a fund number, so the names don't match. Result: in 2015 it is left out as "built but not sold by the as-of owner". In 2016 it is left out by the first-quarter rule, which is the wrong-drop case in item 1. Both records should be positive.
- **Site `09C160000610469`.** The deed name ends "…, LLC A DELAWARE", cut at 30 characters. That "organised in" tail has no comma after LLC, so it is not stripped. The owner sold on 28 Jun 2021 for $2.3M, and the building first appears in the 2023 digest. The 2016–19 records are left out but should be positive.
- **How I checked.** I re-classified all 15,620 pool records with a `same_owner` that also tries a 30-character name minus its last word. Exactly these 6 records change, all from left out to positive; nothing else moves.
- **Fix.** Change `owners.same_owner`. It is shared with the live tool, so this is the operator's call. Then rebuild.

**NOTE 2 (item 1, 2017, `09F110200550111`).** The rule fired on a $38,050 deed dated 25 Jan 2017 from a third party. A $0 deed in Dec 2016 and the $650k sale in Jun 2017 both have the digest owner as seller, so the digest probably shows the January owner. It makes no difference to the key: that owner bought at $1.42M in Oct 2016, so the window rule leaves it out anyway.

**NOTE 3: weakly grounded positives** (I read each chain by hand).
- Two parcels (`07 310000920098`, `07 360200930025`; 10 records) match only on a near-spelling between the digest and deed names (a one-letter difference). The chain makes a typo likely.
- `09F120200570142` (2015) rests on a deed of exactly $50,000. The parcel then changed hands twice more in 2015.
- `14 003400040080` (2017): the digest name changed between 2016 and 2017 with no deed in the window. A 2009 priced deed points to a buyer from before the window, which agrees with the label.
- `13 0033  LL0088` and `13 0033  LL0195`: a second company sold the same day at the same price, which looks like a double closing.

**NOTE 4 (N7, operator's call).** Item 3's pre-window recent buyers are still positives, as the window rule allows.

**Not checked**
- Map-layer features (flood, wetlands, rail, exits, easements).
- Whether the cached digests match the live county layers.
- First-quarter drops of never-built parcels.
- The test split beyond counts.
- `--control`, the baseline, `--null-test` and the trait-zeroing probe on the rebuilt key.
- Whether `same_owner` has false matches beyond the 43 positive sites I read.
