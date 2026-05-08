"""
多账户模式 (Option D) 下的"收养"迁移：
- 把孤儿账户 account_id=1 (user_id=None) 直接挂到目标用户名下，作为该用户
  的另一个独立账户（不合并）。
- 顺手给两个账户起个有意义的名字，方便前端切换时识别。

默认：
  SRC = account_id 1   (user_id=None) → 收养给 USER_ID=1
  名字： id=1 改成 "早期账户"； id=2 改成 "默认账户"

使用：
  .venv/bin/python scripts/adopt_orphan_paper_account.py            # dry-run
  .venv/bin/python scripts/adopt_orphan_paper_account.py --apply    # 实际写库
"""
import sys

sys.path.insert(0, ".")

from database import SessionLocal
from models.models import PaperAccount, PaperPosition, PaperTransaction

USER_ID  = 1            # 目标用户
ORPHAN_ID = 1           # 待收养的孤儿账户
ORPHAN_NAME = "早期账户"
DEFAULT_NAME = "默认账户"   # 用户既有的默认账户（最早一个除孤儿外）


def main(apply: bool):
    db = SessionLocal()
    try:
        orphan = db.query(PaperAccount).filter_by(id=ORPHAN_ID).first()
        if not orphan:
            print(f"账户 id={ORPHAN_ID} 不存在，无需迁移。")
            return
        if orphan.user_id is not None:
            print(f"账户 id={ORPHAN_ID} 已经有 user_id={orphan.user_id}，不是孤儿，跳过。")
            return

        # 用户名下其它账户（不含孤儿）
        existing = (
            db.query(PaperAccount)
            .filter(PaperAccount.user_id == USER_ID, PaperAccount.id != ORPHAN_ID)
            .order_by(PaperAccount.id.asc())
            .all()
        )

        n_pos = db.query(PaperPosition).filter_by(account_id=ORPHAN_ID).count()
        n_tx  = db.query(PaperTransaction).filter_by(account_id=ORPHAN_ID).count()

        print("=" * 60)
        print("迁移计划：")
        print(f"  孤儿账户 id={orphan.id}  cash={orphan.cash_balance}  "
              f"持仓={n_pos}  流水={n_tx}")
        print(f"  → 收养给 user_id={USER_ID}, 重命名为「{ORPHAN_NAME}」")
        if existing:
            print(f"  用户已有账户：")
            for a in existing:
                tag = f"  → 重命名为「{DEFAULT_NAME}」" if a is existing[0] else ""
                print(f"    id={a.id}  name={a.name!r}  cash={a.cash_balance}{tag}")
        else:
            print(f"  用户当前还没有其它账户。")
        print("=" * 60)

        if not apply:
            print("\n[dry-run] 未写库。加 --apply 实际执行。")
            return

        orphan.user_id = USER_ID
        orphan.name    = ORPHAN_NAME
        if existing:
            existing[0].name = DEFAULT_NAME
        db.commit()
        print("\n✓ 迁移完成。可以在 /paper/accounts 看到这两个账户。")
    finally:
        db.close()


if __name__ == "__main__":
    apply = "--apply" in sys.argv
    main(apply)
