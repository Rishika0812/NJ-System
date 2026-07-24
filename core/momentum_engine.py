"""
S³ Core — Momentum Engine
=========================
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from typing import Optional
import streamlit as st

from core.multi_leg_engine import (
    find_pattern_windows,
    _nifty_near,
    _entry_price,
    _exit_price,
    _window_vol,
    _annualized_vol,
)
from core.phase_engine import quartile_label


# ─────────────────────────────────────────────────────────────────────────────
# Beta (market-correlation) helpers — using ln (log base-e) returns
# ─────────────────────────────────────────────────────────────────────────────

def _compute_beta(stock_prices: pd.Series, nifty_df: pd.DataFrame,
                  start, end) -> dict:
    """Compute OLS beta and Pearson correlation of stock vs NIFTY using daily
    ln returns over [start, end].

    Method: prepend the last available price BEFORE the window start as the
    base so that the window's first day return is included in the calculation.
    E.g. window 22-Jan → 05-Feb uses 21-Jan as base → 11 ln returns.

    Returns dict with keys: beta, corr_nifty, n_obs.  All NaN when insufficient data.
    """
    out = {"beta": float("nan"), "corr_nifty": float("nan"), "n_obs": 0}
    if stock_prices is None or nifty_df is None or nifty_df.empty:
        return out
    s, e = pd.Timestamp(start), pd.Timestamp(end)

    # ── Window prices ─────────────────────────────────────────────────────────
    stock_win = stock_prices[(stock_prices.index >= s) & (stock_prices.index <= e)]
    nifty_win = nifty_df["close"][(nifty_df.index >= s) & (nifty_df.index <= e)]

    # ── Prepend last price BEFORE window as base (user's manual method) ───────
    stock_pre = stock_prices[stock_prices.index < s]
    nifty_pre = nifty_df["close"][nifty_df.index < s]
    stock_sub = pd.concat([stock_pre.iloc[[-1]], stock_win]) if not stock_pre.empty else stock_win
    nifty_sub = pd.concat([nifty_pre.iloc[[-1]], nifty_win]) if not nifty_pre.empty else nifty_win

    if len(stock_sub) < 3 or len(nifty_sub) < 3:
        return out

    # ── ln returns — first return is now the window-start day (not NaN) ──────
    stock_lr = np.log(stock_sub / stock_sub.shift(1)).dropna()
    nifty_lr = np.log(nifty_sub / nifty_sub.shift(1)).dropna()

    # ── Align on common dates ─────────────────────────────────────────────────
    common_idx = stock_lr.index.intersection(nifty_lr.index)
    if len(common_idx) < 3:
        return out
    x = nifty_lr.loc[common_idx].values
    y = stock_lr.loc[common_idx].values
    var_x = float(np.var(x, ddof=1))
    var_y = float(np.var(y, ddof=1))
    if var_x == 0 or np.isnan(var_x):
        return out
    beta = float(np.cov(y, x, ddof=1)[0, 1] / var_x)
    corr = float(np.corrcoef(y, x)[0, 1]) if var_y > 1e-12 else float("nan")
    out.update(beta=round(beta, 4), corr_nifty=round(corr, 4) if not np.isnan(corr) else corr, n_obs=len(common_idx))
    return out


# ─────────────────────────────────────────────────────────────────────────────
# NIFTY scan helpers
# ─────────────────────────────────────────────────────────────────────────────

def _nifty_rise_trigger(nifty, start, rise_pct):
    base = _nifty_near(nifty, pd.Timestamp(start), "forward")
    if base is None:
        return None, 0.0, 0.0
    if rise_pct <= 0:
        return pd.Timestamp(start), base, base
    sub = nifty[nifty.index >= pd.Timestamp(start)]
    if sub.empty:
        return None, base, base
    level = base * (1.0 + rise_pct / 100.0)
    hits = sub[sub["close"] >= level]
    if hits.empty:
        return None, base, float(sub["close"].iloc[-1])
    return hits.index[0], base, float(hits["close"].iloc[0])


def _nifty_peak_fall(nifty, start, fall_pct):
    sub = nifty[nifty.index >= pd.Timestamp(start)]
    out = {"peak_close": None, "peak_date": None, "fall_date": None, "fall_close": None}
    if sub.empty:
        return out
    closes = sub["close"].astype(float)
    # running peak via cummax (vectorised)
    running_peak = closes.cummax()
    peak_close = float(running_peak.iloc[-1])
    peak_date  = closes.index[closes == running_peak.iloc[-1]][0] if peak_close > -np.inf else None
    out.update(peak_close=peak_close, peak_date=peak_date)
    if fall_pct > 0:
        threshold = running_peak * (1.0 - fall_pct / 100.0)
        fall_mask = closes <= threshold
        if fall_mask.any():
            fall_idx = fall_mask.idxmax()  # first True
            # peak is the running peak up to that point
            pk_val  = float(running_peak.loc[fall_idx])
            pk_dt   = closes.index[closes.index <= fall_idx][
                        (closes.loc[:fall_idx] == pk_val).values
                      ]
            pk_date = pk_dt[-1] if len(pk_dt) else peak_date
            out.update(peak_close=pk_val, peak_date=pk_date,
                       fall_date=fall_idx, fall_close=float(closes.loc[fall_idx]))
    return out


def _find_last_significant_fall(nifty_df: pd.DataFrame, before: pd.Timestamp,
                                fall_pct: float = 10.0) -> dict:
    """Scan NIFTY backward from `before` to find the most recent peak→trough
    sequence where NIFTY fell at least fall_pct%.

    Returns {peak_date, peak_close, trough_date, trough_close} or all None if not found.

    Algorithm: walk backward through NIFTY closes; maintain a running
    highest-close (peak candidate) and lowest-close (trough candidate);
    the moment we find a trough that is >=fall_pct% below ANY prior peak,
    record the pair.  We walk the FULL history before `before` and keep
    the MOST RECENT qualifying pair.
    """
    sub = nifty_df[nifty_df.index < before].sort_index()
    if sub.empty:
        return {"peak_date": None, "peak_close": None, "trough_date": None, "trough_close": None}

    closes = sub["close"].values
    dates  = sub.index.values
    n = len(closes)

    best_peak_idx = None
    best_trough_idx = None
    best_trough_date = None   # track most recent qualifying pair

    # Forward scan: find peak→trough pairs where fall >= fall_pct
    peak_idx = 0
    for i in range(1, n):
        if closes[i] > closes[peak_idx]:
            peak_idx = i
        else:
            drop_pct = (closes[peak_idx] - closes[i]) / closes[peak_idx] * 100
            if drop_pct >= fall_pct:
                # This is a qualifying fall.  Keep scanning for a lower trough.
                trough_idx = i
                for j in range(i + 1, n):
                    if closes[j] < closes[trough_idx]:
                        trough_idx = j
                    elif closes[j] > closes[trough_idx] * (1 + fall_pct / 200):
                        # Recovery started — stop tracking this trough
                        break
                # Record if this is more recent than previous best
                if best_trough_date is None or pd.Timestamp(dates[trough_idx]) > best_trough_date:
                    best_peak_idx   = peak_idx
                    best_trough_idx = trough_idx
                    best_trough_date = pd.Timestamp(dates[trough_idx])
                # Restart peak search from trough
                peak_idx = trough_idx

    if best_peak_idx is None:
        return {"peak_date": None, "peak_close": None, "trough_date": None, "trough_close": None}

    return {
        "peak_date":   pd.Timestamp(dates[best_peak_idx]),
        "peak_close":  float(closes[best_peak_idx]),
        "trough_date": pd.Timestamp(dates[best_trough_idx]),
        "trough_close": float(closes[best_trough_idx]),
    }


def _nifty_trough_recovery(nifty, start, recovery_pct):
    sub = nifty[nifty.index >= pd.Timestamp(start)]
    out = {"trough_close": None, "trough_date": None, "recovery_date": None, "recovery_close": None}
    if sub.empty:
        return out
    trough, trough_date = np.inf, None
    for dt, c in sub["close"].items():
        c = float(c)
        if c < trough:
            trough, trough_date = c, dt
        if recovery_pct <= 0:
            out.update(trough_close=trough, trough_date=trough_date, recovery_date=dt, recovery_close=c)
            return out
        if trough > 0 and c >= trough * (1.0 + recovery_pct / 100.0):
            out.update(trough_close=trough, trough_date=trough_date, recovery_date=dt, recovery_close=c)
            return out
    out.update(trough_close=trough, trough_date=trough_date)
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Volatility helpers
# ─────────────────────────────────────────────────────────────────────────────

def _downside_vol(series: pd.Series) -> float:
    """Annualised Downside Volatility — exact step-by-step formula.

    NOTE: For ranking purposes all closes in the leg window are used.

    Step 1 : r_t = ln(Close_t / Close_{t-1})
             Log return for each consecutive pair of closes.

    Step 2 : Convert positive returns to 0, keep negative returns as-is.
             r_t_neg = r_t  if r_t < 0  else  0

    Step 3 : Square every value (including the zeroed positives).
             squared_t = r_t_neg ** 2

    Step 4 : Average the squared values over all N returns.
             avg = (1/N) * sum(squared_t)          [N = total return count]

    Step 5 : Annualise.
             AnnualisedDownsideVol = sqrt(avg) * sqrt(252)
    """
    if series is None or len(series) < 2:
        return float("nan")
    # Keep only positive prices (log needs > 0)
    s = series[series > 0]
    if len(s) < 2:
        return float("nan")

    # Step 1 — log returns between consecutive closes
    lr = np.log(s / s.shift(1)).replace([np.inf, -np.inf], np.nan).dropna()
    if len(lr) < 2:
        return float("nan")

    # Step 2 — convert positive returns to 0, keep negatives
    r_neg = np.where(lr.values < 0, lr.values, 0.0)

    # Require at least 2 negative return days for a reliable downside vol estimate.
    # With 0 or 1 negative days:
    #   0 negative days → avg_sq = 0 → DV = 0 → ROC/DV undefined (÷0)
    #   1 negative day  → DV estimate based on a single observation — unreliable
    #                     and produces extreme ROC/DV scores when the return
    #                     magnitude is tiny (e.g. one -0.005% day → DV ≈ 0.0008
    #                     → score = ROC / 0.0008 = 10000+)
    # Both cases return NaN so the stock is excluded from ROC/Vol ranking.
    n_neg = int(np.sum(r_neg < 0))
    if n_neg < 2:
        return float("nan")

    # Step 3 & 4 — square and average (over ALL N returns, including zeroed positives)
    avg_sq = float(np.mean(r_neg ** 2))
    if np.isnan(avg_sq) or avg_sq <= 0:
        return float("nan")

    # Step 5 — annualise: sqrt(avg) * sqrt(252)
    return float(np.sqrt(avg_sq) * np.sqrt(252))


def _vol_func(series: pd.Series, vol_type: str = "standard") -> float:
    """Dispatch to standard or downside volatility."""
    if vol_type == "downside":
        return _downside_vol(series)
    return float(_annualized_vol(series))


def _leg_vol_detail(stock, start, end, vol_type: str = "standard") -> dict:
    out = {"vol": float("nan"), "start": None, "end": None, "n_days": 0}
    if stock is None:
        return out
    s, e = pd.Timestamp(start), pd.Timestamp(end)
    if e <= s:
        e = s + pd.Timedelta(days=1)
    sub = stock.loc[s:e, ["close"]]
    if sub.empty:
        return out
    # Prepend last close before window for first log return
    pre_idx = stock.index.searchsorted(s)
    if pre_idx > 0:
        sub_for_vol = pd.concat([stock["close"].iloc[pre_idx - 1:pre_idx].to_frame(), sub])
    else:
        sub_for_vol = sub
    v = _vol_func(sub_for_vol["close"], vol_type) if len(sub_for_vol) >= 2 else float("nan")
    out.update(vol=(round(float(v), 6) if v == v else float("nan")),
               start=sub.index[0], end=sub.index[-1], n_days=int(len(sub)))
    return out


def _leg_vol_detail_with_recency(stock, start, end, vol_type: str = "standard",
                                  max_gap_days: int = 365) -> dict:
    """Wrapper that checks the stock has data near leg_end."""
    out = {"vol": float("nan"), "start": None, "end": None, "n_days": 0}
    if stock is None:
        return out
    e = pd.Timestamp(end)
    recent = stock[(stock.index <= e)]
    if recent.empty:
        return out
    if (e - recent.index[-1]).days > max_gap_days:
        return out   # stock has no data near leg end
    return _leg_vol_detail(stock, start, end, vol_type)


def _window_vol(stock, start, end, vol_type: str = "standard") -> float:
    return _leg_vol_detail(stock, start, end, vol_type)["vol"]


def _leg_roc_detail(returns_df, leg, stock_dict, tickers):
    """
    Compute per-ticker ROC and vol dates for one leg.

    ROC is always computed from ALL price data in the leg window
    so that ranking is purely based on raw price performance.
    """
    leg_entry = pd.Timestamp(leg["entry"])
    leg_exit = pd.Timestamp(leg["exit"])
    if leg_exit <= leg_entry:
        leg_exit = leg_entry + pd.Timedelta(days=1)
    out = {}
    for tkr in tickers:
        sdf = stock_dict.get(tkr)
        if sdf is None:
            continue
        # Use ALL rows in the phase window for ranking
        sub = sdf[(sdf.index >= leg_entry) & (sdf.index <= leg_exit)]
        if sub.empty or len(sub) < 2:
            continue
        epx, edate = float(sub["close"].iloc[0]), sub.index[0]
        xpx, xdate = float(sub["close"].iloc[-1]), sub.index[-1]
        if epx <= 0:
            continue
        # ROC from full phase window (first close → last close, all trading days)
        roc = round((xpx - epx) / epx * 100, 4)
        out[tkr] = {
            "roc": roc,
            "entry_date": edate, "exit_date": xdate,
        }
    return out


def _leg_roc_map(returns_df, leg, stock_dict, tickers):
    return {t: d["roc"] for t, d in _leg_roc_detail(returns_df, leg, stock_dict, tickers).items()}


def _leg_vol_map(stock_dict, tickers, entry, exit, vol_type: str = "standard"):
    out = {}
    for tkr in tickers:
        d = _leg_vol_detail(stock_dict.get(tkr), entry, exit, vol_type)
        if d["vol"] == d["vol"]:
            out[tkr] = d["vol"]
    return out

def _fixed_leg_roc_vol_detail(stock_dict: dict, tickers: list, as_of,
                               n_trading_days: int, vol_type: str = "standard") -> dict:
    """ROC and volatility for a FIXED trailing-N window ending at `as_of`.

    Returns {ticker: {roc, vol, entry_date, exit_date, n_days}}
    Tickers with < 2 closes in the window are excluded.
    """
    as_of_ts = pd.Timestamp(as_of)
    n = int(max(2, n_trading_days))
    _cal_days = int(n * 1.55) + 30
    _cal_start = as_of_ts - pd.Timedelta(days=_cal_days)
    out = {}
    for tkr in tickers:
        sdf = stock_dict.get(tkr)
        if sdf is None:
            continue
        # Slice the calendar window once (boolean index on DatetimeIndex is fast)
        sub_window = sdf.loc[_cal_start:as_of_ts, ["close"]]
        if len(sub_window) < 2:
            continue
        sub = sub_window.tail(n)
        # Prepend the close immediately before the tail(n) window start.
        # IMPORTANT: must use sub.index[0] (tail start), NOT _cal_start,
        # because the calendar window is wider than n trading days and
        # searchsorted(_cal_start) would point to a row weeks earlier,
        # producing a spuriously large first log return and inflating vol.
        _pre = sdf.loc[:sub.index[0], ["close"]].iloc[:-1].tail(1)
        sub_vol = pd.concat([_pre, sub]) if not _pre.empty else sub
        if len(sub) < 2:
            continue
        first_close = float(sub["close"].iloc[0])
        last_close  = float(sub["close"].iloc[-1])
        if first_close <= 0:
            continue
        roc = round((last_close - first_close) / first_close * 100, 4)
        v = _vol_func(sub_vol["close"], vol_type)
        out[tkr] = {
            "roc": roc,
            "vol": (round(float(v), 6) if v == v else float("nan")),
            "entry_date": sub.index[0],
            "exit_date":  sub.index[-1],
            "n_days":     int(len(sub)),
        }
    return out


def _trailing_vol_window(stock, as_of, n_trading_days: int = 100, vol_type: str = "standard") -> dict:
    out = {"vol": float("nan"), "start": None, "end": None, "n_days": 0}
    if stock is None:
        return out
    as_of_ts = pd.Timestamp(as_of)
    n = int(max(2, n_trading_days))
    # Use ALL closes for ranking computation.
    # Restrict to calendar window to avoid mixing data from delisting gaps.
    _cal_days = int(n * 1.55) + 30
    _cal_start = as_of_ts - pd.Timedelta(days=_cal_days)
    sub_window = stock[(stock.index >= _cal_start) & (stock.index <= as_of_ts)]
    # Minimum 2 data points — rank on whatever data is in the window.
    if len(sub_window) < 2:
        return out
    sub     = sub_window.tail(n)
    _pre = stock[stock.index < sub.index[0]].tail(1)
    sub_vol = pd.concat([_pre[["close"]], sub[["close"]]]) if not _pre.empty else sub[["close"]]
    v = _vol_func(sub_vol["close"], vol_type)
    out.update(vol=(round(float(v), 6) if v == v else float("nan")),
               start=sub.index[0], end=sub.index[-1], n_days=int(len(sub)))
    return out


def _trailing_vol(stock, as_of, lookback_days: int = 100, vol_type: str = "standard") -> float:
    return _trailing_vol_window(stock, as_of, lookback_days, vol_type)["vol"]


def _universe_vol_topn(stock_dict, windows, n, direction, vol_type: str = "standard"):
    detail = {}
    if direction not in ("low", "high") or not windows:
        return None, "off", {}, detail
    n = max(1, int(n))
    asc = (direction == "low")
    per_window_sets, all_vols, win_txts = [], {}, []
    for wi, win in enumerate(windows):
        if len(win) == 3:
            ref, as_of, days = win
        else:
            as_of, days = win
            ref = ""
        if as_of is None:
            continue
        vols = {}
        for tkr, sdf in stock_dict.items():
            d = _trailing_vol_window(sdf, as_of, days, vol_type)
            if d["vol"] == d["vol"] and d["n_days"] >= 1 and d["end"] is not None:
                vol_end = pd.Timestamp(d["end"])
                if vol_end < pd.Timestamp(as_of) - pd.Timedelta(days=int(days) * 2):
                    continue
                vols[tkr] = d["vol"]
                detail.setdefault(tkr, []).append({
                    "window_idx": wi, "ref": ref, "as_of": pd.Timestamp(as_of),
                    "req_days": int(days), "vol_start": d["start"],
                    "vol_end": d["end"], "vol_days": d["n_days"],
                    "vol": d["vol"], "in_topn": False})
        if len(vols) < 2:
            win_txts.append(f"{days}td@{pd.Timestamp(as_of).date()}(insufficient)")
            continue
        ordered = sorted(vols.items(), key=lambda kv: kv[1], reverse=not asc)
        sel = {t for t, _ in ordered[:n]}
        per_window_sets.append(sel)
        for tkr in sel:
            for rec in detail.get(tkr, []):
                if rec["window_idx"] == wi:
                    rec["in_topn"] = True
        for t, v in vols.items():
            all_vols.setdefault(t, []).append(v)
        win_txts.append(f"{days}td@{pd.Timestamp(as_of).date()}")
    if not per_window_sets:
        return None, "n/a (insufficient trailing history)", {}, detail
    keep = (set.intersection(*per_window_sets) if len(per_window_sets) > 1 else per_window_sets[0])
    score_map = {t: float(np.mean(all_vols[t])) for t in keep if t in all_vols}
    basis = (f"{direction} top-{n} per window → INTERSECTION = {len(keep)} stocks "
             f"(windows [{', '.join(win_txts)}])")
    return keep, basis, score_map, detail


# ─────────────────────────────────────────────────────────────────────────────
# Ranking helpers
# ─────────────────────────────────────────────────────────────────────────────

def _common_volatility_only(eff, top_k_common, vol_scores, vol_direction):
    asc = (vol_direction != "high")
    rows = []
    for t in eff:
        sc = (vol_scores or {}).get(t)
        rows.append({"ticker": t, "mean_vol": sc, "mean_metric": sc})
    detail = pd.DataFrame(rows)
    if not detail.empty:
        detail = detail.sort_values("mean_metric", ascending=asc, na_position="last").reset_index(drop=True)
        detail["common_rank"] = range(1, len(detail) + 1)
        detail["selected_to_buy"] = detail["ticker"].isin(detail["ticker"].tolist()[:top_k_common])
    ordered = detail["ticker"].tolist()[:top_k_common] if not detail.empty else []
    meta = {
        "leg_labels": [], "n_legs_used": 0,
        "n_common_total": len(eff), "metric": "off",
        "vol_direction": vol_direction, "vol_pct": 0,
        "selection_side": "top", "n_vol_allowed": len(eff),
        "n_universe": len(eff), "n_selected": len(ordered),
        "selection_funnel": (f"volatility-filter intersection {len(eff)} → bought {len(ordered)} "
                             f"(ranking OFF)"),
        "audit_rows": [], "legs_funnel": [],
    }
    return ordered, detail, meta


def _filter_fixed_fd(fd: dict, as_of, tolerance_days: int = 5) -> dict:
    """Keep only tickers whose actual data exit_date is within *tolerance_days*
    calendar days of *as_of*.  Stocks without data at the canonical window end
    (trough for Fall Exit, peak for Fall Entry) are excluded from ranking."""
    as_of_ts = pd.Timestamp(as_of)
    cutoff = as_of_ts - pd.Timedelta(days=tolerance_days)
    return {t: v for t, v in fd.items()
            if pd.Timestamp(v["exit_date"]) >= cutoff}


def _common_from_legs(
    leg_specs, returns_df, stock_dict, metric, vol_direction,
    top_n, top_k_common, *, vol_pct=25, selection_side="top",
    allowed=None, vol_scores=None, vol_type="standard",
    nifty_df=None, gate_params=None, db_path=None,
):
    all_tickers = list(stock_dict.keys())
    eff = [t for t in all_tickers if (allowed is None or t in allowed)]
    p = max(1, min(99, int(vol_pct)))
    asc_low = (vol_direction == "low")
    pick_bottom = (selection_side == "bottom")

    if metric == "off":
        return _common_volatility_only(eff, int(top_k_common), vol_scores, vol_direction)

    _mf_g, _qual_rolled_g, _qual_cached_g, _gp = None, None, None, None
    if metric == "gate":
        import os
        from core.gate_system import rank_universe, load_market_features, load_quality_features, _rollup_quality, DEFAULT_PARAMS
        _gp = gate_params or DEFAULT_PARAMS
        _db_p = db_path or os.path.join("storage", "market_data.duckdb")
        _m_time = os.path.getmtime(_db_p) if os.path.exists(_db_p) else 0.0
        _mf_g = load_market_features(_db_p, _m_time)
        _qual_raw_g = load_quality_features(_db_p, _m_time)
        _qual_rolled_g = _rollup_quality(_qual_raw_g, _gp)
        _qual_cached_g = None

    legs = []
    audit_rows = []
    for leg in leg_specs:
        is_rise = str(leg.get("trade", "Rise")).capitalize() == "Rise"
        n = top_n  # unified top_n for both rise and fall
        is_fixed = leg.get("kind") == "fixed"

        if is_fixed and leg.get("n_days") and leg.get("as_of"):
            # Fixed trailing-N window: use exact last n_days closes
            # ending at as_of (peak date for Fall Entry, trough date for Fall Exit).
            _n_days = int(leg["n_days"])
            _as_of  = pd.Timestamp(leg["as_of"])
            _fd = _fixed_leg_roc_vol_detail(stock_dict, eff, _as_of, _n_days, vol_type)
            # Exclude stocks whose data doesn't reach the canonical window end date
            _fd = _filter_fixed_fd(_fd, _as_of)
            roc_detail = {}
            vol_detail_map = {}
            for t, fd in _fd.items():
                roc_detail[t] = {
                    "roc": fd["roc"],
                    "entry_date": fd["entry_date"], "exit_date": fd["exit_date"],
                }
                vol_detail_map[t] = {
                    "vol": fd["vol"],
                    "start": fd["entry_date"], "end": fd["exit_date"],
                    "n_days": fd["n_days"],
                }
        else:
            roc_detail = _leg_roc_detail(returns_df, leg, stock_dict, eff)
            # Use recency-aware vol detail: exclude stocks inactive near leg_exit
            _leg_end_ts = pd.Timestamp(leg["exit"])
            _max_gap = 365  # stock must have data within 1 year of leg end
            vol_detail_map = {
                t: _leg_vol_detail_with_recency(stock_dict.get(t), leg["entry"], leg["exit"],
                                                vol_type, _max_gap)
                for t in eff
            }

        roc_map, roc_excl = {}, {}
        for t, d in roc_detail.items():
            roc_map[t] = d["roc"]
        vol_map = {t: vd["vol"] for t, vd in vol_detail_map.items() if vd.get("vol") == vd.get("vol")}

        roc_sorted = sorted(roc_map.items(), key=lambda kv: kv[1], reverse=True)
        roc_rank = {t: i + 1 for i, (t, _) in enumerate(roc_sorted)}
        roc_pick = sorted(roc_map.items(), key=lambda kv: kv[1], reverse=not pick_bottom)
        roc_sel = {t for t, _ in roc_pick[:n]}

        vol_rank, vol_sel = {}, set()
        if vol_map:
            vol_sorted = sorted(vol_map.items(), key=lambda kv: kv[1], reverse=not asc_low)
            vol_rank = {t: i + 1 for i, (t, _) in enumerate(vol_sorted)}
            vol_sel = {t for t, _ in vol_sorted[:n]}

        # ROC / Volatility (division): rank by ROC / vol — higher = better
        roc_over_vol_map = {}
        roc_over_vol_rank, roc_over_vol_sel = {}, set()
        for t in roc_map:
            if t in vol_map and vol_map[t] and vol_map[t] > 0:
                roc_over_vol_map[t] = roc_map[t] / vol_map[t]
        if roc_over_vol_map:
            rov_sorted = sorted(roc_over_vol_map.items(), key=lambda kv: kv[1], reverse=not pick_bottom)
            roc_over_vol_rank = {t: i + 1 for i, (t, _) in enumerate(rov_sorted)}
            rov_pick = sorted(roc_over_vol_map.items(), key=lambda kv: kv[1], reverse=not pick_bottom)
            roc_over_vol_sel = {t for t, _ in rov_pick[:n]}

        prod_map, prod_rank, prod_sel = {}, {}, set()
        for t in roc_map:
            if t in vol_map and roc_map[t] is not None and vol_map[t] is not None:
                prod_map[t] = roc_map[t] * vol_map[t]
        if prod_map:
            prod_sorted = sorted(prod_map.items(), key=lambda kv: kv[1], reverse=True)
            prod_rank = {t: i + 1 for i, (t, _) in enumerate(prod_sorted)}
            prod_pick = sorted(prod_map.items(), key=lambda kv: kv[1], reverse=not pick_bottom)
            prod_sel = {t for t, _ in prod_pick[:n]}

        # ── Beta (NIFTY correlation) using ln returns ──────────────────────────
        beta_map, corr_map, beta_rank = {}, {}, {}
        beta_sel = set()
        if nifty_df is not None and not nifty_df.empty:
            _leg_end = pd.Timestamp(leg.get("as_of") or leg["exit"])
            # For fixed-window legs (FF252, FF100 etc.) Beta must use the same
            # trailing-N window as Volatility — NOT the full NIFTY cycle window.
            # The full cycle window (leg["entry"] → as_of) spans more days than N
            # (e.g. 282 days for FF252) which inflates/deflates Beta vs the audit.
            # Fix: anchor Beta to the N-th NIFTY trading day before as_of.
            if is_fixed and leg.get("n_days") and not nifty_df.empty:
                _n_beta = int(leg["n_days"])
                _nifty_upto = nifty_df[nifty_df.index <= _leg_end]
                if len(_nifty_upto) >= _n_beta:
                    _leg_start = _nifty_upto.index[-_n_beta]  # exact vol-window start
                else:
                    _leg_start = _nifty_upto.index[0] if not _nifty_upto.empty else pd.Timestamp(leg["entry"])
            else:
                _leg_start = pd.Timestamp(leg["entry"])
            for t in eff:
                stk = stock_dict.get(t)
                if stk is None:
                    continue
                prices = stk["close"] if "close" in stk.columns else stk.iloc[:, 0]
                bd = _compute_beta(prices, nifty_df, _leg_start, _leg_end)
                if not np.isnan(bd["beta"]):
                    beta_map[t] = bd["beta"]
                if not np.isnan(bd["corr_nifty"]):
                    corr_map[t] = bd["corr_nifty"]
            if beta_map:
                # Rank always ascending: rank 1 = lowest beta (most defensive)
                beta_sorted_asc = sorted(beta_map.items(), key=lambda kv: kv[1])
                beta_rank = {t: i + 1 for i, (t, _) in enumerate(beta_sorted_asc)}
                # Selection:
                #   Bottom-N (pick_bottom=True)  → lowest  beta first → take first N
                #   Top-N    (pick_bottom=False) → highest beta first → take last  N
                if pick_bottom:
                    beta_sel = {t for t, _ in beta_sorted_asc[:n]}
                else:
                    beta_sel = {t for t, _ in beta_sorted_asc[-n:]}

        # ── Beta / Volatility (division): beta / vol ───────────────────────────
        beta_over_vol_map, beta_over_vol_rank, beta_over_vol_sel = {}, {}, set()
        for t in beta_map:
            if t in vol_map and vol_map[t] and vol_map[t] > 0:
                beta_over_vol_map[t] = beta_map[t] / vol_map[t]
        if beta_over_vol_map:
            bov_sorted = sorted(beta_over_vol_map.items(), key=lambda kv: kv[1], reverse=not pick_bottom)
            beta_over_vol_rank = {t: i + 1 for i, (t, _) in enumerate(bov_sorted)}
            beta_over_vol_sel = {t for t, _ in bov_sorted[:n]}

        # ── Beta × Volatility (product): beta * vol ────────────────────────────
        beta_x_vol_map, beta_x_vol_rank, beta_x_vol_sel = {}, {}, set()
        for t in beta_map:
            if t in vol_map and vol_map[t] is not None:
                beta_x_vol_map[t] = beta_map[t] * vol_map[t]
        if beta_x_vol_map:
            bxv_sorted = sorted(beta_x_vol_map.items(), key=lambda kv: kv[1], reverse=not pick_bottom)
            beta_x_vol_rank = {t: i + 1 for i, (t, _) in enumerate(bxv_sorted)}
            beta_x_vol_sel = {t for t, _ in bxv_sorted[:n]}

        # ── Std Dev Vol / Downside Vol ratio ──────────────────────────────────
        sd_over_dv_map, sd_over_dv_rank, sd_over_dv_sel = {}, {}, set()
        sd_std_vol_map, sd_dv_vol_map = {}, {}   # individual vols for export columns
        if metric == "sd_over_dv":
            _std_vmap_sod, _dv_vmap_sod = {}, {}
            for _t in eff:
                _stk_sod = stock_dict.get(_t)
                if _stk_sod is None:
                    continue
                if is_fixed and leg.get("n_days") and leg.get("as_of"):
                    _sv = _trailing_vol(_stk_sod, pd.Timestamp(leg["as_of"]), int(leg["n_days"]), "standard")
                    _dv = _trailing_vol(_stk_sod, pd.Timestamp(leg["as_of"]), int(leg["n_days"]), "downside")
                else:
                    _sv = _leg_vol_detail(_stk_sod, leg["entry"], leg["exit"], "standard").get("vol")
                    _dv = _leg_vol_detail(_stk_sod, leg["entry"], leg["exit"], "downside").get("vol")
                if _sv is not None and _sv == _sv:
                    _std_vmap_sod[_t] = _sv
                if _dv is not None and _dv == _dv:
                    _dv_vmap_sod[_t] = _dv
            for _t in eff:
                _sv = _std_vmap_sod.get(_t)
                _dv = _dv_vmap_sod.get(_t)
                if _sv is not None and _dv is not None and _dv > 0 and _sv == _sv and _dv == _dv:
                    sd_over_dv_map[_t] = _sv / _dv
            sd_std_vol_map = _std_vmap_sod
            sd_dv_vol_map  = _dv_vmap_sod
            if sd_over_dv_map:
                _sod_sorted = sorted(sd_over_dv_map.items(), key=lambda kv: kv[1], reverse=not pick_bottom)
                sd_over_dv_rank = {t: i + 1 for i, (t, _) in enumerate(_sod_sorted)}
                sd_over_dv_sel = {t for t, _ in _sod_sorted[:n]}

        # ── Gate System (ARQM 3-gate pipeline: Momentum + Stability + Quality) ──
        gate_map, gate_rank, gate_sel, gate_scorecard = {}, {}, set(), {}
        gate_scorecard = {}
        if metric == "gate" and _mf_g is not None:
            _as_of_g = pd.Timestamp(leg.get("as_of") or leg["exit"])
            _sc_g = rank_universe(_as_of_g, eff, _mf_g, _qual_rolled_g, _gp, qual_cached=_qual_cached_g)
            if not _sc_g.empty:
                gate_scorecard = _sc_g.set_index("ticker").to_dict(orient="index")
                for _, r in _sc_g.iterrows():
                    if pd.notna(r["combined_score"]):
                        gate_map[r["ticker"]] = float(r["combined_score"])
                    if r.get("selected"):
                        gate_sel.add(r["ticker"])
                _g_sorted = sorted(gate_map.items(), key=lambda kv: kv[1], reverse=not pick_bottom)
                gate_rank = {t: i + 1 for i, (t, _) in enumerate(_g_sorted)}

        if metric == "roc":
            leg_set = roc_sel
        elif metric == "vol":
            leg_set = vol_sel
        elif metric == "volxroc":
            leg_set = prod_sel
        elif metric == "roc_over_vol":
            leg_set = roc_over_vol_sel
        elif metric == "beta":
            leg_set = beta_sel
        elif metric == "beta_over_vol":
            leg_set = beta_over_vol_sel
        elif metric == "beta_x_vol":
            leg_set = beta_x_vol_sel
        elif metric == "sd_over_dv":
            leg_set = sd_over_dv_sel
        elif metric == "gate":
            leg_set = gate_sel
        elif metric == "off":
            leg_set = set(eff)
        else:  # both
            leg_set = roc_sel & vol_sel

        legs.append({"label": leg["label"], "roc_map": roc_map, "vol_map": vol_map,
                     "prod_map": prod_map, "roc_over_vol_map": roc_over_vol_map,
                     "beta_map": beta_map, "corr_map": corr_map,
                     "beta_over_vol_map": beta_over_vol_map,
                     "beta_x_vol_map": beta_x_vol_map,
                     "sd_over_dv_map": sd_over_dv_map,
                     "sd_std_vol_map": sd_std_vol_map,
                     "sd_dv_vol_map": sd_dv_vol_map,
                     "gate_map": gate_map,
                     "gate_rank": gate_rank,
                     "gate_scorecard": gate_scorecard,
                     "roc_rank": roc_rank, "vol_rank": vol_rank,
                     "prod_rank": prod_rank, "roc_over_vol_rank": roc_over_vol_rank,
                     "beta_rank": beta_rank,
                     "beta_over_vol_rank": beta_over_vol_rank,
                     "beta_x_vol_rank": beta_x_vol_rank,
                     "sd_over_dv_rank": sd_over_dv_rank,
                     "set": leg_set,
                     "n_roc": len(roc_sel), "n_vol": len(vol_sel),
                     "n_prod": len(prod_sel), "n_rov": len(roc_over_vol_sel),
                     "n_beta": len(beta_sel),
                     "n_bov": len(beta_over_vol_sel),
                     "n_bxv": len(beta_x_vol_sel),
                     "n_sod": len(sd_over_dv_sel),
                     "n_set": len(leg_set)})

        uses_roc = metric in ("roc", "both", "volxroc", "roc_over_vol")
        uses_beta = (metric in ("beta", "beta_over_vol", "beta_x_vol"))
        # For fixed legs show the actual window end (as_of) as the "exit" reference
        if is_fixed and leg.get("as_of"):
            _leg_entry_display = pd.Timestamp(leg["entry"])  # approx cal start
            _leg_exit_display  = pd.Timestamp(leg["as_of"])  # actual as_of (peak/trough)
        else:
            _leg_entry_display = pd.Timestamp(leg["entry"])
            _leg_exit_display  = pd.Timestamp(leg["exit"])
        for t in eff:
            rd = roc_detail.get(t, {})
            vd = vol_detail_map.get(t, {})
            rv = rd.get("roc")
            vv = vd.get("vol")
            bv = beta_map.get(t)
            pv = (rv * vv) if (rv is not None and vv is not None and vv == vv) else None
            rovv = (rv / vv) if (rv is not None and vv is not None and vv > 0) else None
            bov = (bv / vv) if (bv is not None and vv is not None and vv > 0) else None
            bxv = (bv * vv) if (bv is not None and vv is not None) else None
            sodv = sd_over_dv_map.get(t)
            sodv_std = sd_std_vol_map.get(t)
            sodv_dv  = sd_dv_vol_map.get(t)
            if uses_roc and rv is None:
                qualified, reason = False, "No ROC data in leg window"
            elif uses_beta and bv is None:
                qualified, reason = False, "No Beta data — insufficient NIFTY overlap in leg window"
            else:
                qualified, reason = True, ""
            # metric_score is the actual score used for ranking this leg
            _metric_score = (bov if metric == "beta_over_vol"
                             else bxv if metric == "beta_x_vol"
                             else bv if metric == "beta"
                             else rovv if metric == "roc_over_vol"
                             else pv if metric == "volxroc"
                             else vv if metric == "vol"
                             else sodv if metric == "sd_over_dv"
                             else rv)
            audit_rows.append({
                "leg": leg["label"], "trade": str(leg.get("trade", "")).capitalize(),
                "ticker": t,
                "leg_entry_date": _leg_entry_display,
                "leg_exit_date":  _leg_exit_display,

                "roc_start_date": rd.get("entry_date"),
                "roc_end_date": rd.get("exit_date"),
                "roc": rv,
                "vol_start_date": vd.get("start"),
                "vol_end_date": vd.get("end"),
                "vol_trading_days": vd.get("n_days"),
                "volatility": vv,
                "roc_x_vol": (round(pv, 6) if pv is not None else None),
                "roc_over_vol": (round(rovv, 6) if rovv is not None else None),
                "beta_over_vol": (round(bov, 6) if bov is not None else None),
                "beta_x_vol": (round(bxv, 6) if bxv is not None else None),
                "sd_over_dv": (round(sodv, 6) if sodv is not None else None),
                "sd_std_vol": (round(sodv_std, 6) if sodv_std is not None else None),
                "sd_dv_vol":  (round(sodv_dv,  6) if sodv_dv  is not None else None),
                "metric_score": (round(float(_metric_score), 6) if _metric_score is not None else None),
                "beta_nifty": beta_map.get(t),
                "corr_nifty": corr_map.get(t),
                "beta_rank": beta_rank.get(t),
                "qualified_for_ranking": "Yes" if qualified else "No",
                "in_leg_topn": t in leg_set,
                "reason": reason,
            })

    non_empty = [L["set"] for L in legs if L["set"]]
    common = set.intersection(*non_empty) if non_empty else set()

    rows = []
    for tkr in common:
        row = {"ticker": tkr}
        rocs, vols, prods, rovs, betas, corrs, bovs, bxvs, sods, gates = [], [], [], [], [], [], [], [], [], []
        g_moms, g_stabs, g_quals = [], [], []
        for L in legs:
            lbl = L["label"]
            rv = L["roc_map"].get(tkr)
            vv = L["vol_map"].get(tkr)
            pv = L.get("prod_map", {}).get(tkr)
            rovv = L.get("roc_over_vol_map", {}).get(tkr)
            bv = L.get("beta_map", {}).get(tkr)
            cv = L.get("corr_map", {}).get(tkr)
            bovv = L.get("beta_over_vol_map", {}).get(tkr)
            bxvv = L.get("beta_x_vol_map", {}).get(tkr)
            sodvv = L.get("sd_over_dv_map", {}).get(tkr)
            gv = L.get("gate_map", {}).get(tkr)
            row[f"{lbl} | roc"] = rv
            row[f"{lbl} | roc_rank"] = L["roc_rank"].get(tkr)
            row[f"{lbl} | vol"] = vv
            row[f"{lbl} | vol_rank"] = L["vol_rank"].get(tkr)
            row[f"{lbl} | volxroc"] = (round(pv, 6) if pv is not None else None)
            row[f"{lbl} | roc_over_vol"] = (round(rovv, 6) if rovv is not None else None)
            row[f"{lbl} | beta_nifty"] = bv
            row[f"{lbl} | corr_nifty"] = cv
            row[f"{lbl} | beta_rank"] = L.get("beta_rank", {}).get(tkr)
            row[f"{lbl} | beta_over_vol"] = (round(bovv, 6) if bovv is not None else None)
            row[f"{lbl} | beta_over_vol_rank"] = L.get("beta_over_vol_rank", {}).get(tkr)
            row[f"{lbl} | beta_x_vol"] = (round(bxvv, 6) if bxvv is not None else None)
            row[f"{lbl} | beta_x_vol_rank"] = L.get("beta_x_vol_rank", {}).get(tkr)
            row[f"{lbl} | sd_over_dv"] = (round(sodvv, 6) if sodvv is not None else None)
            row[f"{lbl} | sd_over_dv_rank"] = L.get("sd_over_dv_rank", {}).get(tkr)
            row[f"{lbl} | gate_score"] = (round(gv, 6) if gv is not None else None)
            row[f"{lbl} | gate_rank"] = L.get("gate_rank", {}).get(tkr)
            
            _g_sc = L.get("gate_scorecard", {}).get(tkr)
            if _g_sc:
                for _k, _v in _g_sc.items():
                    if _k not in ("ticker", "combined_score", "selected", "rank", "as_of", "phase_id", "trade", "entry_date", "exit_date"):
                        row[f"{lbl} | gate_{_k}"] = round(float(_v), 4) if pd.notna(_v) else None
                # Collect quality_score_raw (pre-threshold) first, fallback to quality_score
                _g_ms = _g_sc.get("momentum_score")
                _g_ss = _g_sc.get("stability_score")
                _g_qs = _g_sc.get("quality_score_raw") if pd.notna(_g_sc.get("quality_score_raw")) else _g_sc.get("quality_score")
                if pd.notna(_g_ms): g_moms.append(float(_g_ms))
                if pd.notna(_g_ss): g_stabs.append(float(_g_ss))
                if pd.notna(_g_qs): g_quals.append(float(_g_qs))
            if rv is not None:
                rocs.append(rv)
            if vv is not None:
                vols.append(vv)
            if pv is not None:
                prods.append(pv)
            if rovv is not None:
                rovs.append(rovv)
            if bv is not None:
                betas.append(bv)
            if cv is not None:
                corrs.append(cv)
            if bovv is not None:
                bovs.append(bovv)
            if bxvv is not None:
                bxvs.append(bxvv)
            if sodvv is not None:
                sods.append(sodvv)
            if gv is not None:
                gates.append(gv)
        row["mean_roc"] = round(float(np.mean(rocs)), 4) if rocs else None
        row["mean_vol"] = round(float(np.mean(vols)), 6) if vols else None
        row["mean_volxroc"] = round(float(np.mean(prods)), 6) if prods else None
        row["mean_roc_over_vol"] = round(float(np.mean(rovs)), 6) if rovs else None
        row["mean_beta_nifty"] = round(float(np.mean(betas)), 4) if betas else None
        row["mean_corr_nifty"] = round(float(np.mean(corrs)), 4) if corrs else None
        row["mean_beta_over_vol"] = round(float(np.mean(bovs)), 6) if bovs else None
        row["mean_beta_x_vol"] = round(float(np.mean(bxvs)), 6) if bxvs else None
        row["mean_sd_over_dv"] = round(float(np.mean(sods)), 6) if sods else None
        row["mean_gate"] = round(float(np.mean(gates)), 6) if gates else None
        row["mean_momentum_score"] = round(float(np.mean(g_moms)), 4) if g_moms else None
        row["mean_stability_score"] = round(float(np.mean(g_stabs)), 4) if g_stabs else None
        row["mean_quality_score"] = round(float(np.mean(g_quals)), 4) if g_quals else None
        rows.append(row)

    detail = pd.DataFrame(rows)
    if not detail.empty:
        if metric == "vol":
            # BUG FIX v30: vol sort direction must follow vol_direction only.
            # pick_bottom controls which ROC stocks enter the pool, NOT how vols are ranked.
            # low vol → ascending=True → smallest vol = rank 1 = selected first (always).
            _asc = asc_low
            detail = detail.sort_values("mean_vol", ascending=_asc, na_position="last")
            detail["mean_metric"] = detail["mean_vol"]
        elif metric == "volxroc":
            detail = detail.sort_values("mean_volxroc", ascending=pick_bottom, na_position="last")
            detail["mean_metric"] = detail["mean_volxroc"]
        elif metric == "roc_over_vol":
            detail = detail.sort_values("mean_roc_over_vol", ascending=pick_bottom, na_position="last")
            detail["mean_metric"] = detail["mean_roc_over_vol"]
        elif metric == "beta":
            # Bottom-N → ascending  (lowest  beta = rank 1, selected first)
            # Top-N    → descending (highest beta = rank 1, selected first)
            detail = detail.sort_values("mean_beta_nifty", ascending=pick_bottom, na_position="last")
            detail["mean_metric"] = detail["mean_beta_nifty"]
        elif metric == "beta_over_vol":
            detail = detail.sort_values("mean_beta_over_vol", ascending=pick_bottom, na_position="last")
            detail["mean_metric"] = detail["mean_beta_over_vol"]
        elif metric == "beta_x_vol":
            detail = detail.sort_values("mean_beta_x_vol", ascending=pick_bottom, na_position="last")
            detail["mean_metric"] = detail["mean_beta_x_vol"]
        elif metric == "sd_over_dv":
            # StdDev/Downside vol ratio: Top-N picks highest ratio, Bottom-N picks lowest
            detail = detail.sort_values("mean_sd_over_dv", ascending=pick_bottom, na_position="last")
            detail["mean_metric"] = detail["mean_sd_over_dv"]
        elif metric == "gate":
            detail = detail.sort_values("mean_gate", ascending=pick_bottom, na_position="last")
            detail["mean_metric"] = detail["mean_gate"]
        elif metric == "off":
            if vol_scores:
                detail["_vol_score"] = detail["ticker"].map(vol_scores)
                detail = detail.sort_values("_vol_score", ascending=asc_low, na_position="last")
                detail["mean_metric"] = detail["_vol_score"]
            else:
                detail = detail.sort_values("mean_vol", ascending=asc_low, na_position="last")
                detail["mean_metric"] = detail["mean_vol"]
        else:
            detail = detail.sort_values("mean_roc", ascending=pick_bottom, na_position="last")
            detail["mean_metric"] = detail["mean_roc"]
        detail = detail.reset_index(drop=True)
        detail["common_rank"] = range(1, len(detail) + 1)
        # Beta rank: rank by mean_beta_nifty ascending (lower beta = rank 1)
        if "mean_beta_nifty" in detail.columns and detail["mean_beta_nifty"].notna().any():
            detail["beta_rank"] = detail["mean_beta_nifty"].rank(
                method="min", ascending=True, na_option="bottom").astype("Int64")
        detail["selected_to_buy"] = detail["ticker"].isin(detail["ticker"].tolist()[:top_k_common])

    ordered = detail["ticker"].tolist()[:top_k_common] if not detail.empty else []
    n_selected = len(ordered)

    def _leg_funnel(L):
        if metric == "off":
            return f"{L['label']}(all {L['n_set']})"
        if metric == "roc":
            return f"{L['label']}(roc {L['n_roc']})"
        if metric == "vol":
            return f"{L['label']}(vol {L['n_vol']})"
        if metric == "volxroc":
            return f"{L['label']}(vol×roc {L['n_prod']})"
        if metric == "roc_over_vol":
            return f"{L['label']}(roc/vol {L['n_rov']})"
        if metric == "beta":
            return f"{L['label']}(beta {L.get('n_beta', 0)})"
        if metric == "beta_over_vol":
            return f"{L['label']}(β/vol {L.get('n_bov', 0)})"
        if metric == "beta_x_vol":
            return f"{L['label']}(β×vol {L.get('n_bxv', 0)})"
        if metric == "sd_over_dv":
            return f"{L['label']}(σ/DV {L.get('n_sod', 0)})"
        return f"{L['label']}(roc {L['n_roc']} ∩ vol {L['n_vol']} = {L['n_set']})"

    funnel = (f"universe {len(eff)} → "
              + " ∩ ".join(_leg_funnel(L) for L in legs)
              + f" → common {len(common)} → bought {n_selected}")

    _sel_set = set(ordered)
    for ar in audit_rows:
        ar["selected_to_buy"] = ar["ticker"] in _sel_set

    meta = {
        "leg_labels": [L["label"] for L in legs],
        "n_legs_used": len(non_empty),
        "n_common_total": len(common),
        "metric": metric, "vol_direction": vol_direction, "vol_pct": p,
        "selection_side": selection_side,
        "n_vol_allowed": (len(allowed) if allowed is not None else None),
        "n_universe": len(eff),
        "n_selected": n_selected,
        "selection_funnel": funnel,
        "audit_rows": audit_rows,
        "legs_funnel": [{"label": L["label"], "n_roc": L["n_roc"],
                          "n_vol": L["n_vol"], "n_set": L["n_set"]} for L in legs],
    }
    return ordered, detail, meta


# ─────────────────────────────────────────────────────────────────────────────
# Scheduled-phase lookups
# ─────────────────────────────────────────────────────────────────────────────

def _recent_phase(legs, as_of, trade_type=None):
    done = legs[legs["exit_date"] <= pd.Timestamp(as_of)]
    if trade_type is not None:
        done = done[done["trade"].astype(str).str.capitalize() == str(trade_type).capitalize()]
    if done.empty:
        return None
    return done.sort_values("exit_date").iloc[-1]


def _last_two_legs(legs, as_of):
    done = legs[pd.to_datetime(legs["exit_date"]) <= pd.Timestamp(as_of)].sort_values("exit_date")
    if done.empty:
        tail = legs.sort_values("exit_date").head(2)
    else:
        tail = done.tail(2)
    return [tail.iloc[i] for i in range(len(tail))]


def _sched_leg_spec(leg) -> dict:
    return {
        "label": str(leg["trade"]).capitalize(),
        "kind": "sched", "phase_id": int(leg["phase_id"]),
        "entry": pd.Timestamp(leg["entry_date"]), "exit": pd.Timestamp(leg["exit_date"]),
        "trade": str(leg["trade"]).capitalize(),
    }


def _leg_full_rank_rows(cycle_no, leg_specs, returns_df, stock_dict,
                        metric, vol_direction, top_n,
                        winrank, common_set, bought_set, allowed=None, vol_type="standard",
                        selection_side="top", nifty_df=None, gate_params=None, db_path=None):
    all_tk = list(stock_dict.keys())
    eff = [t for t in all_tk if (allowed is None or t in allowed)]
    _mf_g, _qual_rolled_g, _qual_cached_g, _gp = None, None, None, None
    if metric == "gate":
        import os
        from core.gate_system import rank_universe, load_market_features, load_quality_features, _rollup_quality, DEFAULT_PARAMS
        _gp = gate_params or DEFAULT_PARAMS
        _db_p = db_path or os.path.join("storage", "market_data.duckdb")
        _m_time = os.path.getmtime(_db_p) if os.path.exists(_db_p) else 0.0
        _mf_g = load_market_features(_db_p, _m_time)
        _qual_raw_g = load_quality_features(_db_p, _m_time)
        _qual_rolled_g = _rollup_quality(_qual_raw_g, _gp)
        _qual_cached_g = None

    rows = []
    for leg in leg_specs:
        is_rise = str(leg.get("trade", "Rise")).capitalize() == "Rise"
        is_fixed = leg.get("kind") == "fixed"
        n = top_n
        if is_fixed and leg.get("n_days") and leg.get("as_of"):
            _n_days = int(leg["n_days"])
            _as_of  = pd.Timestamp(leg["as_of"])
            _fd = _fixed_leg_roc_vol_detail(stock_dict, eff, _as_of, _n_days, vol_type)
            # Exclude stocks without data at the canonical window end date
            _fd = _filter_fixed_fd(_fd, _as_of)
            roc_detail = {t: {"roc": fd["roc"], "entry_date": fd["entry_date"],
                               "exit_date": fd["exit_date"]} for t, fd in _fd.items()}
            vol_detail_map = {t: {"vol": fd["vol"], "start": fd["entry_date"],
                                   "end": fd["exit_date"], "n_days": fd["n_days"]}
                              for t, fd in _fd.items()}
            roc_map = {t: d["roc"] for t, d in roc_detail.items()}
        else:
            roc_detail = _leg_roc_detail(returns_df, leg, stock_dict, eff)
            _max_gap_r = 365
            vol_detail_map = {
                t: _leg_vol_detail_with_recency(stock_dict.get(t), leg["entry"], leg["exit"],
                                                vol_type, _max_gap_r)
                for t in eff
            }

            roc_map = {t: d["roc"] for t, d in roc_detail.items()}
        vol_map = {t: vd["vol"] for t, vd in vol_detail_map.items() if vd.get("vol") == vd.get("vol")}
        _desc = (selection_side != "bottom")   # top → descending (highest first); bottom → ascending (lowest first)

        # ── Beta per ticker for this leg (computed over ALL eff, needed for beta ordering) ──
        _leg_end_b   = leg.get("as_of") or leg["exit"]
        # For fixed legs (e.g. 252d / 100d), anchor beta start to N NIFTY trading days
        # before the as_of date — same logic as the main selection loop (lines 660-667).
        # Without this, beta was computed over the short phase window instead of the
        # correct N-day lookback, causing wrong beta values in Cycle Ledger / Cycle Leg Rankings.
        if is_fixed and leg.get("n_days") and nifty_df is not None and not nifty_df.empty:
            _n_beta      = int(leg["n_days"])
            _nifty_upto  = nifty_df[nifty_df.index <= pd.Timestamp(_leg_end_b)]
            if len(_nifty_upto) >= _n_beta:
                _leg_start_b = _nifty_upto.index[-_n_beta]
            else:
                _leg_start_b = _nifty_upto.index[0] if not _nifty_upto.empty else pd.Timestamp(leg["entry"])
        else:
            _leg_start_b = pd.Timestamp(leg["entry"])
        _beta_map_lr, _beta_rank_lr = {}, {}
        _sc_dict = {}
        _sod_map_lr = {}
        _std_vmap_lr = {}
        _dv_vmap_lr = {}
        if nifty_df is not None and not nifty_df.empty:
            for _t in eff:
                _stk = stock_dict.get(_t)
                if _stk is None:
                    continue
                _prices = _stk["close"] if "close" in _stk.columns else _stk.iloc[:, 0]
                _bd = _compute_beta(_prices, nifty_df, _leg_start_b, _leg_end_b)
                if not np.isnan(_bd["beta"]):
                    _beta_map_lr[_t] = {"beta": _bd["beta"], "corr": _bd["corr_nifty"]}
            if _beta_map_lr:
                _bsorted = sorted(_beta_map_lr.items(), key=lambda kv: kv[1]["beta"])
                _beta_rank_lr = {t: i + 1 for i, (t, _) in enumerate(_bsorted)}

        if metric == "vol":
            # BUG FIX v30: vol sort direction follows vol_direction only.
            asc = (vol_direction == "low")
            order = [t for t, _ in sorted(vol_map.items(), key=lambda kv: kv[1], reverse=not asc)][:n]
        elif metric == "volxroc":
            prod_map = {t: roc_map[t] * vol_map[t] for t in roc_map
                        if t in vol_map and roc_map[t] is not None and vol_map[t] is not None}
            order = [t for t, _ in sorted(prod_map.items(), key=lambda kv: kv[1], reverse=_desc)][:n]
        elif metric == "roc_over_vol":
            rov_map = {t: roc_map[t] / vol_map[t] for t in roc_map
                       if t in vol_map and roc_map[t] is not None and vol_map[t] is not None and vol_map[t] > 0}
            order = [t for t, _ in sorted(rov_map.items(), key=lambda kv: kv[1], reverse=_desc)][:n]
        elif metric == "beta":
            # Bottom-N → ascending  (lowest  beta first, rank 1 = most defensive)
            # Top-N    → descending (highest beta first, rank 1 = most aggressive)
            _beta_sorted_asc = sorted(_beta_map_lr.items(), key=lambda kv: kv[1]["beta"])
            if selection_side == "bottom":
                order = [t for t, _ in _beta_sorted_asc][:n]
            else:
                order = [t for t, _ in reversed(_beta_sorted_asc)][:n]
        elif metric == "beta_over_vol":
            _bov_map = {t: _beta_map_lr[t]["beta"] / vol_map[t]
                        for t in _beta_map_lr if t in vol_map and vol_map[t] and vol_map[t] > 0}
            order = [t for t, _ in sorted(_bov_map.items(), key=lambda kv: kv[1], reverse=_desc)][:n]
        elif metric == "beta_x_vol":
            _bxv_map = {t: _beta_map_lr[t]["beta"] * vol_map[t]
                        for t in _beta_map_lr if t in vol_map and vol_map[t] is not None}
            order = [t for t, _ in sorted(_bxv_map.items(), key=lambda kv: kv[1], reverse=_desc)][:n]
        elif metric == "sd_over_dv":
            for _t in eff:
                _stk_lr = stock_dict.get(_t)
                if _stk_lr is None:
                    continue
                if is_fixed and leg.get("n_days") and leg.get("as_of"):
                    _sv_lr = _trailing_vol(_stk_lr, pd.Timestamp(leg["as_of"]), int(leg["n_days"]), "standard")
                    _dv_lr = _trailing_vol(_stk_lr, pd.Timestamp(leg["as_of"]), int(leg["n_days"]), "downside")
                else:
                    _sv_lr = _leg_vol_detail(_stk_lr, leg["entry"], leg["exit"], "standard").get("vol")
                    _dv_lr = _leg_vol_detail(_stk_lr, leg["entry"], leg["exit"], "downside").get("vol")
                if _sv_lr is not None and _sv_lr == _sv_lr:
                    _std_vmap_lr[_t] = _sv_lr
                if _dv_lr is not None and _dv_lr == _dv_lr:
                    _dv_vmap_lr[_t] = _dv_lr
                if (_sv_lr is not None and _dv_lr is not None and _dv_lr > 0
                        and _sv_lr == _sv_lr and _dv_lr == _dv_lr):
                    _sod_map_lr[_t] = _sv_lr / _dv_lr
            order = [t for t, _ in sorted(_sod_map_lr.items(), key=lambda kv: kv[1], reverse=_desc)][:n]
        elif metric == "gate" and _mf_g is not None:
            _as_of_g = pd.Timestamp(leg.get("as_of") or leg["exit"])
            _sc_g = rank_universe(_as_of_g, eff, _mf_g, _qual_rolled_g, _gp, qual_cached=_qual_cached_g)
            _rank_map = {}
            if not _sc_g.empty:
                _sc_dict = _sc_g.set_index("ticker").to_dict(orient="index")
                _rank_map = {row["ticker"]: row["rank"] for _, row in _sc_g.iterrows()}
            order = [t for t, _ in sorted(_rank_map.items(), key=lambda kv: kv[1])][:n]
        else:
            order = [t for t, _ in sorted(roc_map.items(), key=lambda kv: kv[1], reverse=_desc)][:n]

        for i, tkr in enumerate(order):
            rv = roc_map.get(tkr)
            vv = vol_map.get(tkr)
            pv = (rv * vv) if (rv is not None and vv is not None) else None
            rovv = (rv / vv) if (rv is not None and vv is not None and vv > 0) else None
            rd = roc_detail.get(tkr, {})
            vd = vol_detail_map.get(tkr, {})
            _row_entry = (rd.get("entry_date") or leg["entry"]) if is_fixed else leg["entry"]
            _row_exit  = (pd.Timestamp(leg["as_of"]) if (is_fixed and leg.get("as_of"))
                          else leg["exit"])
            _bov = (_beta_map_lr[tkr]["beta"] / vv) if (tkr in _beta_map_lr and vv is not None and vv > 0) else None
            _bxv = (_beta_map_lr[tkr]["beta"] * vv) if (tkr in _beta_map_lr and vv is not None) else None
            _sod_score   = _sod_map_lr.get(tkr)   if metric == "sd_over_dv" else None
            _sod_std_vol = _std_vmap_lr.get(tkr)  if metric == "sd_over_dv" else None
            _sod_dv_vol  = _dv_vmap_lr.get(tkr)   if metric == "sd_over_dv" else None
            
            _g_info = _sc_dict.get(tkr, {})
            _g_mom_sc  = (round(float(_g_info["momentum_score"]), 4) if (_g_info and pd.notna(_g_info.get("momentum_score"))) else None)
            _g_stab_sc = (round(float(_g_info["stability_score"]), 4) if (_g_info and pd.notna(_g_info.get("stability_score"))) else None)
            # Use quality_score_raw (pre-threshold) when quality_score is masked to NaN
            _g_qual_sc = (round(float(_g_info["quality_score"]), 4) if (_g_info and pd.notna(_g_info.get("quality_score"))) 
                          else (round(float(_g_info["quality_score_raw"]), 4) if (_g_info and pd.notna(_g_info.get("quality_score_raw"))) else None))
            _g_pm      = ("Y" if _g_info.get("passed_momentum") else "N") if _g_info else "N"
            _g_ps      = ("Y" if _g_info.get("passed_stability") else "N") if _g_info else "N"
            _g_pq      = ("Y" if _g_info.get("passed_quality") else "N") if _g_info else "N"

            _g_pillars = {}
            if _g_info:
                for _pk, _pv in _g_info.items():
                    if _pk.startswith("pillar_") and pd.notna(_pv):
                        _g_pillars[_pk] = round(float(_pv), 4)

            rows.append({
                "cycle": cycle_no + 1, "window": cycle_no + 1,
                "leg": leg["label"],
                "entry_date": _row_entry, "exit_date": _row_exit,
                "leg_rank": i + 1, "ticker": tkr,

                "roc_start_date": rd.get("entry_date"), "roc_end_date": rd.get("exit_date"),
                "roc_value": rv,
                "vol_start_date": vd.get("start"), "vol_end_date": vd.get("end"),
                "vol_trading_days": vd.get("n_days"), "vol_value": vv,
                "roc_x_vol": (round(pv, 6) if pv is not None else None),
                "roc_over_vol": (round(rovv, 6) if rovv is not None else None),
                "beta_over_vol": (round(_bov, 6) if _bov is not None else None),
                "beta_x_vol": (round(_bxv, 6) if _bxv is not None else None),
                "sd_over_dv":   (round(_sod_score,   6) if _sod_score   is not None else None),
                "sd_std_vol":   (round(_sod_std_vol,  6) if _sod_std_vol is not None else None),
                "sd_dv_vol":    (round(_sod_dv_vol,   6) if _sod_dv_vol  is not None else None),
                "momentum_score": _g_mom_sc,
                "stability_score": _g_stab_sc,
                "quality_score": _g_qual_sc,
                **{_k: _v for _k, _v in _g_pillars.items()},
                "passed_momentum": _g_pm,
                "passed_stability": _g_ps,
                "passed_quality": _g_pq,
                "metric_score": (_g_mom_sc if (metric == "gate" and _g_mom_sc is not None)
                                 else round(_bov, 6) if (metric == "beta_over_vol" and _bov is not None)
                                 else round(_bxv, 6) if (metric == "beta_x_vol" and _bxv is not None)
                                 else round(_beta_map_lr[tkr]["beta"], 4) if (metric == "beta" and tkr in _beta_map_lr)
                                 else round(rovv, 6) if (metric == "roc_over_vol" and rovv is not None)
                                 else round(pv, 6) if (metric == "volxroc" and pv is not None)
                                 else round(vv, 6) if (metric == "vol" and vv is not None)
                                 else round(_sod_score, 6) if (metric == "sd_over_dv" and _sod_score is not None)
                                 else round(rv, 4) if rv is not None else None),
                "metric_value": (_g_mom_sc if metric == "gate"
                                 else _bov if metric == "beta_over_vol"
                                 else _bxv if metric == "beta_x_vol"
                                 else _beta_map_lr[tkr]["beta"] if (metric == "beta" and tkr in _beta_map_lr)
                                 else vv if metric == "vol"
                                 else pv if metric == "volxroc"
                                 else rovv if metric == "roc_over_vol"
                                 else _sod_score if metric == "sd_over_dv"
                                 else rv),
                "beta_nifty": _beta_map_lr.get(tkr, {}).get("beta"),
                "corr_nifty": _beta_map_lr.get(tkr, {}).get("corr"),
                "beta_rank": _beta_rank_lr.get(tkr),
                "window_rank": winrank.get(tkr, None),
                "common": "✓" if tkr in common_set else "",
                "bought": "✓" if tkr in bought_set else "",
            })
    return rows


def _vol_window_leg_rows(cycle_no, vol_detail, top_n, common_set, bought_set):
    rows = []
    groups = {}
    for tkr, recs in (vol_detail or {}).items():
        for rec in recs:
            groups.setdefault(rec["window_idx"], []).append((tkr, rec))
    for wi in sorted(groups):
        items = sorted(groups[wi], key=lambda kv: (kv[1].get("window_rank") or 1e9))
        ref = items[0][1].get("ref", "") if items else ""
        _days = items[0][1].get("req_days", 100) if items else 100
        label = f"Vol@{ref} ({_days}d)" if ref else f"Vol window {wi + 1} ({_days}d)"
        for tkr, rec in items[:max(1, int(top_n))]:
            rows.append({
                "cycle": cycle_no + 1, "window": cycle_no + 1,
                "leg": label,
                "entry_date": rec.get("vol_start"), "exit_date": rec.get("vol_end"),
                "leg_rank": rec.get("window_rank"), "ticker": tkr,

                "roc_start_date": None, "roc_end_date": None, "roc_value": None,
                "vol_start_date": rec.get("vol_start"), "vol_end_date": rec.get("vol_end"),
                "vol_trading_days": rec.get("vol_days"), "vol_value": rec.get("vol"),
                "roc_x_vol": None, "roc_over_vol": None, "metric_value": rec.get("vol"),
                "window_rank": None,
                "common": "✓" if tkr in common_set else "",
                "bought": "✓" if tkr in bought_set else "",
            })
    return rows


# ─────────────────────────────────────────────────────────────────────────────
# Fixed Fall Entry/Exit window leg builder
# ─────────────────────────────────────────────────────────────────────────────

def _build_fixed_fall_leg_specs(peak_date, trough_date,
                                fall_entry_days: int, fall_exit_days: int) -> list:
    """Build two fixed trailing-N window leg specs using the previous cycle's fall dates.

      Fall Entry leg: last `fall_entry_days` closes ending at `peak_date`
        → measures performance/vol in the N days leading into the fall start
      Fall Exit  leg: last `fall_exit_days` closes ending at `trough_date`
        → measures performance/vol in the N days leading into the fall exit (trough)

    The two windows have different end dates (peak vs trough) and can have different
    N-day lookbacks. ROC and vol are computed independently over each trailing slice
    via _fixed_leg_roc_vol_detail. Rankings from both windows are intersected to
    produce the common basket.
    """
    pe = pd.Timestamp(peak_date)    # prev cycle peak = end of Fall Entry window
    tr = pd.Timestamp(trough_date)  # prev cycle trough = end of Fall Exit window
    # Calendar approx starts (just for display; actual data sliced by n_days)
    fe_cal_start = pe - pd.Timedelta(days=int(fall_entry_days * 1.6) + 15)
    fx_cal_start = tr - pd.Timedelta(days=int(fall_exit_days  * 1.6) + 15)
    return [
        {
            "label": "Fall Entry",
            "kind": "fixed", "phase_id": None,
            "entry": fe_cal_start, "exit": pe,   # exit = as_of for trailing window
            "trade": "Fall",
            "n_days": fall_entry_days,            # exact trailing-N trading days
            "as_of": pe,                          # end of the window
        },
        {
            "label": "Fall Exit",
            "kind": "fixed", "phase_id": None,
            "entry": fx_cal_start, "exit": tr,   # exit = as_of for trailing window
            "trade": "Fall",
            "n_days": fall_exit_days,
            "as_of": tr,
        },
    ]


# ─────────────────────────────────────────────────────────────────────────────
# Main run_momentum
# ─────────────────────────────────────────────────────────────────────────────

@st.cache_data(show_spinner=False)
def run_momentum(
    _nifty_df: pd.DataFrame,
    _stock_dict: dict,
    _returns_df: pd.DataFrame,
    _phases: pd.DataFrame,
    pattern: list,
    *,
    metric: str = "roc",
    vol_direction: str = "high",
    vol_type: str = "standard",          # "standard" | "downside"
    top_n: int = 50,                     # unified top_n for rise + fall
    top_k_common: int = 10,
    nifty_pct: float = 5.0,
    fall_pct: float = 10.0,
    leg_count: int = 2,
    max_hold_years: float = 2.0,
    vol_pct: int = 25,
    selection_side: str = "top",
    # Legacy vol_filter params (off = no filter)
    vol_filter: str = "off",
    vol_filter_n: int = 50,
    vf_windows: list = None,
    vol_filter_pct: int = 25,
    vol_filter_lookback_days: int = 365,
    # Fixed fall entry/exit mode (uses previous cycle peak/trough dates as as_of)
    use_fixed_fall: bool = False,
    fall_entry_days: int = 100,   # trailing trading days ending at prev peak date
    fall_exit_days: int = 100,    # trailing trading days ending at prev trough date
    # Exact trigger mode: sell exactly at n% fall, buy again at n% recovery from trough
    # In this mode sell_date != buy_date (buy and sell are on different dates)
    exact_trigger_mode: bool = False,

    # Fixed Hold Period mode: exit after N calendar days instead of NIFTY fall trigger.

    use_fixed_hold: bool = False,
    fixed_hold_days: int = 30,   # 30 | 60 | 90
    start_date: str | pd.Timestamp | None = None,
    end_date: str | pd.Timestamp | None = None,
    buy_on_start_date: bool = True,
    gate_params: any = None,
    db_path: str | None = None,
) -> dict:
    nifty_df = _nifty_df
    stock_dict = _stock_dict
    returns_df = _returns_df
    phases = _phases
    empty = {"per_trade_df": pd.DataFrame(), "cycle_df": pd.DataFrame(),
             "candidates_df": pd.DataFrame(), "status_df": pd.DataFrame(),
             "eligible_ranks_df": pd.DataFrame(), "leg_rank_df": pd.DataFrame(),
             "audit_df": pd.DataFrame(), "vol_audit_df": pd.DataFrame()}
    if nifty_df is None or nifty_df.empty or not stock_dict:
        return empty
    if phases is None or phases.empty:
        return empty

    legs = phases.sort_values("exit_date").reset_index(drop=True)

    db_last_date = nifty_df.index.max()
    if end_date is not None and str(end_date).strip() != "":
        end_ts = pd.Timestamp(end_date)
        last_close_date = min(db_last_date, end_ts)
    else:
        last_close_date = db_last_date

    per_trade_rows, cycle_rows, cand_rows = [], [], []
    status_rows, eligible_rows, leg_rank_rows = [], [], []
    audit_rows, vol_audit_rows = [], []

    if len(legs) < 2:
        return empty

    if start_date is not None and str(start_date).strip() != "":
        start_ts = pd.Timestamp(start_date)
        if buy_on_start_date:
            sub = nifty_df[nifty_df.index >= start_ts]
            if sub.empty or sub.index[0] > last_close_date:
                return empty
            buy_date = sub.index[0]
            buy_base = float(sub["close"].iloc[0])
            n_at_buy = buy_base
        else:
            start_scan = start_ts
            buy_date, buy_base, n_at_buy = _nifty_rise_trigger(nifty_df, start_scan, nifty_pct)
    else:
        start_scan = pd.Timestamp(legs.loc[1, "exit_date"]) if len(legs) >= 2 else pd.Timestamp(legs.loc[0, "exit_date"])
        buy_date, buy_base, n_at_buy = _nifty_rise_trigger(nifty_df, start_scan, nifty_pct)

    chained = False
    prev_fall_window = None
    cycle_no = 0
    guard = 0

    while buy_date is not None and pd.Timestamp(buy_date) <= last_close_date and guard < 2000:
        guard += 1
        buy_date = pd.Timestamp(buy_date)
        if chained:
            n_at_buy = _nifty_near(nifty_df, buy_date, "forward") or n_at_buy
            buy_base = n_at_buy

        # ── Build leg_specs ────────────────────────────────────────────────────
        if use_fixed_fall:
            # Fixed Fall Entry & Exit mode:
            #
            # Cycle 1 (prev_fall_window is None):
            #   Bootstrap using the first scheduled Fall phase dates.
            #   entry_date = peak equivalent, exit_date = trough equivalent.
            #
            # Cycle 2+ (prev_fall_window is set):
            #   Use the peak/trough DETECTED DURING THE PREVIOUS CYCLE'S hold.
            #   This is always the most relevant fall because cycles chain
            #   sequentially — sell_date of cycle N = buy_date of cycle N+1.
            #
            # Gap handling (rebuy=False path):
            #   If a cycle didn't complete (no fall/no recovery), the next cycle
            #   may start far in the future. In that case scan NIFTY between
            #   prev_sell_date and current buy_date for a more recent 10%+ fall.
            #   If found, use it; otherwise keep carrying prev_fall_window forward.

            if prev_fall_window is None:
                # ── Cycle 1 bootstrap ─────────────────────────────────────────
                # Select the most recent Fall phase that completed before or on buy_date
                _prior_falls = legs[(legs["trade"].astype(str).str.capitalize() == "Fall") &
                                    (pd.to_datetime(legs["exit_date"]) <= buy_date)]
                if not _prior_falls.empty:
                    _ref_fall = _prior_falls.iloc[-1]
                    prev_fall_window = (
                        pd.Timestamp(_ref_fall["entry_date"]),
                        pd.Timestamp(_ref_fall["exit_date"]),
                    )
                else:
                    # Scan NIFTY backward from buy_date for a recent >=fall_pct% drop
                    _scan_result = _find_last_significant_fall(nifty_df, buy_date, fall_pct)
                    if _scan_result["peak_date"] is not None and _scan_result["trough_date"] is not None:
                        prev_fall_window = (
                            _scan_result["peak_date"],
                            _scan_result["trough_date"],
                        )
                    else:
                        # Fallback to first available fall if buy_date is before all falls
                        _all_falls = legs[legs["trade"].astype(str).str.capitalize() == "Fall"]
                        if not _all_falls.empty:
                            _ref_fall = _all_falls.iloc[0]
                            prev_fall_window = (
                                pd.Timestamp(_ref_fall["entry_date"]),
                                pd.Timestamp(_ref_fall["exit_date"]),
                            )
                        else:
                            nb, bb, na = _nifty_rise_trigger(
                                nifty_df, buy_date + pd.Timedelta(days=1), nifty_pct)
                            buy_date, buy_base, n_at_buy, chained = nb, bb, na, False
                            continue

            # ── Gap scan: if there's a newer fall since prev_fall_window ──────
            # Only runs when: not chained (gap between cycles), NOT cycle 0
            # (first-ever cycle uses bootstrap directly), and the gap is > 90 days.
            _pfw_trough = prev_fall_window[1]
            if not chained and cycle_no > 0 and buy_date > _pfw_trough + pd.Timedelta(days=90):
                # Scan between prev trough and current buy_date for a newer fall
                _scan_result = _find_last_significant_fall(
                    nifty_df[nifty_df.index > _pfw_trough], buy_date, fall_pct)
                if _scan_result["peak_date"] is not None:
                    prev_fall_window = (
                        _scan_result["peak_date"],
                        _scan_result["trough_date"],
                    )

            _prev_peak_date, _prev_trough_date = prev_fall_window

            leg_specs = _build_fixed_fall_leg_specs(
                _prev_peak_date, _prev_trough_date,
                fall_entry_days, fall_exit_days)
            _fe_str = _prev_peak_date.strftime("%d-%b-%Y")
            _fx_str = _prev_trough_date.strftime("%d-%b-%Y")
            legs_used_txt = (f"Fall Entry {fall_entry_days}d@{_fe_str} + "
                             f"Fall Exit {fall_exit_days}d@{_fx_str}")
            sel_start = _prev_peak_date
            sel_end   = _prev_trough_date
            _fall_leg = leg_specs[0]
            swapped_fall_window = False
        else:
            # Auto rise/fall detection
            recent = _last_two_legs(legs, buy_date)
            if recent is None:
                nb, bb, na = _nifty_rise_trigger(nifty_df, buy_date + pd.Timedelta(days=1), nifty_pct)
                buy_date, buy_base, n_at_buy, chained = nb, bb, na, False
                continue

            swapped_fall_window = False
            if len(recent) >= 2:
                L1, L2 = recent[0], recent[1]
                t1 = str(L1["trade"]).capitalize()
                t2 = str(L2["trade"]).capitalize()
                if t1 == "Fall" and t2 == "Rise" and prev_fall_window is not None:
                    pk_d, tr_d = prev_fall_window
                    leg_specs = [
                        _sched_leg_spec(L2),
                        {"label": "Fall", "kind": "dynamic",
                         "phase_id": None, "entry": pd.Timestamp(pk_d),
                         "exit": pd.Timestamp(tr_d), "trade": "Fall"},
                    ]
                    swapped_fall_window = True
                else:
                    leg_specs = [_sched_leg_spec(L1), _sched_leg_spec(L2)]
            else:
                leg_specs = [_sched_leg_spec(recent[0])]

            _seen = {}
            for _ls in leg_specs:
                _lbl = _ls["label"]
                if _lbl in _seen:
                    _seen[_lbl] += 1
                    _ls["label"] = f"{_lbl} {_seen[_lbl]}"
                else:
                    _seen[_lbl] = 1

            legs_used_txt = " + ".join(l["label"] for l in leg_specs)
            _rise_legs = [l for l in leg_specs if str(l["trade"]).capitalize() == "Rise"]
            sel_start = (pd.Timestamp(_rise_legs[0]["entry"]) if _rise_legs
                         else min(pd.Timestamp(l["entry"]) for l in leg_specs))
            if swapped_fall_window:
                sel_end = pd.Timestamp(leg_specs[-1]["exit"])
            else:
                sel_end = max(pd.Timestamp(l["exit"]) for l in leg_specs)

            _fall_legs = [l for l in leg_specs if str(l["trade"]).capitalize() == "Fall"]
            _fall_leg = _fall_legs[-1] if _fall_legs else None

        # ── Volatility filter (only when NOT in fixed-fall mode and vol_filter != off) ─
        _fe = (pd.Timestamp(_fall_leg["entry"]) if _fall_leg else None)
        _fx = (pd.Timestamp(_fall_leg["exit"]) if _fall_leg else None)

        # In fixed-fall mode we do NOT use a separate vol_filter gate —
        # the ranking metric itself incorporates volatility if chosen.
        if not use_fixed_fall and vol_filter != "off":
            _ref_map = {
                "fall_entry": _fe,
                "fall_exit": _fx,
                "buy": buy_date,
            }
            _wins_cfg = vf_windows if vf_windows else [("buy", int(vol_filter_lookback_days))]
            _windows = [(ref, _ref_map.get(ref), int(days)) for (ref, days) in _wins_cfg
                        if _ref_map.get(ref) is not None]
            vol_allowed, vol_filter_basis, vol_scores, vol_detail = _universe_vol_topn(
                stock_dict, _windows, vol_filter_n, vol_filter, vol_type)
        else:
            vol_allowed, vol_filter_basis, vol_scores, vol_detail = None, "off", {}, {}
            _wins_cfg = []

        # rank within windows
        _asc_rank = (vol_filter != "high")
        _win_groups: dict = {}
        for _tkr, _recs in (vol_detail or {}).items():
            for _rec in _recs:
                _win_groups.setdefault(_rec["window_idx"], []).append((_tkr, _rec))
        for _wi, _items in _win_groups.items():
            _items.sort(key=lambda kv: kv[1]["vol"], reverse=not _asc_rank)
            for _rk, (_tkr, _rec) in enumerate(_items, start=1):
                _rec["window_rank"] = _rk

        if vol_detail:
            for tkr, recs in vol_detail.items():
                for rec in recs:
                    vol_audit_rows.append({
                        "cycle": cycle_no + 1, "ticker": tkr,
                        "reference": rec.get("ref", ""),
                        "window_rank": rec.get("window_rank"),
                        "as_of_date": rec.get("as_of"),
                        "requested_trading_days": rec.get("req_days"),
                        "vol_window_start": rec.get("vol_start"),
                        "vol_window_end": rec.get("vol_end"),
                        "trading_days_used": rec.get("vol_days"),
                        "volatility": rec.get("vol"),
                        "in_top_n": "Yes" if rec.get("in_topn") else "No",
                    })

        _cfl_vol_dir = (vol_direction if metric != "off"
                        else (vol_filter if vol_filter in ("low", "high") else "low"))

        # ── Base selection gate: all stocks with data on buy_date ──────────────
        base_at_buy: set = set()
        for _tkr, _sdf in stock_dict.items():
            _brows = _sdf[_sdf.index == buy_date]
            if not _brows.empty:
                base_at_buy.add(_tkr)
        
        selection_gate = base_at_buy

        # Merge with existing vol_allowed (if active)
        effective_allowed = (
            selection_gate & vol_allowed if vol_allowed is not None
            else selection_gate
        )

        common, cand_detail, cmeta = _common_from_legs(
            leg_specs, returns_df, stock_dict, metric, _cfl_vol_dir,
            top_n, top_k_common,
            vol_pct=vol_pct, selection_side=selection_side,
            allowed=effective_allowed, vol_scores=vol_scores, vol_type=vol_type,
            nifty_df=nifty_df, gate_params=gate_params, db_path=db_path)
        cmeta["vol_filter"] = vol_filter if not use_fixed_fall else "off"
        cmeta["vol_filter_basis"] = vol_filter_basis
        cmeta["n_effective_universe"] = len(effective_allowed)

        for ar in cmeta.get("audit_rows", []):
            row = dict(ar)
            row["cycle"] = cycle_no + 1
            if use_fixed_fall:
                # Fixed mode: fall_entry = prev peak date, fall_exit = prev trough date
                row["fall_entry_date"] = pd.Timestamp(leg_specs[0].get("as_of", leg_specs[0]["exit"]))
                row["fall_exit_date"]  = pd.Timestamp(leg_specs[1].get("as_of", leg_specs[1]["exit"]))
            else:
                row["fall_entry_date"] = _fe
                row["fall_exit_date"]  = _fx
            audit_rows.append(row)

        if metric == "off" and vol_detail:
            _common_set = set(vol_allowed or [])
            _bought_set = set(common)
            for tkr, recs in vol_detail.items():
                row = {"cycle": cycle_no + 1, "ticker": tkr,
                       "fall_entry_date": _fe, "fall_exit_date": _fx}
                for i, rec in enumerate(sorted(recs, key=lambda r: r["window_idx"]), start=1):
                    row[f"w{i}_ref"] = rec.get("ref", "")
                    row[f"w{i}_vol"] = rec.get("vol")
                    row[f"w{i}_start"] = rec.get("vol_start")
                    row[f"w{i}_end"] = rec.get("vol_end")
                    row[f"w{i}_days"] = rec.get("vol_days")
                    row[f"w{i}_rank"] = rec.get("window_rank")
                    row[f"w{i}_in_topn"] = "Yes" if rec.get("in_topn") else "No"
                in_common = tkr in _common_set
                row["common"] = "Yes" if in_common else "No"
                row["selected_to_buy"] = "Yes" if tkr in _bought_set else "No"
                row["reason"] = ("" if in_common else
                                 f"Not in Top-N of every {_wins_cfg[0][1] if _wins_cfg else 100}-day window")
                audit_rows.append(row)

        if not common:
            status_rows.append({
                "cycle": cycle_no + 1, "legs_used": legs_used_txt,
                "buy_trigger_date": buy_date, "sell_date": None, "n_trades": 0,
                "status": "Skipped — no common stocks across legs",
                "reason": cmeta.get("selection_funnel", "")})
            nb, bb, na = _nifty_rise_trigger(nifty_df, buy_date + pd.Timedelta(days=1), nifty_pct)
            buy_date, buy_base, n_at_buy, chained = nb, bb, na, False
            continue

        # ── Fixed Hold Period mode OR NIFTY fall detection ─────────────────
        # ── Fall detection & sell_date — identical logic for all modes ──────────
        # Fixed hold only overrides sell_date at the end; all chaining/recovery is normal.
        pf = _nifty_peak_fall(nifty_df, buy_date, fall_pct)
        fall_date = pf["fall_date"]
        held_no_fall = fall_no_recovery = rebuy = False
        trough_close = trough_date = None
        next_buy_date_for_chain = None

        if fall_date is None:
            sell_date = last_close_date
            held_no_fall = True
        else:
            tr = _nifty_trough_recovery(nifty_df, fall_date, nifty_pct)
            trough_close = tr["trough_close"]
            trough_date = tr["trough_date"]
            if tr["recovery_date"] is None:
                fall_no_recovery = True
                if exact_trigger_mode:
                    sell_date = pd.Timestamp(fall_date)
                else:
                    sell_date = last_close_date
            else:
                rebuy = True
                if exact_trigger_mode:
                    sell_date = pd.Timestamp(fall_date)
                    next_buy_date_for_chain = pd.Timestamp(tr["recovery_date"])
                else:
                    sell_date = pd.Timestamp(tr["recovery_date"])
                    next_buy_date_for_chain = sell_date

        sell_date = pd.Timestamp(sell_date)
        over_2y_no_fall = held_no_fall and (sell_date - buy_date).days >= max_hold_years * 365.0

        # Fixed hold: override sell_date to buy_date + N days only.
        # Buy dates, chaining, and next-cycle logic are identical to normal mode.
        if use_fixed_hold:
            _fixed_sell = buy_date + pd.Timedelta(days=int(fixed_hold_days))
            sell_date = pd.Timestamp(min(_fixed_sell, last_close_date))
            over_2y_no_fall = False

        sell_date = pd.Timestamp(sell_date)
        n_at_sell = _nifty_near(nifty_df, sell_date, "backward") or n_at_buy
        held_days = (sell_date - buy_date).days
        over_2y_no_fall = over_2y_no_fall  # already set above
        n_buy_px = _nifty_near(nifty_df, buy_date, "forward") or n_at_buy
        nifty_cycle_ret = (round((n_at_sell - n_buy_px) / n_buy_px * 100, 4)
                           if n_buy_px and n_buy_px > 0 else None)

        # ── Buy execution (fixed dates) ───────────────
        traded, cycle_returns = set(), []
        for tkr in common:
            if len(traded) >= top_k_common:
                break
            sdf = stock_dict.get(tkr)
            if sdf is None:
                continue

            # ── Entry ─────────────────────────────────────────────────────────
            buy_rows = sdf[sdf.index == buy_date]
            if buy_rows.empty:
                continue

            epx = float(buy_rows["close"].iloc[0])
            if epx <= 0:
                continue

            # ── Exit ──────────────────────────────────────────────────────────
            sell_rows = sdf[sdf.index == sell_date]
            if not sell_rows.empty:
                xpx   = float(sell_rows["close"].iloc[0])
                xdate = sell_date
            else:
                # fallback = last available close on or before sell_date
                fallback = sdf[sdf.index <= sell_date]
                if fallback.empty:
                    continue
                xpx   = float(fallback["close"].iloc[-1])
                xdate = fallback.index[-1]

            ret   = round((xpx - epx) / epx * 100, 4)
            alpha = round(ret - nifty_cycle_ret, 4) if nifty_cycle_ret is not None else None
            d_held = (xdate - buy_date).days

            traded.add(tkr)
            cycle_returns.append(ret)
            per_trade_rows.append({
                "window_idx": cycle_no, "cycle": cycle_no + 1, "source_window": cycle_no + 1,
                "legs_used": legs_used_txt,
                "pattern_start": sel_start, "pattern_end": sel_end,
                "buy_phase_start": sel_start, "buy_trigger_date": buy_date,
                "nifty_buy_base": round(buy_base, 2) if buy_base else None,
                "nifty_at_buy": round(n_at_buy, 2) if n_at_buy else None,
                "peak_close": round(pf["peak_close"], 2) if pf["peak_close"] else None,
                "peak_date": pf["peak_date"], "fall_confirm_date": fall_date,
                "trough_close": round(trough_close, 2) if trough_close else None,
                "trough_date": trough_date,
                "sell_date_cycle": sell_date,
                "nifty_at_sell": round(n_at_sell, 2) if n_at_sell else None,
                "ticker": tkr, "entry_date": buy_date, "exit_date": xdate,
                "entry_price": round(epx, 4), "exit_price": round(xpx, 4),
                "return_pct": ret, "nifty_return": nifty_cycle_ret, "alpha": alpha,
                "days_held": d_held,
                "selection_mode": "raw",
                "exit_mode": (f"fixed-hold-{int(fixed_hold_days)}d" if use_fixed_hold else "nifty-fall"),
                "fixed_hold_days": (int(fixed_hold_days) if use_fixed_hold else None),
                "scheduled_sell_date": sell_date,   # = buy_date+N in fixed-hold, cycle sell otherwise
                "status": ("held-no-fall>2y" if over_2y_no_fall else
                           "held-no-fall" if held_no_fall else
                           "fall-no-recovery" if fall_no_recovery else "completed"),
            })

        winrank = {}
        _wvol = {}
        if metric == "off" and vol_detail:
            for _t, _recs in vol_detail.items():
                for _i, _rec in enumerate(sorted(_recs, key=lambda r: r["window_idx"]), start=1):
                    _wvol.setdefault(_t, {})[f"w{_i}_vol ({_rec.get('ref','')})" ] = _rec.get("vol")
                    _wvol[_t][f"w{_i}_rank"] = _rec.get("window_rank")
        if not cand_detail.empty:
            for _, cr in cand_detail.iterrows():
                winrank[cr["ticker"]] = int(cr.get("common_rank", 0))
                base = {
                    "cycle": cycle_no + 1, "window": cycle_no + 1,
                    "common_rank": int(cr.get("common_rank", 0)), "ticker": cr["ticker"],
                    "mean_metric": cr.get("mean_metric", 0.0),
                    "mean_roc": cr.get("mean_roc"), "mean_vol": cr.get("mean_vol"),
                    "mean_beta_nifty": cr.get("mean_beta_nifty"),
                    "mean_corr_nifty": cr.get("mean_corr_nifty"),
                    "beta_rank": cr.get("beta_rank"),
                    "selected_to_buy": bool(cr.get("selected_to_buy", False)),
                    "traded": cr["ticker"] in traded}
                if metric == "off":
                    base["avg_vol"] = cr.get("mean_vol")
                    base.update(_wvol.get(cr["ticker"], {}))
                for col in cand_detail.columns:
                    if "|" in col:
                        base[col] = cr.get(col)
                cand_rows.append(base)

        n_tr = len(traded)
        if n_tr == 0:
            status_rows.append({
                "cycle": cycle_no + 1, "legs_used": legs_used_txt,
                "buy_trigger_date": buy_date, "sell_date": sell_date, "n_trades": 0,
                "status": "No tradable stocks (none of the ranked stocks were available on buy date)", "reason": ""})
            if rebuy and next_buy_date_for_chain is not None and next_buy_date_for_chain < last_close_date:
                prev_fall_window = (pf["peak_date"], trough_date) if trough_date is not None else prev_fall_window
                buy_date, chained = next_buy_date_for_chain, True
                continue
            break

        if metric != "off":
            leg_rank_rows.extend(_leg_full_rank_rows(
                cycle_no, leg_specs, returns_df, stock_dict, metric, vol_direction,
                top_n, winrank, set(common), traded, allowed=None if metric == "gate" else effective_allowed,
                vol_type=vol_type, selection_side=selection_side, nifty_df=nifty_df,
                gate_params=gate_params, db_path=db_path))
        else:
            leg_rank_rows.extend(_vol_window_leg_rows(
                cycle_no, vol_detail, vol_filter_n, set(effective_allowed), traded))

        avg_ret = float(np.mean(cycle_returns)) if cycle_returns else 0.0
        if use_fixed_hold:
            status_txt = f"Fixed hold — {int(fixed_hold_days)}d"
            reason = ""
        else:
            status_txt = ("Completed cycle" if not held_no_fall and not fall_no_recovery else
                          "Held — no fall within data" if held_no_fall else
                          "Fall hit — no recovery within data")
            reason = ""
            if over_2y_no_fall:
                reason = (f"No {fall_pct:g}% fall within {max_hold_years:g} years — "
                          f"kept holding the same stocks ({held_days} days held).")

        # fall_entry_date = prev peak date (as_of for Fall Entry window)
        # fall_exit_date  = prev trough date (as_of for Fall Exit window)
        if use_fixed_fall:
            _cycle_fe = pd.Timestamp(leg_specs[0].get("as_of", leg_specs[0]["exit"]))
            _cycle_fx = pd.Timestamp(leg_specs[1].get("as_of", leg_specs[1]["exit"]))
        else:
            _cycle_fe = _fe
            _cycle_fx = _fx

        cycle_rows.append({
            "cycle": cycle_no + 1, "source_window": cycle_no + 1,
            "legs_used": legs_used_txt,
            "legs_window_start": sel_start, "legs_window_end": sel_end,
            "buy_trigger_date": buy_date,
            "nifty_buy_base": round(buy_base, 2) if buy_base else None,
            "nifty_at_buy": round(n_at_buy, 2) if n_at_buy else None,
            "peak_close": round(pf["peak_close"], 2) if pf["peak_close"] else None,
            "peak_date": pf["peak_date"], "fall_confirm_date": fall_date,
            "fall_close": round(pf["fall_close"], 2) if pf["fall_close"] else None,
            "trough_close": round(trough_close, 2) if trough_close else None,
            "trough_date": trough_date,
            "sell_date": sell_date,
            "nifty_at_sell": round(n_at_sell, 2) if n_at_sell else None,
            "nifty_cycle_return": nifty_cycle_ret, "n_stocks": n_tr,
            "avg_return_pct": round(avg_ret, 4), "held_days": held_days,
            "vol_filter_basis": cmeta.get("vol_filter_basis", "off"),
            "fall_entry_date": _cycle_fe,
            "fall_exit_date": _cycle_fx,
            "selection_funnel": cmeta.get("selection_funnel", ""),
            "n_universe": cmeta.get("n_universe"),
            "n_effective_universe": cmeta.get("n_effective_universe"),
            "selection_mode": "raw",
            "exit_mode": f"fixed-hold-{int(fixed_hold_days)}d" if use_fixed_hold else "nifty-fall",
            "fixed_hold_days": int(fixed_hold_days) if use_fixed_hold else None,
            "n_common": cmeta.get("n_common_total"),
            "n_selected": cmeta.get("n_selected"),
            "status": status_txt, "reason": reason})
        status_rows.append({
            "cycle": cycle_no + 1, "legs_used": legs_used_txt,
            "buy_trigger_date": buy_date, "sell_date": sell_date, "n_trades": n_tr,
            "status": status_txt, "reason": reason})

        # ── Eligible ranks (quartile ranking) ─────────────────────────────────
        elig = []
        for tkr, sdf in stock_dict.items():
            epx, edate = _entry_price(sdf, buy_date, sell_date)
            if epx is None or edate is None or epx <= 0:
                continue
            if pd.Timestamp(edate) != pd.Timestamp(buy_date):
                continue
            xpx, xdate = _exit_price(sdf, sell_date, edate)
            if xpx is None:
                continue
            ret = round((xpx - epx) / epx * 100, 4)
            alpha = round(ret - nifty_cycle_ret, 4) if nifty_cycle_ret is not None else None
            elig.append({
                "cycle": cycle_no + 1, "source_window": cycle_no + 1, "ticker": tkr,
                "entry_date": edate, "exit_date": xdate,
                "entry_price": round(epx, 4), "exit_price": round(xpx, 4),
                "return_pct": ret, "nifty_return": nifty_cycle_ret, "alpha": alpha,
                "traded": tkr in traded})
        if elig:
            eb = (pd.DataFrame(elig).sort_values("return_pct", ascending=False).reset_index(drop=True))
            n_el = len(eb)
            eb["rank"] = range(1, n_el + 1)
            eb["n_eligible"] = n_el
            eb["quartile"] = [str(quartile_label(r, n_el)).split()[0] for r in eb["rank"]]
            eligible_rows.append(eb)

        cycle_no += 1

        if rebuy and next_buy_date_for_chain is not None and next_buy_date_for_chain < last_close_date and trough_date is not None:
            # Carry the current cycle's fall window forward (same for fixed_hold and normal).
            prev_fall_window = (pf["peak_date"], trough_date)
            buy_date, chained = next_buy_date_for_chain, True
        else:
            buy_date = None

    return {
        "per_trade_df": pd.DataFrame(per_trade_rows),
        "cycle_df": pd.DataFrame(cycle_rows),
        "candidates_df": pd.DataFrame(cand_rows),
        "status_df": pd.DataFrame(status_rows),
        "eligible_ranks_df": (pd.concat(eligible_rows, ignore_index=True)
                              if eligible_rows else pd.DataFrame()),
        "leg_rank_df": pd.DataFrame(leg_rank_rows),
        "audit_df": pd.DataFrame(audit_rows),
        "vol_audit_df": pd.DataFrame(vol_audit_rows),
    }
