"""
MACD 零轴上方回踩金叉 —— 买入关注信号。

规则（用户口述）：只看日线 MACD 回踩零轴，而且只做零轴上方的回踩，
不看其他指标和小道消息。

因此这里**刻意不掺任何别的东西**：不看基本面、不看舆情、不看行业、不看宏观。
输入只有一条日线收盘价序列，输出只有"今天是否命中"。

标准 MACD(12, 26, 9)：

    DIF  = EMA12 − EMA26          快线
    DEA  = DIF 的 EMA9            慢线（信号线）
    柱   = 2 × (DIF − DEA)        红绿柱

「零轴上方回踩」落成判定：

    DIF > 0  且  DEA > 0          —— 两条线都在零轴上方（多头格局未破）
    前一日 DIF ≤ DEA              —— 之前是回踩状态（快线在慢线下方）
    当日   DIF > DEA              —— 今天上穿，回踩结束、拐头向上

三个条件同时成立才算命中。只在金叉发生的**当天**为真，不是"处于金叉状态"。

⚠️ EMA 种子取法：本模块复用 `watch_tag.compute_ema`，它用前 period 根的 SMA 作种子。
部分看盘软件（通达信等）用首个收盘价作种子。历史足够长时两者收敛到几乎相同，
但 DIF 末位可能有微小差异，极少数临界情况下金叉日期会差一天。
需要与某个软件逐位对齐时，改这里的 EMA 实现即可，判定逻辑不用动。
"""
import logging
from typing import Optional

from config import settings
from engines.watch_tag import compute_ema

logger = logging.getLogger(__name__)

# DIF 需要 EMA26 收敛，DEA 又要在 DIF 上再跑 EMA9。
# SMA 种子下：DIF 序列从第 26 根才开始有值，DEA 再要 9 根 → 至少 34 根 K 线。
MIN_BARS = 34


def compute_dif_series(closes: list, fast: int, slow: int) -> list:
    """
    逐日 DIF = EMA(fast) − EMA(slow)。

    返回与 closes 尾部对齐的 DIF 列表（长度 = len(closes) − slow + 1）。
    不足 slow 根返回空列表。

    实现上对每个末点重新跑一次 EMA 是 O(n²)，但这里 n 只有几百根、
    全市场 5500 只实测仍在秒级，换来的是与 compute_ema 共用一套种子口径
    （约束 3：不为了性能另写一份 EMA 递推）。
    """
    if not closes or len(closes) < slow:
        return []
    out = []
    for i in range(slow, len(closes) + 1):
        window = closes[:i]
        ef = compute_ema(window, fast)
        es = compute_ema(window, slow)
        if ef is None or es is None:
            continue
        out.append(ef - es)
    return out


def compute_macd(closes: list,
                 fast: Optional[int] = None,
                 slow: Optional[int] = None,
                 signal: Optional[int] = None) -> Optional[dict]:
    """
    算出最近两日的 DIF / DEA，供金叉判定用。

    返回 {"dif", "dea", "hist", "prev_dif", "prev_dea"}，K 线不足返回 None。
    hist = 2 × (DIF − DEA)，即常见的红绿柱高度。
    """
    fast   = fast   or int(settings.MACD_FAST)
    slow   = slow   or int(settings.MACD_SLOW)
    signal = signal or int(settings.MACD_SIGNAL)

    if not closes or len(closes) < slow + signal:
        return None

    difs = compute_dif_series(closes, fast, slow)
    if len(difs) < signal + 1:
        return None      # 还要够算两天的 DEA，才能判"昨天在下、今天在上"

    dea      = compute_ema(difs, signal)
    prev_dea = compute_ema(difs[:-1], signal)
    if dea is None or prev_dea is None:
        return None

    return {
        "dif":      difs[-1],
        "dea":      dea,
        "hist":     2 * (difs[-1] - dea),
        "prev_dif": difs[-2],
        "prev_dea": prev_dea,
    }


def is_zero_axis_pullback_cross(m: Optional[dict]) -> bool:
    """
    零轴上方回踩金叉：DIF>0 且 DEA>0 且 昨日 DIF≤DEA 且 今日 DIF>DEA。

    m 为 None（K 线不足）时返回 False —— 算不出来就不是买点，不猜。
    """
    if not m:
        return False
    return (
        m["dif"] > 0
        and m["dea"] > 0
        and m["prev_dif"] <= m["prev_dea"]
        and m["dif"] > m["dea"]
    )


def evaluate_macd(closes: list) -> dict:
    """
    单只股票的 MACD 关注判定。返回给前端 / 落库用的扁平 dict：

        {"macd_dif", "macd_dea", "macd_hist", "macd_cross_up", "macd_reason"}

    K 线不足时各数值为 None、cross_up=False，并在 reason 里说明原因。
    """
    m = compute_macd(closes)
    if m is None:
        return {
            "macd_dif": None, "macd_dea": None, "macd_hist": None,
            "macd_cross_up": False,
            "macd_reason": f"K 线不足 {MIN_BARS} 根，算不出 MACD",
        }

    hit = is_zero_axis_pullback_cross(m)
    if hit:
        reason = (f"零轴上方回踩金叉：DIF {m['dif']:.3f} 上穿 DEA {m['dea']:.3f}，"
                  f"两线均在零轴上方")
    elif m["dif"] <= 0 or m["dea"] <= 0:
        reason = (f"不在零轴上方（DIF {m['dif']:.3f} / DEA {m['dea']:.3f}），"
                  f"按规则不做")
    elif m["dif"] > m["dea"]:
        reason = (f"零轴上方多头排列但非今日金叉"
                  f"（DIF {m['dif']:.3f} > DEA {m['dea']:.3f}）")
    else:
        reason = (f"零轴上方回踩中，尚未金叉"
                  f"（DIF {m['dif']:.3f} ≤ DEA {m['dea']:.3f}）")

    return {
        "macd_dif":      round(m["dif"], 4),
        "macd_dea":      round(m["dea"], 4),
        "macd_hist":     round(m["hist"], 4),
        "macd_cross_up": hit,
        "macd_reason":   reason,
    }
