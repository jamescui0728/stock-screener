"""
精度分析：把 BUY 信号按 composite 分位细分，看每个分位的真实胜率。
目标：找到 win_rate >= 60% 的最高分位区间，反推 STRONG_BUY 阈值。

使用：python scripts/precision_analysis.py <run_id>
"""
import sys
sys.path.insert(0, ".")

from database import SessionLocal
from models.models import BacktestRecord
import numpy as np


def main(run_id):
    db = SessionLocal()
    try:
        recs = db.query(BacktestRecord).filter(
            BacktestRecord.run_id == run_id,
            BacktestRecord.signal.in_(("BUY", "STRONG_BUY"))
        ).all()
        print(f"=== Run {run_id}: BUY 类信号 {len(recs)} 条 ===")
        if not recs:
            return

        # 按 composite 排序
        recs.sort(key=lambda r: r.composite_score, reverse=True)
        n = len(recs)

        # 不同 top-N 分位的胜率 + 平均超额
        print(f"\n{'分位':>10} {'阈值':>8} {'n':>6} {'胜率':>7} {'avg超额':>9} {'p25':>7} {'p50':>7} {'p75':>7}")
        for pct in [1, 2, 5, 10, 15, 20, 30, 50, 100]:
            top_n = max(int(n * pct / 100), 10)
            subset = recs[:top_n]
            scores = [r.composite_score for r in subset]
            rets   = [r.excess_return for r in subset if r.excess_return is not None]
            wins   = sum(1 for r in subset if r.is_win)
            if not rets:
                continue
            thresh = scores[-1]
            win_rate = wins / len(subset) * 100
            avg = float(np.mean(rets))
            p25, p50, p75 = np.percentile(rets, [25, 50, 75])
            print(f"{'top '+str(pct)+'%':>10} {thresh:>8.2f} {top_n:>6} {win_rate:>6.1f}% {avg:>+8.2f}% {p25:>+6.1f}% {p50:>+6.1f}% {p75:>+6.1f}%")

        # 按 composite 阈值的胜率（每 5 分一个桶）
        print(f"\n=== 按 composite 阈值切（≥X 的所有 BUY）===")
        print(f"{'阈值':>6} {'n':>6} {'胜率':>7} {'avg超额':>9}")
        max_s = max(r.composite_score for r in recs)
        min_s = min(r.composite_score for r in recs)
        for thresh in range(int(min_s), int(max_s)+1, 2):
            subset = [r for r in recs if r.composite_score >= thresh]
            if len(subset) < 50: break
            wins = sum(1 for r in subset if r.is_win)
            rets = [r.excess_return for r in subset if r.excess_return is not None]
            avg = float(np.mean(rets)) if rets else 0
            win_rate = wins / len(subset) * 100
            print(f"{thresh:>6} {len(subset):>6} {win_rate:>6.1f}% {avg:>+8.2f}%")
    finally:
        db.close()


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 56)
