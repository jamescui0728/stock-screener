"""
自选股 EMA20 跟随标签（与长期 / 短期信号无关的第三套规则）。

规则（用户口述，逐条落到代码）：
  1. 持有   —— 跟随 EMA20，线上拿住、线下离场
  2. 可跟进 —— 价格站稳 EMA20 且成交量同步放大，才加仓
  3. 止盈   —— 涨 30% 出一部分，涨 70% 再出一部分；跌破均线则全部清仓
  4. 止损   —— 收盘价跌破 EMA20，第二天无论如何都走

与 signal_engine / short_signal_engine 的关系：**完全独立，互不读写**。
这套规则不打分、不进 composite，只输出一个标签，落在自选股页面。
放在独立模块而不是塞进 short_signal_engine.py（已 1417 行），因为它既不共享
权重体系也不共享阈值体系。

两个口径上的取舍（都是有意为之，不是疏漏）：

* **一律用日收盘价判定**。用户原话里「跌破下一秒就走」是盘中即时动作，但
  price_data 只有日线、没有分钟级数据，盘中判定在当前数据层做不到。因此统一按
  规则 3 的「收盘价跌破」口径，标签的含义是"按最近一个收盘价，此刻应处于什么状态"。

* **放量比较的是「当日量 vs 前 20 日均量」，均量不含当日**。若把当日算进均值，
  放量当天会把自己的分母抬高、稀释掉尖峰，1.5 倍门槛会明显偏钝。

⚠️ 本模块要同时面对**三套价格口径**，混算过一次就出过 +17022% 的荒谬涨幅：

  price_data.close         后复权 hfq（fetcher 用 adjust="hfq"）
  paper_positions.avg_cost 不复权真实成交价（pt_buy 走 _get_latest_price 拿的 sina 价）
  页面要展示给人看的         前复权 qfq

**页面上一律用前复权**。后复权值对人没有意义（万科实测显示 526 元），跟旁边的
真实成交价 3.11 摆在一起会让人以为程序坏了。

前复权换算（`_qfq_factor`）：

    factor = 今日不复权真实价 / 今日后复权收盘
    qfq 序列 = hfq 序列 × factor          → 末值恰好等于真实市价

这是前复权的标准定义（以最新一日为锚）。因子是**正数常量**，整条序列同比例缩放，
所以「站上/跌破均线」的判定结果与用 hfq 完全一致 —— 换算只影响显示，不影响纪律。

因子成立的前提是**两个价格取自同一交易日**。K 线落后于实时价时（行情没更新），
factor 会把这段时间的涨跌也吸收进去，算出来的"前复权价"是错的。因此
`_qfq_factor` 要求 `raw_price_date == trade_date`，对不上就不换算、
`price_basis` 标为 "hfq"，前端据此只显示百分比、不显示绝对价格。

同理，「较成本涨幅」用不复权实时价与 avg_cost 比（两者同为真实成交价口径）。
这里复用 paper_trade 的实时价缓存（`_PRICE_CACHE`），它只会写入真正拿到的 sina
不复权价，DB 兜底那条路径不写缓存 —— 所以「缓存里有」就等于「这是可信的不复权价」。

缓存拿不到时（冷缓存 / sina 挂了）**宁可不出止盈标签、不出前复权价**，
而不是退回后复权价硬算一个错数字。
"""
import logging
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from config import settings
from models.models import PaperAccount, PaperPosition, PriceData, Stock

logger = logging.getLogger(__name__)

# ── 标签取值 ──────────────────────────────────────────────
TAG_FOLLOW      = "FOLLOW"       # 可跟进：线上 + 放量
TAG_HOLD        = "HOLD"         # 持有：线上，未放量、未到止盈档
TAG_TAKE_PROFIT = "TAKE_PROFIT"  # 止盈：线上且涨幅到档（tp_level 区分 1 / 2）
TAG_STOP_LOSS   = "STOP_LOSS"    # 止损：收盘跌破 EMA20
TAG_NO_DATA     = "NO_DATA"      # 数据不足：K 线不够算 EMA

# 一次性拉多少天的历史。EMA 用前 period 根 SMA 作种子递推，历史越长越收敛；
# 400 天（约 270 根交易日）对 20 日 EMA 早已充分收敛，同时把行数控制在可接受范围。
_LOOKBACK_DAYS = 400


def compute_ema(closes: list, period: int) -> Optional[float]:
    """
    指数移动平均。种子 = 前 period 根的 SMA，其后 ema = c*α + ema_prev*(1-α)，α = 2/(period+1)。

    不足 period 根返回 None。恰好 period 根时退化为 SMA（种子本身），这是 EMA 的
    标准定义，不是 bug —— 但此时它没有"指数加权"的意义，调用方应保证历史足够。
    """
    if not closes or len(closes) < period:
        return None
    alpha = 2.0 / (period + 1)
    ema = sum(closes[:period]) / period
    for c in closes[period:]:
        ema = c * alpha + ema * (1 - alpha)
    return ema


def compute_volume_ratio(volumes: list, period: int) -> Optional[float]:
    """
    当日量 / 前 period 日均量（均量**不含当日**，见模块 docstring）。
    需要 period + 1 根数据；不足或均量为 0 返回 None。
    """
    if not volumes or len(volumes) < period + 1:
        return None
    base = volumes[-(period + 1):-1]
    avg = sum(base) / len(base)
    if avg <= 0:
        return None
    return volumes[-1] / avg


def load_ohlc_series(db: Session, codes: list) -> dict:
    """
    批量拉完整 K 线（开高低收 + 量），一次查询解决，避免按股 N+1。

    先取这批股票的最新交易日，再以「最新交易日 - _LOOKBACK_DAYS」为下界过滤。
    用最新交易日而不是今天，是因为 price_data 可能落后于现实日期（数据未更新时），
    按今天回溯会把整段历史筛空。

    返回 {code: [(trade_date, open, high, low, close, volume), ...]}，按日期升序。

    **只要求 close 非空**，开高低可能为 None。EMA / 量比只用收盘与量，不该因为
    缺一个最高价就整条丢掉（那会让纪律标签莫名其妙变成"数据不足"）。
    需要完整 OHLC 的是缺口判定，由 engines/gap.py 在入口自行校验并截取。
    """
    if not codes:
        return {}

    latest = (
        db.query(func.max(PriceData.trade_date))
        .filter(PriceData.stock_code.in_(codes))
        .scalar()
    )
    if latest is None:
        return {}

    rows = (
        db.query(PriceData.stock_code, PriceData.trade_date,
                 PriceData.open, PriceData.high, PriceData.low,
                 PriceData.close, PriceData.volume)
        .filter(
            PriceData.stock_code.in_(codes),
            PriceData.trade_date >= latest - timedelta(days=_LOOKBACK_DAYS),
        )
        .order_by(PriceData.stock_code, PriceData.trade_date)
        .all()
    )

    series: dict = {}
    for code, dt, o, h, l, c, v in rows:
        if c is None:
            continue          # 缺收盘价的行直接丢，不用 0 顶替（会把 EMA 拉歪）
        series.setdefault(code, []).append((dt, o, h, l, c, v))
    return series


def load_price_series(db: Session, codes: list) -> dict:
    """
    load_ohlc_series 的投影：{code: [(trade_date, close, volume), ...]}。

    EMA / 量比只需要收盘和量。保留这个薄封装，是为了让既有调用方
    （compute_watch_tags 等）不必关心 OHLC，同时全市场扫描只查一次库。
    """
    return {
        code: [(b[0], b[4], b[5]) for b in bars]
        for code, bars in load_ohlc_series(db, codes).items()
    }


def load_cost_basis(db: Session, user_id: int, account_id: Optional[int] = None) -> dict:
    """
    取模拟盘持仓成本作为止盈涨幅的起算价：{stock_code: avg_cost}。

    account_id 不传时用该用户最早创建的账户（与 paper_trade 的"默认账户"口径一致）。
    只读，账户不存在就返回空 dict —— 不会顺手把账户建出来。
    """
    q = db.query(PaperAccount).filter(PaperAccount.user_id == user_id)
    if account_id is not None:
        q = q.filter(PaperAccount.id == account_id)
    acct = q.order_by(PaperAccount.id.asc()).first()
    if acct is None:
        return {}

    rows = (
        db.query(PaperPosition.stock_code, PaperPosition.avg_cost)
        .filter(PaperPosition.account_id == acct.id, PaperPosition.shares > 0)
        .all()
    )
    return {code: cost for code, cost in rows if cost and cost > 0}


def load_raw_prices(db: Session, codes: list, refresh: bool = False) -> dict:
    """
    取**不复权**实时价（与 avg_cost 同口径），用于止盈涨幅。

    直接读 paper_trade 的 `_PRICE_CACHE`：该缓存只在真正拿到 sina 价时写入，
    DB 后复权兜底那条路径不写缓存，所以命中即可信（见模块 docstring）。

    refresh=False（默认）：只读缓存，**不发任何网络请求**，冷缓存就返回空。
      —— 自选股页面是高频打开的列表页，不该因为拉 sina 卡住 14 秒。
    refresh=True：先跑一次并行预热（复用 paper_trade._warmup_prices_parallel，
      16 线程 / 单只 8s / 整体 14s hard cap），再读缓存。
      注意 hard cap 拦不住已经发出去的请求（worker 线程不可中断，见
      ARCHITECTURE.md §7），实测 15 只冷缓存约 26 秒。

    返回 {code: (不复权价, 交易日字符串)}。
    """
    import time as _time

    from engines.paper_trade import _CACHE_TTL_SEC, _PRICE_CACHE, _warmup_prices_parallel

    if not codes:
        return {}
    if refresh:
        try:
            _warmup_prices_parallel(db, list(codes), per_call_timeout=8)
        except Exception as e:
            # 预热失败不该让整个自选股列表挂掉，降级为"没有成交价"
            logger.warning(f"自选股实时价预热失败，成交价与止盈涨幅将不可用: {e}")

    now = _time.time()
    out = {}
    for code in codes:
        cached = _PRICE_CACHE.get(code)
        if cached and (now - cached[2] < _CACHE_TTL_SEC) and cached[0]:
            out[code] = (cached[0], cached[1])
    return out


def _qfq_factor(
    hfq_last_close: Optional[float],
    hfq_last_date,
    raw_price: Optional[float],
    raw_date: Optional[str],
) -> Optional[float]:
    """
    前复权因子 = 今日不复权真实价 / 今日后复权收盘。

    **两个价格必须是同一交易日的**，否则因子会把这期间的涨跌当成除权处理，
    换算出来的价格是错的（K 线陈旧时尤其明显）。日期对不上返回 None。
    """
    if not hfq_last_close or not raw_price or hfq_last_close <= 0:
        return None
    if not raw_date or hfq_last_date is None:
        return None
    # sina 可能返回 "2026-08-27" 或 "2026-08-27 00:00:00"，统一截前 10 位比较
    if str(raw_date)[:10] != str(hfq_last_date)[:10]:
        return None
    return raw_price / hfq_last_close


def classify_watch_tag(
    close: Optional[float],
    ema: Optional[float],
    vol_ratio: Optional[float],
    gain_pct: Optional[float],
    cost: Optional[float] = None,
    show_abs: bool = True,
) -> dict:
    """
    判定标签。优先级（高 → 低）：

        数据不足  >  止损  >  止盈②  >  止盈①  >  可跟进  >  持有

    止损排在止盈之前，依据是规则 3 的「跌破均线则全部清仓」——
    已经涨了 50% 但今天跌破均线，该走还是得走，不能因为盈利就压住离场信号。

    close / ema 由调用方换算成**前复权**后传进来（与页面上的成交价同口径）；
    gain_pct 用**不复权**实时价与 avg_cost 算好后传进来，两者不在本函数里混算。
    gain_pct 为 None（不在持仓 / 拿不到不复权价）时止盈档直接跳过，
    只在 持有/可跟进/止损 之间判。

    show_abs=False 时理由文案里不出现绝对价格，只讲百分比 —— 用于换算不出前复权价
    的场景（拿不到实时价 / K 线与实时价不同日），避免把后复权数字写进给人看的文案。
    """
    if close is None or ema is None:
        return {"tag": TAG_NO_DATA, "tp_level": None,
                "reason": "K 线不足，无法计算 EMA20"}

    if close < ema:
        below_pct = (ema - close) / ema * 100
        detail = (f"收盘 {close:.2f} 跌破 EMA20 {ema:.2f}（低 {below_pct:.1f}%）"
                  if show_abs else f"收盘跌破 EMA20（低 {below_pct:.1f}%）")
        return {"tag": TAG_STOP_LOSS, "tp_level": None,
                "reason": f"{detail}，次日离场"}

    above_pct = (close - ema) / ema * 100
    cost_txt = f"成本 {cost:.2f}，" if cost else ""

    if gain_pct is not None and gain_pct >= settings.WATCH_TAKE_PROFIT_2_PCT:
        return {
            "tag": TAG_TAKE_PROFIT, "tp_level": 2,
            "reason": f"{cost_txt}现涨 {gain_pct:.1f}%"
                      f"（≥{settings.WATCH_TAKE_PROFIT_2_PCT:.0f}%），第二档减仓",
        }
    if gain_pct is not None and gain_pct >= settings.WATCH_TAKE_PROFIT_1_PCT:
        return {
            "tag": TAG_TAKE_PROFIT, "tp_level": 1,
            "reason": f"{cost_txt}现涨 {gain_pct:.1f}%"
                      f"（≥{settings.WATCH_TAKE_PROFIT_1_PCT:.0f}%），第一档减仓",
        }

    if vol_ratio is not None and vol_ratio >= settings.WATCH_VOL_SPIKE_RATIO:
        return {
            "tag": TAG_FOLLOW, "tp_level": None,
            "reason": f"站稳 EMA20（高 {above_pct:.1f}%）且放量 "
                      f"{vol_ratio:.1f}×，可跟进",
        }

    vol_txt = f"量能 {vol_ratio:.1f}×" if vol_ratio is not None else "量能数据不足"
    return {
        "tag": TAG_HOLD, "tp_level": None,
        "reason": f"站稳 EMA20（高 {above_pct:.1f}%），{vol_txt}，线上拿住",
    }


def compute_watch_tags(
    db: Session, codes: list, user_id: int,
    account_id: Optional[int] = None,
    refresh_price: bool = False,
) -> dict:
    """
    批量算一组股票的自选股标签。返回 {code: {...}}，每项含：

        tag / tp_level / tag_reason / close / ema20 / above_ema / vol_ratio
        / cost / raw_price / gain_pct / gain_available / trade_date

    close/ema20 是后复权口径，raw_price/cost/gain_pct 是不复权口径，两者在返回值里
    分开放，前端不要拿 close 去和 cost 比（见模块 docstring）。

    没有任何价格数据的股票也会出现在结果里，标签为 NO_DATA —— 让前端能明确
    显示"数据不足"，而不是悄悄少一行。
    """
    period = int(settings.WATCH_EMA_PERIOD)
    series = load_price_series(db, codes)
    costs = load_cost_basis(db, user_id, account_id)
    # 全部自选股都取实时价：既用于止盈涨幅，也直接作为页面上的"最新成交价"展示。
    # （早先只给有持仓的票取，加了成交价列之后没持仓的票也要显示，故放开）
    raw_prices = load_raw_prices(db, codes, refresh_price)

    out = {}
    for code in codes:
        points = series.get(code) or []
        closes = [p[1] for p in points]
        volumes = [p[2] for p in points if p[2] is not None]

        # 量比单独判断长度：收盘价够算 EMA 不代表成交量列也齐（历史数据可能缺 volume）
        vol_ratio = compute_volume_ratio(volumes, period) if len(volumes) == len(points) else None

        cost = costs.get(code)
        raw, raw_date = raw_prices.get(code) or (None, None)
        gain_pct = ((raw - cost) / cost * 100) if (cost and raw) else None

        # 换成前复权再算 EMA：因子是正数常量，整条序列同比例缩放，
        # 判定结果与用后复权完全一致，只是数字变成人能看懂的量级。
        factor = _qfq_factor(closes[-1] if closes else None,
                             points[-1][0] if points else None, raw, raw_date)
        if factor:
            closes = [c * factor for c in closes]
        price_basis = "qfq" if factor else "hfq"

        ema = compute_ema(closes, period)
        close = closes[-1] if closes else None

        verdict = classify_watch_tag(close, ema, vol_ratio, gain_pct, cost,
                                     show_abs=bool(factor))
        out[code] = {
            "tag":         verdict["tag"],
            "tp_level":    verdict["tp_level"],
            "tag_reason":  verdict["reason"],
            # ── 均线（price_basis="qfq" 时已换算成前复权，与成交价同口径）──
            "close":       round(close, 2) if close is not None else None,
            "ema20":       round(ema, 2) if ema is not None else None,
            "above_ema":   (close >= ema) if (close is not None and ema is not None) else None,
            # 距均线百分比：比值，与复权口径无关，任何情况下都可以放心显示
            "above_ema_pct": round((close - ema) / ema * 100, 2)
                             if (close is not None and ema) else None,
            # "qfq"=已换成前复权可直接展示；"hfq"=换算不成立（拿不到实时价或与 K 线
            # 不同日），close/ema20 仍是后复权值，前端只能显示 above_ema_pct
            "price_basis": price_basis,
            "kline_date":  points[-1][0] if points else None,
            "vol_ratio":   round(vol_ratio, 2) if vol_ratio is not None else None,
            "trade_date":  str(points[-1][0]) if points else None,
            # ── 不复权口径（页面展示的"最新成交价" + 止盈涨幅用）──
            "cost":           round(cost, 4) if cost else None,
            "raw_price":      round(raw, 2) if raw else None,
            "raw_price_date": raw_date,
            "gain_pct":       round(gain_pct, 2) if gain_pct is not None else None,
            # 有成本价但拿不到不复权实时价 → 止盈档本轮不可判，前端可据此提示
            "gain_available": bool(cost and raw),
        }
    return out


# ════════════════════════════════════════════════════════════════
# 全市场扫描（落库供公司筛选页 SQL 过滤 / 排序）
# ════════════════════════════════════════════════════════════════
def scan_market(db: Session, commit: bool = True, limit: Optional[int] = None) -> dict:
    """
    给全市场 active 股票算 EMA20 纪律标签 + MACD 零轴上方回踩金叉，结果写回 stocks 表。

    与 compute_watch_tags 的差别（**不是疏漏，是数据约束**）：

      * **没有止盈档**。止盈要 paper_positions 里的成本价，只有持仓股才有，
        全市场没有这个概念，因此只出 可跟进/持有/止损/数据不足 四种。
      * **不做前复权换算、不出绝对价格**。换算要逐只打 sina 拿不复权实时价，
        15 只就要 26 秒，5500 只约 45 分钟，不可行。所以只落 above_ema_pct
        （比值，与复权口径无关，可以放心展示）。

    自选股页面（十几只）仍走 compute_watch_tags，有止盈档也有前复权价。

    实测：2966 只有效股票，拉数 + 计算约 20 秒（MACD 的逐日 DIF 是主要开销）。
    """
    from engines.gap import evaluate_gap_signal
    from engines.macd import evaluate_macd

    period = int(settings.WATCH_EMA_PERIOD)

    q = db.query(Stock).filter(Stock.is_active == True)   # noqa: E712
    if limit:
        q = q.limit(limit)
    stocks = q.all()
    codes = [s.code for s in stocks]
    # 一次查完整 K 线：EMA/量比只要收盘与量，缺口判定还要开高低，共用同一次查询
    bars_map = load_ohlc_series(db, codes)

    now = datetime.utcnow()
    counts = defaultdict(int)
    gap_counts = defaultdict(int)
    macd_hits = 0

    for st in stocks:
        bars = bars_map.get(st.code) or []
        points = [(b[0], b[4], b[5]) for b in bars]
        closes = [p[1] for p in points]
        volumes = [p[2] for p in points if p[2] is not None]

        ema = compute_ema(closes, period)
        vol_ratio = (compute_volume_ratio(volumes, period)
                     if len(volumes) == len(points) else None)
        close = closes[-1] if closes else None

        # 全市场没有成本价，gain_pct 恒为 None → 止盈档自然跳过。
        # show_abs=False：这里的 close/ema 是后复权值，绝不能写进给人看的文案。
        verdict = classify_watch_tag(close, ema, vol_ratio, None, None, show_abs=False)
        macd = evaluate_macd(closes)
        gap = evaluate_gap_signal(bars)

        st.watch_tag           = verdict["tag"]
        st.watch_tag_reason    = verdict["reason"]
        st.watch_above_ema_pct = (round((close - ema) / ema * 100, 2)
                                  if (close is not None and ema) else None)
        st.watch_kline_date    = points[-1][0] if points else None
        st.macd_dif            = macd["macd_dif"]
        st.macd_dea            = macd["macd_dea"]
        st.macd_hist           = macd["macd_hist"]
        st.macd_cross_up       = macd["macd_cross_up"]
        st.macd_reason         = macd["macd_reason"]
        st.gap_signal          = gap["gap_signal"]
        st.gap_days_since      = gap["gap_days_since"]
        st.gap_confirm_date    = gap["gap_confirm_date"]
        st.gap_lower           = gap["gap_lower"]
        st.gap_upper           = gap["gap_upper"]
        st.gap_reason          = gap["gap_reason"]
        st.watch_updated       = now

        counts[verdict["tag"]] += 1
        if macd["macd_cross_up"]:
            macd_hits += 1
        if gap["gap_signal"]:
            gap_counts[gap["gap_signal"]] += 1

    if commit:
        db.commit()

    result = {
        "scanned":       len(stocks),
        "with_prices":   len(bars_map),
        "tag_counts":    dict(counts),
        "macd_cross_up": macd_hits,
        "gap_counts":    dict(gap_counts),
        "updated_at":    now.isoformat(),
    }
    logger.info(
        f"全市场标签扫描完成：{len(stocks)} 只，标签分布 {dict(counts)}，"
        f"MACD 零轴上方回踩金叉 {macd_hits} 只，缺口信号 {dict(gap_counts)}"
    )
    return result
