"""
模拟盘业务逻辑：账户 / 买入 / 卖出 / 估值。
- 多账户模型：每个 user 可有多个 PaperAccount（user_id 一对多，登录后通过
  /paper/accounts CRUD 切换 / 创建 / 重命名 / 删除）。所有权校验在路由层做，
  引擎层只接 account_id 直接寻址。
- 初始资金：从 settings.PAPER_INIT_CASH 读（默认 100 万，可在"参数设置"热更新）
- 手续费：万分之三（买卖均收），最低 5 元
- 最小买入单位：100 股（A 股标准）
- 当前价：用 Sina 实时不复权收盘价（与真实市场价一致），并行预热缓存
  回退策略：网络失败时 → DB price_data 最近一条（注意是后复权价，仅兜底）
"""
import logging
import time
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy.orm import Session
from sqlalchemy import desc

from config import settings
from models.models import (
    PaperAccount, PaperPosition, PaperTransaction,
    PriceData, Stock,
)

logger = logging.getLogger(__name__)


# ── 交易规则参数（从 settings 动态读取，支持在"参数设置"页热更新）──
def _fee_rate() -> float:
    return float(getattr(settings, "PAPER_FEE_RATE", 0.0003))

def _min_fee() -> float:
    return float(getattr(settings, "PAPER_MIN_FEE", 5.0))

def _lot_size() -> int:
    return int(getattr(settings, "PAPER_LOT_SIZE", 100))

def _init_cash() -> float:
    return float(getattr(settings, "PAPER_INIT_CASH", 1_000_000.0))

# ── 实时价格缓存（10 分钟 TTL）──
# 避免每次查询/下单都打 Sina 接口（刷新持仓列表时可能 N 只）
_PRICE_CACHE: dict[str, tuple[float, str, float]] = {}
# stock_code -> (price, trade_date_str, cached_at_ts)
_CACHE_TTL_SEC = 600   # 10 分钟


# ───────────────────────────── 账户 ─────────────────────────────
def list_accounts(db: Session, user_id: int) -> list[PaperAccount]:
    """列出某用户的全部账户（按 id 升序）"""
    return (
        db.query(PaperAccount)
        .filter_by(user_id=user_id)
        .order_by(PaperAccount.id.asc())
        .all()
    )


def get_account(db: Session, account_id: int, user_id: Optional[int] = None) -> PaperAccount:
    """取账户；指定 user_id 时校验所有权（不属则抛 PermissionError）"""
    acct = db.query(PaperAccount).filter_by(id=account_id).first()
    if not acct:
        raise ValueError(f"账户 {account_id} 不存在")
    if user_id is not None and acct.user_id != user_id:
        raise PermissionError(f"账户 {account_id} 不属于当前用户")
    return acct


def get_or_create_default_account(db: Session, user_id: int) -> PaperAccount:
    """
    取用户的"默认"（最早创建）账户；如果完全没有，建一个。
    多账户模式下用于：登录后没有显式选账户时的回退。
    """
    acct = (
        db.query(PaperAccount)
        .filter_by(user_id=user_id)
        .order_by(PaperAccount.id.asc())
        .first()
    )
    if not acct:
        ic = _init_cash()
        acct = PaperAccount(
            user_id=user_id, name="默认账户",
            initial_cash=ic, cash_balance=ic,
        )
        db.add(acct)
        db.commit()
        db.refresh(acct)
    return acct


def create_account(
    db: Session, user_id: int, name: str = "新账户",
    initial_cash: Optional[float] = None,
) -> PaperAccount:
    """创建新账户"""
    if initial_cash is None or initial_cash <= 0:
        initial_cash = _init_cash()
    acct = PaperAccount(
        user_id=user_id,
        name=(name or "").strip() or "新账户",
        initial_cash=initial_cash,
        cash_balance=initial_cash,
    )
    db.add(acct)
    db.commit()
    db.refresh(acct)
    return acct


def update_account(
    db: Session, account_id: int, user_id: int,
    name: Optional[str] = None,
) -> PaperAccount:
    """重命名账户（其他属性如初始资金不在这里改，要改请用 reset_account）"""
    acct = get_account(db, account_id, user_id=user_id)
    if name is not None:
        acct.name = (name or "").strip() or acct.name
    db.commit()
    db.refresh(acct)
    return acct


def delete_account(db: Session, account_id: int, user_id: int) -> None:
    """删除账户。安全限制：用户至少要保留一个账户。"""
    acct = get_account(db, account_id, user_id=user_id)
    others = (
        db.query(PaperAccount)
        .filter(PaperAccount.user_id == user_id, PaperAccount.id != account_id)
        .count()
    )
    if others == 0:
        raise ValueError("不能删除最后一个账户（至少保留 1 个）")
    db.delete(acct)   # cascade 会带走持仓和流水
    db.commit()


def reset_account(
    db: Session, account_id: int, user_id: int,
    initial_cash: Optional[float] = None,
) -> PaperAccount:
    """清仓所有持仓和流水，重置现金到初始值（不传则用 settings.PAPER_INIT_CASH）"""
    if initial_cash is None or initial_cash <= 0:
        initial_cash = _init_cash()
    acct = get_account(db, account_id, user_id=user_id)
    db.query(PaperPosition).filter_by(account_id=acct.id).delete()
    db.query(PaperTransaction).filter_by(account_id=acct.id).delete()
    acct.initial_cash = initial_cash
    acct.cash_balance = initial_cash
    acct.reset_at     = datetime.utcnow()
    db.commit()
    db.refresh(acct)
    return acct


# ───────────────────────────── 估值 ─────────────────────────────
def _fetch_live_price(stock_code: str) -> Optional[tuple]:
    """从 Sina 抓实时不复权收盘价；返回 (price, trade_date_str) 或 None"""
    try:
        import akshare as ak
        prefix = "sh" if stock_code.startswith(("6", "9")) else "sz"
        # 拉最近 5 天，拿最后一条
        end = datetime.now().strftime("%Y%m%d")
        start = (datetime.now() - timedelta(days=10)).strftime("%Y%m%d")
        df = ak.stock_zh_a_daily(
            symbol=f"{prefix}{stock_code}",
            start_date=start, end_date=end,
            adjust="",                      # 关键：不复权 = 真实市场价
        )
        if df is None or df.empty:
            return None
        last = df.iloc[-1]
        return float(last["close"]), str(last["date"])
    except Exception as e:
        logger.warning(f"拉取实时价失败 {stock_code}: {e}")
        return None


def _get_latest_price(db: Session, stock_code: str) -> Optional[float]:
    """
    返回 (price, trade_date_str)。优先实时不复权价（带 10 分钟缓存），
    失败时回退 DB price_data（注意是后复权价，不准，仅兜底）。
    """
    p, _ = _get_latest_price_with_date(db, stock_code)
    return p


def _get_latest_price_with_date(
    db: Session, stock_code: str
) -> tuple[Optional[float], Optional[str]]:
    now = time.time()
    cached = _PRICE_CACHE.get(stock_code)
    if cached and (now - cached[2] < _CACHE_TTL_SEC):
        return cached[0], cached[1]

    live = _fetch_live_price(stock_code)
    if live:
        price, date_str = live
        _PRICE_CACHE[stock_code] = (price, date_str, now)
        return price, date_str

    # 兜底：DB（后复权，不准但总比没有好）
    p = (
        db.query(PriceData)
        .filter_by(stock_code=stock_code)
        .order_by(desc(PriceData.trade_date))
        .first()
    )
    if p:
        logger.warning(f"{stock_code} 使用 DB 兜底价（后复权，可能失真）")
        return p.close, str(p.trade_date)
    return None, None


def _calc_fee(amount: float) -> float:
    return max(round(amount * _fee_rate(), 2), _min_fee())


# 并发预热价格缓存，避免 N 只持仓时 N×Sina-RTT 串行阻塞
def _warmup_prices_parallel(db: Session, stock_codes: list,
                             per_call_timeout: int = 8,
                             overall_timeout: Optional[int] = None) -> None:
    """
    并行拉取一批股票的实时价，结果直接入 _PRICE_CACHE。
    - per_call_timeout：单只 sina 调用最长等待秒数；超时视为失败，下游会用 DB 兜底
    - overall_timeout：整个函数最长执行秒数（默认 per_call_timeout + 6）。
      并发执行时整体应接近"最慢一只"，加 6s buffer 应对 thread pool 调度 + DB 写。
      到点未完成的 future 会被放弃（线程仍在跑但不再 block 主线程）。

    背景：sina 偶尔抖动时单只重试 3 次 × 8s = 24s，并发也救不了——必须让函数
    在 hard cap 内返回，避免拖到 axios 30s timeout。
    """
    import concurrent.futures as _cf
    # 去掉缓存内仍有效的，避免重复打 sina
    now = time.time()
    pending = []
    for code in stock_codes:
        cached = _PRICE_CACHE.get(code)
        if not (cached and (now - cached[2] < _CACHE_TTL_SEC)):
            pending.append(code)
    if not pending:
        return

    if overall_timeout is None:
        overall_timeout = per_call_timeout + 6

    def _fetch_one(code: str):
        live = _fetch_live_price(code)
        if live:
            price, date_str = live
            _PRICE_CACHE[code] = (price, date_str, time.time())
        return code

    # max_workers 不超过待拉的数量，避免线程浪费
    with _cf.ThreadPoolExecutor(max_workers=min(16, len(pending))) as ex:
        futures = {ex.submit(_fetch_one, c): c for c in pending}
        try:
            for fut in _cf.as_completed(futures, timeout=overall_timeout):
                try:
                    fut.result(timeout=per_call_timeout)
                except (_cf.TimeoutError, Exception) as e:
                    code = futures[fut]
                    logger.debug(f"warmup price {code}: {e}")
        except _cf.TimeoutError:
            # 整体到点：放弃剩余未完成的 future（worker 线程仍跑但不 block 主线程）
            unfinished = sum(1 for f in futures if not f.done())
            logger.warning(
                f"warmup 整体超时（>{overall_timeout}s）：放弃 {unfinished} / {len(pending)} 只"
                f"未完成的 sina 请求（已成功的进缓存，未完成的下次访问时按需补）"
            )


def account_snapshot(db: Session, account_id: int) -> dict:
    """账户快照：现金 / 持仓市值 / 总资产 / 盈亏 / 收益率
    （ownership 校验在路由层做；这里直接按 account_id 取）"""
    acct = get_account(db, account_id)
    positions = db.query(PaperPosition).filter_by(account_id=acct.id).all()

    # 关键性能优化：并行预热所有持仓的实时价，避免 N 次串行 Sina 调用
    codes = [p.stock_code for p in positions if p.shares and p.shares > 0]
    if codes:
        _warmup_prices_parallel(db, codes, per_call_timeout=8)

    # 一次性批量加载 Stock，避免 N+1 单查（14 只持仓 → 1 次 IN 查询）
    stocks_map = {s.code: s for s in db.query(Stock).filter(Stock.code.in_(codes)).all()} if codes else {}

    position_details = []
    market_value = 0.0
    cost_value   = 0.0

    for p in positions:
        if not p.shares or p.shares <= 0:
            continue
        cur = _get_latest_price(db, p.stock_code)
        stock = stocks_map.get(p.stock_code)
        mv = (cur or p.avg_cost) * p.shares
        cv = p.avg_cost * p.shares
        market_value += mv
        cost_value   += cv
        position_details.append({
            "stock_code":           p.stock_code,
            "stock_name":           stock.name if stock else p.stock_code,
            "shares":               p.shares,
            "avg_cost":             round(p.avg_cost, 3),
            "current_price":        round(cur, 3) if cur else None,
            "market_value":         round(mv, 2),
            "cost_value":           round(cv, 2),
            "unrealized_pnl":       round(mv - cv, 2),
            "pnl_pct":              round((mv / cv - 1) * 100, 2) if cv else 0.0,
            "signal":               stock.signal if stock else None,
            "composite_score":      stock.composite_score if stock else None,
            "short_signal":         stock.short_signal if stock else None,
            "short_composite_score": stock.short_composite_score if stock else None,
            "opened_at":            str(p.opened_at),
        })

    total_assets = acct.cash_balance + market_value
    total_pnl    = total_assets - acct.initial_cash
    return {
        "account_id":     acct.id,
        "name":           acct.name,
        "initial_cash":   round(acct.initial_cash, 2),
        "cash_balance":   round(acct.cash_balance, 2),
        "market_value":   round(market_value, 2),
        "cost_value":     round(cost_value, 2),
        "total_assets":   round(total_assets, 2),
        "total_pnl":      round(total_pnl, 2),
        "total_return_pct": round(total_pnl / acct.initial_cash * 100, 2)
                            if acct.initial_cash else 0.0,
        "unrealized_pnl": round(market_value - cost_value, 2),
        "position_count": len(position_details),
        "positions":      position_details,
        "reset_at":       str(acct.reset_at),
    }


# ───────────────────────────── 交易 ─────────────────────────────
def buy(db: Session, account_id: int, stock_code: str, shares: float,
        price: Optional[float] = None, note: str = "") -> dict:
    """
    买入：shares 必须是 LOT_SIZE 的倍数；未提供 price 则用最新收盘价。
    校验：股票存在、现金充足。
    （ownership 校验在路由层做）
    """
    acct = get_account(db, account_id)

    stock = db.query(Stock).filter_by(code=stock_code).first()
    if not stock:
        raise ValueError(f"股票 {stock_code} 不存在")

    lot = _lot_size()
    shares = int(shares)
    if shares <= 0:
        raise ValueError("买入股数必须大于 0")
    if lot > 1 and shares % lot != 0:
        raise ValueError(f"买入股数必须是 {lot} 的倍数（当前 {shares}）")

    px = price if (price and price > 0) else _get_latest_price(db, stock_code)
    if not px or px <= 0:
        raise ValueError(f"股票 {stock_code} 暂无可用价格")

    amount = round(px * shares, 2)
    fee    = _calc_fee(amount)
    cost   = round(amount + fee, 2)

    if acct.cash_balance < cost:
        raise ValueError(
            f"现金不足：需 {cost:,.2f} 元（含手续费 {fee:.2f}），"
            f"当前余额 {acct.cash_balance:,.2f}"
        )

    # 更新或新建持仓（加权平均成本）
    pos = (
        db.query(PaperPosition)
        .filter_by(account_id=acct.id, stock_code=stock_code)
        .first()
    )
    if pos and pos.shares > 0:
        total_cost = pos.avg_cost * pos.shares + cost
        total_sh   = pos.shares + shares
        pos.avg_cost  = round(total_cost / total_sh, 4)
        pos.shares    = total_sh
        pos.updated_at = datetime.utcnow()
    else:
        if not pos:
            pos = PaperPosition(account_id=acct.id, stock_code=stock_code)
            db.add(pos)
        pos.shares    = shares
        pos.avg_cost  = round(cost / shares, 4)   # 含手续费的实际成本价
        pos.opened_at = datetime.utcnow()
        pos.updated_at = datetime.utcnow()

    # 扣现金
    acct.cash_balance = round(acct.cash_balance - cost, 2)

    # 记流水
    tx = PaperTransaction(
        account_id=acct.id,
        stock_code=stock_code,
        stock_name=stock.name,
        side="BUY",
        shares=shares,
        price=px,
        amount=amount,
        fee=fee,
        note=note,
    )
    db.add(tx)
    db.commit()
    db.refresh(tx)

    return {
        "message":       f"买入成功 {stock.name}({stock_code}) {shares} 股 @ {px}",
        "transaction":   _tx_dict(tx),
        "cash_balance":  acct.cash_balance,
    }


def sell(db: Session, account_id: int, stock_code: str, shares: float,
         price: Optional[float] = None, note: str = "") -> dict:
    """卖出：shares 必须 ≤ 持仓；LOT_SIZE 倍数（除非清仓）。
    （ownership 校验在路由层做）"""
    acct = get_account(db, account_id)
    stock = db.query(Stock).filter_by(code=stock_code).first()
    if not stock:
        raise ValueError(f"股票 {stock_code} 不存在")

    pos = (
        db.query(PaperPosition)
        .filter_by(account_id=acct.id, stock_code=stock_code)
        .first()
    )
    if not pos or pos.shares <= 0:
        raise ValueError(f"未持有 {stock_code}")

    shares = int(shares)
    if shares <= 0:
        raise ValueError("卖出股数必须大于 0")
    if shares > pos.shares:
        raise ValueError(f"卖出股数 {shares} 超过持仓 {int(pos.shares)}")
    # 非清仓时必须 LOT_SIZE 股倍数
    lot = _lot_size()
    if lot > 1 and shares != pos.shares and shares % lot != 0:
        raise ValueError(f"卖出股数必须是 {lot} 的倍数，或全部清仓")

    px = price if (price and price > 0) else _get_latest_price(db, stock_code)
    if not px or px <= 0:
        raise ValueError(f"股票 {stock_code} 暂无可用价格")

    amount  = round(px * shares, 2)
    fee     = _calc_fee(amount)
    income  = round(amount - fee, 2)
    realized = round((px - pos.avg_cost) * shares - fee, 2)

    # 更新持仓（卖出不动 avg_cost，清仓时删除）
    pos.shares = pos.shares - shares
    pos.updated_at = datetime.utcnow()
    if pos.shares <= 0:
        db.delete(pos)

    acct.cash_balance = round(acct.cash_balance + income, 2)

    tx = PaperTransaction(
        account_id=acct.id,
        stock_code=stock_code,
        stock_name=stock.name,
        side="SELL",
        shares=shares,
        price=px,
        amount=amount,
        fee=fee,
        realized_pnl=realized,
        note=note,
    )
    db.add(tx)
    db.commit()
    db.refresh(tx)

    return {
        "message":       f"卖出成功 {stock.name}({stock_code}) {shares} 股 @ {px}，"
                         f"盈亏 {realized:+.2f} 元",
        "transaction":   _tx_dict(tx),
        "cash_balance":  acct.cash_balance,
        "realized_pnl":  realized,
    }


def list_transactions(db: Session, account_id: int, limit: int = 200) -> list:
    """列出某账户的最近流水（ownership 校验在路由层做）"""
    q = (
        db.query(PaperTransaction)
        .filter_by(account_id=account_id)
        .order_by(desc(PaperTransaction.trade_time))
        .limit(limit)
    )
    return [_tx_dict(t) for t in q.all()]


def _tx_dict(t: PaperTransaction) -> dict:
    return {
        "id":           t.id,
        "stock_code":   t.stock_code,
        "stock_name":   t.stock_name,
        "side":         t.side,
        "shares":       int(t.shares),
        "price":        round(t.price, 3),
        "amount":       round(t.amount, 2),
        "fee":          round(t.fee, 2),
        "realized_pnl": round(t.realized_pnl, 2) if t.realized_pnl is not None else None,
        "trade_time":   str(t.trade_time),
        "note":         t.note,
    }
