# Check of the Atlanta future-land-use fix (commit b190eb6)

> **Restored, 2026-11-13.** The fix and its test (b190eb6, 90a46ec) were
> held before merging (reverted in 0cea72b, c00525a): CI's root test `tests/test_zoning_import.py`
> `test_snippet_matches_the_screener_sources_exactly` requires
> `reviews/21_zoning/sources_json_snippet.json` (the root zoning importer's
> proposed sources) to match `screener/fulton.py`. The follow-up reapplies
> both commits and sets the snippet's `atlanta_flu` `flu_field` to NDP.
> `zoning_import.py` is unchanged: it checks that a `role="flu"` entry names
> a field and then drops it (no FLU is fetched or written), so it needs no
> NDP code table. `sources.json` has no `zoning_sources` key yet, so no
> root run or score moves. Live Atlanta screener grades read NDP from the
> next screener run.
>
> Follow-up check (Tier 1, one fresh-context reviewer, 2026-11-13; model
> as reported by the session, Opus 5.5): **sound**. The reapplied screener
> code is byte-identical to b190eb6..90a46ec. No root module reads a flu
> entry's field (`ZoningSource` has no such attribute; only role="zoning"
> sources are fetched, here and in `adjacency_import.py`). The snippet does
> not carry `flu_labels`: the validator would accept and ignore it, which
> is dead config in a block meant for a human-owned file. A future root
> reader of future land use adds the table and its validation then.
> Tests: zoning 127, operator decisions 48, screener 24, all OK.

Fresh-context reviewer, 2026-11-12. Summarised by the launching session;
the reviewer's report follows in its own order.

**Verdict: sound.** Nothing blocks the commit.

- **The field and its codes are right (verified by running; 10 requests to
  gis.atlantaga.gov, robots.txt 404).**
  - NDP is the field layer 8's legend draws from.
  - The legend has 13 codes and names, and the table matches them exactly.
  - 15 NDP values are in use: those 13, plus IF (3 records) and LD-MU (1).
    The table has all 15.
  - No NDP is null, padded or in another case.
  - DP_NAME disagrees with NDP on 24 records, so leaving it unused is right.
- **LD-MU's name is a guess, on one record of about 0.1 acre (NOTE).** IF
  is well supported by its LANDUSECOD and LANDUSEDES.
- **No other zoning source is affected.** The request now asks for NDP.
  Cache keys include the parameters, so old answers are not reused.
- **Industrial Flex reads as industrial, so any zoning under I-Flex gets
  "likely" (NOTE).** Compared with LANDUSEDES:
  - about 69 acres once Mixed-Use are now I-Flex;
  - about 91 acres once Single-Family are now I;
  - about 308 acres of old Industrial are now LDR, and about 320 acres OS.

  I-1 stays by right. MRC-2 counts as residential through the "MR" prefix,
  which predates this commit.
- **SHOULD-FIX: the test did not pin the whole code table.** 4 of 12
  mutations survived.

Tests: 634 OK.

## What was done

- The test now asserts the category of all 15 codes.
- The comment in `fulton.py` names the two codes the legend does not show.
