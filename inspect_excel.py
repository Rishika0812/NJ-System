"""Inspect the GATE.ARQM Excel file for zero/NaN values."""
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
import pandas as pd
import numpy as np

FILE = 'Gate-ARQM_Bot150_K80_FF252252d_N5pct_Fall10pct_Hold5yr.xlsx'

df = pd.read_excel(FILE, sheet_name='Cycle Candidates', header=2)

# Check if dps_growth_weighted appears anywhere
dps_cols = [c for c in df.columns if 'dps' in c.lower() or 'Dps' in c]
print('Columns containing dps:', dps_cols)
print()

# Growth factor Z-scores per cycle
for cyc in [1, 3, 5]:
    sub = df[df['Cycle'] == cyc]
    print(f'--- Cycle {cyc} Growth factor Z-scores ---')
    for col in ['Fall Entry | Gate Qf Eps Growth Weighted Z',
                'Fall Entry | Gate Qf Revenue Growth Weighted Z',
                'Fall Entry | Gate Qf Roe Growth Weighted Z',
                'Fall Entry | Gate Qf Roce Growth Weighted Z',
                'Fall Entry | Gate Qf Sustainable Growth Rate Z']:
        vals = sub[col].dropna()
        if len(vals) > 0:
            label = col.split('|')[1].strip()
            print(f'  {label}: mean={vals.mean():.4f}, std={vals.std():.4f}, NaN={sub[col].isna().sum()}, min={vals.min():.4f}, max={vals.max():.4f}')
        else:
            label = col.split('|')[1].strip()
            print(f'  {label}: ALL NaN')
    print()

# Check: is minmax collapsing? Compute weighted sum of z-scores for growth factors
print('=== GROWTH PILLAR: CHECKING MINMAX COLLAPSE ===')
growth_z_cols = ['Fall Entry | Gate Qf Eps Growth Weighted Z',
                 'Fall Entry | Gate Qf Revenue Growth Weighted Z',
                 'Fall Entry | Gate Qf Roe Growth Weighted Z',
                 'Fall Entry | Gate Qf Roce Growth Weighted Z',
                 'Fall Entry | Gate Qf Sustainable Growth Rate Z']
weights = [1.0, 1.0, 0.8, 0.8, 0.8]  # from GateParams (dps_growth_weighted=0.6 is missing from export)
w_norm = np.array(weights) / sum(weights)

for cyc in sorted(df['Cycle'].unique()):
    sub = df[df['Cycle'] == cyc]
    mat = sub[growth_z_cols]
    # Count valid rows (non-NaN in all columns)
    valid_mask = mat.notna().all(axis=1)
    valid_count = valid_mask.sum()
    
    if valid_count > 0:
        valid_mat = mat[valid_mask]
        weighted_sum = valid_mat.mul(w_norm, axis=1).sum(axis=1)
        lo, hi = weighted_sum.min(), weighted_sum.max()
        growth_pillar_vals = sub['Fall Entry | Gate Pillar Growth']
        gp_zero = (growth_pillar_vals == 0).sum()
        gp_nan = growth_pillar_vals.isna().sum()
        gp_nonzero = ((growth_pillar_vals != 0) & growth_pillar_vals.notna()).sum()
        print(f'  Cycle {cyc}: valid_rows={valid_count}/{len(sub)}, weighted_sum min={lo:.6f}, max={hi:.6f}, range={hi-lo:.6f}, growth_pillar: zero={gp_zero}, nan={gp_nan}, nonzero={gp_nonzero}')
        if lo == hi:
            print(f'    ** MINMAX COLLAPSE: min==max -> all output = NaN **')
    else:
        print(f'  Cycle {cyc}: NO valid rows (all have NaN in at least one growth z-score)')

print()

# Check the Fall Exit Growth pillar
print('=== FALL EXIT GROWTH PILLAR ===')
exit_growth_z_cols = [c.replace('Entry', 'Exit') for c in growth_z_cols]
for cyc in sorted(df['Cycle'].unique()):
    sub = df[df['Cycle'] == cyc]
    mat = sub[exit_growth_z_cols]
    valid_mask = mat.notna().all(axis=1)
    valid_count = valid_mask.sum()
    growth_exit = sub['Fall Exit | Gate Pillar Growth']
    gp_zero = (growth_exit == 0).sum()
    gp_nan = growth_exit.isna().sum()
    gp_nonzero = ((growth_exit != 0) & growth_exit.notna()).sum()
    print(f'  Cycle {cyc}: valid_rows={valid_count}/{len(sub)}, growth_exit_pillar: zero={gp_zero}, nan={gp_nan}, nonzero={gp_nonzero}')

print()

# Check the Trade Log for Fall Confirm Date NaN
print('=== TRADE LOG: FALL CONFIRM DATE NaN ===')
tl = pd.read_excel(FILE, sheet_name='Trade Log', header=2)
nan_fall = tl[tl['Fall Confirm Date'].isna()]
print(f'Trades with NaN Fall Confirm Date: {len(nan_fall)} / {len(tl)}')
print('Cycles affected:', sorted(nan_fall['Cycle'].unique()))
print('Status of NaN trades:', nan_fall['Status'].value_counts().to_dict())
print()

# Check Cycle Ledger for zeros
print('=== CYCLE LEDGER ZEROS ===')
cl = pd.read_excel(FILE, sheet_name='Cycle Ledger (all)', header=2)
print('PeakGainPct zeros (cycles):', cl[cl['PeakGainPct'] == 0]['Cycle'].tolist())
print('FallDate NaN (cycles):', cl[cl['FallDate'].isna()]['Cycle'].tolist())
print('Q1 zeros (cycles):', cl[cl['Q1'] == 0]['Cycle'].tolist())
