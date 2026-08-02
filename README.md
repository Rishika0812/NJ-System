# S³ Momentum Investment System

**S³** (Simple, Systematic, Stock Selection) — A Momentum-Based Backtesting Platform for the Indian NIFTY 500 Market

---

## Project Overview

### What It Is

S³ Momentum is a web-based quantitative investment research platform that enables users to backtest momentum-driven stock selection strategies on the NIFTY 500 index. The system automates the entire workflow from market data ingestion to performance analysis and Excel export.

### What Problem It Solves

Traditional momentum investing often lacks systematic validation. S³ Momentum solves this by:

- **Automating data ingestion** from MongoDB (no manual Excel uploads)
- **Detecting market cycles** automatically using zig-zag swing detection
- **Testing multiple ranking metrics** in a single interface (11 methodologies)
- **Providing comprehensive risk-adjusted performance metrics**
- **Exporting interactive Excel reports** for offline analysis
- **Supporting professional-grade ARQM 3-Gate quality pipeline** (Momentum → Stability → Quality)

### Who Benefits

- **Quantitative researchers** testing momentum strategies on Indian equities
- **Portfolio managers** evaluating systematic stock selection approaches
- **Data scientists** exploring factor-based investment models
- **Retail investors** wanting to understand backtested momentum performance

### Real-World Applications

- Validate momentum strategy performance on Indian equities
- Compare ROC, volatility, beta-based stock ranking approaches
- Test NIFTY threshold entry/exit logic
- Generate production-ready Excel reports for client presentations
- Research the ARQM 3-gate pipeline (Momentum → Stability → Quality)
- Analyze per-cycle performance with quartile breakdowns
- Debug selection logic via calculation audit trails

---

## Business Context & Motivation

### The Challenge

Momentum strategies require careful calibration of:
- Entry/exit triggers (NIFTY rise/fall percentages)
- Stock selection methodology (ROC, volatility, beta, quality)
- Position sizing and risk management

### Traditional Approaches

| Approach | Limitation |
|----------|------------|
| Manual Excel uploads | Error-prone, time-consuming, no automation |
| Single metric focus | Ignores multi-factor interactions |
| No cycle detection | Requires manual phase scheduling |
| Limited exports | No comprehensive reports |
| No quality screening | No fundamental health checks |

### How S³ Addresses These

1. **Automated Data Pipeline**: Downloads market data from MongoDB GridFS to local DuckDB
2. **Multi-Metric Ranking**: 11 distinct ranking methodologies in one interface
3. **Automatic Cycle Detection**: Zig-zag algorithm identifies Rise/Fall phases
4. **Comprehensive Export**: Interactive Excel with 15+ sheets, dashboard, and drill-down capabilities
5. **Professional Quality Pipeline**: ARQM 3-Gate system with 14 factors across 5 pillars

---

## Key Features

### 1. Automated Data Ingestion
- **What it does**: Downloads market data from MongoDB GridFS to local DuckDB
- **Why it matters**: Eliminates manual Excel uploads; ensures data consistency with the main S3 system
- **How it works**: Uses `db_provisioning.py` to download `.duckdb.gz` from GridFS, decompresses locally
- **Data freshness**: Auto-detects stale data and re-downloads on demand

### 2. Automatic Phase Scheduling
- **What it does**: Detects Rise/Fall market cycles using configurable swing threshold (default 6%)
- **Why it matters**: No manual date ranges; adapts to actual market conditions
- **How it works**: Zig-zag algorithm identifies peaks/troughs where NIFTY moves ≥ threshold%

### 3. Multi-Metric Stock Ranking (11 Methodologies)
- **What it does**: Ranks NIFTY 500 stocks using 11 different factors per leg window
- **Why it matters**: Test various momentum/volatility/quality combinations
- **Available Metrics**:
  1. **ROC** — Pure price momentum
  2. **Volatility** — Annualised standard deviation (standard or downside)
  3. **ROC × Vol** — Momentum-risk product
  4. **ROC / Vol** — Sharpe-style ranking
  5. **Beta** — OLS beta vs NIFTY (ln returns, base-day prepended)
  6. **Beta / Vol** — Risk-adjusted beta
  7. **Beta × Vol** — Combined market exposure + risk
  8. **Std Dev / Downside Vol** — σ ÷ DV ratio
  9. **Both (ROC ∩ Vol)** — Intersection of top-N
  10. **Off (Volatility Filter Only)** — Pure volatility screening
  11. **Gate (ARQM)** — 3-gate sequential filtration

### 4. Common-Stock Selection
- **What it does**: Finds stocks appearing in top-N across ALL legs
- **Why it matters**: Reduces false positives; ensures robust selection
- **How it works**: Set intersection of top-N candidates per leg → Top-K final basket

### 5. NIFTY Threshold Entry/Exit
- **What it does**: Buys when NIFTY rises X%, sells when NIFTY falls Y% from running peak
- **Why it matters**: Systematic risk management; avoids market timing
- **How it works**: Trailing peak exit with configurable fall percentage
- **Exact Trigger Mode**: Separates SELL (fall confirm) and BUY (recovery) dates

### 6. ARQM Gate System (Quality Pipeline) — **Core Differentiator**
- **What it does**: 3-stage sequential filtration: Momentum → Stability (beta) → Quality (14 factors)
- **Why it matters**: Professional-grade stock screening matching S3-main system exactly
- **Pipeline Stages**:
  1. **Momentum Gate** — ROC unscaled, z-scored → minmax (0..1)
  2. **Stability Gate** — Beta only, lower-is-better → minmax (0..1)
  3. **Quality Gate** — 14 factors across 5 pillars, weighted z-scores → minmax per pillar → blended

- **Quality Pillars & Weights**:
  | Pillar | Weight | Factors (14 total) |
  |--------|--------|-------------------|
  | Profitability | 30% | ROE, ROCE, ROA, Cash ROCE (with min thresholds) |
  | Growth | 30% | EPS Growth, Revenue Growth, ROE Growth, ROCE Growth, **Sustainable Growth Rate** |
  | Financial Strength | 15% | Interest Coverage Ratio, Equity/Total Capital |
  | Cash Flow | 15% | OCF/EBITDA |
  | Shareholder Return | 10% | Dividend Payout Ratio, Cumulative Payout Ratio |

- **Key Fixes Applied**:
  - Removed `dps_growth_weighted` (DPS Growth Weighted) — column missing from fundamental data
  - Rebalanced Growth pillar across remaining 5 factors (proportional weights)
  - Fixed View · Candidates sheet to propagate gate scores (momentum/stability/quality + passed flags)
  - Fixed minmax edge-case handling for cycles with insufficient growth factor variance

### 7. Investment Analysis
- **What it does**: Computes CAGR, max drawdown, Sharpe, win rate, equity curve, Calmar ratio
- **Why it matters**: Professional performance attribution
- **How it works**: Equal-weight portfolio simulation with optional reinvestment

### 8. Interactive Excel Export (15+ Sheets)
- **What it does**: Generates comprehensive Excel workbook with dashboard, drill-down, and formulas
- **Sheets**:
  1. **Dashboard** — Cycle selector, auto-updating View sheets
  2. **Portfolio Summary** — Config + headline metrics + window outcomes
  3. **Cycle Ledger** — Buy → peak → fall → sell timeline with quartile breakdown
  4. **Cycle Status** — Every window: traded / skipped with reason
  5. **Cycle Candidates** — Per-cycle common stocks with gate scores
  6. **Eligible Stock Ranking** — All eligible stocks with quartiles (Q4=top 25%)
  7. **Cycle Leg Rankings** — Per-leg Top-N with scores, pillars, passed flags
  8. **Per-Cycle Equity** — Capital, return, profit, running equity
  9. **Common Stocks P&L** — Per-stock profit and alpha
  10. **Trade Log** — Every executed trade with dates/prices
  11. **Stock Summary** — Per-ticker aggregated statistics
  12. **NIFTY Cycle Path** — Day-by-day NIFTY with markers
  13. **Yearwise Summary** — Calendar year + period breakdown with quartiles
  14. **Phase Schedule** — Rise/Fall phase dates
  15. **Portfolio NAV** — Rebalance-by-rebalance equity curve
  16. **Portfolio Stats** — B3 metrics + equity curve chart
  17. **Year-wise Analysis** — Annual profit/equity combo chart
  18. **Trade P&L** — Enriched per-trade rows with ₹ allocations
  19. **Window Status** — Reshuffle reasons + trade counts
  20. **Window Candidates** — Per-window common stocks with performance
  21. **Window Stock Ranks** — Full ranking every window (BUY highlighted)
  22. **Common Stocks P&L** — Entry/exit with trade-phase quartile
  23. **Trade Phase Rankings** — ALL eligible stocks ranked by actual buy→sell return
  24. **Calculation Audit** — Every (cycle, stock, leg) decision with reason
  25. **100-Day Volatility Audit** — Vol window verification
  26. **Per-Cycle Detail** — Linked from Cycle Ledger (one sheet per cycle)

### 9. Calculation Audit Trail
- **What it does**: Records every ranking decision with reason codes
- **Why it matters**: Full transparency, debuggability, compliance
- **Columns**: Qualified status, in-leg-top-N, selection reason, metric scores

---

## End-to-End Workflow

```mermaid
flowchart TD
    A[MongoDB\nMarket Data] -->|GridFS Download| B[Local DuckDB]
    B --> C[Data Loading\nNIFTY + 500 Stocks]
    C --> D[Phase Detection\nZig-zag 6% Swing]
    D --> E[Pattern Windows\nRise-Fall Cycles]
    E --> F[Stock Ranking\nPer Leg\nROC/Vol/Beta/Gate]
    F --> G[Common Selection\nIntersection]
    G --> H[NIFTY Entry Trigger\n+X% Rise]
    H --> I[Stock Execution\nBuy at Trigger]
    I --> J[NIFTY Exit Trigger\n-Y% Fall from Peak]
    J --> K[Portfolio Analysis\nEquity Curve, Metrics]
    K --> L[Export to Excel\nInteractive Workbook]
```

### Data Flow Details

| Stage | Input | Processing | Output |
|-------|-------|------------|--------|
| **Data Load** | `prices` table (MongoDB) | Load OHLCV into DuckDB | `nifty_df`, `stock_dict` |
| **Phase Detection** | NIFTY daily closes | Zig-zag swing detection (6% threshold) | `phases` DataFrame |
| **Phase Returns** | Stock prices + phases | Compute entry/exit returns | `returns_df` |
| **Stock Ranking** | Returns + metrics config | Rank by ROC/Vol/Beta/Gate | `leg_rank_df` |
| **Selection** | Leg rankings | Top-N intersection per window | `candidates_df` |
| **Execution** | Candidates + NIFTY | Apply threshold triggers | `per_trade_df` |
| **Analysis** | Trade data | Compute metrics | `investment_analysis` |

---

## System Architecture

### High-Level Architecture

```mermaid
graph TB
    subgraph "UI Layer"
        A[Streamlit App\napp.py]
    end
    
    subgraph "Core Engine"
        B[momentum_engine.py\nMain backtesting logic]
        C[multi_leg_engine.py\nPattern window finder]
        D[phase_engine.py\nPhase return computation]
        E[investment_analysis.py\nPortfolio metrics]
    end
    
    subgraph "Data Layer"
        F[db_provisioning.py\nMongoDB → DuckDB]
        G[db_loader.py\nDuckDB queries]
        H[db_config.py\nConfiguration]
        I[nifty500_universe.py\nUniverse filtering]
    end
    
    subgraph "Quality Pipeline"
        J[gate_system.py\nARQM 3-gate]
    end
    
    subgraph "Export Layer"
        K[momentum_exporter.py\nExcel generation]
        L[momentum_interactive.py\nInteractive Excel]
    end
    
    A --> B
    A --> C
    B --> D
    B --> E
    B --> J
    C --> D
    D --> B
    E --> K
    E --> L
    F --> G
    G --> B
    G --> D
    G --> J
    H --> F
    I --> G
    J --> B
```

### Component Breakdown

| Module | Responsibility | Key Functions |
|--------|----------------|---------------|
| **app.py** | Streamlit UI, orchestration | `run_momentum()`, sidebar controls, 8 tabs |
| **momentum_engine.py** | Core ranking logic | `_compute_beta()`, `_leg_roc_detail()`, `run_momentum()` |
| **multi_leg_engine.py** | Pattern windows, trading | `find_pattern_windows()`, `compute_nifty_threshold_trades()` |
| **phase_engine.py** | Phase detection, returns | `generate_phases_from_nifty()`, `compute_all_phase_returns()` |
| **investment_analysis.py** | Portfolio metrics | `compute_investment_analysis()`, `_compute_metrics()` |
| **db_provisioning.py** | Data download | `ensure_database()`, `test_connection()` |
| **db_loader.py** | DB queries | `load_all_from_db()`, `load_nifty_from_db()` |
| **gate_system.py** | Quality pipeline | `rank_universe()`, `momentum_gate()`, `quality_gate()` |
| **momentum_exporter.py** | Excel export (static) | `generate_momentum_excel()` |
| **momentum_interactive.py** | Excel export (interactive) | `generate_momentum_interactive_excel()` |

---

## Technology Stack

| Layer | Technology | Version |
|-------|------------|---------|
| **Frontend** | Streamlit | ≥ 1.32.0 |
| **Data Processing** | Pandas | ≥ 2.0.0 |
| **Numerical** | NumPy | ≥ 1.24.0 |
| **Visualization** | Plotly | ≥ 5.18.0 |
| **Database** | DuckDB | ≥ 1.5.0 |
| **MongoDB Driver** | PyMongo | ≥ 4.10.0 |
| **Excel Export** | openpyxl, xlsxwriter | ≥ 3.1.0 |
| **DNS Resolution** | dnspython | ≥ 2.6.0 |
| **Machine Learning** | XGBoost | ≥ 2.0.0 |

---

## Setup & Installation

### Prerequisites

- Python 3.10+
- MongoDB Atlas account with market data
- NIFTY 500 constituents file (bundled in `data/`)

### Installation

```bash
# Clone the repository
git clone <repository-url>
cd asset_class_selection_system_version_2

# Create virtual environment
python -m venv venv
.\venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Configure MongoDB credentials
echo "MONGO_URI=mongodb+srv://user:pass@cluster.mongodb.net" > .env
echo "MONGO_DB_NAME=smartbeta" >> .env
echo "MONGO_GRIDFS_BUCKET=duckdb_store" >> .env
echo "MONGO_DUCKDB_FILE=market_data.duckdb.gz" >> .env
```

### Running the Application

```bash
streamlit run app.py
```

Opens at `http://localhost:8501`

---

## Configuration

### Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `MONGO_URI` | Yes | — | MongoDB Atlas connection string |
| `MONGO_DB_NAME` | No | `smartbeta` | Database name |
| `MONGO_GRIDFS_BUCKET` | No | `duckdb_store` | GridFS bucket name |
| `MONGO_DUCKDB_FILE` | No | `market_data.duckdb.gz` | Blob filename |

### Sidebar Controls

| Parameter | Range | Default | Description |
|-----------|-------|---------|-------------|
| **Start Date** | Phase range | Min date | Backtest start |
| **End Date** | Phase range | Max date | Backtest end |
| **Buy on Start** | Boolean | ✓ | Buy first basket on start date |
| **Top-N** | 1-2000 | 50 | Stocks per leg |
| **K** | 1-100 | 10 | Final basket size |
| **NIFTY Rise %** | 0.5-20% | 5% | Buy trigger threshold |
| **Fall %** | 1-40% | 10% | Sell trigger threshold |
| **Hold Max** | 0.5-5yr | 2yr | Maximum holding period |
| **Exact Trigger Mode** | Boolean | Off | Separate SELL/BUY dates |

---

## Ranking Metrics Reference

| Metric | Formula | Best For |
|--------|---------|----------|
| **ROC** | `(P_exit - P_entry) / P_entry × 100` | Pure momentum |
| **Volatility** | `std(ln returns) × √252` | Risk control |
| **ROC × Vol** | `ROC × Volatility` | Momentum × risk |
| **ROC / Vol** | `ROC / Volatility` | Sharpe-style ranking |
| **Beta** | `Cov(R_stock, R_NIFTY) / Var(R_NIFTY)` | Market correlation |
| **Beta / Vol** | `Beta / Volatility` | Risk-adjusted beta |
| **Beta × Vol** | `Beta × Volatility` | Combined exposure |
| **Std Dev / Downside Vol** | `σ ÷ DV` | Downside risk focus |
| **Gate** | ARQM pipeline score | Professional screening |

---

## Data Sources

### Primary: MongoDB GridFS (Production)

The system expects a pre-built DuckDB database stored in MongoDB GridFS containing:

| Table | Content |
|-------|---------|
| `prices` | Daily OHLCV for NIFTY 500 constituents |
| `fundamentals_company` | Market cap data for cap distribution |
| `fundamental_quality_features` | 14 quality factors per ticker per financial year (raw, _median, _weighted) |
| `feature_store` | Daily beta, momentum_unscaled, momentum_scaled, semi_deviation |

### Secondary: Bundled Constituents

- `data/nifty500_constituents.csv` — Official NIFTY 500 list for universe filtering

### Third-Party Data Scrapers (Development / Augmentation)

For local development or data augmentation, the system supports three external data sources via scraper APIs:

| Source | API / Method | Data Type | Use Case |
|--------|--------------|-----------|----------|
| **Yahoo Finance** | `yfinance` Python library | OHLCV, dividends, splits | Price data for missing tickers, dividend history |
| **Screener.in** | Web scraping / API wrapper | Financial statements, ratios, quality metrics | Fundamental data augmentation, quality factor computation |
| **NSE India** | Official NSE API / `nsepython` | Index constituents, corporate actions, bhavcopy | Universe updates, symbol mapping |

#### Integration Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    S³ Momentum System                        │
├─────────────────────────────────────────────────────────────┤
│  MongoDB GridFS (Production)                                │
│  ┌─────────────┐  ┌──────────────────┐  ┌────────────────┐  │
│  │  prices     │  │ fundamental_     │  │ feature_store  │  │
│  │  (OHLCV)    │  │ quality_features │  │ (beta, mom)    │  │
│  └─────────────┘  └──────────────────┘  └────────────────┘  │
└─────────────────────────────────────────────────────────────┘
                          ▲
                          │ Data Pipeline (ETL)
        ┌─────────────────┼─────────────────┐
        ▼                 ▼                 ▼
   ┌─────────┐       ┌──────────┐     ┌─────────┐
   │ Yahoo   │       │ Screener │     │ NSE     │
   │ Finance │       │ .in      │     │ India   │
   │ (yfin)  │       │ (scraper)│     │ (API)   │
   └─────────┘       └──────────┘     └─────────┘
   Price Data    Fundamental Data    Universe/
   Dividends     Ratios/Quality      Corporate
   Splits        Factors             Actions
```

#### When to Use External Scrapers

| Scenario | Source | Command |
|----------|--------|---------|
| Missing price data for new tickers | Yahoo Finance | `python -m extra_features.data_extractor --source yfinance` |
| Quality factor backfill | Screener.in | `python -m extra_features.data_extractor --source screener` |
| Update NIFTY 500 constituents | NSE India | `python -m core.nifty500_universe --update` |

> **Note**: External scrapers are for development/augmentation only. Production runs use the MongoDB-backed DuckDB which is built by the main S3 system's feature engineering pipeline.

---

## Output Formats

### Excel Workbook Sheets (Interactive Export)

| Sheet | Description | Key Features |
|-------|-------------|--------------|
| **Dashboard** | Cycle selector + summary | Auto-filtering View sheets |
| **Portfolio Summary** | Config + metrics + outcomes | Window status, hit rates |
| **Cycle Ledger** | Timeline per cycle | Peak gain, max DD, recovery, Q1-Q4 |
| **Cycle Status** | Every window outcome | Traded/skipped/reshuffled + reasons |
| **Cycle Candidates** | Common stocks per cycle | Gate scores, passed flags, pillar breakdown |
| **Eligible Stock Ranking** | All eligible with quartiles | Q4=top 25%, traded highlight |
| **Cycle Leg Rankings** | Per-leg Top-N with scores | Gate scores, pillars, passed flags |
| **Per-Cycle Equity** | Capital allocation per window | Running equity, profit, α |
| **Common Stocks P&L** | Per-trade with trade-phase quartile | Entry/exit, α, quartile |
| **Trade Log** | Every executed trade | Dates, prices, returns, status |
| **Stock Summary** | Per-ticker aggregated stats | Win rate, avg α, max DD |
| **NIFTY Cycle Path** | Day-by-day NIFTY with markers | BUY/PEAK/FALL/SELL labels |
| **Yearwise Summary** | Calendar year + period breakdown | Quartile counts, Q3+Q4% |
| **Phase Schedule** | All Rise/Fall phases | Dates, days, trade type |
| **Portfolio NAV** | Rebalance-by-rebalance equity | NAV, return, drawdown |
| **Portfolio Stats** | B3 metrics + equity chart | CAGR, Sharpe, Calmar, MDD |
| **Year-wise Analysis** | Annual profit + equity combo chart | Bar + line chart |
| **Trade P&L** | Enriched per-trade with ₹ | Allocations, cumulative equity |
| **Window Status** | Reshuffle reasons + counts | Fall DD%, status, notes |
| **Window Candidates** | Per-window common stocks | Mean α, mean return |
| **Window Stock Ranks** | Full ranking per window | BUY highlighted in green |
| **Trade Phase Rankings** | ALL eligible by actual return | Q4=top 25%, traded ✓ |
| **Calculation Audit** | Every (cycle, stock, leg) decision | Reason codes, scores |
| **100-Day Vol Audit** | Vol window verification | Days, start/end dates |
| **Cycle N Detail** | Per-cycle deep dive | Linked from Ledger |

### Static Export (momentum_exporter.py)

Simplified 14-sheet workbook without interactive formulas for quick sharing.

---

## Advanced Features

### Reshuffle Threshold
- **What it does**: Skips windows where NIFTY falls more than X% during Fall leg
- **Configuration**: `reshuffle_threshold_pct` (e.g., 10%)
- **Output**: Recorded in Cycle Status with reason "Reshuffled: Fall DD > threshold"

### Market Cap Distribution
- **What it does**: Enforces Large/Mid/Small cap allocation in final basket
- **Default**: 50% Large (≥ rank 100), 30% Mid (101-250), 20% Small (251+)
- **Source**: `fundamentals_company.market_cap` in DuckDB

### Fixed Fall Entry/Exit Windows
- **What it does**: Uses trailing windows anchored to previous cycle's peak/trough
- **Config**: `fall_entry_days`, `fall_exit_days` (e.g., 252 trading days)
- **Use case**: Consistent lookback regardless of cycle length

### Exact Trigger Mode
- **What it does**: Separates SELL date (fall confirm) from next BUY date (recovery)
- **Default**: Off (SELL and next BUY on same recovery date)
- **Output**: Distinct sell_date_cycle vs buy_trigger_date

### Persistence / Run History
- **What it does**: Saves backtest config, trades, cycles, metrics to `runs/<run_id>/`
- **Files**: `config.json`, `trades.parquet`, `cycles.parquet`, `metrics.json`, `excel_report.xlsx`
- **UI**: Previous Runs tab to load/restore any historical run

---

## Tab-by-Tab UI Guide

### 1. Overview
- Strategy configuration summary
- Phase schedule with color-coded Rise/Fall
- NIFTY Trade Analysis (leg-level NIFTY returns)

### 2. Cycles & Trades
- Cycle Ledger with avg return highlighting
- KPI cards: completed cycles, total trades, avg return, win rate
- Detailed per-trade table

### 3. Investment Analysis
- Equity curve chart (interactive Plotly)
- B3 metrics: CAGR, MDD, Calmar, Sharpe, win rate
- Year-wise performance table + combo chart
- Portfolio NAV table

### 4. NIFTY Cycle View
- Per-cycle NIFTY path with BUY/PEAK/FALL/SELL markers
- Drawdown from peak visualization
- Cycle statistics sidebar

### 5. Candidates
- Cycle Candidates table (common ranking)
- Per-cycle filter dropdown
- Gate scores visible when metric="Gate"

### 6. Leg Rankings
- Per-leg Top-N with all scores
- Pillar breakdown for Gate metric
- Beta, volatility, ROC columns

### 7. Export
- Synchronous (blocks UI) / Asynchronous (background)
- Descriptive filename with settings
- Download button when ready

### 8. Previous Runs
- List all saved backtests with metadata
- Load results + Excel without re-running
- Delete individual or all runs

---

## License

Internal research tool. Not for redistribution.

---

## Acknowledgments

Built for the S³ Momentum Investment System, leveraging the same data store and factor pipeline as the S3 main system.

**Key Contributors**: 
- Core momentum engine architecture
- ARQM 3-Gate quality pipeline (S3-main parity)
- Interactive Excel export with dynamic formulas
- MongoDB GridFS data provisioning
- Investment analysis with B3 metrics

---

## Appendix: Gate System Configuration Details

### Default GateParams (from `gate_system.py`)

```python
# Pipeline Controls
enable_momentum: True
enable_stability: True
enable_quality: True

# Momentum Gate
momentum_column: "momentum_unscaled"
momentum_normalization: "zscore"
momentum_selection: "top_pct"
momentum_top_pct: 0.30
momentum_top_n: 50

# Stability Gate
stability_column: "beta"
stability_normalization: "zscore"
stability_selection: "top_pct"
stability_top_pct: 0.50
stability_top_n: 50

# Quality Gate
quality_factors: 14 factors (see table above)
quality_pillar_weights: {profitability: 0.30, growth: 0.30, 
                         financial_strength: 0.15, cash_flow: 0.15, 
                         shareholder_return: 0.10}
quality_normalization: "zscore"
quality_rollup: "median"
min_quality_score: 0.0

# Market Cap Distribution
enable_cap_filter: True
large_cap_pct: 0.50
mid_cap_pct: 0.30
small_cap_pct: 0.20
```

### Quality Factor Thresholds (Knock-out Filters)

| Factor | Min Threshold | Pillar |
|--------|---------------|--------|
| ROE | 12% | Profitability |
| ROCE | 12% | Profitability |
| ROA | 8% | Profitability |
| Cash ROCE | 10% | Profitability |
| Interest Coverage Ratio | 1.5x | Financial Strength |
| Equity/Total Capital | 40% | Financial Strength |
| OCF/EBITDA | 10% | Cash Flow |

Factors without threshold: EPS Growth, Revenue Growth, ROE Growth, ROCE Growth, Sustainable Growth Rate, Dividend Payout Ratio, Cumulative Payout Ratio

---

## Appendix: Fixed Growth Pillar Issue

**Problem**: `dps_growth_weighted` (DPS Growth Weighted) column missing from `fundamental_quality_features` table.

**Impact**: Growth pillar received only 5/6 factors → weight rebalancing → minmax normalization failed for cycles with low factor variance (all 0 or NaN).

**Fix Applied** (`momentum_engine.py:1841-1847`):
1. Removed `dps_growth_weighted` from `GateParams.quality_factors`
2. Rebalanced Growth pillar across remaining 5 factors proportionally
3. Propagated gate-specific columns to View · Candidates sheet

**Result**: Valid Growth pillar scores (0.0–1.0) for all cycles with sufficient data.