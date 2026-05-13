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
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy.orm import Session
from sqlalchemy import desc

from config import settings
from models.models import PriceData, Stock, NewsItem, Industry
from engines.signal_engine import score_macro

logger = logging.getLogger(__name__)


# ════════════════════════════════════════════════════════════════
# 1. 动量评分（0-100）
# ════════════════════════════════════════════════════════════════
def score_momentum(db: Session, stock_code: str, as_of_date=None) -> Optional[dict]:
    """
    基于近 60 个交易日的价格序列计算动量分。
    返回 {"score": 0-100, "ret_5d": ..., "ret_20d": ..., "ret_60d": ...,
          "above_ma20": bool, "above_ma60": bool, "rsi14": float}
    或 None（数据不足）。
    """
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

    # ── 评分（0-100，部分项权重内）──
    score = 50.0   # 中性基准

    # 5 日收益：±10% → ±15 分
    if ret_5d is not None:
        score += max(-15, min(15, ret_5d * 150))   # +1% → +1.5 分

    # 20 日收益：±20% → ±15 分
    if ret_20d is not None:
        score += max(-15, min(15, ret_20d * 75))

    # 60 日收益：±30% → ±10 分（长一点的趋势）
    if ret_60d is not None:
        score += max(-10, min(10, ret_60d * 33))

    # 站上 MA20：+5；MA60：+5
    if above_ma20:
        score += 5
    if above_ma60:
        score += 5

    # RSI 健康区间（40-65）：+5；过热（>75）：-5；超卖反弹（<30）：+3
    if rsi14 is not None:
        if 40 <= rsi14 <= 65:
            score += 5
        elif rsi14 > 75:
            score -= 5
        elif rsi14 < 30:
            score += 3

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
def score_volprice(db: Session, stock_code: str, as_of_date=None) -> Optional[dict]:
    """
    涨且放量 → 正面（资金入场）
    跌且放量 → 负面（恐慌出逃）
    涨且缩量 → 中性偏弱（散户拉抬，不稳）
    跌且缩量 → 中性偏强（抛压减弱）
    """
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

    # 评分逻辑
    score = 50.0
    if ret_5d > 0.02:        # 明显涨
        if vol_ratio > 1.3:  # 涨且放量
            score = 75 + min(15, (vol_ratio - 1.3) * 30)
        elif vol_ratio > 0.9:
            score = 55
        else:                # 涨但缩量
            score = 45
    elif ret_5d < -0.02:     # 明显跌
        if vol_ratio > 1.3:  # 跌且放量 = 恐慌
            score = 25 - min(15, (vol_ratio - 1.3) * 30)
        elif vol_ratio < 0.8:# 跌且缩量 = 抛压减弱
            score = 50
        else:
            score = 35
    # else: 横盘（±2% 内）→ 维持 50

    return {
        "score":     round(max(0, min(100, score)), 2),
        "vol_ratio": round(vol_ratio, 2),
        "ret_5d":    round(ret_5d * 100, 2),
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

    is_tech = stock.industry_code in settings.TECH_INDUSTRIES
    base = 70.0 if is_tech else 30.0

    # 叠加行业评分
    if _cached_industries is not None:
        ind = _cached_industries.get(stock.industry_code)
    else:
        ind = db.query(Industry).filter_by(code=stock.industry_code).first()
    ind_score = (ind.total_score or 50.0) if ind else 50.0

    if is_tech:
        # 白名单内：行业评分 >= 60 满分；< 60 线性衰减
        bonus = max(0, min(30, (ind_score - 50) * 1.5))
    else:
        # 白名单外：行业评分 >= 70 才加分
        bonus = max(0, min(20, (ind_score - 60) * 1.0))

    return {
        "score":     round(base + bonus, 2),
        "is_tech":   is_tech,
        "ind_score": round(ind_score, 2),
    }


# ════════════════════════════════════════════════════════════════
# 4. 新闻热度评分（0-100）
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
# 5. 综合短期信号
# ════════════════════════════════════════════════════════════════
def generate_short_signal(
    db: Session, stock_code: str, as_of_date=None,
    write_back: bool = True,
    commit: bool = True,
    # 批量优化缓存参数（单只调用时不传，调用方负责保证一致性）
    _cached_macro_100: Optional[float] = None,
    _cached_stock: Optional[Stock] = None,
    _cached_industries: Optional[dict] = None,
) -> Optional[dict]:
    """
    生成短期信号；价格数据不足时返回 None。

    write_back=True 时把结果写到 Stock 对象。
    commit=True 时立即 db.commit()（单只调用默认；批量场景请传 False，最后统一 commit）。

    批量场景的缓存参数：
    - _cached_macro_100: 已计算的 macro_score / 15 * 100（全局共享，每只一样）
    - _cached_stock:     已加载的 Stock 对象（避免重复查询）
    - _cached_industries:{code → Industry} 字典（避免每只查 Industry 表）
    """
    # 5 维评分
    mom = score_momentum(db, stock_code, as_of_date)
    if mom is None:
        return None   # 价格数据不足，没法算

    vp = score_volprice(db, stock_code, as_of_date)
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
    news_heat  = score_news_heat(db, stock_code, as_of_date)

    # 加权综合分
    composite = (
        mom["score"]       * settings.SHORT_MOMENTUM_WEIGHT  +
        vp["score"]        * settings.SHORT_VOLPRICE_WEIGHT  +
        macro_100          * settings.SHORT_MACRO_WEIGHT     +
        tech["score"]      * settings.SHORT_TECH_WEIGHT      +
        news_heat["score"] * settings.SHORT_NEWS_HEAT_WEIGHT
    )
    composite = round(max(0, min(100, composite)), 2)

    # ── 5 等级判定 ──
    hard_sell_drop = vp["ret_5d"] / 100   # ret_5d 已经是百分比
    ret_5d_pct = mom.get("ret_5d") or 0   # 来自 momentum 的 5 日涨幅（百分比）
    if hard_sell_drop < settings.SHORT_HARD_SELL_5D_DROP:
        # 硬卖：5 日跌幅超过 -10%（默认）
        signal = "STRONG_SELL"
        reason = f"近 5 日跌幅 {vp['ret_5d']:.1f}%，触发硬卖（< {settings.SHORT_HARD_SELL_5D_DROP*100:.0f}%）"
    elif composite >= settings.SHORT_STRONG_BUY_THRESHOLD and ret_5d_pct > 2.0:
        # 必买：综合分够 + 5 日涨幅 > 2%（实质性动能，而非微涨）
        signal = "STRONG_BUY"
        reason = _build_short_reason("STRONG_BUY", composite, mom, vp, macro_100, tech, news_heat)
    elif composite >= settings.SHORT_STRONG_BUY_THRESHOLD:
        # 综合分够但短期动能不足（5日涨幅 <= 2%）→ 降级为买入
        signal = "BUY"
        reason = _build_short_reason("BUY", composite, mom, vp, macro_100, tech, news_heat)
        reason += "（注：综合分达 STRONG_BUY，但 5 日动能未确认，降级为 BUY）"
    elif composite >= settings.SHORT_BUY_THRESHOLD:
        signal = "BUY"
        reason = _build_short_reason("BUY", composite, mom, vp, macro_100, tech, news_heat)
    elif composite >= settings.SHORT_SELL_THRESHOLD:
        signal = "HOLD"
        reason = _build_short_reason("HOLD", composite, mom, vp, macro_100, tech, news_heat)
    elif composite >= settings.SHORT_STRONG_SELL_THRESHOLD:
        signal = "SELL"
        reason = _build_short_reason("SELL", composite, mom, vp, macro_100, tech, news_heat)
    else:
        signal = "STRONG_SELL"
        reason = _build_short_reason("STRONG_SELL", composite, mom, vp, macro_100, tech, news_heat)

    result = {
        "short_composite_score": composite,
        "short_signal":          signal,
        "short_signal_reason":   reason,
        "sub_scores": {
            "momentum":  mom["score"],
            "volprice":  vp["score"],
            "macro":     round(macro_100, 2),
            "tech":      tech["score"],
            "news_heat": news_heat["score"],
        },
        "details": {
            "momentum":  mom,
            "volprice":  vp,
            "tech":      tech,
            "news_heat": news_heat,
        },
    }

    if write_back:
        # 复用 _cached_stock 避免额外查询
        stock = _cached_stock if _cached_stock is not None else \
                db.query(Stock).filter_by(code=stock_code).first()
        if stock:
            stock.short_composite_score = composite
            stock.short_signal          = signal
            stock.short_signal_reason   = reason
            stock.short_signal_updated  = datetime.utcnow()
            stock.short_score_momentum  = mom["score"]
            stock.short_score_volprice  = vp["score"]
            stock.short_score_macro     = round(macro_100, 2)
            stock.short_score_tech      = tech["score"]
            stock.short_score_news_heat = news_heat["score"]
            if commit:
                db.commit()

    return result


def _build_short_reason(signal, composite, mom, vp, macro_100, tech, news_heat) -> str:
    """生成中文 reason"""
    parts = [f"短期信号：综合分 {composite}"]

    # 动量
    if mom.get("ret_5d") is not None:
        m_desc = f"5日 {mom['ret_5d']:+.1f}%"
        if mom.get("ret_20d") is not None:
            m_desc += f" / 20日 {mom['ret_20d']:+.1f}%"
        ma_status = "站上 MA20" if mom.get("above_ma20") else "跌破 MA20"
        if mom.get("rsi14") is not None:
            ma_status += f", RSI {mom['rsi14']:.0f}"
        parts.append(f"动量：{m_desc}，{ma_status}")

    # 量价
    if vp.get("vol_ratio") is not None:
        vp_desc = "放量" if vp["vol_ratio"] > 1.3 else ("缩量" if vp["vol_ratio"] < 0.8 else "正常量")
        parts.append(f"量价：{vp_desc}（5日 / 20日 = {vp['vol_ratio']}）")

    # 科技
    if tech.get("is_tech"):
        parts.append(f"行业：科技 / 政策催化板块（评分 {tech['ind_score']}）")
    else:
        parts.append(f"行业：非科技板块（评分 {tech['ind_score']}）")

    # 新闻热度
    if news_heat["n_news"] > 0:
        s = news_heat.get("avg_sentiment")
        sent_word = "中性"
        if s is not None:
            sent_word = "积极" if s > 0.55 else ("消极" if s < 0.45 else "中性")
        parts.append(f"新闻：近 7 天 {news_heat['n_news']} 条，{sent_word}")
    else:
        parts.append("新闻：近 7 天无")

    # 操作建议
    if signal == "STRONG_BUY":
        parts.append("→ 强烈看涨（动量足 + 多重共振）")
    elif signal == "BUY":
        parts.append("→ 短线看涨")
    elif signal == "HOLD":
        parts.append("→ 观望")
    elif signal == "SELL":
        parts.append("→ 短线回避 / 减仓")
    else:
        parts.append("→ 强烈回避")

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
    macro_100        = score_macro(db) / 15 * 100
    industries_map   = {i.code: i for i in db.query(Industry).all()}
    logger.info(f"短期信号预加载：macro={macro_100:.1f}，industries={len(industries_map)} 个")

    # 拉所有股票（一次性，不在循环里）
    q = db.query(Stock).filter_by(is_active=True)
    if limit:
        q = q.limit(limit)
    stocks = q.all()

    results = {}
    skipped = 0
    for stock in stocks:
        try:
            r = generate_short_signal(
                db, stock.code,
                write_back=True, commit=False,        # 关键：不在循环里 commit
                _cached_macro_100=macro_100,           # 复用全局 macro
                _cached_stock=stock,                   # 直接用已加载的 Stock 对象
                _cached_industries=industries_map,     # 复用 industries dict
            )
            if r:
                results[stock.code] = r["short_signal"]
            else:
                skipped += 1
        except Exception as e:
            logger.warning(f"短期信号 {stock.code}: {e}")
            skipped += 1

    # 一次性提交（vs 5196 次单独 commit）
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
