"""
热点特征数据拉取 + 本地缓存（封单 / 连板 / 炸板 / 龙虎榜 / 资金流）
=================================================================
为 hot_stock_research.py 提供方案里"拿得到的免费特征"（东财公开数据）。

★ 这些接口本身免费，唯一前置条件是 akshare 能连上东财
  （目前被 Veee 代理墙，断开 VPN 后即可；本模块对失败优雅降级）。
★ 只把数据缓存到本地 pickle（默认 /data/hot_features/），不改 DB schema、
  不碰线上模型。研究脚本读缓存即可，缓存不存在则自动跳过这些特征。

⚠️ 列名注记：东财各接口的中文列名随 akshare 版本可能微调。下面用「优先列名 +
   兜底匹配」处理；首次联网成功后请用 --probe 打印一次真实列名核对。

⚠️ 防泄漏：龙虎榜约 18:00 盘后发布，信号日(收盘决策)当天拿不到 → 只能用于次日。
   research 脚本在并表时对龙虎榜做了 1 个交易日的滞后（见 load_extra_features）。

用法（容器内，需先断 VPN）：
    python scripts/hot_features_fetch.py --probe                 # 打印各接口真实列名
    python scripts/hot_features_fetch.py --backfill 2024-01-01   # 回填到今天
"""
from __future__ import annotations
import sys, os, time, argparse
sys.path.insert(0, ".")

import pandas as pd

from data.fetcher import _retry          # 复用带重试/超时的 akshare 包装器（约束 3）
from database import SessionLocal
from models.models import PriceData
import akshare as ak

CACHE_DIR = os.environ.get("HOT_FEATURES_DIR", "/data/hot_features")
BENCHMARK_CODE = "IDX_000300"


def _ensure_dir():
    os.makedirs(CACHE_DIR, exist_ok=True)


def _pick(df: pd.DataFrame, *names):
    """从 df 里按候选名返回第一个存在的列名（兜底列名漂移）。"""
    for n in names:
        if n in df.columns:
            return n
    return None


# ──────────────────────────────────────────────
# 单日 / 区间拉取（薄封装，便于 --probe 核对列名）
# ──────────────────────────────────────────────
def fetch_zt_pool(date_str: str):
    """涨停板池：封板资金、连板数、炸板次数等。date_str=YYYYMMDD。"""
    return _retry(ak.stock_zt_pool_em, date=date_str)


def fetch_zbgc_pool(date_str: str):
    """炸板股池（首板炸板）。date_str=YYYYMMDD。"""
    return _retry(ak.stock_zt_pool_zbgc_em, date=date_str)


def fetch_lhb(start_str: str, end_str: str):
    """龙虎榜明细（区间）。⚠️ 盘后发布，仅可用于次日。"""
    return _retry(ak.stock_lhb_detail_em, start_date=start_str, end_date=end_str)


def fetch_fund_flow_hist(code: str):
    """个股资金流（估算版，约近 100 交易日）：主力/超大单净流入及占比。"""
    market = "sh" if code.startswith("6") else ("bj" if code[:1] in ("8", "4") or code[:3] == "920" else "sz")
    return _retry(ak.stock_individual_fund_flow, stock=code, market=market)


# ──────────────────────────────────────────────
# 工具
# ──────────────────────────────────────────────
def _trading_dates(start: str, end: str | None = None):
    db = SessionLocal()
    try:
        q = db.query(PriceData.trade_date).filter(
            PriceData.stock_code == BENCHMARK_CODE,
            PriceData.trade_date >= pd.to_datetime(start).date())
        if end:
            q = q.filter(PriceData.trade_date <= pd.to_datetime(end).date())
        return [r[0] for r in q.order_by(PriceData.trade_date).all()]
    finally:
        db.close()


def probe():
    """联网成功后打印各接口真实列名，用于核对/调整下面的列映射。"""
    today = pd.Timestamp.today().strftime("%Y%m%d")
    for name, fn in [("涨停板池", lambda: fetch_zt_pool(today)),
                     ("炸板池", lambda: fetch_zbgc_pool(today)),
                     ("龙虎榜", lambda: fetch_lhb(today, today)),
                     ("个股资金流(600519)", lambda: fetch_fund_flow_hist("600519"))]:
        try:
            df = fn()
            cols = list(df.columns) if df is not None else None
            print(f"[{name}] 行数={0 if df is None else len(df)} 列={cols}")
        except Exception as e:
            print(f"[{name}] FAIL {type(e).__name__}: {str(e)[:80]}")


# ──────────────────────────────────────────────
# 回填到本地缓存
# ──────────────────────────────────────────────
def backfill(start: str, end: str | None = None):
    _ensure_dir()
    dates = _trading_dates(start, end)
    if not dates:
        print("无交易日历，先确保 PriceData 有基准指数数据"); return
    print(f"回填 {len(dates)} 个交易日 → {CACHE_DIR}（失败的日期会跳过，可重复执行补齐）")

    zt_rows, zb_rows = [], []
    for i, d in enumerate(dates, 1):
        ds = d.strftime("%Y%m%d") if hasattr(d, "strftime") else str(d).replace("-", "")
        df = fetch_zt_pool(ds)
        if df is not None and not df.empty:
            df = df.copy(); df["trade_date"] = pd.to_datetime(d); zt_rows.append(df)
        zb = fetch_zbgc_pool(ds)
        if zb is not None and not zb.empty:
            zb = zb.copy(); zb["trade_date"] = pd.to_datetime(d); zb_rows.append(zb)
        time.sleep(0.3)
        if i % 50 == 0 or i == len(dates):
            print(f"  涨停/炸板 [{i}/{len(dates)}]")
    if zt_rows:
        pd.concat(zt_rows, ignore_index=True).to_pickle(f"{CACHE_DIR}/zt_pool.pkl")
        print(f"  写 zt_pool.pkl（{sum(len(x) for x in zt_rows)} 行）")
    if zb_rows:
        pd.concat(zb_rows, ignore_index=True).to_pickle(f"{CACHE_DIR}/zbgc_pool.pkl")
        print(f"  写 zbgc_pool.pkl（{sum(len(x) for x in zb_rows)} 行）")

    # 龙虎榜：区间一次拉
    lhb = fetch_lhb(dates[0].strftime("%Y%m%d") if hasattr(dates[0], "strftime") else str(dates[0]).replace("-", ""),
                    dates[-1].strftime("%Y%m%d") if hasattr(dates[-1], "strftime") else str(dates[-1]).replace("-", ""))
    if lhb is not None and not lhb.empty:
        lhb.to_pickle(f"{CACHE_DIR}/lhb.pkl"); print(f"  写 lhb.pkl（{len(lhb)} 行）")

    # 资金流：逐只（量大，建议先确认网络稳定再开；这里给出循环，失败跳过）
    print("  个股资金流回填较重（逐只 ~5500 次），如需请取消注释 _backfill_fund_flow()")
    # _backfill_fund_flow()


def _backfill_fund_flow():
    from models.models import Stock
    db = SessionLocal()
    try:
        codes = [r[0] for r in db.query(Stock.code).filter(Stock.is_active == True).all()]
    finally:
        db.close()
    rows = []
    for i, c in enumerate(codes, 1):
        df = fetch_fund_flow_hist(c)
        if df is not None and not df.empty:
            df = df.copy(); df["code"] = c; rows.append(df)
        time.sleep(0.2)
        if i % 200 == 0:
            print(f"  资金流 [{i}/{len(codes)}]")
    if rows:
        pd.concat(rows, ignore_index=True).to_pickle(f"{CACHE_DIR}/fund_flow.pkl")
        print(f"  写 fund_flow.pkl（{sum(len(x) for x in rows)} 行）")


# ──────────────────────────────────────────────
# Tushare 历史涨停板（付费积分）—— 免费东财涨停池只给近 1 个月，
# 要历史封单/连板/炸板只能走 Tushare limit_list_d（需 token + 5000 积分）。
# 留桩：写出与 zt_pool.pkl 同 schema 的文件，research 脚本无需改动即可用。
# ──────────────────────────────────────────────
def backfill_tushare_limit(start: str, end: str | None = None, token: str | None = None):
    """
    用 Tushare limit_list_d 回填历史涨停板（limit_type='U'）。
    token 优先用参数，其次环境变量 TUSHARE_TOKEN。
    字段映射 → zt_pool.pkl：fd_amount→封板资金, open_times→炸板次数, limit_times→连板数。
    """
    try:
        import tushare as ts
    except ImportError:
        print("未安装 tushare：pip install tushare（并准备好 token + ≥5000 积分）"); return
    token = token or os.environ.get("TUSHARE_TOKEN")
    if not token:
        print("缺 Tushare token：设环境变量 TUSHARE_TOKEN，或 --token 传入"); return
    pro = ts.pro_api(token)
    _ensure_dir()
    dates = _trading_dates(start, end)
    if not dates:
        print("无交易日历"); return
    print(f"Tushare 回填 {len(dates)} 个交易日的历史涨停板 → zt_pool.pkl")
    out = []
    for i, d in enumerate(dates, 1):
        ds = d.strftime("%Y%m%d") if hasattr(d, "strftime") else str(d).replace("-", "")
        try:
            df = pro.limit_list_d(trade_date=ds, limit_type="U")
        except Exception as e:
            print(f"  {ds} FAIL {type(e).__name__}: {str(e)[:50]}"); time.sleep(0.5); continue
        if df is not None and not df.empty:
            m = pd.DataFrame({
                "代码": df["ts_code"].str[:6],
                "trade_date": pd.to_datetime(d),
                "封板资金": pd.to_numeric(df.get("fd_amount"), errors="coerce"),
                "炸板次数": pd.to_numeric(df.get("open_times"), errors="coerce"),
                "连板数": pd.to_numeric(df.get("limit_times"), errors="coerce"),
            })
            out.append(m)
        time.sleep(0.4)
        if i % 50 == 0 or i == len(dates):
            print(f"  [{i}/{len(dates)}]")
    if out:
        pd.concat(out, ignore_index=True).to_pickle(f"{CACHE_DIR}/zt_pool.pkl")
        print(f"  写 zt_pool.pkl（{sum(len(x) for x in out)} 行，覆盖 Tushare 历史）")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--probe", action="store_true", help="打印各接口真实列名")
    ap.add_argument("--backfill", metavar="START", help="akshare 回填(近月涨停池+全程龙虎榜) 起始日 YYYY-MM-DD")
    ap.add_argument("--tushare-limit", metavar="START", help="Tushare 历史涨停板回填 起始日 YYYY-MM-DD（需 token）")
    ap.add_argument("--token", default=None, help="Tushare token（或用环境变量 TUSHARE_TOKEN）")
    ap.add_argument("--end", metavar="END", default=None)
    args = ap.parse_args()
    if args.probe:
        probe()
    elif args.backfill:
        backfill(args.backfill, args.end)
    elif args.tushare_limit:
        backfill_tushare_limit(args.tushare_limit, args.end, args.token)
    else:
        ap.print_help()
