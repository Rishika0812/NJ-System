"""
S³ Core — NIFTY 500 Universe (exclusivity filter)
==================================================
The shared DuckDB store (``storage/market_data.duckdb``) is built by the S3
main system, whose ``NIFTY500Universe`` provider already scopes ingestion to
the official NIFTY 500 constituent list — so in practice the ``prices`` table
should only ever contain NIFTY 500 names plus the benchmark rows
(``NIFTY_500`` / ``NIFTY_50``).

This module adds a **defensive, explicit** second layer on top of that: it
loads the same official constituent file the main system ships
(``data/nifty500_constituents.csv``, NSE's ``ind_nifty500list`` export) and
filters the loaded stock universe against it, so:

* if the shared DB is ever refreshed with extra tickers (a different index,
  a delisted name, a data-vendor benchmark row, etc.) they are silently
  dropped rather than leaking into rankings / legs / exports, and
* every tab, export sheet, and status badge can say — accurately — "NIFTY
  500 exclusive" and mean it.

Matching is done on the bare NSE symbol (the part before any ``.NS`` /
exchange suffix the DB may use), case-insensitively.
"""
from __future__ import annotations

import os
import re

import pandas as pd
import streamlit as st

_DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
CONSTITUENTS_PATH = os.path.join(_DATA_DIR, "nifty500_constituents.csv")

# Tickers that are benchmarks/indices, never individual constituents — always
# excluded from the "stock universe" regardless of the constituent list.
_BENCHMARK_TICKERS = {"NIFTY_500", "NIFTY_50", "NIFTY500", "NIFTY50"}


def _bare_symbol(ticker: str) -> str:
    """Strip exchange suffixes (.NS, .BO, ...) and non-alnum noise, uppercase."""
    if not isinstance(ticker, str):
        return ""
    sym = ticker.split(".")[0]
    sym = re.sub(r"[^A-Za-z0-9&\-]", "", sym)
    return sym.upper()


@st.cache_data(show_spinner=False)
def load_constituents(path: str | None = None) -> pd.DataFrame:
    """Official NIFTY 500 constituent list: columns ``symbol``, ``name``, ``industry``.

    Returns an empty DataFrame (with a Streamlit warning, not an exception) if
    the bundled CSV is missing, so a stale bundle never hard-crashes the app —
    it just falls back to "no extra filtering" (see :func:`filter_universe`).
    """
    p = path or CONSTITUENTS_PATH
    if not os.path.exists(p):
        st.warning(
            f"NIFTY 500 constituent file not found at {p!r}; universe "
            "exclusivity filter is disabled (relying on the DB alone)."
        )
        return pd.DataFrame(columns=["symbol", "name", "industry"])
    df = pd.read_csv(p)
    df.columns = [c.strip().lower().replace(" ", "_") for c in df.columns]
    ren = {"company_name": "name", "symbol": "symbol", "industry": "industry"}
    df = df.rename(columns={k: v for k, v in ren.items() if k in df.columns})
    if "symbol" not in df.columns:
        return pd.DataFrame(columns=["symbol", "name", "industry"])
    df["symbol"] = df["symbol"].map(_bare_symbol)
    return df[[c for c in ("symbol", "name", "industry") if c in df.columns]].drop_duplicates("symbol")


def constituent_symbols(path: str | None = None) -> set[str]:
    """Set of bare NIFTY 500 symbols (uppercased, no exchange suffix)."""
    df = load_constituents(path)
    return set(df["symbol"]) if not df.empty else set()


def is_nifty500(ticker: str, symbols: set[str] | None = None) -> bool:
    """True if ``ticker`` is a NIFTY 500 constituent (benchmarks always False)."""
    bare = _bare_symbol(ticker)
    if ticker in _BENCHMARK_TICKERS or bare in {_bare_symbol(b) for b in _BENCHMARK_TICKERS}:
        return False
    syms = symbols if symbols is not None else constituent_symbols()
    if not syms:
        # Bundled list unavailable/empty -> don't filter anything out; the DB's
        # own NIFTY 500-only ingestion is the only guarantee in that case.
        return True
    return bare in syms


def filter_universe(stock_dict: dict, path: str | None = None) -> tuple[dict, list[str]]:
    """Filter a ``{ticker: DataFrame}`` universe down to NIFTY 500 names only.

    Returns ``(filtered_dict, dropped_tickers)`` so callers can surface exactly
    what was excluded (e.g. in the sidebar DB status panel).
    """
    syms = constituent_symbols(path)
    if not syms:
        return dict(stock_dict), []
    kept, dropped = {}, []
    for ticker, df in stock_dict.items():
        if is_nifty500(ticker, syms):
            kept[ticker] = df
        else:
            dropped.append(ticker)
    return kept, dropped


def universe_badge(stock_dict: dict, path: str | None = None) -> str:
    """Short markdown status line for the sidebar, e.g. '✅ 487 / 501 NIFTY 500 names loaded'."""
    syms = constituent_symbols(path)
    n_loaded = len(stock_dict)
    if not syms:
        return f"⚠️ NIFTY 500 constituent list unavailable — {n_loaded} tickers loaded (unfiltered)"
    return f"✅ NIFTY 500 exclusive — {n_loaded} / {len(syms)} constituents present in the DB"
