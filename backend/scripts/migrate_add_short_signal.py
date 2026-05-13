"""
v200 migration：给 stocks 表加短期信号相关 9 列。

SQLite 不像 PostgreSQL 那样在程序启动时自动 ALTER，得手动加。
这个脚本幂等：已有的列不会重复加，新列才 ALTER。

使用：
  .venv/bin/python scripts/migrate_add_short_signal.py            # dry-run
  .venv/bin/python scripts/migrate_add_short_signal.py --apply    # 实际执行
"""
import sys
sys.path.insert(0, ".")

from database import SessionLocal
from sqlalchemy import text


# 要加的列（name, type, default）
NEW_COLUMNS = [
    ("short_composite_score", "FLOAT",       None),
    ("short_signal",          "VARCHAR(15)", None),
    ("short_signal_reason",   "TEXT",        None),
    ("short_signal_updated",  "DATETIME",    None),
    ("short_score_momentum",  "FLOAT",       None),
    ("short_score_volprice",  "FLOAT",       None),
    ("short_score_macro",     "FLOAT",       None),
    ("short_score_tech",      "FLOAT",       None),
    ("short_score_news_heat", "FLOAT",       None),
]


def main(apply: bool):
    db = SessionLocal()
    try:
        # 看现在 stocks 表已有的列
        existing = {r[1] for r in db.execute(text("PRAGMA table_info(stocks)")).fetchall()}
        print(f"当前 stocks 表已有 {len(existing)} 列")

        to_add = [(n, t, d) for n, t, d in NEW_COLUMNS if n not in existing]
        already = [n for n, _, _ in NEW_COLUMNS if n in existing]

        if already:
            print(f"\n已存在（跳过）: {', '.join(already)}")
        if not to_add:
            print("\n✓ 所有目标列都已存在，无需迁移。")
            return

        print(f"\n=== 待添加 {len(to_add)} 列 ===")
        for name, typ, default in to_add:
            default_clause = f" DEFAULT {default}" if default is not None else ""
            print(f"  ALTER TABLE stocks ADD COLUMN {name} {typ}{default_clause};")

        if not apply:
            print("\n[dry-run] 加 --apply 实际执行。")
            return

        print("\n=== 执行 ===")
        for name, typ, default in to_add:
            default_clause = f" DEFAULT {default}" if default is not None else ""
            sql = f"ALTER TABLE stocks ADD COLUMN {name} {typ}{default_clause}"
            db.execute(text(sql))
            print(f"  ✓ {name}")
        db.commit()
        print("\n✓ 迁移完成")

        # 再确认一次
        post = {r[1] for r in db.execute(text("PRAGMA table_info(stocks)")).fetchall()}
        print(f"  现在 stocks 表共 {len(post)} 列")
    finally:
        db.close()


if __name__ == "__main__":
    main("--apply" in sys.argv)
