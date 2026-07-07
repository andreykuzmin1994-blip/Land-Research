# Storage Architecture

> Why Postgres + PostGIS, what stays in markdown, and the schema design for the Land Site Selector agent.

---

## Storage Decisions

### Structured data → PostgreSQL with PostGIS extension

Parcel records, harness reports, scoring history, market context, ingested CoStar data, and all other structured records live in PostgreSQL. PostGIS adds geographic functions (bounding box queries, distance calculations, polygon containment) that the agent needs for corridor-based discovery and proximity scoring.

**Recommended host**: Supabase free tier for the initial Atlanta-only build. Migrate to Supabase Pro ($25/month) or self-hosted Postgres if and when storage or compute limits are hit.

**Why not file-based storage (Obsidian, JSON files, markdown frontmatter)**:
- The agent writes 50+ records per minute during overnight cycles — file I/O with git commits per record doesn't scale
- Spatial queries ("parcels within 2 miles of this corridor centroid") require spatial indexing
- Schema enforcement matters when the agent is autonomously generating records — a malformed record in a file silently breaks downstream processing
- Concurrent reads/writes during agent self-modification cycles need transactional guarantees

### Narrative / human-readable content → Markdown files on disk

Investment theses, parcel snapshots, and market strategy memos are written as markdown for human readability. These live on disk in the `snapshots/` and `rankings/` directories and are committed to git. They can optionally be surfaced through Obsidian if the firm uses Obsidian for institutional knowledge management — but the parcel data itself is NOT in Obsidian.

### Cached raw API responses → Local filesystem (or S3 if scaling)

The agent caches every raw API response and AI fallback extraction in `sources/{parcel_id}/{timestamp}_{source}.json` for audit trail and debugging. These are not loaded into Postgres because they're large, denormalized, and rarely queried.

### Configuration → JSON files in the repo

`parameters.json`, `sources.json`, and connector registry config live in the repo as JSON. These are human-tuned, version-controlled, and read by the agent at startup.

---

## Database Schema (Initial)

### Core Tables

**parcels**
The master table of every discovered parcel.
```sql
CREATE TABLE parcels (
    parcel_id TEXT PRIMARY KEY,        -- County-assigned parcel ID (with county prefix for global uniqueness)
    county TEXT NOT NULL,
    state TEXT NOT NULL,
    market TEXT NOT NULL,
    submarket TEXT,
    address TEXT,
    owner_name TEXT,
    owner_mailing_address TEXT,
    owner_type_inferred TEXT,           -- individual | trust | estate | llc | corporate | government | unknown
    acreage NUMERIC,
    land_sf NUMERIC,
    zoning TEXT,
    zoning_description TEXT,
    land_use_code TEXT,
    land_use_description TEXT,
    assessed_value_land BIGINT,
    assessed_value_improvement BIGINT,
    assessed_value_total BIGINT,
    fair_market_value BIGINT,
    tax_year SMALLINT,
    tax_amount NUMERIC,
    tax_status TEXT,
    last_sale_date DATE,
    last_sale_price BIGINT,
    deed_book_page TEXT,
    year_built SMALLINT,
    improvement_sf NUMERIC,
    geometry GEOMETRY(Polygon, 4326),   -- PostGIS polygon
    centroid GEOMETRY(Point, 4326),     -- PostGIS centroid (auto-computed)
    discovery_source TEXT,
    discovery_date DATE,
    last_updated TIMESTAMPTZ DEFAULT NOW(),
    raw_response_path TEXT
);

CREATE INDEX idx_parcels_county ON parcels(county);
CREATE INDEX idx_parcels_market ON parcels(market);
CREATE INDEX idx_parcels_acreage ON parcels(acreage);
CREATE INDEX idx_parcels_geometry ON parcels USING GIST(geometry);
CREATE INDEX idx_parcels_centroid ON parcels USING GIST(centroid);
CREATE INDEX idx_parcels_owner_state ON parcels((SUBSTRING(owner_mailing_address FROM '[A-Z]{2} \d{5}')));
```

**parcel_scores**
Versioned scoring history for each parcel. The agent re-scores parcels as new data becomes available, and prior scores are retained for trend analysis.
```sql
CREATE TABLE parcel_scores (
    score_id SERIAL PRIMARY KEY,
    parcel_id TEXT REFERENCES parcels(parcel_id),
    scored_at TIMESTAMPTZ DEFAULT NOW(),
    composite_score NUMERIC,
    confidence_score NUMERIC,
    actionability TEXT,                 -- PASS | FAIL:entitlement | FAIL:strategy | FAIL:deal_killer | PENDING
    actionability_blockers JSONB,
    sub_scores JSONB,                   -- {"S1": 8, "S2": 7, "S3": null, ...}
    strategy_fit JSONB,                 -- {"bts": "STRONG", "spec": "WEAK", "land_bank": "STRONG", ...}
    primary_strategy TEXT,
    investment_thesis TEXT,             -- The narrative writeup
    notes TEXT,
    run_tag TEXT,                       -- autoresearch/<tag> run this row belongs to (NULL = ad-hoc)
    experiment_id TEXT                  -- evaluate() invocation that wrote it (NULL = ad-hoc)
);

CREATE INDEX idx_scores_parcel ON parcel_scores(parcel_id);
CREATE INDEX idx_scores_actionability ON parcel_scores(actionability);
CREATE INDEX idx_scores_composite ON parcel_scores(composite_score);
CREATE INDEX idx_scores_run_parcel_scored_at ON parcel_scores(run_tag, parcel_id, scored_at DESC, score_id DESC);
```

Run/experiment attribution (prepare-mutation 2026-07-07): the metric in
`prepare.py` counts only rows whose `run_tag` matches the active
`autoresearch/<tag>` run, and `runner.py` deletes the rows of a
discarded/crashed/timed-out experiment by `experiment_id` — so `git reset`
(code revert) and the score purge (data revert) together make a discarded
experiment leave no trace in the next measurement. Rows written outside a
run (both columns NULL) are informational and never purge targets.

**markets**
Reference table for target markets and submarkets.
```sql
CREATE TABLE markets (
    market_id TEXT PRIMARY KEY,
    market_name TEXT NOT NULL,
    tier SMALLINT,                      -- 1 = primary, 2 = secondary
    state TEXT,
    notes TEXT
);

CREATE TABLE submarkets (
    submarket_id TEXT PRIMARY KEY,
    market_id TEXT REFERENCES markets(market_id),
    submarket_name TEXT NOT NULL,
    bbox GEOMETRY(Polygon, 4326),       -- Corridor bounding box
    notes TEXT
);
```

**market_context**
CoStar submarket stats and other market data, refreshed via CoStar ingestion.
```sql
CREATE TABLE market_context (
    context_id SERIAL PRIMARY KEY,
    submarket_id TEXT REFERENCES submarkets(submarket_id),
    as_of_date DATE,
    vacancy_rate_pct NUMERIC,
    availability_rate_pct NUMERIC,
    net_absorption_t12_sf BIGINT,
    under_construction_sf BIGINT,
    proposed_sf BIGINT,
    asking_rent_nnn_psf NUMERIC,
    source TEXT,                        -- 'costar' | 'cushman' | 'cbre' | 'jll' | etc.
    ingested_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_context_submarket_date ON market_context(submarket_id, as_of_date DESC);
```

**sales_comps**
CoStar land and building sale comps, refreshed monthly.
```sql
CREATE TABLE sales_comps (
    comp_id SERIAL PRIMARY KEY,
    address TEXT,
    parcel_id TEXT,
    county TEXT,
    submarket_id TEXT REFERENCES submarkets(submarket_id),
    comp_type TEXT,                     -- 'land' | 'building'
    acres NUMERIC,
    building_sf NUMERIC,
    sale_date DATE,
    sale_price BIGINT,
    price_per_acre NUMERIC,
    price_psf NUMERIC,
    cap_rate NUMERIC,
    buyer_name TEXT,
    seller_name TEXT,
    zoning TEXT,
    raw JSONB,                          -- Full original CoStar export row
    ingested_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_comps_submarket_date ON sales_comps(submarket_id, sale_date DESC);
CREATE INDEX idx_comps_type ON sales_comps(comp_type);
```

**leasing_comps**
CoStar industrial lease comps.
```sql
CREATE TABLE leasing_comps (
    lease_id SERIAL PRIMARY KEY,
    address TEXT,
    submarket_id TEXT REFERENCES submarkets(submarket_id),
    tenant_name TEXT,
    tenant_industry TEXT,
    naics_code TEXT,
    lease_start_date DATE,
    lease_term_months INTEGER,
    building_sf_leased NUMERIC,
    starting_rent_psf_nnn NUMERIC,
    rent_escalation_pct NUMERIC,
    lease_type TEXT,
    raw JSONB,
    ingested_at TIMESTAMPTZ DEFAULT NOW()
);
```

**land_listings**
On-market land listings from CoStar weekly export.
```sql
CREATE TABLE land_listings (
    listing_id SERIAL PRIMARY KEY,
    address TEXT,
    parcel_id TEXT,
    county TEXT,
    submarket_id TEXT REFERENCES submarkets(submarket_id),
    acres NUMERIC,
    zoning TEXT,
    asking_price BIGINT,
    asking_price_per_acre NUMERIC,
    listing_date DATE,
    days_on_market INTEGER,
    listing_broker TEXT,
    listing_broker_firm TEXT,
    utilities_status TEXT,
    entitlement_status TEXT,
    raw JSONB,
    snapshot_date DATE,                 -- Which weekly export this came from
    is_active BOOLEAN DEFAULT TRUE
);
```

**research_log**
Append-only log of every action the agent takes. The Karpathy-pattern equivalent of `results.tsv`.
```sql
CREATE TABLE research_log (
    log_id BIGSERIAL PRIMARY KEY,
    cycle_id TEXT,
    timestamp TIMESTAMPTZ DEFAULT NOW(),
    action_type TEXT,                   -- discovery | scoring | rescore | rejection | flag
    market TEXT,
    parcel_id TEXT,
    composite_score NUMERIC,
    actionability TEXT,
    strategy_fit TEXT,
    actionable_pipeline_count INTEGER,
    discovery_rate_24h NUMERIC,
    scoring_completeness NUMERIC,
    conversion_rate NUMERIC,
    notes TEXT
);

CREATE INDEX idx_log_cycle ON research_log(cycle_id);
CREATE INDEX idx_log_timestamp ON research_log(timestamp DESC);
```

**harness_reports**
Connector health reports from the test harness.
```sql
CREATE TABLE harness_reports (
    report_id SERIAL PRIMARY KEY,
    county TEXT,
    market TEXT,
    run_at TIMESTAMPTZ DEFAULT NOW(),
    overall_health TEXT,                -- healthy | degraded | failing | n/a
    checks JSONB,                       -- Full check results
    sample_features JSONB,
    warnings JSONB,
    errors JSONB
);

CREATE INDEX idx_harness_county_date ON harness_reports(county, run_at DESC);
```

**flagged_items**
Parcels or operations requiring human review.
```sql
CREATE TABLE flagged_items (
    flag_id SERIAL PRIMARY KEY,
    flagged_at TIMESTAMPTZ DEFAULT NOW(),
    flag_type TEXT,                     -- conflict | data_gap | actionability_block | other
    parcel_id TEXT,
    market TEXT,
    description TEXT,
    suggested_resolution TEXT,
    status TEXT DEFAULT 'open',         -- open | resolved | dismissed
    resolved_at TIMESTAMPTZ,
    resolved_by TEXT,
    resolution_notes TEXT
);
```

---

## Key Spatial Queries

PostGIS enables the queries that file-based storage cannot:

```sql
-- Find all parcels within a bounding box (corridor discovery)
SELECT * FROM parcels
WHERE ST_Intersects(geometry, ST_MakeEnvelope(-84.62, 33.52, -84.50, 33.58, 4326))
  AND acreage BETWEEN 5 AND 50;

-- Find parcels within 2 miles of an interstate interchange
SELECT * FROM parcels
WHERE ST_DWithin(centroid::geography, ST_Point(-84.55, 33.65)::geography, 3219);  -- 2 miles in meters

-- Find ag-zoned parcels adjacent to industrial-zoned parcels (mismatched-use signal)
SELECT a.* FROM parcels a
WHERE a.land_use_code IN ('AG-1', '100', '113')
  AND EXISTS (
    SELECT 1 FROM parcels b
    WHERE b.land_use_code LIKE 'M-%'
      AND ST_Touches(a.geometry, b.geometry)
  );

-- Find parcels with absentee out-of-state owners (mismatched-use signal)
SELECT * FROM parcels
WHERE owner_mailing_address NOT LIKE '%GA %'
  AND owner_mailing_address ~ '[A-Z]{2} \d{5}';
```

---

## Backup and Migration

Supabase free tier provides automated daily backups with 7-day retention. For the initial build this is sufficient.

If migrating off Supabase later:
- Standard `pg_dump` exports the entire database
- All schema is plain Postgres + PostGIS, no Supabase-specific features used in this design
- Migration target options: AWS RDS, self-hosted Postgres, GCP Cloud SQL, etc.

The agent's connection logic should read the database connection string from an environment variable (`DATABASE_URL`), so swapping hosts requires only updating the env var.

---

## Connection Pattern

The agent connects to Postgres using a standard psycopg2 or asyncpg connection. Connection string format:

```
postgresql://user:password@host:port/database
```

Stored in `.env` (gitignored), loaded via `python-dotenv`.

For Supabase free tier, the connection pooler endpoint should be used for the agent's autonomous loop (avoids connection limit issues during long-running cycles).
