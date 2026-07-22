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
- **Testing multiple ranking metrics** in a single interface
- **Providing comprehensive risk-adjusted performance metrics**
- **Exporting interactive Excel reports** for offline analysis

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

### How S³ Addresses These

1. **Automated Data Pipeline**: Downloads market data from MongoDB GridFS to local DuckDB
2. **Multi-Metric Ranking**: 11 distinct ranking methodologies in one interface
3. **Automatic Cycle Detection**: Zig-zag algorithm identifies Rise/Fall phases
4. **Comprehensive Export**: Interactive Excel with 15+ sheets, dashboard, and drill-down capabilities

---

## Key Features

### 1. Automated Data Ingestion
- **What it does**: Downloads market data from MongoDB GridFS to local DuckDB
- **Why it matters**: Eliminates manual Excel uploads; ensures data consistency with the main S3 system
- **How it works**: Uses `db_provisioning.py` to download `.duckdb.gz` from GridFS, decompresses locally

### 2. Automatic Phase Scheduling
- **What it does**: Detects Rise/Fall market cycles using 6% swing threshold
- **Why it matters**: No manual date ranges; adapts to actual market conditions
- **How it works**: Zig-zag algorithm identifies peaks/troughs where NIFTY moves ≥ 6%

### 3. Multi-Metric Stock Ranking
- **What it does**: Ranks NIFTY 500 stocks using 11 different factors
- **Why it matters**: Test various momentum/volatility/quality combinations
- **How it works**: Computes ROC, volatility, beta, and quality scores per leg window

### 4. Common-Stock Selection
- **What it does**: Finds stocks appearing in top-N across ALL legs
- **Why it matters**: Reduces false positives; ensures robust selection
- **How it works**: Set intersection of top-N candidates per leg

### 5. NIFTY Threshold Entry/Exit
- **What it does**: Buys when NIFTY rises X%, sells when NIFTY falls Y%
- **Why it matters**: Systematic risk management; avoids market timing
- **How it works**: Trailing peak exit with configurable fall percentage

### 6. ARQM Gate System (Quality Pipeline)
- **What it does**: 3-stage filter: Momentum → Stability (beta) → Quality (14 factors)
- **Why it matters**: Professional-grade stock screening matching S3-main
- **How it works**: Sequential scoring with configurable weights and thresholds

### 7. Investment Analysis
- **What it does**: Computes CAGR, max drawdown, Sharpe, win rate, equity curve
- **Why it matters**: Professional performance attribution
- **How it works**: Equal-weight portfolio simulation with reinvestment option

### 8. Interactive Excel Export
- **What it does**: Generates comprehensive Excel workbook with dashboard
- **Why it matters**: Offline analysis, client presentations, sharing
- **How it works**: 15+ sheets with hyperlinks, auto-filtering, interactive dashboard

---

## End-to-End Workflow

```mermaid
flowchart TD
    A[MongoDB\nMarket Data] -->|GridFS Download| B[Local DuckDB]
    B --> C[Data Loading\nNIFTY + 500 Stocks]
    C --> D[Phase Detection\nZig-zag 6% Swing]
    D --> E[Pattern Windows\nRise-Fall Cycles]
    E --> F[Stock Ranking\nPer Leg\nROC/Vol/Beta]
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
| **Data Load** | `NIFTY_500` table (MongoDB) | Load OHLCV into DuckDB | `nifty_df`, `stock_dict` |
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
    end
    
    A --> B
    A --> C
    B --> D
    B --> E
    B --> J
    C --> D
    D --> B
    E --> K
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
| **app.py** | Streamlit UI, orchestration | `run_momentum()`, sidebar controls, tabs |
| **momentum_engine.py** | Core ranking logic | `_compute_beta()`, `_leg_roc_detail()`, `run_momentum()` |
| **multi_leg_engine.py** | Pattern windows, trading | `find_pattern_windows()`, `compute_nifty_threshold_trades()` |
| **phase_engine.py** | Phase detection, returns | `generate_phases_from_nifty()`, `compute_all_phase_returns()` |
| **investment_analysis.py** | Portfolio metrics | `compute_investment_analysis()`, `_compute_metrics()` |
| **db_provisioning.py** | Data download | `ensure_database()`, `test_connection()` |
| **db_loader.py** | DB queries | `load_all_from_db()`, `load_nifty_from_db()` |
| **gate_system.py** | Quality pipeline | `rank_universe()`, `momentum_gate()`, `quality_gate()` |
| **momentum_exporter.py** | Excel export | `generate_momentum_excel()` |

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
| **Gate** | ARQM pipeline score | Professional screening |

---

## Data Sources

### Primary: MongoDB GridFS

The system expects a pre-built DuckDB database stored in MongoDB GridFS containing:

| Table | Content |
|-------|---------|
| `prices` | Daily OHLCV for NIFTY 500 constituents |
| `fundamentals_company` | Market cap data for cap distribution |

### Secondary: Bundled Constituents

- `data/nifty500_constituents.csv` — Official NIFTY 500 list for universe filtering

---

## Output Formats

### Excel Workbook Sheets

1. **Portfolio Summary** — Configuration + headline metrics
2. **Cycle Ledger** — Buy → peak → fall → sell timeline
3. **Cycle Status** — Traded vs skipped windows
4. **Cycle Candidates** — Per-cycle common stocks ranked
5. **Cycle Leg Rankings** — Per-leg Top-N with scores
6. **Eligible Stock Ranking** — All eligible stocks with quartiles
7. **Per-Cycle Equity** — Capital allocation per window
8. **Common Stocks P&L** — Per-stock profit and alpha
9. **Trade Log** — Every executed trade
10. **Stock Summary** — Per-ticker aggregated statistics
11. **NIFTY Cycle Path** — Day-by-day NIFTY with markers
12. **Yearwise Summary** — Calendar year breakdown
13. **Phase Schedule** — Rise/Fall phase dates
14. **Dashboard** — Interactive cycle selector
15. **Cycle Details** — Per-cycle deep dive

---

## License

Internal research tool. Not for redistribution.

---

## Acknowledgments

Built for the S³ Momentum Investment System, leveraging the same data store and factor pipeline as the S3 main system.