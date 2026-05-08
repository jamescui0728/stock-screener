"""
贝叶斯参数优化器
目标：最大化 win_rate * W1 + IC * W2 + Sharpe * W3
      使买入胜率逼近 85%

流程：
  1. 定义参数空间（权重/阈值/准入线）
  2. 用 bayesian-optimization 库采样
  3. 每次采样调用 run_backtest 获取 composite_score
  4. 收敛后返回最优参数集
"""
import logging
from typing import Optional
from bayes_opt import BayesianOptimization
from config import settings
from backtest.engine import run_backtest
from database import SessionLocal
from models.models import BacktestRun

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────
# 参数空间定义
# ──────────────────────────────────────────────
PARAM_BOUNDS = {
    # 综合评分权重（四项之和≈1，通过归一化处理）
    "fundamental_weight":   (0.25, 0.55),
    "valuation_weight":     (0.15, 0.35),
    "sentiment_weight":     (0.05, 0.25),
    "macro_weight":         (0.05, 0.20),

    # 信号阈值
    "buy_threshold":        (60.0, 82.0),
    "sell_threshold":       (35.0, 55.0),

    # 公司准入
    "roe_min":              (10.0, 20.0),
    "roe_min_years":        (4.0,  10.0),   # 连续年数（取整）
    "min_profit_growth_years": (3.0, 8.0),
    "min_fcf_ratio":        (0.5,  1.2),
    "max_debt_ratio":       (0.45, 0.80),

    # 估值
    "pe_percentile_max":    (60.0, 90.0),

    # 行业权重（子分满分之和固定，用比例）
    "ind_w_revenue":        (20.0, 40.0),
    "ind_w_profit":         (20.0, 40.0),
    "ind_w_anti_cycle":     (10.0, 30.0),
}


# ──────────────────────────────────────────────
# 主优化函数
# ──────────────────────────────────────────────
def optimize(
    n_iter: int = 30,
    init_points: int = 5,
    sample_codes: Optional[list] = None,
    version_offset: int = 1,
) -> dict:
    """
    运行贝叶斯优化
    n_iter:       贝叶斯采样轮数
    init_points:  随机初始化点数
    sample_codes: 仅使用部分股票（加速调试）
    返回最优参数字典
    """
    best_params_store = {}

    def objective(**raw_params):
        params = _normalize_params(raw_params)
        version = version_offset + len(best_params_store)

        try:
            run_id = run_backtest(
                params=params,
                description=f"优化轮次 #{version}",
                version=version,
                sample_codes=sample_codes,
            )
            db = SessionLocal()
            try:
                run = db.query(BacktestRun).filter_by(id=run_id).first()
                score = run.composite_score if run and run.composite_score else 0.0
                win_rate = run.win_rate if run else 0.0
            finally:
                db.close()

            logger.info(
                f"[优化] 轮次#{version} composite={score:.4f} "
                f"win_rate={win_rate:.1f}%"
            )

            # 记录到本地
            best_params_store[version] = {
                "params":    params,
                "score":     score,
                "win_rate":  win_rate,
            }

            return float(score)

        except Exception as e:
            logger.error(f"[优化] 轮次#{version} 失败: {e}")
            return 0.0

    optimizer = BayesianOptimization(
        f=objective,
        pbounds=PARAM_BOUNDS,
        random_state=42,
        verbose=2,
    )

    optimizer.maximize(
        init_points=init_points,
        n_iter=n_iter,
    )

    best_raw = optimizer.max["params"]
    best_params = _normalize_params(best_raw)

    logger.info(f"[优化] 最优参数: {best_params}")
    logger.info(f"[优化] 最优目标分: {optimizer.max['target']:.4f}")

    # 找到对应的 win_rate
    best_entry = max(best_params_store.values(), key=lambda x: x["score"])
    logger.info(f"[优化] 最优 win_rate: {best_entry.get('win_rate', 'N/A'):.1f}%")

    return best_params


# ──────────────────────────────────────────────
# 参数归一化
# ──────────────────────────────────────────────
def _normalize_params(raw: dict) -> dict:
    """
    1. 权重归一化（四项之和 = 1）
    2. 整数参数取整
    """
    p = dict(raw)

    # 归一化权重
    w_keys = ["fundamental_weight", "valuation_weight", "sentiment_weight", "macro_weight"]
    total_w = sum(p.get(k, 0.25) for k in w_keys)
    if total_w > 0:
        for k in w_keys:
            p[k] = round(p.get(k, 0.25) / total_w, 4)

    # 行业权重（不强制归一化，子分满分固定）
    for k in ["ind_w_revenue", "ind_w_profit", "ind_w_anti_cycle"]:
        if k in p:
            p[k] = round(p[k], 1)

    # 整数参数
    for k in ["roe_min_years", "min_profit_growth_years"]:
        if k in p:
            p[k] = int(round(p[k]))

    # 其他浮点参数四舍五入
    for k in ["roe_min", "min_fcf_ratio", "max_debt_ratio",
              "buy_threshold", "sell_threshold", "pe_percentile_max"]:
        if k in p:
            p[k] = round(p[k], 2)

    return p


# ──────────────────────────────────────────────
# 快速网格搜索（用于小范围微调）
# ──────────────────────────────────────────────
def grid_search_thresholds(
    sample_codes: Optional[list] = None,
    version_offset: int = 100,
) -> dict:
    """
    只对 buy_threshold / sell_threshold 做网格搜索
    用于在贝叶斯优化完成后做精细微调
    """
    import itertools
    buy_thresholds  = [65, 68, 70, 72, 75]
    sell_thresholds = [40, 45, 50]
    best_score = -1.0
    best_params = {}

    for i, (bt, st) in enumerate(itertools.product(buy_thresholds, sell_thresholds)):
        params = {"buy_threshold": bt, "sell_threshold": st}
        run_id = run_backtest(
            params=params,
            description=f"网格搜索 buy={bt} sell={st}",
            version=version_offset + i,
            sample_codes=sample_codes,
        )
        db = SessionLocal()
        try:
            run = db.query(BacktestRun).filter_by(id=run_id).first()
            score = run.composite_score or 0.0
            logger.info(f"网格: buy={bt} sell={st} → {score:.4f}")
            if score > best_score:
                best_score = score
                best_params = params
        finally:
            db.close()

    return best_params
