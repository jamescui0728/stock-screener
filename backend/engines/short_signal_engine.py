"""
短期买卖信号引擎（v200 新增）

与长期信号（signal_engine.py）并行存在、互不干扰：
- 长期信号：基本面 + 估值 + 舆情 + 宏观；hold 6-12 个月；适合价值投资
- 短期信号：动量 + 量价 + 宏观 + 科技板块 + 新闻热度；hold 1-2 周；适合波段

5 维评分（合计 100，权重见 config.py 的 SHORT_*_WEIGHT）：
  1. 动量 (momentum)      35%  ─ 5/20/60 日收益、相对 MA20/60、RSI(14)
  2. 量价关系 (volprice)   15%  ─ 涨且放量 / 跌且放量 / 涨且缩量
  3. 宏观环境 (macro)     25%  ─ 复用 signal_engine.score_macro()
  4. 科技板块 (tech)      15%  ─ 行业白名单 + 行业评分
  5. 新闻热度 (news_heat)  10%  ─ 近 7 天新闻条数 × 平均情感

5 等级阈值（与长期独立）：
  composite ≥ 75 + 动量为正  →  STRONG_BUY
  composite ≥ 60             →  BUY
  composite ≥ 45             →  HOLD
  composite ≥ 30             →  SELL
  composite < 30 或 5日跌幅 > 10%  →  STRONG_SELL
"""
import logging
from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Optional

import numpy as np
from sqlalchemy.orm import Session
from sqlalchemy import desc

from config import settings
from models.models import PriceData, Stock, NewsItem, Industry, FinancialData
from engines.signal_engine import score_macro

logger = logging.getLogger(__name__)
BENCHMARK_CODE = "IDX_000300"   # 沪深300，存放在 price_data 中


def compute_recent_price_cache(db: Session, as_of_date=None, lookback_days: int = 180) -> dict:
    """
    批量预加载最近价格窗口，供短期信号的 momentum / volprice / relative 复用。
    返回 {stock_code: [(trade_date, close, volume), ...]}，每只股票按日期升序。
    """
    cutoff = as_of_date or datetime.utcnow().date()
    start = cutoff - timedelta(days=lookback_days)
    rows = (
        db.query(PriceData.stock_code, PriceData.trade_date, PriceData.close, PriceData.volume)
        .filter(
            PriceData.trade_date >= start,
            PriceData.trade_date <= cutoff,
            PriceData.stock_code != BENCHMARK_CODE,
        )
        .order_by(PriceData.stock_code, PriceData.trade_date)
        .all()
    )
    cache = defaultdict(list)
    for code, dt, close, volume in rows:
        if close is not None:
            cache[code].append((dt, close, volume))
    return dict(cache)


def compute_stock_returns_from_price_cache(price_cache: dict) -> dict:
    """从 compute_recent_price_cache 的结果中批量计算个股 5/20 日收益。"""
    result = {}
    for code, points in (price_cache or {}).items():
        if len(points) < 21:
            continue
        closes = [p[1] for p in points if p[1]]
        if len(closes) < 21:
            continue
        try:
            result[code] = (closes[-1] / closes[-6] - 1, closes[-1] / closes[-21] - 1)
        except (ZeroDivisionError, TypeError):
            continue
    return result


# ════════════════════════════════════════════════════════════════
# 1. 动量评分（0-100）
# ════════════════════════════════════════════════════════════════
def score_momentum(
    db: Session, stock_code: str, as_of_date=None,
    _cached_prices: Optional[dict] = None,
) -> Optional[dict]:
    """
    基于近 60 个交易日的价格序列计算动量分。
    返回 {"score": 0-100, "ret_5d": ..., "ret_20d": ..., "ret_60d": ...,
          "above_ma20": bool, "above_ma60": bool, "rsi14": float}
    或 None（数据不足）。
    """
    if _cached_prices is not None:
        points = (_cached_prices.get(stock_code) or [])[-80:]
        if len(points) < 21:
            return None
        closes = [p[1] for p in points if p[1]]
    else:
        cutoff = as_of_date or datetime.utcnow().date()
        # 拉最近 80 天的 K 线（保证够算 60 日动量 + RSI）
        rows = (
            db.query(PriceData)
            .filter(PriceData.stock_code == stock_code, PriceData.trade_date <= cutoff)
            .order_by(desc(PriceData.trade_date))
            .limit(80)
            .all()
        )
        if len(rows) < 21:
            return None   # 不够算 20 日动量
        # 倒序变正序（旧 → 新）
        closes = [r.close for r in reversed(rows) if r.close]
    if len(closes) < 21:
        return None

    cur = closes[-1]
    n = len(closes)

    # 区间收益
    ret_5d  = (cur / closes[-6]  - 1) if n >= 6  else None
    ret_20d = (cur / closes[-21] - 1) if n >= 21 else None
    ret_60d = (cur / closes[-61] - 1) if n >= 61 else None

    # 均线
    ma20 = sum(closes[-20:]) / 20
    ma60 = sum(closes[-60:]) / 60 if n >= 60 else None
    above_ma20 = cur > ma20
    above_ma60 = (cur > ma60) if ma60 else None

    # RSI(14)
    rsi14 = _calc_rsi(closes, period=14)

    # ── 评分（v201：翻转为短期反转模型）──
    # 诊断（run 45, n=105k）：原"追涨"模型 IC=-0.114，最高分桶 win 仅 39.8%
    # A 股短期典型反转：涨多回吐，跌多反弹。把方向翻过来：
    #   涨多 → 扣分；跌多 → 加分（小幅）；RSI 超卖 → 大加分；过热 → 大扣分
    # 保留 60 日趋势的正向（中期动量仍有效；只翻 5/20 日的短期项）
    score = 50.0

    # 5 日收益：翻转（涨多扣分，跌多加分）±10% → ∓12 分
    if ret_5d is not None:
        score -= max(-12, min(12, ret_5d * 120))

    # 20 日收益：翻转，±20% → ∓10 分（短期反转，但权重小一些）
    if ret_20d is not None:
        score -= max(-10, min(10, ret_20d * 50))

    # 60 日收益：保持正向，±30% → ±10 分
    # （A 股的中期动量 60d 仍 weakly positive；只翻 5/20d 的短期反转项）
    if ret_60d is not None:
        score += max(-10, min(10, ret_60d * 33))

    # MA20/MA60：移除（IC 分析里这两项贡献已并入收益项，再加分会双重计算）

    # RSI：放大反转效应
    #   超卖 (<30) → +15  （原 +3，黄金回调机会）
    #   过热 (>70) → -10  （原 >75 时 -5；放宽阈值并加大力度）
    #   中性区不加减分
    if rsi14 is not None:
        if rsi14 < 30:
            score += 15
        elif rsi14 < 40:
            score += 5
        elif rsi14 > 70:
            score -= 10
        elif rsi14 > 60:
            score -= 3

    return {
        "score":      round(max(0, min(100, score)), 2),
        "ret_5d":     round(ret_5d  * 100, 2) if ret_5d  is not None else None,
        "ret_20d":    round(ret_20d * 100, 2) if ret_20d is not None else None,
        "ret_60d":    round(ret_60d * 100, 2) if ret_60d is not None else None,
        "above_ma20": above_ma20,
        "above_ma60": above_ma60,
        "rsi14":      round(rsi14, 1) if rsi14 is not None else None,
    }


def _calc_rsi(closes: list, period: int = 14) -> Optional[float]:
    """Wilder's RSI"""
    if len(closes) < period + 1:
        return None
    gains, losses = 0.0, 0.0
    for i in range(-period, 0):
        diff = closes[i] - closes[i - 1]
        if diff >= 0:
            gains += diff
        else:
            losses += -diff
    if losses == 0:
        return 100.0
    rs = (gains / period) / (losses / period)
    return 100 - 100 / (1 + rs)


# ════════════════════════════════════════════════════════════════
# 2. 量价关系评分（0-100）
# ════════════════════════════════════════════════════════════════
def score_volprice(
    db: Session, stock_code: str, as_of_date=None,
    _cached_prices: Optional[dict] = None,
) -> Optional[dict]:
    """
    v201：翻转为反转量价（诊断 IC=-0.122，最强反向项）
    涨且放量 → 分布形态（顶部资金出货），扣分
    跌且放量 → 恐慌底，加分
    涨且缩量 → 上涨乏力，扣分
    跌且缩量 → 抛压减弱，加分
    """
    if _cached_prices is not None:
        points = (_cached_prices.get(stock_code) or [])[-25:]
        if len(points) < 21:
            return None
        closes = [p[1] for p in points if p[1]]
        volumes = [p[2] for p in points if p[2]]
    else:
        cutoff = as_of_date or datetime.utcnow().date()
        rows = (
            db.query(PriceData)
            .filter(PriceData.stock_code == stock_code, PriceData.trade_date <= cutoff)
            .order_by(desc(PriceData.trade_date))
            .limit(25)
            .all()
        )
        if len(rows) < 21:
            return None
        closes  = [r.close  for r in reversed(rows) if r.close]
        volumes = [r.volume for r in reversed(rows) if r.volume]
    if len(closes) < 21 or len(volumes) < 21:
        return None

    # 近 5 日 vs 20 日均量
    vol_5d  = sum(volumes[-5:])  / 5
    vol_20d = sum(volumes[-20:]) / 20
    vol_ratio = vol_5d / vol_20d if vol_20d > 0 else 1.0

    # 近 5 日涨幅
    ret_5d = closes[-1] / closes[-6] - 1 if len(closes) >= 6 else 0

    # v201 翻转评分逻辑（短期反转）
    score = 50.0
    if ret_5d > 0.02:        # 明显涨
        if vol_ratio > 1.3:  # 涨且放量 = 分布形态 → 扣分
            score = 25 - min(15, (vol_ratio - 1.3) * 30)
        elif vol_ratio > 0.9:
            score = 40
        else:                # 涨但缩量 = 上涨乏力 → 也扣分
            score = 35
    elif ret_5d < -0.02:     # 明显跌
        if vol_ratio > 1.3:  # 跌且放量 = 恐慌底 → 加分
            score = 75 + min(15, (vol_ratio - 1.3) * 30)
        elif vol_ratio < 0.8:# 跌且缩量 = 抛压减弱 → 加分
            score = 65
        else:
            score = 60
    # else: 横盘（±2% 内）→ 维持 50

    return {
        "score":     round(max(0, min(100, score)), 2),
        "vol_ratio": round(vol_ratio, 2),
        "ret_5d":    round(ret_5d * 100, 2),
    }


# ════════════════════════════════════════════════════════════════
# 2.5 市场趋势过滤（不参与 composite，仅作为 BUY 风控门槛）
# ════════════════════════════════════════════════════════════════
def score_market_trend(db: Session, as_of_date=None) -> dict:
    """
    沪深300短线趋势。短期反转模型在大盘 20 日趋势转暖时胜率更稳定；
    数据缺失时返回 pass=True，避免因基准缺数据误杀所有信号。
    """
    cutoff = as_of_date or datetime.utcnow().date()
    rows = (
        db.query(PriceData)
        .filter(PriceData.stock_code == BENCHMARK_CODE, PriceData.trade_date <= cutoff)
        .order_by(desc(PriceData.trade_date))
        .limit(61)
        .all()
    )
    closes = [r.close for r in reversed(rows) if r.close]
    if len(closes) < 21:
        return {"pass": True, "ret_20d": None, "ret_60d": None}

    ret_20d = closes[-1] / closes[-21] - 1
    ret_60d = (closes[-1] / closes[-61] - 1) if len(closes) >= 61 else None
    return {
        "pass":    ret_20d * 100 >= settings.SHORT_MARKET_TREND_MIN_20D,
        "ret_20d": round(ret_20d * 100, 2),
        "ret_60d": round(ret_60d * 100, 2) if ret_60d is not None else None,
    }


# ════════════════════════════════════════════════════════════════
# 3. 科技板块评分（0-100）
# ════════════════════════════════════════════════════════════════
def score_tech_sector(
    db: Session, stock_code: str,
    _cached_stock: Optional[Stock] = None,
    _cached_industries: Optional[dict] = None,
) -> dict:
    """
    白名单内 → 70 起步，再叠加行业评分加分（最高 +30）
    白名单外 → 30 起步，行业评分高（>=70）才加分

    缓存参数（批量调用时传入，避免 N+1 查询）：
    - _cached_stock: 已加载的 Stock 对象，省一次 Stock 表查询
    - _cached_industries: {industry_code: Industry} 字典，省一次 Industry 表查询
    """
    stock = _cached_stock if _cached_stock is not None else \
            db.query(Stock).filter_by(code=stock_code).first()
    if not stock or not stock.industry_code:
        return {"score": 30.0, "is_tech": False}

    # v201：去掉白名单 base bonus（诊断显示 tech bonus 是负 IC），
    # 直接用行业评分线性映射到 0-100
    if _cached_industries is not None:
        ind = _cached_industries.get(stock.industry_code)
    else:
        ind = db.query(Industry).filter_by(code=stock.industry_code).first()
    ind_score = (ind.total_score or 50.0) if ind else 50.0

    # 行业评分（通常 0-100 范围）直接当作 tech score；
    # 不再"科技板块 +40 起步"——那是 v200 的负 IC 主因
    is_tech = stock.industry_code in settings.TECH_INDUSTRIES

    return {
        "score":     round(ind_score, 2),
        "is_tech":   is_tech,
        "ind_score": round(ind_score, 2),
    }


# ════════════════════════════════════════════════════════════════
# 4. 行业相对反转评分（v202 新增）
# ════════════════════════════════════════════════════════════════
def compute_industry_returns_at(
    db: Session, as_of_date=None,
    _cached_stock_returns: Optional[dict] = None,
) -> dict:
    """
    一次 bulk query 算出每个行业在 as_of_date 的平均 5/20 日涨幅。

    返回 {industry_code: {'ret_5d': float, 'ret_20d': float, 'n_stocks': int}}
    （只保留 n_stocks >= 3 的行业，单只样本无意义）

    性能：拉所有 active 股票 + 最近 35 天价格 → 一次扫表，Python 里聚合
    在批量场景（generate_all_short_signals / 回测 _run_window）调用一次即可
    """
    cutoff = as_of_date or datetime.utcnow().date()
    # 45 个自然日 ~ 30 个交易日，够算 20 日动量（含节假日 + 部分股票滞后）
    start  = cutoff - timedelta(days=45)

    # 拉股票 → 行业映射
    stock_industry = dict(
        db.query(Stock.code, Stock.industry_code)
        .filter(Stock.is_active == True, Stock.industry_code.isnot(None))
        .all()
    )
    if not stock_industry:
        return {}

    # 每只算自身 5/20 日收益，加进所在行业的 bucket
    ind_buckets = defaultdict(list)
    if _cached_stock_returns is not None:
        for code, (ret_5d, ret_20d) in _cached_stock_returns.items():
            ind_code = stock_industry.get(code)
            if ind_code:
                ind_buckets[ind_code].append((ret_5d, ret_20d))
    else:
        # 一次拉所有 (code, date, close) — 仅按日期范围过滤
        # （IN clause 5000+ 参数会让 SQLite 一秒变 30 秒，所以把过滤放 Python 里）
        rows = (
            db.query(PriceData.stock_code, PriceData.trade_date, PriceData.close)
            .filter(
                PriceData.trade_date >= start,
                PriceData.trade_date <= cutoff,
            )
            .all()
        )

        # 按股票分组（保留时间顺序），只保留 active + 有 industry_code 的
        by_stock = defaultdict(list)
        for code, dt, close in rows:
            if close is not None and code in stock_industry:
                by_stock[code].append((dt, close))

        for code, prices in by_stock.items():
            ind_code = stock_industry.get(code)
            if not ind_code:
                continue
            if len(prices) < 21:
                continue
            prices.sort(key=lambda x: x[0])
            cur = prices[-1][1]
            if cur is None or cur <= 0:
                continue
            try:
                ret_5d  = cur / prices[-6][1]  - 1
                ret_20d = cur / prices[-21][1] - 1
            except (ZeroDivisionError, TypeError):
                continue
            ind_buckets[ind_code].append((ret_5d, ret_20d))

    # 每个行业做均值，至少 3 只才入结果（避免单 outlier 主导）
    result = {}
    for ind_code, lst in ind_buckets.items():
        if len(lst) < 3:
            continue
        avg_5d  = sum(r[0] for r in lst) / len(lst)
        avg_20d = sum(r[1] for r in lst) / len(lst)
        result[ind_code] = {
            "ret_5d":   avg_5d,
            "ret_20d":  avg_20d,
            "n_stocks": len(lst),
        }
    return result


def score_industry_relative(
    db: Session, stock_code: str, as_of_date=None,
    _cached_stock: Optional[Stock] = None,
    _cached_industry_returns: Optional[dict] = None,
    _cached_stock_returns: Optional[dict] = None,
) -> dict:
    """
    行业相对反转评分（0-100）：
    - 跑输行业越多 → 越超卖 → 反弹候选 → 加分
    - 跑赢行业越多 → 涨过头 → 回调风险 → 扣分

    实证依据：A 股个股相对于同行业的 short-term mean reversion 效应明显，
    且比纯绝对收益的反转信号更稳健（剥离了系统性 + 行业 beta）

    _cached_industry_returns: 由 compute_industry_returns_at 预算的 dict
    _cached_stock_returns: 可选，{code: (ret_5d, ret_20d)} 避免重复查询
    """
    if _cached_industry_returns is None:
        # 没缓存就单只算 — 慢，但单只调用时可接受
        _cached_industry_returns = compute_industry_returns_at(db, as_of_date)

    stock = _cached_stock if _cached_stock is not None else \
            db.query(Stock).filter_by(code=stock_code).first()
    if not stock or not stock.industry_code:
        return {"score": 50.0, "rel_5d": None, "rel_20d": None}

    ind = _cached_industry_returns.get(stock.industry_code)
    if not ind:
        return {"score": 50.0, "rel_5d": None, "rel_20d": None}

    # 个股自身的 5/20 日收益（优先用 cache，否则单查）
    if _cached_stock_returns and stock_code in _cached_stock_returns:
        ret_5d, ret_20d = _cached_stock_returns[stock_code]
    else:
        cutoff = as_of_date or datetime.utcnow().date()
        rows = (
            db.query(PriceData)
            .filter(PriceData.stock_code == stock_code, PriceData.trade_date <= cutoff)
            .order_by(desc(PriceData.trade_date))
            .limit(25)
            .all()
        )
        if len(rows) < 21:
            return {"score": 50.0, "rel_5d": None, "rel_20d": None}
        closes = [r.close for r in reversed(rows) if r.close]
        if len(closes) < 21:
            return {"score": 50.0, "rel_5d": None, "rel_20d": None}
        try:
            ret_5d  = closes[-1] / closes[-6]  - 1
            ret_20d = closes[-1] / closes[-21] - 1
        except (ZeroDivisionError, TypeError):
            return {"score": 50.0, "rel_5d": None, "rel_20d": None}

    rel_5d  = ret_5d  - ind["ret_5d"]    # 正 = 跑赢行业；负 = 跑输
    rel_20d = ret_20d - ind["ret_20d"]

    # 反转打分：跑赢扣分、跑输加分（饱和）
    # 5d 维度：±5% rel → ±18 分（短周期权重大）
    # 20d 维度：±15% rel → ±12 分
    score = 50.0
    score -= max(-18, min(18, rel_5d  * 360))
    score -= max(-12, min(12, rel_20d * 80))

    return {
        "score":   round(max(0, min(100, score)), 2),
        "rel_5d":  round(rel_5d  * 100, 2),
        "rel_20d": round(rel_20d * 100, 2),
    }


# ════════════════════════════════════════════════════════════════
# 5. 新闻热度评分（0-100）
# ════════════════════════════════════════════════════════════════
def score_news_heat(db: Session, stock_code: str, as_of_date=None) -> dict:
    """
    近 7 天新闻：条数 × 平均情感
    无新闻 → 中性 50（避免静默股票被一刀切）
    """
    cutoff   = as_of_date or datetime.utcnow().date()
    since    = datetime.combine(cutoff, datetime.min.time()) - timedelta(days=7)

    rows = (
        db.query(NewsItem)
        .filter(
            NewsItem.stock_code == stock_code,
            NewsItem.pub_date >= since,
            NewsItem.pub_date <= datetime.combine(cutoff, datetime.max.time()),
        )
        .all()
    )

    if not rows:
        return {"score": 50.0, "n_news": 0, "avg_sentiment": None}

    n = len(rows)
    sentiments = [r.sentiment_score for r in rows if r.sentiment_score is not None]
    avg_sent = sum(sentiments) / len(sentiments) if sentiments else 0.5

    # 条数：越多越热（>=10 满热）
    heat_factor = min(1.0, n / 10)

    # 评分 = 50 + (情感偏离中性) × 热度
    # 情感 0.7（积极） + 热度 1.0 → 50 + 40 = 90
    # 情感 0.3（消极） + 热度 1.0 → 50 - 40 = 10
    score = 50 + (avg_sent - 0.5) * 80 * heat_factor

    return {
        "score":         round(max(0, min(100, score)), 2),
        "n_news":        n,
        "avg_sentiment": round(avg_sent, 3),
    }


# ════════════════════════════════════════════════════════════════
# 6. 定价权评分（0-100）— 财报维度
# ════════════════════════════════════════════════════════════════
def _gm_publishable_cutoff(as_of_date) -> date:
    """
    财报可见性截止：as_of_date - REPORT_LAG_DAYS。
    一只 2023-12-31 期的年报，要 ~90 天后（≈ 2024-04-01）才公开 → 在那之前的
    backtest check_date 不该看到该期数据，否则就是 look-ahead bias。
    """
    if as_of_date is None:
        as_of_date = datetime.utcnow().date()
    return as_of_date - timedelta(days=settings.REPORT_LAG_DAYS)


def compute_industry_avg_gm(db: Session, as_of_date=None) -> dict:
    """
    批量计算每个行业在 as_of_date 之前可见的最近 ~3 年年报的平均毛利率。
    返回 {industry_code: avg_gross_margin}

    v202f-fix：加入 as_of_date 截断，避免 backtest 看到未来财报（look-ahead bias）。
    """
    # 拉所有有 industry_code 的股票
    stock_ind = dict(
        db.query(Stock.code, Stock.industry_code)
        .filter(Stock.is_active == True, Stock.industry_code.isnot(None))
        .all()
    )
    if not stock_ind:
        return {}

    cutoff_high = _gm_publishable_cutoff(as_of_date)            # 上限：公开可见的最后一期
    cutoff_low  = date(cutoff_high.year - 3, 1, 1)              # 下限：再往前 3 年
    rows = (
        db.query(FinancialData.stock_code, FinancialData.gross_margin)
        .filter(
            FinancialData.report_type == "annual",
            FinancialData.period >= cutoff_low,
            FinancialData.period <= cutoff_high,
            FinancialData.gross_margin.isnot(None),
        )
        .all()
    )

    ind_gms = defaultdict(list)
    for code, gm in rows:
        ind_code = stock_ind.get(code)
        if ind_code and gm is not None:
            ind_gms[ind_code].append(gm)

    return {
        ind: sum(vals) / len(vals)
        for ind, vals in ind_gms.items()
        if len(vals) >= 2  # 至少 2 个数据点才有意义
    }


def score_pricing_power(
    db: Session, stock_code: str, as_of_date=None,
    _cached_stock: Optional[Stock] = None,
    _cached_industry_gm: Optional[dict] = None,
) -> dict:
    """
    定价权评分（0-100）：
    1. 毛利率水平 vs 行业（40 分）— 高于行业 = 强定价权
    2. 毛利率稳定性（30 分）— 低波动 = 定价权稳固
    3. 毛利率趋势（30 分）— 上升 = 定价权增强

    无财报数据 → 返回 50（中性），不影响信号

    v202f-fix:
    - 加 as_of_date 上界（period <= as_of - REPORT_LAG_DAYS），避免 look-ahead
    - 加 _cached_stock 复用 batch 路径已加载的 Stock，避免 N+1
    """
    cutoff_high = _gm_publishable_cutoff(as_of_date)
    cutoff_low  = date(cutoff_high.year - 5, 1, 1)
    fin_rows = (
        db.query(FinancialData)
        .filter(
            FinancialData.stock_code == stock_code,
            FinancialData.report_type == "annual",
            FinancialData.period >= cutoff_low,
            FinancialData.period <= cutoff_high,
            FinancialData.gross_margin.isnot(None),
        )
        .order_by(FinancialData.period.desc())
        .limit(5)
        .all()
    )

    if len(fin_rows) < 2:
        return {"score": 50.0, "gm_level": None, "gm_stability": None, "gm_trend": None,
                "company_gm": None, "industry_gm": None}

    # 按时间正序排列（最老在前）
    fin_rows = sorted(fin_rows, key=lambda r: r.period)
    margins = [r.gross_margin for r in fin_rows]
    avg_gm = sum(margins) / len(margins)

    # ── 子项 1：毛利率水平 vs 行业（40 分）──
    stock = _cached_stock if _cached_stock is not None else \
            db.query(Stock).filter_by(code=stock_code).first()
    industry_gm = None
    gm_level_score = 20.0  # 默认行业中位数
    if stock and stock.industry_code and _cached_industry_gm:
        industry_gm = _cached_industry_gm.get(stock.industry_code)
    if industry_gm is not None and industry_gm > 0:
        diff = avg_gm - industry_gm
        # diff = +15% → 40分, diff = 0 → 20分, diff = -15% → 0分
        gm_level_score = max(0.0, min(40.0, 20.0 + diff / 15.0 * 20.0))

    # ── 子项 2：毛利率稳定性（30 分）──
    gm_std = float(np.std(margins))
    # std < 1% → 30, std = 3% → 10, std > 5% → 0
    if gm_std < 1.0:
        gm_stability_score = 30.0
    elif gm_std < 5.0:
        gm_stability_score = max(0.0, 30.0 - (gm_std - 1.0) / 4.0 * 30.0)
    else:
        gm_stability_score = 0.0

    # ── 子项 3：毛利率趋势（30 分）──
    if len(margins) >= 3:
        x = np.arange(len(margins))
        slope = float(np.polyfit(x, margins, 1)[0])
        # slope = +2%/年 → 30分, 0 → 15分, -2%/年 → 0分
        gm_trend_score = max(0.0, min(30.0, 15.0 + slope / 2.0 * 15.0))
    else:
        gm_trend_score = 15.0  # 数据不足，默认中性

    score = gm_level_score + gm_stability_score + gm_trend_score
    score = round(max(0.0, min(100.0, score)), 2)

    return {
        "score":         score,
        "gm_level":      round(gm_level_score, 2),
        "gm_stability":  round(gm_stability_score, 2),
        "gm_trend":      round(gm_trend_score, 2),
        "company_gm":    round(avg_gm, 2),
        "industry_gm":   round(industry_gm, 2) if industry_gm is not None else None,
    }


# ════════════════════════════════════════════════════════════════
# 7. 综合短期信号
# ════════════════════════════════════════════════════════════════
def generate_short_signal(
    db: Session, stock_code: str, as_of_date=None,
    write_back: bool = True,
    commit: bool = True,
    # 批量优化缓存参数（单只调用时不传，调用方负责保证一致性）
    _cached_macro_100: Optional[float] = None,
    _cached_stock: Optional[Stock] = None,
    _cached_industries: Optional[dict] = None,
    _cached_industry_returns: Optional[dict] = None,
    _cached_industry_gm: Optional[dict] = None,
    _cached_prices: Optional[dict] = None,
    _cached_stock_returns: Optional[dict] = None,
    _cached_market_trend: Optional[dict] = None,
) -> Optional[dict]:
    """
    生成短期信号；价格数据不足时返回 None。

    write_back=True 时把结果写到 Stock 对象。
    commit=True 时立即 db.commit()（单只调用默认；批量场景请传 False，最后统一 commit）。

    批量场景的缓存参数：
    - _cached_macro_100: 已计算的 macro_score / 15 * 100（全局共享，每只一样）
    - _cached_stock:     已加载的 Stock 对象（避免重复查询）
    - _cached_industries:{code → Industry} 字典（避免每只查 Industry 表）
    - _cached_industry_gm:{industry_code → avg_gm} 字典（避免每只重新聚合财务数据）
    - _cached_prices:    {code → 最近价格序列}，供动量 / 量价复用
    - _cached_stock_returns:{code → (ret_5d, ret_20d)}，供行业相对复用
    - _cached_market_trend: 已计算的沪深300趋势过滤结果（全局共享）
    """
    # 7 维评分
    mom = score_momentum(db, stock_code, as_of_date, _cached_prices=_cached_prices)
    if mom is None:
        return None   # 价格数据不足，没法算

    vp = score_volprice(db, stock_code, as_of_date, _cached_prices=_cached_prices)
    if vp is None:
        return None

    # 宏观分：批量时复用缓存，避免每只重算 3 次 MacroData 查询
    if _cached_macro_100 is not None:
        macro_100 = _cached_macro_100
    else:
        macro_100 = score_macro(db, as_of_date) / 15 * 100

    tech       = score_tech_sector(
        db, stock_code,
        _cached_stock=_cached_stock,
        _cached_industries=_cached_industries,
    )
    if settings.SHORT_NEWS_HEAT_WEIGHT > 0:
        news_heat = score_news_heat(db, stock_code, as_of_date)
    else:
        news_heat = {"score": 50.0, "n_news": 0, "avg_sentiment": None}
    ind_rel    = score_industry_relative(
        db, stock_code, as_of_date,
        _cached_stock=_cached_stock,
        _cached_industry_returns=_cached_industry_returns,
        _cached_stock_returns=_cached_stock_returns,
    )
    # 权重为 0 时跳过整段（v202g：拿掉 pricing_power 但保留 hook，
    # 同时省掉 per-stock 的 FinancialData 查询，回测从 ~80min 回到 ~13min）
    if settings.SHORT_PRICING_POWER_WEIGHT > 0:
        pp = score_pricing_power(
            db, stock_code, as_of_date,
            _cached_stock=_cached_stock,
            _cached_industry_gm=_cached_industry_gm,
        )
    else:
        pp = {"score": 50.0, "gm_level": None, "gm_stability": None, "gm_trend": None,
              "company_gm": None, "industry_gm": None}

    # 加权综合分
    composite = (
        mom["score"]       * settings.SHORT_MOMENTUM_WEIGHT          +
        vp["score"]        * settings.SHORT_VOLPRICE_WEIGHT          +
        macro_100          * settings.SHORT_MACRO_WEIGHT             +
        tech["score"]      * settings.SHORT_TECH_WEIGHT              +
        news_heat["score"] * settings.SHORT_NEWS_HEAT_WEIGHT         +
        ind_rel["score"]   * settings.SHORT_INDUSTRY_RELATIVE_WEIGHT +
        pp["score"]        * settings.SHORT_PRICING_POWER_WEIGHT
    )
    composite = round(max(0, min(100, composite)), 2)

    # ── 5 等级判定（v201：反转模型）──
    if composite >= settings.SHORT_STRONG_BUY_THRESHOLD:
        signal = "STRONG_BUY"
    elif composite >= settings.SHORT_BUY_THRESHOLD:
        signal = "BUY"
    elif composite >= settings.SHORT_SELL_THRESHOLD:
        signal = "HOLD"
    elif composite >= settings.SHORT_STRONG_SELL_THRESHOLD:
        signal = "SELL"
    else:
        signal = "STRONG_SELL"
    market = _cached_market_trend if _cached_market_trend is not None else \
             score_market_trend(db, as_of_date)
    if signal in ("BUY", "STRONG_BUY") and not market["pass"]:
        signal = "HOLD"
    reason = _build_short_reason(signal, composite, mom, vp, macro_100, tech, news_heat, ind_rel, pp, market)

    result = {
        "short_composite_score": composite,
        "short_signal":          signal,
        "short_signal_reason":   reason,
        "sub_scores": {
            "momentum":          mom["score"],
            "volprice":          vp["score"],
            "macro":             round(macro_100, 2),
            "tech":              tech["score"],
            "news_heat":         news_heat["score"],
            "industry_relative": ind_rel["score"],
            "pricing_power":     pp["score"],
            "market_trend":      100.0 if market["pass"] else 0.0,
        },
        "details": {
            "momentum":          mom,
            "volprice":          vp,
            "tech":              tech,
            "news_heat":         news_heat,
            "industry_relative": ind_rel,
            "pricing_power":     pp,
            "market_trend":      market,
        },
    }

    if write_back:
        stock = _cached_stock if _cached_stock is not None else \
                db.query(Stock).filter_by(code=stock_code).first()
        if stock:
            stock.short_composite_score          = composite
            stock.short_signal                   = signal
            stock.short_signal_reason            = reason
            stock.short_signal_updated           = datetime.utcnow()
            stock.short_score_momentum           = mom["score"]
            stock.short_score_volprice           = vp["score"]
            stock.short_score_macro              = round(macro_100, 2)
            stock.short_score_tech               = tech["score"]
            stock.short_score_news_heat          = news_heat["score"]
            stock.short_score_industry_relative  = ind_rel["score"]
            stock.short_score_pricing_power      = pp["score"]
            if commit:
                db.commit()

    return result


def _build_short_reason(signal, composite, mom, vp, macro_100, tech, news_heat, ind_rel=None, pp=None, market=None) -> str:
    """生成中文 reason"""
    parts = [f"短期信号：综合分 {composite}"]

    # 动量
    if settings.SHORT_MOMENTUM_WEIGHT > 0 and mom.get("ret_5d") is not None:
        m_desc = f"5日 {mom['ret_5d']:+.1f}%"
        if mom.get("ret_20d") is not None:
            m_desc += f" / 20日 {mom['ret_20d']:+.1f}%"
        ma_status = "站上 MA20" if mom.get("above_ma20") else "跌破 MA20"
        if mom.get("rsi14") is not None:
            ma_status += f", RSI {mom['rsi14']:.0f}"
        parts.append(f"动量：{m_desc}，{ma_status}")

    # 量价
    if settings.SHORT_VOLPRICE_WEIGHT > 0 and vp.get("vol_ratio") is not None:
        vp_desc = "放量" if vp["vol_ratio"] > 1.3 else ("缩量" if vp["vol_ratio"] < 0.8 else "正常量")
        parts.append(f"量价：{vp_desc}（5日 / 20日 = {vp['vol_ratio']}）")

    # 宏观
    if settings.SHORT_MACRO_WEIGHT > 0:
        parts.append(f"宏观：{macro_100:.0f} 分")

    # 科技
    if settings.SHORT_TECH_WEIGHT > 0:
        if tech.get("is_tech"):
            parts.append(f"行业：科技 / 政策催化板块（评分 {tech['ind_score']}）")
        else:
            parts.append(f"行业：非科技板块（评分 {tech['ind_score']}）")

    # 行业相对（v202）
    if settings.SHORT_INDUSTRY_RELATIVE_WEIGHT > 0 and ind_rel and ind_rel.get("rel_5d") is not None:
        rel = ind_rel["rel_5d"]
        if rel > 3:
            label = f"跑赢行业 {rel:+.1f}%（涨过头）"
        elif rel < -3:
            label = f"跑输行业 {rel:+.1f}%（反转候选）"
        else:
            label = f"跟随行业 {rel:+.1f}%"
        parts.append(f"行业相对：{label}")

    if market and market.get("ret_20d") is not None:
        parts.append(f"市场趋势：沪深300 20日 {market['ret_20d']:+.1f}%")

    # 定价权（v202f）
    if settings.SHORT_PRICING_POWER_WEIGHT > 0 and pp and pp.get("company_gm") is not None:
        gm = pp["company_gm"]
        igm = pp.get("industry_gm")
        if igm is not None:
            diff = gm - igm
            if diff > 5:
                pp_desc = f"毛利率 {gm:.1f}%（行业 {igm:.1f}%，强定价权）"
            elif diff > 0:
                pp_desc = f"毛利率 {gm:.1f}%（行业 {igm:.1f}%，略高于行业）"
            elif diff > -5:
                pp_desc = f"毛利率 {gm:.1f}%（行业 {igm:.1f}%，行业中下游）"
            else:
                pp_desc = f"毛利率 {gm:.1f}%（行业 {igm:.1f}%，价格战选手）"
        else:
            pp_desc = f"毛利率 {gm:.1f}%（行业数据缺失）"
        parts.append(f"定价权：{pp_desc}")
    elif settings.SHORT_PRICING_POWER_WEIGHT > 0 and pp:
        parts.append("定价权：无财报数据（中性 50 分）")

    # 新闻热度
    if settings.SHORT_NEWS_HEAT_WEIGHT > 0:
        if news_heat["n_news"] > 0:
            s = news_heat.get("avg_sentiment")
            sent_word = "中性"
            if s is not None:
                sent_word = "积极" if s > 0.55 else ("消极" if s < 0.45 else "中性")
            parts.append(f"新闻：近 7 天 {news_heat['n_news']} 条，{sent_word}")
        else:
            parts.append("新闻：近 7 天无")

    # 操作建议（v201：反转模型）
    if signal == "STRONG_BUY":
        parts.append("→ 强烈看涨（超卖反弹 + 宏观共振）")
    elif signal == "BUY":
        parts.append("→ 短线看涨（反转机会）")
    elif signal == "HOLD":
        if market and not market.get("pass", True):
            parts.append("→ 观望（市场短线趋势未达反转买入门槛）")
        else:
            parts.append("→ 观望")
    elif signal == "SELL":
        parts.append("→ 短线回避（涨幅过快或宏观偏弱）")
    else:
        parts.append("→ 强烈回避（过热 + 宏观偏弱）")

    return "。".join(parts) + "。"


# ════════════════════════════════════════════════════════════════
# 6.0 截面排名（v203）
# ════════════════════════════════════════════════════════════════
# 各维度的 raw 子分在全市场内 → percentile 0-100。
# 思路：剥离市场整体涨跌（如全市场都涨 5%，绝对动量分都偏低，
# 但相对排名仍清晰）。学术上 cross-sectional ranking 在 A 股短期最稳健。
#
# 注意：macro 在同一 check_date 对所有股票相同 → 不排名；
# news_heat 大部分股票恒为 50 → 排名等于随机 → 不排名。

# 参与排名的维度
_RANKED_DIMS = ("momentum", "volprice", "tech", "industry_relative")


def _apply_cross_sectional_ranks(results: dict) -> dict:
    """
    输入：{stock_code: signal_dict}（generate_short_signal 的输出）
    操作：
      1. 对 _RANKED_DIMS 的每个维度，把 sub_scores 转成跨股票百分位
      2. 用 ranked 子分 + 原 macro/news_heat 重算 composite
      3. 重新分级 signal
      4. 原地更新 results
    返回：results（已 mutated）
    """
    if not results:
        return results

    # Phase 1: per-dim 排名
    ranks = {}  # stock_code → {dim: percentile_0_100}
    for dim in _RANKED_DIMS:
        # 提取所有 (code, raw) 对，跳过缺失值
        pairs = [(code, r["sub_scores"][dim])
                 for code, r in results.items()
                 if r and r.get("sub_scores") and r["sub_scores"].get(dim) is not None]
        if not pairs:
            continue
        # 按 raw 升序排序
        pairs.sort(key=lambda x: x[1])
        n = len(pairs)
        # 同分处理：用平均 rank（避免单调跳）
        for i, (code, _) in enumerate(pairs):
            # (i + 0.5) / n × 100 → 范围约 0-99
            pct = (i + 0.5) / n * 100
            ranks.setdefault(code, {})[dim] = round(pct, 2)

    # Phase 2: 用 percentile 重算 composite + signal
    w_mom    = settings.SHORT_MOMENTUM_WEIGHT
    w_vp     = settings.SHORT_VOLPRICE_WEIGHT
    w_macro  = settings.SHORT_MACRO_WEIGHT
    w_tech   = settings.SHORT_TECH_WEIGHT
    w_news   = settings.SHORT_NEWS_HEAT_WEIGHT
    w_indrel = settings.SHORT_INDUSTRY_RELATIVE_WEIGHT
    w_pp     = settings.SHORT_PRICING_POWER_WEIGHT

    for code, result in results.items():
        if not result:
            continue
        sub = result["sub_scores"]
        r = ranks.get(code, {})

        # 用 percentile（如有），fallback raw
        mom_pct    = r.get("momentum",          sub.get("momentum")          or 50.0)
        vp_pct     = r.get("volprice",          sub.get("volprice")          or 50.0)
        tech_pct   = r.get("tech",              sub.get("tech")              or 50.0)
        indrel_pct = r.get("industry_relative", sub.get("industry_relative") or 50.0)
        # 这两维不排名
        macro_raw  = sub.get("macro")     if sub.get("macro")     is not None else 50.0
        news_raw   = sub.get("news_heat") if sub.get("news_heat") is not None else 50.0
        pp_raw     = sub.get("pricing_power") if sub.get("pricing_power") is not None else 50.0

        composite = (
            mom_pct    * w_mom    +
            vp_pct     * w_vp     +
            macro_raw  * w_macro  +
            tech_pct   * w_tech   +
            news_raw   * w_news   +
            indrel_pct * w_indrel +
            pp_raw     * w_pp
        )
        composite = round(max(0, min(100, composite)), 2)

        if composite >= settings.SHORT_STRONG_BUY_THRESHOLD:
            signal = "STRONG_BUY"
        elif composite >= settings.SHORT_BUY_THRESHOLD:
            signal = "BUY"
        elif composite >= settings.SHORT_SELL_THRESHOLD:
            signal = "HOLD"
        elif composite >= settings.SHORT_STRONG_SELL_THRESHOLD:
            signal = "SELL"
        else:
            signal = "STRONG_SELL"
        if signal in ("BUY", "STRONG_BUY") and sub.get("market_trend") == 0.0:
            signal = "HOLD"

        # 更新 sub_scores 为 ranked 形式（便于诊断脚本直接读）
        result["sub_scores"] = {
            "momentum":          round(mom_pct, 2),
            "volprice":          round(vp_pct, 2),
            "macro":             round(macro_raw, 2),
            "tech":              round(tech_pct, 2),
            "news_heat":         round(news_raw, 2),
            "industry_relative": round(indrel_pct, 2),
            "pricing_power":     round(pp_raw, 2),
            "market_trend":      sub.get("market_trend", 50.0),
        }
        result["short_composite_score"] = composite
        result["short_signal"]          = signal
        # reason 用排名后的视角重写（简版，避免误导）
        result["short_signal_reason"]   = _build_ranked_reason(signal, composite, result["sub_scores"])

    return results


def _build_ranked_reason(signal, composite, sub) -> str:
    parts = [f"短期信号（截面排名）：综合分 {composite}"]
    def _label(name, v):
        if v >= 80:  return f"{name}前 20%"
        if v >= 60:  return f"{name}前 40%"
        if v >= 40:  return f"{name}中游"
        if v >= 20:  return f"{name}后 40%"
        return f"{name}后 20%"
    parts.append(_label("动量", sub["momentum"]))
    parts.append(_label("量价", sub["volprice"]))
    parts.append(_label("行业相对", sub["industry_relative"]))
    parts.append(_label("行业", sub["tech"]))
    parts.append(f"宏观 {sub['macro']:.0f}")
    op = {"STRONG_BUY": "→ 强烈看涨", "BUY": "→ 短线看涨",
          "HOLD": "→ 观望", "SELL": "→ 短线回避", "STRONG_SELL": "→ 强烈回避"}.get(signal, "")
    if op:
        parts.append(op)
    return "。".join(parts) + "。"


# ════════════════════════════════════════════════════════════════
# 6. 批量生成
# ════════════════════════════════════════════════════════════════
def generate_all_short_signals(db: Session, limit: Optional[int] = None) -> dict:
    """
    批量生成所有 active stocks 的短期信号。

    性能优化（vs 单只循环调用）：
    - macro_score 全局计算 1 次（替代 5196 次冗余查询）
    - Industry 表预加载成 dict（替代 5196 次单查）
    - Stock 对象直接传入（不重复 query）
    - 循环里 write_back=True + commit=False，最后统一一次 commit
    """
    import time
    t0 = time.time()

    # 预加载缓存
    macro_100         = score_macro(db) / 15 * 100
    industries_map    = {i.code: i for i in db.query(Industry).all()}
    price_cache       = compute_recent_price_cache(db)
    stock_returns     = compute_stock_returns_from_price_cache(price_cache)
    industry_price_cache = compute_recent_price_cache(db, lookback_days=45)
    industry_stock_returns = compute_stock_returns_from_price_cache(industry_price_cache)
    industry_returns  = compute_industry_returns_at(
        db, _cached_stock_returns=industry_stock_returns
    )
    market_trend      = score_market_trend(db)
    industry_gm       = (
        compute_industry_avg_gm(db)
        if settings.SHORT_PRICING_POWER_WEIGHT > 0 else None
    )
    logger.info(
        f"短期信号预加载：macro={macro_100:.1f}，industries={len(industries_map)} 个，"
        f"price_cache={len(price_cache)} 只，industry_returns={len(industry_returns)} 个，"
        f"market_trend={market_trend.get('ret_20d')}%，"
        f"industry_gm={len(industry_gm or {})} 个"
    )

    # 拉所有股票（一次性，不在循环里）
    q = db.query(Stock).filter_by(is_active=True)
    if limit:
        q = q.limit(limit)
    stocks = q.all()

    # Phase 1：算所有股票的 raw 信号（不 write_back，等排名后再写）
    raw_results = {}   # stock_code → full signal dict
    stock_map   = {s.code: s for s in stocks}
    skipped     = 0
    for stock in stocks:
        try:
            r = generate_short_signal(
                db, stock.code,
                write_back=False, commit=False,        # 关键：先不写
                _cached_macro_100=macro_100,
                _cached_stock=stock,
                _cached_industries=industries_map,
                _cached_industry_returns=industry_returns,
                _cached_industry_gm=industry_gm,
                _cached_prices=price_cache,
                _cached_stock_returns=stock_returns,
                _cached_market_trend=market_trend,
            )
            if r:
                raw_results[stock.code] = r
            else:
                skipped += 1
        except Exception as e:
            logger.warning(f"短期信号 {stock.code}: {e}")
            skipped += 1

    # Phase 2：截面排名（v203）
    if settings.SHORT_USE_CROSS_SECTIONAL_RANKS:
        _apply_cross_sectional_ranks(raw_results)
        logger.info(f"短期信号：cross-sectional ranking 已应用 ({len(raw_results)} 只)")

    # Phase 3：写回 Stock 对象 + commit
    results = {}
    for code, r in raw_results.items():
        stock = stock_map.get(code)
        if not stock:
            continue
        sub = r["sub_scores"]
        stock.short_composite_score          = r["short_composite_score"]
        stock.short_signal                   = r["short_signal"]
        stock.short_signal_reason            = r["short_signal_reason"]
        stock.short_signal_updated           = datetime.utcnow()
        stock.short_score_momentum           = sub.get("momentum")
        stock.short_score_volprice           = sub.get("volprice")
        stock.short_score_macro              = sub.get("macro")
        stock.short_score_tech               = sub.get("tech")
        stock.short_score_news_heat          = sub.get("news_heat")
        stock.short_score_industry_relative  = sub.get("industry_relative")
        stock.short_score_pricing_power      = sub.get("pricing_power")
        results[code] = r["short_signal"]
    db.commit()

    elapsed = time.time() - t0
    logger.info(
        f"短期信号批量生成完成：成功 {len(results)} / 跳过 {skipped} / "
        f"共 {len(stocks)} / 耗时 {elapsed:.1f}s"
    )
    return {
        "generated": len(results), "skipped": skipped,
        "total": len(stocks), "elapsed_sec": round(elapsed, 1),
    }
