"""
回测指标评估模块
提供详细的指标分解和可视化数据
"""
from typing import Optional
from sqlalchemy.orm import Session
from models.models import BacktestRecord, BacktestRun

TOP_CASES_LIMIT = 10
TOP_CASES_MAX_PER_DATE = 3


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

    # Top 10 最优 / 最差案例：每个信号日最多展示 3 条，避免事件日霸屏。
    sorted_by_excess = sorted(
        [r for r in buy_records if r.excess_return is not None],
        key=lambda r: r.excess_return,
        reverse=True,
    )
    top_wins = [
        _record_to_dict(r)
        for r in _pick_diversified_records(sorted_by_excess)
    ]
    top_loses = [
        _record_to_dict(r)
        for r in _pick_diversified_records(reversed(sorted_by_excess))
    ]
    date_concentration = _signal_date_concentration(buy_records)
    top_date = date_concentration[0] if date_concentration else {}

    return {
        "run_id":          run.id,
        "version":         run.version,
        "signal_type":     run.signal_type or "long",
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
            "max_buy_same_date": top_date.get("n", 0),
            "top_buy_date_share": top_date.get("share_pct", 0),
        },
        "window_results":       run.window_results or [],
        "false_buy_patterns":   run.false_buy_patterns or [],
        "false_sell_patterns":  run.false_sell_patterns or [],
        "monthly_performance":  monthly_perf,
        "signal_date_concentration": date_concentration,
        "top_wins":             top_wins,
        "top_losses":           top_loses,
    }


def compare_runs(db: Session, signal_type: Optional[str] = None) -> list:
    """列出所有回测版本的对比摘要。signal_type 传 'long' / 'short' 可过滤。"""
    q = db.query(BacktestRun)
    if signal_type:
        q = q.filter(BacktestRun.signal_type == signal_type)
    runs = q.order_by(BacktestRun.version).all()
    return [
        {
            "run_id":          r.id,
            "version":         r.version,
            "signal_type":     r.signal_type or "long",
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


def _pick_diversified_records(records) -> list:
    from collections import defaultdict

    picked = []
    per_date = defaultdict(int)
    for r in records:
        key = r.signal_date
        if per_date[key] >= TOP_CASES_MAX_PER_DATE:
            continue
        picked.append(r)
        per_date[key] += 1
        if len(picked) >= TOP_CASES_LIMIT:
            break
    return picked


def _signal_date_concentration(records: list) -> list:
    from collections import defaultdict

    buckets = defaultdict(list)
    for r in records:
        if r.signal_date and r.excess_return is not None:
            buckets[r.signal_date].append(r)

    total = sum(len(v) for v in buckets.values())
    rows = []
    for signal_date, items in buckets.items():
        n = len(items)
        wins = sum(1 for r in items if r.is_win)
        avg_excess = sum(r.excess_return for r in items) / n
        rows.append({
            "signal_date": str(signal_date),
            "n": n,
            "share_pct": round(n / total * 100, 2) if total else 0,
            "win_rate": round(wins / n * 100, 2) if n else 0,
            "avg_excess": round(avg_excess, 2),
        })
    return sorted(rows, key=lambda x: (-x["n"], x["signal_date"]))[:20]


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
