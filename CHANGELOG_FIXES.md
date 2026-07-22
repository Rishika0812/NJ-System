# S³ Momentum Engine — Correction Change Log

All changes are confined to the **Momentum Based Investment** strategy. The
short-term engine is untouched.

## Files modified

| File | What changed |
|------|--------------|
| `core/momentum_engine.py` | Aux2 entry gate, ROC date traceability, 100-day volatility window, low-vol intersection, audit data |
| `app.py` | Removed "Top-N per window to keep"; N now sourced from Stock-Selection Top-N; threaded audit data to export + CSVs |
| `export/momentum_interactive.py` | New "Calculation Audit" and "100-Day Volatility Audit" sheets |

No other files were changed.

---

## Bug-by-bug

### #1 Low-Volatility common-stock logic (intersection)
**Found:** the count-based volatility filter already intersected per-window Top-N
sets, but (a) the count came from a *separate* box, not the Stock-Selection N, and
(b) the volatility window was a **calendar-day** lookback, not a true 100-trading-day
window, and the dates used were not reported.
**Fixed:** `_universe_vol_topn` now keeps `Top-N lowest-vol @ fall entry ∩
Top-N lowest-vol @ fall exit` (when both windows are ticked) using a real
100-trading-day window, and returns per-stock window detail. With Filter =
Low-Volatility and Rank = OFF, that intersection **is** the candidate pool from
which the K "Common Stocks to Buy" are taken.

### #2 "Top-N per Window to Keep" removed
**Found:** a `mom_vf_n` number box duplicated the Stock-Selection Top-N.
**Fixed:** box removed from the UI. `mom_vol_filter_n` is now set to the single
Stock-Selection **Top N** (the Rise value is the canonical N), shown via a caption.

### #3 ROC date audit
**Found:** `_leg_roc_map` discarded the exit date and exposed no dates at all.
**Fixed:** new `_leg_roc_detail` returns the **actual** entry/exit dates used plus
the Aux2 value on the leg entry/exit dates. These flow into the Leg-Ranking sheet,
the Calculation Audit sheet, and CSV exports as **ROC Start Date / ROC End Date / ROC**.

### #4 & #5 ROC / ROC×Vol eligibility (Rise entry Aux2 == 1)
**Found:** `_entry_price` silently walked forward to the next Aux2=1 close, so a
stock whose **Rise entry date** had Aux2 = 0 / NULL / missing still entered ROC and
ROC×Vol ranking using a *later* date.
**Fixed:** a hard gate now runs **before** ranking in both `_common_from_legs` and
`_leg_full_rank_rows`. For a **Rise** leg, only stocks with `Aux2 == 1` on the exact
Rise entry date are eligible for ROC / ROC×Vol. The Fall (exit) side is unrestricted.
Excluded stocks are recorded in the audit with the reason.
*Verified on real data:* e.g. `ABCAPITAL` (entry Aux2 = 0) is excluded from ROC ranking.

### #6 Export transparency (ROC & ROC×Vol fields)
**Fixed:** the Calculation Audit sheet/CSV carries, per stock: Stock, Rise/Leg Entry,
Rise/Leg Exit, Fall Entry, Fall Exit, Entry Aux2, Exit Aux2, ROC Start Date, ROC End
Date, ROC, Volatility window dates + trading days, Volatility, ROC×Vol, Qualified For
Ranking (Yes/No), In Leg Top-N, Selected?, and Reason.

### #7 100-Day Volatility transparency
**Fixed:** `_trailing_vol_window` uses the last **100 trading days** of Aux2=1 closes
and returns the exact window Start Date, End Date, and Trading Days Used. The
"100-Day Volatility Audit" sheet/CSV shows these for every measurement.

### #8 "Calculation Audit" sheet
**Fixed:** added. One row per (cycle, stock, leg) with every decision input and the
qualify/exclude reason, allowing full verification of the engine.

---

## Validation (run on the supplied `Data.xlsx`)

- **ROC dates (Fix #3):** ROC Start/End equal the actual Aux2=1 dates used.
- **Aux2 gate (Fix #4/#5):** stocks with Rise-entry Aux2 ≠ 1 are excluded from ROC/ROC×Vol; reason logged.
- **100-Day volatility (Fix #7):** windows report exact start/end and `trading_days_used = 100` where history allows.
- **Low-Vol intersection (Fix #1):** `intersection(Top-N @ fall entry, Top-N @ fall exit)` produced the common set.
- **Workbook:** "Calculation Audit" and "100-Day Volatility Audit" sheets render.

Sample outputs: `sample_calculation_audit.csv`, `sample_volatility_audit.csv`.

> Note: validation used the real stock universe with a synthetic phase/NIFTY
> schedule, because only `Data.xlsx` was supplied (no `Dates.xlsx`). Logic is
> data-independent; running the app with your real `Dates.xlsx` will populate the
> same sheets from real phases.

---

## Update — "Rank By = Off" now ignores everything except volatility

**Reported:** with only the Low-Volatility filter on (Rank By = Off), the engine
still computed ROC / ROC×Vol / leg-window volatility and showed them in the audit,
so it looked like the leg windows were being considered.

**Fixed:** when Rank By = Off the engine now short-circuits to a pure-volatility path:
- one 100-day volatility per ticked window → Top-N lowest (e.g. 50),
- intersection of the windows (= the "common" set),
- take the top-K calmest (e.g. 10) — bought.

No legs, ROC, ROC×Vol or leg-window volatility are computed or considered. The
"Calculation Audit" and "Cycle Leg Rankings" are intentionally empty for an Off run;
the only relevant audit is the "100-Day Volatility Audit" (per-window Top-N + intersection).

---

## Update — Volatility switched to natural log (ln)

**Reported:** volatility should be ln-based.

**Was:** `std(log10(Pₜ/Pₜ₋₁)) × √252` (log base 10).
**Now:** `std(ln(Pₜ/Pₜ₋₁)) × √252` (natural log) in `_annualized_vol`
(used by every volatility path: 100-day filter, leg-window vol, ROC×Vol).

Because ln = log10 × 2.302585, the **ranking/selection is unchanged** (monotonic),
but reported volatility values are now ~2.3026× larger (correct ln scale).

**ROC** remains the simple arithmetic Rate of Change `(P_exit − P_entry)/P_entry × 100`
— this is the standard ROC definition and also the basis for actual trade P&L. It is
NOT a log return. (Tell me if you want the ROC *ranking metric* changed to log return
`ln(P_exit/P_entry)×100`; P&L would stay arithmetic.)

---

## Update — Off mode gets its own simplified sidebar (no leg controls)

**Reported:** with Rank By = Off the sidebar still showed leg-based "Top N — Rise legs /
Top N — Fall legs", and the Overview still described "common of the last two scheduled
legs" — so it looked like legs were considered.

**Fixed (UI):** when Rank By = Off the sidebar now shows ONLY:
- **Filter type** (Low / High volatility),
- **Top N (in EACH 100-day window)** — single control,
- **Common Stocks to Buy** (K).

The Rise/Fall "Stock Selection" sliders and the reference-window tick boxes are hidden.
The two 100-day windows are fixed to the Fall **peak (entry)** and 10%↓ **trough (exit)**.
The Overview text for an Off run no longer mentions legs.

**Trade cycle is unchanged** in every mode: buy on +x% NIFTY rise, hold, confirm a fall
at the chosen % drop from the running peak, mark the trough, sell on the +x% recovery off
the trough, then rebuy — same procedure as the other modes.

---

## Update — Export now adapts fully to the setting (Off-mode leak fixed)

**Found during review:** in Off mode the merged "Cycle Leg Rankings" sheet had a
*fallback* that recomputed ROC-based leg rankings even though ranking was Off, and the
Portfolio Summary still printed "Selection legs / Top-N (Rise/Fall)".

**Fixed:**
- "Cycle Leg Rankings" no longer recomputes anything when metric = Off (shows a note
  pointing to the 100-Day Volatility Audit).
- Portfolio Summary is Off-aware: "Selection method = Pure 100-day volatility …",
  "Top-N per 100-day window" instead of Rise/Fall, "Volatility basis = ln".

### Per-sheet behaviour by setting (verified by code review)
- Trades / Trade Log / Common Stocks P&L / Cycle Ledger / Per-Cycle Equity / Stock
  Summary / NIFTY Path / Eligible Ranking / Yearwise / Monthwise → always render the
  engine's setting-dependent results (the basket differs per setting).
- Calculation Audit → populated for ROC / Both / Vol×ROC; empty-with-note for Off.
- 100-Day Volatility Audit → populated whenever the volatility filter is active
  (always in Off; in other modes when the filter is enabled); note otherwise.
- Cycle Leg Rankings / Cycle Candidates → leg columns for ROC/Vol/Both/Vol×ROC;
  for Off, Candidates show volatility rank only and Leg Rankings show the Off note.
- Portfolio Summary → config block reflects the exact metric, N, K, filter and ln basis.

---

## Update — Off (100-day volatility) gets a fully customised workbook

Now the WHOLE workbook reflects the 100-day-volatility basis for the Off setting,
matching the depth of the other modes:

- **Cycle Leg Rankings → "Volatility Window Rankings":** per cycle, the Top-N stocks
  ranked by 100-day ln volatility in EACH window (Fall entry & Fall exit), with the
  exact window start/end dates, trading-days used, vol value, and ✓ for common
  (in Top-N of every window) / bought. (The leg-wise ranking equivalent.)
- **Calculation Audit (Off schema):** one row per stock showing its 100-day volatility,
  dates, trading days and rank in EACH window, whether it is common to all windows,
  and whether it was bought — i.e. the explicit "on what basis was this selected".
- **Cycle Ledger:** new columns — Vol Window 1/2 dates, Vol Window Days (100),
  and Avg 100-Day Vol (bought) — the 100-day calculation per cycle.
- **Cycle Candidates:** enriched with each stock's per-window 100-day vol and rank.
- Investment Analysis, Per-Cycle Equity, Eligible Ranking (Q4..Q1 quartiles),
  Trade Log, Stock Summary, Yearwise/Monthwise — unchanged and correct (they are
  metric-independent; quartiles rank realized buy→sell return, bought = Traded ✓).

Titles/Portfolio-Summary are setting-aware throughout (ln basis, "Top-N per 100-day
window", "Pure 100-day volatility" selection method).

---

## Aux1 Selection Mode

### What changed

**`core/data_loader.py`**
- `_clean_stock` now reads the `Aux1` column (alongside `Aux2`) from each stock sheet.
- Stocks without an `Aux1` column default to `aux1=0` so backward compatibility is preserved.

**`core/momentum_engine.py`**
- New parameter `use_aux1_selection: bool = False` on `run_momentum`.
- When `True`:
  - **Selection pool** = stocks with `aux1=1` on buy_date (instead of `aux2=1`).
  - **Buy gate** = `aux1=1` on buy_date; stock is bought even if `aux2=0`.
  - **Exit** = close on sell_date; fallback = last available close (not restricted to `aux2=1`).
  - **Quartile / eligible ranking** = `aux2=1` universe (unchanged). Bought stocks (from `aux1=1`) are placed into the quartile of the `aux2` ranking.
  - New columns added to per-trade and cycle records: `selection_mode`, `aux1_on_buy_date`, `aux2_on_buy_date`, `aux2_on_sell_date`.
  - Eligible ranks include `in_aux1` and `in_aux2` flags per row.

**`app.py`**
- New sidebar checkbox **"Enable Aux1 Selection"** under *Aux1 Selection Mode* section.
- Overview card shows a green badge when Aux1 mode is active.
- Eligible ranking view in *Cycles & Trades* tab shows `InAux1` / `InAux2` columns.

**`export/momentum_exporter.py` + `export/momentum_interactive.py`**
- Cycle Ledger: `NAux1Eligible`, `SelectionMode` columns added.
- Trade Log: `SelectionMode`, `Aux1OnBuy`, `Aux2OnBuy`, `Aux2OnSell` columns added.
- Eligible Stock Ranking sheet: `InAux1`, `InAux2` columns added.
- Per-cycle kv block: Aux1 eligible count + selection mode shown.

---

## Fixed Hold Period Mode (v21)

### New option: "Enable Fixed Hold Period"

When enabled, trades exit after a **fixed number of calendar days** (30 / 60 / 90, user selects) instead of waiting for a NIFTY fall trigger.

**Exit priority (when Fixed Hold is ON):**
1. If Aux1 Selection is also ON → exit on the **first date where Aux1=0** after buy_date, **OR** buy_date + N days — whichever comes first.
2. If Aux1 Selection is OFF → exit at buy_date + N days (Aux2=1 preferred for price lookup).

**What does NOT apply in Fixed Hold mode:**
- NIFTY 10% fall detection is skipped entirely.
- `max_hold_years` timeout is ignored (always exits at day N).
- Exact Trigger Mode sell logic is ignored.

**Cycle chaining:** After each fixed-hold exit, the system searches for the next NIFTY rise trigger starting from `sell_date + 1 day`.

### New fields in output

**Cycle Ledger / cycle_df:**
- `exit_mode` — `"fixed-hold-30d"` / `"fixed-hold-60d"` / `"fixed-hold-90d"` / `"nifty-fall"`
- `fixed_hold_days` — N value (30/60/90), or `None` in NIFTY-fall mode

**Per-trade / Trade Log:**
- `exit_mode` — same as above per trade
- `fixed_hold_days` — the N used for this trade
- `scheduled_sell_date` — buy_date + N days (the cap date, regardless of aux1 exit)
- `aux1_exit_triggered` — `Y` if aux1 dropped to 0 before the scheduled sell date

### Files changed
- `core/momentum_engine.py` — new `use_fixed_hold`, `fixed_hold_days` params + fall-detection bypass + fixed chaining
- `app.py` — new "Fixed Hold Period" checkbox + hold-days selectbox (30/60/90) + overview card
- `export/momentum_exporter.py` — Trade Log and per-cycle kv block
- `export/momentum_interactive.py` — `_TradesData`, cycle ledger, dashboard summary
