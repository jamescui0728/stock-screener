"""
直接调用 run_backtest 在扩充后股票池上跑 v108，无需 Web 服务。
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backtest.engine import run_backtest
from database import SessionLocal
from models.models import BacktestRun


def main():
    db = SessionLocal()
    latest = db.query(BacktestRun).order_by(BacktestRun.version.desc()).first()
    next_version = (latest.version + 1) if latest else 1
    db.close()

    # v108 参数：沿用当前 user_settings.json 中的
    run_id = run_backtest(
        params=None,  # 使用全局 settings
        description=f"v108 扩充样本 ~{600}只股票池验证（fin_macro_min=65）",
        version=next_version,
    )
    print(f"\n[DONE] run_id={run_id} version=v{next_version}")


if __name__ == "__main__":
    main()
