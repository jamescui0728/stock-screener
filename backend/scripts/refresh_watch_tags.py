"""
全市场重算 EMA20 跟随纪律标签 + MACD 零轴上方回踩金叉。

结果写回 stocks 表（watch_tag / macd_cross_up 等列），供公司筛选页做 SQL 过滤与排序。
纯 DB 计算，不打任何外部 API；实测 5500 只约 5 秒。

使用：
  .venv/bin/python scripts/refresh_watch_tags.py            # dry-run，只打印分布不写库
  .venv/bin/python scripts/refresh_watch_tags.py --apply    # 实际写库
  .venv/bin/python scripts/refresh_watch_tags.py --apply --limit 200   # 只跑前 200 只
"""
import argparse
import sys
import time

sys.path.insert(0, ".")

from database import SessionLocal  # noqa: E402
from engines.watch_tag import scan_market  # noqa: E402

TAG_CN = {
    "FOLLOW":      "可跟进",
    "HOLD":        "持有",
    "STOP_LOSS":   "止损",
    "NO_DATA":     "数据不足",
    "TAKE_PROFIT": "止盈",
}


def main(apply: bool, limit=None):
    db = SessionLocal()
    try:
        t0 = time.time()
        # dry-run 靠 commit=False：照常算、照常打印，只是不落库
        r = scan_market(db, commit=apply, limit=limit)
        if not apply:
            db.rollback()

        print("=" * 60)
        print(f"扫描 {r['scanned']} 只，其中 {r['with_prices']} 只有价格数据")
        print(f"耗时 {time.time() - t0:.1f}s")
        print("-" * 60)
        for tag, n in sorted(r["tag_counts"].items(), key=lambda kv: -kv[1]):
            print(f"  {TAG_CN.get(tag, tag):<10} {n:>6}")
        print(f"  {'MACD 金叉':<10} {r['macd_cross_up']:>6}   （零轴上方回踩）")
        print("=" * 60)

        if not apply:
            print("\n[dry-run] 未写库。加 --apply 实际执行。")
        else:
            print(f"\n✓ 已写回 stocks 表，watch_updated = {r['updated_at']}")
    finally:
        db.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="全市场重算纪律标签与 MACD 关注信号")
    ap.add_argument("--apply", action="store_true", help="实际写库；不加则只 dry-run")
    ap.add_argument("--limit", type=int, default=None, help="只跑前 N 只（调试用）")
    args = ap.parse_args()
    main(args.apply, args.limit)
