"""
Forward test 回查：news_heat 在科技板块到底是反转信号还是追涨信号。

15 天后（约 2026-06-02）跑这个脚本，对比当时的 BUY 候选实际表现，
按 news_heat 分组（A_neg / mid / B_pos）算每组的胜率 + 平均超额。

使用：
  python scripts/forward_test_check.py [/data/forward_test_news_heat_2026-05-18.json]
"""
import sys, json
sys.path.insert(0, ".")

from datetime import date
from database import SessionLocal
from models.models import PriceData
from sqlalchemy import desc
import numpy as np


def _latest_price(db, code, as_of=None):
    q = db.query(PriceData).filter(PriceData.stock_code == code)
    if as_of:
        q = q.filter(PriceData.trade_date <= as_of)
    r = q.order_by(desc(PriceData.trade_date)).first()
    return (r.close, r.trade_date) if r else (None, None)


def main(snapshot_path: str):
    with open(snapshot_path) as f:
        snap = json.load(f)

    db = SessionLocal()
    today = date.today()
    print(f"=== Forward Test Check ===")
    print(f"基线日期: {snap['snapshot_date']}")
    print(f"今日:    {today}")
    print(f"hold_days: {snap['hold_days']} 天")

    # 当前基准价
    bench_now, bench_now_date = _latest_price(db, snap['bench_code'])
    bench_entry = snap['bench_price']
    bench_ret   = (bench_now - bench_entry) / bench_entry * 100 if bench_entry else 0.0
    print(f"基准（沪深300）{bench_entry:.2f} → {bench_now:.2f}（{bench_now_date}），涨跌 {bench_ret:+.2f}%\n")

    # 逐只算
    rows = []
    for p in snap['positions']:
        cur, cur_date = _latest_price(db, p['code'])
        if cur is None or p['entry_price'] is None:
            continue
        stock_ret  = (cur - p['entry_price']) / p['entry_price'] * 100
        excess_ret = stock_ret - bench_ret
        rows.append({
            **p,
            'current_price': cur,
            'stock_return':  stock_ret,
            'excess_return': excess_ret,
            'is_win':        excess_ret > 0,
        })

    # 总体
    n = len(rows)
    overall_win = sum(1 for r in rows if r['is_win']) / n * 100 if n else 0
    overall_avg = float(np.mean([r['excess_return'] for r in rows])) if rows else 0
    print(f"全部 BUY 候选 n={n}: 胜率={overall_win:.1f}%  平均超额={overall_avg:+.2f}%\n")

    # 按 group 分
    from collections import defaultdict
    groups = defaultdict(list)
    for r in rows:
        groups[r['group']].append(r)

    print(f"{'group':<10} {'n':>4} {'胜率':>7} {'avg超额':>9} {'median':>8} {'best':>7} {'worst':>7}")
    print("-" * 60)
    for g in ('A_neg', 'mid', 'B_pos'):
        sub = groups.get(g, [])
        if not sub:
            continue
        excess = [r['excess_return'] for r in sub]
        wins = sum(1 for r in sub if r['is_win'])
        print(f"{g:<10} {len(sub):>4} {wins/len(sub)*100:>6.1f}% "
              f"{np.mean(excess):>+8.2f}% {np.median(excess):>+7.2f}% "
              f"{max(excess):>+6.1f}% {min(excess):>+6.1f}%")

    # 简要结论
    a = groups.get('A_neg', [])
    b = groups.get('B_pos', [])
    if a and b:
        a_avg = np.mean([r['excess_return'] for r in a])
        b_avg = np.mean([r['excess_return'] for r in b])
        print()
        if a_avg > b_avg + 1.0:
            print(f"→ 结论：A_neg (消极新闻) 跑赢 B_pos (积极新闻) {a_avg-b_avg:+.2f}pp，"
                  f"news_heat 应**反向**加权（低分=买入加分）")
        elif b_avg > a_avg + 1.0:
            print(f"→ 结论：B_pos (积极新闻) 跑赢 A_neg (消极新闻) {b_avg-a_avg:+.2f}pp，"
                  f"news_heat 应**正向**加权（高分=买入加分）— 科技股追涨模型")
        else:
            print(f"→ 结论：A/B 差距 {abs(a_avg-b_avg):.2f}pp，无显著区分，news_heat 权重保持 0")

    # 详细列表
    print("\n--- 详细持仓表现 ---")
    print(f"{'code':>8} {'name':<10} {'group':<8} {'news':>5} {'entry':>8} {'cur':>8} {'stock':>7} {'excess':>7}")
    for r in sorted(rows, key=lambda x: x['excess_return'], reverse=True):
        print(f"{r['code']:>8} {r['name'][:8]:<10} {r['group']:<8} "
              f"{r['news_heat']:>5.1f} {r['entry_price']:>8.2f} {r['current_price']:>8.2f} "
              f"{r['stock_return']:>+6.2f}% {r['excess_return']:>+6.2f}%")
    db.close()


if __name__ == "__main__":
    path = sys.argv[1] if len(sys.argv) > 1 else "/data/forward_test_news_heat_2026-05-18.json"
    main(path)
