"""
回测指标评估模块
提供详细的指标分解和可视化数据
"""
from typing import Optional
from sqlalchemy.orm import Session
from models.models import BacktestRecord, BacktestRun


def get_run_report(db: Session, run_id: int) -> Optional[dict]:
    """生成完整回测报告（供 API 返回前端）"""
    run = db.query(BacktestRun).filter_by(id=run_id).first()
    if not run:
        return None

    records = db.query(BacktestRecord).filter_by(run_id=run_id).all()
    buy_records  = [r for r in records if r.signal in ("BUY", "STRONG_BUY")]
    sell_records = [r for r in records if r.signal in ("SELL", "STRONG_SELL")]

    # 按时间聚合
    monthly_perf = _monthly_performance(buy_records)

    # Top 10 最优 / 最差案例
    sorted_by_excess = sorted(
        [r for r in buy_records if r.excess_return is not None],
        key=lambda r: r.excess_return,
        reverse=True,
    )
    top_wins  = [_record_to_dict(r) for r in sorted_by_excess[:10]]
    top_loses = [_record_to_dict(r) for r in sorted_by_excess[-10:]]

    return {
        "run_id":          run.id,
        "version":         run.version,
        "run_at":          str(run.run_at),
        "description":     run.description,
        "params":          run.params,
        "summary": {
            "win_rate":         run.win_rate,
            "sell_accuracy":    run.sell_accuracy,
            "annualized_alpha": run.annualized_alpha,
            "ic_mean":          run.ic_mean,
            "ic_ir":            run.ic_ir,
            "sharpe_ratio":     run.sharpe_ratio,
            "max_drawdown":     run.max_drawdown,
            "composite_score":  run.composite_score,
            "n_buy_signals":    len(buy_records),
            "n_sell_signals":   len(sell_records),
            "target_win_rate":  85.0,
        },
        "window_results":       run.window_results or [],
        "false_buy_patterns":   run.false_buy_patterns or [],
        "false_sell_patterns":  run.false_sell_patterns or [],
        "monthly_performance":  monthly_perf,
        "top_wins":             top_wins,
        "top_losses":           top_loses,
    }


def compare_runs(db: Session) -> list:
    """列出所有回测版本的对比摘要"""
    runs = db.query(BacktestRun).order_by(BacktestRun.version).all()
    return [
        {
            "run_id":          r.id,
            "version":         r.version,
            "run_at":          str(r.run_at),
            "win_rate":        r.win_rate,
            "ic_mean":         r.ic_mean,
            "sharpe_ratio":    r.sharpe_ratio,
            "composite_score": r.composite_score,
            "description":     r.description,
        }
        for r in runs
    ]


def _monthly_performance(records: list) -> list:
    from collections import defaultdict
    monthly = defaultdict(list)
    for r in records:
        if r.signal_date and r.excess_return is not None:
            key = r.signal_date.strftime("%Y-%m")
            monthly[key].append(r.excess_return)
    return [
        {"month": k, "avg_excess": round(sum(v) / len(v), 2), "n": len(v)}
        for k, v in sorted(monthly.items())
    ]


def _record_to_dict(r: BacktestRecord) -> dict:
    return {
        "stock_code":    r.stock_code,
        "signal_date":   str(r.signal_date),
        "composite_score": r.composite_score,
        "stock_return":  r.stock_return,
        "bench_return":  r.bench_return,
        "excess_return": r.excess_return,
        "is_win":        r.is_win,
        "sub_scores":    r.sub_scores,
    }
