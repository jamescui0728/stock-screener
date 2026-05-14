"""
v200 migration：回测表新增长/短期区分字段。

- backtest_runs.signal_type        : VARCHAR(10) NOT NULL DEFAULT 'long'
- backtest_records.hold_days       : INTEGER NULL（旧 hold_months 仍保留）

幂等：已存在的列跳过。

使用：
  .venv/bin/python scripts/migrate_add_backtest_signal_type.py            # dry-run
  .venv/bin/python scripts/migrate_add_backtest_signal_type.py --apply    # 实际执行
"""
import sys
sys.path.insert(0, ".")

from database import SessionLocal
from sqlalchemy import text


CHANGES = [
    # (table, column, type_with_default)
    ("backtest_runs",    "signal_type", "VARCHAR(10) NOT NULL DEFAULT 'long'"),
    ("backtest_records", "hold_days",   "INTEGER"),
]


def main(apply: bool):
    db = SessionLocal()
    try:
        to_add = []
        for table, col, typ in CHANGES:
            existing = {r[1] for r in db.execute(text(f"PRAGMA table_info({table})")).fetchall()}
            if col in existing:
                print(f"[skip] {table}.{col} 已存在")
            else:
                to_add.append((table, col, typ))

        if not to_add:
            print("\n✓ 无需迁移")
            return

        print(f"\n=== 待添加 {len(to_add)} 列 ===")
        for table, col, typ in to_add:
            print(f"  ALTER TABLE {table} ADD COLUMN {col} {typ};")

        if not apply:
            print("\n[dry-run] 加 --apply 实际执行")
            return

        print("\n=== 执行 ===")
        for table, col, typ in to_add:
            db.execute(text(f"ALTER TABLE {table} ADD COLUMN {col} {typ}"))
            print(f"  ✓ {table}.{col}")
        db.commit()

        # 索引
        try:
            db.execute(text("CREATE INDEX IF NOT EXISTS ix_backtest_runs_signal_type ON backtest_runs(signal_type)"))
            db.commit()
            print("  ✓ ix_backtest_runs_signal_type")
        except Exception as e:
            print(f"  [warn] 索引创建失败: {e}")

        print("\n✓ 完成")
    finally:
        db.close()


if __name__ == "__main__":
    main("--apply" in sys.argv)
