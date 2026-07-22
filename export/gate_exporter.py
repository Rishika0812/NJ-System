"""
S³ Export — Gate System workbook
==================================
Lightweight, self-contained Excel export for Gate System runs (Ranking
Metric = "Off"). Kept separate from ``momentum_exporter.py`` /
``excel_exporter.py`` (which assume a full ROC/Vol/Beta trade-simulation
ledger) so this addition can never destabilize that existing, larger export
path.
"""
from __future__ import annotations

import io

import pandas as pd


def generate_gate_system_excel(leg_rank_df: pd.DataFrame, cycle_df: pd.DataFrame,
                                params_df: pd.DataFrame) -> bytes:
    """Build a two-sheet workbook: Leg Scorecards + Gate Parameters."""
    import xlsxwriter

    buf = io.BytesIO()
    wb = xlsxwriter.Workbook(buf, {"in_memory": True})

    hdr_fmt = wb.add_format({"bold": True, "bg_color": "#1F2937", "font_color": "#FFFFFF",
                              "border": 1, "align": "center"})
    num_fmt = wb.add_format({"num_format": "0.000"})
    pct_fmt = wb.add_format({"num_format": "0.0%"})
    sel_fmt = wb.add_format({"bg_color": "#C6EFCE", "font_color": "#1A6B3C"})
    date_fmt = wb.add_format({"num_format": "dd-mmm-yyyy"})

    # ── Sheet 1: Leg Summary ────────────────────────────────────────────────
    ws1 = wb.add_worksheet("Leg Summary")
    cyc = cycle_df.copy()
    headers1 = list(cyc.columns)
    for c, h in enumerate(headers1):
        ws1.write(0, c, h.replace("_", " ").title(), hdr_fmt)
    for r, row in enumerate(cyc.itertuples(index=False), start=1):
        for c, val in enumerate(row):
            col_name = headers1[c]
            if "date" in col_name and pd.notna(val):
                ws1.write_datetime(r, c, pd.Timestamp(val), date_fmt)
            elif "score" in col_name and pd.notna(val):
                ws1.write_number(r, c, float(val), num_fmt)
            else:
                ws1.write(r, c, "" if pd.isna(val) else val)
    ws1.set_column(0, len(headers1) - 1, 16)
    ws1.autofilter(0, 0, len(cyc), len(headers1) - 1)
    ws1.freeze_panes(1, 0)

    # ── Sheet 2: Leg Scorecards (every ticker, every leg) ───────────────────
    ws2 = wb.add_worksheet("Leg Scorecards")
    lr = leg_rank_df.copy()
    score_cols = {c for c in lr.columns if "score" in c or c.startswith("pillar_")}
    headers2 = list(lr.columns)
    for c, h in enumerate(headers2):
        ws2.write(0, c, h.replace("_", " ").title(), hdr_fmt)
    for r, row in enumerate(lr.itertuples(index=False), start=1):
        selected = bool(getattr(row, "selected", False))
        for c, val in enumerate(row):
            col_name = headers2[c]
            fmt = sel_fmt if selected and col_name == "ticker" else None
            if "date" in col_name and pd.notna(val):
                ws2.write_datetime(r, c, pd.Timestamp(val), date_fmt)
            elif col_name in score_cols and pd.notna(val):
                ws2.write_number(r, c, float(val), num_fmt)
            else:
                ws2.write(r, c, "" if pd.isna(val) else val, fmt)
    ws2.set_column(0, len(headers2) - 1, 15)
    ws2.autofilter(0, 0, len(lr), len(headers2) - 1)
    ws2.freeze_panes(1, 0)

    # ── Sheet 3: Gate Parameters ─────────────────────────────────────────────
    ws3 = wb.add_worksheet("Gate Parameters")
    ws3.write(0, 0, "Section", hdr_fmt)
    ws3.write(0, 1, "Parameter", hdr_fmt)
    ws3.write(0, 2, "Value", hdr_fmt)
    for r, row in enumerate(params_df.itertuples(index=False), start=1):
        ws3.write(r, 0, row.section)
        ws3.write(r, 1, row.parameter)
        ws3.write(r, 2, str(row.value))
    ws3.set_column(0, 0, 26)
    ws3.set_column(1, 1, 32)
    ws3.set_column(2, 2, 40)

    wb.close()
    return buf.getvalue()
