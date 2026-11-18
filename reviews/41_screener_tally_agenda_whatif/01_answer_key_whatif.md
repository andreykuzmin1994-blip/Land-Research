# 41 — More Fulton years in the answer key: what-if, and why no change is proposed

Asked by the operator on 2026-11-18: build "more Fulton years in the
answer key". The key (`screener/autoresearch/build_eval.py`,
`autoresearch/data/`) is human-only, so nothing there was edited. Instead
the builder's own code was run with other years, **writing into a scratch
folder**: `build_eval.DATA` was pointed away, `COHORTS` and the label year
were set from outside, and the sealed test blocks were read from where
they are. `git status` showed no change under `screener/` afterwards.

The harness first rebuilt today's key and got it exactly: 45 distinct
sites, 35 dev / 10 test, 147 site-years. The other numbers below come
from the same code and the same county services, on 2026-11-18.

## Results

| Variant | Distinct sites | Dev / test | Site-years |
|---|---|---|---|
| Today: as-of years 2015-2019, built by the 2025 digest | 45 | 35 / 10 | 147 |
| As-of years 2015-2021 | **45** | 35 / 10 | 162 |
| Built by the county's 2026 snapshot layer | not possible | | |
| Built by the county's live parcel layer (as "2026") | 48 | 36 / 12 | 153 |

**Adding the 2020 and 2021 as-of years adds no site.** The key counts a
site only under an owner from before 2015 (a parcel bought at a real
price since 2015 is left out), so a site that is an answer in 2020 was
already an answer in 2015. The two extra years show the same 45 sites 15
more times: more views, no more evidence, which is the limit
`PROGRAM.md` already names for the five years it has.

**The county's 2026 snapshot (`Tax_Parcels_2026`) cannot date a
building.** It has 28 fields and no improvement value (`ImprAppr`); the
2025 snapshot has 34 fields and has it. The builder's "built" test needs
it. This answers the open point in `AGENTS_DESIGN.md` ("fields not
checked").

**The live parcel layer as a 2026 label moves 11 sites for a net gain of
3.** 7 sites come in and 4 go out (48 against 45; dev 36 against 35). The
gain on the dev split, which is what the loop learns from, is one site.
The price: the label would rest on a layer that changes every day rather
than on a dated snapshot, so the key could not be rebuilt to the same
bytes later, and the thresholds measured on today's key (`MIN_GAIN`, the
null test of `reviews/36`) would have to be measured again.

## Conclusion

No change to the key is proposed. More Fulton years, as the county
publishes them today, do not add evidence. I had put this forward as a
way to give the loop more to learn from; measured, it is not one.

What would add evidence, in the order the data suggests:

1. **The operator's own decisions** as the second term of the score
   (Phase B), at about 30 marks. There are 12. The mark buttons on the
   viewer page (`reviews/39`) are the way there.
2. **More counties.** A county with dated yearly digests gives new sites,
   not new views of the same ones (`reviews/35/05_county_feasibility.md`
   found none nearby with a 2016 snapshot; that was not re-checked here).
3. **The window rule itself.** Each year 9 to 15 pool parcels are left
   out as "bought at a real price since 2015", and 29 to 56 as "built but
   not sold by the as-of owner". Whether some of those are answers is a
   rule decision that `reviews/36` settled on purpose; it is the
   operator's to reopen, and it is the only one of these that more
   Fulton data would help.

## An observation, not checked further

The county service `Tax_ParcelsCurrentDigest_GCS_WGS_1984` has the
improvement field and was last edited on 2022-11-02. It may be the 2022
digest the builder treats as never published. In today's key no site is
left out for the missing 2022 digest, so it would change nothing now; it
would matter only if the rule for ordering a sale and a building were
tightened.

## What was run

Four runs of `build_eval.main()` through a scratch script (about three
minutes each, against the county's public services, cached under the
gitignored `screener/cache/`), plus two layer-description requests. The
scratch outputs were not kept: the numbers above are all that was read
from them, and no feature or label of the key is reproduced here.
