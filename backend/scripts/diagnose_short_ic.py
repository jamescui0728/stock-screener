"""
诊断脚本：分析短期回测记录里每个子维度的 IC，找出哪些维度方向反了。

逻辑：
- 对每只信号样本，取出它当时 5 个子分（momentum/volprice/macro/tech/news_heat）
- 算每个子分 vs excess_return 的 Spearman IC
- 正 IC 意味着该维度有预测力；负 IC 意味着该维度方向反了（A 股短期反转）
- 同时按子分分桶看分位收益

使用：python scripts/diagnose_short_ic.py <run_id>
"""
import sys
sys.path.insert(0, ".")

import json
from database import SessionLocal
from models.models import BacktestRecord, BacktestRun
from scipy.stats import spearmanr
import numpy as np


def diagnose(run_id: int):
    db = SessionLocal()
    try:
        run = db.query(BacktestRun).filter_by(id=run_id).first()
        if run and run.signal_type != "short":
            print(
                f"\n[WARN] Run {run_id} 的 signal_type={run.signal_type!r}，"
                "不是短期回测；下面结果不能用于评估短期信号。"
            )

        recs = db.query(BacktestRecord).filter_by(run_id=run_id).all()
        print(f"\n=== Run {run_id}: {len(recs)} 条记录 ===")
        if not recs:
            return

        # 全部信号样本
        by_signal = {}
        for r in recs:
            by_signal.setdefault(r.signal, []).append(r)
        print("信号分布：", {k: len(v) for k, v in by_signal.items()})

        # 整体 IC：composite_score vs excess_return
        all_x = [r.composite_score for r in recs if r.composite_score is not None and r.excess_return is not None]
        all_y = [r.excess_return  for r in recs if r.composite_score is not None and r.excess_return is not None]
        ic, _ = spearmanr(all_x, all_y)
        print(f"\nComposite IC (全样本，n={len(all_x)}): {ic:.4f}")

        # 各子分 IC
        dims = [
            "momentum", "volprice", "macro", "tech", "news_heat",
            "industry_relative", "pricing_power", "market_trend",
        ]
        print(f"\n各维度 IC（全样本）：")
        for dim in dims:
            xs, ys = [], []
            for r in recs:
                if not r.sub_scores: continue
                v = r.sub_scores.get(dim)
                if v is None or r.excess_return is None: continue
                xs.append(v); ys.append(r.excess_return)
            if len(xs) < 10:
                print(f"  {dim:12s}: 样本不足 ({len(xs)})")
                continue
            ic_d, _ = spearmanr(xs, ys)
            print(f"  {dim:12s}: IC={ic_d:+.4f}  n={len(xs)}  "
                  f"mean={np.mean(xs):.1f}  std={np.std(xs):.1f}  "
                  f"y_mean={np.mean(ys):+.2f}%")

        # 分桶分析（按 composite_score 分 5 桶）
        print(f"\nComposite Score 分桶 → 平均超额收益：")
        sorted_recs = sorted([r for r in recs if r.composite_score is not None and r.excess_return is not None],
                             key=lambda r: r.composite_score)
        n = len(sorted_recs)
        for i in range(5):
            lo, hi = i*n//5, (i+1)*n//5
            bucket = sorted_recs[lo:hi]
            if not bucket: continue
            scores = [r.composite_score for r in bucket]
            rets   = [r.excess_return  for r in bucket]
            wins   = sum(1 for r in bucket if r.is_win)
            print(f"  桶 {i+1} ({scores[0]:.0f}-{scores[-1]:.0f}): "
                  f"n={len(bucket)}  avg_excess={np.mean(rets):+.2f}%  "
                  f"win_rate={wins/len(bucket)*100:.1f}%")

        # 单 BUY 类信号分析（只看实际下注的）
        buys = [r for r in recs if r.signal in ("BUY", "STRONG_BUY")
                and r.composite_score is not None and r.excess_return is not None]
        if buys:
            print(f"\n【仅 BUY/STRONG_BUY】各维度 IC（n={len(buys)}）：")
            for dim in dims:
                xs, ys = [], []
                for r in buys:
                    if not r.sub_scores: continue
                    v = r.sub_scores.get(dim)
                    if v is None: continue
                    xs.append(v); ys.append(r.excess_return)
                if len(xs) < 10:
                    print(f"  {dim:12s}: 样本不足 ({len(xs)})")
                    continue
                ic_d, _ = spearmanr(xs, ys)
                print(f"  {dim:12s}: IC={ic_d:+.4f}")

            # 持有期超额收益分布
            rets = [r.excess_return for r in buys]
            print(f"\n  BUY 超额收益：mean={np.mean(rets):+.2f}%  median={np.median(rets):+.2f}%  "
                  f"std={np.std(rets):.2f}  win={sum(1 for r in rets if r>0)/len(rets)*100:.1f}%")
            print(f"  分位：p10={np.percentile(rets,10):+.1f}%  p25={np.percentile(rets,25):+.1f}%  "
                  f"p50={np.percentile(rets,50):+.1f}%  p75={np.percentile(rets,75):+.1f}%  "
                  f"p90={np.percentile(rets,90):+.1f}%")
    finally:
        db.close()


if __name__ == "__main__":
    run_id = int(sys.argv[1]) if len(sys.argv) > 1 else 45
    diagnose(run_id)
