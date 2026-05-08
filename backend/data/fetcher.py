"""
AKShare 数据采集层
职责：
  1. 拉取行业分类 / 股票列表
  2. 拉取财务数据（利润表 / 资产负债表 / 现金流量表）
  3. 拉取历史行情（日 K）
  4. 拉取宏观指标（PMI / CPI / M2 / 北向资金）
  5. 拉取公司公告与新闻
所有结果写入 SQLite，调用方无需关心 AKShare 细节。
"""
import logging
import time
from datetime import date, datetime, timedelta
from typing import Optional

import akshare as ak
import pandas as pd
import requests
from sqlalchemy import desc
from sqlalchemy.orm import Session

from config import settings
from database import SessionLocal
from models.models import (
    FinancialData, Industry, MacroData, NewsItem, PriceData, Stock,
)

logger = logging.getLogger(__name__)

RETRY_TIMES = 3


def _ak_symbol(code: str) -> str:
    """
    将纯代码转为 AKShare 东方财富接口所需的带市场前缀格式
    6xxxxx → SH600519
    0xxxxx / 3xxxxx → SZ000001
    8xxxxx / 4xxxxx → BJ430047
    """
    if code.startswith("6"):
        return f"SH{code}"
    elif code.startswith(("8", "4")):
        return f"BJ{code}"
    else:
        return f"SZ{code}"
SLEEP_BETWEEN_REQUESTS = 0.3   # 礼貌性延迟，避免被限流
API_TIMEOUT = 45               # AKShare 单次调用最长等待秒数

# 数据错误（不值得重试）
_DATA_ERRORS = (TypeError, KeyError, IndexError, AttributeError, ValueError)

# 用于 _retry 超时的线程池（复用避免重复创建开销）
import concurrent.futures as _cf
_timeout_executor = _cf.ThreadPoolExecutor(max_workers=8, thread_name_prefix="ak_fetch")


def _retry(fn, *args, _timeout: int = None, **kwargs):
    """
    带重试 + 超时的 AKShare 调用包装器
    - 单次最长等待 _timeout（默认 API_TIMEOUT）秒
    - 超时 / 网络错误 → 最多重试 RETRY_TIMES 次（指数退避）
    - 数据解析错误（NoneType subscript 等）→ 静默跳过，不重试
    """
    timeout = _timeout if _timeout is not None else API_TIMEOUT
    for i in range(RETRY_TIMES):
        fut = _timeout_executor.submit(fn, *args, **kwargs)
        try:
            return fut.result(timeout=timeout)
        except _cf.TimeoutError:
            # 超时也算一次失败，继续重试（关键：新浪宏观接口偶尔会慢到 30-40s+）
            fut.cancel()
            if i < RETRY_TIMES - 1:
                logger.warning(
                    f"[AKShare] {fn.__name__} 超时（>{timeout}s），第 {i+1}/{RETRY_TIMES} 次，重试中…"
                )
                time.sleep(1.5 ** i)
            else:
                logger.warning(
                    f"[AKShare] {fn.__name__} 连续 {RETRY_TIMES} 次超时（>{timeout}s），放弃"
                )
        except _DATA_ERRORS:
            return None   # 数据问题，不重试
        except Exception as e:
            if i < RETRY_TIMES - 1:
                time.sleep(1.5 ** i)
            else:
                logger.warning(f"[AKShare] {fn.__name__} 网络失败: {e}")
    return None


# ──────────────────────────────────────────────
# 行业 & 股票列表
# ──────────────────────────────────────────────
def fetch_industry_list(db: Session) -> int:
    """从申万行业分类拉取一级行业列表，写入 industries 表"""
    df = _retry(ak.stock_board_industry_name_em)
    if df is None or df.empty:
        return 0
    saved = 0
    for _, row in df.iterrows():
        name = str(row.get("板块名称", "")).strip()
        code = str(row.get("板块代码", "")).strip()
        if not name or not code:
            continue
        existing = db.query(Industry).filter_by(code=code).first()
        if not existing:
            db.add(Industry(code=code, name=name, level=1))
            saved += 1
    db.commit()
    logger.info(f"行业列表：写入 {saved} 条新记录")
    return saved


def fetch_stock_list(db: Session) -> int:
    """拉取全 A 股列表，写入 stocks 表"""
    df = _retry(ak.stock_info_a_code_name)
    if df is None or df.empty:
        return 0
    saved = 0
    for _, row in df.iterrows():
        code = str(row.get("code", "")).strip()
        name = str(row.get("name", "")).strip()
        if not code:
            continue
        existing = db.query(Stock).filter_by(code=code).first()
        if not existing:
            db.add(Stock(code=code, name=name, market="A"))
            saved += 1
    db.commit()
    logger.info(f"股票列表：写入 {saved} 条新记录")
    return saved


def fetch_stock_industry_mapping(db: Session) -> int:
    """通过东方财富接口填充 stock.industry_code"""
    updated = 0
    industries = db.query(Industry).all()
    for ind in industries:
        try:
            df = ak.stock_board_industry_cons_em(symbol=ind.name)
            time.sleep(SLEEP_BETWEEN_REQUESTS)
            if df is None or df.empty:
                continue
            for _, row in df.iterrows():
                code = str(row.get("代码", "")).strip()
                if not code:
                    continue
                stock = db.query(Stock).filter_by(code=code).first()
                if stock and stock.industry_code != ind.code:
                    stock.industry_code = ind.code
                    updated += 1
        except Exception as e:
            logger.warning(f"行业 {ind.name} 成分股失败: {e}")
    db.commit()
    logger.info(f"行业映射：更新 {updated} 只股票")
    return updated


# ──────────────────────────────────────────────
# 财务数据
# ──────────────────────────────────────────────
def fetch_financial_data(db: Session, stock_code: str, years: int = 10) -> int:
    """
    拉取单只股票近 N 年财务数据
    兼容 akshare 1.16+ 新旧字段名
    """
    saved = 0
    ak_code = _ak_symbol(stock_code)   # 转为 SH/SZ/BJ 前缀格式

    # 利润表：先拉，如果为空直接返回，不再浪费时间拉其他两张表
    income_df = _retry(ak.stock_profit_sheet_by_yearly_em, symbol=ak_code)
    if income_df is None or income_df.empty:
        return 0

    # 资产负债表 + 现金流量表（顺序拉取）
    balance_df  = _retry(ak.stock_balance_sheet_by_yearly_em, symbol=ak_code)
    cashflow_df = _retry(ak.stock_cash_flow_sheet_by_yearly_em, symbol=ak_code)

    income_df = income_df.head(years)

    for _, row in income_df.iterrows():
        # 兼容新旧字段名：REPORT_DATE / 报告期
        period_str = str(
            row.get("REPORT_DATE", row.get("报告期", ""))
        ).split(" ")[0].strip()
        try:
            period = datetime.strptime(period_str[:10], "%Y-%m-%d").date()
        except ValueError:
            continue

        existing = db.query(FinancialData).filter_by(
            stock_code=stock_code, period=period, report_type="annual"
        ).first()
        if existing:
            continue

        fd = FinancialData(
            stock_code=stock_code,
            period=period,
            report_type="annual",
            pub_date=_estimate_pub_date(period),
        )

        # ── 利润表：尝试多种字段名 ──
        fd.revenue          = _safe_float_multi(row, ["TOTAL_OPERATE_INCOME", "营业总收入", "营业收入"])
        fd.gross_profit     = _safe_float_multi(row, ["GROSS_PROFIT", "毛利润"])
        fd.operating_profit = _safe_float_multi(row, ["OPERATE_PROFIT", "营业利润"])
        fd.net_profit       = _safe_float_multi(row, ["PARENT_NETPROFIT", "净利润", "归属净利润"])
        interest_expense    = _safe_float_multi(row, ["FINANCE_EXPENSE", "财务费用", "利息费用"])
        if fd.revenue and fd.revenue != 0:
            fd.gross_margin = (fd.gross_profit or 0) / fd.revenue * 100
            fd.net_margin   = (fd.net_profit or 0) / fd.revenue * 100
        # 利息覆盖倍数 = 营业利润 / |财务费用|
        if fd.operating_profit and interest_expense and abs(interest_expense) > 1e4:
            fd.interest_coverage = fd.operating_profit / abs(interest_expense)

        # ── 资产负债表 ──
        if balance_df is not None and not balance_df.empty:
            b_row = _find_period_row(balance_df, period_str)
            if b_row is not None:
                fd.total_assets = _safe_float_multi(b_row, ["TOTAL_ASSETS", "资产总计", "总资产"])
                fd.total_equity = _safe_float_multi(b_row, ["TOTAL_EQUITY", "股东权益合计", "归属净资产"])
                fd.total_debt   = _safe_float_multi(b_row, ["TOTAL_LIABILITIES", "负债合计", "总负债"])
                if fd.total_assets and fd.total_assets != 0:
                    fd.debt_ratio = (fd.total_debt or 0) / fd.total_assets * 100
                if fd.total_equity and fd.total_equity != 0 and fd.net_profit:
                    fd.roe = fd.net_profit / fd.total_equity * 100

        # ── 现金流量表 ──
        if cashflow_df is not None and not cashflow_df.empty:
            c_row = _find_period_row(cashflow_df, period_str)
            if c_row is not None:
                fd.operating_cashflow = _safe_float_multi(c_row, ["NETCASH_OPERATE", "经营活动现金流量净额"])
                fd.capex              = _safe_float_multi(c_row, ["CONSTRUCT_LONG_ASSET", "购建固定资产支出"])
                if fd.operating_cashflow is not None and fd.capex is not None:
                    fd.free_cashflow = fd.operating_cashflow - abs(fd.capex or 0)
                if fd.net_profit and fd.net_profit != 0 and fd.operating_cashflow is not None:
                    fd.fcf_ratio = fd.operating_cashflow / fd.net_profit

        db.add(fd)
        saved += 1
        time.sleep(SLEEP_BETWEEN_REQUESTS)

    db.commit()
    return saved


def fetch_all_financial_data(db: Session, limit: Optional[int] = None,
                             skip_fresh_days: int = 30) -> None:
    """
    批量拉取所有活跃股票的财务数据（写入进度到 fetch_progress 单例）
    单线程顺序执行，避免占满事件循环线程池。

    skip_fresh_days: 若该股票在 N 天内已抓过最新年报，跳过（=0 表示不跳过）。
    关键优化：
      1. income 为空时直接跳过 balance/cashflow
      2. 已有新鲜数据的股票直接跳过（无需调 AKShare）
    """
    import os
    from datetime import date as _date
    os.environ["TQDM_DISABLE"] = "1"   # 屏蔽 AKShare 内部 tqdm 进度条

    from data.fetch_progress import reset_fetch_progress
    stocks = db.query(Stock).filter_by(is_active=True).all()
    if limit:
        stocks = stocks[:limit]

    total    = len(stocks)
    prog     = reset_fetch_progress(total)
    today    = _date.today()
    # 视今年/去年有年报数据为"新鲜"：截止到 2 年前的 12-31
    # e.g. 2026-04-16 今天，2024-12-31 的年报算新鲜
    cutoff   = date(today.year - 2, 12, 31)

    for i, stock in enumerate(stocks):
        try:
            # 快速路径：已有去年/今年数据 → 跳过
            if skip_fresh_days > 0:
                latest = db.query(FinancialData)\
                    .filter_by(stock_code=stock.code, report_type="annual")\
                    .order_by(FinancialData.period.desc()).first()
                if latest and latest.period >= cutoff:
                    prog.skipped += 1
                    prog.current = i + 1
                    continue

            n = fetch_financial_data(db, stock.code)
            prog.current = i + 1
            if n > 0:
                prog.saved += n
            else:
                prog.skipped += 1
        except Exception as e:
            logger.error(f"财务数据 {stock.code} 失败: {e}")
            prog.skipped += 1
            prog.current = i + 1

        if (i + 1) % 50 == 0 or i == total - 1:
            logger.info(
                f"财务数据进度: {i+1}/{total} "
                f"| 已入库: {prog.saved} 条  跳过: {prog.skipped} 只"
            )

    prog.status      = "done"
    prog.finished_at = time.time()
    logger.info(f"财务数据抓取完成：共入库 {prog.saved} 条，跳过 {prog.skipped} 只")


# ──────────────────────────────────────────────
# 行情数据
# ──────────────────────────────────────────────
def _sina_symbol(code: str) -> str:
    """
    转换为新浪 API 所需的小写前缀格式
    6xxxxx → sh600519
    0xxxxx / 3xxxxx → sz000001
    北交所 (8/4xxxxx) → 不支持，返回 None
    """
    if code.startswith("6"):
        return f"sh{code}"
    elif code.startswith(("0", "3", "2")):
        return f"sz{code}"
    return None   # BJ exchange / 其他：不支持


def fetch_price_history(db: Session, stock_code: str, start_date: str = "20100101") -> int:
    """
    拉取日 K 线（使用新浪 API，稳定性优于东方财富接口）
    北交所股票暂不支持，直接返回 0。
    start_date 为 yyyyMMdd 字符串；调用方可以传 max(trade_date)+1 实现增量更新。
    """
    sina_sym = _sina_symbol(stock_code)
    if not sina_sym:
        logger.debug(f"行情 {stock_code}: 北交所/不支持，跳过")
        return 0

    saved = 0
    today_str = date.today().strftime("%Y%m%d")
    if start_date >= today_str:
        return 0   # 已到今天/未来，没东西可拉
    try:
        df = _retry(
            ak.stock_zh_a_daily,
            symbol=sina_sym,
            start_date=start_date,
            end_date=today_str,
            adjust="hfq",
        )
        if df is None or df.empty:
            return 0

        for _, row in df.iterrows():
            trade_date = _parse_date(str(row.get("date", "")))
            if not trade_date:
                continue
            exists = db.query(PriceData).filter_by(
                stock_code=stock_code, trade_date=trade_date
            ).first()
            if exists:
                continue
            db.add(PriceData(
                stock_code=stock_code,
                trade_date=trade_date,
                open=_safe_float(row, "open"),
                high=_safe_float(row, "high"),
                low=_safe_float(row, "low"),
                close=_safe_float(row, "close"),
                volume=_safe_float(row, "volume"),
            ))
            saved += 1

        db.commit()
        if saved:
            logger.info(f"行情 {stock_code}: 新增 {saved} 条")
    except Exception as e:
        logger.error(f"行情 {stock_code}: {e}")
    return saved


def fetch_all_price_data(db: Session, limit: int = 0, mode: str = "incremental") -> dict:
    """
    批量拉取有财务数据的股票的日 K 线。
    返回 {"fetched": 成功股票数, "skipped": 跳过数, "total_records": 新增K线数}

    mode:
      "incremental" (默认)
        - 已有数据的：从 max(trade_date)+1 增量补到今天（每只仅几秒）
        - 没有数据的：跳过（用 mode='full' 单独触发首次全量初始化）
      "full"
        - 所有股票一并跑；没有数据的从 2010-01-01 全量拉，已有的也走增量补
        - 适合首次部署 / 偶尔补漏；耗时长（数千只 × ~10 秒）
      "init-missing"
        - 只针对完全没价格数据的股票做首次全量；已有数据的 skip
        - 适合 incremental 之后单独补一次新股的历史
    """
    from data.fetch_progress import get_fetch_progress
    from datetime import timedelta
    from sqlalchemy import func

    codes_with_fin = (
        db.query(FinancialData.stock_code)
        .distinct()
        .order_by(FinancialData.stock_code)   # 顺序稳定，便于 debug 跟踪进度
        .all()
    )
    target_codes = [r[0] for r in codes_with_fin]
    if limit > 0:
        target_codes = target_codes[:limit]

    # 一次 GROUP BY 全拿每只股票的 max(trade_date)，避免 N+1（5196 次单查）
    latest_per_code = dict(
        db.query(PriceData.stock_code, func.max(PriceData.trade_date))
          .group_by(PriceData.stock_code)
          .all()
    )
    # 全表当前的"最新交易日"，作为快速跳过的判定基准
    global_max_dt = max(latest_per_code.values()) if latest_per_code else None

    prog = get_fetch_progress()
    prog.total   = len(target_codes)
    prog.current = 0
    prog.skipped = 0

    fetched = 0
    total_records = 0

    for i, code in enumerate(target_codes):
        prog.current = i + 1

        # O(1) 查表，不打 DB
        latest_dt = latest_per_code.get(code)

        # 模式分支
        if latest_dt is None:
            # 没有任何数据 → 看模式
            if mode == "incremental":
                prog.skipped += 1
                continue
            start_date = "20100101"
        else:
            # 有数据 → 增量起点 = 最后一天 + 1
            if mode == "init-missing":
                prog.skipped += 1
                continue
            # 已经追上全表最新日 → 没东西可拉，免一次网络往返
            if global_max_dt is not None and latest_dt >= global_max_dt:
                prog.skipped += 1
                continue
            start_date = (latest_dt + timedelta(days=1)).strftime("%Y%m%d")

        try:
            n = fetch_price_history(db, code, start_date=start_date)
            total_records += n
            if n > 0:
                fetched += 1
        except Exception as e:
            logger.debug(f"行情 {code}: {e}")

    logger.info(f"批量行情({mode})：处理 {fetched} 只股票，跳过 {prog.skipped}，新增 {total_records} 条K线")
    return {"fetched": fetched, "skipped": prog.skipped, "total_records": total_records}


# ──────────────────────────────────────────────
# 宏观数据
# ──────────────────────────────────────────────
_EM_DATACENTER_URL = "https://datacenter-web.eastmoney.com/api/data/v1/get"
_EM_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
    "Referer": "https://data.eastmoney.com/",
}


def _fetch_eastmoney_macro(report_name: str, value_field: str,
                            page_size: int = 1000) -> Optional[pd.DataFrame]:
    """
    从东方财富经济数据中心拉宏观月频数据。
    返回 pd.DataFrame[date, value]，按日期降序；失败/空返 None。

    背景：sina/jin10 系列（akshare 的 macro_china_*_yearly）在 2025-08 后停更。
    东方财富数据中心仍在持续更新，这里直接打它的 JSON API。

    pageSize 默认 1000：覆盖 80+ 年月频历史，避免被截断；东方财富对此值容忍度高。
    """
    try:
        r = requests.get(
            _EM_DATACENTER_URL,
            params={
                "reportName":   report_name,
                "columns":      "ALL",
                "pageNumber":   1,
                "pageSize":     page_size,
                "sortColumns":  "REPORT_DATE",
                "sortTypes":    "-1",
            },
            headers=_EM_HEADERS,
            timeout=15,
        )
        r.raise_for_status()
        payload = r.json()
        if not payload.get("success"):
            logger.warning(f"东方财富 {report_name}: {payload.get('message','无 message')}")
            return None
        rows = payload.get("result", {}).get("data") or []
        if not rows:
            return None
        # 标准化成 DataFrame[date, value]
        records = []
        for row in rows:
            date_str = str(row.get("REPORT_DATE", ""))[:10]   # '2026-04-01'
            val = row.get(value_field)
            if not date_str or val is None:
                continue
            records.append({"date": date_str, "value": val})
        if not records:
            return None
        return pd.DataFrame(records)
    except Exception as e:
        logger.warning(f"东方财富 {report_name} 拉取失败: {type(e).__name__}: {e}")
        return None


def fetch_macro_data(db: Session) -> dict:
    """
    拉取 PMI / CPI / M2 / 北向资金等宏观指标。

    PMI/CPI/M2 主源：东方财富经济数据中心 JSON API（一般滞后 1 个月）
    NORTH_FLOW 主源：akshare stock_hsgt_hist_em（每个交易日更新）
    PMI/CPI/M2 备源：akshare macro_china_*_yearly（sina/jin10，2025-08 后已停更，
                     仅作 fallback 兼容历史接口）

    返回 {"new": 新增条数, "total": 数据库总条数, "detail": "各指标概况"}
    """
    saved = 0

    # (indicator, primary_fetch_fn, fallback_fetch_fn, 日期列候选, 值列候选)
    macro_tasks = [
        ("PMI",
         lambda: _fetch_eastmoney_macro("RPT_ECONOMY_PMI", "MAKE_INDEX"),
         lambda: ak.macro_china_pmi_yearly(),
         ["date", "日期", "月份"], ["value", "今值", "制造业"]),
        ("CPI",
         lambda: _fetch_eastmoney_macro("RPT_ECONOMY_CPI", "NATIONAL_SAME"),
         lambda: ak.macro_china_cpi_yearly(),
         ["date", "日期", "月份"], ["value", "今值", "全国"]),
        ("M2",
         lambda: _fetch_eastmoney_macro("RPT_ECONOMY_CURRENCY_SUPPLY", "BASIC_CURRENCY_SAME"),
         lambda: ak.macro_china_m2_yearly(),
         ["date", "日期", "月份"], ["value", "今值", "数量"]),
        ("NORTH_FLOW",
         lambda: ak.stock_hsgt_hist_em(symbol="北向资金"),
         None,    # 这个 akshare 接口仍然好用，没 fallback
         ["日期", "date"], ["当日资金流入", "当日成交净买额", "净买入额"]),
    ]

    indicator_detail = []
    # 宏观接口（特别是 CPI/M2，走新浪 sina 财经）有时会慢到 60s+，给更宽松的超时
    MACRO_TIMEOUT = 90
    for indicator, primary_fn, fallback_fn, date_cols, val_cols in macro_tasks:
        # 先打主源（东方财富 / akshare-em）；失败再尝试 fallback（akshare-sina/jin10）
        df = None
        try:
            df = _retry(primary_fn, _timeout=MACRO_TIMEOUT)
        except Exception as e:
            logger.warning(f"宏观 {indicator} 主源异常: {e}")

        if (df is None or df.empty) and fallback_fn is not None:
            logger.warning(f"宏观 {indicator}: 主源无数据，回退 akshare 备源")
            try:
                df = _retry(fallback_fn, _timeout=MACRO_TIMEOUT)
            except Exception as e:
                logger.warning(f"宏观 {indicator} 备源也异常: {e}")

        # 主源 + 备源都没数据时，跳过这个指标
        if df is None or df.empty:
            logger.warning(f"宏观 {indicator}: 返回空数据（主源 / 备源都失败）")
            indicator_detail.append(f"{indicator}:无数据")
            continue

        try:
            # 自动匹配列名（兼容东方财富 [date,value] 和 akshare 旧接口的列名）
            date_col = next((c for c in date_cols if c in df.columns), None)
            val_col  = next((c for c in val_cols  if c in df.columns), None)

            if not date_col or not val_col:
                logger.warning(f"宏观 {indicator}: 列名不匹配，实际列={df.columns.tolist()[:6]}")
                indicator_detail.append(f"{indicator}:列名不匹配")
                continue

            ind_saved = 0
            for _, row in df.iterrows():
                d = _parse_date(str(row.get(date_col, "")))
                if not d:
                    continue
                exists = db.query(MacroData).filter_by(date=d, indicator=indicator).first()
                if exists:
                    continue
                val = _safe_float(row, val_col)
                if val is not None:
                    db.add(MacroData(date=d, indicator=indicator, value=val))
                    ind_saved += 1
            saved += ind_saved
            indicator_detail.append(f"{indicator}:+{ind_saved}")
            time.sleep(SLEEP_BETWEEN_REQUESTS)

        except Exception as e:
            logger.warning(f"宏观 {indicator}: {e}")
            indicator_detail.append(f"{indicator}:失败")

    db.commit()
    total = db.query(MacroData).count()
    logger.info(f"宏观数据：新增 {saved} 条，总计 {total} 条")
    return {"new": saved, "total": total, "detail": " | ".join(indicator_detail)}


# ──────────────────────────────────────────────
# 新闻 / 舆情
# ──────────────────────────────────────────────
def fetch_stock_news(db: Session, stock_code: str, limit: int = 30) -> int:
    """拉取个股新闻（东方财富）"""
    saved = 0
    try:
        df = _retry(ak.stock_news_em, symbol=stock_code)
        if df is None or df.empty:
            return 0
        df = df.head(limit)
        for _, row in df.iterrows():
            title = str(row.get("新闻标题", "")).strip()
            if not title:
                continue
            pub_str = str(row.get("发布时间", ""))
            pub_dt = _parse_datetime(pub_str)
            if not pub_dt:
                continue
            exists = db.query(NewsItem).filter_by(
                stock_code=stock_code,
                title=title[:200],
                pub_date=pub_dt,
            ).first()
            if exists:
                continue
            db.add(NewsItem(
                stock_code=stock_code,
                pub_date=pub_dt,
                title=title[:500],
                summary=str(row.get("新闻内容", ""))[:1000],
                source=str(row.get("文章来源", "")),
                url=str(row.get("新闻链接", "")),
            ))
            saved += 1
        db.commit()
    except Exception as e:
        logger.warning(f"新闻 {stock_code}: {e}")
    return saved


# ──────────────────────────────────────────────
# 内部工具函数
# ──────────────────────────────────────────────
def _safe_float(row, col: str) -> Optional[float]:
    try:
        v = row[col]
        if pd.isna(v):
            return None
        return float(v)
    except (KeyError, TypeError, ValueError):
        return None


def _safe_float_multi(row, cols: list) -> Optional[float]:
    """尝试多个候选列名，返回第一个有效值"""
    for col in cols:
        v = _safe_float(row, col)
        if v is not None:
            return v
    return None


def _find_period_row(df: pd.DataFrame, period_str: str):
    """在 DataFrame 中按报告期字符串匹配一行，兼容新旧列名"""
    for col in ["REPORT_DATE", "报告期", "DATE", "日期"]:
        if col in df.columns:
            matched = df[df[col].astype(str).str.startswith(period_str[:7])]
            if not matched.empty:
                return matched.iloc[0]
    return None


def _parse_date(s: str) -> Optional[date]:
    for fmt in ("%Y-%m-%d", "%Y%m%d", "%Y/%m/%d", "%Y年%m月"):
        try:
            return datetime.strptime(s[:10], fmt).date()
        except ValueError:
            continue
    return None


def _parse_datetime(s: str) -> Optional[datetime]:
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(s[:19], fmt)
        except ValueError:
            continue
    return None


def _estimate_pub_date(period: date) -> date:
    """
    估算财报发布日：
    - 年报（12-31）→ 次年 4 月 30 日
    - 三季报（9-30）→ 当年 10 月 31 日
    - 半年报（6-30）→ 当年 8 月 31 日
    - 一季报（3-31）→ 当年 4 月 30 日
    """
    m = period.month
    y = period.year
    if m == 12:
        return date(y + 1, 4, 30)
    elif m == 9:
        return date(y, 10, 31)
    elif m == 6:
        return date(y, 8, 31)
    else:
        return date(y, 4, 30)


def _fill_pe_pb(db: Session, stock_code: str) -> None:
    """补充最新 PE/PB 到最近一条价格记录"""
    try:
        df = ak.stock_a_indicator_lg(symbol=stock_code)
        if df is None or df.empty:
            return
        latest = df.sort_values("trade_date").iloc[-1]
        pe = _safe_float(latest, "pe")
        pb = _safe_float(latest, "pb")
        last_price = db.query(PriceData).filter_by(stock_code=stock_code)\
            .order_by(PriceData.trade_date.desc()).first()
        if last_price:
            last_price.pe_ttm = pe
            last_price.pb = pb
    except Exception:
        pass
