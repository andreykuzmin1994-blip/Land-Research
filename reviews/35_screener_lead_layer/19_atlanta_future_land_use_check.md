# Check of the Atlanta future-land-use fix (commit b190eb6)

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
