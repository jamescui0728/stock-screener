"""
A股短线"起飞"捕捉模型 —— 离线研究脚本（落地版）
=================================================
基于用户提供的研究骨架（三重门标签 + 防泄漏训练 + 带约束回测），
接本项目真实 DB（PriceData / Stock / Industry）跑通整条流程。

★ 定位：纯离线研究 / 复盘工具。
  - 只读 DB，不写库、不联网。
  - 完全独立于线上的逆向短期模型（short_signal_engine）与模拟盘，互不影响。
  - 产出：标签分布、单因子 IC、Purged-CV AUC、带 A 股约束的回测指标。

★ 打法：突破趋势启动（H=5，上轨 +20% / 下轨 −8%，持仓 3–10 天量级）。

★ 模型：sklearn HistGradientBoostingClassifier（LightGBM 等价物，零新依赖）+ 概率校准。

────────────────────────────────────────────────────────────
本项目数据相对完整方案的缺口（已在代码中如实处理 / 标注）：
  - 价格为【后复权】→ 涨停/三重门改用「当日涨幅 + 板块限幅」判定，不用绝对涨停价。
  - 无 amount 成交额列、market_cap 多为空 → 流动性过滤退化为「有成交 + 足够历史」，
    成交额/ADV 容量约束无法精确建模（仅给出注记）。
  - listed_date 多为空 → 用「窗口内历史长度」代理新股剔除。
  - 无资金流 / 龙虎榜 / 涨停封单量 / 炸板率 / Level-2 → 第 3 类(资金) 与封单/炸板特征
    无法实现，已省略（这恰是打板模型最核心的一类，故对结果预期应保守）。
  - 舆情(news_items)覆盖仅约 10% → v1 暂不纳入，留 hook。

⚠️ 幸存者偏差：标的池取自 Stock.is_active==True，已退市股不进入历史样本，
   会系统性高估动量/突破类策略表现。本项目无历史 is_active / 退市快照，暂无法
   消除；解读结果时须知"真实表现只会更差"（当前样本外回测已为大幅负，结论不受影响）。

声明：研究模板，非交易系统，不构成投资建议。A股短线赛道极度拥挤、策略衰减快，
      务必用足够长、跨牛熊的样本外数据反复检验。
"""
from __future__ import annotations
import sys, os
sys.path.insert(0, ".")

from datetime import date, timedelta

# 热点特征本地缓存目录（由 hot_features_fetch.py 回填；不存在则自动跳过这些特征）
EXTRA_CACHE_DIR = os.environ.get("HOT_FEATURES_DIR", "/data/hot_features")

import numpy as np
import pandas as pd

from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.calibration import CalibratedClassifierCV
from sklearn.metrics import roc_auc_score
from scipy.stats import spearmanr

from database import SessionLocal
from models.models import PriceData, Stock


# =============================================================================
# 0. 配置
# =============================================================================
class Config:
    # 研究窗口
    YEARS = 3.0
    MAX_STOCKS = None       # None = 全市场；调试可设小值（如 800）
    # 标签（三重门）—— 突破趋势启动
    HORIZON = 5             # 向前看交易日数 H
    PT = 0.20               # 上轨累计涨幅（"起飞"）
    SL = 0.08               # 下轨累计跌幅
    # 标的池
    MIN_HISTORY = 120       # 窗口内最少交易日（代理新股剔除 + 保证特征可算）
    # 训练
    N_SPLITS = 4
    EMBARGO_DAYS = HORIZON + 1   # Purged CV 隔离期（交易日，>= 标签窗口 HORIZON）防重叠泄漏
    CALIBRATE = True             # 概率校准（Isotonic）
    # 回测
    TOP_K = 5
    PROB_THRESHOLD = 0.50
    COMMISSION = 0.00025    # 佣金（单边）
    STAMP_TAX = 0.0005      # 印花税（卖出单边）
    SLIPPAGE = 0.002        # 滑点（单边）


# 板块涨跌幅限制（按代码前缀）
def board_limit(code: str) -> float:
    if code[:3] in ("300", "301", "688", "689"):   # 创业板 / 科创板
        return 0.20
    if code[:1] in ("8", "4") or code[:3] == "920":  # 北交所
        return 0.30
    return 0.10                                       # 主板（含中小板）


def is_st_name(name: str) -> bool:
    if not name:
        return False
    return ("ST" in name.upper()) or ("退" in name)


# =============================================================================
# 1. 数据加载（真实 DB）
# =============================================================================
def load_price_panel(db, cfg: Config) -> pd.DataFrame:
    """从 PriceData + Stock 拉长表面板，排除基准指数与 ST。"""
    max_date = db.query(PriceData.trade_date).order_by(PriceData.trade_date.desc()).first()[0]
    start = max_date - timedelta(days=int(365 * cfg.YEARS) + 40)

    # 股票元信息（剔除 ST / 非 A 股 / 指数）
    stocks = (
        db.query(Stock.code, Stock.name, Stock.industry_code)
        .filter(Stock.is_active == True, Stock.market == "A").all()
    )
    meta = {c: (n, ind) for c, n, ind in stocks
            if not is_st_name(n) and not c.startswith("IDX")}
    codes = list(meta.keys())
    if cfg.MAX_STOCKS:
        codes = codes[: cfg.MAX_STOCKS]
    codeset = set(codes)

    rows = (
        db.query(PriceData.stock_code, PriceData.trade_date,
                 PriceData.open, PriceData.high, PriceData.low,
                 PriceData.close, PriceData.volume, PriceData.turnover_rate)
        .filter(PriceData.trade_date >= start,
                PriceData.close.isnot(None))
        .all()
    )
    data = [r for r in rows if r[0] in codeset]
    df = pd.DataFrame(data, columns=[
        "code", "date", "open", "high", "low", "close", "volume", "turnover"])
    df["date"] = pd.to_datetime(df["date"])
    df["sector"] = df["code"].map(lambda c: meta[c][1])
    df["board_lim"] = df["code"].map(board_limit)
    df = df.sort_values(["code", "date"]).reset_index(drop=True)
    # 过滤历史过短的票
    counts = df.groupby("code")["date"].transform("size")
    df = df[counts >= cfg.MIN_HISTORY].copy()
    return df, max_date


def add_life_pctile(db, panel: pd.DataFrame) -> pd.DataFrame:
    """
    上市以来价格分位（point-in-time，防泄漏）：每个 (股票, 信号日) 取该股
    【自上市至该日】全部后复权收盘里、当日收盘所处的百分位（0≈历史最低，1≈历史最高）。

    必须用全历史（不能只用研究窗口，否则只是"近 N 年分位"）→ 单独拉一次全历史
    收盘，按股票做 expanding 百分位，再合并回窗口内的 panel。
    min_periods=60：上市不足 60 个交易日的早期段为 NaN。
    """
    codes = sorted(panel["code"].unique())
    win_start = panel["date"].min()
    # 小批(100 只)流式：每批算完分位后只保留窗口内结果、丢弃全历史，控制瞬时内存
    # （容器内 uvicorn 常驻已占内存，一次性物化全历史会 OOM）
    results, B = [], 100
    for i in range(0, len(codes), B):
        batch = codes[i:i + B]
        rows = (db.query(PriceData.stock_code, PriceData.trade_date, PriceData.close)
                .filter(PriceData.stock_code.in_(batch),
                        PriceData.close.isnot(None), PriceData.close > 0)
                .all())
        if not rows:
            continue
        h = pd.DataFrame(rows, columns=["code", "date", "close"])
        h["date"] = pd.to_datetime(h["date"])
        h = h.sort_values(["code", "date"])
        h["price_pctile_life"] = h.groupby("code")["close"].transform(
            lambda s: s.expanding(min_periods=60).rank(pct=True))
        results.append(h.loc[h["date"] >= win_start,
                             ["code", "date", "price_pctile_life"]])
        del rows, h
    if not results:
        panel["price_pctile_life"] = np.nan
        return panel
    pct = pd.concat(results, ignore_index=True)
    return panel.merge(pct, on=["code", "date"], how="left")


# =============================================================================
# 2. 三重门标签（后复权 → 用累计涨跌幅判轨；次日开盘买入；一字涨停剔除）
# =============================================================================
def make_labels(df: pd.DataFrame, cfg: Config) -> pd.DataFrame:
    out = []
    for code, g in df.groupby("code"):
        g = g.reset_index(drop=True)
        o, h, l, c = (g[x].values for x in ("open", "high", "low", "close"))
        lim = g["board_lim"].values
        n = len(g)
        for t in range(n - cfg.HORIZON - 1):
            buy = t + 1
            pre = c[t]                       # 买入日的前收 = 信号日收盘
            if pre <= 0:
                continue
            # 次日一字涨停（开盘即触及板块涨停）→ 买不进，剔除（致命未来函数）
            if o[buy] / pre - 1 >= lim[buy] - 1e-4 and (h[buy] - l[buy]) / pre < 1e-3:
                continue
            buy_price = o[buy]
            if buy_price <= 0:
                continue
            up = buy_price * (1 + cfg.PT)
            dn = buy_price * (1 - cfg.SL)
            label = 0
            for k in range(buy, min(buy + cfg.HORIZON, n)):
                if h[k] >= up:
                    label = 1
                    break
                if l[k] <= dn:
                    label = 0
                    break
            else:
                # H 日内未触轨：按到期收益正负定标签
                end_k = min(buy + cfg.HORIZON - 1, n - 1)
                label = int(c[end_k] > buy_price)
            out.append((g.loc[t, "date"], code, label))
    return pd.DataFrame(out, columns=["date", "code", "label"])


# =============================================================================
# 3. 特征工程（仅用信号日收盘前已知信息；可获取子集）
# =============================================================================
def make_features(df: pd.DataFrame):
    df = df.sort_values(["code", "date"]).copy()
    g = df.groupby("code")
    pre_close = g["close"].shift(1)
    df["ret1"] = df["close"] / pre_close - 1

    # --- 量能 ---
    df["vol_ma5"] = g["volume"].transform(lambda x: x.rolling(5).mean())
    df["vol_ratio"] = df["volume"] / df["vol_ma5"]                       # 量比
    df["amount_surge"] = df["volume"] / g["volume"].transform(           # 放量倍数(20日)
        lambda x: x.rolling(20).mean())
    df["turnover_pct60"] = g["turnover"].transform(                      # 换手率60日分位
        lambda x: x.rolling(60, min_periods=20).rank(pct=True))
    rng = (df["high"] - df["low"]).replace(0, np.nan)
    df["close_pos"] = (df["close"] - df["low"]) / rng                    # 收盘在振幅中的位置
    df["upper_shadow"] = (df["high"] - df[["close", "open"]].max(axis=1)) / rng

    # --- 形态/结构（后复权 → 用涨幅判涨停）---
    df["is_limit_up"] = (df["ret1"] >= df["board_lim"] - 1e-3).astype(int)
    df["limit_up_cnt5"] = g["is_limit_up"].transform(lambda x: x.rolling(5).sum())
    df["high20"] = g["high"].transform(lambda x: x.rolling(20).max())
    df["near_high20"] = df["close"] / df["high20"]                       # 距20日高位
    # 注：price_pctile_life（上市以来价格分位）由 add_life_pctile 用【全历史】预先算好
    #     并合并进 panel，这里不重算（窗口内 expanding 只会得到"近 N 年分位"）。
    for w in (5, 10, 20):
        df[f"ma{w}"] = g["close"].transform(lambda x: x.rolling(w).mean())
    df["ma_spread"] = df[["ma5", "ma10", "ma20"]].std(axis=1) / df["close"]  # 均线发散度
    df["above_ma20"] = (df["close"] > df["ma20"]).astype(int)

    # --- 板块 / 市场情绪（横截面，当日全市场，均为信号日已知）---
    df["sector_limit_cnt"] = df.groupby(["date", "sector"])["is_limit_up"].transform("sum")
    df["mkt_limit_cnt"] = df.groupby("date")["is_limit_up"].transform("sum")
    df["sector_rank"] = df.groupby(["date", "sector"])["ret1"].rank(pct=True)   # 板块内强弱
    # ⚠️ 省略（无数据源）：大单净流入、连板高度、炸板率、龙虎榜(次日)、舆情热度

    feat_cols = [
        "vol_ratio", "amount_surge", "turnover_pct60", "close_pos", "upper_shadow",
        "ret1", "is_limit_up", "limit_up_cnt5", "near_high20", "price_pctile_life",
        "ma_spread", "above_ma20", "sector_limit_cnt", "mkt_limit_cnt", "sector_rank",
    ]
    return df, feat_cols


def _col(df, *names):
    for n in names:
        if n in df.columns:
            return n
    return None


def merge_extra_features(feat_df: pd.DataFrame, feat_cols: list):
    """
    并入 hot_features_fetch.py 缓存的免费东财特征（封单/连板/炸板/龙虎榜/资金流）。
    缓存目录不存在或为空 → 原样返回，脚本照常运行。

    防泄漏：
      - 涨停板池 / 资金流：盘后即当日已知，作信号日特征，不滞后。
      - 龙虎榜：约 18:00 发布，信号日收盘决策时拿不到 → 滞后 1 个交易日并入。
    """
    d = EXTRA_CACHE_DIR
    if not os.path.isdir(d):
        return feat_df, feat_cols
    feat_df = feat_df.copy()
    added = []
    # 交易日历（用于龙虎榜滞后到次日）
    cal = pd.Series(sorted(feat_df["date"].unique()))
    next_day = {cur: nxt for cur, nxt in zip(cal[:-1], cal[1:])}

    # --- 涨停板池：连板数 / 封板资金 / 炸板次数（当日已知）---
    p = f"{d}/zt_pool.pkl"
    if os.path.exists(p):
        zt = pd.read_pickle(p)
        cc, td = _col(zt, "代码", "code"), "trade_date"
        if cc and td in zt.columns:
            m = pd.DataFrame({"code": zt[cc].astype(str), "date": pd.to_datetime(zt[td])})
            lb = _col(zt, "连板数", "连续涨停天数")
            fd = _col(zt, "封板资金", "封单资金")
            zb = _col(zt, "炸板次数")
            if lb: m["zt_lianban"] = pd.to_numeric(zt[lb], errors="coerce")
            if fd: m["zt_fengdan_amt"] = pd.to_numeric(zt[fd], errors="coerce")
            if zb: m["zt_zhaban_cnt"] = pd.to_numeric(zt[zb], errors="coerce")
            feat_df = feat_df.merge(m, on=["code", "date"], how="left")
            for c in ("zt_lianban", "zt_fengdan_amt", "zt_zhaban_cnt"):
                if c in feat_df.columns:
                    feat_df[c] = feat_df[c].fillna(0); added.append(c)

    # --- 龙虎榜：净买额 + 上榜标记（滞后 1 个交易日）---
    p = f"{d}/lhb.pkl"
    if os.path.exists(p):
        lhb = pd.read_pickle(p)
        cc = _col(lhb, "代码", "code")
        dd = _col(lhb, "上榜日", "交易日", "日期")
        nb = _col(lhb, "龙虎榜净买额", "净买额", "净买入额")
        if cc and dd:
            raw_date = pd.to_datetime(lhb[dd])
            sig_date = raw_date.map(lambda x: next_day.get(x))   # 滞后到次日
            m = pd.DataFrame({"code": lhb[cc].astype(str), "date": sig_date,
                              "lhb_on": 1.0})
            if nb: m["lhb_net_buy"] = pd.to_numeric(lhb[nb], errors="coerce")
            m = m.dropna(subset=["date"]).groupby(["code", "date"], as_index=False).agg(
                {**({"lhb_net_buy": "sum"} if nb else {}), "lhb_on": "max"})
            feat_df = feat_df.merge(m, on=["code", "date"], how="left")
            for c in ("lhb_on", "lhb_net_buy"):
                if c in feat_df.columns:
                    feat_df[c] = feat_df[c].fillna(0); added.append(c)

    # --- 个股资金流：主力 / 超大单净流入占比（当日已知）---
    p = f"{d}/fund_flow.pkl"
    if os.path.exists(p):
        ff = pd.read_pickle(p)
        cc = _col(ff, "code", "代码")
        dd = _col(ff, "日期", "date")
        mr = _col(ff, "主力净流入-净占比", "主力净流入净占比")
        sr = _col(ff, "超大单净流入-净占比", "超大单净流入净占比")
        if cc and dd:
            m = pd.DataFrame({"code": ff[cc].astype(str), "date": pd.to_datetime(ff[dd])})
            if mr: m["ff_main_ratio"] = pd.to_numeric(ff[mr], errors="coerce")
            if sr: m["ff_superbig_ratio"] = pd.to_numeric(ff[sr], errors="coerce")
            feat_df = feat_df.merge(m, on=["code", "date"], how="left")
            for c in ("ff_main_ratio", "ff_superbig_ratio"):
                if c in feat_df.columns:
                    added.append(c)   # 资金流缺失保留 NaN（HGB 原生支持）

    if added:
        print(f"   [extra] 并入热点特征 {len(added)} 个: {added}")
        feat_cols = feat_cols + added
    return feat_df, feat_cols


# =============================================================================
# 4. 防泄漏切分（Purged + Embargo）
# =============================================================================
def purged_time_split(dates: pd.Series, n_splits=4, embargo_days=6):
    # embargo_days 按【交易日】计（与 HORIZON 同单位）：在交易日历 uniq 上按下标
    # 往前推 embargo_days 个交易日做隔离，避免日历天换算（节假日不固定）导致隔离不足。
    uniq = np.sort(dates.unique())
    folds = np.array_split(uniq, n_splits + 1)
    for i in range(1, len(folds)):
        val_days = folds[i]
        cut_idx = max(0, int(np.searchsorted(uniq, val_days[0])) - embargo_days)
        cut = uniq[cut_idx]
        yield (dates < cut).values, dates.isin(val_days).values


def single_factor_ic(data: pd.DataFrame, feat_cols):
    """先做单因子 IC：每个特征 vs label 的 Spearman（确认有无预测力）。"""
    print("\n[单因子 IC]  (|IC|>0 即有微弱预测力；正=高值→更易起飞)")
    for f in feat_cols:
        sub = data[[f, "label"]].dropna()
        if len(sub) < 100 or sub[f].nunique() < 2:
            print(f"  {f:18s}: 样本/方差不足"); continue
        ic, _ = spearmanr(sub[f], sub["label"])
        print(f"  {f:18s}: IC={ic:+.4f}  n={len(sub)}")


def _fit(X, y, cfg: Config):
    base = HistGradientBoostingClassifier(
        max_iter=300, learning_rate=0.03, max_leaf_nodes=31,
        min_samples_leaf=100, l2_regularization=1.0, random_state=42)
    model = CalibratedClassifierCV(base, method="isotonic", cv=3) if cfg.CALIBRATE else base
    model.fit(X, y)
    return model


def train_model(data: pd.DataFrame, feat_cols, cfg: Config):
    """Purged-CV 估 AUC；并用最后一折(train→val)训练出可做样本外回测的模型。
    返回 (model, val_dates)：val_dates 即样本外回测应覆盖的日期集合。"""
    data = data.dropna(subset=["label"]).sort_values("date")
    X, y, dts = data[feat_cols], data["label"].astype(int), data["date"]
    aucs = []
    last = None   # (train_mask, val_mask)
    for tr, va in purged_time_split(dts, cfg.N_SPLITS, cfg.EMBARGO_DAYS):
        if y[tr].nunique() < 2 or y[va].nunique() < 2 or tr.sum() < 500:
            continue
        model = _fit(X[tr], y[tr], cfg)
        aucs.append(roc_auc_score(y[va], model.predict_proba(X[va])[:, 1]))
        last = (tr, va)
    if aucs:
        print(f"\n[训练] Purged-CV AUC: {[round(a,3) for a in aucs]}  均值={np.mean(aucs):.3f}")
        print("  (0.5=无预测力；A股真实有效信号通常仅略高于 0.5)")
    else:
        print("\n[训练] 有效折不足，无法评估。")
        return None, None
    # 用最后一折重训，返回其验证集日期供样本外回测（不含训练期，无泄漏）
    tr, va = last
    final = _fit(X[tr], y[tr], cfg)
    val_dates = set(pd.to_datetime(dts[va].unique()))
    return final, val_dates


# =============================================================================
# 5. 带 A股约束的回测（次日开盘买入 / 一字板拦截 / T+1 / 成本 / 三重门退出）
# =============================================================================
def backtest(panel: pd.DataFrame, scored: pd.DataFrame, cfg: Config):
    p = panel.set_index(["code", "date"]).sort_index()
    daily = {}
    for d, grp in scored.groupby("date"):
        picks = grp[grp["prob"] >= cfg.PROB_THRESHOLD].nlargest(cfg.TOP_K, "prob")
        rets = []
        for code in picks["code"]:
            try:
                sub = p.loc[code]
            except KeyError:
                continue
            fut = sub[sub.index > d]
            if len(fut) < 2:
                continue
            bd = fut.iloc[0]                  # 次日
            pre = sub[sub.index <= d].iloc[-1]["close"]
            lim = bd["board_lim"]
            if pre > 0 and bd["open"] / pre - 1 >= lim - 1e-4 \
               and (bd["high"] - bd["low"]) / pre < 1e-3:
                continue                      # 一字涨停买不到
            entry = bd["open"] * (1 + cfg.SLIPPAGE)
            up, dn = entry * (1 + cfg.PT), entry * (1 - cfg.SL)
            window = fut.iloc[: cfg.HORIZON]
            exit_px = window.iloc[-1]["close"]
            for _, bar in window.iterrows():
                if bar["high"] >= up:
                    exit_px = up; break
                if bar["low"] <= dn:
                    exit_px = dn; break
            exit_px *= (1 - cfg.SLIPPAGE)
            cost = cfg.COMMISSION * 2 + cfg.STAMP_TAX
            rets.append(exit_px / entry - 1 - cost)
        if rets:
            daily[d] = float(np.mean(rets))
    if not daily:
        print("\n[回测] 无可成交信号（阈值过高或样本不足）。"); return
    s = pd.Series(daily).sort_index()
    eq = (1 + s).cumprod()
    ann = eq.iloc[-1] ** (252 / max(len(s), 1)) - 1
    sharpe = s.mean() / (s.std() + 1e-9) * np.sqrt(252)
    mdd = (eq / eq.cummax() - 1).min()
    print(f"\n[回测]  调仓日={len(s)}  日胜率={(s>0).mean():.1%}  "
          f"年化≈{ann:.1%}  夏普≈{sharpe:.2f}  最大回撤≈{mdd:.1%}")
    print("  ⚠️ 未计成交额/ADV 容量约束（缺 amount 数据）；短线实盘容量小、衰减快，结果偏乐观。")


# =============================================================================
# 主流程
# =============================================================================
def main(cfg: Config):
    db = SessionLocal()
    try:
        print(f"=== 短线起飞模型·研究 ===  窗口≈{cfg.YEARS}年  H={cfg.HORIZON} "
              f"PT={cfg.PT:+.0%} SL={-cfg.SL:.0%}")
        print("1) 加载真实价格面板…")
        panel, max_date = load_price_panel(db, cfg)
        print(f"   股票 {panel['code'].nunique()} 只，{len(panel)} 行，至 {max_date}")
        print("1b) 计算上市以来价格分位（全历史，防泄漏）…")
        panel = add_life_pctile(db, panel)
        print("2) 三重门标签…")
        labels = make_labels(panel, cfg)
        if labels.empty:
            print("   无样本，退出"); return
        print(f"   样本 {len(labels)}，正样本(起飞)占比 {labels['label'].mean():.1%}")
        print("3) 特征工程…")
        feat_df, core_cols = make_features(panel)
        feat_df, feat_cols = merge_extra_features(feat_df, core_cols)   # 缓存存在则并入热点特征
        data = labels.merge(feat_df[["date", "code"] + feat_cols],
                            on=["date", "code"], how="left")
        single_factor_ic(data, feat_cols)
        print("\n4) 防泄漏训练（HistGradientBoosting + 隔离 Purged-CV）…")
        model, val_dates = train_model(data, feat_cols, cfg)
        if model is None:
            return
        print(f"5) 样本外打分 + 带约束回测（仅末折验证期 {len(val_dates)} 天，无泄漏）…")
        # 只对核心特征去 NaN；额外热点特征(可能稀疏)的缺失交给 HGB 原生处理
        fd = feat_df.dropna(subset=core_cols).copy()
        fd = fd[fd["date"].isin(val_dates)].copy()
        if fd.empty:
            print("   样本外无可打分行"); return
        fd["prob"] = model.predict_proba(fd[feat_cols])[:, 1]
        backtest(panel, fd[["code", "date", "board_lim", "open", "high",
                            "low", "close", "prob"]], cfg)
    finally:
        db.close()


if __name__ == "__main__":
    cfg = Config()
    if len(sys.argv) > 1:
        cfg.YEARS = float(sys.argv[1])
    if len(sys.argv) > 2:
        cfg.MAX_STOCKS = int(sys.argv[2])
    main(cfg)
