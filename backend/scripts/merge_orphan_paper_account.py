"""
合并孤儿模拟盘账户（user_id=None）到目标用户的账户：
- SRC = account_id 1 (user_id=None)        ← 用户系统上线前的孤儿数据
- DST = account_id 2 (user_id=1)           ← 当前用户 1 在用的账户

策略：以 paper_transactions 为唯一真实来源，按时间顺序重放出
  cash_balance + paper_positions，保证与 engine 的 buy/sell 逻辑完全一致。

使用：
  .venv/bin/python scripts/merge_orphan_paper_account.py        # dry-run，只打印结果
  .venv/bin/python scripts/merge_orphan_paper_account.py --apply  # 实际写库
"""
import sys
from collections import defaultdict
from sqlalchemy import update, delete

sys.path.insert(0, ".")

from database import SessionLocal
from models.models import PaperAccount, PaperPosition, PaperTransaction

SRC_ACCT_ID = 1   # 孤儿
DST_ACCT_ID = 2   # 用户 1 当前账户


def main(apply: bool):
    db = SessionLocal()
    try:
        src = db.query(PaperAccount).filter_by(id=SRC_ACCT_ID).first()
        dst = db.query(PaperAccount).filter_by(id=DST_ACCT_ID).first()
        assert src and dst, "源/目标账户不存在"
        print(f"SRC: id={src.id} user_id={src.user_id} init={src.initial_cash} cash={src.cash_balance}")
        print(f"DST: id={dst.id} user_id={dst.user_id} init={dst.initial_cash} cash={dst.cash_balance}")

        # 1. 收集两个账户的全部交易，按时间排序
        txns = (
            db.query(PaperTransaction)
            .filter(PaperTransaction.account_id.in_([SRC_ACCT_ID, DST_ACCT_ID]))
            .order_by(PaperTransaction.trade_time.asc(), PaperTransaction.id.asc())
            .all()
        )
        print(f"待重放交易数: {len(txns)}")

        # 2. 重放（与 engines/paper_trade.py 的 buy/sell 公式严格一致）
        cash = float(dst.initial_cash)
        positions: dict = {}   # stock_code → {"shares": float, "cost_total": float}
        for t in txns:
            amount = float(t.amount or 0)
            fee    = float(t.fee or 0)
            if t.side == "BUY":
                cash -= (amount + fee)
                p = positions.setdefault(t.stock_code, {"shares": 0.0, "cost_total": 0.0})
                p["shares"]     += float(t.shares)
                p["cost_total"] += amount  # 含买入金额，不含手续费（与 engine 行为一致）
            elif t.side == "SELL":
                cash += (amount - fee)
                p = positions.get(t.stock_code)
                if p and p["shares"] > 0:
                    # 按比例减少 cost_total，保持 avg_cost 不变
                    sell_ratio = float(t.shares) / p["shares"]
                    p["cost_total"] *= (1 - sell_ratio)
                    p["shares"]     -= float(t.shares)
                    if p["shares"] <= 1e-6:
                        positions.pop(t.stock_code)

        cash = round(cash, 2)
        print(f"\n新 cash_balance = {cash:,.2f}")
        print(f"新持仓 ({len(positions)} 只):")
        for code, p in sorted(positions.items()):
            avg_cost = p["cost_total"] / p["shares"] if p["shares"] else 0
            print(f"  {code:>8s}: shares={p['shares']:>10.0f}, avg_cost={avg_cost:>8.4f}")

        if not apply:
            print("\n[dry-run] 未写库。加 --apply 实际执行。")
            return

        # 3. 实际写库
        print("\n=== 开始写库 ===")
        # 3a. 把 SRC 的 transactions 改挂到 DST
        n = (
            db.query(PaperTransaction)
            .filter_by(account_id=SRC_ACCT_ID)
            .update({"account_id": DST_ACCT_ID}, synchronize_session=False)
        )
        print(f"  搬迁 {n} 条交易: account_id {SRC_ACCT_ID} → {DST_ACCT_ID}")

        # 3b. 删除 SRC 和 DST 的旧持仓
        n1 = db.query(PaperPosition).filter_by(account_id=SRC_ACCT_ID).delete(synchronize_session=False)
        n2 = db.query(PaperPosition).filter_by(account_id=DST_ACCT_ID).delete(synchronize_session=False)
        print(f"  清空旧持仓: SRC={n1}, DST={n2}")

        # 3c. 写入新持仓
        from datetime import datetime
        for code, p in positions.items():
            avg_cost = p["cost_total"] / p["shares"] if p["shares"] else 0
            db.add(PaperPosition(
                account_id=DST_ACCT_ID,
                stock_code=code,
                shares=p["shares"],
                avg_cost=round(avg_cost, 4),
                opened_at=datetime.utcnow(),
                updated_at=datetime.utcnow(),
            ))
        print(f"  写入 {len(positions)} 条新持仓")

        # 3d. 更新 DST.cash_balance
        dst.cash_balance = cash
        print(f"  更新 DST.cash_balance = {cash}")

        # 3e. 删除空的 SRC 账户
        db.delete(src)
        print(f"  删除孤儿账户 id={SRC_ACCT_ID}")

        db.commit()
        print("✓ 写库完成")
    finally:
        db.close()


if __name__ == "__main__":
    apply = "--apply" in sys.argv
    main(apply)
