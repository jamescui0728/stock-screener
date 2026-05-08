import sys, warnings
warnings.filterwarnings('ignore')
sys.path.insert(0, '.')

import akshare as ak

tests = [
    ("PMI",   lambda: ak.macro_china_pmi_yearly()),
    ("CPI",   lambda: ak.macro_china_cpi_yearly()),
    ("M2",    lambda: ak.macro_china_m2_yearly()),
    ("NORTH", lambda: ak.stock_hsgt_hist_em(symbol="北向资金")),
]

for name, fn in tests:
    try:
        df = fn()
        if df is not None and not df.empty:
            print(f"[OK]   {name}: cols={df.columns.tolist()[:5]}")
        else:
            print(f"[EMPTY] {name}")
    except Exception as e:
        print(f"[ERR]  {name}: {e}")
