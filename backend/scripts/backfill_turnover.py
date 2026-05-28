"""
一次性回填近 N 天的换手率到 PriceData.turnover_rate。

用法（容器内）：
    docker compose exec backend python scripts/backfill_turnover.py [days]
days 默认 365。

对所有 active 股票逐只跑 ak.stock_zh_a_hist，仅 UPDATE 已有 PriceData 行，
不插入新的 OHLCV。已有 turnover_rate 的行不会被覆盖。
"""
import sys, time
sys.path.insert(0, ".")

from database import SessionLocal
from data.fetcher import fetch_turnover_history
from models.models import Stock


def main(days: int = 365):
    db = SessionLocal()
    try:
        codes = [r[0] for r in db.query(Stock.code).filter(Stock.is_active == True).all()]
        print(f"换手率回填：{len(codes)} 只 active 股票 × 近 {days} 天")
        total_updated = 0
        t0 = time.time()
        for i, code in enumerate(codes, 1):
            try:
                n = fetch_turnover_history(db, code, days=days)
                total_updated += n
            except Exception as e:
                print(f"  {code} 失败: {e}")
            # 节流避免打爆东财；每 100 只打一次进度
            time.sleep(0.3)
            if i % 100 == 0 or i == len(codes):
                elapsed = (time.time() - t0) / 60
                eta = elapsed / i * (len(codes) - i)
                print(f"  [{i}/{len(codes)}] 已更新 {total_updated} 行，用时 {elapsed:.1f}min，ETA {eta:.1f}min")
        print(f"完成：累计 UPDATE {total_updated} 行，总耗时 {(time.time()-t0)/60:.1f} 分钟")
    finally:
        db.close()


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 365
    main(days=n)
