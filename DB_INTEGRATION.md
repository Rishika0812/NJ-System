# Automatic Database Loading (replaces Excel upload)

The Momentum system no longer asks you to upload `Data.xlsx` / `Dates.xlsx`.
On Streamlit startup it connects automatically to the same market-data store
the **S3 main system** builds, using the same `.env` file.

## How it works

1. **`.env`** at the project root holds the Mongo connection info:
   ```
   MONGO_URI="mongodb+srv://user:pass@cluster/..."
   MONGO_DB_NAME="smartbeta"
   MONGO_GRIDFS_BUCKET="duckdb_store"
   MONGO_DUCKDB_FILE="market_data.duckdb.gz"
   ```
   (`core/db_config.py` loads it — no `python-dotenv` dependency needed, same
   approach as `S3-main/core/config/providers_config.py`.)

2. **`core/db_provisioning.py`** — on open, if `storage/market_data.duckdb`
   isn't already on local disk, it's downloaded (and gunzipped) from MongoDB
   GridFS, exactly like `S3-main/core/data/storage/provisioning.py`, just
   triggered automatically instead of via a button.

3. **`core/db_loader.py`** — reads straight from the DuckDB `prices` table:
   - **Stock universe** (`load_all_stocks_from_db`): every ticker except the
     benchmark rows.
   - **NIFTY series** (`load_nifty_from_db`): the `NIFTY_500` row (falls back
     to `NIFTY_50`).
   - **Phase schedule** (`generate_phases_from_nifty`): the DB has no
     Rise/Fall phase schedule, so it's *generated* from the NIFTY series with
     a zig-zag swing detector — alternating peak/trough dates wherever the
     index has moved **≥ 6%** up or **≤ -6%** down from the last confirmed
     high/low (`DEFAULT_PHASE_THRESHOLD_PCT` in `core/db_loader.py`).

## Known differences from the old Excel input

- **Aux1 / Aux2 eligibility flags** don't exist in the DB (it only has plain
  OHLCV). They're defaulted to `Aux2=1` (always tradable) and `Aux1=0`
  (selection-gate off) for every row/date, so nothing is filtered out that
  wasn't already excluded by simple price availability.
- **Price field**: uses `adj_close` (falls back to `close` if null), matching
  the S3 main system's own fallback behaviour — so returns account for
  splits/dividends rather than using raw traded price.
- **Phase schedule** is computed, not stored — change
  `DEFAULT_PHASE_THRESHOLD_PCT` in `core/db_loader.py` if you want a
  different swing % than 6.

## Setup

- Put your `.env` file in the project root (already gitignored).
- First load downloads the DuckDB file (can be a few hundred MB) — the
  sidebar shows a "📥 Downloading market data…" status while this happens.
- Subsequent runs reuse the local copy in `storage/market_data.duckdb`
  automatically.
