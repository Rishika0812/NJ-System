"""
S³ Core — Database Loader
===========================
Replaces the old Excel-upload workflow (``Data.xlsx`` / ``Dates.xlsx``). Reads
straight from the same DuckDB analytical store the S3 main system builds
(``storage/market_data.duckdb`` — auto-downloaded from MongoDB GridFS via
``core.db_provisioning`` if it isn't already on disk).

What we get from the DB vs. what the Momentum engine used to get from Excel:

  Data.xlsx (stock universe)
      -> ``load_all_stocks_from_db()``. The DB only has plain OHLCV, no
         No dates are excluded by the old filters.

  Dates.xlsx (phase schedule + NIFTY)
      -> NIFTY closes come straight from the ``NIFTY_500`` row of the prices
         table (``load_nifty_from_db``). The phase schedule (Rise/Fall
         entry/exit dates) doesn't exist in the DB, so it is *generated* from
         the NIFTY series with a zig-zag swing detector: alternating
         peak/trough dates wherever the index has moved >= 6% up (Rise) or
         <= -6% down (Fall) from the last confirmed extreme
         (``generate_phases_from_nifty``).
"""
from __future__ import annotations

import os
from typing import Optional

import pandas as pd
import streamlit as st

from core.db_provisioning import DB_LOCAL_PATH
from core.nifty500_universe import filter_universe

# Benchmark ticker(s) as stored by the S3 main system, in preference order.
_BENCHMARK_CANDIDATES = ["NIFTY_500", "NIFTY_50"]

# Default zig-zag swing threshold (%) used to auto-generate the phase
# schedule from the NIFTY series, per the original Dates.xlsx convention.
DEFAULT_PHASE_THRESHOLD_PCT = 6.0


def _db_mtime(db_path: str) -> float:
    """File mtime, used as a cache-busting key so a refreshed DB is picked up."""
    try:
        return os.path.getmtime(db_path)
    except OSError:
        return 0.0


def _connect_ro(db_path: str):
    import duckdb
    return duckdb.connect(db_path, read_only=True)


# ─────────────────────────────────────────────────────────────────────────────
# NIFTY series
# ─────────────────────────────────────────────────────────────────────────────

@st.cache_data(show_spinner=False)
def load_nifty_from_db(db_path: str, _mtime: float) -> pd.DataFrame:
    """Load the NIFTY 500 (fallback NIFTY 50) daily closes from the DB.

    Returns a date-indexed DataFrame with a single ``close`` column, matching
    the shape the rest of the app expects from ``load_nifty()``.
    """
    con = _connect_ro(db_path)
    try:
        for ticker in _BENCHMARK_CANDIDATES:
            df = con.execute(
                "SELECT date, COALESCE(adj_close, close) AS close FROM prices "
                "WHERE ticker = ? ORDER BY date", [ticker],
            ).fetchdf()
            if not df.empty:
                df["date"] = pd.to_datetime(df["date"]).dt.normalize()
                df["close"] = pd.to_numeric(df["close"], errors="coerce")
                df = df.dropna(subset=["date", "close"])
                df = df.sort_values("date").drop_duplicates("date").set_index("date")
                return df[["close"]]

        # Fallback: compute synthetic market index (average stock close per date)
        df_syn = con.execute(
            "SELECT date, AVG(COALESCE(adj_close, close)) AS close FROM prices "
            "GROUP BY date ORDER BY date"
        ).fetchdf()
        if not df_syn.empty:
            df_syn["date"] = pd.to_datetime(df_syn["date"]).dt.normalize()
            df_syn["close"] = pd.to_numeric(df_syn["close"], errors="coerce")
            df_syn = df_syn.dropna(subset=["date", "close"])
            df_syn = df_syn.sort_values("date").drop_duplicates("date").set_index("date")
            return df_syn[["close"]]

        return pd.DataFrame()
    finally:
        con.close()


# ─────────────────────────────────────────────────────────────────────────────
# Stock universe
# ─────────────────────────────────────────────────────────────────────────────

@st.cache_data(show_spinner=False)
def load_all_stocks_from_db(db_path: str, _mtime: float) -> dict[str, pd.DataFrame]:
    """Load every non-benchmark ticker's price series from the DB.

    Returns ``{ticker: DataFrame}`` date-indexed with columns
    ``close, ticker`` — the same shape ``load_all_stocks()``
    returns when reading from Excel.
    """
    con = _connect_ro(db_path)
    try:
        placeholders = ",".join("?" * len(_BENCHMARK_CANDIDATES))
        df = con.execute(
            f"SELECT ticker, date, COALESCE(adj_close, close) AS close FROM prices "
            f"WHERE ticker NOT IN ({placeholders}) ORDER BY ticker, date",
            _BENCHMARK_CANDIDATES,
        ).fetchdf()
    finally:
        con.close()

    if df.empty:
        return {}

    df["date"] = pd.to_datetime(df["date"]).dt.normalize()
    df["close"] = pd.to_numeric(df["close"], errors="coerce")
    df = df.dropna(subset=["date", "close"])
    df = df[df["close"] > 0]

    out: dict[str, pd.DataFrame] = {}
    for ticker, g in df.groupby("ticker", sort=False):
        g = g.sort_values("date").drop_duplicates("date").reset_index(drop=True)
        if g.empty:
            continue
        # Ensure standard columns are present
        g["ticker"] = ticker
        out[ticker] = g.set_index("date")[["close", "ticker"]]
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Phase schedule — generated from NIFTY via a zig-zag swing detector
# ─────────────────────────────────────────────────────────────────────────────

def _zigzag_pivots(dates: pd.DatetimeIndex, closes: pd.Series, pct: float) -> list[tuple]:
    """Return alternating (date, price, 'H'/'L') pivots wherever the series
    has swung >= ``pct`` percent from the last confirmed pivot extreme.

    Standard zig-zag algorithm: track a running extreme in the current
    direction; once price retraces by >= pct% from that extreme, confirm the
    extreme as a pivot (high if we were trending up, low if trending down)
    and start tracking the opposite direction from the new price.
    """
    n = len(closes)
    if n < 2:
        return []

    pivots: list[tuple] = []
    trend: Optional[str] = None  # 'up' | 'down' | None (undetermined)
    anchor_price = closes.iloc[0]
    anchor_date = dates[0]
    # Running high/low seen since the anchor, while direction is still undetermined.
    hi_price, hi_date = anchor_price, anchor_date
    lo_price, lo_date = anchor_price, anchor_date
    ext_price = closes.iloc[0]
    ext_date = dates[0]

    for i in range(1, n):
        price = closes.iloc[i]
        date = dates[i]

        if trend is None:
            if price > hi_price:
                hi_price, hi_date = price, date
            if price < lo_price:
                lo_price, lo_date = price, date
            up_move = (hi_price - anchor_price) / anchor_price * 100
            down_move = (anchor_price - lo_price) / anchor_price * 100
            if up_move >= pct and up_move >= down_move:
                trend = "up"
                ext_price, ext_date = hi_price, hi_date
            elif down_move >= pct:
                trend = "down"
                ext_price, ext_date = lo_price, lo_date
            continue

        if trend == "up":
            if price >= ext_price:
                ext_price, ext_date = price, date
            else:
                retrace = (ext_price - price) / ext_price * 100
                if retrace >= pct:
                    pivots.append((ext_date, ext_price, "H"))
                    trend = "down"
                    ext_price, ext_date = price, date
        else:  # trend == 'down'
            if price <= ext_price:
                ext_price, ext_date = price, date
            else:
                rally = (price - ext_price) / ext_price * 100
                if rally >= pct:
                    pivots.append((ext_date, ext_price, "L"))
                    trend = "up"
                    ext_price, ext_date = price, date

    # Close out the final running extreme as a pivot too, so the last leg is usable.
    if trend is not None:
        pivots.append((ext_date, ext_price, "H" if trend == "up" else "L"))
    return pivots


@st.cache_data(show_spinner=False)
def generate_phases_from_nifty(
    nifty_df: pd.DataFrame, threshold_pct: float = DEFAULT_PHASE_THRESHOLD_PCT
) -> pd.DataFrame:
    """Auto-generate the Rise/Fall phase schedule from the NIFTY series.

    A phase is the leg between two consecutive confirmed swing points:
    trough -> peak = 'Rise', peak -> trough = 'Fall'. A swing point is
    confirmed once price has moved >= ``threshold_pct`` percent (default 6%)
    away from the running high/low — i.e. the classic zig-zag definition of
    "highest close" / "lowest close" pivots.

    Returns the same shape ``load_phases()`` used to produce from Dates.xlsx:
    columns ``phase_id, trade, entry_date, exit_date, days``.
    """
    if nifty_df is None or nifty_df.empty:
        return pd.DataFrame(columns=["phase_id", "trade", "entry_date", "exit_date", "days"])

    s = nifty_df["close"].dropna().sort_index()
    pivots = _zigzag_pivots(s.index, s, threshold_pct)
    if len(pivots) < 2:
        return pd.DataFrame(columns=["phase_id", "trade", "entry_date", "exit_date", "days"])

    rows = []
    for (d0, _, t0), (d1, _, t1) in zip(pivots[:-1], pivots[1:]):
        if t0 == "L" and t1 == "H":
            trade = "Rise"
        elif t0 == "H" and t1 == "L":
            trade = "Fall"
        else:
            continue  # shouldn't happen — pivots alternate by construction
        rows.append({"trade": trade, "entry_date": pd.Timestamp(d0), "exit_date": pd.Timestamp(d1)})

    df = pd.DataFrame(rows)
    if df.empty:
        return pd.DataFrame(columns=["phase_id", "trade", "entry_date", "exit_date", "days"])
    df["phase_id"] = range(len(df))
    df["days"] = (df["exit_date"] - df["entry_date"]).dt.days
    return df[["phase_id", "trade", "entry_date", "exit_date", "days"]]


# ─────────────────────────────────────────────────────────────────────────────
# Convenience: load everything in one call
# ─────────────────────────────────────────────────────────────────────────────

@st.cache_data(show_spinner=False)
def load_all_from_db(db_path: str | None = None, threshold_pct: float = DEFAULT_PHASE_THRESHOLD_PCT):
    """Load NIFTY, the stock universe, and the auto-generated phase schedule.

    Returns ``(phases, nifty_df, stock_dict, meta)`` where ``meta`` carries
    summary counts for the sidebar status indicator.
    """
    path = db_path or DB_LOCAL_PATH
    mtime = _db_mtime(path)

    nifty_df = load_nifty_from_db(path, mtime)
    stock_dict_raw = load_all_stocks_from_db(path, mtime)
    stock_dict, dropped_tickers = filter_universe(stock_dict_raw)
    phases = generate_phases_from_nifty(nifty_df, threshold_pct)

    meta = {
        "n_stocks": len(stock_dict),
        "n_stocks_raw": len(stock_dict_raw),
        "dropped_non_nifty500": dropped_tickers,
        "nifty_rows": len(nifty_df),
        "n_phases": len(phases),
        "date_range": None,
        "universe": "NIFTY 500 (exclusive)",
    }
    if not phases.empty:
        start = phases["entry_date"].min().strftime("%b %Y")
        end = phases["exit_date"].max().strftime("%b %Y")
        meta["date_range"] = f"{start} → {end}"
    return phases, nifty_df, stock_dict, meta
