"""
滚动回测引擎（Walk-Forward Backtesting）

核心设计：
1. 严格时间隔离 — 信号生成只使用 as_of_date 之前已发布的数据
2. 滚动窗口 — 每次用 TRAIN 年训练，VAL 年验证，向前滑动
3. 记录每笔信号的结果用于误差分析
4. 实时进度通过 backtest.progress 模块对外暴露
"""
import logging
from datetime import date
from typing import Optional

import numpy as np
from dateutil.relativedelta import relativedelta
from sqlalchemy.orm import Session

from config import settings
from database import SessionLocal
from engines.signal_engine import generate_signal
from models.models import BacktestRecord, BacktestRun, PriceData, Stock
from backtest.progress import get_progress, reset as reset_progress

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────
# 主回测入口
# ──────────────────────────────────────────────
def run_backtest(
    params: Optional[dict] = None,
    description: str = "",
    version: int = 1,
    sample_codes: Optional[list] = None,
) -> int:
    """
    执行一次完整的滚动回测，返回 BacktestRun.id
    进度实时写入 backtest.progress 单例
    """
    db = SessionLocal()
    try:
        # 确保沪深300基准数据已入库
        ensure_benchmark_data(db)

        run = BacktestRun(
            description=description or f"回测 v{version}",
            version=version,
            params=params or {},
        )
        db.add(run)
        db.commit()
        db.refresh(run)
        run_id = run.id

        # ── 初始化进度 ──
        prog = reset_progress(run_id)
        prog.push_log(f"回测 v{version} 已创建（run_id={run_id}）")

        p = params or {}
        train_years = int(p.get("train_years", settings.BACKTEST_TRAIN_YEARS))
        val_years   = int(p.get("val_years",   settings.BACKTEST_VAL_YEARS))
        hold_months = int(p.get("hold_months", settings.HOLD_MONTHS))
        start_year  = int(p.get("start_year",  settings.BACKTEST_START_YEAR))

        # 计算总窗口数
        window_start = date(start_year, 1, 1)
        end_date     = date.today() - relativedelta(months=hold_months + 1)
        total_windows = 0
        ws = window_start
        while ws + relativedelta(years=train_years + val_years) <= end_date:
            total_windows += 1
            ws += relativedelta(years=1)

        prog.total = total_windows
        prog.stage = f"准备滚动窗口（共 {total_windows} 个）"
        prog.push_log(f"参数：train={train_years}年 val={val_years}年 hold={hold_months}月")
        prog.push_log(f"时间范围：{start_year} → {end_date}")

        # ── 确定样本股票 ──
        if sample_codes:
            target_codes = sample_codes
            _ensure_price_for_stocks(db, target_codes, prog)
        else:
            # 优先使用同时有财务数据 + 价格数据的股票（避免大批量拉取）
            from models.models import FinancialData
            from sqlalchemy import func
            fin_codes = set(
                r[0] for r in
                db.query(FinancialData.stock_code)
                .group_by(FinancialData.stock_code)
                .having(func.count() >= 3)
                .all()
            )
            price_codes = set(
                r[0] for r in
                db.query(PriceData.stock_code)
                .filter(PriceData.stock_code != BENCHMARK_CODE)
                .distinct()
                .all()
            )
            target_codes = sorted(fin_codes & price_codes)
            prog.push_log(
                f"样本股票：{len(target_codes)} 只（已有财务+价格数据）"
            )
            if len(target_codes) < 10:
                # 数据太少，尝试为部分财务股票补充价格
                extra = sorted(fin_codes - price_codes)[:50]
                prog.push_log(f"  数据不足，尝试补充 {len(extra)} 只...")
                _ensure_price_for_stocks(db, extra, prog)
                target_codes = sorted(fin_codes & set(
                    r[0] for r in
                    db.query(PriceData.stock_code)
                    .filter(PriceData.stock_code != BENCHMARK_CODE)
                    .distinct().all()
                ))

        window_results = []
        window_idx = 0

        while window_start + relativedelta(years=train_years + val_years) <= end_date:
            val_start = window_start + relativedelta(years=train_years)
            val_end   = val_start + relativedelta(years=val_years)
            window_idx += 1

            prog.current = window_idx
            prog.pct     = window_idx / max(total_windows, 1) * 90   # 留 10% 给汇总
            prog.stage   = f"窗口 {window_idx}/{total_windows}：{val_start} ~ {val_end}"
            prog.push_log(f"▶ 窗口 {window_idx}：验证期 {val_start} ~ {val_end}")

            window_records = _run_window(
                db, run_id, val_start, val_end, hold_months, params, sample_codes, prog
            )
            if window_records:
                metrics = _calc_window_metrics(window_records)
                metrics["window"] = str(val_start)
                window_results.append(metrics)
                prog.push_log(
                    f"  ✓ 信号 {metrics['n_buy']+metrics['n_sell']} 条 "
                    f"| 胜率 {metrics['win_rate']:.1f}%"
                )

            window_start += relativedelta(years=1)

        # ── 汇总阶段 ──
        prog.stage = "计算汇总指标..."
        prog.pct   = 92
        prog.push_log("正在计算 IC / Sharpe / 最大回撤...")

        all_records = db.query(BacktestRecord).filter_by(run_id=run_id).all()
        summary     = _calc_summary_metrics(all_records)
        false_buys  = _analyze_false_signals(all_records, ("BUY", "STRONG_BUY"))
        false_sells = _analyze_false_signals(all_records, ("SELL", "STRONG_SELL"))

        run = db.query(BacktestRun).filter_by(id=run_id).first()
        run.win_rate          = summary.get("win_rate")
        run.sell_accuracy     = summary.get("sell_accuracy")
        run.annualized_alpha  = summary.get("annualized_alpha")
        run.ic_mean           = summary.get("ic_mean")
        run.ic_ir             = summary.get("ic_ir")
        run.sharpe_ratio      = summary.get("sharpe_ratio")
        run.max_drawdown      = summary.get("max_drawdown")
        run.composite_score   = summary.get("composite_score")
        run.window_results    = window_results
        run.false_buy_patterns  = false_buys
        run.false_sell_patterns = false_sells
        db.commit()

        # ── 完成 ──
        import time
        prog.pct          = 100
        prog.stage        = "回测完成"
        prog.status       = "done"
        prog.finished_at  = time.time()
        prog.win_rate     = run.win_rate
        prog.ic           = run.ic_mean
        prog.push_log(
            f"✅ 完成！买入胜率={run.win_rate:.1f}%  "
            f"IC={run.ic_mean:.4f}  Sharpe={run.sharpe_ratio:.2f}"
        )

        logger.info(
            f"回测完成 run_id={run_id} "
            f"win_rate={run.win_rate:.1f}% IC={run.ic_mean:.4f}"
        )
        return run_id

    except Exception as e:
        import time
        prog = get_progress()
        prog.status    = "error"
        prog.error_msg = str(e)
        prog.finished_at = time.time()
        prog.push_log(f"❌ 错误：{e}")
        logger.error(f"回测失败: {e}")
        raise
    finally:
        db.close()


# ──────────────────────────────────────────────
# 单窗口回测
# ──────────────────────────────────────────────
def _ensure_price_for_stocks(db: Session, stock_codes: list, prog=None):
    """回测前按需拉取样本股票的价格数据"""
    import time
    from data.fetcher import fetch_price_history
    missing = []
    for code in stock_codes:
        cnt = db.query(PriceData).filter_by(stock_code=code).count()
        if cnt < 10:
            missing.append(code)
    if not missing:
        if prog:
            prog.push_log(f"  ✓ 所有 {len(stock_codes)} 只股票行情数据已就绪")
        return
    if prog:
        prog.push_log(f"  ⬇ 需为 {len(missing)} 只股票拉取行情数据（使用新浪 API）...")
    fetched_ok = 0
    for i, code in enumerate(missing):
        try:
            n = fetch_price_history(db, code)
            if n > 0:
                fetched_ok += 1
        except Exception as e:
            logger.debug(f"行情 {code}: {e}")
        # 礼貌性延迟，避免被限流（新浪 API 较宽松，0.5s 即可）
        time.sleep(0.5)
        if prog and ((i + 1) % 10 == 0 or i == len(missing) - 1):
            prog.push_log(f"    行情进度 {i+1}/{len(missing)}，成功 {fetched_ok} 只")
    if prog:
        prog.push_log(f"  ✓ 行情拉取完成：{fetched_ok}/{len(missing)} 只成功")


def _run_window(
    db: Session,
    run_id: int,
    val_start: date,
    val_end: date,
    hold_months: int,
    params: Optional[dict],
    sample_codes: Optional[list],
    prog=None,
) -> list:
    records = []
    check_dates = _quarterly_dates(val_start, val_end)

    stocks = db.query(Stock).filter_by(is_active=True).all()
    if sample_codes:
        stocks = [s for s in stocks if s.code in sample_codes]

    total_steps = len(check_dates) * len(stocks)
    step = 0

    for check_date in check_dates:
        if prog:
            prog.push_log(f"  检查日 {check_date}（{len(stocks)} 只）")

        for stock in stocks:
            step += 1
            try:
                result = generate_signal(
                    db, stock.code,
                    as_of_date=check_date,
                    params=params,
                    write_back=False,
                )
                if result is None:
                    continue

                signal = result["signal"]
                if signal not in ("BUY", "SELL"):
                    continue

                exit_date = check_date + relativedelta(months=hold_months)
                if exit_date > date.today():
                    continue

                entry_price = _get_price(db, stock.code, check_date)
                exit_price  = _get_price(db, stock.code, exit_date)
                bench_entry = _get_benchmark_price(db, check_date)
                bench_exit  = _get_benchmark_price(db, exit_date)

                if not entry_price or not exit_price:
                    continue

                stock_ret  = (exit_price - entry_price) / entry_price
                bench_ret  = (
                    (bench_exit - bench_entry) / bench_entry
                    if bench_entry and bench_exit and bench_entry != 0 else 0.0
                )
                excess_ret = stock_ret - bench_ret
                # 5 等级：STRONG_BUY/BUY 视为"看多"，STRONG_SELL/SELL 视为"看空"
                is_buy_signal  = signal in ("BUY", "STRONG_BUY")
                is_win = excess_ret > 0 if is_buy_signal else excess_ret < 0

                rec = BacktestRecord(
                    run_id=run_id,
                    stock_code=stock.code,
                    signal_date=check_date,
                    signal=signal,
                    composite_score=result["composite_score"],
                    entry_price=entry_price,
                    exit_price=exit_price,
                    hold_months=hold_months,
                    stock_return=round(stock_ret * 100, 4),
                    bench_return=round(bench_ret * 100, 4),
                    excess_return=round(excess_ret * 100, 4),
                    is_win=is_win,
                    sub_scores=result.get("sub_scores"),
                    news_summary=result.get("reason"),
                )
                db.add(rec)
                records.append(rec)

            except Exception as e:
                logger.debug(f"信号 {stock.code}@{check_date}: {e}")

        db.commit()

    return records


# ──────────────────────────────────────────────
# 指标计算
# ──────────────────────────────────────────────
def _calc_window_metrics(records: list) -> dict:
    buys  = [r for r in records if r.signal in ("BUY", "STRONG_BUY")  and r.is_win is not None]
    sells = [r for r in records if r.signal in ("SELL", "STRONG_SELL") and r.is_win is not None]
    return {
        "n_buy":      len(buys),
        "n_sell":     len(sells),
        "win_rate":   _safe_mean([r.is_win for r in buys]) * 100,
        "sell_acc":   _safe_mean([r.is_win for r in sells]) * 100,
        "avg_excess": _safe_mean([r.excess_return for r in buys + sells]),
    }


def _calc_summary_metrics(records: list) -> dict:
    buys  = [r for r in records if r.signal in ("BUY", "STRONG_BUY")  and r.is_win is not None]
    sells = [r for r in records if r.signal in ("SELL", "STRONG_SELL") and r.is_win is not None]

    win_rate      = _safe_mean([r.is_win for r in buys]) * 100
    sell_accuracy = _safe_mean([r.is_win for r in sells]) * 100
    excess_returns = [r.excess_return for r in buys + sells if r.excess_return is not None]

    scores = [r.composite_score for r in buys + sells if r.composite_score is not None]
    ic_val = 0.0
    ic_ir  = 0.0
    if len(scores) >= 10 and len(excess_returns) >= 10:
        from scipy.stats import spearmanr
        ic_val, _ = spearmanr(scores[:len(excess_returns)], excess_returns[:len(scores)])
        ic_val = float(ic_val) if not np.isnan(ic_val) else 0.0
        ic_ir  = ic_val / 0.1

    alpha  = float(np.mean(excess_returns)) if excess_returns else 0.0
    std    = float(np.std(excess_returns))  if len(excess_returns) > 1 else 1.0
    sharpe = alpha / std if std != 0 else 0.0

    buy_rets = [r.stock_return for r in buys if r.stock_return is not None]
    max_dd   = _max_drawdown(buy_rets)
    ann_alpha = alpha * (12 / max(settings.HOLD_MONTHS, 1))

    opt_score = (
        win_rate / 100 * settings.OPT_W_WIN_RATE +
        max(0, ic_val) * settings.OPT_W_IC +
        max(0, sharpe) * settings.OPT_W_SHARPE
    )

    return {
        "win_rate":         round(win_rate, 2),
        "sell_accuracy":    round(sell_accuracy, 2),
        "annualized_alpha": round(ann_alpha, 4),
        "ic_mean":          round(ic_val, 6),
        "ic_ir":            round(ic_ir, 4),
        "sharpe_ratio":     round(sharpe, 4),
        "max_drawdown":     round(max_dd, 4),
        "composite_score":  round(opt_score, 6),
    }


def _analyze_false_signals(records: list, signal: str) -> list:
    # signal 参数支持单值或多值（"BUY" / ("BUY","STRONG_BUY")）
    if isinstance(signal, str):
        match_set = {signal}
    else:
        match_set = set(signal)
    false_records = [r for r in records if r.signal in match_set and r.is_win is False]
    if not false_records:
        return []
    patterns = {}
    for rec in false_records:
        if not rec.sub_scores:
            continue
        dims = {
            "fundamental": rec.sub_scores.get("fundamental", 50),
            "valuation":   rec.sub_scores.get("valuation", 50),
            "sentiment":   rec.sub_scores.get("sentiment", 50),
            "macro":       rec.sub_scores.get("macro", 50),
        }
        weakest = min(dims, key=dims.get)
        patterns[weakest] = patterns.get(weakest, 0) + 1
    return [{"pattern": k, "count": v} for k, v in sorted(patterns.items(), key=lambda x: -x[1])]


# ──────────────────────────────────────────────
# 工具函数
# ──────────────────────────────────────────────
def _get_price(db: Session, code: str, target_date: date) -> Optional[float]:
    row = (
        db.query(PriceData)
        .filter(PriceData.stock_code == code, PriceData.trade_date <= target_date)
        .order_by(PriceData.trade_date.desc())
        .first()
    )
    return row.close if row else None


BENCHMARK_CODE = "IDX_000300"   # 沪深300 在 PriceData 中的虚拟代码

def _get_benchmark_price(db: Session, target_date: date) -> Optional[float]:
    return _get_price(db, BENCHMARK_CODE, target_date)


def ensure_benchmark_data(db: Session):
    """确保沪深300指数数据已入库（回测必需）"""
    existing = db.query(PriceData).filter_by(stock_code=BENCHMARK_CODE).count()
    if existing > 100:
        return   # 已有数据
    try:
        import akshare as ak
        import concurrent.futures as _cf

        # 使用线程超时避免 hang
        _exec = _cf.ThreadPoolExecutor(max_workers=1)
        fut = _exec.submit(ak.stock_zh_index_daily, symbol="sh000300")
        df = fut.result(timeout=60)

        if df is None or df.empty:
            logger.warning("沪深300指数数据为空")
            return
        from datetime import datetime as _dt, date as _date
        saved = 0
        for _, row in df.iterrows():
            raw = row.get("date") or row["date"]
            # stock_zh_index_daily 返回 datetime.date 对象
            if isinstance(raw, _date) and not isinstance(raw, _dt):
                trade_date = raw          # 直接使用 date 对象
            elif isinstance(raw, _dt):
                trade_date = raw.date()   # datetime → date
            elif isinstance(raw, str):
                trade_date = _dt.strptime(raw[:10], "%Y-%m-%d").date()
            else:
                continue
            # 跳过已存在的记录
            exists = db.query(PriceData).filter_by(
                stock_code=BENCHMARK_CODE, trade_date=trade_date
            ).first()
            if exists:
                continue
            db.add(PriceData(
                stock_code=BENCHMARK_CODE,
                trade_date=trade_date,
                open=float(row.get("open", 0) or 0),
                high=float(row.get("high", 0) or 0),
                low=float(row.get("low", 0) or 0),
                close=float(row.get("close", 0) or 0),
                volume=float(row.get("volume", 0) or 0),
            ))
            saved += 1
        db.commit()
        logger.info(f"沪深300指数数据：写入 {saved} 条（共 {existing + saved} 条）")
    except Exception as e:
        logger.error(f"拉取沪深300失败: {e}")


def _quarterly_dates(start: date, end: date) -> list:
    dates, current = [], start
    while current <= end:
        dates.append(current)
        current += relativedelta(months=3)
    return dates


def _safe_mean(lst: list) -> float:
    vals = [v for v in lst if v is not None]
    return float(np.mean(vals)) if vals else 0.0


def _max_drawdown(returns: list) -> float:
    """
    用于截面回测：报告最大单次亏损（p5 分位数）。
    截面信号不是连续持仓，不能用累积净值算真实最大回撤。
    """
    if not returns:
        return 0.0
    # 取损失最大的 5th percentile 作为风险指标
    losses = [r for r in returns if r < 0]
    if not losses:
        return 0.0
    return float(abs(np.percentile(losses, 5)))
