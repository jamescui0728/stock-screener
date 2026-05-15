"""
启动时的幂等 schema 迁移。

背景：SQLAlchemy 的 create_all() 只建新表，不会给已存在的表加列。
Docker 部署里数据库存在 volume 中，重建镜像时表已存在 → 新加的列不会自动出现 → 查询 500。

这里把历史上所有"加列 / 加索引"操作汇总成一张表，在 FastAPI 启动期间跑一遍：
- 用 PRAGMA table_info() 查现有列，只对缺失的 ALTER
- 索引用 CREATE INDEX IF NOT EXISTS（SQLite 原生幂等）

新加迁移：只在 COLUMN_ADDS / INDEX_CREATES 末尾追加条目即可，无需写新脚本。
独立 scripts/migrate_*.py 保留，供需要手动 dry-run 的运维场景。
"""
import logging
import re
from sqlalchemy import text
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

# PRAGMA 不支持参数化查询，用白名单校验表名/列名防止注入
_SAFE_NAME = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")


def _validate_ident(name: str):
    if not _SAFE_NAME.match(name):
        raise ValueError(f"非法 SQL 标识符: {name!r}")
    return name


# (table, column, type_definition)
#   type_definition 直接拼到 ALTER TABLE 后面，可包含 NOT NULL / DEFAULT 等
#   注：SQLite ALTER TABLE ADD COLUMN 不允许 NOT NULL 无 DEFAULT，组合时要给默认值
COLUMN_ADDS = [
    # v200 — 短期信号（stocks 表）
    ("stocks", "short_composite_score", "FLOAT"),
    ("stocks", "short_signal",          "VARCHAR(15)"),
    ("stocks", "short_signal_reason",   "TEXT"),
    ("stocks", "short_signal_updated",  "DATETIME"),
    ("stocks", "short_score_momentum",  "FLOAT"),
    ("stocks", "short_score_volprice",  "FLOAT"),
    ("stocks", "short_score_macro",     "FLOAT"),
    ("stocks", "short_score_tech",      "FLOAT"),
    ("stocks", "short_score_news_heat", "FLOAT"),

    # v200 — 长短期回测区分
    ("backtest_runs",    "signal_type", "VARCHAR(10) NOT NULL DEFAULT 'long'"),
    ("backtest_records", "hold_days",   "INTEGER"),

    # v202 — 短期信号第 6 维（行业相对反转）
    ("stocks", "short_score_industry_relative", "FLOAT"),

    # v202f — 短期信号第 7 维（定价权）
    ("stocks", "short_score_pricing_power", "FLOAT"),
]


# (index_name, table, columns_csv)
INDEX_CREATES = [
    ("ix_backtest_runs_signal_type", "backtest_runs", "signal_type"),
]


def _existing_columns(db: Session, table: str) -> set:
    """读取 SQLite 当前表的列集合。表不存在则返回 None（让外层判断）。"""
    _validate_ident(table)
    rows = db.execute(text(f"PRAGMA table_info({table})")).fetchall()
    return {r[1] for r in rows} if rows else None


def run_auto_migrations(db: Session) -> dict:
    """
    幂等运行所有挂载的 schema 迁移。
    返回 {"added_cols": [...], "added_indexes": [...], "skipped": int}

    每列单独 commit，失败回滚不影响已成功的列 — 部分迁移安全。
    """
    added_cols, added_indexes, skipped = [], [], 0

    # 按 table 分组，避免每列都 PRAGMA 一次
    tables = sorted({t for t, _, _ in COLUMN_ADDS})
    table_cols = {t: _existing_columns(db, t) for t in tables}

    for table, col, typ in COLUMN_ADDS:
        cols = table_cols.get(table)
        if cols is None:
            # 表还没建出来（init_db 应该已经建过；这里防御一下）
            logger.warning(f"auto_migrate: 表 {table} 不存在，跳过 {col}")
            skipped += 1
            continue
        if col in cols:
            skipped += 1
            continue
        _validate_ident(table)
        _validate_ident(col)
        sql = f"ALTER TABLE {table} ADD COLUMN {col} {typ}"
        try:
            db.execute(text(sql))
            db.commit()
            cols.add(col)   # 同一 table 后续判断
            added_cols.append(f"{table}.{col}")
            logger.info(f"auto_migrate: + {table}.{col}")
        except Exception as e:
            db.rollback()
            logger.error(f"auto_migrate: ALTER {table}.{col} 失败: {e}")

    for idx_name, table, cols_csv in INDEX_CREATES:
        _validate_ident(idx_name)
        _validate_ident(table)
        for col in cols_csv.split(","):
            _validate_ident(col.strip())
        sql = f"CREATE INDEX IF NOT EXISTS {idx_name} ON {table}({cols_csv})"
        try:
            db.execute(text(sql))
            db.commit()
            added_indexes.append(idx_name)
        except Exception as e:
            db.rollback()
            logger.error(f"auto_migrate: 索引 {idx_name} 失败: {e}")

    result = {
        "added_cols":    added_cols,
        "added_indexes": added_indexes,
        "skipped":       skipped,
    }
    if added_cols:
        logger.info(f"auto_migrate: 完成，加列 {len(added_cols)} / 跳过 {skipped}")
    else:
        logger.info(f"auto_migrate: 无需迁移（{skipped} 列已就绪）")
    return result
