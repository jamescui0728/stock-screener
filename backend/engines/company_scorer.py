"""
公司评分引擎（满分 100）
五个维度：
  1. ROE 质量      25 分
  2. 盈利增长      20 分
  3. 现金流健康    20 分
  4. 财务稳健度    15 分
  5. 估值安全边际  20 分（在 signal_engine 中合并）

返回 fundamental_score（前四项，满分 80）
估值分由 signal_engine 单独计算，避免耦合
"""
import numpy as np
import pandas as pd
from typing import Optional, List
from sqlalchemy.orm import Session

from models.models import FinancialData, PriceData, Stock
from config import settings


# ──────────────────────────────────────────────
# 金融行业特殊处理
# 银行/证券/保险的负债率 85-95% 是行业常态（存款即负债），
# 传统现金流指标也不适用，需要差异化评分
# ──────────────────────────────────────────────
_FINANCIAL_INDUSTRY_KEYWORDS = ("银行", "证券", "保险", "金融", "信托", "期货")

def _is_financial_sector(db: Session, stock_code: str) -> bool:
    """判断股票是否属于金融行业"""
    stock = db.query(Stock).filter_by(code=stock_code).first()
    if not stock or not stock.industry_code:
        return False
    from models.models import Industry
    ind = db.query(Industry).filter_by(code=stock.industry_code).first()
    if not ind:
        return False
    return any(kw in ind.name for kw in _FINANCIAL_INDUSTRY_KEYWORDS)


# ──────────────────────────────────────────────
# 主评分入口
# ──────────────────────────────────────────────
def score_company(
    db: Session,
    stock_code: str,
    as_of_date=None,
    params: dict = None,
    write_back: bool = True,
) -> Optional[dict]:
    """
    计算公司基本面评分
    返回 {roe_quality, profit_growth, cashflow, financial_health, fundamental_score}
    as_of_date: 回测时传入防未来泄露
    """
    p = params or {}
    W = {
        "roe":      p.get("co_w_roe",      25),
        "growth":   p.get("co_w_growth",   20),
        "cashflow": p.get("co_w_cashflow", 20),
        "health":   p.get("co_w_health",   15),
    }
    MAX = sum(W.values())   # 通常 80

    financials = _get_financials(db, stock_code, as_of_date)
    if not financials or len(financials) < 2:
        return None

    df = _to_dataframe(financials)
    if df.empty:
        return None

    is_fin = _is_financial_sector(db, stock_code)

    s_roe   = _score_roe_quality(df, p, is_fin)         * W["roe"] / 25
    s_grow  = _score_profit_growth(df, p)              * W["growth"] / 20
    s_cf    = _score_cashflow(df, p, is_fin)           * W["cashflow"] / 20
    s_hlth  = _score_financial_health(df, p, is_fin)   * W["health"] / 15

    fundamental = round(min(MAX, max(0, s_roe + s_grow + s_cf + s_hlth)), 2)

    result = {
        "score_roe_quality":      round(s_roe, 2),
        "score_profit_growth":    round(s_grow, 2),
        "score_cashflow":         round(s_cf, 2),
        "score_financial_health": round(s_hlth, 2),
        "fundamental_score":      fundamental,
        "is_financial_sector":    is_fin,
    }

    if write_back:
        stock = db.query(Stock).filter_by(code=stock_code).first()
        if stock:
            for k, v in result.items():
                setattr(stock, k, v)
            db.commit()

    return result


def score_all_companies(
    db: Session, as_of_date=None, params: dict = None, limit: Optional[int] = None
) -> dict:
    """批量评分，返回 {code: fundamental_score}"""
    stocks = db.query(Stock).filter_by(is_active=True).all()
    if limit:
        stocks = stocks[:limit]
    results = {}
    for stock in stocks:
        try:
            r = score_company(db, stock.code, as_of_date, params, write_back=False)
            if r:
                results[stock.code] = r["fundamental_score"]
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning(f"公司 {stock.code} 评分失败: {e}")
    return results


# ──────────────────────────────────────────────
# 1. ROE 质量（满分 25）
# ──────────────────────────────────────────────
def _score_roe_quality(df: pd.DataFrame, p: dict, is_financial: bool = False) -> float:
    """
    金融行业的 ROE 特征与一般企业完全不同：
      - 银行 ROE 普遍 8-14%（高杠杆低利差模式）
      - 证券 ROE 普遍 5-12%（轻资产但周期波动大）
      - 保险 ROE 普遍 8-18%（投资收益波动大）
    用 15% 的统一门槛，金融股 ROE 全部归零，不合理。
    金融行业改用 8% 作为达标线（优秀金融机构 10-15%）。
    """
    if is_financial:
        # 金融行业：ROE 达标线 8%，优秀线 15%
        roe_min   = p.get("roe_min_fin", 8.0)
        roe_years = p.get("roe_min_years", settings.ROE_MIN_YEARS)
    else:
        roe_min   = p.get("roe_min",       settings.ROE_MIN)
        roe_years = p.get("roe_min_years", settings.ROE_MIN_YEARS)

    roe_series = df["roe"].dropna()
    if roe_series.empty:
        return 0.0

    # (a) 均值水平（两段式，保证低 ROE 也有区分度）：
    #     ROE < 0%       → 0 分
    #     ROE 0~roe_min  → 0~10 分（线性）
    #     ROE roe_min~roe_min+10 → 10~25 分（线性）
    avg_roe = roe_series.mean()
    if avg_roe <= 0:
        level_score = 0.0
    elif avg_roe < roe_min:
        level_score = avg_roe / roe_min * 10          # 0→0, roe_min→10
    else:
        level_score = min(25, 10 + (avg_roe - roe_min) / 10 * 15)  # roe_min→10, +10→25

    # (b) 稳定性惩罚：标准差每增加 5% 扣 3 分
    #     惩罚不超过 level_score 的 70%，保证正 ROE 公司至少有基础分
    raw_penalty = min(10, roe_series.std() / 5 * 3) if len(roe_series) > 1 else 0
    std_penalty = min(raw_penalty, level_score * 0.7)

    # (c) 连续达标年数奖励
    meet_years = int((roe_series >= roe_min).sum())
    consecutive_bonus = min(10, meet_years / roe_years * 10)

    # (d) 趋势方向（近 3 年是否上升）
    trend_bonus = 0
    if len(roe_series) >= 3:
        slope = np.polyfit(range(len(roe_series[-3:])), roe_series[-3:].values, 1)[0]
        trend_bonus = min(5, max(-5, slope * 2))

    score = level_score - std_penalty + consecutive_bonus + trend_bonus
    # 正 ROE 公司保底 2 分，与"无数据/负ROE = 0"形成区分
    if avg_roe > 0 and score < 2.0:
        score = 2.0
    return round(min(25.0, max(0.0, score)), 2)


# ──────────────────────────────────────────────
# 2. 盈利增长（满分 20）
# ──────────────────────────────────────────────
def _score_profit_growth(df: pd.DataFrame, p: dict) -> float:
    min_growth_years = p.get("min_profit_growth_years", settings.MIN_PROFIT_GROWTH_YEARS)

    profit = df["net_profit"].dropna()
    if len(profit) < 2:
        return 10.0

    yoy = profit.pct_change().dropna()

    # (a) CAGR（近 5-10 年）
    years = min(len(profit) - 1, 10)
    if years > 0 and profit.iloc[0] > 0:
        cagr = (profit.iloc[-1] / profit.iloc[-years - 1]) ** (1 / years) - 1
    else:
        cagr = 0
    cagr_score = min(15, max(0, cagr * 60))  # 25% CAGR → 15分

    # (b) 连续正增长年数
    consecutive = _consecutive_positive(yoy)
    consecutive_score = min(5, consecutive / min_growth_years * 5)

    score = cagr_score + consecutive_score
    return round(min(20.0, max(0.0, score)), 2)


# ──────────────────────────────────────────────
# 3. 现金流健康（满分 20）
# ──────────────────────────────────────────────
def _score_cashflow(df: pd.DataFrame, p: dict, is_financial: bool = False) -> float:
    """
    金融行业（银行/证券/保险）的现金流结构与一般企业完全不同：
      - 银行没有传统意义的"资本开支"，FCF 指标不适用
      - 银行经营现金流波动大（受存贷款变动影响），不能用一般标准衡量
    对金融行业改用净利润增长稳定性作为"盈利质量"代理指标
    """
    if is_financial:
        return _score_cashflow_fin(df, p)

    min_fcf_ratio = p.get("min_fcf_ratio", settings.MIN_FCF_RATIO)

    cf = df["fcf_ratio"].dropna()
    ocf = df["operating_cashflow"].dropna()
    np_series = df["net_profit"].dropna()

    if cf.empty or ocf.empty:
        return 10.0

    # (a) FCF/净利润比率：>1 → 20分，0.5→10分，<0→0分
    avg_fcf_ratio = cf.mean()
    fcf_score = min(20, max(0, avg_fcf_ratio * 20))

    # (b) 经营现金流连续为正
    ocf_positive_years = int((ocf > 0).sum())
    total_years = len(ocf)
    consistency_bonus = min(5, ocf_positive_years / total_years * 5) if total_years > 0 else 0

    # (c) 惩罚：现金流质量差（FCF<净利润50%）
    below_threshold = int((cf < min_fcf_ratio).sum())
    penalty = below_threshold * 1.5

    score = fcf_score + consistency_bonus - penalty
    return round(min(20.0, max(0.0, score)), 2)


def _score_cashflow_fin(df: pd.DataFrame, p: dict) -> float:
    """
    金融行业专用"盈利质量"评分（替代传统现金流指标）
    用净利润的稳定性和连续盈利年数来衡量
    """
    np_series = df["net_profit"].dropna()
    if len(np_series) < 2:
        return 10.0

    # (a) 连续盈利年数占比（满分 10）
    profitable_years = int((np_series > 0).sum())
    total_years = len(np_series)
    profit_ratio = profitable_years / total_years
    continuity_score = min(10.0, profit_ratio * 10.0)

    # (b) 净利润波动率（满分 5）：CV 越小越稳定
    if np_series.mean() > 0:
        cv = np_series.std() / np_series.mean()   # 变异系数
        stability_score = min(5.0, max(0.0, (1.0 - cv) * 5.0))
    else:
        stability_score = 0.0

    # (c) 净利润趋势方向（满分 5）：近 3 年是否上升
    trend_score = 2.5
    if len(np_series) >= 3:
        recent = np_series.iloc[-3:].values
        slope = np.polyfit(range(len(recent)), recent, 1)[0]
        if slope > 0:
            trend_score = min(5.0, 2.5 + slope / abs(recent.mean()) * 10)
        else:
            trend_score = max(0.0, 2.5 + slope / abs(recent.mean()) * 10)

    score = continuity_score + stability_score + trend_score
    return round(min(20.0, max(0.0, score)), 2)


# ──────────────────────────────────────────────
# 4. 财务稳健度（满分 15）
# ──────────────────────────────────────────────
def _score_financial_health(df: pd.DataFrame, p: dict, is_financial: bool = False) -> float:
    """
    金融行业（银行/证券/保险）的负债率 85-95% 是行业常态：
      - 银行的"负债"主要是客户存款，不是经营性借款
      - 证券/保险的负债结构也与一般企业完全不同
    因此对金融行业使用独立的评分标准：
      - 负债率阈值从 65% 提高到 95%（银行正常 90-93%）
      - 不考查利息覆盖倍数（银行的利息是主营业务）
      - 改为考查资本充足率代理指标：负债率趋势稳定性
    """
    if is_financial:
        return _score_financial_health_fin(df, p)

    max_debt = p.get("max_debt_ratio", settings.MAX_DEBT_RATIO * 100)

    debt = df["debt_ratio"].dropna()
    ic   = df["interest_coverage"].dropna()

    if debt.empty:
        return 8.0

    avg_debt = debt.mean()

    # (a) 资产负债率（分段评分，更合理的梯度）：
    #     < 30%  → 10 分（极度保守，如贵州茅台）
    #     30~50% → 6~10 分（健康范围）
    #     50~65% → 2~6 分（偏高但重资产行业常见）
    #     65~80% → 0~2 分（高杠杆，需警惕）
    #     > 80%  → 0 分
    if avg_debt < 30:
        debt_score = 10.0
    elif avg_debt < 50:
        debt_score = 10.0 - (avg_debt - 30) / 20 * 4      # 10→6
    elif avg_debt < max_debt:
        debt_score = 6.0 - (avg_debt - 50) / (max_debt - 50) * 4  # 6→2
    elif avg_debt < 80:
        debt_score = 2.0 - (avg_debt - max_debt) / (80 - max_debt) * 2  # 2→0
    else:
        debt_score = 0.0

    # (b) 利息覆盖倍数：>5→5分，<1→0分
    #     数据缺失时给中性分 2.5（不奖不罚），避免因数据缺失严重压低总分
    if not ic.empty:
        avg_ic = ic.mean()
        ic_score = min(5, max(0, (avg_ic - 1) / 4 * 5))
    else:
        ic_score = 2.5  # 中性分：无数据不惩罚

    score = debt_score + ic_score
    return round(min(15.0, max(0.0, score)), 2)


def _score_financial_health_fin(df: pd.DataFrame, p: dict) -> float:
    """金融行业专用的财务稳健度评分"""
    debt = df["debt_ratio"].dropna()
    if debt.empty:
        return 8.0

    avg_debt = debt.mean()
    latest_debt = debt.iloc[-1]

    # (a) 负债率水平：<90%→满分15，90-93%→10，93-95%→5，>95%→0
    #     银行正常范围 90-93%；>95% 说明杠杆偏高
    if avg_debt < 90:
        debt_score = 10.0
    elif avg_debt < 93:
        debt_score = 10.0 - (avg_debt - 90) / 3 * 5    # 10→5
    elif avg_debt < 95:
        debt_score = 5.0 - (avg_debt - 93) / 2 * 5     # 5→0
    else:
        debt_score = 0.0

    # (b) 负债率稳定性：波动小说明经营审慎（满分 5）
    #     金融机构杠杆率不应剧烈波动
    if len(debt) >= 3:
        debt_std = debt.std()
        stability_score = min(5.0, max(0.0, (3.0 - debt_std) / 3.0 * 5.0))
    else:
        stability_score = 2.5

    score = debt_score + stability_score
    return round(min(15.0, max(0.0, score)), 2)


# ──────────────────────────────────────────────
# 估值评分（满分 20，供 signal_engine 调用）
# ──────────────────────────────────────────────
def score_valuation(db: Session, stock_code: str, as_of_date=None, params: dict = None) -> float:
    """
    PE/PB 历史分位打分
    低分位 = 便宜 = 高分（逆向打分）
    返回 [0, 20]

    v106: 原实现依赖 PriceData.pe_ttm / pb 字段，但 Sina API 未提供该数据，
          导致全库 pe_ttm=NULL，估值分永远返回 10.0 (50/100) — 完全失效。
          现改为按需计算：
              PE_t = close_t × total_shares / net_profit_annual(最近已披露)
              PB_t = close_t × total_shares / total_equity_annual(最近已披露)
          保证任意回测时点用时间隔离后的数据，不产生未来泄露。
    """
    p = params or {}
    pe_max_pct = p.get("pe_percentile_max", 80)  # 允许进入的最高 PE 分位

    stock = db.query(Stock).filter_by(code=stock_code).first()
    if not stock or not stock.total_shares or stock.total_shares <= 0:
        return 10.0   # 无股本数据 → 中性分

    prices = _get_prices(db, stock_code, as_of_date)
    financials = _get_financials(db, stock_code, as_of_date)
    if not prices or not financials:
        return 10.0

    # 按 pub_date 排序的财报列表：只用已披露的
    fins_sorted = [f for f in financials
                   if f.pub_date and (f.net_profit or f.total_equity)]
    fins_sorted.sort(key=lambda f: f.pub_date)
    if not fins_sorted:
        return 10.0

    shares = stock.total_shares
    pe_vals, pb_vals = [], []

    # 双指针：价格按日期递增，财报也递增
    f_idx = 0
    for px in prices:
        # 前移到 px.trade_date 时已披露的最新财报
        while (f_idx + 1 < len(fins_sorted)
               and fins_sorted[f_idx + 1].pub_date <= px.trade_date):
            f_idx += 1
        f = fins_sorted[f_idx]
        # px.trade_date 必须晚于或等于该财报的披露日
        if f.pub_date > px.trade_date:
            continue
        market_cap = px.close * shares
        # PE
        if f.net_profit and f.net_profit > 0:
            pe = market_cap / f.net_profit
            if 0 < pe < 500:          # 过滤极端值（爆雷/数据错误）
                pe_vals.append(pe)
        # PB
        if f.total_equity and f.total_equity > 0:
            pb = market_cap / f.total_equity
            if 0 < pb < 50:
                pb_vals.append(pb)

    if not pe_vals:
        return 10.0

    # PE 历史分位（越低越好）
    pe_percentile = _percentile_rank(pe_vals)
    pe_score = max(0, (pe_max_pct - pe_percentile) / pe_max_pct * 15)

    # PB 历史分位辅助
    pb_score = 5.0
    if pb_vals:
        pb_pct = _percentile_rank(pb_vals)
        pb_score = max(0, (80 - pb_pct) / 80 * 5)

    score = pe_score + pb_score
    return round(min(20.0, max(0.0, score)), 2)


# ──────────────────────────────────────────────
# 工具函数
# ──────────────────────────────────────────────
def _get_financials(db: Session, stock_code: str, as_of_date=None) -> list:
    q = db.query(FinancialData).filter_by(stock_code=stock_code, report_type="annual")
    if as_of_date:
        q = q.filter(FinancialData.pub_date <= as_of_date)
    return q.order_by(FinancialData.period).all()


def _get_prices(db: Session, stock_code: str, as_of_date=None) -> list:
    q = db.query(PriceData).filter_by(stock_code=stock_code)
    if as_of_date:
        q = q.filter(PriceData.trade_date <= as_of_date)
    return q.order_by(PriceData.trade_date).all()


def _to_dataframe(financials: list) -> pd.DataFrame:
    rows = []
    for f in financials:
        rows.append({
            "period":             f.period,
            "revenue":            f.revenue,
            "net_profit":         f.net_profit,
            "gross_margin":       f.gross_margin,
            "net_margin":         f.net_margin,
            "roe":                f.roe,
            "debt_ratio":         f.debt_ratio,
            "interest_coverage":  f.interest_coverage,
            "operating_cashflow": f.operating_cashflow,
            "free_cashflow":      f.free_cashflow,
            "fcf_ratio":          f.fcf_ratio,
        })
    df = pd.DataFrame(rows).sort_values("period").reset_index(drop=True)
    return df


def _percentile_rank(values: list) -> float:
    """当前值（最后一个）在历史分布中的百分位 [0, 100]"""
    arr = np.array(values)
    current = arr[-1]
    return float(np.sum(arr <= current) / len(arr) * 100)


def _consecutive_positive(series: pd.Series) -> int:
    count = 0
    for v in reversed(series.values):
        if v > 0:
            count += 1
        else:
            break
    return count
