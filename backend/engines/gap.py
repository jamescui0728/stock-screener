"""
跳空缺口信号：突破 / 加油 / 衰竭 / 加仓。

规则（用户口述，逐条落到代码）：
  1. 突破 —— 底部放量跳空，连续三天不补跳空缺口
  2. 加油 —— 上涨途中跳空，量能保持温和放大，连续三天不补跳空缺口
  3. 衰竭 —— 大幅上涨后当天成交量放出天量，收盘收出长上影线
  4. 加仓 —— 股价连续上涨后出现回调，但回踩至缺口上沿，守住不破，同时成交量逐步萎缩

输入只有日线 OHLC + 成交量，不掺基本面 / 舆情 / 行业 / 宏观。
所有阈值在 config.py 的 GAP_* 里，不硬编码。

━━ 缺口的几何 ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

向上跳空：今日最低 > 昨日最高。缺口是这两者之间那段没有成交的价格区间。

        L_today  ←── 缺口**上沿**（价格高的一侧）
    ╱╲    ▲
   ╱  ╲  缺口
  ╱    ╲  ▼
         H_prev  ←── 缺口**下沿**（价格低的一侧）

  * 「回补缺口」= 价格跌回下沿（low <= H_prev），把缺口完全填掉
  * 规则 1/2 的「连续三天不补」= 之后三个交易日 low 都 > H_prev
  * 规则 4 的「回踩至缺口上沿，守住不破」= low 回到 L_today 附近但 **low >= L_today**，
    完全不进缺口（用户明确选择的口径，非"进了缺口但没补完"的宽松版）

━━ 后复权与缺口 ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

price_data 是后复权（fetcher 用 adjust="hfq"）。这对缺口识别恰好是**对的**：
除权除息在不复权数据上会留下巨大的假缺口，后复权已经把它们抹平，
剩下的跳空才是真实的供需失衡。这里不需要也不应该换算成前复权。

━━ 信号有效期 ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

规则 1/2 要求"连续三天不补"，所以信号在跳空后第 3 天才**确认**。确认后按
GAP_SIGNAL_VALID_DAYS（默认 5 个交易日）继续显示，并标出"第 N 日"，
免得必须每天盯盘才不错过。
"""
import logging
from typing import Optional

from config import settings

logger = logging.getLogger(__name__)

# ── 信号取值 ──────────────────────────────────────────────
GAP_BREAKOUT = "BREAKOUT"    # 突破：底部放量跳空
GAP_RUNAWAY  = "RUNAWAY"     # 加油：上涨途中温和放量跳空
GAP_EXHAUST  = "EXHAUST"     # 衰竭：天量长上影（风险提示）
GAP_ADD      = "ADD"         # 加仓：回踩缺口上沿守住 + 缩量

GAP_LABELS = {
    GAP_BREAKOUT: "突破",
    GAP_RUNAWAY:  "加油",
    GAP_EXHAUST:  "衰竭",
    GAP_ADD:      "加仓",
}

# bar 元组下标（与 watch_tag.load_ohlc_series 的返回一致）
D, O, H, L, C, V = 0, 1, 2, 3, 4, 5

# 规则 3 只看当日形态，最少要够算 20 日涨幅 + 20 日均量
MIN_BARS = 62


def _avg_vol(bars: list, end_idx: int, n: int = 20) -> Optional[float]:
    """end_idx 之前 n 根的平均成交量（**不含 end_idx 当日**）。"""
    start = end_idx - n
    if start < 0:
        return None
    vols = [b[V] for b in bars[start:end_idx] if b[V] is not None]
    if len(vols) < n or sum(vols) <= 0:
        return None
    return sum(vols) / len(vols)


# 相邻两根 K 线相隔超过这么多自然日，就认为中间有数据空洞而非真实跳空。
# A 股最长休市是春节 / 国庆，含前后周末约 9-11 个自然日，取 15 天留足余量。
MAX_BAR_GAP_DAYS = 15


def find_gaps(bars: list, min_pct: Optional[float] = None) -> list:
    """
    找出所有向上跳空。返回 [{"idx", "lower", "upper", "pct"}, ...]，idx 为跳空当日下标。

    lower = 前一日最高（缺口下沿），upper = 当日最低（缺口上沿）。

    ⚠️ **相邻两行必须是相邻交易日**。price_data 若缺了一段（增量抓取中断、
    只补了近期若干天等），相邻两行之间可能隔着几个月，这几个月的正常涨幅会被
    当成一次巨大跳空。这类假缺口在真实库里出现过的风险很高，因此这里显式校验
    日期间隔，超过 MAX_BAR_GAP_DAYS 就跳过 —— 宁可漏掉一个真跳空，
    也不能凭空造一个假的出来。
    """
    min_pct = settings.GAP_MIN_PCT if min_pct is None else min_pct
    out = []
    for i in range(1, len(bars)):
        prev_dt, cur_dt = bars[i - 1][D], bars[i][D]
        if prev_dt is not None and cur_dt is not None:
            if (cur_dt - prev_dt).days > MAX_BAR_GAP_DAYS:
                continue                  # 数据空洞，不是跳空
        lower = bars[i - 1][H]
        upper = bars[i][L]
        if not lower or lower <= 0 or upper is None:
            continue
        if upper <= lower:
            continue                      # 没跳空（或向下跳空，规则里不涉及）
        pct = (upper - lower) / lower * 100
        if pct < min_pct:
            continue
        out.append({"idx": i, "lower": lower, "upper": upper, "pct": pct})
    return out


def gap_unfilled_through(bars: list, gap: dict, end_idx: int) -> bool:
    """缺口从跳空次日到 end_idx（含）是否始终未被回补（low 都 > 下沿）。"""
    for j in range(gap["idx"] + 1, min(end_idx, len(bars) - 1) + 1):
        if bars[j][L] <= gap["lower"]:
            return False
    return True


def is_bottom_before(bars: list, idx: int) -> bool:
    """
    跳空前一日是否处于"底部"：收盘距近 N 日最低价不超过设定比例。

    N 日窗口取跳空前一日往回数，不含跳空当日本身 —— 跳空日的高价会把参照抬歪。
    """
    look = int(settings.GAP_BOTTOM_LOW_LOOKBACK)
    start = max(0, idx - look)
    window = bars[start:idx]              # 不含 idx
    if len(window) < 20:
        return False
    lows = [b[L] for b in window if b[L] is not None]
    if not lows:
        return False
    low_n = min(lows)
    if low_n <= 0:
        return False
    prev_close = bars[idx - 1][C]
    return (prev_close - low_n) / low_n * 100 <= settings.GAP_BOTTOM_MAX_ABOVE_PCT


def upper_shadow_ratios(bar) -> tuple:
    """
    返回 (上影/实体, 上影/全日振幅)。

    实体为 0（十字星）时第一项返回 inf —— 这类形态上影相对实体确实是无穷大，
    真正起过滤作用的是第二项（上影占振幅比例）。振幅为 0（一字板）返回 (0, 0)。
    """
    high, low, o, c = bar[H], bar[L], bar[O], bar[C]
    rng = high - low
    if rng <= 0:
        return (0.0, 0.0)
    body = abs(c - o)
    shadow = high - max(o, c)
    ratio_body = float("inf") if body == 0 else shadow / body
    return (ratio_body, shadow / rng)


# ════════════════════════════════════════════════════════════════
# 四条规则：每条返回"确认日下标"或 None
# ════════════════════════════════════════════════════════════════
def _check_breakout(bars: list, gap: dict) -> Optional[int]:
    """规则 1 突破：底部 + 放量 + 连续 N 天不补。返回确认日下标。"""
    i = gap["idx"]
    hold = int(settings.GAP_HOLD_DAYS)
    confirm = i + hold
    if confirm > len(bars) - 1:
        return None                       # 还没走满确认天数
    if not is_bottom_before(bars, i):
        return None
    avg = _avg_vol(bars, i)
    if not avg or bars[i][V] is None:
        return None
    if bars[i][V] / avg < settings.GAP_BREAKOUT_VOL_RATIO:
        return None
    if not gap_unfilled_through(bars, gap, confirm):
        return None
    return confirm


def _check_runaway(bars: list, gap: dict, ema_at) -> Optional[int]:
    """
    规则 2 加油：上涨途中 + 温和放量 + 连续 N 天不补。

    "上涨途中"用两条同时成立来判：跳空前一日收盘在当日 EMA20 上方（趋势向上），
    且不处于底部（否则那是规则 1 的突破，不该重复计为加油）。
    """
    i = gap["idx"]
    hold = int(settings.GAP_HOLD_DAYS)
    confirm = i + hold
    if confirm > len(bars) - 1:
        return None
    if is_bottom_before(bars, i):
        return None                       # 底部跳空归规则 1
    ema = ema_at(i - 1)
    if ema is None or bars[i - 1][C] <= ema:
        return None
    avg = _avg_vol(bars, i)
    if not avg or bars[i][V] is None:
        return None
    ratio = bars[i][V] / avg
    if not (settings.GAP_RUNAWAY_VOL_MIN <= ratio <= settings.GAP_RUNAWAY_VOL_MAX):
        return None
    if not gap_unfilled_through(bars, gap, confirm):
        return None
    return confirm


def _check_exhaust(bars: list, idx: int) -> bool:
    """
    规则 3 衰竭：大幅上涨 + 天量 + 长上影。只看 idx 当日形态，不要求跳空。
    """
    days = int(settings.GAP_EXHAUST_RUNUP_DAYS)
    if idx - days < 0:
        return False
    base = bars[idx - days][C]
    if not base or base <= 0:
        return False
    if (bars[idx][C] - base) / base * 100 < settings.GAP_EXHAUST_RUNUP_PCT:
        return False
    avg = _avg_vol(bars, idx)
    if not avg or bars[idx][V] is None:
        return False
    if bars[idx][V] / avg < settings.GAP_EXHAUST_VOL_RATIO:
        return False
    r_body, r_range = upper_shadow_ratios(bars[idx])
    return (r_body >= settings.GAP_EXHAUST_SHADOW_BODY
            and r_range >= settings.GAP_EXHAUST_SHADOW_RANGE)


def _check_add(bars: list, idx: int) -> Optional[dict]:
    """
    规则 4 加仓：涨后回调，回踩缺口上沿守住不破，量能萎缩。

    "守住不破"按用户选定的严格口径：当日最低 >= 缺口**上沿** L_today，
    即完全没有跌进缺口里。返回命中的缺口信息或 None。
    """
    look = int(settings.GAP_PULLBACK_LOOKBACK)
    bar = bars[idx]

    # 量能：最近 N 日逐日递减，且当日已萎缩到均量的设定比例以下
    shrink_days = int(settings.GAP_PULLBACK_SHRINK_DAYS)
    if idx - shrink_days < 0:
        return None
    recent = [bars[idx - k][V] for k in range(shrink_days - 1, -1, -1)]
    if any(v is None for v in recent):
        return None
    if any(recent[k] >= recent[k - 1] for k in range(1, len(recent))):
        return None                       # 不是逐日递减
    avg = _avg_vol(bars, idx)
    if not avg or bar[V] / avg > settings.GAP_PULLBACK_VOL_SHRINK:
        return None

    # 找回溯窗口内、至今仍未被回补的向上缺口，取最近的一个
    start = max(1, idx - look)
    for gap in reversed(find_gaps(bars)):
        if gap["idx"] < start or gap["idx"] >= idx:
            continue
        if not gap_unfilled_through(bars, gap, idx):
            continue
        # 缺口之后确实涨上去过（否则谈不上"连续上涨后回调"）
        peak = max(b[H] for b in bars[gap["idx"]:idx + 1])
        if (peak - gap["upper"]) / gap["upper"] * 100 < settings.GAP_PULLBACK_RUNUP_PCT:
            continue
        # 回踩到位：最低价贴近上沿
        if bar[L] > gap["upper"] * (1 + settings.GAP_PULLBACK_TOUCH_PCT / 100):
            continue
        # 守住不破：完全不进缺口
        if bar[L] < gap["upper"]:
            continue
        return gap
    return None


# ════════════════════════════════════════════════════════════════
# 对外：评估最后一根 K 线
# ════════════════════════════════════════════════════════════════
def evaluate_gap_signal(bars: list) -> dict:
    """
    评估序列末日的跳空信号。返回：

        {"gap_signal", "gap_signal_label", "gap_days_since", "gap_confirm_date",
         "gap_lower", "gap_upper", "gap_reason"}

    gap_confirm_date 是**信号确认那天的真实日期**。gap_days_since 只是它距序列末根
    的交易日数 —— 当 K 线停更时，"+4" 说的是"末根 K 线往前 4 个交易日"，
    而末根本身可能已经是几个月前，直接显示会让人误以为是最近发生的。
    前端应当展示日期，天数只作辅助。

    未命中时 gap_signal 为 None。

    优先级：**衰竭最高**（它是风险提示，不能被同期的看涨信号盖掉），
    其余按确认日从新到旧取第一个。
    """
    empty = {
        "gap_signal": None, "gap_signal_label": None, "gap_days_since": None,
        "gap_confirm_date": None,
        "gap_lower": None, "gap_upper": None, "gap_reason": None,
    }
    if not bars:
        return dict(empty, gap_reason=f"K 线不足 {MIN_BARS} 根，无法判定缺口形态")

    # 缺口判定必须有完整的开高低量。load_ohlc_series 只保证 close 非空，
    # 这里把序列截到"最后一根残缺 K 线之后"，取连续完整的尾段。
    # 不能只是把残缺行过滤掉 —— 那会让被删行两侧的 K 线变成相邻，凭空造出假缺口。
    bad = -1
    for i, b in enumerate(bars):
        if b[O] is None or b[H] is None or b[L] is None or b[V] is None:
            bad = i
    if bad >= 0:
        bars = bars[bad + 1:]

    if len(bars) < MIN_BARS:
        return dict(empty, gap_reason=f"K 线不足 {MIN_BARS} 根，无法判定缺口形态")

    last = len(bars) - 1
    valid = int(settings.GAP_SIGNAL_VALID_DAYS)
    earliest = last - valid + 1           # 确认日不早于此，信号才仍在有效期内

    # 衰竭：在有效期内逐日回看，命中即返回（风险优先）
    for t in range(last, max(earliest, 0) - 1, -1):
        if _check_exhaust(bars, t):
            r_body, r_range = upper_shadow_ratios(bars[t])
            avg = _avg_vol(bars, t)
            return {
                "gap_signal": GAP_EXHAUST,
                "gap_signal_label": GAP_LABELS[GAP_EXHAUST],
                "gap_days_since": last - t,
                "gap_confirm_date": bars[t][D],
                "gap_lower": None, "gap_upper": None,
                "gap_reason": (
                    f"大幅上涨后天量长上影：量能 {bars[t][V] / avg:.1f}× 均量，"
                    f"上影占振幅 {r_range * 100:.0f}%，见顶风险"
                ),
            }

    # 加仓：只看当日是否正踩在缺口上沿
    gap = _check_add(bars, last)
    if gap:
        return {
            "gap_signal": GAP_ADD,
            "gap_signal_label": GAP_LABELS[GAP_ADD],
            "gap_days_since": 0,
            "gap_confirm_date": bars[last][D],
            "gap_lower": round(gap["lower"], 2), "gap_upper": round(gap["upper"], 2),
            "gap_reason": (
                f"回踩缺口上沿 {gap['upper']:.2f} 守住未破，"
                f"量能萎缩至均量 {bars[last][V] / _avg_vol(bars, last):.0%}，可加仓"
            ),
        }

    # 突破 / 加油：取有效期内确认日最新的一个
    ema_cache = {}

    def ema_at(i):
        """i 日收盘时的 EMA20（含当日）。缓存避免同一序列重复递推。"""
        if i in ema_cache:
            return ema_cache[i]
        from engines.watch_tag import compute_ema
        val = compute_ema([b[C] for b in bars[:i + 1]], int(settings.WATCH_EMA_PERIOD))
        ema_cache[i] = val
        return val

    best = None
    for gap in find_gaps(bars):
        for kind, checker in ((GAP_BREAKOUT, _check_breakout),
                              (GAP_RUNAWAY, lambda b, g: _check_runaway(b, g, ema_at))):
            confirm = checker(bars, gap)
            if confirm is None or confirm < earliest or confirm > last:
                continue
            if best is None or confirm > best[1]:
                best = (kind, confirm, gap)

    if best:
        kind, confirm, gap = best
        avg = _avg_vol(bars, gap["idx"])
        ratio = bars[gap["idx"]][V] / avg if avg else 0
        who = "底部放量" if kind == GAP_BREAKOUT else "上涨途中温和放量"
        return {
            "gap_signal": kind,
            "gap_signal_label": GAP_LABELS[kind],
            "gap_days_since": last - confirm,
            "gap_confirm_date": bars[confirm][D],
            "gap_lower": round(gap["lower"], 2), "gap_upper": round(gap["upper"], 2),
            "gap_reason": (
                f"{who}跳空 {gap['pct']:.1f}%（量能 {ratio:.1f}×），"
                f"连续 {settings.GAP_HOLD_DAYS} 日未回补缺口 "
                f"[{gap['lower']:.2f}, {gap['upper']:.2f}]"
            ),
        }

    return dict(empty, gap_reason="近期无符合条件的跳空形态")
