with open('momentum_exporter.py', 'rb') as f:
    content = f.read()

# Find the section
idx = content.find(b'Portfolio NAV \xe2\x95\x90')
# Find end of section (next major section marker)
idx2 = content.find(b'\r\n\r\n    # ', idx)
if idx2 == -1:
    idx2 = content.find(b'\r\n\r\n# ', idx)

old_section = content[idx:idx2]
print(f'Old section length: {len(old_section)}')

# Build new section with EXACT same box drawing chars
# Count box chars in old
box_end = old_section.find(b'\r\n')
header_line = old_section[:box_end]  # "Portfolio NAV ████..."
print(f'Header: {header_line[:50]}...')

new_section = header_line + b'\r\n' + b'''
    ws13 = wb.create_sheet("Portfolio NAV")
    if ia is not None and not ia["window_table"].empty:
        wt = ia["window_table"].copy()
        if "exit_date" in wt.columns:
            wt = wt.sort_values("exit_date").reset_index(drop=True)
        wt["exit_date"] = wt["exit_date"].map(_fmt_date)
        nav_df = pd.DataFrame({
            "Rebalance Date": wt["exit_date"],
            "NAV": (wt["equity_inr"] / initial_capital).round(6)
        })
        _title(ws13, "Portfolio NAV \xe2\x80\x94 Rebalance Date & NAV (Equity / Initial Capital)", 2)
        _write_df(ws13, nav_df, 3)
    else:
        _title(ws13, "Portfolio NAV", 2)
        ws13.cell(row=3, column=1, value="(needs \xe2\x89\xa5 2 completed cycles)")'''

print(f'New section length: {len(new_section)}')
print(f'First 80 bytes of new: {new_section[:80]}')

if old_section in content:
    content = content.replace(old_section, new_section)
    with open('momentum_exporter.py', 'wb') as f:
        f.write(content)
    print('SUCCESS: Replacement done!')
else:
    print('FAIL: old_section not found in content')
    # Debug: show first diff
    for i, (a, b) in enumerate(zip(old_section, content[idx:idx+len(old_section)])):
        if a != b:
            print(f'First diff at {i}: old={a:02x} new={b:02x}')
            print(f'Context old: {old_section[max(0,i-10):i+10]}')
            print(f'Context new: {content[idx+max(0,i-10):idx+i+10]}')
            break