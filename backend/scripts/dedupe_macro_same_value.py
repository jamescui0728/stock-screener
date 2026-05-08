"""
清理 macro_data 表里「同月同值」的纯重复记录。

背景：
  历史上 PMI/CPI/M2 经过两个数据源写入：
  - sina/jin10（akshare 的 macro_china_*_yearly）—— 2025-08 后停更，曾用公布日为 date
  - 东方财富 RPT_ECONOMY_*（新主源）—— 用月初日为 date

  对 PMI 而言两边值大多一致（但 date 不同），形成"冗余"；
  对 CPI/M2 而言两边可能口径不同（公布日 vs 月初日），值不一致——
  这种"同月不同值"的情况**不删**，因为存在统计口径差异，是历史信息价值。

策略：
  对每个 (indicator, year_month, value) 组合，如果出现 2 条以上，只留 date 最新的那条。
  即"完全同值的纯重复"才删；任何值不同的多条记录保留。

使用：
  .venv/bin/python scripts/dedupe_macro_same_value.py            # dry-run
  .venv/bin/python scripts/dedupe_macro_same_value.py --apply    # 实际写库
"""
import sys

sys.path.insert(0, ".")

from database import SessionLocal
from sqlalchemy import text


def main(apply: bool):
    db = SessionLocal()
    try:
        # 查所有 (indicator, year_month, value) 出现 ≥ 2 次的组
        # 用 ROWID 来定位具体记录（SQLite 内置）
        rows = db.execute(text("""
            SELECT
                indicator,
                strftime('%Y-%m', date) AS ym,
                value,
                COUNT(*) AS cnt,
                MAX(date) AS keep_date,
                GROUP_CONCAT(date, ',') AS all_dates
            FROM macro_data
            GROUP BY indicator, ym, value
            HAVING cnt > 1
            ORDER BY indicator, ym DESC
        """)).fetchall()

        if not rows:
            print("✓ 没有「同月同值」重复，无需清理。")
            return

        print(f"=== 发现 {len(rows)} 组「同月同值」重复 ===\n")
        total_to_delete = 0
        delete_targets = []   # [(indicator, date_str), ...]

        for r in rows:
            indicator, ym, value, cnt, keep_date, all_dates = r
            dates = all_dates.split(",")
            to_delete = [d for d in dates if d != keep_date]
            total_to_delete += len(to_delete)
            for d in to_delete:
                delete_targets.append((indicator, d))
            print(f"  [{indicator}] {ym} value={value}  共 {cnt} 条")
            print(f"     保留: {keep_date}")
            print(f"     删除: {', '.join(to_delete)}")

        print(f"\n=== 待删: {total_to_delete} 条 ===")

        if not apply:
            print("\n[dry-run] 未写库。加 --apply 实际执行。")
            return

        n = 0
        for indicator, date_str in delete_targets:
            n += db.execute(text("""
                DELETE FROM macro_data
                WHERE indicator = :ind AND date = :d
            """), {"ind": indicator, "d": date_str}).rowcount
        db.commit()
        print(f"\n✓ 已删 {n} 条")

        # 同时输出清理后的总览
        print("\n=== 清理后总览 ===")
        for ind in ["PMI", "CPI", "M2", "NORTH_FLOW"]:
            total = db.execute(text(f"SELECT COUNT(*) FROM macro_data WHERE indicator='{ind}'")).scalar()
            months = db.execute(text(f"SELECT COUNT(DISTINCT strftime('%Y-%m', date)) FROM macro_data WHERE indicator='{ind}'")).scalar()
            print(f"  {ind:<12} 总条数={total:>4}  唯一月份={months:>4}  剩余冗余={total - months:>4}")

    finally:
        db.close()


if __name__ == "__main__":
    apply = "--apply" in sys.argv
    main(apply)
