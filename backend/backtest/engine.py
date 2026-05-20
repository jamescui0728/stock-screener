"""
滚动回测引擎（Walk-Forward Backtesting）

核心设计：
1. 严格时间隔离 — 信号生成只使用 as_of_date 之前已发布的数据
2. 滚动窗口 — 每次用 TRAIN 年训练，VAL 年验证，向前滑动
3. 记录每笔信号的结果用于误差分析
4. 实时进度通过 backtest.progress 模块对外暴露
"""
import logging
from datetime import date, timedelta
from typing import Optional

import numpy as np
from dateutil.relativedelta import relativedelta
from sqlalchemy.orm import Session

from config import settings
from database import SessionLocal
from engines.signal_engine import generate_signal
from engines.short_signal_engine import (
    generate_short_signal,
    compute_recent_price_cache,
    compute_stock_returns_from_price_cache,
    compute_industry_returns_at,
    compute_industry_avg_gm,
    score_market_trend,
    _apply_cross_sectional_ranks,
)
from models.models import BacktestRecord, BacktestRun, PriceData, Stock
from backtest.progress import get_progress, reset as reset_progress

# 长期信号的 sub_scores 维度（用于误差归因饼图）
LONG_DIMS  = ("fundamental", "valuation", "sentiment", "macro")
# 短期信号的 sub_scores 维度
SHORT_DIMS = (
    "momentum", "volprice", "macro", "tech", "news_heat",
    "industry_relative", "pricing_power", "market_trend",
)

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────
# 主回测入口
# ──────────────────────────────────────────────
def run_backtest(
    params: Optional[dict] = None,
    description: str = "",
    version: int = 1,
    sample_codes: Optional[list] = None,
    signal_type: str = "long",
) -> int:
    """
    执行一次完整的滚动回测，返回 BacktestRun.id

    signal_type:
      "long"  — 调用 generate_signal（基本面+估值+舆情+宏观），季度检查，默认持有 365 天
      "short" — 调用 generate_short_signal（动量+量价+宏观+科技+新闻），周检查，默认持有 14 天
    """
    if signal_type not in ("long", "short"):
        raise ValueError(f"signal_type 必须是 'long' / 'short'，收到 {signal_type!r}")

    db = SessionLocal()
    try:
        # 确保沪深300基准数据已入库
        ensure_benchmark_data(db)

        run = BacktestRun(
            description=description or f"{'短期' if signal_type == 'short' else ''}回测 v{version}",
            version=version,
            signal_type=signal_type,
            params=params or {},
        )
        db.add(run)
        db.commit()
        db.refresh(run)
        run_id = run.id

        # ── 初始化进度 ──
        prog = reset_progress(run_id)
        prog.push_log(f"{'短期' if signal_type == 'short' else '长期'}回测 v{version} 已创建（run_id={run_id}）")

        p = params or {}
        # 长/短期的训练/验证窗口默认值不同 — 短期数据从 2022 起算，
        # 若沿用长期的 train=5/val=2（共 7 年）会得到 0 个窗口
        if signal_type == "short":
            default_train = settings.BACKTEST_TRAIN_YEARS_SHORT
            default_val   = settings.BACKTEST_VAL_YEARS_SHORT
        else:
            default_train = settings.BACKTEST_TRAIN_YEARS
            default_val   = settings.BACKTEST_VAL_YEARS
        # 用 float 支持半年（短期默认 1.5 / 0.5）
        train_years = float(p.get("train_years", default_train))
        val_years   = float(p.get("val_years",   default_val))
        start_year  = int(p.get("start_year",    settings.BACKTEST_START_YEAR))

        # 持有天数 + 检查频率（按 signal_type 切换默认值）
        default_hold = (settings.BACKTEST_HOLD_DAYS_SHORT if signal_type == "short"
                        else settings.BACKTEST_HOLD_DAYS_LONG)
        default_freq = (settings.BACKTEST_CHECK_FREQ_DAYS_SHORT if signal_type == "short"
                        else settings.BACKTEST_CHECK_FREQ_DAYS_LONG)
        hold_days       = int(p.get("hold_days",       default_hold))
        check_freq_days = int(p.get("check_freq_days", default_freq))

        # 短期回测不需要早到 2014：节省样本量、聚焦近期市场
        # 长期保留 start_year（含训练 5 年验证 2 年的窗口设计）
        if signal_type == "short" and "start_year" not in p:
            start_year = max(start_year, date.today().year - 4)   # 近 4 年

        # 计算总窗口数
        # 用 months 来支持小数年（短期默认 train=1.5y / val=0.5y）；
        # 步长保持 1 年（窗口滚动），太密集 backtest 会爆炸
        window_start  = date(start_year, 1, 1)
        end_date      = date.today() - timedelta(days=hold_days + 30)
        total_months  = int(round((train_years + val_years) * 12))
        train_months  = int(round(train_years * 12))
        val_months    = int(round(val_years   * 12))
        total_windows = 0
        ws = window_start
        while ws + relativedelta(months=total_months) <= end_date:
            total_windows += 1
            ws += relativedelta(years=1)

        prog.total = total_windows
        prog.stage = f"准备滚动窗口（共 {total_windows} 个）"
        prog.push_log(
            f"参数：signal_type={signal_type} train={train_years}年 "
            f"val={val_years}年 hold={hold_days}天 检查间隔={check_freq_days}天"
        )
        prog.push_log(f"时间范围：{start_year} → {end_date}")

        # ── 确定样本股票 ──
        if sample_codes:
            target_codes = sample_codes
            _ensure_price_for_stocks(db, target_codes, prog)
        elif signal_type == "short":
            # 短期回测：只要有足够价格数据即可（短期信号不依赖财务）
            price_codes = sorted(set(
                r[0] for r in
                db.query(PriceData.stock_code)
                .filter(PriceData.stock_code != BENCHMARK_CODE)
                .distinct().all()
            ))
            target_codes = price_codes
            prog.push_log(f"样本股票：{len(target_codes)} 只（短期信号仅需价格数据）")
        else:
            # 长期回测：优先使用同时有财务数据 + 价格数据的股票
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

        while window_start + relativedelta(months=total_months) <= end_date:
            val_start = window_start + relativedelta(months=train_months)
            val_end   = val_start + relativedelta(months=val_months)
            window_idx += 1

            prog.current = window_idx
            prog.pct     = window_idx / max(total_windows, 1) * 90   # 留 10% 给汇总
            prog.stage   = f"窗口 {window_idx}/{total_windows}：{val_start} ~ {val_end}"
            prog.push_log(f"▶ 窗口 {window_idx}：验证期 {val_start} ~ {val_end}")

            window_records = _run_window(
                db, run_id, val_start, val_end,
                hold_days, check_freq_days, params, sample_codes, prog,
                signal_type=signal_type,
                target_codes=target_codes,
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
        summary     = _calc_summary_metrics(all_records, hold_days=hold_days)
        false_buys  = _analyze_false_signals(all_records, ("BUY", "STRONG_BUY"),  signal_type=signal_type)
        false_sells = _analyze_false_signals(all_records, ("SELL", "STRONG_SELL"), signal_type=signal_type)

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
    hold_days: int,
    check_freq_days: int,
    params: Optional[dict],
    sample_codes: Optional[list],
    prog=None,
    signal_type: str = "long",
    target_codes: Optional[list] = None,
) -> list:
    records = []
    check_dates = _check_dates(val_start, val_end, check_freq_days)

    stocks = db.query(Stock).filter_by(is_active=True).all()
    if sample_codes:
        stocks = [s for s in stocks if s.code in sample_codes]
    elif target_codes is not None:
        keep = set(target_codes)
        stocks = [s for s in stocks if s.code in keep]

    # v202g: pricing_power 权重为 0 时跳过 industry_gm 预计算（省 90% 时间）
    from config import settings as _s
    need_gm = signal_type == "short" and _s.SHORT_PRICING_POWER_WEIGHT > 0

    for check_date in check_dates:
        if prog:
            prog.push_log(f"  检查日 {check_date}（{len(stocks)} 只）")

        if signal_type == "short":
            # 只有需要 pricing_power 时才 per-check_date 重算（避免 look-ahead）
            industry_gm = compute_industry_avg_gm(db, as_of_date=check_date) if need_gm else None
            _run_short_check_date(
                db, run_id, check_date, hold_days, stocks, params, records,
                industry_gm=industry_gm,
            )
        else:
            _run_long_check_date(
                db, run_id, check_date, hold_days, stocks, params, records
            )

        db.commit()

    return records


def _run_short_check_date(db, run_id, check_date, hold_days, stocks, params, records,
                          industry_gm=None):
    """短期回测：两阶段（raw → 截面排名 → BacktestRecord）"""
    price_cache = compute_recent_price_cache(db, check_date)
    stock_returns = compute_stock_returns_from_price_cache(price_cache)
    industry_price_cache = compute_recent_price_cache(db, check_date, lookback_days=45)
    industry_stock_returns = compute_stock_returns_from_price_cache(industry_price_cache)
    ind_returns_at_date = compute_industry_returns_at(
        db, check_date, _cached_stock_returns=industry_stock_returns
    )
    market_trend = score_market_trend(db, check_date)

    raw_short = {}
    for stock in stocks:
        try:
            r = generate_short_signal(
                db, stock.code,
                as_of_date=check_date,
                write_back=False,
                commit=False,
                _cached_stock=stock,
                _cached_industry_returns=ind_returns_at_date,
                _cached_industry_gm=industry_gm,
                _cached_prices=price_cache,
                _cached_stock_returns=stock_returns,
                _cached_market_trend=market_trend,
            )
            if r:
                raw_short[stock.code] = r
        except Exception as e:
            logger.debug(f"短期信号 {stock.code}@{check_date}: {e}")

    if settings.SHORT_USE_CROSS_SECTIONAL_RANKS:
        _apply_cross_sectional_ranks(raw_short)

    selected = _select_tradeable_short_results(raw_short)
    for code, result in selected:
        rec = _signal_to_record(
            db, run_id, code, check_date, hold_days,
            signal=result.get("short_signal"),
            composite_score=result.get("short_composite_score"),
            sub_scores=result.get("sub_scores"),
            reason=result.get("short_signal_reason"),
        )
        if rec:
            db.add(rec)
            records.append(rec)


def _select_tradeable_short_results(raw_short: dict) -> list[tuple[str, dict]]:
    """
    Keep all sell signals, but cap same-day buy signals by rank.

    Without this, a single market event day can produce dozens of BUY rows and
    dominate the backtest as if each row were an independent opportunity.
    """
    buy_items = []
    other_items = []
    for code, result in raw_short.items():
        signal = result.get("short_signal")
        item = (code, result)
        if signal in ("BUY", "STRONG_BUY"):
            buy_items.append(item)
        else:
            other_items.append(item)

    def buy_rank(item):
        code, result = item
        signal = result.get("short_signal")
        score = result.get("short_composite_score")
        signal_rank = 0 if signal == "STRONG_BUY" else 1
        return (signal_rank, -(score or 0), code)

    max_buy = getattr(settings, "SHORT_MAX_BUY_PER_CHECK_DATE", 0) or 0
    buy_items = sorted(buy_items, key=buy_rank)
    if max_buy > 0:
        buy_items = buy_items[:max_buy]

    return other_items + buy_items


def _run_long_check_date(db, run_id, check_date, hold_days, stocks, params, records):
    """长期回测：单阶段路径"""
    for stock in stocks:
        try:
            result = generate_signal(
                db, stock.code,
                as_of_date=check_date,
                params=params,
                write_back=False,
            )
            if result is None:
                continue
            rec = _signal_to_record(
                db, run_id, stock.code, check_date, hold_days,
                signal=result.get("signal"),
                composite_score=result.get("composite_score"),
                sub_scores=result.get("sub_scores"),
                reason=result.get("reason"),
            )
            if rec:
                db.add(rec)
                records.append(rec)
        except Exception as e:
            logger.debug(f"长期信号 {stock.code}@{check_date}: {e}")


def _signal_to_record(db, run_id, stock_code, check_date, hold_days,
                      signal, composite_score, sub_scores, reason):
    """信号 → 价格查询 → 超额收益计算 → BacktestRecord（或 None）"""
    if signal not in ("BUY", "SELL", "STRONG_BUY", "STRONG_SELL"):
        return None
    entry_price = _get_next_price(db, stock_code, check_date, prefer_open=True)
    bench_entry = _get_next_price(db, BENCHMARK_CODE, check_date, prefer_open=True)
    if not entry_price:
        return None

    # 信号在 check_date 收盘后才可知，回测从下一交易日成交开始计持有期。
    # 这样避免用同一根 K 线既生成信号又成交的 look-ahead / execution bias。
    entry_trade_date = entry_price[1]
    exit_date = entry_trade_date + timedelta(days=hold_days)
    if exit_date > date.today():
        return None
    exit_price = _get_price_on_or_after(db, stock_code, exit_date)
    bench_exit = _get_price_on_or_after(db, BENCHMARK_CODE, exit_date)
    if not exit_price:
        return None
    stock_ret = (exit_price[0] - entry_price[0]) / entry_price[0]
    bench_ret = (
        (bench_exit[0] - bench_entry[0]) / bench_entry[0]
        if bench_entry and bench_exit and bench_entry[0] != 0 else 0.0
    )
    excess_ret = stock_ret - bench_ret
    is_buy_signal = signal in ("BUY", "STRONG_BUY")
    is_win = excess_ret > 0 if is_buy_signal else excess_ret < 0
    return BacktestRecord(
        run_id=run_id,
        stock_code=stock_code,
        signal_date=check_date,
        signal=signal,
        composite_score=composite_score,
        entry_price=entry_price[0],
        exit_price=exit_price[0],
        hold_days=hold_days,
        stock_return=round(stock_ret * 100, 4),
        bench_return=round(bench_ret * 100, 4),
        excess_return=round(excess_ret * 100, 4),
        is_win=is_win,
        sub_scores=sub_scores,
        news_summary=reason,
    )


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


def _calc_summary_metrics(records: list, hold_days: int = 365) -> dict:
    buys  = [r for r in records if r.signal in ("BUY", "STRONG_BUY")  and r.is_win is not None]
    sells = [r for r in records if r.signal in ("SELL", "STRONG_SELL") and r.is_win is not None]

    win_rate      = _safe_mean([r.is_win for r in buys]) * 100
    sell_accuracy = _safe_mean([r.is_win for r in sells]) * 100

    # IC：用 buys+sells 全样本算 rank 相关性是对的（高分 BUY 正超额，低分 SELL 负超额，单调关系）
    excess_returns_all = [r.excess_return for r in buys + sells if r.excess_return is not None]
    scores             = [r.composite_score for r in buys + sells if r.composite_score is not None]
    ic_val = 0.0
    ic_ir  = 0.0
    if len(scores) >= 10 and len(excess_returns_all) >= 10:
        from scipy.stats import spearmanr
        ic_val, _ = spearmanr(scores[:len(excess_returns_all)], excess_returns_all[:len(scores)])
        ic_val = float(ic_val) if not np.isnan(ic_val) else 0.0
        ic_ir  = ic_val / 0.1

    # Alpha / Sharpe / 年化：只用 BUY 端（这是 long-only 策略，散户做空不便）
    # 之前 bug：把 buys+sells 的 excess_return 混合，SELL 正确时是负超额，
    # SELL 数量远超 BUY 时（高阈值场景）→ 平均被拖成负数。
    buy_excess = [r.excess_return for r in buys if r.excess_return is not None]
    alpha  = float(np.mean(buy_excess)) if buy_excess else 0.0
    std    = float(np.std(buy_excess))  if len(buy_excess) > 1 else 1.0
    sharpe = alpha / std if std != 0 else 0.0

    buy_rets = [r.stock_return for r in buys if r.stock_return is not None]
    max_dd   = _max_drawdown(buy_rets)
    # 年化：把单次持有期超额收益按 365/hold_days 倍放大
    ann_alpha = alpha * (365.0 / max(hold_days, 1))

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


def _analyze_false_signals(records: list, signal, signal_type: str = "long") -> list:
    """
    signal: 单值或多值（"BUY" / ("BUY","STRONG_BUY")）
    signal_type: "long" / "short"，决定使用哪套维度
    """
    if isinstance(signal, str):
        match_set = {signal}
    else:
        match_set = set(signal)
    false_records = [r for r in records if r.signal in match_set and r.is_win is False]
    if not false_records:
        return []

    dim_keys = SHORT_DIMS if signal_type == "short" else LONG_DIMS
    patterns = {}
    for rec in false_records:
        if not rec.sub_scores:
            continue
        dims = {k: rec.sub_scores.get(k, 50) for k in dim_keys}
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


def _row_price(row: PriceData, prefer_open: bool = False) -> Optional[float]:
    """Return a usable execution price from a PriceData row."""
    if not row:
        return None
    primary = row.open if prefer_open else row.close
    fallback = row.close if prefer_open else row.open
    if primary and primary > 0:
        return primary
    if fallback and fallback > 0:
        return fallback
    return None


def _get_next_price(
    db: Session, code: str, after_date: date, prefer_open: bool = False
) -> Optional[tuple[float, date]]:
    """First available trading price strictly after after_date."""
    row = (
        db.query(PriceData)
        .filter(PriceData.stock_code == code, PriceData.trade_date > after_date)
        .order_by(PriceData.trade_date.asc())
        .first()
    )
    px = _row_price(row, prefer_open=prefer_open)
    return (px, row.trade_date) if px is not None else None


def _get_price_on_or_after(
    db: Session, code: str, target_date: date, prefer_open: bool = False
) -> Optional[tuple[float, date]]:
    """First available trading price on or after target_date."""
    row = (
        db.query(PriceData)
        .filter(PriceData.stock_code == code, PriceData.trade_date >= target_date)
        .order_by(PriceData.trade_date.asc())
        .first()
    )
    px = _row_price(row, prefer_open=prefer_open)
    return (px, row.trade_date) if px is not None else None


BENCHMARK_CODE = "IDX_000300"   # 沪深300 在 PriceData 中的虚拟代码

def _get_benchmark_price(db: Session, target_date: date) -> Optional[float]:
    return _get_price(db, BENCHMARK_CODE, target_date)


def ensure_benchmark_data(db: Session, force_refresh: bool = False):
    """
    确保沪深300指数数据已入库（回测必需）。
    v202h-fix: 默认增量更新（之前只在首次插入，导致基准数据卡住不更新）。
    force_refresh=True 时强制拉全量，否则只补 latest 之后的新数据。
    """
    existing = db.query(PriceData).filter_by(stock_code=BENCHMARK_CODE).count()
    if existing == 0:
        pass  # 首次，下面拉全量
    elif not force_refresh:
        # 已有数据 → 检查最新日期，若已是今天则跳过；否则拉增量
        from sqlalchemy import func as _func
        latest = db.query(_func.max(PriceData.trade_date)).filter_by(stock_code=BENCHMARK_CODE).scalar()
        if latest and (date.today() - latest).days < 1:
            return   # 今天已更新
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


def _check_dates(start: date, end: date, freq_days: int) -> list:
    """按 freq_days 间隔生成检查日列表"""
    dates, current = [], start
    while current <= end:
        dates.append(current)
        current += timedelta(days=max(freq_days, 1))
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
