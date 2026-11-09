# 10 — F6 worst-case timing claim (fix)

**Defect (confirmed).** README said "Worst case is under a second per parcel".
Reproduced through `sitefit.fit_parcel`: a 1,400 ft square with 100 small
20 ft holes simplifies to under 600 vertices, is fitted, finds nothing and
takes about 4.3 s (`simplified=True`, 44.1 open acres). The 0.7 s figure came
from a 36-hole shape, which is not the worst case the cap allows. 121 holes
goes over the cap and is refused in about 0.01 s.

**Fix.** Kept the cap and pruning as they are (changing them changes which
parcels get fitted). README now states the measured worst case: about 4 to 5 s
per parcel, for open ground just under the 600-vertex cap where nothing fits.

**Regression tests** (`tests/test_sitefit.py`):
- `test_worst_case_timing` now uses the 100-hole shape and asserts it is
  fitted after simplify, finds nothing, and stays under 20 s.
- `test_readme_states_the_measured_worst_case` fails if the README goes back
  to "under a second" or drops the measured figure.

The 0.7 s in `08_f6_code_writer_response.md` is left as the historical record;
this file corrects it. No root file touched. Suite: 291 tests OK.
