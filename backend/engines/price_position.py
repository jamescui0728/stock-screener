"""
上市以来总收益分位（描述性指标，仅展示，不参与买卖信号）。

为每只 active 股票计算：最新【后复权】收盘在其自上市至今全部后复权收盘中的百分位
（0≈历史最低，1≈历史最高），写入 Stock.price_pctile_life。
注：后复权含分红再投，衡量的是「持有总收益」高低位，非市价距历史高低点
（高股息股会偏高）；前端展示名为「总收益分位」。

实现：按 100 只小批流式查询（stock_code 有索引），避免一次性物化全表 OON。
分位与顺序无关，只需「最新收盘」+「全历史收盘分布」：pct = mean(全历史 close ≤ 最新)。
"""
import logging

import pandas as pd
from sqlalchemy.orm import Session

from models.models import Stock, PriceData

logger = logging.getLogger(__name__)


def compute_price_pctile_life(db: Session, min_history: int = 60, batch: int = 100) -> int:
    """计算并写入所有 active 股票的上市以来价格分位。返回更新条数。"""
    codes = [r[0] for r in db.query(Stock.code).filter(Stock.is_active == True).all()]
    updated = 0
    for i in range(0, len(codes), batch):
        bcodes = codes[i:i + batch]
        rows = (
            db.query(PriceData.stock_code, PriceData.trade_date, PriceData.close)
            .filter(PriceData.stock_code.in_(bcodes),
                    PriceData.close.isnot(None), PriceData.close > 0)
            .all()
        )
        if not rows:
            continue
        h = pd.DataFrame(rows, columns=["code", "date", "close"])
        pct_map = {}
        for code, g in h.groupby("code"):
            if len(g) < min_history:
                continue                       # 上市不足 min_history 个交易日，分位无意义
            latest = g.loc[g["date"].idxmax(), "close"]
            pct_map[code] = round(float((g["close"].values <= latest).mean()), 4)
        if pct_map:
            for s in db.query(Stock).filter(Stock.code.in_(list(pct_map.keys()))).all():
                s.price_pctile_life = pct_map[s.code]
                updated += 1
            db.commit()
    logger.info(f"上市以来价格分位：更新 {updated} 只")
    return updated
