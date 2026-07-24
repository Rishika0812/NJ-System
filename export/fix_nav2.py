with open('momentum_exporter.py', 'rb') as f:
    content = f.read()

old = (b'Portfolio NAV \xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\r\n'
    b'    ws13 = wb.create_sheet("Portfolio NAV")\r\n'
    b'    if ia is not None and not ia["window_table"].empty:\r\n'
    b'        wt = ia["window_table"].copy()\r\n'
    b'        if "exit_date" in wt.columns:\r\n'
    b'            wt = wt.sort_values("exit_date").reset_index(drop=True)\r\n'
    b'        wt["drawdown_pct"] = round((wt["equity_inr"] - wt["equity_inr"].cummax()) / wt["equity_inr"].cummax() * 100, 2)\r\n'
    b'        wt["exit_date"] = wt["exit_date"].map(_fmt_date)\r\n'
    b'        nav_keep = [c for c in ["exit_date", "equity_inr", "window_return_pct",\r\n'
    b'                    "per_window_capital", "n_stocks", "cumulative_profit_inr", "drawdown_pct"]\r\n'
    b'                    if c in wt.columns]\r\n'
    b'        wt = wt[nav_keep].rename(columns={\r\n'
    b'            "exit_date":             "Rebalance Date",\r\n'
    b'            "equity_inr":            "Portfolio NAV",\r\n'
    b'            "window_return_pct":     "Portfolio Return %",\r\n'
    b'            "per_window_capital":    "Invested Capital",\r\n'
    b'            "n_stocks":              "Holdings Count",\r\n'
    b'            "cumulative_profit_inr": "Cumulative Profit",\r\n'
    b'            "drawdown_pct":          "Drawdown %",\r\n'
    b'        })\r\n'
    b'        _title(ws13, "Portfolio NAV \xe2\x80\x94 Rebalance-by-Rebalance Equity Curve", len(wt.columns))\r\n'
    b'        _write_df(ws13, wt, 3)\r\n'
    b'    else:\r\n'
    b'        _title(ws13, "Portfolio NAV", 2)\r\n'
    b'        ws13.cell(row=3, column=1, value="(needs \xe2\x89\xa5 2 completed cycles)")')

new = (b'Portfolio NAV \xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\r\n'
    b'    ws13 = wb.create_sheet("Portfolio NAV")\r\n'
    b'    if ia is not None and not ia["window_table"].empty:\r\n'
    b'        wt = ia["window_table"].copy()\r\n'
    b'        if "exit_date" in wt.columns:\r\n'
    b'            wt = wt.sort_values("exit_date").reset_index(drop=True)\r\n'
    b'        wt["exit_date"] = wt["exit_date"].map(_fmt_date)\r\n'
    b'        nav_df = pd.DataFrame({\r\n'
    b'            "Rebalance Date": wt["exit_date"],\r\n'
    b'            "NAV": (wt["equity_inr"] / initial_capital).round(6)\r\n'
    b'        })\r\n'
    b'        _title(ws13, "Portfolio NAV \xe2\x80\x94 Rebalance Date & NAV (Equity / Initial Capital)", 2)\r\n'
    b'        _write_df(ws13, nav_df, 3)\r\n'
    b'    else:\r\n'
    b'        _title(ws13, "Portfolio NAV", 2)\r\n'
    b'        ws13.cell(row=3, column=1, value="(needs \xe2\x89\xa5 2 completed cycles)")')

if old in content:
    content = content.replace(old, new)
    with open('momentum_exporter.py', 'wb') as f:
        f.write(content)
    print('Replacement successful!')
else:
    print('OLD not found!')
    # Debug: find differences
    print('First 200 bytes of old:')
    print(repr(old[:200]))
    print('First 200 bytes of content at idx:')
    idx = content.find(b'Portfolio NAV \xe2\x95\x90')
    print(repr(content[idx:idx+200]))