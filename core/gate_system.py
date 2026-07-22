"""
S³ Core — Gate System (ARQM pipeline)
=====================================
This module implements the 3‑gate ARQM (Momentum → Stability → Quality)
pipeline used by the S3‑main system. The gates operate sequentially on
pre‑computed factor tables (``feature_store`` and ``fundamental_quality_features``)
so that scores match the commercial product.

Pipeline steps
--------------
1. Momentum – raw ROC (unscaled), z‑scored then min‑max to 0..1.
2. Stability – β only, lower-is-better (negated z-score), min‑max to 0..1.
3. Quality – 14 factors across 5 pillars, weighted & z-scored, min‑max final.

Pillar weights: profitability 30 %, growth 30 %, financial_strength 15 %,
cash_flow 15 %, shareholder_return 10 %.

Selection blend: momentum 0.40, quality 0.40, stability 0.20.
Top 30 % kept by default (configurable via ``GateParams``).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

import numpy as np
import pandas as pd
import streamlit as st

Normalization = Literal["zscore", "robust_zscore", "percentile", "minmax"]
SelectionMode = Literal["top_pct", "top_n"]


def get_arqm_analysis() -> str:
    """Return a professional markdown analysis of the ARQM 3‑gate pipeline,
    its sequential settings, and the key parameters used in this gate system.
    """
    return (
        "# ARQM Model – Professional Analysis\n\n"
        "## Overview\n"
        "The **ARQM** (Asset‑Rotation Quality‑Momentum) pipeline is a three‑gate "
        "scoring engine that runs **sequentially**:\n\n"
        "1. **Momentum Gate** – ranks the eligible universe by an *unscaled ROC* "
        "factor.  The raw values are z‑scored then min‑max normalised to a 0‑1 "
        "scale.\n"
        "2. **Stability / Low‑Vol Gate** – uses the *beta* factor only.  Lower "
        "beta is considered better; the series is normalised (z‑score), "
        "sign‑flipped so that *lower‑is‑better*, then min‑max scaled.\n"
        "3. **Quality Gate** – aggregates **14 fundamental factors** across five "
        "pillars (Profitability 30 %, Growth 30 %, Financial‑Strength 15 %, "
        "Cash‑Flow 15 %, Shareholder‑Return 10 %).  Each factor is z‑scored, "
        "weighted inside its pillar, pillar scores are min‑max normalised, "
        "then blended with the pillar weights.  Hard minimum thresholds (e.g. "
        "`ROE ≥ 12 %`, `Interest‑Coverage ≥ 1.5`) are applied as knock‑out "
        "filters.\n\n"
        "## Overall Blend (default)\n"
        "- Momentum weight: **0.40**\n"
        "- Quality weight: **0.40**\n"
        "- Stability weight: **0.20**\n\n"
        "The blended score is again min‑max normalised.  Selection keeps the "
        "top **30 %** (`top_pct = 0.30`) or a hard cap `top_n = 50` when the "
        "mode is *top_n*.\n\n"
        "## Key Parameter Objects\n"
        "- `GateParams` (frozen dataclass) – centralises every tunable:\n"
        "  - `momentum_column = \"momentum_unscaled\"`\n"
        "  - `momentum_normalization = \"zscore\"`\n"
        "  - `stability_column = \"beta\"`\n"
        "  - `stability_normalization = \"zscore\"`\n"
        "  - `quality_factors` – 14 `QualityFactor` tuples (name, pillar, "
        "weight, optional `min_threshold`)\n"
        "  - `quality_pillar_weights` – dict with the five pillar weights\n"
        "  - `quality_normalization = \"zscore\"`\n"
        "  - `quality_rollup = \"median\"` (year‑over‑year rollup)\n"
        "  - `min_quality_score = 0.0`\n"
        "  - Blend weights: `momentum_weight`, `quality_weight`, `stability_weight`\n"
        "  - Selection: `selection_mode`, `top_pct`, `top_n`\n\n"
        "## Sequential Execution Flow (per rebalance date)\n"
        "1. Pull latest `feature_store` row ≤ rebalance date (`_asof_row`).\n"
        "2. `momentum_gate()` → `momentum_score` (0‑1).\n"
        "3. `stability_gate()` → `stability_score` (0‑1).\n"
        "4. Load & roll‑up quality table (`_rollup_quality`).\n"
        "5. `quality_gate()` → pillar scores + `quality_score` (0‑1).\n"
        "6. Blend: `combined = Σ weight_i * score_i`.\n"
        "7. Drop names with **no data in any gate**.\n"
        "8. Rank & select via `_select()`.\n\n"
        "## Usage\n"
        "```python\n"
        "from core.gate_system import get_arqm_analysis, run_gate_system_legs\n"
        "print(get_arqm_analysis())\n"
        "```\n"
    )


# ─────────────────────────────────────────────────────────────────────────────
# Normalization helpers — verbatim port of core/backtesting/normalization.py
# from the S3 main system, so scores match exactly.
# ─────────────────────────────────────────────────────────────────────────────

def zscore(series: pd.Series) -> pd.Series:
    s = pd.to_numeric(series, errors="coerce")
    mu, sd = s.mean(), s.std()
    if sd is None or pd.isna(sd) or sd == 0:
        return pd.Series(np.nan, index=s.index)
    return (s - mu) / sd


def minmax(series: pd.Series) -> pd.Series:
    s = pd.to_numeric(series, errors="coerce")
    lo, hi = s.min(), s.max()
    if pd.isna(lo) or pd.isna(hi) or hi == lo:
        return pd.Series(np.nan, index=s.index)
    return (s - lo) / (hi - lo)


def normalize(series: pd.Series, method: Normalization | str) -> pd.Series:
    if method in ("raw", "none"):
        return pd.to_numeric(series, errors="coerce")
    if method == "zscore":
        return zscore(series)
    if method == "minmax":
        return minmax(series)
    if method == "percentile":
        return pd.to_numeric(series, errors="coerce").rank(pct=True, method="average")
    if method == "robust_zscore":
        s = pd.to_numeric(series, errors="coerce")
        med = s.median()
        iqr = s.quantile(0.75) - s.quantile(0.25)
        scale = iqr / 1.349 if not pd.isna(iqr) and iqr != 0 else np.nan
        if pd.isna(scale) or scale == 0:
            return pd.Series(np.nan, index=s.index)
        return (s - med) / scale
    return pd.to_numeric(series, errors="coerce")


def score_lower_is_better(series: pd.Series, method: Normalization | str) -> pd.Series:
    if method in ("percentile", "minmax"):
        return 1.0 - normalize(series, method)
    if method in ("raw", "none"):
        return -pd.to_numeric(series, errors="coerce")
    return -normalize(series, method)


# ─────────────────────────────────────────────────────────────────────────────
# Parameters — mirrors S3-main's BacktestParameters defaults, narrowed for
# momentum (ROC-unscaled only) and stability (beta only) per spec.
# ─────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class QualityFactor:
    name: str
    pillar: str
    weight: float = 1.0
    min_threshold: float | None = None


@dataclass(frozen=True)
class GateParams:
    # -- Enable / Disable Pipeline Gates --------------------------------------
    enable_momentum: bool = True
    enable_stability: bool = True
    enable_quality: bool = True

    # -- Momentum: ROC (unscaled) only ---------------------------------------
    momentum_column: str = "momentum_unscaled"
    momentum_normalization: Normalization = "zscore"

    # -- Stability / Low-Vol: beta only --------------------------------------
    stability_column: str = "beta"
    stability_normalization: Normalization = "zscore"

    # -- Quality: identical to S3-main defaults ------------------------------
    quality_factors: tuple[QualityFactor, ...] = field(default_factory=lambda: (
        QualityFactor("roe", "profitability", weight=1.0, min_threshold=0.12),
        QualityFactor("roce", "profitability", weight=1.0, min_threshold=0.12),
        QualityFactor("roa", "profitability", weight=0.8, min_threshold=0.08),
        QualityFactor("cash_roce", "profitability", weight=0.8, min_threshold=0.10),
        QualityFactor("eps_growth_weighted", "growth", weight=1.0),
        QualityFactor("revenue_growth_weighted", "growth", weight=1.0),
        QualityFactor("roe_growth_weighted", "growth", weight=0.8),
        QualityFactor("roce_growth_weighted", "growth", weight=0.8),
        QualityFactor("dps_growth_weighted", "growth", weight=0.6),
        QualityFactor("sustainable_growth_rate", "growth", weight=0.8),
        QualityFactor("interest_coverage_ratio", "financial_strength", weight=1.0, min_threshold=1.5),
        QualityFactor("equity_to_total_capital", "financial_strength", weight=1.0, min_threshold=0.40),
        QualityFactor("ocf_to_ebitda", "cash_flow", weight=1.0, min_threshold=0.10),
        QualityFactor("dividend_payout_ratio", "shareholder_return", weight=0.6),
        QualityFactor("dividend_payout_ratio_cumulative", "shareholder_return", weight=0.5),
    ))
    quality_pillar_weights: dict = field(default_factory=lambda: {
        "profitability": 0.30, "growth": 0.30, "financial_strength": 0.15,
        "cash_flow": 0.15, "shareholder_return": 0.10,
    })
    quality_normalization: Normalization = "zscore"
    quality_rollup: Literal["latest", "median", "weighted"] = "median"
    min_quality_score: float = 0.0

    # -- Overall blend (REMOVED) ----------------------------------------------
    # No longer using weights; filtration is sequential.

    # -- Selection (Momentum Discovery) ----------------------------------------
    momentum_selection: SelectionMode = "top_pct"
    momentum_top_pct: float = 0.30
    momentum_top_n: int = 50

    # -- Selection (Stability / Low Vol) ---------------------------------------
    stability_selection: SelectionMode = "top_pct"
    stability_top_pct: float = 0.50
    stability_top_n: int = 50

    # -- Market Cap Allocation (Large, Mid, Small Cap) -------------------------
    enable_cap_filter: bool = True
    large_cap_pct: float = 0.50
    mid_cap_pct: float = 0.30
    small_cap_pct: float = 0.20


DEFAULT_PARAMS = GateParams()


@st.cache_data(show_spinner=False)
def load_market_cap_categories(db_path: str, _mtime: float) -> dict[str, str]:
    """Map bare NSE symbol -> 'Large Cap' | 'Mid Cap' | 'Small Cap'.
    Large Cap = Top 100, Mid Cap = Ranks 101-250, Small Cap = Ranks 251-500.
    """
    import duckdb
    mapping = {}
    try:
        con = duckdb.connect(db_path, read_only=True)
        if _table_exists(con, "fundamentals_company"):
            df = con.execute("SELECT ticker, market_cap FROM fundamentals_company").fetchdf()
            if not df.empty and "market_cap" in df.columns:
                df["market_cap"] = pd.to_numeric(df["market_cap"], errors="coerce").fillna(0)
                df["bare"] = df["ticker"].map(lambda t: str(t).split(".")[0].upper())
                df = df.sort_values("market_cap", ascending=False).reset_index(drop=True)
                for idx, row in df.iterrows():
                    sym = row["bare"]
                    if idx < 100:
                        mapping[sym] = "Large Cap"
                    elif idx < 250:
                        mapping[sym] = "Mid Cap"
                    else:
                        mapping[sym] = "Small Cap"
        con.close()
    except Exception:
        pass
        
    # Fallback to constituents file order if missing from DB
    if not mapping:
        try:
            from core.nifty500_universe import load_constituents
            const_df = load_constituents()
            if not const_df.empty:
                for idx, row in const_df.iterrows():
                    sym = str(row["symbol"]).upper()
                    if idx < 100:
                        mapping[sym] = "Large Cap"
                    elif idx < 250:
                        mapping[sym] = "Mid Cap"
                    else:
                        mapping[sym] = "Small Cap"
        except Exception:
            pass
    return mapping


# ─────────────────────────────────────────────────────────────────────────────
# Data access — reads feature_store / fundamental_quality_features straight
# from the shared DuckDB (same tables S3-main's backtest engine reads).
# ─────────────────────────────────────────────────────────────────────────────

def _table_exists(con, name: str) -> bool:
    try:
        row = con.execute(
            "SELECT 1 FROM information_schema.tables WHERE table_name = ?", [name]
        ).fetchone()
        return row is not None
    except Exception:
        return False


@st.cache_data(show_spinner=False)
def load_market_features(db_path: str, _mtime: float,
                          columns: tuple = ("beta", "momentum_unscaled")) -> pd.DataFrame:
    """Daily ``feature_store`` rows (ticker, date, beta, momentum_unscaled, ...).

    Empty DataFrame (not an exception) if the table/columns aren't present —
    e.g. an older DB snapshot taken before feature engineering ran — so the
    gate system degrades to "insufficient data" rather than crashing.
    """
    import duckdb
    con = duckdb.connect(db_path, read_only=True)
    try:
        if not _table_exists(con, "feature_store"):
            return pd.DataFrame(columns=["ticker", "date", *columns])
        existing_cols = {r[0] for r in con.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_name = 'feature_store'"
        ).fetchall()}
        cols = [c for c in columns if c in existing_cols]
        if not cols:
            return pd.DataFrame(columns=["ticker", "date", *columns])
        sel = ", ".join(f'"{c}"' for c in cols)
        df = con.execute(f'SELECT ticker, date, {sel} FROM feature_store').fetchdf()
        df["date"] = pd.to_datetime(df["date"]).dt.normalize()
        return df.sort_values(["ticker", "date"]).reset_index(drop=True)
    finally:
        con.close()


@st.cache_data(show_spinner=False)
def load_quality_features(db_path: str, _mtime: float) -> pd.DataFrame:
    """Latest-per-ticker quality snapshot from ``fundamental_quality_features``,
    rolled up per ``params.quality_rollup`` (default: median across years)."""
    import duckdb
    con = duckdb.connect(db_path, read_only=True)
    try:
        if not _table_exists(con, "fundamental_quality_features"):
            return pd.DataFrame()
        df = con.execute("SELECT * FROM fundamental_quality_features").fetchdf()
        return df
    finally:
        con.close()


def _rollup_quality(raw: pd.DataFrame, params: GateParams) -> pd.DataFrame:
    """One row per ticker, applying the configured rollup suffix (matches
    S3-main's ``_load_quality``)."""
    if raw.empty:
        return pd.DataFrame()
    base_cols = [f.name for f in params.quality_factors]
    keep = {"ticker", "financial_year"}
    for base in base_cols:
        suf = {"median": "_median", "weighted": "_weighted"}.get(params.quality_rollup)
        if suf and f"{base}{suf}" in raw.columns:
            keep.add(f"{base}{suf}")
        elif base in raw.columns:
            keep.add(base)
        else:
            for s in ("_median", "_weighted"):
                if f"{base}{s}" in raw.columns:
                    keep.add(f"{base}{s}")
                    break
    q = raw[[c for c in raw.columns if c in keep]]
    if "financial_year" in q.columns:
        q = q.sort_values(["ticker", "financial_year"]).groupby("ticker").tail(1)
    q = q.set_index("ticker")
    rename = {c: c.replace("_median", "").replace("_weighted", "") for c in q.columns}
    return q.rename(columns=rename)


# ─────────────────────────────────────────────────────────────────────────────
# Gates
# ─────────────────────────────────────────────────────────────────────────────

def _asof_row(mf: pd.DataFrame, date: pd.Timestamp) -> pd.DataFrame:
    """Latest feature_store row on/before ``date``, per ticker (ultra-fast)."""
    if mf.empty:
        return mf
    if "date" in mf.columns:
        dates = mf["date"].values
        d_ts = pd.Timestamp(date)
        valid = dates[dates <= d_ts]
        if len(valid) == 0:
            return pd.DataFrame(columns=mf.columns)
        target_date = valid.max()
        sub = mf[mf["date"] == target_date]
        return sub.set_index("ticker")
    return pd.DataFrame()


def momentum_gate(mf_asof: pd.DataFrame, eligible: list[str], params: GateParams) -> pd.Series:
    """ROC-unscaled momentum score, normalized cross-sectionally, 0..1 scale."""
    idx = pd.Index(eligible)
    if mf_asof.empty or params.momentum_column not in mf_asof.columns:
        return pd.Series(np.nan, index=idx)
    raw = mf_asof.reindex(idx)[params.momentum_column]
    ns = normalize(raw, params.momentum_normalization)
    return minmax(ns) if ns.notna().any() else ns


def stability_gate(mf_asof: pd.DataFrame, eligible: list[str], params: GateParams) -> pd.Series:
    """Beta-only stability score (lower |beta| exposure = higher score)."""
    idx = pd.Index(eligible)
    if mf_asof.empty or params.stability_column not in mf_asof.columns:
        return pd.Series(np.nan, index=idx)
    raw = mf_asof.reindex(idx)[params.stability_column]
    ns = score_lower_is_better(raw, params.stability_normalization)
    return minmax(ns) if ns.notna().any() else ns


def quality_gate(quality: pd.DataFrame, eligible: list[str],
                  params: GateParams) -> tuple[dict[str, pd.Series], pd.Series]:
    """Full-parity quality pillar scoring (verbatim logic from S3-main's quality_gate)."""
    idx = pd.Index(eligible)
    if quality.empty:
        return {}, pd.Series(np.nan, index=idx)
    sub = quality.reindex(idx)

    pillar_norm: dict[str, list[pd.Series]] = {p: [] for p in params.quality_pillar_weights}
    pillar_w: dict[str, list[float]] = {p: [] for p in params.quality_pillar_weights}
    for f in params.quality_factors:
        if f.name not in sub.columns:
            continue
        raw = sub[f.name]
        ns = normalize(raw, params.quality_normalization)
        pillar_norm[f.pillar].append(ns)
        pillar_w[f.pillar].append(f.weight)

    pillar_scores: dict[str, pd.Series] = {}
    for p, series_list in pillar_norm.items():
        if not series_list:
            pillar_scores[p] = pd.Series(np.nan, index=idx)
            continue
        mat = pd.concat(series_list, axis=1)
        w = np.array(pillar_w[p], dtype=float)
        w = w / w.sum()
        combined = mat.mul(w, axis=1).sum(axis=1)
        pillar_scores[p] = minmax(combined) if combined.notna().any() else combined

    pw = pd.Series(params.quality_pillar_weights)
    active = [p for p in pw.index if pillar_scores[p].notna().any()]
    if not active:
        return pillar_scores, pd.Series(np.nan, index=idx)
    wvec = pw[active] / pw[active].sum()
    qual = sum(pillar_scores[p] * wvec[p] for p in active)
    qual = minmax(qual) if qual.notna().any() else qual

    for f in params.quality_factors:
        if f.min_threshold is None or f.name not in sub.columns:
            continue
        breach = sub[f.name] < f.min_threshold
        qual = qual.mask(breach, np.nan)

    if params.min_quality_score > 0:
        qual = qual[qual >= params.min_quality_score]
    return pillar_scores, qual


def _select(score: pd.Series, mode: SelectionMode, top_pct: float, top_n: int) -> list[str]:
    s = score.dropna().sort_values(ascending=False)
    if s.empty:
        return []
    if mode == "top_n":
        return list(s.head(top_n).index)
    n = max(1, int(round(len(s) * top_pct)))
    return list(s.head(n).index)


# ─────────────────────────────────────────────────────────────────────────────
# Per-leg orchestration — plugs into the existing Rise/Fall phase schedule.
# ─────────────────────────────────────────────────────────────────────────────

def rank_universe(as_of: pd.Timestamp, eligible: list[str], mf: pd.DataFrame,
                   quality_rolled: pd.DataFrame, params: GateParams = DEFAULT_PARAMS,
                   qual_cached: pd.Series | None = None) -> pd.DataFrame:
    """Run enabled gates sequentially as-of a single date and return the 
    scorecard for the final surviving ``eligible`` tickers.
    
    Pipeline: Momentum -> Stability -> Quality (each optional).
    Final rank is based on the first active enabled gate score.
    """
    mf_asof = _asof_row(mf, as_of)
    
    # 1. Momentum Gate
    mom = momentum_gate(mf_asof, eligible, params)
    if params.enable_momentum:
        mom_survivors = set(_select(mom, params.momentum_selection, params.momentum_top_pct, params.momentum_top_n))
    else:
        mom_survivors = set(eligible)
    
    # 2. Stability Gate
    stab_eligible = [t for t in eligible if t in mom_survivors]
    stab = stability_gate(mf_asof, stab_eligible, params)
    if params.enable_stability:
        stab_survivors = set(_select(stab, params.stability_selection, params.stability_top_pct, params.stability_top_n))
    else:
        stab_survivors = set(stab_eligible)
    
    # 3. Quality Gate
    qual_eligible = [t for t in stab_eligible if t in stab_survivors]
    pillars = {}
    if params.enable_quality:
        if qual_cached is not None:
            qual = qual_cached.reindex(qual_eligible)
        else:
            pillars, qual = quality_gate(quality_rolled, qual_eligible, params)
        final_survivors = set(qual.dropna().index)
    else:
        qual = pd.Series(np.nan, index=qual_eligible)
        final_survivors = set(qual_eligible)

    out = pd.DataFrame({
        "ticker": eligible,
        "momentum_score": mom.reindex(eligible).values,
        "stability_score": stab.reindex(eligible).values,
        "quality_score": qual.reindex(eligible).values,
        "combined_score": mom.reindex(eligible).values,
        "passed_momentum": [t in mom_survivors for t in eligible],
        "passed_stability": [t in stab_survivors for t in eligible],
        "passed_quality": [t in final_survivors for t in eligible],
    })
    for p, s in pillars.items():
        if not s.empty:
            out[f"pillar_{p}"] = s.reindex(eligible).values
            
    has_any = out[["momentum_score", "stability_score", "quality_score"]].notna().any(axis=1)
    if not has_any.any():
        out["has_any"] = True
    else:
        out = out[has_any].copy()
    
    sort_col = ("momentum_score" if params.enable_momentum
                else "stability_score" if params.enable_stability
                else "quality_score" if params.enable_quality
                else "momentum_score")

    out = out.sort_values(sort_col, ascending=False, na_position="last").reset_index(drop=True)
    out["rank"] = range(1, len(out) + 1)
    out["selected"] = out["ticker"].isin(final_survivors)
    out["as_of"] = as_of
    return out


def run_gate_system_legs(db_path: str, db_mtime: float, phases: pd.DataFrame,
                          eligible_tickers: list[str],
                          params: GateParams = DEFAULT_PARAMS) -> dict:
    """Run the gate system at every leg's entry date in ``phases``.

    Mirrors the shape ``run_momentum()`` returns so it can be dropped into the
    same app.py rendering path: a dict with ``leg_rank_df`` (per-leg full
    scorecard, analogous to ``leg_rank_df`` from the ROC/Vol/Beta path) and
    ``cycle_df`` (one row per leg: trade direction, dates, n_selected, top
    picks) for the Overview / Cycles tabs.
    """
    empty = {"leg_rank_df": pd.DataFrame(), "cycle_df": pd.DataFrame(),
              "candidates_df": pd.DataFrame(), "params": params}
    if phases is None or phases.empty or not eligible_tickers:
        return empty

    mf = load_market_features(db_path, db_mtime)
    quality_raw = load_quality_features(db_path, db_mtime)
    quality_rolled = _rollup_quality(quality_raw, params)

    if mf.empty and quality_rolled.empty:
        st.warning(
            "Gate System: neither `feature_store` nor `fundamental_quality_features` "
            "were found in the shared DB — the momentum/stability/quality gates have "
            "no data to score against. Re-run the S3 main system's feature-engineering "
            "and fundamentals pipelines and re-upload the DB to GridFS."
        )
        return empty

    leg_frames, cycle_rows = [], []
    for _, leg in phases.sort_values("entry_date").iterrows():
        as_of = pd.Timestamp(leg["entry_date"])
        scorecard = rank_universe(as_of, eligible_tickers, mf, quality_rolled, params)
        scorecard["phase_id"] = leg.get("phase_id")
        scorecard["trade"] = leg.get("trade")
        scorecard["entry_date"] = as_of
        scorecard["exit_date"] = leg.get("exit_date")
        leg_frames.append(scorecard)

        picked = scorecard[scorecard["selected"]]
        cycle_rows.append({
            "phase_id": leg.get("phase_id"), "trade": leg.get("trade"),
            "entry_date": as_of, "exit_date": leg.get("exit_date"),
            "n_eligible": int(scorecard["combined_score"].notna().sum()),
            "n_selected": int(len(picked)),
            "top_picks": ", ".join(picked.sort_values("combined_score", ascending=False)
                                    ["ticker"].head(10).tolist()),
            "avg_momentum_score": picked["momentum_score"].mean(),
            "avg_stability_score": picked["stability_score"].mean(),
            "avg_quality_score": picked["quality_score"].mean(),
        })

    leg_rank_df = pd.concat(leg_frames, ignore_index=True) if leg_frames else pd.DataFrame()
    cycle_df = pd.DataFrame(cycle_rows)
    return {"leg_rank_df": leg_rank_df, "cycle_df": cycle_df,
            "candidates_df": leg_rank_df, "params": params}


def params_summary(params: GateParams = DEFAULT_PARAMS) -> pd.DataFrame:
    """Flat parameter table for the Export tab's "Gate Parameters" sheet."""
    rows = [
        {"section": "Pipeline Controls", "parameter": "enable_momentum", "value": params.enable_momentum},
        {"section": "Pipeline Controls", "parameter": "enable_stability", "value": params.enable_stability},
        {"section": "Pipeline Controls", "parameter": "enable_quality", "value": params.enable_quality},

        {"section": "Momentum", "parameter": "factor", "value": params.momentum_column},
        {"section": "Momentum", "parameter": "normalization", "value": params.momentum_normalization},
        {"section": "Momentum", "parameter": "selection_mode", "value": params.momentum_selection},
        {"section": "Momentum", "parameter": "top_pct", "value": params.momentum_top_pct},
        {"section": "Momentum", "parameter": "top_n", "value": params.momentum_top_n},
        
        {"section": "Stability / Low-Vol", "parameter": "factor", "value": params.stability_column},
        {"section": "Stability / Low-Vol", "parameter": "normalization", "value": params.stability_normalization},
        {"section": "Stability / Low-Vol", "parameter": "selection_mode", "value": params.stability_selection},
        {"section": "Stability / Low-Vol", "parameter": "top_pct", "value": params.stability_top_pct},
        {"section": "Stability / Low-Vol", "parameter": "top_n", "value": params.stability_top_n},
        
        {"section": "Quality Validation", "parameter": "n_factors", "value": len(params.quality_factors)},
        {"section": "Quality Validation", "parameter": "rollup", "value": params.quality_rollup},
        {"section": "Quality Validation", "parameter": "min_score", "value": params.min_quality_score},
        {"section": "Quality Validation", "parameter": "normalization", "value": params.quality_normalization},

        {"section": "Market Cap Distribution", "parameter": "enable_cap_filter", "value": params.enable_cap_filter},
        {"section": "Market Cap Distribution", "parameter": "large_cap_pct", "value": f"{params.large_cap_pct*100:.0f}%"},
        {"section": "Market Cap Distribution", "parameter": "mid_cap_pct", "value": f"{params.mid_cap_pct*100:.0f}%"},
        {"section": "Market Cap Distribution", "parameter": "small_cap_pct", "value": f"{params.small_cap_pct*100:.0f}%"},
    ]
    for pillar, w in params.quality_pillar_weights.items():
        rows.append({"section": "Quality Pillar Weights", "parameter": pillar, "value": w})
    for f in params.quality_factors:
        rows.append({"section": f"Quality Factor — {f.pillar}", "parameter": f.name,
                     "value": f"weight={f.weight}" + (f", min={f.min_threshold}" if f.min_threshold else "")})
    return pd.DataFrame(rows)
