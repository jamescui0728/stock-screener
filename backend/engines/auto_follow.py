"""
v202g 自动跟单引擎

每天 cron 跑一次（信号刷新之后），系统账户自动：
  1. 卖出持仓 >= HOLD_DAYS 天的票（匹配 backtest 的持有期）
  2. 买入新出现的 BUY/STRONG_BUY 信号（按 composite 排序，STRONG_BUY 优先）

账户：系统级别（user_id=None），名为"v202g 自动跟单"，初始 100 万。
仓位：每只固定 50,000 元（含手续费），约 ~20 只并发。

通过 PaperTransaction 的 note 字段标记 "v202g-auto" 便于绩效分析。
"""
import logging
import threading
from datetime import date, datetime, timedelta
from collections import defaultdict
from typing import Optional

from sqlalchemy import and_
from sqlalchemy.orm import Session

from config import settings
from engines.paper_trade import buy as pt_buy, sell as pt_sell, _get_latest_price
from models.models import PaperAccount, PaperPosition, PaperTransaction, Stock

logger = logging.getLogger(__name__)

_auto_account_lock = threading.Lock()


def get_or_create_auto_account(db: Session) -> PaperAccount:
    """
    系统级别的自动跟单账户（user_id IS NULL）。

    注意：filter_by(user_id=None) 在 SQLAlchemy 里**翻译成 `=NULL`**（不会匹配任何行），
    必须用显式 `user_id.is_(None)` 才是 SQL `IS NULL`。
    """
    with _auto_account_lock:
        acct = db.query(PaperAccount).filter(
            PaperAccount.user_id.is_(None),
            PaperAccount.name == settings.AUTO_FOLLOW_ACCOUNT_NAME,
        ).first()
        if acct:
            return acct
        acct = PaperAccount(
            user_id=None,
            name=settings.AUTO_FOLLOW_ACCOUNT_NAME,
            initial_cash=settings.AUTO_FOLLOW_INITIAL_CASH,
            cash_balance=settings.AUTO_FOLLOW_INITIAL_CASH,
        )
        db.add(acct)
        try:
            db.commit()
            db.refresh(acct)
        except Exception:
            db.rollback()
            acct = db.query(PaperAccount).filter(
                PaperAccount.user_id.is_(None),
                PaperAccount.name == settings.AUTO_FOLLOW_ACCOUNT_NAME,
            ).first()
            if not acct:
                raise
        logger.info(
            f"创建自动跟单账户 id={acct.id}（初始资金 {settings.AUTO_FOLLOW_INITIAL_CASH}）"
        )
        return acct


def _shares_for_target_amount(price: float, target_amount: float) -> int:
    """按 100 整数倍 + 不超过 target_amount。"""
    if price <= 0:
        return 0
    raw = int(target_amount / price / 100) * 100
    return max(0, raw)


def run_v202g_auto_follow(db: Session) -> dict:
    """
    每日跟单核心逻辑。返回执行摘要：
      {"sold": [...], "bought": [...], "skipped": [...], "errors": [...]}
    """
    acct = get_or_create_auto_account(db)
    # 容器 TZ=Asia/Shanghai；date.today() 走系统时区，避免 utcnow() 在 22:00-08:00
    # 把"今天"算成昨天，从而把刚建仓的持仓算多 1 天。
    today = date.today()

    sold, bought, skipped, errors = [], [], [], []

    # ───────── Step 1: 卖出超期持仓 ─────────
    positions = db.query(PaperPosition).filter(
        PaperPosition.account_id == acct.id, PaperPosition.shares > 0
    ).all()

    for pos in positions:
        opened = pos.opened_at.date() if pos.opened_at else today
        held = (today - opened).days
        if held < settings.AUTO_FOLLOW_HOLD_DAYS:
            continue
        # 全部卖出
        try:
            r = pt_sell(
                db, acct.id, pos.stock_code, shares=int(pos.shares),
                note=f"{settings.AUTO_FOLLOW_NOTE_TAG} | hold={held}d 自动平仓",
            )
            sold.append({
                "code": pos.stock_code, "shares": int(pos.shares),
                "held_days": held,
                "realized_pnl": r.get("transaction", {}).get("realized_pnl"),
            })
        except Exception as e:
            errors.append({"action": "sell", "code": pos.stock_code, "err": str(e)})
            logger.warning(f"自动卖出 {pos.stock_code} 失败: {e}")

    # ───────── Step 2: 买入新 BUY 信号 ─────────
    # 优先 STRONG_BUY，按 composite 降序
    candidates = db.query(Stock).filter(
        Stock.is_active == True,
        Stock.short_signal.in_(("BUY", "STRONG_BUY")),
        Stock.short_composite_score >= settings.SHORT_BUY_THRESHOLD,
    ).order_by(
        # STRONG_BUY 优先
        (Stock.short_signal == "STRONG_BUY").desc(),
        Stock.short_composite_score.desc(),
    ).all()

    # 当前已持仓代码（避免重复买）
    held_codes = {p.stock_code for p in db.query(PaperPosition).filter(
        PaperPosition.account_id == acct.id, PaperPosition.shares > 0
    ).all()}

    # 也跳过今天已卖（避免立刻又买回来 — A 股 T+1 隐含约束）
    today_dt = datetime.combine(today, datetime.min.time())
    sold_today_codes = {
        t.stock_code for t in db.query(PaperTransaction).filter(
            PaperTransaction.account_id == acct.id,
            PaperTransaction.side == "SELL",
            PaperTransaction.trade_time >= today_dt,
        ).all()
    }

    max_new_buys = max(0, getattr(settings, "SHORT_MAX_BUY_PER_CHECK_DATE", 0) or 0)
    max_open = max(1, int(getattr(settings, "AUTO_FOLLOW_MAX_OPEN_POSITIONS", 20) or 20))
    for stock in candidates:
        if len(held_codes) + len(bought) >= max_open:
            skipped.append({"code": stock.code, "reason": f"max_open_positions_{max_open}"})
            continue
        if max_new_buys and len(bought) >= max_new_buys:
            skipped.append({"code": stock.code, "reason": f"daily_buy_cap_{max_new_buys}"})
            continue
        if stock.code in held_codes:
            skipped.append({"code": stock.code, "reason": "already_held"})
            continue
        if stock.code in sold_today_codes:
            skipped.append({"code": stock.code, "reason": "sold_today"})
            continue
        px = _get_latest_price(db, stock.code)
        if not px or px <= 0:
            skipped.append({"code": stock.code, "reason": "no_price"})
            continue
        shares = _shares_for_target_amount(px, settings.AUTO_FOLLOW_POSITION_YUAN)
        if shares <= 0:
            skipped.append({"code": stock.code, "reason": f"price_too_high ({px})"})
            continue
        # 提前估算资金是否够（含手续费的安全 buffer）
        est_cost = px * shares * 1.001
        if est_cost > acct.cash_balance:
            skipped.append({"code": stock.code, "reason": f"cash_short (need {est_cost:.0f}, have {acct.cash_balance:.0f})"})
            continue
        try:
            r = pt_buy(
                db, acct.id, stock.code, shares=shares, price=px,
                note=f"{settings.AUTO_FOLLOW_NOTE_TAG} | "
                     f"{stock.short_signal} composite={stock.short_composite_score}",
            )
            bought.append({
                "code": stock.code, "name": stock.name,
                "shares": shares, "price": px,
                "signal": stock.short_signal,
                "composite": stock.short_composite_score,
            })
            held_codes.add(stock.code)
            db.refresh(acct)
        except Exception as e:
            errors.append({"action": "buy", "code": stock.code, "err": str(e)})
            logger.warning(f"自动买入 {stock.code} 失败: {e}")

    summary = {
        "account_id":   acct.id,
        "date":         today.isoformat(),
        "sold_n":       len(sold),
        "bought_n":     len(bought),
        "skipped_n":    len(skipped),
        "errors_n":     len(errors),
        "cash_balance": acct.cash_balance,
        "sold":         sold,
        "bought":       bought,
        "skipped":      skipped[:10],   # 限长，太多没意义
        "errors":       errors,
    }
    logger.info(
        f"v202g 自动跟单完成：卖出 {len(sold)}，买入 {len(bought)}，"
        f"跳过 {len(skipped)}，失败 {len(errors)}，剩余现金 {acct.cash_balance:.0f}"
    )
    return summary


def get_performance(db: Session) -> dict:
    """
    返回自动跟单账户的绩效摘要：
    - 总交易、买入、卖出、平均持仓天数
    - 已实现 PnL 统计（胜率、累计、平均、median）
    - 当前持仓快照
    """
    acct = get_or_create_auto_account(db)

    txns = db.query(PaperTransaction).filter(
        PaperTransaction.account_id == acct.id,
        PaperTransaction.note.like(f"%{settings.AUTO_FOLLOW_NOTE_TAG}%"),
    ).order_by(PaperTransaction.trade_time.asc()).all()

    buys  = [t for t in txns if t.side == "BUY"]
    sells = [t for t in txns if t.side == "SELL"]

    # 配对持仓：FIFO 配对（简化版，因为我们每只只买一次）
    pnls = [t.realized_pnl for t in sells if t.realized_pnl is not None]
    win_rate = sum(1 for p in pnls if p > 0) / len(pnls) * 100 if pnls else None
    avg_pnl  = sum(pnls) / len(pnls) if pnls else 0
    total_pnl = sum(pnls)

    # 当前持仓
    positions = db.query(PaperPosition).filter(
        PaperPosition.account_id == acct.id, PaperPosition.shares > 0
    ).all()
    pos_snapshot = []
    today = date.today()
    for p in positions:
        cur = _get_latest_price(db, p.stock_code)
        market_value = (cur * p.shares) if cur else None
        unrealized = ((cur - p.avg_cost) * p.shares) if cur else None
        held_days = (today - p.opened_at.date()).days if p.opened_at else None
        stock = db.query(Stock).filter_by(code=p.stock_code).first()
        pos_snapshot.append({
            "code":         p.stock_code,
            "name":         stock.name if stock else "",
            "shares":       p.shares,
            "avg_cost":     p.avg_cost,
            "current_price": cur,
            "market_value": market_value,
            "unrealized":   unrealized,
            "held_days":    held_days,
        })

    market_value_total = sum(p["market_value"] for p in pos_snapshot if p["market_value"]) or 0
    nav = acct.cash_balance + market_value_total
    total_return_pct = (nav - acct.initial_cash) / acct.initial_cash * 100

    return {
        "account_id":     acct.id,
        "initial_cash":   acct.initial_cash,
        "cash_balance":   acct.cash_balance,
        "market_value":   round(market_value_total, 2),
        "nav":            round(nav, 2),
        "total_return":   round(total_return_pct, 2),
        "n_buys":         len(buys),
        "n_sells":        len(sells),
        "n_open":         len(positions),
        "win_rate":       round(win_rate, 2) if win_rate is not None else None,
        "avg_realized_pnl": round(avg_pnl, 2),
        "total_realized_pnl": round(total_pnl, 2),
        "positions":      pos_snapshot,
    }
