"""
买卖信号引擎
综合四个模块输出最终建议：BUY / HOLD / SELL
并生成文字理由
"""
from datetime import datetime, timedelta
from typing import Optional
from sqlalchemy.orm import Session

from config import settings
from models.models import MacroData, Stock, Industry, PriceData
from engines.company_scorer import score_company, score_valuation
from engines.industry_scorer import score_industry
from data.sentiment import get_stock_sentiment_score


# ──────────────────────────────────────────────
# 宏观环境评分（满分 15）
# ──────────────────────────────────────────────
def score_macro(db: Session, as_of_date=None) -> float:
    """
    基于 PMI / CPI / 北向资金流向 评分
    景气度越高分越高
    """
    cutoff = as_of_date or datetime.utcnow().date()

    def latest_val(indicator: str):
        row = (
            db.query(MacroData)
            .filter(MacroData.indicator == indicator, MacroData.date <= cutoff)
            .order_by(MacroData.date.desc())
            .first()
        )
        return row.value if row else None

    score = 7.5   # 中性基准

    # PMI > 50 景气扩张
    pmi = latest_val("PMI")
    if pmi is not None:
        score += min(3, max(-3, (pmi - 50) * 0.6))

    # CPI 温和通胀（1-3%）
    cpi = latest_val("CPI")
    if cpi is not None:
        if 1.0 <= cpi <= 3.0:
            score += 2
        elif cpi > 4.0 or cpi < 0:
            score -= 2

    # 北向资金近 5 日净流入
    north = latest_val("NORTH_FLOW")
    if north is not None:
        if north > 0:
            score += min(2.5, north / 2e9 * 2.5)
        else:
            score += max(-2.5, north / 2e9 * 2.5)

    return round(min(15.0, max(0.0, score)), 2)


# ──────────────────────────────────────────────
# 综合信号生成
# ──────────────────────────────────────────────
def generate_signal(
    db: Session,
    stock_code: str,
    as_of_date=None,
    params: dict = None,
    write_back: bool = True,
) -> Optional[dict]:
    """
    生成综合买卖建议
    返回:
      {composite_score, signal, sub_scores, reason}
    """
    p = params or {}

    W_fund    = p.get("fundamental_weight", settings.FUNDAMENTAL_WEIGHT)
    W_val     = p.get("valuation_weight",   settings.VALUATION_WEIGHT)
    W_sent    = p.get("sentiment_weight",   settings.SENTIMENT_WEIGHT)
    W_macro   = p.get("macro_weight",       settings.MACRO_WEIGHT)
    # 阈值语义（5 等级）：
    #   composite >= STRONG_BUY_TH (80)   → STRONG_BUY  （上界，达到触发）
    #   composite >= BUY_TH (64)          → BUY
    #   composite >= SELL_TH (50)         → HOLD
    #   composite >= STRONG_SELL_TH (30)  → SELL
    #   composite <  STRONG_SELL_TH (30)  → STRONG_SELL （下界，跌破触发）
    STRONG_BUY_TH  = p.get("strong_buy_threshold",  settings.STRONG_BUY_THRESHOLD)
    BUY_TH         = p.get("buy_threshold",         settings.BUY_THRESHOLD)
    SELL_TH        = p.get("sell_threshold",        settings.SELL_THRESHOLD)
    STRONG_SELL_TH = p.get("strong_sell_threshold", settings.STRONG_SELL_THRESHOLD)

    # ── 1. 基本面评分 (0-80 → 归一化到 0-100) ──
    co_result = score_company(db, stock_code, as_of_date, params, write_back=False)
    if co_result is None:
        return None
    fundamental_raw = co_result["fundamental_score"]   # 0-80
    fundamental_100 = fundamental_raw / 80 * 100

    # ── 2. 估值评分 (0-20) ──
    valuation_score = score_valuation(db, stock_code, as_of_date, params)

    # ── 3. 舆情评分 (0-20) ──
    sentiment_score = get_stock_sentiment_score(db, stock_code)

    # ── 4. 宏观评分 (0-15) ──
    macro_score = score_macro(db, as_of_date)

    # ── 5. 加权综合分 ──
    composite = (
        fundamental_100 * W_fund +
        (valuation_score / 20 * 100) * W_val +
        (sentiment_score / 20 * 100) * W_sent +
        (macro_score / 15 * 100) * W_macro
    )
    composite = round(min(100.0, max(0.0, composite)), 2)

    # ── 6. 一票否决规则 ──
    is_fin = co_result.get("is_financial_sector", False)
    veto, veto_reason = _check_veto(co_result, valuation_score, sentiment_score, is_fin)

    # ── 6b. 质量门槛（回测数据驱动） ──
    # v102 研究：composite 60-64 胜率 43-47%（亏损区），ROE <15 胜率 36% → BUY 需 ROE>=15
    # v103 研究（run#26 分析 290 条 BUY）：
    #   行业评分 <50：n=113，胜率 48.4%（亏损区）
    #   行业评分 ≥50：n=177，胜率 70.1%
    # 因此增加行业评分门槛作为 BUY 二次过滤
    roe_q      = co_result.get("score_roe_quality") or 0
    quality_gate = (roe_q >= 15.0)     # ROE 分数（满分25）>= 15，即质地及格线

    # 行业门槛（v103 新增）
    industry_min = p.get("industry_min_score", settings.INDUSTRY_MIN_SCORE)
    industry_score_val = _get_industry_score(db, stock_code)
    industry_gate = (industry_score_val is None) or (industry_score_val >= industry_min)
    # 注：industry_score 缺失时通行（新行业/数据空缺不应强制否决）

    # 宏观门槛（v104 新增）
    # 回测（run#28）表明宏观 pct>=55 的 BUY 胜率 75.86%，<55 的仅约 55%
    # macro_score 原始 0-15，乘 100/15 转为 0-100 与阈值同尺度
    macro_min = p.get("macro_min_score", getattr(settings, "MACRO_MIN_SCORE", 55.0))
    macro_pct = macro_score / 15 * 100
    macro_gate = (macro_pct >= macro_min)

    # 动量门槛（v105 新增）
    # run#29 分析：60 日前动量 -10%~0% 区间（接下跌的刀）胜率仅 50%
    # 其他区间胜率：<-10%→89.5%, 0~10%→62.5%, 10~20%→88.9%, >20%→100%
    # 因此仅拒绝 "正在下跌中" 的股票，保留深跌反转与上升趋势
    momentum_block = _check_momentum_block(db, stock_code, as_of_date, p)

    # 估值门槛（v107 新增）
    # run#31 分析（v106 修复 PE/PB 后）：valuation_pct 分布与胜率：
    #   <50: n=11 胜率 63.6%   50-60: n=2 100%(样本小)   ≥75: n=23 胜率 82.6%
    # 即"历史估值百分位越低（越便宜）胜率越高"。设门槛 valuation_pct >= 70
    # 拒绝偏贵区间，保留便宜与中等偏下，进一步提纯 BUY 信号
    val_min = p.get("valuation_min_score", getattr(settings, "VALUATION_MIN_SCORE", 70.0))
    val_pct = valuation_score / 20 * 100
    valuation_gate = (val_pct >= val_min)

    # 金融行业加强门槛（v108 新增）
    # run#32 分析：11 笔独立 BUY 中 2 笔误判均为银行股（002142/600036）2022-10-01
    #   误判时 macro=63.7（银行盈利压力区间）；银行成功 trades macro ∈ [64.1, 70.9]
    # 银行对宏观利率环境极度敏感（NIM 受 LPR 下调直接挤压），需更严格宏观门槛
    # 非金融继续用 MACRO_MIN_SCORE=55，金融要求 macro >= FIN_MACRO_MIN_SCORE=65
    fin_macro_min = p.get("fin_macro_min_score",
                          getattr(settings, "FIN_MACRO_MIN_SCORE", 65.0))
    is_fin_sector = co_result.get("is_financial_sector", False)
    fin_macro_gate = (not is_fin_sector) or (macro_pct >= fin_macro_min)

    # 所有 8 个买入门槛全部通过的判定（必买 + 买入共用）
    all_gates_pass = (
        quality_gate and industry_gate and macro_gate and valuation_gate
        and fin_macro_gate and not momentum_block
    )

    if veto:
        # veto 升级为必卖：财务造假 / 退市风险等是真正必须避开的红牌
        signal = "STRONG_SELL"
        reason = veto_reason
    elif composite >= STRONG_BUY_TH and all_gates_pass:
        # 必买：综合分 ≥ 80 + 全门槛通过
        signal = "STRONG_BUY"
        reason = _build_reason("STRONG_BUY", composite, co_result, valuation_score,
                               sentiment_score, macro_score)
    elif composite >= BUY_TH and all_gates_pass:
        # 买入：综合分 ≥ 64 + 全门槛通过
        signal = "BUY"
        reason = _build_reason("BUY", composite, co_result, valuation_score,
                               sentiment_score, macro_score)
    elif composite >= BUY_TH and not quality_gate:
        # 综合分达标但 ROE 质地不足 → 降级为 HOLD
        signal = "HOLD"
        reason = _build_reason("HOLD", composite, co_result, valuation_score,
                               sentiment_score, macro_score)
        reason += "（ROE 质地未达持续盈利标准，暂不列入买入候选）"
    elif composite >= BUY_TH and not industry_gate:
        # 综合分达标但行业景气度不足 → 降级为 HOLD
        signal = "HOLD"
        reason = _build_reason("HOLD", composite, co_result, valuation_score,
                               sentiment_score, macro_score)
        reason += f"（行业评分 {industry_score_val:.1f} 低于门槛 {industry_min}，行业景气度不足）"
    elif composite >= BUY_TH and not macro_gate:
        # 综合分达标但宏观环境偏弱 → 降级为 HOLD
        signal = "HOLD"
        reason = _build_reason("HOLD", composite, co_result, valuation_score,
                               sentiment_score, macro_score)
        reason += f"（宏观环境评分 {macro_pct:.1f} 低于门槛 {macro_min}，等待景气度回升）"
    elif composite >= BUY_TH and momentum_block:
        # 综合分达标但处于"接下跌的刀"动量区间 → 降级为 HOLD
        signal = "HOLD"
        reason = _build_reason("HOLD", composite, co_result, valuation_score,
                               sentiment_score, macro_score)
        reason += "（近 60 日处于缓慢下跌区间[-10%~0%]，等待趋势明朗）"
    elif composite >= BUY_TH and not fin_macro_gate:
        # 金融行业综合分达标但宏观偏弱 → 降级为 HOLD
        signal = "HOLD"
        reason = _build_reason("HOLD", composite, co_result, valuation_score,
                               sentiment_score, macro_score)
        reason += f"（金融行业宏观门槛 {macro_pct:.1f} 低于 {fin_macro_min}，LPR/利率风险）"
    elif composite >= SELL_TH:
        # 持有：50 ≤ composite < 64
        signal = "HOLD"
        reason = _build_reason("HOLD", composite, co_result, valuation_score,
                               sentiment_score, macro_score)
    elif composite >= STRONG_SELL_TH:
        # 卖出：30 ≤ composite < 50
        signal = "SELL"
        reason = _build_reason("SELL", composite, co_result, valuation_score,
                               sentiment_score, macro_score)
    else:
        # 必卖：composite < 30，质地过低
        signal = "STRONG_SELL"
        reason = _build_reason("STRONG_SELL", composite, co_result, valuation_score,
                               sentiment_score, macro_score)

    sub_scores = {
        "fundamental":  round(fundamental_100, 2),
        "valuation":    round(valuation_score / 20 * 100, 2),
        "sentiment":    round(sentiment_score / 20 * 100, 2),
        "macro":        round(macro_score / 15 * 100, 2),
        **co_result,
    }

    result = {
        "composite_score": composite,
        "signal":          signal,
        "sub_scores":      sub_scores,
        "reason":          reason,
    }

    if write_back:
        stock = db.query(Stock).filter_by(code=stock_code).first()
        if stock:
            stock.composite_score  = composite
            stock.signal           = signal
            stock.signal_reason    = reason
            stock.signal_updated   = datetime.utcnow()
            stock.score_valuation  = valuation_score
            for k in ["score_roe_quality", "score_profit_growth",
                      "score_cashflow", "score_financial_health", "fundamental_score"]:
                setattr(stock, k, co_result.get(k))
            db.commit()

    return result


def generate_all_signals(
    db: Session, as_of_date=None, params: dict = None, limit: Optional[int] = None
) -> dict:
    """
    批量生成并写回所有股票的买卖信号。
    write_back=True：结果直接存入 Stock 表，避免前端轮询看到空数据。
    """
    import logging as _log
    stocks = db.query(Stock).filter_by(is_active=True).all()
    if limit:
        stocks = stocks[:limit]
    results = {}
    for stock in stocks:
        try:
            r = generate_signal(db, stock.code, as_of_date, params, write_back=True)
            if r:
                results[stock.code] = r
        except Exception as e:
            _log.getLogger(__name__).warning(f"信号 {stock.code}: {e}")
    return results


# ──────────────────────────────────────────────
# 动量检查（供 BUY 门槛使用）—— v105 新增
# ──────────────────────────────────────────────
def _check_momentum_block(
    db: Session, stock_code: str, as_of_date=None, params: dict = None
) -> bool:
    """
    判断是否处于"接下跌的刀"动量区间（-10% ~ 0%）
    返回 True 表示应降级为 HOLD（BUY 被阻止）

    基于 run#29 回测数据：
      60 日前动量 -10% ~ 0% 区间胜率仅 50%（接下跌的刀）
      其余区间：<-10%→89.5%, 0~10%→62.5%, 10~20%→88.9%, >20%→100%
    """
    p = params or {}
    lookback_days = p.get("momentum_lookback_days", 90)
    low  = p.get("momentum_block_low",  -0.10)
    high = p.get("momentum_block_high",  0.0)

    cutoff = as_of_date or datetime.utcnow().date()
    past_cutoff = cutoff - timedelta(days=lookback_days)

    p_now = (
        db.query(PriceData)
        .filter(PriceData.stock_code == stock_code, PriceData.trade_date <= cutoff)
        .order_by(PriceData.trade_date.desc()).first()
    )
    p_past = (
        db.query(PriceData)
        .filter(PriceData.stock_code == stock_code, PriceData.trade_date <= past_cutoff)
        .order_by(PriceData.trade_date.desc()).first()
    )
    # 数据不足时不阻止（新上市/数据缺失）
    if not p_now or not p_past or not p_past.close or p_past.close <= 0:
        return False

    mom = p_now.close / p_past.close - 1
    return low <= mom < high


# ──────────────────────────────────────────────
# 行业评分查询（供 BUY 门槛使用）
# ──────────────────────────────────────────────
def _get_industry_score(db: Session, stock_code: str) -> Optional[float]:
    """取股票所属行业当前的 total_score；无数据返回 None"""
    stock = db.query(Stock).filter_by(code=stock_code).first()
    if not stock or not stock.industry_code:
        return None
    ind = db.query(Industry).filter_by(code=stock.industry_code).first()
    if not ind or ind.total_score is None:
        return None
    return float(ind.total_score)


# ──────────────────────────────────────────────
# 一票否决规则
# ──────────────────────────────────────────────
def _check_veto(co_result: dict, val_score: float, sent_score: float, is_financial: bool = False):
    """返回 (is_veto: bool, reason: str)"""

    # 规则1：极端负面舆情（sentiment 接近 0）
    if sent_score < 2.0:
        return True, "舆情出现极端负面事件（疑似造假/立案/债务违约），建议立即规避"

    # 规则2：估值极度高估（PE > 历史 90% 分位）
    val_pct = val_score / 20 * 100
    if val_pct < 10:   # 估值分<10% → 处于历史最贵区间
        return True, "估值处于历史极高分位（>90%），安全边际不足，建议减仓等待回落"

    # 规则3：财务健康彻底崩坏
    # 金融行业（银行/证券/保险）已在 company_scorer 中用行业专用标准评分，
    # 此处 score=0 意味着负债率即使按金融行业标准也异常高（>95%），才触发否决
    health = co_result.get("score_financial_health", 15)
    if health is not None and health <= 0.0:
        if is_financial:
            return True, "财务杠杆率异常偏高（即使以金融行业标准衡量），风险需警惕"
        else:
            return True, "财务健康度崩溃（资产负债率已严重超标），偿债风险极高"

    return False, ""


# ──────────────────────────────────────────────
# 理由生成
# ──────────────────────────────────────────────
def _build_reason(
    signal: str,
    score: float,
    co_result: dict,
    val_score: float,
    sent_score: float,
    macro_score: float,
) -> str:
    fund_s  = co_result.get("fundamental_score", 0)
    roe_s   = co_result.get("score_roe_quality", 0)
    grow_s  = co_result.get("score_profit_growth", 0)
    cf_s    = co_result.get("score_cashflow", 0)
    hlth_s  = co_result.get("score_financial_health", 0)

    parts = [f"综合评分 {score:.1f}/100"]

    # 优势
    strengths = []
    if roe_s >= 20:     strengths.append("ROE 持续高于 15%（质地优秀）")
    if grow_s >= 15:    strengths.append("净利润保持高速增长")
    if cf_s >= 16:      strengths.append("经营现金流充裕，盈利质量高")
    if val_score >= 15: strengths.append("估值处于历史低位，安全边际充足")
    if sent_score >= 15: strengths.append("近期舆情偏正面")
    if macro_score >= 12: strengths.append("宏观环境景气，北向资金流入")

    # 风险
    risks = []
    if roe_s < 12:      risks.append("ROE 不稳定或低于基准")
    if grow_s < 8:      risks.append("盈利增长放缓")
    if cf_s < 8:        risks.append("现金流质量偏弱")
    # hlth_s < 6 才提示，避免因利息覆盖数据缺失误报"负债偏高"
    if hlth_s < 6:
        if co_result.get("is_financial_sector"):
            risks.append("杠杆率偏高（即使以金融行业标准衡量）")
        else:
            risks.append("财务稳健度偏低（高杠杆/利息保障弱）")
    if val_score < 8:   risks.append("估值偏高，上行空间有限")
    if sent_score < 8:  risks.append("近期负面舆情需关注")
    if macro_score < 6: risks.append("宏观环境偏弱，外资流出")

    if signal == "STRONG_BUY":
        parts.append("强烈建议买入（综合分高 + 全部门槛通过）")
        if strengths:
            parts.append("核心优势：" + "；".join(strengths[:3]))
        if risks:
            parts.append("仍需关注：" + "；".join(risks[:2]))
    elif signal == "BUY":
        parts.append("建议买入")
        if strengths:
            parts.append("优势：" + "；".join(strengths[:3]))
        if risks:
            parts.append("注意：" + "；".join(risks[:2]))
    elif signal == "HOLD":
        parts.append("建议持有观察")
        if strengths:
            parts.append("支撑：" + "；".join(strengths[:2]))
        if risks:
            parts.append("制约：" + "；".join(risks[:2]))
    elif signal == "STRONG_SELL":
        parts.append("强烈建议卖出/回避（综合分极低或触发风险红牌）")
        if risks:
            parts.append("主要风险：" + "；".join(risks[:3]))
    else:
        # SELL
        parts.append("建议卖出/回避")
        if risks:
            parts.append("主要风险：" + "；".join(risks[:3]))

    return "。".join(parts) + "。"
