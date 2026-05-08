"""
行业评分引擎（满分 100）
四个维度：
  1. 营收增长稳定性  30 分
  2. 盈利能力稳定性  30 分
  3. 抗周期性       20 分
  4. 竞争格局       20 分

输入：行业内所有公司的历史财务数据
输出：Industry 对象各子分 + total_score
"""
import numpy as np
import pandas as pd
from typing import List, Optional
from sqlalchemy.orm import Session

from models.models import FinancialData, Industry, Stock
from config import settings


# ──────────────────────────────────────────────
# 主评分入口
# ──────────────────────────────────────────────
def score_industry(db: Session, industry_code: str,
                   as_of_date=None, params: dict = None) -> Optional[float]:
    """
    计算行业综合评分，写回 Industry 表，返回 total_score
    as_of_date: 回测时传入，防未来数据泄露
    params: 可被回测优化器覆盖的子分权重
    """
    p = params or {}
    W = {
        "revenue_stability":  p.get("ind_w_revenue",     30),
        "profit_stability":   p.get("ind_w_profit",      30),
        "anti_cycle":         p.get("ind_w_anti_cycle",  20),
        "competition":        p.get("ind_w_competition", 20),
    }

    ind = db.query(Industry).filter_by(code=industry_code).first()
    if not ind:
        return None

    # 取行业内股票的财务数据
    financials = _get_industry_financials(db, industry_code, as_of_date)
    if not financials:
        return None

    df = pd.DataFrame([{
        "stock_code":   f.stock_code,          # ← 新增，供抗周期函数用
        "period":       f.period,
        "revenue":      f.revenue,
        "gross_margin": f.gross_margin,
        "net_margin":   f.net_margin,
        "net_profit":   f.net_profit,
        "roe":          f.roe,
    } for f in financials]).dropna(subset=["period"])

    if df.empty:
        return None

    df = df.sort_values("period")

    # ── 子分计算 ──
    s_rev  = _score_revenue_stability(df)  * W["revenue_stability"] / 30
    s_prof = _score_profit_stability(df)   * W["profit_stability"] / 30
    s_cyc  = _score_anti_cycle(df)         * W["anti_cycle"] / 20
    s_comp = _score_competition(db, industry_code) * W["competition"] / 20

    total = s_rev + s_prof + s_cyc + s_comp
    total = round(min(100.0, max(0.0, total)), 2)

    # 写回数据库
    ind.score_revenue_stability = round(s_rev, 2)
    ind.score_profit_stability  = round(s_prof, 2)
    ind.score_anti_cycle        = round(s_cyc, 2)
    ind.score_competition       = round(s_comp, 2)
    ind.total_score             = total
    db.commit()

    return total


def score_all_industries(db: Session, as_of_date=None, params: dict = None) -> dict:
    """批量评分所有行业，返回 {code: score}"""
    industries = db.query(Industry).all()
    results = {}
    for ind in industries:
        try:
            score = score_industry(db, ind.code, as_of_date, params)
            if score is not None:
                results[ind.code] = score
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning(f"行业 {ind.code} 评分失败: {e}")
    return results


# ──────────────────────────────────────────────
# 子维度评分
# ──────────────────────────────────────────────
def _score_revenue_stability(df: pd.DataFrame) -> float:
    """
    营收增长稳定性（满分 30）
    用各公司中位数 YoY 营收增速，避免样本量增加导致总量虚增。
    """
    df = df.copy()
    df["year"] = pd.to_datetime(df["period"]).dt.year
    company_rev = (
        df.dropna(subset=["revenue", "stock_code"])
          .groupby(["stock_code", "year"])["revenue"]
          .sum()
          .reset_index()
          .sort_values(["stock_code", "year"])
    )

    if company_rev.empty:
        return 15.0

    yoy_list = []
    companies_seen = set()
    for code, grp in company_rev.groupby("stock_code"):
        grp = grp.set_index("year")["revenue"]
        yoy = grp.pct_change().dropna()
        if len(yoy) > 0:
            yoy_list.extend(yoy.tolist())
            companies_seen.add(code)

    # 需要至少 3 家公司，避免少数公司主导评分
    if len(companies_seen) < 3 or len(yoy_list) < 3:
        return 15.0

    median_yoy = float(np.median(yoy_list))
    std_yoy    = float(np.std(yoy_list))

    # 中位数增速得分（10% CAGR → ~15分，20% → 25分）
    cagr_score = min(25, max(0, median_yoy * 80))

    # 波动惩罚（标准差大扣分）
    std_penalty = min(10, std_yoy * 30)

    # 连续正增长奖励
    all_positives = sum(1 for v in yoy_list if v > 0) / max(len(yoy_list), 1)
    bonus = min(5, all_positives * 5)

    return round(min(30.0, max(0.0, cagr_score - std_penalty + bonus)), 2)


def _score_profit_stability(df: pd.DataFrame) -> float:
    """
    盈利能力稳定性（满分 30）
    指标：
      - 行业平均毛利率水平
      - 毛利率稳定性（低标准差）
      - 净利率趋势（上升趋势加分）
    """
    gm = df.groupby("period")["gross_margin"].mean().dropna()
    nm = df.groupby("period")["net_margin"].mean().dropna()

    if gm.empty:
        return 15.0

    # 毛利率水平分（>40% 满分）
    avg_gm = gm.mean()
    gm_level_score = min(15, avg_gm / 40 * 15)

    # 毛利率稳定性（标准差越低越高分）
    gm_std = gm.std() if len(gm) > 1 else 0
    gm_stability_score = max(0, 10 - gm_std / 2)

    # 净利率趋势（线性回归斜率）
    nm_trend = 0
    if len(nm) >= 3:
        x = np.arange(len(nm))
        slope = np.polyfit(x, nm.values, 1)[0]
        nm_trend = min(5, max(-5, slope * 10))

    score = gm_level_score + gm_stability_score + nm_trend
    return round(min(30.0, max(0.0, score)), 2)


def _score_anti_cycle(df: pd.DataFrame) -> float:
    """
    抗周期性（满分 20）
    逻辑：在经济收缩年份（2015/2016/2018/2020），
          计算行业内各公司净利润的 YoY 变化，取所有样本的中位数。
          用中位数而非行业总和，避免公司数量增加导致总量虚增。

    评分标准（中位数 YoY）：
      ≥ +5%  → 20 分（逆势增长）
      0~+5%  → 15 分（小幅增长）
      -10~0% → 10 分（基本持平）
      -30~-10% → 5 分（小幅下滑）
      < -30% → 0 分（大幅下滑）
    """
    DOWNTURN_YEARS = [2015, 2016, 2018, 2020]

    df = df.copy()
    df["year"] = pd.to_datetime(df["period"]).dt.year

    # 每只公司、每年的年度净利润
    company_year = (
        df.dropna(subset=["net_profit", "stock_code"])
          .groupby(["stock_code", "year"])["net_profit"]
          .sum()
          .reset_index()
          .sort_values(["stock_code", "year"])
    )

    if company_year.empty:
        return 10.0

    # 计算每只公司在收缩年的 YoY 变化
    yoy_changes = []
    companies_with_data = set()
    for code, grp in company_year.groupby("stock_code"):
        grp = grp.set_index("year")["net_profit"]
        for y in DOWNTURN_YEARS:
            if y in grp.index and (y - 1) in grp.index:
                prev = grp[y - 1]
                curr = grp[y]
                if prev is not None and abs(prev) > 1e4:   # 过滤极小基数
                    yoy_changes.append((curr - prev) / abs(prev))
                    companies_with_data.add(code)

    # 需要至少 3 家公司的数据，避免单家公司主导整个行业评分
    if len(companies_with_data) < 3 or len(yoy_changes) < 3:
        return 10.0   # 数据不足给中性分

    median_yoy = float(np.median(yoy_changes))

    # 分段线性评分
    if median_yoy >= 0.05:
        score = 20.0
    elif median_yoy >= 0.0:
        score = 15.0 + median_yoy / 0.05 * 5.0
    elif median_yoy >= -0.10:
        score = 10.0 + median_yoy / 0.10 * 5.0   # -0.10 → 5, 0 → 10
    elif median_yoy >= -0.30:
        score = 5.0 + (median_yoy + 0.10) / 0.20 * 5.0
    else:
        score = max(0.0, 5.0 + (median_yoy + 0.30) / 0.70 * 5.0)

    return round(min(20.0, max(0.0, score)), 2)


def _score_competition(db: Session, industry_code: str) -> float:
    """
    竞争格局（满分 20）
    用行业内 ROE 分布来近似评估护城河深度：
    - 头部公司 ROE 均值高 → 竞争格局好
    - ROE 离散度低 → 行业稳定
    """
    stocks = db.query(Stock).filter_by(industry_code=industry_code, is_active=True).all()
    if not stocks:
        return 10.0

    roes = []
    for s in stocks:
        latest_fin = db.query(FinancialData)\
            .filter_by(stock_code=s.code, report_type="annual")\
            .order_by(FinancialData.period.desc()).first()
        if latest_fin and latest_fin.roe is not None:
            roes.append(latest_fin.roe)

    if not roes:
        return 10.0

    roes_arr = np.array(roes)
    # 头部 25% ROE 均值
    top_roe = np.percentile(roes_arr, 75)
    roe_score = min(15, top_roe / 25 * 15)   # 25% ROE → 满分
    # 低离散度加分
    cv = np.std(roes_arr) / (np.mean(roes_arr) + 1e-6)
    cv_bonus = max(0, 5 - cv * 5)

    return round(min(20.0, roe_score + cv_bonus), 2)


# ──────────────────────────────────────────────
# 工具函数
# ──────────────────────────────────────────────
def _get_industry_financials(db: Session, industry_code: str, as_of_date=None) -> list:
    """取行业内所有公司的历史财报（严格时间隔离）"""
    stocks = db.query(Stock).filter_by(industry_code=industry_code, is_active=True).all()
    codes = [s.code for s in stocks]
    if not codes:
        return []
    q = db.query(FinancialData).filter(
        FinancialData.stock_code.in_(codes),
        FinancialData.report_type == "annual",
    )
    if as_of_date:
        # 只使用实际发布日 <= as_of_date 的数据
        q = q.filter(FinancialData.pub_date <= as_of_date)
    return q.all()


def _consecutive_positive(series: pd.Series) -> int:
    """从最近一年往前数，连续正增长的年数"""
    count = 0
    for v in reversed(series.values):
        if v > 0:
            count += 1
        else:
            break
    return count
