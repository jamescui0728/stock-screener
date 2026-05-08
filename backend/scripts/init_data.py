"""
首次初始化脚本：拉取行业列表 + 股票列表 + 行业映射
运行一次即可，此后由定时任务自动维护
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from database import init_db, SessionLocal
from data.fetcher import (
    fetch_industry_list,
    fetch_stock_list,
    fetch_stock_industry_mapping,
    fetch_macro_data,
)
from engines.industry_scorer import score_all_industries

def main():
    print("=== 初始化数据库 ===")
    init_db()
    db = SessionLocal()
    try:
        print("1/5 拉取行业列表...")
        n = fetch_industry_list(db)
        print(f"    行业：{n} 条")

        print("2/5 拉取股票列表...")
        n = fetch_stock_list(db)
        print(f"    股票：{n} 条")

        print("3/5 建立行业-股票映射（耗时较长）...")
        n = fetch_stock_industry_mapping(db)
        print(f"    更新：{n} 只")

        print("4/5 拉取宏观数据...")
        n = fetch_macro_data(db)
        print(f"    宏观：{n} 条")

        print("5/5 计算行业初始评分...")
        scores = score_all_industries(db)
        print(f"    评分：{len(scores)} 个行业")

        print("\n=== 初始化完成 ===")
        print("接下来运行：python main.py 启动服务")
        print("再运行财报拉取（耗时较长，建议分批）：")
        print("  POST /api/data/update-financials?limit=100")
    finally:
        db.close()

if __name__ == "__main__":
    main()
