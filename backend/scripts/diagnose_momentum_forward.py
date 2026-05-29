"""
无偏动量检验：脱离现有短期模型，在全市场按动量分层，直接测前向（超额）收益。

回答一个问题：买"近期强势 / 热点板块"的股票，未来 5/10/20 个交易日是赚还是亏？
  - 个股动量：按每只股票的 20 日涨幅分 5 档
  - 板块动量：复用 compute_industry_returns_at 按行业 20 日涨幅分 5 档（"热点板块"）
  Q5 = 最强/最热，Q1 = 最弱/最冷。

判据：
  - 若 Q5 前向超额 > Q1 且 IC(动量, 前向) 显著为正 → 动量/追热点有效
  - 若 Q5 <= Q1 / IC 为负 → A 股短期反转占优，"追热点"是负收益（支持现有逆向设计）

只读 PriceData，不写库、不联网（绕开被墙的东财接口）。
复用 short_signal_engine.compute_industry_returns_at（约束 3，不重造行业动量）。

用法（容器内）：
    docker compose exec backend python scripts/diagnose_momentum_forward.py [years] [step_days]
    years     回看年数（默认 2）
    step_days 调仓点间隔的交易日数（默认 10）
"""
import sys
sys.path.insert(0, ".")

from collections import defaultdict
from datetime import timedelta

import numpy as np
from scipy.stats import spearmanr

from database import SessionLocal
from models.models import PriceData, Stock
from engines.short_signal_engine import compute_industry_returns_at, BENCHMARK_CODE

TRAIL = 20            # 动量回看窗口（交易日）
HORIZONS = [5, 10, 20]  # 前向持有期（交易日）
N_BUCKETS = 5


def _load_panel(db, start_date):
    """{code: {date: close}} + {code: sorted([date])}，排除基准指数。"""
    rows = (
        db.query(PriceData.stock_code, PriceData.trade_date, PriceData.close)
        .filter(PriceData.trade_date >= start_date,
                PriceData.stock_code != BENCHMARK_CODE,
                PriceData.close.isnot(None))
        .all()
    )
    close_by = defaultdict(dict)
    for code, dt, close in rows:
        if close and close > 0:
            close_by[code][dt] = close
    dates_by = {c: sorted(d.keys()) for c, d in close_by.items()}
    return close_by, dates_by


def _trading_calendar(db, start_date):
    """用基准指数的交易日作为全市场交易日历（升序）。"""
    rows = (
        db.query(PriceData.trade_date)
        .filter(PriceData.stock_code == BENCHMARK_CODE,
                PriceData.trade_date >= start_date)
        .order_by(PriceData.trade_date)
        .all()
    )
    return [r[0] for r in rows]


def _quintile_means(pairs, n_buckets=N_BUCKETS):
    """pairs=[(signal, excess)]，按 signal 升序分桶，返回每桶 excess 均值 + 边界。"""
    if len(pairs) < n_buckets * 5:
        return None
    pairs = sorted(pairs, key=lambda x: x[0])
    n = len(pairs)
    out = []
    for i in range(n_buckets):
        lo, hi = i * n // n_buckets, (i + 1) * n // n_buckets
        bucket = pairs[lo:hi]
        sig = [p[0] for p in bucket]
        exc = [p[1] for p in bucket]
        out.append((sig[0], sig[-1], np.mean(exc), len(bucket)))
    return out


def main(years=2, step_days=10):
    db = SessionLocal()
    try:
        max_date = db.query(PriceData.trade_date).order_by(PriceData.trade_date.desc()).first()[0]
        start_date = max_date - timedelta(days=int(365 * years) + 60)  # +60 缓冲给 trailing
        print(f"=== 无偏动量检验 ===  回看 {years} 年，调仓步长 {step_days} 交易日")
        print(f"价格窗口: {start_date} ~ {max_date}")

        close_by, dates_by = _load_panel(db, start_date)
        calendar = _trading_calendar(db, start_date)
        print(f"载入 {len(close_by)} 只股票，交易日历 {len(calendar)} 天")

        stock_industry = dict(
            db.query(Stock.code, Stock.industry_code)
            .filter(Stock.is_active == True, Stock.industry_code.isnot(None)).all()
        )

        max_h = max(HORIZONS)
        # 调仓点：日历里留足 trailing(TRAIL) 和 forward(max_h) 空间
        rebal_idx = list(range(TRAIL, len(calendar) - max_h, step_days))
        rebal_dates = [calendar[i] for i in rebal_idx]
        print(f"调仓点 {len(rebal_dates)} 个: {rebal_dates[0]} ~ {rebal_dates[-1]}\n")

        # 累积器：每个 horizon 一套
        stock_pairs = {h: [] for h in HORIZONS}    # (momentum_20d, excess_fwd)
        sector_pairs = {h: [] for h in HORIZONS}    # (sector_mom_quintile_rank, excess_fwd)
        ic_per_date = {h: [] for h in HORIZONS}

        for ci in rebal_idx:
            T = calendar[ci]
            T_trail = calendar[ci - TRAIL]

            # 板块动量分桶（复用现有函数）
            sec_ret = compute_industry_returns_at(db, T)
            sec_sorted = sorted(sec_ret.items(), key=lambda kv: kv[1]["ret_20d"])
            sec_rank = {}  # industry_code -> 0..N_BUCKETS-1
            m = len(sec_sorted)
            for rank, (icode, _) in enumerate(sec_sorted):
                sec_rank[icode] = min(N_BUCKETS - 1, rank * N_BUCKETS // m) if m else 0

            for h in HORIZONS:
                T_fwd = calendar[ci + h]
                moms, fwds, secs = [], [], []
                for code, cmap in close_by.items():
                    c_now = cmap.get(T)
                    c_tr = cmap.get(T_trail)
                    c_fw = cmap.get(T_fwd)
                    if c_now is None or c_tr is None or c_fw is None:
                        continue
                    mom = c_now / c_tr - 1
                    fwd = c_fw / c_now - 1
                    moms.append(mom)
                    fwds.append(fwd)
                    icode = stock_industry.get(code)
                    secs.append(sec_rank.get(icode) if icode in sec_rank else None)
                if len(moms) < 50:
                    continue
                mkt = float(np.mean(fwds))   # 横截面等权市场均值
                for mom, fwd, sq in zip(moms, fwds, secs):
                    stock_pairs[h].append((mom, fwd - mkt))
                    if sq is not None:
                        sector_pairs[h].append((sq, fwd - mkt))
                ic, _ = spearmanr(moms, fwds)
                if not np.isnan(ic):
                    ic_per_date[h].append(ic)

        # ── 报告 ──
        for h in HORIZONS:
            print(f"────────── 前向 {h} 交易日 ──────────")
            ics = ic_per_date[h]
            if ics:
                mean_ic = np.mean(ics)
                t_stat = mean_ic / (np.std(ics) / np.sqrt(len(ics))) if np.std(ics) > 0 else 0
                print(f"个股动量 IC: mean={mean_ic:+.4f}  std={np.std(ics):.4f}  "
                      f"n_dates={len(ics)}  t≈{t_stat:+.2f}  "
                      f"(>0 动量赢 / <0 反转赢)")
            q = _quintile_means(stock_pairs[h])
            if q:
                print("个股动量分档 → 平均前向超额(%):")
                for i, (lo, hi, mexc, n) in enumerate(q):
                    tag = " ← 最强" if i == N_BUCKETS - 1 else (" ← 最弱" if i == 0 else "")
                    print(f"  Q{i+1} 动量[{lo*100:+.1f}%~{hi*100:+.1f}%]: "
                          f"超额={mexc*100:+.2f}%  n={n}{tag}")
                print(f"  >>> Q5-Q1 价差 = {(q[-1][2]-q[0][2])*100:+.2f}%  "
                      f"(正=追涨赢, 负=抄底赢)")
            # 板块动量分档
            sp = sector_pairs[h]
            if sp:
                by_q = defaultdict(list)
                for sq, exc in sp:
                    by_q[sq].append(exc)
                print("板块动量(热点)分档 → 板块内个股平均前向超额(%):")
                for sq in range(N_BUCKETS):
                    if sq in by_q:
                        tag = " ← 最热板块" if sq == N_BUCKETS - 1 else (" ← 最冷板块" if sq == 0 else "")
                        print(f"  S{sq+1}: 超额={np.mean(by_q[sq])*100:+.2f}%  n={len(by_q[sq])}{tag}")
                if 0 in by_q and (N_BUCKETS - 1) in by_q:
                    spread = np.mean(by_q[N_BUCKETS-1]) - np.mean(by_q[0])
                    print(f"  >>> 最热-最冷 价差 = {spread*100:+.2f}%  (正=热点板块赢)")
            print()
    finally:
        db.close()


if __name__ == "__main__":
    yrs = float(sys.argv[1]) if len(sys.argv) > 1 else 2
    step = int(sys.argv[2]) if len(sys.argv) > 2 else 10
    main(years=yrs, step_days=step)
