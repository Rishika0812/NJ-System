# What changed in this delivery

## 1. DB connection — root cause found & fixed
`.env` uses a `mongodb+srv://` Atlas URI, which **requires the `dnspython`
package** to resolve the SRV DNS record. `pymongo` does not bundle it.
`requirements.txt` had `pymongo` but not `dnspython` — confirmed by diffing
against the S3 main system's own `requirements.txt`, which pins
`dnspython==2.8.0` right next to `pymongo==4.10.1`.

- **Fixed**: added `dnspython>=2.6.0` to `requirements.txt`.
- Added `test_connection()` / `gridfs_status()` diagnostics (ported from
  S3-main) to `core/db_provisioning.py`, and wired `test_connection()` into
  the sidebar so a bad URI, missing dnspython, or an Atlas IP-allow-list
  rejection is reported with the *actual* driver error instead of a generic
  failure.
- Wrapped the GridFS download in a clearer error pointing at the likely
  causes (Atlas Network Access allow-list is the other common one — add
  `0.0.0.0/0` there if deploying to Streamlit Cloud / any host with a
  non-static IP).

I could not live-test the Atlas connection from this sandbox (its network
egress doesn't reach `mongodb.net`), so please redeploy and check the
sidebar — it should now either connect or tell you exactly why not.

## 2. NIFTY 500 exclusivity
New `core/nifty500_universe.py`, bundling the official constituent list
(`data/nifty500_constituents.csv`, 500 names, same file the S3 main system
ships) and filtering the loaded universe against it. `core/db_loader.py`
now applies this filter; any non-constituent ticker that shows up in the
shared DB (a benchmark row, a different index, a delisted name) is dropped
and reported in the sidebar banner instead of silently entering rankings.

## 3. Gate System (Ranking Metric = "Off")
New `core/gate_system.py` — a straight port of the S3 main system's 3-gate
ARQM pipeline logic (`core/backtesting/gates.py` / `gate_registry.py` /
`normalization.py`), reading the **same precomputed tables** the main
system builds (`feature_store`, `fundamental_quality_features`) so scores
match S3-main exactly rather than being re-derived independently:

- **Momentum** — `momentum_unscaled` (ROC) only, weight 1.0
- **Stability / Low-Vol** — `beta` only, weight 1.0, lower-is-better
- **Quality** — unchanged from S3-main: 14 factors / 5 pillars
  (profitability 30 / growth 30 / financial_strength 15 / cash_flow 15 /
  shareholder_return 10), same min-thresholds, same median rollup
- **Blend** — momentum 0.40 / quality 0.40 / stability 0.20 (editable in
  the sidebar), selection top-30% per leg (editable)

Selecting **"Off (→ Gate System)"** in the Ranking Metric dropdown runs
this pipeline once per Rise/Fall leg entry date (using the same phase
schedule as everything else) instead of the single-metric ROC/Vol/Beta
ranking. Results land in a new **🚦 Gate System** tab: per-leg summary,
full per-ticker scorecard (momentum/stability/quality/pillar breakdown),
and CSV/Excel export (`export/gate_exporter.py`, kept separate from the
existing momentum exporter so it can't destabilize it).

**Scope note**: the Gate System is a ranking/selection pipeline, not a
priced trade simulator — it doesn't produce a buy/sell P&L ledger the way
the ROC/Vol/Beta path does. The Investment Analysis tab shows a pointer to
the Gate System tab when this mode is active rather than a misleading empty
P&L table.

## 4. NIFTY Trade Analysis
Added a section to the Overview tab: per-leg NIFTY index return (Rise vs.
Fall), leg counts/averages, and the NIFTY-500-exclusive universe badge.

## 5. Requires the DB to actually have the new tables
`feature_store` (beta, momentum_unscaled) and `fundamental_quality_features`
must exist in the shared DuckDB for the Gate System to have anything to
score. If your current DB snapshot predates the S3 main system's feature
engineering / fundamentals pipelines, the Gate System tab will say so
explicitly rather than silently returning empty results.

## What I could not verify in this sandbox
No live Mongo/Atlas access and no real market-data DuckDB file here, so:
`core/gate_system.py`'s query/scoring logic was unit-tested against a
synthetic DuckDB with the right schema (passes), but not against your real
data. Please smoke-test the Gate System tab after deploying, and share any
traceback if something doesn't match.
