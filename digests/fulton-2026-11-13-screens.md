# Fulton County — 2026-11-13, with the new screens

Second run of the day. Code: `claude/upbeat-feynman-4bc1be` at 5d40e25,
not `main`. It is `main` plus the operator-requested screen changes of
2026-11-13 (below), not yet merged. Same data and cache as this morning's
[`fulton-2026-11-13`](fulton-2026-11-13.md); the decisions from the
2026-11-12 document checks are applied (1 `pass`, 11 `maybe`, in
`decisions/decisions.csv`). About four minutes; every source answered;
the neighbour check reached all 250 parcels it asked about.

Reading list: [`leads.md`](../runs/fulton-2026-11-13-screens/leads.md).

## What changed in the screen

1. **Built sites are screened out.** A parcel the county's deed file lists
   with 2,500+ sq ft of commercial buildings and $50,000+ of improvements
   is "already built on", even when the improvements are a small share of
   the land value (`[screens] built_min_building_sf`,
   `built_min_improvement_value`). Live only; the evaluation never uses it.
2. **Norfolk Southern's real-estate company** (SOUTHERN REGION INDUSTRIAL
   REALTY) counts as railroad-owned and is screened out.
3. **New lead-grade items** (report only; scores and order unchanged):
   - a listed contamination site within 250 ft, or on a bordering parcel;
   - the owner's own built-on parcel next door, now looking at touching
     parcels of any size (the 94-acre Costco site, Owens Corning,
     Microsoft and trucking terminals all show up); houses don't count;
   - an owner name that reads like a quarry or waste company.

## The AutoResearch loop

Baseline recorded on 5d40e25 (graded 11.7 of 35 dev sites, 2.4 hits; the
screen changes do not touch the evaluation). A fresh session ran 15
experiments on `rank.py`: **none kept**, so the ranking is unchanged.
Every change that gave access credit past the 6-mile cutoff scored above
the baseline (+1.0 to +1.8 graded), none reached the +2.5 bar. The log and
best-run record are in [`autoresearch/`](../autoresearch/) on this branch.

## Leads

- **Grade A: 3, down from 12.** None is new. All three already carry a
  `maybe`: 0 Irwin Rd (#4) and 0 Spence Rd (#8) are in South Fulton's
  March 2026 rezoning for a developer (probably taken), and 601 Senoia Rd
  (#30) has a superseded plan label behind its "likely" zoning path.
  **In practice there is no unchecked grade A lead this week.**
- **Grade B: 68, down from 94.** 17 are new (13 of them came up from
  "not graded" as built sites above them left the top 250), 43 left.
- **What became of this morning's 12 grade A leads:**

| Was | Site | Now | Why |
|---|---|---|---|
| #2 | 81 Poole Creek Rd SE | screened out | railroad owner |
| #4 | 3600 Naturally Fresh Blvd | screened out | built: 4 buildings, 17,438 sq ft |
| #6, #11 | 0 Irwin Rd, 0 Spence Rd | still A (#4, #8) | marked `maybe` (ordinance) |
| #12 | 0 Connally Dr | C | cleanup site 169 ft away; owner's built parcel next door |
| #28 | 3132 Parrott Ave NW | screened out | your `pass` (fuel terminal) |
| #41, #48 | 0 Buffington Rd strips | B (#29, #36) | quarry owner name |
| #42 | 601 Senoia Rd | still A (#30) | marked `maybe` |
| #46 | 0 Bohannon Rd | B (#34) | waste-company owner name |
| #142 | 1688 Empire Ave | screened out | built: 9 buildings, 40,045 sq ft |
| #171 | 0 Cooks Rd | B (#126) | Costco's own 94-ac and 66-ac sites next door |

- **Screened out as built: 81 parcels** that this morning were ranked
  (4 A, 18 B, 33 C, 6 blocked, 22 not graded). The B ones include 897
  Adamson St SW, 1024 Sampler Way, 3650 Jonesboro Rd SE (161,752 sq ft),
  1745 Connally Dr (the wood-treating cleanup site), 650 Ponce de Leon Ave
  NE and 4250 Stacks Rd.
- **Top 50:** 13 new, 13 left, nearly all from the built-site screen.

## Worth a document check next

The B leads ranked highest with a single, soft open item (a lower-bound
fit, a pond, or a recent deed), all zoned industrial by right:

| # | Site | Acres | Zoning | Open item |
|---|---|---|---|---|
| 11 | 0 Graham Rd, Fairburn | 9.6 | M-2 | no envelope found (lower bound) |
| 16 | 6715 Oakley Industrial Blvd, Union City | 13.9 | M-1 | no envelope found |
| 18 | 5009 Terminus Dr, Fairburn | 14.2 | M-2 | no pond room beside the envelope; the owner is a pet-food maker, so check for its plant next door |
| 21 | 0 Mount Zion Rd SW, Atlanta | 9.4 | I-1 | no envelope found |
| 23 | 4380 Stacks Rd # Rear, South Fulton | 21.0 | M-2 | last priced deed 2.8 years ago |
| 25 | 0 Welcome All Rd, South Fulton | 11.2 | M-2 | no pond room beside the envelope |

(#20, 0 Fayetteville Rd, is one of the ordinance parcels already marked
`maybe`.)

## Limits

- The neighbour check follows the owner's name loosely and sees only
  touching parcels; a plant held under another company name is missed
  (Nestle Purina at #18 is an example to check by hand).
- Soils and road frontage cover only the top 250; 1,249 parcels are not
  graded.
- 531 ranked parcels have no machine-readable zoning (mostly Chattahoochee
  Hills and Palmetto).
