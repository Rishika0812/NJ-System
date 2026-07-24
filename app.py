"""
╔══════════════════════════════════════════════════════════════════╗
║     S³ — Multi-Leg Stock Selection System                       ║
║     NIFTY Threshold Entry/Exit + Window-Based Stock Selection    ║
╚══════════════════════════════════════════════════════════════════╝

Run:  streamlit run app.py
"""
from __future__ import annotations

import sys
import os
import warnings
import io
import uuid
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor
from threading import Thread

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import plotly.express as px
from plotly.subplots import make_subplots
import streamlit as st

warnings.filterwarnings("ignore")

# ── local modules ─────────────────────────────────────────────────────────────
sys.path.insert(0, os.path.dirname(__file__))
from core.db_config      import is_configured, config_summary
from core.db_provisioning import ensure_database, local_status, DB_LOCAL_PATH
from core.db_loader       import load_all_from_db, DEFAULT_PHASE_THRESHOLD_PCT
from core.phase_engine   import compute_all_phase_returns, quartile_label
from core.investment_analysis import (
    compute_investment_analysis, fmt_inr, fmt_inr_full,
)
from core.momentum_engine import run_momentum
from export.momentum_exporter import generate_momentum_excel, _make_run_name as mom_make_run_name
from export.momentum_interactive import generate_momentum_interactive_excel
from core.persistence import (
    generate_run_id, save_config, save_metrics, save_execution_metadata,
    save_trades_parquet, save_portfolio_nav_parquet, save_excel_report,
    save_dataframe_parquet, load_dataframe_parquet,
    list_runs, load_config, load_metrics, load_trades_parquet, load_portfolio_nav_parquet,
    load_excel_report, delete_run, RESULTS_ROOT
)


def _make_safe_run_id(trial_name: str) -> str:
    """Generate a filesystem-safe run_id from a user-provided trial name.
    
    - Sanitizes the name (removes invalid filesystem chars, replaces spaces)
    - Appends timestamp suffix if name already exists to avoid overwrites
    - Falls back to timestamp-based name if trial_name is empty
    """
    import re
    from datetime import datetime
    from pathlib import Path
    
    if not trial_name or not trial_name.strip():
        return generate_run_id()
    
    # Sanitize: keep alphanumeric, hyphen, underscore; replace spaces with underscore
    safe_name = re.sub(r'[^\w\s-]', '', trial_name.strip())
    safe_name = re.sub(r'[\s]+', '_', safe_name)
    safe_name = safe_name.strip('_')
    
    if not safe_name:
        return generate_run_id()
    
    # Check for existing run with same name, append timestamp if needed
    run_dir = RESULTS_ROOT / safe_name
    if run_dir.exists():
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_name = f"{safe_name}_{ts}"
    
    return safe_name


# ══════════════════════════════════════════════════════════════════════════════
# PAGE CONFIG
# ══════════════════════════════════════════════════════════════════════════════

st.set_page_config(
    page_title="S³ Momentum System",
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown("""
<style>
html, body, [class*="css"] { font-family: 'Inter', sans-serif; }
.block-container { padding-top: 5rem; padding-bottom: 2rem; max-width: 1400px; }
[data-testid="stSidebar"] {
    background: linear-gradient(180deg, #0A0A18 0%, #0D0D22 100%);
    border-right: 1px solid #1E1E3A;
}
button[data-baseweb="tab"] {
    font-size: 0.84rem; font-weight: 600; color: #8B9DB5; padding: 8px 18px;
}
button[data-baseweb="tab"]:hover { color: #A89FFF !important; }
button[data-baseweb="tab"][aria-selected="true"] {
    color: #6C63FF !important;
    border-bottom: 3px solid #6C63FF !important;
    background: rgba(108,99,255,0.06) !important;
}
[data-testid="metric-container"] {
    background: linear-gradient(135deg, rgba(108,99,255,0.09) 0%, rgba(0,200,150,0.05) 100%);
    border: 1px solid rgba(108,99,255,0.22);
    border-radius: 12px; padding: 16px 20px;
}
[data-testid="stMetricLabel"] {
    font-size: 0.76rem; color: #8B9DB5; font-weight: 600;
    letter-spacing: 0.04em; text-transform: uppercase;
}
[data-testid="stMetricValue"] { font-size: 1.45rem; font-weight: 800; color: #E8E4FF; }
.sec {
    font-size: 1rem; font-weight: 700; color: #C9B7FF;
    border-left: 4px solid #6C63FF; padding: 4px 12px;
    margin: 18px 0 10px; background: rgba(108,99,255,0.04); border-radius: 0 6px 6px 0;
}
.card {
    background: rgba(108,99,255,0.06); border: 1px solid rgba(108,99,255,0.18);
    border-radius: 10px; padding: 14px 18px; margin: 6px 0; line-height: 1.7;
}
.logo-text {
    font-size: 2.2rem; font-weight: 900;
    background: linear-gradient(135deg, #6C63FF 0%, #00C896 100%);
    -webkit-background-clip: text; -webkit-text-fill-color: transparent;
    letter-spacing: -0.02em;
}
.logo-sub { font-size: 0.78rem; color: #8B9DB5; margin-top: -4px; letter-spacing: 0.03em; }
.stButton > button[kind="primary"] {
    background: linear-gradient(135deg, #6C63FF, #8B5CF6) !important;
    border: none !important; border-radius: 8px !important; font-weight: 700 !important;
}
</style>
""", unsafe_allow_html=True)


# ══════════════════════════════════════════════════════════════════════════════
# HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def _ss(k, v):
    if k not in st.session_state:
        st.session_state[k] = v
    return st.session_state[k]

def _fmt(v, decimals=2, suffix=""):
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return "N/A"
    return f"{v:+.{decimals}f}{suffix}" if suffix == "%" else f"{v:.{decimals}f}"

def _color(v):
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return "gray"
    return "normal" if v >= 0 else "inverse"

def _section(txt):
    st.markdown(f'<div class="sec">{txt}</div>', unsafe_allow_html=True)

def _card(txt):
    st.markdown(f'<div class="card">{txt}</div>', unsafe_allow_html=True)

def _render_db_status():
    """Auto-connect to the shared market-data DB on open (downloading from
    MongoDB GridFS via .env if it isn't already on local disk), and show a
    small status indicator. Returns (ready: bool, error: str | None)."""
    local = local_status()

    if local["exists"]:
        st.success(f"✅ Connected — {local['size_mb']:.0f} MB")
        st.caption(local["path"])
        return True, None

    if not is_configured():
        st.error("MONGO_URI not set")
        st.caption("Add it to your .env file (see the S3 main system's .env) "
                   "or to Streamlit secrets, then reload.")
        return False, "MONGO_URI not configured"

    cfg = config_summary()
    st.caption(f"Connecting to {cfg['host'] or 'MongoDB'} …")

    from core.db_provisioning import test_connection
    _diag = test_connection()
    if not _diag["ok"]:
        st.error(f"❌ Connection failed: {_diag['detail']}")
        if st.button("🔄 Retry", key="db_retry"):
            st.rerun()
        return False, _diag["detail"]

    try:
        with st.spinner("📥 Downloading market data from MongoDB…"):
            ensure_database()
        st.success("✅ Download complete")
        st.rerun()
    except Exception as exc:
        st.error(f"❌ Connection failed: {exc}")
        if st.button("🔄 Retry", key="db_retry"):
            st.rerun()
        return False, str(exc)
    return True, None

# ══════════════════════════════════════════════════════════════════════════════
# SIDEBAR
# ══════════════════════════════════════════════════════════════════════════════

with st.sidebar:
    st.markdown('<div class="logo-text">S³</div>', unsafe_allow_html=True)
    st.markdown('<div class="logo-sub">Momentum Investment System</div>', unsafe_allow_html=True)
    st.divider()

    st.markdown("#### 🗄️ Database")
    db_ready, db_error = _render_db_status()
    st.divider()

    # ── Strategy mode selector ────────────────────────────────────────────────
    strategy_mode = "Momentum Based Investment"

    if db_ready and strategy_mode == "Momentum Based Investment":
        # Load data once to derive default min/max dates for controls
        _phases, _nifty_df, _stock_dict, _val = load_all_from_db(
            DB_LOCAL_PATH, threshold_pct=DEFAULT_PHASE_THRESHOLD_PCT
        )
        _min_d = _nifty_df.index.min().date() if not _nifty_df.empty else pd.Timestamp("2018-01-01").date()
        _max_d = _nifty_df.index.max().date() if not _nifty_df.empty else pd.Timestamp("2026-12-31").date()

        st.caption("**📅 Backtest Date Range & Initial Buy**")
        _dc1, _dc2 = st.columns(2)
        with _dc1:
            mom_start_date = st.date_input(
                "Start Date",
                value=_min_d,
                min_value=_min_d,
                max_value=_max_d,
                key="mom_start_date",
                help="Backtest start date. If 'Buy on Start Date' is checked, Cycle 1 buys the first set of stocks on this date."
            )
        with _dc2:
            mom_end_date = st.date_input(
                "End Date",
                value=_max_d,
                min_value=_min_d,
                max_value=_max_d,
                key="mom_end_date",
                help="Backtest end date. No trades will be initiated after this date."
            )
        mom_buy_on_start = st.checkbox(
            "Buy first set of stocks on Start Date",
            value=True,
            key="mom_buy_on_start",
            help="When checked: Cycle 1 immediately buys the selected stock basket on the Start Date. When unchecked: waits for the NIFTY rise trigger past Start Date."
        )
        st.divider()

        mom_leg_count = 2
        mom_pattern = ["Rise", "Fall"]
        mom_vol_pct = 25
        mom_vol_filter = "off"
        mom_vol_filter_n = 50
        mom_vf_windows = []
        mom_vol_filter_lookback = 100

        # ── Ranking Metric (dropdown) ─────────────────────────────────────────
        st.caption("**Ranking Metric**")
        mom_metric_label = st.selectbox(
            "Rank stocks by",
            ["ROC", "Volatility", "ROC*Vol", "Both (ROC ∩ Volatility)", "ROC / Volatility",
             "Beta (NIFTY Correlation)", "Beta / Volatility", "Beta × Volatility",
             "Std Dev / Downside Vol",
             "Off (→ Gate System: Momentum + Stability + Quality)"],
            key="mom_metric",
            help="ROC: rank by leg return. Volatility: rank by annualised leg vol. "
                 "ROC*Vol: product score. Both: ROC Top-N ∩ Vol Top-N. "
                 "ROC/Volatility: rank by ROC divided by vol (Sharpe-style). "
                 "Beta (NIFTY Correlation): rank by OLS beta of stock vs NIFTY daily ln-returns "
                 "(base day prepended so window-start return is included). "
                 "Beta/Volatility: rank by beta divided by vol — high β per unit of risk. "
                 "Beta×Volatility: rank by beta multiplied by vol — combined market exposure + risk. "
                 "Bottom-N selects lowest-beta stocks — most defensive / least market-correlated. "
                 "Off: opens the S3-main-parity Gate System — Momentum (ROC unscaled) → "
                 "Stability (beta only) → Quality (same pillars/weights as S3-main), run per "
                 "Rise/Fall entry-exit leg. See the '🚦 Gate System' tab for results & parameters.")
        mom_metric = ("roc" if mom_metric_label == "ROC"
                      else "vol" if mom_metric_label == "Volatility"
                      else "volxroc" if mom_metric_label == "ROC*Vol"
                      else "both" if mom_metric_label.startswith("Both")
                      else "roc_over_vol" if mom_metric_label.startswith("ROC /")
                      else "beta_over_vol" if mom_metric_label == "Beta / Volatility"
                      else "beta_x_vol" if mom_metric_label == "Beta × Volatility"
                      else "sd_over_dv" if mom_metric_label == "Std Dev / Downside Vol"
                      else "beta" if mom_metric_label.startswith("Beta")
                      else "gate" if mom_metric_label.startswith("Off")
                      else "off")
        mom_vol_dir = "low"

        # ── Gate System parameters (only shown when "Off" -> Gate System) ──────
        gate_params = None
        if mom_metric == "gate":
            import core.gate_system as _gs
            st.info(
                "**Gate System** (S3-main parity): Momentum = ROC unscaled only · "
                "Stability/Low-Vol = beta only · Quality = same 14-factor / 5-pillar "
                "system as the S3 main system. Runs once per Rise/Fall leg entry date.",
                icon="🚦")
            st.caption("**Pipeline (Sequential Gate Filtration)**")
            
            with st.expander("1. Momentum Discovery", expanded=True):
                _g_enable_mom = st.checkbox("Enable Momentum Gate", value=True, key="gate_enable_mom")
                mc1, mc2, mc3 = st.columns(3)
                with mc1:
                    _mom_sel_mode = st.selectbox("Selection", ["top_pct", "top_n"], index=0, key="gate_mom_sel")
                with mc2:
                    if _mom_sel_mode == "top_pct":
                        _mom_top_pct = st.number_input("Top %", min_value=0.01, max_value=1.0, value=0.30, step=0.05, key="gate_mom_pct")
                        _mom_top_n = 50
                    else:
                        _mom_top_n = st.number_input("Top N", min_value=1, value=50, step=1, key="gate_mom_n")
                        _mom_top_pct = 0.30
                with mc3:
                    _g_mom_col = st.selectbox("Column", ["momentum_unscaled", "momentum_scaled"], index=0, key="gate_mom_col")
                    _g_mom_norm = st.selectbox("Normalization", ["raw", "zscore", "minmax", "robust_zscore", "percentile"], index=0, key="gate_mom_norm")
                    
            with st.expander("2. Stability (Low Vol)", expanded=True):
                _g_enable_stab = st.checkbox("Enable Stability Gate", value=True, key="gate_enable_stab")
                sc1, sc2, sc3 = st.columns(3)
                with sc1:
                    _stab_sel_mode = st.selectbox("Selection ", ["top_pct", "top_n"], index=0, key="gate_stab_sel")
                with sc2:
                    if _stab_sel_mode == "top_pct":
                        _stab_top_pct = st.number_input("Top % ", min_value=0.01, max_value=1.0, value=0.50, step=0.05, key="gate_stab_pct")
                        _stab_top_n = 50
                    else:
                        _stab_top_n = st.number_input("Top N ", min_value=1, value=50, step=1, key="gate_stab_n")
                        _stab_top_pct = 0.50
                with sc3:
                    _g_stab_col = st.selectbox("Column ", ["beta", "semi_deviation"], index=0, key="gate_stab_col")
                    _g_stab_norm = st.selectbox("Normalization ", ["raw", "zscore", "minmax", "robust_zscore", "percentile"], index=0, key="gate_stab_norm")
            
            with st.expander("3. Quality Validation", expanded=True):
                _g_enable_qual = st.checkbox("Enable Quality Gate", value=True, key="gate_enable_qual")
                qc1, qc2, qc3 = st.columns(3)
                with qc1:
                    _g_qual_norm = st.selectbox("Normalization  ", ["zscore", "minmax", "robust_zscore", "percentile", "raw"], index=0, key="gate_qual_norm")
                with qc2:
                    _g_qual_rollup = st.selectbox("Rollup", ["median", "mean", "latest"], index=0, key="gate_qual_rollup")
                with qc3:
                    _g_min_qual = st.number_input("Min quality score", value=0.0, step=0.05, key="gate_min_qual")
                
                st.caption("Quality pillars (30/30/15/15/10) and the 14 underlying factors "
                           "match the S3 main system exactly and aren't editable here — see "
                           "the parameter table in the Export sheet.")

            with st.expander("4. Market Cap Distribution (Large, Mid, Small Cap)", expanded=True):
                _g_enable_cap = st.checkbox("Enable Market Cap Distribution", value=True, key="gate_enable_cap")
                cp1, cp2, cp3 = st.columns(3)
                with cp1:
                    _g_large_cap_pct = st.number_input("Large Cap %", min_value=0.0, max_value=1.0, value=0.50, step=0.05, key="gate_large_cap_pct")
                with cp2:
                    _g_mid_cap_pct = st.number_input("Mid Cap %", min_value=0.0, max_value=1.0, value=0.30, step=0.05, key="gate_mid_cap_pct")
                with cp3:
                    _g_small_cap_pct = st.number_input("Small Cap %", min_value=0.0, max_value=1.0, value=0.20, step=0.05, key="gate_small_cap_pct")

            gate_params = _gs.GateParams(
                enable_momentum=_g_enable_mom,
                enable_stability=_g_enable_stab,
                enable_quality=_g_enable_qual,
                enable_cap_filter=_g_enable_cap,
                large_cap_pct=_g_large_cap_pct,
                mid_cap_pct=_g_mid_cap_pct,
                small_cap_pct=_g_small_cap_pct,
                momentum_selection=_mom_sel_mode,
                momentum_top_pct=_mom_top_pct,
                momentum_top_n=int(_mom_top_n),
                momentum_column=_g_mom_col, 
                momentum_normalization=_g_mom_norm,
                stability_selection=_stab_sel_mode,
                stability_top_pct=_stab_top_pct,
                stability_top_n=int(_stab_top_n),
                stability_column=_g_stab_col, 
                stability_normalization=_g_stab_norm,
                quality_normalization=_g_qual_norm, 
                quality_rollup=_g_qual_rollup,
                min_quality_score=_g_min_qual,
            )

        # ── Volatility type ───────────────────────────────────────────────────
        if mom_metric in ("vol", "both", "volxroc", "roc_over_vol", "off", "beta_over_vol", "beta_x_vol"):
            mom_vol_type_lbl = st.selectbox(
                "Volatility type",
                ["Standard (annualised ln vol)", "Downside Volatility"],
                key="mom_vol_type",
                help="Standard: annualised std of ln daily returns. "
                     "Downside: only negative log-returns used (zero replaces positives), "
                     "then sqrt(mean of squared negatives) × sqrt(252).")
            mom_vol_type = "downside" if "Downside" in mom_vol_type_lbl else "standard"
        else:
            mom_vol_type = "standard"

        if mom_metric in ("vol", "both", "roc_over_vol"):
            _vd = st.radio("Volatility direction", ["Calmest (low)", "Most volatile (high)"],
                           key="mom_vol_dir_r", horizontal=True,
                           help="Low keeps calmest N; High keeps most volatile N.")
            mom_vol_dir = "low" if _vd.startswith("Calmest") else "high"

        # Beta info box — describes Top-N vs Bottom-N behaviour
        if mom_metric in ("beta", "beta_over_vol", "beta_x_vol"):
            _bov_note = (" Beta/Vol ranks β÷vol (high β per unit of risk)." if mom_metric == "beta_over_vol"
                         else " Beta×Vol ranks β×vol (combined exposure + risk)." if mom_metric == "beta_x_vol"
                         else "")
            st.info(
                f"**Beta selection** (base-day prepended → includes window-start return):{_bov_note}\n\n"
                "• **Bottom-N** → N stocks with **lowest score** per leg (most defensive) "
                "→ common → K smallest mean-score bought\n\n"
                "• **Top-N** → N stocks with **highest score** per leg (most aggressive) "
                "→ common → K largest mean-score bought",
                icon="ℹ️")

        # Std Dev / Downside Vol info box
        if mom_metric == "sd_over_dv":
            st.info(
                "**Std Dev / Downside Vol ratio** (σ ÷ DV):\n\n"
                "Computes Standard Deviation Volatility ÷ Downside Volatility per leg for each stock.\n\n"
                "• **Top-N** → N stocks with **highest ratio** per leg → common → K largest mean-ratio bought\n\n"
                "• **Bottom-N** → N stocks with **lowest ratio** per leg → common → K smallest mean-ratio bought\n\n"
                "No High/Low vol direction — ranking is purely by the σ÷DV ratio.",
                icon="📊")

        if mom_metric not in ("off",):
            _mside_lbl = st.radio(
                "Selection side", ["Top-N (best) & common", "Bottom-N (worst) & common"],
                horizontal=True, key="side_mom",
                help="Top-N picks the best stocks per leg and intersects; "
                     "Bottom-N picks the worst (laggard basket).")
            mom_selection_side = "top" if _mside_lbl.startswith("Top") else "bottom"
        else:
            mom_selection_side = "top"

        st.divider()

        # ── Enable Fall Entry & Exit ──────────────────────────────────────────
        st.caption("**Selection Window**")
        mom_use_fixed_fall = st.checkbox(
            "Enable Fall Entry & Exit",
            value=False, key="mom_fixed_fall",
            help="When ON: rankings use TWO trailing windows anchored to the previous "
                 "cycle's 10%+ fall dates.\n"
                 "• Fall Entry window: last N days ending at the prev PEAK date\n"
                 "• Fall Exit  window: last N days ending at the prev TROUGH date\n"
                 "Top-N stocks per window → intersection → Top-K bought.\n"
                 "Cycle 1 is skipped (no prior fall). Buy date = prev sell date.\n"
                 "When OFF: use last two scheduled legs (auto rise/fall detection).")

        if mom_use_fixed_fall:
            st.caption("ℹ️ Two windows anchored to the **previous cycle's fall** "
                       "(peak & trough dates). Configure the lookback below.")
            _fe_col, _fx_col = st.columns(2)
            with _fe_col:
                mom_fall_entry_days = int(st.number_input(
                    "Fall Entry Days", min_value=5, max_value=3650, value=100, step=1,
                    key="mom_fe_days",
                    help="Trailing trading-day lookback ending at the prev cycle's PEAK date "
                         "(start of the fall). e.g. 100 = last 100 closes before the peak."))
            with _fx_col:
                mom_fall_exit_days = int(st.number_input(
                    "Fall Exit Days", min_value=5, max_value=3650, value=100, step=1,
                    key="mom_fx_days",
                    help="Trailing trading-day lookback ending at the prev cycle's TROUGH date "
                         "(end of the fall). e.g. 100 = last 100 closes before the trough."))
            st.caption(f"Fall Entry: {mom_fall_entry_days}d ending at prev **peak** · "
                       f"Fall Exit: {mom_fall_exit_days}d ending at prev **trough**")
        else:
            mom_fall_entry_days = 100
            mom_fall_exit_days  = 100
            st.caption("Auto: last two scheduled legs (Rise + Fall, or Rise + dynamic fall window).")

        st.divider()

        # ── Stock Selection (unified Top N) ───────────────────────────────────
        if mom_metric == "off":
            st.caption("**Volatility Selection (no ROC)**")
            _dir = st.radio("Filter direction",
                            ["Low volatility (calmest)", "High volatility (most volatile)"],
                            horizontal=True, key="mom_vf_dir_off")
            mom_vol_filter = "low" if _dir.startswith("Low") else "high"
            mom_vol_dir = mom_vol_filter
            mom_top_n = int(st.number_input(
                "Top N per window", min_value=1, max_value=2000, value=50, step=1,
                key="mom_topn_off",
                help="Top-N stocks per window. Common of both windows = candidate pool."))
            mom_vol_days_off = int(st.number_input(
                "Trailing days (volatility window)", min_value=5, max_value=3650,
                value=100, step=1, key="mom_vol_days_off",
                help="Trailing trading days for each reference date."))
            mom_top_k = int(st.number_input(
                "Common Stocks to Buy", min_value=1, max_value=100, value=10, step=1,
                key="mom_k_off"))
            st.caption(f"top-{mom_top_n} per {mom_vol_days_off}d window → intersection → buy {mom_top_k}")
            mom_vol_filter_n = mom_top_n
            mom_vf_windows = [("fall_entry", mom_vol_days_off), ("fall_exit", mom_vol_days_off)]
            mom_vol_filter_lookback = mom_vol_days_off
        else:
            st.caption("**Stock Selection**")
            mom_top_n = int(st.number_input(
                "Top N (Rise & Fall legs)", min_value=1, max_value=2000, value=50, step=1,
                key="mom_top_n",
                help="Single Top-N applied to BOTH Rise and Fall legs (and fixed Fall Entry/Exit "
                     "windows when enabled). Stocks common across legs → final K bought."))
            mom_top_k = int(st.number_input(
                "Common Stocks to Buy (K)", min_value=1, max_value=100, value=10, step=1,
                key="mom_k",
                help="Final basket size — top-K from the common set."))

        st.divider()

        st.caption("**NIFTY Cycle Thresholds**")
        mom_nifty_pct = st.slider("NIFTY rise % (BUY trigger & SELL recovery)",
                                  0.5, 20.0, 5.0, 0.5, key="mom_nifty_pct")
        mom_fall = st.slider("Fall: % drop from running peak", 1.0, 40.0, 10.0, 0.5,
                             key="mom_fall")
        mom_max_hold = st.slider("Keep-holding window (years, no-fall)", 0.5, 5.0, 2.0, 0.5,
                                 key="mom_hold")
        mom_exact_trigger = st.checkbox(
            "Exact Trigger Mode",
            value=False,
            key="mom_exact_trigger",
            help=(
                "**Exact Trigger Mode** separates SELL and BUY into two distinct dates:\n\n"
                f"• **SELL** exactly when NIFTY drops n% from its running peak (fall confirm date)\n\n"
                f"• **BUY** again only when NIFTY recovers n% from the trough (recovery date)\n\n"
                "In the default mode, sell and the next buy happen on the same date (recovery). "
                "In Exact Trigger Mode, you exit at the exact fall and re-enter at the exact recovery — "
                "buy and sell dates are always different."
            ),
        )

        st.divider()

        st.caption("**Portfolio**")
        mc_a, mc_b = st.columns(2)
        with mc_a:
            mom_capital = st.number_input("Initial Capital (₹)", 10_000, value=100_000,
                                          step=5_000, key="mom_cap")
        with mc_b:
            mom_reinvest = st.checkbox("Reinvest profits", value=True, key="mom_re")
        st.divider()

        st.caption("**Backtest Identity**")
        mom_trial_name = st.text_input(
            "Trial Name",
            value="",
            placeholder="Optional: e.g., 'ROC_Top50_Gate_v1'",
            key="mom_trial_name",
            help="Custom name for this backtest run. Used as the run folder name. "
                 "If empty, a timestamp-based name is generated. "
                 "Duplicates get a timestamp suffix to avoid overwriting."
        )

        mom_run_btn = st.button("🚀 Run Momentum Analysis", type="primary",
                                use_container_width=True)

# ══════════════════════════════════════════════════════════════════════════════
# DB NOT READY — welcome / connecting screen
# ══════════════════════════════════════════════════════════════════════════════

if not db_ready:
    st.markdown('<div class="logo-text" style="text-align:center;padding:40px 0 10px">S³</div>', unsafe_allow_html=True)
    st.markdown("<h3 style='text-align:center;color:#8B9DB5'>Multi-Leg Stock Selection System</h3>",
                unsafe_allow_html=True)
    st.markdown(f"""
    <div style='max-width:680px;margin:0 auto;text-align:left'>
    <div class='card'>
    <b>Connecting to the shared market-data database…</b><br><br>
    Data now loads automatically from the same MongoDB-backed store used by the
    S3 main system (via <code>.env</code>) — no more manual Excel uploads.
    {"<br><br>❌ " + db_error if db_error else ""}
    </div>
    <div class='card' style='margin-top:12px'>
    <b>NIFTY Threshold Logic</b><br><br>
    • <b>Entry:</b> After the pattern window, wait until NIFTY rises by N% from phase start → BUY<br>
    • <b>Exit:</b> During the following sell phase, wait until NIFTY falls by M% → SELL<br>
    • If threshold never hit → fallback to phase start (entry) or phase end (exit)
    </div>
    <div class='card' style='margin-top:12px'>
    <b>New: Reshuffle Threshold &amp; Three-Leg Selection</b><br><br>
    • <b>Reshuffle (optional):</b> if NIFTY falls more than your chosen % (e.g. -10%) during a
      Fall leg, that window is skipped ('reshuffled') and the reason is recorded.<br>
    • <b>Three-leg common stocks:</b> trading candidates are the common stocks across the
      pattern legs <i>and</i> the entry segment (Fall exit → NIFTY entry-trigger), computed
      <b>per window</b> — so each window has its own stock set.
    </div>
    </div>
    """, unsafe_allow_html=True)
    st.stop()


# ══════════════════════════════════════════════════════════════════════════════
# LOAD DATA — straight from the DB, on open (no Excel upload)
# ══════════════════════════════════════════════════════════════════════════════

with st.spinner("📥 Loading market data from the database…"):
    phases, nifty_df, stock_dict, val = load_all_from_db(
        DB_LOCAL_PATH, threshold_pct=DEFAULT_PHASE_THRESHOLD_PCT
    )

if nifty_df.empty or not stock_dict:
    st.error("❌ The database is connected but contains no price data yet. "
             "Run the data extractor in the S3 main system first, then reload.")
    st.stop()

# ── Data validation banner ────────────────────────────────────────────────────
st.markdown("""
<div style='background:rgba(108,99,255,0.06);border:1px solid rgba(108,99,255,0.2);
     border-radius:10px;padding:10px 16px;margin-bottom:8px'>
<span style='font-size:0.75rem;color:#8B9DB5;font-weight:600;letter-spacing:0.05em'>
✅ DATA LOADED FROM DATABASE
</span>
</div>""", unsafe_allow_html=True)

c1, c2, c3, c4 = st.columns(4)
c1.metric("📅 Total Phases",       val["n_phases"],   help="Rise/Fall market phases auto-generated from NIFTY 500 swings ≥6%")
c2.metric("📈 NIFTY Data Rows",    val["nifty_rows"], help="Daily NIFTY close prices available for analysis")
c3.metric("🏢 NIFTY 500 Stocks", val["n_stocks"],
          help="Tickers loaded from the DB, filtered to NIFTY 500 constituents only")
c4.metric("📊 Date Range", val.get("date_range") or "—",
          help="Full date range covered by the phase schedule")

_dropped = val.get("dropped_non_nifty500") or []
if _dropped:
    st.caption(f"🚫 {len(_dropped)} non-NIFTY-500 ticker(s) excluded from the universe "
               f"(benchmarks / other indices found in the DB): "
               f"{', '.join(_dropped[:15])}{' …' if len(_dropped) > 15 else ''}")

st.divider()

# ══════════════════════════════════════════════════════════════════════════════
# COMPUTE PHASE RETURNS (cached)
# ══════════════════════════════════════════════════════════════════════════════

with st.spinner("Computing phase returns..."):
    # ── Feature A: optional low-volatility filter (runs BEFORE top-N) ─────────
    _excluded_lowvol = ()
    if 'lowvol_on' in dir() and lowvol_on:
        _vols = {}
        for _tkr, _sdf in stock_dict.items():
            try:
                _dr = _sdf["close"].pct_change().dropna()
            except Exception:
                continue
            if len(_dr) > 1:
                _vols[_tkr] = float(_dr.std())
        if _vols:
            _keep = {t for t, _ in sorted(_vols.items(), key=lambda kv: kv[1])[:int(lowvol_n)]}
            _excluded_lowvol = tuple(t for t in _vols if t not in _keep)
        _n_before = len(stock_dict)
        _n_after = _n_before - len(_excluded_lowvol)
        st.info(f"Low-vol filter: universe reduced from {_n_before} → {_n_after} stocks "
                f"(keeping the {lowvol_n} calmest by daily volatility)")

    returns_df = compute_all_phase_returns(
        stock_dict, phases, nifty_df, excluded_tickers=_excluded_lowvol
    )

if returns_df.empty:
    st.error("No phase returns could be computed. Check your data files.")
    st.stop()

# ══════════════════════════════════════════════════════════════════════════════
# MOMENTUM BASED INVESTMENT  —  self-contained branch (stops before short-term)
# ══════════════════════════════════════════════════════════════════════════════

if strategy_mode == "Momentum Based Investment":
    m_tab_overview, m_tab_cycles, m_tab_invest, m_tab_vis, m_tab_cands, m_tab_legrank, m_tab_export, m_tab_history = st.tabs([
        "📋 Overview", "🔁 Cycles & Trades", "💰 Investment Analysis",
        "📈 NIFTY Cycle View", "🔍 Candidates", "🏆 Leg Rankings", "⬇️ Export",
        "📚 Previous Runs",
    ])

    # ── Overview (always available) ───────────────────────────────────────────
    with m_tab_overview:
        _section("Momentum Strategy — How This Run Works")
        _beta_side_lbl = "lowest β (defensive)" if mom_selection_side == "bottom" else "highest β (aggressive)"
        _metric_display = {
            "roc": "ROC", "vol": f"Volatility ({mom_vol_dir})",
            "volxroc": "ROC×Vol", "both": f"Both — ROC ∩ Volatility ({mom_vol_dir})",
            "roc_over_vol": "ROC / Volatility", "off": "Off (volatility filter only)",
            "beta": f"Beta (NIFTY ln-ret, base-day prepended) — {_beta_side_lbl}",
            "beta_over_vol": f"Beta / Volatility — {_beta_side_lbl}",
            "beta_x_vol": f"Beta × Volatility — {_beta_side_lbl}",
            "sd_over_dv": "Std Dev / Downside Vol (σ÷DV ratio)",
        }.get(mom_metric, mom_metric)
        _vol_type_display = "Downside" if mom_vol_type == "downside" else "Standard"
        _date_range_desc = f"<b>Date Range:</b> {mom_start_date.strftime('%d-%b-%Y')} → {mom_end_date.strftime('%d-%b-%Y')}"
        _start_buy_badge = (" &nbsp;·&nbsp; <b style='color:#00C896'>⚡ Buy 1st Basket on Start Date</b>"
                             if mom_buy_on_start else " &nbsp;·&nbsp; Wait for NIFTY trigger after Start Date")
        if mom_metric == "off":
            _card(
                f"{_date_range_desc}{_start_buy_badge}<br>"
                f"<b>Selection (Off — pure volatility):</b> top-<b>{mom_top_n}</b> "
                f"{'calmest' if mom_vol_filter=='low' else 'most-volatile'} stocks per "
                f"<b>{mom_vol_days_off}-day</b> window ({_vol_type_display} vol) at fall entry & exit → "
                f"intersection → buy <b>{mom_top_k}</b>. No leg ranking.<br>"
                f"<b>Buy</b> +{mom_nifty_pct:g}% · <b>Fall</b> −{mom_fall:g}% · "
                f"<b>Hold max</b> {mom_max_hold:g}yr"
            )
        else:
            _win_desc = (f"Fall Entry {mom_fall_entry_days}d@prev-peak + "
                          f"Fall Exit {mom_fall_exit_days}d@prev-trough"
                         if mom_use_fixed_fall
                         else "Last two scheduled legs (auto rise/fall detection)")
            _exit_line  = f" · <b>Fall</b> −{mom_fall:g}% · <b>Hold max</b> {mom_max_hold:g}yr"
            _card(
                f"{_date_range_desc}{_start_buy_badge}<br>"
                f"<b>Window:</b> {_win_desc} &nbsp;·&nbsp; "
                f"<b>Rank by:</b> {_metric_display} &nbsp;·&nbsp; "
                f"<b>Vol type:</b> {_vol_type_display} &nbsp;·&nbsp; "
                f"<b>Top-N:</b> {mom_top_n} &nbsp;·&nbsp; <b>K:</b> {mom_top_k}<br>"
                f"<b>Buy</b> +{mom_nifty_pct:g}%{_exit_line}"
            )

        _section("Phase Schedule")
        _phase_disp = phases.copy()
        _phase_disp["entry_date"] = _phase_disp["entry_date"].dt.strftime("%d-%b-%Y").fillna("")
        _phase_disp["exit_date"]  = _phase_disp["exit_date"].dt.strftime("%d-%b-%Y").fillna("")

        def _hl_trade_m(s):
            return ["background-color:#C6EFCE;color:#1A6B3C" if v == "Rise"
                    else "background-color:#FFC7CE;color:#9C0006" if v == "Fall"
                    else "" for v in s]
        st.dataframe(_phase_disp.style.apply(_hl_trade_m, subset=["trade"]),
                     width="stretch", height=300)

        # ── NIFTY Trade Analysis (index-level performance per leg, NIFTY 500 exclusive) ──
        _section("📈 NIFTY Trade Analysis")
        try:
            from core.nifty500_universe import universe_badge
            st.caption(universe_badge(stock_dict))
        except Exception:
            pass
        _nta = phases.copy()
        _nta["nifty_entry"] = _nta["entry_date"].map(
            lambda d: float(nifty_df.loc[nifty_df.index <= d, "close"].iloc[-1])
            if (nifty_df.index <= d).any() else np.nan)
        _nta["nifty_exit"] = _nta["exit_date"].map(
            lambda d: float(nifty_df.loc[nifty_df.index <= d, "close"].iloc[-1])
            if (nifty_df.index <= d).any() else np.nan)
        _nta["nifty_leg_return_pct"] = (_nta["nifty_exit"] / _nta["nifty_entry"] - 1.0) * 100
        _rise_legs = _nta[_nta["trade"] == "Rise"]
        _fall_legs = _nta[_nta["trade"] == "Fall"]
        nc1, nc2, nc3, nc4 = st.columns(4)
        nc1.metric("Rise Legs", len(_rise_legs),
                   f"avg {_rise_legs['nifty_leg_return_pct'].mean():+.2f}%" if not _rise_legs.empty else None)
        nc2.metric("Fall Legs", len(_fall_legs),
                   f"avg {_fall_legs['nifty_leg_return_pct'].mean():+.2f}%" if not _fall_legs.empty else None)
        nc3.metric("Total Legs", len(_nta))
        nc4.metric("NIFTY 500 Universe Size", len(stock_dict))
        _nta_disp = _nta[["phase_id", "trade", "entry_date", "exit_date", "days",
                          "nifty_entry", "nifty_exit", "nifty_leg_return_pct"]].copy()
        _nta_disp["entry_date"] = _nta_disp["entry_date"].dt.strftime("%d-%b-%Y").fillna("")
        _nta_disp["exit_date"] = _nta_disp["exit_date"].dt.strftime("%d-%b-%Y").fillna("")
        st.dataframe(
            _nta_disp.rename(columns=lambda c: c.replace("_", " ").title())
                     .style.apply(_hl_trade_m, subset=["Trade"])
                     .format({"Nifty Entry": "{:.1f}", "Nifty Exit": "{:.1f}",
                              "Nifty Leg Return Pct": "{:+.2f}"}, na_rep="—"),
            width="stretch", height=280, hide_index=True)

    # ── Run the momentum engine (button or cached) ────────────────────────────
    mom_run_local = "mom_run_btn" in locals() and mom_run_btn
    if mom_run_local or "mom_results" in st.session_state:
        if mom_run_local:

            with st.spinner("Running momentum analysis..."):
                _mres = run_momentum(
                    nifty_df, stock_dict, returns_df, phases, mom_pattern,
                    metric=mom_metric, vol_direction=mom_vol_dir,
                    vol_type=mom_vol_type,
                    top_n=int(mom_top_n),
                    top_k_common=int(mom_top_k),
                    nifty_pct=float(mom_nifty_pct), fall_pct=float(mom_fall),
                    leg_count=int(mom_leg_count),
                    max_hold_years=float(mom_max_hold),
                    vol_pct=int(mom_vol_pct),
                    selection_side=mom_selection_side,
                    vol_filter=mom_vol_filter,
                    vol_filter_n=int(mom_vol_filter_n),
                    vf_windows=mom_vf_windows,
                    vol_filter_lookback_days=int(mom_vol_filter_lookback),
                    use_fixed_fall=bool(mom_use_fixed_fall),
                    fall_entry_days=int(mom_fall_entry_days),
                    fall_exit_days=int(mom_fall_exit_days),
                    exact_trigger_mode=bool(mom_exact_trigger),
                    start_date=mom_start_date,
                    end_date=mom_end_date,
                    buy_on_start_date=bool(mom_buy_on_start),
                    gate_params=gate_params,
                    db_path=DB_LOCAL_PATH,
                )
            st.session_state["mom_results"] = _mres
            st.session_state["mom_cfg"] = {
                "pattern": mom_pattern, "metric": mom_metric, "vol_dir": mom_vol_dir,
                "vol_type": mom_vol_type,
                "top_n": int(mom_top_n),
                "top_k": int(mom_top_k), "nifty_pct": float(mom_nifty_pct),
                "fall": float(mom_fall), "leg_count": int(mom_leg_count),
                "max_hold": float(mom_max_hold),
                "vol_pct": int(mom_vol_pct),
                "selection_side": mom_selection_side,
                "vol_filter": mom_vol_filter,
                "vol_filter_n": int(mom_vol_filter_n),
                "vf_windows": mom_vf_windows,
                "vol_filter_lookback_days": int(mom_vol_filter_lookback),
                "use_fixed_fall": bool(mom_use_fixed_fall),
                "fall_entry_days": int(mom_fall_entry_days),
                "fall_exit_days": int(mom_fall_exit_days),
                "exact_trigger_mode": bool(mom_exact_trigger),
                "start_date": str(mom_start_date),
                "end_date": str(mom_end_date),
                "buy_on_start_date": bool(mom_buy_on_start),
                "capital": float(mom_capital), "reinvest": bool(mom_reinvest),
                "gate_params": gate_params,
            }

        _mres = st.session_state["mom_results"]
        _mcfg = st.session_state.get("mom_cfg", {})
        m_per_trade = _mres["per_trade_df"]
        m_cycle     = _mres["cycle_df"]
        m_cands     = _mres["candidates_df"]
        m_status    = _mres["status_df"]
        m_elig      = _mres.get("eligible_ranks_df", pd.DataFrame())
        m_legrank   = _mres.get("leg_rank_df", pd.DataFrame())
        m_audit     = _mres.get("audit_df", pd.DataFrame())
        m_vol_audit = _mres.get("vol_audit_df", pd.DataFrame())
        _m_cap      = float(_mcfg.get("capital", 100_000))
        _m_re       = bool(_mcfg.get("reinvest", True))

        # ── Persist backtest results ───────────────────────────────────────────
        if mom_run_local:
            _run_id = _make_safe_run_id(st.session_state.get("mom_trial_name", ""))
            try:
                save_config(_run_id, _mcfg)
                save_execution_metadata(_run_id, {
                    "run_id": _run_id,
                    "started_at": datetime.now().isoformat(),
                    "completed_at": datetime.now().isoformat(),
                    "status": "completed",
                    "metric": mom_metric,
                    "cycles": len(m_cycle) if not m_cycle.empty else 0,
                    "trades": len(m_per_trade) if not m_per_trade.empty else 0,
                })
                if not m_per_trade.empty:
                    save_trades_parquet(_run_id, m_per_trade)
                if not m_cycle.empty:
                    save_dataframe_parquet(_run_id, m_cycle, "cycles.parquet")
                if not m_cands.empty:
                    save_dataframe_parquet(_run_id, m_cands, "candidates.parquet")
                if not m_status.empty:
                    save_dataframe_parquet(_run_id, m_status, "status.parquet")
                if not m_elig.empty:
                    save_dataframe_parquet(_run_id, m_elig, "eligible_ranks.parquet")
                if not m_legrank.empty:
                    save_dataframe_parquet(_run_id, m_legrank, "leg_rankings.parquet")
                if not m_audit.empty:
                    save_dataframe_parquet(_run_id, m_audit, "audit.parquet")
                if not m_vol_audit.empty:
                    save_dataframe_parquet(_run_id, m_vol_audit, "vol_audit.parquet")
                
                # Compute portfolio NAV/equity from investment analysis
                try:
                    ia = compute_investment_analysis(m_per_trade, initial_capital=_m_cap, 
                                                      alloc_mode="equal", reinvest=_m_re)
                    if ia and "equity_curve" in ia and ia["equity_curve"] is not None:
                        save_portfolio_nav_parquet(_run_id, ia["equity_curve"])
                    if ia and "metrics" in ia and ia["metrics"]:
                        save_metrics(_run_id, ia["metrics"])
                except Exception:
                    pass  # NAV/metrics are optional
                
                st.session_state["current_run_id"] = _run_id
                st.toast(f"✅ Backtest saved (run: {_run_id})", icon="💾")
            except Exception as e:
                st.warning(f"Could not persist backtest: {e}")

        # ── Cycles & Trades ────────────────────────────────────────────────────
        with m_tab_cycles:
            if m_cycle.empty:
                st.warning("No cycles were generated. Check the status table below — "
                           "the buy trigger or common-stock conditions may not have been met.")
            else:
                k1, k2, k3, k4 = st.columns(4)
                k1.metric("Completed / Total Cycles",
                          f"{int((m_cycle['status']=='Completed cycle').sum())} / {len(m_cycle)}")
                k2.metric("Total Trades", int(len(m_per_trade)))
                _avg = m_per_trade["return_pct"].mean() if not m_per_trade.empty else 0
                k3.metric("Avg Trade Return", f"{_avg:+.2f}%", delta_color=_color(_avg))
                _wr = (m_per_trade["return_pct"] > 0).mean()*100 if not m_per_trade.empty else 0
                k4.metric("Win Rate", f"{_wr:.1f}%")

                _section("Cycle Ledger (buy → peak → fall → recovery → sell)")
                cyc = m_cycle.copy()
                for dc in ["buy_trigger_date", "peak_date", "fall_confirm_date",
                           "sell_date"]:
                    if dc in cyc.columns:
                        cyc[dc] = pd.to_datetime(cyc[dc], errors="coerce").dt.strftime("%d-%b-%Y").fillna("")
                show_cols = [c for c in ["cycle", "source_window", "buy_trigger_date",
                             "nifty_at_buy", "peak_close", "peak_date", "fall_confirm_date",
                             "trough_close", "sell_date", "nifty_at_sell", "n_stocks",
                             "avg_return_pct", "held_days", "status"] if c in cyc.columns]
                disp = cyc[show_cols].rename(columns=lambda c: c.replace("_", " ").title())

                def _hl_cyc(row):
                    try:
                        v = float(row.get("Avg Return Pct", 0))
                    except Exception:
                        v = 0
                    col = "#C6EFCE" if v > 0 else "#FFC7CE" if v < 0 else ""
                    fg  = "#1A6B3C" if v > 0 else "#9C0006" if v < 0 else ""
                    return [f"background-color:{col};color:{fg}" if c == "Avg Return Pct" else ""
                            for c in row.index]
                st.dataframe(disp.style.apply(_hl_cyc, axis=1)
                             .format({"Avg Return Pct": "{:+.2f}"}, na_rep="—"),
                             width="stretch", height=320, hide_index=True)

                # Cycle inspector
                _section("Inspect a Cycle — common stocks & per-stock trades")
                cyc_labels = [f"Cycle {int(r['cycle'])}  (window {int(r['source_window'])} · "
                              f"{pd.Timestamp(r['buy_trigger_date']).strftime('%d-%b-%Y')} → "
                              f"{pd.Timestamp(r['sell_date']).strftime('%d-%b-%Y')})"
                              for _, r in m_cycle.iterrows()]
                sel_cyc = st.selectbox("Cycle", cyc_labels, key="mom_cyc_sel")
                sel_cyc_no = int(m_cycle.iloc[cyc_labels.index(sel_cyc)]["cycle"])

                _crow = m_cycle[m_cycle["cycle"] == sel_cyc_no]
                _fun = (str(_crow.iloc[0].get("selection_funnel", "")) if not _crow.empty else "")
                if _fun:
                    st.caption(f"**Selection funnel:** {_fun}")

                if not m_cands.empty:
                    cd = m_cands[m_cands["cycle"] == sel_cyc_no].copy()
                    if not cd.empty:
                        lead = [c for c in ["common_rank", "beta_rank", "ticker",
                                "mean_roc", "mean_vol", "mean_beta_nifty", "mean_corr_nifty",
                                "selected_to_buy", "traded"] if c in cd.columns]
                        legc = [c for c in cd.columns if "|" in c]
                        cshow = cd[lead + legc].sort_values("common_rank")
                        cshow = cshow.rename(columns=lambda c: c.replace("_", " ").title())

                        def _hl_buy_m(row):
                            return (["background-color:#C6EFCE;color:#1A6B3C"] * len(row)
                                    if str(row.get("Selected To Buy")) == "True" else [""] * len(row))
                        st.markdown("**Common stocks ranked for this cycle** (highlighted = bought)")
                        st.dataframe(cshow.style.apply(_hl_buy_m, axis=1),
                                     width="stretch", height=300, hide_index=True)

                if not m_per_trade.empty:
                    tt = m_per_trade[m_per_trade["cycle"] == sel_cyc_no].copy()
                    if not tt.empty:
                        for dc in ["entry_date", "exit_date"]:
                            tt[dc] = pd.to_datetime(tt[dc], errors="coerce").dt.strftime("%d-%b-%Y").fillna("")
                        cols = [c for c in ["ticker", "entry_date", "entry_price",
                                "exit_date", "exit_price", "return_pct", "nifty_return",
                                "alpha", "days_held"] if c in tt.columns]
                        tshow = tt[cols].rename(columns=lambda c: c.replace("_", " ").title())
                        st.markdown("**Per-stock trades for this cycle**")
                        st.dataframe(
                            tshow.style.format({"Return Pct": "{:+.2f}", "Alpha": "{:+.2f}",
                                                "Nifty Return": "{:+.2f}", "Entry Price": "{:.2f}",
                                                "Exit Price": "{:.2f}"}, na_rep="—"),
                            width="stretch", height=320, hide_index=True)

                # ── Verification: ALL eligible stocks ranked (quartiles) ────────
                if not m_elig.empty:
                    ev = m_elig[m_elig["cycle"] == sel_cyc_no].copy()
                    if not ev.empty:
                        for dc in ["entry_date", "exit_date"]:
                            ev[dc] = pd.to_datetime(ev[dc], errors="coerce").dt.strftime("%d-%b-%Y").fillna("")
                        ev["Traded?"] = ev["traded"].map(lambda v: "✓" if bool(v) else "")
                        ecols = [c for c in ["rank", "ticker", "quartile",
                                 "entry_date", "exit_date", "entry_price", "exit_price", "return_pct",
                                 "nifty_return", "alpha", "Traded?"] if c in ev.columns]
                        eshow = ev[ecols].sort_values("rank")
                        eshow = eshow.rename(columns=lambda c: c.replace("_", " ").title()
                                             if c != "Traded?" else c)
                        n_tr_e = int(ev["traded"].sum())
                        st.markdown(f"**Eligible Stock Ranking — verification** "
                                    f"({len(ev)} eligible · {n_tr_e} bought/sold, highlighted)")

                        def _hl_traded_e(row):
                            return (["background-color:#C6EFCE;color:#1A6B3C;font-weight:600"] * len(row)
                                    if str(row.get("Traded?")) == "✓" else [""] * len(row))
                        st.dataframe(
                            eshow.style.apply(_hl_traded_e, axis=1)
                                 .format({"Return Pct": "{:+.2f}", "Alpha": "{:+.2f}",
                                          "Nifty Return": "{:+.2f}", "Entry Price": "{:.2f}",
                                          "Exit Price": "{:.2f}"}, na_rep="—"),
                            width="stretch", height=380, hide_index=True)

                _section("Full Trade Log")
                full = m_per_trade.copy()
                for dc in ["entry_date", "exit_date", "buy_trigger_date", "sell_date_cycle"]:
                    if dc in full.columns:
                        full[dc] = pd.to_datetime(full[dc], errors="coerce").dt.strftime("%d-%b-%Y").fillna("")
                fcols = [c for c in ["cycle", "source_window", "ticker", "buy_trigger_date",
                         "entry_date", "entry_price", "sell_date_cycle", "exit_date",
                         "exit_price", "return_pct", "nifty_return", "alpha", "days_held",
                         "status"] if c in full.columns]
                st.dataframe(full[fcols].rename(columns=lambda c: c.replace("_", " ").title()),
                             width="stretch", height=400, hide_index=True)

            # Status table (skipped windows etc.)
            if not m_status.empty:
                with st.expander("ℹ️ Window-by-window status (incl. skipped windows)", expanded=False):
                    sd = m_status.copy()
                    for dc in ["pattern_start", "pattern_end", "buy_trigger_date", "sell_date"]:
                        if dc in sd.columns:
                            sd[dc] = pd.to_datetime(sd[dc], errors="coerce").dt.strftime("%d-%b-%Y").fillna("")
                    st.dataframe(sd.rename(columns=lambda c: c.replace("_", " ").title()),
                                 width="stretch", height=360, hide_index=True)

        # ── Investment Analysis (reuses the shared module) ──────────────────────
        with m_tab_invest:
            if m_per_trade.empty or m_per_trade["window_idx"].nunique() < 2:
                st.warning("Need at least 2 completed cycles for investment analysis.")
            else:
                _ia = compute_investment_analysis(
                    m_per_trade, initial_capital=_m_cap,
                    alloc_mode="equal", reinvest=_m_re)
                if _ia is None:
                    st.warning("Not enough cycles to compute investment analysis.")
                else:
                    m = _ia["metrics"]
                    _section(f"💰 Investment Analysis — {fmt_inr_full(_m_cap)} Initial Capital")
                    r1 = st.columns(4)
                    r1[0].metric("Final Equity", fmt_inr_full(m["final_equity"]))
                    r1[1].metric("Total P/L", fmt_inr_full(m["total_pl"]),
                                 delta=f"{m['total_pl_pct']:+.1f}%")
                    r1[2].metric("CAGR", "N/A" if np.isnan(m["cagr"]) else f"{m['cagr']:+.2f}%")
                    r1[3].metric("Max Drawdown", f"{m['mdd_pct']:.1f}%", delta_color="inverse")
                    r2 = st.columns(4)
                    r2[0].metric("CAR / MDD", "N/A" if np.isnan(m["calmar"]) else f"{m['calmar']:.2f}")
                    r2[1].metric("Sharpe", "N/A" if np.isnan(m["sharpe"]) else f"{m['sharpe']:.2f}")
                    r2[2].metric("Win Rate", f"{m['win_rate']:.1f}%")
                    r2[3].metric("Avg Holding Days",
                                 "N/A" if np.isnan(m["avg_holding_days"]) else f"{m['avg_holding_days']:.0f}")

                    eq = _ia["equity_curve"].dropna(subset=["exit_date"]).sort_values("exit_date")
                    if not eq.empty:
                        up = float(eq["equity_inr"].iloc[-1]) >= _m_cap
                        fig = go.Figure()
                        fig.add_trace(go.Scatter(
                            x=eq["exit_date"], y=eq["equity_inr"], mode="lines+markers",
                            line=dict(color="#00C896" if up else "#FF4B6E", width=2.5),
                            marker=dict(size=6), name="Equity (₹)"))
                        fig.add_hline(y=_m_cap, line_dash="dash", line_color="#8B9DB5")
                        fig.update_layout(height=340, margin=dict(t=20, b=20, l=10, r=10),
                            paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                            font_color="#C9C9C9", title="Equity Curve (sell date of each cycle)",
                            xaxis_title="Cycle sell date", yaxis_title="Equity (₹)",
                            xaxis=dict(color="#8B9DB5"), yaxis=dict(color="#8B9DB5", gridcolor="#2A2A4A"),
                            title_font=dict(color="#C9C9C9", size=14))
                        st.plotly_chart(fig, use_container_width=True)

                    yw = _ia["yearwise"]
                    if not yw.empty:
                        _section("Year-wise Breakdown")
                        d = yw.copy()
                        d["Invested(₹)"] = d["Invested"].map(fmt_inr_full)
                        d["Profit(₹)"]   = d["Profit"].map(fmt_inr_full)
                        d["Equity(₹)"]   = d["Cumulative Equity"].map(fmt_inr_full)
                        d["Return%"]     = d["Return%"].map(lambda v: f"{v:+.2f}%" if pd.notna(v) else "—")
                        d["Win Rate"]    = d["Win Rate"].map(lambda v: f"{v:.1f}%" if pd.notna(v) else "—")
                        d["Year"]        = d["Year"].astype(str)
                        st.dataframe(d[["Year", "Windows", "Trades", "Invested(₹)", "Profit(₹)",
                                        "Return%", "Win Rate", "Equity(₹)"]],
                                     width="stretch", hide_index=True)

        # ── NIFTY Cycle View ────────────────────────────────────────────────────
        with m_tab_vis:
            if m_cycle.empty:
                st.warning("Run the momentum analysis to see cycle charts.")
            else:
                _section("NIFTY Path — Buy → Peak → Fall → Sell")
                _card("The chart shows NIFTY across one cycle. "
                      "<span style='color:#00C896'><b>▲ BUY</b></span> on the +rise trigger, "
                      "<span style='color:#FFD700'><b>★ PEAK</b></span> the running high, "
                      "<span style='color:#FF4B6E'><b>● FALL</b></span> where the drop is confirmed, "
                      "<span style='color:#FF4B6E'><b>▼ SELL</b></span> on the recovery.")
                cyc_labels2 = [f"Cycle {int(r['cycle'])}  ({pd.Timestamp(r['buy_trigger_date']).strftime('%d-%b-%Y')} → "
                               f"{pd.Timestamp(r['sell_date']).strftime('%d-%b-%Y')})"
                               for _, r in m_cycle.iterrows()]
                sel2 = st.selectbox("Cycle", cyc_labels2, key="mom_vis_sel")
                row = m_cycle.iloc[cyc_labels2.index(sel2)]
                b_d = pd.Timestamp(row["buy_trigger_date"]); s_d = pd.Timestamp(row["sell_date"])
                seg = nifty_df[(nifty_df.index >= b_d - pd.Timedelta(days=10)) &
                               (nifty_df.index <= s_d + pd.Timedelta(days=10))]
                if not seg.empty:
                    fig = go.Figure()
                    fig.add_trace(go.Scatter(x=seg.index, y=seg["close"], mode="lines",
                                  line=dict(color="#6C63FF", width=2), name="NIFTY"))

                    def _mark(dt, label, color, sym, pos):
                        if dt is None or pd.isna(dt):
                            return
                        dt = pd.Timestamp(dt)
                        rr = seg[seg.index >= dt]
                        if rr.empty:
                            rr = seg[seg.index <= dt]
                        if rr.empty:
                            return
                        y = float(rr["close"].iloc[0])
                        fig.add_trace(go.Scatter(x=[dt], y=[y], mode="markers+text",
                            marker=dict(size=14, color=color, symbol=sym,
                                        line=dict(color="#fff", width=1.5)),
                            text=[label], textposition=pos,
                            textfont=dict(color=color, size=11, family="Arial Black"),
                            name=label))
                    _mark(row.get("buy_trigger_date"), "BUY", "#00C896", "triangle-up", "top center")
                    _mark(row.get("peak_date"), "PEAK", "#FFD700", "star", "top center")
                    _mark(row.get("fall_confirm_date"), "FALL", "#FF4B6E", "circle", "bottom center")
                    _mark(row.get("sell_date"), "SELL", "#FF4B6E", "triangle-down", "bottom center")
                    if pd.notna(row.get("peak_close")):
                        lvl = float(row["peak_close"]) * (1 - _mcfg.get("fall", 10)/100)
                        fig.add_hline(y=lvl, line_dash="dash", line_color="#FF4B6E",
                                      annotation_text=f"Peak −{_mcfg.get('fall',10):g}%",
                                      annotation_font=dict(color="#FF4B6E"))
                    fig.update_layout(height=460, margin=dict(t=30, b=20, l=20, r=20),
                        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                        font_color="#C9C9C9", xaxis=dict(color="#8B9DB5", gridcolor="#2A2A4A"),
                        yaxis=dict(color="#8B9DB5", gridcolor="#2A2A4A"),
                        legend=dict(font=dict(color="#C9C9C9"), orientation="h", y=1.1),
                        hovermode="x unified")
                    st.plotly_chart(fig, use_container_width=True)



        # ── Candidates ───────────────────────────────────────────────────────────
        with m_tab_cands:
            if m_cands.empty:
                st.warning("Run the analysis to view cycle candidates.")
            else:
                _section("Cycle Candidates & Sequential Gate Filtration")
                cyc_labels_cand = [f"Cycle {int(r['cycle'])}  ({pd.Timestamp(r['buy_trigger_date']).strftime('%d-%b-%Y')} → "
                                   f"{pd.Timestamp(r['sell_date']).strftime('%d-%b-%Y')})"
                                   for _, r in m_cycle.iterrows()]
                sel_c_lbl = st.selectbox("Cycle", cyc_labels_cand, key="mom_cand_tab_sel")
                sel_c_no = int(m_cycle.iloc[cyc_labels_cand.index(sel_c_lbl)]["cycle"])

                cd_view = m_cands[m_cands["cycle"] == sel_c_no].copy()
                if not cd_view.empty:
                    cd_disp = cd_view.rename(columns=lambda c: c.replace("_", " ").title())
                    for c in cd_disp.columns:
                        if cd_disp[c].dtype == object:
                            cd_disp[c] = cd_disp[c].astype(str).replace({"nan": "—", "None": "—", "<NA>": "—"})
                    st.dataframe(cd_disp, width="stretch", height=450, hide_index=True)

        # ── Leg Rankings ─────────────────────────────────────────────────────────
        with m_tab_legrank:
            if m_legrank.empty:
                st.warning("Run the analysis to view per-leg stock rankings.")
            else:
                _section("Per-Leg Stock Rankings & Gate Scorecards")
                cyc_labels_lr = [f"Cycle {int(r['cycle'])}  ({pd.Timestamp(r['buy_trigger_date']).strftime('%d-%b-%Y')} → "
                                 f"{pd.Timestamp(r['sell_date']).strftime('%d-%b-%Y')})"
                                 for _, r in m_cycle.iterrows()]
                sel_lr_lbl = st.selectbox("Cycle", cyc_labels_lr, key="mom_legrank_tab_sel")
                sel_lr_no = int(m_cycle.iloc[cyc_labels_lr.index(sel_lr_lbl)]["cycle"])

                lr_view = m_legrank[m_legrank["cycle"] == sel_lr_no].copy()
                if not lr_view.empty:
                    for dc in ["entry_date", "exit_date", "roc_start_date", "roc_end_date", "vol_start_date", "vol_end_date"]:
                        if dc in lr_view.columns:
                            lr_view[dc] = pd.to_datetime(lr_view[dc], errors="coerce").dt.strftime("%d-%b-%Y").fillna("")
                    lr_disp = lr_view.rename(columns=lambda c: c.replace("_", " ").title())
                    for c in lr_disp.columns:
                        if lr_disp[c].dtype == object:
                            lr_disp[c] = lr_disp[c].astype(str).replace({"nan": "—", "None": "—", "<NA>": "—"})
                    st.dataframe(lr_disp, width="stretch", height=450, hide_index=True)

        # ── Export ───────────────────────────────────────────────────────────────
        with m_tab_export:
            _section("Download Momentum Report (single interactive workbook)")
            _card("One workbook with a <b>Dashboard</b> — type a cycle number in one cell and "
                  "every <b>View</b> sheet (Trades, Eligible Ranking, Candidates, Leg Rankings, "
                  "NIFTY Path, Monthwise) auto-updates to that cycle. Plus the enriched "
                  "<b>Cycle Ledger (all)</b> (per-cycle Q1–Q4, Q3+Q4, Q3+Q4 %, click ▶ to drill "
                  "in), Overall Monthwise, and <b>all</b> the detailed sheets — Portfolio Summary, "
                  "Cycle Status, Candidates, Eligible Stock Ranking, Leg Rankings, Per-Cycle "
                  "Equity, Common Stocks P&L, Trade Log, Stock Summary, NIFTY Cycle Path, "
                  "Yearwise Summary, Phase Schedule, and a per-cycle detail sheet for each cycle. "
                  "(Auto-filtering works in Excel 2007+ / LibreOffice — allow recalculation when prompted.)")
            if not m_per_trade.empty:
                _run_id = st.session_state.get("current_run_id", generate_run_id())
                
                # Async export button
                if st.button("Generate Excel File (Async)", type="primary", key="mom_xl_async"):
                    # Prepare data for async export
                    export_data = {
                        "per_trade_df": m_per_trade.copy(),
                        "cycle_df": m_cycle.copy(),
                        "candidates_df": m_cands.copy(),
                        "status_df": m_status.copy(),
                        "phases": phases,
                        "config": _mcfg.copy(),
                        "eligible_ranks_df": m_elig.copy(),
                        "leg_rank_df": m_legrank.copy(),
                        "audit_df": m_audit.copy(),
                        "vol_audit_df": m_vol_audit.copy(),
                        "nifty_df": nifty_df.copy(),
                        "stock_dict": {k: v.copy() for k, v in stock_dict.items()},
                        "returns_df": returns_df.copy(),
                        "initial_capital": _m_cap,
                        "reinvest": _m_re,
                    }
                    
                    from export.async_exporter import export_excel_async
                    job_id = export_excel_async(_run_id, export_data, generate_momentum_interactive_excel)
                    st.session_state["excel_job_id"] = job_id
                    st.session_state["excel_run_id"] = _run_id
                    st.success("✓ Excel generation started in background. Check status below.")
                    st.rerun()
                
                # Show export status if job exists
                if "excel_job_id" in st.session_state:
                    from export.async_exporter import get_async_export_status, get_async_export_result, get_async_export_error
                    job_id = st.session_state["excel_job_id"]
                    status = get_async_export_status(job_id)
                    
                    st.write(f"**Export Status:** {status}")
                    
                    if status == "completed":
                        xlb = get_async_export_result(job_id)
                        if xlb:
                            # Save to persistent storage
                            try:
                                save_excel_report(st.session_state["excel_run_id"], xlb)
                                st.toast("✅ Excel saved to persistent storage", icon="💾")
                            except Exception:
                                pass
                            
                            # Build descriptive filename
                            from export.momentum_exporter import _make_run_name as _mrnfn
                            _rn = (_mrnfn(_mcfg)
                                   .replace(" ", "_").replace("/", "ov")
                                   .replace("%", "pct").replace("+", ""))
                            _excel_filename = f"{_rn}.xlsx"
                            
                            st.download_button(
                                "⬇️ Download Excel Workbook",
                                data=xlb,
                                file_name=_excel_filename,
                                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                                type="primary")
                            st.info("✅ Export completed! The file is also saved in your backtest history.")
                        
                    elif status == "failed":
                        error = get_async_export_error(job_id)
                        st.error(f"Export failed: {error}")
                    
                    elif status in ("pending", "running"):
                        st.info("⏳ Export running in background... Click to refresh.")
                        if st.button("🔄 Refresh Status"):
                            st.rerun()
                
                # Also keep the sync option for quick downloads
                with st.expander("🔄 Synchronous Export (blocks UI)", expanded=False):
                    if st.button("Generate Excel File (Sync)", type="secondary", key="mom_xl_sync"):
                        with st.spinner("Building workbook (this is comprehensive — a moment)..."):
                            try:
                                xlb = generate_momentum_interactive_excel(
                                    per_trade_df=m_per_trade, cycle_df=m_cycle,
                                    candidates_df=m_cands, status_df=m_status,
                                    phases=phases, config=_mcfg,
                                    eligible_ranks_df=m_elig, leg_rank_df=m_legrank,
                                    audit_df=m_audit, vol_audit_df=m_vol_audit,
                                    nifty_df=nifty_df, stock_dict=stock_dict,
                                    returns_df=returns_df,
                                    initial_capital=_m_cap, reinvest=_m_re)
                                # Build descriptive filename
                                from export.momentum_exporter import _make_run_name as _mrnfn
                                _rn = (_mrnfn(_mcfg)
                                       .replace(" ", "_").replace("/", "ov")
                                       .replace("%", "pct").replace("+", ""))
                                _excel_filename = f"{_rn}.xlsx"
                                st.session_state["mom_xl_bytes"] = xlb
                                st.session_state["mom_xl_filename"] = _excel_filename
                                st.success("✓ Ready! Open the Dashboard and type a cycle number.")
                            except Exception as e:
                                st.error(f"Excel generation failed: {e}")
                    
                    if st.session_state.get("mom_xl_bytes"):
                        st.download_button(
                            "⬇️ Download Excel Workbook (Sync)",
                            data=st.session_state["mom_xl_bytes"],
                            file_name=st.session_state.get("mom_xl_filename", "momentum.xlsx"),
                            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                            type="primary")
            else:
                st.info("Run the analysis first to enable export.")

    # ── Previous Runs ──────────────────────────────────────────────────────────────
    with m_tab_history:
        _section("Previous Backtest Runs")
        _card("Click a run to load its results and Excel report without re-running the backtest.")
        
        runs = list_runs()
        if not runs:
            st.info("No previous backtest runs found. Run a backtest to save results.")
        else:
            for run in runs:
                run_id = run["run_id"]
                meta = run.get("metadata", {})
                cfg = run.get("config", {}) or {}
                
                # Run header
                col1, col2, col3, col4 = st.columns([3, 2, 2, 1])
                with col1:
                    metric = cfg.get("metric", "unknown")
                    top_n = cfg.get("top_n", "?")
                    top_k = cfg.get("top_k", "?")
                    st.write(f"**{run_id}**  ·  {metric}  ·  Top-{top_n}  ·  K={top_k}")
                with col2:
                    if meta:
                        started = meta.get("started_at", "")
                        if started:
                            st.write(f"🕐 {started[:19].replace('T', ' ')}")
                with col3:
                    if meta:
                        cycles = meta.get("cycles", 0)
                        trades = meta.get("trades", 0)
                    else:
                        cycles = 0
                        trades = 0
                    st.write(f"🔁 {cycles} cycles  ·  📈 {trades} trades")
                with col4:
                    # Load button
                    if st.button("Load", key=f"load_{run_id}"):
                        # Load all data for this run
                        try:
                            # Load config
                            loaded_cfg = load_config(run_id)
                            st.session_state["mom_cfg"] = loaded_cfg
                            
                            # Load all result dataframes
                            loaded_trades = load_trades_parquet(run_id)
                            loaded_cycles = load_dataframe_parquet(run_id, "cycles.parquet")
                            loaded_cands  = load_dataframe_parquet(run_id, "candidates.parquet")
                            loaded_status = load_dataframe_parquet(run_id, "status.parquet")
                            loaded_elig   = load_dataframe_parquet(run_id, "eligible_ranks.parquet")
                            loaded_legrank = load_dataframe_parquet(run_id, "leg_rankings.parquet")
                            loaded_audit  = load_dataframe_parquet(run_id, "audit.parquet")
                            loaded_vol_audit = load_dataframe_parquet(run_id, "vol_audit.parquet")
                            
                            # Reconstruct mom_results from loaded data
                            st.session_state["mom_results"] = {
                                "per_trade_df": loaded_trades if loaded_trades is not None else pd.DataFrame(),
                                "cycle_df": loaded_cycles if loaded_cycles is not None else pd.DataFrame(),
                                "candidates_df": loaded_cands if loaded_cands is not None else pd.DataFrame(),
                                "status_df": loaded_status if loaded_status is not None else pd.DataFrame(),
                                "eligible_ranks_df": loaded_elig if loaded_elig is not None else pd.DataFrame(),
                                "leg_rank_df": loaded_legrank if loaded_legrank is not None else pd.DataFrame(),
                                "audit_df": loaded_audit if loaded_audit is not None else pd.DataFrame(),
                                "vol_audit_df": loaded_vol_audit if loaded_vol_audit is not None else pd.DataFrame(),
                            }
                            
                            # Load NAV
                            nav_df = load_portfolio_nav_parquet(run_id)
                            if nav_df is not None:
                                st.session_state["mom_nav_df"] = nav_df
                            
                            # Load Excel report
                            excel_bytes = load_excel_report(run_id)
                            if excel_bytes:
                                st.session_state["mom_xl_bytes"] = excel_bytes
                                from export.momentum_exporter import _make_run_name as _mrnfn
                                _rn = (_mrnfn(loaded_cfg)
                                       .replace(" ", "_").replace("/", "ov")
                                       .replace("%", "pct").replace("+", ""))
                                st.session_state["mom_xl_filename"] = f"{_rn}.xlsx"
                            
                            st.session_state["current_run_id"] = run_id
                            st.success(f"✅ Loaded run {run_id}")
                            st.rerun()
                        except Exception as e:
                            st.error(f"Failed to load run: {e}")
                    
                    # Delete button
                    if st.button("🗑️", key=f"delete_{run_id}", help="Delete this run"):
                        st.session_state[f"confirm_delete_{run_id}"] = True
                        st.rerun()

                # Show details in expander
                with st.expander(f"Details for {run_id}"):
                    if meta:
                        st.json(meta)
                    if cfg:
                        st.write("**Configuration:**")
                        st.json(cfg)
                
                # Confirmation dialog for delete
                if st.session_state.get(f"confirm_delete_{run_id}", False):
                    st.warning(f"⚠️ Are you sure you want to delete run **{run_id}**? This action is irreversible.")
                    cdel1, cdel2, _ = st.columns([1, 1, 4])
                    with cdel1:
                        if st.button("✅ Yes, delete", key=f"confirm_yes_{run_id}", type="primary"):
                            try:
                                deleted = delete_run(run_id)
                                if deleted:
                                    st.success(f"✅ Deleted run {run_id}")
                                    # Clear confirmation state
                                    del st.session_state[f"confirm_delete_{run_id}"]
                                    # Clear loaded data if this was the loaded run
                                    if st.session_state.get("current_run_id") == run_id:
                                        for key in ["mom_results", "mom_cfg", "mom_nav_df", "mom_xl_bytes", "mom_xl_filename", "current_run_id"]:
                                            st.session_state.pop(key, None)
                                    st.rerun()
                                else:
                                    st.warning(f"⚠️ Run folder {run_id} not found (already deleted or missing). Removing from list.")
                                    del st.session_state[f"confirm_delete_{run_id}"]
                                    st.rerun()
                            except Exception as e:
                                st.error(f"Error deleting run: {e}")
                                del st.session_state[f"confirm_delete_{run_id}"]
                                st.rerun()
                    with cdel2:
                        if st.button("❌ Cancel", key=f"confirm_no_{run_id}"):
                            del st.session_state[f"confirm_delete_{run_id}"]
                            st.rerun()
                
                st.divider()
            
            # Download previously generated Excel if available
            if "mom_xl_bytes" in st.session_state and st.session_state.get("current_run_id"):
                st.download_button(
                    "⬇️ Download Excel for Loaded Run",
                    data=st.session_state["mom_xl_bytes"],
                    file_name=st.session_state.get("mom_xl_filename", "momentum.xlsx"),
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    type="primary"
                )

    st.stop()
