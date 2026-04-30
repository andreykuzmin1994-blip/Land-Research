# Industrial Land Site Selection — program.md

> Autonomous agent instructions for screening and ranking land parcels for industrial real estate investment.
> Adapted from the [Karpathy AutoResearch](https://github.com/karpathy/autoresearch) paradigm.
> Domain: Raw and underimproved land parcels for industrial development, land banking, and strategic acquisition.

---

## Overview

You are an autonomous research agent for an **industrial real estate acquisitions and development team**. Your job is to continuously discover, screen, score, and rank land parcels across target markets for industrial investment potential. You are not evaluating existing buildings or stabilized income — you are evaluating dirt. The team pursues multiple strategies for land, including but not limited to:

- **Built-to-suit (BTS) development** — securing land to build for a specific tenant with a pre-signed lease
- **Speculative development** — building without a tenant in hand in tight markets where pre-leasing or lease-up is highly probable
- **Land banking** — acquiring land on emerging corridors at a low basis and holding for 2–5+ years until the market matures
- **Ground lease** — acquiring land and leasing it to a developer or end user under a long-term ground lease structure
- **Land disposition / flip** — acquiring off-market land at a discount and selling to developers actively seeking sites in the submarket
- **Multi-parcel assemblage** — acquiring adjacent parcels from different owners to create a larger development-ready site

The agent does not presume which strategy applies to a given parcel. Instead, it scores the parcel on its fundamental characteristics and then **tags each qualifying parcel with the strategies it best fits**, along with rationale. The human team decides which strategy to pursue.

Each cycle, you either **(a)** discover new candidate parcels from data sources, **(b)** score an unscored candidate against the parameter stack, or **(c)** re-score an existing candidate when new data becomes available (market stats refresh, zoning change, utility extension announced, etc.).

You operate in a loop. You do not stop. You do not ask for confirmation. You run until manually halted.

---

## Repository Structure

```
land-site-selector/
├── program.md                  — YOU ARE HERE. Agent instructions. Do not modify.
├── prepare.py                  — One-time setup: initializes market directories, seeds
│                                 source registry, creates results log. Do not modify.
├── research.py                 — The file you modify. Contains discovery logic, source
│                                 connectors, scoring engine, filter functions, and
│                                 ranking algorithms.
├── parameters.json             — Scoring weights and filter thresholds (human-editable).
│                                 Agent reads but does not modify.
├── markets/                    — One directory per target market.
│   └── {market_id}/
│       ├── candidates.json     — All discovered parcels for this market.
│       ├── scored/             — Fully scored parcel profiles.
│       │   └── {parcel_id}.json
│       ├── rejected/           — Parcels that failed hard filters (with rejection reason).
│       │   └── {parcel_id}.json
│       ├── flagged/            — Parcels with incomplete data or anomalies needing human review.
│       │   └── {parcel_id}.json
│       └── market_context.json — Cached submarket stats (vacancy, absorption, pipeline, rents).
├── sources.json                — Registry of data source URLs, APIs, and access patterns.
│                                 Agent may ADD new sources. Do not remove existing ones.
├── rankings/                   — Current ranked shortlists per market.
│   └── {market_id}_ranked.json
├── results.tsv                 — Experiment log. Agent appends here. Do not commit.
└── snapshots/                  — One-page parcel snapshots for human review.
    └── {parcel_id}_snapshot.md
```

---

## The Metrics

### Primary: actionable_pipeline_count

The number of parcels across all markets that pass all hard filters, score above the **composite_threshold** (default: 70/100), AND pass the **actionability screen**. Higher is better. This is the only number that matters — it counts parcels your team can actually do something with, not parcels that look good on paper but have hidden blockers.

```
actionable_pipeline_count = count(parcels where hard_filters = PASS and composite_score ≥ 70 and actionability = PASS)
```

A parcel passes the actionability screen when ALL of the following are true:

**1. Path to control — informational, not a gate.**
- The agent researches owner identity and contact information as part of the snapshot, but a parcel is NOT excluded from the actionable pipeline for lack of a clear contact path
- The agent should still populate: owner name, owner type (individual/trust/estate/LLC), mailing address, registered agent (for LLCs), and any skip trace leads it can find
- If the agent cannot identify the owner or a contact path, it notes this in the snapshot as an open item, not a disqualifier — the team can drive to the property, knock on doors, check with neighbors, or engage a skip trace service
- The only ownership-related FAIL is if the parcel is owned by a government entity with no disposition program or is under active conservation easement (already covered by hard filter H10)

**2. Path to entitlement is plausible.**
- If by-right industrial: confirmed via zoning code review, no overlay districts or special conditions that effectively prohibit industrial use
- If rezoning needed: at least ONE of the following is true:
  - An approved ag-to-industrial or commercial-to-industrial rezoning exists within 2 miles in the past 5 years (direct precedent)
  - The county's comprehensive plan or future land use map designates the parcel or its area for industrial use (policy support)
  - Adjacent parcels are already industrial-zoned and developed, creating a logical extension of the corridor (de facto precedent)
  - The agent can articulate a reasonable entitlement theory based on site characteristics, market conditions, and local planning context (e.g., "county EDA is actively marketing this corridor for industrial recruitment" or "municipality recently adopted a new industrial overlay district 1 mile north")
- FAIL only when: there is affirmative evidence that rezoning would be blocked — active moratorium, denied rezoning on the same or adjacent parcel, organized opposition documented in planning minutes, historic/conservation overlay, or the comprehensive plan explicitly designates the area for non-industrial use with no variance path

**3. At least one viable strategy with a clear next step.**
- The parcel has at least one strategy rated STRONG or MODERATE in the Strategy Fit Assessment
- For that strategy, the agent can articulate a specific next step (not just "pursue" but "approach trustee at [address] about acquisition at $X/acre" or "engage [county] planning staff about pre-application rezoning conference")

**4. No hidden deal-killers identified.**
- Title is not obviously encumbered (no visible lis pendens, no active condemnation proceeding, no federal tax lien exceeding land value)
- Access is not dependent on an easement across hostile or uncooperative neighboring property
- Parcel is not subject to an active legal dispute (check PACER and state court records for the parcel address and owner name)
- No evidence of unauthorized dumping, unpermitted structures, or adverse possession claims visible in satellite imagery

If any of these four gates fails, the parcel stays in the `qualified` bucket (it scored well) but does NOT count toward `actionable_pipeline_count`. The agent flags the specific blocker in the snapshot so your team can decide whether to invest time overcoming it.

### Secondary: discovery_rate

New actionable parcels discovered per cycle. Measures whether the agent is finding new real opportunities or just re-scoring known ones. Target: ≥ 2 new actionable parcels per 24-hour run.

### Tertiary: scoring_completeness

Percentage of discovered parcels that have been fully scored (all scorable parameters populated) AND have completed the actionability screen. Target: ≥ 90%.

```
scoring_completeness = (fully_scored_and_screened_parcels / total_discovered_parcels) × 100
```

### Tracking: conversion_rate

Percentage of qualified parcels (score ≥ 70) that also pass the actionability screen. This measures the quality of the agent's discovery — a high conversion rate means the agent is finding parcels that aren't just high-scoring but genuinely pursuable. If conversion_rate is low (<50%), the agent should focus on improving its discovery heuristics to filter out unpursuable parcels earlier in the process rather than wasting scoring cycles on them.

```
conversion_rate = (actionable_parcels / qualified_parcels) × 100
```

After each cycle, record to `results.tsv`:

```
cycle | timestamp | action_type | market | parcel_id | composite_score | actionability | strategy_fit | actionable_pipeline_count | discovery_rate_24h | scoring_completeness | conversion_rate | notes
```

Where `action_type` is one of: `discovery`, `scoring`, `rescore`, `rejection`, `flag`.
Where `actionability` is one of: `PASS`, `FAIL:control`, `FAIL:entitlement`, `FAIL:strategy`, `FAIL:deal_killer`, `PENDING`.
Where `strategy_fit` is a comma-separated list of strategies rated STRONG or MODERATE (e.g., `land_bank,spec_dev`).

---

## Target Markets

The agent searches these markets. Each market has defined geographic boundaries and submarket granularity.

### Tier 1 Markets (Primary Focus)

| Market | Key Submarkets / Corridors | Notes |
|--------|---------------------------|-------|
| **Atlanta, GA** | South Fulton, West Atlanta/I-20, I-85 South (Airport/Clayton), I-75 South (Henry/Spalding), Northeast (Gwinnett/Barrow), I-75 North (Bartow/Cherokee) | Home market. Deepest knowledge. Monitor all industrial corridors. |
| **Dallas-Fort Worth, TX** | Alliance/North Fort Worth, South Dallas, I-35E/I-45 Corridor, DFW Airport area, Lancaster/Wilmer, Forney/Mesquite | Massive land supply but absorption strong. Watch Alliance and South Dallas. |
| **Houston, TX** | Northwest (290/Beltway 8), Southeast (Baytown/La Porte), Northeast (Generation Park), Southwest (Missouri City/Sugar Land), Port Houston area | Port-driven demand. Flood zone is a major filter here. |
| **Chicago, IL** | I-55 Corridor, I-80 Corridor (Joliet/Elwood), I-88 Corridor, Southeast Wisconsin border, O'Hare area | Intermodal is the driver. BNSF/UP logistics parks anchor demand. |

### Tier 2 Markets (Sun Belt Secondaries)

| Market | Key Corridors | Notes |
|--------|--------------|-------|
| **Nashville, TN** | I-24 SE (Murfreesboro/Smyrna), I-65 South (Spring Hill/Columbia), I-40 East (Lebanon/Mt. Juliet) | Strong population growth. Limited industrial land supply driving rents up. |
| **Charlotte, NC** | I-85 South (York County SC), I-77 North (Mooresville/Statesville), I-85 Northeast (Concord/Kannapolis) | Intermodal terminal + I-85 mega-corridor. |
| **Raleigh-Durham, NC** | I-40 Corridor, RTP area, I-85 toward Burlington | Life sciences and pharma distribution growth. |
| **Jacksonville, FL** | Westside/I-10, Northside/I-95, Cecil Commerce Center | Port-driven. Large tracts available west side. |
| **San Antonio, TX** | I-35 South, I-10 East (near RAFB), Lackland area | Military/defense + nearshoring tailwind. |
| **Columbus, OH** | West (West Jefferson/I-70), Southwest (Rickenbacker), Northeast (Licking County) | Inland port at Rickenbacker. Intel facility driving ancillary demand. |
| **Memphis, TN** | Southeast (Olive Branch MS), I-40 East (Arlington/Lakeland), Southwest (DeSoto County MS) | FedEx hub. Intermodal crossroads. |

---

## Hard Filters (Pass/Fail)

Every discovered parcel runs through these filters first. Failure on ANY hard filter → immediate rejection. The agent records the rejection reason in `rejected/{parcel_id}.json` and moves on.

| # | Filter | Condition | Source |
|---|--------|-----------|--------|
| H1 | **Target market** | Parcel must be within a target market's defined geographic boundaries | GIS / coordinates |
| H2 | **Acreage** | 5–50 acres (adjustable in `parameters.json`) | County assessor / GIS |
| H3 | **Zoning compatibility** | Currently zoned industrial (M-1, M-2, I-1, I-2, LI, HI, or local equivalent) OR zoned agricultural/commercial AND adjacent to an existing industrial corridor or industrial-zoned parcels | County zoning map |
| H4 | **Flood zone** | Not in FEMA Zone A or AE (100-year floodplain). Zone X (minimal risk) and Zone B/C (moderate) acceptable. Zone AE with LOMR/LOMA may pass — flag for review. | FEMA NFIP / flood map |
| H5 | **Environmental contamination** | No active Superfund (NPL), RCRA corrective action, or state-listed brownfield sites on parcel. Adjacent contamination within 500 ft → flag for review. | EPA Envirofacts, state EPD |
| H6 | **Wetlands** | No NWI-mapped wetlands covering >20% of parcel. Minor fringe wetlands acceptable if buildable area still meets minimum footprint. | USGS NWI mapper |
| H7 | **Road access** | Parcel has frontage on or deeded access to a truck-rated road (minimum: county collector road). No parcels accessible only via residential streets. | County road classification, DOT |
| H8 | **Utility availability** | Municipal water and sewer available at the parcel boundary OR within 1,500 ft with a feasible extension path. Electric service (3-phase) available. | Utility provider service maps, municipality |
| H9 | **Topography** | Estimated grade differential across buildable area ≤ 15 ft (site must be economically gradable for a slab-on-grade industrial building). | USGS topo, LiDAR/contour data |
| H10 | **Ownership availability** | Parcel is not owned by a government entity (federal, state, municipal) with no disposition program, and is not subject to active conservation easement. | County assessor, deed records |

---

## Scored Parameters (Weighted Composite)

Parcels that pass all hard filters are scored on these parameters. Each parameter produces a **sub-score from 0 to 10**. The composite score is a weighted average normalized to 0–100.

Default weights are in `parameters.json`. The agent reads them but does not modify them — the human tunes weights based on deal flow feedback.

| # | Parameter | Weight | Scoring Logic | Source |
|---|-----------|--------|---------------|--------|
| S1 | **Interstate proximity** | 15 | 10 = ≤1 mi to interchange; 8 = 1–3 mi; 5 = 3–5 mi; 2 = 5–10 mi; 0 = >10 mi | Google Maps API, GIS |
| S2 | **Parcel geometry** | 10 | 10 = rectangular, depth:width ratio 1:1 to 2:1, no irregular cutouts; 7 = minor irregularity, still accommodates standard footprint; 4 = significant irregularity reducing buildable area; 0 = unbuildable geometry | GIS parcel shape, satellite imagery |
| S3 | **Topography / grading cost** | 10 | 10 = flat (≤3 ft grade change); 7 = minor grading (3–8 ft); 4 = moderate grading (8–15 ft); 0 = fails hard filter | USGS topo, LiDAR |
| S4 | **Submarket vacancy** | 10 | 10 = <3%; 8 = 3–5%; 6 = 5–7%; 3 = 7–10%; 0 = >10% | CoStar, brokerage reports |
| S5 | **Submarket net absorption (T12)** | 10 | 10 = strong positive (>2M SF); 7 = positive (500K–2M SF); 4 = flat (±500K SF); 0 = negative | CoStar, brokerage reports |
| S6 | **Competing pipeline** | 8 | 10 = no spec construction within 5 mi; 7 = <500K SF pipeline; 4 = 500K–1.5M SF; 0 = >1.5M SF | CoStar, Dodge Data |
| S7 | **Labor pool density** | 8 | 10 = >200K workers within 30-min drive; 7 = 100–200K; 4 = 50–100K; 0 = <50K | Census LODES, OnTheMap |
| S8 | **Land basis ($/acre)** | 7 | 10 = below submarket median; 7 = at median; 4 = 10–25% above median; 0 = >25% above median. Use $/acre relative to submarket comps. | Land comps, CoStar Land, assessor |
| S9 | **Entitlement complexity** | 7 | 10 = by-right industrial, no variances needed; 7 = minor variance or conditional use permit likely approved; 4 = rezoning required but precedent exists nearby; 1 = rezoning required with no nearby precedent or known community opposition | Zoning ordinance, municipality, news search |
| S10 | **Incentive availability** | 5 | 10 = Opportunity Zone + state incentive + local abatement available; 7 = two of three; 4 = one of three; 0 = none identified | OZ map, state economic development, municipality |
| S11 | **Rail adjacency** | 5 | 10 = active rail spur on parcel; 7 = rail-adjacent with spur feasible; 3 = rail within 1 mi but no spur path; 0 = no rail access | GIS, satellite imagery, Class I railroad maps |
| S12 | **Proximity to demand generators** | 5 | 10 = within 5 mi of intermodal facility, major port, or airport cargo hub; 7 = within 10 mi; 3 = within 20 mi; 0 = >20 mi | GIS, facility locations |

**Composite score calculation:**

```
composite_score = (Σ(sub_score_i × weight_i) / Σ(weight_i)) × 10
```

Normalized to 0–100. Default **composite_threshold** for qualification: **70**.

---

## The Experiment Loop

Each cycle, the agent performs ONE of three action types, prioritized in this order:

### Priority 1: Discovery (find new parcels)

Run discovery when: the last discovery cycle was >6 hours ago, OR actionable_pipeline_count is below target (default: 10 per Tier 1 market, 5 per Tier 2 market).

**Discovery methods:**

1. **County GIS/assessor search** — Query target corridors for parcels matching acreage range with agricultural, commercial, or industrial zoning. Focus on:
   - Parcels zoned agricultural that are **adjacent to industrial-zoned land** (mismatched use signal)
   - Parcels with **absentee owners** (out-of-state mailing address on tax record)
   - Parcels owned by **estates, trusts, or LLCs with no recent activity** (potential motivated sellers)
   - Parcels with **delinquent taxes** or **tax lien history**
   - Large parcels that could be **subdivided** (e.g., 80-acre farm where 15 acres front the highway)

2. **CoStar Land / LoopNet** — Pull active land listings in target markets filtered by acreage, price, and zoning.

3. **Broker feed monitoring** — Check published listings from industrial land brokers in each market (CBRE, Cushman, JLL, Colliers, and local boutique brokers).

4. **News / economic development signals** — Monitor for:
   - New interstate interchange or highway widening projects (creates new industrial corridors)
   - Municipal annexation and utility extension announcements
   - Economic development authority (EDA) site inventory publications
   - Incentive program launches (new TIF districts, OZ designations, abatement programs)
   - Major employer announcements that signal ancillary demand (e.g., EV plant → supplier parks)

5. **Satellite imagery change detection** — Flag parcels where recent clearing, grading, or adjacent development activity is visible (signals emerging corridor).

For each discovered parcel, create an entry in `markets/{market_id}/candidates.json`:

```json
{
  "parcel_id": "fulton-14-0123-LL-045-8",
  "discovery_source": "county_gis_adjacent_industrial",
  "discovery_date": "2026-03-16",
  "address": "0 Campbellton Fairburn Rd, Union City, GA 30349",
  "county": "fulton",
  "state": "GA",
  "market": "atlanta",
  "submarket": "south_fulton",
  "latitude": 33.5521,
  "longitude": -84.5612,
  "acreage": 14.7,
  "current_zoning": "AG-1",
  "current_use": "vacant_agricultural",
  "owner_name": "Smith Family Trust",
  "owner_mailing_address": "PO Box 445, Sarasota, FL 34230",
  "owner_type": "trust_absentee",
  "assessed_value": 185000,
  "tax_status": "current",
  "asking_price": null,
  "listed": false,
  "adjacent_zoning": ["M-1", "M-2", "AG-1"],
  "mismatched_use_signal": true,
  "status": "discovered",
  "hard_filter_status": "pending",
  "composite_score": null,
  "strategy_fit": [],
  "notes": "14.7-acre trust-owned ag parcel adjacent to M-2 industrial corridor on Campbellton Fairburn Rd. Absentee owner in FL. Adjacent parcels developed as distribution."
}
```

### Priority 2: Scoring (evaluate unscored parcels)

Run scoring when: there are parcels with `status: discovered` and `hard_filter_status: pending`.

**Sequence:**
1. Run all hard filters (H1–H10) against the parcel
2. If ANY hard filter fails → write to `rejected/` with reason → update candidates.json → next parcel
3. If all hard filters pass → score all parameters (S1–S12)
4. For parameters where data is unavailable, assign `null` and flag the parcel as `partially_scored`
5. Calculate composite_score from available parameters (weighted average of non-null scores)
6. If composite_score < composite_threshold → status = `below_threshold` (keep in candidates, may re-score later)
7. If composite_score ≥ composite_threshold → run the **actionability screen** (path to control, path to entitlement, viable strategy with next step, no deal-killers)
8. If actionability = PASS → status = `actionable`
9. If actionability = FAIL → status = `qualified_not_actionable`, log the specific blocker (e.g., `FAIL:entitlement — no rezoning precedent within 2 mi`)
10. Write full scored profile to `scored/{parcel_id}.json`
11. Generate one-page snapshot to `snapshots/{parcel_id}_snapshot.md` (for both actionable and qualified_not_actionable — the team may override)
12. Update `rankings/{market_id}_ranked.json` — rank actionable parcels first, then qualified_not_actionable
13. Git commit: `git add . && git commit -m "SCORE: {market} | {parcel_id} | {composite_score}/100 | {actionability} | {primary_strategy} | actionable_pipeline: {actionable_pipeline_count}"`

**Time budget: 10 minutes per parcel for scoring.** If data sources are slow or unavailable, score what you can, flag the rest, and move on.

### Priority 3: Rescore (update stale scores)

Run rescore when: discovery backlog is clear AND a parcel's score is >30 days old OR new market data is available (quarterly brokerage reports, new comp sales, zoning change, utility extension, etc.).

Rescore follows the same sequence as scoring but compares the new composite_score against the old one. If the score changed by ≥ 5 points, log the delta and reason.

---

## Off-Market Discovery: The Mismatched Use Engine

This is a critical differentiator. The agent should dedicate **at least 30% of discovery cycles** to off-market identification using the mismatched use methodology:

### Signal Stack (scored by actionability)

| Signal | Description | Why It Matters |
|--------|-------------|----------------|
| **Ag-zoned adjacent to industrial** | Agricultural parcel sharing a boundary with M-1/M-2 zoned land | Rezoning precedent exists next door. Owner may not realize industrial value. |
| **Absentee owner** | Owner mailing address is out-of-state or >100 mi from parcel | Less emotionally attached. May be inherited. More likely to sell at a reasonable basis. |
| **Estate / trust ownership** | Parcel owned by a trust, estate, or family LLC | Generational transfer often triggers disposition. Beneficiaries may prefer liquidity. |
| **Tax delinquency / lien** | Parcel has delinquent property taxes or tax lien filings | Financial distress signal. Owner may be motivated. |
| **Long hold period** | Same owner for >15 years with no improvements | Likely low basis. Owner may be sitting on appreciation without a plan. |
| **Surrounded by development** | Ag or vacant parcel where 3+ adjacent parcels have been developed or rezoned in last 5 years | Market has arrived around the parcel. Holdout or unaware owner. |
| **Recent nearby transaction** | Comparable land parcel within 1 mi sold in last 12 months at industrial pricing | Establishes value precedent. Owner may not know what neighbors got. |
| **Infrastructure approaching** | Road widening, sewer extension, or utility project within 1 mi announced or under construction | Value inflection incoming. Best to approach before the owner catches on. |

For each parcel with ≥ 3 mismatched use signals, generate a **proactive outreach recommendation** in the snapshot with:
- Estimated land value (based on comps)
- Owner contact research path (county records → secretary of state → skip trace)
- Suggested approach angle (e.g., "Estate-owned, approach trustee/executor, emphasize clean transaction")

---

## Strategy Fit Assessment Engine

After scoring a parcel, the agent evaluates which investment strategies the parcel is suited for. This is not a hard classification — a single parcel may fit multiple strategies. The agent assigns a fit rating (STRONG / MODERATE / WEAK / N/A) for each strategy based on the decision logic below.

### BTS Development

The parcel is suited for built-to-suit when a specific tenant demand signal can be matched to the site's characteristics.

| Fit | Conditions |
|-----|-----------|
| STRONG | By-right industrial zoning (S9 ≥ 7) + utilities at boundary (H8 pass, no extension) + identifiable tenant demand signal in submarket (expansion announcement, RFP, broker requirement) + parcel accommodates ≥ 150K SF footprint |
| MODERATE | Entitlement path is clear but not by-right (S9 ≥ 4) + utilities available within extension distance + general submarket demand is strong (S4 ≥ 6, S5 ≥ 7) but no specific tenant signal identified |
| WEAK | Rezoning required with uncertain outcome (S9 ≤ 3) OR utilities require major extension OR parcel geometry limits building efficiency |
| N/A | Parcel is too small for a single-user industrial building (<5 acres) OR market fundamentals don't support new construction |

**Data the agent should actively research for BTS fit:** SEC filings for expansion CapEx, tenant broker requirements on CoStar/Crexi, economic development authority prospect lists, trade publication announcements (e.g., Food Engineering, Logistics Management, Automotive News), local news about company expansions/relocations.

### Spec Development

The parcel is suited for speculative development when market fundamentals justify building without a pre-signed tenant.

| Fit | Conditions |
|-----|-----------|
| STRONG | Submarket vacancy < 5% (S4 ≥ 8) + positive net absorption > 1M SF T12 (S5 ≥ 7) + limited competing pipeline (S6 ≥ 7) + by-right or near-by-right entitlements (S9 ≥ 7) + land basis supports development yields at market rents |
| MODERATE | Vacancy 5–7% (S4 ≥ 6) + positive absorption + some pipeline but absorption outpaces it + entitlement path clear within 6 months |
| WEAK | Vacancy > 7% OR negative/flat absorption OR heavy pipeline that could depress rents below development feasibility |
| N/A | Market fundamentals clearly don't support new spec construction (vacancy > 10%, negative absorption, major pipeline overhang) |

**Key analysis the agent should perform for spec fit:** Back-of-envelope development feasibility — estimated all-in development cost (land + hard costs + soft costs) vs. achievable stabilized rent and resulting yield-on-cost. If yield-on-cost exceeds market cap rates by ≥ 100 bps, spec development is economically viable. Use submarket asking rents from market_context.json and rough industrial construction cost estimates ($85–$130/SF depending on specs and market).

### Land Bank

The parcel is suited for land banking when the location has strong long-term trajectory but the market hasn't fully arrived yet.

| Fit | Conditions |
|-----|-----------|
| STRONG | Parcel is on an emerging corridor (adjacent development activity, infrastructure approaching) + land basis is ≤ 50% of pricing in the nearest mature industrial submarket + entitlement risk is acceptable on a 3–5 year horizon + carry cost (taxes, insurance, maintenance) is manageable relative to expected appreciation |
| MODERATE | Corridor trajectory is plausible but less certain + land basis is below mature submarket pricing but discount is < 50% + some entitlement work may be needed during hold period |
| WEAK | Corridor maturation timeline is uncertain (5+ years) OR carry costs are high relative to likely appreciation OR significant entitlement/environmental risk that may not resolve during hold |
| N/A | Parcel is already priced at developed-market levels (no basis advantage to holding) OR market is already mature (better suited for immediate development) |

**Key analysis for land bank fit:** Compare $/acre to comparable parcels in the nearest established industrial submarket. Calculate annual carry cost (taxes + insurance + maintenance + opportunity cost of capital). Model implied appreciation rate needed to hit target return over 3-year and 5-year holds. Monitor infrastructure triggers (road projects, utility extensions, rezoning wave) that would signal when to exit the hold.

### Ground Lease

The parcel is suited for a ground lease structure when the location is strong enough that a developer or user would lease the land rather than require fee simple ownership.

| Fit | Conditions |
|-----|-----------|
| STRONG | Prime location (S1 ≥ 8, S4 ≥ 8) + by-right entitlements + large enough for institutional-quality development + land value justifies separating land and building ownership (high-value infill or constrained-supply submarket) |
| MODERATE | Good location + the team can acquire at a basis that supports ground lease rent yields of 5–7% + demand exists from developers looking to reduce basis |
| WEAK | Location or market fundamentals don't command ground lease premiums — developers would likely require fee simple |
| N/A | Rural or emerging corridor where ground lease structures are uncommon and developers have cheaper fee simple alternatives |

### Land Flip / Disposition

The parcel is suited for quick disposition when the team can acquire off-market at a discount to what developers would pay on the open market.

| Fit | Conditions |
|-----|-----------|
| STRONG | Off-market acquisition at ≥ 25% below recent comparable land sales + active developer demand in the submarket (identifiable buyers) + clean title and entitlements (or clear path) that a buyer would value + transaction can close and flip within 6–12 months |
| MODERATE | Off-market discount of 10–25% + developer demand exists but may require some entitlement work to maximize value before disposition |
| WEAK | Discount is marginal (<10%) OR limited buyer pool OR significant entitlement/environmental work needed before the parcel is marketable |
| N/A | Parcel is listed on-market at fair value — no basis advantage exists |

**Key analysis for flip fit:** Identify 3–5 active developers/buyers in the submarket (check recent land purchases on CoStar, broker announcements, development pipeline filings). Estimate acquisition basis vs. likely disposition price. Calculate gross margin after transaction costs. If net margin < 15%, the risk-adjusted return likely doesn't justify the flip.

### Multi-Parcel Assemblage (Cross-Parcel Analysis)

This strategy is unique because it requires analyzing the parcel in context with its neighbors. The agent should flag assemblage opportunities when:

- The target parcel alone is undersized for institutional development (< 10 acres) but 2–3 adjacent parcels under different ownership would create a qualifying site (15+ acres)
- Adjacent parcels share ownership characteristics that suggest potential willingness to sell (multiple absentee owners, estate/trust holders, long hold periods)
- The assembled site would score significantly higher than any individual parcel (e.g., combining a highway-frontage parcel with a deeper adjacent parcel creates a site with both access and depth)

When assemblage is identified, the snapshot should include a subsection listing each component parcel with its owner, acreage, estimated value, and the assembled site's combined score.

---

---

## Parcel Snapshot Format

For every qualified parcel (composite_score ≥ threshold), generate a one-page snapshot at `snapshots/{parcel_id}_snapshot.md`:

```markdown
# Site Snapshot: {address}
## {market} — {submarket} | {acreage} acres | Score: {composite_score}/100 | {ACTIONABLE / QUALIFIED — NOT ACTIONABLE}

### Investment Thesis
{2–4 paragraph narrative written in plain language explaining WHY this is a good development site. This is not a data dump — it is the agent's reasoned argument for why the team should spend time on this parcel. It should read like a brief you'd give a principal before a site visit. Cover:

- **The location story**: What makes this specific spot attractive for industrial use? What's the corridor trajectory? What's nearby that creates demand or validates the location? What infrastructure advantages exist (interchange proximity, utility capacity, rail, labor pool)?

- **The opportunity angle**: Why is this parcel available or undervalued? Is it a mismatched use play where the owner doesn't realize the industrial value? Is it an estate disposition? Is it priced below the submarket because of a solvable issue (rezoning needed but precedent exists)? What's the basis advantage relative to comparable developed sites?

- **The market timing**: Why now? Is the submarket tightening? Is infrastructure approaching that will unlock value? Is there a tenant demand signal that creates urgency? Or is this a patient land bank where the thesis is "buy at $X/acre today, corridor matures in 3 years, comparable sites will trade at $3X/acre"?

- **The risk and what mitigates it**: What's the main thing that could go wrong (rezoning uncertainty, environmental unknowns, access limitations) and why the agent believes it's manageable?

Be specific. Reference actual data points, comparable transactions, market stats, and named infrastructure projects. Do not use generic language like "strong market fundamentals" without backing it up with numbers. The thesis should make the team smarter about this site in 60 seconds of reading.}

### Location
- **Coordinates**: {lat}, {lng}
- **County**: {county}
- **Parcel ID**: {parcel_id}
- **Interstate access**: {nearest interchange, distance}
- **Nearest industrial cluster**: {name, distance}

### Physical Characteristics
- **Acreage**: {acreage}
- **Geometry**: {description — regular/irregular, dimensions}
- **Topography**: {flat/rolling/sloped, estimated grade change}
- **Frontage**: {road name, road classification, estimated frontage ft}

### Zoning & Entitlements
- **Current zoning**: {code — description}
- **Required action**: {by-right / CUP / rezoning}
- **Rezoning precedent**: {yes/no — cite nearby rezonings if yes}
- **Estimated entitlement timeline**: {months}

### Utilities
- **Water**: {available at boundary / extension needed — distance}
- **Sewer**: {available at boundary / extension needed — distance}
- **Electric (3-phase)**: {available / distance to nearest service}
- **Gas**: {available / distance}
- **Fiber**: {available / distance}

### Environmental
- **Flood zone**: {FEMA zone designation}
- **Wetlands**: {NWI status — none / minor fringe / significant}
- **EPA flags**: {none / details}

### Market Context
- **Submarket vacancy**: {%}
- **Submarket absorption (T12)**: {SF}
- **Competing pipeline (5 mi)**: {SF under construction}
- **Submarket asking rent (NNN)**: {$/SF}

### Ownership & Off-Market Signals
- **Owner**: {name}
- **Owner type**: {individual / trust / estate / LLC / corporate}
- **Owner location**: {local / absentee — mailing address}
- **Hold period**: {years since last transfer}
- **Listed**: {yes — broker/price / no}
- **Mismatched use signals**: {count — list each}
- **Estimated land value**: {$/acre based on comps}

### Development Potential
- **Estimated buildable area**: {SF footprint at 40% coverage ratio}
- **Estimated max building SF**: {based on FAR and coverage}
- **Suited for**: {big-box distribution / multi-tenant flex / manufacturing / cold storage}
- **Rail potential**: {yes/no — details}
- **Expansion potential**: {adjacent parcels available}
- **Assemblage opportunity**: {adjacent parcels with different owners that could create a larger site — list parcel IDs, owners, and combined acreage if applicable}

### Strategy Fit Assessment
| Strategy | Fit | Rationale |
|----------|-----|-----------|
| BTS Development | {STRONG / MODERATE / WEAK / N/A} | {1–2 sentence rationale} |
| Spec Development | {STRONG / MODERATE / WEAK / N/A} | {1–2 sentence rationale} |
| Land Bank | {STRONG / MODERATE / WEAK / N/A} | {1–2 sentence rationale} |
| Ground Lease | {STRONG / MODERATE / WEAK / N/A} | {1–2 sentence rationale} |
| Land Flip / Disposition | {STRONG / MODERATE / WEAK / N/A} | {1–2 sentence rationale} |

**Primary recommended strategy**: {strategy} — {1 sentence}

### Incentives
- **Opportunity Zone**: {yes/no}
- **State incentives**: {details}
- **Local abatements**: {details}
- **TIF district**: {yes/no}

### Score Breakdown
| Parameter | Sub-Score | Weight | Weighted |
|-----------|-----------|--------|----------|
| Interstate proximity | {x}/10 | 15 | {w} |
| Parcel geometry | {x}/10 | 10 | {w} |
| ... | ... | ... | ... |
| **Composite** | | | **{score}/100** |

### Actionability Assessment
| Gate | Status | Detail |
|------|--------|--------|
| Path to control | {PASS / FAIL} | {owner identified, contact path, willingness indicators — or specific blocker} |
| Path to entitlement | {PASS / FAIL} | {by-right / rezoning precedent exists / specific blocker} |
| Viable strategy with next step | {PASS / FAIL} | {strategy + specific next step — or why no strategy fits} |
| No deal-killers | {PASS / FAIL} | {clean / specific blocker identified} |

**Overall actionability**: {PASS / FAIL — if FAIL, state the primary blocker and what would need to change for it to become actionable}

### Flags / Open Items
- {list any data gaps, conflicts, or items needing human verification}

### Recommendation
{PURSUE / MONITOR / PASS — with 1–2 sentence rationale}
{If PURSUE: recommended strategy and immediate next step (e.g., "Land bank — basis is $9K/acre vs. $35K/acre for developed parcels 2 mi east. Approach trustee through county deed records. Corridor is 2–3 years from institutional interest.")}
{If MONITOR: what trigger would move this to PURSUE (e.g., "Rezoning application on adjacent parcel pending — if approved, entitlement path clears and this becomes a spec development candidate.")}
```

---

## Data Source Configuration — sources.json

```json
{
  "parcel_data": {
    "sources": [
      {
        "name": "County GIS / Assessor Portal",
        "tier": 1,
        "access": "web_scrape",
        "notes": "Primary source for parcel boundaries, acreage, ownership, assessed value, zoning. URL varies by county. Build connector per county."
      },
      {
        "name": "LandGlide / Regrid",
        "tier": 2,
        "access": "api",
        "notes": "Nationwide parcel data aggregator. Good for initial discovery. Cross-reference with county records for accuracy."
      },
      {
        "name": "CoStar Land",
        "tier": 2,
        "access": "api",
        "notes": "Listed land parcels. Requires CoStar credentials."
      }
    ]
  },
  "zoning": {
    "sources": [
      {
        "name": "County/Municipal Zoning Map",
        "tier": 1,
        "access": "web_gis",
        "notes": "Official zoning. Most counties have interactive GIS portals."
      },
      {
        "name": "Municipal Planning Department",
        "tier": 1,
        "access": "web_scrape",
        "notes": "Zoning ordinance text, permitted uses, rezoning application history."
      }
    ]
  },
  "environmental": {
    "sources": [
      {
        "name": "FEMA Flood Map Service Center",
        "tier": 1,
        "access": "web_api",
        "url": "https://msc.fema.gov/portal/search"
      },
      {
        "name": "EPA Envirofacts",
        "tier": 1,
        "access": "web_api",
        "url": "https://enviro.epa.gov/"
      },
      {
        "name": "USGS National Wetlands Inventory",
        "tier": 1,
        "access": "web_api",
        "url": "https://fwsprimary.wim.usgs.gov/wetlands/apps/wetlands-mapper/"
      },
      {
        "name": "State EPD / DEQ",
        "tier": 1,
        "access": "web_scrape",
        "notes": "State-level brownfield and contamination registries. GA: GEOS database."
      }
    ]
  },
  "topography": {
    "sources": [
      {
        "name": "USGS National Map / 3DEP",
        "tier": 1,
        "access": "web_api",
        "url": "https://apps.nationalmap.gov/",
        "notes": "LiDAR-derived elevation data. 1-meter resolution in many areas."
      },
      {
        "name": "Google Earth Pro",
        "tier": 2,
        "access": "manual",
        "notes": "Elevation profiles and terrain layer for quick visual assessment."
      }
    ]
  },
  "market_data": {
    "sources": [
      {
        "name": "CoStar",
        "tier": 2,
        "access": "api",
        "notes": "Submarket vacancy, absorption, pipeline, rent, comps. Primary market data source."
      },
      {
        "name": "Brokerage Quarterly Reports",
        "tier": 2,
        "access": "web_fetch",
        "urls": {
          "cushman": "https://www.cushmanwakefield.com/en/united-states/insights/industrial-marketbeat",
          "cbre": "https://www.cbre.com/insights/figures/industrial-figures",
          "jll": "https://www.us.jll.com/en/trends-and-insights/research",
          "colliers": "https://www.colliers.com/en-us/research"
        },
        "notes": "Free quarterly snapshots. Refresh every 90 days."
      },
      {
        "name": "BLS QCEW / LAUS",
        "tier": 1,
        "access": "api",
        "url": "https://api.bls.gov/publicAPI/v2/timeseries/data/",
        "notes": "Employment data by MSA and county. Use for labor pool scoring."
      },
      {
        "name": "Census OnTheMap / LODES",
        "tier": 1,
        "access": "web_api",
        "url": "https://onthemap.ces.census.gov/",
        "notes": "Worker home-work flow data. Use for 30-minute drive time labor shed estimates."
      }
    ]
  },
  "infrastructure": {
    "sources": [
      {
        "name": "State DOT Traffic Counts",
        "tier": 1,
        "access": "web_gis",
        "notes": "AADT data on adjacent roads. Higher counts = better truck access confirmation."
      },
      {
        "name": "Utility Provider Service Maps",
        "tier": 1,
        "access": "web_scrape",
        "notes": "Water, sewer, electric service area maps. Varies by municipality/utility."
      },
      {
        "name": "FCC Broadband Map",
        "tier": 1,
        "access": "web_api",
        "url": "https://broadbandmap.fcc.gov/",
        "notes": "Fiber and broadband availability by address."
      },
      {
        "name": "Class I Railroad Maps",
        "tier": 2,
        "access": "web",
        "notes": "BNSF, UP, CSX, NS system maps for rail adjacency scoring."
      }
    ]
  },
  "incentives": {
    "sources": [
      {
        "name": "Opportunity Zone Map",
        "tier": 1,
        "access": "web",
        "url": "https://opportunityzones.hud.gov/",
        "notes": "Census tract-level OZ designation."
      },
      {
        "name": "State Economic Development Agency",
        "tier": 1,
        "access": "web_scrape",
        "notes": "GA: Georgia Department of Economic Development. TX: Office of the Governor Economic Development. Etc."
      },
      {
        "name": "Municipal/County EDA",
        "tier": 1,
        "access": "web_scrape",
        "notes": "Local incentive programs, TIF districts, enterprise zones, abatement schedules."
      }
    ]
  },
  "ownership_research": {
    "sources": [
      {
        "name": "County Assessor / Tax Records",
        "tier": 1,
        "access": "web_scrape",
        "notes": "Owner name, mailing address, transfer history, assessed value, tax status."
      },
      {
        "name": "Secretary of State (LLC/Corp Lookup)",
        "tier": 1,
        "access": "web_scrape",
        "notes": "Registered agent and principal for LLC/corporate owners. GA: https://ecorp.sos.ga.gov/"
      },
      {
        "name": "County Deed Records / Clerk of Court",
        "tier": 1,
        "access": "web_scrape",
        "notes": "Transfer history, deed type, liens, encumbrances."
      }
    ]
  }
}
```

---

## Constraints

1. **Do not modify `program.md`** — this file is your instructions. The human iterates on it.
2. **Do not modify `prepare.py`** — it handles setup and is considered fixed infrastructure.
3. **Do not modify `parameters.json`** — scoring weights are human-tuned. Read only.
4. **Only modify `research.py`** — all logic changes (new source connectors, scoring improvements, discovery heuristics, filter refinements) go here.
5. **Never fabricate data** — if a data point is unavailable, leave it `null` and flag it. A missing score is infinitely better than a wrong score. Never estimate acreage, never guess zoning, never assume utilities exist.
6. **Respect source tiers** — county assessor data (Tier 1) always overrides CoStar or LandGlide (Tier 2) when they conflict on acreage, zoning, or ownership.
7. **Flag conflicts** — if two sources disagree on acreage by >5%, zoning classification, or flood zone designation, write to `flagged/` with both values and sources. Do not silently pick one.
8. **Time budget** — 10 minutes per parcel for scoring. 5 minutes per parcel for discovery (initial filter). If data sources are slow, score what you can and flag the rest.
9. **Git discipline** — commit after every scored parcel or discovery batch. Commit messages must include market, parcel_id, composite_score, actionability status, and current actionable_pipeline_count.
10. **Snapshot every qualifier** — every parcel that scores ≥ threshold gets a one-page snapshot. No exceptions.
11. **Off-market priority** — at least 30% of discovery cycles must use the mismatched use engine (county GIS searches for absentee owners, ag-adjacent-to-industrial, estates/trusts). Do not rely solely on listed inventory.
12. **Market context freshness** — refresh `market_context.json` for each market at least every 30 days. Stale vacancy/absorption data degrades scoring accuracy.

---

---

## Market Strategy Memo

After each run cycle (typically once per 24-hour period), the agent generates a **market strategy memo** for each market it researched. This memo sits at `rankings/{market_id}_strategy_memo.md` and explains the agent's approach, decisions, and learnings for that market. It is written for a principal-level reader who wants to understand the thought process behind the ranked pipeline, not just the results.

The memo is generated AFTER the ranking is finalized for the cycle. It is not a per-parcel document — it is a per-market reflection that contextualizes the entire pipeline.

### Memo Structure

```markdown
# {Market} Strategy Memo — {Date}

## This Cycle's Approach

{2–3 paragraphs explaining what the agent prioritized this cycle and why. Cover:
- Which corridors received the most discovery attention and what made them the priority (e.g., "Focused 60% of discovery effort on the I-85 South corridor in Clayton County this cycle because last week's run showed conversion rate of 72% there vs. 45% across other corridors, and recent absorption data from CBRE's Q1 report shows 1.8M SF of net positive absorption in the submarket.")
- What discovery methods produced the best results (standard ArcGIS query, mismatched-use engine, development authority inventories, satellite change detection)
- Any deliberate scope adjustments made this cycle (e.g., "Expanded the West Atlanta bounding box westward by 0.04 degrees after last cycle's results showed the prior box was missing the western half of Fulton Industrial Boulevard.")}

## Criteria Applied

{Document the actual criteria used this cycle, especially where they deviate from defaults:
- Acreage range, composite threshold, and scoring weights as configured for this market
- Any market-specific adjustments to hard filters or scored parameters (e.g., "Tightened S4 vacancy threshold to 4% for Atlanta because submarket fundamentals are tighter than the generic default; loosened S3 topography for Henry County because rolling terrain is the norm and grading cost is already priced into local land comps.")
- Any data source substitutions (e.g., "Used Cushman & Wakefield's Atlanta Industrial MarketBeat for vacancy data because CoStar credentials were unavailable this cycle; cross-referenced against JLL Atlanta Industrial Insight for confirmation.")}

## What I Learned This Cycle

{1–2 paragraphs of insights that emerged from the run. This is the agent's most valuable output — it's where pattern recognition across hundreds of parcels surfaces things a human reviewer wouldn't catch. Examples:
- "Parcels with absentee owners in the Atlanta market scored 18% higher on average than locally-owned parcels, primarily because the locally-owned parcels in the dataset tended to be active farming operations with homestead exemptions, which reduces both willingness-to-sell and basis advantage."
- "TIF district parcels in Cook County had a 23-point higher composite score than non-TIF parcels with comparable characteristics. Adding TIF district as a positive signal in S9 (entitlement complexity) is a candidate change for next cycle."
- "The 'rezoning velocity within 2-mile radius' signal correlates strongly with parcels tagged STRONG for land bank. Considering proposing this as a new scored parameter."}

## Pipeline Composition

{Brief summary of the actionable pipeline by strategy fit:
- Total actionable parcels: {N}
- By primary strategy: {breakdown — e.g., "8 BTS, 5 spec, 12 land bank, 2 flip"}
- By submarket: {distribution across corridors}
- Notable concentrations or gaps: {e.g., "Heavy concentration in South Fulton (14 parcels). Zero qualified parcels in DeKalb this cycle — dataset has been thoroughly screened, recommend deprioritizing DeKalb in next cycle's discovery effort and redirecting cycles to Cobb."}}

## What's in This Cycle's Pipeline — Top 10 Highlights

{Brief 2–3 sentence callouts on the top 10 parcels, grouped by strategy. Not full snapshots — just enough to give the reader a sense of the deal flow shape. Reference parcel IDs so the reader can pull the full snapshot if interested.}

## Open Questions and Recommended Human Decisions

{This section flags things the agent encountered that require human judgment to resolve:
- Data source decisions: "Daniel's Law in NJ has redacted owner names. Recommend authorizing NJPropertyRecords subscription ($X/month) to enable mismatched-use engine in NJ market, or deprioritize NJ until that's resolved."
- Strategy calibration: "Three parcels scored above 80 but were tagged WEAK for all five strategies because they're in submarkets with no clear demand thesis. Recommend human review — these may be land bank plays for a longer hold horizon than the current 5-year ceiling allows."
- Conflicting data: "Two sources disagree on zoning for parcel {ID} — county GIS shows AG-1, qPublic shows R-1. Flagged for review. Recommend confirming with planning department before further analysis."}

## Recommended Adjustments for Next Cycle

{Specific, actionable recommendations the agent has for the human to consider before the next cycle:
- Parameter tuning: "Lower composite threshold to 65 for Lehigh Valley — pipeline is thin and several 67–69 parcels look strong on qualitative review."
- Discovery focus: "Allocate 40% of next cycle's discovery to off-market mismatched-use rather than the current 30%; conversion rates are higher on off-market sourced parcels."
- Source additions: "Add Cobb County Development Authority's site inventory PDF as a recurring source — found 2 parcels through it this cycle that weren't in the standard ArcGIS query."
- Scope adjustments: "Consider expanding acreage upper bound to 75 acres for Houston market — port-proximate distribution sites are trending larger."}
```

### When to Generate the Memo

The memo is generated:
1. **After every full cycle completes** (default: once per 24-hour run)
2. **After a significant adjustment to `research.py`** that materially changes the agent's approach (so the human can review what changed and why)
3. **When the agent is manually halted** (final memo summarizing the partial run)

### Constraints on Memo Generation

- The memo MUST be specific. Generic statements like "good market fundamentals" or "strong pipeline" are not acceptable. Every claim must reference actual data, specific parcels, or concrete observations from the cycle.
- The memo MUST surface tradeoffs and open questions, not just successes. If the agent made a decision it's uncertain about, that goes in the memo.
- The memo MUST be honest about limitations. If a data source was unavailable, if a county connector failed, if the agent had to fall back to a degraded mode — those facts go in the memo, not buried in logs.
- The memo MUST be readable in 5 minutes by a busy principal. Density over length. No filler.

---

---

## Notes for Human Iterating on This File

This `program.md` is version 1.1. Areas to iterate on:

1. **Acreage range**: Currently 5–50 acres. Adjust based on your typical deal size. For big-box spec or BTS you may need 15–30 acres minimum at 40% coverage. For land banking on emerging corridors, you may want to include larger parcels (50–100+ acres) that can be subdivided or phased. For assemblage plays, the agent already considers smaller parcels in combination.

2. **Scoring weights**: The default weights reflect a balanced industrial land sourcing program. If your current pipeline is more weighted toward land banking (value plays on emerging corridors), bump S8 (land basis) and reduce S4/S5 (current market fundamentals matter less for a 5-year hold). If focused on spec development, bump S4/S5/S6 (vacancy, absorption, pipeline are everything).

3. **Composite threshold**: Default is 70/100. If you're getting too many qualifiers and want a tighter shortlist, raise to 75 or 80. If pipeline is thin, lower to 65.

4. **County connector buildout**: The biggest lift is building web scraper connectors for each target county's GIS/assessor portal. Start with your Tier 1 markets (Fulton, DeKalb, Clayton, Gwinnett for Atlanta; Dallas, Tarrant, Denton for DFW; Harris, Fort Bend for Houston; Will, DuPage, Cook for Chicago). Each county has a different portal format. See Appendix A for detailed per-county specs.

5. **Mismatched use signal weights**: Currently unweighted — all signals count equally. Consider weighting "absentee owner + estate/trust" higher than "long hold period" based on your outreach conversion experience.

6. **Strategy fit calibration**: The strategy fit thresholds in the Strategy Fit Assessment Engine are starting points. As your team reviews parcels and decides which strategies to pursue, feed that back into the fit criteria. If your team keeps pursuing land bank plays the agent rated as MODERATE, loosen the STRONG criteria for land bank.

7. **Development feasibility model**: The spec development fit assessment references a back-of-envelope feasibility check. As you refine this, consider adding a more detailed cost model with market-specific hard cost estimates, typical soft cost percentages, and target yield-on-cost thresholds by market.

8. **Tenant demand signal integration**: For BTS strategy fit, the agent needs to actively research tenant expansion signals. A future iteration could cross-reference against the BTS Tenant Scoring Model and Signal Priority Matrix you built — matching tenant demand signals to specific site characteristics (e.g., food/bev manufacturing needs heavy water/sewer capacity; pharma distribution needs airport proximity).

9. **Multi-parcel assemblage**: Current version flags assemblage opportunities when scoring individual parcels. Future versions could proactively search for assemblage candidates by querying adjacent parcels for every qualified site and evaluating the combined potential.

10. **Pipeline to diligence handoff**: When your team greenlights a site, the parcel snapshot data can seed the `input.json` for the acquisitions diligence agent, pre-populating address, county, parcel ID, acreage, zoning, and environmental baseline.

11. **Disposition buyer database**: For the land flip strategy, the agent would benefit from a maintained list of active developers/buyers by market with their known acquisition criteria (size range, budget, submarket preferences). This is a manual input the team can maintain.
