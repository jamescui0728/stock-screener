"""
数据库迁移：引入多用户支持
- 新建 users 表
- 给 watchlist、paper_account 加 user_id 列（nullable，兼容老数据）
- （可选）把现有 watchlist + paper_account 归属到首个管理员用户

幂等：每一步都先检查再执行，可反复运行。

用法：
  cd backend
  .venv/bin/python scripts/migrate_add_users.py
  # 可选：同时归属旧数据给管理员
  .venv/bin/python scripts/migrate_add_users.py --assign-to <admin_phone>
"""
import argparse
import sys
from pathlib import Path

# 允许脚本直接从 backend/ 目录运行
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import inspect, text  # noqa: E402

from auth import hash_password, is_valid_phone, normalize_phone  # noqa: E402
from database import SessionLocal, engine, init_db  # noqa: E402
from models.models import PaperAccount, User, Watchlist  # noqa: E402


def _col_exists(table: str, col: str) -> bool:
    insp = inspect(engine)
    return col in {c["name"] for c in insp.get_columns(table)}


def _ensure_column(table: str, col: str, ddl: str):
    if _col_exists(table, col):
        print(f"  · {table}.{col}  已存在，跳过")
        return
    with engine.begin() as conn:
        conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {ddl}"))
    print(f"  ✓ 新增列 {table}.{col}")


def migrate():
    print("▶ 1/3 确保所有表存在（users 表会被新建）...")
    init_db()
    print("  ✓ 完成")

    print("▶ 2/3 给旧表加 user_id 列（幂等）...")
    _ensure_column("watchlist",     "user_id", "user_id INTEGER")
    _ensure_column("paper_account", "user_id", "user_id INTEGER")

    # 确保 paper_account.user_id 有索引（唯一约束在 ORM 层，迁移只加索引不强约束）
    with engine.begin() as conn:
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_paper_account_user_id "
            "ON paper_account(user_id)"
        ))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_watchlist_user_id "
            "ON watchlist(user_id)"
        ))
    print("  ✓ 索引已就绪")

    print("▶ 3/3 ok.")


def create_admin(phone: str, password: str, name: str = "管理员") -> User:
    phone = normalize_phone(phone)
    if not is_valid_phone(phone):
        print(f"✘ 手机号格式不正确: {phone}")
        sys.exit(2)

    db = SessionLocal()
    try:
        user = db.query(User).filter_by(phone=phone).first()
        if user:
            print(f"  · 管理员已存在（id={user.id}, phone={phone}），跳过创建")
            return user
        user = User(
            phone=phone,
            password_hash=hash_password(password),
            name=name,
            is_admin=True,
            is_active=True,
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        print(f"  ✓ 创建管理员用户 id={user.id}, phone={phone}, 密码={password}")
        return user
    finally:
        db.close()


def assign_orphan_data_to_user(user_id: int):
    """把 user_id IS NULL 的旧自选股/模拟盘账户归属到指定用户"""
    db = SessionLocal()
    try:
        # watchlist
        wl_updated = (
            db.query(Watchlist)
            .filter(Watchlist.user_id.is_(None))
            .update({"user_id": user_id})
        )
        # paper_account（理论上最多 1 条 legacy）
        pa_orphans = db.query(PaperAccount).filter(PaperAccount.user_id.is_(None)).all()
        pa_updated = 0
        for pa in pa_orphans:
            # 如果目标用户已有账户，就跳过（避免唯一键冲突）
            if db.query(PaperAccount).filter_by(user_id=user_id).first():
                continue
            pa.user_id = user_id
            pa_updated += 1
        db.commit()
        print(f"  ✓ 归属旧数据到 user_id={user_id}："
              f"watchlist {wl_updated} 条，paper_account {pa_updated} 条")
    finally:
        db.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--assign-to", metavar="PHONE",
                    help="创建/查找该手机号的管理员，并把旧的无主数据归属给他")
    ap.add_argument("--password", default="admin123456",
                    help="创建管理员时的初始密码（仅 --assign-to 首次生效）")
    ap.add_argument("--name", default="管理员")
    args = ap.parse_args()

    migrate()

    if args.assign_to:
        print(f"▶ 创建/查找管理员 {args.assign_to} 并迁移旧数据...")
        admin = create_admin(args.assign_to, args.password, args.name)
        assign_orphan_data_to_user(admin.id)

    print("✓ 迁移完成")
